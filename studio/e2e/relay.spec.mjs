// (d) Tao-S1 through the real Worker: `wrangler dev` of studio/edge serving the freshly built site, with the relay's
// upstream pointed at a mock of the service behind it. The default provider needs no setup; its answer streams; the
// page, its storage and the relay's answers never name the upstream; reasoning formats go out renamed and come back
// restored; an identity question is sent with thinking off.

import { expect, test } from "@playwright/test";
import { UPSTREAM_MODEL, UPSTREAM_VENDOR, startMockLLM } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startWorker } from "./lib/servers.mjs";

test.use({ ignoreHTTPSErrors: true });

let upstream;
let worker;
let skipReason = "";

test.beforeAll(async () => {
  test.setTimeout(240_000);
  upstream = await startMockLLM({ flavor: "upstream" });
  try {
    worker = await startWorker({ site: process.env.STUDIO_SITE, upstream: upstream.url });
  } catch (err) {
    // wrangler comes from npm at the pinned version; without the registry there is no Worker to test
    skipReason = `wrangler dev could not start: ${String(err.message).split("\n")[0]}`;
  }
});

test.afterAll(async () => {
  await worker?.stop();
  await upstream?.close();
});

const leak = new RegExp(UPSTREAM_VENDOR, "i");

test("the default model Tao-S1 answers through the relay and the page never names the upstream", async ({ page }) => {
  test.skip(Boolean(skipReason), skipReason);
  test.setTimeout(420_000);
  const watch = watchConsole(page);
  const bodies = [];
  page.on("response", async (r) => {
    if (r.url().includes("/v1/")) bodies.push(await r.text().catch(() => ""));
  });
  await seedSettings(page, {});
  await page.goto(`${worker.url}/`);
  await waitForApp(page);
  expect(await page.evaluate(() => globalThis.crossOriginIsolated)).toBe(true);

  // the relay is healthy and the model chip says Tao-S1
  await expect.poll(() => page.evaluate(() => globalThis.__studio.state.relay?.ok), { timeout: 30000 }).toBe(true);
  await expect(page.locator(".composer")).toContainText("Tao-S1");

  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E Tao-S1" }));
  await send(page, "你好");
  let answer = await waitForAnswers(page, 1);
  await expect(answer).toContainText("测试模型的回答");
  await expect(answer).toContainText("Tao-S1");

  // what the upstream received: its own model name, the key, a capped output, the relay's own fields
  const first = upstream.requests[0];
  expect(first.body.model).toBe(UPSTREAM_MODEL);
  expect(first.body.max_completion_tokens).toBeLessThanOrEqual(8192);
  expect(first.body.reasoning_split).toBe(true);

  // a second turn replays the first answer's reasoning in the relay's names; the upstream gets its own back
  await send(page, "你是谁？");
  answer = await waitForAnswers(page, 2);
  await expect(answer).toContainText("研究助手");
  const second = upstream.requests[1];
  const replayed = second.body.messages.filter((m) => m.role === "assistant").flatMap((m) => m.reasoning_details || []);
  expect(replayed.length).toBeGreaterThan(0);
  expect(replayed.every((d) => d.format === `${UPSTREAM_VENDOR}-response-v1`)).toBe(true);
  expect(second.body.thinking).toEqual({ type: "disabled" });

  // the governed question: Tao-S1 asks for the tool, the browser runs it (no runner here)
  const boot = await page.evaluate(async () => (await fetch("/runtime/boot.json")).json());
  const cdn = await page.evaluate(async (u) => { try { return (await fetch(`${u}pyodide-lock.json`)).ok; } catch { return false; } }, boot.pyodide.index_url);
  if (cdn) {
    await send(page, "甘草与甘遂同用，有哪些记载？");
    answer = await waitForAnswers(page, 3, { timeout: 300_000 });
    await expect(answer).toContainText("工具状态：succeeded");
    await expect(answer.locator(".tool-card").first()).toContainText("浏览器");
  } else {
    test.info().annotations.push({ type: "skipped part", description: "Pyodide's CDN is unreachable: the governed question through Tao-S1 was not run" });
  }

  // nothing the page shows, stores, or received from the relay names the upstream
  expect(await page.evaluate(() => document.documentElement.outerHTML)).not.toMatch(leak);
  expect(JSON.stringify(await readDb(page))).not.toMatch(leak);
  expect(JSON.stringify(await page.evaluate(() => ({ ...localStorage })))).not.toMatch(leak);
  for (const b of bodies) expect(b).not.toMatch(leak);
  const stored = (await readDb(page)).messages.filter((m) => m.role === "assistant");
  expect(JSON.stringify(stored.map((m) => m.wire))).toMatch(/Tao-response-v1/);

  await shot(page, "relay-tao-s1");
  watch.expectClean();
});
