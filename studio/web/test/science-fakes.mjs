// A WebGPU double for the Tanimoto kernel (compute/science-gpu.js), shared by the science-*.test.mjs files: it runs
// the recorded commands on submit, with the WGSL's semantics computed on the CPU from the buffers the passes were
// bound to (the partial pass per workgroup, with its chunk and query-block offsets and its atomics' outcome; the reduce
// pass per query), buffer copies and timestamp resolves.
//
//   info              the adapter's GPUAdapterInfo (default a physical one)
//   adapterLimits     what the adapter offers (default 1 GiB storage binding and buffer, 65535 workgroups)
//   corrupt           {from, kind}: reduce passes after the first `from` come back wrong: "arg" another reference with
//                     that reference's own counts and key (only a CPU check of the search finds it), "counts" an
//                     intersection one too small (the device-side check finds it: its key no longer fits)
//   delayMs           every wait on the queue takes this long (state.delayMs can be changed between calls)
//   lost              the first readback loses the device
//   fail              shader validation fails
//   errorFromPop      every popErrorScope() from this one on (counting from 1) reports an error: one raised by the
//                     commands of a search rather than by its shaders
//   timestamps        the adapter offers timestamp-query; each pass then takes 2.5 ms of GPU time
// state: dispatches [{entry, x, y, c0, qb0}], created and destroyed buffers, deviceRequests, adapters.

const pop32 = (x) => {
  x -= (x >>> 1) & 0x55555555;
  x = (x & 0x33333333) + ((x >>> 2) & 0x33333333);
  x = (x + (x >>> 4)) & 0x0f0f0f0f;
  return Math.imul(x, 0x01010101) >>> 24;
};

function key(c, u) {
  if (u === 0) return 0;
  const hi = Math.floor((c << 11) / u), rem = (c << 11) % u;
  return ((hi << 11) | Math.floor((rem << 11) / u)) >>> 0;
}

function counts(qfp, q, rfp, r) {
  let a = 0, b = 0, c = 0;
  for (let w = 0; w < 64; w++) {
    const x = qfp[q * 64 + w] ?? 0, y = rfp[r * 64 + w] ?? 0;
    a += pop32(x); b += pop32(y); c += pop32(x & y);
  }
  return { a, b, c };
}

function partial(views, x, y) {
  const [qfp, rfp, part, p] = views;
  const [Q, R, chunks, c0, qb0] = p;
  for (let wy = 0; wy < y; wy++) for (let wx = 0; wx < x; wx++) {
    const chunk = wx + c0, qbase = (wy + qb0) * 16;
    for (let q = 0; q < 16 && qbase + q < Q; q++) {
      let best = 0, idx = 0xffffffff;
      const keys = [];
      for (let t = 0; t < 256; t++) {
        const r = chunk * 256 + t;
        if (r >= R) break;
        const { a, b, c } = counts(qfp, qbase + q, rfp, r);
        const k = key(c, a + b - c);
        keys.push([r, k]);
        best = Math.max(best, k);
      }
      for (const [r, k] of keys) if (k === best && r < idx) idx = r;
      part[2 * ((qbase + q) * chunks + chunk)] = best;
      part[2 * ((qbase + q) * chunks + chunk) + 1] = idx;
    }
  }
}

function reduce(views, x, wrong) {
  const [qfp, rfp, part, p, best] = views;
  const [Q, R, chunks] = p;
  for (let q = 0; q < Math.min(Q, x * 64); q++) {
    let bk = 0, bi = 0xffffffff;
    for (let ch = 0; ch < chunks; ch++) {
      const k = part[2 * (q * chunks + ch)], i = part[2 * (q * chunks + ch) + 1];
      if (bi === 0xffffffff || k > bk) { bk = k; bi = i; }
    }
    let { a, b, c } = counts(qfp, q, rfp, bi);
    if (wrong === "arg" && q === 0 && R > 1) {
      bi = (bi + 1) % R;
      ({ a, b, c } = counts(qfp, q, rfp, bi));
      bk = key(c, a + b - c);
    }
    if (wrong === "counts" && q === 0) c = Math.max(0, c - 1);
    best.set([bi, c, a + b - c, bk], 4 * q);
  }
}

export function fakeScienceGPU({
  info = { vendor: "nvidia", architecture: "ampere" }, adapterLimits = {}, corrupt = null, delayMs = 0, lost = false, fail = false, timestamps = false,
  errorFromPop = Infinity,
} = {}) {
  let resolveLoss;
  const state = { dispatches: [], created: [], destroyed: [], deviceRequests: [], adapters: 0, reduces: 0, delayMs, waits: 0, pops: 0, errorFromPop };
  const offered = { maxStorageBufferBindingSize: 1 << 30, maxBufferSize: 1 << 30, maxComputeWorkgroupsPerDimension: 65535, ...adapterLimits };
  const wait = async () => { state.waits++; if (state.delayMs) await new Promise((r) => setTimeout(r, state.delayMs)); };
  let device = null;
  const makeDevice = (descriptor = {}) => {
    const req = descriptor.requiredLimits || {};
    for (const [k, v] of Object.entries(req)) if (v > offered[k]) throw new Error(`OperationError: ${k} ${v} is beyond the adapter's ${offered[k]}`);
    const granted = {
      // the specification's defaults, raised to what was asked for
      maxStorageBufferBindingSize: Math.max(134217728, req.maxStorageBufferBindingSize ?? 0),
      maxBufferSize: Math.max(268435456, req.maxBufferSize ?? 0),
      maxComputeWorkgroupsPerDimension: offered.maxComputeWorkgroupsPerDimension,
    };
    // a test may offer less than the defaults: the device then has what the adapter has
    for (const k of ["maxStorageBufferBindingSize", "maxBufferSize"]) granted[k] = Math.min(granted[k], offered[k]);
    return {
      features: new Set((descriptor.requiredFeatures || []).filter((f) => f === "timestamp-query" && timestamps)),
      limits: granted,
      lost: new Promise((resolve) => { resolveLoss = resolve; }),
      pushErrorScope() {},
      async popErrorScope() {
        state.pops++;
        if (fail) return { message: "shader validation failed" };
        return state.pops >= state.errorFromPop ? { message: "Binding size is larger than the maximum binding size" } : null;
      },
      createShaderModule({ code }) { return { code }; },
      async createComputePipelineAsync({ compute }) {
        const entry = compute.entryPoint;
        if (!new RegExp(`fn ${entry}\\(`).test(compute.module.code)) throw new Error(`no entry point ${entry}`);
        return { entry, getBindGroupLayout() { return { entry }; } };
      },
      createBuffer({ size, usage }) {
        if (size > granted.maxBufferSize) throw new Error(`buffer of ${size} bytes is beyond maxBufferSize`);
        const buffer = {
          data: new ArrayBuffer(size), size, usage, destroy() { state.destroyed.push(this); }, unmap() {},
          async mapAsync() { await wait(); if (lost) { resolveLoss({ message: "GPU disconnected" }); throw new Error("lost"); } },
          getMappedRange(offset = 0, length) { return this.data.slice(offset, length === undefined ? undefined : offset + length); },
        };
        state.created.push(buffer);
        return buffer;
      },
      createQuerySet({ type, count }) { return { type, count, destroy() {} }; },
      createBindGroup({ layout, entries }) {
        for (const e of entries) if (e.resource.buffer.size > granted.maxStorageBufferBindingSize) throw new Error(`binding ${e.binding} is beyond maxStorageBufferBindingSize`);
        return { entry: layout.entry, entries };
      },
      createCommandEncoder() {
        const commands = [];
        return {
          beginComputePass(descriptor) {
            const pass = { op: "dispatch", descriptor };
            return { setPipeline(p) { pass.entry = p.entry; }, setBindGroup(i, group) { pass.group = group; }, dispatchWorkgroups(x, y = 1) { pass.x = x; pass.y = y; }, end() { commands.push(pass); } };
          },
          copyBufferToBuffer(source, sourceOffset, target, targetOffset, size) { commands.push({ op: "copy", source, sourceOffset, target, targetOffset, size }); },
          resolveQuerySet(querySet, first, count, target, offset) { commands.push({ op: "resolve", target, offset }); },
          finish() { return { commands }; },
        };
      },
      queue: {
        writeBuffer(buffer, offset, array) { new Uint8Array(buffer.data).set(new Uint8Array(array.buffer, array.byteOffset, array.byteLength), offset); },
        submit([commandBuffer]) {
          for (const c of commandBuffer.commands) {
            if (c.op === "dispatch") {
              const views = c.group.entries.map((e) => new Uint32Array(e.resource.buffer.data));
              const max = granted.maxComputeWorkgroupsPerDimension;
              if (c.x > max || c.y > max) throw new Error(`dispatch ${c.x} × ${c.y} is beyond maxComputeWorkgroupsPerDimension`);
              state.dispatches.push({ entry: c.entry, x: c.x, y: c.y, c0: views[3][3], qb0: views[3][4] });
              if (c.entry === "partial") partial(views, c.x, c.y);
              else {
                state.reduces++;
                reduce(views, c.x, corrupt && state.reduces > corrupt.from ? corrupt.kind : null);
              }
            } else if (c.op === "copy") {
              new Uint8Array(c.target.data, c.targetOffset, c.size).set(new Uint8Array(c.source.data, c.sourceOffset, c.size));
            } else if (c.op === "resolve") new BigUint64Array(c.target.data, c.offset, 2).set([1_000_000n, 3_500_000n]);
          }
        },
        onSubmittedWorkDone() { return wait(); },
      },
      destroy() { resolveLoss({ reason: "destroyed" }); },
    };
  };
  const adapter = {
    info, limits: offered, features: new Set(timestamps ? ["timestamp-query"] : []),
    async requestDevice(descriptor) { state.deviceRequests.push(descriptor); device = makeDevice(descriptor); return device; },
  };
  return {
    navigator: { gpu: { async requestAdapter() { state.adapters++; return adapter; } }, userAgent: "TestBrowser/1.0", userAgentData: { brands: [{ brand: "Test", version: "1" }], platform: "Test", mobile: false } },
    state, get device() { return device; },
  };
}

/**
 * A device check report as compute/device-check.js writes one, for the adapter `info` and the browser of `navigator`,
 * with the given sizes ({queries, refs, accelerated, gpu_ms, cpu_ms}): what the scheduler's tests choose with, without
 * timing a double. `kind` overrides the adapter's class (to forge one), knownAnswer its test, versions the kernels' digests
 * it measured (default this build's).
 */
export async function calibrationFor({ navigator, info = { vendor: "nvidia", architecture: "ampere" }, sizes = [], kind = null, knownAnswer = { ok: true, ms: 1 }, id = "c".repeat(64), versions = {} } = {}) {
  const { classifyAdapter } = await import("../js/runtime/webgpu.js");
  const { DEVICE_CHECK_SCHEMA, deviceFingerprint } = await import("../js/compute/calibration.js");
  const { scienceKernelVersions } = await import("../js/compute/science-scheduler.js");
  const adapter = { ...classifyAdapter(info), ...(kind ? { kind } : {}) };
  const fingerprint = await deviceFingerprint({ adapter: classifyAdapter(info), navigator });
  return {
    schema: DEVICE_CHECK_SCHEMA, id, measured_at: "2026-10-19T12:00:00.000Z", ok: true, verdict: "test", software_allowed: false,
    device: { fingerprint, adapter, limits: null, gpu_error: null }, versions: { ...(await scienceKernelVersions()), ...versions },
    kernels: {
      "tanimoto.best": {
        kernel: "tanimoto.best/1", known_answer: knownAnswer,
        sizes: sizes.map((s) => ({
          queries: s.queries, refs: s.refs, words: 64, pairs: s.queries * s.refs, exact: { "wasm-simd": true, webgpu: true },
          timings: { js: { ms: 10 * s.cpu_ms }, "wasm-simd": { ms: s.cpu_ms }, webgpu: { ms: s.gpu_ms } }, cpu_baseline: "wasm-simd",
          accelerated: s.accelerated, reason: s.reason ?? "test",
        })),
      },
    },
  };
}
