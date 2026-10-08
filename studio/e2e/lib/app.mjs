// Page helpers for the end-to-end specs: settings seeded before the app boots, a console watcher (the app must boot
// and run with zero console errors), the composer, finished answers, and a read of the app's IndexedDB.

import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";

export const SCREENS = process.env.STUDIO_E2E_SCREENS || path.join(process.env.STUDIO_E2E_TMP || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "test-results"), "screens");

/**
 * Settings written into localStorage before the first load only (a reload keeps what the app saved since).
 * `custom` = {id, base_url, model} adds an OpenAI-compatible provider and makes it the active one.
 */
export async function seedSettings(page, patch = {}, { custom = null } = {}) {
  const settings = { onboarded: true, lang: "zh", theme: "light", ...patch };
  if (custom) {
    settings.customProviders = [{ id: custom.id || "custom-e2e", label: custom.label || "E2E mock", format: "openai", base_url: custom.base_url, model: custom.model, headers: {}, extra_body: {}, max_tokens_param: "max_tokens", stream_usage: true }];
    settings.provider = custom.id || "custom-e2e";
    settings.models = { ...(settings.models || {}), [custom.id || "custom-e2e"]: custom.model };
  }
  await page.addInitScript((s) => {
    try {
      if (!localStorage.getItem("e2e.seeded")) {
        localStorage.setItem("tcmstudio.settings", JSON.stringify(s));
        localStorage.setItem("tcmscience.lang", s.lang || "zh");
        localStorage.setItem("e2e.seeded", "1");
      }
    } catch { /* storage blocked: the test will say so */ }
  }, settings);
}

/** Collects console errors, uncaught exceptions and failed requests. `allow` = regexps of expected messages. */
export function watchConsole(page, { allow = [] } = {}) {
  const problems = [];
  const allowed = (s) => allow.some((re) => re.test(s));
  page.on("console", (m) => {
    if (m.type() === "error" && !allowed(m.text())) problems.push(`console.error: ${m.text()}`);
  });
  page.on("pageerror", (e) => { if (!allowed(String(e))) problems.push(`pageerror: ${e.message}\n${e.stack || ""}`); });
  page.on("requestfailed", (r) => {
    const why = r.failure()?.errorText || "";
    // a stream the page itself aborted (stop, page change) is not a failure
    if (/ERR_ABORTED/.test(why)) return;
    const line = `requestfailed: ${r.method()} ${r.url()} ${why}`;
    if (!allowed(line)) problems.push(line);
  });
  return {
    problems,
    expectClean() { expect(problems, problems.join("\n")).toEqual([]); },
  };
}

export async function shot(page, name, opts = {}) {
  mkdirSync(SCREENS, { recursive: true });
  await page.screenshot({ path: path.join(SCREENS, `${name}.png`), ...opts });
}

export async function waitForApp(page) {
  await expect(page.locator("#app")).not.toHaveAttribute("aria-busy", "true", { timeout: 30000 });
  await expect(page.locator("#composer-input")).toBeVisible({ timeout: 30000 });
}

/** Type into the composer and send with Enter. */
export async function send(page, text) {
  const box = page.locator("#composer-input");
  await box.click();
  await box.fill(text);
  await box.press("Enter");
}

/** Wait until the number of finished assistant answers reaches `n`; returns the last one. */
export async function waitForAnswers(page, n, { timeout = 120000 } = {}) {
  const done = page.locator(".msg--assistant:not(.is-live)");
  await expect(done).toHaveCount(n, { timeout });
  return done.nth(n - 1);
}

/** Every record in the app's IndexedDB ("tcmstudio"), by store. */
export async function readDb(page) {
  return page.evaluate(async () => {
    const db = await new Promise((resolve, reject) => {
      const r = indexedDB.open("tcmstudio");
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
    const out = {};
    for (const name of db.objectStoreNames) {
      out[name] = await new Promise((resolve, reject) => {
        const r = db.transaction(name).objectStore(name).getAll();
        r.onsuccess = () => resolve(r.result.map((x) => (name === "files" ? { ...x, blob: undefined } : x)));
        r.onerror = () => reject(r.error);
      });
    }
    db.close();
    return out;
  });
}

/** Open the app through the runner's own pairing link and connect (the dialog's Connect button). */
export async function openPaired(page, runner) {
  await page.goto(runner.pairUrl);
  await waitForApp(page);
  const pair = page.locator(".dialog, [role=dialog]").filter({ hasText: /Runner|runner/ });
  if (await pair.first().isVisible().catch(() => false)) {
    await pair.first().getByRole("button", { name: /^(连接|Connect)/ }).click();
    await expect(pair.first()).toBeHidden({ timeout: 60000 });
  }
  await expect.poll(() => page.evaluate(() => globalThis.__studio.state.runner.status), { timeout: 60000 }).toBe("ready");
}

/** Answer the permission card that is waiting: "once" | "project" | "deny". Returns the card's text as asked. */
export async function approve(page, decision) {
  const card = page.locator(".permission:not(.is-decided)").last();
  await expect(card).toBeVisible({ timeout: 60000 });
  const text = await card.innerText();
  const cls = { once: ".btn--primary", project: ".btn--secondary", deny: ".btn--ghost" }[decision];
  await card.locator(`.permission__actions ${cls}`).click();
  return text;
}

/** A new project, opened (the composer is its first conversation). */
export async function newProject(page, name) {
  await page.evaluate((n) => globalThis.__studio.createProject({ name: n }), name);
  await expect(page.locator("#composer-input")).toBeVisible();
}
