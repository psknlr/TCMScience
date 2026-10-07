"""Three cases that run a question end to end, through the pieces this round connected.

Each case starts from inputs, runs real implementations, turns their output into evidence
the contracts can read, and puts drafted claims through the claim contract. It reports
every step's status and implementation, and every claim's verdict with its codes:

* :mod:`.rnaseq_gsea`: counts → differential expression (built-in or PyDESeq2) → preranked
  GSEA (GSEApy) → claims about the pathway;
* :mod:`.literature_claims`: documents → PaperQA2 index and retrieval → located, typed
  evidence items → a reviewer's quality assessment → claims;
* :mod:`.compound_hypothesis`: a compound and its target → Open Targets evidence by
  datatype → docking with a validated setup → ADMET rules → complex prediction (refused
  when the engine is absent) → a hypothesis report.

They are tests of the chain, not studies. The inputs are fixtures (a simulated experiment,
the ablation's fixture abstracts, a recorded Open Targets answer and the 3PTB pocket), and
each report says which. What a case shows is what each step did and which claims the
evidence it produced can carry.
"""

from __future__ import annotations

from .report import CaseReport, CaseStep, ClaimCheck, check_drafts

__all__ = ["CaseReport", "CaseStep", "ClaimCheck", "check_drafts"]
