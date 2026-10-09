"""The seven corpus tools (docs/V2.md §11.5), as the dispatcher runs them.

``run(op, arguments, ctx)`` answers ``corpus.search``, ``corpus.herb``, ``corpus.formula``,
``corpus.formulas_with``, ``corpus.compounds``, ``corpus.safety`` and ``corpus.info`` from
the published corpus and returns the fields of the dispatcher's ``Outcome``: the result,
a compact view for the model, the summary in Chinese and English, the governance (kind
``corpus``: the licence of every pack read, its limits, the formula table's owner
decision) and ``receipt.corpus`` (snapshot, manifest hash, the objects read).

The rules every answer keeps: a value the corpus does not state is shown as unstated, not
filled in; a processed form, part or product is reported as an alias of its crude drug,
never resolved to it; a formula-table row is a compiled reference record whose 出处 is a
pointer, never a classical quotation; safety records are records, and no record is not
safety; not found in the corpus is not absence. Nothing here raises: a corpus that cannot
be read is a failed outcome with the remedy.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Iterable, Mapping

from .. import governance as gv
from . import packs as P
from .reader import CorpusError, Session, default_corpus

__all__ = ["OPS", "run"]

OPS = ("search", "herb", "formula", "formulas_with", "compounds", "safety", "info")

TABLE_CITE = "中医方剂数据表"
_LOTUS_DOI = "10.7554/eLife.70780"
_KIND_ZH = {"herb": "药材", "syndrome": "证候", "formula": "方剂", "safety": "安全性记录"}
_ALIAS_ZH = {"synonym": "异名", "processed": "炮制品/炮制状态", "part": "药用部位", "product": "制成品",
             "related": "相关名称"}
_ALIAS_EN = {"synonym": "synonym", "processed": "processed form or state", "part": "part",
             "product": "product", "related": "related name"}
_LAYER_ZH = {"seed": "种子语料", "clinic": "临床知识包（草案）", "materia": "药材表"}
_LIMIT_SEARCH = ("Not found in the corpus is not absence; a formula-table row is a compiled "
                 "reference record, never a classical quotation.")
_LIMIT_SAFETY = ("These are recorded rules, not a clinical safety assessment: no record is not "
                 "safety, and severity 'unstated' means the source gives none.")


# ============================================================================ helpers

def _norm(text: Any) -> str:
    try:
        from bioagent.sources.materia import to_simplified
        text = to_simplified(str(text or ""))
    except Exception:                                           # noqa: BLE001
        text = str(text or "")
    return re.sub(r"[\s·\-_.,()（）]+", "", text).casefold()


class _Core:
    """Indexes over ``core/core.json``, built once per snapshot."""

    def __init__(self, doc: Mapping[str, Any]) -> None:
        self.doc = doc
        self.herbs = {h["id"]: h for h in doc["herbs"]}
        self.syndromes = {s["id"]: s for s in doc["syndromes"]}
        self.formulas = {f["id"]: f for f in doc["formulas"]}
        self.safety = list(doc["safety"])
        self.passages = {p["id"]: p for p in doc.get("passages") or ()}
        self.names: dict[str, list[tuple[str, str, str]]] = {}
        for name, entries in doc["names"].items():
            for kind, eid in entries:
                self.names.setdefault(_norm(name), []).append((kind, eid, name))
        self.aliases: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for h in doc["herbs"]:
            for a in h["aliases"]:
                self.aliases.setdefault(_norm(a["name"]), []).append((h["id"], a))
        self.by_materia = {h["materia_id"]: h for h in doc["herbs"] if h.get("materia_id")}

    def name_of(self, kind: str, eid: str) -> str:
        table = {"herb": self.herbs, "syndrome": self.syndromes, "formula": self.formulas}.get(kind, {})
        rec = table.get(eid) or {}
        return rec.get("chinese") or eid

    def lookup(self, name: str, kind: str | None = None) -> list[tuple[str, str, str]]:
        hits = self.names.get(_norm(name), [])
        return [h for h in hits if kind is None or h[0] == kind]

    def alias_hits(self, name: str, *, synonyms: bool = False) -> list[tuple[str, dict[str, Any]]]:
        return [(hid, a) for hid, a in self.aliases.get(_norm(name), [])
                if synonyms or a["kind"] != "synonym"]


_CORES: dict[str, _Core] = {}
_CORE_LOCK = threading.Lock()


def _core(s: Session) -> _Core:
    key = s.manifest_sha256
    with _CORE_LOCK:
        hit = _CORES.get(key)
    doc = s.get("core/core.json")                # recorded in the receipt either way
    if hit is None or hit.doc is not doc:
        hit = _Core(doc)
        with _CORE_LOCK:
            if len(_CORES) > 4:
                _CORES.clear()
            _CORES[key] = hit
    return hit


def _ctx_value(ctx: Any, key: str, default: Any = None) -> Any:
    if isinstance(ctx, Mapping):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def _commercial(ctx: Any) -> bool:
    return _ctx_value(ctx, "purpose") == "commercial"


def _fail(etype: str, message: str, hint: str = "", *, summary: str = "", summary_en: str = "",
          limitations: Iterable[str] = (), status: str = "failed",
          refusals: Iterable[Mapping[str, Any]] = (), session: Session | None = None,
          result: Any = None) -> dict[str, Any]:
    g = gv.empty("corpus")
    g["limitations"] = list(limitations)
    g["refusals"] = [dict(r) for r in refusals]
    out: dict[str, Any] = {"status": status, "result": result, "model_view": None,
                           "summary": summary, "summary_en": summary_en, "governance": g,
                           "error": {"type": etype, "message": message, "hint": hint}}
    if session is not None:
        out["receipt"] = {"corpus": session.receipt()}
        _licences(g, session, session.packs_read)
    return out


def _from_error(exc: CorpusError, title: str, title_en: str,
                session: Session | None = None) -> dict[str, Any]:
    etype = "network_off" if exc.type == "network_off" else "unavailable"
    zh = "未联网，且本机没有该语料的副本" if etype == "network_off" else "语料不可读"
    en = ("web access is off and this machine has no copy of the corpus" if etype == "network_off"
          else "the corpus cannot be read")
    return _fail(etype, str(exc), exc.hint or "Run `tcmstudio corpus fetch` once to keep the "
                 "corpus on this machine.", summary=f"{title}：{zh}",
                 summary_en=f"{title_en}: {en}", session=session)


def _licences(g: dict[str, Any], s: Session, packs: Iterable[str]) -> None:
    """One licence line per pack read, the formula table's with its owner decision; the
    pack's limits (English for the model, both languages under ``governance.corpus``)."""
    manifest_packs = s.manifest.get("packs") or {}
    seen = {x.get("asset") for x in g["licences"]}
    meta_out = list((g.get("corpus") or {}).get("packs") or [])
    # the pack a tool answers from first; core (read to resolve names) after it
    packs = list(dict.fromkeys(packs))
    packs = [p for p in packs if p != "core"] + [p for p in packs if p == "core"]
    for name in packs:
        meta = manifest_packs.get(name)
        if not meta or f"corpus:{name}" in seen:
            continue
        pub = meta.get("publication") or {}
        if pub.get("basis") == "open-licence":
            note = meta["attribution"]["en"]
            commercial = None
        else:
            note = (f"published by the owner's decision ({pub.get('decided_by')}, {pub.get('date')}): "
                    f"{pub.get('basis')}; the source states no origin or licence; commercial use: "
                    f"{pub.get('commercial', 'unknown')}")
            commercial = False
        g["licences"].append(gv.licence_entry(f"corpus:{name}", meta.get("licence"), note=note,
                                              commercial=commercial))
        for lim in meta.get("limitations") or ():
            if lim.get("en") and lim["en"] not in g["limitations"]:
                g["limitations"].append(lim["en"])
        meta_out.append({"id": name, "title": meta.get("title"), "licence": meta.get("licence"),
                         "publication": pub, "evidence_ceiling": meta.get("evidence_ceiling"),
                         "limitations": meta.get("limitations") or []})
    g["corpus"] = {"snapshot_id": s.snapshot_id, "packs": meta_out,
                   "attribution_url": P.ATTRIBUTION_URL}


def _ok(s: Session, result: Any, view: Any, summary: str, summary_en: str, *,
        limits: Iterable[str] = (), citations: Iterable[Mapping[str, Any]] = (),
        notes: Iterable[str] = (), refusals: Iterable[Mapping[str, Any]] = (),
        extra_packs: Iterable[str] = ()) -> dict[str, Any]:
    g = gv.empty("corpus")
    g["limitations"] = list(limits)
    g["refusals"] = [dict(r) for r in refusals]
    _licences(g, s, [*s.packs_read, *[p for p in extra_packs if p not in s.packs_read]])
    return {"status": "succeeded", "result": result, "model_view": view, "summary": summary,
            "summary_en": summary_en, "governance": g, "citations": list(citations),
            "receipt": {"corpus": s.receipt()}, "notes": list(notes)}


def _table_refusal() -> dict[str, Any]:
    return gv.refusal("usage.corpus.formulas",
                      "The formula table's licence is unstated and its commercial use unknown; "
                      "a commercial project does not read it.")


def _row_citation(row: Mapping[str, Any]) -> dict[str, Any]:
    source = str(row.get("source") or "").rstrip("。").strip()
    label = f"{TABLE_CITE} 第 {int(row['rowno']) + 2} 行" + (f" · 出处{source}" if source.startswith("《")
                                                            else (f" · 出处：{source}" if source else ""))
    return {"kind": "source", "label": label, "url": f"{P.ATTRIBUTION_URL}#formulas",
            "evidence_ref": f"formulas:row:{row['rowno']}"}


_LOTUS_CITE = {"kind": "doi", "label": "LOTUS (Rutz A. et al., eLife 2022;11:e70780), frozen export "
                                       "2026-04-13 (CC BY 4.0)",
               "url": f"https://doi.org/{_LOTUS_DOI}", "evidence_ref": f"doi:{_LOTUS_DOI}"}


# ====================================================================== formula table

def _table_ok(s: Session) -> bool:
    return s.has_pack("formulas") and s.has("formulas/index.json")


class _Table:
    """The formula index with its names and 出处 normalised once per snapshot."""

    def __init__(self, index: Mapping[str, Any]) -> None:
        self.index = index
        self.names = index["names"]
        self.sources = index["sources"]
        self.source_ids = index["source_ids"]
        self.norm_names = [_norm(n) for n in self.names]
        self.norm_sources = [_norm(x) for x in self.sources]
        self.by_name: dict[str, list[int]] = {}
        for r, n in enumerate(self.norm_names):
            self.by_name.setdefault(n, []).append(r)

    def source_of(self, rowno: int) -> str:
        return self.sources[self.source_ids[rowno]]


_TABLES: dict[int, _Table] = {}


def _index(s: Session) -> _Table:
    index = s.get("formulas/index.json")          # recorded in the receipt either way
    with _CORE_LOCK:
        hit = _TABLES.get(id(index))
        if hit is not None and hit.index is index:
            return hit
    table = _Table(index)
    with _CORE_LOCK:
        if len(_TABLES) > 2:
            _TABLES.clear()
        _TABLES[id(index)] = table
    return table


def _row(s: Session, rowno: int) -> dict[str, Any] | None:
    chunk = s.get(f"formulas/rows/{rowno // P.ROWS_PER_CHUNK:04d}.json")
    i = rowno - int(chunk["start"])
    rows = chunk["rows"]
    return dict(rows[i]) if 0 <= i < len(rows) else None


def _brief(index: _Table, rowno: int) -> list[Any]:
    return [rowno, index.names[rowno], index.source_of(rowno)]


def _posting(s: Session, materia_id: str) -> list[int]:
    path = f"formulas/by-herb/{materia_id}.json"
    if not s.has(path):
        return []
    rows, total = [], 0
    for d in s.get(path)["rows_delta"]:
        total += d
        rows.append(total)
    return rows


def _full_row(s: Session, core: _Core, row: Mapping[str, Any]) -> dict[str, Any]:
    comps = []
    for name, dose, processing, mid in row.get("components") or ():
        herb = core.by_materia.get(mid) if mid else None
        comps.append({"name": name, "dose": dose, "processing": processing, "materia_id": mid,
                      "herb_id": herb["id"] if herb else None,
                      "herb": herb["chinese"] if herb else None})
    unresolved = [c["name"] for c in comps if not c["materia_id"]]
    out = {"rowno": row["rowno"], "sheet_row": int(row["rowno"]) + 2, "id": row["id"],
           "name": row["name"], "source": row["source"], "composition": row["composition"],
           "components": comps, "resolved": bool(comps) and not unresolved,
           "unresolved": unresolved, "preparation": row.get("preparation", ""),
           "indications": row.get("indications", ""), "usage": row.get("usage", ""),
           "cautions": row.get("cautions", ""), "roles_recorded": False}
    if row.get("written_name"):
        out["written_name"] = row["written_name"]
    return out


def _row_view(full: Mapping[str, Any]) -> dict[str, Any]:
    return {k: full[k] for k in ("rowno", "sheet_row", "id", "name", "source", "composition",
                                 "indications", "preparation", "usage", "cautions", "unresolved")
            if full.get(k) not in (None, "", [])} | {
        "components": [[c["name"], c["dose"], c["herb_id"]] for c in full["components"]]}


# ============================================================================ search

def _match_kind(norm_name: str, q: str) -> str:
    if norm_name == q:
        return "exact"
    if norm_name.startswith(q):
        return "prefix"
    return "substring" if q in norm_name else ""


_RANK = {"exact": 0, "prefix": 1, "substring": 2}


def _search(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    query = str(args["query"]).strip()
    q = _norm(query)
    if not q:
        return _fail("bad_arguments", "query is empty", 'Give a name or a phrase, e.g. {"query": "黄芪"}.')
    kinds = set(args.get("kinds") or ("herb", "formula", "syndrome", "safety"))
    limit = int(args.get("limit") or 20)
    core = _core(s)
    best: dict[tuple[str, str], dict[str, Any]] = {}

    def offer(kind: str, eid: str, matched: str, how: str, via: str,
              extra: Mapping[str, Any] | None = None) -> None:
        key = (kind, eid)
        item = {"kind": kind, "id": eid, "name": core.name_of(kind, eid), "matched": matched,
                "match": how, "via": via, **(extra or {})}
        old = best.get(key)
        rank = (_RANK[how], via != "name", len(matched))
        if old is None or rank < old["_rank"]:
            best[key] = {**item, "_rank": rank}

    for norm_name, entries in core.names.items():
        how = _match_kind(norm_name, q)
        if not how:
            continue
        for kind, eid, written in entries:
            if kind not in kinds:
                continue
            rec = (core.herbs if kind == "herb" else core.syndromes if kind == "syndrome"
                   else core.formulas).get(eid) or {}
            via = "name" if written == rec.get("chinese") else "synonym"
            extra = {"layer": rec["layer"]} if rec.get("layer") else {}
            offer(kind, eid, written, how, via, extra)
    if "herb" in kinds:
        for norm_name, hits in core.aliases.items():
            how = _match_kind(norm_name, q)
            if not how:
                continue
            for hid, a in hits:
                if a["kind"] == "synonym":
                    continue
                offer("herb", hid, a["name"], how, a["kind"],
                      {"alias_kind": a["kind"], "note": f"{a['name']} is a {_ALIAS_EN[a['kind']]} "
                                                        f"of {core.herbs[hid]['chinese']}, not the "
                                                        "crude drug itself"})
    safety_hits = []
    if "safety" in kinds:
        for r in core.safety:
            hay = [r["rule"], r["subject"]["name"], *(r["subject"].get("written") or ())]
            if r.get("counterpart"):
                hay += [r["counterpart"]["name"], *(r["counterpart"].get("written") or ())]
            if any(q in _norm(h) for h in hay if h):
                safety_hits.append({"kind": "safety", "id": r["id"], "name": r["rule"],
                                    "record_kind": r["kind"], "layer": r["layer"],
                                    "severity": r["severity"]})
    matches = sorted(best.values(), key=lambda m: (m["_rank"], m["kind"], m["id"]))
    for m in matches:
        m.pop("_rank", None)
    counts = {k: sum(1 for m in matches if m["kind"] == k) for k in ("herb", "syndrome", "formula")}
    counts["safety"] = len(safety_hits)
    table: dict[str, Any] | None = None
    refusals = []
    if "formula" in kinds and _table_ok(s):
        if _commercial(ctx):
            refusals.append(_table_refusal())
        else:
            index = _index(s)
            by_name: dict[str, list[int]] = {"exact": list(index.by_name.get(q, ())),
                                             "prefix": [], "substring": []}
            for r, name in enumerate(index.norm_names):
                if q in name and name != q:
                    by_name["prefix" if name.startswith(q) else "substring"].append(r)
            src_ids = {i for i, src in enumerate(index.norm_sources) if q in src}
            by_source = [r for r, i in enumerate(index.source_ids) if i in src_ids] if src_ids else []
            ordered = [(r, h) for h in ("exact", "prefix", "substring") for r in by_name[h]]
            ordered += [(r, "source") for r in by_source]
            rows = [_brief(index, r) + [h] for r, h in ordered[:limit]]
            table = {"name_matches": {h: len(v) for h, v in by_name.items()},
                     "name_total": sum(len(v) for v in by_name.values()),
                     "source_matches": len(by_source), "rows": rows,
                     "columns": ["rowno", "name", "出处", "match"],
                     "truncated": max(0, len(ordered) - limit)}
            counts["formula_table"] = table["name_total"]
            counts["formula_table_by_source"] = len(by_source)
    total = sum(counts.values())
    result = {"query": query, "kinds": sorted(kinds), "counts": counts,
              "matches": matches[:limit], "safety": safety_hits[:limit],
              "truncated": {"core": max(0, len(matches) - limit),
                            "safety": max(0, len(safety_hits) - limit)},
              "table": table}
    if not total:
        out = _fail("not_found", f"{query!r} matches nothing in the corpus ({', '.join(sorted(kinds))})",
                    "Try another written form (繁体 is folded to 简体), a shorter part of the name, "
                    "or corpus_formula with a 出处. Not found in the corpus is not absence.",
                    summary=f"语料检索「{query}」：未找到（不等于不存在）",
                    summary_en=f"Corpus search “{query}”: nothing found (which is not absence)",
                    limitations=[_LIMIT_SEARCH], session=s, refusals=refusals, result=result)
        return out
    parts = [f"{_KIND_ZH[k]} {counts[k]}" for k in ("herb", "syndrome", "formula", "safety") if counts.get(k)]
    parts_en = [f"{counts[k]} {k}{'s' if counts[k] != 1 else ''}" for k in
                ("herb", "syndrome", "formula") if counts.get(k)]
    if counts.get("safety"):
        parts_en.append(f"{counts['safety']} safety record(s)")
    if table:
        parts.append(f"方剂表 {table['name_total']:,} 行（方名）"
                     + (f"、{table['source_matches']:,} 行（出处）" if table["source_matches"] else ""))
        parts_en.append(f"{table['name_total']:,} formula-table row(s) by name"
                        + (f", {table['source_matches']:,} by 出处" if table["source_matches"] else ""))
    view = {"query": query, "counts": counts,
            "matches": [{k: m[k] for k in ("kind", "id", "name", "matched", "match", "via", "alias_kind",
                                           "layer") if m.get(k)} for m in matches[:limit]],
            "safety": [{k: x[k] for k in ("id", "name", "record_kind", "layer")}
                       for x in safety_hits[:limit]],
            "table_rows": table["rows"] if table else None}
    return _ok(s, result, view, f"语料检索「{query}」：" + "；".join(parts),
               f"Corpus search “{query}”: " + "; ".join(parts_en),
               limits=[_LIMIT_SEARCH], refusals=refusals)


# ============================================================================== herb

def _resolve_herb(core: _Core, name: str) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
    """(herb ids the name resolves to, non-synonym aliases it names)."""
    ids = list(dict.fromkeys(eid for kind, eid, _ in core.lookup(name, "herb")))
    return ids, ([] if ids else core.alias_hits(name))


def _herb_not_found(s: Session, core: _Core, name: str, title: str, title_en: str,
                    aliases: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    if aliases:
        hid, a = aliases[0]
        base = core.herbs[hid]["chinese"]
        kind_zh, kind_en = _ALIAS_ZH[a["kind"]], _ALIAS_EN[a["kind"]]
        return _fail("not_found",
                     f"{name!r} is recorded as a {kind_en} of {base} ({hid}), not as a crude drug of its "
                     "own; the corpus keeps it apart from the crude drug (processing, part or product "
                     "changes what it is)",
                     f"Call corpus_herb with {base!r} for the crude drug's record, and say that it is "
                     f"the record of {base}, not of {name}.",
                     summary=f"{title}：「{name}」记为{base}的{kind_zh}，语料不把它当作{base}本身（未合并）",
                     summary_en=f"{title_en}: {name} is recorded as a {kind_en} of {base}, not merged "
                                "with the crude drug", limitations=[_LIMIT_SEARCH], session=s,
                     result={"query": name, "alias_of": {"id": hid, "chinese": base, **a}})
    other = [(k, eid) for k, eid, _ in core.lookup(name) if k != "herb"]
    q = _norm(name)
    similar = list(dict.fromkeys(core.herbs[eid]["chinese"] for key, hits in core.names.items()
                                 if q and q in key for kind, eid, _ in hits if kind == "herb"))[:10]
    hint = ("Try corpus_search for other written forms. Not found in the corpus is not absence.")
    if similar:
        hint = "Herbs whose names contain it: " + "、".join(similar) + ". " + hint
    if other:
        k, eid = other[0]
        use = "corpus_formula" if k == "formula" else "corpus_search"
        hint = f"{name!r} names a {k} ({eid}); use {use}. " + hint
    return _fail("not_found", f"{name!r} names no herb in the corpus", hint,
                 summary=f"{title}：未找到「{name}」（不等于不存在）",
                 summary_en=f"{title_en}: {name} not found (which is not absence)",
                 limitations=[_LIMIT_SEARCH], session=s,
                 result={"query": name, "similar": similar} if similar else None)


def _records_for(core: _Core, hid: str) -> list[dict[str, Any]]:
    return [r for r in core.safety
            if r["subject"].get("id") == hid or (r.get("counterpart") or {}).get("id") == hid]


def _pregnancy(herb: Mapping[str, Any], records: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Every pregnancy statement with its layer: the seed and the clinic pack may differ
    (附子: 孕妇禁用 in the seed, 慎用 in the clinic pack); both are shown."""
    out = []
    for r in records:
        if r["population"] == "孕妇" and r["subject"].get("id") == herb["id"]:
            out.append({"statement": r["rule"], "layer": r["layer"], "record": r["id"],
                        "as_written": "/".join(r["subject"].get("written") or [r["subject"]["name"]])})
    return out


def _herb(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    name = str(args["name"]).strip()
    title, title_en = "语料·药材", "Corpus · herb"
    core = _core(s)
    ids, aliases = _resolve_herb(core, name)
    if not ids:
        return _herb_not_found(s, core, name, title, title_en, aliases)
    if len(ids) > 1:
        cands = [{"id": i, "chinese": core.herbs[i]["chinese"], "latin": core.herbs[i]["latin"]}
                 for i in ids]
        result = {"query": name, "status": "ambiguous", "candidates": cands}
        names = "、".join(c["chinese"] for c in cands)
        return _ok(s, result, result, f"{title}：「{name}」存在歧义，候选 {names}（未作选择）",
                   f"{title_en}: {name} is ambiguous; candidates {names} (none chosen)",
                   limits=[_LIMIT_SEARCH])
    herb = dict(core.herbs[ids[0]])
    records = _records_for(core, herb["id"])
    preg = _pregnancy(herb, records)
    curated = [{"id": f["id"], "chinese": f["chinese"], "layer": f["layer"]}
               for f in core.formulas.values()
               if any(i.get("herb_id") == herb["id"] for i in f["ingredients"])]
    passages = [p for p in core.passages.values() if herb["id"] in (p.get("mentions") or ())]
    refusals, citations, table = [], [], None
    if herb.get("materia_id") and _table_ok(s):
        if _commercial(ctx):
            refusals.append(_table_refusal())
        else:
            rows = _posting(s, herb["materia_id"])
            index = _index(s)
            table = {"count": len(rows), "first": [_brief(index, r) for r in rows[:10]],
                     "columns": ["rowno", "name", "出处"]}
    lotus = None
    if s.has_pack("lotus") and herb.get("materia_id"):
        if herb.get("compound_count") is None and s.has("lotus/unmatched.json"):
            lotus = {"compound_count": None,
                     "unmatched": (s.get("lotus/unmatched.json") or {}).get(herb["materia_id"])}
        else:
            lotus = {"compound_count": herb.get("compound_count")}
    seed = herb["layers"].get("nature") == "seed"
    result = {"herb": herb, "safety": records, "pregnancy": preg, "curated_formulas": curated,
              "passages": passages, "formula_table": table, "lotus": lotus,
              "identity_only": not seed,
              "query": name}
    clinic_aliases = [a for a in herb["aliases"] if a.get("clinic")]
    view = {"herb": {k: herb[k] for k in ("id", "chinese", "pinyin", "latin", "category", "part",
                                          "nature", "flavours", "meridians", "actions", "toxicity",
                                          "dose", "pregnancy", "cautions", "first_recorded",
                                          "layers", "formula_count", "compound_count")},
            "species": herb["species"],
            "aliases": [[a["name"], a["kind"]] for a in herb["aliases"]],
            "aliases_with_clinic_data": [{"name": a["name"], "kind": a["kind"], "clinic": a["clinic"]}
                                         for a in clinic_aliases],
            "safety": [{k: r[k] for k in ("id", "kind", "rule", "severity", "population", "layer")}
                       | {"subject": r["subject"]["name"],
                          "counterpart": (r.get("counterpart") or {}).get("name")} for r in records],
            "pregnancy_statements": preg, "curated_formulas": curated,
            "passages": [p["id"] for p in passages], "formula_table": table, "lotus": lotus}
    if herb.get("dose") or clinic_aliases:
        citations.append({"kind": "source", "label": "临床知识包 " + str(core.doc["clinic_pack"]["version"])
                          + "（未经执业中医师审核）：" + str(core.doc["clinic_pack"]["reference"].get("herbs", "")),
                          "url": f"{P.ATTRIBUTION_URL}#core", "evidence_ref": "corpus:core:clinic_pack"})
    zh, en = [], []
    if seed:
        zh.append(f"{herb['nature']}，{'、'.join(herb['flavours'])}；归{'、'.join(herb['meridians'])}经")
        en.append(f"{herb['nature']}, {'/'.join(herb['flavours'])}; meridians {'/'.join(herb['meridians'])}")
    else:
        zh.append("药材表仅有名称与物种，性味、归经、功效未录（不作推定）")
        en.append("the materia table gives identity only; nature, meridians and actions are not "
                  "recorded (not inferred)")
    n_rec = len(records)
    zh.append(f"安全性记录 {n_rec} 条（记载，无记录不等于安全）" if n_rec else "无安全性记录（不等于安全）")
    en.append(f"{n_rec} safety record(s) (records; no record is not safety)" if n_rec
              else "no safety record (which is not safety)")
    if len({p["statement"] for p in preg}) > 1:
        zh.append("孕妇：" + "；".join(f"{p['statement']}（{_LAYER_ZH.get(p['layer'], p['layer'])}）"
                                     for p in preg))
        en.append("pregnancy statements differ between layers: " + "; ".join(
            f"{p['statement']} ({p['layer']})" for p in preg))
    if table is not None:
        zh.append(f"方剂表中 {table['count']:,} 行含此药")
        en.append(f"in {table['count']:,} formula-table row(s)")
    if lotus is not None:
        if lotus.get("compound_count") is not None:
            zh.append(f"LOTUS 记录化合物 {lotus['compound_count']:,} 个（报道存在，不等于有效成分）")
            en.append(f"LOTUS records {lotus['compound_count']:,} compound(s) (reported presence, "
                      "not an active constituent)")
        else:
            zh.append("LOTUS 无此药材基原的记录（不等于不含成分）")
            en.append("LOTUS has no record for its source organisms (which is not absence of "
                      "constituents)")
    limits = [gv.LIMIT_NO_RECORD]
    if seed:
        limits.append(gv.LIMIT_CLASSICAL)
    if any(r["subject"].get("id") == herb["id"] and r["population"] == "孕妇" for r in records) and \
            len({p["statement"] for p in preg}) > 1:
        limits.append("The seed corpus and the clinic pack state different pregnancy rules for "
                      "this herb; report both, with their sources.")
    return _ok(s, result, view, f"{title}·{herb['chinese']}：" + "；".join(zh),
               f"{title_en} · {herb['chinese']}: " + "; ".join(en), limits=limits,
               citations=citations, refusals=refusals,
               notes=([f"{name} resolves to {herb['chinese']} ({herb['id']})"]
                      if name != herb["chinese"] else []))


# =========================================================================== formula

def _curated_view(f: Mapping[str, Any]) -> dict[str, Any]:
    return {k: f[k] for k in ("id", "chinese", "source", "layer", "roles_recorded", "dosage_form",
                              "indications", "actions", "contraindications", "notes")
            if f.get(k) not in (None, "", [])} | {
        "ingredients": [[i["name"], i["dose"], i["role"], i["herb_id"]] for i in f["ingredients"]]}


def _formula(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    title, title_en = "语料·方剂", "Corpus · formula"
    name = str(args.get("name") or "").strip()
    source = str(args.get("source") or "").strip()
    rowno = args.get("rowno")
    fid = str(args.get("id") or "").strip()
    if not (name or fid or rowno is not None):
        return _fail("bad_arguments", "name the formula: name, rowno or id",
                     'e.g. {"name": "桂枝汤"}, {"name": "人参散", "source": "圣惠"} or {"rowno": 23203}')
    core = _core(s)
    refusals: list[dict[str, Any]] = []
    # curated formulas first, unless a table row is asked for
    curated_ids: list[str] = []
    if fid.startswith("formula."):
        curated_ids = [fid] if fid in core.formulas else []
        if not curated_ids:
            return _fail("not_found", f"no curated formula {fid!r}", "corpus_search finds curated "
                         "formula ids. Not found in the corpus is not absence.",
                         summary=f"{title}：未找到 {fid}（不等于不存在）",
                         summary_en=f"{title_en}: {fid} not found (which is not absence)", session=s)
    elif name and not source and rowno is None and not fid:
        curated_ids = list(dict.fromkeys(eid for _, eid, _ in core.lookup(name, "formula")))
    table_rows: list[int] = []
    table_ok = _table_ok(s)
    if table_ok and _commercial(ctx):
        refusals.append(_table_refusal())
        table_ok = False
    index = _index(s) if table_ok and (name or fid.startswith("tcm:")) else None
    if curated_ids:
        curated = [core.formulas[i] for i in curated_ids]
        curated.sort(key=lambda f: (f["layer"] != "seed", f["id"]))
        variants = None
        if index is not None:
            rows = list(index.by_name.get(_norm(curated[0]["chinese"]), ()))
            variants = {"count": len(rows), "rows": [_brief(index, r) for r in rows[:20]],
                        "columns": ["rowno", "name", "出处"],
                        "note": "the formula table's rows of the same name; read one with rowno"}
        result = {"query": name or fid, "curated": curated, "table_variants": variants}
        view = {"curated": [_curated_view(f) for f in curated], "table_variants": variants}
        first = curated[0]
        n_roles = "君臣佐使" if first["roles_recorded"] else "君臣佐使未记录"
        zh = [f"{_LAYER_ZH[first['layer']]}记载组成 {len(first['ingredients'])} 味"
              f"（{n_roles}，出处《{first['source']}》）"]
        en = [f"{first['layer']} record: {len(first['ingredients'])} herbs ("
              f"{'roles recorded' if first['roles_recorded'] else 'roles not recorded'}, "
              f"from {first['source']})"]
        if len(curated) > 1:
            zh.append("另有" + "、".join(_LAYER_ZH[f["layer"]] for f in curated[1:]) + "版本")
            en.append("also a " + ", ".join(f["layer"] for f in curated[1:]) + " version")
        if variants is not None:
            zh.append(f"方剂表另有同名记录 {variants['count']} 行（汇编记录，非经典条文）")
            en.append(f"{variants['count']} formula-table row(s) of the same name (compiled records, "
                      "not classical passages)")
        limits = [gv.LIMIT_CLASSICAL]
        if any(f["layer"] == "clinic" for f in curated):
            limits.append("The clinic pack's version is a draft transcription of the 《方剂学》 "
                          "textbook, not reviewed by a licensed practitioner; roles are not recorded.")
        return _ok(s, result, view, f"{title}·{first['chinese']}：" + "；".join(zh),
                   f"{title_en} · {first['chinese']}: " + "; ".join(en), limits=limits,
                   refusals=refusals)
    if not table_ok:
        if refusals:
            return _fail("refused", "the formula table is not read for a commercial project",
                         refusals[0]["remedy"], status="refused", refusals=refusals,
                         summary=f"{title}：已拒绝（方剂表许可未声明，商业用途未知）",
                         summary_en=f"{title_en}: refused (the formula table's licence is unstated; "
                                    "commercial use unknown)", session=s)
        return _fail("unavailable", "this snapshot has no formula table",
                     "Use a corpus snapshot built with the formulas pack.",
                     summary=f"{title}：本快照不含方剂表", summary_en=f"{title_en}: no formula table in "
                                                                "this snapshot", session=s)
    if rowno is not None:
        rowno = int(rowno)
        counts = s.manifest["packs"]["formulas"].get("counts") or {}
        total = int(counts.get("sheet_rows") or counts.get("rows") or 0)
        if not 0 <= rowno < total:
            return _fail("bad_arguments", f"rowno {rowno} is outside the table (0–{total - 1})",
                         "rowno is the 0-based data row; corpus_search and corpus_formula list them.",
                         session=s)
        table_rows = [rowno]
    elif fid:
        shard = f"formulas/ids/{fid.rsplit('.fx', 1)[-1][:1] or '0'}.json"
        table_rows = list((s.get(shard) if s.has(shard) else {}).get(fid) or [])
        if not table_rows:
            return _fail("not_found", f"no formula-table row has id {fid!r}",
                         "Ids look like tcm:formula.fx…; corpus_formula with a name lists them. "
                         "Not found in the corpus is not absence.",
                         summary=f"{title}：未找到 {fid}（不等于不存在）",
                         summary_en=f"{title_en}: {fid} not found (which is not absence)",
                         limitations=[_LIMIT_SEARCH], session=s)
    else:
        q = _norm(name)
        table_rows = list(index.by_name.get(q, ()))
        if source and table_rows:
            sq = _norm(source)
            table_rows = [r for r in table_rows if sq in index.norm_sources[index.source_ids[r]]]
        if not table_rows:
            similar = [r for r, n in enumerate(index.norm_names) if q and q in n][:10]
            where = f" with 出处 containing {source!r}" if source else ""
            return _fail("not_found", f"no formula-table row is named {name!r}{where}",
                         ("Similar names: " + "、".join(index.names[r] for r in similar) + ". "
                          if similar else "") + "Not found in the table is not absence from the "
                         "literature.",
                         summary=f"{title}：方剂表中未找到「{name}」"
                                 + (f"（出处含「{source}」）" if source else "") + "（不等于古籍未载）",
                         summary_en=f"{title_en}: {name} not found in the formula table (which is "
                                    "not absence from the literature)",
                         limitations=[_LIMIT_SEARCH], session=s,
                         result={"query": name, "source": source,
                                 "similar": [_brief(index, r) for r in similar]})
    limits: list[str] = []                    # the formula pack's own limits say it
    if len(table_rows) > 3:
        index = index or _index(s)
        cands = [_brief(index, r) for r in table_rows[:100]]
        result = {"query": name, "source": source, "status": "candidates",
                  "count": len(table_rows), "candidates": cands,
                  "columns": ["rowno", "name", "出处"], "truncated": max(0, len(table_rows) - 100)}
        view = {"count": len(table_rows), "candidates": cands, "how": "narrow with source (出处 "
                "substring) or read one with rowno"}
        return _ok(s, result, view,
                   f"{title}·{name}：方剂表中同名 {len(table_rows)} 行，需按出处或行号选定（候选，未作选择）",
                   f"{title_en} · {name}: {len(table_rows)} formula-table rows share the name; "
                   "choose by 出处 or rowno (candidates, none chosen)", limits=limits)
    found = [_row(s, r) for r in table_rows]
    if any(not r or r.get("empty") for r in found):
        return _fail("not_found", f"sheet row {int(table_rows[0]) + 2} of the formula table is empty",
                     "Read another row; corpus_search lists rows by name.", session=s)
    full = [_full_row(s, core, r) for r in found]
    result = {"query": name or fid or rowno, "source": source, "status": "rows", "rows": full}
    view = {"rows": [_row_view(f) for f in full]}
    first = full[0]
    unresolved = f"，{len(first['unresolved'])} 味未能对应药材" if first["unresolved"] else ""
    unresolved_en = f", {len(first['unresolved'])} unresolved" if first["unresolved"] else ""
    extra = f"（另 {len(full) - 1} 行）" if len(full) > 1 else ""
    return _ok(s, result, view,
               f"{title}·{first['name']}（{first['source'].rstrip('。')}，方剂表第 {first['sheet_row']} 行）："
               f"{len(first['components'])} 味{unresolved}；汇编记录，出处非引文{extra}",
               f"{title_en} · {first['name']} ({first['source'].rstrip('。')}, table row "
               f"{first['sheet_row']}): {len(first['components'])} components{unresolved_en}; a "
               "compiled record, its 出处 is not a quotation", limits=limits,
               citations=[_row_citation(f) for f in full])


# ===================================================================== formulas_with

def _formulas_with(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    title, title_en = "语料·含药方剂", "Corpus · formulas containing"
    names = [str(h).strip() for h in args["herbs"] if str(h).strip()]
    match = args.get("match") or "all"
    limit, offset = int(args.get("limit") or 20), int(args.get("offset") or 0)
    if not names:
        return _fail("bad_arguments", "herbs is empty", 'e.g. {"herbs": ["黄芪"]}')
    if not _table_ok(s):
        return _fail("unavailable", "this snapshot has no formula table",
                     "Use a corpus snapshot built with the formulas pack.", session=s)
    if _commercial(ctx):
        r = _table_refusal()
        return _fail("refused", r["message"], r["remedy"], status="refused", refusals=[r],
                     summary=f"{title}：已拒绝（方剂表许可未声明，商业用途未知）",
                     summary_en=f"{title_en}: refused (the formula table's licence is unstated; "
                                "commercial use unknown)", session=s)
    from bioagent.sources import materia as M
    core = _core(s)
    resolved, unresolved, notes = [], [], []
    for n in names:
        mid = M.resolve_name(n)
        if not mid:
            unresolved.append(n)
            continue
        herb = core.by_materia.get(mid)
        item = {"name": n, "materia_id": mid, "herb_id": herb["id"] if herb else None,
                "herb": herb["chinese"] if herb else mid}
        if herb and n != herb["chinese"]:
            alias = next((a for a in herb["aliases"] if a["name"] == n), None)
            if alias is None or alias["kind"] != "synonym":
                item["note"] = (f"{n} counts as {herb['chinese']}: the table's postings are by crude "
                                "drug, in any processing state")
                notes.append(item["note"])
        resolved.append(item)
    if not resolved:
        return _fail("not_found", "none of the names resolves to a crude drug of the table: "
                     + "、".join(unresolved),
                     "Use a crude-drug name (corpus_search finds written forms). Not found is not "
                     "absence.", summary=f"{title}：未能识别 {'、'.join(unresolved)}（不等于不存在）",
                     summary_en=f"{title_en}: {', '.join(unresolved)} not recognised (which is not "
                                "absence)", session=s)
    sets = [set(_posting(s, r["materia_id"])) for r in resolved]
    if match == "any":
        rows = sorted(set().union(*sets))
    else:
        rows = sorted(set.intersection(*sets)) if not unresolved else []
    index = _index(s)
    page = [_brief(index, r) for r in rows[offset:offset + limit]]
    result = {"herbs": resolved, "unresolved": unresolved, "match": match, "total": len(rows),
              "offset": offset, "limit": limit, "rows": page, "columns": ["rowno", "name", "出处"],
              "per_herb": {r["name"]: len(x) for r, x in zip(resolved, sets)}}
    label = (" + " if match == "all" else " 或 ").join(r["herb"] for r in resolved)
    label_en = (" + " if match == "all" else " or ").join(r["herb"] for r in resolved)
    tail = f"；未识别：{'、'.join(unresolved)}" if unresolved else ""
    tail_en = f"; not recognised: {', '.join(unresolved)}" if unresolved else ""
    if unresolved and match == "all":
        tail += "（无法求交集，总数记为 0）"
        tail_en += " (no intersection can be taken, so the total is 0)"
    limits = ["Counts describe a compiled formula table (rows naming the herb), not prescribing "
              "practice, frequency of use or efficacy."]
    return _ok(s, result, {k: result[k] for k in ("herbs", "unresolved", "match", "total", "offset",
                                                  "rows", "per_herb")},
               f"{title}「{label}」：方剂表中 {len(rows):,} 行（汇编记录）{tail}",
               f"{title_en} {label_en}: {len(rows):,} formula-table row(s) (compiled records){tail_en}",
               limits=limits, notes=notes)


# ========================================================================= compounds

def _compounds(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    del ctx
    title, title_en = "语料·化合物", "Corpus · compounds"
    name = str(args["herb"]).strip()
    limit, offset = int(args.get("limit") or 50), int(args.get("offset") or 0)
    core = _core(s)
    ids, aliases = _resolve_herb(core, name)
    if not ids:
        return _herb_not_found(s, core, name, title, title_en, aliases)
    if len(ids) > 1:
        names = "、".join(core.herbs[i]["chinese"] for i in ids)
        return _fail("bad_arguments", f"{name!r} is ambiguous: {names}",
                     "Name one of the candidates.", summary=f"{title}：「{name}」存在歧义（{names}）",
                     summary_en=f"{title_en}: {name} is ambiguous ({names})", session=s)
    herb = core.herbs[ids[0]]
    if not s.has_pack("lotus"):
        return _fail("unavailable", "this snapshot has no LOTUS pack",
                     "Use a corpus snapshot built with the lotus pack.",
                     summary=f"{title}：本快照不含 LOTUS 数据", summary_en=f"{title_en}: no LOTUS pack "
                                                                     "in this snapshot", session=s)
    mid = herb.get("materia_id") or ""
    path = f"lotus/{mid}.json"
    if not s.has(path):
        why = (s.get("lotus/unmatched.json") or {}).get(mid) if s.has("lotus/unmatched.json") else None
        reason = (why or {}).get("reason", "")
        zh = ("非生物来源（矿物或加工品），无基原生物" if reason == "not_an_organism"
              else "LOTUS 中没有与其基原物种对应的生物")
        en = ("not derived from an organism (a mineral or a processed product)"
              if reason == "not_an_organism" else "LOTUS has no organism matching its source species")
        result = {"herb": {"id": herb["id"], "chinese": herb["chinese"], "materia_id": mid,
                           "species": herb["species"]}, "organisms": [], "compounds": [],
                  "total": 0, "unmatched": why}
        return _ok(s, result, result, f"{title}·{herb['chinese']}：{zh}；无化合物记录（不等于不含成分）",
                   f"{title_en} · {herb['chinese']}: {en}; no compound record (which is not absence "
                   "of constituents)", citations=[_LOTUS_CITE], extra_packs=["lotus"])
    doc = s.get(path)
    compounds = doc["compounds"]
    page = compounds[offset:offset + limit]
    result = {"herb": {"id": herb["id"], "chinese": herb["chinese"], "materia_id": mid,
                       "species": herb["species"]}, "organisms": doc["organisms"],
              "total": len(compounds), "offset": offset, "limit": limit, "compounds": page}
    view = {"herb": herb["chinese"], "organisms": doc["organisms"], "total": len(compounds),
            "offset": offset,
            "compounds": [[c["ik"], c["name"], c["formula"], c["refs"]] for c in page],
            "columns": ["InChIKey", "name", "formula", "references"]}
    orgs = "、".join(o["name"] for o in doc["organisms"])
    return _ok(s, result, view,
               f"{title}·{herb['chinese']}：LOTUS 在其基原生物（{orgs}）中记录化合物 {len(compounds):,} 个"
               "（报道存在，不等于有效成分、含量或疗效）",
               f"{title_en} · {herb['chinese']}: LOTUS records {len(compounds):,} compound(s) in its "
               f"source organisms ({orgs}) (reported presence: not an active constituent, a content "
               "or efficacy)", citations=[_LOTUS_CITE])


# ============================================================================ safety

def _safety(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    del ctx
    title, title_en = "语料·安全性", "Corpus · safety"
    names = list(dict.fromkeys(str(h).strip() for h in args["herbs"] if str(h).strip()))
    core = _core(s)
    parties: list[dict[str, Any]] = []
    unresolved = []
    for n in names:
        ids, aliases = _resolve_herb(core, n)
        item: dict[str, Any] = {"name": n, "ids": ids}
        if not ids and aliases:
            hid, a = aliases[0]
            item["ids"] = [hid]
            item["note"] = (f"{n} is a {_ALIAS_EN[a['kind']]} of {core.herbs[hid]['chinese']}: its "
                            "records are matched to that herb (processing can change toxicity)")
        parties.append(item)

    def matches(party: Mapping[str, Any] | None, who: Mapping[str, Any]) -> bool:
        if not party:
            return False
        if party.get("id") and party["id"] in who["ids"]:
            return True
        written = {party.get("name"), *(party.get("written") or ())}
        return who["name"] in written or any(
            who["name"] in str(w).split("、") for w in written if w)

    for p in parties:
        if p["ids"]:
            continue
        named = [r["id"] for r in core.safety if any(matches(x, p) for x in (r["subject"],
                                                                             r.get("counterpart")))]
        if named:
            p["note"] = (f"{p['name']} is not a herb of the corpus; it is named in "
                         f"{len(named)} record(s) as written")
        else:
            unresolved.append(p["name"])

    pairs = []
    for r in core.safety:
        if r["kind"] not in ("incompatibility", "interaction") or not r.get("counterpart"):
            continue
        for i, a in enumerate(parties):
            for b in parties[i + 1:]:
                if (matches(r["subject"], a) and matches(r["counterpart"], b)) or \
                        (matches(r["subject"], b) and matches(r["counterpart"], a)):
                    pairs.append({"between": [a["name"], b["name"]], **r})
    paired_ids = {p["id"] for p in pairs}
    per_herb = []
    for p in parties:
        recs = [r for r in core.safety if r["kind"] != "incompatibility" and matches(r["subject"], p)
                and (not r.get("counterpart") or (r["kind"] == "interaction"
                                                  and r["id"] not in paired_ids))]
        herb = core.herbs[p["ids"][0]] if p["ids"] else None
        per_herb.append({"name": p["name"], "herb_id": herb["id"] if herb else None,
                         "herb": herb["chinese"] if herb else None,
                         "pregnancy": herb.get("pregnancy") if herb else None,
                         "toxicity": herb.get("toxicity") if herb else None,
                         "toxicity_layer": (herb["layers"].get("toxicity") if herb else None),
                         "cautions": herb.get("cautions") if herb else [],
                         "records": recs, "note": p.get("note", "")})
    if not any(p["ids"] for p in parties) and not pairs:
        return _fail("not_found", "none of the names is recognised: " + "、".join(unresolved),
                     "Use the names the corpus knows (corpus_search). An unrecognised name has no "
                     "record here, which is not safety.",
                     summary=f"{title}：未能识别 {'、'.join(unresolved)}（未识别不等于安全）",
                     summary_en=f"{title_en}: {', '.join(unresolved)} not recognised (not "
                                "recognised is not safe)", limitations=[_LIMIT_SAFETY], session=s)
    pair_keys = {(tuple(sorted(p["between"])), p["kind"]) for p in pairs}
    rules = list(dict.fromkeys(re.split(r"[：:]", p["rule"], maxsplit=1)[0] for p in pairs
                               if p["kind"] == "incompatibility"))
    n_inc = sum(1 for k in pair_keys if k[1] == "incompatibility")
    n_int = sum(1 for k in pair_keys if k[1] == "interaction")
    label = " + ".join(names)
    zh, en = [], []
    if n_inc:
        zh.append(f"记载 {n_inc} 处配伍禁忌（{'、'.join(rules)}）")
        en.append(f"{n_inc} recorded incompatibilit{'y' if n_inc == 1 else 'ies'} ({', '.join(rules)})")
    if n_int:
        zh.append(f"记载 {n_int} 处药物相互作用")
        en.append(f"{n_int} recorded interaction(s)")
    if not pairs and len(names) > 1:
        zh.append("未见配伍禁忌记录（不等于可以合用）")
        en.append("no incompatibility recorded (which is not safety to combine)")
    n_rec = sum(len(h["records"]) for h in per_herb)
    zh.append(f"单药安全性记录 {n_rec} 条")
    en.append(f"{n_rec} single-herb safety record(s)")
    if unresolved:
        zh.append(f"未识别：{'、'.join(unresolved)}（未识别不等于安全）")
        en.append(f"not recognised: {', '.join(unresolved)} (not recognised is not safe)")
    result = {"herbs": parties, "pairs": pairs, "per_herb": per_herb, "unresolved": unresolved,
              "statement": "Records, not a clinical safety assessment; no record is not safety."}
    view = {"pairs": [{"between": p["between"], "kind": p["kind"], "rule": p["rule"],
                       "severity": p["severity"], "layer": p["layer"], "citation": p["citation"]}
                      for p in pairs],
            "per_herb": [{"name": h["name"], "herb": h["herb"], "pregnancy": h["pregnancy"],
                          "toxicity": h["toxicity"], "toxicity_layer": h["toxicity_layer"],
                          "cautions": h["cautions"],
                          "records": [{k: r[k] for k in ("kind", "rule", "severity", "population",
                                                         "layer")} for r in h["records"]],
                          **({"note": h["note"]} if h["note"] else {})} for h in per_herb],
            "unresolved": unresolved}
    return _ok(s, result, view, f"{title}·{label}：" + "；".join(zh) + "（记载，非临床安全性评估；无记录不等于安全）",
               f"{title_en} · {label}: " + "; ".join(en) + " (records, not a clinical safety "
               "assessment; no record is not safety)", limits=[_LIMIT_SAFETY, gv.LIMIT_NO_RECORD],
               notes=[p["note"] for p in parties if p.get("note")])


# ============================================================================== info

def _info(s: Session, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    del args, ctx
    m = s.manifest
    packs = {}
    for name, meta in (m.get("packs") or {}).items():
        packs[name] = {"title": meta.get("title"), "licence": meta.get("licence"),
                       "publication": meta.get("publication"),
                       "evidence_ceiling": meta.get("evidence_ceiling"),
                       "counts": meta.get("counts"), "objects": meta.get("objects"),
                       "bytes": meta.get("bytes"),
                       "sources": [{k: x.get(k) for k in ("name", "version", "licence", "citation",
                                                          "url") if x.get(k)}
                                   for x in meta.get("sources") or ()],
                       "limitations": meta.get("limitations")}
    result = {"snapshot_id": s.snapshot_id, "data_date": m.get("data_date"),
              "manifest_sha256": s.manifest_sha256, "packs": packs, "skipped": m.get("skipped") or {},
              "totals": m.get("totals"), "attribution_url": P.ATTRIBUTION_URL,
              # where it was read from is in receipt.corpus.source: the result is the same
              # in the browser and on the runner
              "never_published": [c["name"] for c in P.NEVER_PUBLISHED.values()]}
    view = {"snapshot_id": s.snapshot_id, "data_date": m.get("data_date"),
            "packs": {k: {"title": v["title"]["en"] if v.get("title") else k,
                          "licence": v["licence"],
                          "basis": (v.get("publication") or {}).get("basis", "owner decision"),
                          "counts": v["counts"]} for k, v in packs.items()},
            "skipped": m.get("skipped") or {}, "attribution_url": P.ATTRIBUTION_URL}
    zh = "、".join(f"{(v.get('title') or {}).get('zh', k)}" for k, v in packs.items())
    return _ok(s, result, view, f"语料快照 {s.snapshot_id}：{len(packs)} 个语料包（{zh}）",
               f"Corpus snapshot {s.snapshot_id}: {len(packs)} pack(s) ({', '.join(packs)})",
               extra_packs=list(packs))


_RUN = {"search": _search, "herb": _herb, "formula": _formula, "formulas_with": _formulas_with,
        "compounds": _compounds, "safety": _safety, "info": _info}
_TITLES = {"search": ("语料检索", "Corpus search"), "herb": ("语料·药材", "Corpus · herb"),
           "formula": ("语料·方剂", "Corpus · formula"),
           "formulas_with": ("语料·含药方剂", "Corpus · formulas containing"),
           "compounds": ("语料·化合物", "Corpus · compounds"),
           "safety": ("语料·安全性", "Corpus · safety"), "info": ("语料信息", "Corpus info")}


def run(op: str, arguments: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    """One corpus tool call → the fields of the dispatcher's ``Outcome``. Never raises."""
    title, title_en = _TITLES.get(op, ("语料", "Corpus"))
    if op not in _RUN:
        return _fail("not_found", f"no corpus operation {op!r}", "One of: " + ", ".join(OPS))
    try:
        corpus = default_corpus(ctx)
    except CorpusError as exc:
        return _from_error(exc, title, title_en)
    session = corpus.session()
    try:
        return _RUN[op](session, arguments, ctx)
    except CorpusError as exc:
        return _from_error(exc, title, title_en, session)
