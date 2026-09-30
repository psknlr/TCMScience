"""An append-only journal of what a run did, so a restart can replay it.

``CheckpointStore`` snapshots a loop's whole state after every iteration. That is the right
primitive for *resume* and the wrong one for *amend*: a snapshot says where the run got to,
and says nothing about which of its steps are still valid if the program itself changes. A
snapshot of a program that no longer exists cannot answer "may I keep the retrieval?".

So this is the other half, in the shape ZCode's engine uses and for the same reason: an
append-only record of every node's start and outcome, keyed by the node's **content
signature** rather than its position. Identity by position is the defect worth naming —
resolving a cached result by ordinal means inserting a step at the top silently re-points
every later result — so a record here is only ever matched to a node whose signature hashes
to the same value.

Two rules carried over from the rest of the package rather than reinvented:

* **A result is stored only where the persistence rules would store it.**
  ``psh.runtime.checkpoint.withholding_reason`` is the one function that decides, and this
  calls it. A result withheld here is recorded as withheld, and the node runs again — which
  is exactly what a checkpoint does with the same value.
* **The record holds no prose.** A journal row carries a node id, a signature, a digest, a
  label and a state. Task text, payloads and error messages are the checkpoint's business,
  under the checkpoint's redaction rule.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import content_hash, new_id
from ..labels import DataLabel, Sensitivity
from ..runtime.checkpoint import withholding_reason

__all__ = ["RecordKind", "JournalRecord", "WorkflowJournal", "ReplayState", "NodeOutcome"]


class RecordKind(str, Enum):
    """What happened. Enough kinds to replay a run, and no more."""

    RUN_STARTED = "run_started"
    PROGRAM_COMPILED = "program_compiled"
    PROGRAM_AMENDED = "program_amended"
    NODE_STARTED = "node_started"
    NODE_SUCCEEDED = "node_succeeded"
    NODE_FAILED = "node_failed"
    #: The node was not run because a recorded result for the same signature was reused.
    NODE_REUSED = "node_reused"
    #: A prior result stopped being valid: its signature changed, or an upstream changed.
    NODE_INVALIDATED = "node_invalidated"
    RUN_FINISHED = "run_finished"


class NodeOutcome(str, Enum):
    """A node's standing in a replayed run."""

    UNKNOWN = "unknown"          # started, never reported: the process died inside it
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REUSED = "reused"
    INVALIDATED = "invalidated"


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One row. References, digests and states — never the values themselves."""

    seq: int
    run_id: str
    kind: RecordKind
    program_id: str = ""
    node_id: str = ""
    signature: str = ""
    result_digest: str = ""
    sensitivity: str = ""
    withheld: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "run_id": self.run_id, "kind": self.kind.value,
                "program_id": self.program_id, "node_id": self.node_id,
                "signature": self.signature, "result_digest": self.result_digest,
                "sensitivity": self.sensitivity, "withheld": self.withheld,
                "detail": dict(self.detail), "at": self.at}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    kind          TEXT NOT NULL,
    program_id    TEXT NOT NULL DEFAULT '',
    node_id       TEXT NOT NULL DEFAULT '',
    signature     TEXT NOT NULL DEFAULT '',
    result_digest TEXT NOT NULL DEFAULT '',
    sensitivity   TEXT NOT NULL DEFAULT '',
    withheld      TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '{}',
    at            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_journal_run ON journal(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_journal_sig ON journal(signature, kind);
"""

_COLUMNS = ("seq", "run_id", "kind", "program_id", "node_id", "signature",
            "result_digest", "sensitivity", "withheld", "detail", "at")


def result_digest(value: Any) -> str:
    """A short, stable digest of a result: enough to tell two apart, no content."""
    return content_hash(value)[:24]


@dataclass(frozen=True, slots=True)
class _Stored:
    outcome: NodeOutcome
    signature: str
    digest: str
    has_value: bool
    withheld: str
    sensitivity: str
    attempts: int


class ReplayState:
    """What a replay of the journal says about each node of a run."""

    def __init__(self, run_id: str, nodes: Mapping[str, _Stored],
                 values: Mapping[str, Any], program_id: str = "") -> None:
        self.run_id = run_id
        self.program_id = program_id
        self._nodes = dict(nodes)
        self._values = dict(values)

    def outcome(self, node_id: str) -> NodeOutcome:
        stored = self._nodes.get(node_id)
        return stored.outcome if stored is not None else NodeOutcome.UNKNOWN

    def signature(self, node_id: str) -> str:
        stored = self._nodes.get(node_id)
        return stored.signature if stored is not None else ""

    def has_result(self, node_id: str) -> bool:
        """Whether a *value* is available, not merely a record that one existed."""
        stored = self._nodes.get(node_id)
        return bool(stored is not None and stored.has_value and node_id in self._values)

    def result(self, node_id: str) -> Any:
        return self._values.get(node_id)

    def withheld_reason(self, node_id: str) -> str:
        stored = self._nodes.get(node_id)
        return stored.withheld if stored is not None else ""

    def label(self, node_id: str) -> DataLabel:
        stored = self._nodes.get(node_id)
        if stored is None or not stored.sensitivity:
            return DataLabel()
        return DataLabel(Sensitivity[stored.sensitivity],
                         rationale="restored from the workflow journal")

    def in_doubt(self) -> tuple[str, ...]:
        """Nodes that started and never reported: the process died inside them.

        The distinction the operation ledger exists for, at the workflow level. "It failed"
        and "nobody knows" call for different responses, and only one of them may be
        retried without asking.
        """
        return tuple(sorted(nid for nid, s in self._nodes.items()
                            if s.outcome is NodeOutcome.UNKNOWN))

    def succeeded(self) -> tuple[str, ...]:
        return tuple(sorted(nid for nid, s in self._nodes.items()
                            if s.outcome is NodeOutcome.SUCCEEDED))

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for stored in self._nodes.values():
            counts[stored.outcome.value] = counts.get(stored.outcome.value, 0) + 1
        return f"{len(self._nodes)} node(s) {counts}, {len(self._values)} stored result(s)"

    def __len__(self) -> int:
        return len(self._nodes)

    def __bool__(self) -> bool:
        return True


class WorkflowJournal:
    """Durable, append-only, thread-safe record of a program's execution."""

    def __init__(self, path: str | Path | None = None) -> None:
        #: ``None`` keeps the journal in memory. It says so rather than pretending: a
        #: run journalled in memory survives nothing, which is fine for a test and never
        #: fine for the restart the journal exists for.
        self.path = Path(path) if path is not None else None
        self.durable = self.path is not None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path) if self.path is not None else ":memory:",
                                     check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA synchronous=FULL")
        if self.path is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self.closed = False

    # ------------------------------------------------------------------ write
    def append(self, kind: RecordKind, run_id: str, *, program_id: str = "",
               node_id: str = "", signature: str = "", result: Any = None,
               store_result: bool = False, label: Any = None, withheld: str = "",
               **detail: Any) -> JournalRecord:
        """Append one record. ``store_result`` is the caller's decision, already ruled on."""
        with self._lock:
            if self.closed:
                raise RuntimeError(
                    f"journal {getattr(self.path, 'name', ':memory:')} is closed; refusing "
                    f"to append {kind.value} to a record that can no longer be replayed")
            digest = result_digest(result) if result is not None else ""
            sensitivity = (label.sensitivity.name if label is not None
                           and hasattr(label, "sensitivity") else "")
            body = ""
            if store_result and not withheld:
                try:
                    # No ``default=str`` here, deliberately: a stored result has to round
                    # trip, and stringifying whatever will not serialise would record
                    # "<object object at 0x7f...>" as the value a later run reuses. The
                    # detail column below may stringify, because nothing reads it back as
                    # data.
                    body = json.dumps(result, sort_keys=True, ensure_ascii=False)
                except (TypeError, ValueError):
                    # A value that will not serialise is not storable, and saying so is
                    # better than storing its repr and calling that the result.
                    body, withheld = "", "the result is not JSON-serialisable"
            cursor = self._conn.execute(
                """INSERT INTO journal (run_id, kind, program_id, node_id, signature,
                       result_digest, sensitivity, withheld, result, detail, at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, kind.value, program_id, node_id, signature, digest, sensitivity,
                 withheld, body, json.dumps(dict(detail), sort_keys=True, default=str),
                 time.time()))
            return JournalRecord(
                seq=int(cursor.lastrowid or 0), run_id=run_id, kind=kind,
                program_id=program_id, node_id=node_id, signature=signature,
                result_digest=digest, sensitivity=sensitivity, withheld=withheld,
                detail=dict(detail))

    def record_success(self, run_id: str, node_id: str, *, signature: str, result: Any,
                       label: Any = None, ceiling: Sensitivity | None = None,
                       allow_results: bool = True, program_id: str = "") -> JournalRecord:
        """Record a node's success, storing its value only where the rules permit.

        The rule is ``psh.runtime.checkpoint.withholding_reason`` — the same one the
        checkpoint applies — so the journal cannot become a second, ungoverned copy of what
        the WorkGraph refused.
        """
        reason = withholding_reason(label, ceiling, allow_results)
        return self.append(RecordKind.NODE_SUCCEEDED, run_id, node_id=node_id,
                           signature=signature, result=result, store_result=reason is None,
                           label=label, withheld=reason or "", program_id=program_id)

    # ------------------------------------------------------------------- read
    def records(self, run_id: str = "") -> list[JournalRecord]:
        with self._lock:
            query = ("SELECT " + ", ".join(_COLUMNS) + " FROM journal"
                     + (" WHERE run_id = ?" if run_id else "") + " ORDER BY seq")
            rows = self._conn.execute(query, (run_id,) if run_id else ()).fetchall()
        return [JournalRecord(
            seq=r["seq"], run_id=r["run_id"], kind=RecordKind(r["kind"]),
            program_id=r["program_id"], node_id=r["node_id"], signature=r["signature"],
            result_digest=r["result_digest"], sensitivity=r["sensitivity"],
            withheld=r["withheld"], detail=json.loads(r["detail"]), at=r["at"])
            for r in rows]

    def replay(self, run_id: str) -> ReplayState:
        """Fold the journal into the state of each node, oldest record first.

        Replay is the *only* way this state is produced. Nothing caches a summary
        alongside the log, because a summary that can disagree with the log is a second
        source of truth and the log then stops being the record.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT seq, kind, node_id, signature, result_digest, sensitivity,
                          withheld, result, program_id
                   FROM journal WHERE run_id = ? ORDER BY seq""", (run_id,)).fetchall()

        nodes: dict[str, _Stored] = {}
        values: dict[str, Any] = {}
        program_id = ""
        for row in rows:
            kind = RecordKind(row["kind"])
            if row["program_id"]:
                program_id = row["program_id"]
            node_id = row["node_id"]
            if not node_id:
                continue
            previous = nodes.get(node_id)
            attempts = previous.attempts if previous is not None else 0
            if kind is RecordKind.NODE_STARTED:
                nodes[node_id] = _Stored(NodeOutcome.UNKNOWN, row["signature"], "",
                                         False, "", "", attempts + 1)
                values.pop(node_id, None)
            elif kind is RecordKind.NODE_SUCCEEDED:
                has_value = bool(row["result"])
                if has_value:
                    try:
                        values[node_id] = json.loads(row["result"])
                    except json.JSONDecodeError:   # pragma: no cover - defensive
                        has_value = False
                nodes[node_id] = _Stored(NodeOutcome.SUCCEEDED, row["signature"],
                                         row["result_digest"], has_value,
                                         row["withheld"], row["sensitivity"], attempts)
            elif kind is RecordKind.NODE_FAILED:
                nodes[node_id] = _Stored(NodeOutcome.FAILED, row["signature"], "", False,
                                         "", "", attempts)
                values.pop(node_id, None)
            elif kind is RecordKind.NODE_REUSED:
                if previous is not None:
                    nodes[node_id] = _Stored(NodeOutcome.REUSED, previous.signature,
                                             previous.digest, previous.has_value,
                                             previous.withheld, previous.sensitivity,
                                             attempts)
            elif kind is RecordKind.NODE_INVALIDATED:
                nodes[node_id] = _Stored(NodeOutcome.INVALIDATED, row["signature"], "",
                                         False, "", "", attempts)
                values.pop(node_id, None)
        return ReplayState(run_id, nodes, values, program_id=program_id)

    def runs(self) -> list[str]:
        """Every run this journal holds, oldest first."""
        with self._lock:
            return [r[0] for r in self._conn.execute(
                "SELECT run_id FROM journal GROUP BY run_id ORDER BY MIN(seq)"
            ).fetchall()]

    def __len__(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0])

    def close(self) -> None:
        """Close under the lock, so a writer in flight finishes and later ones refuse.

        The same rule ``EventStore.close`` learned the hard way: sqlite's C extension does
        not survive one thread closing a connection another thread is inside, and the
        result is a crash rather than an exception.
        """
        with self._lock:
            if not self.closed:
                self.closed = True
                self._conn.close()

    def __enter__(self) -> "WorkflowJournal":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
