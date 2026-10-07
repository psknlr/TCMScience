// Shared by the tests: the relay with its counters on an in-memory SQLite (node:sqlite), and a fake upstream.
import { DatabaseSync } from "node:sqlite";
import { handle } from "../src/relay.js";
import { LimiterCore } from "../src/limiter.js";

/** The Durable Object SQL API's shape over node:sqlite. */
export function sqlStore() {
  const db = new DatabaseSync(":memory:");
  return {
    exec(query, ...params) {
      const stmt = db.prepare(query);
      const rows = /^\s*select|returning/i.test(query) ? stmt.all(...params) : (stmt.run(...params), []);
      return {
        toArray: () => rows,
        one: () => {
          if (rows.length !== 1) throw new Error(`expected one row, got ${rows.length}`);
          return rows[0];
        },
      };
    },
  };
}

export const KEY = "relay-test-key-not-a-real-one";
export const PAGE = "https://science.impf.ai";
export const NOON = Date.UTC(2026, 9, 7, 12, 0, 0);

export function sse(events, headers = {}) {
  const body = events.map((e) => `data: ${e}\n\n`).join("");
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream", ...headers } });
}

/** A response whose body arrives in pieces of `size` bytes: they split events, lines and multi-byte characters. */
export function cut(text, size = 7, headers = { "Content-Type": "text/event-stream" }) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(c) {
      for (let i = 0; i < bytes.length; i += size) c.enqueue(bytes.slice(i, i + size));
      c.close();
    },
  }), { status: 200, headers });
}

export function jsonReply(o, status = 200) {
  return new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } });
}

/**
 * The relay as the Worker binds it, with fakes: `gate` is the Durable Object's hit() on node:sqlite, `fetch` records
 * every upstream call, `defer` collects what the Worker would finish after answering (ctx.waitUntil).
 */
export function relay({ env = {}, upstream, now = () => NOON, burst, gateFails = false, gateAnswer } = {}) {
  const limiter = new LimiterCore(sqlStore());
  const calls = [];
  const pending = [];
  const bursts = [];
  const gates = [];
  const spends = [];
  const deps = {
    gate: async (args) => {
      gates.push(args);
      if (gateFails) throw new Error("Durable Object unreachable");
      if (gateAnswer !== undefined) return gateAnswer;
      return limiter.hit(args.who, args.kind, args.limits, now(), args.reserve);
    },
    spend: async (who, kind, tokens) => {
      spends.push(tokens);
      return limiter.spend(who, kind, tokens, now());
    },
    burst: async (key) => {
      bursts.push(key);
      return burst ? burst(key) : true;
    },
    defer: (p) => { pending.push(Promise.resolve(p).catch(() => {})); },
    fetch: async (url, init) => {
      calls.push({ url, init, body: JSON.parse(init.body) });
      return upstream ? upstream(url, init) : sse(['{"choices":[{"index":0,"delta":{"content":"好"}}],"model":"MiniMax-M3"}', "[DONE]"]);
    },
    now,
  };
  const fullEnv = { MINIMAX_API_KEY: KEY, ...env };
  const call = (path, init = {}, base = "https://science.impf.ai") => handle(new Request(`${base}${path}`, init), fullEnv, deps);
  const settle = async () => { while (pending.length) await Promise.all(pending.splice(0)); };
  const tokens = () => {
    const row = limiter.sql.exec("SELECT n FROM hits WHERE k LIKE 'k:chat:%'").toArray()[0];
    return row ? Number(row.n) : 0;
  };
  return { call, calls, limiter, settle, bursts, gates, spends, tokens, deps, env: fullEnv };
}

export const post = (body, headers = {}) => ({
  method: "POST",
  headers: { "Content-Type": "application/json", Accept: "text/event-stream", Origin: PAGE, "CF-Connecting-IP": "203.0.113.7", ...headers },
  body: typeof body === "string" ? body : JSON.stringify(body),
});

export const ask = { model: "Tao-S1", messages: [{ role: "user", content: "桂枝汤主治什么？" }], stream: true, max_completion_tokens: 16000, n: 3 };
export const tidy = { model: "Tao-S1", messages: [{ role: "user", content: "桂枝汤主治什么？" }], stream: true, stream_options: { include_usage: true }, max_completion_tokens: 4096 };

/** Every `data: {…}` line of an SSE text, parsed. */
export const events = (text) => text.split("\n").filter((l) => l.startsWith("data: {")).map((l) => JSON.parse(l.slice(6)));

/** Silence console.error while `fn` runs (the relay logs status codes on purpose). */
export async function quiet(fn) {
  const saved = console.error;
  console.error = () => {};
  try {
    return await fn();
  } finally {
    console.error = saved;
  }
}
