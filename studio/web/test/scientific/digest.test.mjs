// The incremental SHA-256 and MD5 of js/scientific/data/digest.js against node:crypto: the published test vectors,
// the empty input, every length around the 55/56/64-byte padding edges, random inputs up to 3 MB cut into random
// pieces (a digest must not depend on how a file was chunked), and digestFile over Blobs with and without stream().
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { test } from "node:test";
import { ALGORITHMS, Md5, Sha256, asBytes, digestFile, hasher, parseChecksum } from "../../js/scientific/data/digest.js";

const node = (alg, bytes) => createHash(alg).update(bytes).digest("hex");

/** A seeded generator, so a failure names a case that can be run again. */
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** `bytes` fed to `h` in random pieces (some empty, some one byte, some long). */
function feed(h, bytes, rand) {
  let at = 0;
  while (at < bytes.length) {
    const r = rand();
    const n = r < 0.1 ? 0 : r < 0.3 ? 1 + Math.floor(rand() * 63) : Math.floor(rand() * 200_000);
    h.update(bytes.subarray(at, Math.min(bytes.length, at + n)));
    at += n;
  }
  return h.digestHex();
}

test("the published vectors: FIPS 180-2 for SHA-256 and the RFC 1321 suite for MD5", () => {
  const sha = {
    "": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    abc: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq: "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
  };
  for (const [input, hex] of Object.entries(sha)) assert.equal(new Sha256().update(input).digestHex(), hex, JSON.stringify(input));
  assert.equal(new Sha256().update("a".repeat(1_000_000)).digestHex(), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
  const md5 = {
    "": "d41d8cd98f00b204e9800998ecf8427e",
    a: "0cc175b9c0f1b6a831c399e269772661",
    abc: "900150983cd24fb0d6963f7d28e17f72",
    "message digest": "f96b697d7cb7938d525a2f31aaf161d0",
    abcdefghijklmnopqrstuvwxyz: "c3fcd3d76192e4007dfb496cca67e13b",
    ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789: "d174ab98d277d9f5a5611c2c9f419d9f",
    "12345678901234567890123456789012345678901234567890123456789012345678901234567890": "57edf4a22be3c955ac49da2e2107b67a",
  };
  for (const [input, hex] of Object.entries(md5)) assert.equal(new Md5().update(input).digestHex(), hex, JSON.stringify(input));
});

test("every length across the padding edges, and multi-byte UTF-8 strings, match node:crypto", () => {
  const data = randomBytes(300);
  for (let n = 0; n <= 300; n++) {
    const b = data.subarray(0, n);
    assert.equal(new Sha256().update(b).digestHex(), node("sha256", b), `sha256 of ${n} bytes`);
    assert.equal(new Md5().update(b).digestHex(), node("md5", b), `md5 of ${n} bytes`);
  }
  const text = "葛根芩连汤 · Gegen Qinlian decoction · 🌿";
  assert.equal(new Sha256().update(text).digestHex(), node("sha256", Buffer.from(text, "utf8")));
  assert.equal(new Md5().update(text).digestHex(), node("md5", Buffer.from(text, "utf8")));
});

test("random inputs up to 3 MB in random pieces give the digest of the whole", () => {
  const seed = Date.now() >>> 0;
  const rand = rng(seed);
  for (let k = 0; k < 24; k++) {
    const n = k === 0 ? 0 : k === 1 ? 3 * 1024 * 1024 : Math.floor(rand() * 3 * 1024 * 1024);
    const bytes = randomBytes(n);
    for (const alg of ALGORITHMS) {
      assert.equal(feed(hasher(alg), bytes, rand), node(alg, bytes), `${alg} of ${n} bytes in random pieces (seed ${seed})`);
    }
  }
});

test("a hasher finishes once: the same digest again, no update after it; other inputs are views without copies", () => {
  const h = new Sha256().update("ab").update(new Uint8Array([0x63]));
  assert.equal(h.bytes, 3);
  const hex = h.digestHex();
  assert.equal(h.digestHex(), hex);
  assert.throws(() => h.update("more"), /after digestHex/);
  const buf = new Uint8Array([0, 1, 2, 3, 4, 5, 6, 7]);
  const view = new DataView(buf.buffer, 2, 4);
  assert.deepEqual([...asBytes(view)], [2, 3, 4, 5]);
  assert.equal(new Md5().update(view).digestHex(), node("md5", buf.subarray(2, 6)));
  assert.equal(new Md5().update(buf.buffer).digestHex(), node("md5", buf));
  assert.throws(() => new Sha256().update(42), /cannot hash a number/);
  assert.throws(() => hasher("sha1"), (e) => e.code === "bad_arguments" && /sha256 and md5/.test(e.message));
});

test("checksums from the registry: sha256 and md5 parsed, anything else refused rather than read as none", () => {
  assert.equal(parseChecksum(null), null);
  assert.equal(parseChecksum(""), null);
  assert.deepEqual(parseChecksum(`sha256:${"A".repeat(64)}`), { algorithm: "sha256", hex: "a".repeat(64) });
  assert.deepEqual(parseChecksum("md5:cf0cf2afa2ca4d758b68f2e39d466f5d"), { algorithm: "md5", hex: "cf0cf2afa2ca4d758b68f2e39d466f5d" });
  for (const bad of ["sha1:" + "a".repeat(40), "sha256:abc", "md5", "cf0cf2afa2ca4d758b68f2e39d466f5d"]) {
    assert.throws(() => parseChecksum(bad), (e) => e.code === "bad_arguments", bad);
  }
});

test("digestFile streams a Blob (stream() or slice()), several digests in one pass, progress, and stops on abort", async (t) => {
  const bytes = randomBytes(5 * 1024 * 1024 + 17);
  const blob = new Blob([bytes]);
  const seen = [];
  const both = await digestFile(blob, ["sha256", "md5"], { onProgress: (done, total) => seen.push([done, total]) });
  assert.deepEqual(both, { sha256: node("sha256", bytes), md5: node("md5", bytes) });
  assert.deepEqual(seen.at(-1), [bytes.length, bytes.length]);
  assert.ok(seen.every(([d], i) => i === 0 || d >= seen[i - 1][0]), "progress only goes up");

  // a Blob-like without stream(): read by slice() in `chunk`-byte pieces
  const sliceOnly = { size: blob.size, slice: (a, b) => blob.slice(a, b) };
  const pieces = [];
  assert.equal(await digestFile(sliceOnly, "md5", { chunk: 1 << 20, onProgress: (d) => pieces.push(d) }), node("md5", bytes));
  assert.equal(pieces.length, 6);
  assert.equal(await digestFile(new Blob([]), "sha256"), node("sha256", Buffer.alloc(0)));

  const ctl = new AbortController();
  await assert.rejects(digestFile(sliceOnly, "sha256", { chunk: 1 << 20, signal: ctl.signal, onProgress: () => ctl.abort() }), (e) => e.name === "AbortError");
  await assert.rejects(digestFile(blob, "sha256", { signal: AbortSignal.abort() }), (e) => e.name === "AbortError");

  // a rough speed, for the record (pure JavaScript; Web Crypto is faster but has no streaming and no MD5)
  const big = randomBytes(32 * 1024 * 1024);
  for (const alg of ALGORITHMS) {
    const t0 = performance.now();
    hasher(alg).update(big).digestHex();
    const s = (performance.now() - t0) / 1000;
    t.diagnostic(`${alg}: ${(32 / s).toFixed(0)} MB/s on this machine (32 MiB in one update)`);
  }
});
