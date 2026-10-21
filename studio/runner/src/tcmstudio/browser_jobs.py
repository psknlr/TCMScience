"""The Python side of the browser's science jobs: one entry, a few functions, JSON in and out.

A browser job (studio/docs/BROWSER_JOBS.md) runs its stages in a job worker, a Pyodide
holding the same bundle as the tool runtime. Each stage that computes calls :func:`call`
with a function name and its arguments as JSON text, and gets JSON text back; only strings
cross into JavaScript. The functions are the runner's commands for the same work, so a
browser run and a runner run of the same inputs are to give the same tables (natively that
is tested; in the browser the self-test and the parity check say whether they did):

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
``run_rnaseq_counts`` the RNA-seq count-matrix mode (``bioagent rnaseq --counts``), as
                     ``pipeline.rnaseq`` runs it with counts
``run_scrna``        the single-cell pipeline on count tables (``bioagent scrna``), as
                     ``pipeline.scrna`` runs it with the built-in engines
``admet_split``      the TDC ADMET group archive's endpoints split into their SMILES and
                     targets, for the page's chemistry (RDKit.js) to prepare
``admet_train``      one endpoint's model, domain and card from that chemistry, as
                     ``bioagent admet --build-models`` trains it (the admet.models job)
``admet_read``       a molecule file or one SMILES read as ``bioagent admet`` reads it
``run_admet``        the ADMET pipeline (``bioagent admet``) on the page's chemistry and
                     domain search, as ``pipeline.admet`` runs it
``dock_read``        prepared docking inputs (PDBQT) read and checked as ``bioagent dock
                     RECEPTOR.pdbqt LIGANDS.pdbqt`` reads them, and the plan of Vina runs the
                     page computes: each run's exact receptor and ligand text, box and settings
``run_dock``         the prepared docking (``bioagent.docking.prepared``) on the Vina results
                     the page computed, as ``pipeline.dock`` runs it with a .pdbqt receptor
``verify_pipeline``  a pipeline run re-checked (its ``verify_run``) and held to what the
                     runner's job of that kind requires of its outputs; and the ADMET models
                     held to their cards
``self_test``        the planted world's known answer (``tcmstudio.browser_selftest``): a
                     small world studied the way ``run_research`` studies one, its tables
                     compared with the ones native CPython committed

One difference is recorded rather than hidden. A browser cannot start a process, so on
Emscripten the research loop's governed analysis runs in this process (``isolate=False``):
through the same PSH broker and audit chain, and the artifact records ``isolation:
in_process`` for each stage that ran so. Natively the loop decides, as ``bioagent research``
does.

The pipelines run in the browser only in part: the count-matrix RNA-seq with the built-in
DESeq2 implementation, and the single-cell analysis on count tables with the built-in
parts and built-in Harmony. A call that asks for more (PyDESeq2, Scanpy, scVI, a 10x HDF5,
an .h5ad, a directory) comes back refused with the reason, as the catalog's
``execution.browser_job.runner_only`` says, and is never run another way. So does an input
larger than a browser worker holds (wasm32 caps its memory near 4 GB): more than
``SC_CELLS_MAX`` cells, or a table of more than ``SC_TABLE_COUNTS_MAX`` cells x genes; a
matrix of more than ``RNASEQ_COUNTS_MAX`` genes x samples (``RNASEQ_GENES_MAX`` genes,
``RNASEQ_SAMPLES_MAX`` samples at most), counted from the tables a line at a time before the
analysis; and a run that runs out of memory
all the same (``MemoryError``) is refused with that reason, not reported as an error. A
pipeline's refusal says what would run it in ``need``: ``runner`` (an engine or a format
this browser does not hold), ``memory`` (too large for a browser worker), or null (the
inputs themselves cannot be analysed, wherever they run).

ADMET runs in the browser with its chemistry computed by the page (RDKit.js, which Pyodide
does not have) and handed over as prepared sets (``bioagent.admet.engine``: the
``PrecomputedEngine`` reads them back and holds them to their molecules), the domain search
by the page's kernels and checked here (``PrecomputedSimilarity``), and everything else in
this Python: the models are scikit-learn's, trained here from the TDC archive, and the
pipeline is ``bioagent.admet``'s. An SDF or .mol file is refused for the runner (RDKit's
SDMolSupplier is not ported), and so is a run of more than ``ADMET_MOLECULES_MAX``
molecules (``need`` ``memory``).

Docking runs in the browser on prepared inputs only (``bioagent.docking.prepared``): a rigid
receptor and ligands as PDBQT, used as given. AutoDock Vina 1.2.7, compiled to WebAssembly,
runs in the page's own workers, one Vina run each (``dock_read`` writes the plan of them);
their results come back here as files read by ``PrecomputedVina``, which holds each to the
receptor, ligand and settings it was asked for, and everything else is this Python. A
receptor or ligands that are not ``.pdbqt`` (they need preparing: meeko and RDKit, the
runner's) are refused for the runner, and so are more than ``DOCK_LIGANDS_MAX`` ligands, a
receptor of more than ``DOCK_RECEPTOR_ATOMS_MAX`` atoms, a ligand of more than
``DOCK_LIGAND_ATOMS_MAX`` and a box of more than ``DOCK_BOX_VOLUME_MAX`` Å³ (``need``
``memory``).

``call`` never raises. A failure is ``{"ok": false, "error": {"type", "message"}}`` with
``type`` ``bad_arguments`` (the arguments are not what the function takes), ``refused``
(it cannot run here: a package this runtime does not hold, or, with ``need`` ``memory``,
more memory than it has; for ``admet_split``, ``admet_train`` and ``admet_read`` also
inputs it cannot use, with ``need`` ``runner`` when the runner reads them and none when
nothing does), ``cancelled`` (interrupted) or ``runtime_error``.
``emit(kind, data)``, when given, receives the log lines of a snapshot
build, a research run and a pipeline run as ``("log", {"level", "text"})``, and a
pipeline's stages as ``("progress", {"fraction", "stage", "message"})``, as they happen.

``python -m tcmstudio.browser_jobs FN --args JSON`` runs one function natively and prints
what :func:`call` returns: the parity check runs the same arguments both ways. Everything
beyond the standard library is imported when a function runs, so importing this module in
the worker costs nothing.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, Mapping

__all__ = ["ADMET_MOLECULES_MAX", "BadArguments", "DOCK_BOX_VOLUME_MAX", "DOCK_LIGANDS_MAX",
           "DOCK_LIGAND_ATOMS_MAX", "DOCK_PLAN_SCHEMA", "DOCK_RECEPTOR_ATOMS_MAX", "FUNCTIONS",
           "Refused", "admet_read", "admet_split", "admet_train", "build_snapshots", "call",
           "dock_read", "main", "plan_snapshots", "run_admet", "run_dock", "run_research",
           "run_rnaseq_counts", "run_scrna", "self_test", "verify_pipeline", "verify_research"]

Emit = Callable[[str, dict[str, Any]], Any]

_MESSAGE_MAX = 2000
_BACKGROUNDS = ("assayed", "reactome")
_HITS = ("potency", "screening")
#: The pipeline module of each pipeline job kind the browser runs (its ``verify_run``).
_PIPELINES = {"pipeline.rnaseq": "bioagent.omics.rnaseq",
              "pipeline.scrna": "bioagent.omics.scrna",
              "pipeline.admet": "bioagent.admet.pipeline",
              # the browser docks prepared inputs only (the runner's job checks either mode)
              "pipeline.dock": "bioagent.docking.prepared"}
#: The largest inputs a browser worker takes (wasm32 caps its memory near 4 GB), from whole
#: runs in Pyodide 314.0.7 under Node (the WebAssembly memory at its peak). Single cell: the
#: doublet scores hold one table's cells x genes densely, so that product is bounded per table
#: as well as the cells: 5,424 cells in four tables of 1,356 x 33,000 genes, 1.9 GB; one table
#: of 2,000 x 33,000, 2.2 GB (of 2,712 x 33,000, 3.1 GB: too near the ceiling); 13,568 cells
#: of 1,200 genes reached it. RNA-seq: 10,000,000 counts (100,000 genes x 100 samples, 50,000
#: x 200) took 2.0 and 1.8 GB, and 100,000 x 200 took 3.6 GB. Larger inputs are refused before
#: the analysis, for the runner.
SC_CELLS_MAX = 6000
SC_TABLE_COUNTS_MAX = 66_000_000          # one table's cells x genes: 2,000 x 33,000
RNASEQ_GENES_MAX = 100_000
RNASEQ_SAMPLES_MAX = 200
RNASEQ_COUNTS_MAX = 10_000_000            # genes x samples
_HOLDS = "a browser worker can hold (wasm32, 4 GB): run it on the runner"
#: ADMET: the molecules of one run at most, the most the page's chemistry worker prepares at once
#: (web/js/scientific/chem/chem.worker.js MAX_MOLECULES). Measured in Pyodide 314.0.7 under Node
#: (run_admet against 22 models of the TDC group's sizes, 64,490 domain rows; the WebAssembly memory
#: at its peak): 1,000 molecules 224 MB, 5,000 424 MB, 10,000 625 MB, 20,000 1.0 GB (about 130 MB
#: and 45 MB per 1,000); 20,000 took 23 s with the page's similarities and 113 s (1.1 GB) when the
#: CPU searched the domain here, and admet.tsv was the native run's bytes both ways. The prepared
#: files a browser copies into the worker add about 10 KB a molecule. A model build needs no bound of its own:
#: admet_train on the TDC group's largest endpoint at its size (cyp2d6_veith, 10,504 training and 2,626 test
#: molecules, synthetic) peaked at 877 MB and took 28 s there, its card the native one but for the model's digest.
ADMET_MOLECULES_MAX = 20_000
#: Molecule files the browser's chemistry does not read (RDKit's SDMolSupplier is not ported).
_ADMET_RUNNER_SUFFIXES = (".sdf", ".sdf.gz", ".mol")
_MOLECULES_FILE = "molecules.json"
#: Docking: the largest prepared run the page's Vina workers take, from AutoDock Vina 1.2.7's WebAssembly build
#: (web/js/scientific/dock/vendor) under Node 22, one module per worker thread, on the 3PTB fixture
#: (BioScience-Harness/tests/fixtures/docking/prepared: a 701-atom pocket, an 18 Å box, exhaustiveness 8):
#: - Time. One ligand takes 3.6-14.3 s on one thread (mean 8.3 s over the six fixture ligands; 49.6 s for all six);
#:   24 runs took 52 s on 4 workers (4 cores) and 68 s on 3, so a run of DOCK_LIGANDS_MAX ligands of that size
#:   takes about 3.5 min on 4 workers and 14 min on one, eight times that at exhaustiveness 64. Larger ligands
#:   take longer: 40 atoms and 7 torsions 78 s, a 54-atom peptide (23 torsions) 534 s, glycyrrhizin (66 atoms, 15
#:   torsions) 507 s, on one thread.
#: - Memory, per Vina instance (a worker keeps its peak). Each run builds Vina's tables of pair energies: one over
#:   the scoring function's atom types (about 330 MB, whatever the inputs) and one over the ligand's atom pairs
#:   (0.3 MB a pair), and the grid maps of the box; each Monte Carlo task (exhaustiveness of them) then holds a copy
#:   of the model, the receptor's atoms in it. Measured: 357-429 MB for the fixture ligands (12-23 atoms), 611 MB at
#:   40 atoms, 899 MB at 54, 1.38 GB at 66, 1.96 GB at 79; an 18 Å box 357 MB, 60 Å 618 MB, 90 Å 1.29 GB, 126 Å
#:   2.78 GB (a 12-atom ligand); 79 atoms in a 126 Å box ran out of wasm32's 4 GB (std::bad_alloc). The receptor
#:   costs memory with the exhaustiveness, about 10 MiB a Monte Carlo task per 100,000 atoms: the pocket tiled to
#:   100,243 atoms (copies outside the box) took 435 MiB at exhaustiveness 8 and 1,008 MiB at 64 (a 12-atom ligand,
#:   an 18 Å box; 358 MiB at either with the 701-atom pocket), 1,120 MiB at 64 in a 60 Å box; 70,100 atoms 449 MB
#:   and 140,200 atoms 458 MB at exhaustiveness 8. It costs time too, +1.4 s a run at 70,100 atoms and +2.5 s at
#:   140,200 (Vina reads the receptor again for every run), with the same poses; reading 100,243 atoms here
#:   (read_receptor) took 0.41 s in CPython. The most within these bounds: 79 atoms in a 60 Å box at exhaustiveness
#:   64 with 100,243 receptor atoms, 2,059 MiB (1,957 MiB with the 701-atom pocket, at exhaustiveness 8 or 64).
#: So a ligand of at most DOCK_LIGAND_ATOMS_MAX atoms (as the PDBQT lists them, hydrogens included) in a box of at
#: most DOCK_BOX_VOLUME_MAX Å³ with a receptor of at most DOCK_RECEPTOR_ATOMS_MAX atoms keeps one Vina instance near
#: 2 GiB at most at any exhaustiveness (2,059 MiB measured), and the page plans each Vina worker at 2.25 GB
#: (web/js/scientific/pipelines/dock.js GB_PER_VINA). Larger runs are refused, for the runner (native Vina has none
#: of these bounds).
DOCK_LIGANDS_MAX = 100
DOCK_RECEPTOR_ATOMS_MAX = 100_000
DOCK_LIGAND_ATOMS_MAX = 80
DOCK_BOX_VOLUME_MAX = 216_000            # Å³: a 60 Å cube
DOCK_PLAN_SCHEMA = "tcmstudio.dock-plan/1"
_DOCK_SUFFIX = ".pdbqt"


class BadArguments(ValueError):
    """The arguments are not what the function takes."""


class Refused(Exception):
    """A call this runtime does not run: ``need`` is what would (``runner``, ``memory``), or
    None when the inputs themselves cannot be used. ``call`` answers it as an error of type
    ``refused``."""

    def __init__(self, message: str, need: str | None = None) -> None:
        super().__init__(message)
        self.need = need


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


def _runner_check(check: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """One of the runner's own parameter checks (``kinds``), its refusal as bad arguments."""
    from .kinds import BadParams

    try:
        return check(*args, **kwargs)
    except BadParams as exc:
        raise BadArguments(str(exc)) from None


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


def _log(emit: Emit | None) -> Callable[..., None]:
    def log(line: Any, level: str = "info") -> None:
        if emit is not None:
            emit("log", {"level": level, "text": str(line)})
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
    NPASS as the activity source and STRING as the optional one, profile trusted_local.
    The parameters are held to the runner's ``research.run`` bounds (``kinds``: 100 to
    100000 permutations, source keys, an ontology id for the disease, one formula), so a
    call the runner refuses as bad arguments is refused here the same way."""
    from dataclasses import replace

    from bioagent.analysis.network_pharmacology import Parameters
    from bioagent.research.loop import (QuestionRefused, ResearchRefused, default_protocol,
                                        parse_question, research_summary)
    from bioagent.research.loop import run_research as research

    from . import kinds

    _only(args, ("question", "formula", "disease", "activity", "optional", "background",
                 "hits", "permutations", "accept_review", "snapshot_root", "ledger",
                 "state_dir", "out_dir"))
    text = _text(args, "question")
    _runner_check(kinds._text, "question", text)
    permutations = args.get("permutations")
    permutations = 1000 if permutations is None else _runner_check(
        kinds._int, "permutations", permutations, *kinds.PERMUTATIONS)
    activity = tuple(_runner_check(kinds._text, "activity", a, pattern=kinds._SOURCE_KEY)
                     for a in _strings(args, "activity")) or ("npass",)
    optional = tuple(_runner_check(kinds._text, "optional", a, pattern=kinds._SOURCE_KEY)
                     for a in _strings(args, "optional")) or ("string",)
    disease = _text(args, "disease", required=False)
    if disease:
        disease = _runner_check(kinds._text, "disease", disease, pattern=kinds._DISEASE)
    formula = _text(args, "formula", required=False)
    if formula:
        formula = _runner_check(kinds._text, "formula", formula, max_len=100)
        problem = kinds.formula_ambiguity(formula) or kinds.formula_conflict(text, formula)
        if problem:
            raise BadArguments(f"formula: {problem}")
    try:
        params = replace(Parameters(), background=_choice(args, "background", _BACKGROUNDS),
                         hits=_choice(args, "hits", _HITS), permutations=permutations)
    except BadArguments:
        raise
    except ValueError as exc:                # what the analysis itself refuses
        raise BadArguments(str(exc)) from None
    paths = {k: _text(args, k) for k in ("snapshot_root", "ledger", "state_dir", "out_dir")}
    accept_review = _flag(args, "accept_review", False)
    isolate = _isolate()
    log = _log(emit)
    try:
        question = parse_question(text, disease=disease, formula=formula)
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
        log(f"refused: {exc}", "warn")
        return {"exit": 2, "summary": None, "failed": [], "refused": str(exc)}
    summary, failed = research_summary(run, question)
    code = 3 if failed else (0 if run.released else 1)
    log(f"research {run.run_id}: {'released' if run.released else 'not released'}"
        + (f"; {len(failed)} stage(s) failed their own check" if failed else ""),
        "warn" if failed else "info")
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
    from bioagent.validation import FAIL, aggregate

    _only(args, ("out_dir", "snapshot_root", "ledger"))
    out = Path(_text(args, "out_dir"))
    root = _text(args, "snapshot_root", required=False)
    ledger = _text(args, "ledger", required=False)
    if bool(root) != bool(ledger):
        raise BadArguments("give both snapshot_root and ledger, or neither")
    report = verify(out, snapshot_root=root or None, ledger=ledger or None)
    checks = [dict(c) for c in report["checks"]]
    problems = [f"{c['name']}: {c['detail']}" for c in checks if c["status"] == FAIL]
    more, found = _kind_checks("research.run", out)
    return {"status": aggregate(c["status"] for c in [*checks, *more]),
            "checks": [*checks, *more], "problems": [*problems, *found], "report": report}


def _kind_checks(kind_name: str, out: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """The runner's own collection of a job of this kind once its process has ended
    (``jobs.JobService._check_artefacts``, with the kind's ``artefacts`` and ``validators``
    from ``tcmstudio.kinds``): every required file present and not empty, and each validator
    the kind names passing. Returns the checks ``required_artefacts`` and
    ``validator:<name>``, and the problems in the runner's words."""
    from bioagent.validation import FAIL, PASS

    from .kinds import KINDS

    kind = KINDS[kind_name]
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
    checks = [{"name": "required_artefacts", "status": FAIL if missing else PASS,
               "detail": "; ".join(missing) or
               f"the {len(kind.artefacts)} outputs {kind_name} requires are present"}]
    problems = list(missing)
    for name, verdict in verdicts.items():
        checks.append({"name": f"validator:{name}", "status": FAIL if verdict else PASS,
                       "detail": verdict or "passes the runner's check"})
        if verdict:
            problems.append(f"artefact {name!r}: {verdict}")
    return checks, problems


# ------------------------------------------------------------------------- the pipelines

def _given(args: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """The arguments among ``names`` that were given (null counts as not given), for the
    runner's own checks, which fill in the defaults as a job's parameters would."""
    return {k: args[k] for k in names if args.get(k) is not None}


def _contrast(text: str | None) -> tuple[str, str, str] | None:
    """factor,numerator,denominator as the commands read it (``bioagent rnaseq``/``scrna``)."""
    if not text:
        return None
    parts = tuple(p.strip() for p in text.split(","))
    if len(parts) != 3:
        raise BadArguments("contrast: factor,numerator,denominator")
    return parts[0], parts[1], parts[2]


def _finite(value: Any) -> Any:
    """``value`` with every float that is not finite as null: JSON has no NaN, and the
    commands' summaries can hold one (a threshold no gene reached)."""
    import math

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def _stages_progress(emit: Emit | None) -> Callable[[dict[str, Any]], None]:
    """A pipeline's stage record as it is written (``runrecord.watching``), as progress: the
    share of its planned stages that ended, and the stage running."""
    def seen(doc: dict[str, Any]) -> None:
        if emit is None:
            return
        planned = doc.get("planned") or []
        ended = [s.get("name") for s in doc.get("stages") or ()]
        running = doc.get("running")
        emit("progress", {"fraction": round(len(ended) / max(len(planned), 1), 4),
                          "stage": running,
                          "message": (f"{running}" if running else
                                      f"{ended[-1]} done" if ended else "starting")})
    return seen


def _ended(run: Any, log: Callable[..., None], what: str,
           summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """``{exit, summary, failed, reasons, refused, need}`` of a pipeline run that ended: exit 0
    when it is complete, 1 when it is not (``failed`` names the stages at fault and
    ``reasons`` what each stage record says of itself, as the command prints them).
    ``summary`` is the run's own (``run.summary()``) unless given."""
    stages = run.stages
    at_fault = [s for s in stages if s.get("check") == "FAIL"
                or (s.get("required") and s.get("status") != "succeeded")]
    failed = [s["name"] for s in at_fault]
    log(f"{what}: {'complete' if run.complete else 'incomplete'}"
        + (f"; at fault: {', '.join(failed)}" if failed else ""),
        "info" if run.complete else "warn")
    return {"exit": 0 if run.complete else 1,
            "summary": _finite(run.summary() if summary is None else summary),
            "failed": failed, "reasons": {s["name"]: s.get("reason") for s in at_fault
                                          if s.get("reason")},
            "refused": None, "need": None}


def _refused(log: Callable[..., None], why: str, need: str | None = None) -> dict[str, Any]:
    """A pipeline call refused: exit 2, ``refused`` the reason, ``need`` what would run it
    (``runner``, ``memory``) or None when the inputs themselves are what is refused."""
    log(f"refused: {why}", "warn")
    return {"exit": 2, "summary": None, "failed": [], "refused": why, "need": need}


def _first_line(path: Any, *, count: bool = False) -> tuple[str, int]:
    """A table's first line that is not blank and, with ``count``, how many lines that are
    not blank follow it: read a line at a time, gunzipped when it is gzip (by its magic
    bytes, as the pipelines' readers decide). ("", 0) when it cannot be read; the
    pipeline's own reader then says why."""
    import gzip
    import zlib
    from pathlib import Path

    path = Path(path)
    try:
        with path.open("rb") as fh:
            packed = fh.read(2) == b"\x1f\x8b"
        with (gzip.open(path, "rt", encoding="utf-8-sig") if packed
              else path.open(encoding="utf-8-sig")) as fh:
            first = next((line for line in fh if line.strip()), "")
            rows = sum(1 for line in fh if line.strip()) if count else 0
    except (OSError, EOFError, UnicodeDecodeError, ValueError, zlib.error):
        return "", 0
    return first.rstrip("\r\n"), rows


def _cells(line: str, *, tab_on_tie: bool) -> list[str]:
    """A header line's cells, split at the separator it uses more: a tab, else a comma (on a
    tie, a tab as the single-cell reader decides, a comma as the count-matrix reader does),
    each stripped as the readers strip them."""
    import csv

    tabs, commas = line.count("\t"), line.count(",")
    tab = tabs >= commas if tab_on_tie else tabs > commas
    try:
        return [c.strip() for c in next(csv.reader([line], delimiter="\t" if tab else ","), [])]
    except csv.Error:                     # the pipeline's own reader says what is wrong
        return []


def run_rnaseq_counts(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{counts, samples, design?, contrast?, alpha?, de_backend?, out_dir}`` →
    ``{exit, summary, failed, refused, need}``, as ``bioagent rnaseq --counts`` would end:
    ``exit`` 0 (complete), 1 (incomplete: ``failed`` names the stages at fault) or 2
    (refused: ``refused`` says why, ``need`` what would run it, and nothing else is set).
    ``counts`` and ``samples`` are the two tables' paths, ``out_dir`` where the run writes.
    The parameters are held to the runner's ``pipeline.rnaseq`` checks (``kinds``: design,
    contrast, an alpha of 0.0001 to 0.5), and a FASTQ sample sheet given as ``samples`` is
    refused in the runner's words (it runs without counts). ``de_backend`` ``pydeseq2`` is
    refused: this browser's Python has no PyDESeq2. So is a matrix of more than
    ``RNASEQ_COUNTS_MAX`` genes x samples, ``RNASEQ_GENES_MAX`` genes or
    ``RNASEQ_SAMPLES_MAX`` samples, counted from its lines before it is read in full, and a
    run that runs out of memory (``need`` ``memory``)."""
    from bioagent import runrecord
    from bioagent.omics.rnaseq import RNASeqError
    from bioagent.omics.rnaseq_counts import RNASeqCountsConfig, run_rnaseq_counts as run_counts

    from . import kinds

    _only(args, ("counts", "samples", "design", "contrast", "alpha", "de_backend", "out_dir"))
    paths = {k: _text(args, k) for k in ("counts", "samples", "out_dir")}
    choices = _runner_check(kinds._rnaseq_choices,
                            _given(args, ("design", "contrast", "alpha", "de_backend")))
    contrast = _contrast(choices.get("contrast"))
    log = _log(emit)
    if choices["de_backend"] == "pydeseq2":
        return _refused(log, "PyDESeq2 is not in this browser's Python; the built-in DESeq2 "
                             "implementation is", "runner")
    # a FASTQ sample sheet given as the sample table: the runner refuses it with counts too
    sheet = _cells(_first_line(paths["samples"])[0], tab_on_tie=False)
    if "fastq_1" in sheet or "fastq_2" in sheet:
        return _refused(log, kinds.RNASEQ_COUNTS_FASTQ_SHEET)
    # the size of the matrix, from its header and its lines, before anything is read in full
    first, genes = _first_line(paths["counts"], count=True)
    header = _cells(first, tab_on_tie=False)
    samples = max(len(header) - (2 if header[1:2] == ["gene_name"] else 1), 0)
    size = f"{genes:,} genes x {samples:,} samples"
    if (genes > RNASEQ_GENES_MAX or samples > RNASEQ_SAMPLES_MAX
            or genes * samples > RNASEQ_COUNTS_MAX):
        return _refused(log, f"{size} ({genes * samples:,} counts) exceed the "
                             f"{RNASEQ_COUNTS_MAX:,} counts (genes x samples; at most "
                             f"{RNASEQ_GENES_MAX:,} genes and {RNASEQ_SAMPLES_MAX} samples) "
                             f"{_HOLDS}", "memory")
    config = RNASeqCountsConfig(design=choices["design"], contrast=contrast,
                                de_backend=choices["de_backend"], alpha=choices["alpha"])
    log(f"rnaseq counts: {paths['counts']} ({size}) with the sample table "
        f"{paths['samples']}; design {config.design}, test {config.de_backend}")
    try:
        with runrecord.watching(_stages_progress(emit)):
            run = run_counts(paths["counts"], paths["samples"], config, paths["out_dir"])
    except MemoryError:
        return _refused(log, f"the run ran out of memory (MemoryError) on {size}: more than "
                             f"{_HOLDS}", "memory")
    except (RNASeqError, ValueError, RuntimeError, OSError) as exc:
        # what the command prints as "refused: ..."
        return _refused(log, str(exc))
    return _ended(run, log, "rnaseq counts")


#: Single-cell sources this browser's job does not read, by file name, and why.
_SC_RUNNER_SOURCES = (
    (".h5ad", "an .h5ad file is read with anndata, which is not in this browser's Python"),
    (".h5", "a 10x HDF5 file is not yet supported in the browser (its reader, h5py, is not "
            "loaded by this job)"))


def run_scrna(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{source, doublets?, analysis_backend?, integration?, resolution?, seed?, markers?,
    root?, contrast?, n_top_genes?, out_dir}`` → ``{exit, summary, failed, refused, need}``,
    as ``bioagent scrna`` would end (exit 0 complete, 1 incomplete, 2 refused). ``source`` is a
    count table (CSV/TSV/TXT, gzipped or not; genes in rows) or a sample sheet (``sample``,
    ``path``) of them, ``markers`` a marker panel, both paths. The parameters are held to
    the runner's ``pipeline.scrna`` checks (``kinds``: resolution 0.05 to 5, seed 0 to
    2^31 - 1, 50 to 10000 highly variable genes). Refused here, with the reason: the Scanpy
    backend and scVI (not in this browser's Python), and a 10x HDF5, .h5ad or Cell Ranger
    directory source, in the sheet or alone; the runner reads those (a directory without a
    Cell Ranger output's files is refused as the command refuses it). So are more than
    ``SC_CELLS_MAX`` cells, and a table of more than ``SC_TABLE_COUNTS_MAX`` cells x genes,
    counted from the tables a line at a time before any is read in full, and a run that runs
    out of memory (``need`` ``memory``)."""
    import csv
    from pathlib import Path

    from bioagent import runrecord
    from bioagent.omics.optional import BackendUnavailable
    from bioagent.omics.sc.io import ScIOError, _first
    from bioagent.omics.scrna import ScConfig, ScError, _is_sheet, read_sheet
    from bioagent.omics.scrna import run_scrna as run_sc

    from . import kinds

    _only(args, ("source", "doublets", "analysis_backend", "integration", "resolution", "seed",
                 "markers", "root", "contrast", "n_top_genes", "out_dir"))
    source, out_dir = Path(_text(args, "source")), _text(args, "out_dir")
    markers = _text(args, "markers", required=False) or None
    choices = _runner_check(kinds._scrna_choices, _given(args, (
        "doublets", "analysis_backend", "integration", "resolution", "seed", "n_top_genes",
        "root", "contrast")))
    contrast = _contrast(choices.get("contrast"))
    log = _log(emit)
    if choices["analysis_backend"] == "scanpy":
        return _refused(log, "Scanpy, anndata, leidenalg and umap-learn are not in this "
                             "browser's Python; the built-in analysis is", "runner")
    if choices["integration"] == "scvi":
        return _refused(log, "scVI (scvi-tools and torch) is not in this browser's Python; "
                             "built-in Harmony is", "runner")
    try:
        # a sheet or a table, as the pipeline decides it: from the first line alone
        files = ([Path(r["path"]) for r in read_sheet(source)]
                 if source.is_file() and source.suffix.lower() in (".csv", ".tsv")
                 and _is_sheet(source) else [source])
    except (ScError, ValueError, OSError, csv.Error) as exc:
        # what the command prints as "refused: ..." (a sheet or table that is not UTF-8 too)
        return _refused(log, str(exc))
    for f in files:
        if f.is_dir():
            try:
                # the files the reader opens in a Cell Ranger output: a directory without them
                # is refused as the command refuses it, not sent to the runner
                for names in (("matrix.mtx",), ("barcodes.tsv",), ("features.tsv", "genes.tsv")):
                    _first(f, *names)
            except ScIOError as exc:
                return _refused(log, str(exc))
            return _refused(log, f"{f.name} is a directory (a Cell Ranger output): this "
                                 "browser's job reads count tables (CSV/TSV, gzipped or not); "
                                 "bioagent scrna reads a directory on the machine that holds it",
                            "runner")
        for suffix, why in _SC_RUNNER_SOURCES:
            if f.name.lower().endswith(suffix):
                return _refused(log, f"{f.name}: {why}; the runner reads it, and count tables "
                                     "(CSV/TSV) run here", "runner")
    # each table's cells (a column each after the gene names) and genes (its other lines),
    # a line at a time, before any table is read in full
    sizes = []
    for f in files:
        first, genes = _first_line(f, count=True)
        sizes.append((f, max(len(_cells(first, tab_on_tie=True)) - 1, 0), genes))
    cells = sum(n for _, n, _ in sizes)
    if cells > SC_CELLS_MAX:
        return _refused(log, f"{cells:,} cells exceed the {SC_CELLS_MAX:,} {_HOLDS}", "memory")
    for f, n, genes in sizes:
        if n * genes > SC_TABLE_COUNTS_MAX:
            return _refused(log, f"{f.name}: {n:,} cells x {genes:,} genes ({n * genes:,} "
                                 f"counts) exceed the {SC_TABLE_COUNTS_MAX:,} counts of one "
                                 f"table {_HOLDS}", "memory")
    config = ScConfig(doublets=choices["doublets"], analysis_backend=choices["analysis_backend"],
                      integration_method=choices["integration"],
                      resolution=choices["resolution"], seed=choices["seed"],
                      n_top_genes=choices["n_top_genes"], markers=markers,
                      root=choices.get("root"), contrast=contrast)
    log(f"scrna: {len(files)} count table(s) of {cells:,} cells from {source.name}; "
        f"integration {config.integration_method}, {config.n_top_genes} highly variable genes "
        f"at most, seed {config.seed}")
    try:
        with runrecord.watching(_stages_progress(emit)):
            run = run_sc(source, config, out_dir)
    except MemoryError:
        return _refused(log, f"the run ran out of memory (MemoryError) on {cells:,} cells: more "
                             f"than {_HOLDS}", "memory")
    except (ScError, ValueError, BackendUnavailable, OSError, csv.Error) as exc:
        # what the command prints as "refused: ..." (a table the csv module cannot parse too)
        return _refused(log, str(exc))
    return _ended(run, log, "scrna")


def verify_pipeline(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{kind, out_dir}`` (``kind`` ``pipeline.rnaseq``, ``pipeline.scrna``,
    ``pipeline.admet`` or ``pipeline.dock``), or ``{kind: "admet.models", models_dir,
    endpoints?, archive_sha256?}`` → ``{status, checks, problems, report}``.

    The pipeline's own ``verify_run`` first (rnaseq's covers the count-matrix mode; docking's
    is the prepared mode's, ``bioagent.docking.prepared``, the one the browser runs): every
    recorded input re-hashed at its recorded path (so the inputs must still be where the run
    read them) and every output, the stages complete, the tables as the record says; the
    check ``verify_run``. Then the outputs are held to what the runner's job of that kind
    requires (``tcmstudio.kinds``), as the runner's collection checks them: the checks
    ``required_artefacts`` and ``validator:<name>``. ``status`` is the aggregate; ``report``
    is what the run recorded of itself: its pipeline, whether it is complete, its
    ``limitations.md`` lines and the problems ``verify_run`` found. One log line says the
    verdict.

    For ``admet.models`` the check is ``verify_models``, the one the runner's job of that
    kind makes once it has ended: each endpoint's card (every card, when none is named)
    has its model and domain files at the SHA-256 it records, and the cards record one
    archive, ``archive_sha256`` when given; ``report`` is each endpoint's card digest,
    file digests, archive, chemistry, counts and held-out score."""
    import importlib
    import json
    from pathlib import Path

    from bioagent.runrecord import read_limitations
    from bioagent.validation import FAIL, PASS, aggregate

    kind = _text(args, "kind")
    if kind == "admet.models":
        return _verify_models(args, emit)
    _only(args, ("kind", "out_dir"))
    if kind not in _PIPELINES:
        raise BadArguments(f"kind: one of {', '.join([*_PIPELINES, 'admet.models'])}, not {kind!r}")
    out = Path(_text(args, "out_dir"))
    try:
        record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        record = None
        ok, found = False, [f"run.json cannot be read: {exc}"]
    else:
        try:
            ok, found = importlib.import_module(_PIPELINES[kind]).verify_run(out)
        except Exception as exc:                            # noqa: BLE001 - as the runner says it
            ok, found = False, [f"the run could not be verified: {type(exc).__name__}: {exc}"]
    checks = [{"name": "verify_run", "status": PASS if ok else FAIL,
               "detail": "; ".join(found)[:1000] or
               "every recorded input and output re-hashed; the stages complete; the tables "
               "agree with the record"}]
    more, problems = _kind_checks(kind, out)
    record = record if isinstance(record, dict) else {}
    report = {"pipeline": record.get("pipeline"), "complete": record.get("complete"),
              "limitations": read_limitations(out / "limitations.md"),
              "verify_run": {"ok": ok, "problems": list(found)}}
    status = aggregate(c["status"] for c in [*checks, *more])
    _log(emit)(f"verify {kind}: {status}, {len(checks) + len(more)} checks"
               + (f"; {len(found) + len(problems)} problem(s)" if found or problems else ""),
               "info" if status == PASS else "warn")
    return {"status": status, "checks": [*checks, *more], "problems": [*found, *problems],
            "report": report}


# ------------------------------------------------------------------------- ADMET

def _admet_endpoints(args: Mapping[str, Any], *, every: bool) -> list[str]:
    """``endpoints``, held to the runner's check (``kinds.admet_endpoints``: TDC names, no
    repeats); with ``every``, none given is every TDC endpoint."""
    from bioagent.admet.models import ENDPOINTS

    from . import kinds

    names = _runner_check(kinds.admet_endpoints, args.get("endpoints"))
    return names or (list(ENDPOINTS) if every else [])


def _chem_error() -> type[Exception]:
    from bioagent.admet.chem import ChemError
    return ChemError


def admet_split(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{archive, out_dir, endpoints?}`` → ``{archive_sha256, pinned, endpoints: {endpoint:
    {train_val: n, test: n}}, members}``: each endpoint's TDC splits read from the archive
    as ``build_models`` reads them (``admet_group/<endpoint>/<split>.csv``, the SMILES and
    targets in the archive's order) and written to ``out_dir/<endpoint>/<split>.json`` =
    ``{"smiles": [...], "y": [...]}``, for the page's chemistry to prepare (each split's
    SMILES are the set ``admet_train`` reads). ``endpoints`` are TDC names (default: all
    22). ``pinned`` is whether the archive's SHA-256 is TDC's pinned one; when it is not,
    the models are still built, and their cards say so, as natively. An archive that is not
    a zip, or that lacks a member asked for, is refused (``need`` null)."""
    import io
    import zipfile
    from pathlib import Path

    from bioagent.admet import models as M

    _only(args, ("archive", "out_dir", "endpoints"))
    archive, out = Path(_text(args, "archive")), Path(_text(args, "out_dir"))
    endpoints = _admet_endpoints(args, every=True)
    log = _log(emit)
    try:
        raw, sha = M.read_archive(archive)
    except OSError as exc:
        raise Refused(f"the archive cannot be read: {exc}") from None
    except _chem_error() as exc:
        raise Refused(str(exc)) from None
    split: dict[str, dict[str, int]] = {}
    members = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for ep in endpoints:
                split[ep] = {}
                for name in M.SPLITS:
                    smiles, y = M.read_split(z, ep, name)
                    target = out / ep / f"{name}.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps({"smiles": smiles, "y": [float(v) for v in y]},
                                                 ensure_ascii=False, allow_nan=False),
                                      encoding="utf-8")
                    split[ep][name] = len(smiles)
                    members.append(M.split_member(ep, name))
    except _chem_error() as exc:
        raise Refused(str(exc)) from None
    except ValueError as exc:               # a target that is not a finite number
        raise Refused(f"the archive's targets are not all finite numbers ({exc})") from None
    pinned = sha == M.TDC_ADMET_SHA256
    log(f"admet split: {len(endpoints)} endpoints of the archive {sha[:12]}"
        + ("" if pinned else f" (not TDC's pinned archive {M.TDC_ADMET_SHA256[:12]}: the models' "
                             "held-out scores will not be comparable with TDC's leaderboard)"),
        "info" if pinned else "warn")
    return {"archive_sha256": sha, "pinned": pinned, "endpoints": split, "members": members}


def admet_train(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{archive, prepared_dir, models_dir, endpoint, seed?, max_iter?, rebuild?}`` → the
    endpoint's model card, as ``model_cards.json`` holds it. The model is trained as
    ``bioagent admet --build-models`` trains it (``bioagent.admet.models.train_endpoint``:
    the same code, settings and files), from the chemistry the page prepared for the
    endpoint's two splits (``prepared_dir/<endpoint>/{train_val,test}/``, the prepared sets
    of ``admet_split``'s SMILES, found by their ``smiles_sha256``), and written into
    ``models_dir`` (``<cache>/models``) as the build writes it: the model and its domain,
    and the card merged into ``model_cards.json``. The card names the chemistry
    (``rdkit.js``) and counts the training molecules it refused
    (``dropped_unstandardisable``). A finished model that still matches its card, built
    from the same archive, settings, library versions and chemistry, is kept unless
    ``rebuild``. ``seed`` and ``max_iter`` are held to the runner's admet.models bounds.
    Refused (``need`` null): an archive that is not a zip or lacks the endpoint, prepared
    sets that are not for its molecules."""
    from pathlib import Path

    from bioagent.admet import models as M
    from bioagent.admet.engine import PrecomputedEngine

    from . import kinds

    _only(args, ("archive", "prepared_dir", "models_dir", "endpoint", "seed", "max_iter",
                 "rebuild"))
    paths = {k: Path(_text(args, k)) for k in ("archive", "prepared_dir", "models_dir")}
    endpoint = _text(args, "endpoint")
    if endpoint not in M.ENDPOINTS:
        raise BadArguments(f"endpoint: no TDC ADMET endpoint {endpoint!r}; one of "
                           f"{', '.join(M.ENDPOINTS)}")
    choices = _runner_check(kinds._admet_models_choices, _given(args, ("seed", "max_iter",
                                                                       "rebuild")))
    log = _log(emit)
    try:
        engine = PrecomputedEngine(paths["prepared_dir"] / endpoint)
        card = M.train_endpoint(paths["models_dir"], archive=paths["archive"], endpoint=endpoint,
                                seed=choices["seed"], max_iter=choices["max_iter"],
                                rebuild=choices["rebuild"], log=log, engine=engine)
    except _chem_error() as exc:
        raise Refused(str(exc)) from None
    except OSError as exc:
        raise Refused(f"{endpoint}: {exc}") from None
    return card


def _molecules_rows(path: Any) -> int:
    """The molecules a CSV/TSV/.smi file holds at most, counted a line at a time before it is
    read in full: its lines that are not blank, less a header (as ``read_ligands`` reads one:
    a first line with a comma or a tab, in a file that is not .smi)."""
    first, more = _first_line(path, count=True)
    if not first:
        return 0
    header = not str(path).lower().endswith(".smi") and ("," in first or "\t" in first.strip())
    return more + (0 if header else 1)


def admet_read(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{molecules, out_dir}`` → ``{n, file, smiles_sha256}``: the molecules read as
    ``bioagent admet`` reads them (``pipeline.read_molecules``: a CSV/TSV with name and
    smiles columns, a .smi file, or, when ``molecules`` is not a file, one SMILES) and
    written to ``out_dir/molecules.json`` = ``{"names": [...], "smiles": [...]}``, which
    ``run_admet`` reads; ``smiles_sha256`` names their prepared set. Refused: an SDF or .mol
    file (``need`` ``runner``: RDKit's SDMolSupplier is not ported to the browser's
    chemistry), more than ``ADMET_MOLECULES_MAX`` molecules (``need`` ``memory``, counted
    before the file is read in full), and a file the reader refuses (``need`` null)."""
    from pathlib import Path

    from bioagent.admet.engine import smiles_sha256
    from bioagent.admet.pipeline import read_molecules

    from . import kinds

    _only(args, ("molecules", "out_dir"))
    given, out = _text(args, "molecules"), Path(_text(args, "out_dir"))
    path = Path(given)
    try:
        is_file = path.is_file()
    except (OSError, ValueError):                       # a long SMILES is no file name
        is_file = False
    log = _log(emit)
    if is_file:
        if path.name.lower().endswith(_ADMET_RUNNER_SUFFIXES):
            raise Refused(f"{path.name}: an SDF or .mol file is read with RDKit's SDMolSupplier, "
                          "which the browser's chemistry (RDKit.js) does not port; the runner "
                          "reads it, and CSV, TSV and .smi files run here", "runner")
        rows = _molecules_rows(path)
        if rows > ADMET_MOLECULES_MAX:
            raise Refused(f"{rows:,} molecules exceed the {ADMET_MOLECULES_MAX:,} {_HOLDS}",
                          "memory")
    elif path.name.lower().endswith((*_ADMET_RUNNER_SUFFIXES, ".csv", ".tsv", ".smi", ".txt")):
        # a file name, not a SMILES: read as one, it would be a molecule that cannot be read
        raise BadArguments(f"molecules: {path.name} is not a file here (nor one SMILES)")
    else:
        _runner_check(kinds._text, "molecules", given, pattern=kinds._SMILES, max_len=2000)
    try:
        entries = read_molecules(path if is_file else given.strip())
    except _chem_error() as exc:
        raise Refused(str(exc)) from None
    if len(entries) > ADMET_MOLECULES_MAX:
        raise Refused(f"{len(entries):,} molecules exceed the {ADMET_MOLECULES_MAX:,} {_HOLDS}",
                      "memory")
    names, smiles = [n for n, _ in entries], [s for _, s in entries]
    out.mkdir(parents=True, exist_ok=True)
    target = out / _MOLECULES_FILE
    target.write_text(json.dumps({"names": names, "smiles": smiles}, ensure_ascii=False),
                      encoding="utf-8")
    log(f"admet read: {len(entries):,} molecules from "
        + (path.name if is_file else "one SMILES"))
    return {"n": len(entries), "file": str(target), "smiles_sha256": smiles_sha256(smiles)}


def _read_entries(path: Any) -> list[tuple[str, str]]:
    """The (name, SMILES) ``admet_read`` wrote (``molecules.json``)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    names, smiles = (doc.get(k) if isinstance(doc, dict) else None for k in ("names", "smiles"))
    if not (isinstance(names, list) and isinstance(smiles, list) and len(names) == len(smiles)
            and all(isinstance(v, str) for v in (*names, *smiles))):
        raise ValueError("it is not admet_read's names and smiles, one each")
    return list(zip(names, smiles))


def run_admet(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{molecules_file, prepared_dir, similarity_dir?, models_dir, endpoints?, out_dir}`` →
    ``{exit, summary, failed, refused, need}``, as ``bioagent admet`` would end: ``exit`` 0
    (complete), 1 (incomplete: no model, or none for an endpoint asked for; ``failed`` names
    the stages at fault) or 2 (refused). ``molecules_file`` is what ``admet_read`` wrote; it
    is recorded in run.json as the run's input and re-hashed by ``verify_run``. The chemistry
    is the page's, ``prepared_dir`` (a prepared set for those SMILES, read back by
    ``PrecomputedEngine``); ``similarity_dir``, when given, holds the page's domain search,
    used only once it checks out (``PrecomputedSimilarity``), else the CPU searches here.
    ``models_dir`` is the models directory (``<cache>/models``) that ``admet_train`` writes.
    ``endpoints`` are held to the runner's check (``kinds``); an endpoint asked for with no
    model makes the run incomplete, as natively. Refused: more than
    ``ADMET_MOLECULES_MAX`` molecules (``need`` ``memory``), and chemistry that is not for
    these molecules (``need`` null)."""
    from pathlib import Path

    from bioagent import runrecord
    from bioagent.admet.engine import PrecomputedEngine, PrecomputedSimilarity
    from bioagent.admet.pipeline import AdmetConfig
    from bioagent.admet.pipeline import run_admet as run_pipeline
    from bioagent.omics.rnaseq import _sha256

    from . import kinds

    _only(args, ("molecules_file", "prepared_dir", "similarity_dir", "models_dir", "endpoints",
                 "out_dir"))
    paths = {k: Path(_text(args, k)) for k in ("molecules_file", "prepared_dir", "models_dir",
                                               "out_dir")}
    similarity_dir = _text(args, "similarity_dir", required=False)
    endpoints = tuple(_runner_check(kinds._admet_prepare_endpoints, args.get("endpoints")))
    if paths["models_dir"].name != "models":
        raise BadArguments("models_dir: the models directory build_models writes (<cache>/models), "
                           f"not {paths['models_dir'].name!r}")
    log = _log(emit)
    try:
        entries = _read_entries(paths["molecules_file"])
    except (OSError, ValueError) as exc:
        return _refused(log, f"{paths['molecules_file'].name} cannot be read as admet_read's "
                             f"molecules: {exc}")
    if len(entries) > ADMET_MOLECULES_MAX:
        return _refused(log, f"{len(entries):,} molecules exceed the {ADMET_MOLECULES_MAX:,} "
                             f"{_HOLDS}", "memory")
    config = AdmetConfig(cache_dir=str(paths["models_dir"].parent), endpoints=endpoints)
    try:
        engine = PrecomputedEngine(paths["prepared_dir"])
        similarity = PrecomputedSimilarity(similarity_dir) if similarity_dir else None
        log(f"admet: {len(entries):,} molecules, chemistry {engine.detail}; similarity "
            + (f"offered by {similarity.offered}" if similarity is not None and similarity.offered
               else "searched on this CPU"))
        with runrecord.watching(_stages_progress(emit)):
            run = run_pipeline(entries, config, paths["out_dir"], engine=engine,
                               similarity=similarity,
                               inputs={str(paths["molecules_file"]): _sha256(paths["molecules_file"])})
    except MemoryError:
        return _refused(log, f"the run ran out of memory (MemoryError) on {len(entries):,} "
                             f"molecules: more than {_HOLDS}", "memory")
    except (_chem_error(), ValueError, OSError) as exc:
        # what the command prints as "refused: ..."
        return _refused(log, str(exc))
    return _ended(run, log, "admet")


def _verify_models(args: Mapping[str, Any], emit: Emit | None) -> dict[str, Any]:
    """verify_pipeline for ``admet.models``: the models held to their cards
    (``bioagent.admet.models.verify_models``, the runner's own check of the job)."""
    import re
    from pathlib import Path

    from bioagent.admet.models import verify_models
    from bioagent.validation import FAIL, PASS

    _only(args, ("kind", "models_dir", "endpoints", "archive_sha256"))
    models = Path(_text(args, "models_dir"))
    endpoints = _admet_endpoints(args, every=False)
    archive = _text(args, "archive_sha256", required=False) or None
    if archive is not None and not re.fullmatch(r"[0-9a-f]{64}", archive):
        raise BadArguments("archive_sha256: 64 lower-case hexadecimal digits")
    ok, problems, report = verify_models(models, endpoints or None, archive_sha256=archive)
    detail = ("; ".join(problems)[:1000] if problems else
              f"{len(report['endpoints'])} models, each file and its applicability domain at the "
              "SHA-256 its card records, all trained on one archive")
    checks = [{"name": "verify_models", "status": PASS if ok else FAIL, "detail": detail}]
    _log(emit)(f"verify admet.models: {checks[0]['status']}, {len(report['endpoints'])} cards"
               + (f"; {len(problems)} problem(s)" if problems else ""),
               "info" if ok else "warn")
    return {"status": checks[0]["status"], "checks": checks, "problems": problems,
            "report": {"models_dir": str(models), "archive_sha256": archive, **report}}


# ------------------------------------------------------------------------- docking

_DOCK_FILES = ("receptor", "ligands", "reference_ligand")
_DOCK_SETTINGS = ("center", "size", "scoring", "exhaustiveness", "poses", "seed")
_DOCK_PLAN_FILE = "dock_plan.json"


def _dock_paths(args: Mapping[str, Any]) -> dict[str, Any]:
    """The prepared inputs' paths: the receptor and the ligands, and the reference ligand when
    given. A receptor or ligands that are not ``.pdbqt`` need preparing, which is the runner's
    (``need`` ``runner``); a reference ligand that is not is refused as the runner refuses it."""
    from pathlib import Path

    paths = {}
    for key in _DOCK_FILES:
        value = _text(args, key, required=key != "reference_ligand")
        if not value:
            continue
        path = Path(value)
        if not path.name.lower().endswith(_DOCK_SUFFIX):
            if key == "reference_ligand":
                raise BadArguments(f"reference_ligand: {path.name} is not a .pdbqt file: the "
                                   "co-crystal ligand, prepared, in its crystal pose")
            raise Refused(f"{key}: {path.name} is not a prepared .pdbqt: " + (
                "a receptor from PDB is prepared with meeko" if key == "receptor" else
                "ligands from SMILES, CSV or SDF are prepared with RDKit and meeko")
                + ", which this browser does not hold; the runner prepares and docks them",
                "runner")
        if not path.is_file():
            raise BadArguments(f"{key}: {path.name} is not a file here")
        paths[key] = path
    return paths


def _dock_config(args: Mapping[str, Any]) -> Any:
    """The run's settings, held to the runner's ``pipeline.dock`` checks (``kinds``: vina or
    vinardo, exhaustiveness 1 to 64, 1 to 20 poses, a seed from 1 for prepared inputs) and to
    ``PreparedDockConfig``'s (three finite numbers each for the box's centre and size)."""
    from bioagent.docking.prep import DockingError
    from bioagent.docking.prepared import PreparedDockConfig

    from . import kinds

    p = _given(args, _DOCK_SETTINGS)
    try:
        return PreparedDockConfig(
            center=p.get("center"), size=p.get("size"),
            scoring=_runner_check(kinds._enum, "scoring", p.get("scoring"), ("vina", "vinardo"),
                                  "vina"),
            exhaustiveness=_runner_check(kinds._int, "exhaustiveness", p.get("exhaustiveness", 8),
                                         1, 64),
            n_poses=_runner_check(kinds._int, "poses", p.get("poses", 9), 1, 20),
            seed=_runner_check(kinds._int, "seed", p.get("seed", 42), 1, 2**31 - 1))
    except DockingError as exc:
        raise BadArguments(str(exc)) from None


def _pdbqt_text(path: Any, what: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise Refused(f"{what}: {path.name} is not a text file (UTF-8)") from None
    except OSError as exc:
        raise Refused(f"{what}: {path.name} cannot be read ({exc})") from None


def _count_lines(path: Any, starts: tuple[str, ...]) -> int:
    """The lines of a file that start with one of ``starts``, read a line at a time."""
    with path.open("rb") as fh:
        return sum(1 for line in fh if line.startswith(tuple(s.encode() for s in starts)))


def _atoms(text: str) -> int:
    return sum(1 for line in text.split("\n") if line.startswith(("ATOM  ", "HETATM")))


def _dock_plan(paths: Mapping[str, Any], config: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """The plan of the Vina runs (``DOCK_PLAN_SCHEMA``) and the text each file of it holds,
    from the inputs read as ``bioagent.docking.prepared`` reads them; the limits are checked
    before anything is read in full."""
    from bioagent.docking.prep import DockingError
    from bioagent.docking.prepared import (MAX_EVALS, MIN_RMSD, REFERENCE_KEY, SPACING,
                                           read_ligands, read_receptor, site_box)

    try:
        atoms = _count_lines(paths["receptor"], ("ATOM", "HETATM"))
        models = _count_lines(paths["ligands"], ("MODEL",))
    except OSError as exc:
        raise Refused(f"the inputs cannot be read ({exc})") from None
    if atoms > DOCK_RECEPTOR_ATOMS_MAX:
        raise Refused(f"the receptor's {atoms:,} atoms exceed the {DOCK_RECEPTOR_ATOMS_MAX:,} "
                      f"{_HOLDS}", "memory")
    if models > DOCK_LIGANDS_MAX:
        raise Refused(f"{models:,} ligands exceed the {DOCK_LIGANDS_MAX:,} of one run that "
                      f"{_HOLDS}", "memory")
    try:
        receptor = read_receptor(_pdbqt_text(paths["receptor"], "receptor"))
        ligands = read_ligands(_pdbqt_text(paths["ligands"], "ligands"), paths["ligands"].stem)
        reference = None
        if "reference_ligand" in paths:
            ref = paths["reference_ligand"]
            found = read_ligands(_pdbqt_text(ref, "reference_ligand"), ref.stem)
            if len(found) != 1:
                raise Refused(f"reference_ligand: {ref.name} holds {len(found)} ligands; a "
                              "reference is one")
            reference = found[0]
    except DockingError as exc:
        # an input that AutoDock Vina 1.2.7 would not read, wherever it runs
        raise Refused(str(exc)) from None
    if len(ligands) > DOCK_LIGANDS_MAX:
        raise Refused(f"{len(ligands):,} ligands exceed the {DOCK_LIGANDS_MAX:,} of one run "
                      f"that {_HOLDS}", "memory")
    large = [f"{lig.name} ({_atoms(lig.text)})" for lig in [*ligands, *([reference] if reference
                                                                         else [])]
             if _atoms(lig.text) > DOCK_LIGAND_ATOMS_MAX]
    if large:
        raise Refused(f"ligands of more than {DOCK_LIGAND_ATOMS_MAX} atoms (Vina's tables of "
                      f"their atom pairs grow with the square): {', '.join(large[:10])}"
                      + (f" and {len(large) - 10} more" if len(large) > 10 else "")
                      + f"; more than {_HOLDS}", "memory")
    try:
        box = site_box(config, reference)
    except DockingError as exc:
        raise BadArguments(str(exc)) from None
    volume = box.size[0] * box.size[1] * box.size[2]
    if volume > DOCK_BOX_VOLUME_MAX:
        raise Refused(f"a box of {' x '.join(f'{v:g}' for v in box.size)} Å ({volume:,.0f} "
                      f"Å³) exceeds the {DOCK_BOX_VOLUME_MAX:,.0f} Å³ (Vina's grid maps grow with "
                      f"its volume) {_HOLDS}", "memory")
    files = {"receptor.pdbqt": receptor.text,
             **{f"{lig.key}.pdbqt": lig.text for lig in ligands},
             **({f"{REFERENCE_KEY}.pdbqt": reference.text} if reference else {})}
    plan: dict[str, Any] = {
        "schema": DOCK_PLAN_SCHEMA,
        "receptor": {"file": "plan/receptor.pdbqt", "sha256": receptor.sha256,
                     "atoms": receptor.atoms, "heavy_atoms": receptor.heavy_atoms,
                     "chains": list(receptor.chains), "residues": receptor.residues},
        "ligands": [{"index": lig.index, "key": lig.key, "name": lig.name,
                     "file": f"plan/{lig.key}.pdbqt", "sha256": lig.sha256,
                     "heavy_atoms": lig.heavy_atoms, "torsions": lig.torsions}
                    for lig in ligands]}
    if reference is not None:
        plan["reference"] = {"key": REFERENCE_KEY, "name": reference.name,
                             "file": f"plan/{REFERENCE_KEY}.pdbqt", "sha256": reference.sha256,
                             "heavy_atoms": reference.heavy_atoms,
                             "torsions": reference.torsions}
    plan["box"] = {"center": [float(v) for v in box.center], "size": [float(v) for v in box.size],
                   "source": box.source}
    plan["params"] = {"scoring": config.scoring, "exhaustiveness": config.exhaustiveness,
                      "n_poses": config.n_poses, "seed": config.seed,
                      "energy_range": config.energy_range, "spacing": SPACING,
                      "min_rmsd": MIN_RMSD, "max_evals": MAX_EVALS}
    return plan, files


def dock_read(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{receptor, ligands, reference_ligand?, center?, size?, scoring?, exhaustiveness?,
    poses?, seed?, out_dir}`` → the plan of the Vina runs (``tcmstudio.dock-plan/1``): the
    prepared inputs read and checked as ``bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt`` reads
    them (``bioagent.docking.prepared``: the records Vina 1.2.7 reads, the ligands' names, the
    box given or built around the reference ligand) and written to ``out_dir/plan/``:
    ``receptor.pdbqt``, ``ligand-<i>.pdbqt`` and ``reference.pdbqt``, the exact texts Vina is
    given (line ends as \\n), and ``dock_plan.json``: ``receptor`` {file, sha256, atoms,
    heavy_atoms, chains, residues}, ``ligands`` [{index, key, name, file, sha256,
    heavy_atoms, torsions}], ``reference`` {key, name, file, sha256, heavy_atoms, torsions}
    (with a reference ligand), ``box`` {center, size, source} and ``params`` {scoring,
    exhaustiveness, n_poses, seed, energy_range, spacing, min_rmsd, max_evals}; ``file`` is
    relative to ``out_dir``. One Vina run per ligand (``key`` ``ligand-<i>``) and for the
    reference (``reference``), each with the box and the params, makes the results
    ``run_dock`` reads. The settings are held to the runner's ``pipeline.dock`` checks.
    Refused: a receptor or ligands that are not ``.pdbqt`` (``need`` ``runner``: preparing
    them is the runner's), more than ``DOCK_LIGANDS_MAX`` ligands, a receptor of more than
    ``DOCK_RECEPTOR_ATOMS_MAX`` atoms, a ligand of more than ``DOCK_LIGAND_ATOMS_MAX`` or a
    box of more than ``DOCK_BOX_VOLUME_MAX`` Å³ (``need`` ``memory``), and inputs Vina would
    not read (``need`` null)."""
    from pathlib import Path

    _only(args, (*_DOCK_FILES, *_DOCK_SETTINGS, "out_dir"))
    out = Path(_text(args, "out_dir"))
    paths = _dock_paths(args)
    config = _dock_config(args)
    plan, files = _dock_plan(paths, config)
    target = out / "plan"
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (target / name).write_bytes(text.encode("utf-8"))
    (target / _DOCK_PLAN_FILE).write_bytes(
        (json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    _log(emit)(f"dock read: receptor {plan['receptor']['atoms']:,} atoms, "
               f"{len(plan['ligands'])} ligands"
               + (f", reference {plan['reference']['name']}" if "reference" in plan else "")
               + f"; box {plan['box']['source']}; {len(files) - 1} Vina runs to compute")
    return plan


def _dock_summary(run: Any) -> dict[str, Any]:
    """What a prepared docking run says of itself, as ``_ended`` reports it."""
    v = run.validation
    order = sorted(run.results, key=lambda r: (r.best_raw, r.ligand.index))
    return {"ligands": len(run.ligands), "docked": len(run.results),
            "failures": dict(run.failures), "validated": run.validated,
            "validation": None if not v else {k: v.get(k) for k in (
                "ligand", "top_pose_rmsd", "rmsd", "threshold", "passed", "error") if k in v},
            "box": {"center": list(run.box.center), "size": list(run.box.size),
                    "source": run.box.source},
            "engine": dict(run.engine),
            "best": [{"ligand": r.ligand.name, "best_score": r.best_score} for r in order],
            "warnings": list(run.warnings), "out_dir": str(run.out_dir),
            "complete": run.complete}


def run_dock(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{receptor, ligands, reference_ligand?, plan_dir, results_dir, out_dir, center?, size?,
    scoring?, exhaustiveness?, poses?, seed?}`` → ``{exit, summary, failed, reasons, refused,
    need}``, as ``bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt`` would end: ``exit`` 0
    (complete), 1 (incomplete: no ligand docked, the redocking of the reference ligand off by
    more than 2 Å, or a best pose that failed its checks; ``failed`` names the stages at
    fault) or 2 (refused). The prepared docking (``bioagent.docking.prepared``) runs on the
    Vina results the page computed (``results_dir/<key>.json``, read by ``PrecomputedVina``,
    which refuses a result for another receptor, ligand, box or setting, or whose energies
    are not its poses'; a WebAssembly build's results only from the pinned build,
    ``catalog.VINA_WASM``) with the same
    arguments ``dock_read`` was given: the plan it wrote (``plan_dir/dock_plan.json``) must
    still be what these inputs and settings make, or the run is refused. The inputs are
    recorded in run.json at their paths and re-hashed by ``verify_run``. Refused as
    ``dock_read`` refuses, and a run that runs out of memory (``need`` ``memory``)."""
    from pathlib import Path

    from bioagent import runrecord
    from bioagent.docking.prep import DockingError
    from bioagent.docking.prepared import PrecomputedVina, run_prepared_docking

    _only(args, (*_DOCK_FILES, *_DOCK_SETTINGS, "plan_dir", "results_dir", "out_dir"))
    dirs = {k: Path(_text(args, k)) for k in ("plan_dir", "results_dir", "out_dir")}
    log = _log(emit)
    try:
        paths = _dock_paths(args)
        config = _dock_config(args)
        plan, _ = _dock_plan(paths, config)
    except Refused as exc:
        return _refused(log, str(exc), exc.need)
    try:
        written = json.loads((dirs["plan_dir"] / _DOCK_PLAN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _refused(log, f"{_DOCK_PLAN_FILE} cannot be read from {dirs['plan_dir']}: {exc}")
    written = written if isinstance(written, dict) else {}
    differ = [k for k in dict.fromkeys([*plan, *written])
              if json.loads(json.dumps(plan.get(k))) != written.get(k)]
    if differ:
        return _refused(log, f"the inputs or settings are not the ones the Vina runs were planned "
                             f"for ({_DOCK_PLAN_FILE}: {', '.join(differ)} differ)")
    log(f"dock: {len(plan['ligands'])} ligands"
        + (f" and the reference {plan['reference']['name']}" if "reference" in plan else "")
        + f" on the Vina results in {dirs['results_dir'].name}")
    try:
        engine = PrecomputedVina(dirs["results_dir"])
        # run.json names the engine as the results do: a WebAssembly build must be the pinned one
        from .catalog import VINA_WASM
        if engine.build == "wasm" and engine.wasm_sha256 != VINA_WASM["sha256"]:
            return _refused(log, f"the Vina results come from a WebAssembly build with SHA-256 "
                                 f"{str(engine.wasm_sha256)[:12]}…, not the pinned AutoDock Vina "
                                 f"{VINA_WASM['version']} ({VINA_WASM['sha256'][:12]}…)")
        with runrecord.watching(_stages_progress(emit)):
            run = run_prepared_docking(paths["receptor"], paths["ligands"], config,
                                       dirs["out_dir"], engine=engine,
                                       reference_path=paths.get("reference_ligand"))
    except MemoryError:
        return _refused(log, f"the run ran out of memory (MemoryError): more than {_HOLDS}",
                        "memory")
    except (DockingError, ValueError, OSError) as exc:
        # what the command prints as "refused: ..." (a result that is not this run's too)
        return _refused(log, str(exc))
    return _ended(run, log, "dock", summary=_dock_summary(run))


def self_test(args: Mapping[str, Any], emit: Emit | None = None) -> dict[str, Any]:
    """``{work_dir?}`` → the known-answer report (``tcmstudio.browser-selftest/1``): ``status``
    PASS only when the planted world studied here gives the committed tables (identical, or
    every number within 1e-12) and its exports recompute."""
    from .browser_selftest import run

    _only(args, ("work_dir",))
    return run(args, emit)


FUNCTIONS: Mapping[str, Callable[..., dict[str, Any]]] = {
    "plan_snapshots": plan_snapshots, "build_snapshots": build_snapshots,
    "run_research": run_research, "verify_research": verify_research,
    "run_rnaseq_counts": run_rnaseq_counts, "run_scrna": run_scrna,
    "admet_split": admet_split, "admet_train": admet_train, "admet_read": admet_read,
    "run_admet": run_admet, "dock_read": dock_read, "run_dock": run_dock,
    "verify_pipeline": verify_pipeline, "self_test": self_test}


# ------------------------------------------------------------------------- the entry

def _error(kind: str, message: str, **more: Any) -> str:
    return json.dumps({"ok": False, "error": {"type": kind, "message": message[:_MESSAGE_MAX],
                                              **more}}, ensure_ascii=False)


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
    except SystemExit as exc:
        # nothing here should exit the interpreter the worker keeps for its next call
        return _error("runtime_error", f"{fn} tried to exit (code {exc.code!r})")
    except BadArguments as exc:
        return _error("bad_arguments", str(exc))
    except Refused as exc:
        return _error("refused", str(exc), **({"need": exc.need} if exc.need else {}))
    except ModuleNotFoundError as exc:
        return _error("refused", f"{fn} cannot run here: {exc} (a package this runtime does "
                                 "not hold)")
    except MemoryError:
        return _error("refused", f"{fn} ran out of memory (MemoryError): more than this "
                                 "runtime holds (a browser worker, wasm32, at most 4 GB): run it "
                                 "on the runner", need="memory")
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
