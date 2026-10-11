# Benchmarks

Measured results of the browser compute benchmark (`studio/web/test/runtime/bench.mjs`), kept as they came out: the
Markdown report and the raw JSON it was written from. [BROWSER_COMPUTE.md §9](../BROWSER_COMPUTE.md#9-performance)
discusses them.

| File | What |
|---|---|
| `2026-10-10-chromium-141-linux.md` / `.json` | The final run on branch `claude/keen-einstein-2l36a3`: Chromium 141 headless on a 4-vCPU Linux VM, SwiftShader WebGPU, native CPython 3.13 for comparison |
| `2026-10-10-chromium-141-linux-run1.json` | The run before the envelope fast paths (same machine, ten minutes earlier): the "before" column of §9.3 |

Every timing in them is of a computation whose output was first checked identical to the reference: a backend that
answered differently would have been a failure, not a data point (`ok: true`, `failures: []` in both). Nothing is
extrapolated, and a backend that was absent is reported as absent. The WebGPU rows ran on a software renderer and say
so; they are not a GPU's timings.

Reproduce (a local Pyodide 314.0.7 distribution, or omit `--pyodide-dir` to fetch it from jsDelivr):

```bash
cd studio && npm ci
node web/test/runtime/bench.mjs --pyodide-dir DIR --reps 3 --gpu-flags --json out.json --md out.md
```

`--gpu-flags` starts Chromium with WebGPU forced on (SwiftShader where there is no GPU) and WebNN enabled. Without it,
those sections report what the browser offers by default. Run it on an idle machine: other load shows up in the small
timings first.
