// What every grader returns: {grader, pass, checks: [{name, pass, detail}], measures}. A grader passes when every
// check it ran passed; a grader with nothing to check passes (it is reported with no checks).

export const check = (name, pass, detail = "", evidence) => ({ name, pass: Boolean(pass), detail, ...(evidence !== undefined ? { evidence } : {}) });

export function gradeOf(grader, checks, measures = {}) {
  return { grader, pass: checks.every((c) => c.pass), checks, measures };
}

/** The answer text the graders read. */
export const answerOf = (obs) => String(obs?.assistant?.content || "");
