"""The closed research loop (audit F10): question → protocol → retrieve → analyse →
rebut → release, on snapshot data, under PSH audit, resumable."""

from __future__ import annotations

import dataclasses as dc
import json
from pathlib import Path

import pytest

from bioagent.contracts import validate_artifact
from bioagent.research import (QuestionRefused, ResearchRefused, default_protocol,
                               parse_question, run_research, snapshot_content_store)
from bioagent.research.loop import Protocol
from bioagent.sources import build_snapshot, load_snapshot
from bioagent.sources.herbs import GEGEN_QINLIAN, HERBS
from bioagent.sources.ledger import SnapshotLedger

from test_network_pharmacology import FAST, PROTEINS, _build, _edge

QUESTION = "葛根芩连汤的实测靶点是否集中在某条 Reactome 通路？"


def _world(tmp_path: Path, **kw) -> SnapshotLedger:
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    _build(tmp_path / "snap", ledger=ledger, **kw)
    return ledger


def _run(tmp_path: Path, ledger: SnapshotLedger, protocol=None, *, tag="a", **kw):
    q = parse_question(QUESTION)
    return run_research(q, protocol=protocol or default_protocol(q, parameters=FAST),
                        snapshot_root=tmp_path / "snap", ledger_path=ledger.path,
                        state_dir=tmp_path / f"state-{tag}",
                        output_dir=tmp_path / f"out-{tag}", **kw)


# ------------------------------------------------------------------ the question

def test_a_question_is_parsed_into_a_formula_and_disease():
    q = parse_question("Is Gegen Qinlian Decoction linked to MONDO_0005148 genes?")
    assert q.formula_id == GEGEN_QINLIAN.id and q.disease == "MONDO_0005148"
    assert parse_question("葛根黄芩黄连汤").formula_id == GEGEN_QINLIAN.id


@pytest.mark.parametrize("text", ["四君子汤作用于哪些通路？", "what does this herb do"])
def test_a_question_naming_no_known_formula_is_refused(text):
    with pytest.raises(QuestionRefused, match="no known formula"):
        parse_question(text)


def test_a_disease_that_disagrees_with_the_question_is_refused():
    with pytest.raises(QuestionRefused, match="disagrees"):
        parse_question("葛根芩连汤 and MONDO_0005148", disease="MONDO_0000001")


def test_a_protocol_must_name_an_activity_source_and_opentargets_for_a_disease():
    q = parse_question(QUESTION)
    with pytest.raises(ValueError, match="activity source"):
        Protocol(q, required_sources=("tcm_herbs", "reactome"))
    dq = parse_question(QUESTION + " MONDO_0005148")
    with pytest.raises(ValueError, match="opentargets"):
        Protocol(dq, required_sources=("tcm_herbs", "reactome", "npass"))
    assert "opentargets" in default_protocol(dq).required_sources


# ------------------------------------------------------------------ end to end

def test_a_surviving_hypothesis_is_released_with_verified_receipts(tmp_path):
    # Inactive measurements across the other proteins give the assayed background
    # something to test against, so the planted pathway survives the rebuttal.
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    run = _run(tmp_path, ledger)
    assert run.released, run.verdict.as_dict()
    assert run.hypotheses == ("reactome:R-HSA-A",)
    assert set(run.verdict.states.values()) == {True}
    claim = run.artifact.claims[0]
    assert claim.claim_kind == "mechanism_hypothesis"
    cited = [e for e in run.artifact.evidence if e.id in claim.supports]
    assert {e.design for e in cited} == {"in_vitro", "pathway_enrichment"}
    assert all(e.has_quote_receipt for e in cited)
    out = Path(run.output_dir)
    doc = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
    assert doc["audit_head"] == run.audit_head
    assert doc["validation"]["states"]["release_authorized"] is True


def test_every_stage_is_recorded_in_order_and_the_protocol_comes_first(tmp_path):
    from psh import PSHConfig, TrustedKernel
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    run = _run(tmp_path, ledger)
    kernel = TrustedKernel(PSHConfig(state_dir=Path(run.state_dir) / "psh").ensure_dirs())
    try:
        events = [r.event_type for r in kernel.events.records()
                  if r.event_type.startswith("research_")]
        assert kernel.events.verify()
    finally:
        kernel.close()
    assert events == ["research_protocol_registered", "research_sources_retrieved",
                      "research_analysis_completed", "research_rebuttal_completed",
                      "research_release_validated"]


def test_a_pathway_explained_by_which_proteins_were_assayed_is_refuted(tmp_path):
    """With no inactive measurements, every assayed protein is a hit, so the planted
    pathway is only enriched against the whole annotation. The rebuttal refutes it; the
    negative result is still released, with the reason as a limitation."""
    ledger = _world(tmp_path)
    run = _run(tmp_path, ledger)
    assert run.released and run.hypotheses == ()
    assert run.refuted[0]["object"] == "reactome:R-HSA-A"
    assert "assayed" in run.refuted[0]["reasons"][0]
    assert any(l.startswith("refuted at rebuttal") for l in run.artifact.limitations)
    assert any("leave_one_source_out" in l for l in run.artifact.limitations), \
        "a rebuttal that could not run is recorded, never counted as passed"


def test_a_claim_resting_on_one_source_is_refuted(tmp_path):
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    # A second activity source that reports a constituent but no activity at all.
    species = f"ncbitaxon:{next(iter(HERBS.values())).species[0].taxid}"
    raw = tmp_path / "snap" / "raw.txt"
    build_snapshot(key="cmaup", version="fx",
                   nodes=[{"id": species, "category": "organism", "name": "s", "source": "fx"},
                          {"id": "inchikey:AAAAAAAAAAAAAA-BBBBBBBBBB-N",
                           "category": "ingredient", "name": "c", "source": "fx"}],
                   edges=[_edge("c0", species, "contains", "inchikey:AAAAAAAAAAAAAA-BBBBBBBBBB-N",
                                "chemical_analysis", composition_level="C1")],
                   license="CC-BY-4.0", citation="fixture", raw_files={"raw.txt": raw},
                   parser="fixture", root=tmp_path / "snap", ledger=ledger)
    q = parse_question(QUESTION)
    protocol = default_protocol(q, activity=("npass", "cmaup"), parameters=FAST)
    run = _run(tmp_path, ledger, protocol)
    assert run.hypotheses == ()
    assert any("leaving out npass" in r for r in run.refuted[0]["reasons"])


# ------------------------------------------------------------------ repair and refusal

def test_a_missing_optional_source_is_dropped_as_a_recorded_deviation(tmp_path):
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    q = parse_question(QUESTION)
    protocol = default_protocol(q, optional=("string", "lotus"), parameters=FAST)
    run = _run(tmp_path, ledger, protocol)
    assert run.released
    assert run.deviations == ({"fallback": "drop_missing_optional_source", "source": "lotus",
                               "reason": "no snapshot recorded in the ledger"},)
    assert any(l.startswith("protocol deviation") for l in run.artifact.limitations)


def test_without_the_fallback_a_missing_optional_source_refuses(tmp_path):
    ledger = _world(tmp_path)
    q = parse_question(QUESTION)
    protocol = dc.replace(default_protocol(q, optional=("lotus",), parameters=FAST),
                          fallbacks=())
    with pytest.raises(ResearchRefused, match="lotus"):
        _run(tmp_path, ledger, protocol)


def test_a_missing_required_source_refuses(tmp_path):
    ledger = _world(tmp_path)
    q = parse_question(QUESTION)
    with pytest.raises(ResearchRefused, match="cmaup"):
        _run(tmp_path, ledger, default_protocol(q, activity=("npass", "cmaup"),
                                                parameters=FAST))


def test_a_snapshot_changed_after_it_was_recorded_is_never_worked_around(tmp_path):
    ledger = _world(tmp_path)
    _build(tmp_path / "snap", targets_of={0: PROTEINS[:2], 1: [], 2: [], 3: []})
    with pytest.raises(ResearchRefused, match="failed verification"):
        _run(tmp_path, ledger)


# ------------------------------------------------------------------ resume

def test_a_run_that_failed_in_rebuttal_resumes_without_re_analysing(tmp_path):
    from bioagent.analysis.network_pharmacology import run_network_pharmacology
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    calls = {"n": 0}

    def crashing(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:                       # the first rebuttal variant
            raise RuntimeError("worker lost")
        return run_network_pharmacology(*a, **k)

    with pytest.raises(RuntimeError, match="worker lost"):
        _run(tmp_path, ledger, analyse=crashing)

    def counting(*a, **k):
        calls["n"] += 1
        return run_network_pharmacology(*a, **k)

    calls["n"] = 0
    run = _run(tmp_path, ledger, analyse=counting)
    assert run.resumed_stages == ("protocol", "retrieve", "analyse")
    assert calls["n"] == len(run.rebuttal["tests"]), "only the rebuttals were re-run"
    assert run.released and run.hypotheses == ("reactome:R-HSA-A",)

    again = _run(tmp_path, ledger, analyse=counting)
    assert again.resumed_stages == ("protocol", "retrieve", "analyse", "rebut")
    assert again.artifact.claims == run.artifact.claims


def test_a_damaged_checkpoint_is_recomputed_not_trusted(tmp_path):
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    first = _run(tmp_path, ledger)
    ckpt = Path(first.state_dir) / "research" / first.run_id / "02-analyse.json"
    doc = json.loads(ckpt.read_text(encoding="utf-8"))
    doc["data"]["release"]["released"] = []
    ckpt.write_text(json.dumps(doc), encoding="utf-8")
    second = _run(tmp_path, ledger)
    assert "analyse" not in second.resumed_stages
    assert second.hypotheses == first.hypotheses


# ------------------------------------------------------------------ receipts

def test_receipts_are_checked_against_the_snapshots_not_the_run(tmp_path):
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    run = _run(tmp_path, ledger)
    snaps = [load_snapshot(tmp_path / "snap", key, version, ledger=ledger)
             for key, version in (("npass", "fx"), ("reactome", "current"),
                                  ("tcm_herbs", "gold"))]
    store = snapshot_content_store(snaps)
    store.put((Path(run.output_dir) / "enrichment.jsonl").read_text(encoding="utf-8"))
    assert validate_artifact(run.artifact, output_root=run.output_dir,
                             content_store=store).release_authorized
    edge_item = next(e for e in run.artifact.evidence if e.design == "in_vitro")
    forged = dc.replace(edge_item, quote=edge_item.quote.replace('"IC50"', '"Kd"'))
    tampered = dc.replace(run.artifact, evidence=tuple(
        forged if e.id == edge_item.id else e for e in run.artifact.evidence))
    verdict = validate_artifact(tampered, output_root=run.output_dir, content_store=store)
    assert not verdict.release_authorized and "ART115" in verdict.codes


def test_the_research_command_runs_the_loop(tmp_path, capsys):
    from bioagent.cli import main
    ledger = _world(tmp_path, tested_only=PROTEINS[8:40])
    code = main(["research", QUESTION, "--snapshots", str(tmp_path / "snap"),
                 "--ledger", str(ledger.path), "--state-dir", str(tmp_path / "st"),
                 "--out", str(tmp_path / "o"), "--background", "reactome",
                 "--permutations", "200"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0 and summary["released"] is True
    assert summary["hypotheses"] == ["reactome:R-HSA-A"]
    assert main(["research", "四君子汤", "--snapshots", "x", "--ledger", "y",
                 "--state-dir", "z", "--out", "w"]) == 2
