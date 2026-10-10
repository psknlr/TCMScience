// The browser's CPU kernels against the Python implementation (fixtures/kernel_cases.json, made by
// make_kernel_fixtures.py from bioagent with no kernel installed), through the JSON boundary Python uses in the Worker.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { bitsDouble, doubleBits, pythonKernels } from "../js/compute/bridge.js";
import { codePoints, levenshtein, needlemanWunsch, sequenceCounts, smithWaterman } from "../js/compute/kernels.js";

const CASES = JSON.parse(readFileSync(new URL("./fixtures/kernel_cases.json", import.meta.url), "utf8")).cases;

test("every fixture case: the JavaScript kernel answers what Python computes, to the bit and the type", () => {
  const k = pythonKernels();
  const byKernel = {};
  for (const c of CASES) {
    // the text Python sent, not a re-serialisation of it (JSON.stringify(-0) is "0")
    const got = JSON.parse(k.call(c.kernel, c.payload_json));
    assert.deepEqual(got, c.expect, `${c.kernel} ${c.payload_json.slice(0, 120)}`);
    byKernel[c.kernel] = (byKernel[c.kernel] || 0) + 1;
  }
  // the fixture covers each kernel substantially
  assert.ok(byKernel["align.nw_linear"] > 100 && byKernel["align.sw_linear"] > 100, JSON.stringify(byKernel));
  assert.ok(byKernel["sequence.levenshtein"] > 30 && byKernel["sequence.counts"] >= 30, JSON.stringify(byKernel));
});

test("the fixtures include the hard cases: integer-typed scores, negative zero, empty local alignments, astral CJK", () => {
  const align = CASES.filter((c) => c.kernel.startsWith("align."));
  assert.ok(align.some((c) => c.expect.score_int), "an int-typed score");
  assert.ok(align.some((c) => !c.expect.score_int), "a float-typed score");
  assert.ok(align.some((c) => c.expect.score_bits === "8000000000000000"), "a -0.0 score");
  assert.ok(align.some((c) => c.kernel === "align.sw_linear" && c.expect.aligned_x === "" && c.expect.start_a === 1 && c.expect.end_a === 0), "an empty local alignment");
  assert.ok(CASES.some((c) => /[\u{20000}-\u{2FFFF}]/u.test(c.payload_json)), "a letter outside the BMP");
  assert.ok(CASES.some((c) => /"gap": -0\.0/.test(c.payload_json)), "a gap of -0.0, as Python writes it");
});

test("scores cross the boundary as their IEEE-754 bits: 4.0 stays a float, -0.0 keeps its sign", () => {
  for (const x of [0, -0, 4, -1.5, 0.1 + 0.2, Number.MAX_SAFE_INTEGER, -(2 ** 52) + 0.5, 1e-308]) {
    const back = bitsDouble(doubleBits(x));
    assert.ok(Object.is(back, x), `${x} → ${doubleBits(x)} → ${back}`);
  }
  assert.equal(doubleBits(-0), "8000000000000000");
  assert.equal(doubleBits(4), "4010000000000000");
});

test("code points, not UTF-16 units: an astral letter is one letter", () => {
  assert.deepEqual(Array.from(codePoints("𠀀a")), [0x20000, 0x61]);
  assert.equal(levenshtein("𠀀", "𠀁"), 1, "one substitution, not two");
  // x = 𠀀黄 against y = 黄, a match scoring 1 and anything else -1: the two 黄 meet, 𠀀 takes the gap
  const r = needlemanWunsch("𠀀黄", "黄", "黄𠀀", "黄", [1, -1], [false, false], -1, false);
  assert.equal(r.alignedX, "𠀀黄");
  assert.equal(r.alignedY, "-黄");
  assert.equal(r.score, 0);
  assert.equal([...r.alignedX].length, 2, "the surrogate pair stays one letter");
});

test("a kernel that cannot take its input declines (null) instead of throwing into Python", () => {
  const k = pythonKernels();
  assert.equal(k.call("align.nw_linear", JSON.stringify({ x: "AC", y: "AG", alpha_x: "A", alpha_y: "AG", table: [1, 0], table_int: [false, false], gap: -1, gap_int: false })), "null", "a letter missing from its alphabet");
  assert.equal(k.call("sequence.counts", JSON.stringify({ tool: "native.distance_matrix", sequences: ["ACG", "AC"] })), "null", "unequal lengths");
  assert.equal(k.call("sequence.counts", JSON.stringify({ tool: "native.gc_content", sequences: ["ACG"], window: 4 })), "null", "a window longer than the sequence");
  assert.equal(k.call("no.such.kernel", "{}"), "null");
  assert.equal(k.call("sequence.levenshtein", "not json"), "null");
});

test("sequence counts lay records out as the WebGPU kernel does", () => {
  const d = sequenceCounts("native.distance_matrix", ["AGCT", "GGCN", "A-CT"]);
  // pair (0,1): A/G (transition), G/G, C/C, T/N skipped → 3 sites, 1 difference, 1 transition
  assert.deepEqual(Array.from(d.slice(4, 8)), [3, 1, 1, 0]);
  assert.deepEqual(Array.from(d.slice(0, 4)), [0, 0, 0, 0], "the diagonal and the lower triangle stay zero");
  assert.deepEqual(Array.from(sequenceCounts("native.gc_content", ["GCASN"], 2)), [3, 0, 0, 0, 2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]);
  assert.deepEqual(Array.from(sequenceCounts("native.hamming_distance", ["ANU", "ACT"])), [2, 0, 0, 0]);
});

test("large inputs stay fast and exact: a 2000×2000 global alignment, a 5000×5000 edit distance", () => {
  const rnd = (n, seed) => { let s = seed; return Array.from({ length: n }, () => "ACGT"[(s = (s * 1103515245 + 12345) >>> 0) % 4]).join(""); };
  const a = rnd(2000, 7), b = rnd(2000, 11);
  const t0 = performance.now();
  const r = needlemanWunsch(a, b, "ACGT", "ACGT", [2, -1, -1, -1, -1, 2, -1, -1, -1, -1, 2, -1, -1, -1, -1, 2], new Array(16).fill(false), -2, false);
  const ms = performance.now() - t0;
  assert.equal(r.alignedX.replaceAll("-", ""), a);
  assert.equal(r.alignedY.replaceAll("-", ""), b);
  assert.ok(ms < 2000, `${ms} ms`);
  const t1 = performance.now();
  const d = levenshtein(rnd(5000, 3), rnd(5000, 5));
  assert.ok(d > 0 && performance.now() - t1 < 3000);
  const sw = smithWaterman(a, b, "ACGT", "ACGT", [2, -1, -1, -1, -1, 2, -1, -1, -1, -1, 2, -1, -1, -1, -1, 2], new Array(16).fill(false), -2, false);
  assert.ok(sw.score > 0 && sw.endA >= sw.startA);
});

test("a stop ends a kernel at its next row, and the bridge declines so Python raises its own KeyboardInterrupt", () => {
  let polls = 0;
  const stopAfter = (n) => () => ++polls > n;
  const a = "ACGT".repeat(500), b = "TGCA".repeat(500);
  assert.throws(() => levenshtein(a, b, { isCancelled: stopAfter(3) }), (e) => e.code === "cancelled");
  assert.equal(polls, 4, "checked once per row, stopped at the fourth");
  polls = 0;
  assert.throws(() => needlemanWunsch(a, b, "ACGT", "ACGT", Array(16).fill(1), Array(16).fill(false), -1, false, { isCancelled: stopAfter(0) }), (e) => e.code === "cancelled");
  polls = 0;
  assert.throws(() => sequenceCounts("native.distance_matrix", [a, b, a], 0, { isCancelled: stopAfter(1) }), (e) => e.code === "cancelled");
  const k = pythonKernels({ isCancelled: () => true });
  assert.equal(k.call("sequence.levenshtein", JSON.stringify({ x: a, y: b })), "null");
  assert.equal(JSON.parse(pythonKernels({ isCancelled: () => false }).call("sequence.levenshtein", JSON.stringify({ x: "ab", y: "abc" }))).distance, 1);
});
