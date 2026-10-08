import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { IDBFactory } from "fake-indexeddb";
import { DB_VERSION, latestLeaf, openStore, siblingsOf, threadPath, toolMessagesOf } from "../js/core/store.js";

const fresh = () => openStore({ indexedDB: new IDBFactory() });

test("opens IndexedDB at the contract's version, with the contract's stores and indexes", async () => {
  const idb = new IDBFactory();
  const store = await openStore({ indexedDB: idb });
  assert.equal(store.persistent, true);
  const db = store.backend.db;
  assert.equal(db.version, DB_VERSION);
  assert.deepEqual([...db.objectStoreNames].sort(), ["conversations", "files", "messages", "projects"]);
  const tx = db.transaction(["projects", "conversations", "messages", "files"]);
  assert.deepEqual([...tx.objectStore("conversations").indexNames].sort(), ["projectId", "updatedAt"]);
  assert.deepEqual([...tx.objectStore("files").indexNames].sort(), ["projectId", "sha256"]);
  assert.deepEqual([...tx.objectStore("messages").indexNames], ["conversationId"]);
  store.close();
  const again = await openStore({ indexedDB: idb });
  assert.equal(again.persistent, true, "reopening an existing database works");
});

test("without IndexedDB the in-memory store has the same API and says it is not persistent", async () => {
  const mem = await openStore({ indexedDB: null });
  assert.equal(mem.persistent, false);
  const p = await mem.projects.create({ name: "临时" });
  assert.equal((await mem.projects.get(p.id)).name, "临时");
  const broken = await openStore({ indexedDB: { open() { throw new Error("SecurityError"); } } });
  assert.equal(broken.persistent, false);
  assert.match(String(broken.openError), /SecurityError/);
});

test("projects: create with defaults, update keeps identity, list sorts by recency and filters archived", async () => {
  const s = await fresh();
  const a = await s.projects.create({ name: "葛根芩连汤复核", description: "网络药理学复核", instructions: "回答用中文" });
  assert.match(a.id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(a.defaults, { web: false });
  assert.deepEqual(a.approvals, {});
  assert.equal(a.archived, false);
  await new Promise((r) => setTimeout(r, 2));
  const b = await s.projects.create({ name: "安全性检索", web: true });
  assert.equal(b.defaults.web, true);
  assert.deepEqual((await s.projects.list()).map((p) => p.name), ["安全性检索", "葛根芩连汤复核"]);
  await new Promise((r) => setTimeout(r, 2));
  const up = await s.projects.update(a.id, { name: "葛根芩连汤", id: "hijack", createdAt: 0 });
  assert.equal(up.id, a.id);
  assert.equal(up.createdAt, a.createdAt);
  assert.ok(up.updatedAt > a.updatedAt);
  await s.projects.update(b.id, { archived: true });
  assert.deepEqual((await s.projects.list()).map((p) => p.id), [a.id]);
  assert.deepEqual((await s.projects.list({ archived: true })).map((p) => p.id), [b.id]);
  await assert.rejects(s.projects.update("nope", {}), /no project/);
  assert.equal((await s.projects.create({})).name, "新项目");
});

test("conversations and messages: create, put, list, recent, pinned first", async () => {
  const s = await fresh();
  const p = await s.projects.create({ name: "P" });
  const c1 = await s.conversations.create({ projectId: p.id, title: "桂枝汤出处" });
  await new Promise((r) => setTimeout(r, 2));
  const c2 = await s.conversations.create({ projectId: p.id, title: "葛根" });
  await s.conversations.update(c1.id, { pinned: true });
  assert.deepEqual((await s.conversations.list(p.id)).map((c) => c.id), [c1.id, c2.id]);
  await new Promise((r) => setTimeout(r, 3));
  const m = await s.messages.put({ conversationId: c2.id, role: "user", content: "葛根的性味？" });
  assert.equal(m.parentId, null);
  assert.ok(m.createdAt);
  assert.deepEqual((await s.messages.list(c2.id)).map((x) => x.content), ["葛根的性味？"]);
  assert.equal((await s.conversations.recent(1))[0].id, c2.id, "a new message makes the conversation recent");
  await assert.rejects(s.conversations.create({ title: "x" }), /project/);
  await assert.rejects(s.messages.put({ role: "user" }), /conversationId/);
});

test("branching: editing a user message adds a sibling; leafId selects the shown branch", async () => {
  const s = await fresh();
  const p = await s.projects.create({ name: "P" });
  const c = await s.conversations.create({ projectId: p.id });
  const at = (n) => 1_000 + n;
  const u1 = await s.messages.put({ conversationId: c.id, role: "user", content: "问一", parentId: null, createdAt: at(1) });
  const a1 = await s.messages.put({ conversationId: c.id, role: "assistant", content: "答一", parentId: u1.id, createdAt: at(2) });
  await s.messages.put({ conversationId: c.id, role: "tool", content: "{}", parentId: a1.id, toolCallId: "t", createdAt: at(3) });
  const u2 = await s.messages.put({ conversationId: c.id, role: "user", content: "问二", parentId: a1.id, createdAt: at(4) });
  const a2 = await s.messages.put({ conversationId: c.id, role: "assistant", content: "答二", parentId: u2.id, createdAt: at(5) });
  const u2b = await s.messages.put({ conversationId: c.id, role: "user", content: "问二（改）", parentId: a1.id, createdAt: at(6) });
  const a2b = await s.messages.put({ conversationId: c.id, role: "assistant", content: "答二（改）", parentId: u2b.id, createdAt: at(7) });
  const all = await s.messages.list(c.id);
  assert.deepEqual(threadPath(all, a2.id).map((m) => m.content), ["问一", "答一", "问二", "答二"]);
  assert.deepEqual(threadPath(all, null).map((m) => m.content), ["问一", "答一", "问二（改）", "答二（改）"], "the newest branch by default");
  assert.deepEqual(siblingsOf(all, u2.id).map((m) => m.id), [u2.id, u2b.id]);
  assert.equal(latestLeaf(all, u1.id), a2b.id);
  assert.equal(toolMessagesOf(all, a1.id).length, 1);
  await s.conversations.update(c.id, { leafId: a2.id });
  assert.deepEqual((await s.messages.path(c.id)).map((m) => m.content), ["问一", "答一", "问二", "答二"]);
});

test("files: stored with their sha256, listed per project", async () => {
  const s = await fresh();
  const p = await s.projects.create({ name: "P" });
  const f = await s.files.add(p.id, new Blob(["gene,log2fc\nTNF,-1.2\n"], { type: "text/csv" }), { name: "de.csv" });
  assert.equal(f.sha256, createHash("sha256").update("gene,log2fc\nTNF,-1.2\n").digest("hex"));
  assert.equal(f.bytes, 21);
  assert.equal(f.source, "upload");
  const back = await s.files.get(f.id);
  assert.equal(await back.blob.text(), "gene,log2fc\nTNF,-1.2\n");
  assert.equal((await s.files.list(p.id)).length, 1);
  await s.files.update(f.id, { runnerUploadId: "u_1", sha256: "tampered" });
  assert.equal((await s.files.get(f.id)).sha256, f.sha256, "the hash cannot be patched");
  await assert.rejects(s.files.add(p.id, "not a blob"), TypeError);
});

test("removing a project removes its conversations, messages and files", async () => {
  const s = await fresh();
  const keep = await s.projects.create({ name: "keep" });
  const p = await s.projects.create({ name: "gone" });
  const c = await s.conversations.create({ projectId: p.id });
  await s.messages.put({ conversationId: c.id, role: "user", content: "x" });
  await s.files.add(p.id, new Blob(["x"]), { name: "x.txt" });
  const ck = await s.conversations.create({ projectId: keep.id });
  await s.projects.remove(p.id);
  assert.equal(await s.projects.get(p.id), null);
  assert.equal(await s.conversations.get(c.id), null);
  assert.deepEqual(await s.messages.list(c.id), []);
  assert.deepEqual(await s.files.list(p.id), []);
  assert.ok(await s.conversations.get(ck.id));
  await s.conversations.remove(ck.id);
  assert.equal(await s.conversations.get(ck.id), null);
});

test("search: projects, titles and message text, zh and en, every word must match", async () => {
  const s = await fresh();
  const p = await s.projects.create({ name: "Safety review", instructions: "关注十八反" });
  const c = await s.conversations.create({ projectId: p.id, title: "甘草 与 甘遂" });
  await s.messages.put({ conversationId: c.id, role: "user", content: "甘草和甘遂可以同用吗？ licorice" });
  await s.messages.put({ conversationId: c.id, role: "tool", content: "甘草 tool output" });
  const hits = await s.search("甘草");
  assert.deepEqual(hits.map((h) => h.type), ["conversation", "message"]);
  assert.equal(hits[1].conversationId, c.id);
  assert.equal(hits[1].projectId, p.id);
  assert.match(hits[1].snippet, /甘草/);
  assert.deepEqual((await s.search("SAFETY")).map((h) => h.type), ["project"]);
  assert.deepEqual((await s.search("十八反")).map((h) => h.type), ["project"]);
  assert.equal((await s.search("甘草 licorice")).length, 1);
  assert.equal((await s.search("甘草 nothing")).length, 0);
  assert.deepEqual(await s.search("  "), []);
});

test("export → import: a full copy with new ids, links rewritten, file bytes checked", async () => {
  const s = await fresh();
  const p = await s.projects.create({ name: "导出", instructions: "x", web: true });
  await s.projects.update(p.id, { approvals: { runner: "project" } });
  const f = await s.files.add(p.id, new Blob([new Uint8Array([0, 1, 2, 250, 255])], { type: "application/octet-stream" }), { name: "bin.dat" });
  const c = await s.conversations.create({ projectId: p.id, title: "对话" });
  const u = await s.messages.put({ conversationId: c.id, role: "user", content: "问", attachments: [f.id] });
  const a = await s.messages.put({ conversationId: c.id, role: "assistant", content: "答", parentId: u.id, wire: [{ role: "assistant", content: "答" }] });
  await s.conversations.update(c.id, { leafId: a.id });
  const blob = await s.exportProject(p.id);
  const doc = JSON.parse(await blob.text());
  assert.equal(doc.format, "tcmstudio.project/1");
  assert.equal(doc.files[0].sha256, f.sha256);

  const other = await fresh();
  const newId = await other.importProject(blob);
  assert.notEqual(newId, p.id);
  const imported = await other.projects.get(newId);
  assert.deepEqual(imported.approvals, {}, "approvals are not imported");
  assert.equal(imported.defaults.web, false, "web access is not imported");
  assert.equal(imported.instructions, "x");
  const [conv] = await other.conversations.list(newId);
  assert.notEqual(conv.id, c.id);
  const msgs = await other.messages.list(conv.id);
  const path = threadPath(msgs, conv.leafId);
  assert.deepEqual(path.map((m) => m.content), ["问", "答"]);
  const [file] = await other.files.list(newId);
  assert.deepEqual(msgs.find((m) => m.role === "user").attachments, [file.id]);
  assert.deepEqual([...new Uint8Array(await file.blob.arrayBuffer())], [0, 1, 2, 250, 255]);
  assert.equal(file.sha256, f.sha256);
  const twice = await other.importProject(blob);
  assert.notEqual(twice, newId, "importing twice gives two copies");

  const tampered = structuredClone(doc);
  tampered.files[0].data = Buffer.from("other").toString("base64");
  const before = (await other.projects.list()).length;
  await assert.rejects(other.importProject(JSON.stringify(tampered)), /sha256/);
  assert.equal((await other.projects.list()).length, before, "nothing is imported when a file does not match");
  await assert.rejects(other.importProject("{}"), /not a TCMScience Studio project/);
  await assert.rejects(other.importProject("not json"), /invalid JSON/);
});

test("concurrent writers do not overwrite each other's fields", async () => {
  for (const s of [await fresh(), await openStore({ memory: true })]) {
    const p = await s.projects.create({ name: "并发" });
    const c = await s.conversations.create({ projectId: p.id, title: "t" });
    await Promise.all([
      s.conversations.update(c.id, { leafId: "leaf-1" }),
      s.messages.put({ conversationId: c.id, role: "user", content: "x" }),
      s.conversations.update(c.id, { title: "新标题" }),
    ]);
    const conv = await s.conversations.get(c.id);
    assert.equal(conv.leafId, "leaf-1");
    assert.equal(conv.title, "新标题");
    await Promise.all([s.projects.update(p.id, { approvals: { runner: "project" } }), s.projects.update(p.id, { name: "改名" })]);
    const proj = await s.projects.get(p.id);
    assert.equal(proj.approvals.runner, "project");
    assert.equal(proj.name, "改名");
  }
});

test("clearAll deletes everything", async () => {
  const s = await fresh();
  await s.projects.create({ name: "x" });
  await s.clearAll();
  assert.deepEqual(await s.projects.list({ archived: null }), []);
});
