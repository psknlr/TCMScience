// Conversation records in their own SQLite-backed Durable Object (ConversationLog in src/index.js), apart from usage
// statistics and security events: one row per finished turn of a visitor who agreed to the notice of the current
// version (web/js/core/telemetry-contract.js CONSENT_VERSION), as the contract reduced it and the sensitive-content
// check let it through (src/telemetry/routes.js). No address, no key, no setting, no file is in a record.
//
//   owner      sha256 of the visitor's deletion key: the visitor can delete their records by presenting the key; the
//              Worker never sees the key otherwise, and nobody can tell from a record who its visitor is
//   deletions  a deletion request leaves a tombstone (when, how many, the owner digest) so an export taken before it can
//              be reconciled; the records themselves are gone at once
// Retention: records older than RETENTION_CONVERSATIONS_DAYS are deleted (daily, by the object's alarm); beyond
// CONVERSATION_MAX_RECORDS the oldest go first.

import { shiftDay } from "./config.js";

export class ConversationCore {
  constructor(sql) {
    this.sql = sql;
    sql.exec(`CREATE TABLE IF NOT EXISTS conv_records (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, received TEXT NOT NULL,
      day TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, consent INTEGER NOT NULL,
      record TEXT NOT NULL)`);
    sql.exec("CREATE INDEX IF NOT EXISTS conv_records_owner ON conv_records (owner)");
    sql.exec("CREATE INDEX IF NOT EXISTS conv_records_day ON conv_records (day)");
    sql.exec(`CREATE TABLE IF NOT EXISTS conv_deletions (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, day TEXT NOT NULL,
      owner TEXT NOT NULL, n INTEGER NOT NULL, by TEXT NOT NULL)`);
    sql.exec("CREATE TABLE IF NOT EXISTS conv_counts (day TEXT NOT NULL, metric TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, metric))");
    sql.exec("CREATE TABLE IF NOT EXISTS conv_limits (k TEXT PRIMARY KEY, n INTEGER NOT NULL, day TEXT NOT NULL)");
    this.minute = new Map();
    this.currentMinute = -1;
    this.limitDay = null;
  }

  count(day, metric, n = 1) {
    this.sql.exec("INSERT INTO conv_counts (day, metric, n) VALUES (?, ?, ?) ON CONFLICT(day, metric) DO UPDATE SET n = n + excluded.n", day, metric, n);
  }

  /** Per-visitor limits (the same shape as UsageCore.limit). */
  limit(caller, kind, limits, now = Date.now()) {
    const today = new Date(now).toISOString().slice(0, 10);
    if (today !== this.limitDay) {
      this.sql.exec("DELETE FROM conv_limits WHERE day <> ?", today);
      this.limitDay = today;
    }
    const minute = Math.floor(now / 60000);
    if (minute !== this.currentMinute) {
      this.minute.clear();
      this.currentMinute = minute;
    }
    const mk = `${kind}:${caller}`;
    const m = this.minute.get(mk) || 0;
    if (limits.perMinute && m >= limits.perMinute) return { ok: false, reason: "minute", retry: 60 - Math.floor((now / 1000) % 60) };
    const dk = `${kind}:${caller}:${today}`;
    const d = Number(this.sql.exec("SELECT n FROM conv_limits WHERE k = ?", dk).toArray()[0]?.n || 0);
    if (limits.perDay && d >= limits.perDay) return { ok: false, reason: "day", retry: Math.ceil((86400000 - (now % 86400000)) / 1000) };
    this.minute.set(mk, m + 1);
    this.sql.exec("INSERT INTO conv_limits (k, n, day) VALUES (?, 1, ?) ON CONFLICT(k) DO UPDATE SET n = n + 1", dk, today);
    return { ok: true };
  }

  /** A turn the routes accepted (already normalized, checked and redacted). A second copy of the same turn (its id) is
   * not stored again. `keep`: the most records held. → {stored, reason?}. */
  add(record, { received = new Date().toISOString(), day = received.slice(0, 10), keep = 50000 } = {}) {
    const rows = this.sql.exec(`INSERT INTO conv_records (id, received, day, owner, status, provider, model, consent, record)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING RETURNING seq`,
    record.id, received, day, record.owner, record.status, record.provider || "", record.model || "", record.consent, JSON.stringify(record)).toArray();
    if (!rows.length) {
      this.count(day, "duplicate");
      return { stored: false, reason: "duplicate" };
    }
    this.count(day, "stored");
    const seq = Number(rows[0].seq);
    if (keep && seq % 100 === 0) this.sql.exec("DELETE FROM conv_records WHERE seq <= (SELECT MAX(seq) FROM conv_records) - ?", keep);
    return { stored: true };
  }

  /**
   * One record from a page, in one call: the visitor's limits {caller, limits}, then either the count of a record
   * judged sensitive (`excluded`: its reason; nothing of it is kept) or the record stored. → {ok, stored, reason?} |
   * {ok:false, reason:"rate_limited", retry}.
   */
  accept(record, { now = Date.now(), caller = "", limits = null, received = new Date(now).toISOString(), day = received.slice(0, 10), keep = 50000, excluded = null } = {}) {
    if (caller && limits) {
      const gate = this.limit(caller, "records", limits, now);
      if (!gate.ok) return { ok: false, reason: "rate_limited", retry: gate.retry };
    }
    if (excluded) {
      this.excluded(day, excluded);
      return { ok: true, stored: false, reason: "excluded" };
    }
    return { ok: true, ...this.add(record, { received, day, keep }) };
  }

  /** A visitor's own deletion request (their key's digest), limited to ten a minute per address. → {ok, deleted}. */
  forget(owner, { caller = "", day, now = Date.now() } = {}) {
    if (caller) {
      const gate = this.limit(caller, "forget", { perMinute: 10, perDay: 200 }, now);
      if (!gate.ok) return { ok: false, reason: "rate_limited", retry: gate.retry };
    }
    return { ok: true, ...this.deleteOwner(owner, { by: "visitor", at: new Date(now).toISOString(), day }) };
  }

  /** A turn the routes refused to store (its content judged sensitive there): counted, nothing kept. */
  excluded(day, reason) {
    this.count(day, `excluded:${reason}`);
  }

  /** Every record of `owner` deleted; a tombstone says when and how many. → {deleted}. */
  deleteOwner(owner, { by = "visitor", at = new Date().toISOString(), day } = {}) {
    const n = Number(this.sql.exec("SELECT COUNT(*) AS n FROM conv_records WHERE owner = ?", owner).one().n);
    this.sql.exec("DELETE FROM conv_records WHERE owner = ?", owner);
    this.sql.exec("INSERT INTO conv_deletions (at, day, owner, n, by) VALUES (?, ?, ?, ?, ?)", at, day || at.slice(0, 10), owner, n, by);
    if (n) this.count(day || at.slice(0, 10), "deleted", n);
    return { deleted: n };
  }

  /** The records with these ids deleted (an admin's choice). → {deleted}. */
  deleteIds(ids, { by = "admin", at = new Date().toISOString(), day } = {}) {
    let n = 0;
    for (const id of (Array.isArray(ids) ? ids : []).slice(0, 500)) {
      const row = this.sql.exec("DELETE FROM conv_records WHERE id = ? RETURNING owner", String(id)).toArray()[0];
      if (!row) continue;
      n++;
      this.sql.exec("INSERT INTO conv_deletions (at, day, owner, n, by) VALUES (?, ?, ?, 1, ?)", at, day || at.slice(0, 10), row.owner, by);
    }
    if (n) this.count(day || at.slice(0, 10), "deleted", n);
    return { deleted: n };
  }

  /**
   * Records from..to, newest first, `limit` at a time after the cursor `before` (a seq). Filters: status, provider,
   * owner. → {items: [{seq, received, record}], next}.
   */
  list({ from, to, status = "", provider = "", owner = "", before = 0, limit = 50 } = {}) {
    const where = ["day >= ?", "day <= ?"];
    const args = [from, to];
    if (status) { where.push("status = ?"); args.push(status); }
    if (provider) { where.push("provider = ?"); args.push(provider); }
    if (owner) { where.push("owner = ?"); args.push(owner); }
    if (before > 0) { where.push("seq < ?"); args.push(before); }
    const n = Math.min(200, Math.max(1, Math.floor(Number(limit)) || 50));
    const rows = this.sql.exec(`SELECT seq, received, record FROM conv_records WHERE ${where.join(" AND ")} ORDER BY seq DESC LIMIT ?`, ...args, n + 1).toArray();
    const items = rows.slice(0, n).map((r) => ({ seq: Number(r.seq), received: r.received, record: JSON.parse(r.record) }));
    return { items, next: rows.length > n ? items[items.length - 1].seq : null };
  }

  /** All records from..to, oldest first (an authorized export), at most `limit`. */
  exportRows(from, to, limit = 20000) {
    return this.sql.exec("SELECT seq, received, record FROM conv_records WHERE day >= ? AND day <= ? ORDER BY seq LIMIT ?", from, to, limit).toArray()
      .map((r) => ({ seq: Number(r.seq), received: r.received, ...JSON.parse(r.record) }));
  }

  /** Counts for the dashboard: stored, duplicate, excluded (by reason), deleted, by day; what is held now. */
  stats(from, to) {
    const daily = this.sql.exec("SELECT day, metric, n FROM conv_counts WHERE day >= ? AND day <= ? ORDER BY day", from, to).toArray()
      .map((r) => ({ day: r.day, metric: r.metric, n: Number(r.n) }));
    const totals = {};
    for (const r of daily) totals[r.metric] = (totals[r.metric] || 0) + r.n;
    const held = this.sql.exec("SELECT COUNT(*) AS n, MIN(day) AS oldest FROM conv_records").one();
    const byStatus = this.sql.exec("SELECT status, COUNT(*) AS n FROM conv_records WHERE day >= ? AND day <= ? GROUP BY status", from, to).toArray()
      .reduce((m, r) => ((m[r.status] = Number(r.n)), m), {});
    const deletions = Number(this.sql.exec("SELECT COUNT(*) AS n FROM conv_deletions WHERE day >= ? AND day <= ?", from, to).one().n);
    return { daily, totals, held: Number(held.n), oldest: held.oldest || null, by_status: byStatus, deletion_requests: deletions };
  }

  /** Delete records older than `days` days before `today`, and tombstones and counts older than the longer of that and
   * a year (they hold no content). → rows removed. */
  prune(today, { days = 90 } = {}) {
    const before = Number(this.sql.exec("SELECT COUNT(*) AS n FROM conv_records").one().n);
    this.sql.exec("DELETE FROM conv_records WHERE day < ?", shiftDay(today, -days));
    const meta = shiftDay(today, -Math.max(days, 365));
    this.sql.exec("DELETE FROM conv_deletions WHERE day < ?", meta);
    this.sql.exec("DELETE FROM conv_counts WHERE day < ?", meta);
    return { removed: before - Number(this.sql.exec("SELECT COUNT(*) AS n FROM conv_records").one().n) };
  }
}
