// Telemetry through the real Worker (`wrangler dev` of studio/edge with the freshly built site and its three Durable
// Objects): no page links to the repository, its homepage or the Arena; what a visitor is asked, and what follows from
// each answer; what is kept out whatever the answer; a telemetry outage the conversation never notices; a turn cut off
// by a reload; distinct visitors; and the administrator's dashboard — closed to everyone else, signed in, in both
// languages, on a phone, showing a visitor's words as text, deleting, exporting, signing out. Numbers are read from
// the admin API (with the admin token) as differences, so the tests do not depend on each other's order.

import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startWorker, workerSkipReason } from "./lib/servers.mjs";

test.use({ ignoreHTTPSErrors: true });

const ADMIN = "e2e-admin-token-0123456789abcdefghijklmnop";
const EXPORT = "e2e-export-token-0123456789abcdefghijklmn";
const LINKS = /github\.com\/psknlr|psknlr\.github\.io|TCMScience\/arena/i;
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

let upstream;
let worker;
let skipReason = "";

test.beforeAll(async () => {
  test.setTimeout(240_000);
  upstream = await startMockLLM({ flavor: "upstream" });
  try {
    worker = await startWorker({ site: process.env.STUDIO_SITE, upstream: upstream.url, vars: { ADMIN_TOKEN: ADMIN, EXPORT_TOKEN: EXPORT } });
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

test.beforeEach(() => {
  test.skip(Boolean(skipReason), skipReason);
});

// ------------------------------------------------------------------------------------------------- helpers

/** An admin API answer (the admin token, as a script would use it). */
async function api(request, path, init = {}) {
  const res = await request.fetch(`${worker.url}${path}`, { ...init, headers: { Authorization: `Bearer ${ADMIN}`, ...(init.headers || {}) } });
  const text = await res.text();
  expect(res.status(), `${path}: ${text.slice(0, 300)}`).toBe(200);
  return JSON.parse(text);
}
const summary = (request) => api(request, "/api/admin/telemetry/summary?days=7");
const records = async (request, query = "") => (await api(request, `/api/admin/telemetry/conversations?days=7&limit=100${query}`)).items;
const excluded = (s, reason) => s.usage.conversations.excluded_by.find((r) => r.reason === reason)?.n || 0;

/** The app, as a visitor opens it, once the site has said it takes telemetry (its /v1/health); a fresh project. */
async function openApp(page, { lang = "zh" } = {}) {
  await seedSettings(page, { lang });
  await page.goto(`${worker.url}/`);
  await waitForApp(page);
  await expect.poll(() => page.evaluate(() => globalThis.__studio.telemetry.config?.here === true), { timeout: 30000 }).toBe(true);
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E telemetry" }));
}

const flush = (page) => page.evaluate(() => globalThis.__studio.telemetry.flush());
const consent = (page) => page.locator("dialog.dialog--consent");

async function signIn(page) {
  await page.goto(`${worker.url}/admin/`);
  await page.fill("#token", ADMIN);
  await page.click('button[type="submit"]');
  await expect(page.locator(".kpis")).toBeVisible();
}

// ------------------------------------------------------------------------------------------------- the visitor's side

test("no page links to the repository, its homepage or the Arena: Chinese on a desktop, English on a phone", async ({ browser, request }) => {
  for (const [lang, phone] of [["zh", false], ["en", true]]) {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true, ...(phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } : {}),
    });
    const page = await context.newPage();
    const watch = watchConsole(page);
    await openApp(page, { lang });
    for (const hash of ["#/about", "#/settings/general", "#/settings/privacy", "#/catalog", "#/"]) {
      await page.evaluate((h) => { location.hash = h; }, hash);
      await page.waitForTimeout(400);
      expect(await page.locator('a[href*="github.com/psknlr" i], a[href*="psknlr.github.io" i]').count(), `${lang} ${hash}`).toBe(0);
      expect(await page.evaluate(() => document.documentElement.outerHTML), `${lang} ${hash}`).not.toMatch(LINKS);
    }
    await page.evaluate(() => { location.hash = "#/about"; });
    await expect(page.locator(".about__sec").first()).toBeVisible();
    await shot(page, `telemetry-about-${lang}${phone ? "-phone" : ""}`, { fullPage: true });
    if (phone) {
      // the notice in English, on a phone: it fits, and declining still sends the question
      const finished0 = (await summary(request)).usage.conversations.totals.finished;
      await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E phone" }));
      await send(page, "What is known about Astragalus?");
      await expect(consent(page)).toBeVisible();
      await expect(consent(page)).toContainText("Never recorded");
      const box = await consent(page).boundingBox();
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(390);
      await page.waitForTimeout(400); // the notice's opening transition, for a clear picture
      await shot(page, "telemetry-consent-en-phone");
      await consent(page).locator('[data-consent="decline"]').click();
      await waitForAnswers(page, 1);
      // counted before this browser goes (the next test measures from here)
      await expect.poll(async () => {
        await flush(page);
        return (await summary(request)).usage.conversations.totals.finished - finished0;
      }).toBe(1);
    }
    watch.expectClean();
    await context.close();
  }
});

test("consent: declined, the question is answered and nothing recorded; agreed later, the next turn is; withdrawn with deletion, it is gone", async ({ page, request }) => {
  const watch = watchConsole(page);
  const s0 = await summary(request);
  await openApp(page);
  await send(page, "黄芪的研究证据有哪些？");
  await expect(consent(page)).toBeVisible();
  await expect(consent(page)).toContainText("从不记录");
  // the refusal is the default: it has the focus
  await expect(consent(page).locator('[data-consent="decline"]')).toBeFocused();
  await page.waitForTimeout(400);
  await shot(page, "telemetry-consent-zh");
  await consent(page).locator('[data-consent="decline"]').click();
  await waitForAnswers(page, 1);
  await flush(page);
  await expect.poll(async () => excluded(await summary(request), "no_consent") - excluded(s0, "no_consent")).toBe(1);
  const owner = await page.evaluate(async () => {
    const c = JSON.parse(localStorage.getItem("tcmstudio.consent") || "{}");
    return { content: c.content, key: Boolean(c.key) };
  });
  expect(owner).toEqual({ content: false, key: false }); // declining makes no deletion key: nothing to delete

  // agreed under Settings → Privacy: the next turn of the same conversation is recorded, as the contract keeps it
  const conversation = await page.evaluate(() => location.hash);
  await page.evaluate(() => { location.hash = "#/settings/privacy"; });
  const records0 = (await records(request)).length;
  const sw = page.getByRole("switch", { name: "记录我的对话" });
  await expect(sw).toHaveAttribute("aria-checked", "false");
  await sw.click();
  await expect(sw).toHaveAttribute("aria-checked", "true");
  await expect(page.locator(".privacy__ref")).toContainText(/记录编号：[0-9a-f]{12}/);
  await page.evaluate((h) => { location.hash = h; }, conversation);
  await send(page, "黄芪多糖的研究证据？");
  await waitForAnswers(page, 2);
  await expect.poll(async () => (await records(request)).length - records0, { timeout: 30000 }).toBe(1);
  const record = (await records(request)).find((r) => r.record.question === "黄芪多糖的研究证据？")?.record;
  expect(record).toBeTruthy();
  expect(record.status).toBe("completed");
  expect(record.model).toBe("Tao-S1");
  expect(record.consent).toBe(1);
  expect(record.owner).toMatch(/^[0-9a-f]{64}$/);
  expect(record.answer.length).toBeGreaterThan(0);
  expect(JSON.stringify(record)).not.toMatch(/MiniMax|e2e-upstream-key|127\.0\.0\.1/i);
  const reference = record.owner.slice(0, 12);

  // withdrawn with deletion: the record is gone from the server, and nothing more is sent
  await page.evaluate(() => { location.hash = "#/settings/privacy"; });
  await expect(page.locator(".privacy__ref")).toContainText(reference);
  await page.getByRole("button", { name: "撤回同意并删除" }).click();
  await page.locator("dialog[open]").getByRole("button", { name: "撤回同意并删除" }).click();
  await expect.poll(async () => (await records(request, `&owner=${record.owner}`)).length).toBe(0);
  await expect(page.getByRole("switch", { name: "记录我的对话" })).toHaveAttribute("aria-checked", "false");
  const s1 = await summary(request);
  expect(s1.conversations.totals.deleted || 0).toBeGreaterThanOrEqual(1);
  watch.expectClean();
});

test("identifying details and an uploaded file keep a conversation out, though the visitor agreed", async ({ page, request }) => {
  const watch = watchConsole(page);
  const s0 = await summary(request);
  const records0 = (await records(request)).length;
  await openApp(page);
  await send(page, "患者，女，32岁，主诉头痛三天，舌红苔黄，请问是什么证？");
  await consent(page).locator('[data-consent="agree"]').click();
  await waitForAnswers(page, 1);
  // a new project, a question with a file attached
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E upload" }));
  await page.locator(".composer input[type=file]").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("黄芪 30g\n当归 10g\n") });
  await send(page, "这份笔记里提到了哪些药？");
  await waitForAnswers(page, 1);
  await flush(page);
  await expect.poll(async () => {
    const s = await summary(request);
    return [excluded(s, "sensitive_text") - excluded(s0, "sensitive_text"), excluded(s, "uploaded_file") - excluded(s0, "uploaded_file")];
  }, { timeout: 30000 }).toEqual([1, 1]);
  expect((await records(request)).length).toBe(records0);
  watch.expectClean();
});

test("when the telemetry cannot be reached, the conversation goes on; what waited is sent once it can", async ({ page, request }) => {
  // the browser's own line for a failed request carries no address: in this test every failed request is telemetry's
  const watch = watchConsole(page, { allow: [/\/api\/telemetry\//, /Failed to load resource: net::ERR_FAILED/] });
  let blocked = true;
  await page.route("**/api/telemetry/**", (route) => (blocked ? route.abort("failed") : route.continue()));
  const finished0 = (await summary(request)).usage.conversations.totals.finished;
  await openApp(page);
  await send(page, "黄芪的研究证据？");
  await consent(page).locator('[data-consent="decline"]').click();
  const answer = await waitForAnswers(page, 1);
  await expect(answer).toContainText("测试模型的回答");
  await flush(page);
  await page.waitForTimeout(1000);
  expect((await summary(request)).usage.conversations.totals.finished).toBe(finished0); // nothing got through
  blocked = false;
  await expect.poll(async () => {
    await flush(page);
    return (await summary(request)).usage.conversations.totals.finished - finished0;
  }, { timeout: 30000 }).toBe(1);
  watch.expectClean();
});

test("a reload in the middle of an answer reports that turn as interrupted", async ({ page, request }) => {
  const watch = watchConsole(page);
  const interrupted0 = (await summary(request)).usage.conversations.totals.interrupted;
  await openApp(page);
  await send(page, "慢速：请慢慢回答");
  await consent(page).locator('[data-consent="decline"]').click();
  await expect(page.locator(".msg--assistant.is-live")).toBeVisible();
  await page.waitForTimeout(1500);
  await page.reload();
  await waitForApp(page);
  // the reloaded tab finds the turn's lock gone and reports it (15 s after starting)
  await expect.poll(async () => {
    await flush(page);
    return (await summary(request)).usage.conversations.totals.interrupted - interrupted0;
  }, { timeout: 60000, intervals: [2000] }).toBe(1);
  watch.expectClean();
});

test("two visitors at once: each counted once however many pages they show, each one's agreed turn recorded under their own key", async ({ browser, request }) => {
  const s0 = await summary(request);
  const visit = async (ua, question) => {
    const context = await browser.newContext({ ignoreHTTPSErrors: true, userAgent: ua });
    const page = await context.newPage();
    const watch = watchConsole(page);
    await openApp(page);
    for (const hash of ["#/about", "#/catalog", "#/settings/general"]) {
      await page.evaluate((h) => { location.hash = h; }, hash);
      await page.waitForTimeout(150);
    }
    await page.goBack();
    await page.goBack();
    await page.goBack();
    await send(page, question);
    await consent(page).locator('[data-consent="agree"]').click();
    await waitForAnswers(page, 1);
    await expect.poll(async () => (await records(request)).some((r) => r.record.question === question), { timeout: 30000 }).toBe(true);
    await flush(page);
    watch.expectClean();
    return { context, page };
  };
  // both at the same time: their pages, their questions and their records interleave
  const [a, b] = await Promise.all([
    visit("Mozilla/5.0 (X11; Linux x86_64) E2E-Visitor-A", "当归的研究证据？（访客甲）"),
    visit("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) E2E-Visitor-B", "川芎的研究证据？（访客乙）"),
  ]);
  const mine = (await records(request)).filter((r) => /（访客[甲乙]）$/.test(r.record.question));
  expect(mine).toHaveLength(2);
  expect(new Set(mine.map((r) => r.record.owner)).size).toBe(2);
  await expect.poll(async () => {
    const s = await summary(request);
    return [s.usage.traffic.totals.visitor_days - s0.usage.traffic.totals.visitor_days, s.usage.traffic.totals.pageviews - s0.usage.traffic.totals.pageviews >= 8,
      s.usage.conversations.totals.completed - s0.usage.conversations.totals.completed];
  }).toEqual([2, true, 2]);
  // each deletes their own: the other's record stays
  for (const v of [a]) {
    await v.page.evaluate(() => { location.hash = "#/settings/privacy"; });
    await v.page.locator('[data-key="privacy-delete"]').click();
    await v.page.locator("dialog[open]").getByRole("button", { name: "删除已记录的对话" }).click();
  }
  await expect.poll(async () => (await records(request)).filter((r) => /（访客[甲乙]）$/.test(r.record.question)).map((r) => r.record.question)).toEqual(["川芎的研究证据？（访客乙）"]);
  await a.context.close();
  await b.context.close();
});

// ------------------------------------------------------------------------------------------------- the administrator's side

test("the admin side is closed to anyone not signed in, and the export token reaches aggregates only", async ({ request }) => {
  for (const path of ["/admin/dashboard.js", "/admin/charts.js", "/admin/i18n.js", "/api/admin/telemetry/summary", "/api/admin/telemetry/conversations?days=7"]) {
    expect((await request.get(`${worker.url}${path}`)).status(), path).toBe(401);
  }
  const wrong = await request.get(`${worker.url}/api/admin/telemetry/summary`, { headers: { Authorization: `Bearer ${"w".repeat(40)}` } });
  expect(wrong.status()).toBe(401);
  const bearer = { Authorization: `Bearer ${EXPORT}` };
  expect((await request.get(`${worker.url}/api/admin/telemetry/export?dataset=daily&format=csv&days=7`, { headers: bearer })).status()).toBe(200);
  for (const path of ["/api/admin/telemetry/export?dataset=conversations&format=json&days=7", "/api/admin/telemetry/export?dataset=security&format=csv&days=7",
    "/api/admin/telemetry/conversations?days=7"]) {
    expect((await request.get(`${worker.url}${path}`, { headers: bearer })).status(), path).toBe(403);
  }
  const health = await (await request.get(`${worker.url}/api/telemetry/health`)).json();
  expect(health).toMatchObject({ ok: true, service: "tcmscience-telemetry", analytics: true, conversations: true, consent_version: 1 });
  expect(JSON.stringify(health)).not.toMatch(/admin|export|token/i);
});

test("the dashboard: a wrong token refused; signed in, the numbers, a visitor's words as text, deletion, a period, English, a phone, an export, sign-out", async ({ browser, request }) => {
  // a visitor who agrees, with words that would be markup if the dashboard were careless
  const visitor = await browser.newContext({ ignoreHTTPSErrors: true });
  const vp = await visitor.newPage();
  await openApp(vp);
  const question = '<img src=x onerror="alert(1)"> 黄芪甲苷的研究证据？';
  await send(vp, question);
  await consent(vp).locator('[data-consent="agree"]').click();
  await waitForAnswers(vp, 1);
  await expect.poll(async () => (await records(request)).some((r) => r.record.question === question), { timeout: 30000 }).toBe(true);
  await flush(vp);
  await visitor.close();

  const context = await browser.newContext({ ignoreHTTPSErrors: true, locale: "zh-CN", acceptDownloads: true });
  const page = await context.newPage();
  const watch = watchConsole(page);
  const dialogs = [];
  page.on("dialog", (d) => {
    dialogs.push(d.type());
    d.type() === "confirm" ? d.accept() : d.dismiss();
  });
  await page.goto(`${worker.url}/admin/`);
  await expect(page.locator("#token")).toBeVisible();
  await page.fill("#token", "w".repeat(40));
  await page.click('button[type="submit"]');
  await expect(page.locator("#error")).toHaveText("令牌不对，请重试。");
  await page.fill("#token", ADMIN);
  await page.click('button[type="submit"]');
  await expect(page.locator(".kpis")).toBeVisible();
  const tile = (label) => page.locator(".tile").filter({ has: page.locator(".tile__label", { hasText: new RegExp(`^${label}$`) }) }).locator(".tile__value");
  expect(Number((await tile("页面浏览").textContent()).replace(/[^\d]/g, ""))).toBeGreaterThan(0);
  expect(Number((await tile("对话轮次").textContent()).replace(/[^\d]/g, ""))).toBeGreaterThan(0);
  await expect(page.locator(".chart svg").first()).toBeVisible();
  await expect(page.locator(".card", { hasText: "安全" }).first()).toContainText("管理员登录失败");
  await shot(page, "admin-dashboard-zh", { fullPage: true });

  // the records: only when asked; the visitor's words as text, never as markup
  await page.getByRole("button", { name: "显示对话记录" }).click();
  const item = page.locator(".record", { hasText: "黄芪甲苷的研究证据" });
  await expect(item).toBeVisible();
  await item.locator("summary").click();
  await expect(item.locator(".record__text").first()).toHaveText(question);
  expect(await page.locator("#content img").count()).toBe(0);
  await item.getByRole("button", { name: "删除" }).click();
  await expect(page.locator(".record", { hasText: "黄芪甲苷的研究证据" })).toHaveCount(0);
  await expect.poll(async () => (await records(request)).some((r) => r.record.question === question)).toBe(false);
  expect(dialogs).toEqual(["confirm"]); // the question's markup never ran (it would have been an alert)

  // another period, kept in the address; English
  await page.getByRole("button", { name: "30 天" }).click();
  await expect(page).toHaveURL(/\/admin\/\?days=30$/);
  await page.click("#lang");
  await expect(page.locator("#title")).toHaveText("TCMScience Studio · Analytics");
  await expect(page.getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");

  // an export, as a download: a spreadsheet's UTF-8, the counters' columns
  const [download] = await Promise.all([page.waitForEvent("download"), page.locator(".exports a", { hasText: "CSV" }).first().click()]);
  expect(download.suggestedFilename()).toMatch(/^tcmscience-daily-\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.csv$/);
  const bytes = await readFile(await download.path());
  expect([...bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  expect(bytes.toString("utf8").slice(1).split("\r\n")[0]).toBe("day,metric,a,b,n");

  // a phone, signed in by the same session: everything within the width
  const phone = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, storageState: await context.storageState() });
  const pp = await phone.newPage();
  const phoneWatch = watchConsole(pp);
  await pp.goto(`${worker.url}/admin/`);
  await expect(pp.locator(".kpis")).toBeVisible();
  await pp.waitForTimeout(500);
  expect(await pp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  await shot(pp, "admin-dashboard-phone-en", { fullPage: true });
  phoneWatch.expectClean();
  await phone.close();

  // sign out: the sign-in page again, the dashboard's code closed again; every step of the admin's is in the audit
  // trail, and the wrong token among the sources' events (a flood of those cannot push the admin's steps out)
  await page.click("#signout");
  await expect(page.locator("#token")).toBeVisible();
  expect((await page.request.get(`${worker.url}/admin/dashboard.js`)).status()).toBe(401);
  const security = (await summary(request)).security;
  const audit = security.audit.map((a) => a.kind);
  for (const kind of ["admin_login", "admin_view", "admin_delete", "admin_export"]) expect(audit, kind).toContain(kind);
  expect(audit).not.toContain("admin_auth_failed");
  expect(security.by_kind.admin_auth_failed).toBeGreaterThan(0);
  expect(security.sources.flatMap((s) => s.kinds)).toContain("admin_auth_failed");
  watch.expectClean();
  await context.close();
});

test("axe-core finds no serious or critical WCAG A/AA issue on the sign-in page and the dashboard, light and dark, desktop and phone", async ({ browser }) => {
  test.setTimeout(240_000);
  const problems = [];
  const sweep = async (page, where) => {
    await page.waitForTimeout(300);
    const found = await page.evaluate(async (tags) => {
      const r = await globalThis.axe.run(document, { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"] });
      return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
        .flatMap((v) => v.nodes.map((n) => `${v.id} (${v.impact}) ${n.target.join(" ")} — ${(n.failureSummary || "").split("\n").slice(1, 3).join(" ").trim()}`));
    }, WCAG);
    problems.push(...found.map((f) => `${where}: ${f}`));
  };
  for (const colorScheme of ["light", "dark"]) {
    for (const phone of [false, true]) {
      const context = await browser.newContext({
        ignoreHTTPSErrors: true, colorScheme, locale: "zh-CN",
        ...(phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } : { viewport: { width: 1280, height: 900 } }),
      });
      const page = await context.newPage();
      await page.addInitScript({ content: AXE });
      const at = `${colorScheme} ${phone ? "phone" : "desktop"}`;
      await page.goto(`${worker.url}/admin/`);
      await expect(page.locator("#token")).toBeVisible();
      await sweep(page, `${at} sign-in`);
      await page.fill("#token", "w".repeat(40));
      await page.click('button[type="submit"]');
      await expect(page.locator("#error")).toBeVisible();
      await sweep(page, `${at} sign-in, refused`);
      await page.fill("#token", ADMIN);
      await page.click('button[type="submit"]');
      await expect(page.locator(".kpis")).toBeVisible();
      await expect(page.locator(".chart svg").first()).toBeVisible();
      await sweep(page, `${at} dashboard`);
      await page.getByRole("button", { name: "显示对话记录" }).click();
      await page.waitForTimeout(500);
      await page.locator(".chart__toggle").first().click();
      await sweep(page, `${at} dashboard, records and a table view`);
      await context.close();
    }
  }
  expect(problems, problems.join("\n")).toEqual([]);
});
