"""Does a mechanism signal survive when the evidence it rests on is held out by its origin?

A pathway enriched among a formula's screening hits may be specific to the formula, or it
may be carried by one assay campaign (a CYP inhibition panel that tested thousands of
compounds, a luciferase reporter prone to interference). The test here re-runs the
analysis with one *group* of evidence removed at a time — an assay campaign (PubChem AID),
an assay family, or one original study whichever database redistributed it — and reports,
for each pathway the full analysis released, whether it is still significant.

All rows of one source-specific AID are held out together. An original study
redistributed by several databases can be held out across all those sources by its study ID.

Each holdout records whether the pathway could still be tested and whether it passed
the same statistical criteria as the primary analysis.
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
                        across_sources: bool = False, run: Callable[..., Any] | None = None) -> dict:
    """Re-run the analysis once per held-out group of ``source``'s edges.

    ``groups``: the groups to hold out (default: the ``top`` groups contributing most
    edges to the targets of the released pathways). For shared study identifiers such as
    PMIDs, set ``across_sources=True`` so all contributing datasets lose the same origin.
    AIDs and assay family labels are scoped to their source by default. ``run`` defaults to
    ``analysis.network_pharmacology.run_network_pharmacology``.
    """
    if run is None:
        from ..analysis.network_pharmacology import run_network_pharmacology as run
    snaps = list(snapshots)
    base = run(snaps, formula=formula, params=params)
    from ..research.workflow import significant_rows

    def passing(result):
        return {row["pathway"] for row in significant_rows({
            "enrichment": result.enrichment,
            "parameters": {"fdr": params.fdr, "permutation_alpha": params.permutation_alpha}})}

    baseline_passing = passing(base) & {c["object"] for c in base.release["released"]}
    sig = {r["pathway"]: r for r in base.enrichment if r["pathway"] in baseline_passing}
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
    for g in dict.fromkeys(groups):
        trial = []
        held = 0
        for snap in snaps:
            if snap.key == source or across_sources:
                kept = tuple(e for e in snap.edges if group_of(e) != g)
                held += len(snap.edges) - len(kept)
                trial.append(replace(snap, edges=kept))
            else:
                trial.append(snap)
        res = run(trial, formula=formula, params=params)
        passed = passing(res)
        q = {r["pathway"]: r for r in res.enrichment}
        if not held or not any(s.edges for s in trial):
            q.clear()
        bg = getattr(res, "background", None)
        if bg is not None and bg.get("kind") != "screening" and not bg.get("potent_in_background"):
            q.clear()
        rows.append({"held_out": g, "edges_removed": held,
                     "pathways": {p: {"q_value": q[p]["q_value"] if p in q else None,
                                      "fold": q[p]["fold_enrichment"] if p in q else None,
                                      "testable": p in q,
                                      "significant": (p in passed) if p in q else None}
                                  for p in sorted(focus)}})
    summary = {}
    for p in sorted(focus):
        lost = [r["held_out"] for r in rows if r["pathways"][p]["significant"] is False]
        missing = [r["held_out"] for r in rows if not r["pathways"][p]["testable"]]
        summary[p] = {"name": sig[p]["name"], "baseline_q": sig[p]["q_value"],
                      "survives_all": False if lost else (None if missing or not rows else True),
                      "untestable_when_holding_out": missing, "lost_when_holding_out": lost,
                      "verdict": ("lost significance after holding out " + ", ".join(lost) if lost else
                                  "insufficient independent evidence" if missing or not rows else
                                  "robust to every held-out group tested")}
    return {"source": source, "across_sources": across_sources,
            "groups_tested": [r["held_out"] for r in rows], "fdr": params.fdr,
            "pathways": summary, "runs": rows,
            "interpretation": ("a pathway lost when one group is held out is not supported "
                               "independently of that group; this does not show the signal "
                               "is false, a missing or untestable comparison cannot refute it")}
