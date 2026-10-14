// The Worker's own counts, summed in memory between flushes (src/telemetry/buffer.js): what is kept, when a flush is
// due, and that a flood never makes flushes more frequent (each one is a Durable Object call from the shared budget).
import assert from "node:assert/strict";
import test from "node:test";

import { FLUSH_MS, MetricBuffer, SecurityBuffer } from "../src/telemetry/buffer.js";

const DAY = "2026-10-14";

test("metrics: summed by day, metric and dimensions; the first after a quiet spell is due at once, then once a window", () => {
  const b = new MetricBuffer();
  assert.equal(b.due(1000), false); // nothing held
  b.add([{ metric: "relay_call", a: "ok" }, { metric: "relay_tokens", a: "in", n: 120 }], DAY);
  assert.equal(b.due(1000), true); // never flushed: the first goes at once
  assert.deepEqual(b.take(1000), [{ day: DAY, list: [{ metric: "relay_call", a: "ok", b: "", n: 1 }, { metric: "relay_tokens", a: "in", b: "", n: 120 }] }]);
  for (let i = 0; i < 500; i++) b.add([{ metric: "connector", a: "rest.uniprot.org", b: "2xx" }, { metric: "connector_ms", a: "rest.uniprot.org", n: 40 }], DAY);
  b.add([{ metric: "relay_call", a: "ok" }], "2026-10-15");
  b.add([{ metric: "bad", n: -1 }, null, { metric: "relay_call", a: "ok", n: 0 }], DAY); // ignored: not positive
  b.add([{ metric: "relay_call" }], "not a day");
  assert.equal(b.due(1000 + FLUSH_MS - 1), false);
  assert.equal(b.due(1000 + FLUSH_MS), true);
  const out = b.take(1000 + FLUSH_MS);
  assert.deepEqual(out.map((d) => d.day), [DAY, "2026-10-15"]);
  assert.deepEqual(out[0].list, [{ metric: "connector", a: "rest.uniprot.org", b: "2xx", n: 500 }, { metric: "connector_ms", a: "rest.uniprot.org", b: "", n: 20000 }]);
  assert.equal(b.counts.size, 0);
});

test("metrics: a buffer holding very many distinct counts goes early, but never sooner than five seconds after the last", () => {
  const b = new MetricBuffer();
  b.take(0);
  for (let i = 0; i < 1000; i++) b.add([{ metric: "connector", a: `host-${i}.example.org`, b: "2xx" }], DAY);
  assert.equal(b.due(4999), false);
  assert.equal(b.due(5000), true);
});

test("security events: every one counted, the first twenty kept in full; a flood does not bring the flush forward", () => {
  const b = new SecurityBuffer();
  const ev = (i) => ({ kind: "scanner", route: "page", status: 404, detail: `/wp-${i}.php`, facts: { ip: "203.0.113.9" }, day: DAY });
  b.add(ev(0));
  assert.equal(b.due(1000), true); // the first after a quiet spell
  assert.deepEqual(b.take(1000).counts, [{ day: DAY, kind: "scanner", n: 1 }]);
  for (let i = 1; i <= 5000; i++) b.add(ev(i));
  b.add({ kind: "rate_limited", day: DAY });
  b.add({ kind: "scanner", day: "bad" }); // ignored
  assert.equal(b.due(1000 + FLUSH_MS - 1), false); // 5000 events within the window: still one flush per window
  const out = b.take(1000 + FLUSH_MS);
  assert.deepEqual(out.counts, [{ day: DAY, kind: "scanner", n: 5000 }, { day: DAY, kind: "rate_limited", n: 1 }]);
  assert.equal(out.events.length, 20);
  assert.equal(out.events[0].detail, "/wp-1.php");
  assert.equal(b.events.length, 0);
});
