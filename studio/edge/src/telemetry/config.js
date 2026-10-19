// The telemetry's variables (wrangler.toml [vars]) and secrets, read the way the relay reads its own: an unset, empty or
// mistyped value falls back to the default here, and a number outside its range never lifts a limit or a retention
// period (test/config.test.js checks that wrangler.toml sets each one, well formed).
//
// Secrets (never in wrangler.toml, the repository or the site): ADMIN_TOKEN (the dashboard and every admin endpoint) and
// EXPORT_TOKEN (optional, read-only: the summary and aggregated exports, for a collector). Each must be at least 32
// characters; a shorter one counts as unset, so a mistaken value never opens the admin endpoints.

import { CONSENT_VERSION } from "../../../web/js/core/telemetry-contract.js";

export const TELEMETRY_DEFAULTS = {
  TELEMETRY: "on", // usage statistics (off: none are taken; health says analytics:false)
  TELEMETRY_CONVERSATIONS: "on", // conversation records of visitors who agreed (off: none are taken)
  // country | off: where a request comes from, as Cloudflare places it. Nothing finer: the notice visitors read says
  // "country or region" (a territory), and a setting must never collect more than the notice discloses
  TELEMETRY_GEO: "country",
  TELEMETRY_UTC_OFFSET: "8", // the reporting day, hours from UTC (8: Beijing)
  TELEMETRY_MAX_BODY_BYTES: "32768", // one batch of usage events
  CONVERSATION_MAX_BODY_BYTES: "65536", // one conversation record
  TELEMETRY_PER_MINUTE: "30", // batches of usage events per visitor (a page sends one every 15 s at most while busy)
  TELEMETRY_PER_DAY: "1500",
  CONVERSATIONS_PER_MINUTE: "20", // conversation records per visitor
  CONVERSATIONS_PER_DAY: "500",
  RETENTION_EVENTS_DAYS: "90", // per-turn and per-tool rows (no text)
  RETENTION_AGGREGATES_DAYS: "400", // daily counts
  RETENTION_CONVERSATIONS_DAYS: "90", // conversation records
  RETENTION_SECURITY_DAYS: "30", // security events
  CONVERSATION_MAX_RECORDS: "50000", // beyond this many, the oldest records go first
  SECURITY_RAW_IP: "off", // off | flagged: keep the full address of a high-severity security event (else a network prefix)
  ADMIN_SESSION_SECONDS: "3600", // how long a dashboard sign-in lasts
  // rows the telemetry may write a day, all three objects together (0: no budget, on a paid plan): 60 % usage, 25 %
  // conversation records, 15 % security events. On the Workers Free plan the account has 100,000 a day for every
  // Durable Object, the relay's and the gateway's limiter included, which refuses service once they are spent
  // (telemetry/budget.js)
  TELEMETRY_DAILY_ROWS: "40000",
};

const RANGES = {
  TELEMETRY_UTC_OFFSET: [-14, 14], TELEMETRY_MAX_BODY_BYTES: [1024, 1048576], CONVERSATION_MAX_BODY_BYTES: [1024, 1048576],
  TELEMETRY_PER_MINUTE: [1, 100000], TELEMETRY_PER_DAY: [1, 10000000], CONVERSATIONS_PER_MINUTE: [1, 100000],
  CONVERSATIONS_PER_DAY: [1, 10000000], RETENTION_EVENTS_DAYS: [1, 3650], RETENTION_AGGREGATES_DAYS: [1, 3650],
  RETENTION_CONVERSATIONS_DAYS: [1, 3650], RETENTION_SECURITY_DAYS: [1, 3650], CONVERSATION_MAX_RECORDS: [100, 10000000],
  ADMIN_SESSION_SECONDS: [300, 86400], TELEMETRY_DAILY_ROWS: [0, 1000000000],
};
export const TELEMETRY_RANGES = RANGES;
const OFF = /^(off|0|false|no)$/i;
export const MIN_SECRET = 32;

export function telemetryConfig(env = {}) {
  const get = (k) => (env[k] !== undefined && env[k] !== null && String(env[k]).trim() !== "" ? String(env[k]).trim() : TELEMETRY_DEFAULTS[k]);
  const num = (k) => {
    const v = Number(get(k));
    const [min, max] = RANGES[k];
    return Number.isInteger(v) && v >= min && v <= max ? v : Number(TELEMETRY_DEFAULTS[k]);
  };
  const geo = get("TELEMETRY_GEO").toLowerCase();
  const secret = (k) => {
    const v = typeof env[k] === "string" ? env[k].replace(/\s+/g, "") : "";
    return v.length >= MIN_SECRET ? v : "";
  };
  return {
    analytics: !OFF.test(get("TELEMETRY")),
    conversations: !OFF.test(get("TELEMETRY_CONVERSATIONS")),
    geo: geo === "off" ? "off" : "country",
    offset: num("TELEMETRY_UTC_OFFSET"),
    maxEventBytes: num("TELEMETRY_MAX_BODY_BYTES"),
    maxRecordBytes: num("CONVERSATION_MAX_BODY_BYTES"),
    eventLimits: { perMinute: num("TELEMETRY_PER_MINUTE"), perDay: num("TELEMETRY_PER_DAY") },
    recordLimits: { perMinute: num("CONVERSATIONS_PER_MINUTE"), perDay: num("CONVERSATIONS_PER_DAY") },
    retention: {
      events: num("RETENTION_EVENTS_DAYS"), aggregates: num("RETENTION_AGGREGATES_DAYS"),
      conversations: num("RETENTION_CONVERSATIONS_DAYS"), security: num("RETENTION_SECURITY_DAYS"),
    },
    maxRecords: num("CONVERSATION_MAX_RECORDS"),
    rawIp: get("SECURITY_RAW_IP").toLowerCase() === "flagged" ? "flagged" : "off",
    sessionSeconds: num("ADMIN_SESSION_SECONDS"),
    budgets: budgetsOf(num("TELEMETRY_DAILY_ROWS")),
    adminToken: secret("ADMIN_TOKEN"),
    exportToken: secret("EXPORT_TOKEN"),
    consentVersion: CONSENT_VERSION,
  };
}

/** Each object's rows a day, from the telemetry's whole budget (0: none). */
export function budgetsOf(total) {
  if (!(total > 0)) return { usage: 0, conversations: 0, security: 0 };
  return { usage: Math.floor(total * 0.6), conversations: Math.floor(total * 0.25), security: Math.floor(total * 0.15) };
}

/** What a page may know of the telemetry (in /v1/health and /api/telemetry/health): whether each kind is taken, the
 * notice's version, how coarse a place is, how long things are kept. Nothing about the admin side. */
export function telemetryPublic(env = {}) {
  const cfg = telemetryConfig(env);
  return {
    analytics: cfg.analytics && Boolean(env.USAGE),
    conversations: cfg.conversations && Boolean(env.CONVERSATIONS),
    consent_version: cfg.consentVersion,
    geo: cfg.geo,
    retention_days: {
      conversations: cfg.retention.conversations, events: cfg.retention.events, aggregates: cfg.retention.aggregates, security: cfg.retention.security,
    },
  };
}

/** The reporting day of a moment: `offset` hours from UTC, as YYYY-MM-DD. */
export function reportDay(now, offset = 8) {
  return new Date(now + offset * 3600000).toISOString().slice(0, 10);
}

/** YYYY-MM-DD plus `n` days. */
export function shiftDay(day, n) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** The days from `from` to `to`, both included (at most 400). */
export function daysBetween(from, to) {
  const out = [];
  for (let d = from; d <= to && out.length < 400; d = shiftDay(d, 1)) out.push(d);
  return out;
}
