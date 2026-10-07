"""Candidate skills over the molecular-modelling pipelines (``bioagent.structure`` and the
like). None is in the stable lockfile; see ``bioagent.skills.omics`` for what that means."""

from __future__ import annotations

from .structure import predict_protein_structure

__all__ = ["predict_protein_structure", "CANDIDATE_VERSIONS"]

CANDIDATE_VERSIONS = {
    "predict-protein-structure": "0.1.0",
}
