// How much memory a browser job may count on (studio/docs/BROWSER_JOBS.md). A page cannot ask the system how much
// memory is free, so every figure here is either reported by the browser or an assumption that says it is one:
//
//   navigator.deviceMemory     the device's RAM in GB, rounded down to a power of two and capped at 8 (Chromium only;
//                              absent in Firefox and Safari). 2 GB is assumed when it is missing, and the budget says so.
//   measureUserAgentSpecificMemory()
//                              what this page and its workers hold now, measured by the browser; only on a
//                              cross-origin isolated page, and it may take until the next garbage collection.
//
// Each job worker runs CPython compiled to wasm32: its heap can never exceed 4 GiB, whatever the device has, and it
// never shrinks once grown (a wasm memory only grows), so a worker's heap size is the peak it reached since it started.

/** A wasm32 memory addresses at most 4 GiB: no job worker can hold more, on any device. */
export const WASM32_MAX_BYTES = 4 * 2 ** 30;
/** The device memory assumed when navigator.deviceMemory is not reported. */
export const ASSUMED_DEVICE_GB = 2;
/** The share of the device's memory a job may plan on: the system, the browser and other tabs keep the rest. */
export const PAGE_SHARE = 0.5;

const GIB = 2 ** 30;

/**
 * The memory budget for a job → {device_gb, assumed_gb, measured_bytes, total_bytes, per_worker_bytes, workers,
 * source, note}. deviceMemory: GB as navigator.deviceMemory reports it (missing → ASSUMED_DEVICE_GB, and device_gb is
 * null); measured: what the page already holds (bytes, or measureMemory()'s result), taken off the total; workers: how
 * many job workers share it. per_worker_bytes never exceeds WASM32_MAX_BYTES.
 */
export function memoryBudget({ deviceMemory = globalThis.navigator?.deviceMemory, measured = null, workers = 1 } = {}) {
  const reported = Number(deviceMemory);
  const known = Number.isFinite(reported) && reported > 0;
  const gb = known ? reported : ASSUMED_DEVICE_GB;
  const used = typeof measured === "number" ? measured : measured?.bytes;
  const measuredBytes = Number.isFinite(used) && used >= 0 ? used : null;
  const total = Math.max(0, Math.floor(gb * GIB * PAGE_SHARE) - (measuredBytes ?? 0));
  const n = Math.max(1, Math.floor(Number(workers)) || 1);
  const perWorker = Math.min(WASM32_MAX_BYTES, Math.floor(total / n));
  const notes = [
    known
      ? `the device reports ${gb} GB (navigator.deviceMemory: rounded down, at most 8)`
      : `the device memory is not reported here; ${ASSUMED_DEVICE_GB} GB is assumed`,
    `a job plans on ${Math.round(PAGE_SHARE * 100)}% of it`,
    measuredBytes === null ? "what the page already holds was not measured" : `less the ${(measuredBytes / 1e6).toFixed(0)} MB this page holds now (measured)`,
    `each wasm32 worker can hold at most ${WASM32_MAX_BYTES / GIB} GiB`,
  ];
  return {
    device_gb: known ? gb : null,
    assumed_gb: known ? null : ASSUMED_DEVICE_GB,
    measured_bytes: measuredBytes,
    total_bytes: total,
    per_worker_bytes: perWorker,
    workers: n,
    source: known ? "navigator.deviceMemory" : "assumed",
    note: notes.join("; "),
  };
}

/**
 * What this page (with its workers) holds now, as the browser measures it → {bytes, measured_by, reason}. Only a
 * cross-origin isolated page may ask (performance.measureUserAgentSpecificMemory); elsewhere, or when the browser
 * has no such call, or it does not answer within timeoutMs (it may wait for the next garbage collection), bytes is
 * null and reason says why. Nothing is estimated in its place.
 */
export async function measureMemory({ performance: perf = globalThis.performance, isolated = globalThis.crossOriginIsolated, timeoutMs = 30_000 } = {}) {
  const none = (reason) => ({ bytes: null, measured_by: null, reason });
  if (!isolated) return none("the page is not cross-origin isolated, so the browser does not measure its memory");
  if (typeof perf?.measureUserAgentSpecificMemory !== "function") return none("this browser has no performance.measureUserAgentSpecificMemory");
  let timer = null;
  try {
    const result = await Promise.race([
      perf.measureUserAgentSpecificMemory(),
      new Promise((resolve) => { timer = setTimeout(() => resolve(null), timeoutMs); }),
    ]);
    if (!result) return none(`the browser did not answer within ${Math.round(timeoutMs / 1000)} s`);
    const bytes = Number(result.bytes);
    if (!Number.isFinite(bytes)) return none("the browser's answer had no byte count");
    return { bytes, measured_by: "performance.measureUserAgentSpecificMemory", reason: null };
  } catch (err) {
    return none(`the browser refused to measure: ${err?.name || "Error"}: ${err?.message || err}`);
  } finally {
    clearTimeout(timer);
  }
}
