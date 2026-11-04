// The browser sweep's rule (sweep.mjs): a difference passes only as KNOWN declares it, by entry id and field.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { KNOWN, compareDeclared, judge, locate } from "./sweep.mjs";

const ROOTS = { browser: "/persist", native: "/tmp/tcmstudio-sweep-native-abc123" };

function envelope({ result = {}, text, summary = "ok", status = "succeeded", error = null, receipt = {}, governance = { kind: "native" } } = {}) {
  const body = JSON.stringify(result);
  return {
    status, via: "call_tool", summary, error, governance, citations: [],
    text: text ?? `call_tool: ${status}\nSummary: ${summary}\nResult: ${body}`,
    receipt: { where: "browser", content_hash: "c", input_sha256: "i", versions: { tcmstudio: "0.1.0" }, output_sha256: createHash("sha256").update(body).digest("hex"), ...receipt },
    result,
  };
}
const call = (id, group = "entry") => ({ key: `${group}:${id}`, group, id, tool: "call_tool", input: "example", args: {} });
const rec = (env) => ({ envelope: env, ms: 1 });
const nat = (env) => ({ envelope_json: JSON.stringify(env), ms: 1 });

test("identical envelopes are the same; an undeclared difference fails, whatever the entry", () => {
  const e = envelope({ result: { a: 1 } });
  assert.equal(judge(call("native.anion_gap"), rec(e), nat(e), ROOTS).result, "same");
  const row = judge(call("native.anion_gap"), rec(e), nat(envelope({ result: { a: 2 } })), ROOTS);
  assert.equal(row.result, "differs");
  assert.equal(row.ok, false);
  assert.deepEqual(row.diffs.map((d) => d.field), ["result", "receipt.output_sha256", "text"]);
});

test("the state root: written <state root> on each side for the entries that declare it, and nowhere else", () => {
  const b = envelope({ result: { store: "/persist/p/tcmdb/db/x.sqlite", records: [] } });
  const n = envelope({ result: { store: `${ROOTS.native}/p/tcmdb/db/x.sqlite`, records: [] } });
  const row = judge(call("native.hk_cmm_dna_lookup"), rec(b), nat(n), ROOTS);
  assert.equal(row.result, "declared", JSON.stringify(row.diffs));
  assert.deepEqual(row.declared.map((d) => d.field), ["the state root", "receipt.output_sha256"]);
  // an entry that does not declare it is held to the bytes
  assert.equal(judge(call("native.anion_gap"), rec(b), nat(n), ROOTS).result, "differs");
  // and the declared entry still fails on anything else
  const more = envelope({ result: { store: `${ROOTS.native}/p/tcmdb/db/x.sqlite`, records: [1] } });
  assert.equal(judge(call("native.hk_cmm_dna_lookup"), rec(b), nat(more), ROOTS).result, "differs");
});

test("a declared leaf is set aside in the result and, as a string, in the text; the rest of the result still counts", () => {
  const head = (h, records) => envelope({ result: { intact: true, records, head_hash: h, location: "/persist/p/psh/events.db" } });
  const nhead = (h, records) => envelope({ result: { intact: true, records, head_hash: h, location: `${ROOTS.native}/p/psh/events.db` } });
  const row = judge(call("system.audit_verify"), rec(head("aaaa", 12)), nat(nhead("bbbb", 12)), ROOTS);
  assert.equal(row.result, "declared", JSON.stringify(row.diffs));
  assert.deepEqual(row.declared.map((d) => d.field), ["the state root", "result.head_hash", "receipt.output_sha256"]);
  const fewer = judge(call("system.audit_verify"), rec(head("aaaa", 12)), nat(nhead("bbbb", 11)), ROOTS);
  assert.equal(fewer.result, "differs");
  assert.ok(fewer.diffs.some((d) => d.field === "result"));
});

test("host facts: the declared subtrees set aside, the text compared up to its Result: line", () => {
  const caps = (runtime, deps, host, extra = {}) => envelope({ result: { where: "browser", runtime, versions: { python: runtime.slice(-6) }, dependencies: deps, ...(host ? { host } : {}), formulas: { workbook: { status: host ? "on_demand" : "available" } }, catalog: { entries: 653 }, ...extra } });
  const b = caps("pyodide-314.0.7 / CPython 3.14.2", { scipy: false }, { browser: { cores: 4 } });
  const n = caps("CPython 3.12.3 / linux-x86_64", { scipy: true }, null);
  const row = judge(call("capabilities_status", "core"), rec(b), nat(n), ROOTS);
  assert.equal(row.result, "declared", JSON.stringify(row.diffs));
  assert.ok(row.declared.some((d) => d.field === "text after its Result: line"));
  const other = judge(call("capabilities_status", "core"), rec(b), nat(caps("CPython 3.12.3 / linux-x86_64", { scipy: true }, null, { catalog: { entries: 652 } })), ROOTS);
  assert.equal(other.result, "differs");
  assert.deepEqual(other.diffs.map((d) => d.field), ["result", "receipt.output_sha256"]);
});

test("the capability audit: only the verification count and its limitation are set aside (BS-06)", () => {
  const audit = (pass, limitation) => envelope({
    summary: `能力审计（浏览器运行时）：653 项，就绪 179，未联网 444；已提交核验记录为通过的 ${pass} 项`,
    result: { counts: { entries: 653, by_verification: { PASS: pass }, by_implementation: { tested: pass } } },
  });
  const b = audit(82);
  b.text = b.text.replace("\nResult: ", "\n- This runtime does not carry the committed connector verification record (…), so its connectors read UNVERIFIED here\nResult: ");
  assert.equal(judge(call("system.capability_audit"), rec(b), nat(audit(526)), ROOTS).result, "declared");
  const ready = audit(526);
  ready.summary = ready.summary.replace("就绪 179", "就绪 178");
  assert.equal(judge(call("system.capability_audit"), rec(b), nat(ready), ROOTS).result, "differs");
});

test("study.effect_modification: last-bit differences within RELATIVE are declared with the largest; more fail (BS-04)", () => {
  const fit = (estimate, p) => envelope({ result: { modifiers: [{ interaction: { estimate, p_value: p, df: 4 } }] } });
  const row = judge(call("study.effect_modification", "extra"), rec(fit(0.039999999999999966, 0.04742065558431962)), nat(fit(0.04000000000000003, 0.04742065558431954)), ROOTS);
  assert.equal(row.result, "declared", JSON.stringify(row.diffs));
  const numbers = row.declared.find((d) => d.field === "the numbers of result and text");
  assert.ok(numbers.largest_relative > 0 && numbers.largest_relative < 1e-14);
  assert.equal(judge(call("study.effect_modification", "extra"), rec(fit(0.04, 0.0474206)), nat(fit(0.04, 0.0474207)), ROOTS).result, "differs");
  // the same last bits in a tool that declares nothing fail
  assert.equal(judge(call("study.contrast_formula_monomer"), rec(fit(0.039999999999999966, 0.05)), nat(fit(0.04000000000000003, 0.05)), ROOTS).result, "differs");
});

test("an output whose digest is set aside is compared by its content, apart from its declared leaves", () => {
  const skill = (sha, outDir, formula, source) => envelope({
    governance: { kind: "skill", released: false, refusals: [], verdict: { states: {} }, claims: [], evidence: [],
      outputs: [{ path: "clinic_summary.json", sha256: sha, bytes: 10, content: { out_dir: outDir, formula } }, { path: "report.md", sha256: "r" }],
      artifact: { composite_version: { skill: "draft-tcm-prescription@0.1.0", source } } },
  });
  const b = skill("s1", "/persist/p/runs/r_1/clinic", "四君子汤", "x1");
  assert.equal(judge(call("skill.draft-tcm-prescription", "extra"), rec(b), nat(skill("s2", "/tmp/n/p/runs/r_2/clinic", "四君子汤", "x2")), ROOTS).result, "declared");
  const row = judge(call("skill.draft-tcm-prescription", "extra"), rec(b), nat(skill("s2", "/tmp/n/p/runs/r_2/clinic", "六君子汤", "x2")), ROOTS);
  assert.equal(row.result, "differs");
  assert.deepEqual(row.diffs.map((d) => d.field), ["governance.outputs[clinic_summary.json].content"]);
});

test("failures on one side, timeouts and connectors that are not refused as network_off fail", () => {
  const ok = envelope({ result: { a: 1 } });
  const broke = envelope({ status: "failed", error: { type: "runtime_error" }, result: null });
  assert.equal(judge(call("native.anion_gap"), rec(broke), nat(ok), ROOTS).result, "browser_failure");
  assert.equal(judge(call("native.anion_gap"), rec(ok), nat(broke), ROOTS).result, "native_failure");
  assert.equal(judge(call("native.anion_gap"), { timeout: true, ms: 125_000 }, nat(ok), ROOTS).result, "timeout");
  assert.equal(judge(call("native.anion_gap"), rec(ok), null, ROOTS).result, "not_run");
  const off = envelope({ status: "failed", error: { type: "network_off" }, result: null });
  assert.equal(judge(call("connector.uniprot.entry", "connector"), rec(off), nat(off), ROOTS).result, "same");
  assert.equal(judge(call("connector.uniprot.entry", "connector"), rec(ok), nat(ok), ROOTS).result, "not_network_off");
});

test("every declaration names its fields and gives a reason; paths select array elements by their path", () => {
  for (const [id, rule] of Object.entries(KNOWN)) {
    assert.ok(rule.roots || rule.leaves || rule.masks || rule.numbers, id);
    if (rule.roots) assert.match(rule.roots, /state root/, id);
    for (const [field, why] of Object.entries(rule.leaves || {})) assert.ok(typeof why === "string" && why.length > 10, `${id} ${field}`);
    for (const [pattern, why] of rule.masks || []) assert.ok(pattern.global && why.length > 10, `${id} ${pattern}`);
    if (rule.numbers) assert.ok(rule.numbers[0] > 0 && rule.numbers[0] <= 1e-12 && rule.numbers[1].length > 10, id);
  }
  const env = { governance: { outputs: [{ path: "a.json", sha256: "1" }, { path: "b.json", sha256: "2" }] } };
  assert.equal(locate(env, "governance.outputs[b.json].sha256").value, "2");
  assert.equal(locate(env, "governance.outputs[c.json].sha256"), null);
  assert.deepEqual(compareDeclared("native.anion_gap", envelope(), envelope()), { diffs: [], declared: [] });
});
