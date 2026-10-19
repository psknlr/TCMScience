// The catalog page's words for a browser job kind whose job service is not running here (ui/pages/catalog.js): the
// browser's own job service, by place, never "the runner's"; and the generic job_service remedy, which no longer says
// jobs run only on the runner. The DOM is studio/web/dev/test/setup.mjs's (linkedom).
import "./fixtures/setup.mjs";
import "../dev/test/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

import { entryState, requirement } from "../js/core/catalog.js";
import { setLang, t } from "../js/core/i18n.js";
import { entryFields, requirementView, stateRow } from "../js/ui/pages/catalog.js";
import { registerUiStrings } from "../js/ui/strings.js";
import { pipelineCatalogDoc } from "./fixtures/fakes.mjs";

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
