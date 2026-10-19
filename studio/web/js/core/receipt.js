// The receipt of an envelope made in the page (CONTRACTS §3 and §6): the one shape every JS builder uses — the
// router's decided outcomes (synthEnvelope), the browser runtime's own failures (runtime/browser.js), the runner
// client's (runtime/runner.js), a runtime that never loaded (runtime/index.js UnavailableRuntime) and the two catalog
// answers the page gives itself (core/catalog.js) — so their fields cannot drift apart.
//
// A page-made envelope says what happened without claiming more: nothing was hashed as output, no kernel or engine
// ran, and its result is { kind: null, outcome: "not_executed", assurance: "none" } unless the builder knows better (a
// runtime that threw is "failed", a stop is "cancelled", a page answer "succeeded"). Envelopes that come back from a
// runtime keep the receipt that runtime wrote: nothing here is added to them except by the router, and then only the
// placement and the approvals (receipt.execution.placement / .approvals), never a schema or a result.

import { nowIso } from "./util.js";

/** The receipt version this page writes; a receipt without `schema` is a legacy one, its fields as they were. */
export const RECEIPT_SCHEMA = "tcmstudio.receipt/1";

/** Who decided a page-made envelope (receipt.decided_by). */
export const DECIDERS = Object.freeze(["router", "browser-runtime", "runner-client", "unavailable-runtime"]);

/** receipt.result.outcome: what became of the call. validated ⇔ succeeded and an assurance other than none. */
export const OUTCOMES = Object.freeze(["not_executed", "queued", "running", "succeeded", "validated", "failed", "cancelled"]);

/** receipt.result.assurance: release_authorized (the kernel's six release states passed) | outputs_verified (hashes). */
export const ASSURANCES = Object.freeze(["release_authorized", "outputs_verified", "none"]);

/** receipt.result.kind: what the result is, when there is one; null for an envelope with nothing to classify. */
export const RESULT_KINDS = Object.freeze(["record", "computation", "prediction", "draft", "fixture", "system"]);

/**
 * The receipt of a page-made envelope. where: "page" for an outcome the router decided before any runtime had the
 * call; the runtime a client stands for ("browser" | "runner") when that client made the envelope itself (the browser
 * runtime, the runner client, a runtime that never loaded), decided_by then saying it was not that runtime's own
 * answer; "browser" for an answer the page computed itself (catalog_search, capabilities_status). decided_by: one of
 * DECIDERS, or omitted for a page answer. outcome/kind: receipt.result (default: not executed).
 */
export function baseReceipt({
  where = "page", runtime = null, device = null, versions = {}, decided_by, input_sha256 = null, started_at,
  output_sha256 = null, outcome = "not_executed", kind = null,
} = {}) {
  return {
    schema: RECEIPT_SCHEMA,
    where,
    runtime,
    device,
    versions: { ...(versions && typeof versions === "object" ? versions : {}) },
    composite_version: null,
    content_hash: null,
    audit_head: null,
    input_sha256: input_sha256 || null,
    output_sha256: output_sha256 || null,
    started_at: started_at || nowIso(),
    finished_at: nowIso(),
    ...(decided_by ? { decided_by } : {}),
    // made by this page's JavaScript: no Python runtime, compute kernel or engine stands behind it
    execution: { runtime: "page", kernel: null, engine: null },
    result: { kind: RESULT_KINDS.includes(kind) ? kind : null, outcome: OUTCOMES.includes(outcome) ? outcome : "not_executed", assurance: "none" },
  };
}

/**
 * The outcome of a page-made envelope from its status and error: a stop is cancelled, a runtime that threw (or timed
 * out) failed, everything else (needs_approval, declined, unavailable, network_off, not_found, refused) did not run.
 */
export function pageOutcome(status, error) {
  if (status === "cancelled") return "cancelled";
  if (error && (error.type === "runtime_error" || error.type === "timeout")) return "failed";
  return "not_executed";
}
