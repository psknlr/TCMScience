// The kernels as Python sees them in the Worker (tcmstudio.browser_compute.install_js_kernels): one function,
// JSON text in and JSON text out, so nothing crosses the Pyodide boundary as a proxy and no number is converted on
// the way. A JavaScript number that Pyodide hands to Python as an int would lose a float's type (4.0 → 4) and the
// sign of a zero; a score therefore travels as the 16 hex digits of its IEEE-754 bits, with whether Python holds it
// as an int.
//
// call(name, payloadJson) → resultJson | "null" (declined). Names: sequence.counts, align.nw_linear,
// align.sw_linear, sequence.levenshtein (bioagent.tools._compute documents what each computes).

import { levenshtein, needlemanWunsch, sequenceCounts, smithWaterman } from "./kernels.js";

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

/**
 * Make the object handed to Python. `stats` (optional) receives {name, ms, backend} for each call, for the receipt
 * and the benchmarks; `now` is the clock; `isCancelled` is polled by the kernels (the Worker's interrupt flag).
 */
export function pythonKernels({ stats = null, now = () => performance.now(), isCancelled = null } = {}) {
  const opts = { isCancelled };
  const run = (name, a) => {
    switch (name) {
      case "sequence.counts":
        return Array.from(sequenceCounts(a.tool, a.sequences, a.window ?? 0, opts));
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
      stats?.push?.({ name: String(name), ms: now() - t0, backend: "js", ok: out !== null });
      return JSON.stringify(out);
    },
  };
}
