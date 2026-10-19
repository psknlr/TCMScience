// Browser research jobs (studio/docs/BROWSER_JOBS.md), on the built site served the way Cloudflare serves it: a job
// worker (js/scientific/engines/science.worker.js) boots the same Pyodide and bundle as the tool runtime, loads
// pyarrow from the Pyodide distribution, and studies the planted world (tcmstudio.browser_selftest): the snapshot store,
// the governed analysis in this process (a worker cannot start one), its seeded permutation null, the rebuttals and
// the nine exports, recomputed. Its five tables must be the bytes native CPython committed — the known answer the
// research jobs are held to in a browser — and then the same check runs as a job of the page's job service, which
// writes the job's receipt.

import { expect, test } from "@playwright/test";
import { requirePyodide, startStatic } from "./lib/servers.mjs";

let site;

test.beforeAll(async () => {
  site = await startStatic(process.env.STUDIO_SITE);
});

test.afterAll(async () => {
  await site?.stop();
});

async function pyodideReachable(page) {
  const boot = await page.evaluate(async () => (await fetch("/runtime/boot.json")).json());
  const reachable = await page.evaluate(async (u) => {
    try { return (await fetch(`${u}pyodide-lock.json`)).ok; } catch { return false; }
  }, boot.pyodide.index_url);
  return { boot, reachable };
}

test("a job worker studies the planted world and writes the native bytes", async ({ page }) => {
  test.setTimeout(600_000);
  await page.goto(`${site.url}/?nosw`);
  expect(await page.evaluate(() => globalThis.crossOriginIsolated), "the site's _headers isolate the page").toBe(true);
  const { boot, reachable } = await pyodideReachable(page);
  requirePyodide(test, reachable, boot.pyodide.index_url, "the browser research jobs");

  const out = await page.evaluate(async (bootDoc) => {
    const w = new Worker("/js/scientific/engines/science.worker.js", { type: "module" });
    let seq = 0;
    const pending = new Map();
    const events = [];
    w.onmessage = (e) => {
      const m = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      if (m.type === "event") { events.push(m.event); return; }
      pending.get(m.id)?.(m);
      pending.delete(m.id);
    };
    const call = (op, payload) => new Promise((r) => { const id = ++seq; pending.set(id, r); w.postMessage(JSON.stringify({ id, op, payload })); });
    const init = await call("init", { boot: bootDoc, siteUrl: `${location.origin}/` });
    const packages = await call("packages", { names: ["pyarrow"] });
    const run = await call("run", { fn: "self_test", args: { work_dir: "/work/e2e" } });
    w.terminate();
    return { init, packages, run, events };
  }, boot);

  expect(out.init.ok, JSON.stringify(out.init.error)).toBe(true);
  expect(out.init.result.platform).toBe("emscripten");
  expect(out.init.result.cross_origin_isolated).toBe(true);
  expect(out.packages.ok, JSON.stringify(out.packages.error)).toBe(true);
  expect(out.run.ok, JSON.stringify(out.run.error)).toBe(true);
  const call = out.run.result;
  expect(call.ok, JSON.stringify(call.error)).toBe(true);
  const report = call.result;
  expect(report.here.platform).toBe("emscripten");
  expect(report.verify.status, JSON.stringify(report.verify.problems)).toBe("PASS");
  for (const [name, t] of Object.entries(report.tables)) {
    // the same bytes as native CPython; a table that only agrees within 1e-12 is reported, never passed as identical
    expect(t.status, `${name}: ${JSON.stringify(t)}`).toBe("identical");
  }
  expect(report.status).toBe("PASS");
  expect(out.events.some((e) => e.kind === "log")).toBe(true);
});
