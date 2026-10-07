// The two runtimes a tool call can go to: the browser (Pyodide in a Web Worker, ./browser.js) and the local runner
// (./runner.js). Both implement the RuntimeClient interface of CONTRACTS §6.

import { Emitter } from "../core/events.js";
import { t } from "../core/i18n.js";
import { isLoopbackUrl, nowIso, trimSlash } from "../core/util.js";
import { DEFAULT_RUNNER_URL, RunnerRuntime } from "./runner.js";

export { RunnerRuntime } from "./runner.js";

/**
 * Create both runtimes from the settings. The browser runtime starts lazily (on its first call or when the UI asks).
 * The runner is probed in the background only when that cannot surprise the user with the browser's local-network
 * prompt: the page is served by the runner itself, or it was paired before (a token is saved).
 * opts: {runner: RunnerRuntime options, browser: BrowserRuntime options, autoConnect: false to never probe,
 * importBrowser: () => module (tests)}.
 */
export async function createRuntimes(settings = {}, opts = {}) {
  const browser = await createBrowserRuntime(settings, opts);
  const runner = new RunnerRuntime({ url: settings.runner?.url || DEFAULT_RUNNER_URL, token: settings.runner?.token || "", ...(opts.runner || {}) });
  if (opts.autoConnect !== false && shouldAutoConnect(settings, opts.location ?? globalThis.location)) {
    runner.start().catch(() => { /* the status says why; the UI offers Connect */ });
  }
  return { browser, runner };
}

export function shouldAutoConnect(settings = {}, location = globalThis.location) {
  const url = trimSlash(settings.runner?.url || DEFAULT_RUNNER_URL);
  let sameOrigin = false;
  try { sameOrigin = Boolean(location?.origin) && new URL(url).origin === location.origin; } catch { sameOrigin = false; }
  if (sameOrigin) return true;
  return Boolean(settings.runner?.token) && isLoopbackUrl(url);
}

async function createBrowserRuntime(settings, opts) {
  let mod = null;
  try {
    mod = opts.importBrowser ? await opts.importBrowser() : await import("./browser.js");
  } catch (err) {
    return new UnavailableRuntime("browser", t("core.browser.missing"), err);
  }
  const Ctor = mod?.BrowserRuntime || mod?.default;
  if (typeof Ctor !== "function") return new UnavailableRuntime("browser", t("core.browser.missing"));
  let bootUrl = null;
  try { bootUrl = new URL("../../runtime/boot.json", import.meta.url).href; } catch { bootUrl = null; }
  try {
    return new Ctor({ bootUrl, settings, ...(opts.browser || {}) });
  } catch (err) {
    return new UnavailableRuntime("browser", t("core.browser.missing"), err);
  }
}

/**
 * A runtime that cannot run anything (its module failed to load). It keeps the RuntimeClient shape so the router
 * and the UI need no special case; every call is a failed envelope that says why.
 */
export class UnavailableRuntime {
  constructor(kind, message, cause = null) {
    this.kind = kind;
    this.label = kind === "browser" ? t("core.browser.label") : kind;
    this.status = "offline";
    this.message = message;
    this.cause = cause;
    this._bus = new Emitter();
  }

  onStatus(fn) { return this._bus.on("status", fn); }
  async start() { throw new Error(this.message); }
  async info() { return { kind: this.kind, status: this.status, message: this.message }; }
  async catalog() { return null; }
  async device() { return null; }

  async call(tool) {
    return {
      ok: false, tool, via: tool, status: "failed", duration_ms: 0, summary: this.message,
      text: `Not run: ${this.message}`, result: null, citations: [],
      governance: { kind: "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [] },
      receipt: { where: this.kind === "runner" ? "runner" : "browser", runtime: null, device: null, versions: {}, composite_version: null, content_hash: null, audit_head: null, input_sha256: null, output_sha256: null, started_at: nowIso() },
      job: null, approval: null, error: { type: "unavailable", message: this.message, hint: "" },
    };
  }
}
