// TCMScience Studio's service worker: the app and its Python runtime keep working without a network once they have
// loaded here before. A classic script (not a module) for the widest support.
//
// The build replaces the PRECACHE line with the site's manifest: every file of the app shell with its SHA-256, and a
// version that changes whenever any of them does (studio/runner/src/tcmstudio/webbuild.py). Unbuilt (the source tree,
// or a page served by the local runner) PRECACHE stays null and this worker caches nothing.
//
// Rules:
// - One build per page. The shell (HTML, modules, styles, fonts, the runtime's boot.json and catalog) is served from
//   the cache of the version that installed it, so a page never mixes modules of two builds: the app imports many
//   modules lazily. A new version installs in the background and waits; the page offers to reload into it. When one
//   tab does, the new version takes over every page of the site at once, and the others follow (core/offline.js); one
//   that holds unsent work says so and reloads once that is done. Until then it still runs the build it loaded: the
//   new version keeps the old version's shell while a page that runs it (or a worker that page started) is open, and
//   answers that page from it (olderBuild). A page is counted when a version takes it over at activation, under the
//   version that was active until then, which loaded it (unless it was counted already, under an older one).
// - Installing checks every file against its SHA-256: a deploy that lands while files are being fetched fails the
//   install (retried on the next visit) instead of caching a mixture.
// - Never cached: the model relay (/v1/*), the database gateway and the telemetry (/api/*), the admin dashboard
//   (/admin/*, which the Worker serves only to a signed-in administrator), anything that is not a GET.
// - Pyodide comes from versioned URLs (immutable): kept once fetched, under the index the build names. The Python
//   bundle and data files are content-addressed and already kept, hash-checked, by the runtime Worker itself.
// - RDKit.js's wasm (the browser chemistry of the ADMET jobs, js/scientific/chem/rdkit.js) by its exact URL: the one
//   the build names (boot.json's rdkit entry, copied into the manifest as rdkit {url, sha256}), else the pinned
//   jsDelivr URL. Kept once fetched, and only when its bytes have the pinned SHA-256: other bytes are passed on, not
//   kept (the chemistry refuses them itself), so a bad response is never served again from here.
// - AutoDock Vina's wasm (the browser docking, js/scientific/dock/vina.js), a file of this site, by the exact URL the
//   build names (boot.json's vina entry, copied into the manifest as vina {url, sha256}; none without it). The build
//   leaves it out of the shell, so only a visitor who docks downloads it, and it is kept on first use the way RDKit's
//   is: only bytes with the pinned SHA-256, other bytes passed on (the docking refuses them itself). Precaching it
//   would also be correct, the shell checks every file, but would make every install fetch an engine most visitors
//   never start. Its loader, a module, stays in the shell with the other modules of the build.

"use strict";

const PRECACHE = null; // filled by the build: {version, index_url, files: [{path, sha256}]}

const SHELL_PREFIX = "tcmstudio-shell-";
const PYODIDE_PREFIX = "tcmstudio-pyodide-";
const CDN_CACHE = "tcmstudio-cdn-v1";
// fonts the stylesheet loads from jsDelivr, at fixed versions
const CDN_FONTS = /^https:\/\/cdn\.jsdelivr\.net\/npm\/@fontsource(-variable)?\/[^/]+@\d[^/]*\//;
// the RDKit.js build the chemistry is pinned to (js/scientific/chem/rdkit.js RDKIT.wasm), unless the build names another
// URL for the same bytes (a local mirror: TCMSTUDIO_RDKIT_URL)
const RDKIT_WASM = Object.freeze({
  url: "https://cdn.jsdelivr.net/npm/@rdkit/rdkit@2026.9.1/dist/RDKit_minimal.wasm",
  sha256: "594ceaed387adc70ea13bfbb2e8eace916f6c782254ed5a6f0f0a0a7a4fe4061",
});
// under the CDN prefix, so core/offline.js's stand-down removes it with the others
const RDKIT_PREFIX = "tcmstudio-cdn-rdkit-";
// the same, for Vina's wasm (same-origin, but under the prefix that is removed with the others)
const VINA_PREFIX = "tcmstudio-cdn-vina-";

// set when the page asked this worker to stand down (core/offline.js removeServiceWorker): it keeps running for the
// pages it already controls until they close, and from now on answers nothing and keeps nothing
let removed = false;

// The pages (and the workers they started) that run an older build than this worker's: client id -> that build's
// version. Kept in a cache, since a stopped worker forgets its variables, and read once per start. Under the shell
// prefix, so a stand-down removes it with the others.
const OLDER_CACHE = `${SHELL_PREFIX}clients`;
const OLDER_KEY = "/older-clients.json";
let older = null; // Map, once read
let olderP = null;

const shellCache = () => `${SHELL_PREFIX}${PRECACHE.version}`;
const pyodideCache = () => `${PYODIDE_PREFIX}${(PRECACHE.index_url || "").replace(/[^A-Za-z0-9.]+/g, "_")}`;
/** The wasm this build's chemistry loads: {url (absolute), sha256}. */
function rdkitWasm() {
  const r = PRECACHE && PRECACHE.rdkit;
  const sha = r && typeof r.sha256 === "string" && /^[0-9a-f]{64}$/.test(r.sha256) ? r.sha256 : null;
  if (r && typeof r.url === "string" && sha) {
    try { return { url: new URL(r.url, self.location.origin).href, sha256: sha }; } catch { /* the pinned one */ }
  }
  return RDKIT_WASM;
}
const rdkitCache = () => `${RDKIT_PREFIX}${rdkitWasm().sha256.slice(0, 16)}`;
/** The docking engine's wasm this build pins: {url (absolute), sha256}, or null when the build names none. */
function vinaWasm() {
  const v = PRECACHE && PRECACHE.vina;
  if (!v || typeof v.url !== "string" || typeof v.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(v.sha256)) return null;
  try { return { url: new URL(v.url, self.location.origin).href, sha256: v.sha256 }; } catch { return null; }
}
const vinaCache = (vina) => `${VINA_PREFIX}${vina.sha256.slice(0, 16)}`;

self.addEventListener("install", (event) => {
  if (!PRECACHE) return;
  event.waitUntil(precache());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(adoptOlderPages().then(() => cleanup()));
});

self.addEventListener("message", (event) => {
  // the page asked to switch to this version now (the person pressed "reload")
  if (event.data?.type === "tcmstudio.skip-waiting") self.skipWaiting();
  if (event.data?.type === "tcmstudio.remove") {
    removed = true;
    event.waitUntil(cleanup({ all: true }));
  }
});

self.addEventListener("fetch", (event) => {
  if (!PRECACHE || removed) return;
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // a page loads (a reload, a new tab): the pages of older builds that have closed since are forgotten
  if (req.mode === "navigate") event.waitUntil(forgetClosedPages());
  const response = olderBuild(event, req, url) || route(req, url);
  if (response) event.respondWith(response);
});

/** Never answered here: the model relay, the gateway and telemetry, the worker itself, the admin dashboard. */
function passedOn(url) {
  return url.pathname.startsWith("/v1/") || url.pathname.startsWith("/api/") || url.pathname === "/sw.js" ||
    url.pathname === "/admin" || url.pathname.startsWith("/admin/");
}

/** The response for a request this worker answers, or null to let the network answer it as usual. */
function route(req, url) {
  if (req.headers.has("range")) return null; // a part of a file: the network answers ranges
  // by its exact URL, whichever host serves it (a same-origin mirror too)
  if (req.url === rdkitWasm().url) return keepChecked(rdkitCache(), req, rdkitWasm().sha256);
  const vina = vinaWasm();
  if (vina && req.url === vina.url) return keepChecked(vinaCache(vina), req, vina.sha256);
  if (url.origin === self.location.origin) {
    if (passedOn(url)) return null;
    // the app is one page (its routes are in the hash); other pages of the site are not the app
    if (req.mode === "navigate") return url.pathname === "/" || url.pathname === "/index.html" ? fromShell("/", req) : null;
    const path = url.pathname === "/index.html" ? "/" : url.pathname;
    if (SHELL.has(path)) return fromShell(path, req);
    if (PRECACHE.index_url && req.url.startsWith(new URL(PRECACHE.index_url, self.location.origin).href)) return keep(pyodideCache(), req);
    return null;
  }
  if (PRECACHE.index_url && req.url.startsWith(PRECACHE.index_url)) return keep(pyodideCache(), req);
  if (CDN_FONTS.test(req.url)) return keep(CDN_CACHE, req);
  return null;
}

const SHELL = new Set(PRECACHE ? PRECACHE.files.map((f) => f.path) : []);

/**
 * The answer for a request of a page that runs an older build, or of a worker it started: that build's own file while
 * its shell is kept, else what this version answers (route, or the network). Null when the request cannot be one: a
 * navigation (it loads a new page, of this version), another origin, a range, what is never answered here, or a page
 * known to run this version. A worker such a page starts runs its build too (the request's resultingClientId).
 */
function olderBuild(event, req, url) {
  const id = event.clientId;
  if (!id || req.mode === "navigate" || url.origin !== self.location.origin || req.headers.has("range") || passedOn(url)) return null;
  if (older && !older.has(id)) return null;
  return (async () => {
    const map = await olderPages();
    const version = map.get(id);
    if (version && version !== PRECACHE.version && !removed) {
      if (event.resultingClientId && !map.has(event.resultingClientId)) {
        map.set(event.resultingClientId, version);
        await saveOlderPages();
      }
      try {
        const hit = await caches.match(url.pathname === "/index.html" ? "/" : url.pathname, { cacheName: `${SHELL_PREFIX}${version}` });
        if (hit) return hit;
      } catch { /* storage refused: as for any page */ }
    }
    return (await route(req, url)) || fetch(req);
  })();
}

/** The pages that run an older build, read once per start of this worker. */
function olderPages() {
  return (olderP ||= (async () => {
    let entries = null;
    try {
      const hit = await caches.match(OLDER_KEY, { cacheName: OLDER_CACHE });
      if (hit) entries = await hit.json();
    } catch { /* none kept, or unreadable: no page is known to run an older build */ }
    older = new Map(Object.entries(entries && typeof entries === "object" ? entries : {}).filter(([, v]) => typeof v === "string"));
    return older;
  })());
}

async function saveOlderPages() {
  try {
    if (!older.size) await caches.delete(OLDER_CACHE);
    else await (await caches.open(OLDER_CACHE)).put(OLDER_KEY, new Response(JSON.stringify(Object.fromEntries(older)), { headers: { "content-type": "application/json" } }));
  } catch { /* quota: those pages get this version's files, as before */ }
}

/**
 * At activation: every page this version now controls was loaded under an older one. A page counted already keeps
 * its build; any other ran the version that was active until now: the one shell cache besides this version's that no
 * counted page holds (activation and forgetClosedPages delete every other), or none if that is not a single one. Then
 * the pages that have closed are forgotten, and cleanup() deletes the shells no open page runs.
 */
async function adoptOlderPages() {
  if (!PRECACHE || !self.clients?.matchAll) return;
  const map = await olderPages();
  const held = new Set(map.values());
  const shells = (await caches.keys()).filter((n) => n.startsWith(SHELL_PREFIX) && n !== OLDER_CACHE && n !== shellCache())
    .map((n) => n.slice(SHELL_PREFIX.length)).filter((v) => !held.has(v));
  const previous = shells.length === 1 ? shells[0] : null;
  for (const c of await self.clients.matchAll({ type: "all" })) if (previous && !map.has(c.id)) map.set(c.id, previous);
  await closedSince(map);
  await saveOlderPages();
}

/** Drop the pages that have closed from `map`. → the older builds no open page runs any more. */
async function closedSince(map) {
  const before = new Set(map.values());
  for (const id of [...map.keys()]) if (!(await self.clients.get(id))) map.delete(id);
  const after = new Set(map.values());
  return [...before].filter((v) => !after.has(v));
}

/** Forget the pages of older builds that have closed, and delete the shells no open page runs any more. */
async function forgetClosedPages() {
  if (!self.clients?.get) return;
  const map = await olderPages();
  if (!map.size) return;
  const size = map.size;
  const gone = await closedSince(map);
  if (map.size === size) return;
  await saveOlderPages();
  for (const v of gone) {
    if (v === PRECACHE.version) continue;
    try { await caches.delete(`${SHELL_PREFIX}${v}`); } catch { /* deleted at the next activation */ }
  }
}

async function fromShell(path, req) {
  try {
    // match with a cache name never creates the cache (caches.open would bring a deleted one back)
    const hit = await caches.match(path, { cacheName: shellCache() });
    if (hit) return hit;
  } catch { /* storage refused: the network answers */ }
  return fetch(req);
}

/** Cache-first for immutable, versioned files: fetched once, kept. */
async function keep(name, req) {
  try {
    const hit = await caches.match(req, { cacheName: name, ignoreVary: true });
    if (hit) return hit;
  } catch { /* storage refused: the network answers */ }
  const res = await fetch(req);
  if (!removed && res.ok && (res.type === "basic" || res.type === "cors")) {
    try { await (await caches.open(name)).put(req, res.clone()); } catch { /* quota: served, not kept */ }
  }
  return res;
}

/**
 * Cache-first for a pinned file: fetched once and kept only when its bytes have the pinned SHA-256 (kept under its exact
 * URL, the bytes checked, with the headers they came with); a response with other bytes is answered, never kept.
 */
async function keepChecked(name, req, sha) {
  try {
    const hit = await caches.match(req.url, { cacheName: name, ignoreVary: true });
    if (hit) return hit;
  } catch { /* storage refused: the network answers */ }
  const res = await fetch(req);
  if (!removed && res.ok && (res.type === "basic" || res.type === "cors")) {
    try {
      const bytes = await res.clone().arrayBuffer();
      if ((await sha256(bytes)) === sha) await (await caches.open(name)).put(req.url, new Response(bytes, { status: 200, statusText: "OK", headers: res.headers }));
    } catch { /* quota, or a body that could not be read: served, not kept */ }
  }
  return res;
}

async function precache() {
  const cache = await caches.open(shellCache());
  for (const f of PRECACHE.files) {
    const res = await fetch(f.path, { cache: "no-cache", credentials: "same-origin" });
    if (!res.ok) throw new Error(`${f.path}: HTTP ${res.status}`);
    const bytes = await res.arrayBuffer();
    const digest = await sha256(bytes);
    if (digest !== f.sha256) throw new Error(`${f.path} changed while this version installed (expected ${f.sha256.slice(0, 12)}…, got ${digest.slice(0, 12)}…)`);
    // exactly the bytes checked, with the headers they came with (COOP/COEP keep the page isolated), and never a
    // redirected response, which cannot answer a navigation
    await cache.put(f.path, new Response(bytes, { status: 200, statusText: "OK", headers: res.headers }));
  }
}

async function cleanup({ all = false } = {}) {
  const vina = vinaWasm();
  // the shells of older builds that open pages still run
  const held = all || !older ? [] : [OLDER_CACHE, ...[...older.values()].map((v) => `${SHELL_PREFIX}${v}`)];
  const keepNames = PRECACHE && !all ? new Set([shellCache(), pyodideCache(), CDN_CACHE, rdkitCache(), ...(vina ? [vinaCache(vina)] : []), ...held]) : new Set();
  for (const name of await caches.keys()) {
    const ours = name.startsWith(SHELL_PREFIX) || name.startsWith(PYODIDE_PREFIX) || name === CDN_CACHE || name.startsWith(RDKIT_PREFIX) ||
      name.startsWith(VINA_PREFIX);
    if (ours && !keepNames.has(name)) await caches.delete(name);
  }
}

async function sha256(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let s = "";
  for (const b of d) s += b.toString(16).padStart(2, "0");
  return s;
}
