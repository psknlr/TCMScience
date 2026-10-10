// The browser runtime (CONTRACTS §5, §6): the real psh + bioagent + tcmstudio Python, run by pinned Pyodide in a
// module Web Worker (./pyodide.worker.js). It implements the RuntimeClient interface: kind "browser", status events
// with load progress, a lazy start(), info(), catalog() straight from the built runtime/catalog.json (tools can be
// offered before Python has loaded), call() → envelope with receipt.where "browser", device().
//
// Calls run one at a time. A stop interrupts Python through a SharedArrayBuffer when the page is cross-origin
// isolated (KeyboardInterrupt → status "cancelled"); otherwise, or when Python does not stop within a grace period,
// the worker is terminated and started again. A Pyodide fatal error also restarts it. A call always resolves to an
// envelope: failures are data.

import { Emitter } from "../core/events.js";
import { registerStrings, t } from "../core/i18n.js";
import { canonicalJson, nowIso, sha256Hex } from "../core/util.js";
import { WEBGPU_TOOLS } from "./webgpu.js";

const CATALOG_SCHEMA = "tcmstudio.catalog/1";
const BOOT_SCHEMA = "tcmstudio.boot/1";
// Entries whose runs read or extend a project's audit chain or session files: their state is synced with IndexedDB.
const STATEFUL_KINDS = new Set(["skill", "clinic"]);
const STATEFUL_IDS = new Set(["system.audit_verify"]);
// Failures a call should not retry by itself (a retry would repeat them); an explicit start() still tries again.
const STICKY_BOOT_ERRORS = new Set(["bundle_hash", "boot_missing", "bad_boot", "restarts"]);
const RESTART_WINDOW_MS = 60_000;
const MAX_RESTARTS = 3;

registerStrings("zh", {
  "runtime.browser.stage.start": "正在启动浏览器内的 Python 运行环境…",
  "runtime.browser.stage.restart": "正在重新启动 Python 运行环境…",
  "runtime.browser.stage.pyodide": "正在载入 Pyodide {version}（CPython，WebAssembly）",
  "runtime.browser.stage.pyodide_ready": "Pyodide {version} 已载入",
  "runtime.browser.stage.bundle": "正在下载 TCMScience 代码包（{mb} MB）",
  "runtime.browser.stage.packages": "正在载入依赖包：{names}",
  "runtime.browser.stage.unpack": "正在解包并校验",
  "runtime.browser.stage.persist": "正在打开本地审计存储",
  "runtime.browser.stage.import": "正在导入 tcmstudio、bioagent 与 psh",
  "runtime.browser.stage.packages_on_demand": "首次使用，正在载入 {names}",
  "runtime.browser.stage.data": "正在按需载入完整方剂表：{rows} 条（{mb} MB）",
  "runtime.browser.stage.ready": "就绪：Python 工具在本机 CPU 上运行（单线程，WebAssembly）",
  "runtime.browser.error.unsupported": "此浏览器不支持 Web Worker 或 WebAssembly，Python 工具无法在浏览器中运行；请改用本机 Runner。",
  "runtime.browser.error.not_built": "此站点没有浏览器运行环境（runtime/boot.json 不存在）；请改用本机 Runner。",
  "runtime.browser.error.boot": "浏览器运行环境未能启动：{message}",
  "runtime.browser.error.hash": "代码包校验失败：SHA-256 与 boot.json 不符，已拒绝运行。请刷新页面；若仍失败，请改用本机 Runner。",
  "runtime.browser.error.fatal": "Python 运行环境发生致命错误，已重新启动；本次调用没有结果。",
  "runtime.browser.error.restarts": "Python 运行环境反复出错，已停止自动重启；请刷新页面或改用本机 Runner。",
  "runtime.browser.error.timeout": "调用超过 {s} 秒未完成，已终止并重新启动运行环境。",
  "runtime.browser.cancelled": "已取消：调用在完成前被停止",
});
registerStrings("en", {
  "runtime.browser.stage.start": "Starting the in-browser Python runtime…",
  "runtime.browser.stage.restart": "Restarting the Python runtime…",
  "runtime.browser.stage.pyodide": "Loading Pyodide {version} (CPython, WebAssembly)",
  "runtime.browser.stage.pyodide_ready": "Pyodide {version} loaded",
  "runtime.browser.stage.bundle": "Downloading the TCMScience bundle ({mb} MB)",
  "runtime.browser.stage.packages": "Loading packages: {names}",
  "runtime.browser.stage.unpack": "Unpacking",
  "runtime.browser.stage.persist": "Opening local audit storage",
  "runtime.browser.stage.import": "Importing tcmstudio, bioagent and psh",
  "runtime.browser.stage.packages_on_demand": "First use: loading {names}",
  "runtime.browser.stage.data": "Loading the full formula table on demand: {rows} rows ({mb} MB)",
  "runtime.browser.stage.ready": "Ready: Python tools run on this computer's CPU (single thread, WebAssembly)",
  "runtime.browser.error.unsupported": "This browser has no Web Worker or WebAssembly support, so Python tools cannot run in it; use the local runner.",
  "runtime.browser.error.not_built": "This site has no browser runtime (runtime/boot.json is missing); use the local runner.",
  "runtime.browser.error.boot": "The browser runtime could not start: {message}",
  "runtime.browser.error.hash": "The code bundle failed its check: its SHA-256 does not match boot.json, so it was not run. Reload the page; if it fails again, use the local runner.",
  "runtime.browser.error.fatal": "The Python runtime hit a fatal error and was restarted; this call has no result.",
  "runtime.browser.error.restarts": "The Python runtime kept failing, so it is no longer restarted automatically; reload the page or use the local runner.",
  "runtime.browser.error.timeout": "The call did not finish within {s} s; the runtime was stopped and started again.",
  "runtime.browser.cancelled": "Cancelled: the call was stopped before it finished",
});

/** The device report of CONTRACTS §5, from the page itself: needs no Python, so it never starts the worker. */
/** "auto" | "cpu" (no GPU) | "reference" (the original Python for every tool); anything else is "auto". */
export function accelerationOf(value) {
  return value === "cpu" || value === "reference" ? value : "auto";
}

export async function detectDevice({ navigator: nav = globalThis.navigator, isolated = globalThis.crossOriginIsolated, timeoutMs = 3000 } = {}) {
  const out = {
    cores: nav?.hardwareConcurrency ?? null,
    memory_gb: nav?.deviceMemory ?? null,
    cross_origin_isolated: Boolean(isolated),
    webgpu: { api: Boolean(nav && "gpu" in nav), adapter: null },
    python: { device: "cpu", threads: 1, note: "Python tools run on the CPU (single thread, WebAssembly)" },
    compute: { webgpu_tools: [...WEBGPU_TOOLS], webgpu_adapter: false, precision: "Exact uint32 reductions; Python float64 transforms", fallback: "pyodide CPU" },
  };
  if (nav?.gpu?.requestAdapter) {
    try {
      const adapter = await Promise.race([
        nav.gpu.requestAdapter({ powerPreference: "high-performance" }),
        new Promise((resolve) => setTimeout(() => resolve(undefined), timeoutMs)),
      ]);
      if (adapter) {
        const i = adapter.info || (adapter.requestAdapterInfo ? await adapter.requestAdapterInfo() : {}) || {};
        const fallback = Boolean(adapter.isFallbackAdapter ?? i.isFallbackAdapter);
        out.webgpu.adapter = {
          vendor: i.vendor || "", architecture: i.architecture || "", description: i.description || "",
          // SwiftShader, llvmpipe and friends are CPU emulators: not a GPU, whatever the API says
          software: fallback || /swiftshader|llvmpipe|lavapipe|software|microsoft basic render/i.test(`${i.vendor} ${i.architecture} ${i.description}`),
          fallback,
        };
        out.compute.webgpu_adapter = !out.webgpu.adapter.software;
      }
    } catch (err) {
      out.webgpu.error = String(err?.message || err);
    }
  }
  return out;
}

export class BrowserRuntime {
  kind = "browser";
  status = "idle";
  progress = 0;
  message = "";
  error = null;

  #bus = new Emitter();
  #opts;
  #bootUrl;
  #siteUrl;
  #bootP = null;
  #catalogP = null;
  #index = null;
  #startP = null;
  #worker = null;
  #generation = 0;
  #seq = 0;
  #pending = new Map();
  #queue = [];
  #running = null;
  #interrupt = null;
  #info = null;
  #deviceP = null;
  #restarts = [];
  #bootError = null;
  #packages = new Set();
  #durable = null;

  /**
   * opts: {bootUrl (absolute URL of runtime/boot.json), siteUrl? (default: the directory above boot.json),
   * workerUrl?, indexUrl? (overrides boot.json's Pyodide URL), persist: true (IDBFS for audit chains),
   * warm: true (build the native section after boot), callTimeoutMs: 600000, bootTimeoutMs: 180000,
   * interruptGraceMs: 3000, debug: false (enables the worker's test-only operations), Worker?, fetch?}.
   */
  constructor(opts = {}) {
    this.#opts = { persist: true, warm: true, callTimeoutMs: 600_000, bootTimeoutMs: 180_000, interruptGraceMs: 3000, debug: false, ...opts };
    const loc = globalThis.location?.href || "http://localhost/";
    this.#bootUrl = new URL(opts.bootUrl || "/runtime/boot.json", loc).href;
    this.#siteUrl = new URL(opts.siteUrl || "../", this.#bootUrl).href;
    const W = this.#opts.Worker || globalThis.Worker;
    if (typeof W !== "function" || typeof globalThis.WebAssembly !== "object") {
      this.status = "offline";
      this.message = t("runtime.browser.error.unsupported");
      this.error = this.message;
    }
  }

  get label() { return t("core.browser.label"); }
  get interruptible() { return Boolean(this.#interrupt); }

  onStatus(fn) { return this.#bus.on("status", fn); }

  // ------------------------------------------------------------------------------------------------------ catalog

  /** The built catalog (CONTRACTS §2) for the browser, fetched once; Python is not needed for it. */
  catalog() {
    if (!this.#catalogP) {
      this.#catalogP = (async () => {
        const boot = await this.#boot();
        const url = new URL(boot.catalog || "runtime/catalog.json", this.#siteUrl).href;
        const res = await this.#fetch(url);
        if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
        const doc = await res.json();
        if (doc?.schema !== CATALOG_SCHEMA || !Array.isArray(doc.entries)) throw new Error(`${url} is not a ${CATALOG_SCHEMA} document`);
        return doc;
      })();
      this.#catalogP.catch(() => { this.#catalogP = null; });
    }
    return this.#catalogP;
  }

  // ------------------------------------------------------------------------------------------------------ lifecycle

  /** Load Pyodide and the bundle (once). Resolves with info() when ready; rejects when the runtime cannot start. */
  start() {
    if (this.status === "offline" && !this.#bootP) return Promise.reject(new Error(this.message));
    if (this.status === "ready" && this.#worker) return Promise.resolve(this.#info);
    if (!this.#startP) {
      if (this.#bootError?.code === "restarts") this.#restarts = [];
      this.#bootError = null;
      this.#startP = this.#spawn(false);
      this.#startP.catch(() => { this.#startP = null; });
    }
    return this.#startP;
  }

  /** Terminate the worker. The next call or start() boots it again (from the HTTP and Cache Storage caches). */
  stop() {
    const err = new Error("the runtime was stopped");
    this.#terminate(err);
    this.#startP = null;
    this.#flushQueue(err);
    this.#setStatus("idle", { progress: 0, message: "" });
  }

  async info() {
    let boot = null;
    try { boot = await this.#boot(); } catch { boot = null; }
    return {
      kind: "browser",
      label: this.label,
      status: this.status,
      runtime: this.#info?.runtime || (boot ? `pyodide-${boot.pyodide?.version}` : null),
      versions: this.#info?.versions || boot?.versions || {},
      pyodide: this.#info?.pyodide || (boot ? { version: boot.pyodide?.version, index_url: boot.pyodide?.index_url } : null),
      bundle: boot ? { path: boot.bundle?.path, sha256: boot.bundle?.sha256, bytes: boot.bundle?.bytes, from_cache: this.#info?.bundle?.from_cache ?? null } : null,
      packages: [...new Set([...(this.#info?.packages || []), ...this.#packages])],
      persist: this.#info?.persist ? { ...this.#info.persist, durable: this.#durable ?? this.#info.persist.durable } : null,
      device: "cpu",
      threads: 1,
      compute: { webgpu_tools: [...WEBGPU_TOOLS], preference: this.#opts.settings?.browserAcceleration || "auto", fallback: "pyodide CPU" },
      cross_origin_isolated: Boolean(globalThis.crossOriginIsolated),
      interruptible: this.interruptible,
      boot_ms: this.#info?.boot_ms ?? null,
      timings: this.#info?.timings || null,
      error: this.error,
    };
  }

  device() {
    if (!this.#deviceP) {
      this.#deviceP = detectDevice();
      this.#deviceP.catch(() => { this.#deviceP = null; });
    }
    return this.#deviceP;
  }

  // ---------------------------------------------------------------------------------------------------------- calls

  /** Run a tool. ctx: {project_id, conversation_id, approvals, signal}. Always resolves to an envelope (§3). */
  async call(tool, args = {}, ctx = {}) {
    const started = nowIso();
    const t0 = now();
    const name = String(tool || "");
    const input = args && typeof args === "object" && !Array.isArray(args) ? args : {};
    const { signal } = ctx;
    const entry = await this.#entryFor(name, input);
    const fail = (status, error, summary, text) => this.#envelope({ tool: name, args: input, entry, status, error, summary, text, started, t0 });

    if (signal?.aborted) return fail("cancelled", null, t("runtime.browser.cancelled"), "Not run: the call was cancelled before it started.");
    if (this.status === "offline" && !this.#bootP) {
      return fail("failed", { type: "unavailable", message: this.message, hint: "Start the local runner (tcmstudio serve) and connect it in Settings." }, this.message,
        `Not run: ${this.message} Say what this would need; do not substitute an approximation.`);
    }
    if (this.#bootError && STICKY_BOOT_ERRORS.has(this.#bootError.code)) return this.#bootFailure(name, input, entry, this.#bootError, started, t0);
    try {
      await this.start();
    } catch (err) {
      return this.#bootFailure(name, input, entry, err, started, t0);
    }
    const context = { project_id: ctx.project_id ?? null, conversation_id: ctx.conversation_id ?? null, approvals: Array.isArray(ctx.approvals) ? ctx.approvals : [], device: "cpu", network: ctx.network === true };
    // only capabilities_status reports host facts; the WebGPU probe is not worth making for every call
    if (entry?.id === "system.capabilities") context.capabilities = await this.#capabilities();
    if (signal?.aborted) return fail("cancelled", null, t("runtime.browser.cancelled"), "Not run: the call was cancelled before it started.");
    const payload = {
      tool: name,
      arguments: input,
      context,
      // the router passes the live setting with every call; the settings this runtime was made with may be old
      acceleration: accelerationOf(ctx.acceleration ?? this.#opts.settings?.browserAcceleration),
      packages: Array.isArray(entry?.pyodide_packages) ? entry.pyodide_packages : [],
      stateful: Boolean(entry && (STATEFUL_KINDS.has(entry.kind) || STATEFUL_IDS.has(entry.id))),
    };
    return new Promise((resolve) => {
      const job = { op: "call", payload, signal, resolve, name, input, entry, started, t0 };
      if (signal) {
        job.onAbort = () => this.#abort(job);
        signal.addEventListener("abort", job.onAbort, { once: true });
      }
      this.#queue.push(job);
      this.#pump();
    });
  }

  /** Test pages only (opts.debug): have the worker induce a failure ("fatal": a Pyodide fatal error). */
  async debug(action) {
    if (!this.#opts.debug) throw new Error("debug operations are off (construct the runtime with debug: true)");
    await this.start();
    return new Promise((resolve) => {
      this.#queue.push({ op: "debug", payload: { action }, internal: true, resolve });
      this.#pump();
    });
  }

  // ------------------------------------------------------------------------------------------------------- internal

  #fetch(url, init) {
    const f = this.#opts.fetch || globalThis.fetch;
    return f(url, init);
  }

  #boot() {
    if (!this.#bootP) {
      this.#bootP = (async () => {
        let res;
        try {
          res = await this.#fetch(this.#bootUrl, { cache: "no-cache" });
        } catch (err) {
          throw codeError("boot_network", `${this.#bootUrl}: ${err?.message || err}`);
        }
        if (res.status === 404) {
          const e = codeError("boot_missing", t("runtime.browser.error.not_built"));
          this.#setStatus("offline", { progress: 0, message: e.message, error: e.message });
          throw e;
        }
        if (!res.ok) throw codeError("boot_http", `${this.#bootUrl}: HTTP ${res.status}`);
        const boot = await res.json();
        if (boot?.schema !== BOOT_SCHEMA || !boot.pyodide?.index_url || !boot.bundle?.path || !/^[0-9a-f]{64}$/.test(boot.bundle?.sha256 || "")) {
          throw codeError("bad_boot", `${this.#bootUrl} is not a usable ${BOOT_SCHEMA} manifest`);
        }
        return boot;
      })();
      this.#bootP.catch((err) => { if (err?.code !== "boot_missing" && err?.code !== "bad_boot") this.#bootP = null; });
    }
    return this.#bootP;
  }

  async #entryFor(name, args) {
    if (!this.#index) {
      try {
        const doc = await this.catalog();
        this.#index = {
          core: new Map(doc.core.map((c) => [c.name, c])),
          byId: new Map(doc.entries.map((e) => [e.id, e])),
        };
      } catch {
        return null;
      }
    }
    const { core, byId } = this.#index;
    const target = name === "call_tool" ? String(args?.tool || "") : name;
    const c = core.get(target);
    if (c) return byId.get(c.maps_to) || null;
    return byId.get(target) || null;
  }

  async #capabilities() {
    let device = null;
    try { device = await this.device(); } catch { device = null; }
    // what this browser can compute with, probed (compute/capabilities.js): requested, reported and verified kept apart
    let compute = null;
    try { compute = await (await import("../compute/capabilities.js")).probeCapabilities({ timeoutMs: 2000 }); } catch { compute = null; }
    return { browser: { device, compute, interruptible: this.interruptible, durable: this.#durable ?? this.#info?.persist?.durable ?? null, packages: [...this.#packages] } };
  }

  async #spawn(restart) {
    const gen = ++this.#generation;
    this.#setStatus("loading", { progress: 1, message: t(restart ? "runtime.browser.stage.restart" : "runtime.browser.stage.start"), error: null });
    try {
      const boot = await this.#boot();
      if (gen !== this.#generation) throw new Error("superseded");
      const W = this.#opts.Worker || globalThis.Worker;
      const url = this.#opts.workerUrl || new URL("./pyodide.worker.js", import.meta.url).href;
      const worker = new W(url, { type: "module", name: "tcmstudio-python" });
      this.#worker = worker;
      worker.onmessage = (ev) => this.#onMessage(gen, ev.data);
      worker.onerror = (ev) => this.#onCrash(gen, new Error(ev?.message || "the Python worker failed"));
      worker.onmessageerror = () => this.#onCrash(gen, new Error("a message from the Python worker could not be read"));
      const sab = globalThis.crossOriginIsolated && typeof SharedArrayBuffer === "function" ? new SharedArrayBuffer(1) : null;
      this.#interrupt = sab ? new Uint8Array(sab) : null;
      if (sab) worker.postMessage({ type: "interrupt-buffer", buffer: sab });
      const t0 = now();
      const reply = await this.#request("init", {
        boot, siteUrl: this.#siteUrl, indexUrl: this.#opts.indexUrl || null, persist: this.#opts.persist !== false, debug: Boolean(this.#opts.debug),
      }, this.#opts.bootTimeoutMs);
      if (gen !== this.#generation) throw new Error("superseded");
      if (!reply.ok) throw codeError(reply.error?.type || "boot_error", reply.error?.message || "the worker could not start");
      this.#info = { ...reply.result, wall_ms: Math.round(now() - t0) };
      this.#durable = this.#info.persist?.durable ?? null;
      for (const p of this.#info.packages || []) this.#packages.add(p);
      this.#setStatus("ready", { progress: 100, message: t("runtime.browser.stage.ready"), error: null });
      if (this.#opts.warm) this.#queue.unshift({ op: "warm", payload: {}, resolve: () => {}, internal: true });
      this.#pump();
      return this.#info;
    } catch (err) {
      if (gen === this.#generation) {
        this.#terminate(err);
        const hash = err?.code === "bundle_hash";
        this.#bootError = err;
        const message = hash ? t("runtime.browser.error.hash") : err?.code === "boot_missing" ? err.message : t("runtime.browser.error.boot", { message: err?.message || String(err) });
        if (this.status !== "offline") this.#setStatus("error", { progress: 0, message, error: message });
        this.#flushQueue(err);
      }
      throw err;
    }
  }

  #request(op, payload, timeoutMs = 0) {
    const id = ++this.#seq;
    return new Promise((resolve) => {
      const rec = { op, resolve, timer: null };
      if (timeoutMs > 0) {
        rec.timer = setTimeout(() => {
          this.#pending.delete(id);
          resolve({ id, ok: false, timeout: true, error: { type: "timeout", message: `${op} took longer than ${Math.round(timeoutMs / 1000)} s` } });
        }, timeoutMs);
      }
      this.#pending.set(id, rec);
      if (this.#interrupt) Atomics.store(this.#interrupt, 0, 0);
      this.#worker.postMessage(JSON.stringify({ id, op, payload }));
    });
  }

  #onMessage(gen, data) {
    if (gen !== this.#generation) return;
    let msg;
    try {
      msg = typeof data === "string" ? JSON.parse(data) : data;
    } catch (err) {
      this.#onCrash(gen, new Error(`unreadable reply from the Python worker: ${err.message}`));
      return;
    }
    if (msg?.type === "progress") {
      const message = t(`runtime.browser.stage.${msg.stage}`, msg.vars || {});
      const text = message === `runtime.browser.stage.${msg.stage}` ? msg.message : message;
      if (this.status === "loading") this.#setStatus("loading", { progress: Math.max(this.progress, msg.progress), message: text });
      else if (this.status === "ready") this.#setStatus("ready", { progress: 100, message: text });
      return;
    }
    const rec = this.#pending.get(msg?.id);
    if (!rec) return;
    this.#pending.delete(msg.id);
    if (rec.timer) clearTimeout(rec.timer);
    rec.resolve(msg);
  }

  #onCrash(gen, err) {
    if (gen !== this.#generation) return;
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const rec of pending) {
      if (rec.timer) clearTimeout(rec.timer);
      rec.resolve({ ok: false, fatal: true, error: { type: "fatal", message: err.message } });
    }
  }

  #pump() {
    if (this.#running || this.status !== "ready" || !this.#worker) return;
    const job = this.#queue.shift();
    if (!job) return;
    this.#running = job;
    this.#request(job.op, job.payload, job.internal ? 0 : this.#opts.callTimeoutMs).then((reply) => this.#settle(job, reply));
  }

  #settle(job, reply) {
    if (this.#running !== job) return; // already answered (a hard cancel or a crash) and restarted
    this.#running = null;
    if (job.graceTimer) clearTimeout(job.graceTimer);
    if (job.onAbort) job.signal?.removeEventListener("abort", job.onAbort);
    if (job.internal) {
      job.resolve(reply);
      if (reply.fatal) this.#restart("fatal");
      else this.#pump();
      return;
    }
    if (reply.terminated) return; // the worker was replaced; whoever terminated it answered the call
    if (reply.ok) {
      const env = reply.result;
      if (reply.persist && env?.receipt && reply.persist.durable === false && env.receipt.durable !== false) {
        env.receipt.durable = false;
        if (reply.persist.error) env.receipt.persist_error = reply.persist.error;
      }
      if (reply.persist) this.#durable = reply.persist.durable;
      for (const p of job.payload.packages || []) this.#packages.add(p);
      job.resolve(env);
      this.#pump();
      return;
    }
    if (job.cancelRequested) {
      // KeyboardInterrupt surfaced outside the dispatcher (between calls into Python): still a cancel
      job.resolve(this.#cancelled(job));
      if (reply.fatal) this.#restart("fatal");
      else this.#pump();
      return;
    }
    if (reply.timeout) {
      const s = Math.round(this.#opts.callTimeoutMs / 1000);
      job.resolve(this.#fail(job, { type: "timeout", message: t("runtime.browser.error.timeout", { s }), hint: "Run it on the local runner, or with smaller input." }, t("runtime.browser.error.timeout", { s })));
      this.#restart("timeout");
      return;
    }
    if (reply.fatal) {
      job.resolve(this.#fail(job, { type: "runtime_error", message: `${t("runtime.browser.error.fatal")} (${reply.error?.message || "fatal error"})`, hint: "Retry once; if it fails again, run it on the local runner." }, t("runtime.browser.error.fatal")));
      this.#restart("fatal");
      return;
    }
    const type = reply.error?.type === "unavailable" ? "unavailable" : "runtime_error";
    job.resolve(this.#fail(job, { type, message: reply.error?.message || "the call failed", hint: type === "unavailable" ? "Run it on the local runner." : "" }, reply.error?.message || "the call failed"));
    this.#pump();
  }

  #abort(job) {
    const queued = this.#queue.indexOf(job);
    if (queued >= 0) {
      this.#queue.splice(queued, 1);
      job.resolve(this.#cancelled(job));
      return;
    }
    if (this.#running !== job || job.cancelRequested) return;
    job.cancelRequested = true;
    if (this.#interrupt) {
      Atomics.store(this.#interrupt, 0, 2); // SIGINT: Python raises KeyboardInterrupt at its next check
      job.graceTimer = setTimeout(() => this.#hardCancel(job), this.#opts.interruptGraceMs);
    } else {
      this.#hardCancel(job);
    }
  }

  #hardCancel(job) {
    if (this.#running !== job) return;
    this.#running = null;
    job.resolve(this.#cancelled(job));
    this.#restart("cancel", { counts: false });
  }

  #restart(reason, { counts = true } = {}) {
    const nowMs = Date.now();
    if (counts) {
      this.#restarts = this.#restarts.filter((x) => nowMs - x < RESTART_WINDOW_MS);
      this.#restarts.push(nowMs);
    }
    this.#terminate(new Error(`restarted after ${reason}`), { quiet: true });
    this.#startP = null;
    if (this.#restarts.length > MAX_RESTARTS) {
      const message = t("runtime.browser.error.restarts");
      this.#bootError = codeError("restarts", message);
      this.#setStatus("error", { progress: 0, message, error: message });
      this.#flushQueue(this.#bootError);
      return;
    }
    this.#startP = this.#spawn(true);
    this.#startP.catch(() => { this.#startP = null; });
  }

  #terminate(err, { quiet = false } = {}) {
    this.#generation++;
    const w = this.#worker;
    this.#worker = null;
    this.#interrupt = null;
    if (w) {
      try { w.terminate(); } catch { /* already gone */ }
    }
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const rec of pending) {
      if (rec.timer) clearTimeout(rec.timer);
      rec.resolve({ ok: false, fatal: false, terminated: true, error: { type: "runtime_error", message: err?.message || "terminated" } });
    }
    const running = this.#running;
    this.#running = null;
    if (running?.internal) running.resolve({ ok: false, terminated: true, error: { type: "runtime_error", message: err?.message || "terminated" } });
    else if (running) {
      if (running.graceTimer) clearTimeout(running.graceTimer);
      if (running.onAbort) running.signal?.removeEventListener("abort", running.onAbort);
      running.resolve(running.cancelRequested ? this.#cancelled(running) : this.#fail(running, { type: "runtime_error", message: err?.message || "the runtime was stopped", hint: "" }, err?.message || "stopped"));
    }
    if (!quiet && this.status === "ready") this.#setStatus("idle", { progress: 0, message: "" });
  }

  #flushQueue(err) {
    const queued = this.#queue.splice(0);
    for (const job of queued) {
      if (job.internal) {
        job.resolve({ ok: false, error: { type: "unavailable", message: err?.message || String(err) } });
        continue;
      }
      if (job.onAbort) job.signal?.removeEventListener("abort", job.onAbort);
      this.#bootFailure(job.name, job.input, job.entry, err, job.started, job.t0).then(job.resolve);
    }
  }

  async #bootFailure(name, input, entry, err, started, t0) {
    const hash = err?.code === "bundle_hash";
    const message = hash ? t("runtime.browser.error.hash") : err?.code === "boot_missing" || err?.code === "restarts" ? err.message : t("runtime.browser.error.boot", { message: err?.message || String(err) });
    return this.#envelope({
      tool: name, args: input, entry, status: "failed", started, t0, summary: message,
      error: { type: "unavailable", message, hint: "Start the local runner (tcmstudio serve) and connect it in Settings, or reload the page." },
      text: `Not run: the browser's Python runtime is not available (${err?.message || err}). Say what this would need; do not substitute an approximation.`,
    });
  }

  #cancelled(job) {
    return this.#envelope({
      tool: job.name, args: job.input, entry: job.entry, status: "cancelled", started: job.started, t0: job.t0,
      summary: t("runtime.browser.cancelled"), text: "Not run to completion: the user stopped the call. There is no result.",
      error: { type: "runtime_error", message: "cancelled", hint: "" },
    });
  }

  #fail(job, error, summary) {
    return this.#envelope({
      tool: job.name, args: job.input, entry: job.entry, status: "failed", started: job.started, t0: job.t0, summary, error,
      text: `The tool failed to run in the browser: ${error.message}. There is no result.${error.hint ? ` ${error.hint}` : ""}`,
    });
  }

  /** An envelope (§3) for an outcome Python never produced: a cancel, a crash, a runtime that cannot start. */
  #envelope({ tool, args, entry, status, error, summary, text, started, t0 }) {
    const versions = this.#info?.versions || {};
    const via = tool === "call_tool" ? String(args?.tool || tool) : entry?.id || tool;
    const base = {
      ok: false, tool, via, status, duration_ms: Math.round(now() - t0), summary, text, result: null, citations: [],
      governance: { kind: entry?.kind || "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
      receipt: {
        where: "browser", runtime: this.#info?.runtime || null, device: "cpu", versions,
        composite_version: null, content_hash: null, audit_head: null, input_sha256: null, output_sha256: null,
        started_at: started, decided_by: "browser-runtime",
      },
      job: null, approval: null, error,
    };
    return sha256Hex(canonicalJson(args || {})).then((h) => { base.receipt.input_sha256 = h; return base; }, () => base);
  }

  #setStatus(status, { progress, message, error } = {}) {
    this.status = status;
    if (typeof progress === "number") this.progress = progress;
    if (message !== undefined) this.message = message;
    if (error !== undefined) this.error = error;
    this.#bus.emit("status", { status: this.status, progress: this.progress, message: this.message, error: this.error });
  }
}

function now() {
  return globalThis.performance?.now ? performance.now() : Date.now();
}

function codeError(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

export default BrowserRuntime;
