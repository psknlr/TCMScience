// What every Pyodide worker of this site does the same way (CONTRACTS §5): the app bundle verified by SHA-256 before
// it runs, kept in Cache Storage between visits, and the modules compiled on an earlier visit put back. The tool
// runtime (./pyodide.worker.js) and the job workers (../scientific/engines/science.worker.js) both boot through here,
// so a job runs exactly the code a tool call runs.

export const CACHE_NAME = "tcmstudio-runtime-v1";
export const BUNDLE_RE = /\/tcms-py\.[0-9a-f]{12}\.tar\.gz$/;
export const PYCACHE_RE = /\/runtime\/\.pycache\/[^/]+\.tar$/;
export const PYCACHE_ROOT = "/pycache";

export async function openCache() {
  try {
    return typeof caches === "undefined" ? null : await caches.open(CACHE_NAME);
  } catch {
    return null; // Cache Storage refused (opaque origin, storage disabled): download every time
  }
}

/** The bundle's bytes, from Cache Storage or the network, never used before its SHA-256 matches boot.json. */
export async function fetchBundle(url, meta, onFraction) {
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

export async function readAll(res, expected, onFraction) {
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

/** Put back the modules compiled on an earlier visit (about a second of the first call), if their copy is intact. */
export async function restorePycache(py, key) {
  const cache = await openCache();
  if (!cache || !key) return false;
  try {
    const hit = await cache.match(key);
    if (!hit) return false;
    const bytes = new Uint8Array(await hit.arrayBuffer());
    if ((await sha256Hex(bytes)) !== hit.headers.get("x-tcmstudio-sha256")) {
      await cache.delete(key);
      return false;
    }
    py.unpackArchive(bytes, "tar", { extractDir: PYCACHE_ROOT });
    return true;
  } catch {
    return false;
  }
}

/** The cache key of the compiled modules: the bundle's hash and the Pyodide version, so a pyc never outlives its source. */
export function pycacheKey(site, bundleSha256, pyodideVersion) {
  return new URL(`runtime/.pycache/${bundleSha256.slice(0, 16)}-pyodide-${pyodideVersion}.tar`, site).href;
}

export function quietLoad() {
  return { messageCallback: () => {}, errorCallback: (m) => console.warn(`[tcmstudio runtime] ${m}`) };
}

export function lockNames(py) {
  try {
    const pkgs = py.lockfile?.packages || py._api?.lockfile_packages;
    return pkgs ? new Set(Object.keys(pkgs)) : null;
  } catch {
    return null;
  }
}

export function isFatalError(err) {
  return Boolean(err?.pyodide_fatal_error) || /fatally failed|can no longer be used/i.test(messageOf(err));
}

export function codeError(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

export function messageOf(err) {
  const s = String(err?.message || err || "error");
  // A Python traceback: the last line names the exception.
  return s.length > 2000 ? `…${s.slice(-2000)}` : s;
}

export function withSlash(url) {
  return url.endsWith("/") ? url : `${url}/`;
}

export async function sha256Hex(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  let s = "";
  for (const b of d) s += b.toString(16).padStart(2, "0");
  return s;
}
