"""Local compute in the browser, consumed inside ordinary validated, policy-kernel calls.

Two kinds of help reach Python from the Worker. Neither is a context capability or a public
tool, and both are optional: without them the original Python computes.

Counts prepared by WebGPU before the call (a GPU answers asynchronously)
    A packet of integer counts. It is used only when it belongs to this call: the SHA-256
    of the input it was computed from must equal that of the call's own normalised input,
    and its shape, bounds and consistency must check out. A packet that fails any of these
    is not used; the original Python computes and the receipt says why. The call never
    fails because of a packet.

CPU kernels the tools call synchronously (JavaScript in the Worker)
    ``bioagent.tools._compute.KERNELS``: the tools hand their own normalised input to the
    kernel and check its result before using it (bioagent.tools.align, .sequence). Small
    inputs stay in Python, where crossing into JavaScript would cost more than it saves.

``receipt.compute`` names the backend that produced the numbers and what each kernel did;
``receipt.device`` is ``webgpu`` only when GPU counts were used (CONTRACTS §3). Python keeps
every model transform, rounding, error and verdict.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import time
from typing import Any

from bioagent.tools._compute import COUNTS_SOURCE, KERNEL_EVENTS, KERNELS, note

_TOOLS = {"native.distance_matrix": "distance_matrix",
          "native.hamming_distance": "hamming_distance", "native.gc_content": "gc_content"}
_TOOL_OF = {name: tool for tool, name in _TOOLS.items()}
_KERNEL_TOOLS = {"native.global_alignment": "align.nw_linear", "native.local_alignment": "align.sw_linear",
                 "native.protein_alignment": "align.*", "native.edit_distance": "sequence.levenshtein",
                 **{t: "sequence.counts" for t in _TOOLS}}

# Below these sizes the original Python takes about as long as the call into JavaScript.
KERNEL_MIN_OPERATIONS = 4096
KERNEL_MIN_CELLS = 2048
GC_MIN_WINDOW = 256   # measured in Chromium: a 60-wide window was slower with the kernel than without

GPU_PRECISION = "uint32 exact counts; Python float64 transforms"
CPU_PRECISION = {
    "sequence.counts": "exact integer counts (JavaScript, CPU); Python float64 transforms",
    "align.nw_linear": "the dynamic programme in IEEE-754 doubles and exact integers, as Python computes it; "
                       "alignment and score checked in Python",
    "align.sw_linear": "the dynamic programme in IEEE-754 doubles and exact integers, as Python computes it; "
                       "alignment and score checked in Python",
    "sequence.levenshtein": "exact integers (JavaScript, CPU)",
}
_SURROGATE = re.compile("[\ud800-\udfff]")

_js: Any = None   # the Worker's kernels (studio/web/js/compute/bridge.js), set once at boot


def install_js_kernels(js: Any) -> None:
    """Called once by the Worker with ``pythonKernels()``: ``js.call(name, payload_json) -> json``."""
    global _js
    _js = js


def input_digest(tool: str, sequences, window: int = 0) -> str:
    """The SHA-256 a prepared packet carries: the normalised sequences, one per line, then
    the tool and the window. The Worker computes the same over its own normalisation;
    when the two disagree the packet is not used."""
    text = "\n".join(sequences) + "\x00" + tool + "\x00" + str(window)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def workload(name: str, sequences, window: int = 0) -> int:
    """Elementary comparisons the original Python makes (what a kernel saves)."""
    width, n = len(sequences[0]), len(sequences)
    if name == "distance_matrix":
        return n * (n - 1) // 2 * width
    if name == "gc_content":
        return (width - window + 1) * window if window > 0 else 0   # str.count is already C
    return width


def _records(name: str, sequences, window: int) -> int:
    width, n = len(sequences[0]), len(sequences)
    return n * n if name == "distance_matrix" else width - window + 2 if name == "gc_content" and window > 0 else 1


def worth_a_kernel(name: str, sequences, window: int = 0) -> bool:
    """Enough work for the kernel to save time: every count handed back costs about as much
    as sixteen of the comparisons it saves (JSON, then the check below)."""
    ops = workload(name, sequences, window)
    if name == "gc_content" and window < GC_MIN_WINDOW:
        return False        # Python counts each window with str.count, already C: short windows gain nothing
    return ops >= KERNEL_MIN_OPERATIONS and ops >= 16 * _records(name, sequences, window)


def check_counts(name: str, counts: Any, sequences, window: int) -> str | None:
    """Why counts cannot be used for this input, or None when they can."""
    width = len(sequences[0])
    records = _records(name, sequences, window)
    if not isinstance(counts, list) or len(counts) != records * 4:
        return "the count buffer has the wrong shape for this input"
    if counts and (set(map(type, counts)) != {int} or min(counts) < 0 or max(counts) > width):
        return "counts must be integers between 0 and the sequence length"
    if name == "distance_matrix":
        n = len(sequences)
        for i in range(n):
            for j in range(i + 1, n):
                at = (i * n + j) * 4
                if not counts[at + 2] <= counts[at + 1] <= counts[at]:
                    return "transition, difference and site counts are inconsistent"
    elif name == "gc_content" and window > 0 and any(counts[r * 4] > window for r in range(1, records)):
        return "a window count exceeds the window width"
    return _sample_check(name, counts, sequences, window)


_TRANSITIONS = {("A", "G"), ("G", "A"), ("C", "T"), ("T", "C")}


def _sample_check(name: str, counts: list, sequences, window: int) -> str | None:
    """A few records recounted here, the way the original Python counts them: a GPU or a
    kernel that returns well-formed but wrong counts is caught without redoing the work."""
    n = len(sequences)
    if name == "distance_matrix":
        for i, j in {(0, 1), (n - 2, n - 1)}:
            sites = diffs = ts = 0
            for a, b in zip(sequences[i], sequences[j]):
                if a in "ACGT" and b in "ACGT":
                    sites += 1
                    if a != b:
                        diffs += 1
                        ts += (a, b) in _TRANSITIONS
            at = (i * n + j) * 4
            if counts[at:at + 3] != [sites, diffs, ts]:
                return f"the counts of pair {i},{j} differ from Python's"
    elif name == "gc_content":
        seq = sequences[0]
        if counts[0] != sum(seq.count(c) for c in "GCS"):
            return "the total GC count differs from Python's"
        if window > 0:
            for r in {1, len(seq) - window + 1}:
                if counts[r * 4] != sum(seq[r - 1:r - 1 + window].count(c) for c in "GCS"):
                    return f"the GC count of window {r} differs from Python's"
    return None


class _Call:
    """What happened to local compute during one call, for its receipt."""

    def __init__(self, packet: Any, kernels_on: bool):
        self.packet = packet if isinstance(packet, dict) else None
        self.kernels_on = kernels_on and _js is not None
        self.gpu_used = False
        self.gpu_rejected: str | None = None
        self.timings: dict[str, float] = {}
        self.events: list[dict[str, Any]] = []

    # -- counts prepared by WebGPU

    def source(self, name, sequences, window):
        p = self.packet
        if p is None or p.get("tool") != _TOOL_OF.get(name):
            return None
        reason = None
        if p.get("input_sha256") != input_digest(p["tool"], sequences, window):
            reason = "the prepared counts were computed from a different input"
        elif p.get("n") != len(sequences) or p.get("width") != len(sequences[0]) or p.get("window", 0) != window:
            reason = "the prepared counts have other dimensions than this input"
        else:
            reason = check_counts(name, p.get("counts"), sequences, window)
        if reason:
            self.gpu_rejected = reason
            return None
        self.gpu_used = True
        return p["counts"]

    # -- synchronous CPU kernels

    def _js(self, name, payload):
        t0 = time.perf_counter()
        try:
            out = json.loads(_js.call(name, json.dumps(payload, ensure_ascii=False)))
        except Exception:                          # noqa: BLE001 - a failing kernel declines; Python runs
            out = None
        self.timings[name] = self.timings.get(name, 0.0) + (time.perf_counter() - t0) * 1000
        return out

    def counts(self, name, sequences, window):
        tool = _TOOL_OF.get(name)
        if tool is None or not worth_a_kernel(name, sequences, window):
            return None
        if not all(s.isascii() for s in sequences):
            note("sequence.counts", "declined", "sequence counts take ASCII sequences")
            return None
        counts = self._js("sequence.counts", {"tool": tool, "sequences": list(sequences), "window": window})
        if counts is None:
            note("sequence.counts", "declined")
            return None
        reason = check_counts(name, counts, sequences, window)
        note("sequence.counts", "rejected" if reason else "used", reason or "")
        return None if reason else counts

    def aligner(self, name):
        def run(x, y, alpha_x, alpha_y, table, gap):
            if len(x) * len(y) < KERNEL_MIN_CELLS or _SURROGATE.search(x) or _SURROGATE.search(y):
                return None
            r = self._js(name, {"x": x, "y": y, "alpha_x": alpha_x, "alpha_y": alpha_y,
                                "table": table, "table_int": [type(v) is int for v in table],
                                "gap": gap, "gap_int": type(gap) is int})
            if not isinstance(r, dict):
                return None
            try:
                score = struct.unpack(">d", bytes.fromhex(r["score_bits"]))[0]
                score = int(score) if r["score_int"] is True else score
                found = (r["aligned_x"], r["aligned_y"], score)
                if name == "align.sw_linear":
                    found += (r["start_a"], r["end_a"], r["start_b"], r["end_b"])
            except (KeyError, TypeError, ValueError, struct.error):
                return None
            return found
        return run

    def levenshtein(self, x, y):
        if len(x) * len(y) < KERNEL_MIN_CELLS or _SURROGATE.search(x) or _SURROGATE.search(y):
            return None
        r = self._js("sequence.levenshtein", {"x": x, "y": y})
        d = r.get("distance") if isinstance(r, dict) else None
        return d if type(d) is int else None

    def kernels(self):
        if not self.kernels_on:
            return None
        return {"sequence.counts": self.counts, "align.nw_linear": self.aligner("align.nw_linear"),
                "align.sw_linear": self.aligner("align.sw_linear"), "sequence.levenshtein": self.levenshtein}

    # -- the receipt

    def compute(self, tool):
        used = [e["kernel"] for e in self.events if e["outcome"] == "used"]
        if self.gpu_used:
            out = {"backend": "webgpu", "operation": self.packet["tool"], "precision": GPU_PRECISION}
        elif used:
            out = {"backend": "js", "operation": tool, "precision": CPU_PRECISION.get(used[0], "")}
        elif tool in _KERNEL_TOOLS:
            out = {"backend": "pyodide", "operation": tool, "precision": "Python float64"}
        else:
            return None
        if self.events:
            out["kernels"] = [{**e, **({"ms": round(self.timings[e["kernel"]], 3)} if e["kernel"] in self.timings else {})}
                              for e in self.events]
        if self.gpu_rejected:
            out["gpu_rejected"] = self.gpu_rejected
        return out


def call_json(tool, arguments_json, context_json, acceleration_json="null", options_json="{}"):
    """dispatch.call_json with the Worker's local compute available to this one call.

    ``acceleration_json``: the WebGPU packet prepared for it (or null). ``options_json``:
    ``{"kernels": bool}``, whether the CPU kernels may run (off when the page asked for
    the Python reference)."""
    from . import dispatch
    started = time.perf_counter()
    try:
        options = json.loads(options_json or "{}")
    except ValueError:
        options = {}
    call = _Call(json.loads(acceleration_json or "null"), bool(options.get("kernels", True)))
    tokens = (COUNTS_SOURCE.set(call.source if call.packet else None), KERNELS.set(call.kernels()),
              KERNEL_EVENTS.set(call.events))
    try:
        text = dispatch.call_json(tool, arguments_json, context_json)
    finally:
        for var, token in zip((COUNTS_SOURCE, KERNELS, KERNEL_EVENTS), tokens):
            var.reset(token)
    try:
        target = json.loads(arguments_json).get("tool") if tool == "call_tool" else tool
    except (ValueError, AttributeError):
        target = tool
    target = target if isinstance(target, str) else tool
    if not target.startswith("native."):
        target = f"native.{target}" if f"native.{target}" in _KERNEL_TOOLS else target
    compute = call.compute(target)
    if compute is None:
        return text
    envelope = json.loads(text)
    receipt = envelope.setdefault("receipt", {})
    if envelope.get("status") == "succeeded":
        if call.gpu_used:
            receipt["device"] = "webgpu"
        receipt["compute"] = compute
    elif call.gpu_used or any(e["outcome"] == "used" for e in call.events):
        # a failed call earns no accelerator receipt, whatever helped along the way
        receipt["compute"] = {"backend": "pyodide", "operation": target, "precision": "Python float64"}
    envelope["duration_ms"] = round((time.perf_counter() - started) * 1000)
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
