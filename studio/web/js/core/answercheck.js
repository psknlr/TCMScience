// The answer check: what an answer says, read against what the turn's tools returned and what the governance rule told
// the model (prompt.js). It is report-only: it never changes, hides or holds back an answer. The page shows its
// findings as a badge on the message with the details in the inspector, the usage event counts them (numbers only, no
// text: telemetry-contract.js), and the agent eval's evidence grader is to import this same module (answercheck.test.mjs
// holds it to that once studio/eval exists). Pure: no DOM, no model, no network; the same answer and envelopes always
// give the same findings.
//
// Findings, each {kind, …}:
//   unresolved_citation    an [E#] that no tool returned anywhere in the conversation (the page resolves an earlier
//                          turn's ids too: ui/conversation.js messageContext), {id}
//   banned_term            a word the rule forbids whatever the evidence (prompt.js BANNED_TERMS), outside the forms
//                          that negate or only mention it (无记录不等于安全, 不能证明, not proven, 「证明」), {term, use, at}
//   released_wording       released, 已发布 or 已放行 said of a result while no envelope of the turn is release-authorized,
//                          {term, at}
//   refusal_unreported     the turn's kernel refused claims, and the answer gives neither their code nor any word of
//                          refusal, {codes}
//   uncited_with_evidence  the turn's tools returned citations and the answer cites none, {citations}
//   overclaim_inquiry      accepted or established wording while every inquiry concluded in the turn is provisional,
//                          undetermined or tentative (explicit word lists only), {term, at, verdict, certainty}
//
// The word rules read clauses, not meaning: a term is let through when a negation, a question or a condition governs
// it in its own clause (不等于, 不能, 是否, not, whether, would …), when it is quoted as a word, in code, or in a link.
// They are tuned on studio/web/test/fixtures/answercheck-corpus.json (answercheck.test.mjs holds the precision and
// recall each rule must keep).

import { citationsOf, claimsOf, refusalsOf, releaseOf } from "./governance.js";
import { BANNED_TERMS } from "./prompt.js";

export const KINDS = Object.freeze(["unresolved_citation", "banned_term", "released_wording", "refusal_unreported", "uncited_with_evidence", "overclaim_inquiry"]);
/** The rules on by default; overclaim_inquiry, the least certain, can be left out (`rules`) if its precision drops. */
export const RULES = KINDS;

const CITE = /[[【]\s*(E\d+(?:\s*[,，、;；]\s*E\d+)*)\s*[\]】]/g;

/** The citation ids an answer cites ([E1], [E2, E3], 【E4】). */
export function citedIds(text) {
  const out = new Set();
  for (const m of String(text || "").matchAll(CITE)) for (const id of m[1].split(/\s*[,，、;；]\s*/)) out.add(id.trim());
  return out;
}

// ------------------------------------------------------------------------------------------------- reading the text

// where a clause ends: its punctuation, the words that turn it (但 / however), and an "and" that starts a new predicate
// (not toxic and is safe): a negation before them does not govern what follows
const BOUNDARY = /[。！？；!?;\n，,]|但是|但|然而|不过|却|而是|而且|反而|\b(?:but|however|although|though|whereas)\b|\band\s+(?=(?:is|are|was|were|can|has|have|it)\b)/gi;
// what negates, asks or doubts what follows in its clause (Chinese: a multi-character form; a bare 不 only right before
// the term, so 不仅、不但 and 非常 never count)
const NEGATION_ZH = /不等于|不代表|不意味着|不说明|不表示|不能|不可|不足以|不一定|不宜|不应|不会|不是|不再|未必|并非|并不|并未|尚未|尚不|尚无|尚缺|未经|未被|未能|未获|未有|未证|未确|未表明|未显示|没有|没|无法|无从|无证据|缺乏|缺少|难以|是否|能否|可否|是不是|有没有|≠|否认/;
const NEGATION_EN = /\b(?:not|no|never|cannot|nor|neither|without|lacks?|lacking|absence|unclear|whether|if|none|nothing|yet to)\b|n['’]t\b|≠/i;
// what makes a word a condition or a limit rather than a statement (需要随机对照试验才能证明, would be needed to prove)
const HEDGE_ZH = /如果|若|待|只有|才能|才可|仍需|尚需|需要|一旦|之前|以前|至多|才会|假如|除非/;
const HEDGE_EN = /\b(?:would|could|might|until|unless|only after|before|once|at most)\b/i;
// … or right after it (被接受的条件是, accepted only if)
const HEDGE_AFTER = /^(?:的(?:条件|前提|标准)|还需|尚需|仍需|\s+(?:only\s+)?(?:if|once|when|provided|unless)\b)/i;
const OPEN_QUOTE = "「“\"'‘『《";
const CLOSE_QUOTE = "」”\"'’』》";
const ZH_WINDOW = 12; // characters before a Chinese term a negation may stand
const EN_WINDOW = 6; // words before an English term

/** The answer's prose: code (fenced and inline), link targets and bare URLs blanked to spaces, so positions hold. */
function prose(text) {
  const blank = (m) => m.replace(/[^\n]/g, " ");
  return String(text || "")
    .replace(/```[\s\S]*?(?:```|$)/g, blank)
    .replace(/~~~[\s\S]*?(?:~~~|$)/g, blank)
    .replace(/`[^`\n]+`/g, blank)
    .replace(/\]\([^)\s]*\)/g, blank)
    .replace(/\bhttps?:\/\/[^\s)）\]]+/g, blank);
}

/** The text of the clause before position `at` (back to its boundary). */
function clauseBefore(text, at) {
  const head = text.slice(Math.max(0, at - 120), at);
  let start = 0;
  for (const m of head.matchAll(BOUNDARY)) start = m.index + m[0].length;
  return head.slice(start);
}

/** The text of the clause after the term (up to its boundary). */
function clauseAfter(text, end) {
  const tail = text.slice(end, end + 120);
  BOUNDARY.lastIndex = 0;
  const m = BOUNDARY.exec(tail);
  BOUNDARY.lastIndex = 0;
  return m ? tail.slice(0, m.index) : tail;
}

/** Does the sentence holding position `at` end with a question mark? */
function inQuestion(text, at) {
  const m = /[。！？!?\n]|\.(?=\s|$)/.exec(text.slice(at));
  return Boolean(m) && (m[0] === "？" || m[0] === "?");
}

const isCJK = (s) => /[\u3400-\u9fff]/.test(s);

/** Is the term at [at, end) negated, asked about, made a condition, or quoted as a word? */
function letThrough(text, at, end) {
  if (OPEN_QUOTE.includes(text[at - 1] || "\u0000") && CLOSE_QUOTE.includes(text[end] || "\u0000")) return true;
  if (inQuestion(text, at) || HEDGE_AFTER.test(text.slice(end, end + 20))) return true;
  const before = clauseBefore(text, at);
  const near = before.slice(-ZH_WINDOW);
  const words = before.split(/\s+/).filter(Boolean).slice(-EN_WINDOW).join(" ");
  if (isCJK(text.slice(at, end))) return /不$/.test(near) || NEGATION_ZH.test(near) || HEDGE_ZH.test(near) || NEGATION_EN.test(near);
  return NEGATION_EN.test(words) || HEDGE_EN.test(words) || NEGATION_ZH.test(near);
}

// the forms of an English term that say the same (proves, proved; safely)
const FORMS = { proven: ["proven", "prove", "proves", "proved", "proving"], discovered: ["discovered", "discover", "discovers", "discovering"], safe: ["safe", "safely"] };

/** Every occurrence of a term: Chinese by its characters, English as a whole word in any of its forms, any case. →
 * [{at, end}] in text order. */
function occurrences(text, term) {
  const out = [];
  if (isCJK(term)) {
    for (let i = text.indexOf(term); i >= 0; i = text.indexOf(term, i + term.length)) out.push({ at: i, end: i + term.length });
    return out;
  }
  const words = (FORMS[term] || [term]).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+"));
  const re = new RegExp(`(?<![\\w-])(?:${words.join("|")})(?![\\w-])`, "gi");
  for (const m of text.matchAll(re)) out.push({ at: m.index, end: m.index + m[0].length });
  return out;
}

// ------------------------------------------------------------------------------------------------- banned terms

// The rule replaces these for a prediction (预测, not 发现/揭示) and a result that is not significant (not irrelevant);
// in other sentences they are ordinary words (一项随机对照试验发现…, irrelevant to the question), so the check leaves
// them to the forms the rule names whole (发现了机制).
const CONTEXTUAL = new Set(["发现", "揭示", "irrelevant"]);
// what makes a banned word part of another word that says nothing forbidden: 安全信号, 安全性评价, 精准医学, a safe harbor
const COMPOUND = {
  安全: {
    after: /^(?:信号|性(?!良好|好|高|较高|较好|很好|很高|可靠|佳|有保障|无虞|确切)|报告|警戒|评价|评估|数据|问题|风险|隐患|监测|监管|事件|研究|证据|记录|信息|考虑|管理|用药)/,
    before: /(?:用药|食品|生物|信息|网络|患者)$/,
  },
  证明: { after: /^(?:书|文件|材料|信)/ },
  精准: { after: /^(?:医学|医疗)/ },
  safe: { after: /^[\s-]+(?:harbou?r|mode|search|word|use|handling|practice|storage)\b/i },
};

function bannedTerms(text) {
  const out = [];
  for (const b of BANNED_TERMS) {
    if (CONTEXTUAL.has(b.term)) continue;
    const c = COMPOUND[b.term] || {};
    for (const { at, end } of occurrences(text, b.term)) {
      if (c.after?.test(text.slice(end, end + 8)) || c.before?.test(text.slice(Math.max(0, at - 4), at))) continue;
      // inside a longer banned term found as well (none today): reported once, by the longer
      if (out.some((f) => at >= f.at && end <= f.end)) continue;
      if (letThrough(text, at, end)) continue;
      out.push({ kind: "banned_term", term: b.term, form: text.slice(at, end), lang: b.lang, use: b.use, at, end });
    }
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- released wording

const RELEASED = ["released", "已发布", "已放行"];
// a release in the kernel's sense is said of a result (the result is released), not of a cytokine or a dataset
const RESULT_EN = /\b(?:result|results|artifact|artefact|artifacts|artefacts|analysis|analyses|output|outputs|finding|findings|conclusion|answer|claim|claims|report|verdict|this)\b/i;
const NOT_RESULT_EN = /^\s+(?:in|on|by|from|into|at|during|under|to)\b/i;
// 已发布 of a guideline, a paper or a dataset is publication, not a release
const PUBLISHED_ZH = /指南|论文|文献|文章|期刊|杂志|数据集|数据库|版本|标准|共识|药典|说明书|公告|专利|综述|研究|试验|报道|新闻|政策|法规|目录/;

function releasedWording(text) {
  const out = [];
  for (const term of RELEASED) {
    for (const { at, end } of occurrences(text, term)) {
      const before = clauseBefore(text, at);
      const after = clauseAfter(text, end);
      if (term === "released" && (!RESULT_EN.test(before) || NOT_RESULT_EN.test(text.slice(end)))) continue;
      if (term === "已发布" && (PUBLISHED_ZH.test(before + after) || /^(?:于|在)/.test(after))) continue;
      if (letThrough(text, at, end)) continue;
      out.push({ kind: "released_wording", term, at, end });
    }
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- refusals

// any word that says the kernel refused (the code itself counts too): 已拒绝, 未被许可, not licensed, does not support …
const REFUSAL_WORDS = /拒绝|驳回|(?:不|未|没有?)(?:能|被|予|获)?(?:内核)?(?:许可|允许|支持|通过)|\brefus|\breject|\b(?:not|never|cannot)\s+(?:be\s+)?(?:licen[cs]ed|allowed|permitted|supported)\b|\b(?:do|does|did|can)\s*(?:not|n['’]t)\s+(?:licen[cs]e|support|allow|permit)\b|\bcannot\s+(?:licen[cs]e|support)\b|\bdisallow/i;

/** The codes of the claims the kernel refused in these envelopes (CLM…, from the claims and the refusals). */
export function refusedClaimCodes(envelopes = []) {
  const codes = new Set();
  let refused = 0;
  for (const env of envelopes) {
    for (const c of safe(() => claimsOf(env), [])) {
      if (c.allowed !== false) continue;
      refused++;
      for (const code of c.codes || []) codes.add(String(code).toUpperCase());
    }
    for (const r of safe(() => refusalsOf(env), [])) {
      if (!/^CLM\d+$/i.test(String(r.code || ""))) continue;
      refused++;
      codes.add(String(r.code).toUpperCase());
    }
  }
  return { refused, codes: [...codes].sort() };
}

// ------------------------------------------------------------------------------------------------- inquiry

const WEAK_VERDICTS = new Set(["provisional", "undetermined", "needs_hypotheses"]);
const WEAK_CERTAINTY = new Set(["tentative", "uncertain"]);
const SETTLED = ["accepted", "established", "confirmed", "definitive", "definitively", "conclusive", "conclusively", "settled", "beyond doubt",
  "已接受", "被接受", "接受了", "判定为接受", "结论为接受", "结论是接受", "确立", "证实", "定论", "确定无疑", "毫无疑问", "已确认", "已确定", "得到确认"];

/** The inquiry conclusions in these envelopes (research_inquiry conclude): [{verdict, certainty, leader}]. */
export function inquiryConclusionsOf(envelopes = []) {
  const out = [];
  for (const env of envelopes) {
    const c = env?.result?.conclusion;
    if (c && typeof c === "object" && (c.verdict || c.certainty)) out.push({ verdict: String(c.verdict || ""), certainty: String(c.certainty || ""), leader: c.leader || null });
  }
  return out;
}

function overclaim(text, conclusions) {
  // only when every conclusion of the turn is weak: with an accepted one beside it, the words may be about that one
  if (!conclusions.length || !conclusions.every((c) => WEAK_VERDICTS.has(c.verdict) || WEAK_CERTAINTY.has(c.certainty))) return [];
  const weak = conclusions[0];
  const out = [];
  for (const term of SETTLED) {
    for (const { at, end } of occurrences(text, term)) {
      if (out.some((f) => at < f.end && end > f.at)) continue;
      if (letThrough(text, at, end)) continue;
      out.push({ kind: "overclaim_inquiry", term, at, end, verdict: weak.verdict, certainty: weak.certainty });
    }
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- the check

/** The ids an envelope list's citations carry. */
export function citationIdsOf(envelopes = []) {
  const ids = new Set();
  for (const env of envelopes) for (const c of safe(() => citationsOf(env), [])) ids.add(c.id);
  return ids;
}

/**
 * checkAnswer(content, {turnEnvelopes, conversationCites, inquiryConclusions, rules}) → {findings, counts}.
 * turnEnvelopes: the envelopes of this turn's tool calls; conversationCites: the citation ids any tool of the
 * conversation returned (a Set, an array, or a function id → boolean; default: the turn's own); inquiryConclusions:
 * [{verdict, certainty}] (default: read from the turn's envelopes); rules: the kinds to check (default RULES).
 * findings are in the order of the answer's text, each with its kind; counts: {kind: n} for every kind. An empty
 * answer has no findings: nothing was said.
 */
export function checkAnswer(content, { turnEnvelopes = [], conversationCites = null, inquiryConclusions = null, rules = RULES } = {}) {
  const raw = typeof content === "string" ? content : "";
  const envs = (Array.isArray(turnEnvelopes) ? turnEnvelopes : []).filter((e) => e && typeof e === "object");
  const on = new Set(rules);
  const findings = [];
  if (raw.trim()) {
    const text = prose(raw);
    const turnIds = citationIdsOf(envs);
    const known = typeof conversationCites === "function" ? conversationCites
      : conversationCites ? ((s) => (id) => s.has(id))(new Set(conversationCites)) : (id) => turnIds.has(id);
    const cited = citedIds(raw);
    if (on.has("unresolved_citation")) {
      for (const id of cited) if (!known(id) && !turnIds.has(id)) findings.push({ kind: "unresolved_citation", id, at: raw.search(new RegExp(`\\b${id}\\b`)) });
    }
    if (on.has("banned_term")) findings.push(...bannedTerms(text));
    if (on.has("released_wording") && !envs.some((e) => safe(() => releaseOf(e), null)?.authorized)) findings.push(...releasedWording(text));
    if (on.has("refusal_unreported")) {
      const { refused, codes } = refusedClaimCodes(envs);
      const told = codes.some((c) => new RegExp(`\\b${c}\\b`, "i").test(raw)) || REFUSAL_WORDS.test(text);
      if (refused && !told) findings.push({ kind: "refusal_unreported", codes, at: -1 });
    }
    if (on.has("uncited_with_evidence") && turnIds.size && !cited.size) findings.push({ kind: "uncited_with_evidence", citations: turnIds.size, at: -1 });
    if (on.has("overclaim_inquiry")) findings.push(...overclaim(text, Array.isArray(inquiryConclusions) ? inquiryConclusions : inquiryConclusionsOf(envs)));
  }
  findings.sort((a, b) => (a.at < 0 ? Infinity : a.at) - (b.at < 0 ? Infinity : b.at));
  const counts = Object.fromEntries(KINDS.map((k) => [k, 0]));
  for (const f of findings) counts[f.kind]++;
  return { findings, counts };
}

/**
 * The check of a stored or finished turn: its answer, its tool messages ({envelope}) and every tool message of the
 * conversation (`known`, for the citation ids an earlier turn returned). What the page and the telemetry call.
 */
export function checkTurn({ content = "", tools = [], known = tools, rules } = {}) {
  const turnEnvelopes = tools.map((t) => t?.envelope).filter(Boolean);
  const conversationCites = citationIdsOf([...known.map((t) => t?.envelope), ...turnEnvelopes].filter(Boolean));
  return checkAnswer(content, { turnEnvelopes, conversationCites, rules });
}

/** A short piece of the answer around a finding, for the inspector (never sent anywhere). */
export function excerpt(content, f, width = 18) {
  if (!f || !(f.at >= 0)) return "";
  const text = String(content || "");
  const end = f.end ?? f.at + String(f.id || f.term || "").length;
  const a = Math.max(0, f.at - width);
  const z = Math.min(text.length, end + width);
  return `${a > 0 ? "…" : ""}${text.slice(a, z).replace(/\s+/g, " ").trim()}${z < text.length ? "…" : ""}`;
}

function safe(fn, fallback) {
  try { return fn() ?? fallback; } catch { return fallback; }
}
