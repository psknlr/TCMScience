# Browser compute benchmark

Measured 2026-10-10T18:21:59.438Z on Intel(R) Xeon(R) Processor @ 2.10GHz (4 logical cores), linux 6.18.44-fc-v114 x64; Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/141.0.0.0 Safari/537.36.
Cross-origin isolated: true. WASM SIMD: true, threads usable: true.
WebGPU adapter: google swiftshader (software renderer). WebNN contexts created: default, cpu, gpu, npu, low-power.
Medians of 3 runs. Every local result was checked identical to the reference before its time was counted.

## Tool calls (Pyodide in a Worker), original Python vs local backends, and native CPython

| Tool | Size | Backend | Python in the browser (ms) | Local backend (ms) | Speed-up | Native CPython (ms) | Same output |
|---|---|---|---:|---:|---:|---:|:---:|
| `global_alignment` | 300×300 | js | 51 | 7 | 7.29× | 29.5 | yes |
| `local_alignment` | 300×300 | js | 60 | 5 | 12× | 36.0 | yes |
| `global_alignment` | 1000×1000 | js | 604 | 23 | 26.26× | 340.9 | yes |
| `local_alignment` | 1000×1000 | js | 666 | 31 | 21.48× | 429.6 | yes |
| `global_alignment` | 2000×2000 | js | 2298 | 74 | 31.05× | 1522.4 | yes |
| `local_alignment` | 2000×2000 | js | 2900 | 93 | 31.18× | 1803.9 | yes |
| `protein_alignment` | 600×600 | js | 271 | 10 | 27.1× | 192.9 | yes |
| `edit_distance` | 500×500 | js | 70 | 5 | 14× | 33.2 | yes |
| `edit_distance` | 2000×2000 | js | 1456 | 34 | 42.82× | 714.5 | yes |
| `edit_distance` | 4000×4000 | js | 5733 | 162 | 35.39× | 2543.2 | yes |
| `distance_matrix` | 20 seqs × 500 | wasm-simd | 29 | 16 | 1.81× | 23.3 | yes |
| `distance_matrix` | 60 seqs × 1000 | wasm-simd | 384 | 84 | 4.57× | 214.4 | yes |
| `distance_matrix` | 120 seqs × 1000 | wasm-simd | 1519 | 268 | 5.67× | 981.8 | yes |
| `hamming_distance` | 200 000 | wasm-simd | 30 | 15 | 2× | 12.0 | yes |
| `gc_content` | 20 000, windows of 500 | wasm-simd | 215 | 183 | 1.17× | 137.0 | yes |
| `fisher_exact` | 2×2 | pyodide | 0 | 0 | — | 0.5 | yes |

## The CPU kernels alone (no Python, no JSON)

Every WebAssembly SIMD result was compared with the JavaScript kernel's before it was timed.

| Kernel | Size | ms | ns per cell |
|---|---|---:|---:|
| needleman-wunsch | 1000×1000 | 12.36 | 12.36 |
| smith-waterman | 1000×1000 | 12.84 | 12.84 |
| levenshtein | 1000×1000 | 8.71 | 8.71 |
| needleman-wunsch | 2000×2000 | 87.54 | 21.885 |
| smith-waterman | 2000×2000 | 50.07 | 12.518 |
| levenshtein | 2000×2000 | 35.74 | 8.935 |
| needleman-wunsch | 4000×4000 | 193.68 | 12.105 |
| smith-waterman | 4000×4000 | 200.46 | 12.529 |
| levenshtein | 4000×4000 | 158.04 | 9.878 |
| distance counts (JavaScript) | 60 seqs × 1000 | 5.4 | 3.051 |
| distance counts (WebAssembly SIMD) | 60 seqs × 1000 | 0.74 | 0.418 |
| distance counts (JavaScript) | 120 seqs × 1000 | 35.82 | 5.017 |
| distance counts (WebAssembly SIMD) | 120 seqs × 1000 | 2.69 | 0.377 |
| distance counts (JavaScript) | 250 seqs × 1000 | 190.59 | 6.123 |
| distance counts (WebAssembly SIMD) | 250 seqs × 1000 | 6.41 | 0.206 |
| distance counts (JavaScript) | 40 seqs × 8000 | 20.08 | 3.218 |
| distance counts (WebAssembly SIMD) | 40 seqs × 8000 | 2.18 | 0.349 |
| hamming counts (JavaScript) | 2 × 1 000 000 | 17.23 | 17.23 |
| hamming counts (WebAssembly SIMD) | 2 × 1 000 000 | 3.99 | 3.99 |
| GC count (JavaScript) | 1 000 000 | 13 | 13 |
| GC count (WebAssembly SIMD) | 1 000 000 | 1.44 | 1.44 |

## WebGPU (the WGSL count kernel, exact parity with the CPU reference)

| Size | Adapter | ms | Exact | Note |
|---|---|---:|:---:|---|
| 60 seqs × 1000 | google swiftshader | 130.82 | yes | software renderer (SwiftShader): these timings are not a GPU's |
| 120 seqs × 1000 | google swiftshader | 578.02 | yes | software renderer (SwiftShader): these timings are not a GPU's |
| 250 seqs × 1000 | google swiftshader | 2136.96 | yes | software renderer (SwiftShader): these timings are not a GPU's |

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
