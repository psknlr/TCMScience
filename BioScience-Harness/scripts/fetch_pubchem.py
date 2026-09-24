#!/usr/bin/env python
"""Save PubChem BioAssay results (active and inactive) for a formula's compounds.

    python scripts/fetch_pubchem.py --composition SNAP/composition.json --raw RAW

``composition.json`` is what ``build_source_snapshots.py gold`` writes; every compound
with an InChIKey is queried. This is the only step that touches the network; build the
snapshot from the saved file with ``build_source_snapshots.py pubchem``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bioagent.sources.fetch_pubchem import PubChemFetchError, fetch_assay_summaries  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--composition", required=True)
    ap.add_argument("--raw", required=True, help="directory to save the results in")
    args = ap.parse_args(argv)
    rows = json.loads(Path(args.composition).read_text(encoding="utf-8"))
    keys = sorted({r["compound"].split(":", 1)[1] for r in rows
                   if r["compound"].startswith("inchikey:")})
    try:
        path = fetch_assay_summaries(keys, args.raw)
    except PubChemFetchError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    print(f"{path} ({len(keys)} compounds queried)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
