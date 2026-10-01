"""Could a third party get this result again?

The question a methods section answers and an agent's transcript does not. Each rule names
one thing that has to be written down *before* the run, because none of them can be
recovered afterwards: which seed, which version of the data, which version of the tool,
which environment — and, for a preregistered analysis, that the protocol was frozen before
the numbers were seen.

``REP506`` is the load-bearing one and the reason preregistration is a graph property
rather than a boolean. An analysis that claims to follow a protocol but does not *depend*
on the protocol node can run before the protocol is frozen, and a protocol frozen after the
analysis is a description of what was done. Making it a dependency makes the ordering a
fact about the program rather than a promise about the author.
"""

from __future__ import annotations

from typing import Any

from ..sir import ExecKind, Role, SIRNode, SIRProgram
from .diagnostics import Diagnostics

__all__ = ["check_reproducibility"]


def check_reproducibility(program: SIRProgram, *, registry: Any = None,
                          require_preregistration: bool = False,
                          diagnostics: Diagnostics | None = None) -> Diagnostics:
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="reproducibility")
    protocols = program.by_role(Role.PROTOCOL)
    required = bool(require_preregistration
                    or program.metadata.get("preregistration_required"))

    if required and not protocols:
        out.emit("REP505",
                 "this run requires preregistration and the program declares no protocol "
                 "node, so there is nothing for the analysis to be checked against")

    for node in program.nodes:
        if not node.executes:
            continue
        if not node.deterministic and node.repro.seed is None:
            out.emit("REP501",
                     f"node {node.node_id!r} is declared non-deterministic and carries no "
                     "seed; its result cannot be reproduced, including by this run's own "
                     "retry", node_id=node.node_id)
        if node.role is Role.RETRIEVAL and not node.repro.dataset_version:
            out.emit("REP502",
                     f"node {node.node_id!r} reads a source and pins no version; the same "
                     "query against a live database returns different rows next month",
                     node_id=node.node_id)
        if node.kind is ExecKind.TOOL and not node.repro.tool_version:
            out.emit("REP503",
                     f"node {node.node_id!r} calls {node.component_id or 'a component'} "
                     "and pins no version", node_id=node.node_id,
                     component=node.component_id)
        if node.kind is ExecKind.TOOL and _runs_isolated(registry, node.component_id) \
                and not (node.repro.container_digest or node.repro.environment):
            out.emit("REP504",
                     f"node {node.node_id!r} runs {node.component_id!r} in its own "
                     "process and records no environment", node_id=node.node_id)

        if node.analysis is not None and node.analysis.preregistered:
            if not protocols:
                out.emit("REP505",
                         f"analysis {node.node_id!r} declares itself preregistered and the "
                         "program holds no protocol node", node_id=node.node_id)
                continue
            upstream = program.upstream_of(node.node_id)
            if not any(p.node_id in upstream for p in protocols):
                out.emit("REP506",
                         f"analysis {node.node_id!r} declares itself preregistered and "
                         f"does not depend on "
                         f"{[p.node_id for p in protocols][:3]}; nothing orders the freeze "
                         "before the analysis, so the protocol could be written after the "
                         "result", node_id=node.node_id,
                         protocols=[p.node_id for p in protocols])
    return out


def _runs_isolated(registry: Any, component_id: str) -> bool:
    """Whether the registry says this component runs in its own process.

    Best effort and silent on failure: a missing registry means the check does not run,
    which is reported as nothing rather than as a pass — there is no diagnostic claiming
    the environment was fine.
    """
    if registry is None or not component_id:
        return False
    for getter in ("manifest_for", "manifest"):
        fn = getattr(registry, getter, None)
        if callable(fn):
            try:
                manifest = fn(component_id)
            except Exception:  # noqa: BLE001 - registries differ
                manifest = None
            if manifest is not None:
                return bool(getattr(manifest, "runs_isolated", False))
    return False
