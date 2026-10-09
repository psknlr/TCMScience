// web/js/accel/speed.js: what the benchmark measured decides whether "auto" uses the GPU.
import assert from "node:assert/strict";
import test from "node:test";

import { clearSpeed, gpuMeasuredFaster, readSpeed, recordSpeed } from "../../js/accel/speed.js";

function withStorage(fn) {
  const before = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const data = new Map();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: (k) => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: (k) => data.delete(k) } });
  try {
    return fn(data);
  } finally {
    if (before) Object.defineProperty(globalThis, "localStorage", before);
    else delete globalThis.localStorage;
  }
}

test("never measured is null; faster and slower are told apart; a bad record is ignored", () => withStorage((data) => {
  assert.equal(readSpeed(), null);
  assert.equal(gpuMeasuredFaster(), null);
  recordSpeed({ adapter: "apple m3", gpu_ms: 900, cpu_ms: 5000, workers: 7, pathways: 1399 });
  assert.equal(gpuMeasuredFaster(), true);
  assert.equal(readSpeed().adapter, "apple m3");
  recordSpeed({ adapter: "adreno 610", gpu_ms: 9000, cpu_ms: 5000 });
  assert.equal(gpuMeasuredFaster(), false);
  assert.equal(recordSpeed({ gpu_ms: Number.NaN, cpu_ms: 1 }), null);
  data.set("tcmstudio.accel.speed/1", "{not json");
  assert.equal(gpuMeasuredFaster(), null);
  clearSpeed();
  assert.equal(readSpeed(), null);
}));

test("no storage at all (private mode, Node): not measured, and recording does not throw", () => {
  const before = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("SecurityError"); } });
  try {
    assert.equal(gpuMeasuredFaster(), null);
    assert.ok(recordSpeed({ gpu_ms: 1, cpu_ms: 2 }));
  } finally {
    if (before) Object.defineProperty(globalThis, "localStorage", before);
    else delete globalThis.localStorage;
  }
});
