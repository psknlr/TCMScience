// gpu-science.mjs --software: the harness fails unless the WGSL ran, and was exact, at every size.
import assert from "node:assert/strict";
import test from "node:test";
import { softwareFailures } from "./gpu-science.mjs";

const size = (webgpu, extra = {}) => ({ queries: 32, refs: 2048, exact: { "wasm-simd": true, webgpu }, ...extra });
const report = (adapter, sizes, gpuError = null) => ({ ok: true, device: { adapter, gpu_error: gpuError }, kernels: { "tanimoto.best": { sizes } } });
const SWIFTSHADER = { kind: "software", vendor: "google", architecture: "swiftshader", reason: "SwiftShader" };

test("--software passes only when the WGSL ran exactly at every size", () => {
  assert.deepEqual(softwareFailures(report(SWIFTSHADER, [size(true), size(true, { queries: 256, refs: 10504 })])), []);
});

test("--software with no adapter fails: what the device check calls 'no physical adapter' left the WGSL unchecked", () => {
  // the report the page gave in a Chromium launched with --disable-gpu (ok: true, verdict "no physical adapter")
  const none = report(null, [size(null), size(null, { queries: 256, refs: 10504 })], { code: null, message: "No WebGPU adapter available" });
  const failures = softwareFailures(none);
  assert.equal(failures.length, 1);
  assert.match(failures[0], /no WebGPU adapter, so the WGSL never ran \(No WebGPU adapter available\)/);
});

test("--software fails at a size where the GPU did not run or was not exact, and when nothing was measured", () => {
  const partial = softwareFailures(report(SWIFTSHADER, [size(true), size(null, { queries: 256, refs: 10504, gpu_error: { message: "device lost" } })]));
  assert.deepEqual(partial, ["--software: the WGSL did not run exactly at 256 × 10504 (device lost)"]);
  assert.match(softwareFailures(report(SWIFTSHADER, [size(false)]))[0], /32 × 2048 \(not exact\)/);
  assert.deepEqual(softwareFailures(report(SWIFTSHADER, [])), ["--software: no size was measured"]);
  assert.deepEqual(softwareFailures(undefined), [], "no report is the harness's exit 2, not this rule's");
});
