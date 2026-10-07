"""Planted worlds: small synthetic snapshots whose answer is known.

A method that decides between explanations should be shown deciding correctly where the
right answer is known before it is run. Each world below is the real 葛根芩连汤 herb layer
with a synthetic natural-product source and Reactome, built so that one explanation of
"the formula's measured targets concentrate in pathway A" is true:

``selective``
    The four constituents hit pathway A, and one of them was also measured, inactive,
    against 32 proteins of other pathways, so the hits in A are not merely where the
    compounds were assayed. Twelve control herbs hit random proteins outside A.
    True explanation: the constituents act selectively on A.

``coverage``
    The same hits, and no inactive measurement at all: the only proteins the
    constituents were assayed against are the ones they hit. Against the assayed
    background nothing can be enriched. True explanation: the concentration is which
    proteins were assayed.

``promiscuous``
    As ``selective``, except the control herbs' compounds hit pathway A too, and were
    measured inactive elsewhere like the formula's. Random herb combinations reach A as
    often as the formula does. True explanation: A's proteins are hit by many compounds.

They are fixtures, not data: the snapshots are built under a ``+planted`` version and say
so in their citation, so nothing built here can be mistaken for a source.
"""

from __future__ import annotations

import random
import string
from pathlib import Path
from typing import Any, Sequence

__all__ = ["PATHWAY", "PROTEINS", "SCENARIOS", "TRUTH", "build_world", "describe"]

PROTEINS = [f"P{10000 + i}" for i in range(60)]
_PATHWAY_A = PROTEINS[:8]
PATHWAY = "reactome:R-HSA-A"

#: Which explanation each world makes true.
TRUTH = {"selective": "target", "coverage": "coverage", "promiscuous": "promiscuity"}
SCENARIOS = tuple(TRUTH)

_INACTIVE = PROTEINS[8:40]
_CONTROLS = 12


def _inchikey(i: int) -> str:
    r = random.Random(i)
    pick = lambda n: "".join(r.choice(string.ascii_uppercase) for _ in range(n))  # noqa: E731
    return f"{pick(14)}-{pick(10)}-N"


def _edge(record: str, s: str, p: str, o: str, design: str,
          level: str = "knowledge_assertion", **kw: Any) -> dict[str, Any]:
    return {"subject": s, "predicate": p, "object": o, "primary_knowledge_source": "planted",
            "knowledge_level": level,
            "agent_type": "computational_model" if level == "prediction" else "manual_agent",
            "study_design": design, "license": "CC0-1.0", "source_record_id": record, **kw}


def _activity(record: str, compound: str, protein: str, value_nm: float) -> dict[str, Any]:
    return _edge(record, compound, "targets", f"uniprot:{protein}", "in_vitro",
                 publications=["pmid:99"],
                 measure={"type": "IC50", "relation": "=", "value": value_nm, "unit": "nM"})


def _control_herbs() -> list[str]:
    from ..sources.herbs import HERBS
    from ..sources.materia import all_drugs
    drugs = all_drugs()
    return sorted(h for h, d in drugs.items() if h not in HERBS and d.species)[:_CONTROLS]


def _tables(scenario: str, controls: bool = True
            ) -> tuple[tuple[list, list], tuple[list, list]]:
    from ..sources.herbs import HERBS
    from ..sources.materia import all_drugs
    species = [f"ncbitaxon:{h.species[0].taxid}" for h in HERBS.values()]
    compounds = [f"inchikey:{_inchikey(i)}" for i in range(4)]
    hits = {0: _PATHWAY_A[:3], 1: _PATHWAY_A[3:5], 2: _PATHWAY_A[5:7],
            3: [_PATHWAY_A[7], PROTEINS[40]]}
    nodes = ([{"id": s, "category": "organism", "name": s, "source": "planted"}
              for s in species]
             + [{"id": c, "category": "ingredient", "name": f"cmp{i}", "source": "planted"}
                for i, c in enumerate(compounds)]
             + [{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "planted"}
                for p in PROTEINS])
    edges = [_edge(f"pair{i}", species[i], "contains", compounds[i], "chemical_analysis",
                   composition_level="C1", publications=[f"pmid:{i + 1}"])
             for i in range(4)]
    for i, targets in hits.items():
        edges += [_activity(f"act{i}-{t}", compounds[i], t, 500.0) for t in targets]
    if scenario != "coverage":
        edges += [_activity(f"inactive-{t}", compounds[2], t, 90_000.0) for t in _INACTIVE]

    drugs = all_drugs()
    rng = random.Random(7)
    for k, herb in enumerate(_control_herbs() if controls else ()):
        organism = f"ncbitaxon:{drugs[herb].species[0].taxid}"
        compound = f"inchikey:{_inchikey(100 + k)}"
        nodes += [{"id": organism, "category": "organism", "name": organism,
                   "source": "planted"},
                  {"id": compound, "category": "ingredient", "name": f"ctl{k}",
                   "source": "planted"}]
        edges.append(_edge(f"ctl-pair{k}", organism, "contains", compound,
                           "chemical_analysis", composition_level="C1",
                           publications=[f"pmid:{500 + k}"]))
        pool = _PATHWAY_A if scenario == "promiscuous" else PROTEINS[8:57]
        hit = rng.sample(pool, 3)
        edges += [_activity(f"ctl-act{k}-{t}", compound, t, 500.0) for t in hit]
        if scenario == "promiscuous":
            edges += [_activity(f"ctl-inactive{k}-{t}", compound, t, 90_000.0)
                      for t in _INACTIVE[k::4]]

    pathways = {"R-HSA-A": _PATHWAY_A}
    for k in range(7):                                   # background pathways of 7
        pathways[f"R-HSA-B{k}"] = PROTEINS[8 + k * 7: 15 + k * 7]
    r_nodes = ([{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "planted"}
                for p in PROTEINS]
               + [{"id": f"reactome:{k}", "category": "pathway", "name": f"pathway {k}",
                   "source": "planted"} for k in pathways])
    r_edges = [_edge(f"{p}|{k}", f"uniprot:{p}", "participates_in", f"reactome:{k}",
                     "expert_consensus") for k, members in pathways.items()
               for p in members]
    return (nodes, edges), (r_nodes, r_edges)


def build_world(scenario: str, root: str | Path, *, ledger: Any = None,
                controls: bool = True) -> list[Any]:
    """Build one planted world's snapshots under ``root`` and return them.

    ``controls=False`` leaves the control herbs out, so no random herb combination can be
    drawn: the world in which the specificity check cannot be run at all."""
    if scenario not in TRUTH:
        raise ValueError(f"unknown planted world {scenario!r}; one of {list(SCENARIOS)}")
    from ..sources.herbs import KEY as HERB_KEY, LICENSE as HERB_LICENSE, herb_rows
    from ..sources.snapshot import build_snapshot
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    raw = root / "planted.txt"
    raw.write_text(f"planted world: {scenario}\n", encoding="utf-8")
    (np_nodes, np_edges), (r_nodes, r_edges) = _tables(scenario, controls)
    herb_nodes, herb_edges = herb_rows()
    common = dict(raw_files={"planted.txt": raw}, parser=f"planted:{scenario}", root=root,
                  ledger=ledger)
    citation = f"planted world '{scenario}' (bioagent.research.planted); not a data source"
    return [
        build_snapshot(key=HERB_KEY, version="gold", nodes=herb_nodes, edges=herb_edges,
                       license=HERB_LICENSE, citation="the bundled herb layer", **common),
        build_snapshot(key="npass", version=f"{scenario}+planted", nodes=np_nodes,
                       edges=np_edges, license="CC0-1.0", citation=citation, **common),
        build_snapshot(key="reactome", version=f"{scenario}+planted", nodes=r_nodes,
                       edges=r_edges, license="CC0-1.0", citation=citation, **common),
    ]


def describe(scenarios: Sequence[str] = SCENARIOS) -> str:
    return "\n".join(f"{s}: true explanation is {TRUTH[s]!r}" for s in scenarios)
