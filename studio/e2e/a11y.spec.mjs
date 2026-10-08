// (f) Keyboard and screen-reader behaviour that the other specs only glance at (DESIGN §8, WCAG 2.2 AA): the skip
// links activated, not just focused; an input method's Enter and Escape left to the input method; where the focus goes
// after the first send, Stop, an approval decision, and Esc on a menu and on a drawer; what the live regions say; and an
// axe-core sweep of the main views (light and dark, desktop and phone) that fails on serious and critical issues.
// The site is served as Cloudflare serves it; the approval needs the real runner.

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { newProject, openPaired, seedSettings, send, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
import { startRunner, startStatic } from "./lib/servers.mjs";

let site;
let mock;
let runner;

test.beforeAll(async () => {
  mock = await startMockLLM();
  site = await startStatic(process.env.STUDIO_SITE);
  runner = await startRunner();
});

test.afterAll(async () => {
  await mock?.close();
  await site?.stop();
  await runner?.stop();
});

const custom = () => ({ custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });

/** What has the focus: "BODY", or {id, cls, label, in: the region it is in}. */
function focused(page) {
  return page.evaluate(() => {
    const a = document.activeElement;
    if (!a || a === document.body || !a.isConnected) return "BODY";
    const region = ["#thread", "#inspector", "#sidebar", ".composer", ".popover", "dialog", "#main"].find((s) => a.closest(s));
    return { id: a.id, cls: String(a.className), label: a.getAttribute("aria-label") || "", in: region || "" };
  });
}

const route = (page) => page.evaluate(() => ({ hash: location.hash, name: globalThis.__studio.state.route.name }));
const turnRunning = (page) => page.evaluate(() => Boolean(globalThis.__studio.state.turn));
const text = (page, key, vars) => page.evaluate(async ([k, v]) => (await import("/js/core/i18n.js")).t(k, v), [key, vars]);

/** Records what the two live regions say, from the first load on. */
async function recordAnnouncements(page) {
  await page.addInitScript(() => {
    const said = (globalThis.__said = []);
    const watch = () => {
      for (const id of ["announcer", "announcer-assertive"]) {
        const el = document.getElementById(id);
        if (!el || el.__watched) continue;
        el.__watched = true;
        new MutationObserver(() => { if (el.textContent) said.push({ region: id, text: el.textContent }); })
          .observe(el, { childList: true, characterData: true, subtree: true });
      }
    };
    document.addEventListener("DOMContentLoaded", watch);
  });
}
const announced = (page) => page.evaluate(() => globalThis.__said || []);

/** Starts the slow answer and waits until it is streaming. */
async function startSlow(page) {
  await send(page, "请慢速回答");
  await expect.poll(() => turnRunning(page)).toBe(true);
  await expect(page.locator(".composer__send.is-stop")).toBeVisible();
}

test("the skip links move the focus to their target and leave the route alone", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, custom());
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await newProject(page, "E2E 跳转");
  await send(page, "你好");
  await waitForAnswers(page, 1);

  const before = await route(page);
  expect(before.name).toBe("conversation");
  const links = page.locator(".skip-link");
  for (const [i, target] of [[0, "composer-input"], [1, "thread"]]) {
    await expect(links.nth(i)).toHaveAttribute("href", `#${target}`);
    await page.locator(".topbar").getByRole("button").first().focus(); // from somewhere else
    await links.nth(i).focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => focused(page).then((f) => f.id), `skip link ${i + 1} focuses #${target}`).toBe(target);
    expect(await route(page), "activating a skip link is not a navigation").toEqual(before);
  }

  // a page without a composer or a thread: the first link goes to the main region, the second is not offered
  await page.evaluate(() => { location.hash = "#/settings/general"; });
  await expect.poll(() => route(page).then((r) => r.name)).toBe("settings");
  const settings = await route(page);
  await expect(links.nth(0)).toHaveAttribute("href", "#main");
  await expect(links.nth(1)).toBeHidden();
  await links.nth(0).focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => focused(page).then((f) => f.id)).toBe("main");
  expect(await route(page)).toEqual(settings);
  watch.expectClean();
});

test("an input method's Enter and Escape belong to the input method: nothing is sent, nothing is stopped", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, custom());
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await newProject(page, "E2E 输入法");
  const cdp = await page.context().newCDPSession(page);
  const key = async (k, code) => {
    await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: k, code: k, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: k, windowsVirtualKeyCode: code, nativeVirtualKeyCode: code });
  };
  const box = page.locator("#composer-input");
  const asked = mock.requests.length;

  // Enter picks the candidate while pinyin is being composed: no message, no conversation, no model call
  await box.click();
  await cdp.send("Input.imeSetComposition", { text: "gancao", selectionStart: 6, selectionEnd: 6 });
  await key("Enter", 13);
  await cdp.send("Input.insertText", { text: "你好" });
  await page.waitForTimeout(500);
  await expect(box).toHaveValue("你好");
  expect((await route(page)).name, "still on the project page").toBe("project");
  expect(mock.requests.length, "no model call").toBe(asked);
  await expect(page.locator(".msg--user")).toHaveCount(0);
  // an Enter of its own sends
  await box.press("Enter");
  await waitForAnswers(page, 1);
  await expect(page.locator(".msg--user")).toHaveCount(1);

  // Escape cancels the candidate while an answer streams: the answer goes on
  await startSlow(page);
  await box.click();
  await cdp.send("Input.imeSetComposition", { text: "xia", selectionStart: 3, selectionEnd: 3 });
  await key("Escape", 27);
  await page.waitForTimeout(600);
  expect(await turnRunning(page), "Escape inside the input method does not stop the answer").toBe(true);
  await cdp.send("Input.imeSetComposition", { text: "", selectionStart: 0, selectionEnd: 0 });
  // an Escape of its own stops it
  await box.press("Escape");
  await expect.poll(() => turnRunning(page)).toBe(false);
  await waitForAnswers(page, 2);
  watch.expectClean();
});

test("the focus after the first send, after Stop, and after Esc on a menu stays where the work is", async ({ page }) => {
  const watch = watchConsole(page);
  await recordAnnouncements(page);
  await seedSettings(page, {}, custom());
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await newProject(page, "E2E 焦点");

  // the first message is typed on the project page; the conversation's own composer takes the focus over
  await page.locator("#composer-input").focus();
  await page.keyboard.type("你好");
  await page.keyboard.press("Enter");
  await expect.poll(() => route(page).then((r) => r.name)).toBe("conversation");
  await expect.poll(() => focused(page).then((f) => f.id), "focus after the first send").toBe("composer-input");
  await waitForAnswers(page, 1);
  expect((await focused(page)).id).toBe("composer-input");
  await page.keyboard.type("第二条");
  await expect(page.locator("#composer-input")).toHaveValue("第二条");
  await page.locator("#composer-input").fill("");
  await expect.poll(async () => (await announced(page)).map((a) => a.text)).toContain(await text(page, "ui.turn.done"));

  // Stop with the keyboard: the button turns back into a (disabled) Send, the focus goes to the message box
  await startSlow(page);
  const stop = page.locator(".composer__send");
  await expect(stop).toHaveAttribute("aria-label", await text(page, "ui.composer.stop"));
  await stop.focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => turnRunning(page)).toBe(false);
  await waitForAnswers(page, 2);
  await expect.poll(() => focused(page).then((f) => f.id), "focus after Stop").toBe("composer-input");
  await expect.poll(async () => (await announced(page)).map((a) => a.text)).toContain(await text(page, "core.agent.stopped"));

  // a menu opened from the keyboard takes the focus to its first item; Esc closes it and gives the focus back
  const more = page.locator(".side-item__more").first();
  const label = await more.getAttribute("aria-label");
  await more.focus();
  await page.keyboard.press("Enter");
  const menu = page.locator(".popover--menu[role=menu]:not(.is-leaving)");
  await expect(menu).toBeVisible();
  await expect.poll(() => focused(page).then((f) => f.in), "focus is in the menu").toBe(".popover");
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect.poll(() => focused(page), "focus after Esc on the menu").toMatchObject({ label });
  watch.expectClean();
});

test("the top bar's compute popover opens once, closes on Esc and gives the focus back to its chip", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, {}, custom());
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  const chip = page.locator(".compute-chip--top");
  await expect(chip).toBeVisible();
  const label = await chip.getAttribute("aria-label");
  await chip.click();
  const pop = page.locator(".popover:not(.is-leaving)");
  await expect(pop).toHaveCount(1);
  await page.waitForTimeout(300); // a second popover, or one that sticks, would show by now
  await expect(pop).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.locator(".popover")).toHaveCount(0);
  await expect.poll(() => focused(page), "focus after Esc on the compute popover").toMatchObject({ cls: expect.stringContaining("compute-chip--top"), label: label || "" });
  // and again: it opens, and closes, every time
  await chip.click();
  await expect(pop).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.locator(".popover")).toHaveCount(0);
  watch.expectClean();
});

for (const sheet of [
  { name: "the inspector drawer", viewport: { width: 1100, height: 800 }, toggle: ".topbar__inspector", region: "#inspector", open: (s) => s.inspector.open },
  { name: "the sidebar sheet on a phone", viewport: { width: 390, height: 844 }, mobile: true, toggle: ".topbar__menu", region: "#sidebar", open: (s) => s.sidebarOpen },
]) {
  test(`${sheet.name}: opened from the keyboard it holds the focus; Esc closes it and gives the focus back`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: sheet.viewport, isMobile: Boolean(sheet.mobile), hasTouch: Boolean(sheet.mobile), locale: "zh-CN" });
    const page = await context.newPage();
    const watch = watchConsole(page);
    await seedSettings(page, {}, custom());
    await page.goto(`${site.url}/`);
    await waitForApp(page);
    await newProject(page, "E2E 抽屉");
    await send(page, "你好");
    await waitForAnswers(page, 1);
    const isOpen = () => page.evaluate(`(${sheet.open.toString()})(globalThis.__studio.state)`);
    expect(await isOpen()).toBe(false);

    const toggle = page.locator(sheet.toggle);
    await toggle.focus();
    await page.keyboard.press("Enter");
    await expect.poll(isOpen).toBe(true);
    await expect.poll(() => focused(page).then((f) => f.in), "focus moves into the sheet").toBe(sheet.region);
    // Tab stays inside while it is open
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      expect((await focused(page)).in, `Tab ${i + 1} stays in ${sheet.region}`).toBe(sheet.region);
    }
    await page.keyboard.press("Escape");
    await expect.poll(isOpen).toBe(false);
    await expect.poll(() => page.evaluate((sel) => document.activeElement?.matches(sel) ?? false, sheet.toggle), "focus goes back to the button that opened it").toBe(true);
    watch.expectClean();
    await context.close();
  });
}

test("an approval is announced, and deciding it from the keyboard keeps the focus in the thread", async ({ page }) => {
  const watch = watchConsole(page);
  await recordAnnouncements(page);
  await seedSettings(page, {}, custom());
  await openPaired(page, runner);
  await newProject(page, "E2E 批准焦点");
  await send(page, "请运行 RNA-seq 流程");
  const card = page.locator(".permission:not(.is-decided)").last();
  await expect(card).toBeVisible({ timeout: 60000 });
  const title = await text(page, "ui.perm.title");
  await expect.poll(async () => (await announced(page)).filter((a) => a.region === "announcer-assertive").map((a) => a.text)).toContain(title);
  // the card did not take the focus from the message box (an Enter there must never approve)
  expect((await focused(page)).id).toBe("composer-input");

  await card.locator(".permission__actions .btn--ghost").focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => focused(page), "focus after the decision").toMatchObject({ in: "#thread" });
  await waitForAnswers(page, 1);
  // the answer replaced its live version: the focus is still in the thread, not on <body>
  await expect.poll(() => focused(page)).toMatchObject({ in: "#thread" });
  watch.expectClean();
});

// ------------------------------------------------------------------------------------------------ axe-core

// injected by the browser's own init script (not a <script> tag), so the page's content policy is not involved
const AXE = readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
// Known and reported to the web app, allowed here until it is fixed (an entry that no longer matches is harmless).
const KNOWN = [];

/** The serious and critical WCAG A/AA violations on the page as it is now, one line per element. */
async function axeProblems(page, where) {
  // transitions settle (a half-faded colour is not the colour): no finite animation running, and no toast, which
  // could start fading out while axe runs (a toast's own contrast is checked by the token tests)
  await page.waitForFunction(() => !document.querySelector("#toasts .toast")
    && document.getAnimations().every((a) => a.playState !== "running" || a.effect?.getComputedTiming?.().iterations === Infinity), null, { timeout: 15000 });
  await page.waitForTimeout(150);
  const found = await page.evaluate(async (tags) => {
    const r = await globalThis.axe.run(document, { runOnly: { type: "tag", values: tags }, resultTypes: ["violations"] });
    return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
      .flatMap((v) => v.nodes.map((n) => ({ rule: v.id, impact: v.impact, target: n.target.join(" "), why: (n.failureSummary || "").split("\n").slice(1, 3).join(" ").trim() })));
  }, WCAG);
  const layout = await page.evaluate(() => globalThis.__studio.state.layout);
  return found
    .filter((f) => !KNOWN.some((k) => k.rule === f.rule && k.target.test(f.target) && (!k.layout || k.layout === layout)))
    .map((f) => `${where}: ${f.rule} (${f.impact}) ${f.target} — ${f.why}`);
}

test("axe-core finds no serious or critical WCAG A/AA issue on the main views, light and dark, desktop and phone", async ({ browser }) => {
  test.setTimeout(300_000);
  const problems = [];
  for (const colorScheme of ["light", "dark"]) {
    for (const phone of [false, true]) {
      const context = await browser.newContext({
        viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: phone, hasTouch: phone, colorScheme, locale: "zh-CN",
      });
      const page = await context.newPage();
      await page.addInitScript({ content: AXE });
      await seedSettings(page, { theme: "system" }, custom());
      await page.goto(`${site.url}/`);
      await waitForApp(page);
      const at = `${colorScheme} ${phone ? "phone" : "desktop"}`;
      await newProject(page, "E2E axe");
      problems.push(...await axeProblems(page, `${at} #/project (new)`));
      await send(page, "你好");
      await waitForAnswers(page, 1);
      problems.push(...await axeProblems(page, `${at} #/conversation`));
      const project = await page.evaluate(() => globalThis.__studio.state.project.id);
      for (const r of [`#/project/${project}`, "#/settings/models", "#/settings/compute", "#/settings/general", "#/settings/shortcuts", "#/catalog", "#/catalog/native.reverse_complement", "#/about"]) {
        await page.evaluate((h) => { location.hash = h; }, r);
        await expect.poll(() => page.evaluate(() => location.hash)).toBe(r);
        problems.push(...await axeProblems(page, `${at} ${r.replace(project, "<id>")}`));
      }
      await context.close();
    }
  }
  expect(problems, problems.join("\n")).toEqual([]);
});

test("axe-core: a governed answer, its approval card and its tool card have no serious or critical issue", async ({ page }) => {
  const watch = watchConsole(page);
  await page.addInitScript({ content: AXE });
  await seedSettings(page, {}, custom());
  await openPaired(page, runner);
  await newProject(page, "E2E axe 受治理");
  await send(page, "甘草与甘遂同用，有哪些记载？");
  const problems = [];
  // the first tool this project runs on the runner asks first (as in runner.spec.mjs)
  const card = page.locator(".permission:not(.is-decided)").last();
  await expect(card).toBeVisible({ timeout: 60000 });
  problems.push(...await axeProblems(page, "approval card"));
  await card.locator(".permission__actions .btn--primary").click();
  const answer = await waitForAnswers(page, 1, { timeout: 180_000 });
  await expect(answer.locator(".tool-card").first()).toBeVisible();
  problems.push(...await axeProblems(page, "governed answer"));
  // the inspector's tabs: the run, its evidence, the claims and what licensed them, the files, the provenance
  for (const tab of ["#insp-run", "#insp-evidence", "#insp-claims", "#insp-files", "#insp-provenance"]) {
    await page.locator(tab).click();
    await expect(page.locator(tab)).toHaveAttribute("aria-selected", "true");
    problems.push(...await axeProblems(page, `governed answer, inspector ${tab}`));
  }
  expect(problems, problems.join("\n")).toEqual([]);
  watch.expectClean();
});
