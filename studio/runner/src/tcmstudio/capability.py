"""Capability state: one vocabulary and one readiness function (CONTRACTS §2).

An entry's state answers two different questions. ``implementation`` is fixed when the
catalog is built: is there code behind the entry, and has a committed record shown that it
does what it says (registered → implemented → tested; deprecated only by an explicit
mark). ``readiness`` belongs to one runtime and changes with it: can the entry run *here,
now* — on this runner, in this browser — and if not, what is missing, as a list of
requirements, each with the step that would supply it.

Everything that decides "can this run here" asks :func:`requirements`: the dispatcher's
preflight before a call, catalog_search's ``runnable_now`` and ``needs``, the counts of
system.capabilities, the runner's catalog and the capability audit. Nothing here opens a
socket: an entry that reaches a host is 'ready' when web access is on, which says the call
may be made, not that the host will answer. Dependencies are probed only through the
``probe`` the caller passes (``catalog.dependency_present``: the import system and PATH,
nothing imported and nothing run).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Collection, Iterable, Mapping, Sequence

__all__ = ["IMPLEMENTATION", "READINESS", "OUTCOME", "NEEDS", "REASON_NEED", "STATE_OF_NEED",
           "MAX_REQUEST_BYTES", "requirement", "requirements", "readiness", "selected_engines",
           "hosts_for", "datasets_for", "refusal_requirements", "audit"]

#: Implementation states. tested ⇔ some committed verification level is PASS; implemented ⇔
#: the implementation reference resolves; deprecated only from an explicit mark.
IMPLEMENTATION = ("registered", "implemented", "tested", "deprecated")
#: Readiness in one runtime, in order of precedence: the first that applies is the state.
#: Waiting for an approval is not readiness; approvals are listed apart (entry.approvals).
READINESS = ("ready", "needs_runner", "missing_dependency", "needs_dataset",
             "network_unavailable", "needs_authentication", "unsupported")
#: What one call came to (receipt.result.outcome); validated ⇔ succeeded with assurance.
OUTCOME = ("not_executed", "queued", "running", "succeeded", "validated", "failed", "cancelled")
#: What a requirement asks for.
NEEDS = ("runner", "browser_runtime", "dependency", "dataset", "network", "job_service",
         "compute_setting", "remote_backend", "memory", "authentication")
#: The page router's reason codes (and the dispatcher's error.reason) with the need each names.
REASON_NEED = {"needs_runner": "runner", "runner_offline": "runner", "no_runtime": "browser_runtime",
               "missing_deps": "dependency", "missing_engine": "dependency",
               "compute_browser": "compute_setting", "unknown_compute": "compute_setting",
               "remote_not_configured": "remote_backend", "too_large_for_browser": "memory",
               "needs_dataset": "dataset", "network_off": "network",
               "needs_job_service": "job_service", "needs_authentication": "authentication",
               "remote_upload_off": "compute_setting"}
#: The readiness state a missing need puts an entry in.
STATE_OF_NEED = {"runner": "needs_runner", "job_service": "needs_runner",
                 "dependency": "missing_dependency", "dataset": "needs_dataset",
                 "network": "network_unavailable", "authentication": "needs_authentication",
                 "browser_runtime": "unsupported", "compute_setting": "unsupported",
                 "remote_backend": "unsupported", "memory": "unsupported"}

#: The largest request one call takes. runner: the runner's HTTP body limit (server.MAX_JSON,
#: answered 413 above it). browser: a conservative bound for one call's JSON arguments in the
#: Pyodide worker (a wasm32 heap holds the text, its parse and the result), an estimate, not a
#: measurement; the page router and the browser preflight refuse above it.
MAX_REQUEST_BYTES = {"runner": 8 * 2**20, "browser": 32 * 2**20}

# the details catalog_search has always given the model: keep them byte for byte
_RUNNER = "the local runner (tcmstudio serve)"
_BROWSER = "the browser runtime"
_NETWORK = "web access for this project"
_JOBS = "the runner's job service"
_BROWSER_JOBS = "this browser's job service (job workers and the site's private file storage)"
_INSTALL = "Install it (pip install …) on the runner machine; Studio never substitutes an approximation."
_PYODIDE = ("The browser runtime loads the Pyodide packages an entry lists in pyodide_packages before "
            "calling it; or run it on the local runner.")
# the runner is running, but its job service cannot start here (the detail says why)
_JOB_SERVICE = ("Restart the runner where its job service can start (Linux or macOS, or WSL on Windows, "
                "with bioagent installed); the detail says why it cannot start here.")
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")

Probe = Callable[[str], bool]


def requirement(need: str, detail: str, remedy: str, names: Iterable[str] | None = None) -> dict[str, Any]:
    """One Requirement {need, detail, remedy, names?}: what is missing, said as an English
    sentence, and the step that supplies it."""
    if need not in NEEDS:
        raise ValueError(f"need is one of {', '.join(NEEDS)}, not {need!r}")
    out: dict[str, Any] = {"need": need, "detail": str(detail), "remedy": str(remedy)}
    if names is not None:
        out["names"] = [str(n) for n in names]
    return out


def _remedy(code: str) -> str:
    from .governance import remedy_for
    return remedy_for(code)


def hosts_for(entry: Mapping[str, Any], args: Mapping[str, Any] | None = None) -> list[str]:
    """The hosts a call would reach: ``hosts_by`` for the argument that chooses them (the
    dataset a fetch downloads, the reviewed wrapper a provider call runs), else the
    entry's static hosts."""
    if args:
        for param, table in (entry.get("hosts_by") or {}).items():
            value = args.get(param)
            if isinstance(value, str) and isinstance(table, Mapping) and value in table:
                return list(table[value])
    return list(entry.get("hosts") or (entry.get("skill") or {}).get("network") or ())


def datasets_for(entry: Mapping[str, Any], args: Mapping[str, Any] | None = None) -> list[str]:
    """The datasets an entry reads: its ``data`` keys ('*' = any built store), or the one its
    ``data_from`` argument names (any built store when no call is in view)."""
    keys = [str(k) for k in entry.get("data") or ()]
    source = entry.get("data_from")
    if isinstance(source, Mapping) and source.get("param"):
        value = (args or {}).get(source["param"])
        keys.append(str(value) if isinstance(value, str) and value else "*")
    return keys


def _value(entry: Mapping[str, Any], args: Mapping[str, Any] | None, name: str) -> Any:
    value = (args or {}).get(name)
    if value not in (None, ""):
        return value
    return (((entry.get("parameters") or {}).get("properties") or {}).get(name) or {}).get("default")


def selected_engines(entry: Mapping[str, Any], args: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """The engines (execution.engines) these arguments choose, an argument left out counting
    as its default."""
    out = []
    for engine in (entry.get("execution") or {}).get("engines") or ():
        sel = engine.get("selected_by") or {}
        if _value(entry, args, sel.get("arg", "")) != sel.get("value"):
            continue
        if any(_value(entry, args, a) != v for a, v in (sel.get("with") or {}).items()):
            continue
        out.append(engine)
    return out


def _dataset_access(key: str) -> str:
    try:
        from bioagent.tcmdb import DATASETS
        return next((d.access for d in DATASETS if d.key == key), "")
    except Exception:                                           # noqa: BLE001
        return ""


def _dataset_requirement(keys: list[str], where: str) -> dict[str, Any]:
    named = [k for k in keys if k != "*"]
    if not named:
        detail = "a tcmdb store built or imported in this runtime"
        remedy = ("Fetch and build a dataset on this runner (job.tcmdb.fetch, then job.tcmdb.build; "
                  "tcmdb.datasets lists them), or import a built store with tcmdb.import_store."
                  if where == "runner" else
                  "Import a built store here with tcmdb.import_store, or fetch and build one on "
                  "the local runner (job.tcmdb.fetch, then job.tcmdb.build).")
        return requirement("dataset", detail, remedy, [])
    detail = (f"the {named[0]} dataset" if len(named) == 1 else
              "the " + ", ".join(named) + " datasets") + " built or imported in this runtime"
    key = named[0]
    if where != "runner":
        remedy = (f"Import a built {key} store here with tcmdb.import_store, or build it on the "
                  f"local runner (job.tcmdb.build {{dataset: {key}}}).")
    elif _dataset_access(key) == "manual":
        remedy = (f"Place its files by hand (tcmdb.datasets gives the instructions), then "
                  f"job.tcmdb.build {{dataset: {key}}}; or import a built store with "
                  "tcmdb.import_store.")
    else:
        remedy = (f"Fetch and build it on this runner: job.tcmdb.fetch {{dataset: {key}}}, then "
                  f"job.tcmdb.build {{dataset: {key}}}; or import a built store with "
                  "tcmdb.import_store.")
    return requirement("dataset", detail, remedy, named)


def _classify(items: Iterable[str], remedy: str = _JOB_SERVICE
              ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """What the job service says a kind lacks: package and command names, the research
    snapshots, or a sentence on why its job layer cannot run here (a job_service
    requirement with ``remedy``: the runner itself is running, so the remedy is never to
    start one)."""
    deps: list[str] = []
    data: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for item in items:
        item = str(item)
        if _PACKAGE.match(item):
            deps.append(item)
        elif item.startswith("snapshots"):
            data.append(requirement("dataset", f"the research {item} built on this runner",
                                    "Build them first: job.research.snapshot (one recorded run "
                                    "downloads, verifies and records every snapshot).",
                                    ["research snapshots"]))
        else:
            other.append(requirement("job_service", item, remedy))
    return deps, data, other


def requirements(entry: Mapping[str, Any], *, where: str, network: bool = True, jobs: bool = True,
                 built: Collection[str] | None = None, args: Mapping[str, Any] | None = None,
                 probe: Probe | None = None, kind_missing: Sequence[str] | None = None,
                 request_bytes: int | None = None,
                 browser_jobs: bool | None = None,
                 browser_snapshots: bool | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Every requirement this entry lacks in ``where``, each with its reason code, in the
    order catalog_search has always named them (runtime, install, web access, job service;
    engines, the request size and datasets in between).

    ``args``: the call's arguments (engines, ``network_if``, ``data_from`` and ``hosts_by``
    follow them); None asks about the entry as such. ``probe``: asks whether a dependency is
    installed here (the runner's packages; the browser's loaded Pyodide packages and the
    chosen engines are checked only with it); without it, what the catalog's own probe found
    (``available``/``missing``) counts. ``built``: the dataset keys built in this runtime's
    data hub, or None not to judge data. ``kind_missing``: what the runner's job service says
    this job kind lacks. ``request_bytes``: the size of the call's arguments as JSON.
    ``browser_jobs``: in the browser, whether the page's job service is running (it runs the
    entries whose ``execution.browser_job`` says so; the page tells the worker in its context);
    None when not known, which counts as not running. ``browser_snapshots``: whether the
    research snapshots are built in this browser's private storage (``research.run`` there
    reads only those); None when the page has not said, which is not judged."""
    out: list[tuple[str, dict[str, Any]]] = []
    exec_ = entry.get("exec") or ()
    if where not in exec_:
        if "runner" in exec_:
            out.append(("needs_runner", requirement("runner", _RUNNER, _remedy("NEEDS_RUNNER"))))
        else:
            out.append(("no_runtime", requirement("browser_runtime", _BROWSER,
                                                  "Run it from the Studio page, in its browser runtime.")))
    if probe is not None and where == "runner":
        missing = [d for d in entry.get("heavy") or () if not probe(d)]
    elif entry.get("available") is False:
        missing = list(entry.get("missing") or ())
    else:
        missing = []
    # the runner's catalog folds what the job service says into ``missing``: count each once
    deps, data, other = _classify(dict.fromkeys([*missing, *(kind_missing or ())]))
    if deps:
        out.append(("missing_deps", requirement("dependency", "install: " + ", ".join(deps),
                                                _INSTALL, deps)))
    if probe is not None and where == "runner":
        for engine in selected_engines(entry, args):
            names = [n for r in engine.get("requires") or () for n in r.get("names") or ()
                     if not probe(n)]
            if names:
                sel = engine["selected_by"]
                out.append(("missing_engine", requirement(
                    "dependency", f"install: {', '.join(names)} ({sel['arg']} {sel['value']})",
                    f"Install it on the runner machine, or choose another {sel['arg']}; Studio "
                    "never runs another method in its place.", names)))
    if probe is not None and where == "browser":
        names = [p for p in entry.get("pyodide_packages") or () if not probe(p)]
        if names:
            out.append(("missing_deps", requirement(
                "dependency", f"{', '.join(names)} loaded in the browser runtime", _PYODIDE, names)))
    limit = ((entry.get("limits") or {}).get("max_request_bytes") or MAX_REQUEST_BYTES).get(where)
    if request_bytes is not None and where == "browser" and limit and request_bytes > limit:
        out.append(("too_large_for_browser", requirement(
            "memory", f"a request of at most {limit / 2**20:.0f} MB in the browser runtime "
                      f"(this one is {request_bytes / 2**20:.1f} MB)",
            "Run it on the local runner, or pass a smaller input.")))
    if built is not None:
        built_set = set(built)
        lacking = [k for k in datasets_for(entry, args)
                   if (k == "*" and not built_set) or (k != "*" and k not in built_set)]
        if lacking:
            out.append(("needs_dataset", _dataset_requirement(lacking, where)))
    out += [("needs_dataset", r) for r in data]
    if entry.get("network_if"):
        # reaches its hosts only when this argument is set (allow_remote: a sequence sent out,
        # a structure fetched); the entry as such needs no network
        needs_net = bool((args or {}).get(entry["network_if"]))
    else:
        needs_net = bool(entry.get("network"))
    if needs_net and not network:
        out.append(("network_off", requirement("network", _NETWORK, _remedy("NETWORK_OFF"),
                                               hosts_for(entry, args))))
    out += [("needs_job_service", r) for r in other]
    in_page = where == "browser" and bool((entry.get("execution") or {}).get("browser_job"))
    if in_page and entry.get("job") and not browser_jobs and not other:
        out.append(("needs_job_service", requirement(
            "job_service", _BROWSER_JOBS,
            "Use a current Chromium-based browser such as Chrome or Edge, outside a private window "
            "(the jobs keep their files in the site's private storage; Firefox and Safari have not "
            "been tested with them), or connect the local runner.")))
    elif in_page and browser_jobs and browser_snapshots is False and entry.get("id") == "job.research.run":
        # worded as the page's own catalog words it (web/js/core/catalog.js entryState)
        out.append(("needs_dataset", requirement(
            "dataset", "the research snapshots built in this browser",
            "Build them first in this browser (job.research.snapshot: the NPASS, CMAUP and LOTUS "
            "files plus STRING and Reactome), or connect a runner that has them.",
            names=["research snapshots"])))
    elif not in_page and entry.get("job") and not jobs and not other:
        # (the job service's own sentence, when it gave one, already says this)
        out.append(("needs_job_service", requirement(
            "job_service", _JOBS, _JOB_SERVICE if where == "runner" else _remedy("NEEDS_RUNNER"))))
    return out


def refusal_requirements(kind: str, missing: Iterable[str] = (), *, message: str = "",
                         hint: str = "", need: str | None = None
                         ) -> tuple[str, list[dict[str, Any]]]:
    """A refusal of the runner's job service (jobs.JobRefused: error type ``kind``, its
    ``missing`` list, message and hint) as ``(reason, requires)``, in the words the runner's
    catalog uses for the same entry. ``missing`` is classified as :func:`requirements`
    classifies it: package and command names are one dependency requirement (missing_deps),
    the research snapshots a dataset (needs_dataset), any other sentence the job service
    (needs_job_service). A network_off refusal is this runner's network switch. A refusal
    that lists nothing missing is this runner's allow_remote setting when ``need`` is
    compute_setting (remote_upload_off), else its job service (the refusal's message is the
    detail). The refusal's hint is the remedy wherever the catalog has none of its own."""
    if kind == "network_off":
        return "network_off", [requirement("network", "this runner's network access",
                                           hint or _remedy("NETWORK_OFF"))]
    deps, data, other = _classify(dict.fromkeys(str(m) for m in missing or ()),
                                  remedy=hint or _JOB_SERVICE)
    pairs: list[tuple[str, dict[str, Any]]] = []
    if deps:
        pairs.append(("missing_deps", requirement("dependency", "install: " + ", ".join(deps),
                                                  _INSTALL, deps)))
    pairs += [("needs_dataset", r) for r in data]
    pairs += [("needs_job_service", r) for r in other]
    if not pairs and need == "compute_setting":
        pairs.append(("remote_upload_off", requirement(
            "compute_setting", "this runner's permission to send data to a third-party service "
                               "(allow_remote)",
            hint or "Turn it on in Settings → Compute, or run the job locally.")))
    elif not pairs:
        pairs.append(("needs_job_service", requirement(
            "job_service", " ".join(str(message).split()) or _JOBS, hint or _JOB_SERVICE)))
    return pairs[0][0], [r for _, r in pairs]


def state_of(requires: Iterable[Mapping[str, Any]]) -> str:
    states = {STATE_OF_NEED.get(r.get("need"), "unsupported") for r in requires}
    return next((s for s in READINESS[1:] if s in states), "ready")


def readiness(entry: Mapping[str, Any], *, where: str, **kwargs: Any) -> dict[str, Any]:
    """``{state, requires}`` for ``entry`` in ``where`` (the keywords of :func:`requirements`)."""
    reqs = [r for _, r in requirements(entry, where=where, **kwargs)]
    return {"state": state_of(reqs), "requires": reqs}


def _latest(verification: Mapping[str, Any]) -> str | None:
    dates = [str(lv.get("at")) for lv in (verification.get("levels") or {}).values()
             if isinstance(lv, Mapping) and lv.get("at")]
    return max(dates) if dates else None


def audit(items: Iterable[Mapping[str, Any]], *, where: str, network: bool, jobs: bool,
          built: Collection[str] | None = None, probe: Probe | None = None,
          kind_missing: Mapping[str, Sequence[str]] | None = None, state: str | None = None,
          kind: str | None = None, category: str | None = None,
          limit: int | None = None, browser_jobs: bool = False,
          browser_snapshots: bool | None = None) -> dict[str, Any]:
    """Every entry against this runtime: implementation, readiness with what is missing,
    and the committed verification; counts by readiness, implementation and verification.
    Filters narrow the list, never the counts. Network reachability is never claimed: an
    entry that reaches a host is ready when web access is on, and no request is made."""
    rows = []
    for e in items:
        r = readiness(e, where=where, network=network, jobs=jobs, built=built, probe=probe,
                      kind_missing=(kind_missing or {}).get(e["id"]), browser_jobs=browser_jobs,
                      browser_snapshots=browser_snapshots)
        v = e.get("verification") or {}
        rows.append({"id": e["id"], "kind": e["kind"], "category": e.get("category"),
                     "implementation": (e.get("state") or {}).get("implementation", "registered"),
                     "readiness": r["state"], "requires": r["requires"],
                     "verification": {"status": v.get("status", "UNVERIFIED"), "at": _latest(v)}})

    def tally(key: Callable[[dict[str, Any]], str], order: Sequence[str]) -> dict[str, int]:
        seen = [key(row) for row in rows]
        extra = sorted(set(seen) - set(order))
        return {k: seen.count(k) for k in (*order, *extra) if seen.count(k)}

    picked = [row for row in rows if (not state or row["readiness"] == state)
              and (not kind or row["kind"] == kind) and (not category or row["category"] == category)]
    shown = picked if limit is None else picked[:max(0, int(limit))]
    return {
        "where": where, "network": {"enabled": bool(network)}, "jobs": bool(jobs),
        "datasets_built": sorted(built) if built is not None else None,
        "counts": {"entries": len(rows),
                   "by_readiness": tally(lambda r: r["readiness"], READINESS),
                   "by_implementation": tally(lambda r: r["implementation"], IMPLEMENTATION),
                   "by_verification": tally(lambda r: r["verification"]["status"],
                                            ("PASS", "FAIL", "BLOCKED", "UNVERIFIED", "SKIPPED",
                                             "NOT_APPLICABLE"))},
        "filters": {k: v for k, v in (("state", state), ("kind", kind), ("category", category),
                                      ("limit", limit)) if v not in (None, "")},
        "matched": len(picked), "entries": shown,
        "note": ("Readiness is this runtime's own: its exec, installed dependencies, built datasets, "
                 "web-access switch and job service. 'ready' for an entry that reaches a host means "
                 "the call may be made, not that the host answers: no request was sent. "
                 "Verification comes from committed records only; UNVERIFIED means no record, "
                 "not a failure.")}
