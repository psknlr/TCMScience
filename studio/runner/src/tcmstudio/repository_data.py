"""The repository's complete formula workbook and materia records, with provenance.

Formula rows are source records, never clinical recommendations. The workbook does
not state a data licence; its licence is retained verbatim and distinct from MIT code.
The browser loads the compressed, indexed store only when a lookup needs it.
"""

from __future__ import annotations

import atexit
import functools
import hashlib
import math
import os
import re
import sqlite3
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator, Mapping

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


#: Set when a lookup wanted the complete workbook and it was not there. The browser's Worker
#: reads it after a call (``take_formula_store_wanted``), loads the asset and runs the call
#: again, so a lookup that only sometimes needs the workbook does not download it every time.
_WANTED = False


def take_formula_store_wanted() -> bool:
    """Whether a lookup since the last call wanted the missing workbook; clears the flag."""
    global _WANTED
    wanted, _WANTED = _WANTED, False
    return wanted


def formula_store() -> Path:
    global _WANTED
    explicit = os.environ.get("BIOAGENT_FORMULA_SQLITE")
    path = Path(explicit or FORMULA_MOUNT)
    if path.is_file():
        return path
    if explicit:
        _WANTED = True
        raise FileNotFoundError(f"formula store is not loaded: {path}")
    workbook = workbook_path()
    if workbook is None:
        _WANTED = True
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


def _record(row: sqlite3.Row, metadata: Mapping[str, str]) -> dict[str, Any]:
    from bioagent.sources.formulas import FormulaRecord, parse_composition

    rec = FormulaRecord(row["row"], row["name"], row["clean_name"], row["source"],
                        row["composition"], parse_composition(row["composition"]),
                        row["preparation"], row["actions"], row["usage"], row["cautions"])
    return {"id": rec.id, "row": rec.row, "name": rec.name,
            "written_name": rec.written_name, "source": rec.source,
            **{c: row[c] for c in FORMULA_COLUMNS if c != "name"},
            "components": [asdict(c) for c in rec.components],
            "resolved": rec.resolved, "unresolved": list(rec.unresolved),
            "license": metadata["license"], "clinical_validation": "not assessed"}


def _open_store() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{formula_store().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def formula_search(query: str = "", *, exact: bool = False, source: str = "",
                   limit: int = 25, offset: int = 0, herb: str = "",
                   indication: str = "") -> dict[str, Any]:
    """Search every formula row, retain duplicate names/versions and paginate explicitly.
    ``herb`` keeps the rows whose composition names that herb (``herb_plan``); they come
    with the formulas named after it first, then in workbook order. ``indication`` keeps
    the rows whose 功效 text mentions any of its terms (``_indication_clause``)."""
    from bioagent.sources.formulas import _clean_name

    conn = _open_store()
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
        plan, order, order_params = None, "row", []
        if herb.strip():
            plan = herb_plan(herb.strip())
            clause, ps = _herb_clause(plan, "composition")
            clauses.append(clause)
            params.extend(ps)
            named, order_params = _herb_clause(plan, "name")
            order = f"{named} DESC, row"
        indications = _indication_terms(indication)
        if indications:
            clause, ps = _indication_clause(indications)
            clauses.append(clause)
            params.extend(ps)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        total = conn.execute("SELECT count(*) FROM formulas" + where, params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM formulas{where} ORDER BY {order} LIMIT ? OFFSET ?",
                            [*params, *order_params, limit, offset]).fetchall()
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        records = [_record(row, metadata) for row in rows]
        out = {"query": query, "count": len(records), "total": total, "offset": offset,
               "next_offset": offset + len(records) if offset + len(records) < total else None,
               "corpus_rows": int(metadata["rows"]), "records": records,
               "provenance": metadata, "claim_scope": "source formula records"}
        if plan is not None:
            out["herb"] = _plan_summary(plan)
        if indications:
            out["indication"] = _indication_summary(indications)
        if query and not exact:
            # a herb the workbook also writes otherwise (黄芪 / 黄耆): say so, without changing the search
            also = search_variants(query.strip())
            if len(also["search"]) > 1:
                out["also_written"] = {"materia": also["materia"], "names": also["search"][1:],
                                       "hint": "rank: true searches these names too; herb: finds "
                                               "the formulas whose composition names it"}
        return out
    finally:
        conn.close()


# ------------------------------------------------------------------- ranked search

#: Terms are separated by spaces or by these marks: 黄芪 当归, 黄芪、当归, 黄芪,当归.
_TERM_SPLIT = re.compile(r"[\s\u3000,，、;；/|+]+")
MAX_TERMS = 12
#: A term's weight in a row is that of the best field it occurs in.
FIELD_WEIGHTS = {"exact_name": 6, "name": 4, "composition": 3, "actions": 2, "other": 1}
_OTHER_FIELDS = ("source", "preparation", "usage", "cautions")
_FIELD_OF_WEIGHT = {w: f for f, w in FIELD_WEIGHTS.items()}
RANK_METHOD = ("score = sum over terms of idf(term) x the weight of the best field the term (or a "
               "name it is searched under) occurs in: the formula's exact name 6, its name 4, "
               "composition 3, actions 2, source, preparation, usage or cautions 1; "
               "idf = ln(1 + (N - df + 0.5) / (df + 0.5)) over the N rows searched; "
               "ties in workbook order. A lexical relevance order, not a clinical one.")


def query_terms(query: str) -> list[str]:
    """The distinct terms of a query, in order."""
    return list(dict.fromkeys(t for t in _TERM_SPLIT.split(query or "") if t))


# The materia table is read here and never extended: bioagent.sources.materia is part of the
# reviewed code of the pinned TCM skills (their content hash covers it), so a helper added
# there would move their pins and their governed runs would be refused until re-reviewed.

@functools.lru_cache(maxsize=1)
def _names_by_drug() -> dict[str, tuple[str, ...]]:
    """Every name the materia table gives each drug: its Chinese name, its aliases, then the
    reviewed aliases (川当归 …), without repeats."""
    from bioagent.sources import materia

    names: dict[str, list[str]] = {d: [e.chinese, *e.aliases] for d, e in materia.MATERIA.items()}
    for name, drug in materia._REVIEWED_ALIASES.items():
        names[drug].append(name)
    return {d: tuple(dict.fromkeys(ns)) for d, ns in names.items()}


def _drug_named(raw: str) -> str | None:
    """The drug whose name ``raw`` is, exactly (after removing notes and spaces and
    simplifying traditional characters); unlike ``materia.resolve_name``, no processing
    word is stripped."""
    from bioagent.sources import materia

    name = materia.normalise_name(materia.to_simplified(raw))
    return materia._NAME_INDEX.get(name) if name else None


def _processed_base(name: str, drug: str) -> str | None:
    """The name of ``drug`` that ``name`` is a processed form of (炙甘草 → 甘草, 甘草末 →
    甘草, 姜汁 → 姜), by the materia table's own processing words; a drug's Chinese name is
    never one (生姜 is a drug of its own)."""
    from bioagent.sources import materia

    if name == materia.MATERIA[drug].chinese:
        return None
    known = set(_names_by_drug()[drug])
    for p in materia._PREFIXES:
        if name.startswith(p) and name[len(p):] in known:
            return name[len(p):]
    for x in materia._SUFFIXES:
        if name.endswith(x) and name[: -len(x)] in known:
            return name[: -len(x)]
    return None


def search_variants(term: str, aliases: bool = True) -> dict[str, Any]:
    """What a search for ``term`` looks for: the term itself and, when it is a drug's name
    in the materia table (and ``aliases``), the drug's other names (黄芪 → 黄耆; 甘草 → 粉草,
    国老, 炙草). Only the fewest strings that find them all are kept, since a substring
    search for 黄芪 already finds 炙黄芪. A processed form asked for (炙甘草) is searched as
    it is, never widened to the plain drug; processed forms are not added either (姜汁 is
    how 黄连（姜汁炒）is prepared, not an ingredient). One-letter names (归, 姜) and names
    that are part of another drug's name are left out: they would find other drugs."""
    out: dict[str, Any] = {"term": term, "search": [term]}
    if not aliases or not term:
        return out
    from bioagent.sources import materia

    drug = _drug_named(term)
    if drug is None:
        return out
    out["materia"] = drug
    base = _processed_base(materia.normalise_name(materia.to_simplified(term)), drug)
    if base is not None:
        out["processed_form_of"] = base
        return out
    table = _names_by_drug()
    others = [name for d, names in table.items() if d != drug for name in names]
    names = [n for n in table[drug]
             if n != term and len(n) >= 2 and not any(n in other for other in others)
             and _processed_base(n, drug) is None]
    keep = [term]
    for n in sorted(names, key=lambda x: (len(x), x)):
        if not any(k in n for k in keep):
            keep.append(n)
    out["search"] = [term] + [n for n in table[drug] if n in keep and n != term]
    return out


# ------------------------------------------------------------ formulas naming a herb

def herb_plan(herb: str) -> dict[str, Any]:
    """How the formulas whose composition names ``herb`` are found: under the names
    ``search_variants`` gives it (黄芪 also as 黄耆), each matched only where it is not part
    of another drug's name: the names of other drugs that contain it are blanked out of the
    text first, so 茯苓 does not find 土茯苓. A name the materia table does not know is
    searched as written."""
    plan = search_variants(herb)
    drug = plan.get("materia")
    table = _names_by_drug()
    needles = []
    for name in plan["search"]:
        near = {n for d, names in table.items() if d != drug for n in names
                if name in n and n != name} if drug else set()
        needles.append({"name": name, "not_within": sorted(near, key=lambda x: (-len(x), x))})
    return {**plan, "needles": needles}


def _herb_clause(plan: Mapping[str, Any], column: str) -> tuple[str, list[str]]:
    parts, params = [], []
    for needle in plan["needles"]:
        expr = column
        for other in needle["not_within"]:
            expr = f"replace({expr}, ?, '|')"
            params.append(other)
        parts.append(f"instr(lower({expr}), lower(?)) > 0" if _has_ascii_letter(needle["name"])
                     else f"instr({expr}, ?) > 0")
        params.append(needle["name"])
    return "(" + " OR ".join(parts) + ")", params


def _plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    out = {"term": plan["term"], "searched": list(plan["search"]),
           "field": "composition",
           "not_within": {n["name"]: n["not_within"] for n in plan["needles"] if n["not_within"]}}
    for key in ("materia", "processed_form_of"):
        if key in plan:
            out[key] = plan[key]
    return out


HERB_FORMULAS_SCOPE = ("workbook source records whose composition names the herb (its other "
                       "names included, other drugs whose names contain it excluded): not "
                       "curated roles, doses or indications")


def herb_formulas(herb: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """The workbook formulas whose composition names ``herb``, compact (row, name, source,
    composition), formulas named after it first; the whole list pages through
    tcmdb.formulas with the same ``herb``."""
    found = formula_search("", herb=herb, limit=limit, offset=offset)
    records = [{"id": r["id"], "row": r["row"], "name": r["name"], "source": r["source"],
                "composition": r["composition"]} for r in found["records"]]
    out = {"herb": found["herb"], "total": found["total"], "count": len(records),
           "offset": offset, "next_offset": found["next_offset"],
           "corpus_rows": found["corpus_rows"], "records": records,
           "scope": HERB_FORMULAS_SCOPE, "provenance": found["provenance"]}
    if found["next_offset"] is not None:
        out["more"] = {"tool": "tcmdb.formulas",
                       "arguments": {"herb": herb, "offset": found["next_offset"]}}
    return out


# ------------------------------------------------------------ formulas for an indication

#: The workbook's 功效 column (``actions``) records what a formula is for. Its 注意 column
#: (``cautions``) records whom it is not for (气虚人减量服), so a term there is never taken
#: for an indication.
INDICATION_FIELD = "actions"


def _indication_terms(text: str) -> list[str]:
    terms = query_terms(text)
    if len(terms) > MAX_TERMS:
        raise ValueError(f"indication takes at most {MAX_TERMS} terms, not {len(terms)}")
    return terms


def _indication_clause(terms: list[str]) -> tuple[str, list[str]]:
    """Rows whose 功效 text contains any of ``terms``, as written."""
    parts = [f"instr(lower({INDICATION_FIELD}), lower(?)) > 0" if _has_ascii_letter(t)
             else f"instr({INDICATION_FIELD}, ?) > 0" for t in terms]
    return "(" + " OR ".join(parts) + ")", list(terms)


def _indication_summary(terms: list[str]) -> dict[str, Any]:
    return {"terms": list(terms), "field": f"{INDICATION_FIELD} (功效)",
            "not_searched": "cautions (注意): a term there says whom a formula is not for"}


def syndrome_terms(names: Any) -> list[str]:
    """How a syndrome is written in the workbook's 功效 text, which is Chinese: each of its
    Chinese names without the closing 证 (脾胃气虚证 → 脾胃气虚), of two characters at least."""
    out: list[str] = []
    for name in names or ():
        term = "".join(str(name or "").split())
        if term.endswith("证"):
            term = term[:-1]
        if len(term) >= 2 and term not in out and any("一" <= c <= "鿿" for c in term):
            out.append(term)
    return out


INDICATION_FORMULAS_SCOPE = ("workbook source records whose recorded 功效 text mentions the "
                             "term as written (never its 注意 cautions): not a curated "
                             "syndrome-formula link, a diagnosis or a recommendation")


def indication_formulas(terms: list[str], *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
    """The workbook formulas whose 功效 text mentions any of ``terms``, compact (row, name,
    source, actions), in workbook order; the whole list pages through tcmdb.formulas with
    the same ``indication``."""
    joined = " ".join(terms)
    found = formula_search("", indication=joined, limit=limit, offset=offset)
    records = [{"id": r["id"], "row": r["row"], "name": r["name"], "source": r["source"],
                "actions": r["actions"]} for r in found["records"]]
    out = {"indication": found["indication"], "total": found["total"], "count": len(records),
           "offset": offset, "next_offset": found["next_offset"],
           "corpus_rows": found["corpus_rows"], "records": records,
           "scope": INDICATION_FORMULAS_SCOPE, "provenance": found["provenance"]}
    if found["next_offset"] is not None:
        out["more"] = {"tool": "tcmdb.formulas",
                       "arguments": {"indication": joined, "offset": found["next_offset"]}}
    return out


def formula_corpus_status() -> dict[str, Any]:
    """Which formula corpus the lookups here can use, without building or loading it: the
    complete workbook (loaded, or indexed on first use) or, when it is missing, why and how
    to supply it. The curated seed corpus is always there."""
    seed = {"formulas": _seed_formulas(), "scope": "curated: roles, indications, safety"}
    fix = ("in the browser it loads with the first formula, herb or syndrome lookup; on a runner, set "
           "BIOAGENT_FORMULA_WORKBOOK to the repository's 中医方剂数据表.xlsx (or "
           "BIOAGENT_FORMULA_SQLITE to a built store)")
    # the same order formula_store() follows
    explicit = os.environ.get("BIOAGENT_FORMULA_SQLITE")
    path = Path(explicit or FORMULA_MOUNT)
    if path.is_file():
        return {"seed": seed, "workbook": {"status": "loaded", "rows": _store_rows(path)}}
    if explicit:
        return {"seed": seed, "workbook": {"status": "unavailable",
                                           "reason": f"BIOAGENT_FORMULA_SQLITE names no file: {path}",
                                           "fix": fix}}
    workbook = workbook_path()
    if workbook is not None:
        built = _LOCAL is not None and _LOCAL[1].is_file()
        return {"seed": seed, "workbook": {
            "status": "loaded" if built else "available", "source": workbook.name,
            **({"rows": _store_rows(_LOCAL[1])} if built else {"note": "indexed on first use"})}}
    return {"seed": seed, "workbook": {"status": "unavailable",
                                       "reason": "the complete formula workbook is not loaded here",
                                       "fix": fix}}


def _store_rows(path: Path) -> int | None:
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            return int(dict(conn.execute("SELECT key, value FROM metadata"))["rows"])
        finally:
            conn.close()
    except Exception:                                           # noqa: BLE001
        return None


def _seed_formulas() -> int | None:
    try:
        from bioagent.tcm.knowledge import default_knowledge
        return len(default_knowledge().formulas)
    except Exception:                                           # noqa: BLE001
        return None


def _has_ascii_letter(text: str) -> bool:
    return any("a" <= c <= "z" or "A" <= c <= "Z" for c in text)


def _matches(column: str, needles: list[str]) -> tuple[str, list[str]]:
    # lower() only where it can change something: it copies every value it reads
    parts = [f"instr(lower({column}), lower(?)) > 0" if _has_ascii_letter(n) else f"instr({column}, ?) > 0"
             for n in needles]
    return "(" + " OR ".join(parts) + ")", list(needles)


def ranked_formula_search(query: str, *, source: str = "", aliases: bool = True,
                          limit: int = 25, offset: int = 0, herb: str = "",
                          indication: str = "") -> dict[str, Any]:
    """Rows matching any term of ``query``, most relevant first (``RANK_METHOD``), each
    with how it matched; herb names also under their other names (``search_variants``)."""
    from bioagent.sources.formulas import _clean_name

    terms = query_terms(query)
    if not terms:
        raise ValueError("a ranked search needs at least one term")
    if len(terms) > MAX_TERMS:
        raise ValueError(f"a ranked search takes at most {MAX_TERMS} terms, not {len(terms)}")
    plans = [search_variants(t, aliases) for t in terms]
    cases, params = [], []
    for i, plan in enumerate(plans):
        sql, ps = ["CASE WHEN (clean_name = ? OR name = ?) THEN 6"], [_clean_name(plan["term"]), plan["term"]]
        for column, weight in (("name", 4), ("composition", 3), ("actions", 2)):
            m, mp = _matches(column, plan["search"])
            sql.append(f"WHEN {m} THEN {weight}")
            ps += mp
        others = [_matches(c, plan["search"]) for c in _OTHER_FIELDS]
        sql.append("WHEN " + " OR ".join(m for m, _ in others) + " THEN 1 ELSE 0 END AS w" + str(i))
        ps += [v for _, mp in others for v in mp]
        cases.append(" ".join(sql))
        params += ps
    filters, where_params = [], []
    if source:
        filters.append("instr(lower(source), lower(?)) > 0")
        where_params.append(source)
    herb_scope = None
    if herb.strip():
        herb_scope = herb_plan(herb.strip())
        clause, ps = _herb_clause(herb_scope, "composition")
        filters.append(clause)
        where_params.extend(ps)
    indications = _indication_terms(indication)
    if indications:
        clause, ps = _indication_clause(indications)
        filters.append(clause)
        where_params.extend(ps)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    weights = ", ".join(f"w{i}" for i in range(len(plans)))
    sql = (f"SELECT row, {weights} FROM (SELECT row, {', '.join(cases)} FROM formulas{where}) "
           f"WHERE {' + '.join(f'w{i}' for i in range(len(plans)))} > 0")
    conn = _open_store()
    try:
        searched = conn.execute("SELECT count(*) FROM formulas" + where, where_params).fetchone()[0]
        hits = conn.execute(sql, [*params, *where_params]).fetchall()
        df = [sum(1 for h in hits if h[1 + i] > 0) for i in range(len(plans))]
        idf = [math.log(1 + (searched - d + 0.5) / (d + 0.5)) for d in df]
        scored = []
        for h in hits:
            score = 0.0
            for i in range(len(plans)):
                if h[1 + i]:
                    score += idf[i] * h[1 + i]
            scored.append((-score, h[0], h))
        scored.sort()
        page = scored[offset:offset + limit]
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        rows = {r["row"]: r for r in conn.execute(
            f"SELECT * FROM formulas WHERE row IN ({', '.join('?' * len(page))})",
            [h[1] for h in page])} if page else {}
        records = []
        for neg, row_id, h in page:
            rec = _record(rows[row_id], metadata)
            rec["match"] = {"score": round(-neg, 6),
                            "fields": {plans[i]["term"]: _FIELD_OF_WEIGHT[h[1 + i]]
                                       for i in range(len(plans)) if h[1 + i]}}
            records.append(rec)
        total = len(scored)
        return {"query": query, "count": len(records), "total": total, "offset": offset,
                "next_offset": offset + len(records) if offset + len(records) < total else None,
                "corpus_rows": int(metadata["rows"]), "records": records,
                "rank": {"method": RANK_METHOD, "aliases": aliases, "rows_searched": searched,
                         "terms": [{**plan, "rows": df[i], "idf": round(idf[i], 6)}
                                   for i, plan in enumerate(plans)]},
                **({"herb": _plan_summary(herb_scope)} if herb_scope else {}),
                **({"indication": _indication_summary(indications)} if indications else {}),
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
