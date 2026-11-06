// A model policy written as data: what the "model" does on each request of a turn. Every driver plays the same policy
// (scripted in-process, over the fault server's HTTP, behind the real relay), so a case's gold and mutant behaviours
// are written once.
//
// Steps (one per model request, retries included; the last repeats once they run out):
//   {calls: [{id, name, args}], text?, reasoning?}   tool calls (args may hold {"$from": callId, "path": "result.x"},
//                                                     and {"$repeat": "text", "times": n}: the text n times, a model
//                                                     that pastes a whole document into a call)
//   {text, finish?: "stop" | "length", reasoning?}   an answer ("{{cite:c1}}" is c1's first citation id, "{{cite:c1:2}}"
//                                                     its second, as the loop renumbered them)
//   {repeat: {name, parallel, args}}                 `parallel` calls on every request; "{n}" and "{k}" in string
//                                                     arguments are the request and the call number (always new args)
//   on_no_tools: step                                what to do instead when the request offers no tools, or (with
//                                                     honour_tool_choice) asks for none
//   HTTP-level faults, for the fault and relay drivers only:
//   {http: {status, headers?, json? | text?}}  {stream_error: {...}, after_text?}  {stall: {after_text}}
//   {base_resp: {status_code, status_msg}, as?: "sse" | "json"}
// A reference that cannot be resolved (the call it names failed, or was never made) ends the policy: from then on it
// answers with its fallback (its own `fallback`, for the gold policy the case's, else its last plain answer), as a model
// with nothing to go on would. A mutant never answers with the case's fallback (harness.mjs policyOf).

import { citationsOf } from "../../web/js/core/governance.js";
import { get } from "./util.mjs";

export const FAULT_KINDS = ["http", "stream_error", "stall", "base_resp"];

export class Policy {
  constructor(spec = {}) {
    this.steps = Array.isArray(spec.steps) ? spec.steps : [];
    this.fallbackStep = spec.fallback || [...this.steps].reverse().find((s) => typeof s.text === "string" && !s.calls && !s.repeat) || { text: "" };
    this.envelopes = new Map(); // callId → the envelope the loop gave (renumbered in place)
    this.ended = false;
    this.requests = 0;
  }

  /** The loop's events: tool results are what later steps may refer to. */
  observe(ev) {
    if ((ev?.type === "tool.end" || ev?.type === "tool.update") && ev.callId) this.envelopes.set(ev.callId, ev.envelope);
  }

  /** The step for the next request, resolved: {tools: offered?, toolChoice} describe the request. */
  next({ tools = true, toolChoice } = {}) {
    const k = this.requests++;
    if (this.ended) return resolveText(this.fallbackStep, this);
    let step = this.steps[Math.min(k, this.steps.length - 1)] || this.fallbackStep;
    const noTools = !tools || (step.honour_tool_choice && toolChoice === "none");
    if (noTools && step.on_no_tools) step = step.on_no_tools;
    if (step.repeat) step = { ...step, calls: expandRepeat(step.repeat, k + 1) };
    try {
      return this.resolve(step);
    } catch (err) {
      if (err?.code !== "unresolved") throw err;
      this.ended = true;
      return resolveText(this.fallbackStep, this);
    }
  }

  resolve(step) {
    const out = { ...step };
    if (Array.isArray(step.calls)) out.calls = step.calls.map((c, i) => ({ id: c.id || `call_${this.requests}_${i}`, name: c.name, args: this.value(c.args || {}) }));
    if (typeof step.text === "string") out.text = this.text(step.text);
    return out;
  }

  value(v) {
    if (Array.isArray(v)) return v.map((x) => this.value(x));
    if (v && typeof v === "object") {
      if (typeof v.$repeat === "string") return v.$repeat.repeat(Math.max(0, Math.floor(Number(v.times) || 0)));
      if (typeof v.$from === "string") {
        const env = this.envelopes.get(v.$from);
        const got = env ? get(env, v.path || "result") : undefined;
        if (got === undefined || got === null) throw Object.assign(new Error(`unresolved: ${v.$from}.${v.path}`), { code: "unresolved" });
        return got;
      }
      return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, this.value(x)]));
    }
    return typeof v === "string" ? this.text(v) : v;
  }

  /** "{{cite:c1}}" → "[E3]"; a citation the call did not return becomes [E0], which no tool ever returns. */
  text(s) {
    return String(s).replace(/\{\{cite:([^:}]+)(?::(\d+))?\}\}/g, (_, id, n) => {
      const cites = this.envelopes.has(id) ? citationsOf(this.envelopes.get(id)) : [];
      const c = cites[Math.max(0, Number(n || 1) - 1)];
      return `[${c ? c.id : "E0"}]`;
    });
  }
}

function resolveText(step, policy) {
  return { ...step, calls: undefined, text: policy.text(step.text || "") };
}

function expandRepeat(rep, n) {
  const fill = (v, k) => {
    if (typeof v === "string") return v.replaceAll("{n}", String(n)).replaceAll("{k}", String(k));
    if (Array.isArray(v)) return v.map((x) => fill(x, k));
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([key, x]) => [key, fill(x, k)]));
    return v;
  };
  return Array.from({ length: Math.max(1, Number(rep.parallel) || 1) }, (_, k) => ({ id: `r${n}_${k}`, name: rep.name, args: fill(rep.args || {}, k) }));
}

/** Is this step an HTTP-level fault (only the fault server and the relay can play it)? */
export const isFault = (step) => FAULT_KINDS.some((k) => step && step[k] !== undefined);
