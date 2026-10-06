"""Crude drugs as their source species and medicinal part, and one formula to test against.

Natural-product databases index species; a crude drug (药材) is a species, a part and a
processing. This table is the hand-checked bridge, kept deliberately small: the four
herbs of 葛根芩连汤, the formula the plan uses as its gold standard. Species are the
pharmacopoeial sources with their NCBI taxonomy ids (checked against NCBI Taxonomy on
2026-09-23); a variety that is not itself a listed source is not included — 粉葛
(*Pueraria montana* var. *thomsonii*) is a different crude drug from 葛根.

``GOLD`` is the answer key a build must reproduce: for each herb, a marker compound whose
InChIKey was checked against PubChem, and which a source must report in one of the
herb's source species.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .parsers.common import TaxonFilter

__all__ = ["Species", "CrudeDrug", "FormulaVersion", "HERBS", "GEGEN_QINLIAN", "GOLD",
           "taxon_filter", "herb_rows", "gold_edges"]

LICENSE = "CC0-1.0"
KEY = "tcm_herbs"
CITATION = "Pharmacopoeia of the People's Republic of China, 2020 edition, Vol. I"


@dataclass(frozen=True)
class Species:
    taxid: str
    name: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrudeDrug:
    id: str                              # tcm:herb.<pinyin>, matching tcm.knowledge ids
    chinese: str
    latin: str                           # pharmaceutical name
    species: tuple[Species, ...]
    parts: tuple[str, ...]               # medicinal part(s), lower-case English
    part_zh: str

    def matches_part(self, recorded: Iterable[str]) -> bool:
        """Whether a recorded isolation part (NPASS ``org_isolation_part``) is this drug's."""
        return any(part in (r or "").lower() for r in recorded for part in self.parts)


HERBS: Mapping[str, CrudeDrug] = {h.id: h for h in (
    CrudeDrug("tcm:herb.gegen", "葛根", "Puerariae Lobatae Radix",
              (Species("3893", "Pueraria montana var. lobata", ("Pueraria lobata",)),),
              ("root",), "根"),
    CrudeDrug("tcm:herb.huangqin", "黄芩", "Scutellariae Radix",
              (Species("65409", "Scutellaria baicalensis"),), ("root",), "根"),
    CrudeDrug("tcm:herb.huanglian", "黄连", "Coptidis Rhizoma",
              (Species("261450", "Coptis chinensis"), Species("261449", "Coptis deltoidea"),
               Species("261448", "Coptis teeta")), ("rhizome",), "根茎"),
    CrudeDrug("tcm:herb.gancao", "甘草", "Glycyrrhizae Radix et Rhizoma",
              (Species("74613", "Glycyrrhiza uralensis"), Species("74614", "Glycyrrhiza inflata"),
               Species("49827", "Glycyrrhiza glabra")), ("root", "rhizome"), "根及根茎"),
)}


@dataclass(frozen=True)
class FormulaVersion:
    """A formula bound to one recorded composition: a name is not enough, the herbs, the
    processing and the doses of a stated source text are."""

    id: str
    chinese: str
    source: str
    components: tuple[tuple[str, str, str, str], ...]   # (herb id, role, dose, processing)
    #: The licence of the composition record itself. The hand-checked formula here is
    #: CC0; formulas read from the user-supplied table carry that table's (unstated)
    #: licence. Not part of the fingerprint: it describes the record, not the formula.
    license: str = LICENSE
    #: Where the composition record comes from when it is not this layer's own table
    #: (``sources.hkbu``): the composition edges name it as their primary knowledge source.
    #: Not part of the fingerprint.
    primary_source: str = ""
    #: The source's own record id for each component, in order, so a composition edge
    #: leads back to the row it came from (default: the formula id and the herb id). Not
    #: part of the fingerprint.
    record_ids: tuple[str, ...] = ()
    #: Each component's name as the source wrote it (炙甘草, 黃芩), in order, when it is
    #: not the drug's own name. Part of the fingerprint only when given, so the formulas
    #: that have none keep the fingerprints already recorded for them.
    written: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for what, values in (("record ids", self.record_ids), ("written names", self.written)):
            if values and len(values) != len(self.components):
                raise ValueError(f"{self.id}: {len(values)} {what} for "
                                 f"{len(self.components)} components")

    @property
    def fingerprint(self) -> str:
        content: dict[str, Any] = {"id": self.id, "source": self.source,
                                   "components": self.components}
        if self.written:
            content["written"] = self.written
        blob = json.dumps(content, ensure_ascii=False, sort_keys=True)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: 《伤寒论》第 34 条：葛根半斤，甘草二两（炙），黄芩三两，黄连三两。
GEGEN_QINLIAN = FormulaVersion(
    "tcm:formula.gegen_qinlian_tang", "葛根黄芩黄连汤", "伤寒论·辨太阳病脉证并治中",
    (("tcm:herb.gegen", "君", "半斤", ""),
     ("tcm:herb.huangqin", "臣", "三两", ""),
     ("tcm:herb.huanglian", "臣", "三两", ""),
     ("tcm:herb.gancao", "佐使", "二两", "炙")))

#: herb -> (marker compound, InChIKey) — InChIKeys checked against PubChem 2026-09-23.
GOLD: Mapping[str, tuple[str, str]] = {
    "tcm:herb.gegen": ("puerarin", "HKEAFJYKMMKDOR-VPRICQMDSA-N"),
    "tcm:herb.huangqin": ("baicalin", "IKIIZLYTISPENI-ZFORQUDYSA-N"),
    "tcm:herb.huanglian": ("berberine", "YBHILYKTIRIUTE-UHFFFAOYSA-N"),
    "tcm:herb.gancao": ("glycyrrhizic acid", "LPLVUJXQOOQHMX-QWBHMCJMSA-N"),
}


def taxon_filter(herbs: Iterable[CrudeDrug] = HERBS.values()) -> TaxonFilter:
    return TaxonFilter({s.taxid: (s.name, *s.synonyms) for h in herbs for s in h.species})


def herb_rows(formula: "FormulaVersion | Iterable[FormulaVersion]" = GEGEN_QINLIAN, *,
              drugs: Mapping[str, CrudeDrug] | None = None
              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nodes and edges for the herb layer: formula -> herbs (as the text records them) and
    herb -> source species (as the pharmacopoeia lists them).

    Takes one formula or many. ``drugs`` maps herb id -> ``CrudeDrug`` for the herbs the
    formulas use (default: the four hand-checked herbs). A component with no organism —
    a mineral, a fermented product, or a drug whose species NCBI did not confirm — gets a
    node and its composition edge but no species edge, so it contributes no constituents
    and the analysis says so rather than inventing one.
    """
    from .materia import MATERIA

    formulas = [formula] if isinstance(formula, FormulaVersion) else list(formula)
    drugs = dict(HERBS) if drugs is None else dict(drugs)
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_species: set[tuple[str, str]] = set()
    for f in formulas:
        nodes[f.id] = {"id": f.id, "category": "formula", "name": f.chinese, "source": KEY,
                       "raw": {"source_text": f.source, "fingerprint": f.fingerprint}}
        for i, (herb_id, role, dose, processing) in enumerate(f.components):
            herb = drugs.get(herb_id)
            entry = MATERIA.get(herb_id.split(".", 1)[-1])
            if herb_id not in nodes:
                node = {"id": herb_id, "category": "herb", "source": KEY,
                        "name": herb.chinese if herb else (entry.chinese if entry else herb_id)}
                if herb is not None:
                    node["names"] = {"zh": [herb.chinese], "latin": [herb.latin]}
                    node["raw"] = {"parts": list(herb.parts), "part_zh": herb.part_zh}
                elif entry is not None:
                    node["names"] = {"zh": [entry.chinese], "latin": [entry.latin]}
                    node["raw"] = {"category": entry.category, "part_zh": entry.part_zh,
                                   "no_organism": True}
                nodes[herb_id] = node
            raw = {"role": role, "dose": dose, "processing": processing}
            if f.written:
                raw["written"] = f.written[i]
            edges.append({
                "subject": f.id, "predicate": "contains", "object": herb_id,
                "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
                "study_design": "classical_text", "license": f.license,
                "source_record_id": f.record_ids[i] if f.record_ids else f"{f.id}|{herb_id}",
                "primary_knowledge_source": f.primary_source or KEY, "raw": raw})
            if herb is None:
                continue
            for sp in herb.species:
                if (herb.id, sp.taxid) in seen_species:
                    continue
                seen_species.add((herb.id, sp.taxid))
                organism = f"ncbitaxon:{sp.taxid}"
                nodes[organism] = {"id": organism, "category": "organism", "name": sp.name,
                                   "source": KEY, "xrefs": {"ncbitaxon": [sp.taxid]},
                                   "names": {"latin": [sp.name, *sp.synonyms]}}
                edges.append({
                    "subject": herb.id, "predicate": "has_base_species", "object": organism,
                    "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
                    "study_design": "expert_consensus", "license": LICENSE,
                    "source_record_id": f"{herb.id}|{sp.taxid}",
                    "primary_knowledge_source": KEY, "raw": {"part": herb.part_zh}})
    # Duplicate composition records (the same formula id listed twice) are dropped.
    unique = {(e["subject"], e["predicate"], e["object"]): e for e in edges}
    return list(nodes.values()), list(unique.values())


def gold_edges(herbs: Iterable[str] = GOLD) -> dict[str, list[tuple[str, str, str]]]:
    """herb -> the (species, contains, marker) edges of which a build must contain one."""
    return {h: [(f"ncbitaxon:{s.taxid}", "contains", f"inchikey:{GOLD[h][1]}")
                for s in HERBS[h].species] for h in herbs}
