// The browser runtime (CONTRACTS §5, §6; docs/V2.md §13): the real psh + bioagent + tcmstudio Python, run by pinned
// Pyodide in module Web Workers (./pyodide.worker.js). It implements the RuntimeClient interface: kind "browser",
// status events with load progress, a lazy start(), info(), catalog() straight from the built runtime/catalog.json
// (tools can be offered before Python has loaded), call() → envelope with receipt.where "browser", device().
//
// A pool of workers (./pool.js): the primary (worker 0) runs stateful calls and boots first; stateless calls spill
// over to secondaries, spawned one at a time while calls wait and retired when idle. The pool's size comes from the
// device class (./device.js) and settings.computeBrowser. The page compiles Pyodide's WebAssembly once and gives every
// worker the compiled module.
//
// A stop interrupts the Python of that call through its worker's SharedArrayBuffer when the page is cross-origin
// isolated (KeyboardInterrupt → status "cancelled"); otherwise, or when Python does not stop within a grace period,
// that worker is terminated (the primary is started again). A Pyodide fatal error restarts the primary, or retires a
// secondary and shrinks the pool. A call that hands a hot loop to the page's accelerator (docs/V2.md §13.4) is
// finished in Python with the accelerator's result. A call always resolves to an envelope: failures are data.

import { Emitter } from "../core/events.js";
import { registerStrings, t } from "../core/i18n.js";
import { onSettingsChange } from "../core/settings.js";
import { canonicalJson, nowIso, sha256Hex } from "../core/util.js";
import { browserSettings, classify, detect, effective, holdWakeLock, quickFacts, workerCap } from "./device.js";
import { BOOT_TIMEOUT_MS, PoolWorker, idleMsFor, pickWorker, poolStats, wantsSpawn } from "./pool.js";

export { detectDevice } from "./device.js";

const CATALOG_SCHEMA = "tcmstudio.catalog/1";
const BOOT_SCHEMA = "tcmstudio.boot/1";
// Entries whose runs read or extend a project's audit chain or session files: their state is synced with IndexedDB.
const STATEFUL_KINDS = new Set(["skill", "clinic"]);
const STATEFUL_IDS = new Set(["system.audit_verify"]);
// Failures a call should not retry by itself (a retry would repeat them); an explicit start() still tries again.
const STICKY_BOOT_ERRORS = new Set(["bundle_hash", "boot_missing", "bad_boot", "restarts"]);
const RESTART_WINDOW_MS = 60_000;
const MAX_RESTARTS = 3;
// Boot stages a secondary may report while the runtime is ready (its own boot stages would read as a restart).
const SECONDARY_STAGES = new Set(["packages_on_demand", "ready"]);

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
  "runtime.browser.stage.ready": "就绪：Python 工具在本机 CPU 上运行（WebAssembly；每个 Python 进程单线程）",
  "runtime.browser.accel.running": "正在用浏览器加速器计算：{what}{pct}",
  "runtime.browser.accel.pct": "（{n}%）",
  "runtime.browser.accel.kernel.np-null-mt/1": "度匹配置换零分布",
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
  "runtime.browser.stage.ready": "Ready: Python tools run on this computer's CPU (WebAssembly; one thread per Python worker)",
  "runtime.browser.accel.running": "Computing with the browser accelerator: {what}{pct}",
  "runtime.browser.accel.pct": " ({n}%)",
  "runtime.browser.accel.kernel.np-null-mt/1": "degree-matched permutation null",
  "runtime.browser.error.unsupported": "This browser has no Web Worker or WebAssembly support, so Python tools cannot run in it; use the local runner.",
  "runtime.browser.error.not_built": "This site has no browser runtime (runtime/boot.json is missing); use the local runner.",
  "runtime.browser.error.boot": "The browser runtime could not start: {message}",
  "runtime.browser.error.hash": "The code bundle failed its check: its SHA-256 does not match boot.json, so it was not run. Reload the page; if it fails again, use the local runner.",
  "runtime.browser.error.fatal": "The Python runtime hit a fatal error and was restarted; this call has no result.",
  "runtime.browser.error.restarts": "The Python runtime kept failing, so it is no longer restarted automatically; reload the page or use the local runner.",
  "runtime.browser.error.timeout": "The call did not finish within {s} s; the runtime was stopped and started again.",
  "runtime.browser.cancelled": "Cancelled: the call was stopped before it finished",
});

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
  #moduleP = null;
  #index = null;
  #startP = null;
  #generation = 0;
  #seq = 0;
  #workers = [];
  #queue = [];
  #spawnTimer = null;
  #info = null;
  #deviceP = null;
  #restarts = [];
  #bootError = null;
  #packages = new Set();
  #durable = null;
  // the pool's size: the device class and the settings, less the secondaries that failed
  #facts;
  #class;
  #browser;
  #size = 1;
  #shrunk = 0;
  // the accelerator host (web/js/accel/host.js), created on first use; calls it is computing for
  #accelHostP = null;
  #retired = [];
  #accels = new Set();
  #wake = { count: 0, release: null };
  #offSettings = null;

  /**
   * opts: {bootUrl (absolute URL of runtime/boot.json), siteUrl? (default: the directory above boot.json),
   * workerUrl?, indexUrl? (overrides boot.json's Pyodide URL), persist: true (IDBFS for audit chains),
   * warm: true (build the native section after boot), callTimeoutMs: 600000, bootTimeoutMs: 90000,
   * interruptGraceMs: 3000, debug: false (enables the worker's test-only operations), Worker?, fetch?,
   * settings? (the app's settings: computeBrowser sizes the pool; later changes are followed),
   * workers? (a fixed pool size, for tests and test pages), device? (navigator facts instead of the navigator's),
   * spawnDelayMs: 250 (how long a call waits before a secondary is booted for it), idleMs? (how long an idle
   * secondary lives; default by device class), shareModule: true (compile
   * Pyodide's WebAssembly once on the page), accel: true | false, accelHost? (an AccelHost instead of
   * web/js/accel/host.js), importAccel? (() → module)}.
   */
  constructor(opts = {}) {
    this.#opts = {
      persist: true, warm: true, callTimeoutMs: 600_000, bootTimeoutMs: BOOT_TIMEOUT_MS, interruptGraceMs: 3000, debug: false,
      spawnDelayMs: 250, shareModule: true, accel: true, ...opts,
    };
    const loc = globalThis.location?.href || "http://localhost/";
    this.#bootUrl = new URL(opts.bootUrl || "/runtime/boot.json", loc).href;
    this.#siteUrl = new URL(opts.siteUrl || "../", this.#bootUrl).href;
    this.#facts = { ...quickFacts(globalThis.navigator), ...(opts.device || {}) };
    this.#class = classify(this.#facts);
    this.#browser = browserSettings(opts.settings?.computeBrowser);
    this.#resize();
    const W = this.#opts.Worker || globalThis.Worker;
    if (typeof W !== "function" || typeof globalThis.WebAssembly !== "object") {
      this.status = "offline";
      this.message = t("runtime.browser.error.unsupported");
      this.error = this.message;
    }
    if (opts.settings && opts.watchSettings !== false) {
      this.#offSettings = onSettingsChange(({ settings, changed }) => {
        if (changed.includes("computeBrowser") || changed.includes("*")) this.configure(settings.computeBrowser);
      });
    }
  }

  get label() { return t("core.browser.label"); }
  get interruptible() { return Boolean(this.#primary()?.interrupt); }

  onStatus(fn) { return this.#bus.on("status", fn); }
  /** fn(pool) whenever a worker boots, takes or finishes a call, or retires. */
  onPool(fn) { return this.#bus.on("pool", fn); }

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

  /** Load Pyodide and the bundle in the primary worker (once). Resolves with info() when ready; rejects when the
   * runtime cannot start. Secondaries are booted later, when calls wait for one. */
  start() {
    if (this.status === "offline" && !this.#bootP) return Promise.reject(new Error(this.message));
    if (this.status === "ready" && this.#primary()) return Promise.resolve(this.#info);
    if (!this.#startP) {
      if (this.#bootError?.code === "restarts") this.#restarts = [];
      this.#bootError = null;
      this.#startP = this.#spawn(false);
      this.#startP.catch(() => { this.#startP = null; });
    }
    return this.#startP;
  }

  /** Terminate every worker. The next call or start() boots the primary again (from the HTTP and Cache Storage caches). */
  stop() {
    const err = new Error("the runtime was stopped");
    this.#generation++;
    this.#terminateAll(err);
    this.#startP = null;
    this.#flushQueue(err);
    this.#setStatus("idle", { progress: 0, message: "" });
  }

  /** Follow settings.computeBrowser ({workers, gpu}): resize the pool and recreate the accelerator host. */
  configure(raw) {
    const next = browserSettings(raw);
    const changed = next.workers !== this.#browser.workers || next.gpu !== this.#browser.gpu;
    this.#browser = next;
    this.#shrunk = 0;
    this.#resize();
    for (const pw of this.#workers.filter((w) => !w.primary && !w.job && w.state === "ready")) {
      if (this.#alive() > this.#size) this.#drop(pw, new Error("the pool was made smaller"));
    }
    if (changed) this.#retireAccelHost();
    this.#emitPool();
    this.#pump();
    return this.pool();
  }

  /** The pool now: {size, live, busy, booting, cls, cap, shrunk, workers:[{index, primary, state, busy}]}. */
  pool() {
    return poolStats(this.#workers, { size: this.#size, cls: this.#class.cls, cap: workerCap(this.#facts), shrunk: this.#shrunk });
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
      pool: this.pool(),
      cross_origin_isolated: Boolean(globalThis.crossOriginIsolated),
      interruptible: this.interruptible,
      boot_ms: this.#info?.boot_ms ?? null,
      timings: this.#info?.timings || null,
      error: this.error,
    };
  }

  /** The device report (CONTRACTS §5) with the platform and the device class (docs/V2.md §13.1); never starts Python. */
  device() {
    if (!this.#deviceP) {
      this.#deviceP = detect();
      this.#deviceP.catch(() => { this.#deviceP = null; });
    }
    return this.#deviceP;
  }

  /** device() with what this page uses: the pool's size, the JS workers an accelerator may use, the GPU or not. */
  async computeDevice() {
    const d = await this.device();
    const eff = effective(d, this.#browser);
    return { ...d, python_workers: this.#size, js_workers: eff.js_workers, gpu_enabled: eff.gpu_enabled, settings: eff.settings };
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
    // the accelerator host loads while Python boots; a call only lists its kernels to Python
    const accelP = this.#accelContext();
    try {
      await this.start();
    } catch (err) {
      return this.#bootFailure(name, input, entry, err, started, t0);
    }
    const context = { project_id: ctx.project_id ?? null, conversation_id: ctx.conversation_id ?? null, approvals: Array.isArray(ctx.approvals) ? ctx.approvals : [], device: "cpu" };
    // only capabilities_status reports host facts; the WebGPU probe is not worth making for every call
    if (entry?.id === "system.capabilities") context.capabilities = await this.#capabilities();
    const accel = await accelP;
    if (accel) context.accel = accel;
    if (signal?.aborted) return fail("cancelled", null, t("runtime.browser.cancelled"), "Not run: the call was cancelled before it started.");
    const payload = {
      tool: name,
      arguments: input,
      context,
      packages: Array.isArray(entry?.pyodide_packages) ? entry.pyodide_packages : [],
      stateful: Boolean(entry && (STATEFUL_KINDS.has(entry.kind) || STATEFUL_IDS.has(entry.id))),
    };
    return new Promise((resolve) => {
      const job = { op: "call", payload, stateful: payload.stateful, signal, resolve, name, input, entry, started, t0 };
      if (signal) {
        job.onAbort = () => this.#abort(job);
        signal.addEventListener("abort", job.onAbort, { once: true });
      }
      this.#queue.push(job);
      this.#pump();
    });
  }

  /** Test pages only (opts.debug): have the primary worker induce a failure ("fatal": a Pyodide fatal error). */
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

  /**
   * Pyodide's WebAssembly compiled once, here, for every worker (docs/V2.md §13.2). Null when it cannot be (no
   * network, a server that does not answer): each worker then compiles its own, which works for one at a time.
   */
  #sharedModule() {
    if (this.#opts.shareModule === false || typeof globalThis.WebAssembly?.compile !== "function") return Promise.resolve(null);
    if (!this.#moduleP) {
      const compile = (async () => {
        const boot = await this.#boot();
        const index = withSlash(new URL(this.#opts.indexUrl || boot.pyodide.index_url, this.#siteUrl).href);
        const res = await this.#fetch(`${index}pyodide.asm.wasm`);
        if (!res?.ok) return null;
        if (typeof WebAssembly.compileStreaming === "function") {
          // a server that does not send application/wasm fails the streaming compile: compile the bytes instead
          try { return await WebAssembly.compileStreaming(res.clone()); } catch { /* below */ }
        }
        return await WebAssembly.compile(await res.arrayBuffer());
      })();
      let timer = null;
      const limit = new Promise((resolve) => { timer = setTimeout(() => resolve(null), this.#opts.bootTimeoutMs); });
      this.#moduleP = Promise.race([compile, limit]).catch(() => null).finally(() => clearTimeout(timer));
      this.#moduleP.then((m) => { if (!m) this.#moduleP = null; });
    }
    return this.#moduleP;
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
    return { browser: { device, pool: this.pool(), interruptible: this.interruptible, durable: this.#durable ?? this.#info?.persist?.durable ?? null, packages: [...this.#packages] } };
  }

  // --------------------------------------------------------------------------------------------------------- the pool

  #primary() {
    return this.#workers.find((w) => w.primary && !w.dead) || null;
  }

  #alive() {
    return this.#workers.filter((w) => !w.dead).length;
  }

  #resize() {
    const fixed = Number.isInteger(this.#opts.workers) && this.#opts.workers > 0 ? this.#opts.workers : null;
    const target = fixed ?? effective({ ...this.#facts, ...this.#class }, this.#browser).python_workers;
    this.#size = Math.max(1, target - this.#shrunk);
  }

  #newWorker(index, primary) {
    const W = this.#opts.Worker || globalThis.Worker;
    const url = this.#opts.workerUrl || new URL("./pyodide.worker.js", import.meta.url).href;
    return new PoolWorker({
      Worker: W, url, index, primary, isolated: Boolean(globalThis.crossOriginIsolated), nextId: () => ++this.#seq,
      onMessage: (pw, msg) => this.#onWorkerMessage(pw, msg),
      onCrash: () => { /* a crash fails what the worker was answering; #settle restarts or retires it */ },
    });
  }

  /** The compiled module first (a structured message), then init. Resolves with the init reply. */
  #init(pw, boot, module) {
    if (module) pw.post({ type: "wasm-module", module });
    return pw.send("init", {
      boot, siteUrl: this.#siteUrl, indexUrl: this.#opts.indexUrl || null, persist: pw.primary && this.#opts.persist !== false,
      debug: Boolean(this.#opts.debug), role: pw.primary ? "primary" : "secondary",
    }, this.#opts.bootTimeoutMs).done;
  }

  /** Boot the primary. A boot that times out is tried once more; any other failure stops here. */
  async #spawn(restart, attempt = 1) {
    const gen = ++this.#generation;
    this.#setStatus("loading", { progress: 1, message: t(restart ? "runtime.browser.stage.restart" : "runtime.browser.stage.start"), error: null });
    try {
      const boot = await this.#boot();
      if (gen !== this.#generation) throw new Error("superseded");
      // the page downloads and compiles Pyodide's WebAssembly first (once; later boots reuse it)
      this.#setStatus("loading", { progress: 2, message: t("runtime.browser.stage.pyodide", { version: boot.pyodide?.version || "" }) });
      const module = await this.#sharedModule();
      if (gen !== this.#generation) throw new Error("superseded");
      const pw = this.#newWorker(0, true);
      this.#workers.unshift(pw);
      this.#emitPool();
      const t0 = now();
      const reply = await this.#init(pw, boot, module);
      if (gen !== this.#generation || pw.dead) throw new Error("superseded");
      if (!reply.ok) {
        if (reply.timeout && attempt < 2) {
          this.#drop(pw, new Error("the worker did not boot in time"));
          return this.#spawn(true, attempt + 1);
        }
        throw codeError(reply.error?.type || "boot_error", reply.error?.message || "the worker could not start");
      }
      pw.state = "ready";
      pw.info = reply.result;
      this.#info = { ...reply.result, wall_ms: Math.round(now() - t0) };
      this.#durable = this.#info.persist?.durable ?? null;
      for (const p of this.#info.packages || []) this.#packages.add(p);
      this.#setStatus("ready", { progress: 100, message: t("runtime.browser.stage.ready"), error: null });
      if (this.#opts.warm) this.#queue.unshift({ op: "warm", payload: {}, resolve: () => {}, internal: true });
      this.#pump();
      return this.#info;
    } catch (err) {
      if (gen === this.#generation) {
        this.#terminateAll(err);
        const hash = err?.code === "bundle_hash";
        this.#bootError = err;
        const message = hash ? t("runtime.browser.error.hash") : err?.code === "boot_missing" ? err.message : t("runtime.browser.error.boot", { message: err?.message || String(err) });
        if (this.status !== "offline") this.#setStatus("error", { progress: 0, message, error: message });
        this.#flushQueue(err);
      }
      throw err;
    }
  }

  /** Boot one secondary (no persistence; it restores compiled modules but never saves them). */
  async #spawnSecondary(attempt = 1) {
    let pw = null;
    try {
      const boot = await this.#boot();
      const module = await this.#sharedModule();
      if (this.status !== "ready") return;
      const used = new Set(this.#workers.map((w) => w.index));
      let index = 1;
      while (used.has(index)) index++;
      pw = this.#newWorker(index, false);
      this.#workers.push(pw);
      this.#emitPool();
      const reply = await this.#init(pw, boot, module);
      if (pw.dead) return; // stopped while it booted
      if (reply.ok) {
        pw.state = "ready";
        pw.info = reply.result;
        this.#idle(pw);
        return;
      }
      this.#drop(pw, new Error(reply.error?.message || "the worker could not start"));
      if (reply.timeout && attempt < 2) {
        await this.#spawnSecondary(attempt + 1);
        return;
      }
      this.#shrink();
    } catch {
      if (pw) this.#drop(pw, new Error("the worker could not start"));
      this.#shrink();
    } finally {
      this.#pump();
    }
  }

  /** Hand waiting calls to free workers; boot a secondary when a stateless call keeps waiting. */
  #pump() {
    const primaryReady = this.status === "ready";
    for (let i = 0; i < this.#queue.length;) {
      const job = this.#queue[i];
      const pw = pickWorker(job, this.#workers, { primaryReady });
      if (!pw) { i++; continue; }
      this.#queue.splice(i, 1);
      this.#run(job, pw);
    }
    if (!this.#spawnTimer && wantsSpawn({ queue: this.#queue, workers: this.#workers, size: this.#size, primaryReady })) {
      this.#spawnTimer = setTimeout(() => {
        this.#spawnTimer = null;
        if (wantsSpawn({ queue: this.#queue, workers: this.#workers, size: this.#size, primaryReady: this.status === "ready" })) this.#spawnSecondary();
      }, this.#opts.spawnDelayMs);
    }
    this.#emitPool();
  }

  #run(job, pw) {
    pw.job = job;
    job.worker = pw;
    if (pw.idleTimer) clearTimeout(pw.idleTimer);
    pw.idleTimer = null;
    const { id, done } = pw.send(job.op, job.payload, job.internal ? 0 : this.#opts.callTimeoutMs);
    job.requestId = id;
    done.then((reply) => this.#settle(job, reply));
  }

  /** A worker finished a call: a secondary beyond the pool's size retires, others wait for the next call. */
  #idle(pw) {
    if (!pw.primary && !pw.dead) {
      if (this.#alive() > this.#size) {
        this.#drop(pw, new Error("the pool was made smaller"));
      } else {
        if (pw.idleTimer) clearTimeout(pw.idleTimer);
        pw.idleTimer = setTimeout(() => {
          if (!pw.job && !pw.dead) this.#drop(pw, new Error("idle"));
        }, this.#opts.idleMs ?? idleMsFor(this.#class.cls));
        pw.idleTimer.unref?.();
      }
    }
    this.#pump();
  }

  /** A worker failed (fatal, timed out): the primary is restarted; a secondary retires, and a fatal one shrinks the pool. */
  #failed(pw, reason) {
    if (pw.primary) {
      this.#restart(reason);
      return;
    }
    this.#drop(pw, new Error(`retired after ${reason}`));
    if (reason === "fatal") this.#shrink();
    this.#pump();
  }

  #shrink() {
    this.#shrunk++;
    this.#resize();
    this.#emitPool();
  }

  #settle(job, reply) {
    const pw = job.worker;
    if (!pw || pw.job !== job) return; // already answered (a hard cancel, a stop) and the worker replaced
    pw.job = null;
    this.#detach(job);
    if (job.internal) {
      job.resolve(reply);
      if (reply.fatal) this.#failed(pw, "fatal");
      else this.#idle(pw);
      return;
    }
    if (reply.terminated) return;
    if (reply.ok) {
      const env = reply.result;
      if (job.stateful && reply.persist && env?.receipt && reply.persist.durable === false && env.receipt.durable !== false) {
        env.receipt.durable = false;
        if (reply.persist.error) env.receipt.persist_error = reply.persist.error;
      }
      if (reply.persist && pw.primary) this.#durable = reply.persist.durable;
      for (const p of job.payload.packages || []) this.#packages.add(p);
      job.resolve(env);
      this.#idle(pw);
      return;
    }
    if (job.cancelRequested) {
      // KeyboardInterrupt surfaced outside the dispatcher (between calls into Python), or the accelerated step was
      // stopped: still a cancel
      job.resolve(this.#cancelled(job));
      if (reply.fatal) this.#failed(pw, "fatal");
      else this.#idle(pw);
      return;
    }
    if (reply.timeout) {
      const s = Math.round(this.#opts.callTimeoutMs / 1000);
      job.resolve(this.#fail(job, { type: "timeout", message: t("runtime.browser.error.timeout", { s }), hint: "Run it on the local runner, or with smaller input." }, t("runtime.browser.error.timeout", { s })));
      this.#failed(pw, "timeout");
      return;
    }
    if (reply.fatal) {
      job.resolve(this.#fail(job, { type: "runtime_error", message: `${t("runtime.browser.error.fatal")} (${reply.error?.message || "fatal error"})`, hint: "Retry once; if it fails again, run it on the local runner." }, t("runtime.browser.error.fatal")));
      this.#failed(pw, "fatal");
      return;
    }
    const type = reply.error?.type === "unavailable" ? "unavailable" : "runtime_error";
    job.resolve(this.#fail(job, { type, message: reply.error?.message || "the call failed", hint: type === "unavailable" ? "Run it on the local runner." : "" }, reply.error?.message || "the call failed"));
    this.#idle(pw);
  }

  #abort(job) {
    const queued = this.#queue.indexOf(job);
    if (queued >= 0) {
      this.#queue.splice(queued, 1);
      this.#detach(job);
      job.resolve(this.#cancelled(job));
      this.#emitPool();
      return;
    }
    const pw = job.worker;
    if (!pw || pw.job !== job || job.cancelRequested) return;
    job.cancelRequested = true;
    if (job.accel) {
      // the page is computing for this call: stop that and tell the worker, which answers "cancelled" without
      // going back into Python
      job.accel.controller.abort();
      job.accel.answer({ ok: false, cancelled: true, error: "cancelled" });
      job.graceTimer = setTimeout(() => this.#hardCancel(job), this.#opts.interruptGraceMs);
    } else if (pw.signalInterrupt()) {
      job.graceTimer = setTimeout(() => this.#hardCancel(job), this.#opts.interruptGraceMs);
    } else {
      this.#hardCancel(job);
    }
  }

  #hardCancel(job) {
    const pw = job.worker;
    if (!pw || pw.job !== job) return;
    pw.job = null;
    this.#detach(job);
    job.resolve(this.#cancelled(job));
    if (pw.primary) {
      this.#restart("cancel", { counts: false });
    } else {
      this.#drop(pw, new Error("cancelled"));
      this.#pump();
    }
  }

  /** Clear what ties a finished job to the page: its grace timer, its abort listener, its accelerated step. */
  #detach(job) {
    if (job.graceTimer) clearTimeout(job.graceTimer);
    job.graceTimer = null;
    if (job.onAbort) job.signal?.removeEventListener("abort", job.onAbort);
    job.onAbort = null;
    if (job.accel) job.accel.controller.abort();
  }

  /** Restart the primary (a fatal error, a timeout, a hard cancel); a primary that keeps failing is not restarted. */
  #restart(reason, { counts = true } = {}) {
    const nowMs = Date.now();
    if (counts) {
      this.#restarts = this.#restarts.filter((x) => nowMs - x < RESTART_WINDOW_MS);
      this.#restarts.push(nowMs);
    }
    const primary = this.#primary();
    if (primary) this.#drop(primary, new Error(`restarted after ${reason}`));
    this.#startP = null;
    if (this.#restarts.length > MAX_RESTARTS) {
      const message = t("runtime.browser.error.restarts");
      this.#bootError = codeError("restarts", message);
      this.#terminateAll(this.#bootError);
      this.#setStatus("error", { progress: 0, message, error: message });
      this.#flushQueue(this.#bootError);
      return;
    }
    this.#startP = this.#spawn(true);
    this.#startP.catch(() => { this.#startP = null; });
  }

  /** Stop one worker. Its running call, if any, is answered here (cancelled, or failed with `err`). */
  #drop(pw, err) {
    const job = pw.job;
    pw.job = null;
    pw.terminate(err);
    this.#workers = this.#workers.filter((w) => w !== pw);
    if (job) {
      this.#detach(job);
      if (job.internal) job.resolve({ ok: false, terminated: true, error: { type: "runtime_error", message: err?.message || "terminated" } });
      else job.resolve(job.cancelRequested ? this.#cancelled(job) : this.#fail(job, { type: "runtime_error", message: err?.message || "the runtime was stopped", hint: "" }, err?.message || "stopped"));
    }
    this.#emitPool();
  }

  #terminateAll(err) {
    if (this.#spawnTimer) clearTimeout(this.#spawnTimer);
    this.#spawnTimer = null;
    for (const pw of [...this.#workers]) this.#drop(pw, err);
  }

  #flushQueue(err) {
    const queued = this.#queue.splice(0);
    for (const job of queued) {
      if (job.internal) {
        job.resolve({ ok: false, error: { type: "unavailable", message: err?.message || String(err) } });
        continue;
      }
      this.#detach(job);
      this.#bootFailure(job.name, job.input, job.entry, err, job.started, job.t0).then(job.resolve);
    }
    this.#emitPool();
  }

  #onWorkerMessage(pw, msg) {
    if (msg.type === "progress") {
      const message = t(`runtime.browser.stage.${msg.stage}`, msg.vars || {});
      const text = message === `runtime.browser.stage.${msg.stage}` ? msg.message : message;
      if (pw.primary && pw.state === "booting") {
        if (this.status === "loading") this.#setStatus("loading", { progress: Math.max(this.progress, msg.progress), message: text });
      } else if (this.status === "ready" && (pw.primary || SECONDARY_STAGES.has(msg.stage))) {
        this.#setStatus("ready", { progress: 100, message: text });
      }
      return;
    }
    if (msg.type === "accelerate") this.#onAccelerate(pw, msg);
  }

  // -------------------------------------------------------------------------------------------------- accelerators

  /** context.accel for a call: the kernels the page's accelerator host offers, and the engine preference. */
  async #accelContext() {
    const host = await this.#accelHost();
    if (!host) return null;
    let kernels = [];
    try { kernels = [...(host.kernels?.() || [])].filter((k) => typeof k === "string" && k); } catch { kernels = []; }
    if (!kernels.length) return null;
    return { kernels, prefer: this.#browser.gpu === "off" ? "cpu" : this.#browser.gpu === "on" ? "gpu" : "auto" };
  }

  /** The AccelHost of web/js/accel/host.js ({kernels(), run(kernel, input, {signal, onProgress})}), or null. */
  #accelHost() {
    if (!this.#accelHostP) {
      this.#accelHostP = (async () => {
        if (this.#opts.accel === false) return null;
        if (this.#opts.accelHost) return this.#opts.accelHost;
        let mod = null;
        try {
          mod = this.#opts.importAccel ? await this.#opts.importAccel() : await import("../accel/host.js");
        } catch {
          return null; // this page has no accelerator: Python computes everything
        }
        if (typeof mod?.createAccelHost !== "function") return null;
        const device = await this.computeDevice();
        const s = { ...this.#browser };
        return mod.createAccelHost({ device, settings: { ...s, compute: { browser: { ...s } } } }) || null;
      })().catch(() => null);
    }
    return this.#accelHostP;
  }

  /** New settings: the next call creates a new host; the old one is disposed once nothing runs on it. */
  #retireAccelHost() {
    const old = this.#accelHostP;
    this.#accelHostP = null;
    if (!old) return;
    old.then((host) => {
      if (host && host !== this.#opts.accelHost) this.#retired.push(host);
      this.#disposeRetired();
    });
  }

  #disposeRetired() {
    if (this.#accels.size) return;
    for (const host of this.#retired.splice(0)) {
      try { (host.dispose || host.close)?.call(host); } catch { /* nothing to free */ }
    }
  }

  /**
   * A worker's call handed a kernel to the page ({type:"accelerate", id, token, kernel, input}): run it on the
   * accelerator host, with the call's Stop as its AbortSignal, and answer {type:"accelerated", id, token, ok,
   * result | error}. An error makes Python compute natively; a stop makes the worker answer "cancelled".
   */
  async #onAccelerate(pw, msg) {
    const send = (body) => pw.post(JSON.stringify({ type: "accelerated", id: msg.id, token: msg.token, ...body }));
    const job = pw.job;
    if (!job || job.requestId !== msg.id) {
      send({ ok: false, error: "no call is waiting for this result" });
      return;
    }
    if (job.cancelRequested) {
      send({ ok: false, cancelled: true, error: "cancelled" });
      return;
    }
    const controller = new AbortController();
    const accel = { kernel: String(msg.kernel || ""), fraction: null, controller, answered: false };
    accel.answer = (body) => {
      if (accel.answered) return;
      accel.answered = true;
      send(body);
    };
    job.accel = accel;
    this.#accels.add(accel);
    this.#accelStatus();
    const release = await this.#holdWake();
    try {
      const host = await this.#accelHost();
      if (!host) throw new Error("this page has no accelerator");
      const result = await host.run(accel.kernel, msg.input, {
        signal: controller.signal,
        onProgress: (p) => {
          const f = fractionOf(p);
          if (f === null || controller.signal.aborted) return;
          accel.fraction = f;
          this.#accelStatus();
        },
      });
      if (controller.signal.aborted) accel.answer({ ok: false, cancelled: true, error: "cancelled" });
      else accel.answer({ ok: true, result: result ?? null });
    } catch (err) {
      accel.answer(controller.signal.aborted && job.cancelRequested
        ? { ok: false, cancelled: true, error: "cancelled" }
        : { ok: false, error: String(err?.message || err || "the accelerator failed") });
    } finally {
      release();
      if (job.accel === accel) job.accel = null;
      this.#accels.delete(accel);
      this.#accelStatus();
      this.#disposeRetired();
    }
  }

  /** One screen wake lock for however many accelerated steps run at once. */
  async #holdWake() {
    const w = this.#wake;
    w.count++;
    if (w.count === 1) {
      const pending = holdWakeLock();
      w.release = async () => (await pending)();
    }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      w.count--;
      if (w.count === 0 && w.release) {
        const r = w.release;
        w.release = null;
        r();
      }
    };
  }

  #accelStatus() {
    if (this.status !== "ready") return;
    const accel = [...this.#accels].at(-1);
    if (!accel) {
      this.#setStatus("ready", { progress: 100, message: t("runtime.browser.stage.ready") });
      return;
    }
    const key = `runtime.browser.accel.kernel.${accel.kernel}`;
    const named = t(key);
    const what = named === key ? accel.kernel : named;
    const pct = accel.fraction === null ? "" : t("runtime.browser.accel.pct", { n: Math.round(accel.fraction * 100) });
    this.#setStatus("ready", { progress: 100, message: t("runtime.browser.accel.running", { what, pct }) });
  }

  // ------------------------------------------------------------------------------------------------------ envelopes

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
    this.#bus.emit("status", { status: this.status, progress: this.progress, message: this.message, error: this.error, pool: this.pool() });
  }

  #emitPool() {
    this.#bus.emit("pool", this.pool());
  }
}

/** A progress report from an accelerator as a fraction 0..1: a number (a fraction, or a percentage), or an object
 * with fraction | progress | done/total. Null when it says nothing usable. */
function fractionOf(p) {
  let f = null;
  if (typeof p === "number") f = p > 1 ? p / 100 : p;
  else if (p && typeof p === "object") {
    if (typeof p.fraction === "number") f = p.fraction;
    else if (typeof p.progress === "number") f = p.progress > 1 ? p.progress / 100 : p.progress;
    else if (typeof p.done === "number" && typeof p.total === "number" && p.total > 0) f = p.done / p.total;
  }
  return f === null || !Number.isFinite(f) ? null : Math.max(0, Math.min(1, f));
}

function now() {
  return globalThis.performance?.now ? performance.now() : Date.now();
}

function withSlash(url) {
  return url.endsWith("/") ? url : `${url}/`;
}

function codeError(code, message) {
  const e = new Error(message);
  e.code = code;
  return e;
}

export default BrowserRuntime;
