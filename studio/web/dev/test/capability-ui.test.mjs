// The capability-state UI (DESIGN §4.5): the catalog's eight state fields and its filters, Settings → Data, the job
// card's stages and assurance words, the tool card's result kind, the Provenance tab's receipt/1 rows, the hardware
// evidence list, the per-browser run index and the project's approval keys. Fixtures are written here, from the
// shapes core/catalog.js, CONTRACTS §3 (receipt/1) and §4 (/api/datasets, Job) give; nothing here is a live run.
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";

import { Catalog, requirement } from "../../js/core/catalog.js";
import { setLang, t } from "../../js/core/i18n.js";
import { hostsDigest, ToolRouter } from "../../js/core/router.js";
import { App } from "../../js/ui/app.js";
import { provenanceSections } from "../../js/ui/inspector.js";
import { evidenceRows, webgpuLine } from "../../js/ui/panels.js";
import { readJobReceipt, JobTracker } from "../../js/ui/jobs.js";
import { entryFields, matchesVerify, mountCatalog, pageReadiness, requirementView, stateRow } from "../../js/ui/pages/catalog.js";
import { dataTab, datasetFacts, downstreamOf } from "../../js/ui/pages/data.js";
import { approvalLabel } from "../../js/ui/pages/project.js";
import { clearRunlog, countsAsRun, emptyEvidence, gpuMeasured, noteEvidence, readRunlog, recordJob, recordRun, RUNLOG_KEY } from "../../js/ui/runlog.js";
import { registerUiStrings } from "../../js/ui/strings.js";
import { isCollecting, jobCard, jobProgressView, resultView, toolCallCard } from "../../js/ui/toolcards.js";

registerUiStrings();
const proto = window.HTMLElement.prototype;
if (!proto.showModal) proto.showModal = function showModal() { this.setAttribute("open", ""); };
if (!proto.close) proto.close = function close() { this.removeAttribute("open"); };
const flush = () => new Promise((r) => setTimeout(r, 0));
const text = (el) => el.textContent.replace(/\s+/g, " ");
/** Choose an option of a <select> and say so (the test DOM's select has no value setter). */
function choose(sel, v) {
  // attributes only: the test DOM's option.selected = false also clears the option that is selected
  for (const o of sel.querySelectorAll("option")) o.removeAttribute("selected");
  sel.querySelector(`option[value="${v}"]`)?.setAttribute("selected", "");
  sel.dispatchEvent(new window.Event("change"));
}

// ------------------------------------------------------------------------------------------------- fixtures

const ENTRIES = [
  {
    id: "native.gc_content", kind: "native", title: { zh: "GC 含量", en: "GC content" }, summary: "GC fraction.", category: "seq_genomics",
    exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [],
    execution: { browser_compute: { backends: ["webgpu", "wasm-simd", "js"], kernel: "sequence.counts", precision: "uint32 exact counts" } },
    implementation: { ref: "bioagent.tools.sequence:gc_content", version: "0.2.7" },
    verification: { status: "PASS", levels: { reference_output: { status: "PASS" }, kernel_parity: { status: "PASS" } } },
    state: { implementation: "tested" }, approvals: [],
  },
  {
    id: "connector.uniprot.search", kind: "connector", title: { zh: "UniProt REST · search", en: "UniProt REST · search" }, summary: "Search UniProtKB.",
    category: "live_sources", exec: ["browser", "runner"], network: true, confirm: false, job: false, gpu: false, heavy: [], tags: [],
    hosts: ["rest.uniprot.org"],
    connector: { key: "uniprot", name: "UniProt REST", operation: "search", host: "rest.uniprot.org", license: "CC-BY-4.0", docs: "https://www.uniprot.org/help/api" },
    implementation: { ref: "bioagent.providers.public_apis:uniprot.search", version: "0.2.7" },
    verification: { status: "PASS", levels: { live_upstream: { status: "PASS", at: "2026-09-17T07:31:16Z" } } },
    state: { implementation: "tested" }, approvals: [{ reason: "network" }],
  },
  {
    id: "connector.nolicence.get", kind: "connector", title: { zh: "无许可源", en: "Unlicensed source" }, summary: "A source that states no licence.",
    category: "live_sources", exec: ["runner"], network: true, confirm: false, job: false, gpu: false, heavy: [], tags: [], hosts: ["example.org"],
    connector: { key: "nolicence", name: "No licence", host: "example.org", license: "" },
    verification: { status: "PASS", levels: { gateway_mock: { status: "PASS" } } }, state: { implementation: "implemented" }, approvals: [{ reason: "network" }],
  },
  {
    id: "tcmdb.query", kind: "tcmdb", title: { zh: "数据表查询", en: "Query a table" }, summary: "Rows of one source table.", category: "tcm_data",
    exec: ["browser", "runner"], network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [], data_from: { param: "dataset" },
    parameters: { type: "object", properties: { dataset: { type: "string", enum: ["npass", "herb2"] } } },
    verification: { status: "UNVERIFIED", levels: {} }, state: { implementation: "implemented" }, approvals: [],
  },
  {
    id: "job.tcmdb.fetch", kind: "job", title: { zh: "下载数据集", en: "Fetch a dataset" }, summary: "Download a dataset's files.", category: "tcm_data",
    exec: ["runner"], network: true, confirm: true, job: true, gpu: false, heavy: [], tags: [],
    hosts_by: { dataset: { npass: ["bidd.group"], herb2: ["47.92.70.12"] } },
    parameters: { type: "object", properties: { dataset: { type: "string", enum: ["npass", "herb2"] } } },
    verification: { status: "UNVERIFIED", levels: {} }, state: { implementation: "implemented" }, approvals: [{ reason: "job" }, { reason: "network" }],
  },
  {
    id: "skill.dock-ligands", kind: "skill", title: { zh: "分子对接", en: "Dock ligands" }, summary: "Dock with AutoDock Vina.", category: "structure_molecules",
    exec: ["runner"], network: false, confirm: true, job: true, gpu: false, heavy: ["vina"], tags: [], network_if: "allow_remote",
    hosts: ["files.rcsb.org"], verification: { status: "UNVERIFIED", levels: {} }, state: { implementation: "implemented" },
    approvals: [{ reason: "job" }, { reason: "remote_upload" }],
  },
];
const DOC = {
  schema: "tcmstudio.catalog/1", core: [], entries: ENTRIES, categories: [],
  limits: { max_request_bytes: { runner: 8388608, browser: 33554432 } },
  verification_records: { live_upstream: "BioScience-Harness/data/connector_live_verification.csv", kernel_parity: "studio/web/test/fixtures/kernel_cases.json" },
};

function catalogApp({ route = { name: "catalog", entryId: null, params: {} } } = {}) {
  const app = new App();
  app.catalog = new Catalog(DOC);
  app.state.route = route;
  app.state.layout = "mobile"; // no focus-on-mount frame
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: {}, settings: app.state.settings });
  return app;
}

// ------------------------------------------------------------------------------------------------- catalog

test("catalog: each entry's eight state fields, in their fixed order, as words (no single mixed 'runnable' chip)", () => {
  setLang("zh");
  const app = catalogApp({ route: { name: "catalog", entryId: "skill.dock-ligands", params: {} } });
  const main = document.createElement("main");
  const view = mountCatalog(app, main);
  const row = main.querySelector('[data-entry="native.gc_content"]');
  const fields = [...row.querySelectorAll("[data-field]")].map((c) => c.getAttribute("data-field"));
  // the compact row: the first four always, in order (registered, ready per runtime, verified, executed)
  assert.deepEqual(fields.slice(0, 5), ["registered", "ready", "ready", "verified", "executed"]);
  assert.match(text(row), /已注册 · 有测试记录/);
  assert.match(text(row), /可执行 · 浏览器/);
  assert.match(text(row), /测试已验证 · 参考输出/);
  assert.match(text(row), /本浏览器未见成功执行/);
  assert.ok(!/现在可运行/.test(text(main)), "no single mixed runnable chip");
  // jade only for ready
  for (const c of main.querySelectorAll(".chip--jade")) assert.match(c.textContent, /可执行/);
  // the detail panel: all eight fields, labelled
  const detail = main.querySelector(".entry__state");
  for (const label of ["已注册", "可执行", "测试已验证", "已成功执行", "需要数据", "需要本机 Runner", "需要远程计算", "需要批准"]) assert.match(text(detail), new RegExp(label));
  // nothing requires remote compute: allow_remote makes it optional
  assert.match(text(detail), /远程计算：可选（allow_remote）/);
  assert.match(text(detail), /需要批准：启动任务/);
  assert.ok(!/需要批准：[^·]*发送数据到第三方/.test(text(detail)), "remote_upload is optional, not a standing approval");
  view.destroy();
});

test("catalog: a connector with web access off is not ready, and says why with its remedy; tcmdb.query needs a dataset", () => {
  setLang("zh");
  const app = catalogApp({ route: { name: "catalog", entryId: "connector.uniprot.search", params: {} } });
  app.state.settings = { ...app.state.settings, web: false };
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: { browser: { status: "idle" } }, settings: app.state.settings });
  const main = document.createElement("main");
  mountCatalog(app, main);
  const row = main.querySelector('[data-entry="connector.uniprot.search"]');
  assert.match(text(row), /浏览器：联网未开启/);
  assert.match(text(row), /测试已验证 · 实时上游 2026-09-17/);
  const detail = main.querySelector(".catalog__detail");
  assert.match(text(detail), /就绪情况/);
  assert.match(text(detail), /需要开启联网/);
  assert.match(text(detail), /为本项目开启联网/);
  // the source: host, licence, docs, and the committed record its live level rests on
  assert.match(text(detail), /rest\.uniprot\.org/);
  assert.match(text(detail), /CC-BY-4\.0/);
  assert.match(text(detail), /connector_live_verification\.csv/);
  // the router's choice is not "would run" when that runtime is not ready here (web access off)
  const placement = detail.querySelector(".entry__placement");
  assert.equal(placement.getAttribute("data-would-run"), "false");
  assert.match(text(placement), /现在调用不会运行：「浏览器」联网未开启/);
  assert.ok(!/会在「浏览器」运行/.test(text(detail)));
  // this browser's store was not read: a dataset is not known to be missing, and nothing is called ready
  const q = main.querySelector('[data-entry="tcmdb.query"]');
  assert.match(text(q), /浏览器：数据未检查/);
  assert.ok(!/浏览器：需要数据集/.test(text(q)));
  assert.ok(!q.querySelector('.chip--jade[data-where="browser"]'));
  assert.match(text(q), /需要数据：参数 dataset 指定的数据集/);
  // a mocked gateway is never live, and a missing licence is said as such
  const f = entryFields(app.catalog.byId["connector.nolicence.get"], {});
  assert.equal(f.verified.best.level, "gateway_mock");
  const verifiedChip = stateRow(f).find((c) => c.getAttribute("data-field") === "verified");
  assert.match(text(verifiedChip), /模拟网关（非实时）/);
  assert.ok(!/实时上游/.test(text(verifiedChip)), "a mocked gateway is never called live");
});

test("catalog: the compute preference and this project's own store decide readiness here, as the router does", () => {
  setLang("zh");
  const withCompute = (compute, extra = {}) => {
    const app = catalogApp({ route: { name: "catalog", entryId: "native.gc_content", params: {} } });
    app.state.settings = { ...app.state.settings, compute };
    Object.assign(app.state, extra);
    app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: { browser: { status: "idle" } }, settings: app.effectiveSettings() });
    const main = document.createElement("main");
    mountCatalog(app, main);
    return main;
  };
  // remote: no backend serves it, so nothing is ready anywhere, and the router's own words say why
  const remote = withCompute("remote");
  assert.equal(remote.querySelectorAll(".chip--jade").length, 0, "nothing is ready under remote");
  assert.match(text(remote.querySelector('[data-entry="native.gc_content"]')), /浏览器：远程计算未配置/);
  assert.equal(remote.querySelector(".entry__placement").getAttribute("data-would-run"), "false");
  // browser: the runner is left out (a connected runner, else needing it comes first); runner: the browser is
  const browser = withCompute("browser", { runner: { status: "ready" } });
  assert.match(text(browser.querySelector('[data-entry="native.gc_content"]')), /本机 Runner：计算设置未选它/);
  assert.ok(browser.querySelector('[data-entry="native.gc_content"] .chip--jade[data-where="browser"]'));
  const runner = withCompute("runner");
  assert.match(text(runner.querySelector('[data-entry="native.gc_content"]')), /浏览器：计算设置未选它/);
  assert.ok(!runner.querySelector('.chip--jade[data-where="browser"]'));
  // the project's store, once read: a store without the dataset needs it; another project's reading is not this one's
  const read = withCompute("auto", { project: { id: "p1", name: "P1" }, browserData: { built: [], projectId: "p1" } });
  assert.match(text(read.querySelector('[data-entry="tcmdb.query"]')), /浏览器：需要数据集/);
  const built = withCompute("auto", { project: { id: "p1", name: "P1" }, browserData: { built: ["npass"], projectId: "p1" } });
  assert.ok(built.querySelector('[data-entry="tcmdb.query"] .chip--jade[data-where="browser"]'));
  const other = withCompute("auto", { project: { id: "p2", name: "P2" }, browserData: { built: ["npass"], projectId: "p1" } });
  assert.match(text(other.querySelector('[data-entry="tcmdb.query"]')), /浏览器：数据未检查/);
  // the pure helper: a store not read keeps the dataset names as unchecked, and leaves the rest of the state alone
  const st = pageReadiness({ implementation: "implemented", readiness: { browser: { state: "needs_dataset", requires: [requirement("dataset", { names: ["npass"] })] }, runner: { state: "ready", requires: [] } } }, { compute: "browser", dataKnown: false });
  assert.deepEqual(st.readiness.browser.unchecked, ["npass"]);
  assert.equal(st.readiness.browser.state, "ready");
  assert.equal(st.readiness.runner.state, "unsupported");
  assert.equal(st.readiness.runner.requires[0].need, "compute_setting");
});

test("catalog: a requirement's own remedy and detail are kept (never a generic sentence that does not apply); links only for http(s)", () => {
  setLang("zh");
  const li = (r) => { const el = requirementView(r); return { el, s: text(el) }; };
  // the runner's remedy for a dataset that cannot be downloaded: shown as written, marked English
  const manual = li({ need: "dataset", names: ["hkmanual"], detail: "the hkmanual dataset's files placed by hand on this runner", remedy: "Place its files by hand (tcmdb.datasets gives the instructions), then build it: job.tcmdb.build {dataset: hkmanual}." });
  assert.match(manual.s, /Place its files by hand/);
  assert.ok(!/在 Runner 上下载并构建/.test(manual.s), "never the download advice for a manual dataset");
  assert.equal(manual.el.querySelector(".need__remedy").getAttribute("lang"), "en");
  assert.match(manual.s, /placed by hand on this runner/, "a detail that says more than the default is shown");
  // the default requirement of a need: its Chinese words, and no repeated detail
  const net = li(requirement("network"));
  assert.match(net.s, /为本项目开启联网/);
  assert.equal(net.el.querySelector(".need__detail"), null);
  // a size the router wrote into the detail is kept, in both languages
  const big = { ...requirement("memory"), detail: "a request of 12.0 MB is larger than the browser runtime takes (8.0 MB)" };
  assert.match(li(big).s, /12\.0 MB/);
  setLang("en");
  assert.match(li(big).s, /12\.0 MB/);
  setLang("zh");
  // the verification filter's catch-all is "no passing test": a failed test is listed under it, and labelled so
  assert.equal(matchesVerify({ verification: { status: "FAIL", levels: { reference_output: { status: "FAIL" } } } }, "unverified"), true);
  const main = document.createElement("main");
  mountCatalog(catalogApp(), main);
  assert.ok([...main.querySelectorAll('select[data-filter="ver"] option')].some((o) => /^没有通过的测试/.test(o.textContent)));
  // a connector's docs address from a (runner-merged) catalog: a link only when it is http(s)
  const bad = { ...DOC, entries: DOC.entries.map((e) => (e.id === "connector.uniprot.search" ? { ...e, connector: { ...e.connector, docs: "javascript:alert(1)" } } : e)) };
  const app = catalogApp({ route: { name: "catalog", entryId: "connector.uniprot.search", params: {} } });
  app.catalog = new Catalog(bad);
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: {}, settings: app.state.settings });
  const m2 = document.createElement("main");
  mountCatalog(app, m2);
  const detail = m2.querySelector(".catalog__detail");
  assert.match(text(detail), /javascript:alert\(1\)/, "shown as text");
  assert.ok(![...detail.querySelectorAll("a")].some((a) => /^javascript:/i.test(a.getAttribute("href") || "")), "never a javascript: link");
});

test("catalog: the filters (state chips, runtime, dataset, backend) narrow the list, and live in the address", () => {
  setLang("zh");
  const app = catalogApp();
  const main = document.createElement("main");
  mountCatalog(app, main);
  const count = () => main.querySelectorAll(".entry-row").length;
  assert.equal(count(), ENTRIES.length);
  const chipBtn = main.querySelector('[data-flag="needs_runner"]');
  assert.equal(chipBtn.getAttribute("aria-pressed"), "false");
  assert.match(chipBtn.textContent, /需要 Runner3/, "its count comes from the catalog");
  chipBtn.dispatchEvent(new window.Event("click"));
  const pressed = main.querySelector('[data-flag="needs_runner"]');
  assert.equal(pressed.getAttribute("aria-pressed"), "true");
  assert.deepEqual([...main.querySelectorAll(".entry-row")].map((a) => a.getAttribute("data-entry")).sort(), ["connector.nolicence.get", "job.tcmdb.fetch", "skill.dock-ligands"]);
  assert.equal(app.state.route.params.f, "needs_runner");
  assert.match(main.querySelector(".entry-row").getAttribute("href"), /\?f=needs_runner$/, "an entry's link keeps the filters");
  pressed.dispatchEvent(new window.Event("click"));
  // the local backend select: webgpu lists the GPU tool only
  const be = main.querySelector('select[data-filter="be"]');
  choose(be, "webgpu");
  assert.deepEqual([...main.querySelectorAll(".entry-row")].map((a) => a.getAttribute("data-entry")), ["native.gc_content"]);
  assert.equal(app.state.route.params.be, "webgpu");
  const beNow = main.querySelector('select[data-filter="be"]');
  choose(beNow, "");
  // the dataset select: npass, among the values of the store reader's dataset argument
  const ds = main.querySelector('select[data-filter="ds"]');
  assert.ok([...ds.querySelectorAll("option")].some((o) => o.value === "npass"));
  assert.ok([...ds.querySelectorAll("option")].some((o) => o.value === "uniprot"), "connector keys are datasets too");
  choose(ds, "npass");
  // the entries that read it (the fetch job acquires it, and is not one of them)
  assert.deepEqual([...main.querySelectorAll(".entry-row")].map((a) => a.getAttribute("data-entry")), ["tcmdb.query"]);
  // the runtime select: runner only
  const ds2 = main.querySelector('select[data-filter="ds"]');
  choose(ds2, "");
  const rt = main.querySelector('select[data-filter="rt"]');
  choose(rt, "gateway");
  assert.deepEqual([...main.querySelectorAll(".entry-row")].map((a) => a.getAttribute("data-entry")), ["connector.uniprot.search"]);
  // a page opened at that address starts with the same view
  const again = catalogApp({ route: { name: "catalog", entryId: null, params: { f: "needs_runner", be: "" } } });
  const main2 = document.createElement("main");
  mountCatalog(again, main2);
  assert.equal(main2.querySelectorAll(".entry-row").length, 3);
});

test("catalog: executed successfully counts only real runs in this browser", () => {
  setLang("zh");
  clearRunlog();
  const ok = { status: "succeeded", via: "native.gc_content", receipt: { schema: "tcmstudio.receipt/1", where: "browser", compute: { backend: "wasm-simd" }, result: { kind: "computation", outcome: "succeeded", assurance: "none" } } };
  assert.deepEqual(recordRun(ok), ["native.gc_content"]);
  recordRun(ok);
  const app = catalogApp();
  const main = document.createElement("main");
  mountCatalog(app, main);
  assert.match(text(main.querySelector('[data-entry="native.gc_content"]')), /已成功执行 2 次（本浏览器所见）/);
  // the detail says where, with which backend, and when the last one ran
  const opened = catalogApp({ route: { name: "catalog", entryId: "native.gc_content", params: {} } });
  const main3 = document.createElement("main");
  mountCatalog(opened, main3);
  assert.match(text(main3.querySelector(".state-fields__last")), /最近一次：浏览器 · WebAssembly SIMD，/);
  setLang("en");
  const main2 = document.createElement("main");
  mountCatalog(catalogApp(), main2);
  assert.match(text(main2.querySelector('[data-entry="native.gc_content"]')), /Executed successfully 2 times \(seen from this browser\)/);
  setLang("zh");
  clearRunlog();
});

// ------------------------------------------------------------------------------------------------- run index

test("run index: a job id, a page-made envelope, a refusal, a pending job never count; a collected job counts once", () => {
  clearRunlog();
  const base = { via: "job.pipeline.dock", receipt: { schema: "tcmstudio.receipt/1", where: "runner" } };
  assert.equal(countsAsRun({ ...base, status: "job_submitted", job: { id: "j1", kind: "pipeline.dock", state: "queued" }, receipt: { ...base.receipt, result: { outcome: "queued", kind: null, assurance: "none" } } }), false);
  assert.equal(countsAsRun({ ...base, status: "needs_approval", receipt: { ...base.receipt, where: "page", decided_by: "router", result: { outcome: "not_executed" } } }), false);
  assert.equal(countsAsRun({ ...base, status: "refused", approval: { decision: "deny" } }), false);
  assert.equal(countsAsRun({ status: "succeeded", via: "system.job_status", job: { id: "j1", kind: "pipeline.dock", state: "running" }, receipt: { schema: "tcmstudio.receipt/1", where: "runner", result: { outcome: "running", assurance: "none" } } }), false);
  assert.equal(countsAsRun({ status: "succeeded", via: "system.catalog_search", receipt: { where: "browser", local: true, result: { outcome: "succeeded" } } }), false, "the page's own answers are not runs");
  const done = { status: "succeeded", via: "system.job_status", job: { id: "j1", kind: "pipeline.dock", state: "succeeded" }, receipt: { schema: "tcmstudio.receipt/1", where: "runner", result: { kind: "prediction", outcome: "validated", assurance: "outputs_verified" } } };
  assert.deepEqual(recordRun(done), ["system.job_status", "job.pipeline.dock"]);
  assert.deepEqual(recordRun(done), ["system.job_status"], "the same job is not counted twice");
  assert.deepEqual(recordJob({ id: "j1", kind: "pipeline.dock", state: "succeeded" }), []);
  assert.equal(readRunlog().entries["job.pipeline.dock"].n, 1);
  // a record of another version is set aside, never read as this one
  localStorage.setItem(RUNLOG_KEY, JSON.stringify({ v: 0, entries: { "native.gc_content": { n: 9 } } }));
  assert.deepEqual(readRunlog().entries, {});
  clearRunlog();
});

// ------------------------------------------------------------------------------------------------- data tab

const DATASETS = {
  count: 2, network: { enabled: true },
  datasets: [
    { key: "npass", name: "NPASS", access: "download", version: "2.0", license: "", licence_class: "unknown", commercial_use: "unknown",
      redistribution: "unknown — downloading is not permission to redistribute", hosts: ["bidd.group"], files_default: 2, bytes_default: 52428800,
      acquirable: true, readiness: { state: "supported", next_step: "job.tcmdb.fetch {dataset: npass}, then job.tcmdb.build" } },
    { key: "herb2", name: "HERB 2.0", access: "download", version: "", license: "CC BY 4.0", licence_class: "open", commercial_use: "allowed",
      redistribution: "unknown — downloading is not permission to redistribute", hosts: ["47.92.70.12"], files_default: 1, bytes_default: null,
      acquirable: true, readiness: { state: "built", next_step: "query it" } },
  ],
};

function dataApp({ runnerReady = true } = {}) {
  const app = catalogApp();
  const calls = { browser: 0, acquire: [], manifest: 0 };
  app.runtimes = {
    browser: { status: "idle", call: async () => { calls.browser += 1; return { status: "succeeded", result: { datasets: [] } }; } },
    runner: {
      url: "http://127.0.0.1:8765",
      datasets: {
        list: async () => DATASETS, acquisitions: async () => ({ acquisitions: [] }),
        manifest: async (key) => { calls.manifest += 1; return { dataset: key, files: [
          { name: "a.tsv", host: "bidd.group", optional: false, expected_bytes: 100, reviewed_checksum: "ab", local: { present: true, verification: "checksum" } },
          { name: "b.tsv", host: "bidd.group", optional: false, expected_bytes: null, reviewed_checksum: null, local: { present: false } },
        ] }; },
        acquire: async (body) => { calls.acquire.push(body); return { acquisition: { id: "j_acq", dataset: body.dataset, state: "queued", job: { id: "j_acq", kind: "tcmdb.fetch", state: "queued", params: { dataset: body.dataset } } } }; },
      },
      jobs: { get: async (id) => ({ id, kind: "tcmdb.fetch", state: "running", progress: { stage: "transferring", fraction: 0.25, bytes: { done: 1048576, total: 4194304 }, files: { done: 1, total: 2 }, verified: { checksum: 1, size: 0, none: 0 }, message: "a.tsv: part 1/4 MB" } }), events: () => () => {}, files: async () => ({ files: [] }) },
    },
  };
  app.state.runner = { ...app.state.runner, status: runnerReady ? "ready" : "idle" };
  return { app, calls };
}

test("Settings → Data: mounting reads the runner's list only, never the browser runtime; licences and redistribution said as found", async () => {
  setLang("zh");
  const { app, calls } = dataApp();
  const host = document.createElement("div");
  const render = () => { host.replaceChildren(dataTab(app, render)); };
  render();
  await flush(); await flush();
  assert.equal(calls.browser, 0, "no Pyodide on mount");
  const npass = host.querySelector('[data-dataset="npass"]');
  assert.ok(npass, "the runner's dataset is listed");
  assert.match(text(npass), /未声明许可/);
  assert.match(text(npass), /未知——下载不等于获准再分发/);
  assert.match(text(npass), /2\.0/);
  assert.match(text(npass), /50(\.0)? MB/);
  assert.match(text(npass), /可下载/);
  assert.match(text(npass), /读取它的工具tcmdb\.query/, "downstream tools from the catalog");
  const herb = host.querySelector('[data-dataset="herb2"]');
  assert.match(text(herb), /未注明版本/);
  assert.match(text(herb), /大小未知/);
  assert.ok(herb.querySelector(".chip--jade"), "built is the one ready state");
  assert.ok(!npass.querySelector(".chip--jade"));
  // the browser's hub is read on a press, and only then
  const press = host.querySelector('[data-key="data-browser-check"]');
  assert.match(press.textContent, /加载 Python 运行环境/);
  press.dispatchEvent(new window.Event("click"));
  await flush(); await flush();
  assert.equal(calls.browser, 2, "tcmdb.datasets and tcmdb.status, after the press");
});

test("Settings → Data: a dataset's files and verification are read when its row is opened", async () => {
  setLang("zh");
  const { app, calls } = dataApp();
  const host = document.createElement("div");
  const render = () => { host.replaceChildren(dataTab(app, render)); };
  render();
  await flush(); await flush();
  assert.equal(calls.manifest, 0);
  host.querySelector('[data-dataset="npass"] .data-files .disclosure__summary').dispatchEvent(new window.Event("click"));
  await flush(); await flush();
  assert.equal(calls.manifest, 1);
  const npass = host.querySelector('[data-dataset="npass"]');
  assert.match(text(npass), /已有 1\/2 个必需文件/);
  assert.match(text(npass), /b\.tsv/);
  assert.match(text(npass), /校验和 1 · 大小 0 · 不符 0 · 未核验 0/);
  assert.match(text(npass), /审核过校验和的文件 1\/2 个/);
  // the facts helper on the browser's answer: status counts, reviewed checksums
  const f = datasetFacts({ key: "x", license: "", files: [{ checksum: "a" }, { checksum: null }], readiness: { state: "partial" } }, { status: { files_present: 1, files_default: 2, missing_default: ["b"], verification: { a: "size" } } });
  assert.deepEqual(f.files, { present: 1, expected: 2, missing: ["b"] });
  assert.equal(f.verification.size, 1);
  assert.deepEqual(f.reviewed, { n: 1, of: 2 });
  assert.equal(f.redistribution, "unknown — downloading is not permission to redistribute");
  assert.deepEqual(downstreamOf(app.catalog, "npass"), { named: ["tcmdb.query"], any: 0 });
});

test("Settings → Data: a download is asked for only after a confirmation that names the hosts and the size; its progress follows", async () => {
  setLang("zh");
  const { app, calls } = dataApp();
  const host = document.createElement("div");
  document.body.append(host); // the job tracker re-fills only cards that are on the page
  const asked = [];
  let answer = false;
  const confirm = async (opts) => { asked.push(opts); return answer; };
  const render = () => { host.replaceChildren(dataTab(app, render, { confirm })); };
  render();
  await flush(); await flush();
  host.querySelector('[data-acquire="npass"]').dispatchEvent(new window.Event("click"));
  await flush();
  assert.equal(asked.length, 1);
  assert.match(text(asked[0].body), /bidd\.group/);
  assert.match(text(asked[0].body), /50(\.0)? MB/);
  assert.match(text(asked[0].body), /下载不等于获准再分发/);
  assert.equal(calls.acquire.length, 0, "declined: nothing is asked of the runner");
  answer = true;
  host.querySelector('[data-acquire="npass"]').dispatchEvent(new window.Event("click"));
  await flush(); await flush(); await flush();
  assert.deepEqual(calls.acquire.map((b) => b.dataset), ["npass"]);
  const npass = host.querySelector('[data-dataset="npass"]');
  // the acquisition's job, followed: its stage in words, bytes and files, how kept files were verified
  assert.match(text(npass), /当前阶段：传输文件/);
  assert.match(text(npass), /文件 1\/2 个/);
  assert.match(text(npass), /校验和核验 1 个/);
  assert.ok(!host.querySelector('[data-acquire="npass"]'), "no second download while one runs");
  host.remove();
});

test("Settings → Data: an address from the runner is a link only when it is http(s); a row's files stay closed once closed", async () => {
  setLang("zh");
  const { app, calls } = dataApp();
  const list = app.runtimes.runner.datasets.list;
  app.runtimes.runner.datasets.list = async () => {
    const doc = await list();
    return { ...doc, datasets: doc.datasets.map((d) => (d.key === "npass" ? { ...d, homepage: "javascript:alert(document.domain)", live_record: { files_pass: 2, files_recorded: 3, files: 3, run: "javascript:alert(2)", at: "2026-10-01" } } : { ...d, homepage: "https://herb.ac.cn/" })) };
  };
  // a manifest that answers only when the test lets it
  let release;
  const gate = new Promise((r) => { release = r; });
  const manifest = app.runtimes.runner.datasets.manifest;
  app.runtimes.runner.datasets.manifest = async (key) => { await gate; return manifest(key); };
  const host = document.createElement("div");
  const render = () => { host.replaceChildren(dataTab(app, render)); };
  render();
  await flush(); await flush();
  const npass = host.querySelector('[data-dataset="npass"]');
  assert.equal(npass.querySelector("a.dataset-row__name"), null, "a javascript: homepage is not a link");
  assert.match(text(npass.querySelector(".dataset-row__name")), /NPASS/);
  assert.ok(![...host.querySelectorAll("a")].some((a) => /^javascript:/i.test(a.getAttribute("href") || "")), "no javascript: link anywhere");
  assert.match(text(npass), /CI 实时下载记录：2\/3 个文件通过（2026-10-01）/, "the record is said, as text");
  assert.equal(host.querySelector('[data-dataset="herb2"] a.dataset-row__name').getAttribute("href"), "https://herb.ac.cn/");
  // opened twice while its manifest is being read: one GET
  const summary = () => host.querySelector('[data-dataset="npass"] .data-files .disclosure__summary');
  summary().dispatchEvent(new window.Event("click"));
  render();
  assert.equal(summary().getAttribute("aria-expanded"), "true", "open across a re-render while it loads");
  summary().dispatchEvent(new window.Event("click"));
  summary().dispatchEvent(new window.Event("click"));
  release();
  await flush(); await flush(); await flush();
  assert.equal(calls.manifest, 1, "a second press while loading sends no second GET");
  assert.equal(summary().getAttribute("aria-expanded"), "true");
  // closed by the person: it stays closed when the tab is rendered again
  summary().dispatchEvent(new window.Event("click"));
  render();
  assert.equal(summary().getAttribute("aria-expanded"), "false");
  assert.equal(calls.manifest, 1);
});

/**
 * The runner's own /api/datasets answer and one manifest (tcmstudio.datasets.DatasetAPI over an empty data directory,
 * with its network off), so the tab is tested on the field names the runner writes, not only on fixtures written here.
 * null when tcmstudio is not importable by $PYTHON or python3 (the runner's tests cover it there).
 */
function runnerDatasets() {
  const script = [
    "import json, tempfile, types",
    "from tcmstudio.datasets import DatasetAPI",
    "jobs = types.SimpleNamespace(state=types.SimpleNamespace(get=lambda: {'network': {'enabled': False}}))",
    "api = DatasetAPI(jobs, {'BIOAGENT_TCMDB': tempfile.mkdtemp()})",
    "print(json.dumps({'list': api.list(), 'manifest': api.manifest('npass')}, default=str))",
  ].join("\n");
  for (const py of [process.env.PYTHON, "python3"].filter(Boolean)) {
    try { return JSON.parse(execFileSync(py, ["-I", "-c", script], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60000 })); } catch { /* the next one */ }
  }
  return null;
}
const REAL = runnerDatasets();

test("Settings → Data: the runner's own /api/datasets and manifest answers read as the tab expects", { skip: REAL ? false : "tcmstudio is not importable by $PYTHON or python3" }, async () => {
  setLang("zh");
  const rows = REAL.list.datasets;
  assert.ok(rows.length > 10);
  for (const row of rows) {
    const f = datasetFacts(row);
    assert.equal(f.key, row.key);
    assert.ok(["catalogued", "live", "supported", "partial", "downloaded", "built"].includes(f.readiness), `${row.key}: ${f.readiness}`);
    assert.ok(typeof f.license === "string" && typeof f.redistribution === "string", row.key);
    if (row.local?.files_default) assert.deepEqual(f.files, { present: 0, expected: row.local.files_default, missing: row.local.missing_default }, `${row.key}: what the runner holds, from the list`);
  }
  const npass = rows.find((r) => r.key === "npass");
  const m = REAL.manifest;
  const fm = datasetFacts(npass, { manifest: m });
  assert.equal(fm.files.expected, m.files.filter((x) => !x.optional).length);
  assert.equal(fm.homepage, m.homepage);
  // the tab over those answers: the list's facts at once, the manifest's when the row is opened
  const { app } = dataApp();
  app.runtimes.runner.datasets.list = async () => REAL.list;
  app.runtimes.runner.datasets.manifest = async () => REAL.manifest;
  const host = document.createElement("div");
  const render = () => { host.replaceChildren(dataTab(app, render)); };
  render();
  await flush(); await flush();
  const row = host.querySelector('[data-dataset="npass"]');
  assert.match(text(row), new RegExp(`已有 0/${npass.local.files_default} 个必需文件`));
  if (npass.live_record) assert.match(text(row), new RegExp(`CI 实时下载记录：${npass.live_record.files_pass}/${npass.live_record.files_recorded} 个文件通过`));
  assert.ok(![...row.querySelectorAll("a")].some((a) => /github\.com\/psknlr/i.test(a.getAttribute("href") || "")), "the project's own pages are not linked");
  assert.ok(host.querySelector(".data-tab").textContent.includes(t("ui.data.runner_network_off")), "the runner said its network is off");
  row.querySelector(".data-files .disclosure__summary").dispatchEvent(new window.Event("click"));
  await flush(); await flush();
  assert.equal(host.querySelectorAll('[data-dataset="npass"] .data-files__table tbody tr').length, m.files.length);
  assert.equal(host.querySelector('[data-dataset="npass"] a.dataset-row__name')?.getAttribute("href"), m.homepage);
});

// ------------------------------------------------------------------------------------------------- job card

test("job card: a submitted or running job is never complete; its stage in words; verifying while it is collected", () => {
  setLang("zh");
  const queued = jobCard({ id: "j_q", kind: "pipeline.rnaseq", state: "queued", created_at: "2026-10-07T09:50:00Z" });
  assert.ok(!/已完成/.test(text(queued)));
  assert.match(text(queued), /排队中/);
  assert.match(text(queued), /不会作为结果/);
  const running = jobCard({ id: "j_r", kind: "pipeline.rnaseq", state: "running", device: "cuda:0", created_at: "2026-10-07T09:44:02Z", started_at: "2026-10-07T09:44:05Z",
    progress: { stage: "quantify", fraction: 0.4, message: "stage quantify" } }, { partial: [{ path: "quant/s1/abundance.tsv", bytes: 1024 }] });
  assert.match(text(running), /当前阶段：定量/);
  assert.ok(!/stage quantify/.test(text(running)), "the runner's English template is not shown");
  assert.match(text(running), /CUDA:0 · 已选定（实际使用未核实）/, "a GPU job's device is a selection, never acceleration");
  assert.ok(!/加速|accelerat/i.test(text(running)));
  // written so far: names and sizes, no button to open them
  const partial = running.querySelector(".job__partial");
  assert.ok(partial);
  assert.match(text(partial), /未核验，不是结果/);
  assert.equal(partial.querySelectorAll("button:not(.disclosure__summary), a").length, 0);
  assert.ok(!running.querySelector(".job__files"));
  const verifying = jobCard({ id: "j_v", kind: "pipeline.dock", state: "running", started_at: "2026-10-07T09:44:05Z", progress: { stage: "verifying", message: "verifying the outputs" } });
  assert.match(text(verifying), /正在核验输出/);
  assert.ok(verifying.querySelector(".job__step.is-active"));
  assert.ok(!/verifying the outputs/.test(text(verifying)));
  // a log line is shown as the last log line; an older pipeline's fixed message becomes its stage
  assert.equal(jobProgressView({ message: "Vina: exhaustiveness 8 · pose 4/10" }).log, "Vina: exhaustiveness 8 · pose 4/10");
  assert.equal(jobProgressView({ message: "writing the report" }).stage, "report");
  assert.match(jobProgressView({ message: "quantified 2 of 6 samples" }).label, /已定量 2\/6 个样本/);
  // a stage nobody named comes through as itself
  assert.equal(jobProgressView({ stage: "a_new_stage" }).label, "a_new_stage");
  setLang("en");
  assert.match(text(jobCard({ id: "j_r", kind: "pipeline.scrna", state: "running", progress: { stage: "doublets" } })), /Stage: Doublet detection/);
  setLang("zh");
});

test("job card: a download checking one file mid-transfer is still running; only the runner's collection is verifying", () => {
  setLang("zh");
  const fetching = { id: "j_f", kind: "tcmdb.fetch", state: "running", started_at: "2026-10-19T00:00:00Z",
    progress: { stage: "verifying", fraction: 0.3, files: { done: 3, total: 10 }, bytes: { done: 1000, total: 5000 }, verified: { checksum: 3, size: 0, none: 0 }, message: "herb.csv: 120 bytes" } };
  assert.equal(isCollecting(fetching), false);
  const card = jobCard(fetching);
  assert.notEqual(card.querySelector(".job__head .verdict").textContent, "核验中");
  assert.ok(!/正在核验输出/.test(text(card)));
  const steps = [...card.querySelectorAll(".job__step")];
  assert.ok(steps[1].classList.contains("is-active"), "the running step is the active one");
  assert.ok(steps[2].classList.contains("is-todo"));
  assert.match(text(card), /当前阶段：核验刚下载的文件/);
  // a download the runner is collecting (its process ended): verifying the outputs
  const collected = { ...fetching, progress: { ...fetching.progress, message: "verifying the outputs" } };
  assert.equal(isCollecting(collected), true);
  assert.match(text(jobCard(collected)), /正在核验输出/);
  // any other kind: "verifying" is only ever the collection
  assert.equal(isCollecting({ id: "x", kind: "pipeline.dock", state: "running", progress: { stage: "verifying" } }), true);
  assert.equal(isCollecting({ id: "x", kind: "pipeline.dock", state: "succeeded", progress: { stage: "verifying" } }), false);
  setLang("en");
  assert.match(text(jobCard(fetching)), /Stage: Checking a file just downloaded/);
  setLang("zh");
});

test("job receipt: used only at its recorded digest; a listed job that has ended is shown from the list, with no request", async () => {
  setLang("zh");
  const doc = { schema: "tcmstudio.job-receipt/1", duration_s: 5, resources: { peak_rss_mb: 10 } };
  const bytes = new TextEncoder().encode(JSON.stringify(doc));
  const digest = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((x) => x.toString(16).padStart(2, "0")).join("");
  const fetched = [];
  const runner = {
    fileUrl: (id, path) => `http://127.0.0.1:8765/api/jobs/${id}/files/${path}`,
    _fetch: async (url) => { fetched.push(url); return { ok: true, arrayBuffer: async () => bytes.buffer.slice(0) }; },
  };
  assert.deepEqual(await readJobReceipt(runner, "j1", "job_receipt.json", digest), doc);
  assert.deepEqual(await readJobReceipt(runner, "j1", "job_receipt.json", "0".repeat(64)), { changed: true });
  assert.equal(await readJobReceipt(runner, "j1", "job_receipt.json", null), null, "no recorded digest: nothing is used");
  assert.equal(fetched.length, 2);
  // the card says a changed record was not used, and shows nothing from it
  const card = jobCard({ id: "j1", kind: "pipeline.dock", state: "succeeded", receipt: { path: "job_receipt.json", sha256: "0".repeat(64) } }, { jobReceipt: { changed: true } });
  assert.match(text(card), /运行记录与登记的哈希不符/);
  // Settings → Compute's list: an ended job is the runner's own view already; a running one is followed
  const asked = [];
  const app = new App();
  app.state.runner = { ...app.state.runner, status: "ready" };
  app.runtimes = { runner: {
    url: "http://127.0.0.1:8765", fileUrl: runner.fileUrl, _fetch: async (url) => { asked.push(url); return { ok: false }; },
    jobs: { get: async (id) => { asked.push(`get ${id}`); return { id, state: "running" }; }, files: async (id) => { asked.push(`files ${id}`); return { files: [] }; }, events: () => () => {} },
  } };
  const tracker = new JobTracker(app);
  const ended = tracker.node({ id: "j_done", kind: "pipeline.dock", state: "succeeded", artefacts: [{ name: "run.json", sha256: "a".repeat(64), bytes: 3 }], receipt: { path: "job_receipt.json", sha256: digest } }, { fresh: true });
  tracker.node({ id: "j_failed", kind: "pipeline.dock", state: "failed", outcome: { status: "failed", error: "x" } }, { fresh: true });
  tracker.node({ id: "j_run", kind: "pipeline.dock", state: "running" }, { fresh: true });
  await flush(); await flush();
  assert.deepEqual(asked, ["get j_run"], "only the running job is asked about");
  assert.ok(ended.querySelector(".job__files"), "the listed job's files come from its view");
  // re-drawn after the runner reconnects, a listed job that had ended is not one this page saw end (no run counted)
  const events = [];
  app.on("job", (j) => events.push(j.id));
  tracker.refresh();
  await flush(); await flush();
  assert.ok(!events.includes("j_done") && !events.includes("j_failed"), `no job event for listed ended jobs: ${events}`);
  tracker.close();
});

test("job card: a succeeded job's files, records, limitations and resources; the pill says output hashes verified", () => {
  setLang("zh");
  const job = {
    id: "j_s", kind: "research.snapshot", title: { zh: "研究快照", en: "Research snapshots" }, state: "succeeded", device: "cpu",
    created_at: "2026-10-07T09:20:11Z", started_at: "2026-10-07T09:20:12Z", finished_at: "2026-10-07T09:21:40Z",
    outcome: { status: "succeeded", exit_code: 0, checks: ["controller_digests", "kind.verify"] },
    artefacts: [{ name: "run.json", sha256: "a".repeat(64), bytes: 10 }, { name: "job_receipt.json", sha256: "b".repeat(64), bytes: 20 }],
    receipt: { path: "job_receipt.json", sha256: "b".repeat(64) },
    limitations: ["A snapshot is a copy of the source at one time, not the source itself."],
    stages: [{ name: "qc", required: true, status: "succeeded", check: "PASS", device: "cpu", seconds: 1.5 }],
  };
  const card = jobCard(job, { jobReceipt: { schema: "tcmstudio.job-receipt/1", duration_s: 88, resources: { peak_rss_mb: 512.4 }, device: { assigned: "cpu", evidence: "selected" }, engine: [{ id: "salmon", version: "1.10.3", stages: ["quantify"] }] } });
  assert.match(text(card), /研究快照（下载与核验）/);
  assert.equal(card.querySelector(".job__head .verdict").textContent, "已完成（输出哈希已核验）");
  assert.ok(card.querySelector(".job__files"));
  assert.match(text(card), /512\.4 MB（任务中最大的单个进程）/);
  assert.match(text(card), /1 分 28 秒|88/);
  assert.match(text(card), /salmon 1\.10\.3/);
  assert.match(text(card), /controller_digests · kind\.verify/);
  assert.match(text(card), /job_receipt\.json/);
  assert.match(text(card), /复现记录/);
  assert.match(text(card), /A snapshot is a copy/);
  assert.match(text(card), /质量控制/);
  // a kind without words of the UI's own takes the runner's title
  assert.match(text(jobCard({ id: "x", kind: "new.kind", title: { zh: "新任务", en: "New job" }, state: "queued" })), /新任务/);
});

// ------------------------------------------------------------------------------------------------- tool card

const receipt = (result, extra = {}) => ({ schema: "tcmstudio.receipt/1", where: "browser", runtime: "pyodide", device: "cpu", result, ...extra });

test("tool card: the result-kind chip for each kind, the assurance words, and Not run for every page-made envelope", () => {
  setLang("zh");
  const card = (env) => toolCallCard({ id: "c", name: "x", args: {} }, { tool: "x", via: "x", text: "{}", summary: "", governance: { kind: "native" }, ...env });
  const words = {
    record: "检索到的记录", computation: "计算结果", prediction: "预测（非实测）", draft: "草稿", fixture: "演示数据（非真实数据）", system: "系统",
  };
  for (const [kind, label] of Object.entries(words)) {
    const el = card({ status: "succeeded", receipt: receipt({ kind, outcome: "succeeded", assurance: "none" }) });
    assert.equal(el.querySelector(".result-chip")?.getAttribute("data-result"), kind, kind);
    assert.match(text(el), new RegExp(label.replace(/[()（）]/g, ".")), kind);
  }
  const verified = card({ status: "succeeded", receipt: receipt({ kind: "prediction", outcome: "validated", assurance: "outputs_verified" }, { where: "runner", runtime: "cpython" }) });
  assert.match(text(verified), /输出哈希已核验/);
  assert.ok(!/(?<!哈希)已核验(?!输出)/.test(text(verified).replace("输出哈希已核验", "")), "never a bare 已核验");
  for (const decided_by of ["router", "browser-runtime", "runner-client", "unavailable-runtime"]) {
    const el = card({ status: "failed", error: { type: "unavailable", message: "m" }, receipt: { ...receipt({ kind: null, outcome: "not_executed", assurance: "none" }, { where: decided_by === "runner-client" ? "runner" : "browser" }), decided_by } });
    const badge = el.querySelector(".where");
    assert.ok(badge.classList.contains("where--none"), decided_by);
    assert.match(badge.textContent, /未运行/, decided_by);
    assert.ok(!/本机 Runner|浏览器/.test(badge.textContent), `${decided_by}: never under a runtime's badge`);
  }
  // a runtime that may have started it and gave no answer: no result, not "not run"
  const timeout = card({ status: "failed", error: { type: "timeout", message: "t" }, receipt: { ...receipt({ kind: null, outcome: "failed", assurance: "none" }, { where: "runner" }), decided_by: "runner-client" } });
  assert.match(timeout.querySelector(".where").textContent, /无结果/);
  // a legacy envelope (no receipt.schema): its kind derived, and said so
  const legacy = { status: "succeeded", governance: { kind: "connector", limitations: [] }, receipt: { where: "runner" } };
  const v = resultView(legacy);
  assert.equal(v.key, "record");
  assert.equal(v.derived, true);
  const el = card(legacy);
  assert.match(el.querySelector(".result-chip").getAttribute("data-tip"), /由旧版记录推断/);
  // a job id is not a result: job_submitted, and a succeeded poll of a running job, are never 完成
  const submitted = card({ status: "job_submitted", job: { id: "j1", kind: "pipeline.dock", state: "queued" }, receipt: receipt({ kind: null, outcome: "queued", assurance: "none" }, { where: "runner" }) });
  assert.match(text(submitted), /已提交任务/);
  assert.match(text(submitted), /尚无结果（排队或运行中）/);
  assert.ok(!/完成/.test(submitted.querySelector(".tool-card__head").textContent));
  const polled = card({ status: "succeeded", job: { id: "j1", kind: "pipeline.dock", state: "running" }, receipt: receipt({ kind: null, outcome: "running", assurance: "none" }, { where: "runner" }) });
  assert.equal(polled.querySelector(".tool-card__status").getAttribute("aria-label"), "任务：运行中");
  assert.ok(!/完成/.test(polled.querySelector(".tool-card__head").textContent));
  // the browser's CPU kernels are named by what computed
  const simd = card({ status: "succeeded", receipt: receipt({ kind: "computation", outcome: "succeeded", assurance: "none" }, { compute: { backend: "wasm-simd", kernels: [{ kernel: "sequence.counts", outcome: "used" }] } }) });
  assert.match(simd.querySelector(".where").textContent, /WebAssembly SIMD/);
  simd.querySelector(".tool-card__head").dispatchEvent(new window.Event("click"));
  assert.match(text(simd), /内核结果经 Python 核对/);
});

test("provenance: receipt/1 rows for the result, the execution, the provenance and the compute", () => {
  setLang("zh");
  const env = {
    status: "succeeded", tool: "native.gc_content", via: "native.gc_content",
    receipt: {
      schema: "tcmstudio.receipt/1", where: "browser", runtime: "pyodide", device: "webgpu",
      execution: {
        runtime: "pyodide", kernel: "webgpu", engine: null, implementation: { ref: "bioagent.tools.sequence:gc_content", version: "0.2.7" },
        validation: { ok: true, schema: "entry", repairs: ["window: 0"], normalized_sha256: "c".repeat(64) },
        device: { requested: "auto", assigned: "webgpu", used: "webgpu", evidence: "validated" },
        placement: { requested: "auto", chosen: "browser", reason: "ok", runner: null },
        approvals: [{ reason: "network", scope: "standing", hosts: ["rest.uniprot.org"] }],
      },
      provenance: { datasets: [{ id: "tcmscience.tcm.seed", version: "0.2.6", release: null, licence: "MIT" }], seeds: { seed: 42 }, models: [] },
      result: { kind: "computation", outcome: "succeeded", assurance: "none", checks: [{ id: "schema_valid", ok: true }], artifacts: { expected: ["a.json"], missing: [], hash_mismatch: [] }, fallbacks: [{ what: "compute", reason: "the GPU was set aside" }] },
      compute: { backend: "webgpu", precision: "uint32 exact counts", gpu: { adapter: { kind: "software" }, dispatched: true, validated: true, accelerated: false, cpu_baseline_ms: 3 } },
    },
  };
  const secs = provenanceSections(env);
  assert.deepEqual(secs.map((s) => s.id), ["result", "execution", "provenance", "compute"]);
  const flat = (id) => secs.find((s) => s.id === id).rows.filter(Boolean).map(([k, v]) => `${k}: ${typeof v === "string" ? v : v.textContent}`).join(" | ");
  assert.match(flat("result"), /结果类型: 计算结果/);
  assert.match(flat("result"), /保证: 无独立核验/);
  assert.match(flat("result"), /schema_valid/);
  assert.match(flat("result"), /预期 1 个 · 缺失 0 个/);
  assert.match(flat("result"), /the GPU was set aside/);
  assert.match(flat("execution"), /运行时: Pyodide（浏览器）/);
  assert.match(flat("execution"), /计算内核: WebGPU/);
  assert.match(flat("execution"), /bioagent\.tools\.sequence:gc_content · 0\.2\.7/);
  assert.match(flat("execution"), /通过（entry 模式，修复 1 处）/);
  assert.match(flat("execution"), /输出已核对/);
  assert.match(flat("execution"), /auto → browser/);
  assert.match(flat("execution"), /network · 已有的项目批准 rest\.uniprot\.org/);
  assert.match(flat("provenance"), /tcmscience\.tcm\.seed · 版本 0\.2\.6 · MIT/);
  assert.match(flat("provenance"), /seed=42/);
  assert.match(flat("compute"), /适配器: 软件模拟 GPU/);
  // a CPU baseline exists, but a software adapter is never compared: not measured, never "not faster"
  assert.match(flat("compute"), /加速: 未测量（不声称加速）/);
  assert.ok(!/实测/.test(flat("compute")));
  const computeOf = (gpu) => provenanceSections({ ...env, receipt: { ...env.receipt, compute: { ...env.receipt.compute, gpu } } }).find((s) => s.id === "compute").rows.filter(Boolean).map(([k, v]) => `${k}: ${typeof v === "string" ? v : v.textContent}`).join(" | ");
  // an unidentified adapter whose counts were used: the baseline is there, no comparison was made (accelerator.js basis)
  const unidentified = computeOf({ adapter: { kind: "unidentified" }, dispatched: true, validated: true, verified: null, accelerated: false, gpu_ms: 3.4, cpu_baseline_ms: 1.2, basis: "the adapter is unidentified, so it is not counted as a GPU" });
  assert.match(unidentified, /加速: 未测量（不声称加速）/);
  assert.match(unidentified, /the adapter is unidentified/);
  // a physical adapter, its known-answer test passed, but a step not timed: not compared
  assert.match(computeOf({ adapter: { kind: "physical" }, validated: true, verified: true, accelerated: false, gpu_ms: 3, cpu_baseline_ms: 1, basis: "this call's GPU time is incomplete (a step was not timed), so it is not compared" }), /加速: 未测量/);
  // measured on this device: not faster, and faster
  const basis = "measured on this device: 3 ms on the GPU against 1 ms by the wasm-simd kernel for counts at the same size class";
  assert.match(computeOf({ adapter: { kind: "physical" }, validated: true, verified: true, accelerated: false, gpu_ms: 3, cpu_baseline_ms: 1, basis }), /加速: 本设备实测不比 CPU 内核快/);
  assert.match(computeOf({ adapter: { kind: "physical" }, validated: true, verified: true, accelerated: true, gpu_ms: 1, cpu_baseline_ms: 3, basis }), /加速: 本设备实测比 CPU 内核快/);
  assert.equal(gpuMeasured({ adapter: { kind: "software" }, validated: true, verified: true, accelerated: false, gpu_ms: 3, cpu_baseline_ms: 1, basis }), false);
  // accelerated only when measured on a physical adapter
  const fast = { ...env, receipt: { ...env.receipt, compute: { ...env.receipt.compute, gpu: { adapter: { kind: "software" }, accelerated: true, cpu_baseline_ms: 3 } } } };
  assert.ok(!/实测比 CPU 内核快/.test(provenanceSections(fast).find((s) => s.id === "compute").rows.filter(Boolean).map(([, v]) => (typeof v === "string" ? v : v.textContent)).join(" ").replace("不比", "")));
  // a legacy envelope: its result derived, and the section says so; nothing else is made up
  const legacy = provenanceSections({ status: "succeeded", governance: { kind: "tcmdb" }, receipt: { where: "runner", device: "cpu" } });
  assert.deepEqual(legacy.map((s) => s.id), ["result"]);
  assert.match(legacy[0].note, /推断/);
});

// ------------------------------------------------------------------------------------------------- hardware

test("hardware: a software adapter is 软件模拟 GPU, never acceleration; WebNN is detected and NPU execution unverified; remote is not configured", () => {
  setLang("zh");
  const state = {
    browser: {
      device: { cores: 8, webgpu: { api: true, adapter: { vendor: "google", architecture: "swiftshader", description: "", fallback: true } } },
      caps: { wasm: { simd: true }, webgpu: { api: true }, webnn: { api: true, contexts: [{ label: "npu", created: true }] } },
    },
    computeEvidence: noteEvidence(emptyEvidence(), { status: "succeeded", receipt: { where: "browser", compute: { backend: "wasm-simd" } } }),
    runner: { status: "ready", devices: { devices: [{ id: "cpu", kind: "cpu" }, { id: "cuda:0", kind: "cuda", name: "NVIDIA RTX 4090", available: true }] }, settings: { device: "cuda:0" } },
    settings: { runner: { url: "http://127.0.0.1:8765" } },
  };
  const rows = Object.fromEntries(evidenceRows(state).map((r) => [r.id, r]));
  assert.deepEqual(evidenceRows(state).map((r) => r.id), ["cpu", "simd", "webgpu", "runner", "npu", "remote"]);
  assert.equal(rows.webgpu.name, "软件模拟 GPU");
  assert.match(rows.webgpu.evidence, /从不算作 GPU 加速/);
  assert.match(rows.simd.evidence, /1 次计数由 SIMD 内核计算/);
  assert.equal(rows.npu.reported, "检测到 WebNN；NPU 执行未经验证");
  assert.equal(rows.remote.reported, "未配置");
  assert.equal(rows.runner.name, "本机 Runner GPU");
  assert.match(rows.runner.reported, /CUDA:0 NVIDIA RTX 4090/);
  assert.match(rows.runner.evidence, /选定，不等于核实了实际使用/);
  assert.ok(!/加速/.test(rows.runner.evidence));
  // a runner on another machine is never called local
  const remote = evidenceRows({ ...state, settings: { runner: { url: "http://10.0.0.5:8765" } } }).find((r) => r.id === "runner");
  assert.equal(remote.name, "远程 Runner GPU");
  // no WebNN at all: no verified NPU backend
  const none = evidenceRows({ ...state, browser: { ...state.browser, caps: { ...state.browser.caps, webnn: { api: false } } } }).find((r) => r.id === "npu");
  assert.equal(none.reported, "没有可用的已验证 NPU 后端");
  // the browser card's line goes through the one classifier: an adapter that names nothing is not a GPU
  assert.match(webgpuLine({ webgpu: { api: true, adapter: { vendor: "", architecture: "", description: "" } } }), /不视为 GPU/);
  assert.match(webgpuLine({ webgpu: { api: true, adapter: { vendor: "google", architecture: "swiftshader", software: true } } }), /软件模拟 GPU/);
  setLang("en");
  const en = Object.fromEntries(evidenceRows(state).map((r) => [r.id, r]));
  assert.equal(en.npu.reported, "WebNN detected; NPU execution unverified");
  assert.equal(en.remote.reported, "Not configured");
  assert.equal(en.webgpu.name, "Software-emulated GPU");
  setLang("zh");
});

test("hardware: a validated WebGPU receipt raises the rung; accelerated needs a measured, physical adapter", () => {
  setLang("zh");
  const ev = emptyEvidence();
  noteEvidence(ev, { status: "succeeded", receipt: { where: "browser", compute: { backend: "webgpu", gpu: { adapter: { kind: "software" }, dispatched: true, validated: true, accelerated: true, cpu_baseline_ms: 1 } }, execution: { device: { evidence: "accelerated" } } } });
  assert.equal(ev.webgpu.evidence, "verified", "a software adapter never reaches accelerated");
  assert.equal(ev.webgpu.accelerated, null, "no comparison was made: neither faster nor not faster");
  // an unidentified adapter with a CPU baseline: no comparison either; the list says not measured, never "not faster"
  const unid = noteEvidence(emptyEvidence(), { status: "succeeded", receipt: { where: "browser", compute: { backend: "webgpu", gpu: { adapter: { kind: "unidentified" }, dispatched: true, validated: true, verified: null, accelerated: false, gpu_ms: 3.4, cpu_baseline_ms: 1.2, basis: "the adapter is unidentified, so it is not counted as a GPU" } }, execution: { device: { evidence: "validated" } } } });
  assert.equal(unid.webgpu.accelerated, null);
  const unidRow = evidenceRows({ browser: { device: { webgpu: { api: true, adapter: { kind: "unidentified" } } }, caps: null }, computeEvidence: unid, runner: {}, settings: {} }).find((r) => r.id === "webgpu");
  assert.match(unidRow.evidence, /是否更快未经测量/);
  assert.ok(!/实测/.test(unidRow.evidence));
  noteEvidence(ev, { status: "succeeded", receipt: { decided_by: "router", where: "page" } });
  assert.equal(ev.calls.webgpu, 1, "a page-made envelope adds nothing");
  const measured = { adapter: { kind: "physical" }, dispatched: true, validated: true, verified: true, gpu_ms: 0.5, cpu_baseline_ms: 1, basis: "measured on this device: 0.5 ms on the GPU against 1 ms by the js kernel for counts at the same size class" };
  const phys = noteEvidence(emptyEvidence(), { status: "succeeded", receipt: { where: "browser", compute: { backend: "webgpu", gpu: { ...measured, accelerated: true } }, execution: { device: { evidence: "accelerated" } } } });
  assert.equal(phys.webgpu.evidence, "accelerated");
  assert.equal(phys.webgpu.accelerated, true);
  // measured and not faster: said as such
  const slow = noteEvidence(emptyEvidence(), { status: "succeeded", receipt: { where: "browser", compute: { backend: "webgpu", gpu: { ...measured, gpu_ms: 2, accelerated: false } }, execution: { device: { evidence: "verified" } } } });
  assert.equal(slow.webgpu.accelerated, false);
  const slowRow = evidenceRows({ browser: { device: { webgpu: { api: true, adapter: { kind: "physical", vendor: "nvidia" } } }, caps: null }, computeEvidence: slow, runner: {}, settings: {} }).find((r) => r.id === "webgpu");
  assert.match(slowRow.evidence, /本设备实测不比 CPU 内核快/);
  // an accelerated rung without a comparison behind it stays verified
  const claimed = noteEvidence(emptyEvidence(), { status: "succeeded", receipt: { where: "browser", compute: { backend: "webgpu", gpu: { adapter: { kind: "physical" }, validated: true, verified: true, accelerated: true, cpu_baseline_ms: 1 } }, execution: { device: { evidence: "accelerated" } } } });
  assert.equal(claimed.webgpu.evidence, "verified");
});

// ------------------------------------------------------------------------------------------------- approvals

test("project approvals: network grants name their hosts, a remote runner is never the local one", async () => {
  setLang("zh");
  const cat = new Catalog(DOC);
  const sha = await hostsDigest(["bidd.group"]);
  const el = approvalLabel(`job.tcmdb.fetch:network@${sha}`, cat);
  await flush(); await flush();
  assert.match(text(el), /job\.tcmdb\.fetch · 联网 （主机：bidd\.group）/);
  assert.match(text(approvalLabel("connector.uniprot.search:network", cat)), /rest\.uniprot\.org/);
  assert.match(text(approvalLabel("job.tcmdb.fetch:network@bidd.group", cat)), /主机：bidd\.group/, "a key written without Web Crypto names its hosts");
  assert.match(text(approvalLabel("runner@10.0.0.5:8765", cat)), /远程 Runner.*10\.0\.0\.5:8765/);
  assert.match(text(approvalLabel("remote@compute.example.org", cat)), /远程计算后端.*compute\.example\.org/);
  assert.match(text(approvalLabel("runner", cat)), /本机 Runner/);
  assert.match(text(approvalLabel("skill.dock-ligands:remote_upload", cat)), /第三方服务/);
});
