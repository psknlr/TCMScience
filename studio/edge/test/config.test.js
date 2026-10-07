// wrangler.toml says what the Worker is in production: these tests read it, so that the defaults the other tests run
// with are the deployed ones, and the settings the design depends on cannot drift unnoticed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { DEFAULTS, config } from "../src/relay.js";

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

const toml = parseToml(readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8"));

test("the relay's defaults are the deployed variables", () => {
  assert.deepEqual(Object.keys(DEFAULTS).filter((k) => !(k in toml.vars)), []);
  for (const [k, v] of Object.entries(DEFAULTS)) assert.equal(toml.vars[k], v, k);
  assert.deepEqual(Object.keys(toml.vars).filter((k) => !(k in DEFAULTS) && k !== "HSTS_MAX_AGE"), []);
  const c = config(toml.vars);
  assert.equal(c.publicModel, "Tao-S1");
  assert.deepEqual(c.upstreamFields, { reasoning_split: true });
  assert.deepEqual(c.origins, ["https://science.impf.ai", "http://127.0.0.1:8765", "http://localhost:8765"]);
  assert.equal(c.maxTokens, 8192);
  assert.equal(c.enabled, true);
  // sized for the agent: a question takes up to 16 model calls (CONTRACTS.md §6 maxSteps)
  assert.ok(c.chat.perMinute >= 16 && c.chat.perDay >= 16 * 50 && c.chat.total >= c.chat.perDay);
});

test("the Worker: one hostname, the app's files first, the relay under /v1/*, its bindings", () => {
  assert.equal(toml.name, "tcmscience-studio");
  assert.equal(toml.main, "src/index.js");
  assert.ok(toml.compatibility_flags.includes("enable_request_signal"));
  assert.match(toml.compatibility_date, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(toml.workers_dev, false);
  assert.equal(toml.preview_urls, false);
  assert.deepEqual(toml.routes, [{ pattern: "science.impf.ai", custom_domain: true }]);
  assert.deepEqual(toml.assets, { directory: "../_site", binding: "ASSETS", run_worker_first: ["/v1/*"], not_found_handling: "none" });
  assert.deepEqual(toml.ratelimits, [{ name: "BURST", namespace_id: "7321", simple: { limit: 40, period: 10 } }]);
  assert.ok(!["7311", "7312", "7313"].includes(toml.ratelimits[0].namespace_id)); // TaoChronos's: their counters would be shared
  assert.deepEqual(toml.durable_objects.bindings, [{ name: "LIMITER", class_name: "Limiter" }]);
  assert.deepEqual(toml.migrations, [{ tag: "v1", new_sqlite_classes: ["Limiter"] }]);
  assert.equal(toml.observability.enabled, true);
  assert.equal(toml.observability.logs.invocation_logs, false);
  assert.equal(String(toml.vars.HSTS_MAX_AGE), "15552000");
});

test("no secret is written in the configuration", () => {
  const text = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  assert.doesNotMatch(text, /MINIMAX_API_KEY\s*=|VISITOR_SALT\s*=|sk-[A-Za-z0-9]{8,}|eyJ[A-Za-z0-9_-]{20,}/);
});
