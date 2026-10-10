// The compute panel (DESIGN §3.6) and the model panel (§3.7), shared by the composer/top-bar popovers and the
// settings pages, plus the runner pairing dialog. Compute runs on the user's machine: the browser (Pyodide in a Web
// Worker) or the local runner on 127.0.0.1, on the device the user picks.

import { allProviders, localFix, PRESETS } from "../core/providers.js";
import { lang, t } from "../core/i18n.js";
import { append, clear, fill, formatNumber, h } from "./dom.js";
import { icon } from "./icons.js";
import { openDialog, openPopover, toast } from "./overlay.js";
import { button, chip, copyButton, notice, progressBar, segmented, slider, spinner, statusDot, switchControl, textField } from "./primitives.js";
import { deviceLabel } from "./toolcards.js";

// ------------------------------------------------------------------------------------------------- compute chip

export function runnerTone(app) {
  const st = app.state.runner.status;
  if (st === "ready") return "ok";
  if (st === "connecting") return "loading";
  if (st === "error") return "busy";
  return "off";
}

function selectedDeviceKind(app) {
  const sel = app.state.runner.settings?.device || app.state.runner.devices?.selected || "auto";
  const devices = app.state.runner.devices?.devices || app.state.runner.info?.devices || [];
  if (sel === "auto") return devices.some((d) => d.available && d.kind !== "cpu" && d.id !== "cpu") ? "GPU" : "CPU";
  return /^(cuda|rocm|mps)/.test(sel) ? "GPU" : "CPU";
}

/** 「浏览器」 / 「本机 · GPU」 / 「本机（未连接）」 — the words on the compute chip. */
export function computeChipLabel(app) {
  const compute = app.compute();
  const ready = app.state.runner.status === "ready";
  if (compute === "browser") return t("ui.compute.chip.browser");
  if (compute === "runner") return ready ? t("ui.compute.chip.runner", { device: selectedDeviceKind(app) }) : t("ui.compute.chip.runner_off");
  return ready ? t("ui.compute.chip.runner", { device: selectedDeviceKind(app) }) : t("ui.compute.chip.browser");
}

export function openComputePopover(app, anchor, { returnTo } = {}) {
  let off = null;
  const ctl = openPopover(anchor, () => {
    const host = h("div.panel-pop");
    const render = () => fill(host, computePanel(app, { compact: true, onNavigate: () => ctl.close({ restoreFocus: false }) }));
    render();
    off = app.on("runtime", render);
    return host;
  }, { placement: "bottom-end", width: 400, label: t("ui.compute.title"), returnTo, onClose: () => off?.() });
  return ctl;
}

// ------------------------------------------------------------------------------------------------- compute panel

/** The whole compute panel. compact: the popover version (links to the settings page for the rest). */
export function computePanel(app, { compact = false, onNavigate } = {}) {
  const s = app.state;
  const parts = [];
  parts.push(h("div.panel__block",
    h("p.panel__k", t("ui.compute.target")),
    segmented({
      label: t("ui.compute.target"), value: app.compute(),
      options: [
        { value: "auto", label: t("ui.compute.auto"), title: t("ui.compute.auto_tip") },
        { value: "browser", label: t("ui.compute.browser"), icon: "globe" },
        { value: "runner", label: t("ui.compute.runner"), icon: "laptop" },
      ],
      onChange: (v) => app.setCompute(v),
    }),
    h("p.panel__help", t(`ui.compute.target_help.${app.compute()}`), s.project?.defaults?.compute ? h("span.muted", ` ${t("ui.compute.project_override", { name: s.project.name })}`) : null)));
  if (s.layout === "mobile") parts.push(notice({ tone: "info", body: t("ui.compute.mobile") }));
  parts.push(browserCard(app, { compact }));
  parts.push(runnerCard(app, { compact }));
  if (compact) {
    parts.push(h("div.panel__block.panel__block--row",
      webSwitch(app, { description: s.project ? t("ui.web.desc_project", { name: s.project.name }) : t("ui.web.desc_default") })));
    parts.push(h("div.panel__foot", h("a.panel__link", { href: "#/settings/compute", onClick: () => onNavigate?.() }, t("ui.compute.more"), icon("chevronRight", { size: 14 }))));
  }
  return h("div", { class: ["panel", compact && "panel--compact"] }, parts);
}

/**
 * The project's web-access switch. Every copy on the page (the compute popover, the phone's options sheet, the
 * project's defaults) follows the app's "web" and "project" events, so each shows the same state as the composer
 * chip: a stale copy would turn a press into a no-op (pressing "off" on a switch that still showed off while the
 * project was on). One listener per app updates the copies in the document; a removed copy holds nothing.
 */
export function webSwitch(app, { label = t("ui.web.label"), description } = {}) {
  syncWebSwitches(app);
  const row = switchControl({ label, description, checked: app.webOn(), onChange: (v) => app.setWeb(v) });
  const sw = row.matches?.('[role="switch"]') ? row : row.querySelector('[role="switch"]');
  sw.dataset.sync = "web";
  return row;
}

const webSynced = new WeakSet();

function syncWebSwitches(app) {
  if (webSynced.has(app)) return;
  webSynced.add(app);
  const sync = () => {
    const on = String(app.webOn());
    for (const sw of document.querySelectorAll('[data-sync="web"]')) {
      if (sw.getAttribute("aria-checked") !== on) sw.setAttribute("aria-checked", on);
    }
  };
  app.on("web", sync);
  app.on("project", sync);
}

function browserCard(app, { compact }) {
  const b = app.state.browser;
  const status = b.status || "idle";
  const tone = status === "ready" ? "ok" : status === "loading" || status === "connecting" ? "loading" : status === "error" ? "busy" : status === "offline" ? "error" : "off";
  const dev = b.device;
  if (!dev) app.browserDevice();
  const loading = status === "loading" || status === "connecting";
  const head = h("div.rt-card__head",
    h("span.rt-card__icon", icon("globe")),
    h("div.rt-card__title", h("p.rt-card__name", t("ui.compute.browser_runtime")), h("p.rt-card__sub", t("ui.compute.browser_sub"))),
    h("span.rt-card__status", statusDot(tone), t(`ui.browser.status.${status}`)));
  const body = [];
  if (loading) {
    body.push(progressBar({ value: typeof b.progress === "number" ? b.progress / 100 : null, label: t("ui.browser.loading") }));
    if (b.message) body.push(h("p.rt-card__msg", b.message));
  }
  if (status === "idle") body.push(h("p.rt-card__note", t("ui.browser.idle_note")));
  if (status === "offline") body.push(h("p.rt-card__note", t("ui.browser.offline_note")));
  if (status === "error" && b.error) body.push(h("p.rt-card__note.is-warn", String(b.error?.message || b.error)));
  const facts = [];
  facts.push(h("li", icon("cpu", { size: 14 }), t("ui.browser.cpu_note")));
  if (dev) {
    facts.push(h("li", icon("gauge", { size: 14 }), t("ui.browser.cores", { cores: dev.cores ?? "?", memory: dev.memory_gb ? t("ui.browser.memory", { gb: dev.memory_gb }) : "" })));
    const ad = dev.webgpu?.adapter;
    let gpuText;
    if (!dev.webgpu?.api) gpuText = t("ui.browser.webgpu_none");
    else if (!ad) gpuText = t("ui.browser.webgpu_no_adapter");
    else if (ad.software) gpuText = t("ui.browser.webgpu_software", { name: [ad.vendor, ad.architecture].filter(Boolean).join(" ") || "software" });
    else gpuText = t("ui.browser.webgpu_hw", { name: [ad.vendor, ad.architecture, ad.description].filter(Boolean).join(" ") });
    facts.push(h("li", { class: ad?.software ? "is-muted" : "" }, icon("gpu", { size: 14 }), gpuText));
    if (!compact) facts.push(h("li", icon("lock", { size: 14 }), dev.cross_origin_isolated ? t("ui.browser.isolated") : t("ui.browser.not_isolated")));
  }
  body.push(h("ul.rt-card__facts", { role: "list" }, facts));
  if (status === "idle" || status === "error") body.push(h("div.rt-card__actions", button({ label: t("ui.browser.preload"), size: "sm", icon: "download", onClick: () => app.preloadBrowser() })));
  return h("section", { class: ["rt-card", status === "ready" && "is-ready"] }, head, body);
}

function runnerCard(app, { compact }) {
  const r = app.state.runner;
  const status = r.status || "idle";
  const tone = runnerTone(app);
  const info = r.info || {};
  const url = app.state.settings.runner.url;
  const head = h("div.rt-card__head",
    h("span.rt-card__icon", icon("laptop")),
    h("div.rt-card__title", h("p.rt-card__name", t("ui.compute.runner_runtime")), h("p.rt-card__sub.mono", hostOf(url) + (info.version ? ` · v${info.version}` : ""))),
    h("span.rt-card__status", statusDot(tone), t(`ui.runner.status.${status}`)));
  if (status !== "ready") return h("section.rt-card", head, runnerConnect(app, { compact }));

  const body = [];
  const cpu = info.cpu || {};
  body.push(h("ul.rt-card__facts", { role: "list" },
    h("li", icon("cpu", { size: 14 }), [cpu.model, cpu.cores ? t("ui.runner.cores", { n: cpu.cores }) : null].filter(Boolean).join(" · ") || t("ui.runner.cpu_unknown")),
    info.memory_gb ? h("li", icon("memory", { size: 14 }), t("ui.runner.memory", { gb: formatNumber(info.memory_gb, { maximumFractionDigits: 1 }) })) : null,
    info.platform ? h("li", icon("monitor", { size: 14 }), info.platform) : null));
  body.push(deviceSelector(app));
  if (!compact) {
    const rs = r.settings || info.settings || {};
    const cores = cpu.cores || 8;
    body.push(h("div.rt-card__sliders",
      slider({ label: t("ui.runner.threads"), min: 1, max: Math.max(1, cores), value: rs.threads || Math.max(1, Math.min(4, cores)), format: (v) => t("ui.runner.threads_n", { n: v }), onChange: (v) => app.updateRunnerSettings({ threads: v }) }),
      slider({ label: t("ui.runner.max_jobs"), min: 1, max: 8, value: rs.max_jobs || 1, format: (v) => t("ui.runner.jobs_n", { n: v }), onChange: (v) => app.updateRunnerSettings({ max_jobs: v }) })));
    body.push(enginesList(info.engines || []));
    body.push(h("div.rt-card__switches",
      switchControl({ label: t("ui.runner.network"), description: t("ui.runner.network_desc"), checked: Boolean(rs.network?.enabled ?? info.network?.enabled), onChange: (v) => app.updateRunnerSettings({ network: { ...(rs.network || {}), enabled: v } }) }),
      switchControl({ label: t("ui.runner.allow_remote"), description: t("ui.runner.allow_remote_desc"), checked: Boolean(rs.allow_remote), onChange: (v) => app.updateRunnerSettings({ allow_remote: v }) })));
  } else {
    const engines = info.engines || [];
    if (engines.length) {
      const ok = engines.filter(engineInstalled).length;
      body.push(h("p.rt-card__note", t("ui.runner.engines_summary", { ok, total: engines.length })));
    }
  }
  body.push(h("div.rt-card__actions", button({ label: t("ui.runner.disconnect"), size: "sm", variant: "ghost", icon: "unplug", onClick: () => app.disconnectRunner() })));
  return h("section.rt-card.is-ready", head, body);
}

function engineInstalled(e) {
  return Boolean(e.installed ?? e.available ?? e.ok);
}

function enginesList(engines) {
  if (!engines.length) return h("p.rt-card__note", t("ui.runner.engines_none"));
  return h("div.engines",
    h("p.panel__k", t("ui.runner.engines")),
    h("ul.engines__list", { role: "list" }, engines.map((e) => {
      const ok = engineInstalled(e);
      const cmd = e.install || e.install_command || "";
      return h("li", { class: ["engines__item", ok ? "is-ok" : "is-missing"] },
        icon(ok ? "check" : "circleDashed", { size: 14 }),
        h("div.engines__text",
          h("p.engines__name", e.name || e.id, e.version ? h("span.muted.mono", ` ${e.version}`) : null, e.gpu ? chip({ label: "GPU", tone: "navy" }) : null),
          ok ? null : h("p.engines__missing", t("ui.runner.engine_missing", { name: e.name || e.id }),
            e.missing?.length ? h("span.mono", ` (${e.missing.join(", ")})`) : null),
          !ok && cmd ? h("div.engines__cmd", h("code", cmd), copyButton(cmd, { label: t("ui.runner.copy_install") })) : null));
    })));
}

function deviceSelector(app) {
  const r = app.state.runner;
  const devices = r.devices?.devices || r.info?.devices || [];
  const selected = r.settings?.device || r.devices?.selected || "auto";
  const options = [{ id: "auto", name: t("ui.runner.device_auto"), available: true, kind: "auto" }, ...devices];
  return h("div.devices",
    h("p.panel__k", t("ui.runner.device")),
    h("div.devices__list", { role: "radiogroup", "aria-label": t("ui.runner.device") }, options.map((d) => {
      const on = d.id === selected;
      return h("button", {
        type: "button", role: "radio", "aria-checked": String(on), class: ["device", d.id === "auto" && "device--auto", on && "is-on", d.available === false && "is-unavailable"], disabled: d.available === false,
        onClick: () => app.updateRunnerSettings({ device: d.id }),
      },
      icon(d.id === "auto" ? "zap" : d.id === "cpu" || d.kind === "cpu" ? "cpu" : "gpu", { size: 14 }),
      h("span.device__name", d.id === "auto" ? d.name : deviceLabel(d.id)),
      d.id !== "auto" && d.name ? h("span.device__desc", d.name) : null,
      d.memory_gb ? h("span.device__mem.num", `${formatNumber(d.memory_gb, { maximumFractionDigits: 1 })} GB`) : null,
      d.note ? h("span.device__note", d.note) : null);
    })));
}

/** Not connected: how to start the runner, the address and token, the pre-explanation of the browser's prompt. */
function runnerConnect(app, { compact }) {
  const r = app.state.runner;
  const box = h("div.connect");
  const url = textField({ label: t("ui.runner.url"), value: app.state.settings.runner.url, mono: true, attrs: { inputmode: "url" } });
  const token = textField({ label: t("ui.runner.token"), value: app.state.settings.runner.token, type: "password", mono: true, help: t("ui.runner.token_help") });
  const err = h("div.connect__error", { "aria-live": "polite" });
  const onHttps = location.protocol === "https:";
  const connectBtn = button({
    label: t("ui.runner.connect"), variant: "primary", icon: "plug",
    onClick: async () => {
      connectBtn.disabled = true;
      connectBtn.querySelector(".btn__label").textContent = t("ui.runner.connecting");
      clear(err);
      const res = await app.connectRunner({ url: url.input.value, token: token.input.value });
      connectBtn.disabled = false;
      connectBtn.querySelector(".btn__label").textContent = t("ui.runner.connect");
      if (!res.ok) fill(err, notice({ tone: "warn", body: res.message }));
    },
  });
  append(box, [
    notice({ tone: "neutral", body: t("ui.runner.not_connected") }),
    h("div.connect__how",
      h("p.panel__k", t("ui.runner.how")),
      h("div.engines__cmd", h("code", "tcmstudio serve"), copyButton("tcmstudio serve", { label: t("ui.action.copy") })),
      h("p.panel__help", t("ui.runner.how_help"))),
    compact ? null : h("div.connect__fields", url, token),
    onHttps || !compact ? notice({ tone: "info", icon: "shieldCheck", body: t("core.runner.lna_prompt") }) : null,
    h("div.rt-card__actions", connectBtn, compact ? h("a.panel__link", { href: "#/settings/compute" }, t("ui.runner.enter_token")) : null),
    err]);
  if (r.error && r.status === "error") err.append(notice({ tone: "warn", body: r.error.message || String(r.error) }));
  return box;
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return url; }
}

/** The dialog a pairing link opens: what will happen, the browser's prompt explained, then one Connect button. */
export function openPairingDialog(app, pairing) {
  let d;
  const status = h("div", { "aria-live": "polite" });
  const go = button({
    label: t("ui.runner.connect"), variant: "primary", icon: "plug", onClick: async () => {
      go.disabled = true;
      fill(status, h("p.muted.small", spinner({ size: 12 }), " ", t("ui.runner.connecting")));
      const res = await app.connectRunner({ url: pairing.url, token: pairing.token });
      go.disabled = false;
      if (res.ok) d.close();
      else fill(status, notice({ tone: "warn", body: res.message }));
    },
  });
  // the browser asks about local-network access only for an https page reaching loopback (runner.js lnaNotice)
  const lna = app.runnerMod?.lnaNotice ? app.runnerMod.lnaNotice(pairing.url) : (location.protocol === "https:" ? t("core.runner.lna_prompt") : "");
  d = openDialog({
    title: t("ui.pair.title"), size: "sm",
    body: h("div.stack",
      h("p.dialog__text", t("ui.pair.body", { host: hostOf(pairing.url) })),
      lna ? notice({ tone: "info", icon: "shieldCheck", body: lna }) : null,
      app.state.runner.status === "error" || app.state.runner.status === "offline" ? notice({ tone: "warn", body: app.state.runner.error?.message || t(`ui.runner.status.${app.state.runner.status}`) }) : null,
      status),
    actions: [button({ label: t("ui.action.later"), variant: "ghost", onClick: () => d.close() }), go],
    initialFocus: go,
  });
}

// ------------------------------------------------------------------------------------------------- model chip & panel

/**
 * The relay's health error as a line (`tag` is an h() selector): in the page's language when its type is known
 * (core relayHealth), the relay's own Chinese text marked lang="zh-Hans" on a page in another language. On the
 * Settings → Models page itself (`here`), without the pointer to that same page.
 */
export function relayErrorLine(relay, { tag = "p", here = false } = {}) {
  const message = here ? withoutModelsPointer(relay?.error) : String(relay?.error || "");
  const [before, after = ""] = t("ui.models.relay_error", { message: "\u0001" }).split("\u0001");
  return h(tag, before, h("span", { lang: relay?.error_lang === "zh" ? "zh-Hans" : null }, message), after);
}

/** A relay error without 「或在「设置 → 模型」里改用自己的模型」 / "or use your own model in Settings → Model". */
export function withoutModelsPointer(text) {
  const s = String(text || "");
  const out = s
    .replace(/[，,]\s*或在「设置 → 模型」(?:里|中)(?:改用|接入|使用)[^。]*。/g, "。")
    .replace(/可以在「设置 → 模型」(?:里|中)(?:改用|接入|使用)[^。]*。/g, "")
    .replace(/,\s*or use your own model[^.()]*?\(Settings → Models?\)\./g, ".")
    .replace(/,\s*or use your own model[^.]*? in Settings → Models?\./g, ".")
    .replace(/\s*You can (?:use|connect) your own model[^.]*? in Settings → Models?\./g, "")
    .trim();
  return out || s;
}

export function modelChipLabel(app) {
  const p = app.provider();
  return p.relay ? "Tao-S1" : p.model || p.label;
}

export function openModelPopover(app, anchor) {
  let off = null;
  const ctl = openPopover(anchor, () => {
    const host = h("div.panel-pop");
    const render = () => fill(host, modelPanel(app, { onPick: () => ctl.close(), onNavigate: () => ctl.close({ restoreFocus: false }) }));
    render();
    off = app.on("relay", render);
    return host;
  }, { placement: "top-start", width: 360, label: t("ui.model.title"), onClose: () => off?.() });
  return ctl;
}

/** The providers the user can pick now: Tao-S1, the APIs with a key, custom endpoints, configured local servers. */
export function availableProviders(app) {
  const s = app.state.settings;
  const all = allProviders(s);
  return all.filter((p) => {
    if (p.relay) return true;
    if (p.custom && (p.id === "custom_openai" || p.id === "custom_anthropic")) return false;
    if (p.custom) return Boolean(p.base_url);
    if (p.local) return Boolean(s.models?.[p.id] || (p.default_model && s.baseUrls?.[p.id])) || Boolean(app.state.localDetected?.some((x) => x.id === p.id && x.ok));
    return Boolean((s.keys || {})[p.id]);
  });
}

export function modelPanel(app, { onPick, onNavigate } = {}) {
  const s = app.state.settings;
  const choice = app.modelChoice();
  const active = app.provider();
  const list = availableProviders(app);
  const relay = app.state.relay;
  const row = (p, model, sub) => {
    const on = p.id === active.id && (model || "") === (active.relay ? model : active.model);
    return h("button", {
      type: "button", role: "radio", "aria-checked": String(on), class: ["model-row", on && "is-on"],
      onClick: () => { app.chooseModel({ provider: p.id, model }); onPick?.(); },
    },
    h("span.model-row__check", on ? icon("check", { size: 14 }) : null),
    h("span.model-row__text", h("span.model-row__name", model || p.label), sub ? h("span.model-row__sub", sub) : null));
  };
  const groups = [];
  const tao = list.find((p) => p.relay);
  if (tao) {
    // the relay's health in words beside the dot (colour is never the only signal)
    const health = relay ? (relay.checking ? spinner({ size: 10, label: t("ui.model.relay_checking") }) : h("span.model-row__health-text", statusDot(relay.ok ? "ok" : "busy"), h("span", relay.ok ? t("ui.model.relay_ok") : t("ui.model.relay_down")))) : null;
    groups.push(h("div.model-group",
      h("p.panel__k", t("ui.model.default")),
      h("div.model-row-wrap", row(tao, "Tao-S1", t("ui.model.tao_sub")), health ? h("span.model-row__health", health) : null)));
  }
  const apis = list.filter((p) => !p.relay && !p.local);
  if (apis.length) {
    groups.push(h("div.model-group", h("p.panel__k", t("ui.model.yours")),
      apis.flatMap((p) => {
        const models = [...new Set([s.models?.[p.id] || p.default_model, ...(p.models || [])].filter(Boolean))];
        return models.slice(0, p.id === active.id ? 6 : 1).map((m, i) => row(p, m, i === 0 ? p.label : ""));
      })));
  }
  const locals = list.filter((p) => p.local);
  const detected = (app.state.localDetected || []).filter((x) => x.ok && x.models?.length);
  if (locals.length || detected.length) {
    const rows = [];
    for (const p of locals) rows.push(row(p, s.models?.[p.id] || p.default_model, p.label));
    for (const srv of detected) {
      const preset = PRESETS.find((p) => p.id === srv.id);
      if (!preset) continue;
      for (const m of srv.models.slice(0, 6)) if (!rows.some((r) => r.textContent.includes(m))) rows.push(row(preset, typeof m === "string" ? m : m.id || m.name, t("ui.model.detected", { label: preset.label })));
    }
    groups.push(h("div.model-group", h("p.panel__k", t("ui.model.local")), rows));
  }
  const detectBtn = app.state.runner.status === "ready" ? button({
    label: t("ui.model.detect"), size: "sm", variant: "ghost", icon: "search",
    onClick: async () => {
      detectBtn.disabled = true;
      await detectLocalModels(app);
      detectBtn.disabled = false;
      app.emit("relay", app.state.relay);
    },
  }) : null;
  return h("div.panel.panel--compact.model-panel",
    h("div.model-panel__head", h("p.model-panel__title", t("ui.model.title")), choice.provider !== s.provider || app.state.conversation?.provider ? chip({ label: app.state.conversation ? t("ui.model.this_conv") : t("ui.model.this_new"), tone: "navy" }) : null),
    // one choice among all the rows: a radiogroup (the rows are radios; a menuitemradio needs a menu around it)
    h("div.model-panel__groups", { role: "radiogroup", "aria-label": t("ui.model.title") }, groups),
    h("div.panel__foot", detectBtn, h("a.panel__link", { href: "#/settings/models", onClick: () => onNavigate?.() }, t("ui.model.manage"), icon("chevronRight", { size: 14 }))));
}

/** Ask the runner which local model servers answer (Ollama, LM Studio, vLLM, llama.cpp). */
export async function detectLocalModels(app) {
  const runner = app.runtimes.runner;
  if (!runner || app.state.runner.status !== "ready") {
    toast(t("ui.model.detect_needs_runner"), { tone: "warn" });
    return [];
  }
  try {
    const res = await runner.localModels();
    app.state.localDetected = (res?.servers || []).map((x) => ({ ...x, models: (x.models || []).map((m) => (typeof m === "string" ? m : m.id || m.name)) }));
    const n = app.state.localDetected.filter((x) => x.ok).length;
    toast(n ? t("ui.model.detected_n", { n }) : t("ui.model.detected_none"), { tone: n ? "ok" : "neutral" });
    return app.state.localDetected;
  } catch (err) {
    toast(err?.message || String(err), { tone: "warn" });
    return [];
  }
}

/** The sentence telling the user how to let the browser reach a local model server (CORS), or the runner route. */
export function localHelp(preset) {
  return localFix(preset);
}

export { lang };
