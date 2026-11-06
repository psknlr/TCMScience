// Grader mutation testing: every case has a gold policy and at least one mutant policy written to fail one grader (and,
// when it names one, one check of it), and every mutant does fail what it targets (a grader that passes a mutant would
// pass anything). The cases that need no runner (the fault and relay drivers) run here against a fake runner; the proof
// of concept's pair runs against a real `tcmstudio serve` when this machine's Python has tcmstudio (CI's web step
// does), and is skipped, saying so, when it does not. run.mjs checks every mutant of every case on every run.
import "../../web/test/fixtures/setup.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { after, before, test } from "node:test";
import { Catalog } from "../../web/js/core/catalog.js";
import { fakeRuntime, testCatalogDoc } from "../../web/test/fixtures/fakes.mjs";
import { graderOf, ORDER } from "../graders/index.mjs";
import { createHarness } from "../lib/harness.mjs";
import { caseProblems, loadCases, mutantSurvived } from "../run.mjs";
import { EVAL } from "../lib/util.mjs";
import path from "node:path";

const cases = loadCases(path.join(EVAL, "cases", "dev"));

test("the dev split: at least 20 cases, each with a gold policy and a mutant that names the grader it targets", () => {
  assert.ok(cases.length >= 20, `${cases.length} cases`);
  assert.equal(new Set(cases.map((c) => c.id)).size, cases.length);
  for (const c of cases) {
    assert.deepEqual(caseProblems(c), [], c.id);
    assert.ok(c.policies.mutants.length >= 1, c.id);
    for (const m of c.policies.mutants) assert.ok(ORDER.includes(m.targets), `${c.id}/${m.id}`);
  }
  for (const d of ["scripted", "fault", "relay"]) assert.ok(cases.some((c) => c.driver === d), `a ${d} case`);
});

test("a case without a mutant, or with a mutant that targets no grader, is refused", () => {
  const base = { id: "x", driver: "scripted", question: "q", policies: { gold: { steps: [{ text: "a" }] }, mutants: [] } };
  assert.match(caseProblems(base).join("\n"), /no mutant/);
  assert.match(caseProblems({ ...base, policies: { ...base.policies, mutants: [{ id: "m", targets: "style", steps: [{ text: "b" }] }] } }).join("\n"), /targets must be one of/);
  assert.match(caseProblems({ ...base, policies: { ...base.policies, mutants: [{ id: "m", targets: "cost", check: "cost.max_ms", steps: [{ text: "b" }] }] } }).join("\n"), /check must name one check/);
});

test("every grader is the target of some mutant, and every fault and relay case has one aimed at the structural or cost check it is for", () => {
  for (const g of ORDER) assert.ok(cases.some((c) => c.policies.mutants.some((m) => m.targets === g)), `no mutant targets ${g}`);
  for (const c of cases.filter((x) => x.driver === "fault" || x.driver === "relay")) {
    const aimed = c.policies.mutants.filter((m) => ["structural", "cost"].includes(m.targets) && m.check);
    assert.ok(aimed.length, `${c.id}: every mutant targets ${[...new Set(c.policies.mutants.map((m) => m.targets))].join(", ")}`);
    for (const m of aimed) assert.ok(c.expect?.[m.targets], `${c.id}/${m.id}: the case sets no ${m.targets} expectations`);
  }
});

test("a mutant that names a check survives when its grader fails for another reason", () => {
  const grades = { graders: [{ grader: "structural", pass: false, checks: [{ name: "status", pass: false }, { name: "retry_offered", pass: true }] }] };
  assert.equal(mutantSurvived(grades, { targets: "structural" }), false);
  assert.equal(mutantSurvived(grades, { targets: "structural", check: "status" }), false);
  assert.equal(mutantSurvived(grades, { targets: "structural", check: "retry_offered" }), true);
  assert.equal(mutantSurvived(grades, { targets: "structural", check: "error_kind" }), true, "a check that did not run shows nothing");
  assert.equal(mutantSurvived({ graders: [{ grader: "cost", pass: true, checks: [] }] }, { targets: "cost" }), true);
});

let harness;
before(async () => {
  harness = await createHarness({ catalog: new Catalog(testCatalogDoc()), runtimes: { runner: fakeRuntime("runner") } });
});
after(async () => { await harness?.close(); });

test("every mutant of every fault and relay case fails the grader (and the check) it targets", async () => {
  const runnerFree = cases.filter((c) => c.driver === "fault" || c.driver === "relay");
  assert.ok(runnerFree.length >= 10);
  for (const c of runnerFree) {
    for (const m of c.policies.mutants) {
      const { obs, grades } = await harness.run(c, { policy: m.id });
      assert.equal(obs.crash, null, `${c.id}/${m.id}: ${obs.crash}`);
      assert.equal(graderOf(grades, m.targets).pass, false, `${c.id}/${m.id} passed the ${m.targets} grader it targets`);
      assert.equal(mutantSurvived(grades, m), false, `${c.id}/${m.id} passed the ${m.targets}.${m.check} check it targets`);
    }
  }
});

test("a mutant whose references break answers as the bad model it is, never with the case's honest fallback", async () => {
  // every call succeeds with a result that has no inquiry_id, as when a fix (P1.6) refuses the open call: the $from
  // chains of the gold policy and of its mutants end after the first call
  for (const id of ["inquiry-role-omitted-artefact", "inquiry-selfcert-pmids", "inquiry-bypass-scaffold"]) {
    const c = cases.find((x) => x.id === id);
    assert.ok(c.policies.fallback?.text, `${id} has a case fallback`);
    const broken = { ...c, router: "stub", stub: { result_chars: 40, charset: "ascii" } };
    const gold = await harness.run(broken);
    assert.equal(gold.obs.assistant.content, c.policies.fallback.text, `${id}: the gold policy answers with the case's fallback`);
    for (const m of c.policies.mutants) {
      const run = await harness.run(broken, { policy: m.id });
      assert.equal(run.obs.tools.length, 1, `${id}/${m.id}: the chain broke after the open call`);
      assert.equal(run.obs.assistant.content, m.steps.at(-1).text, `${id}/${m.id}: its own last answer, not the case's fallback`);
      assert.equal(mutantSurvived(run.grades, m), false, `${id}/${m.id} passed the ${m.targets} grader it targets once its chain broke`);
    }
  }
});

test("a mutant that does not fail its target is caught: the same check on a gold policy passes", async () => {
  const c = cases.find((x) => x.id === "fault-429-retry-after");
  const gold = await harness.run(c);
  assert.equal(graderOf(gold.grades, "evidence").pass, true);
  const m = await harness.run(c, { policy: "fabricated-citation" });
  assert.equal(graderOf(m.grades, "evidence").pass, false);
});

/** A Python with tcmstudio, or null (then the real-runner part is skipped, with the reason). */
const RUNNER_PYTHON = [process.env.PYTHON, "python3"].filter(Boolean).find((py) => {
  try { execFileSync(py, ["-c", "import tcmstudio"], { stdio: "ignore", timeout: 60000 }); return true; } catch { return false; }
}) ?? null;

test("the proof of concept's pair through a real runner: recall 1.0 vs 0.5, unresolved citations 0 vs 1, banned words 0 vs 1 (证明)", { skip: RUNNER_PYTHON ? false : "no Python with tcmstudio here (set PYTHON): the real-runner pair is not run", timeout: 180000 }, async () => {
  const saved = process.env.PYTHON;
  process.env.PYTHON = RUNNER_PYTHON;
  const real = await createHarness();
  try {
    const c = cases.find((x) => x.id === "scenario-01-formula-provenance");
    const good = await real.run(c);
    const bad = await real.run(c, { policy: "poc-bad" });
    const m = (run, g) => graderOf(run.grades, g).measures;
    assert.equal(good.grades.pass, true, JSON.stringify(good.grades.failed));
    assert.deepEqual(good.obs.tools.map((t) => `${t.entry}:${t.status}`), ["native.tcm_formula:succeeded", "native.tcm_compatibility:succeeded"]);
    assert.equal(m(good, "execution").recall, 1);
    assert.equal(m(bad, "execution").recall, 0.5);
    assert.equal(m(good, "evidence").citations_unresolved, 0);
    assert.equal(m(bad, "evidence").citations_unresolved, 1);
    assert.deepEqual(m(good, "evidence").banned_terms, []);
    assert.deepEqual(m(bad, "evidence").banned_terms, ["证明"]);
    assert.equal(bad.obs.status, "ok", "today the bad answer ends ok");
    assert.equal(real.startupEgress + good.obs.egress.length + bad.obs.egress.length, 0, "nothing left the machine");
  } finally {
    await real.close();
    if (saved === undefined) delete process.env.PYTHON; else process.env.PYTHON = saved;
  }
});
