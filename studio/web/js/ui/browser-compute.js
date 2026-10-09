// Settings → Compute → 浏览器算力 (docs/V2.md §13): what this browser offers for computing (device class, cores,
// memory, the WebGPU adapter and whether it is real hardware), the Python worker pool (automatic or a number, capped at
// cores − 1) and the GPU switch, the pool's live state, the browser benchmark, and a plain answer to "can the browser
// use the CPU and the GPU?". Numbers come from the browser and are labelled as such; a software renderer is never
// called a GPU.

import { lang, registerStrings, t } from "../core/i18n.js";
import { browserSettings, effective, holdWakeLock, workerCap } from "../runtime/device.js";
import { fill, formatDuration, h } from "./dom.js";
import { icon } from "./icons.js";
import { button, chip, keyValue, notice, progressBar, section, selectField, statusDot, switchControl } from "./primitives.js";

registerStrings("zh", {
  "bc.title": "浏览器算力",
  "bc.lede": "Python 工具和加速计算在你的浏览器里运行，用的是这台设备的 CPU（和可用时的 GPU）；数据不离开这台设备。",
  "bc.device": "本机（浏览器所报告）",
  "bc.detecting": "正在检测…",
  "bc.class": "设备类别",
  "bc.class.phone-low": "低内存手机",
  "bc.class.phone": "手机",
  "bc.class.tablet": "平板（或大内存手机）",
  "bc.class.desktop": "电脑",
  "bc.cores": "CPU 核心",
  "bc.cores.value": "{n} 个",
  "bc.cores.ios": "{n} 个（iPhone 上的浏览器一律报告 4 个，不是实际核心数）",
  "bc.cores.unknown": "浏览器未提供",
  "bc.memory": "内存",
  "bc.memory.value": "约 {gb} GB（浏览器给出的取整值）",
  "bc.memory.unknown": "浏览器未提供（Safari 与 Firefox 不报告内存）",
  "bc.gpu": "WebGPU",
  "bc.gpu.hardware": "硬件 GPU：{name}",
  "bc.gpu.software": "软件渲染器（{name}）不是 GPU：WebGPU 由 CPU 模拟",
  "bc.gpu.no_adapter": "浏览器支持 WebGPU，但没有可用的适配器",
  "bc.gpu.no_api": "此浏览器不支持 WebGPU",
  "bc.plan": "按此类别",
  "bc.plan.value": "Python 进程最多 {py} 个 · 加速计算 JavaScript Worker {js} 个",
  "bc.workers": "Python 进程数",
  "bc.workers.auto": "自动（{n} 个）",
  "bc.workers.n": "{n} 个",
  "bc.workers.help": "每个 Python 进程单线程运行，约占 100 MB 内存（载入 scipy / pandas 后约 300 MB）。上限为 CPU 核心数减 1（{cap} 个）。",
  "bc.workers.phone": "手机上默认只用 1 个：内存不足时，系统会直接重新载入页面。",
  "bc.gpu_switch": "允许使用 GPU（WebGPU）",
  "bc.gpu_switch.desc": "只在检测到硬件 GPU 时使用。GPU 只做整数计数；p 值和多重检验校正仍在 CPU 上以双精度计算。",
  "bc.gpu_switch.none": "这台设备当前没有可用的硬件 GPU，开启后也只用 CPU。",
  "bc.status": "运行状态",
  "bc.status.idle": "尚未启动：首次调用 Python 工具时启动。",
  "bc.status.value": "{live} 个 Python 进程在运行（上限 {size} 个），{busy} 个正在计算",
  "bc.status.booting": "，{n} 个正在启动",
  "bc.status.shrunk": "有 {n} 个辅助进程未能启动或出错，进程数已自动减少。",
  "bc.worker.primary": "主进程",
  "bc.worker.secondary": "辅助进程 {n}",
  "bc.worker.ready": "空闲",
  "bc.worker.busy": "计算中",
  "bc.worker.booting": "启动中",
  "bc.bench": "浏览器算力测试",
  "bc.bench.lede": "用一组合成数据（固定种子）在每种可用的计算引擎上运行同一项置换检验，比较用时，并检查结果是否与参考结果逐位一致。不使用你的数据。",
  "bc.bench.run": "浏览器算力测试",
  "bc.bench.stop": "停止测试",
  "bc.bench.running": "正在测试…",
  "bc.bench.stopped": "测试已停止。",
  "bc.bench.failed": "测试未能完成：{message}",
  "bc.bench.unavailable": "此页面没有浏览器算力测试模块。",
  "bc.bench.engine": "引擎",
  "bc.bench.on": "设备",
  "bc.bench.time": "用时",
  "bc.bench.equal": "结果",
  "bc.bench.note": "说明",
  "bc.bench.same": "✓ 逐位一致",
  "bc.bench.differs": "✗ 不一致",
  "bc.bench.unchecked": "未比较",
  "bc.bench.software": "软件渲染器（{name}）不是 GPU",
  "bc.bench.world": "合成数据：{pathways} 条通路 × {permutations} 次置换（固定种子）。",
  "bc.bench.world_generic": "合成数据（固定种子），不是你的数据。",
  "bc.bench.mismatch": "有引擎的结果与参考结果不一致；实际计算时，不一致的结果不会被采用。",
  "bc.bench.timing": "用时只反映这台设备此刻的情况，会随后台负载变化。",
  "bc.engine.webgpu": "WebGPU 计算着色器",
  "bc.engine.js-workers": "JavaScript 并行 Worker",
  "bc.engine.python": "Python（Pyodide，单线程）",
  "bc.faq.q": "浏览器能直接使用 CPU / GPU 吗？",
  "bc.faq.cpu": "能使用 CPU：通过 WebAssembly，在多个并行的 Web Worker 中用到这台设备的全部 CPU 核心（每个 Worker 一个线程）。",
  "bc.faq.gpu": "能使用 GPU：通过 WebGPU 计算着色器（Chrome / Edge 113 及以上、Safari 26 / iOS 26、Android 版 Chrome 121 及以上）。",
  "bc.faq.cuda": "不能使用 CUDA。依赖 CUDA 的重计算（蛋白结构预测、大规模分子对接、FASTQ 定量）仍在本机 Runner 上运行。",
});
registerStrings("en", {
  "bc.title": "Browser compute",
  "bc.lede": "Python tools and accelerated steps run in your browser, on this device's CPU (and its GPU when there is one); the data stays on this device.",
  "bc.device": "This device (as the browser reports it)",
  "bc.detecting": "Detecting…",
  "bc.class": "Device class",
  "bc.class.phone-low": "Phone (low memory)",
  "bc.class.phone": "Phone",
  "bc.class.tablet": "Tablet (or a phone with a tablet's memory)",
  "bc.class.desktop": "Computer",
  "bc.cores": "CPU cores",
  "bc.cores.value": "{n}",
  "bc.cores.ios": "{n} (browsers on an iPhone always report 4, not the real count)",
  "bc.cores.unknown": "not reported by the browser",
  "bc.memory": "Memory",
  "bc.memory.value": "about {gb} GB (rounded by the browser)",
  "bc.memory.unknown": "not reported by the browser (Safari and Firefox do not report it)",
  "bc.gpu": "WebGPU",
  "bc.gpu.hardware": "Hardware GPU: {name}",
  "bc.gpu.software": "Software renderer ({name}), not a GPU: WebGPU is emulated on the CPU",
  "bc.gpu.no_adapter": "The browser supports WebGPU but offers no adapter",
  "bc.gpu.no_api": "This browser does not support WebGPU",
  "bc.plan": "For this class",
  "bc.plan.value": "up to {py} Python workers · {js} JavaScript workers for accelerated steps",
  "bc.workers": "Python workers",
  "bc.workers.auto": "Automatic ({n})",
  "bc.workers.n": "{n}",
  "bc.workers.help": "Each Python worker runs on one thread and needs about 100 MB of memory (about 300 MB with scipy / pandas). At most the number of CPU cores minus one ({cap}).",
  "bc.workers.phone": "Phones use 1 by default: when memory runs out, the system simply reloads the page.",
  "bc.gpu_switch": "Allow the GPU (WebGPU)",
  "bc.gpu_switch.desc": "Used only when a hardware GPU is found. The GPU only counts integers; p-values and multiple-testing corrections are computed on the CPU in double precision.",
  "bc.gpu_switch.none": "This device has no hardware GPU available right now; with this on, the CPU is still used.",
  "bc.status": "Status",
  "bc.status.idle": "Not started: it starts with the first Python tool call.",
  "bc.status.value": "{live} Python worker(s) running (up to {size}), {busy} computing",
  "bc.status.booting": ", {n} starting",
  "bc.status.shrunk": "{n} helper worker(s) failed to start or crashed; the pool was made smaller.",
  "bc.worker.primary": "Primary",
  "bc.worker.secondary": "Helper {n}",
  "bc.worker.ready": "idle",
  "bc.worker.busy": "computing",
  "bc.worker.booting": "starting",
  "bc.bench": "Browser benchmark",
  "bc.bench.lede": "Runs the same permutation test on synthetic data (fixed seed) with each available engine, compares the times, and checks that each result equals the reference bit for bit. None of your data is used.",
  "bc.bench.run": "Run the browser benchmark",
  "bc.bench.stop": "Stop the benchmark",
  "bc.bench.running": "Benchmark running…",
  "bc.bench.stopped": "The benchmark was stopped.",
  "bc.bench.failed": "The benchmark did not finish: {message}",
  "bc.bench.unavailable": "This page has no browser benchmark module.",
  "bc.bench.engine": "Engine",
  "bc.bench.on": "Device",
  "bc.bench.time": "Time",
  "bc.bench.equal": "Result",
  "bc.bench.note": "Note",
  "bc.bench.same": "✓ identical",
  "bc.bench.differs": "✗ differs",
  "bc.bench.unchecked": "not compared",
  "bc.bench.software": "software renderer ({name}), not a GPU",
  "bc.bench.world": "Synthetic data: {pathways} pathways × {permutations} permutations (fixed seed).",
  "bc.bench.world_generic": "Synthetic data (fixed seed), not your data.",
  "bc.bench.mismatch": "An engine's result differs from the reference; in a real computation a result that differs is never used.",
  "bc.bench.timing": "Times describe this device at this moment and change with background load.",
  "bc.engine.webgpu": "WebGPU compute shader",
  "bc.engine.js-workers": "JavaScript workers",
  "bc.engine.python": "Python (Pyodide, one thread)",
  "bc.faq.q": "Can the browser use the CPU / GPU directly?",
  "bc.faq.cpu": "The CPU, yes: through WebAssembly, every CPU core of this device in parallel Web Workers (one thread each).",
  "bc.faq.gpu": "The GPU, yes: through WebGPU compute shaders (Chrome / Edge 113+, Safari 26 / iOS 26, Chrome for Android 121+).",
  "bc.faq.cuda": "CUDA, no. Heavy CUDA work (protein structure prediction, docking at scale, FASTQ quantification) stays on the local runner.",
});

const SOFTWARE = /swiftshader|llvmpipe|lavapipe|software|basic render/i;

// The benchmark outlives a re-render of the settings page: its state lives here, and whichever section is on the
// page shows it.
const bench = { running: false, rows: null, world: null, error: null, stopped: false, fraction: null, controller: null, listeners: new Set() };

function benchChanged() {
  for (const fn of [...bench.listeners]) fn();
}

/** The section for Settings → Compute. */
export function browserComputeSection(app) {
  const body = h("div.stack.stack--lg");
  const rt = app.runtimes?.browser;
  let device = null;
  const render = () => {
    // a control that had focus gets it back after the rebuild (keyboard users must not be thrown to the top)
    const focused = body.contains(document.activeElement) ? document.activeElement?.dataset?.key : null;
    fill(body, ...content(app, rt, device));
    if (focused) body.querySelector(`[data-key="${focused}"]`)?.focus({ preventScroll: true });
  };
  render();
  // the device report (a WebGPU probe) arrives later; the runtime caches it
  Promise.resolve(rt?.device?.()).then((d) => {
    device = d || null;
    render();
  }).catch(() => {});
  // live pool and benchmark updates, until this section leaves the page
  const offPool = rt?.onPool?.(() => {
    if (!body.isConnected) { offPool?.(); return; }
    const box = body.querySelector("[data-bc-status]");
    if (box) fill(box, ...statusLines(rt));
  });
  const onBench = () => {
    if (!body.isConnected) { bench.listeners.delete(onBench); return; }
    const box = body.querySelector("[data-bc-bench]");
    if (box) {
      const hadFocus = box.contains(document.activeElement);
      fill(box, ...benchContent(app, rt));
      if (hadFocus) box.querySelector("button")?.focus({ preventScroll: true });
    }
  };
  bench.listeners.add(onBench);
  return section({ title: t("bc.title"), children: body, className: "browser-compute" });
}

function content(app, rt, device) {
  return [
    h("p.muted.small", t("bc.lede")),
    deviceCard(device),
    controls(app, rt, device),
    h("div.stack", h("p.field__label", t("bc.status")), h("div.stack", { "data-bc-status": "", role: "status", "aria-live": "polite" }, statusLines(rt))),
    h("div.stack", { "data-bc-bench": "" }, benchContent(app, rt)),
    faq(),
  ];
}

// --------------------------------------------------------------------------------------------------------- device

function deviceCard(d) {
  const head = h("div.rt-card__head",
    h("span.rt-card__icon", icon("gauge")),
    h("div.rt-card__title", h("p.rt-card__name", t("bc.device"))),
    d ? chip({ label: t(`bc.class.${d.cls}`), tone: "navy", icon: d.mobile ? "monitor" : "laptop" }) : null);
  if (!d) return h("section.rt-card", head, h("p.rt-card__note", t("bc.detecting")));
  const eff = effective(d, {});
  return h("section.rt-card", head, keyValue([
    [t("bc.class"), t(`bc.class.${d.cls}`)],
    [t("bc.cores"), d.cores ? t(d.ios ? "bc.cores.ios" : "bc.cores.value", { n: d.cores }) : h("span.muted", t("bc.cores.unknown"))],
    [t("bc.memory"), d.memory_gb ? t("bc.memory.value", { gb: d.memory_gb }) : h("span.muted", t("bc.memory.unknown"))],
    [t("bc.gpu"), gpuLine(d)],
    [t("bc.plan"), t("bc.plan.value", { py: eff.python_workers, js: eff.js_workers })],
  ]));
}

function gpuLine(d) {
  const ad = d.webgpu?.adapter;
  if (!d.webgpu?.api) return h("span.muted", t("bc.gpu.no_api"));
  if (!ad) return h("span.muted", t("bc.gpu.no_adapter"));
  const name = [ad.vendor, ad.architecture, ad.description].filter(Boolean).join(" ") || "—";
  if (d.gpu === "software") return h("span", statusDot("off"), " ", t("bc.gpu.software", { name: softwareName(name) }));
  return h("span", statusDot("ok"), " ", t("bc.gpu.hardware", { name }));
}

function softwareName(text) {
  const m = /swiftshader|llvmpipe|lavapipe|microsoft basic render(?: driver)?/i.exec(String(text || ""));
  if (!m) return String(text || "software");
  const word = m[0].toLowerCase();
  return word === "swiftshader" ? "SwiftShader" : word.startsWith("microsoft") ? "Microsoft Basic Render" : word;
}

// ------------------------------------------------------------------------------------------------------- controls

function controls(app, rt, device) {
  const s = browserSettings(app.state.settings.computeBrowser);
  const facts = device || { cores: globalThis.navigator?.hardwareConcurrency ?? null };
  const cap = workerCap(facts);
  const auto = device ? effective(device, { workers: "auto" }).python_workers : rt?.pool?.().size ?? 1;
  const options = [{ value: "auto", label: t("bc.workers.auto", { n: auto }) }];
  for (let n = 1; n <= cap; n++) options.push({ value: String(n), label: t("bc.workers.n", { n }) });
  const save = (patch) => app.setSetting({ computeBrowser: { ...s, ...patch } });
  const phone = device && (device.cls === "phone" || device.cls === "phone-low");
  const workers = selectField({
    label: t("bc.workers"), value: s.workers === "auto" ? "auto" : String(Math.min(s.workers, cap)), options,
    help: [t("bc.workers.help", { cap }), phone ? t("bc.workers.phone") : ""].filter(Boolean).join(" "),
    onChange: (v) => save({ workers: v === "auto" ? "auto" : Number(v) }),
  });
  workers.input.dataset.key = "bc-workers";
  const noGpu = device && device.gpu !== "hardware";
  const gpu = switchControl({
    label: t("bc.gpu_switch"),
    description: [t("bc.gpu_switch.desc"), noGpu ? t("bc.gpu_switch.none") : ""].filter(Boolean).join(" "),
    checked: s.gpu !== "off",
    onChange: (on) => save({ gpu: on ? "auto" : "off" }),
  });
  gpu.querySelector("[role=switch]")?.setAttribute("data-key", "bc-gpu");
  return h("div.stack", workers, gpu);
}

function statusLines(rt) {
  const pool = rt?.pool?.();
  if (!pool) return [h("p.muted.small", t("bc.status.idle"))];
  if (!pool.live && !pool.booting) return [h("p.muted.small", t("bc.status.idle"))];
  const line = t("bc.status.value", { live: pool.live, size: pool.size, busy: pool.busy }) + (pool.booting ? t("bc.status.booting", { n: pool.booting }) : "");
  const workers = (pool.workers || []).map((w) => {
    const name = w.primary ? t("bc.worker.primary") : t("bc.worker.secondary", { n: w.index });
    const st = w.state === "booting" ? "booting" : w.busy ? "busy" : "ready";
    const tone = st === "busy" ? "loading" : st === "booting" ? "busy" : "ok";
    return h("li", statusDot(tone), " ", `${name} · ${t(`bc.worker.${st}`)}`);
  });
  return [
    h("p.small", line),
    workers.length ? h("ul.rt-card__facts", { role: "list" }, workers) : null,
    pool.shrunk ? h("p.small.muted", t("bc.status.shrunk", { n: pool.shrunk })) : null,
  ];
}

// ------------------------------------------------------------------------------------------------------ benchmark

function benchContent(app, rt) {
  const parts = [h("p.field__label", t("bc.bench")), h("p.muted.small", t("bc.bench.lede"))];
  const action = bench.running
    ? button({ label: t("bc.bench.stop"), icon: "stop", attrs: { "data-key": "bc-bench" }, onClick: () => bench.controller?.abort() })
    : button({ label: t("bc.bench.run"), icon: "gauge", variant: "primary", attrs: { "data-key": "bc-bench" }, onClick: () => runBench(rt) });
  parts.push(h("div.row", action));
  if (bench.running) {
    parts.push(progressBar({ value: bench.fraction, label: t("bc.bench.running") }));
    parts.push(h("p.muted.small", { role: "status" }, t("bc.bench.running")));
  }
  if (bench.stopped) parts.push(h("p.muted.small", { role: "status" }, t("bc.bench.stopped")));
  if (bench.error) parts.push(notice({ tone: "warn", body: bench.error, role: "status" }));
  if (bench.rows?.length) parts.push(...benchResults(bench.rows, bench.world));
  return parts;
}

async function runBench(rt) {
  if (bench.running) return;
  let mod = null;
  try {
    mod = await import("../accel/bench.js");
  } catch {
    mod = null;
  }
  if (typeof mod?.runBenchmark !== "function") {
    Object.assign(bench, { error: t("bc.bench.unavailable"), rows: null, stopped: false });
    benchChanged();
    return;
  }
  const controller = new AbortController();
  Object.assign(bench, { running: true, rows: null, world: null, error: null, stopped: false, fraction: null, controller });
  benchChanged();
  // a long run on a phone: keep the screen on (the tap that started it allows the request on Safari)
  const release = await holdWakeLock();
  try {
    const device = rt?.computeDevice ? await rt.computeDevice() : await rt?.device?.();
    const s = browserSettings(rt?.computeDevice ? device?.settings : null);
    const out = await mod.runBenchmark({
      device, settings: { ...s, compute: { browser: { ...s } } }, signal: controller.signal,
      onProgress: (p) => {
        const f = typeof p === "number" ? (p > 1 ? p / 100 : p) : typeof p?.fraction === "number" ? p.fraction : null;
        bench.fraction = f === null ? null : Math.max(0, Math.min(1, f));
        const bar = document.querySelector("[data-bc-bench] .progress");
        bar?.set?.(bench.fraction);
      },
    });
    bench.rows = Array.isArray(out?.rows) ? out.rows : [];
    bench.world = out?.world ?? null;
  } catch (err) {
    if (controller.signal.aborted) bench.stopped = true;
    else bench.error = t("bc.bench.failed", { message: String(err?.message || err) });
  } finally {
    release();
    bench.running = false;
    bench.controller = null;
    benchChanged();
  }
}

function benchResults(rows, world) {
  const table = h("table",
    h("thead", h("tr", ["engine", "on", "time", "equal", "note"].map((k) => h("th", { scope: "col" }, t(`bc.bench.${k}`))))),
    h("tbody", rows.map((r) => {
      const label = t(`bc.engine.${r.engine}`);
      const engine = label === `bc.engine.${r.engine}` ? String(r.engine || "—") : label;
      const dev = String(r.device || "");
      const software = r.engine === "webgpu" && SOFTWARE.test(dev);
      return h("tr",
        h("td", engine),
        h("td", dev || "—", software ? h("div", chip({ label: t("bc.bench.software", { name: softwareName(dev) }), tone: "slate" })) : null),
        h("td.num", Number.isFinite(Number(r.ms)) ? formatDuration(Number(r.ms)) : "—"),
        h("td", r.equal === true ? chip({ label: t("bc.bench.same"), tone: "jade" }) : r.equal === false ? chip({ label: t("bc.bench.differs"), tone: "vermilion" }) : h("span.muted", t("bc.bench.unchecked"))),
        h("td.small", r.note ? String(r.note) : ""));
    })));
  const out = [h("div.tablewrap", { role: "region", "aria-label": t("bc.bench"), tabindex: "0" }, table)];
  const w = world && typeof world === "object" ? world : null;
  out.push(h("p.muted.small", w && Number.isFinite(Number(w.pathways)) && Number.isFinite(Number(w.permutations))
    ? t("bc.bench.world", { pathways: fmt(w.pathways), permutations: fmt(w.permutations) })
    : t("bc.bench.world_generic")));
  if (rows.some((r) => r.equal === false)) out.push(notice({ tone: "warn", body: t("bc.bench.mismatch") }));
  out.push(h("p.muted.small", t("bc.bench.timing")));
  return out;
}

function fmt(n) {
  return new Intl.NumberFormat(lang() === "zh" ? "zh-CN" : "en-US").format(Number(n));
}

// ------------------------------------------------------------------------------------------------------------ FAQ

function faq() {
  return h("section.rt-card",
    h("div.rt-card__head", h("span.rt-card__icon", icon("help")), h("div.rt-card__title", h("h3.rt-card__name", t("bc.faq.q")))),
    h("ul.rt-card__facts", { role: "list" },
      h("li", icon("cpu", { size: 14 }), " ", t("bc.faq.cpu")),
      h("li", icon("gpu", { size: 14 }), " ", t("bc.faq.gpu")),
      h("li", icon("laptop", { size: 14 }), " ", t("bc.faq.cuda"))));
}
