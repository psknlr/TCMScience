// (b) Browser mode: no runner. The built site is served the way Cloudflare serves it (its own _headers, so the page is
// cross-origin isolated), Pyodide boots from jsDelivr, and the same governed question runs in the browser with the
// same skill content hash and the same output bytes as the native run. Then the same with Pyodide self-hosted
// (docs/V2.md §15): a site built with --self-host-pyodide boots without a single request off its origin.

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { PYTHON, REPO, STUDIO, scratch, startStatic } from "./lib/servers.mjs";

let site;
let mock;

test.beforeAll(async () => {
  mock = await startMockLLM();
  site = await startStatic(process.env.STUDIO_SITE);
});

test.afterAll(async () => {
  await mock?.close();
  await site?.stop();
});

function nativeEnvelope(tool, args) {
  const r = spawnSync(PYTHON, ["-m", "tcmstudio", "call", tool, "--args", JSON.stringify(args), "--where", "browser", "--compact"], { cwd: REPO, encoding: "utf8" });
  return JSON.parse(r.stdout);
}

test("the governed skill runs in the browser (Pyodide) with the native content hash", async ({ page }) => {
  test.setTimeout(420_000);
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  expect(await page.evaluate(() => globalThis.crossOriginIsolated), "the site's _headers isolate the page").toBe(true);

  // Pyodide comes from jsDelivr: without it there is nothing to test here
  const boot = await page.evaluate(async () => (await fetch("/runtime/boot.json")).json());
  const reachable = await page.evaluate(async (u) => {
    try { return (await fetch(`${u}pyodide-lock.json`, { method: "GET" })).ok; } catch { return false; }
  }, boot.pyodide.index_url);
  test.skip(!reachable, `Pyodide's CDN (${boot.pyodide.index_url}) is not reachable from this browser: browser mode not tested`);

  // no runner: the compute chip says the browser
  expect(await page.evaluate(() => globalThis.__studio.state.runner.status)).not.toBe("ready");
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 浏览器" }));
  await send(page, "甘草与甘遂同用，有哪些记载？");
  const answer = await waitForAnswers(page, 1, { timeout: 300_000 });

  const card = page.locator(".tool-card").first();
  await expect(card).toContainText("tcm_safety_report");
  await expect(card).toContainText("浏览器");
  await expect(answer).toContainText("工具状态：succeeded");

  const db = await readDb(page);
  const env = db.messages.find((m) => m.role === "tool").envelope;
  expect(env.receipt.where).toBe("browser");
  expect(env.receipt.runtime).toMatch(/^pyodide-314\.0\.7/);
  expect(env.governance.released).toBe(true);

  const native = nativeEnvelope("tcm_safety_report", { subject: "甘草", co_administered: ["甘遂"] });
  expect(native.status).toBe("succeeded");
  expect(env.receipt.content_hash).toBe(native.receipt.content_hash);
  expect(env.receipt.input_sha256).toBe(native.receipt.input_sha256);
  const outs = (e) => Object.fromEntries((e.governance.outputs || []).map((o) => [o.path, o.sha256]));
  expect(outs(env)).toEqual(outs(native));
  test.info().annotations.push({ type: "content_hash", description: env.receipt.content_hash });

  // the compute chip follows the runtime: Python is loaded now
  expect(await page.evaluate(() => globalThis.__studio.state.browser.status)).toBe("ready");
  await shot(page, "browser-answer-light");
  watch.expectClean();
});

test("self-hosted Pyodide: the runtime boots from the site itself, and a governed call and a corpus read make no runtime request off the origin", async ({ browser }) => {
  test.setTimeout(600_000);
  const out = path.join(scratch("tcmstudio-selfhost"), "site");
  const built = spawnSync(PYTHON, [path.join(STUDIO, "scripts", "build_web.py"), "--out", out, "--self-host-pyodide"], { cwd: REPO, encoding: "utf8" });
  // the pinned files come from jsDelivr once (then from the local cache): without either there is nothing to host
  test.skip(built.status !== 0 && /could not download/.test(built.stderr), `Pyodide could not be downloaded for self-hosting: ${built.stderr.split("\n").find((l) => /could not download/.test(l))}`);
  expect(built.status, built.stderr).toBe(0);
  const hosted = await startStatic(out);
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const origin = new URL(hosted.url).origin;
    const requests = [];
    const missing = [];
    // the app's web fonts (css/fonts.css) are not the runtime's: they are listed apart, below
    const fonts = [];
    context.on("request", (r) => (r.resourceType() === "font" ? fonts : requests).push(r.url()));
    context.on("response", (r) => { if (r.status() === 404) missing.push(new URL(r.url()).pathname); });
    // the accelerator host is another part of v2; a build without it answers 404 for it, on this origin
    const accelBuilt = existsSync(path.join(out, "js", "accel", "host.js"));
    const watch = watchConsole(page, { allow: accelBuilt ? [] : [/Failed to load resource: the server responded with a status of 404/] });
    await seedSettings(page);
    await page.goto(`${hosted.url}/`);
    await waitForApp(page);
    expect(await page.evaluate(() => globalThis.crossOriginIsolated)).toBe(true);
    const boot = await page.evaluate(async () => (await fetch("/runtime/boot.json")).json());
    expect(boot.pyodide.index_url).toBe("pyodide/314.0.7/");

    const res = await page.evaluate(async () => {
      const rt = globalThis.__studio.runtimes.browser;
      const env = await rt.call("tcm_safety_report", { subject: "甘草", co_administered: ["甘遂"] }, { project_id: "e2e-selfhost" });
      const corpus = await rt.call("corpus_info", {});
      const info = await rt.info();
      return {
        status: env.status, released: env.governance?.released, content_hash: env.receipt?.content_hash, runtime: env.receipt?.runtime,
        outputs: Object.fromEntries((env.governance?.outputs || []).map((o) => [o.path, o.sha256])),
        corpus: { status: corpus.status, error: corpus.error, snapshot: corpus.receipt?.corpus?.snapshot_id ?? corpus.result?.snapshot_id ?? null },
        index: info.pyodide?.index_url,
      };
    });
    expect(res.status).toBe("succeeded");
    expect(res.released).toBe(true);
    expect(res.runtime).toMatch(/^pyodide-314\.0\.7/);
    expect(res.index).toBe(`${origin}/pyodide/314.0.7/`);
    const native = nativeEnvelope("tcm_safety_report", { subject: "甘草", co_administered: ["甘遂"] });
    expect(res.content_hash).toBe(native.receipt.content_hash);
    expect(res.outputs).toEqual(Object.fromEntries((native.governance.outputs || []).map((o) => [o.path, o.sha256])));
    expect(res.corpus.status, JSON.stringify(res.corpus.error)).toBe("succeeded");
    expect(res.corpus.snapshot).toBe(boot.corpus.snapshot_id);

    // the worker's own requests are seen here (so the next check is not vacuous) …
    const paths = requests.map((u) => new URL(u).pathname);
    for (const p of ["/pyodide/314.0.7/pyodide.mjs", "/pyodide/314.0.7/pyodide.asm.wasm", "/pyodide/314.0.7/python_stdlib.zip", "/pyodide/314.0.7/pyodide-lock.json"]) {
      expect(paths, `${p} was requested`).toContain(p);
    }
    expect(paths.some((p) => /^\/pyodide\/314\.0\.7\/pyyaml-.*\.whl$/.test(p)), "a wheel from the site").toBe(true);
    expect(paths.some((p) => p.startsWith("/corpus/")), "the corpus from the site").toBe(true);
    // … and none of them left the origin
    expect(requests.filter((u) => !u.startsWith("data:") && !u.startsWith("blob:") && new URL(u).origin !== origin)).toEqual([]);
    expect(missing.filter((p) => p !== "/js/accel/host.js" || accelBuilt), "nothing else was missing").toEqual([]);
    test.info().annotations.push({ type: "requests (all same-origin)", description: String(requests.length) });
    const offFonts = fonts.filter((u) => new URL(u).origin !== origin);
    if (offFonts.length) test.info().annotations.push({ type: "web fonts from another origin (css/fonts.css, not the runtime)", description: [...new Set(offFonts.map((u) => new URL(u).host))].join(", ") });
    watch.expectClean();
  } finally {
    await context.close();
    await hosted.stop();
  }
});
