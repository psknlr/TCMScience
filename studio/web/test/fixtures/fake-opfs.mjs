// An in-memory Origin Private File System for the node tests: navigator.storage.getDirectory() and the handles it
// gives, as the File System standard and the browsers define them, strictly enough that code which misuses them
// fails here as it would in a browser:
//   - directory handles: getDirectoryHandle / getFileHandle ({create}), removeEntry ({recursive}), entries() /
//     values() / keys() / for await, isSameEntry; NotFoundError, TypeMismatchError, InvalidModificationError and
//     TypeError (a bad name) where a browser throws them;
//   - file handles: getFile() (a File snapshot), createWritable() (a WritableStream whose bytes replace the file only
//     on close, so pipeTo works), createSyncAccessHandle({mode}) (read/write {at}, truncate, getSize, flush, close)
//     and move();
//   - locks: one open sync access handle per file in "readwrite" mode, shared "read-only" or "readwrite-unsafe"
//     handles, writables shared among themselves; a locked file cannot be opened otherwise, removed, moved or read by
//     getFile() (NoModificationAllowedError); a removed entry's handles throw NotFoundError;
//   - a quota (QuotaExceededError) and storage.estimate() / persisted() / persist().
// Options turn off what some browsers lack: syncAccessHandles (the main thread has none), writables (older Safari),
// move (Safari).

const MiB = 1 << 20;
const domError = (name, message) => new DOMException(message, name);

function checkName(name) {
  const s = String(name);
  if (!s || s === "." || s === ".." || s.includes("/") || s.includes("\\") || s.includes("\0")) {
    throw new TypeError(`Name is not allowed: ${JSON.stringify(name)}`);
  }
  return s;
}

function bytesOf(data) {
  if (data instanceof Uint8Array) return data;
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (typeof data === "string") return new TextEncoder().encode(data);
  throw new TypeError("expected an ArrayBuffer, a view, a Blob or a string");
}

class FileNode {
  constructor() {
    this.kind = "file";
    this.data = new Uint8Array(0);
    this.size = 0;
    this.lastModified = Date.now();
    this.removed = false;
    this.sync = []; // open sync access handles: {mode}
    this.writables = 0;
  }

  bytes() {
    return this.data.subarray(0, this.size);
  }

  /** Make room for `n` bytes, keeping what is there (doubling, so streaming writes are not quadratic). */
  reserve(n) {
    if (n <= this.data.length) return;
    const next = new Uint8Array(Math.max(n, this.data.length * 2, 64));
    next.set(this.bytes());
    this.data = next;
  }

  replace(bytes) {
    this.data = new Uint8Array(bytes);
    this.size = bytes.length;
    this.lastModified = Date.now();
  }
}

class DirNode {
  constructor() {
    this.kind = "directory";
    this.children = new Map();
    this.removed = false;
  }
}

class FakeFs {
  constructor({ quota = 8 * 1024 * MiB, syncAccessHandles = true, writables = true, move = true } = {}) {
    this.quota = quota;
    this.options = { syncAccessHandles, writables, move };
    this.rootNode = new DirNode();
    this.root = new FakeDirectoryHandle(this, this.rootNode, "");
  }

  usage(node = this.rootNode) {
    if (node.kind === "file") return node.size;
    let n = 0;
    for (const child of node.children.values()) n += this.usage(child);
    return n;
  }

  /** Throws QuotaExceededError when growing `node` to `size` bytes would pass the quota. */
  reserve(node, size) {
    const grow = size - node.size;
    if (grow > 0 && this.usage() + grow > this.quota) throw domError("QuotaExceededError", `the quota of ${this.quota} bytes is used up`);
  }

  /** Open handles anywhere in the tree (sync access handles and writables): a test's check that nothing leaked. */
  openHandles(node = this.rootNode, path = []) {
    if (node.kind === "file") return node.sync.length || node.writables ? [{ path: path.join("/"), sync: node.sync.map((s) => s.mode), writables: node.writables }] : [];
    return [...node.children].flatMap(([name, child]) => this.openHandles(child, [...path, name]));
  }

  /** {path: bytes} for every file below `node`: what the store holds, for assertions. */
  files(node = this.rootNode, path = []) {
    if (node.kind === "file") return { [path.join("/")]: node.bytes().slice() };
    return Object.assign({}, ...[...node.children].map(([name, child]) => this.files(child, [...path, name])));
  }
}

function locked(node) {
  if (node.kind === "file") return node.sync.length > 0 || node.writables > 0;
  for (const child of node.children.values()) if (locked(child)) return true;
  return false;
}

function markRemoved(node) {
  node.removed = true;
  if (node.kind === "directory") for (const child of node.children.values()) markRemoved(child);
}

class FakeHandle {
  #fs;
  constructor(fs, node, name, parent) {
    this.#fs = fs;
    this.node = node;
    this.parentNode = parent || null;
    this.name = name;
  }

  get fs() {
    return this.#fs;
  }

  alive() {
    if (this.node.removed) throw domError("NotFoundError", `${this.name} was removed`);
  }

  async isSameEntry(other) {
    return Boolean(other) && other.node === this.node;
  }
}

export class FakeDirectoryHandle extends FakeHandle {
  get kind() {
    return "directory";
  }

  async getDirectoryHandle(name, { create = false } = {}) {
    return this.#child(name, "directory", create);
  }

  async getFileHandle(name, { create = false } = {}) {
    return this.#child(name, "file", create);
  }

  #child(name, kind, create) {
    const n = checkName(name);
    this.alive();
    let node = this.node.children.get(n);
    if (node && node.kind !== kind) throw domError("TypeMismatchError", `${n} is a ${node.kind}, not a ${kind}`);
    if (!node) {
      if (!create) throw domError("NotFoundError", `${n} does not exist`);
      node = kind === "file" ? new FileNode() : new DirNode();
      this.node.children.set(n, node);
    }
    return kind === "file" ? new FakeFileHandle(this.fs, node, n, this.node) : new FakeDirectoryHandle(this.fs, node, n, this.node);
  }

  async removeEntry(name, { recursive = false } = {}) {
    const n = checkName(name);
    this.alive();
    const node = this.node.children.get(n);
    if (!node) throw domError("NotFoundError", `${n} does not exist`);
    if (node.kind === "directory" && node.children.size && !recursive) throw domError("InvalidModificationError", `${n} is not empty`);
    if (locked(node)) throw domError("NoModificationAllowedError", `${n} is open (a sync access handle or a writable holds it)`);
    this.node.children.delete(n);
    markRemoved(node);
  }

  async *entries() {
    this.alive();
    for (const [name, node] of [...this.node.children]) {
      if (!this.node.children.has(name)) continue;
      yield [name, node.kind === "file" ? new FakeFileHandle(this.fs, node, name, this.node) : new FakeDirectoryHandle(this.fs, node, name, this.node)];
    }
  }

  async *keys() {
    for await (const [name] of this.entries()) yield name;
  }

  async *values() {
    for await (const [, handle] of this.entries()) yield handle;
  }

  [Symbol.asyncIterator]() {
    return this.entries();
  }

  async resolve(other) {
    const walk = (node, path) => {
      if (node === other.node) return path;
      if (node.kind !== "directory") return null;
      for (const [name, child] of node.children) {
        const hit = walk(child, [...path, name]);
        if (hit) return hit;
      }
      return null;
    };
    return walk(this.node, []);
  }
}

export class FakeFileHandle extends FakeHandle {
  constructor(fs, node, name, parent) {
    super(fs, node, name, parent);
    // what a browser without these lacks is absent from the object, not a method that throws
    if (!fs.options.syncAccessHandles) this.createSyncAccessHandle = undefined;
    if (!fs.options.writables) this.createWritable = undefined;
    if (!fs.options.move) this.move = undefined;
  }

  get kind() {
    return "file";
  }

  async getFile() {
    this.alive();
    if (this.node.sync.some((s) => s.mode !== "read-only")) throw domError("NoModificationAllowedError", `${this.name} is open for writing through a sync access handle`);
    return new File([this.node.bytes().slice()], this.name, { lastModified: this.node.lastModified });
  }

  async createWritable({ keepExistingData = false } = {}) {
    this.alive();
    const node = this.node;
    const fs = this.fs;
    if (node.sync.length) throw domError("NoModificationAllowedError", `${this.name} is open through a sync access handle`);
    node.writables += 1;
    let buf = keepExistingData ? node.bytes().slice() : new Uint8Array(0);
    let size = buf.length;
    let pos = 0;
    let open = true;
    const release = () => {
      if (open) {
        open = false;
        node.writables -= 1;
      }
    };
    const put = (bytes, at) => {
      if (at + bytes.length > buf.length) {
        const next = new Uint8Array(Math.max(at + bytes.length, buf.length * 2));
        next.set(buf.subarray(0, size));
        buf = next;
      }
      if (at > size) buf.fill(0, size, at);
      buf.set(bytes, at);
      size = Math.max(size, at + bytes.length);
    };
    return new FakeWritable({
      async write(chunk) {
        if (chunk && typeof chunk === "object" && !ArrayBuffer.isView(chunk) && !(chunk instanceof ArrayBuffer) && !(chunk instanceof Blob) && "type" in chunk) {
          if (chunk.type === "seek") { pos = chunk.position; return; }
          if (chunk.type === "truncate") {
            if (chunk.size > buf.length) { const next = new Uint8Array(chunk.size); next.set(buf.subarray(0, size)); buf = next; }
            if (chunk.size > size) buf.fill(0, size, chunk.size);
            size = chunk.size;
            pos = Math.min(pos, size);
            return;
          }
          if (chunk.type !== "write") throw new TypeError(`unknown write type ${chunk.type}`);
          if (Number.isInteger(chunk.position)) pos = chunk.position;
          chunk = chunk.data;
        }
        const bytes = chunk instanceof Blob ? new Uint8Array(await chunk.arrayBuffer()) : bytesOf(chunk);
        put(bytes, pos);
        pos += bytes.length;
      },
      async close() {
        try {
          if (node.removed) throw domError("NotFoundError", "the file was removed while it was written");
          fs.reserve(node, size);
          node.replace(buf.subarray(0, size));
        } finally {
          release();
        }
      },
      abort() {
        release();
      },
    });
  }

  async createSyncAccessHandle({ mode = "readwrite" } = {}) {
    this.alive();
    const node = this.node;
    if (!["readwrite", "read-only", "readwrite-unsafe"].includes(mode)) throw new TypeError(`unknown mode ${mode}`);
    if (node.writables) throw domError("NoModificationAllowedError", `${this.name} is open through a writable`);
    if (node.sync.length && (mode === "readwrite" || node.sync.some((s) => s.mode !== mode))) {
      throw domError("NoModificationAllowedError", `${this.name} already has an open sync access handle (${node.sync.map((s) => s.mode).join(", ")})`);
    }
    const lock = { mode };
    node.sync.push(lock);
    return new FakeSyncAccessHandle(this.fs, node, lock, this.name);
  }

  /** move(newName) | move(directory) | move(directory, newName); an existing file at the destination is replaced. */
  async move(a, b) {
    this.alive();
    const node = this.node;
    if (locked(node)) throw domError("NoModificationAllowedError", `${this.name} is open`);
    const dest = typeof a === "string" ? null : a;
    const name = checkName(typeof a === "string" ? a : b ?? this.name);
    const destNode = dest ? dest.node : this.parentNode;
    if (!destNode || destNode.removed) throw domError("NotFoundError", "the destination directory does not exist");
    const there = destNode.children.get(name);
    if (there && there !== node) {
      if (there.kind !== "file") throw domError("InvalidModificationError", `${name} is a directory`);
      if (locked(there)) throw domError("NoModificationAllowedError", `${name} is open`);
      markRemoved(there);
    }
    for (const [k, v] of this.parentNode.children) if (v === node) this.parentNode.children.delete(k);
    destNode.children.set(name, node);
    this.parentNode = destNode;
    this.name = name;
  }
}

/** A FileSystemWritableFileStream: a WritableStream with write / seek / truncate that take its lock for one call. */
class FakeWritable extends WritableStream {
  async write(data) {
    const w = this.getWriter();
    try {
      await w.write(data);
    } finally {
      w.releaseLock();
    }
  }

  async seek(position) {
    return this.write({ type: "seek", position });
  }

  async truncate(size) {
    return this.write({ type: "truncate", size });
  }
}

class FakeSyncAccessHandle {
  #fs;
  #node;
  #lock;
  #name;
  #pos = 0;
  #closed = false;

  constructor(fs, node, lock, name) {
    this.#fs = fs;
    this.#node = node;
    this.#lock = lock;
    this.#name = name;
  }

  #open(writing = false) {
    if (this.#closed) throw domError("InvalidStateError", `the access handle of ${this.#name} is closed`);
    if (writing && this.#lock.mode === "read-only") throw domError("NoModificationAllowedError", `the access handle of ${this.#name} is read-only`);
  }

  read(buffer, { at } = {}) {
    this.#open();
    const out = bytesOf(buffer);
    const from = at === undefined ? this.#pos : at;
    if (!Number.isInteger(from) || from < 0) throw new TypeError("at must be a non-negative integer");
    const n = Math.max(0, Math.min(out.length, this.#node.size - from));
    if (n) out.set(this.#node.data.subarray(from, from + n));
    this.#pos = from + n;
    return n;
  }

  write(buffer, { at } = {}) {
    this.#open(true);
    const bytes = bytesOf(buffer);
    const node = this.#node;
    const from = at === undefined ? this.#pos : at;
    if (!Number.isInteger(from) || from < 0) throw new TypeError("at must be a non-negative integer");
    const end = from + bytes.length;
    if (end > node.size) this.#fs.reserve(node, end);
    node.reserve(end);
    if (from > node.size) node.data.fill(0, node.size, from);
    node.data.set(bytes, from);
    node.size = Math.max(node.size, end);
    node.lastModified = Date.now();
    this.#pos = end;
    return bytes.length;
  }

  truncate(size) {
    this.#open(true);
    const node = this.#node;
    if (!Number.isInteger(size) || size < 0) throw new TypeError("size must be a non-negative integer");
    if (size > node.size) {
      this.#fs.reserve(node, size);
      node.reserve(size);
      node.data.fill(0, node.size, size);
    }
    node.size = size;
    node.lastModified = Date.now();
    this.#pos = Math.min(this.#pos, size);
  }

  getSize() {
    this.#open();
    return this.#node.size;
  }

  flush() {
    this.#open(true);
  }

  close() {
    if (this.#closed) return;
    this.#closed = true;
    const i = this.#node.sync.indexOf(this.#lock);
    if (i >= 0) this.#node.sync.splice(i, 1);
  }
}

/**
 * A fake navigator.storage with its own OPFS. → {storage, root, fs}: storage.getDirectory() resolves to `root`;
 * estimate() reports the bytes held against `quota`; persist() grants when `grant` is true. `fail` makes
 * getDirectory() reject with that DOMException name (a private window's SecurityError, for one).
 */
export function fakeOpfs({ quota, persisted = false, grant = true, fail = null, ...options } = {}) {
  const fs = new FakeFs({ quota, ...options });
  let isPersisted = persisted;
  const storage = {
    async getDirectory() {
      if (fail) throw domError(fail, "The operation is insecure.");
      return fs.root;
    },
    async estimate() {
      return { usage: fs.usage(), quota: fs.quota };
    },
    async persisted() {
      return isPersisted;
    },
    async persist() {
      if (grant) isPersisted = true;
      return isPersisted;
    },
  };
  return { storage, root: fs.root, fs };
}
