"""`analyze-tcm-network-pharmacology` — build a herb–target network, marked by provenance.

This skill is the reason the kernel's design vocabulary had to grow. Its output
is *predicted* relationships — which compound might bind which target, which
pathway might be enriched — and before `PREDICTIVE_DESIGNS` existed there was no
way to say so in a compiled program. The nearest available design was
`in_vitro`, which would have recorded a simulation as a bench experiment.

So the skill's central obligation is the split the plan names:

> predicted relationships and experimental relationships must not be mixed

That is enforced here in three places rather than asserted once.

**Different collections.** `measured_targets` and `predicted_targets` are
separate keys in the output, never a single `targets` list. A consumer cannot
accidentally read a prediction as a measurement, because there is no key where
both appear.

**Different evidence designs.** Every predicted edge is an `EvidenceItem` with a
predictive design; every measured edge is one with an experimental design. The
distinction survives into the artifact rather than living only in the layout.

**The claim is a mechanism claim and says so.** The skill's manifest permits
`mechanism` and forbids `efficacy`/`association`/`safety_signal`, and the kernel
enforces the same thing independently: in its claim-support table, predictive
designs appear in exactly one row, MECHANISTIC. So a clinical claim from this
skill is refused twice — once by the manifest's own policy and once by the
kernel's type system. Neither refusal depends on the model behaving.

`cross_validate` is the honest path out of prediction-only territory: when a
measured record exists for a predicted edge, the two are reported side by side
with an explicit agreement status. That is what makes the prediction falsifiable
rather than merely disclaimed.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...contracts import (CandidateClaim, Directness, EvidenceItem, EvidenceQuality,
                          require_declared)
from ...tcm import knowledge as tcm_knowledge
from ...tcm.model import EvidenceTier, Formula
from .common import (SEED_SOURCE_ID, seed_evidence, SKILL_VERSIONS, _now, _quality_for, artifact,
                     json_file, seed_source_card)

__all__ = ["analyze_tcm_network_pharmacology"]

#: The design this skill's own inferences carry. Named once so every predicted
#: edge is labelled identically and a reviewer can grep for it.
PREDICTION_DESIGN = "network_prediction"


def analyze_tcm_network_pharmacology(formula_name: str, *, run_id: str = "") -> Any:
    """Build the network for a formula and separate prediction from measurement.

    Reads the corpus for the formula's composition, its recorded action
    relations, and any measured study evidence. Edges from `relations_of` are
    *recorded* relations — the corpus asserts them, so they are reported as
    recorded, not as measured: attributing an experimental design to a corpus
    assertion would be exactly the overstatement this skill exists to prevent.
    """
    kb = tcm_knowledge.default_knowledge()
    source = seed_source_card()

    resolution = kb.resolve(formula_name)
    formula = resolution.entity
    if not isinstance(formula, Formula):
        return _not_a_formula(formula_name, run_id=run_id,
                              candidates=[getattr(c, "id", str(c))
                                          for c in resolution.candidates])

    # ---- composition -----------------------------------------------------
    ingredients: list[dict[str, Any]] = []
    for ingredient in formula.ingredients:
        herb = kb.entity(ingredient.herb_id)
        ingredients.append({
            "herb_id": ingredient.herb_id,
            "chinese": getattr(herb, "chinese", ""),
            "role": ingredient.role,
            "dose": ingredient.dose,
            "processed": ingredient.processed,
        })

    # ---- recorded relations, split by what they actually are -------------
    # The corpus records three kinds of relation about a formula and its herbs, and
    # only one belongs in a target network. `contains` is composition. `treats` and
    # `indicated_for` record a traditional use: their object is a syndrome, not a
    # molecule. `targets` is a molecular target relation. An earlier version put
    # every non-composition relation under `predicted_targets`, so 人参 treats 气虚证
    # was reported as a predicted target; it also read `relation.subject`, which does
    # not exist, and crashed on any formula whose herbs carry relations (四君子汤).
    composition_edges: list[dict[str, Any]] = []
    recorded_indications: list[dict[str, Any]] = []
    recorded_target_edges: list[dict[str, Any]] = []
    for subject in [formula.id, *(i.herb_id for i in formula.ingredients)]:
        for relation in kb.relations_of(subject):
            edge = {"subject": relation.subject_id, "predicate": relation.predicate,
                    "object": relation.object_id, "tier": relation.tier.name.lower(),
                    "evidence": list(relation.evidence_ids)}
            if relation.predicate == "contains":
                composition_edges.append(edge)
            elif relation.predicate == "targets":
                recorded_target_edges.append(edge)
            elif relation.predicate in ("treats", "indicated_for"):
                recorded_indications.append(edge)

    # ---- measured vs predicted -------------------------------------------
    # A target edge is measured only when an assay or a binding method stands behind
    # it. The corpus stores relations, not assays, so every target edge it records is
    # a hypothesis, and the measured side is empty. Measured targets come from the
    # snapshot pipeline (`analysis.network_pharmacology`), not from this corpus. The
    # earlier version filled `measured_targets` from the studies behind safety records,
    # which name no target at all.
    measured_targets: list[dict[str, Any]] = []
    predicted_targets: list[dict[str, Any]] = []
    evidence: list[EvidenceItem] = []

    for edge in recorded_target_edges:
        predicted_targets.append({
            "herb": edge["subject"], "target": edge["object"],
            "predicate": edge["predicate"], "provenance": "corpus_relation",
            "evidence": edge["evidence"], "tier": edge["tier"],
            "measured": False,
            "note": "recorded in the corpus; no assay or binding method is stored",
        })

    for index, record in enumerate(predicted_targets):
        evidence.append(seed_evidence(
            id=f"predicted.{index}", design=PREDICTION_DESIGN,
            quote=(f"subject={record['herb']}; predicate={record['predicate']}; "
                   f"object={record['target']}; tier={record['tier']}"),
            citation=f"TCMScience TCM seed corpus, relation {record['herb']}->"
                     f"{record['target']}",
            identifier=f"relation.{record['herb']}.{record['target']}",
            identifier_type="pharmacopoeia", subject=record["herb"],
            # Direct for what the claim says, that the corpus records the relation as
            # a hypothesis. The predictive design is what keeps the edge from being
            # restated as a measurement: it licenses a mechanism hypothesis and
            # nothing stronger. Marking it extrapolated, as before, blocked the
            # hypothesis claim it exists to support.
            quality=EvidenceQuality(
                directness=Directness.DIRECT,
                rationale={"directness":
                           "direct evidence that the corpus records this relation; a "
                           "predictive design licenses a mechanism hypothesis only"},
                assessed_by="analyze-tcm-network-pharmacology",
                assessment_tool="provenance-separation"),
            source_card_id=SEED_SOURCE_ID, retrieved_by="analyze-tcm-network-pharmacology",
            retrieval_run=run_id))

    # ---- enrichment, marked as the prediction it is ----------------------
    pathway_enrichment = sorted({f"{e['predicate']}:{e['object']}"
                                 for e in recorded_target_edges})

    payload = {
        "formula": {"id": formula.id, "chinese": formula.chinese,
                    "source": formula.source, "actions": list(formula.actions)},
        "ingredients": ingredients,
        "composition_edges": composition_edges,
        "recorded_indications": recorded_indications,
        "predicted_targets": predicted_targets,
        "measured_targets": measured_targets,
        "pathway_enrichment": pathway_enrichment,
        "provenance_summary": {
            "edges_total": len(predicted_targets),
            "edges_measured": len(measured_targets),
            "edges_predicted": len(predicted_targets),
            "measured_fraction": (len(measured_targets) /
                                  max(1, len(measured_targets) + len(predicted_targets))),
        },
        "randomisation": {"performed": False,
                          "note": "network randomisation is not implemented in this "
                                  "build; enrichment ranks are not background-corrected"},
    }
    out, _ = json_file("network.json", payload,
                       description="herb–target network with predicted/measured split")

    limitations = [
        "EVERY target edge here is a corpus-recorded relation or a network "
        "inference; none is a binding measurement. No docking, no assay, no "
        "structure-based prediction was run",
        "pathway enrichment is a ranked list of recorded predicates, not a "
        "background-corrected enrichment analysis; no network randomisation was "
        "performed, so no p-value is reported",
        "the network is built from the seed corpus only; no external target "
        "database (BindingDB, ChEMBL, STITCH) was queried",
        "a predicted edge is a hypothesis about a mechanism. It is not evidence "
        "that the formula treats any condition in any patient",
    ]
    if not measured_targets:
        limitations.append(
            "no measured target data exists for this formula in the corpus, so the "
            "measured side of the split is empty; measured targets come from the "
            "snapshot pipeline (analysis.network_pharmacology), not this corpus")
    if not predicted_targets:
        limitations.append(
            "the corpus records no molecular target relation for this formula or its "
            "herbs, so no target network was built and no claim is made")
    if recorded_indications:
        limitations.append(
            "recorded indications (treats / indicated_for) are traditional-use records "
            "whose object is a syndrome; they are reported apart and are not targets")

    # A claim is made only if there is a network to make it about. The first
    # version emitted the claim unconditionally and produced an artifact with a
    # claim citing no evidence — caught by `validate_artifact` as ART101, which
    # is the validator doing exactly its job.
    claims: list[CandidateClaim] = []
    if predicted_targets:
        claims.append(CandidateClaim(
            id="network.mechanism.hypothesis",
            text=(f"{len(predicted_targets)} target relation(s) are inferred for "
                  f"{formula.chinese}, all of them computational or corpus-recorded "
                  "rather than measured"),
            claim_kind="mechanism_hypothesis", subject=formula.id, predicate="targets",
            object="; ".join(sorted({r["target"] for r in predicted_targets})),
            supports=tuple(f"predicted.{i}"
                           for i in range(min(3, len(predicted_targets)))),
            asserted_population="in silico", supported_population="in silico",
            asserted_outcome="predicted target relation",
            supported_outcome="predicted target relation",
            confidence=0.3,
            confidence_basis=("corpus-recorded relations and network inference only; "
                              "no binding measurement, no randomisation, no "
                              "enrichment correction"),
            declared_extrapolations={
                "outcome:predicted target relation":
                    "analysis stops at a hypothesis; no clinical outcome is claimed"},
            direction="unclear",
            falsified_by=("a binding or inhibition assay for any listed herb-target "
                          "pair that fails to reproduce the relation"),
            rationale="priority ranking for wet-lab follow-up, not a finding",
            produced_by="analyze-tcm-network-pharmacology"))

    for claim in claims:
        require_declared(claim)

    return artifact(
        id="analyze-tcm-network-pharmacology", run_id=run_id,
        skill_id="analyze-tcm-network-pharmacology",
        skill_version=SKILL_VERSIONS["analyze-tcm-network-pharmacology"],
        question=f"what target network is inferred for {formula.chinese}?",
        sources=(source,), evidence=evidence, claims=claims, outputs=(out,),
        limitations=limitations,
        assumptions=(
            "corpus-recorded relations are treated as hypotheses about mechanism",
            "absence of a measured edge means no measurement was found, not that "
            "binding was tested and excluded",
        ),
        created_at=_now(),
        provenance={"formula": formula.id, "predicted": len(predicted_targets),
                    "measured": len(measured_targets),
                    "recorded_indications": len(recorded_indications),
                    "prediction_only": not measured_targets})


def _not_a_formula(name: str, *, run_id: str, candidates: Sequence[str]) -> Any:
    """An artifact for a name that is not a formula, rather than an exception.

    A skill that raised here would make an unrecognised input look like a system
    fault. It is not: it is a legitimate answer, and the artifact reports it with
    the candidates the resolver found so the caller can retry with a real name.
    """
    source = seed_source_card()
    out, _ = json_file("network.json",
                       {"formula": None, "query": name, "candidates": list(candidates)},
                       description="no formula resolved; nothing was analysed")
    return artifact(
        id="analyze-tcm-network-pharmacology", run_id=run_id,
        skill_id="analyze-tcm-network-pharmacology",
        skill_version=SKILL_VERSIONS["analyze-tcm-network-pharmacology"],
        question=f"what target network is inferred for {name}?",
        sources=(source,), evidence=(), claims=(), outputs=(out,),
        limitations=(f"{name!r} did not resolve to a formula in the seed corpus, so no "
                     "network was built. This is an empty result, not a null finding",
                     "the corpus holds 6 formulas; a name outside them cannot be "
                     "analysed in this configuration"),
        created_at=_now(),
        provenance={"query": name, "candidates": list(candidates), "analysed": False})
