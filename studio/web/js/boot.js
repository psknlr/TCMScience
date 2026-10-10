// Runs before first paint (a classic, blocking script in <head>; the CSP forbids inline scripts). It applies the saved
// theme, the language and the collapsed sidebar, so the page never flashes the wrong colours or language. Everything
// else is main.js. Keep this file tiny and dependency-free.
(function () {
  "use strict";
  var root = document.documentElement;
  var settings = {};
  try { settings = JSON.parse(localStorage.getItem("tcmstudio.settings") || "{}") || {}; } catch (e) { settings = {}; }

  var theme = settings.theme === "light" || settings.theme === "dark" ? settings.theme : "system";
  if (theme !== "system") root.setAttribute("data-theme", theme);
  var dark = theme === "dark" || (theme === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
  var metas = document.querySelectorAll('meta[name="theme-color"]');
  if (theme !== "system") for (var i = 0; i < metas.length; i++) metas[i].setAttribute("content", dark ? "#0f1318" : "#fbfaf7");

  // language: ?lang= wins, then the Arena-wide key, then the settings, then the browser (DESIGN §3.8)
  var lang = null;
  try { lang = new URLSearchParams(location.search).get("lang"); } catch (e) { lang = null; }
  if (lang !== "zh" && lang !== "en") { try { lang = localStorage.getItem("tcmscience.lang"); } catch (e) { lang = null; } }
  if (lang !== "zh" && lang !== "en") lang = settings.lang;
  if (lang !== "zh" && lang !== "en") {
    var nav = (navigator.languages && navigator.languages[0]) || navigator.language || "zh";
    lang = /^zh\b/i.test(nav) ? "zh" : "en";
  }
  root.lang = lang === "zh" ? "zh-Hans" : "en";
  root.setAttribute("data-lang", lang);

  if (settings.sidebarCollapsed) root.classList.add("sidebar-collapsed");
  var w = Number(settings.inspectorWidth);
  if (w >= 320 && w <= 720) root.style.setProperty("--inspector-w", w + "px");
  root.classList.add("js");

  // The app's modules could not be loaded (the connection dropped while the page loaded, a file is missing): main.js
  // never runs then, and its own "could not start" message with it. Say so instead of loading forever. A module
  // that failed to download fires "error" on its <script>, which only a capturing listener sees; an exception while
  // the modules are evaluated reaches window before main.js has run (main.js sets __studio when it starts).
  var shown = false;
  function failed() {
    if (shown || window.__studio) return;
    var app = document.getElementById("app");
    if (!app) return;
    shown = true;
    var zh = lang === "zh";
    app.removeAttribute("aria-busy");
    var box = document.createElement("div");
    box.className = "boot-splash";
    box.setAttribute("role", "alert");
    var p = document.createElement("p");
    p.className = "boot-note";
    p.textContent = navigator.onLine === false
      ? (zh ? "TCMScience Studio 未能载入：设备已断网。联网后请重新载入。" : "TCMScience Studio could not load: this device is offline. Reload once it is connected.")
      : (zh ? "TCMScience Studio 未能载入页面文件。请重新载入页面。" : "TCMScience Studio could not load its files. Reload the page to try again.");
    var b = document.createElement("button");
    b.type = "button";
    b.className = "btn btn--primary btn--md";
    b.textContent = zh ? "重新载入" : "Reload";
    b.addEventListener("click", function () { location.reload(); });
    box.appendChild(p);
    box.appendChild(b);
    app.replaceChildren ? app.replaceChildren(box) : (app.innerHTML = "", app.appendChild(box));
  }
  window.addEventListener("error", function (e) {
    var el = e && e.target;
    if (el && el.tagName === "SCRIPT" && el.type === "module") { failed(); return; }
    // only this site's own files: an extension's script that throws early is not the app failing to start
    var file = e && typeof e.filename === "string" ? e.filename : "";
    if (file && file.indexOf(location.origin + "/js/") === 0) failed();
  }, true);
})();
