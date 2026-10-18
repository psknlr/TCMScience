// What the Worker counts itself — Tao-S1 relay calls and tokens, database-gateway requests, refused or unusual requests
// (security events) — is summed in this isolate's memory and sent to its Durable Object in one call at most every
// FLUSH_MS: the first count after a quiet spell goes at once, the rest when the window ends (the next request's flush,
// or the trailing flush the Worker arms for it: index.js). A busy site, or a scanner knocking thousands of times, then
// costs the telemetry objects a few requests a minute instead of one per request.
//
// That matters beyond cost. On the Workers Free plan an account has 100,000 Durable Object requests and 100,000 rows
// written a day; the relay's and the gateway's limiter (Limiter) draws on the same budget and, once it is spent, refuses
// service until 00:00 UTC (it fails closed). Telemetry must never be what spends it.
//
// Counts still held by an isolate when it is retired before its trailing flush are lost: the Worker's own counts are
// approximate by that much.
// The admin audit trail does not come through here: each admin action is written at once (admin.js).

export const FLUSH_MS = 20000; // shorter than the 30 seconds waitUntil allows after an answer (the trailing flush)
const MIN_GAP_MS = 5000; // a full buffer is sent early, but never sooner than this after the last flush
const MAX_KEYS = 1000; // distinct counts that make a buffer full (hosts × outcomes stay well under it)
const MAX_EVENTS = 20; // security events kept in full per flush; beyond that they are counted only (a flood is not a
// reason to flush sooner: that would turn it back into one Durable Object call per few requests)

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** Counts [{metric, a, b, n}] for UsageLog.observe, summed by day, metric, a and b. */
export class MetricBuffer {
  constructor() {
    this.counts = new Map();
    this.last = -Infinity; // never flushed: the first count is due at once, whatever the clock reads
  }

  add(list, day) {
    if (!DAY.test(String(day)) || !Array.isArray(list)) return;
    for (const m of list) {
      const n = m?.n === undefined ? 1 : Math.floor(Number(m.n));
      if (!m || typeof m.metric !== "string" || !(n > 0)) continue;
      const key = [day, m.metric, String(m.a ?? "").slice(0, 120), String(m.b ?? "").slice(0, 120)].join("\u0000");
      this.counts.set(key, (this.counts.get(key) || 0) + n);
    }
  }

  due(now) {
    const gap = now - this.last;
    return this.counts.size > 0 && (gap >= FLUSH_MS || (this.counts.size >= MAX_KEYS && gap >= MIN_GAP_MS));
  }

  /** Everything held, by day: [{day, list}]; the buffer is emptied. */
  take(now) {
    const byDay = new Map();
    for (const [key, n] of this.counts) {
      const [day, metric, a, b] = key.split("\u0000");
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push({ metric, a, b, n });
    }
    this.counts.clear();
    this.last = now;
    return [...byDay].map(([day, list]) => ({ day, list }));
  }
}

/** Security events for SecurityLog.recordBatch: every one counted by day and kind, the first MAX_EVENTS kept in full. */
export class SecurityBuffer {
  constructor() {
    this.counts = new Map();
    this.events = [];
    this.last = -Infinity;
  }

  add(ev) {
    if (!ev || typeof ev.kind !== "string" || !DAY.test(String(ev.day))) return;
    const key = `${ev.day}\u0000${ev.kind}`;
    this.counts.set(key, (this.counts.get(key) || 0) + 1);
    if (this.events.length < MAX_EVENTS) this.events.push(ev);
  }

  due(now) {
    return this.counts.size > 0 && now - this.last >= FLUSH_MS;
  }

  /** {counts: [{day, kind, n}], events}; the buffer is emptied. */
  take(now) {
    const counts = [...this.counts].map(([key, n]) => {
      const [day, kind] = key.split("\u0000");
      return { day, kind, n };
    });
    const events = this.events;
    this.counts = new Map();
    this.events = [];
    this.last = now;
    return { counts, events };
  }
}
