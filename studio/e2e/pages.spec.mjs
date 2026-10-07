// (e) The pages around the thread: first-run onboarding, settings (a custom model added through the form and tested),
// the tool catalog, about; a phone viewport; the dark theme; and basic accessibility (names, labels, landmarks,
// focus). The site is served as Cloudflare serves it, without a runner.

import { expect, test } from "@playwright/test";
import { startMockLLM } from "./mock-llm.mjs";
import { seedSettings, send, shot, waitForAnswers, waitForApp, watchConsole } from "./lib/app.mjs";
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

/** Buttons, links and form fields without an accessible name; images without alt. */
async function unnamed(page) {
  return page.evaluate(() => {
    const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";
    const named = (el) => {
      if (el.getAttribute("aria-label")?.trim() || el.getAttribute("title")?.trim()) return true;
      const by = el.getAttribute("aria-labelledby");
      if (by && by.split(/\s+/).some((id) => document.getElementById(id)?.textContent.trim())) return true;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) {
        if (el.type === "hidden") return true;
        if (el.labels?.length && [...el.labels].some((l) => l.textContent.trim())) return true;
        return Boolean(el.getAttribute("placeholder")?.trim() && el.getAttribute("aria-describedby"));
      }
      return Boolean(el.textContent.trim()) || [...el.querySelectorAll("img[alt]")].some((i) => i.alt.trim());
    };
    const bad = [];
    for (const el of document.querySelectorAll("button, a[href], input, textarea, select, [role=button], [role=tab], [role=switch], [role=menuitem]")) {
      if (visible(el) && !named(el)) bad.push(el.outerHTML.slice(0, 160));
    }
    for (const img of document.querySelectorAll("img")) if (visible(img) && !img.hasAttribute("alt")) bad.push(img.outerHTML.slice(0, 160));
    return bad;
  });
}

test("first run, settings with a custom model, catalog and about", async ({ page }) => {
  const watch = watchConsole(page);
  await seedSettings(page, { onboarded: false });
  await page.goto(`${site.url}/`);
  await waitForApp(page);

  // onboarding: three skippable steps, then never again
  const onb = page.locator(".dialog--onboarding");
  await expect(onb).toBeVisible();
  await expect(onb.locator(".onb__step")).toHaveCount(3);
  await shot(page, "onboarding-light");
  await onb.getByRole("button", { name: "下一步" }).click();
  await expect(onb).toContainText("Tao-S1");
  await onb.getByRole("button", { name: "下一步" }).click();
  await expect(onb).toContainText("tcmstudio serve");
  await onb.getByRole("button", { name: "开始使用" }).click();
  await expect(onb).toBeHidden();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("tcmstudio.settings")).onboarded)).toBe(true);
  await page.reload();
  await waitForApp(page);
  await expect(page.locator(".dialog--onboarding")).toHaveCount(0);

  // the relay of this site is off (no key): said in words, with what to do
  await page.goto(`${site.url}/#/settings/models`);
  await expect(page.locator("#app")).toContainText("Tao-S1 暂未开放");

  // a custom OpenAI-compatible endpoint, added through the form, tested, made the default
  await page.getByRole("button", { name: "添加 OpenAI 兼容接口" }).click();
  const card = page.locator(".provider-card").filter({ hasText: "API 1" }).last();
  if (!(await card.getByLabel("接口地址").isVisible().catch(() => false))) await card.locator("summary, .disclosure__summary, button").first().click();
  await card.getByLabel("接口地址").fill(`${mock.url}/v1`);
  await card.getByLabel("模型", { exact: true }).fill("mock-sci-1");
  await card.getByRole("button", { name: "测试连接" }).click();
  await expect(card).toContainText("连接正常，支持工具调用", { timeout: 30000 });
  await card.getByRole("button", { name: "设为默认" }).click();
  const settings = await page.evaluate(() => JSON.parse(localStorage.getItem("tcmstudio.settings")));
  expect(settings.customProviders).toHaveLength(1);
  expect(settings.provider).toBe(settings.customProviders[0].id);
  await shot(page, "settings-models-light");

  for (const tab of ["compute", "general", "shortcuts"]) {
    await page.goto(`${site.url}/#/settings/${tab}`);
    await expect(page.locator(`[aria-current="page"], .is-on, [aria-selected="true"]`).filter({ hasText: { compute: "计算", general: "通用", shortcuts: "快捷键" }[tab] }).first()).toBeVisible();
  }
  await expect(page.locator("#app")).toContainText("Ctrl+K");

  // the catalog: categories with counts, search, an entry's page
  await page.goto(`${site.url}/#/catalog`);
  await expect(page.locator("#app")).toContainText("642");
  await expect(page.locator("#app")).toContainText("安全与配伍");
  const search = page.getByRole("searchbox").or(page.locator("input[type=search]")).first();
  await search.fill("reverse complement");
  await expect(page.locator("#app")).toContainText("native.reverse_complement");
  await page.goto(`${site.url}/#/catalog/native.reverse_complement`);
  await expect(page.locator("#app")).toContainText("IUPAC");
  await shot(page, "catalog-light");

  await page.goto(`${site.url}/#/about`);
  await expect(page.locator("#app")).toContainText("science.impf.ai");

  // the custom model answers in a new conversation
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 页面" }));
  await send(page, "你好");
  await expect(await waitForAnswers(page, 1)).toContainText("测试模型的回答");

  expect(await unnamed(page), "every control has an accessible name").toEqual([]);
  watch.expectClean();
});

test("phone viewport (390×844): the thread, the off-canvas sidebar, no horizontal scroll", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: "zh-CN" });
  const page = await context.newPage();
  const watch = watchConsole(page);
  await seedSettings(page, {}, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 手机" }));
  await send(page, "你好");
  await waitForAnswers(page, 1);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, "no horizontal page scroll").toBeLessThanOrEqual(0);
  await shot(page, "mobile-thread-light");

  // the sidebar opens over the page and closes with Escape
  await page.locator(".topbar").getByRole("button").first().click();
  await expect.poll(() => page.evaluate(() => globalThis.__studio.state.sidebarOpen)).toBe(true);
  await page.waitForTimeout(400); // the slide-in transition
  await shot(page, "mobile-sidebar-light");
  await page.keyboard.press("Escape");
  await expect.poll(() => page.evaluate(() => globalThis.__studio.state.sidebarOpen)).toBe(false);

  await page.goto(`${site.url}/#/settings/compute`);
  await expect(page.locator("#app")).toContainText("本机 Runner");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  await shot(page, "mobile-settings-light");
  watch.expectClean();
  await context.close();
});

test("dark theme, keyboard focus and landmarks", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", locale: "zh-CN" });
  const page = await context.newPage();
  const watch = watchConsole(page);
  await seedSettings(page, { theme: "system" }, { custom: { id: "custom-e2e", base_url: `${mock.url}/v1`, model: "mock-sci-1" } });
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  // "system" follows the OS: dark here, with a dark page background
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const [r, g, b] = bg.match(/\d+/g).map(Number);
  expect(r + g + b, `body background ${bg}`).toBeLessThan(150);

  await page.evaluate(() => globalThis.__studio.createProject({ name: "E2E 深色" }));
  await send(page, "你好");
  await waitForAnswers(page, 1);
  await shot(page, "dark-thread");
  await page.goto(`${site.url}/#/settings/models`);
  await shot(page, "dark-settings");

  // landmarks and the first Tab stop (the skip link)
  expect(await page.locator("main, [role=main]").count()).toBeGreaterThan(0);
  expect(await page.locator("nav, [role=navigation]").count()).toBeGreaterThan(0);
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => ({ cls: document.activeElement?.className, text: document.activeElement?.textContent }));
  expect(first.cls).toContain("skip-link");
  // a focused control shows a focus ring
  await page.locator("#composer-input").focus();
  await page.keyboard.press("Shift+Tab");
  const ring = await page.evaluate(() => {
    const s = getComputedStyle(document.activeElement);
    return s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0 || s.boxShadow !== "none";
  });
  expect(ring, "keyboard focus is visible").toBe(true);
  // every string is translated, in both languages (a missing one would show its key)
  const keys = /\b(ui|core|runtime)\.[a-z_]+\.[a-z_.]+\b/;
  for (const l of ["en", "zh"]) {
    await page.evaluate(async (x) => (await import("/js/core/i18n.js")).setLang(x), l);
    for (const r of ["#/", "#/settings/models", "#/settings/compute", "#/settings/general", "#/catalog", "#/about"]) {
      await page.evaluate((h) => { location.hash = h; }, r);
      await page.waitForTimeout(400);
      const text = await page.locator("body").innerText();
      expect(text.match(keys)?.[0] ?? null, `${l} ${r}`).toBeNull();
    }
  }
  await shot(page, "dark-about-zh");
  // the theme switch in the top bar: light again
  await page.evaluate(() => globalThis.__studio.setSetting({ theme: "light" }));
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await unnamed(page)).toEqual([]);
  watch.expectClean();
  await context.close();
});
