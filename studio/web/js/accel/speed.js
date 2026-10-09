// What the benchmark measured on this device (Settings → Compute → 浏览器算力测试), so that "auto" uses the GPU only
// where it was shown to be faster than the JS workers. A GPU's speed for this kernel cannot be told from its name:
// one invocation per pathway leaves most of a large GPU idle, and a phone GPU may lose to two CPU cores. Kept in
// localStorage per browser profile; unreadable storage (private mode) simply means "not measured".

const KEY = "tcmstudio.accel.speed/1";

function store() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

/** The last measurement: {adapter, gpu_ms, cpu_ms, cpu_engine, workers, pathways, at} or null. */
export function readSpeed() {
  try {
    const raw = store()?.getItem(KEY);
    const v = raw ? JSON.parse(raw) : null;
    return v && Number.isFinite(v.gpu_ms) && Number.isFinite(v.cpu_ms) ? v : null;
  } catch {
    return null;
  }
}

/** Record a benchmark's comparison: both engines ran the same world and gave the same counts. */
export function recordSpeed({ adapter = "", gpu_ms, cpu_ms, cpu_engine = "js-workers", workers = 1, pathways = 0 } = {}) {
  if (!Number.isFinite(gpu_ms) || !Number.isFinite(cpu_ms) || gpu_ms <= 0 || cpu_ms <= 0) return null;
  const entry = { adapter: String(adapter), gpu_ms: Math.round(gpu_ms), cpu_ms: Math.round(cpu_ms), cpu_engine, workers, pathways, at: new Date().toISOString() };
  try {
    store()?.setItem(KEY, JSON.stringify(entry));
  } catch { /* storage refused: auto keeps using the CPU */ }
  return entry;
}

/** true when this device's benchmark showed the GPU faster, false when slower, null when never measured. */
export function gpuMeasuredFaster(speed = readSpeed()) {
  if (!speed) return null;
  return speed.gpu_ms < speed.cpu_ms;
}

export function clearSpeed() {
  try {
    store()?.removeItem(KEY);
  } catch { /* nothing to clear */ }
}
