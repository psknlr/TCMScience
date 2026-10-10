// CPU kernels that the Python tools call synchronously in the browser (bioagent.tools._compute.KERNELS), with the
// tools' own normalised input. Each returns exactly what the Python implementation computes, so a tool's output (and
// its output hash) is the same whether a kernel ran or not; bioagent checks every result before using it.
//
// Exactness, case by case:
// - Python's max(a, b, c) keeps the first of equal maxima; the selection here compares in the same order, never
//   with Math.max (which also differs for -0 and NaN).
// - Python adds ints exactly and floats in IEEE double. A cell here carries its value as a double and whether Python
//   would hold it as an int; ints stay below 2**53 because bioagent bounds every score by 2**30.
// - Python indexes strings by code point; JavaScript strings are UTF-16. Sequences are read as code points, so a
//   letter outside the Basic Multilingual Plane (some CJK) is one letter here too.
//
// Each kernel takes {isCancelled} last and checks it every row (every pair for the counts): a stop the person asks
// for ends the kernel at once, and Python, which reads the same interrupt flag, then raises KeyboardInterrupt.

/** Code points of a string. */
export function codePoints(s) {
  const out = new Uint32Array(s.length);
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) { out[n++] = ((c - 0xd800) << 10) + (d - 0xdc00) + 0x10000; i++; continue; }
    }
    out[n++] = c;
  }
  return out.subarray(0, n);
}

function indexer(alpha) {
  const map = new Map();
  const cps = codePoints(alpha);
  for (let i = 0; i < cps.length; i++) map.set(cps[i], i);
  return { map, size: cps.length };
}

/** A sequence as indexes into its alphabet, plus its code points (to write the alignment back). */
function encode(s, alpha) {
  const cps = codePoints(s);
  const idx = new Int32Array(cps.length);
  for (let i = 0; i < cps.length; i++) {
    const k = alpha.map.get(cps[i]);
    if (k === undefined) throw new RangeError("a letter of the sequence is missing from its alphabet");
    idx[i] = k;
  }
  return { cps, idx };
}

function scores(table, isInt, nx, ny) {
  if (table.length !== nx * ny || isInt.length !== nx * ny) throw new RangeError("score table does not match the alphabets");
  const value = Float64Array.from(table);
  const int = Uint8Array.from(isInt, (b) => (b ? 1 : 0));
  return { value, int };
}

function stopIf(isCancelled) {
  if (isCancelled?.()) throw Object.assign(new Error("cancelled"), { code: "cancelled" });
}

function render(moves, count, xs, ys, iStart, jStart) {
  // moves were collected end → start; columns are written start → end
  const a = [];
  const b = [];
  let i = iStart, j = jStart;
  for (let k = count - 1; k >= 0; k--) {
    const mv = moves[k];
    if (mv === 0) { a.push(String.fromCodePoint(xs[i])); b.push(String.fromCodePoint(ys[j])); i++; j++; }
    else if (mv === 1) { a.push(String.fromCodePoint(xs[i])); b.push("-"); i++; }
    else { a.push("-"); b.push(String.fromCodePoint(ys[j])); j++; }
  }
  return [a.join(""), b.join("")];
}

/**
 * Needleman–Wunsch with a linear gap, as bioagent.tools.align.global_alignment computes it.
 * table[i * |alphaY| + j] is the score of alphaX[i] against alphaY[j]; tableInt says which of them are Python ints;
 * gap and gapInt likewise. → {alignedX, alignedY, score (a double), scoreInt (whether Python holds an int)}.
 */
export function needlemanWunsch(x, y, alphaX, alphaY, table, tableInt, gap, gapInt, { isCancelled } = {}) {
  const ax = indexer(alphaX), ay = indexer(alphaY);
  const X = encode(x, ax), Y = encode(y, ay);
  const S = scores(table, tableInt, ax.size, ay.size);
  const n = X.idx.length, m = Y.idx.length, w = m + 1;
  const g = Number(gap), gi = gapInt ? 1 : 0;
  // two rows of values and int flags; the full traceback (0 diag, 1 up, 2 left)
  let pv = new Float64Array(w), pi = new Uint8Array(w);
  let cv = new Float64Array(w), ci = new Uint8Array(w);
  const T = new Uint8Array((n + 1) * w);
  pv[0] = 0; pi[0] = 0;                         // H[0][0] = 0.0, a float
  for (let j = 1; j <= m; j++) { pv[j] = j * g; pi[j] = gi; T[j] = 2; }
  for (let i = 1; i <= n; i++) {
    stopIf(isCancelled);
    cv[0] = i * g; ci[0] = gi; T[i * w] = 1;
    const rowScore = X.idx[i - 1] * ay.size;
    for (let j = 1; j <= m; j++) {
      const s = rowScore + Y.idx[j - 1];
      const dv = pv[j - 1] + S.value[s], di = pi[j - 1] & S.int[s];
      const uv = pv[j] + g, ui = pi[j] & gi;
      const lv = cv[j - 1] + g, li = ci[j - 1] & gi;
      let bv = dv, bi = di;                     // max(diag, up, left): the first of equal maxima
      if (uv > bv) { bv = uv; bi = ui; }
      if (lv > bv) { bv = lv; bi = li; }
      cv[j] = bv; ci[j] = bi;
      T[i * w + j] = bv === dv ? 0 : bv === uv ? 1 : 2;
    }
    let t = pv; pv = cv; cv = t;
    t = pi; pi = ci; ci = t;
  }
  const score = pv[m], scoreInt = pi[m] === 1;
  const moves = new Uint8Array(n + m);
  let count = 0, i = n, j = m;
  while (i > 0 || j > 0) {
    const t = T[i * w + j];
    if (i > 0 && j > 0 && t === 0) { moves[count++] = 0; i--; j--; }
    else if (i > 0 && (j === 0 || t === 1)) { moves[count++] = 1; i--; }
    else { moves[count++] = 2; j--; }
  }
  const [alignedX, alignedY] = render(moves, count, X.cps, Y.cps, 0, 0);
  return { alignedX, alignedY, score, scoreInt };
}

/**
 * Smith–Waterman with a linear gap, as bioagent.tools.align.local_alignment computes it. → {alignedX, alignedY,
 * score, scoreInt, startA, endA, startB, endB} (1-based starts, inclusive ends; an empty alignment is 1, 0, 1, 0).
 */
export function smithWaterman(x, y, alphaX, alphaY, table, tableInt, gap, gapInt, { isCancelled } = {}) {
  const ax = indexer(alphaX), ay = indexer(alphaY);
  const X = encode(x, ax), Y = encode(y, ay);
  const S = scores(table, tableInt, ax.size, ay.size);
  const n = X.idx.length, m = Y.idx.length, w = m + 1;
  const g = Number(gap), gi = gapInt ? 1 : 0;
  let pv = new Float64Array(w), pi = new Uint8Array(w);
  let cv = new Float64Array(w), ci = new Uint8Array(w);
  const T = new Uint8Array((n + 1) * w).fill(3);   // 3 = stop; the first row and column stay 0.0 and stop
  let best = 0, bestInt = 0, bi_ = 0, bj = 0;
  for (let i = 1; i <= n; i++) {
    stopIf(isCancelled);
    cv[0] = 0; ci[0] = 0;
    const rowScore = X.idx[i - 1] * ay.size;
    for (let j = 1; j <= m; j++) {
      const s = rowScore + Y.idx[j - 1];
      const dv = pv[j - 1] + S.value[s], di = pi[j - 1] & S.int[s];
      const uv = pv[j] + g, ui = pi[j] & gi;
      const lv = cv[j - 1] + g, li = ci[j - 1] & gi;
      let vv = 0, vi = 0;                         // max(0.0, diag, up, left)
      if (dv > vv) { vv = dv; vi = di; }
      if (uv > vv) { vv = uv; vi = ui; }
      if (lv > vv) { vv = lv; vi = li; }
      cv[j] = vv; ci[j] = vi;
      T[i * w + j] = vv === 0 ? 3 : vv === dv ? 0 : vv === uv ? 1 : 2;
      if (vv > best) { best = vv; bestInt = vi; bi_ = i; bj = j; }
    }
    let t = pv; pv = cv; cv = t;
    t = pi; pi = ci; ci = t;
  }
  const moves = new Uint8Array(n + m);
  let count = 0, i = bi_, j = bj;
  while (i > 0 && j > 0 && T[i * w + j] !== 3) {
    const t = T[i * w + j];
    if (t === 0) { moves[count++] = 0; i--; j--; }
    else if (t === 1) { moves[count++] = 1; i--; }
    else { moves[count++] = 2; j--; }
  }
  const [alignedX, alignedY] = render(moves, count, X.cps, Y.cps, i, j);
  return { alignedX, alignedY, score: best, scoreInt: bestInt === 1, startA: i + 1, endA: bi_, startB: j + 1, endB: bj };
}

/** Levenshtein distance over code points, as bioagent.tools.sequence.edit_distance computes it. */
export function levenshtein(x, y, { isCancelled } = {}) {
  const a = codePoints(x), b = codePoints(y);
  const m = b.length;
  let prev = new Int32Array(m + 1), cur = new Int32Array(m + 1);
  for (let j = 0; j <= m; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    stopIf(isCancelled);
    cur[0] = i;
    const ca = a[i - 1];
    for (let j = 1; j <= m; j++) {
      const del = prev[j] + 1, ins = cur[j - 1] + 1, sub = prev[j - 1] + (ca !== b[j - 1] ? 1 : 0);
      cur[j] = del < ins ? (del < sub ? del : sub) : (ins < sub ? ins : sub);
    }
    const t = prev; prev = cur; cur = t;
  }
  return prev[m];
}

// ------------------------------------------------------------------------------------------------- sequence counts

const BASE = new Uint8Array(128);   // A=1 C=2 G=3 T=4, everything else 0 (a gap or an ambiguity code)
BASE[65] = 1; BASE[67] = 2; BASE[71] = 3; BASE[84] = 4;
// for a pair of base codes (a * 5 + b): bit 0 a comparable site, bit 1 a difference, bit 2 a transition (A↔G, C↔T)
const PAIR = new Uint8Array(25);
for (let a = 1; a <= 4; a++) {
  for (let b = 1; b <= 4; b++) {
    const purine = (c) => c === 1 || c === 3;
    PAIR[a * 5 + b] = 1 | (a !== b ? 2 : 0) | (a !== b && purine(a) === purine(b) ? 4 : 0);
  }
}

function ascii(s) {
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c > 127) throw new RangeError("sequence counts take ASCII sequences");
    out[i] = c;
  }
  return out;
}

/**
 * The integer counts the WebGPU kernel produces (runtime/webgpu.js), on the CPU: 4 per record, records laid out as
 * the Python tools read them. tool: native.distance_matrix (n×n records: [sites, differences, transitions, 0] for
 * i < j) | native.hamming_distance (1 record: [differences]) | native.gc_content (record 0 the whole sequence,
 * record r ≥ 1 the window starting at r − 1). Sequences are already normalised by Python and ASCII.
 */
export function sequenceCounts(tool, sequences, window = 0, { isCancelled } = {}) {
  const seqs = sequences.map(ascii);
  const width = seqs[0]?.length ?? 0;
  if (!width || seqs.some((s) => s.length !== width)) throw new RangeError("sequences must be non-empty and of one length");
  if (tool === "native.distance_matrix") {
    const n = seqs.length;
    const codes = seqs.map((s) => s.map((c) => BASE[c]));
    const out = new Uint32Array(n * n * 4);
    for (let i = 0; i < n; i++) {
      const a = codes[i];
      for (let j = i + 1; j < n; j++) {
        stopIf(isCancelled);
        const b = codes[j];
        let sites = 0, diffs = 0, ts = 0;
        for (let k = 0; k < width; k++) {
          const t = PAIR[a[k] * 5 + b[k]];
          sites += t & 1; diffs += (t >> 1) & 1; ts += t >> 2;
        }
        const at = (i * n + j) * 4;
        out[at] = sites; out[at + 1] = diffs; out[at + 2] = ts;
      }
    }
    return out;
  }
  if (tool === "native.hamming_distance") {
    if (seqs.length !== 2) throw new RangeError("hamming distance compares two sequences");
    const [a, b] = seqs;
    let d = 0;
    for (let k = 0; k < width; k++) if (a[k] !== b[k]) d++;
    return Uint32Array.of(d, 0, 0, 0);
  }
  if (tool === "native.gc_content") {
    const s = seqs[0];
    if (!Number.isSafeInteger(window) || window < 0 || window > width) throw new RangeError("window out of range");
    const records = window > 0 ? width - window + 2 : 1;
    const out = new Uint32Array(records * 4);
    const prefix = new Uint32Array(width + 1);
    for (let k = 0; k < width; k++) {
      const c = s[k];
      prefix[k + 1] = prefix[k] + (c === 71 || c === 67 || c === 83 ? 1 : 0);   // G, C, S
    }
    out[0] = prefix[width];
    for (let r = 1; r < records; r++) out[r * 4] = prefix[r - 1 + window] - prefix[r - 1];
    return out;
  }
  throw new RangeError(`no count kernel for ${tool}`);
}
