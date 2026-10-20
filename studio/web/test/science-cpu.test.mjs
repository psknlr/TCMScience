// The CPU Tanimoto kernels (compute/science-cpu.js, compute/wasm/science.wat): the packed-word layout against the bytes
// numpy's np.packbits wrote (fixtures/science_tanimoto.json, make_science_fixtures.py), every kernel against Python's
// bioagent.admet.chem.tanimoto_best on that fixture, the WebAssembly SIMD kernel against the JavaScript one and both
// against a brute force over bit positions (random, structured, tie, empty and all-ones inputs), the committed binary
// against its text, and the check a GPU's result passes before it is used.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  FP_WORDS, fingerprintBits, packedBytes, packedWords, popcounts, sampleQueries, scienceSimdStatus, searchShape, similarity, tanimotoBestCPU,
  tanimotoBestJS, tanimotoBestWasm, tanimotoRows, validateTanimoto, wordsFromBits,
} from "../js/compute/science-cpu.js";

const FIXTURE = JSON.parse(readFileSync(new URL("./fixtures/science_tanimoto.json", import.meta.url), "utf8"));
const hexBytes = (rows) => Uint8Array.from(Buffer.from(rows.join(""), "hex"));

function random(seed) {
  let s = seed >>> 0;
  return () => (s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 2 ** 32;
}

/** Fingerprints as sets of bit positions, each bit set with probability `density`. */
function randomSets(r, n, bits, density) {
  return Array.from({ length: n }, () => { const s = []; for (let b = 0; b < bits; b++) if (r() < density) s.push(b); return s; });
}

/** The definition, from bit positions alone: |A ∩ B| / |A ∪ B|, the first index of the greatest (an empty union is 0). */
function brute(querySets, refSets) {
  const out = { arg: [], inter: [], union: [] };
  const refs = refSets.map((s) => new Set(s));
  for (const q of querySets) {
    const qs = new Set(q);
    let best = null;
    refs.forEach((rs, i) => {
      let c = 0;
      for (const b of qs) if (rs.has(b)) c++;
      const u = new Set([...qs, ...rs]).size;
      const [num, den] = u ? [c, u] : [0, 1];
      if (!best || num * best.den > best.num * den) best = { i, c, u, num, den };
    });
    out.arg.push(best.i); out.inter.push(best.c); out.union.push(best.u);
  }
  return out;
}

const plain = (r) => ({ arg: Array.from(r.arg), inter: Array.from(r.inter), union: Array.from(r.union) });

/** Every kernel on the same input: JS, WASM (one block and blocks of a few references), the brute force. */
function allAgree(querySets, refSets, words, label) {
  const q = wordsFromBits(querySets, { words }), r = wordsFromBits(refSets, { words });
  const want = brute(querySets, refSets);
  assert.deepEqual(plain(tanimotoBestJS(q, r, { words })), want, `${label}: JavaScript`);
  if (words <= FP_WORDS) {
    assert.deepEqual(plain(tanimotoBestWasm(q, r, { words })), want, `${label}: WebAssembly`);
    // a budget that holds a block of 1 to 3 references: the blocks merge as one search over every reference
    const small = Math.min(q.length / words, 1024) * (256 + 16) + 3 * 260;
    assert.deepEqual(plain(tanimotoBestWasm(q, r, { words, memoryBytes: small })), want, `${label}: WebAssembly in blocks`);
  }
  return want;
}

test("this engine has WebAssembly SIMD for the Tanimoto kernel", () => {
  assert.deepEqual(scienceSimdStatus(), { available: true });
});

test("the committed binary is what its text compiles to", async (t) => {
  try {
    await import("wabt");
  } catch {
    t.skip("wabt is not installed here (npm ci in studio/)");
    return;
  }
  const { compile, moduleSource, modules, orphans } = await import("../../scripts/build_wasm.mjs");
  const wat = fileURLToPath(new URL("../js/compute/wasm/science.wat", import.meta.url));
  const committed = readFileSync(new URL("../js/compute/wasm/science.wasm.js", import.meta.url), "utf8");
  assert.equal(committed, moduleSource("science", await compile(wat)), "run node studio/scripts/build_wasm.mjs");
  assert.ok(["science", "seqcount"].every((m) => modules().includes(m)), "build_wasm builds every module, each on its own");
  assert.deepEqual(orphans(), [], "no generated module without its text");
});

test("np.packbits rows: packedWords and wordsFromBits give exactly the bytes numpy wrote", () => {
  for (const side of ["queries", "refs"]) {
    const bytes = hexBytes(FIXTURE[side].rows);
    assert.equal(createHash("sha256").update(bytes).digest("hex"), FIXTURE[side].sha256, `${side}: the fixture's bytes`);
    const words = packedWords(bytes);
    assert.equal(words.length, FIXTURE[side].rows.length * FP_WORDS);
    assert.equal(words.buffer, bytes.buffer, "aligned bytes are viewed, not copied");
    const fromBits = wordsFromBits(FIXTURE[side].bits_set);
    assert.deepEqual(Array.from(fromBits), Array.from(words), `${side}: bit b set → the word and bit numpy's byte order gives`);
    FIXTURE[side].bits_set.forEach((set, i) => assert.deepEqual(fingerprintBits(words, i), set, `${side} ${i}: the bits read back`));
    assert.deepEqual(Array.from(popcounts(words)), FIXTURE[side].bits_set.map((s) => s.length));
    assert.equal(createHash("sha256").update(packedBytes(fromBits)).digest("hex"), FIXTURE[side].sha256, `${side}: the bytes back, as numpy wrote them`);
    // unaligned bytes (an offset into a larger buffer) are copied with the same words
    const shifted = new Uint8Array(bytes.length + 1);
    shifted.set(bytes, 1);
    const copy = packedWords(shifted.subarray(1));
    assert.notEqual(copy.buffer, shifted.buffer);
    assert.deepEqual(Array.from(copy), Array.from(words));
  }
  // the first bits of a row are the most significant of its first byte, which is the low byte of word 0
  assert.deepEqual(Array.from(wordsFromBits([[0]], { words: 1 })), [0x80]);
  assert.deepEqual(Array.from(wordsFromBits([[7, 8, 31]], { words: 1 })), [0x01 | 0x8000 | 0x01000000]);
  assert.deepEqual(Array.from(packedWords(Uint8Array.of(0x80, 0, 0, 1), { words: 1 })), [0x01000080]);
  assert.throws(() => packedWords(new Uint8Array(255)), /not whole fingerprints of 256 bytes/);
  assert.throws(() => packedWords([1, 2, 3, 4], { words: 1 }), TypeError);
  assert.throws(() => wordsFromBits([[2048]]), /not one of a 2048-bit/);
});

test("every kernel answers as bioagent.admet.chem.tanimoto_best on numpy's fingerprints", () => {
  const q = packedWords(hexBytes(FIXTURE.queries.rows)), r = packedWords(hexBytes(FIXTURE.refs.rows));
  const want = { arg: FIXTURE.expected.arg, inter: FIXTURE.expected.inter, union: FIXTURE.expected.union };
  const js = tanimotoBestJS(q, r);
  assert.deepEqual(plain(js), want, "JavaScript");
  assert.deepEqual(plain(tanimotoBestWasm(q, r)), want, "WebAssembly SIMD");
  assert.deepEqual(plain(tanimotoBestWasm(q, r, { memoryBytes: 40 * 272 + 7 * 260 })), want, "WebAssembly in blocks of 7 references");
  assert.deepEqual(Array.from(similarity(js), (x) => Buffer.from(new Float64Array([x]).buffer).reverse().toString("hex")), FIXTURE.expected.similarity_f64,
    "the float64 similarities Python computes from the counts");
  // what was planted came out as planted (so the fixture tests what it says)
  assert.equal(want.arg[0], 0, "the empty query: every similarity 0, reference 0");
  assert.equal(want.inter[0], 0);
  assert.deepEqual([want.arg[1], want.inter[1], want.union[1]], [211, 2048, 2048]);
  assert.equal(want.arg[2], 20, "three equal maxima: the smallest index");
  assert.deepEqual([want.arg[4], want.inter[4], want.union[4]], [60, 20, 40], "1/2 at 60 (20/40) before 1/2 at 120 (25/50)");
  assert.deepEqual([want.arg[6], want.inter[6], want.union[6]], [70, 25, 50], "1/2 at 70 (25/50) before 1/2 at 130 (20/40)");
});

test("random, structured, tie, empty and all-ones inputs: WebAssembly = JavaScript = the brute force", () => {
  const r = random(20261019);
  for (const words of [64, 1, 3, 17, 63, 65]) {
    const bits = words * 32;
    for (const density of [0.003, 0.03, 0.5, 0.97]) {
      allAgree(randomSets(r, 1 + Math.floor(r() * 12), bits, density), randomSets(r, 1 + Math.floor(r() * 300), bits, density), words, `random ${words} words, density ${density}`);
    }
  }
  // families: references that are copies of a few bases with a bit or two changed; queries drawn the same way
  const bases = randomSets(r, 5, 2048, 0.025);
  const member = () => { const b = bases[Math.floor(r() * bases.length)].filter(() => r() > 0.05); b.push(Math.floor(r() * 2048)); return [...new Set(b)].sort((x, y) => x - y); };
  allAgree(Array.from({ length: 40 }, member), Array.from({ length: 700 }, member), 64, "families");
  // ties: every reference the same (index 0), duplicates far apart, equal ratios with other counts
  const one = randomSets(r, 1, 2048, 0.02)[0];
  assert.deepEqual(allAgree([one, [], one.slice(0, 5)], Array(50).fill(one), 64, "all references equal").arg, [0, 0, 0]);
  const refs = randomSets(r, 600, 2048, 0.02);
  refs[599] = refs[3];
  refs[300] = refs[3];
  assert.equal(allAgree([refs[3]], refs, 64, "duplicates across blocks").arg[0], 3);
  const s = Array.from({ length: 40 }, (_, i) => 50 * i);
  const ratioRefs = [[1, 2, 3], s.slice(0, 25).concat([3, 7, 11, 13, 17, 19, 23, 29, 31, 37]), s.slice(0, 20)];
  assert.deepEqual(allAgree([s], ratioRefs, 64, "25/50 before 20/40"), { arg: [1], inter: [25], union: [50] });
  assert.deepEqual(allAgree([s], [ratioRefs[0], ratioRefs[2], ratioRefs[1]], 64, "20/40 before 25/50"), { arg: [1], inter: [20], union: [40] });
  // empty and all-ones, every way round
  const ones = Array.from({ length: 2048 }, (_, i) => i);
  assert.deepEqual(allAgree([[]], [[], [], []], 64, "empty against empty"), { arg: [0], inter: [0], union: [0] });
  assert.deepEqual(allAgree([[]], [[5], ones, []], 64, "empty query"), { arg: [0], inter: [0], union: [1] });
  assert.deepEqual(allAgree([ones], [[5], ones, []], 64, "all-ones query"), { arg: [1], inter: [2048], union: [2048] });
  assert.deepEqual(allAgree([ones, [1, 2]], [ones, ones], 64, "all-ones references"), { arg: [0, 0], inter: [2048, 2], union: [2048, 2048] });
  // more queries than one block of the WebAssembly kernel (1024)
  allAgree(randomSets(r, 1100, 2048, 0.02), randomSets(r, 30, 2048, 0.02), 64, "1100 queries");
});

test("no queries, no references, wrong shapes: the conventions Python keeps, and clear refusals", () => {
  const some = wordsFromBits([[1], [2]]);
  for (const kernel of [tanimotoBestJS, tanimotoBestWasm]) {
    assert.deepEqual(plain(kernel(new Uint32Array(0), new Uint32Array(0))), { arg: [], inter: [], union: [] }, "no queries: no answers, whatever the references");
    assert.throws(() => kernel(some, new Uint32Array(0)), /there are no reference fingerprints to compare with/);
    assert.throws(() => kernel(new Uint8Array(256), some), /queries must be a Uint32Array/);
    assert.throws(() => kernel(some, new Uint32Array(65)), /refs hold 65 words, not whole fingerprints of 64/);
    assert.throws(() => kernel(some, some, { words: 0 }), /a fingerprint is 1 to/);
  }
  assert.throws(() => tanimotoBestWasm(new Uint32Array(65), new Uint32Array(65), { words: 65 }), /up to 2048 bits \(64 words\), not 2080/);
  assert.deepEqual(searchShape(some, some), { nq: 2, nr: 2 });
});

test("a cancel stops both kernels", () => {
  const r = random(7);
  // the WebAssembly kernel looks between slices of about 4 M comparisons: 1000 × 9000 is three of them
  const q = wordsFromBits(randomSets(r, 1000, 2048, 0.02)), refs = wordsFromBits(randomSets(r, 9000, 2048, 0.02));
  for (const kernel of [tanimotoBestJS, tanimotoBestWasm]) {
    let calls = 0;
    assert.throws(() => kernel(q, refs, { isCancelled: () => ++calls > 1 }), (e) => e.code === "cancelled");
    assert.equal(calls, 2, "stopped at the first look after the cancel");
  }
});

test("the CPU's exact search: WebAssembly where it runs, JavaScript beyond 2048 bits", () => {
  const q = wordsFromBits([[1, 2]]), r = wordsFromBits([[1], [1, 2]]);
  assert.equal(tanimotoBestCPU(q, r).backend, "wasm-simd");
  assert.equal(tanimotoBestCPU(new Uint32Array(65), new Uint32Array(130), { words: 65 }).backend, "js");
});

test("a result as <endpoint>.u32: n × 3 little-endian uint32 (arg, inter, union)", () => {
  const rows = tanimotoRows({ arg: Uint32Array.of(1, 70000), inter: Uint32Array.of(2, 3), union: Uint32Array.of(4, 2048) });
  assert.deepEqual(Array.from(rows), [1, 0, 0, 0, 2, 0, 0, 0, 4, 0, 0, 0, 0x70, 0x11, 1, 0, 3, 0, 0, 0, 0, 8, 0, 0]);
});

test("the sample: every query up to 32, else the first, the last and 8 more, the same every time", () => {
  assert.deepEqual(Array.from(sampleQueries(0)), []);
  assert.deepEqual(Array.from(sampleQueries(32)), Array.from({ length: 32 }, (_, i) => i));
  const s = Array.from(sampleQueries(1000));
  assert.equal(s.length, 10);
  assert.equal(s[0], 0);
  assert.equal(s.at(-1), 999);
  assert.deepEqual(s, [...s].sort((a, b) => a - b));
  assert.equal(new Set(s).size, 10);
  assert.deepEqual(Array.from(sampleQueries(1000)), s, "a fixed seed");
  assert.equal(sampleQueries(33).length, 10);
});

test("validateTanimoto: an exact result passes; wrong counts, a bad index or a wrong sampled answer do not", () => {
  const r = random(11);
  const qs = randomSets(r, 100, 2048, 0.02), rs = randomSets(r, 400, 2048, 0.02);
  const q = wordsFromBits(qs), refs = wordsFromBits(rs);
  const exact = tanimotoBestJS(q, refs);
  const ok = validateTanimoto(q, refs, exact);
  assert.equal(ok.ok, true, ok.reason);
  assert.equal(ok.checked, 10);
  assert.equal(ok.backend, "wasm-simd");
  const clone = () => ({ arg: exact.arg.slice(), inter: exact.inter.slice(), union: exact.union.slice() });
  const sample = new Set(sampleQueries(100));
  const outside = [...Array(100).keys()].find((i) => !sample.has(i));
  // counts that are not those of the query and its reference, wherever the query is
  let bad = clone(); bad.inter[outside] += 1;
  assert.match(validateTanimoto(q, refs, bad).reason, new RegExp(`query ${outside}: its intersection and union with its nearest reference`));
  bad = clone(); bad.union[outside] += 1;
  assert.equal(validateTanimoto(q, refs, bad).ok, false);
  bad = clone(); bad.arg[outside] = 400;
  assert.match(validateTanimoto(q, refs, bad).reason, /its nearest index 400 is not a reference \(there are 400\)/);
  bad = clone(); bad.arg = bad.arg.subarray(1);
  assert.match(validateTanimoto(q, refs, bad).reason, /its arg is not 100 uint32 values/);
  // another reference, with that reference's own (consistent) counts: caught in the sample …
  const worse = (i) => {
    const b = clone();
    const other = (exact.arg[i] + 1) % 400;
    const t = tanimotoBestJS(q.subarray(i * 64, (i + 1) * 64), refs.subarray(other * 64, (other + 1) * 64));
    b.arg[i] = other; b.inter[i] = t.inter[0]; b.union[i] = t.union[0];
    return b;
  };
  assert.match(validateTanimoto(q, refs, worse(0)).reason, /query 0: the exact search gives reference/);
  assert.match(validateTanimoto(q, refs, worse(99)).reason, /query 99: the exact search gives reference/);
  // … and not outside it: the check is a sample, as it says
  assert.equal(validateTanimoto(q, refs, worse(outside)).ok, true);
});
