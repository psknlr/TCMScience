// The tool router: where each call runs (the browser's Pyodide worker or the local runner), which approvals it needs
// first, and an envelope (CONTRACTS §3) for every outcome — including the ones decided here, before anything runs:
// needs_approval, refused (the user said no), and failed/unavailable or network_off. A call never throws.
//
// Placement: settings.compute "browser" or "runner" is honoured strictly (a tool that cannot run there is unavailable,
// with the reason). "auto" sends everything the runner has to the runner while it is connected (one place per project:
// native speed, the durable audit chain, the user's devices), and what the browser can run to the browser otherwise.
//
// Approvals (DESIGN §3.3, ARCHITECTURE decision 5): entries marked confirm (jobs among them), network entries (which
// also need the project's web access on), sending data to a third party (allow_remote), and the first runner call of
// each project. The answer is "once", "project" (remembered in the project record) or "deny".

import { localCall, isLocalTool } from "./catalog.js";
import { t } from "./i18n.js";
import { canonicalJson, clip, isAbort, isPlainObject, nowIso, sha256Hex } from "./util.js";

export const TEXT_LIMIT = 16000;
const RUNNER_KEY = "runner";

const SIGN = "Signing a clinic draft is the licensed practitioner's own act; it is never done by a tool call.";
const REGISTRY = "Registry releases and skill promotion are reviewed acts of the maintainers, not tool calls.";
const SHELL = "Studio offers no raw shell: jobs run only the fixed command of a registered job kind.";
/**
 * Acts reserved for a person (tcmstudio.catalog.NEVER_OFFERED). They are never catalog entries; called by name, here
 * or through call_tool, they are refused with HUMAN_ONLY, as the runner's dispatcher refuses them, instead of being
 * reported as an unknown tool the model might go and search for. A catalog's `never_offered` adds to these.
 */
export const HUMAN_ONLY = {
  "clinic.sign": SIGN,
  clinic_sign: SIGN,
  "clinic.agreement": "Agreement studies against practitioners' labels are run by a person, not by the model.",
  "registry.release": REGISTRY,
  "registry.promote": REGISTRY,
  "system.shell": SHELL,
  shell: SHELL,
  "tcmdb.fetch_confirm": "A download above the data hub's size gate needs the user's own confirmation; it is not a tool call.",
};
const HUMAN_ONLY_REMEDY = "A person does this, not the model: a licensed practitioner signs a clinic draft; the maintainers review a release.";

/** Approvals kept in the project records of a Store (Store.projects.get / update). */
export function storeApprovals(store) {
  return {
    async get(projectId) {
      if (!projectId) return null;
      const p = await store.projects.get(projectId);
      return p ? { web: p.defaults?.web, approvals: p.approvals || {} } : null;
    },
    async grant(projectId, keys) {
      if (!projectId || !keys.length) return;
      if (typeof store.projects.patchApprovals === "function") {
        // inside the write's transaction: an approval revoked while this call waited for its answer stays revoked
        try { await store.projects.patchApprovals(projectId, { grant: keys }); } catch { /* the project is gone */ }
        return;
      }
      const p = await store.projects.get(projectId);
      if (!p) return;
      const approvals = { ...(p.approvals || {}) };
      for (const k of keys) approvals[k] = "project";
      await store.projects.update(projectId, { approvals });
    },
  };
}

export class ToolRouter {
  /**
   * catalog: Catalog; runtimes: {browser?, runner?}; settings: Settings (compute, web); approvals: {get(projectId),
   * grant(projectId, keys)} or omitted when `store` is given.
   */
  constructor({ catalog, runtimes = {}, settings = {}, approvals, store } = {}) {
    this.catalog = catalog;
    this.runtimes = runtimes;
    this.settings = settings;
    this.approvals = approvals || (store ? storeApprovals(store) : null);
    this.sessionGrants = new Map(); // projectId → Set of keys granted "once" that stand for this page's life
    this.queue = Promise.resolve(); // one approval prompt at a time, so parallel calls do not ask twice
  }

  setCatalog(catalog) { this.catalog = catalog; }
  setSettings(settings) { this.settings = settings; }
  setRuntimes(runtimes) { this.runtimes = runtimes; }

  /** "browser" | "runner" | null. */
  where(toolOrEntry) {
    return this.placement(toolOrEntry).where;
  }

  /** Where a call would run, resolving call_tool to the entry it names. */
  whereCall(name, args = {}) {
    const r = this.resolve(name, isPlainObject(args) ? args : {});
    return r.error ? null : this.placement(r.target).where;
  }

  /** {where, reason, message}: the choice and, when there is none, why. */
  placement(toolOrEntry) {
    const target = this.#target(toolOrEntry);
    if (!target) return { where: null, reason: "not_found", message: t("core.router.unknown_tool", { name: String(toolOrEntry?.name || toolOrEntry?.id || toolOrEntry) }) };
    const name = target.name || target.id;
    const exec = Array.isArray(target.exec) ? target.exec : [];
    const browserUp = this.#browserUp();
    const runnerUp = this.#runnerUp();
    const canB = exec.includes("browser") && browserUp;
    const runnerHasIt = exec.includes("runner") && target.available !== false;
    const canR = runnerHasIt && runnerUp;
    const compute = this.settings?.compute || "auto";
    if (isLocalTool(target.name)) {
      // the page can always answer these two; a ready runtime answers with more detail
      const w = compute === "runner" && canR ? "runner" : canB && this.runtimes.browser?.status === "ready" ? "browser" : canR ? "runner" : "browser";
      return { where: w, reason: "ok", message: "" };
    }
    const missing = () => (target.available === false && exec.includes("runner") && runnerUp
      ? { where: null, reason: "missing_deps", message: t("core.router.missing_deps", { name, missing: (target.missing || []).join(", ") || "?" }) }
      : null);
    if (compute === "browser") {
      if (canB) return { where: "browser", reason: "ok", message: "" };
      if (!exec.includes("browser")) return { where: null, reason: "compute_browser", message: t("core.router.browser_only_setting", { name }) };
      return { where: null, reason: "no_runtime", message: t("core.router.no_runtime", { name }) };
    }
    if (compute === "runner") {
      if (canR) return { where: "runner", reason: "ok", message: "" };
      if (!runnerUp) return { where: null, reason: "runner_offline", message: t("core.router.runner_setting_offline") };
      return missing() || { where: null, reason: "no_runtime", message: t("core.router.no_runtime", { name }) };
    }
    // auto: a connected runner first (native CPython, the project's durable audit chain, the user's own devices),
    // the browser otherwise (ARCHITECTURE, placement rules)
    if (canR) return { where: "runner", reason: "ok", message: "" };
    if (canB) return { where: "browser", reason: "ok", message: "" };
    if (exec.includes("runner") && !runnerUp) return { where: null, reason: "needs_runner", message: t("core.router.needs_runner", { name }) };
    return missing() || { where: null, reason: "no_runtime", message: t("core.router.no_runtime", { name }) };
  }

  /** What a call resolves to: the core tool, the entry it reaches (call_tool → arguments.tool), and its flags. */
  resolve(name, args = {}) {
    const cat = this.catalog;
    const humanOnly = (id) => {
      const extra = cat?.neverOffered;
      const msg = (extra && Object.hasOwn(extra, id) ? extra[id] : null) || (Object.hasOwn(HUMAN_ONLY, id) ? HUMAN_ONLY[id] : null);
      return msg ? { error: "human_only", name: id, message: String(msg), suggestions: [] } : null;
    };
    const refusedByName = humanOnly(name);
    if (refusedByName) return refusedByName;
    const core = cat?.coreTool?.(name) || null;
    if (name === "call_tool") {
      const id = String(args?.tool || "");
      const refused = humanOnly(id);
      if (refused) return refused;
      const entry = cat?.byId?.[id] || cat?.coreTool?.(id) || null;
      if (!entry) return { error: "not_found", name: id || "(missing tool)", suggestions: cat?.suggest?.(id) || [] };
      return { core, entry, target: entry, key: entry.id || entry.name, label: labelOf(entry) };
    }
    if (core) {
      const entry = core.maps_to ? cat.byId?.[core.maps_to] || null : null;
      // the core tool's own flags decide (it may be narrower than its entry); the entry supplies heavy/gpu/available
      const target = { ...(entry || {}), ...core, heavy: core.heavy ?? entry?.heavy, gpu: core.gpu ?? entry?.gpu, available: core.available ?? entry?.available, missing: core.missing ?? entry?.missing };
      // approvals are keyed by the entry, so one given through call_tool covers the core tool too
      return { core, entry, target, key: entry?.id || core.name, label: labelOf(core) };
    }
    const entry = cat?.byId?.[name];
    if (entry) return { core: null, entry, target: entry, key: entry.id, label: labelOf(entry) };
    return { error: "not_found", name, suggestions: cat?.suggest?.(name) || [] };
  }

  /**
   * Run a call. ctx: {projectId, conversationId, signal, onApproval(request) → "once"|"project"|"deny", callId}.
   * Always resolves to an envelope.
   */
  async call(name, args, ctx = {}) {
    const { projectId = null, conversationId = null, signal, onApproval, callId = null } = ctx;
    const input = isPlainObject(args) ? args : {};
    const r = this.resolve(name, input);
    if (r.error === "human_only") {
      // a refusal, shown as one with its code: nothing here or anywhere else can do it for the model
      return synthEnvelope({
        tool: name, via: r.name, args: input, status: "refused", kind: r.name.startsWith("clinic") ? "clinic" : "system",
        summary: t("core.router.human_only", { name: r.name }),
        text: `Refused (HUMAN_ONLY): ${r.message} It is not offered as a tool and no tool can do it; tell the user it is a person's act, and do not look for another tool to do it.`,
        error: { type: "refused", message: r.message, hint: HUMAN_ONLY_REMEDY },
        refusals: [{ code: "HUMAN_ONLY", message: r.message, remedy: HUMAN_ONLY_REMEDY }],
      });
    }
    if (r.error) {
      const hint = r.suggestions.length ? `Did you mean: ${r.suggestions.join(", ")}? ` : "";
      return synthEnvelope({
        tool: name, args: input, status: "failed",
        summary: t("core.router.unknown_tool", { name: r.name }),
        text: `There is no tool named '${r.name}'. ${hint}Use catalog_search to find tools, then call_tool with an entry id.`,
        error: { type: "not_found", message: `unknown tool: ${r.name}`, hint: hint.trim() || "use catalog_search" },
      });
    }
    const place = this.placement(r.target);

    if (isLocalTool(r.target.name)) {
      const rt = place.where && this.runtimes[place.where];
      if (!rt || rt.status !== "ready") {
        return localCall(r.target.name, input, {
          catalog: this.catalog, where: (e) => this.where(e), t,
          status: this.#status(await this.#webOn(projectId)),
        });
      }
    }
    if (!place.where) {
      return synthEnvelope({
        tool: name, via: r.entry?.id, args: input, status: "failed", kind: r.entry?.kind,
        summary: place.message,
        text: `Not run: ${t(...messageKey(place, r), "en")} Say what it would need; do not substitute an approximation.`,
        error: { type: "unavailable", message: place.message, hint: place.reason },
      });
    }

    const flags = { network: Boolean(r.target.network), confirm: Boolean(r.target.confirm), job: Boolean(r.target.job) };
    if (flags.network && !(await this.#webOn(projectId))) {
      return synthEnvelope({
        tool: name, via: r.entry?.id, args: input, status: "failed", kind: r.entry?.kind,
        summary: t("core.router.network_off", { name: r.label }),
        text: "Not run: web access is off for this project and this tool reaches the network. Ask the user to turn on web access for the project if the result is needed.",
        error: { type: "network_off", message: t("core.router.network_off", { name: r.label }), hint: "turn on web access for this project" },
      });
    }

    const needs = this.#reasons(r, flags, place.where, input);
    const granted = [];
    if (needs.length) {
      const outcome = await this.#approve({ needs, r, name, projectId, callId, signal, onApproval, input });
      if (outcome.envelope) return outcome.envelope;
      granted.push(...needs.map((n) => n.reason));
    }

    const rt = this.runtimes[place.where];
    if (signal?.aborted) return cancelled(name, r, input);
    try {
      const env = await raceAbort(rt.call(name, input, {
        project_id: projectId, conversation_id: conversationId, approvals: granted, signal,
        network: await this.#webOn(projectId), acceleration: this.settings.browserAcceleration || "auto",
      }), signal);
      return normalizeEnvelope(env, { tool: name, where: place.where, entry: r.entry });
    } catch (err) {
      if (isAbort(err) || signal?.aborted) return cancelled(name, r, input);
      const message = err?.message || String(err);
      return synthEnvelope({
        tool: name, via: r.entry?.id, args: input, status: "failed", kind: r.entry?.kind, where: place.where,
        summary: t("core.router.runtime_error", { name: r.label, message }),
        text: `The tool failed to run: ${message}. There is no result.`,
        error: { type: "runtime_error", message, hint: "" },
      });
    }
  }

  async #approve({ needs, r, name, projectId, callId, signal, onApproval, input }) {
    const run = async () => {
      const pending = await this.#pending(projectId, needs);
      if (!pending.length) return {};
      const hosts = [...new Set(pending.flatMap((n) => n.hosts || []))];
      const what = whatOf(r);
      const request = {
        callId, tool: name, entry: r.entry?.id || null, reason: pending[0].reason, reasons: pending.map((n) => n.reason),
        what, hosts, args: input, projectId,
        text: pending.map((n) => t(`core.approval.${n.reason}`, { what, hosts: hosts.join(", ") || "—" })).join("；"),
      };
      if (typeof onApproval !== "function") {
        return {
          envelope: await synthEnvelope({
            tool: name, via: r.entry?.id, args: input, status: "needs_approval", kind: r.entry?.kind,
            summary: t("core.router.needs_approval", { name: r.label }),
            text: `Not run yet: this call needs the user's approval (${request.reasons.join(", ")}). Tell the user what it would do and that it is waiting for approval.`,
            approval: { reason: request.reason, what, hosts },
          }),
        };
      }
      let decision;
      try {
        decision = await raceAbort(Promise.resolve(onApproval(request)), signal);
      } catch (err) {
        if (isAbort(err) || signal?.aborted) return { envelope: await cancelled(name, r, input) };
        decision = "deny";
      }
      if (decision !== "once" && decision !== "project") {
        return {
          envelope: await synthEnvelope({
            tool: name, via: r.entry?.id, args: input, status: "refused", kind: r.entry?.kind,
            summary: t("core.router.denied", { name: r.label }),
            text: `Not run: the user declined this call (${request.reasons.join(", ")}). Do not call it again unless the user asks; answer with what you have, and say what this call would have added.`,
            // the person's "no", not the kernel's refusal: status stays "refused" (not run), the type says who decided
            error: { type: "declined", message: t("core.router.denied", { name: r.label }), hint: "the user declined" },
            approval: { reason: request.reason, what, hosts, decision: "deny" },
          }),
        };
      }
      if (decision === "project" && projectId && this.approvals) {
        try { await this.approvals.grant(projectId, pending.map((n) => n.key)); } catch { /* the call still runs once */ }
      }
      // "once" on the first runner call: the first call has now been approved for this page's life
      const session = this.#grants(projectId);
      for (const n of pending) if (n.reason === "first_runner_call" || decision === "project") session.add(n.key);
      return {};
    };
    const p = this.queue.then(run, run);
    this.queue = p.then(() => undefined, () => undefined);
    return p;
  }

  #reasons(r, flags, where, input) {
    const key = r.key;
    const hosts = hostsOf(r.target);
    const out = [];
    if (flags.confirm) out.push({ reason: flags.job ? "job" : "confirm", key });
    if (flags.network) out.push({ reason: "network", key: `${key}:network`, hosts });
    if (input.allow_remote === true || input.arguments?.allow_remote === true) out.push({ reason: "remote_upload", key: `${key}:remote_upload`, hosts });
    if (where === "runner") out.push({ reason: "first_runner_call", key: RUNNER_KEY });
    return out;
  }

  async #pending(projectId, needs) {
    let standing = {};
    if (projectId && this.approvals) {
      try { standing = (await this.approvals.get(projectId))?.approvals || {}; } catch { standing = {}; }
    }
    const session = this.#grants(projectId);
    return needs.filter((n) => standing[n.key] !== "project" && !session.has(n.key));
  }

  #grants(projectId) {
    const k = projectId || "";
    if (!this.sessionGrants.has(k)) this.sessionGrants.set(k, new Set());
    return this.sessionGrants.get(k);
  }

  async #webOn(projectId) {
    if (projectId && this.approvals) {
      try {
        const p = await this.approvals.get(projectId);
        if (p && typeof p.web === "boolean") return p.web;
      } catch { /* fall back to the default */ }
    }
    return Boolean(this.settings?.web);
  }

  #status(web) {
    return {
      browser: this.runtimes.browser?.status || "offline",
      runner: this.runtimes.runner?.status || "offline",
      runnerUrl: this.runtimes.runner?.url || null,
      compute: this.settings?.compute || "auto",
      web,
    };
  }

  #browserUp() {
    const b = this.runtimes.browser;
    return Boolean(b) && b.status !== "offline";
  }

  #runnerUp() {
    return this.runtimes.runner?.status === "ready";
  }

  #target(x) {
    if (!x) return null;
    if (typeof x === "object") return x;
    const r = this.resolve(String(x), {});
    return r.error ? null : r.target;
  }
}

function messageKey(place, r) {
  const keys = {
    needs_runner: "core.router.needs_runner", compute_browser: "core.router.browser_only_setting",
    runner_offline: "core.router.runner_setting_offline", missing_deps: "core.router.missing_deps",
  };
  return [keys[place.reason] || "core.router.no_runtime", { name: r.label, missing: (r.target.missing || []).join(", ") }];
}

function labelOf(x) {
  return x?.name || x?.id || "tool";
}

function whatOf(r) {
  const title = r.target?.title;
  const label = title && typeof title === "object" ? title.zh || title.en : "";
  const id = r.entry?.id || r.target?.name || r.key;
  return label && label !== id ? `${label} (${id})` : id;
}

function hostsOf(target) {
  const h = target?.hosts || target?.skill?.network || [];
  return Array.isArray(h) ? h.filter((x) => typeof x === "string") : [];
}

function cancelled(name, r, input) {
  return synthEnvelope({
    tool: name, via: r.entry?.id, args: input, status: "cancelled", kind: r.entry?.kind,
    summary: t("core.router.cancelled", { name: r.label }),
    text: "The call was stopped before it finished; there is no result.",
    error: null,
  });
}

/** Resolve with the promise, or reject with an AbortError when the signal aborts first. */
export function raceAbort(promise, signal) {
  if (!signal) return promise;
  if (signal.aborted) return Promise.reject(abort());
  return new Promise((resolve, reject) => {
    const onAbort = () => reject(abort());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (v) => { signal.removeEventListener("abort", onAbort); resolve(v); },
      (e) => { signal.removeEventListener("abort", onAbort); reject(e); },
    );
  });
}

function abort() {
  try { return new DOMException("aborted", "AbortError"); } catch { const e = new Error("aborted"); e.name = "AbortError"; return e; }
}

/**
 * An envelope for an outcome decided in the page (CONTRACTS §3): nothing ran, so there is no result, no governance
 * verdict and no output hash; the receipt says where it was decided.
 */
export async function synthEnvelope({ tool, via = null, args = {}, status, summary = "", text = "", error = null, approval = null, kind, where = "browser", result = null, refusals = [] }) {
  // the same first line as the Python envelopes ("tool → via: status"), so the model reads every outcome alike
  // and the error's type (its message is in the UI's language; the model gets the English hint)
  const head = `${tool} → ${via || tool}: ${status}${error?.type ? `\nError (${error.type})${error.hint ? `. Hint: ${error.hint}` : ""}` : ""}`;
  const body = String(text || "");
  return {
    ok: status === "succeeded",
    tool, via: via || tool, status, duration_ms: 0, summary, text: clip(body.startsWith(head) ? body : `${head}\n${body}`.trim(), TEXT_LIMIT), result,
    citations: [],
    governance: { kind: kind || "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: Array.isArray(refusals) ? refusals : [], labels: [], licences: [], limitations: [], outputs: [] },
    receipt: {
      where, runtime: "studio-router", device: "cpu", versions: {}, composite_version: null, content_hash: null,
      audit_head: null, input_sha256: await inputSha256(args), output_sha256: null, started_at: nowIso(), decided_by: "router",
    },
    job: null, approval, error,
  };
}

/** sha256 of the canonical JSON of a call's arguments (what receipt.input_sha256 holds). */
export async function inputSha256(args) {
  try { return await sha256Hex(canonicalJson(args || {})); } catch { return null; }
}

/** Fill the fields a runtime left out, cap `text`, and record where the call ran. */
export async function normalizeEnvelope(env, { tool, where, entry } = {}) {
  if (!isPlainObject(env)) {
    return synthEnvelope({ tool, via: entry?.id, status: "failed", where, summary: "invalid result", text: "The runtime returned no envelope; there is no result.", error: { type: "runtime_error", message: "the runtime returned no envelope", hint: "" } });
  }
  const status = env.status || (env.ok ? "succeeded" : "failed");
  const out = {
    ok: env.ok ?? status === "succeeded",
    tool: env.tool || tool,
    via: env.via || entry?.id || tool,
    status,
    duration_ms: Number(env.duration_ms) || 0,
    summary: env.summary || "",
    text: clip(typeof env.text === "string" ? env.text : env.text == null ? "" : JSON.stringify(env.text), TEXT_LIMIT,
      (n) => `… [truncated by the page; ${n} characters in full; the full result is in the inspector]`),
    result: env.result ?? null,
    citations: Array.isArray(env.citations) ? env.citations : [],
    governance: { kind: entry?.kind || "native", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: [], labels: [], licences: [], limitations: [], outputs: [], ...(isPlainObject(env.governance) ? env.governance : {}) },
    receipt: { where, ...(isPlainObject(env.receipt) ? env.receipt : {}) },
    job: env.job ?? null,
    approval: env.approval ?? null,
    error: env.error ?? null,
  };
  if (!out.receipt.where) out.receipt.where = where;
  if (!out.text) {
    out.text = out.ok ? (out.summary || "{}") : `Failed: ${out.error?.message || out.summary || "no result"}`;
  }
  for (const k of Object.keys(env)) if (!(k in out)) out[k] = env[k];
  return out;
}
