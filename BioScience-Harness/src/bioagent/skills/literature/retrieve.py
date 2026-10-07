"""`retrieve-literature-evidence` — passages from the caller's documents, typed by rule.

Runs the literature path (:mod:`bioagent.literature`) over a corpus the caller hands over
and returns a research artifact:

* **Corpus.** A directory holding ``manifest.json``: each document is a file in that
  directory with its SHA-256 and what the caller knows about it (DOI or PMID, citation,
  title, year, licence), and the corpus may declare its sensitivity. The skill reads
  nothing outside the directory and nothing the manifest does not pin.
* **Sources.** One source card per document searched, pinned by the digest of its bytes.
* **Evidence.** One item per retrieved passage whose study design a rule could read, each
  with a receipt for its quote. A passage with no readable design is withheld and listed,
  with its offset and readings, in ``literature_evidence.json``.
* **No claim.** Retrieval gathers material for a claim; it does not make one. Which claim,
  of which kind and scope, is for whoever reads the evidence, and the contract checks that
  choice. Nor does the skill call a model: a candidate answer needs
  :func:`bioagent.literature.synthesise`, a configured model and an envelope permitting it.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...literature import (INDEX_MANIFEST, DocumentRef, IndexRef, LiteratureRefused,
                           build_index, document_card, load_index, retrieve, to_evidence)
from ...status import ExecutionStatus
from ..base import artifact, file_of, json_file

__all__ = ["CORPUS_MANIFEST", "read_corpus", "retrieve_literature_evidence"]

SKILL_ID = "retrieve-literature-evidence"
SKILL_VERSION = "0.1.0"
#: The file in a corpus directory that lists its documents.
CORPUS_MANIFEST = "manifest.json"
_CORPUS_KEYS = frozenset({"documents", "sensitivity"})
_DOCUMENT_KEYS = frozenset({"file", "sha256", "doc_id", "doi", "pmid", "citation", "title",
                            "year", "license_spdx"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_corpus(corpus: str | Path) -> tuple[tuple[DocumentRef, ...], str | None]:
    """The documents a corpus manifest pins, and the sensitivity it declares.

    Strict about keys, as skill manifests are: a misspelled ``sah256`` would otherwise read
    as a document with no digest. Every file must resolve, symlinks followed, to a place
    under the corpus directory, so the skill's declared read permission covers what it
    reads.
    """
    root = Path(corpus).resolve()
    path = root / CORPUS_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not a readable corpus manifest: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError(f"{path}: expected an object with a 'documents' list")
    unknown = sorted(set(data) - _CORPUS_KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {unknown}")
    refs: list[DocumentRef] = []
    for i, entry in enumerate(data["documents"]):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: document {i} is not an object")
        unknown = sorted(set(entry) - _DOCUMENT_KEYS)
        if unknown:
            raise ValueError(f"{path}: document {i} has unknown key(s) {unknown}")
        missing = [key for key in ("file", "sha256") if not entry.get(key)]
        if missing:
            raise ValueError(f"{path}: document {i} names no {' or '.join(missing)}")
        target = (root / str(entry["file"])).resolve()
        if root not in target.parents:
            raise ValueError(f"{path}: {entry['file']!r} is outside the corpus directory; "
                             "the skill reads only what sits under it")
        refs.append(DocumentRef(
            path=str(target), sha256=str(entry["sha256"]),
            doc_id=str(entry.get("doc_id") or ""), doi=str(entry.get("doi") or ""),
            pmid=str(entry.get("pmid") or ""), citation=str(entry.get("citation") or ""),
            title=str(entry.get("title") or ""), year=int(entry.get("year") or 0),
            license_spdx=str(entry.get("license_spdx") or "")))
    sensitivity = data.get("sensitivity")
    return tuple(refs), (str(sensitivity) if sensitivity else None)


def _index(refs: tuple[DocumentRef, ...], sensitivity: str | None,
           index_dir: str) -> IndexRef:
    """An index of exactly these documents: the one in ``index_dir`` if it is one, else new.

    An index already in ``index_dir`` is reused only when it pins the same documents, with
    the same metadata and sensitivity. Anything else is refused, not rebuilt over: an index
    another run recorded is the record of what that run searched, and ``build_index``
    writes only into an empty directory.
    """
    if index_dir and (Path(index_dir) / INDEX_MANIFEST).is_file():
        index = load_index(index_dir)
        if (sorted(_described(d.ref) for d in index.documents)
                != sorted(_described(r) for r in refs)
                or index.sensitivity != (sensitivity.lower() if sensitivity else None)):
            raise LiteratureRefused(ExecutionStatus.DENIED, (
                f"{index_dir} holds an index of a different corpus; pass an empty or new "
                "index_dir to build one"))
        return index
    return build_index(refs, index_dir or tempfile.mkdtemp(prefix="bioagent-literature-"),
                       sensitivity=sensitivity)


def _described(ref: DocumentRef) -> str:
    """A reference without its path, which moves with the corpus while the bytes do not."""
    return json.dumps({k: v for k, v in ref.as_dict().items() if k != "path"},
                      sort_keys=True)


def retrieve_literature_evidence(question: str, corpus: str, *, k: int = 5,
                                 min_score: float = 0.0, doc_ids: list[str] | None = None,
                                 index_dir: str = "", run_id: str = "") -> Any:
    """Passages of ``corpus`` that bear on ``question``, as located and typed evidence."""
    refs, sensitivity = read_corpus(corpus)
    index = _index(refs, sensitivity, index_dir)
    result = retrieve(question, index, k=k, doc_ids=tuple(doc_ids or ()), min_score=min_score)
    cards = {d.doc_id: document_card(d, snapshot_at=index.manifest["created_at"])
             for d in result.documents}
    retrieved_at = time.time()
    typed = [to_evidence(p, result.sources[p.document.text_sha256],
                         source_card_id=cards[p.doc_id].id, retrieved_by=SKILL_ID,
                         retrieval_run=run_id, retrieved_at=retrieved_at)
             for p in result.passages]
    evidence = [t.item for t in typed if t.item is not None]
    withheld = len(typed) - len(evidence)

    # The index manifest as written, so the output's digest is the index's own pin.
    manifest = (Path(index.path) / INDEX_MANIFEST).read_bytes()
    if hashlib.sha256(manifest).hexdigest() != index.manifest_sha256:
        raise LiteratureRefused(ExecutionStatus.DENIED, (
            f"the index manifest in {index.path} changed while it was being searched"))
    out_index = file_of("index_manifest.json", manifest.decode("utf-8"),
                        media_type="application/json",
                        description="the paper-qa index searched: document digests, how each "
                                    "was read, chunking, embedding and file digests")
    out_passages, _ = json_file("literature_evidence.json", {
        "question": question,
        "retrieval": {key: value for key, value in result.as_dict().items()
                      if key != "passages"},
        "passages": [t.as_dict() for t in typed],
        "counts": {"documents_searched": len(result.documents), "passages": len(typed),
                   "evidence_items": len(evidence), "withheld": withheld}},
        description="every retrieved passage: offset, score, the fields each rule read, and "
                    "its evidence id or why it was withheld")

    ndim = index.manifest["embedding"].get("ndim")
    limitations = [
        "retrieval is lexical: paper-qa's sparse embedding hashes tokens into "
        f"{ndim} buckets with no notion of meaning, so a passage that says the same thing in "
        "other words can be missed; the cosine score ranks passages for this question only",
        f"only the {len(result.documents)} document(s) searched were in scope; this is not a "
        "systematic search, and a passage not returned is not evidence of absence",
        "design, population, comparator and outcome are read by fixed text rules, and only "
        "when the text gives one reading; a field not read is left unassessed, not inferred",
        "retraction status was not checked, as no registry was queried: every item reads "
        "'unverified'",
        "quality (risk of bias, directness, precision, consistency) was not assessed",
        "identifiers, citations, years and licences are as the corpus manifest declares "
        "them; none was checked against a registry",
        "no claim is made and no model was called: the passages are material for a claim, "
        "and a candidate answer needs bioagent.literature.synthesise with a model the run "
        "permits",
    ]
    if withheld:
        limitations.append(
            f"{withheld} of {len(typed)} retrieved passage(s) come from documents with no "
            "study design a rule could read; they are listed in literature_evidence.json with "
            "their offsets and are not evidence items")
    if not typed:
        limitations.append(
            "no passage was retrieved: none shared a token with the question at a score of at "
            f"least {min_score}; this is an empty result, not evidence of absence")
    if any(d.text_sha256 != d.ref.sha256 for d in result.documents):
        limitations.append(
            "quotes from HTML and PDF documents are located in the text paper-qa's reader "
            "extracted, not in the files' bytes; each source card pins the file and names the "
            "extractor")

    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question=question, sources=tuple(cards.values()), evidence=evidence,
        outputs=(out_passages, out_index), limitations=limitations,
        assumptions=("the corpus manifest describes each document correctly: its "
                     "identifier, citation, year and licence",),
        created_at=_now(),
        provenance={"retriever": result.method["retriever"],
                    "versions": dict(index.manifest["implementation"]["versions"]),
                    "index_dir": index.path, "index_manifest_sha256": index.manifest_sha256,
                    "retrieval": dict(result.method), "k": k, "min_score": min_score,
                    "doc_ids": list(doc_ids or ()),
                    "documents_searched": len(result.documents), "passages": len(typed),
                    "evidence_items": len(evidence), "withheld": withheld})
