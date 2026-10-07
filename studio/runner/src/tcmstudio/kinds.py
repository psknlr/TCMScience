"""Job kinds: typed parameters → one fixed command line, its outputs, and how they are checked.

Every kind of CONTRACTS §4 is here. A kind takes the parameters the catalog declares
(``tcmstudio.catalog.JOB_KINDS``), checks them beyond the schema, resolves file parameters
to files the runner holds (upload ids, or paths under the runner's home), and builds a
command line from a fixed template — ``sys.executable -I -m bioagent.cli …`` (or this
module's own worker for ``skill.run``). Nothing from the browser becomes a command: values
are passed as ``--name=value`` arguments or as checked positionals, and ``{output}`` is
reserved for the job's output directory.

A job is ``succeeded`` only after its required outputs are present, non-empty, and pass the
pipeline's own ``verify_run`` (digests recorded in ``run.json`` checked again); the TCM data
hub kinds are checked against the hub's status instead.

``python -m tcmstudio.kinds skill-run --request FILE --out DIR`` is the worker behind
``skill.run``: it runs one governed skill through the dispatcher, in this process, on the
project's durable audit chain, and writes the envelope and the skill's outputs into the
job's output directory.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = ["BadParams", "Kind", "PrepareContext", "StartContext", "KINDS", "default_kinds",
           "OUTPUT_TOKEN", "safe_id", "stdout_json", "main"]

OUTPUT_TOKEN = "{output}"
_UPLOAD_ID = re.compile(r"^u_[0-9a-f]{16}$")
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")
_NAME = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
_SEQUENCE = re.compile(r"^[A-Za-z*]{10,10000}$")
_SMILES = re.compile(r"^[A-Za-z0-9@+\-\[\]()=#$%/\\.:*~]{1,2000}$")
_REMOTE_STRUCTURE = re.compile(r"^(PDB:[0-9A-Za-z]{4}(:[A-Za-z0-9]{1,26})?|UniProt:[A-Za-z0-9]{6,10})$",
                               re.IGNORECASE)
_DISEASE = re.compile(r"^[A-Za-z]{2,12}[_:][A-Za-z0-9]{1,20}$")
_SOURCE_KEY = re.compile(r"^[a-z0-9_]{1,40}$")


class BadParams(ValueError):
    """Parameters a kind cannot run with; the message names the parameter and the fix."""


def safe_id(project_id: str | None) -> str:
    """The directory name of a project, as the dispatcher derives it."""
    raw = str(project_id or "default")
    cleaned = _SAFE.sub("_", raw).strip("._")[:80]
    if not cleaned or cleaned != raw:
        cleaned = (cleaned[:60] + "-" if cleaned else "p-") + hashlib.sha256(
            raw.encode("utf-8")).hexdigest()[:12]
    return cleaned


# ------------------------------------------------------------------------------ contexts

Finder = Callable[[str], "tuple[Path, dict[str, Any]] | None"]


@dataclass
class PrepareContext:
    """What a kind needs at submission: where the job lives, and how to find files."""

    job_id: str
    job_dir: Path
    project_id: str | None
    paths: Mapping[str, Path]                 # settings.home_paths(home)
    data_env: Mapping[str, str]               # BIOAGENT_DATA_LAKE / _TCMDB / _WORKSPACE
    find: Finder                              # upload id, upload name or allowed path → file
    settings: Mapping[str, Any]
    inputs: dict[str, Any] = field(default_factory=dict)

    def file(self, name: str, value: Any) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise BadParams(f"{name}: give an upload id (POST /api/uploads) or a file the "
                            "runner holds")
        found = self.find(value.strip())
        if found is None:
            raise BadParams(f"{name}: {value!r} is neither an upload id, the name of an uploaded "
                            "file, nor a file under the runner's home; upload it first "
                            "(POST /api/uploads) and pass its id")
        path, info = found
        self.inputs[name] = info
        return path

    @property
    def staging(self) -> Path:
        d = self.job_dir / "inputs"
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass
class StartContext:
    """What a kind needs when the job starts: the device and threads in force then."""

    job_id: str
    job_dir: Path
    project_id: str | None
    paths: Mapping[str, Path]
    data_env: Mapping[str, str]
    settings: Mapping[str, Any]
    device: str
    threads: int
    python: str = sys.executable


@dataclass
class Kind:
    """One job kind. ``prepare`` runs at submission (checks, file resolution, staging) and
    returns what ``command`` needs when the job starts."""

    kind: str
    title: tuple[str, str]
    duration: str
    prepare: Callable[[dict[str, Any], PrepareContext], dict[str, Any]]
    command: Callable[[dict[str, Any], StartContext], list[str]]
    artefacts: tuple[tuple[str, str, bool], ...] = ()        # (name, path under out/, required)
    validators: Mapping[str, Callable[[Path], str | None]] = field(default_factory=dict)
    gpu: bool | Callable[[dict[str, Any]], bool] = False
    #: reaches the internet (always, or given the prepared parameters)
    network: bool | Callable[[dict[str, Any]], bool] = False
    #: sends the user's data to a third-party service (the runner's allow_remote setting)
    remote: Callable[[dict[str, Any]], bool] | None = None
    ok_exit_codes: frozenset[int] = frozenset({0})
    timeout_s: float = 12 * 3600.0
    parameters: dict[str, Any] | None = None                 # default: the catalog's
    heavy: tuple[str, ...] | None = None                      # default: the catalog's
    requirements: Callable[[Mapping[str, str]], list[str]] | None = None
    result: Callable[[Path, dict[str, Any]], Any] | None = None
    verify: Callable[[dict[str, Any], Mapping[str, str]], list[str]] | None = None
    progress: Callable[[Path, dict[str, Any]], dict[str, Any] | None] | None = None

    def __post_init__(self) -> None:
        if self.progress is None:
            self.progress = _last_line_progress

    def uses_gpu(self, prepared: dict[str, Any]) -> bool:
        return self.gpu(prepared) if callable(self.gpu) else bool(self.gpu)

    def needs_network(self, prepared: dict[str, Any]) -> bool:
        return bool(self.network(prepared) if callable(self.network) else self.network)

    def sends_remote(self, prepared: dict[str, Any]) -> bool:
        return bool(self.remote(prepared)) if self.remote is not None else False

    def schema(self) -> dict[str, Any]:
        if self.parameters is not None:
            return copy.deepcopy(self.parameters)
        from .catalog import JOB_KINDS, get_entry
        entry = get_entry(f"job.{self.kind}")
        if entry is not None:
            return copy.deepcopy(entry["parameters"])
        return copy.deepcopy(JOB_KINDS[self.kind]["parameters"])

    def heavy_deps(self) -> tuple[str, ...]:
        if self.heavy is not None:
            return self.heavy
        from .catalog import JOB_KINDS
        return tuple(JOB_KINDS.get(self.kind, {}).get("heavy", ()))

    def missing(self, data_env: Mapping[str, str]) -> list[str]:
        from .catalog import dependency_present
        out = [d for d in self.heavy_deps() if not dependency_present(d)]
        if self.requirements is not None:
            out += self.requirements(data_env)
        return out


# ------------------------------------------------------------------------- shared checks

def _text(name: str, value: Any, *, pattern: re.Pattern[str] | None = None,
          max_len: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BadParams(f"{name}: a non-empty text is needed")
    value = value.strip()
    if len(value) > max_len:
        raise BadParams(f"{name}: at most {max_len} characters")
    if value.startswith("-"):
        raise BadParams(f"{name}: a value cannot start with '-'")
    if any(ord(c) < 32 for c in value):
        raise BadParams(f"{name}: control characters are not allowed")
    if pattern is not None and not pattern.match(value):
        raise BadParams(f"{name}: {value!r} is not in the expected form")
    return value


def _no_output_token(value: Any, where: str = "params") -> None:
    if isinstance(value, str):
        if OUTPUT_TOKEN in value:
            raise BadParams(f"{where}: '{OUTPUT_TOKEN}' is reserved for the job's output directory")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _no_output_token(v, f"{where}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _no_output_token(v, f"{where}[{i}]")


def _opt(name: str, value: Any) -> str:
    # --name=value binds the value even when it looks like an option
    return f"--{name}={value}"


def _cli(ctx: StartContext, *args: str) -> list[str]:
    return [ctx.python, "-I", "-m", "bioagent.cli", *args]


def _sniff_dialect(text: str) -> str:
    first = text.splitlines()[0] if text.strip() else ""
    return "excel-tab" if first.count("\t") > first.count(",") else "excel"


def _stage_sheet(ctx: PrepareContext, name: str, value: Any, columns: Sequence[str]) -> tuple[Path, int]:
    """A sample sheet with every file column resolved to an absolute path, written under the
    job's inputs. A file column may name an upload id, the name of an uploaded file, or a
    path relative to the sheet's own directory (when the sheet is a file the runner holds).
    Returns the staged sheet and the number of distinct samples."""
    src = ctx.file(name, value)
    try:
        text = src.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise BadParams(f"{name}: the sheet could not be read as UTF-8 text ({exc})") from None
    dialect = _sniff_dialect(text)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = [h.strip() for h in (reader.fieldnames or [])]
    if "sample" not in header:
        raise BadParams(f"{name}: the sheet has no 'sample' column")
    rows = []
    resolved: dict[str, Any] = {}
    for line, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        for col in columns:
            ref = row.get(col, "")
            if not ref:
                continue
            found = ctx.find(ref)
            if found is None and not os.path.isabs(ref):
                local = (src.parent / ref).resolve()
                if local.is_file():
                    found = ctx.find(str(local))
            if found is None:
                raise BadParams(f"{name}: line {line}, {col} {ref!r} is neither an upload id nor "
                                "the name of an uploaded file; upload it and name it in the sheet")
            row[col] = str(found[0])
            resolved[f"{col}:{line}"] = found[1]
        rows.append(row)
    if not rows:
        raise BadParams(f"{name}: the sheet lists no samples")
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header, dialect=dialect, lineterminator="\n")
    writer.writeheader()
    writer.writerows({k: r.get(k, "") for k in header} for r in rows)
    staged = ctx.staging / (src.name if src.suffix.lower() in (".csv", ".tsv", ".txt")
                            else f"{name}.csv")
    staged.write_text(out.getvalue(), encoding="utf-8")
    ctx.inputs[f"{name}.files"] = resolved
    return staged, len({r.get("sample") for r in rows})


def _is_sheet(path: Path, column: str) -> bool:
    if path.suffix.lower() not in (".csv", ".tsv", ".txt") or not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8-sig", errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return False
    cells = [c.strip() for c in re.split(r"[\t,]", first)]
    return "sample" in cells and column in cells


def stdout_json(job_dir: Path) -> Any:
    """The JSON document a ``--json`` command printed, or None."""
    path = job_dir / "stdout.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("{", "[")):
            try:
                return json.loads("\n".join(lines[i:]))
            except ValueError:
                continue
    return None


def _table_head(path: Path, n: int = 20) -> list[dict[str, str]] | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh, dialect="excel-tab")
            return [dict(row) for row in itertools.islice(reader, n)]
    except (OSError, csv.Error):
        return None


def _verifier(module: str) -> Callable[[Path], str | None]:
    """A validator for ``run.json`` that re-checks the run with the pipeline's own
    ``verify_run`` (the output digests ``run.json`` records)."""
    def check(path: Path) -> str | None:
        import importlib
        try:
            verify_run = importlib.import_module(module).verify_run
            ok, problems = verify_run(path.parent)
        except Exception as exc:                                # noqa: BLE001
            return f"the run could not be verified: {type(exc).__name__}: {exc}"
        return None if ok else "; ".join(str(p) for p in problems)[:1000] or "verify_run refused"
    return check


def _json_file(path: Path) -> str | None:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"not readable JSON: {exc}"
    return None


def _artifact_outputs(path: Path) -> str | None:
    """artifact.json of a research run: readable, and every output it lists has the digest
    it records."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"not readable JSON: {exc}"
    problems = []
    for out in doc.get("outputs") or ():
        if not isinstance(out, Mapping) or not out.get("path") or not out.get("sha256"):
            continue
        target = (path.parent / str(out["path"])).resolve()
        if path.parent.resolve() not in target.parents:
            problems.append(f"{out['path']}: outside the output directory")
            continue
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            problems.append(f"{out['path']}: missing")
            continue
        if digest != str(out["sha256"]).removeprefix("sha256:"):
            problems.append(f"{out['path']}: digest differs from the artifact's record")
    return "; ".join(problems) or None


def _last_line_progress(job_dir: Path, prepared: dict[str, Any]) -> dict[str, Any] | None:
    del prepared
    for name in ("stderr.log", "stdout.log"):
        line = _tail_line(job_dir / name)
        if line:
            m = re.search(r"(\d{1,3}(?:\.\d+)?)\s?%", line)
            out: dict[str, Any] = {"message": line[:200]}
            if m and float(m.group(1)) <= 100:
                out["fraction"] = round(float(m.group(1)) / 100, 3)
            return out
    return None


def _tail_line(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(re.split(r"[\r\n]+", data)):
        if line.strip():
            return line.strip()
    return ""


def _int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise BadParams(f"{name}: a whole number is needed")
    if not low <= int(value) <= high:
        raise BadParams(f"{name}: between {low} and {high}")
    return int(value)


def _num(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadParams(f"{name}: a number is needed")
    return float(value)


def _enum(name: str, value: Any, allowed: Sequence[str], default: str) -> str:
    value = default if value in (None, "") else value
    if value not in allowed:
        raise BadParams(f"{name}: one of {', '.join(allowed)}")
    return str(value)


# ------------------------------------------------------------------------- pipeline.rnaseq

def _rnaseq_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    sheet, samples = _stage_sheet(ctx, "samples", p.get("samples"), ("fastq_1", "fastq_2"))
    files = {k: str(ctx.file(k, p[k])) for k in ("transcripts", "annotation", "genome") if p.get(k)}
    engine = _enum("engine", p.get("engine"), ("auto", "builtin", "salmon", "kallisto", "hisat2"), "auto")
    if engine == "hisat2" and "genome" not in files:
        raise BadParams("engine: hisat2 needs a genome FASTA (genome)")
    if engine in ("builtin", "salmon", "kallisto") and "transcripts" not in files:
        raise BadParams(f"engine: {engine} needs a transcriptome FASTA (transcripts)")
    if engine == "auto" and "transcripts" not in files and "genome" not in files:
        raise BadParams("give transcripts (a transcriptome FASTA), or a genome with engine hisat2")
    out = {"sheet": str(sheet), "samples": samples, "files": files, "engine": engine,
           "design": _text("design", p.get("design") or "~ condition", max_len=200),
           "trimmer": _enum("trimmer", p.get("trimmer"), ("auto", "builtin", "fastp", "none"), "auto"),
           "de_backend": _enum("de_backend", p.get("de_backend"), ("builtin", "pydeseq2"), "builtin"),
           "alpha": _num("alpha", p.get("alpha", 0.05))}
    if p.get("contrast"):
        out["contrast"] = _text("contrast", p["contrast"],
                                pattern=re.compile(r"^[^,\s]+,[^,]+,[^,]+$"), max_len=200)
    return out


def _rnaseq_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "rnaseq", _opt("samples", r["sheet"]))
    for key in ("transcripts", "annotation", "genome"):
        if key in r["files"]:
            argv.append(_opt(key, r["files"][key]))
    argv += [_opt("design", r["design"]), _opt("engine", r["engine"]),
             _opt("trimmer", r["trimmer"]), _opt("de-backend", r["de_backend"]),
             _opt("alpha", r["alpha"]), _opt("threads", ctx.threads)]
    if r.get("contrast"):
        argv.append(_opt("contrast", r["contrast"]))
    return argv + [_opt("out", OUTPUT_TOKEN), "--json"]


def _rnaseq_progress(job_dir: Path, r: dict[str, Any]) -> dict[str, Any] | None:
    out = job_dir / "out"
    if (out / "report.html").is_file():
        return {"fraction": 0.95, "message": "writing the report"}
    n = int(r.get("samples") or 0)
    done = len(list((out / "quant").glob("*/abundance.tsv"))) if (out / "quant").is_dir() else 0
    if n and done:
        if done >= n:
            return {"fraction": 0.85, "message": "differential expression"}
        return {"fraction": round(0.1 + 0.7 * done / n, 3),
                "message": f"quantified {done} of {n} samples"}
    return _last_line_progress(job_dir, r)


# -------------------------------------------------------------------------- pipeline.scrna

def _scrna_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    src = ctx.file("source", p.get("source"))
    samples = None
    if _is_sheet(src, "path"):
        staged, samples = _stage_sheet(ctx, "source", p.get("source"), ("path",))
        src = staged
    out: dict[str, Any] = {
        "source": str(src), "samples": samples,
        "doublets": _enum("doublets", p.get("doublets"), ("remove", "flag", "off"), "remove"),
        "analysis_backend": _enum("analysis_backend", p.get("analysis_backend"),
                                  ("builtin", "scanpy"), "builtin"),
        "integration": _enum("integration", p.get("integration"), ("none", "harmony", "scvi"), "harmony"),
        "resolution": _num("resolution", p.get("resolution", 1.0)),
        "seed": _int("seed", p.get("seed", 0), 0, 2**31 - 1)}
    if p.get("markers"):
        out["markers"] = str(ctx.file("markers", p["markers"]))
    for key in ("root", "contrast"):
        if p.get(key):
            out[key] = _text(key, p[key], max_len=200)
    return out


def _scrna_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "scrna", r["source"], _opt("out", OUTPUT_TOKEN),
                _opt("doublets", r["doublets"]), _opt("analysis-backend", r["analysis_backend"]),
                _opt("integration", r["integration"]), _opt("resolution", r["resolution"]),
                _opt("seed", r["seed"]), _opt("scvi-threads", ctx.threads))
    for key in ("markers", "root", "contrast"):
        if r.get(key):
            argv.append(_opt(key, r[key]))
    return argv + ["--json"]


# --------------------------------------------------------------------------- pipeline.fold

def _fold_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    method = _enum("method", p.get("method"), ("esmatlas", "esmfold", "colabfold"), "esmatlas")
    remote = bool(p.get("allow_remote"))
    if method in ("esmatlas", "colabfold") and not remote:
        where = "api.esmatlas.com" if method == "esmatlas" else "the ColabFold MSA server"
        raise BadParams(f"method: {method} sends the sequence to {where}; set allow_remote "
                        "(the user approves it), or use method esmfold on this machine")
    raw = p.get("source")
    found = ctx.find(raw.strip()) if isinstance(raw, str) and raw.strip() else None
    if found is not None:
        source = str(found[0])
        ctx.inputs["source"] = found[1]
    else:
        seq = re.sub(r"\s+", "", raw) if isinstance(raw, str) else ""
        if not _SEQUENCE.match(seq):
            raise BadParams("source: give an uploaded FASTA (its upload id) or one protein "
                            "sequence in one-letter code (10 to 10000 residues)")
        source = seq.upper()
    refs = []
    for item in p.get("reference") or []:
        name, sep, ref = str(item).partition("=")
        if not sep or not _NAME.match(name.strip()) or not ref.strip():
            raise BadParams(f"reference: {item!r} must be NAME=PDB:1abc[:A], NAME=UniProt:P… or "
                            "NAME=<upload id>")
        ref = ref.strip()
        if _REMOTE_STRUCTURE.match(ref):
            if not remote:
                raise BadParams(f"reference: {ref} is fetched from RCSB or AlphaFold DB; set "
                                "allow_remote, or upload the structure")
        else:
            ref = str(ctx.file(f"reference {name.strip()}", ref))
        refs.append(f"{name.strip()}={ref}")
    return {"source": source, "method": method, "allow_remote": remote, "references": refs,
            "timeout": _int("timeout", p.get("timeout", 300), 10, 86400)}


def _fold_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "fold", r["source"], _opt("out", OUTPUT_TOKEN), _opt("method", r["method"]),
                _opt("timeout", r["timeout"]))
    if r["allow_remote"]:
        argv.append("--allow-remote")
    argv += [_opt("reference", ref) for ref in r["references"]]
    return argv + ["--json"]


# --------------------------------------------------------------------------- pipeline.dock

def _dock_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    remote = bool(p.get("allow_remote"))
    rec = p.get("receptor")
    if isinstance(rec, str) and _REMOTE_STRUCTURE.match(rec.strip()):
        if not remote:
            raise BadParams(f"receptor: {rec.strip()} is fetched from RCSB or AlphaFold DB; set "
                            "allow_remote, or upload the PDB file")
        receptor = rec.strip()
    else:
        receptor = str(ctx.file("receptor", rec))
    lig = p.get("ligands")
    found = ctx.find(lig.strip()) if isinstance(lig, str) and lig.strip() else None
    if found is not None:
        ligands = str(found[0])
        ctx.inputs["ligands"] = found[1]
    elif isinstance(lig, str) and _SMILES.match(lig.strip()) and not lig.strip().startswith("-"):
        ligands = lig.strip()
    else:
        raise BadParams("ligands: an uploaded CSV (name,smiles), SDF or .smi file (its upload "
                        "id), or one SMILES")
    out: dict[str, Any] = {
        "receptor": receptor, "ligands": ligands, "allow_remote": remote,
        "scoring": _enum("scoring", p.get("scoring"), ("vina", "vinardo"), "vina"),
        "exhaustiveness": _int("exhaustiveness", p.get("exhaustiveness", 8), 1, 64),
        "poses": _int("poses", p.get("poses", 9), 1, 20),
        "seed": _int("seed", p.get("seed", 42), 0, 2**31 - 1)}
    if p.get("chains"):
        out["chains"] = _text("chains", p["chains"], pattern=re.compile(r"^[A-Za-z0-9]{1,26}$"))
    if p.get("site_ligand"):
        out["site_ligand"] = _text("site_ligand", p["site_ligand"],
                                   pattern=re.compile(r"^[A-Za-z0-9]{1,5}$"))
        if not remote:
            raise BadParams("site_ligand: its SMILES comes from the RCSB Chemical Component "
                            "Dictionary; set allow_remote, or give center and size instead")
    center, size = p.get("center"), p.get("size")
    if center or size:
        if not (isinstance(center, list) and isinstance(size, list) and len(center) == 3
                and len(size) == 3):
            raise BadParams("center and size: three numbers each (x, y, z; Å)")
        out["center"] = ",".join(f"{_num('center', v):g}" for v in center)
        out["size"] = ",".join(f"{_num('size', v):g}" for v in size)
    if "site_ligand" not in out and "center" not in out:
        raise BadParams("give the docking site: site_ligand (a co-crystal ligand), or center "
                        "and size")
    return out


def _dock_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "dock", r["receptor"], r["ligands"], _opt("out", OUTPUT_TOKEN),
                _opt("scoring", r["scoring"]), _opt("exhaustiveness", r["exhaustiveness"]),
                _opt("poses", r["poses"]), _opt("seed", r["seed"]))
    for key, flag in (("site_ligand", "site-ligand"), ("center", "center"), ("size", "size"),
                      ("chains", "chains")):
        if r.get(key):
            argv.append(_opt(flag, r[key]))
    if r["allow_remote"]:
        argv.append("--allow-remote")
    return argv


# -------------------------------------------------------------------------- pipeline.admet

def _admet_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    mol = p.get("molecules")
    found = ctx.find(mol.strip()) if isinstance(mol, str) and mol.strip() else None
    if found is not None:
        molecules = str(found[0])
        ctx.inputs["molecules"] = found[1]
    elif isinstance(mol, str) and _SMILES.match(mol.strip()) and not mol.strip().startswith("-"):
        molecules = mol.strip()
    else:
        raise BadParams("molecules: an uploaded CSV (name,smiles), SDF or .smi file (its "
                        "upload id), or one SMILES")
    endpoints = [_text("endpoints", e, pattern=re.compile(r"^[A-Za-z0-9_.]{1,64}$"))
                 for e in p.get("endpoints") or []]
    return {"molecules": molecules, "endpoints": endpoints,
            "cache": str(Path(ctx.data_env["BIOAGENT_DATA_LAKE"]) / "admet")}


def _admet_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "admet", r["molecules"], _opt("out", OUTPUT_TOKEN), _opt("cache", r["cache"]))
    return argv + [_opt("endpoint", e) for e in r["endpoints"]]


# ----------------------------------------------------------------------------- research

def snapshot_paths(data_env: Mapping[str, str]) -> tuple[Path, Path]:
    """Where the research loop's snapshots and their ledger live on this runner."""
    root = Path(data_env["BIOAGENT_DATA_LAKE"]) / "snapshots"
    return root, root / "ledger.jsonl"


def _research_requirements(data_env: Mapping[str, str]) -> list[str]:
    root, ledger = snapshot_paths(data_env)
    return [] if ledger.is_file() else [f"snapshots ({ledger})"]


def _research_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    root, ledger = snapshot_paths(ctx.data_env)
    out: dict[str, Any] = {
        "question": _text("question", p.get("question")),
        "snapshots": str(root), "ledger": str(ledger),
        "state_dir": str(ctx.paths["projects"] / safe_id(ctx.project_id) / "research" / ctx.job_id),
        "background": _enum("background", p.get("background"), ("assayed", "reactome"), "assayed"),
        "hits": _enum("hits", p.get("hits"), ("potency", "screening"), "potency"),
        "permutations": _int("permutations", p.get("permutations", 1000), 100, 100000),
        "accept_review": bool(p.get("accept_review")),
        "activity": [_text("activity", a, pattern=_SOURCE_KEY) for a in p.get("activity") or []],
        "optional": [_text("optional", a, pattern=_SOURCE_KEY) for a in p.get("optional") or []]}
    if p.get("disease"):
        out["disease"] = _text("disease", p["disease"], pattern=_DISEASE)
    if p.get("formula"):
        out["formula"] = _text("formula", p["formula"], max_len=100)
    return out


def _research_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "research", r["question"], _opt("snapshots", r["snapshots"]),
                _opt("ledger", r["ledger"]), _opt("state-dir", r["state_dir"]),
                _opt("out", OUTPUT_TOKEN), _opt("background", r["background"]),
                _opt("hits", r["hits"]), _opt("permutations", r["permutations"]),
                _opt("profile", "trusted_local"))
    if r.get("disease"):
        argv.append(_opt("disease", r["disease"]))
    argv += [_opt("activity", a) for a in r["activity"]]
    argv += [_opt("optional", a) for a in r["optional"]]
    if r["accept_review"]:
        argv.append("--accept-review")
    return argv


# ------------------------------------------------------------------------------ skill.run

#: File arguments of the governed skills that run as jobs: how each is resolved.
_SKILL_FILES: dict[str, dict[str, str]] = {
    "rnaseq-differential-expression": {"sample_sheet": "rnaseq_sheet", "transcripts": "file",
                                       "annotation": "file", "genome": "file"},
    "scrna-cell-atlas": {"source": "scrna_source", "markers": "file"},
    "dock-ligands": {"receptor": "structure", "ligands": "molecules"},
    "predict-admet": {"molecules": "molecules"},
    "predict-protein-structure": {"sequences": "sequence", "reference": "structure"},
    "retrieve-literature-evidence": {"corpus": "file"},
}
GPU_SKILLS = frozenset({"predict-protein-structure"})


def _resolve_uploads(value: Any, ctx: PrepareContext, where: str) -> Any:
    """Any string in free-form arguments that is an upload id becomes that file's path."""
    if isinstance(value, str) and _UPLOAD_ID.match(value.strip()):
        return str(ctx.file(where, value.strip()))
    if isinstance(value, Mapping):
        return {k: _resolve_uploads(v, ctx, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_uploads(v, ctx, f"{where}[{i}]") for i, v in enumerate(value)]
    return value


def _skill_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    from .catalog import get_entry, skill_specs
    from .dispatch import validate

    sid = p.get("skill_id")
    spec = next((s for s in skill_specs() if s["id"] == sid), None)
    entry = get_entry(f"skill.{sid}") if isinstance(sid, str) else None
    if spec is None or entry is None:
        raise BadParams(f"skill_id: no governed skill {sid!r}")
    if not spec["pinned"] and not p.get("allow_unpinned"):
        raise BadParams(f"skill_id: {sid} is a candidate no lockfile pins; it runs only as a "
                        "development run (allow_unpinned: true), which is recorded and never "
                        "released")
    args = p.get("arguments")
    if not isinstance(args, Mapping):
        raise BadParams("arguments: an object with the skill's arguments")
    v = validate(entry["parameters"], dict(args))
    if not v.ok:
        hint = " ".join(dict.fromkeys(v.hints))
        raise BadParams(f"arguments: {'; '.join(v.problems[:6])}" + (f" ({hint})" if hint else ""))
    args = dict(v.value)
    for name, how in _SKILL_FILES.get(sid, {}).items():
        value = args.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if how == "rnaseq_sheet":
            args[name] = str(_stage_sheet(ctx, name, value, ("fastq_1", "fastq_2"))[0])
        elif how == "scrna_source":
            path = ctx.file(name, value)
            args[name] = (str(_stage_sheet(ctx, name, value, ("path",))[0])
                          if _is_sheet(path, "path") else str(path))
        elif how == "file":
            args[name] = str(ctx.file(name, value))
        else:   # structure / molecules / sequence: a file when it names one, else a literal
            found = ctx.find(value)
            if found is not None:
                args[name] = str(found[0])
                ctx.inputs[name] = found[1]
    args = _resolve_uploads(args, ctx, "arguments")
    remote = bool(entry.get("network_if") and args.get(entry["network_if"]))
    return {"skill_id": sid, "arguments": args, "pinned": bool(spec["pinned"]),
            "network": bool(entry.get("network")) or remote, "remote": remote}


def _skill_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    request = {"skill_id": r["skill_id"], "arguments": r["arguments"],
               "project_id": ctx.project_id, "state_root": str(ctx.paths["projects"]),
               "network": ctx.settings.get("network"), "purpose": ctx.settings.get("purpose"),
               "device": ctx.device, "tcmdb_root": ctx.data_env.get("BIOAGENT_TCMDB")}
    path = ctx.job_dir / "skill_request.json"
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    return [ctx.python, "-I", "-m", "tcmstudio.kinds", "skill-run", _opt("request", path),
            _opt("out", OUTPUT_TOKEN)]


def _envelope_ok(path: Path) -> str | None:
    """envelope.json of a skill.run job: a governed run that completed, and every output
    copied into the job with the digest the run recorded."""
    try:
        env = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"not readable JSON: {exc}"
    if env.get("status") != "succeeded":
        return f"the governed run did not complete ({env.get('status')})"
    problems = []
    base = (path.parent / "outputs").resolve()
    for out in (env.get("governance") or {}).get("outputs") or ():
        if not out.get("path") or not out.get("sha256") or out.get("missing"):
            continue
        target = (base / str(out["path"])).resolve()
        if base not in target.parents:
            problems.append(f"{out['path']}: outside the output directory")
            continue
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            problems.append(f"{out['path']}: not copied")
            continue
        if digest != str(out["sha256"]).removeprefix("sha256:"):
            problems.append(f"{out['path']}: digest differs from the governed run's record")
    return "; ".join(problems) or None


def _skill_result(job_dir: Path, r: dict[str, Any]) -> Any:
    del r
    try:
        env = json.loads((job_dir / "out" / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    gov = env.get("governance") or {}
    receipt = env.get("receipt") or {}
    return {"status": env.get("status"), "summary": env.get("summary"),
            "released": gov.get("released"), "via": env.get("via"),
            "text": str(env.get("text") or "")[:4000],
            "content_hash": receipt.get("content_hash"), "audit_head": receipt.get("audit_head"),
            "envelope": "envelope.json"}


# ---------------------------------------------------------------------------- tcmdb.fetch

def _tcmdb_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    from .catalog import get_entry
    entry = get_entry("job.tcmdb.fetch")
    allowed = ((entry or {}).get("parameters", {}).get("properties", {})
               .get("dataset", {}).get("enum")) or []
    dataset = _text("dataset", p.get("dataset"), pattern=re.compile(r"^[a-z0-9_]{1,40}$"))
    if allowed and dataset not in allowed:
        raise BadParams(f"dataset: {dataset!r} cannot be fetched by a program here; one of "
                        f"{', '.join(allowed[:20])}…")
    return {"dataset": dataset, "include_optional": bool(p.get("include_optional")),
            "root": ctx.data_env["BIOAGENT_TCMDB"]}


def _tcmdb_fetch_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    argv = _cli(ctx, "tcmdb", _opt("root", r["root"]), "fetch", r["dataset"])
    return argv + (["--all"] if r["include_optional"] else [])


def _hub_status(r: dict[str, Any], data_env: Mapping[str, str]) -> list[dict[str, Any]]:
    from bioagent.tcmdb.hub import TCMDataHub
    hub = TCMDataHub(r.get("root") or data_env["BIOAGENT_TCMDB"])
    return hub.status(None if r["dataset"] == "all" else r["dataset"])


def _tcmdb_fetch_verify(r: dict[str, Any], data_env: Mapping[str, str]) -> list[str]:
    try:
        status = _hub_status(r, data_env)
    except Exception as exc:                                    # noqa: BLE001
        return [f"the hub could not report the dataset: {type(exc).__name__}: {exc}"]
    missing = [f for s in status for f in s.get("missing_default") or ()]
    return [f"files still missing after the download: {', '.join(missing)}"] if missing else []


def _tcmdb_build_prepare(p: dict[str, Any], ctx: PrepareContext) -> dict[str, Any]:
    from .catalog import get_entry
    entry = get_entry("job.tcmdb.build")
    allowed = ((entry or {}).get("parameters", {}).get("properties", {})
               .get("dataset", {}).get("enum")) or []
    dataset = _text("dataset", p.get("dataset"), pattern=re.compile(r"^[a-z0-9_]{1,40}$"))
    if allowed and dataset not in allowed:
        raise BadParams(f"dataset: no dataset {dataset!r} in the hub's catalogue")
    return {"dataset": dataset, "root": ctx.data_env["BIOAGENT_TCMDB"]}


def _tcmdb_build_command(r: dict[str, Any], ctx: StartContext) -> list[str]:
    return _cli(ctx, "tcmdb", _opt("root", r["root"]), "build", r["dataset"])


def _tcmdb_build_verify(r: dict[str, Any], data_env: Mapping[str, str]) -> list[str]:
    try:
        status = _hub_status(r, data_env)
    except Exception as exc:                                    # noqa: BLE001
        return [f"the hub could not report the dataset: {type(exc).__name__}: {exc}"]
    if r["dataset"] == "all":
        unbuilt = [s["dataset"] for s in status if s.get("files_present") and not s.get("built")]
        return [f"not built: {', '.join(unbuilt)}"] if unbuilt else []
    return [] if status and status[0].get("built") else [f"{r['dataset']} is not built"]


def _tcmdb_result(job_dir: Path, r: dict[str, Any]) -> Any:
    del job_dir
    try:
        status = _hub_status(r, {"BIOAGENT_TCMDB": r["root"]})
    except Exception:                                           # noqa: BLE001
        return None
    if r["dataset"] == "all":
        return {"built": [s["dataset"] for s in status if s.get("built")]}
    return status[0] if status else None


# ------------------------------------------------------------------------------- registry

def _stdout_result(job_dir: Path, r: dict[str, Any]) -> Any:
    del r
    return stdout_json(job_dir)


def _table_result(name: str) -> Callable[[Path, dict[str, Any]], Any]:
    def result(job_dir: Path, r: dict[str, Any]) -> Any:
        del r
        rows = _table_head(job_dir / "out" / name)
        return {name: rows} if rows is not None else None
    return result


def default_kinds() -> dict[str, Kind]:
    run_json = ("run", "run.json", True)
    report = ("report", "report.html", True)
    kinds = [
        Kind("pipeline.rnaseq", ("RNA-seq 差异表达流程", "RNA-seq pipeline"), "hours",
             _rnaseq_prepare, _rnaseq_command,
             artefacts=(run_json, report, ("results", "deseq2_results.tsv", True)),
             validators={"run": _verifier("bioagent.omics.rnaseq")},
             timeout_s=48 * 3600.0, result=_stdout_result, progress=_rnaseq_progress),
        Kind("pipeline.scrna", ("单细胞分析流程", "Single-cell pipeline"), "minutes",
             _scrna_prepare, _scrna_command,
             artefacts=(run_json, report, ("clusters", "clusters.tsv", True),
                        ("cells", "cells.tsv", True)),
             validators={"run": _verifier("bioagent.omics.scrna")},
             # scVI is CPU-only by design (reproducible models); nothing else here uses a GPU
             gpu=False, timeout_s=12 * 3600.0, result=_stdout_result),
        Kind("pipeline.fold", ("蛋白结构预测流程", "Structure prediction"), "minutes",
             _fold_prepare, _fold_command,
             artefacts=(run_json, ("summary", "summary.json", True), report),
             validators={"run": _verifier("bioagent.structure.fold")},
             gpu=lambda r: r.get("method") == "esmfold",
             network=lambda r: bool(r.get("allow_remote")),
             remote=lambda r: bool(r.get("allow_remote")),
             timeout_s=12 * 3600.0, result=_stdout_result),
        Kind("pipeline.dock", ("分子对接流程", "Docking"), "minutes", _dock_prepare, _dock_command,
             artefacts=(run_json, ("scores", "scores.tsv", True), report),
             validators={"run": _verifier("bioagent.docking.pipeline")},
             network=lambda r: bool(r.get("allow_remote")),
             remote=lambda r: bool(r.get("allow_remote")), timeout_s=24 * 3600.0,
             result=_table_result("scores.tsv")),
        Kind("pipeline.admet", ("ADMET 预测流程", "ADMET"), "minutes", _admet_prepare,
             _admet_command, artefacts=(("table", "admet.tsv", True), run_json),
             validators={"run": _verifier("bioagent.admet.pipeline")},
             timeout_s=6 * 3600.0, result=_table_result("admet.tsv")),
        Kind("research.run", ("研究闭环", "Research loop"), "minutes", _research_prepare,
             _research_command,
             artefacts=(("artifact", "artifact.json", True), ("result", "result.json", True)),
             validators={"artifact": _artifact_outputs, "result": _json_file},
             # exit 1: the loop ran to the end and release was not authorised — a result
             ok_exit_codes=frozenset({0, 1}), timeout_s=12 * 3600.0,
             requirements=_research_requirements, result=_stdout_result),
        Kind("skill.run", ("受治理 Skill 后台运行", "Governed skill (background)"), "minutes",
             _skill_prepare, _skill_command,
             artefacts=(("envelope", "envelope.json", True),),
             validators={"envelope": _envelope_ok},
             gpu=lambda r: r.get("skill_id") in GPU_SKILLS,
             network=lambda r: bool(r.get("network")), remote=lambda r: bool(r.get("remote")),
             timeout_s=12 * 3600.0, result=_skill_result),
        Kind("tcmdb.fetch", ("下载数据集", "Fetch a dataset"), "minutes", _tcmdb_prepare,
             _tcmdb_fetch_command, network=True, timeout_s=12 * 3600.0,
             verify=_tcmdb_fetch_verify, result=_tcmdb_result),
        Kind("tcmdb.build", ("构建数据集", "Build a dataset"), "minutes", _tcmdb_build_prepare,
             _tcmdb_build_command, timeout_s=6 * 3600.0, verify=_tcmdb_build_verify,
             result=_tcmdb_result),
    ]
    return {k.kind: k for k in kinds}


KINDS = default_kinds()


# ------------------------------------------------------------------- the skill.run worker

def _copy_outputs(envelope: Mapping[str, Any], out: Path) -> None:
    result = envelope.get("result") or {}
    src_root = Path(str(((result.get("governed") or {}).get("output_dir")) or ""))
    if not src_root.is_dir():
        return
    dest_root = (out / "outputs").resolve()
    for item in (envelope.get("governance") or {}).get("outputs") or ():
        rel = str(item.get("path") or "")
        src = (src_root / rel).resolve()
        dest = (dest_root / rel).resolve()
        if not rel or src_root.resolve() not in src.parents or dest_root not in dest.parents:
            continue
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


def skill_run(request_path: str | Path, out_dir: str | Path) -> int:
    """Run one governed skill in this process and write ``envelope.json`` and ``outputs/``.

    Exit 0 when the governed run completed (released or not: the envelope says which);
    3 when it was refused before running; 1 when it failed."""
    from .catalog import get_entry
    from .dispatch import call

    req = json.loads(Path(request_path).read_text(encoding="utf-8"))
    sid = str(req.get("skill_id") or "")
    entry = get_entry(f"skill.{sid}")
    if entry is None:
        print(f"refused: no governed skill {sid!r}", file=sys.stderr)
        return 3
    # This process is the job: the entry must run here instead of submitting another job.
    entry["job"] = False
    context = {"where": "runner", "state_root": req.get("state_root"),
               "project_id": req.get("project_id"), "network": req.get("network"),
               "purpose": req.get("purpose") or "academic", "device": req.get("device") or "cpu",
               "tcmdb_root": req.get("tcmdb_root")}
    envelope = call(f"skill.{sid}", req.get("arguments") or {}, context)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _copy_outputs(envelope, out)
    (out / "envelope.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    status = envelope.get("status")
    print(json.dumps({"status": status, "summary": envelope.get("summary"),
                      "released": (envelope.get("governance") or {}).get("released")},
                     ensure_ascii=False))
    if status == "succeeded":
        return 0
    err = envelope.get("error") or {}
    print(f"{status}: {err.get('message') or envelope.get('summary')}", file=sys.stderr)
    return 3 if status == "refused" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tcmstudio.kinds",
                                     description="workers behind the runner's job kinds")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("skill-run", help="run one governed skill as a job")
    s.add_argument("--request", required=True)
    s.add_argument("--out", required=True)
    a = parser.parse_args(argv)
    if a.cmd == "skill-run":
        return skill_run(a.request, a.out)
    return 2                                                    # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
