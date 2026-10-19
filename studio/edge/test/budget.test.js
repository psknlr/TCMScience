// The telemetry's daily budget of rows written (src/telemetry/budget.js): what it lets through, one visitor's share,
// the day it follows (UTC, as Cloudflare's allowance), and that an object started again remembers what was written.
import assert from "node:assert/strict";
import test from "node:test";

import { RowBudget, VISITOR_SHARE, markSpent, resetSpent, spentFor, untilUtcMidnight } from "../src/telemetry/budget.js";
import { sqlStore } from "./helpers.js";

const NOW = Date.parse("2026-10-14T23:59:00Z");

test("a day's rows: let through until written, a visitor's share apart, new again at 00:00 UTC", () => {
  const b = new RowBudget(sqlStore());
  assert.deepEqual(b.check(0, { now: NOW }), { ok: true }); // no budget (a paid plan)
  assert.deepEqual(b.check(1000, { caller: "a", now: NOW }), { ok: true });
  b.charge(1000 * VISITOR_SHARE, { caller: "a", now: NOW });
  assert.deepEqual(b.check(1000, { caller: "a", now: NOW }), { ok: false, reason: "visitor", retry: 60 });
  assert.deepEqual(b.check(1000, { caller: "b", now: NOW }), { ok: true }); // another visitor
  b.charge(900, { caller: "b", now: NOW });
  assert.deepEqual(b.check(1000, { caller: "c", now: NOW }), { ok: false, reason: "budget", retry: 60 });
  assert.equal(b.check(1000, { now: NOW + 60000 }).ok, true); // the next UTC day
  assert.equal(untilUtcMidnight(Date.parse("2026-10-14T00:00:00Z")), 86400);
});

test("what was written survives the object being started again (to within a checkpoint), and old days are dropped", () => {
  const sql = sqlStore();
  const b = new RowBudget(sql);
  for (let i = 0; i < 6; i++) b.charge(100, { now: NOW }); // 600 rows, kept at two checkpoints (each one row more)
  const again = new RowBudget(sql);
  const state = again.state(5000, NOW);
  assert.equal(state.day, "2026-10-14");
  assert.equal(state.rows, 602);
  assert.equal(new RowBudget(sql).check(state.rows, { now: NOW }).ok, false);
  // the next day starts from nothing and forgets the last
  const next = new RowBudget(sql);
  assert.equal(next.state(5000, NOW + 86400000).rows, 1); // the deleted row of yesterday
  assert.deepEqual(sql.exec("SELECT day FROM row_budget").toArray().map((r) => r.day), []);
});

test("an object that said its day is spent is not asked again until it is new", () => {
  resetSpent();
  assert.equal(spentFor("usage", NOW), 0);
  markSpent("usage", 60, NOW);
  assert.equal(spentFor("usage", NOW), 60);
  assert.equal(spentFor("usage", NOW + 30000), 30);
  assert.equal(spentFor("conversations", NOW), 0);
  assert.equal(spentFor("usage", NOW + 60000), 0);
  resetSpent();
});
