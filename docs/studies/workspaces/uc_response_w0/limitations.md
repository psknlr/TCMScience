# Limitations: Baseline transcriptome and endoscopic healing

Audit verdict: **proceed** (10 checks)

- [warn] batch_confounding: batch determines therapy in therapy_by_period: no batch contains both levels, so condition and batch effects cannot be separated; therapy is a covariate here, so it is recorded, not a stop

- GSE73661: original design rct_substudy_and_open_label_cohort, not randomised; sample unit biopsy
  - vedolizumab patients come from randomised trials, but the biopsy substudy compares responders and non-responders, which is not a randomised contrast
  - the infliximab cohort is open-label, before/after
  - no hybridisation batch or scan date is deposited; the biobank numbers form three ranges: infliximab and 6 controls 69-207, 6 controls 607-616, vedolizumab and placebo 771-1473
