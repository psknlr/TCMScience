// The three telemetry stores on node:sqlite (the Durable Object SQL API's shape): what each counts and keeps, that a
// retried batch or a turn reported twice is counted once, that page views, visits, visitors, turns, model calls and tool
// runs stay distinct, the day's salt, retention, deletion, and what security events keep of an address.
import assert from "node:assert/strict";
import test from "node:test";

import { CONSENT_VERSION, ownerOf } from "../../web/js/core/telemetry-contract.js";
import { shiftDay } from "../src/telemetry/config.js";
import { ConversationCore } from "../src/telemetry/conversations.js";
import { LOCK, SecurityCore, isHoneypot, netPrefix, uaClass } from "../src/telemetry/security.js";
import { UsageCore, percentile, spread } from "../src/telemetry/usage.js";
import { sqlStore } from "./helpers.js";

const DAY = "2026-10-11";
const NOW = Date.parse("2026-10-11T04:00:00Z");
let n = 0;
const batch = (events, id = `b${++n}`) => ({ v: 1, batch: id, events });
const rows = (core, table) => core.sql.exec(`SELECT * FROM ${table}`).toArray();

test("usage: page views, visits, visitors, turns, model calls and tool runs are counted apart", async () => {
  const u = new UsageCore(sqlStore());
  const ctx = { day: DAY, country: "CN", visitor: "visitor-a", now: NOW };
  const r = await u.ingest(batch([
    { type: "app_init", layout: "wide", lang: "zh", browser: "chromium", mobile: false },
    { type: "visit", route: "home" },
    { type: "pageview", route: "home" },
    { type: "pageview", route: "conversation" },
    { type: "pageview", route: "settings", tab: "privacy" },
    { type: "turn_start", provider: "tao", model: "Tao-S1", first: true },
    { type: "turn_end", turn: "t1", provider: "tao", model: "Tao-S1", status: "completed", steps: 3, calls: 4, tool_rounds: 2, tools: 3,
      tool_failures: 1, ms: 9000, first_ms: 800, tokens_in: 1200, tokens_out: 300, citations: 4, citations_verified: 3, citations_used: 2, first: true },
    { type: "tool_end", call: "t1:c1", tool: "tcm_herb", kind: "native", place: "browser", status: "succeeded", ms: 120 },
    { type: "tool_end", call: "t1:c2", tool: "skill.assess-tcm-safety", kind: "skill", place: "browser", status: "failed", ms: 2400, error_type: "runtime_error" },
    { type: "tool_end", call: "t1:c3", tool: "tcm_herb", kind: "native", place: "runner", status: "succeeded", ms: 80 },
    { type: "workflow", key: "t1:c2", kind: "skill", name: "skill.assess-tcm-safety", phase: "started" },
    { type: "workflow", key: "t1:c2", kind: "skill", name: "skill.assess-tcm-safety", phase: "failed" },
    { type: "error", category: "js_error", name: "TypeError", where: "js/ui/app.js:12" },
    { type: "availability", service: "relay", ok: true },
    { type: "consent", decision: "granted", version: CONSENT_VERSION },
    { type: "pageview", route: "/p/secret-project-id" }, // not a route name: dropped, counted as rejected
  ]), ctx);
  assert.deepEqual(r, { ok: true, accepted: 15, dropped: 1 });
  const s = u.stats({ from: DAY, to: DAY });
  assert.deepEqual(s.traffic.totals, { pageviews: 3, visits: 1, visitor_days: 1, asker_days: 1, app_inits: 1 });
  assert.deepEqual(s.traffic.routes, { home: 1, conversation: 1, settings: 1 });
  assert.equal(s.conversations.totals.turns_started, 1);
  assert.equal(s.conversations.totals.conversations_started, 1);
  assert.equal(s.conversations.totals.completed, 1);
  assert.equal(s.conversations.success_rate, 1);
  assert.deepEqual(s.models.totals, { calls: 4, tokens_in: 1200, tokens_out: 300 });
  assert.deepEqual(s.models.list.map((m) => [m.provider, m.model, m.turns, m.calls]), [["tao", "Tao-S1", 1, 4]]);
  assert.deepEqual(s.tools.totals, { runs: 3, failed: 1 });
  const herb = s.tools.list.find((t) => t.tool === "tcm_herb");
  assert.deepEqual([herb.runs, herb.failed, herb.failure_rate], [2, 0, 0]);
  assert.deepEqual(s.workflows, [{ workflow: "skill:skill.assess-tcm-safety", started: 1, submitted: 0, completed: 0, failed: 1, refused: 0, cancelled: 0 }]);
  assert.deepEqual(s.errors.list, [{ category: "js_error", name: "TypeError@js/ui/app.js:12", n: 1 }]);
  assert.deepEqual(s.availability, [{ service: "relay", checks: 1, up: 1, down: 0 }]);
  assert.deepEqual(s.consent, { granted: 1 });
  assert.deepEqual(s.rejected, [{ reason: "invalid_event", n: 1 }]);
  assert.deepEqual(s.evidence, { citations: 4, verified: 3, used: 2, claims: 0, claims_refused: 0 });
  assert.equal(s.latency.total.p50, 9000);
  assert.equal(s.latency.first_token.p50, 800);
  assert.deepEqual(s.geography.find((g) => g.country === "CN"), { country: "CN", pageviews: 3, visits: 1, visitors: 1, turns: 1 });
  // nothing stored names a project, a conversation or what anyone wrote
  const all = JSON.stringify([rows(u, "usage_counts"), rows(u, "usage_turns"), rows(u, "usage_tools")]);
  assert.doesNotMatch(all, /secret-project-id|visitor-a/);
});

test("usage: a retried batch and a turn reported twice are counted once; a visitor once a day", async () => {
  const u = new UsageCore(sqlStore());
  const ctx = { day: DAY, country: "CN", visitor: "v1", now: NOW };
  const b = batch([{ type: "pageview", route: "home" }, { type: "turn_end", turn: "t9", status: "completed", provider: "tao", calls: 2 }], "same");
  await u.ingest(b, ctx);
  assert.deepEqual(await u.ingest(b, ctx), { ok: true, duplicate: true, accepted: 0, dropped: 0 });
  // the same turn, later, as "interrupted" from the next page load: the first report stands
  await u.ingest(batch([{ type: "turn_end", turn: "t9", status: "interrupted", provider: "tao", calls: 2 }]), ctx);
  await u.ingest(batch([{ type: "pageview", route: "about" }]), ctx); // same visitor, another batch
  await u.ingest(batch([{ type: "pageview", route: "home" }]), { ...ctx, visitor: "v2", country: "SG" });
  const s = u.stats({ from: DAY, to: DAY });
  assert.equal(s.traffic.totals.pageviews, 3);
  assert.equal(s.traffic.totals.visitor_days, 2);
  assert.deepEqual([s.conversations.totals.completed, s.conversations.totals.interrupted], [1, 0]);
  assert.equal(s.models.totals.calls, 2);
  // the visitor's own digest is salted with the day's random salt, which is the only thing that makes it countable
  assert.equal(rows(u, "usage_salts").length, 1);
  assert.ok(rows(u, "usage_seen").every((r) => /^[0-9a-f]{32}$/.test(r.who) && r.who !== "v1" && r.who !== "v2"));
  // the next day's first visitor makes a new salt, and yesterday's salt and seen-set are gone at once
  const next = shiftDay(DAY, 1);
  await u.ingest(batch([{ type: "pageview", route: "home" }]), { ...ctx, day: next });
  assert.deepEqual(rows(u, "usage_salts").map((r) => r.day), [next]);
  assert.ok(rows(u, "usage_seen").every((r) => r.day === next));
  assert.equal(u.stats({ from: next, to: next }).traffic.totals.visitor_days, 1); // the same person, counted anew
});

test("usage: filters, periods, the previous period, and the Worker's own counts", async () => {
  const u = new UsageCore(sqlStore());
  for (const [day, provider, model, status, ms] of [[DAY, "tao", "Tao-S1", "completed", 1000], [DAY, "openai", "gpt-5", "failed", 500],
    [shiftDay(DAY, -3), "tao", "Tao-S1", "completed", 3000], [shiftDay(DAY, -10), "tao", "Tao-S1", "cancelled", 200]]) {
    await u.ingest(batch([{ type: "turn_end", turn: `t-${day}-${provider}`, provider, model, status, ms, calls: 1, error_kind: status === "failed" ? "model" : undefined, error_type: status === "failed" ? "rate_limited" : undefined }]), { day, country: "CN", visitor: "x", now: NOW });
  }
  u.observe([{ metric: "relay_call", a: "ok", n: 2 }, { metric: "relay_call", a: "forbidden_origin" }, { metric: "relay_tokens", a: "in", n: 500 },
    { metric: "connector", a: "uniprot", b: "2xx", n: 3 }, { metric: "connector", a: "uniprot", b: "5xx" },
    { metric: "connector", a: "uniprot", b: "cancelled" }, { metric: "connector", a: "uniprot", b: "refused:rate_limited", n: 4 },
    { metric: "connector_ms", a: "uniprot", n: 1000 },
    { metric: "not_a_metric", a: "x", n: 5 }, { metric: "relay_call", a: "ok", n: -3 }], { day: DAY });
  const week = u.stats({ from: shiftDay(DAY, -6), to: DAY });
  assert.equal(week.days.length, 7);
  assert.deepEqual([week.conversations.totals.completed, week.conversations.totals.failed, week.conversations.totals.cancelled], [2, 1, 0]);
  assert.deepEqual(week.conversations.errors, [{ kind: "model", type: "rate_limited", n: 1 }]);
  assert.equal(Math.round(week.conversations.success_rate * 100), 67);
  const tao = u.stats({ from: shiftDay(DAY, -6), to: DAY, provider: "tao" });
  assert.deepEqual([tao.conversations.totals.finished, tao.latency.total.p50, tao.latency.total.max], [2, 1000, 3000]);
  // the change is measured against the week before with the same filters (a cancelled Tao-S1 turn ten days ago)
  assert.deepEqual([week.conversations.previous.finished, week.models.previous.calls, tao.conversations.previous.finished], [1, 1, 1]);
  const openai = u.stats({ from: shiftDay(DAY, -6), to: DAY, provider: "openai" });
  assert.deepEqual([openai.conversations.totals.finished, openai.conversations.previous.finished, openai.models.totals.calls], [1, 0, 1]);
  const month = u.stats({ from: shiftDay(DAY, -29), to: DAY });
  assert.equal(month.conversations.totals.cancelled, 1);
  assert.deepEqual(week.relay.outcomes, [{ outcome: "ok", n: 2 }, { outcome: "forbidden_origin", n: 1 }]);
  assert.deepEqual(week.relay.tokens, { in: 500 });
  // availability: a cancelled query says nothing of the database, nor do the gateway's own refusals (its limits); a 5xx
  // counts against it. The time is of the four requests sent.
  assert.deepEqual(week.connectors, [{ source: "uniprot", requests: 9, ok: 3, client_errors: 0, server_errors: 1, refused: 4, failed: 0, cancelled: 1, availability: 0.75, avg_ms: 250 }]);
  assert.deepEqual(week.filters.providers, ["openai", "tao"]);
  assert.equal(week.previous ?? null, null); // previous lives under each section
  assert.equal(month.traffic.previous.pageviews, 0);
  // what a filter cannot narrow is null, never the unfiltered number: questions sent carry no country, a
  // conversation's start neither a country nor a model
  await u.ingest(batch([{ type: "turn_start", provider: "tao", model: "Tao-S1", first: true }, { type: "turn_start", provider: "openai", model: "gpt-5", first: true }]), { day: DAY, country: "US", visitor: "y", now: NOW });
  const all = u.stats({ from: shiftDay(DAY, -6), to: DAY });
  assert.deepEqual([all.conversations.totals.turns_started, all.conversations.totals.conversations_started, all.conversations.unfiltered], [2, 2, []]);
  const cn = u.stats({ from: shiftDay(DAY, -6), to: DAY, country: "CN" });
  assert.deepEqual([cn.conversations.totals.turns_started, cn.conversations.totals.conversations_started, cn.conversations.daily.started], [null, null, null]);
  assert.deepEqual(cn.conversations.unfiltered.sort(), ["conversations_started", "turns_started"]);
  assert.equal(cn.conversations.totals.finished, 3); // the turn rows are filtered (all three of the week are from CN)
  const model = u.stats({ from: shiftDay(DAY, -6), to: DAY, model: "Tao-S1" });
  assert.deepEqual([model.conversations.totals.turns_started, model.conversations.totals.conversations_started], [1, null]);
});

test("usage: a summary reads each table a few times, not once per number (rows read are the account's, daily)", async () => {
  const store = sqlStore();
  const reads = [];
  const sql = { exec: (q, ...a) => { if (/^\s*select/i.test(q)) reads.push(/FROM (usage_\w+)/.exec(q)?.[1]); return store.exec(q, ...a); } };
  const u = new UsageCore(sql);
  await u.ingest(batch([{ type: "pageview", route: "home" }, { type: "turn_end", turn: "t1", provider: "tao", status: "completed", ms: 10 },
    { type: "tool_end", call: "t1:c1", tool: "tcm_herb", kind: "native", place: "browser", status: "succeeded", ms: 5 }]), { day: DAY, visitor: "v", now: NOW });
  reads.length = 0;
  u.stats({ from: shiftDay(DAY, -89), to: DAY });
  const per = reads.filter(Boolean).reduce((m, t) => ((m[t] = (m[t] || 0) + 1), m), {}); // (and the day's budget row)
  assert.deepEqual(per, { usage_counts: 2, usage_turns: 3, usage_tools: 3 }); // the period and the one before; latency; per tool
});

test("usage: a summary's lists stay bounded whatever names the pages sent", async () => {
  const u = new UsageCore(sqlStore());
  const events = (i) => Array.from({ length: 50 }, (_, j) => ({ type: "workflow", key: `k${i}-${j}`, kind: "skill", name: `skill.made-up-${i}-${j}`, phase: "started" }));
  for (let i = 0; i < 6; i++) await u.ingest(batch(events(i)), { day: DAY, visitor: `v${i}`, now: NOW });
  u.observe(Array.from({ length: 300 }, (_, i) => ({ metric: "connector", a: `h${i}.example.org`, b: "2xx" })), { day: DAY });
  const s = u.stats({ from: DAY, to: DAY });
  assert.equal(s.workflows.length, 100);
  assert.equal(s.connectors.length, 200);
});

test("usage: limits per visitor, then exports and retention", async () => {
  const u = new UsageCore(sqlStore());
  const limits = { perMinute: 2, perDay: 3 };
  const go = (t) => u.ingest(batch([{ type: "pageview", route: "home" }]), { day: DAY, country: "CN", visitor: "v", now: t, caller: "c1", limits });
  assert.equal((await go(NOW)).ok, true);
  assert.equal((await go(NOW + 1)).ok, true);
  const third = await go(NOW + 2);
  assert.deepEqual([third.ok, third.reason], [false, "rate_limited"]);
  assert.ok(third.retry > 0 && third.retry <= 60);
  assert.equal((await go(NOW + 61000)).ok, true);
  assert.equal((await go(NOW + 122000)).reason, "rate_limited"); // the day's three are used
  assert.deepEqual(u.exportRows("daily", DAY, DAY).rows.map((r) => [r.metric, r.a, r.n]).filter((r) => r[0] === "pageview"), [["pageview", "home", 3]]);
  assert.equal(u.exportRows("nope", DAY, DAY), null);
  await u.ingest(batch([{ type: "turn_end", turn: "old", status: "completed", provider: "tao" }]), { day: shiftDay(DAY, -100), country: "CN", visitor: "v", now: NOW });
  await u.ingest(batch([{ type: "turn_end", turn: "new", status: "completed", provider: "tao" }]), { day: DAY, country: "CN", visitor: "v", now: NOW });
  const { removed } = u.prune(DAY, { events: 90, aggregates: 400 });
  assert.ok(removed > 0);
  assert.deepEqual(rows(u, "usage_turns").map((r) => r.id), ["new"]);
  // the 100-day-old counter is kept (aggregates: 400 days); a 500-day-old one is not
  u.observe([{ metric: "relay_call", a: "ok", n: 1 }], { day: shiftDay(DAY, -500) });
  u.prune(DAY, { events: 90, aggregates: 400 });
  assert.equal(rows(u, "usage_counts").filter((r) => r.day === shiftDay(DAY, -500)).length, 0);
  assert.ok(rows(u, "usage_counts").some((r) => r.day === shiftDay(DAY, -100)));
});

test("percentiles are nearest-rank and ignore what is missing", () => {
  assert.equal(percentile([], 50), null);
  assert.equal(percentile([5], 99), 5);
  assert.equal(percentile([1, 2, 3, 4], 50), 2);
  assert.equal(percentile([1, 2, 3, 4], 90), 4);
  assert.deepEqual(spread([NaN, 30, 10, 20]), { n: 3, avg: 20, p50: 20, p90: 30, p95: 30, p99: 30, max: 30 });
});

test("conversations: a record once, a deletion by owner with a tombstone, retention and a cap", async () => {
  const c = new ConversationCore(sqlStore());
  const ownerA = await ownerOf("A".repeat(43));
  const ownerB = await ownerOf("B".repeat(43));
  const rec = (id, owner, extra = {}) => ({ v: 1, id, owner, consent: CONSENT_VERSION, status: "completed", provider: "tao", model: "Tao-S1", question: `q ${id}`, answer: "a", ...extra });
  assert.deepEqual(c.accept(rec("m1", ownerA), { received: "2026-10-11T04:00:00.000Z", day: DAY }), { ok: true, stored: true });
  assert.deepEqual(c.accept(rec("m1", ownerA), { received: "2026-10-11T04:00:01.000Z", day: DAY }), { ok: true, stored: false, reason: "duplicate" });
  c.accept(rec("m2", ownerA), { received: "x", day: DAY });
  c.accept(rec("m3", ownerB, { status: "failed" }), { received: "x", day: DAY });
  assert.deepEqual(c.accept(null, { day: DAY, excluded: "identifier" }), { ok: true, stored: false, reason: "excluded" });
  const listed = c.list({ from: DAY, to: DAY });
  assert.deepEqual(listed.items.map((i) => i.record.id), ["m3", "m2", "m1"]);
  assert.deepEqual(c.list({ from: DAY, to: DAY, status: "failed" }).items.map((i) => i.record.id), ["m3"]);
  // a visitor's record reference (the digest's first 12 characters, as their settings show it) finds their records on
  // any day; anything shorter, or not hex, is not an owner filter
  c.accept(rec("m0", ownerA), { received: "x", day: shiftDay(DAY, -40) });
  assert.deepEqual(c.list({ from: DAY, to: DAY, owner: ownerA.slice(0, 12) }).items.map((i) => i.record.id), ["m0", "m2", "m1"]);
  assert.deepEqual(c.list({ from: DAY, to: DAY, owner: ownerA }).items.length, 3);
  assert.deepEqual(c.list({ from: DAY, to: DAY, owner: ownerA.slice(0, 11) }).items.map((i) => i.record.id), ["m3", "m2", "m1"]);
  assert.deepEqual(c.list({ from: DAY, to: DAY, owner: "%" }).items.length, 3);
  assert.equal(c.deleteIds(["m0"], { day: DAY }).deleted, 1);
  const page = c.list({ from: DAY, to: DAY, limit: 2 });
  assert.deepEqual([page.items.length, c.list({ from: DAY, to: DAY, before: page.next }).items.map((i) => i.record.id)], [2, ["m1"]]);
  assert.deepEqual(c.forget(ownerA, { day: DAY, now: NOW }), { ok: true, deleted: 2 });
  assert.deepEqual(c.list({ from: DAY, to: DAY }).items.map((i) => i.record.id), ["m3"]);
  const tomb = rows(c, "conv_deletions");
  assert.deepEqual(tomb.map((t) => [t.owner, t.n, t.by]), [[ownerA, 1, "admin"], [ownerA, 2, "visitor"]]);
  assert.doesNotMatch(JSON.stringify(tomb), /q m1/); // a tombstone holds no content
  const s = c.stats(DAY, DAY);
  assert.deepEqual([s.totals.stored, s.totals.duplicate, s.totals["excluded:identifier"], s.totals.deleted, s.held, s.deletion_requests], [3, 1, 1, 3, 1, 2]);
  assert.deepEqual(c.deleteIds(["m3", "nope"], { day: DAY }), { deleted: 1 });
  // retention: older than the period goes; a cap keeps the newest
  c.accept(rec("old", ownerB), { received: "x", day: shiftDay(DAY, -91) });
  c.accept(rec("kept", ownerB), { received: "x", day: shiftDay(DAY, -89) });
  assert.equal(c.prune(DAY, { days: 90 }).removed, 1);
  assert.deepEqual(c.list({ from: shiftDay(DAY, -100), to: DAY }).items.map((i) => i.record.id), ["kept"]);
  const capped = new ConversationCore(sqlStore());
  for (let i = 1; i <= 200; i++) capped.accept(rec(`r${i}`, ownerA), { received: "x", day: DAY, keep: 150 });
  assert.equal(rows(capped, "conv_records").length, 150);
  assert.equal(rows(capped, "conv_records")[0].id, "r51");
});

test("conversations: per-visitor limits on records and on deletion requests", async () => {
  const c = new ConversationCore(sqlStore());
  const owner = await ownerOf("C".repeat(43));
  const rec = { v: 1, id: "x", owner, consent: CONSENT_VERSION, status: "completed", question: "q" };
  const limits = { perMinute: 1, perDay: 10 };
  assert.equal(c.accept({ ...rec, id: "x1" }, { caller: "k", limits, day: DAY, now: NOW }).stored, true);
  assert.deepEqual(c.accept({ ...rec, id: "x2" }, { caller: "k", limits, day: DAY, now: NOW }).reason, "rate_limited");
  for (let i = 0; i < 10; i++) assert.equal(c.forget(owner, { caller: "k", day: DAY, now: NOW }).ok, true);
  assert.equal(c.forget(owner, { caller: "k", day: DAY, now: NOW }).reason, "rate_limited");
});

test("security: addresses as a network prefix and a monthly-keyed hash; the full address only when flagged", async () => {
  const s = new SecurityCore(sqlStore());
  const facts = { ip: "203.0.113.77", country: "US", asn: 64500, method: "GET", path: "/wp-login.php", uaClass: "tool" };
  await s.record({ kind: "scanner", route: "page", status: 404, detail: "/wp-login.php", facts, day: DAY }, { now: NOW });
  await s.record({ kind: "scanner", route: "page", status: 404, detail: "/wp-login.php", facts, day: DAY }, { now: NOW + 1000 }); // throttled, counted
  await s.record({ kind: "scanner", route: "page", status: 404, detail: "/.env", facts, day: DAY }, { rawIp: "flagged", now: NOW + 20000 });
  await s.record({ kind: "forbidden_origin", route: "relay", status: 403, facts, day: DAY }, { rawIp: "flagged", now: NOW + 30000 });
  await s.record({ kind: "not_a_kind", facts, day: DAY }, { now: NOW });
  const ev = s.exportRows(DAY, DAY).rows;
  assert.equal(ev.length, 3);
  assert.ok(ev.every((e) => e.net === "203.0.113.0/24" && /^[0-9a-f]{16}$/.test(e.who) && e.who === ev[0].who));
  assert.deepEqual(ev.map((e) => e.ip), [null, "203.0.113.77", null]); // only the flagged high-severity event
  const sum = s.summary(DAY, DAY);
  assert.deepEqual(sum.by_kind, { scanner: 3, forbidden_origin: 1 });
  assert.deepEqual([sum.sources[0].n, sum.sources[0].net, sum.sources[0].with_ip], [3, "203.0.113.0/24", true]);
  // next month the same address is a different `who`: sources cannot be followed across months
  const later = await s.who("203.0.113.77", Date.parse("2026-11-02T00:00:00Z"));
  assert.notEqual(later, ev[0].who);
  assert.equal(await s.who("203.0.113.77", NOW), ev[0].who);
  // IPv6 by its /64 (privacy addresses rotate the rest), shown as its /48
  assert.equal(await s.who("2001:db8:1:2:aaaa::1", NOW), await s.who("2001:db8:1:2:bbbb::9", NOW));
  assert.equal(netPrefix("2001:db8:1:2:aaaa::1"), "2001:db8:1::/48");
  // retention removes events and the keys of months that have none left (November's, made above, is still in use)
  s.prune(shiftDay(DAY, 60), 30);
  assert.equal(s.count(), 0);
  assert.deepEqual(rows(s, "sec_keys").map((r) => r.month), ["2026-11"]);
});

test("security: a flush's batch adds its counts once and stores its events at the time each happened", async () => {
  const s = new SecurityCore(sqlStore());
  const facts = { ip: "203.0.113.9", country: "US", method: "GET", path: "/wp-1.php", uaClass: "tool" };
  const flushed = NOW + 30000;
  const res = await s.recordBatch({
    counts: [{ day: DAY, kind: "scanner", n: 4 }, { day: DAY, kind: "not_a_kind", n: 9 }, { day: "bad", kind: "scanner", n: 1 }, { day: DAY, kind: "rate_limited", n: 0 }],
    events: [
      { kind: "scanner", route: "page", status: 404, detail: "/wp-1.php", facts, day: DAY, at: NOW },
      { kind: "scanner", route: "page", status: 404, detail: "/wp-2.php", facts, day: DAY, at: NOW + 5000 }, // same source within ten seconds
      { kind: "scanner", route: "page", status: 404, detail: "/wp-3.php", facts, day: DAY, at: NOW + 12000 },
      { kind: "scanner", route: "page", status: 404, detail: "/wp-4.php", facts: { ...facts, ip: "198.51.100.1" }, day: DAY, at: flushed + 60000 }, // not plausible
    ],
  }, { now: flushed });
  assert.equal(res.stored, 3);
  const ev = s.exportRows(DAY, DAY).rows;
  assert.deepEqual(ev.map((e) => [e.detail, e.ts]), [
    ["/wp-1.php", new Date(NOW).toISOString()], ["/wp-3.php", new Date(NOW + 12000).toISOString()], ["/wp-4.php", new Date(flushed).toISOString()],
  ]);
  assert.deepEqual(s.summary(DAY, DAY).by_kind, { scanner: 4 }); // the counts as summed by the Worker, the events not again
});

test("security: failed admin attempts lock a source for a while, a success clears it", async () => {
  const s = new SecurityCore(sqlStore());
  for (let i = 1; i < LOCK.failures; i++) assert.equal((await s.authFailed("198.51.100.5", NOW + i)).locked, false);
  assert.equal((await s.authFailed("198.51.100.5", NOW + 99)).locked, true);
  assert.equal((await s.authState("198.51.100.5", NOW + 1000)).locked, true);
  assert.equal((await s.authState("198.51.100.6", NOW + 1000)).locked, false); // another source
  assert.equal((await s.authState("198.51.100.5", NOW + LOCK.lockMs + 1000)).locked, false);
  await s.authFailed("198.51.100.7", NOW);
  await s.authSucceeded("198.51.100.7", NOW);
  assert.equal(rows(s, "sec_auth").filter((r) => r.n > 0).length, 1);
});

test("budget: each object stops taking what visitors send once its day of rows is written; deletions and the audit trail never wait", async () => {
  const at = Date.parse("2026-10-11T04:00:00Z");
  const u = new UsageCore(sqlStore());
  const batch = (i, events) => ({ v: 1, batch: `b${i}`, events });
  const tools = (i) => Array.from({ length: 10 }, (_, j) => ({ type: "tool_end", call: `c${i}-${j}`, tool: "tcm_herb", kind: "native", place: "browser", status: "succeeded", ms: 5 }));
  let refused = null;
  for (let i = 0; i < 40 && !refused; i++) {
    const res = await u.ingest(batch(i, tools(i)), { day: DAY, visitor: `v${i}`, caller: `k${i % 20}`, now: at, budget: 600 });
    if (!res.ok) refused = { i, res };
  }
  assert.ok(refused, "refused once the day's rows were written");
  assert.deepEqual([refused.res.reason, refused.res.scope], ["budget", "budget"]);
  assert.equal(refused.res.retry, 20 * 3600); // seconds to 00:00 UTC
  const state = u.budgetState(600, at);
  assert.ok(state.rows >= 600 && state.rows < 700, String(state.rows)); // the batch that crossed the line is the last
  // the Worker's own counts too
  assert.deepEqual(u.observe([{ metric: "relay_call", a: "ok" }], { day: DAY, budget: 600, now: at }).reason, "budget");
  assert.equal(u.observe([{ metric: "relay_call", a: "ok" }], { day: DAY, budget: 0, now: at }).ok, true); // no budget: taken

  // one visitor's share of the day (a tenth) is theirs only
  const v = new UsageCore(sqlStore());
  let mine = null;
  for (let i = 0; i < 40 && !mine; i++) {
    const res = await v.ingest(batch(i, tools(i)), { day: DAY, visitor: "same", caller: "one", now: at, budget: 5000 });
    if (!res.ok) mine = res;
  }
  assert.deepEqual([mine.reason, mine.scope], ["budget", "visitor"]);
  assert.equal((await v.ingest(batch(99, tools(99)), { day: DAY, visitor: "other", caller: "two", now: at, budget: 5000 })).ok, true);

  // records: refused beyond the day's rows; a deletion request still works
  const c = new ConversationCore(sqlStore());
  const owner = await ownerOf("d".repeat(43));
  const rec = (id) => ({ v: 1, id, owner, consent: CONSENT_VERSION, status: "completed", question: "q" });
  let full = null;
  for (let i = 0; i < 20 && !full; i++) {
    const res = c.accept(rec(`r${i}`), { caller: `k${i}`, day: DAY, now: at, budget: 40 });
    if (!res.ok) full = res;
  }
  assert.equal(full.reason, "budget");
  assert.ok(c.list({ from: DAY, to: DAY }).items.length > 0);
  assert.deepEqual(c.forget(owner, { caller: "k1", day: DAY, now: at }), { ok: true, deleted: c.stats(DAY, DAY).totals.stored });
  assert.equal(c.list({ from: DAY, to: DAY }).items.length, 0);

  // security: every event counted; stored within the day's rows, the admin's own actions always
  const s = new SecurityCore(sqlStore());
  const ev = (i, kind = "scanner") => ({ kind, route: "page", status: 404, detail: `/x${i}.php`, facts: { ip: `203.0.113.${i}` }, day: DAY, at: at + i * 20000 });
  await s.recordBatch({ counts: [{ day: DAY, kind: "scanner", n: 30 }], events: Array.from({ length: 30 }, (_, i) => ev(i)) }, { now: at + 30 * 20000, budget: 20 });
  const stored = s.exportRows(DAY, DAY).rows.length;
  assert.ok(stored > 0 && stored < 30, String(stored));
  assert.equal(s.summary(DAY, DAY).by_kind.scanner, 30);
  assert.equal((await s.record({ kind: "admin_login", route: "admin", facts: { ip: "198.51.100.1" }, day: DAY }, { now: at + 700000, budget: 20 })).stored, true);
  assert.equal((await s.authFailed("198.51.100.2", at + 700000)).locked, false); // the sign-in state is kept whatever the budget
});

test("scanner paths, user agents", () => {
  for (const p of ["/wp-login.php", "/.env", "/.git/config", "/phpmyadmin/index.php", "/backup.sql", "/x.aspx"]) assert.ok(isHoneypot(p), p);
  for (const p of ["/", "/js/main.js", "/runtime/tcms-py.abc.tar.gz", "/runtime/formulas-source.0ddb7ad8a00c.xlsx", "/assets/icon-192.png", "/sw.js", "/admin/", "/manifest.webmanifest"]) {
    assert.ok(!isHoneypot(p), p);
  }
  assert.equal(uaClass(""), "empty");
  assert.equal(uaClass("curl/8.4.0"), "tool");
  assert.equal(uaClass("Mozilla/5.0 (compatible; Googlebot/2.1)"), "bot");
  assert.equal(uaClass("sqlmap/1.7"), "scanner");
  assert.equal(uaClass("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"), "browser");
});
