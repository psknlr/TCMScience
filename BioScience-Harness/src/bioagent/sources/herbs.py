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

    @property
    def fingerprint(self) -> str:
        blob = json.dumps({"id": self.id, "source": self.source,
                           "components": self.components}, ensure_ascii=False, sort_keys=True)
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


def herb_rows(formula: FormulaVersion = GEGEN_QINLIAN
              ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Nodes and edges for the herb layer: formula -> herbs (as the text records them) and
    herb -> source species (as the pharmacopoeia lists them)."""
    nodes: dict[str, dict[str, Any]] = {formula.id: {
        "id": formula.id, "category": "formula", "name": formula.chinese, "source": KEY,
        "raw": {"source_text": formula.source, "fingerprint": formula.fingerprint}}}
    edges: list[dict[str, Any]] = []
    for herb_id, role, dose, processing in formula.components:
        herb = HERBS[herb_id]
        nodes[herb.id] = {"id": herb.id, "category": "herb", "name": herb.chinese,
                          "source": KEY, "names": {"zh": [herb.chinese], "latin": [herb.latin]},
                          "raw": {"parts": list(herb.parts), "part_zh": herb.part_zh}}
        edges.append({
            "subject": formula.id, "predicate": "contains", "object": herb.id,
            "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
            "study_design": "classical_text", "license": LICENSE,
            "source_record_id": f"{formula.id}|{herb.id}", "primary_knowledge_source": KEY,
            "raw": {"role": role, "dose": dose, "processing": processing}})
        for sp in herb.species:
            organism = f"ncbitaxon:{sp.taxid}"
            nodes[organism] = {"id": organism, "category": "organism", "name": sp.name,
                               "source": KEY, "xrefs": {"ncbitaxon": [sp.taxid]},
                               "names": {"latin": [sp.name, *sp.synonyms]}}
            edges.append({
                "subject": herb.id, "predicate": "has_base_species", "object": organism,
                "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
                "study_design": "expert_consensus", "license": LICENSE,
                "source_record_id": f"{herb.id}|{sp.taxid}", "primary_knowledge_source": KEY,
                "raw": {"part": herb.part_zh}})
    return list(nodes.values()), edges


def gold_edges(herbs: Iterable[str] = GOLD) -> dict[str, list[tuple[str, str, str]]]:
    """herb -> the (species, contains, marker) edges of which a build must contain one."""
    return {h: [(f"ncbitaxon:{s.taxid}", "contains", f"inchikey:{GOLD[h][1]}")
                for s in HERBS[h].species] for h in herbs}
