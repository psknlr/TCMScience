"""Retry backoff through the doors an application uses, not only through the loop.

``ResearchRunService`` and ``ScientificRunService`` built their loop with
``sleep=lambda s: None``. A task's ``RetryPolicy(initial_delay_s=0.5)`` was honoured by a
direct ``AgentLoopController`` (gaps of 0.503 s and 1.003 s, measured) and ignored by both
services (0.002 s and 0.003 s): a failing service was hit again at once, through the very
entrances the roadmap calls the supported ones.

Each service now takes a ``sleep`` and defaults to ``jittered_sleep``: real time, less up
to a tenth, so the delay is never longer than the policy's. A test that wants instant
retries injects a no-op, as these do wherever they do not measure time.
"""

from __future__ import annotations

import time

import pytest

from psh.capabilities import CapabilityRegistry
from psh.config import PSHConfig
from psh.contracts import Autonomy, ComponentKind, ComponentManifest, RiskTier
from psh.kernel import TrustedKernel
from psh.labels import Destination, Sensitivity
from psh.policy import PolicySnapshot
from psh.runtime import (
    RETRY_JITTER, AgentLoopController, Criterion, Plan, PlanTask, ResearchRunService,
    RetryPolicy, ScientificRunService, StaticPlanner, TaskKind, Termination, jittered_sleep,
)
from psh.runtime import plan as plan_module
from psh.runtime import scientific as scientific_module
from psh.sir import Acceptance, Effect, ExecKind, Role, SIRNode, SIRProgram

LOCAL = (Destination.LOCAL_COMPUTE, Destination.LOCAL_MODEL, Destination.USER_OUTPUT,
         Destination.PERSISTENT)


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(
        PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
        policy=PolicySnapshot(profile_id="backoff_bench", allowed_destinations=LOCAL,
                              max_data_label=Sensitivity.PHI, autonomy=Autonomy.ACT,
                              risk_ceiling=RiskTier.R2_CONSEQUENTIAL,
                              require_claim_support=False))
    yield k
    k.close()


class Flaky:
    """Fails with a retryable ``OSError`` until its third call, and times every call."""

    def __init__(self, fail_times=2):
        self.manifest = ComponentManifest(id="flaky", name="flaky", kind=ComponentKind.TOOL,
                                          max_label=Sensitivity.PHI, idempotent=True)
        self.fail_times = fail_times
        self.times: list[float] = []

    def invoke(self, payload, envelope):
        self.times.append(time.monotonic())
        if len(self.times) <= self.fail_times:
            raise OSError("transient")
        return {"ok": True}

    def gaps(self):
        return [b - a for a, b in zip(self.times, self.times[1:])]


def research(kernel, tool, **kw):
    registry = CapabilityRegistry()
    registry.register(tool)
    plan = Plan(objective="call", produced_by="test", tasks=(PlanTask(
        task_id="t", objective="call", kind=TaskKind.TOOL, component_id="flaky",
        retry=RetryPolicy(max_attempts=3, initial_delay_s=0.5)),),
        completion_criteria=(Criterion(description="ran", kind="task"),))
    return ResearchRunService(kernel, planner=StaticPlanner(plan), registry=registry, **kw)


# ============================================================ the services wait

def test_research_service_waits_out_the_backoff_by_default(kernel):
    """The acceptance measurement: 0.5 s then 1.0 s, less at most a tenth."""
    tool = Flaky()
    service = research(kernel, tool)
    service.run("call")

    assert service.last_loop_result.termination is Termination.GOAL_SATISFIED
    assert len(tool.times) == 3
    first, second = tool.gaps()
    assert first >= 0.45, f"the first retry came {first:.3f}s after the failure"
    assert second >= 0.9, f"the second retry came {second:.3f}s after the failure"
    # Jitter only ever shortens; what remains is scheduling, not a doubled delay.
    assert first < 0.5 + 0.4 and second < 1.0 + 0.4, tool.gaps()


def test_research_service_hands_the_policy_delay_to_its_sleep(kernel):
    slept: list[float] = []
    tool = Flaky()
    research(kernel, tool, sleep=slept.append).run("call")
    assert slept == [0.5, 1.0], "the service passes the policy's delay through unchanged"


def test_a_no_op_sleep_still_gives_instant_retries(kernel):
    """What tests that do not measure time inject, and what the services used to hard-code."""
    tool = Flaky()
    research(kernel, tool, sleep=lambda s: None).run("call")
    assert len(tool.times) == 3
    assert max(tool.gaps()) < 0.2, tool.gaps()


def test_both_services_default_to_the_jittered_sleep(kernel):
    assert research(kernel, Flaky()).sleep is jittered_sleep
    assert ScientificRunService(kernel).sleep is jittered_sleep


def test_the_scientific_service_gives_its_loop_its_sleep(kernel, monkeypatch):
    """The program door builds its own controller; the sleep must reach that one too.

    A compiled program's plan carries no retry delay today (lowering sets only
    ``max_attempts``), so this checks the hand-over itself rather than a measured gap.
    """
    handed: list = []

    class Recording(AgentLoopController):
        def __init__(self, *args, **kw):
            handed.append(kw.get("sleep"))
            super().__init__(*args, **kw)

    monkeypatch.setattr(scientific_module, "AgentLoopController", Recording)
    registry = CapabilityRegistry()
    registry.register(Flaky(fail_times=0))
    program = SIRProgram(
        question="does it run?", program_id="prog_backoff",
        acceptance=(Acceptance(description="ran", kind="task"),),
        nodes=(SIRNode(node_id="call", role=Role.TRANSFORM, kind=ExecKind.TOOL,
                       objective="call", component_id="flaky",
                       effects={Effect.READ_LOCAL}, max_attempts=3),))

    def mine(seconds):
        return None

    ScientificRunService(kernel, registry=registry, sleep=mine).run(program)
    assert handed == [mine]
    handed.clear()
    ScientificRunService(kernel, registry=registry).run(program)
    assert handed == [jittered_sleep]


# =========================================================== the jittered sleep

@pytest.mark.parametrize("draw", [0.0, 0.5, 0.999999])
def test_jitter_only_shortens_and_by_at_most_a_tenth(monkeypatch, draw):
    slept: list[float] = []
    monkeypatch.setattr(plan_module.time, "sleep", slept.append)
    monkeypatch.setattr(plan_module.random, "random", lambda: draw)
    jittered_sleep(2.0)
    assert len(slept) == 1
    assert 2.0 * (1.0 - RETRY_JITTER) <= slept[0] <= 2.0
    assert slept[0] == pytest.approx(2.0 * (1.0 - RETRY_JITTER * draw))


def test_jitter_varies_between_calls(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(plan_module.time, "sleep", slept.append)
    for _ in range(50):
        jittered_sleep(1.0)
    assert len(set(slept)) > 1, "fifty identical delays decorrelate nothing"
    assert all(1.0 - RETRY_JITTER <= s <= 1.0 for s in slept)


@pytest.mark.parametrize("delay", [0.0, -1.0])
def test_no_delay_means_no_sleep(monkeypatch, delay):
    slept: list[float] = []
    monkeypatch.setattr(plan_module.time, "sleep", slept.append)
    jittered_sleep(delay)
    assert slept == []
