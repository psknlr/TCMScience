"""tcmstudio.browser_selftest: the browser research jobs' known answer.

The committed answer (data/browser_selftest.json) is what native CPython writes for the planted
world; a browser job worker must write the same (studio/e2e/science.spec.mjs). Here: the answer is
current (studying the world now gives exactly the committed tables, so a change to the analysis
that changes them must re-record it), and the comparison says what it found without smoothing a
difference over.
"""
from __future__ import annotations

import json

from tcmstudio import browser_selftest as st


def test_the_committed_answer_is_what_native_cpython_writes_now(tmp_path):
    report = st.run({"work_dir": str(tmp_path)})
    assert report["status"] == "PASS", json.dumps(report["tables"], indent=1)
    assert report["exit"] in (0, 1) and report["verify"]["status"] == "PASS"
    # natively the bytes are the same; within tolerance would mean the answer was recorded elsewhere
    assert {t["status"] for t in report["tables"].values()} == {"identical"}, (
        "re-record it: python -m tcmstudio.browser_selftest --write")
    assert report["digests"] == report["expected_digests"]


def test_the_known_answer_exercises_the_permutation_null_and_a_release():
    doc = json.loads(st.EXPECTED.read_text(encoding="utf-8"))
    assert doc["schema"] == st.SCHEMA and doc["scenario"] == "selective"
    assert doc["permutations"] == st.PERMUTATIONS
    rows = [line.split("\t") for line in doc["tables"]["enrichment.tsv"].splitlines()]
    head, body = rows[0], rows[1:]
    planted = next(dict(zip(head, r)) for r in body if r[head.index("pathway")] == "reactome:R-HSA-A")
    # the planted pathway went through the degree-matched null: an empirical p was drawn for it
    assert planted["empirical_p"] and float(planted["empirical_p"]) <= 0.05
    assert float(planted["q_value"]) <= 0.05
    assert set(doc["tables"]) == set(st.TABLES)


def test_the_comparison_names_what_differs():
    want = {name: "a\tb\n1\t0.5\n" for name in st.TABLES}
    same = st.compare(dict(want), want)
    assert {t["status"] for t in same.values()} == {"identical"}
    near = dict(want, **{"enrichment.tsv": "a\tb\n1\t0.5000000000000001\n"})
    assert st.compare(near, want)["enrichment.tsv"]["status"] == "within_tolerance"
    far = dict(want, **{"enrichment.tsv": "a\tb\n1\t0.51\n"})
    out = st.compare(far, want)["enrichment.tsv"]
    assert out["status"] == "different" and out["first"][0] == {"row": 1, "column": 1, "got": "0.51", "want": "0.5"}
    text = dict(want, **{"targets.tsv": "a\tb\n2\t0.5\n"})
    assert st.compare(text, want)["targets.tsv"]["status"] == "different"
    missing = {k: v for k, v in want.items() if k != "network.tsv"}
    assert st.compare(missing, want)["network.tsv"]["status"] == "missing"
