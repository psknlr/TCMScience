// Incremental SHA-256 and MD5 in plain JavaScript, for files too large to hold in memory at once (the research raw
// files are up to 90 MB each). Web Crypto's digest() takes the whole input in one buffer and has no MD5, which the
// registry needs for LOTUS (its reviewed checksum is md5:…). Both hashers accept bytes in any pieces: the digest of
// a file does not depend on how its chunks were cut. update() copies nothing that is already a whole block; only the
// tail of a chunk (< 64 bytes) waits in a small buffer for the next one.

const enc = new TextEncoder();
const HEX = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, "0"));
const DEFAULT_CHUNK = 4 << 20;

// FIPS 180-4 §4.2.2
const K256 = new Int32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

// RFC 1321 §3.4: the table T[i] = floor(2^32 · |sin(i + 1)|), written out rather than computed (Math.sin is not
// required to be correctly rounded) — and the per-step shift amounts
const KMD5 = new Int32Array([
  0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
  0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
  0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
  0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
  0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
  0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
  0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
  0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
]);
const SMD5 = new Uint8Array([
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
]);

/** The bytes of `input` without a copy (a Uint8Array, another typed array or DataView, an ArrayBuffer), or the UTF-8
 * bytes of a string. Anything else is refused: a number or an object has no one obvious byte form. */
export function asBytes(input) {
  if (input instanceof Uint8Array) return input;
  if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  if (input instanceof ArrayBuffer || (typeof SharedArrayBuffer === "function" && input instanceof SharedArrayBuffer)) return new Uint8Array(input);
  if (typeof input === "string") return enc.encode(input);
  throw new TypeError(`cannot hash a ${input === null ? "null" : typeof input}: give bytes or a string`);
}

/** Shared by both hashers: the 64-byte block buffer, the byte count, and finishing once. */
class BlockHasher {
  #buf = new Uint8Array(64);
  #fill = 0;
  #bytes = 0;
  #hex = null;

  /** Add `data` (bytes or a string); returns this, so calls chain. */
  update(data) {
    if (this.#hex !== null) throw new Error(`${this.algorithm}: update() after digestHex(); start a new hasher`);
    const bytes = asBytes(data);
    const n = bytes.length;
    if (!n) return this;
    this.#bytes += n;
    let off = 0;
    if (this.#fill) {
      const take = Math.min(64 - this.#fill, n);
      this.#buf.set(bytes.subarray(0, take), this.#fill);
      this.#fill += take;
      off = take;
      if (this.#fill < 64) return this;
      this.blocks(this.#buf, 0, 64);
      this.#fill = 0;
    }
    const whole = n - ((n - off) % 64);
    if (whole > off) {
      this.blocks(bytes, off, whole);
      off = whole;
    }
    if (off < n) {
      this.#buf.set(bytes.subarray(off), 0);
      this.#fill = n - off;
    }
    return this;
  }

  /** The digest as lower-case hex. The hasher is finished afterwards; asking again gives the same answer. */
  digestHex() {
    if (this.#hex !== null) return this.#hex;
    const buf = this.#buf;
    // the message length in bits, as two 32-bit halves (exact for any length below 2^53 bytes)
    const lo = (this.#bytes * 8) % 0x100000000;
    const hi = Math.floor(this.#bytes / 0x20000000);
    let fill = this.#fill;
    buf[fill++] = 0x80;
    if (fill > 56) {
      buf.fill(0, fill, 64);
      this.blocks(buf, 0, 64);
      fill = 0;
    }
    buf.fill(0, fill, 56);
    this.lengthWords(buf, hi, lo);
    this.blocks(buf, 0, 64);
    this.#hex = this.hex();
    return this.#hex;
  }

  /** Bytes taken so far. */
  get bytes() {
    return this.#bytes;
  }
}

export class Sha256 extends BlockHasher {
  #h = new Int32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  #w = new Int32Array(64);

  get algorithm() {
    return "sha256";
  }

  blocks(p, off, end) {
    const h = this.#h;
    const w = this.#w;
    let h0 = h[0], h1 = h[1], h2 = h[2], h3 = h[3], h4 = h[4], h5 = h[5], h6 = h[6], h7 = h[7];
    for (; off < end; off += 64) {
      for (let i = 0, j = off; i < 16; i++, j += 4) w[i] = (p[j] << 24) | (p[j + 1] << 16) | (p[j + 2] << 8) | p[j + 3];
      for (let i = 16; i < 64; i++) {
        const x = w[i - 15];
        const y = w[i - 2];
        const s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
        const s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, k = h7;
      for (let i = 0; i < 64; i++) {
        const s1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const t1 = (k + s1 + ((e & f) ^ (~e & g)) + K256[i] + w[i]) | 0;
        const s0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const t2 = (s0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        k = g;
        g = f;
        f = e;
        e = (d + t1) | 0;
        d = c;
        c = b;
        b = a;
        a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + k) | 0;
    }
    h[0] = h0; h[1] = h1; h[2] = h2; h[3] = h3; h[4] = h4; h[5] = h5; h[6] = h6; h[7] = h7;
  }

  lengthWords(buf, hi, lo) {
    // big-endian
    buf[56] = hi >>> 24; buf[57] = hi >>> 16; buf[58] = hi >>> 8; buf[59] = hi;
    buf[60] = lo >>> 24; buf[61] = lo >>> 16; buf[62] = lo >>> 8; buf[63] = lo;
  }

  hex() {
    let s = "";
    for (const v of this.#h) s += HEX[(v >>> 24) & 255] + HEX[(v >>> 16) & 255] + HEX[(v >>> 8) & 255] + HEX[v & 255];
    return s;
  }
}

export class Md5 extends BlockHasher {
  #s = new Int32Array([0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476]);
  #x = new Int32Array(16);

  get algorithm() {
    return "md5";
  }

  blocks(p, off, end) {
    const s = this.#s;
    const x = this.#x;
    let a0 = s[0], b0 = s[1], c0 = s[2], d0 = s[3];
    for (; off < end; off += 64) {
      for (let i = 0, j = off; i < 16; i++, j += 4) x[i] = p[j] | (p[j + 1] << 8) | (p[j + 2] << 16) | (p[j + 3] << 24);
      let a = a0, b = b0, c = c0, d = d0;
      // four rounds of sixteen steps; each round has its own function and message order (RFC 1321 §3.4)
      for (let i = 0; i < 16; i++) {
        const t = (a + ((b & c) | (~b & d)) + KMD5[i] + x[i]) | 0;
        a = d; d = c; c = b;
        b = (b + ((t << SMD5[i]) | (t >>> (32 - SMD5[i])))) | 0;
      }
      for (let i = 16; i < 32; i++) {
        const t = (a + ((b & d) | (c & ~d)) + KMD5[i] + x[(5 * i + 1) & 15]) | 0;
        a = d; d = c; c = b;
        b = (b + ((t << SMD5[i]) | (t >>> (32 - SMD5[i])))) | 0;
      }
      for (let i = 32; i < 48; i++) {
        const t = (a + (b ^ c ^ d) + KMD5[i] + x[(3 * i + 5) & 15]) | 0;
        a = d; d = c; c = b;
        b = (b + ((t << SMD5[i]) | (t >>> (32 - SMD5[i])))) | 0;
      }
      for (let i = 48; i < 64; i++) {
        const t = (a + (c ^ (b | ~d)) + KMD5[i] + x[(7 * i) & 15]) | 0;
        a = d; d = c; c = b;
        b = (b + ((t << SMD5[i]) | (t >>> (32 - SMD5[i])))) | 0;
      }
      a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0;
    }
    s[0] = a0; s[1] = b0; s[2] = c0; s[3] = d0;
  }

  lengthWords(buf, hi, lo) {
    // little-endian
    buf[56] = lo; buf[57] = lo >>> 8; buf[58] = lo >>> 16; buf[59] = lo >>> 24;
    buf[60] = hi; buf[61] = hi >>> 8; buf[62] = hi >>> 16; buf[63] = hi >>> 24;
  }

  hex() {
    let s = "";
    for (const v of this.#s) s += HEX[v & 255] + HEX[(v >>> 8) & 255] + HEX[(v >>> 16) & 255] + HEX[(v >>> 24) & 255];
    return s;
  }
}

export const ALGORITHMS = Object.freeze(["sha256", "md5"]);

/** A new hasher for "sha256" or "md5". Any other name is refused (the registry's checksums use only these two). */
export function hasher(algorithm) {
  const name = String(algorithm || "").toLowerCase();
  if (name === "sha256") return new Sha256();
  if (name === "md5") return new Md5();
  const err = new Error(`no ${algorithm} digest here: the browser computes ${ALGORITHMS.join(" and ")}`);
  err.code = "bad_arguments";
  throw err;
}

/** "sha256:<hex>" → {algorithm, hex} (lower case); null for an empty value. An unknown algorithm or a malformed value
 * throws: a reviewed checksum that cannot be checked must not read as "no checksum". */
export function parseChecksum(value) {
  if (value === null || value === undefined || value === "") return null;
  const m = /^([a-z0-9]+):([0-9a-f]+)$/i.exec(String(value).trim());
  const lengths = { sha256: 64, md5: 32 };
  const algorithm = m?.[1].toLowerCase();
  if (!m || !(algorithm in lengths) || m[2].length !== lengths[algorithm]) {
    const err = new Error(`checksum ${JSON.stringify(value)} is not sha256:<64 hex> or md5:<32 hex>; it cannot be checked here`);
    err.code = "bad_arguments";
    throw err;
  }
  return { algorithm, hex: m[2].toLowerCase() };
}

/**
 * Digest a File or Blob without reading it whole: through file.stream() where it exists, else slice() by `chunk`
 * bytes (4 MiB). `algorithm` is "sha256" | "md5" → hex, or a list of them → {name: hex}, all in one pass.
 * onProgress(bytesDone, total) after every piece; a `signal` that aborts stops between two pieces (AbortError).
 */
export async function digestFile(file, algorithm, { onProgress, signal, chunk = DEFAULT_CHUNK } = {}) {
  const names = Array.isArray(algorithm) ? [...new Set(algorithm.map((a) => String(a).toLowerCase()))] : [algorithm];
  const hashers = names.map((a) => hasher(a));
  const total = Number(file?.size);
  if (!file || !Number.isFinite(total)) throw new TypeError("digestFile needs a File or Blob");
  let done = 0;
  const take = (bytes) => {
    for (const h of hashers) h.update(bytes);
    done += bytes.length;
    onProgress?.(done, total);
  };
  const stop = () => {
    if (signal?.aborted) throw signal.reason instanceof Error ? signal.reason : new DOMException("the digest was cancelled", "AbortError");
  };
  stop();
  if (typeof file.stream === "function") {
    const reader = file.stream().getReader();
    try {
      for (;;) {
        stop();
        const { done: end, value } = await reader.read();
        if (end) break;
        take(value);
      }
    } catch (err) {
      reader.cancel().catch(() => {});
      throw err;
    }
  } else {
    const step = Math.max(64, Math.floor(chunk) || DEFAULT_CHUNK);
    for (let at = 0; at < total; at += step) {
      stop();
      take(new Uint8Array(await file.slice(at, Math.min(total, at + step)).arrayBuffer()));
    }
  }
  if (done !== total) throw new Error(`read ${done} bytes of a ${total}-byte file: it changed while it was read`);
  if (!Array.isArray(algorithm)) return hashers[0].digestHex();
  return Object.fromEntries(names.map((n, i) => [n, hashers[i].digestHex()]));
}
