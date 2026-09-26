"""The governance around snapshots: the ledger, the release check, and skill programs.

* the ledger is append-only and hash-chained, and ``load_snapshot`` trusts it over the
  snapshot directory;
* a candidate claim is released only if its edges exist, connect its subject to its
  object, stay under the skill's ceiling, and license its kind by the weakest link;
* a ``skill.yaml`` compiles into a PSH ``ScientificProgram`` and PSH refuses what the
  evidence designs cannot license.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from bioagent.providers.skills import SkillContract, SkillContractError
from bioagent.sources import build_snapshot, load_snapshot
from bioagent.sources.ledger import LedgerError, SnapshotLedger
from bioagent.sources.release import (CandidateClaim, ReleaseRefused, check_release,
                                      path_licenses, require_release)
from bioagent.sources.snapshot import SnapshotError

HERB, SPECIES = "tcm:herb.gegen", "ncbitaxon:3893"
PUERARIN = "inchikey:HKEAFJYKMMKDOR-VPRICQMDSA-N"
COX2 = "uniprot:P35354"


def _edge(record, subject, predicate, obj, design, level="knowledge_assertion", **kw):
    return {"subject": subject, "predicate": predicate, "object": obj,
            "primary_knowledge_source": "fixture", "knowledge_level": level,
            "agent_type": "computational_model" if level == "prediction" else "manual_agent",
            "study_design": design, "license": "CC0-1.0", "source_record_id": record, **kw}


def _nodes():
    return [{"id": HERB, "category": "herb", "name": "葛根", "source": "fixture"},
            {"id": SPECIES, "category": "organism", "name": "Pueraria", "source": "fixture"},
            {"id": PUERARIN, "category": "ingredient", "name": "puerarin", "source": "fixture"},
            {"id": COX2, "category": "target", "name": "PTGS2", "source": "fixture"}]


EDGES = [
    _edge("base", HERB, "has_base_species", SPECIES, "expert_consensus"),
    _edge("c1", SPECIES, "contains", PUERARIN, "chemical_analysis", composition_level="C1",
          publications=["pmid:1"]),
    _edge("c3", SPECIES, "contains", PUERARIN, "chemical_analysis", composition_level="C3",
          publications=["pmid:2"]),
    _edge("ic50", PUERARIN, "targets", COX2, "in_vitro", publications=["pmid:3"]),
    _edge("dock", PUERARIN, "targets", COX2, "in_silico", level="prediction"),
]


@pytest.fixture
def snap(tmp_path):
    raw = tmp_path / "raw.txt"
    raw.write_text("fixture", encoding="utf-8")
    return build_snapshot(key="fixture", version="1", nodes=_nodes(), edges=EDGES,
                          raw_files={"raw.txt": raw}, parser="def p(): pass",
                          root=tmp_path / "snap", license="CC0-1.0", citation="doi:10/x")


# ================================================================ ledger

def test_the_ledger_records_builds_and_is_what_load_trusts(tmp_path, snap):
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    ledger.record(snap)
    loaded = load_snapshot(tmp_path / "snap", "fixture", "1", ledger=ledger)
    assert loaded.snapshot_id == snap.snapshot_id and ledger.verify() == 1
    with pytest.raises(LedgerError, match="no snapshot"):
        load_snapshot(tmp_path / "snap", "fixture", "2", ledger=ledger)
    with pytest.raises(SnapshotError, match="ledger recorded"):
        load_snapshot(tmp_path / "snap", "fixture", "1", ledger=ledger,
                      expected_id="fixture@1#000000000000")


def test_a_consistently_rewritten_snapshot_is_caught_by_the_ledger(tmp_path, snap):
    """Tables, manifest and id rewritten together pass self-verification; the ledger,
    kept elsewhere, still says what was built."""
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    ledger.record(snap)
    raw = tmp_path / "raw.txt"
    tampered = [dict(e) for e in EDGES]
    tampered[4] = {**tampered[4], "knowledge_level": "knowledge_assertion",
                   "agent_type": "manual_agent", "study_design": "in_vitro",
                   "publications": ["pmid:9"]}                  # a prediction "upgraded"
    build_snapshot(key="fixture", version="1", nodes=_nodes(), edges=tampered,
                   raw_files={"raw.txt": raw}, parser="def p(): pass",
                   root=tmp_path / "snap", license="CC0-1.0", citation="doi:10/x")
    assert load_snapshot(tmp_path / "snap", "fixture", "1")        # self-consistent
    with pytest.raises(SnapshotError, match="not the recorded"):
        load_snapshot(tmp_path / "snap", "fixture", "1", ledger=ledger)


def test_an_edited_ledger_breaks_its_chain(tmp_path, snap):
    ledger = SnapshotLedger(tmp_path / "ledger.jsonl")
    ledger.record(snap)
    ledger.record(snap)
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["snapshot_id"] = "fixture@1#ffffffffffff"
    ledger.path.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError, match="entry 1"):
        ledger.verify()
    ledger.path.write_text(lines[1] + "\n", encoding="utf-8")       # entry deleted
    with pytest.raises(LedgerError):
        ledger.verify()


def test_build_records_into_a_ledger(tmp_path):
    raw = tmp_path / "raw.txt"
    raw.write_text("x", encoding="utf-8")
    ledger = SnapshotLedger(tmp_path / "ledger.jsonl")
    built = build_snapshot(key="fixture", version="1", nodes=_nodes(), edges=EDGES,
                           raw_files={"raw.txt": raw}, parser="p", root=tmp_path / "s",
                           license="CC0-1.0", citation="doi:10/x", ledger=ledger)
    assert ledger.expected("fixture", "1") == built.snapshot_id


# ================================================================ release check

def _claim(kind, subject, obj, *records, snap_id):
    return CandidateClaim(kind, subject, obj, tuple((snap_id, r) for r in records))


def test_a_path_is_only_as_strong_as_its_weakest_link():
    by = {e["source_record_id"]: e for e in EDGES}
    assert path_licenses([by["ic50"]]) == {"mechanism_hypothesis", "mechanism"}
    assert path_licenses([by["dock"]]) == {"mechanism_hypothesis"}
    herb_c1 = [by["base"], by["c1"], by["ic50"]]
    assert path_licenses(herb_c1) == {"mechanism_hypothesis"}         # C1 caps the herb
    herb_c3 = [by["base"], by["c3"], by["ic50"]]
    assert path_licenses(herb_c3) == {"mechanism_hypothesis", "mechanism"}
    assert path_licenses([by["base"], by["c1"]]) == frozenset()       # composition alone


def test_release_passes_supported_claims_and_says_why_it_refuses_the_rest(snap):
    sid = snap.snapshot_id
    claims = [
        _claim("mechanism", PUERARIN, COX2, "ic50", snap_id=sid),                  # ok
        _claim("mechanism_hypothesis", HERB, COX2, "base", "c1", "ic50", snap_id=sid),
        _claim("mechanism", HERB, COX2, "base", "c1", "ic50", snap_id=sid),        # C1 cap
        _claim("mechanism", PUERARIN, COX2, "dock", snap_id=sid),                  # prediction
        _claim("mechanism", PUERARIN, COX2, "nowhere", snap_id=sid),               # missing
        _claim("mechanism", HERB, COX2, "ic50", snap_id=sid),                      # unconnected
        _claim("efficacy", PUERARIN, COX2, "ic50", snap_id=sid),
        _claim("mechanism", PUERARIN, COX2, "ic50", snap_id="other@1#000000000000"),
        CandidateClaim("mechanism", PUERARIN, COX2, ()),
    ]
    verdict = check_release(claims, [snap])
    assert [c.kind for c in verdict.released] == ["mechanism", "mechanism_hypothesis"]
    reasons = [r for _, r in verdict.refused]
    assert "not mechanism" in reasons[0] and "mechanism_hypothesis" in reasons[0]
    assert "not mechanism" in reasons[1]
    assert "not found" in reasons[2] and "do not connect" in reasons[3]
    assert "not efficacy" in reasons[4] and "not found" in reasons[5]
    assert "no supporting edge" in reasons[6]
    with pytest.raises(ReleaseRefused):
        require_release(claims, [snap])


def test_release_holds_a_skill_to_its_ceiling(snap):
    contract = SkillContract(id="tcm.np", version="1", max_claim_kind="mechanism_hypothesis")
    claim = _claim("mechanism", PUERARIN, COX2, "ic50", snap_id=snap.snapshot_id)
    verdict = check_release([claim], [snap], contract=contract)
    assert not verdict.ok and "ceiling" in verdict.refused[0][1]


# ================================================================ skill programs

def _contract(**kw):
    base = dict(id="tcm.network-pharmacology", version="0.1.0",
                tools=("sources.composition", "stats.enrichment_analysis"),
                steps={"composition": {"tool": "sources.composition"},
                       "enrichment": {"tool": "stats.enrichment_analysis",
                                      "after": ("composition",), "design": "in_silico"}})
    base.update(kw)
    return SkillContract(**base)


def test_steps_are_validated_against_tools_and_designs():
    with pytest.raises(SkillContractError, match="requires.tools"):
        _contract(steps={"x": {"tool": "network.topology"}})
    with pytest.raises(SkillContractError, match="unknown step"):
        _contract(steps={"x": {"tool": "sources.composition", "after": ("y",)}})
    with pytest.raises(SkillContractError, match="design"):
        _contract(steps={"x": {"tool": "sources.composition", "design": "chemical_analysis"}})


def test_a_skill_compiles_into_a_psh_program_and_psh_enforces_the_evidence():
    pytest.importorskip("psh")
    from psh.policy import PolicySnapshot
    from psh.runtime import PlanRejected
    from psh.workflow import ScientificCompiler

    from bioagent.psh.skill_program import ClaimScope, SkillProgramError, skill_program

    policy = PolicySnapshot(profile_id="skill-test", require_claim_support=False)
    scope = ClaimScope("adults with type 2 diabetes", "葛根芩连汤", "glycaemic pathways")
    program = skill_program(_contract(), scope, provenance=("npass@2.0+subset#abc",))
    compiled = ScientificCompiler().compile(program, policy.envelope(), policy=policy)
    assert compiled.validated.order == ("composition", "enrichment", "claim")
    assert program.contracts["enrichment"].evidence.provenance == ("npass@2.0+subset#abc",)

    with pytest.raises(SkillProgramError, match="ceiling"):
        skill_program(_contract(), scope, claim_kind="mechanism")
    # Even with the ceiling raised, PSH itself refuses what in_silico cannot license.
    raised = replace(_contract(), max_claim_kind="mechanism")
    with pytest.raises(PlanRejected, match="EVIDENCE103"):
        ScientificCompiler().compile(skill_program(raised, scope), policy.envelope(),
                                     policy=policy)
    with pytest.raises(SkillProgramError, match="no evidence step"):
        skill_program(_contract(steps={"composition": {"tool": "sources.composition"}}), scope)


def test_skill_yaml_with_steps_round_trips_through_the_block_parser(tmp_path):
    path = tmp_path / "skill.yaml"
    path.write_text("""id: tcm.network-pharmacology
version: 0.1.0
requires:
  tools:
    - sources.composition
    - stats.enrichment_analysis
steps:
  composition:
    tool: sources.composition
  enrichment:
    tool: stats.enrichment_analysis
    after:
      - composition
    design: in_silico
max_claim_kind: mechanism_hypothesis
""", encoding="utf-8")
    contract = SkillContract.load(path)
    assert contract.steps["enrichment"]["after"] == ("composition",)
    assert contract.as_dict()["steps"]["enrichment"]["design"] == "in_silico"


def test_pathway_membership_connects_but_does_not_limit_a_claim():
    pathway = "reactome:R-HSA-2162123"
    member = _edge("member", COX2, "participates_in", pathway, "expert_consensus")
    by = {e["source_record_id"]: e for e in EDGES}
    assert path_licenses([by["ic50"], member]) == {"mechanism_hypothesis", "mechanism"}
