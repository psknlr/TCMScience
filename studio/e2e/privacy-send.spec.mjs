// The check before a question leaves this computer for a model (web/js/ui/phidialog.js), in the built site served the
// way Cloudflare serves it: Tao-S1 (the default, through the same-origin relay, here answered by the scripted model
// e2e/mock-llm.mjs) is a model outside the computer, so an ID number opens the dialog before anything is sent; each
// choice shows the exact text it sends, and what the model then receives is read from the mock's request bodies. A
// patient's case with nothing to redact has no redacted choice; going back leaves the text in the box; "as written" is
// not asked again in that conversation; research questions (one about patients over 65 among them) are sent at once; a
// model on this computer (a custom endpoint on 127.0.0.1) is never asked about. The dialog passes axe-core (WCAG A/AA,
// nothing serious or critical).

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { readDb, seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startStatic } from "./lib/servers.mjs";

const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const ID_CASE = "患者，女，32岁，身份证号110101199003071234";
const CASE = "患者张三，男，45岁，主诉胃痛";

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

/** Tao-S1's relay on the page's own origin, answered by the scripted model (the static site has no relay of its own). */
async function relayToMock(page) {
  await page.route(`${site.url}/v1/chat/completions`, async (route) => {
    const response = await route.fetch({ url: `${mock.url}/v1/chat/completions` });
    await route.fulfill({ response });
  });
}

const dialog = (page) => page.locator("dialog.dialog--phi");
const lastBody = () => JSON.stringify(mock.requests.at(-1)?.body ?? null);

async function axeProblems(page) {
  await page.waitForTimeout(300); // the dialog's opening transition
  return page.evaluate(async (tags) => {
    const r = await globalThis.axe.run(document.querySelector("dialog.dialog--phi"), { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"] });
    return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").flatMap((v) => v.nodes.map((n) => `${v.id} ${n.target.join(" ")}`));
  }, WCAG);
}

test("Tao-S1: an ID number is held for the person's choice; redacted, the model receives the marker and no 18-digit run", async ({ page }) => {
  test.setTimeout(240_000);
  const watch = watchConsole(page);
  await page.addInitScript({ content: AXE });
  await seedSettings(page, {});
  await relayToMock(page);
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await expect(page.locator(".composer")).toContainText("Tao-S1");
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 隐私" }));
  const sent0 = mock.requests.length;

  await send(page, ID_CASE);
  await expect(dialog(page)).toBeVisible();
  expect(mock.requests.length, "nothing is sent while the dialog is open").toBe(sent0);
  await expect(dialog(page)).toContainText("Tao-S1 不在你的电脑上运行");
  await expect(dialog(page).locator("[data-phi]")).toHaveText(["返回修改", "按原文发送", "隐去后发送"]);
  await expect(dialog(page).locator('[data-phi="edit"]')).toBeFocused();
  await expect(dialog(page).locator('.phi__choice[data-choice="redacted"] pre')).toHaveText("患者，女，32岁，身份证号[redacted:id_number]");
  await expect(dialog(page).locator('.phi__choice[data-choice="redacted"]')).toContainText("正文里的人名不会被隐去");
  await expect(dialog(page).locator('.phi__choice[data-choice="as_written"] pre')).toHaveText(ID_CASE);
  expect(await axeProblems(page)).toEqual([]);
  await shot(page, "privacy-send-dialog");

  await dialog(page).locator('[data-phi="redacted"]').click();
  await expect(dialog(page)).toHaveCount(0);
  await waitForAnswers(page, 1);
  expect(mock.requests.length).toBe(sent0 + 1);
  expect(lastBody()).toContain("[redacted:id_number]");
  expect(lastBody()).not.toMatch(/\d{18}/);
  const stored = (await readDb(page)).messages.filter((m) => m.role === "user");
  expect(stored.map((m) => [m.content, m.phi])).toEqual([["患者，女，32岁，身份证号[redacted:id_number]", { reason: "identifier", choice: "redacted" }]]);
  expect(JSON.stringify(await readDb(page))).not.toContain("110101199003071234");

  // a patient's case with nothing to redact: no redacted choice; going back sends nothing and keeps the text
  await send(page, CASE);
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).locator("[data-phi]")).toHaveText(["返回修改", "按原文发送"]);
  await dialog(page).locator('[data-phi="edit"]').click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(page.locator("#composer-input")).toHaveValue(CASE);
  expect(mock.requests.length).toBe(sent0 + 1);

  // sent as written: the model gets the text as typed, and this conversation is not asked again
  await page.locator("#composer-input").press("Enter");
  await dialog(page).locator('[data-phi="as_written"]').click();
  await waitForAnswers(page, 2);
  expect(lastBody()).toContain("患者张三");
  await send(page, "患者，男，60岁，咳嗽三天，舌红苔黄");
  await waitForAnswers(page, 3);
  await expect(dialog(page)).toHaveCount(0);
  expect(mock.requests.length).toBe(sent0 + 3);

  // research questions go at once, in a new conversation too
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 研究" }));
  for (const [i, q] of ["舌红苔黄，脉数是什么证？", "黄芪的靶点", "PubChem CID 13912345678 berberine", "65岁以上患者使用黄芪注射液的不良反应"].entries()) {
    await send(page, q);
    await waitForAnswers(page, i + 1);
    await expect(dialog(page)).toHaveCount(0);
  }
  expect(lastBody()).toContain("13912345678");
  watch.expectClean();
});

test("English, and a model on this computer: the dialog in English on Tao-S1; a 127.0.0.1 endpoint gets the text with no dialog", async ({ browser }) => {
  test.setTimeout(240_000);
  // English: the same dialog, its words in English
  const en = await browser.newContext();
  let page = await en.newPage();
  let watch = watchConsole(page);
  await page.addInitScript({ content: AXE });
  await seedSettings(page, { lang: "en" });
  await relayToMock(page);
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E privacy" }));
  await send(page, "Patient Alice Smith MRN 04851923 hepatotoxicity");
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).locator("[data-phi]")).toHaveText(["Back to edit", "Send as written", "Send redacted"]);
  await expect(dialog(page).locator('.phi__choice[data-choice="redacted"] pre')).toHaveText("Patient Alice Smith MRN [redacted:record_number] hepatotoxicity");
  await expect(dialog(page).locator('.phi__choice[data-choice="redacted"]')).toContainText("A name in running text stays");
  expect(await axeProblems(page)).toEqual([]);
  await page.keyboard.press("Escape"); // closing is going back: nothing sent
  await expect(dialog(page)).toHaveCount(0);
  await expect(page.locator("#composer-input")).toHaveValue("Patient Alice Smith MRN 04851923 hepatotoxicity");
  watch.expectClean();
  await en.close();

  // a model on this computer: the scripted model as a custom endpoint on 127.0.0.1
  const local = await browser.newContext();
  page = await local.newPage();
  watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 本机" }));
  const sent0 = mock.requests.length;
  await send(page, ID_CASE);
  await waitForAnswers(page, 1);
  await expect(dialog(page)).toHaveCount(0);
  expect(mock.requests.length).toBe(sent0 + 1);
  expect(lastBody()).toContain("110101199003071234");
  watch.expectClean();
  await local.close();
});
