"""Does a mechanism signal survive when the evidence it rests on is held out by its origin?

A pathway enriched among a formula's screening hits may be specific to the formula, or it
may be carried by one assay campaign (a CYP inhibition panel that tested thousands of
compounds, a luciferase reporter prone to interference). The test here re-runs the
analysis with one *group* of evidence removed at a time — an assay campaign (PubChem AID),
an assay family, or one original study whichever database redistributed it — and reports,
for each pathway the full analysis released, whether it is still significant.

Grouping is by origin, not by row, so the same experiment can never sit in both the
analysis and the check: all rows of one AID (or one paper, however many databases carry
it) are removed together.

A pathway that loses significance when one group is removed is *carried by that group*:
the signal may still be real, but it is not supported independently of that one source of
data. The report says which group carries it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = ["aid_of", "assay_family", "group_folds", "leave_one_group_out"]


def aid_of(edge: Mapping[str, Any]) -> str | None:
    """The PubChem AID of a screening edge (first field of its source record id)."""
    rid = str(edge.get("source_record_id") or "")
    head = rid.split("|", 1)[0]
    return f"AID{head}" if head.isdigit() else None


_FAMILIES = (
    ("cyp_panel", re.compile(r"\b(p450|cyp\d|cytochrome p450)", re.I)),
    ("luciferase_reporter", re.compile(r"lucifer|luminesc|-glo\b", re.I)),
    ("fluorescence", re.compile(r"fluoresc", re.I)),
)


def assay_family(edge: Mapping[str, Any]) -> str:
    """A coarse assay family from the assay name (first matching family, else 'other')."""
    name = str(edge.get("assay_name") or "")
    for fam, rx in _FAMILIES:
        if rx.search(name):
            return fam
    return "other"


def group_folds(items: Sequence[Any], group: Callable[[Any], str], k: int) -> list[list[Any]]:
    """``k`` folds that never split a group (greedy, largest groups first)."""
    groups: dict[str, list[Any]] = {}
    for it in items:
        groups.setdefault(group(it), []).append(it)
    folds: list[list[Any]] = [[] for _ in range(k)]
    for _, members in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        min(folds, key=len).extend(members)
    return folds


def leave_one_group_out(snapshots: Iterable[Any], *, formula: Any, params: Any,
                        source: str, group_of: Callable[[Mapping[str, Any]], str | None],
                        groups: Sequence[str] | None = None, top: int = 10,
                        run: Callable[..., Any] | None = None) -> dict:
    """Re-run the analysis once per held-out group of ``source``'s edges.

    ``groups``: the groups to hold out (default: the ``top`` groups contributing most
    edges to the targets of the released pathways). ``run`` defaults to
    ``analysis.network_pharmacology.run_network_pharmacology``.
    """
    if run is None:
        from ..analysis.network_pharmacology import run_network_pharmacology as run
    snaps = list(snapshots)
    base = run(snaps, formula=formula, params=params)
    sig = {r["pathway"]: r for r in base.enrichment if r["q_value"] <= params.fdr}
    focus = set(sig)
    target_set = {t for p in focus for t in sig.get(p, {}).get("targets", [])}
    src = next((s for s in snaps if s.key == source), None)
    if src is None:
        raise ValueError(f"no {source} snapshot among the inputs")
    if groups is None:
        counts: dict[str, int] = {}
        for e in src.edges:
            if e.get("object") in target_set:
                g = group_of(e)
                if g:
                    counts[g] = counts.get(g, 0) + 1
        groups = [g for g, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    rows = []
    for g in groups:
        kept = tuple(e for e in src.edges if group_of(e) != g)
        held = len(src.edges) - len(kept)
        trial = [replace(s, edges=kept) if s.key == source else s for s in snaps]
        res = run(trial, formula=formula, params=params)
        q = {r["pathway"]: r for r in res.enrichment}
        rows.append({"held_out": g, "edges_removed": held,
                     "pathways": {p: {"q_value": q[p]["q_value"] if p in q else None,
                                      "fold": q[p]["fold_enrichment"] if p in q else None,
                                      "significant": p in q and q[p]["q_value"] <= params.fdr}
                                  for p in sorted(focus)}})
    summary = {}
    for p in sorted(focus):
        lost = [r["held_out"] for r in rows if not r["pathways"][p]["significant"]]
        summary[p] = {"name": sig[p]["name"] if p in sig else p,
                      "baseline_q": sig[p]["q_value"] if p in sig else None,
                      "survives_all": not lost, "lost_when_holding_out": lost,
                      "verdict": ("robust to every held-out group tested" if not lost else
                                  "carried by " + ", ".join(lost))}
    return {"source": source, "groups_tested": list(groups), "fdr": params.fdr,
            "pathways": summary, "runs": rows,
            "interpretation": ("a pathway lost when one group is held out is not supported "
                               "independently of that group; this does not show the signal "
                               "is false, only that it rests on one origin of data")}
