// The browser runtime's Python worker pool and accelerator hand-off (docs/V2.md §13.2–§13.4), with the real worker,
// the real Pyodide and the real psh + bioagent + tcmstudio, in Chromium:
//   - on a desktop two calls run at once (the primary and a secondary both busy), with the native results;
//   - a phone (emulated) keeps one worker, however many calls wait;
//   - the compiled-module tar the primary keeps holds the bundle tree only, never site-packages;
//   - the accelerate → accelerated → resume loop with a stub accelerator host (a fake kernel), and Stop during an
//     accelerated step.
// The runtime is driven from a bare test page on the site's origin (served with the site's own COOP/COEP headers), not
// through the app: these are properties of the runtime, not of a conversation.

import { spawnSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { watchConsole } from "./lib/app.mjs";
import { PYTHON, REPO, startStatic } from "./lib/servers.mjs";

let site;

test.beforeAll(async () => {
  site = await startStatic(process.env.STUDIO_SITE);
});

test.afterAll(async () => {
  await site?.stop();
});

const LONG = (n) => ({ a: "ACGT".repeat(n / 4), b: "TGCA".repeat(n / 4) });
// A page whose only script is the runtime: helpers to make runtimes, a stub accelerator host, and the pool's history.
const PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>pool</title></head><body>
<script type="module">
import { BrowserRuntime } from "/js/runtime/browser.js";
window.BrowserRuntime = BrowserRuntime;
window.make = (opts = {}) => {
  const rt = new BrowserRuntime({ bootUrl: new URL("/runtime/boot.json", location.href).href, ...opts });
  rt.busyMax = 0;
  rt.onPool((p) => { rt.busyMax = Math.max(rt.busyMax, p.busy); });
  return rt;
};
// a fake kernel for debug.accel_probe: the sum 0 + 1 + … + (n − 1), computed here; "hang" waits for Stop
window.stubHost = (mode = "sum") => {
  const host = { runs: 0, aborted: false, progress: [] };
  host.kernels = () => ["np-null-mt/1"];
  host.run = (kernel, input, { signal, onProgress }) => new Promise((resolve, reject) => {
    host.runs++;
    if (mode === "hang") {
      onProgress?.(0.25);
      signal.addEventListener("abort", () => { host.aborted = true; reject(new DOMException("stopped", "AbortError")); });
      return;
    }
    if (mode === "fail") { reject(new Error("the fake kernel failed")); return; }
    onProgress?.(0.5);
    resolve({ sum: (input.n * (input.n - 1)) / 2, engine: "stub-kernel" });
  });
  return host;
};
window.__ready = true;
</script></body></html>`;

/** Open the test page on the site's origin, with the headers the site gives every document (COOP/COEP). */
async function openPage(context, { emulateMemory } = {}) {
  const page = await context.newPage();
  if (emulateMemory) await page.addInitScript((gb) => { Object.defineProperty(Navigator.prototype, "deviceMemory", { get: () => gb, configurable: true }); }, emulateMemory);
  const headers = await (await fetch(`${site.url}/index.html`)).headers;
  const keep = {};
  for (const name of ["cross-origin-opener-policy", "cross-origin-embedder-policy", "cross-origin-resource-policy"]) if (headers.get(name)) keep[name] = headers.get(name);
  await page.route(`${site.url}/__pool.html`, (route) => route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", headers: keep, body: PAGE }));
  await page.goto(`${site.url}/__pool.html`);
  await page.waitForFunction(() => window.__ready === true);
  return page;
}

async function cdnReachable(page) {
  return page.evaluate(async () => {
    const boot = await (await fetch("/runtime/boot.json")).json();
    const index = new URL(boot.pyodide.index_url, location.href).href;
    try { return (await fetch(`${index}pyodide-lock.json`)).ok; } catch { return false; }
  });
}

function native(tool, args) {
  const r = spawnSync(PYTHON, ["-m", "tcmstudio", "call", tool, "--args", JSON.stringify(args), "--where", "browser", "--compact"], { cwd: REPO, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  return JSON.parse(r.stdout);
}

test("desktop: two calls run at once, on the primary and a secondary, with the native results", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext();
  const page = await openPage(context);
  const watch = watchConsole(page);
  test.skip(!(await cdnReachable(page)), "Pyodide's CDN is not reachable from this browser");
  expect(await page.evaluate(() => crossOriginIsolated)).toBe(true);

  const args = LONG(2400);
  const out = await page.evaluate(async (args) => {
    const rt = window.make({ device: { cores: 8, memory_gb: 8, mobile: false, ios: false, tablet: false }, spawnDelayMs: 0, accel: false });
    window.rt = rt;
    const size = rt.pool().size;
    // a long call keeps the primary busy; a second one waits and boots a secondary
    const first = await Promise.all([
      rt.call("call_tool", { tool: "native.edit_distance", arguments: args }),
      rt.call("tcm_compatibility", { herbs: ["甘草", "甘遂"] }),
    ]);
    // both workers are up now; a first pair warms the secondary (its first call imports what the primary's warm-up did)
    await Promise.all([0, 1].map(() => rt.call("call_tool", { tool: "native.edit_distance", arguments: args })));
    const t0 = performance.now();
    const solo = await rt.call("call_tool", { tool: "native.edit_distance", arguments: args });
    const soloMs = performance.now() - t0;
    rt.busyMax = 0;
    const t1 = performance.now();
    const pair = await Promise.all([
      rt.call("call_tool", { tool: "native.edit_distance", arguments: args }),
      rt.call("call_tool", { tool: "native.edit_distance", arguments: args }),
    ]);
    const pairMs = performance.now() - t1;
    const info = await rt.info();
    return { size, first: first.map((e) => e.status), solo: solo.result, soloMs, pair: pair.map((e) => ({ status: e.status, result: e.result, where: e.receipt.where })), pairMs, busyMax: rt.busyMax, pool: info.pool };
  }, args);

  expect(out.size).toBe(4);
  expect(out.first).toEqual(["succeeded", "succeeded"]);
  expect(out.pool.live).toBeGreaterThanOrEqual(2);
  expect(out.pool.workers.find((w) => w.primary)).toBeTruthy();
  expect(out.busyMax, "the primary and a secondary were busy at the same time").toBe(2);
  for (const e of out.pair) {
    expect(e.status).toBe("succeeded");
    expect(e.where).toBe("browser");
    expect(e.result).toEqual(out.solo);
  }
  const ref = native("call_tool", { tool: "native.edit_distance", arguments: args });
  expect(out.solo).toEqual(ref.result);
  expect(out.pairMs, `two calls at once (${Math.round(out.pairMs)} ms) take well under twice one call (${Math.round(out.soloMs)} ms)`).toBeLessThan(out.soloMs * 1.7);
  test.info().annotations.push({ type: "one call / two at once", description: `${Math.round(out.soloMs)} ms / ${Math.round(out.pairMs)} ms` });
  watch.expectClean();
  await context.close();
});

test("phone (emulated): the pool keeps one worker however many calls wait", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, locale: "zh-CN",
    userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
  });
  const page = await openPage(context, { emulateMemory: 4 });
  const watch = watchConsole(page);
  test.skip(!(await cdnReachable(page)), "Pyodide's CDN is not reachable from this browser");
  const out = await page.evaluate(async (args) => {
    const rt = window.make({ spawnDelayMs: 0, accel: false });
    const before = rt.pool();
    let maxLive = 0;
    let maxBooting = 0;
    rt.onPool((p) => { maxLive = Math.max(maxLive, p.live); maxBooting = Math.max(maxBooting, p.booting); });
    const envs = await Promise.all([
      rt.call("call_tool", { tool: "native.edit_distance", arguments: args }),
      rt.call("tcm_compatibility", { herbs: ["人参", "藜芦"] }),
      rt.call("call_tool", { tool: "native.benjamini_hochberg", arguments: { p_values: [0.01, 0.04, 0.03, 0.2] } }),
    ]);
    return { before, statuses: envs.map((e) => e.status), maxLive, maxBooting, busyMax: rt.busyMax, after: rt.pool() };
  }, LONG(800));
  expect(out.before.cls).toBe("phone");
  expect(out.before.size).toBe(1);
  expect(out.statuses).toEqual(["succeeded", "succeeded", "succeeded"]);
  expect(out.maxLive).toBe(1);
  expect(out.busyMax).toBe(1);
  expect(out.after.workers.length).toBe(1);
  watch.expectClean();
  await context.close();
});

test("the compiled-module tar holds the bundle tree only, never site-packages", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext();
  const page = await openPage(context);
  test.skip(!(await cdnReachable(page)), "Pyodide's CDN is not reachable from this browser");
  const out = await page.evaluate(async () => {
    const rt = window.make({ workers: 1, debug: true, accel: false });
    // numpy (site-packages) is imported by this study tool; yaml and packaging already are at boot
    const env = await rt.call("call_tool", {
      tool: "study.contrast_formula_monomer",
      arguments: { formula: { label: "GQD", values: [2.1, 2.4, 2.0, 2.6, 2.3, 2.2], study: "demo" }, monomer: { label: "berberine", values: [1.8, 1.7, 2.0, 1.6, 1.9, 1.75], study: "demo" }, margin: 0.5 },
    });
    // the primary saves after its reply, before it takes the next operation: once this answers, the save is done
    const probe = (await rt.debug("pycache")).result;
    const cache = await caches.open("tcmstudio-runtime-v1");
    const entry = (await cache.keys()).find((r) => /\/runtime\/\.pycache\/.+\.tar$/.test(new URL(r.url).pathname)) || null;
    if (!entry) return { status: env.status, packages: env.receipt, entry: null };
    const bytes = new Uint8Array(await (await cache.match(entry)).arrayBuffer());
    // tar: 512-byte headers; a name (100 bytes) with a ustar prefix (155 bytes at 345), and PAX records for long names
    const names = [];
    const text = (a, b) => new TextDecoder().decode(bytes.subarray(a, b)).replace(/\0.*$/s, "");
    let at = 0;
    let paxName = null;
    while (at + 512 <= bytes.length) {
      const name = text(at, at + 100);
      if (!name) break;
      const size = parseInt(text(at + 124, at + 136).trim() || "0", 8);
      const type = String.fromCharCode(bytes[at + 156] || 48);
      const prefix = text(at + 345, at + 500);
      if (type === "x") {
        const rec = text(at + 512, at + 512 + size);
        paxName = /\d+ path=([^\n]+)\n/.exec(rec)?.[1] || null;
      } else {
        names.push(paxName || (prefix ? `${prefix}/${name}` : name));
        paxName = null;
      }
      at += 512 + Math.ceil(size / 512) * 512;
    }
    return { status: env.status, url: entry.url, bytes: bytes.length, names, probe, info: await rt.info() };
  });
  expect(out.status).toBe("succeeded");
  expect(out.entry === null ? "no tar saved" : "saved").toBe("saved");
  expect(out.info.packages).toContain("numpy");
  expect(out.url).toMatch(/\/runtime\/\.pycache\/[0-9a-f]{16}-pyodide-314\.0\.7-bundle\.tar$/);
  const pycs = out.names.filter((n) => n.endsWith(".pyc"));
  expect(pycs.length, "the bundle's compiled modules are kept").toBeGreaterThan(50);
  expect(out.names.filter((n) => /site-packages|lib\/python3/.test(n)), "nothing from site-packages or the standard library").toEqual([]);
  expect(out.names.every((n) => n.startsWith("opt/tcms/site"))).toBe(true);
  expect(pycs.some((n) => /^opt\/tcms\/site\/tcmstudio\/dispatch\.cpython-314\.pyc$/.test(n)), "tcmstudio.dispatch is among them (pycache_prefix mirrors the source tree)").toBe(true);
  expect(out.probe.saves).toBe(true);
  expect(pycs.length, "the tar saved after the call holds every compiled module of the bundle").toBe(out.probe.files);
  test.info().annotations.push({ type: "pycache tar", description: `${out.bytes} bytes, ${pycs.length} .pyc files` });
  await context.close();
});

test("the accelerator loop: a stub kernel runs on the page and Python finishes the call with its result", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext();
  const page = await openPage(context);
  const watch = watchConsole(page);
  test.skip(!(await cdnReachable(page)), "Pyodide's CDN is not reachable from this browser");
  const out = await page.evaluate(async () => {
    const host = window.stubHost("sum");
    const rt = window.make({ workers: 1, debug: true, accelHost: host });
    const messages = [];
    rt.onStatus((s) => messages.push(s.message));
    const ok = await rt.call("debug.accel_probe", { n: 1000 });
    const failing = window.make({ workers: 1, debug: true, accelHost: window.stubHost("fail") });
    const fallback = await failing.call("debug.accel_probe", { n: 1000 });
    const none = window.make({ workers: 1, debug: true, accel: false });
    const plain = await none.call("debug.accel_probe", { n: 10 });
    return { ok: ok.result, runs: host.runs, messages, fallback: fallback.result, plain: plain.result };
  });
  expect(out.runs).toBe(1);
  expect(out.ok).toEqual({ value: 499500, native: 499500, equal: true, engine: "stub-kernel", error: null, accelerated: true });
  expect(out.messages.some((m) => /浏览器加速器|browser accelerator/.test(m) && /50%/.test(m)), "progress in the runtime status").toBe(true);
  expect(out.fallback, "a failed kernel: Python computes natively and says why").toEqual({ value: 499500, native: 499500, equal: true, engine: "python", error: "the fake kernel failed", accelerated: true });
  expect(out.plain, "no accelerator on the page: Python never asks").toEqual({ value: 45, native: 45, equal: true, engine: "python", error: null, accelerated: false });
  watch.expectClean();
  await context.close();
});

test("Stop during an accelerated step cancels the call at once; the worker stays and runs the next call", async ({ browser }) => {
  test.setTimeout(300_000);
  const context = await browser.newContext();
  const page = await openPage(context);
  const watch = watchConsole(page);
  test.skip(!(await cdnReachable(page)), "Pyodide's CDN is not reachable from this browser");
  const out = await page.evaluate(async () => {
    const host = window.stubHost("hang");
    const rt = window.make({ workers: 1, debug: true, accelHost: host });
    await rt.start();
    const ac = new AbortController();
    const p = rt.call("debug.accel_probe", { n: 1000 }, { signal: ac.signal });
    while (!host.runs) await new Promise((r) => setTimeout(r, 20));
    const t0 = performance.now();
    ac.abort();
    const env = await p;
    const ms = performance.now() - t0;
    const restarts = rt.pool();
    const next = await rt.call("tcm_compatibility", { herbs: ["人参", "藜芦"] });
    return { status: env.status, ms, aborted: host.aborted, pool: restarts, next: next.status, compatible: next.result?.compatible, statusAfter: rt.status };
  });
  expect(out.status).toBe("cancelled");
  expect(out.aborted, "the host's AbortSignal fired").toBe(true);
  expect(out.ms).toBeLessThan(1500);
  expect(out.pool.workers.length).toBe(1);
  expect(out.next).toBe("succeeded");
  expect(out.compatible).toBe(false);
  expect(out.statusAfter).toBe("ready");
  watch.expectClean();
  await context.close();
});
