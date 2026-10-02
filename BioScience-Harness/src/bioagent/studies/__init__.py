"""Falsifiable computational studies of a specific intervention.

Network pharmacology asks which targets and pathways a formula *may* touch. The analyses
here ask questions that data can answer no:

* ``robustness``: does a mechanism signal survive when its evidence is held out by origin
  (assay campaign, assay family, original study)?
* ``exposure``: can an activity measured in vitro happen at the exposure reached at the
  site of action?
* ``contrast``: does the whole formula do more than its main constituent, in a direct
  comparison, with equivalence judged against a margin set in advance?
* ``combination``: does a combination exceed a reference model chosen in advance, and is
  the excess selective?
* ``heterogeneity``: does a baseline feature modify the treatment effect (an interaction
  across both arms), as opposed to predicting who improved?

``design`` holds the records these share: the exact intervention, the preparation batch,
censored measurements, exposures, assay results, perturbation contrasts, and the study
protocol, which is locked by hash so a confirmatory analysis cannot quietly change its
endpoint or thresholds after seeing the data.

Study design for bioinformatics (questions before tools):

* ``contract``: ``StudySpec``, ``OriginalDesign`` (how the source data were made, kept
  apart from this analysis), ``CohortManifest``, ``ContrastSpec``, ``OmicsArtifact``,
  ``ValidationPlan`` and ``ClaimRecord``; disease and intervention evidence are separate
  chains, and a bridge claim needs both.
* ``gate``: whether the question can be answered from these data at all.
* ``audit``: rigor checks (independent units, batch confounding, leakage, duplicate
  studies, measured coverage, outcomes, test families, sufficiency, pairing, same-name
  interventions) with stop / downgrade / warn.
* ``singlecell``, ``spatial``, ``validation``, ``longitudinal``, ``power``: subject-level
  analyses (pseudobulk, composition against state, neighbourhoods per patient,
  leave-subjects-out prediction, next-visit prediction against persistence, power by
  simulation).
* ``faults``: a benchmark that injects design defects into studies with known truth.
* ``workspace``: the study directory; ``skills``: the ``bio.*`` study-skill contracts.
"""
