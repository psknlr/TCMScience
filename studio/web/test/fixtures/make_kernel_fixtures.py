#!/usr/bin/env python3
"""The parity cases for the browser's CPU kernels (studio/web/js/compute/kernels.js).

Each case is an input and what the Python implementation returns for it, computed with no
kernel installed. web/test/kernels.test.mjs runs the JavaScript kernels on the same inputs
through the same JSON boundary Python uses in the Worker and requires identical answers: the
same aligned strings, the same score to the bit and in type (int or float), the same counts.
studio/runner/tests/test_kernel_fixtures.py recomputes the cases and requires the committed
file to match, so a change to the Python implementation cannot leave the fixtures behind.

    python3 studio/web/test/fixtures/make_kernel_fixtures.py   # rewrites kernel_cases.json
"""
from __future__ import annotations

import json
import random
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "kernel_cases.json"
SEED = 20261010


def bits(x: float) -> str:
    return struct.pack(">d", float(x)).hex()


def _inputs(x, y, score, gap):
    from bioagent.tools.align import _kernel_inputs
    return _kernel_inputs(x, y, score, gap)


def align_case(kind, a, b, **kw):
    from bioagent.tools import align
    fn = align.global_alignment if kind == "nw" else align.local_alignment
    out = fn(a, b, **kw)
    x, y = align._prepare(a, b)
    score = align._score_fn(kw.get("match", 2.0), kw.get("mismatch", -1.0), kw.get("matrix"))
    gap = kw.get("gap", -2.0)
    inputs = _inputs(x, y, score, gap)
    if inputs is None:
        return None
    alpha_x, alpha_y, table = inputs
    payload = {"x": x, "y": y, "alpha_x": alpha_x, "alpha_y": alpha_y, "table": table,
               "table_int": [type(v) is int for v in table], "gap": gap, "gap_int": type(gap) is int}
    expect = {"aligned_x": out["aligned_a"], "aligned_y": out["aligned_b"], "score_bits": bits(out["score"]),
              "score_int": type(out["score"]) is int}
    if kind == "sw":
        expect.update({"start_a": out["start_a"], "end_a": out["end_a"], "start_b": out["start_b"], "end_b": out["end_b"]})
    return {"kernel": f"align.{'nw' if kind == 'nw' else 'sw'}_linear", "payload": payload, "expect": expect}


def lev_case(a, b):
    from bioagent.tools.sequence import edit_distance
    out = edit_distance(a, b)
    return {"kernel": "sequence.levenshtein", "payload": {"x": a.strip().upper(), "y": b.strip().upper()},
            "expect": {"distance": out["distance"]}}


def counts_case(tool, sequences, window=0):
    """The counts the Python implementation would compute for this (already normalised) input."""
    n, width = len(sequences), len(sequences[0])
    if tool == "native.distance_matrix":
        counts = [0] * (n * n * 4)
        for i in range(n):
            for j in range(i + 1, n):
                sites = diffs = ts = 0
                for p, q in zip(sequences[i], sequences[j]):
                    if p in "ACGT" and q in "ACGT":
                        sites += 1
                        if p != q:
                            diffs += 1
                            if (p, q) in {("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")}:
                                ts += 1
                counts[(i * n + j) * 4:(i * n + j) * 4 + 3] = [sites, diffs, ts]
    elif tool == "native.hamming_distance":
        counts = [sum(1 for p, q in zip(*sequences) if p != q), 0, 0, 0]
    else:
        s = sequences[0]
        records = width - window + 2 if window > 0 else 1
        counts = [0] * (records * 4)
        counts[0] = sum(s.count(c) for c in "GCS")
        for r in range(1, records):
            counts[r * 4] = sum(s[r - 1:r - 1 + window].count(c) for c in "GCS")
    return {"kernel": "sequence.counts", "payload": {"tool": tool, "sequences": sequences, "window": window},
            "expect": counts}


def _as_sent(case):
    """The payload as text, exactly as tcmstudio.browser_compute sends it: a JavaScript
    re-serialisation would lose what the boundary must keep (JSON.stringify(-0) is "0")."""
    case["payload_json"] = json.dumps(case.pop("payload"), ensure_ascii=False)
    return case


def build():
    rng = random.Random(SEED)
    dna = lambda n, alphabet="ACGT": "".join(rng.choice(alphabet) for _ in range(n))  # noqa: E731
    protein = "ARNDCQEGHILKMFPSTWYV"
    cases = []
    add = lambda c: cases.append(c) if c is not None else None  # noqa: E731
    # defaults (floats), random lengths, both modes
    for _ in range(40):
        a, b = dna(rng.randint(1, 60)), dna(rng.randint(1, 60))
        add(align_case("nw", a, b))
        add(align_case("sw", a, b))
    # integer scores: Python mixes int and float cells (H[0][0] is 0.0); the type of the result follows the path
    for _ in range(40):
        a, b = dna(rng.randint(1, 40)), dna(rng.randint(1, 40))
        kw = {"match": rng.choice([1, 2, 3]), "mismatch": rng.choice([-1, -2, 0]), "gap": rng.choice([-1, -2, -3, 0])}
        add(align_case("nw", a, b, **kw))
        add(align_case("sw", a, b, **kw))
    # mixed int and float, ties everywhere, negative zero
    for kw in ({"match": 1, "mismatch": -1.0, "gap": -1}, {"match": 1.0, "mismatch": -1, "gap": -1.0},
               {"match": 0.0, "mismatch": -0.0, "gap": -0.0}, {"match": 0, "mismatch": 0, "gap": 0},
               {"match": -0.0, "mismatch": -1.0, "gap": -0.5}, {"match": 0.1, "mismatch": -0.2, "gap": -0.3}):
        for _ in range(8):
            a, b = dna(rng.randint(1, 30), "AC"), dna(rng.randint(1, 30), "AC")
            add(align_case("nw", a, b, **kw))
            add(align_case("sw", a, b, **kw))
    # a score of -0.0 (gaps of -0.0 beat a mismatch): JSON writes -0.0, so the sign must survive the kernel
    for a, b in (("A", "C"), ("AA", "CC"), ("ACA", "CAC"), ("AAAA", "C")):
        add(align_case("nw", a, b, match=1.0, mismatch=-1.0, gap=-0.0))
    # nothing to align locally: every pair scores below zero
    add(align_case("sw", "AAAA", "CCCC", match=1.0, mismatch=-1.0, gap=-1.0))
    add(align_case("sw", "A", "C"))
    # proteins with BLOSUM62 (protein_alignment's path), and lengths near the ends of the range
    for _ in range(20):
        a = "".join(rng.choice(protein) for _ in range(rng.randint(5, 80)))
        b = "".join(rng.choice(protein) for _ in range(rng.randint(5, 80)))
        add(align_case("nw", a, b, gap=-4.0, matrix="BLOSUM62"))
        add(align_case("sw", a, b, gap=-4.0, matrix="BLOSUM62"))
    add(align_case("nw", dna(1), dna(300)))
    add(align_case("nw", dna(300), dna(1)))
    add(align_case("sw", dna(250), dna(250)))
    # letters outside the Basic Multilingual Plane are one letter each, in Python and in the kernel
    add(align_case("nw", "𠀀黄芪𠀁", "黄𠀀芪", match=1.0, mismatch=-1.0, gap=-1.0))
    add(align_case("sw", "甘草𠀂甘遂", "甘遂𠀂", match=1.0, mismatch=-1.0, gap=-1.0))
    # edit distance: any alphabet, including astral CJK
    for a, b in (("kitten", "sitting"), ("", "abc"), ("abc", ""), ("黄芪", "黃耆"), ("𠀀𠀁", "𠀁𠀀"),
                 ("葛根芩连汤", "葛根黄芩黄连汤"), ("flaw", "lawn")):
        add(lev_case(a, b))
    for _ in range(30):
        add(lev_case(dna(rng.randint(0, 50), "ACGTN"), dna(rng.randint(0, 50), "ACGTN")))
    # sequence counts: the WebGPU kernel's layout, on the CPU
    for _ in range(10):
        width = rng.randint(1, 80)
        seqs = [dna(width, "ACGTN-RYKM") for _ in range(rng.randint(2, 6))]
        cases.append(counts_case("native.distance_matrix", seqs))
    for _ in range(10):
        width = rng.randint(1, 80)
        cases.append(counts_case("native.hamming_distance", [dna(width, "ACGTUNRYSW"), dna(width, "ACGTUNRYSW")]))
    for _ in range(10):
        width = rng.randint(1, 80)
        cases.append(counts_case("native.gc_content", [dna(width, "ACGTUNRYSW")], rng.choice([0, 1, rng.randint(1, width)])))
    return {"schema": "tcmstudio.kernel-cases/2", "seed": SEED, "cases": [_as_sent(c) for c in cases]}


def main() -> int:
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(build()['cases'])} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
