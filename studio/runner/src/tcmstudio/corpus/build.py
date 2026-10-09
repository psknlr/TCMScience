"""Build the published corpus (docs/V2.md §11.2–§11.3): packs → objects → manifest.

    build_corpus(out_dir, …)      writes out_dir/corpus/… and out_dir/attribution.html
    build_site_corpus(staging, log=…)   the webbuild hook; returns boot.json's ``corpus`` entry
    runtime_boot_entry()          boot.json's ``corpus`` entry for a page the runner serves

Each pack is assembled from its sources with nothing invented: a value a source does not
state stays ``""``, ``null`` or ``"unstated"``. The core pack merges the seed knowledge
base, the materia table (with NCBI taxa) and the clinic pack; the formula table is a
separate universe (its names never enter the core name index), parsed with
``bioagent.sources.formulas`` so components resolve exactly as the research loop resolves
them; the LOTUS and Reactome/HGNC packs are read from the committed open-data extracts in
``studio/corpus/data`` and skipped, with a warning and a note in the manifest, when those
files are absent.

Every object is compact UTF-8 JSON (sorted keys), gzip level 9 with mtime 0, named by the
SHA-256 of its gzip bytes. The build reads no clock, so the same sources give the same
bytes and the same snapshot id. Reading the 14 MB xlsx takes about 20 s, so the formula
pack's objects are cached under ``$TCMSTUDIO_CACHE/corpus-build`` (default
``~/.cache/tcmstudio/corpus-build``), keyed by the xlsx's SHA-256, the parser's source code
and ``BUILD_VERSION``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote

from . import packs as P

__all__ = ["BUILD_VERSION", "MAX_OBJECT_BYTES", "ROWS_PER_CHUNK", "BuildError",
           "build_corpus", "build_site_corpus", "runtime_boot_entry", "default_data_dir",
           "default_xlsx", "core_payload", "type_aliases", "encode", "object_url"]

#: Changes whenever the layout or content rules of any object change (it keys the cache).
BUILD_VERSION = "tcmstudio.corpus.build/1"
#: No object may be larger than this on the wire (phones fetch them one by one).
MAX_OBJECT_BYTES = 2 * 1024 * 1024
ROWS_PER_CHUNK = P.ROWS_PER_CHUNK
#: Formula-pack build-cache entries kept (about 21 MB each).
_CACHE_KEEP = 3
ID_SHARDS = 16
_STUDIO = Path(__file__).resolve().parents[4]
#: Where the repository's own sources are published (the sources[].url of the core and
#: formula packs).
_REPO = "https://github.com/psknlr/TCMScience"

Log = Callable[[str], None]


class BuildError(RuntimeError):
    """The corpus cannot be built as asked; the message says why."""


# ======================================================================== encoding

def encode(value: Any) -> bytes:
    """Compact, key-sorted UTF-8 JSON: the raw bytes of every object."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _gzip(raw: bytes) -> bytes:
    return gzip.compress(raw, compresslevel=9, mtime=0)


def object_url(sha256: str) -> str:
    return f"o/{sha256[:32]}.gz"


@dataclass
class _Object:
    path: str
    pack: str
    raw: int
    gz: bytes
    sha256: str


@dataclass
class _Pack:
    name: str
    objects: dict[str, _Object] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def add(self, path: str, value: Any) -> None:
        raw = encode(value)
        gz = _gzip(raw)
        self.add_bytes(path, len(raw), gz)

    def add_bytes(self, path: str, raw_len: int, gz: bytes) -> None:
        if len(gz) > MAX_OBJECT_BYTES:
            raise BuildError(f"{path}: {len(gz):,} bytes gzipped, over the "
                             f"{MAX_OBJECT_BYTES:,}-byte limit for one object")
        self.objects[path] = _Object(path, self.name, raw_len, gz,
                                     hashlib.sha256(gz).hexdigest())


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ======================================================================= core pack

#: Names the materia table lists under a drug whose kind the rules below cannot see. Each
#: was read against what it denotes; every one is kept off the name index (a lookup by it
#: reports the drug it is listed under instead of resolving to it).
_ALIAS_KINDS: dict[str, str] = {
    # processed forms with no processing word in front
    "红参": "processed", "黑附子": "processed", "枯矾": "processed", "飞矾": "processed",
    "胆星": "processed", "阿胶珠": "processed", "百药煎": "product",
    # another part of the plant or animal
    "茯神": "part", "白茯神": "part", "朱茯神": "part", "侧子": "part", "椒目": "part",
    "茅针": "part", "龙齿": "part", "莱菔": "part", "萝卜": "part", "韭菜": "part",
    "车前草": "part", "苍耳草": "part", "桃奴": "part", "浮小麦": "part", "猪胆": "part",
    "猪肚": "part", "猪腰子": "part", "猪肾": "part",
    # a product of the same organism
    "鸡子": "product", "鸡子清": "product", "鸡子黄": "product", "鸡蛋": "product",
    "鸡屎白": "product", "乌鸡": "product", "牛乳": "product", "乳汁": "product",
    "大豆黄卷": "product", "谷芽": "product", "粟芽": "product", "白面": "product",
    "面": "product", "干面": "product", "麦面": "product", "小麦面": "product",
    "铁落": "product", "生铁落": "product",
    # grouped under the drug for its organism or material, but another drug or variety
    "海带": "related", "昆布": "related", "糯米": "related", "黄豆": "related",
    "大豆": "related", "食盐": "related", "盐": "related", "铅": "related", "黑铅": "related",
}

#: Prefixes that mark a processed form when what follows is the same drug (法半夏, 姜厚朴,
#: 胆南星, 炼蜜), in addition to bioagent's own processing words.
_PROCESS_PREFIXES_SAME = ("法", "清", "姜", "胆", "淡", "炼", "水飞", "飞", "枯")
#: Endings that make a processed form (comminuted, sliced, charred, defatted) or a product.
_PROCESS_SUFFIXES = ("炭", "霜", "末", "粉", "屑", "片", "泥")
_PRODUCT_SUFFIXES = ("曲", "胶", "汁", "油", "膏", "灰", "饭", "沥", "饼")
#: Words naming a part of a plant or animal, longest first.
_PART_SUFFIXES = ("茎叶", "根皮", "树皮", "白皮", "梢", "节", "须", "芦", "身", "尾", "皮", "心",
                  "头", "尖", "叶", "根", "花", "子", "仁", "核", "壳", "肉", "白", "梗", "穗",
                  "茎", "枝", "藤", "实", "刺", "针", "角", "蒂")


def _part_ending(name: str) -> str:
    return next((s for s in _PART_SUFFIXES if name.endswith(s) and len(name) > len(s)), "")


def type_aliases(chinese: str, mid: str, aliases: Iterable[str], *, seed_aliases: Iterable[str] = (),
                 seed_processed: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Each name of one crude drug with its kind: ``synonym`` (resolves to the drug),
    ``processed`` (a processing state: 炙甘草, 生地黄, 甘草末), ``part`` (甘草梢, 茯苓皮),
    ``product`` (半夏曲, 生姜汁) or ``related`` (another drug the materia table groups with it
    by organism). Only synonyms resolve; the rest are kept on the herb and never resolved
    to it, because a processed form, a part or a product is not the crude drug.

    Rules, first match wins: a seed processed form; a seed alias (curated synonym); a reviewed
    kind (``_ALIAS_KINDS``); bioagent's ``processing_of`` (a processing word before the
    drug's own name, or a named processed form); a processing prefix the drug's own name
    does not start with; a processing or product ending the drug's own name does not end
    with; a part word after another name of the drug, or after the stem the drug's name
    shares with it; otherwise a synonym."""
    from bioagent.sources import materia as M

    seed_aliases = set(seed_aliases)
    seed_processed = dict(seed_processed or {})
    names = [a for a in dict.fromkeys(aliases) if a and a != chinese]
    own = {chinese, *names}
    out: list[dict[str, Any]] = []
    for a in names:
        item: dict[str, Any] = {"name": a, "kind": "synonym"}
        if a in seed_processed:
            p = seed_processed[a]
            item.update(kind="processed", processing=p.method, processed_id=p.id,
                        effect_change=p.effect_change, toxicity_change=p.toxicity_change)
        elif a in seed_aliases:
            pass
        elif a in _ALIAS_KINDS:
            item["kind"] = _ALIAS_KINDS[a]
        else:
            state = ""
            try:
                state = M.processing_of(M.NameMention(a, mid, 0))
            except Exception:                                   # noqa: BLE001
                state = ""
            prefix = next((w for w in (*M.PROCESSING_WORDS, *_PROCESS_PREFIXES_SAME)
                           if a.startswith(w) and not chinese.startswith(w)
                           and len(a) > len(w)
                           and (w in M.PROCESSING_WORDS or M.resolve_name(a[len(w):]) == mid)),
                          "")
            p_end = next((s for s in _PROCESS_SUFFIXES if a.endswith(s) and len(a) > len(s)
                          and not chinese.endswith(s)), "")
            q_end = next((s for s in _PRODUCT_SUFFIXES if a.endswith(s) and len(a) > len(s)
                          and not chinese.endswith(s)), "")
            part = _part_ending(a)
            if state or prefix:
                item.update(kind="processed", processing=state or prefix)
            elif p_end:
                item.update(kind="processed", processing=p_end)
            elif q_end:
                item["kind"] = "product"
            elif part and not chinese.endswith(part) and (
                    a[:-len(part)] in own or M.resolve_name(a[:-len(part)]) == mid
                    or _shares_stem(a, part, chinese)):
                item.update(kind="part", part=part)
        out.append(item)
    return out


def _shares_stem(alias: str, part: str, chinese: str) -> bool:
    """桃叶 under 桃枝, 韭根 under 韭菜子: the alias is a stem of the drug's own name plus
    another part word."""
    own = _part_ending(chinese)
    stem = alias[:-len(part)]
    if not own or part.endswith(own) or own.endswith(part):
        return False
    return bool(stem) and chinese.startswith(stem)


def _taxa_index() -> dict[str, dict[str, Any]]:
    from bioagent.sources.materia import TAXA_FILE
    try:
        return json.loads(Path(TAXA_FILE).read_text(encoding="utf-8")).get("names", {})
    except (OSError, ValueError):
        return {}


def _species(names: Iterable[str], taxa: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Species with their NCBI taxid where NCBI resolved the name (a match on NCBI's 'any
    name' index counts only once a person reviewed it, as in ``materia.crude_drugs``)."""
    out = []
    for name in dict.fromkeys(names):
        rec = taxa.get(name) or {}
        ok = bool(rec.get("taxid")) and (rec.get("matched_on") != "All Names"
                                         or bool(rec.get("reviewed")))
        item: dict[str, Any] = {"name": name, "taxid": str(rec["taxid"]) if ok else None}
        if ok and rec.get("scientific_name") and rec["scientific_name"] != name:
            item["ncbi_name"] = rec["scientific_name"]
        out.append(item)
    return out


def _citation_of(kb: Any, ids: Iterable[str]) -> str:
    parts = []
    for i in ids:
        e = kb.passages.get(i) or kb.studies.get(i)
        if e is None:
            parts.append(i)
        elif hasattr(e, "text"):
            parts.append(f"《{e.source}》{e.chapter}".strip())
        else:
            parts.append(e.citation)
    return "；".join(p for p in parts if p)


class _Resolver:
    """Clinic-pack names → herb ids, with the alias kind that matched."""

    def __init__(self, herbs: list[dict[str, Any]]) -> None:
        self.canonical: dict[str, str] = {}
        self.alias: dict[str, tuple[str, dict[str, Any]]] = {}
        for h in herbs:
            self.canonical.setdefault(h["chinese"], h["id"])
        for h in herbs:
            for a in h["aliases"]:
                if a["name"] not in self.canonical:
                    self.alias.setdefault(a["name"], (h["id"], a))
        self.by_materia = {h["materia_id"]: h["id"] for h in herbs if h["materia_id"]}

    def resolve(self, name: str) -> tuple[str | None, str]:
        """(herb id, how): how is "name", the alias kind, "materia" (bioagent's resolver
        stripped a processing word), or "" when nothing matched."""
        if name in self.canonical:
            return self.canonical[name], "name"
        if name in self.alias:
            hid, a = self.alias[name]
            return hid, a["kind"]
        from bioagent.sources.materia import resolve_name
        mid = resolve_name(name)
        if mid and mid in self.by_materia:
            return self.by_materia[mid], "materia"
        return None, ""


def _herb_id(mid: str) -> str:
    return "herb.shudihuang" if mid == "shudi" else f"herb.{mid}"


def core_payload(*, formula_counts: Mapping[str, int] | None = None,
                 compound_counts: Mapping[str, int] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """``core/core.json`` and its build report (counts, unresolved names, sources)."""
    from bioagent.clinic.pack import DEFAULT_PACK, load_pack
    from bioagent.sources import materia as M
    from bioagent.tcm.knowledge import seed

    kb = seed()
    raw_pack = DEFAULT_PACK.read_bytes()
    load_pack(DEFAULT_PACK)                    # the pack's own consistency checks
    clinic = json.loads(raw_pack.decode("utf-8"))
    taxa = _taxa_index()
    pack_note = f"clinic_pack {clinic['version']} ({clinic['review_status']})"
    ref = clinic.get("reference") or {}
    unresolved: dict[str, set[str]] = {}

    def miss(name: str, where: str) -> None:
        unresolved.setdefault(name, set()).add(where)

    # ---------------------------------------------------------------- herbs
    seed_herbs = {h.id: h for h in kb.herbs.values()}
    seed_processed: dict[str, dict[str, Any]] = {}
    for p in kb.processed.values():
        seed_processed.setdefault(p.herb_id, {})[p.chinese] = p
    herbs: list[dict[str, Any]] = []
    materia_ids = set()
    for mid, m in M.MATERIA.items():
        hid = _herb_id(mid)
        materia_ids.add(hid)
        s = seed_herbs.get(hid)
        reviewed = sorted(n for n, d in M._REVIEWED_ALIASES.items() if d == mid)
        names = [*(s.aliases if s else ()), *seed_processed.get(hid, {}), *m.aliases, *reviewed]
        aliases = type_aliases(s.chinese if s else m.chinese, mid, names,
                               seed_aliases=s.aliases if s else (),
                               seed_processed=seed_processed.get(hid))
        for a in aliases:
            if a["name"] in reviewed and a["name"] not in m.aliases:
                a["reviewed"] = True
        layers: dict[str, str] = {"chinese": "seed" if s else "materia",
                                  "latin": "seed" if s and s.latin else "materia",
                                  "part": "seed" if s and s.part else "materia",
                                  "category": "materia"}
        species_names = list(m.species_names)
        if s:
            extra = [x for x in s.species if x not in species_names]
            species_names += extra
            layers["species"] = "materia+seed" if extra else "materia"
        elif species_names:
            layers["species"] = "materia"
        h: dict[str, Any] = {
            "id": hid, "materia_id": mid, "chinese": s.chinese if s else m.chinese,
            "pinyin": s.pinyin if s else "", "latin": (s.latin if s and s.latin else m.latin),
            "category": m.category, "part": (s.part if s and s.part else m.part_zh),
            "species": _species(species_names, taxa), "aliases": aliases,
            "nature": s.nature if s else "", "flavours": list(s.flavours) if s else [],
            "meridians": list(s.meridians) if s else [], "actions": list(s.actions) if s else [],
            # the seed states each of its herbs' toxicity; nothing else here does unless the
            # clinic pack names it below — an unstated toxicity stays ""
            "toxicity": s.toxicity if s else "", "first_recorded": s.source if s else "",
            "dose": None, "pregnancy": "", "cautions": [], "layers": layers,
            "formula_count": None, "compound_count": None}
        if s:
            for f in ("pinyin", "nature", "flavours", "meridians", "actions", "toxicity"):
                if h[f]:
                    layers[f] = "seed"
            if s.source:
                layers["first_recorded"] = "seed"
        herbs.append(h)
    for hid, s in seed_herbs.items():
        if hid not in materia_ids:                      # a seed herb the materia lacks
            herbs.append({"id": hid, "materia_id": None, "chinese": s.chinese,
                          "pinyin": s.pinyin, "latin": s.latin, "category": "",
                          "part": s.part, "species": _species(s.species, taxa),
                          "aliases": type_aliases(s.chinese, "", s.aliases,
                                                  seed_aliases=s.aliases,
                                                  seed_processed=seed_processed.get(hid)),
                          "nature": s.nature, "flavours": list(s.flavours),
                          "meridians": list(s.meridians), "actions": list(s.actions),
                          "toxicity": s.toxicity, "first_recorded": s.source, "dose": None,
                          "pregnancy": "", "cautions": [], "layers": {"chinese": "seed"},
                          "formula_count": None, "compound_count": None})
    herbs.sort(key=lambda x: x["id"])
    resolver = _Resolver(herbs)
    by_id = {h["id"]: h for h in herbs}

    def alias_of(hid: str, name: str) -> dict[str, Any] | None:
        return next((a for a in by_id[hid]["aliases"] if a["name"] == name), None)

    for name, spec in clinic["herbs"].items():
        hid, how = resolver.resolve(name)
        if hid is None:
            miss(name, "herbs")
            continue
        data: dict[str, Any] = {}
        rng = spec.get("range")
        if isinstance(rng, list) and len(rng) == 2:
            data["dose"] = {"min_g": rng[0], "max_g": rng[1], "note": spec.get("note", "")}
        for key in ("toxicity", "pregnancy"):
            if spec.get(key):
                data[key] = spec[key]
        h = by_id[hid]
        if how in ("name", "synonym"):
            # the clinic pack's entry for the herb itself (or a synonym of it)
            for key, value in data.items():
                if not h[key] or key != "toxicity":
                    h[key] = value
                    h["layers"][key] = "clinic"
            if how == "synonym":
                h["layers"]["clinic_name"] = name
        else:
            # a processed form, part or product: its own data, kept on that name
            a = alias_of(hid, name)
            if a is None:
                a = {"name": name, "kind": "processed" if how == "materia" else how}
                h["aliases"].append(a)
            a["clinic"] = data
    for rule in clinic.get("condition_cautions") or ():
        for name in rule.get("herbs") or ():
            hid, how = resolver.resolve(name)
            if hid is None:
                miss(name, "condition_cautions")
                continue
            text = rule.get("why", "")
            if text and text not in by_id[hid]["cautions"]:
                by_id[hid]["cautions"].append(text)
                by_id[hid]["layers"]["cautions"] = "clinic"

    # ------------------------------------------------------------ syndromes
    syndromes: list[dict[str, Any]] = []
    seed_syn = {s.chinese: s for s in kb.syndromes.values()}
    syn_id: dict[str, str] = {}
    for s in kb.syndromes.values():
        syn_id[s.chinese] = s.id
    for name in clinic["syndromes"]:
        syn_id.setdefault(name, "syndrome.cp_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8])

    # ------------------------------------------------------------- formulas
    formulas: list[dict[str, Any]] = []
    for f in kb.formulas.values():
        ings = []
        for i in f.ingredients:
            herb = by_id.get(i.herb_id)
            p = kb.processed.get(i.processed) if i.processed else None
            ings.append({"herb_id": i.herb_id if herb else None,
                         "name": p.chinese if p else (herb["chinese"] if herb else i.herb_id),
                         "dose": i.dose, "role": i.role,
                         "processing": p.method if p else "", "processed_id": i.processed})
        formulas.append({"id": f.id, "chinese": f.chinese, "pinyin": f.pinyin, "source": f.source,
                         "ingredients": ings, "roles_recorded": True,
                         "indications": list(f.indications), "dosage_form": f.dosage_form,
                         "actions": list(f.actions), "contraindications": list(f.contraindications),
                         "aliases": list(f.aliases), "modifications": [], "notes": f.notes,
                         "layer": "seed"})
    clinic_fid: dict[str, str] = {}
    for name in clinic["formulas"]:
        clinic_fid[name] = "formula.cp_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    seed_processed_by_name = {p.chinese: p for p in kb.processed.values()}
    for name, f in clinic["formulas"].items():
        ings = []
        for herb_name, grams in f.get("herbs") or ():
            hid, how = resolver.resolve(herb_name)
            if hid is None:
                miss(herb_name, "formulas")
            a = alias_of(hid, herb_name) if hid else None
            p = seed_processed_by_name.get(herb_name)
            ings.append({"herb_id": hid, "name": herb_name, "dose": f"{grams:g}g", "role": "",
                         "processing": ((a or {}).get("processing", "")
                                        if how not in ("name", "synonym") else ""),
                         "processed_id": p.id if p else ""})
        indications = sorted(syn_id[s] for s, d in clinic["syndromes"].items()
                             if d.get("formula") == name)
        formulas.append({"id": clinic_fid[name], "chinese": name, "pinyin": "",
                         "source": f.get("source", ""), "ingredients": ings,
                         "roles_recorded": False, "indications": indications,
                         # the pack states no dosage form; 六味地黄丸 is a 丸 by its name,
                         # but the composition here is the textbook's, not a recipe
                         "dosage_form": "", "actions": [], "contraindications": [],
                         "aliases": [], "modifications": list(f.get("modifications") or []),
                         "notes": f.get("note", ""), "layer": "clinic",
                         "citation": ref.get("formulas", "")})
    formulas.sort(key=lambda x: (x["layer"] != "seed", x["id"]))

    for s in kb.syndromes.values():
        c = clinic["syndromes"].get(s.chinese)
        rec: dict[str, Any] = {
            "id": s.id, "chinese": s.chinese, "pinyin": s.pinyin, "english": s.english,
            "category": s.category, "aliases": list(s.aliases),
            # the seed does not split its manifestations into main and secondary
            "manifestations": list(s.manifestations), "main": [], "secondary": [],
            "tongue": [s.tongue] if s.tongue else [], "pulse": [s.pulse] if s.pulse else [],
            "principle": s.treatment_principle,
            "formulas": [{"id": f.id, "name": f.chinese, "layer": "seed"}
                         for f in kb.formulas_for(s.id)],
            "parent": None, "layer": "seed",
            "layers": {k: "seed" for k in ("manifestations", "tongue", "pulse", "principle",
                                           "category")}}
        if c:
            rec["main"], rec["secondary"] = list(c.get("main") or []), list(c.get("secondary") or [])
            rec["layers"].update(main="clinic", secondary="clinic")
            if c.get("parent"):
                rec["parent"] = syn_id.get(c["parent"])
                rec["layers"]["parent"] = "clinic"
            if c.get("formula"):
                rec["formulas"].append({"id": clinic_fid.get(c["formula"]), "name": c["formula"],
                                        "layer": "clinic"})
            # where the clinic pack words them differently, its version is kept beside
            # the seed's, not merged into it
            variant = {k: v for k, v in (("tongue", list(c.get("tongue") or [])),
                                         ("pulse", list(c.get("pulse") or [])),
                                         ("principle", c.get("principle", "")))
                       if v and v != rec[k]}
            if variant:
                rec["clinic"] = variant
        syndromes.append(rec)
    for name, c in clinic["syndromes"].items():
        if name in seed_syn:
            continue
        syndromes.append({
            "id": syn_id[name], "chinese": name, "pinyin": "", "english": "", "category": "",
            "aliases": [], "manifestations": [], "main": list(c.get("main") or []),
            "secondary": list(c.get("secondary") or []), "tongue": list(c.get("tongue") or []),
            "pulse": list(c.get("pulse") or []), "principle": c.get("principle", ""),
            "formulas": ([{"id": clinic_fid.get(c["formula"]), "name": c["formula"],
                           "layer": "clinic"}] if c.get("formula") else []),
            "parent": syn_id.get(c["parent"]) if c.get("parent") else None,
            "layer": "clinic", "citation": ref.get("criteria", ""),
            "layers": {k: "clinic" for k in ("main", "secondary", "tongue", "pulse", "principle")}})
    syndromes.sort(key=lambda x: (x["layer"] != "seed", x["id"]))

    # --------------------------------------------------------------- safety
    def party(eid: str | None, name: str) -> dict[str, Any]:
        return {"id": eid, "name": name}

    safety: list[dict[str, Any]] = []
    names_of = {h["id"]: h["chinese"] for h in herbs}
    names_of.update({f["id"]: f["chinese"] for f in formulas})
    for r in kb.safety.values():
        safety.append({"id": r.id, "kind": r.kind,
                       "subject": party(r.subject_id, names_of.get(r.subject_id, r.subject_id)),
                       "counterpart": (party(r.counterpart_id,
                                             names_of.get(r.counterpart_id, r.counterpart_id))
                                       if r.counterpart_id else None),
                       "rule": r.description, "severity": r.severity, "population": r.population,
                       "note": r.management, "evidence_tier": r.tier.name,
                       "evidence_ids": list(r.evidence_ids),
                       "citation": _citation_of(kb, r.evidence_ids), "layer": "seed"})
    clinic_cite = f"{ref.get('herbs', '')}（{pack_note}）"
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for rule in clinic.get("incompatible") or ():
        rk = {"十八反": "shibafan", "十九畏": "shijiuwei"}.get(rule["rule"], "rule")
        for a in rule.get("a") or ():
            ia, _ = resolver.resolve(a)
            if ia is None:
                miss(a, "incompatible")
            for b in rule.get("b") or ():
                ib, _ = resolver.resolve(b)
                if ib is None:
                    miss(b, "incompatible")
                if ia is not None and ia == ib:
                    continue
                key = (rule["rule"], ia or a, ib or b)
                rec = grouped.get(key)
                if rec is None:
                    rid = "safety.cp_{}_{}".format(rk, hashlib.sha1(
                        "|".join(key).encode("utf-8")).hexdigest()[:8])
                    rec = grouped[key] = {
                        "id": rid, "kind": "incompatibility",
                        "subject": {**party(ia, names_of.get(ia, a) if ia else a), "written": []},
                        "counterpart": {**party(ib, names_of.get(ib, b) if ib else b),
                                        "written": []},
                        "rule": f"{rule['rule']}：{a}—{b}", "severity": "unstated",
                        "population": "", "note": "", "evidence_tier": "EXPERT_EXPERIENCE",
                        "evidence_ids": [], "citation": f"{rule.get('source', '')}（{pack_note}）",
                        "layer": "clinic"}
                for side, name in (("subject", a), ("counterpart", b)):
                    if name not in rec[side]["written"]:
                        rec[side]["written"].append(name)
    for rec in grouped.values():
        s, c = rec["subject"], rec["counterpart"]
        rec["rule"] = (f"{rec['rule'].split('：', 1)[0]}：{'/'.join(s['written'])}—"
                       f"{'/'.join(c['written'])}")
    safety += list(grouped.values())
    for name, spec in clinic["herbs"].items():
        hid, how = resolver.resolve(name)
        if hid is None:
            continue
        subject = {**party(hid, names_of[hid]), "written": [name]}
        slug = hid.split(".", 1)[1] + ("" if how in ("name", "synonym") else
                                      "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6])
        if spec.get("pregnancy"):
            safety.append({"id": f"safety.cp_preg_{slug}",
                           "kind": "contraindication" if spec["pregnancy"] == "禁用" else "caution",
                           "subject": subject, "counterpart": None,
                           "rule": f"孕妇{spec['pregnancy']}", "severity": "unstated",
                           "population": "孕妇", "note": "", "evidence_tier": "EXPERT_EXPERIENCE",
                           "evidence_ids": [], "citation": clinic_cite, "layer": "clinic"})
        if spec.get("toxicity"):
            safety.append({"id": f"safety.cp_tox_{slug}", "kind": "toxicity",
                           "subject": dict(subject, written=[name]), "counterpart": None,
                           "rule": spec["toxicity"], "severity": "unstated", "population": "",
                           "note": spec.get("note", ""), "evidence_tier": "EXPERT_EXPERIENCE",
                           "evidence_ids": [], "citation": clinic_cite, "layer": "clinic"})
    def per_herb(rule_names: Iterable[str], where: str) -> dict[str, dict[str, Any]]:
        """One subject per herb a rule names (甘草 and 炙甘草 are one subject, both written)."""
        out: dict[str, dict[str, Any]] = {}
        for name in rule_names:
            hid, _ = resolver.resolve(name)
            if hid is None:
                miss(name, where)
            key = hid or name
            subject = out.setdefault(key, {**party(hid, names_of.get(hid, name) if hid else name),
                                           "written": []})
            subject["written"].append(name)
        return out

    def slug_of(key: str) -> str:
        return key.split(".", 1)[1] if key.startswith("herb.") else \
            "x" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]

    for n, rule in enumerate(clinic.get("drug_interactions") or ()):
        drugs = [d for d in rule.get("drugs") or ()]
        zh = [d for d in drugs if re.search(r"[一-鿿]", d)] or drugs
        for key, subject in per_herb(rule.get("herbs") or (), "drug_interactions").items():
            safety.append({"id": f"safety.cp_ddi{n + 1}_{slug_of(key)}", "kind": "interaction",
                           "subject": subject,
                           "counterpart": {"id": None, "name": "、".join(zh), "written": drugs},
                           "rule": rule.get("risk", ""), "severity": "unstated", "population": "",
                           "note": "", "evidence_tier": "EXPERT_EXPERIENCE", "evidence_ids": [],
                           "citation": f"{rule.get('source', '')}（{pack_note}）",
                           "layer": "clinic"})
    for n, rule in enumerate(clinic.get("condition_cautions") or ()):
        conds = list(rule.get("conditions") or ())
        zh = [c for c in conds if re.search(r"[一-鿿]", c)] or conds
        for key, subject in per_herb(rule.get("herbs") or (), "condition_cautions").items():
            safety.append({"id": f"safety.cp_cond{n + 1}_{slug_of(key)}", "kind": "caution",
                           "subject": subject, "counterpart": None, "rule": rule.get("why", ""),
                           "severity": "unstated", "population": "、".join(zh), "note": "",
                           "evidence_tier": "EXPERT_EXPERIENCE", "evidence_ids": [],
                           "citation": f"{rule.get('source', '')}（{pack_note}）",
                           "layer": "clinic"})
    ids = [r["id"] for r in safety]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise BuildError(f"core: duplicate safety record ids {dup[:5]}")
    safety.sort(key=lambda x: (x["layer"] != "seed", x["id"]))

    # ------------------------------------------------------- counts and index
    for h in herbs:
        if formula_counts is not None and h["materia_id"]:
            h["formula_count"] = int(formula_counts.get(h["materia_id"], 0))
        if compound_counts is not None and h["materia_id"]:
            # null when LOTUS matched no organism of the herb (lotus/unmatched.json says why)
            n = compound_counts.get(h["materia_id"])
            h["compound_count"] = int(n) if n is not None else None
    names: dict[str, list[list[str]]] = {}

    def put(name: str, kind: str, eid: str) -> None:
        if name and [kind, eid] not in names.setdefault(name, []):
            names[name].append([kind, eid])

    canonical = {h["chinese"] for h in herbs}
    shadowed = 0
    for h in herbs:
        put(h["chinese"], "herb", h["id"])
        for extra in (h["pinyin"], h["latin"]):
            put(extra, "herb", h["id"])
        for a in h["aliases"]:
            if a["kind"] != "synonym":
                continue
            if a["name"] in canonical and a["name"] != h["chinese"]:
                a["shadowed"] = True            # another herb's own name: never resolves here
                shadowed += 1
                continue
            put(a["name"], "herb", h["id"])
    for s in syndromes:
        for n in (s["chinese"], s["pinyin"], s["english"], *s["aliases"]):
            put(n, "syndrome", s["id"])
    for f in formulas:
        for n in (f["chinese"], f["pinyin"], *f["aliases"]):
            put(n, "formula", f["id"])
    for v in names.values():
        v.sort()

    passages = [p.as_dict() for p in kb.passages.values()]
    passages.sort(key=lambda x: x["id"])
    studies = [s.as_dict() for s in kb.studies.values()]
    payload = {"herbs": herbs, "syndromes": syndromes, "formulas": formulas, "safety": safety,
               "passages": passages, "studies": studies, "names": names,
               "unresolved": [{"name": n, "where": sorted(w)} for n, w in sorted(unresolved.items())],
               "clinic_pack": {"version": clinic["version"], "review_status": clinic["review_status"],
                               "reference": ref}}
    alias_kinds: dict[str, int] = {}
    for h in herbs:
        for a in h["aliases"]:
            alias_kinds[a["kind"]] = alias_kinds.get(a["kind"], 0) + 1
    seed_n = len(seed_herbs)
    report = {
        "counts": {"herbs": len(herbs), "herbs_seed": seed_n,
                   "herbs_with_clinic_data": sum(1 for h in herbs if any(
                       v == "clinic" for v in h["layers"].values())),
                   "aliases": alias_kinds, "aliases_shadowed": shadowed,
                   "syndromes": len(syndromes),
                   "syndromes_seed": sum(1 for s in syndromes if s["layer"] == "seed"),
                   "formulas": len(formulas),
                   "formulas_seed": sum(1 for f in formulas if f["layer"] == "seed"),
                   "safety": len(safety),
                   "safety_by_kind": _count(r["kind"] for r in safety),
                   "passages": len(passages), "names": len(names)},
        "unresolved": payload["unresolved"],
        "sources": [
            {"key": "tcmscience-seed", "name": "TCMScience seed knowledge base",
             "version": "bioagent " + _version("bioagent"), "url":
                 _REPO + "/tree/main/BioScience-Harness/src/bioagent/tcm",
             "licence": "MIT", "retrieved": P.DATA_DATE,
             "citation": "bioagent.tcm.knowledge.seed()", "sha256": _seed_hash()},
            {"key": "tcmscience-materia", "name": "TCMScience materia table",
             "version": "bioagent " + _version("bioagent"), "url":
                 _REPO + "/blob/main/BioScience-Harness/src/bioagent/sources/materia.py",
             "licence": "MIT", "retrieved": P.DATA_DATE,
             "citation": "bioagent.sources.materia.MATERIA",
             "sha256": _file_sha(M.__file__)},
            {"key": "ncbi-taxonomy", "name": "NCBI Taxonomy (via registry/materia_taxa.json)",
             "version": str(_taxa_checked()), "url": "https://www.ncbi.nlm.nih.gov/taxonomy",
             "licence": "public domain (NCBI Taxonomy)", "retrieved": str(_taxa_checked()),
             "citation": "Schoch C.L. et al., Database 2020, doi:10.1093/database/baaa062",
             "sha256": _file_sha(M.TAXA_FILE)},
            {"key": "tcmscience-clinic-pack", "name": "TCMScience clinic knowledge pack",
             "version": clinic["version"], "url":
                 _REPO + "/blob/main/BioScience-Harness/src/bioagent/data/clinic_pack.json",
             "licence": "MIT", "retrieved": P.DATA_DATE,
             "citation": "；".join(str(v) for k, v in ref.items() if k != "response"),
             "sha256": hashlib.sha256(raw_pack).hexdigest(),
             "review_status": clinic["review_status"]},
        ]}
    return payload, report


def _count(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return dict(sorted(out.items()))


def _version(dist: str) -> str:
    from ..envelope import package_version
    return package_version(dist)


def _file_sha(path: Any) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def _seed_hash() -> str | None:
    try:
        from bioagent.tcm.knowledge import __file__ as kf
        return _file_sha(kf)
    except Exception:                                           # noqa: BLE001
        return None


def _taxa_checked() -> str:
    from bioagent.sources.materia import TAXA_FILE
    try:
        return str(json.loads(Path(TAXA_FILE).read_text(encoding="utf-8")).get("checked_at") or "")
    except (OSError, ValueError):
        return ""


def _core_pack(formula_counts: Mapping[str, int] | None,
               compound_counts: Mapping[str, int] | None, log: Log) -> _Pack:
    payload, report = core_payload(formula_counts=formula_counts, compound_counts=compound_counts)
    pack = _Pack("core")
    pack.add("core/core.json", payload)
    pack.counts = report["counts"]
    pack.sources = report["sources"]
    pack.extra["unresolved"] = report["unresolved"]
    c = report["counts"]
    log(f"  core: {c['herbs']} herbs ({c['herbs_seed']} seed), {c['syndromes']} syndromes, "
        f"{c['formulas']} curated formulas, {c['safety']} safety records, {c['names']} names")
    if report["unresolved"]:
        log("  core: clinic-pack names that match no materia entry (kept by name, reported): "
            + "、".join(u["name"] for u in report["unresolved"]))
    return pack


# =================================================================== formulas pack

def default_xlsx() -> Path:
    from bioagent.sources.formulas import TABLE_FILE
    return Path(TABLE_FILE)


def _parser_digest() -> str:
    """The parser's source and this file's, so a change in how compositions resolve or in
    how the objects are laid out rebuilds the cache."""
    from bioagent.sources import formulas, materia
    h = hashlib.sha256()
    for path in (formulas.__file__, materia.__file__, __file__):
        h.update(Path(path).read_bytes())
    return h.hexdigest()


def _cache_root() -> Path:
    base = os.environ.get("TCMSTUDIO_CACHE")
    return (Path(base).expanduser() if base else Path("~/.cache/tcmstudio").expanduser()) / \
        "corpus-build"


_BOOK = re.compile(r"《([^》]+)》")


def _formula_rows(path: Path) -> list[Any]:
    from bioagent.sources.formulas import load_formula_table
    return list(load_formula_table(path).records)


def _formulas_pack(xlsx: Path, log: Log, *, cache: bool = True) -> _Pack:
    digest = _sha256_file(xlsx)
    key = hashlib.sha256(f"{BUILD_VERSION}\n{digest}\n{_parser_digest()}".encode()).hexdigest()[:24]
    source = {"key": "formula-table", "name": "中医方剂数据表", "version": xlsx.name,
              "url": _REPO + "/blob/main/" + quote(xlsx.name),
              "sha256": digest, "retrieved": P.DATA_DATE,
              "licence": P.OWNER_DECISION_FORMULAS["licence"],
              "citation": "中医方剂数据表.xlsx（原表未声明来源与许可）"}
    cdir = _cache_root() / f"formulas-{key}"
    pack = _Pack("formulas")
    pack.sources = [source]
    if cache and (cdir / "meta.json").is_file():
        try:
            meta = json.loads((cdir / "meta.json").read_text(encoding="utf-8"))
            for path, info in meta["objects"].items():
                gz = (cdir / "o" / info["file"]).read_bytes()
                if hashlib.sha256(gz).hexdigest() != info["sha256"]:
                    raise ValueError(f"cached {path} does not match its hash")
                pack.add_bytes(path, info["raw"], gz)
            pack.counts = meta["counts"]
            pack.extra["per_herb"] = meta["per_herb"]
            log(f"  formulas: {pack.counts['rows']:,} rows from the build cache ({cdir})")
            return pack
        except (OSError, ValueError, KeyError) as exc:
            log(f"  formulas: the build cache is unusable ({exc}); rebuilding")
            pack = _Pack("formulas")
            pack.sources = [source]
    log(f"  formulas: reading {xlsx.name} (about 20 s)")
    records = _formula_rows(xlsx)
    _formulas_objects(pack, records)
    log(f"  formulas: {pack.counts['rows']:,} rows, {pack.counts['resolved_rows']:,} fully "
        f"resolved, {len(pack.objects)} objects")
    if cache:
        try:
            tmp = cdir.with_name(cdir.name + f".tmp-{secrets.token_hex(4)}")
            (tmp / "o").mkdir(parents=True)
            meta = {"version": BUILD_VERSION, "xlsx_sha256": digest, "counts": pack.counts,
                    "per_herb": pack.extra["per_herb"], "objects": {}}
            for path, obj in pack.objects.items():
                name = obj.sha256 + ".gz"
                (tmp / "o" / name).write_bytes(obj.gz)
                meta["objects"][path] = {"file": name, "sha256": obj.sha256, "raw": obj.raw}
            (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            if cdir.exists():
                shutil.rmtree(cdir, ignore_errors=True)
            tmp.rename(cdir)
            # each change of the table or of the code is a new entry: keep the newest few
            entries = sorted((p for p in cdir.parent.glob("formulas-*") if ".tmp-" not in p.name),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            for old in entries[_CACHE_KEEP:]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError as exc:
            log(f"  formulas: could not write the build cache ({exc})")
    return pack


def _formulas_objects(pack: _Pack, records: list[Any]) -> None:
    """Rows, index, by-herb postings, id shards and stats from parsed ``FormulaRecord``s."""
    rows: list[dict[str, Any]] = []
    names: list[str] = []
    source_count: dict[str, int] = {}
    postings: dict[str, list[int]] = {}
    ids: dict[str, list[int]] = {}
    books: dict[str, int] = {}
    components = resolved_components = resolved_rows = empty = 0
    for rec in records:
        rowno = rec.row - 2                                   # 0-based data row of the sheet
        while len(rows) < rowno:
            # a sheet row with neither name nor composition: kept as an empty row, so that
            # rowno stays the row's place in the sheet
            rows.append({"rowno": len(rows), "empty": True, "name": "", "source": ""})
            names.append("")
            source_count[""] = source_count.get("", 0) + 1
            empty += 1
        comps = []
        drugs = set()
        for c in rec.components:
            comps.append([c.name or c.written, c.dose, c.processing, c.drug])
            components += 1
            if c.drug:
                resolved_components += 1
                drugs.add(c.drug)
        if rec.resolved:
            resolved_rows += 1
        row = {"rowno": rowno, "id": rec.id, "name": rec.name, "source": rec.source,
               "composition": rec.composition, "components": comps,
               "preparation": rec.preparation, "indications": rec.actions,
               "usage": rec.usage, "cautions": rec.cautions}
        if rec.written_name != rec.name:
            row["written_name"] = rec.written_name
        rows.append(row)
        names.append(rec.name)
        source_count[rec.source] = source_count.get(rec.source, 0) + 1
        for d in drugs:
            postings.setdefault(d, []).append(rowno)
        ids.setdefault(rec.id, []).append(rowno)
        m = _BOOK.search(rec.source or "")
        book = m.group(1) if m else ""
        if book:
            books[book] = books.get(book, 0) + 1
    # The most frequent 出处 get the smallest ids: the index is fetched by every formula
    # lookup, and short ids make it about 5% smaller gzipped (measured: 456 KB, not 482 KB).
    sources = sorted(source_count, key=lambda s: (-source_count[s], s))
    source_index = {s: i for i, s in enumerate(sources)}
    source_ids = [source_index[r["source"]] for r in rows]
    for start in range(0, len(rows), ROWS_PER_CHUNK):
        pack.add(f"formulas/rows/{start // ROWS_PER_CHUNK:04d}.json",
                 {"start": start, "rows": rows[start:start + ROWS_PER_CHUNK]})
    pack.add("formulas/index.json", {"names": names, "source_ids": source_ids, "sources": sources})
    for drug, rownos in sorted(postings.items()):
        deltas = [rownos[0]] + [b - a for a, b in zip(rownos, rownos[1:])]
        pack.add(f"formulas/by-herb/{drug}.json",
                 {"herb": drug, "count": len(rownos), "rows_delta": deltas})
    shards: dict[str, dict[str, list[int]]] = {}
    for fid, rownos in ids.items():
        h = fid.rsplit(".fx", 1)[-1][:1] or "0"
        shards.setdefault(h, {})[fid] = rownos
    for h in "0123456789abcdef"[:ID_SHARDS]:
        pack.add(f"formulas/ids/{h}.json", shards.get(h, {}))
    from bioagent.sources.materia import MATERIA
    per_herb = {d: len(r) for d, r in sorted(postings.items())}
    top = sorted(books.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
    stats = {"rows": len(rows) - empty, "sheet_rows": len(rows), "empty_rows": empty,
             "resolved_rows": resolved_rows,
             "components": components,
             "resolved_components": resolved_components, "drugs_used": len(postings),
             "drugs_in_table": len(MATERIA), "distinct_names": len(set(names)),
             "distinct_sources": len(sources),
             "duplicate_ids": sum(1 for r in ids.values() if len(r) > 1),
             "rows_per_chunk": ROWS_PER_CHUNK, "per_herb": per_herb,
             "top_books": [[b, n] for b, n in top]}
    pack.add("formulas/stats.json", stats)
    pack.counts = {k: stats[k] for k in ("rows", "sheet_rows", "resolved_rows", "components",
                                         "resolved_components", "drugs_used", "distinct_names",
                                         "distinct_sources", "duplicate_ids")}
    pack.extra["per_herb"] = per_herb


# ============================================================ lotus and pathways

def default_data_dir() -> Path:
    """``studio/corpus/data`` of this source tree (absent in an installed wheel)."""
    env = os.environ.get("TCMSTUDIO_CORPUS_DATA")
    return Path(env).expanduser() if env else _STUDIO / "corpus" / "data"


def _read_json_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


_SOURCE_KEYS = ("key", "name", "version", "url", "sha256", "retrieved", "licence", "licence_url",
                "citation", "doi", "dataset_doi", "changes")


def _source_entries(doc: Mapping[str, Any], default_key: str) -> list[dict[str, Any]]:
    """The ``sources[]`` of a pack from an extract's own record: what it is, its version,
    upstream URL and hash, licence, citation, and what was changed (CC BY asks for it)."""
    raw = doc.get("sources")
    if raw is None and doc.get("source") is not None:
        raw = [doc["source"]]
    items = [s for s in (raw if isinstance(raw, list) else [raw]) if isinstance(s, Mapping)]
    out = []
    for s in items:
        e = {k: s[k] for k in _SOURCE_KEYS if s.get(k) not in (None, "")}
        if not e.get("licence") and s.get("license"):
            e["licence"] = s["license"]
        if "key" not in e:
            e["key"] = s.get("id") or (default_key if len(items) == 1 else s.get("name"))
        if "changes" not in e and isinstance(doc.get("changes"), str):
            e["changes"] = doc["changes"]
        out.append(e)
    return out


def _lotus_pack(path: Path, log: Log) -> _Pack:
    from bioagent.sources.materia import MATERIA
    doc = _read_json_gz(path)
    if doc.get("schema") != "tcmstudio.corpus.lotus/1":
        raise BuildError(f"{path}: schema {doc.get('schema')!r}, expected tcmstudio.corpus.lotus/1")
    pack = _Pack("lotus")
    pack.sources = _source_entries(doc, "lotus")
    P.check_sources("lotus", pack.sources)
    compounds = doc.get("compounds") or {}
    counts: dict[str, int] = {}
    occurrences = 0
    seen: set[str] = set()
    skipped = []
    for mid, herb in sorted((doc.get("herbs") or {}).items()):
        if mid not in MATERIA:
            skipped.append(mid)
            continue
        rows = []
        for ik, refs in herb.get("occurrences") or ():
            c = compounds.get(ik) or {}
            rows.append({"ik": ik, "name": c.get("name", ""), "formula": c.get("formula", ""),
                         "smiles": c.get("smiles", ""), "refs": int(refs or 0)})
            seen.add(ik)
        rows.sort(key=lambda r: (-r["refs"], r["ik"]))
        organisms = [{"name": o.get("name", ""), "taxid": (str(o["taxid"]) if o.get("taxid")
                                                          not in (None, "") else None),
                      "matched_by": o.get("matched_by", "")}
                     for o in herb.get("organisms") or () if isinstance(o, Mapping)]
        pack.add(f"lotus/{mid}.json", {"herb": mid, "organisms": organisms, "compounds": rows})
        counts[mid] = len(rows)
        occurrences += len(rows)
    if skipped:
        log(f"  lotus: {len(skipped)} herb key(s) not in the materia table skipped: "
            + ", ".join(skipped[:10]))
    # why a herb has no LOTUS record: not an organism (a mineral), or no LOTUS organism
    # matched its species — absence here is not absence of constituents
    unmatched = {mid: {k: v for k, v in (u or {}).items() if k in ("category", "reason", "species")}
                 for mid, u in sorted((doc.get("unmatched") or {}).items()) if mid in MATERIA}
    pack.add("lotus/unmatched.json", unmatched)
    pack.counts = {"herbs": len(counts), "herbs_with_compounds": sum(1 for v in counts.values() if v),
                   "herbs_unmatched": len(unmatched), "compounds": len(seen),
                   "occurrences": occurrences}
    pack.extra["per_herb"] = counts
    log(f"  lotus: {pack.counts['herbs']} herbs, {pack.counts['compounds']:,} compounds, "
        f"{occurrences:,} occurrences")
    return pack


def _pathways_pack(path: Path, log: Log) -> _Pack:
    doc = _read_json_gz(path)
    if doc.get("schema") != "tcmstudio.corpus.pathways/1":
        raise BuildError(f"{path}: schema {doc.get('schema')!r}, expected tcmstudio.corpus.pathways/1")
    pack = _Pack("pathways")
    pack.sources = _source_entries(doc, "reactome")
    P.check_sources("pathways", pack.sources)

    def version(key: str) -> str:
        for s in pack.sources:
            if P.source_key(s.get("key")) == key or key in P.source_key(s.get("name")):
                return str(s.get("version") or "")
        return ""

    pathways = doc.get("pathways") or {}
    pack.add("pathways/reactome.json", {"version": version("reactome"), "pathways": pathways})
    s2u = doc.get("symbol_to_uniprot") or {}
    u2s = doc.get("uniprot_to_symbol")
    if u2s is None:
        inv: dict[str, list[str]] = {}
        for sym, acc in s2u.items():
            for a in (acc if isinstance(acc, list) else [acc]):
                inv.setdefault(str(a), []).append(sym)
        u2s = {a: (sorted(v) if len(v) > 1 or any(isinstance(x, list) for x in s2u.values())
                   else v[0]) for a, v in inv.items()}
    hgnc = {"version": version("hgnc"), "symbol_to_uniprot": s2u, "uniprot_to_symbol": u2s}
    # the extract's own ambiguity records travel with it: a symbol with several accessions
    # and an accession several symbols claim are listed, never resolved by guessing
    for key in ("symbol_to_uniprot_all", "uniprot_to_symbols_ambiguous"):
        if isinstance(doc.get(key), Mapping):
            hgnc[key] = doc[key]
    pack.add("pathways/hgnc.json", hgnc)
    members = {m for p in pathways.values() for m in (p.get("members") or ())}
    pack.counts = {"pathways": len(pathways), "proteins": len(members), "symbols": len(s2u)}
    log(f"  pathways: {len(pathways):,} pathways, {len(members):,} proteins, "
        f"{len(s2u):,} HGNC symbols")
    return pack


# ======================================================================== assembly

def _manifest(built: list[_Pack], skipped: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, _Object]]:
    objects: dict[str, _Object] = {}
    for pack in built:
        objects.update(pack.objects)
    lines = "".join(f"{p} {o.sha256}\n" for p, o in sorted(objects.items()))
    sha12 = hashlib.sha256(lines.encode("utf-8")).hexdigest()[:12]
    snapshot = f"tcmcorpus-{P.DATA_DATE.replace('-', '.')}-{sha12}"
    packs: dict[str, Any] = {}
    for pack in built:
        meta = P.pack_meta(pack.name)
        size = sum(o.gz.__len__() for o in pack.objects.values())
        largest = max(pack.objects.values(), key=lambda o: (len(o.gz), o.path))
        meta.update(counts=pack.counts, sources=pack.sources,
                    objects=len(pack.objects), bytes=size,
                    raw_bytes=sum(o.raw for o in pack.objects.values()),
                    largest={"path": largest.path, "bytes": len(largest.gz)})
        if pack.extra.get("unresolved"):
            meta["unresolved"] = pack.extra["unresolved"]
        packs[pack.name] = meta
    unique = {o.sha256: len(o.gz) for o in objects.values()}
    largest = max(objects.values(), key=lambda o: (len(o.gz), o.path))
    manifest = {
        "schema": P.SCHEMA, "snapshot_id": snapshot, "data_date": P.DATA_DATE,
        "packs": packs, "skipped": dict(sorted(skipped.items())),
        "objects": {p: {"url": object_url(o.sha256), "sha256": o.sha256, "bytes": len(o.gz),
                        "raw_bytes": o.raw, "pack": o.pack} for p, o in sorted(objects.items())},
        "totals": {"objects": len(objects), "files": len(unique), "bytes": sum(unique.values()),
                   "raw_bytes": sum(o.raw for o in objects.values()),
                   "largest": {"path": largest.path, "bytes": len(largest.gz)}},
        "attribution": {"page": "../attribution.html", "markdown": "ATTRIBUTION.md",
                        "url": P.ATTRIBUTION_URL},
        "builder": {"build": BUILD_VERSION, "tcmstudio": _version("tcmstudio"),
                    "bioagent": _version("bioagent")},
    }
    return manifest, objects


def build_corpus(out_dir: str | os.PathLike[str], *, xlsx: str | os.PathLike[str] | None = None,
                 data_dir: str | os.PathLike[str] | None = None, cache: bool = True,
                 packs: Iterable[str] = P.PACK_ORDER,
                 log: Log | None = None) -> dict[str, Any]:
    """Build every pack and write ``out_dir/corpus/`` and ``out_dir/attribution.html``.

    ``xlsx`` defaults to the repository's formula table and ``data_dir`` to
    ``studio/corpus/data``; a pack whose input is absent is skipped with a warning and
    listed under ``skipped`` in the manifest. A gate failure (``packs.GateError``) or an
    object over ``MAX_OBJECT_BYTES`` stops the build. Returns a summary."""
    from .attribution import attribution_html, attribution_md

    say = log or (lambda _m: None)
    wanted = [p for p in P.PACK_ORDER if p in set(packs)]
    out = Path(out_dir)
    data = Path(data_dir) if data_dir is not None else default_data_dir()
    built: dict[str, _Pack] = {}
    skipped: dict[str, str] = {}

    if "formulas" in wanted:
        table = Path(xlsx) if xlsx is not None else default_xlsx()
        if not table.is_file():
            skipped["formulas"] = f"{table.name} is absent"
            say(f"  warning: formulas pack skipped: {table} is absent")
        else:
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                skipped["formulas"] = "openpyxl is not installed (pip install 'bioagent[formulas]')"
                say("  warning: formulas pack skipped: openpyxl is not installed")
            else:
                P.check_sources("formulas", [{"key": "formula-table", "name": "中医方剂数据表",
                                              "licence": P.OWNER_DECISION_FORMULAS["licence"]}])
                built["formulas"] = _formulas_pack(table, say, cache=cache)
    for name, filename, step in (("lotus", "lotus-herbs.json.gz", _lotus_pack),
                                 ("pathways", "pathways.json.gz", _pathways_pack)):
        if name not in wanted:
            continue
        path = data / filename
        if not path.is_file():
            skipped[name] = f"studio/corpus/data/{filename} is absent"
            say(f"  warning: {name} pack skipped: {path} is absent")
            continue
        built[name] = step(path, say)
    if "core" in wanted:
        core = _core_pack(built["formulas"].extra["per_herb"] if "formulas" in built else None,
                          built["lotus"].extra["per_herb"] if "lotus" in built else None, say)
        P.check_sources("core", core.sources)
        built["core"] = core
    ordered = [built[p] for p in P.PACK_ORDER if p in built]
    if not ordered:
        raise BuildError("no corpus pack could be built")
    for pack in ordered:
        P.check_sources(pack.name, pack.sources)
    manifest, objects = _manifest(ordered, skipped)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")) + "\n").encode("utf-8")
    msha = hashlib.sha256(manifest_bytes).hexdigest()
    mname = f"manifest.{msha[:12]}.json"
    latest = {"schema": P.SCHEMA, "snapshot_id": manifest["snapshot_id"], "manifest": mname,
              "sha256": msha}

    corpus_dir = out / "corpus"
    (corpus_dir / "o").mkdir(parents=True, exist_ok=True)
    written = set()
    for o in objects.values():
        url = object_url(o.sha256)
        if url in written:
            continue
        written.add(url)
        target = corpus_dir / url
        if not (target.is_file() and target.read_bytes() == o.gz):
            target.write_bytes(o.gz)
    (corpus_dir / mname).write_bytes(manifest_bytes)
    (corpus_dir / "latest.json").write_bytes(
        (json.dumps(latest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    (corpus_dir / "ATTRIBUTION.md").write_bytes(attribution_md(manifest).encode("utf-8"))
    (out / "attribution.html").write_bytes(attribution_html(manifest).encode("utf-8"))
    t = manifest["totals"]
    say(f"  corpus {manifest['snapshot_id']}: {t['objects']} objects ({t['files']} files), "
        f"{t['bytes']:,} bytes gzipped ({t['raw_bytes']:,} raw); largest "
        f"{t['largest']['path']} {t['largest']['bytes']:,} bytes")
    return {"snapshot_id": manifest["snapshot_id"], "manifest": mname, "sha256": msha,
            "dir": str(corpus_dir), "totals": t, "skipped": skipped,
            "packs": {k: {"objects": v["objects"], "bytes": v["bytes"], "counts": v["counts"],
                          "largest": v["largest"]} for k, v in manifest["packs"].items()}}


def build_site_corpus(staging: str | os.PathLike[str], *, log: Log | None = None) -> dict[str, Any]:
    """The webbuild hook: build the corpus into the staged site and return boot.json's
    ``corpus`` entry (``{schema, snapshot_id, manifest, sha256, base}``, paths relative to
    the site root)."""
    say = log or (lambda _m: None)
    say("building the corpus (docs/V2.md §11)")
    summary = build_corpus(staging, log=say)
    return {"schema": P.SCHEMA, "snapshot_id": summary["snapshot_id"],
            "manifest": f"corpus/{summary['manifest']}", "sha256": summary["sha256"],
            "base": "corpus/"}


def runtime_boot_entry() -> dict[str, Any]:
    """boot.json's ``corpus`` entry for the page a runner serves: it reads the public corpus
    (``/corpus/*`` allows cross-origin reads)."""
    return {"schema": P.SCHEMA, "latest_url": P.LATEST_URL}
