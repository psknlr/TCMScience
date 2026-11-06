// The runner's only way out during an eval: an HTTP proxy on loopback that records every request the runner tries to
// send off this machine and refuses it. The runner is started with HTTPS_PROXY/HTTP_PROXY pointing here (Python's
// urllib, which the connectors use, honours them), so the eval stays offline by construction, and a case can measure
// whether a call that carried an identifier ever tried to leave (an https request shows only its host: CONNECT is
// refused before any byte of the request, its path or its arguments is sent).

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

/** The variables that send a child process's HTTP(S) through `url`, loopback excepted (both spellings: urllib reads either). */
export function proxyEnv(url) {
  const local = "127.0.0.1,localhost,::1";
  return { HTTPS_PROXY: url, HTTP_PROXY: url, https_proxy: url, http_proxy: url, ALL_PROXY: "", all_proxy: "", NO_PROXY: local, no_proxy: local };
}
