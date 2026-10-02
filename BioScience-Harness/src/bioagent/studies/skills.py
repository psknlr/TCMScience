"""The ``bio.*`` study skills as contracts: what each needs, checks, runs and may claim.

These are not yet pinned in ``registry/skills.lock.yaml``: a pin needs a named human
approver, so the contracts live here as code until one signs them. Each contract says
which question type it answers (``contract.QUESTION_TYPES``), which audit checks always
run, which analysis functions it calls, the highest claim it can produce, and what it
refuses. A skill whose analysis is not implemented says so (``implemented=False``): it runs
the gate and the audit and then stops.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["StudySkill", "STUDY_SKILLS", "skill"]

_ALWAYS = ("independent_units", "batch_confounding", "leakage", "duplicate_studies",
           "coverage", "test_family", "sufficiency", "pairing", "intervention_identity")


@dataclass(frozen=True)
class StudySkill:
    id: str
    question_type: str
    needs: tuple[str, ...]
    analyses: tuple[str, ...]
    max_claim: str
    refuses: tuple[str, ...]
    checks: tuple[str, ...] = _ALWAYS
    implemented: bool = True


STUDY_SKILLS = {s.id: s for s in (
    StudySkill("bio.cohort-audit", "response_prediction",
               ("cohort_manifest.tsv with subject, dataset, condition, batch, role",),
               ("gate.design_gate", "audit.audit_study"), "not_supported",
               ("running any analysis; it only decides whether one may run",)),
    StudySkill("bio.cell-composition-state", "composition_vs_state",
               ("cell-level counts with cell-type labels", "≥5 subjects per arm"),
               ("singlecell.pseudobulk", "singlecell.composition_test",
                "singlecell.state_test", "singlecell.decompose_bulk", "power.simulate_power"),
               "validated_association",
               ("cells as replicates", "a composition shift reported as a state change",
                "absolute abundance from proportions")),
    StudySkill("bio.spatial-context", "spatial_neighbourhood",
               ("coordinates and cell-type labels per section", "subject per section"),
               ("spatial.neighbourhood_enrichment", "spatial.compare_neighbourhoods",
                "spatial.detectable_genes"), "validated_association",
               ("pooling cells across patients into one permutation",
                "a genome background for a targeted panel")),
    StudySkill("bio.longitudinal-response", "response_prediction",
               ("≥2 visits per subject with collection days", "a real outcome",
                "prespecified windows in the validation plan"),
               ("longitudinal.visit_pairs", "longitudinal.next_visit_prediction",
                "validation.grouped_cv"), "validated_association",
               ("sample-level splits", "windows chosen after seeing results",
                "skill claimed without beating persistence")),
    StudySkill("bio.spatial-transcriptomics", "spatial_neighbourhood",
               ("processed Visium / Visium HD bin output or an .h5ad with obsm['spatial'] and "
                "raw counts", "section, sample and subject identifiers per input"),
               ("spatial.runner.run_isolated", "spatial.io.read_visium",
                "spatial.qc.apply_qc", "spatial.expression", "spatial.graph.build_graph",
                "spatial.statistics.morans_i", "spatial.statistics.neighbourhood_enrichment",
                "studies.spatial.compare_neighbourhoods"), "exploratory",
               ("an .h5ad without tissue coordinates", "UMAP or other embeddings as positions",
                "edges between unregistered sections", "spots or cells as replicates in a "
                "between-condition comparison", "expression clusters reported as spatial "
                "domains", "deconvolution or communication results without their method")),
    StudySkill("bio.genetic-triangulation", "genetic_target",
               ("tissue/cell-type QTL summary statistics", "GWAS summary statistics"),
               (), "association",
               ("a GWAS hit as a drug target without colocalisation",
                "colocalisation as evidence an intervention acts on the gene"),
               implemented=False),
    StudySkill("bio.isoform-context", "isoform",
               ("isoform- or junction-resolved RNA",), (), "association",
               ("isoform claims from 3'-tag single-cell libraries",), implemented=False),
    StudySkill("bio.microbial-function", "microbial_function",
               ("metagenome and metatranscriptome of the same collection",),
               ("longitudinal.match_layers",), "association",
               ("expression from DNA alone", "layers from different stools treated as "
                                             "one sample")),
    StudySkill("bio.perturbation-validation", "perturbation_transfer",
               ("perturbation signatures with cell context", "a disease contrast"),
               (), "association",
               ("transfer across cell types assumed rather than tested",
                "a model reported without no-change, mean and linear baselines"),
               implemented=False),
)}


def skill(skill_id: str) -> StudySkill:
    try:
        return STUDY_SKILLS[skill_id]
    except KeyError:
        raise KeyError(f"unknown study skill {skill_id!r}; one of "
                       f"{sorted(STUDY_SKILLS)}") from None
