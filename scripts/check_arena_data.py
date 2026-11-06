#!/usr/bin/env python3
"""A score must never be rendered without its decomposition.

The plan states it as a requirement, so it is checked mechanically rather than
left to a template: every leaderboard row must carry all eight dimensions, and a
row blocked from the trusted board must say why. A row showing only an aggregate
would be a design failure that looks like a formatting choice.

And a row on the trusted board must have earned it: a frozen Season, the status
``benchmarks/season.py`` gives a withheld case set whose scores go on the board
(``season_status`` in ``tracks.json``), and more than one case. The demonstration
run once put its seven one-case rows on the trusted board, against its own
docstring, so a visitor read them as benchmark results.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Both trees: these scripts import bioagent, which imports psh.
for _entry in (ROOT / "BioScience-Harness" / "src", ROOT / "PSH-Harness" / "src"):
    if _entry.is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

#: The ``season_status`` of a Season whose rows may go on the trusted board:
#: ``frozen``, a withheld case set (``bioagent.benchmarks.season.SEASON_STATUSES``),
#: which ``benchmarks/independent.py`` records in the score document it puts on
#: board ``trusted``. The other statuses are not it: ``conformance`` (public cases;
#: the scores say the machinery works), ``designed`` (benchmarks.json, nothing run)
#: and ``demonstration`` (run_demo_season.py).
TRUSTED_SEASON_STATUSES = ("frozen",)


def main(argv: list[str]) -> int:
    from bioagent.benchmarks import SCORE_DIMENSIONS

    data_dir = Path(argv[1]) if len(argv) > 1 else ROOT / "arena" / "web" / "data"
    problems: list[str] = []

    lb = data_dir / "leaderboard.json"
    if not lb.is_file():
        print(f"no leaderboard.json in {data_dir}", file=sys.stderr)
        return 1
    doc = json.loads(lb.read_text(encoding="utf-8"))

    # The site reads `leaderboard.runs` (see arena/web/assets/app.js). The
    # checker originally looked for `rows`, which no data file has — so it
    # passed on every input including an empty one, and was decoration until a
    # real data file made the key mismatch visible.
    if "runs" not in doc:
        print("ARENA DATA CHECK FAILED:", file=sys.stderr)
        print("  - leaderboard.json has no `runs` key; the site reads `runs` and "
              "would render an empty table", file=sys.stderr)
        return 1
    rows = doc["runs"] or []

    # Every row must carry its full decomposition, and a blocked row must say
    # why. This is the rule the plan states as a requirement, and it is checked
    # mechanically rather than left to a template: a row showing only an
    # aggregate would be a design failure that looks like a formatting choice.
    for row in rows:
        rid = row.get("system") or row.get("run_id") or "?"
        scores = row.get("scores") or {}
        missing = [d for d in SCORE_DIMENSIONS if d not in scores]
        if missing:
            problems.append(f"{rid}: row is missing dimension(s) {missing}")
        if row.get("aggregate") is None and not row.get("placeholder"):
            problems.append(f"{rid}: row carries no aggregate")
        if row.get("board") in ("experimental",) and not row.get("blocking_reasons"):
            problems.append(
                f"{rid}: on the experimental board but names no blocking reason; a "
                "blocked row must say why it is blocked")

    # A dimension that is null must carry a reason, or the site renders a dash
    # with no explanation — which reads as "zero" rather than "not measured".
    for row in rows:
        rid = row.get("system") or row.get("run_id") or "?"
        reasons = row.get("score_reasons") or {}
        for dim in SCORE_DIMENSIONS:
            if (row.get("scores") or {}).get(dim) is None and dim not in reasons:
                problems.append(
                    f"{rid}: dimension {dim!r} is null with no stated reason")

    # The season's dimension list and the code's must agree, or the site would
    # render a column nothing scores.
    tracks = data_dir / "tracks.json"
    tracks_doc = (json.loads(tracks.read_text(encoding="utf-8"))
                  if tracks.is_file() else {})
    declared = {d["key"] for d in (tracks_doc.get("dimensions") or [])}
    if declared and declared != set(SCORE_DIMENSIONS):
        problems.append(
            f"tracks.json declares dimensions {sorted(declared)} but the scorer "
            f"produces {sorted(SCORE_DIMENSIONS)}")

    # The trusted board is for benchmark results: a frozen Season, and more than
    # one case. Anything else belongs on the demonstration board, where the site
    # shows it under that name.
    season_status = tracks_doc.get("season_status") or doc.get("season_status")
    for row in rows:
        if row.get("board") != "trusted":
            continue
        rid = row.get("run_id") or row.get("system") or "?"
        if season_status not in TRUSTED_SEASON_STATUSES:
            problems.append(
                f"{rid}: on the trusted board, but its Season is not a frozen one "
                f"(season_status {season_status!r}); a demonstration row belongs on "
                "the demonstration board")
        cases = row.get("cases")
        if not isinstance(cases, int) or cases < 2:
            problems.append(
                f"{rid}: on the trusted board with {cases!r} case(s); one case is an "
                "example, not a benchmark result")

    if problems:
        print("ARENA DATA CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"ok: {len(rows or [])} row(s), every score decomposed across "
          f"{len(SCORE_DIMENSIONS)} dimensions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
