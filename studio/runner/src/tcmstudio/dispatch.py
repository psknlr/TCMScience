"""Execute one tool call and return the §3 envelope — in the runner and in the browser.

``call(tool, arguments, context)`` never raises. It validates the arguments against the
tool's schema (types, required names, enums, with a 'did you mean' hint), refuses what is
reserved for a person, checks that the entry can run here (runtime, optional dependencies,
web access), and routes it:

* native tools and connectors through BioScience's ``Runtime.invoke`` — the policy kernel
  rules on licence, network and filesystem, and on the purpose (academic / commercial);
* governed skills through ``bioagent.governed.run_governed``, with one durable PSH state
  directory per project, the lockfile pin checked, and the written outputs read back;
* the clinic, the TCM data hub and the study designs by their Python APIs;
* job kinds to the runner's job service (``context.jobs``), or ``unavailable`` without it.

The context says where the call runs (``browser`` / ``runner``), whether the project has
web access, the purpose, where per-project state lives, and the jobs hook. Nothing here
starts a thread or a process, or opens a socket except a connector call the kernel allowed.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import re
import secrets
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from . import governance as gv
from .catalog import (NEVER_OFFERED, dependency_present, entries as catalog_entries,
                      catalog_search, get_entry, skill_specs, suggest)
from .core_tools import CORE_BY_NAME, route
from .envelope import jsonable, shape, utc_now

__all__ = ["Context", "call", "call_json", "validate", "ValidationResult"]

_ENV_LOCK = threading.Lock()


# =============================================================================== context

def _auto_where() -> str:
    return "browser" if sys.platform == "emscripten" else "runner"


def _truthy(value: Any) -> bool:
    # JSON from the page carries booleans; a hand-written context may say "false".
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


@dataclass
class Context:
    """Where and under what rules a call runs.

    ``state_root`` is the directory under which each project gets ``<project_id>/psh`` (its
    PSH audit chain), ``<project_id>/runs`` (governed-run outputs) and
    ``<project_id>/clinic`` (clinic sessions): ``<home>/projects`` on the runner,
    ``/persist`` in the browser. Without it, state goes to a temporary directory: attested,
    but not durable. ``jobs`` is the runner's job service: ``submit(kind, params,
    project_id) -> Job`` and ``get(job_id, wait_s) -> Job``.
    """

    where: str = field(default_factory=_auto_where)
    network: bool = False
    purpose: str = "academic"
    state_root: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    approvals: tuple[str, ...] = ()
    device: str = "cpu"
    jobs: Any = None
    tcmdb_root: str | None = None
    durable: bool | None = None
    capabilities: Mapping[str, Any] | None = None

    @property
    def profile(self) -> str:
        return "biomedical-research" if self.network else "offline-analysis"

    @classmethod
    def coerce(cls, value: Any) -> tuple["Context", list[str]]:
        """A Context from None, a Context or a mapping (the JSON the worker or the runner
        passes). Unknown keys are ignored; a bad value falls back to the default, with a
        note."""
        if isinstance(value, Context):
            return value, []
        notes: list[str] = []
        ctx = cls()
        if value is None:
            return ctx, notes
        if not isinstance(value, Mapping):
            return ctx, [f"context of type {type(value).__name__} ignored"]
        where = value.get("where")
        if where in ("browser", "runner"):
            ctx.where = where
        elif where is not None:
            notes.append(f"context.where {where!r} is not browser or runner; using {ctx.where}")
        net = value.get("network")
        if isinstance(net, Mapping):
            enabled = _truthy(net.get("enabled"))
            ctx.network = enabled and net.get("profile", "biomedical-research") != "offline-analysis"
        elif net is not None:
            ctx.network = _truthy(net)
        purpose = value.get("purpose")
        if purpose in ("academic", "commercial"):
            ctx.purpose = purpose
        elif purpose is not None:
            notes.append(f"context.purpose {purpose!r} is not academic or commercial; using academic")
        for key in ("state_root", "project_id", "conversation_id", "tcmdb_root"):
            if value.get(key) not in (None, ""):
                setattr(ctx, key, str(value[key]))
        if isinstance(value.get("device"), str) and value["device"]:
            ctx.device = value["device"]
        approvals = value.get("approvals")
        if isinstance(approvals, (list, tuple)):
            ctx.approvals = tuple(str(a) for a in approvals)
        if value.get("durable") is not None:
            ctx.durable = _truthy(value["durable"])
        if isinstance(value.get("capabilities"), Mapping):
            ctx.capabilities = value["capabilities"]
        if value.get("jobs") is not None:
            ctx.jobs = value["jobs"]
        return ctx, notes


def _jobs_fn(jobs: Any, name: str) -> Callable[..., Any] | None:
    if jobs is None:
        return None
    fn = jobs.get(name) if isinstance(jobs, Mapping) else getattr(jobs, name, None)
    return fn if callable(fn) else None


# ============================================================================ validation

_TYPE_NAMES = {"string": "a string", "integer": "an integer", "number": "a number",
               "boolean": "true or false", "array": "a list", "object": "an object",
               "null": "null"}
_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


@dataclass
class ValidationResult:
    value: Any
    problems: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _describe(schema: Mapping[str, Any]) -> str:
    if "enum" in schema:
        return "one of " + ", ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"][:12])
    if "anyOf" in schema:
        return " or ".join(_describe(s) for s in schema["anyOf"] if isinstance(s, Mapping))
    t = schema.get("type")
    if t == "array":
        inner = schema.get("items") or {}
        return f"a list of {_describe(inner).removeprefix('a ').removeprefix('an ')}s" \
            if inner.get("type") in ("string", "number", "integer", "object") else "a list"
    return _TYPE_NAMES.get(t, "any value") if isinstance(t, str) else "any value"


def validate(schema: Mapping[str, Any] | None, value: Any, path: str = "arguments") -> ValidationResult:
    """Check ``value`` against a JSON Schema subset (type, enum, anyOf, required,
    properties, additionalProperties, items, min/max, minItems/maxItems, min/maxLength).

    Harmless slips are repaired and noted rather than refused: a numeral given as a string,
    a whole float for an integer, ``"true"`` for a boolean, a single value where a list is
    expected (a string is never iterated character by character), and ``null`` for an
    optional argument (treated as omitted)."""
    res = ValidationResult(value)
    res.value = _check(schema or {}, value, path, res)
    return res


def _check(schema: Mapping[str, Any], value: Any, path: str, res: ValidationResult) -> Any:
    if not isinstance(schema, Mapping) or not schema:
        return value
    before = len(res.problems)
    if "anyOf" in schema:
        trials = []
        for option in schema["anyOf"]:
            trial = ValidationResult(value)
            trial.value = _check(option, value, path, trial)
            if trial.ok:
                res.notes += trial.notes
                return trial.value
            trials.append(trial)
        res.problems.append(f"{path} must be {_describe(schema)}")
        return value
    t = schema.get("type")
    if isinstance(t, list):
        base = {k: v for k, v in schema.items() if k != "type"}
        return _check({"anyOf": [{**base, "type": x} for x in t]}, value, path, res)
    if t == "null":
        if value is not None:
            res.problems.append(f"{path} must be null")
        return value
    if t == "string":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            res.problems.append(f"{path} must be a string, not {_kind_of(value)}")
            return value
        if not isinstance(value, str):
            res.notes.append(f"{path}: the number {value} was read as the text \"{value}\"")
            value = str(value)
        if "minLength" in schema and len(value) < schema["minLength"]:
            res.problems.append(f"{path} must have at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            res.problems.append(f"{path} must have at most {schema['maxLength']} characters")
    elif t == "integer":
        if isinstance(value, bool):
            res.problems.append(f"{path} must be an integer, not a boolean")
            return value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, str) and _INT_RE.match(value.strip()):
            res.notes.append(f"{path}: \"{value}\" was read as the integer {int(value)}")
            value = int(value.strip())
        if not isinstance(value, int):
            res.problems.append(f"{path} must be an integer, not {_kind_of(value)}")
            return value
        _bounds(schema, value, path, res)
    elif t == "number":
        if isinstance(value, bool):
            res.problems.append(f"{path} must be a number, not a boolean")
            return value
        if isinstance(value, str) and _NUM_RE.match(value.strip()):
            res.notes.append(f"{path}: \"{value}\" was read as the number {float(value)}")
            value = float(value.strip())
        if not isinstance(value, (int, float)):
            res.problems.append(f"{path} must be a number, not {_kind_of(value)}")
            return value
        _bounds(schema, value, path, res)
    elif t == "boolean":
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            res.notes.append(f"{path}: \"{value}\" was read as {value.strip().lower()}")
            value = value.strip().lower() == "true"
        if not isinstance(value, bool):
            res.problems.append(f"{path} must be true or false, not {_kind_of(value)}")
            return value
    elif t == "array":
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            items = schema.get("items") or {}
            if value is not None and not isinstance(value, Mapping) and \
                    items.get("type") in (None, "string", "number", "integer"):
                res.notes.append(f"{path}: a single value was given; it was treated as a "
                                 "one-element list")
                value = [value]
            else:
                res.problems.append(f"{path} must be {_describe(schema)}, not {_kind_of(value)}")
                return value
        if "minItems" in schema and len(value) < schema["minItems"]:
            res.problems.append(f"{path} needs at least {schema['minItems']} item(s); "
                                f"{len(value)} given")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            res.problems.append(f"{path} takes at most {schema['maxItems']} item(s); "
                                f"{len(value)} given")
        items = schema.get("items")
        if isinstance(items, Mapping) and items:
            value = [_check(items, v, f"{path}[{i}]", res) for i, v in enumerate(value)]
    elif t == "object":
        if isinstance(value, str) and value.strip().startswith("{"):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                res.notes.append(f"{path}: a JSON string was given; it was parsed as an object")
                value = parsed
        if not isinstance(value, Mapping):
            res.problems.append(f"{path} must be an object, not {_kind_of(value)}")
            return value
        value = _check_object(schema, dict(value), path, res)
    if "enum" in schema and len(res.problems) == before and value not in schema["enum"]:
        allowed = schema["enum"]
        res.problems.append(f"{path} must be one of "
                            + ", ".join(json.dumps(v, ensure_ascii=False) for v in allowed[:20])
                            + (", …" if len(allowed) > 20 else "")
                            + f"; got {json.dumps(value, ensure_ascii=False)}")
        close = difflib.get_close_matches(str(value), [str(v) for v in allowed], n=1, cutoff=0.5)
        if close:
            res.hints.append(f"Did you mean {close[0]!r} for {path}?")
    return value


def _check_object(schema: Mapping[str, Any], value: dict[str, Any], path: str,
                  res: ValidationResult) -> dict[str, Any]:
    props: Mapping[str, Any] = schema.get("properties") or {}
    required = [r for r in schema.get("required") or () if isinstance(r, str)]
    additional = schema.get("additionalProperties", True)
    out: dict[str, Any] = {}
    unknown: list[str] = []
    for key, item in value.items():
        sub = f"{path}.{key}" if path else key
        if key in props:
            prop = props[key] or {}
            if item is None and key not in required and not _allows_null(prop):
                continue                      # null for an optional argument = omitted
            out[key] = _check(prop, item, sub, res)
        elif additional is False:
            unknown.append(key)
        elif isinstance(additional, Mapping):
            out[key] = _check(additional, item, sub, res)
        else:
            out[key] = item
    missing = [r for r in required if r not in out]
    if missing and additional is not False:
        # Free-form objects accept any key, but a key standing in for a missing required
        # one is still worth naming.
        for key in [k for k in value if k not in props][:1]:
            close = difflib.get_close_matches(key, missing, n=1, cutoff=0.5) or (
                missing if len(missing) == 1 else [])
            if close:
                res.hints.append(f"Did you mean {close[0]!r} instead of {key!r}?")
    for key in unknown:
        res.problems.append(f"{path} has no argument {key!r}")
        candidates = missing or list(props)
        close = difflib.get_close_matches(key, candidates, n=1, cutoff=0.5)
        if not close and len(unknown) == 1 and len(missing) == 1:
            close = missing                   # e.g. 'query' given where 'subject' is required
        if close:
            res.hints.append(f"Did you mean {close[0]!r} instead of {key!r}?")
    for key in missing:
        res.problems.append(f"{path} is missing the required argument {key!r} "
                            f"({_describe(props.get(key) or {})})")
    return out


def _allows_null(schema: Mapping[str, Any]) -> bool:
    if schema.get("type") == "null" or (isinstance(schema.get("type"), list)
                                        and "null" in schema["type"]):
        return True
    return any(isinstance(s, Mapping) and s.get("type") == "null"
               for s in schema.get("anyOf") or ())


def _bounds(schema: Mapping[str, Any], value: float, path: str, res: ValidationResult) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        res.problems.append(f"{path} must be ≥ {schema['minimum']}; got {value}")
    if "maximum" in schema and value > schema["maximum"]:
        res.problems.append(f"{path} must be ≤ {schema['maximum']}; got {value}")


def _kind_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, (list, tuple)):
        return "a list"
    if isinstance(value, Mapping):
        return "an object"
    return type(value).__name__


def _signature(schema: Mapping[str, Any]) -> str:
    props = schema.get("properties") or {}
    req = set(schema.get("required") or ())
    parts = [f"{k}{'' if k in req else '?'}: {_describe(v or {})}" for k, v in props.items()]
    return "expected {" + "; ".join(parts[:16]) + ("; …" if len(parts) > 16 else "") + "}"


# ============================================================================ outcomes

@dataclass
class Outcome:
    status: str = "succeeded"
    result: Any = None
    model_view: Any = ...
    summary: str = ""
    governance: dict[str, Any] | None = None
    citations: list[Mapping[str, Any]] | None = None
    error: dict[str, Any] | None = None
    job: dict[str, Any] | None = None
    content_hash: str | None = None
    audit_head: str | None = None
    composite_version: str | None = None
    notes: list[str] = field(default_factory=list)
    receipt: dict[str, Any] = field(default_factory=dict)


def _failure(kind: str, error_type: str, message: str, hint: str = "", *,
             status: str = "failed", summary: str = "", refusals: list[dict] | None = None,
             limitations: list[str] | None = None) -> Outcome:
    g = gv.empty(kind)
    g["refusals"] = list(refusals or [])
    g["limitations"] = list(limitations or [])
    return Outcome(status=status, result=None, model_view=None, summary=summary,
                   governance=g, error={"type": error_type, "message": message, "hint": hint})


#: zh labels used in summaries (DESIGN §2.3 / §7.4 vocabulary).
_CLAIM_ZH = {"attribution": "记载", "traditional_use": "传统应用", "mechanism_hypothesis": "机制假说",
             "mechanism": "机制", "association": "关联", "efficacy": "疗效",
             "safety_signal": "安全信号", "recommendation": "推荐"}
_DESIGN_ZH = {"systematic_review": "系统评价", "randomized_trial": "随机对照试验",
              "observational": "观察性研究", "case_report": "病例报告", "animal": "动物实验",
              "in_vitro": "体外实验", "expert_consensus": "专家共识", "classical_text": "经典文献",
              "in_silico": "计算模拟", "network_prediction": "网络预测", "docking": "分子对接",
              "molecular_dynamics": "分子动力学", "target_prediction": "靶点预测",
              "pathway_enrichment": "通路富集"}
_SEVERITY_ZH = {"stop": "不可签署", "block": "须医师说明理由", "warn": "警示", "info": "提示"}
_EXPOSURE_ZH = {"plausible": "可能达到", "implausible": "难以达到",
                "depends_on_unknowns": "取决于未知量", "undeterminable": "无法判定",
                "inactive_in_assay": "实验中无活性"}

_FAIL_ZH = {"bad_arguments": "参数有误", "unavailable": "此处不可运行", "not_found": "未找到",
            "refused": "已拒绝", "runtime_error": "运行出错", "timeout": "超时",
            "network_off": "未联网"}


def _failure_summary(o: Outcome, title_zh: str) -> str:
    err = o.error or {}
    head = _FAIL_ZH.get(err.get("type", ""), "未完成")
    msg = " ".join(str(err.get("message", "")).split())
    if len(msg) > 90:
        msg = msg[:89] + "…"
    return f"{title_zh}：{head}" + (f"（{msg}）" if msg else "")


# ============================================================================== entry

def call(tool: str, arguments: Any = None, context: Any = None) -> dict[str, Any]:
    """Run ``tool`` (a core tool name, ``call_tool`` or a catalog entry id) and return the
    §3 envelope. Never raises."""
    t0 = time.perf_counter()
    started = utc_now()
    name = str(tool or "").strip() if not isinstance(tool, bytes) else tool.decode("utf-8", "replace")
    try:
        ctx, ctx_notes = Context.coerce(context)
    except Exception as exc:                                    # noqa: BLE001
        ctx, ctx_notes = Context(), [f"context ignored: {exc}"]
    try:
        return _call(name, arguments, ctx, ctx_notes, t0, started)
    except KeyboardInterrupt:
        return shape(name, via=name, kind="system", status="cancelled", summary="已取消",
                     arguments=arguments if isinstance(arguments, Mapping) else {},
                     started_at=started, duration_ms=(time.perf_counter() - t0) * 1000,
                     where=ctx.where, device=ctx.device,
                     error={"type": "runtime_error", "message": "interrupted", "hint": ""})
    except BaseException as exc:                                # noqa: BLE001
        return shape(name, via=name, kind="system", status="failed",
                     summary="运行出错：调用未能完成",
                     arguments=arguments if isinstance(arguments, Mapping) else {},
                     started_at=started, duration_ms=(time.perf_counter() - t0) * 1000,
                     where=ctx.where, device=ctx.device,
                     error={"type": "runtime_error",
                            "message": f"{type(exc).__name__}: {exc}"[:2000], "hint": ""})


def call_json(tool: str, arguments_json: str = "{}", context_json: str = "{}") -> str:
    """``call`` with JSON text in and out, for the browser worker: nothing but strings
    crosses the JavaScript boundary, so no proxy leaks and no float is reformatted.
    Never raises."""
    context: Any = None
    notes: list[str] = []
    try:
        context = json.loads(context_json) if str(context_json or "").strip() else None
    except ValueError as exc:
        notes.append(f"context ignored: not JSON ({exc})")
    envelope = call(tool, arguments_json, context)
    if notes:
        envelope["text"] = (envelope["text"] + "\nNote: " + notes[0])[:16_000]
    try:
        return json.dumps(envelope, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:                      # pragma: no cover - shape is JSON-safe
        return json.dumps({"ok": False, "tool": str(tool), "via": str(tool), "status": "failed",
                           "error": {"type": "runtime_error", "message": str(exc), "hint": ""}})


def _parse_arguments(arguments: Any) -> tuple[dict[str, Any] | None, str]:
    if arguments is None:
        return {}, ""
    if isinstance(arguments, (str, bytes)):
        text = arguments.decode("utf-8", "replace") if isinstance(arguments, bytes) else arguments
        if not text.strip():
            return {}, ""
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            return None, f"arguments are not valid JSON ({exc})"
        arguments = parsed
    if not isinstance(arguments, Mapping):
        return None, f"arguments must be an object, not {_kind_of(arguments)}"
    return {str(k): v for k, v in arguments.items()}, ""


def _call(name: str, arguments: Any, ctx: Context, notes: list[str], t0: float,
          started: str) -> dict[str, Any]:
    args, problem = _parse_arguments(arguments)
    called = name
    via = name
    entry: dict[str, Any] | None = None
    outcome: Outcome | None = None
    kind = "system"
    title_zh = name
    if args is None:
        outcome = _failure("system", "bad_arguments", problem,
                           'Pass the arguments as a JSON object, e.g. {"name": "黄芪"}.')
        args = {}
    elif not name:
        outcome = _failure("system", "not_found", "no tool was named",
                           "Name a core tool, or call_tool with a catalog id.")
    elif name in NEVER_OFFERED:
        outcome = _refuse_human(name)
    else:
        target, target_args = name, args
        if name == "call_tool":
            v = validate(CORE_BY_NAME["call_tool"]["parameters"], args)
            notes += v.notes
            if not v.ok:
                outcome = _bad(v, CORE_BY_NAME["call_tool"]["parameters"], "system")
            else:
                target = str(v.value.get("tool") or "").strip()
                target_args = dict(v.value.get("arguments") or {})
                via = target
                if target == "call_tool":
                    outcome = _failure("system", "bad_arguments",
                                       "call_tool cannot call itself",
                                       "Name the catalog entry to run in 'tool'.")
                elif target in NEVER_OFFERED:
                    outcome = _refuse_human(target)
        if outcome is None:
            if target in CORE_BY_NAME:
                core = CORE_BY_NAME[target]
                title_zh = core["title"]["zh"]
                v = validate(core["parameters"], target_args)
                notes += v.notes
                if not v.ok:
                    entry = get_entry(core.get("maps_to") or "")
                    kind = entry["kind"] if entry else "system"
                    outcome = _bad(v, core["parameters"], kind)
                    via = core.get("maps_to") or target
                elif target == "literature_search":
                    entry = get_entry("connector.europepmc.search")
                    kind = "connector"
                    outcome, via = _literature_search(v.value, ctx)
                else:
                    entry_id, entry_args = route(target, v.value)
                    via = entry_id
                    entry = get_entry(entry_id)
                    if entry is None:
                        outcome = _not_found_entry(entry_id, target)
                    else:
                        kind = entry["kind"]
                        title_zh = entry["title"]["zh"]
                        outcome = _validate_and_run(entry, entry_args, ctx, notes)
            else:
                entry = get_entry(target)
                via = target
                if entry is None:
                    outcome = _not_found_entry(target, target)
                else:
                    kind = entry["kind"]
                    title_zh = entry["title"]["zh"]
                    outcome = _validate_and_run(entry, target_args, ctx, notes)
    return _finish(called, via, kind, title_zh, outcome, args, ctx, notes, t0, started)


def _refuse_human(name: str) -> Outcome:
    message = NEVER_OFFERED[name]
    return _failure("clinic" if name.startswith("clinic") else "system", "refused", message,
                    gv.remedy_for("HUMAN_ONLY"), status="refused",
                    summary="已拒绝：此操作只能由人完成，不是工具调用",
                    refusals=[gv.refusal("HUMAN_ONLY", message)])


def _bad(v: ValidationResult, schema: Mapping[str, Any], kind: str) -> Outcome:
    hint = " ".join(dict.fromkeys(v.hints)) or _signature(schema)
    return _failure(kind, "bad_arguments", "; ".join(v.problems[:8]), hint)


def _not_found_entry(entry_id: str, name: str) -> Outcome:
    close = suggest(entry_id) or suggest(name)
    hint = ("Did you mean " + " or ".join(repr(c) for c in close) + "? " if close else "") + \
        "Find ids with catalog_search."
    return _failure("system", "not_found", f"no tool or catalog entry named {entry_id!r}", hint)


def _validate_and_run(entry: dict[str, Any], args: dict[str, Any], ctx: Context,
                      notes: list[str]) -> Outcome:
    v = validate(entry["parameters"], args)
    notes += v.notes
    if not v.ok:
        return _bad(v, entry["parameters"], entry["kind"])
    blocked = _preflight(entry, v.value, ctx)
    if blocked is not None:
        return blocked
    executor = _EXECUTORS[entry["kind"]]
    return executor(entry, v.value, ctx)


def _preflight(entry: Mapping[str, Any], args: Mapping[str, Any], ctx: Context) -> Outcome | None:
    """Can this entry run here, now? Missing runtime, dependency or web access is a plain
    'unavailable' / 'network_off' with the remedy; nothing is approximated."""
    kind = entry["kind"]
    title = entry["title"]["en"]
    zh = entry["title"]["zh"]
    exec_ = entry.get("exec") or ()
    if ctx.where not in exec_:
        where = "the local runner" if "runner" in exec_ else "the browser runtime"
        return _failure(kind, "unavailable",
                        f"{title} runs on {where}; it is not available in the {ctx.where}.",
                        gv.remedy_for("NEEDS_RUNNER") if "runner" in exec_ else "",
                        summary=f"{zh}：需要本机 Runner，浏览器中不可运行" if "runner" in exec_
                        else f"{zh}：仅在浏览器运行时中可用")
    if ctx.where == "runner":
        missing = [d for d in entry.get("heavy") or () if not dependency_present(d)]
        if missing:
            return _failure(kind, "unavailable",
                            f"{title} needs {', '.join(missing)}, which is not installed on "
                            "this runner.", "Install it (pip install …) on the runner machine; "
                            "Studio never substitutes an approximation.",
                            summary=f"{zh}：缺少可选依赖 {'、'.join(missing)}（不以近似结果替代）")
    else:
        missing = [p for p in entry.get("pyodide_packages") or () if not dependency_present(p)]
        if missing:
            return _failure(kind, "unavailable",
                            f"{title} needs {', '.join(missing)} in the browser runtime, and it "
                            "is not loaded.", "The browser runtime loads the Pyodide packages an "
                            "entry lists in pyodide_packages before calling it; or run it on "
                            "the local runner.",
                            summary=f"{zh}：浏览器运行时尚未加载 {'、'.join(missing)}")
    if entry.get("network_if"):
        # Reaches its hosts only when this argument is set (allow_remote: sending a sequence
        # or fetching a structure from a third party).
        needs_net = bool(args.get(entry["network_if"]))
    else:
        needs_net = bool(entry.get("network")) and kind != "connector"
    if needs_net and not ctx.network:
        hosts = ", ".join(entry.get("hosts") or (entry.get("skill") or {}).get("network") or ())
        return _failure(kind, "network_off",
                        f"Web access is off for this project; {title} would reach "
                        f"{hosts or 'the network'}.", gv.remedy_for("NETWORK_OFF"),
                        summary="未联网：本项目未开启网络访问")
    return None


# ============================================================================ finish

def _finish(called: str, via: str, kind: str, title_zh: str, o: Outcome,
            args: Mapping[str, Any], ctx: Context, notes: list[str], t0: float,
            started: str) -> dict[str, Any]:
    gov = o.governance if o.governance is not None else gv.empty(kind)
    view = o.result if o.model_view is ... else o.model_view
    if o.status in ("succeeded", "job_submitted") and view is not None:
        labels, detail = gv.advisory_labels(view)
        if labels and not gov.get("labels"):
            gov["labels"] = labels
            gov["label"] = detail
    summary = o.summary or (_failure_summary(o, title_zh) if o.error else f"{title_zh}：完成")
    citations = _citations(o.result, gov, o.citations)
    receipt: dict[str, Any] = {"profile": ctx.profile, "purpose": ctx.purpose}
    if ctx.project_id:
        receipt["project_id"] = ctx.project_id
    if ctx.approvals:
        receipt["approvals"] = list(ctx.approvals)
    receipt.update(o.receipt)
    return shape(called, via=via, kind=gov.get("kind") or kind, status=o.status,
                 result=o.result, model_view=view, summary=summary, governance=gov,
                 citations=citations, error=o.error, job=o.job, arguments=args,
                 started_at=started, duration_ms=(time.perf_counter() - t0) * 1000,
                 where=ctx.where, device=ctx.device, content_hash=o.content_hash,
                 audit_head=o.audit_head, composite_version=o.composite_version,
                 receipt_extra=receipt, notes=[*notes, *o.notes])


def _citations(result: Any, gov: Mapping[str, Any],
               given: Any) -> list[dict[str, Any]]:
    """The envelope's citations, with a classical passage named by its book and chapter
    when the result carried only its id."""
    from .envelope import extract_citations
    cites = extract_citations(jsonable(result), gov, given=given or ())
    for c in cites:
        if c["kind"] == "classical" and c["label"] == c["evidence_ref"]:
            passage = _passage(c["evidence_ref"])
            if passage is not None:
                c["label"] = f"《{passage.source}》{passage.chapter}".strip()
    return cites


def _passage(passage_id: str) -> Any:
    try:
        from bioagent.tcm.knowledge import default_knowledge
        return default_knowledge().passages.get(passage_id)
    except Exception:                                           # noqa: BLE001
        return None


# ===================================================================== kernel runtime

_RUNTIME: Any = None
_RUNTIME_ERROR: str = ""


def _runtime() -> Any:
    """BioScience's runtime over the native tools and the public connectors, built once.
    Every native and connector call goes through its policy kernel."""
    global _RUNTIME, _RUNTIME_ERROR
    with _ENV_LOCK:
        if _RUNTIME is None and not _RUNTIME_ERROR:
            try:
                from bioagent.psh.assembly import default_runtime
                _RUNTIME = default_runtime(catalogue=False, skills=False, tooluniverse=False,
                                           mcp_credentials=None)
            except Exception as exc:                            # noqa: BLE001
                _RUNTIME_ERROR = f"{type(exc).__name__}: {exc}"
        if _RUNTIME is None:
            raise RuntimeError(f"the BioScience runtime could not be built: {_RUNTIME_ERROR}")
        return _RUNTIME


def _spec(ctx: Context) -> Any:
    from bioagent.runtime.agentspec import AgentSpec
    return AgentSpec(name="tcmstudio", permission_profile=ctx.profile, purpose=ctx.purpose)


def _call_result(res: Any, entry: Mapping[str, Any], ctx: Context, *, kind: str,
                 licences: list[dict[str, Any]], limits: list[str]) -> Outcome:
    status = getattr(getattr(res, "status", None), "value", str(getattr(res, "status", "")))
    g = gv.from_call_result(res, kind=kind, licences=licences, limits=limits)
    meta = dict(getattr(res, "metadata", None) or {})
    receipt = {"kernel": "bioagent.policy", "adapter": getattr(res, "adapter", "")}
    request = {k: meta[k] for k in ("url", "method", "host", "http_status", "cached",
                                    "attempts", "fetched_at") if k in meta}
    if request:
        receipt["request"] = request
    error = str(getattr(res, "error", "") or "")
    if status in ("SUCCEEDED", "DEGRADED"):
        o = Outcome(result=getattr(res, "value", None), governance=g, receipt=receipt)
        if status == "DEGRADED":
            o.notes.append(f"the call succeeded in a degraded mode: {error}" if error
                           else "the call succeeded in a degraded mode")
        return o
    if status == "DENIED":
        network = any(r["code"].startswith("perm.network") for r in g["refusals"])
        if network and not ctx.network:
            o = _failure(kind, "network_off", f"Web access is off for this project: {error}",
                         gv.remedy_for("NETWORK_OFF"), summary="未联网：本项目未开启网络访问",
                         refusals=g["refusals"], limitations=limits)
        else:
            code = g["refusals"][0]["code"] if g["refusals"] else "policy"
            o = _failure(kind, "refused", error or "the policy kernel refused the call",
                         gv.remedy_for(code), status="refused",
                         summary=f"已拒绝（{code}）：内核判定不予执行", refusals=g["refusals"],
                         limitations=limits)
        o.governance.update({k: g[k] for k in ("licences", "policy") if k in g})
        o.receipt = receipt
        return o
    if status == "TIMEOUT":
        etype = "timeout"
    elif status == "UNAVAILABLE":
        etype = "unavailable"
    elif status == "CANCELLED":
        o = _failure(kind, "runtime_error", error or "cancelled", status="cancelled",
                     summary="已取消")
        o.receipt = receipt
        return o
    elif error.startswith("signature mismatch") or error.split(":", 1)[0] in (
            "ValueError", "TypeError", "KeyError", "ArgumentError", "IntakeError"):
        etype = "bad_arguments"
    else:
        etype = "runtime_error"
    message = error.removeprefix("ValueError: ") if etype == "bad_arguments" else error
    hint = ""
    if etype == "bad_arguments":
        hint = _signature(entry["parameters"])
        m = re.search(r"Did you mean '([^']+)'", error)
        if m:
            hint = f"Did you mean {m.group(1)!r}? " + hint
    o = _failure(kind, etype, message or status.lower(), hint, limitations=limits)
    o.governance.update({k: g[k] for k in ("licences", "policy") if k in g})
    o.receipt = receipt
    return o


# =========================================================================== natives

_SEED_LICENCE = {"asset": "tcmscience.tcm.seed", "licence": "MIT",
                 "note": "TCMScience seed corpus, compiled into the package"}
_KB_TOOLS = {"tcm_lookup", "tcm_herb", "tcm_formula", "tcm_syndrome", "tcm_classical_search",
             "tcm_compatibility", "tcm_applicability", "tcm_evidence_tiers"}


def _native_limits(name: str, domain: str, value: Any) -> list[str]:
    limits: list[str] = []
    if name in _KB_TOOLS and name != "tcm_evidence_tiers":
        if name in ("tcm_compatibility", "tcm_herb"):
            limits.append(gv.LIMIT_NO_RECORD)
        if name != "tcm_lookup":
            limits.append(gv.LIMIT_CLASSICAL)
        limits.append(gv.LIMIT_SEED)
    elif name in ("hkbu_formula_lookup", "hkcmms_standard_lookup", "hk_cmm_dna_lookup"):
        limits.append(gv.LIMIT_HUB)
    elif domain in ("clinical-calculators", "pharmacology"):
        limits.append(gv.LIMIT_CALCULATOR)
    elif domain in ("statistics", "survival-analysis") and _has_key(value, ("p_value", "q_value",
                                                                            "p_values")):
        limits.append(gv.LIMIT_NOT_SIGNIFICANT)
    return limits


def _has_key(value: Any, keys: tuple[str, ...], depth: int = 0) -> bool:
    if depth > 4:
        return False
    if isinstance(value, Mapping):
        return any(k in value for k in keys) or any(_has_key(v, keys, depth + 1)
                                                    for v in list(value.values())[:50])
    if isinstance(value, list):
        return any(_has_key(v, keys, depth + 1) for v in value[:20])
    return False


def _exec_native(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    name = entry["id"].split(".", 1)[1]
    domain = entry.get("domain", "")
    licences = [gv.licence_entry(f"native.{name}", "MIT", note="tool code (BioScience-Harness)")]
    if name in _KB_TOOLS:
        licences.append(gv.licence_entry(**_SEED_LICENCE))
    for key in entry.get("data") or ():
        try:
            from bioagent.tcmdb.datasets import dataset
            licences.append(gv.licence_entry(key, dataset(key).license,
                                             note="licence of the records returned"))
        except Exception:                                       # noqa: BLE001
            licences.append(gv.licence_entry(key, "not stated"))
    try:
        rt = _runtime()
    except RuntimeError as exc:
        return _failure("native", "unavailable", str(exc),
                        "The policy kernel is needed to run tools; check the bioagent install.")
    res = rt.invoke(f"native.tool.{name}", spec=_spec(ctx), **args)
    value = getattr(res, "value", None)
    o = _call_result(res, entry, ctx, kind="native", licences=licences,
                     limits=_native_limits(name, domain, value))
    if o.status == "succeeded":
        o.summary = _native_summary(name, entry["title"]["zh"], args, o.result)
    return o


def _native_summary(name: str, title: str, args: Mapping[str, Any], v: Any) -> str:
    if not isinstance(v, Mapping):
        return f"{title}：完成"
    try:
        if name == "tcm_compatibility":
            herbs = " + ".join(str(h) for h in args.get("herbs") or ())
            conflicts = v.get("conflicts") or []
            unresolved = v.get("unresolved") or []
            tail = f"；{len(unresolved)} 个名称未解析" if unresolved else ""
            if conflicts:
                kinds = "、".join(dict.fromkeys(
                    (str(c.get("description") or "").split("：", 1)[0] or c.get("kind", ""))
                    for c in conflicts))
                return f"{herbs}：记载 {len(conflicts)} 处配伍禁忌（{kinds}）{tail}"
            return f"{herbs}：种子语料中未见配伍禁忌记录（无记录不等于安全）{tail}"
        if name == "tcm_lookup":
            status = v.get("status")
            if status == "resolved" and v.get("entity"):
                e = v["entity"]
                return f"{v.get('query')}：解析为 {e.get('chinese', '')}（{e.get('id', '')}）"
            if status == "ambiguous":
                names = "、".join(c.get("chinese", "") for c in v.get("candidates") or [])
                return f"{v.get('query')}：存在歧义，候选 {names}"
            return f"{v.get('query')}：种子语料中未找到（不等于不存在）"
        if name == "tcm_herb" and v.get("herb"):
            h = v["herb"]
            return (f"{h.get('chinese')}：{h.get('nature', '')}，{'、'.join(h.get('flavours') or [])}；"
                    f"归{'、'.join(h.get('meridians') or [])}经；{len(v.get('safety') or [])} 条安全性记录（记载）")
        if name == "tcm_formula" and v.get("formula"):
            f = v["formula"]
            return (f"{f.get('chinese')}（{f.get('source', '')}）：{len(v.get('ingredients') or [])} 味，"
                    "记载的组成与主治（记载，非疗效证据）")
        if name == "tcm_syndrome" and v.get("syndrome"):
            s = v["syndrome"]
            return (f"{s.get('chinese')}：治法 {s.get('treatment_principle', '')}；记载方剂 "
                    f"{len(v.get('formulas') or [])} 首")
        if name == "tcm_classical_search":
            return f"经典条文检索「{v.get('query')}」：{v.get('count', 0)} 条（记载，非疗效证据）"
        if name == "tcm_applicability":
            verdict = "可支撑" if v.get("licensed") else "不能支撑"
            subj = (v.get("subject") or {}).get("chinese") or args.get("subject")
            obj_ = (v.get("object") or {}).get("chinese") or args.get("object")
            kind = _CLAIM_ZH.get(str(v.get("claim_kind")), v.get("claim_kind"))
            return (f"{subj} → {obj_}：现有记载{verdict}「{kind}」主张"
                    f"（需要 {v.get('required_tier_zh', v.get('required_tier', ''))}）")
        if name in ("hkbu_formula_lookup", "hkcmms_standard_lookup", "hk_cmm_dna_lookup") \
                and v.get("status") == "not_loaded":
            return f"{title}：本地数据未导入（not_loaded）"
    except Exception:                                           # noqa: BLE001
        pass
    if isinstance(v.get("p_value"), (int, float)):
        return f"{title}：p = {v['p_value']:.3g}（不显著 ≠ 无关）"
    shown = []
    for key, value in v.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        if isinstance(value, str) and (len(value) > 40 or not value.strip()):
            continue
        shown.append(f"{key} = {value:.4g}" if isinstance(value, float) else f"{key} = {value}")
        if len(shown) == 3:
            break
    return f"{title}：" + ("，".join(shown) if shown else "完成")


# ======================================================================== connectors

def _exec_connector(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    info = entry["connector"]
    from bioagent.providers.public_apis import BY_KEY
    from bioagent.psh.arguments import ArgumentError, arguments_for

    key, op = info["key"], info["operation"]
    licences = [gv.licence_entry(f"connector.{key}", info.get("license"),
                                 note=f"{info.get('name')} ({info.get('host')})")]
    try:
        kwargs = arguments_for({"operation": op, **args}, source=BY_KEY[key],
                               component_id=f"public.connector.{key}")
    except ArgumentError as exc:
        return _failure("connector", "bad_arguments", str(exc), _signature(entry["parameters"]))
    try:
        rt = _runtime()
    except RuntimeError as exc:
        return _failure("connector", "unavailable", str(exc), "")
    res = rt.invoke(f"public.connector.{key}", spec=_spec(ctx), **kwargs)
    o = _call_result(res, entry, ctx, kind="connector", licences=licences,
                     limits=[gv.LIMIT_LIVE])
    if o.status == "succeeded":
        n = _count_records(o.result)
        http = o.receipt.get("request", {}).get("http_status")
        o.summary = (f"{info.get('name')} · {op}：已返回" + (f" {n} 条记录" if n is not None else "")
                     + (f"（HTTP {http}）" if http else ""))
    return o


def _count_records(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, Mapping):
        for k in ("results", "result", "items", "data", "hits", "entries", "records"):
            v = value.get(k)
            if isinstance(v, list):
                return len(v)
            if isinstance(v, Mapping):
                inner = _count_records(v)
                if inner is not None:
                    return inner
    return None


def _literature_search(args: Mapping[str, Any], ctx: Context) -> tuple[Outcome, str]:
    source = args.get("source") or "europepmc"
    query = str(args["query"])
    limit = int(args.get("limit") or 10)
    limits = ["A search hit is a pointer to read, not evidence: no claim is licensed until "
              "the study itself has been assessed.", gv.LIMIT_LIVE]
    if source == "pubmed":
        via = "connector.ncbi_eutils.esearch"
        first = _run_connector_entry(via, {"db": "pubmed", "term": query, "retmax": limit}, ctx)
        if first.status != "succeeded":
            return first, via
        ids = [str(i) for i in ((first.result or {}).get("esearchresult") or {}).get("idlist") or []]
        total = ((first.result or {}).get("esearchresult") or {}).get("count")
        hits: list[dict[str, Any]] = []
        if ids:
            second = _run_connector_entry("connector.ncbi_eutils.esummary",
                                          {"db": "pubmed", "id": ",".join(ids)}, ctx)
            if second.status != "succeeded":
                return second, "connector.ncbi_eutils.esummary"
            docs = (second.result or {}).get("result") or {}
            for pid in ids:
                d = docs.get(pid) or {}
                doi = next((a.get("value") for a in d.get("articleids") or ()
                            if a.get("idtype") == "doi"), "")
                hits.append({"title": d.get("title", ""), "pmid": pid, "doi": doi or "",
                             "journal": d.get("fulljournalname") or d.get("source", ""),
                             "year": str(d.get("pubdate", ""))[:4],
                             "authors": ", ".join(a.get("name", "") for a in d.get("authors") or ()[:6])})
        o = first
    elif source == "crossref":
        via = "connector.crossref.search"
        o = _run_connector_entry(via, {"query": query, "rows": limit}, ctx)
        if o.status != "succeeded":
            return o, via
        msg = (o.result or {}).get("message") or {}
        total = msg.get("total-results")
        hits = []
        for it in msg.get("items") or ():
            title = (it.get("title") or [""])[0] if isinstance(it.get("title"), list) else it.get("title", "")
            parts = (((it.get("issued") or {}).get("date-parts") or [[None]])[0] or [None])
            hits.append({"title": title, "doi": it.get("DOI", ""),
                         "journal": (it.get("container-title") or [""])[0]
                         if isinstance(it.get("container-title"), list) else "",
                         "year": str(parts[0] or ""),
                         "authors": ", ".join(f"{a.get('family', '')} {a.get('given', '')}".strip()
                                              for a in (it.get("author") or [])[:6])})
    else:
        via = "connector.europepmc.search"
        o = _run_connector_entry(via, {"query": query, "page_size": limit}, ctx)
        if o.status != "succeeded":
            return o, via
        total = (o.result or {}).get("hitCount")
        hits = []
        for r in ((o.result or {}).get("resultList") or {}).get("result") or ():
            hits.append({"title": r.get("title", ""), "pmid": r.get("pmid", ""),
                         "pmcid": r.get("pmcid", ""), "doi": r.get("doi", ""),
                         "journal": r.get("journalTitle", ""), "year": r.get("pubYear", ""),
                         "authors": r.get("authorString", ""), "source": r.get("source", "")})
    hits = [{k: v for k, v in h.items() if v not in ("", None)} for h in hits[:limit]]
    name = {"pubmed": "PubMed", "crossref": "Crossref"}.get(source, "Europe PMC")
    result = {"source": source, "query": query, "total": total, "count": len(hits), "hits": hits}
    g = o.governance or gv.empty("connector")
    g["limitations"] = limits
    out = Outcome(result=result, governance=g, receipt=o.receipt,
                  summary=f"文献检索（{name}）：返回 {len(hits)} 条"
                          + (f"，共 {total} 条命中" if total not in (None, "") else "")
                          + "（命中不等于证据）")
    return out, via


def _run_connector_entry(entry_id: str, args: dict[str, Any], ctx: Context) -> Outcome:
    entry = get_entry(entry_id)
    if entry is None:
        return _failure("connector", "not_found", f"no connector entry {entry_id!r}")
    blocked = _preflight(entry, args, ctx)
    return blocked if blocked is not None else _exec_connector(entry, args, ctx)


# ======================================================================= project state

_TEMP_ROOT: Path | None = None
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _temp_root() -> Path:
    global _TEMP_ROOT
    with _ENV_LOCK:
        if _TEMP_ROOT is None:
            _TEMP_ROOT = Path(tempfile.mkdtemp(prefix="tcmstudio-state-"))
        return _TEMP_ROOT


def _safe_id(project_id: str | None) -> str:
    raw = str(project_id or "default")
    cleaned = _SAFE.sub("_", raw).strip("._")[:80]
    if not cleaned or cleaned != raw:
        import hashlib
        cleaned = (cleaned[:60] + "-" if cleaned else "p-") + hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:12]
    return cleaned


@dataclass
class _ProjectPaths:
    base: Path
    durable: bool

    @property
    def psh(self) -> Path:
        return self.base / "psh"

    @property
    def runs(self) -> Path:
        return self.base / "runs"

    @property
    def clinic(self) -> Path:
        return self.base / "clinic"


def _project(ctx: Context) -> _ProjectPaths:
    if ctx.state_root:
        return _ProjectPaths(Path(ctx.state_root) / _safe_id(ctx.project_id),
                             ctx.durable if ctx.durable is not None else True)
    return _ProjectPaths(_temp_root() / _safe_id(ctx.project_id), False)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


_STATE_LOCKS: dict[str, threading.Lock] = {}


def _state_lock(path: Path) -> threading.Lock:
    # One governed run at a time per audit chain: the chain head is read and extended by
    # each run, and the runner serves requests on several threads.
    with _ENV_LOCK:
        return _STATE_LOCKS.setdefault(str(path), threading.Lock())


# =========================================================================== skills

_VIEW_ROOT: Path | None = None


def _skill_view(spec: Mapping[str, Any]) -> Path:
    """A directory holding only this skill, for ``run_governed``.

    ``run_governed`` hashes every skill in the directory it is given before hashing its
    target again; on a directory of one skill it hashes two instead of six, about three
    times faster. The content hash does not depend on the directory's location, so the
    pin in the lockfile still applies, and the lockfile itself is passed explicitly."""
    global _VIEW_ROOT
    with _ENV_LOCK:
        if _VIEW_ROOT is None:
            _VIEW_ROOT = Path(tempfile.mkdtemp(prefix="tcmstudio-skills-"))
        root = _VIEW_ROOT
    src = Path(spec["directory"])
    dst = root / spec["id"] / spec["id"]
    dst.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for path in src.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        rel = path.relative_to(src)
        wanted.add(rel)
        target = dst / rel
        data = path.read_bytes()
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    for path in list(dst.rglob("*")):
        if path.is_file() and path.relative_to(dst) not in wanted:
            path.unlink()
    return root / spec["id"]


_SKILL_LIMITS = {
    "assess-tcm-safety": [gv.LIMIT_NO_RECORD, gv.LIMIT_SEED],
    "normalize-tcm-entities": [gv.LIMIT_SEED],
    "retrieve-tcm-evidence": [gv.LIMIT_CLASSICAL, gv.LIMIT_SEED],
    "analyze-tcm-network-pharmacology": [gv.LIMIT_PREDICTED, gv.LIMIT_SEED],
    "draft-tcm-prescription": [gv.LIMIT_DRAFT, gv.LIMIT_NOT_EXHAUSTIVE_CHECK],
    "predict-protein-structure": [gv.LIMIT_PREDICTED],
    "dock-ligands": [gv.LIMIT_PREDICTED],
    "predict-admet": [gv.LIMIT_PREDICTED],
}


def _exec_skill(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    sid = entry["id"].split(".", 1)[1]
    spec = next((s for s in skill_specs() if s["id"] == sid), None)
    if spec is None:
        return _failure("skill", "not_found", f"no manifest for skill {sid!r}")
    if entry.get("job"):
        return _submit_job("skill.run", {"skill_id": sid, "arguments": args,
                                         "allow_unpinned": not spec["pinned"]}, ctx, entry)
    from bioagent.governed import GovernedRunRefused, run_governed

    project = _project(ctx)
    run_id = _new_id("r")
    run_dir = project.runs / run_id
    out_dir = run_dir / "out"
    call_args = dict(args)
    if sid == "draft-tcm-prescription":
        run_dir.mkdir(parents=True, exist_ok=True)
        intake_path = run_dir / "intake.json"
        intake_path.write_text(json.dumps(args.get("intake") or {}, ensure_ascii=False,
                                          sort_keys=True), encoding="utf-8")
        call_args["intake"] = str(intake_path)
        call_args["modifications"] = json.dumps(list(args.get("modifications") or []),
                                                ensure_ascii=False)
        call_args["out_dir"] = str(run_dir / "clinic")
    view = _skill_view(spec)
    lockfile = spec.get("lockfile") or None
    project.psh.mkdir(parents=True, exist_ok=True)
    try:
        with _state_lock(project.psh):
            run = run_governed(sid, call_args, skill_dir=view, state_dir=project.psh,
                               output_dir=out_dir, lockfile=lockfile,
                               allow_unpinned=not spec["pinned"])
    except GovernedRunRefused as exc:
        return _failure("skill", "refused", str(exc), gv.remedy_for("GovernedRunRefused"),
                        status="refused", summary=f"{entry['title']['zh']}：已拒绝（未准入受治理运行）",
                        refusals=[gv.refusal("GovernedRunRefused", str(exc))],
                        limitations=_SKILL_LIMITS.get(sid, []))
    except (ImportError, ModuleNotFoundError) as exc:
        return _failure("skill", "unavailable", f"{type(exc).__name__}: {exc}",
                        gv.remedy_for("MISSING_DEPENDENCY"))
    except (ValueError, TypeError, KeyError) as exc:
        return _failure("skill", "bad_arguments", f"{type(exc).__name__}: {exc}",
                        _signature(entry["parameters"]))
    except Exception as exc:                                    # noqa: BLE001
        return _failure("skill", "runtime_error", f"{type(exc).__name__}: {exc}")
    g = gv.from_governed_run(run, limits=_SKILL_LIMITS.get(sid, []))
    doc = g["artifact"] or {}
    verdict = g["verdict"] or {}
    outputs = {o["path"]: o.get("content") for o in g["outputs"]}
    result = {"skill_id": sid, "run_id": run_id, "released": run.released,
              "content_hash": run.content_hash, "audit_head": run.audit_head,
              "pinned": bool(run.lockfile), "outputs": outputs, "artifact": doc,
              "verdict": verdict,
              "governed": {"state_dir": run.state_dir, "output_dir": run.output_dir,
                           "lockfile": run.lockfile, "operations": jsonable(run.operations),
                           "durable": project.durable}}
    view_model = {"skill": sid, "released": run.released, "question": doc.get("question"),
                  "status": doc.get("status"), "outputs": outputs,
                  "evidence": [{k: e.get(k) for k in ("id", "design", "tier", "citation",
                                                      "quote", "quote_verified")}
                               for e in (doc.get("evidence") or [])[:20]]}
    states = verdict.get("states") or {}
    passed = sum(1 for v in states.values() if v)
    tail = " · 已准予发布" if run.released else f" · 未准予发布（{passed}/{len(states) or 6}）"
    summary = _skill_summary(sid, entry["title"]["zh"], args, outputs) + tail
    governed = (doc.get("provenance") or {}).get("governed") or {}
    return Outcome(result=result, model_view=view_model, summary=summary, governance=g,
                   content_hash=run.content_hash, audit_head=run.audit_head or None,
                   composite_version=doc.get("composite_version_string"),
                   # The chain records every run under the skill id; the anchor (the chain
                   # head when this run started) is what tells runs of one project apart.
                   receipt={"durable": project.durable, "run_id": run_id,
                            "run_anchor": governed.get("run_anchor"),
                            "skill_pinned": bool(run.lockfile)})


def _skill_summary(sid: str, title: str, args: Mapping[str, Any],
                   outputs: Mapping[str, Any]) -> str:
    try:
        if sid == "assess-tcm-safety":
            c = outputs.get("safety.json") or {}
            names = " + ".join([str(args.get("subject", ""))] + [str(x) for x in
                                                                 args.get("co_administered") or ()])
            if c.get("status") == "unknown" or (not c.get("records") and c.get("unresolved")):
                return f"{names}：种子语料中未能解析或未见记录（状态 unknown，不等于安全）"
            conflicts = c.get("combination_conflicts") or []
            records = c.get("records") or []
            critical = c.get("critical_records") or []
            head = (f"{names}：记载 {len(conflicts)} 处配伍禁忌（十八反等）；" if conflicts
                    else f"{names}：未见配伍禁忌记录；")
            return head + f"{len(records)} 条安全性记录，{len(critical)} 条为高或严重级别（记载，非临床安全性结论）"
        if sid == "normalize-tcm-entities":
            qs = (outputs.get("entities.json") or {}).get("queries") or []
            resolved = sum(1 for q in qs if q.get("status") == "resolved")
            ambiguous = sum(1 for q in qs if q.get("status") == "ambiguous")
            return f"{len(qs)} 个名称：{resolved} 个解析，{ambiguous} 个存在歧义，{len(qs) - resolved - ambiguous} 个未解析"
        if sid == "retrieve-tcm-evidence":
            c = outputs.get("evidence.json") or {}
            ev = c.get("evidence") or []
            designs = "、".join(dict.fromkeys(_DESIGN_ZH.get(str(e.get("design", "")),
                                                          str(e.get("design", ""))) for e in ev)) or "无"
            return f"{args.get('subject')}：检索到 {len(ev)} 条记录（设计：{designs}）"
        if sid == "analyze-tcm-network-pharmacology":
            c = outputs.get("network.json") or {}
            p = c.get("provenance_summary") or {}
            return (f"{args.get('formula_name')}：组成 {len(c.get('ingredients') or [])} 味；"
                    f"实测边 {p.get('edges_measured', 0)}、预测边 {p.get('edges_predicted', 0)}"
                    "（种子语料；预测 ≠ 实测）")
    except Exception:                                           # noqa: BLE001
        pass
    return f"{title}：受治理运行完成"


# =========================================================================== clinic

_PACK: Any = None
_SESSION_RE = re.compile(r"^cs_[0-9a-f]{12}$")


def _pack() -> Any:
    global _PACK
    with _ENV_LOCK:
        if _PACK is None:
            from bioagent.clinic.pack import load_pack
            _PACK = load_pack()
        return _PACK


def _clinic_governance(pack: Any, limits: list[str]) -> dict[str, Any]:
    g = gv.empty("clinic")
    review = "" if getattr(pack, "reviewed", False) else (
        f"知识包 {pack.version} 未经执业中医师审核（{pack.review_status}）")
    g["limitations"] = [*limits, *([review] if review else [])]
    g["licences"] = [gv.licence_entry(
        "bioagent.clinic.pack", "MIT",
        note=f"clinical knowledge pack {pack.version}: a transcription of the textbooks and the "
             "Pharmacopoeia it names; " + ("reviewed" if pack.reviewed else "a draft, not reviewed"))]
    return g


def _exec_clinic(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    op = entry["id"].split(".", 1)[1]
    from bioagent.clinic.intake import IntakeError
    try:
        pack = _pack()
    except Exception as exc:                                    # noqa: BLE001
        return _failure("clinic", "unavailable", f"the clinic knowledge pack could not be "
                                                 f"loaded: {exc}")
    try:
        if op == "template":
            from bioagent.clinic.intake import template
            doc = template(pack)
            g = _clinic_governance(pack, [])
            return Outcome(result=doc, governance=g,
                           summary=f"四诊采集模板（知识包 {pack.version}"
                                   + ("" if pack.reviewed else "，未经执业中医师审核") + "）")
        if op == "assess":
            return _clinic_assess(args, ctx, pack)
        if op == "check":
            return _clinic_check(args, pack)
        if op == "followup":
            from bioagent.clinic.followup import assess_followup, parse_visit
            r = assess_followup(parse_visit(args["baseline"]), parse_visit(args["current"]),
                                pack, syndrome=args.get("syndrome"))
            d = r.as_dict()
            g = _clinic_governance(pack, ["It describes change in the recorded scores; it does "
                                          "not show that the treatment caused the change.",
                                          gv.LIMIT_DRAFT.split(";")[0] + "."])
            red = d.get("reduction")
            red_s = f"，减分率 {red:.0%}" if isinstance(red, (int, float)) else ""
            return Outcome(result=d, governance=g,
                           summary=f"复诊：证候积分 {d.get('baseline_total')} → {d.get('current_total')}"
                                   f"{red_s}（{d.get('category', '')}；描述变化，不归因于治疗）")
        if op == "verify":
            sid = str(args["session_id"])
            if not _SESSION_RE.match(sid):
                return _failure("clinic", "bad_arguments",
                                f"{sid!r} is not a session id (cs_ and 12 hex digits)")
            from bioagent.clinic.session import verify
            folder = _project(ctx).clinic / sid
            if not (folder / "session.json").is_file():
                return _failure("clinic", "not_found",
                                f"no session {sid} in this project's clinic folder")
            ok, problems = verify(folder)
            g = _clinic_governance(pack, [])
            return Outcome(result={"session_id": sid, "verified": ok, "problems": problems},
                           governance=g,
                           summary=f"诊疗记录 {sid}：" + ("核验通过" if ok else f"核验未通过（{len(problems)} 处）"))
    except ImportError as exc:
        return _failure("clinic", "unavailable", f"{type(exc).__name__}: {exc}",
                        gv.remedy_for("MISSING_DEPENDENCY"))
    except IntakeError as exc:
        return _failure("clinic", "bad_arguments", f"intake: {exc}",
                        "The intake follows bioagent.clinic.intake/1; clinic.template shows it.")
    except (ValueError, KeyError, TypeError) as exc:
        return _failure("clinic", "bad_arguments", f"{type(exc).__name__}: {exc}")
    return _failure("clinic", "not_found", f"no clinic operation {op!r}")


def _clinic_assess(args: Mapping[str, Any], ctx: Context, pack: Any) -> Outcome:
    from bioagent.clinic.session import assess

    project = _project(ctx)
    session_id = "cs_" + secrets.token_hex(6)
    folder = project.clinic / session_id
    session = assess(dict(args["intake"]), folder, pack=pack,
                     modifications=[dict(m) for m in args.get("modifications") or ()],
                     apply_textbook=bool(args.get("apply_textbook", False)),
                     days=int(args.get("days", 7)))
    outputs = gv.outputs_from(folder, ["session.json", "report.md", "report.html"])
    doc = next((o["content"] for o in outputs if o["path"] == "session.json"), None) or {}
    g = _clinic_governance(pack, [gv.LIMIT_DRAFT, gv.LIMIT_NOT_EXHAUSTIVE_CHECK])
    g["outputs"] = outputs
    for w in doc.get("warnings") or ():
        if w not in g["limitations"]:
            g["limitations"].append(str(w))
    result = {"session_id": session_id, **doc}
    rx = doc.get("prescription") or {}
    diff = doc.get("differentiation") or {}
    status = doc.get("status", session.status)
    parts = [doc.get("status_zh") or status]
    if status == "refer":
        flags = [f.get("id") for f in doc.get("red_flags") or () if not f.get("cleared")]
        if flags:
            parts.append("未排除红旗征：" + "、".join(str(f) for f in flags))
    elif rx.get("formula"):
        parts.append(f"{rx.get('syndrome', '')} → {rx.get('formula')}（{len(rx.get('lines') or [])} 味，"
                     f"{rx.get('total_g', 0):g} g）")
        stops = [i for i in rx.get("issues") or () if i.get("severity") in ("stop", "block")]
        if stops:
            parts.append(f"{len(stops)} 个须医师处理的问题")
    elif status == "needs_information":
        parts.append(f"待补充问诊 {len(diff.get('questions') or [])} 项")
    view = {k: result.get(k) for k in ("session_id", "status", "status_zh", "warnings",
                                       "red_flags", "prescription")}
    view["differentiation"] = {"status": diff.get("status"), "rule": diff.get("rule"),
                               "candidates": [{k: c.get(k) for k in (
                                   "syndrome", "status", "score", "principle", "formula",
                                   "contradictions")} for c in (diff.get("candidates") or [])[:5]],
                               "questions": (diff.get("questions") or [])[:10]}
    return Outcome(result=result, model_view=view, governance=g, summary=" · ".join(parts),
                   receipt={"durable": project.durable, "session_id": session_id})


def _clinic_check(args: Mapping[str, Any], pack: Any) -> Outcome:
    from bioagent.clinic.intake import parse_intake
    from bioagent.clinic.prescribe import _line_for, age_fraction, check

    intake = parse_intake(dict(args["intake"]), pack)
    fraction, note = age_fraction(pack, intake.patient.age)
    lines, notices = [], []
    for item in args["herbs"]:
        line, issues = _line_for(pack, str(item["herb"]), float(item["grams"]), fraction,
                                 basis="practitioner")
        lines.append(line)
        notices += issues
    issues = list(check(lines, intake, pack)) + notices
    issue_docs = [d for d in (jsonable(i) for i in issues) if isinstance(d, dict)]
    stop = any(d.get("severity") == "stop" for d in issue_docs if isinstance(d, dict))
    counts: dict[str, int] = {}
    for d in issue_docs:
        counts[d.get("severity", "?")] = counts.get(d.get("severity", "?"), 0) + 1
    result = {"lines": [jsonable(line) for line in lines],
              "issues": issue_docs, "stop": stop, "age_fraction": fraction,
              "age_note": note, "intake_warnings": list(intake.warnings),
              "note": "no issues found by the pack's rules (this is not a safety guarantee)"
              if not issue_docs else ""}
    g = _clinic_governance(pack, [gv.LIMIT_NOT_EXHAUSTIVE_CHECK])
    if issue_docs:
        order = ("stop", "block", "warn", "info")
        summary = "处方核查：" + "，".join(f"{_SEVERITY_ZH[k]} {counts[k]} 项" for k in order
                                         if counts.get(k))
        if stop:
            summary += "（含不可签署问题）"
    else:
        summary = "处方核查：知识包规则未发现问题（不等于安全）"
    return Outcome(result=result, governance=g, summary=summary)


# ========================================================================= data hub

def _hub(ctx: Context) -> Any:
    from bioagent.tcmdb import TCMDataHub
    return TCMDataHub(ctx.tcmdb_root or None)


def _exec_tcmdb(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    op = entry["id"].split(".", 1)[1]
    from bioagent.tcmdb import HubError
    try:
        return _tcmdb_op(op, args, ctx)
    except HubError as exc:
        text = str(exc)
        if "not built" in text or "no store" in text:
            return _failure("tcmdb", "unavailable", text,
                            "Fetch and build the dataset on the runner first (job.tcmdb.fetch, "
                            "then job.tcmdb.build).", limitations=[gv.LIMIT_HUB])
        return _failure("tcmdb", "bad_arguments", text, limitations=[gv.LIMIT_HUB])
    except KeyError as exc:
        return _failure("tcmdb", "bad_arguments", f"unknown dataset or key: {exc}")
    except ValueError as exc:
        return _failure("tcmdb", "bad_arguments", str(exc))


def _tcmdb_op(op: str, args: Mapping[str, Any], ctx: Context) -> Outcome:
    g = gv.empty("tcmdb")
    g["limitations"] = [gv.LIMIT_HUB]
    if op == "catalog":
        from bioagent.tcmdb import catalog as cards
        items = list(cards())
        if args.get("module"):
            items = [c for c in items if str(args["module"]).upper() in c.modules]
        if args.get("access"):
            items = [c for c in items if c.access == args["access"]
                     or c.access.startswith(str(args["access"]))]
        q = str(args.get("query") or "").casefold().strip()
        if q:
            items = [c for c in items if q in " ".join(str(x) for x in (
                c.name, c.license, c.assessment, c.barriers, c.connector or "", c.dataset or "",
                c.url)).casefold()]
        docs = [c.as_dict() for c in items]
        if ctx.where == "runner":
            hub = _hub(ctx)
            for d in docs:
                if d.get("dataset"):
                    try:
                        st = hub.status(d["dataset"])[0]
                        d["local"] = {k: st.get(k) for k in ("built", "files_present",
                                                             "files_default", "missing_default")}
                    except Exception:                           # noqa: BLE001
                        d["local"] = None
        else:
            g["limitations"].append("Local download/build status is known only on the runner.")
        g["licences"] = [gv.licence_entry(f"source:{d['name']}", d.get("license"),
                                          note=f"commercial use: {d.get('commercial_use', 'unknown')}")
                         for d in docs[:40]]
        return Outcome(result={"count": len(docs), "cards": docs}, governance=g,
                       summary=f"中医药数据源目录：{len(docs)} 张数据源卡片")
    if op == "datasets":
        from bioagent.tcmdb import DATASETS
        from bioagent.tcmdb.spec import licence_class
        q = str(args.get("query") or "").casefold().strip()
        out = []
        for d in DATASETS:
            if args.get("access") and d.access != args["access"]:
                continue
            text = f"{d.key} {d.name} {d.license} {' '.join(d.relations)}".casefold()
            if q and q not in text:
                continue
            out.append({"key": d.key, "name": d.name, "access": d.access, "version": d.version,
                        "license": d.license, "licence_class": licence_class(d.license),
                        "commercial_use": d.commercial_use, "relations": list(d.relations),
                        "homepage": d.homepage,
                        "expected_bytes_default": sum(f.expected_bytes or 0 for f in d.files
                                                      if not f.optional) or None,
                        "hosts": sorted({f.url.split("/")[2] for f in d.files if "://" in f.url}),
                        "instructions": d.instructions[:400] if d.access == "manual" else ""})
        return Outcome(result={"count": len(out), "datasets": out}, governance=g,
                       summary=f"数据集说明：{len(out)} 个")
    if op == "relation_kinds":
        from bioagent.tcmdb import EVIDENCE, RELATION_KINDS
        from bioagent.tcmdb.rowkit import COLUMNS, EFFECTS, OUTCOMES
        kinds = RELATION_KINDS if isinstance(RELATION_KINDS, Mapping) else sorted(RELATION_KINDS)
        return Outcome(result={"relation_kinds": jsonable(kinds), "evidence": sorted(EVIDENCE),
                               "effects": sorted(EFFECTS), "outcomes": sorted(OUTCOMES),
                               "columns": list(COLUMNS)}, governance=g,
                       summary=f"关系类型 {len(kinds)} 种与证据词表")
    hub = _hub(ctx)
    if op == "status":
        rows = hub.status(args.get("dataset") or None)
        built = [r["dataset"] for r in rows if r.get("built")]
        return Outcome(result={"root": str(hub.root), "datasets": rows, "built": built},
                       governance=g, summary=f"本地数据：{len(built)}/{len(rows)} 个数据集已构建")
    if op == "tables":
        tables = hub.tables(args["dataset"])
        return Outcome(result={"dataset": args["dataset"], "tables": tables}, governance=g,
                       summary=f"{args['dataset']}：{len(tables)} 张表")
    if op == "query":
        rows = hub.query(args["dataset"], args["table"], where=args.get("where"),
                         contains=args.get("contains"), columns=args.get("columns"),
                         limit=int(args.get("limit", 50)), offset=int(args.get("offset", 0)))
        g["licences"] = _dataset_licences([args["dataset"]])
        return Outcome(result={"dataset": args["dataset"], "table": args["table"],
                               "count": len(rows), "rows": rows}, governance=g,
                       summary=f"{args['dataset']}.{args['table']}：{len(rows)} 行")
    if op in ("relations", "evidence_for"):
        if op == "relations":
            rows = hub.relations(args.get("kind"), subject=args.get("subject"),
                                 object=args.get("object"), sources=args.get("sources"),
                                 evidence=args.get("evidence"),
                                 contains=bool(args.get("contains", False)),
                                 outcomes=args.get("outcomes"),
                                 commercial=bool(args.get("commercial", False)),
                                 limit=int(args.get("limit", 50)))
        else:
            rows = hub.evidence_for(args["subject"], limit=int(args.get("limit", 50)),
                                    contains=bool(args.get("contains", False)))
        by_source: dict[str, int] = {}
        by_evidence: dict[str, int] = {}
        for r in rows:
            by_source[str(r.get("source"))] = by_source.get(str(r.get("source")), 0) + 1
            by_evidence[str(r.get("evidence"))] = by_evidence.get(str(r.get("evidence")), 0) + 1
        g["licences"] = _row_licences(rows)
        if any(e in by_evidence for e in ("predicted", "aggregated", "signal")):
            g["limitations"].insert(0, gv.LIMIT_PREDICTED)
        built = hub.built()
        if not built:
            g["limitations"].insert(0, "No dataset is built on this runner, so nothing could "
                                       "be found: an empty result here says nothing about the "
                                       "relation.")
        ev = "、".join(f"{k} {v}" for k, v in sorted(by_evidence.items(), key=lambda x: -x[1]))
        return Outcome(result={"count": len(rows), "by_source": by_source,
                               "by_evidence": by_evidence, "rows": rows, "built": built},
                       governance=g,
                       summary=f"关系查询：{len(rows)} 行，来自 {len(by_source)} 个数据源"
                               + (f"（证据类型：{ev}）" if ev else "（无记录不等于不存在）"))
    if op == "consensus":
        kw = {k: args[k] for k in ("sources", "contains", "merge_processed", "min_support")
              if k in args}
        doc = hub.consensus(args["kind"], subject=args.get("subject"), object=args.get("object"),
                            **kw)
        items = list(doc.get("items") or [])
        limit = int(args.get("limit", 50))
        if len(items) > limit:
            doc = {**doc, "items": items[:limit], "items_truncated": len(items) - limit}
        support = doc.get("support") or {}
        g["limitations"].insert(0, "Support counts independent sources, not truth; a source "
                                   "that is silent did not test the relation.")
        s = "、".join(f"{k} {v}" for k, v in support.items())
        return Outcome(result=doc, governance=g,
                       summary=f"多源一致性：{len(items)} 项" + (f"（{s}）" if s else ""))
    if op == "compare":
        kw = {k: args[k] for k in ("sources", "merge_processed") if k in args}
        doc = hub.compare(args["kind"], args["subject"], **kw)
        return Outcome(result=doc, governance=g, summary=f"来源比较：{args['subject']}")
    if op == "licences":
        rows = hub.licences()
        g["licences"] = [gv.licence_entry(f"{r['dataset']}:{r['kind']}", r.get("license"),
                                          commercial=bool(r.get("commercial"))) for r in rows]
        return Outcome(result={"count": len(rows), "licences": rows}, governance=g,
                       summary=f"数据许可：{len(rows)} 项")
    if op == "unresolved":
        rows = hub.unresolved(args["dataset"], limit=int(args.get("limit", 50)))
        return Outcome(result={"dataset": args["dataset"], "count": len(rows), "rows": rows},
                       governance=g, summary=f"{args['dataset']}：{len(rows)} 条未解析记录")
    return _failure("tcmdb", "not_found", f"no data-hub operation {op!r}")


def _dataset_licences(keys: list[str]) -> list[dict[str, Any]]:
    out = []
    for key in keys:
        try:
            from bioagent.tcmdb.datasets import dataset
            out.append(gv.licence_entry(key, dataset(key).license))
        except Exception:                                       # noqa: BLE001
            out.append(gv.licence_entry(key, "not stated"))
    return out


def _row_licences(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (str(r.get("source")), str(r.get("license") or "not stated"))
        seen[key] = seen.get(key, 0) + 1
    return [gv.licence_entry(src, lic, note=f"{n} row(s)") for (src, lic), n in seen.items()]


# =========================================================================== studies

def _exec_study(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    op = entry["id"].split(".", 1)[1]
    g = gv.empty("study")
    g["limitations"] = [gv.LIMIT_NOT_SIGNIFICANT]
    try:
        if op == "contrast_formula_monomer":
            from bioagent.studies.contrast import Group, contrast_formula_monomer

            def group(d: Mapping[str, Any]) -> Any:
                return Group(label=str(d["label"]), values=tuple(float(x) for x in d["values"]),
                             study=str(d["study"]), intervention=str(d.get("intervention", "")),
                             dose=str(d.get("dose", "")))
            r = contrast_formula_monomer(group(args["formula"]), group(args["monomer"]),
                                         higher_is_better=bool(args.get("higher_is_better", True)),
                                         margin=args.get("margin"), alpha=float(args.get("alpha", 0.05)),
                                         dose_matched=args.get("dose_matched"))
            g["limitations"] += list(r.get("may_not_conclude") or [])
            return Outcome(result=r, governance=g,
                           summary=f"复方与单体对照：判定 {r.get('verdict')}（仅限此终点、此模型、此剂量）")
        if op == "combination":
            from bioagent.studies.combination import Cell, analyse_combination
            cells = [Cell(dose_a=float(c["dose_a"]), dose_b=float(c["dose_b"]),
                          effect=tuple(float(x) for x in c["effect"]),
                          cytotoxicity=tuple(float(x) for x in c.get("cytotoxicity") or ()))
                     for c in args["cells"]]
            r = analyse_combination(cells, primary=args["primary"], seed=int(args.get("seed", 0)),
                                    boot=int(args.get("boot", 200)),
                                    cytotoxic_above=float(args.get("cytotoxic_above", 0.5)),
                                    level=float(args.get("level", 0.95)))
            g["limitations"].insert(0, "Excess over a reference model is not synergy of "
                                       "mechanism, and in vitro is not in people.")
            exceeds = sum(1 for c in r.get("cells") or () if c.get("exceeds_primary"))
            return Outcome(result=r, governance=g,
                           summary=f"联合用药分析（{args['primary']}）：{exceeds}/{len(r.get('cells') or [])} "
                                   "个组合高于参考模型")
        if op == "effect_modification":
            from bioagent.studies.heterogeneity import Feature, effect_modification
            mods = [Feature(name=str(m["name"]), values=tuple(float(x) for x in m["values"]),
                            baseline=bool(m["baseline"])) for m in args["modifiers"]]
            r = effect_modification([float(x) for x in args["outcome"]],
                                    [int(x) for x in args["treated"]], mods,
                                    baseline_outcome=args.get("baseline_outcome"),
                                    prespecified=args.get("prespecified"),
                                    level=float(args.get("level", 0.95)))
            return Outcome(result=r, governance=g,
                           summary=f"效应修饰分析：检验 {r.get('tested', len(mods))} 个基线特征"
                                   "（交互作用，不是应答者预测）")
        if op == "exposure_screen":
            from bioagent.studies.design import AssayResult, ExposureRecord, Measurement
            from bioagent.studies.exposure import screen_exposure

            def meas(d: Mapping[str, Any]) -> Any:
                return Measurement(qualifier=str(d["qualifier"]), value=d.get("value"),
                                   unit=str(d.get("unit", "")), bound=d.get("bound"))
            exposures = [ExposureRecord(
                molecule=str(e["molecule"]), species=str(e["species"]), site=str(e["site"]),
                time_h=e.get("time_h"), concentration=meas(e["concentration"]),
                free=bool(e.get("free", False)),
                free_fraction=tuple(e["free_fraction"]) if e.get("free_fraction") else None,
                method=str(e.get("method", "")), study=str(e.get("study", "")))
                for e in args["exposures"]]
            assays = [AssayResult(molecule=str(a["molecule"]), target=str(a["target"]),
                                  endpoint=str(a["endpoint"]), result=meas(a["result"]),
                                  species=str(a.get("species", "")), system=str(a.get("system", "")),
                                  method=str(a.get("method", "")),
                                  flags=tuple(a.get("flags") or ()), study=str(a.get("study", "")))
                      for a in args["assays"]]
            rows = screen_exposure(exposures, assays, site=str(args["site"]),
                                   mw=dict(args.get("mw") or {}) or None)
            verdicts: dict[str, int] = {}
            for row in rows:
                verdicts[str(row.get("verdict"))] = verdicts.get(str(row.get("verdict")), 0) + 1
            g["limitations"] = ["An exposure ratio is not target occupancy or a probability "
                                "of efficacy."]
            return Outcome(result={"site": args["site"], "pairs": rows, "verdicts": verdicts},
                           governance=g, summary="暴露-活性筛查：" + "、".join(
                               f"{_EXPOSURE_ZH.get(k, k)} {v} 对" for k, v in verdicts.items()))
    except ImportError as exc:
        return _failure("study", "unavailable", f"{type(exc).__name__}: {exc}",
                        gv.remedy_for("MISSING_DEPENDENCY"))
    except (ValueError, TypeError, KeyError) as exc:
        return _failure("study", "bad_arguments", f"{type(exc).__name__}: {exc}",
                        _signature(entry["parameters"]))
    return _failure("study", "not_found", f"no study design {op!r}")


# ============================================================================== jobs

_STATE_ZH = {"queued": "排队中", "running": "运行中", "succeeded": "已完成并核验",
             "failed": "失败", "cancelled": "已取消"}


def _job_view(job: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": job.get("id"), "kind": job.get("kind"), "state": job.get("state")}


def _submit_job(kind: str, params: Mapping[str, Any], ctx: Context,
                entry: Mapping[str, Any]) -> Outcome:
    submit = _jobs_fn(ctx.jobs, "submit")
    title = entry["title"]["zh"]
    if submit is None:
        return _failure("job", "unavailable",
                        f"{entry['title']['en']} runs as a job and needs the local runner's job "
                        "service.", gv.remedy_for("NEEDS_RUNNER"),
                        summary=f"{title}：需要本机 Runner 的任务服务",
                        limitations=[gv.LIMIT_PENDING])
    try:
        job = submit(kind, jsonable(dict(params)), ctx.project_id)
    except (ValueError, KeyError, TypeError) as exc:
        return _failure("job", "bad_arguments", f"the job service refused the parameters: {exc}",
                        _signature(entry["parameters"]))
    except Exception as exc:                                    # noqa: BLE001
        return _failure("job", "runtime_error", f"the job could not be submitted: "
                                                f"{type(exc).__name__}: {exc}")
    job = jsonable(job) if isinstance(job, Mapping) else {"id": str(job), "kind": kind,
                                                           "state": "queued"}
    g = gv.empty("job")
    g["limitations"] = [gv.LIMIT_PENDING]
    if kind in ("pipeline.fold", "pipeline.dock", "pipeline.admet", "skill.run"):
        g["limitations"].append(gv.LIMIT_PREDICTED)
    state = str(job.get("state") or "queued")
    if state in ("failed", "cancelled"):
        outcome = job.get("outcome") or {}
        o = _failure("job", "runtime_error" if state == "failed" else "unavailable",
                     str(outcome.get("error") or f"the job {state} at once"),
                     limitations=g["limitations"], status="failed" if state == "failed" else "cancelled")
        o.result, o.job = job, _job_view(job)
        return o
    return Outcome(status="job_submitted", result=job, model_view=_job_view(job),
                   governance=g, job=_job_view(job),
                   summary=f"已提交任务 {job.get('id')}（{title}）：{_STATE_ZH.get(state, state)}；"
                           "完成并核验前没有结果")


def _exec_job(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    kind = entry["id"].split(".", 1)[1]
    return _submit_job(kind, args, ctx, entry)


# ============================================================================ system

_PROBED: dict[str, list[dict[str, Any]]] = {}


def _searchable(where: str) -> list[dict[str, Any]]:
    with _ENV_LOCK:
        if where not in _PROBED:
            _PROBED[where] = catalog_entries(where, probe=where == "runner")
        return _PROBED[where]


def _exec_system(entry: dict[str, Any], args: dict[str, Any], ctx: Context) -> Outcome:
    op = entry["id"].split(".", 1)[1]
    g = gv.empty("system")
    if op == "catalog_search":
        r = catalog_search(args.get("query") or "", category=args.get("category"),
                           kind=args.get("kind"), runnable_now=bool(args.get("runnable_now")),
                           limit=int(args.get("limit") or 10), where=ctx.where,
                           network=ctx.network, jobs=ctx.jobs is not None,
                           items=_searchable(ctx.where))
        view = {"matched": [{k: m[k] for k in ("id", "title", "summary", "parameters",
                                               "runnable_now", "needs", "confirm", "network")
                             if k in m and (k != "needs" or m[k])}
                            for m in r["matched"]], "how": r["how"]}
        if not r["matched"]:
            view["note"] = ("No entry matched. Try other words (Chinese or English), a "
                            "category, or no filters.")
        return Outcome(result=r, model_view=view, governance=g,
                       summary=f"能力检索「{r['query']}」：{len(r['matched'])} 项"
                               + (f"（共 {r['total']} 项相关）" if r["total"] > len(r["matched"]) else ""))
    if op == "capabilities":
        return _capabilities(ctx, g)
    if op == "job_status":
        get = _jobs_fn(ctx.jobs, "get")
        if get is None:
            return _failure("system", "unavailable", "job status needs the local runner's job "
                                                     "service.", gv.remedy_for("NEEDS_RUNNER"))
        wait = max(0, min(int(args.get("wait_s") or 0), 60))
        try:
            job = get(str(args["job_id"]), wait)
        except (KeyError, LookupError) as exc:
            return _failure("system", "not_found", f"no job {args['job_id']!r} ({exc})")
        if job is None:
            return _failure("system", "not_found", f"no job {args['job_id']!r}")
        job = jsonable(job)
        state = str(job.get("state"))
        g["limitations"] = [] if state == "succeeded" else [gv.LIMIT_PENDING]
        progress = job.get("progress") or {}
        frac = progress.get("fraction") if isinstance(progress, Mapping) else None
        tail = f"，进度 {frac:.0%}" if isinstance(frac, (int, float)) else ""
        if state == "succeeded":
            tail += f"，{len(job.get('artefacts') or [])} 个产出文件"
        elif state == "failed":
            err = (job.get("outcome") or {}).get("error")
            tail += f"：{err}" if err else ""
        return Outcome(result=job, governance=g, job=_job_view(job),
                       summary=f"任务 {job.get('id')}：{_STATE_ZH.get(state, state)}{tail}"
                               + ("" if state == "succeeded" else "（尚无结果）"))
    if op == "doctor":
        from bioagent.doctor import diagnose
        report = diagnose(smoke=bool(args.get("smoke", False)))
        problems = report.get("problems") or []
        return Outcome(result=report, governance=g,
                       summary=f"安装诊断：{report.get('verdict', '?')}，{len(problems)} 个问题（附处理建议）")
    if op == "skills":
        specs = [{k: v for k, v in s.items() if k not in ("directory", "group", "lockfile")}
                 for s in skill_specs()]
        pinned = sum(1 for s in specs if s["pinned"])
        return Outcome(result={"skills": specs}, governance=g,
                       summary=f"受治理 Skill：{len(specs)} 个，其中 {pinned} 个已锁定可发布")
    if op == "classify":
        labels, detail = gv.advisory_labels(str(args["text"]))
        if detail is None:
            return _failure("system", "unavailable", "the PSH classifier could not be loaded")
        g["labels"] = labels
        g["label"] = detail
        g["limitations"] = ["Advisory: the classifier's fallback rules flag Chinese names as "
                            "name cues; Studio shows the label and does not enforce it."]
        allowed = [d for d, ok in (detail.get("permits") or {}).items() if ok]
        return Outcome(result=detail, governance=g,
                       summary=f"分级：{detail['sensitivity']}；可去向 {'、'.join(allowed) or '无'}")
    if op == "audit_verify":
        return _audit_verify(ctx, g)
    return _failure("system", "not_found", f"no system operation {op!r}")


def _capabilities(ctx: Context, g: dict[str, Any]) -> Outcome:
    from .envelope import runtime_string, versions

    deps = {name: dependency_present(name) for name in (
        "numpy", "scipy", "pandas", "pyarrow", "openpyxl", "rdkit", "meeko", "vina", "gemmi",
        "scikit-learn", "torch", "transformers", "scanpy", "harmonypy", "leidenalg",
        "scvi-tools", "pydeseq2", "paper-qa")}
    if ctx.where == "runner":
        deps.update({b: dependency_present(b) for b in ("salmon", "kallisto", "fastp",
                                                        "colabfold_batch")})
    items = _searchable(ctx.where)
    runnable = [e for e in items if ctx.where in e.get("exec", ())
                and e.get("available") is not False
                and (not e.get("network") or ctx.network)
                and (not e.get("job") or ctx.jobs is not None)]
    project = _project(ctx)
    hub_root = None
    built: list[str] = []
    if ctx.where == "runner":
        try:
            hub = _hub(ctx)
            hub_root, built = str(hub.root), hub.built()
        except Exception:                                       # noqa: BLE001
            pass
    result: dict[str, Any] = {
        "where": ctx.where, "runtime": runtime_string(), "versions": versions(),
        "device": ctx.device,
        "compute": ("Python on the CPU (single thread, WebAssembly)" if ctx.where == "browser"
                    else "Python on this machine; jobs use the device setting"),
        "network": {"enabled": ctx.network, "profile": ctx.profile}, "purpose": ctx.purpose,
        "jobs": ctx.jobs is not None, "dependencies": deps,
        "audit_chain": {"project_id": ctx.project_id, "durable": project.durable,
                        "location": str(project.psh)},
        "tcmdb": {"root": hub_root, "built": built} if ctx.where == "runner" else None,
        "counts": {"entries": len(items), "runnable_here": len(runnable),
                   "by_kind": {k: sum(1 for e in runnable if e["kind"] == k) for k in (
                       "native", "skill", "connector", "clinic", "tcmdb", "study", "job",
                       "system")}}}
    if ctx.where == "browser":
        result["pyodide_loadable"] = ["numpy", "scipy", "pandas"]
    if ctx.capabilities:
        result["host"] = jsonable(dict(ctx.capabilities))
    missing = [k for k, v in deps.items() if not v]
    g["limitations"] = ["Missing optional dependencies are refused when needed, never "
                        "approximated."] if missing else []
    where_zh = "浏览器（单线程 WebAssembly CPU）" if ctx.where == "browser" else "本机 Runner"
    return Outcome(result=result, governance=g,
                   summary=f"运行能力：{where_zh}；{len(runnable)}/{len(items)} 项可运行；"
                           f"网络{'已开启' if ctx.network else '未开启'}")


def _audit_verify(ctx: Context, g: dict[str, Any]) -> Outcome:
    project = _project(ctx)
    path = project.psh / "events.db"
    if not path.is_file():
        return Outcome(result={"project_id": ctx.project_id, "records": 0, "intact": True,
                               "durable": project.durable, "location": str(path)},
                       governance=g, summary="审计链：本项目尚无受治理运行记录")
    from psh.kernel.events import EventStore
    store = EventStore(path)
    try:
        check = store.verify()
        result = {"project_id": ctx.project_id, "intact": bool(check.intact),
                  "records": check.records, "first_break": check.first_break,
                  "detail": check.detail, "head_hash": store.head_hash,
                  "events": store.events(), "durable": project.durable, "location": str(path)}
    finally:
        with contextlib.suppress(Exception):
            store.close()
    if not result["intact"]:
        g["refusals"] = [gv.refusal("ART117", f"the audit chain breaks at seq "
                                              f"{result['first_break']}: {result['detail']}",
                                    "Treat every artifact attested after the break as "
                                    "unverified; restore the chain from a copy.")]
    return Outcome(result=result, governance=g, audit_head=result["head_hash"],
                   summary=f"审计链：{result['records']} 条记录，"
                           + ("完整" if result["intact"] else f"在第 {result['first_break']} 条断开"))


_EXECUTORS: dict[str, Callable[[dict[str, Any], dict[str, Any], Context], Outcome]] = {
    "native": _exec_native, "connector": _exec_connector, "skill": _exec_skill,
    "clinic": _exec_clinic, "tcmdb": _exec_tcmdb, "study": _exec_study, "job": _exec_job,
    "system": _exec_system}
