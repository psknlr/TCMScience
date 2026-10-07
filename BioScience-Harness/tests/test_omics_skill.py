"""The RNA-seq pipeline as a governed candidate skill."""

from __future__ import annotations

import json

import pytest

from bioagent.contracts import check_claim
from bioagent.governed import GovernedRunRefused, run_governed
from omics_world import DOWN, UP, make_world

pytestmark = pytest.mark.unit

from pathlib import Path                                         # noqa: E402

CANDIDATES = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "omics"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return make_world(tmp_path_factory.mktemp("skill-world"), paired=False, seed=4)


def _args(world, out, design="in_vitro"):
    return {"sample_sheet": str(world.sheet), "transcripts": str(world.transcripts),
            "annotation": str(world.gtf), "engine": "builtin", "trimmer": "builtin",
            "experiment_design": design, "out_dir": str(out)}


def test_a_candidate_is_refused_until_it_is_promoted(world, tmp_path):
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("rnaseq-differential-expression", _args(world, tmp_path / "run"),
                     skill_dir=CANDIDATES, state_dir=tmp_path / "psh")


@pytest.fixture(scope="module")
def dev_run(world, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("skill-run")
    return run_governed("rnaseq-differential-expression", _args(world, tmp / "run"),
                        skill_dir=CANDIDATES, state_dir=tmp / "psh",
                        output_dir=tmp / "out", allow_unpinned=True)


def test_a_development_run_is_valid_but_not_released(dev_run):
    assert not dev_run.released and not dev_run.verdict.execution_attested
    assert dev_run.verdict.publishable, dev_run.verdict.codes


def test_cells_support_a_hypothesis_and_its_evidence_is_the_table(dev_run):
    art = dev_run.artifact
    (claim,) = art.claims
    assert claim.claim_kind == "mechanism_hypothesis"
    assert check_claim(claim, {e.id: e for e in art.evidence}).allowed
    cited = {e.subject for e in art.evidence}
    assert set(UP) | set(DOWN) <= cited
    assert all(e.has_quote_receipt and e.design == "in_vitro" for e in art.evidence)
    assert "nothing here describes people" in " ".join(art.limitations)


def test_human_samples_support_an_association(world, tmp_path):
    run = run_governed("rnaseq-differential-expression",
                       _args(world, tmp_path / "run", design="observational"),
                       skill_dir=CANDIDATES, state_dir=tmp_path / "psh",
                       output_dir=tmp_path / "out", allow_unpinned=True)
    (claim,) = run.artifact.claims
    assert claim.claim_kind == "association"
    assert check_claim(claim, {e.id: e for e in run.artifact.evidence}).allowed


def test_the_outputs_name_the_run(dev_run):
    out = Path(dev_run.output_dir)
    summary = json.loads((out / "rnaseq_summary.json").read_text())
    assert summary["summary"]["engine"] == "builtin"
    assert (Path(summary["out_dir"]) / "report.html").is_file()
    assert (out / "deseq2_results.tsv").is_file() and (out / "report.md").is_file()


def test_an_unknown_design_is_refused(world, tmp_path):
    from bioagent.skills.omics import rnaseq_differential_expression
    with pytest.raises(ValueError, match="experiment_design"):
        rnaseq_differential_expression(str(world.sheet), transcripts=str(world.transcripts),
                                       experiment_design="randomized_trial",
                                       out_dir=str(tmp_path))
