#!/usr/bin/env node
// The browser compute benchmark (bench-page.mjs) in headless Chromium, and the same tool calls made natively by the
// runner's dispatcher (CPython), on the same inputs. Writes a JSON report and a Markdown summary.
//
//   node studio/web/test/runtime/bench.mjs [--site DIR] [--chromium PATH] [--pyodide-dir DIR | --index URL]
//        [--reps N] [--quick] [--gpu-flags] [--software-gpu] [--webnn] [--no-tools] [--json FILE] [--md FILE]
//
// --gpu-flags     WebGPU (with Vulkan, which Chromium on Linux generally needs to expose a hardware adapter) and WebNN
//                 enabled, on whatever adapter the browser then picks. Whether that is a hardware GPU depends on the
//                 machine, its drivers and the browser's GPU blocklist: the rows say which kind it was, never assume.
// --software-gpu  forces Chromium's SwiftShader software renderer (for machines with no GPU, to exercise the code
//                 path). Its rows are labelled software and carry no speed-up: SwiftShader runs on the CPU.
// --webnn         WebNN enabled (also part of --gpu-flags).
// --no-tools      skips the tool calls (no Pyodide, no native CPython comparison): kernels, WebGPU and WebNN only.
// Every WebGPU row names its adapter as the runtime's classifier sees it (software | physical | unidentified), whatever
// the flags asked for.

import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os, { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildSite, loadPlaywright, runPage, serve } from "./run.mjs";

const PYTHON = process.env.PYTHON || "python3";

function parseArgs(argv) {
  const a = { reps: 3, quick: false, gpuFlags: false, softwareGpu: false, webnn: false, tools: true };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], v = () => argv[++i];
    if (k === "--site") a.site = v();
    else if (k === "--chromium") a.chromium = v();
    else if (k === "--pyodide-dir") a.pyodideDir = v();
    else if (k === "--index") a.index = v();
    else if (k === "--reps") a.reps = Number(v());
    else if (k === "--quick") a.quick = true;
    else if (k === "--gpu-flags") a.gpuFlags = true;
    else if (k === "--software-gpu") a.softwareGpu = true;
    else if (k === "--webnn") a.webnn = true;
    else if (k === "--no-tools") a.tools = false;
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

/** The Chromium switches for the options: --enable-features is one switch (the last one given wins), so it is joined. */
export function chromiumFlags({ gpuFlags = false, softwareGpu = false, webnn = false } = {}) {
  const features = [];
  const flags = ["--enable-unsafe-webgpu"];
  if (softwareGpu) flags.push("--use-angle=swiftshader");
  if (softwareGpu || gpuFlags) features.push("Vulkan");
  if (webnn || gpuFlags) features.push("WebMachineLearningNeuralNetwork");
  if (features.length) flags.push(`--enable-features=${features.join(",")}`);
  return flags;
}

export function markdown(report) {
  const env = report.environment;
  const caps = report.browser.caps || {};
  const gpu = caps.webgpu?.adapter;
  const lines = [];
  const browser = report.browser.browser || {};
  lines.push(`# Browser compute benchmark`, "");
  lines.push(`Measured ${report.measured_at} on ${env.cpu} (${env.cores} logical cores), ${env.platform}; ${report.browser_version || ""} ${report.browser.user_agent}.`);
  if (browser.platform) lines.push(`The browser reports ${browser.platform} ${browser.platform_version || ""} ${browser.architecture || ""}${browser.brands ? `; ${browser.brands.map((b) => `${b.brand} ${b.version}`).join(", ")}` : ""}.`);
  lines.push(`Cross-origin isolated: ${report.browser.isolated}. WASM SIMD: ${caps.wasm?.simd}, threads usable: ${caps.wasm?.threads_usable}. Chromium flags: \`${report.chromium_flags.join(" ")}\`${report.software_gpu_forced ? " (SwiftShader forced)" : ""}.`);
  lines.push(`WebGPU adapter: ${gpu ? `${[gpu.vendor, gpu.architecture, gpu.device, gpu.description].filter(Boolean).join(" ") || "(no name)"}, classified **${gpu.kind}** (${gpu.reason})` : "none"}. WebNN contexts created: ${(caps.webnn?.contexts || []).filter((c) => c.created).map((c) => c.label).join(", ") || "none"}; ${caps.webnn?.npu?.label_en || "No verified NPU backend is available"}.`);
  if (env.load_avg) lines.push(`Load average (1, 5, 15 min) on ${env.cores} cores: ${env.load_avg.before.join(", ")} before, ${env.load_avg.after.join(", ")} after${Math.max(...env.load_avg.before, ...env.load_avg.after) > env.cores / 2 ? " (other work shared the machine: the timings are inflated by it)" : ""}.`);
  lines.push(`Medians of ${report.reps} runs. Every local result was checked identical to the reference before its time was counted.`, "");
  if (report.browser.tools_run === false) lines.push("Tool calls were not run (`--no-tools`): no Pyodide, no native comparison.", "");
  else {
    lines.push(`## Tool calls (Pyodide in a Worker), original Python vs local backends, and native CPython`, "");
    lines.push(`| Tool | Size | Backend | Python in the browser (ms) | Local backend (ms) | Speed-up | Native CPython (ms) | Same output |`);
    lines.push(`|---|---|---|---:|---:|---:|---:|:---:|`);
    for (const t of report.browser.tools) {
      const n = report.native?.calls?.find((c, i) => i === report.browser.tools.indexOf(t));
      const sameNative = n?.output_sha256 && n.output_sha256 === t.output_sha256;
      lines.push(`| \`${t.tool.replace("native.", "")}\` | ${t.size} | ${t.backend} | ${t.reference_ms} | ${t.local_ms} | ${t.speedup ? `${t.speedup}×` : "—"} | ${n ? n.ms.toFixed(1) : "—"} | ${t.same_output && (n ? sameNative : true) ? "yes" : "**no**"} |`);
    }
  }
  lines.push("", `## The CPU kernels alone (no Python, no JSON)`, "", "Every WebAssembly SIMD result was compared with the JavaScript kernel's before it was timed.", "", `| Kernel | Size | ms | ns per cell |`, `|---|---|---:|---:|`);
  for (const k of report.browser.kernels) lines.push(`| ${k.kernel} | ${k.size} | ${k.ms ?? "—"} | ${k.ns_per_cell ?? (k.note || "—")} |`);
  lines.push("", `## WebGPU (the WGSL count kernel, exact parity with the CPU reference)`, "");
  const rows = report.browser.webgpu.filter((w) => w.workload);
  const legacy = report.browser.webgpu.filter((w) => !w.workload && w.ms !== undefined); // tcmstudio.browser-bench/1
  if (legacy.length) {
    lines.push(`| Size | Adapter | ms | Exact | Note |`, `|---|---|---:|:---:|---|`);
    for (const w of legacy) lines.push(`| ${w.size} | ${w.adapter} | ${w.ms} | ${w.exact ? "yes" : "**no**"} | ${w.note} |`);
  } else if (rows.length) {
    const v = (x) => (x === null || x === undefined ? "—" : x);
    lines.push("CPU: each kernel alone / through the bridge as Python calls it (JSON both ways), on the same input. GPU: the first call on a fresh device (adapter, device, pipeline, known-answer test), then warm calls end to end (packing, upload, dispatch, readback, the sample check, the digest and the packet's JSON), and the kernel alone from GPU timestamps where the device has them.", "");
    lines.push(`| Workload | Adapter | SIMD (ms) | JS (ms) | GPU first (ms) | GPU end to end (ms) | GPU kernel (ms) | Exact | Accelerated | Note |`, `|---|---|---:|---:|---:|---:|---:|:---:|:---:|---|`);
    for (const w of rows) {
      const cpu = (id) => `${v(w.cpu_baseline_ms[id])} / ${v(w.cpu_e2e_ms[id])}`;
      lines.push(`| ${w.workload.n} × ${w.workload.width} (${w.workload.operations} comparisons) | ${w.adapter.kind} | ${cpu("wasm-simd")} | ${cpu("js")} | ${v(w.gpu_cold_ms)} | ${v(w.gpu_e2e_ms)} | ${v(w.gpu_kernel_ms)} | ${w.exact === null ? "—" : w.exact ? "yes" : "**no**"} | ${w.accelerated === null ? "not assessed" : w.accelerated ? `yes (${w.speedup}×)` : "no"} | ${[w.note, w.fallback_reason].filter(Boolean).join("; ")} |`);
    }
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
  const flags = chromiumFlags(args);
  const loadBefore = os.loadavg().map((x) => Math.round(x * 100) / 100);
  const browser = await chromium.launch({ executablePath: args.chromium || process.env.CHROMIUM_PATH || undefined, args: flags });
  let result, browserVersion = null;
  try {
    browserVersion = `Chromium ${browser.version()}`;
    const ctx = await browser.newContext();
    const q = `?reps=${args.reps}${args.quick ? "&quick=1" : ""}${args.tools ? "" : "&tools=0"}${index ? `&index=${encodeURIComponent(index)}` : ""}`;
    result = await runPage(ctx, `${iso.origin}/test/runtime/bench.html${q}`, 1_800_000);
  } finally {
    await browser.close();
    iso.server.close();
  }
  const native = args.tools ? nativeTimes(result.tools || [], args.reps) : null;
  for (const t of result.tools || []) delete t.args;
  const report = {
    measured_at: new Date().toISOString(), reps: args.reps, chromium_flags: flags, software_gpu_forced: Boolean(args.softwareGpu),
    browser_version: browserVersion,
    environment: { cpu: os.cpus()[0]?.model || "unknown", cores: os.cpus().length, platform: `${os.platform()} ${os.release()} ${os.arch()}`, os: { type: os.type(), release: os.release(), version: typeof os.version === "function" ? os.version() : null, arch: os.arch() }, node: process.version,
      // the machine's 1, 5 and 15 minute load averages before and after (all zeros on Windows): other work inflates timings
      load_avg: { before: loadBefore, after: os.loadavg().map((x) => Math.round(x * 100) / 100) } },
    browser: result, native,
  };
  if (args.json) writeFileSync(args.json, JSON.stringify(report, null, 1));
  const md = markdown(report);
  if (args.md) writeFileSync(args.md, md);
  process.stdout.write(md);
  if (built) built.cleanup();
  process.exit(result.ok ? 0 : 1);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => { console.error(err?.stack || String(err)); process.exit(2); });
}
