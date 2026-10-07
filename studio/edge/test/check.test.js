// The deploy check (scripts/check.mjs) against the real relay handler, through an injected fetch.
import assert from "node:assert/strict";
import test from "node:test";

import { check } from "../scripts/check.mjs";
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
  await assert.rejects(check({}, io(site({ upstream: async () => jsonReply({ error: { message: "bad key" } }, 401) }))),
    /503 upstream_auth.*api\.minimax\.io/);
  await assert.rejects(check({ origin: "https://elsewhere.example" }, io(site())), /forbidden_origin.*ALLOWED_ORIGINS/);
});
