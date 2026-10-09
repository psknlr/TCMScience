// A scripted stand-in for pyodide.worker.js, for the pool and accelerator tests: each instance answers the worker
// protocol (JSON-string requests, replies by id, progress and accelerate messages) with per-op behaviour, and records
// what it was sent, in order, structured messages included.

import assert from "node:assert/strict";

export const SHA = "a".repeat(64);
export const BOOT = {
  schema: "tcmstudio.boot/1",
  pyodide: { version: "314.0.7", index_url: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/", packages: ["pyyaml", "packaging", "sqlite3"] },
  bundle: { path: "runtime/tcms-py.aaaaaaaaaaaa.tar.gz", sha256: SHA, bytes: 1000, format: "gztar", extract_dir: "/opt/tcms/site", python_path: ["/opt/tcms/site"] },
  catalog: "runtime/catalog.json",
  state_root: "/persist",
  versions: { tcmstudio: "0.1.0", bioagent: "0.2.7", psh: "0.6.0" },
};
export const CATALOG = {
  schema: "tcmstudio.catalog/1",
  core: [
    { name: "tcm_compatibility", maps_to: "native.tcm_compatibility" },
    { name: "tcm_safety_report", maps_to: "skill.assess-tcm-safety" },
    { name: "call_tool", maps_to: null },
  ],
  entries: [
    { id: "native.tcm_compatibility", kind: "native" },
    { id: "native.edit_distance", kind: "native" },
    { id: "skill.assess-tcm-safety", kind: "skill" },
    { id: "system.audit_verify", kind: "system" },
  ],
};

/** The smallest valid WebAssembly module (magic + version), for the shared-module path. */
export const EMPTY_WASM = new Uint8Array([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]);

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** A site: boot.json, catalog.json and, when `wasm` is set, pyodide.asm.wasm at the boot's index URL. */
export function site({ boot = BOOT, wasm = null } = {}) {
  const calls = [];
  const fetch = async (url) => {
    calls.push(String(url));
    const p = new URL(url).pathname;
    if (p === "/runtime/boot.json") return json(boot);
    if (p === "/runtime/catalog.json") return json(CATALOG);
    if (wasm && p.endsWith("/pyodide.asm.wasm")) return new Response(wasm, { headers: { "content-type": "application/wasm" } });
    return new Response("missing", { status: 404 });
  };
  return { fetch, calls };
}

export const envelope = (tool, extra = {}) => ({
  ok: true, tool, via: tool, status: "succeeded", duration_ms: 1, summary: `${tool} done`, text: "ok", result: { tool },
  citations: [], governance: { kind: "native", released: null, refusals: [], outputs: [] },
  receipt: { where: "browser", runtime: "pyodide-314.0.7 / CPython 3.14.2", device: "cpu" },
  job: null, approval: null, error: null, ...extra,
});

export const DEFAULTS = {
  init(w, { id, payload }) {
    w.send({ type: "progress", progress: 3, stage: "pyodide", vars: { version: "314.0.7" }, message: "Loading Pyodide" });
    w.reply(id, {
      runtime: "pyodide-314.0.7 / CPython 3.14.2", versions: { tcmstudio: "0.1.0", python: "3.14.2" }, role: payload.role,
      packages: ["pyyaml", "packaging"], persist: payload.persist ? { mode: "idbfs", durable: true, error: null, root: "/persist" } : { mode: "memory", durable: false, error: "persistence turned off", root: "/persist" },
      bundle: { sha256: SHA, from_cache: false }, pycache: { restored: false, saves: payload.role === "primary" }, timings: {}, boot_ms: 1,
    });
  },
  warm(w, { id }) { w.reply(id, { status: "succeeded", ms: 1 }); },
  call(w, { id, payload }) { w.reply(id, envelope(payload.tool)); },
  device(w, { id }) { w.reply(id, { cores: 1 }); },
  debug(w, { id }) { w.reply(id, { induced: "fatal" }); },
};

/** A Worker class; `behavior` overrides ops by name, and `message(w, msg)` receives JSON messages with a type. */
export function workerClass(behavior = {}) {
  const instances = [];
  class FakeWorker {
    constructor(url, opts) {
      this.url = String(url);
      this.opts = opts;
      this.received = [];   // requests, in order
      this.log = [];        // everything posted: "interrupt-buffer", "wasm-module", "init", "call", "accelerated", …
      this.messages = [];   // JSON messages with a type (accelerated answers)
      this.terminated = false;
      this.buffer = null;
      this.module = null;
      instances.push(this);
    }
    postMessage(data) {
      if (this.terminated) return;
      if (data && typeof data === "object") {
        this.log.push(data.type);
        if (data.type === "interrupt-buffer") this.buffer = new Uint8Array(data.buffer);
        else if (data.type === "wasm-module") this.module = data.module;
        else assert.fail(`unexpected structured message ${data.type}`);
        return;
      }
      assert.equal(typeof data, "string", "requests cross the boundary as JSON strings");
      const msg = JSON.parse(data);
      if (msg.type) {
        this.log.push(msg.type);
        this.messages.push(msg);
        setTimeout(() => behavior.message?.(this, msg), 0);
        return;
      }
      this.log.push(msg.op);
      this.received.push(msg);
      const handler = behavior[msg.op] || DEFAULTS[msg.op];
      setTimeout(() => handler(this, msg), 0);
    }
    send(obj) {
      if (this.terminated) return;
      setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: typeof obj === "string" ? obj : JSON.stringify(obj) }); }, 0);
    }
    reply(id, result, extra = {}) { this.send({ id, ok: true, ms: 1, result, ...extra }); }
    terminate() { this.terminated = true; }
  }
  FakeWorker.instances = instances;
  return FakeWorker;
}

export const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));

export async function until(cond, ms = 2000) {
  const t0 = Date.now();
  while (!cond()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await tick(2);
  }
}
