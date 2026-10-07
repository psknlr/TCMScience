"""Defects in two P0 skills, found while writing the conformance benchmark's gold answers.

analyze-tcm-network-pharmacology:
* read ``relation.subject``, which does not exist, and crashed on 四君子汤;
* reported herb → syndrome ``treats`` relations as predicted targets;
* filled ``measured_targets`` from the studies behind safety records, which name no target;
* marked its own evidence extrapolated, which blocked the hypothesis claim it supports.

assess-tcm-safety listed a 十八反 pair's record twice when both herbs were queried.
"""

from __future__ import annotations

import json

import pytest

from bioagent.contracts import check_claim, validate_artifact
from bioagent.contracts.receipts import default_store
from bioagent.skills.p0 import analyze_tcm_network_pharmacology, assess_tcm_safety
from bioagent.tcm import knowledge as tcm_knowledge
from bioagent.tcm.model import ActionRelation, EvidenceTier

pytestmark = pytest.mark.unit


def _payload(artifact) -> dict:
    return json.loads(default_store.get(artifact.outputs[0].sha256))


@pytest.mark.parametrize("formula", ["桂枝汤", "麻黄汤", "四逆汤", "四君子汤", "四物汤",
                                     "补中益气汤"])
def test_every_seed_formula_is_analysed(formula):
    artifact = analyze_tcm_network_pharmacology(formula, run_id="t")
    assert validate_artifact(artifact, content_store=default_store).publishable
    body = _payload(artifact)
    assert body["measured_targets"] == [] and body["predicted_targets"] == []
    assert not artifact.claims                 # no target network, so nothing to claim


def test_a_syndrome_relation_is_an_indication_not_a_target():
    body = _payload(analyze_tcm_network_pharmacology("四君子汤", run_id="t"))
    indications = {(e["subject"], e["predicate"], e["object"])
                   for e in body["recorded_indications"]}
    assert ("herb.renshen", "treats", "syndrome.qixu") in indications
    assert ("formula.sijunzitang", "indicated_for", "syndrome.piwei_qixu") in indications


def test_a_recorded_target_relation_is_a_hypothesis_its_claim_may_state(monkeypatch):
    kb = tcm_knowledge.seed()
    relation = ActionRelation(id="relation.test_target", subject_id="herb.renshen",
                              predicate="targets", object_id="uniprot:P35354",
                              tier=EvidenceTier.CLASSICAL_TEXT,
                              evidence_ids=("study.pharmacopoeia_2020",))
    kb.relations[relation.id] = relation
    monkeypatch.setattr(tcm_knowledge, "default_knowledge", lambda: kb)
    artifact = analyze_tcm_network_pharmacology("四君子汤", run_id="t")
    body = _payload(artifact)
    assert [t["target"] for t in body["predicted_targets"]] == ["uniprot:P35354"]
    assert body["measured_targets"] == []
    (claim,) = artifact.claims
    assert claim.claim_kind == "mechanism_hypothesis"
    assert check_claim(claim, {e.id: e for e in artifact.evidence}).allowed
    assert validate_artifact(artifact, content_store=default_store).publishable


def test_a_pairs_record_is_listed_once():
    body = _payload(assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="t"))
    ids = [(r["subject"], r["counterpart"], r["kind"], r["combination"])
           for r in body["critical_records"]]
    assert len(ids) == len(set(ids))
    assert ids.count(("herb.gancao", "herb.gansui", "incompatibility", False)) == 1
    assert ids.count(("herb.gancao", "herb.gansui", "incompatibility", True)) == 1
