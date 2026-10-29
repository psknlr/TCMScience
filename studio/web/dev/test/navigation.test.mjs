// Navigations that overlap: a project or conversation route waits on the store (App#onRoute), and a page's module is
// loaded the first time it is shown (the shell's mountView). A later navigation that finishes first stays on screen:
// the earlier one is dropped, its route neither set nor shown, and nothing of it painted over the later page.
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
 * `fn` of the store answers `ms` later for the ids `slow` names → a promise that the slow answer has been given and its
 * caller has had the time to go on with it (the store's next steps, as a navigation that is not dropped would take).
 */
function slowDown(obj, fn, slow, ms) {
  const real = obj[fn];
  let given;
  const answered = new Promise((resolve) => { given = resolve; });
  obj[fn] = async (id, ...rest) => {
    const v = await real(id, ...rest);
    if (slow.includes(id)) { await delay(ms); setTimeout(() => delay(50).then(given), 0); }
    return v;
  };
  return answered;
}

test("a project still being read when About is opened does not take the page back: About stays, its route set", async () => {
  const { app, routes } = await makeApp("#/settings/models");
  const slow = await app.store.projects.create({ name: "慢的项目" });
  const answered = slowDown(app.store.projects, "get", [slow.id], 60);
  await app.startRouting();
  try {
    app.navigate({ name: "project", projectId: slow.id });
    await delay(10); // its hashchange has fired: the project route is waiting on the store
    app.navigate({ name: "about" });
    await until(() => routes.includes("about"), "About");
    await answered; // and the project's record has come back since
    assert.equal(location.hash, "#/about");
    assert.equal(app.state.route.name, "about", "the route the address says");
    assert.deepEqual(routes, ["settings", "about"], "the project's route was never shown");
  } finally {
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
  const answered = slow === "its messages" ? slowDown(s.messages, "list", [c1.id], 60) : slowDown(s.conversations, "list", [p1.id], 60);
  const lists = [];
  const threads = [];
  app.on("conversations", (list) => lists.push([...new Set(list.map((c) => c.projectId))]));
  app.on("messages", (list) => threads.push(list.map((m) => m.content)));
  await app.startRouting();
  try {
    app.navigate({ name: "conversation", projectId: p1.id, convId: c1.id });
    await delay(10);
    app.navigate({ name: "conversation", projectId: p2.id, convId: c2.id });
    await until(() => routes.includes(`conversation:${c2.id}`), "the second conversation");
    await answered;
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
    app.router.destroy();
  }
});

test("the shell: a page whose module arrives after a later route was mounted is not mounted over it", async () => {
  const { app } = await makeApp("#/about");
  const mounted = [];
  const destroyed = [];
  const arrived = [];
  // the project page's module is slow to arrive (its first load); About's is at hand
  const page = (name, ms) => async () => {
    await delay(ms);
    setTimeout(() => arrived.push(name), 20); // a moment after the shell had it, as it would mount it at once
    return (_app, main) => {
      const el = document.createElement("div");
      el.className = `page page--${name}`;
      main.replaceChildren(el);
      mounted.push(name);
      return { el, destroy() { destroyed.push(name); } };
    };
  };
  const root = document.createElement("div");
  const { main } = mountShell(app, root, { pages: { home: page("home", 0), project: page("project", 60), about: page("about", 0) } });
  app.state.route = { name: "project", projectId: "p1" };
  app.emit("route", app.state.route);
  await delay(10);
  app.state.route = { name: "about" };
  app.emit("route", app.state.route);
  await until(() => arrived.includes("about") && arrived.includes("project"), "both pages' modules");
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
