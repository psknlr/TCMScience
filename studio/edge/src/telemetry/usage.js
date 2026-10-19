// Usage statistics in one SQLite-backed Durable Object (UsageLog in src/index.js): counts by day, and one row per finished
// turn and per tool run for rates and percentiles. No text a visitor wrote is ever here, and no identifier that
// outlives two days: a visitor is counted once a day by sha256(daily salt | a digest of their address and browser), the
// salt is random, made each day and deleted the day after, so a day's visitors cannot be linked to another day's — by
// anyone, the owner included.
//
// What is counted, and how the dashboard keeps them apart (studio/docs/TELEMETRY.md):
//   pageview     each route the app shows (a route's name only: never an id of a project or conversation)
//   visit        a visit begins (the page decides: a tab's first route, or the first after 30 minutes idle)
//   visitor      a distinct visitor that day (salted, above); "asker" the same for visitors who asked
//   turn_start   a question sent; conversation_start the first question of a conversation
//   turn_end     a turn finished, by status (completed / cancelled / failed / interrupted)
//   model_call   one request to a model: a turn makes one per round of tool use, and one per retry
//   tool_run     one tool execution, by tool and status
//   relay_call, relay_tokens, connector   seen by the Worker itself (Tao-S1 calls, database gateway requests by database)
// Counters are written once per batch (summed first) to keep the number of rows written small; the tables keyed by text
// are WITHOUT ROWID (a rowid table keyed by text keeps a second index of the key: one more row written per insert); and
// what a day may write at all is the object's share of TELEMETRY_DAILY_ROWS (telemetry/budget.js).

import { normalizeBatch } from "../../../web/js/core/telemetry-contract.js";
import { RowBudget } from "./budget.js";
import { daysBetween, shiftDay } from "./config.js";

const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
const enc = new TextEncoder();
const MAX_ROWS = 50000; // rows read for a percentile at most (the newest)
export const PAGE_ROWS = 1000; // rows of an export a page (each page one request: the Worker's CPU per request is small)
// each export: its rows from..to (two parameters), and the columns that order them and resume after a page
const EXPORT_SPECS = {
  daily: { key: ["day", "metric", "a", "b"], sql: "SELECT day, metric, a, b, n FROM usage_counts WHERE day >= ? AND day <= ?" },
  turns: {
    key: ["day", "id"], // the day index's own order: a page seeks, never sorts the period
    sql: `SELECT id, day, ts, provider, model, status, first, steps, tool_rounds, calls, tools, tool_failures, ms, first_ms, tokens_in,
      tokens_out, citations, citations_verified, citations_used, claims, claims_refused, recorded, excluded, error_kind, error_type, limit_reached, country
      FROM usage_turns WHERE day >= ? AND day <= ?`,
  },
  tools: { key: ["day", "id"], sql: "SELECT id, day, ts, tool, kind, place, status, ms, error_type FROM usage_tools WHERE day >= ? AND day <= ?" },
  connectors: { key: ["day", "source", "outcome"], sql: "SELECT day, a AS source, b AS outcome, n FROM usage_counts WHERE metric = 'connector' AND day >= ? AND day <= ?" },
  geo: {
    key: ["day", "metric", "country"],
    sql: `SELECT day, metric, CASE WHEN metric = 'visitor' THEN a ELSE b END AS country, SUM(n) AS n FROM usage_counts
      WHERE metric IN ('pageview', 'visit', 'visitor') AND day >= ? AND day <= ? GROUP BY day, metric, country`,
  },
};
export const METRICS = [
  "app_init", "app_browser", "visit", "pageview", "pageview_tab", "visitor", "asker", "turn_start", "conversation_start", "turn_end",
  "turn_recorded", "turn_excluded", "model_call", "tokens_in", "tokens_out", "tool_run", "tool_kind", "tool_place", "workflow",
  "app_error", "availability", "consent", "relay_call", "relay_tokens", "connector", "connector_ms", "telemetry_rejected",
];

export function percentile(sorted, p) {
  if (!sorted.length) return null;
  const rank = Math.min(sorted.length, Math.max(1, Math.ceil((p / 100) * sorted.length)));
  return sorted[rank - 1];
}

/** {n, avg, p50, p90, p95, p99, max} of a list of numbers (nulls dropped). */
export function spread(values) {
  const v = values.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return { n: 0, avg: null, p50: null, p90: null, p95: null, p99: null, max: null };
  const sum = v.reduce((a, b) => a + b, 0);
  return { n: v.length, avg: Math.round(sum / v.length), p50: percentile(v, 50), p90: percentile(v, 90), p95: percentile(v, 95), p99: percentile(v, 99), max: v[v.length - 1] };
}

export class UsageCore {
  constructor(sql) {
    this.sql = sql;
    sql.exec("CREATE TABLE IF NOT EXISTS usage_counts (day TEXT NOT NULL, metric TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, metric, a, b)) WITHOUT ROWID");
    sql.exec("CREATE TABLE IF NOT EXISTS usage_seen (day TEXT NOT NULL, kind TEXT NOT NULL, who TEXT NOT NULL, PRIMARY KEY (day, kind, who)) WITHOUT ROWID");
    sql.exec("CREATE TABLE IF NOT EXISTS usage_salts (day TEXT PRIMARY KEY, salt TEXT NOT NULL) WITHOUT ROWID");
    sql.exec("CREATE TABLE IF NOT EXISTS usage_once (id TEXT PRIMARY KEY, day TEXT NOT NULL) WITHOUT ROWID");
    sql.exec(`CREATE TABLE IF NOT EXISTS usage_turns (id TEXT PRIMARY KEY, day TEXT NOT NULL, ts INTEGER NOT NULL, provider TEXT NOT NULL,
      model TEXT NOT NULL, status TEXT NOT NULL, first INTEGER NOT NULL, steps INTEGER, tool_rounds INTEGER, calls INTEGER, tools INTEGER,
      tool_failures INTEGER, ms INTEGER, first_ms INTEGER, tokens_in INTEGER, tokens_out INTEGER, citations INTEGER, citations_verified INTEGER,
      citations_used INTEGER, claims INTEGER, claims_refused INTEGER, recorded INTEGER, excluded TEXT, error_kind TEXT, error_type TEXT,
      limit_reached INTEGER, country TEXT NOT NULL) WITHOUT ROWID`);
    sql.exec("CREATE INDEX IF NOT EXISTS usage_turns_day ON usage_turns (day)");
    sql.exec(`CREATE TABLE IF NOT EXISTS usage_tools (id TEXT PRIMARY KEY, day TEXT NOT NULL, ts INTEGER NOT NULL, tool TEXT NOT NULL,
      kind TEXT NOT NULL, place TEXT NOT NULL, status TEXT NOT NULL, ms INTEGER, error_type TEXT) WITHOUT ROWID`);
    sql.exec("CREATE INDEX IF NOT EXISTS usage_tools_day ON usage_tools (day)");
    sql.exec("CREATE TABLE IF NOT EXISTS usage_limits (k TEXT PRIMARY KEY, n INTEGER NOT NULL, day TEXT NOT NULL) WITHOUT ROWID");
    this.minute = new Map();
    this.currentMinute = -1;
    this.limitDay = null;
    this.budget = new RowBudget(sql);
    this.written = 0; // rows written so far (each insert, update or delete, and each index entry it changes)
  }

  #wrote(n) {
    this.written += n;
  }

  // ----------------------------------------------------------------------------------------------- limits

  /** One request of `kind` from `caller` (a hashed address of the UTC day), within {perMinute, perDay}? Counted only
   * when let through. → {ok} | {ok:false, reason, retry}. */
  limit(caller, kind, limits, now = Date.now()) {
    const today = new Date(now).toISOString().slice(0, 10);
    if (today !== this.limitDay) {
      this.#wrote(this.sql.exec("DELETE FROM usage_limits WHERE day <> ? RETURNING k", today).toArray().length);
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
    const d = Number(this.sql.exec("SELECT n FROM usage_limits WHERE k = ?", dk).toArray()[0]?.n || 0);
    if (limits.perDay && d >= limits.perDay) return { ok: false, reason: "day", retry: Math.ceil((86400000 - (now % 86400000)) / 1000) };
    this.minute.set(mk, m + 1);
    this.sql.exec("INSERT INTO usage_limits (k, n, day) VALUES (?, 1, ?) ON CONFLICT(k) DO UPDATE SET n = n + 1", dk, today);
    this.#wrote(1);
    return { ok: true };
  }

  // ----------------------------------------------------------------------------------------------- writing

  /** The day's random salt, made on the day's first use; making it deletes every older salt and seen-set, so no
   * visitor of an earlier day can be matched again, by anyone. */
  salt(day) {
    let row = this.sql.exec("SELECT salt FROM usage_salts WHERE day = ?", day).toArray()[0];
    if (!row) {
      this.#wrote(this.sql.exec("DELETE FROM usage_salts WHERE day < ? RETURNING day", day).toArray().length);
      this.#wrote(this.sql.exec("DELETE FROM usage_seen WHERE day < ? RETURNING day", day).toArray().length);
      this.#wrote(this.sql.exec("INSERT INTO usage_salts (day, salt) VALUES (?, ?) ON CONFLICT(day) DO NOTHING RETURNING day", day,
        hex(crypto.getRandomValues(new Uint8Array(32)))).toArray().length);
      row = this.sql.exec("SELECT salt FROM usage_salts WHERE day = ?", day).toArray()[0];
    }
    return row.salt;
  }

  /** Is this the first time `who` is seen as `kind` on `day`? (salted here; `visitor` is the Worker's digest) */
  async firstSeen(day, kind, visitor) {
    if (!visitor) return false;
    const who = hex(await crypto.subtle.digest("SHA-256", enc.encode(`${this.salt(day)}|${visitor}`))).slice(0, 32);
    const fresh = this.sql.exec("INSERT INTO usage_seen (day, kind, who) VALUES (?, ?, ?) ON CONFLICT DO NOTHING RETURNING who", day, kind, who).toArray().length > 0;
    if (fresh) this.#wrote(1);
    return fresh;
  }

  /** Has this id been taken before (a retried batch, a turn reported twice, a job's end seen in two tabs)? */
  once(id, day) {
    const fresh = this.sql.exec("INSERT INTO usage_once (id, day) VALUES (?, ?) ON CONFLICT DO NOTHING RETURNING id", id, day).toArray().length > 0;
    if (fresh) this.#wrote(1);
    return fresh;
  }

  bump(counts) {
    for (const [key, n] of counts) {
      if (!n) continue;
      const [day, metric, a, b] = key.split("\u0000");
      this.sql.exec("INSERT INTO usage_counts (day, metric, a, b, n) VALUES (?, ?, ?, ?, ?) ON CONFLICT(day, metric, a, b) DO UPDATE SET n = n + excluded.n",
        day, metric, a, b, n);
      this.#wrote(1);
    }
  }

  /**
   * One batch from a page: {v, batch, events}, read through the contract (telemetry-contract.js normalizeBatch).
   * ctx: {day, country, visitor (the Worker's digest of address and browser; never stored as given), now, the
   * per-visitor limits {caller, limits}, checked first in the same call, and `budget`: the rows this object may write
   * today (0: no budget)}. → {ok, accepted, duplicate?, dropped} | {ok:false, reason: "rate_limited" (with retry) |
   * "budget" (with retry: the day's rows are written, or this visitor's share of them) | "bad_batch"}.
   */
  async ingest(body, { day, country = "XX", visitor = "", now = Date.now(), caller = "", limits = null, budget = 0 } = {}) {
    const room = this.budget.check(budget, { caller, now });
    if (!room.ok) return { ok: false, reason: "budget", retry: room.retry, scope: room.reason };
    const start = this.written;
    try {
      return await this.#ingest(body, { day, country, visitor, now, caller, limits });
    } finally {
      this.budget.charge(this.written - start, { caller, now });
    }
  }

  async #ingest(body, { day, country, visitor, now, caller, limits }) {
    if (caller && limits) {
      const gate = this.limit(caller, "events", limits, now);
      if (!gate.ok) return { ok: false, reason: "rate_limited", retry: gate.retry };
    }
    const b = normalizeBatch(body);
    if (!b) return { ok: false, reason: "bad_batch" };
    if (!this.once(`batch:${b.batch}`, day)) return { ok: true, duplicate: true, accepted: 0, dropped: b.dropped };
    const counts = new Map();
    const add = (metric, a = "", b2 = "", n = 1) => {
      const k = [day, metric, String(a ?? ""), String(b2 ?? "")].join("\u0000");
      counts.set(k, (counts.get(k) || 0) + n);
    };
    if (b.dropped) add("telemetry_rejected", "invalid_event", "", b.dropped);
    if (b.events.length && (await this.firstSeen(day, "visitor", visitor))) add("visitor", country);
    for (const e of b.events) {
      switch (e.type) {
        case "app_init":
          add("app_init", e.layout || "unknown", e.lang || "");
          add("app_browser", e.browser || "other", e.mobile ? "mobile" : "desktop");
          break;
        case "visit": add("visit", e.route, country); break;
        case "pageview":
          add("pageview", e.route, country);
          if (e.tab) add("pageview_tab", e.route, e.tab);
          break;
        case "turn_start":
          add("turn_start", e.provider || "unknown", e.model || "");
          if (e.first) add("conversation_start", e.provider || "unknown", "");
          if (await this.firstSeen(day, "asker", visitor)) add("asker", country);
          break;
        case "turn_end": {
          const fresh = this.sql.exec(`INSERT INTO usage_turns (id, day, ts, provider, model, status, first, steps, tool_rounds, calls, tools,
            tool_failures, ms, first_ms, tokens_in, tokens_out, citations, citations_verified, citations_used, claims, claims_refused, recorded,
            excluded, error_kind, error_type, limit_reached, country) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING RETURNING id`,
          e.turn, day, now, e.provider || "unknown", e.model || "", e.status, e.first ? 1 : 0, e.steps ?? null, e.tool_rounds ?? null,
          e.calls ?? null, e.tools ?? null, e.tool_failures ?? null, e.ms ?? null, e.first_ms ?? null, e.tokens_in ?? null, e.tokens_out ?? null,
          e.citations ?? null, e.citations_verified ?? null, e.citations_used ?? null, e.claims ?? null, e.claims_refused ?? null,
          e.recorded ? 1 : 0, e.excluded ?? null, e.error_kind ?? null, e.error_type ?? null, e.limit_reached ? 1 : 0, country).toArray().length > 0;
          if (!fresh) break;
          this.#wrote(2); // the row and its day index
          const provider = e.provider || "unknown";
          add("turn_end", e.status, provider);
          add("model_call", provider, e.model || "", e.calls ?? e.steps ?? 0);
          add("tokens_in", provider, e.model || "", e.tokens_in || 0);
          add("tokens_out", provider, e.model || "", e.tokens_out || 0);
          if (e.recorded) add("turn_recorded", provider);
          if (e.excluded) add("turn_excluded", e.excluded);
          break;
        }
        case "tool_end": {
          const fresh = this.sql.exec(`INSERT INTO usage_tools (id, day, ts, tool, kind, place, status, ms, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING RETURNING id`, e.call, day, now, e.tool, e.kind, e.place, e.status, e.ms ?? null, e.error_type ?? null).toArray().length > 0;
          if (!fresh) break;
          this.#wrote(2);
          add("tool_run", e.tool, e.status);
          add("tool_kind", e.kind, e.status);
          add("tool_place", e.place, e.status);
          break;
        }
        case "workflow":
          if (this.once(`workflow:${e.key}:${e.phase}`, day)) add("workflow", `${e.kind}:${e.name || "-"}`, e.phase);
          break;
        case "error": add("app_error", e.category, `${e.name || "Error"}${e.where ? `@${e.where}` : ""}`.slice(0, 120)); break;
        case "availability": add("availability", e.service, e.ok ? "up" : `down${e.detail ? `:${e.detail}` : ""}`); break;
        case "consent": add("consent", e.decision, String(e.version ?? "")); break;
        default: break;
      }
    }
    this.bump(counts);
    return { ok: true, accepted: b.events.length, dropped: b.dropped };
  }

  /** Counts the Worker took itself, summed in its memory between flushes (telemetry/buffer.js): [{metric, a, b, n}] on
   * `day` (only known metrics, whole positive numbers), within the day's `budget` of rows (0: none). → {ok} |
   * {ok:false, reason: "budget", retry} | {ok:false}. */
  observe(list, { day, budget = 0, now = Date.now() } = {}) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day)) || !Array.isArray(list)) return { ok: false };
    const room = this.budget.check(budget, { now });
    if (!room.ok) return { ok: false, reason: "budget", retry: room.retry };
    const start = this.written;
    const counts = new Map();
    for (const m of list.slice(0, 2000)) {
      const n = m?.n === undefined ? 1 : Math.floor(Number(m.n));
      if (!METRICS.includes(m?.metric) || !(n > 0)) continue;
      const k = [day, m.metric, String(m.a ?? "").slice(0, 120), String(m.b ?? "").slice(0, 120)].join("\u0000");
      counts.set(k, (counts.get(k) || 0) + n);
    }
    this.bump(counts);
    this.budget.charge(this.written - start, { now });
    return { ok: true };
  }

  /** Today's rows written by this object, and its budget (for the dashboard). */
  budgetState(budget = 0, now = Date.now()) {
    return this.budget.state(budget, now);
  }

  // ----------------------------------------------------------------------------------------------- reading

  /**
   * Everything the dashboard shows for from..to (inclusive), and the as-long period before it for the change. Filters:
   * {provider, model, country} narrow the turns (and with country, the traffic). Never reads anything a visitor wrote:
   * there is none here.
   */
  stats({ from, to, provider = "", model = "", country = "", budget = 0 } = {}) {
    const days = daysBetween(from, to);
    const span = days.length;
    const prevFrom = shiftDay(from, -span);
    const prevTo = shiftDay(from, -1);
    const idx = new Map(days.map((d, i) => [d, i]));
    const zeros = () => days.map(() => 0);
    const sum = (a) => a.reduce((x, y) => x + y, 0);

    // ---- the counters, by day: the filters apply the same way to this period and to the one before it (the change).
    // country narrows the traffic; provider and model narrow what was counted per model.
    const trafficMetrics = ["pageview", "visit", "visitor", "asker"];
    const modelMetrics = ["turn_start", "model_call", "tokens_in", "tokens_out"];
    const SERIES = [...trafficMetrics, "app_init", "turn_start", "conversation_start", "model_call", "tokens_in", "tokens_out", "tool_run", "app_error", "relay_call", "connector"];
    const countryCol = (metric) => (metric === "pageview" || metric === "visit" ? "b" : "a");
    const kept = (r) => {
      if (country && trafficMetrics.includes(r.metric) && r[countryCol(r.metric)] !== country) return false;
      if (provider && (modelMetrics.includes(r.metric) || r.metric === "conversation_start") && r.a !== provider) return false;
      if (model && modelMetrics.includes(r.metric) && r.b !== model) return false;
      return true;
    };
    const unfilterable = new Set([...(country ? ["turns_started", "conversations_started"] : []), ...(model ? ["conversations_started"] : [])]);
    const series = Object.fromEntries(SERIES.map((m) => [m, zeros()]));
    // the period's counters, read once: the series (filtered) and every breakdown below (not filtered) come from it
    // (each row read is one of the account's daily rows read, which every Durable Object shares)
    const groups = new Map(); // metric → `${a}\u0000${b}` → n
    for (const r of this.sql.exec("SELECT day, metric, a, b, n FROM usage_counts WHERE day >= ? AND day <= ?", from, to).toArray()) {
      const n = Number(r.n) || 0;
      if (series[r.metric] && idx.has(r.day) && kept(r)) series[r.metric][idx.get(r.day)] += n;
      if (!groups.has(r.metric)) groups.set(r.metric, new Map());
      const g = groups.get(r.metric);
      const k = `${r.a}\u0000${r.b}`;
      g.set(k, (g.get(k) || 0) + n);
    }
    const totals = Object.fromEntries(Object.entries(series).map(([k, v]) => [k, sum(v)]));
    const previous = Object.fromEntries(SERIES.map((m) => [m, 0]));
    for (const r of this.sql.exec("SELECT metric, a, b, SUM(n) AS n FROM usage_counts WHERE day >= ? AND day <= ? GROUP BY metric, a, b", prevFrom, prevTo).toArray()) {
      if (r.metric in previous && kept(r)) previous[r.metric] += Number(r.n) || 0;
    }

    // ---- turns (rows, every filter applies): status by day, success rate, latency, models, calls and tokens. One
    // grouped read of the period serves them all (and, unfiltered, the filters' choices and the geography); the
    // latency percentiles read the completed turns' times; the period before is one aggregate
    const turnsWhere = (a, z) => {
      const where = ["day >= ?", "day <= ?"];
      const args = [a, z];
      if (provider) { where.push("provider = ?"); args.push(provider); }
      if (model) { where.push("model = ?"); args.push(model); }
      if (country) { where.push("country = ?"); args.push(country); }
      return [where.join(" AND "), args];
    };
    const [W, args] = turnsWhere(from, to);
    const statuses = ["completed", "cancelled", "failed", "interrupted"];
    const turnsDaily = Object.fromEntries(statuses.map((s) => [s, zeros()]));
    const modelDaily = { calls: zeros(), tokens_in: zeros(), tokens_out: zeros() };
    const byModel = new Map();
    const errorCounts = new Map();
    const agg = { n: 0, citations: 0, verified: 0, used: 0, claims: 0, refused: 0, recorded: 0, excluded: 0, limit_reached: 0, conversations: 0, steps: 0, stepsN: 0 };
    const providerSet = new Set();
    const modelSet = new Set();
    const turnsByCountry = new Map();
    const grouped = this.sql.exec(`SELECT day, provider, model, country, status, COALESCE(error_kind, '') AS ek, COALESCE(error_type, '') AS et,
      COUNT(*) AS n, SUM(first) AS first, SUM(COALESCE(calls, steps, 0)) AS calls, SUM(COALESCE(tokens_in, 0)) AS tokens_in,
      SUM(COALESCE(tokens_out, 0)) AS tokens_out, SUM(CASE WHEN status = 'completed' THEN ms END) AS ms_sum,
      SUM(CASE WHEN status = 'completed' AND ms IS NOT NULL THEN 1 ELSE 0 END) AS ms_n, SUM(steps) AS steps_sum, COUNT(steps) AS steps_n,
      SUM(COALESCE(citations, 0)) AS citations, SUM(COALESCE(citations_verified, 0)) AS verified, SUM(COALESCE(citations_used, 0)) AS used,
      SUM(COALESCE(claims, 0)) AS claims, SUM(COALESCE(claims_refused, 0)) AS refused, SUM(recorded) AS recorded,
      SUM(CASE WHEN excluded IS NOT NULL THEN 1 ELSE 0 END) AS excluded, SUM(limit_reached) AS limit_reached
      FROM usage_turns WHERE day >= ? AND day <= ? GROUP BY day, provider, model, country, status, ek, et`, from, to).toArray();
    for (const r of grouped) {
      const n = Number(r.n) || 0;
      // unfiltered: what the filters may choose, and the turns of each place
      providerSet.add(r.provider);
      if (r.model) modelSet.add(r.model);
      turnsByCountry.set(r.country, (turnsByCountry.get(r.country) || 0) + n);
      if ((provider && r.provider !== provider) || (model && r.model !== model) || (country && r.country !== country)) continue;
      const i = idx.get(r.day);
      if (i !== undefined && turnsDaily[r.status]) turnsDaily[r.status][i] += n;
      if (i !== undefined) {
        modelDaily.calls[i] += Number(r.calls) || 0;
        modelDaily.tokens_in[i] += Number(r.tokens_in) || 0;
        modelDaily.tokens_out[i] += Number(r.tokens_out) || 0;
      }
      const mk = `${r.provider}\u0000${r.model}`;
      const m = byModel.get(mk) || { provider: r.provider, model: r.model, turns: 0, completed: 0, failed: 0, calls: 0, tokens_in: 0, tokens_out: 0, ms_sum: 0, ms_n: 0 };
      m.turns += n;
      if (r.status === "completed") m.completed += n;
      if (r.status === "failed") m.failed += n;
      m.calls += Number(r.calls) || 0;
      m.tokens_in += Number(r.tokens_in) || 0;
      m.tokens_out += Number(r.tokens_out) || 0;
      m.ms_sum += Number(r.ms_sum) || 0;
      m.ms_n += Number(r.ms_n) || 0;
      byModel.set(mk, m);
      if (r.status === "failed") {
        const ek = `${r.ek}\u0000${r.et}`;
        errorCounts.set(ek, (errorCounts.get(ek) || 0) + n);
      }
      agg.n += n;
      agg.conversations += Number(r.first) || 0;
      agg.steps += Number(r.steps_sum) || 0;
      agg.stepsN += Number(r.steps_n) || 0;
      for (const k of ["citations", "verified", "used", "claims", "refused", "recorded", "excluded", "limit_reached"]) agg[k] += Number(r[k]) || 0;
    }
    const turnTotals = Object.fromEntries(statuses.map((s) => [s, sum(turnsDaily[s])]));
    const finished = sum(Object.values(turnTotals));
    const models = [...byModel.values()].sort((a, b) => b.turns - a.turns).slice(0, 100)
      .map(({ ms_sum: msSum, ms_n: msN, ...m }) => ({ ...m, avg_ms: msN ? Math.round(msSum / msN) : null }));
    const errorsByType = [...errorCounts].map(([k, n]) => {
      const [kind, type] = k.split("\u0000");
      return { kind, type, n };
    }).sort((a, b) => b.n - a.n).slice(0, 30);
    const [PW, pargs] = turnsWhere(prevFrom, prevTo);
    const before = this.sql.exec(`SELECT COUNT(*) AS n, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed, SUM(first) AS conversations,
      SUM(COALESCE(calls, steps, 0)) AS calls, SUM(COALESCE(tokens_in, 0)) AS tokens_in, SUM(COALESCE(tokens_out, 0)) AS tokens_out
      FROM usage_turns WHERE ${PW}`, ...pargs).one();
    const toolsBefore = this.sql.exec("SELECT COUNT(*) AS n, SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed FROM usage_tools WHERE day >= ? AND day <= ?",
      prevFrom, prevTo).one();
    const latencyRows = this.sql.exec(`SELECT ms, first_ms FROM usage_turns WHERE ${W} AND status = 'completed' ORDER BY ts DESC LIMIT ?`, ...args, MAX_ROWS).toArray();

    // ---- tools (rows): runs, failures, durations
    const toolRows = this.sql.exec("SELECT tool, kind, status, ms FROM usage_tools WHERE day >= ? AND day <= ? ORDER BY ts DESC LIMIT ?", from, to, MAX_ROWS).toArray();
    const byTool = new Map();
    for (const r of toolRows) {
      const t = byTool.get(r.tool) || { tool: r.tool, kind: r.kind, runs: 0, failed: 0, refused: 0, ms: [] };
      t.runs++;
      if (r.status === "failed") t.failed++;
      if (r.status === "refused") t.refused++;
      if (Number.isFinite(r.ms)) t.ms.push(Number(r.ms));
      byTool.set(r.tool, t);
    }
    const tools = [...byTool.values()].map(({ ms, ...t }) => {
      const s = spread(ms);
      return { ...t, failure_rate: t.runs ? t.failed / t.runs : 0, p50_ms: s.p50, p90_ms: s.p90 };
    }).sort((a, b) => b.runs - a.runs).slice(0, 200);
    const toolDaily = { runs: zeros(), failed: zeros() };
    for (const r of this.sql.exec("SELECT day, status, COUNT(*) AS n FROM usage_tools WHERE day >= ? AND day <= ? GROUP BY day, status", from, to).toArray()) {
      if (!idx.has(r.day)) continue;
      toolDaily.runs[idx.get(r.day)] += Number(r.n);
      if (r.status === "failed") toolDaily.failed[idx.get(r.day)] += Number(r.n);
    }

    // ---- breakdowns from the counters
    // the largest thousand of each breakdown (a page's events name what they name: their number is not the store's to
    // bound, and a summary must stay small enough for one answer)
    const group = (metric) => [...(groups.get(metric) || new Map())].map(([k, n]) => {
      const [a, b] = k.split("\u0000");
      return { a, b, n };
    }).sort((x, y) => y.n - x.n).slice(0, 1000);
    // by database (the registry's source key), "-" for what the gateway refused before it matched one
    const connectors = new Map();
    for (const r of group("connector")) {
      const c = connectors.get(r.a) || { source: r.a, requests: 0, ok: 0, client_errors: 0, server_errors: 0, refused: 0, failed: 0, cancelled: 0, ms_total: 0 };
      c.requests += r.n;
      if (/^[123]xx$/.test(r.b)) c.ok += r.n;
      else if (r.b === "4xx") c.client_errors += r.n;
      else if (r.b === "5xx") c.server_errors += r.n;
      else if (r.b.startsWith("refused")) c.refused += r.n; // the gateway's own: its limits, its failures — never sent
      else if (r.b === "cancelled") c.cancelled += r.n; // the visitor stopped it: says nothing of the database
      else c.failed += r.n; // sent, and no answer: timeout, unreachable
      connectors.set(r.a, c);
    }
    for (const r of group("connector_ms")) {
      const c = connectors.get(r.a);
      if (c) c.ms_total += r.n;
    }
    // availability: of the requests sent to the database, the share it answered without a server error (a 4xx is the
    // query's fault, not the database's; a refusal of the gateway never left it); the time, of those sent too
    const connectorList = [...connectors.values()].map(({ ms_total: total, ...c }) => {
      const tried = c.ok + c.client_errors + c.server_errors + c.failed;
      return { ...c, availability: tried ? (c.ok + c.client_errors) / tried : null, avg_ms: tried ? Math.round(total / tried) : null };
    }).sort((a, b) => b.requests - a.requests).slice(0, 200);
    const workflows = new Map();
    for (const r of group("workflow")) {
      const w = workflows.get(r.a) || { workflow: r.a, started: 0, submitted: 0, completed: 0, failed: 0, refused: 0, cancelled: 0 };
      if (r.b in w) w[r.b] += r.n;
      workflows.set(r.a, w);
    }
    const geo = new Map();
    const place = (c) => {
      if (!geo.has(c)) geo.set(c, { country: c, pageviews: 0, visits: 0, visitors: 0, turns: 0 });
      return geo.get(c);
    };
    for (const r of group("pageview")) place(r.b).pageviews += r.n;
    for (const r of group("visit")) place(r.b).visits += r.n;
    for (const r of group("visitor")) place(r.a).visitors += r.n;
    for (const [c, n] of turnsByCountry) place(c).turns += n;
    const availability = new Map();
    for (const r of group("availability")) {
      const s = availability.get(r.a) || { service: r.a, checks: 0, up: 0, down: 0 };
      s.checks += r.n;
      if (r.b === "up") s.up += r.n;
      else s.down += r.n;
      availability.set(r.a, s);
    }
    const byKey = (metric) => group(metric).sort((a, b) => b.n - a.n);
    return {
      days,
      budget: this.budget.state(budget),
      traffic: { daily: { pageviews: series.pageview, visits: series.visit, visitors: series.visitor, askers: series.asker, app_inits: series.app_init },
        totals: { pageviews: totals.pageview, visits: totals.visit, visitor_days: totals.visitor, asker_days: totals.asker, app_inits: totals.app_init },
        previous: { pageviews: previous.pageview, visits: previous.visit, visitor_days: previous.visitor },
        routes: byKey("pageview").reduce((m, r) => ((m[r.a] = (m[r.a] || 0) + r.n), m), {}),
        clients: { layouts: byKey("app_init"), browsers: byKey("app_browser") } },
      conversations: {
        // a question sent is counted by provider and model only, a conversation's start by provider only: under a filter
        // they cannot be narrowed by, they are null, never the unfiltered numbers (the turn rows below are filtered)
        unfiltered: [...unfilterable],
        daily: { started: unfilterable.has("turns_started") ? null : series.turn_start, conversations: unfilterable.has("conversations_started") ? null : series.conversation_start, ...turnsDaily },
        totals: { turns_started: unfilterable.has("turns_started") ? null : totals.turn_start, conversations_started: unfilterable.has("conversations_started") ? null : totals.conversation_start, finished, ...turnTotals },
        previous: { turns_started: unfilterable.has("turns_started") ? null : previous.turn_start, finished: Number(before.n) || 0, completed: Number(before.completed) || 0,
          conversations: Number(before.conversations) || 0 },
        success_rate: finished ? turnTotals.completed / finished : null,
        conversations: agg.conversations, recorded: agg.recorded, excluded: agg.excluded,
        excluded_by: byKey("turn_excluded").map((r) => ({ reason: r.a, n: r.n })),
        limit_reached: agg.limit_reached, avg_steps: agg.stepsN ? Math.round((agg.steps / agg.stepsN) * 10) / 10 : null,
        errors: errorsByType,
      },
      evidence: { citations: agg.citations, verified: agg.verified, used: agg.used, claims: agg.claims, claims_refused: agg.refused },
      models: { daily: modelDaily, totals: { calls: sum(modelDaily.calls), tokens_in: sum(modelDaily.tokens_in), tokens_out: sum(modelDaily.tokens_out) },
        previous: { calls: Number(before.calls) || 0, tokens_in: Number(before.tokens_in) || 0, tokens_out: Number(before.tokens_out) || 0 }, list: models },
      latency: { total: spread(latencyRows.map((r) => (r.ms === null ? NaN : Number(r.ms)))), first_token: spread(latencyRows.map((r) => (r.first_ms === null ? NaN : Number(r.first_ms)))) },
      tools: { daily: toolDaily, totals: { runs: sum(toolDaily.runs), failed: sum(toolDaily.failed) },
        previous: { runs: Number(toolsBefore.n) || 0, failed: Number(toolsBefore.failed) || 0 }, list: tools,
        by_kind: byKey("tool_kind").map((r) => ({ kind: r.a, status: r.b, n: r.n })), by_place: byKey("tool_place").map((r) => ({ place: r.a, status: r.b, n: r.n })) },
      connectors: connectorList,
      workflows: [...workflows.values()].sort((a, b) => (b.started + b.submitted) - (a.started + a.submitted)).slice(0, 100),
      geography: [...geo.values()].sort((a, b) => (b.pageviews - a.pageviews) || (b.turns - a.turns)).slice(0, 250),
      errors: { daily: series.app_error, total: totals.app_error, previous: previous.app_error, list: byKey("app_error").map((r) => ({ category: r.a, name: r.b, n: r.n })).slice(0, 50) },
      availability: [...availability.values()],
      relay: { daily: series.relay_call, outcomes: byKey("relay_call").map((r) => ({ outcome: r.a, n: r.n })),
        tokens: byKey("relay_tokens").reduce((m, r) => ((m[r.a] = (m[r.a] || 0) + r.n), m), {}) },
      consent: byKey("consent").reduce((m, r) => ((m[r.a] = (m[r.a] || 0) + r.n), m), {}),
      rejected: byKey("telemetry_rejected").map((r) => ({ reason: r.a, n: r.n })),
      filters: { providers: [...providerSet].sort(), models: [...modelSet].sort(), countries: [...geo.keys()].sort() },
    };
  }

  /**
   * One page of an export, oldest first: daily (the counters), turns, tools, connectors, geo. `after`: the previous
   * page's last row's key (the `next` it returned), `limit`: rows a page (at most PAGE_ROWS). → {rows, next} (next:
   * null on the last page), or null for an unknown dataset or a malformed key.
   */
  exportRows(dataset, from, to, { after = null, limit = PAGE_ROWS } = {}) {
    const n = Math.min(PAGE_ROWS, Math.max(1, Math.floor(Number(limit)) || PAGE_ROWS));
    const spec = EXPORT_SPECS[dataset];
    if (!spec) return null;
    const key = after === null || after === undefined ? null : after;
    if (key !== null && !(Array.isArray(key) && key.length === spec.key.length && key.every((v) => typeof v === "string" || Number.isFinite(v)))) return null;
    const keyCols = spec.key.join(", ");
    const where = key ? ` AND (${keyCols}) > (${spec.key.map(() => "?").join(", ")})` : "";
    // every key starts with the day: a later page begins its scan at the day it stopped in, not at the period's start
    const lo = key && String(key[0]) > from ? String(key[0]) : from;
    const rows = this.sql.exec(`SELECT * FROM (${spec.sql}) WHERE 1${where} ORDER BY ${keyCols} LIMIT ?`, lo, to, ...(key || []), n + 1).toArray();
    const page = rows.slice(0, n);
    const last = page[page.length - 1];
    return { rows: page, next: rows.length > n ? spec.key.map((k) => last[k]) : null };
  }

  /** Delete what is past its retention: rows after `events` days, counts after `aggregates`, the salts and seen-sets
   * of every day before today (a visitor of an earlier day can no longer be matched), a batch's dedup id after two
   * days, other dedup ids with the rows. → rows removed. */
  prune(today, { events = 90, aggregates = 400 } = {}) {
    const before = this.size();
    const ev = shiftDay(today, -events);
    this.sql.exec("DELETE FROM usage_turns WHERE day < ?", ev);
    this.sql.exec("DELETE FROM usage_tools WHERE day < ?", ev);
    this.sql.exec("DELETE FROM usage_once WHERE day < ? OR (id LIKE 'batch:%' AND day < ?)", ev, shiftDay(today, -2));
    this.sql.exec("DELETE FROM usage_counts WHERE day < ?", shiftDay(today, -aggregates));
    this.sql.exec("DELETE FROM usage_seen WHERE day < ?", today);
    this.sql.exec("DELETE FROM usage_salts WHERE day < ?", today);
    return { removed: before - this.size() };
  }

  size() {
    let n = 0;
    for (const t of ["usage_counts", "usage_turns", "usage_tools", "usage_once", "usage_seen", "usage_salts"]) n += Number(this.sql.exec(`SELECT COUNT(*) AS n FROM ${t}`).one().n);
    return n;
  }
}
