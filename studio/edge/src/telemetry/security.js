// Security events: what the Worker itself sees of requests that are not the page's ordinary use — a scanner probing
// for /wp-login.php or /.env, a call from another site's page, a rejected or oversized telemetry payload, a refused
// source-gateway request, a rate limit hit, a failed admin sign-in — and the admin's own sign-ins, exports and
// deletions (an audit trail). Kept apart from usage statistics and from conversation records (one Durable Object each).
//
// Addresses (studio/docs/TELEMETRY.md, "IP 地址"):
//   who   an HMAC of the address (IPv6 by its /64) under a key that changes every month and is deleted after the
//         retention period, so repeated events of one source can be followed within a month and never after
//   net   the network prefix (IPv4 /24, IPv6 /48): coarse enough to name no one, enough to act on at the edge
//   ip    the full address, only when SECURITY_RAW_IP = "flagged" and only for a high-severity event (a scanner probe,
//         a failed admin sign-in), and gone with the event at the end of the retention period
// Nothing here ever holds what a visitor wrote; `detail` is a short label of the app's own (never a request body).

import { ipKey } from "../relay.js";
import { RowBudget } from "./budget.js";
import { shiftDay } from "./config.js";

const enc = new TextEncoder();
const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

export const SECURITY_KINDS = [
  "scanner", "forbidden_origin", "invalid_payload", "oversized", "rate_limited", "source_refused",
  "admin_auth_failed", "admin_locked", "admin_login", "admin_export", "admin_view", "admin_delete", "admin_prune",
];
const HIGH = new Set(["scanner", "admin_auth_failed", "admin_locked"]);
// the admin's own actions: each one stored (an audit trail), whatever the sampling or the budget; failed sign-ins are
// events like the others (counted, stored one a source every ten seconds), so a flood of them cannot push the admin's
// actions out of the audit list
const AUDIT = ["admin_login", "admin_view", "admin_export", "admin_delete", "admin_prune"];
// a source keeps being refused this long after this many failed admin attempts within the window
export const LOCK = { failures: 8, windowMs: 15 * 60000, lockMs: 15 * 60000 };
const THROTTLE_MS = 10000; // one stored event per source and kind every ten seconds (every one is still counted)

// ------------------------------------------------------------------------------------------------- the request

// scanners ask every host for these; nothing in Studio is at any of them
const HONEYPOT_PREFIXES = ["/wp-", "/wordpress", "/xmlrpc.php", "/.git", "/.svn", "/.hg", "/.env", "/.aws", "/.ssh", "/.ds_store", "/.vscode",
  "/phpmyadmin", "/pma/", "/myadmin", "/adminer", "/cgi-bin/", "/vendor/phpunit", "/actuator", "/boaform", "/hnap1", "/eval-stdin",
  "/phpinfo", "/server-status", "/owa/", "/autodiscover", "/solr/", "/manager/html", "/console/", "/_ignition", "/telescope", "/debug/",
  "/jenkins", "/api/jsonws", "/config.json", "/.circleci", "/docker-compose"];
const HONEYPOT_SUFFIXES = [".php", ".php7", ".phtml", ".asp", ".aspx", ".jsp", ".jspx", ".cgi", ".bak", ".sql", ".old", ".orig", ".swp",
  ".save", ".rar", ".7z", ".env", ".ini", ".log", ".pem", ".key", ".tar", ".tgz", ".zip.bak"];

/** A path no one asks this site for but a scanner (the site serves none of these kinds of file). */
export function isHoneypot(path) {
  const p = String(path || "").toLowerCase();
  return HONEYPOT_PREFIXES.some((x) => p.startsWith(x)) || HONEYPOT_SUFFIXES.some((x) => p.endsWith(x));
}

const SCANNER = /masscan|zgrab|zmap|nmap|nikto|sqlmap|nuclei|dirbuster|gobuster|feroxbuster|ffuf|wfuzz|wpscan|acunetix|nessus|openvas|censys|expanse|internetmeasurement|projectdiscovery|scanner/i;
const TOOL = /^curl\/|^wget\/|python-requests|python-urllib|python-httpx|aiohttp|go-http-client|^java\/|okhttp|apache-httpclient|node-fetch|undici|^axios\/|libwww|httpie|scrapy|postmanruntime|headlesschrome|phantomjs|selenium|puppeteer/i;
const BOT = /bot\b|spider|crawler|slurp|facebookexternalhit|bingpreview|gptbot|claudebot|perplexity/i;

/** empty | scanner | tool | bot | browser | other. */
export function uaClass(ua) {
  if (!ua) return "empty";
  if (SCANNER.test(ua)) return "scanner";
  if (BOT.test(ua)) return "bot";
  if (TOOL.test(ua)) return "tool";
  if (/^mozilla\/5\.0/i.test(ua)) return "browser";
  return "other";
}

/** The network an address is in: IPv4 /24, IPv6 /48 ("" when there is no address). */
export function netPrefix(ip) {
  const s = String(ip || "");
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(s)) return `${s.split(".").slice(0, 3).join(".")}.0/24`;
  if (s.includes(":")) {
    const key = ipKey(s); // a:b:c:d::/64
    return `${key.split(":").slice(0, 3).join(":")}::/48`;
  }
  return "";
}

/** What a security event keeps of a request, read at the edge (country from Cloudflare; the path without its query,
 * which may carry what a visitor typed). */
export function requestFacts(request) {
  const url = new URL(request.url);
  const cf = request.cf || {};
  const ip = request.headers.get("CF-Connecting-IP") || "";
  const country = String(cf.country || request.headers.get("CF-IPCountry") || "").toUpperCase();
  return {
    ip, country: /^[A-Z]{2}$/.test(country) && country !== "T1" ? country : "XX",
    asn: Number.isInteger(cf.asn) ? cf.asn : null, method: request.method, path: url.pathname.slice(0, 200),
    uaClass: uaClass(request.headers.get("User-Agent") || ""),
  };
}

// ------------------------------------------------------------------------------------------------- the store

export class SecurityCore {
  constructor(sql) {
    this.sql = sql;
    sql.exec(`CREATE TABLE IF NOT EXISTS sec_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
      kind TEXT NOT NULL, route TEXT NOT NULL, status INTEGER, who TEXT NOT NULL, net TEXT NOT NULL, ip TEXT, country TEXT NOT NULL,
      asn INTEGER, ua_class TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, detail TEXT NOT NULL)`);
    sql.exec("CREATE INDEX IF NOT EXISTS sec_events_day ON sec_events (day)");
    sql.exec("CREATE TABLE IF NOT EXISTS sec_counts (day TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, kind)) WITHOUT ROWID");
    sql.exec("CREATE TABLE IF NOT EXISTS sec_keys (month TEXT PRIMARY KEY, key TEXT NOT NULL) WITHOUT ROWID");
    sql.exec("CREATE TABLE IF NOT EXISTS sec_auth (who TEXT PRIMARY KEY, since INTEGER NOT NULL, n INTEGER NOT NULL, locked_until INTEGER NOT NULL) WITHOUT ROWID");
    // the admin sessions' epoch: every session is signed with it, and signing out moves it on, which ends them all
    sql.exec("CREATE TABLE IF NOT EXISTS sec_meta (k TEXT PRIMARY KEY, v INTEGER NOT NULL) WITHOUT ROWID");
    this.recent = new Map(); // `${who}|${kind}` → when an event of it was last stored
    this.keys = new Map();
    // the day's rows: every event is counted whatever the budget; one is stored in full only within it, but the
    // admin's own actions always are (an audit trail is not refused), and so is the sign-in state
    this.budget = new RowBudget(sql);
  }

  #wrote(n, now = Date.now()) {
    this.budget.charge(n, { now });
  }

  /** The month's key for `who` (random, made on first use, deleted with the month's events). */
  monthKey(month) {
    if (this.keys.has(month)) return this.keys.get(month);
    let row = this.sql.exec("SELECT key FROM sec_keys WHERE month = ?", month).toArray()[0];
    if (!row) {
      const key = hex(crypto.getRandomValues(new Uint8Array(32)));
      this.sql.exec("INSERT INTO sec_keys (month, key) VALUES (?, ?) ON CONFLICT(month) DO NOTHING", month, key);
      this.#wrote(1);
      row = this.sql.exec("SELECT key FROM sec_keys WHERE month = ?", month).toArray()[0];
    }
    this.keys.set(month, row.key);
    return row.key;
  }

  /** The source of a request as events name it: HMAC(month key, address by its /64), 16 hex characters. */
  async who(ip, now = Date.now()) {
    const month = new Date(now).toISOString().slice(0, 7);
    const key = await crypto.subtle.importKey("raw", enc.encode(this.monthKey(month)), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return hex(await crypto.subtle.sign("HMAC", key, enc.encode(ipKey(ip || "unknown")))).slice(0, 16);
  }

  /**
   * One event: {kind, route, status, detail, facts: requestFacts(), day}. Counted always; stored at most once per source
   * and kind every ten seconds (a scanner knocks thousands of times) and within the day's `budget` of rows (an admin's
   * action always), with the full address only for a high-severity event when `rawIp` is "flagged". → {stored, who}.
   */
  record(ev, opts = {}) {
    return this.#record(ev, opts);
  }

  async #record({ kind, route = "", status = null, detail = "", facts = {}, day }, { rawIp = "off", now = Date.now(), count = true, budget = 0 } = {}) {
    if (!SECURITY_KINDS.includes(kind) || !/^\d{4}-\d{2}-\d{2}$/.test(String(day))) return { stored: false };
    if (count) {
      this.sql.exec("INSERT INTO sec_counts (day, kind, n) VALUES (?, ?, 1) ON CONFLICT(day, kind) DO UPDATE SET n = n + 1", day, kind);
      this.#wrote(1, now);
    }
    const audit = AUDIT.includes(kind);
    if (!audit && !this.budget.check(budget, { now }).ok) return { stored: false };
    const who = await this.who(facts.ip, now);
    const k = `${who}|${kind}`;
    const last = this.recent.get(k) || 0;
    if (now - last < THROTTLE_MS && !audit) return { stored: false, who };
    this.recent.set(k, now);
    if (this.recent.size > 5000) this.recent.clear();
    const keepIp = rawIp === "flagged" && HIGH.has(kind) && facts.ip ? String(facts.ip).slice(0, 64) : null;
    this.sql.exec(`INSERT INTO sec_events (ts, day, kind, route, status, who, net, ip, country, asn, ua_class, method, path, detail)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    new Date(now).toISOString(), day, kind, String(route).slice(0, 40), Number.isInteger(status) ? status : null, who, netPrefix(facts.ip),
    keepIp, String(facts.country || "XX").slice(0, 2), Number.isInteger(facts.asn) ? facts.asn : null, String(facts.uaClass || "other").slice(0, 12),
    String(facts.method || "").slice(0, 8), String(facts.path || "").slice(0, 200), String(detail).slice(0, 80));
    this.#wrote(3, now); // the row, its sequence and its day index
    return { stored: true, who };
  }

  /**
   * What the Worker summed in memory between flushes (telemetry/buffer.js SecurityBuffer): {counts: [{day, kind, n}],
   * events} — the counts added as they are, the events stored as record() stores them (throttled) without being counted
   * a second time, each at the time it happened (`at`; the flush's own time when that is missing or not plausible).
   * → {stored}.
   */
  async recordBatch({ counts = [], events = [] } = {}, opts = {}) {
    const flushed = Number.isFinite(opts.now) ? opts.now : Date.now();
    for (const c of Array.isArray(counts) ? counts.slice(0, 200) : []) {
      const n = Math.floor(Number(c?.n));
      if (!SECURITY_KINDS.includes(c?.kind) || !/^\d{4}-\d{2}-\d{2}$/.test(String(c?.day)) || !(n > 0)) continue;
      this.sql.exec("INSERT INTO sec_counts (day, kind, n) VALUES (?, ?, ?) ON CONFLICT(day, kind) DO UPDATE SET n = n + excluded.n", c.day, c.kind, n);
      this.#wrote(1, flushed);
    }
    let stored = 0;
    for (const ev of Array.isArray(events) ? events.slice(0, 50) : []) {
      const at = Number(ev?.at);
      const now = Number.isFinite(at) && at <= flushed && at > flushed - 3600000 ? at : flushed;
      if ((await this.#record(ev, { ...opts, now, count: false })).stored) stored++;
    }
    return { stored };
  }

  /** Today's rows written by this object, and its budget (for the dashboard). */
  budgetState(budget = 0, now = Date.now()) {
    return this.budget.state(budget, now);
  }

  // ----------------------------------------------------------------------------------------------- admin sign-in

  /** Is this source refused now (too many failed admin attempts)? → {locked, retry (seconds), epoch (the sessions')}. */
  async authState(ip, now = Date.now()) {
    const who = await this.who(ip, now);
    const row = this.sql.exec("SELECT locked_until FROM sec_auth WHERE who = ?", who).toArray()[0];
    const until = Number(row?.locked_until) || 0;
    const epoch = this.sessionEpoch();
    return until > now ? { locked: true, retry: Math.ceil((until - now) / 1000), who, epoch } : { locked: false, who, epoch };
  }

  /** The epoch admin sessions are signed with now. */
  sessionEpoch() {
    if (this.epoch === undefined) this.epoch = Number(this.sql.exec("SELECT v FROM sec_meta WHERE k = 'session_epoch'").toArray()[0]?.v) || 0;
    return this.epoch;
  }

  /** Sign out everywhere: every session signed so far is no longer one. → {epoch}. */
  endSessions(now = Date.now()) {
    const epoch = this.sessionEpoch() + 1;
    this.sql.exec("INSERT INTO sec_meta (k, v) VALUES ('session_epoch', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", epoch);
    this.epoch = epoch;
    this.#wrote(1, now);
    return { epoch };
  }

  /** A failed attempt; the LOCK.failures-th within the window locks the source for LOCK.lockMs. → {locked, retry}. */
  async authFailed(ip, now = Date.now()) {
    const who = await this.who(ip, now);
    const row = this.sql.exec("SELECT since, n FROM sec_auth WHERE who = ?", who).toArray()[0];
    const fresh = !row || now - Number(row.since) > LOCK.windowMs;
    const n = fresh ? 1 : Number(row.n) + 1;
    const until = n >= LOCK.failures ? now + LOCK.lockMs : 0;
    this.sql.exec(`INSERT INTO sec_auth (who, since, n, locked_until) VALUES (?, ?, ?, ?)
      ON CONFLICT(who) DO UPDATE SET since = excluded.since, n = excluded.n, locked_until = excluded.locked_until`,
    who, fresh ? now : Number(row.since), n, until);
    this.#wrote(1, now);
    return until ? { locked: true, retry: Math.ceil(LOCK.lockMs / 1000), who } : { locked: false, who };
  }

  async authSucceeded(ip, now = Date.now()) {
    const who = await this.who(ip, now);
    this.#wrote(this.sql.exec("DELETE FROM sec_auth WHERE who = ? RETURNING who", who).toArray().length, now);
  }

  // ----------------------------------------------------------------------------------------------- reading

  /** Events by day and kind, by kind, and the sources with the most events (by their monthly key) from..to. */
  summary(from, to, budget = 0) {
    const daily = this.sql.exec("SELECT day, kind, n FROM sec_counts WHERE day >= ? AND day <= ? ORDER BY day", from, to).toArray()
      .map((r) => ({ day: r.day, kind: r.kind, n: Number(r.n) }));
    const byKind = {};
    for (const r of daily) byKind[r.kind] = (byKind[r.kind] || 0) + r.n;
    const actions = AUDIT.map(() => "?").join(", ");
    // sources: every event but the admin's own actions (failed sign-ins included: they are where an attack shows)
    const sources = this.sql.exec(`SELECT who, net, country, COUNT(*) AS n, GROUP_CONCAT(DISTINCT kind) AS kinds, MAX(ts) AS last,
      SUM(CASE WHEN ip IS NOT NULL THEN 1 ELSE 0 END) AS with_ip FROM sec_events WHERE day >= ? AND day <= ? AND kind NOT IN (${actions})
      GROUP BY who ORDER BY n DESC LIMIT 20`, from, to, ...AUDIT).toArray()
      .map((r) => ({ who: r.who, net: r.net, country: r.country, n: Number(r.n), kinds: String(r.kinds || "").split(",").filter(Boolean), last: r.last, with_ip: Number(r.with_ip) > 0 }));
    const audit = this.sql.exec(`SELECT ts, kind, detail, who, net FROM sec_events WHERE day >= ? AND day <= ? AND kind IN (${actions}) ORDER BY seq DESC LIMIT 50`, from, to, ...AUDIT)
      .toArray().map((r) => ({ ts: r.ts, kind: r.kind, detail: r.detail, who: r.who, net: r.net }));
    return { daily, by_kind: byKind, sources, audit, budget: this.budget.state(budget) };
  }

  /** One page of the stored events from..to (an authorized export), oldest first, after the event `after` (a seq).
   * → {rows, next}. */
  exportRows(from, to, { after = 0, limit = 1000 } = {}) {
    const n = Math.min(1000, Math.max(1, Math.floor(Number(limit)) || 1000));
    const rows = this.sql.exec(`SELECT seq, ts, day, kind, route, status, who, net, ip, country, asn, ua_class, method, path, detail
      FROM sec_events WHERE day >= ? AND day <= ? AND seq > ? ORDER BY seq LIMIT ?`, from, to, Math.max(0, Math.floor(Number(after)) || 0), n + 1).toArray();
    const page = rows.slice(0, n);
    return { rows: page, next: rows.length > n ? Number(page[page.length - 1].seq) : null };
  }

  /** Delete what is older than `days` days before `today` (events, counts, sign-in state, and the keys of months that
   * no longer have events, which unlinks every `who` made with them). → rows removed. */
  prune(today, days) {
    const cut = shiftDay(today, -days);
    const before = this.count();
    this.sql.exec("DELETE FROM sec_events WHERE day < ?", cut);
    this.sql.exec("DELETE FROM sec_counts WHERE day < ?", cut);
    this.sql.exec("DELETE FROM sec_auth WHERE since < ? AND locked_until < ?", Date.now() - LOCK.windowMs, Date.now());
    this.sql.exec("DELETE FROM sec_keys WHERE month < ?", cut.slice(0, 7));
    for (const m of [...this.keys.keys()]) if (m < cut.slice(0, 7)) this.keys.delete(m);
    return { removed: before - this.count() };
  }

  count() {
    return Number(this.sql.exec("SELECT COUNT(*) AS n FROM sec_events").one().n);
  }
}
