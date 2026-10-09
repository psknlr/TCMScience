#!/usr/bin/env node
// After a deployment: does science.impf.ai answer, with its headers, and does Tao-S1 work? (stdlib only; Node 22)
//   node scripts/check.mjs [--url https://science.impf.ai] [--origin <url>] [--wait 300] [--settle 120] [--require-model] [--require-sources]
//                          [--relay on|off]
// 1. GET /v1/health, retried while a new custom domain and its certificate come up (--wait seconds), and — when Tao-S1
//    is required — while a key put a moment ago reaches the edge (--settle seconds: health says ok:false until then);
// 2. GET / is the app, with the headers from _headers (cross-origin isolation, the content policy);
// 3. one tiny non-streaming model call (max_tokens 8) with the page's Origin, unless the relay has no key — then a
//    warning, or a failure with --require-model — or is paused: RELAY = "off" in wrangler.toml (read next to this
//    script unless --relay says otherwise) is the owner's choice, not a failure.
// When the model call fails and MINIMAX_API_KEY is in the environment (the deploy workflow passes it), the upstream is
// called once directly with the settings in wrangler.toml, and its own status, code and message (the key redacted) are
// added to the reason: the relay never shows them, by design, so this is where the owner learns what the upstream said.
// Prints one JSON line; exits non-zero with a reason when something is wrong.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { compileRegistry } from "../src/sources.js";

const UA = "tcmscience-deploy-check/1";
const OFF = /^(off|0|false|no)$/i; // as the relay reads RELAY (src/relay.js config)

/** Inspect only this deployment's health and generated registry, without consuming a
 * third-party API's quota. Counts must agree, and the registry must compile using the
 * gateway's real read-only template validation. */
export async function checkSources(url, fetcher = fetch) {
  const read = async (path, maxBytes) => {
    const response = await fetcher(`${url}${path}`, { headers: { "User-Agent": UA, Accept: "application/json" }, signal: AbortSignal.timeout(30000) });
    const type = response.headers.get("Content-Type") || "";
    if (response.status !== 200 || !type.toLowerCase().includes("json")) {
      await response.body?.cancel();
      throw new Error(`${url}${path} answered ${response.status} ${type}: rebuild and deploy the complete site and source gateway (SOURCES must be on)`);
    }
    const text = await response.text();
    if (Buffer.byteLength(text) > maxBytes) throw new Error(`${url}${path} exceeds the source registry size limit`);
    try { return JSON.parse(text); } catch { throw new Error(`${url}${path} is not valid JSON`); }
  };
  const health = await read("/api/sources/health", 65536);
  const whole = (value, min, max) => Number.isInteger(value) && value >= min && value <= max;
  if (health?.ok !== true || health.service !== "tcmscience-sources" || health.version !== 1
      || !whole(health.sources, 1, 2000) || !whole(health.operations, health.sources, 1000000)
      || !whole(health.max_response_bytes, 1024, 16777216) || !whole(health.timeout_ms, 1000, 30000)
      || !whole(health.limits?.perMinute, 1, 10000) || !whole(health.limits?.perDay, 1, 100000)
      || !whole(health.limits?.total, 1, 1000000)) {
    throw new Error(`${url}/api/sources/health is not a valid enabled source gateway health response`);
  }
  const manifest = await read("/runtime/source-gateway.json", 4194304);
  let registry;
  try { registry = compileRegistry(manifest); }
  catch (error) { throw new Error(`${url}/runtime/source-gateway.json has invalid read-only source definitions (${error.message})`); }
  if (registry.sources !== health.sources || registry.routes.length !== health.operations) {
    throw new Error(`${url}: source gateway health counts (${health.sources}/${health.operations}) differ from the deployed registry (${registry.sources}/${registry.routes.length}); redeploy the Worker and site together`);
  }
  return { status: "ok", sources: health.sources, operations: health.operations };
}

/** RELAY as a wrangler.toml sets it ("on" when the file does not say), or null when there is no such file. */
export function relayIn(text) {
  if (typeof text !== "string") return null;
  const m = /^\s*RELAY\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s#]+))/m.exec(text);
  return m ? (m[1] ?? m[2] ?? m[3]).trim() || "on" : "on";
}

/** A [vars] value as wrangler.toml sets it (a plain `NAME = "value"` line), or undefined. */
export function varIn(text, name) {
  if (typeof text !== "string") return undefined;
  const m = new RegExp(String.raw`^\s*${name}\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([^\s#]+))`, "m").exec(text);
  if (!m) return undefined;
  return m[1] !== undefined ? m[1].replace(/\\(["\\])/g, "$1") : (m[2] ?? m[3]);
}

/** The upstream's settings from wrangler.toml and the key, for the direct call (null without a key). */
export function upstreamFrom(text, key) {
  const k = String(key || "").replace(/\s+/g, "");
  if (!k) return null;
  let fields = {};
  try {
    const v = JSON.parse(varIn(text, "UPSTREAM_FIELDS") || "{}");
    if (v && typeof v === "object" && !Array.isArray(v)) fields = v;
  } catch { /* none */ }
  return {
    base: String(varIn(text, "UPSTREAM_BASE") || "https://api.minimax.cn/v1").replace(/\/+$/, ""),
    model: String(varIn(text, "MODELS") || "MiniMax-M3").split(",")[0].trim(),
    fields, key: k,
  };
}

const redact = (text, key) => {
  let out = String(text ?? "");
  if (key) out = out.split(key).join("[key]");
  return out.replace(/\b(?:sk|eyJ)[A-Za-z0-9._-]{8,}/g, "[redacted]").replace(/\s+/g, " ").trim().slice(0, 300);
};

/**
 * One tiny call to the upstream itself, as the relay makes it: what the upstream says, in its own words (a 200 with a
 * base_resp failure is MiniMax's way too). Never throws: the result is text for the owner's log.
 */
export async function diagnose(upstream, fetcher = fetch) {
  const where = `${upstream.base}/chat/completions with model ${upstream.model}`;
  let res;
  try {
    res = await fetcher(`${upstream.base}/chat/completions`, {
      method: "POST",
      headers: { "User-Agent": UA, "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${upstream.key}` },
      body: JSON.stringify({ model: upstream.model, ...upstream.fields, messages: [{ role: "user", content: "请只回复两个字：可以" }], max_completion_tokens: 16 }),
    });
  } catch (err) {
    return `calling ${where} directly failed: ${redact(err?.cause?.code || err?.message || err, upstream.key)}`;
  }
  const text = await res.text().catch(() => "");
  let j = null;
  try {
    j = JSON.parse(text);
  } catch { /* plain text */ }
  const base = j?.base_resp;
  if (res.ok && j && !j.error && !(base && base.status_code)) {
    return `calling ${where} directly worked (HTTP ${res.status}): the key and the model are fine, so the relay's request differs from this one`;
  }
  const parts = [`HTTP ${res.status}`];
  if (base && base.status_code) parts.push(`base_resp ${base.status_code}${base.status_msg ? ` "${redact(base.status_msg, upstream.key)}"` : ""}`);
  if (j?.error) {
    const e = typeof j.error === "object" ? j.error : { message: j.error };
    parts.push(`error ${[e.type, e.code].filter(Boolean).join("/")}${e.message ? ` "${redact(e.message, upstream.key)}"` : ""}`.trim());
  }
  if (parts.length === 1 && text) parts.push(`"${redact(text, upstream.key)}"`);
  return `calling ${where} directly: ${parts.join(", ")}`;
}

export async function check(options = {}, io = {}) {
  const url = String(options.url || "https://science.impf.ai").replace(/\/+$/, "");
  const origin = options.origin || url;
  const wait = Number(options.wait ?? 300);
  const fetcher = io.fetch || fetch;
  const sleep = io.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const log = io.log || ((line) => console.error(line));
  const now = io.now || (() => Date.now());

  // 1. health
  const end = now() + wait * 1000;
  let health = null;
  for (;;) {
    let said;
    try {
      const res = await fetcher(`${url}/v1/health`, { headers: { "User-Agent": UA, Accept: "application/json" } });
      said = `HTTP ${res.status}`;
      if (res.status === 200 && (res.headers.get("Content-Type") || "").includes("json")) {
        const j = await res.json();
        if (j && j.service === "tcmscience-studio") {
          health = j;
          break;
        }
        said = "an answer that is not this service's";
      }
    } catch (err) {
      said = err?.cause?.code || err?.message || String(err);
    }
    if (now() >= end) throw new Error(`${url}/v1/health does not answer (${said}) after ${wait} s: the custom domain or its certificate may still be coming up; run the workflow again in a few minutes`);
    log(`waiting for ${url}/v1/health (${said})`);
    await sleep(10000);
  }

  // a key put moments ago takes a little while to reach every location: the version before it says ok:false
  const paused = OFF.test(String(options.relay ?? "").trim());
  if (!health.ok && options.requireModel && !paused) {
    const settle = Number(options.settle ?? 120);
    const until = now() + settle * 1000;
    while (!health.ok && now() < until) {
      log(`waiting for Tao-S1's key to take effect at ${url} (health ok:false)`);
      await sleep(5000);
      try {
        const res = await fetcher(`${url}/v1/health`, { headers: { "User-Agent": UA, Accept: "application/json" } });
        if (res.status === 200 && (res.headers.get("Content-Type") || "").includes("json")) {
          const j = await res.json();
          if (j && j.service === "tcmscience-studio") health = j;
        }
      } catch { /* the next round */ }
    }
  }

  // 2. the app and its headers
  const page = await fetcher(`${url}/`, { headers: { "User-Agent": UA, Accept: "text/html" } });
  const type = page.headers.get("Content-Type") || "";
  if (page.status !== 200 || !type.startsWith("text/html")) throw new Error(`${url}/ answered ${page.status} ${type}: the app's files were not deployed`);
  const missing = [
    ["Cross-Origin-Opener-Policy", "same-origin"], ["Cross-Origin-Embedder-Policy", "require-corp"],
    ["X-Content-Type-Options", "nosniff"], ["Referrer-Policy", "strict-origin-when-cross-origin"],
    ["Content-Security-Policy-Report-Only", "script-src"],
  ].filter(([name, value]) => !(page.headers.get(name) || "").includes(value)).map(([name]) => name);
  if (missing.length) throw new Error(`${url}/ lacks ${missing.join(", ")}: _headers was not in the deployed site (copy studio/edge/_headers into studio/_site)`);
  await page.body?.cancel();

  const summary = { url, model: health.model, max_output_tokens: health.max_output_tokens, limits: health.limits, headers: "ok" };
  if (options.requireSources) summary.sources = await checkSources(url, fetcher);
  if (!health.ok) {
    if (paused) {
      log("Tao-S1 is paused (RELAY = \"off\" in wrangler.toml); the site works with visitors' own models");
      return { ...summary, call: "skipped (RELAY off)" };
    }
    if (options.requireModel) {
      const why = options.relay == null ? "no MINIMAX_API_KEY secret on the Worker, or RELAY = \"off\" in wrangler.toml" : "no MINIMAX_API_KEY secret on the Worker";
      throw new Error(`the relay answers but Tao-S1 is off: ${why}`);
    }
    log("warning: Tao-S1 is off (health ok:false); the site works with visitors' own models");
    return { ...summary, call: "skipped (Tao-S1 off)" };
  }

  // 3. one tiny model call, as the page makes it (model first)
  const res = await fetcher(`${url}/v1/chat/completions`, {
    method: "POST",
    headers: { "User-Agent": UA, "Content-Type": "application/json", Accept: "application/json", Origin: origin },
    body: JSON.stringify({ model: health.model, messages: [{ role: "user", content: "请只回复一个字：好" }], max_tokens: 8, stream: false }),
  });
  const text = await res.text();
  let reply = null;
  try {
    reply = JSON.parse(text);
  } catch { /* told below */ }
  if (res.status !== 200) {
    const e = reply?.error || {};
    const hint = e.type === "upstream_auth"
      ? " — the model service refused the key: a key from the international platform (platform.minimax.io) needs UPSTREAM_BASE = \"https://api.minimax.io/v1\" in studio/edge/wrangler.toml, a mainland key https://api.minimax.cn/v1; or the key is wrong"
      : e.type === "upstream_quota" ? " — the model service says this key's balance is used up: top up the MiniMax account the key belongs to, or put a key with balance in MINIMAX_API_KEY"
      : e.type === "forbidden_origin" ? ` — ${origin} is not in ALLOWED_ORIGINS` : "";
    const upstreamSays = options.upstream && /^upstream_|^content_/.test(String(e.type || ""))
      ? ` — ${await diagnose(options.upstream, fetcher)}` : "";
    throw new Error(`the model call answered ${res.status} ${e.type || ""}: ${e.message || text.slice(0, 200)}${hint}${upstreamSays}`);
  }
  if (!reply || !Array.isArray(reply.choices)) throw new Error(`the model call answered 200 but not a completion: ${text.slice(0, 200)}`);
  if (reply.model !== health.model) throw new Error(`the model call answered as ${JSON.stringify(reply.model)}, not ${health.model}`);
  if (/minimax/i.test(text)) throw new Error("the model call's answer names the upstream service: the relay's rename is broken");
  return { ...summary, call: "ok", usage: reply.usage || null };
}

function parse(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--url") options.url = argv[++i];
    else if (a === "--origin") options.origin = argv[++i];
    else if (a === "--wait") options.wait = Number(argv[++i]);
    else if (a === "--settle") options.settle = Number(argv[++i]);
    else if (a === "--require-model") options.requireModel = true;
    else if (a === "--require-sources") options.requireSources = true;
    else if (a === "--relay") options.relay = argv[++i];
    else if (a === "-h" || a === "--help") options.help = true;
    else throw new Error(`unknown argument ${a}`);
  }
  return options;
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  try {
    const options = parse(process.argv.slice(2));
    let text = null;
    try {
      text = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
    } catch { /* not beside the repository's wrangler.toml: RELAY unknown */ }
    if (options.relay === undefined) options.relay = relayIn(text);
    options.upstream = upstreamFrom(text, process.env.MINIMAX_API_KEY);
    if (options.help) {
      console.log("node scripts/check.mjs [--url https://science.impf.ai] [--origin URL] [--wait SECONDS] [--settle SECONDS] [--require-model] [--require-sources] [--relay on|off]");
    } else {
      console.log(JSON.stringify(await check(options)));
    }
  } catch (err) {
    console.error(`check failed: ${err.message}`);
    process.exit(1);
  }
}
