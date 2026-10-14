// The app controller: state, data, the agent turn, runtimes, routes and shortcuts. Views (shell, conversation,
// pages) render from `app.state` and call `app` actions; they subscribe to `app.on(type)` for the parts that change.
//
// Events: route · projects · conversations · conversation · messages · turn (live updates) · inspector · runtime ·
// settings · catalog · relay · lang · layout · privacy.
//
// Telemetry (core/telemetry.js, studio/docs/TELEMETRY.md): usage events as the app works (routes, turns, tools, research
// workflows, availability, errors), and — only while the visitor agrees, and only for conversations that may be
// recorded at all (core/turnrecord.js) — one record per finished turn. None of it is ever awaited by a turn: a
// telemetry failure leaves the conversation exactly as it would have been.

import { runTurn } from "../core/agent.js";
import { loadCatalog } from "../core/catalog.js";
import { Emitter } from "../core/events.js";
import { lang, onLangChange, setLang, t } from "../core/i18n.js";
import { buildSystemPrompt } from "../core/prompt.js";
import { ownerHash } from "../core/consent.js";
import { activeProvider, relayBaseUrl, relayHealth } from "../core/providers.js";
import { Telemetry, STALE_TURN_MS } from "../core/telemetry.js";
import { WORKFLOW_KINDS, token } from "../core/telemetry-contract.js";
import { STICKY_EXCLUSIONS, buildRecord, exclusionOf, turnEndEvent, turnStatus } from "../core/turnrecord.js";
import { ToolRouter } from "../core/router.js";
import { loadSettings, onSettingsChange, updateSettings } from "../core/settings.js";
import { latestLeaf, openStore, siblingsOf, threadPath } from "../core/store.js";
import { sha256Hex, sleep, uuid } from "../core/util.js";
import { copyText, debounce, h, throttle } from "./dom.js";
import { announce, closeTopOverlay, hasOverlay, installTooltips, toast } from "./overlay.js";
import { inlinedInPrompt } from "./files.js";
import { HashRouter, routeHref } from "./router.js";
import { registerUiStrings } from "./strings.js";

const DRAFT_SAVE_MS = 1500;
const PEER_CHANNEL = "tcmstudio";

export class App {
  // projectId → the web access last asked for, while the write that stores it is still under way
  #webIntent = new Map();
  // writes of project defaults (web, compute, model), one at a time and in the order they were asked for
  #defaultsQueue = Promise.resolve();
  // PUT /api/settings to the runner, one at a time: the answers then arrive in the order of the presses
  #runnerQueue = Promise.resolve();
  #webToastClose = null;
  #peers = null;
  #sending = false;
  #capsP = null;
  #relayCheck = null;
  #toolNames = new Set();
  #jobsReported = new Set();

  constructor() {
    this.bus = new Emitter();
    this.telemetry = new Telemetry();
    this.store = null;
    this.runtimes = { browser: null, runner: null };
    this.catalog = null;
    this.toolRouter = null;
    this.router = null;
    this.views = {};
    this.state = {
      settings: loadSettings(),
      route: { name: "home" },
      layout: layoutMode(),
      projects: [],
      archived: [],
      project: null,
      conversations: [],          // of the current project
      recent: [],                 // across projects, for home and the palette
      conversation: null,
      messages: [],               // all messages of the open conversation (the tree)
      turn: null,                 // the live turn: {conversationId, userId, live, abort}
      draftOverride: null,        // {provider, model} chosen in a new conversation before it exists
      inspector: { open: false, tab: "run", scope: "turn", messageId: null, selection: null },
      sidebarOpen: false,         // the off-canvas / overlay sidebar on narrow screens
      relay: null,
      runner: { status: "idle", info: null, error: null, devices: null, settings: null },
      browser: { status: "idle", progress: null, message: "", device: null, caps: null },
      storageWarning: null,
    };
  }

  on(type, fn) { return this.bus.on(type, fn); }
  emit(type, payload) { this.bus.emit(type, payload); }

  // ----------------------------------------------------------------------------------------------- boot

  async boot(root) {
    registerUiStrings();
    installTooltips();
    this.telemetry.start();
    this.root = root;
    applyTheme(this.state.settings.theme);
    document.documentElement.lang = lang() === "zh" ? "zh-Hans" : "en";
    document.documentElement.dataset.lang = lang();
    const q = new URLSearchParams(location.search).get("lang");
    if ((q === "zh" || q === "en") && q !== this.state.settings.lang) setLang(q);

    this.store = await openStore();
    if (!this.store.persistent) this.state.storageWarning = t("ui.storage.memory");
    // projects from before web access was on by default, read once as they read now (store.js webOnByDefault)
    try { await this.store.projects.adoptWebDefault(); } catch (err) { console.warn("web access default not applied", err); }
    this.connectPeers();

    // a pairing link from `tcmstudio serve` (#pair=…): take it before the router reads the hash
    let pairing = null;
    let runtimeMod = null;
    try {
      runtimeMod = await import("../runtime/index.js");
      this.runtimeMod = runtimeMod;
      const runnerMod = await import("../runtime/runner.js");
      pairing = runnerMod.takePairing?.() || null;
      this.runnerMod = runnerMod;
    } catch (err) {
      console.warn("runtime modules unavailable", err);
    }
    if (pairing) this.state.settings = updateSettings({ runner: { url: pairing.url, token: pairing.token } });

    try {
      this.runtimes = runtimeMod ? await runtimeMod.createRuntimes(this.state.settings) : stubRuntimes();
    } catch (err) {
      console.warn("createRuntimes failed", err);
      this.runtimes = stubRuntimes();
    }
    this.#watchRuntimes();
    this.catalog = await loadCatalog(this.runtimes).catch(() => null);
    this.#toolNames = toolNamesOf(this.catalog);
    this.toolRouter = new ToolRouter({ catalog: this.catalog, runtimes: this.runtimes, settings: this.state.settings, store: this.store });
    this.telemetry.track("app_init", {
      layout: this.state.layout, lang: lang(), app: this.catalog?.versions?.tcmstudio, persistent: Boolean(this.store.persistent), ...clientFacts(),
    });
    if (!this.store.persistent) this.telemetry.track("availability", { service: "storage", ok: false, detail: "memory" });

    onSettingsChange(({ settings, changed }) => this.#onSettings(settings, changed));
    translateStatic();
    onLangChange((l) => {
      document.documentElement.lang = l === "zh" ? "zh-Hans" : "en";
      document.documentElement.dataset.lang = l;
      translateStatic();
      this.emit("lang", l);
    });
    window.addEventListener("resize", debounce(() => {
      const mode = layoutMode();
      if (mode !== this.state.layout) {
        this.state.layout = mode;
        this.state.sidebarOpen = false;
        this.emit("layout", mode);
      }
    }, 80));
    window.addEventListener("keydown", (e) => this.#onKey(e));
    // skip links move focus; they never change the hash (a hash is a route here: #composer-input is not a page)
    document.addEventListener("click", (e) => {
      const link = e.target.closest?.("a.skip-link");
      if (!link) return;
      e.preventDefault();
      skipTo(link.getAttribute("href"));
    });
    // code blocks from core/markdown.js: copy and wrap, by delegation (the renderer builds no handlers)
    document.addEventListener("click", async (e) => {
      const copy = e.target.closest?.("[data-copy-code]");
      if (copy) {
        const code = copy.closest(".md-code")?.querySelector("pre")?.textContent ?? "";
        const ok = await copyText(code);
        toast(ok ? t("ui.toast.code_copied") : t("ui.toast.copy_failed"), { tone: ok ? "ok" : "warn" });
        return;
      }
      const wrap = e.target.closest?.("[data-wrap-code]");
      if (wrap) {
        const block = wrap.closest(".md-code");
        const on = !block.classList.contains("is-wrapped");
        block.classList.toggle("is-wrapped", on);
        wrap.setAttribute("aria-pressed", String(on));
      }
    });

    await this.refreshProjects();
    const { mountShell } = await import("./shell.js");
    this.views.shell = mountShell(this, root);
    root.removeAttribute("aria-busy");

    this.router = new HashRouter((route, prev) => this.#onRoute(route, prev));
    await this.#onRoute(this.router.current, null);

    if (pairing) this.#afterPairing(pairing);
    else if (!this.state.settings.onboarded) import("./onboarding.js").then((m) => m.openOnboarding(this));
    // the relay's health also says what this site records; a turn cut off by the last page load is reported then, and
    // looked for again a little later (a page that went away a moment ago beat a moment ago) and, for browsers without
    // Web Locks, once its heartbeat would certainly have come (core/telemetry.js staleTurns)
    this.checkRelay().then(() => this.#recoverTurns());
    setTimeout(() => this.#recoverTurns(), 15000);
    setTimeout(() => this.#recoverTurns(), STALE_TURN_MS + 5000);
    this.on("job", (job) => this.#jobEvent(job));
    this.#watchConnectivity();
    this.#offline();
  }

  /** The service worker that keeps the app and its runtime usable offline (core/offline.js, /sw.js). */
  #offline() {
    import("../core/offline.js")
      .then((m) => m.registerServiceWorker({
        // a new version waits until the person chooses to reload into it: nothing changes under an open page
        onUpdate: (apply) => toast(t("ui.offline.update"), { action: { label: t("ui.offline.reload"), onClick: apply }, timeout: 0 }),
      }))
      .then((r) => { this.state.serviceWorker = r?.state || null; })
      .catch(() => { this.state.serviceWorker = "failed"; });
  }

  // ----------------------------------------------------------------------------------------------- connectivity

  /**
   * The device's own connection. Going offline is said once (what still works: tools in this browser, the local
   * runner, a local model); coming back re-checks the relay, whose health was otherwise only read at start, and lets a
   * paired runner that dropped meanwhile reconnect. navigator.onLine can only be trusted when it says offline.
   */
  #watchConnectivity() {
    this.state.online = globalThis.navigator?.onLine !== false;
    const recheck = debounce(() => {
      this.checkRelay();
      // the same rule as at start: only a runner this page was paired with, so no new permission prompt appears
      const r = this.runtimes.runner;
      if (r?.status === "offline" && this.runtimeMod?.shouldAutoConnect?.(this.state.settings)) r.start().catch(() => {});
    }, 800);
    window.addEventListener("offline", () => {
      this.state.online = false;
      this.emit("connectivity", { online: false });
      toast(t("ui.net.offline"), { tone: "warn", timeout: 8000 });
    });
    window.addEventListener("online", () => {
      const was = this.state.online;
      this.state.online = true;
      this.emit("connectivity", { online: true });
      if (!was) toast(t("ui.net.online"), { tone: "ok" });
      recheck();
    });
  }

  // ----------------------------------------------------------------------------------------------- settings

  /** The settings the tool router uses: the project's compute default (when set) over the global one. */
  effectiveSettings() {
    const s = this.state.settings;
    const c = this.state.project?.defaults?.compute;
    return c ? { ...s, compute: c } : s;
  }

  /** auto | browser | runner, for the open project. */
  compute() {
    return this.effectiveSettings().compute;
  }

  #onSettings(settings, changed = []) {
    this.state.settings = settings;
    this.toolRouter?.setSettings(this.effectiveSettings());
    if (changed.includes("theme") || changed.includes("*")) applyTheme(settings.theme);
    if (changed.includes("sidebarCollapsed")) document.documentElement.classList.toggle("sidebar-collapsed", settings.sidebarCollapsed);
    this.emit("settings", { settings, changed });
  }

  setSetting(patch) {
    this.state.settings = updateSettings(patch);
    return this.state.settings;
  }

  async checkRelay() {
    this.state.relay = { ...(this.state.relay || {}), checking: true };
    this.emit("relay", this.state.relay);
    this.#relayCheck = relayHealth();
    this.state.relay = await this.#relayCheck;
    // what this site records comes with the relay's health, when the relay is this page's own site
    this.telemetry.configure(this.state.relay, { relayBase: relayBaseUrl() });
    this.telemetry.track("availability", { service: "relay", ok: Boolean(this.state.relay.ok), detail: this.state.relay.error_type });
    this.emit("relay", this.state.relay);
    return this.state.relay;
  }

  // ----------------------------------------------------------------------------------------------- runtimes

  #watchRuntimes() {
    const { browser, runner } = this.runtimes;
    if (runner) {
      this.state.runner.status = runner.status;
      runner.onStatus?.(async (ev) => {
        const st = typeof ev === "string" ? { status: ev } : ev || {};
        this.#availability("runner", this.state.runner.status, st.status);
        this.state.runner = { ...this.state.runner, status: st.status || runner.status, error: st.error || null, info: st.info || runner._info || this.state.runner.info };
        this.emit("runtime", { kind: "runner" });
        if (st.status === "ready") {
          this.#afterRunnerReady();
        }
      });
    }
    if (browser) {
      this.state.browser.status = browser.status;
      browser.onStatus?.((ev) => {
        const st = typeof ev === "string" ? { status: ev } : ev || {};
        this.#availability("browser_runtime", this.state.browser.status, st.status);
        this.state.browser = {
          ...this.state.browser, status: st.status || browser.status,
          progress: typeof st.progress === "number" ? st.progress : st.status === "ready" ? 100 : this.state.browser.progress,
          message: st.message || "", error: st.error || null,
        };
        this.emit("runtime", { kind: "browser" });
      });
    }
  }

  /** A runtime that became ready, or failed: one availability event per change (not the steps in between). */
  #availability(service, before, after) {
    if (!after || after === before || !["ready", "error"].includes(after)) return;
    this.telemetry.track("availability", { service, ok: after === "ready" });
  }

  async #afterRunnerReady() {
    const runner = this.runtimes.runner;
    try { this.state.runner.info = await runner.info(); } catch { /* keep what the status gave */ }
    try { this.state.runner.devices = await runner.device(); } catch { this.state.runner.devices = null; }
    try { this.state.runner.settings = await runner.settings.get(); } catch { this.state.runner.settings = this.state.runner.info?.settings || null; }
    // the runner knows what it can run: merge its catalog
    try {
      this.catalog = await loadCatalog(this.runtimes);
      this.#toolNames = toolNamesOf(this.catalog);
      this.toolRouter.setCatalog(this.catalog);
      this.emit("catalog", this.catalog);
    } catch { /* keep the built catalog */ }
    this.emit("runtime", { kind: "runner" });
  }

  /** Connect the runner (a button press: waits for the browser's local-network prompt). */
  async connectRunner({ url, token } = {}) {
    const runner = this.runtimes.runner;
    if (!runner) return { ok: false, message: t("ui.runner.unavailable") };
    const cur = this.state.settings.runner;
    const nextUrl = (url ?? cur.url).trim();
    const nextToken = (token ?? cur.token).trim();
    try {
      runner.pair?.(nextUrl, nextToken);
      this.setSetting({ runner: { url: nextUrl, token: nextToken } });
      await runner.start({ interactive: true });
      toast(t("ui.runner.connected"), { tone: "ok" });
      return { ok: true };
    } catch (err) {
      const message = err?.message || String(err);
      this.state.runner.error = err;
      this.emit("runtime", { kind: "runner" });
      return { ok: false, message, code: err?.code };
    }
  }

  disconnectRunner() {
    this.runtimes.runner?.stop?.();
    this.state.runner = { ...this.state.runner, status: "idle", info: null, devices: null };
    this.emit("runtime", { kind: "runner" });
  }

  async preloadBrowser() {
    try { await this.runtimes.browser?.start?.(); } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
  }

  /** What this browser can compute with (compute/capabilities.js), probed once per page. */
  async computeCapabilities() {
    if (this.state.browser.caps) return this.state.browser.caps;
    this.#capsP ||= import("../compute/capabilities.js")
      .then((m) => m.probeCapabilities({ timeoutMs: 2500 }))
      .then((caps) => { this.state.browser.caps = caps; this.emit("runtime", { kind: "browser" }); return caps; })
      .catch(() => null);
    return this.#capsP;
  }

  async browserDevice() {
    if (this.state.browser.device) return this.state.browser.device;
    let dev = null;
    try { dev = await this.runtimes.browser?.device?.(); } catch { dev = null; }
    if (!dev) dev = await detectBrowserDevice();
    this.state.browser.device = dev;
    this.emit("runtime", { kind: "browser" });
    return dev;
  }

  /**
   * Change some of the runner's settings (the device, threads, its network switch). Only the changed fields are sent:
   * the runner merges them into what it holds, which another tab or `tcmstudio serve --network` may have changed since
   * this page last read it, so an older copy is never written back over them. One request at a time, so the answers
   * come back in the order of the presses. Whatever happens, the views are told to show what the runner now holds:
   * a switch that flipped when pressed flips back when the change did not happen. → the runner's settings, or null.
   */
  updateRunnerSettings(patch) {
    const run = async () => {
      const runner = this.runtimes.runner;
      try {
        if (!runner || this.state.runner.status !== "ready") {
          toast(t("ui.runner.settings_offline"), { tone: "warn" });
          return null;
        }
        try {
          const next = await runner.settings.put(patch);
          this.state.runner.settings = next && typeof next === "object" ? next : mergeRunnerSettings(this.state.runner.settings, patch);
          return this.state.runner.settings;
        } catch (err) {
          toast(t("ui.runner.settings_failed", { message: err?.message || String(err) }), { tone: "warn", timeout: 6000 });
          try { this.state.runner.settings = await runner.settings.get(); } catch { /* keep the last copy read */ }
          return null;
        }
      } finally {
        this.emit("runtime", { kind: "runner" });
      }
    };
    const done = this.#runnerQueue.then(run, run);
    this.#runnerQueue = done.then(() => undefined, () => undefined);
    return done;
  }

  openPairing(pairing) {
    import("./panels.js").then((m) => m.openPairingDialog(this, pairing));
  }

  /**
   * A pairing link was opened. The pairing is already saved and the runner connects by itself: when it does, say so;
   * only when it does not (within a few seconds) open the dialog, whose Connect button retries.
   */
  async #afterPairing(pairing) {
    const status = await this.runnerSettled(5000);
    if (status === "ready") toast(t("ui.runner.paired", { host: hostOf(pairing.url) }), { tone: "ok" });
    else this.openPairing(pairing);
  }

  /** The runner's status once it is no longer connecting (or after `ms`). */
  runnerSettled(ms = 5000) {
    const pending = () => ["connecting", "idle"].includes(this.state.runner.status);
    if (!this.runtimes.runner || !pending()) return Promise.resolve(this.state.runner.status);
    return new Promise((resolve) => {
      let off = null;
      const done = () => { clearTimeout(timer); off?.(); resolve(this.state.runner.status); };
      const timer = setTimeout(done, ms);
      off = this.on("runtime", () => { if (!pending()) done(); });
    });
  }

  // ----------------------------------------------------------------------------------------------- data

  async refreshProjects() {
    this.state.projects = await this.store.projects.list({ archived: false });
    this.state.archived = await this.store.projects.list({ archived: true });
    this.state.recent = await this.store.conversations.recent(30);
    this.emit("projects", this.state.projects);
  }

  async refreshConversations() {
    const pid = this.state.project?.id;
    this.state.conversations = pid ? await this.store.conversations.list(pid) : [];
    this.state.recent = await this.store.conversations.recent(30);
    this.emit("conversations", this.state.conversations);
  }

  async loadMessages() {
    const conv = this.state.conversation;
    this.state.messages = conv ? await this.store.messages.list(conv.id) : [];
    this.emit("messages", this.state.messages);
  }

  /** The branch shown: root → leaf, without tool messages. */
  path() {
    const conv = this.state.conversation;
    if (!conv) return [];
    return threadPath(this.state.messages, conv.leafId);
  }

  toolMessagesOf(assistantId) {
    return this.state.messages.filter((m) => m.role === "tool" && m.parentId === assistantId);
  }

  // ----------------------------------------------------------------------------------------------- routes

  navigate(route, opts) {
    this.router.navigate(route, opts);
  }

  async #onRoute(route) {
    this.state.sidebarOpen = false;
    if (route.name === "home") {
      const last = this.state.recent[0];
      if (last) return this.navigate({ name: "conversation", projectId: last.projectId, convId: last.id }, { replace: true });
      if (this.state.projects[0]) return this.navigate({ name: "project", projectId: this.state.projects[0].id }, { replace: true });
      this.state.project = null;
      this.state.conversation = null;
      this.state.messages = [];
    } else if (route.name === "project" || route.name === "conversation") {
      const project = await this.store.projects.get(route.projectId);
      if (!project) {
        toast(t("ui.project.missing"), { tone: "warn" });
        return this.navigate({ name: "home" }, { replace: true });
      }
      const changedProject = this.state.project?.id !== project.id;
      this.state.project = project;
      this.toolRouter?.setSettings(this.effectiveSettings());
      if (changedProject || !this.state.conversations.length) await this.refreshConversations();
      if (route.name === "conversation") {
        const conv = await this.store.conversations.get(route.convId);
        if (!conv || conv.projectId !== project.id) {
          toast(t("ui.conv.missing"), { tone: "warn" });
          return this.navigate({ name: "project", projectId: project.id }, { replace: true });
        }
        const switched = this.state.conversation?.id !== conv.id;
        this.state.conversation = conv;
        if (switched) {
          this.state.inspector = { ...this.state.inspector, messageId: null, selection: null };
          await this.loadMessages();
        }
      } else {
        this.state.conversation = null;
        this.state.messages = [];
        this.state.draftOverride = null;
      }
    } else {
      this.state.conversation = null;
    }
    this.state.route = route;
    this.emit("route", route);
    this.telemetry.route(route);
  }

  // ----------------------------------------------------------------------------------------------- projects

  async createProject({ name, open = true } = {}) {
    const p = await this.store.projects.create({ name: name || t("ui.project.default_name"), web: this.state.settings.web });
    await this.refreshProjects();
    if (open) this.navigate({ name: "project", projectId: p.id });
    return p;
  }

  async updateProject(id, patch) {
    const p = await this.store.projects.update(id, patch);
    this.#adoptProject(p);
    try {
      await this.refreshProjects();
    } finally {
      // the change is stored: the views hear of it even when re-reading the lists fails
      this.emit("project", p);
      this.#tellPeers(p.id);
    }
    return p;
  }

  /**
   * Change some of a project's defaults ({web, compute, provider, model}; undefined removes one), merged into the
   * stored record inside its transaction (store.projects.setDefaults), and queued behind the writes asked for before
   * it, so they land in the order of the presses. Rejects when the write fails; nothing else is changed then.
   */
  updateProjectDefaults(id, partial) {
    const run = async () => {
      const p = await this.store.projects.setDefaults(id, partial);
      this.#adoptProject(p);
      this.emit("project", p);
      this.#tellPeers(p.id);
      // the lists only follow (the sidebar's order by updatedAt): a failure there does not undo the change
      this.refreshProjects().catch(() => {});
      return p;
    };
    const done = this.#defaultsQueue.then(run, run);
    this.#defaultsQueue = done.then(() => undefined, () => undefined);
    return done;
  }

  /** The same, for a control: a failed write is said in a toast instead of being thrown. → the project, or null. */
  async saveProjectDefaults(id, partial) {
    try {
      return await this.updateProjectDefaults(id, partial);
    } catch (err) {
      toast(t("ui.project.save_failed", { message: err?.message || String(err) }), { tone: "warn", timeout: 6000 });
      await this.#reloadProject(id);
      return null;
    }
  }

  /** Revoke standing approvals of a project (store.projects.patchApprovals: a grant landing meanwhile is kept). */
  async revokeApprovals(id, keys) {
    const p = await this.store.projects.patchApprovals(id, { revoke: keys });
    this.#adoptProject(p);
    this.emit("project", p);
    this.#tellPeers(p.id);
    await this.refreshProjects().catch(() => {});
    return p;
  }

  /** A stored project record is the open one: the state and the tool router follow it. */
  #adoptProject(p) {
    if (!p || this.state.project?.id !== p.id) return;
    this.state.project = p;
    this.toolRouter?.setSettings(this.effectiveSettings());
  }

  /** Re-read a project from the store (after a failed write, or another tab's change) and tell the views. */
  async #reloadProject(id) {
    let p = null;
    try { p = await this.store.projects.get(id); } catch { p = null; }
    if (p) {
      this.#adoptProject(p);
      this.emit("project", p);
    }
    if (this.state.project?.id === id) this.emit("web", this.webOn());
    return p;
  }

  // ----------------------------------------------------------------------------------------------- other tabs

  /**
   * Tabs of this site share the store. When one changes a project, the others re-read it, so their switches show the
   * stored state (and a press there starts from it). BroadcastChannel is missing in some old browsers: then each tab
   * only sees its own changes, as before.
   */
  connectPeers({ channel } = {}) {
    if (this.#peers) return true;
    try {
      this.#peers = channel || (typeof BroadcastChannel === "function" ? new BroadcastChannel(PEER_CHANNEL) : null);
    } catch {
      this.#peers = null;
    }
    if (!this.#peers) return false;
    this.#peers.onmessage = (e) => { this.#onPeer(e?.data).catch(() => {}); };
    return true;
  }

  disconnectPeers() {
    try { this.#peers?.close?.(); } catch { /* closed */ }
    this.#peers = null;
  }

  #tellPeers(projectId) {
    try { this.#peers?.postMessage({ type: "project", id: projectId }); } catch { /* the channel is closed */ }
  }

  async #onPeer(msg) {
    if (!msg || msg.type !== "project" || typeof msg.id !== "string") return;
    // a press here that is still being written wins over the record another tab wrote before it
    if (this.state.project?.id === msg.id && !this.#webIntent.has(msg.id)) await this.#reloadProject(msg.id);
    await this.refreshProjects().catch(() => {});
  }

  async deleteProject(id) {
    // a record of one of its conversations that has not been sent yet is not sent
    for (const c of await this.store.conversations.list(id).catch(() => [])) this.telemetry.purgeRecords(c.id);
    const turn = this.state.turn;
    if (turn && turn.projectId === id) await this.#discardTurn(turn);
    await this.store.projects.remove(id);
    if (this.state.project?.id === id) {
      this.state.project = null;
      this.state.conversation = null;
    }
    await this.refreshProjects();
    this.navigate({ name: "home" });
  }

  async renameConversation(id, title) {
    const c = await this.store.conversations.update(id, { title, updatedAt: undefined });
    if (this.state.conversation?.id === id) this.state.conversation = c;
    await this.refreshConversations();
    this.emit("conversation", c);
  }

  async deleteConversation(id) {
    this.telemetry.purgeRecords(id); // a record of it that has not been sent yet is not sent
    const conv = await this.store.conversations.get(id);
    const turn = this.state.turn;
    if (turn && turn.conversationId === id) await this.#discardTurn(turn);
    await this.store.conversations.remove(id);
    await this.refreshConversations();
    if (this.state.conversation?.id === id) this.navigate({ name: "project", projectId: conv?.projectId || this.state.project?.id });
  }

  /**
   * The conversation of a running turn is being deleted: stop the turn and make sure nothing of it is written back
   * (its draft saves, its final answer and tool records). Waits for a save already under way, so the delete that
   * follows removes it too.
   */
  async #discardTurn(turn) {
    turn.discarded = true;
    turn.abort.abort();
    await turn.saving?.().catch?.(() => {});
  }

  /** A new conversation: the project page with its composer (the conversation is created on the first message). */
  newConversation(projectId = this.state.project?.id || this.state.projects[0]?.id) {
    this.state.draftOverride = null;
    if (projectId) this.navigate({ name: "project", projectId });
    else this.navigate({ name: "home" });
    requestAnimationFrame(() => this.focusComposer());
  }

  focusComposer() {
    document.getElementById("composer-input")?.focus();
  }

  // ----------------------------------------------------------------------------------------------- model & compute

  /** {provider, model} chosen for the open (or new) conversation, else the project's or the global default. */
  modelChoice() {
    const c = this.state.conversation;
    if (c?.provider) return { provider: c.provider, model: c.model || "" };
    if (!c && this.state.draftOverride) return this.state.draftOverride;
    const d = this.state.project?.defaults;
    if (d?.provider) return { provider: d.provider, model: d.model || "" };
    return { provider: this.state.settings.provider, model: this.state.settings.models?.[this.state.settings.provider] || "" };
  }

  provider() {
    const choice = this.modelChoice();
    return activeProvider(this.state.settings, {
      ...choice, runner: { ...this.state.settings.runner, connected: this.state.runner.status === "ready" },
      maxOutput: this.state.relay?.max_output_tokens || undefined,
    });
  }

  async chooseModel({ provider, model }) {
    const c = this.state.conversation;
    if (c) {
      const next = await this.store.conversations.update(c.id, { provider, model: model || "", updatedAt: c.updatedAt });
      this.state.conversation = next;
    } else {
      this.state.draftOverride = { provider, model: model || "" };
    }
    this.emit("model", this.modelChoice());
  }

  /** The compute target: the project's own default when it has one, else the global setting. */
  async setCompute(compute) {
    const p = this.state.project;
    if (p?.defaults?.compute) await this.saveProjectDefaults(p.id, { compute });
    else this.setSetting({ compute });
    this.emit("runtime", { kind: "compute" });
  }

  /**
   * Web access for the open project (with none open, the default for new projects). While a press is being written,
   * what that press asked for: a second press toggles from what the switch shows, not from the record it replaces.
   */
  webOn() {
    const p = this.state.project;
    if (!p) return Boolean(this.state.settings.web);
    if (this.#webIntent.has(p.id)) return this.#webIntent.get(p.id);
    // a project without its own value follows the default for new projects, as the router and the prompt do
    return typeof p.defaults?.web === "boolean" ? p.defaults.web : Boolean(this.state.settings.web);
  }

  /** Every web-access write asked for so far has landed (a turn about to start waits for this). */
  webSettled() {
    return this.#defaultsQueue;
  }

  /**
   * Turn web access on or off. Every view shows the new state at once ("web"); the write is queued behind the ones
   * before it and changes only `defaults.web` in the stored record, so quick presses end where the last one says
   * and a model or compute default changed meanwhile stays. The toast speaks when the last press is stored. When a
   * write fails, the views go back to what the store holds and a toast says why. → true when stored.
   */
  async setWeb(on) {
    const value = Boolean(on);
    const p = this.state.project;
    if (!p) {
      this.setSetting({ web: value });
      this.emit("web", value);
      this.#webToast(value ? t("ui.web.on_toast") : t("ui.web.off_toast"));
      return true;
    }
    const id = p.id;
    this.#webIntent.set(id, value);
    this.emit("web", value);
    try {
      await this.updateProjectDefaults(id, { web: value });
    } catch (err) {
      // a later press still queued settles the switch itself; this one only clears its own intent
      if (this.#webIntent.get(id) === value) this.#webIntent.delete(id);
      this.#webToast(t("ui.web.failed", { message: err?.message || String(err) }), "warn");
      await this.#reloadProject(id);
      return false;
    }
    if (this.#webIntent.get(id) !== value) return true; // a later press is on its way
    this.#webIntent.delete(id);
    this.emit("web", this.webOn());
    if (this.state.project?.id === id) this.#webToast(value ? t("ui.web.on_toast") : t("ui.web.off_toast"));
    return true;
  }

  /** One toast for web access at a time: a newer press replaces the word of the one before it. */
  #webToast(message, tone = "neutral") {
    this.#webToastClose?.();
    this.#webToastClose = toast(message, { tone, ...(tone === "warn" ? { timeout: 6000 } : {}) });
  }

  // ----------------------------------------------------------------------------------------------- turns

  /**
   * Send a user message: in the open conversation (after the shown branch), or in a new one. `files` are File
   * objects already hashed into the project (records from addFiles) or raw Files.
   */
  async send(text, { files = [], editOf = null } = {}) {
    const content = String(text || "").trim();
    if (!content || this.state.turn || this.#sending) return false;
    this.#sending = true;
    try {
      return await this.#send(content, { files, editOf });
    } finally {
      this.#sending = false;
    }
  }

  async #send(content, { files, editOf }) {
    // typed in a composer that is about to be replaced (the project page → the conversation): its successor takes focus
    const typing = document.activeElement?.id === "composer-input";
    // the conversation-record notice, once, before the first question (only on a site that takes records)
    await this.#consentGate();
    // web access (or compute) just switched: the turn starts once that is stored, so its tools see what the switch shows
    await this.webSettled();
    let project = this.state.project;
    if (!project) {
      project = this.state.projects[0] || await this.store.projects.create({ name: t("ui.project.default_name"), web: this.state.settings.web });
      this.state.project = project;
      await this.refreshProjects();
    }
    // the attachments are stored first: when that fails (quota, memory) nothing is sent and no empty conversation is left
    const records = [];
    try {
      for (const f of files) {
        if (f?.id && f.sha256 && f.blob) { records.push(f); continue; }
        const file = f?.file || f;
        records.push(await this.store.files.add(project.id, file, { name: file.name, source: "upload", sha256: f?.sha256 || undefined }));
      }
    } catch (err) {
      toast(t("ui.composer.store_failed", { message: err?.message || String(err) }), { tone: "warn", timeout: 8000 });
      return false;
    }
    let conv = this.state.conversation;
    if (!conv) {
      const choice = this.state.draftOverride || {};
      conv = await this.store.conversations.create({ projectId: project.id, title: autoTitle(content), provider: choice.provider, model: choice.model });
      this.state.conversation = conv;
      this.state.messages = [];
      this.state.draftOverride = null;
    }
    let parentId;
    if (editOf) parentId = editOf.parentId ?? null;
    else {
      const path = threadPath(this.state.messages, conv.leafId);
      parentId = path.length ? path[path.length - 1].id : null;
    }
    const user = await this.store.messages.put({
      conversationId: conv.id, parentId, role: "user", content,
      attachments: records.map((r) => r.id), attachmentNames: records.map((r) => r.name),
    });
    conv = await this.store.conversations.update(conv.id, { leafId: user.id, title: conv.title || autoTitle(content) });
    this.state.conversation = conv;
    await this.loadMessages();
    await this.refreshConversations();
    const route = this.state.route;
    if (route.name !== "conversation" || route.convId !== conv.id) {
      if (typing) this.state.focusComposerOnMount = true;
      this.navigate({ name: "conversation", projectId: project.id, convId: conv.id }, { replace: route.name === "project" || route.name === "home" });
    }
    this.#runAssistant(conv, user);
    return true;
  }

  async regenerate(assistantId) {
    // a question being sent (the notice may be open before it) is a turn about to start
    if (this.state.turn || this.#sending) return;
    const a = this.state.messages.find((m) => m.id === assistantId);
    const user = a && this.state.messages.find((m) => m.id === a.parentId);
    if (!user) return;
    this.#runAssistant(this.state.conversation, user);
  }

  /** Editing a user message adds a sibling with the same parent (a branch) and answers it. */
  async editMessage(messageId, text) {
    const m = this.state.messages.find((x) => x.id === messageId);
    if (!m || this.state.turn) return;
    await this.send(text, { editOf: m });
  }

  async switchBranch(messageId, dir) {
    const sibs = siblingsOf(this.state.messages, messageId);
    const i = sibs.findIndex((s) => s.id === messageId);
    const next = sibs[i + dir];
    if (!next) return;
    const leaf = latestLeaf(this.state.messages, next.id);
    const conv = await this.store.conversations.update(this.state.conversation.id, { leafId: leaf, updatedAt: this.state.conversation.updatedAt });
    this.state.conversation = conv;
    this.state.inspector = { ...this.state.inspector, messageId: null, selection: null };
    this.emit("messages", this.state.messages);
  }

  stop() {
    if (!this.state.turn) return false;
    this.state.turn.abort.abort();
    return true;
  }

  /** Ask the visitor about conversation records when the site takes them and they have not answered this notice. */
  async #consentGate() {
    try {
      if (!this.telemetry.config && this.#relayCheck) await Promise.race([this.#relayCheck, sleep(2500)]);
      if (!this.telemetry.needsConsent()) return;
      const { askConsent } = await import("./privacy.js");
      await askConsent(this);
    } catch { /* recording stays off */ }
  }

  async #knowledge(projectId) {
    const files = await this.store.files.list(projectId);
    const out = [];
    for (const f of files) {
      const rec = { name: f.name, bytes: f.bytes, sha256: f.sha256, type: f.type };
      if (f.blob && inlinedInPrompt(f)) {
        try { rec.text = await f.blob.text(); } catch { /* binary after all */ }
      }
      out.push(rec);
    }
    return out;
  }

  #env() {
    const r = this.state.runner;
    return {
      date: new Date().toISOString().slice(0, 10),
      web: this.webOn(),
      compute: this.compute(),
      browser: { status: this.state.browser.status, device: "cpu (WebAssembly, single thread)" },
      runner: {
        status: r.status, url: this.state.settings.runner.url, version: r.info?.version || null,
        devices: (r.devices?.devices || r.info?.devices || []).map((d) => ({ id: d.id, name: d.name, kind: d.kind, available: d.available })),
      },
    };
  }

  async #runAssistant(conv, user) {
    const provider = this.provider();
    const project = this.state.project;
    const draftId = uuid();
    const live = newLive(draftId, user.id, provider);
    const abort = new AbortController();
    let saving = Promise.resolve();
    const turn = { conversationId: conv.id, projectId: conv.projectId || project?.id, userId: user.id, live, abort, discarded: false, saving: () => saving };
    this.state.turn = turn;
    // a question, and the first of its conversation or not; the turn beats while it runs, so that the next page load
    // can tell one that was cut off (core/telemetry.js staleTurns)
    // a conversation's first question: nothing answered in it yet (a regenerated first answer, or an edited first
    // question, is the same conversation again, not a new one)
    turn.first = !this.state.messages.some((m) => m.role === "assistant") && this.state.messages.filter((m) => m.role === "user").length <= 1;
    // a question is activity: a visit begins when it is the first in half an hour, route change or not
    this.telemetry.active(this.state.route?.name);
    // the visitor's agreement as the question is sent: a record needs it then and still at the end (agreeing while an
    // answer streams, or after a reload cut one off, does not reach back to it)
    turn.consent = this.telemetry.contentOn;
    this.telemetry.track("turn_start", { provider: provider.id, model: provider.model, first: turn.first });
    this.telemetry.turnBegan({ id: draftId, conversationId: conv.id, projectId: turn.projectId, provider: provider.id, model: provider.model, first: turn.first, consent: turn.consent, startedAt: live.startedAt });
    turn.beat = setInterval(() => this.telemetry.turnBeat(draftId), 5000);
    this.state.inspector = { ...this.state.inspector, messageId: draftId, selection: null };
    this.emit("turn", { type: "start", live });

    if (provider.needsKey) {
      live.error = { message: t("core.provider.no_key", { label: provider.label }), kind: "model", needsKey: true };
      live.status = "error";
      return this.#finishTurn(conv, user, live, null, turn);
    }

    // The draft: the answer so far and every tool result with its envelope, saved at least every DRAFT_SAVE_MS while
    // the turn runs (a throttle, not a debounce: a stream never pauses long enough for a debounce to fire), and once
    // more when the page is hidden or closed. Tool records get ids from the call (${draftId}:${callId}), so the final
    // save replaces them instead of adding a second copy.
    let finalized = false;
    let leafSaved = false;
    const savedTools = new Map(); // callId → the envelope object last written
    turn.draftToolIds = new Set();
    const saveDraft = throttle(() => {
      if (finalized || turn.discarded) return;
      saving = saving.then(async () => {
        if (finalized || turn.discarded) return;
        // the conversation was deleted (here or in another tab): write nothing back
        if (!(await this.store.conversations.get(conv.id))) { turn.discarded = true; return; }
        await this.store.messages.put(liveToRecord(live, conv.id, { draft: true }));
        for (const m of liveToolMessages(live, conv.id)) {
          if (savedTools.get(m.toolCallId) === m.envelope) continue;
          await this.store.messages.put(m);
          savedTools.set(m.toolCallId, m.envelope);
          turn.draftToolIds.add(m.id);
        }
        if (!leafSaved) {
          leafSaved = true;
          const saved = await this.store.conversations.update(conv.id, { leafId: draftId });
          if (this.state.conversation?.id === conv.id) this.state.conversation = saved;
        }
      }).catch(() => {});
    }, DRAFT_SAVE_MS);
    const onHide = () => { if (document.visibilityState === "hidden") { saveDraft(); saveDraft.flush(); } };
    const onPageHide = () => { saveDraft(); saveDraft.flush(); };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onPageHide);

    const knowledge = await this.#knowledge(project.id);
    turn.projectFiles = knowledge.length;
    // what keeps this conversation out is known before the model sees anything (an upload, the project's files, which
    // go to the model with every question, identifying text): noted now, so that it holds even once the files are
    // removed, and so that a page cut off mid-answer still knows
    const early = exclusionOf({ path: threadPath(this.state.messages, user.id), projectFiles: turn.projectFiles });
    if (STICKY_EXCLUSIONS.includes(early)) await this.#keepOut(conv.id, early);
    const system = buildSystemPrompt({ lang: lang(), provider, project, knowledge, env: this.#env() });
    const history = threadPath(await this.store.messages.list(conv.id), user.id);
    let result = null;
    try {
      result = await runTurn({
        provider, system, history, router: this.toolRouter, catalog: this.catalog, settings: this.state.settings, signal: abort.signal, llm: this.llm,
        projectId: project.id, conversationId: conv.id,
        onApproval: (request) => new Promise((resolve) => {
          const tool = live.tools.get(request.callId);
          if (tool) {
            tool.approval = { request, decided: null, resolve: (d) => { tool.approval.decided = d; live.approvals[request.callId] = d; resolve(d); this.emit("turn", { type: "update", live }); } };
            tool.call.status = "needs_approval";
          }
          announce(t("ui.perm.title"), { assertive: true });
          this.emit("turn", { type: "approval", live, callId: request.callId });
          if (!tool) resolve("deny");
        }),
        onEvent: (ev) => {
          applyEvent(live, ev);
          if (ev.type === "tool.start" || ev.type === "tool.end") this.#toolEvent(ev, live);
          if (ev.type === "tool.end") this.#announceTool(ev.envelope);
          if (ev.type === "tool.start" && !this.state.inspector.open && this.state.layout === "wide" && !this.state.inspector.autoOpened) {
            // the inspector opens on the first tool result of a conversation (DESIGN §4.2)
            this.state.inspector = { ...this.state.inspector, open: true, autoOpened: true };
            this.emit("inspector", this.state.inspector);
          }
          this.emit("turn", { type: "event", live, ev });
          saveDraft();
        },
      });
    } catch (err) {
      live.error = { message: err?.message || String(err), kind: "runtime" };
      live.status = "error";
    }
    finalized = true;
    saveDraft.cancel();
    document.removeEventListener("visibilitychange", onHide);
    window.removeEventListener("pagehide", onPageHide);
    await saving;
    return this.#finishTurn(conv, user, live, result, turn);
  }

  async #finishTurn(conv, user, live, result, turn = null) {
    const store = this.store;
    let assistant;
    let toolMessages = [];
    if (result) {
      assistant = { ...result.assistant, id: live.id, parentId: user.id, conversationId: conv.id };
      toolMessages = result.toolMessages.map((m) => ({ ...m, id: toolMessageId(live.id, m.toolCallId), parentId: live.id, conversationId: conv.id }));
      if (result.status === "stopped") assistant.status = "stopped";
    } else {
      assistant = liveToRecord(live, conv.id, {});
      assistant.status = live.status === "error" ? "error" : "stopped";
      if (live.error) assistant.error = live.error;
      toolMessages = liveToolMessages(live, conv.id).map(({ draft, ...m }) => m);
    }
    assistant.ui = { thinkingMs: live.thinkingMs, approvals: live.approvals, startedAt: live.startedAt, endedAt: Date.now() };
    delete assistant.draft;
    if (turn?.beat) clearInterval(turn.beat);
    // a turn whose conversation was deleted while it ran writes nothing back (the person deleted that data)
    const discarded = Boolean(turn?.discarded) || !(await store.conversations.get(conv.id).catch(() => null));
    if (!discarded) {
      try {
        await store.messages.put(assistant);
        for (const m of toolMessages) await store.messages.put(m);
        // draft tool records the final answer does not carry (none, normally) do not outlive the draft
        const kept = new Set(toolMessages.map((m) => m.id));
        for (const id of turn?.draftToolIds || []) if (!kept.has(id)) await store.messages.remove(id);
        // the person may have moved on while the turn ran: update the record, and the view only if it shows it
        const saved = await store.conversations.update(conv.id, { leafId: assistant.id });
        if (this.state.conversation?.id === conv.id) this.state.conversation = saved;
      } catch (err) {
        toast(t("ui.turn.save_failed", { message: err?.message || String(err) }), { tone: "warn" });
      }
    }
    this.state.turn = null;
    // counted, and recorded when it may be: after the answer is stored and shown, never in its way
    this.#reportTurn({ conv, user, assistant, toolMessages, live, discarded, turn });
    if (this.state.conversation?.id === conv.id) await this.loadMessages();
    await this.refreshConversations();
    this.emit("turn", { type: "end", live });
    const n = toolMessages.length;
    if (discarded) announce(t("core.agent.stopped"));
    else if (assistant.status === "error") announce(t("ui.turn.failed_announce"), { assertive: true });
    else if (assistant.status === "stopped") announce(t("core.agent.stopped"));
    else announce(n ? t("ui.turn.done_tools", { n }) : t("ui.turn.done"));
  }

  // ----------------------------------------------------------------------------------------------- telemetry

  /**
   * A finished (or cut-off) turn for the telemetry: the usage event always, the conversation record when the visitor
   * agrees and the conversation may be recorded. Never throws, never awaited by the turn.
   */
  async #reportTurn({ conv, user, assistant, toolMessages = [], live, discarded = false, turn = null, interrupted = false, endedAt }) {
    const tel = this.telemetry;
    try {
      const all = discarded ? [] : await this.store.messages.list(conv.id).catch(() => []);
      const path = threadPath(all.some((m) => m.id === user.id) ? all : [user], user.id);
      const seen = new Set();
      const known = [...all.filter((m) => m.role === "tool"), ...toolMessages].filter((m) => !seen.has(m.id) && seen.add(m.id));
      const projectFiles = turn?.projectFiles ?? (await this.store.files.list(conv.projectId).catch(() => [])).length;
      const before = discarded ? null : (await this.store.conversations.get(conv.id).catch(() => null))?.recordExcluded;
      const reason = exclusionOf({ path, tools: known, projectFiles, discarded, before });
      // judged whole: once a turn may not be recorded, no later turn of the conversation is
      if (reason && reason !== before && STICKY_EXCLUSIONS.includes(reason)) await this.#keepOut(conv.id, reason);
      const tools = toolMessages.map((m) => this.#namedTool(m));
      const base = {
        id: assistant.id, provider: assistant.provider, model: assistant.model, status: turnStatus(assistant, { interrupted }),
        first: turn?.first ?? path.filter((m) => m.role === "user").length <= 1, startedAt: live?.startedAt,
        endedAt: endedAt ?? assistant.ui?.endedAt ?? Date.now(), firstAt: live?.firstAt, calls: live?.calls, assistant, tools,
        known: known.map((m) => this.#namedTool(m)),
      };
      const agreed = tel.contentOn && turn?.consent === true;
      const excluded = reason || (!tel.conversationsAvailable ? "unavailable" : !agreed ? "no_consent" : null);
      let recorded = false;
      if (!excluded) {
        const owner = await ownerHash();
        const record = owner && buildRecord({
          ...base, conversation: conv.id, index: path.filter((m) => m.role === "user").length, owner, question: user.content,
          app: this.catalog?.versions || null, runtime: this.#runtimeFacts(),
        });
        if (record) {
          tel.sendRecord(record, { conversationId: conv.id });
          recorded = true;
        }
      }
      tel.track("turn_end", turnEndEvent({ ...base, excluded: excluded || undefined, recorded }));
    } catch { /* never the conversation's concern */ } finally {
      tel.turnEnded(live?.id || assistant?.id);
    }
  }

  /** A conversation that may never be recorded again, and why (kept with it: exclusionOf's `before`). */
  async #keepOut(conversationId, reason) {
    try {
      const cur = await this.store.conversations.get(conversationId);
      if (cur && !cur.recordExcluded) await this.store.conversations.note(conversationId, { recordExcluded: reason });
    } catch { /* the next turn judges again */ }
  }

  /** A tool message with a name the catalog knows, else "unknown" (a model may invent a name; it is never sent). */
  #namedTool(m) {
    const name = String(m?.envelope?.via || m?.name || "");
    return this.#toolNames.has(name) ? m : { ...m, name: "unknown", envelope: m?.envelope ? { ...m.envelope, via: "unknown" } : m?.envelope };
  }

  #runtimeFacts() {
    return {
      compute: this.compute(), browser: this.state.browser.status, runner: this.state.runner.info?.version || undefined,
      layout: this.state.layout, lang: lang(),
    };
  }

  /** A tool run (and a research workflow's start and end: a governed skill, a study design, a job). */
  #toolEvent(ev, live) {
    try {
      const tool = live.tools.get(ev.callId);
      const call = tool?.call || {};
      let r = null;
      try { r = this.toolRouter?.resolve?.(ev.name || call.name, ev.args || call.args || {}) ?? null; } catch { r = null; }
      const raw = String(ev.envelope?.via || r?.entry?.id || r?.core?.name || ev.name || call.name || "");
      const name = this.#toolNames.has(raw) ? raw : "unknown";
      const kind = ev.envelope?.governance?.kind || r?.entry?.kind || r?.target?.kind || "unknown";
      const key = `${live.id}:${String(ev.callId || "").replace(/[^A-Za-z0-9_.:-]/g, "_")}`.slice(0, 100);
      const workflow = WORKFLOW_KINDS.includes(kind);
      if (ev.type === "tool.start") {
        if (workflow) this.telemetry.track("workflow", { key, kind, name, phase: "started" });
        return;
      }
      const env = ev.envelope || {};
      const where = env.receipt?.decided_by === "router" ? "page" : env.receipt?.where || call.where;
      const ms = Number(env.duration_ms) || (call.endedAt && call.startedAt ? call.endedAt - call.startedAt : undefined);
      this.telemetry.track("tool_end", { call: key, tool: name, kind, place: where, status: env.status, ms, error_type: env.error?.type });
      if (workflow) this.telemetry.track("workflow", { key, kind, name, phase: WORKFLOW_PHASE[env.status] || "failed" });
    } catch { /* never the turn's concern */ }
  }

  /** A runner job the thread follows reached its end: the workflow's completion or failure, once per job and state. */
  #jobEvent(job) {
    try {
      if (!job?.id || !["succeeded", "failed", "cancelled"].includes(job.state)) return;
      const k = `${job.id}|${job.state}`;
      if (this.#jobsReported.has(k)) return;
      this.#jobsReported.add(k);
      // the job's id itself is not sent: a digest of it says the same job twice is one
      sha256Hex(String(job.id)).then((hash) => this.telemetry.track("workflow", {
        key: `job:${hash.slice(0, 32)}`, kind: "job", name: token(String(job.kind || ""), 80) || "job",
        phase: job.state === "succeeded" ? "completed" : job.state,
      })).catch(() => {});
    } catch { /* never the thread's concern */ }
  }

  /**
   * Turns cut off by a reload, a crash or a closed tab (their heartbeat stopped): reported as interrupted, with what
   * the draft kept, and recorded when they may be. Each is taken off the list first, so two pages do not both report it.
   */
  async #recoverTurns() {
    // only once the site has said what it takes (a page opened offline tries again later), and in one tab at a time
    if (!this.telemetry.config) return;
    await this.telemetry.exclusive(() => this.#recoverStale());
  }

  async #recoverStale() {
    for (const m of await this.telemetry.staleTurns()) {
      if (this.state.turn?.live?.id === m.id) continue;
      this.telemetry.turnEnded(m.id);
      try {
        const draft = await this.store.messages.get(m.id).catch(() => null);
        const conv = await this.store.conversations.get(m.conversationId || draft?.conversationId).catch(() => null);
        const all = conv ? await this.store.messages.list(conv.id).catch(() => []) : [];
        const user = draft ? all.find((x) => x.id === draft.parentId) : null;
        const tools = all.filter((x) => x.role === "tool" && x.parentId === m.id);
        const steps = Math.max(0, ...(draft?.segments || []).map((sg) => Number(sg.step) || 0));
        const assistant = { id: m.id, provider: m.provider, model: m.model, content: draft?.content || "", segments: draft?.segments || [], steps, status: "stopped" };
        const live = { id: m.id, startedAt: m.startedAt };
        if (!conv || !user) {
          // the conversation deleted since, or cut off before anything of the turn was saved
          this.telemetry.track("turn_end", turnEndEvent({ id: m.id, provider: m.provider, model: m.model, status: "interrupted", first: m.first,
            startedAt: m.startedAt, endedAt: m.beat, assistant, tools: [], excluded: conv ? "unavailable" : "discarded" }));
          continue;
        }
        await this.#reportTurn({ conv, user, assistant, toolMessages: tools, live, turn: { first: m.first, consent: m.consent === true }, interrupted: true, endedAt: m.beat });
      } catch { /* the next page load tries no more: it was taken off the list */ }
    }
  }

  #announceTool(env) {
    if (!env) return;
    if (env.status === "refused") announce(t("ui.turn.tool_refused", { name: env.tool }), { assertive: true });
  }

  /** Copy the last answer of the shown branch as Markdown (with its citations listed). */
  async copyLastAnswer() {
    const path = this.path();
    const last = [...path].reverse().find((m) => m.role === "assistant" && m.content);
    if (!last) return;
    const { answerMarkdown } = await import("./conversation.js");
    const ok = await copyText(answerMarkdown(this, last));
    toast(ok ? t("ui.toast.answer_copied") : t("ui.toast.copy_failed"), { tone: ok ? "ok" : "warn" });
  }

  // ----------------------------------------------------------------------------------------------- inspector

  /** Open the inspector on a tab and (optionally) a selected object: {tab, kind, id, messageId}. */
  select({ tab, kind = null, id = null, messageId = null, open = true } = {}) {
    const insp = this.state.inspector;
    this.state.inspector = {
      ...insp, open: open || insp.open, tab: tab || insp.tab,
      messageId: messageId || insp.messageId, selection: kind ? { kind, id, at: Date.now() } : null,
    };
    this.emit("inspector", this.state.inspector);
  }

  toggleInspector(force) {
    const open = typeof force === "boolean" ? force : !this.state.inspector.open;
    this.state.inspector = { ...this.state.inspector, open };
    this.emit("inspector", this.state.inspector);
  }

  toggleSidebar() {
    const mode = this.state.layout;
    if (mode === "wide" || mode === "medium") {
      const collapsed = !this.state.settings.sidebarCollapsed;
      document.documentElement.classList.toggle("sidebar-collapsed", collapsed);
      this.setSetting({ sidebarCollapsed: collapsed });
      this.emit("layout", mode);
    } else {
      this.state.sidebarOpen = !this.state.sidebarOpen;
      this.emit("sidebar", this.state.sidebarOpen);
    }
  }

  /** Scroll the thread to a message (or a tool card in it) and highlight it for 1.2 s (inspector → thread sync). */
  revealInThread({ messageId, callId } = {}) {
    const sel = callId ? `[data-call="${CSS.escape(callId)}"]` : `[data-msg="${CSS.escape(messageId || "")}"]`;
    const el = document.querySelector(sel);
    if (!el) return;
    el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 1300);
    if (this.state.layout !== "wide") this.toggleInspector(false);
  }

  // ----------------------------------------------------------------------------------------------- keyboard

  /** The window's keydown handler (also callable directly, for tests). */
  handleKey(e) { this.#onKey(e); }

  #onKey(e) {
    // a key that confirms or cancels an IME candidate (Esc while composing pinyin) is the IME's, never a shortcut
    if (e.isComposing || e.keyCode === 229) return;
    const mod = e.metaKey || e.ctrlKey;
    const typing = isTyping(e.target);
    if (e.key === "Escape") {
      if (e.defaultPrevented) return;
      if (hasOverlay()) { closeTopOverlay(); e.preventDefault(); return; }
      if (this.state.turn) { this.stop(); e.preventDefault(); return; }
      if (this.state.sidebarOpen) { this.state.sidebarOpen = false; this.emit("sidebar", false); return; }
      if (this.state.inspector.open && this.state.layout !== "wide") { this.toggleInspector(false); return; }
      return;
    }
    if (mod && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); this.openPalette(); return; }
    if (mod && !e.shiftKey && (e.key === "/" || e.code === "Slash")) { e.preventDefault(); this.openShortcuts(); return; }
    if (mod && e.shiftKey && e.code === "KeyO") { e.preventDefault(); this.newConversation(); return; }
    if (mod && e.shiftKey && e.code === "KeyS") { e.preventDefault(); this.toggleSidebar(); return; }
    if (mod && e.shiftKey && e.code === "KeyC" && !hasSelection()) { e.preventDefault(); this.copyLastAnswer(); return; }
    if (mod && e.shiftKey && e.code === "Semicolon") { e.preventDefault(); this.focusComposer(); return; }
    if (mod && !e.shiftKey && e.code === "KeyU") { e.preventDefault(); this.emit("attach"); return; }
    if (e.altKey && !mod && e.code === "Backslash") { e.preventDefault(); this.toggleInspector(); return; }
    if (e.altKey && !mod && /^Digit[1-5]$/.test(e.code)) {
      e.preventDefault();
      const tab = ["run", "evidence", "claims", "files", "provenance"][Number(e.code.slice(5)) - 1];
      this.select({ tab });
      return;
    }
    if (!typing && !mod && !e.altKey && e.key === "/") { e.preventDefault(); this.focusComposer(); }
  }

  openPalette() {
    if (hasOverlay()) return;
    import("./palette.js").then((m) => m.openPalette(this));
  }

  openShortcuts() {
    if (hasOverlay()) return;
    import("./palette.js").then((m) => m.openShortcutSheet(this));
  }

  href(route) {
    return routeHref(route);
  }
}

// ------------------------------------------------------------------------------------------------- live turn model

function newLive(id, userId, provider) {
  return {
    id, userId, provider: provider.id, model: provider.model, providerLabel: provider.label, relay: Boolean(provider.relay),
    startedAt: Date.now(), segments: [], tools: new Map(), pending: new Map(), thinkingMs: {}, thinkingStart: {}, stepStart: {},
    approvals: {}, status: "streaming", error: null, usage: null, notice: "", retry: null, step: 0, calls: 0, firstAt: null,
  };
}

/** How a research workflow's tool run ended, as a phase (telemetry-contract.js WORKFLOW_PHASES). */
const WORKFLOW_PHASE = { succeeded: "completed", job_submitted: "submitted", failed: "failed", refused: "refused", cancelled: "cancelled" };

/** Every tool name the catalog knows: core tools and entries (anything else is reported as "unknown"). */
function toolNamesOf(catalog) {
  const out = new Set();
  for (const c of catalog?.core || []) if (c?.name) out.add(c.name);
  for (const e of catalog?.entries || []) if (e?.id) out.add(e.id);
  return out;
}

/** The browser, coarsely: its engine family, whether it is a phone or tablet, whether installed as an app. */
function clientFacts() {
  const nav = globalThis.navigator || {};
  const ua = String(nav.userAgent || "");
  const brands = (nav.userAgentData?.brands || []).map((b) => b.brand).join(" ");
  const ios = /iPhone|iPad|iPod/.test(ua);
  const browser = ios ? "safari" : /Firefox\//.test(ua) ? "firefox" : /Chrom(?:e|ium)|Edg\//.test(`${brands} ${ua}`) ? "chromium" : /Safari\//.test(ua) ? "safari" : "other";
  const mobile = typeof nav.userAgentData?.mobile === "boolean" ? nav.userAgentData.mobile : /Mobi|Android|iPhone|iPad|iPod/.test(ua);
  let standalone = false;
  try { standalone = Boolean(globalThis.matchMedia?.("(display-mode: standalone)")?.matches); } catch { standalone = false; }
  return { browser, mobile, standalone };
}

/** Fold one agent event into the live model (the thread re-renders from it). */
export function applyEvent(live, ev) {
  const lastSeg = () => live.segments[live.segments.length - 1];
  const endThinking = (step) => {
    if (live.thinkingStart[step] && live.thinkingMs[step] === undefined) live.thinkingMs[step] = Date.now() - live.thinkingStart[step];
  };
  switch (ev.type) {
    case "model.start":
      live.step = ev.step;
      live.retry = null;
      live.calls = (live.calls || 0) + 1; // one request to the model (a retry, below, is another)
      (live.stepStart ||= {})[ev.step] = Date.now();
      break;
    case "reasoning": {
      live.firstAt ||= Date.now();
      const seg = lastSeg();
      if (seg && seg.type === "reasoning" && seg.step === ev.step) seg.text += ev.delta;
      else {
        live.segments.push({ type: "reasoning", step: ev.step, text: ev.delta });
        // from the start of the step: a provider that sends its reasoning in one burst still made the person wait
        if (live.thinkingStart[ev.step] === undefined) live.thinkingStart[ev.step] = live.stepStart?.[ev.step] ?? Date.now();
      }
      break;
    }
    case "text": {
      live.firstAt ||= Date.now();
      endThinking(ev.step);
      const seg = lastSeg();
      if (seg && seg.type === "text" && seg.step === ev.step) seg.text += ev.delta;
      else live.segments.push({ type: "text", step: ev.step, text: ev.delta });
      break;
    }
    case "tool.pending":
      endThinking(ev.step);
      live.pending.set(`${ev.step}:${ev.index}`, { name: ev.name, step: ev.step });
      break;
    case "tool.start": {
      endThinking(ev.step);
      for (const [k, p] of live.pending) if (p.step === ev.step) live.pending.delete(k);
      const seg = lastSeg();
      if (seg && seg.type === "tools" && seg.step === ev.step) seg.callIds.push(ev.callId);
      else live.segments.push({ type: "tools", step: ev.step, callIds: [ev.callId] });
      live.tools.set(ev.callId, { call: { id: ev.callId, name: ev.name, args: ev.args, where: ev.where, status: "running", startedAt: Date.now() }, envelope: null, approval: null });
      break;
    }
    case "tool.end": {
      const tool = live.tools.get(ev.callId);
      if (tool) {
        tool.envelope = ev.envelope;
        tool.call.status = ev.envelope?.status || "succeeded";
        tool.call.endedAt = Date.now();
      }
      break;
    }
    case "tool.update": {
      // the agent renumbered this result's citations after an earlier result of the turn
      const tool = live.tools.get(ev.callId);
      if (tool) tool.envelope = ev.envelope;
      break;
    }
    case "usage":
      live.usage = { input: ev.input, output: ev.output };
      break;
    case "model.retry":
      live.calls = (live.calls || 0) + 1;
      live.segments = live.segments.filter((s) => !(s.step === ev.step && (s.type === "text" || s.type === "reasoning")));
      live.retry = ev.notice || ev.message;
      break;
    case "model.end":
      endThinking(ev.step);
      break;
    case "error":
      if (ev.kind !== "aborted") live.error = { message: ev.message, kind: ev.kind };
      break;
    case "done":
      live.status = ev.status;
      break;
    default:
      break;
  }
}

/** A stored-message shape for the live turn (drafts during streaming; the record when a turn fails early). */
function liveToRecord(live, conversationId, { draft = false } = {}) {
  const content = live.segments.filter((s) => s.type === "text").map((s) => s.text).join("\n\n");
  const reasoning = live.segments.filter((s) => s.type === "reasoning").map((s) => s.text).join("\n\n");
  return {
    id: live.id, conversationId, parentId: live.userId, role: "assistant", createdAt: live.startedAt, content, reasoning,
    segments: live.segments.map((s) => ({ ...s, callIds: s.callIds ? [...s.callIds] : undefined })),
    toolCalls: [...live.tools.values()].map((x) => ({ id: x.call.id, name: x.call.name, args: x.call.args })),
    provider: live.provider, model: live.model, status: "stopped", ...(draft ? { draft: true } : {}),
  };
}

/** The stored id of a turn's tool record: fixed by the call, so the draft's copy and the final one are the same row. */
export function toolMessageId(assistantId, callId) {
  return `${assistantId}:${callId}`;
}

/** The tool records of the live turn so far: every call that has a result (its envelope, receipt, job id). */
export function liveToolMessages(live, conversationId) {
  const out = [];
  for (const seg of live.segments) {
    if (seg.type !== "tools") continue;
    for (const callId of seg.callIds || []) {
      const tool = live.tools.get(callId);
      if (!tool?.envelope) continue;
      const env = tool.envelope;
      out.push({
        id: toolMessageId(live.id, callId), conversationId, parentId: live.id, role: "tool", createdAt: tool.call.endedAt || tool.call.startedAt || Date.now(),
        toolCallId: callId, name: tool.call.name, args: tool.call.args, content: env.text || "", envelope: env, step: seg.step,
        where: env.receipt?.where || null, status: env.status, draft: true,
      });
    }
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- helpers

function hostOf(url) {
  try { return new URL(url).host; } catch { return String(url || ""); }
}

/** The runner's settings with a patch merged as the runner merges it (network one level deep). */
export function mergeRunnerSettings(current, patch = {}) {
  const cur = current && typeof current === "object" ? current : {};
  const out = { ...cur, ...patch };
  if (patch.network !== undefined) {
    const net = typeof patch.network === "boolean" ? { enabled: patch.network } : patch.network || {};
    out.network = { ...(cur.network || {}), ...net };
  }
  return out;
}

export function autoTitle(text) {
  const first = String(text || "").split("\n").map((s) => s.trim()).find(Boolean) || "";
  const chars = [...first];
  return chars.length > 32 ? `${chars.slice(0, 31).join("")}…` : first;
}

/** wide ≥1280 · medium 1024–1279 · narrow 768–1023 · mobile <768 (DESIGN §4.3). */
export function layoutMode(w = window.innerWidth) {
  if (w >= 1280) return "wide";
  if (w >= 1024) return "medium";
  if (w >= 768) return "narrow";
  return "mobile";
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
  const dark = theme === "dark" || (theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
  for (const m of document.querySelectorAll('meta[name="theme-color"]')) {
    if (theme === "system") m.setAttribute("content", m.media.includes("dark") ? "#0f1318" : "#fbfaf7");
    else m.setAttribute("content", dark ? "#0f1318" : "#fbfaf7");
  }
}

/**
 * Focus a skip link's target: the element itself when this page has it (#composer-input, #thread), else the main
 * region. Focus moves; the address does not.
 */
export function skipTo(href) {
  const id = String(href || "").replace(/^#/, "");
  const target = (id && document.getElementById(id)) || document.getElementById("main");
  if (!target) return null;
  if (!target.matches("a[href], button, input, textarea, select, [tabindex]")) target.setAttribute("tabindex", "-1");
  target.focus({ preventScroll: true });
  target.scrollIntoView?.({ block: "nearest" });
  return target;
}

/** The few strings in index.html itself (skip links, live regions). */
function translateStatic() {
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.getAttribute("data-i18n"));
  document.getElementById("toasts")?.setAttribute("aria-label", t("ui.toasts.label"));
}

function isTyping(el) {
  if (!el || !(el instanceof Element)) return false;
  return el.matches("input, textarea, select, [contenteditable='true'], [contenteditable='']");
}

function hasSelection() {
  const s = window.getSelection?.();
  return Boolean(s && String(s).length);
}

/** Runtimes that cannot run anything, used when the runtime modules are missing (the page still works for chat). */
function stubRuntimes() {
  const mk = (kind) => ({
    kind, status: "offline", label: kind, onStatus: () => () => {}, start: async () => { throw new Error(t("ui.runner.unavailable")); },
    info: async () => null, catalog: async () => null, device: async () => null,
    call: async (tool) => ({ ok: false, tool, via: tool, status: "failed", duration_ms: 0, summary: t("ui.runner.unavailable"), text: "Not run: no runtime is available.", result: null, citations: [], governance: { kind: "system", claims: [], evidence: [], refusals: [], outputs: [], limitations: [] }, receipt: { where: kind }, job: null, approval: null, error: { type: "unavailable", message: t("ui.runner.unavailable"), hint: "" } }),
    settings: { get: async () => null, put: async () => null },
    jobs: { list: async () => ({ jobs: [] }), get: async () => null, cancel: async () => null, files: async () => ({ files: [] }), events: () => () => {} },
  });
  return { browser: mk("browser"), runner: mk("runner") };
}

/** WebGPU and core count, honestly: a software adapter (SwiftShader) is reported as such (CONTRACTS §5). */
export async function detectBrowserDevice() {
  const out = { cores: navigator.hardwareConcurrency || null, memory_gb: navigator.deviceMemory ?? null, cross_origin_isolated: Boolean(self.crossOriginIsolated), webgpu: { api: "gpu" in navigator, adapter: null } };
  if (navigator.gpu) {
    try {
      const a = await navigator.gpu.requestAdapter({ powerPreference: "high-performance" });
      if (a) {
        const info = a.info ?? (a.requestAdapterInfo ? await a.requestAdapterInfo() : {});
        const fallback = a.isFallbackAdapter ?? info.isFallbackAdapter ?? false;
        out.webgpu.adapter = {
          vendor: info.vendor || "", architecture: info.architecture || "", description: info.description || "",
          software: Boolean(fallback || /swiftshader|llvmpipe|software/i.test(`${info.architecture} ${info.description} ${info.vendor}`)),
        };
      }
    } catch (err) {
      out.webgpu.error = String(err?.message || err);
    }
  }
  return out;
}

export function makeApp() {
  return new App();
}

export { h };
