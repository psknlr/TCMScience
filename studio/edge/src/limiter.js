// The counters behind the Tao-S1 limits: one SQLite-backed Durable Object (src/index.js wraps this class).
// Ported from TaoChronos relay/src/state.js (LimiterCore) without its `served` table. It is a plain class over the
// Durable Object SQL API's shape — exec(query, ...bindings) → cursor {toArray(), one()} — so the tests run it on
// node:sqlite. A Durable Object's input gate serializes its calls, so hit() needs no transaction to be race-free.

/** The UTC day of a timestamp: every daily limit resets at UTC midnight (08:00 in Beijing). */
export const day = (now) => new Date(now).toISOString().slice(0, 10);
export const secondsToMidnight = (now) => Math.ceil((86400000 - (now % 86400000)) / 1000);

export class LimiterCore {
  constructor(sql) {
    this.sql = sql;
    // CREATE ... IF NOT EXISTS never migrates an existing table: a change of columns needs a new table name.
    this.sql.exec("CREATE TABLE IF NOT EXISTS hits (k TEXT PRIMARY KEY, n INTEGER NOT NULL, day TEXT NOT NULL)");
    this.minute = new Map(); // per-minute counts live in memory: an evicted object forgets at most a minute
    this.currentMinute = -1;
    this.currentDay = null;
  }

  count(key) {
    const rows = this.sql.exec("SELECT n FROM hits WHERE k = ?", key).toArray();
    return rows.length ? Number(rows[0].n) : 0;
  }

  bump(key, today, by = 1) {
    this.sql.exec("INSERT INTO hits (k, n, day) VALUES (?, MAX(0, ?), ?) ON CONFLICT(k) DO UPDATE SET n = MAX(0, n + ?)", key, by, today, by);
  }

  /**
   * Count one model call for `who`, or say why not.
   * limits: {perMinute, perDay, total, tokensPerDay, totalTokens} (0 = no limit).
   * reserve: tokens charged now (about the request's size), settled later by spend() with what the answer cost, so that
   * many calls in flight at once cannot overdraw the token allowance.
   * → {ok:true, remaining, reserved} | {ok:false, reason:"minute"|"total"|"day"|"total_tokens"|"tokens", retry}
   */
  hit(who, kind, limits, now = Date.now(), reserve = 0) {
    const today = day(now);
    if (today !== this.currentDay) {
      this.sql.exec("DELETE FROM hits WHERE day <> ?", today);
      this.currentDay = today;
    }
    const minute = Math.floor(now / 60000);
    if (minute !== this.currentMinute) {
      this.minute.clear();
      this.currentMinute = minute;
    }
    const mk = `${kind}:${who}`;
    const m = this.minute.get(mk) || 0;
    const retry = 60 - Math.floor((now / 1000) % 60);
    if (limits.perMinute && m >= limits.perMinute) return { ok: false, reason: "minute", retry };
    const tk = `t:${kind}:${today}`;
    if (limits.total && this.count(tk) >= limits.total) return { ok: false, reason: "total", retry: secondsToMidnight(now) };
    const dk = `d:${kind}:${who}:${today}`;
    const d = this.count(dk);
    if (limits.perDay && d >= limits.perDay) return { ok: false, reason: "day", retry: secondsToMidnight(now) };
    if (limits.totalTokens && this.count(`kt:${kind}:${today}`) >= limits.totalTokens) {
      return { ok: false, reason: "total_tokens", retry: secondsToMidnight(now) };
    }
    if (limits.tokensPerDay && this.count(`k:${kind}:${who}:${today}`) >= limits.tokensPerDay) {
      return { ok: false, reason: "tokens", retry: secondsToMidnight(now) };
    }
    this.minute.set(mk, m + 1);
    this.bump(dk, today);
    if (limits.total) this.bump(tk, today);
    const reserved = (limits.tokensPerDay || limits.totalTokens) && reserve > 0 ? Math.floor(reserve) : 0;
    if (reserved) this.spend(who, kind, reserved, now);
    return { ok: true, remaining: limits.perDay ? limits.perDay - d - 1 : null, reserved };
  }

  /** Tokens an answer cost beyond what was already charged for it (negative: the reserve was more than the cost). */
  spend(who, kind, tokens, now = Date.now()) {
    const n = Math.trunc(Number(tokens));
    if (!n) return;
    const today = day(now);
    this.bump(`k:${kind}:${who}:${today}`, today, n);
    this.bump(`kt:${kind}:${today}`, today, n);
  }
}
