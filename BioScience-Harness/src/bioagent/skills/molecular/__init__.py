"""Candidate skills over the molecular-modelling pipelines (``bioagent.structure`` and the
like). None is in the stable lockfile; see ``bioagent.skills.omics`` for what that means."""

from __future__ import annotations

from .admet import predict_admet
from .docking import dock_ligands
from .structure import predict_protein_structure

__all__ = ["predict_protein_structure", "dock_ligands", "predict_admet",
           "CANDIDATE_VERSIONS"]

CANDIDATE_VERSIONS = {
    "predict-protein-structure": "0.1.0",
    "dock-ligands": "0.1.0",
    "predict-admet": "0.1.0",
}
