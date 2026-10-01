"""When several databases offer the same kind of relation: reconcile, never just vote.

Herb→ingredient, ingredient→target and the other relation kinds come from several
databases at once. Counting "how many databases say so" fails for three reasons, each
measured on the stores built on 2026-10-01 (``docs/tcm-data-sources.md`` gives the
numbers):

1. **The same thing has different names.** One compound is ``pubchem:5280343`` in
   ITCM, ``inchikey:REFJ…`` in TCMIO, ``herb2:HBIN041495`` in HERB and
   ``symmap:SMIT00013`` in SymMap. One gene is ``ncbigene:7157``, ``uniprot:P04637`` and
   ``ensembl:ENSP00000269305``. Before anything is compared, ids are mapped to one
   identity (:class:`Crosswalk`); what cannot be mapped stays separate and is counted as
   unmapped.
2. **Databases copy each other.** ITCM and TM-MC share 3,406 of quercetin's targets
   (Jaccard 0.82): one upstream (STITCH-style text mining and database integration)
   seen twice. BATMAN-TCM's and dbPTH's "known" targets overlap heavily because both
   take from HIT. Agreement between two copies is not replication. Each row is traced
   to its **lineage**: the primary resource it ultimately rests on. That is the cited
   paper when there is one, the upstream source a database names for the row (HERB,
   TM-MC, BATMAN-TCM 1.0), the model for a prediction, or else the database itself.
   Pairs of databases whose object sets for the same subjects overlap beyond a threshold
   are additionally treated as one lineage, because undocumented copying shows up as
   overlap.
3. **Evidence kinds do not add up.** Ten predictions are not one measurement, and a
   database's integration of other databases is not a new observation. Support is
   reported per evidence kind, and the class of an assertion is decided by its best
   *kind* first and by independent lineages second, never by a raw count.

Two more distinctions follow:

* **Silence is not absence.** A database that covers the subject but does not list the
  object is *silent*; one that does not cover the subject at all *does not apply*. Only
  the first is weak evidence of absence, and the report keeps them apart.
* **An ambiguous name is not merged.** When a name resolves to several entities in one
  source (HERB has two herbs called "Huang Qi"), they are reported as ambiguous instead of
  being pooled.

The result is an :class:`Assertion` per distinct (subject, object) pair. It lists every
source, its evidence, references and scores, the independent lineages, the silent
sources, and a ``support`` class:

``independently_replicated``
    known or reported evidence from at least two independent lineages;
``documented``
    known or reported evidence from one lineage;
``associated``
    a statistical association (eQTL, GWAS, correlation) and no direct observation;
``integrated``
    only aggregated or listed rows (database integrations, composition lists);
``mentioned``
    only text-mined co-mentions (an abstract naming both);
``predicted``
    only model output;
``signal``
    only disproportionality signals;
``tested_negative``
    every row that reaches the pair says it was tested and not found (an inactive assay,
    a screen without a hit);
``inconclusive``
    only rows the sources flag as unreliable.

A negative result never counts as support, and support never cancels a negative result:
an assertion with both lists the sources on each side (``outcomes``,
``contradicted_by``). Effects are reported per source; activation against inhibition,
or increase against decrease, is flagged as ``effect_conflict`` rather than resolved.

Scores are never combined across sources: an IES, a STITCH score, a P value and a PRR
are on different scales, so each is reported under its own source.
"""

from __future__ import annotations

import itertools
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["Crosswalk", "Assertion", "EVIDENCE_RANK", "SUPPORT_CLASSES", "consensus",
           "compare", "redundancy", "survey", "lineage_of", "independent_count"]

#: Best first. ``known`` and ``reported`` point at observations; the rest do not.
EVIDENCE_RANK = ("known", "reported", "associated", "listed", "aggregated", "mentioned",
                 "predicted", "signal")
OBSERVED = frozenset({"known", "reported"})
SUPPORT_CLASSES = ("independently_replicated", "documented", "associated", "integrated",
                   "mentioned", "predicted", "signal", "tested_negative", "inconclusive")
#: Effects that contradict each other when two sources report them for one pair.
OPPOSED_EFFECTS = (frozenset({"activation", "inhibition"}), frozenset({"increase", "decrease"}))
#: Two sources whose object sets for the same subjects overlap at least this much
#: (Jaccard) are counted as one lineage.
REDUNDANT_JACCARD = 0.5
#: Overlap is judged only when both sources list at least this many objects for the
#: shared subjects: a few objects in common say nothing about copying.
MIN_OBJECTS = 20

#: Processing prefixes that make a different material (炙黄芪 is not 黄芪): kept apart
#: unless a query asks to merge processed forms.
_PROCESSED = ("炙", "炒", "麸炒", "焦", "炭", "煅", "制", "酒", "醋", "盐", "姜", "蜜", "熟",
              "法", "清", "胆")
_CJK = re.compile(r"[一-鿿]")
#: Entity types the crosswalk maps (compounds to InChIKeys, genes to symbols).
_MAPPED = ("ingredient", "compound", "drug", "ligand", "target", "gene", "protein")
_VIA = re.compile(r"via\s+(.+)$")


# ---------------------------------------------------------------------------- lineage
#: The upstream a database's rows rest on when the row does not say, as each database
#: documents it. Anything not here is its own lineage.
DECLARED_LINEAGE: Mapping[tuple[str, str], tuple[str, ...]] = {
    # BATMAN-TCM 2.0's known interactions: HIT, DrugBank, KEGG, TTD and its own curation
    ("batman2", "known"): ("HIT", "DrugBank", "KEGG", "TTD"),
    # ITCM integrates TCMSP, SymMap, TCMID and ETCM, and its ingredient-target endpoint
    # names SymMap and TCMSP as the source of each pair
    ("itcm", "aggregated"): ("TCMSP", "SymMap", "TCMID", "ETCM"),
}


def lineage_of(row: Mapping[str, Any]) -> frozenset[str]:
    """The primary resources a relation row rests on."""
    ref = row.get("reference") or ""
    evidence, source = row.get("evidence"), row.get("source")
    if evidence in OBSERVED and str(ref).startswith(("pmid:", "doi:", "nct:", "prospero:")):
        return frozenset({str(ref)})
    note = row.get("note") or ""
    m = _VIA.search(note) if "via " in note else None
    if m:
        units = {re.split(r"[:\s]", u.strip())[0] for u in re.split(r"[;|]", m.group(1))
                 if u.strip()}
        if units:
            return frozenset(u.upper() for u in units)
    if evidence == "predicted":
        return frozenset({f"model:{source}"})
    declared = DECLARED_LINEAGE.get((source, evidence))
    if declared:
        # one row rests on one of these, and the database does not say which
        return frozenset({"any:" + "|".join(sorted(u.upper() for u in declared))})
    return frozenset({f"db:{source}"})


def _upstreams(unit: str) -> frozenset[str]:
    """The resources a lineage unit may rest on: ``any:A|B`` may be either."""
    return frozenset(unit[4:].split("|")) if unit.startswith("any:") else frozenset({unit})


def independent_count(units: Iterable[str]) -> int:
    """Units that cannot share an upstream: connected components of 'may share'.

    Conservative on purpose. ``any:DRUGBANK|HIT|KEGG|TTD`` (a BATMAN-TCM row) and ``HIT``
    (a HERB row naming HIT) may be the same upstream, so together they count once.
    """
    groups: list[set[str]] = []
    for unit in units:
        ups = set(_upstreams(unit))
        merged = [g for g in groups if g & ups]
        for g in merged:
            ups |= g
            groups.remove(g)
        groups.append(ups)
    return len(groups)


# --------------------------------------------------------------------------- crosswalk
class Crosswalk:
    """Maps the ids different sources use for one compound, gene or herb to one identity.

    Built from the entity tables of the stores that are present: CID↔InChIKey from HERB
    and TM-MC, source-local ingredient ids from HERB, SymMap and TM-MC, Entrez and UniProt
    to HGNC symbols from HERB, SymMap, ITCM and TCMIO, herb names to the materia table.
    """

    def __init__(self, hub: Any) -> None:
        self.hub = hub

    def _rows(self, key: str, sql: str) -> list[tuple]:
        path = self.hub.db_path(key)
        if not path.exists():
            return []
        with closing(sqlite3.connect(path)) as conn:
            try:
                return conn.execute(sql).fetchall()
            except sqlite3.OperationalError:
                return []

    @cached_property
    def compound(self) -> dict[str, str]:
        """Any compound id -> ``inchikey:…`` (or ``pubchem:…`` when no key is known)."""
        cid_ik: dict[str, str] = {}
        for key, sql in (("herb2", "SELECT PubChem_id, InChIKey FROM ingredient"),
                         ("tmmc2", "SELECT CID, INCHIKEY FROM chemical_property")):
            for cid, ik in self._rows(key, sql):
                cid = _int(cid)
                if cid and ik and len(str(ik)) == 27:
                    cid_ik.setdefault(cid, f"inchikey:{ik}")
        out: dict[str, str] = {f"pubchem:{c}": ik for c, ik in cid_ik.items()}
        for hb, cid, ik in self._rows("herb2", "SELECT Ingredient_id, PubChem_id, InChIKey "
                                               "FROM ingredient"):
            canon = (f"inchikey:{ik}" if ik and len(str(ik)) == 27
                     else cid_ik.get(_int(cid) or "") or (f"pubchem:{_int(cid)}" if _int(cid)
                                                           else None))
            if canon:
                out[f"herb2:{hb}"] = canon
        for mol, cid in self._rows("symmap2", "SELECT Mol_id, PubChem_CID FROM ingredient"):
            first = _int(str(cid or "").split("|")[0])
            if mol and first:
                out[f"symmap:SMIT{int(_int(mol) or 0):05d}"] = cid_ik.get(first,
                                                                          f"pubchem:{first}")
        for tid, cid, ik in self._rows("tmmc2", "SELECT ID, CID, INCHIKEY FROM chemical_property"):
            if ik and len(str(ik)) == 27:
                out[f"tmmc:{tid}"] = f"inchikey:{ik}"
        for local, canon in self._declared("compound"):
            if canon.startswith("inchikey:") and len(canon) == 36:
                out.setdefault(local, canon)
        return out

    def _declared(self, entity: str) -> list[tuple[str, str]]:
        """(id, canonical id) pairs the built datasets declare (``DatasetSpec.crosswalk``)."""
        from .datasets import DATASETS
        pairs: list[tuple[str, str]] = []
        for spec in DATASETS:
            sql = spec.crosswalk.get(entity)
            if sql:
                pairs += [(str(a), str(b)) for a, b in self._rows(spec.key, sql) if a and b]
        return pairs

    @cached_property
    def gene(self) -> dict[str, str]:
        """Any human gene/protein id -> ``symbol:…``."""
        out: dict[str, str] = {}
        for key, sql in (("herb2", "SELECT Entrez_id, Gene_symbol, Target_id FROM target"),
                         ("symmap2", "SELECT NCBI_id, Gene_symbol, Gene_id FROM target")):
            for entrez, sym, local in self._rows(key, sql):
                if sym:
                    sym = f"symbol:{str(sym).upper()}"
                    if _int(entrez):
                        out.setdefault(f"ncbigene:{_int(entrez)}", sym)
                    if key == "herb2":
                        out[f"herb2:{local}"] = sym
                    elif _int(local):
                        out[f"symmap:SMTT{int(_int(local) or 0):05d}"] = sym
        for key, sql in (("itcm", "SELECT UniProtKB, gene_symbol FROM target"),
                         ("tcmio", "SELECT Uniprot_id, Gene_name FROM target"),
                         ("symmap2", "SELECT UniProt_id, Gene_symbol FROM target")):
            for acc, sym in self._rows(key, sql):
                if acc and sym:
                    for a in str(acc).split("|"):
                        out.setdefault(f"uniprot:{a.strip()}", f"symbol:{str(sym).upper()}")
        for local, canon in self._declared("gene"):
            if canon.startswith("symbol:") and len(canon) > 7:
                out.setdefault(local, "symbol:" + canon[7:].upper())
        return out

    def canon(self, entity_type: str, entity_id: str, name: str | None = None, *,
              merge_processed: bool = False) -> str:
        """One identity for an entity as one source names it."""
        if entity_type in ("ingredient", "compound", "drug", "ligand"):
            if entity_id.startswith("inchikey:"):
                return entity_id
            return self.compound.get(entity_id, entity_id)
        if entity_type in ("target", "gene", "protein"):
            if entity_id.startswith("symbol:"):
                return entity_id.upper().replace("SYMBOL:", "symbol:")
            if entity_id.startswith("ensembl:ENSP") and name:
                # STITCH's preferred name for a human protein is its gene symbol
                return f"symbol:{name.split(' | ')[0].upper()}"
            return self.gene.get(entity_id, entity_id)
        if entity_type == "herb":
            return herb_key(name, entity_id, merge_processed=merge_processed)
        return entity_id

    def aliases(self, entity_type: str, canonical: str) -> list[str]:
        """Every id that maps to ``canonical`` (for querying all sources at once)."""
        table = (self.compound if entity_type in ("ingredient", "compound", "drug", "ligand")
                 else self.gene)
        return [canonical] + [k for k, v in table.items() if v == canonical]


def herb_key(name: str | None, entity_id: str, *, merge_processed: bool = False) -> str:
    """A herb's identity: its materia drug, a processed form of it, or its Chinese name."""
    from ..sources.materia import MATERIA, resolve_name
    for raw in (name or "").split(" | "):
        raw = raw.strip()
        if not raw:
            continue
        drug = resolve_name(raw)
        if drug:
            base = MATERIA[drug]
            processed = (raw != base.chinese and base.chinese in raw
                         and raw.startswith(_PROCESSED))
            if processed and not merge_processed:
                return f"materia:{drug}#{raw}"
            return f"materia:{drug}"
    for raw in (name or "").split(" | "):
        pinyin = re.sub(r"[\s'-]+", "", raw).lower()      # BATMAN names herbs in pinyin only
        if pinyin in MATERIA:
            return f"materia:{pinyin}"
    for raw in (name or "").split(" | "):
        if _CJK.search(raw):
            return f"name:{raw.strip()}"
    return entity_id


def _int(value: Any) -> str | None:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text if text.isdigit() else None


# --------------------------------------------------------------------------- assertions
@dataclass
class Assertion:
    kind: str
    subject: str
    object: str
    subject_names: set[str] = field(default_factory=set)
    object_names: set[str] = field(default_factory=set)
    rows: list[dict] = field(default_factory=list)
    silent: list[str] = field(default_factory=list)       # cover the subject, lack the object
    not_tested: list[str] = field(default_factory=list)   # screens with no row for the pair

    @property
    def sources(self) -> list[str]:
        return sorted({r["source"] for r in self.rows})

    @property
    def positive(self) -> list[dict]:
        """The rows that found the relation (a row without an outcome found it)."""
        return [r for r in self.rows if (r.get("outcome") or "positive") == "positive"]

    def _with(self, outcome: str) -> list[str]:
        return sorted({r["source"] for r in self.rows
                       if (r.get("outcome") or "positive") == outcome})

    @property
    def evidence(self) -> dict[str, list[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for r in self.positive or self.rows:
            out[r["evidence"]].add(r["source"])
        return {e: sorted(out[e]) for e in EVIDENCE_RANK if e in out}

    @property
    def best_evidence(self) -> str:
        return next(iter(self.evidence))

    @property
    def effects(self) -> dict[str, list[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for r in self.positive:
            if r.get("effect"):
                out[r["effect"]].add(r["source"])
        return {e: sorted(out[e]) for e in sorted(out)}

    @property
    def effect_conflict(self) -> bool:
        have = set(self.effects)
        return any(pair <= have for pair in OPPOSED_EFFECTS)

    def lineages(self, evidence: Iterable[str] | None = None,
                 redundant: Mapping[str, str] | None = None) -> set[str]:
        """Independent lineage units behind the rows of the given evidence kinds.

        ``redundant`` maps a database lineage (``db:itcm``) to the cluster it was found to
        copy, so two copies count once. A non-observed row from a database in such a
        cluster counts as the cluster, whatever upstream its note names: the copies share
        their content, not necessarily their labels.
        """
        wanted = set(evidence) if evidence else None
        redundant = redundant or {}
        units: set[str] = set()
        for r in self.positive:
            if wanted is None or r["evidence"] in wanted:
                cluster = redundant.get(f"db:{r['source']}")
                if cluster and r["evidence"] not in OBSERVED:
                    units.add(cluster)
                    continue
                for u in lineage_of(r):
                    units.add(redundant.get(u, u))
        return units

    def support(self, redundant: Mapping[str, str] | None = None) -> str:
        if not self.positive:
            return "tested_negative" if self._with("negative") else "inconclusive"
        best = self.best_evidence
        if best in OBSERVED:
            return ("independently_replicated"
                    if independent_count(self.lineages(OBSERVED, redundant)) >= 2
                    else "documented")
        if best in ("listed", "aggregated"):
            return "integrated"
        return best                       # associated | mentioned | predicted | signal

    def as_dict(self, redundant: Mapping[str, str] | None = None) -> dict[str, Any]:
        refs = sorted({r["reference"] for r in self.rows
                       if r.get("reference") and str(r["reference"]).startswith(("pmid:",
                                                                                 "doi:"))})
        return {
            "kind": self.kind, "subject": self.subject, "object": self.object,
            "subject_names": sorted(self.subject_names)[:5],
            "object_names": sorted(self.object_names)[:5],
            "support": self.support(redundant), "best_evidence": self.best_evidence,
            "evidence": self.evidence, "sources": self.sources,
            "lineages": {"observed": sorted(self.lineages(OBSERVED, redundant)),
                         "all": sorted(self.lineages(None, redundant))},
            "independent": {
                "observed": independent_count(self.lineages(OBSERVED, redundant)),
                "all": independent_count(self.lineages(None, redundant))},
            "references": refs,
            "scores": {r["source"]: r["score"] for r in self.rows if r.get("score")},
            "silent_sources": self.silent,
            "not_tested_in": self.not_tested,
            "outcomes": {o: self._with(o) for o in ("positive", "negative", "inconclusive")
                         if self._with(o)},
            "contradicted_by": self._with("negative") if self.positive else [],
            "effects": self.effects,
            "effect_conflict": self.effect_conflict,
            "contexts": len({r["context"] for r in self.rows if r.get("context")}),
        }


def _screening_sources(hub: Any) -> set[str]:
    """Source labels whose rows include negative or inconclusive results (assay screens).

    ``build`` records them per store; a store built without that record is read directly.
    """
    out: set[str] = set()
    built = hub.built() if hasattr(hub, "built") else []
    for key in built:
        with closing(sqlite3.connect(hub.db_path(key))) as conn:
            try:
                row = conn.execute("SELECT value FROM _tcmdb_build "
                                   "WHERE key = 'sources_with_negatives'").fetchone()
                if row is not None:
                    out.update(json.loads(row[0]))
                    continue
            except sqlite3.OperationalError:
                pass
            try:
                out.update(r[0] for r in conn.execute(
                    "SELECT DISTINCT source FROM relations "
                    "WHERE outcome IN ('negative', 'inconclusive')"))
            except sqlite3.OperationalError:          # built before the outcome column
                pass
    return out


def _gather(hub: Any, kind: str, *, subject: str | None, object: str | None,
            sources: Sequence[str] | None, cw: Crosswalk, contains: bool,
            merge_processed: bool) -> tuple[list[dict], dict[str, set[str]]]:
    """Every row of ``kind`` for the subject/object under any of its ids, canonicalised."""
    from .relations import RELATION_KINDS
    stype, otype = RELATION_KINDS[kind]

    def variants(value: str | None, etype: str) -> list[str | None]:
        if not value:
            return [None]
        if ":" in value and etype in _MAPPED:
            canonical = cw.canon(etype, value)
            return cw.aliases(etype, canonical)
        return [value]

    rows: list[dict] = []
    seen: set[tuple] = set()

    def collect(subjects: Iterable[str | None], objects: Iterable[str | None]) -> None:
        objects = list(objects)
        for s in subjects:
            for o in objects:
                for r in hub.relations(kind, subject=s, object=o, sources=sources,
                                       contains=contains, limit=10 ** 7):
                    key = tuple(r.get(c) for c in ("source", "subject_id", "object_id",
                                                   "evidence", "reference", "effect",
                                                   "outcome", "context"))
                    if key not in seen:
                        seen.add(key)
                        rows.append(r)

    collect(variants(subject, stype), variants(object, otype))
    if subject and ":" not in subject and stype in _MAPPED:
        # A name reaches only the sources that store names. Sources that key compounds or
        # genes by id alone are reached through the ids the name resolved to.
        named = {cw.canon(stype, r["subject_id"], r["subject_name"]) for r in rows}
        extra = sorted({a for c in named if c.startswith(("inchikey:", "symbol:"))
                        for a in cw.aliases(stype, c)})
        collect(extra, variants(object, otype))
    subject_ids: dict[str, set[str]] = defaultdict(set)   # source -> subject canonical ids
    for r in rows:
        r["_s"] = cw.canon(r["subject_type"], r["subject_id"], r["subject_name"],
                           merge_processed=merge_processed)
        r["_o"] = cw.canon(r["object_type"], r["object_id"], r["object_name"],
                           merge_processed=merge_processed)
        subject_ids[r["source"]].add(r["_s"])
    return rows, subject_ids


def redundancy(rows: Sequence[Mapping[str, Any]], *, threshold: float = REDUNDANT_JACCARD,
               min_objects: int = MIN_OBJECTS) -> dict[str, Any]:
    """Pairwise overlap of sources over the subjects they share, and the copy clusters.

    For each pair of sources, the object sets of the subjects *both* cover are compared;
    comparing whole sources would only measure their sizes. Pairs at or above
    ``threshold`` are joined into one lineage cluster.
    """
    by: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if r["evidence"] in ("aggregated", "listed", "known") \
                and (r.get("outcome") or "positive") == "positive":
            by[r["source"]][r["_s"]].add(r["_o"])
    pairs = {}
    parent = {s: s for s in by}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in itertools.combinations(sorted(by), 2):
        shared = set(by[a]) & set(by[b])
        A = set().union(*(by[a][s] for s in shared)) if shared else set()
        B = set().union(*(by[b][s] for s in shared)) if shared else set()
        if len(A) < min_objects or len(B) < min_objects:
            continue
        j = len(A & B) / len(A | B)
        pairs[f"{a}|{b}"] = round(j, 3)
        if j >= threshold:
            parent[find(a)] = find(b)
    clusters: dict[str, list[str]] = defaultdict(list)
    for s in by:
        clusters[find(s)].append(s)
    found = sorted(sorted(m) for m in clusters.values() if len(m) > 1)
    return {"jaccard": pairs, "threshold": threshold, "clusters": found,
            "lineage_map": lineage_map(found)}


def lineage_map(clusters: Sequence[Sequence[str]]) -> dict[str, str]:
    """Lineage unit -> the copy cluster it belongs to, for clusters of database keys."""
    mapping: dict[str, str] = {}
    for members in clusters:
        name = "copies:" + "+".join(sorted(members))
        for m in members:
            mapping[f"db:{m}"] = name
            # a declared lineage of a member collapses into the cluster as well
            for (src, _ev), ups in DECLARED_LINEAGE.items():
                if src == m:
                    mapping.setdefault("any:" + "|".join(sorted(u.upper() for u in ups)), name)
    return mapping


def consensus(hub: Any, kind: str, *, subject: str | None = None, object: str | None = None,
              sources: Sequence[str] | None = None, contains: bool = False,
              merge_processed: bool = False, min_support: str | None = None,
              crosswalk: Crosswalk | None = None) -> dict[str, Any]:
    """Reconcile every source's rows of ``kind`` for a subject (and/or object)."""
    if not subject and not object:
        raise ValueError("consensus needs a subject or an object")
    cw = crosswalk or Crosswalk(hub)
    rows, subject_ids = _gather(hub, kind, subject=subject, object=object, sources=sources,
                                cw=cw, contains=contains, merge_processed=merge_processed)
    red = redundancy(rows)
    saved = hub.saved_survey(kind) if hasattr(hub, "saved_survey") else None
    if saved is not None:
        # copy clusters from the corpus-wide survey; this query's overlap stays a report
        red["lineage_map"] = lineage_map(saved["clusters"])
        red["clusters_from"] = (f"survey of {saved['subjects_sampled']} shared subjects "
                                f"({saved.get('surveyed_at', '')})")
        red["survey_clusters"] = saved["clusters"]
    else:
        red["clusters_from"] = "this query (no saved survey; run hub.survey(kind))"
    subjects = sorted({r["_s"] for r in rows})
    ambiguous = {src: sorted(ids) for src, ids in subject_ids.items() if len(ids) > 1} \
        if subject and not contains else {}

    groups: dict[tuple[str, str], Assertion] = {}
    for r in rows:
        a = groups.setdefault((r["_s"], r["_o"]), Assertion(kind, r["_s"], r["_o"]))
        a.rows.append(r)
        if r.get("subject_name"):
            a.subject_names.update(n for n in r["subject_name"].split(" | ")[:2])
        if r.get("object_name"):
            a.object_names.update(n for n in r["object_name"].split(" | ")[:2])
    covering: dict[str, set[str]] = defaultdict(set)      # subject -> sources covering it
    for r in rows:
        covering[r["_s"]].add(r["source"])
    # A source that records negative results (an assay screen) would have a row for a
    # pair it tested; its missing row means "not tested", not silence.
    screening = _screening_sources(hub) & set(subject_ids)
    for a in groups.values():
        lacking = covering[a.subject] - set(a.sources)
        a.silent = sorted(lacking - screening)
        a.not_tested = sorted(lacking & screening)

    order = {c: i for i, c in enumerate(SUPPORT_CLASSES)}
    out = [a.as_dict(red["lineage_map"]) for a in groups.values()]
    if min_support:
        out = [x for x in out if order[x["support"]] <= order[min_support]]
    out.sort(key=lambda x: (order[x["support"]], -x["independent"]["observed"],
                            -x["independent"]["all"], x["object"]))
    summary: dict[str, int] = defaultdict(int)
    for x in out:
        summary[x["support"]] += 1
    unmapped = sorted({r["_o"] for r in rows if r["_o"].split(":")[0] not in
                       ("inchikey", "symbol", "materia", "pubchem")})
    return {"kind": kind, "query": {"subject": subject, "object": object},
            "subjects": subjects, "ambiguous_subjects": ambiguous,
            "sources": sorted(subject_ids), "rows": len(rows), "assertions": len(out),
            "support": dict(summary), "redundancy": {k: v for k, v in red.items()
                                                     if k != "lineage_map"},
            "unmapped_objects": len(unmapped), "items": out}


def compare(hub: Any, kind: str, subject: str, *, sources: Sequence[str] | None = None,
            merge_processed: bool = False) -> dict[str, Any]:
    """Per-source object sets for one subject: what all agree on and what is source-only."""
    cw = Crosswalk(hub)
    rows, _ = _gather(hub, kind, subject=subject, object=None, sources=sources, cw=cw,
                      contains=False, merge_processed=merge_processed)
    from .relations import RELATION_KINDS
    # A formula's composition is a version: two formulas with one name from two books are
    # two formulas. They are compared as versions, never pooled per source.
    versions = RELATION_KINDS[kind][0] == "formula"
    by: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        by[r["subject_id"] if versions else r["source"]].add(r["_o"])
    if not by:
        return {"kind": kind, "subject": subject, "sources": {}}
    union = set().union(*by.values())
    counts: dict[str, int] = defaultdict(int)
    for objs in by.values():
        for o in objs:
            counts[o] += 1
    return {"kind": kind, "subject": subject, "compared": "versions" if versions else "sources",
            "sources": {s: len(v) for s, v in sorted(by.items())},
            "union": len(union),
            "in_all": sorted(o for o, n in counts.items() if n == len(by)),
            "by_number_of_sources": {n: sum(1 for c in counts.values() if c == n)
                                     for n in range(1, len(by) + 1)},
            "only_in": {s: len([o for o in v if counts[o] == 1]) for s, v in sorted(by.items())},
            "jaccard": {f"{a}|{b}": round(len(by[a] & by[b]) / len(by[a] | by[b]), 3)
                        for a, b in itertools.combinations(sorted(by), 2)},
            "redundancy": {k: v for k, v in redundancy(rows).items() if k != "lineage_map"}}


def survey(hub: Any, kind: str, *, sample: int = 200, min_sources: int = 3, seed: int = 0,
           sources: Sequence[str] | None = None) -> dict[str, Any]:
    """How much the sources of one relation kind copy each other, over shared subjects.

    Samples ``sample`` subjects that at least ``min_sources`` sources cover (after id
    unification), then compares the sources on those subjects (see :func:`redundancy`).
    The result is the evidence behind treating two databases as one lineage, so it can
    be checked rather than assumed.
    """
    import random

    from .relations import RELATION_KINDS
    stype = RELATION_KINDS[kind][0]
    cw = Crosswalk(hub)
    keys = [k for k in (sources or hub.built()) if hub.db_path(k).exists()]
    per_source: dict[str, dict[str, list[str]]] = {}         # source -> canon -> local ids
    for key in keys:
        with closing(sqlite3.connect(hub.db_path(key))) as conn:
            try:
                found = conn.execute("SELECT DISTINCT subject_id, subject_name FROM relations "
                                     "WHERE kind = ?", (kind,)).fetchall()
            except sqlite3.OperationalError:
                continue
        if found:
            m: dict[str, list[str]] = defaultdict(list)
            for sid, name in found:
                m[cw.canon(stype, sid, name)].append(sid)
            per_source[key] = m
    counts: dict[str, int] = defaultdict(int)
    for m in per_source.values():
        for c in m:
            counts[c] += 1
    shared = sorted(c for c, n in counts.items() if n >= min_sources)
    rng = random.Random(seed)
    chosen = set(rng.sample(shared, min(sample, len(shared))))
    rows: list[dict] = []
    for key, m in per_source.items():
        ids = [sid for c in chosen for sid in m.get(c, [])]
        with closing(sqlite3.connect(hub.db_path(key))) as conn:
            conn.row_factory = sqlite3.Row
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                for r in conn.execute(f"SELECT * FROM relations WHERE kind = ? AND subject_id "
                                      f"IN ({', '.join('?' * len(chunk))})", [kind, *chunk]):
                    r = dict(r)
                    r["_s"] = cw.canon(r["subject_type"], r["subject_id"], r["subject_name"])
                    r["_o"] = cw.canon(r["object_type"], r["object_id"], r["object_name"])
                    rows.append(r)
    result = redundancy(rows)
    return {"kind": kind, "subjects_shared": len(shared), "subjects_sampled": len(chosen),
            "min_sources": min_sources, "seed": seed,
            "coverage": {k: len(m) for k, m in sorted(per_source.items())},
            "jaccard": result["jaccard"], "clusters": result["clusters"],
            "threshold": result["threshold"]}
