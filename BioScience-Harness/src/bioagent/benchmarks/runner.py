"""Run a candidate skill over a frozen Season's cases, and record everything it produced.

The runner executes; it does not score. For each case on the tracks the Season
assigns to the candidate's skill, it runs the skill through the governed path
(:func:`governed.run_governed`) ``repeats`` times. It records each artifact, its
verdict, its output files and its latency in a bundle directory. The bundle states
what was run:

* the Season, by its manifest digest;
* the candidate, by id, version and content hash, and whether a lockfile pins it;
* the environment;
* the runner's own code digest.

Every file the bundle names is listed with its sha256. :mod:`benchmarks.independent`
scores a bundle from these files alone, and anyone holding the bundle and the cases
can re-score it and get the same numbers.

A candidate is by definition not yet reviewed, so it usually has no pin. It then runs
as an unpinned development run: recorded in the audit chain, never attested and never
released. The bundle records that, and so does the score.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..contracts.source_card import canonical_hash
from .models import BenchmarkCase, BenchmarkSeason
from .season import SeasonError, SeasonManifest

__all__ = ["BUNDLE_FILE", "Candidate", "RunnerError", "code_digest", "resolve_candidate",
           "run_candidate", "sha256_file"]

BUNDLE_FILE = "bundle.json"
_CANDIDATE = re.compile(r"^([a-z0-9][a-z0-9-]*)@([0-9]+\.[0-9]+\.[0-9]+)$")


class RunnerError(SeasonError):
    """The candidate cannot be run against this Season as asked."""


@dataclass(frozen=True)
class Candidate:
    """The exact skill being evaluated."""

    skill_id: str
    version: str
    directory: Path
    content_hash: str
    pinned_by: str = ""                 # the lockfile whose entry matches, if any

    @property
    def composite_id(self) -> str:
        return f"{self.skill_id}@{self.version}"

    def as_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "version": self.version,
                "composite_id": self.composite_id, "content_hash": self.content_hash,
                "pinned": bool(self.pinned_by), "pinned_by": self.pinned_by}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_digest(*modules: Any) -> str:
    """sha256 over the source of ``modules``: which code produced a bundle or a score."""
    digest = hashlib.sha256()
    for module in modules:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def _skill_roots() -> list[Path]:
    from ..config import skills_dir
    root = skills_dir()
    roots = [p for p in sorted(root.iterdir()) if p.is_dir() and p.name != "candidates"] \
        if root.is_dir() else []
    candidates = root / "candidates"
    if candidates.is_dir():
        roots += [p for p in sorted(candidates.iterdir()) if p.is_dir()]
    return roots


def resolve_candidate(candidate: str, *, roots: Sequence[str | Path] | None = None,
                      lockfile: str | Path | None = None) -> Candidate:
    """Find ``skill@version`` in the skill trees, and whether a lockfile pins it as is."""
    from ..skills.loader import load_skills
    from ..updates import load_lockfile

    match = _CANDIDATE.match(candidate.strip())
    if not match:
        raise RunnerError(f"candidate must be <skill-id>@<major>.<minor>.<patch>, "
                          f"not {candidate!r}")
    skill_id, version = match.groups()
    found = []
    for root in (roots or _skill_roots()):
        loaded, _ = load_skills(root)
        found += [s for s in loaded if s.spec.id == skill_id]
    if not found:
        raise RunnerError(f"no skill {skill_id!r} in the skill trees")
    if len(found) > 1:
        raise RunnerError(f"skill {skill_id!r} is defined more than once: "
                          f"{[str(s.directory) for s in found]}")
    skill = found[0]
    if skill.spec.version != version:
        raise RunnerError(f"candidate {candidate} was asked for, but the tree holds "
                          f"{skill_id}@{skill.spec.version}")
    pinned_by = ""
    if lockfile is None:
        from ..config import registry_dir
        lockfile = registry_dir() / "skills.lock.yaml"
    lock = Path(lockfile)
    if lock.is_file():
        for pin in load_lockfile(lock.read_text(encoding="utf-8")):
            if (pin.skill_id, pin.version, pin.content_hash) == (
                    skill_id, version, skill.content_hash):
                pinned_by = str(lock)
    return Candidate(skill_id=skill_id, version=version, directory=skill.directory,
                     content_hash=skill.content_hash, pinned_by=pinned_by)


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True,
                               default=str), encoding="utf-8")
    return sha256_file(path)


def run_candidate(candidate: Candidate, manifest: SeasonManifest, season: BenchmarkSeason,
                  out_dir: str | Path, *, repeats: int = 2,
                  callables: Mapping[str, Callable[..., Any]] | None = None) -> Path:
    """Run every case of the candidate's tracks ``repeats`` times; return the bundle dir."""
    from .. import governed
    from ..environment import environment_record
    from ..governed import GovernedRunRefused, run_governed, skill_callables

    if repeats < 2:
        raise RunnerError("reproducibility is measured by repetition; repeats must be ≥ 2")
    if season.season != manifest.season:
        raise RunnerError(f"cases of season {season.season!r} under manifest "
                          f"{manifest.season!r}")
    tracks = sorted(t for t, skill in manifest.tracks.items() if skill == candidate.skill_id)
    if not tracks:
        raise RunnerError(f"season {manifest.season!r} evaluates no track with skill "
                          f"{candidate.skill_id!r}")
    cases: list[BenchmarkCase] = [c for c in season.cases if c.track in tracks]
    if not cases:
        raise RunnerError(f"season {manifest.season!r} has no case on {tracks}")
    table = dict(callables) if callables is not None else skill_callables()
    fn = table.get(candidate.skill_id)
    if fn is None:
        raise RunnerError(f"no implementation is registered for {candidate.skill_id!r}")
    takes_run_id = "run_id" in inspect.signature(fn).parameters

    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise RunnerError(f"{out} is not empty; a bundle is written once")
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    case_records = []
    for case in cases:
        runs = []
        for rep in range(1, repeats + 1):
            rep_dir = out / "cases" / case.id / f"rep{rep}"
            arguments = dict(case.inputs)
            if takes_run_id:
                arguments.setdefault("run_id", f"bench-{manifest.season}-{case.id}")
            began = time.perf_counter()
            record: dict[str, Any] = {"rep": rep, "error": ""}
            try:
                run = run_governed(candidate.skill_id, arguments,
                                   skill_dir=candidate.directory.parent,
                                   state_dir=out / "state" / case.id / f"rep{rep}",
                                   output_dir=rep_dir / "outputs",
                                   lockfile=candidate.pinned_by or None,
                                   callables=table, allow_unpinned=not candidate.pinned_by)
            except GovernedRunRefused as exc:
                record["error"] = f"refused: {exc}"
            except Exception as exc:                         # noqa: BLE001
                record["error"] = f"{type(exc).__name__}: {exc}"
            record["latency_s"] = round(time.perf_counter() - began, 6)
            if not record["error"]:
                document = run.artifact.document()
                record.update({
                    "artifact": f"cases/{case.id}/rep{rep}/artifact.json",
                    "artifact_sha256": _write_json(rep_dir / "artifact.json", document),
                    "artifact_digest": run.artifact.digest,
                    "verdict": f"cases/{case.id}/rep{rep}/verdict.json",
                    "verdict_sha256": _write_json(rep_dir / "verdict.json",
                                                  run.verdict.as_dict()),
                    "outputs": {Path(w).name: sha256_file(w) for w in sorted(run.written)},
                    "released": run.released, "pinned_by": run.lockfile})
            runs.append(record)
        case_records.append({"case_id": case.id, "track": case.track,
                             "case_digest": case.digest, "runs": runs})
    bundle = {
        "schema": 1, "season": manifest.season, "season_status": manifest.status,
        "season_manifest_digest": manifest.digest, "scoring_rules": manifest.scoring_rules,
        "candidate": candidate.as_dict(), "tracks": tracks,
        "environment": environment_record(),
        "runner": {"code_digest": code_digest(sys.modules[__name__], governed),
                   "repeats": repeats},
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cases": case_records,
    }
    bundle["bundle_digest"] = canonical_hash({k: v for k, v in bundle.items()})
    _write_json(out / BUNDLE_FILE, bundle)
    return out
