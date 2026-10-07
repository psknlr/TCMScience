"""The four-arm comparison: the same model, tools, data and budget, with and without each layer.

The governance ablation (``ablation``) judges identical outputs; it cannot say whether a model
writes better science inside the governance, which also depends on what the model writes when
it knows it will be checked. This module runs that experiment. Every arm gets the same model,
the same sources (identifiers, design, population, outcome and text, in the same words) and
the same budget of model calls; the arms differ in what TCMScience adds:

``A``   the model answers in prose with citations. Nothing is checked; everything is released.
``A+``  arm A plus one self-review round, so arm D's extra round is matched by an ordinary
        review of the same size: D's advantage must not be "one more call".
``B``   the model answers in TCMScience's structured claims (kind, subject, population,
        outcome, direction, certainty, citation). Nothing is checked.
``C``   arm B, told that claims are checked, and the runtime's gates applied: ingest, the
        kernel's output gate, licensing, the claim contract, the release check
        (the same gates the ablation measures). A claim that fails is withheld.
``D``   arm C, asked for rival explanations and contradicting evidence, with one revision
        round in which the withheld claims and their reasons go back to the model.

**Scoring is not done by the gates.** A claim is judged against gold annotations a reviewer
wrote for the task: phrases that identify a claim the sources license, and phrases that
identify an error (a population, an outcome or a level the sources do not reach). A claim
matching an error is an error even if it also matches a licensed claim. A claim matching
nothing is exported, with its arm hidden, for blinded review (``export_for_review``); the
reviewer marks it correct, an error, or not a claim at all (a prose answer's framing sentence).

**Statistics are by independent unit.** Arms are compared on the same tasks (paired), and the
interval of a difference resamples the tasks' independent units (a dataset, a primary study),
not claims: one paper producing twenty claims is one unit.

What it does not do: no model is bundled, and no result in this repository comes from running
it. ``ScriptedClient`` exists to test the harness's mechanics and is labelled so in every
report; ``AnthropicClient`` needs a model id and credentials supplied by whoever runs the
experiment. Tasks marked ``synthetic`` are fixtures for the same purpose and are reported as
such. See docs/comparison.md for the protocol.
"""

from __future__ import annotations

import json
import random
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .ablation import Case, Source

__all__ = ["ARMS", "VERDICTS", "AnthropicClient", "Arm", "Budget", "ComparisonReport",
           "ComparisonTask", "GoldClaim", "ModelReply", "ReleasedClaim", "ScriptedClient",
           "Transcript", "export_for_review", "fixture_script", "fixture_tasks",
           "load_reviews", "load_tasks",
           "render_markdown", "run_arm", "run_comparison", "score"]


# ------------------------------------------------------------------ tasks and gold
@dataclass(frozen=True)
class GoldClaim:
    """A claim a reviewer has judged for one task, identified by the phrases it must contain.

    ``licensed`` is True for a claim the sources support as worded, False for an error a
    careful reviewer would refuse. Phrases compare without case, spaces or punctuation; each
    entry of ``all_of`` may list alternatives separated by "|".
    """

    id: str
    all_of: tuple[str, ...]
    licensed: bool = True
    none_of: tuple[str, ...] = ()
    note: str = ""

    def matches(self, text: str) -> bool:
        bare = _bare(text)
        return (all(any(_bare(alt) in bare for alt in phrase.split("|"))
                    for phrase in self.all_of)
                and not any(_bare(p) in bare for p in self.none_of))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoldClaim":
        return cls(id=str(data["id"]), all_of=tuple(data["all_of"]),
                   licensed=bool(data.get("licensed", True)),
                   none_of=tuple(data.get("none_of") or ()), note=str(data.get("note") or ""))


@dataclass(frozen=True)
class ComparisonTask:
    """One research question, its sources, its independent unit and its gold annotations."""

    id: str
    scenario: str
    question: str
    sources: tuple[Source, ...]
    unit: str
    gold: tuple[GoldClaim, ...]
    #: Ids of licensed gold claims a complete answer makes: the task's useful findings.
    expected: tuple[str, ...] = ()
    synthetic: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComparisonTask":
        return cls(id=str(data["id"]), scenario=str(data.get("scenario") or ""),
                   question=str(data["question"]),
                   sources=tuple(Source.from_dict(s) for s in data["sources"]),
                   unit=str(data.get("unit") or data["id"]),
                   gold=tuple(GoldClaim.from_dict(g) for g in data.get("gold") or ()),
                   expected=tuple(data.get("expected") or ()),
                   synthetic=bool(data.get("synthetic")))


def load_tasks(path: str | Path) -> list[ComparisonTask]:
    """Tasks from a JSON-lines file, one task per line."""
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(ComparisonTask.from_dict(json.loads(line)))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError(f"{path}:{n}: {exc}") from exc
    return out


# -------------------------------------------------------------------------- arms
@dataclass(frozen=True)
class Arm:
    name: str
    structured: bool
    gated: bool
    told_checked: bool
    counter_evidence: bool
    revision_rounds: int
    self_review_rounds: int
    description: str


ARMS: dict[str, Arm] = {a.name: a for a in (
    Arm("A", False, False, False, False, 0, 0, "generic: prose with citations, unchecked"),
    Arm("A+", False, False, False, False, 0, 1,
        "generic with one self-review round, matched to D's revision round"),
    Arm("B", True, False, False, False, 0, 0, "structured TCMScience claims, unchecked"),
    Arm("C", True, True, True, False, 0, 0, "structured claims, checked by the runtime's gates"),
    Arm("D", True, True, True, True, 1, 0,
        "checked claims, rival explanations, one revision round on the reasons for refusal"),
)}


# ------------------------------------------------------------------------- model
@dataclass(frozen=True)
class ModelReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    #: "end_turn", "max_tokens", "refusal", or "error" when the call itself failed.
    stop_reason: str = "end_turn"
    detail: str = ""


class ModelClient(Protocol):
    label: str

    def complete(self, system: str, messages: Sequence[Mapping[str, str]], *,
                 max_tokens: int) -> ModelReply: ...


class AnthropicClient:
    """The Messages API through the official ``anthropic`` SDK (optional dependency).

    The model id has no default: which model an experiment runs is the experiment's
    decision, recorded with its results, and the same id must serve every arm. Request
    settings beyond the model and ``max_tokens`` (effort, thinking) go in ``params`` and are
    likewise the same for every arm. Credentials are the SDK's own resolution (an API key in
    the environment, or a profile). A refusal is returned as a reply, not raised, and a
    failed call becomes a reply with ``stop_reason="error"``, so the run records what
    happened instead of stopping.
    """

    def __init__(self, model: str, *, params: Mapping[str, Any] | None = None,
                 client: Any = None) -> None:
        if not model:
            raise ValueError("name the model the experiment runs; there is no default")
        self.model = model
        self.params = dict(params or {})
        self.label = f"anthropic:{model}"
        if client is None:
            import anthropic                                   # optional dependency
            client = anthropic.Anthropic()
        self._client = client

    def complete(self, system: str, messages: Sequence[Mapping[str, str]], *,
                 max_tokens: int) -> ModelReply:
        try:
            response = self._client.messages.create(
                model=self.model, max_tokens=max_tokens, system=system,
                messages=[dict(m) for m in messages], **self.params)
        except Exception as exc:                               # noqa: BLE001
            return ModelReply("", stop_reason="error", detail=f"{type(exc).__name__}: {exc}"[:300])
        usage = getattr(response, "usage", None)
        stop = str(getattr(response, "stop_reason", "") or "end_turn")
        text = "" if stop == "refusal" else "".join(
            getattr(block, "text", "") for block in getattr(response, "content", ())
            if getattr(block, "type", "") == "text")
        return ModelReply(text, int(getattr(usage, "input_tokens", 0) or 0),
                          int(getattr(usage, "output_tokens", 0) or 0), stop)


class ScriptedClient:
    """Replies from a fixed script, for testing the harness. Never a source of results.

    ``script`` maps ``(task_id, arm, turn)`` to the reply text; a missing entry replies with
    an empty answer.
    """

    label = "scripted (harness test; not a model)"

    def __init__(self, script: Mapping[tuple[str, str, int], str]) -> None:
        self.script = dict(script)
        self.calls: list[tuple[str, str, int]] = []
        self._context: tuple[str, str] = ("", "")

    def bind(self, task: str, arm: str) -> None:
        self._context = (task, arm)

    def complete(self, system: str, messages: Sequence[Mapping[str, str]], *,
                 max_tokens: int) -> ModelReply:
        turn = sum(1 for m in messages if m["role"] == "user") - 1
        key = (*self._context, turn)
        self.calls.append(key)
        text = self.script.get(key, "")
        return ModelReply(text, input_tokens=len(system) // 4, output_tokens=len(text) // 4)


# ---------------------------------------------------------------------------- run
@dataclass(frozen=True)
class Budget:
    """What one arm may spend on one task: ``max_tokens`` per call, and the calls its rounds
    allow (A, B and C one; A+ and D two). D spends its second call only when a claim was
    withheld; the calls each arm actually made are reported with its results."""

    max_tokens: int = 4000


@dataclass(frozen=True)
class ReleasedClaim:
    text: str
    citation: str = ""
    claim_kind: str = ""
    subject: str = ""
    population: str = ""
    outcome: str = ""
    direction: str = ""
    certainty: str = ""
    released: bool = True
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReleasedClaim":
        return cls(**{**{k: str(data.get(k) or "") for k in (
            "text", "citation", "claim_kind", "subject", "population", "outcome",
            "direction", "certainty")}, "released": bool(data.get("released", True)),
            "reasons": tuple(data.get("reasons") or ())})


@dataclass
class Transcript:
    task: str
    arm: str
    client: str
    repeat: int = 0
    system: str = ""
    turns: list[dict[str, str]] = field(default_factory=list)
    claims: list[ReleasedClaim] = field(default_factory=list)
    rivals: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reasons: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def released(self) -> list[ReleasedClaim]:
        return [c for c in self.claims if c.released]

    def as_dict(self) -> dict[str, Any]:
        return {"task": self.task, "arm": self.arm, "client": self.client,
                "repeat": self.repeat, "system": self.system, "turns": self.turns,
                "claims": [c.as_dict() for c in self.claims], "rivals": self.rivals,
                "contradicting": self.contradicting, "next_steps": self.next_steps,
                "calls": self.calls,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "stop_reasons": self.stop_reasons, "parse_errors": self.parse_errors}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Transcript":
        return cls(task=str(data["task"]), arm=str(data["arm"]), client=str(data["client"]),
                   repeat=int(data.get("repeat") or 0), system=str(data.get("system") or ""),
                   turns=list(data.get("turns") or ()),
                   claims=[ReleasedClaim.from_dict(c) for c in data.get("claims") or ()],
                   rivals=list(data.get("rivals") or ()),
                   contradicting=list(data.get("contradicting") or ()),
                   next_steps=list(data.get("next_steps") or ()),
                   calls=int(data.get("calls") or 0),
                   input_tokens=int(data.get("input_tokens") or 0),
                   output_tokens=int(data.get("output_tokens") or 0),
                   stop_reasons=list(data.get("stop_reasons") or ()),
                   parse_errors=list(data.get("parse_errors") or ()))


_KINDS = ("efficacy", "association", "safety_signal", "mechanism", "mechanism_hypothesis",
          "traditional_use", "attribution", "recommendation")
_CERTAINTIES = ("strong", "moderate", "tentative")


def _sources_block(task: ComparisonTask) -> str:
    """The sources, in the same words for every arm: identifier, design, scope and text."""
    lines = []
    for n, s in enumerate(task.sources, 1):
        lines.append(f"[{n}] {s.identifier} | design: {s.design} | subject: {s.subject} | "
                     f"population: {s.population or 'not stated'} | "
                     f"outcome: {s.outcome or 'not stated'}\n{s.text}")
    return "\n\n".join(lines)


def _system_prompt(task: ComparisonTask, arm: Arm) -> str:
    parts = [
        "You are assisting a research team with a question in Traditional Chinese Medicine "
        "and biomedicine. Use only the sources below. Cite the identifier of the source each "
        "claim rests on, in parentheses after the claim.",
        f"Question: {task.question}",
        "Sources:\n" + _sources_block(task),
    ]
    if arm.structured:
        parts.append(
            "Answer with one JSON object and nothing else, of the form "
            '{"claims": [{"text": ..., "citation": ..., "claim_kind": ..., "subject": ..., '
            '"population": ..., "outcome": ..., "direction": ..., "certainty": ...}], '
            '"next_steps": [...]}. claim_kind is one of ' + ", ".join(_KINDS) + "; direction "
            "is increase, decrease, no_difference or unclear; certainty is "
            + ", ".join(_CERTAINTIES[:-1]) + " or " + _CERTAINTIES[-1] + ".")
    else:
        parts.append("Answer in prose under two headings. Under 'Findings:', the findings "
                     "the sources support, each with its citation. Under 'Next steps:', the "
                     "analyses or experiments you would run next, one per line.")
    if arm.told_checked:
        parts.append("Each claim is checked against the source it cites before it is "
                     "released: its subject, population, outcome, wording and citation, and "
                     "the kind of claim the study design can support. A claim that fails a "
                     "check is withheld.")
    if arm.counter_evidence:
        parts.append('Add to the JSON object "rivals": the explanations other than your '
                     "main one that could produce the main finding, each with what it would "
                     'predict, and "contradicting": any source that contradicts a claim.')
    return "\n\n".join(parts)


_CITED = re.compile(r"PMID:?\s*\d{5,9}|NCT\d{8}|(?:doi:\s*)?\b10\.\d{4,9}/[^\s()（）\[\]<>;；,，。]+"
                    r"|passage\.[a-z0-9_]+", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|(?<=[。！？；])")
_HEADING = re.compile(
    r"^[ \t]*#+[ \t]*(findings|next steps|发现|结论|下一步)[ \t]*[:：]?[ \t]*$"
    r"|(?:^|(?<=[\s。.!?]))(?:\*\*)?(findings|next steps|发现|结论|下一步)(?:\*\*)?[ \t]*[:：]"
    r"(?:\*\*)?[ \t]*", re.I | re.M)
_NEXT = {"next steps", "下一步"}


def _prose_answer(text: str) -> tuple[list[ReleasedClaim], list[str]]:
    """A prose answer's findings, a claim per sentence with the identifier it cites, and its
    next steps. Sentences under "Next steps" are plans, not claims, as in the structured arms;
    a sentence that is neither claim nor plan goes to review, where it can be marked so."""
    text = text or ""
    parts: dict[str, list[str]] = {"findings": [], "next": []}
    section, last = "findings", 0
    for m in _HEADING.finditer(text):
        parts[section].append(text[last:m.start()])
        section = "next" if (m.group(1) or m.group(2)).lower() in _NEXT else "findings"
        last = m.end()
    parts[section].append(text[last:])
    claims = []
    for sentence in _SENTENCE.split(" ".join(parts["findings"])):
        sentence = sentence.strip(" -*•\n\t")
        if len(sentence) < 8:
            continue
        cited = _CITED.search(sentence)
        claims.append(ReleasedClaim(text=sentence, citation=(
            cited.group(0).rstrip(".") if cited else "")))
    plans = [line.strip(" -*•\t0123456789.)") for chunk in parts["next"]
             for line in chunk.splitlines()]
    return claims, [p for p in plans if p]


def _json_object(text: str) -> dict[str, Any]:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply's JSON is not an object")
    return data


def _structured_claims(data: Mapping[str, Any]) -> list[ReleasedClaim]:
    out = []
    for raw in data.get("claims") or ():
        if not isinstance(raw, Mapping) or not str(raw.get("text") or "").strip():
            continue
        out.append(ReleasedClaim(**{k: str(raw.get(k) or "") for k in (
            "text", "citation", "claim_kind", "subject", "population", "outcome",
            "direction", "certainty")}))
    return out


def _source_for(task: ComparisonTask, citation: str) -> Source | None:
    from psh.evidence.record import canonical_identifier
    wanted = canonical_identifier(citation)
    for source in task.sources:
        if canonical_identifier(source.identifier) == wanted:
            return source
    return None


def _gate(claim: ReleasedClaim, task: ComparisonTask, ctx: Any, n: int) -> ReleasedClaim:
    """The runtime's gates on one structured claim, as the ablation's full stack runs them.

    What the gates read must be stated, not guessed: a claim whose citation names none of
    the task's sources, or whose kind or certainty is not one of the declared values, is
    withheld with that reason before any gate runs. Filling in "mechanism hypothesis" or
    "moderate" would check a claim the model did not make. An unstated direction is the
    contract's own unknown ("unclear").
    """
    from .ablation import _evaluate
    source = _source_for(task, claim.citation) if claim.citation else None
    missing = []
    if source is None:
        missing.append("the claim cites no source" if not claim.citation else
                       f"the citation {claim.citation!r} names none of the sources given")
    if claim.claim_kind not in _KINDS:
        missing.append(f"claim_kind {claim.claim_kind!r} is not one of {', '.join(_KINDS)}")
    if claim.certainty not in _CERTAINTIES:
        missing.append(f"certainty {claim.certainty!r} is not one of "
                       f"{', '.join(_CERTAINTIES)}")
    if missing:
        return replace(claim, released=False, reasons=tuple(missing))
    statement = claim.text if claim.citation in claim.text \
        else f"{claim.text.rstrip('。.')} ({claim.citation})"
    case = Case(
        id=f"{task.id}/{n}", language="zh" if re.search("[一-鿿]", claim.text)
        else "en", claim_kind=claim.claim_kind, statement=statement,
        citation=claim.citation, subject=claim.subject, population=claim.population,
        outcome=claim.outcome, certainty=claim.certainty,
        direction=claim.direction if claim.direction in ("increase", "decrease") else "",
        source=source)
    results = _evaluate(case, ctx)
    seen = [results["provenance"], results["output|ingested"],
            *(results[g] for g in ("licensing", "claim_contract", "release_path"))]
    reasons = tuple(f"{r.gate}: {r.detail[:200]}" for r in seen if r.refused)
    return replace(claim, released=not reasons, reasons=reasons)


def run_arm(task: ComparisonTask, arm: Arm, client: Any, budget: Budget = Budget(), *,
            repeat: int = 0, workdir: str | Path | None = None) -> Transcript:
    """One arm on one task: the model's answer, the gates where the arm has them, the rounds."""
    if hasattr(client, "bind"):
        client.bind(task.id, arm.name)
    transcript = Transcript(task=task.id, arm=arm.name, client=getattr(client, "label", ""),
                            repeat=repeat)
    system = transcript.system = _system_prompt(task, arm)
    messages: list[dict[str, str]] = [{"role": "user", "content": task.question}]

    def ask() -> str:
        reply = client.complete(system, messages, max_tokens=budget.max_tokens)
        transcript.calls += 1
        transcript.input_tokens += reply.input_tokens
        transcript.output_tokens += reply.output_tokens
        transcript.stop_reasons.append(reply.stop_reason)
        messages.append({"role": "assistant", "content": reply.text})
        return reply.text

    def read(text: str) -> list[ReleasedClaim]:
        if not arm.structured:
            claims, transcript.next_steps = _prose_answer(text)
            return claims
        try:
            data = _json_object(text)
        except ValueError as exc:
            transcript.parse_errors.append(str(exc))
            return []
        transcript.rivals = [str(r) for r in data.get("rivals") or ()]
        transcript.contradicting = [str(r) for r in data.get("contradicting") or ()]
        transcript.next_steps = [str(s) for s in data.get("next_steps") or ()]
        return _structured_claims(data)

    with tempfile.TemporaryDirectory(prefix="comparison-") as tmp:
        ctx = None
        if arm.gated:
            from .ablation import _Context
            ctx = _Context(Path(workdir) if workdir else Path(tmp))

        def settle(claims: list[ReleasedClaim]) -> list[ReleasedClaim]:
            return [_gate(c, task, ctx, n) for n, c in enumerate(claims)] if arm.gated \
                else claims

        claims = settle(read(ask()))
        for _ in range(arm.self_review_rounds):
            messages.append({"role": "user", "content": (
                "Review your answer for errors of scope, wording, citation and study design, "
                "and give the corrected answer in the same form.")})
            claims = settle(read(ask()))
        for _ in range(arm.revision_rounds):
            withheld = [c for c in claims if not c.released]
            if not withheld:
                break
            reasons = "\n".join(f"- {c.text}\n  withheld: {'; '.join(c.reasons)}"
                                for c in withheld)
            messages.append({"role": "user", "content": (
                "These claims were withheld, with the reasons:\n" + reasons + "\nRevise or "
                "drop them, keep the claims that were released, and answer in the same form. "
                "Use only the sources given.")})
            claims = settle(read(ask()))
    transcript.turns = messages
    transcript.claims = claims
    return transcript


def run_comparison(tasks: Sequence[ComparisonTask], arms: Iterable[str], client: Any,
                   budget: Budget = Budget(), *, repeats: int = 1) -> list[Transcript]:
    """Every arm on every task, ``repeats`` times; the same client and budget throughout."""
    chosen = [ARMS[a] for a in arms]
    return [run_arm(task, arm, client, budget, repeat=r)
            for r in range(repeats) for task in tasks for arm in chosen]


# ------------------------------------------------------------------------- score
def _bare(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "")).lower()


#: What a reviewer may say of a claim gold does not decide.
VERDICTS = ("correct", "error", "not_a_claim")


def _judge(claim: ReleasedClaim, task: ComparisonTask,
           reviews: Mapping[tuple[str, str], str]) -> tuple[str, str]:
    """(verdict, gold id or "review") for one claim: "error", "correct", "not_a_claim" or
    "unadjudicated". Gold decides first, and an error phrase outranks a licensed one."""
    errors = [g for g in task.gold if not g.licensed and g.matches(claim.text)]
    if errors:
        return "error", errors[0].id
    licensed = [g for g in task.gold if g.licensed and g.matches(claim.text)]
    if licensed:
        return "correct", licensed[0].id
    verdict = reviews.get((task.id, _bare(claim.text)))
    if verdict in VERDICTS:
        return verdict, "review"
    return "unadjudicated", ""


@dataclass
class ComparisonReport:
    rows: dict[str, dict[str, float]]
    per_task: dict[str, dict[str, dict[str, float]]]
    differences: list[dict[str, Any]]
    synthetic: bool
    clients: tuple[str, ...]
    #: Per arm, how alike its repeats are (None with one repeat): see ``score``.
    stability: dict[str, float | None] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rows": self.rows, "per_task": self.per_task, "differences": self.differences,
                "synthetic": self.synthetic, "clients": list(self.clients),
                "stability": self.stability}


#: Per task: correctness (correct, errors), utility (coverage of the expected findings),
#: what the constraints cost (withheld, of them licensed; calls; output tokens), evidence
#: quality (uncited, citing no source given), and how many replies could not be read.
_METRICS = ("released", "correct", "errors", "unadjudicated", "coverage", "withheld",
            "withheld_licensed", "uncited", "unresolved", "calls", "output_tokens",
            "parse_failures")

#: The comparisons the protocol names, each one layer apart and matched in calls.
_PAIRS = (("B", "A"), ("C", "B"), ("D", "C"), ("D", "A+"), ("D", "A"))


def score(transcripts: Sequence[Transcript], tasks: Sequence[ComparisonTask], *,
          reviews: Mapping[tuple[str, str], str] | None = None, resamples: int = 2000,
          seed: int = 0) -> ComparisonReport:
    """Per arm and task: claims released, correct, errors, coverage, withheld, cost.

    Repeats are averaged within a task before tasks are compared, so a task counts once.
    Stability is the mean, over tasks, of the pairwise Jaccard similarity of what each
    repeat released, read through gold and reviews (which gold claims, which reviewed
    claims); it needs two repeats or more.
    """
    reviews = dict(reviews or {})
    by_id = {t.id: t for t in tasks}
    cells: dict[tuple[str, str], list[dict[str, float]]] = {}
    outputs: dict[tuple[str, str], list[frozenset[str]]] = {}
    for tr in transcripts:
        task = by_id[tr.task]
        found: dict[str, float] = {m: 0.0 for m in _METRICS}
        covered: set[str] = set()
        said: set[str] = set()
        for claim in tr.claims:
            verdict, gold = _judge(claim, task, reviews)
            if verdict == "not_a_claim":
                continue
            if not claim.released:
                found["withheld"] += 1
                if verdict == "correct":
                    found["withheld_licensed"] += 1
                continue
            found["released"] += 1
            found["uncited"] += 0 if claim.citation else 1
            found["unresolved"] += (1 if claim.citation
                                    and _source_for(task, claim.citation) is None else 0)
            said.add(gold if gold not in ("", "review") else f"text:{_bare(claim.text)}")
            if verdict == "correct":
                found["correct"] += 1
                covered.add(gold)
            elif verdict == "error":
                found["errors"] += 1
            else:
                found["unadjudicated"] += 1
        found["coverage"] = (len(covered & set(task.expected)) / len(task.expected)
                             if task.expected else 0.0)
        found["calls"] = tr.calls
        found["output_tokens"] = tr.output_tokens
        found["parse_failures"] = len(tr.parse_errors)
        cells.setdefault((tr.task, tr.arm), []).append(found)
        outputs.setdefault((tr.task, tr.arm), []).append(frozenset(said))

    per_task: dict[str, dict[str, dict[str, float]]] = {}
    for (task, arm), runs in cells.items():
        per_task.setdefault(arm, {})[task] = {
            m: sum(r[m] for r in runs) / len(runs) for m in _METRICS}
    rows = {arm: {m: sum(v[m] for v in tasks_.values()) / len(tasks_) for m in _METRICS}
            for arm, tasks_ in per_task.items()}

    differences = []
    rng = random.Random(seed)
    for left, right in _PAIRS:
        if left not in per_task or right not in per_task:
            continue
        shared = sorted(set(per_task[left]) & set(per_task[right]))
        units: dict[str, list[str]] = {}
        for task in shared:
            units.setdefault(by_id[task].unit, []).append(task)
        for metric in ("errors", "correct", "coverage"):
            diffs = {t: per_task[left][t][metric] - per_task[right][t][metric] for t in shared}
            estimate = sum(diffs.values()) / len(diffs) if diffs else 0.0
            draws = []
            names = sorted(units)
            for _ in range(resamples if len(names) > 1 else 0):
                picked = [t for u in (rng.choice(names) for _ in names) for t in units[u]]
                draws.append(sum(diffs[t] for t in picked) / len(picked))
            draws.sort()
            ci = ((draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1])
                  if draws else (float("nan"), float("nan")))
            differences.append({"comparison": f"{left} - {right}", "metric": metric,
                                "estimate": estimate, "ci95": list(ci),
                                "tasks": len(shared), "units": len(units)})
    stability: dict[str, float | None] = {}
    for arm in per_task:
        per = [_mean_jaccard(outputs[(task, arm)]) for task in per_task[arm]
               if len(outputs[(task, arm)]) > 1]
        stability[arm] = sum(per) / len(per) if per else None
    return ComparisonReport(rows=rows, per_task=per_task, differences=differences,
                            synthetic=any(by_id[t].synthetic for t in {tr.task for tr in
                                                                       transcripts}),
                            clients=tuple(sorted({tr.client for tr in transcripts})),
                            stability=stability)


def _mean_jaccard(runs: Sequence[frozenset[str]]) -> float:
    pairs = [(a, b) for i, a in enumerate(runs) for b in runs[i + 1:]]
    return sum((len(a & b) / len(a | b)) if a | b else 1.0 for a, b in pairs) / len(pairs)


def render_markdown(report: ComparisonReport) -> str:
    banner = []
    if report.synthetic or any("scripted" in c for c in report.clients):
        banner = ["> **Not a result.** These numbers come from fixture tasks or a scripted "
                  "client, which exist to test the harness. They say nothing about any "
                  "model or about the governance.", ""]
    out = ["# Four-arm comparison", "", *banner,
           f"Clients: {', '.join(report.clients)}", "",
           "Per task, averaged over tasks. Withheld (licensed): claims the gates withheld, "
           "and of them those gold licenses, which is what the constraints cost.", "",
           "| Arm | Released | Correct | Errors | Unadjudicated | Coverage | Withheld "
           "(licensed) | Uncited | Unresolved citation | Calls | Output tokens | Stability |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in sorted(report.rows, key=list(ARMS).index):
        r = report.rows[arm]
        stable = report.stability.get(arm)
        out.append(f"| {arm} | {r['released']:.2f} | {r['correct']:.2f} | {r['errors']:.2f} | "
                   f"{r['unadjudicated']:.2f} | {r['coverage']:.0%} | {r['withheld']:.2f} "
                   f"({r['withheld_licensed']:.2f}) | {r['uncited']:.2f} | "
                   f"{r['unresolved']:.2f} | {r['calls']:.1f} | {r['output_tokens']:.0f} | "
                   f"{'—' if stable is None else f'{stable:.2f}'} |")
    out += ["", "Paired differences per task (95% interval resampling independent units):", "",
            "| Comparison | Metric | Estimate | 95% interval | Tasks | Units |",
            "| --- | --- | ---: | --- | ---: | ---: |"]
    for d in report.differences:
        lo, hi = d["ci95"]
        interval = "—" if lo != lo else f"{lo:+.2f} to {hi:+.2f}"
        out.append(f"| {d['comparison']} | {d['metric']} | {d['estimate']:+.2f} | {interval} "
                   f"| {d['tasks']} | {d['units']} |")
    return "\n".join(out) + "\n"


# -------------------------------------------------------------------- blinding
def export_for_review(transcripts: Sequence[Transcript], tasks: Sequence[ComparisonTask],
                      path: str | Path, key_path: str | Path, *, seed: int = 0) -> int:
    """Write the claims gold does not decide, arm hidden and order shuffled, for review.

    Each item carries the task's question, the claim, its citation and the cited source's
    text; the reviewer fills ``verdict`` with one of ``VERDICTS``.

    ``path`` goes to reviewers; ``key_path`` (which arm wrote which item) stays with the
    person running the experiment until the reviews are in. Returns the number of items.
    """
    by_id = {t.id: t for t in tasks}
    items, seen = [], set()
    for tr in transcripts:
        task = by_id[tr.task]
        for claim in tr.released:
            if _judge(claim, task, {})[0] != "unadjudicated":
                continue
            key = (task.id, _bare(claim.text))
            if key in seen:
                continue
            seen.add(key)
            source = _source_for(task, claim.citation)
            items.append({"task": task.id, "question": task.question, "claim": claim.text,
                          "citation": claim.citation,
                          "source": (source.text[:1500] if source else "(no such source)"),
                          "arm": tr.arm})
    random.Random(seed).shuffle(items)
    with Path(path).open("w", encoding="utf-8") as out, \
            Path(key_path).open("w", encoding="utf-8") as key:
        for n, item in enumerate(items, 1):
            review_id = f"R{n:04d}"
            arm = item.pop("arm")
            out.write(json.dumps({"review_id": review_id, **item,
                                  "verdict": "", "note": ""}, ensure_ascii=False) + "\n")
            key.write(json.dumps({"review_id": review_id, "arm": arm, "task": item["task"],
                                  "claim": item["claim"]}, ensure_ascii=False) + "\n")
    return len(items)


def load_reviews(path: str | Path) -> dict[tuple[str, str], str]:
    """Reviewers' verdicts (``VERDICTS``), keyed for ``score``. Blank ones are skipped; any
    other word is an error in the review file, not a verdict to guess at."""
    out: dict[tuple[str, str], str] = {}
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        verdict = str(row.get("verdict") or "").strip().lower().replace(" ", "_")
        if not verdict:
            continue
        if verdict not in VERDICTS:
            raise ValueError(f"{path}:{n}: verdict {verdict!r} is not one of {VERDICTS}")
        out[(str(row["task"]), _bare(row["claim"]))] = verdict
    return out


# ------------------------------------------------------------------ fixture tasks
def fixture_tasks() -> list[ComparisonTask]:
    """Two synthetic tasks for testing the harness, built on the ablation's fixture sources.

    Marked ``synthetic``: every report that includes them says it is not a result.
    """
    from .ablation_corpus import BASE_CASES
    by_id = {c["id"]: c for c in BASE_CASES}

    def source(case_id: str, **override: Any) -> Source:
        return Source.from_dict({**by_id[case_id]["source"], **override})

    mechanism = ComparisonTask(
        id="fixture-formula-mechanism", scenario="formula_mechanism", synthetic=True,
        unit="fixture-unit-1",
        question="What do these sources establish about 葛根芩连汤 in type 2 diabetes, and "
                 "what should be tested next?",
        sources=(source("E1"), source("H1"), source("M1")),
        gold=(
            GoldClaim("trial", ("葛根芩连汤", "糖化血红蛋白|HbA1c", "成人|adults")),
            GoldClaim("docking", ("baicalin|黄芩苷", "PTP1B", "may|可能|hypothes|假说")),
            GoldClaim("children", ("葛根芩连汤", "儿童|children"), licensed=False,
                      note="population the trial did not enrol"),
            GoldClaim("mechanism-as-fact", ("PTP1B", "葛根芩连汤|formula"), licensed=False,
                      none_of=("may", "可能", "hypothes", "假说"),
                      note="a docking result stated as the formula's mechanism"),
        ),
        expected=("trial", "docking"))
    safety = ComparisonTask(
        id="fixture-heshouwu-safety", scenario="safety_signal", synthetic=True,
        unit="fixture-unit-2",
        question="What do these sources say about the safety of preparations containing 何首乌?",
        sources=(source("S1"),),
        gold=(
            GoldClaim("signal", ("何首乌", "肝损伤|liver injury", "可能|may|associated|有关")),
            GoldClaim("causal", ("何首乌", "导致|causes"), licensed=False,
                      note="a case report stated as causation"),
            GoldClaim("children", ("何首乌", "儿童|children"), licensed=False),
        ),
        expected=("signal",))
    return [mechanism, safety]


def fixture_script() -> dict[tuple[str, str, int], str]:
    """Scripted replies for the fixture tasks, for ``ScriptedClient``: what the self-test and
    the tests run. In the formula task every first answer makes the same three claims, one
    of them an extrapolation to children; A+ fixes it in review, D in revision. Written to
    exercise each path of the harness, not to resemble any model."""
    mech, safety = "fixture-formula-mechanism", "fixture-heshouwu-safety"
    trial, docking, report = ("doi:10.5555/tcm-ablation.01", "doi:10.5555/tcm-ablation.09",
                              "doi:10.5555/tcm-ablation.07")

    def claim(text: str, citation: str, kind: str, subject: str, population: str = "",
              outcome: str = "", direction: str = "", certainty: str = "moderate") -> dict:
        return {"text": text, "citation": citation, "claim_kind": kind, "subject": subject,
                "population": population, "outcome": outcome, "direction": direction,
                "certainty": certainty}

    def structured(*claims: dict, **extra: Any) -> str:
        return "Here is the answer:\n" + json.dumps(
            {"claims": list(claims),
             "next_steps": ["test baicalin against PTP1B in an enzyme assay"], **extra},
            ensure_ascii=False)

    adults = claim("葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。", trial, "efficacy", "葛根芩连汤",
                   "成人2型糖尿病患者", "糖化血红蛋白", "decrease")
    children = claim("葛根芩连汤可降低儿童2型糖尿病患者的糖化血红蛋白。", trial, "efficacy",
                     "葛根芩连汤", "儿童2型糖尿病患者", "糖化血红蛋白", "decrease")
    dock = claim("Docking suggests that baicalin may bind PTP1B, a hypothesis for "
                 "experimental testing.", docking, "mechanism_hypothesis", "baicalin",
                 outcome="PTP1B", certainty="tentative")
    signal = claim("服用含何首乌的制剂可能与成人药物性肝损伤有关。", report, "safety_signal",
                   "含何首乌的制剂", "成人", "药物性肝损伤", certainty="tentative")
    first_prose = ("Findings:\n葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白（doi:10.5555/tcm-ablation.01）。"
                   "葛根芩连汤可降低儿童2型糖尿病患者的糖化血红蛋白（doi:10.5555/tcm-ablation.01）。\n"
                   "Next steps:\n- test baicalin against PTP1B")
    reviewed_prose = ("Findings:\n葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白"
                      "（doi:10.5555/tcm-ablation.01）。Docking suggests baicalin may bind PTP1B "
                      "(doi:10.5555/tcm-ablation.09).\nNext steps:\n- an assay")
    script = {
        (mech, "A", 0): first_prose, (mech, "A+", 0): first_prose,
        (mech, "A+", 1): reviewed_prose,
        (mech, "B", 0): structured(adults, children, dock),
        (mech, "C", 0): structured(adults, children, dock),
        (mech, "D", 0): structured(adults, children, dock,
                                   rivals=["regression to the mean"], contradicting=[]),
        (mech, "D", 1): structured(adults, dock, rivals=["regression to the mean"]),
    }
    for arm in ARMS:
        script[(safety, arm, 0)] = (structured(signal) if ARMS[arm].structured else
                                    "Findings:\n服用含何首乌的制剂可能与成人药物性肝损伤有关"
                                    "（doi:10.5555/tcm-ablation.07）。")
    script[(safety, "A+", 1)] = script[(safety, "A+", 0)]
    return script

