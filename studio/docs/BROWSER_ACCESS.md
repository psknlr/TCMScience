# Browser data access and local computation

TCMScience provides browser-based access to its registered scientific capabilities.
Lightweight tools execute locally through Pyodide, WebAssembly, and selected WebGPU
kernels. Resource-intensive pipelines require a configured local Runner or an
authorized remote compute environment; no remote compute environment is deployed
today ([REMOTE_COMPUTE.md](REMOTE_COMPUTE.md): designed, not deployed).

When a connector call runs in the browser, every registered public-source operation
is routed through the same-origin `/api/sources/request` gateway. (Under the default
compute setting `auto`, a connected runner takes the call first and sends it from the
user's machine; the browser runs it when no runner is connected, or under `browser`.)
The manifest is generated from
`bioagent.providers.public_apis.SOURCES` at build time, so the browser and edge use
the same reviewed paths, methods, query fields and body templates. The gateway is
not an arbitrary URL proxy. It validates origins, templates, hosts, redirects,
credentials, request sizes and response sizes, and applies visitor and source
rate limits. `/api/sources/health` reports the deployed manifest counts and limits.

What has been verified, per operation, is reported by level in
[compat/connectors.md](compat/connectors.md) (generated; its `--check` is a step of CI's
`validation` job, which passed in `ci` #317 on 2026-10-19): every
operation's request reaches a mock transport as the live verification sends it and
is refused offline (both checked offline by that check), and every operation
answered from its real upstream in a maintainer's run through the runner's HTTP
backend (dated, in `BioScience-Harness/data/connector_live_verification.csv`). None
has been verified from a browser through the deployed gateway to its real upstream:
the post-deploy check reads `/api/sources/health` only, and the end-to-end test sends
one operation through the gateway code with a mocked upstream.

Project web access is on by default in every compute mode (browser, runner and auto), and the runner's own
network switch is on by default too. The approval flow still applies: each network tool asks before its first
call, naming its hosts, and sending data to a third-party service asks separately. A project can turn web access
off, and `tcmstudio serve --no-network` turns the runner's off.
Python policy checks, request rendering, parsers and error statuses run inside the
Pyodide Worker; only HTTP transport crosses the edge. Approved database URLs,
query bodies and supported request headers therefore pass through science.impf.ai
and the chosen source. Neither the edge gateway nor its invocation logs retain
these payloads. The browser keeps local project state. A source can still be
unavailable due to its own login, API terms, outage or unsupported response size.

Some registered upstream sources offer HTTP only. Those exact public endpoints
remain accessible, but credential headers are stripped from their unencrypted
transport. HTTPS-to-HTTP redirects are refused. Credentials never cross hosts.
Bulk downloads and large model assets are outside this bounded query gateway. The
research files the browser's own research jobs read (NPASS, CMAUP, LOTUS, STRING,
Reactome, UniProt: 17 files, 418 MB) have a separate route: a direct download when the
host allows a web page to make one, else the dataset relay
(`/api/datasets/files/<group>/<name>`), which serves only the exact URLs of the build's
`runtime/datasets.json`, streams the body without storing it, passes `Range`/`If-Range`
through so a download resumes, and is rate-limited like this gateway; or an import of
files obtained elsewhere ([BROWSER_JOBS.md](BROWSER_JOBS.md) §2).

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
`native.gc_content` in WebAssembly SIMD, or with WebGPU where the scheduler expects
the GPU to be faster, on any adapter not identified as a software renderer (a physical
one, or an unidentified one, which is never counted as a GPU). WebGPU has run only on a
software adapter in tests; its speed on a hardware GPU is unmeasured. The original
Python functions still compute the scientific formulas, rounding, validation and
policy processing, and check every kernel result before using it; the output is byte-identical to Python's. Receipts
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
alone. WebNN capability detection is available (Settings, `system.capabilities`).
Actual NPU acceleration is supported only for workloads with a verified execution
provider and hardware-backed execution evidence; no workload has either, so none is
used or claimed, and a context the browser creates for an "npu" request is not
evidence of an NPU.

The full data-driven network pharmacology analysis is implemented as browser jobs:
`research.snapshot` builds verified snapshots from the 17 raw files kept in the site's
private storage, and `research.run` runs the sixteen-stage workflow on them, both with
the runner's own Python in job workers ([BROWSER_JOBS.md](BROWSER_JOBS.md),
[docs/analysis-pipelines.md](../../docs/analysis-pipelines.md)). It is checked on a known
answer (the planted world) and on the parser tests' fixture files; the build and run on
the real 17 files in a browser, compared export by export with the native run, passed in `live-data` #18 and #19 (runs 39251758718 and 39258060948, commits 43918ac and 53bdbb0, 2026-10-19, headless Chromium on GitHub's runner, Pyodide 314.0.7 with its pyarrow 22.0.0): the 17 raw files imported into the site's private storage, `research.snapshot` and `research.run` succeeded as browser jobs, and every export was the native run's bytes on the same files (the five tables, `provenance.json`, `limitations.md`, `report.html`; `run_manifest.json` apart from its execution block). That run: 4 herbs, 1,792 constituents, 370 measured human targets, a 237-node, 624-edge STRING network, 959 Reactome pathways tested, none significant after BH (so no permutation null was drawn there; the planted world exercises it). The pinned seed skill
remains a limited seed-corpus network hypothesis workflow.

RNA-seq differential expression from a count matrix (`pipeline.rnaseq` with `counts` and a sample table) and
single-cell analysis of count tables (`pipeline.scrna`) are implemented as browser jobs too, with Pyodide's numpy and
scipy ([BROWSER_JOBS.md §3.1](BROWSER_JOBS.md#31-rna-seq-and-single-cell-from-count-tables)). Their inputs are files
of the person's own project: copied from the page's storage into the job's private storage and checked against their
recorded SHA-256, they are computed on in the page and never sent through the gateway or the relay (what the model
reads of a job, its status and summary, reaches the model like any tool result). A FASTQ
run, PyDESeq2, Scanpy, scVI, 10x HDF5 and `.h5ad` inputs, and inputs above 6,000 cells or 100,000 genes × 200 samples
(a browser worker's wasm32 memory holds about 4 GB) are refused in the browser with the reason and need the runner.
On two known-answer references, compared with the native command on the same files, every output was identical,
within a stated tolerance, or (the UMAP layout and figures) not compared, and the known answers held: tested locally
in headless Chromium with Pyodide 314.0.7, and in Studio's CI with Pyodide's numpy and scipy from jsDelivr (`studio`
#91, 2026-10-19).

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
