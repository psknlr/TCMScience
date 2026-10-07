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
})();
