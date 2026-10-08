import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { indexedDB } from "fake-indexeddb";
import { Catalog } from "../js/core/catalog.js";
import { HUMAN_ONLY, ToolRouter, normalizeEnvelope, storeApprovals } from "../js/core/router.js";
import { openStore } from "../js/core/store.js";
import { refusalsOf } from "../js/core/governance.js";
import { envelopeFor, fakeRuntime, memoryApprovals, testCatalogDoc } from "./fixtures/fakes.mjs";

const catalog = () => new Catalog(testCatalogDoc());

function router({ browser = "idle", runner = "ready", compute = "auto", web = false, approvals, answer } = {}) {
  const runtimes = {
    browser: browser === null ? undefined : fakeRuntime("browser", { status: browser, answer }),
    runner: runner === null ? undefined : fakeRuntime("runner", { status: runner, answer }),
  };
  return { r: new ToolRouter({ catalog: catalog(), runtimes, settings: { compute, web }, approvals: approvals || memoryApprovals() }), runtimes };
}

test("placement: auto uses a connected runner for everything it has, the browser otherwise", () => {
  const { r } = router({ runner: "ready" });
  assert.equal(r.where("tcm_herb"), "runner");
  assert.equal(r.where("native.reverse_complement"), "runner");
  assert.equal(router({ runner: "offline" }).r.where("native.reverse_complement"), "browser");
  assert.equal(r.where("literature_search"), "runner");
  assert.equal(r.where("native.scrna_cluster"), "runner", "heavy deps go to the runner when it is connected");
  assert.equal(r.where("job.pipeline"), "runner");
  assert.equal(r.whereCall("call_tool", { tool: "native.scrna_cluster" }), "runner");
  assert.equal(r.whereCall("call_tool", { tool: "nope" }), null);
});

test("placement: without a runner, runner-only tools are unavailable with the reason; heavy browser-capable ones stay in the browser", () => {
  const { r } = router({ runner: "offline" });
  assert.equal(r.where("tcm_herb"), "browser");
  assert.equal(r.where("native.scrna_cluster"), "browser");
  const p = r.placement("literature_search");
  assert.equal(p.where, null);
  assert.equal(p.reason, "needs_runner");
  assert.match(p.message, /Runner/);
});

test("placement: compute=browser and compute=runner are honoured strictly", () => {
  const b = router({ compute: "browser", runner: "ready" }).r;
  assert.equal(b.where("tcm_herb"), "browser");
  assert.equal(b.placement("literature_search").reason, "compute_browser");
  const rr = router({ compute: "runner", runner: "ready" }).r;
  assert.equal(rr.where("tcm_herb"), "runner");
  const off = router({ compute: "runner", runner: "offline" }).r;
  assert.equal(off.placement("tcm_herb").reason, "runner_offline");
});

test("placement: an entry the runner cannot run (missing deps) says what is missing", () => {
  const { r } = router({ runner: "ready" });
  const p = r.placement("native.dock");
  assert.equal(p.where, null);
  assert.equal(p.reason, "missing_deps");
  assert.match(p.message, /vina, meeko/);
});

test("a browser runtime that failed to load counts as absent", () => {
  const { r } = router({ browser: "offline", runner: "offline" });
  assert.equal(r.where("tcm_herb"), null);
});

test("a call runs where placed, with the project context, and the envelope records where", async () => {
  const { r, runtimes } = router({ runner: "offline" });
  const env = await r.call("tcm_herb", { name: "甘草" }, { projectId: "p1", conversationId: "c1" });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "browser");
  assert.deepEqual(runtimes.browser.calls[0].ctx.project_id, "p1");
  assert.deepEqual(runtimes.browser.calls[0].ctx.conversation_id, "c1");
});

test("unknown tools fail as not_found with suggestions; unavailable tools fail with the reason, nothing substituted", async () => {
  const { r } = router({ runner: "offline" });
  const env = await r.call("tcm_herbs", {});
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "not_found");
  assert.match(env.text, /tcm_herb/);
  const un = await r.call("literature_search", { query: "葛根" });
  assert.equal(un.status, "failed");
  assert.equal(un.error.type, "unavailable");
  assert.match(un.text, /local runner/);
  assert.ok(un.receipt.input_sha256 && /^[0-9a-f]{64}$/.test(un.receipt.input_sha256));
});

test("the first runner call of a project needs approval; 'once' covers this page's life, 'project' is remembered", async () => {
  const approvals = memoryApprovals({ p1: { web: true, approvals: {} }, p2: { web: true, approvals: {} } });
  const { r } = router({ runner: "ready", web: true, approvals });
  const asked = [];
  const once = async (req) => { asked.push(req); return "once"; };
  const a = await r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p1", onApproval: once, callId: "x1" });
  assert.equal(a.status, "succeeded");
  assert.equal(asked.length, 1);
  assert.equal(asked[0].reason, "first_runner_call");
  assert.equal(asked[0].callId, "x1");
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p1", onApproval: once });
  assert.equal(asked.length, 1, "not asked again in this page");
  assert.deepEqual(approvals.projects.p1.approvals, {}, "'once' is not stored");
  const proj = async () => "project";
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p2", onApproval: proj });
  assert.equal(approvals.projects.p2.approvals.runner, "project");
  const fresh = new ToolRouter({ catalog: catalog(), runtimes: router({ runner: "ready" }).runtimes, settings: {}, approvals });
  let askedAgain = false;
  await fresh.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p2", onApproval: async () => { askedAgain = true; return "deny"; } });
  assert.equal(askedAgain, false, "a project approval stands for a new page too");
});

test("deny: the call does not run and the model is told plainly", async () => {
  const { r, runtimes } = router({ runner: "ready" });
  const env = await r.call("run_pipeline", { pipeline: "fold", arguments: {} }, { projectId: "p1", onApproval: async () => "deny" });
  assert.equal(env.status, "refused");
  assert.equal(env.error.type, "declined");
  assert.match(env.text, /declined/);
  assert.equal(env.approval.decision, "deny");
  assert.equal(runtimes.runner.calls.length, 0);
});

test("jobs ask with reason 'job' (plus the first runner call); without a handler the call is needs_approval", async () => {
  const { r } = router({ runner: "ready" });
  let req;
  await r.call("run_pipeline", { pipeline: "fold" }, { projectId: "p1", onApproval: async (q) => { req = q; return "once"; } });
  assert.deepEqual(req.reasons, ["job", "first_runner_call"]);
  assert.equal(req.reason, "job");
  const { r: r2 } = router({ runner: "ready" });
  const env = await r2.call("run_pipeline", { pipeline: "fold" }, { projectId: "p1" });
  assert.equal(env.status, "needs_approval");
  assert.equal(env.approval.reason, "job");
  assert.match(env.approval.what, /job\.pipeline|流程/);
});

test("network tools need the project's web access, then an approval naming the hosts", async () => {
  const approvals = memoryApprovals({ off: { web: false, approvals: {} }, on: { web: true, approvals: { runner: "project" } } });
  const { r } = router({ runner: "ready", approvals });
  const off = await r.call("literature_search", { query: "葛根素" }, { projectId: "off", onApproval: async () => "once" });
  assert.equal(off.status, "failed");
  assert.equal(off.error.type, "network_off");
  let req;
  const on = await r.call("literature_search", { query: "葛根素" }, { projectId: "on", onApproval: async (q) => { req = q; return "project"; } });
  assert.equal(on.status, "succeeded");
  assert.deepEqual(req.reasons, ["network"]);
  assert.deepEqual(req.hosts, ["www.ebi.ac.uk"]);
  assert.equal(approvals.projects.on.approvals["connector.europepmc.search:network"], "project");
});

test("sending data to a third party (allow_remote) is its own approval", async () => {
  const approvals = memoryApprovals({ p: { web: true, approvals: { runner: "project" } } });
  const { r } = router({ runner: "ready", approvals });
  let req;
  await r.call("call_tool", { tool: "native.scrna_cluster", arguments: { allow_remote: true } }, { projectId: "p", onApproval: async (q) => { req = q; return "once"; } });
  assert.deepEqual(req.reasons, ["remote_upload"]);
});

test("parallel calls that need the same approval ask once", async () => {
  const { r } = router({ runner: "ready" });
  let asked = 0;
  const onApproval = async () => { asked++; await new Promise((res) => setTimeout(res, 5)); return "once"; };
  const envs = await Promise.all([1, 2, 3].map(() => r.call("call_tool", { tool: "native.scrna_cluster", arguments: {} }, { projectId: "p", onApproval })));
  assert.deepEqual(envs.map((e) => e.status), ["succeeded", "succeeded", "succeeded"]);
  assert.equal(asked, 1);
});

test("approvals persist in the Store's project record", async () => {
  const store = await openStore({ indexedDB, name: `router-${Math.random()}` });
  const p = await store.projects.create({ name: "审批", web: true });
  const { runtimes } = router({ runner: "ready" });
  const r = new ToolRouter({ catalog: catalog(), runtimes, settings: {}, store });
  await r.call("literature_search", { query: "x" }, { projectId: p.id, onApproval: async () => "project" });
  const saved = await store.projects.get(p.id);
  assert.equal(saved.approvals.runner, "project");
  assert.equal(saved.approvals["connector.europepmc.search:network"], "project");
  const adapter = storeApprovals(store);
  assert.equal((await adapter.get(p.id)).web, true);
});

test("catalog_search and capabilities_status are answered in the page when no runtime is ready", async () => {
  const { r, runtimes } = router({ browser: "idle", runner: "offline" });
  const env = await r.call("catalog_search", { query: "配伍禁忌", limit: 5 });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "browser");
  assert.equal(env.receipt.local, true);
  assert.equal(runtimes.browser.calls.length, 0);
  const parsed = JSON.parse(env.text);
  assert.equal(parsed.matched[0].id, "native.tcm_compatibility");
  const caps = await r.call("capabilities_status", {});
  assert.equal(caps.result.runner.connected, false);
  assert.equal(caps.result.web_access, false);
  const ready = router({ browser: "ready", runner: "offline" });
  await ready.r.call("catalog_search", { query: "x" });
  assert.equal(ready.runtimes.browser.calls.length, 1, "a ready runtime answers itself");
});

test("a runtime that throws gives a runtime_error envelope; an abort gives cancelled", async () => {
  const { r } = router({ runner: "offline", answer: () => { throw new Error("worker crashed"); } });
  const env = await r.call("tcm_herb", { name: "甘草" });
  assert.equal(env.status, "failed");
  assert.equal(env.error.type, "runtime_error");
  assert.match(env.text, /worker crashed/);

  const slow = new ToolRouter({ catalog: catalog(), runtimes: { browser: fakeRuntime("browser", { delayMs: 200 }) }, settings: {} });
  const ctl = new AbortController();
  setTimeout(() => ctl.abort(), 10);
  const cancelled = await slow.call("tcm_herb", { name: "甘草" }, { signal: ctl.signal });
  assert.equal(cancelled.status, "cancelled");

  const asking = router({ runner: "ready" }).r;
  const ctl2 = new AbortController();
  setTimeout(() => ctl2.abort(), 10);
  const pending = await asking.call("run_pipeline", { pipeline: "fold" }, { signal: ctl2.signal, onApproval: () => new Promise(() => {}) });
  assert.equal(pending.status, "cancelled", "a stop while the approval card is open cancels the call");
});

test("normalizeEnvelope fills the shape and caps text", async () => {
  const env = await normalizeEnvelope({ ok: true, text: "x".repeat(20000) }, { tool: "t", where: "runner" });
  assert.equal(env.status, "succeeded");
  assert.equal(env.receipt.where, "runner");
  assert.ok(env.text.length < 16200);
  assert.match(env.text, /truncated/);
  const bad = await normalizeEnvelope(null, { tool: "t", where: "browser" });
  assert.equal(bad.status, "failed");
  const kept = await normalizeEnvelope(envelopeFor("tcm_herb", "runner", { extra_field: 1 }), { tool: "tcm_herb", where: "runner" });
  assert.equal(kept.extra_field, 1);
});

test("acts reserved for a person are refused with HUMAN_ONLY, by name or through call_tool — never 'unknown tool'", async () => {
  const { r, runtimes } = router({ runner: "ready", browser: "ready" });
  for (const [name, args] of [["clinic.sign", {}], ["call_tool", { tool: "clinic.sign", arguments: { draft_id: "d1" } }], ["call_tool", { tool: "clinic_sign" }], ["shell", { cmd: "ls" }], ["call_tool", { tool: "registry.release" }]]) {
    const env = await r.call(name, args, { projectId: "p" });
    assert.equal(env.status, "refused", name);
    assert.equal(env.ok, false);
    assert.deepEqual(env.governance.refusals.map((x) => x.code), ["HUMAN_ONLY"]);
    assert.ok(env.governance.refusals[0].message && env.governance.refusals[0].remedy);
    assert.match(env.text, /HUMAN_ONLY/);
    assert.doesNotMatch(env.text, /catalog_search|There is no tool/, "the model is not sent looking for a way to do it");
    assert.notEqual(env.error?.type, "not_found");
  }
  const sign = await r.call("call_tool", { tool: "clinic.sign" }, {});
  assert.equal(sign.governance.kind, "clinic");
  assert.equal(sign.via, "clinic.sign");
  assert.match(sign.summary, /只能由人完成/);
  assert.equal(refusalsOf(sign)[0].explanation, "只能由人完成的操作，不是工具调用");
  assert.equal(runtimes.runner.calls.length + runtimes.browser.calls.length, 0, "nothing runs, and no approval is asked");
  assert.equal(r.whereCall("call_tool", { tool: "clinic.sign" }), null);
  assert.ok(Object.keys(HUMAN_ONLY).includes("clinic.sign"));
  // a catalog that ships never_offered adds to the page's own list
  const withList = new ToolRouter({ catalog: new Catalog({ ...testCatalogDoc(), never_offered: { "lab.order": "Ordering a lab test is a clinician's act." } }), runtimes, settings: {}, approvals: memoryApprovals() });
  const order = await withList.call("call_tool", { tool: "lab.order" }, {});
  assert.equal(order.status, "refused");
  assert.match(order.governance.refusals[0].message, /clinician's act/);
});
