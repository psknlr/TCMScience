// The administrators' dashboard (web/admin/): its words in both languages, its chart scales, and what it draws from
// the real admin API (edge/src/admin.js) over the real stores — every word a visitor wrote shown as text and never as
// markup, conversation text fetched only when asked for, deletion and the audit trail, a lapsed session; and that no
// page of the site carries an admin secret, an inline script or an inline style (the admin pages' policy forbids both).
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

import { handleAdmin } from "../../edge/src/admin.js";
import { ConversationCore } from "../../edge/src/telemetry/conversations.js";
import { SecurityCore } from "../../edge/src/telemetry/security.js";
import { UsageCore } from "../../edge/src/telemetry/usage.js";
import { sqlStore } from "../../edge/test/helpers.js";
import { ticks } from "../admin/charts.js";
import { EN, ZH, setLang } from "../admin/i18n.js";
import { CONSENT_VERSION, normalizeRecord, ownerOf } from "../js/core/telemetry-contract.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.join(here, "..");
const ADMIN_DIR = path.join(WEB, "admin");
const read = (f) => fs.readFileSync(path.join(ADMIN_DIR, f), "utf8");
const ADMIN = "admin-token-for-dashboard-tests-0123456789";
const SITE = "https://science.impf.ai";
const NOW = Date.parse("2026-10-11T04:00:00Z");
const DAY = "2026-10-11"; // the report day (UTC+8) of NOW
const HOSTILE_Q = '<img src=x onerror="globalThis.pwned=1">何首乌的肝毒性证据有哪些？';
const HOSTILE_A = '<script>globalThis.pwned=2</script>**结论**：证据等级为病例报告。';

// ------------------------------------------------------------------------------------------------- the words

/** The top-level keys of one dictionary in i18n.js, as written (an object literal keeps only the last of two). */
function writtenKeys(name) {
  const src = read("i18n.js");
  const start = src.indexOf(`const ${name} = {`);
  const body = src.slice(src.indexOf("{", start) + 1, src.indexOf("\n};", start));
  const keys = [];
  let depth = 0;
  let quote = null;
  for (let i = 0; i < body.length; i++) {
    const c = body[i];
    if (quote) {
      if (c === "\\") i++;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") quote = c;
    else if (c === "/" && body[i + 1] === "/") i = body.indexOf("\n", i);
    else if (c === "{") depth++;
    else if (c === "}") depth--;
    else if (depth === 0 && /[\s,]/.test(body[i - 1] ?? " ")) {
      const m = /^([A-Za-z_]\w*)\s*:/.exec(body.slice(i));
      if (m) {
        keys.push(m[1]);
        i += m[1].length;
      }
    }
  }
  return keys;
}

const flat = (o, p = "") => Object.entries(o).flatMap(([k, v]) => (v && typeof v === "object" ? flat(v, `${p}${k}.`) : [`${p}${k}`]));
const lookup = (o, key) => key.split(".").reduce((x, k) => (x && typeof x === "object" ? x[k] : undefined), o);

test("the dashboard's words: the same keys in Chinese and English, none written twice, every key the code uses present", () => {
  assert.deepEqual(flat(ZH).sort(), flat(EN).sort());
  for (const name of ["ZH", "EN"]) {
    const keys = writtenKeys(name);
    assert.ok(keys.length > 100);
    assert.deepEqual(keys.filter((k, i) => keys.indexOf(k) !== i), [], `${name}: a key written twice silently loses one of its words`);
  }
  for (const file of ["dashboard.js", "charts.js"]) {
    const code = read(file);
    for (const [, key] of code.matchAll(/\bt\(\s*"([^"]+)"/g)) {
      for (const dict of [ZH, EN]) assert.equal(typeof lookup(dict, key), "string", `${file}: ${key}`);
    }
    for (const [, group] of code.matchAll(/\bt\(\s*`([a-z_]+)\.\$\{/g)) {
      for (const dict of [ZH, EN]) assert.equal(typeof dict[group], "object", `${file}: the group ${group}`);
    }
  }
  // the house style of the Studio's own words holds here too: no exclamation, no apology
  for (const v of [...flat(ZH).map((k) => lookup(ZH, k)), ...flat(EN).map((k) => lookup(EN, k))]) {
    assert.doesNotMatch(v, /[!！]|抱歉|对不起|sorry/i, v);
  }
});

test("chart scales: whole-number ticks from zero to a round top", () => {
  assert.deepEqual(ticks(0), [0, 1]);
  assert.deepEqual(ticks(3), [0, 1, 2, 3]);
  assert.deepEqual(ticks(1234), [0, 500, 1000, 1500]);
  assert.deepEqual(ticks(7), [0, 2, 4, 6, 8]);
  const many = ticks(98765);
  assert.equal(many[0], 0);
  assert.ok(many.at(-1) >= 98765 && many.length <= 6);
});

// ------------------------------------------------------------------------------------------------- what is published

function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]));
}

test("no page of the site carries an admin secret; the admin pages run no inline script or style and write no markup", () => {
  // the public app: not a word of the admin side, so nothing there can leak or reach it
  const app = [...walk(path.join(WEB, "js")), path.join(WEB, "index.html"), path.join(WEB, "sw.js")].filter((f) => /\.(js|mjs|html)$/.test(f));
  for (const f of app) {
    const text = fs.readFileSync(f, "utf8");
    assert.doesNotMatch(text, /ADMIN_TOKEN|EXPORT_TOKEN|\/api\/admin|__Host-tcms_admin/, path.relative(WEB, f));
  }
  for (const f of fs.readdirSync(ADMIN_DIR)) {
    const text = read(f);
    assert.doesNotMatch(text, /ADMIN_TOKEN|EXPORT_TOKEN|Bearer\s+[A-Za-z0-9]/, `admin/${f}: the dashboard holds no secret, the server checks it`);
    if (f.endsWith(".js")) {
      assert.doesNotMatch(text, /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|cssText/, `admin/${f}`);
      assert.doesNotMatch(text, /setAttribute\(\s*["']style|setAttribute\(\s*["']on/, `admin/${f}`);
    }
    if (f.endsWith(".html")) {
      assert.doesNotMatch(text, /<style|\sstyle=|\son[a-z]+=/i, `admin/${f}`);
      for (const [tag] of text.matchAll(/<script\b[^>]*>/g)) assert.match(tag, /\ssrc="\/admin\/[a-z]+\.js"/, `admin/${f}: ${tag}`);
      assert.match(text, /<meta name="robots" content="noindex, nofollow">/);
    }
  }
});

// ------------------------------------------------------------------------------------------------- the dashboard itself

async function stores() {
  const usage = new UsageCore(sqlStore());
  const conversations = new ConversationCore(sqlStore());
  const security = new SecurityCore(sqlStore());
  await usage.ingest({
    v: 1, batch: "b-1", events: [
      { type: "app_init", layout: "wide", lang: "zh", browser: "chromium", mobile: false },
      { type: "visit", route: "home" },
      { type: "pageview", route: "home" },
      { type: "pageview", route: "conversation" },
      { type: "pageview", route: "settings", tab: "privacy" },
      { type: "turn_start", provider: "tao", model: "Tao-S1", first: true },
      { type: "turn_end", turn: "t-1", provider: "tao", model: "Tao-S1", status: "completed", steps: 2, calls: 2, ms: 4200, first_ms: 600, tokens_in: 900, tokens_out: 120, recorded: true, first: true },
      { type: "turn_end", turn: "t-2", provider: "tao", model: "Tao-S1", status: "failed", calls: 1, error_kind: "network", error_type: "timeout", excluded: "uploaded_file" },
      { type: "tool_end", call: "t-1:c1", tool: "tcm_herb", kind: "native", place: "browser", status: "succeeded", ms: 140 },
      { type: "error", category: "js_error", name: "TypeError", where: "js/ui/app.js:12" },
      { type: "availability", service: "relay", ok: true },
      { type: "consent", decision: "granted", version: CONSENT_VERSION },
    ],
  }, { day: DAY, country: "CN", visitor: "visitor-a", now: NOW });
  usage.observe([{ metric: "connector", a: "rest.uniprot.org", b: "2xx", n: 3 }, { metric: "relay_call", a: "ok", n: 2 }], { day: DAY });
  const owner = await ownerOf("k".repeat(43));
  const record = normalizeRecord({
    v: 1, id: "rec-1", owner, consent: CONSENT_VERSION, status: "completed", question: HOSTILE_Q, answer: HOSTILE_A,
    provider: "tao", model: "Tao-S1", steps: 2, calls: 2, ms: 4200,
    tools: [{ name: "tcm_herb", kind: "native", where: "browser", status: "succeeded", ms: 140, arg_keys: ["herb"] }],
  });
  assert.ok(record, "a record as the contract keeps it");
  conversations.add(record, { received: new Date(NOW).toISOString(), day: DAY });
  // the same visitor, forty days earlier: outside a 7-day view, found by their record reference
  conversations.add({ ...record, id: "rec-0", question: "黄芪的研究证据？", answer: "见文献。" }, { received: "2026-09-01T04:00:00.000Z", day: "2026-09-01" });
  await security.record({ kind: "scanner", route: "missing", status: 404, detail: "honeypot",
    facts: { ip: "203.0.113.9", country: "US", method: "GET", path: "/wp-login.php", uaClass: "bot" }, day: DAY }, { now: NOW });
  return { usage, conversations, security };
}

/** The dashboard page in a DOM, its requests answered by the real admin handler signed in with a session cookie. */
async function mount({ expire = false } = {}) {
  const s = await stores();
  const env = { ADMIN_TOKEN: ADMIN, USAGE: {}, CONVERSATIONS: {}, SECURITY: {} };
  const deps = { usage: () => s.usage, conversations: () => s.conversations, security: () => s.security, assets: null, now: () => NOW };
  const browser = { Origin: SITE, "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "198.51.100.7" };
  const login = await handleAdmin(new Request(`${SITE}/api/admin/session`, {
    method: "POST", headers: { ...browser, "Content-Type": "application/json" }, body: JSON.stringify({ token: ADMIN }),
  }), env, deps);
  assert.equal(login.status, 200);
  const cookie = login.headers.get("Set-Cookie").split(";")[0];
  const calls = [];
  const addresses = [];
  const { window, document } = parseHTML(read("index.html"));
  window.confirm = () => true;
  Object.assign(globalThis, {
    window, document, Node: window.Node,
    location: new URL(`${SITE}/admin/`),
    history: { replaceState: (_s, _t, url) => addresses.push(url) },
    requestAnimationFrame: (fn) => setTimeout(() => fn(0), 0),
    fetch: async (input, init = {}) => {
      const url = new URL(String(input), SITE);
      calls.push(`${init.method || "GET"} ${url.pathname}${url.search}`);
      if (expire) return new Response(JSON.stringify({ error: { type: "unauthorized", message: "需要管理员登录" } }), { status: 401 });
      const headers = new Headers(init.headers || {});
      for (const [k, v] of Object.entries(browser)) headers.set(k, v);
      headers.set("Cookie", cookie);
      return handleAdmin(new Request(url, { method: init.method || "GET", headers, body: init.body }), env, deps);
    },
  });
  setLang("zh");
  await import(`../admin/dashboard.js?mount=${Math.random()}`);
  return { s, calls, addresses, window, document };
}

async function until(fn, what, ms = 4000) {
  const start = Date.now();
  for (;;) {
    const v = fn();
    if (v) return v;
    if (Date.now() - start > ms) throw new Error(`timed out waiting for ${what}`);
    await new Promise((r) => setTimeout(r, 10));
  }
}

const click = (window, node) => node.dispatchEvent(new window.Event("click", { bubbles: true }));
const button = (document, text) => [...document.querySelectorAll("button")].find((b) => b.textContent === text);
const tileValue = (document, label) => [...document.querySelectorAll(".tile")].find((t) => t.querySelector(".tile__label")?.textContent === label)?.querySelector(".tile__value")?.textContent;

test("the dashboard draws the real API's summary, and shows a visitor's words as text only when asked", async () => {
  const { s, calls, addresses, window, document } = await mount();
  await until(() => document.querySelector(".kpis"), "the headline numbers");
  assert.deepEqual(calls, ["GET /api/admin/telemetry/summary?days=7"]);
  assert.equal(tileValue(document, "页面浏览"), "3");
  assert.equal(tileValue(document, "访问"), "1");
  assert.equal(tileValue(document, "对话轮次"), "2");
  assert.equal(tileValue(document, "成功率"), "50%");
  assert.equal(tileValue(document, "模型调用"), "3");
  assert.match(document.querySelector("#sub").textContent, /2026-10-05 至 2026-10-11（UTC\+8）/);
  const text = document.querySelector("#content").textContent;
  for (const word of ["访问量", "结束方式", "rest.uniprot.org", "tcm_herb", "含上传文件", "扫描探测", "管理员登录", "203.0.113.0/24"]) assert.ok(text.includes(word), word);
  await until(() => document.querySelectorAll(".chart svg").length >= 3, "the charts");
  // the outcome bar: colour with an icon and a word for each state
  assert.deepEqual([...document.querySelectorAll(".stack-wrap .state")].map((x) => [x.className, x.textContent]),
    [["state state--good", "已完成"], ["state state--none", "已停止"], ["state state--serious", "被中断"], ["state state--critical", "失败"]]);

  // conversation text: not fetched until the administrator asks
  assert.ok(!calls.some((c) => c.includes("/conversations")));
  assert.equal(document.querySelector(".record"), null);
  click(window, button(document, "显示对话记录"));
  await until(() => document.querySelector(".record"), "a record");
  assert.ok(calls.includes("GET /api/admin/telemetry/conversations?days=7&limit=25"));
  const texts = [...document.querySelectorAll(".record__text")].map((x) => x.textContent);
  assert.deepEqual(texts, [HOSTILE_Q, HOSTILE_A], "shown exactly as written");
  assert.equal(document.querySelector("#content img, #content script, #content [onerror]"), null, "never as markup");
  assert.equal(globalThis.pwned, undefined);
  assert.ok((await s.security.summary(DAY, DAY)).audit.some((a) => a.kind === "admin_view"), "viewing text is audited");

  // deletion, by its id, audited
  click(window, button(document, "删除"));
  await until(() => document.querySelector(".record") === null, "the record to go");
  assert.ok(calls.includes("POST /api/admin/telemetry/conversations/delete"));
  assert.equal(s.conversations.list({ from: DAY, to: DAY }).items.length, 0);
  assert.ok((await s.security.summary(DAY, DAY)).audit.some((a) => a.kind === "admin_delete" && a.detail === "ids:1"));

  // a visitor's deletion request: their record reference finds their records on any day, and all of them go at once
  const owner = (await ownerOf("k".repeat(43)));
  const ref = document.querySelector('input[type="search"][placeholder="按记录编号查找"]');
  ref.value = "not-hex";
  ref.dispatchEvent(new window.Event("change"));
  assert.match(document.querySelector("#status").textContent, /十六进制/);
  ref.value = owner.slice(0, 12).toUpperCase();
  ref.dispatchEvent(new window.Event("change"));
  await until(() => document.querySelector(".record"), "the visitor's older record");
  assert.ok(calls.includes(`GET /api/admin/telemetry/conversations?days=7&limit=25&owner=${owner.slice(0, 12)}`));
  assert.deepEqual([...document.querySelectorAll(".record__q")].map((x) => x.textContent), ["黄芪的研究证据？"]);
  assert.ok([...document.querySelectorAll(".record__facts li")].some((li) => li.textContent === `记录编号 ${owner.slice(0, 12)}`));
  click(window, button(document, "删除此访客的全部记录"));
  await until(() => /已删除 1 条/.test(document.querySelector("#status").textContent), "the deletion");
  assert.equal(s.conversations.list({ from: "2026-01-01", to: DAY, owner }).items.length, 0);
  assert.ok((await s.security.summary(DAY, DAY)).audit.some((a) => a.kind === "admin_delete" && a.detail === "owner:1"));
  assert.equal(button(document, "删除此访客的全部记录").hidden, true);

  // another period, kept in the address; another language, from what was loaded
  click(window, button(document, "30 天"));
  await until(() => calls.includes("GET /api/admin/telemetry/summary?days=30"), "the 30-day summary");
  assert.equal(addresses.at(-1), "/admin/?days=30");
  await until(() => document.querySelector("#sub").textContent.includes("2026-09-12"), "the 30-day view");
  click(window, document.querySelector("#lang"));
  await until(() => document.title === EN.title, "English");
  assert.equal(tileValue(document, "Page views"), "3");
  assert.equal(document.documentElement.lang, "en");
  // the exports: aggregates and, for an administrator, the admin-only datasets, all for the period chosen
  const hrefs = [...document.querySelectorAll(".exports a")].map((a) => a.getAttribute("href"));
  assert.ok(hrefs.includes("/api/admin/telemetry/export?dataset=daily&format=csv&days=30"));
  assert.ok(hrefs.includes("/api/admin/telemetry/export?dataset=conversations&format=json&days=30"));
  setLang("zh");
});

test("a visitor's records found by reference: deleting one keeps the delete-all for the rest, deleting the last shows none", async () => {
  const { window, document } = await mount();
  const owner = await ownerOf("k".repeat(43));
  const ref = await until(() => document.querySelector('input[type="search"][placeholder="按记录编号查找"]'), "the reference field");
  ref.value = owner.slice(0, 12);
  ref.dispatchEvent(new window.Event("change"));
  await until(() => document.querySelectorAll(".record").length === 2, "both of this visitor's records");
  const removeAll = button(document, "删除此访客的全部记录");
  assert.equal(removeAll.hidden, false);
  click(window, document.querySelector(".record .btn--danger"));
  await until(() => document.querySelectorAll(".record").length === 1, "one deleted");
  assert.equal(removeAll.hidden, false, "the other record is still this visitor's");
  click(window, document.querySelector(".record .btn--danger"));
  await until(() => document.querySelectorAll(".record").length === 0, "both deleted");
  assert.equal(removeAll.hidden, true);
  assert.ok(document.querySelector(".records .empty"), "says there are none");
});

test("an export button takes every page of the export and saves them as one file", async () => {
  const { s, window, document, calls } = await mount();
  s.usage.observe(Array.from({ length: 1500 }, (_, i) => ({ metric: "connector", a: `h${String(i).padStart(4, "0")}.example.org`, b: "2xx", n: 1 })), { day: DAY });
  const files = [];
  const created = new Map();
  const { createObjectURL, revokeObjectURL } = URL;
  URL.createObjectURL = (blob) => { const href = `blob:${SITE}/${created.size}`; created.set(href, blob); return href; };
  URL.revokeObjectURL = () => {};
  const proto = window.HTMLElement.prototype;
  const own = Object.getOwnPropertyDescriptor(proto, "click");
  proto.click = function click() { if (this.getAttribute("download")) files.push({ name: this.getAttribute("download"), blob: created.get(this.getAttribute("href")) }); };
  try {
    const link = await until(() => [...document.querySelectorAll(".exports a")].find((a) => a.getAttribute("href").includes("dataset=connectors&format=csv")), "the export link");
    click(window, link);
    await until(() => files.length, "the saved file");
    assert.match(files[0].name, /^tcmscience-connectors-\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.csv$/);
    const bytes = new Uint8Array(await files[0].blob.arrayBuffer());
    assert.deepEqual([...bytes.slice(0, 3)], [0xef, 0xbb, 0xbf]);
    const lines = new TextDecoder("utf-8").decode(bytes).trim().split("\r\n");
    assert.equal(lines[0], "day,source,outcome,n");
    assert.equal(lines.length, 1 + 1500 + 1); // the header, these 1,500 and the fixtures' rest.uniprot.org
    assert.equal(lines.filter((l) => l.startsWith("\ufeff") || l === "day,source,outcome,n").length, 1, "one header, one BOM");
    assert.equal(calls.filter((c) => c.startsWith("GET /api/admin/telemetry/export?dataset=connectors&format=csv")).length, 2, "two pages");
  } finally {
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    if (own) Object.defineProperty(proto, "click", own);
    else delete proto.click;
  }
});

test("a lapsed session says so and links to the sign-in page; nothing else is shown", async () => {
  const { document } = await mount({ expire: true });
  await until(() => document.querySelector("#status a"), "the sign-in link");
  assert.equal(document.querySelector("#status a").getAttribute("href"), "/admin/");
  assert.match(document.querySelector("#status").textContent, /登录已过期/);
  assert.equal(document.querySelector(".kpis"), null);
});
