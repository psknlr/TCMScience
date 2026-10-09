import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { handleSources } from "../edge/src/sources.js";
import { seedSettings, waitForApp } from "./lib/app.mjs";
import { startStatic } from "./lib/servers.mjs";

let site;
let upstreamCalls = [];
test.beforeAll(async () => {
  const manifest = JSON.parse(readFileSync(path.join(process.env.STUDIO_SITE, "runtime/source-gateway.json")));
  site = await startStatic(process.env.STUDIO_SITE, { sources: async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const response = await handleSources(new Request(`${site.url}${req.url}`, {
      method: req.method, headers: req.headers, body: req.method === "POST" ? Buffer.concat(chunks) : undefined,
    }), { ALLOWED_ORIGINS: site.url }, {
      manifest: async () => manifest, gate: async () => ({ ok: true }), pace: async () => ({ ok: true, delay: 0 }),
      fetch: async (url) => {
        upstreamCalls.push(String(url));
        return new Response(JSON.stringify({ _id: "7157", symbol: "TP53", name: "tumor protein p53" }), {
          headers: { "Content-Type": "application/json" },
        });
      },
    });
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } });
});
test.afterAll(async () => { await site?.stop(); });

test("browser Python uses full formula rows and governed same-origin database access", async ({ page }) => {
  test.setTimeout(420000);
  await seedSettings(page, { web: false });
  await page.goto(`${site.url}/`);
  await waitForApp(page);
  const reachable = await page.evaluate(async () => {
    const boot = await (await fetch("/runtime/boot.json")).json();
    try { return (await fetch(`${boot.pyodide.index_url}pyodide-lock.json`)).ok; } catch { return false; }
  });
  test.skip(!reachable, "Pyodide CDN unavailable; real browser integration remains unverified");

  const denied = await page.evaluate(() => globalThis.__studio.runtimes.browser.call(
    "call_tool", { tool: "connector.mygene.gene", arguments: { id: "7157" } }, { network: false }));
  expect(denied.ok).toBe(false);
  expect(upstreamCalls).toHaveLength(0);
  const live = await page.evaluate(() => globalThis.__studio.runtimes.browser.call(
    "call_tool", { tool: "connector.mygene.gene", arguments: { id: "7157" } }, { network: true }));
  expect(live.status, JSON.stringify(live.error)).toBe("succeeded");
  expect(live.receipt.where).toBe("browser");
  expect(live.result.symbol).toBe("TP53");
  expect(upstreamCalls).toEqual(["https://mygene.info/v3/gene/7157?fields=symbol%2Cname%2Csummary%2Cgo"]);

  const full = await page.evaluate(() => globalThis.__studio.runtimes.browser.call(
    "call_tool", { tool: "tcmdb.formulas", arguments: { query: "", limit: 2 } }));
  expect(full.status, JSON.stringify(full.error)).toBe("succeeded");
  expect(full.result.corpus_rows).toBe(84294);
  expect(full.result.records).toHaveLength(2);
  expect(full.result.next_offset).toBe(2);
  expect(full.result.provenance.license).toBe("LicenseRef-user-supplied-unstated");
  const compute = await page.evaluate(async () => {
    const browser = globalThis.__studio.runtimes.browser;
    const args = { sequences: ["ACGT".repeat(20000), "ACGA".repeat(20000)], model: "p" };
    const gpu = await browser.call("call_tool", { tool: "native.distance_matrix", arguments: args }, { acceleration: "auto" });
    const cpu = await browser.call("call_tool", { tool: "native.distance_matrix", arguments: args }, { acceleration: "cpu" });
    return { gpu, cpu, device: await browser.device() };
  });
  expect(compute.gpu.status, JSON.stringify(compute.gpu.error)).toBe("succeeded");
  expect(compute.cpu.status, JSON.stringify(compute.cpu.error)).toBe("succeeded");
  expect(compute.gpu.result).toEqual(compute.cpu.result);
  expect(compute.cpu.receipt.compute.backend).toBe("pyodide");
  if (compute.device.compute?.webgpu_adapter) expect(compute.gpu.receipt.compute.backend).toBe("webgpu");
  test.info().annotations.push({ type: "compute_backend", description: compute.gpu.receipt.compute.backend });
});
