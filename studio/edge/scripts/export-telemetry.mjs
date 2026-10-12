#!/usr/bin/env node
// Take a copy of the telemetry into a private place you control (studio/docs/TELEMETRY.md, "导出与私有保存").
// TaoChronos collects its chat logs with a scheduled workflow that commits them into its repository; this repository is
// public, so nothing here is collected by a workflow or committed: this script is run by the owner, on their own
// machine (or from a private repository's scheduled job), and writes only where they say — never inside this
// repository, and inside any other git work tree only when told it is a private one.
//
//   TCMS_EXPORT_TOKEN=… node studio/edge/scripts/export-telemetry.mjs --out ~/tcmscience-exports
//   TCMS_ADMIN_TOKEN=…  node studio/edge/scripts/export-telemetry.mjs --out ~/tcmscience-exports --datasets daily,conversations
//
// Options: --url (https://science.impf.ai) · --days 7|30|90 (30) · --datasets (daily,connectors,geo: aggregated, no
// content; turns and tools are per-turn and per-tool rows, still without content; security and conversations need the
// admin token and hold network prefixes or what visitors wrote) · --format csv|json (csv) · --keep-days N (180: earlier
// exports this script wrote are deleted; 0 keeps all) · --private-repo (allow a git work tree that is not this one).
// The token comes from the environment only (never an argument, which other users of the machine could read), is
// never printed, and goes only to --url over https. Files are readable by their owner only (0600, folders 0700).
// Stdlib only; Node 22.
import { chmodSync, existsSync, mkdirSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const AGGREGATED = ["daily", "connectors", "geo"];
export const ROWS = ["turns", "tools"];
export const ADMIN_ONLY = ["security", "conversations"];
const DATASETS = [...AGGREGATED, ...ROWS, ...ADMIN_ONLY];
const MARK = ".tcmscience-export"; // in each dated folder this script made: only those are ever pruned
const UA = "tcmscience-export/1";
const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPOSITORY = path.resolve(HERE, "..", "..", "..");

/** The git work tree a directory is in (the nearest folder above it with a .git entry), or null. */
export function workTreeOf(dir) {
  let at = path.resolve(dir);
  for (;;) {
    if (existsSync(path.join(at, ".git"))) return at;
    const up = path.dirname(at);
    if (up === at) return null;
    at = up;
  }
}

/** The nearest existing folder at or above `p`, resolved through links (so a link cannot hide where files go). */
function realOf(p) {
  let at = path.resolve(p);
  while (!existsSync(at)) at = path.dirname(at);
  return realpathSync(at);
}

/** Where the exports may go: never in this repository; another git work tree only with privateRepo. Throws why not. */
export function checkDestination(out, { privateRepo = false, repository = REPOSITORY } = {}) {
  if (!out) throw new Error("--out is required: a folder outside this repository, readable only by you");
  const real = realOf(out);
  const repo = existsSync(repository) ? realpathSync(repository) : path.resolve(repository);
  if (real === repo || real.startsWith(`${repo}${path.sep}`)) {
    throw new Error(`${out} is inside this repository, which is public: exports must never be committed or published; choose a folder outside it`);
  }
  const tree = workTreeOf(real);
  if (tree && !privateRepo) {
    throw new Error(`${out} is inside the git work tree ${tree}: pass --private-repo only if that repository is private and access-restricted`);
  }
  return path.resolve(out);
}

/** Delete the dated export folders this script made that are older than keepDays (by their name). → names removed. */
export function prune(out, keepDays, now = Date.now()) {
  if (!keepDays || !existsSync(out)) return [];
  const cut = new Date(now - keepDays * 86400000).toISOString().slice(0, 10);
  const removed = [];
  for (const name of readdirSync(out)) {
    const dir = path.join(out, name);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(name) || name >= cut || !statSync(dir).isDirectory() || !existsSync(path.join(dir, MARK))) continue;
    rmSync(dir, { recursive: true, force: true });
    removed.push(name);
  }
  return removed;
}

export function parse(argv) {
  const o = { url: "https://science.impf.ai", days: 30, datasets: [...AGGREGATED], format: "csv", keepDays: 180, privateRepo: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") o.out = argv[++i];
    else if (a === "--url") o.url = String(argv[++i] || "").replace(/\/+$/, "");
    else if (a === "--days") o.days = Number(argv[++i]);
    else if (a === "--datasets") o.datasets = String(argv[++i] || "").split(",").map((s) => s.trim()).filter(Boolean);
    else if (a === "--format") o.format = argv[++i];
    else if (a === "--keep-days") o.keepDays = Number(argv[++i]);
    else if (a === "--private-repo") o.privateRepo = true;
    else if (a === "-h" || a === "--help") o.help = true;
    else if (a === "--token") throw new Error("the token is read from TCMS_EXPORT_TOKEN or TCMS_ADMIN_TOKEN, never from an argument");
    else throw new Error(`unknown argument ${a}`);
  }
  if (![7, 30, 90].includes(o.days)) throw new Error("--days is 7, 30 or 90");
  if (!["csv", "json"].includes(o.format)) throw new Error("--format is csv or json");
  if (!Number.isInteger(o.keepDays) || o.keepDays < 0) throw new Error("--keep-days is a whole number of days (0 keeps everything)");
  const unknown = o.datasets.filter((d) => !DATASETS.includes(d));
  if (unknown.length || !o.datasets.length) throw new Error(`--datasets: one or more of ${DATASETS.join(", ")}`);
  let u;
  try { u = new URL(o.url); } catch { throw new Error("--url is not an address"); }
  if (u.protocol !== "https:" && !["localhost", "127.0.0.1", "[::1]"].includes(u.hostname)) throw new Error("--url must be https: the token goes with every request");
  return o;
}

/**
 * Fetch each dataset and write it under <out>/<today>/. env: {TCMS_EXPORT_TOKEN, TCMS_ADMIN_TOKEN}; the admin token is
 * used when present (it may take every dataset), else the export token (aggregated and row datasets only).
 * → {folder, files: [{dataset, file, bytes}], pruned}.
 */
export async function run(options, { env = process.env, fetcher = fetch, now = Date.now(), repository = REPOSITORY } = {}) {
  const out = checkDestination(options.out, { privateRepo: options.privateRepo, repository });
  const admin = String(env.TCMS_ADMIN_TOKEN || "").replace(/\s+/g, "");
  const token = admin || String(env.TCMS_EXPORT_TOKEN || "").replace(/\s+/g, "");
  if (token.length < 32) throw new Error("set TCMS_EXPORT_TOKEN (or TCMS_ADMIN_TOKEN) in the environment: the token is at least 32 characters");
  const needAdmin = options.datasets.filter((d) => ADMIN_ONLY.includes(d));
  if (needAdmin.length && !admin) throw new Error(`${needAdmin.join(", ")} need TCMS_ADMIN_TOKEN: the export token never reaches what visitors wrote or network data`);
  const folder = path.join(out, new Date(now).toISOString().slice(0, 10));
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  chmodSync(out, 0o700);
  chmodSync(folder, 0o700);
  writeFileSync(path.join(folder, MARK), "exports written by studio/edge/scripts/export-telemetry.mjs; folders older than --keep-days are deleted\n", { mode: 0o600 });
  const files = [];
  for (const dataset of options.datasets) {
    const q = new URLSearchParams({ dataset, format: options.format, days: String(options.days) });
    const res = await fetcher(`${options.url}/api/admin/telemetry/export?${q}`, {
      headers: { Authorization: `Bearer ${token}`, Accept: options.format === "csv" ? "text/csv" : "application/json", "User-Agent": UA },
      redirect: "error",
    });
    if (res.status !== 200) {
      await res.body?.cancel();
      const why = res.status === 401 ? "the token was refused" : res.status === 403 ? "this token may not take it" : res.status === 503 ? "the dashboard is off or the store is unavailable" : "unexpected answer";
      throw new Error(`${dataset}: HTTP ${res.status} (${why})`);
    }
    const name = /filename="([A-Za-z0-9_.-]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || `tcmscience-${dataset}.${options.format}`;
    const body = new Uint8Array(await res.arrayBuffer());
    const file = path.join(folder, name);
    writeFileSync(file, body, { mode: 0o600 });
    chmodSync(file, 0o600);
    files.push({ dataset, file, bytes: body.length });
  }
  return { folder, files, pruned: prune(out, options.keepDays, now) };
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  try {
    const options = parse(process.argv.slice(2));
    if (options.help) {
      console.log("TCMS_EXPORT_TOKEN=… node studio/edge/scripts/export-telemetry.mjs --out DIR [--url URL] [--days 7|30|90] [--datasets daily,connectors,geo] [--format csv|json] [--keep-days 180] [--private-repo]");
    } else {
      const r = await run(options);
      console.log(JSON.stringify({ folder: r.folder, files: r.files.map((f) => ({ dataset: f.dataset, bytes: f.bytes })), pruned: r.pruned }));
    }
  } catch (err) {
    console.error(`export failed: ${err.message}`);
    process.exit(1);
  }
}
