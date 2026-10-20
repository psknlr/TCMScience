// TCMScience Studio's service worker: the app and its Python runtime keep working without a network once they have
// loaded here before. A classic script (not a module) for the widest support.
//
// The build replaces the PRECACHE line with the site's manifest: every file of the app shell with its SHA-256, and a
// version that changes whenever any of them does (studio/runner/src/tcmstudio/webbuild.py). Unbuilt (the source tree,
// or a page served by the local runner) PRECACHE stays null and this worker caches nothing.
//
// Rules:
// - One build at a time. The shell (HTML, modules, styles, fonts, the runtime's boot.json and catalog) is served from
//   the cache of the version that installed it, so a page never mixes modules of two builds: the app imports many
//   modules lazily. A new version installs in the background and waits; the page offers to reload into it.
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

// set when the page asked this worker to stand down (core/offline.js removeServiceWorker): it keeps running for the
// pages it already controls until they close, and from now on answers nothing and keeps nothing
let removed = false;

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

self.addEventListener("install", (event) => {
  if (!PRECACHE) return;
  event.waitUntil(precache());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(cleanup());
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
  const response = route(req, url);
  if (response) event.respondWith(response);
});

/** The response for a request this worker answers, or null to let the network answer it as usual. */
function route(req, url) {
  if (req.headers.has("range")) return null; // a part of a file: the network answers ranges
  // by its exact URL, whichever host serves it (a same-origin mirror too)
  if (req.url === rdkitWasm().url) return keepChecked(rdkitCache(), req, rdkitWasm().sha256);
  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith("/v1/") || url.pathname.startsWith("/api/") || url.pathname === "/sw.js") return null;
    if (url.pathname === "/admin" || url.pathname.startsWith("/admin/")) return null;
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
  const keepNames = PRECACHE && !all ? new Set([shellCache(), pyodideCache(), CDN_CACHE, rdkitCache()]) : new Set();
  for (const name of await caches.keys()) {
    const ours = name.startsWith(SHELL_PREFIX) || name.startsWith(PYODIDE_PREFIX) || name === CDN_CACHE || name.startsWith(RDKIT_PREFIX);
    if (ours && !keepNames.has(name)) await caches.delete(name);
  }
}

async function sha256(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let s = "";
  for (const b of d) s += b.toString(16).padStart(2, "0");
  return s;
}
