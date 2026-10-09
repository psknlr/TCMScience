// The WebGPU engine of np-null-mt/1 (docs/V2.md §13.4): the same draws as npnull.js, on the GPU, u32 only.
//
// One invocation per pathway runs that pathway's own MT19937 stream, so the result does not depend on how the
// pathways are split. Everything an invocation keeps between draws lives in storage buffers, interleaved by pathway
// (word w of pathway p at w·paths + p, so neighbouring invocations touch neighbouring words):
//
//   mt       624 state words + mti, seeded on the host (SHA-512 → init_by_array, as CPython does)
//   work     the pool branch's scratch (the identity between samples), the set branch's selection bits (zero between
//            samples) and the log of the indices drawn in the current sample, which restores both in O(k)
//   member   each pathway's members as a bitset over the flat pool (the wanted pools end to end)
//   atLeast  the count, summed over passes
//
// Permutations advance in passes of at most 100 per dispatch, one submit each; the state persists between passes,
// so the pass size never changes the numbers. The first pass is short and times the device; later passes are sized
// to take about a second, so no submit runs long enough to trip a GPU watchdog on a slow phone. Pathways that do not
// fit one binding (never assume more than 128 MiB) run in batches. Only the u32 counts are read back. The host
// (host.js) recomputes two pathways in JS before it trusts a GPU result.

import { abortError } from "../core/util.js";
import { fromString } from "./mt19937.js";
import { streamName } from "./npnull.js";

/** At most this many permutations per dispatch. */
export const PASS_PERMUTATIONS = 100;
/** The first pass of a run: short, to time the device before a longer submit. */
export const FIRST_PASS_PERMUTATIONS = 10;
/** Later passes are sized to take about this long. */
export const PASS_TARGET_MS = 1000;
/** Never assume a storage binding larger than this, whatever the adapter says (the WebGPU default limit). */
export const MAX_BINDING_BYTES = 128 * 1024 * 1024;
/** All of one batch's buffers together stay below this. */
export const MAX_BATCH_BYTES = 256 * 1024 * 1024;
const WORKGROUP = 64;
const STATE_ROWS = 625; // 624 words of MT19937 state, then mti
const SOFTWARE = /swiftshader|llvmpipe|lavapipe|software|microsoft basic render/i;

/** The kernel, a transliteration of countPathway (npnull.js) with the state in storage buffers. */
export const WGSL = /* wgsl */ `// np-null-mt/1: CPython random.Random(f"{seed}:{pathway}") + random.sample, one invocation per pathway.
struct Params {
  paths: u32,   // pathways in this batch: the row stride of mt and work
  bins: u32,    // wanted bins, in sorted(wanted.items()) order
  perms: u32,   // permutations in this pass
  words: u32,   // member-bitset words per pathway
  selRow: u32,  // first work row of the set branch's selection bits
  logRow: u32,  // first work row of the drawn-index log
  pad0: u32,
  pad1: u32,
};
@group(0) @binding(0) var<uniform> prm: Params;
@group(0) @binding(1) var<storage, read> bins: array<vec4<u32>>;     // n, k, offset in the flat pool, 1 = pool branch
@group(0) @binding(2) var<storage, read> member: array<u32>;         // [p * words + w]: the pathway's members
@group(0) @binding(3) var<storage, read> observed: array<u32>;       // [p]
@group(0) @binding(4) var<storage, read_write> mt: array<u32>;       // [w * paths + p]: state words 0..623, mti at 624
@group(0) @binding(5) var<storage, read_write> work: array<u32>;     // [row * paths + p]
@group(0) @binding(6) var<storage, read_write> atLeast: array<u32>;  // [p]

// genrand_uint32() of CPython's _randommodule.c
fn genrand(p: u32, mti: ptr<function, u32>) -> u32 {
  let P = prm.paths;
  if (*mti >= 624u) {
    for (var kk = 0u; kk < 227u; kk++) {
      let y = (mt[kk * P + p] & 0x80000000u) | (mt[(kk + 1u) * P + p] & 0x7fffffffu);
      mt[kk * P + p] = mt[(kk + 397u) * P + p] ^ (y >> 1u) ^ select(0u, 0x9908b0dfu, (y & 1u) != 0u);
    }
    for (var kk = 227u; kk < 623u; kk++) {
      let y = (mt[kk * P + p] & 0x80000000u) | (mt[(kk + 1u) * P + p] & 0x7fffffffu);
      mt[kk * P + p] = mt[(kk - 227u) * P + p] ^ (y >> 1u) ^ select(0u, 0x9908b0dfu, (y & 1u) != 0u);
    }
    let y = (mt[623u * P + p] & 0x80000000u) | (mt[p] & 0x7fffffffu);
    mt[623u * P + p] = mt[396u * P + p] ^ (y >> 1u) ^ select(0u, 0x9908b0dfu, (y & 1u) != 0u);
    *mti = 0u;
  }
  var y = mt[*mti * P + p];
  *mti = *mti + 1u;
  y ^= y >> 11u;
  y ^= (y << 7u) & 0x9d2c5680u;
  y ^= (y << 15u) & 0xefc60000u;
  y ^= y >> 18u;
  return y;
}

// _randbelow(n), n >= 1: getrandbits(n.bit_length()) until below n; 32 - bit_length(n) = countLeadingZeros(n)
fn randbelow(p: u32, mti: ptr<function, u32>, n: u32) -> u32 {
  let shift = countLeadingZeros(n);
  var r = genrand(p, mti) >> shift;
  loop {
    if (r < n) { break; }
    r = genrand(p, mti) >> shift;
  }
  return r;
}

fn isMember(base: u32, q: u32) -> u32 {
  return (member[base + (q >> 5u)] >> (q & 31u)) & 1u;
}

@compute @workgroup_size(${WORKGROUP})
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let p = gid.x;
  let P = prm.paths;
  if (p >= P) { return; }
  var mti = mt[624u * P + p];
  let need = observed[p];
  let base = p * prm.words;
  var hits = atLeast[p];
  for (var perm = 0u; perm < prm.perms; perm++) {
    var overlap = 0u;
    for (var b = 0u; b < prm.bins; b++) {
      let bin = bins[b];
      let n = bin.x;
      let k = bin.y;
      let off = bin.z;
      if (bin.w == 1u) {
        // pool = list(population); j = randbelow(n - i); take pool[j]; pool[j] = pool[n - i - 1]
        for (var i = 0u; i < k; i++) {
          let j = randbelow(p, &mti, n - i);
          overlap += isMember(base, off + work[j * P + p]);
          work[j * P + p] = work[(n - i - 1u) * P + p];
          work[(prm.logRow + i) * P + p] = j;
        }
        for (var i = 0u; i < k; i++) {
          let j = work[(prm.logRow + i) * P + p];
          work[j * P + p] = j;
        }
      } else {
        // selected = set(); j = randbelow(n) until j is new; take population[j]
        for (var i = 0u; i < k; i++) {
          var j = randbelow(p, &mti, n);
          loop {
            if (((work[(prm.selRow + (j >> 5u)) * P + p] >> (j & 31u)) & 1u) == 0u) { break; }
            j = randbelow(p, &mti, n);
          }
          let cell = (prm.selRow + (j >> 5u)) * P + p;
          work[cell] = work[cell] | (1u << (j & 31u));
          overlap += isMember(base, off + j);
          work[(prm.logRow + i) * P + p] = j;
        }
        for (var i = 0u; i < k; i++) {
          let j = work[(prm.logRow + i) * P + p];
          work[(prm.selRow + (j >> 5u)) * P + p] = 0u;
        }
      }
    }
    if (overlap >= need) { hits += 1u; }
  }
  atLeast[p] = hits;
  mt[624u * P + p] = mti;
}
`;

// ------------------------------------------------------------------------------------------------------ layout

/** Rows of the work buffer and words of the member bitset, from the plan (npnull.prepare). */
export function gpuLayout(plan) {
  const poolRows = plan.maxPoolN;
  const selRows = Math.ceil(plan.maxSetN / 32);
  const logRows = plan.maxK;
  return {
    words: Math.max(1, Math.ceil(plan.flat / 32)),
    selRow: poolRows,
    logRow: poolRows + selRows,
    workRows: Math.max(1, poolRows + selRows + logRows),
    stateRows: STATE_ROWS,
  };
}

/**
 * Pathways per batch: every per-pathway buffer within one binding (the device's limits, never above 128 MiB), the
 * batch within MAX_BATCH_BYTES and the dispatch within maxComputeWorkgroupsPerDimension.
 */
export function batchSize(plan, layout, limits = {}, maxBindingBytes = MAX_BINDING_BYTES) {
  const binding = Math.min(MAX_BINDING_BYTES, num(maxBindingBytes, MAX_BINDING_BYTES), num(limits.maxStorageBufferBindingSize, MAX_BINDING_BYTES), num(limits.maxBufferSize, MAX_BINDING_BYTES));
  const rows = Math.max(layout.stateRows, layout.workRows, layout.words);
  const perPathway = 4 * (layout.stateRows + layout.workRows + layout.words + 2);
  const groups = num(limits.maxComputeWorkgroupsPerDimension, 65535);
  const n = Math.min(Math.floor(binding / (4 * rows)), Math.floor(MAX_BATCH_BYTES / perPathway), groups * WORKGROUP);
  if (n < 1) throw new Error(`np-null-mt/1: one pathway needs ${4 * rows} bytes in one binding; this GPU allows ${binding}`);
  return Math.max(1, Math.min(n, plan.ids.length));
}

function num(v, dflt) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : dflt;
}

/**
 * The host-side contents of one batch's buffers (pathways [from, to)): what the GPU starts from. The MT states are
 * seeded here, one SHA-512 per pathway. Also what the CPU emulation of the kernel in the tests runs on.
 */
export async function batchArrays(plan, layout, from, to) {
  const B = to - from;
  const nb = plan.binN.length;
  const bins = new Uint32Array(Math.max(4, nb * 4));
  for (let b = 0; b < nb; b++) bins.set([plan.binN[b], plan.binK[b], plan.binOff[b], plan.binPool[b]], b * 4);
  const member = new Uint32Array(B * layout.words);
  for (let i = 0; i < B; i++) {
    const p = from + i;
    for (let m = plan.memberStart[p]; m < plan.memberStart[p + 1]; m++) {
      const q = plan.memberPos[m];
      member[i * layout.words + (q >>> 5)] |= 1 << (q & 31);
    }
  }
  const observed = plan.observed.slice(from, to);
  const mt = new Uint32Array(layout.stateRows * B);
  for (let i = 0; i < B; i++) {
    const g = await fromString(streamName(plan, from + i));
    for (let w = 0; w < 624; w++) mt[w * B + i] = g.mt[w];
    mt[624 * B + i] = g.mti;
  }
  const work = new Uint32Array(layout.workRows * B);
  for (let r = 0; r < layout.selRow; r++) work.fill(r, r * B, (r + 1) * B); // the pool scratch starts as the identity
  const params = Uint32Array.of(B, nb, 0, layout.words, layout.selRow, layout.logRow, 0, 0);
  return { B, params, bins, member, observed, mt, work, atLeast: new Uint32Array(B) };
}

// ---------------------------------------------------------------------------------------------------- the device

/**
 * A WebGPU device for the kernel, or {ok: false, reason}. A software adapter (SwiftShader, llvmpipe, a fallback
 * adapter) emulates a GPU on the CPU: it is refused unless `allowSoftware` (the tests ask for it explicitly).
 */
export async function openGpu({ allowSoftware = false, gpu = globalThis.navigator?.gpu } = {}) {
  if (!gpu?.requestAdapter) return { ok: false, reason: "this browser has no WebGPU" };
  let adapter;
  try {
    adapter = await gpu.requestAdapter({ powerPreference: "high-performance" });
  } catch (err) {
    return { ok: false, reason: `requestAdapter failed: ${err?.message || err}` };
  }
  if (!adapter) return { ok: false, reason: "no WebGPU adapter" };
  let info = adapter.info;
  if (!info && adapter.requestAdapterInfo) {
    try { info = await adapter.requestAdapterInfo(); } catch { info = null; }
  }
  info = info || {};
  const named = [info.vendor, info.architecture, info.description].filter(Boolean).join(" ");
  const software = Boolean(adapter.isFallbackAdapter ?? info.isFallbackAdapter) || SOFTWARE.test(named);
  const about = { vendor: info.vendor || "", architecture: info.architecture || "", description: info.description || "", software };
  if (software && !allowSoftware) return { ok: false, software: true, info: about, reason: `only a software adapter (${named || "fallback"})` };
  let device;
  try {
    device = await adapter.requestDevice();
  } catch (err) {
    return { ok: false, info: about, reason: `requestDevice failed: ${err?.message || err}` };
  }
  const ctx = { ok: true, device, info: about, label: `${software ? "gpu (software)" : "gpu"}: ${named || "webgpu"}`, lost: null, pipeline: null };
  // rejects when the device is lost, so a wait on the GPU ends then instead of never
  ctx.lostP = device.lost?.then
    ? device.lost.then((why) => {
      ctx.lost = why?.message || why?.reason || "device lost";
      throw new Error(`the GPU device was lost: ${ctx.lost}`);
    })
    : new Promise(() => {});
  ctx.lostP.catch(() => {});
  return ctx;
}

async function pipelineFor(ctx) {
  if (!ctx.pipeline) {
    ctx.pipeline = (async () => {
      const module = ctx.device.createShaderModule({ code: WGSL, label: "np-null-mt/1" });
      const info = await module.getCompilationInfo?.();
      const errors = (info?.messages || []).filter((m) => m.type === "error");
      if (errors.length) throw new Error(`np-null-mt/1 WGSL: ${errors.map((m) => `${m.lineNum}:${m.linePos} ${m.message}`).join("; ")}`);
      return ctx.device.createComputePipelineAsync({ layout: "auto", compute: { module, entryPoint: "main" }, label: "np-null-mt/1" });
    })();
    ctx.pipeline.catch(() => { ctx.pipeline = null; });
  }
  return ctx.pipeline;
}

const now = () => (globalThis.performance?.now ? performance.now() : Date.now());

/**
 * at_least for every pathway of the plan on the GPU (`ctx` from openGpu). Rejects with an AbortError when `signal`
 * aborts (between passes: a pass already submitted finishes on the GPU), or an Error when the device fails.
 * `maxBindingBytes` lowers the binding size a batch may use (the tests force several batches with it).
 */
export async function runWebGPU(plan, ctx, { signal, onProgress, passTargetMs = PASS_TARGET_MS, maxBindingBytes = MAX_BINDING_BYTES } = {}) {
  const n = plan.ids.length;
  const perms = plan.permutations;
  const counts = new Array(n);
  if (!n) return counts;
  if (ctx.lost) throw new Error(`the GPU device was lost: ${ctx.lost}`);
  const { device } = ctx;
  const pipeline = await pipelineFor(ctx);
  const layout = gpuLayout(plan);
  const size = batchSize(plan, layout, device.limits, maxBindingBytes);
  const G = globalThis.GPUBufferUsage;
  let step = FIRST_PASS_PERMUTATIONS;
  const total = n * Math.max(1, perms);
  for (let from = 0; from < n; from += size) {
    if (signal?.aborted) throw abortError();
    const to = Math.min(n, from + size);
    const host = await batchArrays(plan, layout, from, to);
    const made = [];
    const buffer = (data, usage) => {
      const b = device.createBuffer({ size: Math.max(16, data.byteLength), usage: usage | G.COPY_DST });
      made.push(b);
      device.queue.writeBuffer(b, 0, data);
      return b;
    };
    try {
      const created = scoped(device, () => {
        const uniform = buffer(host.params, G.UNIFORM);
        const bufs = [
          uniform,
          buffer(host.bins, G.STORAGE), buffer(host.member, G.STORAGE), buffer(host.observed, G.STORAGE),
          buffer(host.mt, G.STORAGE), buffer(host.work, G.STORAGE), buffer(host.atLeast, G.STORAGE | G.COPY_SRC),
        ];
        const bindGroup = device.createBindGroup({
          layout: pipeline.getBindGroupLayout(0),
          entries: bufs.map((b, i) => ({ binding: i, resource: { buffer: b } })),
        });
        const readback = device.createBuffer({ size: Math.max(16, host.atLeast.byteLength), usage: G.MAP_READ | G.COPY_DST });
        made.push(readback);
        return { uniform, bufs, bindGroup, readback };
      });
      await created.scope;
      const { uniform, bufs, bindGroup, readback } = created;
      let done = 0;
      while (done < perms) {
        if (signal?.aborted) throw abortError();
        if (ctx.lost) throw new Error(`the GPU device was lost: ${ctx.lost}`);
        const count = Math.min(step, perms - done);
        host.params[2] = count;
        const t0 = now();
        const submitted = scoped(device, () => {
          device.queue.writeBuffer(uniform, 0, host.params);
          const enc = device.createCommandEncoder();
          const pass = enc.beginComputePass();
          pass.setPipeline(pipeline);
          pass.setBindGroup(0, bindGroup);
          pass.dispatchWorkgroups(Math.ceil(host.B / WORKGROUP));
          pass.end();
          device.queue.submit([enc.finish()]);
          return {};
        });
        await settle(device.queue.onSubmittedWorkDone(), signal, ctx);
        await submitted.scope;
        // size the next pass to about passTargetMs at this batch's measured rate
        const ms = Math.max(1, now() - t0);
        step = Math.max(1, Math.min(PASS_PERMUTATIONS, Math.floor((count * passTargetMs) / ms)));
        done += count;
        onProgress?.((from * perms + (to - from) * done) / total);
      }
      const copied = scoped(device, () => {
        const enc = device.createCommandEncoder();
        enc.copyBufferToBuffer(bufs[6], 0, readback, 0, host.atLeast.byteLength);
        device.queue.submit([enc.finish()]);
        return {};
      });
      await settle(readback.mapAsync(globalThis.GPUMapMode.READ), signal, ctx);
      await copied.scope;
      const got = new Uint32Array(readback.getMappedRange()).slice(0, host.B);
      readback.unmap();
      for (let i = 0; i < host.B; i++) counts[from + i] = got[i];
    } finally {
      for (const b of made) { try { b.destroy(); } catch { /* already gone */ } }
    }
    if (!perms) onProgress?.(to / n);
  }
  return counts;
}

/**
 * Run the synchronous GPU calls of `fn` inside a validation and an out-of-memory error scope, popped at once (so an
 * exception never leaves a scope pushed). The result's `scope` promise rejects with the first error the scopes caught.
 */
function scoped(device, fn) {
  device.pushErrorScope("out-of-memory");
  device.pushErrorScope("validation");
  let out;
  try {
    out = fn();
  } finally {
    const validation = device.popErrorScope();
    const memory = device.popErrorScope();
    const scope = Promise.all([validation, memory]).then(([v, m]) => {
      const err = v || m;
      if (err) throw new Error(`WebGPU: ${err.message}`);
    });
    scope.catch(() => {});
    if (out) out.scope = scope;
  }
  return out;
}

/** A GPU promise, or an AbortError as soon as `signal` aborts, or an Error when the device is lost meanwhile. */
function settle(promise, signal, ctx) {
  const races = [promise, ctx.lostP];
  let onAbort = null;
  if (signal) {
    races.push(new Promise((_, reject) => {
      onAbort = () => reject(abortError());
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }));
  }
  return Promise.race(races).finally(() => { if (onAbort) signal.removeEventListener("abort", onAbort); });
}

/** The host code whose source text, with WGSL, defines what the GPU computes (host.js digests it). */
export const SOURCES = Object.freeze([WGSL, gpuLayout, batchArrays, runWebGPU]);
