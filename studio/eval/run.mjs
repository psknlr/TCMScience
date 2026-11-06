#!/usr/bin/env node
// The headless agent eval: every case of the dev split through the real Studio agent loop (core/agent.js runTurn), the
// real ToolRouter and RunnerRuntime against a real `tcmstudio serve` (a temporary home, its traffic off this machine
// refused and recorded), graded structural → execution → evidence → goal → cost, and held to baseline.json.
//
//   cd studio && npm run eval:agent [-- options]
//
//   --suite a,b          scripted, fault, relay, cassette (default: all four); live (only with --live)
//   --case id            only this case (repeatable)
//   --cases dir          the case files (default eval/cases/dev)
//   --report file        where agent_eval_report.json goes (default: the system's temporary directory)
//   --baseline file      default eval/baseline.json
//   --update-baseline    rewrite the baseline from this run (known failures fixed are moved to passing; a case that
//                        passed and now fails is refused unless named with --accept-regression id)
//   --record             record the cassettes of the cases tagged "cassette" through the fault server's echo of their
//                        gold policies (never a case tagged hidden), into --cassettes (default eval/cassettes/dev)
//   --no-mutants         skip the mutant policies (the mutation check then does not run)
//   --live               run the scripted cases' questions with a real model at EVAL_LIVE_BASE_URL (EVAL_LIVE_MODEL,
//                        EVAL_LIVE_API_KEY): asked for by name only, and in CI only from workflow_dispatch
//
// Exit status: 0 when nothing regressed and every mutant failed the grader it targets; 1 otherwise; 2 on a usage error.
// Scripted, fault and relay runs measure the loop, the router, the runner, the tools and the graders: the model is a
// script, so nothing here measures a model's choices, and the report says so (model: "scripted").

import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { graderOf, ORDER } from "./graders/index.mjs";
import { createHarness } from "./lib/harness.mjs";
import { compareBaseline, loadBaseline, recordOf, reportOf, updateBaseline, writeBaseline } from "./lib/report.mjs";
import { EVAL, PINNED_DATE } from "./lib/util.mjs";
import { loadCassette, saveCassette } from "./models/cassette.mjs";
import { liveRefusal } from "./models/live.mjs";

const SUITES = ["scripted", "fault", "relay", "cassette"];

export function parseArgs(argv) {
  const o = { suites: null, cases: [], dir: path.join(EVAL, "cases", "dev"), report: path.join(os.tmpdir(), "agent_eval_report.json"),
    baseline: path.join(EVAL, "baseline.json"), cassettes: path.join(EVAL, "cassettes", "dev"), update: false, accept: [], record: false,
    mutants: true, live: false, commit: "" };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const val = () => { if (i + 1 >= argv.length) throw new Error(`${a} needs a value`); return argv[++i]; };
    if (a === "--suite") o.suites = val().split(",").map((s) => s.trim()).filter(Boolean);
    else if (a === "--case") o.cases.push(val());
    else if (a === "--cases") o.dir = path.resolve(val());
    else if (a === "--report") o.report = path.resolve(val());
    else if (a === "--baseline") o.baseline = path.resolve(val());
    else if (a === "--cassettes") o.cassettes = path.resolve(val());
    else if (a === "--update-baseline") o.update = true;
    else if (a === "--accept-regression") o.accept.push(val());
    else if (a === "--commit") o.commit = val();
    else if (a === "--record") o.record = true;
    else if (a === "--no-mutants") o.mutants = false;
    else if (a === "--live") o.live = true;
    else if (a === "-h" || a === "--help") o.help = true;
    else throw new Error(`unknown option ${a}`);
  }
  o.suites ??= o.live ? ["live"] : SUITES;
  for (const s of o.suites) if (![...SUITES, "live"].includes(s)) throw new Error(`unknown suite ${s}`);
  if (o.suites.includes("live") && !o.live) throw new Error("the live suite runs only with --live");
  if (o.record && o.live) throw new Error("--record records the fault server's echo of the gold policies; recording a live model's cassettes, for review before they are committed, is not part of this run");
  return o;
}

/** The case files of a directory, checked: [{...case}]. */
export function loadCases(dir, only = []) {
  const files = readdirSync(dir).filter((f) => f.endsWith(".json")).sort();
  const cases = files.map((f) => {
    const c = JSON.parse(readFileSync(path.join(dir, f), "utf8"));
    if (`${c.id}.json` !== f) throw new Error(`${f}: its id is ${c.id}`);
    return c;
  });
  const problems = cases.flatMap(caseProblems);
  if (problems.length) throw new Error(`invalid cases:\n${problems.join("\n")}`);
  const missing = only.filter((id) => !cases.some((c) => c.id === id));
  if (missing.length) throw new Error(`no such case: ${missing.join(", ")}`);
  return only.length ? cases.filter((c) => only.includes(c.id)) : cases;
}

/** What is wrong with a case document (an empty list when nothing is). */
export function caseProblems(c) {
  const out = [];
  const where = c?.id || "(no id)";
  if (!/^[a-z0-9][a-z0-9-]*$/.test(c?.id || "")) out.push(`${where}: id must be lowercase words joined by -`);
  if (!["scripted", "fault", "relay"].includes(c?.driver)) out.push(`${where}: driver must be scripted, fault or relay`);
  if (typeof c?.question !== "string" || !c.question.trim()) out.push(`${where}: no question`);
  if (!Array.isArray(c?.policies?.gold?.steps) || !c.policies.gold.steps.length) out.push(`${where}: no gold policy`);
  const mutants = c?.policies?.mutants || [];
  if (!mutants.length) out.push(`${where}: no mutant policy (every case needs one that fails the grader it targets)`);
  for (const m of mutants) {
    if (!m.id) out.push(`${where}: a mutant without an id`);
    if (!ORDER.includes(m.targets)) out.push(`${where}/${m.id}: targets must be one of ${ORDER.join(", ")}`);
    if (!Array.isArray(m.steps) || !m.steps.length) out.push(`${where}/${m.id}: no steps`);
  }
  for (const k of Object.keys(c?.expect || {})) if (!ORDER.includes(k)) out.push(`${where}: expect.${k} is not a grader`);
  return out;
}

const gh = Boolean(process.env.GITHUB_ACTIONS);
const annotate = (level, msg) => console.log(gh ? `::${level}::${msg}` : `${level.toUpperCase()}: ${msg}`);

export async function main(argv = process.argv.slice(2)) {
  let o;
  try { o = parseArgs(argv); } catch (err) { console.error(String(err.message)); return 2; }
  if (o.help) { console.log(readFileSync(new URL(import.meta.url), "utf8").split("\n").filter((l) => l.startsWith("//")).map((l) => l.slice(3)).join("\n")); return 0; }
  if (o.live) {
    const why = liveRefusal(process.env, { asked: true });
    if (why) { console.error(`live run refused: ${why}`); return 2; }
  }
  let cases;
  try { cases = loadCases(o.dir, o.cases); } catch (err) { console.error(String(err.message)); return 2; }
  const t0 = Date.now();
  const harness = await createHarness({ log: (m) => console.log(m) });
  try {
    if (o.record) return await record(harness, cases, o);
    return await evaluate(harness, cases, o, t0);
  } finally {
    await harness.close();
  }
}

async function record(harness, cases, o) {
  const chosen = cases.filter((c) => (c.tags || []).includes("cassette"));
  for (const c of chosen) {
    if ((c.tags || []).includes("hidden")) { console.log(`refused: ${c.id} is tagged hidden and is never recorded`); continue; }
    const { obs, grades, cassette } = await harness.run(c, { mode: "record" });
    saveCassette(o.cassettes, cassette);
    console.log(`recorded ${c.id}: ${cassette.recordings.length} request(s), ${grades.pass ? "passes" : `fails ${grades.failed.join(", ")}`} (${obs.ms} ms)`);
  }
  return 0;
}

async function evaluate(harness, cases, o, t0) {
  const baseline = loadBaseline(o.baseline);
  const gold = [];
  const mutants = [];
  const records = [];
  for (const suite of o.suites) {
    for (const c of suiteCases(suite, cases)) {
      const id = `${suite}/${c.id}`;
      let run;
      if (suite === "cassette") {
        const doc = loadCassette(o.cassettes, c.id);
        if (!doc) { run = missingCassette(c); } else run = await harness.run(c, { mode: "replay", cassette: doc });
      } else if (suite === "live") {
        run = await harness.run(c, { mode: "live" });
      } else {
        run = await harness.run(c);
      }
      gold.push({ id, suite, caseDoc: c, run, stale: run.obs.stale, grades: run.grades });
      records.push(recordOf({ id, suite, caseDoc: c, run, baselineEntry: baseline.cases?.[id] }));
      line(id, run, baseline.cases?.[id]);
      if (o.mutants && ["scripted", "fault", "relay"].includes(suite)) {
        for (const m of c.policies.mutants || []) {
          const mr = await harness.run(c, { policy: m.id });
          const target = graderOf(mr.grades, m.targets);
          mutants.push({ case: c.id, suite, mutant: m.id, targets: m.targets, failed: mr.grades.failed, survived: Boolean(target?.pass), why: m.why || "" });
        }
      }
    }
  }
  const live = o.suites.includes("live");
  const comparison = live ? null : compareBaseline(baseline, gold);
  const survived = mutants.filter((m) => m.survived);
  const model = live ? "live" : o.suites.every((s) => s === "cassette") ? "cassette" : "scripted";
  const report = reportOf({
    records, mutants, comparison, model, tier: live ? "live" : "offline",
    environment: {
      node: process.version, platform: `${process.platform}-${process.arch}`, pinned_date: PINNED_DATE, suites: o.suites,
      cases: path.relative(EVAL, o.dir) || o.dir, catalog: { core: harness.catalog.core.length, entries: harness.catalog.entries.length },
      runner_egress_at_start: harness.startupEgress, seconds: Math.round((Date.now() - t0) / 100) / 10,
    },
    notes: [
      "model scripted: the scripted, fault and relay suites play written policies (a gold one and its mutants) through the real loop, router, runner and tools; they measure those and the graders, not a model's choices.",
      "model cassette: replayed event streams; the committed cassettes were recorded from the fault server's echo of the gold policies, not from a model.",
      "Live-model numbers come only from --live (workflow_dispatch in CI), never from a push or a pull request.",
      "Every runner request off this machine went to a recording proxy that refused it (environment.runner_egress_at_start, records' provenance.egress).",
    ],
  });
  writeFileSync(o.report, `${JSON.stringify(report, null, 1)}\n`);
  console.log(`\nreport: ${o.report}`);
  let failed = false;
  if (comparison) {
    if (!comparison.digest_ok) { failed = true; annotate("error", `${path.basename(o.baseline)} was edited by hand: its digest does not match its cases; regenerate it with --update-baseline`); }
    for (const r of comparison.regressions) { failed = true; annotate("error", `regression: ${r.id} fails ${r.failing.join(", ")} (baseline: ${r.was})`); }
    for (const r of comparison.unrecorded) { failed = true; annotate("error", `${r.id} fails (${r.failing.join(", ")}) and the baseline does not know it: record it with --update-baseline`); }
    for (const r of comparison.fixed) annotate("notice", `${r.id} was known_failing and now passes: run --update-baseline to record it`);
    for (const r of comparison.improved) annotate("notice", `${r.id} fails fewer checks than recorded (${r.now.join(", ")})`);
    for (const r of comparison.new_passing) annotate("notice", `${r.id} passes and is not in the baseline yet: run --update-baseline`);
    for (const r of comparison.stale) annotate("warning", `${r.id}: cassette_stale (${r.reason}); re-record with --record`);
  }
  if (o.mutants && !live) {
    for (const m of survived) { failed = true; annotate("error", `mutant ${m.case}/${m.mutant} passed the ${m.targets} grader it targets`); }
    console.log(`mutants: ${mutants.length - survived.length}/${mutants.length} failed the grader they target`);
  }
  for (const r of gold.filter((g) => g.run.obs.crash)) { failed = true; annotate("error", `${r.id}: the harness caught ${String(r.run.obs.crash).split("\n")[0]}`); }
  if (o.update && comparison) {
    const present = new Set(cases.map((c) => c.id));
    const ran = new Set(o.suites);
    const drop = Object.keys(baseline.cases || {}).filter((id) => { const [s, c] = id.split("/"); return ran.has(s) && !present.has(c) && !o.cases.length; });
    const { baseline: next, refused } = updateBaseline(baseline, gold, { accept: o.accept, commit: o.commit, drop });
    for (const r of refused) annotate("error", `not written: ${r.id} passed before and now fails ${r.failing.join(", ")} (fix it, or name it with --accept-regression)`);
    writeBaseline(o.baseline, next);
    console.log(`baseline written: ${o.baseline} (${Object.values(next.cases).filter((e) => e.status === "known_failing").length} known_failing of ${Object.keys(next.cases).length})`);
    return refused.length ? 1 : 0;
  }
  const n = gold.length;
  const pass = gold.filter((g) => g.grades?.pass).length;
  console.log(`${pass}/${n} cases pass; ${comparison ? `${comparison.known_failing.length} known_failing, ${comparison.regressions.length} regression(s), ${comparison.fixed.length} fixed, ${comparison.stale.length} stale` : "live run: no baseline"}; ${Math.round((Date.now() - t0) / 1000)} s`);
  return failed ? 1 : 0;
}

function suiteCases(suite, cases) {
  if (suite === "cassette") return cases.filter((c) => (c.tags || []).includes("cassette"));
  if (suite === "live") return cases.filter((c) => c.driver === "scripted");
  return cases.filter((c) => c.driver === suite);
}

/** A cassette the case says it has and the directory does not: a failure, not a skip. */
function missingCassette(c) {
  const obs = { case: c.id, policy: "gold", driver: "cassette", model: "cassette", status: "crashed", steps: 0, ms: 0, crash: `no cassette for ${c.id}: record it with --record`, tools: [], approvals: [], retries: [], egress: [], requests: [], assistant: null, stale: null };
  return { obs, grades: { pass: false, failed: ["structural"], first_failure: "structural", graders: [{ grader: "structural", pass: false, checks: [{ name: "cassette", pass: false, detail: obs.crash }], measures: {} }] } };
}

function line(id, run, entry) {
  const g = run.grades;
  const state = run.obs.stale ? "STALE" : g.pass ? "pass" : "FAIL";
  const known = entry?.status === "known_failing" ? (g?.pass ? " (was known_failing: fixed)" : " (known_failing)") : "";
  const why = run.obs.stale ? run.obs.stale : g.pass ? "" : g.graders.flatMap((x) => x.checks.filter((c) => !c.pass).map((c) => `${x.grader}.${c.name}`)).join(", ");
  console.log(`${state.padEnd(5)} ${id.padEnd(58)} ${String(run.obs.ms).padStart(6)} ms${known}${why ? `  ${why}` : ""}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  main().then((code) => process.exit(code), (err) => { console.error(err?.stack || err); process.exit(1); });
}
