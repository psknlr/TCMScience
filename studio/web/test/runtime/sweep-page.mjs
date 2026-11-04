// The browser side of the sweep (sweep.mjs): boots BrowserRuntime as the runtime check does (check.mjs) and makes
// each call of a list as the router hands it to the runtime: rt.call(tool, args, {project_id, conversation_id,
// approvals: [], signal, network: false}), with the acceleration the router passes by default. Every envelope is kept,
// with the call's wall time, for the Node side to take as it goes.
//
//   globalThis.__sweep.start(calls, {timeoutMs, from})  start (or resume) the list at `from`; returns at once
//   globalThis.__sweep.state()                         {booted, boot, done, next, total, error}
//   globalThis.__sweep.take(from)                      the records from index `from` of this page's records on
//
// ?project=ID ?index=URL (Pyodide) ?timeout=MS (each call; the runtime's own callTimeoutMs is set to it as well)

import { BrowserRuntime } from "../../js/runtime/browser.js";

const q = new URLSearchParams(location.search);
const project = q.get("project") || `sweep-${Date.now().toString(36)}`;
const indexUrl = q.get("index") || undefined;
const callTimeoutMs = Number(q.get("timeout") || 120_000);
const bootUrl = new URL("/runtime/boot.json", location.href).href;

const $status = document.getElementById("status");
const records = [];
const st = { booted: false, boot: null, done: false, next: 0, total: 0, error: null };

const rt = new BrowserRuntime({ bootUrl, indexUrl, debug: true, callTimeoutMs });

async function boot() {
  const t0 = performance.now();
  const info = await rt.start();
  st.boot = { wall_ms: Math.round(performance.now() - t0), runtime: info?.runtime, bundle_from_cache: info?.bundle?.from_cache ?? null, persist: info?.persist?.mode ?? null, isolated: Boolean(globalThis.crossOriginIsolated) };
  st.booted = true;
}

/** One call; never throws: a call that throws or outlives its timeout is recorded as such. */
async function one(c, timeoutMs) {
  const ac = new AbortController();
  let timer = null;
  const t0 = performance.now();
  const rec = { key: c.key, index: c.index };
  try {
    const timeout = new Promise((resolve) => { timer = setTimeout(() => { ac.abort(); resolve("timeout"); }, timeoutMs + 5_000); });
    const env = await Promise.race([
      rt.call(c.tool, c.args, { project_id: project, conversation_id: "runtime-check", approvals: [], signal: ac.signal, network: false }),
      timeout,
    ]);
    if (env === "timeout") rec.timeout = true;
    else rec.envelope = env;
  } catch (err) {
    rec.threw = String(err?.stack || err?.message || err).slice(0, 2000);
  } finally {
    clearTimeout(timer);
  }
  rec.ms = Math.round(performance.now() - t0);
  return rec;
}

async function loop(calls, timeoutMs) {
  try {
    if (!st.booted) await boot();
    while (st.next < calls.length) {
      const c = { ...calls[st.next], index: st.next };
      $status.textContent = `${st.next + 1}/${calls.length} ${c.key}`;
      records.push(await one(c, timeoutMs));
      st.next += 1;
    }
  } catch (err) {
    st.error = String(err?.stack || err?.message || err).slice(0, 2000);
  }
  st.done = true;
  $status.textContent = st.error ? `stopped: ${st.error}` : `done: ${records.length} calls`;
}

globalThis.__sweep = {
  start(calls, { timeoutMs = callTimeoutMs, from = 0 } = {}) {
    st.total = calls.length;
    st.next = from;
    st.done = false;
    void loop(calls, timeoutMs);
    return true;
  },
  state: () => ({ ...st }),
  take: (from = 0) => records.slice(from),
};
$status.textContent = "ready";
