// The tool catalog (CONTRACTS §2): 13 categories, search (zh/en words, tags, ids), and an entry's detail — its
// parameters, example, where it can run (浏览器 / 本机), network, GPU, heavy dependencies, confirmation, and for a
// governed skill its version, pin, claim kinds and content hash. Read from core/catalog.js.

import { glossary, lang, t } from "../../core/i18n.js";
import { clear, debounce, fill, guessLang, h } from "../dom.js";
import { hashBadge } from "../governance.js";
import { icon } from "../icons.js";
import { button, chip, copyButton, emptyState, keyValue } from "../primitives.js";

const CAT_ICON = {
  tcm_knowledge: "leaf", tcm_safety: "shieldAlert", clinic: "stethoscope", tcm_data: "database", live_sources: "globe",
  netpharm: "network", study_design: "sigma", clinical_calc: "calculator", seq_genomics: "dna", structure_molecules: "atom",
  omics: "layers", literature: "bookText", system: "fingerprint",
};

export function mountCatalog(app, main) {
  const page = h("div.page.page--catalog");
  fill(main, page);
  // kept on the app, so leaving the catalog and coming back finds the same search
  const ui = (app.state.catalogUI ||= { category: "", query: "" });
  let category = app.state.route.params?.category ?? ui.category;
  let query = app.state.route.params?.q ?? ui.query;
  const input = h("input.input.catalog__search-input", { type: "search", placeholder: t("ui.catalog.search_ph"), "aria-label": t("ui.catalog.search"), autocomplete: "off", spellcheck: "false" });
  input.value = query;
  const results = h("div.catalog__results", { "aria-live": "polite" });
  const detail = h("aside.catalog__detail", { "aria-label": t("ui.catalog.detail") });

  const runnable = (e) => app.toolRouter?.where(e) || null;

  function render() {
    const cat = app.catalog;
    if (!cat || !cat.entries.length) {
      fill(page, h("div.page__inner", header(cat), emptyState({ icon: "library", title: t("ui.catalog.empty_title"), body: t("ui.catalog.empty"), actions: [button({ label: t("ui.runner.connect"), icon: "plug", onClick: () => app.navigate({ name: "settings", tab: "compute" }) })] })));
      return;
    }
    const nav = h("nav.catalog__cats", { "aria-label": t("ui.catalog.categories") },
      catButton("", t("ui.catalog.all"), cat.entries.length, "library"),
      cat.categories.map((c) => catButton(c.id, glossary.category(c.id), c.count, CAT_ICON[c.id] || "box")));
    fill(page, h("div.catalog",
      header(cat),
      h("div.catalog__search", icon("search", { size: 16 }), input),
      h("div.catalog__grid", nav, results, detail)));
    renderResults();
    renderDetail();
  }

  function header(cat) {
    return h("header.catalog__head",
      h("h1.catalog__title.serif", t("ui.nav.catalog")),
      h("p.catalog__lede", cat ? t("ui.catalog.lede", { entries: cat.entries.length, core: cat.core.length }) : t("ui.catalog.lede_empty")));
  }

  function catButton(id, label, count, ic) {
    return h("button", { type: "button", class: ["catalog__cat", category === id && "is-on"], "aria-pressed": String(category === id), onClick: () => { category = id; ui.category = id; render(); } },
      icon(ic, { size: 16 }), h("span.catalog__cat-label", label), h("span.catalog__cat-n.num", String(count)));
  }

  function renderResults() {
    const cat = app.catalog;
    // no words: every entry of the category in catalog order; words: the 200 best matches
    const hits = query.trim() ? cat.search(query, { category: category || undefined, limit: 200 }) : cat.entries.filter((e) => !category || e.category === category);
    fill(results, 
      h("p.catalog__count", hits.length >= 200 && query.trim() ? t("ui.catalog.count_top", { n: hits.length }) : t("ui.catalog.count", { n: hits.length })),
      hits.length ? h("ul.catalog__list", { role: "list" }, hits.map((e) => entryRow(e))) : h("p.muted", t("ui.catalog.no_match")));
  }

  function entryRow(e) {
    const on = app.state.route.entryId === e.id;
    return h("li", h("a", { href: app.href({ name: "catalog", entryId: e.id }), class: ["entry-row", on && "is-on"], "aria-current": on ? "true" : null },
      h("div.entry-row__head",
        h("span.entry-row__title", titleOf(e)),
        h("span.entry-row__kind", t(`ui.catalog.kind.${e.kind}`) !== `ui.catalog.kind.${e.kind}` ? t(`ui.catalog.kind.${e.kind}`) : e.kind)),
      h("code.entry-row__id", e.id),
      e.summary ? h("p.entry-row__summary", { lang: guessLang(e.summary) || null }, inlineLiterals(e.summary)) : null,
      h("div.entry-row__badges", badges(e, runnable(e)))));
  }

  function renderDetail() {
    const id = app.state.route.entryId;
    const e = id ? app.catalog.byId[id] || app.catalog.entry(id) : null;
    detail.hidden = !e;
    page.querySelector(".catalog")?.classList.toggle("has-detail", Boolean(e));
    if (!e) { clear(detail); return; }
    const core = app.catalog.core.filter((c) => c.maps_to === e.id);
    const props = e.parameters?.properties || {};
    const req = new Set(e.parameters?.required || []);
    const rows = Object.entries(props);
    const example = e.example ? JSON.stringify(e.example, null, 2) : "";
    fill(detail, h("div.entry",
      h("div.entry__top",
        h("a.icon-btn.icon-btn--ghost.icon-btn--sm", { href: app.href({ name: "catalog" }), "aria-label": t("ui.action.close") }, icon("x", { size: 14 }))),
      h("p.kicker", glossary.category(e.category)),
      h("h2.entry__title", titleOf(e)),
      h("code.entry__id", e.id),
      h("div.entry__badges", badges(e, runnable(e))),
      e.summary ? h("p.entry__summary", { lang: guessLang(e.summary) || null }, inlineLiterals(e.summary)) : null,
      e.available === false && e.missing?.length ? h("p.entry__missing", icon("alert", { size: 14 }), t("ui.catalog.missing", { deps: e.missing.join(", ") })) : null,
      core.length ? h("p.entry__core", icon("sparkles", { size: 14 }), t("ui.catalog.core", { names: core.map((c) => c.name).join(", ") })) : null,
      h("section.entry__sec", h("h3", t("ui.catalog.params")),
        rows.length ? h("div.tablewrap", h("table.params",
          h("thead", h("tr", h("th", t("ui.catalog.param")), h("th", t("ui.catalog.type")), h("th", t("ui.catalog.desc")))),
          h("tbody", rows.map(([name, sch]) => h("tr",
            h("td", h("code", name), req.has(name) ? h("span.params__req", { "aria-label": t("ui.catalog.required") }, " *") : null),
            h("td.params__type", typeOf(sch)),
            h("td", { lang: guessLang(sch.description) || null }, inlineLiterals(sch.description || ""), sch.default !== undefined ? h("span.muted", ` (${t("ui.catalog.default")} ${JSON.stringify(sch.default)})`) : null, sch.enum ? h("span.muted", ` · ${sch.enum.join(" / ")}`) : null)))))) : h("p.muted.small", t("ui.catalog.no_params"))),
      // a scrolling <pre> is reachable by keyboard (tabindex=0) and named (WCAG 2.1.1)
      example ? h("section.entry__sec", h("div.entry__sec-head", h("h3", t("ui.catalog.example")), copyButton(example)), h("pre.code-block", { tabindex: "0", role: "region", "aria-label": t("ui.catalog.example") }, example)) : null,
      e.skill ? h("section.entry__sec", h("h3", t("ui.catalog.skill")), keyValue([
        [t("ui.catalog.version"), e.skill.version || "—"],
        [t("ui.catalog.pinned"), e.skill.pinned ? t("ui.common.yes") : t("ui.common.no")],
        [t("ui.catalog.claim_kinds"), (e.skill.claim_kinds || []).map((k) => glossary.claimKind(k)).join("、") || "—"],
        [t("ui.catalog.forbidden"), (e.skill.forbidden_claims || []).map((k) => glossary.claimKind(k)).join("、") || "—"],
        [t("ui.catalog.max_tier"), e.skill.max_evidence_tier ? glossary.evidenceTier(e.skill.max_evidence_tier) : "—"],
        [t("ui.catalog.hosts"), (e.skill.network || []).join(", ") || t("ui.catalog.no_network")],
        [t("ui.catalog.content_hash"), e.skill.content_hash ? hashBadge(e.skill.content_hash) : "—"],
      ])) : null,
      h("div.entry__actions", button({ label: t("ui.catalog.try"), variant: "primary", icon: "message", onClick: () => {
        app.state.pendingPrompt = t("ui.catalog.try_prompt", { title: titleOf(e), id: e.id });
        app.newConversation();
      } }))));
  }

  input.addEventListener("input", debounce(() => { query = input.value; ui.query = query; renderResults(); }, 120));
  const offs = [app.on("catalog", render), app.on("runtime", () => { if (app.catalog?.entries.length) renderResults(); })];
  render();
  if (!app.state.route.entryId && app.state.layout !== "mobile") requestAnimationFrame(() => input.focus({ preventScroll: true }));
  return {
    el: page,
    update() {
      if (!app.catalog?.entries.length) { render(); return; }
      for (const a of results.querySelectorAll(".entry-row")) {
        const on = a.getAttribute("href") === app.href({ name: "catalog", entryId: app.state.route.entryId });
        a.classList.toggle("is-on", on);
        if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
      }
      renderDetail();
      if (app.state.layout === "mobile") detail.scrollIntoView?.({ block: "start" });
    },
    destroy: () => offs.forEach((off) => off()),
  };
}

/**
 * Docstring text with its reStructuredText inline literals (``X``) as <code>: built as nodes (never innerHTML), the
 * text itself unchanged.
 */
export function inlineLiterals(text) {
  const s = String(text || "");
  const out = [];
  let last = 0;
  for (const m of s.matchAll(/``([^`]+)``/g)) {
    if (m.index > last) out.push(s.slice(last, m.index));
    out.push(h("code", m[1]));
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

function titleOf(e) {
  const tt = e.title;
  if (tt && typeof tt === "object") return tt[lang()] || tt.en || tt.zh || e.id;
  return tt || e.id;
}

function typeOf(s) {
  if (!s) return "";
  if (s.type === "array") return `${typeOf(s.items) || "any"}[]`;
  if (Array.isArray(s.type)) return s.type.join(" | ");
  if (s.anyOf) return s.anyOf.map(typeOf).join(" | ");
  return s.type || (s.enum ? "enum" : "any");
}

/** Where it can run and what it needs: 浏览器 / 本机 / 联网 / GPU / heavy deps / 需确认 / 任务 / runnable now. */
export function badges(e, where) {
  const out = [];
  const exec = e.exec || [];
  if (exec.includes("browser")) out.push(chip({ label: t("ui.catalog.badge.browser"), icon: "globe" }));
  if (exec.includes("runner")) out.push(chip({ label: t("ui.catalog.badge.runner"), icon: "laptop" }));
  if (e.network) out.push(chip({ label: t("ui.catalog.badge.network"), tone: "ochre", icon: "radio" }));
  if (e.gpu) out.push(chip({ label: "GPU", tone: "navy", icon: "gpu" }));
  for (const dep of e.heavy || []) out.push(chip({ label: dep, mono: true, tone: "slate" }));
  if (e.confirm) out.push(chip({ label: t("ui.catalog.badge.confirm"), tone: "ochre", icon: "shieldAlert" }));
  if (e.job) out.push(chip({ label: t("ui.catalog.badge.job"), icon: "clock" }));
  if (e.available === false) out.push(chip({ label: t("ui.catalog.badge.missing"), tone: "vermilion", dashed: true }));
  out.push(where
    ? chip({ label: t("ui.catalog.badge.now", { where: where === "runner" ? t("ui.where.runner") : t("ui.where.browser") }), tone: "jade", icon: "check" })
    : chip({ label: t("ui.catalog.badge.not_now"), tone: "slate", dashed: true }));
  return out;
}
