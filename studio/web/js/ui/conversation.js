// The conversation workspace: the thread (role=log) and the docked composer. Stored and live assistant turns share
// one view model and one renderer; the live one is updated in place (per segment), so expanding a tool card or
// hovering a citation survives the stream. Tokens are never announced: the streaming message is aria-busy, and the
// end of a turn is announced once (DESIGN §8.2).

import { citationsOf, claimsOf, evidenceOf, releaseOf } from "../core/governance.js";
import { lang, t } from "../core/i18n.js";
import { renderMarkdown } from "../core/markdown.js";
import { siblingsOf } from "../core/store.js";
import { clear, copyText, fill, formatClock, formatDateTime, formatDuration, h, rafThrottle } from "./dom.js";
import { artifactCard, citationChip, claimCard } from "./governance.js";
import { icon } from "./icons.js";
import { toast } from "./overlay.js";
import { button, iconButton, notice, spinner } from "./primitives.js";
import { mountComposer } from "./composer.js";
import { jobCard, permissionCard, thinkingBlock, toolCallCard } from "./toolcards.js";

export function mountConversation(app, main) {
  const thread = h("div#thread.thread", { role: "log", "aria-label": t("ui.thread.label"), "aria-live": "polite", "aria-relevant": "additions" });
  const scroller = h("div.thread-scroll", thread);
  const jump = h("button.thread-jump", { type: "button", hidden: true, "aria-label": t("ui.thread.jump"), onClick: () => scrollToBottom(true) }, icon("arrowDown", { size: 16 }));
  const composer = mountComposer(app, { variant: "dock" });
  const dock = h("div.composer-dock", composer.el);
  const workspace = h("div.workspace", scroller, jump, dock);
  fill(main, workspace);

  const jobs = new JobTracker(app);
  let liveView = null;
  let stick = true;
  let editing = null; // message id being edited

  scroller.addEventListener("scroll", () => {
    const gap = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    stick = gap < 80;
    jump.hidden = gap < 240;
  }, { passive: true });

  function scrollToBottom(smooth = false) {
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth && !matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "auto" });
  }

  function renderAll() {
    const path = app.path();
    const turn = app.state.turn;
    const liveHere = turn && turn.conversationId === app.state.conversation?.id ? turn : null;
    const nodes = [];
    for (const m of path) {
      if (liveHere && m.id === liveHere.live.id) continue; // a draft of the live turn
      if (m.role === "user") nodes.push(userMessage(m));
      else if (m.role === "assistant") nodes.push(assistantNode(viewOfStored(app, m), { live: false }));
    }
    if (liveHere) {
      liveView = assistantNode(liveHere.live, { live: true });
      nodes.push(liveView);
    } else liveView = null;
    fill(thread, ...nodes);
    requestAnimationFrame(() => { if (stick) scrollToBottom(); });
  }

  // ----------------------------------------------------------------------------------------------- user messages

  function userMessage(m) {
    const sibs = siblingsOf(app.state.messages, m.id);
    const isEditing = editing === m.id;
    const atts = (m.attachmentNames || []).map((n) => h("span.msg__att", icon("paperclip", { size: 12 }), n));
    const bubble = isEditing ? editBox(m) : h("div.msg__bubble", { lang: guessLang(m.content) },
      h("div.msg__text", m.content),
      atts.length ? h("div.msg__atts", atts) : null);
    return h("div.msg.msg--user", { "data-msg": m.id },
      bubble,
      isEditing ? null : h("div.msg__actions.msg__actions--user",
        branchSwitcher(m, sibs),
        iconButton({ icon: "edit", label: t("ui.msg.edit"), size: "sm", disabled: Boolean(app.state.turn), onClick: () => { editing = m.id; renderAll(); } }),
        iconButton({ icon: "copy", label: t("ui.action.copy"), size: "sm", onClick: async () => toast((await copyText(m.content)) ? t("ui.toast.copied") : t("ui.toast.copy_failed")) }),
        h("time.msg__time", { datetime: new Date(m.createdAt).toISOString(), title: formatDateTime(m.createdAt) }, formatClock(m.createdAt))));
  }

  function editBox(m) {
    const ta = h("textarea.input.textarea.msg__edit-input", { rows: "3", "aria-label": t("ui.msg.edit") });
    ta.value = m.content;
    const save = () => {
      const v = ta.value.trim();
      if (!v) return;
      editing = null;
      app.editMessage(m.id, v);
    };
    ta.addEventListener("keydown", (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); editing = null; renderAll(); }
    });
    requestAnimationFrame(() => { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); ta.style.height = `${Math.min(ta.scrollHeight + 2, 320)}px`; });
    return h("div.msg__edit",
      ta,
      h("p.msg__edit-note", t("ui.msg.edit_note")),
      h("div.msg__edit-actions",
        button({ label: t("ui.action.cancel"), size: "sm", variant: "ghost", onClick: () => { editing = null; renderAll(); } }),
        button({ label: t("ui.msg.edit_send"), size: "sm", variant: "primary", onClick: save })));
  }

  function branchSwitcher(m, sibs) {
    if (sibs.length < 2) return null;
    const i = sibs.findIndex((s) => s.id === m.id);
    return h("div.branch", { role: "group", "aria-label": t("ui.branch.label", { i: i + 1, n: sibs.length }) },
      iconButton({ icon: "chevronLeft", label: t("ui.branch.prev"), size: "sm", disabled: i <= 0 || Boolean(app.state.turn), onClick: () => app.switchBranch(m.id, -1) }),
      h("span.branch__pos.num", `${i + 1}/${sibs.length}`),
      iconButton({ icon: "chevronRight", label: t("ui.branch.next"), size: "sm", disabled: i >= sibs.length - 1 || Boolean(app.state.turn), onClick: () => app.switchBranch(m.id, 1) }));
  }

  // ----------------------------------------------------------------------------------------------- assistant messages

  /** One assistant message; for the live turn the node keeps per-segment children and is patched by update(). */
  function assistantNode(vm, { live }) {
    const root = h("div", { class: ["msg", "msg--assistant", live && "is-live"], "data-msg": vm.id, "aria-busy": live ? "true" : null });
    const body = h("div.msg__body");
    const foot = h("div.msg__foot");
    root.append(body, foot);
    const segNodes = [];
    const cards = new Map();
    const perms = new Map();
    let govNodes = new Map();

    const ctx = () => messageContext(vm);

    const renderSegment = (seg, i, c) => {
      if (seg.type === "reasoning") {
        if (vm.hideReasoning) return null;
        const streaming = live && i === vm.segments.length - 1 && vm.status === "streaming";
        return thinkingBlock({ text: seg.text, durationMs: vm.thinkingMs?.[seg.step] ?? null, streaming, startedAt: vm.thinkingStart?.[seg.step] });
      }
      if (seg.type === "text") {
        const streaming = live && i === vm.segments.length - 1 && vm.status === "streaming";
        return proseNode(seg.text, c, streaming);
      }
      if (seg.type === "tools") return toolsNode(seg, c);
      return null;
    };

    const proseNode = (text, c, streaming) => {
      const node = h("div.prose.msg__prose", { lang: guessLang(text) },
        renderMarkdown(text, { streaming, onCitation: (id) => citationChip(c.cites.get(id) || null, { evidence: c.evidenceFor(id), onActivate: (cit) => cit && app.select({ tab: "evidence", kind: "evidence", id: cit.evidence_ref || cit.id, messageId: vm.id }) }) }));
      for (const head of node.querySelectorAll(".md-code-head")) {
        head.querySelector(".md-code-copy")?.before(h("button.md-code-copy", { type: "button", "data-wrap-code": "", "aria-pressed": "false" }, t("ui.code.wrap")));
      }
      return node;
    };

    function toolsNode(seg, c) {
      const wrap = h("div.msg__tools");
      for (const callId of seg.callIds || []) {
        const tool = vm.tools.get(callId);
        if (!tool) continue;
        const card = toolCallCard(tool.call, tool.envelope, {
          catalog: app.catalog, runnerUrl: app.state.settings.runner.url, compact: app.state.layout === "mobile",
          onOpen: (id) => app.select({ tab: "run", kind: "call", id, messageId: vm.id }),
        });
        cards.set(callId, card);
        wrap.append(card);
        if (tool.approval) {
          const p = permissionCard(tool.approval.request, { decided: tool.approval.decided, catalog: app.catalog, onDecide: (d) => tool.approval.resolve?.(d) });
          perms.set(callId, p);
          wrap.append(p);
        }
        // one card per job in a turn: job_status calls that follow it update that card instead of adding another
        const job = tool.envelope?.job;
        if (job?.id && firstCallOfJob(job.id) === callId) wrap.append(jobs.node(job));
      }
      const gov = governanceBlock(seg, c);
      if (gov) { govNodes.set(seg.step, gov); wrap.append(gov); }
      return wrap;
    }

    function firstCallOfJob(jobId) {
      for (const sg of vm.segments) {
        if (sg.type !== "tools") continue;
        for (const id of sg.callIds || []) if (vm.tools.get(id)?.envelope?.job?.id === jobId) return id;
      }
      return null;
    }

    function governanceBlock(seg, c) {
      const parts = [];
      for (const callId of seg.callIds || []) {
        const env = vm.tools.get(callId)?.envelope;
        if (!env || env.status !== "succeeded") continue;
        const claims = claimsOf(env);
        const ev = evidenceOf(env);
        const refused = claims.filter((x) => x.allowed === false);
        const shown = [...refused, ...claims.filter((x) => x.allowed !== false).slice(0, Math.max(0, 2 - refused.length))];
        for (const cl of shown) {
          parts.push(claimCard(cl, {
            evidence: ev, compact: true,
            onOpen: () => app.select({ tab: "claims", kind: "claim", id: `${callId}:${cl.id}`, messageId: vm.id }),
            onEvidence: (ref) => app.select({ tab: "evidence", kind: "evidence", id: ref, messageId: vm.id }),
          }));
        }
        if (claims.length > shown.length) {
          parts.push(button({ label: t("ui.thread.more_claims", { n: claims.length - shown.length }), size: "sm", variant: "quiet", onClick: () => app.select({ tab: "claims", messageId: vm.id }) }));
        }
        if (releaseOf(env)) parts.push(artifactCard(env, { compact: true, onOpen: () => app.select({ tab: "provenance", kind: "call", id: callId, messageId: vm.id }) }));
      }
      return parts.length ? h("div.msg__gov", parts) : null;
    }

    function renderFoot() {
      const items = [];
      if (live && vm.status === "streaming") {
        const running = [...vm.tools.values()].filter((x) => x.call.status === "running").length;
        const waiting = [...vm.tools.values()].some((x) => x.approval && !x.approval.decided);
        items.push(h("span.msg__status", spinner({ size: 12 }), waiting ? t("ui.thread.waiting") : running ? t("ui.thread.running_tools", { n: running }) : t("ui.thread.generating")));
        if (vm.retry) items.push(h("span.msg__retry", vm.retry));
      } else {
        items.push(attribution(vm));
        items.push(h("div.msg__actions",
          branchSwitcher({ id: vm.id }, siblingsOf(app.state.messages, vm.id)),
          iconButton({ icon: "copy", label: t("ui.msg.copy_md"), size: "sm", onClick: async () => toast((await copyText(answerMarkdown(app, vm.record || vm))) ? t("ui.toast.answer_copied") : t("ui.toast.copy_failed")) }),
          iconButton({ icon: "regenerate", label: t("ui.msg.regenerate"), size: "sm", disabled: Boolean(app.state.turn), onClick: () => app.regenerate(vm.id) }),
          vm.tools.size ? iconButton({ icon: "inspector", label: t("ui.action.open_inspector"), size: "sm", onClick: () => app.select({ tab: "run", messageId: vm.id }) }) : null));
      }
      fill(foot, ...items);
    }

    function renderNotices() {
      const out = [];
      if (vm.error) {
        const actions = vm.error.needsKey || /API Key|api key/i.test(vm.error.message)
          ? [button({ label: t("ui.thread.open_models"), size: "sm", onClick: () => app.navigate({ name: "settings", tab: "models" }) })]
          : !live ? [button({ label: t("ui.action.retry"), size: "sm", icon: "regenerate", onClick: () => app.regenerate(vm.id) })] : [];
        out.push(notice({ tone: "warn", title: t(`ui.thread.error.${vm.error.kind || "model"}`), body: vm.error.message, actions, role: "alert" }));
      }
      if (!live && vm.status === "stopped" && !vm.error) out.push(h("p.msg__note", icon("circleStop", { size: 12 }), t("core.agent.stopped")));
      if (vm.notice) out.push(h("p.msg__note", icon("info", { size: 12 }), vm.notice));
      if (vm.refusal) out.push(notice({ tone: "refusal", title: t("ui.thread.model_refused"), body: vm.refusal.explanation || "" }));
      if (vm.fallback) out.push(h("p.msg__note", icon("info", { size: 12 }), t("ui.thread.fallback", { model: vm.fallback.model || vm.fallback })));
      if (!vm.segments.length && live && vm.status === "streaming") out.push(h("div.msg__typing", h("span"), h("span"), h("span")));
      return out;
    }

    let noticeNodes = [];
    function fullRender() {
      const c = ctx();
      segNodes.length = 0;
      cards.clear();
      perms.clear();
      govNodes = new Map();
      const kids = [];
      vm.segments.forEach((seg, i) => {
        const n = renderSegment(seg, i, c);
        segNodes.push({ seg: { type: seg.type, step: seg.step, len: seg.text?.length || 0, calls: (seg.callIds || []).join(",") }, node: n });
        if (n) kids.push(n);
      });
      if (live) for (const p of vm.pending?.values?.() || []) kids.push(toolCallCard({ id: `pending-${p.step}-${p.name}`, name: p.name || "…", status: "pending" }, null, { catalog: app.catalog }));
      noticeNodes = renderNotices();
      fill(body, ...kids, ...noticeNodes);
      renderFoot();
    }

    /** Patch the live message: re-render only segments whose content changed, update cards in place. */
    root.update = () => {
      const c = ctx();
      const same = segNodes.length <= vm.segments.length && segNodes.every((s, i) => s.seg.type === vm.segments[i].type && s.seg.step === vm.segments[i].step);
      if (!same || (vm.pending?.size || 0) > 0 || body.querySelector(".tool-card[data-call^='pending-']")) { fullRender(); return; }
      vm.segments.forEach((seg, i) => {
        const prev = segNodes[i];
        if (!prev) {
          const n = renderSegment(seg, i, c);
          segNodes.push({ seg: { type: seg.type, step: seg.step, len: seg.text?.length || 0, calls: (seg.callIds || []).join(",") }, node: n });
          if (n) body.insertBefore(n, noticeNodes[0] || null);
          return;
        }
        if (seg.type === "text" && (prev.seg.len !== seg.text.length || i === vm.segments.length - 1)) {
          const n = renderSegment(seg, i, c);
          prev.node.replaceWith(n);
          prev.node = n;
          prev.seg.len = seg.text.length;
        } else if (seg.type === "reasoning" && prev.node) {
          prev.node.update?.({ text: seg.text, durationMs: vm.thinkingMs?.[seg.step] ?? null, streaming: live && i === vm.segments.length - 1 && vm.status === "streaming" });
        } else if (seg.type === "tools") {
          const calls = (seg.callIds || []).join(",");
          const changedApproval = (seg.callIds || []).some((id) => Boolean(vm.tools.get(id)?.approval) !== perms.has(id));
          if (calls !== prev.seg.calls || changedApproval) {
            const n = renderSegment(seg, i, c);
            prev.node.replaceWith(n);
            prev.node = n;
            prev.seg.calls = calls;
          } else {
            for (const id of seg.callIds || []) {
              const tool = vm.tools.get(id);
              cards.get(id)?.update(tool.call, tool.envelope);
              if (tool.approval && perms.has(id)) perms.get(id).setDecided(tool.approval.decided);
            }
            const gov = governanceBlock(seg, c);
            const old = govNodes.get(seg.step);
            if (gov && old) { old.replaceWith(gov); govNodes.set(seg.step, gov); }
            else if (gov && !old) { prev.node.append(gov); govNodes.set(seg.step, gov); }
          }
        }
      });
      const fresh = renderNotices();
      noticeNodes.forEach((n) => n.remove());
      noticeNodes = fresh;
      body.append(...fresh);
      renderFoot();
    };

    fullRender();
    return root;
  }

  // ----------------------------------------------------------------------------------------------- events

  const patchLive = rafThrottle(() => {
    if (!liveView || !liveView.isConnected) { renderAll(); return; }
    liveView.update();
    if (stick) scrollToBottom();
  });

  const offs = [
    app.on("messages", () => renderAll()),
    app.on("turn", (e) => {
      if (e.type === "start") { stick = true; renderAll(); }
      else if (e.type === "end") renderAll();
      else patchLive();
    }),
    app.on("edit-last", () => {
      const last = [...app.path()].reverse().find((m) => m.role === "user");
      if (last && !app.state.turn) { editing = last.id; renderAll(); }
    }),
    app.on("layout", () => renderAll()),
    app.on("catalog", () => renderAll()),
    app.on("runtime", (e) => { if (e?.kind === "runner") jobs.refresh(); }),
  ];
  renderAll();
  requestAnimationFrame(() => scrollToBottom());

  return {
    el: workspace,
    destroy() {
      offs.forEach((off) => off());
      composer.destroy();
      jobs.close();
    },
  };
}

// ------------------------------------------------------------------------------------------------- view models

/** A stored assistant message as the renderer's view model (the live turn already has this shape). */
export function viewOfStored(app, m) {
  const toolMsgs = app.toolMessagesOf(m.id);
  const byCall = new Map(toolMsgs.map((x) => [x.toolCallId, x]));
  const tools = new Map();
  for (const c of m.toolCalls || []) {
    const tm = byCall.get(c.id);
    const env = tm?.envelope || null;
    tools.set(c.id, { call: { id: c.id, name: c.name, args: c.args, where: env?.receipt?.where || tm?.where || null, status: env?.status || (m.status === "stopped" ? "cancelled" : "failed") }, envelope: env, approval: null });
  }
  let segments = Array.isArray(m.segments) && m.segments.length ? m.segments : null;
  if (!segments) {
    segments = [];
    if (m.reasoning) segments.push({ type: "reasoning", step: 1, text: m.reasoning });
    if (m.toolCalls?.length) segments.push({ type: "tools", step: 1, callIds: m.toolCalls.map((c) => c.id) });
    if (m.content) segments.push({ type: "text", step: 2, text: m.content });
  }
  return {
    id: m.id, record: m, segments, tools, status: m.status || "ok", error: m.error || null, notice: m.notice || "",
    model: m.model, provider: m.provider, servedBy: m.servedBy, fallback: m.fallback, refusal: m.refusal, hideReasoning: m.hideReasoning,
    thinkingMs: m.ui?.thinkingMs || {}, startedAt: m.ui?.startedAt || m.createdAt, endedAt: m.ui?.endedAt || null, usage: m.usage || null,
    approvals: m.ui?.approvals || {}, createdAt: m.createdAt,
  };
}

/**
 * What a message's citations point at: [E#] resolved against the envelopes of the same turn, in call order; when two
 * results both number an E1, the later one wins (the model answers after the latest result).
 */
export function messageContext(vm) {
  const cites = new Map();
  const evidence = new Map();
  const envelopes = [];
  for (const seg of vm.segments) {
    if (seg.type !== "tools") continue;
    for (const id of seg.callIds || []) {
      const env = vm.tools.get(id)?.envelope;
      if (!env) continue;
      envelopes.push({ callId: id, env });
      const evs = evidenceOf(env);
      const byRef = new Map(evs.map((e) => [e.id, e]));
      for (const c of citationsOf(env)) {
        cites.set(c.id, c);
        if (c.evidence_ref && byRef.has(c.evidence_ref)) evidence.set(c.id, byRef.get(c.evidence_ref));
      }
    }
  }
  return { cites, envelopes, evidenceFor: (id) => evidence.get(id) || null };
}

function attribution(vm) {
  const model = vm.provider === "tao" ? "Tao-S1" : vm.model || vm.provider || "";
  const n = vm.tools.size;
  const dur = vm.endedAt && vm.startedAt ? vm.endedAt - vm.startedAt : null;
  const bits = [h("span.msg__model", { "data-tip": vm.usage ? t("ui.thread.usage", { input: vm.usage.input, output: vm.usage.output }) : null }, model)];
  if (vm.servedBy && vm.servedBy !== vm.model && vm.provider !== "tao") bits.push(h("span", t("ui.thread.served_by", { model: vm.servedBy })));
  if (n) bits.push(h("span", t("ui.thread.tool_count", { n })));
  if (dur) bits.push(h("span.num", formatDuration(dur)));
  return h("p.msg__attr", bits.flatMap((b, i) => (i ? [h("span.dot-sep", "·"), b] : [b])));
}

/** The answer as Markdown with its citations listed, for "copy" (DESIGN §3.2). */
export function answerMarkdown(app, m) {
  const vm = m.segments && m.tools instanceof Map ? m : viewOfStored(app, m);
  const text = vm.segments.filter((s) => s.type === "text").map((s) => s.text).join("\n\n") || m.content || "";
  const { cites } = messageContext(vm);
  const used = [...new Set([...text.matchAll(/E\d{1,4}/g)].map((x) => x[0]))].filter((id) => cites.has(id));
  if (!used.length) return text;
  const lines = used.map((id) => {
    const c = cites.get(id);
    return `- [${id}] ${c.label || ""}${c.url ? ` <${c.url}>` : ""}`;
  });
  return `${text}\n\n${lang() === "zh" ? "引用" : "Citations"}:\n${lines.join("\n")}`;
}

function guessLang(text) {
  const s = String(text || "");
  const cjk = (s.match(/[㐀-鿿]/g) || []).length;
  return cjk > s.length * 0.08 ? "zh-Hans" : "en";
}

// ------------------------------------------------------------------------------------------------- jobs

/** Follows runner jobs that the thread shows (events when connected); a job is pending until collected. */
class JobTracker {
  constructor(app) {
    this.app = app;
    this.jobs = new Map(); // id → {job, files, logs, close, fetched}
    this.slots = new Map(); // id → Set of slot elements in the thread
  }

  /** A slot that shows the job's card and is re-filled whenever the job changes. */
  node(stub) {
    let rec = this.jobs.get(stub.id);
    if (!rec) {
      rec = { job: { ...stub }, files: null, logs: [], close: null, fetched: false };
      this.jobs.set(stub.id, rec);
      this.#follow(stub.id);
    }
    const slot = h("div.job-slot", { "data-job": stub.id });
    if (!this.slots.has(stub.id)) this.slots.set(stub.id, new Set());
    this.slots.get(stub.id).add(slot);
    this.#fill(slot, rec);
    return slot;
  }

  #fill(slot, rec) {
    const runner = this.app.runtimes.runner;
    const connected = this.app.state.runner.status === "ready";
    const card = jobCard(rec.job, {
      files: rec.files, logs: rec.logs,
      onCancel: connected ? async (job) => {
        try { rec.job = { ...rec.job, ...(await runner.jobs.cancel(job.id)) }; this.#changed(job.id); } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
      } : null,
      onOpenFile: (path) => this.app.select({ tab: "files", kind: "jobfile", id: `${rec.job.id}/${path}` }),
    });
    if (!connected && !["succeeded", "failed", "cancelled"].includes(rec.job.state)) {
      card.append(h("p.job__pending", icon("plug", { size: 12 }), t("ui.job.connect_to_follow")));
    }
    fill(slot, card);
  }

  #changed(id) {
    const rec = this.jobs.get(id);
    for (const slot of [...(this.slots.get(id) || [])]) {
      if (!slot.isConnected) { this.slots.get(id).delete(slot); continue; }
      this.#fill(slot, rec);
    }
    this.app.emit("job", rec.job);
  }

  async #follow(id) {
    const runner = this.app.runtimes.runner;
    if (!runner || this.app.state.runner.status !== "ready") return;
    const rec = this.jobs.get(id);
    try {
      rec.job = { ...rec.job, ...(await runner.jobs.get(id)) };
      rec.fetched = true;
      if (rec.job.state === "succeeded") rec.files = (await runner.jobs.files(id))?.files || null;
      this.#changed(id);
    } catch { return; }
    if (["succeeded", "failed", "cancelled"].includes(rec.job.state) || rec.close) return;
    rec.close = runner.jobs.events(id, async (ev) => {
      const data = ev?.data || {};
      if (ev.type === "state" || ev.type === "done") rec.job = { ...rec.job, ...(data.job || data) };
      else if (ev.type === "progress") rec.job = { ...rec.job, progress: data };
      else if (ev.type === "log") rec.logs.push(typeof data === "string" ? data : data.line || JSON.stringify(data));
      if (ev.type === "done" || ["succeeded", "failed", "cancelled"].includes(rec.job.state)) {
        rec.close?.();
        rec.close = null;
        if (rec.job.state === "succeeded") {
          try { rec.files = (await runner.jobs.files(id))?.files || null; } catch { /* listed later */ }
        }
      }
      this.#changed(id);
    });
  }

  refresh() {
    for (const [id, rec] of this.jobs) {
      if (!rec.fetched) this.#follow(id);
      else this.#changed(id);
    }
  }

  close() {
    for (const rec of this.jobs.values()) rec.close?.();
    this.jobs.clear();
    this.slots.clear();
  }
}
