// The browser runtime's worker (CONTRACTS §5, docs/V2.md §13): pinned Pyodide running the real psh + bioagent +
// tcmstudio code. The page runs one or more of these (./pool.js): the primary keeps each project's state in IDBFS and
// saves the compiled modules; a secondary runs stateless calls with neither.
//
// Protocol. Requests arrive as JSON strings {id, op, payload} with op init | info | call | device | warm (| debug when
// the page asked for it); replies leave as JSON strings {id, ok, result | error, ms}; while booting, progress leaves
// as {type:"progress", progress, stage, vars, message}. The envelope of a call is the JSON text Python wrote, spliced
// into the reply unparsed, so no number is reformatted on the way. Two structured messages may come before init:
// {type:"interrupt-buffer", buffer} (a SharedArrayBuffer cannot travel as JSON) and {type:"wasm-module", module}
// (Pyodide's WebAssembly, compiled once by the page for every worker).
//
// Accelerators (docs/V2.md §13.4). When a call's reply is a marker {"__accelerate__": {token, kernel, input}}, the
// worker posts {type:"accelerate", id, token, kernel, input} to the page, waits for {type:"accelerated", id, token,
// ok, result | error | cancelled}, and resumes the call in Python with the result (an error resumes with {"error"}:
// Python then computes natively). A cancelled answer ends the call as cancelled without going back into Python.
//
// One operation runs at a time. Python blocks this thread while it runs; a cancel comes through the interrupt buffer
// (KeyboardInterrupt in Python) when the page is cross-origin isolated, otherwise the page terminates the worker.

const STATE_ROOT_DEFAULT = "/persist";
const PYCACHE_ROOT = "/pycache";
const CACHE_NAME = "tcmstudio-runtime-v1";
const BUNDLE_RE = /\/tcms-py\.[0-9a-f]{12}\.tar\.gz$/;
const PYCACHE_RE = /\/runtime\/\.pycache\/[^/]+\.tar$/;
const PERSIST_LOCK = "tcmstudio.persist";
const WASM_RE = /\/pyodide\.asm\.wasm(?:[?#]|$)/;
const MARKER_PREFIX = '{"__accelerate__"';
// A tool may hand more than one loop to the page; more rounds than this is a bug, not a computation.
const MAX_ACCEL_ROUNDS = 8;
const CORPUS_POINTER_TIMEOUT_MS = 15_000;

const BOOT_PY = `
import importlib.util as _ilu
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

# urllib through synchronous XHR (docs/V2.md §12), when this bundle has it: Pyodide has no ssl, so stock urllib
# cannot open an https URL at all.
_BROWSERHTTP = "absent"
if _ilu.find_spec("tcmstudio.browserhttp") is not None:
    try:
        import tcmstudio.browserhttp as _browserhttp
        _browserhttp.install()
        _BROWSERHTTP = "installed"
    except Exception as _exc:  # noqa: BLE001 - reported by tcms_info, never fatal to the boot
        _BROWSERHTTP = f"failed: {type(_exc).__name__}: {_exc}"

import sqlite3  # noqa: F401  PSH's audit chain; part of the standard library from Pyodide 314 on
import tcmstudio.dispatch as _dispatch
from tcmstudio.envelope import runtime_string as _runtime_string, versions as _versions

try:
    from tcmstudio.accel import resume_json as _resume_json
except ImportError:  # a bundle from before accelerators: nothing ever asks to resume
    _resume_json = None


def tcms_call(tool, arguments_json, context_json):
    return _dispatch.call_json(tool, arguments_json, context_json)


def tcms_resume(token, result_json):
    if _resume_json is None:
        return _json.dumps({"ok": False, "tool": "accelerate", "via": "accelerate", "status": "failed",
                            "summary": "此代码包不支持加速计算", "summary_en": "This bundle has no accelerators",
                            "text": "accelerate: failed. Error (runtime_error): no accelerator support",
                            "error": {"type": "runtime_error", "message": "no accelerator support", "hint": ""}})
    return _resume_json(token, result_json)


def tcms_modules():
    return len(_sys.modules)


def _bundle_pycache_dirs():
    return [PYCACHE_ROOT + _p for _p in _json.loads(PY_PATH_JSON) if _os.path.isdir(PYCACHE_ROOT + _p)]


def tcms_pycache_count():
    n = 0
    for _top in _bundle_pycache_dirs():
        for _root, _dirs, _files in _os.walk(_top):
            n += sum(1 for _f in _files if _f.endswith(".pyc"))
    return n


def tcms_pycache_tar():
    # The bundle tree only (docs/V2.md §13.3). numpy, scipy and pandas ship sources, so their modules are compiled
    # into the same prefix; carried in the tar they made it five times larger and every later boot slower.
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for _top in _bundle_pycache_dirs():
            tar.add(_top, arcname=_top[len(PYCACHE_ROOT):].lstrip("/"))
    return buf.getvalue()


def tcms_info():
    return _json.dumps({"runtime": _runtime_string(), "versions": _versions(),
                        "platform": _sys.platform, "browserhttp": _BROWSERHTTP,
                        "python_path": [p for p in _sys.path if p in _json.loads(PY_PATH_JSON)]},
                       ensure_ascii=False)
`;

// Test pages only (the page's debug option): a tool, "debug.accel_probe", that hands one kernel input to the page
// and finishes with whatever comes back, so the accelerator loop can be checked end to end without a real
// accelerated tool. It computes the same sum natively and says whether the two agree.
const DEBUG_PY = `
import json as _json
import tcmstudio.dispatch as _dispatch
from tcmstudio import accel as _accel

_PROBE = "debug.accel_probe"
_PROBE_KERNEL = "np-null-mt/1"
_call_before_probe = _dispatch._call


def _probe_call(name, arguments, ctx, notes, t0, started):
    if name != _PROBE:
        return _call_before_probe(name, arguments, ctx, notes, t0, started)
    args, _problem = _dispatch._parse_arguments(arguments)
    args = args or {}
    data = {"n": int(args.get("n", 1000)), "seed": str(args.get("seed", "probe"))}
    if _accel.available(ctx, _PROBE_KERNEL):
        raise _accel.Accelerate(_PROBE_KERNEL, data)
    got = _accel.result_for(ctx, _PROBE_KERNEL, data)
    native = sum(range(data["n"]))
    value, engine, error = native, "python", None
    if got is not None and "_error" not in got:
        value, engine = got.get("sum"), str(got.get("engine") or "accelerator")
    elif got is not None:
        error = got["_error"]
    result = {"value": value, "native": native, "equal": value == native, "engine": engine, "error": error,
              "accelerated": got is not None}
    return {"ok": True, "tool": name, "via": name, "status": "succeeded", "duration_ms": 0,
            "summary": "accelerator probe", "summary_en": "accelerator probe",
            "text": f"{name} → {name}: succeeded", "result": result, "citations": [],
            "governance": {"kind": "system", "released": None, "refusals": [], "outputs": []},
            "receipt": {"where": ctx.where, "accel": {"engine": engine}}, "job": None, "approval": None, "error": None}


_dispatch._call = _probe_call
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
let wasmModule = null;
let fatal = null;
let bootInfo = null;
let pycache = { key: null, modules: 0, files: 0, restored: false };
const state = {
  debug: false,
  role: "primary",
  stateRoot: STATE_ROOT_DEFAULT,
  persist: { mode: "none", durable: false, error: null },
  packages: new Set(),
  lockNames: null,
  corpusP: Promise.resolve(null),
  moduleShared: false,
};
const accelWaiters = new Map();

let chain = Promise.resolve();

self.onmessage = (ev) => {
  const data = ev.data;
  if (data && typeof data === "object" && data.type === "interrupt-buffer") {
    interrupt = data.buffer instanceof SharedArrayBuffer ? new Uint8Array(data.buffer) : null;
    if (py && interrupt) py.setInterruptBuffer(interrupt);
    return;
  }
  if (data && typeof data === "object" && data.type === "wasm-module") {
    wasmModule = data.module instanceof WebAssembly.Module ? data.module : null;
    return;
  }
  let msg;
  try {
    msg = typeof data === "string" ? JSON.parse(data) : data;
  } catch {
    return; // not ours
  }
  if (msg?.type === "accelerated") {
    // the page's answer to an accelerate request: the call that asked is waiting in the chain, so not through it
    const waiter = accelWaiters.get(String(msg.token));
    if (waiter) {
      accelWaiters.delete(String(msg.token));
      waiter(msg);
    }
    return;
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
        const out = await call(id, payload || {});
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

async function init({ boot, siteUrl, indexUrl, debug: dbg = false, persist = true, role = "primary" }) {
  if (py && api) return bootInfo;
  if (!boot?.pyodide || !boot?.bundle?.sha256 || !boot?.bundle?.path) throw codeError("bad_boot", "boot.json is incomplete (pyodide, bundle.path, bundle.sha256 are required)");
  const t0 = performance.now();
  const timings = {};
  let lap = performance.now();
  const mark = (k) => { const n = performance.now(); timings[k] = Math.round(n - lap); lap = n; };
  state.debug = Boolean(dbg);
  state.role = role === "secondary" ? "secondary" : "primary";
  state.stateRoot = boot.state_root || STATE_ROOT_DEFAULT;
  const site = siteUrl || new URL("../", self.location.href).href;
  // a self-hosted Pyodide is named relative to the site ("pyodide/314.0.7/"): its wheels then load from there too
  const index = withSlash(new URL(indexUrl || boot.pyodide.index_url, site).href);
  const bundleUrl = new URL(boot.bundle.path, site).href;
  state.corpusP = corpusSpec(boot, site);

  // Pyodide and the bundle download at the same time; a bundle that fails its hash stops the boot at once.
  let pyDone = false;
  let bundleFraction = 0;
  const overall = () => 3 + (pyDone ? 52 : 0) + 20 * bundleFraction;
  progress(3, "pyodide", { version: boot.pyodide.version }, `Loading Pyodide ${boot.pyodide.version}`);
  const pyP = (async () => {
    const { loadPyodide } = await import(index + "pyodide.mjs");
    const restore = wasmModule ? serveCompiledModule(wasmModule) : () => {};
    let instance;
    try {
      instance = await loadPyodide({ indexURL: index });
    } finally {
      restore();
    }
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
  pycache.key = new URL(`runtime/.pycache/${boot.bundle.sha256.slice(0, 16)}-pyodide-${py.version}-bundle.tar`, site).href;
  pycache.restored = await restorePycache();
  mark("pycache");
  const ns = py.globals.get("dict")();
  ns.set("PY_PATH_JSON", JSON.stringify(boot.bundle.python_path || [boot.bundle.extract_dir || "/opt/tcms/site"]));
  ns.set("PYCACHE_ROOT", PYCACHE_ROOT);
  py.runPython(BOOT_PY, { globals: ns });
  if (state.debug) py.runPython(DEBUG_PY, { globals: py.globals.get("dict")() });
  api = {
    call: ns.get("tcms_call"), resume: ns.get("tcms_resume"), info: ns.get("tcms_info"), modules: ns.get("tcms_modules"),
    pycacheTar: ns.get("tcms_pycache_tar"), pycacheCount: ns.get("tcms_pycache_count"),
  };
  if (pycache.restored) {
    pycache.modules = api.modules();
    pycache.files = api.pycacheCount();
  }
  mark("import");

  const pyInfo = JSON.parse(api.info());
  bootInfo = {
    ...pyInfo,
    role: state.role,
    pyodide: { version: py.version, index_url: index },
    packages: [...state.packages],
    persist: { ...state.persist, root: state.stateRoot },
    interruptible: Boolean(interrupt),
    cross_origin_isolated: Boolean(self.crossOriginIsolated),
    shared_module: state.moduleShared,
    bundle: { path: boot.bundle.path, sha256: boot.bundle.sha256, bytes: bundle.bytes.byteLength, from_cache: bundle.fromCache },
    pycache: { restored: pycache.restored, saves: state.role === "primary" },
    timings,
    boot_ms: Math.round(performance.now() - t0),
  };
  return bootInfo;
}

/**
 * Hand Pyodide the module the page compiled instead of compiling pyodide.asm.wasm here: its loader asks for it with
 * WebAssembly.instantiateStreaming(fetch(<index>pyodide.asm.wasm)). Anything else goes to the real function. Returns
 * the function that puts the original back (after loadPyodide, so no later instantiation is touched).
 */
function serveCompiledModule(module) {
  const original = WebAssembly.instantiateStreaming;
  WebAssembly.instantiateStreaming = async function instantiateStreaming(source, imports) {
    const res = await source;
    if (!WASM_RE.test(String(res?.url || ""))) return original.call(WebAssembly, res, imports);
    try { await res.body?.cancel?.(); } catch { /* already read or closed */ }
    const instance = await WebAssembly.instantiate(module, imports);
    state.moduleShared = true;
    return { instance, module };
  };
  return () => { WebAssembly.instantiateStreaming = original; };
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

// ------------------------------------------------------------------------------------------------------- corpus

/**
 * context.corpus for every call (docs/V2.md §11.4), from boot.json's "corpus": the published manifest pinned by the
 * build ({base, manifest, sha256}, relative to the site), or, on a page the runner serves, the public pointer
 * ({latest_url}), read once here. A pointer that cannot be read is passed on as is: Python reads it itself and says
 * why the corpus is unavailable.
 */
async function corpusSpec(boot, site) {
  const c = boot?.corpus;
  if (!c || typeof c !== "object") return null;
  if (c.manifest && c.sha256) {
    return { base_url: new URL(c.base || "corpus/", site).href, manifest: String(c.manifest), sha256: String(c.sha256), ...(c.snapshot_id ? { snapshot_id: String(c.snapshot_id) } : {}) };
  }
  if (!c.latest_url) return null;
  const latest = new URL(c.latest_url, site).href;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), CORPUS_POINTER_TIMEOUT_MS);
  try {
    const res = await fetch(latest, { cache: "no-cache", signal: ctl.signal });
    if (res.ok) {
      const doc = await res.json();
      if (doc?.manifest && /^[0-9a-f]{64}$/.test(String(doc.sha256 || ""))) {
        return { base_url: new URL(".", latest).href, manifest: String(doc.manifest), sha256: String(doc.sha256), ...(doc.snapshot_id ? { snapshot_id: String(doc.snapshot_id) } : {}) };
      }
    }
  } catch { /* below */ } finally {
    clearTimeout(timer);
  }
  return { latest_url: latest };
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

/**
 * After an operation that compiled new modules of the bundle, keep them for the next visit (the primary only: a
 * secondary restores the tree and never writes it, so two workers never race to replace it).
 */
async function savePycache() {
  if (!api || !pycache.key || fatal || state.role !== "primary") return;
  let bytes;
  try {
    const n = api.modules();
    if (n <= pycache.modules) return;
    pycache.modules = n;
    const files = api.pycacheCount();
    if (files <= pycache.files) return; // only site-packages modules were new: nothing of the bundle to keep
    pycache.files = files;
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

async function call(id, { tool, arguments: args = {}, context = {}, packages = [], stateful = false }) {
  if (!api) throw codeError("not_ready", "the runtime has not been initialised");
  if (packages.length) await ensurePackages(packages);
  const argsJson = JSON.stringify(args ?? {});
  const corpus = await state.corpusP;
  const run = (durable) => finishAccelerated(id, api.call(String(tool), argsJson, JSON.stringify({
    ...(corpus ? { corpus } : {}), ...context, where: "browser", state_root: state.stateRoot, durable,
  })));
  if (!stateful || state.persist.mode !== "idbfs") {
    return { text: await run(state.persist.durable), persist: { mode: state.persist.mode, durable: state.persist.durable } };
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
    const text = await run(durable);
    try {
      await syncfs(false);
    } catch (err) {
      durable = false;
      error = `sync to IndexedDB failed: ${messageOf(err)}`;
    }
    return { text, persist: { mode: "idbfs", durable, error } };
  });
}

/** A reply that is an accelerator marker goes to the page; the call resumes with its answer, until it is an envelope. */
async function finishAccelerated(id, text) {
  for (let round = 0; typeof text === "string" && text.startsWith(MARKER_PREFIX); round++) {
    if (round >= MAX_ACCEL_ROUNDS) throw codeError("runtime_error", `the tool asked for the accelerator more than ${MAX_ACCEL_ROUNDS} times`);
    const marker = JSON.parse(text).__accelerate__ || {};
    const answer = await accelerate(id, marker);
    if (answer.cancelled) throw codeError("cancelled", "stopped during the accelerated step");
    const body = answer.ok
      ? JSON.stringify(answer.result && typeof answer.result === "object" ? answer.result : {})
      : JSON.stringify({ error: String(answer.error || "the accelerator failed") });
    text = api.resume(String(marker.token || ""), body);
  }
  return text;
}

function accelerate(id, marker) {
  return new Promise((resolve) => {
    accelWaiters.set(String(marker.token || ""), resolve);
    self.postMessage(JSON.stringify({ type: "accelerate", id, token: marker.token, kernel: marker.kernel, input: marker.input ?? null }));
  });
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
  if (action === "pycache") {
    // what the compiled-module tar of this bundle would hold now, and whether this worker saves it
    const proxy = api.pycacheTar();
    let bytes;
    try { bytes = proxy.toJs(); } finally { proxy.destroy?.(); }
    return { role: state.role, saves: state.role === "primary", bytes: bytes.byteLength, files: api.pycacheCount(), key: pycache.key };
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
    python: { device: "cpu", threads: 1, note: "Python tools run on the CPU (single thread per worker, WebAssembly)" },
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
