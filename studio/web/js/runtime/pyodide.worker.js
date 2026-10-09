// The browser runtime's worker (CONTRACTS §5): pinned Pyodide running the real psh + bioagent + tcmstudio code.
//
// Protocol. Requests arrive as JSON strings {id, op, payload} with op init | info | call | device | warm (| debug when
// the page asked for it); replies leave as JSON strings {id, ok, result | error, ms}; while booting, progress leaves
// as {type:"progress", progress, stage, vars, message}. The envelope of a call is the JSON text Python wrote, spliced
// into the reply unparsed, so no number is reformatted on the way. The one structured message is
// {type:"interrupt-buffer", buffer}: a SharedArrayBuffer cannot travel as JSON.
//
// One operation runs at a time. Python blocks this thread while it runs; a cancel comes through the interrupt buffer
// (KeyboardInterrupt in Python) when the page is cross-origin isolated, otherwise the page terminates the worker.

const STATE_ROOT_DEFAULT = "/persist";
import { createSourceTransport } from "./source-gateway.js";
import { SequenceCompute, WEBGPU_TOOLS } from "./webgpu.js";
const PYCACHE_ROOT = "/pycache";
const CACHE_NAME = "tcmstudio-runtime-v1";
const BUNDLE_RE = /\/tcms-py\.[0-9a-f]{12}\.tar\.gz$/;
const PYCACHE_RE = /\/runtime\/\.pycache\/[^/]+\.tar$/;
const PERSIST_LOCK = "tcmstudio.persist";

const BOOT_PY = `
import json as _json
import os as _os
import sys as _sys

# Compiled modules go to a separate tree that is kept in Cache Storage between visits (keyed by the bundle's hash
# and the Pyodide version, so a pyc never outlives the source it was compiled from). The tree's top must exist:
# the import system will not create an absolute prefix itself.
for _p in _json.loads(PY_PATH_JSON):
    _os.makedirs(PYCACHE_ROOT + _p, exist_ok=True)
_sys.pycache_prefix = PYCACHE_ROOT
_sys.dont_write_bytecode = False

for _p in reversed(_json.loads(PY_PATH_JSON)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sqlite3  # noqa: F401  PSH's audit chain; part of the standard library from Pyodide 314 on
import tcmstudio.dispatch as _dispatch
from tcmstudio.envelope import runtime_string as _runtime_string, versions as _versions
from tcmstudio.browser_http import install as _install_source_transport
from tcmstudio.browser_compute import call_json as _browser_compute_call
_install_source_transport(SOURCE_TRANSPORT)


def tcms_call(tool, arguments_json, context_json, acceleration_json="null"):
    return _browser_compute_call(tool, arguments_json, context_json, acceleration_json)


def tcms_modules():
    return len(_sys.modules)


def tcms_pycache_tar():
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(PYCACHE_ROOT, arcname=".")
    return buf.getvalue()


def tcms_info():
    return _json.dumps({"runtime": _runtime_string(), "versions": _versions(),
                        "platform": _sys.platform,
                        "python_path": [p for p in _sys.path if p in _json.loads(PY_PATH_JSON)]},
                       ensure_ascii=False)
`;

// scipy's Matrix Market reader starts a C++ thread pool; WebAssembly here has no threads, and the failed thread start
// can abort the interpreter. One thread is what the browser has anyway.
const SCIPY_PY = `
try:
    import scipy.io._fast_matrix_market as _fmm
    _fmm.PARALLELISM = 1
except Exception:
    pass
`;

let py = null;
let api = null;
let interrupt = null;
let fatal = null;
let bootInfo = null;
let pycache = { key: null, modules: 0, restored: false };
const state = {
  debug: false,
  stateRoot: STATE_ROOT_DEFAULT,
  persist: { mode: "none", durable: false, error: null },
  packages: new Set(),
  lockNames: null,
  boot: null,
  site: null,
  dataAssets: new Set(),
};

let chain = Promise.resolve();
const compute = new SequenceCompute();

self.onmessage = (ev) => {
  const data = ev.data;
  if (data && typeof data === "object" && data.type === "interrupt-buffer") {
    interrupt = data.buffer instanceof SharedArrayBuffer ? new Uint8Array(data.buffer) : null;
    if (py && interrupt) py.setInterruptBuffer(interrupt);
    return;
  }
  let msg;
  try {
    msg = typeof data === "string" ? JSON.parse(data) : data;
  } catch {
    return; // not ours
  }
  if (!msg || msg.id === undefined || msg.id === null) return;
  chain = chain.then(() => handle(msg)).catch(() => {});
};

async function handle({ id, op, payload }) {
  const t0 = performance.now();
  const ms = () => Math.round(performance.now() - t0);
  try {
    if (fatal && op !== "device") throw fatal;
    if (op !== "device") assertAlive();
    switch (op) {
      case "init": return reply(id, ms, JSON.stringify(await init(payload || {})));
      case "info": return reply(id, ms, JSON.stringify(info()));
      case "device": return reply(id, ms, JSON.stringify(await detectDevice()));
      case "warm": {
        reply(id, ms, JSON.stringify(warm()));
        return await savePycache().catch(() => {});
      }
      case "call": {
        const out = await call(payload || {});
        self.postMessage(`{"id":${JSON.stringify(id)},"ok":true,"ms":${ms()},"persist":${JSON.stringify(out.persist)},"result":${out.text}}`);
        // after the reply, so the caller never waits for it
        return await savePycache().catch(() => {});
      }
      case "debug": return reply(id, ms, JSON.stringify(debug(payload || {})));
      default: throw codeError("bad_request", `unknown operation: ${op}`);
    }
  } catch (err) {
    const isFatal = isFatalError(err);
    if (isFatal && !fatal) fatal = err;
    self.postMessage(JSON.stringify({
      id, ok: false, ms: ms(), fatal: isFatal,
      error: { type: isFatal ? "fatal" : err?.code || "runtime_error", message: messageOf(err) },
    }));
  }
}

function reply(id, ms, resultJson) {
  self.postMessage(`{"id":${JSON.stringify(id)},"ok":true,"ms":${ms()},"result":${resultJson}}`);
}

function progress(value, stage, vars = {}, message = "") {
  self.postMessage(JSON.stringify({ type: "progress", progress: Math.max(0, Math.min(100, Math.round(value))), stage, vars, message: message || stage }));
}

// ------------------------------------------------------------------------------------------------------------- boot

async function init({ boot, siteUrl, indexUrl, debug: dbg = false, persist = true }) {
  if (py && api) return bootInfo;
  if (!boot?.pyodide || !boot?.bundle?.sha256 || !boot?.bundle?.path) throw codeError("bad_boot", "boot.json is incomplete (pyodide, bundle.path, bundle.sha256 are required)");
  const t0 = performance.now();
  const timings = {};
  let lap = performance.now();
  const mark = (k) => { const n = performance.now(); timings[k] = Math.round(n - lap); lap = n; };
  state.debug = Boolean(dbg);
  state.stateRoot = boot.state_root || STATE_ROOT_DEFAULT;
  const site = siteUrl || new URL("../", self.location.href).href;
  state.boot = boot;
  state.site = site;
  const index = withSlash(new URL(indexUrl || boot.pyodide.index_url, site).href);
  const bundleUrl = new URL(boot.bundle.path, site).href;

  // Pyodide and the bundle download at the same time; a bundle that fails its hash stops the boot at once.
  let pyDone = false;
  let bundleFraction = 0;
  const overall = () => 3 + (pyDone ? 52 : 0) + 20 * bundleFraction;
  progress(3, "pyodide", { version: boot.pyodide.version }, `Loading Pyodide ${boot.pyodide.version}`);
  const pyP = (async () => {
    const { loadPyodide } = await import(index + "pyodide.mjs");
    const instance = await loadPyodide({ indexURL: index });
    pyDone = true;
    timings.pyodide = Math.round(performance.now() - t0);
    progress(overall(), "pyodide_ready", { version: instance.version });
    return instance;
  })();
  const bundleP = fetchBundle(bundleUrl, boot.bundle, (f) => {
    const before = Math.round(overall());
    bundleFraction = f;
    if (Math.round(overall()) !== before) progress(overall(), "bundle", { mb: (boot.bundle.bytes / 1e6).toFixed(1) });
  }).then((b) => { timings.bundle = Math.round(performance.now() - t0); return b; });
  const [instance, bundle] = await Promise.all([pyP, bundleP]);
  lap = performance.now();
  py = instance;
  if (interrupt) py.setInterruptBuffer(interrupt);

  state.lockNames = lockNames();
  const wanted = (boot.pyodide.packages || []).filter((n) => !state.lockNames || state.lockNames.has(n));
  progress(76, "packages", { names: wanted.join(", ") });
  if (wanted.length) await py.loadPackage(wanted, quietLoad());
  for (const n of wanted) state.packages.add(n);
  mark("packages");

  progress(84, "unpack");
  py.unpackArchive(bundle.bytes, boot.bundle.format || "gztar", { extractDir: boot.bundle.extract_dir || "/opt/tcms/site" });
  mark("unpack");

  progress(87, "persist");
  await mountPersist(persist);
  mark("persist");

  progress(89, "import");
  pycache.key = new URL(`runtime/.pycache/${boot.bundle.sha256.slice(0, 16)}-pyodide-${py.version}.tar`, site).href;
  pycache.restored = await restorePycache();
  mark("pycache");
  const ns = py.globals.get("dict")();
  ns.set("PY_PATH_JSON", JSON.stringify(boot.bundle.python_path || [boot.bundle.extract_dir || "/opt/tcms/site"]));
  ns.set("PYCACHE_ROOT", PYCACHE_ROOT);
  ns.set("SOURCE_TRANSPORT", createSourceTransport(site));
  py.runPython(BOOT_PY, { globals: ns });
  api = { call: ns.get("tcms_call"), info: ns.get("tcms_info"), modules: ns.get("tcms_modules"), pycacheTar: ns.get("tcms_pycache_tar") };
  if (pycache.restored) pycache.modules = api.modules();
  mark("import");

  const pyInfo = JSON.parse(api.info());
  bootInfo = {
    ...pyInfo,
    pyodide: { version: py.version, index_url: index },
    packages: [...state.packages],
    persist: { ...state.persist, root: state.stateRoot },
    interruptible: Boolean(interrupt),
    cross_origin_isolated: Boolean(self.crossOriginIsolated),
    bundle: { path: boot.bundle.path, sha256: boot.bundle.sha256, bytes: bundle.bytes.byteLength, from_cache: bundle.fromCache },
    pycache: { restored: pycache.restored },
    timings,
    boot_ms: Math.round(performance.now() - t0),
  };
  return bootInfo;
}

function quietLoad() {
  return { messageCallback: () => {}, errorCallback: (m) => console.warn(`[tcmstudio runtime] ${m}`) };
}

function lockNames() {
  try {
    const pkgs = py.lockfile?.packages || py._api?.lockfile_packages;
    return pkgs ? new Set(Object.keys(pkgs)) : null;
  } catch {
    return null;
  }
}

async function openCache() {
  try {
    return typeof caches === "undefined" ? null : await caches.open(CACHE_NAME);
  } catch {
    return null; // Cache Storage refused (opaque origin, storage disabled): download every time
  }
}

/** The bundle's bytes, from Cache Storage or the network, never used before its SHA-256 matches boot.json. */
async function fetchBundle(url, meta, onFraction) {
  const cache = await openCache();
  if (cache) {
    try {
      const hit = await cache.match(url);
      if (hit) {
        const bytes = new Uint8Array(await hit.arrayBuffer());
        if ((await sha256Hex(bytes)) === meta.sha256) {
          onFraction(1);
          return { bytes, fromCache: true };
        }
        await cache.delete(url); // damaged or stale copy: fetch it again
      }
    } catch { /* fall through to the network */ }
  }
  let res;
  try {
    res = await fetch(url);
  } catch (err) {
    throw codeError("bundle_network", `could not download ${url}: ${messageOf(err)}`);
  }
  if (!res.ok) throw codeError(res.status === 404 ? "bundle_missing" : "bundle_http", `${url}: HTTP ${res.status}`);
  const bytes = await readAll(res, meta.bytes, onFraction);
  const digest = await sha256Hex(bytes);
  if (digest !== meta.sha256) {
    throw codeError("bundle_hash", `app bundle hash mismatch: boot.json expects ${meta.sha256}, the download is ${digest}; refusing to run it`);
  }
  if (cache) {
    try {
      await cache.put(url, new Response(bytes, { headers: { "content-type": "application/gzip" } }));
      for (const req of await cache.keys()) if (req.url !== url && BUNDLE_RE.test(new URL(req.url).pathname)) await cache.delete(req);
    } catch { /* quota or private mode: still runs, downloads again next time */ }
  }
  return { bytes, fromCache: false };
}

async function readAll(res, expected, onFraction) {
  if (!res.body || !expected) {
    const b = new Uint8Array(await res.arrayBuffer());
    onFraction(1);
    return b;
  }
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.byteLength;
    onFraction(Math.min(1, got / expected));
  }
  const out = new Uint8Array(got);
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.byteLength; }
  onFraction(1);
  return out;
}

// ---------------------------------------------------------------------------------------------- compiled modules

/** Put back the modules compiled on an earlier visit (about a second of the first call), if their copy is intact. */
async function restorePycache() {
  const cache = await openCache();
  if (!cache || !pycache.key) return false;
  try {
    const hit = await cache.match(pycache.key);
    if (!hit) return false;
    const bytes = new Uint8Array(await hit.arrayBuffer());
    if ((await sha256Hex(bytes)) !== hit.headers.get("x-tcmstudio-sha256")) {
      await cache.delete(pycache.key);
      return false;
    }
    py.unpackArchive(bytes, "tar", { extractDir: PYCACHE_ROOT });
    return true;
  } catch {
    return false;
  }
}

/** After an operation that imported new modules, keep the compiled tree for the next visit. */
async function savePycache() {
  if (!api || !pycache.key || fatal) return;
  let bytes;
  try {
    const n = api.modules();
    if (n <= pycache.modules) return;
    pycache.modules = n;
    const proxy = api.pycacheTar();
    try { bytes = proxy.toJs(); } finally { proxy.destroy?.(); }
  } catch (err) {
    // A cancel meant for the next call can land here; hand it on so that call still stops.
    if (/KeyboardInterrupt/.test(messageOf(err)) && interrupt) Atomics.store(interrupt, 0, 2);
    return;
  }
  const cache = await openCache();
  if (!cache) return;
  try {
    const digest = await sha256Hex(bytes);
    await cache.put(pycache.key, new Response(bytes, { headers: { "content-type": "application/x-tar", "x-tcmstudio-sha256": digest } }));
    for (const req of await cache.keys()) if (req.url !== pycache.key && PYCACHE_RE.test(new URL(req.url).pathname)) await cache.delete(req);
  } catch { /* quota: the next visit compiles again */ }
}

// ------------------------------------------------------------------------------------------------------ persistence

/**
 * Governed runs write each project's PSH state under <state root>/<project>/psh. IDBFS keeps it in IndexedDB across
 * reloads; it is synced in before and out after each stateful call. Without IndexedDB the directory stays in memory:
 * the run is still attested, and the receipt says durable:false.
 */
async function mountPersist(wanted) {
  const root = state.stateRoot;
  py.FS.mkdirTree(root);
  if (!wanted) {
    state.persist = { mode: "memory", durable: false, error: "persistence turned off" };
    return;
  }
  let mounted = false;
  try {
    if (typeof indexedDB === "undefined") throw new Error("IndexedDB is not available");
    py.FS.mount(py.FS.filesystems.IDBFS, {}, root);
    mounted = true;
    await syncfs(true);
    // A write that reaches IndexedDB is the proof (private modes can open a database and then refuse writes).
    py.FS.writeFile(`${root}/.tcmstudio`, JSON.stringify({ schema: "tcmstudio.persist/1" }));
    await syncfs(false);
    state.persist = { mode: "idbfs", durable: true, error: null };
  } catch (err) {
    if (mounted) {
      try { py.FS.unmount(root); } catch { /* already gone */ }
      py.FS.mkdirTree(root);
    }
    state.persist = { mode: "memory", durable: false, error: messageOf(err) };
  }
}

function syncfs(populate) {
  return new Promise((resolve, reject) => {
    py.FS.syncfs(populate, (err) => (err ? reject(err) : resolve()));
  });
}

/** Serialise stateful calls across this origin's tabs: each tab has its own copy of the files between syncs. */
function withPersistLock(fn) {
  const locks = self.navigator?.locks;
  return locks?.request ? locks.request(PERSIST_LOCK, { mode: "exclusive" }, fn) : fn();
}

// ------------------------------------------------------------------------------------------------------------ calls

async function call({ tool, arguments: args = {}, context = {}, packages = [], stateful = false, acceleration = "auto" }) {
  if (!api) throw codeError("not_ready", "the runtime has not been initialised");
  await ensureDataAssets(tool, args);
  if (packages.length) await ensurePackages(packages);
  const t0 = performance.now();
  const target = tool === "call_tool" ? args?.tool : tool;
  const nativeArgs = tool === "call_tool" ? args?.arguments || {} : args;
  const prepared = await compute.prepare(target, nativeArgs, {
    preference: acceleration, isCancelled: () => Boolean(interrupt && Atomics.load(interrupt, 0)),
  });
  const argsJson = JSON.stringify(args ?? {});
  const run = (durable) => {
    const text = api.call(String(tool), argsJson, JSON.stringify({
      ...context, where: "browser", state_root: state.stateRoot, durable,
    }), JSON.stringify(prepared));
    if (!WEBGPU_TOOLS.includes(target)) return text;
    const envelope = JSON.parse(text);
    if (envelope.receipt) {
      envelope.receipt.compute ??= { backend: "pyodide", operation: target, precision: "Python float64" };
      envelope.receipt.compute.requested = acceleration;
      if (!prepared && compute.fallbackReason) envelope.receipt.compute.fallback_reason = compute.fallbackReason;
    }
    envelope.duration_ms = Math.round(performance.now() - t0);
    return JSON.stringify(envelope);
  };
  if (!stateful || state.persist.mode !== "idbfs") {
    return { text: run(state.persist.durable), persist: { mode: state.persist.mode, durable: state.persist.durable } };
  }
  return withPersistLock(async () => {
    let durable = true;
    let error = null;
    try {
      await syncfs(true); // another tab may have extended this project's audit chain
    } catch (err) {
      durable = false;
      error = `sync from IndexedDB failed: ${messageOf(err)}`;
    }
    const text = run(durable);
    try {
      await syncfs(false);
    } catch (err) {
      durable = false;
      error = `sync to IndexedDB failed: ${messageOf(err)}`;
    }
    return { text, persist: { mode: "idbfs", durable, error } };
  });
}

async function ensureDataAssets(tool, args) {
  const target = tool === "call_tool" ? String(args?.tool || "") : String(tool);
  const input = tool === "call_tool" ? args?.arguments || {} : args;
  if (["tcm_lookup", "native.tcm_lookup"].includes(target) && input.kind && input.kind !== "formula") return;
  if (!["tcm_formula", "tcm_lookup", "native.tcm_formula", "native.tcm_lookup", "tcmdb.formulas"].includes(target)) return;
  const asset = state.boot?.data_assets?.find((x) => x.key === "repository_formulas");
  if (!asset || state.dataAssets.has(asset.sha256)) return;
  if (!/^[a-f0-9]{64}$/.test(asset.sha256) || !asset.path || asset.bytes > 25 * 1024 * 1024) {
    throw codeError("bad_boot", "The full formula asset manifest is invalid");
  }
  progress(100, "data", { rows: asset.rows, mb: (asset.bytes / 1e6).toFixed(1) });
  const url = new URL(asset.path, state.site);
  if (url.origin !== new URL(state.site).origin) throw codeError("bad_boot", "Formula data must use the same origin");
  const download = await fetchBundle(url.href, asset, () => {});
  py.unpackArchive(download.bytes, asset.format || "gztar", { extractDir: asset.extract_dir || "/opt/tcms/data" });
  if (!py.FS.analyzePath(asset.mount_path).exists) throw codeError("data_missing", "Formula data archive has no expected database");
  py.runPython(`import os as _asset_os\n_asset_os.environ["BIOAGENT_FORMULA_SQLITE"] = ${JSON.stringify(asset.mount_path)}`);
  state.dataAssets.add(asset.sha256);
  progress(100, "ready");
}

async function ensurePackages(names) {
  const missing = names.filter((n) => !state.packages.has(n));
  if (!missing.length) return;
  const unknown = state.lockNames ? missing.filter((n) => !state.lockNames.has(n)) : [];
  if (unknown.length) throw codeError("unavailable", `Pyodide ${py.version} has no package ${unknown.join(", ")}`);
  progress(100, "packages_on_demand", { names: missing.join(", ") });
  try {
    await py.loadPackage(missing, quietLoad());
  } catch (err) {
    throw codeError("unavailable", `could not load ${missing.join(", ")}: ${messageOf(err)}`);
  }
  for (const n of missing) state.packages.add(n);
  if (missing.includes("scipy")) py.runPython(SCIPY_PY);
  progress(100, "ready");
}

function warm() {
  // The first native call builds the kernel's runtime (about half a second); doing it now keeps the first real
  // call quick. Nothing is written: no project, no state.
  const t0 = performance.now();
  const text = api.call("call_tool", JSON.stringify({ tool: "native.gc_content", arguments: { sequence: "ACGT" } }), JSON.stringify({ where: "browser" }));
  const env = JSON.parse(text);
  return { status: env.status, ms: Math.round(performance.now() - t0) };
}

function info() {
  if (!api) return { ready: false };
  return { ...bootInfo, ...JSON.parse(api.info()), packages: [...state.packages], persist: { ...state.persist, root: state.stateRoot } };
}

function debug({ action }) {
  if (!state.debug) throw codeError("bad_request", "debug operations are off");
  if (action === "fatal") {
    // What a real fatal error leaves behind: an interpreter that refuses every further use.
    try { py._api.fatal_error(new Error("fatal error induced by the runtime test page")); } catch { /* reported on next use */ }
    return { induced: "fatal" };
  }
  throw codeError("bad_request", `unknown debug action: ${action}`);
}

// ----------------------------------------------------------------------------------------------------------- device

async function detectDevice() {
  const nav = self.navigator || {};
  const out = {
    cores: nav.hardwareConcurrency ?? null,
    memory_gb: nav.deviceMemory ?? null,
    cross_origin_isolated: Boolean(self.crossOriginIsolated),
    webgpu: { api: "gpu" in nav, adapter: null },
    python: { device: "cpu", threads: 1, note: "Python tools run on the CPU (single thread, WebAssembly)" },
  };
  if (nav.gpu) {
    try {
      const adapter = await Promise.race([
        nav.gpu.requestAdapter({ powerPreference: "high-performance" }),
        new Promise((resolve) => setTimeout(() => resolve(undefined), 3000)),
      ]);
      if (adapter) {
        const i = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : {}) || {};
        const fallback = Boolean(adapter.isFallbackAdapter ?? i.isFallbackAdapter);
        out.webgpu.adapter = {
          vendor: i.vendor || "", architecture: i.architecture || "", description: i.description || "",
          software: fallback || /swiftshader|llvmpipe|lavapipe|software|microsoft basic render/i.test(`${i.vendor} ${i.architecture} ${i.description}`),
          fallback,
        };
      }
    } catch (err) {
      out.webgpu.error = messageOf(err);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------------------- helpers

/** After a fatal error every public Pyodide API throws; a call through a kept proxy must not touch that memory. */
function assertAlive() {
  if (!py) return;
  try {
    void py.runPython;
  } catch (err) {
    fatal = err;
    throw err;
  }
}

function isFatalError(err) {
  return Boolean(err?.pyodide_fatal_error) || /fatally failed|can no longer be used/i.test(messageOf(err));
}

function codeError(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

function messageOf(err) {
  const s = String(err?.message || err || "error");
  // A Python traceback: the last line names the exception.
  return s.length > 2000 ? `…${s.slice(-2000)}` : s;
}

function withSlash(url) {
  return url.endsWith("/") ? url : `${url}/`;
}

async function sha256Hex(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let s = "";
  for (const b of d) s += b.toString(16).padStart(2, "0");
  return s;
}
