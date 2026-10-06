"""Evaluation, promotion and distribution, after the October 2026 audit (AUD-18..21, 25).

AUD-18  ``score_run`` took the gates the scores reported about themselves: a missed
        critical case failed GATE001 when the gate was computed and was trusted at 1.0
        when the run was scored;
AUD-19  a case scored 99 times counted 99 times, so repeating the easy case raised
        task_success from 0.50 to 0.99;
AUD-21  a candidate scoring NaN was promoted over an incumbent at 0.8, because NaN
        compares False with every threshold;
AUD-20  an approval of 1.0.0 (MIT, hash A) promoted 999.0.0 (no licence, hash B);
AUD-25  the wheel carried no skill manifest and no lockfile, so an installed package
        could not run one governed skill.

AUD-22..24 (tool-call permissions and declassification) are PSH's and are tested there.
Each test starts from the audit's own example.
"""

from __future__ import annotations

import dataclasses as dc
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from bioagent import config
from bioagent.benchmarks import (BenchmarkCase, CaseError, CaseScore, RunRecord,
                                 ScoreComponents, gate_failures, score_run)
from bioagent.benchmarks.scorers import ScoringRefused
from bioagent.evolution import EvolutionAgent
from bioagent.evolution.pipeline import BenchmarkResult, EvolutionPipeline
from bioagent.runtime.hmr import HotReloader
from bioagent.runtime.registry import ComponentRegistry
from bioagent.skills.loader import load_skills, skill_content_hash
from bioagent.skills.models import SkillRuntime, SkillSpec
from bioagent.updates import (PromotionDecision, PromotionRefused, Registry, RegistryError,
                              load_lockfile, score_candidate, to_candidate, version_from_spec)
from bioagent.updates.registry import REVIEWED_FIELDS, review_digest
from test_v2_harness import mk

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "skills" / "tcm"
LOCKFILE = REPO / "registry" / "skills.lock.yaml"


# ============================================== AUD-18: the gates are computed, not taken

def components(**kw) -> ScoreComponents:
    base = dict(task_success=1.0, evidence_grounding=1.0, provenance_completeness=1.0,
                reproducibility=1.0, safety_abstention=1.0, claim_calibration=1.0,
                latency=0.0, cost=0.0)
    base.update(kw)
    return ScoreComponents(**base)


VERSIONS = {"runtime": "psh-0.6.0", "skill": "s@1.0.0", "source": "s1",
            "benchmark": "season-1"}


def record(scores, **kw) -> RunRecord:
    base = dict(run_id="r", system="sys", submission_type="skill", season="season-1",
                composite_version=VERSIONS, trace_digest="sha256:trace",
                artifact_digest="sha256:artifact", scores=tuple(scores))
    base.update(kw)
    return RunRecord(**base)


SAFETY = BenchmarkCase(id="c1", track="TCM-Safety", visibility="dev", question="q", gold={})
MISSED = CaseScore(case_id="c1", track="TCM-Safety",
                   components=components(notes={"severity": "critical",
                                                "surfaced": "false"}))


def test_a_missed_critical_case_fails_its_gate_when_the_run_is_scored():
    """The audit's case: gate_failures said GATE001, score_run said trusted at 1.0."""
    assert gate_failures(scores=[MISSED]) == ("GATE001",)
    row = score_run(record([MISSED]), [SAFETY], claims=(), artifact_reruns=True)
    assert "GATE001" in row["gates_failed"] and not row["trusted"]
    assert row["board"] == "experimental"


def test_a_gate_whose_evidence_is_missing_is_not_passed():
    surfaced = dc.replace(MISSED, components=components(
        notes={"severity": "critical", "surfaced": "true"}))
    unchecked = score_run(record([surfaced]), [SAFETY])
    assert unchecked["gates_failed"] == ["GATES_NOT_RUN"] and not unchecked["trusted"]
    assert unchecked["gates_not_run"] == ["GATE002", "GATE003", "GATE004"]
    checked = score_run(record([surfaced]), [SAFETY], claims=(), artifact_reruns=True)
    assert checked["gates_failed"] == [] and checked["trusted"]
    rerun_failed = score_run(record([surfaced]), [SAFETY], claims=(), artifact_reruns=False)
    assert "GATE003" in rerun_failed["gates_failed"]


@pytest.mark.parametrize("change", [
    {"composite_version": {}},
    {"composite_version": {**VERSIONS, "source": ""}},
    {"trace_digest": ""},
    {"artifact_digest": ""},
])
def test_a_run_that_does_not_say_what_it_was_is_not_trusted(change):
    """The audit's case also had empty versions, trace digest and artifact digest."""
    surfaced = dc.replace(MISSED, components=components(
        notes={"severity": "critical", "surfaced": "true"}))
    row = score_run(record([surfaced], **change), [SAFETY], claims=(), artifact_reruns=True)
    assert "UNTRACEABLE" in row["gates_failed"] and not row["trusted"]


# ================================================ AUD-19: the case set is the Season's

EASY = BenchmarkCase(id="easy", track="TCM-Entity", visibility="dev", question="q", gold={})
HARD = BenchmarkCase(id="hard", track="TCM-Entity", visibility="dev", question="q", gold={})
WIN = CaseScore(case_id="easy", track="TCM-Entity", components=components())
LOSE = CaseScore(case_id="hard", track="TCM-Entity", components=components(task_success=0.0))


def test_a_case_scored_twice_is_refused():
    """The audit's case: once each, task_success 0.50; the easy case 99 times, 0.99."""
    once = score_run(record([WIN, LOSE]), [EASY, HARD], claims=(), artifact_reruns=True)
    assert once["dimensions"]["task_success"] == pytest.approx(0.5)
    with pytest.raises(ScoringRefused, match="more than once"):
        score_run(record((WIN,) * 99 + (LOSE,)), [EASY, HARD], claims=(),
                  artifact_reruns=True)


@pytest.mark.parametrize("scores, cases, problem", [
    ((WIN, LOSE, dc.replace(WIN, case_id="elsewhere")), (EASY, HARD), "not in this Season"),
    ((WIN, dc.replace(LOSE, track="TCM-Safety")), (EASY, HARD), "another track"),
    ((WIN, LOSE), (EASY, HARD, EASY), "more than once"),
])
def test_a_case_set_that_is_not_the_seasons_is_refused(scores, cases, problem):
    with pytest.raises(ScoringRefused, match=problem):
        score_run(record(scores), list(cases), claims=(), artifact_reruns=True)


def test_a_skipped_case_still_keeps_the_run_off_the_trusted_board():
    row = score_run(record([WIN]), [EASY, HARD], claims=(), artifact_reruns=True)
    assert row["unscored_cases"] == ["hard"] and not row["trusted"]


# ======================================== AUD-21: a score must be a number before compared

def _pipeline(candidate_score, *, incumbent_score=0.8, n_cases=1):
    registry = ComponentRegistry([mk(version="1.0.0")])
    scores = {"1.0.0": incumbent_score, "1.1.0": candidate_score}
    pipeline = EvolutionPipeline(
        registry, HotReloader(registry, smoke_runner=lambda m: (True, "ok")),
        benchmark_runner=lambda m, b: BenchmarkResult(b, scores[m.version], n_cases),
        min_improvement=0.1)
    proposal = EvolutionAgent(registry).propose(
        "t.tool.a", rationale="audit", changes={
            "version": "1.1.0",
            "validation": {"smoke_test": "s", "benchmarks": ["b1"], "last_validated": ""}})
    return registry, pipeline.submit(proposal)


def test_a_nan_score_is_not_promoted():
    """The audit's case: incumbent 0.8, candidate NaN, required gain 0.1 — PROMOTED."""
    registry, out = _pipeline(float("nan"))
    assert out.state.value == "QUARANTINED"
    assert "not finite" in out.stage_log[-1]["detail"]
    assert registry.get("t.tool.a").version == "1.0.0"


@pytest.mark.parametrize("candidate, incumbent, n_cases, problem", [
    (float("inf"), 0.8, 1, "not finite"),
    (1.5, 0.8, 1, "outside [0, 1]"),
    (-0.1, 0.8, 1, "outside [0, 1]"),
    (0.95, float("nan"), 1, "incumbent"),
    (0.95, 0.8, 0, "at least one"),
])
def test_an_invalid_benchmark_result_quarantines_the_candidate(candidate, incumbent,
                                                               n_cases, problem):
    registry, out = _pipeline(candidate, incumbent_score=incumbent, n_cases=n_cases)
    assert out.state.value == "QUARANTINED" and problem in out.stage_log[-1]["detail"]
    assert registry.get("t.tool.a").version == "1.0.0"


def test_a_real_improvement_is_still_promoted():
    registry, out = _pipeline(0.95)
    assert out.state.value == "PROMOTED" and registry.get("t.tool.a").version == "1.1.0"


@pytest.mark.parametrize("value, problem", [
    (float("inf"), "not finite"), (True, "must be a number"), ("0.5", "must be a number"),
    (float("nan"), "NaN"),
])
def test_a_case_score_must_be_a_finite_number(value, problem):
    with pytest.raises(CaseError, match=problem):
        components(task_success=value)


# ============================================= AUD-20: an approval binds to its candidate

def spec(**kw) -> SkillSpec:
    base = dict(id="candidate", name="Candidate", version="1.0.0", license_spdx="MIT",
                integration_mode="native", content_hash="a" * 64,
                runtime=SkillRuntime(backend="python", entrypoint="impl:run"),
                outputs={"type": "object"})
    base.update(kw)
    return SkillSpec(**base)


def candidate(**kw):
    return to_candidate(spec(**kw), score_candidate(spec(**kw)))


def approve(reviewed, **kw) -> PromotionDecision:
    base = dict(skill_id=reviewed.spec.id, version=reviewed.spec.version, decision="approve",
                decided_by="reviewer@example.org", decided_at="2026-10-01T00:00:00Z",
                reason="reviewed", candidate_digest=reviewed.digest)
    base.update(kw)
    return PromotionDecision(**base)


def test_an_approval_does_not_promote_another_object():
    """The audit's case: candidate 1.0.0, MIT, hash A, approved; 999.0.0, no licence,
    hash B, promoted with the reviewer's name on it."""
    registry, reviewed = Registry(), candidate()
    registry.add_candidate(reviewed)
    swapped = dc.replace(reviewed.version, version="999.0.0", license_spdx="",
                         content_hash="b" * 64)
    with pytest.raises(PromotionRefused, match="not the candidate that was reviewed"):
        registry.promote(reviewed, approve(reviewed), decided_version=swapped)
    assert registry.stable() == ()


@pytest.mark.parametrize("field, value", [
    ("content_hash", "b" * 64), ("license_spdx", ""), ("source_commit", "f" * 40),
    ("allowed_hosts", ("evil.example.org",)), ("subprocess_permissions", True),
    ("filesystem_permissions", ("/",)),
])
def test_every_reviewed_field_is_bound(field, value):
    registry, reviewed = Registry(), candidate()
    changed = dc.replace(reviewed.version, **{field: value})
    with pytest.raises(PromotionRefused, match=field):
        registry.promote(reviewed, approve(reviewed), decided_version=changed)
    assert field in REVIEWED_FIELDS


def test_an_approval_names_the_digest_of_what_was_reviewed():
    reviewed, other = candidate(), candidate(content_hash="b" * 64)
    with pytest.raises(RegistryError, match="candidate_digest"):
        approve(reviewed, candidate_digest="")
    with pytest.raises(PromotionRefused, match="candidate digest"):
        Registry().promote(other, approve(reviewed, version=other.spec.version),
                           decided_version=other.version)
    with pytest.raises(PromotionRefused, match="decision is about"):
        Registry().promote(reviewed, approve(reviewed, version="999.0.0"),
                           decided_version=reviewed.version)


def test_the_reviewed_candidate_is_promoted_as_reviewed():
    registry, reviewed = Registry(), candidate()
    entry = registry.promote(reviewed, approve(reviewed), decided_version=reviewed.version)
    assert review_digest(entry.version) == reviewed.digest
    assert entry.version.approved_by == "reviewer@example.org"


def test_a_rollback_reinstates_the_version_as_it_was_promoted():
    """A rollback renamed the current entry: 1.1.0's code under 1.0.0's version."""
    registry, first = Registry(), candidate()
    registry.promote(first, approve(first), decided_version=first.version)
    second = candidate(version="1.1.0", content_hash="c" * 64, license_spdx="Apache-2.0")
    registry.promote(second, approve(second), decided_version=second.version)
    back = registry.rollback("candidate", "1.0.0", approve(first, reason="regression"))
    assert (back.version.version, back.version.content_hash, back.version.license_spdx) \
        == ("1.0.0", "a" * 64, "MIT")
    assert back.version.rollback_version == "1.1.0"
    with pytest.raises(PromotionRefused, match="never promoted"):
        registry.rollback("candidate", "0.9.0", approve(first, version="0.9.0"))
    with pytest.raises(PromotionRefused, match="does not approve"):
        registry.rollback("candidate", "1.1.0", approve(first, version="1.1.0"))


# ============================================ AUD-25: an installed package runs a skill

def test_a_skill_hashes_the_same_wherever_its_directory_sits(tmp_path):
    """Files were hashed in absolute-path order, so the same skill hashed differently
    once its directory sorted after the package (as it does inside an installed one)."""
    pins = {v.skill_id: v.content_hash for v in load_lockfile(LOCKFILE.read_text("utf-8"))}
    loaded, _ = load_skills(SKILLS)
    for skill in loaded:
        moved = tmp_path / "zz" / skill.spec.id
        shutil.copytree(skill.directory, moved)
        entry = skill.spec.runtime.entrypoint
        assert skill_content_hash(moved, entrypoint=entry) == pins[skill.spec.id]


def test_shipped_resources_resolve_to_the_tree_here_and_to_the_package_when_installed(
        tmp_path, monkeypatch):
    assert config.SOURCE_TREE
    assert config.skills_dir() == config.REPO_ROOT / "skills"
    assert config.registry_dir() == config.REPO_ROOT / "registry"
    bundled = tmp_path / "_bundled"
    (bundled / "skills").mkdir(parents=True)
    (bundled / "registry").mkdir()
    monkeypatch.setattr(config, "SOURCE_TREE", False)
    monkeypatch.setattr(config, "BUNDLED_ROOT", bundled)
    monkeypatch.delenv(config.ENV_SKILLS, raising=False)
    assert config.skills_dir() == bundled / "skills"
    assert config.registry_dir() == bundled / "registry"
    assert config.skills_dir("elsewhere") == Path("elsewhere")


def test_the_skill_command_needs_no_particular_working_directory(tmp_path, monkeypatch,
                                                                 capsys):
    """``--dir`` defaulted to the relative ``skills/tcm``."""
    from bioagent.cli import main
    monkeypatch.chdir(tmp_path)
    code = main(["skill", "normalize-tcm-entities", "--arg", "names=黄芪", "--json",
                 "--state-dir", str(tmp_path / "psh"), "--out-dir", str(tmp_path / "out")])
    document = json.loads(capsys.readouterr().out)
    assert code == 0 and document["validation"]["states"]["release_authorized"]
    assert Path(document["governed"]["lockfile"]) == LOCKFILE


def test_the_release_check_wants_the_skills_and_their_lockfile_in_the_wheel(tmp_path):
    sys.path.insert(0, str(REPO / "scripts"))
    from make_release import required_in_wheel, verify_artifact
    wheel = tmp_path / "bioagent-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:      # what the wheel held before the fix
        zf.writestr("bioagent/data/unified_capability_catalogue.csv", "name\n")
    problems = verify_artifact(wheel)
    for name in ("registry/skills.lock.yaml", "registry/materia_taxa.json",
                 "skills/tcm/normalize-tcm-entities/skill.yaml"):
        assert f"missing runtime data: bioagent/_bundled/{name}" in problems
    assert len(required_in_wheel()) == 3 + len(list((REPO / "skills").rglob("skill.yaml")))
