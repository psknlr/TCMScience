#!/usr/bin/env node
// The science kernels' device check (compute/device-check.js) in a real browser: the GPU's known-answer test, exact
// parity of the WebGPU, WebAssembly SIMD and JavaScript Tanimoto kernels on fixed seeded inputs, their timings, and a
// verdict per size, kept as the browser's calibration (gpu-science.html, gpu-science-page.mjs).
//
//   node studio/web/test/runtime/gpu-science.mjs [--chromium PATH | --channel chrome] [--headed] [--software]
//        [--quick] [--sizes 32x2048,256x10504] [--reps N] [--site DIR] [--json FILE] [--timeout-min N]
//
// --software        lets the check compute on a software adapter (Chromium's SwiftShader, forced with
//                   --use-angle=swiftshader): it shows the WGSL is exact there, and its timings are the CPU's, so the
//                   report says "software, not evidence" and gives no speed-up. The page also checks that a production
//                   ScienceGPU refuses that adapter. With it, no adapter, or a GPU that did not run exactly at every
//                   size, fails (softwareFailures): the step exists to check the WGSL.
// without it        a machine whose only adapter is a software renderer (or none) reports "no physical adapter" and
//                   exits 0 with that verdict: it is not a parity failure; the CPU kernels are still checked and timed.
// --channel chrome --headed
//                   the person's own Chrome with its real GPU (on a Mac Studio, a Windows + NVIDIA machine): what the
//                   acceptance of a physical GPU needs, which no machine here or in CI has.
// --site DIR        serves a built site (build_web.py --dev) instead of studio/web.
// Exit 0 when every exactness check passed (whatever the verdict), 1 when one did not (or a production ScienceGPU
// computed on a software adapter, or --software and the WGSL did not run), 2 when no report came back. A long TMPDIR
// breaks Chromium's launch on Linux (its profile and socket paths): run with a short one (TMPDIR=/tmp/x).

import { createServer } from "node:http";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { MIME, loadPlaywright, runPage } from "./run.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(HERE, "../..");

function parseArgs(argv) {
  const a = { software: false, quick: false, headed: false, reps: 3, timeoutMin: 20 };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = () => argv[++i];
    if (k === "--chromium") a.chromium = v();
    else if (k === "--channel") a.channel = v();
    else if (k === "--headed") a.headed = true;
    else if (k === "--software") a.software = true;
    else if (k === "--quick") a.quick = true;
    else if (k === "--sizes") a.sizes = v();
    else if (k === "--reps") a.reps = Number(v());
    else if (k === "--site") a.site = v();
    else if (k === "--json") a.json = v();
    else if (k === "--timeout-min") a.timeoutMin = Number(v());
    else throw new Error(`unknown argument ${k}`);
  }
  if (a.sizes && !/^\d+x\d+(,\d+x\d+)*$/.test(a.sizes)) throw new Error("--sizes is QUERIESxREFS[,QUERIESxREFS…]");
  return a;
}

/** The switches: WebGPU on; with --software SwiftShader forced; on Linux, Vulkan (which a hardware adapter needs there). */
export function chromiumFlags({ software = false, platform = process.platform } = {}) {
  const flags = ["--enable-unsafe-webgpu"];
  if (software) flags.push("--use-angle=swiftshader");
  if (software || platform === "linux") flags.push("--enable-features=Vulkan");
  return flags;
}

/** studio/web (or a built site) on a loopback port, cross-origin isolated as the site is. */
function serve(root) {
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    let file = path.join(root, decodeURIComponent(url.pathname));
    if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
    if (existsSync(file) && statSync(file).isDirectory()) file = path.join(file, "index.html");
    const headers = { "cross-origin-opener-policy": "same-origin", "cross-origin-embedder-policy": "require-corp", "cache-control": "no-store" };
    if (!existsSync(file)) { res.writeHead(404, headers).end("not found"); return; }
    res.writeHead(200, { ...headers, "content-type": MIME[path.extname(file)] || "application/octet-stream" });
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, origin: `http://127.0.0.1:${server.address().port}` })));
}

const v = (x) => (x === null || x === undefined ? "—" : x === true ? "yes" : x === false ? "**no**" : String(x));

/**
 * What --software requires beyond the page's own checks → failures: that the WGSL ran, on an adapter, and was exact at
 * every size. The device check reports a machine without an adapter (or whose GPU could not run) as "no physical
 * adapter", which is not a parity failure for a visitor; under --software it would leave the GPU's exactness unchecked.
 */
export function softwareFailures(report) {
  if (!report) return [];
  const k = report.kernels?.["tanimoto.best"];
  const failures = [];
  const unrun = (k?.sizes || []).filter((s) => s.exact?.webgpu !== true);
  if (!report.device?.adapter) failures.push(`--software: no WebGPU adapter, so the WGSL never ran${report.device?.gpu_error ? ` (${report.device.gpu_error.message})` : ""}`);
  else if (unrun.length) failures.push(`--software: the WGSL did not run exactly at ${unrun.map((s) => `${s.queries} × ${s.refs} (${s.exact?.webgpu === false ? "not exact" : s.gpu_error?.message || report.device.gpu_error?.message || "not run"})`).join(", ")}`);
  if (!k?.sizes?.length) failures.push("--software: no size was measured");
  return failures;
}

/** The report as lines a person reads in a terminal. */
export function summary(result) {
  const r = result.report;
  if (!r) return [`no report: ${(result.failures || []).join("; ")}`];
  const a = r.device.adapter;
  const k = r.kernels["tanimoto.best"];
  const lines = [
    `browser: ${result.browser_version || ""} ${result.browser?.user_agent || ""}`.trim(),
    `adapter: ${a ? `${[a.vendor, a.architecture, a.device, a.description].filter(Boolean).join(" ") || "(no name)"}, ${a.kind} (${a.reason})` : "none"}${r.label ? ` — ${r.label}` : ""}`,
    `known-answer test: ${k.known_answer ? (k.known_answer.ok ? `ok (${k.known_answer.ms} ms)` : `FAILED: ${k.known_answer.reason}`) : `not run${r.device.gpu_error ? ` (${r.device.gpu_error.message})` : ""}`}`,
    `WebAssembly SIMD: ${r.device.wasm_simd.available ? "yes" : `no (${r.device.wasm_simd.reason})`}; kernel ${r.versions.kernel}, WASM ${r.versions.wasm_sha256.slice(0, 12)}, WGSL ${r.versions.wgsl_sha256.slice(0, 12)}`,
    "",
    "| Queries × refs | Comparisons | Exact (SIMD, GPU) | JS ms | SIMD ms | GPU ms (search + check) | GPU kernel ms | Bands | Verdict |",
    "|---|---:|---|---:|---:|---:|---:|---:|---|",
  ];
  for (const s of k.sizes) {
    const g = s.timings.webgpu;
    lines.push(`| ${s.queries} × ${s.refs} | ${s.pairs.toLocaleString("en-US")} | ${v(s.exact["wasm-simd"])}, ${v(s.exact.webgpu)} | ${v(s.timings.js?.ms)} | ${v(s.timings["wasm-simd"]?.ms)} | ${v(g?.ms)} | ${v(g?.kernel_ms)} | ${v(g?.bands)} | ${s.accelerated === null ? "no verdict" : s.accelerated ? "accelerated" : "not faster"}: ${s.reason} |`);
  }
  lines.push("", `verdict: ${r.verdict} (${r.verdict_reason})`);
  lines.push(`production refuses software: ${v(result.production_refuses_software)}; calibration kept: ${r.stored?.ok ? `yes, read back ${v(result.calibration_read_back)}` : `no (${r.stored?.error})`}`);
  if (result.failures?.length) lines.push(...result.failures.map((f) => `FAILURE: ${f}`));
  return lines;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (os.platform() === "linux" && os.tmpdir().length > 60) console.error(`warning: TMPDIR is ${os.tmpdir().length} characters long; Chromium may fail to launch (set TMPDIR=/tmp/x)`);
  const root = path.resolve(args.site || WEB);
  const { server, origin } = await serve(root);
  const { chromium } = await loadPlaywright();
  const flags = chromiumFlags({ software: args.software });
  const launch = { headless: !args.headed, args: flags };
  if (args.channel) launch.channel = args.channel;
  else if (args.chromium || process.env.CHROMIUM_PATH) launch.executablePath = args.chromium || process.env.CHROMIUM_PATH;
  let result, browserVersion = null;
  const browser = await chromium.launch(launch);
  try {
    browserVersion = `${args.channel || "chromium"} ${browser.version()}`;
    const ctx = await browser.newContext();
    const q = new URLSearchParams({ reps: String(args.reps) });
    if (args.software) q.set("software", "1");
    if (args.quick) q.set("quick", "1");
    if (args.sizes) q.set("sizes", args.sizes);
    result = await runPage(ctx, `${origin}/test/runtime/gpu-science.html?${q}`, args.timeoutMin * 60_000);
  } finally {
    await browser.close();
    server.close();
  }
  result.browser_version = browserVersion;
  result.harness = { flags, channel: args.channel || null, headed: args.headed, software_allowed: args.software, node: process.version, os: `${os.platform()} ${os.release()} ${os.arch()}`, cpu: os.cpus()[0]?.model || null, cores: os.cpus().length };
  const required = args.software ? softwareFailures(result.report) : [];
  if (required.length) Object.assign(result, { ok: false, failures: [...(result.failures || []), ...required] });
  if (args.json) writeFileSync(args.json, JSON.stringify(result, null, 1));
  for (const line of summary(result)) console.log(line);
  if (!result.report) { for (const l of result.console || []) console.error(l); process.exit(2); }
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => { console.error(err?.stack || String(err)); process.exit(2); });
}
