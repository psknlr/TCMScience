// A daily budget of rows written, one for each telemetry object (UsageLog, ConversationLog, SecurityLog).
//
// Durable Objects bill (and on the Workers Free plan, limit) the rows each insert, update and delete touches, each index
// it updates counting one more: 100,000 rows written a day for the whole account on the Free plan, after which every
// operation of that kind fails until 00:00 UTC. The Limiter behind the relay's and the gateway's limits writes from the
// same allowance and fails closed: if the telemetry spent it, Tao-S1 and the database gateway would refuse everyone.
// So each telemetry object stops taking what visitors send once its share of TELEMETRY_DAILY_ROWS is written
// (telemetry/config.js), and no one visitor may write more than VISITOR_SHARE of it.
//
// What is never refused for the budget: a visitor's deletion request, the retention pruning, the admin's audit trail
// and sign-in state (they are counted, but privacy and security do not wait for tomorrow).
//
// The count follows the calendar day in UTC, as Cloudflare's allowance does. It is kept in memory and written to the
// object's own table every CHECKPOINT rows, so an object that was evicted and started again still knows (at most a
// checkpoint's worth less), at the cost of one row per checkpoint.

const CHECKPOINT = 250;
export const VISITOR_SHARE = 0.1; // of a day's budget, for one visitor (their hashed address)

const utcDay = (now) => new Date(now).toISOString().slice(0, 10);

/** Seconds until the next 00:00 UTC, when the budgets are new again. */
export function untilUtcMidnight(now = Date.now()) {
  return Math.max(1, Math.ceil((86400000 - (now % 86400000)) / 1000));
}

export class RowBudget {
  constructor(sql) {
    this.sql = sql;
    sql.exec("CREATE TABLE IF NOT EXISTS row_budget (day TEXT PRIMARY KEY, rows INTEGER NOT NULL) WITHOUT ROWID");
    this.day = null;
    this.spent = 0;
    this.saved = 0;
    this.visitors = new Map(); // caller → rows written for them today (memory only: a fairness share, not the cap)
  }

  #roll(now) {
    const day = utcDay(now);
    if (day === this.day) return;
    this.day = day;
    this.visitors.clear();
    const row = this.sql.exec("SELECT rows FROM row_budget WHERE day = ?", day).toArray()[0];
    this.spent = Number(row?.rows) || 0;
    this.saved = this.spent;
    this.spent += this.sql.exec("DELETE FROM row_budget WHERE day <> ? RETURNING day", day).toArray().length;
  }

  /** May `caller` (or anyone, without one) still write today, within `limit` rows (0: no budget)? → {ok} |
   * {ok:false, reason: "budget" (the object's day is spent) | "visitor" (this visitor's share is), retry}. */
  check(limit, { caller = "", now = Date.now() } = {}) {
    if (!(limit > 0)) return { ok: true };
    this.#roll(now);
    if (this.spent >= limit) return { ok: false, reason: "budget", retry: untilUtcMidnight(now) };
    if (caller && (this.visitors.get(caller) || 0) >= Math.max(1, Math.floor(limit * VISITOR_SHARE))) {
      return { ok: false, reason: "visitor", retry: untilUtcMidnight(now) };
    }
    return { ok: true };
  }

  /** `rows` written, for `caller` when a visitor's request wrote them. */
  charge(rows, { caller = "", now = Date.now() } = {}) {
    const n = Math.max(0, Math.floor(Number(rows) || 0));
    if (!n) return;
    this.#roll(now);
    this.spent += n;
    if (caller) this.visitors.set(caller, (this.visitors.get(caller) || 0) + n);
    if (this.visitors.size > 50000) this.visitors.clear();
    if (this.spent - this.saved >= CHECKPOINT) this.#save();
  }

  #save() {
    this.spent += 1; // this row too
    this.sql.exec("INSERT INTO row_budget (day, rows) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET rows = excluded.rows", this.day, this.spent);
    this.saved = this.spent;
  }

  /** What the dashboard shows: today's rows written by this object and its budget. */
  state(limit, now = Date.now()) {
    this.#roll(now);
    return { day: this.day, rows: this.spent, limit: limit > 0 ? limit : null };
  }
}

// Which objects said their day is spent, until when (ms): kept in this isolate's memory, so that what follows is
// refused without calling the object again (each call is a Durable Object request from the same allowance).
const spentUntil = new Map();

/** The object `name` said its day's rows are written: refuse in front of it until 00:00 UTC. */
export function markSpent(name, retrySeconds, now = Date.now()) {
  spentUntil.set(name, now + Math.max(1, Number(retrySeconds) || untilUtcMidnight(now)) * 1000);
}

/** Seconds until the object `name` takes writes again, or 0 when it may be asked now. */
export function spentFor(name, now = Date.now()) {
  const until = spentUntil.get(name) || 0;
  return until > now ? Math.ceil((until - now) / 1000) : 0;
}

/** For tests: every object may be asked again. */
export function resetSpent() {
  spentUntil.clear();
}
