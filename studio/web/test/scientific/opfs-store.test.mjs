// OpfsStore (js/scientific/data/opfs-store.js) over the in-memory OPFS of fixtures/fake-opfs.mjs: opening it (and
// the reasons it cannot be opened), paths that name one entry inside the store and nothing else, reads and writes on
// the main thread (createWritable) and in a worker (sync access handles), removal, moving with and without
// FileSystemHandle.move, the quota, estimate() and persist(). The fake's own locking rules are checked first, since
// the other tests lean on them.
import "../fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fakeOpfs } from "../fixtures/fake-opfs.mjs";
import { OpfsStore, checkPath } from "../../js/scientific/data/opfs-store.js";

const enc = new TextEncoder();
const bytes = (n, seed = 1) => Uint8Array.from({ length: n }, (_, i) => (i * 31 + seed) & 255);

async function opened(options) {
  const fake = fakeOpfs(options);
  const { store, reason } = await OpfsStore.open({ storage: fake.storage });
  assert.equal(reason, null);
  return { store, ...fake };
}

test("the fake OPFS keeps a browser's rules: one writer per file, shared readers, locked files stay put", async () => {
  const { root, fs } = fakeOpfs();
  const dir = await root.getDirectoryHandle("d", { create: true });
  await assert.rejects(root.getDirectoryHandle("missing"), { name: "NotFoundError" });
  await assert.rejects(root.getFileHandle("d"), { name: "TypeMismatchError" });
  await assert.rejects(root.getFileHandle("..", { create: true }), TypeError);
  const fh = await dir.getFileHandle("f", { create: true });
  const h = await fh.createSyncAccessHandle();
  assert.equal(h.write(enc.encode("hello"), { at: 0 }), 5);
  assert.equal(h.write(enc.encode("!"), { at: 7 }), 1); // a gap reads as zeros
  assert.equal(h.getSize(), 8);
  await assert.rejects(fh.createSyncAccessHandle(), { name: "NoModificationAllowedError" });
  await assert.rejects(fh.createWritable(), { name: "NoModificationAllowedError" });
  await assert.rejects(fh.getFile(), { name: "NoModificationAllowedError" });
  await assert.rejects(dir.removeEntry("f"), { name: "NoModificationAllowedError" });
  await assert.rejects(root.removeEntry("d", { recursive: true }), { name: "NoModificationAllowedError" });
  assert.deepEqual(fs.openHandles(), [{ path: "d/f", sync: ["readwrite"], writables: 0 }]);
  const buf = new Uint8Array(8);
  assert.equal(h.read(buf, { at: 0 }), 8);
  assert.deepEqual([...buf], [104, 101, 108, 108, 111, 0, 0, 33]);
  h.truncate(5);
  h.close();
  assert.throws(() => h.getSize(), { name: "InvalidStateError" });
  assert.equal(await (await fh.getFile()).text(), "hello");

  const r1 = await fh.createSyncAccessHandle({ mode: "read-only" });
  const r2 = await fh.createSyncAccessHandle({ mode: "read-only" });
  assert.throws(() => r1.write(enc.encode("x")), { name: "NoModificationAllowedError" });
  await assert.rejects(fh.createSyncAccessHandle(), { name: "NoModificationAllowedError" });
  r1.close();
  r2.close();

  // a writable's bytes replace the file only when it closes
  const w = await fh.createWritable();
  await w.write("new");
  assert.equal(await (await fh.getFile()).text(), "hello");
  await w.close();
  assert.equal(await (await fh.getFile()).text(), "new");

  await assert.rejects(dir.removeEntry("missing"), { name: "NotFoundError" });
  await assert.rejects(root.removeEntry("d"), { name: "InvalidModificationError" });
  await root.removeEntry("d", { recursive: true });
  await assert.rejects(fh.getFile(), { name: "NotFoundError" });
  assert.deepEqual(fs.openHandles(), []);
});

test("open(): the store, or no store and the reason (no OPFS, a refused root)", async () => {
  assert.match((await OpfsStore.open({ storage: null })).reason, /no Origin Private File System/);
  assert.match((await OpfsStore.open({ storage: {} })).reason, /getDirectory is missing/);
  const refused = await OpfsStore.open({ storage: fakeOpfs({ fail: "SecurityError" }).storage });
  assert.equal(refused.store, null);
  assert.match(refused.reason, /SecurityError/);
  const { store, root } = await opened();
  assert.deepEqual(store.base, ["tcmstudio"]);
  assert.ok(await root.getDirectoryHandle("tcmstudio"), "the base is created when the store opens");
  assert.equal(await store.root(), root);
  const lazy = new OpfsStore({ getRoot: async () => root });
  assert.equal(await lazy.root(), root);
  assert.throws(() => new OpfsStore({}), /root directory handle/);
});

test("paths: lists of plain names inside the store's base; '..', '', '/', and anything outside are refused", async () => {
  const { store } = await opened();
  assert.deepEqual(checkPath(["a", "b.json"]), ["a", "b.json"]);
  for (const bad of [["tcmstudio", ".."], ["tcmstudio", ""], ["tcmstudio", "a/b"], ["tcmstudio", "."], ["tcmstudio", "a\\b"], "tcmstudio/a", [1]]) {
    await assert.rejects(store.writeText(bad, "x"), (e) => e.code === "bad_path", JSON.stringify(bad));
  }
  await assert.rejects(store.readText(["elsewhere", "x"]), (e) => e.code === "bad_path" && /outside this store/.test(e.message));
  await assert.rejects(store.dir([]), (e) => e.code === "bad_path");
  await assert.rejects(store.writeText(["tcmstudio"], "x"), (e) => e.code === "bad_path" && /not a file/.test(e.message));
  assert.ok(await store.dir(["tcmstudio"]));
});

test("directories, files, stat and list; JSON records, and a damaged one is not read as missing", async () => {
  const { store } = await opened();
  const p = ["tcmstudio", "datasets", "research", "raw"];
  assert.equal(await store.dir(p), null);
  assert.equal(await store.file([...p, "a.txt"]), null);
  assert.equal(await store.stat([...p, "a.txt"]), null);
  assert.deepEqual(await store.list(p), []);
  assert.equal(await store.exists(p), false);
  assert.equal(await store.writeText([...p, "b.txt"], "bee"), 3);
  await store.writeJson([...p, ".meta", "b.txt.json"], { name: "b.txt", n: 1 });
  await store.writeBytes([...p, "a.txt"], bytes(10));
  assert.equal(await store.exists(p), true);
  assert.equal(await store.kind([...p, ".meta"]), "directory");
  assert.equal(await store.kind([...p, "a.txt"]), "file");
  assert.deepEqual(await store.list(p), [{ name: ".meta", kind: "directory" }, { name: "a.txt", kind: "file" }, { name: "b.txt", kind: "file" }]);
  const st = await store.stat([...p, "a.txt"]);
  assert.equal(st.kind, "file");
  assert.equal(st.bytes, 10);
  assert.ok(Number.isFinite(st.lastModified));
  assert.deepEqual(await store.stat(p), { kind: "directory", bytes: null, lastModified: null });
  assert.equal(await store.readText([...p, "b.txt"]), "bee");
  assert.deepEqual(await store.readBytes([...p, "a.txt"]), bytes(10));
  assert.deepEqual(await store.readJson([...p, ".meta", "b.txt.json"]), { name: "b.txt", n: 1 });
  assert.match(await store.readText([...p, ".meta", "b.txt.json"]), /\n {2}"name": "b.txt",\n.*\n}\n$/s);
  assert.equal(await store.readJson([...p, ".meta", "none.json"]), null);
  await store.writeText([...p, ".meta", "torn.json"], '{"name": "b.t');
  await assert.rejects(store.readJson([...p, ".meta", "torn.json"]), (e) => e.code === "corrupt");
  await assert.rejects(store.writeText([...p, "a.txt", "x"], "y"), (e) => e.code === "bad_path" && /is a file/.test(e.message));
  await assert.rejects(store.readText([...p, ".meta"]), (e) => e.code === "bad_path" && /is a directory/.test(e.message));
});

test("writing: createWritable on the main thread, a sync access handle in a worker, and a clear refusal with neither", async () => {
  const data = bytes(70_000, 7);
  for (const options of [{ syncAccessHandles: false }, { writables: false }]) {
    const { store, fs } = await opened(options);
    await store.writeBytes(["tcmstudio", "x.bin"], bytes(100_000, 3));
    await store.writeBytes(["tcmstudio", "x.bin"], data); // shorter: nothing of the old file is left
    assert.deepEqual(await store.readBytes(["tcmstudio", "x.bin"]), data, JSON.stringify(options));
    assert.deepEqual(fs.openHandles(), []);
  }
  const { store } = await opened({ syncAccessHandles: false, writables: false });
  await assert.rejects(store.writeText(["tcmstudio", "x"], "y"), (e) => e.code === "storage" && /only from a worker/.test(e.message));
});

test("remove: missing is false, a non-empty directory needs recursive, an open file is busy", async () => {
  const { store, root } = await opened();
  const d = ["tcmstudio", "jobs", "bj-0000000000000001"];
  assert.equal(await store.remove([...d, "x"]), false);
  await store.writeText([...d, "out", "job_receipt.json"], "{}");
  await assert.rejects(store.remove(d), (e) => e.code === "not_empty");
  const fh = await store.file([...d, "out", "job_receipt.json"]);
  const h = await fh.createSyncAccessHandle();
  await assert.rejects(store.remove(d, { recursive: true }), (e) => e.code === "busy");
  h.close();
  assert.equal(await store.remove(d, { recursive: true }), true);
  assert.equal(await store.exists(d), false);
  assert.equal(await store.remove(["tcmstudio"], { recursive: true }), true, "clearing all local data removes the base itself");
  await assert.rejects(root.getDirectoryHandle("tcmstudio"), { name: "NotFoundError" });
});

test("move: in place where the browser has move(), else a copy and a delete; the destination is replaced", async () => {
  for (const [options, how] of [[{}, "move"], [{ move: false }, "copy"], [{ move: false, writables: false }, "copy"]]) {
    const { store, fs } = await opened(options);
    const part = ["tcmstudio", "datasets", "research", "raw", ".part", "f.txt"];
    const fin = ["tcmstudio", "datasets", "research", "raw", "f.txt"];
    const data = bytes(9_000_001, 5);
    await store.writeBytes(part, data);
    await store.writeText(fin, "an older file");
    assert.equal(await store.move(part, fin), how, JSON.stringify(options));
    assert.equal(await store.exists(part), false);
    assert.deepEqual(await store.readBytes(fin), data);
    assert.deepEqual(fs.openHandles(), []);
    await assert.rejects(store.move(part, fin), (e) => e.code === "not_found");
  }
});

test("a full quota is named as such, and the copy that hit it is removed", async () => {
  const { store } = await opened({ quota: 1500, move: false });
  await store.writeBytes(["tcmstudio", "a"], bytes(1000));
  await assert.rejects(store.writeBytes(["tcmstudio", "b"], bytes(600)), (e) => e.code === "quota" && /quota/.test(e.message));
  await assert.rejects(store.move(["tcmstudio", "a"], ["tcmstudio", "c"]), (e) => e.code === "quota");
  assert.deepEqual(await store.readBytes(["tcmstudio", "a"]), bytes(1000), "the original stays");
  assert.equal(await store.exists(["tcmstudio", "c"]), false);
});

test("estimate() and persist(): what the browser says, null where it says nothing", async () => {
  const { store } = await opened({ quota: 10_000, grant: false });
  await store.writeBytes(["tcmstudio", "a"], bytes(1234));
  assert.deepEqual(await store.estimate(), { usage: 1234, quota: 10_000, persisted: false });
  assert.equal(await store.persist(), false);
  const granted = await opened({ grant: true });
  assert.equal(await granted.store.persist(), true);
  assert.equal((await granted.store.estimate()).persisted, true);
  const bare = new OpfsStore({ root: granted.root, storage: null });
  assert.deepEqual(await bare.estimate(), { usage: null, quota: null, persisted: null });
  assert.equal(await bare.persist(), false);
});
