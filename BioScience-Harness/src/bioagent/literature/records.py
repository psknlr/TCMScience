"""What the literature path reads and returns: documents, located passages, refusals.

Kept apart from the PaperQA adapter so that typing a passage does not import paper-qa. A
passage is an excerpt located in a text whatever found it; which EvidenceItem fields it
can carry is decided by :mod:`bioagent.literature.evidence`, not by the retriever.

A document is named by the digest of its bytes, not by its path. Two runs that read
"trial.pdf" a month apart read the same document only if the bytes agree, and the
digest is the one fact about the file that a later reader can re-check.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..status import ExecutionStatus

__all__ = ["MEDIA_TYPES", "DocumentRef", "IndexedDocument", "LiteratureRefused", "Passage",
           "RetrievalResult"]

#: The file types the adapter reads, by suffix. Anything else is refused rather than
#: handed to a parser chosen by guesswork.
MEDIA_TYPES: Mapping[str, str] = {".txt": "text/plain", ".html": "text/html",
                                  ".htm": "text/html", ".pdf": "application/pdf"}

_SHA256 = re.compile(r"[0-9a-f]{64}")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_PMID = re.compile(r"[1-9]\d{0,8}")
_DOC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")


class LiteratureRefused(RuntimeError):
    """The literature path would not do what was asked, and the status says why.

    ``DENIED`` is a policy refusal (a digest that does not match, a model the run may not
    reach); ``UNAVAILABLE`` is something missing here (paper-qa, a PDF parser, a model);
    ``FAILED`` is an input that broke a guarantee the path relies on.
    """

    def __init__(self, status: ExecutionStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DocumentRef:
    """One document the run is authorised to read: a local file and its SHA-256.

    The digest is required and is checked against the file's bytes before anything is
    parsed. Without it, whatever sat at the path on the day would be indexed and cited
    as the document that was authorised.

    ``doi`` and ``pmid`` are what the caller knows about the document. They are carried,
    not looked up: nothing here resolves them against a registry, and the source card
    says so.
    """

    path: str
    sha256: str
    doc_id: str = ""
    doi: str = ""
    pmid: str = ""
    citation: str = ""
    title: str = ""
    year: int = 0
    license_spdx: str = ""

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("DocumentRef needs a path")
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError(f"{self.path}: sha256 must be 64 lowercase hex characters")
        if Path(self.path).suffix.lower() not in MEDIA_TYPES:
            raise ValueError(f"{self.path}: only {sorted(MEDIA_TYPES)} files are read")
        if self.doc_id and not _DOC_ID.fullmatch(self.doc_id):
            raise ValueError(f"doc_id {self.doc_id!r} must be letters, digits, '.', '_' or "
                             "'-' (it names files and evidence ids)")
        if self.doi and not _DOI.fullmatch(self.doi):
            raise ValueError(f"{self.path}: {self.doi!r} is not a DOI (10.<prefix>/<suffix>)")
        if self.pmid and not _PMID.fullmatch(self.pmid):
            raise ValueError(f"{self.path}: {self.pmid!r} is not a PMID")
        if self.year < 0:
            raise ValueError(f"{self.path}: year cannot be negative")

    @property
    def id(self) -> str:
        return self.doc_id or f"doc-{self.sha256[:12]}"

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[Path(self.path).suffix.lower()]

    @property
    def cited_as(self) -> str:
        """The declared citation, else the file's name and digest — never a model's guess."""
        return self.citation or f"{Path(self.path).name} (sha256 {self.sha256[:12]})"

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "doc_id": self.id,
                "doi": self.doi, "pmid": self.pmid, "citation": self.citation,
                "title": self.title, "year": self.year, "license_spdx": self.license_spdx}


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """A document as the index holds it: the reference, and the text it was read as.

    ``text_sha256`` names the text every passage offset points into. For a UTF-8 text
    file it equals ``ref.sha256`` — the quote is located in the original document. For
    HTML and PDF it cannot: their bytes are not the text, so the text is the parser's
    output, and ``parser`` names the parser so the step from file to text can be re-run.
    """

    ref: DocumentRef
    bytes: int
    parser: str
    text_sha256: str
    text_chars: int
    chunks: int

    @property
    def doc_id(self) -> str:
        return self.ref.id

    @property
    def citation(self) -> str:
        return self.ref.cited_as

    @property
    def identifier(self) -> tuple[str, str]:
        """``(identifier, identifier_type)``: the DOI or PMID when known, else the digest.

        The fallback is the content digest rather than the path, because a path names a
        place and the digest names the bytes that were read.
        """
        if self.ref.doi:
            return self.ref.doi, "doi"
        if self.ref.pmid:
            return self.ref.pmid, "pmid"
        return f"sha256:{self.ref.sha256}", "local_artifact"

    def as_dict(self) -> dict[str, Any]:
        return {**self.ref.as_dict(), "media_type": self.ref.media_type,
                "bytes": self.bytes, "parser": self.parser,
                "text_sha256": self.text_sha256, "text_chars": self.text_chars,
                "chunks": self.chunks}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IndexedDocument":
        ref = DocumentRef(path=str(data["path"]), sha256=str(data["sha256"]),
                          doc_id=str(data.get("doc_id") or ""), doi=str(data.get("doi") or ""),
                          pmid=str(data.get("pmid") or ""),
                          citation=str(data.get("citation") or ""),
                          title=str(data.get("title") or ""), year=int(data.get("year") or 0),
                          license_spdx=str(data.get("license_spdx") or ""))
        return cls(ref=ref, bytes=int(data["bytes"]), parser=str(data["parser"]),
                   text_sha256=str(data["text_sha256"]), text_chars=int(data["text_chars"]),
                   chunks=int(data["chunks"]))


@dataclass(frozen=True, slots=True)
class Passage:
    """A retrieved excerpt and where it sits in the text it was retrieved from.

    ``score`` is the retriever's cosine similarity between the question and the passage.
    It ranks passages for one question; it is not a measure of support, relevance to a
    claim, or quality, and nothing downstream reads it as one.
    """

    document: IndexedDocument
    text: str
    offset: int
    score: float
    rank: int

    @property
    def doc_id(self) -> str:
        return self.document.doc_id

    @property
    def passage_id(self) -> str:
        return f"{self.doc_id}@{self.offset}"

    @property
    def end(self) -> int:
        return self.offset + len(self.text)

    def located_in(self, content: str) -> bool:
        """Whether ``content`` is this passage's text, with the passage at its offset."""
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return (digest == self.document.text_sha256
                and content[self.offset:self.end] == self.text)

    def as_dict(self) -> dict[str, Any]:
        identifier, scheme = self.document.identifier
        return {"passage_id": self.passage_id, "doc_id": self.doc_id, "rank": self.rank,
                "score": self.score, "offset": self.offset, "end": self.end,
                "text": self.text, "citation": self.document.citation,
                "identifier": identifier, "identifier_type": scheme,
                "text_sha256": self.document.text_sha256,
                "document_sha256": self.document.ref.sha256}


@dataclass(frozen=True)
class RetrievalResult:
    """Passages for one question, with what was searched and how.

    ``documents`` is every document in the search, not only those that returned a
    passage: a reader must be able to tell "not in the corpus" from "in the corpus and not
    retrieved". ``sources`` holds the full text of each document a passage came from,
    keyed by its digest, so each passage can be re-located and given a receipt.
    """

    question: str
    passages: tuple[Passage, ...]
    documents: tuple[IndexedDocument, ...]
    sources: Mapping[str, str]
    index_path: str
    index_manifest_sha256: str
    method: Mapping[str, Any]
    k: int
    doc_ids: tuple[str, ...] = ()
    min_score: float = 0.0
    #: The corpus classification declared when the index was built; ``None`` when it was
    #: not, which the model gate reads as the run's ceiling rather than as public.
    sensitivity: str | None = None
    retrieved_at: str = ""
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED

    def as_dict(self) -> dict[str, Any]:
        return {"question": self.question, "status": self.status.value, "k": self.k,
                "filters": {"doc_ids": list(self.doc_ids), "min_score": self.min_score},
                "index": {"path": self.index_path,
                          "manifest_sha256": self.index_manifest_sha256,
                          "documents": [d.doc_id for d in self.documents]},
                "method": dict(self.method), "sensitivity": self.sensitivity,
                "retrieved_at": self.retrieved_at,
                "passages": [p.as_dict() for p in self.passages]}
