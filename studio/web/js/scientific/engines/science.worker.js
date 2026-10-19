// A job worker (studio/docs/BROWSER_JOBS.md): pinned Pyodide running the same bundle as the tool runtime
// (../../runtime/pyodide.worker.js boots through the same ../../runtime/pyboot.js), for the stages of browser jobs.
// The pool (../runtime/worker-pool.js) runs several of these side by side, one per CPU core it can spare.
//
// Protocol. Requests arrive as JSON strings {id, op, payload}; replies leave as JSON strings {id, ok, result | error,
// ms}; while an operation runs it may send {type:"event", id, event:{kind, ...}} (Python's progress and log lines).
// The one structured message is {type:"interrupt-buffer", buffer}: a cancel lands in Python as KeyboardInterrupt.
//
// Files. Python works in this worker's memory (Emscripten's MEMFS). Large inputs are not copied into it: `mount`
// makes read-only files whose reads go straight to the Origin Private File System through synchronous access
// handles, so a 90 MB source file costs no memory until Python reads a block of it. What a stage writes is copied
// out to OPFS with `persist`, which returns each file's SHA-256 as this worker read it; the job service re-reads
// the same files from OPFS and compares, so a receipt's digests are two independent readings.

import {
  PYCACHE_ROOT, codeError, fetchBundle, isFatalError, lockNames, messageOf, pycacheKey, quietLoad, restorePycache,
  sha256Hex, withSlash,
} from "../../runtime/pyboot.js";

const JOB_PY = `
import json as _json
import os as _os
import sys as _sys

for _p in _json.loads(PY_PATH_JSON):
    _os.makedirs(PYCACHE_ROOT + _p, exist_ok=True)
_sys.pycache_prefix = PYCACHE_ROOT
_sys.dont_write_bytecode = False
for _p in reversed(_json.loads(PY_PATH_JSON)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sqlite3  # noqa: F401  PSH's audit chain
from tcmstudio.envelope import runtime_string as _runtime_string, versions as _versions


def tcms_job(fn, args_json, emit):
    from tcmstudio.browser_jobs import call as _call

    def _emit(kind, payload):
        emit(kind, _json.dumps(payload, ensure_ascii=False, default=str))

    try:
        return _call(fn, args_json, _emit)
    except KeyboardInterrupt:
        return _json.dumps({"ok": False, "error": {"type": "cancelled", "message": "cancelled"}})


def tcms_info():
    import platform
    return _json.dumps({"runtime": _runtime_string(), "versions": _versions(), "platform": _sys.platform,
                        "machine": platform.machine(), "python": platform.python_version()},
                       ensure_ascii=False)
`;

// scipy's Matrix Market reader starts a C++ thread pool; WebAssembly here has no threads (see pyodide.worker.js)
const SCIPY_PY = `
try:
    import scipy.io._fast_matrix_market as _fmm
    _fmm.PARALLELISM = 1
except Exception:
    pass
`;

/** Without synchronous access handles a mounted file is copied into memory, up to this much in all. */
const COPY_BUDGET_BYTES = 768 * 1024 * 1024;
const EROFS = 69; // Emscripten's errno for a write to a read-only file system
const EINVAL = 28;

let py = null;
let api = null;
let interrupt = null;
let fatal = null;
let bootInfo = null;
const packages = new Set();
let locked = null; // the lockfile's package names
let debugOps = false; // the test pages' python op (init {debug: true}); never on in Studio
const mounts = new Map(); // MEMFS root → {handles, files, bytes, mode}
let chain = Promise.resolve();

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

async function handle({ id, op, payload = {} }) {
  const t0 = performance.now();
  const ms = () => Math.round(performance.now() - t0);
  try {
    if (fatal) throw fatal;
    if (interrupt) Atomics.store(interrupt, 0, 0);
    switch (op) {
      case "init": return reply(id, ms, JSON.stringify(await init(payload)));
      case "packages": return reply(id, ms, JSON.stringify(await ensurePackages(payload.names || [])));
      case "mount": return reply(id, ms, JSON.stringify(await mount(payload)));
      case "unmount": return reply(id, ms, JSON.stringify(unmount(payload)));
      case "restore": return reply(id, ms, JSON.stringify(await restore(payload)));
      case "persist": return reply(id, ms, JSON.stringify(await persist(payload)));
      case "run": return reply(id, ms, run(id, payload)); // Python's JSON text, unparsed
      case "free": return reply(id, ms, JSON.stringify(free(payload)));
      case "memory": return reply(id, ms, JSON.stringify(memory()));
      case "python": return reply(id, ms, JSON.stringify(python(payload)));
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

function event(id, ev) {
  self.postMessage(JSON.stringify({ type: "event", id, event: ev }));
}

function cancelled() {
  return Boolean(interrupt && Atomics.load(interrupt, 0));
}

function throwIfCancelled() {
  if (cancelled()) throw codeError("cancelled", "cancelled");
}

// ------------------------------------------------------------------------------------------------------------- boot

async function init({ boot, siteUrl, indexUrl, debug = false }) {
  debugOps = Boolean(debug);
  if (py && api) return bootInfo;
  if (!boot?.pyodide || !boot?.bundle?.sha256 || !boot?.bundle?.path) throw codeError("bad_boot", "boot.json is incomplete (pyodide, bundle.path, bundle.sha256 are required)");
  const t0 = performance.now();
  const site = siteUrl || new URL("../../../", self.location.href).href;
  const index = withSlash(new URL(indexUrl || boot.pyodide.index_url, site).href);
  const [instance, bundle] = await Promise.all([
    import(index + "pyodide.mjs").then(({ loadPyodide }) => loadPyodide({ indexURL: index })),
    fetchBundle(new URL(boot.bundle.path, site).href, boot.bundle, () => {}),
  ]);
  py = instance;
  if (interrupt) py.setInterruptBuffer(interrupt);
  locked = lockNames(py);
  const wanted = (boot.pyodide.packages || []).filter((n) => !locked || locked.has(n));
  if (wanted.length) await py.loadPackage(wanted, quietLoad());
  for (const n of wanted) packages.add(n);
  py.unpackArchive(bundle.bytes, boot.bundle.format || "gztar", { extractDir: boot.bundle.extract_dir || "/opt/tcms/site" });
  const restored = await restorePycache(py, pycacheKey(site, boot.bundle.sha256, py.version));
  const ns = py.globals.get("dict")();
  ns.set("PY_PATH_JSON", JSON.stringify(boot.bundle.python_path || [boot.bundle.extract_dir || "/opt/tcms/site"]));
  ns.set("PYCACHE_ROOT", PYCACHE_ROOT);
  py.runPython(JOB_PY, { globals: ns });
  api = { job: ns.get("tcms_job"), info: ns.get("tcms_info") };
  bootInfo = {
    ...JSON.parse(api.info()),
    pyodide: { version: py.version, index_url: index },
    packages: [...packages],
    interruptible: Boolean(interrupt),
    cross_origin_isolated: Boolean(self.crossOriginIsolated),
    bundle: { path: boot.bundle.path, sha256: boot.bundle.sha256, from_cache: bundle.fromCache },
    pycache: { restored },
    sync_access: syncAccessAvailable(),
    boot_ms: Math.round(performance.now() - t0),
  };
  return bootInfo;
}

async function ensurePackages(names) {
  if (!py) throw codeError("not_ready", "the job worker has not been initialised");
  const missing = names.filter((n) => !packages.has(n));
  if (missing.length) {
    const unknown = locked ? missing.filter((n) => !locked.has(n)) : [];
    if (unknown.length) throw codeError("unavailable", `Pyodide ${py.version} has no package ${unknown.join(", ")}`);
    try {
      await py.loadPackage(missing, quietLoad());
    } catch (err) {
      throw codeError("unavailable", `could not load ${missing.join(", ")}: ${messageOf(err)}`);
    }
    for (const n of missing) packages.add(n);
    if (missing.includes("scipy")) py.runPython(SCIPY_PY);
  }
  return { packages: [...packages] };
}

// ------------------------------------------------------------------------------------------------------------ files

function syncAccessAvailable() {
  return typeof FileSystemFileHandle !== "undefined" && "createSyncAccessHandle" in FileSystemFileHandle.prototype;
}

async function opfsDir(segments, { create = false } = {}) {
  if (!self.navigator?.storage?.getDirectory) throw codeError("storage", "this browser has no Origin Private File System");
  let dir = await navigator.storage.getDirectory();
  for (const seg of segments) {
    checkSegment(seg);
    dir = await dir.getDirectoryHandle(seg, { create });
  }
  return dir;
}

function checkSegment(seg) {
  if (typeof seg !== "string" || !seg || seg === "." || seg === ".." || seg.includes("/") || seg.includes("\\")) {
    throw codeError("bad_request", `bad path segment ${JSON.stringify(seg)}`);
  }
}

function memfsPath(p) {
  if (typeof p !== "string" || !p.startsWith("/") || p.split("/").includes("..")) throw codeError("bad_request", `bad path ${JSON.stringify(p)}`);
  return p.replace(/\/+$/, "") || "/";
}

/**
 * Read-only files at `root`/<name>, each read straight from OPFS `dir`/<name>. With synchronous access handles (every
 * browser with OPFS, inside a worker) nothing is copied; without them each file is copied into memory, within
 * COPY_BUDGET_BYTES in all, and a mount that would exceed it is refused rather than half made.
 */
async function mount({ root, dir, files }) {
  root = memfsPath(root);
  if (mounts.has(root)) unmount({ root });
  const folder = await opfsDir(dir);
  const handles = [];
  const listed = [];
  let total = 0;
  const sync = syncAccessAvailable();
  py.FS.mkdirTree(root);
  try {
    if (!sync) {
      for (const name of files) {
        checkSegment(name);
        total += (await (await folder.getFileHandle(name)).getFile()).size;
      }
      if (total > COPY_BUDGET_BYTES) {
        throw codeError("storage", `this browser cannot read files in place (no synchronous access handles) and copying ${(total / 1e6).toFixed(0)} MB into memory exceeds the ${COPY_BUDGET_BYTES / 1e6} MB budget`);
      }
      total = 0;
    }
    for (const name of files) {
      checkSegment(name);
      throwIfCancelled();
      const fh = await folder.getFileHandle(name);
      if (sync) {
        const handle = await fh.createSyncAccessHandle();
        handles.push(handle);
        const size = handle.getSize();
        readOnlyFile(root, name, size, handle);
        total += size;
        listed.push({ name, bytes: size });
      } else {
        const bytes = new Uint8Array(await (await fh.getFile()).arrayBuffer());
        py.FS.writeFile(`${root}/${name}`, bytes, { canOwn: true });
        py.FS.chmod(`${root}/${name}`, 0o444);
        total += bytes.byteLength;
        listed.push({ name, bytes: bytes.byteLength });
      }
    }
  } catch (err) {
    for (const h of handles) { try { h.close(); } catch { /* already closed */ } }
    try { rmTree(root); } catch { /* nothing made */ }
    if (err?.name === "NoModificationAllowedError") throw codeError("storage", `a file under ${dir.join("/")} is open elsewhere (another tab or job is writing it)`);
    if (err?.name === "NotFoundError") throw codeError("storage", `not in this browser's storage: ${dir.join("/")}/${err.message || ""}`.trim());
    throw err;
  }
  mounts.set(root, { handles, files: listed, bytes: total, mode: sync ? "sync-access" : "copied" });
  return { root, mode: sync ? "sync-access" : "copied", files: listed, bytes: total };
}

/** An Emscripten file node whose reads come from a FileSystemSyncAccessHandle. */
function readOnlyFile(dirPath, name, size, handle) {
  const FS = py.FS;
  const node = FS.createFile(dirPath, name, {}, true, false);
  // the shape FS.createLazyFile gives a node: contents present (so nothing is "loaded"), usedBytes as the size
  node.contents = { length: size };
  Object.defineProperty(node, "usedBytes", { get: () => size });
  node.stream_ops = {
    open() {},
    close() {},
    llseek(stream, offset, whence) {
      let position = offset;
      if (whence === 1) position += stream.position;
      else if (whence === 2) position += size;
      if (position < 0) throw new FS.ErrnoError(EINVAL);
      return position;
    },
    read(stream, buffer, offset, length, position) {
      if (position >= size) return 0;
      const n = Math.min(size - position, length);
      return handle.read(buffer.subarray(offset, offset + n), { at: position });
    },
    write() { throw new FS.ErrnoError(EROFS); },
  };
  return node;
}

function unmount({ root }) {
  root = memfsPath(root);
  const m = mounts.get(root);
  if (m) for (const h of m.handles) { try { h.close(); } catch { /* already closed */ } }
  mounts.delete(root);
  try { rmTree(root); } catch { /* not there */ }
  return { root, closed: m ? m.handles.length : 0 };
}

/** Copy an OPFS tree into memory at `to`. */
async function restore({ dir, to }) {
  to = memfsPath(to);
  const folder = await opfsDir(dir);
  const out = [];
  const walk = async (handle, rel) => {
    for await (const [name, entry] of handle.entries()) {
      throwIfCancelled();
      const path = [...rel, name];
      if (entry.kind === "directory") await walk(entry, path);
      else {
        const bytes = new Uint8Array(await (await entry.getFile()).arrayBuffer());
        const target = `${to}/${path.join("/")}`;
        py.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
        py.FS.writeFile(target, bytes, { canOwn: true });
        out.push({ path, bytes: bytes.byteLength });
      }
    }
  };
  py.FS.mkdirTree(to);
  await walk(folder, []);
  return { to, files: out.length, bytes: out.reduce((a, f) => a + f.bytes, 0) };
}

/**
 * Copy the memory tree at `from` out to OPFS `dir` (emptied first when `clean`), each file with the SHA-256 of the
 * bytes written. Symbolic links are refused: a job's outputs are plain files.
 */
async function persist({ from, dir, clean = false }) {
  from = memfsPath(from);
  const FS = py.FS;
  if (!FS.analyzePath(from).exists) throw codeError("not_found", `nothing at ${from}`);
  if (clean && dir.length) {
    const parent = await opfsDir(dir.slice(0, -1), { create: true });
    try { await parent.removeEntry(dir[dir.length - 1], { recursive: true }); } catch { /* not there */ }
  }
  const root = await opfsDir(dir, { create: true });
  const files = [];
  const walk = async (path, rel, folder) => {
    for (const name of FS.readdir(path)) {
      if (name === "." || name === "..") continue;
      throwIfCancelled();
      const full = `${path}/${name}`;
      const st = FS.lstat(full);
      if (FS.isLink(st.mode)) throw codeError("bad_output", `${full} is a symbolic link; outputs must be plain files`);
      if (FS.isDir(st.mode)) {
        await walk(full, [...rel, name], await folder.getDirectoryHandle(name, { create: true }));
        continue;
      }
      const bytes = FS.readFile(full);
      await writeOpfs(folder, name, bytes);
      files.push({ path: [...rel, name], bytes: bytes.byteLength, sha256: await sha256Hex(bytes) });
    }
  };
  await walk(from, [], root);
  return { dir, files };
}

async function writeOpfs(folder, name, bytes) {
  const fh = await folder.getFileHandle(name, { create: true });
  if (syncAccessAvailable()) {
    const h = await fh.createSyncAccessHandle();
    try {
      h.truncate(0);
      let at = 0;
      while (at < bytes.byteLength) at += h.write(bytes.subarray(at), { at });
      h.flush();
    } finally {
      h.close();
    }
    return;
  }
  const w = await fh.createWritable();
  await w.write(bytes);
  await w.close();
}

/** Remove `paths` from memory. A mount at or below a freed path is unmounted first, so its OPFS handles close: an open
 * sync access handle would refuse the next mount, or a new download, of the same file. */
function free({ paths = [] }) {
  for (const p of paths) {
    const path = memfsPath(p);
    for (const root of [...mounts.keys()]) if (root === path || root.startsWith(`${path}/`)) unmount({ root });
    try { rmTree(path); } catch { /* not there */ }
  }
  return { freed: paths.length };
}

function rmTree(path) {
  const FS = py.FS;
  const st = FS.lstat(path);
  if (!FS.isDir(st.mode)) return FS.unlink(path);
  for (const name of FS.readdir(path)) if (name !== "." && name !== "..") rmTree(`${path}/${name}`);
  return FS.rmdir(path);
}

// ------------------------------------------------------------------------------------------------------------- runs

/** One function of tcmstudio.browser_jobs; Python's events go out as they come. → Python's JSON text. */
function run(id, { fn, args = {} }) {
  if (!api) throw codeError("not_ready", "the job worker has not been initialised");
  if (typeof fn !== "string" || !/^[a-z_]+$/.test(fn)) throw codeError("bad_request", `bad function name ${JSON.stringify(fn)}`);
  const emit = (kind, json) => {
    let data = {};
    try { data = JSON.parse(json); } catch { data = { text: String(json) }; }
    event(id, { kind: String(kind), ...data });
  };
  return api.job(fn, JSON.stringify(args), emit);
}

/** Test pages only (init {debug: true}): run Python text, return what it assigns to `result` as JSON text. */
function python({ code }) {
  if (!debugOps) throw codeError("bad_request", "debug operations are off");
  if (!api) throw codeError("not_ready", "the job worker has not been initialised");
  const ns = py.globals.get("dict")();
  try {
    py.runPython(String(code || ""), { globals: ns });
    const value = ns.get("result");
    return value === undefined ? null : String(value);
  } finally {
    ns.destroy?.();
  }
}

function memory() {
  let wasm = null;
  try { wasm = py?._module?.HEAPU8?.byteLength ?? null; } catch { wasm = null; }
  return {
    wasm_bytes: wasm,
    mounted_bytes: [...mounts.values()].reduce((a, m) => a + (m.mode === "copied" ? m.bytes : 0), 0),
  };
}
