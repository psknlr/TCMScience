// Kernel np-null-mt/1 (docs/V2.md §13.4): the degree-matched permutation null of
// bioagent.analysis.network_pharmacology step 4, bit for bit. For each pathway, independently:
//
//   prng = random.Random(f"{seed}:{pathway}")
//   for _ in range(permutations):
//       draw = [x for b, k in sorted(wanted.items()) for x in prng.sample(pool[b], k)]
//       if len(pathway_members.intersection(draw)) >= observed: at_least += 1
//
// Input  {seed, permutations, pools: [[protein index…]…], wanted: [[b, k]…] (sorted), pathways: [{id, members, observed}]}
// Output at_least per pathway (the integers only: empirical p, BH and every float stay in Python).
//
// The pools partition the background (Python builds them that way, and checkInput refuses anything else), so the
// draw has no repeats and |members ∩ draw| is the number of drawn items that are members. The counting loop draws
// exactly what random.sample draws (mt19937.js), but only counts: the pool branch keeps one identity scratch and
// undoes its own writes after each sample instead of copying the pool; the set branch marks chosen indices with a
// per-sample stamp instead of building a set. Nothing is allocated per permutation.

import { fromString, setsize } from "./mt19937.js";

export const KERNEL = "np-null-mt/1";
/** Bounds that keep every count and index a u32 and the work finite. */
export const LIMITS = Object.freeze({ permutations: 10_000_000, protein: 0x7fffffff, poolTotal: 1 << 24, pathways: 1 << 20, idLength: 4096 });

/** A refused input: the message says which field and why (the worker then lets Python compute natively). */
export class KernelInputError extends TypeError {
  constructor(message) {
    super(`${KERNEL}: ${message}`);
    this.name = "KernelInputError";
  }
}

const isIndex = (v, max) => Number.isInteger(v) && v >= 0 && v <= max;
// a lone surrogate cannot be UTF-8 encoded: Python's str.encode() would raise where TextEncoder substitutes
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

/**
 * The input checked and normalised ({seed: string, permutations, pools, wanted, pathways}), or a KernelInputError.
 * The seed is the run's seed as Python formats it in f"{seed}:{pathway}": a string, or a safe integer.
 */
export function checkInput(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new KernelInputError("the input is not an object");
  const { seed, permutations, pools, wanted, pathways } = input;
  let seedText;
  if (typeof seed === "string" && seed.length && seed.length <= LIMITS.idLength && !LONE_SURROGATE.test(seed)) seedText = seed;
  else if (Number.isSafeInteger(seed)) seedText = String(seed);
  else throw new KernelInputError("seed must be a non-empty string or a safe integer");
  if (!isIndex(permutations, LIMITS.permutations)) throw new KernelInputError(`permutations must be an integer from 0 to ${LIMITS.permutations}`);
  if (!Array.isArray(pools)) throw new KernelInputError("pools must be an array of arrays of protein indices");
  const owner = new Map(); // protein index → its pool: the pools must not share a protein
  let total = 0;
  pools.forEach((pool, b) => {
    if (!Array.isArray(pool)) throw new KernelInputError(`pools[${b}] is not an array of protein indices`);
    total += pool.length;
    if (total > LIMITS.poolTotal) throw new KernelInputError(`the pools hold more than ${LIMITS.poolTotal} proteins`);
    for (const x of pool) {
      if (!isIndex(x, LIMITS.protein)) throw new KernelInputError(`pools[${b}] holds ${JSON.stringify(x)}, not a protein index`);
      if (owner.has(x)) {
        const other = owner.get(x);
        throw new KernelInputError(other === b ? `pools[${b}] lists protein ${x} twice` : `protein ${x} is in pools[${other}] and pools[${b}]`);
      }
      owner.set(x, b);
    }
  });
  if (!Array.isArray(wanted)) throw new KernelInputError("wanted must be an array of [pool, k] pairs");
  let last = -1;
  wanted.forEach((pair, i) => {
    if (!Array.isArray(pair) || pair.length !== 2) throw new KernelInputError(`wanted[${i}] is not a [pool, k] pair`);
    const [b, k] = pair;
    if (!isIndex(b, pools.length - 1)) throw new KernelInputError(`wanted[${i}] names pool ${JSON.stringify(b)}, which does not exist`);
    if (b <= last) throw new KernelInputError("wanted must be sorted by pool, each pool once (sorted(wanted.items()))");
    last = b;
    if (!isIndex(k, pools[b].length)) throw new KernelInputError(`wanted[${i}] asks for ${JSON.stringify(k)} of the ${pools[b].length} proteins in pool ${b}`);
  });
  if (!Array.isArray(pathways)) throw new KernelInputError("pathways must be an array of {id, members, observed}");
  if (pathways.length > LIMITS.pathways) throw new KernelInputError(`more than ${LIMITS.pathways} pathways`);
  pathways.forEach((p, i) => {
    if (!p || typeof p !== "object" || Array.isArray(p)) throw new KernelInputError(`pathways[${i}] is not an object`);
    if (typeof p.id !== "string" || !p.id || p.id.length > LIMITS.idLength || LONE_SURROGATE.test(p.id)) {
      throw new KernelInputError(`pathways[${i}].id must be a non-empty string`);
    }
    if (!Array.isArray(p.members)) throw new KernelInputError(`pathways[${i}].members is not an array of protein indices`);
    for (const x of p.members) {
      if (!isIndex(x, LIMITS.protein)) throw new KernelInputError(`pathways[${i}].members holds ${JSON.stringify(x)}, not a protein index`);
    }
    if (!isIndex(p.observed, 0xffffffff)) throw new KernelInputError(`pathways[${i}].observed must be a non-negative integer`);
  });
  return { seed: seedText, permutations, pools, wanted, pathways };
}

/**
 * The plan every engine computes from: the wanted pools laid end to end (the flat pool; only their order and sizes
 * matter, not which proteins they hold), and each pathway's members as positions in it. Members outside the wanted
 * pools can never be drawn and are dropped; a repeated member counts once, as in the Python set. Plain arrays and
 * typed arrays only, so it is cheap to send to a worker. Throws KernelInputError for an input checkInput refuses.
 */
export function prepare(input) {
  const inp = checkInput(input);
  const nb = inp.wanted.length;
  const binN = new Uint32Array(nb);
  const binK = new Uint32Array(nb);
  const binOff = new Uint32Array(nb);
  const binPool = new Uint8Array(nb); // 1: CPython's pool-list branch, 0: its set branch
  const position = new Map();
  let flat = 0;
  inp.wanted.forEach(([b, k], i) => {
    const pool = inp.pools[b];
    binN[i] = pool.length;
    binK[i] = k;
    binOff[i] = flat;
    binPool[i] = pool.length <= setsize(k) ? 1 : 0;
    pool.forEach((x, j) => position.set(x, flat + j));
    flat += pool.length;
  });
  const np = inp.pathways.length;
  const memberStart = new Uint32Array(np + 1);
  const pos = [];
  const seen = new Set();
  inp.pathways.forEach((p, i) => {
    seen.clear();
    for (const x of p.members) {
      const at = position.get(x);
      if (at !== undefined && !seen.has(at)) { seen.add(at); pos.push(at); }
    }
    memberStart[i + 1] = pos.length;
  });
  let maxPoolN = 0;
  let maxSetN = 0;
  let maxK = 0;
  for (let i = 0; i < nb; i++) {
    if (binPool[i]) maxPoolN = Math.max(maxPoolN, binN[i]);
    else maxSetN = Math.max(maxSetN, binN[i]);
    maxK = Math.max(maxK, binK[i]);
  }
  return {
    kernel: KERNEL, seed: inp.seed, permutations: inp.permutations,
    binN, binK, binOff, binPool, flat, maxPoolN, maxSetN, maxK,
    ids: inp.pathways.map((p) => p.id),
    observed: Uint32Array.from(inp.pathways, (p) => p.observed),
    memberStart, memberPos: Uint32Array.from(pos),
  };
}

/** The plan restricted to the pathways at `indices` (in that order): for recomputing a sample of a result. */
export function subPlan(plan, indices) {
  const memberStart = new Uint32Array(indices.length + 1);
  const pos = [];
  indices.forEach((p, i) => {
    for (let m = plan.memberStart[p]; m < plan.memberStart[p + 1]; m++) pos.push(plan.memberPos[m]);
    memberStart[i + 1] = pos.length;
  });
  return {
    ...plan, ids: indices.map((p) => plan.ids[p]), observed: Uint32Array.from(indices, (p) => plan.observed[p]),
    memberStart, memberPos: Uint32Array.from(pos),
  };
}

/** The string Python seeds a pathway's stream with: f"{seed}:{pathway}". */
export function streamName(plan, p) {
  return `${plan.seed}:${plan.ids[p]}`;
}

/** The buffers one thread reuses across pathways: a member mask, the pool scratch, the drawn log, the set stamps. */
export function makeWork(plan) {
  const scratch = new Uint32Array(plan.maxPoolN);
  for (let i = 0; i < scratch.length; i++) scratch[i] = i;
  return {
    mask: new Uint8Array(plan.flat),
    scratch,
    drawn: new Uint32Array(plan.maxK),
    stamp: new Uint32Array(plan.maxSetN),
    epoch: 0,
  };
}

/**
 * at_least for pathway p, drawing from `g` (random.Random(streamName(plan, p)), fresh). Synchronous: the hot loop.
 */
export function countPathway(plan, p, g, work) {
  const { binN, binK, binOff, binPool, memberStart, memberPos } = plan;
  const { mask, scratch, drawn, stamp } = work;
  mask.fill(0);
  for (let i = memberStart[p], end = memberStart[p + 1]; i < end; i++) mask[memberPos[i]] = 1;
  const nb = binN.length;
  const observed = plan.observed[p];
  let atLeast = 0;
  for (let perm = 0; perm < plan.permutations; perm++) {
    let overlap = 0;
    for (let b = 0; b < nb; b++) {
      const n = binN[b];
      const k = binK[b];
      const off = binOff[b];
      if (binPool[b]) {
        // pool = list(population); j = randbelow(n − i); take pool[j]; pool[j] = pool[n − i − 1]
        for (let i = 0; i < k; i++) {
          const j = g.randbelow(n - i);
          overlap += mask[off + scratch[j]];
          scratch[j] = scratch[n - i - 1];
          drawn[i] = j;
        }
        for (let i = 0; i < k; i++) scratch[drawn[i]] = drawn[i]; // only the drawn slots were written
      } else {
        // selected = set(); j = randbelow(n) until j is new; take population[j]
        if (work.epoch === 0xffffffff) { stamp.fill(0); work.epoch = 0; }
        const e = ++work.epoch;
        for (let i = 0; i < k; i++) {
          let j = g.randbelow(n);
          while (stamp[j] === e) j = g.randbelow(n);
          stamp[j] = e;
          overlap += mask[off + j];
        }
      }
    }
    if (overlap >= observed) atLeast++;
  }
  return atLeast;
}

/**
 * at_least for pathways [from, to), in order. Seeds each stream (SHA-512, asynchronous) before its hot loop.
 * `shouldStop()` is asked between pathways; `onPathway(p, count)` is told each result.
 */
export async function countRange(plan, from = 0, to = plan.ids.length, { work = makeWork(plan), shouldStop, onPathway } = {}) {
  const out = new Array(Math.max(0, to - from));
  for (let p = from; p < to; p++) {
    if (shouldStop?.()) break;
    const g = await fromString(streamName(plan, p));
    out[p - from] = countPathway(plan, p, g, work);
    onPathway?.(p, out[p - from]);
  }
  return out;
}

/** The kernel on this thread, start to finish: the reference the other engines are compared with. */
export async function nullCounts(input, opts) {
  const plan = prepare(input);
  return countRange(plan, 0, plan.ids.length, opts);
}

/** The functions whose source text defines this kernel's numbers on the CPU (host.js digests them). */
export const SOURCES = Object.freeze([checkInput, prepare, subPlan, streamName, makeWork, countPathway, countRange]);
