// The page side of the Worker: http → https, and the security headers on whatever the Worker itself answers (/v1/*,
// and the fallback for a path that matches no file). Files Cloudflare serves directly get the same headers from
// studio/edge/_headers; test/site.test.js checks that the two say the same.

const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

export const HSTS_MAX_AGE = 15552000; // 180 days, as the impf.ai zone's own HSTS setting (which overrides this)

// No camera, microphone, location, payment or device APIs. `local-network-access` is deliberately not listed: the page
// must be able to reach the user's own runner and local model servers on 127.0.0.1.
export const PERMISSIONS_POLICY = [
  "accelerometer=()", "autoplay=()", "browsing-topics=()", "camera=()", "display-capture=()", "encrypted-media=()",
  "fullscreen=(self)", "geolocation=()", "gyroscope=()", "hid=()", "magnetometer=()", "microphone=()", "midi=()",
  "payment=()", "publickey-credentials-get=()", "serial=()", "usb=()", "xr-spatial-tracking=()",
].join(", ");

// Report-only for now: a violation shows in the browser's console and blocks nothing. Pyodide comes from jsDelivr and
// compiles WebAssembly; model calls go to any https API the user names, the runner and local model servers to loopback;
// runner reports are framed from loopback or blob: URLs.
export const CSP_REPORT_ONLY = [
  "default-src 'self'",
  "script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'",
  "connect-src 'self' https: http://127.0.0.1:* http://localhost:* ws://127.0.0.1:*",
  "worker-src 'self' blob:",
  "img-src 'self' data: blob:",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' https://cdn.jsdelivr.net data:",
  "frame-src 'self' http://127.0.0.1:* http://localhost:* blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'self'",
].join("; ");

/** The headers every answer carries. */
export function commonHeaders(env = {}) {
  const hsts = env.HSTS_MAX_AGE !== undefined && env.HSTS_MAX_AGE !== "" ? Number(env.HSTS_MAX_AGE) : HSTS_MAX_AGE;
  return {
    ...(Number.isFinite(hsts) && hsts > 0 ? { "Strict-Transport-Security": `max-age=${Math.floor(hsts)}` } : {}),
    "X-Content-Type-Options": "nosniff",
    // NOT no-referrer: with it a browser sends `Origin: null`, and the relay refuses the page's own model calls
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": PERMISSIONS_POLICY,
    "Cross-Origin-Resource-Policy": "same-origin",
  };
}

/** The headers an HTML page (and a worker script) carries: cross-origin isolation, so that Pyodide can be interrupted
 * through a SharedArrayBuffer; no framing by other sites; the content policy. */
export const PAGE_HEADERS = {
  "X-Frame-Options": "SAMEORIGIN",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Content-Security-Policy-Report-Only": CSP_REPORT_ONLY,
};

/** `http:` (other than loopback) → a permanent redirect to https; otherwise null. */
export function redirectHttps(url) {
  if (url.protocol !== "http:" || LOCAL.has(url.hostname)) return null;
  const to = new URL(url);
  to.protocol = "https:";
  return new Response(null, { status: 301, headers: { Location: to.href, "Cache-Control": "max-age=3600" } });
}

/** A copy of `response` with the security headers (a fetched Response's headers are immutable). */
export function secure(response, env = {}) {
  const res = new Response(response.body, response);
  for (const [k, v] of Object.entries(commonHeaders(env))) res.headers.set(k, v);
  if ((res.headers.get("Content-Type") || "").toLowerCase().startsWith("text/html")) {
    for (const [k, v] of Object.entries(PAGE_HEADERS)) res.headers.set(k, v);
  }
  return res;
}
