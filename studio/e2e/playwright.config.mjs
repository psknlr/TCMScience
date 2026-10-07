// Playwright configuration for TCMScience Studio's end-to-end tests (studio/e2e).
//
//   cd studio && npm install && npm run test:e2e
//
// Chromium: $CHROMIUM_PATH, else a Playwright-managed Chromium found under $PLAYWRIGHT_BROWSERS_PATH or
// /opt/pw-browsers (chromium-1194 = Playwright 1.56), else Playwright's default (`npx playwright install chromium`).
// The browser goes through $HTTPS_PROXY when one is set (Pyodide is fetched from jsDelivr); loopback never does.

import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function chromiumPath() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  for (const base of [process.env.PLAYWRIGHT_BROWSERS_PATH, "/opt/pw-browsers"].filter(Boolean)) {
    const p = path.join(base, "chromium-1194", "chrome-linux", "chrome");
    if (existsSync(p)) return p;
  }
  return undefined;
}

const proxy = process.env.HTTPS_PROXY || process.env.https_proxy || "";
const tmp = process.env.STUDIO_E2E_TMP || path.join(HERE, "test-results");
process.env.STUDIO_E2E_TMP = tmp;

export default defineConfig({
  testDir: HERE,
  testMatch: /.*\.spec\.mjs$/,
  outputDir: path.join(tmp, "artifacts"),
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: path.join(HERE, "global-setup.mjs"),
  use: {
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
    colorScheme: "light",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: {
      executablePath: chromiumPath(),
      args: proxy ? [`--proxy-server=${proxy}`, "--proxy-bypass-list=127.0.0.1;localhost;[::1]"] : [],
    },
  },
});
