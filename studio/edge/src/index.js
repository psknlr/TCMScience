// TCMScience Studio — the one Worker for https://science.impf.ai.
//   The app's files: Workers Static Assets (binding ASSETS, directory ../_site). Cloudflare serves them without
//     running this Worker (free and not counted), with the headers in _headers. A path that matches no file comes
//     here: a browser navigating to one of the app's routes gets index.html, anything else 404.
//   /v1/*: the Tao-S1 relay (relay.js). /api/sources/*: registered read-only database transport (sources.js).
//   The Worker runs first on both API namespaces (run_worker_first in wrangler.toml).
//   Limiter: the counters behind the relay's limits, one SQLite-backed Durable Object for the whole service.
import { DurableObject } from "cloudflare:workers";
import { LimiterCore } from "./limiter.js";
import { handle } from "./relay.js";
import { handleSources } from "./sources.js";
import { redirectHttps, secure } from "./site.js";

export class Limiter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new LimiterCore(ctx.storage.sql);
  }

  hit(who, kind, limits, reserve) {
    return this.core.hit(who, kind, limits, Date.now(), reserve);
  }

  spend(who, kind, tokens) {
    return this.core.spend(who, kind, tokens);
  }

  sourceSlot(host, rps, prefixes) {
    return this.core.sourceSlot(host, rps, Date.now(), prefixes);
  }
}

/** The relay's dependencies, bound to this Worker's bindings. */
export function relayDeps(env, ctx) {
  const limiter = () => env.LIMITER.get(env.LIMITER.idFromName("limits")); // one object: exact counts for everyone
  return {
    // throws when the object cannot be reached: the relay then refuses the call (fail closed)
    gate: ({ who, kind, limits, reserve }) => limiter().hit(who, kind, limits, reserve),
    spend: (who, kind, tokens) => limiter().spend(who, kind, tokens),
    pace: (host, rps, prefixes) => limiter().sourceSlot(host, rps, prefixes),
    // the ten-second gate is a cheap, approximate pre-filter in front of the Durable Object, which still counts every
    // call: if the binding itself fails, the call goes on to the object rather than being refused
    burst: async (key) => {
      if (!env.BURST) return true;
      try {
        return (await env.BURST.limit({ key })).success;
      } catch (err) {
        console.error("burst", err?.name || "Error");
        return true;
      }
    },
    defer: (promise) => ctx.waitUntil(Promise.resolve(promise).catch((err) => console.error("deferred", err?.name || "Error"))),
    fetch: (input, init) => fetch(input, init),
    now: () => Date.now(),
  };
}

/** Whether a request is a browser navigating to a page (a deep link into the app), rather than fetching a file. */
export function isNavigation(request) {
  const mode = request.headers.get("Sec-Fetch-Mode");
  if (mode) return mode === "navigate";
  return (request.headers.get("Accept") || "").includes("text/html"); // browsers without Fetch Metadata
}

/** A path that matched no file (not_found_handling = "none" sends it here). A browser navigating to one of the app's
 * own routes gets the app; anything else (a script, a JSON file the app asked for) is told the truth: 404, never the
 * app's HTML in its place. */
async function missing(request, env, url) {
  const redirect = redirectHttps(url);
  if (redirect) return redirect;
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method Not Allowed\n", { status: 405, headers: { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=utf-8" } });
  }
  if (isNavigation(request) && env.ASSETS) {
    const page = await env.ASSETS.fetch(new Request(new URL("/", url), { method: request.method, headers: request.headers }));
    if (page.ok) return page;
  }
  return new Response(request.method === "HEAD" ? null : "Not Found\n", {
    status: 404, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-cache" },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/v1" || url.pathname.startsWith("/v1/")) {
      return secure(await handle(request, env, relayDeps(env, ctx)), env);
    }
    if (url.pathname === "/api/sources" || url.pathname.startsWith("/api/sources/")) {
      return secure(await handleSources(request, env, relayDeps(env, ctx)), env);
    }
    return secure(await missing(request, env, url), env);
  },
};
