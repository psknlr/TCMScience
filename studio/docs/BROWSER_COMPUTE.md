# Browser-native computing in TCMScience Studio

What runs where when Studio (<https://science.impf.ai>) computes in a visitor's browser: which backends exist, how one is
chosen, how its results are checked, what was measured, and what is still only a plan. Written on 2026-10-10 against
branch `claude/keen-einstein-2l36a3`. Every number here was measured on the machine named in [§9](#9-performance);
anything not measured says so.

Status words used throughout:

- **Implemented and tested**: in the code, covered by automated tests that ran green for this report.
- **Implemented, partly verified**: in the code, but checked only where this environment allows (for example, WebGPU
  only on a software adapter).
- **Proposed**: designed here, not implemented. Nothing in the app claims it.

Related: the per-entry [compatibility matrix](compat/summary.md) ([CSV](compat/matrix.csv)), the
[benchmark report](benchmarks/2026-10-10-chromium-141-linux.md), [BROWSER_ACCESS.md](BROWSER_ACCESS.md) (data access),
[CONTRACT_NOTES.md](CONTRACT_NOTES.md) (`receipt.compute`, in "browser-compute").

---

## 1. What was found (audit)

**Shape of the system.** Studio is a static single-page app (`studio/web`, ES modules, no framework, no build step), a
Cloudflare Worker (`studio/edge`: static files, the `/v1` relay to the Tao-S1 model, the bounded `/api/sources` gateway
to public databases) and an optional local runner (`tcmstudio serve`, native CPython). In the browser, tools run in a
Web Worker on **Pyodide 314.0.7** (CPython 3.14 compiled to WebAssembly) executing the real `psh`, `bioagent` and
`tcmstudio` packages from a SHA-256-verified bundle, so a browser call and a runner call return the same envelope
(CONTRACTS §3). The tool catalog has **647 entries** (25 core tools); **629 run in the browser** today
([matrix](compat/summary.md)).

**Where computation happened before this work.** Every browser tool ran single-threaded on the CPU in Pyodide, except
three sequence tools whose integer counts had a WebGPU kernel. Model inference never ran in the page: Tao-S1 goes
through the relay (remote), "your own API" goes from the browser to that provider (remote), and local models are
servers the person runs (Ollama, LM Studio, llama.cpp, vLLM). No model ships with the site.

**Hot spots** (measured in the browser, [§9](#9-performance)): the dynamic programmes of sequence alignment and edit
distance (pure-Python O(n·m) loops: a 2000 × 2000 local alignment took 2.9 s in Pyodide); pairwise sequence counts
(O(n²·w)); and, for large results, the envelope's own post-processing ([§9.3](#93-the-envelope-overhead)). Not ported
yet: neighbour joining and UPGMA (O(n³) in Python) and the network-pharmacology permutation null (1000 seeded
permutations per pathway; a port must reproduce Python's Mersenne Twister draws exactly).

**Defects found and fixed along the way**:

| Defect | Effect | Fix |
|---|---|---|
| The web-access (联网) switch toggled from the stored record, which changed only once its IndexedDB write landed | A quick double press ended **on** | The switch follows the latest press while its write is under way; writes are queued in press order |
| The project page's model and compute selects wrote back the whole defaults object drawn earlier | Changing the model turned web access **off** again | Defaults merge inside the write's transaction; each control sends only what it changes |
| Copies of the switch (composer, compute popover, project page) did not follow each other | A press on a stale copy was a no-op | Every copy follows the `web` and `project` events, and other tabs (BroadcastChannel) |
| The runner's network switch sent the whole settings object, concurrently, and said nothing when the runner was not ready | Out-of-order writes, lost changes, silent failures | One patch at a time; the switch is redrawn from what the runner holds; a failure is said |
| Approvals were granted and revoked by read-modify-write | A revoked `:network` approval could come back | Atomic grant/revoke in one transaction |
| A WebGPU count packet was not bound to its input; a bad packet failed the call | Counts from another input could be used; a GPU fault failed a correct call | The packet carries the input's SHA-256; Python recounts samples; a bad packet is ignored and Python counts |
| `receipt.device` stayed `cpu` when GPU counts were used | Contradicted CONTRACTS §3 | `webgpu` when (and only when) GPU counts were used |
| The scheduler learned an in-Python path's cost from its instant hand-off | CPU paths about 40% underestimated after a few calls | Such backends declare `defers`; the Worker reports the kernel's own time |

Each was reproduced first (unit tests, and for the switches in Chromium) and has a regression test.

---

## 2. Compatibility matrix

[`compat/matrix.csv`](compat/matrix.csv) has one row per catalog entry (`id`, `kind`, `class`, `local_backends`,
`network`, `offline`, `gpu`, `deps`, `mobile`, `fallback`, `validation`, `notes`). It is generated from the catalog
(`python3 studio/scripts/compat_matrix.py`); a runner test fails when the committed copy is out of date.

| Class | Meaning | Entries |
|---|---|---:|
| 1 | Runs in the browser now, unchanged (Pyodide, CPU) | 169 |
| 1g | Runs in the browser now; its data comes from a public API through the bounded gateway | 444 |
| 2 | A JavaScript CPU kernel computes the hot loop, byte-identical to Python | 4 |
| 3 | WebGPU or WebAssembly SIMD integer reductions, exact, recounted in Python | 3 |
| 4 | WebNN candidate (a neural model would run here) | 0 |
| 5 | A WASM SIMD / threads kernel would pay off; not written | 9 |
| 6 | Needs substantial work for the browser (data size, memory, a job model) | 3 |
| 7 | Needs a native engine or GPU framework (RNA-seq, docking, ADMET, folding) | 9 |
| 8 | Stays on the local runner (processes, files, long jobs, MCP servers) | 6 |

Class 4 is empty because no tool runs a neural model; the matrix does not pretend otherwise. Classes 7 and 8 stay on
the runner: a browser cannot run STAR, AutoDock Vina or PyTorch-CUDA, and should not pretend to. Nothing was removed:
every entry that ran before still runs, where it ran before.

---

## 3. Architecture

```
 page (UI thread)                                Worker (Pyodide)
 ────────────────                                ─────────────────────────────────────────────────────────────
 tool call ──postMessage──► accelerator.prepare(tool, args, {preference: auto | cpu | reference})
                             │ scheduler.plan → webgpu | wasm-simd | js | python
                             │   (cost model, then this device's measurements; exclusions; set-aside)
                             ├─ webgpu chosen → WGSL counts → packet {counts, input_sha256}
                             ▼
                            Python  tcmstudio.browser_compute.call_json(tool, args, ctx, packet, {kernels})
                             │ dispatch → bioagent policy kernel → the tool's own Python function
                             │   counts:     the packet, if bound to this input and its checks pass,
                             │               else KERNELS["sequence.counts"]  (bridge → WASM SIMD | JS)
                             │   alignment:  KERNELS["align.nw_linear" | "align.sw_linear"]     (bridge → JS)
                             │   edit dist.: KERNELS["sequence.levenshtein"]                     (bridge → JS)
                             │   every kernel result is checked in Python before it is used
                             ▼
                            envelope + receipt.compute {backend, precision, kernels[], requested, plan}
```

**Backends** ([`compute/scheduler.js`](../web/js/compute/scheduler.js) kinds):

| Backend | Exact | Where | Used for | Status |
|---|:---:|---|---|---|
| `webgpu` | yes | GPU, WGSL compute shader | distance / Hamming / GC counts | Implemented, partly verified (software adapter only) |
| `wasm-simd` | yes | CPU, 128-bit WebAssembly SIMD | the same counts | Implemented and tested |
| `js` | yes | CPU, JavaScript in the Worker | alignments, edit distance; the counts without SIMD | Implemented and tested |
| `python` | yes (reference) | CPU, Pyodide | everything | Unchanged |
| `webnn` | — | CPU / GPU / NPU, as the browser decides | nothing yet | Probed and reported only |
| `wasm` (threads) | — | CPU, several cores | nothing yet | Proposed |
| `runner` / `native` | yes | the person's computer | jobs, native engines, GPU frameworks | Unchanged (existing runner) |
| `remote` | — | elsewhere | nothing: no remote compute backend is registered | — |

**Rules the scheduler enforces** (tested in `web/test/compute.test.mjs`):

- Exact work goes only to exact backends. An approximate backend could at most rank candidates that an exact one
  re-scores; no such pairing is used today.
- A task marked sensitive never goes to a non-local backend unless it carries the person's consent
  (`consent.remote === true`). No remote compute backend exists, so nothing leaves the device for computation; the rule
  is there so that adding one cannot silently change that.
- A cancelled task stops; it is not retried elsewhere.
- A backend that loses its device is set aside for the session at once; one that fails twice is set aside too.
- Every run carries its provenance: what was asked for, what was considered (with estimates), what was passed over and
  why, what was tried, what ran, and whether its output was verified.

**Exactness contract.** A kernel either returns what the Python implementation returns, to the bit and the type, or
declines, and Python runs. For the alignments that means IEEE-754 doubles carried across the boundary as their 16 hex
digits (JSON would turn `4.0` into `4` and lose the sign of `-0.0`), Python's int-versus-float typing of scores, its
first-maximum tie-breaking, and code points rather than UTF-16 units. Python checks every alignment it receives (the
path re-scores to the reported score, coordinates are consistent) and every count buffer (shape, integer type, bounds,
consistency, and a few records recounted itself). The limit of that check is documented in `bioagent/tools/align.py`.
For an alignment it proves validity and the exact score of what was returned. It does not prove optimality, nor that,
among equally good alignments, the kernel returned the one Python's traceback would choose. That the kernel runs the
same dynamic programme and breaks ties the same way is shown by the parity tests (374 fixture cases from the Python
reference, plus randomized end-to-end runs against it), not by the check.

---

## 4. What was implemented (code)

| File | What it does |
|---|---|
| [`web/js/compute/capabilities.js`](../web/js/compute/capabilities.js) | Probes by trying, never by browser name: WebAssembly SIMD, relaxed SIMD, threads, bulk memory (validated modules); WebGPU adapter, features, limits, software renderers named; WebNN contexts per requested device with operator-limit fingerprints; storage quota, persistence, OPFS, Cache API; timer resolution. Schema `tcmstudio.capabilities/1`. |
| [`web/js/compute/scheduler.js`](../web/js/compute/scheduler.js) | The adaptive scheduler of [§3](#3-architecture) and [§5](#5-the-hardware-aware-runtime). |
| [`web/js/compute/kernels.js`](../web/js/compute/kernels.js) | Needleman–Wunsch and Smith–Waterman (linear gap, any score table), Levenshtein, sequence counts, in JavaScript, with Python's semantics; each polls the Worker's interrupt flag. |
| [`web/js/compute/wasm/seqcount.wat`](../web/js/compute/wasm/seqcount.wat) → `seqcount.wasm.js` | The SIMD count kernel (691 bytes), compiled with wabt by `studio/scripts/build_wasm.mjs`; a test fails if the committed binary is not what the text compiles to. |
| [`web/js/compute/simd.js`](../web/js/compute/simd.js) | Instantiates it synchronously from the embedded bytes; copies letters in with `TextEncoder.encodeInto`; lets go of a memory grown past 16 MB. |
| [`web/js/compute/bridge.js`](../web/js/compute/bridge.js) | The one function Python calls (`call(name, json) → json`): SIMD counts, else JavaScript; declines on any error. |
| [`web/js/compute/accelerator.js`](../web/js/compute/accelerator.js) | The Worker's decision before a sequence tool runs (GPU packet or not), and learning from what ran. |
| [`runner/src/tcmstudio/browser_compute.py`](../runner/src/tcmstudio/browser_compute.py) | The Python side: binds packets to inputs, decides when a kernel is worth calling, checks results, writes `receipt.compute`. |
| `BioScience-Harness/src/bioagent/tools/{_compute,align,sequence}.py` | Call-scoped kernel hooks (ContextVars), each tool's fast path and its check. |
| [`web/sw.js`](../web/sw.js), [`web/js/core/offline.js`](../web/js/core/offline.js) | The service worker of [§7](#7-offline-storage-and-caching). |
| [`web/test/runtime/bench.mjs`](../web/test/runtime/bench.mjs) | The benchmark of [§9](#9-performance). |
| [`scripts/compat_matrix.py`](../scripts/compat_matrix.py) | The matrix of [§2](#2-compatibility-matrix). |

The settings page has **Browser acceleration: Auto / CPU only / Python reference only**. Auto lets the scheduler pick;
CPU only excludes the GPU (and any NPU); the reference runs the original Python for every tool, for checking results.
Computation starts only when the person runs a tool: opening the page computes nothing.

---

## 5. The hardware-aware runtime

**Capabilities: requested, reported, verified.** These are kept apart because browsers blur them:

- *Requested*: what the page asked for (`navigator.ml.createContext({deviceType: "npu"})`).
- *Reported*: what the browser says (a context was created; an adapter's `vendor`/`architecture`).
- *Verified*: what a computation proved (a WGSL kernel's counts equal the CPU's; a WebNN graph's output is correct).

Measured in Chromium 141 on a Linux VM with no GPU and no NPU, with WebNN enabled: contexts were created for every
request, including `deviceType: "npu"`, and every context reported identical operator limits. A created "npu" context is
therefore not evidence of an NPU, and Studio does not claim one: Settings says "an NPU is unverified; no tool uses
WebNN yet". The only WebGPU adapter there was SwiftShader, a CPU emulator. The probe names it as a software renderer and
the runtime refuses it for real work (the counts then run on the CPU).

**Choosing a backend.** For each sequence-count task the scheduler orders the supported backends by estimated cost.
At first the estimate comes from a cost model: per-operation costs, a fixed GPU dispatch cost, and the cost of handing
the counts to Python. Once this device has measured a backend at a given size (log₂ buckets, exponentially weighted,
α = 0.3), the measurement is used instead. The model's constants are estimates. The CPU ones were measured here (SIMD
about 2–4 × 10⁻⁷ ms per comparison, JavaScript about 3–6 × 10⁻⁶); the GPU ones could not be, because no hardware GPU
was available. With these estimates the GPU wins only past roughly 3 × 10⁷ comparisons, and SIMD takes everything
below. A device with a fast GPU learns otherwise after its first runs.

**Memory and limits.** The GPU kernel takes up to 128 M comparisons, 64 MB of input and 8 MB of counts. The SIMD kernel
takes up to 256 MB of working memory, and a memory grown past 16 MB is released after the call. Python decides whether
a kernel is worth calling at all (`worth_a_kernel`): small inputs stay in Python, where crossing into JavaScript would
cost more than it saves. GC windows shorter than 256 stay too; that threshold was measured.

**Cancellation.** On a cross-origin isolated page (the site is: COOP `same-origin`, COEP `require-corp`) a stop sets a
byte in a `SharedArrayBuffer`. The kernels poll it between rows, and Python raises `KeyboardInterrupt` at its next
bytecode. A 30 000 × 30 000 edit distance in the JavaScript kernel, stopped 600 ms in, returned `cancelled` at 607 ms,
with no Worker restart. Without isolation the Worker is terminated and restarted, as before.

**Device loss and errors.** A WebGPU `device lost` sets the GPU aside for the session. A kernel that throws declines and
Python computes. A result that fails its check is not used, and the receipt says why (`gpu_rejected`, or a kernel event
`rejected` with its reason). No accelerator failure fails a call.

---

## 6. WebGPU, WebAssembly and WebNN

**WebGPU** is used where it is exact: integer reductions (sites, differences and transitions between aligned sequences,
mismatches, GC counts) in a WGSL compute shader. Floating-point model transforms, rounding, errors and verdicts stay in
Python. WebGPU is not a CUDA substitute here. No Python library runs on it, and the GPU frameworks used by the runner's
jobs (PyTorch for structure prediction, for example) cannot be loaded in a page. Its code paths were exercised only on
SwiftShader (exact, as the benchmark checks, but slow); its speed on a real GPU is **unmeasured**.

**WebAssembly is the portable CPU layer.** Pyodide runs everything; the SIMD kernel speeds up the counts. SIMD is in
every current engine (Chrome 91+, Firefox 89+, Safari 16.4+), and where it is missing the JavaScript kernel takes its
place. Relaxed SIMD is probed but not used: its results may differ between machines, which exact work cannot allow.
Threads are available (the site is isolated) but not used yet: Python in Pyodide is single-threaded, and the kernels
are synchronous calls from Python, so parallel kernels would need a pool of Workers and an asynchronous boundary
(proposed, [§11](#11-roadmap)).

**WebNN** is a progressive enhancement that no tool needs. The intended use is a packaged ONNX model (term embeddings,
a classifier) run through ONNX Runtime Web with the execution providers tried in order WebNN → WebGPU → WASM. The
order would not be fixed: the scheduler would demote a provider that is slower here or fails a check. The checks would
be: unsupported operators found before running (`opSupportLimits`), and every provider's output compared with the WASM
provider's on a fixed probe input, within a stated tolerance, before it is trusted. Its results would be marked
approximate, so exact work could never route to it. **Proposed**: no model is packaged, and the model hubs and CDNs
needed to fetch one are unreachable from this build environment, so nothing of it could be verified here.

**A native NPU layer** is **proposed, not implemented**, and would stay optional. A web page cannot reach NPU drivers,
local services or executables by itself, and Studio does not try. If it is ever built, it should be a capability of the
existing local runner rather than a new companion app. The runner already has the narrow, authenticated interface such
a layer needs: it binds to loopback, checks `Host`, allows listed origins only, requires a pairing token, and the page
shows when it is connected. It would add one operation (for example, an embedding batch through a named model on
OpenVINO, QNN or Core ML) with the same envelope and receipt, the device reported by the native runtime rather than
guessed, and the person choosing it. The app must keep working without it.

---

## 7. Offline, storage and caching

**Implemented and tested** (`web/test/sw.test.mjs`, `runner/tests/test_webbuild.py`, and offline reloads in Chromium):

- A service worker (`web/sw.js`) keeps the app shell (73 files, each listed in a manifest the build writes with its
  SHA-256, and checked against it when cached), Pyodide, and the fonts. The page loads and runs tools with the network
  off. An offline run produced the same output hash as an online one.
- Each version is a consistent snapshot. A new version installs in the background and is offered ("a new version is
  ready"); it takes over only when the person reloads.
- It never caches `/v1` (model traffic) or `/api` (database queries), nor range requests. `?nosw` unregisters it and
  deletes its caches, as a way out. Automated browsers get it only when they ask (`?sw`).
- The Python bundle and data assets were already content-addressed: kept by SHA-256 in the runtime cache and verified
  before use. Projects, conversations, files and keys stay in IndexedDB. The Python state of governed runs is synced
  to IndexedDB (IDBFS) where it works, and the receipt says `durable: false` where it does not.
- The capability probe reports quota, usage, whether storage is persisted, OPFS and the Cache API.

**Proposed**: ask for persistent storage (`navigator.storage.persist()`) when the person downloads something large;
resumable, content-addressed model downloads into OPFS, with a quota check before the download starts.

---

## 8. Privacy and security

- **No new path off the device.** The kernels run in the same Worker, on the same origin, on data already there. The
  scheduler has no remote backend, and the sensitive-data rule ([§3](#3-architecture)) means one added later cannot
  receive sensitive input without the person's consent.
- **Nothing was weakened for speed.** Cross-origin isolation is unchanged. The SIMD module is embedded and compiled
  under the `'wasm-unsafe-eval'` that Pyodide already required. No new permission, origin, or native access was added.
- **What reaches servers is unchanged.** As the README's privacy section says: model calls go to the model chosen
  (Tao-S1 through the relay, which does not store them; your own provider; or a local model). Database queries go
  through the gateway only when the project's web access is on and the call is approved. Computation stays on the
  person's own devices: the browser, or their runner. The exceptions are runner jobs that call a third-party service
  (remote structure prediction, for example), which run only when the runner's `allow_remote` setting is on.
- **Labelled honestly.** Every receipt names the backend that produced the numbers (`webgpu`, `wasm-simd`, `js`,
  `pyodide`). A software GPU is labelled as software. An NPU is never claimed. Settings says what each acceleration
  choice does.
- **Proposed hardening**: move the CSP from report-only to enforcing once its reports are reviewed (it already lists
  only the origins in use).

---

## 9. Performance

All figures are from [`benchmarks/2026-10-10-chromium-141-linux.md`](benchmarks/2026-10-10-chromium-141-linux.md)
(raw: the `.json` beside it). Machine: a 4-vCPU Intel Xeon VM at 2.10 GHz, 15 GB, Linux 6.18. Browser: Chromium
141.0.7390.37, headless, cross-origin isolated, with `--enable-unsafe-webgpu --use-angle=swiftshader
--enable-features=Vulkan,WebMachineLearningNeuralNetwork`, driven from Node 22.22. Pyodide 314.0.7 (CPython 3.14.2) was
served locally. The native comparison used CPython 3.13.16 on the same machine.

Each figure is the median of 3 runs. A local result counted only after it was checked identical to the reference (the
same output hash). Reproduce with `node studio/web/test/runtime/bench.mjs --pyodide-dir DIR --reps 3 --gpu-flags`.
Two full runs ten minutes apart differed by under 10% for the slow pure-Python references, and by up to 1.7× for single
kernel timings (JIT and garbage collection on a shared VM). Read the small figures as orders of magnitude.

### 9.1 Whole tool calls

The whole call: validation, the policy kernel, the tool, and the envelope with its hashes and citations.

| Tool | Size | Backend | Pyodide Python (ms) | Local backend (ms) | Speed-up | Native CPython (ms) |
|---|---|---|---:|---:|---:|---:|
| `global_alignment` | 300 × 300 | js | 51 | 7 | 7× | 30 |
| `global_alignment` | 1000 × 1000 | js | 604 | 23 | 26× | 341 |
| `global_alignment` | 2000 × 2000 | js | 2298 | 74 | 31× | 1522 |
| `local_alignment` | 1000 × 1000 | js | 666 | 31 | 21× | 430 |
| `local_alignment` | 2000 × 2000 | js | 2900 | 93 | 31× | 1804 |
| `protein_alignment` (BLOSUM62) | 600 × 600 | js | 271 | 10 | 27× | 193 |
| `edit_distance` | 2000 × 2000 | js | 1456 | 34 | 43× | 715 |
| `edit_distance` | 4000 × 4000 | js | 5733 | 162 | 35× | 2543 |
| `distance_matrix` (k2p) | 20 seqs × 500 | wasm-simd | 29 | 16 | 1.8× | 23 |
| `distance_matrix` (k2p) | 60 seqs × 1000 | wasm-simd | 384 | 84 | 4.6× | 214 |
| `distance_matrix` (k2p) | 120 seqs × 1000 | wasm-simd | 1519 | 268 | 5.7× | 982 |
| `hamming_distance` | 200 000 | wasm-simd | 30 | 15 | 2× | 12 |
| `gc_content` | 20 000, windows of 500 | wasm-simd | 215 | 183 | 1.2× | 137 |
| `fisher_exact` (no kernel) | 2 × 2 | pyodide | < 1 | < 1 | — | 0.5 |

- The alignments and the edit distance in the browser are now **7–43× faster than Pyodide's Python, and faster than
  native CPython** on the same machine: a 2000 × 2000 alignment takes 74 ms in the page against 1.5 s natively. These
  results are byte-identical to the Python reference.
- The distance matrices gain 1.8–5.7×. The counting itself now takes about 3 ms (§9.2). What remains is Python's per-pair
  transforms, the JSON hand-off of n² counts, Python's checks, and the envelope (§9.3).
- Hamming distance and GC windows are linear, and Python's `str.count` is already C, so there is little to win.

### 9.2 The kernels alone (no Python, no JSON)

| Kernel | Size | JavaScript (ms) | WebAssembly SIMD (ms) |
|---|---|---:|---:|
| Needleman–Wunsch | 4000 × 4000 | 194 (12 ns per cell) | — |
| Smith–Waterman | 4000 × 4000 | 200 (13 ns per cell) | — |
| Levenshtein | 4000 × 4000 | 158 (10 ns per cell) | — |
| distance counts | 60 seqs × 1000 | 5.4 | 0.74 |
| distance counts | 120 seqs × 1000 | 35.8 | 2.7 |
| distance counts | 250 seqs × 1000 | 191 | 6.4 |
| distance counts | 40 seqs × 8000 | 20.1 | 2.2 |
| Hamming counts | 2 × 1 000 000 | 17.2 | 4.0 |
| GC count | 1 000 000 | 13.0 | 1.4 |

SIMD counts were checked equal to the JavaScript ones before they were timed. They are 4–30× faster (0.2–0.4 ns per
comparison against 3–6 ns).

### 9.3 The envelope overhead

Profiling the 120 × 1000 call showed that, once the counting was fast, most of the time went to building the envelope.
The result was copied to JSON-ready form about seven times (for the hashes, the citations, the labels, the model's
text), with an abstract-base-class `isinstance` check on every container, and the citation scan made one on every
number. Exact-type fast paths now cover the plain JSON types. Their answers are those of the original code, which still
handles everything else, and a differential test checks them against a verbatim copy of it over thousands of generated
values. Effect in Pyodide (run 1 before the change, run 2 after, same machine):

| Call | Before (ms) | After (ms) |
|---|---:|---:|
| `distance_matrix` 120 × 1000, SIMD | 499 | 268 |
| `distance_matrix` 120 × 1000, Pyodide Python | 1861 | 1519 |
| `distance_matrix` 60 × 1000, SIMD | 142 | 84 |
| `gc_content` 20 000 with windows of 500, Pyodide Python | 301 | 215 |

Natively, with the counting taken out, the same call fell from a median of 214 ms to 138 ms (15 calls each, two
alternating rounds), about 1.6×. Calls with large results gain the most, in the browser and on the runner alike;
calls with small results are unaffected.

### 9.4 WebGPU, WebNN and start-up

WebGPU ran only on SwiftShader, a CPU emulation, so its timings (131 ms for 60 × 1000, 2.1 s for 250 × 1000) say
nothing about a GPU. They do show that the WGSL kernel's counts are exact. A hardware GPU's speed is **unmeasured**.
WebNN accepted every context request, including `npu`, and computed a small graph correctly on each. All of them
reported the same operator limits, so this shows nothing about which processor ran them. Pyodide was ready 3.8 s
after the page asked, 2.2 s of it loading Pyodide and 1.2 s importing the packages. Here the files came from loopback;
a first visit downloads about 11 MB from jsDelivr, and later visits read them from the cache.

---

## 10. Validation

| Suite | What it proves | Result |
|---|---|---|
| `cd studio && npm test` (Node 22) | Kernels against 374 fixture cases from the Python reference (SIMD and JavaScript counts both); SIMD against JavaScript over awkward widths, alphabets and refusals; the committed WebAssembly binary against its source; scheduler rules; capability probing; the service worker; the network-switch races (19 tests) | 331 passed, 1 skipped (bioagent not installed for the system Python) |
| `pytest studio/runner/tests` | The Python side: packets bound to inputs, lying and broken kernels caught, the real JavaScript kernels driven through Node (with SIMD and without) against the original Python, the envelope fast paths against the original code, the web build and its service-worker manifest, the matrix | 325 passed |
| `pytest -m unit BioScience-Harness` | The tools' kernel hooks and checks; everything else in bioagent | 2534 passed, 131 skipped |
| `node --test studio/edge/test` | The Worker: relay, gateway, headers | 63 passed |
| `node studio/web/test/runtime/run.mjs` | Real Pyodide in Chromium 141: 47 checks (governed skills, tools, kernels with each acceleration choice, cancellation), reload from cache, a non-isolated page, a tampered bundle refused; 17 envelopes compared field by field with native CPython | all passed |

**Cross-platform status.** Tested: Chromium 141 headless on Linux x86-64, isolated and not isolated; Node 22 (V8)
for the kernels; native CPython 3.13 for the comparisons. The repository's Playwright suite also emulates a phone (390 ×
844, touch, CPU throttled 4×), but it fetches Pyodide from jsDelivr, which this build environment cannot reach, so it
was not re-run for this report. **Not tested**: Firefox, Safari / iOS, Android devices, any hardware GPU, any NPU.
SIMD, WebGPU and WebNN support in those browsers comes from their vendors' documentation, not from a run here. On
them the code is built to degrade: no SIMD means the JavaScript kernel, no usable GPU means the CPU, no WebNN means
nothing changes.

---

## 11. Roadmap

| Phase | Work | Priority | Status |
|---|---|---|---|
| 1 | Network switches made reliable; capability probe; scheduler; exact CPU kernels (JavaScript, WASM SIMD); WebGPU packets bound and checked; offline service worker; matrix; benchmark; envelope fast paths | — | **Done** (this branch) |
| 2 | Ranked multi-term formula retrieval (today `tcmdb.formulas` is one substring, in row order) | P1 | Proposed |
| 2 | Network-pharmacology permutation null in WebAssembly, drawing exactly what Python's MT19937 draws | P1 | Proposed |
| 2 | A compact count format across the Pyodide boundary (JSON dominates large matrices) | P1 | Proposed |
| 2 | CSP enforcing; `storage.persist()` on large downloads; Firefox and WebKit runs of the runtime check in CI | P1 | Proposed |
| 3 | One ONNX embedding model for TCM terms through ONNX Runtime Web (WebNN → WebGPU → WASM, each checked against WASM) | P2 | Proposed |
| 3 | WASM threads for distance-matrix rows and neighbour joining; SIMD alignment (anti-diagonals) | P2 | Proposed |
| 4 | Optional NPU operation in the local runner ([§6](#6-webgpu-webassembly-and-webnn)); in-browser chat models (WebLLM) for desktops with a capable GPU, opt-in and labelled | P3 | Proposed |
| 4 | Small-dataset browser mode for single-cell analysis | P3 | Proposed |

---

## 12. Final report

**Implemented and tested**

- The web-access and runner network switches: four root causes fixed, plus cross-tab sync, atomic approvals, an
  IndexedDB reconnect, and failures said instead of swallowed.
- A capability probe that keeps requested, reported and verified apart, shown in Settings and in `system.capabilities`.
- An adaptive scheduler with exactness, privacy, cancellation and set-aside rules, cost model plus measured history,
  and provenance on every run.
- Exact CPU kernels for 7 tools: Needleman–Wunsch, Smith–Waterman and Levenshtein in JavaScript; the sequence counts
  in WebAssembly SIMD, with JavaScript as fallback. Each is checked in Python before use, and each receipt names it.
- WebGPU count packets bound to their input by SHA-256 and recounted in Python; a bad packet is ignored, never fatal.
- An offline-capable app shell and Python runtime (service worker, verified manifest, opt-in updates, kill switch).
- A compatibility matrix of all 647 catalog entries, generated and checked in CI.
- A benchmark harness that times only outputs checked equal to the reference, and the measurements in [§9](#9-performance).
- Envelope post-processing for large results about 1.6× faster natively, and the 120 × 1000 distance-matrix call
  1.9× faster in the browser, byte-identical (differential tests against the original code).

**Implemented, partly verified**

- The WebGPU path: exact on SwiftShader; never run on a hardware GPU here, so its speed is unknown.
- WebNN probing: contexts and a small graph checked in Chromium; which processor ran them cannot be known from the page.

**Proposed, not implemented** (nothing in the app claims these): WebNN or WebGPU model inference (embeddings,
classifiers, local chat models), WASM-threaded kernels, the MT19937-exact permutation port, ranked formula retrieval,
a native NPU operation, persistent-storage requests and model caching in OPFS, an enforcing CSP, and testing in
Firefox, Safari, on phones, GPUs and NPUs.

**What Studio may say about itself, then**: tools run locally in the browser, on the CPU, with exact kernels for
alignment, edit distance and sequence counts, and on the GPU for large count workloads where a hardware adapter
exists (its speed unmeasured here). Model calls are remote unless the person runs a local model. No NPU is used.
