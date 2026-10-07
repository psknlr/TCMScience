#!/usr/bin/env node
// After a deployment: does science.impf.ai answer, with its headers, and does Tao-S1 work? (stdlib only; Node 22)
//   node scripts/check.mjs [--url https://science.impf.ai] [--origin <url>] [--wait 300] [--require-model]
// 1. GET /v1/health, retried while a new custom domain and its certificate come up (--wait seconds);
// 2. GET / is the app, with the headers from _headers (cross-origin isolation, the content policy);
// 3. one tiny non-streaming model call (max_tokens 8) with the page's Origin, unless the relay has no key — then a
//    warning, or a failure with --require-model.
// Prints one JSON line; exits non-zero with a reason when something is wrong.
import { pathToFileURL } from "node:url";

const UA = "tcmscience-deploy-check/1";

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
  if (!health.ok) {
    if (options.requireModel) throw new Error("the relay answers but Tao-S1 is off: no MINIMAX_API_KEY secret on the Worker, or RELAY = \"off\" in wrangler.toml");
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
      : e.type === "forbidden_origin" ? ` — ${origin} is not in ALLOWED_ORIGINS` : "";
    throw new Error(`the model call answered ${res.status} ${e.type || ""}: ${e.message || text.slice(0, 200)}${hint}`);
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
    else if (a === "--require-model") options.requireModel = true;
    else if (a === "-h" || a === "--help") options.help = true;
    else throw new Error(`unknown argument ${a}`);
  }
  return options;
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  try {
    const options = parse(process.argv.slice(2));
    if (options.help) {
      console.log("node scripts/check.mjs [--url https://science.impf.ai] [--origin URL] [--wait SECONDS] [--require-model]");
    } else {
      console.log(JSON.stringify(await check(options)));
    }
  } catch (err) {
    console.error(`check failed: ${err.message}`);
    process.exit(1);
  }
}
