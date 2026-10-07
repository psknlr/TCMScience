"""PaperQA2 as a retriever: documents in, located passages out, and no model on that path.

PaperQA2 handles documents and retrieval well — readers for text, HTML and PDF, chunking,
an embedding index, a vector search — and it answers with a model's text. That text is not
evidence: a summary can drop the qualifier that mattered, and its citations name a chunk,
not the words relied on. This adapter keeps the first half and does not let the second
stand in for the evidence chain, which stays in :mod:`bioagent.literature.evidence`.

What is used from paper-qa (2026.8, read from its installed source), and what is not:

* **Indexing.** Each document is checked against its digest, read to text, cut by
  paper-qa's character chunkers and added with ``Docs.aadd_texts``, which embeds the chunks.
  ``Docs.aadd`` is not used: given no citation it asks a model to write one, and with
  ``use_doc_details`` it queries metadata services over the network.
* **Reading.** A ``.txt`` file is decoded as strict UTF-8 here, so its text *is* the file
  and the quote's content hash is the document's own digest; paper-qa's ``parse_text``
  opens it in the locale's encoding, translates newlines and drops undecodable bytes. HTML
  goes through ``html2text``, the converter paper-qa's reader uses; PDF through the parser
  paper-qa is configured with (``paperqa_pypdf`` here).
* **Chunking.** ``chunk_text(..., use_tiktoken=False)`` for text and HTML, ``chunk_pdf`` for
  PDF: both cut by characters, so every chunk is a verbatim slice and has an offset.
  paper-qa's default for text decodes windows of tiktoken tokens and splits multi-byte
  characters at their edges: cutting a Chinese abstract into 120-character windows, two
  chunks of four were not excerpts of the source at all.
* **Embedding.** ``SparseEmbeddingModel`` ("sparse"), a hashed bag of cl100k_base tokens
  computed locally; its tokenizer file ships inside litellm, which points tiktoken at it,
  so nothing is downloaded. paper-qa's default embedding is a remote API. Any embedding
  other than "sparse" must be named with a model profile and pass :func:`permit_model`.
* **Retrieval.** ``NumpyVectorStore.max_marginal_relevance_search``, which returns cosine
  scores. ``Docs.aget_evidence`` is not used: it summarises every chunk with a model, and
  with summaries skipped it gives every chunk the fixed score 5 and strips citation-like
  text from it, so its "evidence" is neither ranked nor verbatim. ``Docs.retrieve_texts``
  drops the scores.
* **Synthesis.** ``Docs.aquery`` over the retrieved passages, behind :func:`permit_model`.
  Its output is a :class:`CandidateAnswer`, which is never an EvidenceItem. The model is a
  LiteLLM model name, or a model served on this machine behind an OpenAI-compatible
  endpoint (``api_base``, such as llama.cpp's ``llama-server``); a profile that declares
  the model local must point at this machine.

Importing paper-qa imports litellm, which fetches a cost map from GitHub unless
``LITELLM_LOCAL_MODEL_COST_MAP`` is true. :func:`_paperqa` sets it before the import, so
indexing and retrieval open no socket at all; ``tests/test_literature_evidence.py`` checks
that in a subprocess that refuses every connection.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from psh.contracts import ModelProfile, RunEnvelope
from psh.labels import DataLabel, Destination, Sensitivity

from ..status import ExecutionStatus
from .records import DocumentRef, IndexedDocument, LiteratureRefused, Passage, RetrievalResult

__all__ = ["INDEX_FORMAT", "INDEX_MANIFEST", "LOCAL_EMBEDDING", "CandidateAnswer",
           "ChunkingConfig", "EmbeddingConfig", "IndexRef", "ModelPermission", "build_index",
           "load_index", "permit_model", "retrieve", "synthesise"]

INDEX_FORMAT = "bioagent.literature.paperqa-index/1"
#: The index manifest's file name inside an index directory.
INDEX_MANIFEST = "manifest.json"
#: The one embedding that runs here with no network and no model provider.
LOCAL_EMBEDDING = "sparse"

_DOCS = "docs.json"
#: Embedded at build and again at every search. A tokenizer or embedding change between
#: the two would rank queries against vectors made another way, silently; the digests must
#: agree.
_PROBE = "TCMScience literature index probe: 葛根芩连汤, empagliflozin, 2026."
#: Characters of paper-qa's answer prompt around the passages, for the budget estimate.
_PROMPT_OVERHEAD = 2500
_CONFLICTS = ("After the answer, write a final paragraph that begins 'Conflicts:' and names, "
              "with their citation keys, the sources that disagree with one another or with "
              "the answer; write 'Conflicts: none' when they do not disagree.")
_CONFLICT_SECTION = re.compile(r"^[\s*#_>-]*conflicts[\s*_]*:[\s*_]*(?P<body>.*)\Z",
                               re.I | re.M | re.S)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Which embedding the index uses: local by default, anything else gated.

    ``name`` is paper-qa's embedding name. Only ``"sparse"`` runs here without a network.
    Every other name reaches a host — a LiteLLM model is a provider API, ``st-`` weights
    come from Hugging Face, ``hybrid-`` pairs one of those with sparse — so it needs
    ``profile``, the model's destination, data ceiling and price, whose ``id`` must be that
    name, and a run envelope that permits it.
    """

    name: str = LOCAL_EMBEDDING
    ndim: int = 1024
    profile: ModelProfile | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("an embedding needs a name")
        if self.local and self.ndim < 16:
            raise ValueError("the sparse embedding needs at least 16 dimensions")
        if self.profile is not None and self.profile.id != self.name:
            raise ValueError(f"profile {self.profile.id!r} does not describe embedding "
                             f"{self.name!r}")

    @property
    def local(self) -> bool:
        return self.name == LOCAL_EMBEDDING

    def as_dict(self) -> dict[str, Any]:
        profile = self.profile
        return {"name": self.name, "local": self.local,
                "ndim": self.ndim if self.local else None,
                "profile": None if profile is None else {
                    "id": profile.id, "provider": profile.provider,
                    "destination": profile.destination.name,
                    "max_label": profile.max_label.name,
                    "usd_per_1k_input": profile.usd_per_1k_input}}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmbeddingConfig":
        raw = data.get("profile")
        profile = None if raw is None else ModelProfile(
            id=raw["id"], provider=raw["provider"],
            destination=Destination[raw["destination"]],
            max_label=Sensitivity[raw["max_label"]],
            usd_per_1k_input=float(raw["usd_per_1k_input"]))
        return cls(name=str(data["name"]), ndim=int(data.get("ndim") or 1024), profile=profile)


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """How documents are cut into passages: windows of characters, with an overlap.

    Shorter passages make a more exact quote; longer ones keep a finding with its
    qualifiers. An abstract under ``chunk_chars`` is one passage.
    """

    chunk_chars: int = 1000
    overlap: int = 100

    def __post_init__(self) -> None:
        if self.chunk_chars < 100:
            raise ValueError("chunk_chars below 100 cuts most sentences in two")
        if not 0 <= self.overlap < self.chunk_chars:
            raise ValueError("overlap must be at least 0 and below chunk_chars")


@dataclass(frozen=True)
class IndexRef:
    """A built index: a directory holding paper-qa's state, pinned by its manifest's digest.

    The manifest lists every document's digest, how its text was read, the chunking, the
    embedding and the digest of every file in the directory, so the index can be reused
    by another run and refused when anything in it has changed.
    """

    path: str
    manifest_sha256: str
    manifest: Mapping[str, Any]

    @property
    def documents(self) -> tuple[IndexedDocument, ...]:
        return tuple(IndexedDocument.from_dict(d) for d in self.manifest["documents"])

    @property
    def sensitivity(self) -> str | None:
        return self.manifest.get("sensitivity")

    @property
    def embedding(self) -> EmbeddingConfig:
        return EmbeddingConfig.from_dict(self.manifest["embedding"])


@dataclass(frozen=True, slots=True)
class ModelPermission:
    """Whether a model call may go ahead, and every reason it may not."""

    allowed: bool
    status: ExecutionStatus
    reason: str
    tokens: int = 0
    usd: float = 0.0


def permit_model(profile: ModelProfile | None, envelope: RunEnvelope | None,
                 sensitivity: str | None, *, tokens: int, out_tokens: int = 0,
                 calls: int = 1) -> ModelPermission:
    """Whether this run may send ``tokens`` of material classified ``sensitivity`` to a model.

    The checks are PSH's ``ModelGateway``: the envelope permits the model's destination,
    and the material's label is within the run's ceiling, the model's ``max_label`` and the
    destination's ceiling. They are restated over the public ``RunEnvelope``,
    ``ModelProfile`` and ``DataLabel`` because bioagent may not import the kernel. The
    envelope's hard budget is checked too: a call the run cannot pay for is refused before
    it is made, not reported after. Every failing check is reported, not only the first.

    ``sensitivity`` ``None`` means the material was never classified. It is then held at
    the run's ceiling, not treated as public: the documents are the caller's files, and
    assuming them publishable is the mistake a label lattice exists to prevent.
    """
    if profile is None:
        return ModelPermission(False, ExecutionStatus.UNAVAILABLE,
                               "no model is configured, so there is nothing to call")
    target = f"{profile.provider}/{profile.id}"
    if envelope is None:
        return ModelPermission(False, ExecutionStatus.DENIED, (
            f"no run envelope: nothing says this run may reach {target}"))
    level = (Sensitivity[sensitivity.upper()] if sensitivity
             else envelope.max_label.sensitivity)
    label = DataLabel(level)
    reasons: list[str] = []
    if not envelope.permits_destination(profile.destination):
        reasons.append(f"the run envelope does not permit destination "
                       f"{profile.destination.name} ({target})")
    if level > envelope.max_label.sensitivity:
        reasons.append(f"the material is {level.name}, above the run's ceiling "
                       f"{envelope.max_label.sensitivity.name}")
    if not profile.may_receive(label):
        reasons.append(f"the material is {level.name}"
                       + (" (unclassified, so held at the run's ceiling)"
                          if not sensitivity else "")
                       + f" but {target} accepts at most {profile.max_label.name}")
    if not label.permits(profile.destination):
        reasons.append(f"{level.name} material may not go to {profile.destination.name}")
    budget = envelope.budget
    usd = profile.estimated_usd(tokens, out_tokens)
    if calls > budget.max_model_calls:
        reasons.append(f"{calls} model call(s) exceed the budget of {budget.max_model_calls}")
    if tokens + out_tokens > budget.tokens_hard:
        reasons.append(f"about {tokens + out_tokens} tokens exceed the hard budget of "
                       f"{budget.tokens_hard}")
    if usd > budget.usd_hard:
        reasons.append(f"about ${usd:.2f} exceeds the hard budget of ${budget.usd_hard:.2f}")
    if reasons:
        return ModelPermission(False, ExecutionStatus.DENIED, "; ".join(reasons),
                               tokens + out_tokens, usd)
    return ModelPermission(True, ExecutionStatus.READY, (
        f"{target} at {profile.destination.name} may receive {level.name} material within "
        "the run's budget"), tokens + out_tokens, usd)


# ---------------------------------------------------------------------------
# paper-qa, imported without a network request
# ---------------------------------------------------------------------------

def _paperqa() -> Any:
    """paper-qa, imported so that the import opens no socket.

    litellm, which paper-qa imports, fetches its model cost map from GitHub at import time
    unless ``LITELLM_LOCAL_MODEL_COST_MAP`` is true. The variable is set here when the caller
    has not set it. A caller who set it to anything else has asked for that fetch, and this
    path refuses rather than make a request it says it does not make. Once litellm is
    imported the variable no longer matters. paper-qa's settings cannot be built without a
    PDF parser, so a missing parser is reported here, not at the first PDF.
    """
    if "litellm" not in sys.modules:
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        if os.environ["LITELLM_LOCAL_MODEL_COST_MAP"].strip().lower() != "true":
            raise LiteratureRefused(ExecutionStatus.DENIED, (
                "LITELLM_LOCAL_MODEL_COST_MAP is set to "
                f"{os.environ['LITELLM_LOCAL_MODEL_COST_MAP']!r}, so importing paper-qa would "
                "fetch litellm's cost map from the network; the literature path makes no "
                "network request"))
    try:
        import paperqa
        from paperqa.settings import get_default_pdf_parser

        get_default_pdf_parser()
    except ImportError as exc:
        raise LiteratureRefused(ExecutionStatus.UNAVAILABLE, (
            f"paper-qa is not usable here ({exc}); install bioagent's 'literature' extra"
        )) from exc
    return paperqa


def _versions(pq: Any) -> dict[str, str]:
    """The versions of everything on the path that decides what a passage is."""
    from importlib.metadata import PackageNotFoundError, version

    out = {"paper-qa": pq.__version__}
    for dist in ("fhlmi", "tiktoken", "html2text", "paper-qa-pypdf", "pypdf"):
        try:
            out[dist] = version(dist)
        except PackageNotFoundError:
            out[dist] = "absent"
    return out


def _settings(pq: Any, chunking: ChunkingConfig) -> Any:
    """paper-qa settings for indexing, every field this path depends on stated.

    ``Settings`` is a pydantic ``BaseSettings``: a field left unset is read from the
    environment, so an unrelated ``PARSING`` variable could add a document filter that
    silently drops documents. Stating the fields closes that door.
    """
    from paperqa.settings import ParsingSettings

    return pq.Settings(parsing=ParsingSettings(
        use_doc_details=False, multimodal=False, defer_embedding=False, doc_filters=None,
        reader_config={"chunk_chars": chunking.chunk_chars, "overlap": chunking.overlap}))


def _permit_embedding(config: EmbeddingConfig, envelope: RunEnvelope | None,
                      sensitivity: str | None, *, tokens: int, why: str) -> None:
    """Refuse a remote embedding the run has not been shown to permit."""
    if config.profile is None:
        raise LiteratureRefused(ExecutionStatus.DENIED, (
            f"embedding {config.name!r} reaches a remote host and names no model profile, "
            "so its destination, data ceiling and price are unknown and no run can permit "
            f"it ({why})"))
    permission = permit_model(config.profile, envelope, sensitivity, tokens=tokens)
    if not permission.allowed:
        raise LiteratureRefused(permission.status, (
            f"embedding {config.name!r} is not the local sparse embedding and {why}: "
            f"{permission.reason}"))


def _embedding_model(config: EmbeddingConfig) -> Any:
    from paperqa import embedding_model_factory

    if config.local:
        return embedding_model_factory(LOCAL_EMBEDDING, ndim=config.ndim)
    return embedding_model_factory(config.name)


def _vector_digest(vector: Sequence[float]) -> str:
    return _sha256(json.dumps([float(v) for v in vector]).encode("utf-8"))


# ---------------------------------------------------------------------------
# building an index
# ---------------------------------------------------------------------------

def build_index(documents: Sequence[DocumentRef], out_dir: str | Path, *,
                embedding: EmbeddingConfig = EmbeddingConfig(),
                chunking: ChunkingConfig = ChunkingConfig(),
                sensitivity: str | None = None,
                envelope: RunEnvelope | None = None) -> IndexRef:
    """Index ``documents`` into ``out_dir``, an empty or new directory.

    Every document is read only after its bytes match its declared digest, and the whole
    build is refused when one does not: an index silently missing a document reads, at
    retrieval, as a corpus with nothing to say. ``sensitivity`` is the corpus's
    classification (a :class:`psh.labels.Sensitivity` name), used when a passage is sent to
    a model; leaving it unset holds the corpus at the run's ceiling.
    """
    refs = tuple(documents)
    if not refs:
        raise ValueError("no documents to index")
    for what, values in (("doc_id", [r.id for r in refs]),
                         ("sha256", [r.sha256 for r in refs])):
        repeated = sorted({v for v in values if values.count(v) > 1})
        if repeated:
            raise ValueError(f"documents repeat {what} {repeated}; each document is indexed "
                             "once, or it would be counted twice")
    if sensitivity is not None and sensitivity.upper() not in Sensitivity.__members__:
        raise ValueError(f"sensitivity {sensitivity!r} is not one of "
                         f"{[s.name.lower() for s in Sensitivity]}")
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise LiteratureRefused(ExecutionStatus.DENIED, (
            f"{out} is not empty; an index is written into an empty directory, so an older "
            "one is never mixed into it"))
    if not embedding.local:
        # An estimate before anything is read; a missing file is reported when it is read.
        size = sum(Path(r.path).stat().st_size for r in refs if Path(r.path).is_file())
        _permit_embedding(embedding, envelope, sensitivity, tokens=size // 4 + 1,
                          why="the documents must be embedded by it")

    pq = _paperqa()
    model = _embedding_model(embedding)
    settings = _settings(pq, chunking)
    key = json.dumps([[r.id, r.sha256] for r in refs] + [embedding.as_dict(),
                     [chunking.chunk_chars, chunking.overlap]], sort_keys=True)
    docs = pq.Docs(id=uuid.uuid5(uuid.NAMESPACE_URL, f"bioagent-literature:{key}"),
                   name="bioagent-literature")
    indexed, texts, probe = asyncio.run(_build(pq, docs, refs, chunking, settings, model))

    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for digest, text in texts.items():
        rel = f"texts/{digest}.txt"
        (out / "texts").mkdir(exist_ok=True)
        (out / rel).write_bytes(text.encode("utf-8"))
        files[rel] = digest
    state = docs.model_dump_json(exclude={"texts_index"}).encode("utf-8")
    (out / _DOCS).write_bytes(state)
    files[_DOCS] = _sha256(state)
    manifest = {
        "format": INDEX_FORMAT, "created_at": _now(),
        "implementation": {"retriever": "paper-qa", "versions": _versions(pq),
                           "embedding_class": f"{type(model).__module__}."
                                              f"{type(model).__qualname__}"},
        "embedding": {**embedding.as_dict(), "probe_sha256": _vector_digest(probe)},
        "chunking": {"chunk_chars": chunking.chunk_chars, "overlap": chunking.overlap,
                     "text_and_html": "paperqa.readers.chunk_text(use_tiktoken=False)",
                     "pdf": "paperqa.readers.chunk_pdf"},
        "retrieval": {"vector_store": "paperqa.NumpyVectorStore",
                      "search": "max_marginal_relevance_search", "mmr_lambda": 1.0,
                      "similarity": "cosine"},
        "sensitivity": sensitivity.lower() if sensitivity else None,
        "documents": [d.as_dict() for d in indexed],
        "files": files,
    }
    blob = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
    (out / INDEX_MANIFEST).write_bytes(blob)
    return IndexRef(str(out), _sha256(blob), manifest)


async def _build(pq: Any, docs: Any, refs: Sequence[DocumentRef], chunking: ChunkingConfig,
                 settings: Any, model: Any) -> tuple[list[IndexedDocument], dict[str, str],
                                                     list[float]]:
    indexed: list[IndexedDocument] = []
    texts: dict[str, str] = {}
    for ref in refs:
        document, text, doc, chunks = await _read(pq, ref, chunking, settings)
        if not await docs.aadd_texts(chunks, doc, settings=settings, embedding_model=model):
            raise LiteratureRefused(ExecutionStatus.FAILED,
                                    f"paper-qa did not add {ref.path} to the index")
        indexed.append(document)
        texts[document.text_sha256] = text
    probe = (await model.embed_documents([_PROBE]))[0]
    return indexed, texts, probe


async def _read(pq: Any, ref: DocumentRef, chunking: ChunkingConfig,
                settings: Any) -> tuple[IndexedDocument, str, Any, list[Any]]:
    """One document: checked, read to text, chunked, and every chunk located in the text."""
    from paperqa.readers import chunk_pdf, chunk_text, html2text, html2text_version, read_doc
    from paperqa.types import Doc, ParsedMetadata, ParsedText
    from paperqa.utils import ImpossibleParsingError

    path = Path(ref.path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LiteratureRefused(ExecutionStatus.UNAVAILABLE,
                                f"{ref.path} cannot be read: {exc}") from exc
    digest = _sha256(data)
    if digest != ref.sha256:
        raise LiteratureRefused(ExecutionStatus.DENIED, (
            f"{ref.path} hashes {digest[:12]}, not the {ref.sha256[:12]} the run was "
            "authorised to read"))
    doc = Doc(docname=ref.id, dockey=ref.id, citation=ref.cited_as, content_hash=ref.sha256)
    try:
        if ref.media_type == "application/pdf":
            parsed = await read_doc(path, doc, parsed_text_only=True,
                                    parse_pdf=settings.parsing.parse_pdf, parse_media=False)
            text = "".join(c if isinstance(c, str) else c[0] for c in parsed.content.values())
            parser = (f"{settings.parsing.parse_pdf.__module__} "
                      f"({', '.join(parsed.metadata.parsing_libraries)})")
            chunks = chunk_pdf(parsed, doc, chunk_chars=chunking.chunk_chars,
                               overlap=chunking.overlap)
        else:
            text = _utf8(data, ref.path)
            parser = "strict utf-8"
            if ref.media_type == "text/html":
                version = ".".join(str(v) for v in html2text_version)
                text, parser = html2text(text), f"html2text {version}"
            parsed = ParsedText(content=text, metadata=ParsedMetadata(
                parsing_libraries=[parser], paperqa_version=pq.__version__,
                total_parsed_text_length=len(text), name=ref.media_type))
            chunks = chunk_text(parsed, doc, chunk_chars=chunking.chunk_chars,
                                overlap=chunking.overlap, use_tiktoken=False)
    except ImpossibleParsingError as exc:
        raise LiteratureRefused(ExecutionStatus.FAILED,
                                f"no text could be read from {ref.path}: {exc}") from exc
    chunks = [c for c in chunks if c.text.strip()]
    if not chunks:
        raise LiteratureRefused(ExecutionStatus.FAILED, f"{ref.path} holds no text")
    cursor = 0
    for chunk in chunks:
        # Located by search from the previous chunk's start, not by repeating the
        # chunker's arithmetic: a chunk that is not a verbatim slice is caught here.
        at = text.find(chunk.text, cursor)
        if at < 0:
            raise LiteratureRefused(ExecutionStatus.FAILED, (
                f"{ref.path}: paper-qa returned a chunk that is not a verbatim excerpt of "
                "the text it was cut from"))
        chunk.offset = at
        cursor = at
    encoded = text.encode("utf-8")
    document = IndexedDocument(ref=ref, bytes=len(data), parser=parser,
                               text_sha256=_sha256(encoded), text_chars=len(text),
                               chunks=len(chunks))
    return document, text, doc, chunks


def _utf8(data: bytes, where: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LiteratureRefused(ExecutionStatus.FAILED, (
            f"{where} is not UTF-8 ({exc.reason} at byte {exc.start}); read leniently, "
            "its quotes would no longer be the file's text")) from exc


# ---------------------------------------------------------------------------
# loading and searching an index
# ---------------------------------------------------------------------------

def load_index(path: str | Path, *, expected_sha256: str = "") -> IndexRef:
    """The index at ``path``, refused when any file differs from what its manifest records.

    ``expected_sha256`` pins the manifest itself — what a later run passes to be sure it
    reads the index an earlier run recorded.
    """
    root = Path(path)
    try:
        blob = (root / INDEX_MANIFEST).read_bytes()
    except OSError as exc:
        raise LiteratureRefused(ExecutionStatus.UNAVAILABLE,
                                f"{root} holds no readable index manifest: {exc}") from exc
    digest = _sha256(blob)
    if expected_sha256 and digest != expected_sha256:
        raise LiteratureRefused(ExecutionStatus.DENIED, (
            f"the index manifest at {root} hashes {digest[:12]}, not the expected "
            f"{expected_sha256[:12]}"))
    manifest = json.loads(blob)
    if manifest.get("format") != INDEX_FORMAT:
        raise LiteratureRefused(ExecutionStatus.FAILED, (
            f"{root} is not a {INDEX_FORMAT} index (format {manifest.get('format')!r})"))
    _check_files(root, manifest["files"], list(manifest["files"]))
    return IndexRef(str(root), digest, manifest)


def _check_files(root: Path, files: Mapping[str, str], wanted: Sequence[str]) -> None:
    problems: list[str] = []
    base = root.resolve()
    for rel in sorted(wanted):
        target = (root / rel).resolve()
        if base not in target.parents:
            problems.append(f"{rel} lies outside the index")
        elif not target.is_file():
            problems.append(f"{rel} is missing")
        elif _sha256(target.read_bytes()) != files[rel]:
            problems.append(f"{rel} does not match its digest")
    if problems:
        raise LiteratureRefused(ExecutionStatus.DENIED, (
            f"the index at {root} does not match its manifest: {'; '.join(problems)}"))


def _open(pq: Any, index: IndexRef) -> tuple[list[Any], dict[str, str]]:
    """paper-qa's texts, with their embeddings and offsets, and every document's text.

    The files are checked against the manifest again as they are read: an index checked
    when it was loaded and edited before it was searched would otherwise pass.
    """
    root, files = Path(index.path), index.manifest["files"]
    documents = index.documents
    wanted = [_DOCS] + [f"texts/{d.text_sha256}.txt" for d in documents]
    _check_files(root, files, wanted)
    docs = pq.Docs.model_validate_json((root / _DOCS).read_bytes())
    # Bytes, then decode: read_text would translate "\r\n" and move every offset after it.
    sources = {d.text_sha256: (root / f"texts/{d.text_sha256}.txt").read_bytes().decode(
        "utf-8") for d in documents}
    known = {d.doc_id for d in documents}
    for text in docs.texts:
        if text.doc.dockey not in known or not isinstance(
                (text.model_extra or {}).get("offset"), int):
            raise LiteratureRefused(ExecutionStatus.FAILED, (
                f"the index at {root} holds a passage ({text.name}) with no offset or no "
                "document in its manifest"))
    return list(docs.texts), sources


def retrieve(question: str, index: IndexRef | str | Path, *, k: int = 5,
             doc_ids: Sequence[str] = (), min_score: float = 0.0,
             envelope: RunEnvelope | None = None) -> RetrievalResult:
    """The ``k`` passages of the index nearest ``question``, each located in its document.

    No model is called. ``doc_ids`` restricts the search to those documents before
    ranking, so the top ``k`` are the top ``k`` among them; ``min_score`` then drops
    passages below a cosine similarity, and a passage at 0 — sharing nothing with the
    question — is never returned, whatever ``k`` asks for. ``envelope`` is needed only for
    an index built with a remote embedding, whose query embedding is a model call like any
    other.
    """
    if not question.strip():
        raise ValueError("a question is needed to retrieve passages")
    if k < 1:
        raise ValueError("k must be at least 1")
    ref = index if isinstance(index, IndexRef) else load_index(index)
    config = ref.embedding
    if not config.local:
        _permit_embedding(config, envelope, ref.sensitivity, tokens=len(question) // 4 + 1,
                          why="the index was built with it, so the question must be too")
    documents = {d.doc_id: d for d in ref.documents}
    unknown = sorted(set(doc_ids) - set(documents))
    if unknown:
        raise ValueError(f"doc_ids {unknown} are not in the index")
    pq = _paperqa()
    texts, sources = _open(pq, ref)
    model = _embedding_model(config)
    hits, probe = asyncio.run(_search(
        [t for t in texts if not doc_ids or t.doc.dockey in doc_ids], question, k, model))
    if _vector_digest(probe) != ref.manifest["embedding"]["probe_sha256"]:
        raise LiteratureRefused(ExecutionStatus.FAILED, (
            "the embedding here does not reproduce the index's embeddings (probe digest "
            "differs), so its scores would rank vectors made another way; rebuild the index"))
    passages: list[Passage] = []
    for rank, (text, score) in enumerate(hits, start=1):
        document = documents[text.doc.dockey]
        passage = Passage(document, text.text, int(text.offset), round(float(score), 6), rank)
        if not passage.located_in(sources[document.text_sha256]):
            raise LiteratureRefused(ExecutionStatus.FAILED, (
                f"passage {passage.passage_id} is not at its offset in its document's text"))
        if passage.score > 0 and passage.score >= min_score:
            passages.append(passage)
    searched = tuple(documents[d] for d in (doc_ids or documents))
    return RetrievalResult(
        question=question, passages=tuple(passages), documents=searched,
        sources={p.document.text_sha256: sources[p.document.text_sha256] for p in passages},
        index_path=ref.path, index_manifest_sha256=ref.manifest_sha256,
        method={"retriever": f"paper-qa {pq.__version__}",
                "vector_store": "paperqa.NumpyVectorStore",
                "search": f"max_marginal_relevance_search (k={k}, fetch_k={2 * k}, "
                          "mmr_lambda=1.0)",
                "similarity": "cosine", "embedding": config.as_dict(),
                "model_calls": 0 if config.local else 1, "network": not config.local},
        k=k, doc_ids=tuple(doc_ids), min_score=min_score, sensitivity=ref.sensitivity,
        retrieved_at=_now())


async def _search(texts: list[Any], question: str, k: int,
                  model: Any) -> tuple[list[tuple[Any, float]], list[float]]:
    from paperqa import NumpyVectorStore

    store = NumpyVectorStore()
    await store.add_texts_and_embeddings(texts)
    found, scores = await store.max_marginal_relevance_search(
        question, k=k, fetch_k=2 * k, embedding_model=model)
    probe = (await model.embed_documents([_PROBE]))[0]
    # With mmr_lambda at 1 the store returns every fetched text; paper-qa's own
    # retrieve_texts cuts to k afterwards, and so does this.
    return list(zip(found, scores))[:k], probe


# ---------------------------------------------------------------------------
# synthesis: a candidate explanation, never evidence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateAnswer:
    """A model's explanation over retrieved passages. It is never evidence.

    ``citations`` are the passages the text cites; ``unsupported_citations`` are keys the
    text cites that name no passage it was given. ``conflicts`` are what the model stated
    as disagreement, and ``conflicts_stated`` says whether it stated anything at all. A
    claim cites the passages' EvidenceItems, not this text: the text is one reading of
    them, made by a model, and can drop a qualifier that a quote keeps.
    """

    question: str
    status: ExecutionStatus
    reason: str = ""
    text: str = ""
    citations: tuple[str, ...] = ()
    unsupported_citations: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    conflicts_stated: bool = False
    passages: tuple[str, ...] = ()
    model: str = ""
    destination: str = ""
    estimated_tokens: int = 0
    estimated_usd: float = 0.0
    tokens: Mapping[str, Any] = field(default_factory=dict)
    cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"kind": "candidate_explanation", "is_evidence": False,
                "question": self.question, "status": self.status.value,
                "reason": self.reason, "text": self.text,
                "citations": list(self.citations),
                "unsupported_citations": list(self.unsupported_citations),
                "conflicts": list(self.conflicts),
                "conflicts_stated": self.conflicts_stated,
                "passages": list(self.passages), "model": self.model,
                "destination": self.destination,
                "estimated_tokens": self.estimated_tokens,
                "estimated_usd": self.estimated_usd, "tokens": dict(self.tokens),
                "cost": self.cost}


def _loopback(url: str) -> bool:
    """Whether ``url`` names this machine: localhost or a loopback address."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def synthesise(retrieval: RetrievalResult, *, model: ModelProfile | None = None,
               envelope: RunEnvelope | None = None, llm: Any = None,
               max_answer_tokens: int = 1024, api_base: str = "") -> CandidateAnswer:
    """A candidate explanation of ``retrieval``'s passages, or the reason there is none.

    Refused without a configured ``model``, without passages (an answer over nothing would
    be the model's recall, not the documents'), and when :func:`permit_model` refuses the
    call. ``llm`` is the object paper-qa calls (its ``call_single``, as on an lmi
    ``LLMModel``); by default it is built from ``model.id`` as a LiteLLM model name, with
    ``max_answer_tokens`` as its output limit. ``model`` is what the gate judges either way.

    ``api_base`` is an OpenAI-compatible endpoint on this machine that serves the model
    (``model.id`` is then ``openai/<served name>``, and the profile's destination is
    normally ``LOCAL_MODEL``). Any other host is refused: the gate judges the profile, and
    LiteLLM would send the environment's provider key to an endpoint that is not that
    provider. A model on another host is reached through ``llm``, built with its own
    credentials, under a ``TRUSTED_REMOTE`` or ``PUBLIC_REMOTE`` profile.
    """
    question = retrieval.question
    ids = tuple(p.passage_id for p in retrieval.passages)
    if model is None:
        return CandidateAnswer(question, ExecutionStatus.UNAVAILABLE, passages=ids, reason=(
            "no model is configured for synthesis; retrieval needs none, and a candidate "
            "answer is not produced without one"))
    common = {"passages": ids, "model": model.id,
              "destination": model.destination.name.lower()}
    if api_base and not _loopback(api_base):
        return CandidateAnswer(question, ExecutionStatus.DENIED, **common, reason=(
            f"api_base is for a model served on this machine, and "
            f"{urllib.parse.urlsplit(api_base).hostname or api_base!r} is not; a model on "
            "another host is reached through llm, built with its own credentials, under a "
            "TRUSTED_REMOTE or PUBLIC_REMOTE profile"))
    if not retrieval.passages:
        return CandidateAnswer(question, ExecutionStatus.DENIED, **common, reason=(
            "no passage was retrieved; an answer over nothing would be the model's recall, "
            "not the documents'"))
    chars = _PROMPT_OVERHEAD + len(question) + sum(
        len(p.text) + len(p.document.citation) for p in retrieval.passages)
    permission = permit_model(model, envelope, retrieval.sensitivity, tokens=chars // 4 + 1,
                              out_tokens=max_answer_tokens)
    common |= {"estimated_tokens": permission.tokens, "estimated_usd": permission.usd}
    if not permission.allowed:
        return CandidateAnswer(question, permission.status, reason=permission.reason,
                               **common)
    keys = {f"pqac-{_sha256(p.passage_id.encode('utf-8'))[:8]}": p
            for p in retrieval.passages}
    try:
        session = asyncio.run(_answer(retrieval, keys, model, llm, max_answer_tokens,
                                      api_base))
    except LiteratureRefused as exc:
        return CandidateAnswer(question, exc.status, reason=exc.reason, **common)
    except Exception as exc:                                  # noqa: BLE001
        # A provider error is the outcome of this call, reported as FAILED with its
        # cause, not an exception that loses the passages it was asked about.
        return CandidateAnswer(question, ExecutionStatus.FAILED, **common, reason=(
            f"the model call failed: {type(exc).__name__}: {str(exc)[:300]}"))
    from paperqa.utils import get_citation_ids

    raw = session.raw_answer
    cited = get_citation_ids(raw)
    conflicts, stated = _conflicts(raw)
    return CandidateAnswer(
        question, ExecutionStatus.SUCCEEDED, text=raw,
        citations=tuple(keys[c].passage_id for c in cited if c in keys),
        unsupported_citations=tuple(c for c in cited if c not in keys),
        conflicts=conflicts, conflicts_stated=stated, tokens=dict(session.token_counts),
        cost=float(session.cost), **common)


async def _answer(retrieval: RetrievalResult, keys: Mapping[str, Passage],
                  model: ModelProfile, llm: Any, max_answer_tokens: int,
                  api_base: str = "") -> Any:
    """paper-qa's answer step over the given passages, with no retrieval of its own."""
    pq = _paperqa()
    from paperqa.prompts import default_system_prompt
    from paperqa.settings import (AnswerSettings, ParsingSettings, PromptSettings,
                                  make_default_litellm_model_list_settings)
    from paperqa.types import Context, Doc, PQASession, Text

    if llm is None:
        from lmi import LiteLLMModel

        config = make_default_litellm_model_list_settings(model.id, 0.0)
        params = config["model_list"][0]["litellm_params"]
        params["max_tokens"] = max_answer_tokens
        if api_base:
            # The server on this machine takes no key, and LiteLLM's OpenAI client wants
            # one; without this it would send the environment's OpenAI key.
            params.update(api_base=api_base, api_key="no-key-for-a-local-server")
        llm = LiteLLMModel(name=model.id, config=config)
    contexts = [Context(
        id=key, context=p.text, question=retrieval.question,
        # paper-qa ranks contexts on a 0-10 integer and drops those below 1.
        score=max(1, min(10, round(p.score * 10))),
        text=Text(text=p.text, name=p.passage_id,
                  doc=Doc(docname=p.doc_id, dockey=p.doc_id, citation=p.document.citation)))
        for key, p in keys.items()]
    settings = pq.Settings(
        llm=model.id, summary_llm=model.id, temperature=0.0,
        answer=AnswerSettings(answer_max_sources=len(contexts),
                              evidence_relevance_score_cutoff=1,
                              get_evidence_if_no_contexts=False),
        prompts=PromptSettings(system=f"{default_system_prompt}\n\n{_CONFLICTS}"),
        parsing=ParsingSettings(use_doc_details=False, multimodal=False))
    session = PQASession(question=retrieval.question, contexts=contexts)
    return await pq.Docs().aquery(session, settings=settings, llm_model=llm,
                                  summary_llm_model=llm,
                                  embedding_model=_embedding_model(EmbeddingConfig()))


def _conflicts(answer: str) -> tuple[tuple[str, ...], bool]:
    """The disagreements the answer states, and whether it stated any section at all."""
    match = _CONFLICT_SECTION.search(answer)
    if match is None:
        return (), False
    body = match.group("body").strip()
    if not body or re.match(r"none\b", body, re.I):
        return (), True
    items = (s.strip(" -*•\t") for s in re.split(r"\n+|;\s+", body))
    return tuple(i for i in items if i), True
