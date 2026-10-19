// The browser's research datasets: the data worker's op handlers (js/scientific/data/data.worker.js) run in node over
// the in-memory OPFS (fixtures/fake-opfs.mjs) and an in-memory HTTP server that serves byte ranges with If-Range,
// ETag and Last-Modified, answers 200 / 206 / 412 / 416, and has hosts that refuse a web page outright (a TypeError,
// as a CORS refusal looks to fetch). Then DatasetWorkspace (datasets.js) driving the same handlers through the
// worker's own message loop: the plan against runtime/datasets.json, and acquisition rows with their statuses.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { test } from "node:test";
import { fakeOpfs } from "../fixtures/fake-opfs.mjs";
import { OpfsStore } from "../../js/scientific/data/opfs-store.js";
import { RAW_FILE_SCHEMA, cancel, handle, parseContentRange, serve, strongEtag, workerEnv } from "../../js/scientific/data/data.worker.js";
import { BLOCKED, DATASETS_SCHEMA, DatasetWorkspace, FAIL, PASS, SKIPPED, UNVERIFIED } from "../../js/scientific/data/datasets.js";

const SITE = "https://science.impf.ai/";
const WORKER_URL = `${SITE}js/scientific/data/data.worker.js`;
const RELAY = "/api/datasets/files/research/";
const DIR = ["tcmstudio", "datasets", "research", "raw"];
const CHUNK = 64 * 1024;
const sha256 = (b) => createHash("sha256").update(b).digest("hex");
const md5 = (b) => createHash("md5").update(b).digest("hex");
const tick = () => new Promise((r) => setImmediate(r));

/**
 * An HTTP server in memory. serve(url, body, options): etag, lastModified, ranges (false: ignores Range),
 * exposeRange (false: the 206 has no readable Content-Range), wrongRange, mismatch412 (If-Range mismatch → 412),
 * contentLength (false: none), status (always this), failAfter (bytes, then the connection drops), onChunk(sent),
 * redirectedTo (the answer came from this URL after a redirect).
 * refuse(host): every request to it throws TypeError, as fetch does for a CORS refusal.
 */
function fakeNet() {
  const resources = new Map();
  const refused = new Set();
  const requests = [];
  const net = {
    requests,
    serve(url, body, options = {}) {
      resources.set(new URL(url).href, { body, ...options });
    },
    refuse(host) {
      refused.add(host);
    },
    async fetch(url, init = {}) {
      const u = new URL(url);
      const headers = Object.fromEntries(Object.entries(init.headers || {}).map(([k, v]) => [k.toLowerCase(), v]));
      requests.push({ url: u.href, headers, mode: init.mode ?? null });
      if (init.signal?.aborted) throw new DOMException("aborted", "AbortError");
      if (refused.has(u.host)) throw new TypeError("Failed to fetch");
      const r = resources.get(u.href);
      if (!r) return new Response("not found", { status: 404 });
      if (r.status) return new Response("no", { status: r.status });
      const total = r.body.length;
      let start = 0;
      let status = 200;
      const range = /^bytes=(\d+)-$/.exec(headers.range || "");
      if (range && r.ranges !== false) {
        const ifRange = headers["if-range"];
        const matches = !ifRange || ifRange === r.etag || ifRange === r.lastModified;
        if (!matches && r.mismatch412) return new Response("", { status: 412 });
        if (matches) {
          start = Number(range[1]);
          if (start >= total) return new Response("", { status: 416, headers: { "content-range": `bytes */${total}` } });
          status = 206;
        }
      }
      const h = new Headers();
      if (r.etag) h.set("etag", r.etag);
      if (r.lastModified) h.set("last-modified", r.lastModified);
      if (r.contentLength !== false) h.set("content-length", String(total - start));
      if (status === 206 && r.exposeRange !== false) h.set("content-range", r.wrongRange ? `bytes ${start + 1}-${total - 1}/${total}` : `bytes ${start}-${total - 1}/${total}`);
      const res = new Response(bodyStream(r.body.subarray(start), start, r, init.signal), { status, headers: h });
      if (r.redirectedTo) {
        // what fetch reports after following a redirect: the request URL is not where the answer came from
        Object.defineProperty(res, "redirected", { value: true });
        Object.defineProperty(res, "url", { value: r.redirectedTo });
      }
      return res;
    },
  };
  return net;
}

function bodyStream(bytes, offset, r, signal) {
  let at = 0;
  return new ReadableStream({
    start(controller) {
      signal?.addEventListener("abort", () => { try { controller.error(new DOMException("aborted", "AbortError")); } catch { /* closed */ } }, { once: true });
    },
    async pull(controller) {
      await tick();
      if (signal?.aborted) return;
      if (at >= bytes.length) { controller.close(); return; }
      if (r.failAfter !== undefined && offset + at >= r.failAfter) { controller.error(new TypeError("network error")); return; }
      const piece = bytes.slice(at, at + CHUNK);
      at += piece.length;
      controller.enqueue(piece);
      r.onChunk?.(offset + at);
    },
  });
}

async function setup(options = {}) {
  const fake = fakeOpfs(options);
  const { store } = await OpfsStore.open({ storage: fake.storage });
  const net = fakeNet();
  const events = [];
  const env = workerEnv({ store, fetch: net.fetch, post: (m) => events.push(JSON.parse(m)), baseUrl: WORKER_URL });
  let seq = 0;
  const call = (op, payload, id = ++seq) => handle({ id, op, payload }, env);
  return { fake, store, net, env, events, call };
}

const rowFor = (name, body, extra = {}) => ({
  name, url: `https://bidd.group/NPASS/downloadFiles/${name}`, host: "bidd.group",
  bytes: body.length, checksum: `sha256:${sha256(body)}`, reviewed: true, relay: true, ...extra,
});
const get = (call, row, transport = "direct", id) => call("download", { file: row, dir: DIR, transport, relayBase: RELAY }, id);
const nothingKept = async (store, name) => {
  assert.equal(await store.exists([...DIR, name]), false, "no file");
  assert.equal(await store.exists([...DIR, ".part", name]), false, "no partial file");
  assert.equal(await store.exists([...DIR, ".part", `${name}.json`]), false, "no identity record");
  assert.equal(await store.exists([...DIR, ".meta", `${name}.json`]), false, "no record");
};

// ---------------------------------------------------------------------------------------------- the worker's ops

test("a fresh download: written while hashed, moved to its name, and its record says how it came and was checked", async () => {
  const { store, net, call, events, fake } = await setup();
  const body = randomBytes(5 * CHUNK + 123);
  const row = rowFor("NPASS3.0_target.txt", body);
  net.serve(row.url, body, { etag: '"v1"', lastModified: "Tue, 07 Oct 2025 00:00:00 GMT" });
  const reply = await get(call, row);
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  const r = reply.result;
  assert.equal(r.reused, false);
  assert.deepEqual(r.attempts, [{ transport: "direct", ok: true, error: null }]);
  assert.deepEqual(r.path, [...DIR, row.name]);
  assert.deepEqual(await store.readBytes([...DIR, row.name]), new Uint8Array(body));
  const meta = await store.readJson([...DIR, ".meta", `${row.name}.json`]);
  assert.equal(meta.schema, RAW_FILE_SCHEMA);
  assert.equal(meta.name, row.name);
  assert.equal(meta.bytes, body.length);
  assert.equal(meta.url, row.url);
  assert.equal(meta.route, "download");
  assert.equal(meta.transport, "direct");
  assert.equal(meta.etag, '"v1"');
  assert.equal(meta.last_modified, "Tue, 07 Oct 2025 00:00:00 GMT");
  assert.match(meta.retrieved_at, /^\d{4}-\d\d-\d\dT/);
  assert.equal(meta.sha256, sha256(body));
  assert.equal(meta.md5, undefined, "MD5 only when the reviewed checksum is md5");
  assert.equal(meta.checksum_ok, true);
  assert.equal(meta.verified, "checksum");
  assert.equal(meta.resumed_from, null);
  assert.deepEqual(meta.restarts, []);
  assert.equal(net.requests.length, 1);
  assert.equal(net.requests[0].mode, "cors");
  assert.equal(net.requests[0].headers.range, undefined);
  assert.deepEqual(await store.list([...DIR, ".part"]), [], "the partial file and its identity record are gone");
  assert.deepEqual(fake.fs.openHandles(), []);
  const progress = events.filter((e) => e.type === "event" && e.event.kind === "progress").map((e) => e.event);
  assert.ok(progress.length >= 2);
  assert.deepEqual(progress.at(-1), { kind: "progress", file: row.name, bytes: body.length, total: body.length, phase: "download" });
  assert.ok(events.every((e) => e.id === 1));

  // asked again: the kept file is the reviewed one, nothing is fetched
  const again = await get(call, row);
  assert.equal(again.result.reused, true);
  assert.equal(net.requests.length, 1);
});

test("a cancel mid-download keeps a resumable partial file; the resume joins it with Range and If-Range", async () => {
  const { store, net, env, call, fake } = await setup();
  const body = randomBytes(8 * CHUNK + 5);
  const row = rowFor("NPASS3.0_activities.txt", body);
  net.serve(row.url, body, { etag: '"a1"', onChunk: (sent) => { if (sent >= 3 * CHUNK) cancel(env, 7); } });
  const stopped = await get(call, row, "direct", 7);
  assert.equal(stopped.ok, false);
  assert.equal(stopped.error.type, "cancelled");
  assert.equal(stopped.error.kept_part, true);
  const have = stopped.error.have;
  assert.ok(have >= 3 * CHUNK && have < body.length, `kept ${have} bytes`);
  assert.equal((await store.stat([...DIR, ".part", row.name])).bytes, have);
  const side = await store.readJson([...DIR, ".part", `${row.name}.json`]);
  assert.deepEqual({ url: side.url, etag: side.etag, total: side.total, transport: side.transport }, { url: row.url, etag: '"a1"', total: body.length, transport: "direct" });
  assert.equal(await store.exists([...DIR, row.name]), false, "nothing is kept under the file's own name");
  assert.deepEqual(fake.fs.openHandles(), []);

  net.serve(row.url, body, { etag: '"a1"' });
  const resumed = await get(call, row);
  assert.equal(resumed.ok, true, JSON.stringify(resumed.error));
  assert.deepEqual(net.requests.at(-1).headers, { range: `bytes=${have}-`, "if-range": '"a1"' });
  assert.equal(resumed.result.resumed_from, have);
  assert.equal(resumed.result.resume_validator, "etag");
  assert.equal(resumed.result.sha256, sha256(body), "the joined file is hashed whole");
  assert.equal(resumed.result.verified, "checksum");
  assert.deepEqual(await store.readBytes([...DIR, row.name]), new Uint8Array(body));
});

test("the stop flag of the interrupt buffer cancels between chunks, as a cancel message does", async () => {
  const { net, env, call } = await setup();
  const body = randomBytes(6 * CHUNK);
  const row = rowFor("NPASS3.0_structure.txt", body);
  env.interrupt = new Uint8Array(new SharedArrayBuffer(1));
  net.serve(row.url, body, { etag: '"s"', onChunk: (sent) => { if (sent >= 2 * CHUNK) Atomics.store(env.interrupt, 0, 2); } });
  const reply = await get(call, row);
  assert.equal(reply.error.type, "cancelled");
  assert.equal(reply.error.kept_part, true);
  Atomics.store(env.interrupt, 0, 0);
  env.cancelled.add(99);
  assert.equal((await get(call, row, "direct", 99)).error.type, "cancelled", "a cancel for an op still queued stops it at its start");
});

test("If-Range no longer matching: the server's whole new file (200), or its 412, restarts from byte 0 — recorded", async () => {
  for (const mismatch412 of [false, true]) {
    const { store, net, env, call } = await setup();
    const old = randomBytes(6 * CHUNK);
    const now = randomBytes(6 * CHUNK);
    const row = rowFor("CMAUPv2.0_download_Targets.txt", now); // the registry reviews the current release
    net.serve(row.url, old, { etag: '"old"', onChunk: (sent) => { if (sent >= 2 * CHUNK) cancel(env, 1); } });
    assert.equal((await get(call, row, "direct", 1)).error.type, "cancelled");
    net.serve(row.url, now, { etag: '"new"', mismatch412 });
    const reply = await get(call, row);
    assert.equal(reply.ok, true, JSON.stringify(reply.error));
    assert.equal(reply.result.resumed_from, null);
    assert.match(reply.result.restarts.join("\n"), mismatch412 ? /HTTP 412 .*dropped and the download restarted from byte 0/ : /sent the whole file .*If-Range did not match/);
    assert.equal(net.requests.at(-1).headers.range, mismatch412 ? undefined : `bytes=${2 * CHUNK}-`);
    assert.deepEqual(await store.readBytes([...DIR, row.name]), new Uint8Array(now));
    assert.equal(reply.result.etag, '"new"');
  }
});

test("a 206 that cannot be joined (Content-Range unreadable, or another range) drops the part and restarts without Range", async () => {
  for (const [options, why] of [[{ exposeRange: false }, /Content-Range is not readable/], [{ wrongRange: true }, /not bytes \d+-<last>\/<total>/]]) {
    const { store, net, env, call } = await setup();
    const body = randomBytes(5 * CHUNK);
    const row = rowFor("CMAUPv2.0_download_Plants.txt", body);
    net.serve(row.url, body, { etag: '"p"', onChunk: (sent) => { if (sent >= CHUNK) cancel(env, 1); } });
    await get(call, row, "direct", 1);
    net.serve(row.url, body, { etag: '"p"', ...options });
    const reply = await get(call, row);
    assert.equal(reply.ok, true, JSON.stringify(reply.error));
    assert.match(reply.result.restarts.join("\n"), why);
    const [first, second] = net.requests.slice(-2);
    assert.match(first.headers.range, /^bytes=\d+-$/);
    assert.equal(second.headers.range, undefined, "the restart asks for the whole file");
    assert.equal(reply.result.resumed_from, null);
    assert.deepEqual(await store.readBytes([...DIR, row.name]), new Uint8Array(body));
  }
});

test("no validator and no reviewed checksum: a partial file is not joined to anything; a weak ETag gives way to Last-Modified", async () => {
  {
    const { net, env, call } = await setup();
    const body = randomBytes(4 * CHUNK);
    const row = rowFor("UniProt2Reactome.txt", body, { url: "https://reactome.org/download/current/UniProt2Reactome.txt", host: "reactome.org", checksum: null, bytes: null });
    net.serve(row.url, body, { onChunk: (sent) => { if (sent >= CHUNK) cancel(env, 1); } });
    await get(call, row, "direct", 1);
    net.serve(row.url, body, {});
    const reply = await get(call, row);
    assert.equal(net.requests.at(-1).headers.range, undefined);
    assert.match(reply.result.restarts.join("\n"), /identity cannot be shown/);
    assert.equal(reply.result.verified, "none");
    assert.equal(reply.result.checksum_ok, null);
    assert.equal(reply.result.declared_bytes, body.length);
  }
  {
    const { net, env, call } = await setup();
    const body = randomBytes(4 * CHUNK);
    const lm = "Mon, 13 Apr 2026 08:00:00 GMT";
    const row = rowFor("9606.protein.info.v12.0.txt.gz", body);
    net.serve(row.url, body, { etag: 'W/"weak"', lastModified: lm, onChunk: (sent) => { if (sent >= CHUNK) cancel(env, 1); } });
    await get(call, row, "direct", 1);
    net.serve(row.url, body, { etag: 'W/"weak"', lastModified: lm });
    const reply = await get(call, row);
    assert.equal(net.requests.at(-1).headers["if-range"], lm);
    assert.equal(reply.result.etag, null, "a weak ETag is not kept");
    assert.equal(reply.result.resume_validator, "last-modified");
  }
});

test("a partial file that is already whole is checked as it is: by the reviewed checksum, or by the server's 416", async () => {
  {
    const { store, net, call } = await setup();
    const body = randomBytes(3 * CHUNK);
    const row = rowFor("NPASS3.0_species.txt", body);
    await store.writeBytes([...DIR, ".part", row.name], body);
    await store.writeJson([...DIR, ".part", `${row.name}.json`], { url: row.url, etag: '"w"', last_modified: null, total: body.length, transport: "direct" });
    const reply = await get(call, row);
    assert.equal(reply.ok, true, JSON.stringify(reply.error));
    assert.equal(net.requests.length, 0, "nothing left to ask for");
    assert.equal(reply.result.resume_validator, "checksum");
  }
  {
    const { store, net, call } = await setup();
    const body = randomBytes(3 * CHUNK);
    const row = rowFor("NPASS3.0_species.txt", body, { checksum: null });
    net.serve(row.url, body, { etag: '"w"' });
    await store.writeBytes([...DIR, ".part", row.name], body);
    await store.writeJson([...DIR, ".part", `${row.name}.json`], { url: row.url, etag: '"w"', last_modified: null, total: body.length, transport: "direct" });
    const reply = await get(call, row);
    assert.equal(reply.ok, true, JSON.stringify(reply.error));
    assert.deepEqual(net.requests[0].headers, { range: `bytes=${body.length}-`, "if-range": '"w"' });
    assert.equal(reply.result.verified, "size");
    assert.equal(reply.result.sha256, sha256(body));
  }
});

test("more than the reviewed size is refused, from the declared length or while streaming, and nothing is kept", async () => {
  for (const contentLength of [true, false]) {
    const { store, net, call } = await setup();
    const body = randomBytes(4 * CHUNK);
    const row = rowFor("NPASS3.0_naturalproducts.txt", body, { bytes: 2 * CHUNK + 1, checksum: null });
    net.serve(row.url, body, { etag: '"o"', contentLength });
    const reply = await get(call, row);
    assert.equal(reply.ok, false);
    assert.equal(reply.error.type, "oversize");
    assert.match(reply.error.message, contentLength ? /says the file is 262144 bytes, more than the reviewed 131073/ : /sent at least \d+ bytes, more than the reviewed 131073/);
    await nothingKept(store, row.name);
  }
});

test("a checksum that does not match keeps nothing (error type checksum); a wrong size neither", async () => {
  const { store, net, call } = await setup();
  const body = randomBytes(3 * CHUNK);
  const row = rowFor("Ingredients_All.txt", body, { checksum: `sha256:${sha256(Buffer.from("another release"))}` });
  net.serve(row.url, body, { etag: '"i"' });
  const reply = await get(call, row);
  assert.equal(reply.error.type, "checksum");
  assert.match(reply.error.message, /not the reviewed sha256:.*nothing was kept/);
  assert.equal(reply.error.kept_part, false);
  await nothingKept(store, row.name);

  const short = rowFor("Targets.txt", body, { bytes: body.length + 10, checksum: null });
  net.serve(short.url, body, { etag: '"t"' });
  const r2 = await get(call, short);
  assert.equal(r2.error.type, "size");
  await nothingKept(store, short.name);
});

test("an md5 reviewed checksum (LOTUS) is checked by md5, with SHA-256 recorded beside it, in the same pass", async () => {
  const { net, call } = await setup();
  const body = randomBytes(5 * CHUNK + 1);
  const row = rowFor("260413_frozen.csv.gz", body, { url: "https://zenodo.org/api/records/19360665/files/260413_frozen.csv.gz/content", host: "zenodo.org", checksum: `md5:${md5(body)}` });
  net.serve(row.url, body, { etag: '"md5:z"' });
  const reply = await get(call, row);
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  assert.equal(reply.result.md5, md5(body));
  assert.equal(reply.result.sha256, sha256(body));
  assert.equal(reply.result.verified, "checksum");
});

test("transport auto: a host that refuses the page (TypeError) is tried through the relay, and both attempts are reported", async () => {
  const { store, net, call } = await setup();
  const body = randomBytes(2 * CHUNK + 9);
  const row = rowFor("NPASS3.0_target.txt", body);
  net.refuse("bidd.group");
  const relayed = `${SITE}api/datasets/files/research/${row.name}`;
  net.serve(relayed, body, { etag: '"r"' });
  const reply = await get(call, row, "auto");
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  assert.equal(reply.result.attempts.length, 2);
  assert.deepEqual(reply.result.attempts[0].transport, "direct");
  assert.equal(reply.result.attempts[0].ok, false);
  assert.match(reply.result.attempts[0].error, /^network: bidd\.group could not be reached \(TypeError: Failed to fetch\).*CORS/);
  assert.deepEqual(reply.result.attempts[1], { transport: "relay", ok: true, error: null });
  assert.equal(reply.result.route, "relay");
  assert.equal(reply.result.transport, "relay");
  assert.equal((await store.readJson([...DIR, ".meta", `${row.name}.json`])).transport, "relay");
  assert.deepEqual(net.requests.map((r) => [r.url, r.mode]), [[row.url, "cors"], [relayed, null]]);

  // a host that answers — whatever it answers — is not worked around
  const { net: net2, call: call2 } = await setup();
  net2.serve(row.url, body, { status: 403 });
  net2.serve(relayed, body, {});
  const refused = await get(call2, row, "auto");
  assert.equal(refused.error.type, "http");
  assert.equal(refused.error.status, 403);
  assert.equal(refused.error.attempts.length, 1);
  assert.equal(net2.requests.length, 1);

  // direct only: the TypeError is the answer
  const { net: net3, call: call3 } = await setup();
  net3.refuse("bidd.group");
  const direct = await get(call3, row, "direct");
  assert.equal(direct.error.type, "network");
  assert.deepEqual(direct.error.attempts.map((a) => a.transport), ["direct"]);
});

test("an answer redirected off the reviewed host, or down to http, is refused and nothing of it is kept", async () => {
  const { store, net, call } = await setup();
  const body = randomBytes(CHUNK);
  const row = rowFor("NPASS3.0_target.txt", body);
  net.serve(row.url, body, { etag: '"r"', redirectedTo: "https://mirror.example.org/x.txt" });
  const moved = await get(call, row);
  assert.equal(moved.error.type, "redirect");
  assert.match(moved.error.message, /redirected to mirror\.example\.org, which the registry does not list for this file \(bidd\.group\)/);
  await nothingKept(store, row.name);
  net.serve(row.url, body, { etag: '"r"', redirectedTo: "http://bidd.group/x.txt" });
  assert.match((await get(call, row)).error.message, /from https down to http/);
  // a host the registry lists for this file, and a move within the same host, are followed
  net.serve(row.url, body, { etag: '"r"', redirectedTo: "https://mirror.example.org/x.txt" });
  assert.equal((await get(call, { ...row, redirect_hosts: ["mirror.example.org"] })).ok, true);
  await store.remove([...DIR, row.name]);
  await store.remove([...DIR, ".meta", `${row.name}.json`]);
  net.serve(row.url, body, { etag: '"r"', redirectedTo: "https://bidd.group/NPASS/v3/x.txt" });
  assert.equal((await get(call, row)).ok, true);
});

test("a connection that drops mid-body keeps the partial file for a resume", async () => {
  const { store, net, call } = await setup();
  const body = randomBytes(5 * CHUNK);
  const row = rowFor("NPASS3.0_activities.txt", body);
  net.serve(row.url, body, { etag: '"d"', failAfter: 2 * CHUNK });
  const reply = await get(call, row);
  assert.equal(reply.error.type, "network");
  assert.match(reply.error.message, /broke off at 131072 bytes.*kept for a resume/);
  assert.equal(reply.error.kept_part, true);
  assert.equal((await store.stat([...DIR, ".part", row.name])).bytes, 2 * CHUNK);
});

test("a file under the final name without a matching record is removed and fetched again", async () => {
  const { store, net, call } = await setup();
  const body = randomBytes(2 * CHUNK);
  const row = rowFor("Plants.txt", body);
  await store.writeBytes([...DIR, row.name], body.subarray(0, 1000)); // a copy cut short by a closed tab
  net.serve(row.url, body, { etag: '"x"' });
  const reply = await get(call, row);
  assert.equal(reply.ok, true);
  assert.match(reply.result.restarts[0], /already here was removed: it has no record/);
  assert.deepEqual(await store.readBytes([...DIR, row.name]), new Uint8Array(body));
});

test("import: a chosen File streamed into OPFS, hashed and checked like a download; a wrong one keeps nothing", async () => {
  const { store, call, fake } = await setup();
  const body = randomBytes(3 * CHUNK + 77);
  const row = rowFor("260413_frozen.csv.gz", body, { url: "https://zenodo.org/x/260413_frozen.csv.gz", host: "zenodo.org", checksum: `md5:${md5(body)}` });
  const file = new File([body], "lotus-download.csv.gz", { lastModified: Date.parse("2026-04-13T08:00:00Z") });
  const reply = await call("import", { file, name: row.name, dir: DIR, expect: row });
  assert.equal(reply.ok, true, JSON.stringify(reply.error));
  const meta = await store.readJson([...DIR, ".meta", `${row.name}.json`]);
  assert.equal(meta.route, "import");
  assert.equal(meta.transport, null);
  assert.equal(meta.url, row.url);
  assert.equal(meta.etag, null);
  assert.deepEqual(meta.source, { name: "lotus-download.csv.gz", bytes: body.length, last_modified: "2026-04-13T08:00:00.000Z" });
  assert.equal(meta.md5, md5(body));
  assert.equal(meta.verified, "checksum");
  assert.deepEqual(await store.readBytes([...DIR, row.name]), new Uint8Array(body));
  assert.deepEqual(fake.fs.openHandles(), []);

  const other = new File([randomBytes(body.length)], "wrong.csv.gz");
  const bad = await call("import", { file: other, name: "x.csv.gz", dir: DIR, expect: { ...row, name: "x.csv.gz" } });
  assert.equal(bad.error.type, "checksum");
  await nothingKept(store, "x.csv.gz");
  const sized = await call("import", { file: new File([body.subarray(5)], "short"), name: "y.csv.gz", dir: DIR, expect: { ...row, name: "y.csv.gz" } });
  assert.equal(sized.error.type, "size");
  await nothingKept(store, "y.csv.gz");
  const named = await call("import", { file, name: "z", dir: DIR, expect: row });
  assert.equal(named.error.type, "bad_arguments");
});

test("hash, stat, list, remove and estimate ops; bad requests are refused with their reason", async () => {
  const { store, call } = await setup();
  const body = randomBytes(CHUNK + 3);
  await store.writeBytes([...DIR, "a.txt"], body);
  assert.equal((await call("hash", { path: [...DIR, "a.txt"], algorithm: "sha256" })).result, sha256(body));
  assert.deepEqual((await call("hash", { path: [...DIR, "a.txt"], algorithm: ["md5", "sha256"] })).result, { md5: md5(body), sha256: sha256(body) });
  assert.equal((await call("hash", { path: [...DIR, "none"] })).error.type, "not_found");
  assert.equal((await call("stat", { path: [...DIR, "a.txt"] })).result.bytes, body.length);
  assert.deepEqual((await call("list", { path: DIR })).result, [{ name: "a.txt", kind: "file" }]);
  assert.equal((await call("remove", { path: [...DIR, "a.txt"] })).result, true);
  assert.equal((await call("estimate", {})).result.usage, 0);
  assert.equal((await call("nope", {})).error.type, "bad_request");
  assert.equal((await call("stat", { path: [...DIR, ".."] })).error.type, "bad_arguments");
  assert.equal((await call("download", { file: { name: "../x", url: "https://h/x" }, dir: DIR })).error.type, "bad_arguments");
  assert.equal((await call("download", { file: { name: "x", url: "ftp://h/x" }, dir: DIR })).error.type, "bad_arguments");
  assert.equal((await call("download", { file: { name: "x", url: "https://h/x", checksum: "sha1:abc" }, dir: DIR, transport: "direct" })).error.type, "bad_arguments");
  assert.equal((await call("download", { file: { name: "x", url: "https://h/x" }, dir: DIR, transport: "auto" })).error.type, "bad_arguments", "auto needs the relay path");
  assert.equal((await call("download", { file: { name: "x", url: "https://h/x" }, dir: DIR, transport: "pigeon" })).error.type, "bad_arguments");

  const none = workerEnv({ storage: fakeOpfs({ fail: "SecurityError" }).storage, fetch: async () => {} });
  const unavailable = await handle({ id: 1, op: "stat", payload: { path: DIR } }, none);
  assert.equal(unavailable.error.type, "unavailable");
  assert.match(unavailable.error.message, /not available here/);
});

test("the header helpers: Content-Range parsed only when exact, weak ETags dropped", () => {
  assert.deepEqual(parseContentRange("bytes 100-199/200"), { start: 100, end: 199, total: 200 });
  for (const bad of [null, "", "bytes */200", "bytes 100-199/*", "bytes 200-100/300", "bytes 0-300/300", "items 0-1/2"]) assert.equal(parseContentRange(bad), null, String(bad));
  assert.equal(strongEtag('"x"'), '"x"');
  assert.equal(strongEtag('W/"x"'), null);
  assert.equal(strongEtag(null), null);
});

test("the worker's message loop: JSON requests one at a time, events and replies as JSON, cancel and interrupt at once", async () => {
  const { store, net } = await setup();
  const out = [];
  const scope = { postMessage: (m) => out.push(JSON.parse(m)), onmessage: null };
  const env = workerEnv({ store, fetch: net.fetch, post: (m) => scope.postMessage(m), baseUrl: WORKER_URL });
  serve(scope, env);
  const body = randomBytes(6 * CHUNK);
  const row = rowFor("NPASS3.0_target.txt", body);
  net.serve(row.url, body, { etag: '"m"', onChunk: (sent) => { if (sent >= 2 * CHUNK) scope.onmessage({ data: { type: "cancel", id: 1 } }); } });
  scope.onmessage({ data: JSON.stringify({ id: 1, op: "download", payload: { file: row, dir: DIR, transport: "direct" } }) });
  scope.onmessage({ data: JSON.stringify({ id: 2, op: "stat", payload: { path: [...DIR, ".part", row.name] } }) });
  scope.onmessage({ data: "not json" });
  const sab = new SharedArrayBuffer(1);
  scope.onmessage({ data: { type: "interrupt-buffer", buffer: sab } });
  assert.ok(env.interrupt instanceof Uint8Array);
  while (out.filter((m) => m.ok !== undefined).length < 2) await tick();
  const replies = out.filter((m) => m.ok !== undefined);
  assert.deepEqual(replies.map((m) => m.id), [1, 2], "in arrival order");
  assert.equal(replies[0].error.type, "cancelled");
  assert.equal(replies[1].result.bytes, replies[0].error.have, "the stat ran after the download stopped");
  assert.ok(out.some((m) => m.type === "event" && m.id === 1));
});

// ------------------------------------------------------------------------------------------- DatasetWorkspace

/** A Worker class that runs data.worker.js's own message loop in this process, over the same OPFS. */
function inProcessWorker({ store, fetch }) {
  class InProcessWorker {
    static made = [];
    constructor(url, options) {
      this.url = String(url);
      this.options = options;
      this.posted = [];
      this.terminated = false;
      this.scope = { postMessage: (m) => setImmediate(() => { if (!this.terminated) this.onmessage?.({ data: m }); }), onmessage: null };
      serve(this.scope, workerEnv({ store, fetch, post: (m) => this.scope.postMessage(m), baseUrl: WORKER_URL }));
      InProcessWorker.made.push(this);
    }
    postMessage(data) {
      this.posted.push(data);
      setImmediate(() => this.scope.onmessage({ data }));
    }
    terminate() {
      this.terminated = true;
    }
  }
  return InProcessWorker;
}

const bodies = { a: randomBytes(3 * CHUNK + 1), b: randomBytes(2 * CHUNK + 2), c: randomBytes(CHUNK + 3), d: randomBytes(1000), e: randomBytes(2000) };
function registryDoc() {
  const row = (name, host, body, extra = {}) => ({
    name, url: `https://${host}/files/${name}`, host, bytes: body.length, checksum: `sha256:${sha256(body)}`, reviewed: true,
    project: "x", fmt: null, license: "CC-BY-4.0", redistribution: "with attribution (CC BY 4.0)", relay: true, ...extra,
  });
  return {
    schema: DATASETS_SCHEMA,
    groups: {
      research: {
        title: { zh: "网络药理学研究数据", en: "Network-pharmacology research data" }, sources: ["npass", "cmaup", "lotus"], network: true,
        files: [
          row("a.txt", "bidd.group", bodies.a),
          row("b.csv.gz", "zenodo.org", bodies.b, { checksum: `md5:${md5(bodies.b)}` }),
          row("c.txt", "reactome.org", bodies.c, { bytes: null, checksum: null, reviewed: false }),
          row("d.txt", "stringdb-downloads.org", bodies.d),
          row("e.txt", "rest.uniprot.org", bodies.e),
        ],
      },
    },
  };
}

async function workspace({ doc = registryDoc(), Worker: W, options = {} } = {}) {
  const fake = fakeOpfs();
  const { store } = await OpfsStore.open({ storage: fake.storage });
  const net = fakeNet();
  net.serve(`${SITE}runtime/datasets.json`, new TextEncoder().encode(JSON.stringify(doc)), {});
  const files = doc.groups?.research?.files || [];
  for (const f of files) if (bodies[f.name[0]]) net.serve(f.url, bodies[f.name[0]], { etag: `"${f.name}"` });
  const Worker = W === undefined ? inProcessWorker({ store, fetch: net.fetch }) : W;
  const ws = new DatasetWorkspace({ store, fetch: net.fetch, Worker, siteUrl: SITE, ...options });
  return { ws, store, net, Worker, fake };
}

test("the plan: the registry against OPFS — missing files, what they weigh (null when a size is unknown), their hosts", async () => {
  const { ws } = await workspace();
  assert.deepEqual(ws.rawDir(), DIR);
  const reg = await ws.registry();
  assert.equal(reg.group, "research");
  assert.deepEqual(reg.files.map((f) => f.name), ["a.txt", "b.csv.gz", "c.txt", "d.txt", "e.txt"]);
  const plan = await ws.plan();
  assert.deepEqual(plan.missing, ["a.txt", "b.csv.gz", "c.txt", "d.txt", "e.txt"]);
  assert.equal(plan.download_bytes, null, "c.txt has no reviewed size");
  assert.deepEqual(plan.hosts, ["bidd.group", "reactome.org", "rest.uniprot.org", "stringdb-downloads.org", "zenodo.org"]);
  assert.ok(plan.files.every((f) => f.present === false && f.meta === null && f.reason === "not here" && f.partial_bytes === 0));
});

test("acquire: PASS by checksum (sha256 or md5), UNVERIFIED with nothing reviewed, FAIL for a gone URL, BLOCKED for a refusal", async () => {
  const { ws, net, Worker } = await workspace();
  net.serve("https://stringdb-downloads.org/files/d.txt", new Uint8Array(0), { status: 404 });
  net.serve("https://rest.uniprot.org/files/e.txt", new Uint8Array(0), { status: 403 });
  const seen = [];
  const rows = await ws.acquire({ transport: "direct", onProgress: (p) => seen.push(p) });
  const by = Object.fromEntries(rows.map((r) => [r.file, r]));
  assert.deepEqual(rows.map((r) => [r.file, r.status]), [["a.txt", PASS], ["b.csv.gz", PASS], ["c.txt", UNVERIFIED], ["d.txt", FAIL], ["e.txt", BLOCKED]]);
  assert.equal(by["a.txt"].route, "download");
  assert.equal(by["a.txt"].transport, "direct");
  assert.equal(by["a.txt"].sha256, sha256(bodies.a));
  assert.match(by["a.txt"].reason, /downloaded from its host, verified by checksum/);
  assert.equal(by["b.csv.gz"].md5, md5(bodies.b));
  assert.match(by["c.txt"].reason, /no reviewed checksum or size: its SHA-256 is recorded for review/);
  assert.match(by["d.txt"].reason, /HTTP 404: the reviewed URL is out of date/);
  assert.match(by["e.txt"].reason, /HTTP 403/);
  assert.ok(rows.every((r) => typeof r.duration_s === "number"));
  assert.ok(seen.some((p) => p.file === "a.txt" && p.index === 0 && p.count === 5));
  assert.equal(Worker.made.length, 1, "one worker for all files");
  assert.equal(Worker.made[0].options.type, "module");
  assert.ok(Worker.made[0].url.endsWith("/js/scientific/data/data.worker.js"));
  assert.ok(Worker.made[0].posted.every((m) => typeof m === "string"), "requests travel as JSON strings");

  const plan = await ws.plan();
  assert.deepEqual(plan.missing, ["d.txt", "e.txt"]);
  assert.equal(plan.download_bytes, bodies.d.length + bodies.e.length);
  assert.deepEqual(plan.hosts, ["rest.uniprot.org", "stringdb-downloads.org"]);
  const a = plan.files.find((f) => f.name === "a.txt");
  assert.equal(a.present, true);
  assert.equal(a.status, PASS);
  assert.equal(a.meta.sha256, sha256(bodies.a));
  assert.equal(plan.files.find((f) => f.name === "c.txt").status, UNVERIFIED);

  // what is here already is not fetched again
  const before = net.requests.length;
  const [again] = await ws.acquire({ names: ["a.txt"] });
  assert.equal(again.status, PASS);
  assert.equal(again.route, "cache");
  assert.match(again.reason, /already in this browser's storage \(downloaded from its host on .*\); verified by checksum/);
  assert.equal(net.requests.length, before);
});

test("acquire: a chosen File is imported instead of downloaded; unknown names are refused before anything runs", async () => {
  const { ws, net, Worker } = await workspace();
  const rows = await ws.acquire({ names: ["d.txt"], files: new Map([["d.txt", new File([bodies.d], "my-copy.txt")]]) });
  assert.equal(rows[0].status, PASS);
  assert.equal(rows[0].route, "import");
  assert.equal(rows[0].transport, null);
  assert.equal(net.requests.filter((r) => r.url.includes("d.txt")).length, 0);
  assert.equal(typeof Worker.made[0].posted[0], "object", "a File travels structured, not as JSON");
  await assert.rejects(ws.acquire({ names: ["zzz.txt"] }), (e) => e.type === "bad_arguments" && /zzz\.txt/.test(e.message));
  await assert.rejects(ws.acquire({ files: { "q.txt": new File(["x"], "q") } }), (e) => e.type === "bad_arguments");
  await assert.rejects(ws.acquire({ transport: "carrier" }), (e) => e.type === "bad_arguments");
});

test("acquire: transport auto falls back to the relay only for a host the page cannot reach, and says so; relay:false is honoured", async () => {
  const doc = registryDoc();
  doc.groups.research.files = doc.groups.research.files.slice(0, 1).concat([{ ...doc.groups.research.files[3], relay: false }]);
  const { ws, net } = await workspace({ doc });
  net.refuse("bidd.group");
  net.serve(`${SITE}api/datasets/files/research/a.txt`, bodies.a, { etag: '"ra"' });
  net.refuse("stringdb-downloads.org");
  const rows = await ws.acquire({ transport: "auto" });
  assert.equal(rows[0].status, PASS);
  assert.equal(rows[0].route, "relay");
  assert.equal(rows[0].transport, "relay");
  assert.deepEqual(rows[0].attempts.map((a) => [a.transport, a.ok]), [["direct", false], ["relay", true]]);
  assert.match(rows[0].reason, /downloaded through the site's relay; the direct download could not be made \(network: .*\), so it came through the relay, verified by checksum/);
  assert.equal(rows[1].status, BLOCKED);
  assert.deepEqual(rows[1].attempts.map((a) => a.transport), ["direct"]);
  assert.match(rows[1].reason, /could not be reached.*only the direct download was tried/);
  assert.ok(!net.requests.some((r) => r.url.endsWith("/research/d.txt")), "a file the registry does not relay never goes to the relay");
  const [relayOnly] = await ws.acquire({ names: ["d.txt"], transport: "relay" });
  assert.equal(relayOnly.status, BLOCKED);
  assert.match(relayOnly.reason, /does not relay this file/);

  // a relay that answers 404 is a site without the relay, not a stale reviewed URL
  const other = await workspace();
  const [noRelay] = await other.ws.acquire({ names: ["a.txt"], transport: "relay" });
  assert.equal(noRelay.status, BLOCKED);
  assert.match(noRelay.reason, /the site's relay answered HTTP 404: the relay does not serve this file here/);
  assert.equal(noRelay.transport, "relay");
});

test("acquire: a cancel stops the running file (kept for a resume, SKIPPED) and skips the rest; the stop flag is raised when isolated", async () => {
  globalThis.crossOriginIsolated = true;
  try {
    const { ws, net, Worker } = await workspace();
    const ctl = new AbortController();
    net.serve("https://bidd.group/files/a.txt", bodies.a, { etag: '"a"', onChunk: (sent) => { if (sent >= CHUNK) ctl.abort(); } });
    const rows = await ws.acquire({ transport: "direct", signal: ctl.signal });
    assert.equal(rows[0].status, SKIPPED);
    assert.equal(rows[0].kept_part, true);
    assert.match(rows[0].reason, /^cancelled; \d+ bytes are kept for a resume$/);
    assert.ok(rows.slice(1).every((r) => r.status === SKIPPED && r.reason === "cancelled before it started"));
    const w = Worker.made[0];
    assert.equal(w.posted[0].type, "interrupt-buffer");
    assert.ok(w.posted.some((m) => m && m.type === "cancel" && m.id === 1));
    assert.equal(Atomics.load(new Uint8Array(w.posted[0].buffer), 0), 1);
    const plan = await ws.plan();
    assert.ok(plan.files.find((f) => f.name === "a.txt").partial_bytes >= CHUNK);

    // the next acquisition clears the flag and resumes the kept part
    net.serve("https://bidd.group/files/a.txt", bodies.a, { etag: '"a"' });
    const [resumed] = await ws.acquire({ names: ["a.txt"], transport: "direct" });
    assert.equal(resumed.status, PASS, resumed.reason);
    assert.ok(resumed.resumed_from >= CHUNK);
    assert.match(resumed.reason, /resumed at byte \d+ \(checked by etag\), then hashed whole/);
  } finally {
    delete globalThis.crossOriginIsolated;
  }
});

test("no Web Worker, a worker that crashes, and a registry that is missing or malformed: each said plainly", async () => {
  const { ws } = await workspace({ Worker: null });
  assert.equal(ws.support().ok, false);
  const rows = await ws.acquire({ names: ["a.txt"] });
  assert.equal(rows[0].status, BLOCKED);
  assert.match(rows[0].reason, /no Web Workers/);

  class Crashing {
    constructor() { Crashing.last = this; }
    postMessage() { setImmediate(() => this.onerror?.({ message: "out of memory" })); }
    terminate() { this.terminated = true; }
  }
  const crashed = await workspace({ Worker: Crashing });
  const [row] = await crashed.ws.acquire({ names: ["a.txt"], transport: "direct" });
  assert.equal(row.status, FAIL);
  assert.match(row.reason, /the data worker failed: out of memory/);
  assert.equal(Crashing.last.terminated, true);

  const missing = await workspace();
  missing.net.serve(`${SITE}runtime/datasets.json`, new Uint8Array(0), { status: 404 });
  await assert.rejects(missing.ws.plan(), (e) => e.type === "unavailable" && /was not built/.test(e.message) && e.need === "dataset");
  for (const [doc, why] of [[{ schema: "x" }, /not tcmstudio\.datasets\/1/], [{ schema: DATASETS_SCHEMA, groups: {} }, /no research group/],
    [{ schema: DATASETS_SCHEMA, groups: { research: { files: [{ name: "../a", url: "https://h/a" }] } } }, /is not a file name/],
    [{ schema: DATASETS_SCHEMA, groups: { research: { files: [{ name: "a", url: "https://h/a" }, { name: "a", url: "https://h/b" }] } } }, /names a twice/],
    [{ schema: DATASETS_SCHEMA, groups: { research: { files: [{ name: "a", url: "https://h/a", checksum: "sha1:00" }] } } }, /cannot be checked here/]]) {
    const bad = await workspace({ doc });
    await assert.rejects(bad.ws.registry(), (e) => e.type === "bad_registry" && why.test(e.message), JSON.stringify(doc));
  }
  assert.throws(() => new DatasetWorkspace({}), /needs an OpfsStore/);
});

test("the plan while the worker writes: a file held open reads as being written, its partial size as unknown", async () => {
  const { ws, store } = await workspace();
  await store.writeBytes([...DIR, ".part", "a.txt"], bodies.a.subarray(0, 100));
  await store.writeBytes([...DIR, "b.csv.gz"], bodies.b);
  const part = await (await store.file([...DIR, ".part", "a.txt"])).createSyncAccessHandle();
  const fin = await (await store.file([...DIR, "b.csv.gz"])).createSyncAccessHandle();
  try {
    const plan = await ws.plan();
    const by = Object.fromEntries(plan.files.map((f) => [f.name, f]));
    assert.equal(by["a.txt"].partial_bytes, null);
    assert.equal(by["b.csv.gz"].present, false);
    assert.equal(by["b.csv.gz"].reason, "it is being written");
  } finally {
    part.close();
    fin.close();
  }
  const plan = await ws.plan();
  assert.equal(plan.files.find((f) => f.name === "a.txt").partial_bytes, 100);
  assert.equal(plan.files.find((f) => f.name === "b.csv.gz").reason, "it has no record of how it was obtained");
});

test("hash(): an OPFS file digested in the worker, off the page", async () => {
  const { ws, store } = await workspace();
  await store.writeBytes([...DIR, "a.txt"], bodies.a);
  assert.equal(await ws.hash([...DIR, "a.txt"]), sha256(bodies.a));
  await assert.rejects(ws.hash([...DIR, "none.txt"]), (e) => e.type === "not_found");
  ws.terminate();
});
