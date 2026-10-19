#!/usr/bin/env node
// Compiles and runs the real WGSL on a browser adapter, checking every integer count.
// node studio/web/test/runtime/webgpu.browser.mjs [--chromium PATH] [--software]
// --software explicitly permits software WebGPU for shader validation; production never uses it, and this harness
// checks that a production SequenceCompute refuses the software adapter it found. The adapter is classified by the
// runtime's own classifyAdapter; a software run shows the WGSL is exact, never that a GPU is fast.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const args = process.argv.slice(2), software = args.includes("--software");
const pathAt = args.indexOf("--chromium");
const executablePath = pathAt >= 0 ? args[pathAt + 1] : process.env.CHROMIUM_PATH;
const source = readFileSync(fileURLToPath(new URL("../../js/runtime/webgpu.js", import.meta.url)));
const server = createServer((req, res) => {
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin"); res.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  if (req.url === "/webgpu.js") { res.setHeader("Content-Type", "text/javascript"); res.end(source); }
  else { res.setHeader("Content-Type", "text/html"); res.end("<!doctype html><title>TCMScience WGSL parity</title>"); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ executablePath, headless: true, args: software ? ["--enable-unsafe-webgpu", "--use-angle=swiftshader", "--enable-features=Vulkan"] : ["--enable-unsafe-webgpu"] });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const report = await page.evaluate(async (allowSoftware) => {
    const { SequenceCompute, classifyAdapter, planSequenceCompute, referenceCounts } = await import("/webgpu.js");
    const adapter = await navigator.gpu?.requestAdapter({ powerPreference: "high-performance" });
    if (!adapter) return { ok: false, reason: "No WebGPU adapter; WGSL parity was not verified" };
    const adapterInfo = classifyAdapter(adapter.info, adapter);
    const probe = ["native.distance_matrix", { sequences: Array.from({ length: 8 }, (_, i) => "ACGT".repeat(2048).slice(i) + "A".repeat(i)) }];
    // production never computes on a software adapter
    let productionRefusesSoftware = null;
    if (adapterInfo.kind === "software") {
      const production = new SequenceCompute();
      productionRefusesSoftware = (await production.prepare(...probe)) === null && /software/.test(production.fallbackReason || "");
      production.stop();
    }
    const compute = new SequenceCompute({ allowSoftware, timestamps: true });
    const cases = [
      ...["p", "jc69", "k2p"].map((model) => ["native.distance_matrix", { model, sequences: { a: "ACGTURYN-".repeat(257), b: "GGTTTCAN-".repeat(257), c: "AC-TNACGR".repeat(257) } }]),
      ["native.hamming_distance", { a: "ACGTURYN".repeat(513), b: "GCSNTTCN".repeat(513) }],
      ["native.gc_content", { sequence: "ACGTURYSN".repeat(517) }],
      ["native.gc_content", { sequence: "ACGTURYSN".repeat(67), window: 127 }],
    ];
    const results = [];
    for (const [tool, input] of cases) {
      const t0 = performance.now();
      const expected = Array.from(referenceCounts(planSequenceCompute(tool, input)));
      const result = await compute.prepare(tool, input, { force: true });
      const run = compute.lastRun;
      results.push({ tool, model: input.model || null, window: input.window || 0, counts: expected.length, backend: result?.backend || "cpu", same: Boolean(result) && JSON.stringify(result.counts) === JSON.stringify(expected), reason: compute.fallbackReason, ms: Math.round(performance.now() - t0),
        dispatched: run?.dispatched ?? false, sample_checked: run?.checked ?? false, kernel_ms: run?.kernel_ms ?? null, ...(run?.kernel_note ? { kernel_note: run.kernel_note } : {}) });
    }
    // Rectangular dispatch (records exceed max x) and non-multiple-of-64 lengths exercise bounds.
    const long = { sequence: "GCASN".repeat(14001), window: 1 };
    const expected = Array.from(referenceCounts(planSequenceCompute("native.gc_content", long)));
    const result = await compute.prepare("native.gc_content", long, { force: true });
    results.push({ tool: "native.gc_content", window: 1, rectangular_dispatch: true, same: Boolean(result) && JSON.stringify(result.counts) === JSON.stringify(expected), reason: compute.fallbackReason });
    const knownAnswer = compute.knownAnswer;
    compute.stop();
    return { ok: results.every((r) => r.same), adapter: adapterInfo, known_answer: knownAnswer, production_refuses_software: productionRefusesSoftware,
      allow_software: allowSoftware, accelerated: null, note: "parity only: nothing here is compared with the CPU, so nothing here is evidence of acceleration", results };
  }, software);
  console.log(JSON.stringify(report, null, 2));
  assert.equal(report.ok, true, "real WGSL execution must exactly match every CPU reference count");
  assert.equal(report.known_answer?.ok, true, "the device passed the known-answer test");
  assert.notEqual(report.production_refuses_software, false, "production must refuse a software adapter");
} finally { await browser?.close(); await new Promise((resolve) => server.close(resolve)); }
