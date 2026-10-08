import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { RunnerRuntime, describeConnectError, lnaNotice, lnaPermission, parsePairFragment, takePairing } from "../js/runtime/runner.js";
import { UnavailableRuntime, createRuntimes, shouldAutoConnect } from "../js/runtime/index.js";
import { toBase64Url } from "../js/core/util.js";
import { envelopeFor, fakeFetch } from "./fixtures/fakes.mjs";

const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

function runnerServer({ token = "tok", tokenRequired = true, routes = {} } = {}) {
  return fakeFetch((url, init) => {
    const u = new URL(url);
    const authed = !tokenRequired || init.headers?.["X-TCM-Token"] === token;
    if (u.pathname === "/api/health") return json({ name: "tcmstudio", version: "0.1.0", token_required: tokenRequired, paired: authed });
    if (!authed) return json({ error: { message: "token required", type: "unauthorized" } }, 401);
    const key = `${init.method || "GET"} ${u.pathname}`;
    if (routes[key]) return routes[key](u, init);
    if (key === "GET /api/info") return json({ name: "tcmstudio", version: "0.1.0", devices: [{ id: "cpu", available: true }] });
    return json({ error: { message: `no route ${key}` } }, 404);
  });
}

test("start: health without the token, info with it; status goes connecting → ready", async () => {
  const f = runnerServer();
  const r = new RunnerRuntime({ url: "http://127.0.0.1:8765/", token: "tok", fetch: f });
  const statuses = [];
  r.onStatus((s) => statuses.push(s.status));
  const info = await r.start();
  assert.equal(info.version, "0.1.0");
  assert.equal(r.status, "ready");
  assert.deepEqual(statuses, ["connecting", "ready"]);
  assert.equal(f.calls[0].url, "http://127.0.0.1:8765/api/health");
  assert.equal(f.calls[0].init.headers["X-TCM-Token"], undefined, "health needs no token");
  assert.equal(f.calls[1].init.headers["X-TCM-Token"], "tok");
  assert.match(r.label, /127\.0\.0\.1:8765/);
});

test("start without a token, or with a wrong one, is an error that asks for pairing", async () => {
  const noToken = new RunnerRuntime({ token: "", fetch: runnerServer() });
  await assert.rejects(noToken.start(), (e) => e.code === "token");
  assert.equal(noToken.status, "error");
  const wrong = new RunnerRuntime({ token: "nope", fetch: runnerServer() });
  await assert.rejects(wrong.start(), (e) => e.code === "token" && /配对|token/i.test(e.message));
  assert.equal(wrong.status, "error");
});

test("a runner that is not there: offline, with words the user can act on", async () => {
  const down = fakeFetch(() => { throw new TypeError("Failed to fetch"); });
  const r = new RunnerRuntime({ fetch: down, location: { protocol: "https:", origin: "https://science.impf.ai" }, permissions: null });
  await assert.rejects(r.start(), (e) => e.code === "not_running" && /tcmstudio serve/.test(e.message));
  assert.equal(r.status, "offline");
  const denied = new RunnerRuntime({ fetch: down, location: { protocol: "https:", origin: "https://science.impf.ai" }, permissions: { query: async ({ name }) => ({ state: name === "local-network-access" ? "denied" : undefined }) } });
  await assert.rejects(denied.start(), (e) => e.code === "lna_denied");
  assert.equal(denied.status, "error");
  const notHtml = new RunnerRuntime({ fetch: fakeFetch(() => json({ name: "something-else" })) });
  await assert.rejects(notHtml.start(), (e) => e.code === "not_runner");
});

test("describeConnectError: mixed content, timeout, unknown origin", () => {
  const page = { protocol: "https:", origin: "https://science.impf.ai" };
  assert.equal(describeConnectError(new TypeError("x"), { url: "http://192.168.1.5:8765", location: page }).code, "mixed_content");
  assert.equal(describeConnectError(new DOMException("t", "TimeoutError"), { url: "http://127.0.0.1:8765", location: page }).code, "timeout");
  const cors = describeConnectError(new TypeError("x"), { url: "http://127.0.0.1:8765", location: { protocol: "https:", origin: "https://preview.example.dev" } });
  assert.equal(cors.code, "cors");
  assert.match(cors.message, /--allow-origin https:\/\/preview\.example\.dev/);
  assert.equal(describeConnectError(new TypeError("x"), { url: "http://127.0.0.1:8765", location: { protocol: "http:", origin: "http://127.0.0.1:8765" } }).code, "not_running");
  assert.match(lnaNotice("http://127.0.0.1:8765", page), /本机上的应用/);
  assert.equal(lnaNotice("http://127.0.0.1:8765", { protocol: "http:" }), "");
});

test("lnaPermission reads whichever permission name the browser knows", async () => {
  assert.equal(await lnaPermission(null), "unknown");
  assert.equal(await lnaPermission({ query: async ({ name }) => { if (name !== "local-network-access") throw new TypeError("unknown"); return { state: "granted" }; } }), "granted");
});

test("call: POST /api/call with the context; the envelope says it ran on the runner", async () => {
  const f = runnerServer({ routes: { "POST /api/call": (u, init) => json(envelopeFor(JSON.parse(init.body).tool, undefined, { receipt: { device: "cuda:0" } })) } });
  const r = new RunnerRuntime({ token: "tok", fetch: f });
  const env = await r.call("tcm_herb", { name: "甘草" }, { project_id: "p1", conversation_id: "c1", approvals: ["first_runner_call"] });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "runner");
  assert.equal(env.receipt.device, "cuda:0");
  assert.deepEqual(f.calls[0].body, { tool: "tcm_herb", arguments: { name: "甘草" }, context: { project_id: "p1", conversation_id: "c1", approvals: ["first_runner_call"] } });
  assert.equal(f.calls[0].init.headers["Content-Type"], "application/json");
});

test("call never throws: an unreachable runner or an HTTP error is a failed envelope", async () => {
  const r = new RunnerRuntime({ token: "tok", fetch: fakeFetch(() => { throw new TypeError("Failed to fetch"); }), permissions: null });
  const env = await r.call("tcm_herb", {});
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "unavailable");
  const r500 = new RunnerRuntime({ token: "tok", fetch: runnerServer({ routes: { "POST /api/call": () => json({ error: { message: "boom" } }, 500) } }) });
  const e2 = await r500.call("tcm_herb", {});
  assert.equal(e2.error.type, "runtime_error");
  assert.match(e2.summary, /500/);
  const ready = new RunnerRuntime({ token: "tok", fetch: runnerServer() });
  await ready.start();
  ready._fetch = fakeFetch(() => { throw new TypeError("Failed to fetch"); });
  await ready.call("x", {});
  assert.equal(ready.status, "offline", "a ready runner that stops answering goes offline");
});

test("jobs: submit, get, list with filters, cancel, files; the methods and paths of §4", async () => {
  const job = { id: "j_1", kind: "pipeline.fold", state: "queued" };
  const f = runnerServer({
    routes: {
      "POST /api/jobs": () => json(job, 201),
      "GET /api/jobs/j_1": () => json({ ...job, state: "running" }),
      "GET /api/jobs": (u) => json({ jobs: [job], q: u.search }),
      "DELETE /api/jobs/j_1": () => json({ ...job, state: "cancelled" }),
      "GET /api/jobs/j_1/files": () => json({ files: [{ path: "out/model.pdb", bytes: 10, sha256: "a".repeat(64), media_type: "chemical/x-pdb" }] }),
      "GET /api/llm/local": () => json({ servers: [{ id: "ollama", base_url: "http://127.0.0.1:11434/v1", ok: true, models: ["qwen3:8b"] }] }),
      "GET /api/settings": () => json({ device: "auto", threads: 4 }),
      "PUT /api/settings": (u, init) => json(JSON.parse(init.body)),
    },
  });
  const r = new RunnerRuntime({ token: "tok", fetch: f });
  assert.deepEqual(await r.jobs.submit({ kind: "pipeline.fold", params: { sequence: "MK" }, project_id: "p1", submission_id: "s1" }), job);
  assert.deepEqual(f.calls[0].body, { kind: "pipeline.fold", params: { sequence: "MK" }, project_id: "p1", submission_id: "s1" });
  assert.equal((await r.jobs.get("j_1")).state, "running");
  assert.equal((await r.jobs.list({ project_id: "p1", state: "running" })).q, "?project_id=p1&state=running");
  assert.equal((await r.jobs.cancel("j_1")).state, "cancelled");
  assert.equal((await r.jobs.files("j_1")).files[0].path, "out/model.pdb");
  assert.equal((await r.localModels()).servers[0].id, "ollama");
  assert.equal((await r.settings.get()).threads, 4);
  assert.deepEqual(await r.settings.put({ device: "cuda:0" }), { device: "cuda:0" });
  assert.equal(r.fileUrl("j_1", "out/图 1.png"), "http://127.0.0.1:8765/api/jobs/j_1/files/out/%E5%9B%BE%201.png?token=tok");
});

test("job events: EventSource with ?token=, typed events parsed, closed on done", async () => {
  const sources = [];
  class FakeES {
    constructor(url) { this.url = url; this.listeners = {}; this.readyState = 1; this.closed = false; sources.push(this); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    close() { this.closed = true; this.readyState = 2; }
    fire(type, data) { for (const fn of this.listeners[type] || []) fn({ data: JSON.stringify(data) }); }
  }
  const r = new RunnerRuntime({ token: "t k", EventSource: FakeES, fetch: runnerServer() });
  const got = [];
  const close = r.jobs.events("j_1", (e) => got.push(e));
  const es = sources[0];
  assert.equal(es.url, "http://127.0.0.1:8765/api/jobs/j_1/events?token=t%20k");
  es.fire("state", { state: "running" });
  es.fire("progress", { fraction: 0.5, message: "折叠中" });
  es.fire("done", { state: "succeeded" });
  assert.deepEqual(got.map((e) => [e.type, e.data]), [["state", { state: "running" }], ["progress", { fraction: 0.5, message: "折叠中" }], ["done", { state: "succeeded" }]]);
  assert.equal(es.closed, true);
  close();
  const r2 = new RunnerRuntime({ token: "x", EventSource: FakeES });
  const errs = [];
  r2.jobs.events("j_2", (e) => errs.push(e.type));
  const es2 = sources[1];
  es2.readyState = 2;
  es2.onerror();
  assert.deepEqual(errs, ["error"]);
});

test("upload: raw body with the name percent-encoded, the token, and progress", async () => {
  const xhrs = [];
  class FakeXHR {
    constructor() { this.headers = {}; this.upload = {}; xhrs.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(k, v) { this.headers[k] = v; }
    send(body) {
      this.body = body;
      setTimeout(() => {
        this.upload.onprogress?.({ loaded: 5, total: 10 });
        this.status = 201;
        this.responseText = JSON.stringify({ id: "u_1", name: "数据.csv", bytes: 10, sha256: "b".repeat(64), media_type: "text/csv" });
        this.onload();
      }, 1);
    }
    abort() { this.onabort?.(); }
  }
  const r = new RunnerRuntime({ token: "tok", XMLHttpRequest: FakeXHR });
  const progress = [];
  const blob = new Blob(["a,b\n1,2\n"], { type: "text/csv" });
  const res = await r.upload(blob, { name: "数据.csv", onProgress: (p) => progress.push(p.fraction) });
  assert.equal(res.id, "u_1");
  const x = xhrs[0];
  assert.deepEqual([x.method, x.url], ["POST", "http://127.0.0.1:8765/api/uploads"]);
  assert.equal(x.headers["X-Filename"], "%E6%95%B0%E6%8D%AE.csv");
  assert.equal(x.headers["Content-Type"], "text/csv");
  assert.equal(x.headers["X-TCM-Token"], "tok");
  assert.equal(x.body, blob);
  assert.deepEqual(progress, [0.5, 1]);
});

test("pairing links: only a loopback runner, base64url with or without padding; the fragment is removed", () => {
  const frag = (obj) => `#pair=${toBase64Url(JSON.stringify(obj))}`;
  assert.deepEqual(parsePairFragment(frag({ url: "http://127.0.0.1:8765/", token: "abc" })), { url: "http://127.0.0.1:8765", token: "abc" });
  assert.deepEqual(parsePairFragment(`${frag({ url: "http://localhost:9000", token: "t" })}==`), { url: "http://localhost:9000", token: "t" });
  assert.equal(parsePairFragment(frag({ url: "http://evil.example:8765", token: "abc" })), null, "a link cannot pair the page to another machine");
  assert.equal(parsePairFragment(frag({ url: "javascript:alert(1)", token: "x" })), null);
  assert.equal(parsePairFragment("#pair=!!!"), null);
  assert.equal(parsePairFragment("#other=1"), null);
  const replaced = [];
  const loc = { hash: `${frag({ url: "http://127.0.0.1:8765", token: "abc" })}`, pathname: "/p/1", search: "?lang=en" };
  const got = takePairing({ location: loc, history: { state: null, replaceState: (s, t, u) => replaced.push(u) } });
  assert.deepEqual(got, { url: "http://127.0.0.1:8765", token: "abc" });
  assert.deepEqual(replaced, ["/p/1?lang=en"]);
  assert.equal(takePairing({ location: { hash: "" }, history: {} }), null);
});

test("createRuntimes: the browser runtime when its module loads, an unavailable stand-in when it does not", async () => {
  class FakeBrowser { constructor(opts) { this.kind = "browser"; this.status = "idle"; this.opts = opts; } }
  const ok = await createRuntimes({ runner: { url: "http://127.0.0.1:8765", token: "" } }, { importBrowser: async () => ({ BrowserRuntime: FakeBrowser }), autoConnect: false });
  assert.ok(ok.browser instanceof FakeBrowser);
  assert.match(ok.browser.opts.bootUrl, /runtime\/boot\.json$/);
  assert.ok(ok.runner instanceof RunnerRuntime);
  const missing = await createRuntimes({}, { importBrowser: async () => { throw new Error("Cannot find module"); }, autoConnect: false });
  assert.ok(missing.browser instanceof UnavailableRuntime);
  assert.equal(missing.browser.status, "offline");
  const env = await missing.browser.call("tcm_herb", {});
  assert.equal(env.error.type, "unavailable");
});

test("the runner is probed in the background only when it cannot surprise the user", () => {
  assert.equal(shouldAutoConnect({ runner: { url: "http://127.0.0.1:8765", token: "" } }, { origin: "https://science.impf.ai" }), false);
  assert.equal(shouldAutoConnect({ runner: { url: "http://127.0.0.1:8765", token: "t" } }, { origin: "https://science.impf.ai" }), true);
  assert.equal(shouldAutoConnect({ runner: { url: "http://127.0.0.1:8765", token: "" } }, { origin: "http://127.0.0.1:8765" }), true);
  assert.equal(shouldAutoConnect({ runner: { url: "http://10.0.0.2:8765", token: "t" } }, { origin: "https://science.impf.ai" }), false);
});

test("pair() validates the address and resets the status", () => {
  const r = new RunnerRuntime({});
  r.pair("http://127.0.0.1:9999/", "new");
  assert.deepEqual([r.url, r.token, r.status], ["http://127.0.0.1:9999", "new", "idle"]);
  assert.throws(() => r.pair("ftp://x", "t"));
});

// Chrome opens at most six HTTP/1.1 connections to one host, across its tabs: one event stream per open job card would
// starve every other request to the runner (tool calls, cancel) once six jobs were open.
test("job events: open jobs share one poll; only one holds an event stream, and a freed stream goes to a running job", async () => {
  const sources = [];
  class FakeES {
    constructor(url) { this.url = url; this.listeners = {}; this.readyState = 1; this.closed = false; sources.push(this); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    close() { this.closed = true; this.readyState = 2; }
    fire(type, data) { for (const fn of this.listeners[type] || []) fn({ data: JSON.stringify(data) }); }
  }
  const states = { j_1: "running", j_2: "queued", j_3: "queued", j_4: "queued", j_5: "queued", j_6: "queued" };
  let inflight = 0;
  let maxInflight = 0;
  const route = (u) => new Promise((resolve) => {
    inflight++;
    maxInflight = Math.max(maxInflight, inflight);
    setTimeout(() => {
      inflight--;
      const id = u.pathname.split("/").pop();
      resolve(states[id] ? json({ id, kind: "test.sleep", state: states[id], progress: null }) : json({ error: { message: `no job ${id}`, type: "not_found" } }, 404));
    }, 2);
  });
  const routes = Object.fromEntries(["j_1", "j_2", "j_3", "j_4", "j_5", "j_6", "j_x"].map((id) => [`GET /api/jobs/${id}`, route]));
  const r = new RunnerRuntime({ token: "tok", EventSource: FakeES, fetch: runnerServer({ routes }), jobPollMs: 5 });
  const got = {};
  const closes = {};
  for (const id of Object.keys(states)) {
    got[id] = [];
    closes[id] = r.jobs.events(id, (e) => got[id].push(e));
  }
  const settle = (ms = 40) => new Promise((res) => setTimeout(res, ms));
  assert.equal(sources.length, 1, "one stream, not six");
  assert.match(sources[0].url, /\/api\/jobs\/j_1\/events\?token=tok$/);
  await settle();
  assert.equal(sources.length, 1, "queued jobs are polled, not streamed");
  assert.equal(maxInflight, 1, "the poll asks one job at a time");
  assert.deepEqual(got.j_2.map((e) => [e.type, e.data.state]), [["state", "queued"]], "a state is reported once, when it changes");
  assert.deepEqual(got.j_1.map((e) => [e.type, e.data.state]), [["state", "running"]], "the streamed job is polled too: its card moves even when its stream holds events back");
  // a polled job that ends is reported done and is no longer polled
  states.j_3 = "succeeded";
  await settle();
  assert.deepEqual(got.j_3.map((e) => [e.type, e.data.state]), [["state", "queued"], ["done", "succeeded"]]);
  // the streamed job ends: its slot goes to the job that is now running
  states.j_2 = "running";
  sources[0].fire("done", { state: "succeeded" });
  assert.equal(sources[0].closed, true);
  await settle();
  assert.equal(sources.length, 2);
  assert.match(sources[1].url, /\/api\/jobs\/j_2\/events/);
  assert.equal(got.j_2.filter((e) => e.type === "state").at(-1).data.state, "running");
  sources[1].fire("log", { line: "step 1" });
  assert.deepEqual(got.j_2.at(-1), { type: "log", data: { line: "step 1" } });
  // a job the runner does not know is an error, and is dropped
  const errs = [];
  r.jobs.events("j_x", (e) => errs.push(e.type));
  await settle();
  assert.deepEqual(errs, ["error"]);
  // closing every follower stops the poll
  for (const close of Object.values(closes)) close();
  await settle(20);
  const before = r._fetch.calls?.length;
  await settle(30);
  assert.equal(r._fetch.calls?.length, before, "no polling once nothing is followed");
  assert.equal(r._pollTimer, null);
  assert.equal(sources[1].closed, true);
});

test("job events without EventSource: the poll alone reports state and the end; stop() ends it", async () => {
  let state = "running";
  const routes = { "GET /api/jobs/j_9": () => json({ id: "j_9", state, progress: { fraction: state === "running" ? 0.5 : 1 } }) };
  const r = new RunnerRuntime({ token: "tok", EventSource: null, fetch: runnerServer({ routes }), jobPollMs: 5 });
  const got = [];
  r.jobs.events("j_9", (e) => got.push(e));
  await new Promise((res) => setTimeout(res, 20));
  assert.deepEqual(got.map((e) => e.type), ["state"]);
  assert.equal(got[0].data.progress.fraction, 0.5);
  state = "failed";
  await new Promise((res) => setTimeout(res, 20));
  assert.deepEqual(got.map((e) => e.type), ["state", "done"]);
  const r2 = new RunnerRuntime({ token: "tok", EventSource: null, fetch: runnerServer({ routes }), jobPollMs: 5 });
  r2.jobs.events("j_9", () => {});
  r2.stop();
  assert.equal(r2._pollTimer, null);
});
