// Inside a worker_threads worker: give a module worker script the globals a browser worker has (self, postMessage,
// onmessage), then load it. The port's messages are queued until the listener is added, after the script set
// self.onmessage.
import { parentPort, workerData } from "node:worker_threads";

globalThis.self = globalThis;
globalThis.postMessage = (msg) => parentPort.postMessage(msg);
await import(workerData.module);
parentPort.on("message", (data) => globalThis.onmessage?.({ data }));
