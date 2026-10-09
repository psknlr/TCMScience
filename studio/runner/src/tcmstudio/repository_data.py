"""The repository's complete formula workbook and materia records, with provenance.

Formula rows are source records, never clinical recommendations. The workbook does
not state a data licence; its licence is retained verbatim and distinct from MIT code.
The browser loads the compressed, indexed store only when a lookup needs it.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import re
import sqlite3
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

FORMULA_LICENSE = "LicenseRef-user-supplied-unstated"
FORMULA_MOUNT = "/opt/tcms/data/repository-formulas.sqlite"
FORMULA_COLUMNS = ("name", "composition", "source", "preparation", "actions", "usage", "cautions")
_HEADERS = ("名称", "配方", "出处", "炮制", "功效", "使用方法", "注意")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_LOCK = threading.Lock()
_LOCAL: tuple[str, Path] | None = None


def workbook_path() -> Path | None:
    from bioagent.sources.formulas import TABLE_FILE
    path = Path(os.environ.get("BIOAGENT_FORMULA_WORKBOOK") or TABLE_FILE)
    return path if path.is_file() else None


def workbook_rows(path: Path) -> Iterator[list[str]]:
    """Stream XLSX values without an optional build-time dependency."""
    with zipfile.ZipFile(path) as archive:
        strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as stream:
                for _, node in ET.iterparse(stream, events=("end",)):
                    if node.tag == _NS + "si":
                        strings.append("".join(t.text or "" for t in node.iter(_NS + "t")))
                        node.clear()
        with archive.open("xl/worksheets/sheet1.xml") as stream:
            for _, node in ET.iterparse(stream, events=("end",)):
                if node.tag != _NS + "row":
                    continue
                cells = [""] * 7
                for cell in node.findall(_NS + "c"):
                    letters = re.sub(r"[^A-Z]", "", cell.get("r", ""))
                    column = 0
                    for char in letters:
                        column = column * 26 + ord(char) - ord("A") + 1
                    if not 1 <= column <= 7:
                        continue
                    value = cell.find(_NS + "v")
                    text = value.text or "" if value is not None else ""
                    if cell.get("t") == "s" and text:
                        text = strings[int(text)]
                    elif cell.get("t") == "inlineStr":
                        text = "".join(t.text or "" for t in cell.iter(_NS + "t"))
                    cells[column - 1] = text.strip()
                yield cells
                node.clear()


def build_formula_store(workbook: Path, destination: Path) -> dict[str, Any]:
    """Keep all seven original columns; index names; record source digests and row count."""
    from bioagent.sources.formulas import _clean_name

    digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
    rows = workbook_rows(workbook)
    header = next(rows)
    if tuple(header) != _HEADERS:
        raise ValueError(f"unexpected formula workbook columns: {header}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    count = 0
    with sqlite3.connect(destination) as conn:
        conn.execute("PRAGMA page_size=4096")
        conn.execute("CREATE TABLE formulas (row INTEGER PRIMARY KEY, name TEXT, "
                     "composition TEXT, source TEXT, preparation TEXT, actions TEXT, "
                     "usage TEXT, cautions TEXT, clean_name TEXT)")
        batch = []
        for row_number, values in enumerate(rows, start=2):
            if not values[0] and not values[1]:
                continue
            batch.append((row_number, *values, _clean_name(values[0])))
            count += 1
            if len(batch) == 2000:
                conn.executemany("INSERT INTO formulas VALUES (?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT INTO formulas VALUES (?,?,?,?,?,?,?,?,?)", batch)
        conn.execute("CREATE INDEX formula_names ON formulas(clean_name)")
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("source_name", workbook.name), ("source_sha256", digest),
            ("license", FORMULA_LICENSE), ("rows", str(count))])
    return {"rows": count, "source_sha256": digest, "license": FORMULA_LICENSE,
            "source_name": workbook.name}


def formula_store() -> Path:
    explicit = os.environ.get("BIOAGENT_FORMULA_SQLITE")
    path = Path(explicit or FORMULA_MOUNT)
    if path.is_file():
        return path
    if explicit:
        raise FileNotFoundError(f"formula store is not loaded: {path}")
    workbook = workbook_path()
    if workbook is None:
        raise FileNotFoundError("the full formula asset is not loaded; load the repository "
                                "formula asset or set BIOAGENT_FORMULA_WORKBOOK")
    global _LOCAL
    stamp = f"{workbook}:{workbook.stat().st_mtime_ns}:{workbook.stat().st_size}"
    with _LOCK:
        if _LOCAL is None or _LOCAL[0] != stamp:
            folder = tempfile.TemporaryDirectory(prefix="tcmstudio-formulas-")
            atexit.register(folder.cleanup)
            store = Path(folder.name) / "formulas.sqlite"
            build_formula_store(workbook, store)
            _LOCAL = stamp, store
        return _LOCAL[1]


def formula_search(query: str = "", *, exact: bool = False, source: str = "",
                   limit: int = 25, offset: int = 0) -> dict[str, Any]:
    """Search every formula row, retain duplicate names/versions and paginate explicitly."""
    from bioagent.sources.formulas import FormulaRecord, _clean_name, parse_composition

    path = formula_store()
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        clauses, params = [], []
        if query:
            if exact:
                clauses.append("(clean_name = ? OR name = ?)")
                params.extend((_clean_name(query.strip()), query.strip()))
            else:
                clauses.append("(" + " OR ".join(f"instr(lower({c}), lower(?)) > 0"
                                                  for c in FORMULA_COLUMNS) + ")")
                params.extend([query.strip()] * len(FORMULA_COLUMNS))
        if source:
            clauses.append("instr(lower(source), lower(?)) > 0")
            params.append(source)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = conn.execute("SELECT count(*) FROM formulas" + where, params).fetchone()[0]
        rows = conn.execute("SELECT * FROM formulas" + where + " ORDER BY row LIMIT ? OFFSET ?",
                            [*params, limit, offset]).fetchall()
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        records = []
        for row in rows:
            rec = FormulaRecord(row["row"], row["name"], row["clean_name"], row["source"],
                                row["composition"], parse_composition(row["composition"]),
                                row["preparation"], row["actions"], row["usage"], row["cautions"])
            records.append({"id": rec.id, "row": rec.row, "name": rec.name,
                            "written_name": rec.written_name, "source": rec.source,
                            **{c: row[c] for c in FORMULA_COLUMNS if c != "name"},
                            "components": [asdict(c) for c in rec.components],
                            "resolved": rec.resolved, "unresolved": list(rec.unresolved),
                            "license": metadata["license"], "clinical_validation": "not assessed"})
        return {"query": query, "count": len(records), "total": total, "offset": offset,
                "next_offset": offset + len(records) if offset + len(records) < total else None,
                "corpus_rows": int(metadata["rows"]), "records": records,
                "provenance": metadata, "claim_scope": "source formula records"}
    finally:
        conn.close()


def materia_search(query: str = "", *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    from bioagent.sources.materia import MATERIA
    needle = query.casefold().strip()
    records = [asdict(entry) for entry in MATERIA.values()
               if not needle or needle in " ".join((entry.id, entry.chinese, entry.latin,
                                                     *entry.aliases, *entry.species_names)).casefold()]
    return {"count": len(records[offset:offset + limit]), "total": len(records),
            "corpus_rows": len(MATERIA), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(records) else None,
            "records": records[offset:offset + limit],
            "claim_scope": "materia identity and recorded species; clinical properties not inferred"}


def provider_catalog(query: str = "", *, project: str = "", kind: str = "",
                     limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Full third-party index plus reviewed wrapper admission and actual Studio reachability."""
    from bioagent.psh.assembly import load_catalogue_rows
    from bioagent.providers.tooluniverse import load_allowlist, review
    from bioagent.providers.biomcp import check_installed, load_server_config
    rows = load_catalogue_rows()
    needle = query.casefold().strip()
    filtered = [row for row in rows
                if (not needle or needle in " ".join(row.values()).casefold())
                and (not project or project.casefold() in row.get("contributing_projects", "").casefold())
                and (not kind or kind == row.get("kind"))]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("kind", "unknown")] = counts.get(row.get("kind", "unknown"), 0) + 1
    allow = load_allowlist()
    checked = {entry["name"]: entry for entry in review(allow)}
    tooluniverse = [{"name": tool.name, "component_id": tool.component_id,
                    "hosts": list(tool.hosts), "description": tool.purpose,
                    "data_terms": tool.data_terms, "reviewed_version": allow.reviewed_version,
                    "package_state": checked.get(tool.name, {}),
                    "studio_available": bool(checked.get(tool.name, {}).get("installed"))
                                        and not checked.get(tool.name, {}).get("drift"), "exec": ["runner"],
                    "studio_entry": "system.provider_call",
                    "reason": "Requires the reviewed ToolUniverse package on the runner; package drift or missing dependencies are refused."}
                   for tool in allow.tools]
    server = load_server_config()
    biomcp_missing = check_installed(server)
    biomcp = [{"name": tool.name, "hosts": list(tool.hosts), "record_kind": tool.record_kind,
               "schema_sha256": tool.input_schema_sha256, "notes": tool.notes,
               "studio_available": server.status == "reviewed" and not biomcp_missing,
               "missing": biomcp_missing, "exec": ["runner"],
               "studio_entry": "system.provider_call", "configuration": server.status,
               "reason": "Requires the pinned BioMCP package and a reviewed server configuration on the runner; draft configuration or schema drift is refused."}
              for tool in server.tools.values()]
    return {"corpus_rows": len(rows), "total": len(filtered), "count": len(filtered[offset:offset + limit]),
            "offset": offset, "next_offset": offset + limit if offset + limit < len(filtered) else None,
            "by_kind": counts, "entries": [{**row, "studio_status": "indexed",
                                               "execution_note": "Use a registered connector or reviewed provider_call binding; an index row alone has no Studio execution binding."}
                                              for row in filtered[offset:offset + limit]],
            "reviewed_wrappers": {"tooluniverse": tooluniverse, "biomcp": biomcp,
                                  "biomcp_not_admitted": dict(server.not_admitted)},
            "claim_scope": "Capability index; catalogue availability is not executable availability."}
