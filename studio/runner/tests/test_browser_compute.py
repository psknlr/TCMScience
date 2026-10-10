"""Local compute in the browser produces the same governed results as the original Python.

Counts prepared by WebGPU are used only for the input they were computed from; a packet that
does not belong to the call, or does not check out, is not used and the call still succeeds
with the original Python. The CPU kernels are the real JavaScript ones (studio/web/js/compute),
run through a Node process that speaks the Worker's JSON boundary.
"""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from tcmstudio import browser_compute

BRIDGE = Path(__file__).resolve().parents[2] / "web" / "js" / "compute" / "bridge.js"
CTX = json.dumps({"where": "browser"})


def run(args, packet=None, kernels=True):
    return json.loads(browser_compute.call_json("call_tool", json.dumps(args, ensure_ascii=False), CTX,
                                                json.dumps(packet), json.dumps({"kernels": kernels})))


def packet(tool, sequences, counts, window=0, digest=None):
    """A packet as the Worker prepares it, bound to `sequences` (already normalised)."""
    return {"tool": tool, "width": len(sequences[0]), "n": len(sequences), "window": window, "counts": counts,
            "backend": "webgpu", "input_sha256": digest or browser_compute.input_digest(tool, sequences, window)}


# ------------------------------------------------------------------------------------------- prepared GPU counts

DIST = {"tool": "native.distance_matrix", "arguments": {
    "sequences": {"one": "ACGTNN", "two": "AGGT--", "three": "GGTTAA"}, "model": None}}
DIST_SEQS = ["ACGTNN", "AGGT--", "GGTTAA"]
DIST_COUNTS = [0] * 36
DIST_COUNTS[4:8] = [4, 1, 0, 0]
DIST_COUNTS[8:12] = [4, 3, 2, 0]
DIST_COUNTS[20:24] = [4, 2, 1, 0]


@pytest.mark.parametrize("model", ["p", "jc69", "k2p"])
def test_bound_gpu_counts_give_the_original_result_and_a_webgpu_receipt(model):
    args = json.loads(json.dumps(DIST))
    args["arguments"]["model"] = model
    cpu = run(args, kernels=False)
    gpu = run(args, packet("native.distance_matrix", DIST_SEQS, DIST_COUNTS))
    assert cpu["status"] == gpu["status"] == "succeeded"
    assert cpu["result"] == gpu["result"]
    assert cpu["receipt"]["output_sha256"] == gpu["receipt"]["output_sha256"]
    assert cpu["receipt"]["kernel"] == gpu["receipt"]["kernel"] == "bioagent.policy"
    assert gpu["receipt"]["device"] == "webgpu", "CONTRACTS §3: webgpu for a JS-side GPU path"
    assert gpu["receipt"]["compute"]["backend"] == "webgpu"
    assert run(args, kernels=False)["receipt"]["device"] == "cpu", "call-scoped counts must not leak"


def test_gc_windows_include_s_ambiguity_and_match_python():
    args = {"tool": "native.gc_content", "arguments": {"sequence": "GCASN", "window": 2}}
    counts = [3, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    gpu = run(args, packet("native.gc_content", ["GCASN"], counts, 2))
    assert gpu["result"] == run(args, kernels=False)["result"]
    assert gpu["receipt"]["compute"]["backend"] == "webgpu"


def test_hamming_keeps_u_and_ambiguity_distinct():
    args = {"tool": "native.hamming_distance", "arguments": {"a": "a N u", "b": "ACT"}}
    gpu = run(args, packet("native.hamming_distance", ["ANU", "ACT"], [2, 0, 0, 0]))
    assert gpu["result"] == run(args, kernels=False)["result"] == {"distance": 2, "identity": 0.333333, "length": 3}
    assert gpu["receipt"]["compute"]["backend"] == "webgpu"


@pytest.mark.parametrize("why, bad", [
    ("computed from a different input", lambda: packet("native.distance_matrix", ["A", "G"], [0] * 16,
                                                      digest=browser_compute.input_digest("native.distance_matrix", ["A", "C"]))),
    ("no digest at all", lambda: {**packet("native.distance_matrix", ["A", "G"], [0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
                                  "input_sha256": None}),
    ("all-zero counts: no comparable sites", lambda: packet("native.distance_matrix", ["A", "G"], [0] * 16)),
    ("transitions above differences", lambda: packet("native.distance_matrix", ["A", "G"], [0, 0, 0, 0, 1, 0, 1, 0] + [0] * 8)),
    ("the wrong shape", lambda: packet("native.distance_matrix", ["A", "G"], [1, 1, 1, 0])),
    ("a float where a count goes", lambda: packet("native.distance_matrix", ["A", "G"], [0, 0, 0, 0, 1.0, 1, 1, 0] + [0] * 8)),
])
def test_a_packet_that_does_not_belong_or_check_out_is_not_used_and_the_call_still_succeeds(why, bad):
    args = {"tool": "native.distance_matrix", "arguments": {"sequences": ["A", "G"]}}
    reference = run(args, kernels=False)
    assert reference["status"] == "succeeded"
    got = run(args, bad())
    assert got["status"] == "succeeded", why
    assert got["result"] == reference["result"], why
    assert got["receipt"]["device"] == "cpu", why
    assert got["receipt"]["compute"]["backend"] == "pyodide", why
    assert got["receipt"]["compute"]["gpu_rejected"], why


def test_well_formed_but_wrong_counts_with_the_right_digest_are_caught_by_python_s_own_recount():
    # 0 ≤ 0 ≤ 0 passes the bounds and the digest is right: only the values are wrong (a broken GPU). Python recounts a
    # few records itself before using any packet, so the call does not report "no comparable sites" for A/G.
    args = {"tool": "native.distance_matrix", "arguments": {"sequences": ["A", "G", "A"]}}
    zeros = packet("native.distance_matrix", ["A", "G", "A"], [0] * 36)
    got = run(args, zeros)
    assert got["status"] == "succeeded"
    assert got["result"] == run(args, kernels=False)["result"]
    assert "pair" in got["receipt"]["compute"]["gpu_rejected"]
    gc = {"tool": "native.gc_content", "arguments": {"sequence": "GCASN", "window": 2}}
    off_by_one = [3, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0]   # the last window recounted wrong
    got = run(gc, packet("native.gc_content", ["GCASN"], off_by_one, 2))
    assert got["result"] == run(gc, kernels=False)["result"]
    assert "window" in got["receipt"]["compute"]["gpu_rejected"]


def test_invalid_arguments_fail_as_before_with_or_without_a_packet():
    args = {"tool": "native.distance_matrix", "arguments": {"misspelling": ["A", "G"]}}
    invalid = run(args, packet("native.distance_matrix", ["A", "G"], [0] * 16))
    assert invalid["error"]["type"] == "bad_arguments"
    assert invalid["receipt"]["device"] == "cpu"


# ------------------------------------------------------------------------------------------- the real JS kernels

class NodeKernels:
    """studio/web/js/compute/bridge.js in a Node process: call(name, payload_json) -> json, as in the Worker."""

    SCRIPT = (
        "import { pythonKernels } from %s;\n"
        "import readline from 'node:readline';\n"
        "const k = pythonKernels();\n"
        "readline.createInterface({ input: process.stdin }).on('line', (line) => {\n"
        "  const { name, payload } = JSON.parse(line);\n"
        "  process.stdout.write(JSON.stringify(k.call(name, payload)) + '\\n');\n"
        "});\n")

    def __init__(self):
        self.proc = subprocess.Popen(["node", "--input-type=module", "-e", self.SCRIPT % json.dumps(BRIDGE.as_uri())],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")
        self.calls = []

    def call(self, name, payload):
        self.calls.append(name)
        self.proc.stdin.write(json.dumps({"name": name, "payload": payload}, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


@pytest.fixture
def js():
    if shutil.which("node") is None:
        pytest.skip("node is not installed: the JavaScript kernels cannot be run here")
    k = NodeKernels()
    browser_compute.install_js_kernels(k)
    try:
        yield k
    finally:
        browser_compute.install_js_kernels(None)
        k.close()


def both(args):
    """The call with the JavaScript kernels and with the original Python only."""
    return run(args, kernels=True), run(args, kernels=False)


def same(fast, slow):
    assert fast["status"] == slow["status"]
    assert fast["result"] == slow["result"]
    assert json.dumps(fast["result"], ensure_ascii=False) == json.dumps(slow["result"], ensure_ascii=False), \
        "identical as JSON too: an int stays an int, a float a float, -0.0 keeps its sign"
    assert fast["receipt"]["output_sha256"] == slow["receipt"]["output_sha256"]


def dna(rng, n, alphabet="ACGT"):
    return "".join(rng.choice(alphabet) for _ in range(n))


def test_alignment_kernels_match_python_on_random_inputs(js):
    rng = random.Random(7)
    params = [{}, {"match": 1, "mismatch": -1, "gap": -1}, {"match": 2, "mismatch": -1.0, "gap": -2},
              {"match": 1.0, "mismatch": -1.0, "gap": -0.0}, {"match": 0.5, "mismatch": -0.25, "gap": -0.75}]
    used = 0
    for trial in range(60):
        kind = "native.global_alignment" if trial % 2 else "native.local_alignment"
        a, b = dna(rng, rng.randint(40, 160), "ACGT" if trial % 3 else "AC"), dna(rng, rng.randint(40, 160))
        fast, slow = both({"tool": kind, "arguments": {"a": a, "b": b, **params[trial % len(params)]}})
        same(fast, slow)
        used += fast["receipt"]["compute"]["backend"] == "js"
        assert slow["receipt"]["compute"]["backend"] == "pyodide"
    assert used == 60, "every one of these is large enough for the kernel"


def test_protein_alignment_with_blosum62(js):
    rng = random.Random(11)
    aa = "ARNDCQEGHILKMFPSTWYV"
    for mode in ("global", "local"):
        for _ in range(8):
            a = "".join(rng.choice(aa) for _ in range(rng.randint(60, 200)))
            b = "".join(rng.choice(aa) for _ in range(rng.randint(60, 200)))
            fast, slow = both({"tool": "native.protein_alignment", "arguments": {"a": a, "b": b, "mode": mode}})
            same(fast, slow)
            assert fast["receipt"]["compute"]["backend"] == "js"


def test_a_matrix_without_a_pair_raises_the_original_error(js):
    fast, slow = both({"tool": "native.protein_alignment", "arguments": {"a": "ACDE" * 30, "b": "ACDJ" * 30}})
    assert fast["status"] == slow["status"] == "failed"
    assert fast["error"] == slow["error"]


def test_edit_distance_kernel_matches_python_including_astral_cjk(js):
    rng = random.Random(3)
    cases = [("葛根芩连汤" * 20, "葛根黄芩黄连汤" * 20), ("𠀀𠀁𠀂" * 30, "𠀁𠀀𠀂" * 30)]
    cases += [(dna(rng, rng.randint(50, 300), "ACGTN"), dna(rng, rng.randint(50, 300), "ACGTN")) for _ in range(20)]
    for a, b in cases:
        fast, slow = both({"tool": "native.edit_distance", "arguments": {"a": a, "b": b}})
        same(fast, slow)
        assert fast["receipt"]["compute"]["backend"] == "js"


def test_count_kernels_match_python(js):
    rng = random.Random(5)
    for _ in range(6):
        width = rng.randint(60, 400)
        seqs = {f"s{i}": dna(rng, width, "ACGTN-RY") for i in range(rng.randint(3, 12))}
        for model in ("p", "jc69", "k2p"):
            fast, slow = both({"tool": "native.distance_matrix", "arguments": {"sequences": seqs, "model": model}})
            same(fast, slow)
    a, b = dna(rng, 9000, "ACGTUNRY"), dna(rng, 9000, "ACGTUNRY")
    fast, slow = both({"tool": "native.hamming_distance", "arguments": {"a": a, "b": b}})
    same(fast, slow)
    assert fast["receipt"]["compute"]["backend"] == "js"
    s = dna(rng, 3000, "ACGTSWN")
    fast, slow = both({"tool": "native.gc_content", "arguments": {"sequence": s, "window": 300}})
    same(fast, slow)
    assert fast["receipt"]["compute"]["backend"] == "js"
    fast, slow = both({"tool": "native.gc_content", "arguments": {"sequence": s, "window": 50}})
    same(fast, slow)
    assert fast["receipt"]["compute"]["backend"] == "pyodide", "short windows: Python's str.count is as fast"


def test_small_inputs_stay_in_python(js):
    fast, slow = both({"tool": "native.global_alignment", "arguments": {"a": "GATTACA", "b": "GCATGCU"}})
    same(fast, slow)
    assert fast["receipt"]["compute"]["backend"] == "pyodide"
    assert js.calls == []


def test_a_wrong_kernel_answer_is_caught_and_the_original_runs(js):
    real = js.call

    def lying(name, payload):
        out = json.loads(real(name, payload))
        if name == "align.nw_linear":
            out["score_bits"] = "4059000000000000"                 # 100.0
        if name == "sequence.levenshtein":
            out["distance"] = -1
        if name == "sequence.counts":
            out = out[:-1]                                        # the wrong shape
        return json.dumps(out)

    js.call = lying
    rng = random.Random(9)
    for args in ({"tool": "native.global_alignment", "arguments": {"a": dna(rng, 80), "b": dna(rng, 90)}},
                 {"tool": "native.edit_distance", "arguments": {"a": dna(rng, 80), "b": dna(rng, 90)}},
                 {"tool": "native.hamming_distance", "arguments": {"a": dna(rng, 9000), "b": dna(rng, 9000)}}):
        fast, slow = both(args)
        same(fast, slow)
        compute = fast["receipt"]["compute"]
        assert compute["backend"] == "pyodide"
        assert [k["outcome"] for k in compute["kernels"]] == ["rejected"]


def test_a_kernel_that_breaks_declines_and_the_original_runs(js):
    js.call = lambda name, payload: "this is not JSON"
    rng = random.Random(13)
    fast, slow = both({"tool": "native.local_alignment", "arguments": {"a": dna(rng, 80), "b": dna(rng, 90)}})
    same(fast, slow)
    assert fast["receipt"]["compute"]["backend"] == "pyodide"


def test_kernels_off_means_none_runs(js):
    rng = random.Random(17)
    got = run({"tool": "native.global_alignment", "arguments": {"a": dna(rng, 80), "b": dna(rng, 90)}}, kernels=False)
    assert got["receipt"]["compute"]["backend"] == "pyodide"
    assert js.calls == []
