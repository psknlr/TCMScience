"""The snapshot ledger: an append-only, hash-chained record of every snapshot built.

A snapshot re-verifies its own tables and manifest on load, but someone who can write the
snapshot directory can rewrite all three consistently. The ledger is the independent
record: when a snapshot is built its id goes here, and ``load_snapshot(..., ledger=...)``
refuses a snapshot whose id is not the one recorded. Keep the ledger outside the
agent-writable tree — the workspace's ``audit/`` directory, which ``Workspace.write``
refuses — so the record and the data cannot be changed by the same hand.

Each entry carries the SHA-256 of the previous entry, so editing or deleting an entry
breaks the chain and ``verify`` says where.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

__all__ = ["SnapshotLedger", "LedgerError", "LedgerEntry"]

_GENESIS = "sha256:" + "0" * 64


class LedgerError(RuntimeError):
    """The ledger is broken, or has no entry for the snapshot asked about."""


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    key: str
    version: str
    snapshot_id: str
    qc_status: str
    recorded_at: float
    prev: str
    hash: str

    def body(self) -> dict[str, Any]:
        return {"seq": self.seq, "key": self.key, "version": self.version,
                "snapshot_id": self.snapshot_id, "qc_status": self.qc_status,
                "recorded_at": self.recorded_at, "prev": self.prev}


def _digest(body: dict[str, Any]) -> str:
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


class SnapshotLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reading
    def entries(self) -> Iterator[LedgerEntry]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield LedgerEntry(**json.loads(line))

    def verify(self) -> int:
        """Check the chain end to end; return the number of entries."""
        prev, n = _GENESIS, 0
        for n, entry in enumerate(self.entries(), start=1):
            if entry.seq != n or entry.prev != prev or entry.hash != _digest(entry.body()):
                raise LedgerError(f"{self.path}: chain broken at entry {n}")
            prev = entry.hash
        return n

    def expected(self, key: str, version: str) -> str:
        """The id recorded for ``key@version`` (the latest, if rebuilt)."""
        self.verify()
        found = [e.snapshot_id for e in self.entries() if e.key == key and e.version == version]
        if not found:
            raise LedgerError(f"{self.path}: no snapshot of {key}@{version} was recorded")
        return found[-1]

    # ------------------------------------------------------------------ writing
    def record(self, snapshot: Any) -> LedgerEntry:
        with self._lock:
            count = self.verify()
            last = _GENESIS
            for last_entry in self.entries():
                last = last_entry.hash
            body = {"seq": count + 1, "key": snapshot.key, "version": snapshot.version,
                    "snapshot_id": snapshot.snapshot_id,
                    "qc_status": str(snapshot.manifest["content"]["qc"]["status"]),
                    "recorded_at": time.time(), "prev": last}
            entry = LedgerEntry(**body, hash=_digest(body))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({**entry.body(), "hash": entry.hash},
                                    ensure_ascii=False, sort_keys=True) + "\n")
            return entry
