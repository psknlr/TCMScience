// Navigations that overlap: a project or conversation route waits on the store (App#onRoute), and a page's module is
// loaded the first time it is shown (the shell's mountView). A later navigation that finishes first stays on screen:
// the earlier one is dropped, its route neither set nor shown, and nothing of it painted over the later page.
// The overlap is made, not timed: the earlier navigation's store answer (or page module) is held until the test lets
// it go, which it does only once the later route is shown; the test then waits until every navigation has finished.
// No step waits a fixed time, so a slow or loaded machine changes how long a test takes, not what it sees.
import { window } from "./setup.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import { IDBFactory } from "fake-indexeddb";
import { openStore } from "../../js/core/store.js";
import { App } from "../../js/ui/app.js";
import { mountShell } from "../../js/ui/shell.js";
import { registerUiStrings } from "../../js/ui/strings.js";

registerUiStrings();

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

// The address bar: location.hash and history.replaceState; a new hash fires hashchange a task later, as a browser does.
const address = { hash: "" };
globalThis.location = {
  pathname: "/", search: "",
  get hash() { return address.hash; },
  set hash(v) {
    const next = String(v).startsWith("#") ? String(v) : `#${v}`;
    if (next === address.hash) return;
    address.hash = next;
    setTimeout(() => window.dispatchEvent(new window.Event("hashchange")), 0);
  },
};
globalThis.history = {
  state: null,
  replaceState(state, _title, url) { this.state = state; const i = String(url).indexOf("#"); address.hash = i < 0 ? "" : String(url).slice(i); },
};

/** An App with a store (fake IndexedDB), routing from `hash`; `routes` collects every route it shows. */
async function makeApp(hash = "#/about") {
  const app = new App();
  app.store = await openStore({ indexedDB: new IDBFactory() });
  address.hash = hash;
  const routes = [];
  app.on("route", (r) => routes.push(r.name === "conversation" ? `conversation:${r.convId}` : r.name));
  return { app, routes };
}

/** Wait until `fn()` is true (polled), at most `ms`. */
async function until(fn, what, ms = 10000) {
  const end = Date.now() + ms;
  while (!fn()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await delay(5);
  }
}

/**
 * A gate: `wait()` (a promise) is held until `open()`; `waiting()` says whether anything has come to wait at it yet.
 */
function gate() {
  let open;
  let waiting = false;
  const opened = new Promise((resolve) => { open = resolve; });
  return { wait() { waiting = true; return opened; }, open: () => open(), waiting: () => waiting };
}

/**
 * The store's `fn`, for the ids `held` names, reads its answer and then holds it at a gate until the test opens it
 * (the store is slow for that one read) → the gate: `waiting()` once its caller is waiting on that read.
 */
function holdBack(obj, fn, held) {
  const real = obj[fn];
  const g = gate();
  obj[fn] = async (id, ...rest) => {
    const v = await real(id, ...rest);
    if (held.includes(id)) await g.wait();
    return v;
  };
  return g;
}

/**
 * Every navigation the router hands the app (App#onRoute, through HashRouter#onChange) → `settled(what)`: waits until
 * each one has finished or stopped (a dropped navigation stops at its next step), and throws an error one ended with.
 */
function trackNavigations(app) {
  const runs = [];
  const onChange = app.router.onChange;
  app.router.onChange = (route, prev) => {
    const run = { done: false, error: null };
    runs.push(run);
    const p = Promise.resolve(onChange(route, prev));
    p.then(() => { run.done = true; }, (e) => { run.done = true; run.error = e; });
    return p;
  };
  return {
    async settled(what) {
      await until(() => runs.every((r) => r.done), what);
      const failed = runs.find((r) => r.error);
      if (failed) throw failed.error;
    },
  };
}

test("a project still being read when About is opened does not take the page back: About stays, its route set", async () => {
  const { app, routes } = await makeApp("#/settings/models");
  const slow = await app.store.projects.create({ name: "慢的项目" });
  const read = holdBack(app.store.projects, "get", [slow.id]);
  await app.startRouting();
  const navigations = trackNavigations(app);
  try {
    app.navigate({ name: "project", projectId: slow.id });
    await until(read.waiting, "the project route to wait on the store");
    app.navigate({ name: "about" });
    await until(() => routes.includes("about"), "About");
    read.open(); // the project's record comes back only now, after About was shown
    await navigations.settled("both navigations to finish");
    assert.equal(location.hash, "#/about");
    assert.equal(app.state.route.name, "about", "the route the address says");
    assert.deepEqual(routes, ["settings", "about"], "the project's route was never shown");
  } finally {
    read.open();
    app.router.destroy();
  }
});

for (const slow of ["its project's conversations", "its messages"]) test(`a conversation still loading (${slow}) when another project's conversation is opened: the later one's project, list, thread and route`, async () => {
  const { app, routes } = await makeApp("#/about");
  const s = app.store;
  const [p1, p2] = [await s.projects.create({ name: "甲" }), await s.projects.create({ name: "乙" })];
  const [c1, c2] = [await s.conversations.create({ projectId: p1.id, title: "一" }), await s.conversations.create({ projectId: p2.id, title: "二" })];
  await s.messages.put({ conversationId: c1.id, role: "user", content: "甲的问题" });
  await s.messages.put({ conversationId: c2.id, role: "user", content: "乙的问题" });
  const read = slow === "its messages" ? holdBack(s.messages, "list", [c1.id]) : holdBack(s.conversations, "list", [p1.id]);
  const lists = [];
  const threads = [];
  app.on("conversations", (list) => lists.push([...new Set(list.map((c) => c.projectId))]));
  app.on("messages", (list) => threads.push(list.map((m) => m.content)));
  await app.startRouting();
  const navigations = trackNavigations(app);
  try {
    app.navigate({ name: "conversation", projectId: p1.id, convId: c1.id });
    await until(read.waiting, `the first conversation to wait on the store (${slow})`);
    app.navigate({ name: "conversation", projectId: p2.id, convId: c2.id });
    await until(() => routes.includes(`conversation:${c2.id}`), "the second conversation");
    read.open(); // the first conversation's read comes back only now, after the second was shown
    await navigations.settled("both navigations to finish");
    assert.equal(app.state.route.convId, c2.id);
    assert.equal(app.state.project.id, p2.id);
    assert.equal(app.state.conversation.id, c2.id);
    assert.deepEqual(app.state.conversations.map((c) => c.id), [c2.id], "the list is the open project's");
    assert.deepEqual(app.state.messages.map((m) => m.content), ["乙的问题"], "the thread is the open conversation's");
    assert.deepEqual(routes, ["about", `conversation:${c2.id}`]);
    // nothing of the first conversation reached the views after the second was opened
    assert.deepEqual(lists.at(-1), [p2.id]);
    assert.ok(!lists.slice(lists.findIndex((l) => l[0] === p2.id)).some((l) => l.includes(p1.id)), JSON.stringify(lists));
    assert.deepEqual(threads.at(-1), ["乙的问题"]);
    assert.ok(!threads.slice(threads.findIndex((m) => m[0] === "乙的问题")).some((m) => m.includes("甲的问题")), JSON.stringify(threads));
  } finally {
    read.open();
    app.router.destroy();
  }
});

test("the shell: a page whose module arrives after a later route was mounted is not mounted over it", async () => {
  const { app } = await makeApp("#/about");
  const mounted = [];
  const destroyed = [];
  // the project page's module is slow to arrive (its first load): it comes when the test opens its gate; About's is
  // at hand
  const projectModule = gate();
  let projectArrived = false;
  const page = (name) => async () => {
    if (name === "project") { await projectModule.wait(); projectArrived = true; }
    return (_app, main) => {
      const el = document.createElement("div");
      el.className = `page page--${name}`;
      main.replaceChildren(el);
      mounted.push(name);
      return { el, destroy() { destroyed.push(name); } };
    };
  };
  const root = document.createElement("div");
  const pages = { home: page("home"), project: page("project"), about: page("about") };
  const { main } = mountShell(app, root, { pages });
  app.state.route = { name: "project", projectId: "p1" };
  app.emit("route", app.state.route);
  await until(projectModule.waiting, "the shell to ask for the project page's module");
  app.state.route = { name: "about" };
  app.emit("route", app.state.route);
  await until(() => mounted.includes("about"), "About");
  projectModule.open(); // the project page's module arrives only now, after About was mounted
  await until(() => projectArrived, "the project page's module");
  // what the shell does with a module that arrived it does at once (no await between): one turn of the event loop
  // later it has done it
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(mounted, ["about"], "the project page was dropped, not mounted after About");
  assert.deepEqual(destroyed, []);
  assert.equal(main.children.length, 1);
  assert.equal(main.firstElementChild.className, "page page--about");
  assert.equal(main.firstElementChild.getAttribute("data-route-href"), "#/about", "the page says the route it was mounted for");
  assert.equal(root.dataset.route, "about");
  // the next route replaces it, and the page it replaces is destroyed
  app.state.route = { name: "project", projectId: "p1" };
  app.emit("route", app.state.route);
  await until(() => mounted.length === 2, "the project page");
  assert.deepEqual([mounted, destroyed], [["about", "project"], ["about"]]);
  assert.equal(main.firstElementChild.getAttribute("data-route-href"), "#/p/p1");
});
