"""The shape of a source snapshot: a node table and an edge table, KGX-style.

Every third-party database is reduced to the same two tables so nothing downstream needs
a class per database. Column names follow KGX (the exchange format the Monarch knowledge
graph ships in); the edge provenance vocabulary is Biolink's ``knowledge_level`` and
``agent_type``. Those two say *how a statement was produced* (a curator reading a paper,
an algorithm predicting). The second evidence axis, ``study_design``, says *what kind of
study stands behind it*, and maps onto ``tcm.EvidenceTier``. Keeping the axes apart is
what stops a network-pharmacology prediction from being filed as a preclinical
experiment: a prediction is ``in_silico`` and licenses a mechanism hypothesis only.

Rows are plain mappings. ``validate_node`` / ``validate_edge`` return a list of problems
(empty when the row is sound) rather than raising, so a quality gate can count them.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from ..tcm.model import CLAIM_SUPPORT, PREDICATES, EvidenceTier

__all__ = [
    "NODE_CATEGORIES", "EDGE_PREDICATES", "KNOWLEDGE_LEVELS", "AGENT_TYPES",
    "STUDY_DESIGNS", "COMPOSITION_LEVELS", "NODE_REQUIRED", "EDGE_REQUIRED",
    "is_curie", "tier_for", "validate_node", "validate_edge", "licensed_claims",
    "check_claim",
]

NODE_CATEGORIES: frozenset[str] = frozenset({
    "herb", "processed_herb", "formula", "ingredient", "target", "disease", "syndrome",
    "symptom", "pathway", "organism", "publication",
})

#: ``tcm.model.PREDICATES`` plus the relations a source snapshot needs beyond the TCM seed.
EDGE_PREDICATES: frozenset[str] = PREDICATES | frozenset({
    "associated_with", "participates_in", "manifests_as", "interacts_with",
    "has_base_species",
})

#: Biolink KnowledgeLevelEnum.
KNOWLEDGE_LEVELS: frozenset[str] = frozenset({
    "knowledge_assertion", "logical_entailment", "prediction", "statistical_association",
    "text_co_occurrence", "observation", "not_provided",
})

#: Biolink AgentTypeEnum.
AGENT_TYPES: frozenset[str] = frozenset({
    "manual_agent", "automated_agent", "data_analysis_pipeline", "computational_model",
    "text_mining_agent", "image_processing_agent", "manual_validation_of_automated_agent",
    "not_provided",
})

#: Study design -> evidence tier. Matches PSH's ``psh.workflow.ir.DESIGNS`` name for name,
#: plus ``chemical_analysis``: isolating or detecting a compound in a material. That is how
#: composition ("this herb contains this compound") is established, and it is evidence for
#: no claim about an effect, so it maps to no tier and licenses nothing.
STUDY_DESIGNS: Mapping[str, EvidenceTier | None] = {
    "chemical_analysis": None,
    "in_silico": EvidenceTier.COMPUTATIONAL_PREDICTION,
    "classical_text": EvidenceTier.CLASSICAL_TEXT,
    "expert_consensus": EvidenceTier.EXPERT_EXPERIENCE,
    "in_vitro": EvidenceTier.PRECLINICAL,
    "animal": EvidenceTier.PRECLINICAL,
    "case_report": EvidenceTier.CASE_REPORT,
    "observational": EvidenceTier.OBSERVATIONAL,
    "randomized_trial": EvidenceTier.RANDOMIZED_TRIAL,
    "systematic_review": EvidenceTier.SYSTEMATIC_REVIEW,
}

#: How strongly "this herb contains this compound" is established. Natural-product
#: databases index *species*; a crude drug is a species, a part and a processing, and a
#: formula is also a decoction. Each level is a stronger statement than the one before.
COMPOSITION_LEVELS: Mapping[str, str] = {
    "C0": "predicted by a database or model",
    "C1": "reported in the source species (any part)",
    "C2": "detected in the medicinal part or crude-drug sample",
    "C3": "detected in the processed drug or the formula decoction",
    "C4": "detected in plasma or tissue after dosing",
    "part_unverified": "reported in the species; the medicinal part is not confirmed",
}

NODE_REQUIRED: tuple[str, ...] = ("id", "category", "name", "source")
EDGE_REQUIRED: tuple[str, ...] = (
    "subject", "predicate", "object", "primary_knowledge_source", "knowledge_level",
    "agent_type", "study_design", "license", "source_record_id",
)

#: prefix:local — prefix lowercase-ish (letters, digits, dot, dash, underscore), local
#: part non-empty and without whitespace.
_CURIE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*:\S+$")
_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_UNIPROT = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})(-\d+)?$")
_XREF_FORMATS = {"inchikey": _INCHIKEY, "uniprot": _UNIPROT}


def is_curie(value: Any) -> bool:
    return isinstance(value, str) and bool(_CURIE.match(value))


def tier_for(study_design: str) -> EvidenceTier | None:
    try:
        return STUDY_DESIGNS[study_design]
    except KeyError:
        raise ValueError(f"study design {study_design!r} is not one of "
                         f"{sorted(STUDY_DESIGNS)}") from None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_node(row: Mapping[str, Any]) -> list[str]:
    problems = [f"missing {c}" for c in NODE_REQUIRED if _blank(row.get(c))]
    if not _blank(row.get("id")) and not is_curie(row["id"]):
        problems.append(f"id {row['id']!r} is not a CURIE")
    category = row.get("category")
    if not _blank(category) and category not in NODE_CATEGORIES:
        problems.append(f"category {category!r} is not one of {sorted(NODE_CATEGORIES)}")
    xrefs = row.get("xrefs") or {}
    if not isinstance(xrefs, Mapping):
        problems.append("xrefs must be a mapping of prefix -> identifiers")
    else:
        for prefix, pattern in _XREF_FORMATS.items():
            for value in _as_list(xrefs.get(prefix)):
                if not pattern.match(str(value)):
                    problems.append(f"xref {prefix}:{value} is malformed")
    names = row.get("names") or {}
    if not isinstance(names, Mapping):
        problems.append("names must be a mapping of language -> names")
    return problems


def validate_edge(row: Mapping[str, Any]) -> list[str]:
    """Structural and evidential soundness of one edge.

    Beyond required fields and vocabularies, three rules tie the axes together:
    a prediction is ``in_silico`` and an ``in_silico`` edge is a prediction; the edge's
    ``evidence_tier`` (when given) is the one its design implies; and a design at or above
    PRECLINICAL cites a publication — a study nobody can look up is not evidence.
    """
    problems = [f"missing {c}" for c in EDGE_REQUIRED if _blank(row.get(c))]
    for end in ("subject", "object"):
        if not _blank(row.get(end)) and not is_curie(row[end]):
            problems.append(f"{end} {row[end]!r} is not a CURIE")
    predicate = row.get("predicate")
    if not _blank(predicate) and predicate not in EDGE_PREDICATES:
        problems.append(f"predicate {predicate!r} is not one of {sorted(EDGE_PREDICATES)}")
    level = row.get("knowledge_level")
    if not _blank(level) and level not in KNOWLEDGE_LEVELS:
        problems.append(f"knowledge_level {level!r} is not a Biolink knowledge level")
    agent = row.get("agent_type")
    if not _blank(agent) and agent not in AGENT_TYPES:
        problems.append(f"agent_type {agent!r} is not a Biolink agent type")
    design = row.get("study_design")
    if _blank(design):
        return problems
    if design not in STUDY_DESIGNS:
        problems.append(f"study_design {design!r} is not one of {sorted(STUDY_DESIGNS)}")
        return problems
    tier = STUDY_DESIGNS[design]
    if design == "chemical_analysis" and predicate != "contains":
        problems.append("chemical_analysis establishes composition ('contains') only")
    if (level == "prediction") != (design == "in_silico"):
        problems.append("a prediction must be in_silico and an in_silico edge must be a "
                        f"prediction (knowledge_level={level!r}, study_design={design!r})")
    stated = row.get("evidence_tier")
    if not _blank(stated) and str(stated) != (tier.name if tier is not None else ""):
        problems.append(f"evidence_tier {stated!r} contradicts study_design {design!r} "
                        f"({tier.name if tier is not None else 'no tier'})")
    if tier is not None and tier.needs_citation and not _as_list(row.get("publications")):
        problems.append(f"a {design} edge must cite a publication (PMID or DOI)")
    level_c = row.get("composition_level")
    if not _blank(level_c):
        if level_c not in COMPOSITION_LEVELS:
            problems.append(f"composition_level {level_c!r} is not one of "
                            f"{sorted(COMPOSITION_LEVELS)}")
        elif predicate != "contains":
            problems.append("composition_level only applies to 'contains' edges")
        elif (level_c == "C0") != (level == "prediction"):
            problems.append("composition level C0 is exactly the predicted composition")
    return problems


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [v for v in value if not _blank(v)]
    return [value]


def licensed_claims(edges: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """The claim kinds that at least one of ``edges`` can license on its own design.

    Edges with an unknown or missing design license nothing. This is the release-gate
    rule in its simplest form: if every supporting edge is a prediction, the strongest
    claim the set can carry is ``mechanism_hypothesis``.
    """
    tiers = {STUDY_DESIGNS[e["study_design"]] for e in edges
             if STUDY_DESIGNS.get(e.get("study_design")) is not None}
    return frozenset(kind for kind, admitted in CLAIM_SUPPORT.items() if tiers & admitted)


def check_claim(claim_kind: str, edges: Iterable[Mapping[str, Any]]) -> tuple[bool, str]:
    """Whether ``edges`` can support a claim of ``claim_kind``, and why (not)."""
    if claim_kind not in CLAIM_SUPPORT:
        raise ValueError(f"claim kind {claim_kind!r} is not one of {sorted(CLAIM_SUPPORT)}")
    edges = list(edges)
    if not edges:
        return False, "no supporting edges"
    licensed = licensed_claims(edges)
    if claim_kind in licensed:
        return True, f"supported by {claim_kind}-grade evidence"
    designs = sorted({str(e.get("study_design")) for e in edges})
    if all(e.get("knowledge_level") == "prediction" for e in edges):
        return False, (f"every supporting edge is a prediction ({', '.join(designs)}); the "
                       "strongest claim they carry is mechanism_hypothesis")
    return False, (f"a {claim_kind} claim needs "
                   f"{' or '.join(t.name for t in sorted(CLAIM_SUPPORT[claim_kind]))} "
                   f"evidence; the edges rest on {', '.join(designs)}")
