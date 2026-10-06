"""Statistics and execution provenance, after the October 2026 audit (AUD-06, 13, 14).

AUD-06  the generic enrichment tool adjusted only the sets the query happened to hit;
AUD-13  an artifact's two attestation fields were read as an attestation;
AUD-14  a resumed run reported the current configuration as the history of its stages.

Each test starts from the audit's own example.
"""

from __future__ import annotations

import dataclasses as dc
import json
import sqlite3
from pathlib import Path

import pytest

from bioagent.contracts import validate_artifact
from bioagent.contracts.artifact import ResearchArtifact
from bioagent.contracts.attestation import AuditChainAttestor
from bioagent.contracts.receipts import default_store
from bioagent.governed import run_governed
from bioagent.research.loop import _digest
from bioagent.skills.p0.entities import normalize_tcm_entities
from bioagent.tools import stats
from test_research_loop import PROTEINS, _run, _world

pytestmark = pytest.mark.unit

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills" / "tcm"


# ============================================ AUD-06: the family is fixed beforehand

def _audit_sets():
    """The audit's case: 100 sets of 50 in a background of 1,000; one shares 2 of the 5
    query genes."""
    query = [f"Q{i}" for i in range(5)]
    filler = iter(f"G{i}" for i in range(10_000))
    sets = {}
    for k in range(100):
        members = [next(filler) for _ in range(50)]
        if k == 0:
            members[:2] = query[:2]
        sets[f"set{k:03d}"] = members
    return query, sets


def test_the_adjustment_covers_every_set_tested_not_only_those_hit():
    query, sets = _audit_sets()
    out = stats.enrichment_analysis(query, sets, background=1000)
    (row,) = out["results"]
    assert row["set"] == "set000" and row["p_value"] == pytest.approx(0.0222478323)
    assert row["q_value"] == 1.0                      # was 0.0222478323, "significant"
    assert out["significant_at_0_05"] == 0
    assert out["tests"] == 100 and out["shown"] == 1
    family = [stats.hypergeometric_test(2 if k == 0 else 0, 5, 50, 1000)["p_value"]
              for k in range(100)]
    assert row["q_value"] == stats.benjamini_hochberg(family)["q_values"][0]


def test_min_overlap_chooses_what_is_shown_not_what_is_tested():
    query, sets = _audit_sets()
    shown = stats.enrichment_analysis(query, sets, background=1000, min_overlap=3)
    assert shown["tests"] == 100 and shown["results"] == []
    planted = {**sets, "set000": query[:5] + sets["set000"][5:]}       # all 5 genes
    strong = stats.enrichment_analysis(query, planted, background=1000, min_overlap=6)
    assert strong["significant_at_0_05"] == 1 and strong["significant_not_shown"] == 1


def test_the_family_is_bounded_by_set_size_whatever_the_query():
    query, sets = _audit_sets()
    bounded = {**sets, "huge": [f"H{i}" for i in range(500)] + query[:3]}
    out = stats.enrichment_analysis(query, bounded, background=1000, max_set_size=100)
    assert out["tests"] == 100 and all(r["set"] != "huge" for r in out["results"])
    with pytest.raises(ValueError, match="nothing was tested"):
        stats.enrichment_analysis(query, sets, background=1000, max_set_size=10)
    with pytest.raises(ValueError, match="min_overlap"):
        stats.enrichment_analysis(query, sets, background=1000, min_overlap=-1)


# ============================== AUD-13: declared is not attested

def test_fields_that_name_a_policy_and_a_head_do_not_attest_a_run():
    """The audit's case: an otherwise valid artifact naming a policy that was never
    created and a head that is not a hash was execution_attested and release_authorized."""
    forged = dc.replace(normalize_tcm_entities(["黄芪"]), policy_id="policy-never-created",
                        audit_head="not-a-real-hash")
    verdict = validate_artifact(forged, content_store=default_store)
    assert verdict.publishable and verdict.evidence_verified and verdict.outputs_verified
    assert verdict.execution_declared and not verdict.execution_attested
    assert not verdict.release_authorized
    assert verdict.states["execution_declared"] is True
    assert any("not checked against an audit chain" in u for u in verdict.unverified)


@pytest.fixture
def governed(tmp_path):
    return run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=SKILLS_DIR,
                        state_dir=tmp_path / "psh", output_dir=tmp_path / "out")


def test_a_governed_run_is_attested_by_its_audit_chain(governed, tmp_path):
    assert governed.verdict.execution_attested and governed.released
    imported = ResearchArtifact.from_dict(json.loads(json.dumps(
        governed.artifact.document(), default=str)))
    with AuditChainAttestor.open(tmp_path / "psh") as attestor:
        again = validate_artifact(imported, output_root=tmp_path / "out",
                                  content_store=default_store, attestor=attestor)
        assert again.execution_attested and again.release_authorized


@pytest.mark.parametrize("change", [
    {"policy_id": "policy-never-created"},
    {"audit_head": "not-a-real-hash"},
    {"limitations": ("edited after release",)},
])
def test_an_artifact_that_differs_from_the_one_released_is_not_attested(governed, tmp_path,
                                                                        change):
    edited = dc.replace(governed.artifact, **change)
    with AuditChainAttestor.open(tmp_path / "psh") as attestor:
        verdict = validate_artifact(edited, output_root=tmp_path / "out",
                                    content_store=default_store, attestor=attestor)
    assert not verdict.execution_attested and not verdict.release_authorized
    assert "ART117" in verdict.codes


def test_an_edited_audit_chain_attests_nothing(governed, tmp_path):
    with sqlite3.connect(tmp_path / "psh" / "events.db") as db:
        db.execute("UPDATE events SET detail = '{}' WHERE seq = 1")
    with AuditChainAttestor.open(tmp_path / "psh") as attestor:
        ok, why = attestor.attest(governed.artifact)
    assert not ok and "does not verify" in why
    with pytest.raises(FileNotFoundError):
        AuditChainAttestor.open(tmp_path / "nowhere")


# ============================== AUD-14: a resumed stage keeps its own history

def test_a_resumed_run_does_not_promote_an_ungoverned_stage(tmp_path):
    """The audit's case: a first run in-process, then a resume under the default,
    governed configuration. The resume reused the in-process stages with no tool call
    and reported governed_execution: true."""
    from bioagent.analysis.network_pharmacology import run_network_pharmacology
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    first = _run(tmp_path, ledger, analyse=run_network_pharmacology)
    assert first.artifact.provenance["governed_execution"] is False
    resumed = _run(tmp_path, ledger)
    prov = resumed.artifact.provenance
    assert "analyse" not in resumed.resumed_stages and "rebut" not in resumed.resumed_stages
    assert prov["governed_execution"] is True and prov["tool_calls"] >= 2
    assert {r["mode"] for r in prov["execution"].values()} == {"governed"}


def test_a_governed_resume_reports_the_stages_as_they_ran(tmp_path):
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    first = _run(tmp_path, ledger)
    again = _run(tmp_path, ledger)
    assert again.resumed_stages == ("protocol", "retrieve", "analyse", "rebut")
    prov, before = again.artifact.provenance, first.artifact.provenance
    assert prov["governed_execution"] is True
    assert prov["tool_calls"] == before["tool_calls"] >= 2   # recorded, not recounted
    assert prov["execution"] == before["execution"]


def test_a_checkpoint_edited_to_claim_governance_is_not_reused(tmp_path):
    """The checkpoint is a cache; the audit chain is the record of what ran."""
    from bioagent.analysis.network_pharmacology import run_network_pharmacology
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    ungoverned = _run(tmp_path, ledger, analyse=run_network_pharmacology)
    elsewhere = _run(tmp_path, ledger, tag="governed")
    mine = Path(ungoverned.state_dir) / "research" / ungoverned.run_id
    theirs = Path(elsewhere.state_dir) / "research" / elsewhere.run_id
    for stage in ("02-analyse", "03-rebut"):
        claimed = json.loads((theirs / f"{stage}.json").read_text(encoding="utf-8"))
        doc = json.loads((mine / f"{stage}.json").read_text(encoding="utf-8"))
        doc["input"], doc["data"]["execution"] = claimed["input"], claimed["data"]["execution"]
        doc["digest"] = _digest({k: doc[k] for k in ("stage", "input", "data")})
        (mine / f"{stage}.json").write_text(json.dumps(doc), encoding="utf-8")
    resumed = _run(tmp_path, ledger)
    assert "analyse" not in resumed.resumed_stages
    assert resumed.artifact.provenance["tool_calls"] >= 2
