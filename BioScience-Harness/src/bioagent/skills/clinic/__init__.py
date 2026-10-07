"""Candidate skills over the clinical decision support (``bioagent.clinic``). None is in the
stable lockfile; see ``bioagent.skills.omics`` for what that means."""

from __future__ import annotations

from .draft import draft_tcm_prescription

__all__ = ["draft_tcm_prescription", "CANDIDATE_VERSIONS"]

CANDIDATE_VERSIONS = {
    "draft-tcm-prescription": "0.1.0",
}
