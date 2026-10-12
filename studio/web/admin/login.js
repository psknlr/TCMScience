// The administrators' sign-in page (served to anyone at /admin/ without a session). The form posts the token to
// /api/admin/session, which answers with a session cookie and a redirect back to /admin/, or with ?e=token|locked.
// It works without this script; the script only says why the last attempt failed and switches the language.

const WORDS = {
  zh: {
    doc: "TCMScience Studio · 管理员登录",
    title: "管理员登录",
    note: "运营统计只对持有管理令牌的人开放。令牌在服务器端核对，这个页面不保存它；登录后浏览器只保留一个会过期的会话（默认一小时）。",
    label: "管理令牌",
    submit: "登录",
    other: "English",
    token: "令牌不对，请重试。",
    locked: "失败次数过多，这个地址已暂停登录 15 分钟。",
    short: "管理令牌至少 32 个字符。",
  },
  en: {
    doc: "TCMScience Studio · Administrator sign-in",
    title: "Administrator sign-in",
    note: "The analytics are open only to holders of the admin token. The token is checked on the server and this page does not keep it; once signed in, the browser holds a session that expires (after an hour by default).",
    label: "Admin token",
    submit: "Sign in",
    other: "中文",
    token: "That token is not right. Try again.",
    locked: "Too many failed attempts: sign-in from this address is paused for 15 minutes.",
    short: "The admin token has at least 32 characters.",
  },
};
const KEY = "tcmstudio.admin.lang";

function initial() {
  try {
    const q = new URLSearchParams(location.search).get("lang");
    if (q === "zh" || q === "en") return q;
    const saved = localStorage.getItem(KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch { /* storage blocked */ }
  return /^zh\b/i.test(navigator.language || "zh") ? "zh" : "en";
}

let lang = initial();
const reason = new URLSearchParams(location.search).get("e");

function apply() {
  const w = WORDS[lang];
  document.documentElement.lang = lang === "zh" ? "zh-Hans" : "en";
  document.title = w.doc;
  for (const node of document.querySelectorAll("[data-t]")) node.textContent = w[node.dataset.t];
  const other = document.getElementById("lang");
  other.textContent = w.other;
  other.lang = lang === "zh" ? "en" : "zh-Hans";
  const error = document.getElementById("error");
  const message = reason === "token" || reason === "locked" ? w[reason] : "";
  error.textContent = message;
  error.hidden = !message;
}

function start() {
  apply();
  // the error stays on screen; the address loses it, so a reload starts clean
  if (reason) history.replaceState(null, "", "/admin/");
  document.getElementById("lang").addEventListener("click", () => {
    lang = lang === "zh" ? "en" : "zh";
    try { localStorage.setItem(KEY, lang); } catch { /* storage blocked */ }
    apply();
  });
  const input = document.getElementById("token");
  document.getElementById("login").addEventListener("submit", (e) => {
    input.value = input.value.replace(/\s+/g, "");
    if (input.value.length < 32) {
      e.preventDefault();
      const error = document.getElementById("error");
      error.textContent = WORDS[lang].short;
      error.hidden = false;
      input.focus();
    }
  });
  input.focus();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
