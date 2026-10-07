// The browser runtime, checked in a real browser: boots BrowserRuntime (Pyodide in a module worker, the real
// psh + bioagent + tcmstudio) and calls tools the way the router does. The result lands in window.__result for
// run.mjs (Playwright), which also compares the envelopes with `python3 -m tcmstudio call … --where browser`.
//
// ?mode=full (default) | reload (after full, same project: cache hit, audit chain persisted) | nonisolated
// (cancel by terminate + restart) | tamper (a wrong bundle hash must refuse to boot). ?project=ID ?index=URL ?sci=1

import { BrowserRuntime } from "../../js/runtime/browser.js";

const q = new URLSearchParams(location.search);
const mode = q.get("mode") || "full";
const project = q.get("project") || `check-${Date.now().toString(36)}`;
const indexUrl = q.get("index") || undefined;
const sci = q.get("sci") === "1";
const bootUrl = new URL("/runtime/boot.json", location.href).href;
const T0 = performance.now();

const out = {
  mode, project, ok: true, failures: [], isolated: Boolean(globalThis.crossOriginIsolated),
  user_agent: navigator.userAgent, statuses: [], calls: [], checks: [], boot: null, info: null, device: null,
};

const $rows = document.getElementById("rows");
const $status = document.getElementById("status");

function row(label, ok, summary, ms) {
  const tr = document.createElement("tr");
  const cells = [label, ok ? "ok" : "FAILED", summary ?? "", ms === undefined || ms === null ? "" : String(Math.round(ms))];
  cells.forEach((text, i) => {
    const td = document.createElement("td");
    td.textContent = text;
    if (i === 1) td.className = ok ? "ok" : "bad";
    if (i === 3) td.className = "ms";
    tr.append(td);
  });
  $rows.append(tr);
}

function check(label, ok, detail = "", ms = null) {
  out.checks.push({ label, ok: Boolean(ok), detail, ms });
  if (!ok) {
    out.ok = false;
    out.failures.push(`${label}: ${detail}`);
  }
  row(label, ok, detail, ms);
  return Boolean(ok);
}

function failuresOf(expectations) {
  return expectations.filter(([cond]) => !cond).map(([, what]) => what);
}

/** One call as the router makes it; `expect(env)` returns [[condition, description], …]. */
async function run(label, tool, args, expect, { compare = false, signal, project_id = project } = {}) {
  const t0 = performance.now();
  let env;
  try {
    env = await rt.call(tool, args, { project_id, conversation_id: "runtime-check", approvals: [], signal });
  } catch (err) {
    check(label, false, `call threw: ${err?.message || err}`);
    return null;
  }
  const ms = performance.now() - t0;
  const rec = {
    label, tool, args, ms: Math.round(ms), status: env?.status, via: env?.via, summary: env?.summary,
    released: env?.governance?.released ?? null, content_hash: env?.receipt?.content_hash ?? null,
    durable: env?.receipt?.durable ?? null, error: env?.error ?? null, compare,
    envelope: compare ? env : undefined,
  };
  out.calls.push(rec);
  let problems;
  try {
    problems = failuresOf([
      [env && typeof env === "object", "an envelope"],
      [env?.receipt?.where === "browser", `receipt.where is browser (got ${env?.receipt?.where})`],
      ...expect(env),
    ]);
  } catch (err) {
    problems = [`expectation threw: ${err?.message || err}`];
  }
  check(label, !problems.length, problems.length ? problems.join("; ") : `${env.status} · ${env.summary}`, ms);
  return env;
}

const has = (v, s) => JSON.stringify(v ?? null).includes(s);
const allStates = (env) => Object.values(env?.governance?.verdict?.states || {}).length === 6 && Object.values(env.governance.verdict.states).every(Boolean);

let rt = null;

async function boot(opts = {}) {
  rt = new BrowserRuntime({ bootUrl, indexUrl, debug: true, ...opts });
  rt.onStatus((ev) => out.statuses.push({ ...ev, t: Math.round(performance.now() - T0) }));
  return rt;
}

async function startChecked() {
  const t0 = performance.now();
  let info;
  try {
    info = await rt.start();
  } catch (err) {
    check("start", false, `${err?.code || ""} ${err?.message || err}`);
    return null;
  }
  const ms = performance.now() - t0;
  out.boot = { wall_ms: Math.round(ms), ...info };
  const loading = out.statuses.filter((s) => s.status === "loading").map((s) => s.progress);
  check("start", rt.status === "ready" && info?.runtime?.startsWith("pyodide-314.0.7"),
    `${info?.runtime} · bundle ${info?.bundle?.from_cache ? "from Cache Storage" : "downloaded"} · persist ${info?.persist?.mode}`, ms);
  check("progress events", loading.length >= 4 && loading.every((p, i) => i === 0 || p >= loading[i - 1]) && out.statuses.at(-1)?.progress === 100,
    `${loading.length} loading events, monotonic, ending at 100`);
  return info;
}

async function cancelCheck({ expectInterrupt }) {
  const long = { a: "ACGT".repeat(1000), b: "TGCA".repeat(1000) };
  const ac = new AbortController();
  const restartsBefore = out.statuses.filter((s) => s.status === "loading").length;
  setTimeout(() => ac.abort(), 600);
  const env = await run("cancel a running call", "call_tool", { tool: "native.edit_distance", arguments: long }, (e) => [
    [e.status === "cancelled", `status cancelled (got ${e.status})`],
  ], { signal: ac.signal });
  const ms = out.calls.at(-1)?.ms ?? 0;
  if (expectInterrupt) {
    // Python stopped through the interrupt buffer: no restart, the dispatcher's own cancelled envelope
    const restarted = out.statuses.filter((s) => s.status === "loading").length > restartsBefore;
    check("cancel: interrupted in place", env?.receipt?.runtime?.startsWith("pyodide") && !restarted && ms < 3500,
      `${ms} ms after the call started; ${restarted ? "the worker was restarted" : "no restart"}`);
  } else {
    check("cancel: terminate + restart", ms < 1500 && (rt.status === "loading" || rt.status === "ready"), `${ms} ms; status ${rt.status}`);
  }
  await run("next call after the cancel", "tcm_compatibility", { herbs: ["人参", "藜芦"] }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status}: ${e.error?.message || ""})`],
    [e.result?.compatible === false, "人参 + 藜芦 recorded as incompatible"],
  ]);
}

async function full() {
  await boot();
  const t0 = performance.now();
  const doc = await rt.catalog();
  check("catalog before Python", doc?.entries?.length >= 600 && doc?.core?.length === 25 && rt.status === "idle",
    `${doc?.core?.length} core tools, ${doc?.entries?.length} entries, runtime still ${rt.status}`, performance.now() - t0);
  const d0 = performance.now();
  out.device = await rt.device();
  check("device report", typeof out.device?.cross_origin_isolated === "boolean" && "webgpu" in out.device && rt.status === "idle",
    `cores ${out.device?.cores}, memory ${out.device?.memory_gb} GB, isolated ${out.device?.cross_origin_isolated}, WebGPU ${out.device?.webgpu?.adapter ? `${out.device.webgpu.adapter.vendor} ${out.device.webgpu.adapter.architecture}${out.device.webgpu.adapter.software ? " (software)" : ""}` : "none"}`,
    performance.now() - d0);

  if (!(await startChecked())) return;

  await run("tcm_compatibility 甘草 + 甘遂", "tcm_compatibility", { herbs: ["甘草", "甘遂"] }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [e.result?.compatible === false, "not compatible"],
    [has(e.result?.conflicts, "十八反"), "a 十八反 conflict"],
  ], { compare: true });

  await run("tcm_safety_report 甘草 with 甘遂 (governed)", "tcm_safety_report", { subject: "甘草", co_administered: ["甘遂"] }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status}: ${e.error?.message || ""})`],
    [e.governance?.released === true, "released"],
    [allStates(e), "all six verdict states true"],
    [e.receipt?.content_hash?.startsWith("e525e3abd501"), `content hash e525e3abd501… (got ${e.receipt?.content_hash})`],
    [has(e.governance?.evidence, "十八反"), "the 十八反 finding in the evidence"],
    [e.citations?.length > 0, "citations"],
    [/ABSENCE OF A RECORD IS NOT EVIDENCE OF SAFETY/i.test(e.text || ""), "text restates that no record is not safety"],
  ], { compare: true });

  await run("tcm_normalize 黄芪 / 桂枝汤 / 姜 (governed)", "tcm_normalize", { names: ["黄芪", "桂枝汤", "姜"] }, (e) => {
    const qs = e.result?.outputs?.["entities.json"]?.queries || [];
    const jiang = qs.find((x) => x.query === "姜");
    return [
      [e.status === "succeeded", `succeeded (got ${e.status})`],
      [e.governance?.released === true, "released"],
      [e.receipt?.content_hash?.startsWith("a5de563ed8d6"), `content hash a5de563ed8d6… (got ${e.receipt?.content_hash})`],
      [jiang?.status === "ambiguous" && jiang.candidates?.includes("herb.shengjiang") && jiang.candidates?.includes("herb.ganjiang"), "姜 kept ambiguous (生姜 / 干姜)"],
    ];
  }, { compare: true });

  await run("call_tool native.benjamini_hochberg", "call_tool", { tool: "native.benjamini_hochberg", arguments: { p_values: [0.01, 0.04, 0.03, 0.2] } }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [JSON.stringify(e.result?.q_values) === JSON.stringify([0.04, 0.0533333333, 0.0533333333, 0.2]), `q values (got ${JSON.stringify(e.result?.q_values)})`],
  ], { compare: true });

  await run("call_tool native.fisher_exact", "call_tool", { tool: "native.fisher_exact", arguments: { a: 8, b: 2, c: 1, d: 5 } }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [typeof e.result?.p_value === "number", "a p value"],
  ], { compare: true });

  await run("catalog_search 十八反", "catalog_search", { query: "十八反" }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [(e.result?.matched || []).some((m) => m.id === "native.tcm_compatibility"), "finds native.tcm_compatibility"],
  ], { compare: true });

  await run("connector_call in the browser → unavailable", "connector_call", { connector: "uniprot", operation: "entry", arguments: { accession: "P04637" } }, (e) => [
    [e.status === "failed", `failed (got ${e.status})`],
    [e.error?.type === "unavailable", `error unavailable (got ${e.error?.type})`],
    [/runner/i.test(e.error?.hint || ""), "the remedy names the local runner"],
  ], { compare: true });

  await run("call_tool clinic.sign → refused (a person's act)", "call_tool", { tool: "clinic.sign" }, (e) => [
    [e.status === "refused", `refused (got ${e.status})`],
    [e.governance?.refusals?.[0]?.code === "HUMAN_ONLY", "refusal code HUMAN_ONLY"],
  ], { compare: true });

  await run("tcm_normalize with a misspelt argument", "tcm_normalize", { name: ["黄芪"] }, (e) => [
    [e.status === "failed" && e.error?.type === "bad_arguments", `failed / bad_arguments (got ${e.status} / ${e.error?.type})`],
    [/Did you mean 'names'/.test(e.error?.hint || ""), "hint: Did you mean 'names'"],
  ], { compare: true });

  await run("tcm_safety_report again (warm)", "tcm_safety_report", { subject: "附子" }, (e) => [
    [e.status === "succeeded" && e.governance?.released === true, `succeeded and released (got ${e.status})`],
    [e.receipt?.content_hash?.startsWith("e525e3abd501"), "same content hash"],
  ]);

  await run("audit chain of the project", "call_tool", { tool: "system.audit_verify" }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [e.result?.records >= 3 && e.result?.intact === true, `≥ 3 records, intact (got ${e.result?.records}, ${e.result?.intact})`],
    [e.result?.durable === (out.boot?.persist?.mode === "idbfs"), `durable ${e.result?.durable} matches persist mode ${out.boot?.persist?.mode}`],
  ]);

  await run("capabilities_status", "capabilities_status", {}, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [e.result?.host?.browser?.device?.webgpu !== undefined, "host facts include the device report"],
  ]);

  await cancelCheck({ expectInterrupt: Boolean(globalThis.crossOriginIsolated) });

  // A Pyodide fatal error during a call: the call fails as runtime_error, the worker restarts, the next call works.
  await rt.debug("fatal");
  await run("call after a fatal error → runtime_error", "tcm_compatibility", { herbs: ["人参", "藜芦"] }, (e) => [
    [e.status === "failed" && e.error?.type === "runtime_error", `failed / runtime_error (got ${e.status} / ${e.error?.type})`],
  ]);
  await run("call after the restart", "tcm_compatibility", { herbs: ["人参", "藜芦"] }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status}: ${e.error?.message || ""})`],
    [e.result?.compatible === false, "人参 + 藜芦 recorded as incompatible"],
  ]);

  if (sci) {
    await run("clinic_assess (numpy + scipy on demand)", "clinic_assess", {
      intake: {
        patient: { age: 40, sex: "female", pregnant: false },
        inquiry: { present: ["神疲乏力", "胃口差", "大便稀"] },
        inspection: { tongue: ["舌淡，苔白"] }, palpation: { pulse: ["脉缓弱"] },
        medications: [], allergies: [], conditions: [],
      },
    }, (e) => [
      [e.status === "succeeded", `succeeded (got ${e.status}: ${e.error?.message || ""})`],
      [/practitioner|执业/i.test(e.text || ""), "text says it is a draft for a licensed practitioner"],
    ]);
  }
  out.info = await rt.info();
}

async function reload() {
  await boot();
  const info = await startChecked();
  if (!info) return;
  check("bundle from Cache Storage", info.bundle?.from_cache === true, `from_cache ${info.bundle?.from_cache}`);
  check("compiled modules from Cache Storage", info.pycache?.restored === true, `restored ${info.pycache?.restored}`);
  const before = await run("audit chain survives the reload", "call_tool", { tool: "system.audit_verify" }, (e) => [
    [e.status === "succeeded", `succeeded (got ${e.status})`],
    [info.persist?.mode !== "idbfs" || e.result?.records >= 3, `records from the previous page load (got ${e.result?.records}, persist ${info.persist?.mode})`],
    [e.result?.intact === true, "intact"],
  ]);
  await run("tcm_evidence 黄芪 (governed)", "tcm_evidence", { subject: "黄芪" }, (e) => [
    [e.status === "succeeded" && e.governance?.released === true, `succeeded and released (got ${e.status})`],
    [e.receipt?.content_hash?.startsWith("f844cf695a60"), `content hash f844cf695a60… (got ${e.receipt?.content_hash})`],
  ], { compare: true });
  const runs = (e) => e?.result?.events?.bioscience_skill_run_started ?? 0;
  await run("the chain grew by one run", "call_tool", { tool: "system.audit_verify" }, (e) => [
    [runs(e) === runs(before) + 1, `governed runs recorded: ${runs(before)} → ${runs(e)}`],
    [e.result?.records > (before?.result?.records ?? 0) && e.result?.intact === true, `${before?.result?.records} → ${e.result?.records} records, intact`],
  ]);
  out.info = await rt.info();
}

async function nonisolated() {
  await boot();
  check("page is not cross-origin isolated", !globalThis.crossOriginIsolated, `crossOriginIsolated ${globalThis.crossOriginIsolated}`);
  if (!(await startChecked())) return;
  check("not interruptible", rt.interruptible === false, `interruptible ${rt.interruptible}`);
  await run("tcm_compatibility", "tcm_compatibility", { herbs: ["甘草", "甘遂"] }, (e) => [[e.status === "succeeded", `succeeded (got ${e.status})`]]);
  await cancelCheck({ expectInterrupt: false });
  out.info = await rt.info();
}

async function tamper() {
  // The worker must refuse a bundle whose SHA-256 differs from boot.json; here boot.json is the liar.
  await boot({
    fetch: async (url, init) => {
      const res = await fetch(url, init);
      if (!String(url).endsWith("/runtime/boot.json")) return res;
      const doc = await res.json();
      doc.bundle.sha256 = "0".repeat(64);
      return new Response(JSON.stringify(doc), { status: 200, headers: { "content-type": "application/json" } });
    },
  });
  const t0 = performance.now();
  let err = null;
  try { await rt.start(); } catch (e) { err = e; }
  check("a wrong bundle hash refuses to boot", err?.code === "bundle_hash" && rt.status === "error", `${err?.code}: ${err?.message?.slice(0, 120)}`, performance.now() - t0);
  await run("a call then reports the runtime unavailable", "tcm_compatibility", { herbs: ["甘草", "甘遂"] }, (e) => [
    [e.status === "failed" && e.error?.type === "unavailable", `failed / unavailable (got ${e.status} / ${e.error?.type})`],
  ]);
}

const modes = { full, reload, nonisolated, tamper };
try {
  if (!modes[mode]) throw new Error(`unknown mode ${mode}`);
  await modes[mode]();
} catch (err) {
  check("check page", false, `threw: ${err?.stack || err}`);
}
out.total_ms = Math.round(performance.now() - T0);
$status.textContent = `${out.ok ? "All checks passed" : `${out.failures.length} check(s) failed`} · mode ${mode} · ${out.total_ms} ms`;
$status.className = out.ok ? "ok" : "bad";
globalThis.__result = out;
