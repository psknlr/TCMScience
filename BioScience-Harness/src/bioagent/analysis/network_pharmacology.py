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
   for which at least one measurement can be classified at the threshold. Natural products are
   screened against the same panels again and again (carbonic anhydrases, transporters,
   cytochromes P450); against the whole human annotation, a pathway can look enriched
   because its proteins were tested, not because they were hit. Against the assayed
   background the question is which pathways the *potent* results concentrate in, given
   what was tested. The whole-annotation background remains available as a parameter.
4. **Annotation-bias control** — a pathway that passes BH must also beat a degree-matched
   permutation null: random target sets drawn so each target is matched on how many
   pathways it is annotated to, and on the number of classifiable compounds in the
   assayed background. The whole-annotation comparator deliberately omits assay coverage.
   In screening mode, whole proteins are randomized within identical campaign and compound
   exposure profiles; every profile in the term must include an outside comparator.
5. **Network topology** — the STRING subnetwork over the targets at a stated confidence,
   reported as description: degree, betweenness, components. No significance is claimed
   for it.
6. **Indication overlap** (when an Open Targets snapshot is given) — the measured targets
   against one disease's gene set, defined from one stated evidence type at a stated
   score (by default human genetic association, not literature co-mention, which would be
   circular for a literature-derived compound network). Genes are compared within their
   assayed population independently of Reactome pathway membership.
7. **Candidate claims** — one ``mechanism_hypothesis`` per pathway passing 3 and 4, each
   citing the exact snapshot edges of one supporting path, then the release check
   (``sources.release``). The strongest kind each path would license is recorded too, so
   the report says *why* it stops at a hypothesis.

Every parameter is explicit, the only randomness is seeded, and the result carries the
snapshot ids and a digest of this code, so the same inputs reproduce the same outputs.

What the result holds beyond the statistics, so every export can be traced and re-derived:
each compound and target row lists the snapshot records it rests on (``records``); the
network holds its edge list (``network["edge_list"]``: every STRING edge at or above the
cut-off between two targets, with its record) and the snapshot it was read from
(``ppi_source``, None when no STRING snapshot was given); the indication overlap holds the
whole gene set (``disease["genes"]``); and ``coverage`` counts how far the data reached:
herbs with a species and with a constituent, compounds with an InChIKey and with a
measured target, targets in the background, pathways tested, and how compounds were
identified (``coverage["identity"]``: by the source-supplied standard InChIKey only; keys
that share a connectivity block are reported, not merged).
"""

from __future__ import annotations

import bisect
import hashlib
import inspect
import json
import math
import random
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..sources.herbs import GEGEN_QINLIAN, HERBS, FormulaVersion
from ..sources.identity import canonical_many, group_sources
from ..sources.parsers.opentargets import KNOWLEDGE_LEVEL as DISEASE_EVIDENCE_TYPES
from ..sources.release import CandidateClaim, check_release, path_licenses
from ..sources.snapshot import Snapshot
from ..tools.stats import benjamini_hochberg, hypergeometric_test

__all__ = ["Parameters", "NetworkPharmacologyResult", "run_network_pharmacology",
           "FormulaNotRecorded", "recorded_composition"]

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
    #: "assayed": annotated proteins with a classifiable measurement at the cutoff;
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
    #: the Open Targets evidence datatype that defines the disease gene set — or a tuple of
    #: datatypes, whose union defines it — and the minimum score a target must reach in
    #: one of them; used only when a disease-association snapshot is given. One name stays
    #: a plain string, so a protocol written before tuples were accepted keeps its digest.
    disease_evidence: str | tuple[str, ...] = "genetic_association"
    disease_min_score: float = 0.5
    seed: int = 20260923

    @property
    def evidence_datatypes(self) -> tuple[str, ...]:
        """The datatypes ``disease_evidence`` names, as a tuple."""
        return ((self.disease_evidence,) if isinstance(self.disease_evidence, str)
                else tuple(self.disease_evidence))

    def __post_init__(self) -> None:
        if not math.isfinite(self.activity_max_nm) or self.activity_max_nm <= 0:
            raise ValueError("activity_max_nm must be finite and positive")
        if self.degree_bins < 1:
            raise ValueError("degree_bins must be positive")
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
        kinds = self.evidence_datatypes
        unknown = [k for k in kinds if k not in DISEASE_EVIDENCE_TYPES]
        if unknown or not kinds:
            raise ValueError(f"disease evidence {unknown or kinds!r} is not one of "
                             f"{sorted(DISEASE_EVIDENCE_TYPES)}")
        if len(set(kinds)) != len(kinds) or ("overall" in kinds and len(kinds) > 1):
            raise ValueError(f"disease evidence {kinds!r} repeats a datatype or combines "
                             "'overall', which already aggregates every datatype")
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
    #: how far the data reached, stage by stage (see the module docstring)
    coverage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["parameters"] = asdict(self.parameters)
        return out

    def digest(self) -> str:
        """Content hash of the result, for replay checks."""
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ helpers
class FormulaNotRecorded(ValueError):
    """The herb layer does not hold the composition the run was asked to analyse."""


def recorded_composition(herbs: Snapshot, formula: FormulaVersion) -> list[Mapping[str, Any]]:
    """The composition edges of ``formula`` in the herb-layer snapshot, once they are known
    to be the composition the caller means.

    The analysis reads the composition from the snapshot, never from ``formula``; the
    caller's formula only names it and is what the provenance reports. So the two must be
    one record: a formula that keeps its id but changes its herbs (葛根芩连汤 with 黄连
    removed, for a 拆方 study) would otherwise be reported as analysed while the old
    composition, 黄连 and its berberine included, was the one computed on. The snapshot's
    formula node records the fingerprint it was built from; a different one is refused.
    """
    edges = [e for e in herbs.edges
             if e.get("subject") == formula.id and e.get("predicate") == "contains"]
    if not edges:
        raise FormulaNotRecorded(
            f"the herb-layer snapshot {herbs.snapshot_id} records no composition for "
            f"{formula.id} ({formula.chinese}); build it with that formula first")
    node = next((n for n in herbs.nodes if n.get("id") == formula.id), None) or {}
    recorded = (node.get("raw") or {}).get("fingerprint")
    if recorded and recorded != formula.fingerprint:
        raise FormulaNotRecorded(
            f"{formula.id} ({formula.chinese}): the herb-layer snapshot {herbs.snapshot_id} "
            f"records composition {recorded}, but the formula given is {formula.fingerprint}; "
            "the record changed after the snapshot was built, so rebuild the herb layer")
    return edges


def _level_at_least(level: str | None, minimum: str) -> bool:
    return level in _COMPOSITION_ORDER and (
        _COMPOSITION_ORDER.index(level) >= _COMPOSITION_ORDER.index(minimum))


def _censored_from_below(measure: Mapping[str, Any]) -> bool:
    """Whether a value is only a lower bound (``>``, ``>=``, ``>>``, ``≥``).

    Such a value says the potency is *worse* than the number, however small the number
    is, so it can never show a compound reaching a cut-off. Matching the two spellings
    ``>`` and ``>=`` let NPASS's ``>>`` through: five real NPASS 2.0 rows read
    "IC50 >> 1000 nM" — much weaker than 1 µM — and counted as hits at 10 µM.
    """
    from ..sources.schema import is_lower_bound

    return is_lower_bound(measure)


def _why_not_potent(measure: Mapping[str, Any] | None, params: Parameters) -> str:
    """The reason a measurement is not a hit, for the run's account of what it left out.

    One reason used to cover all of them. It said "above the cut-off or not a potency
    measure" of NPASS's 22,882 potencies given in µg/mL — which *are* potency measures,
    possibly well under 10 µM, that simply cannot be compared with a molar cut-off
    without the compound's mass. A reader deciding whether the cut-off lost them real
    hits needs to know which it was.
    """
    if not measure or measure.get("type") not in params.activity_types:
        return "not a potency measure (IC50, Ki, Kd or EC50)"
    if _unit_factor(measure) is None:
        return "potency in a non-molar unit (e.g. µg/mL): not comparable with the cut-off"
    if _censored_from_below(measure):
        return "potency given only as a lower bound (>, >=, >>)"
    if _potency_call(measure, params) is None:
        return "indeterminate potency or bound at the cut-off"
    return "potency above the cut-off"


def _unit_factor(measure: Mapping[str, Any]) -> float | None:
    unit = str(measure.get("unit") or "").strip().replace("μ", "u").replace("µ", "u")
    return {"pm": 0.001, "nm": 1.0, "um": 1000.0, "mm": 1e6, "m": 1e9}.get(unit.lower())


def _potency_call(measure: Mapping[str, Any] | None, params: Parameters) -> bool | None:
    """Classify the full measurement interval relative to the protocol cutoff."""
    if not measure or measure.get("type") not in params.activity_types:
        return None
    factor = _unit_factor(measure)
    if factor is None:
        return None
    try:
        value = float(measure["value"]) * factor
    except (TypeError, ValueError, KeyError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    relation = str(measure.get("relation") or "=").strip()
    cutoff = params.activity_max_nm
    if relation in ("=", "=="):
        return value <= cutoff
    if relation in ("<", "<=", "≤", "<<"):
        return True if value <= cutoff else None
    if relation in (">", ">>"):
        return False if value >= cutoff else None
    if relation in (">=", "≥"):
        return False if value > cutoff else None
    return None


def _passes_potency(measure: Mapping[str, Any] | None, params: Parameters) -> bool:
    return _potency_call(measure, params) is True


def _matched_pools(universe: Iterable[str], query: Iterable[str], degree: Mapping[str, int],
                   opportunities: Mapping[str, set[str]] | None, degree_bins: int
                   ) -> tuple[dict[tuple[int, int], list[str]], Counter]:
    """Preserve the measured compound count exactly within annotation-degree strata.

    opportunities=None denotes the explicitly unconditioned annotation comparator.
    """
    ordered = sorted(universe, key=lambda p: (degree.get(p, 0), p))
    cuts = [degree.get(ordered[len(ordered) * k // degree_bins], 0)
            for k in range(1, degree_bins)] if ordered else []
    def stratum(p: str) -> tuple[int, int]:
        count = len(opportunities.get(p, ())) if opportunities is not None else 0
        return count, bisect.bisect_right(cuts, degree.get(p, 0))
    pool = defaultdict(list)
    for p in ordered:
        pool[stratum(p)].append(p)
    return pool, Counter(stratum(p) for p in query)


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


def _pathway_rng(params: Parameters, pathway: str) -> random.Random:
    """The permutation null's random stream for one pathway.

    Seeded by the run's seed and the pathway, so a pathway's null draws do not depend on
    which other pathways were tested before it. One stream shared in pathway order made
    a threshold or a background change move every later pathway's empirical p, and a
    sensitivity scan could not tell the change's effect from a reshuffled null.
    """
    return random.Random(f"{params.seed}:{pathway}")


def _pooled_rate(proteins: Sequence[str], tests: Mapping[str, int],
                 actives: Mapping[str, int]) -> float:
    n = sum(tests[p] for p in proteins)
    return sum(actives[p] for p in proteins) / n if n else 0.0


def _rate_test(members: set[str], eligible: Sequence[str], tests: Mapping[str, int],
               actives: Mapping[str, int], rng: random.Random, permutations: int, *,
               strata: Mapping[str, Any]) -> dict[str, Any]:
    """Randomize whole proteins within the same assay and compound exposure profile."""
    chosen = sorted(members)
    rate = _pooled_rate(chosen, tests, actives)
    result = {"proteins": len(chosen), "tests": sum(tests[p] for p in chosen),
              "active_tests": sum(actives[p] for p in chosen), "active_rate": rate,
              "fold_enrichment": None, "empirical_p": None}
    wanted = Counter(strata.get(p) for p in chosen)
    pools = defaultdict(list)
    for p in sorted(eligible):
        key = strata.get(p)
        if key in wanted:
            pools[key].append(p)
    if not chosen or None in wanted or any(len(pools[k]) <= n for k, n in wanted.items()):
        result["reason"] = "no outside comparator for every assay and compound exposure profile"
        return result
    groups = [(pools[k], n) for k, n in wanted.items()]
    expected = sum(sum(actives[p] for p in group) * n / len(group)
                   for group, n in groups) / result["tests"]
    # A stratum has the same compounds and hence denominator for every member.
    at_least = sum(sum(actives[p] for group, n in groups for p in rng.sample(group, n))
                   >= result["active_tests"] for _ in range(permutations))
    return {**result, "fold_enrichment": rate / expected if expected else None,
            "empirical_p": (1 + at_least) / (1 + permutations),
            "null_exceedances": at_least, "null_permutations": permutations}


def _assays(edge: Mapping[str, Any]) -> tuple[str, ...]:
    aids = {str(x).lower() for x in edge.get("publications") or ()
            if str(x).lower().startswith("pubchem.bioassay:") and str(x).split(":")[-1].isdigit()}
    raw_aid = str(edge.get("source_record_id") or "").split("|", 1)[0]
    if raw_aid.isdigit():
        aids.add("pubchem.bioassay:" + raw_aid)
    return tuple(sorted(aids))


def _screen_calls(records, tested_pairs, active_pairs, target_paths):
    """One consensus call per unique pair; retain excluded discordance and campaign design."""
    strata, signatures, conflicts = {}, {}, []
    active_records = total_records = 0
    for protein, compounds in records.items():
        signature = []
        known_campaigns = True
        for compound, paths in compounds.items():
            positives = [(s, e) for s, e in paths if e["predicate"] == "targets"]
            active_records += len(positives)
            total_records += len(paths)
            if positives and len(positives) < len(paths):
                conflicts.append({"compound": compound, "target": protein,
                                  "active_records": len(positives),
                                  "inactive_records": len(paths) - len(positives),
                                  "records": _records(paths)})
                continue
            tested_pairs[protein].add(compound)
            if positives:
                active_pairs[protein].add(compound)
                target_paths[protein].extend(positives)
            campaigns = [_assays(e) for _, e in paths]
            known_campaigns &= all(bool(aids) for aids in campaigns)
            signature.append((compound, tuple(sorted({aid for aids in campaigns for aid in aids}))))
        if signature and known_campaigns:
            sig = tuple(sorted(signature))
            strata[protein] = signatures.setdefault(sig, len(signatures))
    return strata, {"measurement_records": total_records, "active_records": active_records,
                    "inactive_records": total_records - active_records,
                    "unique_pairs": sum(len(c) for c in records.values()),
                    "consensus_pairs": sum(len(c) for c in tested_pairs.values()),
                    "discordant_pairs": len(conflicts), "discordant_records": conflicts,
                    "unit": "one compound-protein pair with concordant active or inactive calls"}


def _screening_enrichment(members: Mapping[str, set[str]], annotated: set[str],
                          tested_pairs: Mapping[str, set[str]],
                          active_pairs: Mapping[str, set[str]],
                          target_paths: Mapping[str, list], names: Mapping[str, str],
                          params: Parameters,
                          excluded: dict[str, int], strata: Mapping[str, Any],
                          untestable: list[dict]) -> tuple[set[str], list[str], list[dict]]:
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
        test = _rate_test(inside, eligible, tests, actives, _pathway_rng(params, pathway),
                          params.permutations, strata=strata)
        if test["empirical_p"] is None:
            untestable.append({"pathway": pathway, **test})
            continue
        rows.append({"pathway": pathway, "name": names.get(pathway, pathway),
                     "size": len(members[pathway]), "in_background": len(inside),
                     "overlap": sum(1 for p in inside if actives[p]),
                     "targets": sorted(p for p in inside if actives[p]),
                     "tests": test["tests"], "active_tests": test["active_tests"],
                     "active_rate": test["active_rate"],
                     "null_exceedances": test["null_exceedances"],
                     "null_permutations": test["null_permutations"],
                     "p_value": test["empirical_p"], "empirical_p": test["empirical_p"],
                     "fold_enrichment": test["fold_enrichment"]})
    if rows:
        for row, q in zip(rows, benjamini_hochberg([r["p_value"] for r in rows])["q_values"]):
            row["q_value"] = q
    rows.sort(key=lambda r: (r["p_value"], r["pathway"]))
    query = sorted(p for p in eligible if actives[p])
    return background, query, rows


#: Open Targets datatypes that are text mining (Europe PMC co-mention), from the parser's
#: own labels; "literature only" below means passing the cut-off through these alone.
TEXT_MINED = frozenset(k for k, level in DISEASE_EVIDENCE_TYPES.items()
                       if level == "text_co_occurrence")


def _association_gene(edge: Mapping[str, Any]) -> str:
    # In historical snapshots the subject was a single selected protein; the record id
    # still names the original Open Targets gene even for a shared protein accession.
    original = str(edge.get("source_record_id", "")).split("|", 1)[0]
    return f"ensembl:{original}" if original.startswith("ENSG") else edge["subject"]


def _protein_genes(snaps: Sequence[Snapshot]) -> dict[str, set[str]]:
    genes: dict[str, set[str]] = defaultdict(set)
    for snap in snaps:
        for node in snap.nodes:
            refs = node.get("xrefs") or {}
            gene_ids = {f"ensembl:{g}" for g in refs.get("ensembl", ()) if g.startswith("ENSG")}
            for acc in refs.get("uniprot", ()):
                genes[f"uniprot:{acc}"].update(gene_ids)
            if node["id"].startswith("uniprot:"):
                genes[node["id"]].update(gene_ids)
        if snap.key == "opentargets":
            for edge in snap.edges:
                if edge["subject"].startswith("uniprot:"):
                    genes[edge["subject"]].add(_association_gene(edge))
    return genes


def _disease_genes(snap: Snapshot, params: Parameters
                   ) -> tuple[str, str, dict[str, float], dict[str, dict[str, float]]]:
    """(disease id, name, target -> score, datatype -> target -> score).

    The gene set is the targets reaching the cut-off in at least one stated datatype, each
    scored by its best stated datatype. The per-datatype scores at the cut-off come back
    too, so what the choice of datatypes left out can be reported beside what it kept.
    """
    diseases = sorted((n["id"], n.get("name") or n["id"]) for n in snap.nodes
                      if n.get("category") == "disease")
    if len(diseases) != 1:
        raise ValueError(f"{snap.snapshot_id} must hold exactly one disease, has "
                         f"{len(diseases)}")
    disease, name = diseases[0]
    passing: dict[str, dict[str, float]] = defaultdict(dict)
    for e in snap.edges:
        if (e.get("predicate") == "associated_with" and e.get("object") == disease
                and float(e.get("score") or 0) >= params.disease_min_score):
            kind, target = str(e.get("score_name")), _association_gene(e)
            passing[kind][target] = max(passing[kind].get(target, 0.0), float(e["score"]))
    genes: dict[str, float] = {}
    for kind in params.evidence_datatypes:
        for target, score in passing.get(kind, {}).items():
            genes[target] = max(genes.get(target, 0.0), score)
    return disease, name, genes, dict(passing)


def _by_datatype(passing: Mapping[str, Mapping[str, float]], genes: Mapping[str, float],
                 snap: Snapshot, query: Iterable[str], background: set[str],
                 params: Parameters) -> dict[str, Any]:
    """What each datatype would contribute at the cut-off, and the literature-only targets.

    A text-mining association and a genetic one carry the same predicate and score scale;
    reported side by side, the reader can see how much of a gene set rests on co-mention.
    """
    used = set(params.evidence_datatypes)
    query = set(query)
    present = {str(e.get("score_name")) for e in snap.edges
               if e.get("predicate") == "associated_with"}
    datatypes = {kind: {"disease_genes": len(targets),
                        "overlap": len(query & set(targets) & background),
                        "used": kind in used}
                 for kind, targets in sorted(passing.items())}
    others = set().union(*(set(t) for k, t in passing.items()
                           if k not in TEXT_MINED and k != "overall"))
    text_only = set().union(*(set(passing.get(k, {})) for k in TEXT_MINED)) - others
    return {
        "datatypes": datatypes,
        "literature_only": {
            "disease_genes": len(text_only),
            "overlap": len(query & text_only & background),
            "in_gene_set": len(text_only & set(genes)),
            "definition": (f"targets reaching {params.disease_min_score} through "
                           f"{' or '.join(sorted(TEXT_MINED))} and through no other "
                           "datatype")},
        "declared_absent": sorted(k for k in params.evidence_datatypes if k not in present),
    }


def _publication_counts(paths: Sequence[tuple[Snapshot, Mapping[str, Any]]]) -> dict[str, int]:
    """Count bibliographic evidence separately from experiments and uncited observations."""
    papers = [[sid for sid in canonical_many(e.get("publications") or ())
               if sid.scheme in ("pmid", "pmcid", "doi")] for _, e in paths]
    alias_sets = []
    for ids in papers:
        if len({sid.scheme for sid in ids}) == len(ids):
            alias_sets.append(ids)
        else:
            # Two different PMIDs on one measurement cite two papers. Cross-scheme
            # aliases on such a row cannot be assigned without an external crosswalk.
            alias_sets.extend([sid] for sid in ids)
    grouped = group_sources(alias_sets)
    return {"publications": len(grouped.groups), "uncited_measurements": sum(not ids for ids in papers),
            "assays": len({aid for snap, e in paths if getattr(snap, "key", "") == SCREENING_SOURCE
                           for aid in _assays(e)})}


def _sources(paths: Sequence[tuple[Snapshot, Mapping[str, Any]]]) -> int:
    return _publication_counts(paths)["publications"]


def _records(paths: Iterable[tuple[Snapshot, Mapping[str, Any]]]) -> list[list[str]]:
    """[snapshot id, source record id] of each edge, sorted and once each."""
    return [list(p) for p in sorted({(s.snapshot_id, str(e.get("source_record_id")))
                                     for s, e in paths})]


def _gene_rows(snap: Snapshot, disease: str, genes: Mapping[str, float],
               passing: Mapping[str, Mapping[str, float]], names: Mapping[str, str],
               background: set[str], measured: set[str],
               params: Parameters) -> list[dict[str, Any]]:
    """The whole disease gene set, one row per gene: its best score in a stated datatype,
    the stated datatypes in which it reaches the cut-off, whether it is a measured target
    and in the background, and the association records it rests on."""
    used = params.evidence_datatypes
    records: dict[str, set[str]] = {}
    for e in snap.edges:
        gene = _association_gene(e)
        if (e.get("predicate") == "associated_with" and e.get("object") == disease
                and gene in genes and str(e.get("score_name")) in used
                and float(e.get("score") or 0) >= params.disease_min_score):
            records.setdefault(gene, set()).add(str(e.get("source_record_id")))
    return [{"target": g, "name": names.get(g, g), "score": genes[g],
             "datatypes": [k for k in used if g in (passing.get(k) or {})],
             "is_measured_target": g in measured, "in_background": g in background,
             "records": [[snap.snapshot_id, r] for r in sorted(records.get(g, ()))]}
            for g in sorted(genes, key=lambda g: (-genes[g], g))]


def _identity(compounds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How the compounds were identified and deduplicated.

    Compounds are keyed by the standard InChIKey the source supplies
    (``sources.parsers.common``), or by the source's own id when it gives none, and two
    rows are one compound only when their full keys are identical. The snapshots carry no
    structure (no SMILES or InChI), so nothing was re-standardised: keys that share the
    first block (the connectivity) but differ in stereochemistry, isotopes or charge are
    possibly one substance, and are reported here, not merged.
    """
    keyed = [c["compound"] for c in compounds if str(c["compound"]).startswith("inchikey:")]
    blocks: dict[str, list[str]] = defaultdict(list)
    for cid in keyed:
        blocks[cid.split(":", 1)[1].split("-", 1)[0]].append(cid)
    groups = [{"connectivity": block, "compounds": sorted(ids)}
              for block, ids in sorted(blocks.items()) if len(ids) > 1]
    return {
        "rule": "identical source-supplied standard InChIKey (full key); no "
                "re-standardisation: the snapshots carry no structure",
        "with_inchikey": len(keyed), "source_local": len(compounds) - len(keyed),
        "merged_across_sources": sum(1 for c in compounds if len(c.get("sources") or ()) > 1),
        "connectivity_groups": len(groups),
        "compounds_in_connectivity_groups": sum(len(g["compounds"]) for g in groups),
        "groups": groups,
    }


def _identity_limitation(identity: Mapping[str, Any]) -> str:
    groups = identity.get("connectivity_groups") or 0
    return ("Deduplication is by the source-supplied standard InChIKey; structures were not "
            "re-standardised because the snapshots carry no structure (SMILES or InChI). "
            + (f"{groups} group(s) of distinct keys share a connectivity block (possible "
               "stereo, isotope or charge variants) and are kept separate."
               if groups else "No two distinct keys share a connectivity block.")
            + (f" {identity['source_local']} compound(s) have no InChIKey and are keyed by "
               "the source's own id, so they cannot be matched across sources."
               if identity.get("source_local") else ""))


def _coverage_limitations(coverage: Mapping[str, Any], screening: bool) -> list[str]:
    """What could not be tested because the data did not reach it: an empty result is
    reported as one, never as evidence that the constituents are inactive."""
    herbs, compounds = coverage["herbs"], coverage["compounds"]
    targets = coverage["targets"]
    out = []
    if herbs["with_species"] < herbs["total"]:
        out.append(f"{herbs['total'] - herbs['with_species']} of the {herbs['total']} "
                   "components have no verified source species and contribute no "
                   "constituents.")
    if herbs["with_compound"] < herbs["with_species"]:
        out.append(f"{herbs['with_species'] - herbs['with_compound']} of the "
                   f"{herbs['with_species']} components with a species have no constituent "
                   "recorded in these snapshots; absence from a snapshot is not absence from "
                   "the plant.")
    what = "concordant active screening result" if screening else "measured target"
    if not compounds["total"]:
        out.append("No constituent of the formula is recorded in these snapshots, so no "
                   f"{what} could be looked for and no enrichment could be tested; {EMPTY_RESULT}.")
    elif not targets["measured"]:
        out.append(f"No {what} was found for the {compounds['total']} constituents in these "
                   f"snapshots, so no enrichment could be tested; {EMPTY_RESULT}.")
    elif not targets["annotated"]:
        out.append(f"None of the {targets['measured']} {what}s is in the Reactome human "
                   f"annotation, so no enrichment could be tested; {EMPTY_RESULT}.")
    return out


#: The words for a result that holds nothing because nothing could be tested.
EMPTY_RESULT = "an empty result, not evidence of inactivity"


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

    # 1. composition: formula -> herb -> species -> compound -------------------------------
    herbs_snap = by_key["tcm_herbs"]
    formula_edges = {e["object"]: e for e in recorded_composition(herbs_snap, formula)}
    base_edges = {(e["subject"], e["object"]): e for e in herbs_snap.edges
                  if e["predicate"] == "has_base_species"}
    from ..sources.materia import all_drugs
    drugs = {**all_drugs(), **HERBS}
    species_of: dict[str, list[str]] = defaultdict(list)
    for (herb, species) in base_edges:
        if herb in formula_edges:
            species_of[species].append(herb)
    names: dict[str, str] = {}
    compound_paths: dict[str, list[tuple[Snapshot, dict, str, str]]] = defaultdict(list)
    compound_rows: dict[str, dict[str, Any]] = {}
    for snap in snaps:
        for n in snap.nodes:
            if n.get("category") in ("ingredient", "target", "pathway"):
                names.setdefault(n["id"], n.get("name") or n["id"])
        for e in snap.edges:
            if e.get("predicate") != "contains" or e.get("subject") not in species_of:
                continue
            parts = (e.get("raw") or {}).get("parts") or ()
            if isinstance(parts, str):
                parts = (parts,)
            herbs = species_of[e["subject"]]
            accepted = []
            for herb in sorted(herbs):
                drug = drugs.get(herb)
                level = e.get("composition_level")
                matches_part = drug is not None and drug.matches_part(parts)
                if level == "C1" and matches_part:
                    level = "C2"
                elif (level in {"C2", "C3", "C4"} and len(herbs) > 1
                      and not matches_part):
                    # A species-level edge does not identify which crude drug was sampled.
                    level = "C1"
                if _level_at_least(level, params.composition_min_level):
                    accepted.append((herb, level))
            if not accepted:
                excluded["composition below the minimum level"] += 1
                continue
            compound = e["object"]
            for herb, level in accepted:
                compound_paths[compound].append((snap, e, herb, level))
                row = compound_rows.setdefault(compound, {
                    "compound": compound, "herbs": set(), "herb_levels": {},
                    "level": level, "sources": set()})
                row["herbs"].add(herb)
                row["sources"].add(snap.key)
                previous = row["herb_levels"].get(herb)
                if previous is None or _level_at_least(level, previous):
                    row["herb_levels"][herb] = level
                if _level_at_least(level, row["level"]):
                    row["level"] = level

    # 2. measured targets ------------------------------------------------------------------
    screening = params.hits == "screening"
    target_paths: dict[str, list[tuple[Snapshot, dict]]] = defaultdict(list)
    measured_proteins: set[str] = set()
    opportunities: dict[str, set[str]] = defaultdict(set)
    # screening hits: which compounds were tested against each protein, and were active
    tested_pairs: dict[str, set[str]] = defaultdict(set)
    active_pairs: dict[str, set[str]] = defaultdict(set)
    screen_records = defaultdict(lambda: defaultdict(list))
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
                screen_records[e["object"]][e["subject"]].append((snap, e))
                continue
            if predicate != "targets":
                continue
            if e.get("knowledge_level") == "prediction":
                excluded["predicted target edge (not used by this skill)"] += 1
                continue
            if (e.get("measure") or {}).get("type") in params.activity_types:
                measured_proteins.add(e["object"])
            call = _potency_call(e.get("measure"), params)
            if call is not None:
                opportunities[e["object"]].add(e["subject"])
            if call is not True:
                excluded[_why_not_potent(e.get("measure"), params)] += 1
                continue
            target_paths[e["object"]].append((snap, e))
    assayed = set(opportunities)
    screen_strata, screen_coverage = _screen_calls(screen_records, tested_pairs, active_pairs,
                                                  target_paths)

    # 3. over-representation ---------------------------------------------------------------
    reactome = by_key["reactome"]
    members: dict[str, set[str]] = defaultdict(set)
    membership: dict[tuple[str, str], dict] = {}
    for e in reactome.edges:
        if e.get("predicate") == "participates_in":
            members[e["object"]].add(e["subject"])
            membership[(e["subject"], e["object"])] = e
    annotated = set().union(*members.values()) if members else set()
    degree = Counter(protein for proteins in members.values() for protein in proteins)
    untestable = []
    if screening:
        background, query, rows = _screening_enrichment(
            members, annotated, tested_pairs, active_pairs, target_paths, names, params,
            excluded, screen_strata, untestable)
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
        pool, wanted = _matched_pools(background, query, degree,
                                      opportunities if params.background == "assayed" else None,
                                      params.degree_bins)
        for row in rows:
            row["empirical_p"] = None
            if row["q_value"] > params.fdr:
                continue
            pathway_members = tested[row["pathway"]]
            prng = _pathway_rng(params, row["pathway"])
            at_least = 0
            for _ in range(params.permutations):
                draw = [x for b, k in sorted(wanted.items()) for x in prng.sample(pool[b], k)]
                if len(pathway_members.intersection(draw)) >= row["overlap"]:
                    at_least += 1
            # exact, not rounded: rounding can put the value below its own 1/(n+1) floor
            row["empirical_p"] = (1 + at_least) / (1 + params.permutations)
        rows.sort(key=lambda r: (r["p_value"], r["pathway"]))
    significant = [r for r in rows if r["q_value"] <= params.fdr and r["empirical_p"] is not None
                   and r["empirical_p"] <= params.permutation_alpha]

    # 5. network topology (descriptive) ----------------------------------------------------
    string = by_key.get("string")
    represented = set()
    if string is not None:
        represented = {n["id"] for n in string.nodes if n.get("category") == "target"}
        represented.update(e[k] for e in string.edges if e.get("predicate") == "interacts_with"
                           for k in ("subject", "object"))
    adjacency: dict[str, set[str]] = {t: set() for t in sorted(represented.intersection(target_paths))}
    # each pair once (a <= b), at its highest score; on a tie, the lowest record id
    pair_edges: dict[tuple[str, str], tuple[float, str]] = {}
    if "string" in by_key:
        for e in by_key["string"].edges:
            a, b = e.get("subject"), e.get("object")
            if a in adjacency and b in adjacency and (e.get("score") or 0) >= params.string_min_score:
                adjacency[a].add(b)
                adjacency[b].add(a)
                pair = (a, b) if a <= b else (b, a)
                score, record = float(e.get("score") or 0), str(e.get("source_record_id"))
                kept = pair_edges.get(pair)
                if kept is None or (-score, record) < (-kept[0], kept[1]):
                    pair_edges[pair] = (score, record)
    betweenness = _brandes(adjacency) if adjacency else {}
    components = _components(adjacency) if adjacency else []
    network = {
        "nodes": len(adjacency), "edges": sum(len(v) for v in adjacency.values()) // 2,
        "measured_targets": len(target_paths),
        "measured_targets_outside_snapshot": sorted(set(target_paths) - represented)
        if string is not None else None,
        "largest_component": len(components[0]) if components else 0,
        "components": len(components),
        "string_min_score": params.string_min_score,
        "ppi_source": by_key["string"].snapshot_id if "string" in by_key else None,
        "hubs": [{"target": t, "name": names.get(t, t), "degree": len(adjacency[t]),
                  "betweenness": betweenness.get(t, 0.0)}
                 for t in sorted(adjacency, key=lambda t: (-len(adjacency[t]), t))[:15]],
        "edge_list": [{"a": a, "b": b, "score": score, "record": record}
                      for (a, b), (score, record) in sorted(pair_edges.items())],
        "interpretation": ("descriptive topology of measured targets represented in this "
                           "STRING snapshot; unrepresented targets have unknown degree"),
    }

    # 6. indication overlap (descriptive) ---------------------------------------------------
    disease: dict[str, Any] = {}
    disease_genes: dict[str, float] = {}
    if "opentargets" in by_key:
        disease_id, disease_name, disease_genes, passing = _disease_genes(
            by_key["opentargets"], params)
        mapping = _protein_genes(snaps)
        protein_tests = tested_pairs if screening else opportunities
        # Only unique mappings are countable gene units. An unlinked human protein
        # cannot silently become a gene with a negative disease association.
        products: dict[str, set[str]] = defaultdict(set)
        for protein in protein_tests:
            if len(mapping.get(protein, ())) == 1:
                products[next(iter(mapping[protein]))].add(protein)
        gene_tests = {g: set().union(*(protein_tests[p] for p in ps)) for g, ps in products.items()}
        minimum = params.screening_min_compounds if screening else 1
        population = {g for g, compounds in gene_tests.items() if len(compounds) >= minimum}
        measured = {g for g, ps in products.items() if ps.intersection(target_paths)}
        selected = population & measured
        in_background = population.intersection(disease_genes)
        hits = sorted(selected & in_background, key=lambda g: (-disease_genes[g], g))
        disease_rng = random.Random(params.seed + 1)
        rate: dict[str, Any] = {}
        if screening:
            tests = {g: len(gene_tests[g]) for g in population}
            actives = {g: len(set().union(*(active_pairs.get(p, set()) for p in products[g])))
                       for g in population}
            profiles = {}
            for gene in population:
                signatures = [screen_strata.get(p) for p in products[gene]]
                if all(sig is not None for sig in signatures):
                    # Preserve each product's compound/campaign profile as well as the
                    # number of measured products per gene when sampling comparators.
                    profiles[gene] = tuple(sorted(signatures))
            rate = _rate_test(in_background, sorted(population), tests, actives, disease_rng,
                              params.permutations, strata=profiles)
            test = {"p_value": rate["empirical_p"], "fold_enrichment": rate["fold_enrichment"]}
            empirical = rate["empirical_p"]
        else:
            test = (hypergeometric_test(len(hits), len(selected), len(in_background), len(population))
                    if selected and in_background else {"p_value": None, "fold_enrichment": None})
            empirical = None
            if test["p_value"] is not None:
                gene_pathways: dict[str, set[str]] = defaultdict(set)
                for pathway, proteins in members.items():
                    for protein in proteins:
                        if len(mapping.get(protein, ())) == 1:
                            gene_pathways[next(iter(mapping[protein]))].add(pathway)
                degree = {g: len(v) for g, v in gene_pathways.items()}
                pool, wanted = _matched_pools(population, selected, degree, gene_tests,
                                              params.degree_bins)
                at_least = 0
                for _ in range(params.permutations):
                    draw = [x for group, count in sorted(wanted.items())
                            for x in disease_rng.sample(pool[group], count)]
                    at_least += len(in_background.intersection(draw)) >= len(hits)
                empirical = (1 + at_least) / (1 + params.permutations)
        all_products: dict[str, list[str]] = defaultdict(list)
        for protein, gene_ids in sorted(mapping.items()):
            for gene in gene_ids:
                all_products[gene].append(protein)

        def display_gene(gene):
            mapped = all_products.get(gene, [])
            unique = len(mapped) == 1 and len(mapping[mapped[0]]) == 1
            return {"gene": gene, "target": mapped[0] if unique else gene,
                    "protein_products": mapped, "assayed_proteins": sorted(products.get(gene, ()))}
        all_genes = _gene_rows(by_key["opentargets"], disease_id, disease_genes, passing,
                               names, population, measured, params)
        disease = {
            "disease": disease_id, "name": disease_name, "snapshot": by_key["opentargets"].snapshot_id,
            "evidence": params.disease_evidence, "min_score": params.disease_min_score,
            "disease_genes": len(disease_genes), "in_background": len(in_background),
            "query": len(selected), "overlap": len(hits), "universe_genes": len(population),
            "eligible_proteins_without_gene_mapping": sum(not mapping.get(p) for p in protein_tests),
            "proteins_with_ambiguous_gene_mapping": sum(len(mapping.get(p, ())) > 1 for p in protein_tests),
            "targets": [{**display_gene(g), "name": names.get(g, g), "score": disease_genes[g]}
                        for g in hits],
            "p_value": test["p_value"], "fold_enrichment": test["fold_enrichment"],
            "empirical_p": empirical,
            **{k: rate[k] for k in ("tests", "active_tests", "active_rate", "reason") if k in rate},
            **_by_datatype(passing, disease_genes, by_key["opentargets"], selected, population, params),
            "genes": [{**row, **display_gene(row["target"])} for row in all_genes],
            "interpretation": ("descriptive gene-level overlap, not claimed as an effect on disease. "
                               "The universe is conditioned on assay opportunity and a unique human "
                               "gene mapping supplied by the snapshots. Multiple products count once. "
                               "Historical snapshots retain only the product mappings they recorded."),
        }
        disease_genes = {p: disease_genes[g] for g in disease_genes for p in products.get(g, ())}

    # 7. candidate claims, each citing one supporting path ----------------------------------
    claims: list[CandidateClaim] = []
    claim_rows = []
    for row in significant:
        target = row["targets"][0]
        target_snap, target_edge = sorted(target_paths[target],
                                          key=lambda se: (se[0].key, se[1]["source_record_id"]))[0]
        compound = target_edge["subject"]
        comp_snap, comp_edge, herb, herb_level = min(
            compound_paths[compound],
            key=lambda path: (-_COMPOSITION_ORDER.index(path[3]), path[0].key,
                              path[1]["source_record_id"], path[2]))
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
                               if herb_level not in ("C3", "C4")
                               else "")})
    verdict = check_release(claims, snaps, contract=contract)

    compounds = sorted(({**r, "herbs": sorted(r["herbs"]), "sources": sorted(r["sources"]),
                         "name": names.get(r["compound"], r["compound"]),
                         "records": _records((snap, edge) for snap, edge, _, _
                                             in compound_paths[r["compound"]])}
                        for r in compound_rows.values()), key=lambda r: r["compound"])
    targets = []
    for t in sorted(target_paths):
        via = sorted({e["subject"] for _, e in target_paths[t]})
        evidence_counts = _publication_counts(target_paths[t])
        targets.append({
            "target": t, "name": names.get(t, t), "compounds": via,
            "measurements": len(target_paths[t]), "sources": evidence_counts["publications"],
            **evidence_counts,
            "in_background": t in background, "disease_score": disease_genes.get(t),
            "herbs": sorted({h for c in via for h in compound_rows[c]["herbs"]}),
            "degree": len(adjacency[t]) if t in adjacency else None,
            "betweenness": betweenness.get(t) if t in adjacency else None,
            "records": _records(target_paths[t])})
    hit_rate = len(set(query) & background) / len(background) if background else 0.0
    screen_tests = sum(len(tested_pairs[p]) for p in background) if screening else 0
    screen_active = sum(len(active_pairs.get(p, ())) for p in background) if screening else 0
    identity = _identity(compounds)
    coverage = {
        "herbs": {"total": len(formula_edges),
                  "with_species": len({h for hs in species_of.values() for h in hs}),
                  "with_compound": len({h for r in compound_rows.values() for h in r["herbs"]})},
        "compounds": {"total": len(compounds),
                      "with_inchikey": identity["with_inchikey"],
                      "with_measured_target": len({e["subject"] for t in target_paths
                                                   for _, e in target_paths[t]})},
        "targets": {"measured": len(target_paths),
                    "annotated": sum(1 for t in target_paths if t in annotated),
                    "in_background": len(set(query) & background)},
        "pathways": {"tested": len(rows),
                     "in_size_range": sum(1 for m in members.values()
                                          if params.pathway_min_size <= len(m)
                                          <= params.pathway_max_size)},
        "identity": identity,
    }
    if screening:
        coverage["screening"] = {**screen_coverage, "untestable_pathways": untestable}
    else:
        coverage["potency"] = {"measured_proteins": len(measured_proteins),
                               "classifiable_proteins": len(assayed),
                               "indeterminate_only_proteins": len(measured_proteins - assayed),
                               "classifiable_compound_counts": {p: len(c) for p, c in
                                                               sorted(opportunities.items())}}
    limitations = [
        "Composition is species-level (C1/C2): no constituent is shown in the decoction "
        "itself (C3) or in plasma after dosing (C4), so every claim stops at a hypothesis.",
        *_coverage_limitations(coverage, screening),
        *(_screening_limitations(params, len(background), screen_tests, screen_active)
          if screening else _potency_limitations(params, hit_rate, len(background))),
        *(_disease_limitations(disease, params) if disease else [
            "No disease gene set is joined: the claims concern pathways, not the indication. "
            "Relevance to the indication needs a disease-association snapshot (e.g. Open "
            "Targets) and is not asserted here."]),
        ("Network topology is descriptive and depends on the STRING confidence cut-off."
         if network["ppi_source"] else
         "No PPI snapshot was read: the network has no edges because none were read, not "
         "because none exist."),
        _identity_limitation(identity),
    ]
    if screening:
        limitations.append(
            f"The screened constituents had {screen_coverage['measurement_records']} deposited "
            f"measurements on {screen_coverage['unique_pairs']} distinct compound-protein pairs. "
            f"{screen_coverage['discordant_pairs']} pairs had conflicting calls. "
            f"{len(untestable)} pathway(s) meeting the size and coverage thresholds lacked "
            "comparable assay and compound exposure and could not be tested.")
    return NetworkPharmacologyResult(
        formula=formula.id, parameters=params,
        snapshots={s.key: s.snapshot_id for s in snaps},
        compounds=compounds, targets=targets, enrichment=rows, network=network,
        claims=claim_rows, release=verdict.as_dict(), excluded=dict(excluded),
        code_digest="sha256:" + hashlib.sha256(
            inspect.getsource(inspect.getmodule(run_network_pharmacology)).encode()).hexdigest(),
        limitations=limitations, disease=disease, coverage=coverage,
        background={"kind": "screening" if screening else params.background,
                    "null_model": ("screening_assay_compound_matched" if screening else
                                   "compound_count_and_annotation_degree" if params.background
                                   == "assayed" else "whole_annotation_degree_only"),
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


def _potency_limitations(params: Parameters, hit_rate: float,
                         proteins: int | None = None) -> list[str]:
    """``proteins`` is the background's size. With no protein in it, or none of them a
    potent hit, there is no hit rate and no fold-enrichment bound to state: the sentence
    that gives them would print 0% and an infinite bound."""
    assayed = ("Enrichment is against the assayed proteins with a potency measurement "
               "classifiable at the stated threshold. Measurements in mass units or with "
               "bounds spanning the threshold cannot supply a hit or a negative.")
    if hit_rate:
        assayed_text = (
            f"{assayed} {hit_rate:.0%} of the assayed proteins are potent hits: databases "
            "record active results far more often than inactive ones, so this background is "
            "itself biased toward hits and the test has little power (no pathway can exceed "
            f"a {1 / hit_rate:.2f}-fold enrichment). A pathway that is not enriched here is "
            "not shown to be irrelevant.")
    elif proteins:
        assayed_text = (f"{assayed} None of the {proteins} assayed proteins in the annotation "
                        "is a potent hit, so no pathway could be enriched.")
    else:
        assayed_text = (f"{assayed} No assayed protein is in the annotation, so there was "
                        "nothing to test against.")
    return [
        "Targets are in-vitro potency measurements at or below the stated cut-off; "
        "predicted targets are not used, and absence of a measurement is not absence of "
        "activity.",
        *([assayed_text]
          if params.background == "assayed" else [
           "Enrichment is against Reactome's whole human annotation, so a pathway can stand "
           "out because its proteins were tested, not because they were hit."]),
        "Pathways outside the size range are not tested, and the result depends on how "
        "thoroughly each protein is studied — hence the degree-matched null.",
        "Measured activities over-represent the target families natural products are "
        "routinely screened against (carbonic anhydrases, drug transporters, cytochromes "
        "P450). The degree-matched null conditions on Reactome annotation degree. "
        + ("Within the assayed background the null also matches the exact number of "
           "distinct classifiable compounds for each protein. Compound identities, dose, "
           "panel selection and publication bias may still influence the hit rate."
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
        f"The test compares active rates over {tests} concordant compound-protein pairs ({rate:.1%} "
        f"active) on {proteins} human proteins, each tested against at least "
        f"{params.screening_min_compounds} of the formula's compounds; a pathway needs "
        f"{params.screening_min_members} such proteins. Proteins never screened are outside "
        "the question.",
        "Mixed active and inactive calls for a pair are reported separately and excluded. "
        "The null samples whole proteins with identical compound and assay-ID profiles. "
        "A pathway without an outside comparator in each stratum has no inferential result. "
        "Protocol details within an AID and selection of the deposited assays still limit interpretation.",
        "The empirical p-value has a floor of 1/(permutations+1); BH is applied to it "
        "over every pathway tested.",
    ]


def _disease_limitations(disease: Mapping[str, Any], params: Parameters) -> list[str]:
    kinds = params.evidence_datatypes
    out = [
        f"The indication is joined as a descriptive overlap with Open Targets "
        f"{' + '.join(kinds)} associations (score >= {params.disease_min_score}) for "
        f"{disease['name']} ({disease['disease']}). The overlap is a statistic, not a claim: "
        "it neither shows that the formula acts on the disease nor licenses a stronger "
        "claim kind. Which disease is joined is the analyst's choice of indication; a "
        "formula's classical indication is stated in syndrome terms and does not map "
        "one-to-one onto a modern disease.",
        "Open Targets scores aggregate many evidence items; the gene set changes with the "
        "evidence type, the score cut-off and the Platform release (recorded in the snapshot "
        "id).",
    ]
    if set(kinds) & (TEXT_MINED | {"overall"}):
        out.append("The disease gene set includes literature co-mention, which shares its "
                   "sources with the compound-target measurements; the overlap is partly "
                   "circular.")
    text_only = disease.get("literature_only") or {}
    if text_only.get("in_gene_set"):
        out.append(f"{text_only['in_gene_set']} of the disease genes rest on literature "
                   "co-mention alone (no other datatype reaches the cut-off for them).")
    elif text_only.get("disease_genes"):
        out.append(f"{text_only['disease_genes']} targets reach the cut-off through "
                   "literature co-mention alone; they are left out of the gene set and "
                   "reported separately.")
    for kind in disease.get("declared_absent") or ():
        out.append(f"The snapshot holds no {kind} associations for this disease, so that "
                   "datatype contributes nothing. A Platform release can rename a datatype: "
                   "API 26.9 reports drug evidence as 'clinical' and has no 'known_drug'.")
    return out
