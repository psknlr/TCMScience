// The science kernels' device check (compute/device-check.js) in a real browser; gpu-science.mjs drives it and reads
// globalThis.__result. ?software=1 lets the check compute on a software adapter (its report then says "software, not
// evidence"); without it, a software-only browser's report says "no physical adapter". ?sizes=32x2048,256x10504 and
// ?reps=N choose what is measured; ?quick=1 the two smallest sizes. The page also checks that a production ScienceGPU
// refuses the software adapter it found, and that the calibration was kept in this origin's IndexedDB.

import { loadCalibration } from "../../js/compute/calibration.js";
import { DEVICE_CHECK_SIZES, QUICK_SIZES, deviceCheck } from "../../js/compute/device-check.js";
import { ScienceGPU } from "../../js/compute/science-gpu.js";

const q = new URLSearchParams(location.search);
const allowSoftware = q.get("software") === "1";
const reps = Math.max(1, Number(q.get("reps") || 3));
const sizes = q.get("sizes")
  ? q.get("sizes").split(",").map((s) => s.split("x").map(Number))
  : q.get("quick") === "1" ? QUICK_SIZES : DEVICE_CHECK_SIZES;
const $ = (id) => document.getElementById(id);
const say = (s) => { $("status").textContent = s; };
const cell = (text, cls = "") => { const td = document.createElement("td"); td.textContent = text; if (cls) td.className = cls; return td; };
const yes = (v) => (v === true ? "yes" : v === false ? "NO" : "—");

function show(report) {
  const a = report.device.adapter;
  $("adapter").textContent = a
    ? `Adapter: ${[a.vendor, a.architecture, a.device, a.description].filter(Boolean).join(" ") || "(no name)"}, ${a.kind} (${a.reason}).${report.label ? ` ${report.label}.` : ""}`
    : `No WebGPU adapter${report.device.gpu_error ? `: ${report.device.gpu_error.message}` : ""}.`;
  const rows = $("rows");
  rows.replaceChildren();
  for (const s of report.kernels["tanimoto.best"].sizes) {
    const tr = document.createElement("tr");
    const exact = s.exact["wasm-simd"] !== false && s.exact.webgpu !== false;
    tr.append(cell(`${s.queries} × ${s.refs}`), cell(`SIMD ${yes(s.exact["wasm-simd"])}, GPU ${yes(s.exact.webgpu)}`, exact ? "ok" : "bad"),
      cell(String(s.timings.js?.ms ?? "—"), "ms"), cell(String(s.timings["wasm-simd"]?.ms ?? "—"), "ms"), cell(String(s.timings.webgpu?.ms ?? "—"), "ms"),
      cell(`${s.accelerated === null ? "no verdict" : s.accelerated ? "accelerated" : "not faster"}: ${s.reason}`));
    rows.append(tr);
  }
}

async function browserInfo() {
  const info = { user_agent: navigator.userAgent, brands: null, platform: null, platform_version: null, architecture: null };
  try {
    const d = navigator.userAgentData;
    if (d?.getHighEntropyValues) {
      const v = await d.getHighEntropyValues(["platform", "platformVersion", "architecture", "fullVersionList"]);
      Object.assign(info, { brands: v.fullVersionList || d.brands || null, platform: v.platform || null, platform_version: v.platformVersion || null, architecture: v.architecture || null });
    }
  } catch { /* not offered here */ }
  return info;
}

try {
  say("running the device check");
  const report = await deviceCheck({
    allowSoftware, sizes, reps,
    onProgress: (p) => say(p.stage === "size" ? `measuring ${p.size.queries} × ${p.size.refs}` : p.stage === "gpu" ? "asking for the GPU and running its known-answer test" : "done"),
  });
  show(report);
  // production never computes on a software adapter
  let productionRefusesSoftware = null;
  if (report.device.adapter?.kind === "software") {
    const production = new ScienceGPU();
    try { await production.ready(); productionRefusesSoftware = false; } catch (err) { productionRefusesSoftware = err?.code === "software_adapter"; } finally { production.stop(); }
  }
  let readBack = null;
  if (report.stored?.ok) readBack = (await loadCalibration(report.device.fingerprint.id))?.id === report.id;
  const failures = [];
  if (!report.ok) failures.push(`the device check failed: ${report.verdict} (${report.verdict_reason})`);
  if (productionRefusesSoftware === false) failures.push("a production ScienceGPU computed on a software adapter");
  if (readBack === false) failures.push("the calibration read back from IndexedDB is not the one written");
  say(`${report.verdict}${failures.length ? `; ${failures.join("; ")}` : ""}`);
  globalThis.__result = {
    ok: failures.length === 0, failures, report, production_refuses_software: productionRefusesSoftware, calibration_read_back: readBack,
    browser: await browserInfo(), isolated: Boolean(globalThis.crossOriginIsolated),
  };
} catch (err) {
  say(`failed: ${err?.message || err}`);
  globalThis.__result = { ok: false, failures: [String(err?.stack || err)] };
}
