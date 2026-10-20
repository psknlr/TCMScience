#!/usr/bin/env python3
"""The packed-fingerprint fixture of the browser's Tanimoto kernels (studio/web/js/compute/science-*.js).

The browser's applicability-domain search takes the fingerprints the chemistry writes as np.packbits rows (bits.u8, the
models' domain files) and must answer exactly as Python's ``bioagent.admet.chem.tanimoto_best``. This writes, with
numpy, a set of query and reference fingerprints as the bytes np.packbits gives (hex, one row each) and the positions
of the bits set in each row, and what ``tanimoto_best`` returns for them: the nearest reference's index (the smallest
on ties), the integer intersection and union, and ``similarity`` in float64 (as hex). web/test/science-cpu.test.mjs
requires the JavaScript layout (packedWords, wordsFromBits) to give these very bytes and every kernel these answers.

The rows are structured: families of similar fingerprints, an empty and an all-ones reference and query, exact
duplicates (ties next to each other and far apart), and pairs of references at the same similarity to a query with
different counts (20/40 and 25/50), in both orders, so that the tie rule decides the counts as well as the index.

    python3 studio/web/test/fixtures/make_science_fixtures.py           # rewrites science_tanimoto.json
    python3 studio/web/test/fixtures/make_science_fixtures.py --check   # fails when the committed file differs

Needs numpy 2 (np.bitwise_count) and bioagent importable (BioScience-Harness/src on the path, or installed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
OUT = HERE / "science_tanimoto.json"
SRC = HERE.parents[3] / "BioScience-Harness" / "src"
SEED = 20261019
BITS = 2048
N_REF = 300
N_QUERY = 40


def _rows():
    import numpy as np
    rng = np.random.default_rng(SEED)

    def bits_of(positions):
        row = np.zeros(BITS, dtype=bool)
        row[list(positions)] = True
        return row

    def family_member(base):
        row = base.copy()
        on = np.flatnonzero(row)
        row[rng.choice(on, size=int(rng.integers(0, 6)), replace=False)] = False
        row[rng.integers(0, BITS, size=int(rng.integers(0, 6)))] = True
        return row

    bases = [bits_of(rng.choice(BITS, size=int(rng.integers(40, 61)), replace=False)) for _ in range(30)]
    refs = np.stack([family_member(bases[int(rng.integers(0, len(bases)))]) for _ in range(N_REF)])
    queries = np.stack([family_member(bases[int(rng.integers(0, len(bases)))]) for _ in range(N_QUERY)])
    refs[37] = False                     # an empty reference
    refs[211] = True                     # an all-ones reference
    refs[21] = refs[20]                  # duplicates: next to each other and in another block
    refs[150] = refs[20]
    queries[0] = False                   # an empty query: every similarity is 0, so reference 0
    queries[1] = True                    # an all-ones query: the all-ones reference
    queries[2] = refs[20]                # three equal maxima: 20, 21 and 150
    queries[3] = refs[37]                # the empty reference's copy, as the empty query
    queries[5] = refs[N_REF - 1]
    # the same similarity with other counts: 20/40 at the smaller index for query 4, 25/50 for query 6
    for q, (first, second) in ((4, (60, 120)), (6, (70, 130))):
        s = rng.choice(BITS, size=40, replace=False)
        others = np.setdiff1d(np.arange(BITS), s)
        queries[q] = bits_of(s)
        lo, hi = (first, second) if q == 4 else (second, first)
        refs[lo] = bits_of(s[:20])                                        # 20 / (40 + 20 - 20) = 1/2
        refs[hi] = bits_of(list(s[:25]) + list(rng.choice(others, 10, replace=False)))  # 25 / (40 + 35 - 25)
    return queries, refs


def _brute(q_bits, r_bits):
    best = []
    for q in q_bits:
        top = None
        for i, r in enumerate(r_bits):
            c, u = int((q & r).sum()), int((q | r).sum())
            # c / u > bc / bu, exactly; an empty union is 0 / 1
            ce, ue = (c, u) if u else (0, 1)
            if top is None or ce * top[4] > top[3] * ue:
                top = (i, c, u, ce, ue)
        best.append(top[:3])
    return best


def build() -> dict:
    sys.path.insert(0, str(SRC))
    import numpy as np
    from bioagent.admet.chem import similarity, tanimoto_best
    q_bits, r_bits = _rows()
    q_packed, r_packed = np.packbits(q_bits, axis=1), np.packbits(r_bits, axis=1)
    arg, inter, union = tanimoto_best(q_packed, r_packed)
    sim = similarity(inter, union)
    if [tuple(map(int, t)) for t in zip(arg, inter, union)] != _brute(q_bits, r_bits):
        raise SystemExit("tanimoto_best and the brute-force search disagree: not writing a fixture")
    if tuple(arg[[2, 4, 6]]) != (20, 60, 70) or tuple(inter[[4, 6]]) != (20, 25):
        raise SystemExit("the planted ties did not come out as planted")
    hexrows = lambda packed: [bytes(row).hex() for row in packed]
    positions = lambda b: [np.flatnonzero(row).tolist() for row in b]
    return {
        "about": "np.packbits rows (MSB first) and bioagent.admet.chem.tanimoto_best's answers for them; "
                 "written by make_science_fixtures.py",
        "numpy": np.__version__,
        "seed": SEED,
        "bits": BITS,
        "words": BITS // 32,
        "queries": {"rows": hexrows(q_packed), "bits_set": positions(q_bits),
                    "sha256": hashlib.sha256(q_packed.tobytes()).hexdigest()},
        "refs": {"rows": hexrows(r_packed), "bits_set": positions(r_bits),
                 "sha256": hashlib.sha256(r_packed.tobytes()).hexdigest()},
        "expected": {"arg": arg.tolist(), "inter": inter.tolist(), "union": union.tolist(),
                     "similarity_f64": [struct.pack(">d", float(x)).hex() for x in sim]},
        "planted": {"empty_query": 0, "all_ones_query": 1, "three_equal_maxima": {"query": 2, "refs": [20, 21, 150]},
                    "empty_reference": 37, "all_ones_reference": 211,
                    "same_ratio_other_counts": [{"query": 4, "refs": [60, 120]}, {"query": 6, "refs": [70, 130]}]},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when the committed fixture differs")
    args = ap.parse_args()
    text = json.dumps(build(), separators=(",", ":")) + "\n"
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            print(f"out of date: {OUT}", file=sys.stderr)
            return 1
        print(f"up to date: {OUT}")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
