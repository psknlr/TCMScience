// The WGSL kernel of web/js/accel/gpu.js on the CPU, statement for statement, with WGSL's u32 semantics (every value
// kept in [0, 2^32), logical shifts, countLeadingZeros = Math.clz32). It runs over the very buffers gpu.js uploads
// (batchArrays), so the tests prove the kernel's logic and its host-side layout without a GPU; the e2e test runs the
// real WGSL.

/** The bound buffers of one dispatch, as Uint32Arrays: {params, bins, member, observed, mt, work, atLeast}. */
export function dispatch(buf, invocations) {
  const prm = {
    paths: buf.params[0], bins: buf.params[1], perms: buf.params[2], words: buf.params[3],
    selRow: buf.params[4], logRow: buf.params[5],
  };
  for (let x = 0; x < invocations; x++) main(x, prm, buf);
}

// fn genrand(p: u32, mti: ptr<function, u32>) -> u32
function genrand(p, mti, prm, mt) {
  const P = prm.paths;
  if (mti.v >= 624) {
    for (let kk = 0; kk < 227; kk++) {
      const y = ((mt[kk * P + p] & 0x80000000) | (mt[(kk + 1) * P + p] & 0x7fffffff)) >>> 0;
      mt[kk * P + p] = mt[(kk + 397) * P + p] ^ (y >>> 1) ^ ((y & 1) !== 0 ? 0x9908b0df : 0);
    }
    for (let kk = 227; kk < 623; kk++) {
      const y = ((mt[kk * P + p] & 0x80000000) | (mt[(kk + 1) * P + p] & 0x7fffffff)) >>> 0;
      mt[kk * P + p] = mt[(kk - 227) * P + p] ^ (y >>> 1) ^ ((y & 1) !== 0 ? 0x9908b0df : 0);
    }
    const y = ((mt[623 * P + p] & 0x80000000) | (mt[p] & 0x7fffffff)) >>> 0;
    mt[623 * P + p] = mt[396 * P + p] ^ (y >>> 1) ^ ((y & 1) !== 0 ? 0x9908b0df : 0);
    mti.v = 0;
  }
  let y = mt[mti.v * P + p];
  mti.v = (mti.v + 1) >>> 0;
  y = (y ^ (y >>> 11)) >>> 0;
  y = (y ^ ((y << 7) & 0x9d2c5680)) >>> 0;
  y = (y ^ ((y << 15) & 0xefc60000)) >>> 0;
  y = (y ^ (y >>> 18)) >>> 0;
  return y;
}

// fn randbelow(p: u32, mti: ptr<function, u32>, n: u32) -> u32
function randbelow(p, mti, n, prm, mt) {
  const shift = Math.clz32(n);
  let r = genrand(p, mti, prm, mt) >>> shift;
  for (;;) {
    if (r < n) break;
    r = genrand(p, mti, prm, mt) >>> shift;
  }
  return r;
}

// fn isMember(base: u32, q: u32) -> u32
function isMember(base, q, member) {
  return (member[base + (q >>> 5)] >>> (q & 31)) & 1;
}

// @compute @workgroup_size(64) fn main(@builtin(global_invocation_id) gid: vec3<u32>)
function main(gx, prm, { bins, member, observed, mt, work, atLeast }) {
  const p = gx;
  const P = prm.paths;
  if (p >= P) return;
  const mti = { v: mt[624 * P + p] };
  const need = observed[p];
  const base = p * prm.words;
  let hits = atLeast[p];
  for (let perm = 0; perm < prm.perms; perm++) {
    let overlap = 0;
    for (let b = 0; b < prm.bins; b++) {
      const n = bins[b * 4];
      const k = bins[b * 4 + 1];
      const off = bins[b * 4 + 2];
      if (bins[b * 4 + 3] === 1) {
        for (let i = 0; i < k; i++) {
          const j = randbelow(p, mti, n - i, prm, mt);
          overlap += isMember(base, off + work[j * P + p], member);
          work[j * P + p] = work[(n - i - 1) * P + p];
          work[(prm.logRow + i) * P + p] = j;
        }
        for (let i = 0; i < k; i++) {
          const j = work[(prm.logRow + i) * P + p];
          work[j * P + p] = j;
        }
      } else {
        for (let i = 0; i < k; i++) {
          let j = randbelow(p, mti, n, prm, mt);
          for (;;) {
            if (((work[(prm.selRow + (j >>> 5)) * P + p] >>> (j & 31)) & 1) === 0) break;
            j = randbelow(p, mti, n, prm, mt);
          }
          const cell = (prm.selRow + (j >>> 5)) * P + p;
          work[cell] = work[cell] | (1 << (j & 31));
          overlap += isMember(base, off + j, member);
          work[(prm.logRow + i) * P + p] = j;
        }
        for (let i = 0; i < k; i++) {
          const j = work[(prm.logRow + i) * P + p];
          work[(prm.selRow + (j >>> 5)) * P + p] = 0;
        }
      }
    }
    if (overlap >= need) hits = (hits + 1) >>> 0;
  }
  atLeast[p] = hits;
  mt[624 * P + p] = mti.v;
}
