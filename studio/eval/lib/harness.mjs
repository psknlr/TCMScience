// The eval harness: one case, one policy, one driver, through the real agent loop (core/agent.js runTurn), the real
// ToolRouter and RunnerRuntime against a real `tcmstudio serve` (started by e2e/lib/servers.mjs in a temporary home),
// and an observation of everything the graders read: the turn's result, its tool calls and envelopes, each model
// request's size, the retries and the waits they asked for, the relay's upstream calls and gate hits, and any attempt
// the runner made to send something off this machine.
//
// Approvals are answered the way scenarios.py's validator answers them: a lookup, a first runner call or web access
// (in a case that turns it on) is approved for once; a job, an act that needs confirming or an upload of the user's
// data to a third party is declined, because nobody approves those on a person's behalf. A case may say otherwise.

import "../../web/test/fixtures/setup.mjs";
import * as openaiLib from "../../web/js/core/llm/openai.js";
import { runTurn } from "../../web/js/core/agent.js";
import { loadCatalog } from "../../web/js/core/catalog.js";
import { buildSystemPrompt } from "../../web/js/core/prompt.js";
import { ToolRouter } from "../../web/js/core/router.js";
import { RunnerRuntime } from "../../web/js/runtime/runner.js";
import { startRunner } from "../../e2e/lib/servers.mjs";
import { gradeAll } from "../graders/index.mjs";
import { cassetteDriver, recordingLib } from "../models/cassette.mjs";
import { faultDriver, startFaultServer } from "../models/fault.mjs";
import { liveDriver } from "../models/live.mjs";
import { scriptedDriver } from "../models/scripted.mjs";
import { proxyEnv, startEgressRecorder } from "./egress.mjs";
import { Policy } from "./policy.mjs";
import { stubRouter } from "./stubrouter.mjs";
import { PINNED_DATE } from "./util.mjs";

export const DEFAULT_DENY = ["job", "confirm", "remote_upload"];
export const DEFAULT_DEADLINE_MS = 60000;

/**
 * A harness: the runner (unless `catalog` and `runtimes` are given, as the unit tests give fakes), the egress
 * recorder the runner's traffic goes to, and the fault server. close() stops them.
 */
export async function createHarness({ catalog = null, runtimes = null, log = () => {} } = {}) {
  const egress = await startEgressRecorder();
  const fault = await startFaultServer();
  let runnerProc = null;
  if (!catalog) {
    const t0 = Date.now();
    try {
      runnerProc = await withEnv(proxyEnv(egress.url), () => startRunner());
      const runner = new RunnerRuntime({ url: runnerProc.url, location: { origin: runnerProc.url, protocol: "http:", hostname: "127.0.0.1" } });
      await runner.start({ interactive: true });
      if (runner.status !== "ready") throw new Error(`the runner at ${runnerProc.url} is ${runner.status}: ${runner.error?.message || ""}`);
      runtimes = { runner };
      catalog = await loadCatalog({ runner }, { built: null, fetch: null });
    } catch (err) {
      await runnerProc?.stop();
      await fault.stop();
      await egress.stop();
      throw err;
    }
    log(`runner ${runnerProc.url} ready in ${Date.now() - t0} ms; ${catalog.core.length} core tools, ${catalog.entries.length} entries`);
  }
  const startupEgress = egress.attempts.length;
  let serial = 0;
  return {
    catalog, runtimes, egress, fault, startupEgress,
    runner: runnerProc,
    /** Run `caseDoc` with policy `policy` ("gold" or a mutant's id): the observation, graded. */
    run: (caseDoc, opts = {}) => runCase({ caseDoc, catalog, runtimes, egress, fault, serial: ++serial, ...opts }),
    async close() {
      await fault.stop();
      if (runnerProc) await runnerProc.stop();
      await egress.stop();
    },
  };
}

async function withEnv(vars, fn) {
  const saved = Object.fromEntries(Object.keys(vars).map((k) => [k, process.env[k]]));
  Object.assign(process.env, vars);
  try {
    return await fn();
  } finally {
    for (const [k, v] of Object.entries(saved)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
  }
}

/**
 * The policy spec a run plays: the gold one, with the case's `fallback` when it has none of its own, or the named
 * mutant as written. A mutant never takes the case's fallback: that is the gold's honest answer, and a mutant whose
 * references break (a fix refuses the call they name) still answers as the bad model it is, from its own `fallback`
 * or its last plain answer.
 */
export function policyOf(caseDoc, id = "gold") {
  if (id === "gold") {
    const gold = caseDoc.policies?.gold || { steps: [] };
    return { ...gold, fallback: gold.fallback ?? caseDoc.policies?.fallback };
  }
  const m = (caseDoc.policies?.mutants || []).find((x) => x.id === id);
  if (!m) throw new Error(`case ${caseDoc.id} has no mutant ${id}`);
  return m;
}

async function driverFor({ caseDoc, policy, fault, mode, cassette }) {
  const preset = caseDoc.preset || "tao";
  if (mode === "live") return liveDriver();
  if (mode === "replay") return cassetteDriver(cassette, { preset });
  if (mode === "record") {
    // the fault server in echo mode (the case's gold policy over HTTP), through the real client, recorded
    const d = faultDriver(fault, policy, { preset });
    const rec = recordingLib(openaiLib, caseDoc, { source: "echo", provider: d.provider });
    return { ...d, llm: { openai: rec }, cassette: rec.cassette };
  }
  const driver = caseDoc.driver || "scripted";
  if (driver === "scripted") return scriptedDriver(policy, { preset });
  if (driver === "fault") return faultDriver(fault, policy, { preset });
  if (driver === "relay") {
    // loaded when a relay case runs: the edge helper brings node:sqlite (the limiter's store) with it
    const { relayDriver } = await import("../models/relay.mjs");
    return relayDriver(policy, { env: caseDoc.relay_env || {} });
  }
  throw new Error(`case ${caseDoc.id}: unknown driver ${driver}`);
}

/** Which approvals a case grants: every reason but the denied ones, once. */
function approver(caseDoc, log) {
  const deny = new Set(caseDoc.approvals?.deny ?? DEFAULT_DENY);
  return async (request) => {
    const reasons = request.reasons || [request.reason];
    const decision = reasons.some((r) => deny.has(r)) ? "deny" : "once";
    log.push({ tool: request.tool, entry: request.entry, reasons, decision });
    return decision;
  };
}

/** One turn. mode: undefined (the case's own driver), "record", "replay" (with `cassette`) or "live". */
export async function runCase({ caseDoc, catalog, runtimes, egress, fault, policy: policyId = "gold", mode, cassette, serial = 1 }) {
  const policy = new Policy(policyOf(caseDoc, policyId));
  const driver = await driverFor({ caseDoc, policy, fault, mode, cassette });
  Object.assign(driver.provider, caseDoc.provider || {});
  const web = Boolean(caseDoc.settings?.web);
  const router = caseDoc.router === "stub"
    ? stubRouter(caseDoc.stub || {})
    : new ToolRouter({ catalog, runtimes, settings: { compute: "runner", web }, approvals: { get: async () => ({ web, approvals: {} }), grant: async () => {} } });
  const runnerReady = runtimes?.runner?.status === "ready";
  const system = buildSystemPrompt({
    lang: caseDoc.lang || "zh", provider: driver.provider, project: { name: "eval", defaults: { web } },
    env: { date: PINNED_DATE, browser: { status: "idle" }, runner: { status: runnerReady ? "ready" : "offline" }, web },
  });
  const conversationId = `c-${caseDoc.id}`;
  const history = [{ id: "u1", role: "user", content: caseDoc.question, conversationId, parentId: null, createdAt: 0 }];
  const events = [];
  const sleeps = [];
  const approvals = [];
  const ctrl = new AbortController();
  const deadline = Number(caseDoc.deadline_ms) || DEFAULT_DEADLINE_MS;
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; ctrl.abort(); }, deadline);
  const egressAt = egress?.attempts.length ?? 0;
  const t0 = Date.now();
  let res = null;
  let crash = null;
  try {
    res = await runTurn({
      provider: driver.provider, system, history, router, catalog, settings: { ...(caseDoc.settings || {}) },
      llm: driver.llm, signal: ctrl.signal, projectId: `p-eval-${serial}-${caseDoc.id}`, conversationId,
      onApproval: approver(caseDoc, approvals),
      onEvent: (ev) => { policy.observe(ev); events.push(slim(ev)); },
      // the waits a retry asks for are recorded, not slept: a Retry-After of 5 s costs the eval nothing
      sleep: async (ms, signal) => { sleeps.push(ms); if (signal?.aborted) throw new DOMException("aborted", "AbortError"); },
    });
  } catch (err) {
    crash = err?.stack || String(err);
  } finally {
    clearTimeout(timer);
  }
  const ms = Date.now() - t0;
  await driver.close?.();
  const meter = driver.meter;
  const stale = driver.stale?.() || null;
  const obs = {
    case: caseDoc.id, policy: policyId, driver: mode === "record" ? "fault" : driver.kind, model: driver.model,
    status: res?.status ?? "crashed", steps: res?.steps ?? 0, ms, timed_out: timedOut, deadline_ms: deadline, web,
    crash, stale,
    assistant: res ? assistantView(res.assistant) : null,
    tools: (res?.toolMessages || []).map((t) => ({
      callId: t.toolCallId, name: t.name, args: t.args, step: t.step, status: t.status, entry: entryOf(router, t.name, t.args), envelope: t.envelope,
    })),
    retries: events.filter((e) => e.type === "model.retry").map((e) => ({ attempt: e.attempt, delay_ms: e.delay_ms })),
    sleeps, approvals,
    requests: meter.requests,
    model_calls: meter.requests.length,
    upstream_calls: meter.upstreamCalls,
    gate_hits: meter.gateHits,
    egress: egress ? egress.since(egressAt).map((a) => ({ method: a.method, host: a.host })) : [],
    usage: res?.usage || null,
    event_types: [...new Set(events.map((e) => e.type))],
  };
  const grades = stale ? null : gradeAll(obs, caseDoc.expect || {});
  return { obs, grades, cassette: driver.cassette?.() || null };
}

/** What the record keeps of the assistant message: everything but the wire, the segments and the reasoning text. */
function assistantView(a) {
  const { wire, segments, reasoning, ...rest } = a || {};
  return { ...rest, reasoning_chars: String(reasoning || "").length };
}

function slim(ev) {
  if (ev.type === "text" || ev.type === "reasoning") return { type: ev.type, step: ev.step };
  if (ev.type === "tool.end" || ev.type === "tool.update") return { type: ev.type, callId: ev.callId, step: ev.step };
  if (ev.type === "tool.approval") return { type: ev.type, callId: ev.callId };
  return { ...ev };
}

/** The catalog entry a call resolved to (call_tool's target, a core tool's entry), or its own name. */
export function entryOf(router, name, args) {
  try {
    const r = router?.resolve?.(name, args && typeof args === "object" ? args : {});
    return (r && !r.error && (r.entry?.id || r.target?.id)) || name;
  } catch {
    return name;
  }
}
