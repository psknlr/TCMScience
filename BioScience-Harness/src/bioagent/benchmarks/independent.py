"""Score a runner bundle independently, and verify a published score.

The scorer reads a bundle (:mod:`benchmarks.runner`) and the Season's cases and
nothing else: it does not import the candidate, does not re-run it, and trusts no
number the runner wrote. It refuses a bundle whose files do not hash to what the bundle
records, or whose artifacts do not hash to their own digests. It refuses a bundle run
against another Season, another manifest or other scoring rules. It then measures every
case (:mod:`benchmarks.metrics`) and scores the run (:func:`scorers.score_run`) with
the run-level gates evaluated.

:func:`verify_scores` re-scores a bundle and compares the result with a published
score. The score is a pure function of the bundle and the cases, so anyone holding
both gets the same document, digest for digest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..contracts.artifact import ResearchArtifact
from ..contracts.source_card import canonical_hash
from . import metrics, models, scorers, season as season_module
from .metrics import CaseRun, case_score, gate_claims
from .models import BenchmarkSeason, RunRecord
from .runner import BUNDLE_FILE, code_digest, sha256_file
from .scorers import ScoringRefused, score_run
from .season import SeasonManifest

__all__ = ["score_bundle", "verify_scores", "write_scores"]


def _load_bundle(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / BUNDLE_FILE
    if not path.is_file():
        raise ScoringRefused(f"no {BUNDLE_FILE} in {bundle_dir}")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    recorded = bundle.pop("bundle_digest", "")
    if canonical_hash(bundle) != recorded:
        raise ScoringRefused("bundle.json does not hash to the digest it records: it was "
                             "edited after the run")
    bundle["bundle_digest"] = recorded
    return bundle


def _case_run(bundle_dir: Path, record: Mapping[str, Any]) -> CaseRun:
    artifacts, payloads, states, latency, errors = [], [], [], [], []
    for run in record["runs"]:
        latency.append(float(run["latency_s"]))
        if run.get("error"):
            errors.append(run["error"])
            continue
        art_path = bundle_dir / run["artifact"]
        if sha256_file(art_path) != run["artifact_sha256"]:
            raise ScoringRefused(f"{run['artifact']} was changed after the run")
        verdict_path = bundle_dir / run["verdict"]
        if sha256_file(verdict_path) != run["verdict_sha256"]:
            raise ScoringRefused(f"{run['verdict']} was changed after the run")
        artifact = ResearchArtifact.from_dict(json.loads(art_path.read_text("utf-8")))
        if artifact.digest != run["artifact_digest"]:
            raise ScoringRefused(f"{run['artifact']} does not hash to the artifact digest "
                                 "the run recorded")
        payload = {}
        out_dir = art_path.parent / "outputs"
        for name, digest in run["outputs"].items():
            path = out_dir / name
            if sha256_file(path) != digest:
                raise ScoringRefused(f"output {name} of case {record['case_id']} was "
                                     "changed after the run")
            if name.endswith(".json"):
                payload[name] = json.loads(path.read_text(encoding="utf-8"))
        artifacts.append(artifact)
        payloads.append(payload)
        states.append(json.loads(verdict_path.read_text("utf-8")).get("states", {}))
    return CaseRun(case_id=record["case_id"], track=record["track"],
                   artifacts=tuple(artifacts), payloads=tuple(payloads),
                   states=tuple(states), latency_s=tuple(latency),
                   error="; ".join(errors))


def score_bundle(bundle_dir: str | Path, manifest: SeasonManifest,
                 season: BenchmarkSeason) -> dict[str, Any]:
    """The score document of a bundle. Refused if anything does not check out."""
    bundle_dir = Path(bundle_dir)
    bundle = _load_bundle(bundle_dir)
    if bundle["season"] != manifest.season or season.season != manifest.season:
        raise ScoringRefused(f"bundle of season {bundle['season']!r} scored against "
                             f"{manifest.season!r}")
    if bundle["season_manifest_digest"] != manifest.digest:
        raise ScoringRefused("the bundle was run against another revision of the season "
                             "manifest")
    if bundle["scoring_rules"] != manifest.scoring_rules:
        raise ScoringRefused("the bundle was run under other scoring rules")
    by_id = {c.id: c for c in season.cases}
    runs, case_scores, per_case = [], [], []
    for record in bundle["cases"]:
        case = by_id.get(record["case_id"])
        if case is None:
            raise ScoringRefused(f"case {record['case_id']!r} is not in the season")
        if case.digest != record["case_digest"]:
            raise ScoringRefused(f"case {case.id!r} differs from the one the run used")
        run = _case_run(bundle_dir, record)
        runs.append(run)
        scored = case_score(run, case.gold)
        case_scores.append(scored)
        label = case.id if manifest.cases else f"case-{len(per_case) + 1:03d}"
        per_case.append({"case": label, "track": case.track,
                         "components": scored.components.as_dict(),
                         "error": scored.error})
    track_cases = [c for c in season.cases if c.track in bundle["tracks"]]
    first = next((r.artifacts[0] for r in runs if r.artifacts), None)
    versions = dict(first.composite_version) if first is not None else {}
    candidate = bundle["candidate"]
    record = RunRecord(
        run_id=f"bench-{bundle['bundle_digest'][:12]}", system=candidate["composite_id"],
        submission_type="skill", season=manifest.season,
        composite_version={
            "runtime": versions.get("runtime", ""),
            "skill": f"{candidate['composite_id']}#{candidate['content_hash'][:12]}",
            "source": versions.get("source", ""),
            "benchmark": f"{manifest.season}#{manifest.digest[:12]}"},
        scores=tuple(case_scores), trace_digest=bundle["bundle_digest"],
        artifact_digest=canonical_hash([a.digest for r in runs for a in r.artifacts]))
    reproducible = bool(runs) and all(
        not r.error and r.artifacts and s.components.reproducibility == 1.0
        for r, s in zip(runs, case_scores))
    row = score_run(record, track_cases, claims=gate_claims(runs),
                    artifact_reruns=reproducible)
    document = {
        "schema": 1, "season": manifest.season, "season_status": manifest.status,
        "season_manifest_digest": manifest.digest, "scoring_rules": manifest.scoring_rules,
        "bundle_digest": bundle["bundle_digest"], "candidate": candidate,
        "scorer_code_digest": code_digest(metrics, scorers, models, season_module),
        "row": row, "per_case": per_case,
        "note": ("a conformance season: these numbers show the runner and the scorers "
                 "work end to end on public cases; they are not a benchmark result"
                 if manifest.status == "conformance" else
                 "scored against a frozen, withheld Season"),
    }
    if not candidate.get("pinned"):
        document["unpinned"] = ("the candidate is not pinned by a lockfile: every run was "
                                "an unpinned development run, recorded but never released")
    document["scores_digest"] = canonical_hash(document)
    return document


def write_scores(document: Mapping[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True,
                               default=str), encoding="utf-8")
    return path


def verify_scores(bundle_dir: str | Path, scores_path: str | Path,
                  manifest: SeasonManifest, season: BenchmarkSeason) -> tuple[bool, str]:
    """Re-score the bundle and compare with the published document."""
    published = json.loads(Path(scores_path).read_text(encoding="utf-8"))
    claimed = published.pop("scores_digest", "")
    if canonical_hash(published) != claimed:
        return False, "the score document does not hash to the digest it records"
    try:
        fresh = score_bundle(bundle_dir, manifest, season)
    except ScoringRefused as exc:
        return False, f"the bundle does not score: {exc}"
    fresh_round = json.loads(json.dumps(fresh, default=str))
    if fresh_round["scores_digest"] != claimed:
        differing = sorted(k for k in fresh_round if fresh_round.get(k) != {
            **published, "scores_digest": claimed}.get(k))
        return False, f"re-scoring gives a different document (differs in {differing})"
    return True, f"re-scored to the same document ({claimed[:16]})"
