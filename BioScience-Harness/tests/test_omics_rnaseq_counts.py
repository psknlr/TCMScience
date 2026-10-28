"""The RNA-seq count-matrix mode end to end, on a simulated count matrix with a known answer."""

from __future__ import annotations

import gzip
import json
import shutil
import zlib

import numpy as np
import pytest

from bioagent.omics import rnaseq as R
from bioagent.omics import rnaseq_counts as C
from omics_world import make_counts_world, make_world
from test_pipeline_references import _ref, _same_environment, _sha

pytestmark = pytest.mark.unit

CONTRAST = ("condition", "treated", "control")
TABLES = ("counts.tsv", "deseq2_results.tsv", "de_diagnostics.tsv", "vst.tsv")
PLOTS = ("pca", "sample_distances", "ma", "volcano", "p_values", "dispersion")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_counts_world(tmp_path_factory.mktemp("counts-world"), seed=1)


@pytest.fixture(scope="module")
def run(world, tmp_path_factory):
    out = tmp_path_factory.mktemp("counts-run")
    return C.run_rnaseq_counts(world["counts"], world["samples"],
                               C.RNASeqCountsConfig(contrast=CONTRAST), out)


def _outputs(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*"))
            if p.is_file() and p.name not in ("run.json", "stages.json")}


def _called(res):
    return {g: float(res.log2_fold_change[i]) for i, g in enumerate(res.genes)
            if np.isfinite(res.p_adjusted[i]) and res.p_adjusted[i] < res.alpha}


# ------------------------------------------------------------------ the answer and the record

def test_the_planted_genes_are_found_in_their_direction(world, run):
    called = _called(run.result)
    assert sum(called.get(g, 0) > 1 for g in world["up"]) >= 38
    assert sum(called.get(g, 0) < -1 for g in world["down"]) >= 38
    assert not [g for g in world["up"] if called.get(g, 0) < 0]
    assert not [g for g in world["down"] if called.get(g, 0) > 0]
    assert len(set(called) - set(world["up"]) - set(world["down"])) <= 15
    assert run.contrast == CONTRAST and run.complete


def test_every_stage_is_recorded_and_the_record_names_the_inputs(world, run):
    out = run.out_dir
    manifest = json.loads((out / "run.json").read_text())
    assert manifest["pipeline"] == C.PIPELINE and manifest["input_kind"] == "counts"
    stages = manifest["stages"]
    assert [s["name"] for s in stages] == list(C.STAGES)
    assert all(s["status"] == "succeeded" and s["required"] for s in stages)
    assert {s["name"]: s["check"] for s in stages if s["check"]} == {
        "validate_counts": "PASS", "verify": "PASS"}
    engines = {s["name"]: s["engine"] for s in stages}
    assert engines["differential_expression"] == engines["explore"] == "builtin-de"
    assert json.loads((out / "stages.json").read_text())["stages"] == stages
    assert manifest["complete"] is True
    for name in ("counts", "samples"):
        spec = manifest["inputs"][name]
        path = world[name].resolve()
        assert spec == {"path": str(path), "sha256": _sha(path), "bytes": path.stat().st_size}
    assert manifest["validation"]["counts"]["genes"] == 2000
    assert manifest["validation"]["counts"]["content_sha256"] == _sha(out / "counts.tsv")
    assert manifest["reference_dataset"]["id"] == "omics_counts_world"
    assert {"python", "numpy", "scipy", "code_digest"} <= set(manifest)
    assert manifest["code_digest"] == R._code_digest()
    listed = set(manifest["outputs"])
    assert {o for s in stages for o in s["outputs"]} <= listed
    assert listed == set(_outputs(out))
    assert manifest["differential_expression"]["backend"] == "builtin"
    lines = (out / "limitations.md").read_text().splitlines()
    assert lines == manifest["limitations"] == run.limitations
    assert any("analysed as given" in line for line in lines)
    assert any("simulated, known answer" in line and "not public data" in line
               for line in lines)


def test_the_tables_report_and_figures(world, run):
    out = run.out_dir
    # the input is already canonical (tab-separated integers), so it is written back as it is
    assert (out / "counts.tsv").read_bytes() == world["counts"].read_bytes()
    samples = ["ctrl1", "ctrl2", "ctrl3", "trt1", "trt2", "trt3"]
    heads = {name: (out / name).read_text().splitlines()[0].split("\t") for name in TABLES}
    assert heads["counts.tsv"] == ["gene_id", "gene_name", *samples]
    assert heads["vst.tsv"] == ["gene_id", *samples]
    assert heads["deseq2_results.tsv"][:2] == ["gene_id", "gene_name"]
    assert all(len((out / n).read_text().splitlines()) == 2001 for n in TABLES)
    for plot in PLOTS:
        svg = (out / "plots" / f"{plot}.svg").read_text()
        assert svg.startswith("<svg") and "<title>" in svg
    assert sorted(p.stem for p in (out / "plots").iterdir()) == sorted(PLOTS)
    md = (out / "report.md").read_text()
    assert md.startswith("# RNA-seq differential expression: treated vs control")
    assert "analysed as given" in md and "## What this result is" in md
    assert "Quantification" not in md and "QC fail" not in md
    assert "| trt2 | treated |" in md and "<svg" in (out / "report.html").read_text()
    for name in ("report.md", "limitations.md", "deseq2_results.tsv"):
        assert "seconds" not in (out / name).read_text()


def test_a_rerun_and_another_file_format_give_the_same_bytes(world, run, tmp_path):
    again = C.run_rnaseq_counts(world["counts"], world["samples"],
                                C.RNASeqCountsConfig(contrast=CONTRAST), tmp_path / "again")
    assert _outputs(tmp_path / "again") == _outputs(run.out_dir)
    assert again.manifest["code_digest"] == run.manifest["code_digest"]
    # the same counts as gzipped CSV with CRLF lines, the samples as CSV in another order:
    # the canonical tables, and so every output and the reference match, are the same
    rows = [line.split("\t") for line in world["counts"].read_text().splitlines()]
    csv_counts = tmp_path / "counts.csv.gz"
    with gzip.open(csv_counts, "wt", newline="") as fh:
        fh.write("".join(",".join(r) + "\r\n" for r in rows))
    table = tmp_path / "samples.csv"
    table.write_text("sample,condition\ntrt1,treated\nctrl1,control\nctrl2,control\n"
                     "trt2,treated\nctrl3,control\ntrt3,treated\n")
    other = C.run_rnaseq_counts(csv_counts, table, C.RNASeqCountsConfig(contrast=CONTRAST),
                                tmp_path / "csv")
    assert _outputs(tmp_path / "csv") == _outputs(run.out_dir)
    assert other.manifest["validation"] == run.manifest["validation"]
    assert other.reference_dataset == run.reference_dataset
    assert other.manifest["inputs"]["counts"]["sha256"] != \
        run.manifest["inputs"]["counts"]["sha256"]


def test_the_same_counts_give_the_full_pipelines_tables(tmp_path):
    """The FASTQ reference (omics_world) run by the full pipeline, its counts rounded into a
    count table: the count-matrix mode writes the same test tables from them, since both
    run the same stage code. The full pipeline's reference outputs are still its recorded
    bytes (where they were recorded)."""
    ref = _ref("omics_world")
    w = make_world(tmp_path / "world", **ref["source"]["arguments"])
    p = ref["parameters"]
    full = R.run_rnaseq(w.sheet, R.RNASeqConfig(
        transcripts=w.transcripts, annotation=w.gtf, engine=p["engine"], trimmer=p["trimmer"],
        de_backend=p["de_backend"], design=p["design"], contrast=tuple(p["contrast"]),
        alpha=p["alpha"]), tmp_path / "full")
    if _same_environment(ref["expected"]["outputs_recorded_with"]):
        for name, digest in ref["expected"]["outputs"].items():
            assert _sha(tmp_path / "full" / name) == digest, name
    lines = (tmp_path / "full" / "counts.tsv").read_text().splitlines()
    rounded = [lines[0]] + ["\t".join([*c[:2], *(str(int(round(float(v)))) for v in c[2:])])
                            for c in (line.split("\t") for line in lines[1:])]
    (tmp_path / "counts.tsv").write_text("\n".join(rounded) + "\n")
    (tmp_path / "samples.tsv").write_text("sample\tcondition\n" + "".join(
        f"{s.name}\t{s.attributes['condition']}\n" for s in full.samples))
    C.run_rnaseq_counts(tmp_path / "counts.tsv", tmp_path / "samples.tsv",
                        C.RNASeqCountsConfig(design=p["design"], contrast=tuple(p["contrast"]),
                                             alpha=p["alpha"]), tmp_path / "counts")
    for name in ("deseq2_results.tsv", "de_diagnostics.tsv", "vst.tsv"):
        assert (tmp_path / "counts" / name).read_bytes() == \
            (tmp_path / "full" / name).read_bytes(), name


# ------------------------------------------------------------------ refusals

HEADER = "gene_id\tgene_name\ta\tb\tc\n"


@pytest.mark.parametrize("text, needle", [
    ("", "is empty"),
    ("gene_id\ta\tb\n", "lists no genes"),
    ("gene\ta\tb\ng1\t1\t2\n", "the first column must be gene_id"),
    ("gene_id\ta\ng1\t1\n", "a test needs at least two samples"),
    ("gene_id\tgene_name\ta\ng1\tx\t1\n", "a test needs at least two samples"),
    ("gene_id\ta\ta\ng1\t1\t2\n", "named more than once: a"),
    (HEADER + "g1\tx\t1\t2\t3\ng1\ty\t1\t2\t3\n", "gene id(s) named more than once: g1"),
    (HEADER + "g1\tx\t1\tNA\t3\n", "line 2, sample b: 'NA' is not a number"),
    (HEADER + "g1\tx\t1\t\t3\n", "line 2, sample b: '' is not a number"),
    (HEADER + "g1\tx\t1\t2\t3\ng2\ty\t1\t-4\t3\n", "line 3, sample b: counts must be "
                                                   "non-negative"),
    (HEADER + "g1\tx\t1\tinf\t3\n", "counts must be finite"),
    (HEADER + "g1\tx\t1\tnan\t3\n", "counts must be finite"),
    (HEADER + "g1\tx\t1\t2.5\t3\n", "counts must be integers; round estimated counts first"),
    (HEADER + "g1\tx\t1\t0\t3\ng2\ty\t1\t0\t3\n", "sample(s) b have no counts at all"),
    (HEADER + "g1\tx\t1\t2\n", "line 2: 4 cells where the header has 5"),
    (HEADER + "g1\tx\t1\t2\t1e30\n", "2^53 or more"),
    # a name the tab-separated outputs cannot hold in one cell (quoted, so the CSV holds it)
    ('gene_id,a,b\n"g\t1",1,2\n', "the gene id 'g\\t1' holds a tab or a line break"),
    ('gene_id,gene_name,a,b\ng1,"x\ny",1,2\n', "the gene name 'x\\ny' holds a tab or a line break"),
    ('gene_id,a,"b\r\nc"\ng1,1,2\n', "the sample name 'b\\r\\nc' holds a tab or a line break"),
    # a quote never closed: the rest of the file is one cell. Past the csv module's cell limit
    # that is its error; but the limit is process-wide and other bioagent modules raise it when
    # imported, and then the one long cell is a row too short. Refused either way
    ('gene_id\ta\tb\n"g1\t1\t2\n' + "g2\t1\t2\n" * 20000,
     ("cannot be read as a CSV or TSV table (field larger than field limit",
      "line 2: 1 cells where the header has 3")),
])
def test_count_tables_that_cannot_be_tested_are_refused(tmp_path, text, needle):
    path = tmp_path / "counts.tsv"
    path.write_text(text)
    with pytest.raises(R.RNASeqError) as exc:
        C.read_counts_table(path)
    assert any(n in str(exc.value) for n in ((needle,) if isinstance(needle, str) else needle)), \
        str(exc.value)


def test_a_count_table_within_1e8_of_integers_is_read_as_integers(tmp_path):
    path = tmp_path / "counts.csv"
    path.write_text("gene_id,a,b\ng1,3.000000001,7\ng2,0,1e2\n")
    table = C.read_counts_table(path)
    assert table.counts.tolist() == [[3, 7], [0, 100]] and table.counts.dtype == np.int64
    assert table.gene_names == table.genes == ("g1", "g2")
    assert table.text() == "gene_id\tgene_name\ta\tb\ng1\tg1\t3\t7\ng2\tg2\t0\t100\n"


@pytest.mark.parametrize("text, needle", [
    ("sample,fastq_1,fastq_2,condition\nx,a_R1.fq.gz,a_R2.fq.gz,c\n",
     "is a FASTQ sample sheet: the count-matrix mode takes a sample table; run the full "
     "pipeline from FASTQ on the runner"),
    ("sample,fastq_1,condition\nx,a.fq,c\n", "is a FASTQ sample sheet"),
    ("name,condition\nx,c\n", "has no 'sample' column"),
    ("sample,condition\nx,c\nx,d\n", "sample 'x' is listed twice"),
    ("sample,condition\n", "lists no samples"),
    ("sample,condition\n,c\n", "no sample name"),
    ('sample,condition\nx,"a\tb"\n', "line 2: the value 'a\\tb' holds a tab or a line break"),
])
def test_sample_tables_that_cannot_be_used_are_refused(tmp_path, text, needle):
    path = tmp_path / "samples.csv"
    path.write_text(text)
    with pytest.raises(R.RNASeqError) as exc:
        C.read_sample_table(path)
    assert needle in str(exc.value)


def _broken_gzip(text: str, how: str) -> bytes:
    """``text`` gzipped, then made unreadable: its deflate stream corrupted (bytes 20 to 60
    flipped) or cut short."""
    raw = gzip.compress(text.encode("utf-8"), mtime=0)
    if how == "truncated":
        return raw[:len(raw) // 2]
    return raw[:20] + bytes(b ^ 0xFF for b in raw[20:60]) + raw[60:]


@pytest.mark.parametrize("how", ["corrupt", "truncated"])
@pytest.mark.parametrize("which", ["counts", "samples"])
def test_a_gzipped_table_that_cannot_be_read_to_its_end_is_refused(world, tmp_path, capsys,
                                                                    which, how):
    """A corrupt deflate stream (zlib.error) or one cut short (EOFError) is refused with the
    reason, as a FASTQ file is (omics.fastq), never a traceback."""
    data = _broken_gzip(world[which].read_text(), how)
    if how == "corrupt":
        with pytest.raises(zlib.error):
            gzip.decompress(data)
    path = tmp_path / f"{which}.tsv.gz"
    path.write_bytes(data)
    read = C.read_counts_table if which == "counts" else C.read_sample_table
    with pytest.raises(R.RNASeqError) as exc:
        read(path)
    assert str(exc.value).startswith(f"{path} cannot be read to its end ("
                                     + ("error: " if how == "corrupt" else "EOFError: "))
    tables = {"counts": world["counts"], "samples": world["samples"], which: path}
    code, _, err = _cli(["rnaseq", "--counts", str(tables["counts"]), "--samples",
                         str(tables["samples"]), "--out", str(tmp_path / "o")], capsys)
    assert code == 1 and err.startswith(f"refused: {path} cannot be read to its end"), err


def test_a_run_refuses_before_writing_what_it_cannot_run(world, tmp_path):
    out = tmp_path / "out"
    fastq = tmp_path / "sheet.csv"
    fastq.write_text("sample,fastq_1,condition\nctrl1,a.fq,control\n")
    for samples, config, needle in [
            (fastq, C.RNASeqCountsConfig(), "is a FASTQ sample sheet"),
            (world["samples"], C.RNASeqCountsConfig(alpha=0), "between 0 and 1"),
            (world["samples"], C.RNASeqCountsConfig(de_backend="edger"), "de_backend is one of"),
            (world["samples"], C.RNASeqCountsConfig(contrast=("condition", "x", "control")),
             "condition has no level 'x'"),
            (world["samples"], C.RNASeqCountsConfig(design="~ batch"),
             "the sample sheet has no column 'batch'")]:
        with pytest.raises(R.RNASeqError, match=needle):
            C.run_rnaseq_counts(world["counts"], samples, config, out)
        assert not out.exists()


def test_a_contrast_the_run_chose_says_which_way_round(world, tmp_path):
    """Without a named contrast the later level in sorted order is the numerator: with
    'treated' and 'untreated' that is untreated vs treated (the single-cell pipeline puts a
    control-like level in the denominator), so the run says what it tested and why."""
    samples = tmp_path / "samples.tsv"
    samples.write_text(world["samples"].read_text().replace("\tcontrol", "\tuntreated"))
    run = C.run_rnaseq_counts(world["counts"], samples, C.RNASeqCountsConfig(), tmp_path / "o")
    assert run.contrast == ("condition", "untreated", "treated")
    (note,) = [w for w in run.warnings if w.startswith("no contrast was named")]
    assert "untreated vs treated was tested" in note
    assert "the later of condition's two levels in sorted order against the earlier" in note
    assert "a log2 fold change > 0 means higher in untreated" in note
    assert note in (tmp_path / "o" / "report.md").read_text()
    assert note in json.loads((tmp_path / "o" / "run.json").read_text())["warnings"]
    named = C.run_rnaseq_counts(world["counts"], samples, C.RNASeqCountsConfig(
        contrast=("condition", "treated", "untreated")), tmp_path / "named")
    assert not any(w.startswith("no contrast was named") for w in named.warnings)


def test_pydeseq2_is_refused_when_it_is_not_installed(world, tmp_path):
    import importlib.util
    if importlib.util.find_spec("pydeseq2") is not None:
        pytest.skip("pydeseq2 is installed here")
    from bioagent.omics.optional import BackendUnavailable
    with pytest.raises(BackendUnavailable, match="pydeseq2"):
        C.run_rnaseq_counts(world["counts"], world["samples"],
                            C.RNASeqCountsConfig(de_backend="pydeseq2"), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_pydeseq2_runs_the_count_matrix_mode_where_it_is_installed(world, run, tmp_path):
    """Where PyDESeq2 is installed (CI's pipeline job) the same stages run it instead: the
    record names it, and the planted genes are still found."""
    from omics_world import need_module
    need_module("pydeseq2")
    other = C.run_rnaseq_counts(world["counts"], world["samples"],
                                C.RNASeqCountsConfig(contrast=CONTRAST, de_backend="pydeseq2"),
                                tmp_path / "out")
    assert other.complete and other.manifest["differential_expression"]["backend"] == "pydeseq2"
    stages = {s["name"]: s for s in other.manifest["stages"]}
    assert stages["differential_expression"]["engine"] == "pydeseq2"
    called = _called(other.result)
    assert sum(called.get(g, 0) > 1 for g in world["up"]) >= 35
    assert sum(called.get(g, 0) < -1 for g in world["down"]) >= 35
    assert (tmp_path / "out" / "counts.tsv").read_bytes() == \
        (run.out_dir / "counts.tsv").read_bytes()


def test_counts_and_samples_must_match_by_name(world, tmp_path):
    table = tmp_path / "samples.tsv"
    table.write_text(world["samples"].read_text().replace("trt3\t", "trt9\t"))
    with pytest.raises(R.RNASeqError, match="the count columns and the sample table: no "
                                            "metadata for sample.s. trt3"):
        C.run_rnaseq_counts(world["counts"], table, C.RNASeqCountsConfig(), tmp_path / "out")
    stages = json.loads((tmp_path / "out" / "stages.json").read_text())["stages"]
    assert [(s["name"], s["status"]) for s in stages] == [("validate_counts", "failed")]


# ------------------------------------------------------------------ verification

def test_verify_run_catches_a_changed_output_and_a_moved_input(world, run, tmp_path):
    import hashlib
    copy = tmp_path / "copy"
    shutil.copytree(run.out_dir, copy)
    assert C.verify_run(copy) == (True, [])
    table = copy / "deseq2_results.tsv"
    original = table.read_text()
    table.write_text(original.replace("G0000", "G9999", 1))
    ok, problems = C.verify_run(copy)
    assert not ok and "output deseq2_results.tsv has changed" in problems
    # a table cut to its header and re-digested: only the content check can see it
    table.write_text(original.splitlines()[0] + "\n")
    manifest = json.loads((copy / "run.json").read_text())
    manifest["outputs"]["deseq2_results.tsv"] = hashlib.sha256(table.read_bytes()).hexdigest()
    (copy / "run.json").write_text(json.dumps(manifest))
    ok, problems = C.verify_run(copy)
    assert not ok and "deseq2_results.tsv: 0 rows, where the run record has 2000 genes" in \
        problems
    # an input that is no longer where the record says
    moved = tmp_path / "moved"
    shutil.copytree(world["root"], moved)
    inputs = tmp_path / "inputs"
    shutil.copytree(world["root"], inputs)
    fresh = C.run_rnaseq_counts(inputs / "counts.tsv", inputs / "samples.tsv",
                                C.RNASeqCountsConfig(contrast=CONTRAST), tmp_path / "fresh")
    assert C.verify_run(fresh.out_dir) == (True, [])
    (inputs / "counts.tsv").unlink()
    ok, problems = C.verify_run(fresh.out_dir)
    assert not ok and problems == [f"input counts ({inputs.resolve() / 'counts.tsv'}) is missing"]
    (inputs / "counts.tsv").write_text((moved / "counts.tsv").read_text()
                                       + "G9999\tX\t1\t1\t1\t1\t1\t1\n")
    ok, problems = C.verify_run(fresh.out_dir)
    assert not ok and problems == [f"input counts ({inputs.resolve() / 'counts.tsv'}) has changed"]


def test_the_rnaseq_check_hands_a_count_record_to_this_mode(run, tmp_path):
    assert R.verify_run(run.out_dir) == C.verify_run(run.out_dir) == (True, [])
    copy = tmp_path / "copy"
    shutil.copytree(run.out_dir, copy)
    (copy / "plots" / "pca.svg").unlink()
    ok, problems = R.verify_run(copy)
    # the count-matrix record has no tpm.tsv, which the FASTQ check would require
    assert not ok and "output plots/pca.svg is missing" in problems
    assert not any("tpm.tsv" in p for p in problems)
    # the runner's validator for pipeline.rnaseq calls rnaseq.verify_run (tcmstudio.kinds)
    assert R.verify_run(copy) == C.verify_run(copy)


# ------------------------------------------------------------------ the command line

def _cli(argv, capsys):
    from bioagent import cli
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_cli_runs_the_count_matrix_mode(world, tmp_path, capsys):
    code, out, err = _cli(["rnaseq", "--counts", str(world["counts"]), "--samples",
                           str(world["samples"]), "--contrast", "condition,treated,control",
                           "--out", str(tmp_path / "out"), "--json"], capsys)
    assert code == 0, err
    summary = json.loads(out)
    assert summary["input_kind"] == "counts" and summary["complete"] is True
    assert summary["genes"] == 2000 and summary["differential_expression"]["tested"] == 1999
    code, out, _ = _cli(["rnaseq", "--verify", str(tmp_path / "out")], capsys)
    assert code == 0 and out.startswith("verified")
    code, out, _ = _cli(["rnaseq", "--counts", str(world["counts"]), "--samples",
                         str(world["samples"]), "--out", str(tmp_path / "text")], capsys)
    assert code == 0 and "6 samples, 2,000 genes from a count matrix" in out


@pytest.mark.parametrize("extra, needle", [
    (["--transcripts", "tx.fa"], "--transcripts"),
    (["--engine", "auto", "--trimmer", "none"], "--engine, --trimmer"),
    (["--genome", "g.fa", "--annotation", "a.gtf"], "--annotation, --genome"),
    (["--strand", "0"], "--strand"),
])
def test_the_cli_refuses_fastq_options_with_counts(world, tmp_path, capsys, extra, needle):
    code, _, err = _cli(["rnaseq", "--counts", str(world["counts"]), "--samples",
                         str(world["samples"]), "--out", str(tmp_path / "o"), *extra], capsys)
    assert code == 2
    assert err.strip() == ("refused: a count-matrix run takes no FASTQ reference or engine: "
                           + needle)
    assert not (tmp_path / "o").exists()


def test_the_cli_exit_codes_of_the_count_matrix_mode(world, tmp_path, capsys, monkeypatch):
    code, _, err = _cli(["rnaseq", "--counts", str(world["counts"]), "--out",
                         str(tmp_path / "o")], capsys)
    assert code == 2 and "--counts needs --samples" in err
    code, _, err = _cli(["rnaseq", "--counts", str(world["counts"]), "--samples",
                         str(world["samples"]), "--contrast", "condition", "--out",
                         str(tmp_path / "o")], capsys)
    assert code == 2 and "--contrast is factor,numerator,denominator" in err
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("sample,fastq_1,condition\nctrl1,a.fq,control\n")
    code, _, err = _cli(["rnaseq", "--counts", str(world["counts"]), "--samples", str(sheet),
                         "--out", str(tmp_path / "o")], capsys)
    assert code == 1 and err.startswith("refused: ") and "FASTQ sample sheet" in err
    code, _, err = _cli(["rnaseq", "--counts", str(tmp_path / "none.tsv"), "--samples",
                         str(world["samples"]), "--out", str(tmp_path / "o")], capsys)
    assert code == 1 and err.startswith("refused: ")
    monkeypatch.setattr(C, "_content_problems",
                        lambda out, manifest: ["vst.tsv: a planted mismatch"])
    code, _, err = _cli(["rnaseq", "--counts", str(world["counts"]), "--samples",
                         str(world["samples"]), "--out", str(tmp_path / "bad"), "--json"],
                        capsys)
    assert code == 1
    assert "incomplete: stage verify: its check failed: vst.tsv: a planted mismatch" in err
    manifest = json.loads((tmp_path / "bad" / "run.json").read_text())
    assert manifest["complete"] is False


def test_the_fastq_mode_keeps_its_defaults(world, tmp_path, monkeypatch, capsys):
    """The FASTQ-only options now default to None (so the count-matrix mode can tell one
    given): the FASTQ mode still runs with auto, auto, 200, 30 and 0."""
    seen = {}

    def fake(sheet, config, out):
        seen["config"] = config
        raise R.RNASeqError("stop here")
    monkeypatch.setattr(R, "run_rnaseq", fake)
    code, _, err = _cli(["rnaseq", "--samples", str(world["samples"]), "--out",
                         str(tmp_path / "o")], capsys)
    assert code == 1 and "refused: stop here" in err
    config = seen["config"]
    assert (config.engine, config.trimmer, config.fragment_mean, config.fragment_sd,
            config.strand) == ("auto", "auto", 200.0, 30.0, 0)
