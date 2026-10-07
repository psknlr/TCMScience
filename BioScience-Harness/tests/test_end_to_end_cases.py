"""The three end-to-end cases: every step as it ran, and every claim as the contract judged it.

Each case needs the optional implementations it runs (GSEApy and PyDESeq2; paper-qa;
Vina, Meeko, RDKit and gemmi). Without them a test skips, and fails under
``BIOAGENT_REQUIRE_TOOLS=1``. What is asserted is the chain: which implementation ran,
that the planted or recorded answer came back, that a missing engine is reported missing
and not replaced, and that each drafted claim got the verdict its evidence earns.
"""

from __future__ import annotations

import json

import pytest

from bioagent.cases.compound_hypothesis import OPEN_TARGETS, open_targets_rows
from bioagent.status import ExecutionStatus
from omics_world import need_module

OK = ExecutionStatus.SUCCEEDED


def _codes(report) -> dict[str, tuple[str, ...]]:
    return {c.purpose: c.codes for c in report.claims}


# ------------------------------------------------------------------------- case 1

@pytest.fixture(scope="module")
def rnaseq(tmp_path_factory):
    need_module("gseapy")
    from bioagent.cases.rnaseq_gsea import run_case
    return run_case(tmp_path_factory.mktemp("rnaseq"), permutations=500)


def test_counts_to_pathway_recovers_the_planted_answer(rnaseq):
    de, gsea = rnaseq.step("differential expression"), rnaseq.step("preranked GSEA")
    assert de.status is OK and de.implementation.startswith("builtin")
    assert "40/40 planted up and 40/40 planted down" in de.detail
    assert gsea.status is OK and gsea.implementation.startswith("gseapy")
    assert "untouched sets significant at FDR < 0.25: 0" in gsea.detail
    assert gsea.record["library"]["sha256"]


def test_a_pathway_enrichment_carries_a_hypothesis_and_nothing_more(rnaseq):
    assert rnaseq.claims_as_expected
    verdicts = {c.kind + ":" + c.purpose: c.allowed for c in rnaseq.claims}
    assert verdicts["mechanism:a gene-level finding the cell experiment measured"]
    assert verdicts["mechanism_hypothesis:the enrichment, stated as the hypothesis it is"]
    codes = _codes(rnaseq)
    assert "CLM005" in codes["the enrichment stated as an established mechanism"]
    assert "CLM017" in codes["a cell result placed at human exposure"]
    assert "CLM004" in codes["a pathway enrichment cited for patients"]


def test_pydeseq2_gives_the_same_claims(tmp_path):
    need_module("gseapy")
    need_module("pydeseq2")
    from bioagent.cases.rnaseq_gsea import run_case
    report = run_case(tmp_path, de_backend="pydeseq2", permutations=200)
    assert report.step("differential expression").implementation.startswith("pydeseq2")
    assert report.claims_as_expected


def test_a_missing_backend_stops_the_case_and_is_not_replaced(tmp_path, monkeypatch):
    from bioagent.cases.rnaseq_gsea import run_case
    from bioagent.omics import de_backends
    from bioagent.omics.optional import BackendUnavailable

    def absent(name):
        raise BackendUnavailable(name, "not installed (a stand-in for this test)")

    monkeypatch.setattr(de_backends, "check_backend", absent)
    report = run_case(tmp_path, de_backend="pydeseq2")
    assert [s.status for s in report.steps] == [ExecutionStatus.UNAVAILABLE]
    assert report.claims == [] and "no other backend" in report.limits[0]


# ------------------------------------------------------------------------- case 2

@pytest.fixture(scope="module")
def literature(tmp_path_factory):
    need_module("paperqa")
    from bioagent.cases.literature_claims import run_case
    return run_case(tmp_path_factory.mktemp("literature"))


def test_retrieved_passages_are_typed_or_withheld(literature):
    typed = literature.step("type the passages")
    assert set(typed.record["items"]) == {"lit.gegen.0", "lit.ctx.0"}
    assert typed.record["items"]["lit.gegen.0"]["design"] == "randomized_trial"
    (withheld,) = typed.record["withheld"]
    assert withheld["doc"] == "emperor" and "none is guessed" in withheld["reason"]


def test_retrieved_evidence_licenses_what_it_shows_after_review(literature):
    assert literature.claims_as_expected
    codes = _codes(literature)
    assert "CLM006" in codes[
        "the faithful claim, before anyone has assessed the trial's risk of bias"]
    assert codes["the same claim after the reviewer's assessment"] == ()
    assert "CLM009" in codes["a population the trial did not enrol"]
    assert "CLM019" in codes["an outcome the abstract does not report, stated as absent"]
    assert any("CLM002" in c.codes for c in literature.claims)


# ------------------------------------------------------------------------- case 3

def test_the_disease_is_chosen_by_genetics_and_every_datatype_is_kept():
    rows = open_targets_rows(json.loads(OPEN_TARGETS.read_text(encoding="utf-8")))
    chosen = max(rows, key=lambda r: r["datatypes"].get("genetic_association", 0.0))
    assert chosen["disease"] == "hereditary chronic pancreatitis"
    assert set(chosen["datatypes"]) >= {"genetic_association", "literature"}
    by_overall = max(rows, key=lambda r: r["overall"])
    assert by_overall["disease"] == chosen["disease"]          # here they agree
    led_by_literature = [r["disease"] for r in rows
                         if r["datatypes"].get("literature", 0)
                         > r["datatypes"].get("genetic_association", 0)]
    assert led_by_literature == ["pancreatitis"]


@pytest.fixture(scope="module")
def compound(tmp_path_factory):
    for module in ("rdkit", "meeko", "vina", "gemmi"):
        need_module(module)
    from bioagent.cases.compound_hypothesis import run_case
    return run_case(tmp_path_factory.mktemp("compound"))


def test_docking_runs_only_on_a_validated_setup(compound):
    dock = compound.step("docking")
    assert dock.status is OK and "(passed)" in dock.detail
    assert dock.record["validation"]["top_pose_rmsd"] < 2.0


def test_without_built_models_admet_reports_rules_and_alerts_only(compound):
    step = compound.step("ADMET")
    assert step.status is OK and step.detail.startswith("rules and alerts only")
    assert step.record["models_built"] is False and not step.record["model_cards"]


def test_built_admet_models_predict_with_their_domain_and_license_nothing(tmp_path):
    for module in ("rdkit", "sklearn", "meeko", "vina", "gemmi"):
        need_module(module)
    from admet_world import make_archive
    from bioagent.admet.models import build_models
    from bioagent.cases.compound_hypothesis import run_case

    cache = tmp_path / "admet"
    build_models(cache, archive=make_archive(tmp_path / "tdc.zip"),
                 endpoints=["caco2_wang", "hia_hou"], max_iter=50, log=lambda *_: None)
    report = run_case(tmp_path / "case", admet_models=cache)
    step = report.step("ADMET")
    assert step.status is OK and step.detail.startswith("2 endpoints predicted")
    predictions = step.record["molecule"]["predictions"]
    assert set(predictions) == {"caco2_wang", "hia_hou"} == set(step.record["model_cards"])
    assert all(isinstance(p["in_domain"], bool) for p in predictions.values())
    assert report.claims_as_expected, "a model's prediction licenses no claim of its own"


def test_a_missing_engine_is_reported_not_approximated(compound):
    step = compound.step("complex prediction")
    if step.status is not OK:
        assert step.status is ExecutionStatus.UNAVAILABLE
        assert "no model was run" in step.detail and step.record["ran"] is False


def test_docking_carries_a_hypothesis_and_nothing_more(compound):
    assert compound.claims_as_expected
    allowed = [c for c in compound.claims if c.allowed]
    assert [c.kind for c in allowed] == ["mechanism_hypothesis"]
    codes = _codes(compound)
    assert "CLM005" in codes["the same finding stated as a measured mechanism; true or "
                             "not, a docking score does not show it"]
    assert "CLM004" in codes["the target's genetic association carried to the compound, "
                             "as a treatment"]
    assert "CLM017" in codes["a docking result placed at human exposure"]
