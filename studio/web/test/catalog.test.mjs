import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { Catalog, cleanSchema, loadCatalog, localCall, mergeCatalogs, toProviderTools } from "../js/core/catalog.js";
import { fakeFetch, testCatalogDoc } from "./fixtures/fakes.mjs";

const cat = () => new Catalog(testCatalogDoc());

test("lookups: entries by id, core tools by name, a core tool's mapped entry", () => {
  const c = cat();
  assert.equal(c.byId["native.tcm_herb"].kind, "native");
  assert.equal(c.coreTool("tcm_herb").maps_to, "native.tcm_herb");
  assert.equal(c.entry("tcm_herb").id, "native.tcm_herb");
  assert.equal(c.entry("nope"), null);
  assert.equal(c.categories.find((x) => x.id === "omics").count, 3);
});

test("search: Chinese words and tags, English words, ids, ranked", () => {
  const c = cat();
  assert.equal(c.search("配伍禁忌")[0].id, "native.tcm_compatibility");
  assert.equal(c.search("十八反")[0].id, "native.tcm_compatibility");
  assert.equal(c.search("性味归经")[0].id, "native.tcm_herb");
  assert.equal(c.search("reverse complement")[0].id, "native.reverse_complement");
  assert.equal(c.search("native.reverse_complement")[0].id, "native.reverse_complement");
  assert.equal(c.search("DNA 序列")[0].id, "native.reverse_complement");
  assert.equal(c.search("europe pmc")[0].id, "connector.europepmc.search");
  assert.equal(c.search("配伍").length >= 1, true, "a two-character word matches inside a longer tag");
  assert.deepEqual(c.search("zzzz"), []);
  assert.ok(c.search("").length === c.entries.length || c.search("").length === 25, "an empty query lists entries");
});

test("search filters: category, kind, runnable now", () => {
  const c = cat();
  assert.ok(c.search("", { category: "omics" }).every((e) => e.category === "omics"));
  assert.ok(c.search("", { kind: "connector" }).every((e) => e.kind === "connector"));
  const runnable = c.search("", { runnableNow: true, isRunnable: (e) => e.exec.includes("browser") });
  assert.ok(runnable.length > 0 && runnable.every((e) => e.exec.includes("browser")));
  assert.deepEqual(c.search("x", { limit: 1 }).length <= 1, true);
});

test("did-you-mean suggestions", () => {
  assert.deepEqual(cat().suggest("tcm_herbs")[0], "tcm_herb");
  assert.ok(cat().suggest("reverse_complement").includes("native.reverse_complement"));
});

test("merge: the runner's view of where an entry runs wins; runner-only entries are added; titles are kept", () => {
  const built = testCatalogDoc();
  const runner = {
    schema: "tcmstudio.catalog/1", versions: { tcmstudio: "0.1.1" },
    core: [{ name: "tcm_herb", exec: ["browser", "runner"], parameters: { type: "object", properties: {} } }],
    entries: [
      { id: "native.dock", exec: ["runner"], available: true, missing: [], title: { zh: "", en: "Docking (Vina)" } },
      { id: "native.extra", kind: "native", exec: ["runner"], title: { zh: "新", en: "New" }, category: "system", summary: "only on the runner" },
    ],
  };
  const merged = new Catalog(mergeCatalogs(built, runner));
  assert.equal(merged.byId["native.dock"].available, true);
  assert.deepEqual(merged.byId["native.dock"].missing, []);
  assert.deepEqual(merged.byId["native.dock"].title, { zh: "分子对接", en: "Docking (Vina)" });
  assert.equal(merged.byId["native.extra"].summary, "only on the runner");
  assert.equal(merged.versions.tcmstudio, "0.1.1");
  assert.equal(merged.versions.bioagent, "0.2.7");
  assert.equal(merged.coreTool("tcm_herb").description, "Look up a herb in the seed corpus.", "fields the runner left out are kept");
  assert.deepEqual(merged.sources, ["built", "runner"]);
});

test("loadCatalog: the browser runtime's catalog, else the static file, then the connected runner's", async () => {
  const doc = testCatalogDoc();
  const viaBrowser = await loadCatalog({ browser: { catalog: async () => doc } });
  assert.equal(viaBrowser.entries.length, doc.entries.length);
  const f = fakeFetch(() => new Response(JSON.stringify(doc), { headers: { "content-type": "application/json" } }));
  const viaFile = await loadCatalog({ browser: { catalog: async () => { throw new Error("not booted"); } } }, { fetch: f, url: "https://science.impf.ai/runtime/catalog.json" });
  assert.equal(f.calls[0].url, "https://science.impf.ai/runtime/catalog.json");
  assert.equal(viaFile.core.length, doc.core.length);
  const runner = { status: "ready", catalog: async () => ({ entries: [{ id: "native.dock", available: true, exec: ["runner"] }] }) };
  const both = await loadCatalog({ browser: { catalog: async () => doc }, runner });
  assert.equal(both.byId["native.dock"].available, true);
  const offline = await loadCatalog({ browser: { catalog: async () => doc }, runner: { status: "offline", catalog: async () => { throw new Error("must not be called"); } } });
  assert.equal(offline.byId["native.dock"].available, false);
  const none = await loadCatalog({}, { fetch: fakeFetch(() => new Response("", { status: 404 })), url: "https://x/c.json" });
  assert.equal(none.entries.length, 0);
});

test("provider tools: OpenAI functions and Anthropic input_schema, schemas made acceptable", () => {
  const c = cat();
  const oa = toProviderTools(c.core, "openai");
  const herb = oa.find((t) => t.function.name === "tcm_herb");
  assert.deepEqual(herb, { type: "function", function: { name: "tcm_herb", description: "Look up a herb in the seed corpus.", parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } } });
  const compat = oa.find((t) => t.function.name === "tcm_compatibility");
  assert.deepEqual(compat.function.parameters.properties.herbs, { type: "array", items: {} }, "an array without items gets them");
  const an = toProviderTools(c.core, "anthropic");
  assert.deepEqual(Object.keys(an[0]).sort(), ["description", "input_schema", "name"]);
  assert.deepEqual(cleanSchema(undefined), { type: "object", properties: {} });
  assert.deepEqual(cleanSchema({ type: "object", properties: { o: { type: "object" } } }).properties.o, { type: "object", properties: {} });
});

test("local catalog_search: an envelope with matches, where each can run now, and what the rest need", async () => {
  const c = cat();
  const where = (e) => (e.exec.includes("browser") ? "browser" : null);
  const env = await localCall("catalog_search", { query: "literature", runnable_now: false }, { catalog: c, where, status: { browser: "idle", runner: "offline", web: false } });
  assert.equal(env.status, "succeeded");
  assert.equal(env.via, "system.catalog_search");
  assert.equal(env.receipt.where, "browser");
  assert.match(env.receipt.input_sha256, /^[0-9a-f]{64}$/);
  const m = env.result.matched.find((x) => x.id === "connector.europepmc.search");
  assert.equal(m.runnable_now, false);
  assert.ok(m.needs.some((n) => /runner/.test(n)));
  assert.ok(m.needs.some((n) => /web access/.test(n)));
  const text = JSON.parse(env.text);
  assert.ok(text.how.includes("call_tool"));
  const only = await localCall("catalog_search", { query: "", runnable_now: true, limit: 50 }, { catalog: c, where, status: {} });
  assert.ok(only.result.matched.length <= 25);
  assert.ok(only.result.matched.every((x) => x.runnable_now));
  const caps = await localCall("capabilities_status", {}, { catalog: c, where, status: { browser: "ready", runner: "offline", compute: "auto", web: true } });
  assert.equal(caps.result.browser.status, "ready");
  assert.equal(caps.result.web_access, true);
  assert.equal(caps.result.counts.entries, c.entries.length);
  assert.equal(await localCall("tcm_herb", {}, { catalog: c }), null);
});

test("never_offered (the acts reserved for a person) survives loading and merging", () => {
  const built = { ...testCatalogDoc(), never_offered: { "clinic.sign": "a person's act" } };
  const runner = { ...testCatalogDoc(), never_offered: { "lab.order": "a clinician's act" } };
  const merged = new Catalog(mergeCatalogs(built, runner));
  assert.deepEqual(merged.neverOffered, { "clinic.sign": "a person's act", "lab.order": "a clinician's act" });
  assert.deepEqual(new Catalog(mergeCatalogs(built, null)).neverOffered, { "clinic.sign": "a person's act" });
  assert.deepEqual(new Catalog(testCatalogDoc()).neverOffered, {});
  assert.deepEqual(new Catalog(merged.toJSON()).neverOffered, merged.neverOffered);
});
