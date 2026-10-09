// The deploy check (scripts/check.mjs) against the real relay handler, through an injected fetch.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { check, checkPublished, diagnose, relayIn, upstreamFrom, varIn } from "../scripts/check.mjs";
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

// a fake clock: sleeping advances it, so waits (the new domain, the key taking effect) cost no real time
const io = (s, logs = []) => {
  let t = 0;
  return { fetch: s.fetch, sleep: async (ms) => { t += ms; }, log: (l) => logs.push(l), now: () => t };
};

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
  await assert.rejects(check({}, io(site({ upstream: async () => jsonReply({ error: { type: "insufficient_balance_error", message: "insufficient balance (1008)" } }, 402) }))),
    /503 upstream_quota.*balance is used up: top up the MiniMax account/);
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
    execFile(process.execPath, [script, "--url", url, "--wait", "0", "--settle", "0", "--require-model", ...args], (err, stdout, stderr) => resolve({ code: err ? err.code : 0, stdout, stderr }));
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

test("a key put a moment ago: health ok:false is waited out (--settle), then the call is made", async () => {
  const s = site();
  let stale = 3; // the edge still serves the version from before the secret
  const fetch = async (url, init) => {
    if (new URL(url).pathname === "/v1/health" && stale-- > 0) {
      return new Response(JSON.stringify({ ok: false, service: "tcmscience-studio", model: "Tao-S1" }), { headers: { "Content-Type": "application/json" } });
    }
    return s.fetch(url, init);
  };
  const logs = [];
  let clock = 0;
  const out = await check({ requireModel: true, relay: "on" }, { fetch, sleep: async (ms) => { clock += ms; }, log: (l) => logs.push(l), now: () => clock });
  assert.equal(out.call, "ok");
  assert.match(logs.join("\n"), /waiting for Tao-S1's key to take effect/);
  // never taking effect within --settle: the same failure as before, after waiting
  let t = 0;
  const never = site({ env: { MINIMAX_API_KEY: "" } });
  await assert.rejects(check({ requireModel: true, relay: "on", settle: 30 }, { fetch: never.fetch, sleep: async (ms) => { t += ms; }, log: () => {}, now: () => t }),
    /Tao-S1 is off: no MINIMAX_API_KEY secret on the Worker$/);
  assert.ok(t >= 30000);
});

const KEY = "sk-cp-secret0123456789";
const UPSTREAM = { base: "https://api.minimax.cn/v1", model: "MiniMax-M3", fields: { reasoning_split: true }, key: KEY };

test("a refused call: the upstream is asked directly and its own words are in the reason, the key redacted", async () => {
  const refuse = async () => jsonReply({ base_resp: { status_code: 2013, status_msg: `invalid params, unknown model for key ${KEY}` } });
  const s = site({ upstream: refuse });
  const direct = [];
  const fetch = async (url, init) => {
    if (new URL(url).host === "api.minimax.cn") {
      direct.push(JSON.parse(init.body));
      assert.equal(init.headers.Authorization, `Bearer ${KEY}`);
      return refuse();
    }
    return s.fetch(url, init);
  };
  let clock = 0;
  const err = await check({ requireModel: true, upstream: UPSTREAM }, { fetch, sleep: async (ms) => { clock += ms; }, log: () => {}, now: () => clock }).then(() => null, (e) => e);
  assert.ok(err);
  assert.match(err.message, /400 upstream_rejected/);
  assert.match(err.message, /calling https:\/\/api\.minimax\.cn\/v1\/chat\/completions with model MiniMax-M3 directly: HTTP 200, base_resp 2013 "invalid params, unknown model for key \[key\]"/);
  assert.ok(!err.message.includes(KEY), "the key is never printed");
  assert.equal(direct.length, 1);
  assert.equal(direct[0].model, "MiniMax-M3");
  assert.equal(direct[0].reasoning_split, true);
  // without a key in the environment, nothing is called directly
  const s2 = site({ upstream: refuse });
  const err2 = await check({ requireModel: true }, io(s2)).then(() => null, (e) => e);
  assert.ok(!/directly/.test(err2.message));
});

test("diagnose says what the upstream answered, whatever its shape, and never throws", async () => {
  const reply = (body, status = 200) => async () => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  assert.match(await diagnose(UPSTREAM, reply({ error: { type: "invalid_request_error", code: "model_not_found", message: "no such model" } }, 404)),
    /HTTP 404, error invalid_request_error\/model_not_found "no such model"/);
  assert.match(await diagnose(UPSTREAM, reply("Bad Request", 400)), /HTTP 400, "Bad Request"/);
  assert.match(await diagnose(UPSTREAM, reply({ id: "x", choices: [{ message: { content: "可以" } }] })), /directly worked \(HTTP 200\): the key and the model are fine/);
  assert.match(await diagnose(UPSTREAM, async () => { throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ENOTFOUND" } }); }), /directly failed: ENOTFOUND/);
  assert.match(await diagnose(UPSTREAM, reply({ base_resp: { status_code: 1004, status_msg: "login fail: Please carry the API secret key sk-abcdefgh12345 " } })),
    /base_resp 1004 "login fail: Please carry the API secret key \[redacted\]"/);
});

test("the upstream settings for the direct call are read from wrangler.toml, and only with a key", () => {
  const text = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
  const u = upstreamFrom(text, ` ${KEY}\n`);
  assert.equal(u.key, KEY);
  assert.equal(u.base, varIn(text, "UPSTREAM_BASE").replace(/\/+$/, ""));
  assert.equal(u.model, varIn(text, "MODELS").split(",")[0].trim());
  assert.deepEqual(u.fields, JSON.parse(varIn(text, "UPSTREAM_FIELDS")));
  assert.equal(upstreamFrom(text, ""), null);
  assert.equal(upstreamFrom(text, undefined), null);
  assert.equal(varIn('UPSTREAM_FIELDS = "{\\"a\\":1}"', "UPSTREAM_FIELDS"), '{"a":1}');
  assert.equal(varIn("X = 5", "Y"), undefined);
});

// ---------------------------------------------------------------------------------- v2: what the site publishes


/** A fake site serving `files` ({path: string | Uint8Array | {body, headers}}); anything else is 404. */
function published(files) {
  return async (url) => {
    const path = new URL(url).pathname.replace(/^\//, "");
    const f = files[path];
    if (f === undefined) return new Response("not found", { status: 404 });
    const body = f?.body ?? f;
    return new Response(typeof body === "string" ? body : body, { status: 200, headers: f?.headers || {} });
  };
}

function corpusSite({ encoding = null, tamper = false, latest = null } = {}) {
  const obj = new Uint8Array([31, 139, 8, 0, 1, 2, 3]);
  const sha = (b) => createHash("sha256").update(b).digest("hex");
  const manifest = JSON.stringify({ schema: "tcmstudio.corpus/1", snapshot_id: "tcmcorpus-2026.10.09-abc", objects: { "core/core.json": { url: "o/aa.gz", sha256: sha(obj) } } });
  return {
    "runtime/boot.json": JSON.stringify({ pyodide: { index_url: "pyodide/314.0.7/" }, corpus: { manifest: "corpus/manifest.abc.json", sha256: sha(Buffer.from(manifest)) } }),
    "corpus/manifest.abc.json": manifest,
    "corpus/latest.json": JSON.stringify({ manifest: latest || "corpus/manifest.abc.json" }),
    "corpus/o/aa.gz": { body: tamper ? new Uint8Array([0]) : obj, headers: encoding ? { "Content-Encoding": encoding } : {} },
    "pyodide/314.0.7/pyodide-lock.json": JSON.stringify({ info: { python: "3.14.2" } }),
    "runner/manifest.json": JSON.stringify({ version: "0.2.0" }),
    "install.sh": "#!/bin/sh\n# TCMScience runner installer (science.impf.ai)\n",
    "install.ps1": "# TCMScience runner installer (science.impf.ai)\n",
  };
}

test("v2: the corpus, self-hosted Pyodide and the installers are checked when the site declares them", async () => {
  const out = await checkPublished("https://science.impf.ai", published(corpusSite()));
  assert.deepEqual(out.corpus, { snapshot_id: "tcmcorpus-2026.10.09-abc", objects: 1 });
  assert.deepEqual(out.pyodide, { self_hosted: true, python: "3.14.2" });
  assert.equal(out.runner.version, "0.2.0");
  // a site without v2 files: nothing declared, nothing required
  const bare = await checkPublished("https://science.impf.ai", published({ "runtime/boot.json": JSON.stringify({ pyodide: { index_url: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/" } }) }));
  assert.deepEqual(bare, { pyodide: { self_hosted: false } });
});

test("v2: a re-encoded or different corpus object, or a stale latest.json, fails the check", async () => {
  await assert.rejects(checkPublished("https://x.test", published(corpusSite({ encoding: "gzip" }))), /Content-Encoding gzip/);
  await assert.rejects(checkPublished("https://x.test", published(corpusSite({ tamper: true }))), /does not match its SHA-256/);
  await assert.rejects(checkPublished("https://x.test", published(corpusSite({ latest: "corpus/manifest.old.json" }))), /latest\.json points at/);
  const files = corpusSite();
  delete files["install.ps1"];
  await assert.rejects(checkPublished("https://x.test", published(files)), /install\.ps1 answered 404/);
});
