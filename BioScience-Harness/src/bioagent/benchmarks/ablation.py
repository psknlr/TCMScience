"""Governance ablation: which part of the stack catches which scientific error, at what cost.

The claim that a governed architecture makes an agent's science more reliable is a claim
about counterfactuals: the same outputs, with and without each part of the governance.
This module measures it the way mutation testing measures a test suite.

* **Base cases** (``ablation_corpus.BASE_CASES``) are outputs a careful reviewer would let
  through: a statement, its structured claim, and the source it cites, in Chinese and
  English, over trials, cohorts, a case report, a bench assay, a docking run and a passage
  of the 伤寒论.
* **Mutation operators** (``MUTATIONS``) each turn a base case into an output with one known
  scientific error: a claim carried to a population the study did not enrol, a docking
  score cited for efficacy, a near-name herb, a dose read ten times too high, a retracted or
  tampered record, a citation to another paper, a plan clause hiding a claim of cure.
* **Gates** (``GATES``) are the real checks, called as the runtime calls them. Three are the
  trusted kernel's: record ingest (``ingest_evidence``: identifier binding, and the
  signature re-checked so a tampered record is marked), the output gate (``OutputGate``:
  support, scope, citations, plan clauses), and the licensing type check
  (``psh.sir.licenses``, the compiler's TYP rules, on the structured claim). Two are the
  domain layer's: the claim contract (``check_claim``, CLM codes, including the TCM
  near-name check) and the release check that walks snapshot edges (``check_release``).
* **Configurations** switch gates off: none, each layer alone, everything, everything but
  one, one alone. An output is released when no enabled gate refuses it.

Each gate runs once per case and each configuration is read off the results, so the
configurations differ in nothing but the gates. The one dependency is kept as the runtime
has it: with ingest on, the output gate sees the records ingest re-checked; with ingest off
it sees them as supplied, and a record edited after signing looks intact to it.

What it reports: the share of mutants each configuration releases (with a Wilson 95%
interval), the share of base cases it refuses, which gate catches which error class, which
mutants only one gate catches, and which survive the full stack.

What it is not. The base cases and the operators were written by the people who wrote the
gates. An error class nobody thought to generate is not measured. The class balance is set
by how many operators apply to how many cases, not by how often each error occurs in real
agent output. So the numbers are **a regression benchmark with a stated construction**, not
an estimate of field error rates. Bring real drafts (``load_drafts``) to measure those: the
same gates and configurations run on any labelled set of outputs, such as one model's
answers to the same tasks with and without the governance in the loop.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import tempfile
import types
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = ["CONFIGURATIONS", "DOMAIN_GATES", "GATES", "KERNEL_GATES", "MUTATIONS", "Case",
           "GateResult", "Mutation", "Source", "AblationReport", "base_cases",
           "load_drafts", "mutants", "render_markdown", "run_ablation"]


# ------------------------------------------------------------------ the cases
@dataclass(frozen=True)
class Source:
    identifier: str
    design: str
    text: str
    subject: str
    population: str = ""
    outcome: str = ""
    entities: tuple[str, ...] = ()
    dose: str = ""
    retracted: bool = False
    tampered: bool = False        # signed as retracted, then edited to read otherwise

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Source":
        return cls(identifier=str(data["identifier"]), design=str(data["design"]),
                   text=str(data["text"]), subject=str(data.get("subject") or ""),
                   population=str(data.get("population") or ""),
                   outcome=str(data.get("outcome") or ""),
                   entities=tuple(data.get("entities") or ()),
                   dose=str(data.get("dose") or ""),
                   retracted=bool(data.get("retracted")),
                   tampered=bool(data.get("tampered")))


@dataclass(frozen=True)
class Case:
    """One output an agent might release, with what it cites and whether it should go out."""

    id: str
    language: str
    claim_kind: str
    statement: str
    citation: str                  # how the statement cites its source, verbatim
    subject: str
    source: Source
    population: str = ""
    outcome: str = ""
    certainty: str = "strong"
    direction: str = ""
    entities: tuple[str, ...] = ()
    dose: str = ""
    path: tuple[str, ...] = ()     # snapshot records a mechanism claim rests on
    path_subject: str = ""
    path_object: str = ""
    cited_as: str = ""             # the key the record is supplied under ("" = citation)
    supplied: bool = True          # whether any record is supplied for the citation
    duplicates: int = 1            # how many evidence items carry the one cited study
    error_class: str = ""          # "" for a base case
    base: str = ""
    gold: str = "release"          # release | refuse
    material: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def key(self) -> str:
        return self.cited_as or self.citation

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Case":
        return cls(id=str(data["id"]), language=str(data.get("language") or "en"),
                   claim_kind=str(data["claim_kind"]), statement=str(data["statement"]),
                   citation=str(data.get("citation") or ""),
                   subject=str(data.get("subject") or ""),
                   source=Source.from_dict(data["source"]),
                   population=str(data.get("population") or ""),
                   outcome=str(data.get("outcome") or ""),
                   certainty=str(data.get("certainty") or "strong"),
                   direction=str(data.get("direction") or ""),
                   entities=tuple(data.get("entities") or ()),
                   dose=str(data.get("dose") or ""), path=tuple(data.get("path") or ()),
                   path_subject=str(data.get("path_subject") or ""),
                   path_object=str(data.get("path_object") or ""),
                   cited_as=str(data.get("cited_as") or ""),
                   supplied=bool(data.get("supplied", True)),
                   duplicates=int(data.get("duplicates") or 1),
                   error_class=str(data.get("error_class") or ""),
                   base=str(data.get("base") or ""),
                   gold=str(data.get("gold") or "release"),
                   material=dict(data.get("material") or {}))

    def as_dict(self) -> dict[str, Any]:
        body = dataclasses.asdict(self)
        body.pop("material", None)
        return body


def base_cases() -> list[Case]:
    from .ablation_corpus import BASE_CASES
    return [Case.from_dict(c) for c in BASE_CASES]


# ------------------------------------------------------------- the mutations
@dataclass(frozen=True)
class Mutation:
    name: str
    error: str          # the scientific error, as a reviewer would name it
    apply: Callable[[Case], "Case | None"]


def _ci_replace(value: str, old: str, new: str) -> str:
    return re.sub(re.escape(old), new, value, count=0, flags=re.I) if old else value


def _mutant(case: Case, name: str, **changes: Any) -> Case:
    return replace(case, id=f"{case.id}/{name}", error_class=name, base=case.id,
                   gold="refuse", **changes)


def _swap(case: Case, key: str, fields: Sequence[str] = (), *, name: str = "",
          **extra: Any) -> Case | None:
    pair = case.material.get(key)
    if not pair or pair[0] not in case.statement:
        return None
    old, new = pair
    changes: dict[str, Any] = {"statement": case.statement.replace(old, new, 1), **extra}
    for attr in fields:
        value = getattr(case, attr)
        if isinstance(value, str):
            changes[attr] = _ci_replace(value, old, new)
        else:
            changes[attr] = tuple(_ci_replace(v, old, new) for v in value)
    return _mutant(case, name or key, **changes)


def _from_material(case: Case, key: str, **fixed: Any) -> Case | None:
    spec = case.material.get(key)
    if not spec:
        return None
    allowed = {"statement", "claim_kind", "certainty", "population", "outcome", "direction"}
    return _mutant(case, key, **{**{k: v for k, v in spec.items() if k in allowed}, **fixed})


def _source_swap(case: Case, key: str, design: str) -> Case | None:
    spec = case.material.get(key)
    if not spec:
        return None
    source = replace(case.source, design=design, text=spec["text"],
                     population=spec.get("population", case.source.population))
    return _mutant(case, key, source=source)


_INVERSE = {"decrease": "increase", "increase": "decrease"}
_OTHER = {"doi": "doi:10.5555/tcm-ablation.99", "pmid": "PMID: 99999999"}


def _citation(case: Case, name: str, supplied: bool) -> Case | None:
    if not case.citation or case.citation not in case.statement:
        return None
    from psh.evidence.record import canonical_identifier
    scheme = canonical_identifier(case.citation)[0]
    other = _OTHER.get(scheme) or re.sub(r"\d+$", "99", case.citation)
    return _mutant(case, name, statement=case.statement.replace(case.citation, other),
                   citation=other, cited_as=other, supplied=supplied)


MUTATIONS: tuple[Mutation, ...] = (
    Mutation("population", "a finding carried to a population the study did not enrol",
             lambda c: _swap(c, "population", ("population",))),
    Mutation("outcome", "a finding carried to an outcome the study did not measure",
             lambda c: _swap(c, "outcome", ("outcome",))),
    Mutation("subject", "a finding attributed to another intervention",
             lambda c: _swap(c, "subject", ("subject",))),
    Mutation("near_name", "a near-name herb standing in for the one studied (附子/白附子)",
             lambda c: _swap(c, "near_name", ("subject", "entities"))),
    Mutation("dose", "a dose read ten times too high (1.5 g as 15 g)",
             lambda c: _swap(c, "dose", ("dose",))),
    Mutation("direction", "the direction of the effect inverted",
             lambda c: _swap(c, "direction", (),
                             direction=_INVERSE.get(c.direction, c.direction))),
    Mutation("certainty", "a finding stated as proven, always, or curative",
             lambda c: _swap(c, "certainty", (), certainty="definitive")),
    Mutation("upgrade", "a claim kind its evidence cannot reach (association or mechanism "
             "stated as efficacy; a classical passage as a clinical result)",
             lambda c: _from_material(c, "upgrade")),
    Mutation("animal", "an animal study cited for a claim about patients",
             lambda c: _source_swap(c, "animal", "animal")),
    Mutation("docking", "a docking prediction cited for a claim about patients",
             lambda c: _source_swap(c, "docking", "docking")),
    Mutation("plan", "a claim of cure followed by a plan clause, as if the plan exempted it",
             lambda c: _from_material(c, "plan", certainty="definitive")),
    Mutation("borrowed_direction", "a direction no edge on the claim's path records",
             lambda c: (_mutant(c, "borrowed_direction",
                                path=tuple(c.material["borrowed_direction"]))
                        if c.path and c.material.get("borrowed_direction") else None)),
    # The same errors in the text only: the structured claim still names what was
    # studied, and the sentence a reader sees does not. An agent's summary drifting from
    # its own structured claim is the case a check of the structure alone cannot see.
    Mutation("population_text", "a population the study did not enrol, in the text only",
             lambda c: _swap(c, "population", name="population_text")),
    Mutation("outcome_text", "an outcome the study did not measure, in the text only",
             lambda c: _swap(c, "outcome", name="outcome_text")),
    Mutation("subject_text", "another intervention, in the text only",
             lambda c: _swap(c, "subject", name="subject_text")),
    Mutation("near_name_text", "a near-name herb, in the text only",
             lambda c: _swap(c, "near_name", name="near_name_text")),
    Mutation("certainty_text", "proven, always or curative, in the text only",
             lambda c: _swap(c, "certainty", name="certainty_text")),
    Mutation("retracted", "a retracted source cited as support",
             lambda c: _mutant(c, "retracted", source=replace(c.source, retracted=True))),
    Mutation("tampered", "a record edited after it was signed (retraction removed)",
             lambda c: _mutant(c, "tampered", source=replace(c.source, tampered=True))),
    Mutation("citation_mismatch", "the record of one paper supplied under another's "
             "identifier", lambda c: _citation(c, "citation_mismatch", True)),
    Mutation("fabricated_citation", "a citation with no record behind it",
             lambda c: _citation(c, "fabricated_citation", False)),
    # What a finding is about, beyond its named subject (the TCM domain checks, CLM015 to
    # CLM018): the level studied, the processing state, the exposure reached, and how many
    # studies there are.
    Mutation("constituent_formula", "one constituent's evidence carried to the whole formula, "
             "or the formula's credited to one constituent",
             lambda c: _swap(c, "constituent_formula", ("subject",))),
    Mutation("processing_transfer", "evidence for one processing state carried to another "
             "(制附子 to 生附子)",
             lambda c: _swap(c, "processing_transfer", ("subject", "entities"))),
    Mutation("exposure_text", "a bench finding placed at the concentrations patients reach",
             lambda c: _swap(c, "exposure_text", name="exposure_text")),
    Mutation("duplicate_source", "one study, reached through two databases, counted as two "
             "independent ones",
             lambda c: _swap(c, "duplicate_source", name="duplicate_source", duplicates=2)),
)


def mutants(cases: Iterable[Case]) -> list[Case]:
    out = []
    for case in cases:
        for mutation in MUTATIONS:
            made = mutation.apply(case)
            if made is not None:
                out.append(made)
    return out


# ------------------------------------------------------------------ the gates
@dataclass(frozen=True)
class GateResult:
    gate: str
    refused: bool
    applies: bool = True
    codes: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"gate": self.gate, "refused": self.refused, "applies": self.applies,
                "codes": list(self.codes), "detail": self.detail[:300]}


class _Context:
    """What the gates share: a signing key for the fixture records, one release snapshot,
    and one kernel, whose output gate is the gate the runtime calls."""

    def __init__(self, workdir: Path) -> None:
        from psh.evidence.signing import EvidenceSigner
        self.signer = EvidenceSigner(key=b"governance-ablation-fixture-key!")
        self.workdir = workdir
        self._snapshot: Any = None
        self._kernel: Any = None

    @property
    def snapshot(self) -> Any:
        if self._snapshot is None:
            self._snapshot = _release_snapshot(self.workdir / "release-snapshot")
        return self._snapshot

    @property
    def kernel(self) -> Any:
        if self._kernel is None:
            from psh import PSHConfig, TrustedKernel

            from ..tcm.model import PASSAGE_CITATION
            # The deployment's citation shapes, as a TCM deployment configures them: a
            # passage is cited by its record id and looked up like a DOI.
            self._kernel = TrustedKernel(PSHConfig(
                state_dir=self.workdir / "psh",
                citation_patterns=(PASSAGE_CITATION,)).ensure_dirs())
        return self._kernel


_A, _B = "inchikey:AAAAAAAAAAAAAA-AAAAAAAAAA-N", "inchikey:BBBBBBBBBBBBBB-BBBBBBBBBB-N"
_NODES = {"A": _A, "B": _B, "T1": "uniprot:P10001", "T2": "uniprot:P10002"}


def _release_snapshot(root: Path) -> Any:
    from ..sources.snapshot import build_snapshot

    def edge(record: str, s: str, p: str, o: str, **kw: Any) -> dict[str, Any]:
        return {"subject": s, "predicate": p, "object": o,
                "primary_knowledge_source": "ablation-fixture",
                "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
                "study_design": "in_vitro", "license": "CC0-1.0",
                "source_record_id": record, "publications": ["doi:10.5555/tcm-ablation.08"],
                **kw}

    root.mkdir(parents=True, exist_ok=True)
    raw = root / "fixture.txt"
    raw.write_text("governance ablation release fixture", encoding="utf-8")
    nodes = [{"id": _NODES[k], "category": "ingredient" if k in "AB" else "target",
              "name": k, "source": "ablation-fixture"} for k in _NODES]
    edges = [edge("bind", _A, "targets", _NODES["T1"],
                  measure={"type": "Kd", "relation": "=", "value": 50.0, "unit": "nM"}),
             edge("antag_A_T1", _A, "antagonises", _NODES["T1"]),
             edge("antag_B_T2", _B, "antagonises", _NODES["T2"])]
    return build_snapshot(key="ablation_fixture", version="1", nodes=nodes, edges=edges,
                          raw_files={"fixture.txt": raw}, parser="ablation fixture",
                          root=root, license="CC0-1.0", citation="governance ablation fixture")


def _bare(identifier: str) -> tuple[str, str]:
    from psh.evidence.record import canonical_identifier
    scheme, value = canonical_identifier(identifier)
    return scheme, (value if scheme else identifier)


def _record(case: Case, ctx: _Context) -> Any:
    from psh.evidence import EvidenceRecord
    from psh.evidence.record import RetractionStatus, SourceType
    scheme, bare = _bare(case.source.identifier)
    kind = {"pmid": SourceType.PMID, "doi": SourceType.DOI}.get(scheme,
                                                               SourceType.LOCAL_ARTIFACT)
    record = ctx.signer.sign(EvidenceRecord.from_text(
        identifier=bare, text=case.source.text, retrieved_by="ablation-fixture",
        retrieval_run="governance-ablation",
        retracted=case.source.retracted or case.source.tampered, source_type=kind))
    if case.source.tampered:
        record = dataclasses.replace(record, retraction=RetractionStatus.NOT_RETRACTED)
    return record


def _sources(case: Case, ctx: _Context) -> dict[str, Any]:
    return {case.key: _record(case, ctx)} if case.supplied else {}


def _ingest(case: Case, ctx: _Context) -> tuple[GateResult, dict[str, Any] | None]:
    """The kernel's ingest, as the Runner runs it: identifier binding, signature re-checked.

    It refuses a record supplied under another identifier. It does not refuse a tampered or
    retracted record; it marks the first (``revalidate_trust``) and the output gate refuses
    what a sentence cites from either.
    """
    from psh.contracts import PolicyDenied, RunEnvelope
    from psh.runtime.finalize import ingest_evidence
    sources = _sources(case, ctx)
    if not sources:
        return GateResult("provenance", False, applies=False,
                          detail="no record supplied: nothing to ingest"), {}
    try:
        records = ingest_evidence(types.SimpleNamespace(evidence_signer=ctx.signer),
                                  sources, RunEnvelope())
    except PolicyDenied as exc:
        return GateResult("provenance", True, codes=("identifier_binding",),
                          detail=str(exc)), None
    marked = [r.identifier for r in records.values() if getattr(r, "tampered", False)]
    return GateResult("provenance", False,
                      detail=f"marked tampered: {marked}" if marked else ""), records


def _gate_output(case: Case, ctx: _Context, sources: Mapping[str, Any]) -> GateResult:
    """The kernel's output gate: support, scope, citations and plan clauses, per sentence."""
    from psh.contracts import PSHError, RunEnvelope
    gate = ctx.kernel.output_gate
    seen = len(gate.verdicts)
    try:
        gate.check(case.statement, RunEnvelope(), sources=dict(sources))
    except PSHError as exc:
        # The gate is shared: only a verdict recorded by this check describes this case.
        verdict = gate.verdicts[-1] if len(gate.verdicts) > seen else None
        codes = []
        if verdict is not None:
            if getattr(verdict, "unsupported", None):
                codes.append("unsupported")
            if getattr(verdict, "uncited", None):
                codes.append("uncited")
        return GateResult("output", True, codes=tuple(codes) or (type(exc).__name__,),
                          detail=str(exc))
    return GateResult("output", False)


_DESIGN = {"systematic_review": "SYSTEMATIC_REVIEW", "randomized_trial": "RANDOMISED_TRIAL",
           "observational": "COHORT", "case_report": "CASE_REPORT", "animal": "ANIMAL",
           "in_vitro": "IN_VITRO", "expert_consensus": "EXPERT_CONSENSUS",
           "classical_text": "CLASSICAL_TEXT"}
_EVIDENCE_SUBJECT = {"animal": "ANIMAL", "in_vitro": "CELL", "classical_text": "TEXT",
                     "expert_consensus": "TEXT"}
_ABOUT_PEOPLE = {"efficacy", "association", "safety_signal", "recommendation"}


def _gate_licensing(case: Case, ctx: _Context) -> GateResult:
    """The compiler's type check (TYP102/103/104/107) on the structured claim."""
    from psh.evidence.support import Certainty
    from psh.sir.values import (ClaimKind, ClaimType, EvidenceType, Licensing, Provenance,
                                StudyDesign, Subject, licenses)
    from ..contracts.evidence_item import PREDICTIVE_DESIGNS
    design = case.source.design
    if design in _DESIGN:
        study = StudyDesign[_DESIGN[design]]
        subject = Subject[_EVIDENCE_SUBJECT.get(design, "HUMAN")]
    elif design in PREDICTIVE_DESIGNS:
        study, subject = StudyDesign.IN_SILICO, Subject.COMPUTATIONAL
    else:
        study, subject = StudyDesign.UNKNOWN, Subject.UNSPECIFIED
    evidence = EvidenceType(design=study, subject=subject,
                            population=case.source.population,
                            intervention=case.source.subject, outcome=case.source.outcome,
                            provenance=Provenance.RETRIEVED_VERIFIED,
                            identifier=case.source.identifier,
                            retracted=case.source.retracted)
    claim = ClaimType(kind=ClaimKind(case.claim_kind),
                      subject=(Subject.HUMAN if case.claim_kind in _ABOUT_PEOPLE
                               else Subject.UNSPECIFIED),
                      population=case.population, intervention=case.subject,
                      outcome=case.outcome, certainty=Certainty(case.certainty))
    verdict = licenses(evidence, claim)
    if verdict.grade is Licensing.UNLICENSED:
        return GateResult("licensing", True, codes=("TYP102",),
                          detail="; ".join(verdict.reasons))
    if verdict.grade is Licensing.EXTRAPOLATED:
        # TYP103 is an error unless the claim declares the extrapolation; none here does.
        return GateResult("licensing", True, codes=("TYP103",),
                          detail="; ".join(verdict.reasons))
    return GateResult("licensing", False)


def _gate_claim_contract(case: Case, ctx: _Context) -> GateResult:
    """The domain layer's claim contract (CLM codes)."""
    from ..contracts import CandidateClaim, EvidenceItem, check_claim
    scheme, bare = _bare(case.source.identifier)
    item = EvidenceItem(
        id="e1", design=case.source.design, quote=case.source.text[:80],
        source_card_id="ablation-fixture", identifier=bare,
        identifier_type=scheme or ("classical_passage"
                                   if case.source.design == "classical_text"
                                   else "local_artifact"),
        subject=case.source.subject,
        population=case.source.population, outcome=case.source.outcome,
        # A tampered record reads "not retracted": that is the edit. The contract sees the
        # record as it reads; only the kernel's signature check sees the edit.
        retracted="retracted" if case.source.retracted
        else "not_retracted").located_in(case.source.text)
    # The one study, as each database that holds it hands it over: the same identifier on
    # every item. A claim citing them all cites one study, however many items it lists.
    items = {item.id: item}
    for n in range(2, case.duplicates + 1):
        items[f"e{n}"] = replace(item, id=f"e{n}")
    claim = CandidateClaim(
        id="c1", text=case.statement, claim_kind=case.claim_kind, subject=case.subject,
        predicate=case.direction or "is related to", object=case.outcome or case.subject,
        supports=tuple(items), asserted_population=case.population,
        supported_population=case.source.population, asserted_outcome=case.outcome,
        supported_outcome=case.source.outcome,
        direction=case.direction or "unclear",
        confidence=0.5, confidence_basis="one cited source",
        hedged=case.certainty in ("tentative", "uncertain"),
        falsified_by="a study of the same design that finds no effect")
    verdict = check_claim(claim, items)
    if not verdict.allowed:
        return GateResult("claim_contract", True, codes=verdict.codes,
                          detail="; ".join(verdict.reason_text))
    return GateResult("claim_contract", False)


def _gate_release_path(case: Case, ctx: _Context) -> GateResult:
    """The release check over snapshot edges: path, direction, licensing along the path."""
    if not case.path:
        return GateResult("release_path", False, applies=False,
                          detail="the claim cites no snapshot edges")
    from ..sources.release import CandidateClaim as EdgeClaim
    from ..sources.release import check_release
    snap = ctx.snapshot
    statement = re.sub(r"\s*[（(][^()（）]*[)）]\s*", " ", case.statement).strip()
    statement = statement.replace("compound A", "A").replace("target T1", "T1")
    claim = EdgeClaim(case.claim_kind, _NODES.get(case.path_subject, case.path_subject),
                      _NODES.get(case.path_object, case.path_object),
                      tuple((snap.snapshot_id, r) for r in case.path), statement=statement)
    verdict = check_release([claim], [snap])
    if not verdict.ok:
        return GateResult("release_path", True, codes=("release_refused",),
                          detail="; ".join(r for _, r in verdict.refused))
    return GateResult("release_path", False)


KERNEL_GATES: tuple[str, ...] = ("provenance", "output", "licensing")
DOMAIN_GATES: tuple[str, ...] = ("claim_contract", "release_path")
#: The gates in order. ``provenance`` and ``output`` run together in the kernel and are
#: evaluated in ``_evaluate``; the others are independent checks.
GATES: tuple[str, ...] = KERNEL_GATES + DOMAIN_GATES
_INDEPENDENT: Mapping[str, Callable[[Case, _Context], GateResult]] = {
    "licensing": _gate_licensing, "claim_contract": _gate_claim_contract,
    "release_path": _gate_release_path,
}


def _evaluate(case: Case, ctx: _Context) -> dict[str, GateResult]:
    """Every gate once. ``output`` sees the records as supplied, ``output|ingested`` as
    ingest re-checked them, which is what the output gate sees when ingest is on."""
    ingest, records = _ingest(case, ctx)
    raw = _sources(case, ctx)
    out = {"provenance": ingest, "output": _gate_output(case, ctx, raw),
           "output|ingested": (ingest if records is None
                               else _gate_output(case, ctx, records))}
    out.update({name: gate(case, ctx) for name, gate in _INDEPENDENT.items()})
    return out
GATE_LAYER = {**{g: "kernel" for g in KERNEL_GATES}, **{g: "domain" for g in DOMAIN_GATES}}


def _configurations() -> dict[str, tuple[str, ...]]:
    every = tuple(GATES)
    out: dict[str, tuple[str, ...]] = {"ungoverned": (), "kernel only": KERNEL_GATES,
                                       "domain only": DOMAIN_GATES, "full": every}
    for gate in every:
        out[f"full without {gate}"] = tuple(g for g in every if g != gate)
    for gate in every:
        out[f"{gate} only"] = (gate,)
    return out


CONFIGURATIONS: Mapping[str, tuple[str, ...]] = _configurations()


# ------------------------------------------------------------------ the run
def _wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


@dataclass
class AblationReport:
    cases: list[Case]
    results: dict[str, dict[str, GateResult]]        # case id -> gate -> result
    configurations: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(CONFIGURATIONS))

    def released(self, case: Case, gates: Sequence[str]) -> bool:
        r = self.results[case.id]
        if "provenance" in gates and r["provenance"].refused:
            return False
        if "output" in gates:
            seen = r["output|ingested"] if "provenance" in gates else r["output"]
            if seen.refused:
                return False
        return not any(r[g].refused for g in gates if g not in ("provenance", "output"))

    @property
    def base(self) -> list[Case]:
        return [c for c in self.cases if c.gold == "release"]

    @property
    def mutated(self) -> list[Case]:
        return [c for c in self.cases if c.gold == "refuse"]

    def rows(self) -> list[dict[str, Any]]:
        out = []
        for name, gates in self.configurations.items():
            leaked = sum(self.released(c, gates) for c in self.mutated)
            refused = sum(not self.released(c, gates) for c in self.base)
            n, m = len(self.mutated), len(self.base)
            out.append({"configuration": name, "gates": list(gates),
                        "errors_released": leaked, "mutants": n,
                        "error_release_rate": round(leaked / n, 4) if n else 0.0,
                        "error_release_ci95": list(_wilson(leaked, n)),
                        "false_refusals": refused, "base_cases": m,
                        "false_refusal_rate": round(refused / m, 4) if m else 0.0})
        return out

    def kill_matrix(self) -> dict[str, dict[str, Any]]:
        """Error class -> share of its mutants refused by each gate alone, by each layer
        alone, and by the full stack."""
        columns = {**{g: (g,) for g in GATES}, "kernel": KERNEL_GATES,
                   "domain": DOMAIN_GATES, "full": GATES}
        out: dict[str, dict[str, Any]] = {}
        for error in dict.fromkeys(c.error_class for c in self.mutated):
            group = [c for c in self.mutated if c.error_class == error]
            row: dict[str, Any] = {"n": len(group)}
            for column, gates in columns.items():
                row[column] = round(sum(not self.released(c, gates) for c in group)
                                    / len(group), 4)
            out[error] = row
        return out

    def adds(self) -> dict[str, list[str]]:
        """Gate -> mutants the full stack refuses and releases without that gate.

        Leave-one-out, so it is what switching the gate off costs, interactions included:
        without ingest, the output gate no longer sees that a record was tampered with.
        """
        out: dict[str, list[str]] = {}
        for gate in GATES:
            rest = tuple(g for g in GATES if g != gate)
            out[gate] = [c.id for c in self.mutated
                         if not self.released(c, GATES) and self.released(c, rest)]
        return out

    def survivors(self) -> list[Case]:
        return [c for c in self.mutated if self.released(c, GATES)]

    def false_refusals(self) -> list[tuple[Case, list[GateResult]]]:
        return [(c, [r for r in self.results[c.id].values() if r.refused])
                for c in self.base if not self.released(c, GATES)]

    def as_dict(self) -> dict[str, Any]:
        langs = sorted({c.language for c in self.cases})
        by_language = {}
        for lang in langs:
            mutated = [c for c in self.mutated if c.language == lang]
            base = [c for c in self.base if c.language == lang]
            by_language[lang] = {
                "mutants": len(mutated), "base_cases": len(base),
                "released_by_full": sum(self.released(c, GATES) for c in mutated),
                "refused_by_full": sum(not self.released(c, GATES) for c in base)}
        return {
            "construction": ("mutation testing: base cases a reviewer would release, each "
                             "mutated by one named scientific error; base cases and operators "
                             "were written by the authors of the gates, so this is a "
                             "regression benchmark with a stated construction, not an "
                             "estimate of field error rates"),
            "base_cases": len(self.base), "mutants": len(self.mutated),
            "gates": {g: GATE_LAYER[g] for g in GATES},
            "configurations": self.rows(), "kill_matrix": self.kill_matrix(),
            "adds": self.adds(),
            "survivors": [{"id": c.id, "error_class": c.error_class,
                           "statement": c.statement} for c in self.survivors()],
            "false_refusals": [{"id": c.id, "statement": c.statement,
                                "refused_by": [r.as_dict() for r in rs]}
                               for c, rs in self.false_refusals()],
            "by_language": by_language,
            "cases": {c.id: {"error_class": c.error_class, "gold": c.gold,
                             "language": c.language,
                             "gates": {g: r.as_dict() for g, r in self.results[c.id].items()}}
                      for c in self.cases},
        }


def run_ablation(cases: Sequence[Case] | None = None, *,
                 workdir: str | Path | None = None) -> AblationReport:
    """Every gate on every case once; configurations are read off the results."""
    if cases is None:
        base = base_cases()
        cases = base + mutants(base)
    with tempfile.TemporaryDirectory(prefix="ablation-") as tmp:
        ctx = _Context(Path(workdir) if workdir else Path(tmp))
        results = {c.id: _evaluate(c, ctx) for c in cases}
    return AblationReport(cases=list(cases), results=results)


def load_drafts(path: str | Path) -> list[Case]:
    """Labelled outputs from anywhere (a model's drafts, a reviewer's set), one JSON per line.

    Each line is a case in the ``Case.from_dict`` form with ``gold`` set to ``release`` or
    ``refuse`` and, for a refusal, an ``error_class``. The same gates and configurations
    then measure what each part of the governance does to *those* outputs.
    """
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = Case.from_dict(json.loads(line))
        if case.gold not in ("release", "refuse"):
            raise ValueError(f"line {n}: gold must be 'release' or 'refuse'")
        out.append(case)
    return out


# ------------------------------------------------------------------ the report
def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def render_markdown(report: AblationReport) -> str:
    data = report.as_dict()
    lines = ["# Governance ablation", "",
             f"{data['base_cases']} base cases, {data['mutants']} mutants over "
             f"{len(data['kill_matrix'])} error classes, {len(GATES)} gates. "
             + data["construction"][0].upper() + data["construction"][1:] + ".", "",
             "## Configurations", "",
             "| Configuration | Errors released | 95% CI | Correct outputs refused |",
             "| --- | ---: | --- | ---: |"]
    for row in data["configurations"]:
        lo, hi = row["error_release_ci95"]
        lines.append(f"| {row['configuration']} | {row['errors_released']}/{row['mutants']} "
                     f"({_pct(row['error_release_rate'])}) | {_pct(lo)}–{_pct(hi)} | "
                     f"{row['false_refusals']}/{row['base_cases']} |")
    columns = (*GATES, "kernel", "domain", "full")
    lines += ["", "## Which part catches which error", "",
              "Each gate alone, each layer alone, and the full stack: the share of each "
              "error class's mutants refused.", "",
              "| Error class | n | " + " | ".join(columns) + " |",
              "| --- | ---: | " + " | ".join("---:" for _ in columns) + " |"]
    for error, row in data["kill_matrix"].items():
        lines.append(f"| {error} | {row['n']} | "
                     + " | ".join(_pct(row[c]) for c in columns) + " |")
    lines += ["", "## What each gate adds", "",
              "Mutants the full stack refuses and releases once that gate is switched off.",
              ""]
    for gate, ids in data["adds"].items():
        lines.append(f"- **{gate}** ({GATE_LAYER[gate]}, {len(ids)}): "
                     + (", ".join(f"`{i}`" for i in ids) if ids else "nothing the others "
                        "do not also catch"))
    lines += ["", "## Errors the full stack releases", ""]
    if data["survivors"]:
        lines += [f"- `{s['id']}` ({s['error_class']}): {s['statement']}"
                  for s in data["survivors"]]
    else:
        lines.append("None.")
    lines += ["", "## Correct outputs the full stack refuses", ""]
    if data["false_refusals"]:
        for f in data["false_refusals"]:
            why = "; ".join(f"{r['gate']}: {r['detail'][:160]}" for r in f["refused_by"])
            lines.append(f"- `{f['id']}`: {f['statement']} — {why}")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"
