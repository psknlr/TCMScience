// The security headers: what the Worker sets (src/site.js) and what _headers gives the files Cloudflare serves.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { CSP_REPORT_ONLY, PAGE_HEADERS, PERMISSIONS_POLICY, commonHeaders, redirectHttps, secure } from "../src/site.js";

/** _headers as Cloudflare reads it: blocks of a URL pattern and indented `Name: value` (or `! Name`) lines. */
function parseHeaders(text) {
  const rules = [];
  for (const line of text.split("\n")) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (!/^\s/.test(line)) {
      rules.push({ pattern: line.trim(), set: {}, detach: [] });
      continue;
    }
    const rule = rules.at(-1);
    const t = line.trim();
    if (t.startsWith("!")) rule.detach.push(t.slice(1).trim().toLowerCase());
    else {
      const at = t.indexOf(":");
      rule.set[t.slice(0, at).trim().toLowerCase()] = t.slice(at + 1).trim();
    }
  }
  return rules;
}

const file = readFileSync(new URL("../_headers", import.meta.url), "utf8");
const rules = parseHeaders(file);
const all = rules.find((r) => r.pattern === "/*");

test("_headers gives every file what the Worker gives its own answers", () => {
  assert.ok(all, "a /* rule");
  const expected = { ...commonHeaders({}), ...PAGE_HEADERS };
  for (const [name, value] of Object.entries(expected)) assert.equal(all.set[name.toLowerCase()], value, name);
  assert.equal(all.set["content-security-policy-report-only"], CSP_REPORT_ONLY);
  assert.equal(all.set["permissions-policy"], PERMISSIONS_POLICY);
  for (const line of file.split("\n").filter((l) => /^\s+\S/.test(l))) assert.ok(line.length < 2000, "a header line Cloudflare accepts");
});

test("the page headers: isolation for Pyodide, Origin kept for the relay, the content policy the app needs", () => {
  assert.equal(all.set["cross-origin-opener-policy"], "same-origin");
  assert.equal(all.set["cross-origin-embedder-policy"], "require-corp");
  assert.equal(all.set["referrer-policy"], "strict-origin-when-cross-origin"); // never no-referrer: Origin must be sent
  assert.equal(all.set["x-content-type-options"], "nosniff");
  assert.match(all.set["strict-transport-security"], /^max-age=\d{7,}$/);
  assert.equal(all.set["x-frame-options"], "SAMEORIGIN");
  assert.doesNotMatch(all.set["permissions-policy"], /local-network-access/); // the runner on 127.0.0.1 must be reachable
  assert.equal("content-security-policy" in all.set, false, "report-only until the reports are clean");
  const csp = Object.fromEntries(CSP_REPORT_ONLY.split(";").map((d) => d.trim().split(/\s+/)).map(([k, ...v]) => [k, v]));
  assert.deepEqual(csp["script-src"], ["'self'", "https://cdn.jsdelivr.net", "'wasm-unsafe-eval'"]);
  assert.deepEqual(csp["connect-src"], ["'self'", "https:", "http://127.0.0.1:*", "http://localhost:*", "ws://127.0.0.1:*"]);
  assert.deepEqual(csp["worker-src"], ["'self'", "blob:"]);
  assert.deepEqual(csp["img-src"], ["'self'", "data:", "blob:"]);
  assert.deepEqual(csp["style-src"], ["'self'", "'unsafe-inline'"]);
  assert.deepEqual(csp["font-src"], ["'self'", "https://cdn.jsdelivr.net", "data:"]);
  assert.deepEqual(csp["frame-src"], ["'self'", "http://127.0.0.1:*", "http://localhost:*", "blob:"]);
  assert.deepEqual(csp["object-src"], ["'none'"]);
  assert.deepEqual(csp["base-uri"], ["'none'"]);
  assert.deepEqual(csp["frame-ancestors"], ["'self'"]);
  assert.ok(!csp["script-src"].includes("'unsafe-eval'") && !csp["script-src"].includes("'unsafe-inline'"));
});

test("caching: the content-hashed bundle forever, everything else revalidated", () => {
  assert.equal(all.set["cache-control"], "no-cache");
  const bundle = rules.find((r) => r.pattern === "/runtime/*.tar.gz");
  assert.ok(bundle);
  assert.deepEqual(bundle.detach, ["cache-control"]); // otherwise the two rules' values would be joined
  assert.equal(bundle.set["cache-control"], "public, max-age=31536000, immutable");
});

test("secure(): every answer gets the common headers, an HTML one the page headers too", async () => {
  const api = secure(new Response('{"ok":true}', { status: 503, headers: { "Content-Type": "application/json", "Retry-After": "60" } }), {});
  assert.equal(api.status, 503);
  assert.equal(api.headers.get("Retry-After"), "60");
  assert.equal(api.headers.get("X-Content-Type-Options"), "nosniff");
  assert.equal(api.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin");
  assert.equal(api.headers.get("Strict-Transport-Security"), "max-age=15552000");
  assert.equal(api.headers.get("Cross-Origin-Embedder-Policy"), null);
  assert.equal(await api.text(), '{"ok":true}');
  const page = secure(new Response("<!doctype html>", { headers: { "Content-Type": "text/html; charset=utf-8" } }), { HSTS_MAX_AGE: "86400" });
  assert.equal(page.headers.get("Cross-Origin-Opener-Policy"), "same-origin");
  assert.equal(page.headers.get("Cross-Origin-Embedder-Policy"), "require-corp");
  assert.equal(page.headers.get("Content-Security-Policy-Report-Only"), CSP_REPORT_ONLY);
  assert.equal(page.headers.get("X-Frame-Options"), "SAMEORIGIN");
  assert.equal(page.headers.get("Strict-Transport-Security"), "max-age=86400");
  assert.equal(secure(new Response(""), { HSTS_MAX_AGE: "0" }).headers.get("Strict-Transport-Security"), null);
  // headers already set are replaced, never repeated
  const twice = secure(secure(new Response("<p>", { headers: { "Content-Type": "text/html", "Referrer-Policy": "no-referrer" } })));
  assert.equal(twice.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin");
  assert.equal(twice.headers.get("Cross-Origin-Opener-Policy"), "same-origin");
  // a streamed body passes through untouched
  const streamed = secure(new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode("data: 1\n\n")); c.close(); } }), { headers: { "Content-Type": "text/event-stream" } }));
  assert.equal(await streamed.text(), "data: 1\n\n");
});

test("http → https, except loopback", () => {
  const r = redirectHttps(new URL("http://science.impf.ai/p/1?x=2"));
  assert.equal(r.status, 301);
  assert.equal(r.headers.get("Location"), "https://science.impf.ai/p/1?x=2");
  assert.equal(redirectHttps(new URL("https://science.impf.ai/")), null);
  assert.equal(redirectHttps(new URL("http://127.0.0.1:8787/")), null);
  assert.equal(redirectHttps(new URL("http://localhost:8787/")), null);
});
