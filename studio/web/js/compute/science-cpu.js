// The CPU kernels of the ADMET applicability-domain search: for each query fingerprint, the reference fingerprint most
// similar to it (Tanimoto), as three integers, (index, intersection, union) of their bits, with the smallest index on
// ties. They return exactly what Python's bioagent.admet.chem.tanimoto_best returns (argmax as np.argmax gives it,
// over similarities that are ratios of these integers), and Python divides in float64. The JavaScript kernel is the
// reference; the WebAssembly SIMD kernel (wasm/science.wat, i8x16.popcnt) must equal it query for query, and is the CPU
// baseline a GPU is measured against (compute/science-gpu.js, compute/science-scheduler.js).
//
// The input format: a set of n fingerprints of `words` 32-bit words each is one Uint32Array of n × words. Fingerprint
// i is the i-th row of Python's np.packbits(bits, axis=1) (most significant bit first: bit b of the fingerprint is bit
// 7 - b % 8 of byte b / 8), and its word k holds bytes 4k … 4k + 3 of that row little-endian: byte 4k in bits 0-7,
// byte 4k + 3 in bits 24-31. On a little-endian machine (every platform the site runs on) that is the row's bytes
// viewed as a Uint32Array, which packedWords() does without copying; bit b of the fingerprint is then bit
// 8 * ((b >> 3) & 3) + 7 - (b & 7) of word b >> 5 (wordsFromBits). Popcounts and AND do not depend on the order, the
// bits' positions only on this definition: web/test/fixtures/science_tanimoto.json, written with numpy, holds JS and
// Python to the same bytes.
//
// Conventions shared with Python: no queries give empty answers (whatever the references); queries with no references
// are refused ("there are no reference fingerprints to compare with"); an empty union (an empty query against an empty
// reference) has similarity 0, so an empty query's answer is reference 0, the smallest index, intersection 0 and union
// that reference's popcount.

import { SCIENCE_WASM } from "./wasm/science.wasm.js";

export const FP_BITS = 2048;
export const FP_WORDS = FP_BITS / 32;
/** The kernel's name and version, as facts and the device check record it. */
export const TANIMOTO_KERNEL = "tanimoto.best/1";

const FP_BYTES = FP_WORDS * 4;        // one fingerprint in the WebAssembly kernel's memory
const MAX_WORDS = 1 << 16;            // c * u stays an exact double far beyond this
const PAGE = 65536;
const MEMORY_BYTES = 64 * 1024 * 1024; // the WebAssembly kernel's working set: blocks of references and of queries
const KEEP_BYTES = 16 * 1024 * 1024;   // a memory grown beyond this is let go after the call (it cannot shrink)
const QUERY_BLOCK = 1024;
const CHECK_PAIRS = 1 << 22;           // pairs compared between two looks at isCancelled
const SAMPLE_SEED = 20261019;
const LITTLE = new Uint8Array(new Uint32Array([1]).buffer)[0] === 1;

const now = () => (globalThis.performance?.now ? globalThis.performance.now() : Date.now());
const ms3 = (x) => Math.round(x * 1000) / 1000;
const cancelled = () => Object.assign(new Error("cancelled"), { code: "cancelled" });

// ------------------------------------------------------------------------------------------------- the format

function checkWords(words) {
  if (!Number.isSafeInteger(words) || words < 1 || words > MAX_WORDS) throw new RangeError(`a fingerprint is 1 to ${MAX_WORDS} words, not ${words}`);
}

/**
 * np.packbits rows (a Uint8Array or ArrayBuffer of n × words × 4 bytes, as bits.u8 and the domain files hold them) →
 * the Uint32Array the kernels take: a view of the same memory when it is aligned on a little-endian machine, else a copy.
 */
export function packedWords(bytes, { words = FP_WORDS } = {}) {
  checkWords(words);
  const b = bytes instanceof ArrayBuffer ? new Uint8Array(bytes) : bytes;
  if (!(b instanceof Uint8Array)) throw new TypeError("packed fingerprints are bytes: a Uint8Array of np.packbits rows");
  if (b.byteLength % (words * 4)) throw new RangeError(`${b.byteLength} bytes are not whole fingerprints of ${words * 4} bytes`);
  if (LITTLE && b.byteOffset % 4 === 0) return new Uint32Array(b.buffer, b.byteOffset, b.byteLength / 4);
  const out = new Uint32Array(b.byteLength / 4);
  const view = new DataView(b.buffer, b.byteOffset, b.byteLength);
  for (let k = 0; k < out.length; k++) out[k] = view.getUint32(4 * k, true);
  return out;
}

/**
 * packedWords undone: the np.packbits rows of packed words, as Uint8Array bytes (what bits.u8 holds, and what a digest
 * of the fingerprints, such as similarity.json's refs_sha256, is taken over): a view on a little-endian machine.
 */
export function packedBytes(packed) {
  if (!(packed instanceof Uint32Array)) throw new TypeError("packed words are a Uint32Array");
  if (LITTLE) return new Uint8Array(packed.buffer, packed.byteOffset, packed.byteLength);
  const out = new Uint8Array(packed.byteLength);
  const view = new DataView(out.buffer);
  for (let k = 0; k < packed.length; k++) view.setUint32(4 * k, packed[k], true);
  return out;
}

/** Fingerprints given as lists of the bit positions set in each (0 … words × 32 − 1) → their packed words. */
export function wordsFromBits(fingerprints, { words = FP_WORDS } = {}) {
  checkWords(words);
  const list = [...fingerprints];
  const out = new Uint32Array(list.length * words);
  list.forEach((bits, i) => {
    for (const b of bits) {
      if (!Number.isSafeInteger(b) || b < 0 || b >= words * 32) throw new RangeError(`bit ${b} is not one of a ${words * 32}-bit fingerprint's`);
      out[i * words + (b >>> 5)] |= 1 << (8 * ((b >>> 3) & 3) + 7 - (b & 7));
    }
  });
  return out;
}

/** The bit positions set in fingerprint `index` of `packed`, in increasing order (wordsFromBits undone). */
export function fingerprintBits(packed, index, { words = FP_WORDS } = {}) {
  checkWords(words);
  const out = [];
  for (let byte = 0; byte < words * 4; byte++) {
    const v = (packed[index * words + (byte >>> 2)] >>> (8 * (byte & 3))) & 255;
    for (let k = 0; k < 8; k++) if (v & (0x80 >>> k)) out.push(byte * 8 + k);
  }
  return out;
}

function pop32(x) {
  x -= (x >>> 1) & 0x55555555;
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  x = (x + (x >>> 4)) & 0x0f0f0f0f;
  return Math.imul(x, 0x01010101) >>> 24;
}

/** The number of bits set in each fingerprint of `packed`. */
export function popcounts(packed, { words = FP_WORDS } = {}) {
  checkWords(words);
  const n = Math.floor(packed.length / words);
  const out = new Uint32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let k = i * words, e = k + words; k < e; k++) s += pop32(packed[k]);
    out[i] = s;
  }
  return out;
}

/** {nq, nr} of a search, or a TypeError / RangeError that says what is wrong with its input. */
export function searchShape(queries, refs, { words = FP_WORDS } = {}) {
  checkWords(words);
  for (const [name, a] of [["queries", queries], ["refs", refs]]) {
    if (!(a instanceof Uint32Array)) throw new TypeError(`${name} must be a Uint32Array of packed words (packedWords)`);
    if (a.length % words) throw new RangeError(`${name} hold ${a.length} words, not whole fingerprints of ${words}`);
  }
  const nq = queries.length / words, nr = refs.length / words;
  if (nq && !nr) throw new RangeError("there are no reference fingerprints to compare with");
  if (nr > 0xffffffff) throw new RangeError("more references than a uint32 index can name");
  return { nq, nr };
}

function emptyResult(n) {
  return { arg: new Uint32Array(n), inter: new Uint32Array(n), union: new Uint32Array(n) };
}

// ------------------------------------------------------------------------------------------------- JavaScript

/**
 * The reference kernel: for each query, the most similar reference → {arg, inter, union} (Uint32Arrays of the queries'
 * count). Similarities are compared exactly, c1 × u2 > c2 × u1 with an empty union as 0 / 1, and only a strictly
 * greater one replaces the best so far, so the smallest index stands on ties.
 */
export function tanimotoBestJS(queries, refs, { words = FP_WORDS, isCancelled } = {}) {
  const { nq, nr } = searchShape(queries, refs, { words });
  const out = emptyResult(nq);
  if (!nq) return out;
  const rp = popcounts(refs, { words });
  for (let i = 0; i < nq; i++) {
    if (isCancelled?.()) throw cancelled();
    const q0 = i * words;
    let a = 0;
    for (let k = 0; k < words; k++) a += pop32(queries[q0 + k]);
    let bi = -1, bc = 0, bu = 0, bce = 0, bue = 1;
    for (let r = 0; r < nr; r++) {
      const r0 = r * words;
      let c = 0;
      for (let k = 0; k < words; k++) c += pop32(queries[q0 + k] & refs[r0 + k]);
      const u = a + rp[r] - c;
      const ce = u ? c : 0, ue = u ? u : 1;
      if (bi < 0 || ce * bue > bce * ue) { bi = r; bc = c; bu = u; bce = ce; bue = ue; }
    }
    out.arg[i] = bi; out.inter[i] = bc; out.union[i] = bu;
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- WebAssembly SIMD

let instance = null;
let unavailable = null;

function decode(b64) {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

function load() {
  if (instance || unavailable) return instance;
  try {
    const bytes = decode(SCIENCE_WASM);
    if (!WebAssembly.validate(bytes)) throw new Error("this engine has no WebAssembly SIMD");
    instance = new WebAssembly.Instance(new WebAssembly.Module(bytes), {});
  } catch (err) {
    unavailable = String(err?.message || err);
    instance = null;
  }
  return instance;
}

/** Whether the WebAssembly SIMD Tanimoto kernel runs here (and why not). */
export function scienceSimdStatus() {
  return load() ? { available: true } : { available: false, reason: unavailable };
}

/** `count` fingerprints of `src` from `first` into the kernel's 256-byte slots at `at` (narrower ones zero-padded). */
function put(buffer, at, src, first, count, words) {
  const dst = new Uint32Array(buffer, at, count * FP_WORDS);
  if (words === FP_WORDS) { dst.set(src.subarray(first * words, (first + count) * words)); return; }
  dst.fill(0);
  for (let i = 0; i < count; i++) dst.set(src.subarray((first + i) * words, (first + i + 1) * words), i * FP_WORDS);
}

/**
 * The kernel of wasm/science.wat: what tanimotoBestJS returns, or null where the engine has no SIMD. Fingerprints of
 * up to 2048 bits (64 words; narrower ones are zero-padded, which changes no count); the references are searched in
 * blocks that, with a block of queries, fit `memoryBytes`.
 */
export function tanimotoBestWasm(queries, refs, { words = FP_WORDS, isCancelled, memoryBytes = MEMORY_BYTES } = {}) {
  const { nq, nr } = searchShape(queries, refs, { words });
  if (words > FP_WORDS) throw new RangeError(`the WebAssembly kernel takes fingerprints of up to ${FP_BITS} bits (${FP_WORDS} words), not ${words * 32}`);
  if (!load()) return null;
  const out = emptyResult(nq);
  if (!nq) return out;
  try {
    searchWith(instance.exports, queries, refs, words, nq, nr, out, isCancelled, memoryBytes);
    return out;
  } finally {
    if (instance && instance.exports.memory.buffer.byteLength > KEEP_BYTES) instance = null;
  }
}

function searchWith(x, queries, refs, words, nq, nr, out, isCancelled, memoryBytes) {
  // the memory: a block of references (fingerprints, then popcounts), then a block of queries (fingerprints,
  // popcounts, and the best answer so far: index, intersection, union)
  const qBlock = Math.min(nq, QUERY_BLOCK);
  const rBlock = Math.min(nr, Math.floor((memoryBytes - qBlock * (FP_BYTES + 16)) / (FP_BYTES + 4)));
  if (rBlock < 1) throw new RangeError(`${memoryBytes} bytes hold no block of references for the WebAssembly kernel`);
  const RC = rBlock * FP_BYTES, Q = RC + rBlock * 4, QC = Q + qBlock * FP_BYTES, BEST = QC + qBlock * 4;
  const total = BEST + qBlock * 12;
  const mem = x.memory;
  if (mem.buffer.byteLength < total) {
    try { mem.grow(Math.ceil((total - mem.buffer.byteLength) / PAGE)); } catch (err) {
      throw new RangeError(`the WebAssembly kernel could not have ${total} bytes of memory: ${err?.message || err}`);
    }
  }
  const buffer = mem.buffer;
  const best = new Int32Array(buffer, BEST, qBlock * 3);
  const oneBlock = rBlock >= nr;
  if (oneBlock) { put(buffer, 0, refs, 0, nr, words); x.popcounts(0, nr, RC); }
  for (let q0 = 0; q0 < nq; q0 += qBlock) {
    const n = Math.min(qBlock, nq - q0);
    put(buffer, Q, queries, q0, n, words);
    x.popcounts(Q, n, QC);
    for (let i = 0; i < n; i++) { best[3 * i] = -1; best[3 * i + 1] = 0; best[3 * i + 2] = 0; }
    for (let r0 = 0; r0 < nr; r0 += rBlock) {
      const m = Math.min(rBlock, nr - r0);
      if (!oneBlock) { put(buffer, 0, refs, r0, m, words); x.popcounts(0, m, RC); }
      // in slices of queries, so that a cancel is seen within about CHECK_PAIRS comparisons
      const step = Math.max(1, Math.floor(CHECK_PAIRS / m));
      for (let s = 0; s < n; s += step) {
        if (isCancelled?.()) throw cancelled();
        x.tanimoto_best(Q + s * FP_BYTES, QC + s * 4, Math.min(step, n - s), 0, RC, m, r0, BEST + s * 12);
      }
    }
    for (let i = 0; i < n; i++) {
      out.arg[q0 + i] = best[3 * i]; out.inter[q0 + i] = best[3 * i + 1]; out.union[q0 + i] = best[3 * i + 2];
    }
  }
}

// ------------------------------------------------------------------------------------------------- results

/** The float64 similarities Python computes from a result (inter / union; 0 where the union is empty). */
export function similarity({ inter, union }) {
  const out = new Float64Array(inter.length);
  for (let i = 0; i < out.length; i++) out[i] = union[i] > 0 ? inter[i] / union[i] : 0;
  return out;
}

/**
 * A result as bioagent.admet.engine.PrecomputedSimilarity reads `<endpoint>.u32`: n_query × 3 little-endian uint32
 * (arg, inter, union per query) → Uint8Array.
 */
export function tanimotoRows({ arg, inter, union }) {
  const out = new Uint8Array(arg.length * 12);
  const view = new DataView(out.buffer);
  for (let i = 0; i < arg.length; i++) {
    view.setUint32(12 * i, arg[i], true); view.setUint32(12 * i + 4, inter[i], true); view.setUint32(12 * i + 8, union[i], true);
  }
  return out;
}

function mulberry32(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 2 ** 32;
  };
}

/**
 * The queries a result from another kernel is checked on in full (the rule of bioagent.admet.engine.sample_queries):
 * every query when there are at most 32, else the first, the last and 8 more drawn from the others with a fixed seed.
 * The 8 are drawn here with this module's own generator, not numpy's, so the page and Python check different queries.
 */
export function sampleQueries(n, { seed = SAMPLE_SEED } = {}) {
  if (n <= 32) return Uint32Array.from({ length: n }, (_, i) => i);
  const rand = mulberry32(seed);
  const picked = new Set([0, n - 1]);
  while (picked.size < 10) picked.add(1 + Math.floor(rand() * (n - 2)));
  return Uint32Array.from([...picked].sort((a, b) => a - b));
}

/** The exact search on this CPU: WebAssembly SIMD where it runs, else JavaScript → {result, backend}. */
export function tanimotoBestCPU(queries, refs, { words = FP_WORDS, isCancelled } = {}) {
  const wasm = words <= FP_WORDS ? tanimotoBestWasm(queries, refs, { words, isCancelled }) : null;
  return wasm ? { result: wasm, backend: "wasm-simd" } : { result: tanimotoBestJS(queries, refs, { words, isCancelled }), backend: "js" };
}

/**
 * The check a result from another kernel (the GPU's) passes before anything uses it, the rule Python's
 * PrecomputedSimilarity applies again: one answer per query; every index a reference; every intersection and union
 * those of the query and that reference (which bounds the intersection by the smaller popcount and makes the union the
 * two popcounts less it); and for the sampleQueries() the exact search over every reference on this CPU gives the same
 * index, intersection and union. → {ok, checked (the queries searched in full), backend (of that search), reason, ms}.
 * It is a sample: a wrong index on a query outside it is caught only when its counts are wrong too.
 */
export function validateTanimoto(queries, refs, result, { words = FP_WORDS, seed = SAMPLE_SEED, isCancelled } = {}) {
  const t0 = now();
  const { nq, nr } = searchShape(queries, refs, { words });
  const fail = (reason, checked = 0, backend = null) => ({ ok: false, checked, backend, reason, ms: ms3(now() - t0) });
  for (const k of ["arg", "inter", "union"]) {
    if (!(result?.[k] instanceof Uint32Array) || result[k].length !== nq) return fail(`its ${k} is not ${nq} uint32 values`);
  }
  for (let i = 0; i < nq; i++) {
    const r = result.arg[i];
    if (r >= nr) return fail(`query ${i}: its nearest index ${r} is not a reference (there are ${nr})`);
    let a = 0, b = 0, c = 0;
    for (let k = 0; k < words; k++) {
      const x = queries[i * words + k], y = refs[r * words + k];
      a += pop32(x); b += pop32(y); c += pop32(x & y);
    }
    if (result.inter[i] !== c || result.union[i] !== a + b - c) {
      return fail(`query ${i}: its intersection and union with its nearest reference ${r} are ${result.inter[i]}/${result.union[i]}, not those of the fingerprints (${c}/${a + b - c})`);
    }
  }
  const sample = sampleQueries(nq, { seed });
  const sub = new Uint32Array(sample.length * words);
  sample.forEach((q, j) => sub.set(queries.subarray(q * words, (q + 1) * words), j * words));
  const { result: exact, backend } = tanimotoBestCPU(sub, refs, { words, isCancelled });
  for (let j = 0; j < sample.length; j++) {
    const q = sample[j];
    if (exact.arg[j] !== result.arg[q] || exact.inter[j] !== result.inter[q] || exact.union[j] !== result.union[q]) {
      return fail(`query ${q}: the exact search gives reference ${exact.arg[j]} (${exact.inter[j]}/${exact.union[j]}), not ${result.arg[q]} (${result.inter[q]}/${result.union[q]})`, j, backend);
    }
  }
  return { ok: true, checked: sample.length, backend, reason: null, ms: ms3(now() - t0) };
}
