"""Regression tests for the external audit's counterexamples (F01–F09).

Each test is one probe from the audit's reproduction script, or the closest unit
form of it, turned from "was allowed" into "is refused". F01 (population scope)
and F05 (aggregation) have their own tests beside the code they exercise, in
``test_contracts.py`` and ``test_benchmarks.py``; F06's ungoverned-run refusal is in
``test_network_pharmacology.py`` and F09 (bridge defaults) in ``test_psh_bridge.py``.
F10 (a non-seed, end-to-end closed loop) is not a unit-testable defect and is not
covered here.
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bioagent.contracts import EvidenceItem, validate_artifact
from bioagent.contracts.artifact import ArtifactFile
from bioagent.contracts.candidate_claim import check_claim
from bioagent.contracts.claim_language import overreaching_language
from bioagent.contracts.receipts import ContentStore, default_store
from bioagent.skills.p0.entities import normalize_tcm_entities

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent
SKILLS_DIR = REPO / "skills" / "tcm"


@pytest.fixture
def base():
    return normalize_tcm_entities(["黄芪"])


# --------------------------------------------------------------------------
# F02 — a quote_verified flag is not a receipt
# --------------------------------------------------------------------------

def test_a_self_attested_quote_is_refused(base):
    invented = tuple(dc.replace(e, quote="Deliberately invented audit excerpt.",
                                quote_verified=True, content_hash="", quote_offset=-1)
                     for e in base.evidence)
    verdict = validate_artifact(dc.replace(base, evidence=invented))
    assert not verdict.publishable
    assert any("no receipt" in v.detail for v in verdict.violations)


def test_a_receipt_is_issued_only_by_locating_the_quote():
    item = EvidenceItem(id="e", design="classical_text", quote="主治")
    with pytest.raises(ValueError, match="verbatim"):
        item.located_in("完全不同的文本")
    located = item.located_in("黄芪，主治痈疽", store=ContentStore())
    assert located.has_quote_receipt and located.quote_offset == 3
    assert located.content_hash == hashlib.sha256("黄芪，主治痈疽".encode()).hexdigest()


def test_a_tampered_quote_fails_receipt_verification(base):
    store = ContentStore()
    evidence = tuple(e.located_in(e.quote, store=store) for e in base.evidence)
    clean = dc.replace(base, evidence=evidence)
    assert validate_artifact(clean, content_store=store).evidence_verified
    tampered = tuple(dc.replace(e, quote=e.quote[:-1] + "X") for e in evidence)
    verdict = validate_artifact(dc.replace(base, evidence=tampered), content_store=store)
    assert not verdict.publishable and "ART115" in verdict.codes


def test_without_content_a_receipt_is_present_but_unverified(base):
    verdict = validate_artifact(base, content_store=ContentStore())
    assert verdict.publishable
    assert not verdict.evidence_verified and not verdict.release_authorized


# --------------------------------------------------------------------------
# F02 — the wording must not outrun the claim kind
# --------------------------------------------------------------------------

def test_efficacy_language_under_an_attribution_kind_is_refused(base):
    inconsistent = dc.replace(base.claims[0],
                              text="此药已被证明对所有癌症患者有效，应替代标准治疗。")
    verdict = check_claim(inconsistent, base.evidence_index)
    assert not verdict.allowed and "CLM011" in verdict.codes
    families = {f.family for f in overreaching_language(inconsistent.text, "attribution")}
    assert families == {"efficacy", "normative", "certainty", "universal"}
    assert not validate_artifact(dc.replace(base, claims=(inconsistent,))).publishable


@pytest.mark.parametrize("text,kind", [
    ("There is no evidence that it is effective for fever", "association"),
    ("尚无证据表明其有效", "traditional_use"),
    ("It reduced fever duration and is effective in adults with malaria", "efficacy"),
    ("Compound A may act on protein B; this is a hypothesis", "mechanism_hypothesis"),
    ("《本草纲目》记载黄芪补气", "attribution"),
])
def test_calibrated_wording_is_not_flagged(text, kind):
    assert overreaching_language(text, kind) == ()


@pytest.mark.parametrize("text,kind,family", [
    ("Berberine has been proven to lower glucose", "mechanism_hypothesis", "certainty"),
    ("Patients should take this formula daily", "efficacy", "normative"),
    ("It cures influenza", "traditional_use", "efficacy"),
    ("Effective for all patients with diabetes", "efficacy", "universal"),
])
def test_overreaching_wording_is_flagged(text, kind, family):
    assert family in {f.family for f in overreaching_language(text, kind)}


# --------------------------------------------------------------------------
# F03 — publishable is not verified, attested or released
# --------------------------------------------------------------------------

def test_unlinked_cited_evidence_is_an_error_not_a_warning(base):
    unlinked = tuple(dc.replace(e, source_card_id="") for e in base.evidence)
    verdict = validate_artifact(dc.replace(base, evidence=unlinked))
    assert not verdict.publishable and "ART111" in verdict.codes


def test_a_declared_output_that_does_not_exist_is_refused(base, tmp_path):
    missing = tmp_path / "never-created.tsv"
    verdict = validate_artifact(dc.replace(base, outputs=(ArtifactFile(str(missing), "0" * 64),),
                                           audit_head="", policy_id=""))
    assert not verdict.publishable and "ART113" in verdict.codes
    assert not verdict.execution_attested and not verdict.release_authorized


def test_an_output_whose_bytes_differ_from_its_hash_is_refused(base, tmp_path):
    (tmp_path / "entities.json").write_text("{}", encoding="utf-8")
    verdict = validate_artifact(base, output_root=tmp_path)
    assert not verdict.publishable and "ART114" in verdict.codes


def test_an_unattested_artifact_is_never_release_authorized(base):
    verdict = validate_artifact(base, content_store=default_store)
    assert verdict.publishable and verdict.evidence_verified and verdict.outputs_verified
    assert not verdict.execution_attested and not verdict.release_authorized
    assert verdict.as_dict()["states"]["release_authorized"] is False


def test_a_governed_run_is_verified_attested_and_released(tmp_path):
    from bioagent.governed import run_governed
    run = run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=SKILLS_DIR,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out")
    assert run.released, run.verdict.as_dict()
    assert run.artifact.audit_head == run.audit_head and run.artifact.policy_id
    assert (tmp_path / "out" / "entities.json").is_file()
    # The published document re-verifies from disk alone, without the in-memory store.
    again = validate_artifact(run.artifact, output_root=tmp_path / "out")
    assert again.outputs_verified


# --------------------------------------------------------------------------
# F04 — a path is directed, and the statement is bounded by its edges
# --------------------------------------------------------------------------

def _snapshot(*edges):
    from bioagent.sources.snapshot import Snapshot
    return Snapshot("audit-snapshot", "audit", "1", Path("."), {}, (), tuple(edges))


def _edge(**kw):
    base = {"subject": "compound:A", "predicate": "targets", "object": "protein:B",
            "study_design": "in_vitro", "source_record_id": "r1",
            "primary_knowledge_source": "audit", "knowledge_level": "knowledge_assertion",
            "agent_type": "manual_agent", "license": "CC0-1.0"}
    base.update(kw)
    return base


def test_the_audit_path_semantics_counterexamples_are_refused():
    from bioagent.sources.release import CandidateClaim, check_release
    support = (("audit-snapshot", "r1"),)
    verdict = check_release([
        CandidateClaim("mechanism", "compound:A", "protein:B", support,
                       "Compound A activates protein B."),
        CandidateClaim("mechanism", "compound:A", "protein:B", support,
                       "Compound A inhibits protein B."),
        CandidateClaim("mechanism", "protein:B", "compound:A", support,
                       "Protein B acts on compound A."),
    ], [_snapshot(_edge())])
    assert verdict.released == []
    reasons = [r for _, r in verdict.refused]
    assert "direction of effect" in reasons[0] and "direction of effect" in reasons[1]
    assert "direction they were recorded" in reasons[2]


def test_a_statement_within_its_edges_is_released():
    from bioagent.sources.release import CandidateClaim, check_release
    support = (("audit-snapshot", "r1"),)
    verdict = check_release([
        CandidateClaim("mechanism", "compound:A", "protein:B", support,
                       "Compound A acts on protein B in vitro."),
    ], [_snapshot(_edge())])
    assert len(verdict.released) == 1
    directed = check_release([
        CandidateClaim("mechanism", "compound:A", "protein:B", support,
                       "Compound A inhibits protein B."),
    ], [_snapshot(_edge(direction="inhibition"))])
    assert len(directed.released) == 1


def test_an_inactive_measurement_supports_no_effect():
    from bioagent.sources.release import CandidateClaim, check_release
    verdict = check_release([
        CandidateClaim("mechanism_hypothesis", "compound:A", "protein:B",
                       (("audit-snapshot", "r1"),), "Compound A may act on protein B."),
    ], [_snapshot(_edge(predicate="tested_against"))])
    assert verdict.released == [] and "tested_against" in verdict.refused[0][1]


# --------------------------------------------------------------------------
# F06 / F07 — the CLI honours --dir and types its arguments
# --------------------------------------------------------------------------

def _cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO / 'src'}{os.pathsep}{ROOT / 'PSH-Harness' / 'src'}"
    return subprocess.run([sys.executable, "-m", "bioagent.cli", "skill", *args],
                          cwd=str(REPO), capture_output=True, text=True, env=env, timeout=120)


def test_a_missing_skill_directory_is_refused(tmp_path):
    proc = _cli("normalize-tcm-entities", "--dir", str(tmp_path / "nonexistent-skills"),
                "--arg", "names=黄芪", "--json")
    assert proc.returncode == 2 and "does not exist" in proc.stderr
    assert proc.stdout == ""


def test_a_single_chinese_name_is_one_query_not_its_characters():
    import json
    proc = _cli("normalize-tcm-entities", "--dir", str(SKILLS_DIR),
                "--arg", "names=黄芪", "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["question"] == "resolve 1 TCM name(s) to corpus entities"
    assert doc["claims"][0]["subject"] == "黄芪"
    assert doc["validation"]["states"]["release_authorized"] is True


def test_arguments_are_coerced_from_the_signature():
    from bioagent.governed import GovernedRunRefused, coerce_arguments
    from bioagent.skills.p0 import assess_tcm_safety, normalize_tcm_entities, retrieve_tcm_evidence
    assert coerce_arguments(normalize_tcm_entities, {"names": "黄芪"}) == {"names": ["黄芪"]}
    assert coerce_arguments(normalize_tcm_entities, {"names": "黄芪、当归，甘草"}) == {
        "names": ["黄芪", "当归", "甘草"]}
    assert coerce_arguments(assess_tcm_safety, {"subject": "附子", "co_administered": "半夏"}) == {
        "subject": "附子", "co_administered": ["半夏"]}
    assert coerce_arguments(retrieve_tcm_evidence, {"subject": "黄芪", "max_results": "5"}) == {
        "subject": "黄芪", "max_results": 5}
    with pytest.raises(GovernedRunRefused, match="unknown argument"):
        coerce_arguments(normalize_tcm_entities, {"nmes": "黄芪"})
    with pytest.raises(GovernedRunRefused, match="expects int"):
        coerce_arguments(retrieve_tcm_evidence, {"subject": "x", "max_results": "many"})


def test_a_manifest_that_names_another_callable_is_refused(tmp_path):
    from bioagent.governed import GovernedRunRefused, run_governed
    from bioagent.skills.p0 import retrieve_tcm_evidence
    with pytest.raises(GovernedRunRefused, match="entrypoint"):
        run_governed("normalize-tcm-entities", {"subject": "黄芪"}, skill_dir=SKILLS_DIR,
                     state_dir=tmp_path / "psh",
                     callables={"normalize-tcm-entities": retrieve_tcm_evidence})


def test_a_skill_that_drifted_from_its_pin_is_refused(tmp_path):
    from bioagent.governed import GovernedRunRefused, run_governed
    lock = tmp_path / "skills.lock.yaml"
    text = (REPO / "registry" / "skills.lock.yaml").read_text(encoding="utf-8")
    import re
    lock.write_text(re.sub(r"(content_hash: )[0-9a-f]{64}", r"\g<1>" + "0" * 64, text),
                    encoding="utf-8")
    with pytest.raises(GovernedRunRefused, match="not the code that was reviewed"):
        run_governed("normalize-tcm-entities", {"names": ["黄芪"]}, skill_dir=SKILLS_DIR,
                     state_dir=tmp_path / "psh", lockfile=lock)


# --------------------------------------------------------------------------
# F08 — the skill hash covers what the entrypoint imports
# --------------------------------------------------------------------------

def test_a_change_to_an_imported_helper_moves_the_skill_hash(tmp_path, monkeypatch):
    from bioagent.skills.loader import skill_content_hash, skill_dependency_closure
    pkg = tmp_path / "audit_dependency_probe"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "entry.py").write_text("from .dep import VALUE\ndef run():\n    return VALUE\n")
    dep = pkg / "dep.py"
    dep.write_text("VALUE = 1\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text("id: audit\nversion: 1.0.0\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    entry = "audit_dependency_probe.entry:run"
    assert dep.resolve() in {p.resolve() for p in skill_dependency_closure(entry)}
    before = skill_content_hash(skill, entrypoint=entry)
    dep.write_text("VALUE = 999\n")
    assert skill_content_hash(skill, entrypoint=entry) != before


def test_the_p0_closure_includes_the_contract_layer():
    from bioagent.skills.loader import skill_dependency_closure
    names = {p.as_posix().split("src/")[-1]
             for p in skill_dependency_closure("bioagent.skills.p0.entities:normalize_tcm_entities")}
    assert {"bioagent/skills/p0/common.py", "bioagent/contracts/candidate_claim.py",
            "bioagent/tcm/knowledge.py"} <= names
