// Module entry. boot.js has already applied the theme and language before first paint; this starts the app.
import { App } from "./ui/app.js";

const root = document.getElementById("app");
const app = new App();
app.boot(root).catch((err) => {
  console.error(err);
  // a failure to start is shown, never a blank page
  root.removeAttribute("aria-busy");
  const zh = document.documentElement.lang !== "en";
  const box = document.createElement("div");
  box.className = "boot-splash";
  const p = document.createElement("p");
  p.className = "boot-note";
  p.textContent = zh ? `TCMScience Studio 未能启动：${err?.message || err}。请刷新页面重试。` : `TCMScience Studio could not start: ${err?.message || err}. Reload the page to try again.`;
  box.append(p);
  root.replaceChildren(box);
});

// for the end-to-end tests and the console; nothing in the page depends on it
globalThis.__studio = app;
