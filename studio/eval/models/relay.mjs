// The relay-in-front driver: the real Tao-S1 relay (studio/edge/src/relay.js, its handler run in-process through the
// edge tests' helper, with the limiter on node:sqlite) between the real client and an upstream that plays the case's
// policy, as scripts/probe-relay-agent.mjs did. It counts what the relay sends upstream and how often it charges the
// visitor's limits, which is where a misread upstream error costs the visitor quota.

import { readFileSync } from "node:fs";
import path from "node:path";
import { PAGE, relay } from "../../edge/test/helpers.js";
import { STUDIO } from "../lib/util.mjs";
import { responseFor } from "./fault.mjs";
import { newMeter, providerFor } from "./scripted.mjs";

/** wrangler.toml's [vars]: what is deployed, so the relay here reads the owner's settings, not only its defaults. */
export function deployedVars(file = path.join(STUDIO, "edge", "wrangler.toml")) {
  const out = {};
  let inVars = false;
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    const s = line.trim();
    if (/^\[.*\]$/.test(s)) { inVars = s === "[vars]"; continue; }
    if (!inVars || !s || s.startsWith("#")) continue;
    const m = /^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([^#\s]+))/.exec(s);
    if (m) out[m[1]] = m[2] !== undefined ? JSON.parse(`"${m[2]}"`) : m[3] !== undefined ? m[3] : m[4];
  }
  return out;
}

/** {provider, meter, model} for one run of `policy` as the relay's upstream. */
export function relayDriver(policy, { env = {} } = {}) {
  const meter = newMeter();
  // the relay logs each upstream failure on purpose (the Worker's log); here that is noise in the eval's output
  const logged = console.error;
  console.error = () => {};
  const r = relay({
    env: { ...deployedVars(), ...env },
    upstream: async (url, init) => {
      let body = {};
      try { body = JSON.parse(init.body); } catch { /* the relay always sends JSON */ }
      const step = policy.next({ tools: Array.isArray(body.tools) && body.tools.length > 0, toolChoice: body.tool_choice });
      const res = responseFor(step, { model: body.model || "upstream" });
      return new Response(res.body, { status: res.status, headers: res.headers });
    },
  });
  // what the page sends: its own origin and a visitor address, as the Worker sees them
  const fetchImpl = async (url, init = {}) => {
    const body = typeof init.body === "string" ? init.body : "";
    let parsed = {};
    try { parsed = JSON.parse(body); } catch { /* counted as sent */ }
    meter.request(Buffer.byteLength(body), Array.isArray(parsed.tools) && parsed.tools.length > 0, parsed.tool_choice);
    const u = new URL(url);
    return r.call(u.pathname, { ...init, headers: { ...(init.headers || {}), Origin: PAGE, "CF-Connecting-IP": "203.0.113.7" } });
  };
  const provider = providerFor("tao", `${PAGE}/v1`, { fetch: fetchImpl });
  const settle = () => { meter.upstreamCalls = r.calls.length; meter.gateHits = r.gates.length; };
  const close = async () => {
    settle();
    try { await r.settle(); } finally { console.error = logged; }
  };
  return { kind: "relay", model: "scripted", provider, llm: undefined, meter, settle, close };
}
