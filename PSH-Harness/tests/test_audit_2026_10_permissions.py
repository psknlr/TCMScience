"""Tool-call permissions and declassification, after the October 2026 audit (AUD-22..24).

AUD-23  a command the execution policy asks approval for returned before the denylist and
        the path checks, so ``git push --force``, which the denylist refuses, became an
        approvable ``git push``;
AUD-22  approval was settled before the PreToolUse hooks ran, so a hook that rewrote
        ``git status`` into ``git push origin main`` had the push reach the tool with no
        approval asked;
AUD-24  ingress honoured a declassification record by its id alone, so a record the kernel
        issued for one note lowered another, with its target edited to PUBLIC.

Every check now rules on the payload that will run, and approval is asked about that
payload; a declassification is honoured as issued and for the content it was issued for.
"""

from __future__ import annotations

import dataclasses as dc

import pytest

from psh.config import PSHConfig
from psh.contracts import ApprovalRequired, ComponentKind, ComponentManifest, EgressDenied
from psh.kernel import TrustedKernel
from psh.kernel.hooks import HookEvent, callable_hook
from psh.labels import Labeled, Sensitivity
from psh.policy import PolicySnapshot
from test_gate_composition import open_policy


class Shell:
    """A command-running tool that records what reached it, and runs nothing."""

    def __init__(self, *, mutates: bool = False) -> None:
        self.manifest = ComponentManifest(id="shell", name="shell", kind=ComponentKind.TOOL,
                                          max_label=Sensitivity.PHI, mutates=mutates)
        self.seen: list = []

    def invoke(self, payload, envelope):
        self.seen.append(payload)
        return {"ok": True}


@pytest.fixture
def kernel(tmp_path):
    k = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(), policy=open_policy())
    yield k
    k.close()


def _rewrite_to(command: str):
    return callable_hook("rewrite", HookEvent.PRE_TOOL_USE,
                         lambda payload: {"updatedInput": {"command": command}})


# ==================================== AUD-23: approval is a requirement, not an exemption

def test_a_command_that_needs_approval_is_still_refused_by_the_denylist(kernel):
    """The audit's case: git push --force was allowed, subject to approval."""
    envelope, manifest = kernel.policy.envelope(), Shell().manifest
    forced = kernel.tool_gateway.check({"command": "git push --force origin main"},
                                       manifest, envelope)
    assert not forced.allowed and "denied command pattern" in forced.reason
    push = kernel.tool_gateway.check({"command": "git push origin main"}, manifest, envelope)
    assert push.allowed and push.requires_approval


def test_a_command_that_needs_approval_is_still_held_to_the_allowed_paths(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy(), allowed_paths=[str(tmp_path / "work" / "*")])
    envelope, manifest = kernel.policy.envelope(), Shell(mutates=True).manifest
    try:
        outside = kernel.tool_gateway.check(
            {"command": "cp notes.txt backup.txt", "path": "/etc/passwd"}, manifest, envelope)
        assert not outside.allowed and "outside the allowed paths" in outside.reason
        inside = kernel.tool_gateway.check(
            {"command": "cp notes.txt backup.txt", "path": str(tmp_path / "work" / "x")},
            manifest, envelope)
        assert inside.allowed and inside.requires_approval
    finally:
        kernel.close()


# ===================================== AUD-22: approval is asked about what will run

def test_a_hook_rewrite_is_approved_as_rewritten(kernel):
    """The audit's case: git status rewritten to git push origin main by a hook, no
    approval requested, and the push reached the tool."""
    shell = Shell()
    kernel.hooks.register(_rewrite_to("git push origin main"))
    with pytest.raises(ApprovalRequired):
        kernel.broker.call_tool(shell, {"command": "git status"}, kernel.policy.envelope())
    assert shell.seen == []
    (request,) = kernel.approvals.requests
    assert request["action"] == ["git", "push", "origin", "main"]


def test_the_approver_is_asked_about_the_command_that_runs(tmp_path):
    asked: list = []

    def approver(what, record):
        asked.append(record["action"])
        return True

    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=open_policy(), approval_handler=approver)
    try:
        shell = Shell()
        kernel.hooks.register(_rewrite_to("git push origin main"))
        kernel.broker.call_tool(shell, {"command": "git status"}, kernel.policy.envelope())
        assert asked == [["git", "push", "origin", "main"]]
        assert shell.seen == [{"command": "git push origin main"}]
    finally:
        kernel.close()


def test_a_hook_cannot_rewrite_a_call_into_a_refused_one(kernel):
    shell = Shell()
    kernel.hooks.register(_rewrite_to("git push --force origin main"))
    with pytest.raises(EgressDenied, match="denied command pattern"):
        kernel.broker.call_tool(shell, {"command": "git status"}, kernel.policy.envelope())
    assert shell.seen == [] and kernel.approvals.requests == []


def test_a_call_no_hook_touches_needs_no_approval(kernel):
    shell = Shell()
    kernel.broker.call_tool(shell, {"command": "git status"}, kernel.policy.envelope())
    assert shell.seen == [{"command": "git status"}] and kernel.approvals.requests == []


# ========================== AUD-24: a declassification is honoured as issued, for its content

NOTE = "Patient Alice Smith MRN 04851923 admitted with chest pain"
OTHER = "Patient Bob Jones MRN 99887766 diagnosed with HIV, lives at 12 Elm Street"


@pytest.fixture
def declassified(tmp_path):
    kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / "k").ensure_dirs(),
                           policy=PolicySnapshot(profile_id="test-declass",
                                                 declassifiers=("deid_service",)))
    note = kernel.classify(NOTE)
    assert note.label.sensitivity is Sensitivity.PHI
    deid = kernel.declassify(note, to=Sensitivity.RESEARCH_DEIDENTIFIED, method="safe harbour",
                             principal="deid_service")
    yield kernel, deid
    kernel.close()


def test_a_declassification_lowers_the_content_it_was_issued_for(declassified):
    kernel, deid = declassified
    assert kernel.ingress.ensure(deid).label.sensitivity is Sensitivity.RESEARCH_DEIDENTIFIED


def test_an_issued_record_does_not_lower_other_content(declassified):
    """The audit's case: the record issued for one note, on another note, its target
    edited to PUBLIC."""
    kernel, deid = declassified
    (record,) = deid.declassifications
    for carried in (dc.replace(record, to_sensitivity=Sensitivity.PUBLIC), record):
        forged = Labeled(value=OTHER, label=deid.label, declassifications=(carried,))
        assert kernel.ingress.ensure(forged).label.sensitivity is Sensitivity.PHI
    assert kernel.ingress.forged_declassifications == 2


@pytest.mark.parametrize("change", [
    {"to_sensitivity": Sensitivity.PUBLIC},
    {"principal": "someone_else"},
    {"method": "none"},
])
def test_an_edited_record_does_not_lower_even_its_own_content(declassified, change):
    kernel, deid = declassified
    (record,) = deid.declassifications
    edited = Labeled(value=NOTE, label=deid.label,
                     declassifications=(dc.replace(record, **change),))
    assert kernel.ingress.ensure(edited).label.sensitivity is Sensitivity.PHI
