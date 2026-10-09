// A stand-in for navigator.gpu, just enough of WebGPU for web/js/accel/gpu.js: buffers are bytes, a submitted
// dispatch runs the CPU emulation of the kernel (wgsl-emu.mjs) over the bound buffers, a copy copies, a map maps.
// It lets `node --test` drive runWebGPU and the host's GPU path (batches, passes, readback, the host's check and its
// fallback). `tamper(counts)` may change the counts read back, to play a wrong GPU.

import { dispatch } from "./wgsl-emu.mjs";

const USAGE = { MAP_READ: 1, MAP_WRITE: 2, COPY_SRC: 4, COPY_DST: 8, INDEX: 16, VERTEX: 32, UNIFORM: 64, STORAGE: 128, INDIRECT: 256, QUERY_RESOLVE: 512 };
globalThis.GPUBufferUsage ??= USAGE;
globalThis.GPUMapMode ??= { READ: 1, WRITE: 2 };

export function fakeGpu({ software = false, limits = {}, tamper = null, failDevice = false, vendor = "test", architecture = "emulated" } = {}) {
  const stats = { dispatches: 0, invocations: 0, submits: 0, maxInvocations: 0, passes: [], destroyed: 0, devices: 0 };
  const deviceLimits = { maxStorageBufferBindingSize: 128 * 1024 * 1024, maxBufferSize: 256 * 1024 * 1024, maxComputeWorkgroupsPerDimension: 65535, ...limits };

  function makeDevice() {
    stats.devices++;
    const words = (buf) => new Uint32Array(buf.bytes.buffer, 0, buf.bytes.byteLength >>> 2);
    const device = {
      limits: deviceLimits,
      lost: new Promise(() => {}),
      destroy() { stats.destroyed++; },
      pushErrorScope() {},
      popErrorScope: async () => null,
      createBuffer({ size, usage }) {
        return {
          size, usage, bytes: new Uint8Array(size), destroyed: false,
          destroy() { this.destroyed = true; },
          async mapAsync() {},
          getMappedRange() { return this.bytes.slice().buffer; },
          unmap() {},
        };
      },
      createShaderModule({ code }) {
        return { code, getCompilationInfo: async () => ({ messages: [] }) };
      },
      async createComputePipelineAsync() {
        return { getBindGroupLayout: () => ({}) };
      },
      createBindGroup({ entries }) {
        return { buffers: entries.map((e) => e.resource.buffer) };
      },
      createCommandEncoder() {
        const cmds = [];
        return {
          beginComputePass() {
            let group = null;
            return {
              setPipeline() {},
              setBindGroup(_i, g) { group = g; },
              dispatchWorkgroups(x) { cmds.push({ kind: "dispatch", group, x }); },
              end() {},
            };
          },
          copyBufferToBuffer(src, so, dst, d0, size) { cmds.push({ kind: "copy", src, so, dst, d0, size }); },
          finish() { return cmds; },
        };
      },
      queue: {
        writeBuffer(buf, offset, data) {
          const src = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
          buf.bytes.set(src, offset);
        },
        submit(list) {
          stats.submits++;
          for (const cmds of list) {
            for (const c of cmds) {
              if (c.kind === "dispatch") {
                const [params, bins, member, observed, mt, work, atLeast] = c.group.buffers.map(words);
                const n = c.x * 64;
                stats.dispatches++;
                stats.invocations += n;
                stats.maxInvocations = Math.max(stats.maxInvocations, n);
                stats.passes.push(params[2]);
                dispatch({ params, bins, member, observed, mt, work, atLeast }, n);
              } else if (c.kind === "copy") {
                c.dst.bytes.set(c.src.bytes.subarray(c.so, c.so + c.size), c.d0);
                if (tamper && (c.dst.usage & USAGE.MAP_READ)) {
                  const got = new Uint32Array(c.dst.bytes.buffer, c.d0, c.size >>> 2);
                  tamper(got);
                }
              }
            }
          }
        },
        onSubmittedWorkDone: async () => {},
      },
    };
    return device;
  }

  const adapter = {
    info: { vendor, architecture, description: "the CPU emulation of the kernel", isFallbackAdapter: software },
    isFallbackAdapter: software,
    async requestDevice() {
      if (failDevice) throw new Error("no device for you");
      return makeDevice();
    },
  };
  return { gpu: { requestAdapter: async () => adapter }, stats };
}
