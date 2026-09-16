"""The runtime: the single trusted execution path, and the loop that repeats it.

``Runner`` is one governed pass — classify, compile, execute, verify, release.
``AgentLoopController`` is the bounded plan/act/observe/evaluate loop over the same kernel.
The loop is additive: it holds no provider and no subprocess, and reaches the world only
through ``ExecutionBroker``, so adding iteration did not add an execution path.
"""

from .evaluator import ACCEPTANCE_CHECKS, CheckResult, Evaluator, Verdict
from .execgraph import ExecutionGraph, TaskNode, TaskState
from .loop import (
    AgentLoopController, LoopLimits, LoopResult, LoopState, Planner, StaticPlanner,
    Termination,
)
from .plan import (
    Criterion, Plan, PlanTask, RetryBudget, RetryPolicy, TaskKind, TestSpec,
)
from .planner import PLANNER_SYSTEM_PROMPT, ModelPlanner, PlanParseError, parse_plan
from .plan_validator import (
    PlanRejected, PlanValidator, PlanViolation, ValidatedPlan, task_envelope,
)
from .runner import DEFAULT_SYSTEM_PROMPT, RunResult, Runner, StageRecord, StuckLoop

__all__ = [
    "Runner", "RunResult", "StageRecord", "StuckLoop", "DEFAULT_SYSTEM_PROMPT",
    "AgentLoopController", "LoopState", "LoopResult", "LoopLimits", "Termination",
    "Planner", "StaticPlanner", "ModelPlanner", "parse_plan", "PlanParseError",
    "PLANNER_SYSTEM_PROMPT",
    "Plan", "PlanTask", "TaskKind", "TestSpec", "Criterion", "RetryPolicy", "RetryBudget",
    "PlanValidator", "PlanViolation", "ValidatedPlan", "PlanRejected", "task_envelope",
    "ExecutionGraph", "TaskState", "TaskNode",
    "Evaluator", "Verdict", "CheckResult", "ACCEPTANCE_CHECKS",
]
