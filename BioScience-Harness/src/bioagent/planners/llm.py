"""LLM-backed planner.

The v1 report noted the planner was retrieval + heuristics with no live model.
This plugin calls a model to order and justify the retrieved component set. The
model client is injected, so the runtime has no vendor dependency and the planner
degrades to heuristic ordering when no client is supplied.

The model also chooses each step's arguments. The planner used to keep only the
component id, so a model that answered ``{"sequence": "ATGC"}`` produced a step
with no arguments and the tool failed for want of ``sequence`` (audit AUD-17).
Arguments are now kept, and checked against the parameters the component
declares: an unknown name, a missing required one or a value of the wrong type
refuses that step rather than reaching the tool.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from .base import Plan, PlannerPlugin, PlanStep, register_planner
from .heuristic import HeuristicPlanner


@register_planner
class LLMPlanner(PlannerPlugin):
    """Asks a model which retrieved components to use, and in what order."""

    name = "llm"

    def __init__(self, client: Callable[[str], str] | None = None, model: str = "",
                 fallback: PlannerPlugin | None = None) -> None:
        self._client = client
        self.model = model
        self.fallback = fallback or HeuristicPlanner()

    def plan(self, task: str, registry: Any, *, max_steps: int = 5) -> Plan:
        candidates = registry.search(task, limit=max_steps * 6)
        if not candidates:
            return Plan(task=task, steps=[], planner=self.name, notes="no candidates retrieved")
        if self._client is None:
            p = self.fallback.plan(task, registry, max_steps=max_steps)
            p.planner = f"{self.name}->{self.fallback.name}"
            p.notes += " (no model client bound; fell back to heuristic ordering)"
            return p

        menu = [{"id": m.id, "kind": m.kind, "backend": m.runtime.backend,
                 "state": m.state.value, "description": m.description[:160],
                 "parameters": _parameters(m)}
                for m in candidates]
        prompt = (
            "You are planning a biomedical analysis. Choose the components to call, in order.\n"
            f"TASK: {task}\n\n"
            f"AVAILABLE COMPONENTS (JSON):\n{json.dumps(menu, indent=1)[:6000]}\n\n"
            "Rules: prefer components whose state is READY or RESOLVED; a backend of 'none' "
            "cannot execute. Give each step the arguments it needs, using only the listed "
            "parameter names and every required one. Reply with ONLY a JSON array of objects "
            '[{"id": "...", "arguments": {...}, "rationale": "..."}] '
            f"with at most {max_steps} entries."
        )
        try:
            raw = self._client(prompt)
            payload = self._extract_json(raw)
            valid = {m.id: m for m in candidates}
            steps: list[PlanStep] = []
            refused: list[str] = []
            for item in payload:
                if not (isinstance(item, dict) and item.get("id") in valid):
                    continue
                arguments = item.get("arguments", {})
                problems = check_arguments(valid[item["id"]], arguments)
                if problems:
                    refused.append(f"{item['id']}: {'; '.join(problems)}")
                    continue
                steps.append(PlanStep(component_id=item["id"], kind=valid[item["id"]].kind,
                                      arguments=dict(arguments),
                                      rationale=str(item.get("rationale", ""))[:200]))
            if not steps:
                raise ValueError("model returned no valid step" + (
                    f" ({'; '.join(refused[:3])})" if refused else ""))
            notes = (f"model={self.model or 'unspecified'}; "
                     f"chose {len(steps)} of {len(candidates)} candidates")
            if refused:
                notes += f"; refused {len(refused)} step(s): {'; '.join(refused[:3])}"
            return Plan(task=task, steps=steps[:max_steps], planner=self.name, notes=notes)
        except Exception as exc:  # noqa: BLE001 - planning must not crash the run
            p = self.fallback.plan(task, registry, max_steps=max_steps)
            p.planner = f"{self.name}->{self.fallback.name}"
            p.notes += f" (model planning failed: {type(exc).__name__}: {exc}; used heuristic)"
            return p

    @staticmethod
    def _extract_json(text: str) -> list:
        t = text.strip()
        start, end = t.find("["), t.rfind("]")
        if start >= 0 and end > start:
            return json.loads(t[start:end + 1])
        raise ValueError("no JSON array found in model response")


#: JSON Schema types, as a declared parameter may name them.
_JSON_TYPES: Mapping[str, Any] = {"string": str, "integer": int, "number": (int, float),
                                  "boolean": bool, "array": (list, tuple), "object": Mapping}


def _parameters(manifest: Any) -> list[dict[str, Any]] | None:
    """The parameters a component declares, or None when it declares none."""
    inputs = getattr(manifest, "inputs", None) or {}
    declared = inputs.get("parameters")
    if isinstance(declared, (list, tuple)):
        return [dict(p) for p in declared if isinstance(p, Mapping) and p.get("name")]
    properties = inputs.get("properties")
    if isinstance(properties, Mapping):
        required = set(inputs.get("required") or ())
        return [{"name": name, "required": name in required,
                 **({"type": spec["type"]} if isinstance(spec, Mapping) and spec.get("type")
                    else {})}
                for name, spec in properties.items()]
    return None


def _type_ok(parameter: Mapping[str, Any], value: Any) -> bool:
    expected = _JSON_TYPES.get(parameter.get("type", ""))
    if expected is not None:
        if expected in (int, (int, float)) and isinstance(value, bool):
            return False
        return isinstance(value, expected)
    default = parameter.get("default")
    if default is None:
        return True                       # nothing declared to check the value against
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    for kind in (str, (list, tuple), Mapping):
        if isinstance(default, kind):
            return isinstance(value, kind)
    return True


def check_arguments(manifest: Any, arguments: Any) -> list[str]:
    """Why ``arguments`` cannot be passed to ``manifest``'s component; [] when they can.

    Checked against the parameters the component declares: every name must be one of
    them, every required one must be given, and a value must have the declared type (or
    the type of the declared default). A component that declares no parameters accepts
    an object of arguments as given.
    """
    if not isinstance(arguments, Mapping):
        return [f"arguments must be an object, not {type(arguments).__name__}"]
    parameters = _parameters(manifest)
    if parameters is None:
        return []
    by_name = {p["name"]: p for p in parameters}
    problems = [f"unknown argument {name!r}" for name in arguments if name not in by_name]
    problems += [f"missing required argument {p['name']!r}" for p in parameters
                 if p.get("required") and p["name"] not in arguments]
    problems += [f"argument {name!r} has the wrong type ({type(value).__name__})"
                 for name, value in arguments.items()
                 if name in by_name and not _type_ok(by_name[name], value)]
    return problems
