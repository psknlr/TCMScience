"""Accelerators: a browser call hands one hot loop to the page and then finishes in Python
(docs/V2.md §13.4).

Python in the browser worker runs synchronously, so it cannot wait for a WebGPU or a JS
worker computation. A tool that can use a kernel therefore raises ``Accelerate`` when the
call's context says the page has that kernel (``context.accel.kernels``) and carries no
result for it yet. ``dispatch.call`` turns that into a marker, ``{"__accelerate__": {token,
kernel, input}}``, instead of an envelope; the worker runs the kernel on the page and calls
``resume_json(token, result_json)``, which runs the same call again with the kernel's
result in ``context.accel_result``. The tool recomputes its (cheap) inputs, finds the
result with ``result_for`` (matched by the digest of the kernel input, so a result can
never be applied to other inputs), verifies a sample of it natively, and builds its
envelope as usual.

The runner never sets ``context.accel``: there the tool computes everything in Python.
Nothing here imports the dispatcher at module level, so the tools can import this module.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from typing import Any, Mapping

__all__ = ["Accelerate", "KERNELS", "available", "prefer", "result_for", "marker",
           "resume_json", "input_digest", "pending_count"]

#: Kernels a page may offer, by name and version (the input and output shapes are in
#: docs/V2.md §13.4). A tool asks for one by this name.
KERNELS = ("np-null-mt/1",)

#: How long a marker waits for its result, and how many may wait at once.
_TTL_S = 600.0
_MAX_PENDING = 16

_LOCK = threading.Lock()
_PENDING: dict[str, dict[str, Any]] = {}


class Accelerate(BaseException):
    """Raised by a tool to hand ``input`` to the page's ``kernel``.

    A BaseException, like KeyboardInterrupt, so that the ``except Exception`` handlers that
    turn a tool's errors into failed envelopes let it through to ``dispatch.call``."""

    def __init__(self, kernel: str, input: Mapping[str, Any]) -> None:  # noqa: A002
        super().__init__(kernel)
        self.kernel = str(kernel)
        self.input = dict(input)


def input_digest(kernel: str, data: Any) -> str:
    """SHA-256 of the kernel name and its canonical JSON input."""
    text = json.dumps([kernel, data], ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def available(ctx: Any, kernel: str) -> bool:
    """Whether this call may hand ``kernel`` to the page: a browser call whose context
    lists it, and that is not already carrying a result."""
    accel = _mapping(getattr(ctx, "accel", None))
    kernels = accel.get("kernels") or ()
    return (getattr(ctx, "where", "") == "browser" and kernel in KERNELS
            and kernel in kernels and not getattr(ctx, "accel_result", None))


def prefer(ctx: Any) -> str:
    """The user's engine preference: "auto", "gpu" or "cpu"."""
    value = _mapping(getattr(ctx, "accel", None)).get("prefer")
    return value if value in ("auto", "gpu", "cpu") else "auto"


def result_for(ctx: Any, kernel: str, data: Any) -> dict[str, Any] | None:
    """The page's result for exactly this kernel input, or None.

    A result whose digest does not match the input recomputed now is ignored (None), and so
    is one that reports an error: the tool then computes natively. The returned mapping is
    the kernel's output plus ``_error`` when the page reported one."""
    got = _mapping(getattr(ctx, "accel_result", None))
    if not got or got.get("kernel") != kernel:
        return None
    if got.get("digest") != input_digest(kernel, data):
        return None
    result = got.get("result")
    if isinstance(got.get("error"), str) and got["error"]:
        return {"_error": got["error"]}
    return dict(result) if isinstance(result, Mapping) else None


def _expire(now: float) -> None:
    for token in [t for t, p in _PENDING.items() if now - p["at"] > _TTL_S]:
        _PENDING.pop(token, None)


def marker(tool: str, arguments: Any, context: Any, req: Accelerate) -> dict[str, Any]:
    """Remember the call and return the marker the worker acts on."""
    now = time.monotonic()
    token = secrets.token_hex(16)
    digest = input_digest(req.kernel, req.input)
    with _LOCK:
        _expire(now)
        while len(_PENDING) >= _MAX_PENDING:
            _PENDING.pop(min(_PENDING, key=lambda t: _PENDING[t]["at"]), None)
        _PENDING[token] = {"tool": tool, "arguments": arguments,
                           "context": dict(context) if isinstance(context, Mapping) else {},
                           "kernel": req.kernel, "digest": digest, "at": now}
    return {"__accelerate__": {"token": token, "kernel": req.kernel, "digest": digest,
                               "input": req.input}}


def pending_count() -> int:
    with _LOCK:
        _expire(time.monotonic())
        return len(_PENDING)


def resume_json(token: str, result_json: str) -> str:
    """Finish an accelerated call: run it again with the page's result. Never raises; an
    unknown or expired token is a failed envelope."""
    from .dispatch import call_json

    with _LOCK:
        _expire(time.monotonic())
        pending = _PENDING.pop(str(token or ""), None)
    if pending is None:
        return json.dumps({"ok": False, "tool": "accelerate", "via": "accelerate",
                           "status": "failed", "summary": "加速计算的结果已过期，请重新调用",
                           "summary_en": "The accelerated result expired; call the tool again",
                           "text": "accelerate: failed. Error (runtime_error): unknown or expired "
                                   "acceleration token",
                           "error": {"type": "runtime_error",
                                     "message": "unknown or expired acceleration token",
                                     "hint": "call the tool again"}},
                          ensure_ascii=False)
    try:
        parsed = json.loads(result_json) if str(result_json or "").strip() else {}
    except ValueError as exc:
        parsed = {"error": f"the page's result is not JSON ({exc})"}
    if not isinstance(parsed, Mapping):
        parsed = {"error": "the page's result is not an object"}
    error = parsed.get("error")
    context = dict(pending["context"])
    context["accel_result"] = {
        "kernel": pending["kernel"], "digest": pending["digest"],
        "result": None if error else dict(parsed),
        "error": str(error) if error else "",
    }
    args = pending["arguments"]
    args_json = args if isinstance(args, str) else json.dumps(args if args is not None else {},
                                                             ensure_ascii=False)
    return call_json(pending["tool"], args_json, json.dumps(context, ensure_ascii=False))
