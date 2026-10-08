// The app shell (DESIGN §4.2–4.3): top bar, sidebar (or rail, or off-canvas sheet), the main view for the route, and
// the inspector (docked, drawer or bottom sheet). Views are mounted per route; each returns {el, destroy?}.

import { lang, setLang, t } from "../core/i18n.js";
import { cssEscape, dateGroup, fill, formatDateTime, formatRelative, h, shortcutLabel } from "./dom.js";
import { icon } from "./icons.js";
import { openMenu, promptDialog, confirmDialog, trapFocus, toast } from "./overlay.js";
import { iconButton, statusDot } from "./primitives.js";
import { computeChipLabel, runnerTone } from "./panels.js";

export function mountShell(app, root) {
  const topbar = h("header.topbar", { role: "banner" });
  const sidebar = h("aside#sidebar.sidebar", { "aria-label": t("ui.sidebar.label") });
  const main = h("main#main.main", { tabindex: "-1" });
  const inspectorHost = h("aside#inspector.inspector", { "aria-label": t("ui.inspector.label"), hidden: true });
  const scrim = h("div.scrim", { hidden: true, onClick: () => closeOverlays() });
  fill(root, topbar, sidebar, main, inspectorHost, scrim);
  root.classList.add("app--ready");

  let view = null;
  let inspector = null;
  let releaseSidebarTrap = null;
  let releaseInspectorTrap = null;

  const setLayout = () => {
    root.dataset.layout = app.state.layout;
    root.dataset.route = app.state.route.name;
    renderTopbar();
    renderSidebar();
    syncOverlays();
  };

  function closeOverlays() {
    if (app.state.sidebarOpen) { app.state.sidebarOpen = false; syncOverlays(); }
    if (app.state.inspector.open && app.state.layout !== "wide") app.toggleInspector(false);
  }

  /** The sidebar sheet and the inspector drawer/sheet are modal on narrow screens: scrim + focus trap. */
  function syncOverlays() {
    const mode = app.state.layout;
    const sidebarModal = app.state.sidebarOpen && mode !== "wide" && !(mode === "medium" && !document.documentElement.classList.contains("sidebar-collapsed"));
    const insOpen = app.state.inspector.open && app.state.route.name === "conversation";
    const insModal = insOpen && mode !== "wide";
    root.classList.toggle("sidebar-open", Boolean(app.state.sidebarOpen));
    root.classList.toggle("inspector-open", insOpen);
    scrim.hidden = !(sidebarModal || insModal);
    scrim.classList.toggle("is-sheet", insModal && mode === "mobile");
    // what is behind a modal sheet is inert, as <dialog>.showModal() makes it (Tab and the screen reader stay inside)
    const inert = (el, on) => { if (on) el.setAttribute("inert", ""); else el.removeAttribute("inert"); };
    inert(topbar, sidebarModal || insModal);
    inert(main, sidebarModal || insModal);
    inert(sidebar, insModal && !sidebarModal);
    inert(inspectorHost, sidebarModal && !insModal);
    // focus goes back to the button that opened the sheet (found anew: the top bar may have been re-rendered)
    if (sidebarModal && !releaseSidebarTrap) releaseSidebarTrap = trapFocus(sidebar, { returnTo: () => topbar.querySelector(".topbar__menu") });
    if (!sidebarModal && releaseSidebarTrap) { releaseSidebarTrap(); releaseSidebarTrap = null; }
    if (insModal && !releaseInspectorTrap) releaseInspectorTrap = trapFocus(inspectorHost, { returnTo: () => topbar.querySelector(".topbar__inspector") || document.getElementById("composer-input") });
    if (!insModal && releaseInspectorTrap) { releaseInspectorTrap(); releaseInspectorTrap = null; }
    inspectorHost.hidden = !insOpen;
    if (insModal) inspectorHost.setAttribute("aria-modal", "true");
    else inspectorHost.removeAttribute("aria-modal");
    inspectorHost.setAttribute("role", insModal ? "dialog" : "complementary");
  }

  // ----------------------------------------------------------------------------------------------- top bar

  function renderTopbar() {
    const s = app.state;
    const crumbs = [];
    if (s.project && (s.route.name === "project" || s.route.name === "conversation")) {
      crumbs.push(h("a.crumb", { href: app.href({ name: "project", projectId: s.project.id }), "data-tip": t("ui.topbar.project_tip") },
        icon("folder", { size: 14 }), h("span.crumb__text", s.project.name)));
    }
    if (s.conversation && s.route.name === "conversation") {
      crumbs.push(h("button.crumb.crumb--conv", {
        type: "button", "data-tip": t("ui.topbar.rename_tip"),
        onClick: async () => {
          const title = await promptDialog({ title: t("ui.conv.rename"), value: s.conversation.title || "" });
          if (title) app.renameConversation(s.conversation.id, title);
        },
      }, h("span.crumb__text", s.conversation.title || t("ui.conv.untitled"))));
    }
    const pageTitle = { settings: t("ui.nav.settings"), about: t("ui.nav.about"), catalog: t("ui.nav.catalog") }[s.route.name];
    if (pageTitle) crumbs.push(h("span.crumb.crumb--page", h("span.crumb__text", pageTitle)));

    const runner = s.runner.status;
    const chip = h("button.compute-chip.compute-chip--top", {
      type: "button", "aria-haspopup": "dialog", "data-tip": t("ui.compute.chip_tip"),
      // the anchor is read now: after the import resolves, the event's currentTarget is null
      onClick: (e) => { const anchor = e.currentTarget; import("./panels.js").then((m) => m.openComputePopover(app, anchor, { returnTo: () => topbar.querySelector(".compute-chip--top") })); },
    }, statusDot(runnerTone(app), t(`ui.runner.status.${runner}`)), h("span.compute-chip__label", computeChipLabel(app)), icon("chevronDown", { size: 12 }));

    fill(topbar, 
      h("div.topbar__left",
        iconButton({ icon: s.layout === "mobile" ? "menu" : "sidebar", label: t("ui.sidebar.toggle"), kbd: "mod+shift+s", onClick: () => { app.toggleSidebar(); syncOverlays(); }, className: "topbar__menu" }),
        // no aria-label: the name is the visible wordmark (the space inside 「 Studio」 is collapsed on screen, kept in the name)
        h("a.topbar__brand", { href: "#/" },
          h("span.wordmark", h("span.wordmark__tcm", "TCM", h("span.wordmark__dot", "·"), "Science"), h("span.wordmark__studio", " Studio"))),
        crumbs.length ? h("nav.crumbs", { "aria-label": t("ui.topbar.breadcrumb") }, crumbs.flatMap((c, i) => (i ? [h("span.crumbs__sep", { "aria-hidden": "true" }, "/"), c] : [c]))) : null),
      h("div.topbar__right",
        chip,
        // the accessible name starts with the visible 「中 EN」 (voice control: WCAG 2.5.3)
        h("button.lang-switch", {
          type: "button", "aria-label": `中 EN, ${t("ui.lang.switch")}`, "data-tip": t("ui.lang.switch"),
          onClick: () => setLang(lang() === "zh" ? "en" : "zh"),
        }, h("span", { class: lang() === "zh" ? "is-on" : "", lang: "zh-Hans" }, "中"), h("span", { class: lang() === "en" ? "is-on" : "", lang: "en" }, "EN")),
        themeButton(app),
        s.route.name === "conversation" ? iconButton({
          icon: "inspector", label: t("ui.inspector.toggle"), kbd: "alt+backslash", pressed: s.inspector.open, controls: "inspector",
          onClick: () => app.toggleInspector(), className: "topbar__inspector",
        }) : null));
  }

  // ----------------------------------------------------------------------------------------------- sidebar

  function renderSidebar() {
    const s = app.state;
    const collapsedRail = (s.layout === "wide" || s.layout === "medium") && document.documentElement.classList.contains("sidebar-collapsed") && !s.sidebarOpen;
    const rail = collapsedRail || (s.layout === "narrow" && !s.sidebarOpen);
    sidebar.classList.toggle("is-rail", rail);
    const top = h("div.sidebar__top",
      h("button.side-action.side-action--primary", { type: "button", onClick: () => app.newConversation(), "data-tip": `${t("ui.nav.new_chat")}  ${shortcutLabel("mod+shift+o")}`, "aria-label": t("ui.nav.new_chat"), "aria-keyshortcuts": "Control+Shift+O Meta+Shift+O" },
        icon("newChat"), h("span.side-action__label", t("ui.nav.new_chat"))),
      h("button.side-action", { type: "button", onClick: () => app.openPalette(), "data-tip": `${t("ui.nav.search")}  ${shortcutLabel("mod+k")}`, "aria-label": t("ui.nav.search"), "aria-keyshortcuts": "Control+K Meta+K" },
        icon("search"), h("span.side-action__label", t("ui.nav.search")), h("kbd.kbd.side-action__kbd", shortcutLabel("mod+k"))));
    const body = rail ? railBody() : h("div.sidebar__scroll", projectsSection(), conversationsSection());
    const foot = h("nav.sidebar__foot", { "aria-label": t("ui.sidebar.more") },
      navLink("#/catalog", "library", t("ui.nav.catalog"), s.route.name === "catalog", rail),
      navLink("#/settings/models", "settings", t("ui.nav.settings"), s.route.name === "settings", rail),
      navLink("#/about", "help", t("ui.nav.about"), s.route.name === "about", rail));
    fill(sidebar, top, body, foot);
  }

  function railBody() {
    const s = app.state;
    return h("div.sidebar__rail",
      h("button.side-action", {
        type: "button", "aria-label": t("ui.sidebar.projects"), "data-tip": t("ui.sidebar.projects"),
        onClick: () => { app.state.sidebarOpen = true; renderSidebar(); syncOverlays(); },
      }, icon("folder")),
      s.recent.slice(0, 6).map((c) => h("a.rail-conv", {
        href: app.href({ name: "conversation", projectId: c.projectId, convId: c.id }), "data-tip": c.title || t("ui.conv.untitled"),
        "aria-label": c.title || t("ui.conv.untitled"), "aria-current": s.conversation?.id === c.id ? "page" : null,
      }, h("span", [...(c.title || "·")][0]))));
  }

  function navLink(href, ic, label, current, rail) {
    return h("a.side-link", { href, "aria-current": current ? "page" : null, "data-tip": rail ? label : null, "aria-label": rail ? label : null },
      icon(ic), h("span.side-link__label", label));
  }

  function projectsSection() {
    const s = app.state;
    const list = h("ul.side-list", { role: "list" }, s.projects.map((p) => projectItem(p)));
    const archived = s.archived.length ? h("details.side-archived",
      h("summary", t("ui.sidebar.archived", { n: s.archived.length })),
      h("ul.side-list", { role: "list" }, s.archived.map((p) => projectItem(p, true)))) : null;
    return h("section.side-section",
      h("div.side-section__head",
        h("h2.side-section__title", t("ui.sidebar.projects")),
        iconButton({ icon: "folderPlus", label: t("ui.project.new"), size: "sm", onClick: async () => {
          const name = await promptDialog({ title: t("ui.project.new"), label: t("ui.project.name"), placeholder: t("ui.project.name_ph"), confirmLabel: t("ui.action.create") });
          if (name) app.createProject({ name });
        } })),
      s.projects.length ? list : h("p.side-empty", t("ui.sidebar.no_projects")),
      archived);
  }

  function projectItem(p, archived = false) {
    const s = app.state;
    const current = s.project?.id === p.id && s.route.name === "project";
    const active = s.project?.id === p.id;
    return h("li", { class: ["side-item", active && "is-active-project"] },
      h("a.side-item__link", { href: app.href({ name: "project", projectId: p.id }), "aria-current": current ? "page" : null },
        icon(active ? "folderOpen" : "folder", { size: 16 }), h("span.side-item__text", p.name)),
      iconButton({ icon: "more", label: t("ui.project.menu", { name: p.name }), size: "sm", className: "side-item__more", onClick: (e) => openMenu(e.currentTarget, [
        { label: t("ui.action.rename"), icon: "rename", onSelect: async () => {
          const name = await promptDialog({ title: t("ui.project.rename"), value: p.name });
          if (name) app.updateProject(p.id, { name });
        } },
        archived
          ? { label: t("ui.project.unarchive"), icon: "unarchive", onSelect: () => app.updateProject(p.id, { archived: false }) }
          : { label: t("ui.project.archive"), icon: "archive", onSelect: async () => { await app.updateProject(p.id, { archived: true }); toast(t("ui.project.archived", { name: p.name }), { action: { label: t("ui.action.undo"), onClick: () => app.updateProject(p.id, { archived: false }) } }); } },
        "-",
        { label: t("ui.action.delete"), icon: "trash", danger: true, onSelect: async () => {
          const ok = await confirmDialog({ title: t("ui.project.delete_title", { name: p.name }), body: t("ui.project.delete_body"), confirmLabel: t("ui.action.delete"), danger: true });
          if (ok) app.deleteProject(p.id);
        } },
      ]) }));
  }

  function conversationsSection() {
    const s = app.state;
    const convs = s.project ? s.conversations : s.recent;
    const groups = { today: [], yesterday: [], week: [], older: [] };
    const pinned = [];
    for (const c of convs) (c.pinned ? pinned : groups[dateGroup(c.updatedAt)]).push(c);
    const title = s.project ? t("ui.sidebar.conversations") : t("ui.sidebar.recent");
    const block = (key, list) => (list.length ? h("div.side-group",
      h("h3.side-group__title", t(`ui.sidebar.group.${key}`)),
      h("ul.side-list", { role: "list" }, list.map((c) => convItem(c)))) : null);
    return h("section.side-section.side-section--convs",
      h("div.side-section__head", h("h2.side-section__title", { title: s.project ? t("ui.sidebar.conversations_in", { name: s.project.name }) : title }, title)),
      convs.length ? [block("pinned", pinned), block("today", groups.today), block("yesterday", groups.yesterday), block("week", groups.week), block("older", groups.older)]
        : h("p.side-empty", t("ui.sidebar.no_conversations")));
  }

  function convItem(c) {
    const s = app.state;
    const current = s.conversation?.id === c.id;
    const live = s.turn?.conversationId === c.id;
    return h("li", { class: ["side-item", "side-item--conv", current && "is-current"] },
      h("a.side-item__link", { href: app.href({ name: "conversation", projectId: c.projectId, convId: c.id }), "aria-current": current ? "page" : null, title: formatDateTime(c.updatedAt) },
        live ? h("span.spinner", { style: "width:12px;height:12px", "aria-hidden": "true" }) : null,
        c.pinned ? icon("pin", { size: 12, className: "side-item__pin" }) : null,
        h("span.side-item__text", c.title || t("ui.conv.untitled")),
        h("span.side-item__time.num", formatRelative(c.updatedAt))),
      iconButton({ icon: "more", label: t("ui.conv.menu", { name: c.title || t("ui.conv.untitled") }), size: "sm", className: "side-item__more", onClick: (e) => openMenu(e.currentTarget, [
        { label: t("ui.action.rename"), icon: "rename", onSelect: async () => {
          const title = await promptDialog({ title: t("ui.conv.rename"), value: c.title || "" });
          if (title) app.renameConversation(c.id, title);
        } },
        { label: c.pinned ? t("ui.conv.unpin") : t("ui.conv.pin"), icon: "pin", onSelect: async () => { await app.store.conversations.update(c.id, { pinned: !c.pinned, updatedAt: c.updatedAt }); app.refreshConversations(); } },
        "-",
        { label: t("ui.action.delete"), icon: "trash", danger: true, onSelect: async () => {
          const ok = await confirmDialog({ title: t("ui.conv.delete_title"), body: t("ui.conv.delete_body", { name: c.title || t("ui.conv.untitled") }), confirmLabel: t("ui.action.delete"), danger: true });
          if (ok) app.deleteConversation(c.id);
        } },
      ]) }));
  }

  // ----------------------------------------------------------------------------------------------- views

  async function mountView() {
    const route = app.state.route;
    // the same page with another detail (a catalog entry, a settings tab): update in place, keep its state
    if (view?.routeName === route.name && typeof view.update === "function") {
      view.update(route);
      setLayout();
      return;
    }
    view?.destroy?.();
    view = null;
    let mod;
    switch (route.name) {
      case "conversation": mod = await import("./conversation.js"); view = mod.mountConversation(app, main); break;
      case "project":
      case "home": mod = await import("./pages/project.js"); view = mod.mountProject(app, main); break;
      case "settings": mod = await import("./pages/settings.js"); view = mod.mountSettings(app, main); view.routeName = "settings"; break;
      case "about": mod = await import("./pages/about.js"); view = mod.mountAbout(app, main); break;
      case "catalog": mod = await import("./pages/catalog.js"); view = mod.mountCatalog(app, main); view.routeName = "catalog"; break;
      default: {
        const { emptyState } = await import("./primitives.js");
        fill(main, h("div.page", emptyState({ icon: "help", title: t("ui.notfound.title"), body: t("ui.notfound.body") }), h("p", { style: "text-align:center" }, h("a", { href: "#/" }, t("ui.notfound.home")))));
      }
    }
    if (route.name === "conversation") {
      if (!inspector) {
        const m = await import("./inspector.js");
        inspector = m.mountInspector(app, inspectorHost);
      } else inspector.refresh();
    }
    const title = { conversation: app.state.conversation?.title, project: app.state.project?.name, settings: t("ui.nav.settings"), about: t("ui.nav.about"), catalog: t("ui.nav.catalog") }[route.name];
    document.title = title ? `${title} · TCMScience Studio` : "TCMScience Studio";
    setLayout();
    syncSkipLinks();
  }

  /**
   * The skip links point at what this page has: the message box and the conversation where they exist; elsewhere the
   * first one goes to the main region and the second is hidden (a skip link never points at nothing).
   */
  function syncSkipLinks() {
    const [toComposer, toThread] = document.querySelectorAll(".skip-link");
    if (toComposer) {
      const has = Boolean(document.getElementById("composer-input"));
      toComposer.setAttribute("href", has ? "#composer-input" : "#main");
      toComposer.setAttribute("data-i18n", has ? "ui.skip.composer" : "ui.skip.main");
      toComposer.textContent = t(has ? "ui.skip.composer" : "ui.skip.main");
    }
    if (toThread) toThread.hidden = !document.getElementById("thread");
  }

  app.on("route", () => { mountView(); });
  app.on("layout", setLayout);
  app.on("sidebar", () => { renderSidebar(); syncOverlays(); });
  // the inspector toggle is updated in place (re-rendering the top bar would take the focus off the button just pressed)
  app.on("inspector", () => {
    const toggle = topbar.querySelector(".topbar__inspector");
    if (toggle) toggle.setAttribute("aria-pressed", String(Boolean(app.state.inspector.open)));
    else renderTopbar();
    syncOverlays();
  });
  app.on("projects", () => { renderSidebar(); renderTopbar(); });
  app.on("conversations", renderSidebar);
  app.on("conversation", renderTopbar);
  app.on("project", renderTopbar);
  app.on("runtime", renderTopbar);
  app.on("settings", ({ changed }) => { if (changed.some((k) => ["compute", "theme", "*"].includes(k))) renderTopbar(); });
  app.on("turn", (e) => { if (e.type === "start" || e.type === "end") renderSidebar(); });
  app.on("lang", async () => {
    // the page is rebuilt in the new language: the control that changed it keeps the focus
    const a = document.activeElement;
    const refocus = a?.matches?.(".lang-switch") ? ".lang-switch"
      : a?.matches?.("[role=radio][data-value]") ? `[role=radio][data-value="${cssEscape(a.dataset.value)}"]` : null;
    sidebar.setAttribute("aria-label", t("ui.sidebar.label"));
    inspectorHost.setAttribute("aria-label", t("ui.inspector.label"));
    view?.destroy?.();
    view = null;
    await mountView();
    inspector?.refresh();
    if (refocus) (root.querySelector(refocus) || main).focus?.({ preventScroll: true });
  });

  // swipe the off-canvas sidebar closed on phones
  let touchX = null;
  sidebar.addEventListener("touchstart", (e) => { touchX = e.touches[0].clientX; }, { passive: true });
  sidebar.addEventListener("touchend", (e) => {
    if (touchX !== null && app.state.sidebarOpen && e.changedTouches[0].clientX - touchX < -60) { app.state.sidebarOpen = false; renderSidebar(); syncOverlays(); }
    touchX = null;
  });
  sidebar.addEventListener("click", (e) => {
    if (e.target.closest("a[href]") && app.state.sidebarOpen) { app.state.sidebarOpen = false; syncOverlays(); }
  });

  setLayout();
  return { topbar, sidebar, main, inspectorHost, renderTopbar, renderSidebar };
}

function themeButton(app) {
  const theme = app.state.settings.theme;
  const ic = theme === "dark" ? "moon" : theme === "light" ? "sun" : "circleHalf";
  return iconButton({
    icon: ic, label: t("ui.theme.switch", { theme: t(`ui.theme.${theme}`) }),
    onClick: (e) => openMenu(e.currentTarget, ["system", "light", "dark"].map((v) => ({
      label: t(`ui.theme.${v}`), icon: v === "dark" ? "moon" : v === "light" ? "sun" : "circleHalf", hint: v === theme ? "✓" : "",
      onSelect: () => app.setSetting({ theme: v }),
    })), { label: t("ui.theme.title") }),
  });
}
