// The fault driver: an OpenAI-compatible chat server on loopback that the real client (llm/openai.js, over real HTTP)
// talks to. Each request is answered by the case's policy: an ordinary step is streamed the way services stream it
// (role first, text and tool-call arguments in fragments, usage last, [DONE]); a fault step answers with what a failing
// service sends: an HTTP error with its body and headers, an error event inside a 200 stream, a stream that stalls after
// its first chunk, a base_resp code, finish_reason length. Played with a gold policy and nothing else, it is the echo
// mode the cassette recorder records through.

import { createServer } from "node:http";
import { Readable } from "node:stream";
import { isFault } from "../lib/policy.mjs";
import { newMeter, providerFor } from "./scripted.mjs";

const enc = new TextEncoder();

/** start() → {url, use(policy, meter), stop()}: one server for the whole run; each case's run says who answers. */
export async function startFaultServer() {
  let current = null; // {policy, meter}
  const open = new Set();
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const raw = Buffer.concat(chunks);
    if (req.method !== "POST" || !/\/chat\/completions$/.test(req.url || "")) { res.writeHead(404).end(); return; }
    let body = {};
    try { body = JSON.parse(raw.toString("utf8")); } catch { /* answered below */ }
    if (!current) { res.writeHead(503, { "Content-Type": "application/json" }).end('{"error":{"message":"no case is running","type":"eval"}}'); return; }
    const offered = Array.isArray(body.tools) && body.tools.length > 0;
    current.meter.request(raw.length, offered, body.tool_choice);
    const step = current.policy.next({ tools: offered, toolChoice: body.tool_choice });
    const r = responseFor(step, { model: body.model || "eval-model" });
    res.writeHead(r.status, Object.fromEntries(r.headers));
    if (!r.body) { res.end(); return; }
    const stream = Readable.fromWeb(r.body);
    open.add(stream);
    res.on("close", () => { stream.destroy(); open.delete(stream); });
    stream.on("error", () => res.destroy());
    stream.pipe(res);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  return {
    url,
    use(policy, meter) { current = policy ? { policy, meter } : null; },
    stop: () => new Promise((resolve) => {
      for (const s of open) s.destroy();
      server.closeAllConnections?.();
      server.close(() => resolve());
    }),
  };
}

/** {provider, meter, model} for one run of `policy` against the server (the real openai.js client, no `llm`). */
export function faultDriver(server, policy, { preset = "tao", provider: extra = {} } = {}) {
  const meter = newMeter();
  server.use(policy, meter);
  const provider = providerFor(preset, `${server.url}/v1`, extra);
  return { kind: "fault", model: "scripted", provider, llm: undefined, meter, close: async () => server.use(null) };
}

/**
 * The Response a policy step gives (also the relay's upstream answer): {status, headers, body: ReadableStream|null}.
 * `model` is echoed in the chunks, as services do.
 */
export function responseFor(step, { model = "eval-model", stall = null } = {}) {
  const sseHeaders = new Headers({ "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
  if (step.http) {
    const h = new Headers({ "Content-Type": step.http.json !== undefined ? "application/json" : "text/plain", ...(step.http.headers || {}) });
    const text = step.http.json !== undefined ? JSON.stringify(step.http.json) : String(step.http.text ?? "");
    return { status: step.http.status || 500, headers: h, body: streamOf([text]) };
  }
  if (step.base_resp) {
    const payload = { id: "eval", choices: [], base_resp: step.base_resp };
    if (step.as === "json") return { status: 200, headers: new Headers({ "Content-Type": "application/json" }), body: streamOf([JSON.stringify(payload)]) };
    return { status: 200, headers: sseHeaders, body: streamOf([data(payload), "data: [DONE]\n\n"]) };
  }
  if (step.stall) {
    const first = [data(chunk(model, { role: "assistant", content: step.stall.after_text || "" }))];
    return { status: 200, headers: sseHeaders, body: streamOf(first, { hang: true, onHang: stall }) };
  }
  if (step.stream_error) {
    const pieces = [];
    if (step.after_text) pieces.push(data(chunk(model, { role: "assistant", content: step.after_text })));
    pieces.push(data(step.stream_error));
    return { status: 200, headers: sseHeaders, body: streamOf(pieces) };
  }
  if (isFault(step)) throw new Error(`unknown fault step: ${JSON.stringify(step)}`);
  return { status: 200, headers: sseHeaders, body: streamOf(sseOf(step, model)) };
}

/** The SSE lines of an ordinary step, cut the way real services cut them. */
export function sseOf(step, model) {
  const out = [data(chunk(model, { role: "assistant", content: "" }))];
  const piece = (s, n) => { const parts = []; for (let i = 0; i < s.length; i += n) parts.push(s.slice(i, i + n)); return parts; };
  for (const p of piece(step.reasoning || "", 24)) out.push(data(chunk(model, { reasoning_content: p })));
  for (const p of piece(step.text || "", 16)) out.push(data(chunk(model, { content: p })));
  (step.calls || []).forEach((c, index) => {
    out.push(data(chunk(model, { tool_calls: [{ index, id: c.id, type: "function", function: { name: c.name, arguments: "" } }] })));
    for (const p of piece(JSON.stringify(c.args ?? {}), 24)) out.push(data(chunk(model, { tool_calls: [{ index, function: { arguments: p } }] })));
  });
  const finish = step.finish || (step.calls?.length ? "tool_calls" : "stop");
  out.push(data({ ...chunk(model, {}), choices: [{ index: 0, delta: {}, finish_reason: finish }] }));
  const usage = step.usage || { input: 100, output: 10 };
  out.push(data({ id: "eval", object: "chat.completion.chunk", model, choices: [], usage: { prompt_tokens: usage.input, completion_tokens: usage.output, total_tokens: usage.input + usage.output } }));
  out.push("data: [DONE]\n\n");
  return out;
}

const chunk = (model, delta) => ({ id: "eval", object: "chat.completion.chunk", model, choices: [{ index: 0, delta }] });
const data = (o) => `data: ${JSON.stringify(o)}\n\n`;

/** A byte stream of `pieces`, one per read; with hang, it then stays open (a stalled service) until it is cancelled. */
function streamOf(pieces, { hang = false, onHang } = {}) {
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < pieces.length) { controller.enqueue(enc.encode(pieces[i++])); return undefined; }
      if (!hang) { controller.close(); return undefined; }
      onHang?.();
      return new Promise(() => {}); // never: the reader waits until the client gives up
    },
  });
}
