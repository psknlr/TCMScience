#!/usr/bin/env python3
"""IEEE-style flowchart, two columns: the trusted path one request takes, stage by stage.

The sixteen stages are psh.runtime.runner.STAGES, read from the code
(data/extracted/inventory.json); what each does is what Runner.run does in it. Two of
their orderings are kernel invariants (classification before any persistence, release
before any trusted commit), and every stage runs inside one refusal scope: an exception
the kernel raises at any stage closes the run there, refuses the quarantined output and
audits the refusal, so nothing is released by a run that stopped.

    python manuscript/code/ieee_fig_execution_path.py   # -> manuscript/ieee/IEEE_Fig_ExecutionPath.*
"""

from __future__ import annotations

from matplotlib.patches import FancyBboxPatch

import figlib as fl
import ieee_style as st

W = st.TWO_COLUMN * 25.4

#: What Runner.run does at each stage (psh/runtime/runner.py), in a few words.
DOES = {
    "ingest": "request wrapped; a run policy must lie inside the kernel's",
    "classify": "IngressGateway labels the request",
    "policy_snapshot": "a frozen policy mints the run's envelope",
    "bind": "task and run written to the WorkGraph",
    "plan": "the request becomes a plan",
    "validate_plan": "typed plans: the PlanValidator (loop)",
    "resolve": "capabilities resolved for the envelope",
    "compile_context": "context compiled within the budget",
    "preflight": "approval, budget, loop and egress checks",
    "execute": "the model call, through the broker",
    "capture_to_quarantine": "output held; its label is a join",
    "ingest_evidence": "sources become evidence records",
    "verify": "claims checked; rejections recorded",
    "release_gate": "the OutputGate; the only release",
    "commit_validated_state": "verified claims to memory",
    "checkpoint": "the run's completion audited",
}
#: Refusals Runner.run turns into a refused run (the rest close it as failed).
REFUSALS = ["EgressDenied", "VerificationFailed", "BudgetExhausted", "ApprovalRequired",
            "CapabilityUnavailable", "StuckLoop", "PolicyDenied"]


def _name(stage: str) -> str:
    """Break a long identifier after an underscore, keeping the underscore."""
    if len(stage) <= 12:
        return stage
    cut = stage.rfind("_", 0, len(stage) // 2 + 4) + 1
    return stage[:cut] + "\n" + stage[cut:]


def main():
    st.apply()
    inv = fl.load_json("inventory.json")
    stages = inv["runtime"]["runner_stages"]
    assert len(stages) == 16 and set(stages) == set(DOES), stages
    kern, _, kblock = st.ROLE["kernel"]
    ref, _, rblock = st.ROLE["refusal"]
    H = 96.0
    fig = st.figure(st.TWO_COLUMN, H)
    ax = fl.canvas(fig, 0, 0, W, H)

    x0, gap, bw, bh = 3.6, 2.6, None, 21.0
    bw = (W - 2 * x0 - 7 * gap) / 8
    rows = {0: 14.0, 1: 44.0}
    pos = {}
    for i, name in enumerate(stages):
        r, c = divmod(i, 8)
        pos[name] = (x0 + c * (bw + gap), rows[r])

    # the quarantine: output held from capture to release
    qa, qb = pos["capture_to_quarantine"], pos["release_gate"]
    ax.add_patch(FancyBboxPatch((qa[0] - 1.1, qa[1] - 1.4), qb[0] + bw - qa[0] + 2.2, bh + 2.8,
                                boxstyle="square,pad=0", fc="#e6e6e6", ec="none", zorder=1))
    st.text(ax, (qa[0] + qb[0] + bw) / 2, qa[1] + bh + 2.6,
            "quarantine: the output is held and cannot be read", ha="center", va="top",
            style="italic", color=st.DARK)
    # every stage runs inside one refusal scope
    top, bottom = rows[0] - 4.0, rows[1] + bh + 6.6
    ax.add_patch(FancyBboxPatch((1.2, top), W - 2.4, bottom - top, boxstyle="square,pad=0",
                                fc="none", ec=ref, lw=0.9, linestyle=(0, (3.0, 2.0)),
                                zorder=1))
    st.text(ax, W - 2.6, top - 0.6, "refusal scope: every stage", ha="right", va="bottom",
            style="italic", color=ref)

    for i, name in enumerate(stages):
        x, y = pos[name]
        st.block(fig, ax, x, y, bw, bh, _name(name), DOES[name], edge=kern,
                 lw=1.25 if name == "release_gate" else 0.75,
                 fill=kblock if name == "release_gate" else "white")
        st.step(ax, x + 0.2, y + 0.2, i + 1, r=1.9, colour=kern)
        nxt = stages[i + 1] if i + 1 < len(stages) else None
        if nxt and divmod(i, 8)[0] == divmod(i + 1, 8)[0]:
            st.arrow(ax, [(x + bw, y + bh / 2), (x + bw + gap, y + bh / 2)])
    # from the end of the first row to the start of the second
    (x8, y8), (x9, y9) = pos[stages[7]], pos[stages[8]]
    mid = (rows[0] + bh + rows[1]) / 2 + 0.6
    st.arrow(ax, [(x8 + bw / 2, y8 + bh), (x8 + bw / 2, mid), (x9 + bw / 2, mid),
                  (x9 + bw / 2, y9)])

    # the two orderings the kernel holds invariant
    def bracket(a, b, y, text, below=False):
        xa = pos[a][0] + 1.0
        xb = pos[b][0] + bw - 1.0
        d = -1.2 if not below else 1.2
        ax.plot([xa, xa, xb, xb], [y - d, y, y, y - d], color=kern, lw=0.75, zorder=3)
        st.text(ax, (xa + xb) / 2, y + (-0.6 if not below else 0.7), text, ha="center",
                va="bottom" if not below else "top", color=kern, style="italic")

    bracket("classify", "bind", rows[0] - 6.4, "classified before anything is written")
    bracket("release_gate", "commit_validated_state", bottom + 2.4,
            "released before anything is committed", below=True)

    # how a run ends
    ey, eh = bottom + 9.4, H - 1.0 - (bottom + 9.4)
    st.block(fig, ax, 3.6, ey, 92.0, eh, "Refused at the stage it stopped",
             ", ".join(REFUSALS) + "; the quarantined output is refused, run_refused audited",
             edge=ref, fill=rblock, name_colour=ref)
    st.arrow(ax, [(49.6, bottom), (49.6, ey)], color=ref)
    lx = pos["checkpoint"][0]
    st.block(fig, ax, 118.0, ey, W - 118.0 - 3.6, eh, "Completed",
             "released output; verified claims committed; run_completed audited",
             edge=kern, fill=kblock, lw=1.25, name_colour=kern)
    st.arrow(ax, [(lx + bw / 2, rows[1] + bh), (lx + bw / 2, ey)])
    return st.save(fig, "IEEE_Fig_ExecutionPath")


if __name__ == "__main__":
    for p in main():
        print(p)
