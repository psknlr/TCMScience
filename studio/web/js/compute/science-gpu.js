// The WebGPU kernel of the ADMET applicability-domain search: for each query fingerprint, the reference most similar
// to it, as (index, intersection, union), the smallest index on ties: exactly what compute/science-cpu.js and Python's
// bioagent.admet.chem.tanimoto_best return. Integers only; Python divides in float64.
//
// Two passes (WGSL below):
//   partial  one workgroup per 256 references × 16 queries: each invocation holds one reference, the 16 queries sit in
//            workgroup memory, and each query's best key and the smallest index that reaches it are kept with
//            atomicMax and atomicMin → part[query][chunk] = (key, index)
//   reduce   one invocation per query: the best chunk (a strictly greater key wins, chunks in index order, so the
//            smallest index stands), and the exact intersection and union recomputed for its reference
// The key is floor(c · 2^22 / u) by integer long division (0 for an empty union): for 0 ≤ c ≤ u ≤ 2048 it orders every
// two different ratios c/u strictly and gives equal ratios the same key (checked exhaustively in
// web/test/science-gpu.test.mjs), which is why fingerprints longer than 2048 bits are refused here.
//
// Rules, as for the sequence counts (runtime/webgpu.js): the adapter is classified by classifyAdapter and a software
// renderer is refused unless allowSoftware (only tests and the device check's software mode set it); every device runs
// the known-answer test (KNOWN_ANSWER) before any of its answers are used, and a wrong answer sets it aside; every
// asynchronous step has a time limit; every buffer is destroyed. The device asks for the adapter's storage and buffer
// limits, up to a cap, and the work is cut to fit them: pieces of queries and references whose buffers fit one binding,
// and, within a piece, bands of workgroups dispatched one at a time so that no dispatch runs long (the first band is
// small, the next ones are sized from the rate the device showed to take about targetMs; an OS watchdog resets GPUs
// whose work runs for seconds). Whether a result is used is the scheduler's decision (compute/science-scheduler.js),
// after its own check on the CPU.

import { classifyAdapter } from "../runtime/webgpu.js";
import { FP_BITS, FP_WORDS, TANIMOTO_KERNEL, searchShape } from "./science-cpu.js";

const U = { MAP_READ: 1, COPY_SRC: 4, COPY_DST: 8, UNIFORM: 64, STORAGE: 128, QUERY_RESOLVE: 512 };
const FP_VEC4 = FP_WORDS / 4;          // one fingerprint: 16 vec4<u32>
const FP_BYTES = FP_WORDS * 4;
const QB = 16;                          // queries per workgroup
const CHUNK = 256;                      // references per workgroup
const CELL_PAIRS = QB * CHUNK;          // the comparisons of one workgroup
const LIMIT_CAP = 1024 * 1024 * 1024;   // the most asked of the adapter for one buffer and one binding
const PIECE_BYTES = 256 * 1024 * 1024;  // the most one buffer of a call takes, whatever the device allows
const BAND_PAIRS = 1 << 20;             // the first band of a call (256 workgroups); the next are sized from the rate it showed
const TARGET_MS = 250;
const MAX_BAND_GROWTH = 8;              // a band at most this many times the one before it
export const SOFTWARE_REFUSED = "WebGPU adapter is a software CPU renderer";
export const SCIENCE_GPU_LIMITS = Object.freeze(["maxStorageBufferBindingSize", "maxBufferSize"]);

const PARAMS = "struct P { Q: u32, R: u32, chunks: u32, c0: u32, qb0: u32, p0: u32, p1: u32, p2: u32 }";

export const TANIMOTO_PARTIAL = /* wgsl */ `
${PARAMS}
@group(0) @binding(0) var<storage, read> qfp: array<vec4u>;
@group(0) @binding(1) var<storage, read> rfp: array<vec4u>;
@group(0) @binding(2) var<storage, read_write> part: array<vec2u>;
@group(0) @binding(3) var<uniform> p: P;
var<workgroup> qs: array<vec4u, 256>;
var<workgroup> qa: array<u32, 16>;
var<workgroup> wbest: array<atomic<u32>, 16>;
var<workgroup> widx: array<atomic<u32>, 16>;
fn score(c: u32, u: u32) -> u32 {
  if (u == 0u) { return 0u; }
  let hi = (c << 11u) / u;
  let rem = (c << 11u) % u;
  return (hi << 11u) | ((rem << 11u) / u);
}
@compute @workgroup_size(256)
fn partial(@builtin(local_invocation_id) lid: vec3u, @builtin(workgroup_id) wid: vec3u) {
  let t = lid.x;
  let chunk = wid.x + p.c0;
  let qbase = (wid.y + p.qb0) * 16u;
  let qi = qbase + t / 16u;
  var qv = vec4u(0u);
  if (qi < p.Q) { qv = qfp[qi * 16u + t % 16u]; }
  qs[t] = qv;
  if (t < 16u) { atomicStore(&wbest[t], 0u); atomicStore(&widx[t], 0xffffffffu); }
  workgroupBarrier();
  if (t < 16u) {
    var s = vec4u(0u);
    for (var w = 0u; w < 16u; w++) { s += countOneBits(qs[t * 16u + w]); }
    qa[t] = s.x + s.y + s.z + s.w;
  }
  workgroupBarrier();
  let r = chunk * 256u + t;
  let valid = r < p.R;
  var rw: array<vec4u, 16>;
  var b = 0u;
  if (valid) {
    var s = vec4u(0u);
    for (var w = 0u; w < 16u; w++) { let v = rfp[r * 16u + w]; rw[w] = v; s += countOneBits(v); }
    b = s.x + s.y + s.z + s.w;
  }
  var keys: array<u32, 16>;
  for (var q = 0u; q < 16u; q++) {
    var k = 0u;
    if (valid && qbase + q < p.Q) {
      var s = vec4u(0u);
      for (var w = 0u; w < 16u; w++) { s += countOneBits(rw[w] & qs[q * 16u + w]); }
      let c = s.x + s.y + s.z + s.w;
      k = score(c, qa[q] + b - c);
      atomicMax(&wbest[q], k);
    }
    keys[q] = k;
  }
  workgroupBarrier();
  for (var q = 0u; q < 16u; q++) {
    if (valid && qbase + q < p.Q && keys[q] == atomicLoad(&wbest[q])) { atomicMin(&widx[q], r); }
  }
  workgroupBarrier();
  if (t < 16u && qbase + t < p.Q) { part[(qbase + t) * p.chunks + chunk] = vec2u(atomicLoad(&wbest[t]), atomicLoad(&widx[t])); }
}`;

export const TANIMOTO_REDUCE = /* wgsl */ `
${PARAMS}
@group(0) @binding(0) var<storage, read> qfp: array<vec4u>;
@group(0) @binding(1) var<storage, read> rfp: array<vec4u>;
@group(0) @binding(2) var<storage, read> part: array<vec2u>;
@group(0) @binding(3) var<uniform> p: P;
@group(0) @binding(4) var<storage, read_write> best: array<vec4u>;
@compute @workgroup_size(64)
fn reduce(@builtin(global_invocation_id) gid: vec3u) {
  let q = gid.x;
  if (q >= p.Q) { return; }
  var bk = 0u;
  var bi = 0xffffffffu;
  for (var ch = 0u; ch < p.chunks; ch++) {
    let e = part[q * p.chunks + ch];
    if (bi == 0xffffffffu || e.x > bk) { bk = e.x; bi = e.y; }
  }
  var s = vec4u(0u);
  var sa = vec4u(0u);
  var sb = vec4u(0u);
  for (var w = 0u; w < 16u; w++) {
    let x = qfp[q * 16u + w];
    let y = rfp[bi * 16u + w];
    s += countOneBits(x & y); sa += countOneBits(x); sb += countOneBits(y);
  }
  let c = s.x + s.y + s.z + s.w;
  let a = sa.x + sa.y + sa.z + sa.w;
  let b = sb.x + sb.y + sb.z + sb.w;
  best[q] = vec4u(bi, c, a + b - c, bk);
}`;

/** The shader's key in JavaScript: floor(c · 2^22 / u) by the same long division, 0 for an empty union. */
export function tanimotoKey(c, u) {
  if (u === 0) return 0;
  const hi = Math.floor((c << 11) / u), rem = (c << 11) % u;
  return ((hi << 11) | Math.floor((rem << 11) / u)) >>> 0;
}

// ------------------------------------------------------------------------------------------------- known answer

/**
 * The known-answer test's input: 40 queries × 700 references of 2048 bits, from a fixed generator (sparse references,
 * queries copied from references with a few bits flipped), with what the test is for planted in it: an empty and an
 * all-ones query and reference, a query whose union with the empty reference is empty, three equal maxima in two
 * workgroups (10, 11, 650), the first and last reference of a workgroup, and equal ratios with other counts in both
 * orders across workgroups (query 38: 25/50 at 260 before 20/40 at 600; query 39: 20/40 at 100 before 25/50 at 610).
 */
export function knownAnswerInput() {
  const words = FP_WORDS, R = 700, Q = 40;
  let s = 20261019;
  const rand = () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
  const refs = new Uint32Array(R * words), queries = new Uint32Array(Q * words);
  const set = (a, i, b) => { a[i * words + (b >>> 5)] |= 1 << (8 * ((b >>> 3) & 3) + 7 - (b & 7)); };
  const copy = (dst, i, src, j) => dst.set(src.subarray(j * words, (j + 1) * words), i * words);
  for (let i = 0; i < R; i++) for (let k = 0; k < 48; k++) set(refs, i, Math.floor(rand() * 2048));
  copy(refs, 11, refs, 10); copy(refs, 650, refs, 10);
  refs.fill(0, 300 * words, 301 * words);
  refs.fill(0xffffffff, 500 * words, 501 * words);
  const sameRatio = (q, lo, hi, loFirst) => {
    const S = [...new Set(Array.from({ length: 40 }, () => Math.floor(rand() * 2048)))];
    while (S.length < 40) { const b = Math.floor(rand() * 2048); if (!S.includes(b)) S.push(b); }
    const other = [];
    while (other.length < 10) { const b = Math.floor(rand() * 2048); if (!S.includes(b) && !other.includes(b)) other.push(b); }
    queries.fill(0, q * words, (q + 1) * words);
    for (const b of S) set(queries, q, b);
    const twenty = loFirst ? lo : hi, twentyFive = loFirst ? hi : lo;
    refs.fill(0, twenty * words, (twenty + 1) * words);
    for (const b of S.slice(0, 20)) set(refs, twenty, b);
    refs.fill(0, twentyFive * words, (twentyFive + 1) * words);
    for (const b of [...S.slice(0, 25), ...other]) set(refs, twentyFive, b);
  };
  for (let q = 0; q < Q; q++) {
    copy(queries, q, refs, (q * 17 + 3) % R);
    for (let k = 0; k < 4; k++) queries[q * words + Math.floor(rand() * words)] ^= 1 << Math.floor(rand() * 32);
  }
  queries.fill(0, 0, words);
  queries.fill(0xffffffff, words, 2 * words);
  copy(queries, 2, refs, 10);
  copy(queries, 3, refs, 300);
  copy(queries, 20, refs, 699);
  copy(queries, 21, refs, 256);
  copy(queries, 22, refs, 255);
  sameRatio(38, 260, 600, false);
  sameRatio(39, 100, 610, true);
  return { queries, refs, words };
}

// Every answer of the known-answer test, written down (web/test/science-gpu.test.mjs checks them against the CPU
// kernels and a brute force). The test runs in two pieces of references and two of queries, with bands of one
// workgroup, so that the shader's offsets of chunks and of query blocks within a piece (c0, qb0), and the merges across
// bands, chunks and pieces, are what is tested, not only the arithmetic.
export const KNOWN_ANSWER = Object.freeze({
  arg: [0, 500, 10, 0, 71, 88, 105, 122, 139, 156, 173, 190, 207, 224, 241, 258, 275, 292, 309, 326, 699, 256, 255, 394, 411, 428, 445, 462, 479, 496, 513, 530, 547, 564, 581, 598, 615, 632, 260, 100],
  inter: [0, 2048, 47, 0, 48, 47, 48, 47, 48, 45, 45, 48, 47, 48, 47, 46, 48, 48, 48, 48, 48, 48, 47, 47, 48, 46, 47, 48, 47, 48, 48, 48, 48, 47, 48, 47, 48, 48, 25, 20],
  union: [48, 2048, 47, 48, 52, 51, 52, 51, 52, 49, 49, 52, 51, 52, 51, 50, 52, 52, 52, 52, 48, 48, 47, 51, 52, 50, 51, 52, 51, 52, 52, 52, 52, 51, 52, 51, 52, 52, 50, 40],
  plan: Object.freeze({ pieceRefs: 384, pieceQueries: 32, bandCells: 1, adaptive: false }),
});

// ------------------------------------------------------------------------------------------------- the runtime

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const ms3 = (x) => (Number.isFinite(x) ? Math.round(x * 1000) / 1000 : null);
const coded = (message, code) => Object.assign(new Error(message), { code });
const wrongAnswer = (message) => coded(message, "wrong_answer");

function cancelled(check) {
  if (check?.()) throw coded("Tanimoto search cancelled", "cancelled");
}

function limited(promise, timeoutMs, onLate) {
  let timer, expired = false;
  const operation = Promise.resolve(promise).then((result) => { if (expired) onLate?.(result); return result; });
  return Promise.race([operation, new Promise((_, reject) => { timer = setTimeout(() => { expired = true; reject(coded("WebGPU operation timed out", "timeout")); }, timeoutMs); })]).finally(() => clearTimeout(timer));
}

/** `count` fingerprints from `first`, as the shader reads them: 64 words each (narrower ones zero-padded). */
function piece(src, first, count, words) {
  if (words === FP_WORDS) return src.subarray(first * words, (first + count) * words);
  const out = new Uint32Array(count * FP_WORDS);
  for (let i = 0; i < count; i++) out.set(src.subarray((first + i) * words, (first + i + 1) * words), i * FP_WORDS);
  return out;
}

export class ScienceGPU {
  #nav; #timeout; #allowSoftware; #timestamps; #targetMs; #bandPairs; #limitCap; #pieceBytes;
  #device = null; #deviceP = null; #pipelines = null;
  #adapter = null;       // classifyAdapter() of the adapter last requested
  #requested = null;     // the limits asked of it
  #knownAnswer = null;   // {ok, ms, reason?} of the current device's known-answer test; null until it answered
  #knownDevice = null;   // the device that result belongs to
  #setAside = null;      // why this adapter's answers are not used any more (a wrong answer after its test passed):
                         // for the session, so a new device after a loss does not clear it; stop() does
  #last = null;          // what the last tanimotoBest() did (lastRun)

  /**
   * navigator: whose gpu is used; timeoutMs: the limit of every asynchronous step (one band, one readback);
   * allowSoftware: compute on a software renderer (tests and the device check's software mode only); timestamps: ask
   * for timestamp queries, so lastRun.kernel_ms is the GPU's own time; targetMs: how long one dispatch should take;
   * bandPairs: the comparisons of a call's first band; limitCap: the most asked of the adapter per buffer and binding;
   * pieceBytes: the most one buffer of a call takes.
   */
  constructor({ navigator = globalThis.navigator, timeoutMs = 10_000, allowSoftware = false, timestamps = false, targetMs = TARGET_MS,
    bandPairs = BAND_PAIRS, limitCap = LIMIT_CAP, pieceBytes = PIECE_BYTES } = {}) {
    this.#nav = navigator; this.#timeout = timeoutMs; this.#allowSoftware = allowSoftware; this.#timestamps = timestamps;
    this.#targetMs = targetMs; this.#bandPairs = bandPairs; this.#limitCap = limitCap; this.#pieceBytes = pieceBytes;
  }
  get allowsSoftware() { return this.#allowSoftware; }
  /** The adapter as classifyAdapter() saw it, once one was requested; null before. */
  get adapter() { return this.#adapter ? { ...this.#adapter } : null; }
  /** {requested, granted} storage and buffer limits of the current device; null without one. */
  get limits() {
    const d = this.#device;
    if (!d) return null;
    const granted = {};
    for (const k of [...SCIENCE_GPU_LIMITS, "maxComputeWorkgroupsPerDimension"]) granted[k] = d.limits?.[k] ?? null;
    return { requested: { ...this.#requested }, granted };
  }
  /** The current device's known-answer test: {ok, ms, reason?}, or null when it has not run to an answer on it. */
  get knownAnswer() { return this.#knownAnswer && this.#knownDevice && this.#knownDevice === this.#device ? { ...this.#knownAnswer } : null; }
  /** Why this adapter's answers are not used any more in this session (until stop()), or null. */
  get setAside() { return this.#setAside; }
  /**
   * What the last tanimotoBest() did, or null when it did not reach the GPU: {dispatched, cold, setup_ms, upload_ms,
   * dispatch_ms (the bands, waited for one by one), readback_ms, work_ms (everything after set-up), kernel_ms (GPU
   * timestamps; null unless asked for and supported), kernel_note?, bands, max_band_ms, pieces, pairs}.
   */
  get lastRun() { return this.#last ? { ...this.#last } : null; }

  async #getDevice() {
    if (this.#device) return this.#device;
    if (!this.#deviceP) {
      this.#deviceP = (async () => {
        if (!this.#nav?.gpu?.requestAdapter) throw coded("WebGPU API unavailable", "no_api");
        const adapter = await limited(this.#nav.gpu.requestAdapter({ powerPreference: "high-performance" }), this.#timeout);
        if (!adapter) throw coded("No WebGPU adapter available", "no_adapter");
        let info = adapter.info;
        if (!info && typeof adapter.requestAdapterInfo === "function") info = await limited(adapter.requestAdapterInfo(), this.#timeout).catch(() => null);
        this.#adapter = classifyAdapter(info, adapter);
        if (!this.#allowSoftware && this.#adapter.kind === "software") throw coded(SOFTWARE_REFUSED, "software_adapter");
        // the adapter's own storage and buffer limits (the defaults are 128 and 256 MiB), up to the cap
        const requiredLimits = {};
        for (const k of SCIENCE_GPU_LIMITS) {
          const v = Number(adapter.limits?.[k]);
          if (Number.isFinite(v) && v > 0) requiredLimits[k] = Math.min(v, this.#limitCap);
        }
        this.#requested = requiredLimits;
        let timestamps = false;
        try { timestamps = this.#timestamps && Boolean(adapter.features?.has?.("timestamp-query")); } catch { timestamps = false; }
        const descriptor = { requiredLimits, ...(timestamps ? { requiredFeatures: ["timestamp-query"] } : {}) };
        const device = await limited(adapter.requestDevice(descriptor), this.#timeout, (late) => late?.destroy());
        this.#device = device;
        device.lost.then(() => { if (this.#device === device) { this.#device = null; this.#pipelines = null; } });
        return device;
      })().finally(() => { this.#deviceP = null; });
    }
    return this.#deviceP;
  }

  async #compile(device) {
    let scoped = false;
    try {
      device.pushErrorScope("validation"); scoped = true;
      const make = (code, entryPoint, label) => limited(device.createComputePipelineAsync({
        layout: "auto", compute: { module: device.createShaderModule({ code, label }), entryPoint },
      }), this.#timeout);
      const partial = await make(TANIMOTO_PARTIAL, "partial", "TCMScience Tanimoto best: partial");
      const reduce = await make(TANIMOTO_REDUCE, "reduce", "TCMScience Tanimoto best: reduce");
      const validation = await device.popErrorScope(); scoped = false;
      if (validation) throw new Error(validation.message);
      if (this.#device !== device) throw coded("WebGPU device was lost during shader compilation", "device_lost");
      return { partial, reduce };
    } finally {
      if (scoped) await device.popErrorScope().catch(() => {});
    }
  }

  /**
   * The device, its pipelines and its known-answer test, as a call needs them before its own work → {adapter, limits,
   * known_answer, cold}. Throws (with a code) when there is no device to use: no_api, no_adapter, software_adapter,
   * known_answer_failed, set_aside, timeout, device_lost, cancelled.
   */
  async ready({ isCancelled } = {}) {
    const cold = !this.#device || !this.#pipelines || this.#knownDevice !== this.#device || !this.#knownAnswer;
    const device = await this.#getDevice();
    cancelled(isCancelled);
    if (!this.#pipelines) this.#pipelines = await this.#compile(device);
    if (this.#knownDevice !== device || !this.#knownAnswer) {
      const t0 = now();
      let result;
      try {
        const { queries, refs, words } = knownAnswerInput();
        const got = await this.#search(device, queries, refs, words, { isCancelled, last: null, ...KNOWN_ANSWER.plan });
        for (const k of ["arg", "inter", "union"]) {
          const at = KNOWN_ANSWER[k].findIndex((v, i) => got[k][i] !== v);
          if (at >= 0) throw wrongAnswer(`query ${at}: ${k} is ${got[k][at]}, not ${KNOWN_ANSWER[k][at]}`);
        }
        result = { ok: true, ms: ms3(now() - t0) };
      } catch (error) {
        if (error?.code === "cancelled" || isCancelled?.()) throw error;
        // only a wrong answer is a verdict on the device; a test that could not run keeps none, and the next call
        // runs it again
        if (error?.code !== "wrong_answer") {
          const lost = this.#device !== device ? " (the device was lost)" : "";
          throw coded(`WebGPU known-answer test could not run${lost}: ${String(error?.message || error).slice(0, 300)}`, error?.code || "known_answer_unrun");
        }
        result = { ok: false, ms: ms3(now() - t0), reason: String(error?.message || error).slice(0, 300) };
      }
      if (this.#device !== device) throw coded("WebGPU device was lost during its known-answer test", "device_lost");
      this.#knownAnswer = result; this.#knownDevice = device;
    }
    if (!this.#knownAnswer.ok) throw coded(`WebGPU known-answer test failed on this device: ${this.#knownAnswer.reason}`, "known_answer_failed");
    if (this.#setAside) throw coded(`this WebGPU device was set aside: ${this.#setAside}`, "set_aside");
    return { adapter: this.adapter, limits: this.limits, known_answer: this.knownAnswer, cold };
  }

  /** The scheduler's verdict: a result of this device failed its check on the CPU, so no later one is used. */
  setAsideFor(reason) { this.#setAside = String(reason || "a result failed its check").slice(0, 300); }

  /**
   * For each query, the most similar reference → {arg, inter, union}, as tanimotoBestJS returns them. Throws with a
   * code when the device cannot answer (see ready(); also too_large, wrong_answer, timeout, device_lost, cancelled);
   * lastRun says what it did.
   */
  async tanimotoBest(queries, refs, { words = FP_WORDS, isCancelled } = {}) {
    this.#last = null;
    const { nq, nr } = searchShape(queries, refs, { words });
    if (words > FP_WORDS) throw new RangeError(`the WebGPU kernel's score key is exact only up to ${FP_BITS} bits (${FP_WORDS} words), not ${words * 32}`);
    cancelled(isCancelled);
    // no queries: nothing to compute, and no device is asked for
    if (!nq) return { arg: new Uint32Array(0), inter: new Uint32Array(0), union: new Uint32Array(0) };
    const last = { kernel: TANIMOTO_KERNEL, dispatched: false, cold: false, setup_ms: null, upload_ms: 0, dispatch_ms: 0, readback_ms: 0, work_ms: null,
      kernel_ms: null, bands: 0, max_band_ms: null, pieces: 0, pairs: nq * nr };
    this.#last = last;
    const t0 = now();
    const ready = await this.ready({ isCancelled });
    last.cold = ready.cold;
    last.setup_ms = ms3(now() - t0);
    const device = this.#device;
    if (!device || !this.#pipelines) throw coded("WebGPU device was lost after its known-answer test", "device_lost");
    const t1 = now();
    const out = await this.#search(device, queries, refs, words, { isCancelled, last, bandCells: Math.max(1, Math.floor(this.#bandPairs / CELL_PAIRS)), adaptive: true });
    last.work_ms = ms3(now() - t1);
    for (const k of ["upload_ms", "dispatch_ms", "readback_ms"]) last[k] = ms3(last[k]);
    return out;
  }

  /** The search on `device`, in pieces and bands; `last` (null for the known-answer test) receives its marks. */
  async #search(device, queries, refs, words, { isCancelled, last, bandCells, adaptive, pieceRefs = Infinity, pieceQueries = Infinity }) {
    const { nq, nr } = searchShape(queries, refs, { words });
    const lim = device.limits;
    const binding = Math.min(lim.maxStorageBufferBindingSize, lim.maxBufferSize, this.#pieceBytes);
    const groups = lim.maxComputeWorkgroupsPerDimension;
    // a piece of references fits one binding; a piece of queries too, with its partial results (one per chunk)
    const rp = Math.min(nr, Math.floor(binding / FP_BYTES), groups * CHUNK, pieceRefs);
    const chunksMax = Math.ceil(rp / CHUNK);
    const qp = Math.min(nq, Math.floor(binding / FP_BYTES), Math.floor(binding / (8 * chunksMax)), groups * QB, groups * 64, pieceQueries);
    if (rp < 1 || qp < 1) throw coded("Workload exceeds this GPU's buffer/dispatch limits", "too_large");
    const buffers = [];
    const allocate = (size, usage) => { const b = device.createBuffer({ size, usage }); buffers.push(b); return b; };
    let timed = false;
    try { timed = Boolean(last) && this.#timestamps && Boolean(device.features?.has?.("timestamp-query")); } catch { timed = false; }
    if (last && this.#timestamps && !timed) last.kernel_note = "this device was not given timestamp queries (unsupported or not offered)";
    let scopes = 0, querySet = null;
    const arg = new Uint32Array(nq), inter = new Uint32Array(nq), union = new Uint32Array(nq);
    try {
      device.pushErrorScope("out-of-memory"); scopes++;
      device.pushErrorScope("validation"); scopes++;
      const qbuf = allocate(qp * FP_BYTES, U.STORAGE | U.COPY_DST);
      const rbuf = allocate(rp * FP_BYTES, U.STORAGE | U.COPY_DST);
      const part = allocate(qp * chunksMax * 8, U.STORAGE);
      const best = allocate(qp * 16, U.STORAGE | U.COPY_SRC);
      const read = allocate(qp * 16, U.COPY_DST | U.MAP_READ);
      const params = allocate(32, U.UNIFORM | U.COPY_DST);
      let resolved = null, stamps = null;
      if (timed) {
        querySet = device.createQuerySet({ type: "timestamp", count: 2 });
        resolved = allocate(16, U.QUERY_RESOLVE | U.COPY_SRC);
        stamps = allocate(16, U.COPY_DST | U.MAP_READ);
      }
      if (!this.#pipelines || this.#device !== device) throw coded("WebGPU device was lost", "device_lost");
      const { partial, reduce } = this.#pipelines;
      /** The errors of what was submitted so far, as an error (a validation error is this code's, not a wrong answer). */
      const scoped = async () => {
        const validation = await device.popErrorScope(); scopes--;
        const memory = await device.popErrorScope(); scopes--;
        if (validation || memory) throw coded((validation || memory).message, validation ? "validation" : "out_of_memory");
        device.pushErrorScope("out-of-memory"); scopes++;
        device.pushErrorScope("validation"); scopes++;
      };
      const entries = [{ binding: 0, resource: { buffer: qbuf } }, { binding: 1, resource: { buffer: rbuf } }, { binding: 2, resource: { buffer: part } }, { binding: 3, resource: { buffer: params } }];
      const gPartial = device.createBindGroup({ layout: partial.getBindGroupLayout(0), entries });
      const gReduce = device.createBindGroup({ layout: reduce.getBindGroupLayout(0), entries: [...entries, { binding: 4, resource: { buffer: best } }] });
      let kernelNs = 0, stampsAdvanced = true;
      const mark = (k, t) => { if (last) last[k] += now() - t; };
      /** One pass of `pipeline` over (x, y) workgroups, submitted and waited for → its wall time in ms. */
      const pass = async (pipeline, group, x, y, extra) => {
        const encoder = device.createCommandEncoder();
        const p = encoder.beginComputePass(timed ? { timestampWrites: { querySet, beginningOfPassWriteIndex: 0, endOfPassWriteIndex: 1 } } : undefined);
        p.setPipeline(pipeline); p.setBindGroup(0, group); p.dispatchWorkgroups(x, y); p.end();
        if (timed) { encoder.resolveQuerySet(querySet, 0, 2, resolved, 0); encoder.copyBufferToBuffer(resolved, 0, stamps, 0, 16); }
        extra?.(encoder);
        const t = now();
        device.queue.submit([encoder.finish()]);
        if (last) last.dispatched = true;
        if (timed) {
          await this.#wait(stamps.mapAsync(U.MAP_READ), device, isCancelled);
          const [begin, end] = new BigUint64Array(stamps.getMappedRange().slice(0));
          stamps.unmap();
          if (end > begin) kernelNs += Number(end - begin); else stampsAdvanced = false;
        } else await this.#wait(device.queue.onSubmittedWorkDone(), device, isCancelled);
        const ms = now() - t;
        if (last) { last.dispatch_ms += ms; last.max_band_ms = Math.max(last.max_band_ms ?? 0, ms); }
        return ms;
      };
      const onePiece = rp >= nr;
      let cells = bandCells;
      for (let q0 = 0; q0 < nq; q0 += qp) {
        const nqp = Math.min(qp, nq - q0);
        let t = now();
        device.queue.writeBuffer(qbuf, 0, piece(queries, q0, nqp, words));
        mark("upload_ms", t);
        const bi = new Int32Array(nqp).fill(-1), bc = new Uint32Array(nqp), bu = new Uint32Array(nqp);
        for (let r0 = 0; r0 < nr; r0 += rp) {
          cancelled(isCancelled);
          const nrp = Math.min(rp, nr - r0);
          if (!onePiece || q0 === 0) { t = now(); device.queue.writeBuffer(rbuf, 0, piece(refs, r0, nrp, words)); mark("upload_ms", t); }
          if (last) last.pieces++;
          const chunks = Math.ceil(nrp / CHUNK), qBlocks = Math.ceil(nqp / QB);
          for (let qb = 0; qb < qBlocks;) {
            const bandQ = Math.min(qBlocks - qb, Math.max(1, cells), groups);
            for (let c0 = 0; c0 < chunks;) {
              cancelled(isCancelled);
              const bandC = Math.min(chunks - c0, Math.max(1, Math.floor(cells / bandQ)), groups);
              device.queue.writeBuffer(params, 0, Uint32Array.of(nqp, nrp, chunks, c0, qb, 0, 0, 0));
              const ms = await pass(partial, gPartial, bandC, bandQ);
              if (last) last.bands++;
              if (adaptive) {
                const rate = (bandC * bandQ) / Math.max(ms, 0.05);
                cells = Math.max(1, Math.min(Math.floor(rate * this.#targetMs), cells * MAX_BAND_GROWTH, groups * groups));
              }
              c0 += bandC;
            }
            qb += bandQ;
          }
          device.queue.writeBuffer(params, 0, Uint32Array.of(nqp, nrp, chunks, 0, 0, 0, 0, 0));
          await pass(reduce, gReduce, Math.ceil(nqp / 64), 1, (encoder) => encoder.copyBufferToBuffer(best, 0, read, 0, nqp * 16));
          t = now();
          await this.#wait(read.mapAsync(U.MAP_READ), device, isCancelled);
          const got = new Uint32Array(read.getMappedRange(0, nqp * 16).slice(0));
          read.unmap();
          mark("readback_ms", t);
          await scoped();
          // the pieces of references merge as one search: a later piece wins only with a strictly greater similarity
          for (let i = 0; i < nqp; i++) {
            const idx = got[4 * i], c = got[4 * i + 1], u = got[4 * i + 2], key = got[4 * i + 3];
            if (idx >= nrp || c > u || key !== tanimotoKey(c, u)) throw wrongAnswer(`GPU answer for query ${q0 + i} is not one a reference can give (index ${idx} of ${nrp}, ${c}/${u}, key ${key})`);
            const ce = u ? c : 0, ue = u ? u : 1, bce = bu[i] ? bc[i] : 0, bue = bu[i] ? bu[i] : 1;
            if (bi[i] < 0 || ce * bue > bce * ue) { bi[i] = r0 + idx; bc[i] = c; bu[i] = u; }
          }
        }
        arg.set(bi, q0); inter.set(bc, q0); union.set(bu, q0);
      }
      // every piece's errors were read after its readback (scoped); the scopes left open are closed in finally
      if (last && timed) {
        if (stampsAdvanced) last.kernel_ms = ms3(kernelNs / 1e6);
        else last.kernel_note = "the GPU timestamps did not advance (browsers quantize them)";
      }
      if (last) last.max_band_ms = ms3(last.max_band_ms);
      return { arg, inter, union };
    } finally {
      for (; scopes > 0; scopes--) await device.popErrorScope().catch(() => {});
      try { querySet?.destroy?.(); } catch { /* already gone with its device */ }
      for (const buffer of buffers) buffer.destroy();
    }
  }

  /** `promise` (a mapping, or the queue's work done), bounded by the time limit, a cancel and the device's loss. */
  async #wait(promise, device, check) {
    let timer;
    const abort = new Promise((_, reject) => {
      timer = setInterval(() => { try { cancelled(check); } catch (e) { reject(e); } }, 16);
    });
    try {
      await limited(Promise.race([promise, abort, device.lost.then((info) => { throw coded(`WebGPU device lost: ${info?.message || info?.reason}`, "device_lost"); })]), this.#timeout);
    } finally { clearInterval(timer); }
  }

  stop() {
    this.#device?.destroy();
    this.#device = null; this.#pipelines = null; this.#knownAnswer = null; this.#knownDevice = null; this.#setAside = null;
  }
}
