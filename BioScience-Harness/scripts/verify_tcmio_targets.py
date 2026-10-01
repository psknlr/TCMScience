"""Check that TCMIO's relation file numbers targets by their row in target.xlsx.

TCMIO's ``ingredient_target_relation.xlsx`` names each target by a number, and
``target.xlsx`` has no id column. ``tcmdb.relations`` maps the number to the row (1-based,
under the header). This script tests that mapping against TCMIO's own site, using the GET
JSON endpoints its pages call:

* ``/targets/<id>/json`` must return the gene and UniProt accession of row ``<id>``;
* ``/ingredients/<id>/targets`` must return exactly the targets the relation file lists
  for that ingredient.

It samples a few of each, one request per second, against a built store:

    python scripts/verify_tcmio_targets.py --root <tcmdb root> [--targets 11] [--ingredients 8]

On 2026-10-02 a first check by hand and a run of this script (seed 2026) together
sampled 22 targets and 16 ingredients; all agreed.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BASE = "http://tcmio.xielab.net"


def _get(path: str) -> dict:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "bioagent-tcmdb/verify"})
    with urllib.request.urlopen(req, timeout=40) as resp:  # noqa: S310 - fixed host
        return json.load(resp)


def main() -> int:
    from bioagent.tcmdb import TCMDataHub

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", default="", help="tcmdb root (default: the hub's default)")
    ap.add_argument("--targets", type=int, default=11)
    ap.add_argument("--ingredients", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    db = TCMDataHub(args.root or None).db_path("tcmio")
    if not db.exists():
        print(f"no TCMIO store at {db}; build it first", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db)
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT rowid, Gene_name, Uniprot_id FROM target")}
    pairs: dict[int, set[int]] = defaultdict(set)
    for ing, tar in conn.execute("SELECT ingredient_id, target_id FROM ingredient_target"):
        pairs[int(ing)].add(int(tar))

    rng = random.Random(args.seed)
    failures = 0
    for tid in sorted(rng.sample(sorted(rows), min(args.targets, len(rows)))):
        data = _get(f"/targets/{tid}/json").get("Data") or {}
        ok = (data.get("GeneName"), data.get("UniprotId")) == rows[tid]
        failures += not ok
        print(f"{'ok ' if ok else 'BAD'} target {tid}: site {data.get('GeneName')} "
              f"{data.get('UniprotId')} / row {rows[tid][0]} {rows[tid][1]}")
        time.sleep(1)
    for ing in sorted(rng.sample(sorted(pairs), min(args.ingredients, len(pairs)))):
        site = {t.get("Id") for t in (_get(f"/ingredients/{ing}/targets").get("Data") or [])}
        ok = site == pairs[ing]
        failures += not ok
        print(f"{'ok ' if ok else 'BAD'} ingredient {ing}: site {len(site)} targets, "
              f"relation file {len(pairs[ing])}")
        time.sleep(1)
    print("all agree" if not failures else f"{failures} disagreement(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
