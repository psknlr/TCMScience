// The governance inspector (DESIGN §4.4): 过程 Run · 证据 Evidence · 主张 Claims · 文件 Files · 溯源 Provenance, for
// the selected assistant turn (or the whole branch). Docked at ≥1280 px with a resize handle (role=separator), a
// drawer below that, a bottom sheet on phones. Selecting in the thread selects here, scrolls and highlights for 1.2 s;
// clicking a row here scrolls the thread to the message that produced it.

import { claimsOf, evidenceOf, refusalsOf, releaseOf, sourceView } from "../core/governance.js";
import { glossary, lang, t } from "../core/i18n.js";
import { canonicalJson } from "../core/util.js";
import { clamp, debounce, fill, formatBytes, formatDateTime, formatDuration, h, rafThrottle } from "./dom.js";
import { viewOfStored } from "./conversation.js";
import { filePreview, jsonTree } from "./files.js";
import {
  artifactCard, auditHeadView, candidateChip, claimCard, compositeVersionView, evidenceGroups, evidenceTable, hashBadge, limitationsBlock, reasonList,
  releaseChecklist, sourceCard, verdictPill,
} from "./governance.js";
import { icon } from "./icons.js";
import { button, chip, copyButton, emptyState, iconButton, keyValue, segmented, tabList } from "./primitives.js";
import { argsSummary, callStateView, deviceLabel, envelopeSummary, isDeclined, toolTitle, whereBadge } from "./toolcards.js";

export const TABS = ["run", "evidence", "claims", "files", "provenance"];
const TAB_ICONS = { run: "activity", evidence: "bookText", claims: "scale", files: "files", provenance: "fingerprint" };

export function mountInspector(app, host) {
  let evidenceView = "list";
  let claimFilter = "all";
  let provCall = null;
  let filePick = null;
  let sheetFull = false;

  const tabs = tabList({
    label: t("ui.inspector.tabs"), value: app.state.inspector.tab, idPrefix: "insp", variant: "line", className: "inspector__tabs",
    tabs: TABS.map((id) => ({ id, label: t(`ui.inspector.tab.${id}`), icon: TAB_ICONS[id], tip: `${t(`ui.inspector.tab.${id}`)}  Alt+${TABS.indexOf(id) + 1}` })),
    onChange: (id) => app.select({ tab: id }),
  });
  // the tab strip scrolls when the five labels do not fit: an edge fade says so, and the selected tab is kept in view
  const syncTabsOverflow = () => {
    tabs.classList.toggle("is-overflowing", tabs.scrollWidth > tabs.clientWidth + 1);
    // keep the selected tab in view by scrolling the strip itself (scrollIntoView could scroll the page too)
    const sel = tabs.querySelector('[aria-selected="true"]');
    if (sel && tabs.scrollWidth > tabs.clientWidth) {
      const a = tabs.getBoundingClientRect();
      const b = sel.getBoundingClientRect();
      if (b.left < a.left) tabs.scrollLeft -= a.left - b.left + 4;
      else if (b.right > a.right) tabs.scrollLeft += b.right - a.right + 24;
    }
  };
  if (typeof ResizeObserver === "function") new ResizeObserver(() => syncTabsOverflow()).observe(tabs);
  const scope = h("div.inspector__scope");
  const panel = h("div.inspector__panel", { role: "tabpanel", tabindex: "0" });
  const handle = h("div.inspector__resize", { role: "separator", "aria-orientation": "vertical", "aria-label": t("ui.inspector.resize"), tabindex: "0", "aria-valuemin": "320", "aria-valuemax": "720" });
  const grip = h("button.inspector__grip", { type: "button", "aria-label": t("ui.inspector.expand"), onClick: () => { sheetFull = !sheetFull; host.classList.toggle("is-full", sheetFull); grip.setAttribute("aria-label", sheetFull ? t("ui.inspector.collapse") : t("ui.inspector.expand")); } }, h("span"));
  const head = h("div.inspector__head",
    grip,
    h("div.inspector__bar", tabs,
      iconButton({ icon: "x", label: t("ui.inspector.close"), size: "sm", className: "inspector__close", onClick: () => app.toggleInspector(false) })),
    scope);
  fill(host, handle, head, panel);

  // ----------------------------------------------------------------------------------------------- resize

  const setWidth = (w) => {
    const v = clamp(Math.round(w), 320, 720);
    document.documentElement.style.setProperty("--inspector-w", `${v}px`);
    handle.setAttribute("aria-valuenow", String(v));
    saveWidth(v);
  };
  const saveWidth = debounce((v) => app.setSetting({ inspectorWidth: v }), 400);
  handle.setAttribute("aria-valuenow", String(app.state.settings.inspectorWidth || 400));
  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    document.documentElement.classList.add("is-resizing");
    const move = (ev) => setWidth(window.innerWidth - ev.clientX);
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      document.documentElement.classList.remove("is-resizing");
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
  handle.addEventListener("keydown", (e) => {
    const cur = Number(handle.getAttribute("aria-valuenow")) || 400;
    if (e.key === "ArrowLeft") { e.preventDefault(); setWidth(cur + 16); }
    if (e.key === "ArrowRight") { e.preventDefault(); setWidth(cur - 16); }
    if (e.key === "Home") { e.preventDefault(); setWidth(320); }
    if (e.key === "End") { e.preventDefault(); setWidth(720); }
  });

  // ----------------------------------------------------------------------------------------------- data

  /** The turns in scope, each {vm, user, envelopes: [{callId, env, call}]}. */
  function turns() {
    const path = app.path();
    const live = app.state.turn && app.state.turn.conversationId === app.state.conversation?.id ? app.state.turn.live : null;
    const all = [];
    for (let i = 0; i < path.length; i++) {
      const m = path[i];
      if (m.role !== "assistant" || (live && m.id === live.id)) continue;
      all.push({ vm: viewOfStored(app, m), user: path[i - 1]?.role === "user" ? path[i - 1] : null });
    }
    if (live) all.push({ vm: live, user: app.state.messages.find((m) => m.id === live.userId) || null, live: true });
    for (const tr of all) {
      tr.envelopes = [];
      for (const seg of tr.vm.segments) {
        if (seg.type !== "tools") continue;
        for (const id of seg.callIds || []) {
          const tool = tr.vm.tools.get(id);
          if (tool) tr.envelopes.push({ callId: id, env: tool.envelope, call: tool.call, approval: tool.approval || (tr.vm.approvals?.[id] ? { decided: tr.vm.approvals[id] } : null) });
        }
      }
    }
    const insp = app.state.inspector;
    if (insp.scope === "all") return all;
    const sel = all.find((x) => x.vm.id === insp.messageId) || [...all].reverse().find((x) => x.envelopes.length) || all[all.length - 1];
    return sel ? [sel] : [];
  }

  // ----------------------------------------------------------------------------------------------- render

  function render() {
    const insp = app.state.inspector;
    tabs.select(insp.tab);
    panel.id = tabs.panelId(insp.tab);
    panel.setAttribute("aria-labelledby", tabs.tabId(insp.tab));
    const tr = turns();
    const envs = tr.flatMap((x) => x.envelopes.map((e) => ({ ...e, vm: x.vm })));
    const done = envs.filter((e) => e.env);
    tabs.setCount("run", envs.length);
    tabs.setCount("evidence", done.reduce((n, e) => n + evidenceOf(e.env).length, 0));
    tabs.setCount("claims", done.reduce((n, e) => n + claimsOf(e.env).length, 0));
    tabs.setCount("files", done.reduce((n, e) => n + (e.env.governance?.outputs?.length || 0), 0));
    requestAnimationFrame(syncTabsOverflow);
    fill(scope, 
      segmented({
        label: t("ui.inspector.scope"), size: "sm", value: insp.scope,
        options: [{ value: "turn", label: t("ui.inspector.scope_turn") }, { value: "all", label: t("ui.inspector.scope_all") }],
        onChange: (v) => { app.state.inspector = { ...app.state.inspector, scope: v }; render(); },
      }),
      tr.length === 1 && insp.scope === "turn" && tr[0].user ? h("button.inspector__turn", { type: "button", "data-tip": t("ui.inspector.goto_turn"), onClick: () => app.revealInThread({ messageId: tr[0].vm.id }) },
        icon("message", { size: 12 }), h("span", clip(tr[0].user.content, 40))) : null);
    let content;
    switch (insp.tab) {
      case "evidence": content = evidenceTab(done); break;
      case "claims": content = claimsTab(done); break;
      case "files": content = filesTab(done); break;
      case "provenance": content = provenanceTab(done); break;
      default: content = runTab(tr);
    }
    // 「此回答」 is empty but the conversation is not: say so, one click from the whole conversation
    if (content?.classList?.contains("empty") && insp.scope === "turn" && insp.tab !== "files") {
      const n = countInConversation(insp.tab);
      if (n > 0) content = emptyState({
        icon: TAB_ICONS[insp.tab], title: t(`ui.inspector.empty_turn.${insp.tab}`, { n }),
        actions: [button({ label: t("ui.inspector.show_all"), size: "sm", onClick: () => { app.state.inspector = { ...app.state.inspector, scope: "all" }; render(); } })],
      });
    }
    const keep = panel.scrollTop;
    fill(panel, content);
    panel.scrollTop = keep;
    highlightSelection();
  }

  /** How many items of a tab the whole conversation holds (for the empty 「此回答」 view). */
  function countInConversation(tab) {
    const saved = app.state.inspector.scope;
    app.state.inspector.scope = "all";
    const all = turns();
    app.state.inspector.scope = saved;
    const envs = all.flatMap((x) => x.envelopes).filter((e) => e.env);
    if (tab === "evidence") return envs.reduce((n, e) => n + evidenceOf(e.env).length, 0);
    if (tab === "claims") return envs.reduce((n, e) => n + claimsOf(e.env).length, 0);
    if (tab === "provenance") return envs.length;
    if (tab === "run") return all.reduce((n, x) => n + x.vm.segments.length, 0);
    return 0;
  }

  function highlightSelection() {
    const sel = app.state.inspector.selection;
    if (!sel || !sel.id || Date.now() - sel.at > 1500) return;
    const attr = { evidence: "data-evidence", claim: "data-claim-key", call: "data-call-row", source: "data-source", jobfile: "data-file" }[sel.kind];
    if (!attr) return;
    const el = panel.querySelector(`[${attr}="${CSS.escape(sel.id)}"]`) || (sel.kind === "evidence" ? panel.querySelector(`[data-cite-id="${CSS.escape(sel.id)}"]`) : null);
    if (!el) return;
    requestAnimationFrame(() => {
      el.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      el.classList.remove("flash");
      void el.offsetWidth;
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1300);
    });
  }

  // ----------------------------------------------------------------------------------------------- 过程 Run

  function runTab(tr) {
    if (!tr.length || !tr.some((x) => x.vm.segments.length)) return emptyState({ icon: "activity", title: t("ui.inspector.empty.run_title"), body: t("ui.inspector.empty.run") });
    return h("div.run", tr.map((x, k) => {
      const rows = [];
      let n = 0;
      for (const seg of x.vm.segments) {
        if (seg.type === "reasoning" && !x.vm.hideReasoning) {
          n++;
          const ms = x.vm.thinkingMs?.[seg.step];
          // under a second is not shown as a number (a reasoning block that arrived at once is not 「0 毫秒」 of thought)
          rows.push(runRow({ n, icon: "brain", name: t("ui.run.thinking"), kind: t("ui.run.kind.model"), duration: ms >= 1000 ? ms : null, status: null, note: t("ui.run.thinking_note") }));
        } else if (seg.type === "tools") {
          for (const id of seg.callIds || []) {
            const e = x.envelopes.find((y) => y.callId === id);
            if (!e) continue;
            if (e.approval?.decided || e.approval?.request) {
              n++;
              const d = e.approval.decided;
              // the person's decline is theirs: slate, not the kernel's vermilion ✕ (DESIGN §7.1 #4)
              rows.push(runRow({ n, icon: "shieldCheck", name: t("ui.run.approval"), kind: t("ui.run.kind.person"), status: d ? { tone: d === "deny" ? "slate" : "jade", icon: d === "deny" ? "○" : "✓", label: t(`ui.perm.decided.${d}`) } : { tone: "ochre", icon: "?", label: t("ui.perm.title") } }));
            }
            n++;
            rows.push(toolRow(n, e, x.vm));
            const rel = e.env ? releaseOf(e.env) : null;
            if (rel) {
              n++;
              rows.push(runRow({ n, icon: "shield", name: t("ui.run.gate"), kind: t("ui.run.kind.kernel"), status: rel.badge, note: rel.summary }));
            }
          }
        } else if (seg.type === "text") {
          n++;
          rows.push(runRow({ n, icon: "message", name: t("ui.run.answer"), kind: t("ui.run.kind.model"), note: x.vm.provider === "tao" ? "Tao-S1" : x.vm.model || "" }));
        }
      }
      const total = x.vm.endedAt && x.vm.startedAt ? x.vm.endedAt - x.vm.startedAt : null;
      return h("section.run__turn",
        tr.length > 1 ? h("button.run__turn-head", { type: "button", onClick: () => app.revealInThread({ messageId: x.vm.id }) }, h("span.run__turn-n", `#${k + 1}`), h("span", clip(x.user?.content || "", 48))) : null,
        h("ol.run__list", { role: "list" }, rows),
        h("p.run__total.muted", [
          x.live ? t("ui.run.live") : total ? t("ui.run.total", { d: formatDuration(total) }) : null,
          x.vm.usage ? t("ui.run.tokens", { input: x.vm.usage.input, output: x.vm.usage.output }) : null,
        ].filter(Boolean).join(" · ")));
    }));
  }

  function runRow({ n, icon: ic, name, kind, duration, status, note, noteLang = null, where, hashes, onClick, key, mono }) {
    // a clickable row's action is a real button (its name); a click anywhere else on the row is a pointer shortcut
    const nameNode = onClick
      ? h("button.run__link", { type: "button", "data-tip": t("ui.inspector.goto_turn"), onClick: (e) => { e.stopPropagation(); onClick(); } }, h("span", { class: mono ? "mono" : null }, name))
      : h("span", { class: mono ? "mono" : null }, name);
    return h("li", { class: ["run__row", onClick && "is-clickable"], "data-call-row": key || null, onClick: onClick ? (e) => { if (!e.target.closest?.("button, a")) onClick(); } : null },
      h("span.run__n.num", String(n)),
      h("span.run__icon", icon(ic, { size: 14 })),
      h("div.run__main",
        h("p.run__name", nameNode, kind ? h("span.run__kind", kind) : null),
        note ? h("p.run__note", { lang: noteLang }, note) : null,
        where || hashes ? h("div.run__meta", where, hashes) : null),
      h("div.run__side",
        status ? verdictPill(status, { size: "sm" }) : null,
        duration !== undefined && duration !== null ? h("span.run__dur.num", formatDuration(duration)) : null));
  }

  function toolRow(n, e, vm) {
    const env = e.env;
    const { title, id } = toolTitle(e.call.name, app.catalog, e.call.args);
    const status = env?.status || e.call.status;
    const r = env?.receipt || {};
    const declined = isDeclined(env);
    return runRow({
      n, icon: "wrench", name: title === id ? id : title, mono: title === id, key: e.callId,
      // a declined call never ran: it is the person's row (你), not the system's
      kind: declined ? t("ui.run.kind.person")
        : env?.governance?.kind ? `${t(`ui.run.gov.${env.governance.kind}`)}${r.skill_pinned === false ? ` · ${t("ui.artifact.candidate_short")}` : ""}` : t("ui.run.kind.tool"),
      note: envelopeSummary(env).text || argsSummary(e.call.args, 90),
      noteLang: envelopeSummary(env).text ? envelopeSummary(env).lang : null,
      status: glossaryCall(status, env),
      duration: env ? env.duration_ms : null,
      where: r.decided_by === "router" ? h("span.where.where--none", t("ui.where.not_run")) : whereBadge(r, { where: e.call.where, runnerUrl: app.state.settings.runner.url }),
      hashes: [r.input_sha256 ? hashBadge(r.input_sha256, { compact: true, label: t("ui.tool.input") }) : null, r.output_sha256 ? hashBadge(r.output_sha256, { compact: true, label: t("ui.tool.output") }) : null],
      onClick: () => app.revealInThread({ callId: e.callId, messageId: vm.id }),
    });
  }

  function glossaryCall(status, env) {
    const map = {
      succeeded: ["neutral", "✓"], failed: ["warning", "!"], refused: ["refused", "✕"], needs_approval: ["ochre", "?"], job_submitted: ["navy", "◷"],
      cancelled: ["slate", "■"], running: ["navy", "…"], pending: ["navy", "…"], declined: ["slate", "○"], interrupted: ["slate", "○"], clinic_draft: ["caveat", "✎"],
    };
    const view = callStateView(status, env);
    const [tone, ic] = map[view.key] || map[status] || ["neutral", "○"];
    return { tone, icon: ic, label: view.label };
  }

  // ----------------------------------------------------------------------------------------------- 证据 Evidence

  function evidenceTab(envs) {
    const items = [];
    const seen = new Set();
    for (const e of envs) {
      const evs = evidenceOf(e.env);
      const ctxCites = new Map((e.env.citations || []).map((c) => [c.evidence_ref, c.id]));
      evs.forEach((ev, i) => {
        const key = `${ev.id}|${ev.content_hash}`;
        if (seen.has(key)) return;
        seen.add(key);
        items.push({ ...ev, citeId: ctxCites.get(ev.id) || `E${i + 1}` });
      });
    }
    if (!items.length) return emptyState({ icon: "bookText", title: t("ui.inspector.empty.evidence_title"), body: t("ui.inspector.empty.evidence") });
    const sel = app.state.inspector.selection;
    const selectedId = sel?.kind === "evidence" ? sel.id : null;
    const bar = h("div.inspector__toolbar",
      h("p.inspector__count", t("ui.evidence.count", { n: items.length })),
      segmented({ label: t("ui.evidence.view"), size: "sm", value: evidenceView, options: [{ value: "list", icon: "list", ariaLabel: t("ui.evidence.list"), title: t("ui.evidence.list") }, { value: "table", icon: "table", ariaLabel: t("ui.evidence.table"), title: t("ui.evidence.table") }], onChange: (v) => { evidenceView = v; render(); } }));
    const body = evidenceView === "table"
      ? evidenceTable(items, { selectedId, onSelect: (id) => app.select({ tab: "evidence", kind: "evidence", id }) })
      : evidenceGroups(items, { selectedId, onSource: (s) => { provCall = null; app.select({ tab: "provenance", kind: "source", id: s.id }); } });
    for (const node of body.querySelectorAll("[data-evidence]")) {
      const ev = items.find((x) => x.id === node.getAttribute("data-evidence"));
      if (ev) node.setAttribute("data-cite-id", ev.citeId);
    }
    return h("div.ev-tab", bar, h("p.inspector__note", icon("info", { size: 12 }), t("ui.evidence.note")), body);
  }

  // ----------------------------------------------------------------------------------------------- 主张 Claims

  function claimsTab(envs) {
    const all = [];
    // a job's governed result is in every job_status poll after success: its claims are listed once, from the last
    const lastOfJob = new Map();
    for (const e of envs) if (e.env?.status === "succeeded" && e.env.job?.id) lastOfJob.set(e.env.job.id, e.callId);
    for (const e of envs) {
      const jobId = e.env?.job?.id;
      if (jobId && lastOfJob.has(jobId) && lastOfJob.get(jobId) !== e.callId) continue;
      for (const c of claimsOf(e.env)) all.push({ c, e, ev: evidenceOf(e.env) });
    }
    if (!all.length) return emptyState({ icon: "scale", title: t("ui.inspector.empty.claims_title"), body: t("ui.inspector.empty.claims") });
    const tests = {
      all: () => true,
      allowed: (x) => x.c.allowed === true,
      refused: (x) => x.c.allowed === false,
      declaration: (x) => x.c.needs_declaration,
      extrapolation: (x) => x.c.extrapolations.length > 0,
    };
    const counts = Object.fromEntries(Object.entries(tests).map(([k, f]) => [k, all.filter(f).length]));
    const list = all.filter(tests[claimFilter] || tests.all);
    const sel = app.state.inspector.selection;
    return h("div.claims-tab",
      h("div.inspector__toolbar.inspector__toolbar--wrap",
        h("div.filter-chips", { role: "radiogroup", "aria-label": t("ui.claims.filter") }, Object.keys(tests).map((k) => h("button", {
          type: "button", role: "radio", "aria-checked": String(claimFilter === k), class: ["filter-chip", claimFilter === k && "is-on"],
          onClick: () => { claimFilter = k; render(); },
        }, t(`ui.claims.filter.${k}`), h("span.filter-chip__n.num", String(counts[k])))))),
      h("p.inspector__note", icon("info", { size: 12 }), t("ui.claims.note")),
      list.length ? h("div.stack", list.map(({ c, e, ev }) => {
        const key = `${e.callId}:${c.id}`;
        const card = claimCard(c, { evidence: ev, selected: sel?.kind === "claim" && sel.id === key, onEvidence: (ref) => app.select({ tab: "evidence", kind: "evidence", id: ref }) });
        card.setAttribute("data-claim-key", key);
        return card;
      })) : h("p.muted.small", t("ui.claims.none_for_filter")));
  }

  // ----------------------------------------------------------------------------------------------- 文件 Files

  function filesTab(envs) {
    const wrap = h("div.files-tab");
    const outputs = [];
    const jobsSeen = new Set(); // a job followed by job_status is in several envelopes: list its files once
    for (const e of envs) {
      const rel = releaseOf(e.env);
      const verified = Boolean(rel?.states.find((s) => s.id === "outputs_verified")?.ok);
      for (const o of e.env.governance?.outputs || []) outputs.push({ ...o, verified, where: e.env.receipt?.where, callId: e.callId, key: `${e.callId}/${o.path}` });
      const job = e.env.job;
      if (job?.id && !jobsSeen.has(job.id)) {
        jobsSeen.add(job.id);
        outputs.push({ path: job.id, job: true, where: "runner", key: `job:${job.id}`, jobId: job.id });
      }
    }
    const inputs = h("div.files-inputs", h("p.muted.small", t("ui.files.loading")));
    app.store.files.list(app.state.project?.id).then((files) => {
      fill(inputs, files.length ? h("ul.file-list", { role: "list" }, files.map((f) => fileRow({
        name: f.name, type: f.type, bytes: f.bytes, sha256: f.sha256, location: f.runnerUploadId ? t("ui.files.loc_both") : t("ui.files.loc_browser"), key: `file:${f.id}`,
        onPreview: () => { filePick = { key: `file:${f.id}`, source: { blob: f.blob }, meta: { name: f.name, media_type: f.type } }; render(); },
        onDownload: () => downloadBlob(f.blob, f.name),
      }))) : h("p.muted.small", t("ui.files.no_inputs")));
    }).catch(() => fill(inputs, h("p.muted.small", t("ui.files.no_inputs"))));
    const outList = outputs.length ? h("ul.file-list", { role: "list" }, outputs.filter((o) => !o.job).map((o) => fileRow({
      name: o.path, type: o.media_type, bytes: o.bytes, sha256: o.sha256, verified: o.verified,
      location: o.where === "runner" ? t("ui.files.loc_runner") : t("ui.files.loc_browser"), key: o.key,
      onPreview: o.content !== undefined && o.content !== null ? () => { filePick = { key: o.key, source: { json: o.content }, meta: { name: o.path, media_type: o.media_type } }; render(); } : null,
      onDownload: o.content !== undefined && o.content !== null ? () => downloadBlob(new Blob([JSON.stringify(o.content, null, 2)], { type: o.media_type || "application/json" }), o.path) : null,
    }))) : h("p.muted.small", t("ui.files.no_outputs"));
    const jobFiles = outputs.filter((o) => o.job).map((o) => jobFilesBlock(o.jobId));
    wrap.append(
      h("section.files-sec", h("h3.files-sec__title", t("ui.files.inputs")), h("p.inspector__note", icon("lock", { size: 12 }), t("ui.files.inputs_note")), inputs),
      h("section.files-sec", h("h3.files-sec__title", t("ui.files.outputs")), outList, jobFiles));
    if (filePick) {
      wrap.append(h("section.files-sec.files-preview",
        h("div.files-preview__head", h("h3.files-sec__title.mono", filePick.meta.name), iconButton({ icon: "x", label: t("ui.action.close"), size: "sm", onClick: () => { filePick = null; render(); } })),
        filePreview(filePick.source, filePick.meta)));
    }
    return wrap;
  }

  function jobFilesBlock(jobId) {
    const runner = app.runtimes.runner;
    const box = h("div.job-files", { "data-file": jobId }, h("p.files-sec__sub", icon("workflow", { size: 12 }), t("ui.files.job", { id: jobId })));
    if (app.state.runner.status !== "ready") { box.append(h("p.muted.small", t("ui.job.connect_to_follow"))); return box; }
    runner.jobs.get(jobId).then(async (job) => {
      if (job.state !== "succeeded") { box.append(h("p.muted.small", t("ui.job.pending_note"))); return; }
      const files = (await runner.jobs.files(jobId))?.files || [];
      box.append(h("ul.file-list", { role: "list" }, files.map((f) => fileRow({
        // the runner checked the digest recorded at collection: the file is unchanged, which is not a release
        name: f.path, type: f.media_type, bytes: f.bytes, sha256: f.sha256, digest: true, location: t("ui.files.loc_runner"), key: `${jobId}/${f.path}`,
        onPreview: () => { filePick = { key: `${jobId}/${f.path}`, source: { url: runner.fileUrl(jobId, f.path) }, meta: { name: f.path, media_type: f.media_type } }; render(); },
        onDownload: () => { const a = h("a", { href: runner.fileUrl(jobId, f.path), download: f.path.split("/").pop() }); document.body.append(a); a.click(); a.remove(); },
      }))));
    }).catch((err) => box.append(h("p.muted.small", err?.message || String(err))));
    return box;
  }

  function fileRow({ name, type, bytes, sha256, location, verified, digest = false, key, onPreview, onDownload }) {
    const picked = filePick?.key === key;
    return h("li", { class: ["file-row", picked && "is-picked"], "data-file": key },
      icon(/json/.test(type || name) ? "fileJson" : /image/.test(type || "") ? "image" : /csv|tsv|sheet/.test((type || "") + name) ? "fileTable" : /html/.test(type || name) ? "fileCode" : "file", { size: 16, className: "file-row__icon" }),
      h("div.file-row__main",
        h("p.file-row__name", name),
        h("p.file-row__meta", [type || "", bytes !== undefined && bytes !== null ? formatBytes(bytes) : "", location].filter(Boolean).join(" · ")),
        h("div.file-row__badges", sha256 ? hashBadge(sha256, { compact: true }) : null,
          verified ? chip({ label: t("ui.artifact.output_verified"), tone: "jade", icon: "check" })
            : digest ? chip({ label: t("ui.files.digest_checked"), tone: "slate", icon: "check", title: t("ui.files.digest_checked_tip") }) : null)),
      h("div.file-row__actions",
        onPreview ? iconButton({ icon: "eye", label: t("ui.files.preview"), size: "sm", onClick: onPreview }) : null,
        onDownload ? iconButton({ icon: "download", label: t("ui.files.download"), size: "sm", onClick: onDownload }) : null));
  }

  // ----------------------------------------------------------------------------------------------- 溯源 Provenance

  function provenanceTab(envs) {
    const withEnv = envs.filter((e) => e.env);
    if (!withEnv.length) return emptyState({ icon: "fingerprint", title: t("ui.inspector.empty.prov_title"), body: t("ui.inspector.empty.prov") });
    const sel = app.state.inspector.selection;
    if (sel?.kind === "call" && withEnv.some((e) => e.callId === sel.id)) provCall = sel.id;
    const governed = withEnv.filter((e) => releaseOf(e.env));
    const pick = withEnv.find((e) => e.callId === provCall) || governed[governed.length - 1] || withEnv[withEnv.length - 1];
    const env = pick.env;
    const r = env.receipt || {};
    const rel = releaseOf(env);
    const art = env.governance?.artifact;
    const sources = (art?.sources || []).map(sourceView);
    const selector = withEnv.length > 1 ? h("div.prov-pick", { role: "radiogroup", "aria-label": t("ui.prov.pick") }, withEnv.map((e) => {
      const { title } = toolTitle(e.call.name, app.catalog, e.call.args);
      const on = e === pick;
      return h("button", { type: "button", role: "radio", "aria-checked": String(on), class: ["prov-pick__item", on && "is-on"], onClick: () => { provCall = e.callId; render(); } },
        icon(releaseOf(e.env) ? "box" : "wrench", { size: 12 }), h("span", title));
    })) : null;
    const vm = pick.vm;
    const modelLabel = vm.provider === "tao" ? "Tao-S1" : vm.model || vm.provider || "";
    const parts = [
      selector,
      h("section.prov-sec", h("h3.prov-sec__title", t("ui.prov.run")),
        keyValue([
          [t("ui.prov.tool"), h("code.kv__code", env.via && env.via !== env.tool ? `${env.tool} → ${env.via}` : env.tool)],
          [t("ui.prov.model"), modelLabel],
          [t("ui.prov.where"), r.decided_by === "router" ? t("ui.where.decided_in_page") : [r.where === "runner" ? t("ui.where.runner") : t("ui.where.browser"), r.where === "runner" ? hostOf(app.state.settings.runner.url) : null].filter(Boolean).join(" @")],
          [t("ui.prov.device"), deviceLabel(r.device) || "—"],
          [t("ui.prov.runtime"), r.runtime || "—"],
          [t("ui.prov.versions"), r.versions && Object.keys(r.versions).length ? h("code.kv__code", Object.entries(r.versions).map(([k, v]) => `${k} ${v}`).join(" · ")) : "—"],
          [t("ui.prov.started"), r.started_at ? h("time", { datetime: r.started_at }, formatDateTime(r.started_at)) : "—"],
          [t("ui.prov.duration"), env.duration_ms ? formatDuration(env.duration_ms) : "—"],
          [t("ui.tool.input"), r.input_sha256 ? hashBadge(r.input_sha256) : "—"],
          [t("ui.tool.output"), r.output_sha256 ? hashBadge(r.output_sha256) : "—"],
          r.content_hash ? ["Skill", hashBadge(r.content_hash)] : null,
        ])),
    ];
    // the kernel's refusals of this call, whatever its status (a succeeded candidate run is still refused release)
    const refusals = refusalsOf(env);
    if (refusals.length) parts.push(h("section.prov-sec", h("h3.prov-sec__title", t("ui.prov.refusals", { n: refusals.length })), reasonList(refusals)));
    if (rel) {
      parts.push(h("section.prov-sec", h("h3.prov-sec__title", t("ui.cv.title")), compositeVersionView(env) || h("p.muted.small", "—")));
      parts.push(h("section.prov-sec", h("h3.prov-sec__title", t("ui.prov.attestation")), keyValue([
        ["policy_id", rel.policy_id ? h("code.kv__code", rel.policy_id) : h("span.muted", t("ui.artifact.none"))],
        ["audit_head", auditHeadView(env, rel)],
        [t("ui.prov.durable"), r.durable === false ? t("ui.prov.memory_only") : r.durable ? t("ui.prov.durable_yes") : "—"],
        r.skill_pinned === false ? [t("ui.prov.pinned"), candidateChip(env)] : null,
      ], { className: "kv--mono-keys" })));
      parts.push(h("section.prov-sec", h("h3.prov-sec__title", t("ui.prov.release")), releaseChecklist(rel)));
    } else if (env.governance?.limitations?.length) {
      // a result without an artifact card: its limits, all of them
      parts.push(h("section.prov-sec", limitationsBlock(env.governance.limitations, { compact: false })));
    }
    if (sources.length) {
      parts.push(h("section.prov-sec", h("h3.prov-sec__title", t("ui.prov.sources", { n: sources.length })),
        h("div.stack", sources.map((s) => sourceCard(s, { selected: sel?.kind === "source" && sel.id === s.id })))));
    }
    if (rel) parts.push(h("section.prov-sec", h("h3.prov-sec__title", t("ui.prov.artifact")), artifactCard(env)));
    const cmd = reproduceCommand(env, pick.call);
    parts.push(h("section.prov-sec",
      h("h3.prov-sec__title", t("ui.prov.reproduce")),
      h("p.inspector__note", t("ui.prov.reproduce_note")),
      h("pre.code-block.code-block--wrap.prov-cmd", cmd),
      h("div.row", copyButton(cmd, { label: t("ui.prov.copy_cmd"), withLabel: true, variant: "secondary" }))));
    if (env.result !== null && env.result !== undefined) {
      parts.push(h("section.prov-sec", h("details.prov-raw", h("summary", t("ui.prov.raw")), jsonTree(env.result))));
    }
    return h("div.prov", parts);
  }

  // ----------------------------------------------------------------------------------------------- wiring

  const throttled = rafThrottle(render);
  const offs = [
    app.on("inspector", () => render()),
    app.on("turn", () => { if (app.state.inspector.open) throttled(); }),
    app.on("messages", () => render()),
    app.on("lang", () => {
      // labels are built once: rebuild the whole inspector in the new language
      offs.forEach((off) => off());
      Object.assign(api, mountInspector(app, host));
    }),
    app.on("job", () => { if (app.state.inspector.tab === "files") throttled(); }),
  ];
  render();
  const api = { refresh: render, destroy: () => offs.forEach((off) => off()) };
  return api;
}

/** The exact call, through the dispatcher's own command line (the same code the browser and the runner run). */
export function reproduceCommand(env, call) {
  const tool = env?.tool || call?.name || "";
  const args = call?.args && typeof call.args === "object" ? call.args : {};
  const json = canonicalJson(args).replace(/'/g, "'\\''");
  const where = env?.receipt?.where === "browser" ? "browser" : "runner";
  return `python3 -m tcmstudio call ${tool} --where ${where} --args '${json}'`;
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return url; }
}

function clip(s, n) {
  const str = String(s || "").replace(/\s+/g, " ").trim();
  return [...str].length > n ? `${[...str].slice(0, n - 1).join("")}…` : str;
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export { lang };
