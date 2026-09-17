# TCMScience

Two packages that were built for different halves of one system, and the seam between
them.

```
          Agent runtime (PSH v0.6–v0.9): bounded loop, typed planner, checkpoint/resume,
          compaction, supervisor + worker pool, leases, idempotency, MCP/A2A adapters
                                    |  every action is one of three broker calls
          PSH TrustedKernel: classification at ingress, authority + policy lattices,
          model/tool/delegation gates, quarantine + release gate, hash-chained audit
                                    |  bioagent.psh — a call crosses both kernels
          BioScience capability plane: 2,567-row catalogue, 58 live-verified public
          sources / 153 typed operations, 71 native offline bio/clinical tools,
          resolver, backends, acquisition, evolution
```

| Path | What it is |
| --- | --- |
| `PSH-Harness/` | The trusted kernel and the agent runtime, at **v0.5.1**. 517 tests. |
| `BioScience-Harness/` | The capability plane, the PSH bridge and the native toolkit, at **v0.2.4**. 383 tests, 6 skipped. |
| `.github/workflows/ci.yml` | Runs both suites and the bridge on every push; live connector verification is a manual job. |
| `PSH-Harness-main (2).zip`, `BioScience-Harness-main (2).zip` | The uploads the reviews were written against, kept as provenance. |

```bash
cd PSH-Harness        && PYTHONPATH=src python -m pytest tests/ -q                       # 517 pass
cd BioScience-Harness && PYTHONPATH=src python -m pytest tests/ -q                       # 383 pass, 6 skipped
cd BioScience-Harness && PYTHONPATH=src:../PSH-Harness/src python demo_convergence.py    # live: a plan through both kernels
```

`PSH-Harness` needs `pytest` and `hypothesis`; `BioScience-Harness` needs `pandas` and
`pyarrow`. The bridge tests find the sibling `PSH-Harness/src` on their own when `psh` is
not installed.

The one rule everything above obeys: the trusted plane is immutable from inside the
system. The runtime cannot act except through the broker; the bridge depends on PSH and
PSH never depends on it; a self-evolution proposal that targets the kernel is quarantined
before it is tested.

Where to read, in order:

* `PSH-Harness/README.md` — the kernel, the runtime, and what is enforced versus stated.
* `PSH-Harness/docs/V5_1_GATE_COMPOSITION_CLOSURE.md` — the second review's findings,
  reproduced, fixed and tested.
* `PSH-Harness/docs/RUNTIME_SECURITY_REVIEW.md` — the adversarial review of the runtime
  itself: labels now travel across every edge of the loop, and the broker no longer
  trusts a projection.
* `PSH-Harness/docs/ROADMAP_AGENT_RUNTIME.md` — what was built in what order, and what is
  honestly still open.
* `BioScience-Harness/docs/V24_PSH_CONVERGENCE.md` — the bridge, the kernel boundary, the
  connector set and its verification record.
* `BioScience-Harness/docs/V23_1_CONTAINER_HONESTY.md` — container availability measured
  rather than assumed.
