"""Run records: every stage of a pipeline run, with its status, check, engine and device.

A pipeline that writes its report only at the end says nothing about the stages it went
through: a run that stopped half way leaves a stderr tail, and a run whose own check
failed (a redocking 3 Å off, an ADMET run with no model) still looks finished. Each
pipeline therefore records its stages as they happen:

    rec = StageRecorder(out, "bioagent.omics.rnaseq", plan=("ingest", "qc", ...))
    with rec.stage("qc", engine="bioagent.omics.fastq", version="...") as st:
        ...                                    # the stage's work
        st.outputs = ["qc.json"]               # paths under ``out``
        st.check = "PASS"                      # when the stage checks something
    rec.not_applicable("trajectory", "no root cluster or cell type was named")
    rec.finish()

One stage is ``Stage = {name, required, status, check, reason, engine, version, device,
seconds, outputs}``:

* ``status`` is ``succeeded``, ``failed``, ``skipped`` (chosen not to run),
  ``unavailable`` (could not run here: a model, a package or a tool is missing) or
  ``not_applicable`` (nothing to do: one batch to integrate, no root for a trajectory);
* ``check`` is a validation status (:mod:`bioagent.validation`: PASS, FAIL, BLOCKED,
  UNVERIFIED, SKIPPED, NOT_APPLICABLE) when the stage tests something, else null;
* ``device`` is where the stage computed, as observed: ``cpu``, ``cuda:0``,
  ``remote:<host>``, or null with the reason when it cannot be observed (one value is
  reported rather than observed: ColabFold's ``gpu``, from its own log, which names no
  device; the stage's reason says so);
* ``seconds`` is measured with a monotonic clock.

**The rule.** A run is complete when every required stage succeeded and no stage's check
is FAIL (:attr:`StageRecorder.complete`, :func:`complete`). Nothing else makes it
complete: a run that skipped a required stage, or whose check failed, is incomplete
even though every file was written.

``stages.json`` in the output directory is rewritten atomically (a temporary file, then
``os.replace``) when a stage starts and when it ends, so a reader polling it while the
run works sees the stage in progress. A caller that cannot poll (a browser job, whose
worker runs one thing at a time) can watch the same documents as they are written
(:func:`watching`). An exception inside a stage marks it failed with
the exception's message, writes the file and is raised again. The final list goes into
``run.json`` as ``stages``, with ``complete``.

Timings live only in ``stages.json`` and ``run.json``. Reports, tables and
``limitations.md`` carry none, so a re-run gives the same bytes.

This module imports nothing from the pinned skills' code and none of them imports it.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .validation import FAIL, STATUSES as CHECK_STATUSES

__all__ = ["SCHEMA", "STATUSES", "STAGES_FILE", "LIMITATIONS_FILE", "REFERENCES_FILE", "Stage",
           "StageRecorder", "NullRecorder", "watching", "complete", "problems", "summary",
           "record_problems", "output_problems", "write_limitations", "read_limitations",
           "stage_limitations", "read_stages", "tsv_shape", "pipeline_references",
           "match_reference", "content_digest"]

SCHEMA = "bioagent.stages/1"
STATUSES = ("succeeded", "failed", "skipped", "unavailable", "not_applicable")
STAGES_FILE = "stages.json"
LIMITATIONS_FILE = "limitations.md"
REFERENCES_FILE = Path(__file__).parent / "data" / "pipeline_references.json"

_KEYS = ("name", "required", "status", "check", "reason", "engine", "version", "device",
         "seconds", "outputs")
_WORDS = {"failed": "failed", "skipped": "not run", "unavailable": "unavailable here",
          "not_applicable": "not applicable"}
#: Who sees each stages.json document as it is written (:func:`watching`).
_WATCHERS: list[Callable[[dict[str, Any]], Any]] = []


@contextmanager
def watching(watcher: Callable[[dict[str, Any]], Any]) -> Iterator[None]:
    """Within the block, ``watcher(doc)`` is called with every ``stages.json`` document a
    recorder writes, after it is written. What the watcher does is not the run's: its
    errors are dropped (an interruption, KeyboardInterrupt, still stops the run)."""
    _WATCHERS.append(watcher)
    try:
        yield
    finally:
        _WATCHERS.remove(watcher)


@dataclass
class Stage:
    """One stage of a run (see the module docstring for each field's meaning)."""

    name: str
    required: bool = True
    status: str = "succeeded"
    check: str | None = None
    reason: str | None = None
    engine: str | None = None
    version: str | None = None
    device: str | None = "cpu"
    seconds: float | None = None
    outputs: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"stage {self.name}: status {self.status!r} is not one of "
                             f"{', '.join(STATUSES)}")
        if self.check is not None and self.check not in CHECK_STATUSES:
            raise ValueError(f"stage {self.name}: check {self.check!r} is not one of "
                             f"{', '.join(CHECK_STATUSES)}")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": bool(self.required), "status": self.status,
                "check": self.check, "reason": self.reason, "engine": self.engine,
                "version": self.version, "device": self.device, "seconds": self.seconds,
                "outputs": sorted(dict.fromkeys(str(p) for p in self.outputs))}

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> "Stage":
        return cls(**{k: doc.get(k) for k in _KEYS if k in doc})


def _as_dicts(stages: Iterable[Stage | Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [s.as_dict() if isinstance(s, Stage) else dict(s) for s in stages]


def complete(stages: Iterable[Stage | Mapping[str, Any]]) -> bool:
    """Every required stage succeeded and no check is FAIL."""
    docs = _as_dicts(stages)
    return bool(docs) and all(d.get("status") == "succeeded" for d in docs
                              if d.get("required")) \
        and not any(d.get("check") == FAIL for d in docs)


def problems(stages: Iterable[Stage | Mapping[str, Any]]) -> list[str]:
    """Why a run is incomplete, one sentence per stage at fault (empty when complete)."""
    out = []
    for d in _as_dicts(stages):
        why = f": {d['reason']}" if d.get("reason") else ""
        if d.get("required") and d.get("status") != "succeeded":
            out.append(f"stage {d['name']} {_WORDS.get(str(d.get('status')), d.get('status'))}"
                       f"{why}")
        elif d.get("check") == FAIL:
            out.append(f"stage {d['name']}: its check failed{why}")
    return out


def summary(stages: Iterable[Stage | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The short form a job record carries: name, required, status, check, device, seconds."""
    return [{k: d.get(k) for k in ("name", "required", "status", "check", "device", "seconds")}
            for d in _as_dicts(stages)]


class StageRecorder:
    """The stages of one run, written to ``<out>/stages.json`` as they start and end."""

    def __init__(self, out_dir: str | Path, pipeline: str, *,
                 plan: Sequence[str] = ()) -> None:
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.pipeline = pipeline
        self.plan = list(dict.fromkeys(plan))
        self.stages: list[Stage] = []
        self.running: str | None = None
        self.finished = False
        self.write()

    # ------------------------------------------------------------ recording
    @contextmanager
    def stage(self, name: str, *, required: bool = True, engine: str | None = None,
              version: str | None = None, device: str | None = "cpu",
              outputs: Sequence[str] = ()) -> Iterator[Stage]:
        """Time one stage. The body may set ``outputs``, ``check``, ``reason``,
        ``engine``, ``version``, ``device`` or a status other than succeeded on the
        stage it is given; an exception marks the stage failed and is raised again."""
        st = Stage(name=name, required=required, engine=engine, version=version,
                   device=device, outputs=list(outputs))
        self.running = name
        self.write()
        t0 = time.perf_counter()
        try:
            yield st
        except BaseException as exc:
            st.status = "failed"
            st.reason = st.reason or _why(exc)
            st.seconds = round(time.perf_counter() - t0, 3)
            self._add(st)
            raise
        st.seconds = round(time.perf_counter() - t0, 3)
        self._add(st)

    def record(self, name: str, status: str, reason: str | None = None, *,
               required: bool = False, **fields: Any) -> Stage:
        """A stage that did not run as work: skipped, unavailable or not applicable (or
        one whose work happened elsewhere and is recorded here)."""
        st = Stage(name=name, required=required, status=status, reason=reason,
                   device=fields.pop("device", None), **fields)
        self._add(st)
        return st

    def skip(self, name: str, reason: str, *, required: bool = False, **fields: Any) -> Stage:
        return self.record(name, "skipped", reason, required=required, **fields)

    def unavailable(self, name: str, reason: str, *, required: bool = True,
                    **fields: Any) -> Stage:
        return self.record(name, "unavailable", reason, required=required, **fields)

    def not_applicable(self, name: str, reason: str, *, required: bool = False,
                       **fields: Any) -> Stage:
        return self.record(name, "not_applicable", reason, required=required, **fields)

    def _add(self, st: Stage) -> None:
        st.validate()
        self.stages = [s for s in self.stages if s.name != st.name] + [st]
        self.running = None
        self.write()

    def get(self, name: str) -> Stage | None:
        return next((s for s in self.stages if s.name == name), None)

    # ------------------------------------------------------------ the verdict
    @property
    def complete(self) -> bool:
        return complete(self.stages)

    def problems(self) -> list[str]:
        return problems(self.stages)

    def as_list(self) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.stages]

    def finish(self) -> bool:
        """The last write: nothing running, the verdict recorded. Returns ``complete``."""
        self.finished = True
        self.running = None
        self.write()
        return self.complete

    def write(self) -> Path:
        doc = {"schema": SCHEMA, "pipeline": self.pipeline, "planned": self.plan,
               "running": self.running, "stages": self.as_list(),
               "complete": self.complete if self.finished else None,
               "problems": self.problems() if self.finished else []}
        path = self.out / STAGES_FILE
        # hidden and named *.tmp, which the Studio runner's scan of out/ passes over
        tmp = path.with_name(f".{STAGES_FILE}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        for watcher in list(_WATCHERS):
            try:
                watcher(doc)
            except Exception:                                   # noqa: BLE001 - not the run's
                pass
        return path


class NullRecorder:
    """The recorder's interface, keeping nothing: for a pipeline part called on its own
    (``sc.analysis.run`` outside ``run_scrna``)."""

    stages: list[Stage] = []

    @contextmanager
    def stage(self, name: str, *, required: bool = True, engine: str | None = None,
              version: str | None = None, device: str | None = "cpu",
              outputs: Sequence[str] = ()) -> Iterator[Stage]:
        yield Stage(name=name, required=required, engine=engine, version=version,
                    device=device, outputs=list(outputs))

    def record(self, name: str, status: str, reason: str | None = None, *,
               required: bool = False, **fields: Any) -> Stage:
        return Stage(name=name, required=required, status=status, reason=reason,
                     device=fields.pop("device", None), **fields)

    def skip(self, name: str, reason: str, **kw: Any) -> Stage:
        return self.record(name, "skipped", reason, **kw)

    def unavailable(self, name: str, reason: str, **kw: Any) -> Stage:
        return self.record(name, "unavailable", reason, **kw)

    def not_applicable(self, name: str, reason: str, **kw: Any) -> Stage:
        return self.record(name, "not_applicable", reason, **kw)


def _why(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return f"{type(exc).__name__}: {text}"[:600]


def read_stages(out_dir: str | Path) -> dict[str, Any] | None:
    """``stages.json`` of a run directory, or None when there is none (yet) or it cannot be
    read (a reader may meet it between two writes on a system without atomic rename)."""
    try:
        doc = json.loads((Path(out_dir) / STAGES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA else None


# ------------------------------------------------------------------ checking a record

def record_problems(manifest: Mapping[str, Any], out_dir: str | Path) -> list[str]:
    """Completeness of a run.json that carries ``stages`` (an empty list for a legacy
    record without them, which gets the integrity checks only): the run is recorded as
    complete, every required stage succeeded, no check failed, and every file a stage
    names as its output is one of the run's recorded outputs and exists."""
    stages = manifest.get("stages")
    if stages is None:
        return []
    out = Path(out_dir)
    found = problems(stages) if isinstance(stages, list) else ["run.json: 'stages' is not a list"]
    if isinstance(stages, list) and not stages:
        found.append("run.json records no stage")
    if manifest.get("complete") is not True:
        found.append("run.json records the run as incomplete")
    elif isinstance(stages, list) and not complete(stages):
        found.append("run.json says complete, but its stages do not make it so")
    if isinstance(stages, list):
        found += output_problems(stages, manifest.get("outputs") or {}, out)
    return found


def output_problems(stages: Iterable[Stage | Mapping[str, Any]], listed: Mapping[str, Any],
                    out_dir: str | Path) -> list[str]:
    """Every file a stage names as its output is one of the run's recorded outputs
    (``listed``: path under the output directory -> digest) and exists."""
    found = []
    out = Path(out_dir)
    for d in _as_dicts(stages):
        for rel in d.get("outputs") or ():
            if rel not in listed:
                found.append(f"stage {d.get('name')}: output {rel} is not among the run's "
                             "recorded outputs")
            elif not (out / rel).is_file():
                found.append(f"stage {d.get('name')}: output {rel} is missing")
    return found


def tsv_shape(path: str | Path) -> tuple[list[str], int]:
    """(header, number of data rows) of a tab-separated table."""
    with Path(path).open(encoding="utf-8") as fh:
        header = fh.readline().rstrip("\r\n").split("\t")
        rows = sum(1 for line in fh if line.strip())
    return header, rows


# ------------------------------------------------------------------ limitations

def write_limitations(out_dir: str | Path, lines: Iterable[str]) -> list[str]:
    """``limitations.md``: one limitation per line, in order, each once; no timestamps,
    so the same run gives the same bytes. Returns the lines as written."""
    seen: list[str] = []
    for line in lines:
        text = " ".join(str(line).split())
        if text and text not in seen:
            seen.append(text)
    (Path(out_dir) / LIMITATIONS_FILE).write_text("".join(f"{t}\n" for t in seen),
                                                  encoding="utf-8")
    return seen


def read_limitations(path: str | Path) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def stage_limitations(stages: Iterable[Stage | Mapping[str, Any]]) -> list[str]:
    """One line for each stage that did not run as work (skipped, unavailable, not
    applicable or failed), with its reason: what the run did not do."""
    out = []
    for d in _as_dicts(stages):
        status = d.get("status")
        if status == "succeeded" or status not in _WORDS:
            continue
        why = f": {d['reason']}" if d.get("reason") else ""
        out.append(f"Stage {d['name']} was {_WORDS[status]}{why}.".replace("..", "."))
    return out


# ------------------------------------------------------------------ reference datasets

def pipeline_references() -> dict[str, Any]:
    """The versioned reference datasets of the pipelines (``data/pipeline_references.json``)."""
    try:
        return json.loads(REFERENCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"references": []}


def match_reference(pipeline: str, digests: Mapping[str, str]) -> dict[str, Any] | None:
    """The registered reference dataset whose input content digests are exactly
    ``digests`` (name -> sha256), or None. Matching is by content, so a run on the
    simulated reference is said to be one whatever its files are called."""
    wanted = sorted(digests.values())
    if not wanted:
        return None
    for ref in pipeline_references().get("references") or ():
        if ref.get("pipeline") != pipeline:
            continue
        recorded = sorted((ref.get("inputs") or {}).values())
        if recorded == wanted:
            return {k: ref.get(k) for k in ("id", "version", "kind", "label", "statement")}
    return None


def content_digest(parts: Mapping[str, str]) -> str:
    """One SHA-256 over named content digests, in name order (a dataset's digest)."""
    h = hashlib.sha256()
    for name in sorted(parts):
        h.update(f"{name}\0{parts[name]}\n".encode())
    return h.hexdigest()
