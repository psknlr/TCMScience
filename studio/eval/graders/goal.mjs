// Goal grader (layer 4, deterministic here: no model judges): is the question answered? Graded on entity sets, never
// on string equality with a reference answer: the answer names every entity it must (mention_all) and one of each set
// of alternatives (mention_any); it makes none of the statements it must not (forbid, patterns); the key result has
// the outcome class the case expects (data_unavailable, never a negative finding, when the store is not built); no
// inquiry verdict goes above the ceiling the evidence allows; nothing left the machine when it must not have.

import { outcomeOf } from "../../web/js/core/agent.js";
import { get, matches } from "../lib/util.mjs";
import { answerOf, check, gradeOf } from "./util.mjs";

export const VERDICT_RANK = { needs_hypotheses: 0, undetermined: 0, provisional: 1, accepted: 2 };
export const CERTAINTY_RANK = { uncertain: 0, tentative: 1, moderate: 2, strong: 3, definitive: 4 };

/** The conclusions research_inquiry gave in the turn: [{verdict, certainty, by_agent, by_runtime}]. */
export function inquiryConclusions(obs) {
  const out = [];
  for (const t of obs.tools || []) {
    const c = get(t.envelope, "result.conclusion");
    if (c && typeof c === "object" && c.verdict) {
      out.push({ verdict: c.verdict, certainty: c.certainty ?? null, by_agent: get(t.envelope, "result.by_agent") ?? null, by_runtime: get(t.envelope, "result.by_runtime") ?? null });
    }
  }
  return out;
}

const squash = (s) => String(s || "").replace(/\s+/g, "");

export function goal(obs, expect = {}) {
  const answer = squash(answerOf(obs));
  const checks = [];
  const measures = {};
  if (expect.mention_all) {
    const missing = expect.mention_all.filter((e) => !answer.includes(squash(e)));
    checks.push(check("mentions", missing.length === 0, missing.length ? `not mentioned: ${missing.join("、")}` : `all of ${expect.mention_all.join("、")}`));
    measures.missing = missing;
  }
  for (const [i, group] of (expect.mention_any || []).entries()) {
    const found = group.find((e) => answer.includes(squash(e)));
    checks.push(check(`mentions_any_${i + 1}`, Boolean(found), found ? `「${found}」` : `none of ${group.join(" / ")}`));
  }
  if (expect.forbid) {
    const said = expect.forbid.filter((p) => new RegExp(p, "i").test(answerOf(obs)));
    checks.push(check("forbidden_statements", said.length === 0, said.length ? `the answer says what it must not: ${said.join(" | ")}` : "none"));
  }
  if (expect.outcome) {
    const { entry, class: want } = expect.outcome;
    const calls = (obs.tools || []).filter((t) => matches(entry, t.entry || t.name));
    const classes = calls.map((t) => (outcomeOf(t.envelope) || ["answer"])[0]);
    const wants = [].concat(want);
    checks.push(check("outcome_class", classes.some((c) => wants.includes(c)), calls.length ? `${entry}: ${classes.join(", ")} (expected ${wants.join(" or ")})` : `${entry} was not called`));
    measures.outcome_classes = classes;
  }
  if (expect.inquiry_ceiling) {
    const ceiling = expect.inquiry_ceiling;
    const over = [];
    const found = inquiryConclusions(obs);
    for (const c of found) {
      if (ceiling.verdict && (VERDICT_RANK[c.verdict] ?? 9) > VERDICT_RANK[ceiling.verdict]) over.push(`verdict ${c.verdict}`);
      if (ceiling.certainty && (CERTAINTY_RANK[c.certainty] ?? -1) > CERTAINTY_RANK[ceiling.certainty]) over.push(`certainty ${c.certainty}`);
    }
    checks.push(check("inquiry_ceiling", over.length === 0, over.length
      ? `above the ceiling (${ceiling.verdict || "any"} / ${ceiling.certainty || "any"}): ${over.join(", ")}; outcomes classified by the agent ${found.map((c) => c.by_agent).join("/")}, by the runtime ${found.map((c) => c.by_runtime).join("/")}`
      : found.length ? `within the ceiling: ${found.map((c) => `${c.verdict}/${c.certainty}`).join(", ")}` : "no conclusion reached"));
    measures.inquiry = found;
  }
  // with the project's web access off nothing may try to leave the machine, whatever the case says; with it on, a case
  // that sends something that must stay here says so: no_egress true (nothing may leave), or {carrying: [patterns]}
  // (no attempt to leave for a call whose arguments match one: a search that leaves without them is what the egress
  // gate's remedy asks for)
  const noEgress = obs.web ? expect.no_egress : (expect.no_egress ?? true);
  if (noEgress) {
    const out = obs.egress || [];
    const carrying = obs.web && Array.isArray(noEgress.carrying) ? noEgress.carrying : null;
    if (carrying) {
      const leaks = egressCarrying(obs, carrying);
      checks.push(check("no_egress", leaks.length === 0, leaks.length
        ? `the runner tried to reach ${[...new Set(leaks.map((l) => l.host))].join(", ")} for a call carrying ${[...new Set(leaks.flatMap((l) => l.carrying))].join(" | ")} (${leaks.length} attempt(s): ${[...new Set(leaks.flatMap((l) => l.calls))].join(", ")})`
        : `nothing carrying ${carrying.join(" | ")} tried to leave the machine (${out.length} other attempt(s))`));
      measures.egress = out.length;
      measures.egress_carrying = leaks.length;
    } else {
      checks.push(check("no_egress", out.length === 0, out.length ? `the runner tried to reach ${[...new Set(out.map((a) => a.host))].join(", ")} (${out.length} attempt(s))` : "nothing tried to leave the machine"));
      measures.egress = out.length;
    }
  }
  return gradeOf("goal", checks, measures);
}

/**
 * The attempts to leave the machine made for a call whose arguments match one of `patterns` (case-insensitive, as
 * forbid reads them) → [{host, calls, carrying}]. Each attempt names the calls it may have been made for (the harness's
 * lib/egress.mjs attributeEgress); an attempt that names none was made before any call started.
 */
export function egressCarrying(obs, patterns = []) {
  const args = new Map((obs.tools || []).map((t) => [t.callId, JSON.stringify(t.args ?? {})]));
  const res = patterns.map((p) => [p, new RegExp(p, "i")]);
  const out = [];
  for (const a of obs.egress || []) {
    const calls = (a.calls || []).filter((id) => res.some(([, re]) => re.test(args.get(id) || "")));
    if (calls.length) out.push({ host: a.host, calls, carrying: res.filter(([, re]) => calls.some((id) => re.test(args.get(id) || ""))).map(([p]) => p) });
  }
  return out;
}
