// Cassettes: a case recorded through the fault server's echo of its gold policy replays to the same trajectory and the
// same grades; a changed tool output, prompt or tool list makes exactly the cases it touches cassette_stale; the
// recorder refuses a hidden case; the committed cassettes are well formed. The runner here is a fake (fixtures/fakes.mjs)
// whose answers the tests change; the agent loop, router, client and fault server are the real ones.
import "../../web/test/fixtures/setup.mjs";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { after, before, test } from "node:test";
import { Catalog } from "../../web/js/core/catalog.js";
import { envelopeFor, fakeRuntime, testCatalogDoc } from "../../web/test/fixtures/fakes.mjs";
import { createHarness } from "../lib/harness.mjs";
import { EVAL } from "../lib/util.mjs";
import { CASSETTE_SCHEMA, denoise, recordingLib } from "../models/cassette.mjs";

const answers = { tcm_herb: "甘草：味甘，性平 [E1]", tcm_compatibility: "甘草与甘遂：十八反 [E1]" };
const runner = fakeRuntime("runner", {
  answer: (tool) => envelopeFor(tool, "runner", { text: answers[tool] ?? "{}", citations: [{ id: "E1", kind: "source", label: `${tool} record` }] }),
});
let harness;
before(async () => { harness = await createHarness({ catalog: new Catalog(testCatalogDoc()), runtimes: { runner } }); });
after(async () => { await harness?.close(); });

const caseOf = (id, name, args, extra = {}) => ({
  id, split: "dev", driver: "scripted", tags: ["cassette"], question: "甘草的记载？",
  policies: { gold: { steps: [{ calls: [{ id: "c1", name, args }] }, { text: "记载见 {{cite:c1}}。" }] }, mutants: [] },
  expect: { execution: { gold: [`native.${name}`] } },
  ...extra,
});
const herb = caseOf("cassette-herb", "tcm_herb", { name: "甘草" });
const compat = caseOf("cassette-compat", "tcm_compatibility", { herbs: ["甘草", "甘遂"] });

const view = ({ obs, grades }) => ({
  status: obs.status, answer: obs.assistant?.content, trajectory: obs.tools.map((t) => [t.name, t.args, t.status, t.envelope?.text]),
  pass: grades?.pass, checks: grades?.graders.map((g) => [g.grader, g.checks.map((c) => [c.name, c.pass, c.detail])]),
});

test("a case recorded through the fault server's echo mode replays to an identical trajectory and identical grades", async () => {
  const rec = await harness.run(herb, { mode: "record" });
  assert.equal(rec.grades.pass, true, JSON.stringify(rec.grades.failed));
  assert.equal(rec.cassette.schema, CASSETTE_SCHEMA);
  assert.equal(rec.cassette.source, "echo");
  assert.equal(rec.cassette.recordings.length, 2);
  const replay = await harness.run(herb, { mode: "replay", cassette: rec.cassette });
  assert.equal(replay.obs.stale, null);
  assert.equal(replay.obs.model, "cassette");
  assert.deepEqual(view(replay), view(rec));
});

test("changing one tool's output marks exactly the case that reads it cassette_stale", async () => {
  const a = (await harness.run(herb, { mode: "record" })).cassette;
  const b = (await harness.run(compat, { mode: "record" })).cassette;
  const saved = answers.tcm_herb;
  answers.tcm_herb = "甘草：味甘，性平，归心、肺、脾、胃经 [E1]";
  try {
    const ra = await harness.run(herb, { mode: "replay", cassette: a });
    const rb = await harness.run(compat, { mode: "replay", cassette: b });
    assert.match(ra.obs.stale, /tool results before request 2 differ/);
    assert.equal(ra.grades, null, "a stale replay is neither a pass nor a fail");
    assert.equal(rb.obs.stale, null);
    assert.equal(rb.grades.pass, true);
  } finally {
    answers.tcm_herb = saved;
  }
});

test("a different system prompt or tool list is cassette_stale too", async () => {
  const doc = (await harness.run(herb, { mode: "record" })).cassette;
  const prompt = await harness.run(herb, { mode: "replay", cassette: { ...doc, prompt_sha256: "0".repeat(64) } });
  assert.match(prompt.obs.stale, /system prompt differs/);
  const tools = await harness.run(herb, { mode: "replay", cassette: { ...doc, tools_sha256: "0".repeat(64) } });
  assert.match(tools.obs.stale, /tool definitions differ/);
});

test("the recorder refuses a case tagged hidden", async () => {
  const hidden = { ...herb, id: "cassette-hidden", tags: ["cassette", "hidden"] };
  await assert.rejects(harness.run(hidden, { mode: "record" }), /tagged hidden/);
  assert.throws(() => recordingLib({ streamChat: async function* () {} }, hidden), /never recorded/);
});

test("run-to-run noise in a tool's text (times, durations, generated ids) does not change the key; content does", () => {
  const one = denoise('{"started_at":"2026-11-06T01:02:03Z","duration_ms": 12,"run_id":"r_2a924cff9751"} inq_35ad39a9d99d4cad');
  const two = denoise('{"started_at":"2026-11-07T09:10:11.5Z","duration_ms": 977,"run_id":"r_0000aaaabbbb"} inq_0123456789abcdef');
  assert.equal(one, two);
  assert.notEqual(denoise("甘草 [E1]"), denoise("甘遂 [E1]"));
});

test("the committed cassettes are well formed: their schema, an echo source, a case tagged cassette, the pinned date", () => {
  const dir = path.join(EVAL, "cassettes", "dev");
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  assert.ok(files.length >= 1);
  for (const f of files) {
    const doc = JSON.parse(readFileSync(path.join(dir, f), "utf8"));
    assert.equal(doc.schema, CASSETTE_SCHEMA, f);
    assert.equal(`${doc.case}.json`, f);
    assert.equal(doc.source, "echo", `${f}: committed cassettes are echo recordings until real-model ones are reviewed (plan P3.2)`);
    const c = JSON.parse(readFileSync(path.join(EVAL, "cases", "dev", f), "utf8"));
    assert.ok(c.tags.includes("cassette") && !c.tags.includes("hidden"), f);
    assert.match(doc.prompt_sha256, /^[0-9a-f]{64}$/);
    assert.ok(doc.recordings.every((r, i) => r.step === i && /^[0-9a-f]{64}$/.test(r.results_sha256)), f);
  }
});
