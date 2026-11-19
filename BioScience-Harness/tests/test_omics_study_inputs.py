"""Cross-stage regression tests for clinical units, measured features and run ownership."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import sparse

from bioagent.omics import scrna
from bioagent.omics.sc import io, pseudobulk, qc
from sc_world import make_sc_world


pytestmark = pytest.mark.unit


def test_aliased_source_is_refused_in_the_pipeline(tmp_path):
    matrix = tmp_path / "source.tsv"
    matrix.write_text("gene\tc1\tc2\nX\t1\t2\n")
    copy = tmp_path / "source-copy.tsv"
    copy.write_bytes(matrix.read_bytes())
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("sample,path,condition\na,source.tsv,A\nb,source-copy.tsv,B\n")
    with pytest.raises(scrna.ScError, match="same count matrix"):
        scrna.run_scrna(sheet, scrna.ScConfig(), tmp_path / "out")


def test_technical_replicates_are_one_unit_and_pairs_require_blocking():
    obs = {k: np.asarray(v, dtype=object) for k, v in {
        "sample": ["t1", "t2", "t3", "t4", "t5", "t6"],
        "donor": ["d1", "d1", "d2", "d2", "d1", "d2"],
        "condition": ["a", "a", "a", "a", "b", "b"]}.items()}
    contrast = ("condition", "b", "a")
    units, meta, design, key = pseudobulk.experimental_units(obs, contrast, "auto", None)
    assert key == "donor" and design == "~ donor + condition"
    assert len(meta) == 4 and units[0] == units[1] and units[0] != units[4]
    with pytest.raises(ValueError, match="paired unit"):
        pseudobulk.experimental_units(obs, contrast, "auto", "~ condition")
    # One donor per condition; multiple different technical libraries cannot rescue inference.
    nonreplicated = {k: v[:4] for k, v in obs.items()}
    nonreplicated["condition"] = np.array(["a", "a", "b", "b"])
    units, meta, design, _ = pseudobulk.experimental_units(nonreplicated, contrast, "auto", None)
    result = pseudobulk.pseudobulk_de(sparse.csr_matrix(np.ones((4, 2))), ["x", "y"],
                                     ["T"] * 4, units, meta, design=design, contrast=contrast,
                                     min_cells=1)
    assert result.samples_per_group["T"] == {"a": 1, "b": 1} and not result.results


def test_perfect_batch_confounding_cannot_produce_a_test():
    names = ["s0", "s1", "s2", "s3"]
    info = {s: {"condition": "A" if i < 2 else "B", "batch": "x" if i < 2 else "y"}
            for i, s in enumerate(names)}
    out = pseudobulk.pseudobulk_de(sparse.csr_matrix(np.ones((4, 3))), ["X", "Y", "Z"],
                                  ["T"] * 4, names, info, design="~ condition",
                                  contrast=("condition", "B", "A"), min_cells=1)
    assert not out.results and "confounded" in out.skipped["T"]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_nonfinite_counts_are_refused(tmp_path, value):
    f = tmp_path / "x.tsv"
    f.write_text(f"gene\tc1\tc2\nA\t1\t{value}\n")
    with pytest.raises(io.ScIOError, match="raw counts"):
        io.read_counts(f)


def test_only_measured_features_enter_analysis_and_unmapped_mito_is_missing():
    parts = [(name, io.CellMatrix(sparse.csr_matrix([counts]), np.array(["c"]),
                                  np.array(genes), np.array(genes)))
             for name, genes, counts in [("s1", ["ENSG1", "ENSG2"], [1, 9]),
                                         ("s2", ["ENSG3", "ENSG1"], [7, 3])]]
    joined = io.concatenate(parts)
    assert joined.gene_ids.tolist() == ["ENSG1"]
    assert joined.counts.toarray().tolist() == [[1], [3]]
    assert np.isnan(qc.qc_metrics(joined)["pct_mito"]).all()
    corrected = qc.filter_cells(joined, qc.QCSettings(min_genes=1, min_cells=1))
    assert corrected.per_sample["s1"]["median_pct_mito"] is None


def test_multiplexed_h5ad_preserves_independent_units_and_gene_symbols(tmp_path):
    ad = pytest.importorskip("anndata")
    pd = pytest.importorskip("pandas")
    annotated = ad.AnnData(X=sparse.csr_matrix([[1, 2], [2, 3], [4, 5], [4, 1]]),
                           obs=pd.DataFrame({"sample": ["S1", "S2", "S3", "S4"],
                                             "donor": ["d1", "d1", "d2", "d2"],
                                             "condition": ["A", "B", "A", "B"]},
                                            index=["c1", "c2", "c3", "c4"]),
                           var=pd.DataFrame({"gene_symbols": ["MT-ND1", "IL6"]},
                                            index=["ENSG1", "ENSG2"]))
    path = tmp_path / "data.h5ad"
    annotated.write_h5ad(path)
    matrix = io.concatenate([("pool", io.read_counts(path))])
    assert matrix.obs["sample"].tolist() == ["S1", "S2", "S3", "S4"]
    assert matrix.obs["condition"].tolist() == ["A", "B", "A", "B"]
    assert matrix.gene_ids.tolist() == ["ENSG1", "ENSG2"]
    assert np.isclose(qc.qc_metrics(matrix)["pct_mito"][0], 100 / 3)
    _, meta, design, _ = pseudobulk.experimental_units(
        matrix.obs, ("condition", "B", "A"), "auto", None)
    assert len(meta) == 4 and design == "~ donor + condition"
    with pytest.raises(io.ScIOError, match="disagree on"):
        io.concatenate([("pool", io.read_counts(path))], {"pool": {"condition": "A"}})


def test_complete_run_uses_the_samples_from_a_single_h5ad(tmp_path):
    pytest.importorskip("anndata")
    world = make_sc_world(tmp_path / "world", n_genes=400, per_type=50, path_cells=50)
    rows = scrna.read_sheet(world.sheet)
    parts = [(row["sample"], io.read_counts(row["path"])) for row in rows]
    info = {r["sample"]: {"condition": r["condition"], "batch": r["batch"],
                          "donor": r["sample"]} for r in rows}
    matrix = io.concatenate(parts, info)
    source = tmp_path / "multiplexed.h5ad"
    io.to_anndata(matrix).write_h5ad(source)
    run = scrna.run_scrna(source, scrna.ScConfig(
        min_genes=40, n_top_genes=180, n_pcs=12, doublets="off", annotate=False,
        integration_method="none"), tmp_path / "out")
    assert run.complete
    assert set(run.manifest["samples"]) == set(info)
    assert run.pseudobulk.replicate_key == "donor"
    assert run.pseudobulk.results
    assert set(run.pseudobulk.unit_metadata) == set(info)


def test_outputs_belong_to_one_run_and_stale_tables_fail_verification(tmp_path):
    world = make_sc_world(tmp_path / 'world', n_genes=400, per_type=50, path_cells=50)
    out = tmp_path / "atlas"
    out.mkdir()
    (out / "user-note.txt").write_text("prior notes are not run outputs")
    config = scrna.ScConfig(min_genes=40, n_pcs=12, n_top_genes=180, annotate=False,
                            doublets="off", integration_method="none")
    run = scrna.run_scrna(world.sheet, config, out)
    assert run.complete and run.pseudobulk.results
    assert "user-note.txt" not in run.manifest["outputs"]
    before = (out / "run.json").read_bytes()
    with pytest.raises(scrna.ScError, match="fresh output directory"):
        scrna.run_scrna(world.root / "ctrl1", config, out)
    assert (out / "run.json").read_bytes() == before
    (out / "pseudobulk" / "old.tsv").write_text("gene\tFDR\n")
    assert not scrna.verify_run(out)[0]
