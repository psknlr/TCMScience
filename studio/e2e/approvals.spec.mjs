// (c) Approvals: run_pipeline is a confirm/job tool. The PermissionRequestCard appears before anything runs; "deny"
// gives a refused envelope that the model reads in its next round; "allow for this project" is stored in the project
// and holds across later calls and a reload.

import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { approve, newProject, openPaired, readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startRunner } from "./lib/servers.mjs";

let runner;
let mock;

test.beforeAll(async () => {
  mock = await startMockLLM();
  runner = await startRunner();
});

test.afterAll(async () => {
  await mock?.close();
  await runner?.stop();
});

const lastToolResult = (scenario) => mock.requests.filter((r) => r.scenario === scenario).at(-1).body.messages.at(-1);

test("deny is a refusal the model sees; allow for this project persists", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await openPaired(page, runner);
  await newProject(page, "E2E 批准");

  // 1. deny: nothing runs, the envelope says refused, and the model is told so
  await send(page, "请运行 RNA-seq 流程");
  const asked = await approve(page, "deny");
  expect(asked).toMatch(/run_pipeline|job\.pipeline|流程/);
  expect(asked).toMatch(/本机 Runner 上启动任务/);
  let answer = await waitForAnswers(page, 1);
  await expect(answer).toContainText("流程调用的结果：refused");
  // a person's "no" reads as not approved, not as the kernel's refusal of a claim
  await expect(answer.locator(".tool-card").first()).toContainText("未批准");
  await expect(answer.locator(".tool-card").first()).toContainText("未运行");
  // the decision is part of the run's record
  await page.locator("#insp-run").click();
  await expect(page.locator("#inspector")).toContainText("未批准 · 未运行");
  const denied = lastToolResult("pipeline");
  expect(denied.role).toBe("tool");
  expect(denied.content).toMatch(/refused/);
  await shot(page, "approvals-denied");

  // 2. allow for this project: the call goes to the runner, and the decision is stored in the project record
  await send(page, "那就再运行一次 RNA-seq 流程");
  await approve(page, "project");
  answer = await waitForAnswers(page, 2);
  const second = lastToolResult("pipeline");
  expect(second.content).not.toMatch(/: refused\b/);
  let db = await readDb(page);
  const project = db.projects.find((p) => p.name === "E2E 批准");
  expect(Object.values(project.approvals || {})).toContain("project");
  const keys = Object.keys(project.approvals);
  expect(keys.some((k) => /pipeline/.test(k)), `approval keys: ${keys.join(", ")}`).toBe(true);
  const tools = db.messages.filter((m) => m.role === "tool" && m.name === "run_pipeline").sort((a, b) => a.createdAt - b.createdAt);
  expect(tools.at(-1).envelope.receipt.where).toBe("runner");

  // 3. later calls in this project do not ask again, also after a reload
  await page.reload();
  await waitForApp(page);
  await expect.poll(() => page.evaluate(() => globalThis.__studio.state.runner.status), { timeout: 60000 }).toBe("ready");
  await send(page, "第三次运行 RNA-seq 流程");
  await waitForAnswers(page, 3);
  await expect(page.locator(".permission:not(.is-decided)")).toHaveCount(0);
  db = await readDb(page);
  expect(db.messages.filter((m) => m.role === "tool" && m.name === "run_pipeline")).toHaveLength(3);
  expect(lastToolResult("pipeline").content).not.toMatch(/: refused\b/);

  watch.expectClean();
});

test("a background job: approved, followed on its card until collected, then read with job_status", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await openPaired(page, runner);
  await newProject(page, "E2E 任务");

  await send(page, "请作为后台任务重新评估甘草与甘遂");
  expect(await approve(page, "once")).toMatch(/本机 Runner 上启动任务/);
  // the job card follows the runner's events: pending first, then collected and verified, with its files
  const job = page.locator(".job").first();
  await expect(job).toBeVisible({ timeout: 60000 });
  await expect(page.locator(".job.is-succeeded").first()).toBeVisible({ timeout: 120000 });
  await expect(page.locator(".job.is-succeeded .job__files").first()).toContainText("safety.json");
  const answer = await waitForAnswers(page, 1, { timeout: 180000 });
  await expect(answer).toContainText("任务状态：succeeded");
  // the job_status calls update the job's one card
  await expect(answer.locator(".job")).toHaveCount(1);

  const db = await readDb(page);
  const tools = db.messages.filter((m) => m.role === "tool").sort((a, b) => a.createdAt - b.createdAt);
  expect(tools.map((m) => m.name)).toEqual(["call_tool", ...Array(tools.length - 1).fill("job_status")]);
  expect(tools[0].envelope.status).toBe("job_submitted");
  expect(tools[0].envelope.job.kind).toBe("skill.run");
  expect(tools.at(-1).envelope.status).toBe("succeeded");
  // a job file opens in the inspector, fetched from the runner (cross-origin isolated page, CORP on the file)
  await page.locator(".job.is-succeeded .job__file-name", { hasText: "outputs/safety.json" }).first().click();
  await expect(page.locator("#insp-files")).toHaveAttribute("aria-selected", "true");
  const jobRow = page.locator("#inspector .job-files .file-row").filter({ hasText: "outputs/safety.json" });
  await jobRow.getByRole("button", { name: "预览" }).click();
  await expect(page.locator("#inspector .files-preview")).toContainText("甘遂");
  await shot(page, "job-succeeded-light");
  watch.expectClean();
});

test("network: off in the project means no call and no prompt; on, the hosts are asked about, then the runner's own switch decides", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await openPaired(page, runner);
  await newProject(page, "E2E 联网");

  // web access is off by default: decided in the page, no approval card
  await send(page, "检索相关文献");
  let answer = await waitForAnswers(page, 1);
  await expect(answer).toContainText("文献检索的结果：failed（未联网）");
  expect(await page.locator(".permission").count()).toBe(0);

  // on for this project: the card names the hosts; the runner's network switch is still off (its default)
  await page.locator(".composer").getByRole("button", { name: /不联网/ }).click();
  await expect(page.locator(".composer")).toContainText("联网");
  await send(page, "再检索一次文献");
  const asked = await approve(page, "once");
  expect(asked).toMatch(/europepmc|ebi\.ac\.uk|ncbi|crossref/i);
  answer = await waitForAnswers(page, 2);
  await expect(answer).toContainText("文献检索的结果：failed（未联网）");
  const db = await readDb(page);
  const last = db.messages.filter((m) => m.role === "tool").sort((a, b) => a.createdAt - b.createdAt).at(-1);
  expect(last.envelope.receipt.where).toBe("runner");
  expect(last.envelope.error.type).toBe("network_off");
  expect(last.envelope.error.hint || "").toMatch(/runner|Runner|--network/);
  watch.expectClean();
});
