"""Candidate skills over the literature path (``bioagent.literature``).

Not a P0 skill and not in the stable lockfile. Its manifest lives under
``skills/candidates/literature/``; a governed run refuses it unless it is a development
run (``allow_unpinned``), which is recorded but never released, until a person promotes it
through the registry's review (``updates.registry``).
"""

from __future__ import annotations

from .retrieve import retrieve_literature_evidence

__all__ = ["retrieve_literature_evidence"]
