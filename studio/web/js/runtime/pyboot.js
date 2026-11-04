// What every Pyodide worker of this site does the same way (CONTRACTS §5): the app bundle verified by SHA-256 before
// it runs, kept in Cache Storage between visits, and the modules compiled on an earlier visit put back. The tool
// runtime (./pyodide.worker.js) and the job workers (../scientific/engines/science.worker.js) both boot through here,
// so a job runs exactly the code a tool call runs.

export const CACHE_NAME = "tcmstudio-runtime-v1";
// a content-addressed file of the runtime: its name with the hash taken out is what a newer build's copy shares with it
// (runtime/tcms-py.<sha12>.tar.gz, runtime/formulas.<sha12>.tar.gz)
const HASHED_RE = /\.[0-9a-f]{12}(?=\.tar\.gz$)/;
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
      // older copies of this same file go (an earlier build's bundle, an earlier workbook), never another file: the
      // workbook's download must not take the app bundle with it
      const family = familyOf(url);
      if (family) for (const req of await cache.keys()) if (req.url !== url && familyOf(req.url) === family) await cache.delete(req);
    } catch { /* quota or private mode: still runs, downloads again next time */ }
  }
  return { bytes, fromCache: false };
}

/** A content-addressed file's path without its hash (/runtime/tcms-py.tar.gz), or null for any other file. */
function familyOf(url) {
  const path = new URL(url).pathname;
  return HASHED_RE.test(path) ? path.replace(HASHED_RE, "") : null;
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

export function quietLoad(reported = null) {
  return {
    messageCallback: () => {},
    errorCallback: (m) => {
      reported?.push(String(m));
      console.warn(`[tcmstudio runtime] ${m}`);
    },
  };
}

function lockPackages(py) {
  try {
    return py.lockfile?.packages || py._api?.lockfile_packages || null;
  } catch {
    return null;
  }
}

export function lockNames(py) {
  const pkgs = lockPackages(py);
  return pkgs ? new Set(Object.keys(pkgs)) : null;
}

/**
 * The lockfile's key (what a request names: "pillow") of a name py.loadedPackages holds, which is the entry's own
 * `name` field ("Pillow"); the two differ for some packages.
 */
function lockKeyOf(pkgs, name) {
  if (Object.hasOwn(pkgs, name)) return name;
  for (const [key, p] of Object.entries(pkgs)) if (p?.name === name) return key;
  return null;
}

/**
 * Load Pyodide packages (names as the lockfile writes them) → the names. Pyodide 314's loadPackage does not throw when
 * a wheel cannot be fetched or installed: it returns what it did load and reports the rest through errorCallback. So
 * every name asked for is looked up in py.loadedPackages afterwards (under the lockfile entry's own name, which is
 * how Pyodide keys it), and one that is not there is an error ("unavailable") that says what Pyodide reported, never
 * a package recorded as loaded.
 */
export async function loadPackages(py, names) {
  const reported = [];
  try {
    await py.loadPackage(names, quietLoad(reported));
  } catch (err) {
    throw codeError("unavailable", `could not load ${names.join(", ")}: ${messageOf(err)}`);
  }
  const loaded = py.loadedPackages || {};
  const pkgs = lockPackages(py) || {};
  const missing = names.filter((n) => !Object.hasOwn(loaded, n) && !Object.hasOwn(loaded, pkgs[n]?.name ?? n));
  if (missing.length) {
    const why = reported.filter(Boolean).slice(-4).join(" ");
    throw codeError("unavailable", `Pyodide ${py.version} could not load ${missing.join(", ")}${why ? `: ${why}` : ""}`);
  }
  return names;
}

/** {name: version} of every package this Pyodide has loaded, dependencies included, by their lockfile keys. */
export function loadedVersions(py) {
  const pkgs = lockPackages(py) || {};
  return Object.fromEntries(Object.keys(py.loadedPackages || {}).map((n) => {
    const key = lockKeyOf(pkgs, n);
    return [key ?? n, key ? pkgs[key]?.version ?? null : null];
  }).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));
}

// scipy's Matrix Market reader starts a C++ thread pool; WebAssembly here has no threads, and the failed thread start
// can abort the interpreter. One thread is what the browser has anyway.
const SCIPY_PY = `
try:
    import scipy.io._fast_matrix_market as _fmm
    _fmm.PARALLELISM = 1
except Exception:
    pass
`;

/**
 * Hold scipy's Matrix Market reader to one thread once scipy is loaded, whether it was asked for or came in as another
 * package's dependency (scikit-learn's, statsmodels') → whether it is loaded (and so held). Call after every load.
 */
export function pinScipyThreads(py) {
  if (!Object.hasOwn(py.loadedPackages || {}, "scipy")) return false;
  py.runPython(SCIPY_PY);
  return true;
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
