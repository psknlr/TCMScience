// Run transparency in the thread (DESIGN §3.3): the thinking block, the tool-call card (status including refused,
// where it ran and on which device, duration, arguments, a result preview, hashes), the permission request card,
// and the job card (a pending job never shows partial output as a result).

import { refusalsOf, releaseOf, claimsOf } from "../core/governance.js";
import { glossary, lang, t } from "../core/i18n.js";
import { fill, formatBytes, formatDateTime, formatDuration, h, uid } from "./dom.js";
import { hashBadge, reasonList, verdictPill } from "./governance.js";
import { icon } from "./icons.js";
import { button, chip, disclosure, progressBar, spinner } from "./primitives.js";

// ------------------------------------------------------------------------------------------------- thinking

/** 「思考 · 8 秒」, collapsed by default; thinking is not evidence (DESIGN §3.3). */
export function thinkingBlock({ text = "", durationMs = null, streaming = false, startedAt = null } = {}) {
  const label = h("span.think__label");
  const setLabel = () => {
    if (streaming) {
      const s = startedAt ? Math.max(0, Math.round((Date.now() - startedAt) / 1000)) : 0;
      label.textContent = t("ui.think.live", { s });
    } else {
      label.textContent = durationMs !== null && durationMs !== undefined ? t("ui.think.done", { d: formatDuration(durationMs) }) : t("ui.think.title");
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

/** 「浏览器 · CPU」 / 「本机 Runner · CUDA:0」 with the runtime and address in the tooltip. */
export function whereBadge(receipt, { where: whereOverride, runnerUrl } = {}) {
  const where = receipt?.where || whereOverride || null;
  if (!where) return null;
  const device = deviceLabel(receipt?.device);
  const place = where === "runner" ? t("ui.where.runner") : where === "relay" ? t("ui.where.relay") : t("ui.where.browser");
  const host = where === "runner" && runnerUrl ? hostOf(runnerUrl) : "";
  const tip = [receipt?.runtime, host && `@${host}`, receipt?.decided_by === "router" ? t("ui.where.decided_in_page") : ""].filter(Boolean).join(" ");
  return h("span", { class: ["where", `where--${where}`], "data-tip": tip || null },
    icon(where === "runner" ? "laptop" : "globe", { size: 12 }), h("span", place), device ? h("span.where__device", device) : null);
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
};

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
  let text = envelope?.text || "";
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
export function toolCallCard(call, envelope = null, { catalog, runnerUrl, onOpen, open = false, compact = false } = {}) {
  const root = h("div", { class: "tool-card", "data-call": call.id });
  let expanded = open;
  const bodyId = uid("tc");
  const render = () => {
    const status = envelope?.status || call.status || "running";
    const st = STATUS[status] || STATUS.running;
    const { title, id } = toolTitle(call.name, catalog, call.args);
    const via = envelope?.via && envelope.via !== id ? envelope.via : null;
    const receipt = envelope?.receipt || null;
    const decidedInPage = receipt?.decided_by === "router";
    const where = decidedInPage ? null : receipt?.where || call.where || null;
    const duration = envelope ? envelope.duration_ms : call.endedAt && call.startedAt ? call.endedAt - call.startedAt : null;
    const rel = envelope ? releaseOf(envelope) : null;
    const claims = envelope ? claimsOf(envelope) : [];
    const refusedClaims = claims.filter((c) => c.allowed === false).length;
    const statusLabel = glossary.verdictState(status === "pending" ? "running" : status, undefined, "call");

    const line = (() => {
      if (status === "running" || status === "pending") {
        const placeLabel = where === "runner" ? t("ui.where.runner_short") : where === "browser" ? t("ui.where.browser_short") : "";
        return placeLabel ? t("ui.tool.running_at", { place: placeLabel, name: id, device: deviceLabel(receipt?.device) || "CPU" }) : t("ui.tool.preparing", { name: id });
      }
      if (status === "needs_approval") return t("ui.tool.waiting_approval");
      return envelope?.summary || envelope?.error?.message || statusLabel;
    })();

    const head = h("button", { type: "button", class: "tool-card__head", "aria-expanded": String(expanded), "aria-controls": bodyId, onClick: () => { expanded = !expanded; render(); } },
      h("span", { class: ["tool-card__status", `is-${st.tone}`], role: "img", "aria-label": statusLabel }, icon(st.icon, { size: 14, className: st.spin ? "spin" : "" })),
      h("span.tool-card__title",
        h("span", { class: ["tool-card__name", title === id && "mono"] }, title),
        title !== id || via ? h("span.tool-card__id.mono", title === id ? `→ ${via}` : via ? `${id} → ${via}` : id) : null),
      h("span.tool-card__meta",
        rel && !compact ? verdictPill(rel.badge, { size: "sm" }) : null,
        refusedClaims ? chip({ label: t("ui.tool.claims_refused", { n: refusedClaims }), tone: "vermilion", icon: "ban" }) : null,
        status === "refused" ? h("span.tool-card__refused", statusLabel) : null,
        decidedInPage ? h("span.where.where--none", { "data-tip": t("ui.where.decided_in_page") }, icon("circleDashed", { size: 12 }), h("span", t("ui.where.not_run"))) : whereBadge(receipt, { where, runnerUrl }),
        !decidedInPage && duration !== null && duration !== undefined && status !== "running" && status !== "pending" ? h("span.tool-card__dur.num", formatDuration(duration)) : null),
      icon("chevronDown", { size: 14, className: "tool-card__chev" }));

    const summary = h("p", { class: ["tool-card__line", (status === "refused" || status === "failed") && "is-attention"] }, line);

    const body = h("div.tool-card__body", { id: bodyId, hidden: !expanded, class: expanded ? "is-open" : "" });
    if (expanded) body.append(...cardBody(call, envelope, { onOpen, status }));

    root.className = ["tool-card", `is-${st.tone}`, expanded && "is-open", status === "refused" && "tool-card--refused"].filter(Boolean).join(" ");
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

function cardBody(call, envelope, { onOpen, status }) {
  const out = [];
  const summary = argsSummary(call.args);
  if (summary) out.push(h("div.tool-card__row", h("span.tool-card__k", t("ui.tool.args")), h("span.tool-card__v", summary)));
  out.push(disclosure({ summary: t("ui.tool.args_json"), content: () => h("pre.code-block", prettyJson(call.args || {})), className: "tool-card__json" }));
  if (envelope) {
    if (status === "refused" || status === "failed" || status === "cancelled") {
      const refusals = refusalsOf(envelope);
      if (refusals.length) out.push(reasonList(refusals));
      // error.hint is written for the model (English, terse); the person reads the localized message, once
      const msg = envelope.error?.message || "";
      if (msg && msg !== envelope.summary) {
        out.push(h("div", { class: ["tool-card__error", status === "refused" ? "is-refusal" : ""] }, h("p", msg)));
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
    const facts = [
      r.runtime ? h("span", r.runtime) : null,
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
  const root = h("div.permission", { role: "group", "aria-label": t("ui.perm.title") });
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
    const actions = decided
      ? h("p", { class: ["permission__decided", `is-${decided}`] }, icon(decided === "deny" ? "ban" : "check", { size: 14 }), t(`ui.perm.decided.${decided}`))
      : h("div.permission__actions",
        button({ label: t("ui.perm.once"), variant: "primary", size: "sm", onClick: () => decide("once") }),
        button({ label: t("ui.perm.project"), variant: "secondary", size: "sm", onClick: () => decide("project") }),
        button({ label: t("ui.perm.deny"), variant: "ghost", size: "sm", onClick: () => decide("deny") }));
    root.className = ["permission", decided && "is-decided", decided === "deny" && "is-denied"].filter(Boolean).join(" ");
    fill(root, 
      h("header.permission__head", icon("shieldCheck", { size: 16 }), h("p.permission__title", decided ? t("ui.perm.title_decided") : t("ui.perm.title")), h("span.permission__tool", h("span", title), h("code.mono", request.entry || request.tool))),
      h("ul.permission__reasons", { role: "list" }, items),
      request.args ? h("p.permission__args.muted", argsSummary(request.args, 160)) : null,
      actions);
  };
  const decide = (d) => {
    decided = d;
    render();
    onDecide?.(d);
  };
  render();
  root.setDecided = (d) => { decided = d; render(); };
  return root;
}

// ------------------------------------------------------------------------------------------------- jobs

const JOB_STEPS = ["submitted", "running", "collected"];

/**
 * A runner job: submitted → running → collected and verified (or failed / cancelled), each step with its time. While
 * it is not succeeded, nothing it produced is shown as a result (CONTRACTS §4: unfinished work is pending).
 * job: the runner's Job; opts: {files: [{path, bytes, sha256, media_type}], onCancel, onOpenFile(path), logs: [..]}.
 */
export function jobCard(job, { files = null, onCancel, onOpenFile, logs = [] } = {}) {
  const state = job?.state || "queued";
  const done = state === "succeeded";
  const ended = ["succeeded", "failed", "cancelled"].includes(state);
  const stepState = (s) => {
    if (s === "submitted") return "done";
    if (s === "running") return state === "queued" ? "todo" : state === "running" ? "active" : job.started_at ? "done" : "skipped";
    if (state === "succeeded") return "done";
    if (state === "failed" || state === "cancelled") return "stopped";
    return "todo";
  };
  const stepLabel = (s) => {
    if (s === "collected" && (state === "failed" || state === "cancelled")) return glossary.verdictState(state, undefined, "job");
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
  const fraction = job.progress?.fraction;
  const progress = state === "running" ? h("div.job__progress",
    progressBar({ value: typeof fraction === "number" ? fraction : null, label: t("ui.job.progress") }),
    job.progress?.message ? h("p.job__msg.muted", job.progress.message) : null) : null;
  const pendingNote = !done ? h("p.job__pending", icon("hourglass", { size: 12 }), t(ended ? "ui.job.no_result" : "ui.job.pending_note")) : null;
  const err = state === "failed" && job.outcome ? h("div.tool-card__error", h("p", job.outcome.error || t("ui.job.failed")), job.outcome.problems?.length ? h("ul", job.outcome.problems.map((p) => h("li", typeof p === "string" ? p : JSON.stringify(p)))) : null) : null;
  const fileList = done && (files || job.artefacts)?.length ? h("ul.job__files", { role: "list" }, (files || job.artefacts.map((a) => ({ path: a.name, bytes: a.bytes, sha256: a.sha256 }))).map((f) => h("li.job__file",
    icon("file", { size: 14 }),
    onOpenFile ? h("button.job__file-name.mono", { type: "button", onClick: () => onOpenFile(f.path) }, f.path) : h("span.job__file-name.mono", f.path),
    f.bytes !== undefined ? h("span.muted.num", formatBytes(f.bytes)) : null,
    hashBadge(f.sha256, { compact: true })))) : null;
  return h("div", { class: ["job", `is-${state}`] },
    h("header.job__head",
      icon("workflow", { size: 16 }),
      h("div.job__title", h("p.job__kind", t(`ui.job.kind.${job.kind}`) !== `ui.job.kind.${job.kind}` ? t(`ui.job.kind.${job.kind}`) : job.kind), h("p.job__id.mono.muted", job.id + (job.device ? ` · ${deviceLabel(job.device)}` : ""))),
      verdictPill({ tone: { queued: "slate", running: "navy", succeeded: "jade", failed: "warning", cancelled: "slate" }[state], icon: { queued: "◷", running: "…", succeeded: "✓", failed: "!", cancelled: "■" }[state], label: glossary.verdictState(state, undefined, "job") }),
      !ended && onCancel ? button({ label: t("ui.job.cancel"), size: "sm", variant: "ghost", icon: "circleStop", onClick: () => onCancel(job) }) : null),
    timeline, progress, err, pendingNote, fileList,
    logs.length ? disclosure({ summary: t("ui.job.logs", { n: logs.length }), content: () => h("pre.code-block", logs.slice(-200).join("\n")) }) : null);
}

function timeOf(iso) {
  try {
    return new Intl.DateTimeFormat(lang() === "zh" ? "zh-CN" : "en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(iso));
  } catch { return iso; }
}
