#!/usr/bin/env python3
"""The browser sweep's calls (sweep.mjs): every tool the browser runs, with the input ``tcmstudio
validate`` gives it, and the groups beyond what validate runs.

    python sweep_calls.py --site SITE [--groups entry,core,...] [--out FILE]

SITE is a built site (build_web.py --dev): its runtime/catalog.json is the browser's catalog. The
entries the compatibility matrix (studio/docs/compat/matrix.csv) says run in the browser with class
1, 2, 3 or 5, and the catalog's core tools, are called with ``validate._example`` (the entry's
``example``, else ``validate.PROBES``); one with neither is listed in ``no_input`` and not called.
Connectors (class 1g) are called with web access off: both sides must refuse them as network_off.
Class 1j (browser jobs) is left to science.mjs. Entries are called as ``call_tool {tool,
arguments}`` and core tools by their name, as the router calls them.

The groups (all by default):

- entry, core, connector: as above;
- extra: the entries and core tools with no input, with the arguments in sweep_inputs.json (the
  runner's own tests' inputs);
- invalid: every entry and core tool with ``validate._invalid`` of its schema (the first property
  given a wrongly typed value), so that the two sides' refusals are compared;
- reference: each browser native tool with the arguments of each of its independent reference
  cases (``bioagent.validation.references``, what validate's functional_correctness runs);
- direct: each entry called by its own id (the router hands the runtime the entry id when the
  model names it);
- kernel: the seven tools with a CPU kernel, with inputs above the kernels' thresholds;
- workbook: calls that need the complete formula workbook, made before anything has loaded it in
  the page (a sequence of its own: its own page, project and native state root).

In the main sequence the calls the Worker loads the complete formula workbook for first
(FORMULA_TOOLS in pyodide.worker.js) come after every other call, so any other call that needs the
workbook meets a Worker that has not loaded it, as it would on a fresh page. Deterministic: the
same tree gives the same list.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1]
MATRIX = WEB.parent / "docs" / "compat" / "matrix.csv"
WORKER = WEB / "js" / "runtime" / "pyodide.worker.js"
INPUTS = HERE / "sweep_inputs.json"

BROWSER_CLASSES = ("1", "2", "3", "5")
CONNECTOR_CLASS = "1g"
GROUPS = ("entry", "core", "connector", "extra", "invalid", "reference", "direct", "kernel", "workbook")

#: A formula only the complete workbook holds (check.mjs uses it too), and the calls that name it: the governed
#: skills and the clinic check first (the Worker runs a stateful call once, without the reload-and-rerun a lookup
#: gets when it asks for the workbook), then the lookups that ask for it as they go, last those it is loaded for.
WORKBOOK_FORMULA = "甘遂半夏汤"
WORKBOOK_CALLS = (
    ("tcm_safety_report", {"subject": WORKBOOK_FORMULA}),
    ("tcm_normalize", {"names": [WORKBOOK_FORMULA, "桂枝汤《圣济总录》"]}),
    ("tcm_evidence", {"subject": WORKBOOK_FORMULA}),
    ("tcm_network_hypothesis", {"formula_name": WORKBOOK_FORMULA}),
    ("clinic_check_prescription", None),
    ("tcm_applicability", {"subject": WORKBOOK_FORMULA, "object": "留饮"}),
    ("tcm_classical_search", {"query": WORKBOOK_FORMULA}),
    ("call_tool", {"tool": "tcmdb.evidence_for", "arguments": {"subject": WORKBOOK_FORMULA}}),
    ("call_tool", {"tool": "system.provider_catalog", "arguments": {"query": WORKBOOK_FORMULA}}),
    ("catalog_search", {"query": WORKBOOK_FORMULA}),
    ("tcm_compatibility", {"herbs": [WORKBOOK_FORMULA, "人参"]}),
    ("tcm_compatibility", {"herbs": ["桂枝汤《圣济总录》"]}),
    ("tcm_formula", {"name": "桂枝汤《圣济总录》"}),
    ("tcm_lookup", {"name": WORKBOOK_FORMULA}),
)


def worker_formula_tools(worker_js: Path = WORKER) -> set[str]:
    """FORMULA_TOOLS of pyodide.worker.js: the calls the Worker loads the workbook for first."""
    text = worker_js.read_text(encoding="utf-8")
    m = re.search(r"const FORMULA_TOOLS = new Set\(\[(.*?)\]\)", text, re.S)
    if not m:
        raise SystemExit(f"{worker_js}: FORMULA_TOOLS not found")
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _seq(rng: random.Random, n: int, letters: str) -> str:
    return "".join(rng.choice(letters) for _ in range(n))


def kernel_cases() -> list[tuple[str, str, dict]]:
    """Inputs above the Worker's kernel thresholds (browser_compute.KERNEL_MIN_CELLS, KERNEL_MIN_OPERATIONS,
    GC_MIN_WINDOW) for the seven tools with a CPU kernel, so that the browser runs its kernels (acceleration auto)
    against native CPython's original code: awkward alphabets, lowercase, IUPAC codes, gaps, fractional scores, a
    substitution matrix, ties, and a refusal (unequal lengths). Seeded."""
    rng = random.Random(20261028)
    dna, iupac, prot = "ACGT", "ACGTRYSWKMBDHVN", "ARNDCQEGHILKMFPSTWYV"
    a1, b1 = _seq(rng, 140, dna), _seq(rng, 120, dna)
    mutated = "".join(c if rng.random() > 0.1 else rng.choice(dna) for c in a1)
    transitions = ({"A", "G"}, {"C", "T"})
    matrix = {x: {y: (3 if x == y else -2 if {x, y} in transitions else -3) for y in dna} for x in dna}
    cases = [
        ("global_alignment", "dna 140x120 default scores", {"a": a1, "b": b1}),
        ("global_alignment", "fractional scores", {"a": a1, "b": mutated, "match": 1.5, "mismatch": -0.75, "gap": -2.25}),
        ("global_alignment", "lowercase and N", {"a": a1.lower()[:100] + "nnnn", "b": mutated[:90] + "NNNN"}),
        ("global_alignment", "ties (poly-A)", {"a": "A" * 70, "b": "A" * 64}),
        ("global_alignment", "substitution matrix", {"a": a1[:80], "b": b1[:70], "matrix": matrix, "gap": -4}),
        ("local_alignment", "dna 150x130 integer scores", {"a": _seq(rng, 150, dna), "b": _seq(rng, 130, dna), "match": 3, "mismatch": -2, "gap": -3}),
        ("local_alignment", "embedded motif, fractional gap", {"a": _seq(rng, 60, dna) + a1[:50] + _seq(rng, 60, dna), "b": a1[:50], "gap": -1.5}),
        ("local_alignment", "ties (poly-A)", {"a": "A" * 80, "b": "A" * 40}),
        ("protein_alignment", "global 90x80", {"a": _seq(rng, 90, prot), "b": _seq(rng, 80, prot), "mode": "global"}),
        ("protein_alignment", "local, gap -8", {"a": _seq(rng, 100, prot), "b": _seq(rng, 70, prot), "mode": "local", "gap": -8}),
        ("protein_alignment", "X, B, Z, * and lowercase", {"a": _seq(rng, 80, prot + "XBZ*").lower(), "b": _seq(rng, 75, prot + "XBZ*"), "mode": "global", "gap": -6}),
        ("edit_distance", "dna 120x110", {"a": _seq(rng, 120, dna), "b": _seq(rng, 110, dna)}),
        ("edit_distance", "Chinese text", {"a": "桂枝汤方桂枝芍药甘草生姜大枣" * 6, "b": "桂枝加葛根汤方葛根桂枝芍药生姜甘草大枣" * 5}),
        ("edit_distance", "one empty side", {"a": _seq(rng, 3000, dna), "b": ""}),
        ("distance_matrix", "8 x 600, IUPAC and gaps, p", {"sequences": {f"t{i}": _seq(rng, 600, iupac + "--") for i in range(8)}, "model": "p"}),
        ("distance_matrix", "related 10 x 500, k2p", {"sequences": {f"r{i}": "".join(c if rng.random() > 0.05 * (i + 1) else rng.choice(dna) for c in a1 * 4)[:500] for i in range(10)}, "model": "k2p"}),
        ("distance_matrix", "related 10 x 500, jc69, as a list", {"sequences": ["".join(c if rng.random() > 0.04 * (i + 1) else rng.choice(dna + "N-") for c in b1 * 5)[:500] for i in range(10)], "model": "jc69"}),
        ("distance_matrix", "saturated 6 x 400, k2p", {"sequences": {f"u{i}": _seq(rng, 400, dna) for i in range(6)}, "model": "k2p"}),
        ("gc_content", "8000, window 300, IUPAC", {"sequence": _seq(rng, 8000, "ACGTSWNacgt"), "window": 300}),
        ("gc_content", "20000, window 256", {"sequence": _seq(rng, 20000, dna), "window": 256}),
        ("gc_content", "12000, no window", {"sequence": _seq(rng, 12000, dna)}),
        ("hamming_distance", "150000 with N", {"a": _seq(rng, 150000, "ACGTN"), "b": _seq(rng, 150000, "ACGTN")}),
        ("hamming_distance", "lowercase vs uppercase", {"a": a1.lower() * 50, "b": mutated * 50}),
        ("hamming_distance", "unequal lengths (refused)", {"a": _seq(rng, 9000, dna), "b": _seq(rng, 8999, dna)}),
    ]
    return [(f"native.{tool}", label, args) for tool, label, args in cases]


def build(catalog: dict, matrix_rows: list[dict], formula_tools: set[str], inputs: dict,
          groups: set[str]) -> dict:
    """The sequences of calls → {sequences: [{name, calls, no_input}], counts}."""
    from tcmstudio.validate import _example, _invalid

    by_id = {e["id"]: e for e in catalog["entries"]}
    inputs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    by_group: dict[str, list[dict]] = {g: [] for g in GROUPS}
    no_input: list[dict] = []

    def put(rec: dict, args: dict, rid: str, core: bool) -> None:
        if rec["group"] not in groups:
            return
        rec = dict(rec)
        if core:
            rec.update(tool=rid, args=args)
        else:
            rec.update(tool="call_tool", args={"tool": rid, "arguments": args})
        rec["loads_workbook"] = rid in formula_tools
        by_group[rec["group"]].append(rec)

    def add(group: str, rid: str, cls: str, kind: str, item: dict, core: bool) -> None:
        args = _example(item, rid)
        rec = {"key": f"{group}:{rid}", "group": group, "id": rid, "class": cls, "kind": kind,
               "input": "example" if isinstance(item.get("example"), dict) else "probe"}
        if args is None:
            rec["input"] = None
            if group in groups:
                no_input.append({**rec, "extra": rec["key"] in inputs})
            if rec["key"] in inputs:
                put({**rec, "key": f"extra:{rid}", "group": "extra", "input": "sweep_inputs.json"},
                    inputs[rec["key"]], rid, core)
        else:
            put(rec, args, rid, core)
            if group == "entry":
                put({**rec, "key": f"direct:{rid}", "group": "direct"}, args, rid, True)
        if group == "entry" and rid.startswith("native."):
            from bioagent.validation.references import references_for
            for i, ref in enumerate(references_for(rid.split(".", 1)[1])):
                put({**rec, "key": f"reference:{rid}#{i}", "group": "reference", "input": ref.source},
                    json.loads(json.dumps(dict(ref.args))), rid, core)
        if group != "connector":
            bad = _invalid(item.get("parameters") or {})
            if bad is not None:
                put({**rec, "key": f"invalid:{rid}", "group": "invalid", "input": "validate._invalid"}, bad, rid, core)

    for row in matrix_rows:
        cls = row["class"]
        if cls not in BROWSER_CLASSES and cls != CONNECTOR_CLASS:
            continue
        entry = by_id[row["id"]]
        add("connector" if cls == CONNECTOR_CLASS else "entry", row["id"], cls, entry["kind"], entry, False)
    for core in catalog["core"]:
        add("core", core["name"], "core", "core", core, True)
    classes = {r["id"]: r["class"] for r in matrix_rows}
    for i, (rid, label, args) in enumerate(kernel_cases()):
        put({"key": f"kernel:{rid}#{i}", "group": "kernel", "id": rid, "class": classes.get(rid, ""),
             "kind": "native", "input": label}, args, rid, False)
    # group by group, each in the matrix's order; then the workbook's lookups last (a stable sort)
    calls = sorted((c for g in GROUPS for c in by_group[g]), key=lambda c: c["loads_workbook"])

    workbook: list[dict] = []
    if "workbook" in groups:
        for i, (tool, args) in enumerate(WORKBOOK_CALLS):
            if args is None:  # the clinic check of the runner's test intake, with 甘遂半夏汤's herbs
                args = {"intake": inputs["core:clinic_check_prescription"]["intake"],
                        "herbs": [{"herb": "甘遂", "grams": 3}, {"herb": "半夏", "grams": 9},
                                  {"herb": "芍药", "grams": 9}, {"herb": "甘草", "grams": 6}]}
            rid = args["tool"] if tool == "call_tool" else tool
            workbook.append({"key": f"workbook:{rid}#{i}", "group": "workbook", "id": rid, "class": "",
                             "kind": "", "input": "a formula only the workbook holds", "tool": tool,
                             "args": args, "loads_workbook": rid in formula_tools})
    sequences = [{"name": "main", "calls": calls, "no_input": no_input}]
    if workbook:
        sequences.append({"name": "workbook", "calls": workbook, "no_input": []})
    counts = {g: sum(c["group"] == g for s in sequences for c in s["calls"]) for g in GROUPS if g in groups}
    return {"sequences": sequences, "counts": {"calls": counts, "no_input": len(no_input)}}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--site", required=True, help="a built site (build_web.py --dev)")
    p.add_argument("--groups", default=",".join(GROUPS), help=f"comma-separated, of {', '.join(GROUPS)}")
    p.add_argument("--out", default="", help="file to write (default: standard output)")
    a = p.parse_args()
    groups = set(filter(None, a.groups.split(",")))
    unknown = groups - set(GROUPS)
    if unknown:
        p.error(f"unknown group(s): {', '.join(sorted(unknown))}")
    catalog = json.loads((Path(a.site) / "runtime" / "catalog.json").read_text(encoding="utf-8"))
    with MATRIX.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    doc = build(catalog, rows, worker_formula_tools(), inputs, groups)
    text = json.dumps(doc, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    print(json.dumps(doc["counts"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
