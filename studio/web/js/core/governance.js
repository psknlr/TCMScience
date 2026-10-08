// Pure readers over call envelopes (CONTRACTS §3): citations, claims with their verdicts, evidence items, the six
// release states, and the badge each verdict gets (DESIGN §6). They read what the kernel decided and never decide
// anything themselves. Field names follow ResearchArtifact.document(), ArtifactVerdict.as_dict(),
// CandidateClaim and EvidenceItem documents.

import { CLAIM_SUPPORT, DESIGNS, FAMILIES, STATES, TIERS, claimKindInfo, codeInfo, qualityLabel, tierOf } from "./glossary.js";
import { lang as currentLang, t } from "./i18n.js";

export const RELEASE_STATES = ["schema_valid", "evidence_verified", "outputs_verified", "execution_declared", "execution_attested", "release_authorized"];

const gov = (env) => (env && typeof env === "object" && env.governance && typeof env.governance === "object" ? env.governance : {});
const artifactOf = (env) => gov(env).artifact || env?.result?.artifact || null;
const verdictOf = (env) => gov(env).verdict || env?.result?.verdict || artifactOf(env)?.validation || null;
const L = (rec, l) => (rec ? rec[l || currentLang()] ?? rec.en : "");

// ------------------------------------------------------------------------------------------------- families

/**
 * The evidence family of a tier (0–7, enum name, document() tier id) or a study design of either vocabulary:
 * "tradition" | "bench" | "clinical" | "predicted" | null.
 */
export function familyOfTier(tier) {
  const d = typeof tier === "string" ? DESIGNS[tier.trim().toLowerCase()] : null;
  if (d?.predicted) return "predicted";
  const rec = tierOf(tier);
  return rec ? rec.family : null;
}

/** Is this evidence (an item, a design or a tier) a prediction rather than a measurement? */
export function isPredicted(x) {
  if (!x) return false;
  if (typeof x === "object") return isPredicted(x.design) || x.tier === "computational_prediction" || x.tier_rank === 0;
  return familyOfTier(x) === "predicted";
}

// ------------------------------------------------------------------------------------------------- citations

const ID_URL = {
  pmid: (v) => `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(v)}/`,
  pmcid: (v) => `https://www.ncbi.nlm.nih.gov/pmc/articles/${encodeURIComponent(v)}/`,
  doi: (v) => `https://doi.org/${encodeURI(v)}`,
  nct: (v) => `https://clinicaltrials.gov/study/${encodeURIComponent(v)}`,
  isrctn: (v) => `https://www.isrctn.com/${encodeURIComponent(v.startsWith("ISRCTN") ? v : `ISRCTN${v}`)}`,
  chictr: (v) => `https://www.chictr.org.cn/searchprojEN.html?regno=${encodeURIComponent(v)}`,
};

/** A link for an identifier, when the identifier type has a public resolver. Only http(s) ever. */
export function identifierUrl(type, value) {
  const v = String(value || "").trim();
  const make = ID_URL[String(type || "").toLowerCase()];
  return v && make ? make(v) : "";
}

/**
 * The citations of an envelope: envelope.citations when the runtime gave them, otherwise one per evidence item
 * (E1, E2 … in evidence order). Each is {id, kind, label, url, evidence_ref, family?, evidence?}.
 */
export function citationsOf(envelope) {
  const evidence = evidenceOf(envelope);
  const byRef = new Map(evidence.map((e) => [e.id, e]));
  const given = Array.isArray(envelope?.citations) ? envelope.citations : [];
  const out = [];
  const seen = new Set();
  const push = (c) => {
    if (!c || !c.id || seen.has(c.id)) return;
    seen.add(c.id);
    const ev = c.evidence_ref ? byRef.get(c.evidence_ref) : null;
    const url = safeUrl(c.url) || (ev ? ev.url : "");
    out.push({
      id: String(c.id), kind: c.kind || (ev ? kindOfIdentifier(ev.identifier_type) : "source"), label: c.label || ev?.citation || c.id,
      url, evidence_ref: c.evidence_ref || null, family: ev?.family || null, predicted: ev?.predicted || false,
      quote_verified: ev ? ev.quote_verified : null,
    });
  };
  for (const c of given) push(c);
  if (!given.length) evidence.forEach((e, i) => push({ id: `E${i + 1}`, kind: kindOfIdentifier(e.identifier_type), label: e.citation || e.id, url: e.url, evidence_ref: e.id }));
  return out;
}

function kindOfIdentifier(type) {
  const t0 = String(type || "").toLowerCase();
  if (["pmid", "pmcid"].includes(t0)) return "pmid";
  if (t0 === "doi") return "doi";
  if (["nct", "chictr", "isrctn"].includes(t0)) return "nct";
  if (t0 === "classical_passage") return "classical";
  return "source";
}

export function safeUrl(u) {
  if (typeof u !== "string" || !u.trim()) return "";
  try {
    const url = new URL(u.trim());
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
  } catch {
    return "";
  }
}

// ------------------------------------------------------------------------------------------------- evidence

/** Evidence items as views: {id, family, design, tier, tier_rank, predicted, citation, quote, quote_verified, quality, source, …}. */
export function evidenceOf(envelope) {
  const g = gov(envelope);
  const art = artifactOf(envelope);
  const items = Array.isArray(g.evidence) && g.evidence.length ? g.evidence : Array.isArray(art?.evidence) ? art.evidence : [];
  const sources = new Map((art?.sources || []).map((s) => [s.id, s]));
  return items.filter((e) => e && typeof e === "object").map((e) => {
    const tierRec = tierOf(e.tier ?? e.tier_rank ?? e.design);
    const card = e.source_card_id ? sources.get(e.source_card_id) : null;
    const predicted = isPredicted(e);
    return {
      id: e.id,
      family: predicted ? "predicted" : tierRec?.family || null,
      design: e.design || null,
      design_label: DESIGNS[e.design] ? L(DESIGNS[e.design]) : e.design || "",
      tier: tierRec?.id || e.tier || null,
      tier_rank: tierRec ? tierRec.rank : null,
      tier_label: tierRec ? L(tierRec) : "",
      predicted,
      citation: e.citation || "",
      title: e.title || "",
      identifier: e.identifier || "",
      identifier_type: e.identifier_type || "",
      url: identifierUrl(e.identifier_type, e.identifier),
      quote: e.quote || "",
      quote_verified: Boolean(e.quote_verified),
      quote_located: typeof e.quote_offset === "number" ? e.quote_offset >= 0 : null,
      content_hash: e.content_hash || "",
      quality: qualityOf(e.quality),
      retracted: e.retracted || "unverified",
      subject: e.subject || "", population: e.population || "", condition: e.condition || "", comparator: e.comparator || "",
      outcome: e.outcome || "", effect: e.effect || "", sample_size: e.sample_size || 0, year: e.year || 0,
      caveats: Array.isArray(e.caveats) ? e.caveats : [],
      usable: e.usable ?? null,
      conflicts_with: e.conflicts_with || [],
      source: card ? sourceView(card) : e.source_card_id ? { id: e.source_card_id, missing: true } : null,
    };
  });
}

/** The four quality dimensions, each {id, zh, en, assessed}; never a total. */
export function qualityOf(q) {
  if (!q || typeof q !== "object") return null;
  const out = {};
  for (const dim of ["risk_of_bias", "directness", "precision", "consistency"]) {
    out[dim] = qualityLabel(dim, q[dim] ?? "not_assessed") || qualityLabel(dim, "not_assessed");
  }
  out.rationale = q.rationale || {};
  out.assessed_by = q.assessed_by || "";
  out.assessment_tool = q.assessment_tool || "";
  return out;
}

export function sourceView(s) {
  return {
    id: s.id, name: s.name || s.id, kind: s.kind || "", licence: s.license_spdx || "", licence_note: s.license_note || "",
    licensed: s.licensed ?? Boolean(s.license_spdx), pinned: s.pinned ?? Boolean(s.snapshot_hash),
    snapshot_hash: s.snapshot_hash || "", snapshot_at: s.snapshot_at || "", access_method: s.access_method || "",
    offline_capable: Boolean(s.offline_capable), known_limits: s.known_limits || [], citation: s.citation || "",
    home_url: safeUrl(s.home_url), version: s.version || "",
  };
}

// ------------------------------------------------------------------------------------------------- claims

/**
 * Claims joined with their verdicts: {id, kind, kind_label, text, allowed (true|false|null when unchecked), codes,
 * reasons, weakest_tier, family, caveats, extrapolations, declared_extrapolations, falsified_by, needs_declaration,
 * prediction_as_fact, supports, confidence, confidence_basis, licensed_by, clinical}.
 */
export function claimsOf(envelope) {
  const g = gov(envelope);
  const art = artifactOf(envelope);
  const verdict = verdictOf(envelope);
  const claims = Array.isArray(g.claims) && g.claims.length ? g.claims : Array.isArray(art?.claims) ? art.claims : [];
  const verdicts = new Map((verdict?.claim_verdicts || []).map((v) => [v.claim_id, v]));
  return claims.filter((c) => c && typeof c === "object").map((c) => {
    const v = c.verdict || c.claim_verdict || verdicts.get(c.id) || null;
    const kind = c.claim_kind || c.kind || "";
    const info = claimKindInfo(kind);
    const weakest = v?.weakest_tier ?? null;
    const reasons = Array.isArray(v?.reasons) ? v.reasons.map((r) => (typeof r === "string" ? { code: r, detail: "" } : r)) : [];
    const codes = [...new Set([...(v?.codes || []), ...reasons.map((r) => r.code)].filter(Boolean))];
    const declared = c.declared_extrapolations && typeof c.declared_extrapolations === "object" ? c.declared_extrapolations : {};
    return {
      id: c.id,
      kind: info?.canonical || kind,
      kind_label: info ? L(info) : kind,
      text: c.text || "",
      allowed: v ? Boolean(v.allowed) : null,
      codes,
      reasons,
      weakest_tier: weakest,
      family: weakest ? familyOfTier(weakest) : null,
      caveats: Array.isArray(v?.caveats) ? v.caveats : [],
      extrapolations: Array.isArray(v?.unvalidated_extrapolations) ? v.unvalidated_extrapolations : [],
      declared_extrapolations: declared,
      falsified_by: c.falsified_by || "",
      needs_declaration: Boolean(v?.needs_declaration),
      prediction_as_fact: Boolean(v?.prediction_as_fact),
      supports: Array.isArray(c.supports) ? c.supports : [],
      confidence: typeof c.confidence === "number" ? c.confidence : null,
      confidence_basis: c.confidence_basis || "",
      licensed_by: info ? info.licensedBy : [],
      clinical: info ? info.clinical : false,
      subject: c.subject || "", object: c.object || "",
    };
  });
}

/** Kernel codes that refuse a claim because cited evidence cannot license its kind (by set: CLM005; a clinical claim
 * on predictions only: CLM004). */
export const LICENSING_REFUSALS = new Set(["CLM004", "CLM005"]);

/**
 * The licensing ladder for one claim kind (DESIGN §6.4): eight cells, tiers 0–7, which license the kind, which the claim
 * cites, and where its weakest evidence sits; whether its evidence licenses the kind; the caption.
 *
 * The kernel licenses by set: every cited (usable) item must license the kind, so the weakest tier alone does not
 * decide (a classical text licenses traditional use, an observational study cited beside it does not). Pass what is
 * known: `codes` (the claim's verdict codes: CLM005/CLM004 mean not licensed, whatever the weakest tier), `cited` (the
 * tiers of the cited usable evidence), or `evidence` (evidenceOf views) with a claim view as the first argument:
 * licensingLadder(claim, null, lang, {evidence}). With only a kind and a weakest tier, the weakest tier decides.
 */
export function licensingLadder(claimKind, weakestTier, l = currentLang(), { codes, cited, evidence } = {}) {
  let kind = claimKind;
  let weakestIn = weakestTier;
  let codeList = codes;
  let citedIn = cited;
  if (claimKind && typeof claimKind === "object") {
    const c = claimKind;
    kind = c.kind;
    weakestIn = weakestTier ?? c.weakest_tier;
    codeList = codes ?? c.codes;
    if (!citedIn && Array.isArray(evidence) && Array.isArray(c.supports)) {
      const byId = new Map(evidence.map((e) => [e.id, e]));
      citedIn = c.supports.map((id) => byId.get(id)).filter((e) => e && e.usable !== false).map((e) => e.tier_rank ?? e.tier ?? e.design);
    }
  }
  const info = claimKindInfo(kind);
  const licensed = new Set(info ? CLAIM_SUPPORT[info.canonical] || [] : []);
  const citedTiers = (Array.isArray(citedIn) ? citedIn : []).map((x) => tierOf(x)).filter(Boolean);
  const citedRanks = new Set(citedTiers.map((r) => r.rank));
  const weakest = tierOf(weakestIn) || (citedTiers.length ? TIERS[Math.min(...citedRanks)] : null);
  const unlicensed = TIERS.filter((tier) => citedRanks.has(tier.rank) && !licensed.has(tier.rank));
  const refused = (Array.isArray(codeList) ? codeList : []).some((c) => LICENSING_REFUSALS.has(String(c).trim().toUpperCase()));
  const cells = TIERS.map((tier) => ({
    rank: tier.rank, id: tier.id, label: tier[l] ?? tier.en, family: tier.family, licenses: licensed.has(tier.rank),
    weakest: weakest ? weakest.rank === tier.rank : false, cited: citedRanks.has(tier.rank),
  }));
  let supports = null;
  if (refused) supports = false;
  else if (citedTiers.length) supports = unlicensed.length === 0;
  else if (weakest) supports = licensed.has(weakest.rank);
  const needs = TIERS.filter((tier) => licensed.has(tier.rank)).map((tier) => tier[l] ?? tier.en).join(t("core.ladder.or", null, l));
  let caption = "";
  if (info && weakest) {
    const vars = { kind: info[l] ?? info.en, needs, weakest: weakest[l] ?? weakest.en };
    const weakestLicensed = licensed.has(weakest.rank);
    if (supports === false && unlicensed.length && !(unlicensed.length === 1 && unlicensed[0].rank === weakest.rank)) {
      // the weakest link is not what fails: name the cited tiers that do not license the kind
      caption = t("core.ladder.caption_unlicensed", { ...vars, unlicensed: unlicensed.map((tier) => tier[l] ?? tier.en).join(t("core.ladder.and", null, l)) }, l);
    } else if (supports === false && weakestLicensed) {
      // the kernel refused on licensing, and which cited item failed is not known here
      caption = t("core.ladder.caption_refused", vars, l);
    } else {
      caption = t("core.ladder.caption", { ...vars, result: supports ? t("core.ladder.supports", null, l) : t("core.ladder.does_not", null, l) }, l);
    }
  }
  return { kind: info?.canonical || kind, cells, supports, caption, unlicensed: unlicensed.map((tier) => tier.id) };
}

// ------------------------------------------------------------------------------------------------- release

/**
 * The release panel (DESIGN §6.3): the six states in order, each with the reason it is unmet, whether release is
 * authorized, and the header line. null when the envelope carries no artifact verdict.
 */
export function releaseOf(envelope) {
  const verdict = verdictOf(envelope);
  if (!verdict || typeof verdict !== "object" || !verdict.states) return null;
  const art = artifactOf(envelope);
  const states = verdict.states || {};
  const unverified = Array.isArray(verdict.unverified) ? verdict.unverified : [];
  const violations = Array.isArray(verdict.violations) ? verdict.violations : [];
  const reasonsFor = (id) => {
    const out = [];
    for (const u of unverified) if (stateOfUnverified(u) === id) out.push(u);
    for (const v of violations) if (stateOfCode(v.code) === id) out.push(`${v.code}: ${v.detail}`);
    return out;
  };
  const list = RELEASE_STATES.map((id) => {
    const ok = Boolean(states[id]);
    let reasons = ok ? [] : reasonsFor(id);
    let unmet;
    if (!ok && id === "release_authorized") {
      // what keeps release back, in unmet wording (「输出未核验」), never the states' positive labels; ids in `unmet`
      unmet = RELEASE_STATES.filter((s) => s !== "release_authorized" && s !== "execution_declared" && !states[s]);
      reasons = unmet.map((s) => t(`core.release.unmet.${s}`));
    }
    return { id, ok, label: L(STATES.release[id]), reason: reasons.join("; "), reasons, ...(unmet ? { unmet } : {}) };
  });
  const authorized = Boolean(states.release_authorized);
  const passed = list.filter((s) => s.ok).length;
  const summary = t("core.release.header", {
    passed, total: list.length, verdict: authorized ? t("core.release.authorized") : t("core.release.not_authorized"),
  });
  return {
    states: list,
    authorized,
    publishable: Boolean(verdict.publishable),
    passed,
    total: list.length,
    summary,
    badge: verdictBadge({ states, publishable: verdict.publishable, authorized }),
    status: art?.status || null,
    codes: [...new Set(verdict.codes || [])],
    violations: violations.map((v) => ({ ...v, explanation: L(codeInfo(v.code)) })),
    warnings: (verdict.warnings || []).map((w) => ({ ...w, explanation: L(codeInfo(w.code)) })),
    unverified,
    fixable_by_declaration: Boolean(verdict.fixable_by_declaration),
    composite_version: art?.composite_version || null,
    composite_version_string: art?.composite_version_string || null,
    outputs: (art?.outputs || gov(envelope).outputs || []).map((o) => ({ path: o.path, sha256: o.sha256 || "", bytes: o.bytes ?? null, media_type: o.media_type || "", description: o.description || "" })),
    limitations: art?.limitations || gov(envelope).limitations || [],
    assumptions: art?.assumptions || [],
    policy_id: art?.policy_id || "",
    // the artifact's own attestation only; the runner's chain head (the receipt's) is not this artifact's proof
    audit_head: art?.audit_head || "",
    chain_head: envelope?.receipt?.audit_head || "",
    digest: art?.digest || "",
  };
}

/** Which release state an `unverified` sentence of validate_artifact explains. */
export function stateOfUnverified(text) {
  const s = String(text || "");
  if (/^output\b/i.test(s)) return "outputs_verified";
  if (/^evidence\b/i.test(s)) return "evidence_verified";
  if (/policy_id|audit_head|attest/i.test(s)) return "execution_attested";
  return "schema_valid";
}

/** Which release state a violation code keeps false. Most codes make the artifact unpublishable (schema_valid). */
export function stateOfCode(code) {
  const c = String(code || "");
  if (c === "ART115") return "evidence_verified";
  if (["ART107", "ART113", "ART114"].includes(c)) return "outputs_verified";
  if (["ART117", "ART118"].includes(c)) return "execution_attested";
  return "schema_valid";
}

/** Refusals of an envelope with their glossary explanation: [{code, message, remedy, explanation, family}]. */
export function refusalsOf(envelope) {
  const g = gov(envelope);
  const out = [];
  const seen = new Set();
  const add = (code, message, remedy) => {
    const key = `${code}|${message}`;
    if (!code || seen.has(key)) return;
    seen.add(key);
    const info = codeInfo(code);
    out.push({ code: info.code, message: message || "", remedy: remedy || (info.remedy ? L(info.remedy) : ""), explanation: L(info), family: info.family });
  };
  for (const r of g.refusals || []) add(r.code, r.message, r.remedy);
  if (!out.length) {
    for (const v of verdictOf(envelope)?.violations || []) add(v.code, v.detail, "");
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- badges

/**
 * The badge for a verdict (DESIGN §6.2): {tone, icon, label}. Accepts a claim view (claimsOf), a release
 * (releaseOf or an ArtifactVerdict), an envelope, or a state name with its vocabulary: verdictBadge("draft",
 * "artifact"), verdictBadge("accepted", "inquiry"), verdictBadge("pending_manual", "goal"), verdictBadge("refused",
 * "call"). Colour is never the only signal: every badge has an icon and words.
 */
export function verdictBadge(x, vocabulary, l = currentLang()) {
  const mk = (tone, icon, rec) => ({ tone, icon, label: typeof rec === "string" ? rec : L(rec, l) });
  if (x && typeof x === "object") {
    if ("allowed" in x && ("codes" in x || "caveats" in x || "kind" in x)) {
      if (x.allowed === null) return mk("neutral", "○", STATES.claim.unchecked);
      if (x.prediction_as_fact) return mk("prediction", "✕", STATES.claim.prediction_as_fact);
      if (!x.allowed && x.needs_declaration) return mk("declaration", "✎", STATES.claim.needs_declaration);
      if (!x.allowed) return mk("refused", "✕", STATES.claim.refused);
      if ((x.caveats?.length || 0) + (x.extrapolations?.length || 0) > 0) return mk("caveat", "!", STATES.claim.allowed_with_caveats);
      return mk("allowed", "✓", STATES.claim.allowed);
    }
    if (x.governance || x.status && x.receipt) {
      const rel = releaseOf(x);
      if (x.status === "refused") return mk("refused", "✕", STATES.call.refused);
      if (rel) return rel.badge;
      return verdictBadge(x.status, "call", l);
    }
    if (x.states || "authorized" in x) {
      const authorized = Boolean(x.authorized ?? x.states?.release_authorized ?? x.release_authorized);
      const publishable = Boolean(x.publishable ?? x.states?.schema_valid);
      if (authorized) return mk("released", "✓", STATES.release_summary.released);
      if (publishable) return mk("consistent", "◐", STATES.release_summary.consistent);
      return mk("refused", "✕", STATES.release_summary.refused);
    }
  }
  const id = String(x ?? "");
  const vocab = vocabulary || guessVocabulary(id);
  const TONES = {
    artifact: { draft: ["draft", "○"], experimental: ["draft", "◇"], validated: ["released", "✓"], refused: ["refused", "✕"] },
    inquiry: { accepted: ["jade", "✓"], provisional: ["navy", "◐"], undetermined: ["slate", "○"], needs_hypotheses: ["ochre", "✎"] },
    goal: { verified: ["jade", "✓"], pending_manual: ["ochre", "◐"], unverified: ["slate", "○"] },
    call: {
      succeeded: ["neutral", "✓"], failed: ["warning", "!"], refused: ["refused", "✕"], needs_approval: ["ochre", "?"],
      job_submitted: ["navy", "◷"], cancelled: ["slate", "■"], running: ["navy", "…"],
    },
    job: { queued: ["slate", "◷"], running: ["navy", "…"], succeeded: ["jade", "✓"], failed: ["warning", "!"], cancelled: ["slate", "■"] },
  };
  const row = TONES[vocab]?.[id];
  const rec = STATES[vocab]?.[id];
  if (row && rec) return mk(row[0], row[1], rec);
  return { tone: "neutral", icon: "○", label: id };
}

function guessVocabulary(id) {
  for (const v of ["inquiry", "goal", "artifact", "call", "job"]) if (STATES[v]?.[id]) return v;
  return "call";
}

/** "sha256:0da551c0…626d" (first 8, last 4), as DESIGN §6.5 shows hashes. */
export function shortHash(hash, { prefix = true } = {}) {
  const h = String(hash || "").trim().toLowerCase();
  if (!h) return "";
  const body = h.length > 14 ? `${h.slice(0, 8)}…${h.slice(-4)}` : h;
  return prefix ? `sha256:${body}` : body;
}

/** The four axes of a composite version, labelled; "unversioned" stays literal (the UI shows 未版本化). */
export function compositeVersionOf(envelope) {
  const art = artifactOf(envelope);
  const cv = art?.composite_version;
  if (!cv || typeof cv !== "object") return null;
  return ["runtime", "skill", "source", "benchmark"].map((axis) => ({ axis, value: cv[axis] ?? "", unversioned: (cv[axis] ?? "") === "unversioned" || !cv[axis] }));
}

/** The family record (zh/en names) for a family id. */
export function familyInfo(id) {
  return FAMILIES[id] || null;
}
