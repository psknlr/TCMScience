"""Source snapshots: validated, hashed, read-only tables that analyses run against.

An analysis never reads a third-party website mid-run. A source is fetched, parsed and
normalised once into a node table and an edge table (``schema``), passed through a
quality gate, and published as a snapshot::

    <root>/<key>/<version>/nodes.parquet
                           edges.parquet
                           manifest.json     what went in, what came out, the QC result
                           qc.json

The snapshot id is ``<key>@<version>#<12 hex>``, the hex being the SHA-256 of the
manifest's *content* — raw-file hashes, the parser's code hash, the two tables' content
hashes, the QC result, licence and citation — and nothing time-dependent. The same raw
files through the same parser therefore always give the same id; a test pins this.

Table hashes are computed over canonical rows (sorted keys, sorted rows), not over the
Parquet bytes, so they do not change with the writer's version.

Snapshots live in the workspace's ``data/`` tree, which an agent may write. ``load``
therefore re-hashes the tables on every load and, given the id recorded in the audit log
when the snapshot was built (``expected_id``), refuses a snapshot that no longer matches
it.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .cards import SourceCard
from .schema import validate_edge, validate_node

__all__ = ["QCThresholds", "QCReport", "Snapshot", "SnapshotError", "SnapshotRejected",
           "quality_check",
           "build_snapshot", "load_snapshot", "table_hash", "code_hash", "file_hash"]

_NESTED = ("names", "xrefs", "publications", "measure", "raw", "score")


class SnapshotError(RuntimeError):
    """A snapshot is missing, inconsistent or no longer the one that was built."""


class SnapshotRejected(SnapshotError):
    """The quality gate failed; nothing was published."""

    def __init__(self, message: str, report: "QCReport") -> None:
        super().__init__(message)
        self.report = report


# ------------------------------------------------------------------ hashing
def _canonical(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted(json.dumps(dict(r), sort_keys=True, ensure_ascii=False, default=str)
                  for r in rows)


def table_hash(rows: Iterable[Mapping[str, Any]]) -> str:
    h = hashlib.sha256()
    for line in _canonical(rows):
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


def code_hash(parser: Callable[..., Any] | str | bytes) -> str:
    """Hash of the parser's source (or of a given string), so a changed parser is a new
    snapshot even when the raw files are the same."""
    if callable(parser):
        import inspect
        text = inspect.getsource(parser)
    else:
        text = parser.decode("utf-8") if isinstance(parser, bytes) else str(parser)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ------------------------------------------------------------------ quality gate
@dataclass(frozen=True)
class QCThresholds:
    """Per-source limits. Coverage below a minimum, or drift above a maximum, sends the
    snapshot to review; structural and licence problems reject it outright."""

    min_inchikey_coverage: float = 0.0     # ingredient nodes with an InChIKey xref
    min_uniprot_coverage: float = 0.0      # target nodes with a UniProt xref
    max_drift: float = 0.2                 # |rows - previous rows| / previous rows

    @classmethod
    def from_card(cls, card: SourceCard | None) -> "QCThresholds":
        if card is None:
            return cls()
        known = {k: float(v) for k, v in card.qc.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass(frozen=True)
class QCReport:
    status: str                                    # pass | review | fail
    errors: tuple[str, ...] = ()                   # reasons to reject
    warnings: tuple[str, ...] = ()                 # reasons for a person to look
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "errors": list(self.errors),
                "warnings": list(self.warnings), "metrics": dict(self.metrics)}


def _coverage(nodes: Sequence[Mapping[str, Any]], category: str, prefix: str) -> float | None:
    pool = [n for n in nodes if n.get("category") == category]
    if not pool:
        return None
    hit = sum(1 for n in pool if (n.get("xrefs") or {}).get(prefix))
    return round(hit / len(pool), 6)


def quality_check(nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]], *,
                  thresholds: QCThresholds = QCThresholds(),
                  previous: Mapping[str, Any] | None = None,
                  gold: Mapping[str, Mapping[str, Any]] | None = None,
                  max_listed: int = 20) -> QCReport:
    """Run the gate. ``gold`` maps node id -> expected xrefs (a hand-checked answer key)."""
    errors: list[str] = []
    warnings: list[str] = []

    for kind, rows, check in (("node", nodes, validate_node), ("edge", edges, validate_edge)):
        bad = 0
        for i, row in enumerate(rows):
            problems = check(row)
            if problems:
                bad += 1
                if bad <= max_listed:
                    errors.append(f"{kind} {row.get('id', i)}: {'; '.join(problems)}")
        if bad > max_listed:
            errors.append(f"... {bad - max_listed} more {kind}s with problems")

    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        errors.append(f"{len(ids) - len(set(ids))} duplicate node ids")
    known = set(ids)
    dangling = sorted({e.get(end) for e in edges for end in ("subject", "object")
                       if e.get(end) not in known})
    if dangling:
        errors.append(f"{len(dangling)} edge endpoints are not nodes: {dangling[:5]}")

    metrics: dict[str, Any] = {"nodes": len(nodes), "edges": len(edges)}
    for category, prefix, minimum in (
            ("ingredient", "inchikey", thresholds.min_inchikey_coverage),
            ("target", "uniprot", thresholds.min_uniprot_coverage)):
        cov = _coverage(nodes, category, prefix)
        metrics[f"{prefix}_coverage"] = cov
        if cov is not None and cov < minimum:
            warnings.append(f"{prefix} coverage {cov:.1%} of {category} nodes is below "
                            f"{minimum:.0%}")
    levels: dict[str, int] = {}
    for e in edges:
        levels[str(e.get("knowledge_level"))] = levels.get(str(e.get("knowledge_level")), 0) + 1
    metrics["knowledge_levels"] = dict(sorted(levels.items()))

    if previous:
        for kind, now in (("nodes", len(nodes)), ("edges", len(edges))):
            before = int((previous.get("metrics") or {}).get(kind) or 0)
            if before:
                drift = abs(now - before) / before
                metrics[f"{kind}_drift"] = round(drift, 6)
                if drift > thresholds.max_drift:
                    warnings.append(f"{kind} changed by {drift:.1%} since the previous "
                                    f"snapshot ({before} -> {now})")

    if gold:
        by_id = {n.get("id"): n for n in nodes}
        for node_id, expected in sorted(gold.items()):
            node = by_id.get(node_id)
            if node is None:
                errors.append(f"gold: {node_id} is missing")
                continue
            xrefs = node.get("xrefs") or {}
            for prefix, value in expected.items():
                have = {str(v) for v in _listify(xrefs.get(prefix))}
                if str(value) not in have:
                    errors.append(f"gold: {node_id} {prefix} is {sorted(have)}, expected {value}")
        metrics["gold_checked"] = len(gold)

    status = "fail" if errors else ("review" if warnings else "pass")
    return QCReport(status, tuple(errors), tuple(warnings), metrics)


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple, set, frozenset)) else [value]


# ------------------------------------------------------------------ build / load
@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    key: str
    version: str
    path: Path
    manifest: Mapping[str, Any]
    nodes: tuple[Mapping[str, Any], ...]
    edges: tuple[Mapping[str, Any], ...]

    @property
    def qc_status(self) -> str:
        return str(self.manifest["content"]["qc"]["status"])


def _snapshot_id(key: str, version: str, content: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                       default=str).encode("utf-8")).hexdigest()
    return f"{key}@{version}#{digest[:12]}"


def _to_columns(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Nested values as JSON text, so every row has the same flat, typed schema."""
    columns = sorted({k for r in rows for k in r})
    out = []
    for r in rows:
        flat = {}
        for k in columns:
            v = r.get(k)
            if v is None:
                flat[k] = None                     # absent stays absent, nested or not
            elif k in _NESTED or isinstance(v, (dict, list, tuple)):
                flat[k] = json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
            else:
                flat[k] = str(v)
        out.append(flat)
    return out


def _from_columns(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        row = {}
        for k, v in r.items():
            if v is None:
                continue
            if k in _NESTED or (isinstance(v, str) and v[:1] in "[{"):
                try:
                    v = json.loads(v)
                except (TypeError, ValueError):
                    pass
            row[k] = v
        out.append(row)
    return out


def _write_table(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    flat = _to_columns(rows)
    columns = sorted({k for r in flat for k in r})
    table = pa.table({c: pa.array([r.get(c) for r in flat], type=pa.string())
                      for c in columns}) if flat else pa.table({})
    pq.write_table(table, path)


def _read_table(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    return _from_columns(pq.read_table(path).to_pylist())


def _normalise(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows as they will read back: None dropped, tuples as lists."""
    return _from_columns(_to_columns([dict(r) for r in rows]))


def build_snapshot(*, key: str, version: str, nodes: Iterable[Mapping[str, Any]],
                   edges: Iterable[Mapping[str, Any]], raw_files: Mapping[str, str | Path],
                   parser: Callable[..., Any] | str, root: str | Path,
                   card: SourceCard | None = None, license: str = "", citation: str = "",
                   thresholds: QCThresholds | None = None,
                   previous: "Snapshot | None" = None,
                   gold: Mapping[str, Mapping[str, Any]] | None = None) -> Snapshot:
    """Validate, hash and publish one source version. Raises ``SnapshotRejected`` when the
    quality gate fails; a ``review`` result is published but ``load`` will not hand it out
    until a person accepts it."""
    if card is not None and card.key != key:
        raise SnapshotError(f"card {card.key!r} does not describe source {key!r}")
    license = license or (card.license if card else "")
    citation = citation or (card.citation if card else "")
    if not license or not citation:
        raise SnapshotError("a snapshot records the licence and the citation it was built under")
    node_rows = _normalise(nodes)
    edge_rows = _normalise(edges)
    report = quality_check(node_rows, edge_rows,
                           thresholds=thresholds or QCThresholds.from_card(card),
                           previous=previous.manifest["content"]["qc"] if previous else None,
                           gold=gold)
    if report.status == "fail":
        raise SnapshotRejected(f"{key}@{version} failed the quality gate: "
                               + "; ".join(report.errors[:5]), report)
    content = {
        "key": key, "version": version,
        "raw_files": {name: file_hash(p) for name, p in sorted(raw_files.items())},
        "parser": code_hash(parser),
        "tables": {"nodes": table_hash(node_rows), "edges": table_hash(edge_rows)},
        "qc": report.as_dict(), "license": license, "citation": citation,
        "previous": previous.snapshot_id if previous else None,
    }
    if not content["raw_files"]:
        raise SnapshotError("a snapshot names the raw files it was built from")
    sid = _snapshot_id(key, version, content)
    path = Path(root) / key / version
    path.mkdir(parents=True, exist_ok=True)
    _write_table(node_rows, path / "nodes.parquet")
    _write_table(edge_rows, path / "edges.parquet")
    manifest = {"snapshot_id": sid, "content": content, "built_at": time.time()}
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False,
                                                   sort_keys=True), encoding="utf-8")
    (path / "qc.json").write_text(json.dumps(report.as_dict(), indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    return Snapshot(sid, key, version, path, manifest, tuple(node_rows), tuple(edge_rows))


def load_snapshot(root: str | Path, key: str, version: str, *, expected_id: str | None = None,
                  accept_review: bool = False) -> Snapshot:
    """Read a snapshot back, proving it is still the one that was built.

    The tables are re-hashed and compared with the manifest, the manifest is re-hashed and
    compared with its id, and — when the caller has the id recorded at build time — that
    id must match too. A snapshot whose QC status is ``review`` is refused unless the
    caller accepts it explicitly.
    """
    path = Path(root) / key / version
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SnapshotError(f"no snapshot {key}@{version} under {root}") from None
    content = manifest.get("content") or {}
    sid = _snapshot_id(key, version, content)
    if manifest.get("snapshot_id") != sid:
        raise SnapshotError(f"{key}@{version}: manifest does not hash to its id")
    if expected_id is not None and expected_id != sid:
        raise SnapshotError(f"{key}@{version} is {sid}, not the recorded {expected_id}")
    nodes = _read_table(path / "nodes.parquet")
    edges = _read_table(path / "edges.parquet")
    for name, rows in (("nodes", nodes), ("edges", edges)):
        if table_hash(rows) != content["tables"][name]:
            raise SnapshotError(f"{key}@{version}: {name} table was modified after it was built")
    status = content["qc"]["status"]
    if status == "review" and not accept_review:
        raise SnapshotError(f"{key}@{version} awaits review: "
                            + "; ".join(content["qc"]["warnings"]))
    return Snapshot(sid, key, version, path, manifest, tuple(nodes), tuple(edges))
