#!/usr/bin/env python3
"""The research routes, as a document.

    python3 studio/scripts/routes_doc.py            # rewrites studio/docs/ROUTES.md
    python3 studio/scripts/routes_doc.py --check    # fails when the committed file is out of date

studio/docs/ROUTES.md is generated from ``tcmstudio.routes`` (the map research_routes reads);
studio/runner/tests/test_routes.py checks the committed file against the module too.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent
OUT = STUDIO / "docs" / "ROUTES.md"

for src in (STUDIO / "runner" / "src", REPO / "BioScience-Harness" / "src", REPO / "PSH-Harness" / "src"):
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when the committed file differs")
    args = ap.parse_args()
    from tcmstudio.routes import ROUTES, markdown
    text = markdown()
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"out of date: {OUT.relative_to(REPO)} (run python3 studio/scripts/routes_doc.py)")
            return 1
        print(f"research routes: {len(ROUTES)} routes, {OUT.relative_to(REPO)} current")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
