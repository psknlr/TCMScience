"""Research runs that go from a question to a released, checked result.

See :mod:`bioagent.research.loop` for the fixed pipeline (protocol, retrieval, analysis,
pre-registered rebuttals, release) and :mod:`bioagent.research.inquiry` for the inquiry
that decides which analysis to run next, and what a pathway signal means, by competing
explanations and sealed predictions. :mod:`bioagent.research.planted` builds the
synthetic worlds with a known answer that the inquiry is checked on.
"""

from .inquiry import (PathwayInquiryResult, pathway_inquiry, render_markdown,
                      run_pathway_inquiry)
from .loop import (FORMULAS, Protocol, QuestionRefused, ResearchQuestion, ResearchRefused,
                   ResearchRun, canonical_row, default_protocol, parse_question,
                   run_research, snapshot_content_store)

__all__ = ["FORMULAS", "Protocol", "QuestionRefused", "ResearchQuestion", "ResearchRefused",
           "ResearchRun", "canonical_row", "default_protocol", "parse_question",
           "run_research", "snapshot_content_store", "PathwayInquiryResult",
           "pathway_inquiry", "render_markdown", "run_pathway_inquiry"]
