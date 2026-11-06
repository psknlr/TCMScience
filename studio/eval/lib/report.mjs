// The run's record (agent_eval_report.json, the tcmscience.validation/1 schema every validation report uses) and the
// baseline it is held to.
//
// baseline.json says, for every case of every suite, whether it passes today or is known to fail, and for a known
// failure which checks fail and what they said (the probe output that shows the harm). A run fails when a case that
// passed now fails, when a known-failing case fails a check it did not fail before, when a case the baseline does not
// know fails, when a mutant passes the grader it targets, or when the baseline was edited by hand (its digest no
// longer matches its cases). A known-failing case that passes is reported, and the update script (run.mjs
// --update-baseline) moves it to passing: removing a known_failure any other way leaves a case the baseline expects
// to pass, which then fails the run.

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { graderOf } from "../graders/index.mjs";
import { terminationOf } from "../graders/structural.mjs";
import { trajectoryOf } from "../graders/execution.mjs";
import { canonical, sha256 } from "./util.mjs";

export const BASELINE_SCHEMA = "tcmscience.eval.baseline/1";
export const REPORT_SCHEMA = "tcmscience.validation/1";

/** The failing checks of a grading, as "grader.check": the baseline's record of how a case fails. */
export function failingChecks(grades) {
  return (grades?.graders || []).flatMap((g) => g.checks.filter((c) => !c.pass).map((c) => `${g.grader}.${c.name}`));
}

export function loadBaseline(file) {
  if (!existsSync(file)) return { schema: BASELINE_SCHEMA, cases: {}, digest: digestOf({}), missing: true };
  return JSON.parse(readFileSync(file, "utf8"));
}

export const digestOf = (cases) => sha256(canonical(cases));

/**
 * Compare a run's gold results with the baseline: {ok, digest_ok, regressions, fixed, improved, unrecorded, new_passing,
 * stale, known_failing}. `results`: [{id, grades, stale}] (id = "<suite>/<case>").
 */
export function compareBaseline(baseline, results) {
  const out = { digest_ok: digestOf(baseline.cases || {}) === baseline.digest, regressions: [], fixed: [], improved: [], unrecorded: [], new_passing: [], stale: [], known_failing: [] };
  for (const r of results) {
    const entry = baseline.cases?.[r.id];
    if (r.stale) { out.stale.push({ id: r.id, reason: r.stale }); continue; }
    const failing = failingChecks(r.grades);
    const pass = failing.length === 0;
    if (!entry) { (pass ? out.new_passing : out.unrecorded).push({ id: r.id, failing }); continue; }
    if (entry.status !== "known_failing") {
      if (!pass) out.regressions.push({ id: r.id, failing, was: "passing" });
      continue;
    }
    if (pass) { out.fixed.push({ id: r.id, was: entry.failing_checks }); continue; }
    const before = new Set(entry.failing_checks || []);
    const added = failing.filter((c) => !before.has(c));
    if (added.length) out.regressions.push({ id: r.id, failing: added, was: "known_failing", known: entry.failing_checks });
    else {
      out.known_failing.push({ id: r.id, failing });
      if (failing.length < before.size) out.improved.push({ id: r.id, now: failing, was: entry.failing_checks });
    }
  }
  out.ok = out.digest_ok && !out.regressions.length && !out.unrecorded.length;
  return out;
}

/**
 * The baseline after this run. A passing case is recorded passing (a known failure fixed is removed from the known
 * failures); a new case is recorded as it is; a case that passed and now fails is refused unless its id is in
 * `accept` (a regression is fixed, not written down). Stale results keep their entry. Returns {baseline, refused}.
 */
export function updateBaseline(baseline, results, { accept = [], commit = "", drop = [] } = {}) {
  const cases = structuredClone(baseline.cases || {});
  const refused = [];
  for (const id of drop) delete cases[id];
  for (const r of results) {
    if (r.stale) continue;
    const failing = failingChecks(r.grades);
    const entry = cases[r.id];
    if (!failing.length) { cases[r.id] = { status: "passing" }; continue; }
    const before = new Set(entry?.failing_checks || []);
    const worse = entry && (entry.status !== "known_failing" || failing.some((c) => !before.has(c)));
    if (worse && !accept.includes(r.id)) { refused.push({ id: r.id, failing }); continue; }
    cases[r.id] = {
      status: "known_failing", failing_checks: failing, evidence: evidenceOf(r.grades),
      since: entry?.status === "known_failing" ? entry.since : commit || entry?.since || "",
    };
  }
  const sorted = Object.fromEntries(Object.keys(cases).sort().map((k) => [k, cases[k]]));
  return {
    baseline: {
      schema: BASELINE_SCHEMA,
      note: "Written by `npm run eval:agent -- --update-baseline` (studio/eval/run.mjs), never by hand: `digest` is the sha256 of the canonical JSON of `cases`, and a run fails when they disagree. A known_failing entry carries the failing checks and what they said.",
      cases: sorted, digest: digestOf(sorted),
    },
    refused,
  };
}

/** What the failing checks said: the measured symptom a known failure is recorded with. */
export function evidenceOf(grades) {
  return (grades?.graders || []).flatMap((g) => g.checks.filter((c) => !c.pass).map((c) => `${g.grader}.${c.name}: ${c.detail}`)).join(" | ");
}

export function writeBaseline(file, baseline) {
  writeFileSync(file, `${JSON.stringify(baseline, null, 1)}\n`);
}

// ------------------------------------------------------------------------------------------------- the report

const STATUS = (pass) => (pass ? "PASS" : "FAIL");

/** One validation record for a case's gold run. */
export function recordOf({ id, suite, caseDoc, run, baselineEntry }) {
  const { obs, grades } = run;
  const stale = obs.stale;
  const checks = stale
    ? [{ name: "cassette", status: "UNVERIFIED", detail: `cassette_stale: ${stale}` }]
    : grades.graders.flatMap((g) => g.checks.map((c) => ({ name: `${g.grader}.${c.name}`, status: STATUS(c.pass), detail: c.detail })));
  const status = stale ? "UNVERIFIED" : grades.pass ? "PASS" : "FAIL";
  const firstFail = checks.find((c) => c.status === status && status !== "PASS");
  const cost = stale ? null : graderOf(grades, "cost")?.measures;
  return {
    component: `agent_eval.${suite}.${caseDoc.id}`,
    kind: "agent_eval_case",
    version: "",
    status,
    ...(firstFail ? { failure_reason: `${firstFail.name}: ${firstFail.detail}` } : {}),
    input: { id, suite, question: caseDoc.question, driver: obs.driver, model: obs.model, policy: obs.policy, tags: caseDoc.tags || [] },
    baseline: stale ? "stale" : baselineEntry?.status === "known_failing" ? (grades.pass ? "fixed" : "known_failing") : baselineEntry ? (grades.pass ? "passing" : "regression") : grades.pass ? "new" : "unrecorded",
    checks,
    measures: stale ? null : Object.fromEntries(grades.graders.map((g) => [g.grader, g.measures])),
    provenance: {
      status: obs.status, termination: obs.assistant ? terminationOf(obs) : null, steps: obs.steps, trajectory: trajectoryOf(obs),
      calls: obs.tools.map((t) => ({ name: t.name, entry: t.entry, status: t.status, step: t.step, error: t.envelope?.error?.type || null })),
      approvals: obs.approvals, retries: obs.retries, egress: obs.egress, answer: obs.assistant?.content ?? null,
      error: obs.assistant?.error || null, crash: obs.crash || null,
    },
    resources: cost ? { model_calls: cost.model_calls, upstream_calls: cost.upstream_calls, peak_request_bytes: cost.peak_request_bytes, total_request_bytes: cost.total_request_bytes, tokens_in: cost.tokens_in, tokens_out: cost.tokens_out } : {},
    runtime_s: Math.round(obs.ms) / 1000,
    reproduce: `cd studio && npm run eval:agent -- --suite ${suite} --case ${caseDoc.id}`,
  };
}

/** The report document. */
export function reportOf({ records, mutants, comparison, environment, notes, model, tier }) {
  const statuses = ["PASS", "FAIL", "BLOCKED", "SKIPPED", "UNVERIFIED", "NOT_APPLICABLE"];
  const count = (s) => records.filter((r) => r.status === s).length;
  const checks = {};
  for (const r of records) for (const c of r.checks) { checks[c.name] ??= {}; checks[c.name][c.status] = (checks[c.name][c.status] || 0) + 1; }
  return {
    schema: REPORT_SCHEMA,
    report: "agent_eval_report",
    generated_at: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    tier,
    model,
    summary: { records: records.length, status: Object.fromEntries(statuses.map((s) => [s, count(s)])), checks: Object.fromEntries(Object.entries(checks).sort()) },
    environment,
    notes,
    baseline: comparison,
    mutants,
    records,
  };
}
