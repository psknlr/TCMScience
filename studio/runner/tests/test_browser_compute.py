"""GPU exact integer counts produce the same governed results as original Python."""
import json

import pytest

from tcmstudio import browser_compute, dispatch


def run(args, packet=None):
    return json.loads(browser_compute.call_json("call_tool", json.dumps(args),
                      json.dumps({"where": "browser"}), json.dumps(packet)))


def packet(tool, sequences, counts, window=0):
    return {"tool": tool, "width": len(sequences[0]), "n": len(sequences),
            "window": window, "counts": counts, "backend": "webgpu"}


@pytest.mark.parametrize("model", ["p", "jc69", "k2p"])
def test_distance_counts_match_original_python_and_policy_receipt(model):
    args = {"tool": "native.distance_matrix", "arguments": {
        "sequences": {"one": "ACGTNN", "two": "AGGT--", "three": "GGTTAA"}, "model": model}}
    counts = [0] * 36
    counts[4:8] = [4, 1, 0, 0]
    counts[8:12] = [4, 3, 2, 0]
    counts[20:24] = [4, 2, 1, 0]
    cpu = run(args)
    gpu = run(args, packet("native.distance_matrix", ["ACGTNN"] * 3, counts))
    assert cpu["status"] == gpu["status"] == "succeeded"
    assert cpu["result"] == gpu["result"]
    assert cpu["receipt"]["output_sha256"] == gpu["receipt"]["output_sha256"]
    assert cpu["receipt"]["kernel"] == gpu["receipt"]["kernel"] == "bioagent.policy"
    assert gpu["receipt"]["device"] == "gpu"
    assert gpu["receipt"]["compute"]["backend"] == "webgpu"
    assert run(args)["receipt"]["device"] == "cpu", "call-scoped counts must not leak"


def test_gc_windows_include_s_ambiguity_and_match_python():
    args = {"tool": "native.gc_content", "arguments": {"sequence": "GCASN", "window": 2}}
    counts = [3, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
    assert run(args)["result"] == run(args, packet("native.gc_content", ["GCASN"], counts, 2))["result"]


def test_hamming_keeps_u_and_ambiguity_distinct():
    args = {"tool": "native.hamming_distance", "arguments": {"a": "a N u", "b": "ACT"}}
    gpu = run(args, packet("native.hamming_distance", ["ANU", "ACT"], [2, 0, 0, 0]))
    assert gpu["result"] == run(args)["result"] == {"distance": 2, "identity": 0.333333, "length": 3}


def test_invalid_arguments_and_bad_counts_do_not_earn_gpu_receipt():
    args = {"tool": "native.distance_matrix", "arguments": {"sequences": ["A", "G"]}}
    gpu = run(args, packet("native.distance_matrix", ["A", "G"], [0] * 16))
    assert gpu["status"] == "failed"
    assert gpu["receipt"]["device"] == "cpu"
    broken = [0] * 16
    broken[4:8] = [1, 0, 1, 0]
    assert run(args, packet("native.distance_matrix", ["A", "G"], broken))["status"] == "failed"
    assert run(args)["status"] == "succeeded"
    args["arguments"] = {"misspelling": ["A", "G"]}
    invalid = run(args, packet("native.distance_matrix", ["A", "G"], broken))
    assert invalid["error"]["type"] == "bad_arguments"
    assert invalid["receipt"]["device"] == "cpu"
