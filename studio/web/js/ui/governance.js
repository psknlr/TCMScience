// Governance components (DESIGN §3.4, §6): hash badges, evidence-family chips, verdict badges, reason-code chips,
// the licensing ladder, claim cards, the release checklist, artifact cards, composite versions, quality pills,
// evidence items and the evidence table, source cards, and citation chips with their hover cards.
// They render what the kernel decided (core/governance.js reads it from the envelope) and never decide anything.

import {
  claimsOf, compositeVersionOf, evidenceOf, familyOfTier, licensingLadder, refusalsOf, releaseOf, shortHash, verdictBadge,
} from "../core/governance.js";
import { tierOf } from "../core/glossary.js";
import { glossary, lang, t } from "../core/i18n.js";
import { copyText, formatBytes, formatDateTime, h, isForeign, kernelText } from "./dom.js";
import { icon } from "./icons.js";
import { attachHoverCard, toast } from "./overlay.js";
import { chip, disclosure, keyValue } from "./primitives.js";

// ------------------------------------------------------------------------------------------------- hashes

/** sha256:0da551c0…626d in mono; click copies the full value (DESIGN §6.5). */
export function hashBadge(hash, { prefix = "sha256", label, compact = false } = {}) {
  const full = String(hash || "").trim();
  if (!full) return h("span.hash.hash--empty", t("ui.hash.none"));
  const short = shortHash(full, { prefix: false });
  const btn = h("button", {
    type: "button", class: ["hash", compact && "hash--compact"], title: full,
    "aria-label": t("ui.hash.aria", { visible: `${label ? `${label} ` : ""}${prefix ? `${prefix}:` : ""}${short}`, value: full }),
    onClick: async (e) => {
      e.stopPropagation();
      const ok = await copyText(full);
      toast(ok ? t("ui.hash.copied") : t("ui.toast.copy_failed"), { tone: ok ? "ok" : "warn" });
    },
  },
  label ? h("span.hash__label", label) : null,
  prefix ? h("span.hash__prefix", `${prefix}:`) : null,
  h("span.hash__value", short),
  icon("copy", { size: 12, className: "hash__copy" }));
  return btn;
}

// ------------------------------------------------------------------------------------------------- families

const FAMILY_ICON = { tradition: "scroll", bench: "flask", clinical: "stethoscope", predicted: "hexagon" };

/** 「临床 · 随机对照试验」, 「预测 · 分子对接」: family colour + icon + words; predicted is dashed (DESIGN §6.1). */
export function familyChip(family, { design, tier, title, size = "md" } = {}) {
  if (!family) return chip({ label: glossary.design(design) || glossary.evidenceTier(tier) || t("ui.family.unknown"), tone: "neutral" });
  const short = glossary.familyShort(family);
  const specific = design ? glossary.design(design) : tier !== undefined && tier !== null ? glossary.evidenceTier(tier) : "";
  // 「临床 · 随机对照试验」; when the design already names the family (经典文献), the design alone
  const label = !specific || specific === short ? short : specific.startsWith(short) ? specific : `${short} · ${specific}`;
  return h("span", {
    class: ["fam", `fam--${family}`, `fam--${size}`], "data-tip": title || (family === "predicted" ? t("ui.family.predicted_tip") : glossary.family(family)),
  }, icon(FAMILY_ICON[family] || "circle", { size: 14, dashed: family === "predicted" }), h("span.fam__label", label));
}

// ------------------------------------------------------------------------------------------------- verdicts

const GLYPH_ICON = { "✓": "check", "◐": "circleHalf", "!": "alert", "✕": "x", "✎": "edit", "○": "circle", "◷": "clock", "■": "stop", "…": "loader", "?": "help", "◇": "squareDashed" };

/** A pill with an icon and words (colour never alone). badge = verdictBadge(...) → {tone, icon, label}. */
export function verdictPill(badge, { size = "md", title } = {}) {
  const b = badge || { tone: "neutral", icon: "○", label: "" };
  return h("span", { class: ["verdict", `verdict--${b.tone}`, `verdict--${size}`], "data-tip": title || null },
    icon(GLYPH_ICON[b.icon] || "circle", { size: size === "sm" ? 12 : 14, className: b.icon === "…" ? "spin" : "" }),
    h("span.verdict__label", b.label));
}

/** A reason / violation code as a mono chip; its explanation is the tooltip and the accessible description. */
export function codeChip(code, { explanation } = {}) {
  const text = explanation || glossary.code(code) || "";
  return h("span.code-chip", { "data-tip": text ? `${code}: ${text}` : code, tabindex: "0", role: "note", "aria-label": text ? `${code}: ${text}` : code }, code);
}

/** A list of refusals/violations: code chip + plain explanation + the remedy when the kernel names one. */
export function reasonList(reasons, { className = "", tone = "refusal", details = true } = {}) {
  if (!reasons?.length) return null;
  reasons = reasons.map((r) => ({ ...r, explanation: explainCode(r.code, r.explanation) }));
  let list = reasons;
  if (!details) {
    // one row per code: the explanation says what kind of problem it is; the details are in the inspector
    const seen = new Map();
    for (const r of reasons) if (!seen.has(r.code)) seen.set(r.code, r);
    list = [...seen.values()];
  }
  return h("ul", { class: ["reasons", `reasons--${tone}`, className], role: "list" }, list.map((r) => h("li.reasons__item",
    codeChip(r.code, { explanation: r.explanation }),
    h("div.reasons__text",
      h("p.reasons__expl", r.explanation || glossary.code(r.code) || r.message || ""),
      details && r.message && r.message !== r.explanation ? kernelText("p", r.message, { class: "reasons__detail" }) : null,
      r.remedy ? h("p.reasons__remedy", h("span.reasons__remedy-k", t("ui.claim.remedy")), kernelText("span", r.remedy)) : null))));
}

/** A code's explanation: the glossary's, or the studio's own words for codes the glossary does not know (UNPINNED). */
export function explainCode(code, explanation = "") {
  const known = explanation && explanation !== code ? explanation : glossary.code(code);
  if (known && known !== code) return known;
  const key = `ui.code.${String(code || "").toUpperCase()}`;
  const own = t(key);
  return own !== key ? own : (known || "");
}

/** A block title for kernel free text, with a muted 「英文原文」 tag when the text is not in the interface language. */
function kernelTitle(tag, title, texts) {
  return h(tag, title, isForeign(texts) ? h("span.kernel-tag", { "data-tip": t("ui.kernel.original_tip") }, t("ui.kernel.original")) : null);
}

// ------------------------------------------------------------------------------------------------- licensing ladder

/**
 * Eight cells, tiers 0–7: filled in the family colour where the tier licenses the claim kind, hollow otherwise; a caret
 * marks the claim's weakest evidence. The caption says the result in words with ✓/✕ (DESIGN §6.4).
 */
export function licensingLadderView(kind, weakestTier, { caption = true, codes = [], claim = null, evidence = null } = {}) {
  // with the claim (and its evidence) the ladder follows the kernel: licensing is by the whole cited set and its codes
  const ladder = claim ? licensingLadder(claim, null, lang(), evidence ? { evidence } : {}) : licensingLadder(kind, weakestTier, lang());
  const cells = h("div.ladder__cells", ladder.cells.map((c) => h("span", {
    class: ["ladder__cell", `fam--${c.family}`, c.licenses && "is-licensed", c.weakest && "is-weakest", c.cited && "is-cited", c.family === "predicted" && "is-predicted"],
    "data-tip": `${c.rank} · ${c.label}${c.licenses ? ` — ${t("ui.ladder.licenses")}` : ""}${c.weakest ? ` — ${t("ui.ladder.weakest")}` : ""}`,
  }, c.weakest ? h("span.ladder__caret", { "aria-hidden": "true" }) : null)));
  const ticks = h("div.ladder__ticks", { "aria-hidden": "true" }, ladder.cells.map((c) => h("span", String(c.rank))));
  const resultIcon = ladder.supports === null ? null : icon(ladder.supports ? "check" : "x", { size: 14, className: ladder.supports ? "ladder__ok" : "ladder__no" });
  const codeText = !ladder.supports && codes.length ? `（${codes.join("、")}）` : "";
  const text = ladder.caption ? `${ladder.caption}${codeText}` : t("ui.ladder.no_weakest", { kind: glossary.claimKind(kind) });
  return h("figure", { class: ["ladder", ladder.supports === false && "is-unlicensed", ladder.supports && "is-licensed"] },
    h("div.ladder__track", { role: "img", "aria-label": text, tabindex: "0" }, cells, ticks),
    caption ? h("figcaption.ladder__caption", resultIcon, h("span", text)) : null);
}

// ------------------------------------------------------------------------------------------------- claims

/**
 * A claim card (DESIGN §3.4): kind chip and verdict, the text, the evidence it rests on, the codes, the weakest tier
 * on the ladder, caveats, unvalidated extrapolations, what would falsify it, and confidence with its basis.
 * claim = a claimsOf() view. opts: {evidence: EvidenceView[], citations, compact, onEvidence(id), selected}.
 */
export function claimCard(claim, { evidence = [], compact = false, onEvidence, onOpen, selected = false, id } = {}) {
  const badge = verdictBadge(claim);
  const evById = new Map(evidence.map((e, i) => [e.id, { ...e, n: i + 1 }]));
  const refused = claim.allowed === false;
  const supports = claim.supports.map((ref) => {
    const ev = evById.get(ref);
    const fam = ev?.family || null;
    return h("button", {
      type: "button", class: ["cite", "cite--inline", fam && `fam--${fam}`, ev?.predicted && "is-predicted"],
      "data-tip": ev ? `${ev.design_label || ev.tier_label} · ${ev.citation || ev.id}` : ref,
      onClick: (e) => { e.stopPropagation(); onEvidence?.(ref); },
    }, ev ? `E${ev.n}` : ref);
  });
  const reasons = claim.reasons.map((r) => ({ code: r.code, message: r.detail || "", explanation: glossary.code(r.code), remedy: glossary.codeRemedy(r.code) || "" }));
  const head = h("header.claim__head",
    h("span.claim__kind", { "data-tip": t("ui.claim.kind_tip") }, claim.kind_label || claim.kind),
    verdictPill(badge),
    claim.codes.length ? h("span.claim__codes", claim.codes.map((c) => codeChip(c))) : null,
    onOpen ? h("button.claim__open.icon-btn.icon-btn--ghost.icon-btn--sm", { type: "button", "aria-label": t("ui.action.open_inspector"), "data-tip": t("ui.action.open_inspector"), onClick: (e) => { e.stopPropagation(); onOpen(claim); } }, icon("inspector", { size: 14 })) : null);
  const body = [
    kernelText("p", claim.text, { class: "claim__text" }),
    supports.length ? h("div.claim__supports", h("span.claim__k", t("ui.claim.supports")), supports) : h("p.claim__nosupport", t("ui.claim.no_support")),
  ];
  if (refused || !compact) body.push(reasonList(reasons, { details: !compact }));
  if (!compact) {
    body.push(licensingLadderView(claim.kind, claim.weakest_tier, { codes: claim.codes.filter((c) => LICENSING_CODES.has(c)), claim, evidence }));
    if (claim.weakest_tier) {
      // the cited item at that tier names its design, as the Evidence tab does (「传统 · 专家共识」, not the tier's 「名医经验/专家共识」)
      const ev = weakestEvidence(claim, evById);
      body.push(h("p.claim__weakest", h("span.claim__k", t("ui.claim.weakest")),
        ev ? familyChip(ev.family, { design: ev.design, tier: ev.tier }) : familyChip(familyOfTier(claim.weakest_tier), { tier: claim.weakest_tier })));
    }
  }
  const caveats = claim.caveats || [];
  const extrap = claim.extrapolations || [];
  if (!compact) {
    if (caveats.length) body.push(h("div.claim__block", kernelTitle("p.claim__k", t("ui.claim.caveats", { n: caveats.length }), caveats), h("ul.claim__list", caveats.map((c) => kernelText("li", c)))));
    if (extrap.length) {
      body.push(h("div.claim__block", h("p.claim__k", t("ui.claim.extrapolations", { n: extrap.length })), h("ul.claim__list.claim__list--extrap", extrap.map((x) => {
        const declared = claim.declared_extrapolations?.[x];
        return h("li", h("code.claim__extrap", x), declared ? kernelText("span", ` — ${declared}`, { class: "claim__declared" }) : null);
      }))));
    }
    body.push(h("p.claim__falsify", h("span.claim__k", t("ui.claim.falsified_by")), claim.falsified_by ? kernelText("span", claim.falsified_by) : h("span.muted", t("ui.claim.falsified_by_none"))));
    // CandidateClaim defaults confidence to 0.0; a zero with no basis is "not stated", never a number on its own
    if (claim.confidence !== null && claim.confidence !== undefined && (claim.confidence > 0 || claim.confidence_basis)) {
      body.push(h("p.claim__confidence", h("span.claim__k", t("ui.claim.confidence")),
        h("span.num", claim.confidence.toFixed(2)), " · ",
        claim.confidence_basis ? h("span", t("ui.claim.basis_k"), kernelText("span", claim.confidence_basis)) : h("span.muted", t("ui.claim.no_basis"))));
    }
  } else {
    const notes = [];
    if (claim.weakest_tier) notes.push(h("span", t("ui.claim.weakest_short", { tier: weakestEvidence(claim, evById)?.design_label || glossary.evidenceTier(claim.weakest_tier) })));
    if (caveats.length) notes.push(h("span", t("ui.claim.caveats_short", { n: caveats.length })));
    if (extrap.length) notes.push(h("span", t("ui.claim.extrap_short", { n: extrap.length })));
    if (claim.falsified_by) notes.push(h("span", t("ui.claim.falsify_short_k"), kernelText("span", clipText(claim.falsified_by, 48))));
    if (notes.length) body.push(h("p.claim__notes", notes.flatMap((n, i) => (i ? [h("span.dot-sep", "·"), n] : [n]))));
  }
  // clicking the card is a pointer shortcut; the keyboard path is the 「在检查器中打开」 button in its header
  return h("article", {
    class: ["claim", compact && "claim--compact", refused && "claim--refused", claim.prediction_as_fact && "claim--prediction", selected && "is-selected"],
    "data-claim": claim.id, id: id || null,
    onClick: onOpen ? (e) => { if (!e.target.closest?.("button, a")) onOpen(claim); } : null,
  }, head, body);
}

/** The cited evidence item at the claim's weakest tier (one with a design first), or null. */
function weakestEvidence(claim, evById) {
  const rank = tierOf(claim.weakest_tier)?.rank;
  if (rank === undefined) return null;
  const cited = claim.supports.map((ref) => evById.get(ref)).filter((ev) => ev && ev.tier_rank === rank);
  return cited.find((ev) => ev.design) || null;
}

/** The codes that are about licensing (which evidence can support which claim kind): the ladder cites only these. */
const LICENSING_CODES = new Set(["CLM004", "CLM005", "ART105", "ART106", "EVIDENCE103", "GATE004", "INQ113"]);

function clipText(s, n) {
  const str = String(s || "");
  return str.length > n ? `${str.slice(0, n - 1)}…` : str;
}

// ------------------------------------------------------------------------------------------------- release

/** The six release states as a checklist with reasons for the unmet ones (DESIGN §6.3). release = releaseOf(env). */
export function releaseChecklist(release, { compact = false } = {}) {
  if (!release) return null;
  const header = h("div.release__head",
    verdictPill(release.badge),
    h("span.release__summary", release.summary));
  const rows = release.states.map((s) => h("li", { class: ["release__row", s.ok ? "is-ok" : "is-open"] },
    s.ok ? icon("check", { size: 14, className: "release__icon", label: t("ui.release.met") }) : icon("circle", { size: 14, className: "release__icon", label: t("ui.release.unmet") }),
    h("div.release__text",
      h("p.release__label", s.ok ? s.label : unmetLabel(s), h("code.release__id", s.id)),
      !s.ok ? releaseReasons(s.id === "release_authorized" ? unmetStates(release).map(unmetLabel) : s.reasons || []) : null)));
  const list = h("ol.release__list", { role: "list" }, rows);
  return h("div", { class: ["release", compact && "release--compact"] }, header, compact ? disclosure({ summary: t("ui.release.details"), content: list, className: "release__more" }) : list);
}

/** The words for a release state that is not met: 「执行未证明」, never its positive label 「执行已证明」. */
export function unmetLabel(state) {
  const id = typeof state === "string" ? state : state.id;
  const key = `ui.release.unmet.${id}`;
  const s = t(key);
  return s !== key ? s : `${t("ui.release.unmet")}：${typeof state === "string" ? state : state.label}`;
}

/** The unmet states that keep an artifact from release (execution_declared is informative only). */
function unmetStates(release) {
  return release.states.filter((s) => !s.ok && s.id !== "release_authorized" && s.id !== "execution_declared");
}

function releaseReasons(reasons) {
  if (!reasons.length) return null;
  const shown = reasons.slice(0, 2);
  return h("div.release__reasons",
    shown.map((r) => kernelText("p", r, { class: "release__reason" })),
    reasons.length > shown.length ? h("p.release__reason.release__reason--more", t("ui.release.more", { n: reasons.length - shown.length })) : null);
}

/** Composite version: runtime · Skill · source · benchmark; "unversioned" shown as 未版本化 (DESIGN §6.5). */
export function compositeVersionView(envelopeOrAxes) {
  const axes = Array.isArray(envelopeOrAxes) ? envelopeOrAxes : compositeVersionOf(envelopeOrAxes);
  if (!axes) return null;
  return keyValue(axes.map((a) => [t(`ui.cv.${a.axis}`), a.unversioned
    ? h("span.muted", t("ui.cv.unversioned"))
    : /^[0-9a-f]{40,}$/i.test(a.value) ? hashBadge(a.value, { prefix: "" }) : h("code.kv__code", a.value)]), { className: "kv--cv" });
}

const STATUS_TONE = { draft: "slate", validated: "jade", refused: "vermilion", experimental: "slate" };

/**
 * The audit head shown for an artifact: its own (the kernel attested it), or — labelled as such — the runner's chain
 * head when the artifact carries none, so 「未经审计链证明」 and a hash never read as a contradiction.
 */
export function auditHeadView(envelope, rel) {
  const art = envelope?.governance?.artifact || envelope?.result?.artifact || {};
  if (art.audit_head) return hashBadge(art.audit_head, { prefix: "" });
  const chain = rel?.chain_head || envelope?.receipt?.audit_head || "";
  if (!chain) return h("span.muted", t("ui.artifact.not_attested"));
  return h("span.audit-head", h("span.muted", t("ui.artifact.not_attested")), h("span.audit-head__chain", h("span.muted", t("ui.artifact.chain_head")), hashBadge(chain, { prefix: "" })));
}

/** The candidate marker: a Skill no lockfile pins runs, but its result is never released. */
export function candidateChip(envelope) {
  if (envelope?.receipt?.skill_pinned !== false) return null;
  return chip({ label: t("ui.artifact.candidate"), tone: "ochre", icon: "circleDashed", dashed: true, title: t("ui.artifact.candidate_tip") });
}

/**
 * An artifact card: status, release verdict and summary, outputs with hashes, limitations (always visible),
 * composite version, policy and audit head. compact: the thread's version (DESIGN §4.2 layout).
 */
export function artifactCard(envelope, { compact = false, onOpen, title, selected = false } = {}) {
  const rel = releaseOf(envelope);
  if (!rel) return null;
  const art = envelope?.governance?.artifact || envelope?.result?.artifact || {};
  const name = title || art.skill_id || envelope?.via || envelope?.tool || t("ui.artifact.untitled");
  const version = art.skill_version ? `@${art.skill_version}` : "";
  // the artifact's own status is the workflow state the Skill reports (almost always 草稿); next to the kernel's release
  // verdict it reads as a contradiction, so the compact card shows it only when it says something (refused,
  // experimental), and the full card states it as a labelled row
  const statusText = rel.status ? glossary.verdictState(rel.status, undefined, "artifact") : "";
  const status = compact && rel.status && rel.status !== "draft" && rel.status !== "validated"
    ? chip({ label: statusText, tone: STATUS_TONE[rel.status] || "slate", title: t("ui.artifact.status_tip") }) : null;
  const unreleasedRefusals = refusalsOf(envelope).filter((r) => !rel.violations.some((v) => v.code === r.code) && !claimsOf(envelope).some((c) => (c.codes || []).includes(r.code)));
  const outputs = rel.outputs.map((o) => h("li.artifact__output",
    icon(o.media_type?.includes("json") ? "fileJson" : o.media_type?.startsWith("image/") ? "image" : "fileText", { size: 14 }),
    h("span.artifact__path.mono", o.path),
    o.bytes !== null ? h("span.artifact__bytes.muted.num", formatBytes(o.bytes)) : null,
    hashBadge(o.sha256, { compact: true }),
    rel.states.find((s) => s.id === "outputs_verified")?.ok ? chip({ label: t("ui.artifact.output_verified"), tone: "jade", icon: "check" }) : null));
  const limits = rel.limitations || [];
  const limitations = h("div.artifact__limits",
    kernelTitle("p.artifact__k", [icon("alert", { size: 14 }), t("ui.artifact.limitations", { n: limits.length })], limits),
    limits.length
      ? h("ul.artifact__limit-list", (compact ? limits.slice(0, 2) : limits).map((l) => kernelText("li", l)))
      : h("p.artifact__nolimits", t("ui.artifact.no_limitations")),
    compact && limits.length > 2 ? h("p.artifact__more.muted", t("ui.artifact.more_limits", { n: limits.length - 2 })) : null);
  const head = h("header.artifact__head",
    h("span.artifact__icon", icon("box", { size: 16 })),
    h("div.artifact__title",
      h("p.artifact__name", h("span", name), version ? h("span.artifact__ver.mono", version) : null),
      // the pill beside it carries the verdict: the subtitle is the count alone (no 「· 已准予发布」 tail)
      h("p.artifact__sub", t("ui.release.count", { passed: rel.passed, total: rel.total }))),
    h("div.artifact__badges", status, candidateChip(envelope), verdictPill(rel.badge)),
    onOpen ? h("button.icon-btn.icon-btn--ghost.icon-btn--sm", { type: "button", "aria-label": t("ui.action.open_inspector"), "data-tip": t("ui.action.open_inspector"), onClick: (e) => { e.stopPropagation(); onOpen(envelope); } }, icon("inspector", { size: 14 })) : null);
  const parts = [head];
  // refusals that are neither a claim's nor a failed check (UNPINNED, a refused operation): shown, never hidden
  if (unreleasedRefusals.length) parts.push(h("div.artifact__refusals", reasonList(unreleasedRefusals, { details: !compact || unreleasedRefusals.length < 3 })));
  if (outputs.length) parts.push(h("ul.artifact__outputs", { role: "list" }, outputs));
  parts.push(limitations);
  if (!compact) {
    if (rel.status) parts.push(keyValue([[t("ui.artifact.status_k"), h("span", t("ui.artifact.status_v", { status: statusText }))]], { className: "artifact__status" }));
    parts.push(releaseChecklist(rel));
    if (rel.warnings.length) parts.push(h("div.artifact__warnings", h("p.artifact__k", t("ui.artifact.warnings", { n: rel.warnings.length })), reasonList(rel.warnings.map((w) => ({ code: w.code, message: w.detail, explanation: w.explanation })), { tone: "warning" })));
    if (rel.violations.length) parts.push(h("div.artifact__violations", h("p.artifact__k", t("ui.artifact.violations", { n: rel.violations.length })), reasonList(rel.violations.map((v) => ({ code: v.code, message: v.detail, explanation: v.explanation })))));
    if (rel.assumptions.length) parts.push(h("div.artifact__block", kernelTitle("p.artifact__k", t("ui.artifact.assumptions"), rel.assumptions), h("ul.artifact__limit-list", rel.assumptions.map((a) => kernelText("li", a)))));
    const cv = compositeVersionView(envelope);
    if (cv) parts.push(h("div.artifact__block", h("p.artifact__k", t("ui.cv.title")), cv));
    parts.push(keyValue([
      ["policy_id", rel.policy_id ? h("code.kv__code", rel.policy_id) : h("span.muted", t("ui.artifact.none"))],
      ["audit_head", auditHeadView(envelope, rel)],
    ], { className: "kv--mono-keys" }));
  } else {
    const missing = rel.states.filter((s) => !s.ok && s.id !== "release_authorized" && s.reason);
    if (missing.length && !rel.authorized) parts.push(h("p.artifact__why", h("span.artifact__why-k", t("ui.release.unmet_k")), missing.map((s) => unmetLabel(s)).join(" · ")));
  }
  // the inspector button in the header is the keyboard path; a click on the card is a pointer shortcut
  return h("article", { class: ["artifact", compact && "artifact--compact", rel.authorized && "is-released", selected && "is-selected"], onClick: onOpen ? (e) => { if (!e.target.closest?.("button, a")) onOpen(envelope); } : null }, parts);
}

// ------------------------------------------------------------------------------------------------- evidence

/** Four quality pills, never a total; 未评估 is hollow and neutral (DESIGN §2.3). */
export function qualityPills(quality) {
  if (!quality) return h("p.quality.quality--none.muted", t("ui.quality.none"));
  return h("ul.quality", { role: "list" }, ["risk_of_bias", "directness", "precision", "consistency"].map((dim) => {
    const q = quality[dim];
    const dimLabel = t(`ui.quality.${dim}`);
    const label = q ? (lang() === "zh" ? q.zh : q.en) : t("ui.quality.not_assessed");
    return h("li", { class: ["quality__pill", q && !q.assessed && "is-unassessed"], "data-tip": `${dimLabel}: ${label}` },
      h("span.quality__dim", dimLabel), h("span.quality__val", label));
  }));
}

/** One evidence item card (DESIGN §4.4 tab 2). ev = an evidenceOf() view; n = its citation number. */
export function evidenceItem(ev, { n, selected = false, onSource, id } = {}) {
  const quote = ev.quote ? h("blockquote.evidence__quote",
    h("p", ev.quote),
    h("p", { class: ["evidence__qv", ev.quote_verified ? "is-verified" : "is-unverified"] },
      icon(ev.quote_verified ? "check" : "circleDashed", { size: 12 }),
      ev.quote_verified ? t("ui.evidence.quote_verified") : t("ui.evidence.quote_unverified"))) : null;
  const retr = ev.retracted || "unverified";
  const citation = ev.url
    ? h("a.evidence__cite", { href: ev.url, target: "_blank", rel: "noopener noreferrer" }, ev.citation || ev.identifier, icon("external", { size: 12 }))
    : kernelText("span", ev.citation || ev.identifier || ev.id, { class: "evidence__cite" });
  return h("article", { class: ["evidence", ev.predicted && "is-predicted", selected && "is-selected"], "data-evidence": ev.id, id: id || null, tabindex: "-1" },
    h("header.evidence__head",
      n ? h("span", { class: ["cite", `fam--${ev.family}`, ev.predicted && "is-predicted"] }, `E${n}`) : null,
      familyChip(ev.family, { design: ev.design, tier: ev.tier })),
    h("p.evidence__citation", citation, ev.year ? h("span.muted.num", ` · ${ev.year}`) : null, ev.identifier && ev.identifier_type ? h("span.muted.mono.evidence__id", ` ${ev.identifier_type}:${ev.identifier}`) : null),
    ev.subject || ev.population || ev.outcome ? kernelText("p", [ev.subject, ev.population, ev.outcome && `→ ${ev.outcome}`].filter(Boolean).join(" · "), { class: "evidence__pico muted" }) : null,
    quote,
    qualityPills(ev.quality),
    h("div.evidence__meta",
      chip({ label: glossary.verdictState(retr, undefined, "retraction"), tone: retr === "retracted" ? "vermilion" : retr === "not_retracted" ? "neutral" : "slate", dashed: retr === "unverified" }),
      ev.conflicts_with?.length ? chip({ label: t("ui.evidence.conflicts", { n: ev.conflicts_with.length }), tone: "ochre", icon: "gitCompare", title: ev.conflicts_with.join(", ") }) : null,
      ev.source ? h("button.evidence__source", { type: "button", onClick: () => onSource?.(ev.source), "data-tip": t("ui.evidence.source_tip") }, icon("database", { size: 12 }), ev.source.missing ? `${ev.source.id} (${t("ui.evidence.source_missing")})` : ev.source.name) : null,
      ev.content_hash ? hashBadge(ev.content_hash, { compact: true }) : null));
}

/** Evidence grouped by family, in the order 经典与经验 / 临床前 / 临床 / 计算预测. */
export function evidenceGroups(evs, { selectedId, onSource, idFor } = {}) {
  const order = ["tradition", "bench", "clinical", "predicted", null];
  const groups = order.map((fam) => [fam, evs.map((e, i) => [e, i + 1]).filter(([e]) => (e.family || null) === fam)]).filter(([, list]) => list.length);
  return h("div.evidence-groups", groups.map(([fam, list]) => h("section.evidence-group",
    h("h4.evidence-group__title", fam ? familyChip(fam, { size: "sm" }) : t("ui.family.unknown"), h("span.section__count.num", String(list.length))),
    list.map(([e, n]) => evidenceItem(e, { n, selected: e.id === selectedId, onSource, id: idFor?.(e) })))));
}

/** Elicit-style table: one row per item, quality in four separate columns, no total. */
export function evidenceTable(evs, { selectedId, onSelect } = {}) {
  const cols = [
    ["n", "#"], ["family", t("ui.evidence.col.design")], ["citation", t("ui.evidence.col.citation")], ["quote", t("ui.evidence.col.quote")],
    ["risk_of_bias", t("ui.quality.risk_of_bias")], ["directness", t("ui.quality.directness")], ["precision", t("ui.quality.precision")],
    ["consistency", t("ui.quality.consistency")], ["retracted", t("ui.evidence.col.retraction")], ["hash", t("ui.evidence.col.hash")],
  ];
  const q = (ev, dim) => {
    const v = ev.quality?.[dim];
    const label = v ? (lang() === "zh" ? v.zh : v.en) : t("ui.quality.not_assessed");
    return h("span", { class: ["quality__pill", "quality__pill--table", (!v || !v.assessed) && "is-unassessed"] }, label);
  };
  // the row's action is the E# button (a real button: Enter and Space); clicking elsewhere on the row is a shortcut
  const rows = evs.map((ev, i) => h("tr", { class: [ev.id === selectedId && "is-selected"], "data-evidence": ev.id,
    onClick: (e) => { if (!e.target.closest?.("button, a")) onSelect?.(ev.id); } },
  h("td.num", h("button", { type: "button", class: ["cite", `fam--${ev.family}`, ev.predicted && "is-predicted"], "aria-label": t("ui.evidence.select_row", { id: `E${i + 1}` }), onClick: () => onSelect?.(ev.id) }, `E${i + 1}`)),
  h("td", familyChip(ev.family, { design: ev.design, tier: ev.tier, size: "sm" })),
  kernelText("td", ev.citation || ev.identifier || ev.id, { class: "etable__citation" }),
  h("td", ev.quote ? h("span", { class: ["etable__qv", ev.quote_verified ? "is-verified" : "is-unverified"] }, icon(ev.quote_verified ? "check" : "circleDashed", { size: 12 }), ev.quote_verified ? t("ui.evidence.quote_verified_short") : t("ui.evidence.quote_unverified_short")) : h("span.muted", "—")),
  h("td", q(ev, "risk_of_bias")), h("td", q(ev, "directness")), h("td", q(ev, "precision")), h("td", q(ev, "consistency")),
  h("td", glossary.verdictState(ev.retracted || "unverified", undefined, "retraction")),
  h("td", ev.content_hash ? hashBadge(ev.content_hash, { compact: true, prefix: "" }) : h("span.muted", "—"))));
  return h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": t("ui.evidence.table_label") },
    h("table.etable", h("thead", h("tr", cols.map(([, label]) => h("th", { scope: "col" }, label)))), h("tbody", rows)));
}

/** A source card: licence (未声明许可 when absent), snapshot hash and time, access method, known limits. */
export function sourceCard(s, { selected = false } = {}) {
  if (!s) return null;
  return h("article", { class: ["source", selected && "is-selected"], "data-source": s.id },
    h("header.source__head", icon("database", { size: 16 }), h("div",
      h("p.source__name", s.name), h("p.source__id.mono.muted", s.id + (s.version ? ` · v${s.version}` : "")))),
    keyValue([
      [t("ui.source.licence"), s.licence ? h("span", s.licence, s.licence_note ? h("span.muted", ` · ${s.licence_note}`) : null) : h("span.source__unlicensed", t("ui.source.unlicensed"))],
      [t("ui.source.kind"), s.kind],
      [t("ui.source.access"), s.access_method],
      [t("ui.source.snapshot"), s.snapshot_hash ? h("span.source__snap", hashBadge(s.snapshot_hash, { compact: true, prefix: "" }), s.snapshot_at ? h("time.muted", { datetime: s.snapshot_at, title: formatDateTime(s.snapshot_at) }, ` ${s.snapshot_at.slice(0, 10)}`) : null) : h("span.muted", t("ui.source.unpinned"))],
      [t("ui.source.offline"), s.offline_capable ? t("ui.common.yes") : t("ui.common.no")],
    ]),
    s.known_limits?.length ? h("div.source__limits", kernelTitle("p.artifact__k", t("ui.source.limits"), s.known_limits), h("ul", s.known_limits.map((l) => kernelText("li", l)))) : null);
}

// ------------------------------------------------------------------------------------------------- citations

/**
 * [E1] coloured by evidence family (predicted dashed), with a hover card: design/tier, citation, the quote (引文已核对
 * when verified), source card and content hash. onActivate(citation) selects it in the inspector.
 */
export function citationChip(citation, { evidence, onActivate } = {}) {
  const fam = citation?.family || evidence?.family || null;
  const predicted = Boolean(citation?.predicted || evidence?.predicted);
  const label = citation?.id || "E?";
  const btn = h("button", {
    type: "button", class: ["cite", fam && `fam--${fam}`, predicted && "is-predicted", !citation && "is-unknown"],
    "aria-label": citation ? t("ui.cite.aria", { id: label, label: citation.label || "" }) : t("ui.cite.unknown", { id: label }),
    onClick: (e) => { e.preventDefault(); e.stopPropagation(); onActivate?.(citation); },
  }, label);
  if (citation) attachHoverCard(btn, ({ interactive } = {}) => citationHoverCard(citation, evidence, { interactive }));
  else btn.setAttribute("data-tip", t("ui.cite.unknown", { id: label }));
  return btn;
}

/**
 * The card a citation chip shows. interactive (opened by the pointer): the source link and the copy-hash button; from
 * keyboard focus it is a tooltip, so it holds text only (the same facts are in the inspector, one key away).
 */
export function citationHoverCard(citation, ev, { interactive = true } = {}) {
  const parts = [];
  if (ev) {
    parts.push(h("div.hc__head", familyChip(ev.family, { design: ev.design, tier: ev.tier }), ev.year ? h("span.muted.num", String(ev.year)) : null));
  } else {
    parts.push(h("div.hc__head", chip({ label: citation.kind || "source", tone: "neutral" })));
  }
  const url = citation.url || ev?.url;
  parts.push(h("p.hc__citation", url && interactive ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, citation.label || ev?.citation, " ", icon("external", { size: 12 })) : kernelText("span", citation.label || ev?.citation || citation.id)));
  if (ev?.quote) {
    parts.push(kernelText("blockquote", ev.quote, { class: "hc__quote" }));
    parts.push(h("p", { class: ["evidence__qv", ev.quote_verified ? "is-verified" : "is-unverified"] }, icon(ev.quote_verified ? "check" : "circleDashed", { size: 12 }), ev.quote_verified ? t("ui.evidence.quote_verified") : t("ui.evidence.quote_unverified")));
  }
  if (ev?.predicted) parts.push(h("p.hc__predicted", icon("hexagon", { size: 12, dashed: true }), t("ui.family.predicted_tip")));
  const meta = [];
  if (ev?.source) meta.push(h("span.hc__source", icon("database", { size: 12 }), ev.source.name || ev.source.id));
  if (ev?.content_hash) meta.push(interactive ? hashBadge(ev.content_hash, { compact: true }) : h("span.hash.hash--compact.hash--static", h("span.hash__prefix", "sha256:"), h("span.hash__value", shortHash(ev.content_hash, { prefix: false }))));
  if (meta.length) parts.push(h("div.hc__meta", meta));
  return h("div.hc", parts);
}

// ------------------------------------------------------------------------------------------------- limitations

/**
 * The limits the kernel attached to a result that has no artifact card (clinic drafts, a herb with no safety record,
 * live data, predicted rows): the first two, then 「另有 N 条」 — always visible, in the kernel's words.
 */
export function limitationsBlock(limits, { compact = true, onMore } = {}) {
  const list = (limits || []).filter(Boolean);
  if (!list.length) return null;
  const shown = compact ? list.slice(0, 2) : list;
  return h("div.limits",
    kernelTitle("p.limits__k", [icon("alert", { size: 14 }), t("ui.artifact.limitations", { n: list.length })], list),
    h("ul.limits__list", shown.map((l) => kernelText("li", l))),
    compact && list.length > shown.length
      ? (onMore ? h("button.limits__more", { type: "button", onClick: onMore }, t("ui.artifact.more_limits", { n: list.length - shown.length })) : h("p.limits__more.muted", t("ui.artifact.more_limits", { n: list.length - shown.length })))
      : null);
}

// ------------------------------------------------------------------------------------------------- envelope summaries

/** Governance objects of an envelope, ready to render: {evidence, citations, claims, release, refusals}. */
export function governanceOf(envelope) {
  if (!envelope) return { evidence: [], claims: [], release: null, refusals: [] };
  return { evidence: evidenceOf(envelope), claims: claimsOf(envelope), release: releaseOf(envelope), refusals: refusalsOf(envelope) };
}
