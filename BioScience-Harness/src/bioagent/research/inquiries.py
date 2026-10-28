"""Inquiries that last across a conversation: kept as their trails, verified on every load.

An agent that runs an inquiry one tool call at a time cannot keep it in its own context: a
model that holds the sealed predictions can rewrite them, and one that holds the belief can
round it up. The runtime keeps them instead. :class:`InquiryStore` writes each inquiry of a
project to one JSON file, ``<root>/<inquiry id>.json``, holding

* the trail, which *is* the inquiry: ``psh.scientist.Inquiry.from_dict`` rebuilds
  everything else by replaying it, and refuses a trail that does not recompute (INQ150);
* how it was opened (the scaffold and its values, or the analyses an agent proposed, with
  the tools that run them) and what each runtime classifier read.

The operations an agent needs are the functions below. Each loads and verifies the
inquiry, changes it through the engine, saves it under the store's lock, and returns a view
for the model: the belief, the engine's next step with the tools that would carry it out,
and, at the end, the conclusion with the studies that would still decide it.

Two rules are this module's own:

* **The bar cannot be lowered.** A stopping rule may be stricter than the engine's
  defaults, never laxer: acceptance at 0.95 or above, severity 0.8 or above, replication
  required, the catch-all's floor at least 0.05 and its alarm at most 0.5.
* **Who classified an outcome is in the trail.** An outcome the runtime classified carries
  ``runtime:<tool>:<digest>`` as its evidence reference; one the agent reports carries
  ``agent:`` and the evidence it cites, which it must give. A test with a runtime
  classifier accepts only data, never a reported outcome, and so does a replicate an agent
  adds to one. Data already recorded (the same numbers, however labelled or written) are
  refused, as is a replicate citing its original's source: a PMID or DOI however it is
  written (a label or none, a URL, full-width characters, a note after it).

An agent that can call several tools at once asks for a plan: the engine's batch of up to
four analyses, valued by the information they carry together and sealed into the trail
before any of them runs. Every conclusion also says how it holds when the sealed
predictions are made less sure (:data:`TEMPERS`), which separates an answer carried by
which outcome each explanation favoured from one carried by how confident they were.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import rivals

__all__ = ["SCHEMA", "TEMPERS", "InquiryStore", "admit", "conclude", "declare", "digest",
           "listing", "open_inquiry", "plain", "plan", "record_classified", "record_reported",
           "status", "view", "withdraw"]

SCHEMA = "bioagent.inquiry/1"
_ID = re.compile(r"^inq_[0-9a-f]{16}$")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

#: Stopping-rule fields and the direction in which a value is stricter.
_STRICTER = {"accept_at": "higher", "min_severity": "higher", "catch_all_floor": "higher",
             "catch_all_alarm": "lower", "max_steps": "lower"}
#: How much less sure a conclusion's sensitivity makes the sealed predictions: each is
#: mixed with the uniform distribution over its outcomes by these weights in turn.
TEMPERS = (0.1, 0.2, 0.3)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value: Any) -> str:
    """SHA-256 of a JSON value in canonical form."""
    return hashlib.sha256(_canonical(plain(value)).encode("utf-8")).hexdigest()


def plain(value: Any) -> Any:
    """``value`` as JSON data: numpy scalars as numbers, tuples as lists, a non-finite
    number as None (JSON has no NaN)."""
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "item") and callable(value.item) and not isinstance(value, str):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# ===================================================================== the store

class InquiryStore:
    """One project's inquiries, one JSON file each under ``root``."""

    def __init__(self, root: str | Path, *, lock: threading.Lock | None = None) -> None:
        self.root = Path(root)
        if lock is None:
            with _LOCKS_GUARD:
                lock = _LOCKS.setdefault(str(self.root.resolve()), threading.Lock())
        self.lock = lock

    def path(self, inquiry_id: str) -> Path:
        if not isinstance(inquiry_id, str) or not _ID.match(inquiry_id):
            raise ValueError(f"{inquiry_id!r} is not an inquiry id (inq_ and 16 hex digits)")
        return self.root / f"{inquiry_id}.json"

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("inq_*.json") if _ID.match(p.stem))

    def load(self, inquiry_id: str) -> tuple[Any, dict[str, Any]]:
        """The inquiry, replayed and verified, and its record. ``LookupError`` when there
        is none; ``InquiryRefused`` (INQ150) when the file does not verify."""
        from psh.scientist import Inquiry, InquiryRefused
        path = self.path(inquiry_id)
        if not path.is_file():
            raise LookupError(f"no inquiry {inquiry_id} in this project")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InquiryRefused("INQ150", f"{inquiry_id}: unreadable ({exc})") from exc
        if not isinstance(doc, Mapping) or doc.get("schema") != SCHEMA:
            raise InquiryRefused("INQ150", f"{inquiry_id}: not a {SCHEMA} document")
        inquiry = Inquiry.from_dict(doc.get("inquiry") or {})
        if inquiry.inquiry_id != inquiry_id:
            raise InquiryRefused("INQ150", f"the file {inquiry_id} holds inquiry "
                                 f"{inquiry.inquiry_id}")
        record = dict(doc.get("record") or {})
        record.setdefault("scaffold", "")
        record.setdefault("values", {})
        record.setdefault("tests", {})
        record.setdefault("read", {})
        record.setdefault("inputs", {})
        return inquiry, record

    def save(self, inquiry: Any, record: Mapping[str, Any]) -> None:
        path = self.path(inquiry.inquiry_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"schema": SCHEMA, "inquiry": inquiry.as_dict(),
                           "record": dict(record)}, ensure_ascii=False, sort_keys=True,
                          indent=1, allow_nan=False)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)


# ============================================================ opening an inquiry

def _rule(base: Mapping[str, Any], overrides: Mapping[str, Any] | None) -> Any:
    """The stopping rule: the scaffold's or the engine's, made stricter, never laxer."""
    from psh.scientist import StoppingRule
    fields = {**base}
    defaults = StoppingRule(**base)
    problems = []
    for key, value in dict(overrides or {}).items():
        if key not in StoppingRule.__dataclass_fields__:
            problems.append(f"{key} is not a stopping-rule field")
            continue
        if key == "require_replication":
            if value is not True:
                problems.append("require_replication cannot be turned off")
                continue
        elif key in _STRICTER:
            floor = getattr(defaults, key)
            number = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not number or (value < floor if _STRICTER[key] == "higher"
                              else value > floor):
                problems.append(f"{key} may be made stricter than {floor}, not set to "
                                f"{value!r}")
                continue
        fields[key] = value
    if problems:
        raise ValueError("; ".join(problems))
    return StoppingRule(**fields)


def _explanation(data: Mapping[str, Any]) -> Any:
    from psh.scientist import Explanation, Role
    from psh.sir.values import ClaimKind
    if not isinstance(data, Mapping):
        raise ValueError("each explanation is an object {id, proposition, claim_kind or "
                         "role 'artefact', prior}")
    kind = data.get("claim_kind") or None
    role = data.get("role") or ("substantive" if kind else "artefact")
    if role not in ("substantive", "artefact"):
        raise ValueError(f"{data.get('id')}: role is substantive (with a claim kind) or "
                         "artefact")
    try:
        claim = ClaimKind(kind) if kind else None
    except ValueError:
        raise ValueError(f"{data.get('id')}: claim_kind {kind!r} is not one of "
                         f"{[k.value for k in ClaimKind]}") from None
    prior = data.get("prior", 0.0)
    return Explanation(id=str(data.get("id") or ""),
                       proposition=str(data.get("proposition") or ""), claim_kind=claim,
                       role=Role(role), prior=float(prior) if isinstance(
                           prior, (int, float)) and not isinstance(prior, bool) else -1.0)


def _analysis(data: Mapping[str, Any]) -> Any:
    from psh.scientist import Analysis
    from psh.sir.values import StudyDesign
    if not isinstance(data, Mapping):
        raise ValueError("each analysis is an object {id, title, design, outcomes, cost, "
                         "replicates}")
    try:
        design = StudyDesign(data.get("design") or "")
    except ValueError:
        raise ValueError(f"{data.get('id')}: design {data.get('design')!r} is not one of "
                         f"{[d.value for d in StudyDesign]}") from None
    cost = data.get("cost", 1.0)
    return Analysis(id=str(data.get("id") or ""), title=str(data.get("title") or ""),
                    design=design, outcomes=tuple(str(o) for o in data.get("outcomes") or ()),
                    cost=float(cost) if isinstance(cost, (int, float))
                    and not isinstance(cost, bool) else -1.0,
                    replicates=str(data.get("replicates") or ""))


def _how(data: Mapping[str, Any]) -> dict[str, str]:
    return {"run_with": " ".join(str(data.get("run_with") or "").split()),
            "classify": " ".join(str(data.get("classify") or "").split())}


def open_inquiry(store: InquiryStore, *, scaffold: str = "",
                 values: Mapping[str, Any] | None = None, question: str = "",
                 explanations: Sequence[Mapping[str, Any]] = (),
                 analyses: Sequence[Mapping[str, Any]] = (),
                 predictions: Mapping[str, Mapping[str, Mapping[str, float]]] | None = None,
                 rule: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Open an inquiry from a scaffold (``scaffold`` and its ``values``) or from an agent's
    own rivals and analyses, seal every prediction given, and save it."""
    from psh.contracts import new_id
    from psh.scientist import Inquiry
    inquiry_id = new_id("inq")
    if scaffold:
        if question or explanations or analyses or predictions:
            raise ValueError("open either a scaffold with its values, or your own question, "
                             "explanations, analyses and predictions; not both")
        s = rivals.scaffold(scaffold)
        checked = rivals.parameters(scaffold, values or {})
        inquiry = rivals.open_scaffold(scaffold, checked, rule=_rule(s.rule, rule),
                                       inquiry_id=inquiry_id)
        record = {"scaffold": scaffold, "values": checked,
                  "tests": rivals.rendered(scaffold, checked), "notes": list(s.notes),
                  "read": {}}
    else:
        if not str(question or "").strip():
            raise ValueError("a question is needed (or a scaffold)")
        inquiry = Inquiry(" ".join(str(question).split()),
                          [_explanation(e) for e in explanations],
                          [_analysis(a) for a in analyses], _rule({}, rule),
                          inquiry_id=inquiry_id)
        for analysis_id, rows in dict(predictions or {}).items():
            inquiry.declare(analysis_id, rows)
        record = {"scaffold": "", "values": {},
                  "tests": {str(a.get("id")): _how(a) for a in analyses
                            if isinstance(a, Mapping)},
                  "notes": [], "read": {}}
    with store.lock:
        store.save(inquiry, record)
    out = view(inquiry, record)
    if record["notes"]:
        out["notes"] = list(record["notes"])
    return out


# ================================================================ the operations

def status(store: InquiryStore, inquiry_id: str) -> dict[str, Any]:
    inquiry, record = store.load(inquiry_id)
    return view(inquiry, record)


def plan(store: InquiryStore, inquiry_id: str, size: int) -> dict[str, Any]:
    """Seal a batch of analyses to run together (``Inquiry.plan``) and say how to run
    each. An empty batch records nothing and says why."""
    with store.lock:
        inquiry, record = store.load(inquiry_id)
        options = inquiry.plan(size)
        if options:
            store.save(inquiry, record)
    out = view(inquiry, record)
    out["batch"] = [{"analysis_id": o.analysis_id, "adds_bits": round(o.expected_gain, 4),
                     "cost": o.cost, **_how_to(record, o.analysis_id)} for o in options]
    out["batch_bits"] = round(math.fsum(o.expected_gain for o in options), 4)
    if not options:
        out["batch_note"] = ("nothing to run now; the next step is to "
                             f"{out['next']['action']}: {out['next']['reason']}")
    return out


def declare(store: InquiryStore, inquiry_id: str, analysis_id: str,
            predictions: Mapping[str, Mapping[str, float]], *,
            analysis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Seal predictions for an analysis, adding the analysis first when it is given."""
    with store.lock:
        inquiry, record = store.load(inquiry_id)
        if analysis is not None:
            added = _analysis(analysis)
            if added.id != analysis_id:
                raise ValueError(f"the analysis given is {added.id!r}, not {analysis_id!r}")
            inquiry.add_analysis(added)
            record["tests"][added.id] = _how(analysis)
            # A replicate of a test the runtime classifies is classified by the same rule,
            # from data: a reported outcome would skip the criteria sealed for it.
            original = record["tests"].get(added.replicates) or {}
            if original.get("classifier"):
                record["tests"][added.id] = {
                    **original, "test": original.get("test") or added.replicates}
        if predictions:
            inquiry.declare(analysis_id, predictions)
        elif analysis is None:
            raise ValueError("give predictions for the analysis, or an analysis to add")
        store.save(inquiry, record)
    return view(inquiry, record)


def _classifier(record: Mapping[str, Any], analysis_id: str) -> str:
    return str((record.get("tests") or {}).get(analysis_id, {}).get("classifier") or "")


_DOI = re.compile(r"10\.\d+(?:\.\d+)*/[^\s\"']+")
_PMID = re.compile(r"(?:pmid|pubmed(?:\.ncbi\.nlm\.nih\.gov)?)\W*(?:(?:id|term)\W*)?"
                   r"(\d{1,9})(?!\d)")
_OPENS = {")": "(", "]": "[", ">": "<"}
_TRAILING = ".,;:。、"         # punctuation after a DOI, which NFKC leaves as it is


def _sources(ref: str) -> frozenset[str]:
    """The sources a reference names: every PMID and DOI in it, however written (a label
    or none, a URL, full-width characters, punctuation or a note around it), or, when it
    names neither, the reference itself without case or spacing."""
    text = unicodedata.normalize("NFKC", str(ref or "")).casefold()
    found = set()
    for m in _DOI.finditer(text):
        doi = m.group(0).rstrip(_TRAILING)
        # a bracket closing around the DOI, as in "(doi:10.1/x).", is not part of it
        while doi[-1] in _OPENS and doi.count(doi[-1]) > doi.count(_OPENS[doi[-1]]):
            doi = doi[:-1].rstrip(_TRAILING)
        found.add("doi:" + doi)
    rest = _DOI.sub(" ", text)
    found |= {f"pmid:{int(m.group(1))}" for m in _PMID.finditer(rest)}
    bare = "".join(rest.split()).rstrip(".")
    if not found and bare.isdigit() and len(bare) <= 9:
        found.add(f"pmid:{int(bare)}")        # a number alone is a PMID
    return frozenset(found) or frozenset({"".join(text.split())})


def _cited(update: Any) -> str:
    """The source an outcome cites: the agent's, or the one given with runtime data."""
    ref = str(update.evidence_ref or "")
    if ref.startswith("agent:"):
        return ref[len("agent:"):]
    return ref.split(" · ", 1)[1] if ref.startswith("runtime:") and " · " in ref else ""


def _own_source(inquiry: Any, analysis_id: str, source: str) -> None:
    """A replicate may not cite the source its original was recorded from."""
    original = inquiry.analysis(analysis_id).replicates
    if not source or not original or not inquiry.observed(original):
        return
    theirs = next(_cited(u) for u in inquiry.updates if u.analysis_id == original)
    if theirs and _sources(theirs) & _sources(source):
        raise ValueError(f"{analysis_id} replicates {original}: it needs evidence of its "
                         f"own, not the source already recorded for {original}")


def record_reported(store: InquiryStore, inquiry_id: str, analysis_id: str, outcome: str,
                    evidence_ref: str) -> dict[str, Any]:
    """Record an outcome the agent classified, with the evidence it cites."""
    evidence = " ".join(str(evidence_ref or "").split())
    if not evidence:
        raise ValueError("a reported outcome needs evidence_ref: the source of the result "
                         "(PMID, DOI, NCT id, a tool's receipt hash, a dataset version)")
    with store.lock:
        inquiry, record = store.load(inquiry_id)
        inquiry.analysis(analysis_id)
        if _classifier(record, analysis_id):
            raise ValueError(f"{analysis_id} is classified by the runtime: pass the result "
                             f"as data ({record['tests'][analysis_id]['data']}), or "
                             "withdraw it if those data do not exist")
        _own_source(inquiry, analysis_id, evidence)
        update = inquiry.observe(analysis_id, outcome, evidence_ref=f"agent:{evidence}")
        store.save(inquiry, record)
    return view(inquiry, record, update=update)


def record_classified(store: InquiryStore, inquiry_id: str, analysis_id: str,
                      arguments: Mapping[str, Any], result: Mapping[str, Any] | None, *,
                      tool: str = "", evidence_ref: str = "") -> dict[str, Any]:
    """Record a runtime-classified test from the arguments its tool ran with and its
    result: the scaffold's rule decides the outcome, with the criteria sealed in the
    trail. Raises ``rivals.Unclassifiable`` and records nothing when it cannot."""
    with store.lock:
        inquiry, record = store.load(inquiry_id)
        inquiry.analysis(analysis_id)
        classifier = _classifier(record, analysis_id)
        if not classifier or not record.get("scaffold"):
            raise ValueError(f"{analysis_id} is not classified by the runtime: record its "
                             "outcome with the evidence you cite")
        if not rivals.matches(inquiry, record["scaffold"], record["values"]):
            raise rivals.Unclassifiable(
                "this inquiry's stored values are not the ones sealed in its trail, so its "
                "criteria cannot be applied; record outcomes with the evidence you cite")
        # a test an agent added as a replicate is classified by its original's rule
        test = str(record["tests"][analysis_id].get("test") or analysis_id)
        outcome, read = rivals.classify(record["scaffold"], test, record["values"],
                                        arguments, result)
        computed = digest({"arguments": arguments, "result": result})
        # the same data, however labelled or written, are not a second data set
        same = digest({"classifier": classifier,
                       "inputs": rivals.inputs(classifier, arguments)})
        again = [u.analysis_id for u in inquiry.updates if u.result_digest == computed]
        again += [a for a, d in record["inputs"].items() if d == same]
        if again:
            raise rivals.Unclassifiable(
                f"these data were already recorded for {again[0]}: a replication, or any "
                "second analysis, needs data of its own")
        ref = f"runtime:{tool or classifier}:{computed[:16]}"
        source = " ".join(str(evidence_ref or "").split())
        _own_source(inquiry, analysis_id, source)
        update = inquiry.observe(analysis_id, outcome,
                                 evidence_ref=ref + (f" · {source}" if source else ""),
                                 result_digest=computed)
        record["read"][analysis_id] = plain(read)
        record["inputs"][analysis_id] = same
        store.save(inquiry, record)
    return view(inquiry, record, update=update)


def withdraw(store: InquiryStore, inquiry_id: str, analysis_id: str,
             reason: str) -> dict[str, Any]:
    with store.lock:
        inquiry, record = store.load(inquiry_id)
        inquiry.withdraw(analysis_id, " ".join(str(reason or "").split()))
        store.save(inquiry, record)
    return view(inquiry, record)


def admit(store: InquiryStore, inquiry_id: str,
          explanations: Sequence[Mapping[str, Any]],
          shares: Mapping[str, float]) -> dict[str, Any]:
    """Add explanations, each carved out of the catch-all's current mass."""
    with store.lock:
        inquiry, record = store.load(inquiry_id)
        inquiry.admit([_explanation({**dict(e), "prior": 0.0}) for e in explanations],
                      dict(shares))
        store.save(inquiry, record)
    return view(inquiry, record)


def listing(store: InquiryStore) -> list[dict[str, Any]]:
    out = []
    for inquiry_id in store.ids():
        try:
            inquiry, record = store.load(inquiry_id)
        except Exception as exc:                                # noqa: BLE001
            out.append({"inquiry_id": inquiry_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        step = inquiry.next_step()
        out.append({"inquiry_id": inquiry_id, "question": inquiry.question,
                    "scaffold": record.get("scaffold") or None, "leader": inquiry.leader,
                    "belief": _rounded(inquiry.belief), "steps": len(inquiry.updates),
                    "next": step.action})
    return out


# ===================================================================== the views

def _rounded(belief: Mapping[str, float]) -> dict[str, float]:
    return {h: round(p, 4) for h, p in sorted(belief.items(), key=lambda kv: (-kv[1], kv[0]))}


def _by(update: Any) -> str:
    ref = str(update.evidence_ref or "")
    return "runtime" if ref.startswith("runtime:") else "agent" if ref.startswith(
        "agent:") else "unrecorded"


def _how_to(record: Mapping[str, Any], analysis_id: str) -> dict[str, Any]:
    return dict((record.get("tests") or {}).get(analysis_id) or {})


def view(inquiry: Any, record: Mapping[str, Any], *, update: Any = None) -> dict[str, Any]:
    """Where the inquiry stands, for the model: belief, the next step and how to take it."""
    step = inquiry.next_step()
    nxt = step.as_dict()
    if step.analysis_id:
        nxt.update(_how_to(record, step.analysis_id))
    if step.pending:
        nxt["pending"] = {a: list(inquiry.missing(a)) for a in step.pending}
    out: dict[str, Any] = {
        "inquiry_id": inquiry.inquiry_id, "question": inquiry.question,
        "scaffold": record.get("scaffold") or None, "leader": inquiry.leader,
        "belief": _rounded(inquiry.belief), "next": nxt,
        "options": [{"analysis_id": o.analysis_id, "bits": round(o.expected_gain, 4),
                     "cost": o.cost,
                     "run_with": _how_to(record, o.analysis_id).get("run_with", "")}
                    for o in inquiry.ranked()[:5]],
        "observed": [{"analysis_id": u.analysis_id, "outcome": u.outcome,
                      "purpose": u.purpose.value, "bits": round(u.information, 4),
                      "by": _by(u)} for u in inquiry.updates],
        "withdrawn": inquiry.withdrawn, "spent": inquiry.spent, "head": inquiry.head}
    if inquiry.planned:
        out["planned"] = list(inquiry.planned)
    if update is not None:
        out["update"] = {
            "analysis_id": update.analysis_id, "outcome": update.outcome,
            "purpose": update.purpose.value, "against": update.against,
            "recommended": update.recommended, "bits": round(update.information, 4),
            "expected_bits": round(update.expected_gain, 4),
            "prior": _rounded(update.prior), "posterior": _rounded(update.posterior),
            "refuted": list(update.refuted), "held_fixed": list(update.held_fixed),
            "by": _by(update), "evidence_ref": update.evidence_ref}
        read = (record.get("read") or {}).get(update.analysis_id)
        if read is not None and out["update"]["by"] == "runtime":
            out["update"]["read"] = read
    return out


def conclude(store: InquiryStore, inquiry_id: str) -> dict[str, Any]:
    """The verdict the stopping rule supports now, what it licenses, and what is left."""
    inquiry, record = store.load(inquiry_id)
    c = inquiry.conclude()
    body = c.as_dict()
    by = [_by(u) for u in inquiry.updates]
    # The scaffold's own warnings first (a safety verdict that is not reassurance), then
    # who classified the outcomes, then the engine's.
    limitations = [n for n in record.get("notes") or () if n != rivals.GAP_NOTE]
    agent = by.count("agent")
    if agent:
        limitations.append(f"{agent} of the {len(by)} outcomes were classified by the agent "
                           "from the sources it cites; check them against those sources")
    sensitivity, holds_to, statement = _sensitivity(inquiry, c.leader)
    if statement and holds_to < TEMPERS[-1]:
        limitations.append(statement)         # an answer carried by how sure they were
    limitations += list(body["limitations"])
    if statement and holds_to >= TEMPERS[-1]:
        limitations.append(statement)
    rivals_open = set(c.untested) | set(c.unreplicated)
    to_decide = []
    for a in inquiry.analyses:
        if inquiry.observed(a.id):
            continue
        # a severe test, or a replicate of one or of a test the acceptance rests on (an
        # outcome that refuted the rival)
        against = sorted(r for r in rivals_open if c.leader and (
            inquiry.severe(c.leader, r, a.id) or (a.replicates and (inquiry.severe(
                c.leader, r, a.replicates) or a.replicates in c.severe_tests.get(r, ())))))
        entry = {"analysis_id": a.id, "title": a.title, "design": a.design.value,
                 **_how_to(record, a.id)}
        if a.id in inquiry.withdrawn:
            entry["withdrawn"] = inquiry.withdrawn[a.id]
        if against:
            entry["would_test_against"] = against
        if against or a.id in inquiry.withdrawn:
            to_decide.append(entry)
    body["limitations"] = limitations
    return {"inquiry_id": inquiry.inquiry_id, "conclusion": body,
            "statement": c.describe(), "by_runtime": by.count("runtime"),
            "by_agent": agent, "to_decide": to_decide,
            "sensitivity": {"tempers": sensitivity, "leader_holds_to": holds_to},
            "scaffold": record.get("scaffold") or None}


def _sensitivity(inquiry: Any, leader: str) -> tuple[list[dict[str, Any]], float, str]:
    """The conclusion's leader with the sealed predictions made less sure: the rows, the
    largest weight up to which the same explanation leads, and one sentence on it."""
    if not leader or not inquiry.updates:
        return [], 0.0, ""
    rows = inquiry.sensitivity(TEMPERS)
    out = [{"temper": r["temper"], "leader": r["leader"], "belief": _rounded(r["belief"])}
           for r in rows]
    holds_to = 0.0
    for r in rows:
        if r["leader"] != leader:
            break
        holds_to = r["temper"]
    p = inquiry.belief.get(leader, 0.0)
    if holds_to >= TEMPERS[-1]:
        last = rows[-1]
        return out, holds_to, (
            f"with every sealed prediction made {last['temper']:.0%} less sure, {leader} "
            f"still leads ({p:.2f} as sealed, {last['leader_posterior']:.2f} then): the "
            "answer follows which outcome each explanation favoured, not only how sure "
            "the predictions were")
    first = next(r for r in rows if r["leader"] != leader)
    return out, holds_to, (
        f"with every sealed prediction made {first['temper']:.0%} less sure, "
        f"{first['leader'] or 'no named explanation'} leads instead of {leader} "
        f"({first['leader_posterior']:.2f} against {first['belief'].get(leader, 0.0):.2f}): "
        "the answer rests on how sure the predictions were, so it is no firmer than they are")
