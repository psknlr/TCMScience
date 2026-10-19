// The comparison science.mjs --mode gqd holds the browser's research exports to (compare.mjs): identical bytes named so,
// a last-bit difference in a p-value named within tolerance with what it changed downstream, anything else different.
// Then the omics jobs' (--mode omics): compare.mjs's rule for their outputs, omics_native.py's known answer, and what
// science.mjs says when the page gives no result.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  OMICS_RULE, RELATIVE, compareExports, compareJson, compareOmics, compareOmicsTable, compareSignificant, compareTable, compareText,
  omicsBound, omicsFraction, umapAgreement, withoutFigures,
} from "./compare.mjs";
import { lostJob, pageLines } from "./science.mjs";

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

// ------------------------------------------------------------------------------------------------- the omics jobs

const RESULTS = "gene_id\tgene_name\tbase_mean\tlog2_fold_change\tlfc_se\tstat\tp_value\tp_adjusted\n"
  + "G0001\tSIM0001\t812.5\t2.01\t0.2\t10.05\t1e-20\t2.3e-18\n"
  + "G0002\tSIM0002\t40.1\t-0.1\t0.4\t-0.25\t0.8\t0.95\n"
  + "G0003\tSIM0003\t3.2\tNA\tNA\tNA\tNA\tNA\n"
  + "G0004\tSIM0004\t95.5\t1.2\t0.5\t2.4\t0.001\t0.205714\n";

test("the omics rule: 1e-6 + 1e-4·max + one unit in the sixth significant digit, and the share of it a difference uses", () => {
  assert.ok(Math.abs(omicsBound(0.205714, 0.205717) - (1e-6 + 1e-4 * 0.205717 + 1e-6)) < 1e-15);
  assert.ok(Math.abs(omicsBound(1234.5, 1234.6) - (1e-6 + 1e-4 * 1234.6 + 1e-2)) < 1e-12);
  // the last printed digit of a p-value Pyodide moved: a small share of the bound
  const f = omicsFraction(0.205714, 0.205717);
  assert.ok(f > 0.1 && f < 0.2, String(f));
  // near zero the absolute floor holds (a test statistic of 3e-7 against 0)
  assert.ok(omicsFraction(3e-7, 0) < 1);
  // two units in the fourth digit are not within it
  assert.ok(omicsFraction(1, 1.0002) > 1);
  assert.match(OMICS_RULE, /1e-6 \+ 1e-4·max\(\|a\|,\|b\|\)/);
});

test("omics tables: identical, within the rule with the largest share named, or different on any text, NA, flag or header", () => {
  assert.equal(compareOmicsTable(RESULTS, RESULTS).status, "identical");
  const near = compareOmicsTable(RESULTS.replace("0.205714", "0.205717").replace("812.5", "812.501"), RESULTS);
  assert.equal(near.status, "within_tolerance");
  assert.equal(near.numeric, 2);
  assert.equal(near.worst.column, "p_adjusted");
  assert.equal(near.worst.row, 4);
  assert.ok(near.worst.fraction > 0.1 && near.worst.fraction < 0.2);
  assert.ok(Math.abs(near.max_relative - 3e-6 / 0.205717) < 1e-12, "the largest relative difference is the p-value's");
  const far = compareOmicsTable(RESULTS.replace("0.205714", "0.21"), RESULTS);
  assert.equal(far.status, "different");
  assert.deepEqual(far.first[0], { row: 4, column: "p_adjusted", got: "0.21", want: "0.205714" });
  // an NA where the native run has a number, a gene out of order, a flag, a header: never tolerated
  assert.equal(compareOmicsTable(RESULTS.replace("\t0.8\t0.95", "\t0.8\tNA"), RESULTS).status, "different");
  assert.equal(compareOmicsTable(RESULTS.replace("G0001", "G0002").replace("G0002\tSIM0002", "G0001\tSIM0002"), RESULTS).status, "different");
  assert.equal(compareOmicsTable("gene_id\tconverged\nG1\tTrue\n", "gene_id\tconverged\nG1\tFalse\n").status, "different");
  assert.equal(compareOmicsTable(RESULTS.replace("p_adjusted", "padj"), RESULTS).status, "different");
  assert.equal(compareOmicsTable(RESULTS.replace(/\n/g, "\r\n"), RESULTS).status, "different", "other bytes with no cell changed is said, not passed");
  assert.equal(compareOmicsTable(RESULTS + "G0005\tSIM0005\t1\t1\t1\t1\t1\t1\n", RESULTS).status, "different");
});

// a layout of two clusters of 20 cells each, apart: every cell's 15 nearest neighbours are in its own cluster
const LAYOUT = Array.from({ length: 40 }, (_, i) => [(i < 20 ? 0 : 10) + (i % 5) * 0.1, (i < 20 ? 0 : 10) + Math.floor((i % 20) / 5) * 0.1]);
const CLUSTERS = LAYOUT.map((_, i) => (i < 20 ? "0" : "1"));
const CELLS = (layout, clusters = CLUSTERS) => "cell\tsample\tcluster\tcell_type\tumap_1\tumap_2\n"
  + layout.map(([x, y], i) => `s:c${i}\ts\t${clusters[i]}\t${clusters[i] === "0" ? "NK cell" : "B cell"}\t${x}\t${y}`).join("\n") + "\n";
const relabel = (i) => CLUSTERS.map((c, j) => (j === i ? (c === "0" ? "1" : "0") : c));

test("cells.tsv: the UMAP columns are set aside, every other cell still exact", () => {
  const moved = CELLS(LAYOUT.map(([x, y]) => [-y, x + 3]));
  const c = compareOmicsTable(moved, CELLS(LAYOUT), { exclude: ["umap_1", "umap_2"] });
  assert.equal(c.status, "same_apart_from_excluded");
  assert.deepEqual(c.excluded, ["umap_1", "umap_2"]);
  assert.equal(compareOmicsTable(CELLS(LAYOUT, relabel(2)), CELLS(LAYOUT), { exclude: ["umap_1", "umap_2"] }).status, "different");
});

test("the UMAP's structure: the share of each cell's 15 nearest layout neighbours in its cluster, finite coordinates", () => {
  assert.equal(umapAgreement(CELLS(LAYOUT)).agreement, 1);
  // the clusters drawn on top of each other: about half of each cell's neighbours are the other cluster's
  const mixed = umapAgreement(CELLS(LAYOUT.map(([x, y]) => [x % 10, y % 10])));
  assert.ok(mixed.agreement < 0.7, String(mixed.agreement));
  const broken = umapAgreement(CELLS([[0, 0], [NaN, 0], ...LAYOUT.slice(2)]));
  assert.deepEqual([broken.finite, broken.agreement], [false, null]);
  assert.equal(umapAgreement(CELLS(LAYOUT.slice(0, 10), CLUSTERS.slice(0, 10))).agreement, null, "fewer cells than neighbours: no structure to hold");
});

test("significant genes: the same set, a crossing within the rule of alpha named, any other crossing failed", () => {
  assert.equal(compareSignificant(RESULTS, RESULTS, 0.05, "gene_id").status, "same");
  const at = (q) => RESULTS.replace("0.205714", q);
  const near = compareSignificant(at("0.0499998"), at("0.0500003"), 0.05, "gene_id");
  assert.equal(near.status, "flips_near_alpha");
  assert.deepEqual(near.flips.map((f) => [f.gene, f.near]), [["G0004", true]]);
  assert.equal(compareSignificant(at("0.04"), RESULTS, 0.05, "gene_id").status, "different");
  assert.equal(compareSignificant(RESULTS.replace("2.3e-18", "NA"), RESULTS, 0.05, "gene_id").status, "different", "NA is not called");
});

test("report.html is compared with its figures set aside", () => {
  assert.equal(withoutFigures('<p>a</p><svg viewBox="0 0 2 2"><circle r="1.25"/></svg><p>b</p>'), "<p>a</p><svg/><p>b</p>");
});

test("report.html: identical with its figures set aside, or different; a number in its text has no tolerance", () => {
  const run = JSON.stringify({ stages: [], complete: true, outputs: {} });
  const report = (text, figure) => ({ "run.json": run, "report.html": `<p>${text}</p><svg>${figure}</svg>` });
  const row = (b, n) => compareOmics("pipeline.scrna", browserFiles(b), n).find((r) => r.name === "report.html");
  assert.deepEqual([row(report("mixing 1.02530", 1), report("mixing 1.02530", 2)).status, row(report("mixing 1.02530", 1), report("mixing 1.02530", 2)).ok], ["same_apart_from_figures", true]);
  // the last digit of a number it restates, well within OMICS_RULE: still a difference
  const near = row(report("mixing 1.02531", 1), report("mixing 1.02530", 2));
  assert.deepEqual([near.status, near.ok, near.fraction], ["different", false, undefined]);
  assert.match(near.why, /with each inline figure \(<svg>\) set aside.*1\.02531.*1\.02530/);
});

test("a figure that is not the same bytes: its own reason, naming the table that holds what it draws only where one does", () => {
  const run = JSON.stringify({ stages: [], complete: true, outputs: {} });
  const figures = ["ma", "volcano", "p_values", "dispersion", "pca", "sample_distances", "qc_genes", "qc_mito", "markers", "pca_variance", "paga", "umap_clusters", "heatmap_of_something_new"];
  const files = (d) => Object.fromEntries([["run.json", run], ...figures.map((f) => [`plots/${f}.svg`, `<svg>${d}</svg>`])]);
  const by = Object.fromEntries(compareOmics("pipeline.rnaseq", browserFiles(files("a")), files("b")).map((r) => [r.name, r]));
  const why = (f) => by[`plots/${f}.svg`].why;
  for (const f of figures) assert.deepEqual([by[`plots/${f}.svg`].status, by[`plots/${f}.svg`].counted], ["not_compared", false], f);
  for (const f of ["ma", "volcano", "p_values"]) assert.match(why(f), /is compared in deseq2_results\.tsv/, f);
  assert.match(why("dispersion"), /compared in de_diagnostics\.tsv/);
  for (const f of ["qc_genes", "qc_mito"]) assert.match(why(f), /is compared in cells\.tsv/, f);
  assert.match(why("markers"), /markers\.tsv/);
  assert.match(why("markers"), /not compared/, "a marker's share in the other clusters is in no table");
  for (const f of ["pca", "sample_distances", "pca_variance", "heatmap_of_something_new"]) {
    assert.match(why(f), /in no table and is not compared|is not compared here/, f);
    assert.doesNotMatch(why(f), /compared in (the tables|\w+\.tsv)/, f);
  }
  assert.match(why("sample_distances"), /only its input, vst\.tsv, is/);
  assert.match(why("paga"), /UMAP/);
  assert.match(why("paga"), /paga\.tsv/);
  assert.match(why("umap_clusters"), /the layout's structure is checked below/);
  assert.ok(!Object.values(by).some((r) => /the numbers it draws are compared in the tables/.test(r.why)), "no figure is said to be compared in 'the tables'");
});

/** A tiny pipeline.rnaseq or pipeline.scrna output directory, text by path. */
function omicsRun(kind, { results = RESULTS, svg = "<svg><path d=\"M0 0\"/></svg>", cells = CELLS(LAYOUT) } = {}) {
  const run = kind === "pipeline.rnaseq"
    ? { stages: [{ name: "validate_counts", status: "succeeded", check: "PASS" }], complete: true, outputs: { "deseq2_results.tsv": "x", "report.html": "y" }, differential_expression: { alpha: 0.05 } }
    : { stages: [{ name: "verify", status: "succeeded", check: "PASS" }], complete: true, outputs: { "cells.tsv": "x" }, pseudobulk: { tests: { "B cell": { alpha: 0.05 } } } };
  const files = { "run.json": JSON.stringify(run), "report.html": `<h1>Report</h1>${svg}<p>90 genes</p>`, "plots/ma.svg": svg };
  if (kind === "pipeline.rnaseq") files["deseq2_results.tsv"] = results;
  else Object.assign(files, { "cells.tsv": cells, "pseudobulk/B_cell.tsv": results.replace("gene_id\tgene_name", "gene\tgene_name"), "clusters.tsv": "cluster\tcells\tcell_type\tconfidence\ttop_markers\n0\t3\tNK cell\t0.9\tGNLY\n1\t3\tB cell\t0.8\tMS4A1\n" });
  return files;
}

test("an omics run: tables within the rule, figures not compared and not counted, the discrete results exact", () => {
  const native = omicsRun("pipeline.rnaseq");
  const browser = browserFiles(omicsRun("pipeline.rnaseq", { results: RESULTS.replace("0.205714", "0.205717"), svg: "<svg><path d=\"M0 0.01\"/></svg>" }));
  const rows = compareOmics("pipeline.rnaseq", browser, Object.fromEntries(Object.entries(native).map(([k, v]) => [k, Buffer.from(v)])));
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.ok(rows.every((r) => r.ok), JSON.stringify(rows.filter((r) => !r.ok)));
  assert.equal(by["deseq2_results.tsv"].status, "within_tolerance");
  assert.match(by["deseq2_results.tsv"].why, /not identical bytes.*the largest at 0\.1\d* of the bound \(row 4, p_adjusted: 0\.205717 vs 0\.205714\).*rule \|a − b\|/);
  assert.deepEqual([by["plots/ma.svg"].status, by["plots/ma.svg"].counted], ["not_compared", false]);
  assert.equal(by["report.html"].status, "same_apart_from_figures");
  assert.equal(by["genes called (p_adjusted < alpha)"].status, "same");
  assert.equal(by["run.json: stages"].status, "same");
  assert.ok(!("run.json" in by), "the run records are compared by their fields, not their bytes");
  // a gene called on one side only, far from alpha, fails; so does a file the browser did not write
  const flipped = compareOmics("pipeline.rnaseq", browserFiles(omicsRun("pipeline.rnaseq", { results: RESULTS.replace("0.205714", "0.01") })), native);
  assert.equal(flipped.find((r) => r.name === "genes called (p_adjusted < alpha)").ok, false);
  const gone = browserFiles(native);
  delete gone["deseq2_results.tsv"];
  assert.equal(compareOmics("pipeline.rnaseq", gone, native).find((r) => r.name === "deseq2_results.tsv").status, "missing");
});

test("a single-cell run: each cell's cluster and type and the clusters table exact, the UMAP held to its structure", () => {
  const native = omicsRun("pipeline.scrna");
  const moved = browserFiles(omicsRun("pipeline.scrna", { cells: CELLS(LAYOUT.map(([x, y]) => [x + 0.5, y - 0.25])) }));
  const rows = compareOmics("pipeline.scrna", moved, native);
  const by = Object.fromEntries(rows.map((r) => [r.name, r]));
  assert.ok(rows.every((r) => r.ok), JSON.stringify(rows.filter((r) => !r.ok)));
  assert.equal(by["cells.tsv"].status, "same_apart_from_excluded");
  assert.equal(by["each cell's cluster and cell type"].status, "same");
  assert.equal(by["B cell pseudobulk: genes called (p_adjusted < alpha)"].status, "same");
  assert.equal(by["UMAP layout: its structure"].status, "structure_held");
  assert.match(by["UMAP layout: its structure"].why, /browser 1, native 1 .*the coordinates are not compared/);
  // a cell moved to the other cluster: its cells.tsv row and the per-cell row say so
  const relabelled = browserFiles(omicsRun("pipeline.scrna", { cells: CELLS(LAYOUT, relabel(2)) }));
  const bad = Object.fromEntries(compareOmics("pipeline.scrna", relabelled, native).map((r) => [r.name, r]));
  assert.equal(bad["each cell's cluster and cell type"].ok, false);
  assert.match(bad["each cell's cluster and cell type"].why, /from row 3/);
  assert.equal(bad["cells.tsv"].ok, false);
  // a layout whose clusters run into each other has lost its structure, whatever its coordinates
  const smeared = browserFiles(omicsRun("pipeline.scrna", { cells: CELLS(LAYOUT.map(([x, y]) => [x % 10, y % 10])) }));
  assert.equal(compareOmics("pipeline.scrna", smeared, native).find((r) => r.name === "UMAP layout: its structure").ok, false);
});

// ------------------------------------------------------------------- the known answer (omics_native.py values)

const OMICS_NATIVE = path.join(path.dirname(fileURLToPath(import.meta.url)), "omics_native.py");
/** A Python that can run omics_native.py values (bioagent, numpy, scipy, pytest), or null (CI's web step has one). */
const KNOWN_ANSWER_PYTHON = [process.env.PYTHON, "python3"].filter(Boolean).find((py) => {
  try { execFileSync(py, ["-c", "import bioagent.omics.scrna, numpy, scipy, pytest"], { stdio: "ignore", timeout: 60000 }); return true; } catch { return false; }
}) ?? null;

/**
 * A tiny pipeline.scrna output directory that meets sc_world's known answer, with its truth: two samples of five cells
 * of each of sc_world's four types, a path cell and a doublet each, clustered by type; `rename` changes the ids of one
 * sample's cells in cells.tsv only (as a table reader that dropped Cell Ranger's "-1" would) → {work, run}.
 */
function scKnownAnswer(dir, { rename = false } = {}) {
  const kinds = ["B", "Mono", "NK", "Plt"];
  const cells = ["ctrl1", "trt1"].flatMap((s) => [...kinds.flatMap((k) => Array.from({ length: 5 }, () => k)), "path", "doublet"]
    .map((kind, i) => ({ id: `${s}:CELL${String(i).padStart(5, "0")}-1`, sample: s, kind })));
  const types = Object.fromEntries(cells.map((c) => [c.id, c.kind]));
  const genes = Array.from({ length: 12 }, (_, i) => `GENE${String(i).padStart(4, "0")}`);
  const work = path.join(dir, "work");
  const run = path.join(dir, "run");
  mkdirSync(path.join(run, "pseudobulk"), { recursive: true });
  mkdirSync(work, { recursive: true });
  writeFileSync(path.join(work, "truth.json"), JSON.stringify({ scrna: { types, condition_genes: genes } }));
  const cluster = { B: 0, Mono: 1, NK: 2, Plt: 3, path: 2, doublet: 0 };
  writeFileSync(path.join(run, "cells.tsv"), "cell\tsample\tcluster\n"
    + cells.map((c) => `${rename && c.sample === "ctrl1" ? c.id.replace(/-1$/, "") : c.id}\t${c.sample}\t${cluster[c.kind]}\n`).join(""));
  writeFileSync(path.join(run, "clusters.tsv"), "cluster\tcells\tcell_type\n0\t11\tB cell\n1\t10\tCD14+ monocyte\n2\t11\tNK cell\n3\t10\tPlatelet\n");
  const table = (q) => "gene\tlog2_fold_change\tp_adjusted\n" + genes.map((g) => `${g}\t1.5\t${q}\n`).join("");
  writeFileSync(path.join(run, "pseudobulk", "B_cell.tsv"), table(0.001));
  writeFileSync(path.join(run, "pseudobulk", "NK_cell.tsv"), table(0.9));
  writeFileSync(path.join(run, "run.json"), JSON.stringify({
    config: { analysis_backend: "builtin", integration_method: "harmony", de_backend: "builtin", n_top_genes: 600, seed: 0 },
    integration: { mixing_after: 0.95 }, pseudobulk: { tests: { "B cell": { alpha: 0.05 }, "NK cell": { alpha: 0.05 } } }, complete: true,
  }));
  return { work, run };
}

test("the single-cell known answer: every cell of cells.tsv must be one of sc_world's, not only the ones the ARI keeps", { skip: KNOWN_ANSWER_PYTHON ? false : "no Python with bioagent, numpy, scipy and pytest ($PYTHON or python3)" }, () => {
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-known-answer-"));
  try {
    const values = ({ work, run }) => JSON.parse(execFileSync(KNOWN_ANSWER_PYTHON, [OMICS_NATIVE, "values", work, "scrna", run], { encoding: "utf8" }).trim().split("\n").at(-1)).checks;
    const whole = values(scKnownAnswer(path.join(dir, "whole")));
    assert.ok(whole.every((c) => c.ok), JSON.stringify(whole.filter((c) => !c.ok)));
    assert.match(whole.find((c) => /^cluster ARI/.test(c.label)).detail, /^1\.0000 over 40 cells of the four types/);
    // one sample's ids changed on both sides alike: the ARI over the other sample's cells is still 1, and that is not a pass
    const renamed = values(scKnownAnswer(path.join(dir, "renamed"), { rename: true }));
    const every = renamed.find((c) => /every cell of cells\.tsv is one of sc_world's/.test(c.label));
    assert.ok(every && !every.ok, JSON.stringify(renamed));
    assert.match(every.detail, /^22 of 44 are not: ctrl1:CELL00000, /);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// ------------------------------------------------------------- science.mjs: a page that gave no result, or failed

test("no result from the page: its failures, the rows it had shown and its console are said, and each job says why it has nothing", () => {
  // what runPage returns when __result never came (here the omics page stopped by --timeout-min 0.05)
  const timedOut = {
    ok: false, failures: ["no result within 3000 ms: TimeoutError: page.waitForFunction: Timeout 3000ms exceeded."],
    partial: "private storage (OPFS)\tok\t\t\nthe inputs are a project's files\tok\tcounts.tsv (66844 bytes)\t88\njob service started\tok\tready\t",
    console: ["error: Failed to load resource: net::ERR_CONNECTION_RESET", "pageerror: Error: the worker did not boot"], page_ms: 3100,
  };
  const lines = pageLines(timedOut);
  assert.equal(lines[0], "FAILED page: no result within 3000 ms: TimeoutError: page.waitForFunction: Timeout 3000ms exceeded.");
  assert.ok(lines.includes("         job service started | ok | ready"), lines.join("\n"));
  assert.ok(lines.some((l) => /pageerror: Error: the worker did not boot/.test(l)), lines.join("\n"));
  for (const kind of ["pipeline.rnaseq", "pipeline.scrna"]) assert.match(lostJob(timedOut, undefined, kind), /^no result came back from the page \(no result within 3000 ms/);
  // a long console is bounded
  assert.ok(pageLines({ ...timedOut, console: Array.from({ length: 60 }, (_, i) => `log: ${i}`) }).length < 60);
  // a page that came back: its checks say it all, and a job's rows say what is missing of it
  const failed = { ok: false, checks: [{ label: "pipeline.scrna as a browser job", ok: false, detail: "worker lost" }], failures: ["pipeline.scrna as a browser job: worker lost"], console: [] };
  assert.deepEqual(pageLines(failed), ["page   nothing in its console"]);
  assert.match(lostJob(failed, undefined, "pipeline.rnaseq"), /^the page's result has no pipeline\.rnaseq job/);
  assert.match(lostJob(failed, { state: "failed", error: { message: "worker lost" } }, "pipeline.scrna"), /^the pipeline\.scrna job ended failed in the browser \(worker lost\); its outputs were not read back/);
  // past its wait the page records the job as it is: one still running has not ended, and is not said to have
  assert.match(lostJob(failed, { state: "running", error: null }, "pipeline.rnaseq"), /^the pipeline\.rnaseq job had not ended when the page stopped waiting for it \(still running\); its outputs were not read back/);
  assert.match(lostJob(failed, { state: undefined, error: null }, "pipeline.rnaseq"), /had not ended .*\(no state recorded\)/);
  assert.equal(lostJob({ ok: true, checks: [] }, { state: "succeeded", files: {} }, "pipeline.scrna"), null);
  assert.deepEqual(pageLines({ ok: true, checks: [] }), []);
});
