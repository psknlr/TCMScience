"""Per-entity enrichment from the SymMap and HERB site query endpoints.

SymMap's and HERB's downloads hold entity tables only. Their relations (a herb's targets,
an ingredient's targets with the upstream source of each pair, literature-reported
targets with a grade and a supporting sentence) are served one entity at a time by the
form and JSON posts the sites' own pages send. Following the repository owner's decision
(2026-10-02), the hub calls them through the governed connectors ``symmap`` and
``herb_api``:

* **per entity, on request**: ``hub.enrich("黄芪")`` or ``hub.enrich_herb([...])``. There
  is no crawl of the whole database;
* **cached**: each response is kept under ``raw/<dataset>/`` with its fetch time and is
  never requested again unless ``refresh=True``;
* **at one request per second** (the HTTP backend's per-host limit).

``hub.build("symmap_api")`` / ``hub.build("herb_api")`` load the cached responses into a
store and extract relations like any other dataset. The evidence label follows what each
row is:

* rows SymMap or HERB *infer* through their network (a herb's targets and diseases,
  with an IES value or a P value and FDR) are ``predicted``;
* rows backed by a paper are labelled by what the paper does. SymMap's evidence is an
  abstract that names both entities, which makes the row ``mentioned`` (a text
  co-mention, not a measurement). HERB's are graded sentences that state the relation,
  which makes the row ``reported``. Both cite the PubMed id;
* rows integrated from other databases are ``aggregated``. HERB names the upstream source
  of each ingredient-target pair, and that source is kept in ``note``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

__all__ = ["SYMMAP_RELATED", "HERB_LABELS", "cache_path", "fetch_symmap", "fetch_herb",
           "build_live_store", "symmap_relations", "herb_relations"]

SYMMAP_RELATED = ("Mol", "Gene", "TCM_symptom", "MM_symptom", "Disease", "Syndrome")
HERB_LABELS = ("Herb", "Ingredient", "Formula", "Target", "Disease")
_SAFE = re.compile(r"[^0-9A-Za-z_.-]+")
_PMID = re.compile(r"PMID:\s*(\d+)")


def cache_path(raw_dir: Path, entity_id: str, part: str) -> Path:
    return Path(raw_dir) / f"{_SAFE.sub('_', entity_id)}__{_SAFE.sub('_', part)}.json"


def _fetch(hub: Any, connector: str, operation: str, path: Path, request: dict[str, Any],
           *, entity_name: str | None, refresh: bool, log) -> dict[str, Any]:
    if path.exists() and not refresh:
        return {"file": path.name, "cached": True, "ok": True}
    result = hub.live(connector, operation, **request)
    status = getattr(result.status, "value", str(result.status))
    if status not in ("SUCCEEDED", "DEGRADED") or not isinstance(result.value, (dict, list)):
        log(f"{connector} {request}: {status} {result.error or ''}")
        return {"file": path.name, "cached": False, "ok": False,
                "error": result.error or status}
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"connector": connector, "operation": operation, "request": request,
              "entity_name": entity_name,
              "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "value": result.value}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    log(f"{connector}: {path.name}")
    return {"file": path.name, "cached": False, "ok": True}


def fetch_symmap(hub: Any, entity_ids: Iterable[str], *, related: Sequence[str] = SYMMAP_RELATED,
                 names: dict[str, str] | None = None, refresh: bool = False,
                 log=print) -> list[dict[str, Any]]:
    raw = hub.raw_dir("symmap_api")
    out = []
    for entity in entity_ids:
        for part in related:
            if part not in SYMMAP_RELATED and part != "Herb":
                raise ValueError(f"SymMap related type {part!r}; have {SYMMAP_RELATED + ('Herb',)}")
            out.append(_fetch(hub, "symmap", "related", cache_path(raw, entity, part),
                              {"entity_id": entity, "related": part, "filter": 0},
                              entity_name=(names or {}).get(entity), refresh=refresh, log=log))
    return out


def fetch_herb(hub: Any, entity_ids: Iterable[str], *, label: str = "Herb",
               names: dict[str, str] | None = None, refresh: bool = False,
               log=print) -> list[dict[str, Any]]:
    if label not in HERB_LABELS:
        raise ValueError(f"HERB label {label!r}; have {HERB_LABELS}")
    raw = hub.raw_dir("herb_api")
    return [_fetch(hub, "herb_api", "detail", cache_path(raw, entity, label),
                   {"entity_id": entity, "label": label},
                   entity_name=(names or {}).get(entity), refresh=refresh, log=log)
            for entity in entity_ids]


def build_live_store(raw_dir: Path, db_path: Path, *, license: str) -> dict[str, Any]:
    """Cached responses -> ``response`` table (one row each), written atomically."""
    raw_dir, db_path = Path(raw_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.execute("CREATE TABLE response (entity_id TEXT, part TEXT, entity_name TEXT, "
                     "fetched_at TEXT, body TEXT)")
        conn.execute("CREATE TABLE _tcmdb_files (tbl TEXT, file TEXT, url TEXT, bytes INTEGER,"
                     " sha256 TEXT, rows INTEGER, columns TEXT, license TEXT, loaded_at TEXT)")
        n = 0
        for path in sorted(raw_dir.glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            req = rec.get("request") or {}
            part = req.get("related") or req.get("label") or ""
            conn.execute("INSERT INTO response VALUES (?,?,?,?,?)",
                         (req.get("entity_id"), part, rec.get("entity_name"),
                          rec.get("fetched_at"), json.dumps(rec.get("value"), ensure_ascii=False)))
            n += 1
        conn.execute("INSERT INTO _tcmdb_files VALUES (?,?,?,?,?,?,?,?,?)",
                     ("response", f"{n} cached responses", "", 0, "", n, "[]", license,
                      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        conn.commit()
    except Exception:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    tmp.replace(db_path)
    return {"tables": {"response": n}, "missing": [], "optional_absent": [], "skipped": []}


# ----------------------------------------------------------------------------- helpers
def _text(cell: Any) -> str | None:
    """A table cell: plain text, or a link object's title."""
    if cell is None:
        return None
    if isinstance(cell, dict):
        cell = cell.get("title")
    text = str(cell).strip()
    return None if text in ("", "NA", "None", "nan") else text


def _pmids(html: Any) -> list[str]:
    return _PMID.findall(str(html or ""))


def _table(rows: Any) -> Iterator[dict[str, Any]]:
    """HERB's ``[[header...], [row...], ...]`` tables as dicts (raw cells kept)."""
    if not isinstance(rows, list) or len(rows) < 2 or not isinstance(rows[0], list):
        return
    header = [str(h) for h in rows[0]]
    for row in rows[1:]:
        if isinstance(row, list) and row:
            yield dict(zip(header, row))


def _responses(conn: sqlite3.Connection) -> Iterator[tuple[str, str, str | None, Any]]:
    for entity, part, name, body in conn.execute(
            "SELECT entity_id, part, entity_name, body FROM response"):
        try:
            yield entity, part, name, json.loads(body)
        except json.JSONDecodeError:
            continue


# ------------------------------------------------------------------------------ SymMap
def symmap_relations(conn: sqlite3.Connection, rel) -> Iterator[dict | None]:
    """Relations from cached SymMap ``related_components`` responses (``rel`` builds a row)."""
    for entity, part, name, value in _responses(conn):
        rows = (value or {}).get("data") or [] if isinstance(value, dict) else []
        subject = f"symmap:{entity}"
        herb_subject = entity.startswith("SMHB")
        for r in rows:
            pmids = _pmids(r.get("evidence"))
            if part == "Mol" and herb_subject:
                cid = str(r.get("PubChem_CID") or "").split("|")[0].strip()
                obj = f"pubchem:{cid}" if cid.isdigit() else f"symmap:{r.get('MOL_id')}"
                for ref in (pmids or [None]):
                    # SymMap's evidence is an abstract that names both (text co-mention)
                    yield rel("herb_ingredient", "symmap_api", subject, name, obj,
                              r.get("Molecule_name"), "mentioned" if ref else "aggregated",
                              reference=f"pmid:{ref}" if ref else None,
                              note=f"symmap:{r.get('MOL_id')}; TCMSP {r.get('TCMSP_id')}")
            elif part == "Gene" and herb_subject:
                yield rel("herb_target", "symmap_api", subject, name,
                          f"symbol:{r.get('Gene_symbol')}" if r.get("Gene_symbol") else None,
                          r.get("Gene_name"), "predicted", score=r.get("Value"),
                          note=f"IES; P={r.get('P_value')}; FDR(BH)={r.get('FDR(BH)')}; "
                               f"{r.get('Relationship')}")
            elif part == "Gene":                      # an ingredient's targets
                for ref in (pmids or [None]):
                    yield rel("ingredient_target", "symmap_api", subject, name,
                              f"symbol:{r.get('Gene_symbol')}" if r.get("Gene_symbol") else None,
                              r.get("Gene_name"), "mentioned" if ref else "aggregated",
                              score=r.get("score"), reference=f"pmid:{ref}" if ref else None)
            elif part == "TCM_symptom" and herb_subject:
                yield rel("herb_symptom", "symmap_api", subject, name,
                          f"symmap:{r.get('TCM_symptom_id')}", r.get("TCM_symptom_name"),
                          "listed", note=r.get("Type"), object_type="tcm_symptom")
            elif part == "MM_symptom" and herb_subject:
                yield rel("herb_symptom", "symmap_api", subject, name,
                          f"umls:{r['UMLS_id']}" if r.get("UMLS_id") else
                          f"symmap:{r.get('MM_symptom_id')}", r.get("MM_symptom_name"),
                          "predicted", score=r.get("Value"), object_type="mm_symptom",
                          note=f"IES; P={r.get('P_value')}; FDR(BH)={r.get('FDR(BH)')}; "
                               f"{r.get('Relationship')}")
            elif part == "Disease" and herb_subject:
                yield rel("herb_disease", "symmap_api", subject, name,
                          f"symmap:{r.get('Disease_id')}", r.get("Disease_name"), "predicted",
                          score=r.get("Value"),
                          note=f"IES; P={r.get('P_value')}; FDR(BH)={r.get('FDR(BH)')}; "
                               f"{r.get('Relationship')}")
            elif part == "Syndrome" and herb_subject:
                yield rel("herb_syndrome", "symmap_api", subject, name,
                          f"symmap:{r.get('Syndrome_id')}",
                          f"{r.get('Syndrome_name')} | {r.get('Syndrome_English')}", "listed",
                          note=r.get("Type"))


# -------------------------------------------------------------------------------- HERB
def herb_relations(conn: sqlite3.Connection, rel) -> Iterator[dict | None]:
    """Relations from cached HERB ``detail_api`` responses (Herb and Ingredient records)."""
    for entity, part, name, value in _responses(conn):
        if not isinstance(value, dict):
            continue
        subject = f"herb2:{entity}"
        if part == "Herb":
            for r in _table(value.get("herb_ingredient")):
                yield rel("herb_ingredient", "herb_api", subject, name,
                          f"herb2:{_text(r.get('Ingredient id'))}", _text(r.get("Ingredient name")),
                          "aggregated")
            for r in _table(value.get("herb_target")):
                yield rel("herb_target", "herb_api", subject, name,
                          f"symbol:{_text(r.get('Gene symbol'))}", _text(r.get("Protein name")),
                          "predicted", score=_text(r.get("P value")),
                          note=f"P; FDR(BH)={_text(r.get('FDR BH'))}; inferred from ingredients")
            for r in _table(value.get("herb_disease")):
                yield rel("herb_disease", "herb_api", subject, name,
                          f"herb2:{_text(r.get('Disease id'))}", _text(r.get("Disease name")),
                          "predicted", score=_text(r.get("P value")),
                          note=f"P; FDR(BH)={_text(r.get('FDR BH'))}")
            yield from _papers(value, subject, name, "herb_target", "herb_disease", rel)
        elif part == "Ingredient":
            for r in _table(value.get("ingredient_target")):
                sources = r.get("Source") or []
                lineage = "; ".join(_text(s) or "" for s in sources) if isinstance(sources, list) \
                    else _text(sources)
                yield rel("ingredient_target", "herb_api", subject, name,
                          f"symbol:{_text(r.get('Gene symbol'))}", _text(r.get("Protein name")),
                          "aggregated", note=f"via {lineage}" if lineage else None)
            for r in _table(value.get("ingredient_disease")):
                yield rel("ingredient_disease", "herb_api", subject, name,
                          f"herb2:{_text(r.get('Disease id'))}", _text(r.get("Disease name")),
                          "predicted", score=_text(r.get("P value")),
                          note=f"P; FDR(BH)={_text(r.get('FDR BH'))}")
            for r in _table(value.get("herb_ingredient")):
                yield rel("herb_ingredient", "herb_api", f"herb2:{_text(r.get('Herb id'))}",
                          " | ".join(x for x in (_text(r.get("Herb cn name")),
                                                 _text(r.get("Herb pinyin name"))) if x),
                          subject, name, "aggregated")
            yield from _papers(value, subject, name, "ingredient_target",
                               "ingredient_disease", rel)


def _papers(value: dict, subject: str, name: str | None, target_kind: str,
            disease_kind: str, rel) -> Iterator[dict | None]:
    for r in _table(value.get("drug_paper_target")):
        refs = list(_table(r.get("Reference")))
        for ref in refs or [{}]:
            pmid = _text(ref.get("PubMed ID"))
            # a graded sentence in the paper that states the relation
            yield rel(target_kind, "herb_api", subject, name,
                      f"symbol:{_text(r.get('Gene symbol'))}", _text(r.get("Protein name")),
                      "reported", reference=f"pmid:{pmid}" if pmid else _text(ref.get("Reference ID")),
                      note="; ".join(x for x in (_text(ref.get("Grade")),
                                                  _text(ref.get("Relationship"))) if x) or None)
    for r in _table(value.get("drug_paper_disease")):
        pmid = _text(r.get("PubMed id"))
        yield rel(disease_kind, "herb_api", subject, name,
                  f"herb2:{_text(r.get('Disease id'))}", _text(r.get("Disease name")),
                  "reported", reference=f"pmid:{pmid}" if pmid else _text(r.get("Reference id")),
                  note=_text(r.get("Paper title")))
