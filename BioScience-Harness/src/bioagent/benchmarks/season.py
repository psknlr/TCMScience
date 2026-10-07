"""Cases on disk and the frozen manifest of a Season.

A Season is frozen by a manifest: the cases it holds (or, for a withheld case set,
only their split digests and counts), the skill each track evaluates, and the version
of the scoring rules. :func:`load_season` refuses a case directory that does not match
the manifest — a case edited, added or removed after the cut — so a score is always tied
to the exact cases it was computed on (ADR-0001).

Case files are JSON under ``<root>/<visibility>/``: one case per file, or
``{"cases": [...]}``. A case's declared visibility must match its directory, because the
directory is what the ignore rules act on (:func:`models.split_cases`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..contracts.source_card import canonical_hash
from .models import (CASE_VISIBILITIES, TRACKS, BenchmarkCase, BenchmarkSeason, CaseError,
                     split_cases)

__all__ = ["CASE_KEYS", "SCORING_RULES_VERSION", "SEASON_STATUSES", "SeasonError",
           "SeasonManifest", "case_from_dict", "freeze_season", "load_cases",
           "load_season", "read_manifest", "write_manifest"]

#: Bumped whenever a metric in ``benchmarks.metrics`` changes what it measures. A score
#: names the rules it was computed under, so two scores under different rules are not
#: compared silently.
SCORING_RULES_VERSION = "1"

#: ``conformance``: a public case set that exercises the runner and the scorers end to
#: end; its scores say the machinery works, not how good a system is. ``frozen``: a
#: Season whose cases are withheld and whose scores go on the board.
SEASON_STATUSES = ("conformance", "frozen")

CASE_KEYS = frozenset({"id", "track", "visibility", "question", "inputs", "gold",
                       "sources", "licence", "notes", "adjudication", "siblings"})


class SeasonError(CaseError):
    """A case directory or manifest that does not describe the Season it claims to."""


def case_from_dict(data: Mapping[str, Any], *, where: str = "") -> BenchmarkCase:
    """A :class:`BenchmarkCase` from its JSON form; unknown keys are refused."""
    if not isinstance(data, Mapping):
        raise SeasonError(f"{where or 'a case'} is not a JSON object")
    unknown = sorted(set(data) - CASE_KEYS)
    if unknown:
        raise SeasonError(f"case {data.get('id', '?')!r} has unknown key(s) {unknown}")
    for key in ("inputs", "gold"):
        if not isinstance(data.get(key, {}), Mapping):
            raise SeasonError(f"case {data.get('id', '?')!r}: {key} must be an object")
    return BenchmarkCase(
        id=str(data.get("id", "")), track=str(data.get("track", "")),
        visibility=str(data.get("visibility", "")), question=str(data.get("question", "")),
        inputs=dict(data.get("inputs") or {}), gold=dict(data.get("gold") or {}),
        sources=tuple(data.get("sources") or ()), licence=str(data.get("licence", "")),
        notes=str(data.get("notes", "")), adjudication=str(data.get("adjudication", "")),
        siblings=tuple(data.get("siblings") or ()))


def load_cases(root: str | Path) -> tuple[BenchmarkCase, ...]:
    """Every case under ``root/<visibility>/``, refusing duplicates and misplaced cases."""
    root = Path(root)
    if not root.is_dir():
        raise SeasonError(f"no case directory at {root}")
    stray = sorted(p.name for p in root.glob("*.json"))
    if stray:
        raise SeasonError(f"case files must sit in a visibility directory "
                          f"({', '.join(CASE_VISIBILITIES)}), not at the top: {stray}")
    cases: list[BenchmarkCase] = []
    for visibility in CASE_VISIBILITIES:
        folder = root / visibility
        if not folder.is_dir():
            continue
        found = []
        for path in sorted(folder.rglob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data["cases"] if isinstance(data, Mapping) and "cases" in data else [data]
            found.extend(case_from_dict(item, where=str(path)) for item in items)
        try:
            split_cases(found, where=visibility)
        except CaseError as exc:
            raise SeasonError(str(exc)) from exc
        cases.extend(found)
    ids = [c.id for c in cases]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        raise SeasonError(f"case id(s) used more than once: {repeated}")
    return tuple(sorted(cases, key=lambda c: c.id))


@dataclass(frozen=True)
class SeasonManifest:
    """What a Season is: its cases (or their digests), its tracks and its rules."""

    season: str
    status: str
    cut_at: str
    tracks: Mapping[str, str]                      # track -> the skill id it evaluates
    split_digests: Mapping[str, str]
    counts: Mapping[str, int]
    scoring_rules: str = SCORING_RULES_VERSION
    #: ``(id, track, visibility, digest)`` per case; empty for a withheld case set,
    #: whose ids would themselves disclose it.
    cases: tuple[tuple[str, str, str, str], ...] = ()
    description: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in SEASON_STATUSES:
            raise SeasonError(f"season status {self.status!r} is not one of "
                              f"{SEASON_STATUSES}")
        unknown = sorted(set(self.tracks) - set(TRACKS))
        if unknown:
            raise SeasonError(f"unknown track(s) {unknown}")

    def content(self) -> dict[str, Any]:
        body = {"schema": 1, "season": self.season, "status": self.status,
                "cut_at": self.cut_at, "scoring_rules": self.scoring_rules,
                "tracks": dict(sorted(self.tracks.items())),
                "split_digests": dict(sorted(self.split_digests.items())),
                "counts": dict(sorted(self.counts.items())),
                "description": self.description}
        if self.cases:
            body["cases"] = [{"id": i, "track": t, "visibility": v, "digest": d}
                             for i, t, v, d in self.cases]
        if self.extra:
            body["extra"] = dict(self.extra)
        return body

    @property
    def digest(self) -> str:
        """Binds a score to this manifest: cases, tracks and rules together."""
        return canonical_hash(self.content())

    def skill_for(self, track: str) -> str:
        return self.tracks.get(track, "")


def freeze_season(season: str, cases: Sequence[BenchmarkCase], *, cut_at: str,
                  tracks: Mapping[str, str], status: str = "frozen",
                  publish_case_ids: bool | None = None,
                  description: str = "") -> SeasonManifest:
    """The manifest of ``cases`` as a Season. Case ids are listed only when every case
    is public, unless ``publish_case_ids`` says otherwise."""
    built = BenchmarkSeason(season=season, cut_at=cut_at, cases=tuple(cases))
    digests = built.compute_split_digests()
    counts = {v: len([c for c in cases if c.visibility == v]) for v in CASE_VISIBILITIES}
    public = all(not c.is_held_out for c in cases)
    if publish_case_ids is None:
        publish_case_ids = public
    if publish_case_ids and not public:
        raise SeasonError("a manifest that lists case ids must not cover held-out cases")
    missing = sorted({c.track for c in cases} - set(tracks))
    if missing:
        raise SeasonError(f"cases on track(s) {missing} have no skill to evaluate")
    listed = tuple((c.id, c.track, c.visibility, c.digest)
                   for c in sorted(cases, key=lambda c: c.id)) if publish_case_ids else ()
    return SeasonManifest(season=season, status=status, cut_at=cut_at,
                          tracks=dict(tracks), split_digests=digests, counts=counts,
                          cases=listed, description=description)


def write_manifest(manifest: SeasonManifest, path: str | Path) -> Path:
    import yaml
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("# A frozen benchmark Season. Generated by scripts/benchmark_candidate.py "
            "freeze; do not hand-edit.\n"
            f"# manifest digest: {manifest.digest}\n")
    text += yaml.safe_dump(manifest.content(), allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def read_manifest(path: str | Path) -> SeasonManifest:
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {"schema", "season", "status", "cut_at", "scoring_rules", "tracks",
             "split_digests", "counts", "description", "cases", "extra"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise SeasonError(f"manifest {path} has unknown key(s) {unknown}")
    if data.get("schema") != 1:
        raise SeasonError(f"manifest {path} has schema {data.get('schema')!r}, not 1")
    cases = tuple((str(c["id"]), str(c["track"]), str(c["visibility"]), str(c["digest"]))
                  for c in data.get("cases") or ())
    return SeasonManifest(season=str(data["season"]), status=str(data["status"]),
                          cut_at=str(data["cut_at"]),
                          scoring_rules=str(data.get("scoring_rules", "")),
                          tracks=dict(data.get("tracks") or {}),
                          split_digests=dict(data.get("split_digests") or {}),
                          counts={k: int(v) for k, v in (data.get("counts") or {}).items()},
                          cases=cases, description=str(data.get("description", "")),
                          extra=dict(data.get("extra") or {}))


def load_season(manifest: SeasonManifest, cases_root: str | Path) -> BenchmarkSeason:
    """The cases of ``manifest`` from ``cases_root``, refused if they are not its cases."""
    if manifest.scoring_rules != SCORING_RULES_VERSION:
        raise SeasonError(
            f"season {manifest.season!r} was frozen under scoring rules "
            f"{manifest.scoring_rules!r}; this build scores under {SCORING_RULES_VERSION!r}")
    cases = load_cases(cases_root)
    season = BenchmarkSeason(season=manifest.season, cut_at=manifest.cut_at, cases=cases)
    digests = season.compute_split_digests()
    for split, expected in manifest.split_digests.items():
        if digests.get(split) != expected:
            raise SeasonError(
                f"the {split} cases under {cases_root} are not the cases season "
                f"{manifest.season!r} froze (split digest {digests.get(split, '')[:12]} != "
                f"{expected[:12]}): a case was edited, added or removed after the cut")
    if manifest.cases:
        listed = {i: (t, v, d) for i, t, v, d in manifest.cases}
        found = {c.id: (c.track, c.visibility, c.digest) for c in cases}
        if listed != found:
            changed = sorted(set(listed) ^ set(found)) or sorted(
                i for i in listed if listed[i] != found.get(i))
            raise SeasonError(f"case(s) differ from the manifest: {changed}")
    untracked = sorted({c.track for c in cases} - set(manifest.tracks))
    if untracked:
        raise SeasonError(f"cases on track(s) {untracked} have no skill in the manifest")
    return BenchmarkSeason(season=manifest.season, cut_at=manifest.cut_at, cases=cases,
                           split_digests=digests, notes=manifest.description)
