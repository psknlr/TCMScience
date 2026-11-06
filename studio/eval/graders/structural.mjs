// Structural grader (PSH evaluator layer 1): did the turn end, the way the case says it should, with every call
// answered, and does it say how it ended? A turn that stops short (the step limit, a cut-off answer) must still give the
// user something and a notice saying so; a turn that fails says whether a retry can help (the Retry button).

import { check, gradeOf } from "./util.mjs";

/** How the turn ended, read from what it returned (core/agent.js's own `termination`, once it has one, is reported beside it). */
export function terminationOf(obs) {
  if (obs.crash) return "crashed";
  if (obs.timed_out) return "timeout";
  const a = obs.assistant || {};
  if (obs.status === "error") return "error";
  if (obs.status === "stopped") return "stopped";
  if (a.limitReached || obs.status === "max_steps") return "max_steps";
  if (a.finishReason === "length") return "truncated";
  if (a.refusal) return "refused";
  return String(a.content || "").trim() ? "answered" : "empty";
}

export function structural(obs, expect = {}) {
  const a = obs.assistant || {};
  const termination = terminationOf(obs);
  const wantStatus = [].concat(expect.status ?? ["ok"]);
  const content = String(a.content || "").trim();
  const checks = [];

  checks.push(check("ended_by_itself", !obs.timed_out && !obs.crash,
    obs.crash ? `the harness caught: ${String(obs.crash).split("\n")[0]}` : obs.timed_out ? `the turn had not ended after ${obs.deadline_ms} ms and was stopped by the eval` : "the turn ended on its own"));
  checks.push(check("status", wantStatus.includes(obs.status), `status ${obs.status} (expected ${wantStatus.join(" or ")})`));
  if (obs.status === "ok" || obs.status === "max_steps") {
    checks.push(check("answer", expect.answer === false || content.length > 0, content ? `${content.length} characters` : "the turn ended with no answer text"));
  }
  if (termination === "max_steps" || termination === "truncated") {
    checks.push(check("states_how_it_ended", Boolean(a.notice), a.notice ? `notice: ${a.notice}` : `the turn ended ${termination === "truncated" ? "cut off (finish_reason length)" : "at the step limit"} and says nothing about it (notice null)`));
  }
  const answered = new Set((obs.tools || []).map((t) => t.callId));
  const dangling = (a.toolCalls || []).filter((c) => !answered.has(c.id)).map((c) => c.name);
  checks.push(check("no_dangling_calls", dangling.length === 0, dangling.length ? `calls without a result: ${dangling.join(", ")}` : "every call has its result"));
  if (Number.isFinite(expect.max_steps)) checks.push(check("steps", obs.steps <= expect.max_steps, `${obs.steps} model steps (at most ${expect.max_steps})`));
  if (expect.error) {
    const e = a.error || null;
    if (expect.error.permanent !== undefined) {
      const permanent = Boolean(e?.permanent);
      checks.push(check("retry_offered", permanent === Boolean(expect.error.permanent),
        `${e ? `error ${e.type || e.status || e.kind}` : "no error"}: ${permanent ? "no Retry (permanent)" : "Retry offered"}; expected ${expect.error.permanent ? "no Retry" : "Retry offered"}`));
    }
    if (expect.error.kind) checks.push(check("error_kind", e?.kind === expect.error.kind, `error kind ${e?.kind ?? "none"} (expected ${expect.error.kind})`));
    if (expect.error.type) {
      const types = [].concat(expect.error.type);
      checks.push(check("error_type", types.includes(e?.type), `error type ${e?.type || "none"} (expected ${types.join(" or ")})`));
    }
  }
  return gradeOf("structural", checks, {
    status: obs.status, termination, reported_termination: a.termination ?? null, steps: obs.steps, answer_chars: content.length,
    notice: a.notice ?? null, error: a.error ? { type: a.error.type || null, kind: a.error.kind || null, permanent: Boolean(a.error.permanent), status: a.error.status ?? null } : null,
  });
}
