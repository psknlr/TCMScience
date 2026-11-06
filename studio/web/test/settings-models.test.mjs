// Settings → Models (ui/pages/settings.js): every provider card and every custom endpoint has an idle limit, the time
// a model stream may send nothing before the call is cut and retried once; what is typed there reaches the model
// calls (core/providers.js activeProvider → core/llm/sse.js idleTimeout). The DOM is studio/web/dev/test/setup.mjs's
// (linkedom).
import { resetStorage } from "./fixtures/setup.mjs";
import { window } from "../dev/test/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

import { idleTimeout } from "../js/core/llm/sse.js";
import { activeProvider } from "../js/core/providers.js";
import { App } from "../js/ui/app.js";
import { mountSettings } from "../js/ui/pages/settings.js";
import { registerUiStrings } from "../js/ui/strings.js";

registerUiStrings();
const saved = () => new Promise((r) => setTimeout(r, 350));   // the page saves 300 ms after the last keystroke
const type = (input, value) => { input.value = value; input.dispatchEvent(new window.Event("input")); };

function modelsPage(settings = {}) {
  resetStorage();
  const app = new App();
  app.setSetting(settings);
  app.state.route = { name: "settings", tab: "models", params: {} };
  const main = document.createElement("main");
  const view = mountSettings(app, main);
  const open = (id) => main.querySelector(`[data-key="provider:${id}"] .disclosure__summary`).dispatchEvent(new window.Event("click"));
  const field = (id) => main.querySelector(`[data-key="field:${id}:idle"]`);
  return { app, main, view, open, field };
}

test("a preset's card: the default for where it runs is shown; a limit typed in seconds is saved in ms and used", async () => {
  const { app, view, open, field } = modelsPage();
  try {
    open("openai");
    const openai = field("openai");
    assert.ok(openai, "the OpenAI card has an idle limit");
    assert.equal(openai.getAttribute("placeholder"), "300", "OpenAI's own default");
    assert.equal(openai.value, "");
    type(openai, "600");
    await saved();
    assert.deepEqual(app.state.settings.idleLimits, { openai: 600_000 });
    assert.equal(idleTimeout(activeProvider(app.state.settings, { provider: "openai" })), 600_000);
    type(openai, "");
    await saved();
    assert.deepEqual(app.state.settings.idleLimits, {}, "emptied: the default again");
    assert.equal(idleTimeout(activeProvider(app.state.settings, { provider: "openai" })), 300_000);
    open("deepseek");
    assert.equal(field("deepseek").getAttribute("placeholder"), "90");
    open("ollama");
    assert.equal(field("ollama").getAttribute("placeholder"), "600", "a server on this computer");
  } finally {
    view.destroy();
  }
});

test("a custom endpoint's card: its limit is kept in the endpoint's record; 0 means none; one already set is shown", async () => {
  const { app, view, field } = modelsPage({ customProviders: [{ id: "c1", label: "Gemini", format: "openai", base_url: "", model: "" }] });
  try {
    const own = field("c1");
    assert.ok(own, "a new endpoint's card opens with an idle limit");
    assert.equal(own.getAttribute("placeholder"), "90");
    type(own, "300");
    await saved();
    assert.equal(app.state.settings.customProviders[0].idleMs, 300_000);
    assert.equal(idleTimeout(activeProvider(app.state.settings, { provider: "c1" })), 300_000);
    type(own, "0");
    await saved();
    assert.equal(idleTimeout(activeProvider(app.state.settings, { provider: "c1" })), 0);
    type(own, "-3");
    await saved();
    assert.equal(app.state.settings.customProviders[0].idleMs, 0, "a negative number is not saved");
  } finally {
    view.destroy();
  }
  const set = modelsPage({ customProviders: [{ id: "c2", label: "llama.cpp", format: "openai", base_url: "", model: "", idleMs: 900_000 }] });
  try {
    assert.equal(set.field("c2").value, "900");
  } finally {
    set.view.destroy();
  }
});
