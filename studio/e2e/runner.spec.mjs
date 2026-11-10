// (a) Runner mode: the app served by the real local runner, a custom OpenAI-compatible model (the scripted mock), and
// a governed skill executed by the runner's Python. The thread must show what ran where, the kernel's verdicts, the
// release state and the citations; the inspector must hold the run; a reload keeps the conversation; an edit makes a
// branch; and the stop button ends a streaming answer.

import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { approve, newProject, openPaired, readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startRunner } from "./lib/servers.mjs";

const QUESTION = "甘草与甘遂同用，有哪些记载？";

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

test("a governed skill runs on the runner and the thread shows its verdict", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  // the runner prints this link: the page pairs with the runner that serves it
  await openPaired(page, runner);
  // Settings → Compute shows what the runner reported: its CPU and its network switch as the runner has it
  await page.evaluate(() => { location.hash = "#/settings/compute"; });
  const reported = await page.evaluate(() => globalThis.__studio.state.runner);
  const runnerCard = page.locator(".rt-card.is-ready").filter({ hasText: "127.0.0.1" });
  await expect(runnerCard).not.toContainText("CPU 信息不可用");
  if (reported.info.cpu.model) await expect(runnerCard).toContainText(reported.info.cpu.model);
  await expect(runnerCard.getByRole("switch", { name: "Runner 联网" })).toHaveAttribute("aria-checked", String(Boolean(reported.settings.network?.enabled)));
  await newProject(page, "E2E 配伍");

  await send(page, QUESTION);
  // the first runner call of a project asks first (ARCHITECTURE decision 5): the project's one question
  expect(await approve(page, "allow")).toMatch(/第一次在本机 Runner 上运行/);
  const answer = await waitForAnswers(page, 1);

  // the model asked for the governed skill; the runner ran it
  const card = page.locator(".tool-card").first();
  await expect(card).toBeVisible();
  await expect(card).toContainText("tcm_safety_report");
  await expect(card).toContainText(/本机 Runner/);
  await expect(answer).toContainText("十八反");
  await expect(answer).toContainText("工具状态：succeeded");
  // the kernel's verdicts: the claim card (kind, verdict, the evidence it rests on) and the artifact with its release
  const claim = answer.locator(".claim").first();
  await expect(claim).toContainText("传统应用");
  await expect(claim).toContainText(/允许/);
  await expect(claim.locator("button.cite").first()).toBeVisible();
  const artifact = answer.locator(".artifact").first();
  await expect(artifact).toContainText("assess-tcm-safety");
  await expect(artifact).toContainText("发布状态 6/6");
  await expect(artifact).toContainText("已准予发布");
  await expect(artifact).toContainText(/ABSENCE OF A RECORD IS NOT EVIDENCE OF SAFETY/);
  // the citation in the answer is a chip that resolves against the envelope
  const chip = answer.locator(".msg__prose button.cite:not(.is-unknown)").first();
  await expect(chip).toHaveText("E1");
  await chip.click();
  await expect(page.locator("#insp-evidence")).toHaveAttribute("aria-selected", "true");

  // the request the model saw the second time ends with the real envelope's text
  const second = mock.requests.filter((r) => r.scenario === "safety")[1];
  expect(second, "the model was called again with the tool result").toBeTruthy();
  const toolMsg = second.body.messages.at(-1);
  expect(toolMsg.role).toBe("tool");
  expect(toolMsg.content).toMatch(/skill\.assess-tcm-safety: succeeded/);
  expect(toolMsg.content).toMatch(/Absence of a record is not evidence of safety/);
  // the custom endpoint is on loopback: with the runner connected, model calls go through its proxy
  expect(second.via).toBe("proxy");

  // the stored envelope: executed by the runner, governed, released
  const db = await readDb(page);
  const tool = db.messages.find((m) => m.role === "tool");
  expect(tool.envelope.receipt.where).toBe("runner");
  expect(tool.envelope.via).toBe("skill.assess-tcm-safety");
  expect(tool.envelope.governance.released).toBe(true);
  expect(tool.envelope.receipt.content_hash).toMatch(/^[0-9a-f]{64}$/);
  test.info().annotations.push({ type: "content_hash", description: tool.envelope.receipt.content_hash });

  await shot(page, "runner-answer-light");

  // the inspector's tabs hold the run
  for (const [id, text] of [["#insp-run", /安全性与配伍禁忌[\s\S]*本机 Runner/], ["#insp-evidence", /甘遂|safety/], ["#insp-claims", /甘草/], ["#insp-files", /safety\.json/], ["#insp-provenance", /skill\.assess-tcm-safety[\s\S]*本机 Runner[\s\S]*已持久保存/]]) {
    await page.locator(id).click();
    await expect(page.locator("#inspector")).toContainText(text);
  }
  await shot(page, "runner-inspector-provenance");
  // an output file previews from the envelope itself
  await page.locator("#insp-files").click();
  await page.locator("#inspector .file-row").filter({ hasText: "safety.json" }).first().getByRole("button", { name: "预览" }).click();
  await expect(page.locator("#inspector .files-preview")).toContainText("shibafan");

  // the same thread in English: every string translated
  await page.evaluate(async () => (await import("/js/core/i18n.js")).setLang("en"));
  await expect(page.locator(".composer")).toContainText(/Runner|runner/);
  expect((await page.locator("body").innerText()).match(/\b(ui|core|runtime)\.[a-z_]+\.[a-z_.]+\b/)?.[0] ?? null).toBeNull();
  await shot(page, "runner-answer-en");
  await page.evaluate(async () => (await import("/js/core/i18n.js")).setLang("zh"));

  // a reload keeps the conversation (IndexedDB)
  await page.reload();
  await waitForApp(page);
  await waitForAnswers(page, 1);
  await expect(page.locator(".msg--assistant").first()).toContainText("十八反");
  await expect(page.locator(".tool-card").first()).toContainText("tcm_safety_report");

  // the same thread in the dark theme, and on a phone
  await page.evaluate(() => globalThis.__studio.setSetting({ theme: "dark" }));
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.waitForTimeout(500); // colour transitions
  await shot(page, "runner-answer-dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => globalThis.__studio.state.layout)).toBe("mobile");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await shot(page, "runner-answer-mobile-dark");
  await page.evaluate(() => globalThis.__studio.setSetting({ theme: "light" }));
  await page.waitForTimeout(500);
  await shot(page, "runner-answer-mobile-light");

  watch.expectClean();
});

test("editing a question makes a branch; stop ends a streaming answer", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await openPaired(page, runner);
  await newProject(page, "E2E 分支");

  await send(page, "你好");
  await waitForAnswers(page, 1);

  // edit the question: a sibling with the same parent, answered anew, with a ‹1/2› switcher
  const user = page.locator(".msg--user").first();
  await user.hover();
  await user.getByRole("button", { name: /编辑|Edit/ }).click();
  const editor = page.locator(".msg--user textarea").first();
  await editor.fill("你好，请简单介绍一下");
  await editor.press("Enter");
  await waitForAnswers(page, 1);
  await expect(page.locator(".msg--user").first()).toContainText("请简单介绍");
  await expect(page.getByText("2/2")).toBeVisible();
  const db = await readDb(page);
  const users = db.messages.filter((m) => m.role === "user");
  expect(users).toHaveLength(2);
  expect(users[0].parentId ?? null).toBe(users[1].parentId ?? null);

  // stop mid-stream: the partial answer is kept and marked stopped
  await send(page, "请慢速写一段长文");
  await expect(page.locator(".msg--assistant.is-live")).toContainText("四气五味", { timeout: 30000 });
  await page.locator(".composer__send").click();
  await expect(page.locator(".msg--assistant.is-live")).toHaveCount(0, { timeout: 15000 });
  const stopped = (await readDb(page)).messages.filter((m) => m.role === "assistant" && m.status === "stopped" && !m.draft);
  expect(stopped.length).toBeGreaterThan(0);
  expect(stopped.at(-1).content.length).toBeGreaterThan(0);
  const slow = mock.requests.filter((r) => r.scenario === "slow").at(-1);
  await expect.poll(() => slow.aborted, { timeout: 10000 }).toBe(true);
  await shot(page, "runner-stopped");

  watch.expectClean();
});

test("a result is handed to the next call by reference: the runner gets the data, the thread keeps the reference", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await openPaired(page, runner);
  await newProject(page, "E2E 引用");
  await send(page, "请按引用把方剂的药味交给交集工具");
  await approve(page, "allow");
  const answer = await waitForAnswers(page, 1);
  // the four herbs of 葛根芩连汤 were never typed by the model: the overlap with the control set is 甘草
  await expect(answer).toContainText("引用调用的结果：succeeded；共同 1 项：甘草");
  await expect(answer).toContainText(/R\d+ repository_records\.records\[0\]\.components · name: 4 items/);
  const db = await readDb(page);
  const tools = db.messages.filter((m) => m.role === "tool").sort((a, b) => a.createdAt - b.createdAt);
  expect(tools.map((m) => m.name)).toEqual(["tcm_formula", "call_tool"]);
  expect(tools[0].ref).toMatch(/^R\d+$/);
  // the stored call keeps the reference as the model wrote it, and what it gave; the tool ran on the data
  expect(tools[1].args.arguments.sets["方剂"]).toEqual({ $ref: tools[0].ref, path: "repository_records.records[0].components", field: "name" });
  expect(tools[1].refs).toEqual([{ ref: tools[0].ref, path: "repository_records.records[0].components", field: "name", items: 4 }]);
  expect(tools[1].envelope.result.sizes).toEqual({ "方剂": 4, "对照": 3 });
  expect(tools[1].envelope.result.common_to_all).toEqual({ count: 1, items: ["甘草"] });
  expect(tools[1].envelope.receipt.where).toBe("runner");
  // opened, the card shows the reference as what it points to, not as a closed object
  const card = answer.locator(".tool-card").nth(1);
  await card.locator(".tool-card__head").click();
  await expect(card).toContainText(`方剂: →${tools[0].ref} repository_records.records[0].components · name`);
  // what the model was sent: the status line first, the label under it
  const sent = mock.requests.filter((r) => r.scenario === "reference").at(-1).body.messages.filter((m) => m.role === "tool");
  expect(sent[0].content.split("\n").slice(0, 2)).toEqual(["tcm_formula → native.tcm_formula: succeeded", `Ref: ${tools[0].ref}`]);
  watch.expectClean();
});

test("a project file with a Chinese name goes to the runner with its name and hash intact", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await openPaired(page, runner);
  await newProject(page, "E2E 文件");
  const bytes = Buffer.from("sample,condition\nS1,对照\nS2,葛根芩连汤\n", "utf8");
  await page.locator("input[type=file]:not([aria-hidden])").first().setInputFiles({ name: "样本表.csv", mimeType: "text/csv", buffer: bytes });
  const row = page.locator(".file-row").filter({ hasText: "样本表.csv" });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "发送到本机 Runner" }).click();
  // the project's one question: confirming it is the project-wide approval
  await expect(page.getByRole("dialog")).toContainText("本项目只问这一次");
  await page.getByRole("dialog").getByRole("button", { name: "发送", exact: true }).click();
  await expect(row).toContainText("浏览器 + 本机 Runner");
  const { createHash } = await import("node:crypto");
  const uploads = (await (await fetch(`${runner.url}/api/uploads`)).json()).uploads;
  const up = uploads.find((u) => u.name === "样本表.csv");
  expect(up, JSON.stringify(uploads)).toBeTruthy();
  expect(up.sha256).toBe(createHash("sha256").update(bytes).digest("hex"));
  let db = await readDb(page);
  const rec = db.files.find((f) => f.name === "样本表.csv");
  expect(rec.runnerUploadId).toBe(up.id);
  expect(db.projects.find((p) => p.name === "E2E 文件").approvals["*"]).toBe("project");

  // a second file of the project goes without asking again
  await page.locator("input[type=file]:not([aria-hidden])").first().setInputFiles({ name: "计数.tsv", mimeType: "text/tab-separated-values", buffer: Buffer.from("gene\tS1\nA\t1\n", "utf8") });
  const second = page.locator(".file-row").filter({ hasText: "计数.tsv" });
  await expect(second).toBeVisible();
  await second.getByRole("button", { name: "发送到本机 Runner" }).click();
  await expect(second).toContainText("浏览器 + 本机 Runner");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  db = await readDb(page);
  const kept = db.files.find((f) => f.name === "计数.tsv");
  expect(kept.runnerUploadId).toBeTruthy();

  // deleting a file asks nothing: it goes at once, and Undo puts it back as it was (same id and hash)
  await second.getByRole("button", { name: "删除" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".file-row").filter({ hasText: "计数.tsv" })).toHaveCount(0);
  expect((await readDb(page)).files.some((f) => f.name === "计数.tsv")).toBe(false);
  const toast = page.locator(".toast").filter({ hasText: "已从本浏览器删除「计数.tsv」" });
  await expect(toast).toBeVisible();
  await toast.getByRole("button", { name: "撤销" }).click();
  await expect(page.locator(".file-row").filter({ hasText: "计数.tsv" })).toBeVisible();
  const back = (await readDb(page)).files.find((f) => f.name === "计数.tsv");
  expect([back.id, back.sha256, back.runnerUploadId, back.createdAt]).toEqual([kept.id, kept.sha256, kept.runnerUploadId, kept.createdAt]);
  watch.expectClean();
});
