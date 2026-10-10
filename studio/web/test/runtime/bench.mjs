#!/usr/bin/env node
// The browser compute benchmark (bench-page.mjs) in headless Chromium, and the same tool calls made natively by the
// runner's dispatcher (CPython), on the same inputs. Writes a JSON report and a Markdown summary.
//
//   node studio/web/test/runtime/bench.mjs [--site DIR] [--chromium PATH] [--pyodide-dir DIR | --index URL]
//        [--reps N] [--quick] [--gpu-flags] [--json FILE] [--md FILE]
//
// --gpu-flags launches Chromium with WebGPU forced on (SwiftShader where there is no GPU) and WebNN enabled, so their
// code paths are exercised; a software adapter is labelled as such in the report, and its timings are not a GPU's.

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os, { tmpdir } from "node:os";
import path from "node:path";
import { buildSite, loadPlaywright, runPage, serve } from "./run.mjs";

const PYTHON = process.env.PYTHON || "python3";

function parseArgs(argv) {
  const a = { reps: 3, quick: false, gpuFlags: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = () => argv[++i];
    if (k === "--site") a.site = v();
    else if (k === "--chromium") a.chromium = v();
    else if (k === "--pyodide-dir") a.pyodideDir = v();
    else if (k === "--index") a.index = v();
    else if (k === "--reps") a.reps = Number(v());
    else if (k === "--quick") a.quick = true;
    else if (k === "--gpu-flags") a.gpuFlags = true;
    else if (k === "--json") a.json = v();
    else if (k === "--md") a.md = v();
    else throw new Error(`unknown argument ${k}`);
  }
  return a;
}

/** The same calls through tcmstudio.dispatch in this machine's CPython, as the local runner makes them. */
function nativeTimes(tools, reps) {
  const dir = mkdtempSync(path.join(tmpdir(), "tcmstudio-bench-"));
  const input = path.join(dir, "calls.json");
  writeFileSync(input, JSON.stringify(tools.map((t) => ({ tool: t.tool, args: t.args }))));
  const script = `
import json, statistics, sys, time
from tcmstudio import dispatch
calls = json.load(open(sys.argv[1], encoding="utf-8"))
reps = int(sys.argv[2])
ctx = json.dumps({"where": "runner"})
out = []
for c in calls:
    args = json.dumps({"tool": c["tool"], "arguments": c["args"]}, ensure_ascii=False)
    dispatch.call_json("call_tool", args, ctx)            # warm: imports, caches
    ts, sha = [], None
    for _ in range(reps):
        t0 = time.perf_counter()
        env = json.loads(dispatch.call_json("call_tool", args, ctx))
        ts.append((time.perf_counter() - t0) * 1000)
        sha = env.get("receipt", {}).get("output_sha256")
    out.append({"tool": c["tool"], "ms": statistics.median(ts), "status": env.get("status"), "output_sha256": sha})
print(json.dumps({"python": sys.version.split()[0], "calls": out}))
`;
  const r = spawnSync(PYTHON, ["-c", script, input, String(reps)], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  rmSync(dir, { recursive: true, force: true });
  if (r.status !== 0) return { error: (r.stderr || "").slice(-2000) };
  return JSON.parse(r.stdout.trim().split("\n").at(-1));
}

function markdown(report) {
  const env = report.environment;
  const caps = report.browser.caps || {};
  const gpu = caps.webgpu?.adapter;
  const lines = [];
  lines.push(`# Browser compute benchmark`, "");
  lines.push(`Measured ${report.measured_at} on ${env.cpu} (${env.cores} logical cores), ${env.platform}; ${report.browser.user_agent}.`);
  lines.push(`Cross-origin isolated: ${report.browser.isolated}. WASM SIMD: ${caps.wasm?.simd}, threads usable: ${caps.wasm?.threads_usable}.`);
  lines.push(`WebGPU adapter: ${gpu ? `${[gpu.vendor, gpu.architecture].filter(Boolean).join(" ")}${caps.webgpu.software ? " (software renderer)" : ""}` : "none"}. WebNN contexts created: ${(caps.webnn?.contexts || []).filter((c) => c.created).map((c) => c.label).join(", ") || "none"}.`);
  lines.push(`Medians of ${report.reps} runs. Every local result was checked identical to the reference before its time was counted.`, "");
  lines.push(`## Tool calls (Pyodide in a Worker), original Python vs local backends, and native CPython`, "");
  lines.push(`| Tool | Size | Backend | Python in the browser (ms) | Local backend (ms) | Speed-up | Native CPython (ms) | Same output |`);
  lines.push(`|---|---|---|---:|---:|---:|---:|:---:|`);
  for (const t of report.browser.tools) {
    const n = report.native?.calls?.find((c, i) => i === report.browser.tools.indexOf(t));
    const sameNative = n?.output_sha256 && n.output_sha256 === t.output_sha256;
    lines.push(`| \`${t.tool.replace("native.", "")}\` | ${t.size} | ${t.backend} | ${t.reference_ms} | ${t.local_ms} | ${t.speedup ? `${t.speedup}×` : "—"} | ${n ? n.ms.toFixed(1) : "—"} | ${t.same_output && (n ? sameNative : true) ? "yes" : "**no**"} |`);
  }
  lines.push("", `## The JavaScript kernels alone (no Python, no JSON)`, "", `| Kernel | Size | ms | ns per cell |`, `|---|---|---:|---:|`);
  for (const k of report.browser.kernels) lines.push(`| ${k.kernel} | ${k.size} | ${k.ms} | ${k.ns_per_cell} |`);
  lines.push("", `## WebGPU (the WGSL count kernel, exact parity with the CPU reference)`, "");
  if (report.browser.webgpu.some((w) => w.ms !== undefined)) {
    lines.push(`| Size | Adapter | ms | Exact | Note |`, `|---|---|---:|:---:|---|`);
    for (const w of report.browser.webgpu) if (w.ms !== undefined) lines.push(`| ${w.size} | ${w.adapter} | ${w.ms} | ${w.exact ? "yes" : "**no**"} | ${w.note} |`);
  } else lines.push(`Not measured: ${report.browser.webgpu.map((w) => w.reason).filter(Boolean).join("; ") || "no adapter"}.`);
  lines.push("", `## WebNN`, "");
  const nn = caps.webnn;
  if (!nn?.api) lines.push("No WebNN API in this browser.");
  else {
    lines.push(`| Requested | Created | Graph output correct | Note |`, `|---|:---:|:---:|---|`);
    for (const c of nn.contexts) lines.push(`| ${JSON.stringify(c.requested)} | ${c.created ? "yes" : "no"} | ${c.verified ? (c.verified.ok ? "yes" : `no (${c.verified.reason})`) : "—"} | ${c.error || ""} |`);
    lines.push("", nn.note, nn.distinct_backends_hint === 1 ? "Every context reported the same operator limits: probably one backend served them all." : "");
  }
  if (report.browser.failures?.length) lines.push("", "## Failures", "", ...report.browser.failures.map((f) => `- ${f}`));
  return lines.join("\n") + "\n";
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const built = args.site ? null : buildSite();
  const site = path.resolve(args.site || built.out);
  const index = args.index || (args.pyodideDir ? "/pyodide/" : "");
  const iso = await serve({ site, isolated: true, pyodideDir: args.pyodideDir ? path.resolve(args.pyodideDir) : null });
  const { chromium } = await loadPlaywright();
  const flags = ["--enable-unsafe-webgpu", ...(args.gpuFlags ? ["--use-angle=swiftshader", "--enable-features=Vulkan,WebMachineLearningNeuralNetwork"] : [])];
  const browser = await chromium.launch({ executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined, args: flags });
  let result;
  try {
    const ctx = await browser.newContext();
    const q = `?reps=${args.reps}${args.quick ? "&quick=1" : ""}${index ? `&index=${encodeURIComponent(index)}` : ""}`;
    result = await runPage(ctx, `${iso.origin}/test/runtime/bench.html${q}`, 1_800_000);
  } finally {
    await browser.close();
    iso.server.close();
  }
  const native = nativeTimes(result.tools || [], args.reps);
  for (const t of result.tools || []) delete t.args;
  const report = {
    measured_at: new Date().toISOString(), reps: args.reps, chromium_flags: flags,
    environment: { cpu: os.cpus()[0]?.model || "unknown", cores: os.cpus().length, platform: `${os.platform()} ${os.release()} ${os.arch()}`, node: process.version },
    browser: result, native,
  };
  if (args.json) writeFileSync(args.json, JSON.stringify(report, null, 1));
  const md = markdown(report);
  if (args.md) writeFileSync(args.md, md);
  process.stdout.write(md);
  if (built) built.cleanup();
  process.exit(result.ok ? 0 : 1);
}

main().catch((err) => { console.error(err?.stack || String(err)); process.exit(2); });
