// The counters behind the Tao-S1 limits, and the upstream's circuit breaker: one SQLite-backed Durable Object
// (src/index.js wraps this class). Ported from TaoChronos relay/src/state.js (LimiterCore) without its `served` table.
// It is a plain class over the Durable Object SQL API's shape — exec(query, ...bindings) → cursor {toArray(), one()} —
// so the tests run it on node:sqlite. A Durable Object's input gate serializes its calls, so hit() and the breaker need
// no transaction to be race-free.

/** The UTC day of a timestamp: every daily limit resets at UTC midnight (08:00 in Beijing). */
export const day = (now) => new Date(now).toISOString().slice(0, 10);
export const secondsToMidnight = (now) => Math.ceil((86400000 - (now % 86400000)) / 1000);

export class LimiterCore {
  constructor(sql) {
    this.sql = sql;
    // CREATE ... IF NOT EXISTS never migrates an existing table: a change of columns needs a new table name.
    this.sql.exec("CREATE TABLE IF NOT EXISTS hits (k TEXT PRIMARY KEY, n INTEGER NOT NULL, day TEXT NOT NULL)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS source_slots (host TEXT PRIMARY KEY, next_ms INTEGER NOT NULL)");
    // the upstream's circuit breaker: one row, key "upstream", for the whole service
    this.sql.exec("CREATE TABLE IF NOT EXISTS breaker (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
    this.minute = new Map(); // per-minute counts live in memory: an evicted object forgets at most a minute
    this.currentMinute = -1;
    this.currentDay = null;
    this.upstream = null; // the breaker's row, read once per object life and written through: this object is its only writer
  }

  count(key) {
    const rows = this.sql.exec("SELECT n FROM hits WHERE k = ?", key).toArray();
    return rows.length ? Number(rows[0].n) : 0;
  }

  bump(key, today, by = 1) {
    this.sql.exec("INSERT INTO hits (k, n, day) VALUES (?, MAX(0, ?), ?) ON CONFLICT(k) DO UPDATE SET n = MAX(0, n + ?)", key, by, today, by);
  }

  /** Reserve a host's next public API slot across every browser. Persisting the slot
   * prevents a cold Durable Object from bursting immediately after eviction. A full
   * ten-second queue is refused rather than keeping unbounded requests in memory. */
  sourceSlot(host, rps, now = Date.now(), prefixes = {}) {
    const interval = (rate) => Math.ceil(1000 / (Number.isFinite(Number(rate)) && Number(rate) > 0 ? Math.min(20, Number(rate)) : 2));
    const buckets = { [host]: interval(rps), ...Object.fromEntries(Object.entries(prefixes).map(([path, rate]) => [host + path, interval(rate)])) };
    let next = now;
    for (const key of Object.keys(buckets)) {
      const rows = this.sql.exec("SELECT next_ms FROM source_slots WHERE host = ?", key).toArray();
      next = Math.max(next, Number(rows[0]?.next_ms) || now);
    }
    const delay = next - now;
    if (delay > 10000) return { ok: false, retry: Math.ceil(delay / 1000) };
    for (const [key, gap] of Object.entries(buckets)) this.sql.exec("INSERT INTO source_slots (host, next_ms) VALUES (?, ?) ON CONFLICT(host) DO UPDATE SET next_ms = excluded.next_ms", key, next + gap);
    return { ok: true, delay };
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

  /** The breaker's state {until (ms; 0: closed), reason, probe (a call is trying the upstream), failures (ms)}. */
  breakerState() {
    if (!this.upstream) {
      let s = null;
      try {
        const rows = this.sql.exec("SELECT v FROM breaker WHERE k = 'upstream'").toArray();
        s = rows.length ? JSON.parse(rows[0].v) : null;
      } catch { /* a row that cannot be read is a closed breaker */ }
      this.upstream = {
        until: Number(s?.until) || 0, reason: typeof s?.reason === "string" ? s.reason : "", probe: s?.probe === true,
        failures: Array.isArray(s?.failures) ? s.failures.map(Number).filter(Number.isFinite) : [],
      };
    }
    return this.upstream;
  }

  saveBreaker(state) {
    this.upstream = state;
    this.sql.exec("INSERT INTO breaker (k, v) VALUES ('upstream', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", JSON.stringify(state));
  }

  /**
   * May a model call go to the upstream? settings: {open, errors, window} (seconds, count, seconds; relay.js config).
   * → {open:true, reason, until, retry} while the breaker is open: every call is answered at once. Once `until` has
   * passed, the next call that asks with `probe` (a model call; health never does) is let through as the one probe of a
   * new window, → {open:false, probe:true}; the calls after it wait for its outcome (breakerReport), a window at most.
   * Closed → {open:false}.
   */
  breaker(probe, settings, now = Date.now()) {
    const s = this.breakerState();
    if (!s.until) return { open: false };
    if (now < s.until) return { open: true, reason: s.reason, until: s.until, retry: Math.ceil((s.until - now) / 1000) };
    if (!probe) return { open: false };
    this.saveBreaker({ ...s, until: now + positive(settings?.open, 600) * 1000, probe: true });
    return { open: false, probe: true };
  }

  /**
   * What a model call brought, as far as the breaker is concerned:
   *   "upstream_quota"   the upstream's quota is spent: open now, until its reset (`reset` seconds) when it said, else
   *                      for settings.open seconds;
   *   "upstream_error", "upstream_unknown"   settings.errors of them within settings.window seconds open it for
   *                      settings.open seconds; the probe's one failure reopens it;
   *   "ok"               the probe was answered: closed;
   *   "released"         the probe learned nothing (refused here, unreachable): the next call may try.
   * Each trip and each close is logged. → the state as breaker() tells it to health.
   */
  breakerReport(outcome, settings, reset = null, now = Date.now()) {
    const s = this.breakerState();
    const open = (reason, secs) => {
      const until = now + secs * 1000;
      this.saveBreaker({ until, reason, probe: false, failures: [] });
      console.error("upstream breaker open", reason, new Date(until).toISOString());
    };
    const isOpen = s.until && !s.probe && now < s.until; // an answer to a call sent before the breaker opened changes nothing
    if (outcome === "ok") {
      if (s.until) {
        this.saveBreaker({ until: 0, reason: "", probe: false, failures: [] });
        console.error("upstream breaker closed");
      }
    } else if (outcome === "released") {
      if (s.probe) this.saveBreaker({ ...s, until: now, probe: false });
    } else if (outcome === "upstream_quota") {
      if (!isOpen) open("upstream_quota", Number(reset) > 0 ? Number(reset) : positive(settings?.open, 600));
    } else if (outcome === "upstream_error" || outcome === "upstream_unknown") {
      if (s.probe) open("upstream_error", positive(settings?.open, 600));
      else if (!isOpen) {
        const since = now - positive(settings?.window, 60) * 1000;
        const failures = [...s.failures.filter((t) => t > since), now].slice(-positive(settings?.errors, 3));
        if (failures.length >= positive(settings?.errors, 3)) open("upstream_error", positive(settings?.open, 600));
        else this.saveBreaker({ ...s, failures });
      }
    }
    return this.breaker(false, settings, now);
  }
}

/** A positive whole number of the breaker's settings, else its default. */
const positive = (v, fallback) => (Number.isFinite(Number(v)) && Number(v) >= 1 ? Math.floor(Number(v)) : fallback);
