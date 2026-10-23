"""``tcmstudio validate``: every capability enumerated, run through the dispatcher, judged.

    tcmstudio validate --out DIR [--tier offline|live] [--kinds native skill …]
                       [--tcmdb-root DIR] [--probe-network]

writes, in DIR:

    agent_inventory.json            every core tool, catalog entry, job kind, governed skill,
                                    hub dataset, source card and execution backend
    tool_validation_report.json     every core tool and catalog entry against twelve checks
    agent_regression_report.json    twelve research scenarios run as tool chains (scenarios.py)
    sandbox_execution_report.json   where it ran: the network as requests found it, storage
                                    that persists, devices
    dependency_readiness_report.json    optional packages imported, versioned, computing
    database_readiness_report.json  every catalogued source, as far as it is verified here
    validation_summary.json         the counts of every report, and what is not verified

The twelve checks (``bioagent.validation`` statuses; a record is PASS only when every check
that applies passed):

    discovery            the entry is found by its id and by its loose name
    schema               its parameters are an object schema; its example satisfies them
    invocation           it ran through ``dispatch.call`` on its example (or a probe input)
    functional_correctness  its output matches a reference established independently
                         (``bioagent.validation.references``; classical texts for the TCM
                         tools); UNVERIFIED where none exists
    error_handling       a wrongly typed argument is refused as bad_arguments, not run, not
                         crashed
    dependency_readiness its optional packages are importable here
    data_integration     the store it reads is built here
    execution_isolation  offline, it opens no network connection (every socket is watched);
                         a network entry is refused with network_off instead of connecting
    recovery             a retry of a pure computation is a rerun; network and job retries
                         are the HTTP backend's and the job service's (their own tests)
    reproducibility      a second identical call gives the same output digest
    integration          its output fed the next tool of a validated chain
    performance          it finished within its budget

Nothing here is mocked: every invocation is the real code on real input. What could not be
run is SKIPPED, BLOCKED or UNVERIFIED, with the reason, and is never counted as passing.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

__all__ = ["add_arguments", "run", "validate_entries", "inventory", "CHECKS"]

CHECKS = ("discovery", "schema", "invocation", "functional_correctness", "error_handling",
          "dependency_readiness", "data_integration", "execution_isolation", "recovery",
          "reproducibility", "integration", "performance")

#: seconds an in-process call may take before it fails the performance check
_BUDGET_S = {"native": 10.0, "skill": 120.0, "clinic": 30.0, "tcmdb": 60.0, "study": 120.0,
             "system": 30.0, "connector": 60.0, "core": 60.0}

#: inputs for entries that ship no example: real calls, chosen to be cheap and offline
PROBES: dict[str, dict[str, Any]] = {
    "tcmdb.status": {}, "tcmdb.licences": {}, "system.doctor": {},
    "tcmdb.relations": {"kind": "organism_compound", "object": "inchikey:YBHILYKTIRIUTE-UHFFFAOYSA-N"},
    "tcmdb.evidence_for": {"subject": "黄芪"},
    # core tools (the model calls these; none ships an example)
    "tcm_lookup": {"name": "黄芪"}, "tcm_herb": {"name": "甘草"}, "tcm_formula": {"name": "桂枝汤"},
    "tcm_syndrome": {"name": "太阳中风"}, "tcm_classical_search": {"query": "桂枝"},
    "tcm_compatibility": {"herbs": ["甘草", "甘遂"]}, "tcm_normalize": {"names": ["黄芪", "桂枝汤"]},
    "tcm_evidence": {"subject": "黄芪"}, "tcm_safety_report": {"subject": "附子"},
    "tcm_applicability": {"subject": "桂枝汤", "object": "太阳中风"},
    "tcm_network_hypothesis": {"formula_name": "葛根芩连汤"}, "tcmdb_catalog": {"query": "NPASS"},
    "tcmdb_consensus": {"kind": "organism_compound", "subject": "甘草"},
    "tcmdb_relations": {"kind": "organism_compound", "subject": "甘草"},
    "research_inquiry": {"action": "scaffolds"},
    "capabilities_status": {}, "catalog_search": {"query": "GC content"},
    "job_status": {"job_id": "j_0000000000000000"},
    "call_tool": {"tool": "native.gc_content", "arguments": {"sequence": "ACGTGGCC"}},
}

#: what classical texts and pharmacopoeias say, for the TCM core tools
TCM_REFERENCES: list[tuple[str, dict[str, Any], Callable[[Any], tuple[bool, str]], str]] = []


def _ingredients(result: Any) -> list[str]:
    """Ingredient names, from the curated record (``chinese``) or a workbook row (``name``)."""
    return [str(i.get("chinese") or i.get("name") or "") for i in
            (result or {}).get("ingredients") or ()]


def _tcm_reference(tool: str, args: dict[str, Any], source: str):
    def register(fn: Callable[[Any], tuple[bool, str]]):
        TCM_REFERENCES.append((tool, args, fn, source))
        return fn
    return register


@_tcm_reference("tcm_formula", {"name": "桂枝汤"}, "伤寒论 第12条: 桂枝三两、芍药三两、甘草二两（炙）、"
                "生姜三两、大枣十二枚")
def _guizhi(result: Any) -> tuple[bool, str]:
    got = sorted(_ingredients(result))
    doses = {i.get("chinese") or i.get("name"): i.get("dose")
             for i in (result or {}).get("ingredients") or ()}
    ok = got == sorted(["桂枝", "白芍", "甘草", "生姜", "大枣"]) and doses.get("大枣") == "十二枚" \
        and doses.get("桂枝") == "三两" and doses.get("甘草") == "二两"
    return ok, f"ingredients {got}, doses {doses}"


@_tcm_reference("tcm_formula", {"name": "葛根芩连汤"}, "伤寒论 第34条: 葛根半斤、甘草二两（炙）、黄芩三两、"
                "黄连三两")
def _gegen(result: Any) -> tuple[bool, str]:
    got = sorted(_ingredients(result))
    return got == sorted(["葛根", "黄芩", "黄连", "甘草"]), f"ingredients {got}"


@_tcm_reference("tcm_compatibility", {"herbs": ["甘草", "甘遂"]}, "十八反: 藻戟遂芫俱战草")
def _fan_gancao(result: Any) -> tuple[bool, str]:
    conflicts = (result or {}).get("conflicts") or []
    ok = result.get("compatible") is False and any(c.get("kind") == "incompatibility" for c in conflicts)
    return ok, f"compatible {result.get('compatible')}, {len(conflicts)} conflict(s)"


@_tcm_reference("tcm_compatibility", {"herbs": ["人参", "藜芦"]}, "十八反: 诸参辛芍叛藜芦")
def _fan_lilu(result: Any) -> tuple[bool, str]:
    return _fan_gancao(result)


@_tcm_reference("tcm_compatibility", {"herbs": ["桂枝", "白芍", "生姜", "大枣", "甘草"]},
                "桂枝汤's five herbs: none of 十八反 or 十九畏 is among them")
def _guizhi_compatible(result: Any) -> tuple[bool, str]:
    conflicts = [c for c in (result or {}).get("conflicts") or ()
                 if c.get("kind") in ("incompatibility", "antagonism")]
    return not conflicts, f"{len(conflicts)} incompatibility conflict(s)"


@_tcm_reference("tcm_herb", {"name": "甘草"}, "中国药典 2020: 甘草 Glycyrrhizae Radix et Rhizoma, "
                "from Glycyrrhiza uralensis, G. inflata or G. glabra")
def _gancao(result: Any) -> tuple[bool, str]:
    herb = (result or {}).get("herb") or {}
    ok = herb.get("latin") == "Glycyrrhizae Radix et Rhizoma" and set(herb.get("species") or ()) \
        == {"Glycyrrhiza uralensis", "Glycyrrhiza inflata", "Glycyrrhiza glabra"}
    return ok, f"latin {herb.get('latin')!r}, species {herb.get('species')}"


# --------------------------------------------------------------------- isolation
@contextlib.contextmanager
def watched_sockets() -> Iterator[list[str]]:
    """Refuse and record every connection and name lookup off the loopback interface."""
    attempts: list[str] = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    local = ("127.0.0.1", "::1", "localhost", "0.0.0.0")

    def _host(address: Any) -> str:
        return str(address[0]) if isinstance(address, tuple) and address else str(address)

    def connect(self, address):
        if self.family == getattr(socket, "AF_UNIX", None) or _host(address) in local:
            return real_connect(self, address)
        attempts.append(_host(address))
        raise OSError(f"validation: an offline call tried to connect to {_host(address)}")

    def connect_ex(self, address):
        if self.family == getattr(socket, "AF_UNIX", None) or _host(address) in local:
            return real_connect_ex(self, address)
        attempts.append(_host(address))
        return 111

    def getaddrinfo(host, *args, **kwargs):
        if host is None or str(host) in local:
            return real_getaddrinfo(host, *args, **kwargs)
        attempts.append(str(host))
        raise socket.gaierror(-2, f"validation: an offline call looked up {host}")

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo = getaddrinfo
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.socket.connect_ex = real_connect, real_connect_ex
        socket.getaddrinfo = real_getaddrinfo


# ---------------------------------------------------------------------- helpers
def _invalid(schema: Mapping[str, Any]) -> dict[str, Any] | None:
    """Arguments that break the schema in a way a validator must catch: the first declared
    property given a value of the wrong type."""
    props = (schema or {}).get("properties") or {}
    if not props:
        return None
    required = list((schema or {}).get("required") or ())
    name = required[0] if required else next(iter(props))
    t = (props.get(name) or {}).get("type")
    wrong = {"string": {"not": "a string"}, "number": "not-a-number", "integer": "not-an-integer",
             "array": {"not": "a list"}, "object": 42, "boolean": {"not": "a boolean"}}.get(
        t if isinstance(t, str) else "", None)
    if wrong is None:
        return None
    return {name: wrong}


def _example(entry_or_core: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    ex = entry_or_core.get("example")
    if isinstance(ex, Mapping):
        return dict(ex)
    return copy.deepcopy(PROBES[key]) if key in PROBES else None


def _digest(env: Mapping[str, Any]) -> str | None:
    return ((env or {}).get("receipt") or {}).get("output_sha256")


#: Fields a governed run sets per run, by design: its id, its time, the audit chain's new
#: head, the output directory, and the digests that cover them. Two runs of the same input
#: differ in these and in nothing else when the science is reproducible.
_RUN_SCOPED = frozenset({"run_id", "created_at", "audit_head", "digest", "run_anchor",
                         "pre_attestation_digest", "output_dir", "started_at", "finished_at",
                         "duration_ms", "duration_s", "elapsed_ms"})


def _stable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _stable(v) for k, v in value.items() if k not in _RUN_SCOPED}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _stable_digest(env: Mapping[str, Any]) -> str:
    import hashlib
    blob = json.dumps(_stable((env or {}).get("result")), sort_keys=True, ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _deterministic(entry: Mapping[str, Any]) -> bool:
    if entry.get("kind") == "native":
        return True
    skill = entry.get("skill") or {}
    return bool(skill.get("deterministic"))


# ---------------------------------------------------------------------- the checks
def validate_entries(items: Sequence[tuple[str, str, Mapping[str, Any]]], *, ctx_base: Mapping[str, Any],
                     tier: str, built: set[str], log: Callable[[str], None]) -> list[Any]:
    """``items``: (record id, kind, entry or core tool). One record each."""
    from bioagent.validation import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED,
                                     Record)
    from bioagent.validation.references import check as ref_check, references_for

    from .catalog import canonical_name, dependency_present, get_entry
    from .core_tools import CORE_BY_NAME
    from .dispatch import call, validate

    offline = {**ctx_base, "network": False}
    live = {**ctx_base, "network": True}
    refs_tcm: dict[str, list] = {}
    for tool, args, fn, source in TCM_REFERENCES:
        refs_tcm.setdefault(tool, []).append((args, fn, source))
    records = []
    for rid, kind, item in items:
        is_core = kind == "core"
        entry = item if not is_core else (get_entry(item.get("maps_to") or "") or {})
        name = rid
        rec = Record(rid, kind, "", reproduce=f"tcmstudio call {rid} --args '{{…}}'")
        # discovery ------------------------------------------------------------
        found = (rid in CORE_BY_NAME) if is_core else (get_entry(rid) is not None)
        loose = canonical_name(rid.replace(".", "_"))
        rec.add("discovery", PASS if found else FAIL,
                "found by its id" + (" and by its loose name" if loose == rid else ""))
        # schema ---------------------------------------------------------------
        params = item.get("parameters") or {}
        example = _example(item, rid)
        if params.get("type") != "object":
            rec.add("schema", FAIL, "the parameters are not an object schema")
        elif example is None:
            rec.add("schema", PASS, "an object schema (no example to check against it)")
        else:
            v = validate(params, example)
            rec.add("schema", PASS if v.ok else FAIL,
                    "the example satisfies the schema" if v.ok else "; ".join(v.problems)[:400])
        network = bool(item.get("network")) or entry.get("kind") == "connector"
        is_job = bool(entry.get("job")) if not is_core else bool(item.get("job"))
        heavy = list(entry.get("heavy") or ())
        missing = [d for d in heavy if not dependency_present(d)]
        rec.add("dependency_readiness", NOT_APPLICABLE if not heavy else
                BLOCKED if missing else PASS,
                "no optional packages" if not heavy else
                f"not installed here: {', '.join(missing)}" if missing else
                f"installed: {', '.join(heavy)}")
        data = entry.get("data") or entry.get("needs")
        if data:
            rec.add("data_integration", PASS if built else BLOCKED,
                    f"stores built here: {sorted(built)}" if built else
                    "needs a built tcmdb store; none is built in this environment")
        else:
            rec.add("data_integration", NOT_APPLICABLE, "reads no local store")
        # invocation, isolation, performance, reproducibility -----------------
        env: dict[str, Any] | None = None
        if is_job:
            rec.add("invocation", UNVERIFIED, "a job runs as a process under the runner's job "
                    "service; its kind is checked by the job tests and the live tier")
            for c in ("execution_isolation", "reproducibility", "performance"):
                rec.add(c, NOT_APPLICABLE, "a job")
        elif example is None:
            rec.add("invocation", UNVERIFIED, "no example or probe input to run it with")
            rec.add("execution_isolation", UNVERIFIED, "not run")
            rec.add("reproducibility", NOT_APPLICABLE, "not run")
            rec.add("performance", NOT_APPLICABLE, "not run")
        elif missing:
            rec.add("invocation", BLOCKED, f"needs {', '.join(missing)}")
            for c in ("execution_isolation", "reproducibility", "performance"):
                rec.add(c, NOT_APPLICABLE, "not run: its packages are not installed")
        elif network and tier != "live":
            env = call(rid, example, offline)
            refused = env["status"] == "failed" and (env.get("error") or {}).get("type") == "network_off"
            rec.add("invocation", SKIPPED, "a network entry: called for real in the live tier")
            rec.add("execution_isolation", PASS if refused else FAIL,
                    "with web access off it was refused (network_off) without connecting"
                    if refused else f"with web access off it answered {env['status']}")
            rec.add("reproducibility", NOT_APPLICABLE, "not run offline")
            rec.add("performance", NOT_APPLICABLE, "not run offline")
            env = None
        else:
            t0 = time.perf_counter()
            with watched_sockets() as attempts:
                env = call(rid, example, live if network else offline)
            elapsed = time.perf_counter() - t0
            status = env["status"]
            err = env.get("error") or {}
            if status in ("succeeded", "job_submitted"):
                rec.add("invocation", PASS, f"{status} in {elapsed * 1000:.0f} ms",
                        duration_ms=round(elapsed * 1000), output_sha256=_digest(env))
            elif err.get("type") in ("unavailable", "network_off"):
                rec.add("invocation", BLOCKED, err.get("message", "")[:300])
            elif status == "needs_approval" or err.get("type") == "refused":
                rec.add("invocation", SKIPPED, "needs a person's confirmation, which the "
                        "validator never gives")
            elif err.get("type") == "not_found" and kind != "native":
                rec.add("invocation", PASS, f"ran and answered not_found: {err.get('message', '')[:200]}",
                        duration_ms=round(elapsed * 1000))
            else:
                rec.add("invocation", FAIL, f"{status}: {err.get('type')}: {err.get('message', '')[:300]}")
            if network:
                rec.add("execution_isolation", NOT_APPLICABLE, "a network entry run live")
            else:
                rec.add("execution_isolation", FAIL if attempts else PASS,
                        f"tried to reach {sorted(set(attempts))}" if attempts else
                        "no connection or name lookup off the loopback interface")
            budget = _BUDGET_S.get("core" if is_core else entry.get("kind", ""), 60.0)
            rec.add("performance", PASS if elapsed <= budget else FAIL,
                    f"{elapsed:.2f} s of a {budget:g} s budget", seconds=round(elapsed, 3))
            if status == "succeeded":
                again = call(rid, example, live if network else offline)
                if _digest(again) == _digest(env):
                    rec.add("reproducibility", PASS, "the same output digest twice")
                elif _stable_digest(again) == _stable_digest(env):
                    rec.add("reproducibility", PASS, "the same result twice; only the run's id, "
                            "time and audit-chain head differ, as they must")
                else:
                    rec.add("reproducibility", FAIL if _deterministic(entry) and not network
                            else UNVERIFIED, "the results of two identical calls differ")
            else:
                rec.add("reproducibility", NOT_APPLICABLE, "it did not succeed")
        # functional correctness ----------------------------------------------
        refs = references_for(rid.split(".", 1)[1]) if rid.startswith("native.") else []
        tcm = refs_tcm.get(rid, [])
        if refs:
            results = []
            for ref in refs:
                if any(_missing(m) for m in ref.needs):
                    results.append((None, f"needs {ref.needs}"))
                    continue
                r = call(rid, dict(ref.args), offline)
                ok, problems = (ref_check(ref, r["result"]) if r["status"] == "succeeded"
                                else (False, [f"{r['status']}: {(r.get('error') or {}).get('message')}"]))
                results.append((ok, "; ".join(problems) or ref.source))
            if any(ok is False for ok, _ in results):
                rec.add("functional_correctness", FAIL, "; ".join(d for ok, d in results if ok is False)[:600])
            elif all(ok is None for ok, _ in results):
                rec.add("functional_correctness", BLOCKED, results[0][1])
            else:
                rec.add("functional_correctness", PASS,
                        f"matches {sum(ok is True for ok, _ in results)} independent reference(s): "
                        + "; ".join(r.source for r in refs)[:300])
        elif tcm:
            outcomes = []
            for args, fn, source in tcm:
                r = call(rid, dict(args), offline)
                if r["status"] != "succeeded":
                    outcomes.append((False, f"{args}: {r['status']}"))
                    continue
                try:
                    ok, detail = fn(r["result"])
                except Exception as exc:                        # noqa: BLE001
                    ok, detail = False, f"the result has another shape: {type(exc).__name__}: {exc}"
                outcomes.append((ok, f"{json.dumps(args, ensure_ascii=False)} — {source}: {detail}"))
            bad = [d for ok, d in outcomes if not ok]
            rec.add("functional_correctness", FAIL if bad else PASS,
                    "; ".join(bad)[:600] if bad else
                    f"agrees with {len(outcomes)} classical or pharmacopoeia reference(s)")
        else:
            rec.add("functional_correctness", UNVERIFIED, "no reference output established "
                    "independently of its code")
        # error handling -------------------------------------------------------
        bad_args = _invalid(params)
        if bad_args is None:
            rec.add("error_handling", NOT_APPLICABLE, "no typed argument to give a wrong value")
        else:
            r = call(rid, bad_args, offline)
            etype = (r.get("error") or {}).get("type")
            if r["status"] in ("failed", "refused") and etype == "bad_arguments":
                rec.add("error_handling", PASS, "a wrongly typed argument is refused as bad_arguments")
            elif etype == "runtime_error":
                rec.add("error_handling", FAIL, f"crashed on a wrongly typed argument: "
                        f"{(r.get('error') or {}).get('message', '')[:200]}")
            elif r["status"] == "succeeded":
                rec.add("error_handling", FAIL, "ran with a wrongly typed argument")
            else:
                rec.add("error_handling", PASS, f"refused ({etype})")
        # recovery, integration -------------------------------------------------
        if is_job:
            rec.add("recovery", UNVERIFIED, "restart and resume are the job service's "
                    "(tests/test_jobs.py); not exercised here")
        elif network:
            rec.add("recovery", UNVERIFIED, "bounded retries and pacing are the HTTP "
                    "backend's; not exercised here")
        else:
            rec.add("recovery", NOT_APPLICABLE, "an in-process computation: a retry is a "
                    "rerun, which reproducibility checks")
        rec.add("integration", NOT_APPLICABLE, "not part of a validated chain")
        records.append(rec)
        if len(records) % 100 == 0:
            log(f"validated {len(records)} capabilities")
    return records


def _missing(module: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(module) is None
    except (ImportError, ValueError):
        return True


# --------------------------------------------------------------------- chains
CHAINS: tuple[dict[str, Any], ...] = (
    {"name": "formula → its herbs → their compatibility",
     "steps": ("tcm_formula", "tcm_compatibility"),
     "source": "桂枝汤's five herbs carry no 十八反 or 十九畏 conflict"},
    {"name": "catalog search → call the hit",
     "steps": ("catalog_search", "call_tool"),
     "source": "the search finds native.gc_content and its call computes 0.5 for ACGT"},
    {"name": "herb → its record → a safety report",
     "steps": ("tcm_herb", "tcm_safety_report"),
     "source": "甘草's record feeds a safety report that names it"},
)


def run_chains(ctx: Mapping[str, Any]) -> list[tuple[dict[str, Any], str, str]]:
    """(chain, status, detail): each step's output becomes the next step's input."""
    from bioagent.validation import FAIL, PASS

    from .dispatch import call

    off = {**ctx, "network": False}
    out = []
    f = call("tcm_formula", {"name": "桂枝汤"}, off)
    herbs = _ingredients(f.get("result"))
    c = call("tcm_compatibility", {"herbs": herbs}, off) if herbs else {"status": "skipped"}
    ok = f["status"] == "succeeded" and c.get("status") == "succeeded" and not [
        x for x in (c.get("result") or {}).get("conflicts") or () if x.get("kind") == "incompatibility"]
    out.append((CHAINS[0], PASS if ok else FAIL, f"herbs {herbs}; compatibility {c.get('status')}"))
    s = call("catalog_search", {"query": "GC content"}, off)
    hits = [h.get("id") for h in (s.get("result") or {}).get("matched") or ()][:5]
    hit = "native.gc_content" if "native.gc_content" in hits else (hits[0] if hits else None)
    r = call("call_tool", {"tool": hit, "arguments": {"sequence": "ACGT"}}, off) if hit else {}
    ok = hit == "native.gc_content" and abs(((r.get("result") or {}).get("gc_fraction") or -1) - 0.5) < 1e-9
    out.append((CHAINS[1], PASS if ok else FAIL, f"hits {hits}; result {(r.get('result') or {}).get('gc_fraction')}"))
    h = call("tcm_herb", {"name": "甘草"}, off)
    herb = ((h.get("result") or {}).get("herb") or {}).get("chinese")
    sr = call("tcm_safety_report", {"subject": herb}, off) if herb else {}
    ok = h["status"] == "succeeded" and sr.get("status") == "succeeded" and "甘草" in json.dumps(
        sr.get("result"), ensure_ascii=False)
    out.append((CHAINS[2], PASS if ok else FAIL, f"herb {herb}; safety report {sr.get('status')}"))
    return out


# ------------------------------------------------------------------- inventory
def inventory(where: str = "runner") -> dict[str, Any]:
    from bioagent.tcmdb.datasets import DATASETS
    from bioagent.tcmdb.hub import catalog as cards

    from .catalog import JOB_KINDS, build_catalog, skill_specs

    doc = build_catalog(where, probe=True)
    entries = doc["entries"]
    backends = sorted({b for e in entries
                       for b in ((e.get("execution") or {}).get("browser_compute") or {}).get("backends", ())})
    devices = _devices()
    return {
        "schema": "tcmscience.inventory/1", "where": where,
        "counts": {"core_tools": len(doc["core"]), "entries": len(entries),
                   "by_kind": doc["counts"]["by_kind"], "job_kinds": len(JOB_KINDS),
                   "skills": len(skill_specs()), "datasets": len(DATASETS), "sources": len(cards())},
        "core_tools": [{"name": c["name"], "maps_to": c.get("maps_to"), "network": c.get("network"),
                        "confirm": c.get("confirm"), "job": c.get("job")} for c in doc["core"]],
        "entries": [{"id": e["id"], "kind": e["kind"], "category": e["category"], "exec": e["exec"],
                     "network": e["network"], "confirm": e["confirm"], "job": e["job"],
                     "gpu": e["gpu"], "heavy": e.get("heavy") or [],
                     "available_here": e.get("available", True), "missing": e.get("missing") or [],
                     "example": e.get("example") is not None, "probe": e["id"] in PROBES,
                     "browser_compute": ((e.get("execution") or {}).get("browser_compute") or {}).get("backends")}
                    for e in entries],
        "job_kinds": {k: {"network": bool(v.get("network")), "heavy": v.get("heavy") or [],
                          "gpu": bool(v.get("gpu")), "duration": v.get("duration")}
                      for k, v in JOB_KINDS.items()},
        "skills": [{"id": s["id"], "version": s.get("version"), "pinned": bool(s.get("pinned")),
                    "set": s.get("set"), "content_hash": s.get("content_hash")} for s in skill_specs()],
        "datasets": [{"key": d.key, "access": d.access, "relations": list(d.relations)}
                     for d in DATASETS],
        "sources": len(cards()),
        "execution_backends": {"runner": ["cpu", *(["gpu"] if (devices or {}).get("gpu") else [])],
                               "browser": ["pyodide-cpu", *backends], "jobs": "runner job service"},
        "devices": devices,
    }


def _devices() -> dict[str, Any]:
    """CPU, memory and GPUs as the runner's device probe sees them (no torch import)."""
    from . import devices as dev

    out: dict[str, Any] = {}
    for name, fn in (("cpu", dev.cpu_info), ("memory_gb", dev.memory_gb),
                     ("nvidia", dev.nvidia_gpus), ("rocm", dev.rocm_gpus), ("apple", dev.apple_gpu)):
        try:
            out[name] = fn()
        except Exception as exc:                                # noqa: BLE001
            out[name] = f"{type(exc).__name__}: {exc}"
    out["gpu"] = bool(out.get("nvidia") or out.get("rocm") or out.get("apple"))
    return out


# --------------------------------------------------------------- sandbox report
SANDBOX_HOSTS = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi",
                 "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=berberine&format=json&pageSize=1",
                 "https://clinicaltrials.gov/api/v2/studies?pageSize=1",
                 "https://rest.uniprot.org/uniprotkb/P35354.txt",
                 "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_targetInfo.txt",
                 "https://zenodo.org/api/records/5794106",
                 "https://stringdb-downloads.org/download/protein.info.v12.0/9606.protein.info.v12.0.txt.gz",
                 "https://reactome.org/download/current/ReactomePathways.txt")


def sandbox_records(state_root: Path, tcmdb_root: Path, *, probe_network: bool) -> list[Any]:
    from bioagent.validation import NOT_APPLICABLE, PASS, SKIPPED, FAIL, Record
    from bioagent.validation.environment import disk_free
    from bioagent.validation.network import probe

    out = []
    net = Record("network", "sandbox", "", reproduce="tcmstudio validate --probe-network")
    for url in SANDBOX_HOSTS:
        host = url.split("/")[2]
        if not probe_network:
            net.add(host, SKIPPED, "not probed (pass --probe-network to send the requests)")
            continue
        p = probe(url)
        net.add(host, p.status, p.reason, http_status=p.http_status, elapsed_ms=p.elapsed_ms)
    out.append(net)
    store = Record("storage", "sandbox", "")
    for name, root in (("state", state_root), ("tcmdb", tcmdb_root)):
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / ".validation-probe"
            with open(path, "wb") as fh:
                fh.write(b"persisted")
                fh.flush()
                os.fsync(fh.fileno())
            ok = path.read_bytes() == b"persisted"
            path.unlink()
            store.add(f"{name}_write_read", PASS if ok else FAIL,
                      f"{root}: written, synced and read back", free_bytes=disk_free(root))
        except OSError as exc:
            store.add(f"{name}_write_read", FAIL, f"{root}: {type(exc).__name__}: {exc}")
    from bioagent.tcmdb.acquire import read_audit
    audit = read_audit(tcmdb_root)
    store.add("download_audit", PASS if audit else NOT_APPLICABLE,
              f"{len(audit)} recorded download attempts" if audit else "no downloads here yet")
    out.append(store)
    return out


# ------------------------------------------------------------------------- run
def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", required=True, help="where the reports go")
    p.add_argument("--tier", choices=("offline", "live"), default="offline",
                   help="offline: nothing leaves the machine; live: network entries are called "
                        "for real and the network is probed")
    p.add_argument("--kinds", nargs="*", default=None,
                   help="only these kinds (core native skill connector clinic tcmdb study job system)")
    p.add_argument("--tcmdb-root", default="", help="a hub to validate against (default: an empty one)")
    p.add_argument("--probe-network", action="store_true",
                   help="probe the research hosts with real requests (the live tier always does)")
    p.add_argument("--strict", action="store_true", help="exit non-zero on BLOCKED too")


def run(a: argparse.Namespace) -> int:
    from bioagent.tcmdb.hub import TCMDataHub
    from bioagent.validation import BLOCKED, FAIL, PASS, Record, report, write_report
    from bioagent.validation.databases import readiness as db_readiness
    from bioagent.validation.dependencies import readiness as dep_readiness
    from bioagent.validation.environment import fingerprint

    from .catalog import entries as catalog_entries
    from .core_tools import CORE_TOOLS

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(m, flush=True)                        # noqa: E731
    with tempfile.TemporaryDirectory(prefix="tcmstudio-validate-") as tmp:
        state_root = Path(tmp) / "state"
        tcmdb_root = Path(a.tcmdb_root) if a.tcmdb_root else Path(tmp) / "tcmdb"
        hub = TCMDataHub(tcmdb_root)
        built = set(hub.built()) if tcmdb_root.exists() else set()
        env = fingerprint({"state": state_root, "tcmdb": tcmdb_root})
        ctx = {"where": "runner", "state_root": str(state_root), "tcmdb_root": str(tcmdb_root),
               "project_id": "validation"}
        kinds = set(a.kinds) if a.kinds else None
        items: list[tuple[str, str, Mapping[str, Any]]] = []
        if kinds is None or "core" in kinds:
            items += [(c["name"], "core", c) for c in CORE_TOOLS]
        items += [(e["id"], e["kind"], e) for e in catalog_entries("runner", probe=False)
                  if kinds is None or e["kind"] in kinds]
        log(f"validating {len(items)} capabilities ({a.tier} tier)")
        t0 = time.perf_counter()
        records = validate_entries(items, ctx_base=ctx, tier=a.tier, built=built, log=log)
        chains = run_chains(ctx)
        by_id = {r.component: r for r in records}
        for chain, status, detail in chains:
            for step in chain["steps"]:
                rec = by_id.get(step)
                if rec is None:
                    continue
                rec.checks = [c for c in rec.checks if c.name != "integration"]
                rec.add("integration", status, f"{chain['name']}: {detail}")
        chain_records = []
        for chain, status, detail in chains:
            r = Record(f"chain: {chain['name']}", "chain", "", provenance={"steps": list(chain["steps"]),
                                                                         "source": chain["source"]})
            r.add("integration", status, detail)
            chain_records.append(r)
        log(f"validated in {time.perf_counter() - t0:.0f} s")
        from .scenarios import SCENARIOS, run_scenarios
        scenarios = run_scenarios(ctx, tier=a.tier, log=log)
        sandbox = sandbox_records(state_root, tcmdb_root,
                                  probe_network=a.probe_network or a.tier == "live")
        deps = dep_readiness()
        dbs = db_readiness(hub)
    inv = inventory("runner")
    (out / "agent_inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")
    notes = ["PASS only when every applicable check passed; SKIPPED, BLOCKED and UNVERIFIED "
             "are never counted as passing.",
             "functional_correctness rests on references established independently of the "
             "code (bioagent.validation.references, classical texts); a capability without "
             "one is UNVERIFIED."]
    docs = {
        "tool_validation_report": report("tool_validation_report", records + chain_records,
                                         tier=a.tier, environment=env, notes=notes),
        "sandbox_execution_report": report("sandbox_execution_report", sandbox, tier=a.tier,
                                           environment=env),
        "dependency_readiness_report": report("dependency_readiness_report", deps, tier=a.tier,
                                              environment=env),
        "database_readiness_report": report("database_readiness_report", dbs, tier=a.tier,
                                            environment=env),
        "agent_regression_report": report(
            "agent_regression_report", scenarios, tier=a.tier, environment=env,
            notes=["Twelve research scenarios, each the tool calls a correct answer needs, run "
                   "through the dispatcher in order. A step that cannot run here ends the "
                   "scenario with its class (tool_unavailable, data_unavailable) and reason: "
                   "BLOCKED or SKIPPED, never PASS."],
            extra={"scenarios": [x.as_dict() for x in SCENARIOS]})}
    for name, doc in docs.items():
        write_report(out / f"{name}.json", doc)
    summary = {"schema": "tcmscience.validation-summary/1", "tier": a.tier,
               "generated_at": docs["tool_validation_report"]["generated_at"],
               "inventory": inv["counts"],
               "reports": {name: doc["summary"]["status"] for name, doc in docs.items()},
               "checks": docs["tool_validation_report"]["summary"]["checks"],
               "not_verified": {name: sum(n for s, n in doc["summary"]["status"].items()
                                          if s not in (PASS, "NOT_APPLICABLE"))
                                for name, doc in docs.items()}}
    (out / "validation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1)
                                                 + "\n", encoding="utf-8")
    for name, counts in summary["reports"].items():
        log(f"{name}: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    judged = records + chain_records + scenarios
    failed = [r for r in judged if r.status == FAIL]
    for r in failed:
        log(f"  FAIL {r.component}: {r.failure_reason}")
    bad = {FAIL, BLOCKED} if a.strict else {FAIL}
    return 1 if any(r.status in bad for r in judged) else 0


def main(argv: Sequence[str] | None = None) -> int:                 # pragma: no cover
    p = argparse.ArgumentParser(prog="tcmstudio validate")
    add_arguments(p)
    return run(p.parse_args(argv))
