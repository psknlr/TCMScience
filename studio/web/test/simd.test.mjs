// The WebAssembly SIMD counts (compute/simd.js, compute/wasm/seqcount.wat) against the JavaScript kernel they must
// equal record for record, and the committed binary against the text it is compiled from.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { pythonKernels } from "../js/compute/bridge.js";
import { sequenceCounts } from "../js/compute/kernels.js";
import { sequenceCountsSIMD, simdStatus } from "../js/compute/simd.js";

function random(seed) {
  let s = seed >>> 0;
  return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
}
const text = (r, n, letters) => Array.from({ length: n }, () => letters[Math.floor(r() * letters.length)]).join("");

function same(tool, sequences, window = 0) {
  const want = Array.from(sequenceCounts(tool, sequences, window));
  const got = sequenceCountsSIMD(tool, sequences, window);
  assert.ok(got instanceof Uint32Array, "SIMD ran");
  assert.deepEqual(Array.from(got), want, `${tool} n=${sequences.length} width=${sequences[0].length} window=${window}`);
}

test("this engine has WebAssembly SIMD", () => {
  assert.deepEqual(simdStatus(), { available: true });
});

test("the committed binary is what its text compiles to", async (t) => {
  try {
    await import("wabt");
  } catch {
    t.skip("wabt is not installed here (npm ci in studio/)");
    return;
  }
  const { compile, moduleSource } = await import("../../scripts/build_wasm.mjs");
  const wat = fileURLToPath(new URL("../js/compute/wasm/seqcount.wat", import.meta.url));
  const committed = readFileSync(new URL("../js/compute/wasm/seqcount.wasm.js", import.meta.url), "utf8");
  assert.equal(committed, moduleSource("seqcount", await compile(wat)), "run node studio/scripts/build_wasm.mjs");
});

test("every tool, awkward widths and alphabets: SIMD counts exactly what the JavaScript kernel counts", () => {
  const r = random(20261010);
  // lower case, U, IUPAC codes, gaps, NUL and other ASCII are no bases; S still counts toward GC
  const alphabets = ["ACGT", "ACGTN-RY", "acgtACGT", "ACGTUSWKMBDHVN-.*", "\u0000ACGT", "GCS", "A", "-"];
  for (const width of [1, 2, 15, 16, 17, 31, 32, 33, 100, 255, 256, 257, 1000]) {
    for (const letters of alphabets) {
      const n = 1 + Math.floor(r() * 9);
      same("native.distance_matrix", Array.from({ length: n }, () => text(r, width, letters)));
      same("native.hamming_distance", [text(r, width, letters), text(r, width, letters)]);
      const s = text(r, width, letters);
      for (const w of new Set([0, 1, Math.max(1, width >> 1), width])) same("native.gc_content", [s], w);
    }
  }
});

test("identical, all-gap and transition-only sequences", () => {
  same("native.distance_matrix", ["ACGTACGTACGTACGTA", "ACGTACGTACGTACGTA", "ACGTACGTACGTACGTA"]);
  same("native.distance_matrix", ["-----------------", "NNNNNNNNNNNNNNNNN"]);
  same("native.distance_matrix", ["AAAACCCCGGGGTTTTA", "GGGGTTTTAAAACCCCG"]);   // transitions only
  same("native.distance_matrix", ["AAAACCCCGGGGTTTTA", "CCCCAAAATTTTGGGGT"]);   // transversions only
  same("native.hamming_distance", ["ACGU".repeat(40), "ACGT".repeat(40)]);
});

test("large inputs: many sequences, long sequences, and a memory that is let go and grown again", () => {
  const r = random(7);
  same("native.distance_matrix", Array.from({ length: 64 }, () => text(r, 3001, "ACGTN-")));
  same("native.hamming_distance", [text(r, 200003, "ACGTN"), text(r, 200003, "ACGTN")]);
  same("native.gc_content", [text(r, 100001, "ACGTS")], 777);
  // 2,100 × 2,100 records × 16 bytes is past what the module keeps between calls; the next call starts afresh
  const big = Array.from({ length: 2100 }, () => text(r, 16, "ACGT"));
  same("native.distance_matrix", big);
  same("native.distance_matrix", ["ACGT", "AGGT"]);
});

test("what the JavaScript kernel refuses, SIMD refuses too", () => {
  for (const [tool, seqs, w] of [
    ["native.distance_matrix", ["ACG", "AC"], 0],
    ["native.distance_matrix", ["ACé", "ACG"], 0],
    ["native.distance_matrix", [""], 0],
    ["native.hamming_distance", ["A\ud800C", "AGC"], 0],   // a lone surrogate
    ["native.hamming_distance", ["AC", "AG", "AT"], 0],
    ["native.gc_content", ["ACG"], 4],
    ["native.gc_content", ["ACG"], -1],
    ["native.gc_content", ["ACG"], 1.5],
    ["native.gc_content", ["ACG", "ACG"], 0],
    ["native.gc_content", ["葛根"], 0],
    ["native.no_such_tool", ["ACG"], 0],
  ]) {
    assert.throws(() => sequenceCounts(tool, seqs, w), RangeError, `JavaScript: ${tool} ${JSON.stringify(seqs)} ${w}`);
    assert.throws(() => sequenceCountsSIMD(tool, seqs, w), RangeError, `SIMD: ${tool} ${JSON.stringify(seqs)} ${w}`);
  }
});

test("a stop ends the distance rows at the next row", () => {
  let polls = 0;
  const seqs = Array.from({ length: 50 }, () => "ACGT".repeat(50));
  assert.throws(() => sequenceCountsSIMD("native.distance_matrix", seqs, 0, { isCancelled: () => ++polls > 3 }),
    (e) => e.code === "cancelled");
  assert.equal(polls, 4);
  same("native.distance_matrix", seqs.slice(0, 5));   // and the module is still usable
});

test("the bridge counts with SIMD and says so; with simd:false the JavaScript kernel counts the same", () => {
  const payload = JSON.stringify({ tool: "native.distance_matrix", sequences: ["ACGTN", "AGGT-", "GGTTA"], window: 0 });
  const stats = [];
  const fast = JSON.parse(pythonKernels({ stats }).call("sequence.counts", payload));
  const plain = JSON.parse(pythonKernels({ simd: false }).call("sequence.counts", payload));
  assert.equal(fast.backend, "wasm-simd");
  assert.equal(plain.backend, "js");
  assert.deepEqual(fast.counts, plain.counts);
  assert.equal(stats[0].backend, "wasm-simd");
});
