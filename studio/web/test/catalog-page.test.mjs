// The catalog page's words for a browser job kind whose job service is not running here (ui/pages/catalog.js): the
// browser's own job service, by place, never "the runner's"; the generic job_service remedy, which no longer says
// jobs run only on the runner; a browser job kind's detail (what it loads, its chemistry, what it reads here); and
// Settings → Compute's device check report (ui/pages/settings.js). The DOM is studio/web/dev/test/setup.mjs's (linkedom).
import "./fixtures/setup.mjs";
import "../dev/test/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

import { Catalog, entryState, requirement } from "../js/core/catalog.js";
import { setLang, t } from "../js/core/i18n.js";
import { ToolRouter } from "../js/core/router.js";
import { App } from "../js/ui/app.js";
import { entryFields, mountCatalog, requirementView, stateRow } from "../js/ui/pages/catalog.js";
import { deviceReport } from "../js/ui/pages/settings.js";
import { registerUiStrings } from "../js/ui/strings.js";
import { admetCatalogDoc, pipelineCatalogDoc } from "./fixtures/fakes.mjs";

registerUiStrings();
const text = (el) => el.textContent.replace(/\s+/g, " ");
const readyChip = (cells, where) => text(cells.find((c) => c.getAttribute("data-field") === "ready" && c.getAttribute("data-where") === where));

test("a browser job kind whose job service is not running here is labelled by its place: this browser's, not the runner's", () => {
  const rnaseq = pipelineCatalogDoc().entries.find((e) => e.id === "job.pipeline.rnaseq");
  const state = entryState(rnaseq, { browser: "idle", runner: "offline", jobs: { status: "unsupported", kinds: [] } });
  const browser = state.readiness.browser;
  assert.deepEqual([browser.state, browser.requires.map((q) => q.need)], ["needs_runner", ["job_service"]]);
  try {
    for (const [lang, own, runners] of [["en", /this browser's job service is not running/, /the runner's job service/], ["zh", /本浏览器的任务服务未在运行/, /Runner 的任务服务/]]) {
      setLang(lang);
      const cells = stateRow(entryFields(rnaseq, { state }));
      assert.match(readyChip(cells, "browser"), own, lang);
      assert.doesNotMatch(readyChip(cells, "browser"), runners, lang);
      const need = text(requirementView(browser.requires[0], "browser"));
      assert.match(need, lang === "en" ? /Needs this browser's job service/ : /需要本浏览器的任务服务/);
      assert.doesNotMatch(need, runners);
      // the runner's own job service keeps its words
      assert.match(text(requirementView(requirement("job_service"), "runner")), lang === "en" ? /Needs the runner's job service/ : /需要 Runner 的任务服务/);
      const runnerState = { implementation: "implemented", readiness: { browser: browser, runner: { state: "needs_runner", requires: [{ need: "job_service", detail: "the job layer cannot run on Windows without WSL", remedy: "Restart the runner where its job service can start." }] } } };
      assert.match(readyChip(stateRow(entryFields(rnaseq, { state: runnerState })), "runner"), lang === "en" ? /the runner's job service cannot run/ : /Runner 的任务服务无法运行/);
    }
  } finally {
    setLang("zh");
  }
});

test("the generic job_service remedy does not say jobs run only on the runner (the browser runs some kinds)", () => {
  const remedies = [requirement("job_service").remedy, t("ui.need.remedy.job_service", undefined, "en"), t("ui.need.remedy.job_service", undefined, "zh")];
  for (const r of remedies) assert.doesNotMatch(r, /run only there|只在那里运行/, r);
  assert.match(requirement("job_service").remedy, /this browser runs/);
  assert.match(t("ui.need.remedy.job_service", undefined, "zh"), /浏览器/);
  for (const k of ["ui.catalog.readiness.job_service_browser", "ui.need.job_service_browser"]) {
    assert.notEqual(t(k, undefined, "zh"), t(k, undefined, "en"), `${k} has its own zh and en`);
    assert.match(t(k, undefined, "zh"), /[一-鿿]/);
  }
});


test("Settings → Compute: a device check's report as the person reads it: the adapter and its kind, the known answer, each size's times, exactness and verdict", async () => {
  setLang("en");
  const report = {
    schema: "tcmstudio.device-check/1", id: "d".repeat(64), measured_at: "2026-10-20T08:00:00.000Z", verdict: "accelerated at 1 of 2 sizes", label: null, verdict_reason: "…",
    device: { adapter: { kind: "physical", vendor: "apple", architecture: "metal-3", description: "Apple M4 Max" }, gpu_error: null },
    kernels: { "tanimoto.best": { known_answer: { ok: true, ms: 41.2 }, sizes: [
      { queries: 32, refs: 2048, exact: { "wasm-simd": true, webgpu: true }, timings: { js: { ms: 15.6 }, "wasm-simd": { ms: 1.2 }, webgpu: { ms: 3.4 } }, accelerated: false, reason: "the GPU took 3.4 ms (search and check), WebAssembly SIMD 1.2 ms: not faster here" },
      { queries: 4096, refs: 10504, exact: { "wasm-simd": true, webgpu: true }, timings: { js: { ms: 5788 }, "wasm-simd": { ms: 723 }, webgpu: { ms: 90 } }, accelerated: true, reason: "faster here" },
    ] } },
    stored: { ok: true },
  };
  const t1 = text(deviceReport(report));
  assert.match(t1, /Apple M4 Max/);
  assert.match(t1, /Physical WebGPU adapter/);
  assert.match(t1, /passed \(41 ms\)/);
  assert.match(t1, /32 × 2,048.*GPU 3\.4 ms · WebAssembly SIMD 1\.2 ms · JavaScript 15\.6 ms · exact · not faster on the GPU/);
  assert.match(t1, /4,096 × 10,504.*GPU 90 ms .* · exact · faster on the GPU/);
  assert.match(t1, /Kept as this device's calibration/);
  // a software adapter, refused: no physical adapter, and its sizes have no verdict
  const sw = { ...report, verdict: "no physical adapter", label: null, device: { adapter: null, gpu_error: { message: "WebGPU adapter is a software CPU renderer" } },
    kernels: { "tanimoto.best": { known_answer: null, sizes: [{ queries: 32, refs: 2048, exact: { "wasm-simd": true, webgpu: null }, timings: { js: { ms: 15 }, "wasm-simd": { ms: 1 }, webgpu: null }, accelerated: null, reason: "no WebGPU adapter" }] } } };
  const t2 = text(deviceReport(sw));
  assert.match(t2, /No WebGPU adapter \(WebGPU adapter is a software CPU renderer\)/);
  assert.match(t2, /GPU not measured/);
  assert.match(t2, /no verdict/);
  assert.match(t2, /not run/);
  setLang("zh");
  assert.match(text(deviceReport(report)), /GPU 更快/);
});

test("catalog: a browser job kind's detail names what its job worker loads, the chemistry it runs in the page and what it reads here", async () => {
  setLang("en");
  const app = new App();
  const doc = admetCatalogDoc();
  doc.entries.find((e) => e.id === "job.pipeline.admet").execution.browser_job.inputs = "a CSV/TSV (name,smiles) or .smi file from the project's files, or one SMILES";
  app.catalog = new Catalog(doc);
  app.state.route = { name: "catalog", entryId: "job.pipeline.admet", params: {} };
  app.state.layout = "mobile";
  app.toolRouter = new ToolRouter({ catalog: app.catalog, runtimes: {}, settings: app.state.settings });
  const main = document.createElement("main");
  const view = mountCatalog(app, main);
  const detail = text(main.querySelector(".catalog__detail"));
  assert.match(detail, /Browser job\s*numpy, scipy, scikit-learn/);
  assert.match(detail, /Browser chemistry\s*rdkit\.js 2026\.9\.1/);
  assert.match(detail, /Read in the browser\s*a CSV\/TSV \(name,smiles\) or \.smi file/);
  view.destroy();
  setLang("zh");
});
