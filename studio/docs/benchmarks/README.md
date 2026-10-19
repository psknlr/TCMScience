# Benchmarks

Measured results of the browser compute benchmark (`studio/web/test/runtime/bench.mjs`), kept as they came out: the
Markdown report and the raw JSON it was written from. [BROWSER_COMPUTE.md §9](../BROWSER_COMPUTE.md#9-performance)
discusses them.

| File | What |
|---|---|
| `2026-10-10-chromium-141-linux.md` / `.json` | The final run on branch `claude/keen-einstein-2l36a3`: Chromium 141 headless on a 4-vCPU Linux VM, SwiftShader WebGPU, native CPython 3.13 for comparison |
| `2026-10-10-chromium-141-linux-run1.json` | The run before the envelope fast paths (same machine, ten minutes earlier): the "before" column of §9.3 |
| `2026-10-18-chromium-141-linux-webgpu-software.md` / `.json` | Kernels, WebGPU and WebNN only (`--no-tools --software-gpu --webnn --reps 3`, schema `/2`): the WebGPU rows with their full dimensions, on SwiftShader (classified `software`: no speed-up, no `accelerated`). Measured while other test processes shared the four cores (load average about 3, recorded in the report), so its timings are inflated |

Every timing in them is of a computation whose output was first checked identical to the reference: a backend that
answered differently would have been a failure, not a data point (`ok: true`, `failures: []` in both). Nothing is
extrapolated, and a backend that was absent is reported as absent. The WebGPU rows ran on a software renderer and say
so; they are not a GPU's timings.

Reproduce (a local Pyodide 314.0.7 distribution, or omit `--pyodide-dir` to fetch it from jsDelivr):

```bash
cd studio && npm ci
node web/test/runtime/bench.mjs --pyodide-dir DIR --reps 3 --gpu-flags --json out.json --md out.md
# no GPU here: exercise the WebGPU code on Chromium's software renderer (the 2026-10-10 runs did this)
node web/test/runtime/bench.mjs --pyodide-dir DIR --reps 3 --software-gpu --webnn --json out.json --md out.md
# kernels, WebGPU and WebNN only (no Pyodide, no native comparison)
node web/test/runtime/bench.mjs --no-tools --software-gpu --webnn --reps 3 --json out.json --md out.md
```

- `--gpu-flags` enables WebGPU (with the `Vulkan` feature, which Chromium on Linux generally needs to expose a hardware
  adapter) and WebNN, and leaves the choice of adapter to the browser. Whether it then picks a hardware GPU depends on
  the machine, its drivers and Chromium's GPU blocklist; on the VM of the 2026-10-18 runs it still chose SwiftShader.
  The rows say which kind of adapter it was: read that, not the flag. Until 2026-10-18 the flag also forced SwiftShader
  (`--use-angle=swiftshader`), which pushed a machine with a GPU onto the software renderer; the 2026-10-10 runs were
  made that way, on a machine without a GPU.
- `--software-gpu` forces SwiftShader; `--webnn` enables WebNN alone; `--no-tools` skips the tool calls.
- Whatever the flags, every WebGPU row names its adapter as the runtime's classifier sees it (`software`, `physical`
  or `unidentified`).

Each WebGPU row (schema `tcmstudio.browser-bench/2`; the 2026-10-10 files are `/1`, whose rows were `{size, cells,
adapter: "<name>", ms, exact, software, note}`, and `bench.mjs` still renders both) records: the workload (`n`, `width`,
comparisons, records, input, output and GPU buffer bytes); the CPU baseline on the same input (each count kernel alone,
and through the bridge as Python calls it, JSON both ways); the GPU's first call on a fresh device (each workload gets
its own: adapter, device, pipeline, known-answer test) and its set-up; the warm end-to-end time (packing, upload,
dispatch, readback, the sample check, the digest, the packet's JSON) with its parts; the kernel time from GPU
timestamps where the device grants them (browsers quantize them); the JavaScript heap where the browser reports it and
the GPU memory the kernel allocates; whether the counts were exact; the known-answer test; the fallback reason; the
adapter (kind, vendor, architecture, device, description, features) and the browser and OS as they report themselves.
`accelerated` and `speedup` are filled only for a `physical` adapter, from this run's own CPU baseline; for a software
or unidentified adapter they are `null`, and no speed-up is derived from its timings.

Run it on an idle machine: other load shows up in the small timings first. The report records the load averages
before and after the run (`environment.load_avg`), and says so when other work shared the machine.
