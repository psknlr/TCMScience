// wrangler.toml says what the Worker is in production: these tests read it. Its [vars] are the owner's to change
// (SETUP.md, README.md: the international key's UPSTREAM_BASE, RELAY = "off", the limits, the origins), so they are
// checked for form, not against the relay's built-in DEFAULTS; only what holds the design together is pinned.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { DEFAULTS, config } from "../src/relay.js";
import { SOURCE_DEFAULTS, sourceConfig } from "../src/sources.js";

/** The subset of TOML that wrangler.toml uses: [table], [[array of tables]], key = string | number | bool | array |
 * inline table, comments. Enough to read the file, not a general parser. */
function parseToml(text) {
  const root = {};
  let current = root;
  const value = (raw) => {
    const v = raw.trim();
    if (v.startsWith('"')) return JSON.parse(v);
    if (v.startsWith("'")) return v.slice(1, v.lastIndexOf("'"));
    if (v === "true" || v === "false") return v === "true";
    if (/^-?\d+$/.test(v)) return Number(v);
    if (v.startsWith("[")) return JSON.parse(v);
    if (v.startsWith("{")) {
      return Object.fromEntries(v.slice(1, -1).split(",").map((p) => p.split("=").map((s) => s.trim())).map(([k, x]) => [k, value(x)]));
    }
    throw new Error(`cannot read ${v}`);
  };
  for (const line of text.split("\n")) {
    const t = line.replace(/\s+#.*$/, "").trim();
    if (!t || t.startsWith("#")) continue;
    let m;
    if ((m = /^\[\[([\w.]+)\]\]$/.exec(t))) {
      const path = m[1].split(".");
      let at = root;
      for (const p of path.slice(0, -1)) at = at[p] ??= {};
      (at[path.at(-1)] ??= []).push((current = {}));
    } else if ((m = /^\[([\w.]+)\]$/.exec(t))) {
      current = root;
      for (const p of m[1].split(".")) current = current[p] ??= {};
    } else if ((m = /^([\w-]+)\s*=\s*(.+)$/.exec(t))) {
      current[m[1]] = value(m[2]);
    } else {
      throw new Error(`cannot read the line ${line}`);
    }
  }
  return root;
}

const TEXT = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
const toml = parseToml(TEXT);

/** wrangler.toml with one [vars] line rewritten, as the docs tell the owner to rewrite it. */
function edited(key, raw) {
  const line = new RegExp(`^${key} = .*$`, "m");
  assert.match(TEXT, line, `wrangler.toml has a ${key} line`);
  return parseToml(TEXT.replace(line, () => `${key} = ${raw}`)).vars;
}

const SITE = "https://science.impf.ai";
// whole numbers, with their least value (config() reads a smaller or mistyped one as its default, silently)
const NUMBERS = {
  MAX_OUTPUT_TOKENS: 1, MAX_BODY_BYTES: 1, PER_MINUTE: 0, PER_DAY: 0, TOTAL_PER_DAY: 0, TOKENS_PER_DAY: 0,
  TOTAL_TOKENS_PER_DAY: 0, HSTS_MAX_AGE: 0,
  SOURCE_MAX_BODY_BYTES: 1024, SOURCE_MAX_RESPONSE_BYTES: 1024, SOURCE_TIMEOUT_MS: 1000,
  SOURCE_PER_MINUTE: 1, SOURCE_PER_DAY: 1, SOURCE_TOTAL_PER_DAY: 1,
};
// a question takes up to 16 model calls (CONTRACTS.md §6 maxSteps): a call limit below that stops the agent halfway
const AGENT_CALLS = 16;

/** What is wrong with a [vars] table, one line per variable; [] when the Worker can be deployed with it. */
function problems(vars) {
  const out = [];
  const say = (k, why) => out.push(`${k}: ${why}`);
  const known = [...Object.keys(DEFAULTS), ...Object.keys(SOURCE_DEFAULTS), "HSTS_MAX_AGE"];
  for (const k of known) if (!(k in vars)) say(k, "missing from [vars]");
  for (const k of Object.keys(vars)) if (!known.includes(k)) say(k, "not a variable the Worker reads (a typo?)");
  const text = {};
  for (const k of known.filter((x) => x in vars)) {
    const v = vars[k];
    if (!["string", "number", "boolean"].includes(typeof v)) say(k, "must be a string");
    else if (String(v).trim() === "") say(k, "empty: write the value (an empty one falls back to the built-in default)");
    else text[k] = String(v).trim();
  }
  const has = (k) => k in text;
  if (has("RELAY") && !/^(on|off|true|false|yes|no|1|0)$/i.test(text.RELAY)) say("RELAY", `"${text.RELAY}" is neither "on" nor "off"`);
  if (has("SOURCES") && !/^(on|off|true|false|yes|no|1|0)$/i.test(text.SOURCES)) say("SOURCES", "must be on or off");
  for (const [key, maximum] of [["SOURCE_MAX_BODY_BYTES", 1048576], ["SOURCE_MAX_RESPONSE_BYTES", 16777216], ["SOURCE_TIMEOUT_MS", 30000], ["SOURCE_PER_MINUTE", 10000], ["SOURCE_PER_DAY", 100000], ["SOURCE_TOTAL_PER_DAY", 1000000]]) {
    if (has(key) && Number(text[key]) > maximum) say(key, `must be at most ${maximum}`);
  }
  if (has("UPSTREAM_BASE")) {
    let u = null;
    try {
      u = new URL(text.UPSTREAM_BASE);
    } catch { /* told below */ }
    if (!u || u.protocol !== "https:" || u.search || u.hash || /\/chat\/completions\/?$/.test(u.pathname)) {
      say("UPSTREAM_BASE", "must be the model service's https base URL, without /chat/completions (e.g. https://api.minimax.io/v1)");
    }
  }
  if (has("MODELS") && !text.MODELS.split(",").some((m) => m.trim())) say("MODELS", "names no model");
  if (has("PUBLIC_MODEL") && text.PUBLIC_MODEL !== "Tao-S1") say("PUBLIC_MODEL", "must be Tao-S1, the only model name a visitor sees");
  if (has("UPSTREAM_FIELDS")) {
    let f = null;
    try {
      f = JSON.parse(text.UPSTREAM_FIELDS);
    } catch { /* told below */ }
    if (!f || typeof f !== "object" || Array.isArray(f) || f.reasoning_split !== true) {
      say("UPSTREAM_FIELDS", 'must be a JSON object with "reasoning_split":true (the page reads the reasoning from its own field)');
    }
  }
  const whole = (k) => has(k) && Number.isInteger(Number(text[k])) && Number(text[k]) >= NUMBERS[k];
  for (const [k, min] of Object.entries(NUMBERS)) {
    if (has(k) && !whole(k)) say(k, `"${text[k]}" is not a whole number${min ? ` of at least ${min}` : " (0: no limit)"}`);
  }
  for (const k of ["PER_MINUTE", "PER_DAY", "TOTAL_PER_DAY"]) {
    const n = Number(text[k]);
    if (whole(k) && n !== 0 && n < AGENT_CALLS) say(k, `${n} model calls cannot finish one question (up to ${AGENT_CALLS}): use at least ${AGENT_CALLS}, or 0 for no limit`);
  }
  if (has("ALLOWED_ORIGINS")) {
    const origins = text.ALLOWED_ORIGINS.split(",").map((s) => s.trim()).filter(Boolean);
    const exact = (o) => {
      try {
        return new URL(o).origin === o;
      } catch {
        return false;
      }
    };
    for (const o of origins.filter((x) => !exact(x))) say("ALLOWED_ORIGINS", `"${o}" is not an origin (scheme://host[:port], no path or trailing slash): no page would match it`);
    if (!origins.includes(SITE)) say("ALLOWED_ORIGINS", `must include ${SITE}, the site itself`);
  }
  return out;
}

test("wrangler.toml's [vars] are the variables the relay reads, each well formed", () => {
  assert.deepEqual(problems(toml.vars), []);
  const c = config(toml.vars); // read as written, not fallen back to a default
  assert.equal(c.publicModel, "Tao-S1");
  assert.deepEqual(c.upstreamFields, JSON.parse(toml.vars.UPSTREAM_FIELDS));
  assert.ok(c.origins.includes(SITE));
  for (const [k, read] of [["PER_MINUTE", c.chat.perMinute], ["PER_DAY", c.chat.perDay], ["TOTAL_PER_DAY", c.chat.total],
    ["TOKENS_PER_DAY", c.chat.tokensPerDay], ["TOTAL_TOKENS_PER_DAY", c.chat.totalTokens], ["MAX_OUTPUT_TOKENS", c.maxTokens]]) {
    assert.equal(read, Number(toml.vars[k]), k);
  }
});

test("every [vars] change the docs tell the owner to make passes, and takes effect", () => {
  const changes = [
    // SETUP.md step 6 and the troubleshooting row: a key from the international platform
    ["UPSTREAM_BASE", '"https://api.minimax.io/v1"', (c) => assert.equal(c.upstream, "https://api.minimax.io/v1")],
    // the persistent pause (SETUP.md 日常维护, README 停用开关), and back
    ["RELAY", '"off"', (c) => assert.equal(c.enabled, false)],
    ["RELAY", '"on"', (c) => assert.equal(c.enabled, true)],
    // the limits, up, down to what one question needs, and off (0: no limit), quoted or not
    ["PER_DAY", '"3000"', (c) => assert.equal(c.chat.perDay, 3000)],
    ["PER_DAY", "300", (c) => assert.equal(c.chat.perDay, 300)],
    ["PER_MINUTE", `"${AGENT_CALLS}"`, (c) => assert.equal(c.chat.perMinute, AGENT_CALLS)],
    ["PER_MINUTE", '"0"', (c) => assert.equal(c.chat.perMinute, 0)],
    ["TOTAL_PER_DAY", '"0"', (c) => assert.equal(c.chat.total, 0)],
    ["TOTAL_PER_DAY", '"500"', (c) => assert.equal(c.chat.total, 500)],
    ["TOKENS_PER_DAY", '"0"', (c) => assert.equal(c.chat.tokensPerDay, 0)],
    ["TOTAL_TOKENS_PER_DAY", '"50000000"', (c) => assert.equal(c.chat.totalTokens, 50000000)],
    ["TOKENS_PER_DAY", '"2e6"', (c) => assert.equal(c.chat.tokensPerDay, 2000000)],
    ["MAX_OUTPUT_TOKENS", '"4096"', (c) => assert.equal(c.maxTokens, 4096)],
    // the model, and the pages that may call the relay
    ["MODELS", '"MiniMax-M3-pro,MiniMax-M3"', (c) => assert.equal(c.model, "MiniMax-M3-pro")],
    ["ALLOWED_ORIGINS", `"${SITE},http://127.0.0.1:9000"`, (c) => assert.deepEqual(c.origins, [SITE, "http://127.0.0.1:9000"])],
    ["HSTS_MAX_AGE", '"31536000"', () => {}],
  ];
  // only what is said of the changed variable: a mistake elsewhere in the file is the first test's to report
  const about = (key, vars) => problems(vars).filter((p) => p.startsWith(`${key}: `));
  for (const [key, raw, effect] of changes) {
    const vars = edited(key, raw);
    assert.deepEqual(about(key, vars), [], `${key} = ${raw}`);
    effect(config(vars));
  }
});

test("a mistake in [vars] is named, with the variable", () => {
  const mistakes = [
    ["RELAY", '"of"'], ["RELAY", '""'],
    ["UPSTREAM_BASE", '"http://api.minimax.io/v1"'], ["UPSTREAM_BASE", '"api.minimax.io/v1"'],
    ["UPSTREAM_BASE", '"https://api.minimax.io/v1/chat/completions"'],
    ["MODELS", '" , "'], ["PUBLIC_MODEL", '"MiniMax-M3"'], ["UPSTREAM_FIELDS", "'{}'"], ["UPSTREAM_FIELDS", "'reasoning_split'"],
    ["PER_DAY", '"lots"'], ["PER_DAY", '"-1"'], ["PER_DAY", '"1.5"'], ["TOKENS_PER_DAY", '"10M"'],
    ["PER_MINUTE", '"10"'], ["PER_DAY", '"8"'], ["TOTAL_PER_DAY", '"15"'], ["MAX_OUTPUT_TOKENS", '"0"'], ["HSTS_MAX_AGE", '"half a year"'],
    ["ALLOWED_ORIGINS", '"http://127.0.0.1:8765"'], ["ALLOWED_ORIGINS", `"${SITE}/,http://localhost:8765"`],
    ["ALLOWED_ORIGINS", `"${SITE},localhost:8765"`],
  ];
  for (const [key, raw] of mistakes) {
    assert.ok(problems(edited(key, raw)).some((p) => p.startsWith(`${key}: `)), `${key} = ${raw} is not reported`);
  }
  const { PER_DAY, ...missing } = toml.vars;
  assert.ok(problems(missing).includes("PER_DAY: missing from [vars]"));
  assert.match(problems({ ...toml.vars, PER_DAYS: "3000" }).join("\n"), /^PER_DAYS: not a variable the Worker reads/m);
  assert.match(problems({ ...toml.vars, ALLOWED_ORIGINS: [SITE] }).join("\n"), /^ALLOWED_ORIGINS: must be a string/m);
});

test("the Worker: one hostname, the app's files first, the relay under /v1/*, its bindings", () => {
  assert.equal(toml.name, "tcmscience-studio");
  assert.equal(toml.main, "src/index.js");
  assert.ok(toml.compatibility_flags.includes("enable_request_signal"));
  assert.match(toml.compatibility_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(toml.workers_dev, false);
  assert.equal(toml.preview_urls, false);
  assert.deepEqual(toml.routes, [{ pattern: "science.impf.ai", custom_domain: true }]);
  assert.deepEqual(toml.assets, { directory: "../_site", binding: "ASSETS", run_worker_first: ["/v1/*", "/api/sources/*"], not_found_handling: "none" });
  assert.deepEqual(toml.ratelimits, [{ name: "BURST", namespace_id: "7321", simple: { limit: 40, period: 10 } }]);
  assert.ok(!["7311", "7312", "7313"].includes(toml.ratelimits[0].namespace_id)); // TaoChronos's: their counters would be shared
  assert.deepEqual(toml.durable_objects.bindings, [{ name: "LIMITER", class_name: "Limiter" }]);
  assert.deepEqual(toml.migrations, [{ tag: "v1", new_sqlite_classes: ["Limiter"] }]);
  assert.equal(toml.observability.enabled, true);
  assert.equal(toml.observability.logs.invocation_logs, false);
});

test("no secret is written in the configuration", () => {
  const text = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  assert.doesNotMatch(text, /MINIMAX_API_KEY\s*=|VISITOR_SALT\s*=|sk-[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{20,}/);
});
