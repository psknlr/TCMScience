# Browser data access and local computation

Studio exposes the complete registered public-source operation catalog through the
same-origin `/api/sources/request` gateway. The manifest is generated from
`bioagent.providers.public_apis.SOURCES` at build time, so the browser and edge use
the same reviewed paths, methods, query fields and body templates. The gateway is
not an arbitrary URL proxy. It validates origins, templates, hosts, redirects,
credentials, request sizes and response sizes, and applies visitor and source
rate limits. `/api/sources/health` reports the deployed manifest counts and limits.

Project web access is off by default. The existing approval flow still applies.
Python policy checks, request rendering, parsers and error statuses run inside the
Pyodide Worker; only HTTP transport crosses the edge. Approved database URLs,
query bodies and supported request headers therefore pass through science.impf.ai
and the chosen source. Neither the edge gateway nor its invocation logs retain
these payloads. The browser keeps local project state. A source can still be
unavailable due to its own login, API terms, outage or unsupported response size.

Some registered upstream sources offer HTTP only. Those exact public endpoints
remain accessible, but credential headers are stripped from their unencrypted
transport. HTTPS-to-HTTP redirects are refused. Credentials never cross hosts.
Bulk downloads and large model assets are outside this bounded query gateway.

The repository formula workbook is indexed in full, preserving all seven columns,
duplicate names and source versions. Its compressed SQLite asset is verified against
the build manifest's SHA-256 before use and loads when a lookup needs it: before
`tcm_formula`, `tcm_lookup`, `tcm_herb`, `tcm_syndrome` and `tcmdb.formulas`, which always read it,
and during a call that turns out to need it (a compatibility check of a formula
outside the seed), which then runs again with it. Formula questions are answered
from all 84,294 rows: a herb's formulas (`tcm_herb`, under the herb's other names,
other drugs whose names contain it excluded), a syndrome's formulas (`tcm_syndrome`:
those whose recorded 功效 mentions it, never their 注意 cautions), a formula's source
versions, and the herbs of a formula checked for incompatibilities when the workbook
holds one version of it. `tcmdb.formulas` supports pagination, source, herb and
indication filters and a ranked mode; `tcmdb.materia` exposes the complete packaged materia collection. The original
workbook remains available as a separate static asset. Its
`LicenseRef-user-supplied-unstated` provenance is retained. These are source
records, not newly validated clinical recommendations. The curated clinical/safety
seed (23 herbs, 6 formulas) keeps its original evidence annotations, and the
governed skills, whose code is pinned, still run on it. When the workbook cannot be
loaded, an answer says it searched the seed only, and `capabilities_status`
reports which formula corpus is available.

Native Python tools continue to run on the device's CPU in WebAssembly (Pyodide).
Seven tools also have exact local kernels in the Worker: the sequence alignments
(global, local, protein) and the edit distance in JavaScript, and the integer
counts of `native.distance_matrix`, `native.hamming_distance` and
`native.gc_content` in WebAssembly SIMD, or with WebGPU where the scheduler
expects the GPU to be faster. The original Python functions still compute the
scientific formulas, rounding, validation and policy processing, and check every
kernel result before using it; the output is byte-identical to Python's. Receipts
identify the actual backend (`receipt.compute`). No accelerator is used merely
because a visitor opens the page. Settings → Compute → Browser acceleration
chooses Auto, CPU only (no GPU) or the Python reference only. GPU failure,
unsupported inputs or resource bounds fall back to the CPU kernels, then to
Python. A GPU adapter's existence does not imply all tools are accelerated.
Design, rules and measurements: [BROWSER_COMPUTE.md](BROWSER_COMPUTE.md).

`system.provider_catalog` searches all 2,567 repository capability entries, including
286 databases. `system.provider_call` exposes the reviewed ToolUniverse allowlist
and admitted BioMCP operations through the local Runner. Missing dependencies and
unreviewed server pins return explicit unavailable results. These wrappers depend
on reviewed versions and local Python/MCP process transports; their coverage and
requirements are surfaced separately from browser HTTP connectors.
Full database copies, unrestricted third-party code, large
omics/docking engines and general NPU access cannot be supplied by a browser API
alone. No WebNN or NPU acceleration is used or claimed: WebNN is probed and
reported (Settings, `system.capabilities`), but a context the browser creates for
an "npu" request is not evidence of an NPU.

The sync HTTP adapter executes in a dedicated Worker; it cannot block the page's
UI thread. See [Pyodide HTTP APIs](https://pyodide.org/en/stable/usage/api/python-api/http.html),
[Worker XHR](https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequest_API/Synchronous_and_Asynchronous_Requests),
and [WebGPU compute](https://gpuweb.github.io/gpuweb/).

After a first visit the app shell, the Python runtime and the fonts are kept by a
service worker against a manifest of SHA-256 hashes, so the page opens and runs
browser tools offline; data files used once stay in the runtime's verified cache.
A new version is offered, never forced; `?nosw` removes the service worker and its
caches. Model calls and database queries still need the network.

Deploy with the existing `studio` workflow. A changed workbook also triggers that
workflow. Each generated static file must remain below Cloudflare's 25 MiB asset
limit. Run the edge, runner and web unit suites and the browser-access end-to-end
test before merging. On the deployed site, verify `/api/sources/health`, then run
one consented small query, one formula search and one compatible sequence tool.
