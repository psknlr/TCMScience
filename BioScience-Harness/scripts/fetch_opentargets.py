#!/usr/bin/env python
"""Save one disease's Open Targets target associations as a raw file.

    python scripts/fetch_opentargets.py MONDO_0005148 --raw DIR     # type 2 diabetes

This is the only step that touches the network; build the snapshot from the saved file
with ``scripts/build_source_snapshots.py opentargets --file ...``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bioagent.sources.fetch_opentargets import (  # noqa: E402
    OpenTargetsFetchError, fetch_disease_associations)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("disease", help="EFO/MONDO id as Open Targets writes it, e.g. MONDO_0005148")
    ap.add_argument("--raw", required=True, help="directory to save the answer in")
    args = ap.parse_args(argv)
    try:
        path = fetch_disease_associations(args.disease, args.raw)
    except OpenTargetsFetchError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
