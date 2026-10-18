# Browser compute benchmark

Measured 2026-10-18T21:30:31.770Z on Intel(R) Xeon(R) Processor (4 logical cores), linux 6.18.44-fc-v139 x64; Chromium 141.0.7390.37 Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36.
The browser reports Linux 6.18.44 x86; Chromium 141.0.7390.37, Not?A_Brand 8.0.0.0.
Cross-origin isolated: true. WASM SIMD: true, threads usable: true. Chromium flags: `--enable-unsafe-webgpu --use-angle=swiftshader --enable-features=Vulkan,WebMachineLearningNeuralNetwork` (SwiftShader forced).
WebGPU adapter: google swiftshader, classified **software** (the browser marks it a fallback adapter (isFallbackAdapter), which is typically a CPU renderer). WebNN contexts created: default, cpu, gpu, npu, low-power; WebNN detected; NPU execution unverified.
Load average (1, 5, 15 min) on 4 cores: 2.89, 3.04, 2.29 before, 3.34, 3.12, 2.33 after (other work shared the machine: the timings are inflated by it).
Medians of 3 runs. Every local result was checked identical to the reference before its time was counted.

Tool calls were not run (`--no-tools`): no Pyodide, no native comparison.


## The CPU kernels alone (no Python, no JSON)

Every WebAssembly SIMD result was compared with the JavaScript kernel's before it was timed.

| Kernel | Size | ms | ns per cell |
|---|---|---:|---:|
| needleman-wunsch | 1000×1000 | 18.73 | 18.73 |
| smith-waterman | 1000×1000 | 14.36 | 14.36 |
| levenshtein | 1000×1000 | 6.33 | 6.33 |
| needleman-wunsch | 2000×2000 | 97.2 | 24.3 |
| smith-waterman | 2000×2000 | 74.57 | 18.643 |
| levenshtein | 2000×2000 | 25.48 | 6.37 |
| needleman-wunsch | 4000×4000 | 299.36 | 18.71 |
| smith-waterman | 4000×4000 | 161.12 | 10.07 |
| levenshtein | 4000×4000 | 97.64 | 6.103 |
| distance counts (JavaScript) | 60 seqs × 1000 | 3.96 | 2.237 |
| distance counts (WebAssembly SIMD) | 60 seqs × 1000 | 0.35 | 0.198 |
| distance counts (JavaScript) | 120 seqs × 1000 | 44.42 | 6.221 |
| distance counts (WebAssembly SIMD) | 120 seqs × 1000 | 1.13 | 0.158 |
| distance counts (JavaScript) | 250 seqs × 1000 | 121.34 | 3.898 |
| distance counts (WebAssembly SIMD) | 250 seqs × 1000 | 4.75 | 0.153 |
| distance counts (JavaScript) | 40 seqs × 8000 | 14.5 | 2.324 |
| distance counts (WebAssembly SIMD) | 40 seqs × 8000 | 1.01 | 0.162 |
| hamming counts (JavaScript) | 2 × 1 000 000 | 15.57 | 15.57 |
| hamming counts (WebAssembly SIMD) | 2 × 1 000 000 | 11.19 | 11.19 |
| GC count (JavaScript) | 1 000 000 | 35.46 | 35.46 |
| GC count (WebAssembly SIMD) | 1 000 000 | 1 | 1 |

## WebGPU (the WGSL count kernel, exact parity with the CPU reference)

CPU: each kernel alone / through the bridge as Python calls it (JSON both ways), on the same input. GPU: the first call on a fresh device (adapter, device, pipeline, known-answer test), then warm calls end to end (packing, upload, dispatch, readback, the sample check, the digest and the packet's JSON), and the kernel alone from GPU timestamps where the device has them.

| Workload | Adapter | SIMD (ms) | JS (ms) | GPU first (ms) | GPU end to end (ms) | GPU kernel (ms) | Exact | Accelerated | Note |
|---|---|---:|---:|---:|---:|---:|:---:|:---:|---|
| 60 × 1000 (1770000 comparisons) | software | 0.32 / 0.7 | 3.95 / 8.405 | 277.78 | 120.56 | 112.26 | yes | not assessed | software renderer: these timings are not a GPU's, and no speed-up is derived from them |
| 120 × 1000 (7140000 comparisons) | software | 1.14 / 3.05 | 14.605 / 31.325 | 577.7 | 473.96 | 467.155 | yes | not assessed | software renderer: these timings are not a GPU's, and no speed-up is derived from them |
| 250 × 1000 (31125000 comparisons) | software | 4.905 / 13.83 | 68.735 / 77.95 | 2272.5 | 2349.42 | 2278.062 | yes | not assessed | software renderer: these timings are not a GPU's, and no speed-up is derived from them |

## WebNN

| Requested | Created | Graph output correct | Note |
|---|:---:|:---:|---|
| {} | yes | yes |  |
| {"deviceType":"cpu"} | yes | yes |  |
| {"deviceType":"gpu"} | yes | yes |  |
| {"deviceType":"npu"} | yes | yes |  |
| {"powerPreference":"low-power"} | yes | yes |  |

A created context shows that the browser accepted the request; it does not show which processor ran the graph.
Every context reported the same operator limits: probably one backend served them all.
