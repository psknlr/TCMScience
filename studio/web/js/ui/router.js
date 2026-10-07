// Hash routes (DESIGN §4.1). Deep links work only on the device that holds the data: everything lives in this
// browser or on the local runner.
//   #/                           home: the last conversation, or a new one
//   #/p/:projectId               project overview (and a new conversation in it)
//   #/p/:projectId/c/:convId     conversation workspace
//   #/settings/:tab              models | compute | general | shortcuts
//   #/about  ·  #/catalog  ·  #/catalog/:entryId

export const SETTINGS_TABS = ["models", "compute", "general", "shortcuts"];

/** Parse a location hash into a route object. Unknown paths are {name: "notfound"}. */
export function parseRoute(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  const [pathPart, query = ""] = raw.split("?");
  const parts = pathPart.split("/").filter(Boolean).map((p) => {
    try { return decodeURIComponent(p); } catch { return p; }
  });
  const params = Object.fromEntries(new URLSearchParams(query));
  if (!parts.length) return { name: "home", params };
  const [a, b, c, d] = parts;
  if (a === "p" && b && !c) return { name: "project", projectId: b, params };
  if (a === "p" && b && c === "c" && d) return { name: "conversation", projectId: b, convId: d, params };
  if (a === "settings") return { name: "settings", tab: SETTINGS_TABS.includes(b) ? b : "models", params };
  if (a === "about" && !b) return { name: "about", params };
  if (a === "catalog") return { name: "catalog", entryId: b || null, params };
  return { name: "notfound", path: pathPart, params };
}

/** The hash for a route object. */
export function routeHref(route) {
  const enc = encodeURIComponent;
  switch (route?.name) {
    case "project": return `#/p/${enc(route.projectId)}`;
    case "conversation": return `#/p/${enc(route.projectId)}/c/${enc(route.convId)}`;
    case "settings": return `#/settings/${SETTINGS_TABS.includes(route.tab) ? route.tab : "models"}`;
    case "about": return "#/about";
    case "catalog": return route.entryId ? `#/catalog/${enc(route.entryId)}` : "#/catalog";
    default: return "#/";
  }
}

export function sameRoute(a, b) {
  return routeHref(a) === routeHref(b);
}

/**
 * Listen to hash changes; onChange(route, previous). navigate() pushes (or replaces) a route without firing twice.
 */
export class HashRouter {
  constructor(onChange) {
    this.onChange = onChange;
    this.current = parseRoute(location.hash);
    this._onHash = () => {
      const prev = this.current;
      this.current = parseRoute(location.hash);
      this.onChange?.(this.current, prev);
    };
    window.addEventListener("hashchange", this._onHash);
  }

  navigate(routeOrHref, { replace = false } = {}) {
    const href = typeof routeOrHref === "string" ? routeOrHref : routeHref(routeOrHref);
    if (href === location.hash || (href === "#/" && !location.hash)) {
      // same place: still tell the app (it may need to refresh), without a history entry
      this._onHash();
      return;
    }
    if (replace) {
      history.replaceState(history.state, "", `${location.pathname}${location.search}${href}`);
      this._onHash();
    } else {
      location.hash = href;
    }
  }

  destroy() {
    window.removeEventListener("hashchange", this._onHash);
  }
}
