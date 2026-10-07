// The local runner client (CONTRACTS §4): `tcmstudio serve` on 127.0.0.1, the user's own CPU or GPU. Every /api/*
// call but /api/health carries the pairing token (X-TCM-Token, or ?token= for EventSource and file links).
//
// An https page that calls 127.0.0.1 makes Chrome (142+) ask for the Local Network Access permission ("apps on this
// device"). The first, user-initiated connect waits for that answer (start({interactive:true})); background probes
// time out quickly and never run before the user has paired once. Failures are described in words the user can act
// on: the runner is not running, the browser blocked local access, the page origin is not allowed, the token.

import { Emitter } from "../core/events.js";
import { t } from "../core/i18n.js";
import { fetchWithTimeout, fromBase64Url, isLoopbackUrl, trimSlash } from "../core/util.js";

export const DEFAULT_RUNNER_URL = "http://127.0.0.1:8765";
const DEFAULT_ORIGINS = ["https://science.impf.ai"];

export class RunnerError extends Error {
  constructor(message, { code = "error", status = 0, type = "" } = {}) {
    super(message);
    this.name = "RunnerError";
    this.code = code;
    this.status = status;
    this.type = type;
  }
}

export class RunnerRuntime {
  /**
   * opts: {url, token, fetch, EventSource, XMLHttpRequest, probeTimeoutMs, interactiveTimeoutMs, callTimeoutMs,
   * permissions (navigator.permissions), location}.
   */
  constructor(opts = {}) {
    this.kind = "runner";
    this.url = trimSlash(opts.url || DEFAULT_RUNNER_URL);
    this.token = opts.token || "";
    this.status = "idle";
    this.error = null;
    this._info = null;
    this._fetch = opts.fetch || ((...a) => globalThis.fetch(...a));
    this._EventSource = opts.EventSource || globalThis.EventSource;
    this._XHR = opts.XMLHttpRequest || globalThis.XMLHttpRequest;
    this._permissions = opts.permissions ?? globalThis.navigator?.permissions;
    this._location = opts.location ?? globalThis.location;
    this.probeTimeoutMs = opts.probeTimeoutMs ?? 3000;
    this.interactiveTimeoutMs = opts.interactiveTimeoutMs ?? 60000;
    this.callTimeoutMs = opts.callTimeoutMs ?? 0;
    this._bus = new Emitter();
    this._streams = new Set();
    const self = this;

    this.jobs = {
      submit: ({ kind, params = {}, project_id, submission_id } = {}) =>
        self.#json("/api/jobs", { method: "POST", body: { kind, params, ...(project_id ? { project_id } : {}), ...(submission_id ? { submission_id } : {}) } }),
      get: (id) => self.#json(`/api/jobs/${encodeURIComponent(id)}`),
      list: ({ project_id, state } = {}) => {
        const q = new URLSearchParams();
        if (project_id) q.set("project_id", project_id);
        if (state) q.set("state", state);
        const qs = q.toString();
        return self.#json(`/api/jobs${qs ? `?${qs}` : ""}`);
      },
      cancel: (id) => self.#json(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
      files: (id) => self.#json(`/api/jobs/${encodeURIComponent(id)}/files`),
      /** Follow a job's events (state|log|progress|artefact|done); onEvent({type, data}). Returns close(). */
      events: (id, onEvent) => self.#events(id, onEvent),
    };

    this.settings = {
      get: () => self.#json("/api/settings"),
      put: (s) => self.#json("/api/settings", { method: "PUT", body: s }),
    };
  }

  get label() {
    let host = this.url;
    try { host = new URL(this.url).host; } catch { /* keep the raw url */ }
    return `${t("core.runner.label")} @${host}`;
  }

  onStatus(fn) {
    return this._bus.on("status", fn);
  }

  #set(status, extra = {}) {
    this.status = status;
    this.error = extra.error || (["ready", "connecting", "idle"].includes(status) ? null : this.error);
    this._bus.emit("status", { status, error: this.error, info: this._info, message: extra.message || this.error?.message || "" });
  }

  /** Set the runner's address and token (from a pairing link or the compute panel). */
  pair(url, token) {
    const u = trimSlash(url || this.url);
    if (!/^https?:\/\//i.test(u)) throw new RunnerError(t("core.runner.not_running", { url: u }), { code: "bad_url" });
    this.url = u;
    this.token = String(token || "");
    this._info = null;
    this.error = null;
    this.#set("idle");
    return this;
  }

  /**
   * Connect: GET /api/health (no token), then /api/info with it. `interactive` (a button press) waits long enough for
   * the browser's permission prompt; a background probe gives up after a few seconds. Resolves with the info.
   */
  async start({ interactive = false } = {}) {
    this.#set("connecting");
    const timeout = interactive ? this.interactiveTimeoutMs : this.probeTimeoutMs;
    let health;
    try {
      const r = await fetchWithTimeout(this._fetch, `${this.url}/api/health`, { headers: { Accept: "application/json" } }, timeout);
      health = await r.json().catch(() => ({}));
      if (!r.ok || health.name !== "tcmstudio") {
        throw new RunnerError(t("core.runner.not_running", { url: this.url }), { code: "not_runner", status: r.status });
      }
    } catch (err) {
      const e = err instanceof RunnerError ? err : await this.describe(err);
      this.#set(e.code === "lna_denied" || e.code === "mixed_content" || e.code === "cors" ? "error" : "offline", { error: e });
      throw e;
    }
    if (health.token_required && !this.token) {
      const e = new RunnerError(t("core.runner.token"), { code: "token", status: 401 });
      this.#set("error", { error: e });
      throw e;
    }
    try {
      this._info = await this.#json("/api/info", { timeout, keepStatus: true });
    } catch (err) {
      this.#set(err.code === "token" ? "error" : "offline", { error: err });
      throw err;
    }
    this.#set("ready");
    return this._info;
  }

  stop() {
    for (const close of this._streams) close();
    this._streams.clear();
    this.#set("idle");
  }

  async info() {
    if (!this._info) this._info = await this.#json("/api/info");
    return this._info;
  }

  catalog() { return this.#json("/api/catalog"); }
  device() { return this.#json("/api/devices"); }
  kinds() { return this.#json("/api/kinds"); }
  uploads() { return this.#json("/api/uploads"); }
  localModels() { return this.#json("/api/llm/local"); }

  /**
   * POST /api/call → the envelope. Never throws: a runner that cannot be reached or answers with an error gives a
   * failed envelope (error.type "unavailable" or "runtime_error") with the reason.
   */
  async call(tool, args = {}, ctx = {}) {
    const context = {};
    if (ctx.project_id ?? ctx.projectId) context.project_id = ctx.project_id ?? ctx.projectId;
    if (ctx.conversation_id ?? ctx.conversationId) context.conversation_id = ctx.conversation_id ?? ctx.conversationId;
    if (ctx.approvals?.length) context.approvals = ctx.approvals;
    const started = Date.now();
    try {
      const env = await this.#json("/api/call", { method: "POST", body: { tool, arguments: args, context }, signal: ctx.signal, timeout: this.callTimeoutMs });
      if (env && typeof env === "object") {
        env.receipt = { where: "runner", ...(env.receipt || {}) };
        return env;
      }
      throw new RunnerError("the runner returned no envelope", { code: "bad_reply" });
    } catch (err) {
      if (err?.name === "AbortError") throw err;
      const unavailable = ["not_running", "lna_denied", "mixed_content", "cors", "timeout", "token"].includes(err.code);
      return {
        ok: false, tool, via: tool, status: "failed", duration_ms: Date.now() - started,
        summary: err.message, text: `Not run: the local runner ${unavailable ? "is not reachable" : "returned an error"} (${err.message}).`,
        result: null, citations: [],
        governance: { kind: "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
        receipt: { where: "runner", runtime: "tcmstudio", device: null, versions: {}, composite_version: null, content_hash: null, audit_head: null, input_sha256: null, output_sha256: null, started_at: new Date(started).toISOString() },
        job: null, approval: null,
        error: { type: unavailable ? "unavailable" : "runtime_error", message: err.message, hint: unavailable ? "connect the local runner" : "" },
      };
    }
  }

  /** A link to a job's output file (the token rides in the query: links and <a download> cannot set headers). */
  fileUrl(jobId, path) {
    const p = String(path || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
    const u = `${this.url}/api/jobs/${encodeURIComponent(jobId)}/files/${p}`;
    return this.token ? `${u}?token=${encodeURIComponent(this.token)}` : u;
  }

  /**
   * Upload a File or Blob (raw body, streamed by the browser) with progress: onProgress({loaded, total, fraction}).
   * The name goes in X-Filename, percent-encoded UTF-8 (HTTP headers cannot carry Chinese as is).
   */
  upload(file, { onProgress, signal, name } = {}) {
    const XHR = this._XHR;
    if (!XHR) return Promise.reject(new RunnerError("uploads need XMLHttpRequest", { code: "unsupported" }));
    return new Promise((resolve, reject) => {
      const xhr = new XHR();
      xhr.open("POST", `${this.url}/api/uploads`);
      xhr.setRequestHeader("X-Filename", encodeURIComponent(name || file.name || "upload.bin"));
      xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      if (this.token) xhr.setRequestHeader("X-TCM-Token", this.token);
      if (xhr.upload && onProgress) {
        xhr.upload.onprogress = (e) => onProgress({ loaded: e.loaded, total: e.total || file.size || 0, fraction: e.total ? e.loaded / e.total : null });
      }
      xhr.onload = () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText || "null"); } catch { body = null; }
        if (xhr.status >= 200 && xhr.status < 300 && body) { onProgress?.({ loaded: file.size || 0, total: file.size || 0, fraction: 1 }); resolve(body); }
        else reject(httpError(xhr.status, body, xhr.responseText));
      };
      xhr.onerror = () => reject(new RunnerError(t("core.runner.not_running", { url: this.url }), { code: "not_running" }));
      xhr.onabort = () => reject(abortError());
      if (signal) {
        if (signal.aborted) { reject(abortError()); return; }
        signal.addEventListener("abort", () => xhr.abort(), { once: true });
      }
      xhr.send(file);
    });
  }

  /** Turn a failed fetch into the reason the user can act on (async: it may ask the Permissions API). */
  async describe(err) {
    return describeConnectError(err, { url: this.url, location: this._location, permission: await lnaPermission(this._permissions) });
  }

  async #json(path, { method = "GET", body, signal, timeout = 0, keepStatus = false } = {}) {
    const headers = { Accept: "application/json" };
    if (this.token) headers["X-TCM-Token"] = this.token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let r;
    try {
      r = await fetchWithTimeout(this._fetch, `${this.url}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal }, timeout);
    } catch (err) {
      if (err?.name === "AbortError") throw err;
      const e = await this.describe(err);
      if (!keepStatus && this.status === "ready") this.#set("offline", { error: e });
      throw e;
    }
    const text = await r.text().catch(() => "");
    let j = null;
    try { j = text ? JSON.parse(text) : null; } catch { j = null; }
    if (!r.ok) {
      const e = httpError(r.status, j, text);
      if (e.code === "token" && !keepStatus) this.#set("error", { error: e });
      throw e;
    }
    return j;
  }

  #events(id, onEvent) {
    const ES = this._EventSource;
    if (!ES) throw new RunnerError("job events need EventSource", { code: "unsupported" });
    const url = `${this.url}/api/jobs/${encodeURIComponent(id)}/events${this.token ? `?token=${encodeURIComponent(this.token)}` : ""}`;
    const es = new ES(url);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      try { es.close(); } catch { /* closed */ }
      this._streams.delete(close);
    };
    const deliver = (type) => (e) => {
      let data = null;
      try { data = JSON.parse(e.data); } catch { data = e.data ?? null; }
      try { onEvent({ type, data }); } catch (err) { (globalThis.reportError || console.error)(err); }
      if (type === "done") close();
    };
    for (const type of ["state", "log", "progress", "artefact", "done"]) es.addEventListener(type, deliver(type));
    es.onmessage = deliver("message");
    es.onerror = () => {
      // EventSource reconnects by itself; tell the caller only when it gave up
      if (es.readyState === 2 && !closed) { onEvent({ type: "error", data: { message: t("core.runner.not_running", { url: this.url }) } }); close(); }
    };
    this._streams.add(close);
    return close;
  }
}

function abortError() {
  try { return new DOMException("aborted", "AbortError"); } catch { const e = new Error("aborted"); e.name = "AbortError"; return e; }
}

function httpError(status, body, text) {
  const e = body?.error;
  const message = (typeof e === "string" ? e : e?.message) || body?.message || String(text || "").slice(0, 300) || `HTTP ${status}`;
  const type = (typeof e === "object" && e?.type) || "";
  if (status === 401 || (status === 403 && /token/i.test(`${type} ${message}`))) {
    return new RunnerError(t("core.runner.token"), { code: "token", status, type });
  }
  if (status === 403) return new RunnerError(message, { code: "forbidden", status, type });
  return new RunnerError(t("core.runner.http", { status, message }), { code: "http", status, type });
}

// ------------------------------------------------------------------------------------------------- pairing

/**
 * Read '#pair=<base64url(JSON{url,token})>'. Only a loopback runner is accepted from a link: a link that pointed the
 * page at someone else's machine would send it your tool calls and model keys.
 */
export function parsePairFragment(hash) {
  const m = /(?:^#|[#&])pair=([A-Za-z0-9_\-=]+)/.exec(String(hash || ""));
  if (!m) return null;
  let obj;
  try { obj = JSON.parse(fromBase64Url(m[1])); } catch { return null; }
  if (!obj || typeof obj !== "object") return null;
  const url = trimSlash(obj.url || "");
  const token = typeof obj.token === "string" ? obj.token : "";
  if (!/^https?:\/\//i.test(url) || !isLoopbackUrl(url)) return null;
  return { url, token };
}

/** Take a pairing from the page URL and remove the fragment, so the token is not left in history or bookmarks. */
export function takePairing({ location = globalThis.location, history = globalThis.history } = {}) {
  if (!location?.hash || !location.hash.includes("pair=")) return null;
  const pairing = parsePairFragment(location.hash);
  try {
    const rest = location.hash.replace(/(^#|&)pair=[^&]*/, "").replace(/^#&/, "#");
    const clean = `${location.pathname || "/"}${location.search || ""}${rest && rest !== "#" ? rest : ""}`;
    history?.replaceState?.(history.state ?? null, "", clean);
  } catch { /* cannot rewrite the URL: the pairing still works */ }
  return pairing;
}

/** Chrome's Local Network Access permission for this site: "granted" | "denied" | "prompt" | "unknown". */
export async function lnaPermission(permissions = globalThis.navigator?.permissions) {
  if (!permissions?.query) return "unknown";
  for (const name of ["loopback-network", "local-network-access", "local-network"]) {
    try {
      const st = await permissions.query({ name });
      if (st?.state) return st.state;
    } catch { /* not a permission this browser knows */ }
  }
  return "unknown";
}

/**
 * Why a fetch to the runner failed, as {code, message}: mixed_content (an https page and an http address on another
 * machine), lna_denied (the browser's local-access permission is off), timeout, cors (this page's origin is not one
 * the runner allows by default), or not_running.
 */
export function describeConnectError(err, { url = DEFAULT_RUNNER_URL, location = globalThis.location, permission = "unknown" } = {}) {
  const pageProto = location?.protocol || "";
  const origin = location?.origin || "";
  let target;
  try { target = new URL(url); } catch { target = null; }
  if (pageProto === "https:" && target?.protocol === "http:" && !isLoopbackUrl(url)) {
    return new RunnerError(t("core.runner.mixed_content"), { code: "mixed_content" });
  }
  if (permission === "denied") return new RunnerError(t("core.runner.lna_denied"), { code: "lna_denied" });
  if (err?.name === "TimeoutError") return new RunnerError(t("core.runner.timeout", { url }), { code: "timeout" });
  const known = !origin || origin === "null" || DEFAULT_ORIGINS.includes(origin) || isLoopbackUrl(origin) || (target && origin === target.origin);
  if (!known) return new RunnerError(`${t("core.runner.not_running", { url })} ${t("core.runner.cors", { origin })}`, { code: "cors" });
  return new RunnerError(t("core.runner.not_running", { url }), { code: "not_running" });
}

/** The sentence to show before the first connect from an https page, which triggers the browser's prompt. */
export function lnaNotice(url = DEFAULT_RUNNER_URL, location = globalThis.location) {
  return location?.protocol === "https:" && isLoopbackUrl(url) ? t("core.runner.lna_prompt") : "";
}
