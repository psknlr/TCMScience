"""Load a dataset's downloaded files into one SQLite database, as faithfully as possible.

Each file becomes one table with the file's own column names (made SQL-safe) and every
value stored as text, exactly as the file wrote it. Blank spellings (``NA``, ``n/a``,
``-``) are kept as written. Interpreting them is the relation extractors' job
(``tcmdb.relations``), not the loader's, so a query can always be compared with the
source file.

A ``_tcmdb_files`` table records, per file: its table, size, SHA-256, row count,
columns, the URL it came from, the licence and when it was loaded. A built database
therefore says what it was built from.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .datasets import DatasetSpec, FileSpec

__all__ = ["read_table", "build_store", "connect", "safe_name", "StoreError"]

csv.field_size_limit(1 << 30)
_BATCH = 5000


class StoreError(RuntimeError):
    """A file could not be read as the table its spec says it is."""


def safe_name(name: str) -> str:
    """A column or table name SQLite can take without quoting surprises."""
    text = re.sub(r"[^0-9A-Za-z_]+", "_", str(name or "").strip()).strip("_")
    if not text:
        text = "col"
    return ("_" + text) if text[0].isdigit() else text


def _open_text(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig", errors="replace",
                                newline="")
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        members = [n for n in archive.namelist() if not n.endswith("/")]
        if not members:
            raise StoreError(f"{path.name}: empty archive")
        return io.TextIOWrapper(archive.open(members[0]), encoding="utf-8-sig",
                                errors="replace", newline="")
    return open(path, encoding="utf-8-sig", errors="replace", newline="")


def _strip(row: Sequence[Any]) -> list[str | None]:
    out = []
    for v in row:
        if v is None:
            out.append(None)
        else:
            text = str(v).strip().strip("\r")
            out.append(text if text != "" else None)
    return out


def _tsv(path: Path, columns: Sequence[str] = ()) -> Iterator[list[str | None]]:
    """Tab-separated, rejoining records that a newline inside a field split.

    HERB's clinical-trial table writes free text with embedded line breaks and no
    quoting, so one record can span several physical lines. A line with fewer fields than
    the header is the start of such a record: following lines are appended (the break
    kept as a newline) until the record has the header's field count.
    """
    with _open_text(path) as fh:
        header: list[str] | None = list(columns) or None
        if header:
            yield list(header)
        pending: list[str] | None = None
        for line in fh:
            line = line.rstrip("\r\n")
            if header is None:
                if not line.strip():
                    continue
                header = line.split("\t")
                yield _strip(header)
                continue
            parts = line.split("\t")
            if pending is not None:
                pending[-1] += "\n" + parts[0]
                pending.extend(parts[1:])
                parts, pending = pending, None
            if len(parts) < len(header):
                pending = parts
                continue
            if any(p.strip() for p in parts):
                yield _strip(parts)
        if pending is not None and any(p.strip() for p in pending):
            yield _strip(pending)


def _delimited(path: Path, delimiter: str) -> Iterator[list[str | None]]:
    with _open_text(path) as fh:
        reader = csv.reader(fh, delimiter=delimiter,
                            quoting=csv.QUOTE_NONE if delimiter == "\t" else csv.QUOTE_MINIMAL)
        for row in reader:
            if row and any(c.strip() for c in row):
                yield _strip(row)


def _whitespace(path: Path) -> Iterator[list[str | None]]:
    """First field, last field, and everything between joined: ``id  name ...  values``."""
    with _open_text(path) as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            if len(parts) < 3:
                yield _strip(parts)
            else:
                yield _strip([parts[0], " ".join(parts[1:-1]), parts[-1]])


def _xlsx(path: Path, sheet: str | None) -> Iterator[list[str | None]]:
    try:
        import openpyxl
    except ImportError as exc:                           # pragma: no cover
        raise StoreError("reading .xlsx needs openpyxl (pip install 'bioagent[formulas]')") \
            from exc
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            if row and any(c is not None and str(c).strip() for c in row):
                yield _strip(row)
    finally:
        wb.close()


def _parquet(path: Path) -> Iterator[list[str | None]]:
    import pyarrow.parquet as pq
    table = pq.read_table(path)
    names = table.column_names
    yield list(names)
    for batch in table.to_batches(max_chunksize=_BATCH):
        cols = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
        for values in zip(*cols):
            yield _strip([json.dumps(v, ensure_ascii=False, default=str)
                          if isinstance(v, (dict, list)) else v for v in values])


def _json_records(path: Path, lines: bool) -> Iterator[list[str | None]]:
    def records() -> Iterator[dict]:
        with _open_text(path) as fh:
            if lines:
                for line in fh:
                    line = line.strip()
                    if line:
                        yield json.loads(line)
            else:
                data = json.load(fh)
                yield from (data if isinstance(data, list) else [data])
    keys: list[str] = []
    rows: list[dict] = []
    for rec in records():                  # keys of the whole file, in first-seen order
        if isinstance(rec, dict):
            for k in rec:
                if k not in keys:
                    keys.append(k)
            rows.append(rec)
    yield keys
    for rec in rows:
        yield _strip([json.dumps(rec.get(k), ensure_ascii=False)
                      if isinstance(rec.get(k), (dict, list)) else rec.get(k) for k in keys])


def _gmt(path: Path) -> Iterator[list[str | None]]:
    """Gene sets, one member per row: set, description, gene."""
    yield ["gene_set", "description", "gene"]
    with _open_text(path) as fh:
        for line in fh:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3:
                continue
            for gene in parts[2:]:
                if gene.strip():
                    yield _strip([parts[0], parts[1], gene])


_RULE = re.compile(r"^[-_]{20,}$")


def _ttd(path: Path) -> Iterator[list[str | None]]:
    """TTD's tagged format, one row per tag: ``ttd_id, field, value, value2, value3``.

    Each file opens with a free-text preamble and an abbreviation list, each closed by a
    ruled line; data follows the second rule. Two layouts follow it:
    * id rows: ``T47101 <tab> GENENAME <tab> FGFR1``;
    * blocks (drug-disease): ``TTDDRUID <tab> D…`` opens a block, and the rows after it
      (``INDICATI <tab> disease <tab> ICD-11 <tab> status``) belong to that drug.
    """
    yield ["ttd_id", "field", "value", "value2", "value3"]
    rules = 0
    block_id: str | None = None
    with _open_text(path) as fh:
        for line in fh:
            parts = [p.strip() for p in line.rstrip("\r\n").split("\t")]
            if rules < 2:
                if _RULE.match("".join(parts)):
                    rules += 1
                continue
            while parts and not parts[-1]:
                parts.pop()
            if len(parts) < 2 or not parts[0]:
                continue
            if parts[0] == "TTDDRUID":
                block_id = parts[1]
                continue
            # a file in the block layout opens its first block before any data row
            row = [block_id, *parts] if block_id is not None else parts
            yield _strip((row + [None] * 5)[:5])


def read_table(path: str | Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The file's rows, the first being its header, for any format a ``FileSpec`` names."""
    path = Path(path)
    fmt = spec.fmt
    if fmt == "tsv":
        return _tsv(path, spec.columns)
    if fmt == "csv":
        return _delimited(path, ",")
    if fmt == "ws":
        return _whitespace(path)
    if fmt == "xlsx":
        return _xlsx(path, spec.sheet)
    if fmt == "parquet":
        return _parquet(path)
    if fmt in ("json", "jsonl"):
        return _json_records(path, lines=(fmt == "jsonl"))
    if fmt == "gmt":
        return _gmt(path)
    if fmt == "ttd":
        return _ttd(path)
    raise StoreError(f"{path.name}: format {fmt!r} is not loadable as a table")


def _columns(header: Sequence[Any]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for i, name in enumerate(header):
        col = safe_name(name if name is not None else f"col{i + 1}")
        if col.lower() in seen:
            seen[col.lower()] += 1
            col = f"{col}_{seen[col.lower()]}"
        else:
            seen[col.lower()] = 1
        out.append(col)
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def connect(db_path: str | Path, *, readonly: bool = True) -> sqlite3.Connection:
    db_path = Path(db_path)
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load(conn: sqlite3.Connection, table: str, rows: Iterable[list[str | None]]) -> tuple[
        int, list[str]]:
    it = iter(rows)
    header = next(it, None)
    if header is None:
        raise StoreError(f"{table}: no rows")
    cols = _columns(header)
    n = len(cols)
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    # safe_name leaves only [0-9A-Za-z_], so quoting the names is enough
    columns_sql = ", ".join('"%s" TEXT' % c for c in cols)
    conn.execute(f'CREATE TABLE "{table}" ({columns_sql})')
    insert = f'INSERT INTO "{table}" VALUES ({", ".join("?" * n)})'
    batch: list[list[str | None]] = []
    count = 0
    for row in it:
        row = (list(row) + [None] * n)[:n]
        batch.append(row)
        if len(batch) >= _BATCH:
            conn.executemany(insert, batch)
            count += len(batch)
            batch.clear()
    if batch:
        conn.executemany(insert, batch)
        count += len(batch)
    return count, cols


def build_store(spec: DatasetSpec, raw_dir: str | Path, db_path: str | Path, *,
                log=print) -> dict[str, Any]:
    """Load every present, table-shaped file of ``spec`` from ``raw_dir`` into ``db_path``.

    Missing files are reported, not fatal: a manual dataset may hold only some of its
    files, and an optional file is usually absent. The database is written to a temporary
    name and moved into place, so a failed build never leaves a half-written store.
    """
    raw_dir, db_path = Path(raw_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    report: dict[str, Any] = {"dataset": spec.key, "tables": {}, "missing": [],
                              "optional_absent": [], "skipped": []}
    try:
        conn.execute("CREATE TABLE _tcmdb_files (tbl TEXT, file TEXT, url TEXT, bytes INTEGER,"
                     " sha256 TEXT, rows INTEGER, columns TEXT, license TEXT, loaded_at TEXT)")
        for f in spec.files:
            path = raw_dir / f.name
            if not f.table or f.fmt == "raw":
                if path.exists():
                    report["skipped"].append(f.name)
                continue
            if not path.exists():
                report["optional_absent" if f.optional else "missing"].append(f.name)
                continue
            rows, cols = _load(conn, f.table, read_table(path, f))
            conn.execute("INSERT INTO _tcmdb_files VALUES (?,?,?,?,?,?,?,?,?)",
                         (f.table, f.name, f.url, path.stat().st_size, _sha256(path), rows,
                          json.dumps(cols), spec.license,
                          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            report["tables"][f.table] = rows
            log(f"{spec.key}: {f.table} <- {f.name} ({rows} rows)")
        conn.commit()
    except Exception:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    tmp.replace(db_path)
    return report
