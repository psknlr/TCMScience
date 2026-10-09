// The js-workers engine of np-null-mt/1 (docs/V2.md §13.4): the exact kernel (npnull.js) in a pool of plain module
// workers. Each pathway has its own random stream, so splitting by pathway gives the single-thread numbers whatever
// the schedule: a dynamic queue hands out chunks of pathways to whichever worker is free, and the counts are merged by
// pathway index. Works on the page and inside a dedicated worker (nested workers). An abort terminates every worker.

import { abortError } from "../core/util.js";

/** Chunks stay small enough to report progress often (each pathway is milliseconds to a few hundred on a phone). */
export const MAX_CHUNK = 16;

/** A module worker running npnull.worker.js. */
export function defaultWorker() {
  return new Worker(new URL("./npnull.worker.js", import.meta.url), { type: "module", name: "np-null-mt" });
}

/** Pathways per chunk: about six chunks per worker, at most MAX_CHUNK, at least 1. */
export function chunkSize(paths, workers) {
  return Math.max(1, Math.min(MAX_CHUNK, Math.ceil(paths / (Math.max(1, workers) * 6))));
}

/**
 * at_least for every pathway of `plan` (npnull.prepare) on `workers` workers (at least 1, never more than the
 * pathways). `createWorker()` returns a Web Worker-like object ({postMessage, terminate, onmessage, onerror}).
 * onProgress(fraction) after every chunk. Rejects with an AbortError on abort, or an Error when a worker fails.
 */
export function runJsWorkers(plan, { workers = 1, signal, onProgress, createWorker = defaultWorker, chunk } = {}) {
  const n = plan.ids.length;
  if (!n) return Promise.resolve([]);
  if (signal?.aborted) return Promise.reject(abortError());
  const size = Math.max(1, Math.min(Math.floor(workers) || 1, n));
  const step = Math.max(1, Math.floor(chunk) || chunkSize(n, size));
  const counts = new Array(n);
  const pool = [];
  let next = 0;
  let finished = 0;
  let settled = false;

  return new Promise((resolve, reject) => {
    const stop = () => {
      for (const w of pool) { try { w.terminate(); } catch { /* gone */ } }
      pool.length = 0;
      signal?.removeEventListener?.("abort", onAbort);
    };
    const fail = (err) => {
      if (settled) return;
      settled = true;
      stop();
      reject(err);
    };
    const onAbort = () => fail(abortError());
    signal?.addEventListener?.("abort", onAbort, { once: true });

    const feed = (w) => {
      if (next >= n) return;
      const from = next;
      next = Math.min(n, from + step);
      w.postMessage({ type: "range", from, to: next });
    };
    const onMessage = (w, data) => {
      if (settled) return;
      if (data?.type === "ready") feed(w);
      else if (data?.type === "counts") {
        const got = data.counts || [];
        for (let i = 0; i < got.length; i++) counts[data.from + i] = got[i];
        finished += got.length;
        onProgress?.(finished / n);
        if (finished >= n) {
          settled = true;
          stop();
          resolve(counts);
        } else feed(w);
      } else if (data?.type === "error") fail(new Error(`np-null-mt/1 worker: ${data.message}`));
    };

    try {
      for (let i = 0; i < size; i++) {
        const w = createWorker();
        pool.push(w);
        w.onmessage = (ev) => onMessage(w, ev?.data);
        w.onerror = (ev) => {
          ev?.preventDefault?.();
          fail(new Error(`np-null-mt/1 worker failed: ${ev?.message || ev?.error?.message || "error"}`));
        };
        w.onmessageerror = () => fail(new Error("np-null-mt/1 worker: a message could not be read"));
        w.postMessage({ type: "plan", plan });
      }
    } catch (err) {
      fail(new Error(`np-null-mt/1: could not start a worker (${err?.message || err})`));
    }
  });
}
