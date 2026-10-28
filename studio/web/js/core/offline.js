// The service worker from the page's side (studio/web/sw.js): register it, tell the page when a new version waits,
// and switch to it only when the person asks (in this tab or another: every open page then reloads into the new
// version, once that loses nothing). Without a service worker (an old browser, a private window that refuses
// one, a page that is not a secure context) the app works as before, online.
//
// `?nosw` in the address unregisters it and deletes its caches: a way out if a cached version ever misbehaves.

const CACHE_PREFIXES = ["tcmstudio-shell-", "tcmstudio-pyodide-", "tcmstudio-cdn-"];

/**
 * Register /sw.js. opts: {nav, location, caches, onUpdate(apply) — called when a new version is installed and
 * waiting; apply() switches to it and reloads; busy() — whether this page holds what a reload would lose, asked when
 * another tab has switched to a new version; onReplaced(reload) — told when the page waits for that}. →
 * {registration, state: "registered" | "unsupported" | "removed" | "skipped" | "failed", reason?}.
 */
export async function registerServiceWorker({ nav = globalThis.navigator, location = globalThis.location, caches = globalThis.caches, onUpdate, busy, onReplaced } = {}) {
  const sw = nav?.serviceWorker;
  if (!sw?.register) return { registration: null, state: "unsupported" };
  const q = new URLSearchParams(location?.search || "");
  if (q.has("nosw")) return removeServiceWorker({ nav, caches });
  if (globalThis.isSecureContext === false) return { registration: null, state: "unsupported", reason: "not a secure context" };
  // automated browsers (the end-to-end tests) get it only when they ask: a cache would hide what they test
  if (nav.webdriver && !q.has("sw")) return { registration: null, state: "skipped", reason: "automated browser" };
  let registration;
  try {
    registration = await sw.register("/sw.js", { scope: "/", updateViaCache: "none" });
  } catch (err) {
    return { registration: null, state: "failed", reason: String(err?.message || err) };
  }
  watchForUpdates(registration, sw, location, { onUpdate, busy, onReplaced });
  return { registration, state: "registered" };
}

// How often a page another tab switched to a new version looks again whether it can reload without a loss.
export const IDLE_CHECK_MS = 3000;

function watchForUpdates(registration, sw, location, { onUpdate, busy, onReplaced }) {
  if (typeof onUpdate !== "function") return;
  let offered = false;
  let asked = false; // this page asked for the switch
  let waiting = null;
  let reloading = false;
  const reload = () => {
    if (reloading) return;
    reloading = true;
    clearInterval(waiting);
    location.reload();
  };
  // A new version that takes over this page reloads it, whichever tab asked for it: activation switches every page of
  // the site at once and deletes the old version's cache, so a page left as it is would import the new build's modules
  // into the old build's. Asked for here: at once. Asked for in another tab: as soon as nothing here would be lost
  // (busy(): an answer running, text not sent…), looked at again every IDLE_CHECK_MS; meanwhile onReplaced(reload) lets
  // the person know, and reload sooner. A page no version ran (the very first install) has nothing to mix.
  if (sw.controller) {
    sw.addEventListener?.("controllerchange", () => {
      if (asked || typeof busy !== "function" || !busy()) return reload();
      if (waiting !== null) return;
      waiting = setInterval(() => { if (!busy()) reload(); }, IDLE_CHECK_MS);
      if (typeof onReplaced === "function") onReplaced(reload);
    });
  }
  const apply = (worker) => () => {
    // another tab has switched to it already: no controllerchange is coming
    if (sw.controller === worker) {
      reload();
      return;
    }
    asked = true;
    worker.postMessage({ type: "tcmstudio.skip-waiting" });
  };
  const offer = (worker) => {
    // only an update replaces a version this page runs; the very first install takes over at the next visit
    if (offered || !worker || !sw.controller) return;
    offered = true;
    onUpdate(apply(worker));
  };
  if (registration.waiting) offer(registration.waiting);
  registration.addEventListener?.("updatefound", () => {
    const worker = registration.installing;
    worker?.addEventListener?.("statechange", () => { if (worker.state === "installed") offer(worker); });
  });
}

/** Unregister every service worker of this origin and delete the caches it kept. */
export async function removeServiceWorker({ nav = globalThis.navigator, caches = globalThis.caches } = {}) {
  try {
    const regs = (await nav.serviceWorker.getRegistrations?.()) || [];
    for (const r of regs) {
      // first ask it to stand down: it keeps serving the open pages until they close, and must not refill a cache
      for (const w of [r.active, r.waiting, r.installing]) {
        try { w?.postMessage({ type: "tcmstudio.remove" }); } catch { /* gone already */ }
      }
      await r.unregister();
    }
    if (caches?.keys) {
      for (const name of await caches.keys()) {
        if (CACHE_PREFIXES.some((p) => name.startsWith(p))) await caches.delete(name);
      }
    }
    return { registration: null, state: "removed" };
  } catch (err) {
    return { registration: null, state: "failed", reason: String(err?.message || err) };
  }
}
