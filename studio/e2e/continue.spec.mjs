// How a turn ends, in the page (P1.5, P2.3): an answer cut off at the output limit says so and goes on with 继续回答;
// a turn that failed after its tools ran offers 继续 beside 重试, and continuing makes one more model call, runs no
// tool again and goes on in the same message. The built site, served as Cloudflare serves it, and the scripted model
// (mock-llm.mjs: "truncated", "flaky" = 429 then ok, "permanent"); the tools here are answered by the page itself.

import { expect, test } from "@playwright/test";
import { startMockLLM, TRUNCATED } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startStatic } from "./lib/servers.mjs";

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

test.beforeEach(async () => {
  await fetch(`${mock.url}/__reset`, { method: "POST" });
});

const requests = async () => (await fetch(`${mock.url}/__requests`)).json();
// the model's own window, large enough that the page's budget never stands in for what is tested here
const custom = () => ({ custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1", context_tokens: 200000 } });
// the browser logs the model service's 429 as a failed resource: that is the scenario, not a fault of the page
const allow = [/status of 429/];

async function open(page, name) {
  await seedSettings(page, {}, custom());
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await page.evaluate((n) => globalThis.__studio.createProject({ name: n }), name);
}

test("429 then ok: 重试 and 继续 are offered; 继续 makes exactly one model call and runs no tool again", async ({ page }) => {
  const watch = watchConsole(page, { allow });
  await open(page, "E2E 继续");
  await send(page, "继续测试：桂枝和麻黄");
  const answer = await waitForAnswers(page, 1);
  const alert = answer.locator(".notice[role=alert]");
  await expect(alert).toContainText("rate limit");
  await expect(alert.getByRole("button", { name: "重试" })).toBeVisible();
  const cont = alert.locator("[data-continue]");
  await expect(cont).toHaveText("继续");
  const before = await requests();
  expect(before.map((r) => r.status ?? 200)).toEqual([200, 200, 429]);
  let db = await readDb(page);
  const failed = db.messages.find((m) => m.role === "assistant");
  expect([failed.status, failed.termination, failed.error?.permanent]).toEqual(["error", "error:model", undefined]);
  expect(db.messages.filter((m) => m.role === "tool").map((m) => m.status)).toEqual(["succeeded", "succeeded"]);
  await shot(page, "continue-offered");

  await cont.click();
  const done = page.locator(".msg--assistant:not(.is-live)");
  await expect(done.last()).toContainText("两次检索都已完成（2 个结果）。", { timeout: 60000 });
  await expect(done).toHaveCount(1);
  const after = await requests();
  expect(after.length - before.length).toBe(1);
  const sent = after.at(-1).body;
  expect(sent.messages.filter((m) => m.role === "tool").map((m) => m.tool_call_id)).toEqual(["call_cat_1", "call_cat_2"]);
  expect(sent.tools.length).toBeGreaterThan(0);
  db = await readDb(page);
  const ok = db.messages.filter((m) => m.role === "assistant");
  expect(ok.length).toBe(1);
  expect([ok[0].id, ok[0].status, ok[0].termination, ok[0].error]).toEqual([failed.id, "ok", "answered", undefined]);
  expect(db.messages.filter((m) => m.role === "tool").length, "0 repeated tool calls").toBe(2);
  await expect(page.locator(".notice[role=alert]")).toHaveCount(0);
  watch.expectClean();
});

test("a permanent error: no 重试, but 继续 from the settled results", async ({ page }) => {
  const watch = watchConsole(page, { allow });
  await open(page, "E2E 永久");
  await send(page, "永久失败测试：桂枝和麻黄");
  const answer = await waitForAnswers(page, 1);
  const alert = answer.locator(".notice[role=alert]");
  await expect(alert).toContainText("daily quota");
  await expect(alert.getByRole("button", { name: "重试" })).toHaveCount(0);
  await alert.locator("[data-continue]").click();
  await expect(page.locator(".msg--assistant:not(.is-live)").last()).toContainText("两次检索都已完成", { timeout: 60000 });
  expect((await requests()).length).toBe(4);
  const db = await readDb(page);
  expect(db.messages.filter((m) => m.role === "tool").length).toBe(2);
  watch.expectClean();
});

test("a cut-off answer says so, and 继续回答 goes on in the same message", async ({ page }) => {
  const watch = watchConsole(page);
  await open(page, "E2E 截断");
  await send(page, "截断测试：桂枝汤的组成");
  const answer = await waitForAnswers(page, 1);
  await expect(answer).toContainText(TRUNCATED[0]);
  const notice = answer.locator(".notice").filter({ hasText: "回答达到输出上限" });
  await expect(notice).toBeVisible();
  let db = await readDb(page);
  expect(db.messages.find((m) => m.role === "assistant").termination).toBe("truncated");
  await shot(page, "continue-answer-offered");
  await notice.getByRole("button", { name: "继续回答" }).click();
  await expect(page.locator(".msg--assistant:not(.is-live)").last()).toContainText(TRUNCATED[1], { timeout: 60000 });
  await expect(page.locator(".msg--assistant:not(.is-live)")).toHaveCount(1);
  await expect(page.locator(".msg--user")).toHaveCount(1);
  const last = (await requests()).at(-1).body;
  expect(last.messages.at(-1)).toEqual({ role: "user", content: "Continue your answer from exactly where it stopped, without repeating what you already wrote." });
  expect(last.messages.at(-2)).toEqual({ role: "assistant", content: TRUNCATED[0] });
  db = await readDb(page);
  const m = db.messages.filter((x) => x.role === "assistant");
  expect(m.length).toBe(1);
  // one text, as it was meant: the continuation goes on in the text it was cut off in, shown as one block
  expect(m[0].content).toBe(`${TRUNCATED[0]}${TRUNCATED[1]}`);
  expect(m[0].termination).toBe("answered");
  const prose = page.locator(".msg--assistant:not(.is-live) .prose");
  await expect(prose).toHaveCount(1);
  await expect(prose).toHaveText(`${TRUNCATED[0]}${TRUNCATED[1]}`);
  watch.expectClean();
});
