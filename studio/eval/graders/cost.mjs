// Cost grader (layer 5): what the turn cost: model calls (each request the client sent, retries included), upstream
// calls and limit-gate hits behind the relay, the peak and total request bytes, tokens, the waits retries asked for,
// and wall time. Every number is measured and reported; a case sets the ceilings that matter for it (a quota error
// worth one upstream call, a request under the relay's MAX_BODY_BYTES).

import { check, gradeOf } from "./util.mjs";

export function costMeasures(obs) {
  const bytes = (obs.requests || []).map((r) => r.bytes);
  return {
    model_calls: obs.model_calls ?? bytes.length,
    upstream_calls: obs.upstream_calls ?? null,
    gate_hits: obs.gate_hits ?? null,
    peak_request_bytes: bytes.length ? Math.max(...bytes) : 0,
    total_request_bytes: bytes.reduce((a, b) => a + b, 0),
    request_bytes: bytes,
    tokens_in: obs.usage?.input ?? 0,
    tokens_out: obs.usage?.output ?? 0,
    retries: (obs.retries || []).length,
    retry_waits_ms: (obs.retries || []).map((r) => r.delay_ms),
    ms: obs.ms ?? 0,
  };
}

const CEILINGS = [
  ["max_model_calls", "model_calls", "model calls"],
  ["max_upstream_calls", "upstream_calls", "upstream calls"],
  ["max_gate_hits", "gate_hits", "limit-gate hits"],
  ["max_peak_request_bytes", "peak_request_bytes", "bytes in the largest request"],
  ["max_total_request_bytes", "total_request_bytes", "request bytes in all"],
  ["max_retries", "retries", "retries"],
  ["max_ms", "ms", "ms"],
];

export function cost(obs, expect = {}) {
  const m = costMeasures(obs);
  const checks = [];
  for (const [key, field, words] of CEILINGS) {
    if (expect[key] === undefined) continue;
    const v = m[field];
    // a request over the ceiling: which one first (the turn's request n, counting from 1)
    const first = key === "max_peak_request_bytes" ? m.request_bytes.findIndex((b) => b > expect[key]) + 1 : 0;
    checks.push(check(key, v !== null && v <= expect[key], `${v ?? "unmeasured"} ${words} (at most ${expect[key]})${first ? `; first over it: request ${first} of ${m.request_bytes.length}` : ""}`));
  }
  if (expect.min_retry_wait_ms !== undefined) {
    const shortest = m.retry_waits_ms.length ? Math.min(...m.retry_waits_ms) : null;
    checks.push(check("retry_waits", shortest !== null && shortest >= expect.min_retry_wait_ms, `retry waits ${m.retry_waits_ms.join(", ") || "none"} ms (each at least ${expect.min_retry_wait_ms})`));
  }
  return gradeOf("cost", checks, m);
}
