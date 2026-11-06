// Execution grader (layer 2): did the turn make the calls a correct answer needs (trajectory recall and precision
// against the case's gold trajectory, by catalog entry, so call_tool {tool: "tcmdb.relations"} and tcmdb_relations
// are the same call), none it must not make, and never the same call again after it failed in a way that cannot
// change (the tool or its data are not here, a person said no, the arguments were refused, nothing matched)?

import { outcomeOf } from "../../web/js/core/agent.js";
import { canonical, matches } from "../lib/util.mjs";
import { check, gradeOf } from "./util.mjs";

/** Outcomes a repeat of the same call cannot change. */
export const SETTLED = new Set(["tool_unavailable", "data_unavailable", "not_permitted", "input_error", "no_record"]);

export function trajectoryOf(obs) {
  return (obs.tools || []).map((t) => t.entry || t.name);
}

export function execution(obs, expect = {}) {
  const traj = trajectoryOf(obs);
  const checks = [];
  const measures = { trajectory: traj, calls: traj.length };
  if (Array.isArray(expect.gold)) {
    const gold = expect.gold;
    const hit = gold.filter((g) => traj.some((t) => matches(g, t)));
    const recall = gold.length ? hit.length / gold.length : 1;
    const relevant = traj.filter((t) => gold.some((g) => matches(g, t)) || (expect.allowed || []).some((g) => matches(g, t)));
    const precision = traj.length ? relevant.length / traj.length : gold.length ? 0 : 1;
    const minRecall = expect.min_recall ?? 1;
    const minPrecision = expect.min_precision ?? 0.5;
    Object.assign(measures, { gold, recall: round(recall), precision: round(precision) });
    checks.push(check("trajectory_recall", recall >= minRecall, `recall ${round(recall)} (at least ${minRecall}); missing: ${gold.filter((g) => !hit.includes(g)).join(", ") || "none"}`));
    checks.push(check("trajectory_precision", precision >= minPrecision, `precision ${round(precision)} (at least ${minPrecision}) over ${traj.length} call(s)`));
  }
  const forbidden = (expect.forbidden || []).flatMap((p) => traj.filter((t) => matches(p, t)).map((t) => t));
  if (expect.forbidden) checks.push(check("no_forbidden_calls", forbidden.length === 0, forbidden.length ? `forbidden: ${[...new Set(forbidden)].join(", ")}` : "none"));
  const repeats = [];
  const settled = new Map(); // a call (entry and canonical arguments) → {cls, step} of its first settled failure
  for (const t of obs.tools || []) {
    const key = `${t.entry || t.name}\u0000${canonical(t.args ?? {})}`;
    const first = settled.get(key);
    if (first) repeats.push(`${t.entry || t.name} at step ${t.step}, after ${first.cls} at step ${first.step}`);
    const env = t.envelope || {};
    const cls = env.budget ? "not_permitted" : (outcomeOf(env) || [null])[0];
    if (SETTLED.has(cls) && !first) settled.set(key, { cls, step: t.step });
  }
  checks.push(check("no_repeat_of_settled_failure", repeats.length === 0, repeats.length ? `repeated: ${repeats.join("; ")}` : "no call was repeated after a failure a retry cannot change"));
  measures.repeats = repeats.length;
  return gradeOf("execution", checks, measures);
}

const round = (x) => Math.round(x * 1000) / 1000;
