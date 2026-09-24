"""Network pharmacology of a formula, on verified snapshots, ending in releasable hypotheses.

The pipeline for ``tcm.network-pharmacology`` (``skills/tcm/network-pharmacology``):

1. **Composition** — formula -> herbs (as the source text records them) -> source species
   -> compounds, each at a composition level (``sources.composition``).
2. **Measured targets** — compound -> protein edges with a potency value (IC50, Ki, Kd,
   EC50) at or below a cut-off, from sources that record the measurement. Predicted
   targets are not used by this skill.
3. **Pathway over-representation** — Reactome, pathways of a stated size range,
   Benjamini–Hochberg over *every* pathway tested (not only those that happen to overlap
   the query). The background is, by default, the *assayed* proteins: every human protein
   the formula's compounds were measured against, potent or not. Natural products are
   screened against the same panels again and again (carbonic anhydrases, transporters,
   cytochromes P450); against the whole human annotation, a pathway can look enriched
   because its proteins were tested, not because they were hit. Against the assayed
   background the question is which pathways the *potent* results concentrate in, given
   what was tested. The whole-annotation background remains available as a parameter.
4. **Annotation-bias control** — a pathway that passes BH must also beat a degree-matched
   permutation null: random target sets drawn so each target is matched on how many
   pathways it is annotated to. Well-studied proteins sit in many pathways; without this,
   "enriched" can mean "well studied".
5. **Network topology** — the STRING subnetwork over the targets at a stated confidence,
   reported as description: degree, betweenness, components. No significance is claimed
   for it.
6. **Indication overlap** (when an Open Targets snapshot is given) — the measured targets
   against one disease's gene set, defined from one stated evidence type at a stated
   score (by default human genetic association, not literature co-mention, which would be
   circular for a literature-derived compound network). Hypergeometric against the same
   background plus the same degree-matched null; reported as a statistic, never as a claim.
7. **Candidate claims** — one ``mechanism_hypothesis`` per pathway passing 3 and 4, each
   citing the exact snapshot edges of one supporting path, then the release check
   (``sources.release``). The strongest kind each path would license is recorded too, so
   the report says *why* it stops at a hypothesis.

Every parameter is explicit, the only randomness is seeded, and the result carries the
snapshot ids and a digest of this code, so the same inputs reproduce the same outputs.
"""

from __future__ import annotations

import bisect
import hashlib
import inspect
import json
import random
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..sources.herbs import GEGEN_QINLIAN, HERBS, FormulaVersion
from ..sources.parsers.opentargets import KNOWLEDGE_LEVEL as DISEASE_EVIDENCE_TYPES
from ..sources.release import CandidateClaim, check_release, path_licenses
from ..sources.snapshot import Snapshot
from ..tools.stats import benjamini_hochberg, hypergeometric_test

__all__ = ["Parameters", "NetworkPharmacologyResult", "run_network_pharmacology"]

_POTENCY = ("IC50", "Ki", "Kd", "EC50")
#: the snapshot whose active *and* inactive results define screening hits
SCREENING_SOURCE = "pubchem_bioassay"
_COMPOSITION_ORDER = ("C0", "part_unverified", "C1", "C2", "C3", "C4")


@dataclass(frozen=True)
class Parameters:
    """Every analytic choice, stated. Changing any of them is a new analysis."""

    activity_types: tuple[str, ...] = _POTENCY
    #: potency cut-off in nM (10 µM); a ">" value never passes
    activity_max_nm: float = 10_000.0
    #: weakest composition level accepted for a compound
    composition_min_level: str = "C1"
    #: "assayed": human proteins the compounds were measured against, potent or not;
    #: "reactome": every protein in the human Reactome annotation
    background: str = "assayed"
    #: what counts as a hit. "potency": a curated measurement at or below the cut-off
    #: (NPASS, CMAUP, BindingDB). "screening": a depositor's active call in PubChem
    #: BioAssay, tested against its inactive calls (see ``_screening_enrichment``)
    hits: str = "potency"
    screening_assay_types: tuple[str, ...] = ("Confirmatory", "Screening", "Other", "Summary")
    #: a protein enters the screening test only if this many compounds were tested on it
    screening_min_compounds: int = 20
    #: and a pathway only if this many of its proteins did
    screening_min_members: int = 3
    pathway_min_size: int = 5
    pathway_max_size: int = 500
    fdr: float = 0.05
    permutations: int = 1000
    #: empirical p-value a pathway must reach against the degree-matched null
    permutation_alpha: float = 0.05
    degree_bins: int = 10
    string_min_score: float = 0.7
    #: the Open Targets evidence type that defines the disease gene set, and its minimum
    #: score; used only when a disease-association snapshot is given
    disease_evidence: str = "genetic_association"
    disease_min_score: float = 0.5
    seed: int = 20260923

    def __post_init__(self) -> None:
        if self.composition_min_level not in _COMPOSITION_ORDER:
            raise ValueError(f"composition level {self.composition_min_level!r}")
        if not 0 < self.fdr < 1 or not 0 < self.permutation_alpha < 1:
            raise ValueError("fdr and permutation_alpha lie in (0, 1)")
        if self.hits not in ("potency", "screening"):
            raise ValueError(f"hits {self.hits!r} is not 'potency' or 'screening'")
        if self.screening_min_compounds < 1 or self.screening_min_members < 1:
            raise ValueError("screening minimums are at least 1")
        if self.background not in ("assayed", "reactome"):
            raise ValueError(f"background {self.background!r} is not 'assayed' or 'reactome'")
        if self.disease_evidence not in DISEASE_EVIDENCE_TYPES:
            raise ValueError(f"disease evidence {self.disease_evidence!r} is not one of "
                             f"{sorted(DISEASE_EVIDENCE_TYPES)}")
        if not 0 <= self.disease_min_score <= 1:
            raise ValueError("disease_min_score lies in [0, 1]")
        if self.permutations < 100:
            raise ValueError("fewer than 100 permutations cannot resolve p < 0.01")


@dataclass
class NetworkPharmacologyResult:
    formula: str
    parameters: Parameters
    snapshots: dict[str, str]
    compounds: list[dict[str, Any]]
    targets: list[dict[str, Any]]
    enrichment: list[dict[str, Any]]
    network: dict[str, Any]
    claims: list[dict[str, Any]]
    release: dict[str, Any]
    excluded: dict[str, int]
    code_digest: str
    limitations: list[str] = field(default_factory=list)
    #: the indication overlap; empty when no disease-association snapshot was given
    disease: dict[str, Any] = field(default_factory=dict)
    #: what the enrichment was tested against
    background: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["parameters"] = asdict(self.parameters)
        return out

    def digest(self) -> str:
        """Content hash of the result, for replay checks."""
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ helpers
def _level_at_least(level: str | None, minimum: str) -> bool:
    return level in _COMPOSITION_ORDER and (
        _COMPOSITION_ORDER.index(level) >= _COMPOSITION_ORDER.index(minimum))


def _passes_potency(measure: Mapping[str, Any] | None, params: Parameters) -> bool:
    if not measure or measure.get("type") not in params.activity_types:
        return False
    if (measure.get("unit") or "").lower() != "nm" or measure.get("relation") in (">", ">="):
        return False
    try:
        return float(measure["value"]) <= params.activity_max_nm
    except (TypeError, ValueError, KeyError):
        return False


def _brandes(adjacency: Mapping[str, set[str]]) -> dict[str, float]:
    """Unweighted betweenness centrality, normalised for an undirected graph."""
    nodes = sorted(adjacency)
    bc = dict.fromkeys(nodes, 0.0)
    for s in nodes:
        stack, pred = [], defaultdict(list)
        sigma = dict.fromkeys(nodes, 0.0)
        dist = dict.fromkeys(nodes, -1)
        sigma[s], dist[s] = 1.0, 0
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in sorted(adjacency[v]):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = dict.fromkeys(nodes, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                bc[w] += delta[w]
    n = len(nodes)
    scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 0.0
    return {v: round(bc[v] * scale, 6) for v in nodes}      # /2 for undirected, *2 normalise


def _components(adjacency: Mapping[str, set[str]]) -> list[list[str]]:
    seen: set[str] = set()
    out = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        comp, queue = [], deque([start])
        seen.add(start)
        while queue:
            v = queue.popleft()
            comp.append(v)
            for w in adjacency[v] - seen:
                seen.add(w)
                queue.append(w)
        out.append(sorted(comp))
    return sorted(out, key=lambda c: (-len(c), c))


def _pooled_rate(proteins: Sequence[str], tests: Mapping[str, int],
                 actives: Mapping[str, int]) -> float:
    n = sum(tests[p] for p in proteins)
    return sum(actives[p] for p in proteins) / n if n else 0.0


def _rate_test(members: set[str], eligible: Sequence[str], tests: Mapping[str, int],
               actives: Mapping[str, int], rng: random.Random,
               permutations: int) -> dict[str, Any]:
    """Pooled active rate of ``members`` against random protein sets of the same size.

    The unit is a compound-protein test, but tests on one protein are not independent (one
    assay panel, one lab), so the null draws whole proteins: random sets of as many
    eligible proteins, with all their tests, and the pooled rate recomputed.
    """
    chosen = sorted(members)
    rate = _pooled_rate(chosen, tests, actives)
    overall = _pooled_rate(eligible, tests, actives)
    at_least = sum(_pooled_rate(rng.sample(eligible, len(chosen)), tests, actives) >= rate
                   for _ in range(permutations))
    return {"proteins": len(chosen), "tests": sum(tests[p] for p in chosen),
            "active_tests": sum(actives[p] for p in chosen), "active_rate": round(rate, 6),
            "fold_enrichment": round(rate / overall, 4) if overall else None,
            "empirical_p": (1 + at_least) / (1 + permutations)}


def _screening_enrichment(members: Mapping[str, set[str]], annotated: set[str],
                          tested_pairs: Mapping[str, set[str]],
                          active_pairs: Mapping[str, set[str]],
                          target_paths: Mapping[str, list], names: Mapping[str, str],
                          params: Parameters, rng: random.Random,
                          excluded: dict[str, int]) -> tuple[set[str], list[str], list[dict]]:
    """Pathway test on screening results, inactive ones included.

    Counted per protein, screening hits saturate: across a thousand compounds almost every
    protein that was tested has *some* active result. The test is therefore on the rate:
    of the compound-protein tests on a pathway's proteins, what fraction were active,
    against random protein sets of the same size (``_rate_test``). Only proteins tested
    against enough compounds take part, and only pathways with enough such proteins are
    tested; BH runs over the empirical p-values of every pathway tested.
    """
    tests = {p: len(c) for p, c in tested_pairs.items()}
    actives = defaultdict(int, {p: len(c) for p, c in active_pairs.items()})
    in_annotation = {p for p in tested_pairs if p in annotated}
    eligible = sorted(p for p in in_annotation if tests[p] >= params.screening_min_compounds)
    excluded["targets outside the Reactome human background"] = len(
        [t for t in target_paths if t not in annotated])
    excluded["proteins tested against too few compounds"] = len(in_annotation) - len(eligible)
    background = set(eligible)
    rows = []
    for pathway in sorted(members):
        if not params.pathway_min_size <= len(members[pathway]) <= params.pathway_max_size:
            continue
        inside = members[pathway] & background
        if len(inside) < params.screening_min_members:
            continue
        test = _rate_test(inside, eligible, tests, actives, rng, params.permutations)
        rows.append({"pathway": pathway, "name": names.get(pathway, pathway),
                     "size": len(members[pathway]), "in_background": len(inside),
                     "overlap": sum(1 for p in inside if actives[p]),
                     "targets": sorted(p for p in inside if actives[p]),
                     "tests": test["tests"], "active_tests": test["active_tests"],
                     "active_rate": test["active_rate"],
                     "p_value": test["empirical_p"], "empirical_p": test["empirical_p"],
                     "fold_enrichment": test["fold_enrichment"]})
    if rows:
        for row, q in zip(rows, benjamini_hochberg([r["p_value"] for r in rows])["q_values"]):
            row["q_value"] = q
    rows.sort(key=lambda r: (r["p_value"], r["pathway"]))
    query = sorted(p for p in eligible if actives[p])
    return background, query, rows


def _disease_genes(snap: Snapshot, params: Parameters) -> tuple[str, str, dict[str, float]]:
    """(disease id, name, target -> score) for the stated evidence type and cut-off."""
    diseases = sorted((n["id"], n.get("name") or n["id"]) for n in snap.nodes
                      if n.get("category") == "disease")
    if len(diseases) != 1:
        raise ValueError(f"{snap.snapshot_id} must hold exactly one disease, has "
                         f"{len(diseases)}")
    disease, name = diseases[0]
    genes: dict[str, float] = {}
    for e in snap.edges:
        if (e.get("predicate") == "associated_with" and e.get("object") == disease
                and e.get("score_name") == params.disease_evidence
                and float(e.get("score") or 0) >= params.disease_min_score):
            genes[e["subject"]] = max(genes.get(e["subject"], 0.0), float(e["score"]))
    return disease, name, genes


# ------------------------------------------------------------------ the run
def run_network_pharmacology(snapshots: Iterable[Snapshot], *,
                             formula: FormulaVersion = GEGEN_QINLIAN,
                             params: Parameters = Parameters(),
                             contract: Any = None) -> NetworkPharmacologyResult:
    # A fixed order: names and path choices must not depend on the order snapshots were
    # passed in, or the same inputs would not reproduce the same result.
    snaps = sorted(snapshots, key=lambda s: s.snapshot_id)
    by_key = {s.key: s for s in snaps}
    for needed in ("tcm_herbs", "reactome"):
        if needed not in by_key:
            raise ValueError(f"the analysis needs a {needed} snapshot")
    excluded: dict[str, int] = defaultdict(int)
    rng = random.Random(params.seed)

    # 1. composition: formula -> herb -> species -> compound -------------------------------
    herbs_snap = by_key["tcm_herbs"]
    formula_edges = {e["object"]: e for e in herbs_snap.edges
                     if e["subject"] == formula.id and e["predicate"] == "contains"}
    base_edges = {(e["subject"], e["object"]): e for e in herbs_snap.edges
                  if e["predicate"] == "has_base_species"}
    species_of: dict[str, list[str]] = defaultdict(list)
    for (herb, species) in base_edges:
        if herb in formula_edges:
            species_of[species].append(herb)
    names: dict[str, str] = {}
    compound_paths: dict[str, list[tuple[Snapshot, dict]]] = defaultdict(list)
    compound_rows: dict[str, dict[str, Any]] = {}
    for snap in snaps:
        for n in snap.nodes:
            if n.get("category") in ("ingredient", "target", "pathway"):
                names.setdefault(n["id"], n.get("name") or n["id"])
        for e in snap.edges:
            if e.get("predicate") != "contains" or e.get("subject") not in species_of:
                continue
            level = e.get("composition_level")
            if level == "C1" and HERBS.get(species_of[e["subject"]][0]) and (
                    (e.get("raw") or {}).get("parts")):
                herb = HERBS[species_of[e["subject"]][0]]
                if herb.matches_part(e["raw"]["parts"]):
                    level = "C2"
            if not _level_at_least(level, params.composition_min_level):
                excluded["composition below the minimum level"] += 1
                continue
            compound_paths[e["object"]].append((snap, e))
            row = compound_rows.setdefault(e["object"], {
                "compound": e["object"], "herbs": set(), "level": level, "sources": set()})
            row["herbs"].update(species_of[e["subject"]])
            row["sources"].add(snap.key)
            if _COMPOSITION_ORDER.index(level) > _COMPOSITION_ORDER.index(row["level"]):
                row["level"] = level

    # 2. measured targets ------------------------------------------------------------------
    screening = params.hits == "screening"
    target_paths: dict[str, list[tuple[Snapshot, dict]]] = defaultdict(list)
    assayed: set[str] = set()          # measured with a potency type, at any value
    # screening hits: which compounds were tested against each protein, and were active
    tested_pairs: dict[str, set[str]] = defaultdict(set)
    active_pairs: dict[str, set[str]] = defaultdict(set)
    for snap in snaps:
        from_screen = snap.key == SCREENING_SOURCE
        for e in snap.edges:
            predicate = e.get("predicate")
            if predicate not in ("targets", "tested_against") or (
                    e.get("subject") not in compound_rows):
                continue
            if from_screen != screening:
                excluded["screening result (not used for potency hits)" if from_screen else
                         "curated measurement (not used for screening hits)"] += 1
                continue
            if screening:
                if e.get("assay_type") not in params.screening_assay_types:
                    excluded["screening result of an assay type not used"] += 1
                    continue
                tested_pairs[e["object"]].add(e["subject"])
                if predicate == "targets":
                    active_pairs[e["object"]].add(e["subject"])
                    target_paths[e["object"]].append((snap, e))
                continue
            if predicate != "targets":
                continue
            if e.get("knowledge_level") == "prediction":
                excluded["predicted target edge (not used by this skill)"] += 1
                continue
            if (e.get("measure") or {}).get("type") in params.activity_types:
                assayed.add(e["object"])
            if not _passes_potency(e.get("measure"), params):
                excluded["activity above the cut-off or not a potency measure"] += 1
                continue
            target_paths[e["object"]].append((snap, e))

    # 3. over-representation ---------------------------------------------------------------
    reactome = by_key["reactome"]
    members: dict[str, set[str]] = defaultdict(set)
    membership: dict[tuple[str, str], dict] = {}
    for e in reactome.edges:
        if e.get("predicate") == "participates_in":
            members[e["object"]].add(e["subject"])
            membership[(e["subject"], e["object"])] = e
    annotated = set().union(*members.values()) if members else set()
    if screening:
        background, query, rows = _screening_enrichment(
            members, annotated, tested_pairs, active_pairs, target_paths, names, params,
            rng, excluded)
        wanted, pool, tested = {}, {}, {r["pathway"]: r for r in rows}
    else:
        background = annotated & assayed if params.background == "assayed" else annotated
        query = sorted(t for t in target_paths if t in annotated)
        excluded["targets outside the Reactome human background"] = len(
            [t for t in target_paths if t not in annotated])
        # The size range is on the pathway as Reactome defines it; the test counts only its
        # members in the background, and a pathway with none there cannot be hit or tested.
        tested = {p: m & background for p, m in members.items()
                  if params.pathway_min_size <= len(m) <= params.pathway_max_size
                  and m & background}
        rows = []
        for pathway in sorted(tested):
            overlap = sorted(set(query) & tested[pathway])
            test = hypergeometric_test(len(overlap), len(query), len(tested[pathway]),
                                       len(background)) if query else {"p_value": 1.0,
                                                                       "fold_enrichment": None}
            rows.append({"pathway": pathway, "name": names.get(pathway, pathway),
                         "size": len(members[pathway]), "in_background": len(tested[pathway]),
                         "overlap": len(overlap),
                         "targets": overlap, "p_value": test["p_value"],
                         "fold_enrichment": test["fold_enrichment"]})
        if rows:
            for row, q in zip(rows, benjamini_hochberg([r["p_value"] for r in rows])["q_values"]):
                row["q_value"] = q

        # 4. degree-matched permutation null for the BH-significant pathways ---------------------
        degree = defaultdict(int)
        for p, m in members.items():
            for protein in m:
                degree[protein] += 1
        ordered = sorted(background, key=lambda x: (degree[x], x))
        cuts = [degree[ordered[int(len(ordered) * k / params.degree_bins)]]
                for k in range(1, params.degree_bins)] if ordered else []

        def bin_of(protein: str) -> int:
            return bisect.bisect_right(cuts, degree[protein])

        pool: dict[int, list[str]] = defaultdict(list)
        for protein in ordered:
            pool[bin_of(protein)].append(protein)
        wanted = defaultdict(int)
        for t in query:
            wanted[bin_of(t)] += 1
        for row in rows:
            row["empirical_p"] = None
            if row["q_value"] > params.fdr:
                continue
            pathway_members = tested[row["pathway"]]
            at_least = 0
            for _ in range(params.permutations):
                draw = [x for b, k in sorted(wanted.items()) for x in rng.sample(pool[b], k)]
                if len(pathway_members.intersection(draw)) >= row["overlap"]:
                    at_least += 1
            # exact, not rounded: rounding can put the value below its own 1/(n+1) floor
            row["empirical_p"] = (1 + at_least) / (1 + params.permutations)
        rows.sort(key=lambda r: (r["p_value"], r["pathway"]))
    significant = [r for r in rows if r["q_value"] <= params.fdr and r["empirical_p"] is not None
                   and r["empirical_p"] <= params.permutation_alpha]

    # 5. network topology (descriptive) ----------------------------------------------------
    adjacency: dict[str, set[str]] = {t: set() for t in query}
    if "string" in by_key:
        for e in by_key["string"].edges:
            a, b = e.get("subject"), e.get("object")
            if a in adjacency and b in adjacency and (e.get("score") or 0) >= params.string_min_score:
                adjacency[a].add(b)
                adjacency[b].add(a)
    betweenness = _brandes(adjacency) if adjacency else {}
    components = _components(adjacency) if adjacency else []
    network = {
        "nodes": len(adjacency), "edges": sum(len(v) for v in adjacency.values()) // 2,
        "largest_component": len(components[0]) if components else 0,
        "components": len(components),
        "string_min_score": params.string_min_score,
        "hubs": [{"target": t, "name": names.get(t, t), "degree": len(adjacency[t]),
                  "betweenness": betweenness.get(t, 0.0)}
                 for t in sorted(adjacency, key=lambda t: (-len(adjacency[t]), t))[:15]],
        "interpretation": "descriptive only; no significance is claimed for topology",
    }

    # 6. indication overlap (descriptive) ---------------------------------------------------
    disease: dict[str, Any] = {}
    disease_genes: dict[str, float] = {}
    if "opentargets" in by_key:
        disease_id, disease_name, disease_genes = _disease_genes(by_key["opentargets"], params)
        in_background = {g for g in disease_genes if g in background}
        hits = sorted(set(query) & in_background, key=lambda t: (-disease_genes[t], t))
        # its own stream, so adding the disease leaves the pathway permutations unchanged
        disease_rng = random.Random(params.seed + 1)
        rate: dict[str, Any] = {}
        if screening:
            tests = {p: len(tested_pairs[p]) for p in background}
            actives = {p: len(active_pairs.get(p, ())) for p in background}
            rate = _rate_test(in_background, sorted(background), tests, actives,
                              disease_rng, params.permutations) if in_background else {
                "empirical_p": 1.0, "fold_enrichment": None}
            test = {"p_value": rate["empirical_p"],
                    "fold_enrichment": rate["fold_enrichment"]}
            empirical = rate["empirical_p"]
        else:
            test = hypergeometric_test(len(hits), len(query), len(in_background),
                                       len(background)) if query else {"p_value": 1.0,
                                                                       "fold_enrichment": None}
            at_least = 0
            for _ in range(params.permutations):
                draw = [x for b, k in sorted(wanted.items())
                        for x in disease_rng.sample(pool[b], k)]
                if len(in_background.intersection(draw)) >= len(hits):
                    at_least += 1
            empirical = (1 + at_least) / (1 + params.permutations)
        disease = {
            "disease": disease_id, "name": disease_name,
            "snapshot": by_key["opentargets"].snapshot_id,
            "evidence": params.disease_evidence, "min_score": params.disease_min_score,
            "disease_genes": len(disease_genes), "in_background": len(in_background),
            "query": len(query), "overlap": len(hits),
            "targets": [{"target": t, "name": names.get(t, t), "score": disease_genes[t]}
                        for t in hits],
            "p_value": test["p_value"], "fold_enrichment": test["fold_enrichment"],
            "empirical_p": empirical,
            **({k: rate[k] for k in ("tests", "active_tests", "active_rate") if k in rate}),
            "interpretation": ("descriptive: the overlap between measured targets and the "
                               "disease gene set is reported, not claimed; it does not show "
                               "that the formula acts on the disease"),
        }

    # 7. candidate claims, each citing one supporting path ----------------------------------
    claims: list[CandidateClaim] = []
    claim_rows = []
    for row in significant:
        target = row["targets"][0]
        target_snap, target_edge = sorted(target_paths[target],
                                          key=lambda se: (se[0].key, se[1]["source_record_id"]))[0]
        compound = target_edge["subject"]
        comp_snap, comp_edge = sorted(compound_paths[compound],
                                      key=lambda se: (-_COMPOSITION_ORDER.index(
                                          se[1].get("composition_level", "C1")),
                                          se[0].key, se[1]["source_record_id"]))[0]
        herb = species_of[comp_edge["subject"]][0]
        support = [
            (herbs_snap.snapshot_id, formula_edges[herb]["source_record_id"]),
            (herbs_snap.snapshot_id, base_edges[(herb, comp_edge["subject"])]["source_record_id"]),
            (comp_snap.snapshot_id, comp_edge["source_record_id"]),
            (target_snap.snapshot_id, target_edge["source_record_id"]),
            (reactome.snapshot_id, membership[(target, row["pathway"])]["source_record_id"]),
        ]
        edges = [formula_edges[herb], base_edges[(herb, comp_edge["subject"])], comp_edge,
                 target_edge, membership[(target, row["pathway"])]]
        strongest = sorted(path_licenses(edges))
        evidence = ("PubChem screening results (active against inactive)" if screening
                    else "measured activities")
        statement = (f"Based on {formula.chinese} ({formula.source}) composition, {evidence} "
                     f"of its constituents and Reactome annotation, "
                     f"{names.get(row['pathway'], row['pathway'])} may be involved in its "
                     "action; this is a computational hypothesis awaiting experimental test.")
        claim = CandidateClaim("mechanism_hypothesis", formula.id, row["pathway"],
                               tuple(support), statement)
        claims.append(claim)
        claim_rows.append({"kind": claim.kind, "subject": claim.subject, "object": claim.object,
                           "pathway": row["name"], "q_value": row["q_value"],
                           "empirical_p": row["empirical_p"],
                           "support": [list(s) for s in support],
                           "path_licenses": strongest, "statement": statement,
                           "disease_associated_targets": sorted(
                               set(row["targets"]) & set(disease_genes)),
                           "why_not_stronger": (
                               "a constituent is shown in the source species, not in the "
                               "decoction (composition below C3)"
                               if comp_edge.get("composition_level") not in ("C3", "C4")
                               else "")})
    verdict = check_release(claims, snaps, contract=contract)

    compounds = sorted(({**r, "herbs": sorted(r["herbs"]), "sources": sorted(r["sources"]),
                         "name": names.get(r["compound"], r["compound"])}
                        for r in compound_rows.values()), key=lambda r: r["compound"])
    targets = [{"target": t, "name": names.get(t, t),
                "compounds": sorted({e["subject"] for _, e in target_paths[t]}),
                "measurements": len(target_paths[t]), "in_background": t in background,
                "disease_score": disease_genes.get(t)}
               for t in sorted(target_paths)]
    hit_rate = len(set(query) & background) / len(background) if background else 0.0
    screen_tests = sum(len(tested_pairs[p]) for p in background) if screening else 0
    screen_active = sum(len(active_pairs.get(p, ())) for p in background) if screening else 0
    limitations = [
        "Composition is species-level (C1/C2): no constituent is shown in the decoction "
        "itself (C3) or in plasma after dosing (C4), so every claim stops at a hypothesis.",
        *(_screening_limitations(params, len(background), screen_tests, screen_active)
          if screening else _potency_limitations(params, hit_rate)),
        *(_disease_limitations(disease, params) if disease else [
            "No disease gene set is joined: the claims concern pathways, not the indication. "
            "Relevance to the indication needs a disease-association snapshot (e.g. Open "
            "Targets) and is not asserted here."]),
        "Network topology is descriptive and depends on the STRING confidence cut-off.",
    ]
    return NetworkPharmacologyResult(
        formula=formula.id, parameters=params,
        snapshots={s.key: s.snapshot_id for s in snaps},
        compounds=compounds, targets=targets, enrichment=rows, network=network,
        claims=claim_rows, release=verdict.as_dict(), excluded=dict(excluded),
        code_digest="sha256:" + hashlib.sha256(
            inspect.getsource(inspect.getmodule(run_network_pharmacology)).encode()).hexdigest(),
        limitations=limitations, disease=disease,
        background={"kind": "screening" if screening else params.background,
                    "proteins": len(background),
                    "annotated": len(annotated),
                    "assayed_annotated": len(annotated & (set(tested_pairs) if screening
                                                          else assayed)),
                    "pathways_tested": len(tested),
                    "potent_in_background": len(set(query) & background),
                    "hit_rate": round(hit_rate, 4),
                    **({"tests": screen_tests, "active_tests": screen_active,
                        "active_rate": round(screen_active / screen_tests, 4)
                        if screen_tests else 0.0} if screening else {})})


def _potency_limitations(params: Parameters, hit_rate: float) -> list[str]:
    return [
        "Targets are in-vitro potency measurements at or below the stated cut-off; "
        "predicted targets are not used, and absence of a measurement is not absence of "
        "activity.",
        *([f"Enrichment is against the assayed proteins (every human protein the compounds "
           "were measured against, potent or not), so it asks where the potent results "
           "concentrate given what was tested. It says nothing about proteins never "
           f"assayed. {hit_rate:.0%} of the assayed proteins are potent hits: databases "
           "record active results far more often than inactive ones, so this background is "
           "itself biased toward hits and the test has little power (no pathway can exceed "
           f"a {1 / hit_rate if hit_rate else float('inf'):.2f}-fold enrichment). A pathway "
           "that is not enriched here is not shown to be irrelevant."]
          if params.background == "assayed" else [
           "Enrichment is against Reactome's whole human annotation, so a pathway can stand "
           "out because its proteins were tested, not because they were hit."]),
        "Pathways outside the size range are not tested, and the result depends on how "
        "thoroughly each protein is studied — hence the degree-matched null.",
        "Measured activities over-represent the target families natural products are "
        "routinely screened against (carbonic anhydrases, drug transporters, cytochromes "
        "P450). The degree-matched null controls for how well studied a protein is, not for "
        "which assay panels were run. "
        + ("The assayed background removes most of that bias; what remains is which of the "
           "tested proteins were potent, which still reflects how each panel was built."
           if params.background == "assayed" else
           "Enriched ADME and screening-panel pathways should be read as assay coverage "
           "before biology."),
        "The permutation p-value has a floor of 1/(permutations+1) and is a second filter "
        "after BH, not itself corrected for multiple testing.",
    ]


def _screening_limitations(params: Parameters, proteins: int, tests: int,
                           active: int) -> list[str]:
    rate = active / tests if tests else 0.0
    return [
        "Hits are the depositors' active calls in PubChem BioAssay, weighed against their "
        "inactive calls; potency is not re-thresholded. An active call can be an assay "
        "artefact (fluorescence, aggregation, cytotoxicity), and several natural products, "
        "berberine among them, interfere with optical read-outs.",
        f"The test compares active rates over {tests} compound-protein tests ({rate:.1%} "
        f"active) on {proteins} human proteins, each tested against at least "
        f"{params.screening_min_compounds} of the formula's compounds; a pathway needs "
        f"{params.screening_min_members} such proteins. Proteins never screened are outside "
        "the question.",
        "Some PubChem assays are literature measurements deposited from ChEMBL, of "
        "compounds chosen because they were active; on those proteins the active rate is "
        "inflated. The null draws whole proteins, which accounts for tests clustering by "
        "protein, not for how the compounds were chosen.",
        "The empirical p-value has a floor of 1/(permutations+1); BH is applied to it "
        "over every pathway tested.",
    ]


def _disease_limitations(disease: Mapping[str, Any], params: Parameters) -> list[str]:
    out = [
        f"The indication is joined as a descriptive overlap with Open Targets "
        f"{params.disease_evidence} associations (score >= {params.disease_min_score}) for "
        f"{disease['name']} ({disease['disease']}). The overlap is a statistic, not a claim: "
        "it neither shows that the formula acts on the disease nor licenses a stronger "
        "claim kind. Which disease is joined is the analyst's choice of indication; a "
        "formula's classical indication is stated in syndrome terms and does not map "
        "one-to-one onto a modern disease.",
        "Open Targets scores aggregate many evidence items; the gene set changes with the "
        "evidence type, the score cut-off and the Platform release (recorded in the snapshot "
        "id).",
    ]
    if params.disease_evidence in ("literature", "overall"):
        out.append("The disease gene set includes literature co-mention, which shares its "
                   "sources with the compound-target measurements; the overlap is partly "
                   "circular.")
    return out
