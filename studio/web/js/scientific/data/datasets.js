// The research datasets on the page side (job spec §1, §8): runtime/datasets.json (the registry the site build writes
// from bioagent's acquisition specs and licence records) against what this browser's OPFS holds, and data.worker.js
// to fetch or import what is missing. Each file comes back as one row with a status named as bioagent.validation
// names them — PASS (the reviewed checksum or size matched), UNVERIFIED (kept, but there is nothing reviewed to check
// it against; its SHA-256 is recorded), FAIL (it is not the reviewed file, or the reviewed URL is out of date),
// BLOCKED (something outside the code stopped it: the network, a host refusing a web page, storage), SKIPPED
// (cancelled, or not asked for) — and with how it travelled (direct from the host, or the site's relay; both
// attempts when "auto" fell back), so nothing reads as fetched one way when it came another.

import { RAW_FILE_SCHEMA, TRANSPORTS, recordMismatch } from "./data.worker.js";
import { parseChecksum } from "./digest.js";
import { checkPath } from "./opfs-store.js";

export const DATASETS_SCHEMA = "tcmstudio.datasets/1";
export { RAW_FILE_SCHEMA };
export const PASS = "PASS";
export const FAIL = "FAIL";
export const BLOCKED = "BLOCKED";
export const UNVERIFIED = "UNVERIFIED";
export const SKIPPED = "SKIPPED";

// the site root, from js/scientific/data/
const SITE = new URL("../../../", import.meta.url).href;

function workspaceError(type, message, extra = {}) {
  const err = new Error(message);
  err.type = type;
  Object.assign(err, extra);
  return err;
}

/** A registry file row, checked: a name that is one plain file name, an http(s) URL, a size or null. */
function fileRow(f, group) {
  const where = `runtime/datasets.json (${group})`;
  if (!f || typeof f !== "object") throw workspaceError("bad_registry", `${where}: a file entry is not an object`);
  let name;
  try {
    [name] = checkPath([f.name]);
  } catch {
    throw workspaceError("bad_registry", `${where}: ${JSON.stringify(f.name)} is not a file name`);
  }
  let url;
  try {
    url = new URL(f.url);
  } catch {
    throw workspaceError("bad_registry", `${where}: ${name} has no URL`);
  }
  const bytes = f.bytes === null || f.bytes === undefined ? null : Number(f.bytes);
  if (bytes !== null && !(Number.isSafeInteger(bytes) && bytes >= 0)) throw workspaceError("bad_registry", `${where}: ${name} has a size that is not a number of bytes`);
  try {
    parseChecksum(f.checksum || null);
  } catch (err) {
    throw workspaceError("bad_registry", `${where}: ${name}: ${err.message}`);
  }
  return { ...f, name, url: url.href, host: f.host || url.hostname, bytes, checksum: f.checksum || null };
}

export class DatasetWorkspace {
  #store;
  #registryUrl;
  #fetch;
  #Worker;
  #workerUrl;
  #group;
  #relayBase;
  #registry = null;
  #worker = null;
  #interrupt = null;
  #seq = 0;
  #pending = new Map();

  /**
   * store: an OpfsStore; registryUrl (runtime/datasets.json of the site); fetch; Worker and workerUrl (the data
   * worker, ./data.worker.js); group ("research"); relayBase (the site's relay for the group,
   * <site>/api/datasets/files/<group>/).
   */
  constructor({ store, registryUrl, siteUrl = SITE, fetch = globalThis.fetch?.bind(globalThis), Worker = globalThis.Worker, workerUrl, group = "research", relayBase } = {}) {
    if (!store || typeof store.stat !== "function") throw new TypeError("DatasetWorkspace needs an OpfsStore (OpfsStore.open() says why there is none)");
    [this.#group] = checkPath([group]);
    this.#store = store;
    this.#registryUrl = registryUrl ? String(registryUrl) : new URL("runtime/datasets.json", siteUrl).href;
    this.#fetch = fetch;
    this.#Worker = Worker;
    this.#workerUrl = workerUrl ? String(workerUrl) : new URL("./data.worker.js", import.meta.url).href;
    this.#relayBase = relayBase ? String(relayBase) : new URL(`api/datasets/files/${encodeURIComponent(this.#group)}/`, siteUrl).href;
  }

  get group() {
    return this.#group;
  }

  /** The OPFS directory of the group's raw files: tcmstudio/datasets/<group>/raw. */
  rawDir() {
    return [...this.#store.base, "datasets", this.#group, "raw"];
  }

  /** Whether files can be fetched here: {ok, reason}. (OPFS itself is the store's question: OpfsStore.open.) */
  support() {
    if (typeof this.#Worker !== "function") return { ok: false, reason: "this browser has no Web Workers, which download and check the raw files off the page" };
    return { ok: true, reason: null };
  }

  /** The group's entry of runtime/datasets.json: {group, title, sources, network, files: [row]}; read once. */
  async registry() {
    if (this.#registry) return this.#registry;
    let res;
    try {
      res = await this.#fetch(this.#registryUrl, { cache: "no-cache" });
    } catch (err) {
      throw workspaceError("unavailable", `the dataset registry could not be loaded (${err?.message || err})`, { need: "dataset" });
    }
    if (!res.ok) {
      throw workspaceError("unavailable", res.status === 404
        ? "this site has no dataset registry (runtime/datasets.json was not built)"
        : `the dataset registry could not be loaded (HTTP ${res.status})`, { need: "dataset" });
    }
    let doc;
    try {
      doc = await res.json();
    } catch {
      throw workspaceError("bad_registry", "runtime/datasets.json is not JSON");
    }
    if (doc?.schema !== DATASETS_SCHEMA) throw workspaceError("bad_registry", `runtime/datasets.json is ${JSON.stringify(doc?.schema)}, not ${DATASETS_SCHEMA}`);
    const g = doc.groups?.[this.#group];
    if (!g || !Array.isArray(g.files)) throw workspaceError("bad_registry", `runtime/datasets.json has no ${this.#group} group`);
    const files = g.files.map((f) => fileRow(f, this.#group));
    const dup = files.map((f) => f.name).filter((n, i, a) => a.indexOf(n) !== i);
    if (dup.length) throw workspaceError("bad_registry", `runtime/datasets.json names ${dup[0]} twice in ${this.#group}`);
    this.#registry = Object.freeze({ ...g, group: this.#group, files });
    return this.#registry;
  }

  /**
   * What OPFS holds against the registry → {group, files: [{...row, present, meta, status, reason, partial_bytes}],
   * missing: [name], download_bytes (null when a missing file has no reviewed size), hosts (of the missing files)}.
   * A file counts as present only with its record, and only when the record still matches the registry (its
   * reviewed checksum, or size): a file cut short by a closed tab, or a superseded release, is missing.
   */
  async plan() {
    const reg = await this.registry();
    const dir = this.rawDir();
    const files = [];
    for (const row of reg.files) {
      const st = await this.#statOrBusy([...dir, row.name]);
      let meta = null;
      let damaged = false;
      try {
        meta = await this.#store.readJson([...dir, ".meta", `${row.name}.json`]);
      } catch (err) {
        if (err?.code !== "corrupt") throw err;
        damaged = true;
      }
      let reason = !st ? "not here" : st.busy ? "it is being written" : damaged ? "its record is damaged" : recordMismatch(meta, st.bytes, row);
      const present = Boolean(st) && !reason;
      // a partial file the data worker is writing cannot be read here: its size is then unknown (null)
      const part = await this.#statOrBusy([...dir, ".part", row.name]);
      files.push({
        ...row, present, meta: present ? meta : null,
        status: present ? (meta.verified === "none" ? UNVERIFIED : PASS) : null,
        reason: present ? null : reason, partial_bytes: part?.busy ? null : part?.bytes || 0,
      });
    }
    const need = files.filter((f) => !f.present);
    return {
      group: this.#group,
      files,
      missing: need.map((f) => f.name),
      download_bytes: need.every((f) => f.bytes !== null) ? need.reduce((n, f) => n + f.bytes, 0) : null,
      hosts: [...new Set(need.map((f) => f.host))].sort(),
    };
  }

  /**
   * Fetch or import files, one after another → [{file, status, route, transport, bytes, sha256, verified, reason,
   * attempts, restarts, resumed_from, duration_s}]. names: the files to get (default: every missing one, plus each
   * file given in `files`); transport: "direct" | "relay" | "auto"; files: a Map (or object) name → File to import
   * instead of downloading; signal stops the running file (its partial file is kept for a resume) and skips the rest;
   * onProgress({file, bytes, total, phase, index, count}).
   */
  async acquire({ names, transport = "auto", signal, onProgress, files } = {}) {
    if (!TRANSPORTS.includes(transport)) throw workspaceError("bad_arguments", `transport ${JSON.stringify(transport)} is not one of ${TRANSPORTS.join(", ")}`);
    const imports = files instanceof Map ? files : new Map(Object.entries(files || {}));
    const plan = await this.plan();
    const byName = new Map(plan.files.map((f) => [f.name, f]));
    const wanted = [...new Set(names ? [...names] : [...plan.missing, ...imports.keys()])];
    const unknown = [...new Set([...wanted, ...imports.keys()])].filter((n) => !byName.has(n));
    if (unknown.length) throw workspaceError("bad_arguments", `not in the ${this.#group} registry: ${unknown.join(", ")}`);
    const support = this.support();
    const dir = this.rawDir();
    const rows = [];
    for (const [index, name] of wanted.entries()) {
      const f = byName.get(name);
      const file = imports.get(name);
      const t0 = Date.now();
      const base = { file: name, host: f.host, route: null, transport: null, bytes: null, sha256: null, verified: null, attempts: [], restarts: [], resumed_from: null };
      if (signal?.aborted) {
        rows.push({ ...base, status: SKIPPED, reason: "cancelled before it started" });
        continue;
      }
      if (!file && f.present) {
        rows.push(this.#cachedRow(base, f));
        continue;
      }
      if (!support.ok) {
        rows.push({ ...base, status: BLOCKED, reason: support.reason });
        continue;
      }
      const registryRow = registryFields(f);
      let how = transport;
      let note = "";
      if (!file && f.relay === false && transport !== "direct") {
        // the site relays only what the registry lets it; "auto" then means the host itself, and says so
        if (transport === "relay") {
          rows.push({ ...base, status: BLOCKED, reason: "the site does not relay this file (the registry does not offer it); only a direct download from its host is possible" });
          continue;
        }
        how = "direct";
        note = "; the site does not relay this file, so only the direct download was tried";
      }
      const onEvent = (ev) => {
        if (ev?.kind === "progress") onProgress?.({ file: name, bytes: ev.bytes, total: ev.total, phase: ev.phase, index, count: wanted.length });
      };
      const reply = file
        ? await this.#request("import", { file, name, dir, expect: registryRow }, { signal, onEvent })
        : await this.#request("download", { file: registryRow, dir, transport: how, relayBase: this.#relayBase }, { signal, onEvent });
      rows.push({ ...rowOf(base, f, reply, file ? "import" : how), duration_s: Math.round(Date.now() - t0) / 1000 });
      if (note) rows[rows.length - 1].reason += note;
    }
    return rows;
  }

  /** The digest of an OPFS file, computed in the data worker → hex (or {name: hex} for a list of algorithms). */
  async hash(path, algorithm = "sha256", { signal, onProgress } = {}) {
    if (!this.support().ok) throw workspaceError("unavailable", this.support().reason);
    const reply = await this.#request("hash", { path: checkPath(path), algorithm }, {
      signal, onEvent: (ev) => { if (ev?.kind === "progress") onProgress?.(ev); },
    });
    if (!reply.ok) throw workspaceError(reply.error?.type || "runtime_error", reply.error?.message || "the digest failed");
    return reply.result;
  }

  /** Stop the data worker (a running transfer keeps its partial file for a resume). */
  terminate() {
    const w = this.#worker;
    this.#worker = null;
    this.#interrupt = null;
    if (w) w.terminate();
    this.#flush("the data worker was stopped");
  }

  /** stat(), or {busy: true} for a file another context holds open for writing (a sync access handle). */
  async #statOrBusy(path) {
    try {
      return await this.#store.stat(path);
    } catch (err) {
      if (err?.name === "NoModificationAllowedError") return { busy: true };
      throw err;
    }
  }

  #cachedRow(base, f) {
    const m = f.meta;
    const how = m.route === "import" ? "imported" : m.route === "relay" ? "downloaded through the site's relay" : "downloaded from its host";
    const when = m.retrieved_at ? ` on ${m.retrieved_at}` : "";
    return {
      ...base, status: m.verified === "none" ? UNVERIFIED : PASS, route: "cache", transport: m.transport ?? null,
      bytes: m.bytes, sha256: m.sha256, verified: m.verified,
      reason: `already in this browser's storage (${how}${when}); ${m.verified === "none" ? "no reviewed checksum or size to check it against" : `verified by ${m.verified} then`}`,
    };
  }

  #ensureWorker() {
    if (this.#worker) return this.#worker;
    const W = this.#Worker;
    const worker = new W(this.#workerUrl, { type: "module", name: "tcmstudio-data" });
    worker.onmessage = (ev) => this.#onMessage(worker, ev.data);
    worker.onerror = (ev) => this.#crash(worker, `the data worker failed: ${ev?.message || "an error it did not report"}`);
    worker.onmessageerror = () => this.#crash(worker, "a message from the data worker could not be read");
    // the same 1-byte stop flag the Python runtime uses, where the page is isolated; a cancel message works anywhere
    const sab = globalThis.crossOriginIsolated && typeof SharedArrayBuffer === "function" ? new SharedArrayBuffer(1) : null;
    this.#interrupt = sab ? new Uint8Array(sab) : null;
    if (sab) worker.postMessage({ type: "interrupt-buffer", buffer: sab });
    this.#worker = worker;
    return worker;
  }

  /** One op in the data worker → its reply ({id, ok, result} | {id, ok:false, error}); never rejects. */
  #request(op, payload, { signal, onEvent } = {}) {
    let worker;
    try {
      worker = this.#ensureWorker();
    } catch (err) {
      return Promise.resolve({ ok: false, error: { type: "unavailable", message: `the data worker could not start (${err?.message || err})` } });
    }
    const id = ++this.#seq;
    // the stop flag stops whatever op runs: it is cleared and raised only when this is the one op in flight
    const alone = this.#pending.size === 0;
    if (alone && this.#interrupt) Atomics.store(this.#interrupt, 0, 0);
    return new Promise((resolve) => {
      const onAbort = () => {
        if (alone && this.#interrupt && this.#pending.size === 1) Atomics.store(this.#interrupt, 0, 1);
        worker.postMessage({ type: "cancel", id });
      };
      this.#pending.set(id, {
        worker, onEvent,
        resolve: (reply) => {
          signal?.removeEventListener?.("abort", onAbort);
          resolve(reply);
        },
      });
      signal?.addEventListener?.("abort", onAbort, { once: true });
      // a File cannot travel as JSON: an import goes structured, everything else as a JSON string
      worker.postMessage(op === "import" ? { id, op, payload } : JSON.stringify({ id, op, payload }));
      if (signal?.aborted) onAbort();
    });
  }

  #onMessage(worker, data) {
    if (worker !== this.#worker) return;
    let msg;
    try {
      msg = typeof data === "string" ? JSON.parse(data) : data;
    } catch {
      return;
    }
    const p = this.#pending.get(msg?.id);
    if (!p) return;
    if (msg.type === "event") {
      p.onEvent?.(msg.event);
      return;
    }
    this.#pending.delete(msg.id);
    p.resolve(msg);
  }

  #crash(worker, message) {
    if (worker !== this.#worker) return;
    this.#worker = null;
    this.#interrupt = null;
    try {
      worker.terminate();
    } catch { /* already gone */ }
    this.#flush(message);
  }

  #flush(message) {
    const pending = [...this.#pending.values()];
    this.#pending.clear();
    for (const p of pending) p.resolve({ ok: false, fatal: true, error: { type: "fatal", message } });
  }
}

/** The registry's own fields of a planned file (what the worker and the record need; not the plan's). */
function registryFields(f) {
  const { present, meta, status, reason, partial_bytes, ...row } = f;
  return row;
}

const FAILS = new Set(["checksum", "size", "oversize", "transfer", "redirect", "bad_arguments", "bad_request", "read", "runtime_error", "fatal", "not_found"]);

/** A worker reply as an acquisition row. */
function rowOf(base, f, reply, how) {
  if (reply.ok) {
    const r = reply.result;
    const verb = r.reused ? "already in this browser's storage" : r.route === "import" ? "imported from the chosen file"
      : r.route === "relay" ? "downloaded through the site's relay" : "downloaded from its host";
    const resumed = r.resumed_from ? `; resumed at byte ${r.resumed_from} (checked by ${r.resume_validator}), then hashed whole` : "";
    const fell = (r.attempts || []).some((a) => !a.ok) ? `; the direct download could not be made (${r.attempts.find((a) => !a.ok).error}), so it came through the relay` : "";
    return {
      ...base, status: r.verified === "none" ? UNVERIFIED : PASS, route: r.reused ? "cache" : r.route, transport: r.transport ?? null,
      bytes: r.bytes, sha256: r.sha256, ...(r.md5 ? { md5: r.md5 } : {}), verified: r.verified,
      reason: r.verified === "none"
        ? `${verb}${fell}; no reviewed checksum or size: its SHA-256 is recorded for review`
        : `${verb}${fell}, verified by ${r.verified}${resumed}`,
      attempts: r.attempts || [], restarts: r.restarts || [], resumed_from: r.resumed_from ?? null,
    };
  }
  const e = reply.error || {};
  const type = e.type || "runtime_error";
  let status;
  if (type === "cancelled") status = SKIPPED;
  // a host's 404 says the reviewed URL is stale (a defect to fix); the relay's says the site does not serve it here
  else if (type === "http") status = !e.relay && (e.status === 404 || e.status === 410 || (e.status >= 400 && e.status < 500 && ![401, 403, 407, 429].includes(e.status))) ? FAIL : BLOCKED;
  else if (FAILS.has(type)) status = FAIL;
  else status = BLOCKED; // network, storage, unavailable, busy: outside the code
  const kept = e.kept_part ? `; ${e.have} bytes are kept for a resume` : "";
  return {
    ...base, status, route: how === "import" ? "import" : null,
    transport: how === "import" ? null : e.attempts?.length ? e.attempts[e.attempts.length - 1].transport : how,
    reason: `${type === "cancelled" ? "cancelled" : e.message || type}${kept}`, error_type: type,
    attempts: e.attempts || [], restarts: e.restarts || [], kept_part: Boolean(e.kept_part),
  };
}
