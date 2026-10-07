"""Literature evidence: PaperQA2 finds the passages, and the evidence chain stays ours.

    documents + digests ──build_index──▶ index (paper-qa state + manifest)
                                           │
                     question ──retrieve──▶ passages (text, offset, cosine score)
                                           │                 no model on this path
                                 to_evidence ──▶ EvidenceItem (located, typed by rule)
                                           │      or withheld (no design was readable)
                     model + envelope ──synthesise──▶ CandidateAnswer (never evidence)

:mod:`.paperqa` adapts paper-qa for indexing, retrieval and the gated synthesis;
:mod:`.evidence` types a passage without importing paper-qa; :mod:`.records` holds what
the two exchange. paper-qa is imported only when an index is built or read, so this
package imports without it.
"""

from __future__ import annotations

from .evidence import (FIELDS, FieldReading, TypedPassage, card_id, document_card,
                       evidence_id, read_fields, to_evidence)
from .paperqa import (INDEX_FORMAT, INDEX_MANIFEST, LOCAL_EMBEDDING, CandidateAnswer,
                      ChunkingConfig, EmbeddingConfig, IndexRef, ModelPermission, build_index,
                      load_index, permit_model, retrieve, synthesise)
from .records import (MEDIA_TYPES, DocumentRef, IndexedDocument, LiteratureRefused,
                      Passage, RetrievalResult)

__all__ = [
    # records
    "MEDIA_TYPES", "DocumentRef", "IndexedDocument", "LiteratureRefused", "Passage",
    "RetrievalResult",
    # evidence
    "FIELDS", "FieldReading", "TypedPassage", "card_id", "document_card", "evidence_id",
    "read_fields", "to_evidence",
    # paperqa
    "INDEX_FORMAT", "INDEX_MANIFEST", "LOCAL_EMBEDDING", "CandidateAnswer", "ChunkingConfig",
    "EmbeddingConfig", "IndexRef", "ModelPermission", "build_index", "load_index",
    "permit_model", "retrieve", "synthesise",
]
