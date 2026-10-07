// Gallery sections for the composed views: the conversation workspace with the inspector on each tab, the composer,
// the compute and model panels, and the project overview — rendered by the app's own modules over an in-memory
// store seeded with the fixture envelopes (dev/demo.js). Nothing is written to this browser's IndexedDB.

import { openStore } from "../js/core/store.js";
import { t } from "../js/core/i18n.js";
import { App } from "../js/ui/app.js";
import { mountComposer } from "../js/ui/composer.js";
import { mountConversation } from "../js/ui/conversation.js";
import { h } from "../js/ui/dom.js";
import { mountInspector } from "../js/ui/inspector.js";
import { computePanel, modelPanel } from "../js/ui/panels.js";
import { mountProject } from "../js/ui/pages/project.js";
import { seedDemo } from "./demo.js";

let shared = null;

async function demoApp(FIX, { tab = "run", runner = "ready" } = {}) {
  if (!shared) {
    const store = await openStore({ memory: true });
    const seeded = await seedDemo(store, FIX);
    shared = { store, ...seeded };
  }
  const app = new App();
  app.store = shared.store;
  app.catalog = FIX.catalog || null;
  app.runtimes = { browser: { status: "ready", onStatus: () => () => {}, device: async () => null }, runner: null };
  app.state.layout = "wide";
  app.state.project = shared.project;
  app.state.projects = [shared.project];
  app.state.conversations = shared.conversations;
  app.state.conversation = shared.conversations[0];
  app.state.messages = await shared.store.messages.list(shared.conversations[0].id);
  app.state.route = { name: "conversation", projectId: shared.project.id, convId: shared.conversations[0].id };
  app.state.inspector = { open: true, tab, scope: "turn", messageId: null, selection: null };
  app.state.browser = { status: "ready", progress: 100, device: { cores: 8, memory_gb: 8, cross_origin_isolated: true, webgpu: { api: true, adapter: { vendor: "google", architecture: "swiftshader", description: "", software: true } } } };
  app.state.relay = { ok: true, model: "Tao-S1", limits: { per_minute: 10, per_day: 200, tokens_per_day: 400000 }, max_output_tokens: 8192 };
  if (runner === "ready") app.state.runner = { status: "ready", info: FIX.runner_info.info, devices: FIX.runner_info.devices, settings: FIX.runner_info.settings, error: null };
  else app.state.runner = { status: "idle", info: null, devices: null, settings: null, error: null };
  return app;
}

function frame(cls, ...children) {
  return h(`div.g-frame.${cls}`, ...children);
}

export const sections = [
  {
    id: "workspace", title: () => t("ui.gallery.workspace"),
    render(FIX) {
      const box = h("div.stack.stack--lg", h("p.g-lede", t("ui.gallery.workspace_lede")));
      (async () => {
        const app = await demoApp(FIX, { tab: "run" });
        const main = h("div.main");
        const ins = h("aside.inspector");
        box.append(frame("g-workspace", main, ins));
        mountConversation(app, main);
        mountInspector(app, ins);
        const tabs = h("div.g-grid.g-grid--wide");
        for (const tab of ["evidence", "claims", "files", "provenance"]) {
          const a = await demoApp(FIX, { tab });
          const host = h("aside.inspector");
          tabs.append(h("div.g-cell", h("div.g-label", `inspector · ${tab}`), frame("g-inspector", host)));
          mountInspector(a, host);
        }
        box.append(tabs);
      })().catch((err) => box.append(h("pre.code-block", String(err?.stack || err))));
      return box;
    },
  },
  {
    id: "composer", title: () => t("ui.gallery.composer"),
    render(FIX) {
      const box = h("div.stack.stack--lg");
      (async () => {
        const app = await demoApp(FIX);
        const hero = mountComposer(app, { variant: "hero" });
        const dock = mountComposer(app, { variant: "dock", initialText: "附子的现有证据有哪些？按证据种类分组" });
        await dock.addFiles([
          new File(["gene\tlog2fc\nTNF\t-1.42\n"], "deg_lps_vs_puerarin.tsv", { type: "text/tab-separated-values" }),
          new File([JSON.stringify({ herbs: ["葛根", "黄芩", "黄连", "甘草"] })], "formula.json", { type: "application/json" }),
        ]);
        box.append(h("div.g-cell", h("div.g-label", "hero (project page)"), hero.el), h("div.g-cell", h("div.g-label", "dock, with attachments"), dock.el));
      })().catch((err) => box.append(h("pre.code-block", String(err?.stack || err))));
      return box;
    },
  },
  {
    id: "panels", title: () => t("ui.gallery.panels"),
    render(FIX) {
      const box = h("div.g-grid");
      (async () => {
        const ready = await demoApp(FIX, { runner: "ready" });
        const idle = await demoApp(FIX, { runner: "idle" });
        box.append(
          h("div.g-cell", h("div.g-label", "compute · runner connected (compact)"), h("div.g-panel", computePanel(ready, { compact: true }))),
          h("div.g-cell", h("div.g-label", "compute · runner not connected"), h("div.g-panel", computePanel(idle, { compact: true }))),
          h("div.g-cell", h("div.g-label", "compute · full (settings page)"), h("div.g-panel", computePanel(ready, { compact: false }))),
          h("div.g-cell", h("div.g-label", "model"), h("div.g-panel", modelPanel(ready, {}))));
      })().catch((err) => box.append(h("pre.code-block", String(err?.stack || err))));
      return box;
    },
  },
  {
    id: "project", title: () => t("ui.gallery.project"),
    render(FIX) {
      const box = frame("g-page");
      (async () => {
        const app = await demoApp(FIX);
        app.state.route = { name: "project", projectId: app.state.project.id };
        app.state.conversation = null;
        const main = h("div.main");
        box.append(main);
        mountProject(app, main);
      })().catch((err) => box.append(h("pre.code-block", String(err?.stack || err))));
      return box;
    },
  },
];
