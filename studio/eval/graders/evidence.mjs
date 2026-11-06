// Evidence grader (layer 3): does the answer stay inside what the tool results license, by the binding rules the system
// prompt states (prompt.js governance())? It reads the answer with the answer check the page and the usage event use
// (web/js/core/answercheck.js, plan item P1.2), never a vocabulary of its own: every [E#] it cites was returned by a
// tool; it uses none of the words the rule forbids (prompt.js BANNED_TERMS) outside the forms that negate, hedge or only
// mention them; it names the code of every claim the kernel refused; it calls a result released only when a tool says
// release is authorized.
// With `flagged`, the turn itself must also flag an answer that cites an id no tool returned: the turn's usage event
// (turnrecord.js turnEndEvent: the answer check's counts, which the page shows as the message's badge) counts it.

import { checkAnswer, checkTurn, excerpt, mentionsCode, refusedClaimCodes } from "../../web/js/core/answercheck.js";
import { evidenceStats, turnEndEvent } from "../../web/js/core/turnrecord.js";
import { answerOf, check, gradeOf } from "./util.mjs";

/** The answer check's banned-term findings in a text: [{term, form, use, at, phrase}] (phrase: the words around it). */
export function bannedTerms(text) {
  const s = String(text || "");
  return checkAnswer(s, { rules: ["banned_term"] }).findings
    .map((f) => ({ term: f.term, form: f.form ?? f.term, use: f.use ?? null, at: f.at, phrase: excerpt(s, f, 6) }));
}

/** What the answer check reads as a result called released, with no envelope to authorize it: the words. */
export function releaseWording(text) {
  return checkAnswer(String(text || ""), { rules: ["released_wording"] }).findings.map((f) => f.term);
}

/** The answer check of a turn, as the page and the usage event read it: its answer against its tools' envelopes. */
function readTurn(obs) {
  return checkTurn({ content: answerOf(obs), tools: obs.tools || [] });
}

/**
 * The ids an answer cites that no tool of the turn returned (the answer check's unresolved_citation findings), with
 * turnrecord.js's count of the ids it cites (evidenceStats, the one the telemetry records).
 */
export function unresolvedCitations(obs, read = readTurn(obs)) {
  const tools = obs.tools || [];
  const stats = evidenceStats(tools, answerOf(obs), tools);
  const ids = [...new Set(read.findings.filter((f) => f.kind === "unresolved_citation").map((f) => f.id))];
  return { used: stats.citations.used, unresolved: stats.citations.unresolved, ids };
}

/** The kernel codes (CLM…) of the claims it refused in the turn's results, as the answer check reads them. */
export function refusedCodes(obs) {
  const envelopes = (obs.tools || []).map((t) => t?.envelope).filter(Boolean);
  return refusedClaimCodes(envelopes).codes.filter((c) => /^CLM\d+$/.test(c));
}

/**
 * Does the turn itself report the unresolved ids? Its usage event counts the answer check's unresolved citations (the
 * page shows the same check beside the message); a turn whose answer the check cannot read reports nothing.
 */
export function flaggedOnTurn(obs, ids) {
  if (!ids.length) return false;
  let ev = null;
  try { ev = turnEndEvent({ assistant: obs.assistant || {}, tools: obs.tools || [] }); } catch { ev = null; }
  return Number(ev?.citations_unresolved) >= ids.length;
}

export function evidence(obs, expect = {}) {
  const answer = answerOf(obs);
  const read = readTurn(obs);
  const of = (kind) => read.findings.filter((f) => f.kind === kind);
  const checks = [];
  const cites = unresolvedCitations(obs, read);
  if (expect.flagged) {
    // the answer is bad by construction (the case's model fabricates); what is graded is whether the turn says so
    const flagged = flaggedOnTurn(obs, cites.ids);
    checks.push(check("flagged_on_turn", cites.ids.length > 0 && flagged, cites.ids.length
      ? (flagged ? `the turn flags ${cites.ids.join(", ")} (its usage event counts ${cites.ids.length} unresolved)` : `the answer cites ${cites.ids.join(", ")}, which no tool returned, and the turn ended ${obs.status} with nothing flagging it`)
      : "the answer cites nothing unresolved: the case's policy did not do what it is for"));
  } else {
    checks.push(check("citations_resolve", cites.ids.length === 0, cites.ids.length ? `cited but returned by no tool: ${cites.ids.join(", ")}` : `${cites.used} cited, all returned by a tool`));
  }
  const banned = of("banned_term");
  if (expect.vocabulary !== false) {
    checks.push(check("vocabulary", banned.length === 0, banned.length ? `banned: ${banned.map((b) => `${b.term} (「${excerpt(answer, b, 6)}」)`).join(", ")}` : "no banned term"));
  }
  const codes = refusedCodes(obs);
  const missing = codes.filter((c) => !mentionsCode(answer, c));
  checks.push(check("refusals_reported", missing.length === 0, codes.length ? (missing.length ? `refused by the kernel and not named: ${missing.join(", ")}` : `named: ${codes.join(", ")}`) : "the kernel refused no claim"));
  const released = of("released_wording").map((f) => f.term);
  checks.push(check("release_wording", released.length === 0, released.length ? `${released.join(", ")}: no tool authorized release` : "no release wording a tool did not authorize"));
  return gradeOf("evidence", checks, {
    citations_used: cites.used, citations_unresolved: cites.ids.length, unresolved_ids: cites.ids,
    banned_terms: banned.map((b) => b.term), refused_codes: codes, release_wording: released,
  });
}
