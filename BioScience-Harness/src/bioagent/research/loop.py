"""A research question carried end to end on snapshot data, under audit, resumable.

The audit's last finding (F10) was that the pieces existed and did not meet: a skill
layer on a seed corpus, a snapshot pipeline with real sources fixed to one formula, a
kernel with an audit chain, and no single run that went from a question to a released,
checked result. :func:`run_research` is that run. Its stages are fixed and each one is a
refusal point:

``protocol``
    The question is parsed into a formula (and optionally a disease). The protocol is
    frozen before any data is read: its sources, analysis parameters, the rebuttal tests
    the claims must survive, and the fallbacks the run may take. The protocol digest is
    written to the kernel's audit chain first, so a later reader can see that the
    analysis was not chosen after its results were known.
``retrieve``
    Each source is loaded through the snapshot ledger, which re-hashes the tables and
    checks them against the id recorded when they were built. A required source that
    is missing refuses the run. A missing *optional* source is dropped only when the
    protocol lists ``drop_missing_optional_source`` as a fallback, and the drop is
    recorded as a deviation. A source that fails its hash check is never dropped: the
    data changed, and that is a failure, not something to work around.
``analyse``
    :func:`run_network_pharmacology` with the protocol's parameters.
``rebut``
    Every claim the analysis released is tested against the pre-registered rebuttals.
    A claim that disappears under the conservative background, when one activity
    source is left out, or under a different permutation seed is refuted and does not
    reach release. A rebuttal that cannot run (one activity source only) is recorded
    as a limitation, never counted as passed.
``release``
    The surviving claims become a :class:`~bioagent.contracts.ResearchArtifact`. Each
    cited activity edge becomes an evidence item. Its quote is the edge row exactly as
    the snapshot stores it, and its receipt is checked against a content store rebuilt
    from the snapshots on disk, not from anything the run kept in memory. The artifact
    is attested with the kernel's policy id and audit-chain head and validated with
    its written outputs. ``released`` is true only when
    ``validate_artifact(...).release_authorized`` is true.

Every stage writes a checkpoint under ``state_dir``. Run the same question and protocol
again and the completed stages are reused. A stage is reused only if its recorded
inputs still match; the retrieved snapshot ids are compared on every resume. A run that
crashed in ``rebut`` therefore resumes there, without re-running the analysis.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..analysis.network_pharmacology import Parameters, run_network_pharmacology
from ..contracts import (ArtifactFile, CandidateClaim, EvidenceItem, SourceCard,
                         validate_artifact)
from ..contracts.artifact import ArtifactVerdict, ResearchArtifact
from ..contracts.evidence_item import STUDY_DESIGNS
from ..contracts.receipts import ContentStore
from ..sources.herbs import GEGEN_QINLIAN, FormulaVersion
from ..sources.herbs import KEY as HERB_KEY
from ..sources.ledger import SnapshotLedger
from ..sources.snapshot import Snapshot, SnapshotError, load_snapshot
from ..tcm.model import licenses

__all__ = ["FORMULAS", "Protocol", "QuestionRefused", "ResearchQuestion", "ResearchRefused",
           "ResearchRun", "canonical_row", "default_protocol", "parse_question",
           "run_research", "snapshot_content_store"]

SKILL_ID = "research-network-pharmacology"
SKILL_VERSION = "0.1.0"

#: Formulas a question may name, by id, with the names a question may use for them.
FORMULAS: Mapping[str, FormulaVersion] = {GEGEN_QINLIAN.id: GEGEN_QINLIAN}
FORMULA_NAMES: Mapping[str, str] = {
    "葛根黄芩黄连汤": GEGEN_QINLIAN.id, "葛根芩连汤": GEGEN_QINLIAN.id,
    "gegen qinlian": GEGEN_QINLIAN.id, "gegen qinlian tang": GEGEN_QINLIAN.id,
    "gegen qinlian decoction": GEGEN_QINLIAN.id,
}

#: Snapshot keys that carry compound → protein activity. The rebuttals leave these out
#: one at a time.
ACTIVITY_SOURCES = ("npass", "cmaup", "lotus", "bindingdb", "pubchem_bioassay", "chembl")
STAGES = ("protocol", "retrieve", "analyse", "rebut", "release")
REBUTTALS = ("background_sensitivity", "leave_one_source_out", "seed_stability")
FALLBACKS = ("drop_missing_optional_source",)
_DISEASE_ID = re.compile(r"\b((?:MONDO|EFO|HP|Orphanet|DOID)_\d+)\b")


class QuestionRefused(ValueError):
    """The question names nothing this loop can study."""


class ResearchRefused(RuntimeError):
    """A stage refused to proceed; the checkpoint keeps what completed before it."""


# ---------------------------------------------------------------------------
# question and protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchQuestion:
    text: str
    formula_id: str
    disease: str = ""

    @property
    def formula(self) -> FormulaVersion:
        return FORMULAS[self.formula_id]


def parse_question(text: str, *, disease: str = "") -> ResearchQuestion:
    """Read the formula and (optional) disease id out of a question.

    Deliberately literal: a formula is recognised by one of its registered names, a
    disease only by an ontology id (``MONDO_0005148``). A question that names neither a
    known formula nor anything else this loop can test is refused, so the loop never
    studies something other than what was asked.
    """
    lowered = text.lower()
    found = {fid for name, fid in FORMULA_NAMES.items() if name.lower() in lowered}
    if not found:
        raise QuestionRefused(
            f"no known formula is named in {text!r}; known: "
            f"{sorted({f.chinese for f in FORMULAS.values()})}")
    if len(found) > 1:
        raise QuestionRefused(f"the question names more than one formula: {sorted(found)}")
    ids = _DISEASE_ID.findall(text)
    if disease and ids and disease not in ids:
        raise QuestionRefused(f"disease {disease!r} disagrees with the question's {ids}")
    if len(set(ids)) > 1:
        raise QuestionRefused(f"the question names more than one disease: {sorted(set(ids))}")
    return ResearchQuestion(text=text, formula_id=found.pop(),
                            disease=disease or (ids[0] if ids else ""))


@dataclass(frozen=True)
class Protocol:
    """What the run will do, frozen before any data is read."""

    question: ResearchQuestion
    required_sources: tuple[str, ...]
    optional_sources: tuple[str, ...] = ()
    parameters: Parameters = field(default_factory=Parameters)
    rebuttals: tuple[str, ...] = REBUTTALS
    fallbacks: tuple[str, ...] = FALLBACKS
    #: The alternative seeds a claim's significance must survive.
    rebuttal_seeds: tuple[int, ...] = (1, 2)

    def __post_init__(self) -> None:
        unknown = set(self.rebuttals) - set(REBUTTALS)
        if unknown:
            raise ValueError(f"unknown rebuttal(s) {sorted(unknown)}")
        unknown = set(self.fallbacks) - set(FALLBACKS)
        if unknown:
            raise ValueError(f"unknown fallback(s) {sorted(unknown)}")
        for needed in (HERB_KEY, "reactome"):
            if needed not in self.required_sources:
                raise ValueError(f"{needed} must be a required source")
        activity = set(self.required_sources + self.optional_sources) & set(ACTIVITY_SOURCES)
        if not activity:
            raise ValueError(f"the protocol names no activity source ({ACTIVITY_SOURCES})")
        if self.question.disease and "opentargets" not in self.required_sources:
            raise ValueError("a disease question requires the opentargets source")

    def as_dict(self) -> dict[str, Any]:
        return {"question": asdict(self.question),
                "formula_fingerprint": self.question.formula.fingerprint,
                "required_sources": list(self.required_sources),
                "optional_sources": list(self.optional_sources),
                "parameters": asdict(self.parameters),
                "rebuttals": list(self.rebuttals), "fallbacks": list(self.fallbacks),
                "rebuttal_seeds": list(self.rebuttal_seeds)}

    @property
    def digest(self) -> str:
        return _digest(self.as_dict())


def default_protocol(question: ResearchQuestion, *, activity: Sequence[str] = ("npass",),
                     optional: Sequence[str] = ("string",),
                     parameters: Parameters | None = None) -> Protocol:
    required = [HERB_KEY, "reactome", *activity]
    if question.disease:
        required.append("opentargets")
    return Protocol(question=question, required_sources=tuple(required),
                    optional_sources=tuple(o for o in optional if o not in required),
                    parameters=parameters or Parameters())


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResearchRun:
    run_id: str
    protocol: Protocol
    artifact: ResearchArtifact | None
    verdict: ArtifactVerdict | None
    snapshots: Mapping[str, str]
    deviations: tuple[Mapping[str, Any], ...]
    rebuttal: Mapping[str, Any]
    resumed_stages: tuple[str, ...]
    audit_head: str
    state_dir: str
    output_dir: str

    @property
    def released(self) -> bool:
        """Whether the artifact may be released. A released artifact can hold no
        hypothesis at all: when every claim was refuted, that negative result is what
        is released."""
        return self.verdict is not None and self.verdict.release_authorized

    @property
    def hypotheses(self) -> tuple[str, ...]:
        """The pathways that survived analysis and rebuttal and were released."""
        if self.artifact is None:
            return ()
        return tuple(c.object for c in self.artifact.claims)

    @property
    def refuted(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.rebuttal.get("refuted") or ())


def canonical_row(row: Mapping[str, Any]) -> str:
    """One snapshot row as canonical JSON: the exact text an evidence quote cites."""
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


def snapshot_content_store(snapshots: Sequence[Snapshot],
                           records: Sequence[tuple[str, str]] | None = None) -> ContentStore:
    """A content store rebuilt from snapshots, for re-verifying quote receipts.

    ``records`` limits it to the ``(snapshot id, source record id)`` pairs cited, so a
    large snapshot need not be rendered in full.
    """
    store = ContentStore()
    wanted: dict[str, set[str]] | None = None
    if records is not None:
        wanted = {}
        for sid, rid in records:
            wanted.setdefault(sid, set()).add(rid)
    for snap in snapshots:
        keep = None if wanted is None else wanted.get(snap.snapshot_id, set())
        if keep is not None and not keep:
            continue
        for edge in snap.edges:
            if keep is None or str(edge.get("source_record_id")) in keep:
                store.put(canonical_row(edge))
    return store


def run_research(question: ResearchQuestion | str, *, snapshot_root: str | Path,
                 ledger_path: str | Path, state_dir: str | Path, output_dir: str | Path,
                 protocol: Protocol | None = None, skill_dir: str | Path | None = None,
                 accept_review: bool = False,
                 analyse: Callable[..., Any] = run_network_pharmacology) -> ResearchRun:
    """Run ``question`` through protocol, retrieval, analysis, rebuttal and release."""
    try:
        from psh import PSHConfig, TrustedKernel
    except ImportError as exc:
        raise ResearchRefused(f"PSH is not importable ({exc}); a research run needs "
                              "its audit chain") from exc

    if isinstance(question, str):
        question = parse_question(question)
    protocol = protocol or default_protocol(question)
    if protocol.question != question:
        raise ResearchRefused("the protocol was written for a different question")
    run_id = "research-" + protocol.digest.split(":", 1)[1][:16]
    state = Path(state_dir)
    ckpt = _Checkpoint(state / "research" / run_id)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    contract = _contract(skill_dir)
    resumed: list[str] = []

    kernel = TrustedKernel(PSHConfig(state_dir=state / "psh").ensure_dirs())
    try:
        def audit(event: str, **detail: Any) -> None:
            kernel.audit(f"research_{event}", run_id=run_id, detail=detail)

        # -- protocol -----------------------------------------------------------------
        if ckpt.done("protocol", protocol.digest):
            resumed.append("protocol")
        else:
            audit("protocol_registered", protocol_digest=protocol.digest,
                  question=question.text, formula=question.formula_id,
                  disease=question.disease, rebuttals=list(protocol.rebuttals))
            ckpt.save("protocol", protocol.digest, protocol.as_dict())

        # -- retrieve -----------------------------------------------------------------
        ledger = SnapshotLedger(ledger_path)
        ledger.verify()
        snaps, deviations = _retrieve(protocol, snapshot_root, ledger, accept_review)
        ids = {s.key: s.snapshot_id for s in snaps}
        retrieve_input = _digest({"protocol": protocol.digest, "snapshots": ids})
        if ckpt.done("retrieve", retrieve_input):
            resumed.append("retrieve")
        else:
            recorded = ckpt.data("retrieve")
            if recorded and recorded["snapshots"] != ids:
                audit("snapshots_changed_since_checkpoint", before=recorded["snapshots"],
                      after=ids)
                ckpt.invalidate_from("retrieve")
            audit("sources_retrieved", snapshots=ids, deviations=deviations)
            ckpt.save("retrieve", retrieve_input, {"snapshots": ids,
                                                   "deviations": deviations})

        # -- analyse ------------------------------------------------------------------
        if ckpt.done("analyse", retrieve_input):
            resumed.append("analyse")
            result = ckpt.data("analyse")
        else:
            result = _analyse(analyse, snaps, protocol, protocol.parameters, contract)
            audit("analysis_completed", result_digest=result["digest"],
                  released=len(result["release"]["released"]),
                  refused=len(result["release"]["refused"]))
            ckpt.save("analyse", retrieve_input, result)

        # -- rebut --------------------------------------------------------------------
        rebut_input = _digest({"analysis": result["digest"],
                               "rebuttals": list(protocol.rebuttals)})
        if ckpt.done("rebut", rebut_input):
            resumed.append("rebut")
            rebuttal = ckpt.data("rebut")
        else:
            rebuttal = _rebut(analyse, snaps, protocol, result, contract)
            audit("rebuttal_completed", survived=rebuttal["survived"],
                  refuted=[r["object"] for r in rebuttal["refuted"]],
                  not_run=rebuttal["not_run"])
            ckpt.save("rebut", rebut_input, rebuttal)

        # -- release ------------------------------------------------------------------
        artifact = _artifact(run_id, protocol, snaps, result, rebuttal, deviations, out)
        store = snapshot_content_store(
            snaps, [tuple(s) for c in result["claims"] for s in c["support"]])
        for name in ("enrichment.jsonl",):
            store.put((out / name).read_text(encoding="utf-8"))
        before = validate_artifact(artifact, output_root=out, content_store=store)
        audit("release_validated", artifact_digest=artifact.digest,
              states={k: v for k, v in before.states.items() if k != "execution_attested"},
              codes=list(before.codes))
        if not kernel.events.verify():
            raise ResearchRefused("the audit chain does not verify")
        head = kernel.events.head_hash
        artifact = replace(artifact, policy_id=kernel.policy.profile_id or "default",
                           audit_head=head,
                           provenance={**dict(artifact.provenance),
                                       "pre_attestation_digest": artifact.digest})
        verdict = validate_artifact(artifact, output_root=out, content_store=store)
        (out / "artifact.json").write_text(json.dumps(
            {**artifact.document(), "validation": verdict.as_dict()}, indent=2,
            ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        ckpt.save("release", _digest({"rebut": rebut_input, "artifact": artifact.digest}),
                  {"artifact_digest": artifact.digest, "states": verdict.states})
    finally:
        kernel.close()

    return ResearchRun(run_id=run_id, protocol=protocol, artifact=artifact, verdict=verdict,
                       snapshots=ids, deviations=tuple(deviations), rebuttal=rebuttal,
                       resumed_stages=tuple(resumed), audit_head=head,
                       state_dir=str(state), output_dir=str(out))


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------

def _retrieve(protocol: Protocol, root: str | Path, ledger: SnapshotLedger,
              accept_review: bool) -> tuple[list[Snapshot], list[dict[str, Any]]]:
    latest: dict[str, list[str]] = {}
    for entry in ledger.entries():
        latest.setdefault(entry.key, []).append(entry.version)
    snaps: list[Snapshot] = []
    deviations: list[dict[str, Any]] = []
    for key in (*protocol.required_sources, *protocol.optional_sources):
        versions = latest.get(key, [])
        if key == "opentargets" and protocol.question.disease:
            versions = [v for v in versions if v.endswith("+" + protocol.question.disease)]
        if not versions:
            if key in protocol.optional_sources and \
                    "drop_missing_optional_source" in protocol.fallbacks:
                deviations.append({"fallback": "drop_missing_optional_source",
                                   "source": key,
                                   "reason": "no snapshot recorded in the ledger"})
                continue
            raise ResearchRefused(f"no snapshot of required source {key!r} is recorded "
                                  "in the ledger")
        try:
            snaps.append(load_snapshot(root, key, versions[-1], ledger=ledger,
                                       accept_review=accept_review))
        except SnapshotError as exc:
            # Never a fallback: a snapshot that no longer matches its record changed.
            raise ResearchRefused(f"{key}@{versions[-1]} failed verification: {exc}") from exc
    return snaps, deviations


def _analyse(analyse: Callable[..., Any], snaps: Sequence[Snapshot], protocol: Protocol,
             params: Parameters, contract: Any) -> dict[str, Any]:
    result = analyse(snaps, formula=protocol.question.formula, params=params,
                     contract=contract)
    doc = json.loads(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
    doc["digest"] = result.digest()
    return doc


def _released(result: Mapping[str, Any]) -> set[str]:
    return {c["object"] for c in result["release"]["released"]}


def _rebut(analyse: Callable[..., Any], snaps: Sequence[Snapshot], protocol: Protocol,
           result: Mapping[str, Any], contract: Any) -> dict[str, Any]:
    primary = _released(result)
    params = protocol.parameters
    reasons: dict[str, list[str]] = {p: [] for p in primary}
    tests: list[dict[str, Any]] = []
    not_run: list[dict[str, str]] = []

    def variant(label: str, subset: Sequence[Snapshot], p: Parameters) -> set[str]:
        survived = _released(_analyse(analyse, subset, protocol, p, contract))
        tests.append({"test": label, "released": sorted(survived)})
        return survived

    if primary and "background_sensitivity" in protocol.rebuttals:
        if params.background == "reactome":
            kept = variant("background=assayed", snaps, replace(params, background="assayed"))
            for p in primary - kept:
                reasons[p].append("not significant when the background is restricted to "
                                  "proteins that were assayed; the enrichment is explained by "
                                  "which proteins were tested")
        else:
            not_run.append({"test": "background_sensitivity",
                            "why": "the primary background is already the assayed one"})

    if primary and "leave_one_source_out" in protocol.rebuttals:
        activity = sorted(s.key for s in snaps if s.key in ACTIVITY_SOURCES)
        if len(activity) < 2:
            not_run.append({"test": "leave_one_source_out",
                            "why": f"only one activity source ({', '.join(activity)}); "
                                   "the claims cannot be checked against another"})
        else:
            for key in activity:
                kept = variant(f"without {key}", [s for s in snaps if s.key != key], params)
                for p in primary - kept:
                    reasons[p].append(f"does not survive leaving out {key}; it rests on "
                                      "that one source")

    if primary and "seed_stability" in protocol.rebuttals:
        for offset in protocol.rebuttal_seeds:
            kept = variant(f"seed+{offset}", snaps, replace(params, seed=params.seed + offset))
            for p in primary - kept:
                reasons[p].append(f"not significant under permutation seed "
                                  f"{params.seed + offset}; the result is at the edge of "
                                  "the permutation test's resolution")

    refuted = [{"object": p, "reasons": r} for p, r in sorted(reasons.items()) if r]
    return {"primary": sorted(primary), "tests": tests, "not_run": not_run,
            "refuted": refuted,
            "survived": sorted(p for p, r in reasons.items() if not r)}


def _artifact(run_id: str, protocol: Protocol, snaps: Sequence[Snapshot],
              result: Mapping[str, Any], rebuttal: Mapping[str, Any],
              deviations: Sequence[Mapping[str, Any]], out: Path) -> ResearchArtifact:
    from ..skills.base import artifact as build

    by_id = {s.snapshot_id: s for s in snaps}
    edges = {(s.snapshot_id, str(e.get("source_record_id"))): e
             for s in snaps for e in s.edges}
    cards = {s.snapshot_id: _card(s) for s in snaps}
    survived = set(rebuttal["survived"])
    enrichment = {r["pathway"]: r for r in result["enrichment"]}

    enrichment_text = "".join(canonical_row(r) + "\n" for r in result["enrichment"])
    outputs = {
        "protocol.json": _json(protocol.as_dict()),
        "result.json": _json({k: v for k, v in result.items()}),
        "enrichment.jsonl": enrichment_text,
        "rebuttal.json": _json(dict(rebuttal)),
        "limitations.md": "# Limitations\n\n" + "".join(
            f"- {line}\n" for line in _limitations(result, rebuttal, deviations)),
    }
    files = []
    for name, text in outputs.items():
        (out / name).write_text(text, encoding="utf-8")
        files.append(ArtifactFile(path=name, sha256=hashlib.sha256(
            text.encode("utf-8")).hexdigest(), bytes=len(text.encode("utf-8")),
            media_type="application/json" if name.endswith(("json", "jsonl"))
            else "text/markdown"))

    evidence: dict[str, EvidenceItem] = {}
    claims: list[CandidateClaim] = []
    enrichment_card = SourceCard(id=f"{run_id}.enrichment", name="enrichment computed by "
                                 "this run", kind="dataset", access_method="local_file",
                                 license_spdx="CC0-1.0",
                                 snapshot_hash=hashlib.sha256(
                                     enrichment_text.encode("utf-8")).hexdigest(),
                                 snapshot_at=_now())
    for claim in result["claims"]:
        pathway = claim["object"]
        if pathway not in survived:
            continue
        supports: list[str] = []
        for sid, rid in claim["support"]:
            edge = edges.get((sid, rid))
            if edge is None:
                continue
            design = str(edge.get("study_design") or "")
            if design not in STUDY_DESIGNS or not licenses(
                    _tier(design), "mechanism_hypothesis"):
                continue                           # composition and identity: context only
            eid = f"edge.{by_id[sid].key}.{rid}"
            if eid not in evidence:
                quote = canonical_row(edge)
                ident, itype = _identifier(edge)
                evidence[eid] = EvidenceItem(
                    id=eid, design=design, quote=quote,
                    citation=f"{sid} record {rid}", identifier=ident,
                    identifier_type=itype, source_card_id=sid,
                    subject=str(edge.get("subject")), outcome=str(edge.get("predicate")),
                    retrieved_by=SKILL_ID, retrieval_run=run_id,
                ).located_in(quote, store=ContentStore())
            supports.append(eid)
        row = enrichment.get(pathway)
        if row is not None:
            eid = f"enrichment.{pathway}"
            quote = canonical_row(row)
            evidence[eid] = EvidenceItem(
                id=eid, design="pathway_enrichment", quote=quote,
                citation=f"{run_id} enrichment.jsonl", identifier=pathway,
                identifier_type="local_artifact", source_card_id=enrichment_card.id,
                subject=protocol.question.formula_id, outcome=pathway,
                retrieved_by=SKILL_ID, retrieval_run=run_id,
            ).located_in(enrichment_text, store=ContentStore())
            supports.append(eid)
        claims.append(CandidateClaim(
            id=f"hypothesis.{pathway}", text=claim["statement"],
            claim_kind="mechanism_hypothesis", subject=protocol.question.formula_id,
            predicate="may_act_through", object=pathway, supports=tuple(supports),
            asserted_population="human proteins (in silico)",
            supported_population="human proteins (in silico)",
            asserted_outcome="Reactome pathway over-representation",
            supported_outcome="Reactome pathway over-representation",
            hedged=True,
            rationale=(f"q = {row['q_value']:.3g}, permutation p = {row['empirical_p']:.3g}; "
                       f"survived {', '.join(t['test'] for t in rebuttal['tests']) or 'no'} "
                       "rebuttal test(s)") if row else "",
            falsified_by=("a panel screen of the constituents that reports inactive "
                          "results across this pathway's members"),
            produced_by=SKILL_ID))

    return build(
        id=run_id, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question=protocol.question.text,
        sources=[*cards.values(), enrichment_card],
        evidence=list(evidence.values()), claims=claims, outputs=files,
        limitations=_limitations(result, rebuttal, deviations),
        assumptions=("the formula's composition is the one its source text records; "
                     "doses and processing are recorded but not modelled",),
        source_axis=_digest(sorted(cards)), created_at=_now(),
        provenance={"protocol_digest": protocol.digest, "snapshots": sorted(cards),
                    "result_digest": result["digest"], "deviations": list(deviations),
                    "refuted": list(rebuttal["refuted"])})


def _limitations(result: Mapping[str, Any], rebuttal: Mapping[str, Any],
                 deviations: Sequence[Mapping[str, Any]]) -> list[str]:
    out = list(result.get("limitations") or [])
    for r in rebuttal.get("refuted") or ():
        out.append(f"refuted at rebuttal: {r['object']} — {'; '.join(r['reasons'])}")
    for n in rebuttal.get("not_run") or ():
        out.append(f"rebuttal not run: {n['test']} — {n['why']}")
    for d in deviations:
        out.append(f"protocol deviation: {d['fallback']} for {d['source']} ({d['reason']})")
    if not rebuttal.get("survived"):
        out.append("no claim survived analysis and rebuttal; the run releases no hypothesis")
    return out or ["no limitation was recorded; treat that as a gap, not a guarantee"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _Checkpoint:
    """Stage records under one directory. A stage is reusable only with its input."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, stage: str) -> Path:
        return self.directory / f"{STAGES.index(stage):02d}-{stage}.json"

    def _read(self, stage: str) -> dict[str, Any] | None:
        path = self._path(stage)
        if not path.is_file():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        body = {k: doc[k] for k in ("stage", "input", "data")}
        if doc.get("digest") != _digest(body):
            return None                               # a damaged record is not reused
        return doc

    def done(self, stage: str, input_digest: str) -> bool:
        doc = self._read(stage)
        return doc is not None and doc["input"] == input_digest

    def data(self, stage: str) -> Any:
        doc = self._read(stage)
        return None if doc is None else doc["data"]

    def save(self, stage: str, input_digest: str, data: Any) -> None:
        body = {"stage": stage, "input": input_digest,
                "data": json.loads(json.dumps(data, ensure_ascii=False, default=str))}
        path = self._path(stage)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**body, "digest": _digest(body)}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        tmp.replace(path)

    def invalidate_from(self, stage: str) -> None:
        for later in STAGES[STAGES.index(stage):]:
            self._path(later).unlink(missing_ok=True)


def _card(snap: Snapshot) -> SourceCard:
    content = snap.manifest["content"]
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                       default=str).encode("utf-8")).hexdigest()
    built = float(snap.manifest.get("built_at") or 0)
    return SourceCard(
        id=snap.snapshot_id, name=snap.key, kind="dataset", version=snap.version,
        access_method="local_file", license_spdx=str(content.get("license") or ""),
        license_note=str(content.get("citation") or ""), snapshot_hash=digest,
        snapshot_at=datetime.fromtimestamp(built, timezone.utc).isoformat(
            timespec="seconds"), offline_capable=True,
        known_limits=tuple(str(w) for w in (content.get("qc") or {}).get("warnings") or ()))


def _tier(design: str):
    from ..contracts.evidence_item import tier_for_design
    return tier_for_design(design)


def _identifier(edge: Mapping[str, Any]) -> tuple[str, str]:
    for pub in edge.get("publications") or ():
        prefix, _, value = str(pub).partition(":")
        if prefix in ("pmid", "doi", "pmcid") and value:
            return value, prefix
    return str(edge.get("source_record_id") or ""), "dataset"


def _contract(skill_dir: str | Path | None) -> Any:
    from ..providers.skills import SkillContract
    directory = Path(skill_dir) if skill_dir else (
        Path(__file__).resolve().parents[3] / "skills" / "tcm" / "network-pharmacology")
    path = directory / "skill.yaml"
    return SkillContract.load(path) if path.is_file() else None


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, default=str) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

