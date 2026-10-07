"""Candidate skills over the analysis pipelines (``bioagent.omics`` and the like).

These are not P0 skills and are not in the stable lockfile. Their manifests live under
``skills/candidates/``; a governed run refuses them unless it is a development run
(``allow_unpinned``), which is recorded but never released, until a person promotes
one through the registry's review (``updates.registry``).
"""

from __future__ import annotations

from .rnaseq import rnaseq_differential_expression

__all__ = ["rnaseq_differential_expression", "CANDIDATE_VERSIONS"]

CANDIDATE_VERSIONS = {
    "rnaseq-differential-expression": "0.1.0",
}
