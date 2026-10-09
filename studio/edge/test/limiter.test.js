// The Durable Object's counters on node:sqlite.
import assert from "node:assert/strict";
import test from "node:test";

import { LimiterCore, day, secondsToMidnight } from "../src/limiter.js";
import { sqlStore } from "./helpers.js";

const T = Date.UTC(2026, 9, 7, 23, 59, 30);
const limits = { perMinute: 3, perDay: 5, total: 7, tokensPerDay: 0, totalTokens: 0 };

test("calls: per minute in memory, per day and in all in SQLite; the reasons and when to retry", () => {
  const l = new LimiterCore(sqlStore());
  for (let i = 0; i < 3; i++) assert.equal(l.hit("a", "chat", limits, T).ok, true);
  assert.deepEqual(l.hit("a", "chat", limits, T), { ok: false, reason: "minute", retry: 30 });
  assert.equal(l.hit("b", "chat", limits, T).ok, true); // another visitor has its own minute
  const next = T + 30000; // the next minute, and the next UTC day: everything resets
  assert.equal(day(next), "2026-10-08");
  for (let i = 0; i < 3; i++) assert.equal(l.hit("a", "chat", limits, next + i * 60000).ok, true);
  const later = next + 10 * 60000;
  assert.equal(l.hit("a", "chat", limits, later).remaining, 1);
  assert.equal(l.hit("a", "chat", limits, later).remaining, 0);
  const d = l.hit("a", "chat", limits, later);
  assert.deepEqual([d.ok, d.reason, d.retry], [false, "day", secondsToMidnight(later)]);
  assert.equal(l.hit("b", "chat", limits, later).ok, true);
  assert.equal(l.hit("c", "chat", limits, later).ok, true);
  assert.deepEqual(l.hit("e", "chat", limits, later).reason, "total"); // 7 calls in all today
  // yesterday's rows are gone once today has begun
  assert.equal(l.sql.exec("SELECT count(*) AS n FROM hits WHERE day <> ?", "2026-10-08").toArray()[0].n, 0);
  // no limits: always allowed, nothing counted for the total
  const free = new LimiterCore(sqlStore());
  for (let i = 0; i < 50; i++) assert.equal(free.hit("a", "chat", { perMinute: 0, perDay: 0, total: 0 }, T).ok, true);
  assert.equal(free.count(`t:chat:${day(T)}`), 0);
});

test("tokens: reserved with the call, settled by spend (never below zero), per visitor and in all", () => {
  const l = new LimiterCore(sqlStore());
  const tl = { perMinute: 0, perDay: 0, total: 0, tokensPerDay: 100, totalTokens: 150 };
  const g = l.hit("a", "chat", tl, T, 40.7);
  assert.deepEqual([g.ok, g.reserved], [true, 40]);
  assert.equal(l.count(`k:chat:a:${day(T)}`), 40);
  l.spend("a", "chat", 70 - 40, T); // the answer cost 70
  assert.equal(l.count(`k:chat:a:${day(T)}`), 70);
  l.spend("a", "chat", -500, T);
  assert.equal(l.count(`k:chat:a:${day(T)}`), 0); // clamped
  l.spend("a", "chat", 120, T);
  assert.deepEqual(l.hit("a", "chat", tl, T), { ok: false, reason: "tokens", retry: secondsToMidnight(T) });
  l.spend("b", "chat", 40, T); // 160 in all, over the 150 for everyone
  assert.equal(l.hit("c", "chat", tl, T).reason, "total_tokens");
  // without token limits nothing is reserved
  assert.equal(new LimiterCore(sqlStore()).hit("a", "chat", { perMinute: 1 }, T, 500).reserved, 0);
  l.spend("a", "chat", 0, T);
  l.spend("a", "chat", "x", T); // nothing to count
});

test("the table survives a second object over the same storage (an evicted object comes back)", () => {
  const sql = sqlStore();
  const first = new LimiterCore(sql);
  first.hit("a", "chat", limits, T);
  const again = new LimiterCore(sql);
  assert.equal(again.count(`d:chat:a:${day(T)}`), 1);
  assert.equal(again.hit("a", "chat", limits, T).remaining, 3);
});

test("public source slots pace every visitor by host, survive eviction and cap the waiting queue", () => {
  const sql = sqlStore();
  const first = new LimiterCore(sql);
  assert.deepEqual(first.sourceSlot("api.example.org", 2, T), { ok: true, delay: 0 });
  assert.deepEqual(first.sourceSlot("api.example.org", 2, T), { ok: true, delay: 500 });
  const again = new LimiterCore(sql);
  assert.deepEqual(again.sourceSlot("api.example.org", 2, T), { ok: true, delay: 1000 });
  for (let n = 3; n <= 20; n++) assert.deepEqual(again.sourceSlot("api.example.org", 2, T), { ok: true, delay: n * 500 });
  assert.deepEqual(again.sourceSlot("api.example.org", 2, T), { ok: false, retry: 11 });
  assert.deepEqual(again.sourceSlot("other.example.org", 1, T), { ok: true, delay: 0 });
  assert.deepEqual(again.sourceSlot("api.example.org", 2, T + 11000), { ok: true, delay: 0 });
});

test("path-specific source rates do not slow unrelated APIs on the same host", () => {
  const l = new LimiterCore(sqlStore());
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T, { "/metagenomics/": 0.1 }), { ok: true, delay: 0 });
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T), { ok: true, delay: 100 });
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T, { "/metagenomics/": 0.1 }), { ok: true, delay: 10000 });
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T, { "/metagenomics/": 0.1 }), { ok: false, retry: 20 });
});
