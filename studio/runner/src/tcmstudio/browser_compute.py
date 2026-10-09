"""Consume trusted Worker GPU counts inside ordinary validated, policy-kernel calls.

This bridge is not a context capability or public tool: only the Worker passes its
prepared integer buffer. The receipt names WebGPU only if the native function actually
consumed the buffer and its dispatch succeeded. Python retains all model transforms,
rounding, error handling and governance.
"""
from __future__ import annotations

import json
import time

from bioagent.tools._compute import COUNTS_SOURCE

_TOOLS = {"native.distance_matrix": "distance_matrix",
          "native.hamming_distance": "hamming_distance", "native.gc_content": "gc_content"}


def call_json(tool, arguments_json, context_json, acceleration_json="null"):
    from . import dispatch
    started = time.perf_counter()
    packet = json.loads(acceleration_json)
    used = False

    def source(name, sequences, window):
        nonlocal used
        if not isinstance(packet, dict) or _TOOLS.get(packet.get("tool")) != name:
            return None
        width, n = packet.get("width"), packet.get("n")
        if n != len(sequences) or any(len(seq) != width for seq in sequences):
            raise ValueError("prepared GPU sequence dimensions do not match the validated input")
        if window != packet.get("window", 0):
            raise ValueError("prepared GPU window does not match the validated input")
        records = n * n if name == "distance_matrix" else width - window + 2 \
            if name == "gc_content" and window > 0 else 1
        counts = packet.get("counts")
        if not isinstance(counts, list) or len(counts) != records * 4:
            raise ValueError("prepared GPU count buffer has an invalid shape")
        if any(type(count) is not int or not 0 <= count <= width for count in counts):
            raise ValueError("prepared GPU counts must be bounded non-negative integers")
        if name == "distance_matrix":
            for i in range(n):
                for j in range(i + 1, n):
                    at = (i * n + j) * 4
                    if not counts[at + 2] <= counts[at + 1] <= counts[at]:
                        raise ValueError("prepared transition/mismatch/site counts are inconsistent")
        elif name == "gc_content" and window > 0 and any(counts[r * 4] > window for r in range(1, records)):
            raise ValueError("prepared GPU window count exceeds the window width")
        used = True
        return counts

    token = COUNTS_SOURCE.set(source if packet else None)
    try:
        text = dispatch.call_json(tool, arguments_json, context_json)
    finally:
        COUNTS_SOURCE.reset(token)
    if not packet or not used:
        return text
    envelope = json.loads(text)
    if envelope.get("status") == "succeeded":
        receipt = envelope.setdefault("receipt", {})
        receipt["device"] = "gpu"
        receipt["compute"] = {"backend": "webgpu", "operation": packet["tool"],
                              "precision": "uint32 exact counts; Python float64 transforms"}
        envelope["duration_ms"] = round((time.perf_counter() - started) * 1000)
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
