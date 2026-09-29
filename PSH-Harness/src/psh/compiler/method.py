"""Is the scientific method complete?

The family that has no analogue in a coding agent's compiler, and the one that decides
whether this is a research system or a task runner with citations. Each rule is a question
a supervisor asks before the work starts:

* What would make you abandon this hypothesis? (``SCI702``)
* What else could explain the observation? (``SCI703``)
* Which of these two experiments separates the hypotheses? (``SCI705``)
* What is this claim an answer to? (``SCI706``)

None of them can be answered by a program that represents only tasks, which is why the IR
carries hypotheses and predictions as nodes. The severities are mostly WARNING because
exploratory work legitimately starts without an alternative in mind — except ``SCI702``,
which is an ERROR: a prediction compatible with every outcome is not a prediction, and a
plan built on one spends a budget to learn nothing.
"""

from __future__ import annotations

from ..sir import ClaimKind, Role, SIRProgram
from .diagnostics import Diagnostics

__all__ = ["check_method"]

#: Claim kinds that rest on a causal story. Asserting one with no hypothesis in the
#: program is not forbidden — a meta-analysis may state efficacy without proposing a
#: mechanism — but it is worth saying, because the usual cause is that the hypothesis was
#: never written down rather than that there is none.
_NEEDS_HYPOTHESIS: frozenset[ClaimKind] = frozenset({ClaimKind.MECHANISM,
                                                     ClaimKind.EFFICACY})


def check_method(program: SIRProgram, *,
                 diagnostics: Diagnostics | None = None) -> Diagnostics:
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="method")
    ids = program.ids
    hypotheses = program.by_role(Role.HYPOTHESIS)
    predictions = program.by_role(Role.PREDICTION)
    questions = {n.node_id for n in program.by_role(Role.QUESTION)}

    predicted: set[str] = set()
    for node in predictions:
        spec = node.prediction
        if spec is None:
            continue                       # SIR013 already said so
        if spec.hypothesis:
            predicted.add(spec.hypothesis)
        if not spec.falsifier.strip():
            out.emit("SCI702",
                     f"prediction {node.node_id!r} states no falsifier: there is no "
                     "observation that would count against it, so testing it cannot "
                     "change anyone's mind", node_id=node.node_id)
        # A prediction nothing reads is a prediction nothing tests.
        tested_by = [n.node_id for n in program.nodes
                     if node.node_id in n.about or node.node_id in n.dependencies]
        if not any(n != node.node_id for n in tested_by):
            out.emit("SCI708",
                     f"prediction {node.node_id!r} is never tested: no analysis or "
                     "experiment names it", node_id=node.node_id)

    for node in hypotheses:
        spec = node.hypothesis
        if node.node_id not in predicted:
            out.emit("SCI701",
                     f"hypothesis {node.node_id!r} implies no prediction, so nothing in "
                     "this program bears on it", node_id=node.node_id)
        if spec is not None and not spec.alternative_to and len(hypotheses) < 2:
            out.emit("SCI703",
                     f"hypothesis {node.node_id!r} has no alternative: the program can "
                     "confirm it and cannot rule out the other explanation",
                     node_id=node.node_id)

    for node in program.by_role(Role.EXPERIMENT):
        spec = node.experiment
        if spec is None:
            continue
        for hypothesis_id in spec.tests:
            if hypothesis_id not in ids:
                out.emit("SCI704",
                         f"experiment {node.node_id!r} tests {hypothesis_id!r}, which is "
                         "not a node of this program", node_id=node.node_id,
                         missing=hypothesis_id)
        if len(spec.discriminates) < 2:
            out.emit("SCI705",
                     f"experiment {node.node_id!r} discriminates between "
                     f"{len(spec.discriminates)} hypothesis(es): its result is compatible "
                     "with the alternatives, so it cannot decide between them",
                     node_id=node.node_id)

    claim_kinds: set[ClaimKind] = set()
    for node in program.by_role(Role.CLAIM):
        claim_type = node.produces.claim
        if claim_type is not None:
            claim_kinds.add(claim_type.kind)
        spec = node.claim
        if spec is not None and spec.answers and spec.answers not in questions:
            out.emit("SCI706",
                     f"claim {node.node_id!r} answers {spec.answers!r}, which is not a "
                     "question in this program", node_id=node.node_id,
                     missing=spec.answers)

    if claim_kinds & _NEEDS_HYPOTHESIS and not hypotheses:
        kinds = sorted(k.value for k in claim_kinds & _NEEDS_HYPOTHESIS)
        out.emit("SCI707",
                 f"the program asserts {kinds} and states no hypothesis: there is nothing "
                 "for the evidence to be evidence *for*, and nothing to argue against",
                 kinds=kinds)
    return out
