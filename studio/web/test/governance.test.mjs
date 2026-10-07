import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  citationsOf, claimsOf, compositeVersionOf, evidenceOf, familyOfTier, identifierUrl, isPredicted, licensingLadder, refusalsOf,
  releaseOf, shortHash, stateOfUnverified, verdictBadge,
} from "../js/core/governance.js";
import { loadFixture } from "./fixtures/fakes.mjs";

// envelopes around real kernel output (see fixtures/make_governance_fixtures.py)
const released = loadFixture("envelope_safety_released.json");
const unreleased = loadFixture("envelope_network_unreleased.json");
const refused = loadFixture("envelope_claims_refused.json");

test("familyOfTier: ranks, enum names, document() tiers and designs of both vocabularies", () => {
  assert.equal(familyOfTier(0), "predicted");
  assert.equal(familyOfTier("CLASSICAL_TEXT"), "tradition");
  assert.equal(familyOfTier("expert_experience"), "tradition");
  assert.equal(familyOfTier("preclinical"), "bench");
  assert.equal(familyOfTier("in_vitro"), "bench");
  assert.equal(familyOfTier("randomized_trial"), "clinical");
  assert.equal(familyOfTier("randomised_trial"), "clinical");
  assert.equal(familyOfTier("cohort"), "clinical");
  assert.equal(familyOfTier("docking"), "predicted");
  assert.equal(familyOfTier("network_prediction"), "predicted");
  assert.equal(familyOfTier("nonsense"), null);
  assert.equal(isPredicted({ design: "molecular_dynamics" }), true);
});

test("release: a governed run that is released passes all six states", () => {
  const rel = releaseOf(released);
  assert.equal(rel.authorized, true);
  assert.equal(rel.passed, 6);
  assert.deepEqual(rel.states.map((s) => s.id), ["schema_valid", "evidence_verified", "outputs_verified", "execution_declared", "execution_attested", "release_authorized"]);
  assert.ok(rel.states.every((s) => s.ok && s.reason === ""));
  assert.equal(rel.summary, "发布状态 6/6 · 已准予发布");
  assert.deepEqual(rel.badge, { tone: "released", icon: "✓", label: "已准予发布" });
  assert.equal(rel.warnings[0].code, "ART116");
  assert.match(rel.warnings[0].explanation, /外推/);
  assert.equal(rel.outputs[0].path, "safety.json");
  assert.match(rel.outputs[0].sha256, /^[0-9a-f]{64}$/);
  assert.ok(rel.limitations.length > 0, "limitations are always there");
  assert.match(rel.audit_head, /^[0-9a-f]{64}$/);
  assert.equal(compositeVersionOf(released).find((a) => a.axis === "benchmark").unversioned, true);
});

test("release: an ungoverned run is consistent but not verified, and each unmet state says why", () => {
  const rel = releaseOf(unreleased);
  assert.equal(rel.authorized, false);
  assert.equal(rel.publishable, true);
  assert.equal(rel.passed, 2);
  assert.equal(rel.summary, "发布状态 2/6 · 未准予发布");
  const byId = Object.fromEntries(rel.states.map((s) => [s.id, s]));
  assert.match(byId.outputs_verified.reason, /network\.json/);
  assert.match(byId.execution_attested.reason, /policy_id\/audit_head/);
  assert.equal(byId.execution_declared.ok, false);
  assert.match(byId.release_authorized.reason, /输出已核验/);
  assert.deepEqual(verdictBadge(rel), { tone: "consistent", icon: "◐", label: "内部一致 · 未核验" });
});

test("release: refused claims make the artifact unpublishable, with the violation codes as reasons", () => {
  const rel = releaseOf(refused);
  assert.equal(rel.publishable, false);
  assert.equal(rel.states[0].ok, false);
  assert.match(rel.states[0].reason, /ART105|ART106/);
  assert.ok(rel.codes.includes("ART106"));
  assert.equal(rel.badge.tone, "refused");
  assert.equal(releaseOf({ status: "succeeded", governance: { kind: "native" } }), null, "no verdict, no release panel");
});

test("claims joined with their verdicts: allowed with caveats, refused by tier, prediction stated as fact", () => {
  const [safety] = claimsOf(released);
  assert.equal(safety.kind, "traditional_use");
  assert.equal(safety.kind_label, "传统应用");
  assert.equal(safety.allowed, true);
  assert.equal(safety.weakest_tier, "expert_experience");
  assert.equal(safety.family, "tradition");
  assert.ok(safety.caveats.length > 0);
  assert.deepEqual(safety.extrapolations, ["outcome:recorded safety information"]);
  assert.equal(safety.falsified_by, "a corpus record that contradicts the table above");
  assert.deepEqual(verdictBadge(safety), { tone: "caveat", icon: "!", label: "允许 · 附说明" });

  const claims = Object.fromEntries(claimsOf(refused).map((c) => [c.id, c]));
  const eff = claims["claim.efficacy"];
  assert.equal(eff.allowed, false);
  assert.ok(eff.codes.includes("CLM005"));
  assert.equal(eff.weakest_tier, "preclinical");
  assert.equal(eff.family, "bench");
  assert.deepEqual(eff.licensed_by, [6, 7]);
  assert.equal(eff.clinical, true);
  assert.deepEqual(verdictBadge(eff), { tone: "refused", icon: "✕", label: "已拒绝" });
  const pred = claims["claim.prediction"];
  assert.equal(pred.prediction_as_fact, true);
  assert.ok(pred.codes.includes("CLM004"));
  assert.equal(pred.family, "predicted");
  assert.equal(verdictBadge(pred).tone, "prediction");
  const hyp = claims["claim.hypothesis"];
  assert.equal(hyp.allowed, true);
  assert.equal(hyp.kind_label, "机制假说");
  assert.equal(verdictBadge(hyp, undefined, "en").label, "Allowed · with caveats");
});

test("evidence views: family, design, tier, quality without a total, source card", () => {
  const ev = evidenceOf(released);
  assert.ok(ev.length >= 2);
  const shibafan = ev.find((e) => e.id === "safety.safety.shibafan_gancao_gansui");
  assert.equal(shibafan.family, "tradition");
  assert.equal(shibafan.tier, "classical_text");
  assert.equal(shibafan.tier_rank, 1);
  assert.equal(shibafan.tier_label, "经典文献记载");
  assert.equal(shibafan.quote_verified, true);
  assert.equal(shibafan.quality.risk_of_bias.zh, "未评估");
  assert.equal(shibafan.quality.risk_of_bias.assessed, false);
  assert.equal(shibafan.quality.directness.zh, "直接证据");
  assert.equal("total" in shibafan.quality || "score" in shibafan.quality, false);
  assert.equal(shibafan.source.id, "tcmscience.tcm.seed");
  assert.equal(shibafan.source.pinned, true);
  assert.equal(shibafan.source.licence, "MIT");
  const r = Object.fromEntries(evidenceOf(refused).map((e) => [e.id, e]));
  assert.equal(r["ev.docking"].predicted, true);
  assert.equal(r["ev.docking"].family, "predicted");
  assert.equal(r["ev.invitro"].url, "https://pubmed.ncbi.nlm.nih.gov/12345678/");
  assert.equal(r["ev.invitro"].quality.consistency.zh, "单一研究");
});

test("citations: the envelope's own, joined to their evidence; derived E1… when none are given", () => {
  const cites = citationsOf(released);
  assert.equal(cites[0].id, "E1");
  assert.equal(cites[0].family, "tradition");
  assert.equal(cites[0].quote_verified, true);
  const derived = citationsOf({ governance: { evidence: refused.governance.evidence } });
  assert.deepEqual(derived.map((c) => [c.id, c.evidence_ref, c.kind]), [["E1", "ev.invitro", "pmid"], ["E2", "ev.docking", "source"]]);
  assert.equal(derived[0].url, "https://pubmed.ncbi.nlm.nih.gov/12345678/");
  const unsafe = citationsOf({ citations: [{ id: "E1", url: "javascript:alert(1)", label: "x" }] });
  assert.equal(unsafe[0].url, "", "only http(s) links survive");
  assert.equal(identifierUrl("doi", "10.1000/xyz"), "https://doi.org/10.1000/xyz");
  assert.equal(identifierUrl("nct", "NCT01234567"), "https://clinicaltrials.gov/study/NCT01234567");
});

test("refusals with their glossary explanation", () => {
  const rs = refusalsOf(refused);
  const clm5 = rs.find((r) => r.code === "CLM005");
  assert.ok(clm5);
  assert.equal(clm5.explanation, "证据等级不能支撑该主张类型");
  assert.equal(clm5.family, "CLM");
  assert.ok(rs.some((r) => r.code === "ART106"));
});

test("the licensing ladder: eight tiers, the licensing set, the claim's weakest tier, the caption", () => {
  const lad = licensingLadder("efficacy", "preclinical", "zh");
  assert.equal(lad.cells.length, 8);
  assert.deepEqual(lad.cells.filter((c) => c.licenses).map((c) => c.rank), [6, 7]);
  assert.equal(lad.cells.find((c) => c.weakest).rank, 3);
  assert.equal(lad.supports, false);
  assert.equal(lad.caption, "主张类型：疗效 —— 需要 随机对照试验 或 系统评价/荟萃分析；本主张最弱证据：临床前研究 → 不支撑");
  const ok = licensingLadder("mechanism_hypothesis", "computational_prediction", "en");
  assert.equal(ok.supports, true);
  assert.match(ok.caption, /Mechanism hypothesis/);
  assert.equal(licensingLadder("clinical_efficacy", 6).kind, "efficacy", "PSH's vocabulary maps to the same kind");
});

test("badges for every vocabulary carry an icon and words", () => {
  assert.deepEqual(verdictBadge("accepted", "inquiry"), { tone: "jade", icon: "✓", label: "已接受" });
  assert.deepEqual(verdictBadge("needs_hypotheses", "inquiry"), { tone: "ochre", icon: "✎", label: "需补充假说" });
  assert.deepEqual(verdictBadge("pending_manual", "goal"), { tone: "ochre", icon: "◐", label: "待人工判定" });
  assert.deepEqual(verdictBadge("draft", "artifact"), { tone: "draft", icon: "○", label: "草稿" });
  assert.equal(verdictBadge("refused", "call").tone, "refused");
  assert.equal(verdictBadge({ status: "refused", receipt: {}, governance: {} }).label, "已拒绝");
  assert.equal(verdictBadge(released).tone, "released");
  assert.equal(verdictBadge({ allowed: false, needs_declaration: true, codes: ["CLM009"] }).tone, "declaration");
  for (const b of [verdictBadge("verified", "goal"), verdictBadge(unreleased), verdictBadge({ allowed: null, codes: [] })]) {
    assert.ok(b.icon && b.label);
  }
});

test("hashes are shown as sha256:first8…last4", () => {
  assert.equal(shortHash("0da551c093b58f51724d7ab206890c783280d79b008fd7d1ac9c5b45fe1f626d"), "sha256:0da551c0…626d");
  assert.equal(shortHash(""), "");
  assert.equal(stateOfUnverified("evidence 'x': receipt not re-checked"), "evidence_verified");
});
