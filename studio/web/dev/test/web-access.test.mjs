// Web access ("联网") and the runner's own network switch: quick presses, presses on a copy of the switch drawn before
// a change, writes that fail, other tabs, and a store connection the browser closed. Each of these used to leave the
// switch somewhere other than where the last press put it.
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { IDBFactory } from "fake-indexeddb";
import { openStore } from "../../js/core/store.js";
import { ToolRouter } from "../../js/core/router.js";
import { App, mergeRunnerSettings } from "../../js/ui/app.js";
import { webSwitch } from "../../js/ui/panels.js";
import { registerUiStrings } from "../../js/ui/strings.js";

registerUiStrings();
void window;

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

/** An App with a real store (fake IndexedDB) and an open project; `slow` delays every write by that many ms. */
async function makeApp({ idb = new IDBFactory(), slow = 0, projectId = null } = {}) {
  const app = new App();
  app.store = await openStore({ indexedDB: idb });
  if (slow) {
    const backend = app.store.backend;
    const bulk = backend.bulk.bind(backend);
    backend.bulk = async (ops) => { await delay(slow); return bulk(ops); };
  }
  app.toolRouter = new ToolRouter({ catalog: null, runtimes: {}, settings: app.state.settings, store: app.store });
  const p = projectId ? await app.store.projects.get(projectId) : await app.store.projects.create({ name: "葛根芩连汤" });
  app.state.project = p;
  return { app, p };
}

const stored = async (app, id) => (await app.store.projects.get(id)).defaults;
const toastText = () => document.getElementById("toasts")?.textContent || "";

test("two quick presses of the web chip end off: the second press toggles what the first asked for", async () => {
  const { app, p } = await makeApp({ slow: 25 });
  const press = () => app.setWeb(!app.webOn()); // the composer chip's handler
  const first = press();
  assert.equal(app.webOn(), true, "the press shows at once, before the write lands");
  const second = press();
  assert.equal(app.webOn(), false);
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(app.webOn(), false);
  assert.equal((await stored(app, p.id)).web, false);
  await Promise.all([press(), press(), press()]);
  assert.equal(app.webOn(), true, "three presses end on");
  assert.equal((await stored(app, p.id)).web, true);
});

test("the router sees what the switch shows once a press has landed, and a turn waits for it", async () => {
  const { app, p } = await makeApp({ slow: 25 });
  const pending = app.setWeb(true);
  await app.webSettled();
  assert.equal((await stored(app, p.id)).web, true, "webSettled resolves after the write");
  await pending;
  const approvals = app.toolRouter.approvals;
  assert.equal((await approvals.get(p.id)).web, true);
});

test("a default changed from an older copy of the project keeps web access on (the project page's selects)", async () => {
  const { app, p } = await makeApp();
  const drawn = structuredClone(p); // the defaults section keeps the record it was drawn from
  await app.setWeb(true);
  await app.saveProjectDefaults(drawn.id, { compute: "browser" });
  await app.saveProjectDefaults(drawn.id, { provider: "openai", model: "gpt-x" });
  let d = await stored(app, p.id);
  assert.equal(d.web, true, "web access is not put back by the compute or model select");
  assert.equal(d.compute, "browser");
  assert.equal(d.provider, "openai");
  await app.saveProjectDefaults(p.id, { compute: undefined });
  d = await stored(app, p.id);
  assert.equal("compute" in d, false, "undefined removes the default (follow the global setting)");
  assert.equal(d.web, true);
  assert.equal(app.state.project.defaults.web, true, "the open project follows the stored record");
});

test("setCompute on a project with its own compute default changes only that default", async () => {
  const { app, p } = await makeApp();
  await app.saveProjectDefaults(p.id, { compute: "runner" });
  const press = app.setWeb(true);
  await app.setCompute("browser");
  await press;
  const d = await stored(app, p.id);
  assert.deepEqual({ web: d.web, compute: d.compute }, { web: true, compute: "browser" });
});

test("a write that fails puts every copy of the switch back to what the store holds, and says why", async () => {
  const { app, p } = await makeApp();
  const events = [];
  app.on("web", (v) => events.push(v));
  app.store.projects.setDefaults = async () => { throw new Error("QuotaExceededError: the disk is full"); };
  const row = webSwitch(app, { description: "d" });
  document.body.append(row);
  const sw = row.querySelector('[role="switch"]');
  assert.equal(await app.setWeb(true), false);
  assert.equal(app.webOn(), false);
  assert.equal((await stored(app, p.id)).web, false);
  assert.equal(events[0], true, "shown at once");
  assert.equal(events.at(-1), false, "then put back");
  assert.equal(sw.getAttribute("aria-checked"), "false");
  assert.match(toastText(), /QuotaExceededError/);
  row.remove();
});

test("a failed first press does not undo a second press that is still being written", async () => {
  const { app, p } = await makeApp({ slow: 10 });
  const real = app.store.projects.setDefaults;
  let calls = 0;
  app.store.projects.setDefaults = async (...a) => { if (calls++ === 0) { await delay(10); throw new Error("first write failed"); } return real(...a); };
  const first = app.setWeb(true);   // fails
  const second = app.setWeb(false); // lands
  assert.equal(await first, false);
  assert.equal(app.webOn(), false, "the second press is still what is shown");
  assert.equal(await second, true);
  assert.equal(app.webOn(), false);
  assert.equal((await stored(app, p.id)).web, false);
});

test("every copy of the web switch on the page follows a press made on any of them, or on the chip", async () => {
  const { app } = await makeApp({ slow: 5 });
  const a = webSwitch(app, { description: "popover" });
  const b = webSwitch(app, { description: "project defaults" });
  document.body.append(a, b);
  const swA = a.querySelector('[role="switch"]');
  const swB = b.querySelector('[role="switch"]');
  assert.equal(swA.dataset.sync, "web");
  swA.dispatchEvent(new window.Event("click"));
  assert.equal(swA.getAttribute("aria-checked"), "true");
  assert.equal(swB.getAttribute("aria-checked"), "true", "the other copy shows the press at once");
  await app.webSettled();
  await app.setWeb(!app.webOn()); // the chip
  assert.equal(swA.getAttribute("aria-checked"), "false");
  assert.equal(swB.getAttribute("aria-checked"), "false");
  // a copy that is no longer on the page is not updated (and holds no listener of its own)
  b.remove();
  await app.setWeb(true);
  assert.equal(swA.getAttribute("aria-checked"), "true");
  assert.equal(swB.getAttribute("aria-checked"), "false");
  a.remove();
});

test("with no project open, web access is the default for new projects (the settings, not a record)", async () => {
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  app.state.project = null;
  assert.equal(await app.setWeb(true), true);
  assert.equal(app.webOn(), true);
  assert.equal(app.state.settings.web, true);
  await app.setWeb(false);
  assert.equal(app.state.settings.web, false);
});

test("another tab's change reaches this tab: its switch shows the stored state, and a press here starts from it", async () => {
  const idb = new IDBFactory();
  const one = await makeApp({ idb });
  const two = await makeApp({ idb, projectId: one.p.id });
  assert.ok(one.app.connectPeers(), "BroadcastChannel is available");
  two.app.connectPeers();
  try {
    const heard = new Promise((resolve) => { const off = two.app.on("web", (v) => { off(); resolve(v); }); });
    await one.app.setWeb(true);
    assert.equal(await heard, true);
    assert.equal(two.app.webOn(), true);
    // the press in tab two toggles from the state tab one stored, so it turns web access off
    await two.app.setWeb(!two.app.webOn());
    assert.equal((await stored(two.app, one.p.id)).web, false);
  } finally {
    one.app.disconnectPeers();
    two.app.disconnectPeers();
  }
});

test("revoking one approval keeps an approval granted after the list was drawn", async () => {
  const { app, p } = await makeApp();
  await app.store.projects.patchApprovals(p.id, { grant: ["a.tool:network"] });
  const router = app.toolRouter;
  await router.approvals.grant(p.id, ["b.tool:network"]); // granted from a call while the project page was open
  await app.revokeApprovals(p.id, ["a.tool:network"]);
  const rec = await app.store.projects.get(p.id);
  assert.deepEqual(rec.approvals, { "b.tool:network": "project" });
  assert.deepEqual(app.state.project.approvals, { "b.tool:network": "project" });
});

// ------------------------------------------------------------------------------------------------- the runner's switch

function fakeRunner(initial, { slowWhen = () => 0, failWhen = () => false } = {}) {
  let held = structuredClone(initial);
  const puts = [];
  return {
    puts,
    held: () => held,
    status: "ready",
    settings: {
      put: async (patch) => {
        puts.push(structuredClone(patch));
        await delay(slowWhen(patch));
        if (failWhen(patch)) throw new Error("HTTP 400: network.enabled must be true or false");
        held = mergeRunnerSettings(held, patch);
        return structuredClone(held);
      },
      get: async () => structuredClone(held),
    },
  };
}

const RUNNER_SETTINGS = { device: "auto", threads: 4, max_jobs: 1, network: { enabled: false, profile: "biomedical-research" }, purpose: "academic", allow_remote: false };

test("the runner's network switch sends only the change, so an older copy never rewrites the runner's other settings", async () => {
  const app = new App();
  // the runner was told `--threads 8` by another tab after this page read its settings
  const runner = fakeRunner({ ...RUNNER_SETTINGS, threads: 8 });
  app.runtimes = { runner, browser: null };
  app.state.runner = { ...app.state.runner, status: "ready", settings: structuredClone(RUNNER_SETTINGS) };
  const next = await app.updateRunnerSettings({ network: { enabled: true } });
  assert.deepEqual(runner.puts, [{ network: { enabled: true } }]);
  assert.equal(next.threads, 8, "the other tab's change stays");
  assert.equal(next.network.enabled, true);
  assert.equal(next.network.profile, "biomedical-research");
  assert.deepEqual(app.state.runner.settings, runner.held());
});

test("quick presses of the runner's switch are sent one at a time and end where the last press says", async () => {
  const app = new App();
  // the first answer is slow: sent together, the second could land first and the first would win
  const runner = fakeRunner(RUNNER_SETTINGS, { slowWhen: (p) => (p.network?.enabled ? 30 : 0) });
  app.runtimes = { runner, browser: null };
  app.state.runner = { ...app.state.runner, status: "ready", settings: structuredClone(RUNNER_SETTINGS) };
  await Promise.all([app.updateRunnerSettings({ network: { enabled: true } }), app.updateRunnerSettings({ network: { enabled: false } })]);
  assert.deepEqual(runner.puts.map((p) => p.network.enabled), [true, false]);
  assert.equal(app.state.runner.settings.network.enabled, false);
  assert.equal(runner.held().network.enabled, false);
});

test("a press while the runner is not connected changes nothing, says so, and redraws the switch", async () => {
  const app = new App();
  const runner = fakeRunner(RUNNER_SETTINGS);
  runner.status = "offline";
  app.runtimes = { runner, browser: null };
  app.state.runner = { ...app.state.runner, status: "offline", settings: structuredClone(RUNNER_SETTINGS) };
  let redraws = 0;
  app.on("runtime", () => redraws++);
  assert.equal(await app.updateRunnerSettings({ network: { enabled: true } }), null);
  assert.equal(runner.puts.length, 0);
  assert.equal(redraws, 1, "the switch that flipped when pressed flips back");
  assert.equal(app.state.runner.settings.network.enabled, false);
  assert.match(toastText(), /Runner 未连接|not connected/);
});

test("a change the runner refuses re-reads what it holds and redraws", async () => {
  const app = new App();
  const runner = fakeRunner(RUNNER_SETTINGS, { failWhen: () => true });
  app.runtimes = { runner, browser: null };
  // this page's copy was out of date: the runner's network is on
  runner.settings.get = async () => ({ ...RUNNER_SETTINGS, network: { enabled: true, profile: "biomedical-research" } });
  app.state.runner = { ...app.state.runner, status: "ready", settings: structuredClone(RUNNER_SETTINGS) };
  let redraws = 0;
  app.on("runtime", () => redraws++);
  assert.equal(await app.updateRunnerSettings({ network: { enabled: false } }), null);
  assert.equal(redraws, 1);
  assert.equal(app.state.runner.settings.network.enabled, true, "what the runner holds, not what this page guessed");
  assert.match(toastText(), /network\.enabled must be true or false/);
});

test("mergeRunnerSettings merges network one level deep, as the runner does, and accepts a bare boolean", () => {
  assert.deepEqual(mergeRunnerSettings(RUNNER_SETTINGS, { network: { enabled: true } }).network, { enabled: true, profile: "biomedical-research" });
  assert.deepEqual(mergeRunnerSettings(RUNNER_SETTINGS, { network: true }).network, { enabled: true, profile: "biomedical-research" });
  assert.equal(mergeRunnerSettings(null, { threads: 2 }).threads, 2);
});

// ------------------------------------------------------------------------------------------------- the store

test("store: concurrent setDefaults writes keep each other's fields", async () => {
  for (const idb of [new IDBFactory(), null]) {
    const s = await openStore({ indexedDB: idb });
    const p = await s.projects.create({ name: "x" });
    await Promise.all([s.projects.setDefaults(p.id, { web: true }), s.projects.setDefaults(p.id, { compute: "runner" }), s.projects.setDefaults(p.id, { provider: "tao" })]);
    const d = (await s.projects.get(p.id)).defaults;
    assert.deepEqual({ web: d.web, compute: d.compute, provider: d.provider }, { web: true, compute: "runner", provider: "tao" }, idb ? "IndexedDB" : "memory");
    await assert.rejects(s.projects.setDefaults("no-such-project", { web: true }), /no project/);
  }
});

test("store: a connection the browser closed is reopened once, and the write lands", async () => {
  const s = await openStore({ indexedDB: new IDBFactory() });
  const p = await s.projects.create({ name: "x" });
  s.backend.db.close(); // Safari drops it in a tab left in the background; clearing site data closes it
  const next = await s.projects.setDefaults(p.id, { web: true });
  assert.equal(next.defaults.web, true);
  assert.equal((await s.projects.get(p.id)).defaults.web, true);
  s.backend.db.close();
  assert.equal((await s.projects.list()).length, 1, "reads reconnect too");
});

test("store: after another tab upgraded the schema it does not reconnect (this tab's code is older; it must reload)", async () => {
  const idb = new IDBFactory();
  const s = await openStore({ indexedDB: idb });
  const p = await s.projects.create({ name: "x" });
  await new Promise((resolve, reject) => {
    const r = idb.open("tcmstudio", s.backend.version + 1);
    r.onupgradeneeded = () => {};
    r.onsuccess = () => { r.result.close(); resolve(); };
    r.onerror = () => reject(r.error);
  });
  assert.equal(s.backend.upgraded, true);
  await assert.rejects(s.projects.setDefaults(p.id, { web: true }), (err) => err?.name === "InvalidStateError");
});

test("store: a record that cannot be stored writes none of the batch", async () => {
  const s = await openStore({ indexedDB: new IDBFactory() });
  const p = await s.projects.create({ name: "x" });
  const conv = { id: "c1", projectId: p.id, title: "t", createdAt: 1, updatedAt: 1, leafId: null };
  await assert.rejects(s.backend.bulk([{ store: "conversations", put: conv }, { store: "messages", put: { id: "m1", conversationId: "c1", fn: () => 1 } }]));
  assert.equal(await s.conversations.get("c1"), null, "the conversation written before the bad record is not kept");
});
