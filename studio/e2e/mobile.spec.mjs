// A phone, no runner: the governed Python runs in the phone's own browser (Pyodide, the phone's CPU), with the same
// skill content hash as the native run. The CPU is throttled 4× to stand in for a mid-range phone; the timings are
// recorded as annotations, not asserted. This is Chromium's mobile emulation, not a real device: Safari on iOS is not
// covered here.

import { spawnSync } from "node:child_process";
import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { PYTHON, REPO, startStatic } from "./lib/servers.mjs";

const PHONE = {
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
  locale: "zh-CN",
  userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
};

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

test("on a phone the governed skill runs in the browser, on the phone's CPU, with the native content hash", async ({ browser }) => {
  test.setTimeout(600_000);
  const context = await browser.newContext(PHONE);
  const page = await context.newPage();
  const watch = watchConsole(page);
  const cdp = await context.newCDPSession(page);
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });

  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await page.goto(`${site.url}/`);
  await waitForApp(page);

  const boot = await page.evaluate(async () => (await fetch("/runtime/boot.json")).json());
  const reachable = await page.evaluate(async (u) => {
    try { return (await fetch(`${u}pyodide-lock.json`, { method: "GET" })).ok; } catch { return false; }
  }, boot.pyodide.index_url);
  test.skip(!reachable, `Pyodide's CDN (${boot.pyodide.index_url}) is not reachable from this browser: phone compute not tested`);

  // a phone cannot run the local runner: compute stays in the browser
  expect(await page.evaluate(() => globalThis.__studio.state.runner.status)).not.toBe("ready");
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 手机算力" }));
  const started = Date.now();
  await send(page, "甘草与甘遂同用，有哪些记载？");
  const answer = await waitForAnswers(page, 1, { timeout: 480_000 });
  const elapsed = Date.now() - started;

  await expect(page.locator(".tool-card").first()).toContainText("浏览器");
  await expect(answer).toContainText("工具状态：succeeded");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), "no horizontal page scroll").toBeLessThanOrEqual(0);

  const db = await readDb(page);
  const env = db.messages.find((m) => m.role === "tool").envelope;
  expect(env.receipt.where).toBe("browser");
  expect(env.receipt.runtime).toMatch(/^pyodide-314\.0\.7/);
  expect(env.governance.released).toBe(true);

  const r = spawnSync(PYTHON, ["-m", "tcmstudio", "call", "tcm_safety_report", "--args", JSON.stringify({ subject: "甘草", co_administered: ["甘遂"] }), "--where", "browser", "--compact"], { cwd: REPO, encoding: "utf8" });
  const native = JSON.parse(r.stdout);
  expect(env.receipt.content_hash).toBe(native.receipt.content_hash);

  test.info().annotations.push(
    { type: "phone turn (4× CPU throttle, Pyodide cold)", description: `${elapsed} ms` },
    { type: "governed call in the phone's browser", description: `${env.duration_ms} ms` },
  );
  await shot(page, "phone-browser-compute-light");
  watch.expectClean();
  await context.close();
});
