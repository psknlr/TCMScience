"""The network-pharmacology research workflow as sixteen recorded stages.

The analysis (:func:`bioagent.analysis.network_pharmacology.run_network_pharmacology`)
runs as one call: composition, measured targets, enrichment, the permutation null,
topology and the indication overlap are computed together. A reader of the result could
not see, step by step, which of the sixteen stages of a network-pharmacology study were
done, on what, and which were not. :func:`stage_records` reads the result back as those
stages, each a :class:`~bioagent.validation.Record` with one of the six validation
statuses, a reason, the counts it rests on and the command that reproduces it:

==  =======================  ==========================================================
 1  formula_identity         the formula version the herb layer records, by fingerprint
 2  herb_composition         its herbs and their source species
 3  herb_compound            the constituents the activity sources record for them
 4  structure_identity       identity and deduplication (source-supplied InChIKey)
 5  compound_target          measured compound -> protein activities at the cut-off
 6  measured_vs_predicted    predicted edges counted and kept out of the analysis
 7  disease_genes            the indication's gene set (Open Targets), when one is named
 8  intersections            measured targets x disease genes x background
 9  ppi_network              the STRING subnetwork at the confidence cut-off
10  pathway_annotation       Reactome (GO and KEGG: not available, said why)
11  enrichment_background    over-representation against a stated background
12  multiple_testing         Benjamini-Hochberg over every pathway tested
13  permutation_sensitivity  degree-matched permutation null and the rebuttals
14  topology                 degree, betweenness, components (descriptive)
15  bounded_interpretation   claims capped at mechanism_hypothesis, worded as one
16  reproducible_export      the exports written, each digest re-read from disk
==  =======================  ==========================================================

The rules a record follows:

* A stage whose data were not read is never PASS, and never negative evidence. A source
  the protocol dropped (``drop_missing_optional_source``) makes every stage that reads it
  UNVERIFIED, with the deviation attached; a source the run was not given makes the
  stages that need it SKIPPED.
* An empty result is not a pass: a stage that found nothing to test (no measured target,
  no pathway in range, no target in the network) is UNVERIFIED and says that nothing could
  be tested. In the run record (``stages.json``) such a stage is ``not_applicable``, so a
  run in which nothing could be tested is never ``complete``; a run that tested pathways
  and found none significant is (its stage 13 has nothing to permute, and says so).
* A source the skill's contract requested and the run refused (``--allow-source``, a
  disabled card, a commercial run's terms) is named, with the refusal, by every stage that
  reads it; a stage that reads nothing else (the disease stages, the PPI stages) is
  SKIPPED with that reason, never NOT_APPLICABLE.
* GO is SKIPPED: no GO annotation snapshot is registered (a new source card is a person's
  decision, ``sources/cards.py``). KEGG is BLOCKED: its pathway membership requires a
  licence, and none is recorded. Neither is ever PASS or FAIL.
* The disease stages are NOT_APPLICABLE when no disease is named.

The helpers above the stage functions (``activity_edges``, ``measured_support``,
``edge_split``, ``significant_rows``, ``power``, ``moot``) are the generic parts of the
Guizhi benchmark's stages (:mod:`bioagent.validation.benchmark`), which calls them; its
artifacts are byte-identical to what it wrote before they moved here.

This module imports nothing from the pinned skills' code and none of them imports it.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..validation import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED, Record,
                          aggregate)

__all__ = ["WORKFLOW_STAGES", "STAGE_TITLES", "ACTIVITY_SOURCES", "stage_records",
           "export_record", "manifest_stages", "runrecord_stages", "stage_limitations",
           "refused_limitations", "failed_stages",
           "nothing_tested", "overall",
           "activity_edges", "compound_counts", "measured_support", "edge_split",
           "significant_rows", "power", "moot", "reproduce_command"]

#: The sixteen stages, in order.
WORKFLOW_STAGES = (
    "formula_identity", "herb_composition", "herb_compound", "structure_identity",
    "compound_target", "measured_vs_predicted", "disease_genes", "intersections",
    "ppi_network", "pathway_annotation", "enrichment_background", "multiple_testing",
    "permutation_sensitivity", "topology", "bounded_interpretation", "reproducible_export")

STAGE_TITLES = {
    "formula_identity": "Formula identity and source version",
    "herb_composition": "Herb composition and source species",
    "herb_compound": "Herb to compound",
    "structure_identity": "Structure identity and deduplication (InChIKey)",
    "compound_target": "Compound to measured target",
    "measured_vs_predicted": "Measured versus predicted edges",
    "disease_genes": "Disease genes",
    "intersections": "Target and disease-gene intersections",
    "ppi_network": "Protein-protein interaction network",
    "pathway_annotation": "Pathway annotation (Reactome; GO; KEGG)",
    "enrichment_background": "Enrichment against an explicit background",
    "multiple_testing": "Benjamini-Hochberg across the full family",
    "permutation_sensitivity": "Degree-matched permutation and sensitivity",
    "topology": "Network topology (descriptive)",
    "bounded_interpretation": "Bounded interpretation",
    "reproducible_export": "Reproducible export",
}

#: Snapshot keys that carry compound -> protein activity (research.loop.ACTIVITY_SOURCES).
ACTIVITY_SOURCES = ("npass", "cmaup", "lotus", "bindingdb", "pubchem_bioassay", "chembl")

#: The snapshot keys each stage reads ("activity" stands for every activity source).
_NEEDS: dict[str, tuple[str, ...]] = {
    "formula_identity": ("tcm_herbs",),
    "herb_composition": ("tcm_herbs",),
    "herb_compound": ("tcm_herbs", "activity"),
    "structure_identity": ("activity",),
    "compound_target": ("activity",),
    "measured_vs_predicted": ("activity",),
    "disease_genes": ("opentargets",),
    "intersections": ("opentargets", "activity"),
    "ppi_network": ("string",),
    "pathway_annotation": ("reactome",),
    "enrichment_background": ("reactome", "activity"),
    "multiple_testing": ("reactome", "activity"),
    "permutation_sensitivity": ("reactome", "activity"),
    "topology": ("string",),
    "bounded_interpretation": (),
    "reproducible_export": (),
}

#: Stages a complete run must have done (runrecord ``required``); the others depend on an
#: optional source or on a disease being named.
_REQUIRED = frozenset(WORKFLOW_STAGES) - {"disease_genes", "intersections", "ppi_network",
                                          "topology"}

GO_SKIPPED = ("no GO annotation snapshot registered: GO would need a reviewed source card, "
              "which is a person's decision (sources/cards.py), so no GO term was tested")
KEGG_BLOCKED = ("licence required: KEGG pathway membership is licensed, no licence record "
                "permits its use here, and nothing is downloaded or bundled; this workflow has "
                "no KEGG importer, so KEGG is not tested")
EMPTY = "an empty result, not evidence of inactivity"


# --------------------------------------------------------------------------- shared helpers
# The generic parts of the benchmark's stages. Each takes the snapshots as a mapping of
# source key -> Snapshot, the analysis result as the JSON the research loop records.

def activity_edges(snaps: Mapping[str, Any], compounds: set[str]
                   ) -> list[tuple[str, dict[str, Any]]]:
    """Every compound -> protein activity edge (``targets`` or ``tested_against``) of the
    given compounds, as (source key, edge), sources in key order."""
    out = []
    for key, snap in sorted(snaps.items()):
        for e in snap.edges:
            if e.get("predicate") in ("targets", "tested_against") and e.get("subject") in compounds:
                out.append((key, e))
    return out


def compound_counts(rows: Iterable[Mapping[str, Any]], herbs: Iterable[str]
                    ) -> tuple[dict[str, int], dict[str, int]]:
    """(constituents per herb, constituents per source) of the result's compound rows."""
    per_herb = {h: 0 for h in herbs}
    per_source: dict[str, int] = {}
    for row in rows:
        for h in row.get("herbs") or ():
            per_herb[h] = per_herb.get(h, 0) + 1
        for s in row.get("sources") or ():
            per_source[s] = per_source.get(s, 0) + 1
    return per_herb, per_source


def measured_support(snaps: Mapping[str, Any], compounds: set[str], params: Any
                     ) -> dict[str, list[dict[str, Any]]]:
    """target -> the measured activity edges at or below the cut-off that support it,
    read again from the snapshots (not from the result)."""
    from ..analysis.network_pharmacology import _passes_potency

    measured: dict[str, list[dict[str, Any]]] = {}
    for key, e in activity_edges(snaps, compounds):
        if e.get("predicate") == "targets" and e.get("knowledge_level") != "prediction" \
                and _passes_potency(e.get("measure"), params):
            measured.setdefault(e["object"], []).append({"source": key, "record": e.get("source_record_id"),
                                                         "compound": e["subject"],
                                                         "measure": e.get("measure")})
    return measured


def edge_split(result: Mapping[str, Any], snaps: Mapping[str, Any],
               rebuttal: Mapping[str, Any]) -> dict[str, Any]:
    """Each edge class counted as measured or predicted, the predicted compound-target
    edges the analysis left out, targets resting on predictions only, and citations of a
    surviving hypothesis that are not a snapshot edge or not a measured activity."""
    compounds = {r["compound"] for r in result.get("compounds") or ()}
    targets = {t["target"] for t in result.get("targets") or ()}
    counts: dict[str, dict[str, int]] = {}

    def count(cls: str, e: Mapping[str, Any]) -> None:
        kind = "predicted" if e.get("knowledge_level") == "prediction" else "measured"
        counts.setdefault(cls, {"measured": 0, "predicted": 0})[kind] += 1

    activity = activity_edges(snaps, compounds)
    for _, e in activity:
        count("compound_target", e)
    for e in getattr(snaps.get("string"), "edges", ()):
        if e.get("subject") in targets and e.get("object") in targets:
            count("protein_protein (STRING)", e)
    for e in getattr(snaps.get("reactome"), "edges", ()):
        if e.get("predicate") == "participates_in" and e.get("subject") in targets:
            count("pathway_membership (Reactome)", e)
    survived = set(rebuttal.get("survived") or ())
    # only the records a surviving claim cites are looked up: a STRING snapshot holds
    # millions of edges, and indexing them all would hold a second copy in memory
    cited = {(sid, str(rid)) for claim in result.get("claims") or ()
             if claim["object"] in survived for sid, rid in claim.get("support") or ()}
    edges = {}
    for s in snaps.values():
        if not any(sid == s.snapshot_id for sid, _ in cited):
            continue
        for e in s.edges:
            key = (s.snapshot_id, str(e.get("source_record_id")))
            if key in cited:
                edges[key] = e
    bad = []
    for claim in result.get("claims") or ():
        if claim["object"] not in survived:
            continue
        for sid, rid in claim.get("support") or ():
            e = edges.get((sid, str(rid)))
            if e is None or (e.get("predicate") == "targets" and (
                    e.get("knowledge_level") == "prediction" or not e.get("measure"))):
                bad.append((claim["object"], sid, rid))
    predicted_left_out = int((result.get("excluded") or {}).get(
        "predicted target edge (not used by this skill)", 0))
    measured_targets = {e["object"] for _, e in activity
                        if e.get("predicate") == "targets" and e.get("knowledge_level") != "prediction"
                        and e.get("measure")}
    return {"counts": counts, "bad": bad, "predicted_left_out": predicted_left_out,
            "predicted_only": sorted(targets - measured_targets)}


def significant_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The enrichment rows that pass both BH and the permutation null."""
    params = result.get("parameters") or {}
    rows = result.get("enrichment") or []
    fdr, alpha = params.get("fdr", 0.05), params.get("permutation_alpha", 0.05)
    return [r for r in rows if r.get("q_value") is not None and r["q_value"] <= fdr
            and r.get("empirical_p") is not None and r["empirical_p"] <= alpha]


def power(background: Mapping[str, Any]) -> str:
    """How much the over-representation test could find: when most assayed proteins are
    potent hits, no pathway can be enriched by much, and an empty result says little."""
    proteins, potent = background.get("proteins"), background.get("potent_in_background")
    if not proteins or potent is None:
        return ""
    if not potent:
        return f"; none of the {proteins} assayed proteins is a potent hit"
    return (f"; {potent} of the {proteins} assayed proteins are potent hits, so no pathway can "
            f"exceed a {proteins / potent:.2f}-fold enrichment")


def moot(not_run: Any) -> bool:
    """A rebuttal that did not run because the primary analysis already is its conservative
    variant (the assayed background); one that could not run (a single activity source) is a
    limitation instead."""
    return isinstance(not_run, Mapping) and "already" in str(not_run.get("why", ""))


def reproduce_command(argv: Sequence[str]) -> str:
    """A shell command line for ``argv``, quoted so it can be pasted."""
    return " ".join(shlex.quote(str(a)) for a in argv)


# --------------------------------------------------------------------------- the stages

class _Ctx:
    """What every stage reads: the result, the snapshots, and what was asked for."""

    def __init__(self, result: Mapping[str, Any], formula: Any, disease: str,
                 snaps: Mapping[str, Any] | None, requested: Sequence[str] | None,
                 deviations: Sequence[Mapping[str, Any]], rebuttal: Mapping[str, Any] | None,
                 no_organism: Sequence[str], reproduce: str,
                 refused: Mapping[str, str] | None = None) -> None:
        self.result, self.formula, self.snaps = result, formula, snaps
        self.disease = disease or str((result.get("disease") or {}).get("disease") or "")
        self.requested = None if requested is None else set(requested)
        self.deviations = [dict(d) for d in deviations]
        self.dropped = {str(d.get("source")): d for d in self.deviations}
        # source key -> (the reference the contract requested, why the run refused it)
        self.refused = {str(ref).split("@", 1)[0]: (str(ref), str(why))
                        for ref, why in sorted((refused or {}).items())}
        self.rebuttal = rebuttal
        self.no_organism = list(no_organism)
        self.reproduce = reproduce
        self.params = result.get("parameters") or {}
        self.coverage = result.get("coverage") or {}
        self.snapshots = dict(result.get("snapshots") or {})

    def keys(self, stage: str) -> list[str]:
        """The source keys a stage reads, as named in the result (activity expanded)."""
        out: list[str] = []
        for need in _NEEDS[stage]:
            if need == "activity":
                out += sorted(k for k in self.snapshots if k in ACTIVITY_SOURCES)
            elif need in self.snapshots:
                out.append(need)
        return out

    def _needs(self, stage: str) -> set[str]:
        needs: set[str] = set()
        for need in _NEEDS[stage]:
            needs |= set(ACTIVITY_SOURCES) if need == "activity" else {need}
        return needs

    def missing(self, stage: str) -> list[dict[str, Any]]:
        """The protocol deviations that dropped a source this stage reads."""
        needs = self._needs(stage)
        return [d for k, d in sorted(self.dropped.items()) if k in needs]

    def refusals(self, stage: str) -> list[dict[str, str]]:
        """The sources this stage reads that the contract requested and the run refused."""
        needs = self._needs(stage)
        return [{"source": ref, "reason": why} for k, (ref, why) in sorted(self.refused.items())
                if k in needs]

    def refused_line(self, key: str) -> str | None:
        """'<ref> was requested by the run's contract and refused: <why>'."""
        if key not in self.refused:
            return None
        ref, why = self.refused[key]
        return f"{ref} was requested by the run's contract and refused: {why}"


def _rec(ctx: _Ctx, stage: str) -> Record:
    n = WORKFLOW_STAGES.index(stage) + 1
    rec = Record(f"np.workflow.{n:02d}.{stage}", "workflow_stage",
                 input={"stage": stage, "title": STAGE_TITLES[stage],
                        "reads": [k if k != "activity" else "activity sources"
                                  for k in _NEEDS[stage]]},
                 reproduce=ctx.reproduce)
    rec.provenance = {"n": n, "snapshots": [ctx.snapshots[k] for k in ctx.keys(stage)],
                      "counts": {}, "ran": True}
    for d in ctx.missing(stage):
        rec.add("sources", UNVERIFIED,
                f"protocol deviation: {d.get('fallback')} for {d.get('source')} "
                f"({d.get('reason')}); the stage ran without it, so its result is not complete",
                deviation=d)
        rec.provenance.setdefault("deviations", []).append(d)
    # what the run chose not to read is named, not hidden: the stage ran on what it was given
    if ctx.refusals(stage):
        rec.provenance["refused"] = ctx.refusals(stage)
    return rec


def _empty(rec: Record) -> None:
    """Mark a stage that ran and had nothing to work on (no measured target, nothing
    tested, no node in the network): ``not_applicable`` in the run record."""
    rec.provenance["empty"] = True


def _counts(rec: Record, **counts: Any) -> None:
    rec.provenance["counts"].update(counts)


def _formula_fields(formula: Any) -> dict[str, Any]:
    get = (formula.get if isinstance(formula, Mapping)
           else lambda k, d=None: getattr(formula, k, d))
    return {"id": get("id"), "chinese": get("chinese"), "source": get("source"),
            "fingerprint": get("fingerprint"),
            "components": [list(c)[0] for c in (get("components") or ())]}


def _formula_identity(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "formula_identity")
    f = _formula_fields(ctx.formula)
    herbs = (ctx.snaps or {}).get("tcm_herbs")
    _counts(rec, components=len(f["components"]))
    if herbs is None:
        rec.add("recorded", PASS if ctx.snapshots.get("tcm_herbs") else UNVERIFIED,
                f"{f['chinese']} ({f['source']}) is {f['id']}, composition {f['fingerprint']}; "
                "the analysis reads the composition from the herb layer and refuses one whose "
                "fingerprint differs")
        return rec
    node = next((n for n in herbs.nodes if n.get("id") == f["id"]), None) or {}
    recorded = (node.get("raw") or {}).get("fingerprint")
    edges = [e for e in herbs.edges
             if e.get("subject") == f["id"] and e.get("predicate") == "contains"]
    if not edges:
        rec.add("recorded", FAIL, f"the herb layer {herbs.snapshot_id} records no composition "
                f"for {f['id']}, yet the analysis reported one")
    elif recorded and recorded != f["fingerprint"]:
        rec.add("recorded", FAIL, f"the herb layer records composition {recorded} for {f['id']}, "
                f"not {f['fingerprint']}")
    else:
        rec.add("recorded", PASS,
                f"{f['chinese']} ({f['source']}) is {f['id']}, composition {f['fingerprint']}, "
                f"recorded with {len(edges)} component(s) in the herb layer {herbs.snapshot_id}"
                + ("" if recorded else " (the herb layer stores no fingerprint for it; its "
                   "components were matched instead)"),
                fingerprint=f["fingerprint"])
    return rec


def _herb_composition(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "herb_composition")
    herbs = ctx.coverage.get("herbs") or {}
    total, species = herbs.get("total", 0), herbs.get("with_species", 0)
    _counts(rec, components=total, with_species=species)
    if not total:
        rec.add("species", UNVERIFIED, "no component of the formula was read from the herb layer")
    elif species < total:
        rec.add("species", UNVERIFIED,
                f"{total - species} of the {total} components have no verified source species "
                "and contribute no constituents"
                + (f": {', '.join(ctx.no_organism)}" if ctx.no_organism else ""))
    else:
        rec.add("species", PASS, f"each of the {total} components has a verified source species "
                "(formula contains herb, herb has base species)")
    return rec


def _herb_compound(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "herb_compound")
    rows = ctx.result.get("compounds") or []
    f = _formula_fields(ctx.formula)
    per_herb, per_source = compound_counts(rows, f["components"])
    levels: dict[str, int] = {}
    for r in rows:
        levels[str(r.get("level"))] = levels.get(str(r.get("level")), 0) + 1
    herbs = ctx.coverage.get("herbs") or {}
    below = int((ctx.result.get("excluded") or {}).get("composition below the minimum level", 0))
    _counts(rec, compounds=len(rows), per_herb=per_herb, per_source=per_source,
            composition_levels=levels, below_minimum_level=below)
    if not rows:
        rec.add("constituents", UNVERIFIED,
                "no constituent of the formula is recorded in these snapshots, so nothing "
                f"downstream could be tested; {EMPTY}")
    elif herbs.get("with_compound", 0) < herbs.get("with_species", 0):
        none = sorted(h for h, n in per_herb.items() if not n)
        rec.add("constituents", UNVERIFIED,
                f"{len(rows)} constituents; no constituent is recorded for {', '.join(none)} in "
                "these snapshots (absence from a snapshot is not absence from the plant)")
    else:
        rec.add("constituents", PASS,
                f"{len(rows)} constituents at composition level "
                f"{ctx.params.get('composition_min_level', 'C1')} or above, every component "
                "with a species contributing: " + ", ".join(f"{h} {n}" for h, n in per_herb.items()))
    return rec


def _structure_identity(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "structure_identity")
    ident = ctx.coverage.get("identity") or {}
    total = (ctx.coverage.get("compounds") or {}).get("total", 0)
    _counts(rec, compounds=total, with_inchikey=ident.get("with_inchikey", 0),
            source_local=ident.get("source_local", 0),
            merged_across_sources=ident.get("merged_across_sources", 0),
            connectivity_groups=ident.get("connectivity_groups", 0))
    if not total:
        rec.add("deduplication", UNVERIFIED, "no constituent to identify")
    else:
        rec.add("deduplication", PASS,
                f"{total} compounds, one per identical full key: {ident.get('with_inchikey', 0)} "
                f"by standard InChIKey, {ident.get('source_local', 0)} by the source's own id; "
                f"{ident.get('merged_across_sources', 0)} reported by more than one source were "
                "merged on the identical key")
    rec.add("standardisation", UNVERIFIED,
            "structures were not re-standardised: the snapshots carry no structure (SMILES or "
            "InChI), so deduplication rests on the source-supplied standard InChIKey; "
            f"{ident.get('connectivity_groups', 0)} group(s) of distinct keys share a "
            "connectivity block and are reported, not merged")
    return rec


def _activity_index(ctx: _Ctx) -> dict[tuple[str, str], Mapping[str, Any]]:
    """The activity edges the target rows cite, by (snapshot id, record id)."""
    wanted = {(sid, str(rid)) for t in ctx.result.get("targets") or ()
              for sid, rid in t.get("records") or ()}
    out = {}
    for key, snap in sorted((ctx.snaps or {}).items()):
        if key in ACTIVITY_SOURCES:
            for e in snap.edges:
                ref = (snap.snapshot_id, str(e.get("source_record_id")))
                if ref in wanted:
                    out[ref] = e
    return out


def _compound_target(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "compound_target")
    targets = ctx.result.get("targets") or []
    cov = ctx.coverage.get("compounds") or {}
    screening = ctx.params.get("hits") == "screening"
    excluded = {k: v for k, v in sorted((ctx.result.get("excluded") or {}).items())
                if k != "composition below the minimum level"}
    _counts(rec, targets=len(targets),
            measurements=sum(int(t.get("measurements") or 0) for t in targets),
            compounds_with_target=cov.get("with_measured_target", 0), excluded=excluded)
    what = "active screening result" if screening else "measured target"
    if not targets:
        rec.add("targets", UNVERIFIED,
                f"No {what} was found for the {cov.get('total', 0)} constituents in these "
                f"snapshots, so no enrichment could be tested; {EMPTY}")
        return rec
    if ctx.snaps is None:
        rec.add("targets", UNVERIFIED, f"{len(targets)} targets; the snapshots were not given "
                "to re-read the edges they rest on")
        return rec
    from ..analysis.network_pharmacology import Parameters, _passes_potency
    params = Parameters(**{k: tuple(v) if isinstance(v, list) else v
                           for k, v in ctx.params.items()}) if ctx.params else Parameters()
    index = _activity_index(ctx)
    unsupported = []
    for t in targets:
        ok = False
        for sid, rid in t.get("records") or ():
            e = index.get((sid, str(rid)))
            if (e is not None and e.get("predicate") == "targets"
                    and e.get("object") == t["target"]
                    and e.get("knowledge_level") != "prediction"
                    and (screening or _passes_potency(e.get("measure"), params))):
                ok = True
                break
        if not ok:
            unsupported.append(t["target"])
    if unsupported:
        rec.add("targets", FAIL, f"{len(unsupported)} target(s) rest on no measured snapshot "
                f"edge: {unsupported[:10]}", unsupported=unsupported[:50])
    else:
        rec.add("targets", PASS,
                f"{len(targets)} human proteins, each resting on a {what} edge re-read from "
                "its snapshot" + ("" if screening else
                                  f" at or below {float(params.activity_max_nm):g} nM"))
    return rec


def _measured_vs_predicted(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "measured_vs_predicted")
    left_out = int((ctx.result.get("excluded") or {}).get(
        "predicted target edge (not used by this skill)", 0))
    _counts(rec, predicted_left_out=left_out)
    if ctx.snaps is None:
        rec.add("split", UNVERIFIED, f"{left_out} predicted compound-target edge(s) were counted "
                "and left out; the snapshots were not given to re-check the split")
        return rec
    rebuttal = ctx.rebuttal if ctx.rebuttal is not None else {
        "survived": [c["object"] for c in (ctx.result.get("release") or {}).get("released") or ()]}
    split = edge_split(ctx.result, ctx.snaps, rebuttal)
    _counts(rec, edge_classes=split["counts"])
    only = split["predicted_only"]
    if not ctx.result.get("targets"):
        # no target at all: that none rests on a prediction is true of nothing
        rec.add("targets_from_measurements", UNVERIFIED,
                f"no target was found, so there was no target to check; {left_out} predicted "
                f"compound-target edge(s) were counted and left out of the analysis; {EMPTY}")
    else:
        rec.add("targets_from_measurements", FAIL if only else PASS,
                f"no target rests on a prediction; {left_out} predicted compound-target edge(s) "
                "were counted and left out of the analysis" if not only else
                f"{len(only)} target(s) rest on predicted edges only: {only[:10]}")
    if not split["bad"] and not set(rebuttal.get("survived") or ()):
        rec.add("hypotheses_cite_measured_edges", NOT_APPLICABLE,
                "no hypothesis survived, so there is no citation to check")
    else:
        rec.add("hypotheses_cite_measured_edges", FAIL if split["bad"] else PASS,
                "every edge a surviving hypothesis cites is a snapshot edge, its compound-target "
                "step a measured activity" if not split["bad"] else
                f"unsupported citations: {split['bad'][:5]}")
    topology = (ctx.result.get("network") or {}).get("interpretation", "")
    rec.add("topology_descriptive", PASS if "descriptive" in topology else FAIL,
            "STRING edges are predictions and feed only the descriptive topology")
    return rec


def _not_applicable(rec: Record, why: str) -> Record:
    rec.provenance["ran"] = False
    rec.add("applies", NOT_APPLICABLE, why)
    return rec


def _no_disease(ctx: _Ctx, rec: Record, so: str) -> Record:
    """A disease stage with no disease gene set read: SKIPPED when the run refused the
    Open Targets snapshot its contract requested (a disease was named, and not read),
    NOT_APPLICABLE only when no disease was named at all."""
    refused = ctx.refused_line("opentargets")
    if refused:
        rec.provenance["ran"] = False
        rec.add("applies", SKIPPED, f"{refused}, so {so}")
        return rec
    return _not_applicable(rec, f"no disease was named, so {so}")


def _disease_genes(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "disease_genes")
    disease = ctx.result.get("disease") or {}
    if not disease:
        return _no_disease(ctx, rec, "no disease gene set was read")
    genes = disease.get("disease_genes", 0)
    _counts(rec, disease=disease.get("disease"), disease_genes=genes,
            evidence=disease.get("evidence"), min_score=disease.get("min_score"),
            literature_only=(disease.get("literature_only") or {}).get("disease_genes", 0))
    if genes:
        rec.add("gene_set", PASS,
                f"{genes} genes reach {disease.get('min_score')} in "
                f"{disease.get('evidence')} for {disease.get('name')} ({disease.get('disease')}), "
                f"from {disease.get('snapshot')}")
    else:
        rec.add("gene_set", UNVERIFIED,
                f"no gene reaches {disease.get('min_score')} in {disease.get('evidence')} for "
                f"{disease.get('disease')} in this snapshot: an empty gene set, not evidence that "
                "no gene is associated")
    for kind in disease.get("declared_absent") or ():
        rec.add(f"datatype:{kind}", UNVERIFIED, f"the snapshot holds no {kind} associations, so "
                "that datatype contributes nothing")
    return rec


def _intersections(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "intersections")
    disease = ctx.result.get("disease") or {}
    if not disease:
        return _no_disease(ctx, rec, "there is nothing to intersect with")
    _counts(rec, overlap=disease.get("overlap", 0), in_background=disease.get("in_background", 0),
            query=disease.get("query", 0), p_value=disease.get("p_value"),
            empirical_p=disease.get("empirical_p"))
    if not disease.get("query"):
        rec.add("overlap", UNVERIFIED, "no measured target is in the background, so the "
                f"intersection could not be tested; {EMPTY}")
    else:
        rec.add("overlap", PASS,
                f"{disease.get('overlap', 0)} of the {disease.get('query')} measured targets in the "
                f"background are among the {disease.get('in_background', 0)} disease genes there "
                f"(hypergeometric p = {disease.get('p_value')}, degree-matched permutation p = "
                f"{disease.get('empirical_p')}); descriptive, not a claim")
    return rec


def _ppi_missing(ctx: _Ctx, rec: Record) -> Record | None:
    """The record of a stage that needs STRING when none was read; None when one was."""
    network = ctx.result.get("network") or {}
    if network.get("ppi_source"):
        return None
    rec.provenance["ran"] = False
    why = ("No PPI snapshot was read: the network has no edges because none were read, not "
           "because none exist")
    if "string" in ctx.dropped:
        # the stage did not run at all, so the deviation is the reason, not a caveat
        d = ctx.dropped["string"]
        rec.checks = [c for c in rec.checks if c.name != "sources"]
        rec.add("ppi", UNVERIFIED, f"{why} (protocol deviation: {d.get('fallback')} for "
                f"string: {d.get('reason')})", deviation=d)
    elif ctx.refused_line("string"):
        rec.add("ppi", SKIPPED, f"{why} ({ctx.refused_line('string')})")
    elif ctx.requested is not None and "string" in ctx.requested:
        rec.add("ppi", UNVERIFIED, why + " (STRING was asked for and not given)")
    else:
        rec.add("ppi", SKIPPED, why + " (this run did not ask for STRING)")
    return rec


def _ppi_empty(ctx: _Ctx, rec: Record) -> Record | None:
    """The record of a PPI stage whose network has no node (STRING was read, but no measured
    target is in the background to place in it); None when it has one."""
    network = ctx.result.get("network") or {}
    if network.get("nodes") or network.get("edge_list"):
        return None                     # (an edge list without nodes fails the edge check)
    _counts(rec, nodes=0, edges=0, string_min_score=network.get("string_min_score"))
    _empty(rec)
    rec.add("ppi", UNVERIFIED,
            f"STRING {network.get('ppi_source')} was read, but no measured target is in the "
            "background, so no network could be built; an empty result, not evidence of no "
            "interaction")
    return rec


def _ppi_network(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "ppi_network")
    missing = _ppi_missing(ctx, rec)
    if missing is None:
        missing = _ppi_empty(ctx, rec)
    if missing is not None:
        return missing
    network = ctx.result.get("network") or {}
    edges = network.get("edge_list") or []
    cut = network.get("string_min_score")
    _counts(rec, nodes=network.get("nodes", 0), edges=len(edges), string_min_score=cut,
            components=network.get("components", 0))
    if len(edges) != network.get("edges", 0):
        rec.add("edge_list", FAIL, f"the edge list holds {len(edges)} edges, the network "
                f"{network.get('edges')}")
        return rec
    string = (ctx.snaps or {}).get("string")
    if string is None:
        rec.add("edge_list", UNVERIFIED, f"{len(edges)} edges at STRING score >= {cut}; the "
                "snapshot was not given to re-read them")
        return rec
    nodes = {t["target"] for t in ctx.result.get("targets") or () if t.get("degree") is not None}
    expected = set()
    for e in string.edges:
        a, b = e.get("subject"), e.get("object")
        if a in nodes and b in nodes and float(e.get("score") or 0) >= float(cut or 0):
            expected.add((a, b) if a <= b else (b, a))
    got = {(e["a"], e["b"]) for e in edges}
    rec.add("edge_list", PASS if got == expected else FAIL,
            f"{len(edges)} edges among {len(nodes)} targets at STRING score >= {cut}, each the "
            f"record it was read from in {string.snapshot_id}" if got == expected else
            f"the edge list differs from the snapshot's edges at the cut-off "
            f"({len(got - expected)} extra, {len(expected - got)} missing)")
    return rec


def _pathway_annotation(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "pathway_annotation")
    background = ctx.result.get("background") or {}
    pathways = ctx.coverage.get("pathways") or {}
    _counts(rec, annotated_proteins=background.get("annotated", 0),
            pathways_in_size_range=pathways.get("in_size_range", 0),
            size_range=[ctx.params.get("pathway_min_size"), ctx.params.get("pathway_max_size")])
    if ctx.snapshots.get("reactome") and background.get("annotated"):
        rec.add("reactome", PASS,
                f"Reactome {ctx.snapshots['reactome']}: {background.get('annotated')} human "
                f"proteins annotated, {pathways.get('in_size_range', 0)} pathways of "
                f"{ctx.params.get('pathway_min_size')}-{ctx.params.get('pathway_max_size')} "
                "members (lowest-level pathways, as UniProt2Reactome lists them)")
    else:
        rec.add("reactome", UNVERIFIED, "no protein is annotated in the Reactome snapshot read")
    rec.add("go", SKIPPED, GO_SKIPPED)
    rec.add("kegg", BLOCKED, KEGG_BLOCKED)
    return rec


def _nothing_tested(ctx: _Ctx) -> str | None:
    return nothing_tested(ctx.result)


def nothing_tested(result: Mapping[str, Any]) -> str | None:
    """Why no enrichment could be tested in this result, or None when some was: the
    difference between "nothing could be tested" and "tested, and none survived"."""
    background = result.get("background") or {}
    rows = result.get("enrichment") or []
    coverage = result.get("coverage") or {}
    targets = coverage.get("targets") or {}
    compounds = (coverage.get("compounds") or {}).get("total", 0)
    if not targets.get("measured"):
        return (f"No measured target was found for the {compounds} constituents in these "
                f"snapshots, so no enrichment could be tested; {EMPTY}")
    if not background.get("potent_in_background"):
        return ("no measured target is in the background, so no enrichment could be tested; "
                f"{EMPTY}")
    if not rows:
        return f"no pathway in the size range has a member in the background; {EMPTY}"
    return None


def _enrichment_background(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "enrichment_background")
    background = ctx.result.get("background") or {}
    rows = ctx.result.get("enrichment") or []
    query = int(background.get("potent_in_background") or 0)
    _counts(rec, background=background.get("kind"), proteins=background.get("proteins", 0),
            query=query, pathways_tested=len(rows), hit_rate=background.get("hit_rate"))
    why = _nothing_tested(ctx)
    if why:
        _empty(rec)
        rec.add("tested", UNVERIFIED, why)
    elif query < 3:
        rec.add("tested", UNVERIFIED, f"{query} measured target(s) in the background: too few "
                "for an over-representation test to say anything")
    else:
        rec.add("tested", PASS, f"{len(rows)} pathways tested against the "
                f"{background.get('kind')} background with {query} targets in it"
                + power(background))
    rec.add("background", PASS if background.get("kind") else FAIL,
            f"the background ({background.get('kind')}, {background.get('proteins', 0)} "
            "proteins) is a parameter of the protocol, fixed before the test")
    return rec


def _multiple_testing(ctx: _Ctx) -> Record:
    from ..tools.stats import benjamini_hochberg

    rec = _rec(ctx, "multiple_testing")
    rows = ctx.result.get("enrichment") or []
    fdr = ctx.params.get("fdr", 0.05)
    passing = [r for r in rows if r.get("q_value") is not None and r["q_value"] <= fdr]
    _counts(rec, family=len(rows), fdr=fdr, passing=len(passing))
    why = _nothing_tested(ctx)
    if not rows or why:
        # with no target to test, every pathway's p is 1 by construction: a correction
        # over them establishes nothing
        _empty(rec)
        rec.add("family", UNVERIFIED, (why or "no pathway was tested")
                + "; there was no test to correct")
        return rec
    q = benjamini_hochberg([r["p_value"] for r in rows])["q_values"]
    differ = [r["pathway"] for r, v in zip(rows, q) if abs(float(r.get("q_value", -1)) - v) > 1e-9]
    tested = (ctx.result.get("background") or {}).get("pathways_tested", len(rows))
    rec.add("family", FAIL if differ or tested != len(rows) else PASS,
            f"Benjamini-Hochberg over all {len(rows)} pathways tested (every pathway in the size "
            "range with a member in the background, not only those the targets reach); "
            f"{len(passing)} at q <= {fdr}; the q-values recompute exactly"
            if not differ and tested == len(rows) else
            f"the q-values do not recompute over the full family: {differ[:5]}")
    return rec


def _permutation_sensitivity(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "permutation_sensitivity")
    rows = ctx.result.get("enrichment") or []
    fdr = ctx.params.get("fdr", 0.05)
    perms = int(ctx.params.get("permutations") or 0)
    candidates = [r for r in rows if r.get("q_value") is not None and r["q_value"] <= fdr]
    significant = significant_rows(ctx.result)
    _counts(rec, bh_significant=len(candidates), permutations=perms,
            pass_both=len(significant))
    why = _nothing_tested(ctx)
    if why:
        _empty(rec)
        rec.add("permutation", UNVERIFIED, why + "; no permutation null was drawn")
    elif not candidates:
        # tested, and none passed BH: there is nothing to permute, and the stage is done
        rec.add("permutation", NOT_APPLICABLE,
                f"no pathway passed BH at q <= {fdr}, so there was nothing to permute; no "
                "permutation null was drawn")
    else:
        floor = 1 / (perms + 1) if perms else 0
        bad = [r["pathway"] for r in candidates
               if r.get("empirical_p") is None or r["empirical_p"] < floor - 1e-12]
        rec.add("permutation", FAIL if bad else PASS,
                f"{len(candidates)} BH-significant pathway(s), each against {perms} degree-matched "
                f"draws seeded per pathway; {len(significant)} pass both" if not bad else
                f"BH-significant pathways without a valid permutation p: {bad[:5]}")
    if ctx.rebuttal is None:
        rec.add("rebuttals", SKIPPED, "this runner makes no rebuttal; the research job "
                "(bioagent research) tests each claim against the pre-registered rebuttals")
    elif not ctx.rebuttal.get("primary"):
        rec.add("rebuttals", NOT_APPLICABLE, "no claim reached the rebuttals")
    else:
        not_run = list(ctx.rebuttal.get("not_run") or ())
        limiting = [r for r in not_run if not moot(r)]
        tests = [t["test"] for t in ctx.rebuttal.get("tests") or ()]
        _counts(rec, rebuttal_tests=tests, survived=len(ctx.rebuttal.get("survived") or ()),
                refuted=len(ctx.rebuttal.get("refuted") or ()))
        rec.add("rebuttals", UNVERIFIED if limiting else PASS,
                f"{len(ctx.rebuttal.get('survived') or ())} survived and "
                f"{len(ctx.rebuttal.get('refuted') or ())} were refuted by "
                f"{', '.join(tests) or 'no test'}"
                + (f"; could not run: {[r.get('test') for r in limiting]}" if limiting else ""))
    return rec


def _topology(ctx: _Ctx) -> Record:
    rec = _rec(ctx, "topology")
    missing = _ppi_missing(ctx, rec)
    if missing is None:
        missing = _ppi_empty(ctx, rec)
    if missing is not None:
        return missing
    network = ctx.result.get("network") or {}
    _counts(rec, nodes=network.get("nodes", 0), edges=network.get("edges", 0),
            components=network.get("components", 0),
            largest_component=network.get("largest_component", 0))
    descriptive = "descriptive" in str(network.get("interpretation", ""))
    rec.add("descriptive", PASS if descriptive else FAIL,
            f"degree, betweenness and components of the {network.get('nodes', 0)}-node, "
            f"{network.get('edges', 0)}-edge network; descriptive, no significance is claimed")
    return rec


def _bounded_interpretation(ctx: _Ctx) -> Record:
    from ..contracts.claim_language import overreaching_language

    rec = _rec(ctx, "bounded_interpretation")
    claims = ctx.result.get("claims") or []
    release = ctx.result.get("release") or {}
    survived = (set(ctx.rebuttal.get("survived") or ()) if ctx.rebuttal is not None
                else {c["object"] for c in release.get("released") or ()})
    kept = [c for c in claims if c["object"] in survived]
    _counts(rec, candidates=len(claims), released=len(release.get("released") or ()),
            refused=len(release.get("refused") or ()), hypotheses=len(kept))
    problems = []
    for c in claims:
        if c.get("kind") != "mechanism_hypothesis":
            problems.append(f"{c['object']} is a {c.get('kind')} claim")
        found = overreaching_language(c.get("statement", ""), c.get("kind", ""))
        if found:
            problems.append(f"{c['object']}: wording claims more than a hypothesis: "
                            f"{[f.phrase for f in found]}")
    if problems:
        rec.add("claims", FAIL, "; ".join(problems))
    elif kept:
        rec.add("claims", PASS, f"{len(kept)} mechanism hypothesis(es), each citing the snapshot "
                "edges of one supporting path and worded as a hypothesis awaiting test")
    elif _nothing_tested(ctx):
        # nothing could be tested: there is no claim to bound, and no wording to check
        _empty(rec)
        rec.add("claims", UNVERIFIED,
                "no hypothesis is made because nothing could be tested, so there was no claim "
                f"to bound; {EMPTY}")
    else:
        rec.add("claims", PASS,
                "no hypothesis is made: pathways were tested and none survived BH, the "
                "permutation null" + (" and the rebuttals" if ctx.rebuttal is not None else "")
                + "; that is an absence of evidence for enrichment, not evidence that any "
                "pathway is uninvolved")
    if release.get("refused"):
        rec.add("release", UNVERIFIED, f"{len(release['refused'])} candidate claim(s) were "
                "refused at release and are not reported")
    return rec


_STAGES = (_formula_identity, _herb_composition, _herb_compound, _structure_identity,
           _compound_target, _measured_vs_predicted, _disease_genes, _intersections,
           _ppi_network, _pathway_annotation, _enrichment_background, _multiple_testing,
           _permutation_sensitivity, _topology, _bounded_interpretation)


def stage_records(result: Mapping[str, Any], *, formula: Any, disease: str = "",
                  snaps: Mapping[str, Any] | None = None,
                  requested: Sequence[str] | None = None,
                  deviations: Sequence[Mapping[str, Any]] = (),
                  rebuttal: Mapping[str, Any] | None = None,
                  no_organism: Sequence[str] = (), reproduce: str = "",
                  refused: Mapping[str, str] | None = None) -> list[Record]:
    """Stages 1-15 of one analysis, as validation records (stage 16 is
    :func:`export_record`, made once the exports are on disk).

    ``result`` is the analysis result as JSON data (``NetworkPharmacologyResult.as_dict``
    or the research loop's ``result.json``); ``formula`` the ``FormulaVersion`` (or its
    fields); ``snaps`` the snapshots it read, by key, for the checks that re-read edges
    (without them those checks are UNVERIFIED); ``requested`` the source keys the run
    asked for; ``deviations`` the protocol's recorded deviations; ``rebuttal`` the
    research loop's rebuttal record (None for a run that makes none); ``refused`` the
    sources the run's contract requested and the run refused, reference -> reason
    (``SkillContract.grant``).
    """
    ctx = _Ctx(result, formula, disease, snaps, requested, deviations, rebuttal, no_organism,
               reproduce, refused)
    return [fn(ctx) for fn in _STAGES]


def file_digest(path: str | Path) -> tuple[str, int]:
    """("sha256:<hex>", bytes) of a file."""
    data = Path(path).read_bytes()
    return "sha256:" + hashlib.sha256(data).hexdigest(), len(data)


def export_record(out: str | Path, files: Mapping[str, Mapping[str, Any]], *,
                  snapshots: Mapping[str, str], reproduce: str = "") -> Record:
    """Stage 16: every export named in ``files`` (name -> {sha256, bytes}) is read back from
    ``out`` and must have the digest and size recorded for it, and the run names the
    snapshot ids it read."""
    n = WORKFLOW_STAGES.index("reproducible_export") + 1
    rec = Record(f"np.workflow.{n:02d}.reproducible_export", "workflow_stage",
                 input={"stage": "reproducible_export",
                        "title": STAGE_TITLES["reproducible_export"], "reads": []},
                 reproduce=reproduce)
    rec.provenance = {"n": n, "snapshots": sorted(snapshots.values()),
                      "counts": {"files": len(files)}, "ran": True}
    bad = []
    for name, entry in sorted(files.items()):
        try:
            digest, size = file_digest(Path(out) / name)
        except OSError:
            bad.append(f"{name}: missing")
            continue
        if digest != entry.get("sha256") or size != entry.get("bytes"):
            bad.append(f"{name}: differs from its recorded digest")
    rec.add("digests", FAIL if bad or not files else PASS,
            f"{len(files)} exports written; each one read back from disk has the SHA-256 and "
            "size recorded for it" if not bad and files else
            "; ".join(bad) or "no export was written")
    rec.add("snapshots", PASS if snapshots else FAIL,
            f"the {len(snapshots)} snapshot ids the run read are recorded"
            if snapshots else "no snapshot id is recorded")
    return rec


def _reason(rec: Record) -> str:
    return rec.failure_reason or next((c.detail for c in rec.checks if c.detail), "")


def manifest_stages(records: Sequence[Record]) -> list[dict[str, Any]]:
    """The stages as ``run_manifest.json`` and the report list them: number, id, title,
    validation status, the reason, the counts, each check, the snapshots read, any protocol
    deviation and the command that reproduces it. No timings."""
    out = []
    for rec in records:
        stage = rec.input["stage"]
        doc = {"n": rec.provenance["n"], "id": stage, "title": STAGE_TITLES[stage],
               "status": rec.status, "reason": _reason(rec),
               "counts": rec.provenance.get("counts") or {},
               "checks": [{"name": c.name, "status": c.status, "detail": c.detail}
                          for c in rec.checks],
               "snapshots": rec.provenance.get("snapshots") or [],
               "reproduce": rec.reproduce}
        if rec.provenance.get("deviations"):
            doc["deviations"] = rec.provenance["deviations"]
        if rec.provenance.get("refused"):
            doc["refused"] = rec.provenance["refused"]
        out.append(doc)
    return out


#: The exports each stage's result is written to (runrecord ``outputs``).
_OUTPUTS = {
    "herb_compound": ("compounds.tsv",), "structure_identity": ("compounds.tsv",),
    "compound_target": ("targets.tsv",), "measured_vs_predicted": ("targets.tsv",),
    "disease_genes": ("disease_targets.tsv",), "intersections": ("disease_targets.tsv",),
    "ppi_network": ("network.tsv",), "topology": ("network.tsv", "targets.tsv"),
    "pathway_annotation": ("enrichment.tsv",), "enrichment_background": ("enrichment.tsv",),
    "multiple_testing": ("enrichment.tsv",), "permutation_sensitivity": ("enrichment.tsv",),
    "bounded_interpretation": ("limitations.md",),
}


def runrecord_stages(records: Sequence[Record], *, engine: str, version: str | None,
                     outputs: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The stages in :mod:`bioagent.runrecord`'s form (``stages.json``): status is what the
    stage did, check its validation status. ``outputs`` limits each stage's outputs to the
    files written.

    A stage whose check FAILed ``failed``; one that did not run is ``skipped`` (the run
    chose not to read its source), ``not_applicable`` (no disease named) or ``unavailable``
    (its source could not be read); one that ran with nothing to work on (nothing could be
    tested, no node in the network) is ``not_applicable``, so a run in which nothing could
    be tested is not complete; every other stage that ran ``succeeded``, whatever its check
    (a check that is NOT_APPLICABLE because there was nothing to permute included)."""
    from ..runrecord import Stage

    written = set(outputs)
    out = []
    for rec in records:
        stage, status = rec.input["stage"], rec.status
        ran = rec.provenance.get("ran", True)
        if status == FAIL:
            word = "failed"
        elif not ran:
            word = {SKIPPED: "skipped", NOT_APPLICABLE: "not_applicable"}.get(status,
                                                                              "unavailable")
        elif rec.provenance.get("empty"):
            word = "not_applicable"
        else:
            word = "succeeded"
        files = (sorted(written) if stage == "reproducible_export" else
                 [f for f in _OUTPUTS.get(stage, ()) if f in written])
        st = Stage(name=stage, required=stage in _REQUIRED, status=word, check=status,
                   reason=_reason(rec) or None, engine=engine if ran else None,
                   version=version if ran else None, device="cpu" if ran else None,
                   seconds=None, outputs=files if ran else [])
        st.validate()
        out.append(st.as_dict())
    return out


def stage_limitations(records: Sequence[Record]) -> list[str]:
    """One line for each stage that is not PASS or NOT_APPLICABLE, saying what it did not
    establish and why: what a reader must know the workflow did not do."""
    out = []
    for rec in records:
        if rec.status in (PASS, NOT_APPLICABLE):
            continue
        stage = rec.input["stage"]
        parts = [f"{c.name} {c.status} ({c.detail})" for c in rec.checks
                 if c.status not in (PASS, NOT_APPLICABLE)]
        out.append(f"Workflow stage {rec.provenance['n']} ({STAGE_TITLES[stage]}): "
                   f"{rec.status}. " + "; ".join(parts) + ".")
    return out


def refused_limitations(refused: Mapping[str, str] | None) -> list[str]:
    """One line naming the sources the run's contract requested and the run refused, with
    each refusal: what every stage that reads them ran without."""
    if not refused:
        return []
    named = ", ".join(f"{ref} ({why})" for ref, why in sorted(refused.items()))
    return [f"Sources the run's contract requested and this run refused: {named}; every stage "
            "that reads them ran without them, so its result is what the remaining sources "
            "record, not what the full request would have found."]


def overall(records: Sequence[Record]) -> str:
    """The workflow's status over its stages (``bioagent.validation.aggregate``)."""
    return aggregate(r.status for r in records)


def failed_stages(stages: Iterable[Mapping[str, Any]]) -> list[str]:
    """'stage <n> <id>: FAIL: <reason>' for each stage a manifest lists as FAIL: a run whose
    own check failed must not end as a clean success (the research command exits 3)."""
    return [f"stage {s.get('n')} {s.get('id')}: FAIL: {s.get('reason') or 'no reason recorded'}"
            for s in stages if isinstance(s, Mapping) and s.get("status") == FAIL]
