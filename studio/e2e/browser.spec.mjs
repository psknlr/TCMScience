// (b) Browser mode: no runner. The built site is served the way Cloudflare serves it (its own _headers, so the page is
// cross-origin isolated), Pyodide boots from jsDelivr, and the same governed question runs in the browser with the
// same skill content hash and the same output bytes as the native run.

import { spawnSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { PYTHON, REPO, startStatic } from "./lib/servers.mjs";

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
