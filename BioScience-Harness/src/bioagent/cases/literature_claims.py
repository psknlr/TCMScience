"""Case 2: documents → PaperQA2 retrieval → located, typed evidence → which claims stand.

The corpus is three abstracts from the governance ablation (two fixtures under the reserved
DOI prefix 10.5555 that describe no real study, and the EMPEROR-Preserved result sentence,
PMID 34449189). They are indexed and searched with PaperQA2 and a local embedding, with no
model and no network (``bioagent.literature``). Each retrieved passage is located in its
document and typed by fixed rules. A passage whose design no rule can read is withheld, and
nothing is filled in for it.

Then the case does what a team does with retrieved evidence:

* drafts claims an agent might write over the passages;
* records a reviewer's assessment of the trial's risk of bias, because no rule can assess
  it and an unassessed risk of bias blocks an efficacy claim;
* puts every draft through the claim contract.

The reviewer's assessment is part of the case's fixture: a stated human input, named as
one, never something the pipeline produces.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from ..status import ExecutionStatus
from .report import CaseReport, CaseStep, _versions, check_drafts

__all__ = ["CORPUS", "QUESTIONS", "run_case"]

#: The questions the case asks of its corpus. Retrieval is lexical, so they name the terms.
QUESTIONS = ("葛根芩连汤 糖化血红蛋白 成人2型糖尿病患者",
             "C-telopeptide all-cause mortality adults aged 65 or older")


def _texts() -> dict[str, str]:
    from ..benchmarks.ablation_corpus import BASE_CASES
    return {case["id"]: case["source"]["text"] for case in BASE_CASES}


#: file -> (ablation case whose source text it holds, what the corpus manifest declares)
CORPUS = {
    "gegen.txt": ("E1", {"doc_id": "gegen", "doi": "10.5555/tcm-ablation.01",
                         "citation": "Example trial of 葛根芩连汤 (fixture, no real study)",
                         "year": 2024, "license_spdx": "CC0-1.0"}),
    "emperor.txt": ("E2", {"doc_id": "emperor", "pmid": "34449189",
                           "citation": "EMPEROR-Preserved, results sentence"}),
    "ctx.txt": ("A1", {"doc_id": "ctx", "doi": "10.5555/tcm-ablation.05",
                       "citation": "Example cohort (fixture, no real study)",
                       "license_spdx": "CC0-1.0"}),
}


def _write_corpus(root: Path) -> list:
    from ..literature import DocumentRef

    texts = _texts()
    root.mkdir(parents=True, exist_ok=True)
    refs, entries = [], []
    for name, (case_id, meta) in CORPUS.items():
        data = texts[case_id].encode("utf-8")
        (root / name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        entries.append({"file": name, "sha256": digest, **meta})
        refs.append(DocumentRef(path=str(root / name), sha256=digest,
                                **{k: v for k, v in meta.items()
                                   if k in ("doc_id", "doi", "pmid", "citation", "year")}))
    (root / "manifest.json").write_text(json.dumps({"sensitivity": "public",
                                                    "documents": entries},
                                                   ensure_ascii=False), encoding="utf-8")
    return refs


def run_case(work_dir: str | Path) -> CaseReport:
    from ..contracts import CandidateClaim, EvidenceQuality
    from ..contracts.quality import RiskOfBias
    from ..literature import (LiteratureRefused, build_index, evidence_id, retrieve,
                              to_evidence)
    from ..omics.optional import BackendUnavailable

    work = Path(work_dir)
    report = CaseReport(
        case="literature-claims", title="Literature to claims: PaperQA2 retrieval, typed "
        "evidence, the claim contract",
        inputs="three abstracts from the governance ablation corpus: two fixtures under "
               "the reserved DOI prefix 10.5555 (no real study) and the EMPEROR-Preserved "
               "result sentence; one reviewer assessment, stated in the case")
    refs = _write_corpus(work / "corpus")
    versions = _versions("paper-qa", "tiktoken")
    implementation = " ".join(f"{k} {v}" for k, v in versions.items())

    try:
        index = build_index(refs, work / "index", sensitivity="public")
    except (BackendUnavailable, LiteratureRefused, ImportError) as exc:
        status = getattr(exc, "status", ExecutionStatus.UNAVAILABLE)
        report.steps.append(CaseStep("index the corpus", status, implementation,
                                     f"{type(exc).__name__}: {exc}"[:300]))
        report.limits.append("nothing past indexing ran")
        return report
    report.steps.append(CaseStep(
        "index the corpus", ExecutionStatus.SUCCEEDED, implementation,
        f"{len(refs)} documents, each read only after its digest matched; a local sparse "
        "embedding, no model and no network",
        {"index_manifest_sha256": index.manifest_sha256}))

    found: dict[str, tuple] = {}
    for question in QUESTIONS:
        result = retrieve(question, index, k=5)
        for p in result.passages:
            found.setdefault(evidence_id(p), (p, result.sources[p.document.text_sha256]))
    report.steps.append(CaseStep(
        "retrieve passages", ExecutionStatus.SUCCEEDED, implementation,
        f"{len(found)} passages for {len(QUESTIONS)} questions, each located at its offset",
        {"questions": list(QUESTIONS),
         "passages": [{"doc": p.doc_id, "offset": p.offset, "score": round(p.score, 4)}
                      for p, _ in found.values()]}))

    typed = [to_evidence(p, content, source_card_id=f"literature.{p.doc_id}")
             for p, content in found.values()]
    items = {t.item.id: t.item for t in typed if t.item is not None}
    withheld = [t for t in typed if t.item is None]
    report.steps.append(CaseStep(
        "type the passages", ExecutionStatus.SUCCEEDED, "bioagent.literature rules",
        f"{len(items)} typed as evidence items, {len(withheld)} withheld because no rule "
        "read their design",
        {"items": {i.id: {"design": i.design, "population": i.population,
                          "outcome": i.outcome} for i in items.values()},
         "withheld": [{"doc": t.passage.doc_id, "reason": t.withheld} for t in withheld]}))

    trial = next((i for i in items.values() if i.design == "randomized_trial"), None)
    # The id the withheld EMPEROR passage would carry, had a rule read its design.
    unread = next((evidence_id(t.passage) for t in withheld
                   if t.passage.doc_id == "emperor"), "lit.emperor")
    cohort = next((i for i in items.values() if i.design == "observational"), None)
    if trial is None:
        report.steps.append(CaseStep("reviewer assessment", ExecutionStatus.FAILED, "",
                                     "no passage was typed as a trial"))
        return report
    reviewed = replace(trial, id=f"{trial.id}.reviewed", quality=EvidenceQuality(
        risk_of_bias=RiskOfBias.LOW, assessed_by="reviewer (stated in the case fixture)",
        assessment_tool="RoB 2, as the reviewer reports it",
        rationale={"risk_of_bias": "randomised, double-blind, placebo-controlled; "
                                   "the abstract reports no attrition"}))
    evidence = {**items, reviewed.id: reviewed}
    report.steps.append(CaseStep(
        "reviewer assessment", ExecutionStatus.SUCCEEDED, "a person (case fixture)",
        "risk of bias of the trial assessed LOW by a named reviewer; no rule assesses it",
        {"item": reviewed.id}))

    def claim(text, kind, supports, population="成人2型糖尿病患者", outcome="糖化血红蛋白",
              subject="葛根芩连汤"):
        return CandidateClaim(
            id=f"c{abs(hash(text)) % 10**6}", text=text, claim_kind=kind, subject=subject,
            supports=tuple(supports), asserted_population=population,
            supported_population=population, asserted_outcome=outcome,
            supported_outcome=outcome, confidence=0.6,
            confidence_basis="one randomised trial",
            falsified_by="a larger trial that finds no difference in HbA1c")

    faithful = "葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。"
    drafts = [
        (claim(faithful, "efficacy", [trial.id]),
         "the faithful claim, before anyone has assessed the trial's risk of bias", False),
        (claim(faithful, "efficacy", [reviewed.id]),
         "the same claim after the reviewer's assessment", True),
        (claim("葛根芩连汤可降低儿童2型糖尿病患者的糖化血红蛋白。", "efficacy", [reviewed.id],
               population="儿童2型糖尿病患者"),
         "a population the trial did not enrol", False),
        (claim("葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白，且未见明显不良反应。", "efficacy",
               [reviewed.id]),
         "an outcome the abstract does not report, stated as absent", False),
        (claim("Empagliflozin reduced hospitalization for heart failure.", "efficacy",
               [unread], population="patients with heart failure",
               outcome="hospitalization for heart failure", subject="empagliflozin"),
         "a claim on the passage that was withheld: it is no evidence item until a person "
         "types its design", False),
    ]
    if cohort is not None:
        drafts.append((claim(
            "Higher serum C-telopeptide was associated with all-cause mortality in adults "
            "aged 65 or older.", "association", [cohort.id],
            population=cohort.population or "adults aged 65 or older",
            outcome=cohort.outcome or "all-cause mortality", subject="serum C-telopeptide"),
            "an association over the cohort, which an unassessed risk of bias does not "
            "block", True))
    report.claims = check_drafts(drafts, evidence)
    report.limits += [
        "Retrieval is lexical (a hashed bag of tokens): a passage that says the same thing "
        "in other words can be missed.",
        "The typing rules were tested on fixtures, not on a sample of real literature.",
        "No model wrote these claims; they are drafts the case states, so the case tests "
        "the contract on retrieved evidence, not an agent's writing.",
        "The reviewer's assessment is a stated input, not a judgement this case makes.",
    ]
    return report
