// Device classes for browser compute (docs/V2.md §13.1). One page decides how many Python workers it runs, how many
// plain JS workers an accelerator may use, whether the scientific tier loads by itself, how much corpus it caches and
// whether a GPU is worth using, from what the browser says about the machine:
//
//   classify({cores, memory_gb, mobile, ios, tablet, webgpu, persisted}) → {cls, python_workers, js_workers, sci_auto,
//                                                                           corpus_cache_mb, gpu}
//
// The browser's numbers are coarse and sometimes withheld: iOS reports 4 cores for every iPhone and no memory at all,
// Chrome rounds deviceMemory to a power of two (≤ 8 on Android). Memory decides before cores: on a phone the tab is
// killed long before the CPU is the limit. A software WebGPU adapter (SwiftShader, llvmpipe, lavapipe, Microsoft
// Basic Render) emulates a GPU on the CPU, so it never counts as one.

/** Never more Python workers than this, whatever the settings say. */
export const MAX_WORKERS = 8;
/** Memory one Python worker of the scientific tier may need (numpy + scipy + pandas imported), in MB. */
const WORKER_MB = 300;
const SOFTWARE_GPU = /swiftshader|llvmpipe|lavapipe|software|microsoft basic render/i;
export const CLASSES = ["phone-low", "phone", "tablet", "desktop"];

/**
 * The device class and what it allows. Pure: every input is a plain value (`webgpu` is the device report's
 * {api, adapter} or one of "hardware" | "software" | "none").
 */
export function classify({ cores = null, memory_gb = null, mobile = false, ios = false, tablet = false, webgpu = null, persisted = false } = {}) {
  const c = positive(cores) || 2;
  const mem = positive(memory_gb);
  const gpu = gpuKind(webgpu);
  let cls;
  if (tablet) cls = "tablet";                                  // iPad (it reports no memory) or a tablet UA
  else if (ios) cls = "phone";                                  // every iPhone, whatever an emulator says of memory
  else if (mobile && mem !== null && mem <= 2) cls = "phone-low";
  else if (mobile && mem !== null && mem >= 6) cls = "tablet";  // a phone with a tablet's memory is treated as one
  else if (mobile) cls = "phone";                               // Android with 3–4 GB or unknown
  else cls = "desktop";
  const spare = Math.max(1, c - 1);                             // one core stays with the page
  switch (cls) {
    case "phone-low":
      return { cls, python_workers: 1, js_workers: Math.min(2, spare), sci_auto: false, corpus_cache_mb: 64, gpu };
    case "phone":
      return { cls, python_workers: 1, js_workers: Math.min(2, spare), sci_auto: false, corpus_cache_mb: persisted ? 256 : 64, gpu };
    case "tablet":
      return { cls, python_workers: Math.min(2, spare), js_workers: Math.min(4, spare), sci_auto: false, corpus_cache_mb: 256, gpu };
    default: {
      // a quarter of the reported memory for Python heaps; memory unknown (Safari, Firefox) → 2
      const byMemory = mem === null ? 2 : Math.floor((mem * 1024) / 4 / WORKER_MB);
      return { cls, python_workers: clamp(Math.min(spare, byMemory), 1, 4), js_workers: Math.min(spare, 8), sci_auto: true, corpus_cache_mb: 512, gpu };
    }
  }
}

/** "hardware" | "software" | "none" from a device report's webgpu ({api, adapter}) or an already decided string. */
export function gpuKind(webgpu) {
  if (webgpu === "hardware" || webgpu === "software" || webgpu === "none") return webgpu;
  const ad = webgpu?.adapter;
  if (!ad) return "none";
  const named = `${ad.vendor || ""} ${ad.architecture || ""} ${ad.description || ""}`;
  return ad.software || ad.fallback || SOFTWARE_GPU.test(named) ? "software" : "hardware";
}

/** The most Python workers the settings may ask for on this device: cores − 1, at least 1, at most MAX_WORKERS. */
export function workerCap(device = {}) {
  return clamp((positive(device.cores) || 2) - 1, 1, MAX_WORKERS);
}

/** settings.computeBrowser, checked: {workers: "auto" | 1..8, gpu: "auto" | "on" | "off"} ("on": prefer the GPU). */
export function browserSettings(raw) {
  const s = raw && typeof raw === "object" ? raw : {};
  const n = Number(s.workers);
  const workers = s.workers !== "auto" && Number.isInteger(n) && n >= 1 && n <= MAX_WORKERS ? n : "auto";
  return { workers, gpu: s.gpu === "off" || s.gpu === "on" ? s.gpu : "auto" };
}

/**
 * What the page actually uses: the class's numbers, a manual worker count (capped at cores − 1), and the GPU only
 * when there is a hardware adapter and the settings allow it.
 */
export function effective(device = {}, raw = {}) {
  const s = browserSettings(raw);
  const base = device.cls ? device : { ...device, ...classify(device) };
  const cap = workerCap(device);
  const python = s.workers === "auto" ? Math.min(base.python_workers, cap) : Math.min(s.workers, cap);
  return {
    cls: base.cls, python_workers: python, js_workers: base.js_workers, cap, manual: s.workers !== "auto",
    gpu: base.gpu, gpu_enabled: s.gpu !== "off" && base.gpu === "hardware", settings: s,
  };
}

// ------------------------------------------------------------------------------------------------------- detection

/**
 * Mobile, iOS and tablet from the navigator: userAgentData.mobile (Chromium), the UA, and an iPad that presents
 * itself as a Mac (iPadOS 13+) by its touch points.
 */
export function platformOf(nav = globalThis.navigator) {
  const ua = String(nav?.userAgent || "");
  const touch = Number(nav?.maxTouchPoints || 0);
  const ipadAsMac = /Macintosh/.test(ua) && touch > 1;
  const ipad = /iPad/.test(ua) || ipadAsMac;
  const ios = /iPhone|iPod/.test(ua) || ipad;
  const androidTablet = /Android/.test(ua) && !/Mobile/.test(ua);
  const tablet = ipad || androidTablet || /Tablet/.test(ua);
  const mobile = Boolean(nav?.userAgentData?.mobile) || /Android|iPhone|iPad|iPod|Mobile/.test(ua) || ipadAsMac;
  return { mobile, ios, tablet, touch };
}

/** The facts classify() needs that the navigator gives at once (no WebGPU probe): for sizing the pool. */
export function quickFacts(nav = globalThis.navigator) {
  return { cores: nav?.hardwareConcurrency ?? null, memory_gb: nav?.deviceMemory ?? null, ...platformOf(nav) };
}

/** The device report of CONTRACTS §5, from the page itself: needs no Python, so it never starts a worker. */
export async function detectDevice({ navigator: nav = globalThis.navigator, isolated = globalThis.crossOriginIsolated, timeoutMs = 3000 } = {}) {
  const out = {
    cores: nav?.hardwareConcurrency ?? null,
    memory_gb: nav?.deviceMemory ?? null,
    cross_origin_isolated: Boolean(isolated),
    webgpu: { api: Boolean(nav && "gpu" in nav), adapter: null },
    python: { device: "cpu", threads: 1, note: "Python tools run on the CPU (single thread per worker, WebAssembly)" },
  };
  if (nav?.gpu?.requestAdapter) {
    try {
      const adapter = await Promise.race([
        nav.gpu.requestAdapter({ powerPreference: "high-performance" }),
        new Promise((resolve) => setTimeout(() => resolve(undefined), timeoutMs)),
      ]);
      if (adapter) {
        const i = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : {}) || {};
        const fallback = Boolean(adapter.isFallbackAdapter ?? i.isFallbackAdapter);
        out.webgpu.adapter = {
          vendor: i.vendor || "", architecture: i.architecture || "", description: i.description || "",
          // SwiftShader, llvmpipe and friends are CPU emulators: not a GPU, whatever the API says
          software: fallback || SOFTWARE_GPU.test(`${i.vendor} ${i.architecture} ${i.description}`),
          fallback,
        };
      }
    } catch (err) {
      out.webgpu.error = String(err?.message || err);
    }
  }
  return out;
}

/**
 * The device report plus the platform, whether storage is persistent, and the class (flat: `device.cls`,
 * `device.js_workers`, `device.gpu`, …). This is what the pool, the settings page and an accelerator host read.
 */
export async function detect({ navigator: nav = globalThis.navigator, isolated = globalThis.crossOriginIsolated, timeoutMs = 3000 } = {}) {
  const report = await detectDevice({ navigator: nav, isolated, timeoutMs });
  const platform = platformOf(nav);
  let persisted = null;
  try {
    persisted = nav?.storage?.persisted ? Boolean(await nav.storage.persisted()) : null;
  } catch { persisted = null; }
  return { ...report, ...platform, persisted, ...classify({ ...report, ...platform, persisted: Boolean(persisted) }) };
}

// ------------------------------------------------------------------------------------------------------ wake lock

/**
 * Keep the screen on while a long computation runs (a phone dims and then suspends the page). Resolves to a release
 * function; never rejects: no API, a hidden page or a browser that wants a tap first all mean "no lock".
 */
export async function holdWakeLock(nav = globalThis.navigator) {
  let sentinel = null;
  try {
    if (nav?.wakeLock?.request && globalThis.document?.visibilityState !== "hidden") sentinel = await nav.wakeLock.request("screen");
  } catch { sentinel = null; }
  return () => {
    const s = sentinel;
    sentinel = null;
    try { s?.release?.(); } catch { /* already released */ }
  };
}

// --------------------------------------------------------------------------------------------------------- helpers

function positive(v) {
  const n = Number(v);
  return v !== null && v !== undefined && v !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}
