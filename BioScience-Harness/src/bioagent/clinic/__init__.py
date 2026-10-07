"""TCM syndrome differentiation and prescription support, for a licensed practitioner.

``pack``           the clinical knowledge pack (criteria, formulas, herb limits, safety
                   rules) and its consistency checks
``intake``         the 四诊 record and its blank form
``findings``       matching recorded findings, tongues and pulses to the criteria
``redflags``       findings and vital signs that stop the consultation
``differentiate``  scoring the syndromes' criteria; what to ask next
``prescribe``      the draft prescription and the checks every prescription goes through
``followup``       change in the findings, adverse events, what to do next
``validate``       agreement with practitioners' labels on labelled cases
``session``        intake to draft to sign-off, recorded and verifiable

Nothing here prescribes. A draft is a starting point that a licensed practitioner
accepts, modifies or rejects, and the knowledge pack says whether a practitioner has
reviewed it.
"""
