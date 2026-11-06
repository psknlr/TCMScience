// The regression rule and the committed baseline: a case that passed and now fails fails the run; a known failure that
// now passes is reported and moved to passing by the update script; a known failure that fails a new check, or a case
// the baseline does not know that fails, fails the run; a hand edit (the digest disagrees) fails the run; and removing a
// known_failing entry without its case passing leaves a case expected to pass that fails.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { ORDER } from "../graders/index.mjs";
import { compareBaseline, digestOf, failingChecks, updateBaseline } from "../lib/report.mjs";
import { EVAL } from "../lib/util.mjs";
import { loadCases } from "../run.mjs";

/** A grading whose failing checks are `failing` ("grader.check"); every grader also has one passing check. */
const grades = (failing = []) => ({
  pass: failing.length === 0,
  graders: ORDER.map((g) => {
    const mine = failing.filter((f) => f.startsWith(`${g}.`)).map((f) => ({ name: f.split(".")[1], pass: false, detail: `${f} said so` }));
    return { grader: g, pass: mine.length === 0, checks: [...mine, { name: "ok", pass: true, detail: "" }] };
  }),
});
const baselineOf = (cases) => ({ schema: "tcmscience.eval.baseline/1", cases, digest: digestOf(cases) });
const known = (...checks) => ({ status: "known_failing", failing_checks: checks, evidence: "", since: "530031f" });

test("a passing case that fails is a regression; a known failure that passes is reported as fixed, not a failure", () => {
  const b = baselineOf({ "fault/a": { status: "passing" }, "fault/b": known("structural.answer") });
  const r = compareBaseline(b, [{ id: "fault/a", grades: grades(["cost.max_model_calls"]) }, { id: "fault/b", grades: grades() }]);
  assert.equal(r.ok, false);
  assert.deepEqual(r.regressions.map((x) => x.id), ["fault/a"]);
  assert.deepEqual(r.fixed.map((x) => x.id), ["fault/b"]);
  const fine = compareBaseline(b, [{ id: "fault/a", grades: grades() }, { id: "fault/b", grades: grades() }]);
  assert.equal(fine.ok, true, "a fix alone never fails the run");
});

test("a known failure that fails a check it did not fail before is a regression; failing fewer is an improvement", () => {
  const b = baselineOf({ "relay/a1": known("structural.retry_offered") });
  const worse = compareBaseline(b, [{ id: "relay/a1", grades: grades(["structural.retry_offered", "cost.max_upstream_calls"]) }]);
  assert.equal(worse.ok, false);
  assert.deepEqual(worse.regressions[0].failing, ["cost.max_upstream_calls"]);
  const b2 = baselineOf({ "fault/q": known("structural.retry_offered", "cost.max_model_calls") });
  const better = compareBaseline(b2, [{ id: "fault/q", grades: grades(["structural.retry_offered"]) }]);
  assert.equal(better.ok, true);
  assert.equal(better.improved.length, 1);
});

test("a failing case the baseline does not know fails the run; a stale cassette is neither pass nor fail", () => {
  const b = baselineOf({});
  const r = compareBaseline(b, [{ id: "scripted/new", grades: grades(["goal.mentions"]) }, { id: "cassette/x", stale: "the system prompt differs", grades: null }]);
  assert.equal(r.ok, false);
  assert.deepEqual(r.unrecorded.map((x) => x.id), ["scripted/new"]);
  assert.deepEqual(r.stale.map((x) => x.id), ["cassette/x"]);
});

test("a hand edit is caught by the digest, and a known_failing entry removed by hand leaves a case expected to pass", () => {
  const b = baselineOf({ "fault/len": known("structural.states_how_it_ended") });
  const edited = { ...b, cases: {} }; // the entry deleted, the digest left as it was
  const r = compareBaseline(edited, [{ id: "fault/len", grades: grades(["structural.states_how_it_ended"]) }]);
  assert.equal(r.digest_ok, false);
  assert.equal(r.ok, false);
  const redigested = baselineOf({ "fault/len": { status: "passing" } }); // the same edit with the digest recomputed
  const r2 = compareBaseline(redigested, [{ id: "fault/len", grades: grades(["structural.states_how_it_ended"]) }]);
  assert.equal(r2.digest_ok, true);
  assert.deepEqual(r2.regressions.map((x) => x.id), ["fault/len"]);
});

test("the update script moves a fixed case to passing, records a new failure, and refuses to write down a regression", () => {
  const b = baselineOf({ "fault/a": { status: "passing" }, "fault/b": known("structural.answer") });
  const results = [{ id: "fault/a", grades: grades(["cost.max_model_calls"]) }, { id: "fault/b", grades: grades() }, { id: "fault/c", grades: grades(["goal.mentions"]) }];
  const { baseline, refused } = updateBaseline(b, results, { commit: "abc1234" });
  assert.deepEqual(refused.map((x) => x.id), ["fault/a"]);
  assert.deepEqual(baseline.cases["fault/a"], { status: "passing" }, "the regression is not written down");
  assert.deepEqual(baseline.cases["fault/b"], { status: "passing" });
  assert.equal(baseline.cases["fault/c"].status, "known_failing");
  assert.deepEqual(baseline.cases["fault/c"].failing_checks, ["goal.mentions"]);
  assert.equal(baseline.cases["fault/c"].since, "abc1234");
  assert.equal(baseline.digest, digestOf(baseline.cases));
  const accepted = updateBaseline(b, results, { accept: ["fault/a"] });
  assert.equal(accepted.refused.length, 0);
  assert.equal(accepted.baseline.cases["fault/a"].status, "known_failing");
});

test("the committed baseline: its digest holds, every case of every suite is in it, and nothing else is", () => {
  const file = path.join(EVAL, "baseline.json");
  assert.ok(existsSync(file));
  const b = JSON.parse(readFileSync(file, "utf8"));
  assert.equal(b.schema, "tcmscience.eval.baseline/1");
  assert.equal(b.digest, digestOf(b.cases), "baseline.json was edited by hand: regenerate it with --update-baseline");
  const cases = loadCases(path.join(EVAL, "cases", "dev"));
  const ids = new Set([
    ...cases.map((c) => `${c.driver}/${c.id}`),
    ...cases.filter((c) => c.tags?.includes("cassette")).map((c) => `cassette/${c.id}`),
  ]);
  assert.deepEqual(Object.keys(b.cases).sort(), [...ids].sort());
  for (const [id, e] of Object.entries(b.cases)) {
    assert.ok(["passing", "known_failing"].includes(e.status), id);
    if (e.status === "known_failing") assert.ok(e.failing_checks.length && e.evidence, `${id}: a known failure is recorded with its checks and what they said`);
  }
});

test("the harms the plan names are each a case of the dev split", () => {
  const ids = new Set(loadCases(path.join(EVAL, "cases", "dev")).map((c) => c.id));
  for (const id of ["fault-maxsteps-ignores-tool-choice", "fault-length", "growth-cjk-3", "relay-a1-plan-quota-429", "relay-a3-unknown-code-sse",
    "relay-a4-unknown-code-json", "poc-fabricated-citation", "inquiry-role-omitted-artefact", "inquiry-selfcert-pmids", "inquiry-bypass-scaffold",
    "identifier-in-connector-arguments"]) assert.ok(ids.has(id), id);
});

test("failingChecks names grader.check", () => {
  assert.deepEqual(failingChecks(grades(["structural.answer", "cost.max_ms"])), ["structural.answer", "cost.max_ms"]);
  assert.deepEqual(failingChecks(null), []);
});
