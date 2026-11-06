// The graders in the PSH evaluator's order (psh/runtime/evaluator.py): structural, execution, evidence, goal, then
// cost. All of them run on every turn (a later layer's numbers are worth having when an earlier one fails); the
// case's verdict names the first that failed.

import { cost } from "./cost.mjs";
import { evidence } from "./evidence.mjs";
import { execution } from "./execution.mjs";
import { goal } from "./goal.mjs";
import { structural } from "./structural.mjs";

export const GRADERS = { structural, execution, evidence, goal, cost };
export const ORDER = Object.keys(GRADERS);

/** {pass, failed: [grader names], first_failure, graders: [{grader, pass, checks, measures}]}. */
export function gradeAll(obs, expect = {}) {
  const graders = ORDER.map((name) => GRADERS[name](obs, expect[name] || {}));
  const failed = graders.filter((g) => !g.pass).map((g) => g.grader);
  return { pass: failed.length === 0, failed, first_failure: failed[0] || null, graders };
}

/** One grader's result in a grading. */
export const graderOf = (grades, name) => grades?.graders.find((g) => g.grader === name) || null;
