// The page's side of the telemetry (studio/docs/TELEMETRY.md): usage events, batched and sent to this site's Worker;
// conversation records, sent only while the visitor agrees (consent.js) and only for turns that may be recorded at all
// (turnrecord.js); and the deletion of what was recorded. It never stands in the way of the app: every method returns
// without throwing, nothing the app does waits on the network for it, and a failure is retried later or dropped.
//
// Where it sends: to /api/telemetry/* on the page's own origin, and only when that origin's /v1/health says the site
// takes them (configure()); a page served by the local runner or a plain file server sends nothing.
//
// What survives a reload: unsent events (per tab: sessionStorage names the tab, so a reload finds its own), unsent
// records (shared; the Worker keeps one copy of a turn whatever arrives twice), and the turns under way (so the next page
// can tell a turn that was cut off by a reload or a crash, and report it as interrupted).

import { analyticsAllowed, contentConsent, deletionKey } from "./consent.js";
import { CONSENT_VERSION, EVENTS_VERSION, LIMITS, ROUTES, normalizeEvent, token } from "./telemetry-contract.js";
import { storage, uuid } from "./util.js";

export const OUTBOX_PREFIX = "tcmstudio.telemetry.outbox:";
export const RECORDS_KEY = "tcmstudio.telemetry.records";
export const TURNS_KEY = "tcmstudio.telemetry.turns";
export const TAB_KEY = "tcmstudio.tab";
export const VISIT_KEY = "tcmstudio.visit";
export const VISIT_IDLE_MS = 30 * 60000; // a visit ends after half an hour without activity
// a turn under way beats every five seconds and holds a Web Lock named after it (released by the browser when its page
// goes away, however it goes); without the lock API, a turn silent this long was cut off (a hidden tab's timers may be
// throttled to once a minute, so the margin is wide)
export const STALE_TURN_MS = 90000;
const LOCK_PREFIX = "tcmstudio-turn-";
const MAX_BATCHES = 20;
const MAX_QUEUE = 200;
const MAX_PENDING = 100;
const MAX_RECORDS = 10;
const MAX_ERRORS = 10; // error events per page load
// events wait this long for company before they are sent (and go at once when the page is hidden or closed): each batch
// is one Durable Object request on the Worker, from a budget its limiter shares (edge/src/telemetry/buffer.js)
const FLUSH_MS = 15000;
const ABANDONED_MS = 10 * 60000; // another tab's outbox not touched this long is taken over

const emptyBox = () => ({ queue: [], batches: [], saved: 0 });

export class Telemetry {
  /** deps (for tests): fetch, beacon(url, body) → boolean, origin, now, setTimer, clearTimer, nav (for DNT/GPC). */
  constructor({ fetch: f, beacon, origin, now, setTimer, clearTimer, nav } = {}) {
    this.fetch = f || ((...a) => globalThis.fetch(...a));
    this.beacon = beacon ?? ((url, body) => {
      const nav2 = globalThis.navigator;
      return typeof nav2?.sendBeacon === "function" ? nav2.sendBeacon(url, new Blob([body], { type: "text/plain;charset=UTF-8" })) : false;
    });
    this.origin = origin ?? (globalThis.location?.origin || "");
    this.now = now || (() => Date.now());
    this.setTimer = setTimer || ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = clearTimer || ((t) => clearTimeout(t));
    this.nav = nav;
    this.config = null;
    this.pending = [];
    this.timer = null;
    this.errors = 0;
    this.flushing = null;
    this.tab = storage.get(TAB_KEY, "session") || uuid();
    storage.set(TAB_KEY, this.tab, "session");
    this.box = this.#adopt();
  }

  get base() {
    return `${this.origin}/api/telemetry`;
  }

  /** Whether this page's own site takes telemetry at all (its health said so). */
  get here() {
    return Boolean(this.config?.here);
  }

  get analyticsOn() {
    return Boolean(this.config?.analytics) && analyticsAllowed(this.nav);
  }

  /** The site takes conversation records under the notice this page shows. */
  get conversationsAvailable() {
    return Boolean(this.config?.conversations);
  }

  get contentOn() {
    return this.conversationsAvailable && contentConsent() === true;
  }

  /** The visitor has not answered the current notice, and the site takes records: ask before the next question. */
  needsConsent() {
    return this.conversationsAvailable && contentConsent() === null;
  }

  /**
   * Read what /v1/health says of the telemetry (its `telemetry` object). Only a health answered by this page's own
   * origin counts (`relayBase`: where it came from); anything else turns the telemetry off for this page.
   */
  configure(health, { relayBase } = {}) {
    try {
      let same = false;
      try { same = Boolean(relayBase) && new URL(relayBase, this.origin || undefined).origin === this.origin; } catch { same = false; }
      const t = same && health && typeof health.telemetry === "object" && health.telemetry ? health.telemetry : null;
      this.config = {
        here: Boolean(t), analytics: t?.analytics === true,
        // records only under the notice this page shows: a page older or newer than the site's notice sends none
        conversations: t?.conversations === true && t?.consent_version === CONSENT_VERSION,
        consentVersion: Number.isInteger(t?.consent_version) ? t.consent_version : null,
        retention: t?.retention_days && typeof t.retention_days === "object" ? t.retention_days : null,
        geo: typeof t?.geo === "string" ? t.geo : null,
      };
      const pending = this.pending;
      this.pending = [];
      if (this.analyticsOn) {
        for (const e of pending) this.#enqueue(e);
        if (this.box.queue.length || this.box.batches.length) this.flush();
      } else {
        this.#clearEvents();
      }
      if (this.contentOn) this.flushRecords();
    } catch { /* never the app's concern */ }
    return this.config;
  }

  // ----------------------------------------------------------------------------------------------- events

  /** One usage event (telemetry-contract.js EVENT_TYPES), read through the contract. Before configure() it waits. */
  track(type, fields = {}) {
    try {
      const e = normalizeEvent({ type, ts: this.now(), ...fields });
      if (!e) return false;
      if (!this.config) {
        if (this.pending.length < MAX_PENDING) this.pending.push(e);
        return true;
      }
      if (!this.analyticsOn) return false;
      this.#enqueue(e);
      this.schedule();
      return true;
    } catch {
      return false;
    }
  }

  /** A route shown: a page view (by the route's name only), and a visit when this is the first in half an hour. */
  route(route) {
    try {
      const name = ROUTES.includes(route?.name) ? route.name : "notfound";
      if (this.touch()) this.track("visit", { route: name });
      this.track("pageview", { route: name, tab: name === "settings" ? token(route?.tab, 24) : undefined });
    } catch { /* never the app's concern */ }
  }

  /** Activity in this tab; true when it begins a new visit. */
  touch() {
    const now = this.now();
    const v = storage.getJSON(VISIT_KEY, "session");
    const fresh = !v || !(Number(v.last) > 0) || now - Number(v.last) > VISIT_IDLE_MS;
    storage.setJSON(VISIT_KEY, { last: now }, "session");
    return fresh;
  }

  schedule(ms = FLUSH_MS) {
    if (this.timer) return;
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.flush();
    }, ms);
  }

  /**
   * Send what is waiting, in batches. `beacon`: the page is going away (sendBeacon; nothing waits for an answer). A flush
   * asked for while one runs makes that one go round again, so whatever was queued before the call has been tried when
   * its promise settles.
   */
  flush({ beacon = false } = {}) {
    if (beacon) return this.#send(true);
    if (this.flushing) {
      this.again = true;
      return this.flushing;
    }
    const run = (async () => {
      let sent = 0;
      do {
        this.again = false;
        const r = await this.#send(false);
        sent += r.sent;
        if (r.stuck) break; // the network or the Worker must recover first: the next flush tries again
      } while (this.again);
      return { sent };
    })();
    this.flushing = run;
    run.finally(() => { if (this.flushing === run) this.flushing = null; });
    return run;
  }

  async #send(beacon) {
    try {
      if (this.timer) { this.clearTimer(this.timer); this.timer = null; }
      if (!this.config || !this.analyticsOn) return { sent: 0 };
      this.#batch();
      let sent = 0;
      for (const b of [...this.box.batches]) {
        const body = JSON.stringify({ v: EVENTS_VERSION, batch: b.id, events: b.events });
        const res = beacon ? this.#beacon(`${this.base}/events`, body) : await this.#post(`${this.base}/events`, body);
        if (res === "retry") return { sent, stuck: true };
        // sent, or refused for good (a malformed batch never becomes a good one): not sent again
        this.box.batches = this.box.batches.filter((x) => x.id !== b.id);
        this.#save();
        if (res === "sent") sent++;
      }
      return { sent };
    } catch {
      return { sent: 0, stuck: true };
    }
  }

  /** The page's window and document: flush when the page is hidden or closed; errors as events. */
  start(win = globalThis.window, doc = globalThis.document) {
    if (this.started || !win) return;
    this.started = true;
    try {
      doc?.addEventListener?.("visibilitychange", () => { if (doc.visibilityState === "hidden") this.flush({ beacon: true }); });
      win.addEventListener?.("pagehide", () => this.flush({ beacon: true }));
      win.addEventListener?.("error", (e) => {
        // a script, image or style that failed to load is not the app's error (the boot handler says what to do)
        if (e?.target && e.target !== win && e.target.tagName) return;
        this.error("js_error", e?.error, e?.filename, e?.lineno);
      }, true);
      win.addEventListener?.("unhandledrejection", (e) => this.error("unhandled_rejection", e?.reason));
    } catch { /* never the app's concern */ }
  }

  /** An error of the app, as a category, its name and where in the app's own code (never its message). */
  error(category, err, file, line) {
    try {
      if (this.errors >= MAX_ERRORS) return;
      const name = token(typeof err?.name === "string" ? err.name : "Error", 40) || "Error";
      const where = this.#where(file, line, err);
      const key = `${category}|${name}|${where}`;
      this.seenErrors ||= new Set();
      if (this.seenErrors.has(key)) return;
      this.seenErrors.add(key);
      this.errors++;
      this.track("error", { category, name, where });
    } catch { /* never the app's concern */ }
  }

  #where(file, line, err) {
    const own = `${this.origin}/`;
    let f = typeof file === "string" ? file : "";
    let l = Number(line) || 0;
    if (!f && typeof err?.stack === "string") {
      const m = new RegExp(`${own.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([\\w./-]+\\.m?js):(\\d+)`).exec(err.stack);
      if (m) { f = own + m[1]; l = Number(m[2]); }
    }
    if (!f.startsWith(own)) return "external";
    return token(`${f.slice(own.length).split(/[?#]/)[0]}${l ? `:${l}` : ""}`, 80) || "external";
  }

  /** Drop every event not yet sent (usage statistics switched off). */
  discardEvents() {
    try {
      this.pending = [];
      this.#clearEvents();
    } catch { /* nothing to drop */ }
  }

  #enqueue(e) {
    this.box.queue.push(e);
    if (this.box.queue.length > MAX_QUEUE) this.box.queue.splice(0, this.box.queue.length - MAX_QUEUE);
    this.#save();
  }

  /** The queue, cut into batches within the contract's limits. */
  #batch() {
    while (this.box.queue.length) {
      const events = [];
      let bytes = 60;
      while (this.box.queue.length && events.length < LIMITS.events) {
        const n = JSON.stringify(this.box.queue[0]).length + 1;
        if (events.length && bytes + n > LIMITS.batchBytes) break;
        events.push(this.box.queue.shift());
        bytes += n;
      }
      this.box.batches.push({ id: uuid(), events });
    }
    if (this.box.batches.length > MAX_BATCHES) this.box.batches.splice(0, this.box.batches.length - MAX_BATCHES);
    this.#save();
  }

  #clearEvents() {
    this.box = emptyBox();
    this.#save();
  }

  #save() {
    this.box.saved = this.now();
    storage.setJSON(`${OUTBOX_PREFIX}${this.tab}`, this.box);
  }

  /** This tab's outbox from before a reload, and any other tab's that was abandoned (closed with events unsent). */
  #adopt() {
    const box = emptyBox();
    try {
      const own = storage.getJSON(`${OUTBOX_PREFIX}${this.tab}`);
      const take = (b) => {
        if (!b || typeof b !== "object") return;
        if (Array.isArray(b.queue)) box.queue.push(...b.queue.filter((e) => normalizeEvent(e)));
        if (Array.isArray(b.batches)) box.batches.push(...b.batches.filter((x) => x && typeof x.id === "string" && Array.isArray(x.events)));
      };
      take(own);
      const ls = globalThis.localStorage;
      for (let i = 0; ls && i < ls.length; i++) {
        const k = ls.key(i);
        if (!k || !k.startsWith(OUTBOX_PREFIX) || k === `${OUTBOX_PREFIX}${this.tab}`) continue;
        const b = storage.getJSON(k);
        if (!b || this.now() - Number(b.saved || 0) > ABANDONED_MS) {
          take(b);
          storage.remove(k);
          i--;
        }
      }
    } catch { /* storage unavailable: start empty */ }
    box.queue = box.queue.slice(-MAX_QUEUE);
    box.batches = box.batches.slice(-MAX_BATCHES);
    return box;
  }

  async #post(url, body) {
    try {
      const r = await this.fetch(url, {
        method: "POST", body, keepalive: body.length < 60000, credentials: "same-origin",
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
      });
      if (r.ok) return "sent";
      if (r.status === 429 || r.status >= 500) return "retry";
      return r.status === 409 ? "outdated" : "refused";
    } catch {
      return "retry";
    }
  }

  #beacon(url, body) {
    try {
      return this.beacon(url, body) ? "sent" : "retry";
    } catch {
      return "retry";
    }
  }

  // ----------------------------------------------------------------------------------------------- records

  /** Send one record (buildRecord's), kept until the Worker has it. Only while the visitor agrees. */
  sendRecord(record, { conversationId } = {}) {
    try {
      if (!record || !this.contentOn) return Promise.resolve({ sent: 0 });
      this.#records((list) => [...list.filter((r) => r.id !== record.id), { id: record.id, conversation: conversationId || record.conversation || "", body: record }]);
      return this.flushRecords();
    } catch {
      return Promise.resolve({ sent: 0 });
    }
  }

  /** Send the records waiting (from this page or one before it). Stops at the first that must wait. */
  async flushRecords() {
    let sent = 0;
    try {
      if (this.flushingRecords) return { sent: 0 };
      this.flushingRecords = true;
      for (const item of this.#records()) {
        // agreement withdrawn, or a record made under another notice: never sent
        if (!this.contentOn || item?.body?.consent !== CONSENT_VERSION) {
          if (!this.contentOn) break;
          this.#records((list) => list.filter((r) => r.id !== item.id));
          continue;
        }
        const res = await this.#post(`${this.base}/conversations`, JSON.stringify(item.body));
        if (res === "retry") break;
        this.#records((list) => list.filter((r) => r.id !== item.id));
        if (res === "sent") sent++;
      }
    } catch { /* kept for the next time */ } finally {
      this.flushingRecords = false;
    }
    return { sent };
  }

  /** Records not yet sent, dropped: all of them (consent withdrawn), or one conversation's (it was deleted). */
  purgeRecords(conversationId = null) {
    try {
      this.#records((list) => (conversationId ? list.filter((r) => r.conversation !== conversationId) : []));
    } catch { /* nothing to drop */ }
  }

  #records(change) {
    const list = Array.isArray(storage.getJSON(RECORDS_KEY)) ? storage.getJSON(RECORDS_KEY) : [];
    if (!change) return list;
    const next = change(list).slice(-MAX_RECORDS);
    if (next.length) storage.setJSON(RECORDS_KEY, next);
    else storage.remove(RECORDS_KEY);
    return next;
  }

  /** Ask the site to delete every record made with this browser's deletion key. → {ok, deleted} | {ok:false, …}. */
  async deleteMine() {
    const key = deletionKey();
    if (!key) return { ok: true, deleted: 0 };
    if (!this.here) return { ok: false, reason: "unavailable" };
    try {
      const r = await this.fetch(`${this.base}/conversations/delete`, {
        method: "POST", body: JSON.stringify({ key }), credentials: "same-origin", headers: { "Content-Type": "text/plain;charset=UTF-8" },
      });
      const j = await r.json().catch(() => ({}));
      return r.ok ? { ok: true, deleted: Number(j.deleted) || 0 } : { ok: false, status: r.status, reason: j?.error?.type || "refused" };
    } catch {
      return { ok: false, reason: "network" };
    }
  }

  // ----------------------------------------------------------------------------------------------- turns under way

  #turns(change) {
    const all = storage.getJSON(TURNS_KEY);
    const map = all && typeof all === "object" && !Array.isArray(all) ? all : {};
    if (!change) return map;
    const next = change({ ...map });
    if (Object.keys(next).length) storage.setJSON(TURNS_KEY, next);
    else storage.remove(TURNS_KEY);
    return next;
  }

  /** A turn begins: {id, conversationId, projectId, provider, model, first, startedAt}. */
  turnBegan(marker) {
    try {
      this.#turns((m) => ({ ...m, [marker.id]: { ...marker, beat: this.now(), tab: this.tab } }));
      const locks = this.#lockApi();
      if (locks) {
        this.locks ||= new Map();
        locks.request(`${LOCK_PREFIX}${marker.id}`, () => new Promise((release) => this.locks.set(marker.id, release))).catch(() => {});
      }
    } catch { /* not tracked */ }
  }

  turnBeat(id) {
    try { this.#turns((m) => (m[id] ? { ...m, [id]: { ...m[id], beat: this.now() } } : m)); } catch { /* not tracked */ }
  }

  turnEnded(id) {
    try {
      this.#turns((m) => { delete m[id]; return m; });
      this.locks?.get(id)?.();
      this.locks?.delete(id);
    } catch { /* not tracked */ }
  }

  #lockApi() {
    const locks = this.nav?.locks ?? globalThis.navigator?.locks;
    return locks && typeof locks.request === "function" && typeof locks.query === "function" ? locks : null;
  }

  /**
   * Turns that were cut off by a reload, a crash or a closed tab: no page holds their lock any more (or, without the
   * lock API, they stopped beating STALE_TURN_MS ago). A turn another tab is still running is never one of them.
   */
  async staleTurns() {
    try {
      const all = Object.values(this.#turns()).filter((t) => t && typeof t.id === "string");
      if (!all.length) return [];
      const locks = this.#lockApi();
      if (locks) {
        const held = new Set(((await locks.query())?.held || []).map((l) => l.name));
        // a lock just being requested by a turn that began a moment ago: its beat is fresh
        return all.filter((t) => !held.has(`${LOCK_PREFIX}${t.id}`) && this.now() - Number(t.beat || 0) >= 10000);
      }
      return all.filter((t) => this.now() - Number(t.beat || 0) >= STALE_TURN_MS);
    } catch {
      return [];
    }
  }
}
