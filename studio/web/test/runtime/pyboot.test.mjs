// What both Pyodide workers do the same way when they load packages (js/runtime/pyboot.js), against a scripted Pyodide
// that behaves as 314.0.7 does (checked in Node against the real one): a wheel that cannot be fetched makes
// loadPackage return without it and report through errorCallback, not throw. Such a package must be an error, never
// recorded as loaded; scipy's Matrix Market reader is held to one thread whenever scipy is loaded, also as another
// package's dependency; the versions of what is loaded come from the lockfile.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { CACHE_NAME, fetchBundle, loadPackages, loadedVersions, pinScipyThreads, sha256Hex } from "../../js/runtime/pyboot.js";

const LOCK = {
  numpy: { version: "2.4.6", depends: [] },
  scipy: { version: "1.18.0", depends: ["numpy"] },
  "scikit-learn": { version: "1.8.0", depends: ["scipy", "joblib"] },
  joblib: { version: "1.5.3", depends: [] },
  pyarrow: { version: "22.0.0", depends: ["numpy"] },
};

/** A Pyodide whose loadPackage loads each name and its dependencies, except those in `unreachable` (a failed fetch). */
function fakePyodide({ unreachable = [], throws = null } = {}) {
  const ran = [];
  const py = {
    version: "314.0.7",
    loadedPackages: {},
    lockfile: { packages: LOCK },
    runPython(code) { ran.push(code); },
    async loadPackage(names, { errorCallback } = {}) {
      if (throws) throw new Error(throws);
      const out = [];
      const visit = (n) => {
        if (py.loadedPackages[n]) return;
        for (const d of LOCK[n]?.depends || []) visit(d);
        if (unreachable.includes(n)) {
          errorCallback?.(`The following error occurred while loading ${n}:`);
          errorCallback?.("fetch failed");
          return;
        }
        py.loadedPackages[n] = "default channel";
        out.push({ name: n, version: LOCK[n].version });
      };
      for (const n of names) visit(n);
      return out;
    },
  };
  py.ran = ran;
  return py;
}

test("a package Pyodide could not fetch is an error that says so, not a package recorded as loaded", async () => {
  const warn = console.warn;
  console.warn = () => {};
  try {
    const py = fakePyodide({ unreachable: ["scipy"] });
    await assert.rejects(loadPackages(py, ["numpy", "scipy"]), (err) => {
      assert.equal(err.code, "unavailable");
      assert.match(err.message, /^Pyodide 314\.0\.7 could not load scipy: The following error occurred while loading scipy: fetch failed$/);
      return true;
    });
    assert.deepEqual(Object.keys(py.loadedPackages), ["numpy"]);
    // a loadPackage that throws is the same kind of error
    await assert.rejects(loadPackages(fakePyodide({ throws: "lockfile mismatch" }), ["numpy"]), { code: "unavailable", message: /could not load numpy: lockfile mismatch/ });
    assert.deepEqual(await loadPackages(fakePyodide(), ["numpy", "scipy"]), ["numpy", "scipy"]);
  } finally {
    console.warn = warn;
  }
});

test("scipy's reader is held to one thread whenever scipy is loaded, also as a dependency; versions come from the lockfile", async () => {
  const py = fakePyodide();
  await loadPackages(py, ["pyarrow"]);
  assert.equal(pinScipyThreads(py), false, "no scipy, nothing to hold");
  assert.equal(py.ran.length, 0);
  await loadPackages(py, ["scikit-learn"]); // scipy comes in as its dependency, never named
  assert.equal(pinScipyThreads(py), true);
  assert.match(py.ran[0], /_fast_matrix_market as _fmm\n {4}_fmm\.PARALLELISM = 1/);
  assert.deepEqual(loadedVersions(py), { joblib: "1.5.3", numpy: "2.4.6", pyarrow: "22.0.0", "scikit-learn": "1.8.0", scipy: "1.18.0" });
  assert.deepEqual(loadedVersions({ loadedPackages: { numpy: "x" } }), { numpy: null }, "a Pyodide without a lockfile reports no version");
});

test("Cache Storage keeps the app bundle and the formula workbook side by side; a download prunes only older copies of itself", async () => {
  const SITE = "https://science.impf.ai/runtime/";
  const stores = new Map();
  const cache = {
    async match(url) { const r = stores.get(String(url)); return r ? r.clone() : undefined; },
    async put(url, res) { stores.set(String(url), res); },
    async delete(req) { return stores.delete(typeof req === "string" ? req : req.url); },
    async keys() { return [...stores.keys()].map((url) => new Request(url)); },
  };
  const online = { up: true };
  const files = {};
  const saved = { caches: globalThis.caches, fetch: globalThis.fetch };
  globalThis.caches = { async open(name) { assert.equal(name, CACHE_NAME); return cache; } };
  globalThis.fetch = async (url) => {
    if (!online.up) throw new TypeError("Failed to fetch");
    return files[url] ? new Response(files[url]) : new Response("missing", { status: 404 });
  };
  const asset = async (name, text) => {
    const bytes = new TextEncoder().encode(text);
    files[SITE + name] = bytes;
    return [SITE + name, { sha256: await sha256Hex(bytes), bytes: bytes.byteLength }];
  };
  const names = () => [...stores.keys()].map((u) => u.slice(SITE.length)).sort();
  try {
    const bundle = await asset("tcms-py.aaaaaaaaaaaa.tar.gz", "the app");
    const older = await asset("tcms-py.bbbbbbbbbbbb.tar.gz", "an older app");
    const workbook = await asset("formulas.cccccccccccc.tar.gz", "the workbook");
    const oldWorkbook = await asset("formulas.dddddddddddd.tar.gz", "an older workbook");
    await fetchBundle(...older, () => {});
    await fetchBundle(...oldWorkbook, () => {});
    assert.equal((await fetchBundle(...bundle, () => {})).fromCache, false);
    assert.deepEqual(names(), ["formulas.dddddddddddd.tar.gz", "tcms-py.aaaaaaaaaaaa.tar.gz"], "the new bundle replaced the older one");
    assert.equal((await fetchBundle(...workbook, () => {})).fromCache, false);
    assert.deepEqual(names(), ["formulas.cccccccccccc.tar.gz", "tcms-py.aaaaaaaaaaaa.tar.gz"], "the workbook replaced its older copy, and only that");
    // offline, the next visit starts from Cache Storage
    online.up = false;
    assert.equal((await fetchBundle(...bundle, () => {})).fromCache, true);
    assert.equal((await fetchBundle(...workbook, () => {})).fromCache, true);
  } finally {
    globalThis.caches = saved.caches;
    globalThis.fetch = saved.fetch;
  }
});

test("a package whose lockfile entry is named otherwise than its key (pillow, Pillow) counts as loaded under either", async () => {
  // as 314.0.7 keeps them: py.loadedPackages[entry.name], the request by the lockfile's key
  const lock = { pillow: { name: "Pillow", version: "12.2.0", depends: [] }, numpy: { name: "numpy", version: "2.4.6", depends: [] } };
  const py = {
    version: "314.0.7", loadedPackages: {}, lockfile: { packages: lock },
    async loadPackage(names) { for (const n of names) py.loadedPackages[lock[n].name] = "default channel"; return []; },
  };
  assert.deepEqual(await loadPackages(py, ["pillow", "numpy"]), ["pillow", "numpy"]);
  assert.deepEqual(loadedVersions(py), { numpy: "2.4.6", pillow: "12.2.0" });
});
