// A Web Worker look-alike over Node's worker_threads, so the js-workers engine (web/js/accel/workers.js) runs its real
// worker script (npnull.worker.js) under `node --test`: the script sees `self.onmessage` / `self.postMessage`, the
// pool sees {postMessage, terminate, onmessage, onerror}. Every worker started is remembered, so a test can check
// that an abort terminated them.

import { Worker } from "node:worker_threads";

const BOOT = new URL("./node-worker-boot.mjs", import.meta.url);
export const NPNULL_WORKER = new URL("../../js/accel/npnull.worker.js", import.meta.url);

export const started = [];

/** A worker running `moduleUrl` (the np-null worker by default). */
export function nodeWorker(moduleUrl = NPNULL_WORKER) {
  const w = new Worker(BOOT, { workerData: { module: String(moduleUrl) } });
  const facade = {
    onmessage: null,
    onerror: null,
    onmessageerror: null,
    exited: false,
    postMessage: (msg) => w.postMessage(msg),
    terminate: () => { void w.terminate(); },
  };
  w.on("message", (data) => facade.onmessage?.({ data }));
  w.on("error", (err) => facade.onerror?.({ message: err.message, error: err, preventDefault() {} }));
  w.on("messageerror", () => facade.onmessageerror?.({}));
  w.on("exit", () => { facade.exited = true; });
  started.push(facade);
  return facade;
}
