import { resetStorage } from "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { CATEGORIES, CLAIM_KINDS, CLAIM_SUPPORT, CODES, DESIGNS, QUALITY, TIERS, codeInfo, tierOf } from "../js/core/glossary.js";
import { detectLang, glossary, lang, onLangChange, registerStrings, setLang, t } from "../js/core/i18n.js";
import { loadSettings } from "../js/core/settings.js";

test("t(): current language, the other as fallback, then the key; {placeholders}; function values", () => {
  setLang("zh");
  registerStrings("zh", { "test.hello": "你好，{name}" });
  registerStrings("en", { "test.hello": "Hello, {name}", "test.only_en": "only English", "test.count": ({ n }) => `${n} item${n === 1 ? "" : "s"}` });
  assert.equal(t("test.hello", { name: "葛根" }), "你好，葛根");
  assert.equal(t("test.only_en"), "only English");
  assert.equal(t("test.missing"), "test.missing");
  assert.equal(t("test.hello", {}), "你好，{name}", "a missing variable stays visible");
  assert.equal(t("test.count", { n: 2 }, "en"), "2 items");
  assert.throws(() => registerStrings("fr", {}), RangeError);
});

test("setLang persists to settings and tcmscience.lang, and notifies once per change", () => {
  resetStorage();
  setLang("zh");
  const seen = [];
  const off = onLangChange((l) => seen.push(l));
  setLang("en");
  setLang("en");
  assert.equal(lang(), "en");
  assert.equal(loadSettings().lang, "en");
  assert.equal(globalThis.localStorage.getItem("tcmscience.lang"), "en");
  assert.deepEqual(seen, ["en"]);
  off();
  setLang("zh");
  assert.throws(() => setLang("de"), RangeError);
});

test("detectLang: ?lang=, then the saved choice", () => {
  const saved = globalThis.location;
  globalThis.location = { search: "?lang=en" };
  try { assert.equal(detectLang(), "en"); } finally { globalThis.location = saved; }
  globalThis.localStorage.setItem("tcmscience.lang", "zh");
  assert.equal(detectLang(), "zh");
});

test("glossary labels in both languages", () => {
  setLang("zh");
  assert.equal(glossary.evidenceTier(0), "计算预测");
  assert.equal(glossary.evidenceTier("SYSTEMATIC_REVIEW"), "系统评价/荟萃分析");
  assert.equal(glossary.evidenceTier("expert_experience", "en"), "Expert experience / consensus");
  assert.equal(glossary.claimKind("classical_attribution"), "记载");
  assert.equal(glossary.claimKind("clinical_efficacy", "en"), "Efficacy");
  assert.equal(glossary.verdictState("release_authorized"), "准予发布");
  assert.equal(glossary.verdictState("needs_hypotheses"), "需补充假说");
  assert.equal(glossary.verdictState("pending_manual"), "待人工判定");
  assert.equal(glossary.code("CLM005"), "证据等级不能支撑该主张类型");
  assert.equal(glossary.code("CLM005", "en"), "evidence tier is below the floor for this claim kind");
  assert.match(glossary.codeRemedy("INQ102", "en"), /at least two explanations/);
  assert.equal(glossary.code("ART999"), "产物校验（ART999）", "an unknown code is described by its family");
  assert.equal(glossary.category("netpharm"), "网络药理与研究闭环");
  assert.equal(glossary.design("docking"), "分子对接");
  assert.equal(glossary.quality("risk_of_bias", 4), "未评估");
  assert.equal(glossary.quality("consistency", "single_study", "en"), "Single study");
  assert.equal(glossary.family("predicted"), "计算预测");
  assert.equal(tierOf("in_vitro").rank, 3);
  assert.equal(codeInfo("evidence103").code, "EVIDENCE103");
  assert.equal(CATEGORIES.length, 13);
});

test("every code has a zh and an en explanation", () => {
  for (const [code, info] of Object.entries(CODES)) {
    assert.ok(info.zh && info.en, code);
    assert.doesNotMatch(info.zh, /出错了|失败了|！/, `${code}: neutral wording`);
  }
});

// The glossary is checked against the Python it was taken from, when that Python is installed here.
function pythonTables() {
  const script = `
import json
from bioagent.tcm.model import EvidenceTier, CLAIM_SUPPORT
from bioagent.contracts.artifact import _CODES
from bioagent.contracts.candidate_claim import CLAIM_REASONS
from bioagent.contracts.evidence_item import EVIDENCE_TIER_FOR_DESIGN
from bioagent.skills.compiler import COMPILE_CODES
from bioagent.benchmarks.scorers import GATES
from bioagent.contracts import quality as q
from psh.scientist.inquiry import INQUIRY_CODES
from psh.workflow.ir import ClaimType
from psh.sir.values import StudyDesign
print(json.dumps({
  "tiers": [[t.name, int(t), t.chinese] for t in EvidenceTier],
  "support": {k: sorted(int(t) for t in v) for k, v in CLAIM_SUPPORT.items()},
  "art": dict(_CODES), "clm": dict(CLAIM_REASONS), "skill": dict(COMPILE_CODES), "gate": dict(GATES),
  "inq": {k: list(v) for k, v in INQUIRY_CODES.items()},
  "designs": {k: int(v) for k, v in EVIDENCE_TIER_FOR_DESIGN.items()},
  "psh_designs": [d.value for d in StudyDesign], "psh_claims": [c.value for c in ClaimType],
  "quality": {n: [[m.value if isinstance(m.value, str) else m.name.lower(), m.chinese] for m in getattr(q, n)] for n in ["RiskOfBias", "Directness", "Precision", "Consistency"]},
}, ensure_ascii=False))
`;
  try {
    return JSON.parse(execFileSync("python3", ["-I", "-c", script], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60000 }));
  } catch {
    return null;
  }
}

const py = pythonTables();

test("glossary matches the Python tables (bioagent, psh)", { skip: py ? false : "bioagent/psh are not installed for python3" }, () => {
  assert.deepEqual(TIERS.map((x) => [x.name, x.rank, x.zh]), py.tiers);
  assert.deepEqual(CLAIM_SUPPORT, py.support);
  for (const [table, prefix] of [[py.art, "ART"], [py.clm, "CLM"], [py.skill, "SKILL"], [py.gate, "GATE"]]) {
    for (const [code, text] of Object.entries(table)) {
      assert.ok(code.startsWith(prefix));
      assert.equal(CODES[code]?.en, text, code);
    }
  }
  for (const [code, [title, remedy]] of Object.entries(py.inq)) {
    assert.equal(CODES[code]?.en, title, code);
    assert.equal(CODES[code]?.remedy?.en, remedy, code);
  }
  for (const [design, rank] of Object.entries(py.designs)) assert.equal(DESIGNS[design]?.tier, rank, design);
  for (const d of py.psh_designs) assert.ok(DESIGNS[d], `PSH design ${d}`);
  for (const c of py.psh_claims) assert.ok(CLAIM_KINDS[c], `PSH claim type ${c}`);
  const dims = { RiskOfBias: "risk_of_bias", Directness: "directness", Precision: "precision", Consistency: "consistency" };
  for (const [cls, dim] of Object.entries(dims)) {
    assert.deepEqual(QUALITY[dim].values.map(([, zh]) => zh), py.quality[cls].map(([, zh]) => zh), dim);
  }
});
