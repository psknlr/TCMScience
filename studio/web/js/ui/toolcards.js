// Run transparency in the thread (DESIGN §3.3): the thinking block, the tool-call card (status including refused,
// where it ran and on which device, duration, arguments, a result preview, hashes), the permission request card,
// and the job card (a pending job never shows partial output as a result).
//
// What a result is (receipt.result, core/governance.js resultOf): a chip of its kind — 检索到的记录 · 计算结果 ·
// 预测（非实测）· 草稿 · 演示数据（非真实数据）· 系统 · 未运行 — and its assurance in the receipt's own words
// (输出哈希已核验 / 已准予发布), never a bare "verified". An envelope the page made (any receipt.decided_by) never ran
// in a runtime: it is shown as 未运行 (or, when a runtime may have started it and gave no answer, 无结果), never under
// the browser's or the runner's badge. A legacy envelope's kind is derived, and says so.

import { resultOf, refusalsOf, releaseOf, claimsOf } from "../core/governance.js";
import { glossary, lang, t } from "../core/i18n.js";
import { scrubProjectRefs } from "../core/publiclinks.js";
import { isLoopbackUrl } from "../core/util.js";
import { fill, formatBytes, formatDateTime, formatDuration, formatNumber, guessLang, h, uid } from "./dom.js";
import { candidateChip, hashBadge, limitationsBlock, reasonList, verdictPill } from "./governance.js";
import { icon } from "./icons.js";
import { button, chip, disclosure, keyValue, progressBar, spinner } from "./primitives.js";

/**
 * An envelope's one-line summary in the interface language: `summary_en` on an English page when the runner or Pyodide
 * wrote one, else `summary` (Chinese). `lang` is the language of the string actually shown ("en" or "zh-Hans", by its
 * script), so a screen reader voices it right; null when there is nothing to show.
 */
export function envelopeSummary(envelope) {
  const en = typeof envelope?.summary_en === "string" ? envelope.summary_en.trim() : "";
  const text = scrubProjectRefs(lang() === "en" && en ? en : String(envelope?.summary || ""));
  return { text, lang: text ? guessLang(text) || null : null };
}

// ------------------------------------------------------------------------------------------------- thinking

/** 「思考 · 8 秒」, collapsed by default; thinking is not evidence (DESIGN §3.3). */
export function thinkingBlock({ text = "", durationMs = null, streaming = false, startedAt = null } = {}) {
  const label = h("span.think__label");
  const setLabel = () => {
    if (streaming) {
      const s = startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0;
      label.textContent = t("ui.think.live", { s });
    } else {
      // under a second is not worth a number (a reasoning block that arrived in one burst reads 「思考 · 0 毫秒」)
      label.textContent = durationMs !== null && durationMs !== undefined && durationMs >= 1000 ? t("ui.think.done", { d: formatDuration(durationMs) }) : t("ui.think.title");
    }
  };
  setLabel();
  const body = h("div.think__body",
    h("div.think__text", String(text).split(/\n{2,}/).map((p) => h("p", p))),
    h("p.think__note", icon("info", { size: 12 }), t("ui.think.note")));
  const d = disclosure({ summary: [icon("brain", { size: 14, className: "think__icon" }), label, streaming ? spinner({ size: 12 }) : null], content: body, className: ["think", streaming && "is-streaming"].filter(Boolean).join(" "), summaryClass: "think__summary" });
  d.update = ({ text: next, durationMs: dm, streaming: st } = {}) => {
    if (next !== undefined) {
      const tx = body.querySelector(".think__text");
      fill(tx, ...String(next).split(/\n{2,}/).map((p) => h("p", p)));
    }
    if (dm !== undefined) durationMs = dm;
    if (st !== undefined) {
      streaming = st;
      d.classList.toggle("is-streaming", st);
      if (!st) d.querySelector(".spinner")?.remove();
    }
    setLabel();
  };
  if (streaming) {
    const timer = setInterval(() => { if (!d.isConnected || !streaming) clearInterval(timer); else setLabel(); }, 1000);
  }
  return d;
}

// ------------------------------------------------------------------------------------------------- where it ran

/**
 * 「浏览器 · CPU」 / 「浏览器 · WebAssembly SIMD」 / 「本机 Runner · CUDA:0」 / 「远程 Runner · CPU」, with the runtime, the
 * address and the device's evidence (selected, validated, …) in the tooltip. A page-made envelope gets the not-run
 * badge instead (pageBadge): it never ran where its receipt's `where` names.
 */
export function whereBadge(receipt, { where: whereOverride, runnerUrl } = {}) {
  if (receipt?.decided_by) return pageBadge(receipt);
  const where = receipt?.where || whereOverride || null;
  if (!where) return null;
  const backend = receipt?.where === "browser" ? receipt?.compute?.backend : null;
  // the browser's CPU kernels and the GPU are named by what computed (receipt.compute.backend), not the device alone
  const device = backend && backend !== "pyodide" ? computeBackendLabel(backend) : deviceLabel(receipt?.device);
  const remote = where === "runner" && runnerUrl && !isLoopbackUrl(runnerUrl);
  const place = where === "runner" ? (remote ? t("ui.where.runner_remote") : t("ui.where.runner")) : where === "relay" ? t("ui.where.relay") : t("ui.where.browser");
  const host = where === "runner" && runnerUrl ? hostOf(runnerUrl) : "";
  const rung = receipt?.execution?.device?.evidence;
  const tip = [receipt?.runtime, host && `@${host}`, rung ? t(`ui.prov.evidence.${rung}`) : ""].filter(Boolean).join(" · ");
  return h("span", { class: ["where", `where--${where}`], "data-tip": tip || null },
    icon(where === "runner" ? "laptop" : "globe", { size: 12 }), h("span", place), device ? h("span.where__device", device) : null);
}

/**
 * The badge of an envelope the page made (receipt.decided_by): 未运行 when nothing ran (not_executed), 无结果 when a
 * runtime may have started the call and gave no answer (failed, cancelled), with who decided in the tooltip.
 */
export function pageBadge(receipt) {
  const outcome = resultOf({ receipt }).outcome;
  const notRun = !["failed", "cancelled"].includes(outcome);
  const by = ["router", "browser-runtime", "runner-client", "unavailable-runtime"].includes(receipt?.decided_by) ? receipt.decided_by : "router";
  return h("span.where.where--none", { "data-tip": t(`ui.where.decided.${by}`) },
    icon("circleDashed", { size: 12 }), h("span", notRun ? t("ui.where.not_run") : t("ui.where.no_result")));
}

/** WebGPU / WebAssembly SIMD / JavaScript 内核 / Pyodide (CPU): receipt.compute.backend in words. */
export function computeBackendLabel(backend) {
  const b = String(backend || "");
  return ["webgpu", "wasm-simd", "js", "pyodide"].includes(b) ? t(`ui.compute.backend.${b}`) : b;
}

/**
 * What computed a browser call, from receipt.compute: 「WebAssembly SIMD · 经 Python 核对」, 「WebGPU · GPU 计数经
 * Python 复核」, 「Pyodide（CPU）」; null without a compute receipt. "Accelerated" only when the receipt measured it, on a
 * physical adapter; a software adapter is never GPU acceleration.
 */
export function computeSummary(receipt) {
  const c = receipt?.compute;
  if (!c || typeof c !== "object" || !c.backend) return null;
  const parts = [computeBackendLabel(c.backend)];
  const kernelUsed = Array.isArray(c.kernels) && c.kernels.some((k) => k?.outcome === "used");
  if (c.backend === "webgpu") parts.push(t("ui.compute.gpu_checked"));
  else if (kernelUsed) parts.push(t("ui.compute.kernel_checked"));
  if (c.backend === "webgpu" && c.gpu?.accelerated === true && c.gpu?.adapter?.kind === "physical") parts.push(t("ui.compute.measured_faster"));
  if (c.gpu_rejected) parts.push(t("ui.compute.gpu_rejected"));
  return parts.join(" · ");
}

// ------------------------------------------------------------------------------------------------- result kind

const KIND_CHIP = {
  record: { tone: "navy", icon: "database" },
  computation: { tone: "neutral", icon: "calculator" },
  prediction: { tone: "slate", icon: "hexagon", dashed: true },
  draft: { tone: "slate", icon: "edit" },
  fixture: { tone: "ochre", icon: "flask", dashed: true },
  system: { tone: "neutral", icon: "settings" },
  not_run: { tone: "slate", icon: "circleDashed", dashed: true },
  pending: { tone: "slate", icon: "hourglass" },
};

/**
 * What an envelope's result is: {key, outcome, assurance, derived}. key: a result kind (record | computation |
 * prediction | draft | fixture | system), not_run (nothing ran: refused, awaiting approval, not runnable, any
 * page-made envelope that did not reach a runtime), pending (a job queued or running), or null (a failure: no result
 * to classify, or a kind that cannot be told). derived: the envelope predates receipt.result (resultOf derived it).
 */
export function resultView(envelope) {
  if (!envelope || typeof envelope !== "object") return null;
  const r = resultOf(envelope);
  let key = null;
  if (envelope.receipt?.decided_by) key = ["failed", "cancelled"].includes(r.outcome) ? null : "not_run";
  else if (r.outcome === "not_executed") key = "not_run";
  else if (r.outcome === "queued" || r.outcome === "running") key = "pending";
  else if (r.outcome === "succeeded" || r.outcome === "validated") key = KIND_CHIP[r.kind] ? r.kind : null;
  return { key, outcome: r.outcome, assurance: r.assurance || "none", derived: Boolean(r.derived) };
}

/** The result-kind chip (icon + words), or null. A derived kind says so in its tooltip. */
export function resultChip(envelope) {
  const v = resultView(envelope);
  if (!v?.key) return null;
  const c = KIND_CHIP[v.key];
  return chip({ label: t(`ui.result.kind.${v.key}`), tone: c.tone, icon: c.icon, dashed: Boolean(c.dashed), className: "result-chip", title: v.derived ? t("ui.result.derived") : null, attrs: { "data-result": v.key } });
}

/** The assurance in the receipt's words: 输出哈希已核验 / 已准予发布 / 无独立核验. Never a bare "verified". */
export function assuranceLabel(assurance) {
  return t(`ui.result.assurance.${["release_authorized", "outputs_verified"].includes(assurance) ? assurance : "none"}`);
}

export function deviceLabel(device) {
  const d = String(device || "").toLowerCase();
  if (!d) return "";
  if (d === "cpu") return "CPU";
  if (d === "mps") return "MPS";
  if (d === "webgpu") return "WebGPU";
  const m = /^(cuda|rocm):(\d+)$/.exec(d);
  if (m) return `${m[1] === "cuda" ? "CUDA" : "ROCm"}:${m[2]}`;
  return device;
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return url; }
}

// ------------------------------------------------------------------------------------------------- tool call card

const STATUS = {
  pending: { icon: "loader", tone: "running", spin: true },
  running: { icon: "loader", tone: "running", spin: true },
  needs_approval: { icon: "shieldAlert", tone: "approval" },
  succeeded: { icon: "check", tone: "ok" },
  failed: { icon: "alert", tone: "failed" },
  refused: { icon: "ban", tone: "refused" },
  cancelled: { icon: "circleStop", tone: "cancelled" },
  job_submitted: { icon: "clock", tone: "job" },
  // the person did not approve the call: their decision, never shown as a kernel refusal (DESIGN §7.1 #4)
  declined: { icon: "user", tone: "declined" },
  // the page closed while the call ran: whether it finished is not known here
  interrupted: { icon: "circleDashed", tone: "cancelled" },
  // a clinic result is a draft for a licensed practitioner, not a finished ✓
  clinic_draft: { icon: "edit", tone: "draft" },
  // a job_status poll that answered (the call succeeded) about a job that is not done: never a ✓
  job_queued: { icon: "clock", tone: "job" },
  job_running: { icon: "clock", tone: "job" },
  job_failed: { icon: "alert", tone: "failed" },
  job_cancelled: { icon: "circleStop", tone: "cancelled" },
};

/** A call the person declined at the approval card (the router's synthesized envelope records their decision). */
export function isDeclined(envelope) {
  return envelope?.approval?.decision === "deny";
}

/** How a call's state is shown: {key, label} — a declined call and a clinic draft get their own words. */
export function callStateView(status, envelope) {
  if (isDeclined(envelope)) return { key: "declined", label: t("ui.tool.declined") };
  if (status === "succeeded" && envelope?.governance?.kind === "clinic") return { key: "clinic_draft", label: t("ui.tool.clinic_draft") };
  // the poll succeeded, the job did not (yet): the card says the job's state, not 完成
  const jobState = envelope?.job && typeof envelope.job === "object" ? envelope.job.state : null;
  if (status === "succeeded" && ["queued", "running", "failed", "cancelled"].includes(jobState)) {
    return { key: `job_${jobState}`, label: t("ui.tool.job_state", { state: glossary.verdictState(jobState, undefined, "job") }) };
  }
  if (status === "interrupted") return { key: "interrupted", label: t("ui.tool.interrupted") };
  return { key: status, label: glossary.verdictState(status === "pending" ? "running" : status, undefined, "call") };
}

/** A tool's human title in the current language, from the catalog. */
export function toolTitle(name, catalog, args) {
  const target = name === "call_tool" && args?.tool ? args.tool : name;
  const rec = catalog?.coreTool?.(target) || catalog?.byId?.[target] || catalog?.entry?.(target) || null;
  const title = rec?.title;
  const txt = title && typeof title === "object" ? (title[lang()] || title.en || title.zh) : "";
  return { title: txt || target, id: target, entry: rec };
}

/** "subject: 甘草 · co_administered: 甘遂" — short, for the card's header line. */
export function argsSummary(args, max = 120) {
  if (!args || typeof args !== "object") return "";
  const show = (v) => {
    if (v === null || v === undefined) return "—";
    if (Array.isArray(v)) return v.map(show).join("、");
    if (typeof v === "object") return "{…}";
    const s = String(v);
    return s.length > 40 ? `${s.slice(0, 39)}…` : s;
  };
  const src = args.tool && args.arguments && typeof args.arguments === "object" ? args.arguments : args;
  const s = Object.entries(src).map(([k, v]) => `${k}: ${show(v)}`).join(" · ");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function prettyJson(v) {
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

/** The first lines of a result: the envelope text (pretty when it is JSON), cut to `lines`. */
export function resultPreview(envelope, lines = 10) {
  let text = scrubProjectRefs(envelope?.text || "");
  let json = false;
  try {
    const j = JSON.parse(text);
    text = prettyJson(j);
    json = true;
  } catch { /* prose */ }
  const all = String(text).split("\n");
  return { text: all.slice(0, lines).join("\n"), more: Math.max(0, all.length - lines), json };
}

/**
 * call: {id, name, args, where, status, startedAt, endedAt}; envelope: the result (once there). opts: {catalog,
 * runnerUrl, onOpen(callId), open (expanded)}. Returns an element with .update(call, envelope).
 */
export function toolCallCard(call, envelope = null, { catalog, runnerUrl, onOpen, open = false, compact = false, artifactFollows = false } = {}) {
  const root = h("div", { class: "tool-card", "data-call": call.id });
  let expanded = open;
  const bodyId = uid("tc");
  const render = () => {
    const status = envelope?.status || call.status || "running";
    const view = callStateView(status, envelope);
    const declined = view.key === "declined";
    const st = STATUS[view.key] || STATUS[status] || STATUS.running;
    const { title, id } = toolTitle(call.name, catalog, call.args);
    const via = envelope?.via && envelope.via !== id ? envelope.via : null;
    const receipt = envelope?.receipt || null;
    // any page-made envelope (the router, a runtime client, a runtime that never loaded): not under a runtime's badge
    const decidedInPage = Boolean(receipt?.decided_by);
    const rv = envelope ? resultView(envelope) : null;
    const where = decidedInPage ? null : receipt?.where || call.where || null;
    const duration = envelope ? envelope.duration_ms : call.endedAt && call.startedAt ? call.endedAt - call.startedAt : null;
    const rel = envelope ? releaseOf(envelope) : null;
    const claims = envelope ? claimsOf(envelope) : [];
    const refusedClaims = claims.filter((c) => c.allowed === false).length;
    const statusLabel = view.label;

    const line = (() => {
      if (status === "running" || status === "pending") {
        const placeLabel = where === "runner" ? t("ui.where.runner_short") : where === "browser" ? t("ui.where.browser_short") : "";
        return placeLabel ? t("ui.tool.running_at", { place: placeLabel, name: id, device: deviceLabel(receipt?.device) || "CPU" }) : t("ui.tool.preparing", { name: id });
      }
      if (status === "needs_approval") return t("ui.tool.waiting_approval");
      if (status === "interrupted") return t("ui.tool.interrupted_line");
      return envelopeSummary(envelope).text || envelope?.error?.message || statusLabel;
    })();
    const lineLang = (status === "running" || status === "pending" || status === "needs_approval" || status === "interrupted") ? null
      : envelopeSummary(envelope).lang;

    const head = h("button", { type: "button", class: "tool-card__head", "aria-expanded": String(expanded), "aria-controls": bodyId, "data-focus-key": `call:${call.id}`, onClick: () => { expanded = !expanded; render(); } },
      h("span", { class: ["tool-card__status", `is-${st.tone}`], role: "img", "aria-label": statusLabel }, icon(st.icon, { size: 14, className: st.spin ? "spin" : "" })),
      h("span.tool-card__title",
        h("span", { class: ["tool-card__name", title === id && "mono"] }, title),
        title !== id || via ? h("span.tool-card__id.mono", title === id ? `→ ${via}` : via ? `${id} → ${via}` : id) : null),
      h("span.tool-card__meta",
        // the artifact card that follows in the thread carries the release verdict: it is said there once
        rel && !compact && !(artifactFollows && status === "succeeded") ? verdictPill(rel.badge, { size: "sm" }) : null,
        refusedClaims ? chip({ label: t("ui.tool.claims_refused", { n: refusedClaims }), tone: "vermilion", icon: "ban" }) : null,
        status === "refused" && !declined ? h("span.tool-card__refused", statusLabel) : null,
        declined ? h("span.tool-card__declined", statusLabel) : null,
        view.key === "clinic_draft" ? chip({ label: statusLabel, tone: "ochre", icon: "edit", className: "tool-card__draft" }) : null,
        // a candidate (unpinned) Skill runs but is never released: said on the card, not only in the inspector
        !compact ? candidateChip(envelope) : null,
        // what the result is (a clinic draft already says 草稿; the not-run badge already says 未运行)
        rv?.key && view.key !== "clinic_draft" && !(decidedInPage && rv.key === "not_run") ? resultChip(envelope) : null,
        // the receipt's assurance in its own words (a release is said by the verdict pill)
        rv?.assurance === "outputs_verified" ? chip({ label: assuranceLabel("outputs_verified"), tone: "navy", icon: "hash", className: "assurance-chip" }) : null,
        decidedInPage ? pageBadge(receipt) : whereBadge(receipt, { where, runnerUrl }),
        !decidedInPage && duration !== null && duration !== undefined && status !== "running" && status !== "pending" ? h("span.tool-card__dur.num", formatDuration(duration)) : null),
      icon("chevronDown", { size: 14, className: "tool-card__chev" }));

    const summary = h("p", { class: ["tool-card__line", ((status === "refused" && !declined) || status === "failed") && "is-attention"], lang: lineLang }, line);

    const body = h("div.tool-card__body", { id: bodyId, hidden: !expanded, class: expanded ? "is-open" : "" });
    if (expanded) body.append(...cardBody(call, envelope, { onOpen, status, declined }));

    root.className = ["tool-card", `is-${st.tone}`, expanded && "is-open", status === "refused" && !declined && "tool-card--refused", declined && "tool-card--declined"].filter(Boolean).join(" ");
    fill(root, head, summary, body);
  };
  render();
  root.update = (nextCall, nextEnvelope) => {
    if (nextCall) call = { ...call, ...nextCall };
    if (nextEnvelope !== undefined) envelope = nextEnvelope;
    render();
  };
  root.setOpen = (v) => { expanded = v; render(); };
  return root;
}

function cardBody(call, envelope, { onOpen, status, declined = false }) {
  const out = [];
  const summary = argsSummary(call.args);
  if (summary) out.push(h("div.tool-card__row", h("span.tool-card__k", t("ui.tool.args")), h("span.tool-card__v", summary)));
  out.push(disclosure({ summary: t("ui.tool.args_json"), content: () => h("pre.code-block", prettyJson(call.args || {})), className: "tool-card__json" }));
  if (envelope) {
    // the kernel's refusals are results whatever the call's status: a succeeded run can still be refused release
    // (an unpinned candidate Skill: UNPINNED), and that reason must be readable here
    const refusals = refusalsOf(envelope);
    if (refusals.length) {
      out.push(h("div.tool-card__refusals",
        status === "succeeded" ? h("p.tool-card__k", t("ui.tool.kernel_refusals", { n: refusals.length })) : null,
        reasonList(refusals)));
    }
    if (status === "refused" || status === "failed" || status === "cancelled") {
      // error.hint is written for the model (English, terse); the person reads the localized message, once
      const msg = envelope.error?.message || "";
      if (msg && msg !== envelope.summary) {
        out.push(h("div", { class: ["tool-card__error", status === "refused" && !declined ? "is-refusal" : ""] }, h("p", msg)));
      }
    }
    if (envelope.text && (status === "succeeded" || status === "job_submitted")) {
      const prev = resultPreview(envelope);
      out.push(h("div.tool-card__result",
        h("p.tool-card__k", t("ui.tool.result")),
        h("pre", { class: ["code-block", "code-block--preview", !prev.json && "code-block--wrap"] }, prev.text),
        prev.more ? h("p.muted.small", t("ui.tool.more_lines", { n: prev.more })) : null));
    }
    const r = envelope.receipt || {};
    const hashes = [
      r.input_sha256 ? hashBadge(r.input_sha256, { label: t("ui.tool.input"), compact: true }) : null,
      r.output_sha256 ? hashBadge(r.output_sha256, { label: t("ui.tool.output"), compact: true }) : null,
      r.content_hash ? hashBadge(r.content_hash, { label: "Skill", compact: true }) : null,
    ].filter(Boolean);
    if (hashes.length) out.push(h("div.tool-card__hashes", hashes));
    const compute = computeSummary(r);
    const facts = [
      r.runtime ? h("span", r.runtime) : null,
      compute ? h("span.tool-card__compute", compute) : null,
      r.started_at ? h("time", { datetime: r.started_at, title: formatDateTime(r.started_at) }, formatDateTime(r.started_at)) : null,
      r.durable === false ? h("span", t("ui.tool.not_durable")) : null,
    ].filter(Boolean);
    if (facts.length) out.push(h("p.tool-card__facts.muted", facts.flatMap((f, i) => (i ? [h("span.dot-sep", "·"), f] : [f]))));
  }
  if (onOpen && envelope) {
    out.push(h("div.tool-card__actions", button({ label: t("ui.action.open_inspector"), icon: "inspector", size: "sm", variant: "secondary", onClick: () => onOpen(call.id) })));
  }
  return out;
}

// ------------------------------------------------------------------------------------------------- permission

/**
 * Asks before reaching out (ARCHITECTURE decision 5): network hosts, jobs, sending data to a third party, the first
 * runner call. request: the router's {tool, entry, reason, reasons, what, hosts, args}. onDecide("once"|"project"|"deny").
 * decided: the answer when it was already given.
 */
export function permissionCard(request, { onDecide, decided = null, catalog } = {}) {
  const root = h("div.permission", { role: "group", "aria-label": t("ui.perm.title"), "data-perm": request.callId || null, tabindex: "-1" });
  const render = () => {
    const reasons = request.reasons?.length ? request.reasons : [request.reason];
    const { title } = toolTitle(request.tool, catalog, request.args);
    const items = reasons.map((r) => {
      const hostList = (request.hosts || []).length ? h("span.permission__hosts", request.hosts.map((x) => h("code.permission__host", x))) : h("span.muted", t("ui.perm.no_hosts"));
      const text = {
        network: [t("ui.perm.network"), hostList],
        job: [t("ui.perm.job", { what: request.what || title })],
        confirm: [t("ui.perm.confirm", { what: request.what || title })],
        remote_upload: [t("ui.perm.remote_upload"), hostList],
        first_runner_call: [t("ui.perm.first_runner")],
      }[r] || [request.text || r];
      const ic = { network: "globe", job: "clock", confirm: "shieldAlert", remote_upload: "upload", first_runner_call: "laptop" }[r] || "shield";
      return h("li.permission__reason", icon(ic, { size: 16 }), h("div", text));
    });
    const key = `perm:${request.callId || request.tool}`;
    // a decline is the person's decision, said in neutral words (not the kernel's vermilion 已拒绝)
    const actions = decided
      ? h("p", { class: ["permission__decided", `is-${decided}`], tabindex: "-1", "data-focus-key": key }, icon(decided === "deny" ? "user" : "check", { size: 14 }), t(`ui.perm.decided.${decided}`))
      : h("div.permission__actions",
        button({ label: t("ui.perm.once"), variant: "primary", size: "sm", onClick: () => decide("once"), attrs: { "data-focus-key": `${key}:once` } }),
        button({ label: t("ui.perm.project"), variant: "secondary", size: "sm", onClick: () => decide("project"), attrs: { "data-focus-key": `${key}:project` } }),
        button({ label: t("ui.perm.deny"), variant: "ghost", size: "sm", onClick: () => decide("deny"), attrs: { "data-focus-key": `${key}:deny` } }));
    root.className = ["permission", decided && "is-decided", decided === "deny" && "is-denied"].filter(Boolean).join(" ");
    fill(root, 
      h("header.permission__head", icon("shieldCheck", { size: 16 }), h("p.permission__title", decided ? t("ui.perm.title_decided") : t("ui.perm.title")), h("span.permission__tool", h("span", title), h("code.mono", request.entry || request.tool))),
      h("ul.permission__reasons", { role: "list" }, items),
      request.args ? h("p.permission__args.muted", argsSummary(request.args, 160)) : null,
      actions);
  };
  const decide = (d) => {
    // the button that had focus is replaced by the decision: focus moves to that line, not to <body>
    const hadFocus = root.contains(document.activeElement);
    decided = d;
    render();
    if (hadFocus) root.querySelector(".permission__decided")?.focus({ preventScroll: true });
    onDecide?.(d);
  };
  render();
  root.setDecided = (d) => { if (d === decided) return; decided = d; render(); };
  return root;
}

// ------------------------------------------------------------------------------------------------- jobs

const JOB_STEPS = ["submitted", "running", "collected"];
const ENDED = ["succeeded", "failed", "cancelled"];

/** A job kind's name: the UI's own words, else the runner's title in this language, else the kind itself. */
export function jobTitle(job) {
  const key = `ui.job.kind.${job?.kind}`;
  const own = t(key);
  if (own !== key) return own;
  const title = job?.title;
  if (title && typeof title === "object") return title[lang()] || title.en || title.zh || String(job.kind || "");
  return String(job?.kind || "");
}

/** A progress stage in words (ui.job.stage.<name>: the five pipelines' stages, tcmdb.fetch's, verifying), else the raw name. */
export function jobStageLabel(stage) {
  const key = `ui.job.stage.${stage}`;
  const own = t(key);
  return own !== key ? own : String(stage || "");
}

// the runner's own progress messages (English; kinds.py, jobs.py): never shown as they are, but as a stage in words
const RUNNER_MESSAGE = /^(?:stage \S+(?: \S+)?|verifying the outputs)$/;
/** What jobs.py writes (with stage "verifying") while it collects a job whose process has ended. */
const COLLECTING_MESSAGE = "verifying the outputs";

/**
 * Is the runner collecting this job (its process has ended; its outputs are being checked)? jobs.py then sets stage
 * "verifying" with the message "verifying the outputs". A download (tcmdb.fetch) also reports stage "verifying" while
 * it hashes one file it has just transferred (bioagent.acquisition.service acquisition_state), mid-download, with its
 * log's last line as the message: that is still the running step. Another kind has no stage of that name of its own.
 */
export function isCollecting(job) {
  const p = job?.progress;
  if (job?.state !== "running" || !p || typeof p !== "object" || p.stage !== "verifying") return false;
  const msg = typeof p.message === "string" ? p.message.trim() : "";
  return msg === COLLECTING_MESSAGE || job.kind !== "tcmdb.fetch";
}
const LEGACY_MESSAGE = { "writing the report": "report", "differential expression": "differential_expression" };

/**
 * A job's progress as the card shows it: {stage, label, fraction, files, bytes, verified, log}. The stage comes in
 * words (jobStageLabel); a message the runner composed from the stage is not repeated, and an older pipeline's few
 * fixed messages become their stage; any other message is the last line of the job's own log, shown as such.
 */
export function jobProgressView(progress, { kind } = {}) {
  const p = progress && typeof progress === "object" ? progress : {};
  const msg = typeof p.message === "string" ? p.message.trim() : "";
  let stage = typeof p.stage === "string" && p.stage ? p.stage : null;
  let log = null;
  let label = stage ? jobStageLabel(stage) : null;
  // a download checking one file it has just transferred, not the runner checking the job's outputs
  if (stage === "verifying" && kind === "tcmdb.fetch" && msg !== COLLECTING_MESSAGE) label = t("ui.job.stage.verifying_file");
  if (msg && !RUNNER_MESSAGE.test(msg)) {
    const quant = /^quantified (\d+) of (\d+) samples$/.exec(msg);
    if (!stage && LEGACY_MESSAGE[msg]) { stage = LEGACY_MESSAGE[msg]; label = jobStageLabel(stage); }
    else if (!stage && quant) { stage = "quantify"; label = t("ui.job.quantified", { done: quant[1], total: quant[2] }); }
    else log = msg;
  }
  return {
    stage, label, log,
    fraction: typeof p.fraction === "number" && Number.isFinite(p.fraction) ? Math.max(0, Math.min(1, p.fraction)) : null,
    files: p.files && Number.isFinite(p.files.total) ? p.files : null,
    bytes: p.bytes && Number.isFinite(p.bytes.done) ? p.bytes : null,
    verified: p.verified && typeof p.verified === "object" ? p.verified : null,
  };
}

/** The progress block: the stage, a bar, files and bytes done, how kept files were verified, the last log line. kind: the job's. */
export function progressBlock(progress, { label = t("ui.job.progress"), kind } = {}) {
  const v = jobProgressView(progress, { kind });
  const counts = [
    v.files ? t("ui.job.files_done", { done: v.files.done ?? 0, total: v.files.total }) : null,
    v.bytes ? (Number.isFinite(v.bytes.total) && v.bytes.total > 0 ? t("ui.job.bytes_done", { done: formatBytes(v.bytes.done), total: formatBytes(v.bytes.total) }) : t("ui.job.bytes_so_far", { done: formatBytes(v.bytes.done) })) : null,
    v.verified ? t("ui.job.verified_counts", { checksum: v.verified.checksum ?? 0, size: v.verified.size ?? 0, none: v.verified.none ?? 0 }) : null,
    v.verified?.mismatch ? t("ui.job.verified_mismatch", { n: v.verified.mismatch }) : null,
  ].filter(Boolean);
  return h("div.job__progress",
    v.label ? h("p.job__stage", icon(v.stage === "verifying" ? "shieldCheck" : "activity", { size: 12 }), h("span", t("ui.job.stage_now", { stage: v.label }))) : null,
    progressBar({ value: v.fraction, label }),
    counts.length ? h("p.job__counts.num", counts.join(" · ")) : null,
    v.log ? h("p.job__msg.muted", h("span.job__msg-k", t("ui.job.last_log")), " ", h("span.mono", { lang: guessLang(v.log) || null }, v.log)) : null);
}

/** Seconds between two ISO times (or until now), or null. */
function secondsBetween(from, to) {
  const a = Date.parse(from || "");
  const b = to ? Date.parse(to) : Date.now();
  return Number.isFinite(a) && Number.isFinite(b) && b >= a ? (b - a) / 1000 : null;
}

const STAGE_ICON = { succeeded: "check", failed: "x", skipped: "circleDashed", unavailable: "ban", not_applicable: "circleDashed" };

/**
 * A runner job: submitted → running → collected and verified (or failed / cancelled), each step with its time. While
 * it is not succeeded, nothing it produced is shown as a result (CONTRACTS §4: unfinished work is pending): the files
 * it has written so far are listed by name and size only, with no way to open them. A returned job id, a queued or a
 * running job is never "complete"; "verifying" is the runner checking the outputs (isCollecting: never a download's
 * check of one file mid-transfer), and a succeeded job is "finished (output hashes verified)", which is not a release.
 * job: the runner's Job; opts: {files: [{path, bytes, sha256, media_type}], onCancel, onOpenFile(path), logs: [..],
 * partial: [{path, bytes}] (the 'artefact' events), jobReceipt (the parsed out/job_receipt.json at its recorded digest,
 * or {changed: true}), fileHref(path)}.
 */
export function jobCard(job, { files = null, onCancel, onOpenFile, logs = [], partial = [], jobReceipt = null, fileHref = null } = {}) {
  const state = job?.state || "queued";
  const done = state === "succeeded";
  const ended = ENDED.includes(state);
  const verifying = isCollecting(job);
  const stepState = (s) => {
    if (s === "submitted") return "done";
    if (s === "running") return state === "queued" ? "todo" : state === "running" ? (verifying ? "done" : "active") : job.started_at ? "done" : "skipped";
    if (state === "succeeded") return "done";
    if (state === "failed" || state === "cancelled") return "stopped";
    return verifying ? "active" : "todo";
  };
  const stepLabel = (s) => {
    if (s === "collected" && (state === "failed" || state === "cancelled")) return glossary.verdictState(state, undefined, "job");
    if (s === "collected" && verifying) return t("ui.job.step.verifying");
    if (s === "running" && state === "queued") return glossary.verdictState("queued", undefined, "job");
    return t(`ui.job.step.${s}`);
  };
  const stepTime = (s) => ({ submitted: job.created_at, running: job.started_at, collected: job.finished_at }[s] || null);
  const timeline = h("ol.job__timeline", { role: "list" }, JOB_STEPS.map((s) => {
    const ss = stepState(s);
    const at = stepTime(s);
    return h("li", { class: ["job__step", `is-${ss}`] },
      h("span.job__dot", { "aria-hidden": "true" }, ss === "done" ? icon("check", { size: 10 }) : ss === "stopped" ? icon("x", { size: 10 }) : null),
      h("span.job__step-label", stepLabel(s)),
      at ? h("time.job__time.num", { datetime: at, title: formatDateTime(at) }, timeOf(at)) : null);
  }));
  const progress = state === "running" ? progressBlock(job.progress, { kind: job.kind }) : null;
  const pendingNote = !done ? h("p.job__pending", icon("hourglass", { size: 12 }), t(ended ? "ui.job.no_result" : "ui.job.pending_note")) : null;
  const outcome = job.outcome && typeof job.outcome === "object" ? job.outcome : null;
  const err = state === "failed" && outcome ? h("div.tool-card__error", h("p", outcome.error || t("ui.job.failed")), outcome.problems?.length ? h("ul", outcome.problems.map((p) => h("li", typeof p === "string" ? p : JSON.stringify(p)))) : null) : null;

  // device, runtime and resources: the device the runner assigned is a selection, never evidence that it computed
  const receiptDevice = jobReceipt?.device && typeof jobReceipt.device === "object" ? jobReceipt.device : null;
  const assigned = receiptDevice?.assigned || job.device || null;
  const rung = receiptDevice?.evidence || (assigned ? "selected" : null);
  const seconds = Number.isFinite(jobReceipt?.duration_s) ? jobReceipt.duration_s : job.started_at ? secondsBetween(job.started_at, ended ? job.finished_at : null) : null;
  const peak = jobReceipt?.resources?.peak_rss_mb;
  const engines = Array.isArray(jobReceipt?.engine) ? jobReceipt.engine.filter((e) => e && e.id) : [];
  const facts = keyValue([
    assigned ? [t("ui.job.device"), h("span", h("span.mono", deviceLabel(assigned)), h("span.muted", ` · ${t(`ui.prov.evidence.${rung}`)}`))]
      : ended ? null : [t("ui.job.device"), t("ui.job.device_pending")],
    job.device_note || receiptDevice?.note ? [t("ui.job.device_note"), h("span", { lang: "en" }, job.device_note || receiptDevice.note)] : null,
    engines.length ? [t("ui.job.engine"), h("span.mono", engines.map((e) => (e.version ? `${e.id} ${e.version}` : e.id)).join(" · "))] : null,
    seconds !== null ? [ended ? t("ui.job.duration") : t("ui.job.elapsed"), formatDuration(seconds * 1000)] : null,
    Number.isFinite(peak) ? [t("ui.job.peak_memory"), h("span", t("ui.job.peak_memory_v", { mb: formatNumber(peak, { maximumFractionDigits: 1 }) }))] : null,
    outcome && (outcome.exit_code !== undefined && outcome.exit_code !== null) ? [t("ui.job.exit_code"), h("span.mono", String(outcome.exit_code))] : null,
    outcome?.timed_out ? [t("ui.job.timed_out"), t("ui.common.yes")] : null,
    outcome?.note ? [t("ui.job.note"), h("span", { lang: "en" }, String(outcome.note))] : null,
    done && Array.isArray(outcome?.checks) && outcome.checks.length ? [t("ui.job.checks"), h("span.mono", outcome.checks.join(" · "))] : null,
    // its bytes no longer have the digest recorded at collection (ui/jobs.js readJobReceipt): nothing in it is used
    jobReceipt?.changed ? [t("ui.job.receipt"), h("span", t("ui.job.receipt_changed"))] : null,
  ], { className: "job__facts" });

  // the pipeline's stages, as its own record says them
  const stages = Array.isArray(job.stages) && job.stages.length ? h("ol.job__stages", { role: "list", "aria-label": t("ui.job.stages") }, job.stages.filter((x) => x && x.name).map((x) => h("li", { class: ["job__stage-row", `is-${x.status || "unknown"}`] },
    icon(STAGE_ICON[x.status] || "circle", { size: 12 }),
    h("span.job__stage-name", jobStageLabel(x.name)),
    h("span.muted", [x.status ? t(`ui.job.stage_status.${x.status}`) !== `ui.job.stage_status.${x.status}` ? t(`ui.job.stage_status.${x.status}`) : x.status : null, x.required === false ? t("ui.job.optional") : null, x.check ? `${t("ui.job.check")} ${x.check}` : null, x.device ? deviceLabel(x.device) : null, Number.isFinite(x.seconds) ? formatDuration(x.seconds * 1000) : null].filter(Boolean).join(" · "))))) : null;

  // written so far, while it is not collected: names and sizes only (no open, no download, not a result)
  const partialList = !done && partial?.length ? disclosure({
    summary: t("ui.job.partial", { n: partial.length }), className: "job__partial",
    content: () => h("ul.job__partial-list", { role: "list" }, partial.slice(-200).map((f) => h("li", h("span.mono", f.path), Number.isFinite(f.bytes) ? h("span.muted.num", ` ${formatBytes(f.bytes)}`) : null))),
  }) : null;
  const fileList = done && (files || job.artefacts)?.length ? h("ul.job__files", { role: "list" }, (files || job.artefacts.map((a) => ({ path: a.name, bytes: a.bytes, sha256: a.sha256 }))).map((f) => h("li.job__file",
    icon("file", { size: 14 }),
    onOpenFile ? h("button.job__file-name.mono", { type: "button", onClick: () => onOpenFile(f.path) }, f.path) : h("span.job__file-name.mono", f.path),
    f.bytes !== undefined ? h("span.muted.num", formatBytes(f.bytes)) : null,
    hashBadge(f.sha256, { compact: true })))) : null;

  // the limits of what it computed (limitations.md, one per line), and the record to reproduce it
  const limits = Array.isArray(job.limitations) && job.limitations.length ? job.limitations
    : Array.isArray(job.result?.governance?.limitations) ? job.result.governance.limitations : [];
  const limitations = limits.length ? h("div.job__limits", limitationsBlock(limits, { compact: true })) : null;
  const names = new Set([...(files || []).map((f) => f.path), ...(job.artefacts || []).map((a) => a.name)]);
  const records = [
    job.receipt?.path ? { path: job.receipt.path, sha256: job.receipt.sha256, label: t("ui.job.receipt") } : null,
    names.has("run_manifest.json") ? { path: "run_manifest.json", label: t("ui.job.manifest") } : null,
    names.has("run.json") ? { path: "run.json", label: t("ui.job.run_json") } : null,
  ].filter(Boolean);
  const repro = records.length ? h("div.job__repro",
    h("p.job__repro-k", icon("fingerprint", { size: 12 }), t("ui.job.reproduce")),
    h("ul.job__repro-list", { role: "list" }, records.map((r) => h("li",
      fileHref ? h("a.job__file-name.mono", { href: fileHref(r.path), target: "_blank", rel: "noopener noreferrer" }, r.path) : done && onOpenFile ? h("button.job__file-name.mono", { type: "button", onClick: () => onOpenFile(r.path) }, r.path) : h("span.mono", r.path),
      h("span.muted", ` · ${r.label}`),
      r.sha256 ? hashBadge(r.sha256, { compact: true }) : null)))) : null;

  return h("div", { class: ["job", `is-${state}`], "data-job-state": state },
    h("header.job__head",
      icon("workflow", { size: 16 }),
      h("div.job__title", h("p.job__kind", jobTitle(job)), h("p.job__id.mono.muted", job.id + (assigned ? ` · ${deviceLabel(assigned)}` : ""))),
      // finished is not released: a neutral ✓ (as a finished call), never the release verdict's jade
      verdictPill({ tone: { queued: "slate", running: "navy", succeeded: "neutral", failed: "warning", cancelled: "slate" }[state], icon: { queued: "◷", running: "…", succeeded: "✓", failed: "!", cancelled: "■" }[state], label: verifying ? t("ui.job.verifying_pill") : glossary.verdictState(state, undefined, "job") }),
      !ended && onCancel ? button({ label: t("ui.job.cancel"), size: "sm", variant: "ghost", icon: "circleStop", onClick: () => onCancel(job) }) : null),
    timeline, progress, err, pendingNote, facts, stages, partialList, fileList, limitations, repro,
    logs.length ? disclosure({ summary: t("ui.job.logs", { n: logs.length }), content: () => h("pre.code-block", logs.slice(-200).join("\n")) }) : null);
}

function timeOf(iso) {
  try {
    return new Intl.DateTimeFormat(lang() === "zh" ? "zh-CN" : "en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(iso));
  } catch { return iso; }
}
