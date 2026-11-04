"""Current operational status of each connector operation, kept apart from verification.

A verification record says that an operation once answered (``catalog`` ``verification``:
``live_upstream PASS (2026-10-01)``). It says nothing about today. The audit of 2026-11-04
found every one of the 444 operations still showing its earlier successful record while 19
of them failed twice through the production gateway (HERB: HTTP 403 with upstream code
1003; DCABM-TCM: gateway timeouts), and SIGNOR's ``relations_among`` answered a PHP fatal
error. This module is the separate, current status the audit asked for (repair P1):

* **observations**, each one call of one operation: when, by which route
  (``production_gateway``, ``direct_upstream``, ``browser_gateway``, ``runner_direct``),
  succeeded or failed, the layer that failed (``gateway``, ``transport``,
  ``upstream_http``, ``upstream_content``) with the gateway's and the upstream's HTTP
  statuses kept apart, and the record it comes from;
* the **committed record** (``data/operational_status.json``, package data so the browser
  runtime has it too), written by ``studio/scripts/operational_status.py`` from a
  production probe (the audit's workbook) or a scheduled live run, changed only by a
  reviewed commit;
* **local observations** of this runtime's own calls (``<state_root>/opstatus/``), appended
  by the dispatcher after every connector call that reached the network;
* a **view** per operation: ``state`` (``operational``, ``failing``, ``recovered``,
  ``unknown``), the latest observation, the latest production observation, the last
  success and the last failure. Nothing is overwritten: a success after a failure marks the
  operation ``recovered`` and the failure stays in ``last_failure`` and in the history.

The view is read by the catalog (``entry.operational``), by ``catalog_search`` (the model's
tool discovery) and by the dispatcher on every connector call (``receipt.operational``).
"""

from __future__ import annotations

import json
import os
import threading
import time
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = ["SCHEMA", "RECORD", "ROUTES", "LAYERS", "STATES", "committed", "local",
           "record_local", "observation_from", "view", "brief", "describe", "plain",
           "catalog_view", "catalog_default"]

SCHEMA = "tcmstudio.operational_status/1"
#: The committed record, as package data (shipped in the browser bundle with tcmstudio).
RECORD = "studio/runner/src/tcmstudio/data/operational_status.json"
ROUTES = ("production_gateway", "direct_upstream", "browser_gateway", "runner_direct")
#: Where a failed call failed: the source gateway itself; the network between (no answer,
#: a dropped connection, a timeout with no gateway to say so); the upstream's HTTP status;
#: the upstream's answer, which came back but was not data (an error report, a page, an
#: answer that does not fit the operation's declared shape). ``client``: this side refused
#: it (a size cap); ``policy``: a redirect off the declared hosts.
LAYERS = ("gateway", "transport", "upstream_http", "upstream_content", "client", "policy")
STATES = ("operational", "failing", "recovered", "unknown")
_PRODUCTION = ("production_gateway",)
_LOCAL_DIR = "opstatus"
_LOCAL_FILE = "observations.jsonl"
#: Local observations kept per operation (the oldest go first); the file is compacted when
#: it holds more than this many lines in all.
_KEEP_PER_OP = 20
_COMPACT_AT = 5000
_LOCK = threading.Lock()


# ------------------------------------------------------------------------- the record

@lru_cache(maxsize=1)
def _committed_doc() -> dict[str, Any]:
    try:
        text = resources.files("tcmstudio").joinpath("data/operational_status.json").read_text(
            encoding="utf-8")
        doc = json.loads(text)
    except (FileNotFoundError, ModuleNotFoundError, OSError, ValueError):
        return {"schema": SCHEMA, "records": {}, "operations": {}}
    if doc.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "records": {}, "operations": {}}
    return doc


def committed() -> dict[str, Any]:
    """The committed record: ``{schema, records, operations: {entry id: [observation…]}}``."""
    return _committed_doc()


def _local_path(state_root: str | os.PathLike[str] | None) -> Path | None:
    if not state_root:
        return None
    return Path(state_root) / _LOCAL_DIR / _LOCAL_FILE


def local(state_root: str | os.PathLike[str] | None,
          entry_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """This runtime's own observations, by entry id (one id with ``entry_id``)."""
    path = _local_path(state_root)
    out: dict[str, list[dict[str, Any]]] = {}
    if path is None or not path.is_file():
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            ob = json.loads(line)
        except ValueError:
            continue                            # a torn last line is skipped, never fatal
        if not isinstance(ob, dict) or not ob.get("id"):
            continue
        if entry_id is not None and ob["id"] != entry_id:
            continue
        out.setdefault(str(ob["id"]), []).append({k: v for k, v in ob.items() if k != "id"})
    return out


def record_local(state_root: str | os.PathLike[str] | None, entry_id: str,
                 observation: Mapping[str, Any]) -> bool:
    """Append one observation of ``entry_id`` to this runtime's local record. Never raises:
    a status that cannot be written is a status not kept, not a failed call."""
    path = _local_path(state_root)
    if path is None or not entry_id:
        return False
    line = json.dumps({"id": entry_id, **dict(observation)}, ensure_ascii=False,
                      sort_keys=True, default=str)
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if path.stat().st_size > _COMPACT_AT * 200:
                _compact(path)
        return True
    except OSError:
        return False


def _compact(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= _COMPACT_AT:
        return
    keep: dict[str, list[str]] = {}
    for line in lines:
        try:
            key = str(json.loads(line).get("id"))
        except ValueError:
            continue
        keep.setdefault(key, []).append(line)
    kept = [ln for group in keep.values() for ln in group[-_KEEP_PER_OP:]]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _utc(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def observation_from(envelope_or_outcome: Mapping[str, Any], *, where: str,
                     at: str | None = None) -> dict[str, Any] | None:
    """An observation from a call's envelope (or the dispatcher's outcome as a mapping), or
    ``None`` when no request reached the network (refused before it: web access off, bad
    arguments, a policy refusal): only a call that was attempted says anything about the
    operation's availability."""
    receipt = envelope_or_outcome.get("receipt") or {}
    request = receipt.get("request") if isinstance(receipt, Mapping) else None
    if not isinstance(request, Mapping) or not request:
        return None
    if request.get("cached"):
        return None                                   # an answer from the cache saw no upstream
    status = str(envelope_or_outcome.get("status") or "")
    route = "browser_gateway" if where == "browser" else "runner_direct"
    ob: dict[str, Any] = {"at": at or _utc(), "route": route,
                          "outcome": "succeeded" if status == "succeeded" else "failed"}
    gateway = request.get("gateway") if isinstance(request.get("gateway"), Mapping) else {}
    if ob["outcome"] == "failed":
        ob["layer"] = str(request.get("failure_layer") or "transport")
        err = envelope_or_outcome.get("error") or {}
        detail = str(err.get("message") or "") if isinstance(err, Mapping) else ""
        if detail:
            ob["detail"] = " ".join(detail.split())[:200]
        if gateway.get("error_type"):
            ob["error_type"] = str(gateway["error_type"])
    if gateway.get("status") is not None:
        ob["gateway_http"] = int(gateway["status"])
    if request.get("http_status") is not None and ob.get("layer") != "gateway":
        ob["upstream_http"] = int(request["http_status"])
    return ob


# --------------------------------------------------------------------------- the view

def _timeline(entry_id: str, state_root: str | os.PathLike[str] | None,
              extra: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    obs = [dict(o) for o in committed().get("operations", {}).get(entry_id, ())]
    obs += [dict(o) for o in local(state_root, entry_id).get(entry_id, ())]
    obs += [dict(o) for o in extra]
    # ISO 8601 UTC strings sort as times; a date alone sorts before that day's times
    return sorted(obs, key=lambda o: str(o.get("at") or ""))


def _slim(ob: Mapping[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not ob:
        return None
    return {k: ob[k] for k in keys if ob.get(k) not in (None, "")}


_LATEST_KEYS = ("at", "route", "outcome", "layer", "error_type", "gateway_http",
                "upstream_http", "detail", "note", "record")


def view(entry_id: str, state_root: str | os.PathLike[str] | None = None, *,
         extra: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The operation's current status from every observation of it (committed, local and
    ``extra``), oldest first. ``state``: ``unknown`` with no observation; ``failing`` when
    the latest failed; ``recovered`` when the latest succeeded right after a failure;
    ``operational`` otherwise."""
    obs = _timeline(entry_id, state_root, extra)
    if not obs:
        return {"state": "unknown", "observations": 0, "record": RECORD}
    latest = obs[-1]
    failures = [o for o in obs if o.get("outcome") != "succeeded"]
    successes = [o for o in obs if o.get("outcome") == "succeeded"]
    streak = 0
    for o in reversed(obs):
        if o.get("outcome") == "succeeded":
            break
        streak += 1
    if latest.get("outcome") != "succeeded":
        state = "failing"
    elif len(obs) > 1 and obs[-2].get("outcome") != "succeeded":
        state = "recovered"
    else:
        state = "operational"
    production = [o for o in obs if o.get("route") in _PRODUCTION]
    out: dict[str, Any] = {
        "state": state,
        "latest": _slim(latest, _LATEST_KEYS),
        "latest_production": _slim(production[-1], _LATEST_KEYS) if production else None,
        "last_success": _slim(successes[-1], ("at", "route", "record")) if successes else None,
        "last_failure": _slim(failures[-1], ("at", "route", "layer", "error_type",
                                             "gateway_http", "upstream_http", "detail",
                                             "record")) if failures else None,
        "consecutive_failures": streak,
        "observations": len(obs),
        "record": RECORD,
    }
    return out


def brief(v: Mapping[str, Any]) -> dict[str, Any]:
    """The few fields the model's tool discovery carries for one operation."""
    out: dict[str, Any] = {"state": v.get("state", "unknown")}
    latest = v.get("latest") or {}
    if latest:
        out["checked"] = latest.get("at")
        out["route"] = latest.get("route")
    if v.get("state") in ("failing", "recovered") and v.get("last_failure"):
        lf = v["last_failure"]
        out["failure"] = {k: lf[k] for k in ("at", "layer", "error_type", "gateway_http",
                                            "upstream_http") if lf.get(k) not in (None, "")}
    if v.get("last_success"):
        out["last_success"] = v["last_success"].get("at")
    return out


_LAYER_EN = {"gateway": "at the source gateway", "transport": "in transport (no answer)",
             "upstream_http": "at the upstream (HTTP error)",
             "upstream_content": "in the upstream's answer (not data)",
             "client": "on this side (a size cap)", "policy": "by policy (a redirect)"}
_LAYER_ZH = {"gateway": "数据库网关", "transport": "传输（无应答）", "upstream_http": "上游服务（HTTP 错误）",
             "upstream_content": "上游返回内容（非数据）", "client": "本端（大小上限）",
             "policy": "策略（重定向）"}


def describe(v: Mapping[str, Any]) -> tuple[str, str]:
    """One line each (zh, en) on what the status means, for a summary or a hint; empty for
    an operation that is operational or unknown."""
    state = v.get("state")
    lf = v.get("last_failure") or {}
    ls = v.get("last_success") or {}
    if state not in ("failing", "recovered") or not lf:
        return "", ""
    http = ", ".join(f"{n} HTTP {lf[k]}" for k, n in (("gateway_http", "gateway"),
                                                      ("upstream_http", "upstream"))
                     if lf.get(k) not in (None, ""))
    layer_en = _LAYER_EN.get(str(lf.get("layer")), str(lf.get("layer") or "unknown layer"))
    layer_zh = _LAYER_ZH.get(str(lf.get("layer")), str(lf.get("layer") or "未知环节"))
    since = f"; last success {ls.get('at')} ({ls.get('route')})" if ls else "; no success recorded"
    since_zh = f"；最近一次成功 {ls.get('at')}（{ls.get('route')}）" if ls else "；无成功记录"
    if state == "failing":
        n = v.get("consecutive_failures") or 1
        return (f"当前故障：最近 {n} 次调用失败，最近一次 {lf.get('at')}（{lf.get('route')}），故障环节：{layer_zh}"
                + (f"（{http}）" if http else "") + since_zh,
                f"currently failing: the last {n} call(s) failed, latest {lf.get('at')} via "
                f"{lf.get('route')}, {layer_en}" + (f" ({http})" if http else "") + since)
    return (f"已恢复：最近一次调用成功；此前 {lf.get('at')}（{lf.get('route')}）失败于{layer_zh}，记录保留",
            f"recovered: the latest call succeeded; the failure of {lf.get('at')} via "
            f"{lf.get('route')} ({layer_en}) is kept in the record")


# ----------------------------------------------------------------- the catalog's copy

def plain(v: Mapping[str, Any]) -> bool:
    """Whether a view says nothing beyond "operational at the latest production check": the
    catalog then leaves it out of the entry and states it once (``catalog_default``), which
    keeps catalog.json within its budget (444 full views would add ~270 KB)."""
    return (v.get("state") == "operational"
            and (v.get("latest_production") or {}).get("outcome") == "succeeded")


def catalog_view(v: Mapping[str, Any]) -> dict[str, Any]:
    """A view as a catalog entry carries it, compact: what the document states once (the
    record) and what repeats (``latest`` when it is the latest production check, record ids,
    notes) left out, and the failure's excerpt kept short."""
    def slim(ob: Mapping[str, Any] | None, drop: tuple[str, ...]) -> dict[str, Any] | None:
        return {k: val for k, val in ob.items() if k not in drop} if ob else None

    out: dict[str, Any] = {"state": v.get("state", "unknown")}
    for key in ("consecutive_failures",):
        if v.get(key):
            out[key] = v[key]
    # a production check's route is production_gateway by definition: not repeated
    lp = slim(v.get("latest_production"), ("record", "note", "detail", "route"))
    latest = slim(v.get("latest"), ("record", "note", "detail"))
    if lp:
        out["latest_production"] = lp
    if latest and latest != {**(lp or {}), "route": "production_gateway"}:
        out["latest"] = latest
    if v.get("last_success"):
        out["last_success"] = slim(v["last_success"], ("record",))
    lf = slim(v.get("last_failure"), ("record",))
    if lf:
        if lf.get("detail"):
            lf["detail"] = str(lf["detail"])[:120]
        out["last_failure"] = lf
    return out


def catalog_default() -> dict[str, Any]:
    """What a connector entry without ``operational`` means, stated once in the catalog."""
    checked = sorted(str((view(oid).get("latest_production") or {}).get("at") or "")[:10]
                     for oid in committed().get("operations", {}))
    return {"absent": "operational", "record": RECORD,
            "checked": checked[-1] if checked else None,
            "note": "an entry without 'operational' succeeded at its latest production check"}
