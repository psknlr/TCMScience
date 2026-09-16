# TCMScience

Working repository for the **PSH-Harness** (Physician-Scientist Harness) review cycle.

| Path | What it is |
| --- | --- |
| `PSH-Harness/` | The package source, at **v0.5.1**. This is the tree under review and the one to run. |
| `PSH-Harness-main (2).zip` | The v0.5 upload the second review was written against. Kept as the provenance of the input. |
| `BioScience-Harness/` | The BioScience-Harness source, at **v0.2.3.1**. The capability plane in the convergence roadmap. |
| `BioScience-Harness-main (2).zip` | The BioScience-Harness upload the review was written against. |

```bash
cd PSH-Harness       && PYTHONPATH=src python -m pytest tests/ -q   # 345 pass
cd BioScience-Harness && PYTHONPATH=src python -m pytest tests/ -q  # 140 pass, 6 skipped
```

`PSH-Harness` needs `pytest` and `hypothesis`; `BioScience-Harness` needs `pandas` and
`pyarrow` (without the Parquet engine three of its tests fail for want of a dependency
rather than a defect).

Start with `PSH-Harness/README.md`, then:

* `PSH-Harness/docs/V5_1_GATE_COMPOSITION_CLOSURE.md` — the current round: every finding
  from the second review, its reproduction, its fix and its test.
* `PSH-Harness/docs/ROADMAP_AGENT_RUNTIME.md` — what the package is not yet, the order to
  build it in, and the PSH/BioScience convergence shape.
* `BioScience-Harness/docs/V23_1_CONTAINER_HONESTY.md` — the container-availability and
  validation-ordering defects the same review reported, reproduced and closed.
