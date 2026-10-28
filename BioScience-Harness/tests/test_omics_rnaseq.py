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
    for name in ("deseq2_results.tsv", "counts.tsv", "report.md", "limitations.md"):
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


@pytest.mark.parametrize("alpha", [0.0, 1.0, 1.5, -0.1])
def test_an_fdr_level_outside_0_to_1_is_refused_before_anything_is_written(world, tmp_path,
                                                                           alpha):
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", alpha=alpha)
    with pytest.raises(R.RNASeqError, match="alpha is the FDR level, between 0 and 1"):
        R.run_rnaseq(world.sheet, config, tmp_path / "run")
    assert not (tmp_path / "run").exists()


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


# ---------------------------------------------------------------- the run record

def test_every_stage_is_recorded_in_order_and_the_run_is_complete(builtin):
    out = builtin.out_dir
    manifest = json.loads((out / "run.json").read_text())
    stages = manifest["stages"]
    assert [s["name"] for s in stages] == list(R.STAGES)
    assert all(s["status"] == "succeeded" and s["required"] for s in stages)
    assert all(s["device"] == "cpu" and s["seconds"] is not None for s in stages)
    checked = {s["name"]: s["check"] for s in stages if s["check"]}
    assert checked == {"validate_reference": "PASS", "validate_fastq": "PASS",
                       "verify": "PASS"}
    engines = {s["name"]: s["engine"] for s in stages}
    assert engines["quantify"] == "builtin-quant" and engines["trim"] == "builtin-trim"
    assert engines["differential_expression"] == "builtin-de"
    assert manifest["complete"] is True and builtin.complete
    assert json.loads((out / "stages.json").read_text())["stages"] == stages
    # every file a stage names is an output the record digests
    listed = set(manifest["outputs"])
    assert {o for s in stages for o in s["outputs"]} <= listed
    assert "stages.json" not in listed and "limitations.md" in listed
    # what the reads and the reference were checked for, under a key of its own
    reads = manifest["validation"]["reads"]["ctrl1"]
    assert reads["R1"]["reads"] == reads["R2"]["reads"] and reads["paired"]
    assert manifest["validation"]["reference"]["annotation"]["unannotated"] == 0
    # no timestamp or timing reaches the deterministic files
    for name in ("report.md", "limitations.md", "deseq2_results.tsv"):
        assert "seconds" not in (out / name).read_text()


def test_the_limitations_file_is_the_runs_limitations(builtin):
    lines = (builtin.out_dir / "limitations.md").read_text().splitlines()
    assert lines == builtin.manifest["limitations"] == builtin.limitations
    text = " ".join(lines)
    assert "does not establish a mechanism" in text
    assert "docs/omics-backends.md" in text and "PyDESeq2" in text
    # this world is the registered simulated reference, and the run says so
    assert builtin.manifest["reference_dataset"]["id"] == "omics_world"
    assert "simulated, known answer" in text and "not public data" in text


def test_a_reference_the_annotation_does_not_know_stops_the_run_early(world, tmp_path):
    gtf = tmp_path / "other.gtf"
    gtf.write_text(world.gtf.read_text().replace('transcript_id "G', 'transcript_id "X'))
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=gtf, engine="builtin",
                            trimmer="builtin")
    with pytest.raises(R.RNASeqError, match="no transcript of .* is in the annotation"):
        R.run_rnaseq(world.sheet, config, tmp_path / "out")
    assert not (tmp_path / "out" / "trimmed").exists()          # nothing was spent on reads
    assert not (tmp_path / "out" / "merged").exists()           # nor on merging lanes
    stages = json.loads((tmp_path / "out" / "stages.json").read_text())["stages"]
    assert [(s["name"], s["status"]) for s in stages] == [("validate_reference", "failed")]


def test_a_genome_and_gtf_from_different_sources_are_refused():
    from bioagent.omics import quant
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        genome, gtf = Path(d) / "g.fa", Path(d) / "a.gtf"
        genome.write_text(">1\nACGTACGT\n>2\nACGT\n")
        gtf.write_text('chr1\tsim\texon\t1\t4\t.\t+\t.\tgene_id "G"; transcript_id "T";\n')
        with pytest.raises(quant.QuantError, match="sequence names the genome does not have"):
            quant.validate_reference(annotation=gtf, genome=genome)
        gtf.write_text('1\tsim\texon\t1\t4\t.\t+\t.\tgene_id "G"; transcript_id "T";\n')
        rec = quant.validate_reference(annotation=gtf, genome=genome)
        assert rec["genome"]["contigs"] == 2 and rec["genome"]["annotated_seqnames"] == 1
        table = Path(d) / "t2g.csv"
        table.write_text("T,G\n")
        with pytest.raises(quant.QuantError, match="needs a GTF annotation"):
            quant.validate_reference(annotation=table, genome=genome)
        fasta = Path(d) / "tx.fa"
        fasta.write_text(">T1\nACGT\n>T1\nACGA\n")
        with pytest.raises(quant.QuantError, match="appear more than once"):
            quant.validate_reference(transcripts=fasta)


def test_out_of_step_mates_are_refused_before_qc(world, tmp_path):
    import gzip
    lines = gzip.open(world.root / "ctrl1_R2.fastq.gz", "rt").read().splitlines()
    lines[0] = lines[0].replace("ctrl1.0/2", "ctrl1.9/2")
    bad = tmp_path / "ctrl1_R2.fastq.gz"
    with gzip.open(bad, "wt") as fh:
        fh.write("\n".join(lines) + "\n")
    rows = world.sheet.read_text().splitlines()
    for k, row in enumerate(rows[1:], start=1):
        name, r1, r2, condition = row.split(",")
        r2 = str(bad) if name == "ctrl1" else str(world.root / r2)
        rows[k] = ",".join((name, str(world.root / r1), r2, condition))
    sheet = tmp_path / "s.csv"
    sheet.write_text("\n".join(rows) + "\n")
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="none")
    with pytest.raises(R.RNASeqError, match="sample ctrl1: pair 1 is out of step"):
        R.run_rnaseq(sheet, config, tmp_path / "out")
    stages = json.loads((tmp_path / "out" / "stages.json").read_text())["stages"]
    assert stages[-1]["name"] == "validate_fastq" and stages[-1]["status"] == "failed"


def test_a_stage_that_fails_is_recorded_and_the_earlier_ones_kept(world, tmp_path,
                                                                  monkeypatch):
    from bioagent.omics import quant

    def broken(*a, **k):
        raise quant.QuantError("summarising failed")
    monkeypatch.setattr(quant, "summarize_to_gene", broken)
    config = R.RNASeqConfig(transcripts=world.transcripts, annotation=world.gtf,
                            engine="builtin", trimmer="none")
    with pytest.raises(quant.QuantError):
        R.run_rnaseq(world.sheet, config, tmp_path)
    doc = json.loads((tmp_path / "stages.json").read_text())
    states = [(s["name"], s["status"]) for s in doc["stages"]]
    assert states[-1] == ("summarize_genes", "failed")
    assert all(status == "succeeded" for name, status in states[:-1] if name != "trim")
    assert ("trim", "skipped") in states and doc["running"] is None
    assert "QuantError: summarising failed" in doc["stages"][-1]["reason"]


def test_verify_run_checks_completeness_and_content(builtin, tmp_path):
    import hashlib
    copy = tmp_path / "copy"
    shutil.copytree(builtin.out_dir, copy, ignore=shutil.ignore_patterns("index", "trimmed"))
    assert R.verify_run(copy)[0]
    manifest = json.loads((copy / "run.json").read_text())

    def rewrite(doc):
        (copy / "run.json").write_text(json.dumps(doc))

    # a table cut to its header, re-digested so only the content check can see it
    table = copy / "deseq2_results.tsv"
    original = table.read_text()
    table.write_text(original.splitlines()[0] + "\n")
    edited = json.loads(json.dumps(manifest))
    edited["outputs"]["deseq2_results.tsv"] = hashlib.sha256(table.read_bytes()).hexdigest()
    rewrite(edited)
    ok, problems = R.verify_run(copy)
    assert not ok and any("deseq2_results.tsv: 0 rows" in p for p in problems), problems
    table.write_text(original)
    rewrite(manifest)
    # a stage naming an output that is not there
    edited = json.loads(json.dumps(manifest))
    edited["stages"][-2]["outputs"].append("plots/heatmap.svg")
    rewrite(edited)
    ok, problems = R.verify_run(copy)
    assert not ok and any("plots/heatmap.svg is not among" in p for p in problems)
    # a recorded output deleted
    rewrite(manifest)
    (copy / "plots" / "pca.svg").unlink()
    ok, problems = R.verify_run(copy)
    assert not ok and "output plots/pca.svg is missing" in problems
    shutil.copy(builtin.out_dir / "plots" / "pca.svg", copy / "plots" / "pca.svg")
    # a run recorded as incomplete
    rewrite({**manifest, "complete": False,
             "stages": [{**s, "status": "unavailable", "reason": "no VST"}
                        if s["name"] == "explore" else s for s in manifest["stages"]]})
    ok, problems = R.verify_run(copy)
    assert not ok and "stage explore unavailable here: no VST" in problems
    assert "run.json records the run as incomplete" in problems
    # a record written before stages: the digest checks only, as before
    rewrite({k: v for k, v in manifest.items() if k not in ("stages", "complete")})
    assert R.verify_run(copy) == (True, [])


def test_the_cli_exits_1_for_an_incomplete_run(world, tmp_path, monkeypatch, capsys):
    from bioagent import cli
    monkeypatch.setattr(R, "_content_problems",
                        lambda out, manifest: ["deseq2_results.tsv: a planted mismatch"])
    code = cli.main(["rnaseq", "--samples", str(world.sheet), "--transcripts",
                     str(world.transcripts), "--annotation", str(world.gtf), "--engine",
                     "builtin", "--trimmer", "none", "--out", str(tmp_path), "--json"])
    assert code == 1
    err = capsys.readouterr().err
    assert "incomplete: stage verify: its check failed: deseq2_results.tsv: a planted " \
           "mismatch" in err
    manifest = json.loads((tmp_path / "run.json").read_text())
    assert manifest["complete"] is False and not R.verify_run(tmp_path)[0]
