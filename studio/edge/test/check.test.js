// The deploy check (scripts/check.mjs) against the real relay handler, through an injected fetch.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { check, relayIn } from "../scripts/check.mjs";
import { CSP_REPORT_ONLY, secure } from "../src/site.js";
import { jsonReply, relay } from "./helpers.js";

const HTML = "<!doctype html><title>TCMScience Studio</title>";

/** A fake science.impf.ai: /v1/* is the relay (with a fake upstream), / is the app with `pageHeaders`. */
function site({ env = {}, upstream, pageHeaders = true, downFor = 0 } = {}) {
  const r = relay({ env, upstream: upstream || (async () => jsonReply({ id: "c", model: "MiniMax-M3", choices: [{ index: 0, message: { content: "好" } }], usage: { prompt_tokens: 9, completion_tokens: 1 } })) });
  let failures = downFor;
  const seen = [];
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    seen.push([init.method || "GET", u.pathname, init.headers?.Origin || null]);
    if (failures-- > 0) throw new TypeError("fetch failed");
    if (u.pathname.startsWith("/v1/")) {
      const path = u.pathname;
      return secure(await r.call(path, init));
    }
    const page = new Response(HTML, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    return pageHeaders ? secure(page) : page;
  };
  return { fetch, seen, r };
}

const io = (s, logs = []) => ({ fetch: s.fetch, sleep: async () => {}, log: (l) => logs.push(l) });

test("a working deployment: health, the app's headers, one tiny call as the page makes it", async () => {
  const s = site();
  const out = await check({ url: "https://science.impf.ai/", requireModel: true }, io(s));
  assert.equal(out.call, "ok");
  assert.equal(out.model, "Tao-S1");
  assert.equal(out.headers, "ok");
  assert.deepEqual(out.usage, { prompt_tokens: 9, completion_tokens: 1 });
  assert.deepEqual(s.seen.map(([m, p]) => `${m} ${p}`), ["GET /v1/health", "GET /", "POST /v1/chat/completions"]);
  assert.equal(s.seen[2][2], "https://science.impf.ai");
  const [sent] = s.r.calls;
  assert.equal(sent.body.max_tokens, 8);
  assert.ok(sent.init.body.startsWith('{"model":"MiniMax-M3"')); // the page's shape: model first
  assert.ok(CSP_REPORT_ONLY.includes("script-src"));
});

test("a new domain: health is retried until it answers; never answering is a failure that says why", async () => {
  const logs = [];
  const out = await check({ url: "https://science.impf.ai", wait: 300 }, io(site({ downFor: 3 }), logs));
  assert.equal(out.call, "ok");
  assert.equal(logs.length, 3);
  let t = 0;
  const never = site({ downFor: 1e9 });
  await assert.rejects(check({ url: "https://science.impf.ai", wait: 30 }, { fetch: never.fetch, sleep: async (ms) => { t += ms; }, log: () => {}, now: () => t }),
    /does not answer .* after 30 s/);
});

test("what goes wrong is named: no _headers, no key, a refused key, a wrong origin", async () => {
  await assert.rejects(check({}, io(site({ pageHeaders: false }))), /lacks Cross-Origin-Opener-Policy.*_headers/);
  const off = await check({}, io(site({ env: { MINIMAX_API_KEY: "" } })));
  assert.equal(off.call, "skipped (Tao-S1 off)");
  await assert.rejects(check({ requireModel: true }, io(site({ env: { MINIMAX_API_KEY: "" } }))), /Tao-S1 is off/);
  // RELAY on in wrangler.toml: the missing key is the only reason left
  await assert.rejects(check({ requireModel: true, relay: "on" }, io(site({ env: { MINIMAX_API_KEY: "" } }))),
    (err) => /Tao-S1 is off: no MINIMAX_API_KEY secret on the Worker$/.test(err.message));
  await assert.rejects(check({}, io(site({ upstream: async () => jsonReply({ error: { message: "bad key" } }, 401) }))),
    /503 upstream_auth.*api\.minimax\.io/);
  await assert.rejects(check({ origin: "https://elsewhere.example" }, io(site())), /forbidden_origin.*ALLOWED_ORIGINS/);
});

test("a pause committed in wrangler.toml (RELAY = \"off\") is the owner's choice: the check passes without a call", async () => {
  const logs = [];
  const s = site({ env: { RELAY: "off" } }); // the key is there: only the pause turns Tao-S1 off
  const out = await check({ requireModel: true, relay: "off" }, io(s, logs));
  assert.equal(out.call, "skipped (RELAY off)");
  assert.deepEqual(s.seen.map(([m, p]) => `${m} ${p}`), ["GET /v1/health", "GET /"]);
  assert.match(logs.join("\n"), /paused \(RELAY = "off" in wrangler\.toml\)/);
  // its headers are still checked
  await assert.rejects(check({ requireModel: true, relay: "off" }, io(site({ env: { RELAY: "off" }, pageHeaders: false }))), /_headers/);
  // RELAY on, but the Worker answers ok:false: a failure (no key), not a pause
  await assert.rejects(check({ requireModel: true, relay: "on" }, io(site({ env: { RELAY: "off" } }))), /Tao-S1 is off/);
});

test("RELAY is read from the repository's wrangler.toml, from its [vars] line and not a comment", () => {
  const text = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  const line = /^RELAY = .*$/m;
  assert.match(text, line);
  assert.equal(relayIn(text.replace(line, 'RELAY = "off"')), "off");
  assert.equal(relayIn(text.replace(line, 'RELAY = "on"  # back on')), "on");
  assert.equal(relayIn(text.replace(line, "RELAY = 'off'")), "off");
  assert.equal(relayIn(text.replace(line, "RELAY = false")), "false");
  assert.equal(relayIn(text.replace(line, "")), "on"); // unset: the relay's default
  assert.equal(relayIn(null), null);
});

test("the command line: the pause in wrangler.toml, or --relay, decides whether ok:false fails the check", async (t) => {
  const page = secure(new Response("", { headers: { "Content-Type": "text/html; charset=utf-8" } }));
  const server = createServer((req, res) => {
    if (req.url === "/v1/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: false, service: "tcmscience-studio", version: "1", model: "Tao-S1", models: ["Tao-S1"] }));
      return;
    }
    res.writeHead(200, Object.fromEntries(page.headers));
    res.end(HTML);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(() => server.close());
  const url = `http://127.0.0.1:${server.address().port}`;
  const script = fileURLToPath(new URL("../scripts/check.mjs", import.meta.url));
  const run = (...args) => new Promise((resolve) => {
    execFile(process.execPath, [script, "--url", url, "--wait", "0", "--require-model", ...args], (err, stdout, stderr) => resolve({ code: err ? err.code : 0, stdout, stderr }));
  });
  const paused = await run("--relay", "off");
  assert.equal(paused.code, 0, paused.stderr);
  assert.equal(JSON.parse(paused.stdout).call, "skipped (RELAY off)");
  const on = await run("--relay", "on");
  assert.equal(on.code, 1);
  assert.match(on.stderr, /check failed: the relay answers but Tao-S1 is off: no MINIMAX_API_KEY secret on the Worker/);
  // without --relay, the repository's wrangler.toml decides
  const text = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  const fromToml = await run();
  assert.equal(fromToml.code, /^(off|0|false|no)$/i.test(relayIn(text)) ? 0 : 1, fromToml.stderr);
});
