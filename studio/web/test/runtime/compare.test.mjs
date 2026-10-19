// The comparison science.mjs --mode gqd holds the browser's research exports to (compare.mjs): identical bytes named so,
// a last-bit difference in a p-value named within tolerance with what it changed downstream, anything else different.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { RELATIVE, compareExports, compareJson, compareTable, compareText } from "./compare.mjs";

const sha = (text) => `sha256:${createHash("sha256").update(Buffer.from(text)).digest("hex")}`;
const ENRICH = "pathway\tp_value\tq_value\nR-HSA-1\t0.024699668194706335\t0.5\nR-HSA-2\t1\t1\n";
// the same p-value one last bit away, as Pyodide's math.exp can write it
const ENRICH_ULP = "pathway\tp_value\tq_value\nR-HSA-1\t0.02469966819470634\t0.5\nR-HSA-2\t1\t1\n";

function world(enrichment) {
  const manifest = (digest) => JSON.stringify({ execution: { audit_head: digest }, result_digest: sha(enrichment), outputs: { "enrichment.tsv": { bytes: enrichment.length, sha256: sha(enrichment) } }, parameters: { permutations: 1000 } });
  return {
    "compounds.tsv": "a\tb\n1\tx\n", "targets.tsv": "t\n", "disease_targets.tsv": "d\n", "enrichment.tsv": enrichment, "network.tsv": "n\n",
    "provenance.json": JSON.stringify({ result_digest: sha(enrichment), edges: 624 }), "limitations.md": "237 of the 391 assayed proteins are potent hits.\n",
    "report.html": `<p>${enrichment.length}</p>`, manifest,
  };
}

function exportsOf(w, head) {
  const files = { ...w, "run_manifest.json": w.manifest(head) };
  delete files.manifest;
  return files;
}

const browserFiles = (files) => Object.fromEntries(Object.entries(files).map(([k, v]) => [k, { sha256: sha(v), text: v }]));
const nativeOf = (files) => (name) => (name in files ? Buffer.from(files[name]) : null);

test("cells: identical, within a relative 1e-12 (a last-bit p-value), or different with where", () => {
  assert.deepEqual(compareTable(ENRICH, ENRICH), { status: "identical" });
  const near = compareTable(ENRICH_ULP, ENRICH);
  assert.equal(near.status, "within_tolerance");
  assert.ok(near.max_relative > 0 && near.max_relative <= RELATIVE);
  const far = compareTable(ENRICH.replace("0.5", "0.51"), ENRICH);
  assert.equal(far.status, "different");
  assert.deepEqual(far.first[0], { row: 1, column: 2, got: "0.51", want: "0.5" });
  assert.equal(compareTable("a\tb\n", "a\tb\n1\t2\n").status, "different");
  assert.equal(compareTable("x\ty\n", "x\tz\n").status, "different", "text cells are never tolerated");
});

test("json and text: numbers within tolerance, skipped paths set aside, everything else exact", () => {
  assert.equal(compareJson({ a: 1, b: [1, 2] }, { a: 1 + 1e-15, b: [1, 2] }).same, true);
  assert.equal(compareJson({ a: 1, b: [1, 2] }, { a: 1 + 1e-15, b: [1, 2] }, [], 0).same, false);
  assert.deepEqual(compareJson({ a: { x: 1, y: "s" } }, { a: { x: 1, y: "t" } }).first, ["a.y"]);
  assert.equal(compareJson({ o: { "e.tsv": { sha256: "1" } } }, { o: { "e.tsv": { sha256: "2" } } }, ["o.*.sha256"]).same, true);
  assert.equal(compareText("p = 0.024699668194706335 here", "p = 0.02469966819470634 here").same, true);
  assert.equal(compareText("237 of 391", "236 of 391").same, false);
});

test("exports: identical bytes everywhere are identical; the manifest is the same apart from its execution block", () => {
  const w = world(ENRICH);
  const rows = compareExports(browserFiles(exportsOf(w, "head-browser")), nativeOf(exportsOf(w, "head-native")));
  assert.ok(rows.every((r) => r.ok), JSON.stringify(rows));
  assert.deepEqual(rows.filter((r) => r.status !== "identical").map((r) => [r.name, r.status]), [["run_manifest.json", "same_apart_from_execution"]]);
});

test("exports: a last-bit p-value is within tolerance, and so are the digests that restate it — named, never identical", () => {
  const rows = compareExports(browserFiles(exportsOf(world(ENRICH_ULP), "b")), nativeOf(exportsOf(world(ENRICH), "n")));
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.equal(by["enrichment.tsv"].status, "within_tolerance");
  assert.match(by["enrichment.tsv"].why, /not identical bytes/);
  for (const name of ["provenance.json", "run_manifest.json"]) {
    assert.equal(by[name].status, "within_tolerance", name);
    assert.match(by[name].why, /set aside because enrichment\.tsv is within tolerance: result_digest/);
  }
  assert.equal(by["report.html"].ok, true, "report.html is reported, not counted");
  assert.ok(rows.every((r) => r.ok));
});

test("exports: a real difference fails, and a restating file may not hide another change behind the tolerance", () => {
  const changed = world(ENRICH.replace("\t1\t1", "\t0.9\t1"));
  const rows = compareExports(browserFiles(exportsOf(changed, "b")), nativeOf(exportsOf(world(ENRICH), "n")));
  assert.equal(rows.find((r) => r.name === "enrichment.tsv").ok, false);
  // within tolerance in the table, but the provenance also changed a count: that is not set aside
  const near = exportsOf(world(ENRICH_ULP), "b");
  near["provenance.json"] = JSON.stringify({ result_digest: sha(ENRICH_ULP), edges: 623 });
  const rows2 = compareExports(browserFiles(near), nativeOf(exportsOf(world(ENRICH), "n")));
  const prov = rows2.find((r) => r.name === "provenance.json");
  assert.equal(prov.status, "different");
  assert.match(prov.why, /edges/);
  // and nothing written is missing, not passed
  const gone = exportsOf(world(ENRICH), "b");
  delete gone["network.tsv"];
  assert.equal(compareExports(browserFiles(gone), nativeOf(exportsOf(world(ENRICH), "n"))).find((r) => r.name === "network.tsv").status, "missing");
});
