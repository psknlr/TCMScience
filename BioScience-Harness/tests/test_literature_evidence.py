"""Literature evidence on PaperQA2: located, typed passages, and no model on that path.

The corpus is three short abstracts from the governance ablation's corpus
(``bioagent.benchmarks.ablation_corpus``). Two are fixtures under the reserved example DOI
prefix 10.5555 and describe no real study. The third is the EMPEROR-Preserved result
sentence (PMID 34449189) as the ablation quotes it: it names no study design, which makes
it the case for a withheld passage.

Tests that need paper-qa take the ``paperqa`` fixture and skip without it (they fail under
``BIOAGENT_REQUIRE_TOOLS=1``). The typing rules, the model gate and the skill manifest are
tested without it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from psh.contracts import Budget, ModelProfile, RunEnvelope
from psh.labels import DataLabel, Destination, Sensitivity

import bioagent
from bioagent.benchmarks.ablation_corpus import BASE_CASES
from bioagent.contracts import CandidateClaim, check_claim, validate_artifact
from bioagent.contracts.receipts import default_store
from bioagent.literature import (DocumentRef, EmbeddingConfig, IndexedDocument,
                                 LiteratureRefused, Passage, build_index, load_index,
                                 permit_model, read_fields, retrieve, synthesise, to_evidence)
from bioagent.status import ExecutionStatus
from omics_world import need_module

TEXTS = {case["id"]: case["source"]["text"] for case in BASE_CASES}
CANDIDATES = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "literature"
EMPEROR_Q = "Does empagliflozin reduce hospitalization for heart failure?"

#: name -> (body, what the corpus manifest declares about it). The fixtures were written
#: for the benchmark, so their manifest may declare a licence; the published sentence's
#: does not, and its card says so.
CORPUS = {
    "gegen.txt": (TEXTS["E1"], {"doc_id": "gegen", "doi": "10.5555/tcm-ablation.01",
                                "citation": "Example trial of 葛根芩连汤 (fixture)",
                                "year": 2024, "license_spdx": "CC0-1.0"}),
    "emperor.txt": (TEXTS["E2"], {"doc_id": "emperor", "pmid": "34449189",
                                  "citation": "EMPEROR-Preserved, results sentence"}),
    "ctx.html": (f"<html><body><h1>C-telopeptide cohort</h1><p>{TEXTS['A1']}</p></body>"
                 "</html>", {"doc_id": "ctx", "doi": "10.5555/tcm-ablation.05",
                             "citation": "Example cohort (fixture)",
                             "license_spdx": "CC0-1.0"}),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_corpus(root: Path, files=CORPUS, sensitivity: str | None = "public") -> Path:
    """The documents and a corpus manifest pinning each by its digest."""
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, (body, meta) in files.items():
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        (root / name).write_bytes(data)
        entries.append({"file": name, "sha256": _sha(data), **meta})
    manifest = {"documents": entries}
    if sensitivity:
        manifest["sensitivity"] = sensitivity
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                        encoding="utf-8")
    return root


def refs_for(root: Path) -> tuple[DocumentRef, ...]:
    from bioagent.skills.literature.retrieve import read_corpus
    return read_corpus(root)[0]


def pdf_bytes(lines: list[str]) -> bytes:
    """A one-page PDF drawing ``lines`` in Helvetica, written by hand with a valid xref."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["BT", "/F1 11 Tf", "14 TL", "72 760 Td"] + [f"({esc(x)}) Tj T*" for x in lines]
    stream = "\n".join(ops + ["ET"]).encode("latin-1")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
               b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(out)


@pytest.fixture(scope="module")
def paperqa():
    # Importing paper-qa directly would let litellm fetch its cost map from the network;
    # the adapter sets this before it imports paper-qa, and so does the test.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    return need_module("paperqa")


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return write_corpus(tmp_path_factory.mktemp("literature") / "corpus")


@pytest.fixture(scope="module")
def index(paperqa, corpus):
    return build_index(refs_for(corpus), corpus.parent / "index", sensitivity="public")


def passage_of(result, doc_id: str) -> Passage:
    return next(p for p in result.passages if p.doc_id == doc_id)


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------

def test_the_index_pins_every_document_and_how_it_was_read(index, corpus):
    docs = {d.doc_id: d for d in index.documents}
    for name, (body, meta) in CORPUS.items():
        assert docs[meta["doc_id"]].ref.sha256 == _sha((corpus / name).read_bytes())
    # A UTF-8 text file is read as itself: passages are located in the original document.
    assert docs["gegen"].text_sha256 == docs["gegen"].ref.sha256
    assert docs["gegen"].parser == "strict utf-8"
    # HTML is not text; its passages are located in what html2text made of it.
    assert docs["ctx"].text_sha256 != docs["ctx"].ref.sha256
    assert docs["ctx"].parser.startswith("html2text ")
    embedding = index.manifest["embedding"]
    assert (embedding["name"], embedding["local"], embedding["ndim"]) == ("sparse", True, 1024)
    assert index.manifest["implementation"]["embedding_class"].endswith(
        "SparseEmbeddingModel")
    assert index.manifest["implementation"]["versions"]["paper-qa"]
    assert index.manifest["sensitivity"] == "public"
    again = load_index(index.path, expected_sha256=index.manifest_sha256)
    assert again.manifest == index.manifest


def test_a_document_whose_bytes_differ_from_its_digest_is_refused(paperqa, tmp_path):
    path = tmp_path / "trial.txt"
    path.write_text(TEXTS["E3"], encoding="utf-8")
    stale = DocumentRef(path=str(path), sha256=_sha(b"what was authorised"))
    with pytest.raises(LiteratureRefused, match="authorised to read") as refused:
        build_index([stale], tmp_path / "index")
    assert refused.value.status is ExecutionStatus.DENIED
    assert not (tmp_path / "index").exists()


def test_an_index_that_changed_is_refused(index, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(index.path, copy)
    with pytest.raises(LiteratureRefused, match="expected") as pinned:
        load_index(copy, expected_sha256="0" * 64)
    assert pinned.value.status is ExecutionStatus.DENIED
    text = next((copy / "texts").iterdir())
    text.write_text(text.read_text(encoding="utf-8") + " (edited)", encoding="utf-8")
    with pytest.raises(LiteratureRefused, match="does not match its digest"):
        load_index(copy)


def test_an_index_is_written_only_into_an_empty_directory(paperqa, corpus, tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "something").write_text("x")
    with pytest.raises(LiteratureRefused, match="not empty"):
        build_index(refs_for(corpus), tmp_path / "old")


def test_text_that_is_not_utf8_is_refused_not_read_leniently(paperqa, tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes("Résumé of a randomized trial".encode("latin-1"))
    with pytest.raises(LiteratureRefused, match="not UTF-8"):
        build_index([DocumentRef(path=str(path), sha256=_sha(path.read_bytes()))],
                    tmp_path / "index")


def test_a_remote_embedding_is_refused_unless_the_run_permits_it(paperqa, corpus, tmp_path):
    refs = refs_for(corpus)
    with pytest.raises(LiteratureRefused, match="names no model profile"):
        build_index(refs, tmp_path / "a", embedding=EmbeddingConfig("text-embedding-3-small"))
    profile = ModelProfile(id="text-embedding-3-small", provider="openai",
                           destination=Destination.PUBLIC_REMOTE)
    remote = EmbeddingConfig("text-embedding-3-small", profile=profile)
    with pytest.raises(LiteratureRefused, match="does not permit destination PUBLIC_REMOTE"):
        build_index(refs, tmp_path / "b", embedding=remote, sensitivity="public",
                    envelope=RunEnvelope())
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------

def test_retrieval_returns_passages_located_in_their_source_text(index, corpus):
    result = retrieve(EMPEROR_Q, index, k=3)
    assert result.passages[0].doc_id == "emperor"
    assert result.method["model_calls"] == 0 and result.method["network"] is False
    scores = [p.score for p in result.passages]
    assert scores == sorted(scores, reverse=True) and all(0 < s <= 1 for s in scores)
    for p in result.passages:
        source = result.sources[p.document.text_sha256]
        assert source[p.offset:p.end] == p.text
        assert _sha(source.encode("utf-8")) == p.document.text_sha256
    emperor = passage_of(result, "emperor")
    original = (corpus / "emperor.txt").read_bytes()
    # For a text file the content the passage is located in is the file itself.
    assert result.sources[emperor.document.text_sha256].encode("utf-8") == original
    assert emperor.document.identifier == ("34449189", "pmid")


def test_a_chinese_question_finds_the_chinese_abstract(index):
    result = retrieve("葛根芩连汤 糖化血红蛋白", index, k=1)
    assert [p.doc_id for p in result.passages] == ["gegen"]


def test_filters_apply_before_ranking(index):
    only = retrieve(EMPEROR_Q, index, k=3, doc_ids=["gegen"])
    assert {p.doc_id for p in only.passages} <= {"gegen"}
    assert [d.doc_id for d in only.documents] == ["gegen"]
    assert retrieve(EMPEROR_Q, index, k=3, min_score=0.99).passages == ()
    with pytest.raises(ValueError, match="not in the index"):
        retrieve(EMPEROR_Q, index, doc_ids=["nope"])


def test_the_index_is_reused_from_its_directory(index):
    from_ref = retrieve(EMPEROR_Q, index, k=2)
    from_dir = retrieve(EMPEROR_Q, index.path, k=2)
    assert [(p.passage_id, p.score) for p in from_dir.passages] == [
        (p.passage_id, p.score) for p in from_ref.passages]
    assert from_dir.index_manifest_sha256 == index.manifest_sha256


def test_long_text_is_chunked_into_verbatim_passages_even_in_chinese(paperqa, tmp_path):
    from bioagent.literature import ChunkingConfig
    body = "".join(case["source"]["text"] for case in BASE_CASES
                   if case["language"] == "zh") * 2
    path = tmp_path / "long.txt"
    path.write_text(body, encoding="utf-8")
    built = build_index([DocumentRef(path=str(path), sha256=_sha(path.read_bytes()))],
                        tmp_path / "index",
                        chunking=ChunkingConfig(chunk_chars=200, overlap=40))
    (doc,) = built.documents
    assert doc.chunks > 5
    result = retrieve("马兜铃酸 终末期肾病", built, k=doc.chunks)
    assert result.passages
    for p in result.passages:
        assert "\ufffd" not in p.text
        assert body[p.offset:p.end] == p.text


def test_a_crlf_text_file_keeps_its_bytes_and_its_offsets(paperqa, tmp_path):
    body = TEXTS["A1"].replace(", ", ",\r\n")
    path = tmp_path / "crlf.txt"
    path.write_bytes(body.encode("utf-8"))
    built = build_index([DocumentRef(path=str(path), sha256=_sha(path.read_bytes()))],
                        tmp_path / "index")
    (doc,) = built.documents
    assert doc.text_sha256 == doc.ref.sha256
    (p,) = retrieve("C-telopeptide mortality", built.path, k=1).passages
    assert "\r\n" in p.text and body[p.offset:p.end] == p.text


def test_a_pdf_is_read_by_paperqas_parser_and_located_in_its_text(paperqa, tmp_path):
    lines = ["In a prospective cohort of 4,812 adults aged 65 or older,",
             "higher serum C-telopeptide was associated with all-cause",
             "mortality (hazard ratio 1.33 per standard deviation)."]
    path = tmp_path / "cohort.pdf"
    path.write_bytes(pdf_bytes(lines))
    built = build_index([DocumentRef(path=str(path), sha256=_sha(path.read_bytes()),
                                     doi="10.5555/tcm-ablation.05")], tmp_path / "index")
    (doc,) = built.documents
    assert doc.ref.media_type == "application/pdf" and "pypdf" in doc.parser
    result = retrieve("C-telopeptide mortality", built, k=1)
    (p,) = result.passages
    typed = to_evidence(p, result.sources[doc.text_sha256], source_card_id="c")
    assert typed.item is not None and typed.item.design == "observational"
    # The PDF breaks lines inside the phrase; the reading keeps the span, collapses the
    # whitespace in the value.
    assert typed.item.population == "adults aged 65 or older"
    assert typed.item.outcome == "all-cause mortality"
    assert "\n" in typed.readings["outcome"].span
    assert typed.item.verify_receipt(result.sources[doc.text_sha256])


# ---------------------------------------------------------------------------
# typing: located, typed by rule, unknown left unknown
# ---------------------------------------------------------------------------

def test_a_typed_passage_becomes_a_located_evidence_item(index, corpus):
    result = retrieve("葛根芩连汤 糖化血红蛋白", index, k=1)
    p = passage_of(result, "gegen")
    source = result.sources[p.document.text_sha256]
    typed = to_evidence(p, source, source_card_id="literature.gegen", retrieved_by="t",
                        retrieval_run="r1")
    item = typed.item
    assert item is not None and typed.withheld == ""
    assert item.design == "randomized_trial"
    assert (item.identifier, item.identifier_type) == ("10.5555/tcm-ablation.01", "doi")
    assert item.content_hash == _sha((corpus / "gegen.txt").read_bytes())
    assert item.quote == p.text and item.quote_offset == p.offset
    assert item.has_quote_receipt and item.verify_receipt(source)
    assert (item.population, item.comparator, item.outcome) == (
        "成人2型糖尿病患者", "安慰剂", "糖化血红蛋白")
    assert item.retracted == "unverified"
    assert item.quality.assessed == ()
    assert "read by rule design.trial.zh" in item.notes
    assert default_store.get(item.content_hash) == source


def test_unknown_fields_stay_unknown(index):
    result = retrieve(EMPEROR_Q, index, k=1, doc_ids=["emperor"])
    (passage,) = result.passages
    emperor = to_evidence(passage, result.sources[passage.document.text_sha256],
                          source_card_id="literature.emperor")
    # No rule reads a design from the EMPEROR sentence, so no EvidenceItem is made: the
    # passage is reported, located, and withheld rather than given a guessed design.
    assert emperor.item is None
    assert "no study design" in emperor.withheld and "none is guessed" in emperor.withheld
    assert not emperor.readings["design"].assessed
    assert emperor.as_dict()["fields"]["design"]["status"] == "not_assessed"
    # What a rule did read is still reported for the reviewer who types it.
    assert emperor.readings["comparator"].value == "placebo"

    result = retrieve("C-telopeptide mortality", index, k=1, doc_ids=["ctx"])
    (cohort,) = result.passages
    typed = to_evidence(cohort, result.sources[cohort.document.text_sha256],
                        source_card_id="literature.ctx")
    item = typed.item
    assert item.design == "observational"
    assert not typed.readings["comparator"].assessed and item.comparator == ""
    assert "comparator not assessed (no rule matched)" in item.notes
    assert (item.year, item.sample_size, item.subject) == (0, 0, "")
    assert item.retracted == "unverified" and item.quality.assessed == ()


@pytest.mark.parametrize("text,design", [
    ("In this non-randomized study of adults with asthma, inhaler use fell.", ""),
    ("这是一项未随机分组的观察。", ""),
    # two designs, each the text's own: no answer rather than either
    ("We report a randomized trial and a meta-analysis of earlier trials in adults.", ""),
    ("采用随机抽样的方法调查了社区居民。", ""),
    # a protocol reports no results, whatever design it plans
    ("Study protocol for a randomized controlled trial of acupuncture in adults.", ""),
    ("Participants will be randomized to acupuncture or sham acupuncture.", ""),
    # a random split of data is not a trial's allocation
    ("将患者按照7∶3随机分为训练集和验证集，建立预后模型。", ""),
    ("We performed a cross-sectional survey of 300 nurses.", "observational"),
    ("葛根芩连汤灌胃给药2型糖尿病大鼠12周。", "animal"),
    ("We report a case of hepatotoxicity after a herbal product.", "case_report"),
    # the trials a review pools, or an introduction cites, are not the study's own design
    ("A systematic review and meta-analysis of randomized trials in adults.",
     "systematic_review"),
    ("Randomized trials are few; this prospective cohort study followed 900 adults.",
     "observational"),
    ("Women undergoing in vitro fertilization were followed in a prospective cohort.",
     "observational"),
])
def test_the_design_rules_prefer_no_answer_to_a_wrong_one(text, design):
    reading = read_fields(text)["design"]
    assert reading.value == design
    if reading.assessed:
        assert text[reading.offset:reading.offset + len(reading.span)] == reading.span
    else:
        assert reading.note


def test_a_passage_given_other_content_is_refused():
    doc = IndexedDocument(ref=DocumentRef(path="a.txt", sha256=_sha(b"abc")), bytes=3,
                          parser="strict utf-8", text_sha256=_sha(b"abc"), text_chars=3,
                          chunks=1)
    with pytest.raises(ValueError, match="not the text it was retrieved from"):
        to_evidence(Passage(doc, "abc", 0, 0.5, 1), "abd", source_card_id="c")


# ---------------------------------------------------------------------------
# synthesis: refused without a permitted model, never evidence when it runs
# ---------------------------------------------------------------------------

REMOTE = ModelProfile(id="example-model", provider="example",
                      destination=Destination.PUBLIC_REMOTE, max_label=Sensitivity.PUBLIC)
OPEN_RUN = RunEnvelope(allowed_destinations=frozenset({Destination.LOCAL_COMPUTE,
                                                       Destination.PUBLIC_REMOTE}))


def test_synthesis_is_refused_without_a_model(index):
    answer = synthesise(retrieve(EMPEROR_Q, index, k=2))
    assert answer.status is ExecutionStatus.UNAVAILABLE
    assert "no model is configured" in answer.reason
    assert answer.text == "" and answer.citations == ()
    assert answer.as_dict()["is_evidence"] is False


def test_synthesis_is_refused_when_the_run_does_not_permit_the_model(index):
    result = retrieve(EMPEROR_Q, index, k=2)
    no_remote = synthesise(result, model=REMOTE, envelope=RunEnvelope())
    assert no_remote.status is ExecutionStatus.DENIED
    assert "does not permit destination PUBLIC_REMOTE" in no_remote.reason
    unclassified = synthesise(dataclasses.replace(result, sensitivity=None), model=REMOTE,
                              envelope=OPEN_RUN)
    assert unclassified.status is ExecutionStatus.DENIED
    assert "held at the run's ceiling" in unclassified.reason
    poor = dataclasses.replace(OPEN_RUN, budget=Budget(tokens_hard=100))
    assert "hard budget" in synthesise(result, model=REMOTE, envelope=poor).reason
    assert synthesise(result, model=REMOTE).status is ExecutionStatus.DENIED
    nothing = synthesise(dataclasses.replace(result, passages=()), model=REMOTE,
                         envelope=OPEN_RUN)
    assert nothing.status is ExecutionStatus.DENIED and "over nothing" in nothing.reason


class _Model:
    """Stands in for the model paper-qa calls, and keeps what it was sent."""

    def __init__(self, fail: bool = False) -> None:
        self.fail, self.messages = fail, []

    async def call_single(self, messages, callbacks=None, name=None, **kwargs):
        from lmi import LLMResult
        self.messages.append(messages)
        if self.fail:
            raise RuntimeError("provider unavailable")
        valid = re.search(r"Valid Keys: (.*)", messages[-1].content).group(1).split(", ")
        return LLMResult(model="stand-in", prompt_count=200, completion_count=30, text=(
            f"Empagliflozin reduced the composite outcome ({valid[0]}). Another source said "
            "otherwise (pqac-deadbeef).\n\nConflicts: none"))


def test_a_permitted_synthesis_is_a_candidate_answer_not_evidence(index):
    result = retrieve(EMPEROR_Q, index, k=2)
    model = _Model()
    answer = synthesise(result, model=REMOTE, envelope=OPEN_RUN, llm=model)
    assert answer.status is ExecutionStatus.SUCCEEDED, answer.reason
    assert len(answer.citations) == 1
    assert set(answer.citations) <= {p.passage_id for p in result.passages}
    # A key the model invented is reported, not silently dropped or trusted.
    assert answer.unsupported_citations == ("pqac-deadbeef",)
    assert answer.conflicts_stated and answer.conflicts == ()
    assert answer.as_dict()["kind"] == "candidate_explanation"
    assert answer.as_dict()["is_evidence"] is False
    (system, user), = model.messages
    assert "Conflicts:" in system.content
    # paper-qa strips the whitespace around a context; the words are the passage's own.
    assert all(p.text.strip() in user.content for p in result.passages)


def test_a_failed_model_call_is_reported_as_failed(index):
    answer = synthesise(retrieve(EMPEROR_Q, index, k=1), model=REMOTE, envelope=OPEN_RUN,
                        llm=_Model(fail=True))
    assert answer.status is ExecutionStatus.FAILED
    assert "provider unavailable" in answer.reason


def test_an_endpoint_is_for_a_model_on_this_machine(index):
    """LiteLLM would send the environment's provider key to any endpoint it is given, and
    a profile is judged by the destination it declares; so ``api_base`` is this machine or
    nothing, and nothing is sent when it is not."""
    from bioagent.literature.paperqa import _loopback

    local = ModelProfile(id="openai/local-model", provider="llama.cpp",
                         destination=Destination.LOCAL_MODEL, max_label=Sensitivity.SENSITIVE)
    result = retrieve(EMPEROR_Q, index, k=1)
    model = _Model()
    for elsewhere in ("http://10.0.0.7:8080/v1", "https://models.example.org/v1"):
        refused = synthesise(result, model=local, envelope=OPEN_RUN, api_base=elsewhere,
                             llm=model)
        assert refused.status is ExecutionStatus.DENIED and "this machine" in refused.reason
    assert model.messages == []
    assert all(_loopback(u) for u in ("http://127.0.0.1:8080/v1", "http://localhost:1/v1",
                                      "http://[::1]:8080/v1"))
    assert not any(_loopback(u) for u in ("http://127.0.0.1.example.org/v1", "http://h/v1"))


def _local_model() -> tuple[str, str]:
    url = os.environ.get("BIOAGENT_LOCAL_LLM_URL", "")
    name = os.environ.get("BIOAGENT_LOCAL_LLM_MODEL", "")
    if not (url and name):
        why = ("no local model: set BIOAGENT_LOCAL_LLM_URL (an OpenAI-compatible endpoint on "
               "this machine) and BIOAGENT_LOCAL_LLM_MODEL (the name it serves)")
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(why)
        pytest.skip(why)
    return url, name


def test_a_local_model_writes_a_candidate_answer_through_paperqa(index):
    """A real model through paper-qa's answer step: the passages go in, a candidate answer
    comes out, and its citations are read against the passages it was given."""
    url, name = _local_model()
    local = ModelProfile(id=f"openai/{name}", provider="local",
                         destination=Destination.LOCAL_MODEL, max_label=Sensitivity.SENSITIVE)
    run = RunEnvelope(allowed_destinations=frozenset({Destination.LOCAL_MODEL}))
    result = retrieve(EMPEROR_Q, index, k=2)
    answer = synthesise(result, model=local, envelope=run, api_base=url,
                        max_answer_tokens=256)
    assert answer.status is ExecutionStatus.SUCCEEDED, answer.reason
    assert answer.text.strip() and answer.destination == "local_model"
    assert set(answer.citations) <= {p.passage_id for p in result.passages}
    assert answer.tokens and answer.as_dict()["is_evidence"] is False
    remote = dataclasses.replace(local, destination=Destination.PUBLIC_REMOTE)
    assert synthesise(result, model=remote, envelope=run, api_base=url).status is \
        ExecutionStatus.DENIED, "the run permits the local model only"


def test_the_model_gate_reports_every_reason():
    tight = RunEnvelope(budget=Budget(max_model_calls=0, tokens_hard=10, usd_hard=0.0))
    refused = permit_model(REMOTE, tight, "sensitive", tokens=1000, out_tokens=100)
    assert refused.status is ExecutionStatus.DENIED
    for reason in ("does not permit destination", "accepts at most PUBLIC",
                   "may not go to PUBLIC_REMOTE", "exceed the budget", "hard budget"):
        assert reason in refused.reason
    allowed = permit_model(REMOTE, OPEN_RUN, "public", tokens=1000, out_tokens=100)
    assert allowed.allowed and allowed.status is ExecutionStatus.READY
    assert allowed.usd == pytest.approx(REMOTE.estimated_usd(1000, 100))
    assert permit_model(None, OPEN_RUN, "public", tokens=1).status is (
        ExecutionStatus.UNAVAILABLE)
    assert not DataLabel(Sensitivity.SENSITIVE).permits(Destination.PUBLIC_REMOTE)


# ---------------------------------------------------------------------------
# no network on the retrieval path
# ---------------------------------------------------------------------------

_NO_NETWORK = r'''
import hashlib, json, pathlib, socket, sys
attempts = []
def refuse(*args, **kwargs):
    attempts.append(repr(args[:2])[:100])
    raise OSError("network refused by the test")
socket.socket.connect = socket.socket.connect_ex = refuse
socket.create_connection = socket.getaddrinfo = refuse
from bioagent.literature import DocumentRef, build_index, retrieve, to_evidence
root = pathlib.Path(sys.argv[1])
refs = [DocumentRef(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.glob("*.txt"))]
index = build_index(refs, root / "index")
result = retrieve(sys.argv[2], index, k=3)
typed = [to_evidence(p, result.sources[p.document.text_sha256], source_card_id="c")
         for p in result.passages]
during = list(attempts)
try:
    socket.create_connection(("192.0.2.1", 9))
except OSError:
    pass
print(json.dumps({"during": during, "guard_works": len(attempts) == len(during) + 1,
                  "passages": len(result.passages),
                  "items": sum(t.item is not None for t in typed)}))
'''


def _subprocess_env(**extra: str) -> dict[str, str]:
    import psh
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    paths = [str(Path(bioagent.__file__).resolve().parents[1]),
             str(Path(psh.__file__).resolve().parents[1])]
    env["PYTHONPATH"] = os.pathsep.join(paths + [env.get("PYTHONPATH", "")])
    return {**env, **extra}


def test_indexing_and_retrieval_open_no_socket(paperqa, tmp_path):
    corpus = write_corpus(tmp_path / "corpus", files={
        k: v for k, v in CORPUS.items() if k.endswith(".txt")})
    script = tmp_path / "no_network.py"
    script.write_text(_NO_NETWORK, encoding="utf-8")
    run = subprocess.run([sys.executable, str(script), str(corpus), EMPEROR_Q],
                         capture_output=True, text=True, timeout=600, cwd=tmp_path,
                         env=_subprocess_env())
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["guard_works"], "the socket guard did not intercept a deliberate connection"
    assert out["during"] == [], f"the literature path tried the network: {out['during']}"
    assert out["passages"] >= 1 and out["items"] >= 1


def test_a_request_for_litellms_remote_cost_map_is_refused(paperqa, tmp_path):
    script = tmp_path / "cost_map.py"
    script.write_text(
        "from bioagent.literature.paperqa import _paperqa\n"
        "from bioagent.literature import LiteratureRefused\n"
        "try:\n    _paperqa()\nexcept LiteratureRefused as e:\n"
        "    print(e.status.value, e.reason)\n", encoding="utf-8")
    run = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                         timeout=120, cwd=tmp_path,
                         env=_subprocess_env(LITELLM_LOCAL_MODEL_COST_MAP="False"))
    assert run.stdout.startswith("DENIED"), run.stdout + run.stderr[-1000:]
    assert "makes no network request" in run.stdout


# ---------------------------------------------------------------------------
# the candidate skill
# ---------------------------------------------------------------------------

def test_the_manifest_names_the_registered_callable():
    from bioagent.governed import candidate_callables
    from bioagent.skills.loader import load_skill_dir

    skill = load_skill_dir(CANDIDATES / "retrieve-literature-evidence",
                           require_implementation=True)
    fn = candidate_callables()["retrieve-literature-evidence"]
    assert skill.spec.runtime.entrypoint == f"{fn.__module__}:{fn.__qualname__}"
    assert skill.spec.permissions.network == () and skill.spec.resources.expected_tokens == 0


def test_the_corpus_manifest_is_read_strictly(tmp_path):
    from bioagent.skills.literature.retrieve import read_corpus

    root = write_corpus(tmp_path / "corpus")
    data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for broken, match in (
            ({**data["documents"][0], "sah256": "x"}, "unknown key"),
            ({**data["documents"][0], "file": "../outside.txt"}, "outside the corpus"),
            ({k: v for k, v in data["documents"][0].items() if k != "sha256"}, "sha256")):
        (root / "manifest.json").write_text(json.dumps({"documents": [broken]}),
                                            encoding="utf-8")
        with pytest.raises(ValueError, match=match):
            read_corpus(root)


def test_a_candidate_is_refused_until_it_is_promoted(corpus, tmp_path):
    from bioagent.governed import GovernedRunRefused, run_governed
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("retrieve-literature-evidence",
                     {"question": EMPEROR_Q, "corpus": str(corpus)},
                     skill_dir=CANDIDATES, state_dir=tmp_path / "psh")


@pytest.fixture(scope="module")
def dev_run(paperqa, corpus, tmp_path_factory):
    from bioagent.governed import run_governed
    tmp = tmp_path_factory.mktemp("skill-run")
    return run_governed("retrieve-literature-evidence",
                        {"question": "empagliflozin heart failure C-telopeptide 糖化血红蛋白",
                         "corpus": str(corpus), "k": 3, "index_dir": str(tmp / "index")},
                        skill_dir=CANDIDATES, state_dir=tmp / "psh", output_dir=tmp / "out",
                        allow_unpinned=True)


def test_a_development_run_is_valid_and_its_evidence_verifies(dev_run):
    art, verdict = dev_run.artifact, dev_run.verdict
    assert verdict.publishable, verdict.codes
    assert not dev_run.released and not verdict.execution_attested
    assert art.claims == ()
    assert {e.design for e in art.evidence} == {"randomized_trial", "observational"}
    for item in art.evidence:
        assert item.has_quote_receipt
        assert item.verify_receipt(default_store.get(item.content_hash))
        assert item.source_card_id in {s.id for s in art.sources}
    assert all(s.pinned for s in art.sources) and len(art.sources) == 3
    assert art.provenance["withheld"] == 1 and art.provenance["evidence_items"] == 2
    assert art.provenance["retrieval"]["model_calls"] == 0
    out = Path(dev_run.output_dir)
    report = json.loads((out / "literature_evidence.json").read_text(encoding="utf-8"))
    (withheld,) = [p for p in report["passages"] if p["evidence_id"] is None]
    assert withheld["doc_id"] == "emperor" and "none is guessed" in withheld["withheld"]
    manifest = (out / "index_manifest.json").read_bytes()
    assert _sha(manifest) == art.provenance["index_manifest_sha256"]


def test_claims_resting_on_the_evidence_are_checked_receipts_and_all(dev_run):
    art = dev_run.artifact
    cohort = next(e for e in art.evidence if e.design == "observational")
    trial = next(e for e in art.evidence if e.design == "randomized_trial")
    association = CandidateClaim(
        id="c.ctx", text="Higher serum C-telopeptide was associated with all-cause "
                         "mortality in adults aged 65 or older",
        claim_kind="association", subject="serum C-telopeptide", predicate="associated_with",
        object="all-cause mortality", supports=(cohort.id,),
        asserted_population=cohort.population, supported_population=cohort.population,
        asserted_outcome=cohort.outcome, supported_outcome=cohort.outcome,
        direction="increase", hedged=True, falsified_by="a cohort that finds no association")
    with_claim = dataclasses.replace(art, claims=(association,))
    verdict = validate_artifact(with_claim, output_root=dev_run.output_dir,
                                content_store=default_store)
    assert verdict.publishable and verdict.evidence_verified, verdict.codes
    # The receipt is checked, not trusted: an offset moved by one character fails it.
    shifted = dataclasses.replace(cohort, quote_offset=cohort.quote_offset + 1)
    moved = dataclasses.replace(with_claim, evidence=tuple(
        shifted if e.id == cohort.id else e for e in art.evidence))
    assert "ART115" in validate_artifact(moved, content_store=default_store).codes
    # Nobody assessed the trial's risk of bias, so it cannot carry an efficacy claim yet.
    efficacy = CandidateClaim(
        id="c.gegen", text="葛根芩连汤降低成人2型糖尿病患者的糖化血红蛋白", claim_kind="efficacy",
        subject="葛根芩连汤", predicate="decreases", object="糖化血红蛋白", supports=(trial.id,),
        asserted_population=trial.population, supported_population=trial.population,
        asserted_outcome=trial.outcome, supported_outcome=trial.outcome,
        direction="decrease", falsified_by="a replication that finds no difference")
    assert "CLM006" in check_claim(efficacy, {trial.id: trial}).codes


def test_an_index_of_another_corpus_is_not_reused(paperqa, dev_run, tmp_path):
    from bioagent.skills.literature import retrieve_literature_evidence
    other = write_corpus(tmp_path / "other", files={"gegen.txt": CORPUS["gegen.txt"]})
    with pytest.raises(LiteratureRefused, match="different corpus") as refused:
        retrieve_literature_evidence("葛根芩连汤", str(other),
                                     index_dir=dev_run.artifact.provenance["index_dir"])
    assert refused.value.status is ExecutionStatus.DENIED
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "notes.txt").write_text("not an index")
    with pytest.raises(LiteratureRefused, match="not empty") as busy:
        retrieve_literature_evidence("葛根芩连汤", str(other), index_dir=str(tmp_path / "busy"))
    assert busy.value.status is ExecutionStatus.DENIED
