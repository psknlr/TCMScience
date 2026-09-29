"""Durable workflow state: the journal a restart replays, and the amendment it enables.

``psh.runtime.checkpoint`` snapshots a loop so it can be *resumed*. This records each
node's outcome so a *changed* program can keep what is still valid — the thing a snapshot
cannot answer, because a snapshot of a program that no longer exists says nothing about
which of its steps survive the edit.
"""

from .delta import Decision, NodeDelta, WorkflowDelta, diff, seed_graph
from .journal import (
    JournalRecord, NodeOutcome, RecordKind, ReplayState, WorkflowJournal, result_digest,
)

__all__ = [
    "WorkflowJournal", "JournalRecord", "RecordKind", "ReplayState", "NodeOutcome",
    "result_digest",
    "diff", "seed_graph", "WorkflowDelta", "NodeDelta", "Decision",
]
