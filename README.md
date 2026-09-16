# TCMScience

Working repository for the **PSH-Harness** (Physician-Scientist Harness) review cycle.

| Path | What it is |
| --- | --- |
| `PSH-Harness/` | The package source, at **v0.5.1**. This is the tree under review and the one to run. |
| `PSH-Harness-main (2).zip` | The v0.5 upload the second review was written against. Kept as the provenance of the input. |
| `BioScience-Harness-main (2).zip` | The BioScience-Harness v2.3 upload, for the convergence roadmap. |

```bash
cd PSH-Harness
pip install -e '.[test]'
PYTHONPATH=src python -m pytest tests/ -q     # 301 pass
PYTHONPATH=src python -m compileall -q src    # clean
```

Start with `PSH-Harness/README.md`, then:

* `PSH-Harness/docs/V5_1_GATE_COMPOSITION_CLOSURE.md` — the current round: every finding
  from the second review, its reproduction, its fix and its test.
* `PSH-Harness/docs/ROADMAP_AGENT_RUNTIME.md` — what the package is not yet, and the order
  to build it in.
