"""What a crude drug contains, according to the snapshots, and how strongly.

Joins the herb layer (herb -> source species) with the sources' organism -> compound
edges. Every hit starts at composition level C1 (reported in a source species). It is
raised to C2 only when the source recorded the isolation part and that part is the drug's
medicinal part — NPASS records it for some pairs; LOTUS and CMAUP do not. Nothing here
reaches C3 (detected in the decoction) or C4 (absorbed): those need analytical data about
the preparation itself, and no bulk source provides them.

Compounds from different sources merge on their ``inchikey:`` node id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .herbs import HERBS, CrudeDrug
from .snapshot import Snapshot

__all__ = ["CompositionHit", "herb_composition"]


@dataclass
class CompositionHit:
    herb: str
    compound: str
    name: str
    level: str                                  # C1 or C2
    species: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    publications: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {"herb": self.herb, "compound": self.compound, "name": self.name,
                "level": self.level, "species": sorted(self.species),
                "sources": sorted(self.sources), "publications": sorted(self.publications)}


def herb_composition(snapshots: Iterable[Snapshot], *,
                     herbs: Mapping[str, CrudeDrug] = HERBS) -> list[CompositionHit]:
    names: dict[str, str] = {}
    contains: list[tuple[str, dict]] = []
    for snap in snapshots:
        for n in snap.nodes:
            if n.get("category") == "ingredient":
                names.setdefault(n["id"], n.get("name") or n["id"])
        contains.extend((snap.key, e) for e in snap.edges if e.get("predicate") == "contains")
    by_species: dict[str, list[CrudeDrug]] = {}
    for herb in herbs.values():
        for sp in herb.species:
            by_species.setdefault(f"ncbitaxon:{sp.taxid}", []).append(herb)

    hits: dict[tuple[str, str], CompositionHit] = {}
    for source, edge in contains:
        for herb in by_species.get(edge.get("subject"), ()):
            parts = (edge.get("raw") or {}).get("parts") or []
            level = "C2" if parts and herb.matches_part(parts) else "C1"
            hit = hits.get((herb.id, edge["object"]))
            if hit is None:
                hit = hits[(herb.id, edge["object"])] = CompositionHit(
                    herb.id, edge["object"], names.get(edge["object"], edge["object"]), level)
            elif level == "C2":
                hit.level = "C2"
            hit.species.add(edge["subject"])
            hit.sources.add(source)
            hit.publications.update(edge.get("publications") or ())
    return sorted(hits.values(), key=lambda h: (h.herb, h.level != "C2", h.compound))
