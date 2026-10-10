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
duplicate names and source versions. Its compressed SQLite asset loads only for
formula/name search and is verified against the build manifest's SHA-256 before
use. `tcmdb.formulas` supports pagination and source filters; `tcmdb.materia` exposes
the complete packaged materia collection. The original workbook remains available
as a separate static asset. Its `LicenseRef-user-supplied-unstated` provenance is
retained. These are source records, not newly validated clinical recommendations.
The curated clinical/safety seed retains its original evidence annotations.

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
