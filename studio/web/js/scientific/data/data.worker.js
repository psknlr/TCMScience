// The acquisition worker (job spec §1): puts the research raw files into OPFS — downloaded from their own hosts or
// through the site's relay, or imported from a File the person chose — and hashes files there. No Pyodide here: this
// is network and disk work, kept off the page so a 90 MB transfer never stalls it.
//
// Protocol (the shapes of ../../runtime/pyodide.worker.js). Requests are JSON strings {id, op, payload}; an import
// carries a File, which JSON cannot, so it comes as the same object structured. Replies are JSON strings
// {id, ok:true, ms, result} | {id, ok:false, ms, fatal:false, error:{type, message, …details}}; while an op runs,
// {type:"event", id, event:{kind:"progress", file, bytes, total, phase}}. Two structured messages:
// {type:"interrupt-buffer", buffer} (a SharedArrayBuffer the page sets non-zero to stop the running op; polled between
// chunks and every 50 ms) and {type:"cancel", id} (stops that op, running or queued). Ops run one at a time.
//
// Ops: download {file: <registry row>, dir, transport: "direct"|"relay"|"auto", relayBase}, import {file: File, name,
// dir, expect: <registry row>}, hash {path, algorithm}, stat {path}, list {path}, remove {path, recursive},
// estimate {}.
//
// A download is kept only when it is the file the registry describes, and a resume only continues the same file:
//   - bytes go to <dir>/.part/<name>, its identity to <dir>/.part/<name>.json {url, etag, last_modified, total,
//     transport} (a weak W/ ETag cannot validate a range and is not kept);
//   - a partial file is resumed with Range: bytes=<have>- and If-Range (the ETag when the host let the page read it,
//     else Last-Modified) only when that record names the same URL and transport; without either validator only when
//     a reviewed checksum will check the joined file;
//   - a 206 counts only when its Content-Range is readable and is exactly bytes <have>-<total-1>/<total> of the
//     recorded total (and its ETag, when readable, the recorded one); otherwise the partial file is dropped and the
//     download restarts without Range, once. A 200 restarts from byte 0 on that answer; a 412 drops and restarts;
//   - at most the registry's size is accepted (2 GiB when it has none); more is refused and nothing is kept;
//   - an answer redirected off the reviewed host (other than to the registry's redirect_hosts), or from https down to
//     http, is refused: fetch follows a redirect before the page can see it, so its body is not read and nothing kept;
//   - SHA-256 (and MD5 when the reviewed checksum is md5) is computed while the bytes are written; a resumed file is
//     hashed again whole when it is complete. A size or checksum that is not the reviewed one keeps nothing;
//   - a complete file moves to <dir>/<name> (FileSystemHandle.move, or a copy and a delete where the browser has no
//     move) and its record is written to <dir>/.meta/<name>.json (tcmstudio.raw-file/1). The old record is removed
//     first, so a file is never next to a record that is not its own.
// Transport "auto" tries the host directly; only when that request cannot be made at all (a TypeError: the network,
// or the host not allowing a web page to read it — CORS) does it try the relay, and the reply lists both attempts.
// A cancel stops between chunks and keeps the partial file and its identity record for a later resume. Nothing is
// retried here: a transfer that breaks off keeps its partial file too, and the next download of the file resumes it.

import { digestFile, hasher, parseChecksum } from "./digest.js";
import { OpfsStore, checkPath } from "./opfs-store.js";

export const RAW_FILE_SCHEMA = "tcmstudio.raw-file/1";
export const MAX_UNKNOWN_BYTES = 2 * 1024 ** 3;
export const TRANSPORTS = Object.freeze(["direct", "relay", "auto"]);
const READ_CHUNK = 4 << 20;
const PROGRESS_MS = 200;
const POLL_MS = 50;

class OpError extends Error {
  constructor(type, message, details = {}) {
    super(message);
    this.type = type;
    this.details = details;
  }
}

const describe = (err) => (err?.name && err.name !== "Error" ? `${err.name}: ${err.message}` : String(err?.message || err));

/**
 * What the op handlers need, so the tests can give fakes: store (an OpfsStore) or storage (to open one), fetch,
 * post(message) for events, now() in ms, baseUrl to resolve a relative relay path against.
 */
export function workerEnv({ store = null, storage = null, fetch, post = () => {}, now = () => performance.now(), baseUrl = null } = {}) {
  return {
    store, storage, fetch, post, now, baseUrl,
    interrupt: null, // Uint8Array over the page's SharedArrayBuffer, when it is cross-origin isolated
    cancelled: new Set(), // ids a {type:"cancel"} named, before or while they run
    running: new Map(), // id -> AbortController
  };
}

/** Stop op `id`: at once if it is running (its requests are aborted), at its start if it is still queued. */
export function cancel(env, id) {
  env.cancelled.add(id);
  env.running.get(id)?.abort();
}

/** One request → its reply object (the worker posts it as JSON). Never throws. */
export async function handle(msg, env) {
  const t0 = env.now();
  const ms = () => Math.round(env.now() - t0);
  const { id, op, payload } = msg || {};
  const controller = new AbortController();
  env.running.set(id, controller);
  const stopped = () => env.cancelled.has(id) || Boolean(env.interrupt && Atomics.load(env.interrupt, 0));
  const poll = setInterval(() => { if (stopped()) controller.abort(); }, POLL_MS);
  const cx = {
    id, env, signal: controller.signal,
    /** Throws "cancelled" when this op was asked to stop; with `err` (a failure just caught), only then. */
    check(err) {
      if (stopped() || controller.signal.aborted) throw new OpError("cancelled", "cancelled before it finished");
      return err;
    },
    emit(event) {
      env.post(JSON.stringify({ type: "event", id, event }));
    },
  };
  try {
    cx.check();
    const fn = OPS[op];
    if (!fn) throw new OpError("bad_request", `unknown operation: ${op}`);
    const result = await fn(payload || {}, cx);
    return { id, ok: true, ms: ms(), result };
  } catch (err) {
    return { id, ok: false, ms: ms(), fatal: false, error: errorOf(err) };
  } finally {
    clearInterval(poll);
    env.running.delete(id);
    env.cancelled.delete(id);
  }
}

function errorOf(err) {
  if (err instanceof OpError) return { type: err.type, message: err.message, ...err.details };
  const code = err?.code;
  if (code === "bad_path" || code === "bad_arguments") return { type: "bad_arguments", message: err.message };
  if (code === "quota" || code === "storage" || code === "busy" || code === "corrupt") return { type: "storage", message: err.message, ...(code === "quota" ? { quota: true } : {}) };
  if (code === "not_found") return { type: "not_found", message: err.message };
  if (err?.name === "QuotaExceededError") return { type: "storage", quota: true, message: `the browser's storage quota for this site is used up (${describe(err)})` };
  if (err?.name === "NoModificationAllowedError") return { type: "busy", message: `the file is open elsewhere: another tab or worker is writing it (${describe(err)})` };
  return { type: "runtime_error", message: describe(err).slice(0, 2000) };
}

async function storeOf(env) {
  if (env.store) return env.store;
  const { store, reason } = await OpfsStore.open({ storage: env.storage });
  if (!store) throw new OpError("unavailable", reason);
  env.store = store;
  return store;
}

const OPS = {
  download,
  import: importFile,
  async hash({ path, algorithm = "sha256" }, cx) {
    const store = await storeOf(cx.env);
    const file = await store.readFile(checkPath(path));
    if (!file) throw new OpError("not_found", `${path.join("/")} does not exist`);
    const name = path[path.length - 1];
    let last = 0;
    try {
      return await digestFile(file, algorithm, {
        signal: cx.signal,
        onProgress: (bytes, total) => {
          const t = cx.env.now();
          if (bytes === total || t - last >= PROGRESS_MS) { last = t; cx.emit({ kind: "progress", file: name, bytes, total, phase: "hash" }); }
          cx.check();
        },
      });
    } catch (err) {
      throw cx.check(err);
    }
  },
  async stat({ path }, cx) {
    return (await storeOf(cx.env)).stat(checkPath(path));
  },
  async list({ path }, cx) {
    return (await storeOf(cx.env)).list(checkPath(path));
  },
  async remove({ path, recursive = false }, cx) {
    return (await storeOf(cx.env)).remove(checkPath(path), { recursive });
  },
  async estimate(_payload, cx) {
    return (await storeOf(cx.env)).estimate();
  },
};

// ------------------------------------------------------------------------------------------------------- download

/** The registry row's fields this worker relies on, checked. */
function checkRow(row) {
  if (!row || typeof row !== "object") throw new OpError("bad_arguments", "download needs the registry row of the file");
  const [name] = checkPath([row.name]);
  let url;
  try {
    url = new URL(row.url);
  } catch {
    throw new OpError("bad_arguments", `${name}: ${JSON.stringify(row.url)} is not a URL`);
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new OpError("bad_arguments", `${name}: only http(s) URLs are downloaded`);
  const bytes = row.bytes === null || row.bytes === undefined ? null : Number(row.bytes);
  if (bytes !== null && !(Number.isSafeInteger(bytes) && bytes >= 0)) throw new OpError("bad_arguments", `${name}: bytes ${row.bytes} is not a size`);
  const redirectHosts = Array.isArray(row.redirect_hosts) ? row.redirect_hosts.map((h) => String(h).toLowerCase()) : [];
  return { name, url: url.href, bytes, checksum: row.checksum || null, sum: parseChecksum(row.checksum), redirectHosts };
}

function pathsOf(dir, name) {
  return {
    final: [...dir, name],
    part: [...dir, ".part", name],
    side: [...dir, ".part", `${name}.json`],
    meta: [...dir, ".meta", `${name}.json`],
  };
}

/** A JSON record, or null when it is missing or damaged (a torn write reads as no record, never as a match). */
async function readRecord(store, path) {
  try {
    return await store.readJson(path);
  } catch (err) {
    if (err?.code === "corrupt") return null;
    throw err;
  }
}

async function download(payload, cx) {
  const row = checkRow(payload.file);
  const dir = checkPath(payload.dir || []);
  const transport = payload.transport ?? "auto";
  if (!TRANSPORTS.includes(transport)) throw new OpError("bad_arguments", `transport ${JSON.stringify(transport)} is not one of ${TRANSPORTS.join(", ")}`);
  if (transport !== "direct" && !payload.relayBase) throw new OpError("bad_arguments", `transport ${transport} needs relayBase (the site's relay path for this dataset group)`);
  const store = await storeOf(cx.env);
  const paths = pathsOf(dir, row.name);
  const restarts = [];

  const kept = await keptCopy(store, paths, row, restarts);
  if (kept) return { ...kept, reused: true, attempts: [], path: paths.final };

  const part = await openPart(store, paths);
  const attempts = [];
  try {
    const order = transport === "auto" ? ["direct", "relay"] : [transport];
    for (const t of order) {
      const url = t === "direct" ? row.url : relayUrl(payload.relayBase, row.name, cx.env.baseUrl);
      let got;
      try {
        got = await transfer(cx, { store, paths, part, row, url, transport: t, restarts });
      } catch (err) {
        attempts.push({ transport: t, ok: false, error: err instanceof OpError ? `${err.type}: ${err.message}` : describe(err), ...(err?.details?.status ? { status: err.details.status } : {}) });
        // only a request that could not be made at all goes to the relay: an answer from the host, whatever its
        // status, is the host's answer
        if (transport === "auto" && t === "direct" && err instanceof OpError && err.type === "network" && err.details.phase === "request") {
          continue;
        }
        throw err;
      }
      attempts.push({ transport: t, ok: true, error: null });
      const meta = await finish(cx, { store, paths, part, row, got, route: t === "relay" ? "relay" : "download", transport: t, restarts });
      return { ...meta, reused: false, attempts, path: paths.final };
    }
    throw new OpError("network", "no transport reached the file");
  } catch (err) {
    let e = err;
    if (!(err instanceof OpError)) {
      const { type, message, ...rest } = errorOf(err);
      e = new OpError(type, message, rest);
    }
    const have = part.open ? await part.size() : 0;
    Object.assign(e.details, { attempts, restarts, have, kept_part: have > 0 && Boolean(await readRecord(store, paths.side).catch(() => null)) });
    throw e;
  } finally {
    await part.close();
    // a partial file with nothing in it is not kept
    const st = await store.stat(paths.part).catch(() => null);
    if (st && st.bytes === 0) {
      await store.remove(paths.part).catch(() => {});
      await store.remove(paths.side).catch(() => {});
    }
  }
}

function relayUrl(base, name, baseUrl) {
  try {
    return new URL(`${base}${encodeURIComponent(name)}`, baseUrl || undefined).href;
  } catch {
    throw new OpError("bad_arguments", `relay path ${JSON.stringify(base)} is not a URL here`);
  }
}

/** A complete copy already kept, whose record matches it and the registry → its record; else null (and an unmatched
 * copy is removed: a file cut short by a closed tab, or one the registry no longer describes, is not kept). */
async function keptCopy(store, paths, row, restarts) {
  const st = await store.stat(paths.final);
  const meta = await readRecord(store, paths.meta);
  if (!st) {
    if (meta) await store.remove(paths.meta);
    return null;
  }
  const why = recordMismatch(meta, st.bytes, row);
  if (!why) return meta;
  restarts.push(`the ${st.bytes}-byte ${row.name} already here was removed: ${why}`);
  await store.remove(paths.meta);
  await store.remove(paths.final);
  return null;
}

/** Why a kept file and its record are not the registry's file ('' when they are). Shared with datasets.js. */
export function recordMismatch(meta, bytes, row) {
  if (!meta || meta.schema !== RAW_FILE_SCHEMA) return "it has no record of how it was obtained";
  if (meta.bytes !== bytes) return `its record says ${meta.bytes} bytes, the file has ${bytes}`;
  if (meta.name !== row.name) return `its record names ${meta.name}`;
  const sum = parseChecksum(row.checksum || null);
  if (sum && meta[sum.algorithm] !== sum.hex) {
    return meta[sum.algorithm] ? `its ${sum.algorithm} is not the reviewed checksum` : `its record has no ${sum.algorithm} to compare with the reviewed checksum`;
  }
  if (row.bytes !== null && row.bytes !== undefined && meta.bytes !== Number(row.bytes)) return `${meta.bytes} bytes, not the reviewed ${row.bytes}`;
  return "";
}

/** The partial file, open through one sync access handle for the whole op (workers have them in every OPFS browser). */
async function openPart(store, paths) {
  const fh = await store.file(paths.part, { create: true });
  if (typeof fh.createSyncAccessHandle !== "function") throw new OpError("unavailable", "OPFS sync access handles are missing here (they exist in dedicated workers only)");
  let h;
  try {
    h = await fh.createSyncAccessHandle();
  } catch (err) {
    if (err?.name === "NoModificationAllowedError") throw new OpError("busy", `${paths.part.join("/")} is open elsewhere: another tab is downloading this file`);
    throw err;
  }
  const part = {
    open: true,
    handle: h,
    async size() { return h.getSize(); },
    async close() {
      if (!part.open) return;
      part.open = false;
      try { await h.flush(); } catch { /* closing anyway */ }
      await h.close();
    },
    /** Drop the bytes (the file stays open; it is removed when the op ends empty) and the identity record. */
    async drop() {
      await h.truncate(0);
      await store.remove(paths.side);
    },
  };
  return part;
}

/** Strong entity tag or null: a weak one (W/"…") cannot validate a byte range. */
export function strongEtag(value) {
  const v = (value || "").trim();
  return v && !/^w\//i.test(v) ? v : null;
}

/** "bytes 100-199/1000" → {start, end, total}; null for anything else (absent, unreadable, "*" total). */
export function parseContentRange(value) {
  const m = /^\s*bytes\s+(\d+)-(\d+)\/(\d+)\s*$/i.exec(value || "");
  if (!m) return null;
  const [start, end, total] = [Number(m[1]), Number(m[2]), Number(m[3])];
  return start <= end && end < total ? { start, end, total } : null;
}

function unsatisfiedTotal(value) {
  const m = /^\s*bytes\s+\*\/(\d+)\s*$/i.exec(value || "");
  return m ? Number(m[1]) : null;
}

function lengthOf(value) {
  return /^\s*\d+\s*$/.test(value || "") ? Number(value) : null;
}

/** Why a 206 for the right range came from another file than the partial one ('' when nothing shows it). */
function otherFile(side, headers) {
  const now = strongEtag(headers.get("etag"));
  if (side.etag && now && now !== side.etag) return `its ETag is ${now}, not the recorded ${side.etag}`;
  const lm = headers.get("last-modified");
  if (!side.etag && side.last_modified && lm && lm !== side.last_modified) return `its Last-Modified is ${lm}, not the recorded ${side.last_modified}`;
  return "";
}

/** Where a transfer starts: {offset, validator}. Records in `restarts` why a partial file is not continued. */
function planResume(have, side, row, transport, cap, restarts) {
  if (!have) return { offset: 0, validator: null };
  let why = "";
  if (!side) why = "it has no identity record";
  else if (side.url !== row.url) why = "it was started from another URL";
  else if (side.transport !== transport) why = `it was started through the ${side.transport} transport, not ${transport}`;
  else if (side.total !== null && side.total !== undefined && have > side.total) why = "it is longer than the file";
  else if (have > cap) why = "it is longer than the reviewed size";
  const validator = why ? null : side.etag || side.last_modified || null;
  if (!why && !validator && !row.sum) why = "its identity cannot be shown (no ETag or Last-Modified was readable when it was started, and no reviewed checksum will check the joined file)";
  if (why) {
    restarts.push(`${transport}: ${have} bytes of a partial file not resumed: ${why}; the download starts from byte 0`);
    return { offset: 0, validator: null };
  }
  return { offset: have, validator };
}

/** The GETs of one file through one transport, until its body is in the partial file. */
async function transfer(cx, { store, paths, part, row, url, transport, restarts }) {
  const cap = row.bytes ?? MAX_UNKNOWN_BYTES;
  let side = await readRecord(store, paths.side);
  let { offset, validator } = planResume(await part.size(), side, row, transport, cap, restarts);
  // nothing left to ask for, and a reviewed checksum will check the file as it is
  if (offset && side.total === offset && row.sum) return { bytes: offset, total: offset, side, start: offset, hashers: null, validator: "checksum" };
  const host = new URL(url).host;
  // every restart asks for the whole file (offset 0), so this runs at most twice
  for (;;) {
    cx.check();
    const headers = {};
    if (offset) {
      headers.Range = `bytes=${offset}-`;
      if (validator) headers["If-Range"] = validator;
    }
    const init = transport === "direct"
      ? { mode: "cors", credentials: "omit", cache: "no-store", headers, signal: cx.signal }
      : { cache: "no-store", headers, signal: cx.signal };
    let res;
    try {
      res = await cx.env.fetch(url, init);
    } catch (err) {
      cx.check(err);
      if (offset) {
        // If-Range is not a header a page may send cross-origin without asking first (a CORS preflight); a host that
        // refuses the preflight can still serve the whole file
        restarts.push(`${transport}: the request for bytes=${offset}- could not be made (${describe(err)}); asked again for the whole file`);
        offset = 0;
        validator = null;
        continue;
      }
      throw new OpError("network", `${host} could not be reached (${describe(err)})${transport === "direct" ? ": the network is down, or the host does not let a web page download from it (CORS)" : ""}`, { phase: "request" });
    }
    const moved = redirectRefusal(res, url, transport, row);
    if (moved) {
      await cancelBody(res);
      throw new OpError("redirect", `${host}: ${moved}; the reviewed URL needs review, and nothing from it was kept`);
    }
    const h = res.headers;
    const status = res.status;
    let start = 0;
    let total = null;
    if (status === 206 && offset) {
      const raw = h.get("content-range");
      const cr = parseContentRange(raw);
      let why = "";
      if (!cr) why = raw === null ? "its Content-Range is not readable (the host does not expose it to web pages)" : `its Content-Range ${JSON.stringify(raw)} is not bytes start-end/total`;
      else if (cr.start !== offset || cr.end !== cr.total - 1) why = `it answered ${raw}, not bytes ${offset}-<last>/<total>`;
      else if (side?.total !== null && side?.total !== undefined && cr.total !== side.total) why = `its total is ${cr.total} bytes, not the recorded ${side.total}: another file`;
      else why = otherFile(side, h);
      if (why) {
        await cancelBody(res);
        await part.drop();
        restarts.push(`${transport}: the answer to bytes=${offset}- cannot be joined to the partial file (${why}): it was dropped and the download restarted from byte 0`);
        offset = 0;
        validator = null;
        side = null;
        continue;
      }
      if (cr.total > cap) {
        await cancelBody(res);
        await part.drop();
        throw oversize(row, cr.total, "the server says the file is");
      }
      start = offset;
      total = cr.total;
    } else if (status === 206) {
      await cancelBody(res);
      throw new OpError("transfer", `${host} answered a request for the whole file with HTTP 206; nothing was kept`);
    } else if (status === 200) {
      if (offset) restarts.push(`${transport}: the server sent the whole file for bytes=${offset}- (If-Range did not match, or it serves no ranges): restarted from byte 0`);
      const declared = lengthOf(h.get("content-length"));
      if (declared !== null && declared > cap) {
        await cancelBody(res);
        await part.drop();
        throw oversize(row, declared, "the server says the file is");
      }
      total = row.bytes;
      // the identity of the answer whose bytes are kept, before the first byte is: a later resume is checked against it
      await part.handle.truncate(0);
      side = {
        url: row.url, etag: strongEtag(h.get("etag")), last_modified: h.get("last-modified") || null, total,
        transport, declared_bytes: declared,
      };
      await store.writeJson(paths.side, side);
    } else if (status === 412 && offset) {
      await cancelBody(res);
      await part.drop();
      restarts.push(`${transport}: HTTP 412 for bytes=${offset}-: the file changed since the partial file was started; it was dropped and the download restarted from byte 0`);
      offset = 0;
      validator = null;
      side = null;
      continue;
    } else if (status === 416 && offset) {
      const whole = unsatisfiedTotal(h.get("content-range"));
      await cancelBody(res);
      // If-Range is evaluated before Range: a 416 naming this very size says the partial file is the whole file
      if (whole === offset && (validator || row.sum)) return { bytes: offset, total: whole, side, start: offset, hashers: null, validator: validator ? (side.etag ? "etag" : "last-modified") : "checksum" };
      await part.drop();
      restarts.push(`${transport}: HTTP 416 for bytes=${offset}-: the partial file was dropped and the download restarted from byte 0`);
      offset = 0;
      validator = null;
      side = null;
      continue;
    } else {
      await cancelBody(res);
      throw httpError(status, host, transport);
    }
    const hashers = start === 0 ? digestNames(row).map((a) => hasher(a)) : null;
    const end = await streamBody(cx, res, part, { start, cap, total, hint: side?.declared_bytes ?? null, hashers, row });
    return { bytes: end, total, side, start, hashers, validator: start ? (side.etag ? "etag" : side.last_modified ? "last-modified" : "checksum") : null };
  }
}

/** Why an answer that came through a redirect is not used ('' when it may be): fetch follows a redirect before the
 * page can see it, so a hop off the reviewed host (or to any host but the site, through the relay), or from https down
 * to http, is refused after the fact and its body is not read. */
function redirectRefusal(res, url, transport, row) {
  if (!res.redirected || !res.url) return "";
  const from = new URL(url);
  const to = new URL(res.url);
  if (from.protocol === "https:" && to.protocol !== "https:") return `redirected from https down to ${to.protocol.replace(":", "")}`;
  const allowed = transport === "direct" ? [from.hostname, ...row.redirectHosts] : [from.hostname];
  if (!allowed.includes(to.hostname.toLowerCase())) return `redirected to ${to.hostname}, which the registry does not list for this file (${allowed.join(", ")})`;
  return "";
}

function digestNames(row) {
  return row.sum && row.sum.algorithm !== "sha256" ? ["sha256", row.sum.algorithm] : ["sha256"];
}

async function cancelBody(res) {
  try {
    await res.body?.cancel();
  } catch { /* nothing to read anyway */ }
}

function oversize(row, bytes, what) {
  return new OpError("oversize", row.bytes !== null
    ? `${what} ${bytes} bytes, more than the reviewed ${row.bytes}; nothing was kept`
    : `${what} ${bytes} bytes, more than the ${MAX_UNKNOWN_BYTES}-byte limit for a file the registry has no size for; nothing was kept`);
}

function httpError(status, host, transport) {
  const relay = transport === "relay";
  const why = (status === 404 || status === 410) && relay ? ": the relay does not serve this file here (it serves only what the registry names, where the site runs it)"
    : status === 404 || status === 410 ? ": the reviewed URL is out of date and needs review"
    : status === 401 || status === 403 || status === 407 ? ": the host requires authorisation or refuses this client; access controls are not bypassed"
    : status === 429 ? ": the host is rate-limiting; try later"
    : status >= 500 ? ": a server error" : "";
  return new OpError("http", `${relay ? "the site's relay" : host} answered HTTP ${status}${why}`, { status, phase: "response", ...(relay ? { relay: true } : {}) });
}

/** The response body into the partial file at `start`, hashing as it goes; returns the bytes the file holds. */
async function streamBody(cx, res, part, { start, cap, total, hint = null, hashers, row, phase = "download" }) {
  const name = row.name;
  let pos = start;
  let last = -Infinity;
  const emit = (force) => {
    const t = cx.env.now();
    if (force || t - last >= PROGRESS_MS) {
      last = t;
      cx.emit({ kind: "progress", file: name, bytes: pos, total: total ?? hint, phase });
    }
  };
  emit(true);
  if (!res.body) return pos;
  const reader = res.body.getReader();
  let finished = false;
  try {
    for (;;) {
      cx.check();
      let r;
      try {
        r = await reader.read();
      } catch (err) {
        cx.check(err);
        if (phase === "import") throw new OpError("read", `the chosen file could not be read at byte ${pos} (${describe(err)})`);
        throw new OpError("network", `the transfer broke off at ${pos} bytes (${describe(err)}); the partial file is kept for a resume`, { phase: "body" });
      }
      if (r.done) break;
      const chunk = r.value;
      if (pos + chunk.length > cap) {
        await part.drop();
        throw oversize(row, pos + chunk.length, phase === "import" ? "the chosen file has at least" : "the server sent at least");
      }
      if (total !== null && total !== undefined && pos + chunk.length > total) {
        await part.drop();
        throw new OpError("transfer", `the server sent more than the ${total} bytes it declared; nothing was kept`);
      }
      if (part.handle.write(chunk, { at: pos }) !== chunk.length) throw new OpError("storage", `a short write at byte ${pos}`);
      pos += chunk.length;
      if (hashers) for (const hs of hashers) hs.update(chunk);
      emit(false);
    }
    finished = true;
  } finally {
    if (!finished) reader.cancel().catch(() => {});
  }
  emit(true);
  return pos;
}

/** Checks a complete partial file and keeps it: size, digests, checksum, then the move and its record. */
async function finish(cx, { store, paths, part, row, got, route, transport, restarts, extra = {} }) {
  const size = await part.size();
  if (row.bytes !== null && size !== row.bytes) {
    await part.drop();
    throw new OpError("size", `${size} bytes, not the reviewed ${row.bytes}; nothing was kept`);
  }
  if (got.total !== null && got.total !== undefined && size !== got.total) {
    await part.drop();
    throw new OpError("size", `${size} bytes, not the ${got.total} the server declared; nothing was kept`);
  }
  const names = digestNames(row);
  const digests = got.hashers ? Object.fromEntries(names.map((n, i) => [n, got.hashers[i].digestHex()])) : await rehash(cx, part, size, names, row.name);
  if (row.sum && digests[row.sum.algorithm] !== row.sum.hex) {
    await part.drop();
    throw new OpError("checksum", `${row.sum.algorithm} ${digests[row.sum.algorithm].slice(0, 16)}… is not the reviewed ${row.checksum.slice(0, 23)}…: the file is not the reviewed one (it changed upstream, or the transfer was corrupted); nothing was kept`);
  }
  const side = got.side || {};
  return place(cx, store, paths, part, {
    name: row.name, bytes: size, url: row.url, route, transport,
    etag: side.etag ?? null, last_modified: side.last_modified ?? null, retrieved_at: new Date().toISOString(),
    ...digests, checksum: row.checksum, checksum_ok: row.sum ? true : null,
    verified: row.sum ? "checksum" : row.bytes !== null ? "size" : "none",
    expected_bytes: row.bytes, declared_bytes: side.declared_bytes ?? null,
    resumed_from: got.start || null, resume_validator: got.start ? got.validator : null, restarts: [...restarts], ...extra,
  });
}

/** SHA-256 (and MD5) of the whole partial file, read back through its handle: a resumed file is hashed once more. */
async function rehash(cx, part, size, names, name) {
  const hs = names.map((n) => hasher(n));
  const buf = new Uint8Array(Math.max(1, Math.min(READ_CHUNK, size)));
  let last = -Infinity;
  for (let at = 0; at < size;) {
    cx.check();
    const n = part.handle.read(buf, { at });
    if (n <= 0) throw new OpError("storage", `could not read ${name} back at byte ${at}`);
    const piece = buf.subarray(0, n);
    for (const h of hs) h.update(piece);
    at += n;
    const t = cx.env.now();
    if (at === size || t - last >= PROGRESS_MS) {
      last = t;
      cx.emit({ kind: "progress", file: name, bytes: at, total: size, phase: "hash" });
    }
  }
  return Object.fromEntries(names.map((n, i) => [n, hs[i].digestHex()]));
}

/** Move the checked partial file to its name and write its record (the old record goes first). */
async function place(cx, store, paths, part, record) {
  cx.check();
  await part.close();
  await store.remove(paths.meta);
  await store.remove(paths.final);
  const placed = await store.move(paths.part, paths.final);
  await store.remove(paths.side);
  const meta = { schema: RAW_FILE_SCHEMA, ...record, placed_by: placed };
  await store.writeJson(paths.meta, meta);
  return meta;
}

// --------------------------------------------------------------------------------------------------------- import

async function importFile(payload, cx) {
  const file = payload.file;
  if (!file || typeof file.size !== "number" || (typeof file.stream !== "function" && typeof file.slice !== "function")) {
    throw new OpError("bad_arguments", "import needs a File");
  }
  const expect = payload.expect || null;
  const [name] = checkPath([payload.name ?? expect?.name]);
  if (expect?.name && expect.name !== name) throw new OpError("bad_arguments", `the file is imported as ${name}, but the registry row is ${expect.name}`);
  const dir = checkPath(payload.dir || []);
  const bytes = expect?.bytes === null || expect?.bytes === undefined ? null : Number(expect.bytes);
  const row = { name, url: expect?.url ?? null, bytes, checksum: expect?.checksum || null, sum: parseChecksum(expect?.checksum || null) };
  const cap = bytes ?? MAX_UNKNOWN_BYTES;
  if (file.size > cap) throw oversize(row, file.size, "the chosen file is");
  if (bytes !== null && file.size !== bytes) throw new OpError("size", `the chosen file is ${file.size} bytes, not the reviewed ${bytes}; nothing was kept`);
  const store = await storeOf(cx.env);
  const paths = pathsOf(dir, name);
  const restarts = [];
  if (await store.exists(paths.side)) restarts.push("a partial download of this file was replaced by the import");
  const part = await openPart(store, paths);
  let kept = false;
  try {
    await part.drop();
    const hashers = digestNames(row).map((a) => hasher(a));
    const body = typeof file.stream === "function" ? file.stream() : sliceStream(file);
    const end = await streamBody(cx, { body }, part, { start: 0, cap, total: file.size, hashers, row, phase: "import" });
    if (end !== file.size) throw new OpError("storage", `read ${end} of ${file.size} bytes: the chosen file changed while it was read`);
    // an import is not a download: its record says which file the person chose instead of a server's validators
    const source = { name: typeof file.name === "string" ? file.name : null, bytes: file.size, last_modified: Number.isFinite(file.lastModified) ? new Date(file.lastModified).toISOString() : null };
    const meta = await finish(cx, {
      store, paths, part, row, route: "import", transport: null, restarts, extra: { source },
      got: { bytes: end, total: file.size, side: null, start: 0, hashers, validator: null },
    });
    kept = true;
    return { ...meta, reused: false, attempts: [], path: paths.final };
  } catch (err) {
    // a File can be read again, so a cut-short import is dropped, not kept for a resume
    if (!kept && part.open) await part.drop().catch(() => {});
    throw err;
  } finally {
    await part.close();
    const st = await store.stat(paths.part).catch(() => null);
    if (st && st.bytes === 0) await store.remove(paths.part).catch(() => {});
  }
}

function sliceStream(file) {
  let at = 0;
  return new ReadableStream({
    async pull(controller) {
      if (at >= file.size) { controller.close(); return; }
      const piece = new Uint8Array(await file.slice(at, Math.min(file.size, at + READ_CHUNK)).arrayBuffer());
      at += piece.length;
      controller.enqueue(piece);
    },
  });
}

// --------------------------------------------------------------------------------------------------- the worker

/** The message loop of a worker scope (self, or a test's stand-in): requests one at a time in arrival order; a cancel
 * and the interrupt buffer take effect at once. */
export function serve(scope, env) {
  let chain = Promise.resolve();
  scope.onmessage = (ev) => {
    const data = ev.data;
    if (data && typeof data === "object" && data.type === "interrupt-buffer") {
      env.interrupt = typeof SharedArrayBuffer === "function" && data.buffer instanceof SharedArrayBuffer ? new Uint8Array(data.buffer) : null;
      return;
    }
    let msg;
    try {
      msg = typeof data === "string" ? JSON.parse(data) : data;
    } catch {
      return; // not ours
    }
    if (msg && msg.type === "cancel") {
      cancel(env, msg.id);
      return;
    }
    if (!msg || msg.id === undefined || msg.id === null) return;
    chain = chain.then(async () => scope.postMessage(JSON.stringify(await handle(msg, env)))).catch(() => {});
  };
}

if (typeof self !== "undefined" && typeof WorkerGlobalScope !== "undefined" && self instanceof WorkerGlobalScope) {
  serve(self, workerEnv({
    storage: self.navigator?.storage ?? null,
    fetch: self.fetch.bind(self),
    post: (m) => self.postMessage(m),
    baseUrl: self.location.href,
  }));
}
