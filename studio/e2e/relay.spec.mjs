// (d) Tao-S1 through the real Worker: `wrangler dev` of studio/edge serving the freshly built site, with the relay's
// upstream pointed at a mock of the service behind it. The default provider needs no setup; its answer streams; the
// page, its storage and the relay's answers never name the upstream; reasoning formats go out renamed and come back
// restored; an identity question is sent with thinking off. When the upstream's plan quota is spent, the page says so
// (no Retry, one upstream call), and the composer's banner says until when and offers the visitor's own model.

import { expect, test } from "@playwright/test";
import { MODEL, UPSTREAM_MODEL, UPSTREAM_VENDOR, startMockLLM } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { pyodidePartSkipped, startWorker, workerSkipReason } from "./lib/servers.mjs";

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
    // only an unreachable npm registry (never in CI) is a skip; a Worker that cannot boot fails here, with wrangler's log
    skipReason = workerSkipReason(err);
    if (!skipReason) throw err;
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
  // the site takes conversation records (docs/TELEMETRY.md): the notice comes first; declined, the question goes all the same
  await page.locator('dialog.dialog--consent [data-consent="decline"]').click();
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
  // a skipped part outside CI; in CI an unreachable CDN is a failure, as for the browser specs
  const skipped = pyodidePartSkipped(cdn, boot.pyodide.index_url, "the governed question through Tao-S1");
  if (!skipped) {
    await send(page, "甘草与甘遂同用，有哪些记载？");
    answer = await waitForAnswers(page, 3, { timeout: 300_000 });
    await expect(answer).toContainText("工具状态：succeeded");
    await expect(answer.locator(".tool-card").first()).toContainText("浏览器");
  } else {
    test.info().annotations.push({ type: "skipped part", description: skipped });
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

test("a spent upstream quota: said as such, with no Retry and one upstream call; the banner says until when and switches on a click", async ({ page }) => {
  test.skip(Boolean(skipReason), skipReason);
  test.setTimeout(420_000);
  // a Worker of its own: the breaker this opens lives in its Limiter object, apart from the other test's
  const paused = await startWorker({ site: process.env.STUDIO_SITE, upstream: upstream.url });
  const own = await startMockLLM(); // the visitor's own model, a custom endpoint set up in this browser
  try {
    // the relay's answer is a 503 the page reads as a model error: expected in the console, nothing else
    const watch = watchConsole(page, { allow: [/the server responded with a status of 503/] });
    await seedSettings(page, {
      customProviders: [{ id: "custom-e2e", label: "E2E mock", format: "openai", base_url: `${own.url}/v1`, model: MODEL, headers: {}, extra_body: {}, max_tokens_param: "max_tokens", stream_usage: true }],
      models: { "custom-e2e": MODEL },
    });
    await page.goto(`${paused.url}/`);
    await waitForApp(page);
    await expect.poll(() => page.evaluate(() => globalThis.__studio.state.relay?.ok), { timeout: 30000 }).toBe(true);
    await expect(page.locator(".composer__banner")).toBeHidden();
    await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E quota" }));

    const before = upstream.requests.filter((r) => r.scenario === "quota").length;
    await send(page, "额度用完了吗？");
    await page.locator('dialog.dialog--consent [data-consent="decline"]').click();
    const answer = await waitForAnswers(page, 1);
    // told as a spent quota, not "busy"; retrying cannot help, so there is no Retry; the upstream was asked once
    await expect(answer.locator(".notice")).toContainText("额度");
    await expect(answer.locator(".notice")).not.toContainText("繁忙");
    await expect(answer.getByRole("button", { name: "重试" })).toHaveCount(0);
    expect(upstream.requests.filter((r) => r.scenario === "quota").length - before).toBe(1);

    // the page asks the relay again: its health says why and until when, and the composer says so before the next send
    await expect.poll(() => page.evaluate(() => globalThis.__studio.state.relay?.reason), { timeout: 30000 }).toBe("upstream_quota");
    const banner = page.locator(".composer__banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/Tao-S1 在 (\d{2}-\d{2} )?\d{2}:\d{2} 前不可用/);
    const until = await page.evaluate(() => globalThis.__studio.state.relay.until);
    expect(Date.parse(until)).toBeGreaterThan(Date.now() + 9 * 60000);
    // nothing switched by itself: the visitor's own model has not been asked
    expect(own.requests.length).toBe(0);
    const swap = banner.locator('[data-key="relay-switch"]');
    await expect(swap).toContainText(`本对话改用 ${MODEL}`);
    await shot(page, "relay-quota-banner");

    // one click: this conversation's next turn goes to the visitor's own model; the relay is not asked
    const upstreamBefore = upstream.requests.length;
    await swap.click();
    await expect(banner).toBeHidden();
    await expect(page.locator(".composer")).toContainText(MODEL);
    await send(page, "你好");
    await expect(await waitForAnswers(page, 2)).toContainText("测试模型的回答");
    expect(own.requests.length).toBe(1);
    expect(upstream.requests.length).toBe(upstreamBefore);
    watch.expectClean();
  } finally {
    await own.close();
    await paused.stop();
  }
});
