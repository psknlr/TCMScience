"""Reference numbers for the page's accelerators, computed by CPython's own ``random``.

    python3 studio/web/test/accel/make_fixtures.py            # -> studio/web/test/accel/fixtures.json
    python3 studio/web/test/accel/make_fixtures.py --bench    # -> studio/web/js/accel/world-1399.json (~2 min)

bioagent must be importable (``pip install -e BioScience-Harness`` or PYTHONPATH=BioScience-Harness/src).

``fixtures.json`` holds (a) raw MT19937 vectors: the first outputs, ``_randbelow`` and ``sample`` results of
``random.Random(str)`` and ``random.Random(int)``; (b) small worlds in the kernel's input shape (docs/V2.md §13.4)
covering both ``sample`` branches and their boundaries (k <= 5 and k > 5, n == setsize), a bin with k == n, observed
0 and unreachable, members outside the pools, many bins, a numeric seed and non-ASCII ids, with ``at_least`` from the
statements of ``bioagent.analysis.network_pharmacology`` step 4 run here with the real ``_pathway_rng``; (c) the
pipeline's own test world run through ``run_network_pharmacology``, whose ``empirical_p`` gives ``at_least``.

``world-1399.json`` is the investigation's synthetic Reactome-like world (``make_world`` below, seed 1): the 1,399
BH-significant pathways, compactly encoded, with CPython's counts. The benchmark (web/js/accel/bench.js) runs it.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import platform
import random
import sys
import tempfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from bioagent.analysis import Parameters, run_network_pharmacology
from bioagent.analysis.network_pharmacology import _pathway_rng
from bioagent.tools.stats import benjamini_hochberg, hypergeometric_test

HERE = Path(__file__).resolve().parent
STUDIO = HERE.parents[2]
REPO = STUDIO.parent
FIXTURES = HERE / "fixtures.json"
WORLD = STUDIO / "web" / "js" / "accel" / "world-1399.json"


# ------------------------------------------------------------------ the kernel, as the repo runs it

def reference(inp: dict) -> list[int]:
    """at_least per pathway: step 4 of run_network_pharmacology, statement for statement, on the kernel's input
    (pools of protein indices; pathway members as a set, as ``tested[pathway]`` is)."""
    params = SimpleNamespace(seed=inp["seed"])           # _pathway_rng reads params.seed only
    pool = {b: list(members) for b, members in enumerate(inp["pools"])}
    wanted = dict(inp["wanted"])
    out = []
    for row in inp["pathways"]:
        pathway_members = set(row["members"])
        prng = _pathway_rng(params, row["id"])
        at_least = 0
        for _ in range(inp["permutations"]):
            draw = [x for b, k in sorted(wanted.items()) for x in prng.sample(pool[b], k)]
            if len(pathway_members.intersection(draw)) >= row["observed"]:
                at_least += 1
        out.append(at_least)
    return out


# ------------------------------------------------------------------ steps 3-4 setup, verbatim from the repo

def enrichment(members, assayed, query_targets, params: Parameters):
    annotated = set().union(*members.values()) if members else set()
    background = annotated & assayed if params.background == "assayed" else annotated
    query = sorted(t for t in query_targets if t in annotated)
    tested = {p: m & background for p, m in members.items()
              if params.pathway_min_size <= len(m) <= params.pathway_max_size
              and m & background}
    rows = []
    for pathway in sorted(tested):
        overlap = sorted(set(query) & tested[pathway])
        test = hypergeometric_test(len(overlap), len(query), len(tested[pathway]),
                                   len(background)) if query else {"p_value": 1.0}
        rows.append({"pathway": pathway, "overlap": len(overlap), "p_value": test["p_value"]})
    if rows:
        for row, q in zip(rows, benjamini_hochberg([r["p_value"] for r in rows])["q_values"]):
            row["q_value"] = q
    return background, query, tested, rows


def null_setup(members, background, query, params: Parameters):
    degree = defaultdict(int)
    for p, m in members.items():
        for protein in m:
            degree[protein] += 1
    ordered = sorted(background, key=lambda x: (degree[x], x))
    cuts = [degree[ordered[int(len(ordered) * k / params.degree_bins)]]
            for k in range(1, params.degree_bins)] if ordered else []

    def bin_of(protein):
        return bisect.bisect_right(cuts, degree[protein])

    pool = defaultdict(list)
    for protein in ordered:
        pool[bin_of(protein)].append(protein)
    wanted = defaultdict(int)
    for t in query:
        wanted[bin_of(t)] += 1
    return pool, wanted


def kernel_input(seed, permutations, pool, wanted, tested, rows):
    """The kernel's input as the browser tool builds it: proteins numbered in pool order (bins sorted), pools as
    index lists, wanted = sorted(wanted.items()), one entry per pathway row."""
    bins = sorted(pool)
    index, pools = {}, []
    for b in bins:
        pools.append([index.setdefault(x, len(index)) for x in pool[b]])
    position = {b: i for i, b in enumerate(bins)}
    return {"seed": str(seed), "permutations": permutations, "pools": pools,
            "wanted": [[position[b], k] for b, k in sorted(wanted.items())],
            "pathways": [{"id": r["pathway"], "observed": r["overlap"],
                          "members": sorted(index[x] for x in tested[r["pathway"]] if x in index)}
                         for r in rows]}


def make_world(seed=1, n_annot=11000, n_pathways=2600, n_assayed=1500, n_query=250, planted=12):
    """The investigation's Reactome-like world: heavy-tailed protein popularity (degree heterogeneity), assayed
    proteins biased to well-studied ones, potent targets concentrated in a few planted pathways plus noise."""
    r = random.Random(seed)
    prot = [f"uniprot:P{100000 + i}" for i in range(n_annot)]
    w = [1.0 / (i + 10) ** 0.8 for i in range(n_annot)]
    cum = []
    s = 0.0
    for x in w:
        s += x
        cum.append(s)

    def pick():
        return prot[bisect.bisect_left(cum, r.random() * s)]

    members = {}
    for k in range(n_pathways):
        size = int(math.exp(r.uniform(math.log(3), math.log(1500))))
        m = set()
        while len(m) < size:
            m.add(pick())
        members[f"reactome:R-HSA-{k:05d}"] = m
    ann = sorted(set().union(*members.values()))
    assayed = set()
    while len(assayed) < n_assayed:
        assayed.add(pick() if r.random() < 0.7 else r.choice(ann))
    background = set(ann) & assayed
    bg = sorted(background)
    mids = [p for p in sorted(members) if 20 <= len(members[p] & background) <= 120]
    query = set()
    for p in r.sample(mids, planted):
        inside = sorted(members[p] & background)
        query.update(r.sample(inside, max(1, len(inside) // 3)))
    while len(query) < n_query:
        query.add(r.choice(bg))
    return members, assayed, sorted(query)


def synthetic(params: Parameters, only_significant=True, **world):
    members, assayed, query = make_world(**world)
    background, q, tested, rows = enrichment(members, assayed, query, params)
    pool, wanted = null_setup(members, background, q, params)
    chosen = [r for r in rows if r["q_value"] <= params.fdr] if only_significant else rows
    return kernel_input(params.seed, params.permutations, pool, wanted, tested, chosen)


# ------------------------------------------------------------------ fixtures

def mt_vectors():
    out = {"str": [], "int": []}
    seeds = ["20260923:reactome:R-HSA-00001", "", "a", "20260923:通路·測試", "\x00\x00lead", "x" * 300,
             "7:reactome:R-HSA-1640170"]
    cases = [(5, 5), (21, 5), (22, 5), (22, 2), (85, 10), (86, 10), (277, 24), (278, 24), (30, 30), (1000, 1),
             (1, 1), (2 ** 20, 7), (6, 6)]
    for s in seeds:
        g = random.Random(s)
        first = [g.getrandbits(32) for _ in range(8)]
        below_n = [1, 2, 3, 7, 100, 2 ** 31, 2 ** 32 - 1]
        g = random.Random(s)
        below = [g._randbelow(n) for n in below_n]
        g = random.Random(s)
        samples = [[n, k, g.sample(range(n), k)] for n, k in cases]
        g = random.Random(s)
        floats = [g.random() for _ in range(3)]
        out["str"].append({"seed": s, "first": first, "below_n": below_n, "below": below, "samples": samples,
                           "random": floats})
    for n in (0, 1, 7, 20260923, 2 ** 32 + 5, 2 ** 52 + 3):
        g = random.Random(n)
        out["int"].append({"seed": n, "first": [g.getrandbits(32) for _ in range(5)]})
    return out


def rng(seed):
    return random.Random(f"fixtures:{seed}")


def world(name, note, pool_sizes, wanted, permutations, n_paths, seed="20260923", r=None, ids=None,
          member_extra=0, edge=None):
    """A small world: pools of consecutive protein indices, random members, observed spread around the mean."""
    r = r or rng(name)
    pools, start = [], 0
    for n in pool_sizes:
        pools.append(list(range(start, start + n)))
        start += n
    universe = start + member_extra                     # indices >= start are in no pool
    draws = sum(k for _, k in wanted)
    paths = []
    for i in range(n_paths):
        size = r.randint(0, max(1, universe // 3))
        members = sorted(r.sample(range(universe), size))
        expect = draws * size / max(1, start)
        observed = max(0, int(expect + r.randint(-2, 3)))
        pid = ids[i] if ids else f"reactome:R-HSA-{name}-{i:03d}"
        paths.append({"id": pid, "members": members, "observed": observed})
    if edge:
        edge(paths, pools, r)
    inp = {"seed": seed, "permutations": permutations, "pools": pools, "wanted": wanted, "pathways": paths}
    return {"name": name, "note": note, "input": inp}


def observed_edges(paths, pools, r):
    total = sum(len(p) for p in pools)
    paths.append({"id": "reactome:R-HSA-OBS0", "members": [0, 1, 2], "observed": 0})          # always: perms
    paths.append({"id": "reactome:R-HSA-NONE", "members": [], "observed": 0})                 # empty, observed 0
    paths.append({"id": "reactome:R-HSA-EMPTY1", "members": [], "observed": 1})               # empty: never
    paths.append({"id": "reactome:R-HSA-HIGH", "members": list(range(total)), "observed": 10_000})  # unreachable
    paths.append({"id": "reactome:R-HSA-ALL", "members": list(range(total)), "observed": 3})
    paths.append({"id": "reactome:R-HSA-DUP", "members": [5, 5, 6, 6, 7], "observed": 1})    # a repeat counts once


def pipeline_worlds():
    """The pipeline's own test world, run through run_network_pharmacology (the planted targets, and two spreads of
    targets over the background pathways); at_least from the pipeline's empirical_p."""
    sys.path.insert(0, str(REPO / "BioScience-Harness" / "tests"))
    import test_network_pharmacology as T  # noqa: E402
    P = T.PROTEINS
    variants = [
        ("pipeline-planted", "the planted pathway (fdr 0.9)", None, 0.9),
        ("pipeline-three", "targets in R-HSA-A, B0 and B3 (fdr 0.99)",
         {0: P[0:3], 1: P[3:4], 2: P[8:11], 3: [P[29], P[30]]}, 0.99),
        ("pipeline-spread", "targets over seven pathways (fdr 0.99)",
         {0: P[0:3], 1: P[8:10] + P[15:17], 2: [P[22], P[29], P[36]], 3: [P[43], P[44], P[2]]}, 0.99),
    ]
    out = []
    for name, what, targets_of, fdr in variants:
        params = replace(T.FAST, fdr=fdr, permutations=1000)
        with tempfile.TemporaryDirectory() as d:
            snaps = T._build(Path(d), **({"targets_of": targets_of} if targets_of else {}))
            res = run_network_pharmacology(snaps, params=params)
        by = {s.key: s for s in snaps}
        members = {}
        for e in by["reactome"].edges:
            if e.get("predicate") == "participates_in":
                members.setdefault(e["object"], set()).add(e["subject"])
        assayed = {e["object"] for e in by["npass"].edges if e.get("predicate") == "targets"}
        query = [t["target"] for t in res.targets]
        background, q, tested, rows = enrichment(members, assayed, query, params)
        pool, wanted = null_setup(members, background, q, params)
        nulled = [r for r in res.enrichment if r["empirical_p"] is not None]
        by_id = {r["pathway"]: r for r in rows}
        inp = kernel_input(params.seed, params.permutations, pool, wanted, tested, [by_id[r["pathway"]] for r in nulled])
        from_pipeline = [round(r["empirical_p"] * (1 + params.permutations)) - 1 for r in nulled]
        for r, at in zip(nulled, from_pipeline):
            assert (1 + at) / (1 + params.permutations) == r["empirical_p"], r
        out.append({"name": name, "note": f"BioScience-Harness tests' world through run_network_pharmacology, {what}, "
                    "1000 permutations; at_least from the pipeline's own empirical_p",
                    "input": inp, "at_least_from": "run_network_pharmacology", "pipeline_at_least": from_pipeline})
    return out


def worlds():
    out = [
        world("pool-small-k", "k <= 5, n <= 21: the pool branch, n == 21 at the boundary",
              [3, 7, 12, 21], [[0, 1], [1, 3], [2, 5], [3, 5]], 200, 12),
        world("set-small-k", "k <= 5, n > 21: the set branch (n = 22 just past the boundary)",
              [22, 60, 200], [[0, 2], [1, 5], [2, 4]], 200, 12),
        world("k-gt-5-boundaries", "k > 5 around setsize: n = 85 / 86 with k = 10, n = 277 / 278 with k = 24",
              [85, 86, 277, 278], [[0, 10], [1, 10], [2, 24], [3, 24]], 150, 10),
        world("k-equals-n", "bins drawn whole (k == n), next to partial ones",
              [6, 1, 40, 9], [[0, 6], [1, 1], [2, 40], [3, 3]], 120, 10),
        world("observed-edges", "observed 0, unreachable, empty members, all members, a repeated member",
              [15, 30], [[0, 4], [1, 7]], 100, 4, edge=observed_edges),
        world("many-bins", "25 wanted bins out of 30 pools (pools 3, 9, 17, 22, 28 not wanted), one with k = 0",
              [5 + 4 * i for i in range(30)],
              [[b, (b % 7) + 1] for b in range(30) if b not in (3, 9, 17, 22, 28) and b != 11] + [[11, 0]], 100, 8),
        world("outside-pools", "members outside every pool and in pools no bin draws from",
              [10, 25, 50, 12], [[1, 6], [2, 9]], 150, 10, member_extra=40),
        world("seed-and-ids", "a numeric seed and non-ASCII pathway ids",
              [30, 70], [[0, 3], [1, 8]], 120, 4, seed=7,
              ids=["reactome:R-HSA-通路-1", "reactome:R-HSA-Ω", "reactome:R-HSA-😀", "reactome:R-HSA-plain"]),
        world("zero-permutations", "permutations = 0: nothing is drawn, every count is 0",
              [10, 30], [[0, 3], [1, 6]], 0, 3),
        world("no-draws", "no wanted bins: every draw is empty (observed 0 counts every permutation, 1 none)",
              [10, 30], [], 50, 2,
              edge=lambda paths, pools, r: paths.append({"id": "reactome:R-HSA-OBS1", "members": [1, 2], "observed": 1})),
    ]
    # many bins: wanted must stay sorted by pool
    for w in out:
        w["input"]["wanted"].sort()
    mini = synthetic(Parameters(), n_annot=2500, n_pathways=500, n_assayed=600, n_query=80, planted=6)
    out.append({"name": "synthetic-mini", "note": "make_world(n_annot=2500, n_pathways=500, n_assayed=600, "
                "n_query=80, planted=6), BH-significant pathways, default Parameters", "input": mini})
    out.extend(pipeline_worlds())
    for w in out:
        w["at_least"] = reference(w["input"])
        if "pipeline_at_least" in w:
            assert w["at_least"] == w.pop("pipeline_at_least"), w["name"]
    return out


def write_fixtures():
    data = {"schema": "tcmstudio.accel.fixtures/1",
            "generated_by": "python3 studio/web/test/accel/make_fixtures.py",
            "python": platform.python_version(), "kernel": "np-null-mt/1",
            "mt": mt_vectors(), "worlds": worlds()}
    FIXTURES.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    sizes = {w["name"]: len(w["input"]["pathways"]) for w in data["worlds"]}
    print(f"{FIXTURES}: {FIXTURES.stat().st_size} bytes, worlds {sizes}")


def write_world():
    """The 1,399-pathway world, compact: pools are consecutive index ranges, members delta-encoded."""
    inp = synthetic(Parameters())
    sizes = [len(p) for p in inp["pools"]]
    for p, start in zip(inp["pools"], [sum(sizes[:i]) for i in range(len(sizes))]):
        assert p == list(range(start, start + len(p)))
    deltas = []
    for row in inp["pathways"]:
        m = row["members"]
        deltas.append([m[0]] + [b - a for a, b in zip(m, m[1:])] if m else [])
    print(f"{len(inp['pathways'])} pathways; counting with CPython (about 2 minutes) …", flush=True)
    data = {"schema": "tcmstudio.accel.world/1",
            "name": "np-null synthetic world (make_world, seed 1)",
            "about": "11,000 annotated proteins with heavy-tailed popularity, 2,600 pathways, 1,500 assayed "
                     "background proteins, 250 potent targets, 10 degree bins; the BH-significant pathways",
            "generated_by": "python3 studio/web/test/accel/make_fixtures.py --bench",
            "python": platform.python_version(),
            "seed": inp["seed"], "permutations": inp["permutations"],
            "pool_sizes": sizes, "wanted": inp["wanted"],
            "ids": [r["id"] for r in inp["pathways"]], "observed": [r["observed"] for r in inp["pathways"]],
            "members_delta": deltas, "cpython_at_least": reference(inp)}
    WORLD.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{WORLD}: {WORLD.stat().st_size} bytes")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bench", action="store_true", help="write web/js/accel/world-1399.json instead")
    args = ap.parse_args(argv)
    write_world() if args.bench else write_fixtures()


if __name__ == "__main__":
    main()
