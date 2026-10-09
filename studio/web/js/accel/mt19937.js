// CPython's random module, the parts the network-pharmacology null uses, ported exactly (docs/V2.md §13.4):
//
//   random.Random(str)    seed: a = s.encode() + sha512(s.encode()).digest(); int.from_bytes(a, "big"); the C seeding
//                         then runs MT19937 init_by_array over that integer's little-endian 32-bit words
//   random.Random(int)    the same with the integer's own words (used by the tests and the synthetic world)
//   getrandbits(k ≤ 32)   genrand_uint32() >> (32 − k)
//   _randbelow(n)         k = n.bit_length(); r = getrandbits(k) until r < n (CPython ≥ 3.2, every version since)
//   sample(population, k) CPython ≥ 3.11: a pool list while n ≤ setsize, else a set of chosen indices
//
// Same seed → same 32-bit outputs → same draws as the Python reference, bit for bit. Pure: no I/O, no DOM; the only
// asynchronous step is SHA-512 (crypto.subtle), done once per stream before any hot loop.

const N = 624;
const M = 397;
const MATRIX_A = 0x9908b0df;
const UPPER = 0x80000000;
const LOWER = 0x7fffffff;

/** The Mersenne Twister of CPython's _randommodule.c (MT19937, 32-bit outputs). */
export class MT19937 {
  constructor() {
    this.mt = new Uint32Array(N);
    this.mti = N + 1;
  }

  initGenrand(s) {
    const mt = this.mt;
    mt[0] = s >>> 0;
    for (let i = 1; i < N; i++) {
      const p = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = (Math.imul(1812433253, p) + i) >>> 0;
    }
    this.mti = N;
  }

  /** init_by_array(key[], key_length): `key` holds the seed's little-endian 32-bit words. */
  initByArray(key) {
    const mt = this.mt;
    const len = key.length;
    this.initGenrand(19650218);
    let i = 1;
    let j = 0;
    for (let k = Math.max(N, len); k; k--) {
      const p = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(p, 1664525)) + key[j] + j) >>> 0;
      i++;
      j++;
      if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
      if (j >= len) j = 0;
    }
    for (let k = N - 1; k; k--) {
      const p = mt[i - 1] ^ (mt[i - 1] >>> 30);
      mt[i] = ((mt[i] ^ Math.imul(p, 1566083941)) - i) >>> 0;
      i++;
      if (i >= N) { mt[0] = mt[N - 1]; i = 1; }
    }
    mt[0] = 0x80000000; // MSB is 1, assuring a non-zero initial array
  }

  /** genrand_uint32(): the next 32-bit output. */
  next() {
    const mt = this.mt;
    if (this.mti >= N) {
      let kk = 0;
      let y;
      for (; kk < N - M; kk++) {
        y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER);
        mt[kk] = mt[kk + M] ^ (y >>> 1) ^ (y & 1 ? MATRIX_A : 0);
      }
      for (; kk < N - 1; kk++) {
        y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER);
        mt[kk] = mt[kk + (M - N)] ^ (y >>> 1) ^ (y & 1 ? MATRIX_A : 0);
      }
      y = (mt[N - 1] & UPPER) | (mt[0] & LOWER);
      mt[N - 1] = mt[M - 1] ^ (y >>> 1) ^ (y & 1 ? MATRIX_A : 0);
      this.mti = 0;
    }
    let y = mt[this.mti++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  /** getrandbits(k) for 1 ≤ k ≤ 32. */
  getrandbits(k) {
    return this.next() >>> (32 - k);
  }

  /**
   * _randbelow_with_getrandbits(n), 1 ≤ n < 2^32: getrandbits(n.bit_length()) until it is below n. The shift
   * 32 − bit_length(n) is clz32(n).
   */
  randbelow(n) {
    const shift = Math.clz32(n);
    let r = this.next() >>> shift;
    while (r >= n) r = this.next() >>> shift;
    return r;
  }

  /** random() (53-bit float), for the tests: (a·2^26 + b) / 2^53 with a = next() >> 5, b = next() >> 6. */
  random() {
    const a = this.next() >>> 5;
    const b = this.next() >>> 6;
    return (a * 67108864 + b) / 9007199254740992;
  }
}

const encoder = new TextEncoder();

/**
 * The init_by_array key CPython derives from random.Random(s) for a str: the UTF-8 bytes followed by their SHA-512,
 * read as one big-endian integer, split into little-endian 32-bit words, without the zero words above its top bit.
 */
export async function strSeedKey(s) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) throw new Error("SHA-512 is not available here (crypto.subtle needs a secure context)");
  const text = encoder.encode(String(s));
  const digest = new Uint8Array(await subtle.digest("SHA-512", text));
  const bytes = new Uint8Array(text.length + digest.length);
  bytes.set(text);
  bytes.set(digest, text.length);
  return bigEndianKey(bytes);
}

/** The key of random.Random(n) for a non-negative safe integer n (CPython seeds an int with abs(n)'s words). */
export function intSeedKey(n) {
  const v = Math.abs(Number(n));
  if (!Number.isSafeInteger(v)) throw new RangeError(`intSeedKey: ${n} is not a safe integer`);
  const lo = v % 0x100000000;
  const hi = Math.floor(v / 0x100000000);
  return hi ? Uint32Array.of(lo, hi) : Uint32Array.of(lo);
}

/** Little-endian 32-bit words of a big-endian unsigned integer; keyused = (bit_length − 1) // 32 + 1, at least 1. */
function bigEndianKey(bytes) {
  let start = 0;
  while (start < bytes.length && bytes[start] === 0) start++;
  const len = bytes.length - start;
  const words = Math.max(1, Math.ceil(len / 4));
  const key = new Uint32Array(words);
  for (let w = 0; w < words; w++) {
    let v = 0;
    for (let b = 0; b < 4; b++) {
      const idx = bytes.length - 1 - (w * 4 + b);
      if (idx >= start) v |= bytes[idx] << (8 * b);
    }
    key[w] = v >>> 0;
  }
  let used = words;
  while (used > 1 && key[used - 1] === 0) used--;
  return key.subarray(0, used);
}

/** A generator seeded from a key (strSeedKey / intSeedKey). */
export function fromKey(key) {
  const g = new MT19937();
  g.initByArray(key);
  return g;
}

/** random.Random(s) for a str s. */
export async function fromString(s) {
  return fromKey(await strSeedKey(s));
}

/** random.Random(n) for a safe integer n. */
export function fromInt(n) {
  return fromKey(intSeedKey(n));
}

/**
 * sample()'s threshold between its two methods: 21, plus 4 ** ceil(log(3k, 4)) when k > 5. CPython computes the
 * exponent with a float logarithm; 3k is never a power of 4 (3 does not divide 4^e), and for k < 2^32 it is at least
 * 1/(2^32·ln 4) away from one in log₄, far more than a rounding error, so the exact integer exponent here (the
 * smallest e with 4^e ≥ 3k) is the one CPython gets.
 */
export function setsize(k) {
  if (k <= 5) return 21;
  let e = 0;
  let p = 1;
  while (p < 3 * k) { p *= 4; e++; }
  return 21 + p;
}

/**
 * random.sample(population, k) as CPython ≥ 3.11 draws it (counts=None): the same calls to _randbelow in the same
 * order, the same result list. The reference the counting kernels follow; it allocates, so it is not one of them.
 */
export function sample(g, population, k) {
  const n = population.length;
  if (!(Number.isInteger(k) && k >= 0 && k <= n)) throw new RangeError("Sample larger than population or is negative");
  const result = new Array(k);
  if (n <= setsize(k)) {
    // an n-length list is smaller than a k-length set; non-selected items stay at pool[0 : n − i]
    const pool = Array.from(population);
    for (let i = 0; i < k; i++) {
      const j = g.randbelow(n - i);
      result[i] = pool[j];
      pool[j] = pool[n - i - 1];
    }
  } else {
    const selected = new Set();
    for (let i = 0; i < k; i++) {
      let j = g.randbelow(n);
      while (selected.has(j)) j = g.randbelow(n);
      selected.add(j);
      result[i] = population[j];
    }
  }
  return result;
}

/** The code whose source text defines the random stream and the draws (host.js digests it). */
export const SOURCES = Object.freeze([MT19937, strSeedKey, bigEndianKey, fromKey, fromString, setsize]);
