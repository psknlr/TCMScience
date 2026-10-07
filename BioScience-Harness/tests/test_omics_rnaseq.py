"""The RNA-seq pipeline end to end, on a simulated experiment with a known answer."""

from __future__ import annotations

import json
import shutil

import numpy as np
import pytest

from bioagent.omics import rnaseq as R
from omics_world import DOWN, UP, make_world, need_tools

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("rnaseq-world"), adapter_rate=0.05)


@pytest.fixture(scope="module")
def builtin(world, tmp_path_factory):
    out = tmp_path_factory.mktemp("rnaseq-builtin")
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="builtin")
    return R.run_rnaseq(world.sheet, config, out)


def _called(run):
    res = run.result
    return {g: float(res.log2_fold_change[i]) for i, g in enumerate(res.genes)
            if np.isfinite(res.p_adjusted[i]) and res.p_adjusted[i] < 0.05}


def test_the_planted_genes_are_found(builtin):
    called = _called(builtin)
    assert set(UP) | set(DOWN) <= set(called), called
    assert all(called[g] > 1 for g in UP) and all(called[g] < -1 for g in DOWN)
    assert len(set(called) - set(UP) - set(DOWN)) <= 1        # false discoveries


def test_the_contrast_and_engines_are_recorded(builtin):
    assert builtin.contrast == ("condition", "treated", "control")
    assert builtin.engine == "builtin" and builtin.trimmer == "builtin"
    for s in builtin.samples:
        q = builtin.quantification[s.name]
        assert q["mapping_rate"] > 0.95 and q["fragment_distribution"] == "estimated"
        assert builtin.trimming[s.name]["trimmed"].get("illumina_universal", 0) > 0


def test_pca_separates_the_groups(builtin):
    pc1 = dict(zip(builtin.pca["samples"], builtin.pca["pc1"]))
    ctrl = [v for k, v in pc1.items() if k.startswith("ctrl")]
    trt = [v for k, v in pc1.items() if k.startswith("trt")]
    assert max(ctrl) < min(trt) or max(trt) < min(ctrl)


def test_the_report_and_its_digests(builtin):
    out = builtin.out_dir
    for name in ("report.md", "report.html", "run.json", "counts.tsv", "tpm.tsv",
                 "deseq2_results.tsv", "vst.tsv"):
        assert (out / name).is_file(), name
    for plot in ("pca", "ma", "volcano", "p_values", "dispersion", "sample_distances",
                 "mapping"):
        svg = (out / "plots" / f"{plot}.svg").read_text()
        assert svg.startswith("<svg") and "<title>" in svg
    md = (out / "report.md").read_text()
    assert "## What this result is" in md and "does not establish a mechanism" in md
    assert "<svg" in (out / "report.html").read_text()
    manifest = json.loads((out / "run.json").read_text())
    assert manifest["engine"] == "builtin" and manifest["contrast"][1] == "treated"
    assert len(manifest["inputs"]) == 6 * 2 + 2
    ok, problems = R.verify_run(out)
    assert ok, problems


def test_an_edited_result_is_caught(builtin, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(builtin.out_dir, copy, ignore=shutil.ignore_patterns("index", "trimmed"))
    table = copy / "deseq2_results.tsv"
    table.write_text(table.read_text().replace("G000", "G999", 1))
    ok, problems = R.verify_run(copy)
    assert not ok and any("deseq2_results.tsv has changed" in p for p in problems)


def test_a_rerun_gives_the_same_numbers(world, builtin, tmp_path):
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="builtin")
    again = R.run_rnaseq(world.sheet, config, tmp_path / "again")
    for name in ("deseq2_results.tsv", "counts.tsv", "report.md"):
        assert (tmp_path / "again" / name).read_text() == (builtin.out_dir / name).read_text()
    assert again.manifest["code_digest"] == builtin.manifest["code_digest"]


def test_sample_sheets_are_checked(tmp_path, world):
    sheet = tmp_path / "s.csv"
    sheet.write_text("name,fastq_1\nx,a.fq\n")
    with pytest.raises(R.RNASeqError, match="'sample' column"):
        R.read_sample_sheet(sheet)
    sheet.write_text("sample,fastq_1,condition\nx,missing.fq,a\n")
    with pytest.raises(R.RNASeqError, match="does not exist"):
        R.read_sample_sheet(sheet)
    r1 = world.root / "ctrl1_R1.fastq.gz"
    r2 = world.root / "ctrl1_R2.fastq.gz"
    sheet.write_text(f"sample,fastq_1,fastq_2,condition\nx,{r1},{r2},a\ny,{r1},,b\n")
    with pytest.raises(R.RNASeqError, match="mixes single-end and paired-end"):
        R.read_sample_sheet(sheet)
    sheet.write_text(f"sample,fastq_1,fastq_2,condition\nx,{r1},{r2},a\nx,{r1},{r2},a\n")
    (merged,) = R.read_sample_sheet(sheet)
    assert len(merged.fastq_1) == 2 and merged.paired
    sheet.write_text(f"sample,fastq_1,fastq_2,condition\nx,{r1},{r2},a\nx,{r1},{r2},b\n")
    with pytest.raises(R.RNASeqError, match="lanes disagree"):
        R.read_sample_sheet(sheet)


def test_a_contrast_must_name_real_levels(world, tmp_path):
    config = R.RNASeqConfig(transcripts=world.transcripts, contrast=("condition", "x", "y"),
                            engine="builtin")
    with pytest.raises(R.RNASeqError, match="no level 'x'"):
        R.run_rnaseq(world.sheet, config, tmp_path)


def test_a_numeric_batch_is_a_factor(tmp_path):
    from bioagent.omics import deseq
    rows = [{"batch": b, "condition": c} for b, c in
            [("1", "a"), ("2", "a"), ("1", "b"), ("2", "b"), ("1", "a"), ("2", "b")]]
    dm = deseq.design_matrix(rows, "~ batch + condition", covariates=())
    assert "batch[2]" in dm.columns
    dm = deseq.design_matrix(rows, "~ batch + condition")
    assert "batch" in dm.columns                         # the old auto-detection


@pytest.mark.parametrize("engine", ["salmon", "kallisto"])
def test_the_standard_quantifiers_agree_with_the_builtin_one(world, builtin, tmp_path,
                                                             engine):
    need_tools(engine)
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine=engine, trimmer="builtin")
    run = R.run_rnaseq(world.sheet, config, tmp_path)
    assert run.engine == engine
    a = np.log1p(run.genes.counts).ravel()
    b = np.log1p(builtin.genes.counts).ravel()
    assert np.corrcoef(a, b)[0, 1] > 0.98
    assert set(UP) | set(DOWN) <= set(_called(run))
    assert any(s["tool"] == engine for s in run.manifest["steps"])


def test_fastp_trims_like_the_builtin_trimmer(world, tmp_path):
    need_tools("fastp")
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="fastp")
    run = R.run_rnaseq(world.sheet, config, tmp_path)
    for s in run.samples:
        t = run.trimming[s.name]
        assert t["engine"].startswith("fastp") and t["reads_out"] >= 0.9 * t["reads_in"]
    assert set(UP) | set(DOWN) <= set(_called(run))


def test_alignment_with_hisat2_and_featurecounts(world, tmp_path):
    need_tools("hisat2", "hisat2-build", "samtools", "featureCounts")
    config = R.RNASeqConfig(genome=world.genome, annotation=world.gtf, engine="hisat2",
                            trimmer="builtin")
    run = R.run_rnaseq(world.sheet, config, tmp_path)
    assert run.engine == "hisat2"
    assert all(run.quantification[s.name]["mapping_rate"] > 0.9 for s in run.samples)
    assert set(UP) | set(DOWN) <= set(_called(run))
