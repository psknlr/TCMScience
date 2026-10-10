// The WebAssembly SIMD sequence counts (wasm/seqcount.wat), instantiated synchronously from the embedded bytes the
// first time they are needed; null where the engine has no SIMD. They return exactly what compute/kernels.js
// sequenceCounts returns (the JavaScript version stays the reference and the fallback).

import { SEQCOUNT_WASM } from "./wasm/seqcount.wasm.js";

const PAGE = 65536;
const MAX_BYTES = 256 * 1024 * 1024; // beyond this the JavaScript kernel (or Python) takes the work
const KEEP_BYTES = 16 * 1024 * 1024; // a memory grown beyond this is let go after the call (it cannot shrink)
const encoder = new TextEncoder();
const GC = new Uint8Array(256);
GC[71] = 1; GC[67] = 1; GC[83] = 1; // G, C, S

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
    const bytes = decode(SEQCOUNT_WASM);
    if (!WebAssembly.validate(bytes)) throw new Error("this engine has no WebAssembly SIMD");
    instance = new WebAssembly.Instance(new WebAssembly.Module(bytes), {});
  } catch (err) {
    unavailable = String(err?.message || err);
    instance = null;
  }
  return instance;
}

/** Whether the SIMD kernels run here (and why not). */
export function simdStatus() {
  return load() ? { available: true } : { available: false, reason: unavailable };
}

/** The kernel's memory, grown to `bytes` (zero-filled up to there). */
function memoryFor(x, bytes) {
  const mem = x.memory;
  if (bytes > MAX_BYTES) throw new RangeError("workload too large for the SIMD kernel");
  if (mem.buffer.byteLength < bytes) mem.grow(Math.ceil((bytes - mem.buffer.byteLength) / PAGE));
  new Uint8Array(mem.buffer, 0, bytes).fill(0);
  return mem.buffer;
}

const stride16 = (w) => Math.ceil(w / 16) * 16;

/** The ASCII string `s` as bytes at `at` (the rest of its `stride` stays zero); anything but ASCII is refused. */
function put(buffer, s, at, stride) {
  const { read, written } = encoder.encodeInto(s, new Uint8Array(buffer, at, stride));
  // every character beyond ASCII takes two or more UTF-8 bytes (a lone surrogate three)
  if (read !== s.length || written !== s.length) throw new RangeError("sequence counts take ASCII sequences");
}

const cancelled = () => Object.assign(new Error("cancelled"), { code: "cancelled" });

/**
 * The counts of compute/kernels.js sequenceCounts, computed with SIMD. → Uint32Array, or null when SIMD is not here.
 * Windowed GC counts slide in JavaScript over the same bytes (linear, nothing for SIMD to win); the rest is SIMD.
 */
export function sequenceCountsSIMD(tool, sequences, window = 0, { isCancelled } = {}) {
  if (!load()) return null;
  try {
    return countsWith(instance.exports, tool, sequences, window, isCancelled);
  } finally {
    if (instance && instance.exports.memory.buffer.byteLength > KEEP_BYTES) instance = null;
  }
}

function countsWith(x, tool, sequences, window, isCancelled) {
  const width = sequences[0]?.length ?? 0;
  if (!width || sequences.some((s) => typeof s !== "string" || s.length !== width)) {
    throw new RangeError("sequences must be non-empty and of one length");
  }
  const stride = stride16(width);
  if (tool === "native.distance_matrix") {
    const n = sequences.length;
    const outOff = n * stride;
    const buf = memoryFor(x, outOff + n * n * 16);
    sequences.forEach((s, i) => put(buf, s, i * stride, stride));
    x.bases(0, outOff);
    for (let i = 0; i < n; i++) {
      if (isCancelled?.()) throw cancelled();
      x.distance_row(0, n, stride, outOff, i);
    }
    return new Uint32Array(x.memory.buffer, outOff, n * n * 4).slice();
  }
  if (tool === "native.hamming_distance") {
    if (sequences.length !== 2) throw new RangeError("hamming distance compares two sequences");
    const buf = memoryFor(x, 2 * stride);
    put(buf, sequences[0], 0, stride);
    put(buf, sequences[1], stride, stride);
    return Uint32Array.of(x.hamming(0, stride, stride), 0, 0, 0);
  }
  if (tool === "native.gc_content") {
    if (sequences.length !== 1) throw new RangeError("GC content counts one sequence");
    if (!Number.isSafeInteger(window) || window < 0 || window > width) throw new RangeError("window out of range");
    const buf = memoryFor(x, stride);
    put(buf, sequences[0], 0, stride);
    const records = window > 0 ? width - window + 2 : 1;
    const out = new Uint32Array(records * 4);
    out[0] = x.gc(0, stride);
    if (window > 0) {
      const b = new Uint8Array(x.memory.buffer, 0, width);
      let c = 0;
      for (let k = 0; k < window; k++) c += GC[b[k]];
      out[4] = c;
      // window r + 1 drops letter r - 1 and takes letter r - 1 + window
      for (let r = 1; r < records - 1; r++) {
        c += GC[b[r - 1 + window]] - GC[b[r - 1]];
        out[(r + 1) * 4] = c;
      }
    }
    return out;
  }
  throw new RangeError(`no SIMD kernel for ${tool}`);
}
