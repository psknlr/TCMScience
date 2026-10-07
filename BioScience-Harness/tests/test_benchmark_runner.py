"""The candidate benchmark: frozen cases, governed runs, independent scores, verification.

The candidate-benchmark workflow used to run no case at all (audit AUD-26). These
tests hold the parts that make a benchmark score mean something. The Season is frozen
by a manifest the cases must match. The candidate is bound by version and content
hash. Every case runs through the governed path, more than once. A separate scorer
reads only the bundle and the cases. A published score re-scores to itself, and an
edit anywhere in the chain is caught.
"""

from __future__ import annotations

import dataclasses as dc
import json
import shutil
from pathlib import Path

import pytest

from bioagent.benchmarks.independent import score_bundle, verify_scores, write_scores
from bioagent.benchmarks.runner import RunnerError, resolve_candidate, run_candidate
from bioagent.benchmarks.scorers import ScoringRefused
from bioagent.benchmarks.season import (SeasonError, freeze_season, load_cases, load_season,
                                        read_manifest)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "benchmarks" / "conformance"
MANIFEST = REPO / "benchmarks" / "registry" / "conformance-1.yaml"


@pytest.fixture(scope="module")
def season():
    manifest = read_manifest(MANIFEST)
    return manifest, load_season(manifest, CASES)


@pytest.fixture(scope="module")
def safety_bundle(season, tmp_path_factory):
    manifest, cases = season
    out = tmp_path_factory.mktemp("bench") / "safety"
    run_candidate(resolve_candidate("assess-tcm-safety@1.0.0"), manifest, cases, out)
    return out


# ------------------------------------------------------------------ the frozen Season

def test_the_committed_manifest_is_the_cases_it_froze(season):
    manifest, cases = season
    assert manifest.status == "conformance" and len(cases.cases) == manifest.counts["dev"]
    again = freeze_season(manifest.season, cases.cases, cut_at=manifest.cut_at,
                          tracks=manifest.tracks, status=manifest.status,
                          description=manifest.description)
    assert again.digest == manifest.digest


def test_a_case_edited_after_the_cut_is_refused(season, tmp_path):
    manifest, _ = season
    edited = tmp_path / "cases"
    shutil.copytree(CASES, edited)
    path = edited / "dev" / "conf-safety-03.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["gold"]["status"] = "no_record"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SeasonError, match="edited, added or removed"):
        load_season(manifest, edited)


def test_a_misplaced_or_duplicated_case_is_refused(tmp_path):
    (tmp_path / "hidden").mkdir()
    doc = json.loads((CASES / "dev" / "conf-entity-01.json").read_text(encoding="utf-8"))
    (tmp_path / "hidden" / "x.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SeasonError):
        load_cases(tmp_path)
    shutil.rmtree(tmp_path / "hidden")
    (tmp_path / "dev").mkdir()
    for name in ("a.json", "b.json"):
        (tmp_path / "dev" / name).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SeasonError, match="more than once"):
        load_cases(tmp_path)


def test_a_manifest_never_lists_held_out_cases(season):
    _, cases = season
    hidden = [dc.replace(c, visibility="hidden") for c in cases.cases]
    with pytest.raises(SeasonError, match="must not cover held-out"):
        freeze_season("s", hidden, cut_at="t", tracks={"TCM-Entity": "x", "TCM-Evidence": "x",
                                                       "TCM-NetPharm": "x", "TCM-Safety": "x"},
                      publish_case_ids=True)
    withheld = freeze_season("s", hidden, cut_at="t", tracks={
        "TCM-Entity": "x", "TCM-Evidence": "x", "TCM-NetPharm": "x", "TCM-Safety": "x"})
    assert withheld.cases == () and withheld.counts["hidden"] == len(hidden)


# ------------------------------------------------------------------ the candidate

def test_the_candidate_is_bound_by_version_and_hash():
    candidate = resolve_candidate("assess-tcm-safety@1.0.0")
    assert candidate.pinned_by and len(candidate.content_hash) == 64
    with pytest.raises(RunnerError, match="the tree holds"):
        resolve_candidate("assess-tcm-safety@9.9.9")
    with pytest.raises(RunnerError, match="must be"):
        resolve_candidate("assess-tcm-safety")


def test_a_bundle_records_every_case_run_twice(safety_bundle, season):
    manifest, cases = season
    bundle = json.loads((safety_bundle / "bundle.json").read_text(encoding="utf-8"))
    expected = sorted(c.id for c in cases.cases if c.track == "TCM-Safety")
    assert [c["case_id"] for c in bundle["cases"]] == expected
    assert all(len(c["runs"]) == 2 and not any(r["error"] for r in c["runs"])
               for c in bundle["cases"])
    assert bundle["season_manifest_digest"] == manifest.digest
    assert bundle["candidate"]["pinned"] is True


# ------------------------------------------------------------------ independent scoring

def test_the_score_is_independent_and_verifies(safety_bundle, season, tmp_path):
    manifest, cases = season
    document = score_bundle(safety_bundle, manifest, cases)
    row = document["row"]
    assert row["trusted"] and row["gates_failed"] == [] and row["gates_not_run"] == []
    assert row["dimensions"]["task_success"] == 1.0
    assert row["dimensions"]["reproducibility"] == 1.0
    path = write_scores(document, tmp_path / "scores.json")
    ok, detail = verify_scores(safety_bundle, path, manifest, cases)
    assert ok, detail


@pytest.mark.parametrize("target", ["output", "artifact", "bundle", "scores"])
def test_an_edit_anywhere_in_the_chain_is_caught(safety_bundle, season, tmp_path, target):
    manifest, cases = season
    copy = tmp_path / "bundle"
    shutil.copytree(safety_bundle, copy)
    scores = write_scores(score_bundle(copy, manifest, cases), tmp_path / "scores.json")
    rep = copy / "cases" / "conf-safety-01" / "rep1"
    if target == "output":
        out = rep / "outputs" / "safety.json"
        out.write_text(out.read_text(encoding="utf-8").replace("critical", "low"),
                       encoding="utf-8")
    elif target == "artifact":
        art = rep / "artifact.json"
        art.write_text(art.read_text(encoding="utf-8").replace("孕妇", "成人", 1),
                       encoding="utf-8")
    elif target == "bundle":
        doc = json.loads((copy / "bundle.json").read_text(encoding="utf-8"))
        doc["cases"][0]["runs"][0]["latency_s"] = 0.001
        (copy / "bundle.json").write_text(json.dumps(doc), encoding="utf-8")
    else:
        doc = json.loads(scores.read_text(encoding="utf-8"))
        doc["row"]["aggregate"] = 1.0
        scores.write_text(json.dumps(doc), encoding="utf-8")
    ok, detail = verify_scores(copy, scores, manifest, cases)
    assert not ok, detail


def test_a_candidate_that_misses_a_critical_record_fails_its_gate(season, tmp_path):
    """A safety skill that assesses the wrong substance misses the pregnancy
    contraindication and the 十八反 pair; GATE001 keeps it off the trusted board."""
    from bioagent.skills.p0 import assess_tcm_safety

    def careless(subject, *, co_administered=(), population="", run_id=""):
        return assess_tcm_safety("黄芪", population=population, run_id=run_id)

    careless.__module__, careless.__qualname__ = (assess_tcm_safety.__module__,
                                                  assess_tcm_safety.__qualname__)
    manifest, cases = season
    out = run_candidate(resolve_candidate("assess-tcm-safety@1.0.0"), manifest, cases,
                        tmp_path / "careless", callables={"assess-tcm-safety": careless})
    row = score_bundle(out, manifest, cases)["row"]
    assert "GATE001" in row["gates_failed"] and not row["trusted"]


def test_a_bundle_from_another_season_revision_is_refused(safety_bundle, season):
    manifest, cases = season
    other = dc.replace(manifest, description="another revision")
    with pytest.raises(ScoringRefused, match="another revision"):
        score_bundle(safety_bundle, other, cases)
