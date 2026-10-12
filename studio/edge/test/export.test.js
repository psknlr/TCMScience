// The owner's export script (scripts/export-telemetry.mjs): it writes only to a private place outside this repository,
// takes the token from the environment only, takes aggregated data by default and visitors' words only with the admin
// token, against the real admin handler; and its retention removes only the export folders it made itself.
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { REPOSITORY, checkDestination, parse, prune, run, workTreeOf } from "../scripts/export-telemetry.mjs";
import { handleAdmin } from "../src/admin.js";
import { ConversationCore } from "../src/telemetry/conversations.js";
import { SecurityCore } from "../src/telemetry/security.js";
import { UsageCore } from "../src/telemetry/usage.js";
import { CONSENT_VERSION, ownerOf } from "../../web/js/core/telemetry-contract.js";
import { sqlStore } from "./helpers.js";

const ADMIN = "admin-token-for-export-tests-0123456789abcd";
const EXPORT = "export-token-for-export-tests-0123456789abc";
const NOW = Date.parse("2026-10-11T04:00:00Z");
const DAY = "2026-10-11";
const tmp = () => mkdtempSync(path.join(os.tmpdir(), "tcms-export-"));

test("options: aggregated data by default, the token never an argument, https only", () => {
  const o = parse(["--out", "/x"]);
  assert.deepEqual([o.url, o.days, o.datasets, o.format, o.keepDays, o.privateRepo], ["https://science.impf.ai", 30, ["daily", "connectors", "geo"], "csv", 180, false]);
  assert.throws(() => parse(["--out", "/x", "--token", "abc"]), /environment, never from an argument|never from an argument/);
  assert.throws(() => parse(["--days", "14"]), /7, 30 or 90/);
  assert.throws(() => parse(["--datasets", "daily,everything"]), /--datasets/);
  assert.throws(() => parse(["--url", "http://science.impf.ai"]), /https/);
  assert.equal(parse(["--url", "http://127.0.0.1:8787/"]).url, "http://127.0.0.1:8787");
  assert.throws(() => parse(["--keep-days", "-1"]), /whole number/);
});

test("destination: never inside this repository; another git work tree only when said to be private", () => {
  if (existsSync(path.join(REPOSITORY, ".git"))) assert.equal(workTreeOf(path.join(REPOSITORY, "studio", "edge")), REPOSITORY);
  assert.throws(() => checkDestination(path.join(REPOSITORY, "exports")), /inside this repository, which is public/);
  assert.throws(() => checkDestination(path.join(REPOSITORY, "studio", "not-yet", "made")), /inside this repository/);
  assert.throws(() => checkDestination(""), /--out is required/);
  const other = tmp();
  mkdirSync(path.join(other, ".git"));
  assert.throws(() => checkDestination(path.join(other, "data")), /pass --private-repo only if that repository is private/);
  assert.equal(checkDestination(path.join(other, "data"), { privateRepo: true }), path.join(other, "data"));
  // a private repository flag never opens this one
  assert.throws(() => checkDestination(path.join(REPOSITORY, "x"), { privateRepo: true }), /inside this repository/);
  const plain = tmp();
  assert.equal(checkDestination(path.join(plain, "exports")), path.join(plain, "exports"));
});

async function deployment() {
  const usage = new UsageCore(sqlStore());
  const conversations = new ConversationCore(sqlStore());
  const security = new SecurityCore(sqlStore());
  await usage.ingest({ v: 1, batch: "b", events: [{ type: "pageview", route: "home" }, { type: "visit", route: "home" }] }, { day: DAY, country: "CN", visitor: "v", now: NOW });
  conversations.add({ v: 1, id: "r1", owner: await ownerOf("z".repeat(43)), consent: CONSENT_VERSION, status: "completed", question: "黄芪的证据？", answer: "见文献。" }, { received: new Date(NOW).toISOString(), day: DAY });
  const env = { ADMIN_TOKEN: ADMIN, EXPORT_TOKEN: EXPORT };
  const deps = { usage: () => usage, conversations: () => conversations, security: () => security, assets: null, now: () => NOW };
  const seen = [];
  const fetcher = async (url, init = {}) => {
    seen.push({ url, auth: init.headers?.Authorization });
    return handleAdmin(new Request(url, { headers: init.headers }), env, deps);
  };
  return { fetcher, seen, security };
}

test("a run writes owner-only files of the datasets asked for; visitors' words need the admin token, on both sides", async () => {
  const { fetcher, seen, security } = await deployment();
  const out = path.join(tmp(), "exports");
  const options = parse(["--out", out, "--days", "7"]);
  const r = await run(options, { env: { TCMS_EXPORT_TOKEN: ` ${EXPORT}\n` }, fetcher, now: NOW });
  assert.equal(r.folder, path.join(out, DAY));
  assert.deepEqual(r.files.map((f) => path.basename(f.file)), [
    "tcmscience-daily-2026-10-05_2026-10-11.csv", "tcmscience-connectors-2026-10-05_2026-10-11.csv", "tcmscience-geo-2026-10-05_2026-10-11.csv",
  ]);
  for (const f of r.files) assert.equal(statSync(f.file).mode & 0o777, 0o600, f.file);
  assert.equal(statSync(r.folder).mode & 0o777, 0o700);
  assert.match(readFileSync(r.files[0].file, "utf8"), /pageview,home,CN,1/);
  assert.ok(seen.every((s) => s.auth === `Bearer ${EXPORT}` && s.url.startsWith("https://science.impf.ai/api/admin/telemetry/export?")));
  // the export token is refused what visitors wrote here, before asking; and the server refuses it too
  await assert.rejects(run(parse(["--out", out, "--datasets", "conversations"]), { env: { TCMS_EXPORT_TOKEN: EXPORT }, fetcher, now: NOW }), /need TCMS_ADMIN_TOKEN/);
  const refused = await fetcher("https://science.impf.ai/api/admin/telemetry/export?dataset=conversations&format=json&days=7", { headers: { Authorization: `Bearer ${EXPORT}` } });
  assert.equal(refused.status, 403);
  // with the admin token: the records, as JSON; the export is in the audit trail
  const withText = await run(parse(["--out", out, "--datasets", "conversations", "--format", "json", "--days", "7"]), { env: { TCMS_ADMIN_TOKEN: ADMIN }, fetcher, now: NOW });
  const json = JSON.parse(readFileSync(withText.files[0].file, "utf8"));
  assert.deepEqual(json.rows.map((x) => x.question), ["黄芪的证据？"]);
  assert.ok((await security.summary(DAY, DAY)).audit.some((a) => a.kind === "admin_export" && a.detail.startsWith("conversations.json")));
  // a wrong or missing token: nothing written, and the token is never in the message
  await assert.rejects(run(options, { env: {}, fetcher, now: NOW }), /set TCMS_EXPORT_TOKEN/);
  const wrong = "w".repeat(40);
  const err = await run(options, { env: { TCMS_EXPORT_TOKEN: wrong }, fetcher, now: NOW }).then(() => null, (e) => e);
  assert.match(err.message, /HTTP 401 \(the token was refused\)/);
  assert.doesNotMatch(err.message, new RegExp(wrong));
});

test("retention: only the dated folders this script made, and only those older than --keep-days, are deleted", () => {
  const out = tmp();
  const made = (name, mark = true) => {
    mkdirSync(path.join(out, name));
    if (mark) writeFileSync(path.join(out, name, ".tcmscience-export"), "x");
    writeFileSync(path.join(out, name, "a.csv"), "x");
  };
  made("2026-01-01");
  made("2026-09-30");
  made("2025-12-31", false); // not ours: kept
  mkdirSync(path.join(out, "notes"));
  assert.deepEqual(prune(out, 30, NOW), ["2026-01-01"]);
  assert.deepEqual(readdirSync(out).sort(), ["2025-12-31", "2026-09-30", "notes"]);
  assert.deepEqual(prune(out, 0, NOW), []);
  assert.equal(existsSync(path.join(out, "2026-09-30", "a.csv")), true);
});
