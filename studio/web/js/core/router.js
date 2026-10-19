// The tool router: where each call runs (the browser's Pyodide worker or the local runner), which approvals it needs
// first, and an envelope (CONTRACTS §3) for every outcome — including the ones decided here, before anything runs:
// needs_approval, refused (the user said no), and failed/unavailable or network_off. A call never throws.
//
// Placement: settings.compute "browser" or "runner" is honoured strictly (a tool that cannot run there is unavailable,
// with the reason). "auto" sends everything the runner has to the runner while it is connected (one place per project:
// native speed, the durable audit chain, the user's devices), and what the browser can run to the browser otherwise:
// a project's skill and clinic runs write one audit chain, and a project split across two runtimes would fork it.
// "remote" is a preference no backend serves yet: every call is unavailable (remote_not_configured) and nothing falls
// back to the browser or the runner. A value this page does not know is reported (unknown_compute), never read as auto.
// A placement that finds no place says why with Requirements {need, detail, remedy, names?} (core/catalog.js).
//
// Approvals (DESIGN §3.3, ARCHITECTURE decision 5): entries marked confirm (jobs among them), network entries (which
// also need the project's web access on; the grant is for the hosts the call reaches, and a call whose hosts are not
// known in advance is asked about every time), sending data to a third party (allow_remote), and the first runner
// call of each project, or of each remote runner. The answer is "once", "project" (remembered in the project record)
// or "deny".
//
// Every envelope the router returns records how it was placed and approved (receipt.execution.placement / .approvals);
// one decided here also has the page's receipt (core/receipt.js: where "page", decided_by "router", not executed).

import { COMPUTE_VALUES, isLocalTool, localCall, requirement, runnerLacks, unknownCompute } from "./catalog.js";
import { t } from "./i18n.js";
import { baseReceipt, pageOutcome } from "./receipt.js";
import { canonicalJson, clip, flagArgument, isAbort, isLoopbackUrl, isPlainObject, objectArgument, sha256Hex, utf8Bytes } from "./util.js";

export const TEXT_LIMIT = 16000;
const RUNNER_KEY = "runner";
/**
 * The reasons a placement gives (error.reason of an unavailable envelope; receipt.execution.placement.reason).
 * human_only is provenance only: a HUMAN_ONLY refusal is decided before any placement and is never "unavailable".
 */
export const PLACEMENT_REASONS = Object.freeze([
  "ok", "not_found", "missing_deps", "compute_browser", "no_runtime", "runner_offline", "needs_runner",
  "unknown_compute", "remote_not_configured", "too_large_for_browser", "missing_engine", "human_only",
]);
/**
 * The other error.reason codes a runtime's envelope can carry (decided by the Python dispatcher or the runner). The
 * router gives them too: network_off when the project's web access is off, and needs_dataset, needs_job_service (or
 * another of these) when the connected runner's catalog says the entry lacks that need there.
 */
export const RUNTIME_REASONS = Object.freeze([
  "needs_dataset", "needs_job_service", "remote_upload_off", "network_off", "needs_authentication",
]);
/**
 * The reason for what an entry lacks on the connected runner, by the need of its first requirement
 * (tcmstudio.capability.REASON_NEED read backwards): a dataset is never a package to install.
 */
const RUNNER_NEED_REASON = { dependency: "missing_deps", dataset: "needs_dataset", job_service: "needs_job_service", network: "network_off", authentication: "needs_authentication" };
const MESSAGE = {
  not_found: "core.router.unknown_tool", missing_deps: "core.router.missing_deps", compute_browser: "core.router.browser_only_setting",
  no_runtime: "core.router.no_runtime", runner_offline: "core.router.runner_setting_offline", needs_runner: "core.router.needs_runner",
  unknown_compute: "core.router.unknown_compute", remote_not_configured: "core.router.remote_not_configured",
  too_large_for_browser: "core.router.too_large_for_browser", missing_engine: "core.router.missing_engine",
};

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

  /** "browser" | "runner" | null; "page" for catalog_search/capabilities_status when only the page may answer them. */
  where(toolOrEntry, args) {
    return this.placement(toolOrEntry, args).where;
  }

  /** Where a call would run, resolving call_tool to the entry it names. */
  whereCall(name, args = {}) {
    const input = isPlainObject(args) ? args : {};
    const r = this.resolve(name, input);
    return r.error ? null : this.placement(r.target, entryArgs(name, input)).where;
  }

  /**
   * {where, reason, message, requires}: the choice and, when there is none, why (message in the UI's language) and
   * what it would need (Requirements, English). args: the arguments the entry receives, when known (the size check,
   * the engine they select).
   */
  placement(toolOrEntry, args) {
    const p = this.#place(this.#target(toolOrEntry), args, toolOrEntry);
    return { where: p.where, reason: p.reason, message: p.key ? t(p.key, p.vars) : "", requires: p.requires };
  }

  /** The compute preference as set ("auto" when unset): it may be one this page does not know. */
  #compute() {
    const c = this.settings?.compute;
    return c === undefined || c === null || c === "" ? "auto" : String(c);
  }

  #place(target, args, asked) {
    const none = (reason, vars, requires, key = MESSAGE[reason]) => ({ where: null, reason, key, vars, requires });
    if (!target) return none("not_found", { name: String(asked?.name || asked?.id || asked) }, []);
    const name = target.name || target.id;
    const label = { name };
    const exec = Array.isArray(target.exec) ? target.exec : [];
    const browserUp = this.#browserUp();
    const runnerUp = this.#runnerUp();
    const canB = exec.includes("browser") && browserUp;
    const runnerHasIt = exec.includes("runner") && target.available !== false;
    const canR = runnerHasIt && runnerUp;
    const compute = this.#compute();
    const ok = (where) => ({ where, reason: "ok", key: null, vars: null, requires: [] });
    if (isLocalTool(target.name)) {
      // the page can always answer these two (nothing leaves it); a ready runtime answers with more detail. Only
      // compute "runner" or "auto" may send them to the runner; under "browser" they stay here, answered in the page
      // until Pyodide is ready. Under "remote" or a value this page does not know nothing runs anywhere, and a
      // runtime's answer (which does not know the preference) would list as runnable what the router refuses: the
      // page answers them itself ("page"), with the need that preference has
      if (compute === "runner") return ok(canR ? "runner" : "browser");
      if (compute === "auto") return ok(canB && this.runtimes.browser?.status === "ready" ? "browser" : canR ? "runner" : "browser");
      if (compute === "browser") return ok("browser");
      return ok("page");
    }
    if (!COMPUTE_VALUES.includes(compute)) return none("unknown_compute", { ...label, value: compute }, [unknownCompute(compute)]);
    // no remote backend exists: unavailable, and never moved to the runner or the browser instead
    if (compute === "remote") return none("remote_not_configured", label, [requirement("remote_backend")]);
    // a runner on another machine is never called the local one
    // what the runner lacks, as its catalog classifies it (state.readiness.runner): packages are missing_deps, the
    // research snapshots needs_dataset, a job layer that cannot run there needs_job_service
    const missing = () => {
      if (!(target.available === false && exec.includes("runner") && runnerUp)) return null;
      const requires = runnerLacks(target);
      const reason = RUNNER_NEED_REASON[requires[0].need] || "missing_deps";
      const remote = this.#runnerRemote();
      if (reason === "missing_deps") {
        const names = requires.filter((q) => q.need === "dependency").flatMap((q) => q.names || []);
        return none("missing_deps", { ...label, missing: names.join(", ") || "?" }, requires, remote ? "core.router.missing_deps_remote" : MESSAGE.missing_deps);
      }
      return none(reason, { ...label, missing: requires.map((q) => q.detail).join("; ") }, requires, remote ? "core.router.runner_lacks_remote" : "core.router.runner_lacks");
    };
    const inBrowser = () => this.#tooLarge(target, args, label, compute, exec) || ok("browser");
    const onRunner = () => this.#engineMissing(target, args, label) || ok("runner");
    if (compute === "browser") {
      if (canB) return inBrowser();
      if (!exec.includes("browser")) return none("compute_browser", label, [requirement("compute_setting")]);
      return none("no_runtime", label, [requirement("browser_runtime")]);
    }
    if (compute === "runner") {
      if (canR) return onRunner();
      if (!runnerUp) return none("runner_offline", label, [requirement("runner")]);
      return missing() || none("no_runtime", label, [requirement("browser_runtime", { remedy: "It runs only in the browser runtime: set compute to Auto or Browser." })]);
    }
    // auto: a connected runner first (native CPython, the project's durable audit chain, the user's own devices),
    // the browser otherwise (ARCHITECTURE, placement rules)
    if (canR) return onRunner();
    if (canB) return inBrowser();
    if (exec.includes("runner") && !runnerUp) return none("needs_runner", label, [requirement("runner")]);
    return missing() || none("no_runtime", label, [requirement("browser_runtime")]);
  }

  /** too_large_for_browser when the entry's browser request limit is set and the canonical arguments exceed it. */
  #tooLarge(target, args, label, compute, exec) {
    const { entry, args: a } = this.#effective(target, args);
    // the entry's own limit, else the catalog document's (one per runtime)
    const limit = Number(entry?.limits?.max_request_bytes?.browser ?? target?.limits?.max_request_bytes?.browser ?? this.catalog?.limits?.max_request_bytes?.browser);
    if (!Number.isFinite(limit) || limit <= 0 || !isPlainObject(a)) return null;
    let bytes;
    try { bytes = utf8Bytes(canonicalJson(a)); } catch { return null; }
    if (bytes <= limit) return null;
    // an entry the runner cannot run has one remedy: a smaller input
    const onRunner = exec.includes("runner");
    const requires = [requirement("memory", {
      detail: `a request of at most ${formatBytes(limit)} in the browser runtime (this one is ${formatBytes(bytes)} of canonical JSON)`,
      remedy: onRunner ? "Run it on the local runner, or pass a smaller input." : "Pass a smaller input: this entry runs only in the browser runtime.",
    })];
    if (onRunner) requires.push(compute === "browser" ? requirement("compute_setting") : requirement("runner"));
    return {
      where: null, reason: "too_large_for_browser", key: onRunner ? MESSAGE.too_large_for_browser : "core.router.too_large_for_browser_only",
      vars: { ...label, size: formatBytes(bytes), limit: formatBytes(limit) }, requires,
    };
  }

  /**
   * missing_engine when the runner's catalog says the engine these arguments select (execution.engines[].selected_by)
   * lacks what it needs; null when it is ready, or when the catalog does not say (an older runner): the runner itself
   * then refuses the job with the missing packages named.
   */
  #engineMissing(target, args, label) {
    const { entry, args: a } = this.#effective(target, args);
    const engines = Array.isArray(entry?.execution?.engines) ? entry.execution.engines.filter(isPlainObject) : [];
    if (!engines.length) return null;
    const lacking = chosenEngines(entry, engines, isPlainObject(a) ? a : {})
      .map((g) => [g, engineReadiness(g)]).filter(([, q]) => q && q.state !== "ready");
    if (!lacking.length) return null;
    const requires = lacking.flatMap(([g, q]) => (q.requires.length ? q.requires : [requirement("dependency", { names: Array.isArray(g.missing) ? g.missing : [] })]));
    const names = [...new Set(requires.flatMap((q) => q.names || []))];
    return {
      where: null, reason: "missing_engine", key: MESSAGE.missing_engine,
      vars: { ...label, engine: lacking.map(([g]) => String(g.id || "?")).join(", "), missing: names.join(", ") || requires.map((q) => q.detail).join("; ") || "?" }, requires,
    };
  }

  /**
   * The entry a call reaches and the arguments it gets: a core tool that maps to a family (run_pipeline → job.pipeline.*
   * by `pipeline`, connector_call → connector.* by `connector` and `operation`) reaches the one its arguments name.
   */
  #effective(target, args) {
    const a = isPlainObject(args) ? args : {};
    const maps = typeof target?.maps_to === "string" ? target.maps_to : "";
    if (!maps.endsWith(".*")) return { entry: target, args: a };
    const prefix = maps.slice(0, -1);
    let id = null;
    if (prefix === "job.pipeline." && typeof a.pipeline === "string") id = `${prefix}${a.pipeline}`;
    else if (prefix === "connector." && typeof a.connector === "string" && typeof a.operation === "string") id = `${prefix}${a.connector}.${a.operation}`;
    const entry = id ? this.catalog?.byId?.[id] : null;
    return entry ? { entry, args: isPlainObject(a.arguments) ? a.arguments : {} } : { entry: target, args: a };
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
      if (!entry) {
        const canon = id && cat?.canonicalName?.(id);
        if (canon) return { ...this.resolve(name, { ...args, tool: canon }), renamed: { from: id, to: canon, name, args: { ...args, tool: canon } } };
        return { error: "not_found", name: id || "(missing tool)", suggestions: cat?.suggest?.(id) || [] };
      }
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
    // tcmdb_status for tcmdb.status: the one id it can only mean (run as that, and named back to the model)
    const canon = cat?.canonicalName?.(name);
    if (canon && canon !== name) return { ...this.resolve(canon, args), renamed: { from: name, to: canon, name: canon, args } };
    return { error: "not_found", name, suggestions: cat?.suggest?.(name) || [] };
  }

  /**
   * Run a call. ctx: {projectId, conversationId, signal, onApproval(request) → "once"|"project"|"deny", callId}.
   * Always resolves to an envelope: a fault in the page itself (not in a runtime, whose faults #call turns into
   * envelopes) gives a failed one that says so, never a rejection.
   */
  async call(name, args, ctx = {}) {
    try {
      return await this.#call(name, args, ctx);
    } catch (err) {
      const message = err?.message || String(err);
      const tool = String(name || "tool");
      return stamp(await synthEnvelope({
        tool, args: isPlainObject(args) ? args : {}, status: "failed",
        summary: t("core.router.runtime_error", { name: tool, message }),
        text: `The page could not complete this call (${message}). There is no result.`,
        error: { type: "runtime_error", message, hint: "" },
      }), this.#provenance({ where: null, reason: null }));
    }
  }

  async #call(name, args, ctx = {}) {
    const { projectId = null, conversationId = null, signal, onApproval, callId = null } = ctx;
    let input = isPlainObject(args) ? args : {};
    // call_tool's arguments as JSON text run as the object they spell (the dispatcher parses them): they are read, and
    // approved, as that object here too
    if (name === "call_tool" && typeof input.arguments === "string") {
      const parsed = objectArgument(input.arguments);
      if (parsed) input = { ...input, arguments: parsed };
    }
    const r = this.resolve(name, input);
    if (r.renamed && !r.error) {
      const env = await this.call(r.renamed.name, r.renamed.args, ctx);
      const note = `Note: '${r.renamed.from}' is written '${r.renamed.to}'; call it so.`;
      return { ...env, text: env.text ? `${env.text}\n${note}` : note };
    }
    if (r.error === "human_only") {
      // a refusal, shown as one with its code: nothing here or anywhere else can do it for the model
      return stamp(await synthEnvelope({
        tool: name, via: r.name, args: input, status: "refused", kind: r.name.startsWith("clinic") ? "clinic" : "system",
        summary: t("core.router.human_only", { name: r.name }),
        text: `Refused (HUMAN_ONLY): ${r.message} It is not offered as a tool and no tool can do it; tell the user it is a person's act, and do not look for another tool to do it.`,
        error: { type: "refused", message: r.message, hint: HUMAN_ONLY_REMEDY },
        refusals: [{ code: "HUMAN_ONLY", message: r.message, remedy: HUMAN_ONLY_REMEDY }],
      }), this.#provenance({ where: null, reason: "human_only" }));
    }
    if (r.error) {
      const hint = r.suggestions.length ? `Did you mean: ${r.suggestions.join(", ")}? ` : "";
      return stamp(await synthEnvelope({
        tool: name, args: input, status: "failed",
        summary: t("core.router.unknown_tool", { name: r.name }),
        text: `There is no tool named '${r.name}'. ${hint}Use catalog_search to find tools, then call_tool with an entry id.`,
        error: { type: "not_found", message: `unknown tool: ${r.name}`, hint: hint.trim() || "use catalog_search", reason: "not_found" },
      }), this.#provenance({ where: null, reason: "not_found" }));
    }
    const callArgs = entryArgs(name, input);
    const place = this.#place(r.target, callArgs, r.target);

    if (isLocalTool(r.target.name)) {
      const rt = place.where && place.where !== "page" ? this.runtimes[place.where] : null;
      if (!rt || rt.status !== "ready") {
        const env = await localCall(r.target.name, input, {
          catalog: this.catalog, where: (e) => this.where(e), t,
          status: this.#status(await this.#webOn(projectId)),
        });
        return stamp(env, this.#provenance({ where: "page", reason: place.reason }));
      }
    }
    if (!place.where) {
      // what it would need, in English for the model (the first remedy is the hint), the message in the UI's language
      const requires = place.requires || [];
      const needs = requires.map((q) => q.detail).join("; ");
      return stamp(await synthEnvelope({
        tool: name, via: r.entry?.id, args: input, status: "failed", kind: r.entry?.kind,
        summary: t(place.key, place.vars),
        text: `Not run: ${t(place.key, place.vars, "en")}${needs ? ` Needs: ${needs}.` : ""} Say what it would need; do not substitute an approximation.`,
        error: { type: "unavailable", message: t(place.key, place.vars), hint: requires[0]?.remedy || "", reason: place.reason, requires },
      }), this.#provenance(place));
    }

    const flags = { network: Boolean(r.target.network), confirm: Boolean(r.target.confirm), job: Boolean(r.target.job) };
    if (flags.network && !(await this.#webOn(projectId))) {
      // the hosts it would reach, as the network approval names them (the core tool's when the entry names none)
      const { entry: reached, args: reachedArgs } = this.#effective(r.target, callArgs);
      let hosts = hostsOf(reached, reachedArgs);
      if (!hosts.length && reached !== r.target) hosts = hostsOf(r.target, callArgs);
      const requires = [requirement("network", { names: hosts })];
      return stamp(await synthEnvelope({
        tool: name, via: r.entry?.id, args: input, status: "failed", kind: r.entry?.kind,
        summary: t("core.router.network_off", { name: r.label }),
        text: "Not run: web access is off for this project and this tool reaches the network. Ask the user to turn on web access for the project if the result is needed.",
        error: { type: "network_off", message: t("core.router.network_off", { name: r.label }), hint: requires[0].remedy, reason: "network_off", requires },
      }), this.#provenance(place));
    }

    const needs = await this.#reasons(r, flags, place.where, input, callArgs);
    let granted = [];
    if (needs.length) {
      const outcome = await this.#approve({ needs, r, name, projectId, callId, signal, onApproval, input });
      if (outcome.envelope) return stamp(outcome.envelope, this.#provenance(place, outcome.approvals));
      granted = outcome.approvals;
    }
    const prov = this.#provenance(place, granted);

    const rt = this.runtimes[place.where];
    if (signal?.aborted) return stamp(await cancelled(name, r, input, "not_executed"), prov);
    try {
      const env = await raceAbort(rt.call(name, input, {
        project_id: projectId, conversation_id: conversationId, approvals: granted.map((g) => g.reason), signal,
        network: await this.#webOn(projectId), acceleration: this.settings.browserAcceleration || "auto",
      }), signal);
      return stamp(await normalizeEnvelope(env, { tool: name, where: place.where, entry: r.entry }), prov);
    } catch (err) {
      if (isAbort(err) || signal?.aborted) return stamp(await cancelled(name, r, input), prov);
      const message = err?.message || String(err);
      return stamp(await synthEnvelope({
        tool: name, via: r.entry?.id, args: input, status: "failed", kind: r.entry?.kind,
        summary: t("core.router.runtime_error", { name: r.label, message }),
        text: `The tool failed to run: ${message}. There is no result.`,
        error: { type: "runtime_error", message, hint: "" },
      }), prov);
    }
  }

  /** receipt.execution additions: how the call was placed and which approvals it ran under. */
  #provenance(place, approvals = []) {
    return {
      placement: { requested: this.#compute(), chosen: place?.where ?? null, reason: place?.reason ?? null, runner: this.#runnerIdentity() },
      approvals: approvals.map((a) => ({ reason: a.reason, scope: a.scope, hosts: [...(a.hosts || [])], ...(a.legacy ? { legacy: true } : {}) })),
    };
  }

  /** The paired runner: {host, loopback}, or null when there is none (or its address cannot be read). */
  #runnerIdentity() {
    const url = this.runtimes.runner?.url;
    if (!url) return null;
    try { return { host: new URL(url).host, loopback: isLoopbackUrl(url) }; } catch { return null; }
  }

  /** Is the paired runner on another machine? Its calls are a remote runner's (own approval, never called local). */
  #runnerRemote() {
    const id = this.#runnerIdentity();
    return Boolean(id && !id.loopback);
  }

  async #approve({ needs, r, name, projectId, callId, signal, onApproval, input }) {
    const run = async () => {
      const held = await this.#held(projectId, needs);
      const pending = needs.filter((n) => !held.has(n));
      // in the order the call needs them; scope: how each stands (asked now: the answer)
      const approvals = (decision) => needs.filter((n) => held.has(n) || decision)
        .map((n) => ({ reason: n.reason, hosts: n.hosts || [], scope: held.get(n)?.scope || (n.perCall ? "once" : decision), legacy: Boolean(held.get(n)?.legacy) }));
      const standing = approvals(null);
      if (!pending.length) return { approvals: standing };
      const hosts = [...new Set(pending.flatMap((n) => n.hosts || []))];
      const what = whatOf(r);
      const request = {
        callId, tool: name, entry: r.entry?.id || null, reason: pending[0].reason, reasons: pending.map((n) => n.reason),
        what, hosts, args: input, projectId,
        needs: pending.map((n) => ({ reason: n.reason, hosts: [...(n.hosts || [])], ...(n.perCall ? { per_call: true } : {}) })),
        // each reason names its own hosts (a network approval its call's hosts, a remote runner its address)
        text: pending.map((n) => t(n.text || `core.approval.${n.reason}`, { what, hosts: (n.hosts || []).join(", ") || "—", ...(n.vars || {}) })).join("；"),
      };
      if (typeof onApproval !== "function") {
        return {
          approvals: standing,
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
        if (isAbort(err) || signal?.aborted) return { approvals: standing, envelope: await cancelled(name, r, input, "not_executed") };
        decision = "deny";
      }
      if (decision !== "once" && decision !== "project") {
        return {
          approvals: standing,
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
      const keep = pending.filter((n) => !n.perCall);
      let stored = false;
      if (decision === "project" && projectId && this.approvals && keep.length) {
        try { await this.approvals.grant(projectId, keep.map((n) => n.key)); stored = true; } catch { /* the call still runs once */ }
      }
      // "once" on the first call of a runner: approved for this page's life (that runner's, when it is remote); a
      // "project" that could not be stored (no project, no store) holds for this page's life too, and says so
      const session = this.#grants(projectId);
      for (const n of keep) if (n.reason === "first_runner_call" || n.reason === "remote_runner" || decision === "project") session.add(n.key);
      return { approvals: approvals(decision === "project" && !stored ? "page" : decision) };
    };
    const p = this.queue.then(run, run);
    this.queue = p.then(() => undefined, () => undefined);
    return p;
  }

  /**
   * The approvals a call needs: {reason, key, hosts?, legacy?, perCall?}. A network approval is for the hosts this
   * call reaches (key `<entry>:network@<sha12 of the sorted hosts>`), so a project grant never carries over to other
   * hosts; the key of the version before it (`<entry>:network`) still counts when the hosts are among the entry's own
   * static hosts. A call whose hosts are not known in advance (an argument value hosts_by does not map, or no hosts at
   * all) is asked about every time: no grant can be scoped to hosts nobody named. A runner on another machine needs
   * its own approval (remote_runner, key runner@<host>), never the local one's.
   */
  async #reasons(r, flags, where, input, callArgs) {
    const key = r.key;
    const { entry, args } = this.#effective(r.target, callArgs);
    let reach = callHosts(entry, args);
    // an entry that names no hosts of its own takes the core tool's (literature_search's); one with hosts_by never does
    if (!reach.hosts.length && !isPlainObject(entry?.hosts_by) && entry !== r.target) reach = callHosts(r.target, callArgs);
    // the hosts a grant can be scoped to: none when the call's are not known (static ones would not be its own)
    const hosts = reach.known ? reach.hosts : [];
    const remote = where === "runner" && this.#runnerRemote();
    const runnerHost = remote ? this.#runnerIdentity().host : null;
    const out = [];
    if (flags.confirm) {
      // a job on a runner on another machine is started there, not on this computer
      out.push({ reason: flags.job ? "job" : "confirm", key, ...(flags.job && remote ? { text: "core.approval.job_remote", vars: { runner: runnerHost } } : {}) });
    }
    if (flags.network) {
      if (!reach.known) {
        out.push({ reason: "network", key: `${key}:network`, hosts, perCall: true, text: "core.approval.network_unknown" });
      } else {
        const own = hostsOf(this.catalog?.byId?.[key] || r.target);
        const legacy = hosts.every((h) => own.includes(h)) ? `${key}:network` : null;
        out.push({ reason: "network", key: await networkKey(key, hosts), hosts, ...(legacy ? { legacy } : {}) });
      }
    }
    // the argument that sends the visitor's data to a third party's service (the entry's network_if), as the
    // dispatcher reads it: true or "true"; the core tool's and the entry it resolves to both count
    const sends = [...new Set(["allow_remote", r.target?.network_if, entry?.network_if].filter((x) => typeof x === "string" && x))];
    const inner = objectArgument(input.arguments) || {};
    if (sends.some((k) => flagArgument(input[k]) || flagArgument(inner[k]))) out.push({ reason: "remote_upload", key: `${key}:remote_upload`, hosts });
    if (where === "runner") {
      const id = this.#runnerIdentity();
      if (!remote) out.push({ reason: "first_runner_call", key: RUNNER_KEY });
      // patient data (phi entries: the clinic) goes to another machine only with the person's yes to this call
      else if (isPhi(r, entry, args, this.catalog)) out.push({ reason: "remote_runner", key: `${RUNNER_KEY}@${id.host}`, hosts: [id.host], perCall: true, text: "core.approval.remote_runner_phi" });
      else out.push({ reason: "remote_runner", key: `${RUNNER_KEY}@${id.host}`, hosts: [id.host] });
    }
    return out;
  }

  /** The needs that already stand: need → {scope: "standing" (the project record) | "page" (this page), legacy?}. */
  async #held(projectId, needs) {
    let standing = {};
    if (projectId && this.approvals) {
      try { standing = (await this.approvals.get(projectId))?.approvals || {}; } catch { standing = {}; }
    }
    const session = this.#grants(projectId);
    const out = new Map();
    for (const n of needs) {
      if (n.perCall) continue; // asked every time: no grant stands for it
      if (standing[n.key] === "project") out.set(n, { scope: "standing" });
      else if (n.legacy && standing[n.legacy] === "project") out.set(n, { scope: "standing", legacy: true });
      else if (session.has(n.key)) out.set(n, { scope: "page" });
    }
    return out;
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
    // the default for new projects; on unless the user turned it off (settings.js)
    return this.settings?.web !== false;
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

/**
 * Does the call carry patient data? The rule of tcmstudio.dispatch._is_phi (CONTRACTS §3): an entry marked phi, a
 * clinic entry, or one that takes a clinic `intake` (skill.draft-tcm-prescription); and for job.skill.run the skill its
 * skill_id names, whose arguments the job carries.
 */
function isPhi(r, entry, args, catalog) {
  const phi = (e) => isPlainObject(e) && (e.phi === true || e.kind === "clinic" || (isPlainObject(e.parameters?.properties) && Object.hasOwn(e.parameters.properties, "intake")));
  if ([r.target, r.entry, entry].some(phi)) return true;
  const runsSkill = [r.target, r.entry, entry].some((e) => e?.id === "job.skill.run");
  const id = runsSkill && isPlainObject(args) ? args.skill_id : null;
  return typeof id === "string" && phi(catalog?.byId?.[`skill.${id}`]);
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

/**
 * The hosts a call reaches: the entry's hosts for the argument values it names (hosts_by: {<param>: {<value>:
 * [hosts]}}, e.g. job.tcmdb.fetch by dataset), else its static hosts (hosts, or a skill's network list). Lower-cased,
 * sorted, without repeats.
 */
export function hostsOf(target, args = {}) {
  return callHosts(target, args).hosts;
}

/**
 * hostsOf, and whether those are the call's hosts: known when hosts_by maps the call's argument value, or, for an
 * entry without hosts_by, when it names static hosts. An entry with hosts_by whose argument value it does not map
 * (system.provider_call's free-text `tool`) reaches hosts nobody named: unknown, whatever its static hosts say.
 */
function callHosts(target, args = {}) {
  const out = new Set();
  const by = isPlainObject(target?.hosts_by) ? target.hosts_by : null;
  if (by && isPlainObject(args)) {
    for (const [param, map] of Object.entries(by)) {
      const v = args[param];
      const list = isPlainObject(map) && (typeof v === "string" || typeof v === "number") && Object.hasOwn(map, String(v)) ? map[String(v)] : null;
      if (Array.isArray(list)) for (const h of list) if (typeof h === "string" && h.trim()) out.add(h.trim().toLowerCase());
    }
  }
  if (out.size) return { hosts: [...out].sort(), known: true };
  const h = target?.hosts || target?.skill?.network || [];
  if (Array.isArray(h)) for (const x of h) if (typeof x === "string" && x.trim()) out.add(x.trim().toLowerCase());
  return { hosts: [...out].sort(), known: !by && out.size > 0 };
}

const normalHosts = (hosts) => [...new Set((hosts || []).map((h) => String(h).trim().toLowerCase()).filter(Boolean))].sort();

/** The first 12 hex digits of the sha256 of the canonical JSON array of the hosts (sorted, lower-cased). */
export async function hostsDigest(hosts) {
  return (await sha256Hex(canonicalJson(normalHosts(hosts)))).slice(0, 12);
}

/**
 * The key of a network grant for exactly these hosts: `<entry>:network@<sha12>`. A page without Web Crypto (an http
 * page opened at an address other than localhost has no crypto.subtle) keys it by the sorted hosts themselves,
 * comma-joined: still for these hosts only, and the call never fails for want of a hash.
 */
async function networkKey(key, hosts) {
  try { return `${key}:network@${await hostsDigest(hosts)}`; } catch { return `${key}:network@${normalHosts(hosts).join(",")}`; }
}

/** The arguments the entry itself receives: call_tool's `arguments`, otherwise the call's own. */
function entryArgs(name, input) {
  if (name !== "call_tool") return input;
  return isPlainObject(input?.arguments) ? input.arguments : {};
}

/**
 * The engines these arguments select (tcmstudio.capability.selected_engines): each whose selected_by {arg, value,
 * with?} matches the arguments, an argument left out (or empty) counting as its schema default; an engine selected by
 * nothing is always in use.
 */
function chosenEngines(entry, engines, args) {
  const props = isPlainObject(entry?.parameters?.properties) ? entry.parameters.properties : {};
  const valueOf = (name) => (args[name] !== undefined && args[name] !== null && args[name] !== "" ? args[name] : props[name]?.default);
  return engines.filter((g) => {
    const sel = isPlainObject(g.selected_by) ? g.selected_by : null;
    if (!sel) return true;
    if (valueOf(sel.arg) !== sel.value) return false;
    return Object.entries(isPlainObject(sel.with) ? sel.with : {}).every(([k, v]) => valueOf(k) === v);
  });
}

/** An engine's readiness on the runner as its catalog says it ({state, requires}), or null when it does not say. */
function engineReadiness(g) {
  const r = isPlainObject(g.readiness?.runner) ? g.readiness.runner : isPlainObject(g.readiness) && typeof g.readiness.state === "string" ? g.readiness : null;
  const fromList = (list) => (Array.isArray(list) ? list.filter((q) => isPlainObject(q) && typeof q.need === "string" && typeof q.detail === "string") : []);
  if (r) return { state: r.state, requires: fromList(r.requires) };
  if (g.available === false) {
    const names = Array.isArray(g.missing) ? g.missing.map(String) : [];
    return { state: "missing_dependency", requires: names.length ? [requirement("dependency", { names })] : [] };
  }
  return null;
}

/** 1.5 MB, 800 KB, 512 B: sizes in a message. */
function formatBytes(n) {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1).replace(/\.0$/, "")} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

/** The envelope with the router's placement and approvals added to receipt.execution (nothing else is touched). */
function stamp(env, prov) {
  if (!isPlainObject(env)) return env;
  const receipt = isPlainObject(env.receipt) ? env.receipt : {};
  const execution = isPlainObject(receipt.execution) ? receipt.execution : {};
  return { ...env, receipt: { ...receipt, execution: { ...execution, placement: prov.placement, approvals: prov.approvals } } };
}

/** A stop: outcome "cancelled" while the runtime had the call, "not_executed" when it was stopped before it got it. */
function cancelled(name, r, input, outcome = "cancelled") {
  return synthEnvelope({
    tool: name, via: r.entry?.id, args: input, status: "cancelled", kind: r.entry?.kind,
    summary: t("core.router.cancelled", { name: r.label }),
    text: "The call was stopped before it finished; there is no result.",
    error: null, outcome,
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
 * verdict and no output hash; the receipt (core/receipt.js) says it was decided here (where "page", decided_by) and
 * that it was not executed — or "failed" when a runtime threw, "cancelled" for a stop. error may carry reason and
 * requires (an unavailable placement).
 */
export async function synthEnvelope({ tool, via = null, args = {}, status, summary = "", text = "", error = null, approval = null, kind, where = "page", result = null, refusals = [], decided_by = "router", outcome }) {
  // the same first line as the Python envelopes ("tool → via: status"), so the model reads every outcome alike
  // and the error's type (its message is in the UI's language; the model gets the English hint)
  const head = `${tool} → ${via || tool}: ${status}${error?.type ? `\nError (${error.type})${error.hint ? `. Hint: ${error.hint}` : ""}` : ""}`;
  const body = String(text || "");
  return {
    ok: status === "succeeded",
    tool, via: via || tool, status, duration_ms: 0, summary, text: clip(body.startsWith(head) ? body : `${head}\n${body}`.trim(), TEXT_LIMIT), result,
    citations: [],
    governance: { kind: kind || "system", released: null, artifact: null, verdict: null, claims: [], evidence: [], refusals: Array.isArray(refusals) ? refusals : [], labels: [], licences: [], limitations: [], outputs: [] },
    receipt: baseReceipt({
      where, runtime: "studio-router", device: null, versions: {}, decided_by,
      input_sha256: await inputSha256(args), outcome: outcome || pageOutcome(status, error),
    }),
    job: null, approval, error,
  };
}

/** sha256 of the canonical JSON of a call's arguments (what receipt.input_sha256 holds). */
export async function inputSha256(args) {
  try { return await sha256Hex(canonicalJson(args || {})); } catch { return null; }
}

/**
 * Fill the fields a runtime left out, cap `text`, and record where the call ran. The runtime's receipt is kept as it
 * wrote it: no schema, result or execution is made up for it (an envelope without receipt.schema is a legacy one).
 */
export async function normalizeEnvelope(env, { tool, where, entry } = {}) {
  if (!isPlainObject(env)) {
    return synthEnvelope({ tool, via: entry?.id, status: "failed", summary: "invalid result", text: "The runtime returned no envelope; there is no result.", error: { type: "runtime_error", message: "the runtime returned no envelope", hint: "" } });
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
