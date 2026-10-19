"""The Python side of the browser's science jobs: one entry, four functions, JSON in and out.

A browser job (studio/docs/BROWSER_JOBS.md) runs its stages in a job worker, a Pyodide
holding the same bundle as the tool runtime. Each stage that computes calls :func:`call`
with a function name and its arguments as JSON text, and gets JSON text back; only strings
cross into JavaScript. The functions are the runner's commands for the same work, so a
browser run and a runner run of the same inputs give the same tables:

``plan_snapshots``   the raw files the research snapshots need and which are here
                     (``bioagent snapshots plan``)
``build_snapshots``  verify the raw files and build the snapshots and their ledger
                     (``bioagent snapshots build --offline``): the page fetched or imported
                     the files, so nothing is downloaded here, and the report says so
``run_research``     the research loop (``bioagent research``), with its exit code, the
                     summary it prints and the stages that failed
``verify_research``  the exports re-checked (``python -m bioagent.analysis.np_exports
                     verify``) and held to what the runner's ``research.run`` job requires
                     of its outputs (``tcmstudio.kinds``)
``self_test``        the planted world's known answer (``tcmstudio.browser_selftest``): a
                     small world studied the way ``run_research`` studies one, its tables
                     compared with the ones native CPython committed

One difference is recorded rather than hidden. A browser cannot start a process, so on
Emscripten the research loop's governed analysis runs in this process (``isolate=False``):
through the same PSH broker and audit chain, and the artifact records ``isolation:
in_process`` for each stage that ran so. Natively the loop decides, as ``bioagent research``
does.

``call`` never raises. A failure is ``{"ok": false, "error": {"type", "message"}}`` with
``type`` ``bad_arguments`` (the arguments are not what the function takes), ``refused``
(it cannot run here: a package this runtime does not hold), ``cancelled`` (interrupted) or
``runtime_error``. ``emit(kind, data)``, when given, receives the log lines of a snapshot
build and of a research run as ``("log", {"level", "text"})``, as they happen.

``python -m tcmstudio.browser_jobs FN --args JSON`` runs one function natively and prints
what :func:`call` returns: the parity check runs the same arguments both ways. Everything
beyond the standard library is imported when a function runs, so importing this module in
the worker costs nothing.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Mapping

__all__ = ["BadArguments", "FUNCTIONS", "build_snapshots", "call", "main", "plan_snapshots",
           "run_research", "self_test", "verify_research"]

Emit = Callable[[str, dict[str, Any]], Any]

_MESSAGE_MAX = 2000
_BACKGROUNDS = ("assayed", "reactome")
_HITS = ("potency", "screening")


class BadArguments(ValueError):
    """The arguments are not what the function takes."""


# ------------------------------------------------------------------------- arguments

def _only(args: Mapping[str, Any], names: tuple[str, ...]) -> None:
    extra = sorted(k for k, v in args.items() if k not in names and v is not None)
    if extra:
        raise BadArguments(f"unknown argument(s) {', '.join(extra)}; this function takes "
                           f"{', '.join(names)}")


def _text(args: Mapping[str, Any], name: str, *, required: bool = True,
          default: str = "") -> str:
    value = args.get(name)
    if value is None or value == "":
        if required:
            raise BadArguments(f"{name}: required")
        return default
    if not isinstance(value, str):
        raise BadArguments(f"{name}: a string, not {type(value).__name__}")
    return value


def _choice(args: Mapping[str, Any], name: str, choices: tuple[str, ...]) -> str:
    value = _text(args, name, required=False, default=choices[0])
    if value not in choices:
        raise BadArguments(f"{name}: one of {', '.join(choices)}, not {value!r}")
    return value


def _flag(args: Mapping[str, Any], name: str, default: bool) -> bool:
    value = args.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise BadArguments(f"{name}: true or false, not {value!r}")
    return value


def _strings(args: Mapping[str, Any], name: str) -> list[str]:
    value = args.get(name)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise BadArguments(f"{name}: a list of names")
    return list(value)


def _sources(args: Mapping[str, Any]) -> list[str]:
    from bioagent.sources.pipeline import RESEARCH_SOURCES

    sources = list(dict.fromkeys(_strings(args, "sources"))) or list(RESEARCH_SOURCES)
    unknown = [s for s in sources if s not in RESEARCH_SOURCES]
    if unknown:
        raise BadArguments(f"sources: no research source {unknown}; one of "
                           f"{', '.join(RESEARCH_SOURCES)}")
    return sources


def _isolate() -> bool | None:
    """Where the research loop runs its governed analysis: in this process under Emscripten,
    which cannot start one; natively, wherever the loop decides (``None``)."""
    return False if sys.platform == "emscripten" else None


def _log(emit: Emit | None) -> Callable[[str], None]:
    def log(line: Any) -> None:
        if emit is not None:
            emit("log", {"level": "info", "text": str(line)})
    return log


# ------------------------------------------------------------------------- functions

def plan_snapshots(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{raw_dir, sources?, network?}`` → ``bioagent.sources.pipeline.plan`` (each file the
    snapshots need: whether it is here and how it was verified; what is missing, its size
    and hosts) and ``required``, the file names in the order the build reads them."""
    del emit
    from bioagent.sources import pipeline

    _only(args, ("raw_dir", "sources", "network"))
    raw = _text(args, "raw_dir")
    sources, network = _sources(args), _flag(args, "network", True)
    doc = pipeline.plan(raw, sources, network=network)
    return {**doc, "required": [a.filename for a in pipeline.required_files(
        sources, network=network)]}


def build_snapshots(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{raw_dir, snapshot_root, ledger, sources?, network?, formulas?, report_path?}`` →
    the build report (``tcmscience.snapshots/1``) of ``build_research_snapshots``, run
    offline: the files are verified against their reviewed checksums or sizes and parsed,
    and none is downloaded here (``parameters.offline`` is true in the report). ``formulas``
    (each with its source book, e.g. 桂枝汤《伤寒论》) are added to the herb layer, as the
    command's ``--formula``."""
    from bioagent.sources import pipeline

    _only(args, ("raw_dir", "snapshot_root", "ledger", "sources", "network", "formulas",
                 "report_path"))
    return pipeline.build_research_snapshots(
        _text(args, "raw_dir"), _text(args, "snapshot_root"), _text(args, "ledger"),
        sources=_sources(args), network=_flag(args, "network", True),
        formulas=_strings(args, "formulas"), offline=True,
        report_path=_text(args, "report_path", required=False) or None, log=_log(emit))


def run_research(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{question, formula?, disease?, activity?, optional?, background?, hits?,
    permutations?, accept_review?, snapshot_root, ledger, state_dir, out_dir}`` →
    ``{exit, summary, failed, refused}``, as ``bioagent research`` would end: ``exit`` 0
    (released), 1 (release not authorised), 2 (refused: ``refused`` says why, and nothing
    else is set) or 3 (a workflow stage FAILed its own check: ``failed`` names it). The
    defaults are the command's: the assayed background, potency hits, 1000 permutations,
    NPASS as the activity source and STRING as the optional one, profile trusted_local."""
    from dataclasses import replace

    from bioagent.analysis.network_pharmacology import Parameters
    from bioagent.research.loop import (QuestionRefused, ResearchRefused, default_protocol,
                                        parse_question, research_summary)
    from bioagent.research.loop import run_research as research

    _only(args, ("question", "formula", "disease", "activity", "optional", "background",
                 "hits", "permutations", "accept_review", "snapshot_root", "ledger",
                 "state_dir", "out_dir"))
    text = _text(args, "question")
    permutations = args.get("permutations", 1000)
    if permutations is None:
        permutations = 1000
    if isinstance(permutations, bool) or not isinstance(permutations, int) or permutations < 1:
        raise BadArguments(f"permutations: a positive whole number, not {permutations!r}")
    params = replace(Parameters(), background=_choice(args, "background", _BACKGROUNDS),
                     hits=_choice(args, "hits", _HITS), permutations=permutations)
    activity = tuple(_strings(args, "activity") or ["npass"])
    optional = tuple(_strings(args, "optional") or ["string"])
    paths = {k: _text(args, k) for k in ("snapshot_root", "ledger", "state_dir", "out_dir")}
    accept_review = _flag(args, "accept_review", False)
    isolate = _isolate()
    log = _log(emit)
    try:
        question = parse_question(text, disease=_text(args, "disease", required=False),
                                  formula=_text(args, "formula", required=False))
        protocol = default_protocol(question, activity=activity, optional=optional,
                                    parameters=params)
        log(f"research {question.formula_id}: protocol {protocol.digest}"
            + (", analysis in this process" if isolate is False else ""))
        run = research(question, protocol=protocol, snapshot_root=paths["snapshot_root"],
                       ledger_path=paths["ledger"], state_dir=paths["state_dir"],
                       output_dir=paths["out_dir"], accept_review=accept_review,
                       profile="trusted_local", isolate=isolate)
    except (QuestionRefused, ResearchRefused, ValueError) as exc:
        # what the command prints as "refused: ..." and exits 2 for
        log(f"refused: {exc}")
        return {"exit": 2, "summary": None, "failed": [], "refused": str(exc)}
    summary, failed = research_summary(run, question)
    code = 3 if failed else (0 if run.released else 1)
    log(f"research {run.run_id}: {'released' if run.released else 'not released'}"
        + (f"; {len(failed)} stage(s) failed their own check" if failed else ""))
    return {"exit": code, "summary": summary, "failed": list(failed), "refused": None}


def verify_research(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{out_dir, snapshot_root?, ledger?}`` → ``{status, checks, problems, report}``.

    ``report`` is ``np_exports.verify``: every digest the manifest records, the
    enrichment's p and q recomputed from the tables, and, given the snapshots, the analysis
    recomputed to the same tables. Then the outputs are held to what the runner's
    ``research.run`` job requires (``tcmstudio.kinds``): every required file present and
    not empty, and each validator it names (the artifact's and the manifest's digests,
    readable JSON) passing; those are the checks ``required_artefacts`` and
    ``validator:<name>``. ``status`` is the aggregate of all of them; ``problems`` says
    what failed."""
    del emit
    from pathlib import Path

    from bioagent.analysis.np_exports import verify
    from bioagent.validation import FAIL, PASS, aggregate

    from .kinds import KINDS

    _only(args, ("out_dir", "snapshot_root", "ledger"))
    out = Path(_text(args, "out_dir"))
    root = _text(args, "snapshot_root", required=False)
    ledger = _text(args, "ledger", required=False)
    if bool(root) != bool(ledger):
        raise BadArguments("give both snapshot_root and ledger, or neither")
    report = verify(out, snapshot_root=root or None, ledger=ledger or None)
    checks = [dict(c) for c in report["checks"]]
    problems = [f"{c['name']}: {c['detail']}" for c in checks if c["status"] == FAIL]

    # the runner's own collection of a research.run job (jobs.JobService._check_artefacts)
    kind = KINDS["research.run"]
    missing = []
    verdicts: dict[str, str | None] = {}
    for name, rel, required in kind.artefacts:
        path = out / rel
        if not path.is_file() or path.stat().st_size == 0:
            if required:
                missing.append(f"required artefact {name!r} ({rel}) is missing or empty")
            continue
        check = kind.validators.get(name)
        if check is not None:
            verdicts[name] = check(path)
    checks.append({"name": "required_artefacts", "status": FAIL if missing else PASS,
                   "detail": "; ".join(missing) or
                   f"the {len(kind.artefacts)} outputs research.run requires are present"})
    problems += missing
    for name, verdict in verdicts.items():
        checks.append({"name": f"validator:{name}", "status": FAIL if verdict else PASS,
                       "detail": verdict or "passes the runner's check"})
        if verdict:
            problems.append(f"artefact {name!r}: {verdict}")
    return {"status": aggregate(c["status"] for c in checks), "checks": checks,
            "problems": problems, "report": report}


def self_test(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{work_dir?}`` → the known-answer report (``tcmstudio.browser-selftest/1``): ``status``
    PASS only when the planted world studied here gives the committed tables (identical, or
    every number within 1e-12) and its exports recompute."""
    from .browser_selftest import run

    _only(args, ("work_dir",))
    return run(args, emit)


FUNCTIONS: Mapping[str, Callable[..., dict[str, Any]]] = {
    "plan_snapshots": plan_snapshots, "build_snapshots": build_snapshots,
    "run_research": run_research, "verify_research": verify_research, "self_test": self_test}


# ------------------------------------------------------------------------- the entry

def _error(kind: str, message: str) -> str:
    return json.dumps({"ok": False, "error": {"type": kind, "message": message[:_MESSAGE_MAX]}},
                      ensure_ascii=False)


def call(fn: str, args_json: str = "{}", emit: Emit | None = None) -> str:
    """Run function ``fn`` with ``args_json`` (a JSON object) and return JSON text:
    ``{"ok": true, "result": ...}`` or ``{"ok": false, "error": {"type", "message"}}``.
    Never raises."""
    try:
        try:
            args = json.loads(args_json) if str(args_json or "").strip() else {}
        except ValueError as exc:
            raise BadArguments(f"the arguments are not JSON ({exc})") from None
        if not isinstance(args, dict):
            raise BadArguments("the arguments must be a JSON object")
        function = FUNCTIONS.get(str(fn))
        if function is None:
            raise BadArguments(f"no function {fn!r}; one of {', '.join(FUNCTIONS)}")
        result = function(args, emit)
        try:
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False,
                              allow_nan=False)
        except (TypeError, ValueError) as exc:
            return _error("runtime_error", f"{fn} returned a result that is not JSON: {exc}")
    except KeyboardInterrupt:
        return _error("cancelled", f"{fn} was interrupted")
    except BadArguments as exc:
        return _error("bad_arguments", str(exc))
    except ModuleNotFoundError as exc:
        return _error("refused", f"{fn} cannot run here: {exc} (a package this runtime does "
                                 "not hold)")
    except Exception as exc:                                    # noqa: BLE001 - never raises
        return _error("runtime_error", f"{type(exc).__name__}: {exc}")


def main(argv: list[str] | None = None) -> int:
    """``python -m tcmstudio.browser_jobs FN --args JSON``: print what :func:`call` returns.
    Log lines go to stderr. Exit 0 when the call succeeded, 2 for bad arguments, else 1."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="python -m tcmstudio.browser_jobs",
                                     description="run one browser-job function natively")
    parser.add_argument("fn", choices=sorted(FUNCTIONS))
    parser.add_argument("--args", default="{}",
                        help="arguments as JSON, @FILE to read them from a file, or - for stdin")
    a = parser.parse_args(argv)
    spec = a.args
    try:
        text = (sys.stdin.read() if spec == "-" else
                Path(spec[1:]).read_text(encoding="utf-8") if spec.startswith("@") else spec)
    except OSError as exc:
        print(f"--args: {exc}", file=sys.stderr)
        return 2

    def emit(kind: str, data: dict[str, Any]) -> None:
        print(f"{kind}: {data.get('text', json.dumps(data, ensure_ascii=False))}",
              file=sys.stderr)

    out = call(a.fn, text, emit)
    print(out)
    doc = json.loads(out)
    if doc["ok"]:
        return 0
    return 2 if doc["error"]["type"] == "bad_arguments" else 1


if __name__ == "__main__":
    sys.exit(main())
