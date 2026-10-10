// Projects, conversations, messages and files, in this browser only (IndexedDB "tcmstudio", CONTRACTS §7). When
// IndexedDB is missing or blocked (some private windows), an in-memory store with the same API is used and
// `store.persistent` is false, so the UI can say that nothing will be kept.
//
// Messages form a tree through parentId: editing a user message adds a sibling with the same parent, and
// conversation.leafId selects the branch shown. threadPath/siblingsOf/latestLeaf work on a conversation's messages.

import { t } from "./i18n.js";
import { base64ToBytes, blobToBase64, sha256Hex, uuid } from "./util.js";

export const DB_NAME = "tcmstudio";
export const DB_VERSION = 1;
export const EXPORT_FORMAT = "tcmstudio.project/1";

/** Schema migrations, one per version: MIGRATIONS[v - 1] upgrades a database from version v - 1 to v. */
const MIGRATIONS = [
  (db) => {
    const projects = db.createObjectStore("projects", { keyPath: "id" });
    projects.createIndex("updatedAt", "updatedAt");
    const conversations = db.createObjectStore("conversations", { keyPath: "id" });
    conversations.createIndex("projectId", "projectId");
    conversations.createIndex("updatedAt", "updatedAt");
    const messages = db.createObjectStore("messages", { keyPath: "id" });
    messages.createIndex("conversationId", "conversationId");
    const files = db.createObjectStore("files", { keyPath: "id" });
    files.createIndex("projectId", "projectId");
    files.createIndex("sha256", "sha256");
  },
];

const STORES = ["projects", "conversations", "messages", "files"];

// ------------------------------------------------------------------------------------------------- backends

function req(r) {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

/** A connection that is gone rather than a request that was wrong: worth one new connection and one retry. */
function connectionLost(err) {
  return err?.name === "InvalidStateError" || err?.name === "UnknownError";
}

class IDBBackend {
  constructor(db, { idb = null, name = DB_NAME, version = DB_VERSION } = {}) {
    this.persistent = true;
    this.idb = idb;
    this.name = name;
    this.version = version;
    this.upgraded = false;
    this.#adopt(db);
  }

  #adopt(db) {
    this.db = db;
    // another tab upgraded the schema: close so it is not blocked; this tab must reload (no reconnect: it would
    // open the newer schema with this tab's older code)
    db.onversionchange = () => { this.upgraded = true; try { db.close(); } catch { /* closed */ } };
  }

  static open(idb, name = DB_NAME, version = DB_VERSION) {
    return new Promise((resolve, reject) => {
      let r;
      try { r = idb.open(name, version); } catch (err) { reject(err); return; }
      r.onupgradeneeded = (e) => {
        const db = r.result;
        for (let v = (e.oldVersion || 0) + 1; v <= version; v++) MIGRATIONS[v - 1](db, r.transaction);
      };
      r.onsuccess = () => resolve(new IDBBackend(r.result, { idb, name, version }));
      r.onerror = () => reject(r.error);
      r.onblocked = () => reject(new Error("IndexedDB upgrade blocked by another open tab"));
    });
  }

  /**
   * Run `body(tx)` in a new transaction. When the connection is gone (Safari drops it in a tab left in the
   * background; the browser closes it when site data is cleared), every transaction fails until the page reloads:
   * open a new connection once and run it again. A failed transaction wrote nothing, so the retry cannot double it.
   */
  async #run(names, mode, body) {
    for (let attempt = 0; ; attempt++) {
      try {
        return await body(this.db.transaction(names, mode));
      } catch (err) {
        if (attempt > 0 || !connectionLost(err) || this.upgraded || !this.idb) throw err;
        const fresh = await IDBBackend.open(this.idb, this.name, this.version);
        this.#adopt(fresh.db);
      }
    }
  }

  get(store, id) {
    return this.#run(store, "readonly", async (tx) => (await req(tx.objectStore(store).get(id))) ?? null);
  }

  all(store) {
    return this.#run(store, "readonly", (tx) => req(tx.objectStore(store).getAll()));
  }

  byIndex(store, index, value) {
    return this.#run(store, "readonly", (tx) => req(tx.objectStore(store).index(index).getAll(value)));
  }

  /**
   * Several writes in one transaction: [{store, put: record} | {store, delete: id} | {store, patch: id, fn}]. A patch
   * reads and rewrites the record inside the transaction (fn(record) → new record, or null to leave it), so two
   * writers never overwrite each other's fields. Resolves with the patched records, in order.
   */
  bulk(ops) {
    if (!ops.length) return Promise.resolve([]);
    const names = [...new Set(ops.map((o) => o.store))];
    return this.#run(names, "readwrite", (tx) => this.#bulkIn(tx, ops));
  }

  #bulkIn(tx, ops) {
    return new Promise((resolve, reject) => {
      const patched = [];
      tx.oncomplete = () => resolve(patched);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
      try {
        for (const o of ops) {
          const s = tx.objectStore(o.store);
          if ("put" in o) s.put(o.put);
          else if ("patch" in o) {
            const slot = patched.length;
            patched.push(null);
            const r = s.get(o.patch);
            r.onsuccess = () => {
              if (!r.result) return;
              let next;
              try { next = o.fn(r.result); } catch (err) { tx.abort(); reject(err); return; }
              if (next) { s.put(next); patched[slot] = next; }
            };
          } else s.delete(o.delete);
        }
      } catch (err) {
        // a record that cannot be stored (DataCloneError): none of the writes happen, not the ones before it
        try { tx.abort(); } catch { /* already finished */ }
        reject(err);
      }
    });
  }

  clear() {
    return this.#run(STORES, "readwrite", (tx) => new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error("transaction aborted"));
      for (const s of STORES) tx.objectStore(s).clear();
    }));
  }

  close() {
    this.db.close();
  }
}

class MemoryBackend {
  constructor() {
    this.persistent = false;
    this.data = Object.fromEntries(STORES.map((s) => [s, new Map()]));
  }

  async get(store, id) {
    const v = this.data[store].get(id);
    return v === undefined ? null : clone(v);
  }

  async all(store) {
    return [...this.data[store].values()].map(clone);
  }

  async byIndex(store, index, value) {
    return [...this.data[store].values()].filter((r) => r[index] === value).map(clone);
  }

  async bulk(ops) {
    const patched = [];
    for (const o of ops) {
      if ("put" in o) this.data[o.store].set(o.put.id, clone(o.put));
      else if ("patch" in o) {
        const cur = this.data[o.store].get(o.patch);
        const next = cur ? o.fn(clone(cur)) : null;
        if (next) this.data[o.store].set(next.id, clone(next));
        patched.push(next ? clone(next) : null);
      } else this.data[o.store].delete(o.delete);
    }
    return patched;
  }

  async clear() {
    for (const s of STORES) this.data[s].clear();
  }

  close() {}
}

function clone(v) {
  // structuredClone keeps Blobs; a record is never shared between the store and its caller
  return typeof structuredClone === "function" ? structuredClone(v) : JSON.parse(JSON.stringify(v));
}

// ------------------------------------------------------------------------------------------------- store

/**
 * Open the store. opts: {indexedDB (default the page's), name, memory: true for the in-memory store}. Never throws:
 * a failure to open IndexedDB gives the in-memory store, with `store.persistent === false` and `store.openError`.
 */
export async function openStore(opts = {}) {
  let backend = null;
  let openError = null;
  const idb = opts.indexedDB ?? globalThis.indexedDB;
  if (!opts.memory && idb) {
    try { backend = await IDBBackend.open(idb, opts.name || DB_NAME); } catch (err) { openError = err; }
  }
  if (!backend) backend = new MemoryBackend();
  return new Store(backend, openError);
}

const now = () => Date.now();

export class Store {
  constructor(backend, openError = null) {
    this.backend = backend;
    this.persistent = backend.persistent;
    this.openError = openError;
    const b = backend;
    const self = this;

    this.projects = {
      async list({ archived = false } = {}) {
        const all = await b.all("projects");
        return all.filter((p) => (archived === null ? true : Boolean(p.archived) === Boolean(archived))).sort(byUpdatedDesc);
      },
      get: (id) => b.get("projects", id),
      async create({ name, description = "", instructions = "", color = "", icon = "", defaults = {}, web } = {}) {
        const t0 = now();
        const p = {
          id: uuid(), name: String(name || "").trim() || t("core.project.untitled"), description, instructions, color, icon,
          archived: false, createdAt: t0, updatedAt: t0,
          defaults: { web: false, ...defaults, ...(web === undefined ? {} : { web: Boolean(web) }) },
          approvals: {},
        };
        await b.bulk([{ store: "projects", put: p }]);
        return p;
      },
      async update(id, patch = {}) {
        const [next] = await b.bulk([{ store: "projects", patch: id, fn: (cur) => ({ ...cur, ...patch, id: cur.id, createdAt: cur.createdAt, updatedAt: now() }) }]);
        if (!next) throw new Error(`no project ${id}`);
        return next;
      },
      /**
       * Change some of a project's defaults (web, compute, provider, model), merged into the stored ones inside the
       * write's transaction: a caller holding an older copy of the project cannot put back a default someone else
       * changed meanwhile. A key whose value is undefined is removed (the project follows the global setting again).
       */
      async setDefaults(id, partial = {}) {
        const [next] = await b.bulk([{
          store: "projects", patch: id,
          fn: (cur) => {
            const defaults = { ...(cur.defaults || {}) };
            for (const [k, v] of Object.entries(partial || {})) {
              if (v === undefined) delete defaults[k];
              else defaults[k] = v;
            }
            return { ...cur, defaults, updatedAt: now() };
          },
        }]);
        if (!next) throw new Error(`no project ${id}`);
        return next;
      },
      /**
       * Grant standing approvals ("project") and revoke others, inside the write's transaction, so a grant made from
       * an older copy of the record cannot bring back an approval revoked meanwhile (or drop one granted meanwhile).
       */
      async patchApprovals(id, { grant = [], revoke = [] } = {}) {
        const [next] = await b.bulk([{
          store: "projects", patch: id,
          fn: (cur) => {
            const approvals = { ...(cur.approvals || {}) };
            for (const k of grant) approvals[k] = "project";
            for (const k of revoke) delete approvals[k];
            return { ...cur, approvals, updatedAt: now() };
          },
        }]);
        if (!next) throw new Error(`no project ${id}`);
        return next;
      },
      async remove(id) {
        const convs = await b.byIndex("conversations", "projectId", id);
        const ops = [];
        for (const c of convs) {
          for (const m of await b.byIndex("messages", "conversationId", c.id)) ops.push({ store: "messages", delete: m.id });
          ops.push({ store: "conversations", delete: c.id });
        }
        for (const f of await b.byIndex("files", "projectId", id)) ops.push({ store: "files", delete: f.id });
        ops.push({ store: "projects", delete: id });
        await b.bulk(ops);
      },
    };

    this.conversations = {
      async list(projectId) {
        const all = projectId === undefined || projectId === null ? await b.all("conversations") : await b.byIndex("conversations", "projectId", projectId);
        return all.sort((a, z) => Number(Boolean(z.pinned)) - Number(Boolean(a.pinned)) || byUpdatedDesc(a, z));
      },
      async recent(limit = 20) {
        return (await b.all("conversations")).sort(byUpdatedDesc).slice(0, limit);
      },
      get: (id) => b.get("conversations", id),
      async create({ projectId, title = "", model, provider } = {}) {
        if (!projectId) throw new Error("a conversation belongs to a project");
        const t0 = now();
        const c = { id: uuid(), projectId, title: String(title || ""), createdAt: t0, updatedAt: t0, leafId: null, pinned: false };
        if (model) c.model = model;
        if (provider) c.provider = provider;
        await b.bulk([{ store: "conversations", put: c }, touch("projects", projectId, t0)]);
        return c;
      },
      async update(id, patch = {}) {
        const [next] = await b.bulk([{
          store: "conversations", patch: id,
          fn: (cur) => ({ ...cur, ...patch, id: cur.id, projectId: patch.projectId || cur.projectId, createdAt: cur.createdAt, updatedAt: patch.updatedAt ?? now() }),
        }]);
        if (!next) throw new Error(`no conversation ${id}`);
        return next;
      },
      async remove(id) {
        const ops = (await b.byIndex("messages", "conversationId", id)).map((m) => ({ store: "messages", delete: m.id }));
        ops.push({ store: "conversations", delete: id });
        await b.bulk(ops);
      },
    };

    this.messages = {
      async list(conversationId) {
        return (await b.byIndex("messages", "conversationId", conversationId)).sort(byCreated);
      },
      get: (id) => b.get("messages", id),
      /** Insert or replace. Fills id and createdAt; the conversation's updatedAt follows its newest message. */
      async put(message) {
        if (!message?.conversationId) throw new Error("a message needs conversationId");
        const m = { ...message };
        if (!m.id) m.id = uuid();
        if (!m.createdAt) m.createdAt = now();
        if (m.parentId === undefined) m.parentId = null;
        await b.bulk([{ store: "messages", put: m }, touch("conversations", m.conversationId, now())]);
        return m;
      },
      async remove(id) {
        await b.bulk([{ store: "messages", delete: id }]);
      },
      /** The messages of one branch, root first: from conversation.leafId (or `leafId`) up through parentId. */
      async path(conversationId, leafId) {
        const all = await self.messages.list(conversationId);
        const conv = await b.get("conversations", conversationId);
        return threadPath(all, leafId ?? conv?.leafId ?? null);
      },
    };

    this.files = {
      async list(projectId) {
        return (await b.byIndex("files", "projectId", projectId)).sort(byCreated);
      },
      get: (id) => b.get("files", id),
      /**
       * Store a File or Blob; its sha256 is computed here, unless `sha256` (64 hex digits, already computed from this
       * same blob, as the composer does) is given: a large file is then read once, not twice. Returns the record.
       */
      async add(projectId, blob, { name, source = "upload", runnerUploadId, sha256: known } = {}) {
        if (!projectId) throw new Error("a file belongs to a project");
        if (!blob || typeof blob.arrayBuffer !== "function") throw new TypeError("files.add needs a File or Blob");
        const sha256 = typeof known === "string" && /^[0-9a-f]{64}$/i.test(known) ? known.toLowerCase() : await sha256Hex(blob);
        const rec = {
          id: uuid(), projectId, name: String(name || blob.name || "file"), type: blob.type || "application/octet-stream",
          bytes: blob.size, sha256, blob, source: source === "tool" ? "tool" : "upload", createdAt: now(),
        };
        if (runnerUploadId) rec.runnerUploadId = runnerUploadId;
        await b.bulk([{ store: "files", put: rec }, touch("projects", projectId, rec.createdAt)]);
        return rec;
      },
      async update(id, patch = {}) {
        const [next] = await b.bulk([{ store: "files", patch: id, fn: (cur) => ({ ...cur, ...patch, id: cur.id, projectId: cur.projectId, sha256: cur.sha256, blob: cur.blob, bytes: cur.bytes }) }]);
        if (!next) throw new Error(`no file ${id}`);
        return next;
      },
      async remove(id) {
        await b.bulk([{ store: "files", delete: id }]);
      },
    };
  }

  /**
   * Local search over project names and instructions, conversation titles and message text. Every word of the
   * query must appear (case-insensitive; Chinese matched as written). → [{type, id, conversationId?, projectId,
   * snippet}], best first, at most `limit`.
   */
  async search(query, { limit = 50 } = {}) {
    const words = String(query || "").normalize("NFKC").toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return [];
    const b = this.backend;
    const [projects, conversations, messages] = await Promise.all([b.all("projects"), b.all("conversations"), b.all("messages")]);
    const convById = new Map(conversations.map((c) => [c.id, c]));
    const hits = [];
    const test = (text) => {
      const s = String(text || "").normalize("NFKC").toLowerCase();
      return words.every((w) => s.includes(w)) ? s : null;
    };
    for (const p of projects) {
      const text = [p.name, p.description, p.instructions].filter(Boolean).join(" · ");
      if (test(text)) hits.push({ type: "project", id: p.id, projectId: p.id, snippet: snippet(text, words), rank: 3, at: p.updatedAt });
    }
    for (const c of conversations) {
      if (test(c.title)) hits.push({ type: "conversation", id: c.id, conversationId: c.id, projectId: c.projectId, snippet: snippet(c.title, words), rank: 2, at: c.updatedAt });
    }
    for (const m of messages) {
      if (m.role === "tool") continue;
      const text = typeof m.content === "string" ? m.content : "";
      if (!test(text)) continue;
      const conv = convById.get(m.conversationId);
      if (!conv) continue;
      hits.push({ type: "message", id: m.id, conversationId: m.conversationId, projectId: conv.projectId, snippet: snippet(text, words), rank: 1, at: m.createdAt });
    }
    hits.sort((a, z) => z.rank - a.rank || (z.at || 0) - (a.at || 0));
    return hits.slice(0, limit).map(({ rank, at, ...h }) => h);
  }

  /** The project, its conversations, messages and files (bytes in base64, each with its sha256) as a JSON Blob. */
  async exportProject(projectId) {
    const b = this.backend;
    const project = await b.get("projects", projectId);
    if (!project) throw new Error(`no project ${projectId}`);
    const conversations = await b.byIndex("conversations", "projectId", projectId);
    const messages = [];
    for (const c of conversations) messages.push(...(await b.byIndex("messages", "conversationId", c.id)));
    const files = [];
    for (const f of await b.byIndex("files", "projectId", projectId)) {
      const { blob, ...meta } = f;
      files.push({ ...meta, data: blob ? await blobToBase64(blob) : null });
    }
    const doc = {
      format: EXPORT_FORMAT, exportedAt: new Date().toISOString(), app: "TCMScience Studio",
      project, conversations, messages: messages.sort(byCreated), files,
    };
    return new Blob([JSON.stringify(doc)], { type: "application/json" });
  }

  /**
   * Import an exported project as a new project (new ids throughout, links rewritten), so importing twice gives two
   * copies and never overwrites. Every file's bytes must match its sha256, or nothing is imported.
   */
  async importProject(blobOrText) {
    const text = typeof blobOrText === "string" ? blobOrText : await blobOrText.text();
    let doc;
    try { doc = JSON.parse(text); } catch { throw new Error("not a TCMScience Studio project file (invalid JSON)"); }
    if (!doc || doc.format !== EXPORT_FORMAT || !doc.project) throw new Error(`not a TCMScience Studio project file (expected format ${EXPORT_FORMAT})`);
    const ids = new Map();
    const map = (old) => (old == null ? old : ids.get(old) || (ids.set(old, uuid()), ids.get(old)));
    const ops = [];
    const t0 = now();
    // trust does not travel with a file: standing approvals and web access are this browser's decisions to make again
    const project = {
      ...doc.project, id: map(doc.project.id), updatedAt: t0, archived: false, approvals: {},
      defaults: { ...(doc.project.defaults || {}), web: false },
    };
    ops.push({ store: "projects", put: project });
    const fileRecs = [];
    for (const f of doc.files || []) {
      const bytes = f.data ? base64ToBytes(f.data) : new Uint8Array();
      const sha = await sha256Hex(bytes);
      if (f.sha256 && sha !== f.sha256) throw new Error(`file ${f.name}: content does not match its sha256 (${f.sha256.slice(0, 12)}…); nothing was imported`);
      const { data, ...meta } = f;
      fileRecs.push({ ...meta, id: map(f.id), projectId: project.id, sha256: sha, bytes: bytes.length, blob: new Blob([bytes], { type: f.type || "application/octet-stream" }) });
    }
    for (const c of doc.conversations || []) {
      ops.push({ store: "conversations", put: { ...c, id: map(c.id), projectId: project.id, leafId: c.leafId ? map(c.leafId) : null } });
    }
    for (const m of doc.messages || []) {
      const rec = { ...m, id: map(m.id), conversationId: map(m.conversationId), parentId: m.parentId ? map(m.parentId) : null };
      if (Array.isArray(m.attachments)) rec.attachments = m.attachments.map(map);
      ops.push({ store: "messages", put: rec });
    }
    for (const f of fileRecs) ops.push({ store: "files", put: f });
    await this.backend.bulk(ops);
    return project.id;
  }

  /** Delete everything this app stored in this browser. */
  async clearAll() {
    await this.backend.clear();
  }

  close() {
    this.backend.close();
  }
}

/** A patch op that moves a record's updatedAt forward (never back), inside the writer's transaction. */
function touch(store, id, at) {
  return { store, patch: id, fn: (cur) => ({ ...cur, updatedAt: Math.max(cur.updatedAt || 0, at) }) };
}

function byUpdatedDesc(a, z) {
  return (z.updatedAt || 0) - (a.updatedAt || 0);
}

function byCreated(a, z) {
  return (a.createdAt || 0) - (z.createdAt || 0);
}

function snippet(text, words, radius = 40) {
  const s = String(text || "").replace(/\s+/g, " ");
  const low = s.normalize("NFKC").toLowerCase();
  const at = Math.max(0, low.indexOf(words[0]));
  const start = Math.max(0, at - radius);
  const end = Math.min(s.length, at + words[0].length + radius);
  return (start > 0 ? "…" : "") + s.slice(start, end) + (end < s.length ? "…" : "");
}

// ------------------------------------------------------------------------------------------------- branches

/**
 * The branch ending at `leafId`, root first. Without a leaf (or with one that is gone), the newest leaf of the
 * tree. Tool messages hang off their assistant message and are not part of the path.
 */
export function threadPath(messages, leafId) {
  const byId = new Map(messages.map((m) => [m.id, m]));
  let leaf = leafId && byId.get(leafId);
  if (!leaf) {
    const roots = messages.filter((m) => !m.parentId && m.role !== "tool");
    leaf = roots.length ? byId.get(latestLeaf(messages, roots[roots.length - 1].id)) : null;
  }
  const path = [];
  const seen = new Set();
  for (let m = leaf; m && !seen.has(m.id); m = m.parentId ? byId.get(m.parentId) : null) {
    seen.add(m.id);
    path.push(m);
  }
  return path.reverse();
}

/** The messages that share `id`'s parent (itself included), oldest first: the ‹1/2› switcher's choices. */
export function siblingsOf(messages, id) {
  const m = messages.find((x) => x.id === id);
  if (!m) return [];
  return messages.filter((x) => x.parentId === m.parentId && x.role === m.role && x.role !== "tool").sort(byCreated);
}

/** Follow the newest child from `fromId` down to a leaf (tool messages are not branches). */
export function latestLeaf(messages, fromId) {
  const children = new Map();
  for (const m of messages) {
    if (m.role === "tool") continue;
    const k = m.parentId || null;
    if (!children.has(k)) children.set(k, []);
    children.get(k).push(m);
  }
  let cur = fromId;
  const seen = new Set();
  for (;;) {
    if (seen.has(cur)) return cur;
    seen.add(cur);
    const kids = (children.get(cur) || []).sort(byCreated);
    if (!kids.length) return cur;
    cur = kids[kids.length - 1].id;
  }
}

/** Tool messages of an assistant message, in call order. */
export function toolMessagesOf(messages, assistantId) {
  return messages.filter((m) => m.role === "tool" && m.parentId === assistantId).sort(byCreated);
}
