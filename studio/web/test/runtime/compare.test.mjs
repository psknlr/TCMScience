// The comparison science.mjs --mode gqd holds the browser's research exports to (compare.mjs): identical bytes named so,
// a last-bit difference in a p-value named within tolerance with what it changed downstream, anything else different.
// Then the omics jobs' (--mode omics): compare.mjs's rule for their outputs, omics_native.py's known answer, and what
// science.mjs says when the page gives no result. Then the ADMET jobs' (--mode admet): exact, with what is set aside
// named (compareAdmet). Then the docking job's (--mode dock): the plan, the analysis and Vina's energy model exact, the
// searches held to what does not depend on their last bits (compareDock).
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  ADMET_ULP_DESCRIPTORS, ADMET_VALUE_ULPS, CARD_SET_ASIDE, DOCK_RUN_SET_ASIDE, DOCK_SEARCH_BAND, OMICS_RULE, RELATIVE, admetTransform, compareAdmet,
  compareDescriptors, compareDock, compareExports, compareJson, compareOmics, compareOmicsTable, compareSignificant, compareTable, compareText,
  omicsBound, omicsFraction, parseHexFloat, ulps, umapAgreement, withoutFigures,
} from "./compare.mjs";
import { dockPlan, firstPose, float64Bits } from "./dock-plan.mjs";
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

// ---------------------------------------------------------------------------- the ADMET jobs (science.mjs --mode admet)

const hex = (x) => x.toString(16);
/** A consistent browser/native pair as checkAdmet hands them to compareAdmet: one endpoint, two molecules kept, one
 * refused by the browser chemistry (MetalDisconnector), one SMILES that is not one. */
function admetWorld() {
  const card = {
    endpoint: "caco2_wang", metric: "mae", test_score: 0.1328, train: 80, test: 25, iterations: 194, dropped_unreadable: 0,
    dropped_unstandardisable: 0, sklearn: "1.8.0", rdkit: "2026.09.1", model_file: "caco2_wang.pkl", domain_file: "caco2_wang_domain.npy",
    model_sha256: "a".repeat(64), domain_sha256: "d".repeat(64), seconds: 1.4, chemistry: { engine: "rdkit", version: "2026.09.1", detail: "RDKit 2026.09.1 (Python)" },
  };
  const bcard = { ...card, model_sha256: "b".repeat(64), seconds: 3.8, chemistry: { engine: "rdkit.js", version: "2026.09.1", detail: "RDKit.js 2026.09.1 MinimalLib + bioagent ports 1" } };
  const exact = (vals) => ({ value: vals.map(hex), in_domain: vals.map(() => true), arg: vals.map((_, i) => i), inter: vals.map(() => 10), union: vals.map(() => 20) });
  const chem = (qed) => ({ features_sha256: "f", bits_sha256: "b", canonical: "CCO", atoms_with_h: 9, descriptors: { MolLogP: -0.0014, qed }, alerts: [["BRENK", "x"]] });
  const tsv = "name\tsmiles\tcaco2_wang\naspirin\tCC\t-5.285\ncaffeine\tCN\t-5.568\n";
  const md = "# ADMET prediction\n\n- 1 molecules could not be read: bad: 'C1CC' is not a valid SMILES\n";
  const bmd = `${md}- 1 molecules were refused by RDKit.js 2026.09.1 MinimalLib + bioagent ports 1: cisplatin: needs MetalDisconnector\n`;
  const stages = [{ name: "standardize", status: "succeeded" }, { name: "verify", status: "succeeded", check: "PASS" }];
  const model = { caco2_wang: { metric: "mae", test_score: 0.1328, model_sha256: "x", chemistry: {} } };
  const runJson = (refused, read, sim) => JSON.stringify({ stages, complete: true, models: model, read, failures: { bad: "'C1CC' is not a valid SMILES" }, refused, similarity: sim });
  const why = "standardisation needs RDKit's MetalDisconnector, which RDKit.js does not have";
  const files = (sim) => Object.fromEntries(Object.entries({ "admet.tsv": tsv, "report.md": bmd, "run.json": runJson({ cisplatin: why }, ["aspirin", "caffeine"], sim) })
    .map(([k, v]) => [k, { sha256: sha(v), text: v }]));
  const compute = (backend, extra = {}) => [{ backend, accelerated: null, ...extra }];
  const gpuFacts = { forced: "webgpu", gpu: { adapter: { kind: "software", vendor: "google", architecture: "swiftshader" }, validated: true }, basis: "forced" };
  const run = (sim, backend, extra) => ({
    ok: true, files: files(sim), molecules: { names: ["aspirin", "caffeine", "cisplatin", "bad"] }, names: ["aspirin", "caffeine"],
    chemistry: { rows: { aspirin: chem(0.55), caffeine: chem(0.54) }, not_prepared: { cisplatin: { error: why, need: "runner", missing: "MetalDisconnector" }, bad: { error: "'C1CC' is not a valid SMILES", need: null } } },
    similarity: { doc: { backend: sim.backend }, endpoints: { caco2_wang: { sha256: "s", arg: [0, 1], inter: [10, 10], union: [20, 20] } } },
    stages: [{ id: "domain", compute: compute(backend, extra) }], receipt: { device: { used: backend === "webgpu" ? "cpu+webgpu" : "cpu", evidence: "verified" } },
  });
  const rechem = { caco2_wang: { train_val: { n: 80, refused: [], unreadable: [] }, test: { n: 25, refused: [], unreadable: [] } } };
  return {
    browser: {
      models: { cards: { caco2_wang: bcard }, domains: { caco2_wang: { sha256: "d".repeat(64), bytes: 20608 } } }, rechem,
      pipeline: run({ backend: "wasm-simd", checked: { caco2_wang: 2 }, rejected: null }, "wasm-simd"),
      forced: run({ backend: "webgpu", checked: { caco2_wang: 2 }, rejected: null }, "webgpu", gpuFacts),
      probe: { sets: { molecules: { ok: [true, true, false, false], endpoints: { caco2_wang: exact([-5.285, -5.568]) } }, "test:caco2_wang": { ok: [true, true], endpoints: { caco2_wang: exact([1.5, 2.5]) } } } },
    },
    native: {
      cards: { caco2_wang: card }, tests: { caco2_wang: { n: 2, ok: [true, true], endpoints: { caco2_wang: exact([1.5, 2.5]) } } },
      molecules: { names: ["aspirin", "caffeine", "cisplatin"], endpoints: { caco2_wang: exact([-5.285, -5.568, -4]) }, chemistry: { rows: { aspirin: chem(0.55), caffeine: chem(0.54), cisplatin: chem(0.3) } } },
    },
    nativeFiles: {
      kept: { "admet.tsv": Buffer.from(tsv), "report.md": Buffer.from(md), "run.json": Buffer.from(runJson({}, ["aspirin", "caffeine"], { backend: "cpu" })) },
      runner: { "run.json": Buffer.from(runJson({}, ["aspirin", "caffeine", "cisplatin"], { backend: "cpu" })) },
    },
    expected: { refused: { cisplatin: "MetalDisconnector" }, unreadable: { bad: "'C1CC' is not a valid SMILES" } },
  };
}
const rowOf = (rows, name) => rows.find((r) => r.name === name);
const failing = (rows) => rows.filter((r) => !r.ok).map((r) => `${r.name}: ${r.why}`);

test("admet: a consistent run passes, each row saying what it compared and what it set aside", () => {
  const rows = compareAdmet(admetWorld());
  assert.deepEqual(failing(rows), []);
  assert.equal(rowOf(rows, "caco2_wang: model card").status, "same_apart_from_excluded");
  assert.match(rowOf(rows, "caco2_wang: model card").why, new RegExp(CARD_SET_ASIDE.join(", ")));
  assert.equal(rowOf(rows, "caco2_wang: model file").counted, false, "a pickle's bytes are never counted");
  assert.equal(rowOf(rows, "caco2_wang: test-set predictions (full precision)").status, "identical");
  assert.equal(rowOf(rows, "pipeline: admet.tsv").status, "identical");
  assert.equal(rowOf(rows, "pipeline: report.md").status, "same_apart_from_refused");
  assert.equal(rowOf(rows, "pipeline: molecules the browser refused").status, "same");
  assert.equal(rowOf(rows, "pipeline: caco2_wang predictions (full precision)").status, "identical");
  assert.equal(rowOf(rows, "pipeline: caco2_wang domain search (the browser's kernels)").status, "identical");
  assert.equal(rowOf(rows, "forced GPU: every output").status, "identical");
  assert.equal(rowOf(rows, "forced GPU: the domain stage's compute facts").status, "same");
});

test("admet cards: only the process's own fields are set aside; a score, a count or an iteration is not", () => {
  for (const [k, v] of [["test_score", 0.1329], ["train", 79], ["iterations", 195], ["dropped_unstandardisable", 1], ["sklearn", "1.7.2"]]) {
    const w = admetWorld();
    w.browser.models.cards.caco2_wang[k] = v;
    const r = rowOf(compareAdmet(w), "caco2_wang: model card");
    assert.equal(r.status, "different", k);
    assert.match(r.why, new RegExp(`^differs at ${k}`));
  }
  const w = admetWorld();
  w.browser.models.domains.caco2_wang.sha256 = "e".repeat(64);
  assert.equal(rowOf(compareAdmet(w), "caco2_wang: applicability domain (its training molecules' fingerprints)").status, "different");
});

test("admet predictions and domain: exact at full precision, molecule by molecule", () => {
  let w = admetWorld();
  // one unit in the last place of one prediction
  w.browser.probe.sets.molecules.endpoints.caco2_wang.value[1] = hex(-5.568 + 2 ** -50);
  assert.equal(rowOf(compareAdmet(w), "pipeline: caco2_wang predictions (full precision)").status, "different");
  w = admetWorld();
  w.browser.probe.sets["test:caco2_wang"].endpoints.caco2_wang.inter[0] = 11;
  assert.equal(rowOf(compareAdmet(w), "caco2_wang: test-set predictions (full precision)").status, "different");
  w = admetWorld();
  w.browser.pipeline.similarity.endpoints.caco2_wang.arg = [1, 1];
  assert.equal(rowOf(compareAdmet(w), "pipeline: caco2_wang domain search (the browser's kernels)").status, "different");
  w = admetWorld();
  w.browser.probe.sets["test:caco2_wang"].ok = [true, false];
  assert.equal(rowOf(compareAdmet(w), "caco2_wang: test-set predictions (full precision)").status, "different", "another set of molecules kept");
});

test("admet predictions through the C library's exp or pow: the raw output exact, the value within its last bits, named", () => {
  assert.equal(parseHexFloat("0x1.441230517005fp+6"), 81.01776244398341);
  assert.equal(parseHexFloat("-0x1.8p+1"), -3);
  assert.equal(ulps(parseHexFloat("0x1.441230517005fp+6"), parseHexFloat("0x1.441230517005ep+6")), 1);
  assert.equal(admetTransform({ task: "classification" }), "exp");
  assert.equal(admetTransform({ task: "regression", log10_target: true }), "pow");
  assert.equal(admetTransform({ task: "regression", log10_target: false }), null);
  const world = (task, log10) => {
    const w = admetWorld();
    for (const c of [w.native.cards.caco2_wang, w.browser.models.cards.caco2_wang]) Object.assign(c, { task, log10_target: log10 });
    for (const set of [w.browser.probe.sets["test:caco2_wang"], w.native.tests.caco2_wang]) set.endpoints.caco2_wang.raw = ["0x1.8p+0", "0x1.4p+1"];
    return w;
  };
  const name = "caco2_wang: test-set predictions (full precision)";
  // 1.5 is 0x1.8p+0, and 0x1.8000000000001p+0 one unit above it
  const w1 = world("regression", true);
  w1.browser.probe.sets["test:caco2_wang"].endpoints.caco2_wang.value[0] = "0x1.8000000000001p+0";
  w1.native.tests.caco2_wang.endpoints.caco2_wang.value[0] = "0x1.8p+0";
  w1.browser.probe.sets["test:caco2_wang"].endpoints.caco2_wang.value[1] = w1.native.tests.caco2_wang.endpoints.caco2_wang.value[1];
  const r = rowOf(compareAdmet(w1), name);
  assert.equal(r.status, "within_tolerance", r.why);
  assert.match(r.why, /raw output .* identical; 1 prediction differs by at most 1 unit .*the power in 10 \*\* \(a log10 target\): numpy's AVX-512 power or glibc's pow natively, Emscripten's pow in Pyodide .*not identical$/);
  // a regression on its own scale has no C library call: exact
  const w2 = world("regression", false);
  w2.browser.probe.sets["test:caco2_wang"].endpoints.caco2_wang.value[0] = "0x1.8000000000001p+0";
  w2.native.tests.caco2_wang.endpoints.caco2_wang.value[0] = "0x1.8p+0";
  assert.equal(rowOf(compareAdmet(w2), name).status, "different");
  // the raw output differs: the model differs, whatever the value
  const w3 = world("classification", false);
  w3.browser.probe.sets["test:caco2_wang"].endpoints.caco2_wang.raw[0] = "0x1.8000000000001p+0";
  assert.equal(rowOf(compareAdmet(w3), name).status, "different");
  // beyond the bound
  const w4 = world("classification", false);
  w4.browser.probe.sets["test:caco2_wang"].endpoints.caco2_wang.value[0] = `0x1.${(2 ** 51 + ADMET_VALUE_ULPS + 1).toString(16)}p+0`;
  w4.native.tests.caco2_wang.endpoints.caco2_wang.value[0] = "0x1.8000000000000p+0";
  assert.equal(rowOf(compareAdmet(w4), name).status, "different");
});

test("admet refusals: the ones the set expects, for their step, each read by the runner; training refusals held to the card", () => {
  let w = admetWorld();
  w.browser.pipeline.chemistry.not_prepared.cisplatin.missing = "Reionizer";
  assert.equal(rowOf(compareAdmet(w), "pipeline: molecules the browser refused").status, "different");
  w = admetWorld();
  w.nativeFiles.runner["run.json"] = w.nativeFiles.kept["run.json"];
  assert.match(rowOf(compareAdmet(w), "pipeline: molecules the browser refused").why, /the runner's own run did not read cisplatin/);
  w = admetWorld();
  w.expected.refused = {};
  assert.match(rowOf(compareAdmet(w), "pipeline: molecules the browser refused").why, /cisplatin: refused, not expected/);
  // training: none expected of the known-answer archive; a real archive's are reported, and held to the card
  w = admetWorld();
  w.browser.rechem.caco2_wang.train_val.refused = [["[Na]OC(=O)C", "needs MetalDisconnector", "MetalDisconnector"]];
  assert.equal(rowOf(compareAdmet(w), "caco2_wang: training molecules the browser chemistry refused").status, "different");
  w.browser.models.cards.caco2_wang.dropped_unstandardisable = 1;
  assert.match(rowOf(compareAdmet(w), "caco2_wang: training molecules the browser chemistry refused").why, /none expected for the known-answer archive/);
  const real = rowOf(compareAdmet({ ...w, real: true }), "caco2_wang: training molecules the browser chemistry refused");
  assert.equal(real.status, "reported");
  assert.match(real.why, /1 refused \(MetalDisconnector: 1\)/);
  w.browser.models.cards.caco2_wang.dropped_unstandardisable = 0;
  assert.match(rowOf(compareAdmet({ ...w, real: true }), "caco2_wang: training molecules the browser chemistry refused").why, /not the card's counts/);
});

test("admet reports: only the lines naming the refused molecules are set aside", () => {
  const w = admetWorld();
  const other = w.browser.pipeline.files["report.md"].text.replace("prediction", "Prediction");
  w.browser.pipeline.files["report.md"] = { sha256: sha(other), text: other };
  assert.equal(rowOf(compareAdmet(w), "pipeline: report.md").status, "different");
});

test("admet descriptors: QED's float64 may differ in its last bits, named and equal as float32; nothing else may", () => {
  assert.equal(ulps(0.8215995486924975, 0.8215995486924976), 1);
  assert.equal(ulps(0.21150323672030336, 0.2115032367203034), 2);
  assert.equal(ulps(1, -1), Infinity);
  assert.deepEqual(compareDescriptors({ qed: 0.8215995486924975, MolLogP: 1.31 }, { qed: 0.8215995486924976, MolLogP: 1.31 }).exact, []);
  assert.deepEqual(compareDescriptors({ MolLogP: 1.31 }, { MolLogP: 1.31 + 2 ** -52 }).exact, ["MolLogP"]);
  assert.deepEqual(compareDescriptors({ qed: 0.5 }, { qed: 0.5 + 8 * 2 ** -53 }).exact, ["qed"], `beyond ${ADMET_ULP_DESCRIPTORS.qed} units`);
  const w = admetWorld();
  w.browser.pipeline.chemistry.rows.aspirin.descriptors.qed = 0.55 + 2 ** -53;
  const r = rowOf(compareAdmet(w), "pipeline: each molecule's chemistry");
  assert.equal(r.status, "within_tolerance");
  assert.match(r.why, /qed, whose float64 value differs on 1 molecule by at most 1 unit in the last place.*not identical$/);
  w.browser.pipeline.chemistry.rows.aspirin.features_sha256 = "g";
  assert.equal(rowOf(compareAdmet(w), "pipeline: each molecule's chemistry").status, "different");
});

test("admet forced GPU: the same outputs, on a software adapter, forced, validated, never accelerated", () => {
  for (const [what, change] of [
    ["accelerated", (f) => { f.accelerated = true; }], ["a physical adapter", (f) => { f.gpu.adapter.kind = "physical"; }],
    ["not forced", (f) => { delete f.forced; }], ["not validated", (f) => { f.gpu.validated = false; }], ["the CPU", (f) => { f.backend = "wasm-simd"; }],
  ]) {
    const w = admetWorld();
    change(w.browser.forced.stages[0].compute[0]);
    assert.equal(rowOf(compareAdmet(w), "forced GPU: the domain stage's compute facts").status, "different", what);
  }
  const w = admetWorld();
  w.browser.forced.files["admet.tsv"] = { sha256: sha("x"), text: "x" };
  assert.match(rowOf(compareAdmet(w), "forced GPU: every output").why, /not the first run's bytes: admet\.tsv/);
  const v = admetWorld();
  v.browser.forced.receipt.device.used = "cpu";
  assert.equal(rowOf(compareAdmet(v), "forced GPU: the job receipt").status, "different");
});

/** w with the browser's run.json of `job` (pipeline or forced) changed by `change`. */
function withRun(w, job, change) {
  const doc = JSON.parse(w.browser[job].files["run.json"].text);
  change(doc);
  const text = JSON.stringify(doc);
  w.browser[job].files["run.json"] = { sha256: sha(text), text };
  return w;
}

/** Two float64 values 2 units apart in the last place on either side of a float32 rounding midpoint near `x`. */
function acrossFloat32(x) {
  const f = Math.fround(x);
  const v = new DataView(new ArrayBuffer(8));
  v.setFloat32(0, f);
  v.setUint32(0, v.getUint32(0) + 1);
  const mid = (f + v.getFloat32(0)) / 2;
  const step = (y, k) => { v.setFloat64(0, y); v.setBigUint64(0, v.getBigUint64(0) + BigInt(k)); return v.getFloat64(0); };
  return [step(mid, -1), step(mid, 1)];
}

test("admet: what no single row repeats is held too (a short probe or search, the card's domain digest, a refusal's need and reason, QED's float32, a native line that names a refused molecule, an empty GPU run)", () => {
  for (const [row, change] of [
    // the probe predicted fewer of the kept molecules than native did
    ["pipeline: caco2_wang predictions (full precision)", (w) => { const s = w.browser.probe.sets.molecules; s.ok = [true, false, false, false]; s.endpoints.caco2_wang = { ...s.endpoints.caco2_wang, value: ["-0x1.523d70a3d70a4p+2"] }; w.native.molecules.endpoints.caco2_wang.value[0] = "-0x1.523d70a3d70a4p+2"; return w; }],
    // the browser's similarity file has fewer rows than the molecules it prepared
    ["pipeline: caco2_wang domain search (the browser's kernels)", (w) => { w.browser.pipeline.names = ["aspirin"]; Object.assign(w.browser.pipeline.similarity.endpoints.caco2_wang, { arg: [0], inter: [10], union: [20] }); return w; }],
    // the card names a domain digest the file it wrote does not have
    ["caco2_wang: applicability domain (its training molecules' fingerprints)", (w) => { w.browser.models.cards.caco2_wang.domain_sha256 = "e".repeat(64); return w; }],
    // a refusal whose prepared row does not say the runner reads it, or whose reason run.json does not repeat
    ["pipeline: molecules the browser refused", (w) => { w.browser.pipeline.chemistry.not_prepared.cisplatin.need = null; return w; }],
    ["pipeline: molecules the browser refused", (w) => withRun(w, "pipeline", (d) => { d.refused.cisplatin = "another reason"; })],
    // QED within its units in the last place, but not equal as float32 (the feature row's value)
    ["pipeline: each molecule's chemistry", (w) => { const [a, b] = acrossFloat32(0.55); w.browser.pipeline.chemistry.rows.aspirin.descriptors.qed = a; w.native.molecules.chemistry.rows.aspirin.descriptors.qed = b; return w; }],
    // the native run on the kept molecules names a refused molecule itself: that line is not set aside
    ["pipeline: report.md", (w) => { w.nativeFiles.kept["report.md"] = Buffer.from(`${w.nativeFiles.kept["report.md"]}- cisplatin: read natively\n`); return w; }],
    // a forced run that searched no endpoint, or whose domain stage made no GPU call
    ["forced GPU: the similarity files", (w) => { w.browser.pipeline.similarity.endpoints = {}; w.browser.forced.similarity.endpoints = {}; return w; }],
    ["forced GPU: the domain stage's compute facts", (w) => { w.browser.forced.stages[0].compute = []; return w; }],
  ]) {
    const r = rowOf(compareAdmet(change(admetWorld())), row);
    assert.equal(r?.status, "different", `${row}: ${change}`);
    assert.equal(r.ok, false);
  }
  const [a, b] = acrossFloat32(0.55);
  assert.equal(ulps(a, b), 2);
  assert.notEqual(Math.fround(a), Math.fround(b));
});

test("admet records and kernels: a stage, a model, the molecules read or not read, the similarities used, an alert, each run's facts", () => {
  for (const [row, change] of [
    ["pipeline: run.json stages", (w) => withRun(w, "pipeline", (d) => { d.stages[1].status = "failed"; })],
    ["pipeline: run.json stages", (w) => withRun(w, "pipeline", (d) => { d.stages[1].check = "FAIL"; })],
    ["pipeline: run.json stages", (w) => withRun(w, "pipeline", (d) => { d.complete = false; })],
    ["pipeline: run.json models", (w) => withRun(w, "pipeline", (d) => { d.models.caco2_wang.test_score = 0.2; })],
    ["pipeline: run.json molecules read", (w) => withRun(w, "pipeline", (d) => { d.read = ["caffeine", "aspirin"]; })],
    ["pipeline: molecules not read", (w) => withRun(w, "pipeline", (d) => { d.failures = {}; })],
    ["pipeline: molecules not read", (w) => { w.nativeFiles.runner["run.json"] = Buffer.from(JSON.stringify({ ...JSON.parse(w.nativeFiles.runner["run.json"]), failures: {} })); return w; }],
    ["pipeline: Python used the browser's similarities", (w) => withRun(w, "pipeline", (d) => { d.similarity.rejected = "query 0: not the exact maximum"; })],
    ["pipeline: Python used the browser's similarities", (w) => withRun(w, "pipeline", (d) => { d.similarity = { backend: "cpu" }; })],
    ["pipeline: Python used the browser's similarities", (w) => withRun(w, "pipeline", (d) => { d.similarity.checked = { caco2_wang: 0 }; })],
    ["pipeline: structural alerts", (w) => { w.browser.pipeline.chemistry.rows.aspirin.alerts = []; return w; }],
    ["pipeline: structural alerts", (w) => { w.browser.pipeline.chemistry.rows.caffeine.alerts = [["PAINS_A", "x"]]; return w; }],
    ["pipeline: each molecule's chemistry", (w) => { w.browser.pipeline.chemistry.rows.aspirin.bits_sha256 = "c"; return w; }],
    ["pipeline: each molecule's chemistry", (w) => { delete w.browser.pipeline.chemistry.rows.caffeine; return w; }],
    ["forced GPU: the similarity files", (w) => { w.browser.forced.similarity.endpoints.caco2_wang.sha256 = "t"; return w; }],
    ["forced GPU: run.json", (w) => withRun(w, "forced", (d) => { d.similarity.backend = "wasm-simd"; })],
    ["forced GPU: run.json", (w) => withRun(w, "forced", (d) => { d.similarity.rejected = "query 0"; })],
    ["forced GPU: run.json", (w) => withRun(w, "forced", (d) => { d.stages[0].status = "failed"; })],
    ["first run: the domain stage's compute facts", (w) => { w.browser.pipeline.stages[0].compute[0].backend = "webgpu"; return w; }],
    ["first run: the domain stage's compute facts", (w) => { w.browser.pipeline.stages[0].compute[0].accelerated = true; return w; }],
    ["first run: the domain stage's compute facts", (w) => { w.browser.pipeline.stages[0].compute = []; return w; }],
  ]) {
    const r = rowOf(compareAdmet(change(admetWorld())), row);
    assert.equal(r?.status, "different", `${row}: ${change}`);
    assert.equal(r.ok, false);
  }
});

test("admet: nothing from the browser is missing rows, not passes", () => {
  const w = admetWorld();
  delete w.browser.pipeline;
  delete w.browser.forced;
  delete w.browser.probe;
  const rows = compareAdmet(w);
  assert.ok(rows.some((r) => r.name === "pipeline.admet: its outputs" && !r.ok));
  assert.ok(rows.some((r) => r.name === "forced GPU: pipeline.admet once more" && !r.ok));
  assert.ok(!rowOf(rows, "caco2_wang: test-set predictions (full precision)").ok);
});

const ADMET_NATIVE = path.join(path.dirname(fileURLToPath(import.meta.url)), "admet_native.py");
const ANY_PYTHON = [process.env.PYTHON, "python3"].filter(Boolean).find((py) => {
  try { execFileSync(py, ["-c", "import json"], { stdio: "ignore", timeout: 60000 }); return true; } catch { return false; }
}) ?? null;
/** A Python with native RDKit 2026.9.1 and bioagent (admet_native.py Refusing). */
const RDKIT_PYTHON = [process.env.PYTHON, "python3"].filter(Boolean).find((py) => {
  try { execFileSync(py, ["-c", "import rdkit, bioagent.admet.engine; assert rdkit.__version__ == '2026.09.1'"], { stdio: "ignore", timeout: 60000 }); return true; } catch { return false; }
}) ?? null;

test("admet known answer: the small archive's cards, a learnable target each, 80 training and 25 test molecules", { skip: ANY_PYTHON ? false : "no Python ($PYTHON or python3)" }, () => {
  const dir = mkdtempSync(path.join(tmpdir(), "tcms-admet-answer-"));
  try {
    const values = (cards) => {
      writeFileSync(path.join(dir, "cards.json"), JSON.stringify(cards));
      return JSON.parse(execFileSync(ANY_PYTHON, [ADMET_NATIVE, "values", dir, path.join(dir, "cards.json")], { encoding: "utf8" }).trim().split("\n").at(-1)).checks;
    };
    const card = (metric, score) => ({ metric, test_score: score, train: 80, test: 25, iterations: 194 });
    const good = values({ caco2_wang: card("mae", 0.1328), hia_hou: card("auroc", 1) });
    assert.ok(good.length === 4 && good.every((c) => c.ok), JSON.stringify(good));
    const bad = values({ caco2_wang: { ...card("mae", 0.6) }, hia_hou: { ...card("auroc", 0.95), train: 79 } });
    assert.deepEqual(bad.map((c) => c.ok), [false, true, true, false]);
    assert.ok(!values({ hia_hou: card("auroc", 1) })[0].ok, "a card that is not there fails");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("admet native side: Refusing leaves out what the browser refused, as it did (need runner), and nothing else", { skip: RDKIT_PYTHON ? false : "no Python with RDKit 2026.9.1 and bioagent ($PYTHON or python3)" }, () => {
  const code = [
    "import json, runpy, sys",
    "ns = runpy.run_path(sys.argv[1])",
    "p = ns['Refusing']({'CCO': 'refused here'}).prepare(['CCO', 'CCN', 'C1CC', 'CCO'], alerts=False)",
    "native = ns['Refusing']({}).prepare(['CCO', 'CCN', 'C1CC', 'CCO'], alerts=False)",
    "print(json.dumps({'ok': p.ok.tolist(), 'needs': list(p.needs), 'errors': list(p.errors), 'rows': len(p.features),",
    "  'same_row': bool((p.features[0] == native.features[1]).all()), 'native_ok': native.ok.tolist(), 'name': ns['Refusing']({}).name}))",
  ].join("\n");
  const out = JSON.parse(execFileSync(RDKIT_PYTHON, ["-c", code, ADMET_NATIVE], { encoding: "utf8" }).trim().split("\n").at(-1));
  assert.deepEqual(out.ok, [false, true, false, false]);
  assert.deepEqual(out.needs, ["runner", null, null, "runner"]);
  assert.equal(out.errors[0], "refused here");
  assert.match(out.errors[2], /'C1CC' is not a valid SMILES/);
  assert.equal(out.rows, 1);
  assert.ok(out.same_row, "the kept molecule's features are native RDKit's own");
  assert.deepEqual(out.native_ok, [true, true, false, true]);
  assert.equal(out.name, "rdkit", "the cards it builds say native RDKit built them");
});

// --------------------------------------------------------------------------- the docking job (science.mjs --mode dock)

const VINA_WASM = { name: "AutoDock Vina", version: "1.2.7", build: "wasm", wasm_sha256: "9".repeat(64) };
const hex256 = (text) => createHash("sha256").update(Buffer.from(text)).digest("hex");
const asFile = (text) => ({ sha256: sha(text), bytes: Buffer.byteLength(text), text });

/**
 * A consistent browser/native pair as checkDock hands them to compareDock: two ligands and the reference, docked by two
 * Vina workers in the browser; the native analysis on the browser's results (the same outputs, its own process fields);
 * the runner's native run with other best scores at the same seed, as a seeded search gives elsewhere.
 */
function dockWorld() {
  const texts = { "receptor.pdbqt": "ATOM receptor\n", "ligand-0.pdbqt": "ROOT benzamidine\n", "ligand-1.pdbqt": "ROOT paeonol\n", "reference.pdbqt": "ROOT BEN\n" };
  const lig = (index, name, heavy, torsions) => ({ index, key: `ligand-${index}`, name, file: `plan/ligand-${index}.pdbqt`, sha256: hex256(texts[`ligand-${index}.pdbqt`]), heavy_atoms: heavy, torsions });
  const plan = {
    schema: "tcmstudio.dock-plan/1",
    receptor: { file: "plan/receptor.pdbqt", sha256: hex256(texts["receptor.pdbqt"]), atoms: 701, heavy_atoms: 578, chains: ["A"], residues: 81 },
    ligands: [lig(0, "benzamidine", 9, 1), lig(1, "paeonol", 12, 3)],
    reference: { key: "reference", name: "BEN", file: "plan/reference.pdbqt", sha256: hex256(texts["reference.pdbqt"]), heavy_atoms: 9, torsions: 1 },
    box: { center: [-1.8555000000000001, 14.366, 16.747999999999998], size: [18, 18, 18], source: "reference ligand" },
    params: { scoring: "vina", exhaustiveness: 8, n_poses: 9, seed: 42, energy_range: 3, spacing: 0.375, min_rmsd: 1, max_evals: 0 },
  };
  const planText = `${JSON.stringify(plan, null, 2)}\n`;
  const params = { ...plan.params, center: plan.box.center, size: plan.box.size };
  const poses = "MODEL 1\nATOM pose\nENDMDL\n";
  const result = (key, ligandSha, e) => JSON.stringify({ schema: "tcmstudio.vina-result/1", key, receptor_sha256: plan.receptor.sha256, ligand_sha256: ligandSha, params, engine: VINA_WASM, energies: [[e, e, -0.1, 0.2, -0.1]], poses, seconds: 3.5 });
  const results = { reference: asFile(result("reference", plan.reference.sha256, -6.193)), "ligand-0": asFile(result("ligand-0", plan.ligands[0].sha256, -6.036)), "ligand-1": asFile(result("ligand-1", plan.ligands[1].sha256, -5.799)) };
  const stages = (s) => ["inputs", "site", "redock_validation", "dock", "pose_checks", "report", "verify"].map((name, i) => ({ name, required: true, status: "succeeded", check: name === "verify" ? "PASS" : null, seconds: s * (i + 1) }));
  const run = ({ engine, root, python, at, s, best, rmsd }) => JSON.stringify({
    pipeline: "bioagent.docking.prepared", mode: "prepared", code_digest: "c".repeat(64), started: at, finished: at, python, engine, versions: { vina: "1.2.7" },
    config: { center: null, size: null, scoring: "vina", exhaustiveness: 8, n_poses: 9, seed: 42, energy_range: 3 },
    receptor: { file: "3ptb_receptor.pdbqt", sha256: plan.receptor.sha256, atoms: 701, heavy_atoms: 578, chains: ["A"], residues: 81 },
    box: plan.box, inputs: { [`${root}/3ptb_receptor.pdbqt`]: "a".repeat(64), [`${root}/ligands.pdbqt`]: "b".repeat(64), [`${root}/ben_crystal.pdbqt`]: "e".repeat(64) },
    ligands: plan.ligands.map(({ file, ...l }) => l), reference: { key: "reference", name: "BEN", sha256: plan.reference.sha256, heavy_atoms: 9, torsions: 1 },
    validation: { ligand: "BEN", top_pose_rmsd: rmsd, rmsd: "atom order (upper bound of the symmetry-aware RMSD)", top_score: -6.19, threshold: 2, passed: rmsd <= 2, poses: [] },
    outputs: { "scores.tsv": "d".repeat(64) }, warnings: [],
    results: Object.entries(best).map(([ligand, e]) => ({ ligand, best_score: Math.round(e * 1000) / 1000, energies: [[e, e, -0.1, 0.2, -0.1]], poses: 1, seconds: 3.5 })),
    failures: {}, stages: stages(s), complete: true, limitations: ["A Vina score is not a binding free energy."],
  });
  const stagesJson = (s) => JSON.stringify({ schema: "bioagent.stages/1", pipeline: "bioagent.docking.prepared", planned: [], running: null, stages: stages(s), complete: true, problems: [] });
  const outputs = { "scores.tsv": "ligand\tbest_score\nbenzamidine\t-6.036\npaeonol\t-5.799\n", "poses/benzamidine.pdbqt": poses, "poses/paeonol.pdbqt": poses, "redock/BEN.pdbqt": poses, "report.md": "# Molecular docking\n" };
  const browserBest = { benzamidine: -6.036, paeonol: -5.799 };
  const browserFiles = Object.fromEntries(Object.entries({
    ...outputs, "run.json": run({ engine: VINA_WASM, root: "/work/bj-1/inputs", python: "3.14.2", at: "2026-10-20T17:54:40+00:00", s: 0.01, best: browserBest, rmsd: 1.14 }), "stages.json": stagesJson(0.01),
  }).map(([k, v]) => [k, asFile(v)]));
  const nativeEngine = { name: "AutoDock Vina", version: "1.2.7", build: "native" };
  const facts = ["vina-1", "vina-2", "vina-1"].map((worker, i) => ({ op: "vina.dock", kernel: "AutoDock Vina 1.2.7 global search", backend: "vina-wasm", placement: "browser", where: "worker", worker, size: { heavy_atoms: 9, torsions: 1, exhaustiveness: 8 }, gpu: null, accelerated: null, basis: "CPU, one thread per worker", ms: 3500 + i }));
  const scoreRow = (key, name, pose, energies) => ({ key, name, pose, receptor_sha256: plan.receptor.sha256, ligand_sha256: hex256(pose), energies, bits: energies.map(float64Bits) });
  const energies = { reference: [-6.19, -7.1, -0.9, 0, 0.2, -0.3, 0.1, 0], "ligand-0": [-6.03, -7.0, -0.5, 0.1, 0.2, -0.3, 0.1, 0], "ligand-1": [-5.79, -6.2, -0.6, 0.3, 0.1, -0.2, 0.1, 0] };
  const pose = "ATOM pose\n";
  const scored = [["reference", "BEN"], ["ligand-0", "benzamidine"], ["ligand-1", "paeonol"]].map(([k, name]) => ({ ...scoreRow(k, name, pose, energies[k]), params: { scoring: "vina", center: plan.box.center, size: plan.box.size, spacing: 0.375, seed: 42 }, engine: VINA_WASM, ms: 400 }));
  return {
    browser: {
      job: {
        ok: true, state: "succeeded", files: browserFiles, results, plan: Object.fromEntries([["dock_plan.json", asFile(planText)], ...Object.entries(texts).map(([k, v]) => [k, asFile(v)])]),
        stages: [{ id: "dock", reused: false, record: { workers: 2, why: "2 Vina workers: …" }, compute: facts }],
        receipt: {
          assurance: "outputs_verified",
          device: { evidence: "verified", note: "computed on this device's CPU: CPython compiled to WebAssembly (Pyodide) in 1 dedicated Web Worker, one thread each; AutoDock Vina 1.2.7 (WebAssembly) in 2 dedicated Web Workers, one thread each; nothing ran on a GPU" },
          engine: [{ id: "AutoDock Vina", version: "1.2.7", stages: ["dock"], name: "AutoDock Vina", build: "wasm", wasm_sha256: VINA_WASM.wasm_sha256 }],
          resources: { memory: { dedicated: [{ engine: "AutoDock Vina 1.2.7 (WebAssembly)", workers: 2, wasm_peak_bytes: null, measured_by: null, reason: "not measured: these dedicated workers do not report their WebAssembly memory, and the job service does not read it" }] } },
        },
      },
      scored,
    },
    native: {
      runs: { runner: { command: ["python", "-m", "bioagent.cli", "dock"], exit: 0, seconds: 13, checks: { run: "ok", scores: "ok", report: "ok", limitations: "ok" } }, analysis: { exit: 0, seconds: 0.03, ended: { exit: 0 } } },
      scores: scored.map(({ key, name, receptor_sha256, ligand_sha256, energies: e, bits }) => ({ key, name, receptor_sha256, ligand_sha256, first_pose: true, energies: e, bits })),
    },
    nativeFiles: {
      plan: Object.fromEntries([["dock_plan.json", Buffer.from(planText)], ...Object.entries(texts).map(([k, v]) => [k, Buffer.from(v)])]),
      analysis: Object.fromEntries(Object.entries({
        ...outputs, "run.json": run({ engine: VINA_WASM, root: "/tmp/WORK/raw", python: "3.12.3", at: "2026-10-20T17:54:46+00:00", s: 0.02, best: browserBest, rmsd: 1.14 }), "stages.json": stagesJson(0.02),
      }).map(([k, v]) => [k, Buffer.from(v)])),
      runner: { "run.json": Buffer.from(run({ engine: nativeEngine, root: "/tmp/WORK/raw", python: "3.12.3", at: "2026-10-20T17:55:00+00:00", s: 0.5, best: { benzamidine: -6.078, paeonol: -5.814 }, rmsd: 1.92 })) },
    },
    expected: { files: { "3ptb_receptor.pdbqt": { sha256: "a".repeat(64) }, "ligands.pdbqt": { sha256: "b".repeat(64) }, "ben_crystal.pdbqt": { sha256: "e".repeat(64) } }, engine: VINA_WASM },
  };
}

/** w with the file `name` of `group` (browser: job.files; analysis or runner: native) as JSON changed by `change`. */
function withDockJson(w, group, name, change) {
  if (group === "browser") {
    const doc = JSON.parse(w.browser.job.files[name].text);
    change(doc);
    w.browser.job.files[name] = asFile(JSON.stringify(doc));
  } else {
    const doc = JSON.parse(w.nativeFiles[group][name].toString("utf8"));
    change(doc);
    w.nativeFiles[group][name] = Buffer.from(JSON.stringify(doc));
  }
  return w;
}

test("dock: a consistent run passes, each row saying what it compared; the search's best scores are reported, not counted", () => {
  const rows = compareDock(dockWorld());
  assert.deepEqual(failing(rows), []);
  for (const name of ["plan: dock_plan.json", "plan: the texts Vina was given", "run.json: box, settings, receptor, ligands and reference", "run.json: the inputs' digests",
    "analysis: scores.tsv", "analysis: poses/benzamidine.pdbqt", "analysis: redock/BEN.pdbqt", "score: benzamidine (ligand-0), its top pose", "score: BEN (reference), its top pose"]) {
    assert.equal(rowOf(rows, name)?.status, "identical", name);
  }
  assert.equal(rowOf(rows, "analysis: run.json").status, "same_apart_from_excluded");
  assert.match(rowOf(rows, "analysis: run.json").why, new RegExp(DOCK_RUN_SET_ASIDE.join(", ").replace(/[.*]/g, "\\$&")));
  assert.equal(rowOf(rows, "analysis: stages.json").status, "same_apart_from_excluded");
  for (const name of ["search: both runs complete", "search: BEN redocked within 2 Å", "search: the ligands docked", "run.json: the engine", "native: the runner's command",
    "receipt: the engine", "receipt: the dock stage's compute facts", "receipt: the device note", "receipt: the dedicated workers' memory", "results: one Vina result per planned run"]) {
    assert.equal(rowOf(rows, name)?.status, "same", name);
  }
  const best = rowOf(rows, "search: benzamidine best score");
  assert.equal(best.status, "reported");
  assert.equal(best.counted, false);
  assert.match(best.why, /browser -6\.036, native -6\.078 kcal\/mol at seed 42: \|Δ\| 0\.042/);
  assert.ok(rowOf(rows, "search: |Δbest| over the ligands").why.endsWith(DOCK_SEARCH_BAND));
  assert.match(rowOf(rows, "search: BEN redocked within 2 Å").why, /1\.14 Å from the crystal pose in the browser .* 1\.92 Å natively/);
});

test("dock: a search far from native's is reported, never failed; one that does not redock the reference or complete is failed", () => {
  let w = withDockJson(dockWorld(), "runner", "run.json", (d) => { d.results[0].energies[0][0] = -4.5; });
  let rows = compareDock(w);
  assert.deepEqual(failing(rows), [], "a seeded search's best score is not held to native's");
  assert.match(rowOf(rows, "search: benzamidine best score").why, /\|Δ\| 1\.536/);
  for (const [row, group, change] of [
    ["search: BEN redocked within 2 Å", "browser", (d) => { d.validation.top_pose_rmsd = 2.05; d.validation.passed = false; }],
    ["search: BEN redocked within 2 Å", "runner", (d) => { d.validation.top_pose_rmsd = 2.4; d.validation.passed = false; }],
    ["search: BEN redocked within 2 Å", "browser", (d) => { d.validation.passed = false; }],
    ["search: both runs complete", "runner", (d) => { d.complete = false; }],
    ["search: both runs complete", "browser", (d) => { d.complete = false; }],
    ["search: the ligands docked", "runner", (d) => { d.results.pop(); d.failures = { paeonol: "Vina refused it" }; }],
    ["search: the ligands docked", "browser", (d) => { d.failures = { other: "x" }; }],
  ]) {
    rows = compareDock(withDockJson(dockWorld(), group, "run.json", change));
    assert.equal(rowOf(rows, row).status, "different", `${row}: ${change}`);
    assert.equal(rowOf(rows, row).ok, false);
  }
  w = dockWorld();
  w.native.runs.runner = { ...w.native.runs.runner, exit: 1, stderr: "incomplete" };
  assert.equal(rowOf(compareDock(w), "native: the runner's command").status, "different");
  w = dockWorld();
  w.native.runs.runner.checks.run = "verify_run: scores.tsv digest differs";
  assert.match(rowOf(compareDock(w), "native: the runner's command").why, /checks failed: run: verify_run/);
});

test("dock: the plan, the inputs and the ligand table are exact", () => {
  for (const [row, change] of [
    ["plan: dock_plan.json", (w) => { const p = JSON.parse(w.browser.job.plan["dock_plan.json"].text); p.box.center[0] = -1.8555; w.browser.job.plan["dock_plan.json"] = asFile(`${JSON.stringify(p, null, 2)}\n`); }],
    ["plan: dock_plan.json", (w) => { w.browser.job.plan["dock_plan.json"] = asFile(JSON.stringify(JSON.parse(w.browser.job.plan["dock_plan.json"].text))); }],
    ["plan: the texts Vina was given", (w) => { w.browser.job.plan["ligand-1.pdbqt"] = asFile("ROOT paeonol\r\n"); }],
    ["plan: the texts Vina was given", (w) => { w.nativeFiles.plan["receptor.pdbqt"] = Buffer.from("ATOM other\n"); }],
    ["plan: the texts Vina was given", (w) => { delete w.browser.job.plan["reference.pdbqt"]; }],
    ["run.json: box, settings, receptor, ligands and reference", (w) => withDockJson(w, "runner", "run.json", (d) => { d.box.size[2] = 18.000000000000004; })],
    ["run.json: box, settings, receptor, ligands and reference", (w) => withDockJson(w, "browser", "run.json", (d) => { d.ligands[1].torsions = 4; })],
    ["run.json: box, settings, receptor, ligands and reference", (w) => withDockJson(w, "browser", "run.json", (d) => { d.config.seed = 1; })],
    ["run.json: the inputs' digests", (w) => withDockJson(w, "runner", "run.json", (d) => { d.inputs["/tmp/WORK/raw/ligands.pdbqt"] = "f".repeat(64); })],
    ["run.json: the inputs' digests", (w) => { w.expected.files["ligands.pdbqt"].sha256 = "f".repeat(64); }],
    ["run.json: the engine", (w) => withDockJson(w, "browser", "run.json", (d) => { d.engine.wasm_sha256 = "8".repeat(64); })],
    ["run.json: the engine", (w) => withDockJson(w, "runner", "run.json", (d) => { d.engine.version = "1.2.6"; })],
    ["run.json: the engine", (w) => withDockJson(w, "runner", "run.json", (d) => { d.engine.build = "wasm"; })],
  ]) {
    const w = dockWorld();
    const r = rowOf(compareDock(change(w) || w), row);
    assert.equal(r?.status, "different", `${row}: ${change}`);
    assert.equal(r.ok, false);
  }
});

test("dock analysis: every output the browser's bytes; run.json and stages.json apart from the process's own fields only", () => {
  let w = dockWorld();
  w.nativeFiles.analysis["scores.tsv"] = Buffer.from(w.browser.job.files["scores.tsv"].text.replace("-5.799", "-5.800"));
  assert.equal(rowOf(compareDock(w), "analysis: scores.tsv").status, "different");
  w = dockWorld();
  delete w.browser.job.files["redock/BEN.pdbqt"];
  assert.equal(rowOf(compareDock(w), "analysis: redock/BEN.pdbqt").status, "missing");
  w = dockWorld();
  w.native.runs.analysis = { exit: 2, seconds: 0.01, ended: { exit: 2, refused: "ligand-1.json was computed with other parameters: seed" } };
  assert.match(rowOf(compareDock(w), "analysis: run_dock natively on the browser's Vina results").why, /other parameters: seed/);
  // what is set aside may differ; nothing else may, the energies at full precision included
  w = withDockJson(dockWorld(), "analysis", "run.json", (d) => { d.python = "3.11.0"; d.started = "x"; d.stages[2].seconds = 9; });
  assert.equal(rowOf(compareDock(w), "analysis: run.json").status, "same_apart_from_excluded");
  for (const change of [
    (d) => { d.results[0].energies[0][1] += 2 ** -40; }, (d) => { d.validation.top_pose_rmsd = 1.1400000000000001; }, (d) => { d.code_digest = "0".repeat(64); },
    (d) => { d.stages[3].reason = "another reason"; }, (d) => { d.limitations.push("one more"); }, (d) => { d.inputs = { "/tmp/WORK/raw/other.pdbqt": "a".repeat(64) }; },
  ]) {
    assert.equal(rowOf(compareDock(withDockJson(dockWorld(), "analysis", "run.json", change)), "analysis: run.json").status, "different", String(change));
  }
  assert.equal(rowOf(compareDock(withDockJson(dockWorld(), "analysis", "stages.json", (d) => { d.stages[3].status = "failed"; })), "analysis: stages.json").status, "different");
});

test("dock scores: the browser's top poses scored by both engines, every float64 bit the same", () => {
  const name = "score: paeonol (ligand-1), its top pose";
  for (const [what, change] of [
    ["one unit in the last place", (w) => { const s = w.native.scores[2]; s.energies = [...s.energies]; s.energies[3] = 0.1 + 2 ** -56; s.bits = s.energies.map(float64Bits); }],
    ["-0 against 0", (w) => { const s = w.native.scores[2]; s.energies = [...s.energies]; s.energies[7] = -0; s.bits = s.energies.map(float64Bits); }],
    ["a value fewer", (w) => { w.native.scores[2].bits = w.native.scores[2].bits.slice(0, 7); }],
    ["another pose", (w) => { w.native.scores[2].ligand_sha256 = "0".repeat(64); }],
    ["not the first MODEL of the result", (w) => { w.native.scores[2].first_pose = false; }],
  ]) {
    const w = dockWorld();
    change(w);
    const r = rowOf(compareDock(w), name);
    assert.equal(r.status, "different", what);
    assert.equal(r.ok, false, what);
  }
  let w = dockWorld();
  w.native.scores[2] = { key: "ligand-1", name: "paeonol", error: "PDBQT parsing error" };
  assert.equal(rowOf(compareDock(w), name).status, "missing");
  w = dockWorld();
  w.browser.scored = [];
  assert.equal(rowOf(compareDock(w), "score: the browser's top poses").status, "missing");
  assert.equal(float64Bits(-0), "8000000000000000");
  assert.equal(float64Bits(1), "3ff0000000000000");
  assert.notEqual(float64Bits(0.1 + 0.2), float64Bits(0.3));
});

test("dock receipt and results: the engine, vina-wasm facts for every run, the dedicated workers named and their memory not measured", () => {
  for (const [row, change] of [
    ["receipt: outputs_verified, device evidence verified", (w) => { w.browser.job.receipt.assurance = "worker_digests_only"; }],
    ["receipt: outputs_verified, device evidence verified", (w) => { w.browser.job.receipt.device.evidence = "claimed"; }],
    ["receipt: the engine", (w) => { w.browser.job.receipt.engine = []; }],
    ["receipt: the engine", (w) => { w.browser.job.receipt.engine[0].wasm_sha256 = "8".repeat(64); }],
    ["receipt: the engine", (w) => { w.browser.job.receipt.engine[0].stages = ["analysis"]; }],
    ["receipt: the dock stage's compute facts", (w) => { w.browser.job.stages[0].compute[1].backend = "native"; }],
    ["receipt: the dock stage's compute facts", (w) => { w.browser.job.stages[0].compute[0].accelerated = true; }],
    ["receipt: the dock stage's compute facts", (w) => { w.browser.job.stages[0].compute[2].where = "page"; }],
    ["receipt: the dock stage's compute facts", (w) => { w.browser.job.stages[0].compute.pop(); }],
    ["receipt: the dock stage's compute facts", (w) => { w.browser.job.stages[0].reused = true; }],
    ["receipt: the device note", (w) => { w.browser.job.receipt.device.note = "computed on this device's CPU: Pyodide"; }],
    ["receipt: the device note", (w) => { w.browser.job.stages[0].compute.forEach((f) => { f.worker = "vina-1"; }); }],
    ["receipt: the dedicated workers' memory", (w) => { w.browser.job.receipt.resources.memory.dedicated[0].wasm_peak_bytes = 4e8; }],
    ["receipt: the dedicated workers' memory", (w) => { delete w.browser.job.receipt.resources.memory.dedicated; }],
    ["results: one Vina result per planned run", (w) => { delete w.browser.job.results["ligand-1"]; }],
    ["results: one Vina result per planned run", (w) => { w.browser.job.results["ligand-7"] = w.browser.job.results["ligand-1"]; }],
    ["results: one Vina result per planned run", (w) => { const d = JSON.parse(w.browser.job.results["ligand-0"].text); d.params.seed = 1; w.browser.job.results["ligand-0"] = asFile(JSON.stringify(d)); }],
    ["results: one Vina result per planned run", (w) => { const d = JSON.parse(w.browser.job.results["ligand-0"].text); d.engine.build = "native"; w.browser.job.results["ligand-0"] = asFile(JSON.stringify(d)); }],
    ["results: one Vina result per planned run", (w) => { const d = JSON.parse(w.browser.job.results.reference.text); d.ligand_sha256 = "0".repeat(64); w.browser.job.results.reference = asFile(JSON.stringify(d)); }],
  ]) {
    const w = dockWorld();
    change(w);
    const r = rowOf(compareDock(w), row);
    assert.equal(r?.status, "different", `${row}: ${change}`);
    assert.equal(r.ok, false);
  }
  const w = dockWorld();
  delete w.browser.job;
  assert.deepEqual(compareDock(w).map((r) => [r.name, r.status, r.ok]), [["the pipeline.dock browser job", "missing", false]]);
});

test("dock plan: the fixture's files and the pipeline's defaults given explicitly; the first pose as pose_models reads it", () => {
  const plan = dockPlan();
  assert.deepEqual(plan.files, [plan.job.params.receptor, plan.job.params.ligands, plan.job.params.reference_ligand]);
  assert.deepEqual(Object.keys(plan.job.params).sort(), ["exhaustiveness", "ligands", "poses", "receptor", "reference_ligand", "scoring", "seed"]);
  assert.equal(firstPose("MODEL 1\nA\n\nB\nENDMDL\nMODEL 2\nC\nENDMDL\n"), "A\n\nB\n");
  assert.equal(firstPose("MODEL 1\r\nA\r\nENDMDL\r\n"), "A\n");
  assert.equal(firstPose("A\n\nB\n"), "A\nB\n");
  assert.equal(firstPose("MODEL 1\nA\nMODEL 2\nB\nENDMDL\n"), "B\n", "an unclosed MODEL is dropped, as pose_models drops it");
  assert.equal(firstPose("MODEL 1\nA\n"), null);
});

/** A Python with bioagent.docking.prepared (no Vina needed): pose_models, which firstPose mirrors. */
const DOCK_PYTHON = [process.env.PYTHON, "python3"].filter(Boolean).find((py) => {
  try { execFileSync(py, ["-c", "import bioagent.docking.prepared"], { stdio: "ignore", timeout: 60000 }); return true; } catch { return false; }
}) ?? null;
const DOCK_NATIVE = path.join(path.dirname(fileURLToPath(import.meta.url)), "dock_native.py");

test("dock native side: firstPose is pose_models' first pose, and float64Bits dock_native.py's bits", { skip: DOCK_PYTHON ? false : "no Python with bioagent ($PYTHON or python3)" }, () => {
  const texts = ["MODEL 1\nREMARK VINA RESULT: -6.0\nATOM  1\n\nENDMDL\nMODEL 2\nATOM  2\nENDMDL\n", "MODEL 1\r\nATOM  1\r\nENDMDL\r\n", "ATOM  1\n\nATOM  2\n", "MODEL 1\nA\nMODEL 2\nB\nENDMDL\n"];
  const values = [-6.19075629999364, -0, 0, 5e-324, 1.7976931348623157e308, 0.1 + 0.2];
  const code = [
    "import json, runpy, sys",
    "from bioagent.docking.prepared import pose_models",
    "ns = runpy.run_path(sys.argv[1])",
    "doc = json.loads(sys.stdin.read())",
    "print(json.dumps({'poses': ['\\n'.join(pose_models(t)[0]) + '\\n' for t in doc['texts']],",
    "  'bits': [ns['bits'](float(x)) for x in doc['values']]}))",
  ].join("\n");
  // -0 survives JSON only as text: give each value as its repr
  const input = JSON.stringify({ texts, values: values.map((v) => (Object.is(v, -0) ? "-0.0" : String(v))) });
  const out = JSON.parse(execFileSync(DOCK_PYTHON, ["-c", code, DOCK_NATIVE], { input, encoding: "utf8" }).trim().split("\n").at(-1));
  assert.deepEqual(texts.map(firstPose), out.poses);
  assert.deepEqual(values.map(float64Bits), out.bits);
});
