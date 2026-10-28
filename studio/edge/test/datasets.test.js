// The dataset relay (src/datasets.js) with a fake upstream and fake bindings: it relays only the registry's files, from
// their reviewed hosts; passes on only Range and If-Range; copies back only the listed headers; refuses a file larger
// than the registry says before a byte is sent, and stops one of unknown length at the cap; and streams. Also the
// Worker's route to it, and the deploy check's probe (scripts/check.mjs).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import { check, checkDatasets, DATASET_PROBE } from "../scripts/check.mjs";
import { compileDatasets, handleDatasets, ifRangeOk, MAX_UNSIZED_BYTES, rangeOf, USER_AGENT } from "../src/datasets.js";
import { secure } from "../src/site.js";
import { NOON, PAGE, sqlStore } from "./helpers.js";

const TARGETS = {
  name: "CMAUPv2.0_download_Targets.txt", url: "https://bidd.group/CMAUP/downloadFiles/CMAUPv2.0_download_Targets.txt",
  host: "bidd.group", bytes: 117843, checksum: "sha256:baf471c0", reviewed: true, project: "CMAUP", fmt: "tsv",
  license: "Not stated", redistribution: "unknown — downloading is not permission to redistribute", relay: true, redirect_hosts: [],
};
// files.zenodo.org stands in for a reviewed mirror host the registry lists for the file
const LOTUS = {
  ...TARGETS, name: "260413_frozen.csv.gz", url: "https://zenodo.org/api/records/19360665/files/260413_frozen.csv.gz/content",
  host: "zenodo.org", bytes: 20594507, checksum: "md5:cf0cf2afa2ca4d758b68f2e39d466f5d", project: "LOTUS", fmt: "csv.gz", redirect_hosts: ["files.zenodo.org"],
};
const UNIPROT = {
  ...TARGETS, name: "uniprot_human_reviewed_accessions.txt",
  url: "https://rest.uniprot.org/uniprotkb/stream?query=organism_id:9606+AND+reviewed:true&format=list",
  host: "rest.uniprot.org", bytes: null, checksum: null, project: "UniProt", fmt: null,
};
const HELD = { ...TARGETS, name: "held.txt", url: "https://bidd.group/x/held.txt", relay: false };
const registryOf = (files) => ({
  schema: "tcmstudio.datasets/1",
  groups: { research: { title: { zh: "研究数据快照的原始文件", en: "Raw files of the research snapshots" }, sources: ["npass", "cmaup", "lotus"], network: true, files } },
});
const REGISTRY = registryOf([TARGETS, LOTUS, UNIPROT, HELD]);
const TSV = "Target_ID\tGene_Symbol\n";

const file = (body = TSV, headers = {}, status = 200) => new Response(body, { status, headers: {
  "Content-Type": "text/plain", "Content-Length": String(new TextEncoder().encode(body).byteLength), "Accept-Ranges": "bytes",
  ETag: '"1cc53-5f0"', "Last-Modified": "Tue, 07 Oct 2025 08:00:00 GMT", ...headers,
} });

/** A body that never ends: it counts how often it was asked for more, and whether it was cancelled. */
function endless(size = 65536) {
  const seen = { pulls: 0, cancelled: false };
  const stream = new ReadableStream({
    pull(c) { seen.pulls++; c.enqueue(new Uint8Array(size)); },
    cancel() { seen.cancelled = true; },
  }, { highWaterMark: 0 });
  return { stream, seen };
}

/** The relay with fake bindings: ASSETS serves `registry`, `upstream(url, init, n)` answers the n-th upstream call. */
function setup({ registry = REGISTRY, upstream, gate, pace, burst = true, env = {}, assets } = {}) {
  const calls = [];
  const gates = [];
  const paces = [];
  const bursts = [];
  const events = [];
  const assetCalls = [];
  const ASSETS = {
    fetch: async (request) => {
      assetCalls.push(new URL(request.url).pathname);
      return assets ? assets(request) : new Response(JSON.stringify(registry), { headers: { "Content-Type": "application/json" } });
    },
  };
  const deps = {
    now: () => NOON,
    burst: async (key) => { bursts.push(key); return typeof burst === "function" ? burst(key) : burst; },
    gate: async (args) => { gates.push(args); return typeof gate === "function" ? gate(args) : gate ?? { ok: true }; },
    pace: async (host, rps) => { paces.push({ host, rps }); return typeof pace === "function" ? pace(host) : pace ?? { ok: true, delay: 0 }; },
    wait: async () => {},
    fetch: async (url, init) => { calls.push({ url, init }); return upstream ? upstream(url, init, calls.length) : file(); },
    securityEvent: (kind, _request, info) => events.push(`${kind} ${info.route} ${info.status}`),
  };
  const fullEnv = { ASSETS, ...env };
  const call = (name = TARGETS.name, { method = "GET", headers = {}, path, base = "https://science.impf.ai", signal } = {}) => {
    const h = new Headers({ "CF-Connecting-IP": "198.51.100.42", "Sec-Fetch-Site": "same-origin" });
    for (const [k, v] of Object.entries(headers)) v === null ? h.delete(k) : h.set(k, v);
    const p = path ?? `/api/datasets/files/research/${encodeURIComponent(name)}`;
    return handleDatasets(new Request(`${base}${p}`, { method, headers: h, signal }), fullEnv, deps);
  };
  return { call, calls, gates, paces, bursts, events, assetCalls, deps };
}

const errorOf = async (res) => (await res.json()).error;

test("a registered file comes from its reviewed URL, with only the listed headers either way", async () => {
  const w = setup({ upstream: () => file(TSV, {
    "Set-Cookie": "session=1", Server: "nginx", "Content-Disposition": "inline", "Access-Control-Allow-Origin": "*",
    "Cache-Control": "public, max-age=600", Vary: "Accept-Encoding",
  }) });
  const res = await w.call(TARGETS.name, { headers: { Cookie: "tcms=1", Authorization: "Bearer x", "Accept-Encoding": "gzip", "X-Forwarded-For": "10.0.0.1", Accept: "text/html" } });
  assert.equal(res.status, 200);
  assert.equal(await res.text(), TSV);
  assert.deepEqual(Object.fromEntries(res.headers), {
    "accept-ranges": "bytes", "cache-control": "no-store", "content-disposition": "attachment",
    "content-length": String(TSV.length), "content-security-policy": "default-src 'none'; sandbox", "content-type": "text/plain",
    "cross-origin-resource-policy": "same-origin", etag: '"1cc53-5f0"', "last-modified": "Tue, 07 Oct 2025 08:00:00 GMT",
    "x-content-type-options": "nosniff", "x-relay-upstream": TARGETS.url,
  });
  const [sent] = w.calls;
  assert.equal(sent.url, TARGETS.url);
  assert.equal(sent.init.method, "GET");
  assert.equal(sent.init.redirect, "manual");
  assert.equal(sent.init.cache, "no-store", "not kept in Cloudflare's cache on the way");
  assert.equal(sent.init.credentials, undefined);
  // nothing of the visitor's: no cookie, credential or address; the coding asked for is the file's own bytes
  assert.deepEqual(Object.fromEntries(sent.init.headers), { "accept-encoding": "identity", "user-agent": USER_AGENT });
  assert.deepEqual(w.bursts, ["datasets:198.51.100.42"]);
  assert.equal(w.gates[0].kind, "datasets");
  assert.ok(!w.gates[0].who.includes("198.51.100.42"), "the address is kept nowhere");
  assert.deepEqual(w.paces, [{ host: "bidd.group", rps: 1 }]);
  // the registry is read once per isolate; a query string in a reviewed URL is kept as it is
  assert.equal((await w.call(UNIPROT.name)).status, 200);
  assert.equal(w.calls[1].url, UNIPROT.url);
  assert.deepEqual(w.assetCalls, ["/runtime/datasets.json"]);
  const head = await w.call(TARGETS.name, { method: "HEAD" });
  assert.equal(head.status, 200);
  assert.equal(w.calls[2].init.method, "HEAD");
  assert.equal(head.headers.get("Content-Length"), String(TSV.length));
  assert.equal(head.body, null);
});

test("only the registry's files, by GET or HEAD: anything else is a 404 or a 405, before the visitor's counters or the upstream", async () => {
  const w = setup();
  for (const path of [
    "/api/datasets", "/api/datasets/files", "/api/datasets/files/research", "/api/datasets/files/research/",
    "/api/datasets/files//x", "/api/datasets/files/research/a/b", "/api/datasets/other/research/x",
    "/api/datasets/files/research/nothing.txt", "/api/datasets/files/other/CMAUPv2.0_download_Targets.txt",
    "/api/datasets/files/research/..%2Fsecret", "/api/datasets/files/research/%E0%A4%A",
  ]) {
    const res = await w.call(null, { path });
    assert.equal(res.status, 404, path);
    assert.equal(res.headers.get("Content-Type"), "application/json; charset=utf-8");
    assert.equal((await errorOf(res)).type, "not_found", path);
  }
  const held = await w.call(HELD.name);
  assert.deepEqual([held.status, (await errorOf(held)).type], [404, "not_relayed"]);
  for (const method of ["POST", "PUT", "DELETE", "OPTIONS", "PATCH"]) {
    const res = await w.call(TARGETS.name, { method });
    assert.equal(res.status, 405, method);
    assert.equal(res.headers.get("Allow"), "GET, HEAD");
  }
  const head = await w.call("nothing.txt", { method: "HEAD" });
  assert.equal(head.status, 404);
  assert.equal(await head.text(), "");
  assert.equal(w.calls.length, 0);
  assert.equal(w.gates.length, 0, "a file the registry does not name costs the limiter nothing");
});

test("a page of this site only: Fetch Metadata and Origin are checked when a browser sends them; https", async () => {
  const w = setup();
  for (const headers of [
    { "Sec-Fetch-Site": "cross-site" }, { "Sec-Fetch-Site": "same-site" }, { "Sec-Fetch-Site": "none" },
    { Origin: "https://evil.example.org" }, { Origin: "null" }, { "Sec-Fetch-Site": null, Origin: "https://science.impf.ai.evil.example.org" },
  ]) {
    const res = await w.call(TARGETS.name, { headers });
    assert.equal(res.status, 403, JSON.stringify(headers));
    assert.equal((await errorOf(res)).type, "forbidden_origin");
  }
  assert.equal(w.calls.length, 0);
  assert.deepEqual(w.events, Array(6).fill("forbidden_origin datasets 403"));
  for (const headers of [{ Origin: PAGE }, { "Sec-Fetch-Site": null }, { Origin: "http://127.0.0.1:8765" }]) {
    const res = await w.call(TARGETS.name, { headers });
    assert.equal(res.status, 200, JSON.stringify(headers));
    await res.body.cancel();
  }
  const plain = await w.call(TARGETS.name, { base: "http://science.impf.ai" });
  assert.deepEqual([plain.status, (await errorOf(plain)).type], [403, "https_required"]);
});

test("Range and If-Range: one range from an offset, checked, then passed on as sent", async () => {
  assert.deepEqual(rangeOf("bytes=100-"), { start: 100, end: null });
  assert.deepEqual(rangeOf("bytes=0-99"), { start: 0, end: 99 });
  assert.equal(rangeOf(null), null);
  for (const bad of ["bytes=5-1", "bytes=0-1,5-6", "bytes=-500", "items=0-", "bytes = 0-", "bytes=0x10-", "bytes=", `bytes=${"9".repeat(17)}-`, "bytes=99999999999999999-"]) {
    assert.equal(rangeOf(bad), false, bad);
  }
  for (const good of ['"1cc53-5f0"', '""', "1cc53-5f0", "Tue, 07 Oct 2025 08:00:00 GMT"]) assert.ok(ifRangeOk(good), good);
  for (const bad of ['W/"1cc53-5f0"', "W/1cc53", "two words", "", "x".repeat(257), "Tue, 99 Foo"]) assert.ok(!ifRangeOk(bad), bad);

  const w = setup({ upstream: (_url, init) => {
    const range = init.headers.get("Range");
    if (!range) return file();
    const start = rangeOf(range).start;
    return new Response("x", { status: 206, headers: { "Content-Range": `bytes ${start}-${start}/${TARGETS.bytes}`, "Content-Length": "1" } });
  } });
  const resumed = await w.call(TARGETS.name, { headers: { Range: "bytes=117842-", "If-Range": '"1cc53-5f0"' } });
  assert.equal(resumed.status, 206);
  assert.equal(resumed.headers.get("Content-Range"), "bytes 117842-117842/117843");
  assert.equal(await resumed.text(), "x");
  assert.equal(w.calls[0].init.headers.get("Range"), "bytes=117842-");
  assert.equal(w.calls[0].init.headers.get("If-Range"), '"1cc53-5f0"');
  const dated = await w.call(TARGETS.name, { headers: { Range: "bytes=10-10", "If-Range": "Tue, 07 Oct 2025 08:00:00 GMT" } });
  assert.equal(dated.status, 206);
  await dated.body.cancel();
  assert.equal(w.calls[1].init.headers.get("If-Range"), "Tue, 07 Oct 2025 08:00:00 GMT");
  const before = w.calls.length;
  for (const headers of [
    { Range: "bytes=0-1,5-6" }, { Range: "bytes=-500" }, { Range: "items=0-" }, { "If-Range": '"1cc53-5f0"' },
    { Range: "bytes=10-", "If-Range": 'W/"1cc53-5f0"' }, { Range: "bytes=10-", "If-Range": "not a date" },
  ]) {
    const res = await w.call(TARGETS.name, { headers });
    assert.equal(res.status, 400, JSON.stringify(headers));
    assert.equal((await errorOf(res)).type, "invalid_range");
  }
  assert.equal(w.calls.length, before, "a refused range is never sent");
});

test("a 206 is the range asked for, of the file the registry sizes; a 200 to a range request passes as a whole file", async () => {
  const answer = { status: 206, headers: {} };
  const cancelled = [];
  const w = setup({ upstream: () => new Response(new ReadableStream({ cancel() { cancelled.push(true); } }), answer) });
  const ask = { headers: { Range: "bytes=100-" } };
  const cases = [
    [{ "Content-Range": "bytes 100-117842/117843" }, 206, null],
    [{ "Content-Range": "bytes 100-199/200000" }, 413, "size_mismatch"],
    [{ "Content-Range": "bytes 100-199/*" }, 413, "size_mismatch"],
    [{ "Content-Range": "bytes 0-117842/117843" }, 502, "bad_range"],
    [{ "Content-Range": "bytes 100-50/117843" }, 502, "bad_range"],
    [{}, 502, "bad_range"],
    [{ "Content-Range": "bytes 100-117842/117843", "Content-Encoding": "gzip" }, 502, "encoded_response"],
  ];
  for (const [headers, status, type] of cases) {
    answer.headers = headers;
    const res = await w.call(TARGETS.name, ask);
    assert.equal(res.status, status, JSON.stringify(headers));
    if (type) assert.equal((await errorOf(res)).type, type);
    else await res.body.cancel();
  }
  assert.equal(cancelled.length, cases.length, "every refused answer's body is cancelled, and the one relayed when the reader stops");
  answer.headers = { "Content-Range": "bytes 0-9/117843" };
  const unasked = await w.call(TARGETS.name);
  assert.deepEqual([unasked.status, (await errorOf(unasked)).type], [502, "unexpected_partial"]);
  const longer = await w.call(TARGETS.name, { headers: { Range: "bytes=0-9" } });
  assert.equal(longer.status, 206);
  await longer.body.cancel();
  answer.headers = { "Content-Range": "bytes 0-99/117843" };
  const beyond = await w.call(TARGETS.name, { headers: { Range: "bytes=0-9" } });
  assert.deepEqual([beyond.status, (await errorOf(beyond)).type], [502, "bad_range"]);
  // a 206's Content-Length is its part's length, and a part of unknown length is held to it
  answer.headers = { "Content-Range": "bytes 100-109/117843", "Content-Length": "11" };
  const misdeclared = await w.call(TARGETS.name, ask);
  assert.deepEqual([misdeclared.status, (await errorOf(misdeclared)).type], [502, "bad_range"]);
  const part = setup({ upstream: () => new Response(new Uint8Array(12), { status: 206, headers: { "Content-Range": "bytes 100-109/117843" } }) });
  const overlong = await part.call(TARGETS.name, ask);
  assert.equal(overlong.status, 206);
  assert.equal(overlong.headers.get("Content-Length"), null);
  await assert.rejects(overlong.arrayBuffer(), /larger than 10 bytes/);
  const whole = setup({ upstream: () => file() });
  const res = await whole.call(TARGETS.name, ask);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), TSV);
});

test("caps: a declared length over the registry's size is refused before streaming; an undeclared one is counted", async () => {
  let cancelled = 0;
  const over = setup({ upstream: () => new Response(new ReadableStream({ cancel() { cancelled++; } }), { headers: { "Content-Length": String(TARGETS.bytes + 1) } }) });
  const refused = await over.call();
  assert.equal(refused.status, 413);
  assert.equal((await errorOf(refused)).type, "too_large");
  assert.equal(cancelled, 1);
  const small = { ...TARGETS, bytes: 10 };
  const chunks = (...sizes) => new ReadableStream({ start(c) { for (const n of sizes) c.enqueue(new Uint8Array(n).fill(65)); c.close(); } });
  const counted = setup({ registry: registryOf([small]), upstream: (_url, _init, n) => new Response(n === 1 ? chunks(6, 6) : chunks(4, 6)) });
  const cut = await counted.call();
  assert.equal(cut.status, 200, "the status is sent before the body; the body then fails at the cap");
  assert.equal(cut.headers.get("Content-Length"), null);
  await assert.rejects(cut.arrayBuffer(), /larger than 10 bytes/);
  const exact = await counted.call();
  assert.equal((await exact.arrayBuffer()).byteLength, 10);
  // past the cap the upstream is stopped too, not drained
  const source = endless();
  const runaway = setup({ registry: registryOf([small]), upstream: () => new Response(source.stream) });
  await assert.rejects((await runaway.call()).arrayBuffer(), /larger than 10 bytes/);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(source.seen.cancelled, true);
  // no size in the registry: the browser's own limit for such a file, by the declared length or the range's total
  const unsized = setup({ upstream: (_url, init) => init.headers.has("Range")
    ? new Response(null, { status: 206, headers: { "Content-Range": `bytes 5-9/${MAX_UNSIZED_BYTES + 1}` } })
    : new Response(null, { headers: { "Content-Length": String(MAX_UNSIZED_BYTES + 1) } }) });
  assert.equal((await unsized.call(UNIPROT.name)).status, 413);
  const ranged = await unsized.call(UNIPROT.name, { headers: { Range: "bytes=5-" } });
  assert.deepEqual([ranged.status, (await errorOf(ranged)).type], [413, "too_large"]);
  // a part of a file whose total the host does not say ("/*") ends within the cap too: the part's own length is no limit
  let partCancelled = 0;
  const unknown = setup({ upstream: (_url, init) => new Response(new ReadableStream({ cancel() { partCancelled++; } }), { status: 206, headers: {
    "Content-Range": init.headers.get("Range") === "bytes=0-" ? `bytes 0-${2 * MAX_UNSIZED_BYTES - 1}/*` : `bytes 5-${MAX_UNSIZED_BYTES - 1}/*` } }) });
  const beyondCap = await unknown.call(UNIPROT.name, { headers: { Range: "bytes=0-" } });
  assert.equal(beyondCap.status, 413);
  assert.equal((await errorOf(beyondCap)).type, "too_large");
  assert.equal(partCancelled, 1, "refused before a byte is sent");
  const withinCap = await unknown.call(UNIPROT.name, { headers: { Range: "bytes=5-" } });
  assert.equal(withinCap.status, 206);
  await withinCap.body.cancel();
  const fits = setup({ upstream: () => new Response(null, { headers: { "Content-Length": String(MAX_UNSIZED_BYTES) } }) });
  const head = await fits.call(UNIPROT.name, { method: "HEAD" });
  assert.equal(head.status, 200);
  assert.equal(head.headers.get("Content-Length"), String(MAX_UNSIZED_BYTES));
});

test("a body the runtime decoded: its encoded length is neither checked nor sent, the decoded bytes are counted", async () => {
  const small = { ...TARGETS, bytes: 10 };
  const w = setup({ registry: registryOf([small]), upstream: (_url, _init, n) => new Response(new Uint8Array(n === 1 ? 12 : 10), {
    headers: { "Content-Encoding": n === 3 ? "compress" : "gzip", "Content-Length": "3" } }) });
  const over = await w.call();
  assert.equal(over.headers.get("Content-Length"), null);
  assert.equal(over.headers.get("X-Relay-Decoded"), "gzip");
  assert.equal(over.headers.get("Content-Encoding"), null);
  await assert.rejects(over.arrayBuffer(), /larger than 10 bytes/);
  assert.equal((await (await w.call()).arrayBuffer()).byteLength, 10);
  const unknown = await w.call();
  assert.deepEqual([unknown.status, (await errorOf(unknown)).type], [502, "encoded_response"]);
});

test("streams: the upstream body is passed through as it arrives, never read whole", { timeout: 10000 }, async () => {
  // a declared length: the very stream the upstream gave, untouched
  const known = endless();
  const w = setup({ upstream: () => new Response(known.stream, { headers: { "Content-Length": String(TARGETS.bytes) } }) });
  const res = await w.call();
  assert.equal(res.status, 200);
  assert.equal(res.body, known.stream);
  assert.ok(known.seen.pulls <= 1, `the handler asked the upstream for ${known.seen.pulls} chunks`);
  const reader = res.body.getReader();
  for (let i = 0; i < 3; i++) assert.equal((await reader.read()).value.byteLength, 65536);
  await reader.cancel();
  assert.equal(known.seen.cancelled, true, "a reader that stops stops the upstream");
  // no declared length: counted on its way, a chunk at a time
  const unknown = endless();
  const u = setup({ upstream: () => new Response(unknown.stream) });
  const counted = await u.call(UNIPROT.name);
  assert.equal(counted.status, 200);
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(unknown.seen.pulls <= 3, `${unknown.seen.pulls} chunks were taken before anyone read`);
  const r2 = counted.body.getReader();
  let got = 0;
  for (let i = 0; i < 5; i++) got += (await r2.read()).value.byteLength;
  assert.equal(got, 5 * 65536);
  await r2.cancel();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(unknown.seen.cancelled, true);
});

test("redirects: at most three hops, each https to the file's host or a host the registry lists for it", async () => {
  const mirror = "https://files.zenodo.org/records/19360665/260413_frozen.csv.gz";
  const w = setup({ upstream: (url) => (url === LOTUS.url ? new Response(null, { status: 302, headers: { Location: mirror } }) : file()) });
  const res = await w.call(LOTUS.name, { headers: { Range: "bytes=0-" } });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("X-Relay-Upstream"), mirror);
  await res.body.cancel();
  assert.deepEqual(w.calls.map((c) => c.url), [LOTUS.url, mirror]);
  assert.equal(w.calls[1].init.headers.get("Range"), "bytes=0-", "the range goes to the host that answers");
  assert.deepEqual(w.paces, [{ host: "zenodo.org", rps: 1 }, { host: "files.zenodo.org", rps: 1 }]);
  // a rate the registry gives the file is its own host's pace; the host it is redirected to keeps the default
  const slow = setup({ registry: registryOf([{ ...LOTUS, rate_rps: 0.1 }]), upstream: (url) => (url === LOTUS.url ? new Response(null, { status: 302, headers: { Location: mirror } }) : file()) });
  await (await slow.call(LOTUS.name)).body.cancel();
  assert.deepEqual(slow.paces, [{ host: "zenodo.org", rps: 0.1 }, { host: "files.zenodo.org", rps: 1 }]);
  const relative = setup({ upstream: (url) => (url === LOTUS.url ? new Response(null, { status: 307, headers: { Location: "/records/1/file#part" } }) : file()) });
  const followed = await relative.call(LOTUS.name);
  assert.equal(followed.headers.get("X-Relay-Upstream"), "https://zenodo.org/records/1/file");
  await followed.body.cancel();
  for (const location of [
    "https://evil.example.org/file", "http://files.zenodo.org/file", "https://files.zenodo.org:8443/file",
    "https://user:secret@zenodo.org/file", "https://10.0.0.1/file", "https://bidd.group/CMAUP/downloadFiles/x",
  ]) {
    const off = setup({ upstream: () => new Response("moved", { status: 301, headers: { Location: location } }) });
    const refused = await off.call(LOTUS.name);
    assert.equal(refused.status, 502, location);
    const error = await errorOf(refused);
    assert.equal(error.type, "redirect_refused");
    // the host it was sent to is named (null when the address is not one the relay would follow at all), so a
    // reviewer can add it to the file's redirect_hosts
    const named = { "https://evil.example.org/file": "evil.example.org", "https://bidd.group/CMAUP/downloadFiles/x": "bidd.group" }[location] ?? null;
    assert.equal(error.redirected_to, named, location);
    if (named) assert.match(error.message, new RegExp(named.replace(/\./g, "\\.")));
    assert.equal(off.calls.length, 1, location);
  }
  const loop = setup({ upstream: (url) => new Response(null, { status: 302, headers: { Location: url } }) });
  const looped = await loop.call(LOTUS.name);
  assert.deepEqual([looped.status, (await errorOf(looped)).type], [502, "redirect_refused"]);
  assert.equal(loop.calls.length, 4);
  // a 3xx without a Location is an answer, not a redirect: the upstream's status is named
  const odd = setup({ upstream: () => new Response(null, { status: 302 }) });
  const told = await odd.call();
  assert.equal(told.status, 502);
  assert.deepEqual(await errorOf(told), { type: "upstream_status", upstream_status: 302, message: "数据集服务器 bidd.group 返回 HTTP 302" });
});

test("the upstream's failures: its status kept and named, a network failure 502, a stall 504, a cancel 499", async () => {
  for (const [status, headers, expect, extra] of [
    [404, {}, 404, {}],
    [503, { "Retry-After": "30" }, 503, { "retry-after": "30" }],
    // only a number of seconds is passed on: the relay does not make one up from a date
    [429, { "Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT" }, 429, { "retry-after": null }],
    [416, { "Content-Range": "bytes */117843" }, 416, { "content-range": "bytes */117843" }],
    [412, {}, 412, {}],
    [204, {}, 502, {}],
  ]) {
    const w = setup({ upstream: () => new Response(status === 204 ? null : "<html>error</html>", { status, headers: { "Content-Type": "text/html", ...headers } }) });
    const res = await w.call(TARGETS.name, { headers: { Range: "bytes=117843-" } });
    assert.equal(res.status, expect, String(status));
    for (const [k, v] of Object.entries(extra)) assert.equal(res.headers.get(k), v, `${status} ${k}`);
    assert.equal(res.headers.get("Content-Type"), "application/json; charset=utf-8");
    const error = await errorOf(res);
    assert.equal(error.type, "upstream_status");
    assert.equal(error.upstream_status, status);
    assert.match(error.message, new RegExp(`HTTP ${status}`));
  }
  const head = setup({ upstream: () => new Response(null, { status: 404 }) });
  const missing = await head.call(TARGETS.name, { method: "HEAD" });
  assert.deepEqual([missing.status, await missing.text()], [404, ""]);
  const down = setup({ upstream: () => { throw new TypeError("fetch failed"); } });
  const unreachable = await down.call();
  assert.deepEqual([unreachable.status, (await errorOf(unreachable)).type], [502, "upstream_unreachable"]);
  const stalled = setup({ env: { SOURCE_TIMEOUT_MS: "1000" }, upstream: (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
  }) });
  const late = await stalled.call();
  assert.deepEqual([late.status, (await errorOf(late)).type], [504, "timeout"]);
  const controller = new AbortController();
  controller.abort();
  const gone = setup();
  const stopped = await gone.call(TARGETS.name, { signal: controller.signal });
  assert.deepEqual([stopped.status, (await errorOf(stopped)).type], [499, "cancelled"]);
  assert.equal(gone.calls.length, 0);
  assert.equal(gone.gates.length, 0);
});

test("limits: the ten-second gate, the visitor's counters and the host's pacing refuse before the upstream; fail closed", async () => {
  const busy = setup({ burst: false });
  const burst = await busy.call();
  assert.deepEqual([burst.status, burst.headers.get("Retry-After"), (await errorOf(burst)).type], [429, "10", "rate_limited"]);
  assert.deepEqual(busy.events, ["rate_limited datasets 429"]);
  assert.equal(busy.gates.length, 0);
  const spent = setup({ gate: { ok: false, retry: 25 } });
  const quota = await spent.call();
  assert.deepEqual([quota.status, quota.headers.get("Retry-After")], [429, "25"]);
  assert.equal(spent.gates[0].limits.perMinute, 120, "the gateway's SOURCE_PER_* limits, counted apart as kind datasets");
  const queued = setup({ pace: { ok: false, retry: 5 } });
  const full = await queued.call();
  assert.deepEqual([full.status, full.headers.get("Retry-After")], [429, "5"]);
  for (const options of [{ gate: () => { throw new Error("DO down"); } }, { pace: () => { throw new Error("DO down"); } }]) {
    const down = setup(options);
    const res = await down.call();
    assert.deepEqual([res.status, (await errorOf(res)).type], [503, "unavailable"]);
    assert.equal(down.calls.length, 0);
  }
  for (const w of [busy, spent, queued]) assert.equal(w.calls.length, 0);
  const paused = setup({ env: { SOURCES: "off" } });
  const off = await paused.call();
  assert.deepEqual([off.status, (await errorOf(off)).type], [503, "not_configured"]);
  assert.equal(paused.calls.length, 0);
});

test("the registry: build-generated, checked whole; a bad or missing one serves nothing, and is read again next time", async () => {
  const compiled = compileDatasets(REGISTRY);
  assert.equal(compiled.files, 4);
  assert.deepEqual([...compiled.groups.get("research").get(LOTUS.name).hosts], ["zenodo.org", "files.zenodo.org"]);
  assert.equal(compiled.groups.get("research").get(LOTUS.name).rps, 1, "no rate in the registry: one request a second");
  assert.equal(compileDatasets(registryOf([{ ...TARGETS, rate_rps: 50 }])).groups.get("research").get(TARGETS.name).rps, 20, "the Limiter's ceiling");
  const bad = [
    { ...REGISTRY, schema: "tcmstudio.datasets/2" }, { schema: "tcmstudio.datasets/1" }, registryOf([]),
    { schema: "tcmstudio.datasets/1", groups: { "../x": REGISTRY.groups.research } },
    ...[
      { url: "http://bidd.group/x.txt" }, { host: "evil.example.org" }, { url: "https://bidd.group:8443/x.txt" },
      { url: "https://u:p@bidd.group/x.txt" }, { url: "https://10.0.0.1/x.txt", host: "10.0.0.1" },
      { url: "https://localhost/x.txt", host: "localhost" }, { url: "https://bidd.group/x.txt#frag" },
      { name: "a/b.txt" }, { name: ".." }, { name: "" }, { redirect_hosts: ["10.0.0.1"] }, { redirect_hosts: "files.zenodo.org" },
      { redirect_hosts: ["Files.Zenodo.org"] }, { bytes: -1 }, { bytes: 1.5 }, { bytes: "10" },
      { rate_rps: 0 }, { rate_rps: -1 }, { rate_rps: "1" }, { rate_rps: Number.NaN }, { rate_rps: Number.POSITIVE_INFINITY },
    ].map((change) => registryOf([{ ...TARGETS, ...change }])),
    registryOf([TARGETS, TARGETS]),
  ];
  for (const doc of bad) assert.throws(() => compileDatasets(doc), undefined, JSON.stringify(doc).slice(0, 200));
  let broken = true;
  const w = setup({ assets: () => (broken ? new Response("Not Found", { status: 404 }) : new Response(JSON.stringify(REGISTRY), { headers: { "Content-Type": "application/json" } })) });
  const first = await w.call();
  assert.deepEqual([first.status, (await errorOf(first)).type], [503, "not_configured"]);
  broken = false;
  const second = await w.call();
  assert.equal(second.status, 200);
  await second.body.cancel();
  assert.equal(w.assetCalls.length, 2);
  const invalid = setup({ registry: registryOf([{ ...TARGETS, url: "http://bidd.group/x" }]) });
  assert.equal((await invalid.call()).status, 503);
  assert.equal(invalid.calls.length, 0);
});

test("the Worker routes /api/datasets/* to the relay, through the Durable Object and the ratelimit binding", async () => {
  const STUB = "data:text/javascript,export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }";
  register(`data:text/javascript,${encodeURIComponent(`
export async function resolve(specifier, context, next) {
  if (specifier === "cloudflare:workers") return { url: ${JSON.stringify(STUB)}, shortCircuit: true };
  return next(specifier, context);
}`)}`);
  const { default: worker, Limiter } = await import("../src/index.js");
  const limiter = new Limiter({ storage: { sql: sqlStore() } }, {});
  const bursts = [];
  const upstream = [];
  const env = {
    LIMITER: { idFromName: (name) => `id:${name}`, get: () => limiter },
    BURST: { limit: async ({ key }) => { bursts.push(key); return { success: true }; } },
    ASSETS: { fetch: async () => new Response(JSON.stringify(REGISTRY), { headers: { "Content-Type": "application/json" } }) },
  };
  const waits = [];
  const ctx = { waitUntil: (p) => waits.push(p) };
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, init) => { upstream.push({ url: String(url), init }); return file(); };
  try {
    const call = (name) => worker.fetch(new Request(`https://science.impf.ai/api/datasets/files/research/${encodeURIComponent(name)}`, {
      headers: { "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.9" },
    }), env, ctx);
    const res = await call(TARGETS.name);
    assert.equal(res.status, 200);
    assert.equal(await res.text(), TSV);
    assert.equal(res.headers.get("Strict-Transport-Security"), "max-age=15552000");
    assert.equal(res.headers.get("X-Relay-Upstream"), TARGETS.url);
    assert.deepEqual(upstream.map((u) => u.url), [TARGETS.url]);
    assert.deepEqual(bursts, ["datasets:203.0.113.9"]);
    const rows = limiter.core.sql.exec("SELECT k, n FROM hits ORDER BY k").toArray();
    assert.equal(rows.find((r) => r.k.startsWith("d:datasets:")).n, 1);
    assert.ok(rows.every((r) => !r.k.includes("203.0.113.9")));
    assert.equal(limiter.core.sql.exec("SELECT host FROM source_slots").toArray()[0].host, "bidd.group");
    const missing = await call("nothing.txt");
    assert.equal(missing.status, 404);
    assert.equal((await missing.json()).error.type, "not_found");
  } finally {
    globalThis.fetch = saved;
    await Promise.all(waits);
  }
});

test("the deploy check's dataset probe: one byte through the relay, its total the reviewed size; a warning unless --require-datasets", async () => {
  assert.equal(DATASET_PROBE.name, TARGETS.name);
  /**
   * science.impf.ai with the relay behind it; `route` false is a Worker without the route, `bytes` the host's size.
   * `length` false: the host's answers carry no Content-Length (as bidd.group's HEAD through the Worker did); `ranges`
   * false: the host ignores a Range and sends the whole file.
   */
  const site = ({ route = true, bytes = TARGETS.bytes, registry = REGISTRY, length = true, ranges = true } = {}) => {
    const w = setup({ registry, upstream: (_url, init) => {
      const sized = (n) => (length ? { "Content-Length": String(n) } : {});
      if (init.method === "HEAD") return new Response(null, { headers: sized(bytes) });
      if (ranges && init.headers.get("Range") === "bytes=0-0") return new Response("T", { status: 206, headers: { "Content-Range": `bytes 0-0/${bytes}`, ...sized(1) } });
      return new Response(new Uint8Array(bytes), { headers: sized(bytes) });
    } });
    const seen = [];
    const fetch = async (url, init = {}) => {
      const u = new URL(url);
      seen.push(`${init.method || "GET"} ${u.pathname}`);
      if (u.pathname === "/v1/health") return new Response(JSON.stringify({ ok: false, service: "tcmscience-studio", model: "Tao-S1" }), { headers: { "Content-Type": "application/json" } });
      if (u.pathname === "/runtime/datasets.json") return new Response(JSON.stringify(registry), { headers: { "Content-Type": "application/json" } });
      if (u.pathname.startsWith("/api/datasets/") && route) return secure(await handleDatasets(new Request(url, init), {
        ASSETS: { fetch: async () => new Response(JSON.stringify(registry), { headers: { "Content-Type": "application/json" } }) } }, w.deps));
      if (u.pathname === "/") return secure(new Response("<!doctype html>", { headers: { "Content-Type": "text/html; charset=utf-8" } }));
      return new Response(init.method === "HEAD" ? null : "Not Found\n", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8" } });
    };
    return { fetch, seen, w };
  };
  const ok = site();
  const passed0 = { status: "ok", file: `research/${TARGETS.name}`, upstream: TARGETS.url, bytes: TARGETS.bytes };
  assert.deepEqual(await checkDatasets("https://science.impf.ai", ok.fetch), passed0);
  assert.deepEqual(ok.seen, ["GET /runtime/datasets.json", `GET /api/datasets/files/research/${TARGETS.name}`]);
  assert.deepEqual([ok.w.calls[0].init.method, ok.w.calls[0].init.headers.get("Range")], ["GET", "bytes=0-0"], "the host is asked for one byte");
  await assert.rejects(checkDatasets("https://science.impf.ai", site({ route: false }).fetch), /answered 404: the Worker does not route \/api\/datasets/);
  await assert.rejects(checkDatasets("https://science.impf.ai", site({ bytes: 100 }).fetch), /\b100\b.*\b117843\b.*the file changed upstream/);
  // a host whose answers carry no Content-Length still has its size compared: the range's total says it
  assert.deepEqual(await checkDatasets("https://science.impf.ai", site({ length: false }).fetch), passed0);
  await assert.rejects(checkDatasets("https://science.impf.ai", site({ bytes: 100, length: false }).fetch), /\b100\b.*\b117843\b.*the file changed upstream/);
  // a host that ignores the range sends the file, which is counted (the relay stops one longer than the registry says)
  assert.deepEqual(await checkDatasets("https://science.impf.ai", site({ ranges: false, length: false }).fetch), passed0);
  await assert.rejects(checkDatasets("https://science.impf.ai", site({ ranges: false, length: false, bytes: 100 }).fetch), /\b100\b.*\b117843\b.*the file changed upstream/);
  await assert.rejects(checkDatasets("https://science.impf.ai", site({ ranges: false, length: false, bytes: TARGETS.bytes + 1 }).fetch), /could not be read whole.*changed upstream/);
  await assert.rejects(checkDatasets("https://science.impf.ai", site({ registry: registryOf([UNIPROT]) }).fetch), /does not offer research\/CMAUPv2\.0_download_Targets\.txt/);
  const io = (s, logs) => ({ fetch: s.fetch, sleep: async () => {}, log: (l) => logs.push(l), now: () => 0 });
  const logs = [];
  const warned = await check({ datasets: true }, io(site({ route: false }), logs));
  assert.equal(warned.datasets.status, "warning");
  assert.match(logs.join("\n"), /warning: the dataset relay probe failed/);
  await assert.rejects(check({ requireDatasets: true }, io(site({ route: false }), [])), /does not route \/api\/datasets/);
  const passed = await check({ requireDatasets: true }, io(site(), []));
  assert.equal(passed.datasets.status, "ok");
  const skipped = site();
  await check({}, io(skipped, []));
  assert.ok(!skipped.seen.some((p) => p.includes("datasets")), "no probe unless asked for");
});
