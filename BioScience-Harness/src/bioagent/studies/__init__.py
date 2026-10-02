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
"""
