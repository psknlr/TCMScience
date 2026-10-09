// The page-side accelerators in Chromium (docs/V2.md §13.4), from the built site: the js-workers engine (module
// workers, also nested inside a dedicated worker) and the WebGPU kernel give CPython's counts on every fixture world
// (studio/web/test/accel/fixtures.json, made by make_fixtures.py with CPython's own random), Stop cancels both, and
// the benchmark's 1,399-pathway world is timed on 1 JS worker, N JS workers and WebGPU (annotations).
//
// Headless Chromium has no GPU here: with --enable-unsafe-webgpu it offers SwiftShader, a software adapter that runs
// the WGSL on the CPU. That proves the kernel exact, not GPU speed. The host never picks it on "auto"; the tests ask
// for it by name (allowSoftwareGpu + engine "webgpu"). Without any adapter the WebGPU tests skip, saying so; the WGSL
// logic is then still covered by its CPU emulation in the node tests (web/test/accel/wgsl-emu.mjs).

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import config from "./playwright.config.mjs";
import { startStatic } from "./lib/servers.mjs";

const FIX = JSON.parse(readFileSync(new URL("../web/test/accel/fixtures.json", import.meta.url), "utf8"));
const WORLDS = FIX.worlds.map((w) => ({ name: w.name, input: w.input, at_least: w.at_least }));
const GPU_FLAGS = ["--enable-unsafe-webgpu", "--enable-features=Vulkan"];
const base = config.use?.launchOptions || {};
test.use({ launchOptions: { ...base, args: [...(base.args || []), ...GPU_FLAGS] } });

const BLANK = "/__accel__.html";
let site;

test.beforeAll(async () => {
  site = await startStatic(process.env.STUDIO_SITE);
});

test.afterAll(async () => {
  await site?.stop();
});

/**
 * The kernel digest as plain Node computes it from the sources. In a separate process: the test runner transpiles the
 * modules a spec imports, which would change the source text the digest is made of.
 */
function nodeDigest() {
  const host = new URL("../web/js/accel/host.js", import.meta.url).href;
  const r = spawnSync(process.execPath, ["--input-type=module", "-e", `const m = await import(${JSON.stringify(host)}); console.log(await m.kernelDigest());`], { encoding: "utf8" });
  if (r.status !== 0) throw new Error(`node could not compute the digest: ${r.stderr}`);
  return r.stdout.trim();
}

/** A blank page on the site's origin (cross-origin isolated like the app), with page errors collected. */
async function open(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route(`${site.url}${BLANK}`, (route) => route.fulfill({
    status: 200, contentType: "text/html; charset=utf-8",
    headers: { "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp" },
    body: "<!doctype html><meta charset=utf-8><title>accel</title>",
  }));
  await page.goto(`${site.url}${BLANK}`);
  return errors;
}

/** The adapter this browser offers: {vendor, architecture, software} or null. */
function adapterOf(page) {
  return page.evaluate(async () => {
    const a = await navigator.gpu?.requestAdapter?.();
    if (!a) return null;
    const i = a.info || {};
    return { vendor: i.vendor || "", architecture: i.architecture || "", software: Boolean(a.isFallbackAdapter ?? i.isFallbackAdapter) };
  });
}

test("js-workers on the page: every fixture world equals CPython", async ({ page }) => {
  const errors = await open(page);
  const out = await page.evaluate(async (worlds) => {
    const { createAccelHost } = await import("/js/accel/host.js");
    const host = createAccelHost({ device: { cls: "desktop", js_workers: 3, gpu: "none" }, settings: { workers: "auto", gpu: "auto" } });
    const rows = [];
    for (const w of worlds) {
      const r = await host.run("np-null-mt/1", w.input);
      rows.push({ name: w.name, engine: r.engine, device: r.device, equal: JSON.stringify(r.at_least) === JSON.stringify(w.at_least), digest: r.kernel_digest });
    }
    host.dispose();
    return rows;
  }, WORLDS);
  for (const r of out) {
    expect(r.equal, r.name).toBe(true);
    expect(r.engine).toBe("js-workers");
  }
  expect(new Set(out.map((r) => r.digest)).size).toBe(1);
  expect(out[0].digest).toMatch(/^sha256:[0-9a-f]{64}$/);
  expect(out[0].digest, "the page and Node digest the same sources to the same value").toBe(nodeDigest());
  test.info().annotations.push({ type: "kernel_digest", description: out[0].digest });
  expect(errors).toEqual([]);
});

test("the host inside a dedicated worker (nested JS workers) equals CPython", async ({ page }) => {
  const errors = await open(page);
  const picked = WORLDS.filter((w) => ["synthetic-mini", "k-gt-5-boundaries", "seed-and-ids", "pipeline-spread"].includes(w.name));
  const out = await page.evaluate(async (worlds) => {
    const src = `import { createAccelHost } from "${location.origin}/js/accel/host.js";
      self.onmessage = async ({ data }) => {
        try {
          const host = createAccelHost({ device: { js_workers: 2, gpu: "none" } });
          const r = await host.run("np-null-mt/1", data.input);
          self.postMessage({ ok: true, at_least: r.at_least, engine: r.engine, device: r.device });
        } catch (err) { self.postMessage({ ok: false, error: String(err && err.message || err) }); }
      };`;
    const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    const rows = [];
    for (const w of worlds) {
      const worker = new Worker(url, { type: "module" });
      const r = await new Promise((resolve) => {
        worker.onmessage = ({ data }) => resolve(data);
        worker.onerror = (e) => resolve({ ok: false, error: e.message || "worker error" });
        worker.postMessage({ input: w.input });
      });
      worker.terminate();
      rows.push({ name: w.name, ...r, equal: r.ok && JSON.stringify(r.at_least) === JSON.stringify(w.at_least) });
    }
    return rows;
  }, picked);
  for (const r of out) {
    expect(r.error, r.name).toBeUndefined();
    expect(r.equal, r.name).toBe(true);
    expect(r.engine).toBe("js-workers");
    expect(r.device).toBe("cpu: 2 JS workers");
  }
  expect(errors).toEqual([]);
});

test("WebGPU (software adapter, asked for by name): every fixture world equals CPython; auto never takes it", async ({ page }) => {
  test.setTimeout(600_000);
  const errors = await open(page);
  const adapter = await adapterOf(page);
  test.skip(!adapter, "no WebGPU adapter in this headless Chromium (even with --enable-unsafe-webgpu): the WGSL is covered by its CPU emulation in web/test/accel");
  test.info().annotations.push({ type: "adapter", description: JSON.stringify(adapter) });
  const out = await page.evaluate(async (worlds) => {
    const { createAccelHost } = await import("/js/accel/host.js");
    const { openGpu, runWebGPU } = await import("/js/accel/gpu.js");
    const { prepare } = await import("/js/accel/npnull.js");
    const rows = [];
    // the raw engine, whole arrays
    const ctx = await openGpu({ allowSoftware: true });
    if (!ctx.ok) return { error: ctx.reason };
    for (const w of worlds) {
      const t0 = performance.now();
      const got = await runWebGPU(prepare(w.input), ctx, {});
      rows.push({ name: w.name, raw: true, equal: JSON.stringify(got) === JSON.stringify(w.at_least), ms: Math.round(performance.now() - t0) });
    }
    // bindings capped at 48 KiB: the synthetic world runs in several batches, passes sized short
    const mini = worlds.find((x) => x.name === "synthetic-mini");
    const batched = await runWebGPU(prepare(mini.input), ctx, { maxBindingBytes: 48 * 1024, passTargetMs: 50 });
    rows.push({ name: "synthetic-mini in batches", raw: true, equal: JSON.stringify(batched) === JSON.stringify(mini.at_least), ms: 0 });
    ctx.device.destroy();
    // through the host: engine "webgpu" with software allowed
    const host = createAccelHost({ device: { js_workers: 2, gpu: "software" }, allowSoftwareGpu: true });
    for (const w of worlds.filter((x) => x.input.pathways.length)) {
      const r = await host.run("np-null-mt/1", w.input, { engine: "webgpu" });
      rows.push({ name: w.name, engine: r.engine, device: r.device, checked: r.checked, fallback: r.fallback || null, equal: JSON.stringify(r.at_least) === JSON.stringify(w.at_least) });
    }
    // auto, with a device report that (wrongly) says hardware: the host's own adapter check still refuses SwiftShader
    const auto = createAccelHost({ device: { js_workers: 2, gpu: "hardware" }, allowSoftwareGpu: true });
    const big = worlds.find((x) => x.name === "synthetic-mini");
    const a = await auto.run("np-null-mt/1", big.input);
    host.dispose();
    auto.dispose();
    return { rows, auto: { engine: a.engine, fallback: a.fallback || null, equal: JSON.stringify(a.at_least) === JSON.stringify(big.at_least) } };
  }, WORLDS);
  expect(out.error).toBeUndefined();
  for (const r of out.rows) {
    expect(r.equal, `${r.name}${r.raw ? " (runWebGPU)" : " (host)"}`).toBe(true);
    if (!r.raw) {
      expect(r.engine, r.name).toBe("webgpu");
      expect(r.fallback, r.name).toBeNull();
      expect(r.device).toMatch(/^gpu \(software\): /);
      expect(r.checked.length).toBeGreaterThanOrEqual(1);
    }
  }
  expect(out.auto).toEqual({ engine: "js-workers", fallback: null, equal: true });
  test.info().annotations.push({ type: "webgpu fixture ms", description: out.rows.filter((r) => r.raw).map((r) => `${r.name} ${r.ms}`).join(", ") });
  expect(errors).toEqual([]);
});

test("Stop: an abort ends a js-workers run and a WebGPU run with AbortError, promptly", async ({ page }) => {
  test.setTimeout(300_000);
  const errors = await open(page);
  const adapter = await adapterOf(page);
  const out = await page.evaluate(async (gpu) => {
    const { createAccelHost } = await import("/js/accel/host.js");
    const { loadWorld, worldInput } = await import("/js/accel/bench.js");
    const { input } = worldInput(await loadWorld());
    const host = createAccelHost({ device: { js_workers: 3, gpu: "software" }, allowSoftwareGpu: true });
    const stop = async (engine) => {
      const ctrl = new AbortController();
      const t0 = performance.now();
      const run = host.run("np-null-mt/1", input, { signal: ctrl.signal, engine, onProgress: () => {} });
      await new Promise((r) => setTimeout(r, 1500));
      const tAbort = performance.now();
      ctrl.abort();
      try {
        await run;
        return { engine, settled: "resolved" };
      } catch (err) {
        return { engine, settled: err.name, after_abort_ms: Math.round(performance.now() - tAbort), ran_ms: Math.round(tAbort - t0) };
      }
    };
    const rows = [await stop("js-workers")];
    if (gpu) rows.push(await stop("webgpu"));
    // the host still works after a cancelled run
    const small = { ...input, pathways: input.pathways.slice(0, 3) };
    const again = await host.run("np-null-mt/1", small, { engine: "js-workers" });
    host.dispose();
    return { rows, again: again.at_least.length };
  }, Boolean(adapter));
  for (const r of out.rows) {
    expect(r.settled, r.engine).toBe("AbortError");
    // a GPU pass already submitted finishes first (passes are kept to about a second)
    expect(r.after_abort_ms, r.engine).toBeLessThan(r.engine === "webgpu" ? 15_000 : 1_000);
  }
  expect(out.again).toBe(3);
  test.info().annotations.push({ type: "abort", description: JSON.stringify(out.rows) });
  expect(errors).toEqual([]);
});

test("benchmark: the 1,399-pathway world on 1 JS worker, N JS workers and WebGPU, all equal to CPython", async ({ page }) => {
  test.setTimeout(900_000);
  const errors = await open(page);
  const out = await page.evaluate(async () => {
    const { runBenchmark } = await import("/js/accel/bench.js");
    const n = Math.max(1, Math.min(8, (navigator.hardwareConcurrency || 2) - 1));
    const progress = [];
    const r = await runBenchmark({ device: { cls: "desktop", js_workers: n }, settings: { workers: "auto", gpu: "auto" }, allowSoftwareGpu: true, onProgress: (f) => progress.push(f) });
    return { ...r, cores: navigator.hardwareConcurrency, lastProgress: progress.at(-1) };
  });
  expect(out.world.pathways).toBe(1399);
  expect(out.world.reference_equals_cpython).toBe(true);
  expect(out.lastProgress).toBe(1);
  for (const r of out.rows) {
    if (r.ms !== null) expect(r.equal, `${r.engine} ${r.device}`).toBe(true);
    test.info().annotations.push({ type: `bench ${r.engine}`, description: `${r.device || "-"}: ${r.ms === null ? "not run" : `${r.ms} ms (${(r.ms / out.world.pathways).toFixed(2)} ms/pathway)`}; ${r.note}` });
  }
  test.info().annotations.push({ type: "bench world", description: `${out.world.title}; ${out.cores} logical cores` });
  expect(errors).toEqual([]);
});
