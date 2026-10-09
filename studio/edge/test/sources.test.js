import assert from "node:assert/strict";
import test from "node:test";
import { compileRegistry, handleSources, sourceConfig } from "../src/sources.js";
import { PAGE, NOON } from "./helpers.js";

const manifest = { version: 1, sources: [
  { key: "rest", base_url: "https://api.example.org/v1", host: "api.example.org", rate_rps: 3,
    operations: [{ name: "search", path: "lookup/{id}", method: "GET", params: { format: "json", q: "{query}" } },
      { name: "form", path: "search", method: "POST", params: {}, form: { q: "{query}", type: "compound" } }] },
  { key: "graph", base_url: "https://graph.example.org/graphql", host: "graph.example.org",
    operations: [{ name: "lookup", path: "", method: "POST", params: {}, graphql: "query($id:String!){target(id:$id){id}}", variables: { id: "{id}" } },
      { name: "version", path: "", method: "POST", params: {}, graphql: "query{dbVersion}", variables: {} }] },
  { key: "legacy", base_url: "http://47.92.70.12/chedi", host: "47.92.70.12",
    operations: [{ name: "lookup", path: "{id}", method: "GET", params: {} }] },
  { key: "other", base_url: "https://other.example.org/v1", host: "other.example.org",
    operations: [{ name: "lookup", path: "lookup/{id}", method: "GET", params: { format: "json", q: "{query}" } }] },
] };
const standard = { url: "https://api.example.org/v1/lookup/黄芩?format=json&q=TP53", method: "GET", headers: { Accept: "application/json" }, allowed_hosts: ["api.example.org"] };
function setup({ env = {}, upstream, registry = manifest, gate, pace, burst } = {}) {
  const calls = [];
  const gates = [];
  const paces = [];
  const deps = {
    manifest: async () => registry,
    now: () => NOON,
    burst: async () => burst ?? true,
    gate: async (args) => { gates.push(args); return typeof gate === "function" ? gate(args) : gate ?? { ok: true }; },
    pace: async (host, rps) => { paces.push({ host, rps }); return typeof pace === "function" ? pace(host, rps) : pace ?? { ok: true, delay: 0 }; },
    fetch: async (url, init) => { calls.push({ url, init }); return upstream ? upstream(url, init) : new Response('{"id":"TP53"}', { headers: { "Content-Type": "application/json" } }); },
  };
  const call = (packet = standard, headers = {}, options = {}) => handleSources(new Request(`https://science.impf.ai${options.path || "/api/sources/request"}`, {
    method: options.method || "POST", headers: { Origin: PAGE, "Content-Type": "application/json", "CF-Connecting-IP": "198.51.100.42", ...headers },
    body: (options.method || "POST") === "POST" ? JSON.stringify(packet) : undefined, signal: options.signal,
  }), env, deps);
  return { calls, gates, paces, call, deps };
}
const body = (value) => Buffer.from(typeof value === "string" ? value : JSON.stringify(value)).toString("base64");

test("read queries preserve upstream bytes/status/selected headers and use separate visitor quotas", async () => {
  const w = setup({ upstream: () => new Response(new Uint8Array([0, 255, 128]), { status: 429,
    headers: { "Content-Type": "application/octet-stream", "Retry-After": "15", "Set-Cookie": "secret", "Content-Encoding": "gzip" } }) });
  const response = await w.call();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Access-Control-Allow-Origin"), PAGE);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  const result = await response.json();
  assert.equal(result.status, 429);
  assert.deepEqual([...Buffer.from(result.body_base64, "base64")], [0, 255, 128]);
  assert.deepEqual(result.headers, { "Content-Type": "application/octet-stream", "Retry-After": "15" });
  assert.equal(result.transport, "https");
  assert.equal(w.gates[0].kind, "sources");
  assert.ok(!w.gates[0].who.includes("198.51.100.42"));
  assert.deepEqual(w.paces, [{ host: "api.example.org", rps: 3 }]);
  assert.equal(w.calls[0].init.redirect, "manual");
  assert.match(w.calls[0].init.headers.get("User-Agent"), /TCMScience/);
});

test("requires an allowed Origin; health is public; preflight exposes only JSON", async () => {
  const w = setup();
  for (const origin of ["", "null", "https://evil.example.org"]) assert.equal((await w.call(standard, { Origin: origin })).status, 403);
  assert.equal(w.calls.length, 0);
  const health = await w.call(null, {}, { path: "/api/sources/health", method: "GET" });
  assert.equal((await health.json()).operations, 6);
  const preflight = await w.call(null, {}, { method: "OPTIONS" });
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("Access-Control-Allow-Headers"), "Content-Type");
  assert.equal((await w.call(null, {}, { method: "GET" })).status, 405);
  assert.equal((await w.call(null, {}, { path: "/api/sources/unknown", method: "GET" })).status, 404);
});

test("caller host lists cannot add a source, alter paths/query constants or contact private addresses", async () => {
  const w = setup();
  for (const url of [
    "https://127.0.0.1/private", "https://169.254.169.254/latest/meta-data", "http://10.0.0.1/private",
    "https://192.168.0.1/private", "https://[::1]/private", "https://api.example.org:444/v1/lookup/1?format=json&q=a",
    "https://api.example.org/v1/lookup/../admin?format=json&q=a", "https://api.example.org/v1/lookup/%252e%252e/admin?format=json&q=a",
    "https://api.example.org/v1/admin?format=json&q=a", "https://api.example.org/v1/lookup/1?format=xml&q=a",
    "https://api.example.org/v1/lookup/1?format=json&q=a&write=true", "https://user:pass@api.example.org/v1/lookup/1?format=json&q=a",
    "https://evil.example.org/v1/lookup/1?format=json&q=a", "https://api.example.org/v1/lookup/1?format=json&q=a#secret",
  ]) {
    const host = new URL(url).hostname;
    const result = await w.call({ ...standard, url, allowed_hosts: [host] });
    assert.equal(result.status, 403, url);
  }
  assert.equal((await w.call({ ...standard, method: "DELETE" })).status, 403);
  assert.equal(w.calls.length, 0);
});

test("GraphQL permits exact registered read queries and typed variables, never arbitrary or mutated queries", async () => {
  const w = setup();
  const packet = { url: "https://graph.example.org/graphql", method: "POST", allowed_hosts: ["graph.example.org"], headers: { "Content-Type": "application/json" } };
  const query = manifest.sources[1].operations[0].graphql;
  assert.equal((await w.call({ ...packet, body_base64: body({ query, variables: { id: "ENSG00000141510" } }) })).status, 200);
  for (const value of [
    { query: "mutation{deleteAll}", variables: {} }, { query: "query{deleteAll}", variables: {} },
    { query: "query{dbVersion} mutation{deleteAll}", variables: {} }, { query, variables: { id: "x", extra: "y" } },
    { query, variables: { id: "x" }, operationName: "override" },
  ]) assert.equal((await w.call({ ...packet, body_base64: body(value) })).status, 403);
  assert.equal(w.calls.length, 1);
});

test("form templates retain fixed fields; HTTP-only registered sources strip credentials and report HTTP", async () => {
  const w = setup();
  const form = { ...standard, url: "https://api.example.org/v1/search", method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body_base64: body("q=berberine&type=compound") };
  assert.equal((await w.call(form)).status, 200);
  assert.equal((await w.call({ ...form, body_base64: body("q=berberine&type=delete") })).status, 403);
  assert.equal((await w.call({ ...form, body_base64: body("q=berberine&type=compound&admin=true") })).status, 403);
  const legacy = await w.call({ url: "http://47.92.70.12/chedi/1", method: "GET", allowed_hosts: ["47.92.70.12"],
    headers: { Authorization: "Bearer private", "X-API-Key": "private", "Api-Key": "private", Cookie: "private", "X-Forwarded-Host": "localhost" } });
  assert.equal((await legacy.json()).transport, "http");
  const forwarded = w.calls.at(-1).init.headers;
  for (const key of ["Authorization", "X-API-Key", "Api-Key", "Cookie", "X-Forwarded-Host"]) assert.equal(forwarded.has(key), false);
});

test("redirects are revalidated at every hop, enforce declared routes/schemes and remove cross-origin keys", async () => {
  for (const location of ["https://169.254.169.254/metadata", "https://evil.example.org/lookup/1", "/v1/admin", "http://47.92.70.12/chedi/1"]) {
    const w = setup({ upstream: () => new Response("redirect", { status: 302, headers: { Location: location } }) });
    const response = await w.call({ ...standard, allowed_hosts: ["api.example.org", "evil.example.org", "169.254.169.254", "47.92.70.12"] });
    assert.equal(response.status, 403, location);
    assert.equal((await response.json()).error.type, "redirect_refused");
    assert.equal(w.calls.length, 1);
  }
  let i = 0;
  const w = setup({ upstream: () => i++ === 0 ? new Response("redirect", { status: 302,
    headers: { Location: "https://other.example.org/v1/lookup/1?format=json&q=TP53" } }) : new Response("{}") });
  assert.equal((await w.call({ ...standard, headers: { Authorization: "Bearer secret", "X-API-Key": "secret" }, allowed_hosts: ["api.example.org", "other.example.org"] })).status, 200);
  assert.equal(w.calls[1].init.headers.has("Authorization"), false);
  assert.equal(w.calls[1].init.headers.has("X-API-Key"), false);
  const loop = setup({ upstream: (url) => new Response("", { status: 302, headers: { Location: url } }) });
  assert.equal((await loop.call()).status, 502);
  assert.equal(loop.calls.length, 4);
});

test("bounded packet/base64/response streams and fail-closed registry/quotas", async () => {
  const w = setup({ env: { SOURCE_MAX_BODY_BYTES: "1024", SOURCE_MAX_RESPONSE_BYTES: "1024" } });
  assert.equal((await w.call({ ...standard, body_base64: "A".repeat(2000) })).status, 413);
  assert.equal((await w.call({ ...standard, body_base64: "invalid!" })).status, 400);
  const huge = setup({ env: { SOURCE_MAX_RESPONSE_BYTES: "1024" }, upstream: () => new Response("x".repeat(1025)) });
  assert.equal((await huge.call()).status, 413);
  const chunked = setup({ env: { SOURCE_MAX_RESPONSE_BYTES: "1024" }, upstream: () => new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array(600)); c.enqueue(new Uint8Array(600)); c.close(); } })) });
  assert.equal((await chunked.call()).status, 413);
  for (const options of [{ registry: { version: 1, sources: [] } }, { gate: () => { throw new Error("DO down"); } }, { pace: () => { throw new Error("DO down"); } }]) {
    const down = setup(options);
    assert.equal((await down.call()).status, 503);
    assert.equal(down.calls.length, 0);
  }
  const limited = setup({ gate: { ok: false, retry: 25 } });
  const result = await limited.call();
  assert.equal(result.status, 429);
  assert.equal(result.headers.get("Retry-After"), "25");
  assert.equal(limited.calls.length, 0);
});

test("deadline applies to upstream fetch and a response body that never finishes", async () => {
  const slow = setup({ env: { SOURCE_TIMEOUT_MS: "1000" }, upstream: (_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true })) });
  assert.equal((await slow.call()).status, 504);
  const stalled = setup({ env: { SOURCE_TIMEOUT_MS: "1000" }, upstream: () => new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array([1])); } })) });
  const result = await stalled.call();
  assert.equal(result.status, 504);
  assert.equal((await result.json()).error.type, "timeout");
});

test("already cancelled requests never reach the upstream; packet timeout_s shortens the server deadline", async () => {
  const controller = new AbortController();
  controller.abort();
  const stopped = setup();
  const response = await stopped.call(standard, {}, { signal: controller.signal });
  assert.equal(response.status, 499);
  assert.equal((await response.json()).error.type, "cancelled");
  assert.equal(stopped.calls.length, 0);
  assert.equal(stopped.gates.length, 0);
  const slow = setup({ upstream: (_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true })) });
  const start = Date.now();
  assert.equal((await slow.call({ ...standard, timeout_s: 1 })).status, 504);
  assert.ok(Date.now() - start < 5000, "caller deadline must shorten the default 30s deadline");
});

test("registry rejects private sources and GraphQL mutation registrations; mistyped config retains caps", () => {
  for (const url of ["http://127.0.0.1/api", "https://169.254.169.254/api", "http://192.168.1.1/api"]) {
    assert.throws(() => compileRegistry({ version: 1, sources: [{ base_url: url, host: new URL(url).hostname, operations: [] }] }));
  }
  assert.throws(() => compileRegistry({ version: 1, sources: [{ base_url: "https://api.example.org", host: "api.example.org", operations: [{ path: "", method: "POST", graphql: "mutation{deleteAll}" }] }] }));
  const cfg = sourceConfig({ SOURCE_TIMEOUT_MS: "999999", SOURCE_MAX_RESPONSE_BYTES: "NaN", SOURCE_PER_DAY: "0" });
  assert.equal(cfg.timeout, 30000);
  assert.equal(cfg.maxResponse, 8388608);
  assert.equal(cfg.limits.perDay, 3000);
});
