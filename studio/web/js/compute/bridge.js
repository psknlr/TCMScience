// The kernels as Python sees them in the Worker (tcmstudio.browser_compute.install_js_kernels): one function,
// JSON text in and JSON text out, so nothing crosses the Pyodide boundary as a proxy and no number is converted on
// the way. A JavaScript number that Pyodide hands to Python as an int would lose a float's type (4.0 → 4) and the
// sign of a zero; a score therefore travels as the 16 hex digits of its IEEE-754 bits, with whether Python holds it
// as an int.
//
// call(name, payloadJson) → resultJson | "null" (declined). Names: sequence.counts, align.nw_linear,
// align.sw_linear, sequence.levenshtein (bioagent.tools._compute documents what each computes). The counts come
// from the WebAssembly SIMD kernel where the engine has SIMD, else from the JavaScript one; the result says which.

import { levenshtein, needlemanWunsch, sequenceCounts, smithWaterman } from "./kernels.js";
import { sequenceCountsSIMD } from "./simd.js";

const view = new DataView(new ArrayBuffer(8));

/** The 16 hex digits of a double's IEEE-754 bits, big-endian. */
export function doubleBits(x) {
  view.setFloat64(0, x, false);
  let s = "";
  for (let i = 0; i < 8; i++) s += view.getUint8(i).toString(16).padStart(2, "0");
  return s;
}

export function bitsDouble(hex) {
  for (let i = 0; i < 8; i++) view.setUint8(i, parseInt(hex.slice(2 * i, 2 * i + 2), 16));
  return view.getFloat64(0, false);
}

/** The integer counts → {counts, backend: "wasm-simd" | "js"}. SIMD declines (null) where the engine lacks it. */
function counts(a, opts, simd) {
  const window = a.window ?? 0;
  if (simd) {
    let c = null;
    try {
      c = sequenceCountsSIMD(a.tool, a.sequences, window, opts);
    } catch (err) {
      if (err?.code === "cancelled") throw err;
      c = null; // too large for its memory, say: the JavaScript kernel takes it (and rejects what is not valid)
    }
    if (c) return { counts: Array.from(c), backend: "wasm-simd" };
  }
  return { counts: Array.from(sequenceCounts(a.tool, a.sequences, window, opts)), backend: "js" };
}

/**
 * Make the object handed to Python. `stats` (optional) receives {name, ms, backend, ok} for each call, for the
 * benchmarks; `now` is the clock; `isCancelled` is polled by the kernels (the Worker's interrupt flag); `simd: false`
 * keeps the counts on the JavaScript kernel (the tests compare the two).
 */
export function pythonKernels({ stats = null, now = () => performance.now(), isCancelled = null, simd = true } = {}) {
  const opts = { isCancelled };
  const run = (name, a) => {
    switch (name) {
      case "sequence.counts":
        return counts(a, opts, simd);
      case "align.nw_linear": {
        const r = needlemanWunsch(a.x, a.y, a.alpha_x, a.alpha_y, a.table, a.table_int, a.gap, a.gap_int, opts);
        return { aligned_x: r.alignedX, aligned_y: r.alignedY, score_bits: doubleBits(r.score), score_int: r.scoreInt };
      }
      case "align.sw_linear": {
        const r = smithWaterman(a.x, a.y, a.alpha_x, a.alpha_y, a.table, a.table_int, a.gap, a.gap_int, opts);
        return {
          aligned_x: r.alignedX, aligned_y: r.alignedY, score_bits: doubleBits(r.score), score_int: r.scoreInt,
          start_a: r.startA, end_a: r.endA, start_b: r.startB, end_b: r.endB,
        };
      }
      case "sequence.levenshtein":
        return { distance: levenshtein(a.x, a.y, opts) };
      default:
        return null;
    }
  };
  return {
    backend: "js",
    names: ["sequence.counts", "align.nw_linear", "align.sw_linear", "sequence.levenshtein"],
    call(name, payload) {
      const t0 = now();
      let out = null;
      try {
        out = run(String(name), JSON.parse(String(payload)));
      } catch {
        // a kernel that cannot take this input declines and the Python implementation runs; after a stop, Python
        // raises KeyboardInterrupt at once, as it reads the same flag
        out = null;
      }
      stats?.push?.({ name: String(name), ms: now() - t0, backend: out?.backend || "js", ok: out !== null });
      return JSON.stringify(out);
    },
  };
}
