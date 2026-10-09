// The processes an end-to-end test talks to: the real runner (`python3 -m tcmstudio serve`), a static server for the
// built site (the "just open the page" mode without the Worker), and `wrangler dev` of studio/edge with the Tao-S1
// relay pointed at a mock upstream. Every one listens on an ephemeral loopback port and is stopped by the test that
// started it.

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { UPSTREAM_MODEL, UPSTREAM_VENDOR } from "../mock-llm.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const E2E = path.resolve(HERE, "..");
export const STUDIO = path.resolve(E2E, "..");
export const REPO = path.resolve(STUDIO, "..");
export const PYTHON = process.env.PYTHON || "python3";
export const WRANGLER = process.env.WRANGLER || "wrangler@4.148.0";

export function scratch(prefix) {
  const base = process.env.STUDIO_E2E_TMP || tmpdir();
  mkdirSync(base, { recursive: true });
  return mkdtempSync(path.join(base, `${prefix}-`));
}

export function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.once("error", reject);
    s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); });
  });
}

export async function waitFor(fn, { timeoutMs = 30000, everyMs = 200, what = "condition" } = {}) {
  const until = Date.now() + timeoutMs;
  let last;
  while (Date.now() < until) {
    try {
      const v = await fn();
      if (v) return v;
    } catch (err) { last = err; }
    await new Promise((r) => setTimeout(r, everyMs));
  }
  throw new Error(`timed out waiting for ${what}${last ? `: ${last.message}` : ""}`);
}

/** The built site: $STUDIO_SITE (CI builds it first), else one build per test run into a temporary directory. */
export function siteDir() {
  if (process.env.STUDIO_SITE && existsSync(path.join(process.env.STUDIO_SITE, "index.html"))) return process.env.STUDIO_SITE;
  const out = path.join(scratch("tcmstudio-site"), "site");
  const r = spawnSync(PYTHON, [path.join(STUDIO, "scripts", "build_web.py"), "--out", out], { cwd: REPO, encoding: "utf8" });
  if (r.status !== 0) throw new Error(`studio/scripts/build_web.py failed:\n${r.stdout}\n${r.stderr}`);
  if (!existsSync(path.join(out, "_headers"))) writeFileSync(path.join(out, "_headers"), readFileSync(path.join(STUDIO, "edge", "_headers")));
  return out;
}

// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g;

function collect(proc, sink) {
  proc.stdout?.setEncoding("utf8");
  proc.stderr?.setEncoding("utf8");
  proc.stdout?.on("data", (d) => { sink.text += d.replace(ANSI, ""); });
  proc.stderr?.on("data", (d) => { sink.text += d.replace(ANSI, ""); });
}

// children run in their own process groups (so a stop takes their subprocesses too); if this process ends without
// stopping them (an interrupted run), they are taken down with it
const children = new Set();
process.once("exit", () => {
  for (const proc of children) { try { process.kill(-proc.pid, "SIGKILL"); } catch { /* gone */ } }
});

function stopper(proc) {
  children.add(proc);
  proc.once("exit", () => children.delete(proc));
  return () => new Promise((resolve) => {
    if (proc.exitCode !== null || proc.signalCode) return resolve();
    proc.once("exit", () => resolve());
    try { process.kill(-proc.pid, "SIGTERM"); } catch { try { proc.kill("SIGTERM"); } catch { /* gone */ } }
    setTimeout(() => { try { process.kill(-proc.pid, "SIGKILL"); } catch { /* gone */ } }, 5000).unref();
  });
}

/**
 * The real runner, as a user starts it (`python3 -m tcmstudio serve --no-browser --no-token`), with its own home on
 * an ephemeral port. Returns {url, home, pairUrl, log(), stop()}.
 */
export async function startRunner({ home = scratch("tcmstudio-home"), args = [], web } = {}) {
  const argv = ["-m", "tcmstudio", "serve", "--no-browser", "--no-token", "--port", "0", "--home", home, ...(web ? ["--web", web] : []), ...args];
  const env = { ...process.env, PYTHONUNBUFFERED: "1" };
  const proc = spawn(PYTHON, argv, { cwd: REPO, env, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  const sink = { text: "" };
  collect(proc, sink);
  const stop = stopper(proc);
  let url;
  try {
    url = await waitFor(() => {
      if (proc.exitCode !== null) throw new Error(`the runner exited (${proc.exitCode}):\n${sink.text}`);
      return /本机地址 Local\s+(http:\/\/127\.0\.0\.1:\d+)\//.exec(sink.text)?.[1];
    }, { timeoutMs: 60000, what: "the runner's banner" });
    await waitFor(async () => (await fetch(`${url}/api/health`)).ok, { what: "the runner's /api/health" });
  } catch (err) {
    await stop();
    throw err;
  }
  const pairUrl = /本机页面 Local app\s+(\S+)/.exec(sink.text)?.[1] || `${url}/`;
  return { url, home, pairUrl, log: () => sink.text, stop };
}

// ------------------------------------------------------------------------------------------------ static site

const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
  ".png": "image/png", ".webmanifest": "application/manifest+json", ".woff2": "font/woff2", ".wasm": "application/wasm",
  ".gz": "application/gzip", ".txt": "text/plain; charset=utf-8",
};

/** Cloudflare's _headers format: a path pattern, then indented "Name: value" lines. */
export function parseHeaders(text) {
  const rules = [];
  let cur = null;
  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    if (!/^\s/.test(raw)) { cur = { pattern: raw.trim(), set: [] }; rules.push(cur); continue; }
    const i = raw.indexOf(":");
    if (cur && i > 0) cur.set.push([raw.slice(0, i).trim(), raw.slice(i + 1).trim()]);
  }
  return rules.map((r) => ({ ...r, re: new RegExp(`^${r.pattern.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`) }));
}

/**
 * The built site served the way Workers Static Assets serves it: the site's own _headers (COOP/COEP, CSP), a
 * navigation to an unknown path gets index.html, anything else missing is a 404. /v1/health answers like the relay
 * deployed without its key (Tao-S1 off), which is what a page sees before the owner adds the secret.
 */
export function startStatic(site, { sources = null } = {}) {
  const rules = existsSync(path.join(site, "_headers")) ? parseHeaders(readFileSync(path.join(site, "_headers"), "utf8")) : [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    if (sources && url.pathname.startsWith("/api/sources/")) {
      Promise.resolve(sources(req, res)).catch(() => res.writeHead(500).end("source test gateway failed"));
      return;
    }
    const headers = {};
    for (const r of rules) if (r.re.test(url.pathname)) for (const [k, v] of r.set) headers[k] = v;
    if (url.pathname === "/v1/health") {
      res.writeHead(200, { ...headers, "Content-Type": "application/json" }).end(JSON.stringify({
        ok: false, service: "tcmscience-studio", version: "e2e", model: "Tao-S1", models: ["Tao-S1"], max_output_tokens: 8192,
        limits: { per_minute: 40, per_day: 1200, tokens_per_day: 10000000 },
      }));
      return;
    }
    let file = path.join(site, decodeURIComponent(url.pathname));
    if (!file.startsWith(site) || path.basename(file) === "_headers") { res.writeHead(404, headers).end(); return; }
    if (existsSync(file) && statSync(file).isDirectory()) file = path.join(file, "index.html");
    if (!existsSync(file)) {
      if (req.headers["sec-fetch-mode"] === "navigate") file = path.join(site, "index.html");
      else { res.writeHead(404, headers).end("not found"); return; }
    }
    res.writeHead(200, { ...headers, "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => {
    const url = `http://127.0.0.1:${server.address().port}`;
    resolve({ url, stop: () => new Promise((r) => { server.closeAllConnections?.(); server.close(() => r()); }) });
  }));
}

// ------------------------------------------------------------------------------------------------ the Worker

/**
 * Why a failed `startWorker` may be a skip rather than a failure: wrangler is fetched from npm at its pinned version, so
 * without the registry there is no Worker to test. Null (fail) for anything else, and always in CI, where the build
 * job's dry run has already fetched the same wrangler: there a Worker that cannot start (an unknown compatibility flag,
 * a top-level error, a Node built-in without nodejs_compat) is the change's fault and must not pass as a skip.
 */
export function workerSkipReason(err, env = process.env) {
  if (env.CI && !/^(0|false)$/i.test(String(env.CI).trim())) return null;
  const text = String(err?.message ?? err ?? "");
  // npm's own error lines: no network, or a proxy or registry that refuses (E403/E405/E407, 5xx)
  const registry = /npm (?:ERR!|error) (?:code |errno )?(?:ENOTFOUND|ETIMEDOUT|EAI_AGAIN|ECONNREFUSED|ECONNRESET|E4\d\d|E5\d\d)\b|npm (?:ERR!|error) (?:FetchError: |network )?request to \S+ failed|npm (?:ERR!|error) network\b/i;
  if (!registry.test(text)) return null;
  return `wrangler could not be fetched from npm (no registry here): ${text.split("\n")[0]}`;
}

/**
 * `wrangler dev` of studio/edge (its own src/index.js and wrangler.toml settings) with the assets from `site` and the
 * relay's upstream at `upstream` (a mock). The config is a copy in a temporary directory with absolute paths, so
 * nothing is written into the repository. It listens with https (a self-signed certificate) because wrangler dev
 * presents requests to the Worker as https://science.impf.ai, the route in wrangler.toml, and the relay refuses plain
 * http from anywhere but loopback. Returns {url, log(), stop()} or throws with wrangler's output.
 */
export async function startWorker({ site, upstream, key = "e2e-upstream-key", vars = {} }) {
  const dir = scratch("tcmstudio-worker");
  const edge = path.join(STUDIO, "edge");
  let toml = readFileSync(path.join(edge, "wrangler.toml"), "utf8");
  toml = toml.replace(/^main = .*$/m, `main = ${JSON.stringify(path.join(edge, "src", "index.js"))}`);
  toml = toml.replace(/^directory = .*$/m, `directory = ${JSON.stringify(site)}`);
  writeFileSync(path.join(dir, "wrangler.toml"), toml);
  // .dev.vars overrides wrangler.toml's [vars]: pin what the specs depend on (the relay on, the mock's model and
  // reasoning format, the output cap, no limits), so the owner's committed pause or limit change does not fail them
  const devVars = {
    RELAY: "on", MODELS: UPSTREAM_MODEL, FORMAT_PREFIX: `${UPSTREAM_VENDOR}-`, MAX_OUTPUT_TOKENS: "8192",
    PER_MINUTE: "0", PER_DAY: "0", TOTAL_PER_DAY: "0", TOKENS_PER_DAY: "0", TOTAL_TOKENS_PER_DAY: "0",
    MINIMAX_API_KEY: key, UPSTREAM_BASE: `${upstream}/v1`, ...vars,
  };
  writeFileSync(path.join(dir, ".dev.vars"), Object.entries(devVars).map(([k, v]) => `${k}=${v}`).join("\n") + "\n");
  const port = await freePort();
  const argv = ["--yes", WRANGLER, "dev", "-c", path.join(dir, "wrangler.toml"), "--ip", "127.0.0.1", "--port", String(port),
    "--local-protocol", "https", "--persist-to", path.join(dir, "state"), "--show-interactive-dev-session=false"];
  const proc = spawn("npx", argv, { cwd: dir, env: { ...process.env, WRANGLER_SEND_METRICS: "false", CI: "1", NO_COLOR: "1", FORCE_COLOR: "0" }, detached: true, stdio: ["ignore", "pipe", "pipe"] });
  const sink = { text: "" };
  collect(proc, sink);
  const stop = stopper(proc);
  let url;
  try {
    url = await waitFor(() => {
      if (proc.exitCode !== null) throw new Error(`wrangler exited (${proc.exitCode})`);
      return /Ready on (https:\/\/\S+)/.exec(sink.text)?.[1];
    }, { timeoutMs: 180000, everyMs: 300, what: "wrangler dev" });
  } catch (err) {
    await stop();
    throw new Error(`${err.message}\n${sink.text.slice(-4000)}`);
  }
  return { url: url.replace(/\/$/, ""), log: () => sink.text, stop };
}
