import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";
import { Catalog, FILE_ID, READINESS, cleanSchema, entryState, loadCatalog, localCall, mergeCatalogs, requirement, runnerOnly, toProviderTools } from "../js/core/catalog.js";
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
  // what the model reads, byte for byte (tcmstudio.catalog._needs words them alike)
  assert.deepEqual(m.needs, ["local runner (tcmstudio serve)", "web access for this project"]);
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

test("canonicalName: the one id a name means up to case and separators, none when two ids share the spelling", () => {
  const doc = testCatalogDoc();
  const c = new Catalog(doc);
  assert.equal(c.canonicalName("connector_europepmc_search"), "connector.europepmc.search");
  assert.equal(c.canonicalName("TCM-HERB"), "tcm_herb");
  assert.equal(c.canonicalName("native.tcm_herb"), "native.tcm_herb");
  assert.equal(c.canonicalName("no.such.tool"), null);
  const twin = { ...doc.entries[0], id: "native.tcm.herb" };       // native.tcm_herb and native.tcm.herb
  const ambiguous = new Catalog({ ...doc, entries: [...doc.entries, twin] });
  assert.equal(ambiguous.canonicalName("native_tcm_herb"), null, "two ids, so neither is guessed");
});

test("catalog_search needs: the same strings as before for every condition they cover", async () => {
  const c = cat();
  const needs = async (status, query) => (await localCall("catalog_search", { query }, { catalog: c, where: () => null, status })).result.matched.find((x) => x.id === query).needs;
  assert.deepEqual(await needs({ runner: "offline", web: false }, "native.dock"), ["local runner (tcmstudio serve)", "install: vina, meeko"]);
  assert.deepEqual(await needs({ runner: "ready", web: true, compute: "browser" }, "native.dock"), ["install: vina, meeko", "compute set to runner or auto"]);
  assert.deepEqual(await needs({ runner: "ready", web: true }, "native.tcm_herb"), []);
  // the two preferences that run nothing add their own reason; nothing above changes
  assert.deepEqual(await needs({ runner: "ready", web: true, compute: "remote" }, "native.tcm_herb"), ["a remote compute backend (none is configured)"]);
  assert.match((await needs({ runner: "ready", web: true, compute: "gpu" }, "native.tcm_herb"))[0], /"gpu", which Studio does not know/);
});

test("requirement(): {need, detail, remedy, names?} in English", () => {
  assert.deepEqual(requirement("dependency", { names: ["vina"] }), { need: "dependency", detail: "install: vina", remedy: "Install vina in the runtime that runs it, then try again; nothing approximate is substituted.", names: ["vina"] });
  assert.deepEqual(Object.keys(requirement("runner")).sort(), ["detail", "need", "remedy"]);
  assert.equal(requirement("network").detail, "web access for this project");
});

/** Entries with the capability-state fields of the newer catalogs, and one without (an older catalog). */
function stateDoc() {
  const doc = testCatalogDoc();
  const extra = [
    { id: "native.hk_lookup", kind: "native", exec: ["browser", "runner"], data: ["hkbu_formulas_manual"], needs: ["a tcmdb store built or imported in this runtime"], state: { implementation: "tested" }, verification: { status: "PASS", levels: { reference_output: { status: "PASS", record: "x" } } }, execution: { browser_compute: { backends: ["webgpu", "js"] } } },
    { id: "tcmdb.query", kind: "tcmdb", exec: ["browser", "runner"], data_from: { param: "dataset" }, parameters: { type: "object", properties: { dataset: { type: "string", enum: ["herb2", "npass"] } } }, state: { implementation: "implemented" }, verification: { status: "UNVERIFIED", levels: {} } },
    { id: "native.pkg", kind: "native", exec: ["browser", "runner"], pyodide_packages: ["numpy", "rdkit"] },
    { id: "connector.x.live", kind: "connector", exec: ["browser", "runner"], network: true, verification: { status: "PASS", levels: { live_upstream: { status: "PASS", at: "2026-10-01", record: "BioScience-Harness/data/connector_live_verification.csv" } } } },
    { id: "job.pipeline.fold", kind: "job", exec: ["runner"], job: true, gpu: true, execution: { engines: [{ id: "esmfold", device: "gpu", selected_by: { arg: "method", value: "esmfold" } }] }, state: { implementation: "implemented" } },
  ];
  for (const e of extra) doc.entries.push({ title: { zh: e.id, en: e.id }, summary: e.id, category: "system", parameters: { type: "object", properties: {} }, example: null, network: false, confirm: false, job: false, gpu: false, heavy: [], tags: [], ...e });
  return doc;
}

test("entryState: per runtime — the browser's decided in the page, the runner's from the runner's catalog", () => {
  const c = new Catalog(stateDoc());
  const s = (id, ctx) => entryState(c.byId[id], ctx);
  // a pure native, browser up, runner offline
  const herb = s("native.tcm_herb", { browser: "idle", runner: "offline", web: true });
  assert.deepEqual(herb.readiness.browser, { state: "ready", requires: [] });
  assert.equal(herb.readiness.runner.state, "needs_runner");
  assert.equal(herb.implementation, "registered", "an older catalog without state: nothing more is claimed");
  // runner-only: needs the runner in the browser; ready on a connected runner (derived from available/missing)
  const lit = s("connector.europepmc.search", { browser: "ready", runner: "ready", web: false });
  assert.equal(lit.readiness.browser.state, "needs_runner");
  assert.equal(lit.readiness.runner.state, "network_unavailable", "the project's web access is the page's to know");
  const dock = s("native.dock", { runner: "ready", web: true });
  assert.equal(dock.readiness.runner.state, "missing_dependency");
  assert.deepEqual(dock.readiness.runner.requires[0].names, ["vina", "meeko"]);
  assert.equal(dock.readiness.runner.derived, true);
  // datasets: present only when known built
  const hk = s("native.hk_lookup", { browser: "ready", web: true });
  assert.equal(hk.implementation, "tested");
  assert.equal(hk.readiness.browser.state, "needs_dataset");
  assert.equal(hk.readiness.browser.requires[0].detail, "the hkbu_formulas_manual dataset built or imported in this runtime");
  assert.match(hk.readiness.browser.requires[0].remedy, /tcmdb\.import_store/);
  assert.deepEqual(hk.readiness.browser.requires[0].names, ["hkbu_formulas_manual"]);
  assert.equal(s("native.hk_lookup", { browser: "ready", built: ["hkbu_formulas_manual"] }).readiness.browser.state, "ready");
  // a dataset named by an argument: that one, or any built store when the call is not known
  assert.equal(s("tcmdb.query", { browser: "ready", built: ["herb2"] }).readiness.browser.state, "ready");
  assert.equal(s("tcmdb.query", { browser: "ready", built: ["herb2"], args: { dataset: "npass" } }).readiness.browser.state, "needs_dataset");
  assert.equal(s("tcmdb.query", { browser: "ready", built: [] }).readiness.browser.state, "needs_dataset");
  // Pyodide packages: held against the entry only when the list of what this browser can load is known
  assert.equal(s("native.pkg", { browser: "ready" }).readiness.browser.state, "ready");
  const pkg = s("native.pkg", { browser: "ready", packages: ["numpy", "scipy"] });
  assert.equal(pkg.readiness.browser.state, "missing_dependency");
  assert.deepEqual(pkg.readiness.browser.requires[0].names, ["rdkit"]);
  assert.equal(pkg.readiness.browser.requires[0].detail, "rdkit loaded in the browser runtime");
  assert.equal(s("tcmdb.query", { browser: "ready", built: [] }).readiness.browser.requires[0].detail, "a tcmdb store built or imported in this runtime");
  // a browser runtime that failed to load
  assert.equal(s("native.tcm_herb", { browser: "offline" }).readiness.browser.state, "unsupported");
  for (const e of c.entries) {
    const st = entryState(e, { browser: "ready", runner: "ready", web: true });
    assert.ok(READINESS.includes(st.readiness.browser.state) && READINESS.includes(st.readiness.runner.state), e.id);
  }
});

test("entryState: a runner catalog's readiness is used while the runner is connected", () => {
  const doc = stateDoc();
  const runnerDoc = { entries: [{ id: "native.hk_lookup", exec: ["browser", "runner"], state: { implementation: "tested", readiness: { runner: { state: "needs_dataset", requires: [requirement("dataset", { names: ["hkbu_formulas_manual"] })] }, browser: { state: "ready", requires: [] } } } }] };
  const merged = new Catalog(mergeCatalogs(doc, runnerDoc));
  const e = merged.byId["native.hk_lookup"];
  const st = entryState(e, { browser: "ready", runner: "ready", web: true });
  assert.equal(st.readiness.runner.state, "needs_dataset");
  assert.equal(st.readiness.runner.derived, undefined);
  assert.equal(st.readiness.browser.state, "needs_dataset", "the runner's idea of the browser is not taken");
  assert.equal(entryState(e, { runner: "offline" }).readiness.runner.state, "needs_runner", "a runner that went away is not ready");
});

test("merge: the runner's readiness never replaces the browser's; browser compute backends and the browser limit stay", () => {
  const built = { ...testCatalogDoc(), entries: [{ id: "native.k", exec: ["browser", "runner"], state: { implementation: "tested", readiness: { browser: { state: "ready", requires: [] } } }, execution: { browser_compute: { backends: ["webgpu", "wasm-simd", "js"] } }, limits: { max_request_bytes: { browser: 1000, runner: 8388608 } } }] };
  const runner = { entries: [{ id: "native.k", exec: ["browser", "runner"], state: { implementation: "implemented", readiness: { runner: { state: "missing_dependency", requires: [] }, browser: { state: "unsupported", requires: [] } } }, execution: { engines: [{ id: "x" }] }, limits: { max_request_bytes: { browser: 5, runner: 4096 } } }] };
  const e = new Catalog(mergeCatalogs(built, runner)).byId["native.k"];
  assert.deepEqual(e.state.readiness.browser, { state: "ready", requires: [] });
  assert.equal(e.state.readiness.runner.state, "missing_dependency");
  assert.equal(e.state.implementation, "tested");
  assert.deepEqual(e.execution.browser_compute.backends, ["webgpu", "wasm-simd", "js"]);
  assert.deepEqual(e.execution.engines, [{ id: "x" }]);
  assert.deepEqual(e.limits.max_request_bytes, { browser: 1000, runner: 4096 });
  // the document's limits: the same rule
  const docs = mergeCatalogs({ ...built, limits: { max_request_bytes: { runner: 8388608, browser: 33554432 } } }, { ...runner, limits: { max_request_bytes: { runner: 8388608, browser: 1 } } });
  assert.deepEqual(new Catalog(docs).limits, { max_request_bytes: { runner: 8388608, browser: 33554432 } });
  assert.deepEqual(new Catalog(new Catalog(docs).toJSON()).limits, new Catalog(docs).limits);
});

test("merge: an entry's implementation state and its verification come from the same side, so tested ⇔ a level passed", () => {
  const pass = { status: "PASS", levels: { live_upstream: { status: "PASS", record: "data/connector_live_verification.csv" } } };
  const none = { status: "UNVERIFIED", levels: {} };
  const built = { ...testCatalogDoc(), entries: [{ id: "connector.x.y", exec: ["browser", "runner"], state: { implementation: "tested" }, verification: pass }] };
  // a runner whose checkout reads other records (or none): its own pair is not mixed into the built one
  const runner = { entries: [{ id: "connector.x.y", exec: ["browser", "runner"], state: { implementation: "implemented", readiness: { runner: { state: "ready", requires: [] } } }, verification: none }] };
  const e = new Catalog(mergeCatalogs(built, runner)).byId["connector.x.y"];
  assert.equal(e.state.implementation, "tested");
  assert.deepEqual(e.verification, pass);
  assert.equal(e.state.readiness.runner.state, "ready");
  // a built entry without a state of its own takes the runner's pair
  const older = { ...testCatalogDoc(), entries: [{ id: "connector.x.y", exec: ["browser", "runner"], verification: pass }] };
  const o = new Catalog(mergeCatalogs(older, runner)).byId["connector.x.y"];
  assert.equal(o.state.implementation, "implemented");
  assert.deepEqual(o.verification, none);
  for (const m of [e, o]) {
    const passed = Object.values(m.verification.levels).some((l) => l.status === "PASS");
    assert.equal(m.state.implementation === "tested", passed);
  }
});

test("search filters for the catalog page: runtime, readiness, dataset, verification, backend", () => {
  const c = new Catalog(stateDoc());
  const ids = (opts) => c.search("", { limit: 200, ...opts }).map((e) => e.id).sort();
  assert.ok(ids({ where: "browser" }).every((id) => c.byId[id].exec.includes("browser")));
  assert.ok(ids({ where: "runner" }).includes("connector.europepmc.search"));
  assert.deepEqual(ids({ readiness: "needs_dataset", where: "browser", context: { browser: "ready", built: [] } }), ["native.hk_lookup", "tcmdb.query"]);
  assert.deepEqual(ids({ readiness: ["missing_dependency"], context: { runner: "ready", browser: "ready", web: true } }), ["native.dock"]);
  assert.deepEqual(ids({ needsDataset: true }), ["native.hk_lookup", "tcmdb.query"]);
  assert.deepEqual(ids({ needsDataset: "npass" }), ["tcmdb.query"]);
  assert.deepEqual(ids({ needsDataset: "hkbu_formulas_manual" }), ["native.hk_lookup"]);
  assert.ok(!ids({ needsDataset: false }).includes("tcmdb.query"));
  assert.deepEqual(ids({ verified: "live_upstream" }), ["connector.x.live"]);
  assert.deepEqual(ids({ verified: "reference_output" }), ["native.hk_lookup"]);
  assert.deepEqual(ids({ verified: true }), ["connector.x.live", "native.hk_lookup"]);
  assert.ok(ids({ verified: "UNVERIFIED" }).includes("tcmdb.query") && ids({ verified: "UNVERIFIED" }).includes("native.tcm_herb"), "no record is unverified");
  assert.deepEqual(ids({ backend: "webgpu" }), ["native.hk_lookup"]);
  assert.deepEqual(ids({ backend: "esmfold" }), ["job.pipeline.fold"]);
  assert.ok(ids({ backend: "gpu" }).includes("job.pipeline.fold") && ids({ backend: "gpu" }).includes("job.pipeline"));
  assert.equal(c.search("配伍禁忌", { verified: undefined, backend: "" })[0].id, "native.tcm_compatibility", "absent filters change nothing");
});

test("runner_only rule types, as tcmstudio.capability.runner_only reads them: absent, present, is, suffix, not_suffix (a file id not judged)", () => {
  const rules = [
    { arg: "counts", absent: true, why: "absent" },
    { arg: "site_ligand", present: true, why: "present" },
    { arg: "allow_remote", is: true, why: "is" },
    { arg: "molecules", suffix: [".sdf", ".SDF.gz"], why: "suffix" },
    { arg: "receptor", not_suffix: [".pdbqt"], why: "not_suffix" },
  ];
  const entry = { execution: { browser_job: { runner_only: rules } } };
  const met = (args) => runnerOnly(entry, { counts: "c.tsv", ...args });
  assert.deepEqual(runnerOnly(entry, undefined), [], "no arguments, nothing judged");
  assert.deepEqual(runnerOnly(entry, {}), ["absent"]);
  for (const v of [undefined, null, ""]) assert.deepEqual(met({ site_ligand: v }), [], `present: ${JSON.stringify(v)} is not given`);
  for (const v of ["BEN", " ", 0, false, []]) assert.deepEqual(met({ site_ligand: v }), ["present"], `present: ${JSON.stringify(v)} is given`);
  assert.deepEqual(met({ allow_remote: true }), ["is"]);
  assert.deepEqual(met({ allow_remote: "true" }), [], "is: the value itself, not its text");
  assert.deepEqual(met({ molecules: " A.Sdf.GZ " }), ["suffix"]);
  // not_suffix: a name (or any text: PDB:<id>, a SMILES) without the suffix; a name with it, a file id, nothing given
  // or no text is not judged
  for (const v of ["3ptb.pdb", "PDB:3PTB", "UniProt:P00760", "receptor.pdbqt.gz", "CCO"]) assert.deepEqual(met({ receptor: v }), ["not_suffix"], v);
  for (const v of ["r.pdbqt", " R.PDBQT ", "u_0123456789abcdef", "0F8FAD5B-D9CB-469F-A165-70867728950E", "", "  ", null, undefined, 5]) {
    assert.deepEqual(met({ receptor: v }), [], JSON.stringify(v));
  }
  assert.ok(FILE_ID.test("0f8fad5b-d9cb-469f-a165-70867728950e") && FILE_ID.test("u_0123456789abcdef"));
  assert.ok(!FILE_ID.test("u_0123") && !FILE_ID.test("f-1") && !FILE_ID.test("ligands.pdbqt"));
  // several rules met: every reason, in the rules' order
  assert.deepEqual(runnerOnly(entry, { site_ligand: "BEN", allow_remote: true, receptor: "PDB:3PTB" }), ["absent", "present", "is", "not_suffix"]);
});
