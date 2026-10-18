// The benchmark driver's own rules (bench.mjs): which Chromium switches each option sets, and that a software
// adapter's WebGPU rows are reported without a speed-up. The benchmark itself runs in a browser (bench-page.mjs).
import assert from "node:assert/strict";
import { test } from "node:test";
import { chromiumFlags, markdown } from "./bench.mjs";

test("--gpu-flags leaves the adapter to the browser; only --software-gpu forces SwiftShader", () => {
  assert.deepEqual(chromiumFlags({}), ["--enable-unsafe-webgpu"]);
  const gpu = chromiumFlags({ gpuFlags: true });
  assert.ok(!gpu.some((f) => /swiftshader/i.test(f)), "a machine with a GPU is not pushed onto the software renderer");
  assert.deepEqual(gpu, ["--enable-unsafe-webgpu", "--enable-features=Vulkan,WebMachineLearningNeuralNetwork"],
    "Vulkan, which Chromium on Linux generally needs for a hardware adapter");
  assert.deepEqual(chromiumFlags({ webnn: true }), ["--enable-unsafe-webgpu", "--enable-features=WebMachineLearningNeuralNetwork"]);
  assert.deepEqual(chromiumFlags({ softwareGpu: true, webnn: true }),
    ["--enable-unsafe-webgpu", "--use-angle=swiftshader", "--enable-features=Vulkan,WebMachineLearningNeuralNetwork"]);
  assert.equal(chromiumFlags({ softwareGpu: true, gpuFlags: true }).filter((f) => f.startsWith("--enable-features")).length, 1, "one --enable-features switch");
});

test("a software adapter's rows carry no speed-up and say why", () => {
  const adapter = { kind: "software", vendor: "google", architecture: "swiftshader", device: "", description: "", fallback: true, reason: "the browser marks it a fallback adapter" };
  const row = {
    size: "60 seqs × 1000", workload: { n: 60, width: 1000, operations: 1770000 }, adapter, software: true, exact: true,
    cpu_baseline_ms: { "wasm-simd": 0.3, js: 3.9 }, cpu_e2e_ms: { "wasm-simd": 0.8, js: 8 },
    gpu_cold_ms: 330, gpu_e2e_ms: 0.1, gpu_kernel_ms: 0.05, accelerated: null, speedup: null, fallback_reason: null,
    note: "software renderer: these timings are not a GPU's, and no speed-up is derived from them",
  };
  const report = {
    measured_at: "2026-10-18T00:00:00Z", reps: 1, chromium_flags: chromiumFlags({ softwareGpu: true }), software_gpu_forced: true,
    browser_version: "Chromium 141", environment: { cpu: "test CPU", cores: 4, platform: "linux", load_avg: { before: [4.3, 2.8, 1.8], after: [3.9, 3, 2] } }, native: null,
    browser: { user_agent: "test", isolated: true, tools_run: false, tools: [], kernels: [], webgpu: [row], failures: [],
      caps: { wasm: { simd: true }, webgpu: { adapter }, webnn: { api: false, npu: { label_en: "No verified NPU backend is available" } } } },
  };
  const md = markdown(report);
  assert.match(md, /classified \*\*software\*\*/);
  assert.match(md, /SwiftShader forced/);
  assert.match(md, /\| software \|.*\| not assessed \| software renderer/);
  assert.doesNotMatch(md, /\d×\)/, "no speed-up anywhere for a software run");
  assert.match(md, /No verified NPU backend is available/);
  assert.doesNotMatch(md, /## Tool calls/, "tool calls not run, so no empty table");
  assert.match(md, /Load average \(1, 5, 15 min\) on 4 cores: 4\.3, 2\.8, 1\.8 before, 3\.9, 3, 2 after \(other work shared the machine/);
});

test("a tcmstudio.browser-bench/1 record (2026-10-10) still renders", async () => {
  const { readFileSync } = await import("node:fs");
  const report = JSON.parse(readFileSync(new URL("../../../docs/benchmarks/2026-10-10-chromium-141-linux.json", import.meta.url), "utf8"));
  assert.equal(report.browser.schema, "tcmstudio.browser-bench/1");
  const md = markdown(report);
  assert.match(md, /\| Size \| Adapter \| ms \| Exact \| Note \|/);
  assert.match(md, /\| 60 seqs × 1000 \| google swiftshader \| 130\.82 \| yes \| software renderer/);
  assert.match(md, /## Tool calls/);
});
