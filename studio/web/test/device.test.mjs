// Device classes (docs/V2.md §13.1): the table, the platform sniffing behind it, what settings may change, and the
// detection and wake-lock helpers against fake navigators.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { browserSettings, classify, detect, effective, gpuKind, holdWakeLock, platformOf, quickFacts, workerCap } from "../js/runtime/device.js";

const UA = {
  iphone: "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1",
  ipadAsMac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",
  pixel: "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36",
  androidTablet: "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  windows: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
};

test("an iPhone (4 cores, no deviceMemory) is a phone: one Python worker, numpy only, a small corpus cache", () => {
  const p = platformOf({ userAgent: UA.iphone, maxTouchPoints: 5 });
  assert.deepEqual({ mobile: p.mobile, ios: p.ios, tablet: p.tablet }, { mobile: true, ios: true, tablet: false });
  const c = classify({ cores: 4, memory_gb: null, ...p });
  assert.deepEqual(c, { cls: "phone", python_workers: 1, js_workers: 2, sci_auto: false, corpus_cache_mb: 64, gpu: "none" });
  assert.equal(classify({ cores: 4, ...p, persisted: true }).corpus_cache_mb, 256, "256 MB once storage is persistent");
  assert.equal(classify({ cores: 4, memory_gb: 8, ...p }).cls, "phone", "every iPhone is a phone (an emulator may report memory)");
});

test("Android phones by memory: ≤ 2 GB is phone-low, 3–4 GB or unknown a phone, ≥ 6 GB a tablet", () => {
  const p = platformOf({ userAgent: UA.pixel, userAgentData: { mobile: true } });
  assert.equal(p.mobile, true);
  assert.equal(p.ios, false);
  assert.deepEqual(classify({ cores: 8, memory_gb: 2, ...p }), { cls: "phone-low", python_workers: 1, js_workers: 2, sci_auto: false, corpus_cache_mb: 64, gpu: "none" });
  assert.deepEqual(classify({ cores: 8, memory_gb: 1, ...p }).cls, "phone-low");
  assert.deepEqual(classify({ cores: 8, memory_gb: 4, ...p }).cls, "phone");
  assert.deepEqual(classify({ cores: 8, memory_gb: null, ...p }).cls, "phone");
  const high = classify({ cores: 8, memory_gb: 8, ...p });
  assert.deepEqual(high, { cls: "tablet", python_workers: 2, js_workers: 4, sci_auto: false, corpus_cache_mb: 256, gpu: "none" });
  assert.equal(classify({ cores: 2, memory_gb: 2, ...p }).js_workers, 1, "a two-core phone keeps one core for the page");
});

test("an iPad (a Mac user agent with touch points) is a tablet", () => {
  const p = platformOf({ userAgent: UA.ipadAsMac, maxTouchPoints: 5 });
  assert.deepEqual({ mobile: p.mobile, ios: p.ios, tablet: p.tablet }, { mobile: true, ios: true, tablet: true });
  assert.deepEqual(classify({ cores: 8, memory_gb: null, ...p }), { cls: "tablet", python_workers: 2, js_workers: 4, sci_auto: false, corpus_cache_mb: 256, gpu: "none" });
  assert.equal(classify({ cores: 4, ...p }).js_workers, 3);
  assert.equal(platformOf({ userAgent: UA.mac, maxTouchPoints: 0 }).mobile, false, "a real Mac is not an iPad");
  assert.equal(platformOf({ userAgent: UA.androidTablet }).tablet, true);
});

test("desktops: workers by memory and cores (memory unknown → 2), at most 4 Python and 8 JS workers", () => {
  const p = platformOf({ userAgent: UA.windows });
  assert.equal(p.mobile, false);
  assert.deepEqual(classify({ cores: 8, memory_gb: 8, ...p }), { cls: "desktop", python_workers: 4, js_workers: 7, sci_auto: true, corpus_cache_mb: 512, gpu: "none" });
  assert.equal(classify({ cores: 4, memory_gb: 8, ...p }).python_workers, 3, "cores − 1");
  assert.equal(classify({ cores: 4, memory_gb: 4, ...p }).python_workers, 3, "⌊4·1024/4/300⌋ = 3");
  assert.equal(classify({ cores: 16, memory_gb: 2, ...p }).python_workers, 1, "⌊2·1024/4/300⌋ = 1");
  assert.equal(classify({ cores: 16, memory_gb: 1, ...p }).python_workers, 1, "never fewer than one");
  assert.equal(classify({ cores: 8, memory_gb: null, ...p }).python_workers, 2, "memory unknown (Safari, Firefox) → 2");
  assert.equal(classify({ cores: 2, memory_gb: null, ...p }).python_workers, 1, "but not more than cores − 1");
  assert.equal(classify({ cores: 32, memory_gb: 32, ...p }).js_workers, 8);
  assert.equal(classify({ cores: null, memory_gb: null }).python_workers, 1, "cores unknown: assume two");
});

test("the GPU counts only when the adapter is real hardware", () => {
  assert.equal(gpuKind(null), "none");
  assert.equal(gpuKind({ api: true, adapter: null }), "none");
  assert.equal(gpuKind({ api: true, adapter: { vendor: "google", architecture: "swiftshader", description: "", software: true, fallback: true } }), "software");
  assert.equal(gpuKind({ api: true, adapter: { vendor: "mesa", architecture: "", description: "llvmpipe (LLVM 17.0.6, 256 bits)", software: false } }), "software", "named software renderers are software whatever the flag says");
  assert.equal(gpuKind({ api: true, adapter: { vendor: "microsoft", description: "Microsoft Basic Render Driver" } }), "software");
  assert.equal(gpuKind({ api: true, adapter: { vendor: "apple", architecture: "common-3", description: "", software: false } }), "hardware");
  assert.equal(classify({ cores: 8, memory_gb: 8, webgpu: { adapter: { vendor: "nvidia", architecture: "ada", software: false } } }).gpu, "hardware");
  assert.equal(classify({ webgpu: "software" }).gpu, "software");
});

test("settings may lower the pool or ask for more, never beyond cores − 1; the GPU can be turned off", () => {
  const desktop = { cores: 8, memory_gb: 8 };
  assert.equal(workerCap(desktop), 7);
  assert.equal(workerCap({ cores: 1 }), 1);
  assert.equal(workerCap({ cores: 64 }), 8);
  assert.deepEqual(effective(desktop, { workers: "auto" }).python_workers, 4);
  assert.deepEqual(effective(desktop, { workers: 2 }).python_workers, 2);
  assert.deepEqual(effective(desktop, { workers: 8 }).python_workers, 7, "capped at cores − 1");
  const iphone = { cores: 4, memory_gb: null, mobile: true, ios: true };
  assert.equal(effective(iphone, {}).python_workers, 1);
  assert.equal(effective(iphone, { workers: 3 }).python_workers, 3);
  assert.equal(effective(iphone, { workers: 3 }).manual, true);
  const gpu = { ...desktop, webgpu: "hardware" };
  assert.equal(effective(gpu, {}).gpu_enabled, true);
  assert.equal(effective(gpu, { gpu: "off" }).gpu_enabled, false);
  assert.equal(effective({ ...desktop, webgpu: "software" }, {}).gpu_enabled, false, "a software renderer is never used as a GPU");
  assert.deepEqual(browserSettings({ workers: "9", gpu: "on" }), { workers: "auto", gpu: "auto" });
  assert.deepEqual(browserSettings({ workers: 3, gpu: "off" }), { workers: 3, gpu: "off" });
  assert.deepEqual(browserSettings({ workers: 0 }), { workers: "auto", gpu: "auto" });
  assert.deepEqual(browserSettings(null), { workers: "auto", gpu: "auto" });
});

test("detect(): the report, the platform, persistence and the class, flat", async () => {
  const nav = {
    userAgent: UA.pixel, userAgentData: { mobile: true }, hardwareConcurrency: 8, deviceMemory: 4,
    storage: { persisted: async () => true },
    gpu: { requestAdapter: async () => ({ info: { vendor: "qualcomm", architecture: "adreno-7xx", description: "" } }) },
  };
  const d = await detect({ navigator: nav, isolated: true });
  assert.equal(d.cls, "phone");
  assert.equal(d.gpu, "hardware");
  assert.equal(d.persisted, true);
  assert.equal(d.corpus_cache_mb, 256);
  assert.equal(d.cross_origin_isolated, true);
  assert.equal(d.webgpu.adapter.software, false);
  assert.equal(d.python.threads, 1);
  const bare = await detect({ navigator: {} });
  assert.equal(bare.cls, "desktop");
  assert.equal(bare.persisted, null);
  assert.deepEqual(quickFacts({ userAgent: UA.iphone, hardwareConcurrency: 4 }).cores, 4);
});

test("holdWakeLock: takes the screen lock when it can, releases once, never throws", async () => {
  let released = 0;
  const nav = { wakeLock: { request: async (type) => { assert.equal(type, "screen"); return { release: () => { released++; } }; } } };
  const release = await holdWakeLock(nav);
  release();
  release();
  assert.equal(released, 1, "released once, however often it is called");
  const refused = await holdWakeLock({ wakeLock: { request: async () => { throw new Error("NotAllowedError"); } } });
  refused();
  const none = await holdWakeLock({});
  none();
});
