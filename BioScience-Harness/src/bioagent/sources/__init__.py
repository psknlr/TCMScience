"""Third-party databases as validated, hashed, read-only snapshots.

See ``docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md``. Three parts:

* ``cards``    — one card per source: access paths in order of preference, licence,
                 default evidence labels; web access off unless a person approved it;
                 ``effective_sources`` lets a skill narrow access and never widen it.
* ``schema``   — the node and edge tables every source is reduced to, with two evidence
                 axes (Biolink knowledge level / agent type, and study design).
* ``snapshot`` — build (quality gate, content hashes, deterministic id) and load
                 (re-verified on every load).
"""

from .cards import (ACCESS_MODES, SOURCE_CARDS, Access, Approval, EdgeDefault, SourceCard,
                    SourceCardError, card, effective_sources, parse_ref)
from .schema import (AGENT_TYPES, COMPOSITION_LEVELS, EDGE_PREDICATES, KNOWLEDGE_LEVELS,
                     NODE_CATEGORIES, STUDY_DESIGNS, check_claim, licensed_claims,
                     validate_edge, validate_node)
from .snapshot import (QCReport, QCThresholds, Snapshot, SnapshotError, SnapshotRejected,
                       build_snapshot, load_snapshot, quality_check)

__all__ = [
    "ACCESS_MODES", "SOURCE_CARDS", "Access", "Approval", "EdgeDefault", "SourceCard",
    "SourceCardError", "card", "effective_sources", "parse_ref",
    "AGENT_TYPES", "COMPOSITION_LEVELS", "EDGE_PREDICATES", "KNOWLEDGE_LEVELS",
    "NODE_CATEGORIES", "STUDY_DESIGNS", "check_claim", "licensed_claims", "validate_edge",
    "validate_node",
    "QCReport", "QCThresholds", "Snapshot", "SnapshotError", "SnapshotRejected",
    "build_snapshot", "load_snapshot", "quality_check",
]
