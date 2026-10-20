// What this device measured about its science kernels, and what that allows. A device check (compute/device-check.js)
// writes a report (schema tcmstudio.device-check/1); the report is this device's calibration, kept in the origin's
// IndexedDB under the device's fingerprint, and the scheduler (compute/science-scheduler.js) and the job receipts
// (scientific/engines/evidence.js) cite it by its id. Nothing here is a cost model: a calibration says what was
// measured, at the sizes it was measured, on the adapter and browser it names, and a calibration of another adapter or
// browser says nothing about this one.
//
// The device fingerprint is what identifies "this device" for a calibration: the adapter as classifyAdapter() saw it
// (vendor, architecture, device, description, whether it is a fallback adapter, its kind) and the browser's brands
// and platform (navigator.userAgentData, else its user-agent string). A browser update or another adapter changes it,
// so the device check runs again before the GPU is chosen there.

import { classifyAdapter } from "../runtime/webgpu.js";

export const DEVICE_CHECK_SCHEMA = "tcmstudio.device-check/1";
export const CALIBRATION_DB = "tcmstudio-compute";
const DB_VERSION = 1;
const STORE = "calibrations";

const hex = (bytes) => Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");

/** SHA-256 of `text` as 64 hex digits. */
export async function sha256Text(text, subtle = globalThis.crypto?.subtle) {
  if (!subtle?.digest) throw new Error("this browser has no SubtleCrypto (a page that is not a secure context?)");
  return hex(await subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

/** JSON with its keys sorted at every level: the bytes a digest of a document is taken over. */
export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().filter((k) => value[k] !== undefined).map((k) => `${JSON.stringify(k)}:${canonicalJson(value[k])}`).join(",")}}`;
  return JSON.stringify(value ?? null);
}

/** What the browser says it is: {brands, platform, mobile, user_agent}. */
export function browserIdentity(nav = globalThis.navigator) {
  const d = nav?.userAgentData;
  let brands = null;
  try { brands = Array.isArray(d?.brands) ? d.brands.map((b) => ({ brand: String(b.brand), version: String(b.version) })) : null; } catch { brands = null; }
  return { brands, platform: d?.platform ?? null, mobile: typeof d?.mobile === "boolean" ? d.mobile : null, user_agent: nav?.userAgent ?? null };
}

/**
 * This device as a calibration names it → {id (SHA-256 of the rest, canonical JSON), adapter: {kind, vendor,
 * architecture, device, description, fallback} | null, browser: browserIdentity()}. `adapter` is classifyAdapter()'s
 * answer (null where there is none: a CPU-only device).
 */
export async function deviceFingerprint({ adapter = null, navigator = globalThis.navigator, subtle = globalThis.crypto?.subtle } = {}) {
  const a = adapter ? {
    kind: adapter.kind ?? null, vendor: adapter.vendor ?? "", architecture: adapter.architecture ?? "", device: adapter.device ?? "",
    description: adapter.description ?? "", fallback: Boolean(adapter.fallback),
  } : null;
  const body = { adapter: a, browser: browserIdentity(navigator) };
  return { id: await sha256Text(canonicalJson(body), subtle), ...body };
}

// ------------------------------------------------------------------------------------------------- IndexedDB

function req(r) {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

/** The calibrations kept in this origin: one per device fingerprint, the latest. */
export class CalibrationStore {
  #db;
  constructor(db) { this.#db = db; db.onversionchange = () => { try { db.close(); } catch { /* closed */ } }; }

  static open({ indexedDB = globalThis.indexedDB, name = CALIBRATION_DB } = {}) {
    return new Promise((resolve, reject) => {
      if (!indexedDB?.open) { reject(new Error("this browser keeps no IndexedDB (a private window?), so no calibration is kept")); return; }
      let r;
      try { r = indexedDB.open(name, DB_VERSION); } catch (err) { reject(err); return; }
      r.onupgradeneeded = () => { r.result.createObjectStore(STORE, { keyPath: "fingerprint" }); };
      r.onsuccess = () => resolve(new CalibrationStore(r.result));
      r.onerror = () => reject(r.error);
      r.onblocked = () => reject(new Error("the calibration database is blocked by another open tab"));
    });
  }

  async #run(mode, body) {
    const tx = this.#db.transaction(STORE, mode);
    const done = mode === "readwrite" ? new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
    }) : null;
    done?.catch(() => {});
    const result = await body(tx.objectStore(STORE));
    if (done) await done;
    return result;
  }

  /** The report kept for this fingerprint id, or null. */
  async get(fingerprint) { return (await this.#run("readonly", (s) => req(s.get(String(fingerprint)))))?.report ?? null; }
  /** Every report kept, newest first. */
  async all() {
    const rows = await this.#run("readonly", (s) => req(s.getAll()));
    return rows.map((r) => r.report).sort((a, b) => String(b?.measured_at).localeCompare(String(a?.measured_at)));
  }
  /** Keep `report` as its device's calibration (replacing the one before). */
  async put(report) {
    if (report?.schema !== DEVICE_CHECK_SCHEMA || !report?.device?.fingerprint?.id) throw new TypeError(`a calibration is a ${DEVICE_CHECK_SCHEMA} report with its device's fingerprint`);
    await this.#run("readwrite", (s) => req(s.put({ fingerprint: report.device.fingerprint.id, report, saved_at: new Date().toISOString() })));
    return report.device.fingerprint.id;
  }
  delete(fingerprint) { return this.#run("readwrite", (s) => req(s.delete(String(fingerprint)))); }
  close() { try { this.#db.close(); } catch { /* closed */ } }
}

/** Keep `report` (a device check) as this device's calibration → the fingerprint id it is kept under. */
export async function saveCalibration(report, opts = {}) {
  const store = await CalibrationStore.open(opts);
  try { return await store.put(report); } finally { store.close(); }
}

/** The calibration kept for `fingerprint` (an id, or deviceFingerprint()'s answer), or null when there is none. */
export async function loadCalibration(fingerprint, opts = {}) {
  const store = await CalibrationStore.open(opts);
  try { return await store.get(typeof fingerprint === "string" ? fingerprint : fingerprint?.id); } finally { store.close(); }
}

/**
 * This device's calibration as a job would cite it: the adapter the browser offers now (asked for, but no device is
 * created), its fingerprint, and the report kept for that fingerprint → {adapter, fingerprint, calibration (null when
 * there is none), reason}. Never throws: what could not be read is the reason.
 */
export async function currentCalibration({ navigator = globalThis.navigator, indexedDB = globalThis.indexedDB, timeoutMs = 5000 } = {}) {
  let adapter = null, reason = null;
  try {
    if (!navigator?.gpu?.requestAdapter) reason = "this browser has no WebGPU API";
    else {
      let timer;
      const found = await Promise.race([navigator.gpu.requestAdapter({ powerPreference: "high-performance" }),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("requestAdapter timed out")), timeoutMs); })]).finally(() => clearTimeout(timer));
      if (!found) reason = "no WebGPU adapter";
      else adapter = classifyAdapter(found.info || (typeof found.requestAdapterInfo === "function" ? await found.requestAdapterInfo().catch(() => null) : null), found);
    }
  } catch (err) { reason = String(err?.message || err); }
  const fingerprint = await deviceFingerprint({ adapter, navigator });
  let calibration = null;
  try { calibration = await loadCalibration(fingerprint.id, { indexedDB }); } catch (err) { reason ??= String(err?.message || err); }
  if (!calibration) reason ??= "no device check has been run on this device";
  return { adapter, fingerprint, calibration, reason };
}

// ------------------------------------------------------------------------------------------------- what it allows

const fmt = (n) => Number(n).toLocaleString("en-US");

/**
 * What `calibration` (a device check report, or null) says about running `op` on the GPU for a call of `pairs`
 * comparisons → {use_gpu, accelerated (true / false / null), size (the calibrated size it rests on, or null), gpu_ms,
 * cpu_ms, cpu_backend, calibration_id, reason}.
 *
 * It rests on the largest calibrated size whose comparisons are no more than the call's (its GPU's fixed costs weigh at
 * most as much there as in the call); a call smaller than every calibrated size has no verdict and stays on the CPU.
 * The GPU is chosen only when, at that size, a physical adapter passed its known-answer test, its answers were exact,
 * and it was measured faster than the CPU kernel. With `fingerprint` (an id), a calibration of another device is not
 * used at all; with `versions` (science-scheduler.js scienceKernelVersions: the kernel, the WebAssembly module's and the
 * shaders' digests), neither is one that measured other kernels than these (a site update changes them).
 */
export function calibrationVerdict(calibration, { op = "tanimoto.best", pairs, fingerprint = null, versions = null } = {}) {
  const out = { use_gpu: false, accelerated: null, size: null, gpu_ms: null, cpu_ms: null, cpu_backend: null, calibration_id: calibration?.id ?? null, reason: "" };
  const no = (reason) => Object.assign(out, { reason });
  if (!calibration) return no("no device check has been run on this device");
  if (calibration.schema !== DEVICE_CHECK_SCHEMA) return no(`the calibration is not a ${DEVICE_CHECK_SCHEMA} report`);
  if (fingerprint && calibration.device?.fingerprint?.id !== fingerprint) return no("the calibration was measured on another adapter or browser");
  if (versions) {
    const m = calibration.versions || {};
    const differ = ["kernel", "wasm_sha256", "wgsl_sha256"].filter((k) => m[k] !== versions[k]);
    if (differ.length) return no(`the device check measured other kernels than these (${differ.join(", ")} differ): it must run again`);
  }
  const adapter = calibration.device?.adapter;
  if (!adapter) return no("the device check found no WebGPU adapter");
  if (adapter.kind !== "physical") return no(`the device check's adapter is ${adapter.kind}: not evidence of a GPU`);
  const kernel = calibration.kernels?.[op];
  if (!kernel) return no(`the device check did not measure ${op}`);
  if (kernel.known_answer?.ok !== true) return no("the adapter did not pass its known-answer test in the device check");
  const sizes = (kernel.sizes || []).filter((s) => Number.isFinite(s?.pairs) && s.pairs <= pairs).sort((a, b) => b.pairs - a.pairs);
  const basis = sizes[0];
  if (!basis) return no(`this call (${fmt(pairs)} comparisons) is smaller than every size the device check measured`);
  Object.assign(out, {
    size: { queries: basis.queries, refs: basis.refs, pairs: basis.pairs }, gpu_ms: basis.timings?.webgpu?.ms ?? null,
    cpu_ms: basis.timings?.[basis.cpu_baseline]?.ms ?? null, cpu_backend: basis.cpu_baseline ?? null, accelerated: basis.accelerated ?? null,
  });
  const at = `at ${fmt(basis.queries)} × ${fmt(basis.refs)} (${fmt(basis.pairs)} comparisons)`;
  if (basis.accelerated !== true) return no(basis.accelerated === false ? `the device check measured the GPU no faster than the CPU kernel ${at}` : `the device check has no verdict ${at}: ${basis.reason || "not measured"}`);
  out.use_gpu = true;
  out.reason = `the device check measured the GPU faster than the CPU kernel ${at}: ${out.gpu_ms} ms against ${out.cpu_ms} ms (${out.cpu_backend})`;
  return out;
}
