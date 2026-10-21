// The service worker (web/sw.js), run in a sandbox with an in-memory Cache Storage and a scripted network: what it
// answers, what it keeps, how it installs one checked version, cleans up, and stands down. The page-side registration
// (core/offline.js) with a fake navigator. (A real browser runs the whole flow in a separate check: offline reload,
// update on request, ?nosw; see studio/docs/BROWSER_COMPUTE.md.)
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import { registerServiceWorker, removeServiceWorker } from "../js/core/offline.js";

const SOURCE = readFileSync(new URL("../sw.js", import.meta.url), "utf8");
const MARKER = "const PRECACHE = null; // filled by the build: {version, index_url, files: [{path, sha256}]}";
const ORIGIN = "https://science.impf.ai";
const INDEX = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const sha = (s) => createHash("sha256").update(s).digest("hex");

class FakeCache {
  constructor() { this.map = new Map(); }
  async match(req) { const r = this.map.get(typeof req === "string" ? new URL(req, ORIGIN).href : req.url); return r ? r.clone() : undefined; }
  async put(req, res) { this.map.set(typeof req === "string" ? new URL(req, ORIGIN).href : req.url, res); }
  async keys() { return [...this.map.keys()]; }
}
class FakeCaches {
  constructor() { this.stores = new Map(); }
  async open(name) { if (!this.stores.has(name)) this.stores.set(name, new FakeCache()); return this.stores.get(name); }
  async has(name) { return this.stores.has(name); }
  async keys() { return [...this.stores.keys()]; }
  async delete(name) { return this.stores.delete(name); }
  async match(req, { cacheName } = {}) {
    const names = cacheName ? [cacheName] : [...this.stores.keys()];
    for (const n of names) { const hit = await this.stores.get(n)?.match(req); if (hit) return hit; }
    return undefined;
  }
}

/** Load sw.js with a manifest of `files` ({path: body}); `net` answers fetches. → the worker's handlers and state. */
function load({ files = {}, served = files, manifest = true, version = "v1", extra = {} } = {}) {
  const handlers = {};
  const caches = new FakeCaches();
  const fetched = [];
  const net = async (input) => {
    const url = typeof input === "string" ? new URL(input, ORIGIN).href : input.url;
    fetched.push(url);
    const path = url.startsWith(ORIGIN) ? new URL(url).pathname : url;
    if (path in served) {
      const r = new Response(served[path], { status: 200, headers: { "content-type": "text/plain", "cross-origin-opener-policy": "same-origin" } });
      // as fetch() gives them: a same-origin response is "basic", another origin's (CORS) "cors"
      Object.defineProperty(r, "type", { value: url.startsWith(ORIGIN) ? "basic" : "cors" });
      return r;
    }
    if (url.startsWith(INDEX) || url.includes("@fontsource")) {
      const r = new Response(`cdn:${url}`, { status: 200 });
      Object.defineProperty(r, "type", { value: "cors" });
      return r;
    }
    return new Response("not found", { status: 404 });
  };
  const precache = { version, index_url: INDEX, files: Object.entries(files).map(([path, body]) => ({ path, sha256: sha(body) })), ...extra };
  const code = manifest ? SOURCE.replace(MARKER, `const PRECACHE = ${JSON.stringify(precache)};`) : SOURCE;
  const self = {
    location: new URL(`${ORIGIN}/sw.js`),
    addEventListener: (type, fn) => { handlers[type] = fn; },
    skipWaiting: () => { self.skipped = true; },
  };
  vm.runInNewContext(code, { self, caches, fetch: net, crypto: webcrypto, Response, Request, URL, Headers, TextEncoder });
  return { handlers, caches, fetched, self };
}

function event(type, props = {}) {
  let waited = Promise.resolve();
  let responded = null;
  return {
    type, ...props,
    waitUntil(p) { waited = p; },
    respondWith(p) { responded = p; },
    get waited() { return waited; },
    get responded() { return responded; },
  };
}

function request(url, { mode = "cors", method = "GET", headers = {} } = {}) {
  return { url: new URL(url, ORIGIN).href, mode, method, headers: new Headers(headers) };
}

async function fetchVia(sw, url, opts) {
  const e = event("fetch", { request: request(url, opts) });
  sw.handlers.fetch(e);
  return e.responded ? await e.responded : null;
}

const SHELL = { "/": "<html>app</html>", "/js/main.js": "main()", "/runtime/boot.json": "{}" };

test("install keeps every shell file, checked against its SHA-256", async () => {
  const sw = load({ files: SHELL });
  const e = event("install");
  sw.handlers.install(e);
  await e.waited;
  const cache = sw.caches.stores.get("tcmstudio-shell-v1");
  assert.deepEqual((await cache.keys()).map((u) => new URL(u).pathname).sort(), ["/", "/js/main.js", "/runtime/boot.json"]);
  const page = await cache.match("/");
  assert.equal(await page.text(), "<html>app</html>");
  assert.equal(page.headers.get("cross-origin-opener-policy"), "same-origin", "the page keeps its isolation headers");
});

test("a file that changed while installing (a deploy mid-install) fails the install instead of caching a mixture", async () => {
  const sw = load({ files: SHELL, served: { ...SHELL, "/js/main.js": "main() // the next build" } });
  const e = event("install");
  sw.handlers.install(e);
  await assert.rejects(e.waited, /changed while this version installed/);
});

test("what it answers: the app's page and shell from the cache; never the relay, the gateway, other pages or non-GETs", async () => {
  const sw = load({ files: SHELL });
  const e = event("install");
  sw.handlers.install(e);
  await e.waited;
  assert.equal(await (await fetchVia(sw, "/", { mode: "navigate" })).text(), "<html>app</html>");
  assert.equal(await (await fetchVia(sw, "/index.html", { mode: "navigate" })).text(), "<html>app</html>");
  assert.equal(await (await fetchVia(sw, "/js/main.js")).text(), "main()");
  assert.equal(await fetchVia(sw, "/test/runtime/index.html", { mode: "navigate" }), null, "another page of the site is not the app");
  assert.equal(await fetchVia(sw, "/v1/chat/completions"), null);
  assert.equal(await fetchVia(sw, "/v1/health"), null);
  assert.equal(await fetchVia(sw, "/api/sources/request"), null);
  assert.equal(await fetchVia(sw, "/js/main.js", { method: "POST" }), null);
  assert.equal(await fetchVia(sw, "/js/main.js", { headers: { range: "bytes=0-3" } }), null);
  assert.equal(await fetchVia(sw, "/runtime/tcms-py.0123456789ab.tar.gz"), null, "the runtime Worker keeps the bundle itself");
  assert.equal(await fetchVia(sw, "https://example.org/x.js"), null);
});

test("Pyodide and the web fonts: fetched once, then kept (their URLs are versioned)", async () => {
  const sw = load({ files: SHELL });
  const url = `${INDEX}pyodide.asm.wasm`;
  assert.equal(await (await fetchVia(sw, url)).text(), `cdn:${url}`);
  assert.equal(await (await fetchVia(sw, url)).text(), `cdn:${url}`);
  assert.equal(sw.fetched.filter((u) => u === url).length, 1, "the second came from the cache");
  const font = "https://cdn.jsdelivr.net/npm/@fontsource-variable/noto-serif-sc@5.3.0/files/noto-serif-sc-116-wght-normal.woff2";
  await fetchVia(sw, font);
  await fetchVia(sw, font);
  assert.equal(sw.fetched.filter((u) => u === font).length, 1);
});

// RDKit.js's wasm as npm ships it (the devDependency): the bytes the chemistry is pinned to
const RDKIT_URL = "https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.wasm";
const RDKIT_BYTES = readFileSync(new URL("../../node_modules/@rdkit/rdkit/dist/RDKit_minimal.wasm", import.meta.url));

test("RDKit.js's wasm: kept by its exact URL once its bytes are the pinned build's; other bytes are passed on, never kept", async () => {
  const sw = load({ files: SHELL, served: { ...SHELL, [RDKIT_URL]: RDKIT_BYTES } });
  for (let i = 0; i < 2; i++) assert.equal(sha(new Uint8Array(await (await fetchVia(sw, RDKIT_URL)).arrayBuffer())), "594ceaed387adc70ea13bfbb2e8eace916f6c782254ed5a6f0f0a0a7a4fe4061");
  assert.equal(sw.fetched.filter((u) => u === RDKIT_URL).length, 1, "the second came from the cache");
  assert.deepEqual([...sw.caches.stores.keys()], ["tcmstudio-cdn-rdkit-594ceaed387adc70"], "under the CDN prefix core/offline.js stands down");
  assert.equal(await fetchVia(sw, `${RDKIT_URL}?v=2`), null, "only the exact URL");
  assert.equal(await fetchVia(sw, "https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.0/dist/RDKit_minimal.wasm"), null, "nor another version");
  // a response whose bytes are not the pinned build (a proxy's page, a truncated body) is served once and not kept
  const bad = load({ files: SHELL, served: { ...SHELL, [RDKIT_URL]: "<html>blocked</html>" } });
  for (let i = 0; i < 2; i++) assert.equal(await (await fetchVia(bad, RDKIT_URL)).text(), "<html>blocked</html>");
  assert.equal(bad.fetched.filter((u) => u === RDKIT_URL).length, 2, "never answered from the cache");
  assert.deepEqual(await (await bad.caches.open("tcmstudio-cdn-rdkit-594ceaed387adc70")).keys(), []);
});

test("RDKit.js's wasm from the mirror the build names (TCMSTUDIO_RDKIT_URL): that URL is kept, held to the build's SHA-256", async () => {
  const body = "wasm bytes of a local mirror";
  const sw = load({ files: SHELL, served: { ...SHELL, "/mirror/RDKit_minimal.wasm": body, [RDKIT_URL]: RDKIT_BYTES }, extra: { rdkit: { url: "/mirror/RDKit_minimal.wasm", sha256: sha(body) } } });
  for (let i = 0; i < 2; i++) assert.equal(await (await fetchVia(sw, "/mirror/RDKit_minimal.wasm")).text(), body);
  assert.equal(sw.fetched.filter((u) => u.endsWith("/mirror/RDKit_minimal.wasm")).length, 1);
  assert.equal(await fetchVia(sw, RDKIT_URL), null, "the build names another URL: the pinned one is the network's");
  // an activation keeps the build's RDKit cache and removes another build's
  await sw.caches.open("tcmstudio-cdn-rdkit-0000000000000000");
  const e = event("activate");
  sw.handlers.activate(e);
  await e.waited;
  assert.deepEqual((await sw.caches.keys()).filter((n) => n.startsWith("tcmstudio-cdn-rdkit-")), [`tcmstudio-cdn-rdkit-${sha(body).slice(0, 16)}`]);
});

// AutoDock Vina's wasm as the site serves it (js/scientific/dock/vendor), named by the build as boot.json's vina
const VINA_PATH = "/js/scientific/dock/vendor/vina-1.2.7.wasm";
const VINA_BYTES = readFileSync(new URL(`..${VINA_PATH}`, import.meta.url));
const VINA = { url: VINA_PATH.slice(1), sha256: sha(VINA_BYTES) };

test("Vina's wasm: not in the shell; kept on first use by its exact URL, only at the pinned SHA-256", async () => {
  const sw = load({ files: SHELL, served: { ...SHELL, [VINA_PATH]: VINA_BYTES }, extra: { vina: VINA } });
  const e = event("install");
  sw.handlers.install(e);
  await e.waited;
  assert.ok(!sw.fetched.some((u) => u.endsWith(VINA_PATH)), "the install does not fetch it");
  for (let i = 0; i < 2; i++) assert.equal(sha(new Uint8Array(await (await fetchVia(sw, VINA_PATH)).arrayBuffer())), VINA.sha256);
  assert.equal(sw.fetched.filter((u) => u.endsWith(VINA_PATH)).length, 1, "the second came from the cache");
  const name = `tcmstudio-cdn-vina-${VINA.sha256.slice(0, 16)}`;
  assert.ok(sw.caches.stores.has(name), "under the prefix core/offline.js stands down");
  assert.equal(await fetchVia(sw, `${VINA_PATH}?v=2`), null, "only the exact URL");
  // other bytes (a stale deploy, an error page) are served once and never kept
  const bad = load({ files: SHELL, served: { ...SHELL, [VINA_PATH]: "<html>not found</html>" }, extra: { vina: VINA } });
  for (let i = 0; i < 2; i++) assert.equal(await (await fetchVia(bad, VINA_PATH)).text(), "<html>not found</html>");
  assert.equal(bad.fetched.filter((u) => u.endsWith(VINA_PATH)).length, 2);
  assert.deepEqual(await (await bad.caches.open(name)).keys(), []);
  // a build that names no Vina: the network answers it, as any file outside the shell
  const none = load({ files: SHELL, served: { ...SHELL, [VINA_PATH]: VINA_BYTES } });
  assert.equal(await fetchVia(none, VINA_PATH), null);
  // an activation keeps this build's Vina cache and removes another build's
  await sw.caches.open("tcmstudio-cdn-vina-0000000000000000");
  const a = event("activate");
  sw.handlers.activate(a);
  await a.waited;
  assert.deepEqual((await sw.caches.keys()).filter((n) => n.startsWith("tcmstudio-cdn-vina-")), [name]);
});

test("activation deletes older versions of its own caches and nothing else", async () => {
  const sw = load({ files: SHELL, version: "v2" });
  for (const n of ["tcmstudio-shell-v1", "tcmstudio-shell-v2", "tcmstudio-runtime-v1", "someone-else"]) await sw.caches.open(n);
  const e = event("activate");
  sw.handlers.activate(e);
  await e.waited;
  assert.deepEqual((await sw.caches.keys()).sort(), ["someone-else", "tcmstudio-runtime-v1", "tcmstudio-shell-v2"]);
});

test("asked to stand down, it answers nothing more, deletes its caches and does not refill them", async () => {
  const sw = load({ files: SHELL });
  const i = event("install");
  sw.handlers.install(i);
  await i.waited;
  await fetchVia(sw, `${INDEX}pyodide.mjs`);
  const m = event("message", { data: { type: "tcmstudio.remove" } });
  sw.handlers.message(m);
  await m.waited;
  assert.deepEqual(await sw.caches.keys(), []);
  assert.equal(await fetchVia(sw, "/js/main.js"), null);
  assert.equal(await fetchVia(sw, `${INDEX}pyodide.mjs`), null);
  assert.deepEqual(await sw.caches.keys(), [], "nothing came back");
});

test("the page's 'reload into the new version' reaches the waiting worker", () => {
  const sw = load({ files: SHELL });
  sw.handlers.message(event("message", { data: { type: "tcmstudio.skip-waiting" } }));
  assert.equal(sw.self.skipped, true);
});

test("unbuilt (no manifest), the worker caches nothing and answers nothing", async () => {
  const sw = load({ manifest: false });
  const i = event("install");
  sw.handlers.install(i);
  await i.waited;
  assert.deepEqual(await sw.caches.keys(), []);
  assert.equal(await fetchVia(sw, "/", { mode: "navigate" }), null);
});

// ------------------------------------------------------------------------------------------------- the page's side

function fakeNav({ controller = null, webdriver = false, waiting = null } = {}) {
  const regListeners = {};
  const swListeners = {};
  const registration = { waiting, installing: null, unregistered: false, addEventListener: (t, f) => { regListeners[t] = f; }, unregister: async function () { this.unregistered = true; return true; } };
  return {
    registration, regListeners, swListeners,
    nav: {
      webdriver,
      serviceWorker: {
        controller,
        registered: null,
        async register(url, opts) { this.registered = { url, opts }; return registration; },
        async getRegistrations() { return [registration]; },
        addEventListener: (t, f) => { swListeners[t] = f; },
      },
    },
  };
}

test("registration: /sw.js at the root, the HTTP cache bypassed for its updates; skipped in automated browsers unless asked", async () => {
  const f = fakeNav();
  const r = await registerServiceWorker({ nav: f.nav, location: { search: "" } });
  assert.equal(r.state, "registered");
  assert.deepEqual(f.nav.serviceWorker.registered, { url: "/sw.js", opts: { scope: "/", updateViaCache: "none" } });
  assert.equal((await registerServiceWorker({ nav: fakeNav({ webdriver: true }).nav, location: { search: "" } })).state, "skipped");
  assert.equal((await registerServiceWorker({ nav: fakeNav({ webdriver: true }).nav, location: { search: "?sw=1" } })).state, "registered");
  assert.equal((await registerServiceWorker({ nav: {}, location: { search: "" } })).state, "unsupported");
});

test("an update is offered only when it would replace a running version, and applied only when asked", async () => {
  const posted = [];
  const waiting = { postMessage: (m) => posted.push(m) };
  // a controller runs this page and a new version waits: offered
  const f = fakeNav({ controller: {}, waiting });
  let apply = null;
  const reloads = [];
  await registerServiceWorker({ nav: f.nav, location: { search: "", reload: () => reloads.push(1) }, onUpdate: (fn) => { apply = fn; } });
  assert.equal(typeof apply, "function");
  assert.deepEqual(posted, [], "nothing switches by itself");
  apply();
  assert.deepEqual(posted, [{ type: "tcmstudio.skip-waiting" }]);
  f.swListeners.controllerchange();
  f.swListeners.controllerchange();
  assert.equal(reloads.length, 1, "one reload");
  // the first install of all: no controller, nothing to replace
  let offered = false;
  await registerServiceWorker({ nav: fakeNav({ controller: null, waiting: { postMessage() {} } }).nav, location: { search: "" }, onUpdate: () => { offered = true; } });
  assert.equal(offered, false);
});

test("?nosw: every registration told to stand down, unregistered, and its caches deleted", async () => {
  const f = fakeNav();
  const told = [];
  f.registration.active = { postMessage: (m) => told.push(m) };
  const caches = new FakeCaches();
  for (const n of ["tcmstudio-shell-v1", "tcmstudio-pyodide-x", "tcmstudio-cdn-v1", "tcmstudio-runtime-v1"]) await caches.open(n);
  const r = await registerServiceWorker({ nav: f.nav, location: { search: "?nosw" }, caches });
  assert.equal(r.state, "removed");
  assert.deepEqual(told, [{ type: "tcmstudio.remove" }]);
  assert.equal(f.registration.unregistered, true);
  assert.deepEqual(await caches.keys(), ["tcmstudio-runtime-v1"], "the runtime Worker's own verified cache is left alone");
  assert.equal((await removeServiceWorker({ nav: { serviceWorker: { getRegistrations: async () => { throw new Error("denied"); } } }, caches })).state, "failed");
});
