// The browser's own file system for datasets and jobs: the Origin Private File System (OPFS), the one place a page can
// keep hundreds of megabytes of raw files without the person choosing a folder. Layout (the job spec §2):
//
//   tcmstudio/datasets/<group>/raw/<file>                  complete raw files only
//   tcmstudio/datasets/<group>/raw/.meta/<file>.json       how each was obtained and checked (tcmstudio.raw-file/1)
//   tcmstudio/datasets/<group>/raw/.part/<file>(.json)     a partial download and its identity record
//   tcmstudio/jobs/<job_id>/{out,state,checkpoints}/…      job outputs, research state, stage checkpoints
//
// Paths are arrays of segments from the OPFS root, written as the layout above (["tcmstudio", "jobs", id]), so the
// page, the data worker and the science workers name the same file the same way. A store only reaches inside its
// `base` ("tcmstudio" by default): a path outside it is refused, as is a segment that is empty, "." or "..", or holds
// "/" or "\" — a path names one entry, never climbs out of the base.
//
// Writing: through createWritable() where the browser has it (the bytes replace the file only when it closes), else
// through a sync access handle, which browsers give to dedicated workers only; a page with neither is told so.

const domName = (err) => (err && typeof err === "object" ? err.name : "");

function storeError(code, message, cause) {
  const err = new Error(message);
  err.code = code;
  if (cause) err.cause = cause;
  return err;
}

const describe = (err) => `${domName(err) || "Error"}: ${err?.message || err}`;

/** A copy of `path` if every segment is a plain name; otherwise an Error with code "bad_path". */
export function checkPath(path) {
  if (!Array.isArray(path)) throw storeError("bad_path", `a path is a list of names, not ${JSON.stringify(path)}`);
  return path.map((seg) => {
    if (typeof seg !== "string" || !seg || seg === "." || seg === ".." || /[/\\\0]/.test(seg)) {
      throw storeError("bad_path", `${JSON.stringify(seg)} is not a file or directory name (in ${JSON.stringify(path)})`);
    }
    return seg;
  });
}

export class OpfsStore {
  #root = null;
  #getRoot;
  #base;
  #storage;

  /**
   * {root: FileSystemDirectoryHandle} or {getRoot: async () => handle}; base: the segments this store may touch
   * (["tcmstudio"]); storage: the StorageManager for estimate() and persist() (navigator.storage).
   */
  constructor({ root = null, getRoot = null, base = ["tcmstudio"], storage = globalThis.navigator?.storage ?? null } = {}) {
    if (!root && typeof getRoot !== "function") throw new TypeError("OpfsStore needs a root directory handle or getRoot()");
    this.#root = root;
    this.#getRoot = getRoot;
    this.#base = Object.freeze(checkPath(base));
    if (!this.#base.length) throw storeError("bad_path", "the store's base must name a directory inside the OPFS root");
    this.#storage = storage;
  }

  /**
   * The store over this browser's OPFS → {store, reason}: store null with the reason when there is no OPFS here (an
   * old browser, a private window that refuses it, storage blocked for the site).
   */
  static async open({ storage = globalThis.navigator?.storage, base = ["tcmstudio"] } = {}) {
    if (!storage || typeof storage.getDirectory !== "function") {
      return { store: null, reason: "this browser has no Origin Private File System (navigator.storage.getDirectory is missing)" };
    }
    let root;
    try {
      root = await storage.getDirectory();
      // the base is created at once: a browser that hands out a root but refuses writes says so here, not mid-job
      let dir = root;
      for (const seg of checkPath(base)) dir = await dir.getDirectoryHandle(seg, { create: true });
    } catch (err) {
      return { store: null, reason: `the Origin Private File System is not available here (${describe(err)}); private windows and blocked site data refuse it` };
    }
    return { store: new OpfsStore({ root, base, storage }), reason: null };
  }

  get base() {
    return [...this.#base];
  }

  /** The OPFS root directory handle. */
  async root() {
    if (!this.#root) this.#root = await this.#getRoot();
    return this.#root;
  }

  #inside(path, { file = false } = {}) {
    const p = checkPath(path);
    const base = this.#base;
    if (p.length < base.length || base.some((seg, i) => p[i] !== seg)) {
      throw storeError("bad_path", `${JSON.stringify(path)} is outside this store (${base.join("/")}/…)`);
    }
    if (file && p.length === base.length) throw storeError("bad_path", `${JSON.stringify(path)} is the store's own directory, not a file`);
    return p;
  }

  /** The directory handle at `path`, created with its parents when `create`; null when it does not exist. */
  async dir(path, { create = false } = {}) {
    return this.#walk(this.#inside(path), create);
  }

  async #walk(segments, create) {
    let dir = await this.root();
    for (const seg of segments) {
      try {
        dir = await dir.getDirectoryHandle(seg, { create });
      } catch (err) {
        if (domName(err) === "NotFoundError" && !create) return null;
        if (domName(err) === "TypeMismatchError") throw storeError("bad_path", `${segments.join("/")}: ${seg} is a file, not a directory`, err);
        throw err;
      }
    }
    return dir;
  }

  /** The file handle at `path` (its directories created too when `create`); null when it does not exist. */
  async file(path, { create = false } = {}) {
    const p = this.#inside(path, { file: true });
    const dir = await this.#walk(p.slice(0, -1), create);
    if (!dir) return null;
    try {
      return await dir.getFileHandle(p[p.length - 1], { create });
    } catch (err) {
      if (domName(err) === "NotFoundError" && !create) return null;
      if (domName(err) === "TypeMismatchError") throw storeError("bad_path", `${p.join("/")} is a directory, not a file`, err);
      throw err;
    }
  }

  /** "file" | "directory" | null: what is at `path`. */
  async kind(path) {
    const p = this.#inside(path);
    if (p.length === this.#base.length) return (await this.#walk(p, false)) ? "directory" : null;
    const parent = await this.#walk(p.slice(0, -1), false);
    if (!parent) return null;
    const name = p[p.length - 1];
    for (const k of ["file", "directory"]) {
      try {
        await (k === "file" ? parent.getFileHandle(name) : parent.getDirectoryHandle(name));
        return k;
      } catch (err) {
        if (domName(err) !== "TypeMismatchError" && domName(err) !== "NotFoundError") throw err;
      }
    }
    return null;
  }

  async exists(path) {
    return (await this.kind(path)) !== null;
  }

  /** {kind, bytes, lastModified} for a file ({kind:"directory", bytes:null, lastModified:null} for a directory), or
   * null when nothing is there. A file another context is writing through a sync access handle cannot be read. */
  async stat(path) {
    const kind = await this.kind(path);
    if (kind === null) return null;
    if (kind === "directory") return { kind, bytes: null, lastModified: null };
    const handle = await this.file(path);
    if (!handle) return null;
    const f = await handle.getFile();
    return { kind, bytes: f.size, lastModified: f.lastModified };
  }

  /** [{name, kind}] in `path`, sorted by name; [] when the directory does not exist. */
  async list(path) {
    const dir = await this.dir(path);
    if (!dir) return [];
    const out = [];
    for await (const [name, handle] of dir.entries()) out.push({ name, kind: handle.kind });
    return out.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  }

  /** The file as a File, or null. */
  async readFile(path) {
    const handle = await this.file(path);
    return handle ? handle.getFile() : null;
  }

  async readBytes(path) {
    const f = await this.readFile(path);
    return f ? new Uint8Array(await f.arrayBuffer()) : null;
  }

  async readText(path) {
    const f = await this.readFile(path);
    return f ? f.text() : null;
  }

  /** The parsed JSON at `path`, or null when there is no file. A file that is not JSON throws (code "corrupt"):
   * a damaged record is not the same as no record. */
  async readJson(path) {
    const text = await this.readText(path);
    if (text === null) return null;
    try {
      return JSON.parse(text);
    } catch (err) {
      throw storeError("corrupt", `${path.join("/")} is not valid JSON (${err.message})`, err);
    }
  }

  /** Replace the file at `path` (its directories created) with `bytes`. */
  async writeBytes(path, bytes) {
    const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const handle = await this.file(path, { create: true });
    if (typeof handle.createWritable === "function") {
      const w = await handle.createWritable();
      try {
        await w.write(data);
        await w.close();
      } catch (err) {
        try {
          await w.abort();
        } catch { /* already closed or errored: nothing was committed */ }
        throw storageFailure(path, err);
      }
      return data.length;
    }
    if (typeof handle.createSyncAccessHandle === "function") {
      const h = await handle.createSyncAccessHandle();
      try {
        // write first, then cut to length: the file is never empty in between
        const n = h.write(data, { at: 0 });
        if (n !== data.length) throw storeError("storage", `wrote ${n} of ${data.length} bytes`);
        await h.truncate(data.length);
        await h.flush();
      } catch (err) {
        throw storageFailure(path, err);
      } finally {
        await h.close();
      }
      return data.length;
    }
    throw storeError("storage", "this browser can write OPFS files only from a worker (no createWritable here, and sync access handles exist in workers only)");
  }

  async writeText(path, text) {
    return this.writeBytes(path, new TextEncoder().encode(String(text)));
  }

  /** JSON with two-space indents and a final newline (the form Python's json.dump(indent=2) records are read in). */
  async writeJson(path, value) {
    return this.writeText(path, `${JSON.stringify(value, null, 2)}\n`);
  }

  /** Remove the file or directory at `path` (a non-empty directory only when `recursive`) → whether anything was
   * there. Removing the store's own base is allowed: it is how all local data is cleared. */
  async remove(path, { recursive = false } = {}) {
    const p = this.#inside(path);
    const parent = await this.#walk(p.slice(0, -1), false);
    if (!parent) return false;
    try {
      await parent.removeEntry(p[p.length - 1], { recursive });
      return true;
    } catch (err) {
      if (domName(err) === "NotFoundError") return false;
      if (domName(err) === "InvalidModificationError") throw storeError("not_empty", `${p.join("/")} is not empty (remove it with recursive: true)`, err);
      if (domName(err) === "NoModificationAllowedError") throw storeError("busy", `${p.join("/")} is open elsewhere (a download or a job is using it)`, err);
      throw err;
    }
  }

  /**
   * Give the file at `from` the path `to`, replacing a file there → "move" when the browser moved it in place
   * (FileSystemHandle.move), "copy" when it was copied and the original removed (browsers without move()). A copy
   * needs room for both copies for a moment; one that fails removes what it wrote and keeps the original.
   */
  async move(from, to, { signal } = {}) {
    const src = await this.file(from);
    if (!src) throw storeError("not_found", `${from.join("/")} does not exist`);
    const dest = this.#inside(to, { file: true });
    const destDir = await this.dir(dest.slice(0, -1), { create: true });
    const name = dest[dest.length - 1];
    if (typeof src.move === "function") {
      await this.remove(dest);
      try {
        await src.move(destDir, name);
        return "move";
      } catch (err) {
        // a browser whose move() does not take this form (NotSupportedError, TypeError): the copy below does the
        // same; anything else (a lock, the quota) is the answer
        if (domName(err) !== "NotSupportedError" && !(err instanceof TypeError)) throw storageFailure(to, err);
      }
    }
    await this.copy(from, to, { signal });
    await this.remove(from);
    return "copy";
  }

  /** Copy the file at `from` to `to` (replacing it), streaming, never whole in memory. */
  async copy(from, to, { signal, chunk = 4 << 20 } = {}) {
    const src = await this.readFile(from);
    if (!src) throw storeError("not_found", `${from.join("/")} does not exist`);
    const handle = await this.file(to, { create: true });
    try {
      if (typeof handle.createWritable === "function") {
        const w = await handle.createWritable();
        await src.stream().pipeTo(w, { signal });
      } else if (typeof handle.createSyncAccessHandle === "function") {
        const h = await handle.createSyncAccessHandle();
        try {
          await h.truncate(0);
          for (let at = 0; at < src.size; at += chunk) {
            if (signal?.aborted) throw signal.reason ?? new DOMException("the copy was cancelled", "AbortError");
            const piece = new Uint8Array(await src.slice(at, at + chunk).arrayBuffer());
            if (h.write(piece, { at }) !== piece.length) throw storeError("storage", `a short write at byte ${at}`);
          }
          await h.flush();
        } finally {
          await h.close();
        }
      } else {
        throw storeError("storage", "this browser can write OPFS files only from a worker");
      }
    } catch (err) {
      await this.remove(to).catch(() => {});
      throw domName(err) === "AbortError" ? err : storageFailure(to, err);
    }
    return src.size;
  }

  /**
   * Replace the file at `path` with the bytes of `blob` (a Blob or File from anywhere: IndexedDB, an input element),
   * read and written in pieces of `chunk` bytes, never whole in memory → the bytes written. onChunk(bytes) sees each
   * piece as it is written, in order (a caller hashes what was written, not what it was told was there). A write that
   * fails or is cancelled removes what it wrote.
   */
  async writeBlob(path, blob, { signal, chunk = 4 << 20, onChunk } = {}) {
    if (!blob || typeof blob.slice !== "function" || !Number.isFinite(blob.size)) throw storeError("bad_input", "writeBlob needs a Blob or File");
    const handle = await this.file(path, { create: true });
    const pieces = async (write) => {
      for (let at = 0; at < blob.size; at += chunk) {
        if (signal?.aborted) throw signal.reason ?? new DOMException("the copy was cancelled", "AbortError");
        const piece = new Uint8Array(await blob.slice(at, Math.min(blob.size, at + chunk)).arrayBuffer());
        await write(piece, at);
        onChunk?.(piece);
      }
    };
    try {
      if (typeof handle.createWritable === "function") {
        const w = await handle.createWritable();
        try {
          await pieces((piece) => w.write(piece));
          await w.close();
        } catch (err) {
          try { await w.abort(); } catch { /* already closed or errored: nothing was committed */ }
          throw err;
        }
      } else if (typeof handle.createSyncAccessHandle === "function") {
        const h = await handle.createSyncAccessHandle();
        try {
          await h.truncate(0);
          await pieces((piece, at) => {
            if (h.write(piece, { at }) !== piece.length) throw storeError("storage", `a short write at byte ${at}`);
          });
          await h.flush();
        } finally {
          await h.close();
        }
      } else {
        throw storeError("storage", "this browser can write OPFS files only from a worker");
      }
    } catch (err) {
      await this.remove(path).catch(() => {});
      throw domName(err) === "AbortError" ? err : storageFailure(path, err);
    }
    return blob.size;
  }

  /** {usage, quota, persisted} from the StorageManager; a value the browser does not give is null. */
  async estimate() {
    const s = this.#storage;
    let usage = null;
    let quota = null;
    let persisted = null;
    try {
      const e = typeof s?.estimate === "function" ? await s.estimate() : null;
      usage = Number.isFinite(e?.usage) ? e.usage : null;
      quota = Number.isFinite(e?.quota) ? e.quota : null;
    } catch { /* no estimate here */ }
    try {
      persisted = typeof s?.persisted === "function" ? Boolean(await s.persisted()) : null;
    } catch { /* unknown */ }
    return { usage, quota, persisted };
  }

  /** Ask the browser to keep this site's storage under pressure (it may ask the person, or decide itself) → granted. */
  async persist() {
    const s = this.#storage;
    if (typeof s?.persist !== "function") return false;
    try {
      return Boolean(await s.persist());
    } catch {
      return false;
    }
  }
}

/** A write that failed, with the reason in words: a full quota is the one a person can act on. */
function storageFailure(path, err) {
  if (typeof err?.code === "string") return err; // ours already (a DOMException's code is a legacy number)
  if (domName(err) === "QuotaExceededError") {
    return storeError("quota", `no room to write ${path.join("/")}: the browser's storage quota for this site is used up (${describe(err)})`, err);
  }
  return storeError("storage", `could not write ${path.join("/")} (${describe(err)})`, err);
}
