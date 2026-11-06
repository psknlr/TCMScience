// The runner's only way out during an eval: an HTTP proxy on loopback that records every request the runner tries to
// send off this machine and refuses it. The runner is started with HTTPS_PROXY/HTTP_PROXY pointing here (Python's
// urllib, which the connectors use, honours them), so the eval stays offline by construction, and a case can measure
// whether a call that carried an identifier ever tried to leave (an https request shows only its host: CONNECT is
// refused before any byte of the request, its path or its arguments is sent; attributeEgress names the calls each
// attempt may have been made for, by when it was made).

import { createServer } from "node:http";

/** start() → {url, attempts: [{at, method, host, url?}], since(n), stop()}. */
export async function startEgressRecorder() {
  const attempts = [];
  const server = createServer((req, res) => {
    // a plain-http request through the proxy carries its whole URL: keep the host only, never the query
    let host = "";
    try { host = new URL(req.url).host; } catch { host = String(req.headers.host || ""); }
    attempts.push({ at: Date.now(), method: req.method, host });
    res.writeHead(403, { "Content-Type": "text/plain" }).end("refused by the agent eval: no network here");
  });
  server.on("connect", (req, socket) => {
    attempts.push({ at: Date.now(), method: "CONNECT", host: String(req.url || "") });
    socket.on("error", () => {});
    socket.end("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  return {
    url,
    attempts,
    /** the attempts recorded after the first `n` */
    since: (n) => attempts.slice(n),
    stop: () => new Promise((resolve) => { server.closeAllConnections?.(); server.close(() => resolve()); }),
  };
}

/**
 * Which of the turn's tool calls each attempt may have been made for: `attempts` [{at, method, host}], `windows`
 * [{callId, start, end, job}] (when each call started and ended, as the harness saw them; `job` when it left a job
 * running) → [{method, host, calls: [callId]}]. The runner reaches the network only while it answers a call (the
 * attempt is recorded before the refusal it waits for, so before its call ends) or for a job left running after its call
 * returned. An attempt is the calls under way when it was made, and every call before it that left a job running; one
 * made while no call was under way is every call that had started by then. Calls that ran at the same time share their
 * attempts: a privacy check reads them on the side of failing.
 */
export function attributeEgress(attempts = [], windows = []) {
  return attempts.map((a) => {
    const started = windows.filter((w) => w.start <= a.at);
    const during = started.filter((w) => w.end == null || a.at <= w.end);
    const calls = during.length ? [...during, ...started.filter((w) => w.job && !during.includes(w))] : started;
    return { method: a.method, host: a.host, calls: [...new Set(calls.map((w) => w.callId))] };
  });
}

/** The variables that send a child process's HTTP(S) through `url`, loopback excepted (both spellings: urllib reads either). */
export function proxyEnv(url) {
  const local = "127.0.0.1,localhost,::1";
  return { HTTPS_PROXY: url, HTTP_PROXY: url, https_proxy: url, http_proxy: url, ALL_PROXY: "", all_proxy: "", NO_PROXY: local, no_proxy: local };
}
