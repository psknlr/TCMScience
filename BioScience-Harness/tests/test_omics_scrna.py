"""The single-cell pipeline and its parts, on a simulated experiment with a known answer."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from scipy import sparse
from scipy.stats import spearmanr

from bioagent.omics import scrna as S
from bioagent.omics.sc import doublets, io, leiden, markers, preprocess, qc
from sc_world import EXPECTED, make_sc_world

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_sc_world(tmp_path_factory.mktemp("sc-world"))


@pytest.fixture(scope="module")
def matrix(world):
    import csv
    rows = list(csv.DictReader(open(world.sheet)))
    parts = [(r["sample"], io.read_counts(world.root / r["path"])) for r in rows]
    return io.concatenate(parts, {r["sample"]: {"condition": r["condition"],
                                                "batch": r["batch"]} for r in rows})


@pytest.fixture(scope="module")
def flagged(world, tmp_path_factory):
    """doublets='flag' keeps the transition between NK cells and platelets."""
    return S.run_scrna(world.sheet, S.ScConfig(root="NK cell", n_top_genes=600,
                                               doublets="flag"),
                       tmp_path_factory.mktemp("sc-flag"))


@pytest.fixture(scope="module")
def removed(world, tmp_path_factory):
    return S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600),
                       tmp_path_factory.mktemp("sc-remove"))


def _truth(world, cells, key="type"):
    return np.array([world.truth[c][key] for c in cells], dtype=object)


# ---------------------------------------------------------------- reading

def test_ten_x_directories_are_read_and_combined(matrix, world):
    assert matrix.shape[1] == len(world.genes)
    assert set(matrix.obs["sample"]) == {"ctrl1", "ctrl2", "trt1", "trt2"}
    assert matrix.cells[0].startswith(("ctrl1:", "ctrl2:", "trt1:", "trt2:"))
    assert set(matrix.obs["batch"]) == {"b1", "b2"}


def test_normalised_values_are_refused(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("gene,c1,c2\nG1,0.5,1.2\nG2,3,4\n")
    with pytest.raises(io.ScIOError, match="raw counts"):
        io.read_counts(p)


@pytest.mark.parametrize("name, line_end", [("t.tsv", "\n"), ("t.csv.gz", "\r\n")])
def test_a_table_read_a_row_at_a_time_is_the_matrix_parsed_whole(tmp_path, name, line_end):
    """The reader keeps each row's non-zero counts only; the matrix is the one a parse of the
    whole table gives (cells in rows, sorted CSR, float32), and a blank line is skipped."""
    import csv
    import gzip

    rng = np.random.default_rng(3)
    values = np.where(rng.random((40, 25)) < 0.3, rng.integers(1, 9, (40, 25)), 0)
    values[5] = 0                                          # a gene with no counts at all
    cells = [f"c{i}" for i in range(25)]
    cells[3] = "c,3"                                       # a quoted header cell in the CSV
    sep = "\t" if name.startswith("t.tsv") else ","
    lines = [sep.join(['"gene"' if sep == "," else "gene"]
                      + [f'"{c}"' if sep in c else c for c in cells])]
    lines += [sep.join([f"G{g}"] + [str(v) for v in row]) for g, row in enumerate(values)]
    lines.insert(7, "")
    text = line_end.join(lines) + line_end
    p = tmp_path / name
    p.write_bytes(gzip.compress(text.encode(), mtime=0) if name.endswith(".gz")
                  else text.encode())
    m = io.read_counts(p)
    whole = list(csv.reader(text.replace("\r\n", "\n").split("\n")[:-1], delimiter=sep))
    expected = sparse.csr_matrix(np.array([[float(v) for v in r[1:]] for r in whole[1:] if r],
                                          dtype=np.float64).T)
    assert list(m.cells) == cells and list(m.gene_ids) == [f"G{g}" for g in range(40)]
    assert m.counts.dtype == np.float32 and m.counts.has_sorted_indices
    assert (m.counts != expected.astype(np.float32)).nnz == 0
    assert m.counts.nnz == np.count_nonzero(values)


def test_a_table_row_of_the_wrong_length_is_refused_with_the_counts(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text("gene\tc1\tc2\nG1\t1\t2\nG2\t3\n")
    with pytest.raises(io.ScIOError, match=r"rows have 1 values for 2 cells"):
        io.read_counts(p)


def test_a_corrupt_file_of_a_cell_ranger_directory_is_refused(world, tmp_path):
    """A 10x directory whose gzip is corrupt is refused with the reason, as a table is."""
    d = tmp_path / "ctrl1"
    shutil.copytree(world.root / "ctrl1", d)
    raw = (d / "barcodes.tsv.gz").read_bytes()
    (d / "barcodes.tsv.gz").write_bytes(raw[:20] + bytes(b ^ 0xFF for b in raw[20:60])
                                        + raw[60:])
    with pytest.raises(io.ScIOError, match=r"cannot be read to its end"):
        io.read_counts(d)


@pytest.mark.parametrize("how", ["corrupt", "truncated"])
def test_a_gzipped_table_that_cannot_be_read_to_its_end_is_refused(tmp_path, capsys, how):
    """A corrupt deflate stream (zlib.error) or one cut short (EOFError) is refused with the
    reason, as a FASTQ file is (omics.fastq), never a traceback."""
    import gzip
    import zlib

    from bioagent import cli
    text = "gene\t" + "\t".join(f"c{i}" for i in range(60)) + "\n" + "".join(
        f"G{g}\t" + "\t".join(str((g + i) % 4) for i in range(60)) + "\n" for g in range(300))
    raw = gzip.compress(text.encode(), mtime=0)
    data = (raw[:len(raw) // 2] if how == "truncated"
            else raw[:20] + bytes(b ^ 0xFF for b in raw[20:60]) + raw[60:])
    if how == "corrupt":
        with pytest.raises(zlib.error):
            gzip.decompress(data)
    p = tmp_path / "t.tsv.gz"
    p.write_bytes(data)
    with pytest.raises(io.ScIOError) as exc:
        io.read_counts(p)
    assert str(exc.value).startswith(f"{p} cannot be read to its end ("
                                     + ("error: " if how == "corrupt" else "EOFError: "))
    assert cli.main(["scrna", str(p), "--out", str(tmp_path / "o")]) == 1
    assert capsys.readouterr().err.startswith(f"refused: {p} cannot be read to its end")


def test_a_sheet_row_that_names_no_path_is_refused(tmp_path, capsys):
    """An empty path cell is the sheet's mistake, said with its line; it does not name the
    sheet's own directory (a Cell Ranger directory, to the reader)."""
    from bioagent import cli
    (tmp_path / "a.tsv").write_text("gene\tc1\tc2\nG1\t1\t2\n")
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("sample,path,condition\nA,a.tsv,control\n\nB,,treated\n")
    with pytest.raises(S.ScError) as exc:
        S.read_sheet(sheet)
    assert str(exc.value) == f"line 4 of {sheet} names no path"
    assert cli.main(["scrna", str(sheet), "--out", str(tmp_path / "o")]) == 1
    assert capsys.readouterr().err == f"refused: line 4 of {sheet} names no path\n"


# ---------------------------------------------------------------- QC and doublets

def test_low_quality_cells_are_removed(matrix, world):
    res = qc.filter_cells(matrix)
    flags = _truth(world, res.matrix.cells, "flag")
    before = (_truth(world, matrix.cells, "flag") == "low_quality").sum()
    assert before > 20 and (flags == "low_quality").sum() == 0
    kept_good = (_truth(world, matrix.cells, "flag") == "").sum()
    assert (flags == "").sum() >= 0.97 * kept_good


def test_scrublet_finds_doublets_of_distinct_types(matrix, world):
    m = qc.filter_cells(matrix).matrix
    called = np.zeros(m.shape[0], dtype=bool)
    for k, s in enumerate(dict.fromkeys(m.obs["sample"])):
        idx = np.flatnonzero(m.obs["sample"] == s)
        called[idx] = doublets.scrublet(m.counts[idx], seed=k).predicted
    kind = _truth(world, m.cells)
    real = kind == "doublet"
    assert (called & real).sum() / real.sum() > 0.7
    discrete = np.isin(kind, ["B", "Mono"])               # far from any transition
    assert (called & discrete).sum() / discrete.sum() < 0.06


def test_a_flat_score_histogram_calls_no_doublets():
    assert doublets._threshold(np.full(500, 0.1)) is None


# ---------------------------------------------------------------- parts

def test_hvg_matches_the_seurat_definition():
    rng = np.random.default_rng(0)
    counts = sparse.csr_matrix(rng.poisson(rng.gamma(0.5, 2, 300), size=(400, 300)))
    logx = preprocess.normalize_log1p(counts)
    hv = preprocess.highly_variable_genes(logx, n_top=50)
    assert hv["highly_variable"].sum() == 50
    top = np.argsort(-np.nan_to_num(hv["norm_dispersion"], nan=-np.inf))[:50]
    assert set(top) == set(np.flatnonzero(hv["highly_variable"]))


def test_leiden_finds_planted_communities():
    rng = np.random.default_rng(1)
    sizes = [40, 30, 30]
    labels = np.repeat(np.arange(3), sizes)
    n = len(labels)
    p = np.where(labels[:, None] == labels[None, :], 0.3, 0.01)
    a = (rng.random((n, n)) < p).astype(float)
    a = np.triu(a, 1)
    a = sparse.csr_matrix(a + a.T)
    res = leiden.leiden(a, seed=0)
    from collections import Counter
    for c in range(3):
        top = Counter(res.membership[labels == c]).most_common(1)[0][1]
        assert top / sizes[c] > 0.9
    assert res.quality == pytest.approx(leiden.modularity(a, res.membership))


def test_harmony_mixes_the_batches(flagged):
    """Mixing is the share of neighbours from the other batch over the share perfect
    mixing gives; the clusters' agreement with the types is checked below."""
    assert flagged.integration["method"] == "harmony"
    assert flagged.integration["mixing_before"] < 0.3
    assert flagged.integration["mixing_after"] > 0.8


def test_wilcoxon_matches_scipy_on_one_gene():
    from scipy.stats import mannwhitneyu
    rng = np.random.default_rng(2)
    x = np.vstack([rng.poisson(1.0, (60, 1)), rng.poisson(2.5, (40, 1))]).astype(float)
    logx = sparse.csr_matrix(np.log1p(x))
    labels = ["a"] * 60 + ["b"] * 40
    res = markers.rank_genes_groups(logx, labels, np.array(["g"]))
    ref = mannwhitneyu(np.log1p(x[60:, 0]), np.log1p(x[:60, 0]), method="asymptotic",
                       use_continuity=False)
    assert res.pvals[res.groups.index("b"), 0] == pytest.approx(ref.pvalue, rel=1e-6)


# ---------------------------------------------------------------- the pipeline

def test_clusters_recover_the_cell_types(removed, world):
    kind = _truth(world, removed.cells)
    keep = np.isin(kind, list(EXPECTED))
    ari = _ari(kind[keep], removed.clusters[keep])
    assert ari > 0.9


def _ari(a, b):
    from collections import Counter
    from math import comb
    pairs = Counter(zip(a, b))
    rows, cols = Counter(a), Counter(b)
    n = len(a)
    index = sum(comb(v, 2) for v in pairs.values())
    ra, cb = sum(comb(v, 2) for v in rows.values()), sum(comb(v, 2) for v in cols.values())
    expected = ra * cb / comb(n, 2)
    return (index - expected) / ((ra + cb) / 2 - expected)


def test_clusters_are_named_by_their_markers(removed, world):
    kind = _truth(world, removed.cells)
    for c, label in removed.cell_types.items():
        members = kind[removed.clusters.astype(str) == c]
        values, counts = np.unique(members, return_counts=True)
        major = values[np.argmax(counts)]
        if major in EXPECTED:
            assert label == EXPECTED[major], (c, major, label)


def test_pseudotime_follows_the_planted_path(flagged, world):
    t = np.array([world.truth[c]["t"] for c in flagged.cells], dtype=float)
    on_path = np.isfinite(t) & np.isfinite(flagged.pseudotime)
    assert on_path.sum() > 100
    assert spearmanr(flagged.pseudotime[on_path], t[on_path]).correlation > 0.85
    kind = _truth(world, flagged.cells)
    assert np.isnan(flagged.pseudotime[kind == "B"]).all()     # not on the trajectory


def test_the_paga_tree_runs_through_the_transition(flagged):
    names = {c: t for c, t in flagged.cell_types.items()}
    joined = {(names[a], names[b]) for a, b, w in flagged.paga.tree if w >= 0.05}
    assert any("Platelet" in pair for pair in joined)


def test_pseudobulk_finds_the_condition_effect_only_where_planted(removed, world):
    pb = removed.pseudobulk
    assert pb is not None and "B cell" in pb.results
    b = pb.results["B cell"]
    found = {b.genes[i] for i in b.significant()}
    assert len(found & set(world.condition_genes)) >= 12
    for ct, res in pb.results.items():
        if ct != "B cell":
            assert len(res.significant()) <= 1


def test_a_contrast_the_run_chose_says_which_way_round(removed):
    """No contrast was named: a control-like level is the denominator (bulk RNA-seq takes
    the later level in sorted order instead), and the run says so where it is read."""
    assert next(iter(removed.pseudobulk.results.values())).contrast == \
        ("condition", "treated", "control")
    (note,) = [w for w in removed.warnings if w.startswith("no contrast was named")]
    assert "treated vs control was tested in each cell type" in note
    assert "control as the reference, a control-like level" in note
    assert "a log2 fold change > 0 means higher in treated" in note
    md = (removed.out_dir / "report.md").read_text()
    assert note in md and "(a log2 fold change > 0 means higher in treated)" in md
    assert S._infer_contrast({"a": {"condition": "disease"}, "b": {"condition": "healthy"}}) \
        == ("condition", "disease", "healthy")
    assert S._infer_contrast({"a": {"condition": "x2"}, "b": {"condition": "x1"}}) \
        == ("condition", "x2", "x1")


def test_without_a_root_nothing_is_ordered(removed):
    assert removed.pseudotime is None
    assert "No pseudotime" in (removed.out_dir / "report.md").read_text()


def test_the_report_and_its_digests(removed, tmp_path):
    out = removed.out_dir
    for name in ("cells.tsv", "markers.tsv", "clusters.tsv", "paga.tsv", "report.md",
                 "report.html", "run.json"):
        assert (out / name).is_file(), name
    manifest = json.loads((out / "run.json").read_text())
    assert manifest["integration"]["method"] == "harmony"
    ok, problems = S.verify_run(out)
    assert ok, problems
    copy = tmp_path / "copy"
    shutil.copytree(out, copy)
    (copy / "clusters.tsv").write_text("tampered\n")
    ok, problems = S.verify_run(copy)
    assert not ok and any("clusters.tsv" in p for p in problems)


def test_a_rerun_is_identical(world, removed, tmp_path):
    again = S.run_scrna(world.sheet, S.ScConfig(n_top_genes=600), tmp_path)
    for name in ("cells.tsv", "markers.tsv", "clusters.tsv", "report.md", "limitations.md"):
        assert (tmp_path / name).read_text() == (removed.out_dir / name).read_text()
    assert np.array_equal(again.clusters, removed.clusters)


def test_an_unknown_root_is_refused(world, tmp_path):
    with pytest.raises(S.ScError, match="neither a cluster nor"):
        S.run_scrna(world.sheet, S.ScConfig(root="Hepatocyte", n_top_genes=600), tmp_path)


# ---------------------------------------------------------------- the run record

def test_every_stage_is_recorded_with_the_device_it_ran_on(removed):
    manifest = json.loads((removed.out_dir / "run.json").read_text())
    stages = {s["name"]: s for s in manifest["stages"]}
    assert list(stages) == list(S.STAGES)
    assert manifest["complete"] is True and removed.complete
    assert stages["integrate"]["status"] == "succeeded"
    assert stages["integrate"]["engine"] == "builtin-harmony"
    assert stages["trajectory"]["status"] == "not_applicable"           # no root named
    assert not stages["trajectory"]["required"]
    assert stages["pseudobulk_de"]["status"] == "succeeded"
    ran = [s for s in stages.values() if s["status"] == "succeeded"]
    assert all(s["device"] == "cpu" for s in ran)                       # as computed
    assert stages["validate_input"]["check"] == stages["verify"]["check"] == "PASS"
    assert json.loads((removed.out_dir / "stages.json").read_text())["stages"] == \
        manifest["stages"]
    assert manifest["reference_dataset"]["id"] == "sc_world"


def test_the_limitations_name_what_did_not_run(removed, flagged):
    lines = (removed.out_dir / "limitations.md").read_text().splitlines()
    assert lines == removed.manifest["limitations"]
    text = " ".join(lines)
    assert "inference from marker expression" in text and "not elapsed time" in text
    assert "Stage trajectory was not applicable: no root" in text
    assert "simulated, known answer" in text
    rooted = {s["name"]: s for s in flagged.manifest["stages"]}
    assert rooted["trajectory"]["status"] == "succeeded"
    assert "Stage trajectory" not in (flagged.out_dir / "limitations.md").read_text()


def test_a_single_batch_has_nothing_to_integrate(world, tmp_path):
    import csv
    rows = list(csv.DictReader(open(world.sheet)))
    sheet = tmp_path / "one.csv"
    sheet.write_text("sample,path,condition,batch\n" + "".join(
        f"{r['sample']},{world.root / r['path']},{r['condition']},b1\n" for r in rows[:2]))
    run = S.run_scrna(sheet, S.ScConfig(n_top_genes=600, batch_key="batch"), tmp_path / "out")
    stages = {s["name"]: s for s in run.manifest["stages"]}
    assert stages["integrate"]["status"] == "not_applicable"
    assert "one batch only" in stages["integrate"]["reason"]
    assert stages["pseudobulk_de"]["status"] == "not_applicable"       # one condition
    assert run.complete


def test_the_layout_says_how_it_started(removed):
    assert removed.analysis["layout"] == {"start": "spectral"}
    assert removed.manifest["analysis"]["layout"] == {"start": "spectral"}
    assert not any("random positions" in w for w in removed.warnings)
    assert "random positions" not in (removed.out_dir / "limitations.md").read_text()


def test_a_failed_spectral_start_is_recorded_not_hidden(world, tmp_path, monkeypatch):
    """When ARPACK does not converge the layout still starts from random positions, as
    umap-learn's does; the record, the warnings, the report and limitations.md say so."""
    import csv
    from scipy.sparse.linalg import ArpackNoConvergence

    from bioagent.omics.sc import umap

    def no_convergence(*args, **kwargs):
        raise ArpackNoConvergence("No convergence (1201 iterations)", [], [])
    monkeypatch.setattr(umap, "eigsh", no_convergence)
    rows = list(csv.DictReader(open(world.sheet)))
    sheet = tmp_path / "two.csv"
    sheet.write_text("sample,path,condition,batch\n" + "".join(
        f"{r['sample']},{world.root / r['path']},{r['condition']},{r['batch']}\n"
        for r in rows[:2]))
    run = S.run_scrna(sheet, S.ScConfig(n_top_genes=600), tmp_path / "out")
    why = ("the spectral start failed (ArpackNoConvergence: ARPACK error -1: No convergence "
           "(1201 iterations))")
    assert run.manifest["analysis"]["layout"] == {"start": f"random: {why}"}
    assert f"the UMAP layout started from random positions: {why}" in run.warnings
    assert f"- the UMAP layout started from random positions: {why}" in \
        (run.out_dir / "report.md").read_text()
    lines = (run.out_dir / "limitations.md").read_text().splitlines()
    assert any(line.startswith(f"The UMAP layout started from random positions: {why}. Only "
                               "the UMAP coordinates") for line in lines)
    neighbors = next(s for s in run.manifest["stages"] if s["name"] == "neighbors")
    assert neighbors["status"] == "succeeded" and "start was random" in neighbors["reason"]
    assert run.complete and S.verify_run(run.out_dir)[0]


def test_running_out_of_memory_is_not_a_random_start(monkeypatch):
    """A MemoryError in the eigensolver is the run's, raised as it is: a random start is only
    for an eigensolver that failed (a browser worker turns it into a refusal for memory)."""
    from scipy.sparse.linalg import ArpackNoConvergence

    from bioagent.omics.sc import umap

    ring = sparse.diags([np.ones(19), np.ones(19)], [1, -1], shape=(20, 20), format="csr")

    def out_of_memory(*args, **kwargs):
        raise MemoryError
    monkeypatch.setattr(umap, "eigsh", out_of_memory)
    with pytest.raises(MemoryError):
        umap.umap_layout(ring, n_epochs=5)

    def no_convergence(*args, **kwargs):
        raise ArpackNoConvergence("No convergence (100 iterations)", [], [])
    monkeypatch.setattr(umap, "eigsh", no_convergence)
    layout, start = umap.umap_layout(ring, n_epochs=5)
    assert layout.shape == (20, 2) and start.startswith("random: the spectral start failed")


def test_cell_types_whose_names_come_to_one_file_name_keep_their_own_tables(world, tmp_path,
                                                                          monkeypatch):
    """'Mono+' and 'Mono-' are both Mono_ as a file name: each keeps a table of its own,
    the record checks one distinct table per test, and the skill quotes each cell type's
    evidence from its own table."""
    from bioagent.omics.sc.annotate import default_panel
    from bioagent.skills.omics import scrna_cell_atlas
    runs = []
    real = S.run_scrna
    monkeypatch.setattr(S, "run_scrna", lambda *a, **k: runs.append(real(*a, **k)) or runs[-1])
    types = dict(default_panel()["cell_types"])
    types["Mono+"] = types.pop("B cell")                # the planted condition effect
    types["Mono-"] = types.pop("CD14+ monocyte")
    panel = tmp_path / "panel.json"
    panel.write_text(json.dumps({"about": "two names, one file name", "cell_types": types}))
    assert S.pseudobulk_files(["Mono+", "Mono-", "NK cell", "nk_cell"]) == {
        "Mono+": "Mono_", "Mono-": "Mono__2", "NK cell": "NK_cell", "nk_cell": "nk_cell_2"}
    art = scrna_cell_atlas(str(world.sheet), experiment_design="observational",
                           markers=str(panel), out_dir=str(tmp_path / "run"))
    out = tmp_path / "run"
    results = runs[0].pseudobulk.results
    assert {"Mono+", "Mono-"} <= set(results)
    assert len(results["Mono+"].significant()) > len(results["Mono-"].significant())
    files = S.pseudobulk_files(results)
    assert sorted(f"{stem}.tsv" for stem in files.values()) == \
        sorted(p.name for p in (out / "pseudobulk").iterdir())
    for ct, res in results.items():                      # each table is its own test's
        rows = [ln.split("\t") for ln in
                (out / "pseudobulk" / f"{files[ct]}.tsv").read_text().splitlines()]
        col = rows[0].index("p_adjusted")
        called = sum(r[col] != "NA" and float(r[col]) < res.alpha for r in rows[1:])
        assert called == len(res.significant()), ct
    assert S.verify_run(out) == (True, [])
    (out / "pseudobulk" / "Mono__2.tsv").unlink()
    ok, problems = S.verify_run(out)
    assert not ok and "pseudobulk/Mono__2.tsv is missing for the Mono- test" in problems
    mono = [e for e in art.evidence if e.population.endswith("Mono+")]
    assert mono and all(e.id.startswith("pb.Mono_.") for e in mono)
    assert all(e.identifier.startswith("pseudobulk/Mono_.tsv#") for e in mono)
    assert [c.id for c in art.claims] == ["scrna.pseudobulk.Mono_"]


def test_verify_run_checks_the_tables_against_the_record(removed, tmp_path):
    import hashlib
    copy = tmp_path / "copy"
    shutil.copytree(removed.out_dir, copy)
    manifest = json.loads((copy / "run.json").read_text())
    cells = copy / "cells.tsv"
    cells.write_text("\n".join(cells.read_text().splitlines()[:11]) + "\n")
    manifest["outputs"]["cells.tsv"] = hashlib.sha256(cells.read_bytes()).hexdigest()
    (copy / "run.json").write_text(json.dumps(manifest))
    ok, problems = S.verify_run(copy)
    assert not ok and any(p.startswith("cells.tsv: 10 rows") for p in problems), problems


# ---------------------------------------------------------------- the governed skill

def test_the_candidate_skill_claims_only_the_condition_comparison(world, tmp_path):
    from pathlib import Path

    from bioagent.contracts import check_claim
    from bioagent.governed import GovernedRunRefused, run_governed
    candidates = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "omics"
    args = {"source": str(world.sheet), "experiment_design": "observational",
            "out_dir": str(tmp_path / "run")}
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("scrna-cell-atlas", args, skill_dir=candidates,
                     state_dir=tmp_path / "psh0")
    run = run_governed("scrna-cell-atlas", args, skill_dir=candidates,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out",
                       allow_unpinned=True)
    assert not run.released and run.verdict.publishable, run.verdict.codes
    claims = run.artifact.claims
    assert [c.id for c in claims] == ["scrna.pseudobulk.B_cell"]
    assert claims[0].claim_kind == "association"
    assert check_claim(claims[0], {e.id: e for e in run.artifact.evidence}).allowed
    assert "inferred from marker expression" in " ".join(run.artifact.limitations)


def test_the_candidate_skill_makes_no_claim_from_an_incomplete_run(world, tmp_path,
                                                                  monkeypatch):
    """The run is made incomplete after it ran (its verify check marked FAIL, as a changed
    output would): the skill must read the run's own verdict and claim nothing."""
    from bioagent.skills.omics import scrna_cell_atlas
    real = S.run_scrna

    def incomplete(*args, **kwargs):
        run = real(*args, **kwargs)
        run.stages = [dict(s, check="FAIL", reason="an output has changed")
                      if s["name"] == "verify" else s for s in run.stages]
        run.complete = False
        return run

    monkeypatch.setattr(S, "run_scrna", incomplete)
    art = scrna_cell_atlas(str(world.sheet), experiment_design="observational",
                           out_dir=str(tmp_path / "run"))
    assert not art.claims
    text = " ".join(art.limitations)
    assert ("the run is incomplete (stage verify: its check failed: an output has changed), "
            "so no claim is made") in text
