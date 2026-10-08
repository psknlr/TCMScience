// The Worker entry with fake bindings: routing, the Durable Object's wiring (fail closed), the fallback for a path that
// matches no file. `cloudflare:workers` exists only in the Workers runtime; a module hook stands in for it here.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import { KEY, PAGE, events, quiet, sqlStore, tidy } from "./helpers.js";

const STUB = "data:text/javascript,export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }";
register(`data:text/javascript,${encodeURIComponent(`
export async function resolve(specifier, context, next) {
  if (specifier === "cloudflare:workers") return { url: ${JSON.stringify(STUB)}, shortCircuit: true };
  return next(specifier, context);
}`)}`);
const { default: worker, Limiter, isNavigation } = await import("../src/index.js");

const UPSTREAM_EVENTS = [
  '{"id":"a","choices":[{"index":0,"delta":{"content":"好"}}],"model":"MiniMax-M3","usage":null}',
  '{"id":"a","choices":[{"index":0,"finish_reason":"stop","delta":{}}],"model":"MiniMax-M3","usage":{"prompt_tokens":20,"completion_tokens":2,"total_characters":0},"base_resp":{"status_code":0}}',
  "[DONE]",
];

/** The Worker's environment and context as Cloudflare gives them, with fakes behind each binding. */
function setup({ limiterFails = false, burst = () => ({ success: true }), assets } = {}) {
  const limiter = new Limiter({ storage: { sql: sqlStore() } }, {});
  const assetCalls = [];
  const bursts = [];
  const waits = [];
  const upstream = [];
  const env = {
    MINIMAX_API_KEY: KEY,
    LIMITER: {
      idFromName: (name) => `id:${name}`,
      get: (id) => {
        assert.equal(id, "id:limits");
        if (limiterFails) throw new Error("Durable Object: overloaded");
        return limiter;
      },
    },
    BURST: { limit: async (args) => { bursts.push(args.key); return burst(args); } },
    ASSETS: {
      fetch: async (request) => {
        assetCalls.push(new URL(request.url).pathname);
        return assets ? assets(request) : new Response("<!doctype html><title>TCMScience Studio</title>", { headers: { "Content-Type": "text/html; charset=utf-8" } });
      },
    },
  };
  const ctx = { waitUntil: (p) => waits.push(p) };
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    upstream.push({ url: String(url), init });
    return new Response(UPSTREAM_EVENTS.map((e) => `data: ${e}\n\n`).join(""), { headers: { "Content-Type": "text/event-stream" } });
  };
  const call = (path, init = {}, base = "https://science.impf.ai") => worker.fetch(new Request(`${base}${path}`, init), env, ctx);
  const done = async () => {
    globalThis.fetch = saved;
    await Promise.all(waits);
  };
  return { call, env, limiter, assetCalls, bursts, waits, upstream, done };
}

const chat = (headers = {}) => ({
  method: "POST", body: JSON.stringify(tidy),
  headers: { "Content-Type": "application/json", Origin: PAGE, "CF-Connecting-IP": "203.0.113.9", ...headers },
});

test("/v1/* is the relay, through the Durable Object and the ratelimit binding, with the security headers", async () => {
  const w = setup();
  try {
    const health = await w.call("/v1/health");
    assert.equal(health.status, 200);
    assert.equal((await health.json()).model, "Tao-S1");
    assert.equal(health.headers.get("X-Content-Type-Options"), "nosniff");
    assert.equal(health.headers.get("Strict-Transport-Security"), "max-age=15552000");
    assert.equal(health.headers.get("Cross-Origin-Resource-Policy"), "same-origin");
    const res = await w.call("/v1/chat/completions", chat());
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin");
    const out = events(await res.text());
    assert.deepEqual(out.map((e) => e.model), ["Tao-S1", "Tao-S1"]);
    assert.equal(w.upstream.length, 1);
    assert.equal(w.upstream[0].url, "https://api.minimax.cn/v1/chat/completions");
    assert.equal(w.upstream[0].init.headers.Authorization, `Bearer ${KEY}`);
    assert.deepEqual(w.bursts, ["203.0.113.9"]);
    await Promise.all(w.waits); // the answer's cost, settled after it was sent (ctx.waitUntil)
    const rows = w.limiter.core.sql.exec("SELECT k, n FROM hits ORDER BY k").toArray();
    assert.equal(rows.find((r) => r.k.startsWith("d:chat:")).n, 1);
    assert.equal(rows.find((r) => r.k.startsWith("k:chat:")).n, 22);
    assert.ok(rows.every((r) => !r.k.includes("203.0.113.9"))); // the address is kept nowhere
    const notFound = await w.call("/v1");
    assert.equal(notFound.status, 404);
    assert.equal((await notFound.json()).error.type, "not_found");
  } finally {
    await w.done();
  }
});

test("fail closed: the Durable Object out of reach refuses the call; the ratelimit binding failing does not", () => quiet(async () => {
  const down = setup({ limiterFails: true });
  try {
    const res = await down.call("/v1/chat/completions", chat());
    assert.equal(res.status, 503);
    assert.equal((await res.json()).error.type, "unavailable");
    assert.equal(down.upstream.length, 0);
  } finally {
    await down.done();
  }
  const rejecting = setup();
  rejecting.limiter.hit = async () => { throw new Error("storage reset"); };
  try {
    assert.equal((await rejecting.call("/v1/chat/completions", chat())).status, 503);
    assert.equal(rejecting.upstream.length, 0);
  } finally {
    await rejecting.done();
  }
  const flaky = setup({ burst: () => { throw new Error("ratelimit binding"); } });
  try {
    const res = await flaky.call("/v1/chat/completions", chat());
    assert.equal(res.status, 200);
    await res.text();
  } finally {
    await flaky.done();
  }
  const busy = setup({ burst: () => ({ success: false }) });
  try {
    const res = await busy.call("/v1/chat/completions", chat());
    assert.deepEqual([res.status, res.headers.get("Retry-After")], [429, "10"]);
    assert.equal(busy.upstream.length, 0);
  } finally {
    await busy.done();
  }
}));

test("a path that matches no file: a navigation gets the app (with the page headers), anything else a true 404", async () => {
  const w = setup();
  try {
    for (const headers of [{ "Sec-Fetch-Mode": "navigate", Accept: "text/html" }, { Accept: "text/html,application/xhtml+xml,*/*;q=0.8" }]) {
      const res = await w.call("/p/123/c/456", { headers });
      assert.equal(res.status, 200);
      assert.match(await res.text(), /TCMScience Studio/);
      assert.equal(res.headers.get("Cross-Origin-Opener-Policy"), "same-origin");
      assert.equal(res.headers.get("Cross-Origin-Embedder-Policy"), "require-corp");
      assert.match(res.headers.get("Content-Security-Policy-Report-Only"), /script-src/);
    }
    assert.deepEqual(w.assetCalls, ["/", "/"]);
    for (const headers of [{ Accept: "*/*" }, { Accept: "application/json" }, { "Sec-Fetch-Mode": "cors", Accept: "text/html" }, { "Sec-Fetch-Mode": "no-cors" }]) {
      const res = await w.call("/runtime/catalog.json", { headers });
      assert.equal(res.status, 404, JSON.stringify(headers));
      assert.equal(res.headers.get("Content-Type"), "text/plain; charset=utf-8");
      assert.equal(res.headers.get("X-Content-Type-Options"), "nosniff");
      assert.equal(await res.text(), "Not Found\n");
    }
    assert.equal(w.assetCalls.length, 2);
    const head = await w.call("/missing.js", { method: "HEAD" });
    assert.equal(head.status, 404);
    const post = await w.call("/p/1", { method: "POST", body: "x" });
    assert.deepEqual([post.status, post.headers.get("Allow")], [405, "GET, HEAD"]);
    const clear = await w.call("/p/1", { headers: { Accept: "text/html" } }, "http://science.impf.ai");
    assert.deepEqual([clear.status, clear.headers.get("Location")], [301, "https://science.impf.ai/p/1"]);
  } finally {
    await w.done();
  }
  const empty = setup({ assets: () => new Response("Not Found", { status: 404 }) }); // a site without index.html
  try {
    assert.equal((await empty.call("/p/1", { headers: { "Sec-Fetch-Mode": "navigate" } })).status, 404);
  } finally {
    await empty.done();
  }
});

test("isNavigation: Fetch Metadata when the browser sends it, else Accept", () => {
  const nav = (headers) => isNavigation(new Request("https://science.impf.ai/p/1", { headers }));
  assert.equal(nav({ "Sec-Fetch-Mode": "navigate" }), true);
  assert.equal(nav({ "Sec-Fetch-Mode": "cors", Accept: "text/html" }), false);
  assert.equal(nav({ Accept: "text/html" }), true);
  assert.equal(nav({}), false);
});
