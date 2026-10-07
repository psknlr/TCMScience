#!/usr/bin/env python3
"""Benchmark a candidate skill against a frozen Season: freeze, run, score, verify.

    # cut a Season from a case directory (once; the manifest is then committed)
    python scripts/benchmark_candidate.py freeze --cases benchmarks/conformance \\
        --season conformance-1 --status conformance \\
        --track TCM-Entity=normalize-tcm-entities ... --out benchmarks/registry/conformance-1.yaml

    # run the candidate: governed runs of every case on its track(s), twice each
    python scripts/benchmark_candidate.py run --candidate normalize-tcm-entities@1.0.0 \\
        --manifest benchmarks/registry/conformance-1.yaml --cases benchmarks/conformance \\
        --out bench/normalize

    # score it independently, from the bundle and the cases alone
    python scripts/benchmark_candidate.py score --bundle bench/normalize \\
        --manifest ... --cases ... --out bench/normalize/scores.json

    # anyone holding the bundle and the cases can check the published score
    python scripts/benchmark_candidate.py verify --bundle bench/normalize \\
        --scores bench/normalize/scores.json --manifest ... --cases ...

    # every track's skill, as the tree holds it (what CI runs on a pull request)
    python scripts/benchmark_candidate.py season --manifest ... --cases ... --out bench/

The conformance Season is public and shows that the machinery works end to end.
A withheld Season is run the same way by whoever holds its cases. Its bundles contain
the cases' inputs and outputs, so they are kept private, like the cases.
"""

from __future__ import annotations

import _bootstrap

_bootstrap.bootstrap()

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bioagent.benchmarks.independent import score_bundle, verify_scores, write_scores
from bioagent.benchmarks.runner import resolve_candidate, run_candidate
from bioagent.benchmarks.scorers import ScoringRefused
from bioagent.benchmarks.season import (SeasonError, freeze_season, load_cases, load_season,
                                        read_manifest, write_manifest)


def _summary(document: dict) -> str:
    row = document["row"]
    dims = ", ".join(f"{k} {v}" for k, v in row["dimensions"].items())
    gates = ", ".join(row["gates_failed"]) or "none"
    standing = ("conformance run, not a benchmark result"
                if document["season_status"] == "conformance" else f"{row['board']} board")
    return (f"{document['candidate']['composite_id']} on {document['season']}: "
            f"aggregate {row['aggregate']} ({standing}; gates failed: {gates})\n"
            f"  {dims}")


def _cmd_freeze(a) -> int:
    tracks = dict(t.split("=", 1) for t in a.track)
    cases = load_cases(a.cases)
    cut = a.cut_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = freeze_season(a.season, cases, cut_at=cut, tracks=tracks, status=a.status,
                             description=a.description)
    write_manifest(manifest, a.out)
    print(f"froze {len(cases)} case(s) as {a.season} -> {a.out} ({manifest.digest[:16]})")
    return 0


def _load(a):
    manifest = read_manifest(a.manifest)
    return manifest, load_season(manifest, a.cases)


def _cmd_run(a) -> int:
    manifest, season = _load(a)
    candidate = resolve_candidate(a.candidate)
    out = run_candidate(candidate, manifest, season, a.out, repeats=a.repeats)
    pinned = f"pinned by {candidate.pinned_by}" if candidate.pinned_by else "unpinned"
    print(f"ran {candidate.composite_id} ({pinned}) -> {out}")
    return 0


def _cmd_score(a) -> int:
    manifest, season = _load(a)
    document = score_bundle(a.bundle, manifest, season)
    write_scores(document, a.out)
    print(_summary(document))
    print(f"wrote {a.out} ({document['scores_digest'][:16]})")
    return 0


def _cmd_verify(a) -> int:
    manifest, season = _load(a)
    ok, detail = verify_scores(a.bundle, a.scores, manifest, season)
    print(("verified: " if ok else "NOT VERIFIED: ") + detail)
    return 0 if ok else 1


def _cmd_season(a) -> int:
    """Run, score and verify the skill of every track, as the tree holds it."""
    from bioagent.skills.loader import load_skills
    from bioagent.benchmarks.runner import _skill_roots

    manifest, season = _load(a)
    versions = {}
    for root in _skill_roots():
        loaded, _ = load_skills(root)
        versions.update({s.spec.id: s.spec.version for s in loaded})
    out = Path(a.out)
    failures = 0
    summaries = []
    for track, skill in sorted(manifest.tracks.items()):
        if skill not in versions:
            print(f"{track}: skill {skill!r} is not in the tree", file=sys.stderr)
            failures += 1
            continue
        candidate = resolve_candidate(f"{skill}@{versions[skill]}")
        bundle = out / skill
        if not (bundle / "bundle.json").is_file():
            run_candidate(candidate, manifest, season, bundle, repeats=a.repeats)
        document = score_bundle(bundle, manifest, season)
        write_scores(document, bundle / "scores.json")
        ok, detail = verify_scores(bundle, bundle / "scores.json", manifest, season)
        failures += not ok
        summaries.append(_summary(document) + f"\n  {'verified' if ok else 'NOT VERIFIED'}: "
                         f"{detail}")
    text = "\n".join(summaries)
    print(text)
    (out / "summary.txt").write_text(text + "\n", encoding="utf-8")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    fr = sub.add_parser("freeze", help="cut a Season manifest from a case directory")
    fr.add_argument("--cases", required=True)
    fr.add_argument("--season", required=True)
    fr.add_argument("--status", default="frozen", choices=("conformance", "frozen"))
    fr.add_argument("--track", action="append", default=[], required=True,
                    help="TRACK=skill-id; repeatable")
    fr.add_argument("--cut-at", default="")
    fr.add_argument("--description", default="")
    fr.add_argument("--out", required=True)
    for name, help_ in (("run", "run a candidate over its tracks' cases"),
                        ("score", "score a bundle independently"),
                        ("verify", "re-score a bundle and compare with a published score"),
                        ("season", "run, score and verify every track's skill")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--manifest", required=True)
        p.add_argument("--cases", required=True)
        if name == "run":
            p.add_argument("--candidate", required=True, help="skill-id@version")
        if name in ("score", "verify"):
            p.add_argument("--bundle", required=True)
        if name in ("run", "season"):
            p.add_argument("--repeats", type=int, default=2)
        if name in ("run", "score", "season"):
            p.add_argument("--out", required=True)
        if name == "verify":
            p.add_argument("--scores", required=True)
    a = ap.parse_args(argv)
    handler = {"freeze": _cmd_freeze, "run": _cmd_run, "score": _cmd_score,
               "verify": _cmd_verify, "season": _cmd_season}[a.cmd]
    try:
        return handler(a)
    except (SeasonError, ScoringRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
