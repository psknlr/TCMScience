"""The browser kernels' parity fixtures stay what the Python implementation computes.

studio/web/test/fixtures/kernel_cases.json is what web/test/kernels.test.mjs holds the
JavaScript kernels to. If the Python implementation changes, these fail until the fixtures are
regenerated (make_kernel_fixtures.py), and the JavaScript kernels are then held to the new
answers."""
import importlib.util
import json
from pathlib import Path

import pytest

from bioagent.tools import _compute, phylo, sequence

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "test" / "fixtures"


def generator():
    spec = importlib.util.spec_from_file_location("make_kernel_fixtures", FIXTURES / "make_kernel_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_committed_fixtures_are_what_python_computes_now():
    committed = json.loads((FIXTURES / "kernel_cases.json").read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(generator().build(), ensure_ascii=False, sort_keys=True))
    assert committed == fresh, "regenerate: python3 studio/web/test/fixtures/make_kernel_fixtures.py"


def count_cases():
    cases = json.loads((FIXTURES / "kernel_cases.json").read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if c["kernel"] == "sequence.counts"]


@pytest.mark.parametrize("case", count_cases(), ids=lambda c: json.loads(c["payload_json"])["tool"])
def test_the_count_expectations_give_the_tools_their_own_results(case):
    """The fixture's counts, given to the real tool as a kernel's answer, reproduce its original output."""
    p = json.loads(case["payload_json"])
    tool, seqs, window = p["tool"], p["sequences"], p["window"]
    call = {
        "native.distance_matrix": lambda: phylo.distance_matrix(seqs),
        "native.hamming_distance": lambda: sequence.hamming_distance(*seqs),
        "native.gc_content": lambda: sequence.gc_content(seqs[0], window),
    }[tool]
    try:
        reference = call()
    except ValueError as exc:                     # e.g. no comparable sites: the same error either way
        reference = f"error: {exc}"
    token = _compute.KERNELS.set({"sequence.counts": lambda t, s, w: case["expect"]})
    try:
        try:
            with_counts = call()
        except ValueError as exc:
            with_counts = f"error: {exc}"
    finally:
        _compute.KERNELS.reset(token)
    assert with_counts == reference
