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

test("the upstream's breaker: open on a spent quota (until the upstream's reset), one probe per window, closed by a success; logged", () => {
  const logs = [];
  const saved = console.error;
  console.error = (...a) => logs.push(a.join(" "));
  try {
    const sql = sqlStore();
    const l = new LimiterCore(sql);
    const settings = { open: 600, errors: 3, window: 60 };
    assert.deepEqual(l.breaker(true, settings, T), { open: false });
    assert.deepEqual(l.breakerReport("ok", settings, null, T), { open: false }); // closed: nothing to record
    assert.deepEqual(l.breakerReport("released", settings, null, T), { open: false });
    assert.deepEqual(logs, []);
    // a spent quota with the upstream's reset time
    assert.deepEqual(l.breakerReport("upstream_quota", settings, 1800, T), { open: true, reason: "upstream_quota", until: T + 1800000, retry: 1800 });
    assert.deepEqual(logs, [`upstream breaker open upstream_quota ${new Date(T + 1800000).toISOString()}`]);
    // the answers of calls sent before it opened change nothing (and are not logged again)
    l.breakerReport("upstream_quota", settings, 60, T + 1000);
    l.breakerReport("upstream_error", settings, null, T + 1000);
    assert.equal(l.breaker(false, settings, T + 1000).until, T + 1800000);
    assert.equal(logs.length, 1);
    // an evicted object reads it back
    const again = new LimiterCore(sql);
    assert.deepEqual(again.breaker(true, settings, T + 60000), { open: true, reason: "upstream_quota", until: T + 1800000, retry: 1740 });
    // past the reset: health sees it closed (a probe due) and takes no probe; the first model call is the probe, and the
    // rest wait for it, told to ask again in a few seconds (not at the end of its window, which only bounds a probe that
    // never reports)
    const after = T + 1800000;
    assert.deepEqual(again.breaker(false, settings, after), { open: false, due: true });
    assert.deepEqual(again.breaker(true, settings, after), { open: false, probe: true });
    assert.deepEqual(again.breaker(true, settings, after + 1000), { open: true, reason: "upstream_quota", until: after + 16000, retry: 15 });
    assert.deepEqual(again.breaker(false, settings, after + 55000), { open: true, reason: "upstream_quota", until: after + 60000, retry: 5 });
    // the probe answered: closed
    assert.deepEqual(again.breakerReport("ok", settings, null, after + 2000), { open: false });
    assert.equal(logs.at(-1), "upstream breaker closed");
    assert.deepEqual(again.breaker(true, settings, after + 3000), { open: false });
    assert.deepEqual(new LimiterCore(sql).breaker(true, settings, after + 3000), { open: false });
    // without the upstream's reset time: BREAKER_OPEN_SECONDS; a probe that fails opens it again, one that learned
    // nothing lets the next call try
    again.breakerReport("upstream_quota", settings, null, after + 4000);
    const w = after + 4000 + 600000;
    assert.equal(again.breaker(false, settings, w - 1).until, w);
    assert.deepEqual(again.breaker(true, settings, w), { open: false, probe: true });
    assert.deepEqual(again.breakerReport("released", settings, null, w + 10), { open: false, due: true });
    assert.deepEqual(again.breaker(true, settings, w + 20), { open: false, probe: true });
    again.breakerReport("upstream_error", settings, null, w + 30);
    assert.deepEqual(again.breaker(true, settings, w + 40), { open: true, reason: "upstream_error", until: w + 30 + 600000, retry: 600 });
    assert.equal(logs.filter((x) => x.startsWith("upstream breaker open")).length, 3);
    // a probe whose outcome never comes holds the others a minute at most: then the next call probes
    const lease = new LimiterCore(sqlStore());
    lease.breakerReport("upstream_quota", settings, null, T);
    assert.deepEqual(lease.breaker(true, settings, T + 600000), { open: false, probe: true });
    assert.equal(lease.breaker(true, settings, T + 659999).open, true);
    assert.deepEqual(lease.breaker(true, settings, T + 660000), { open: false, probe: true });
    assert.equal(logs.filter((x) => x === "upstream breaker closed").length, 1);
  } finally {
    console.error = saved;
  }
});

test("the breaker opens on `errors` upstream errors within `window` seconds, not on errors further apart", () => {
  const l = new LimiterCore(sqlStore());
  const settings = { open: 300, errors: 3, window: 60 };
  const quietly = (fn) => {
    const saved = console.error;
    console.error = () => {};
    try { return fn(); } finally { console.error = saved; }
  };
  quietly(() => {
    assert.deepEqual(l.breakerReport("upstream_error", settings, null, T, "a"), { open: false });
    assert.deepEqual(l.breakerReport("upstream_unknown", settings, null, T + 30000, "b"), { open: false });
    assert.deepEqual(l.breakerReport("upstream_error", settings, null, T + 61000, "a"), { open: false }); // the first is past
    assert.deepEqual(l.breakerReport("upstream_unknown", settings, null, T + 62000, "c"), { open: true, reason: "upstream_error", until: T + 362000, retry: 300 });
    // what does not count, and settings that are not numbers
    const other = new LimiterCore(sqlStore());
    for (let i = 0; i < 5; i++) other.breakerReport("upstream_rate", settings, null, T + i, `v${i}`);
    assert.deepEqual(other.breaker(true, settings, T + 10), { open: false });
    for (let i = 0; i < 3; i++) other.breakerReport("upstream_error", { open: "x", errors: null }, null, T + i, `v${i}`);
    assert.equal(other.breaker(false, {}, T + 10).until, T + 2 + 600000);
  });
  // one caller's failures alone never open it, however many (the breaker is every visitor's); its row keeps `errors` of
  // them at most; another caller's failure among them within the window opens it
  quietly(() => {
    const one = new LimiterCore(sqlStore());
    for (let i = 0; i < 10; i++) assert.deepEqual(one.breakerReport("upstream_unknown", settings, null, T + i * 1000, "a"), { open: false });
    assert.deepEqual(one.breakerState().failures, [{ at: T + 7000, who: "a" }, { at: T + 8000, who: "a" }, { at: T + 9000, who: "a" }]);
    assert.deepEqual(one.breakerReport("upstream_error", settings, null, T + 70000, "b"), { open: false }); // a's are past
    assert.deepEqual(one.breakerReport("upstream_error", settings, null, T + 71000, "a"), { open: false });
    assert.equal(one.breakerReport("upstream_error", settings, null, T + 72000, "a").open, true);
    // with `errors` 1 the owner asked for any one failure to open it
    const strict = new LimiterCore(sqlStore());
    assert.equal(strict.breakerReport("upstream_error", { open: 300, errors: 1, window: 60 }, null, T, "a").open, true);
    // failures written before callers were kept (bare times) are not read: a fresh count
    const sql = sqlStore();
    sql.exec("CREATE TABLE IF NOT EXISTS breaker (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
    sql.exec("INSERT INTO breaker (k, v) VALUES ('upstream', ?)", JSON.stringify({ until: 0, reason: "", probe: false, failures: [T, T + 1] }));
    const old = new LimiterCore(sql);
    assert.deepEqual(old.breakerState().failures, []);
    assert.deepEqual(old.breakerReport("upstream_error", settings, null, T + 2, "b"), { open: false });
  });
  // a row that cannot be read is a closed breaker
  const sql = sqlStore();
  sql.exec("CREATE TABLE IF NOT EXISTS breaker (k TEXT PRIMARY KEY, v TEXT NOT NULL)");
  sql.exec("INSERT INTO breaker (k, v) VALUES ('upstream', 'not json')");
  assert.deepEqual(new LimiterCore(sql).breaker(true, settings, T), { open: false });
});

test("path-specific source rates do not slow unrelated APIs on the same host", () => {
  const l = new LimiterCore(sqlStore());
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T, { "/metagenomics/": 0.1 }), { ok: true, delay: 0 });
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T), { ok: true, delay: 100 });
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T, { "/metagenomics/": 0.1 }), { ok: true, delay: 10000 });
  assert.deepEqual(l.sourceSlot("www.ebi.ac.uk", 10, T, { "/metagenomics/": 0.1 }), { ok: false, retry: 20 });
});
