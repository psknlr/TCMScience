// How the browser's research exports are held to the native run's (science.mjs --mode gqd; the omics jobs' outputs,
// --mode omics, are held to theirs by the rule at the end of this file). Identical bytes are the
// expectation and are reported as such. Only when a table's bytes differ is it compared cell by cell, as
// tcmstudio.browser_selftest.compare does: every non-numeric cell the same and every number within RELATIVE (the
// tolerance np_exports.verify allows a recomputed p-value) is "within_tolerance", with the largest relative difference;
// anything else is "different". CPython's math.exp is not correctly rounded, and glibc's and Pyodide's (wasm32) can
// differ in the last bit, so a p-value written at full precision can do so too: that is the case this names, never
// smoothing it into "identical".
//
// A table within tolerance changes what restates it: the manifest's digests and sizes of the outputs, and the result
// digest the manifest and provenance carry. Those, and only those, are set aside, and the row says which; every other
// field of the JSON exports, and every word of limitations.md, must still agree (numbers within RELATIVE).

import { createHash } from "node:crypto";

export const RELATIVE = 1e-12;
export const TABLES = ["compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv", "network.tsv"];
export const RESEARCH_EXPORTS = [...TABLES, "provenance.json", "run_manifest.json", "limitations.md", "report.html"];

const sha256 = (buf) => `sha256:${createHash("sha256").update(buf).digest("hex")}`;

function numberOf(cell) {
  if (typeof cell !== "string" || !cell.trim()) return null;
  const n = Number(cell);
  return Number.isFinite(n) ? n : null;
}

function relative(a, b) {
  return Math.abs(a - b) / Math.max(Math.abs(a), Math.abs(b), 1e-300);
}

/** Two TSV texts cell by cell → {status: "identical" | "within_tolerance" | "different", max_relative?, first?, count?}. */
export function compareTable(got, want) {
  if (got === want) return { status: "identical" };
  const rows = (text) => text.split(/\r?\n/).filter((l, i, all) => l !== "" || i < all.length - 1).map((l) => l.split("\t"));
  const ra = rows(got);
  const rb = rows(want);
  const diffs = [];
  let worst = 0;
  if (ra.length !== rb.length) diffs.push({ rows: [ra.length, rb.length] });
  for (let i = 0; i < Math.min(ra.length, rb.length); i++) {
    if (ra[i].length !== rb[i].length) { diffs.push({ row: i, columns: [ra[i].length, rb[i].length] }); continue; }
    for (let j = 0; j < ra[i].length; j++) {
      const u = ra[i][j];
      const v = rb[i][j];
      if (u === v) continue;
      const nu = numberOf(u);
      const nv = numberOf(v);
      if (nu !== null && nv !== null) {
        const rel = relative(nu, nv);
        worst = Math.max(worst, rel);
        if (rel <= RELATIVE) continue;
      }
      diffs.push({ row: i, column: j, got: u, want: v });
    }
  }
  return diffs.length ? { status: "different", first: diffs.slice(0, 5), count: diffs.length } : { status: "within_tolerance", max_relative: worst };
}

/** Two JSON values, numbers within `tolerance` (relative; 0: equal), the paths in `skip` ("a.b.c", "*" a key at that
 * level) set aside → {same: bool, first: [path...]} (the paths that differ, at most five). */
export function compareJson(got, want, skip = [], tolerance = RELATIVE) {
  const skips = skip.map((p) => p.split("."));
  const skipped = (path) => skips.some((s) => s.length === path.length && s.every((k, i) => k === "*" || k === path[i]));
  const out = [];
  const walk = (a, b, path) => {
    if (out.length >= 5 || skipped(path)) return;
    if (typeof a === "number" && typeof b === "number") {
      if (a !== b && relative(a, b) > tolerance) out.push(path.join("."));
      return;
    }
    if (Array.isArray(a) && Array.isArray(b)) {
      if (a.length !== b.length) { out.push(`${path.join(".")}[length]`); return; }
      a.forEach((x, i) => walk(x, b[i], [...path, String(i)]));
      return;
    }
    if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
      for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
        if (!(k in a) || !(k in b)) { if (!skipped([...path, k])) out.push([...path, k].join(".")); continue; }
        walk(a[k], b[k], [...path, k]);
      }
      return;
    }
    if (a !== b) out.push(path.join(".") || "(the document)");
  };
  walk(got, want, []);
  return { same: out.length === 0, first: out };
}

/** Two texts word by word, numbers within RELATIVE (or as `near(a, b)` says) → {same, first: [[got, want]...]}. */
export function compareText(got, want, near = (a, b) => relative(a, b) <= RELATIVE) {
  const split = (t) => t.split(/([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/);
  const a = split(got);
  const b = split(want);
  const first = [];
  if (a.length !== b.length) return { same: false, first: [["(word count)", `${a.length} / ${b.length}`]] };
  for (let i = 0; i < a.length && first.length < 5; i++) {
    if (a[i] === b[i]) continue;
    const na = i % 2 ? numberOf(a[i]) : null;
    const nb = i % 2 ? numberOf(b[i]) : null;
    if (na !== null && nb !== null && near(na, nb)) continue;
    first.push([a[i], b[i]]);
  }
  return { same: first.length === 0, first };
}

/**
 * The browser's exports ({name: {sha256, text?}}) against the native run's files (name → Buffer, or null when absent)
 * → rows [{name, status, ok, why, browser, native}]. status: "identical" (the bytes), "same_apart_from_execution" (the
 * manifest: every field but the run's own execution block, exactly), "same_as_json" (equal values, other bytes),
 * "within_tolerance" (what was set aside is said),
 * "different" or "missing". ok is what the verdict counts: anything but different or missing; report.html restates
 * the manifest's run-specific lines (its own audit head and artifact), so it is reported but never counted.
 */
export function compareExports(browserFiles, nativeOf) {
  const rows = [];
  const tolerated = [];
  const row = (name, status, why, b, native, counted = true) => {
    const ok = !counted || !["different", "missing"].includes(status);
    rows.push({ name, status, ok, same: status === "identical", comparable: status !== "different" && status !== "missing", why, browser: b?.sha256 ?? null, native: native ? sha256(native) : null });
  };
  const pairs = RESEARCH_EXPORTS.map((name) => [name, browserFiles?.[name], nativeOf(name)]);
  // the tables first: what they decide is what the restating files may differ by
  for (const [name, b, native] of pairs.filter(([n]) => TABLES.includes(n))) {
    if (!b || !native) { row(name, "missing", !b ? "not written in the browser" : "no native file", b, native); continue; }
    if (sha256(native) === b.sha256) { row(name, "identical", "identical bytes", b, native); continue; }
    if (typeof b.text !== "string") { row(name, "different", "the bytes differ (the browser's text was not returned to compare cells)", b, native); continue; }
    const c = compareTable(b.text, native.toString("utf8"));
    if (c.status === "within_tolerance") tolerated.push(name);
    row(name, c.status, c.status === "within_tolerance"
      ? `within tolerance: every other cell the same, the numbers within a relative ${RELATIVE} (largest ${c.max_relative.toExponential(2)}); not identical bytes`
      : `the cells differ: ${JSON.stringify(c.first)}${c.count > 5 ? ` and ${c.count - 5} more` : ""}`, b, native);
  }
  const setAside = tolerated.length ? `; set aside because ${tolerated.join(", ")} ${tolerated.length > 1 ? "are" : "is"} within tolerance: ` : "";
  for (const [name, b, native] of pairs.filter(([n]) => !TABLES.includes(n))) {
    const counted = name !== "report.html";
    if (!b || !native) { row(name, "missing", !b ? "not written in the browser" : "no native file", b, native, counted); continue; }
    if (sha256(native) === b.sha256) { row(name, "identical", "identical bytes", b, native, counted); continue; }
    if (typeof b.text !== "string") { row(name, "different", "the bytes differ", b, native, counted); continue; }
    if (name.endsWith(".json")) {
      let got;
      let want;
      try { got = JSON.parse(b.text); want = JSON.parse(native.toString("utf8")); } catch { row(name, "different", "the bytes differ and one is not JSON", b, native, counted); continue; }
      // the run's own execution block (its audit head and artifact) is never the same twice
      const skip = name === "run_manifest.json" ? ["execution"] : [];
      const plain = compareJson(got, want, skip, 0);
      if (plain.same) {
        if (skip.length) row(name, "same_apart_from_execution", "identical apart from execution (the run's own audit head and artifact)", b, native, counted);
        else row(name, "same_as_json", "the same JSON values, written differently", b, native, counted);
        continue;
      }
      if (compareJson(got, want, skip).same) { row(name, "within_tolerance", "the same as JSON, the numbers within tolerance; not identical bytes", b, native, counted); continue; }
      if (tolerated.length) {
        const derived = ["result_digest", ...(name === "run_manifest.json" ? ["outputs.*.sha256", "outputs.*.bytes"] : [])];
        const loose = compareJson(got, want, [...skip, ...derived]);
        if (loose.same) { row(name, "within_tolerance", `the same apart from what restates the tables${setAside}${derived.join(", ")}`, b, native, counted); continue; }
      }
      row(name, "different", `differs at ${plain.first.join(", ")}`, b, native, counted);
      continue;
    }
    const t = compareText(b.text, native.toString("utf8"));
    if (t.same) { row(name, "within_tolerance", "the same words, the numbers within tolerance", b, native, counted); continue; }
    row(name, "different", counted ? `differs: ${JSON.stringify(t.first)}` : "differs (it restates the run's own audit head and artifact; not counted)", b, native, counted);
  }
  return rows;
}

// ------------------------------------------------------------------------- the omics jobs (science.mjs --mode omics)
//
// pipeline.rnaseq (count-matrix mode) and pipeline.scrna as browser jobs, against the native command on the same files.
// Their DESeq2 fits (the dispersion search, a grid then golden sections on a flat likelihood, in the RNA-seq tables and
// each pseudobulk) turn a last-bit difference between the native build (its numpy, scipy and BLAS) and Pyodide's wasm32
// one into a difference the tables' six printed digits can show (the UMAP layout amplifies it too, and is not compared;
// what follows from Harmony was identical in the runs so far), so a numeric cell is held to
// OMICS_RULE. Measured in Pyodide 314.0.7 against native numpy and scipy over three configurations, the largest
// difference used 0.157 of it (a pseudobulk p-value), and the tighter 1e-7 + 1e-5·max was exceeded 1.55-fold. Each row
// says the rule and how much of it the largest difference used. Everything else is exact: headers, gene and cell order, NA positions, True/False flags, every text cell, the
// set of significant genes (a gene that crosses alpha fails the row, unless both values are within the rule of alpha,
// when it is named, not hidden), each cell's cluster and cell type, the clusters table. The UMAP layout is a picture of
// the graph that amplifies last-bit differences: its coordinates (cells.tsv umap_1, umap_2) and the figures are not
// compared; its structure is (umapAgreement). A figure that is not the same bytes says whether what it draws is
// compared, and in which table (FIGURES). report.html must be identical with each figure set aside: no tolerance on its
// text.

export const OMICS_RULE = "|a − b| ≤ 1e-6 + 1e-4·max(|a|,|b|) + one unit in the sixth significant digit of max(|a|,|b|) (the tables print %.6g)";
/** The UMAP's structure: each cell's UMAP_K nearest neighbours in the layout, the share of them in its cluster. */
export const UMAP_K = 15;
export const UMAP_AGREEMENT_MIN = 0.95;
export const UMAP_AGREEMENT_GAP = 0.01;
const UMAP_COLUMNS = ["umap_1", "umap_2"];
/** Files the run writes about itself (times, paths, versions): compared through their fields (record rows). */
const RECORDS = ["run.json", "stages.json", "job_receipt.json"];
/** What each figure draws (bioagent.omics.rnaseq._de_plots, bioagent.omics.scrna._plots), and whether that is compared
 * and where: the reason a figure that is not the same bytes gives. A figure not named here says only that what it
 * draws is not compared. */
const FIGURES = {
  "plots/ma.svg": "what it draws (each gene's base_mean, log2_fold_change and p_adjusted against alpha) is compared in deseq2_results.tsv",
  "plots/volcano.svg": "what it draws (each tested gene's log2_fold_change, p_value and p_adjusted against alpha) is compared in deseq2_results.tsv",
  "plots/p_values.svg": "what it draws (the tested genes' p_value) is compared in deseq2_results.tsv",
  "plots/dispersion.svg": "what it draws (each gene's dispersion_gene_wise, dispersion_trend and dispersion against its base_mean) is compared in de_diagnostics.tsv and deseq2_results.tsv",
  "plots/pca.svg": "what it draws (the samples' PCA coordinates and the variance PC1 and PC2 explain) is in no table and is not compared; only its input, vst.tsv, is",
  "plots/sample_distances.svg": "what it draws (the sample-to-sample distances) is in no table and is not compared; only its input, vst.tsv, is",
  "plots/qc_genes.svg": "what it draws (each cell's n_genes) is compared in cells.tsv",
  "plots/qc_mito.svg": "what it draws (each cell's pct_mito) is compared in cells.tsv",
  "plots/markers.svg": "what it draws (the share of each cluster's cells expressing the clusters' top markers) is compared in markers.tsv (pct_in) for each cluster's own markers; a marker's share in another cluster is not compared unless it is among that cluster's 50 markers too",
  "plots/pca_variance.svg": "what it draws (the variance each PC explains) is in no table and is not compared",
  "plots/paga.svg": "its nodes sit at the clusters' UMAP medians, and its edges are the connectivities compared in paga.tsv",
  "plots/umap_pseudotime.svg": "the pseudotime it is coloured by is compared in cells.tsv",
};

/** The bound OMICS_RULE holds a numeric cell to: 1e-6 + 1e-4·m + u6, m = max(|a|, |b|), u6 one unit in m's sixth
 * significant digit. */
export function omicsBound(a, b) {
  const m = Math.max(Math.abs(a), Math.abs(b));
  const u6 = m > 0 ? 10 ** (Math.floor(Math.log10(m)) - 5) : 0;
  return 1e-6 + 1e-4 * m + u6;
}

/** The share of OMICS_RULE's bound a difference uses (≤ 1: within it). */
export function omicsFraction(a, b) {
  return Math.abs(a - b) / omicsBound(a, b);
}

const tsvRows = (text) => text.split(/\r?\n/).filter((l, i, all) => l !== "" || i < all.length - 1).map((l) => l.split("\t"));
const textOf = (v) => (typeof v === "string" ? v : Buffer.from(v).toString("utf8"));
const shaOf = (v) => (v === null || v === undefined ? null : sha256(typeof v === "string" ? Buffer.from(v) : v));

/**
 * Two TSV texts cell by cell under OMICS_RULE → {status: "identical" | "same_apart_from_excluded" | "within_tolerance" |
 * "different", numeric: how many numeric cells differ, worst: {fraction, row, column, got, want} | null (the cell
 * using most of its bound), max_relative: the largest relative difference of any numeric cell, first: the first
 * differences, count, excluded: the columns set aside}. Columns named in `exclude` are not compared at all.
 */
export function compareOmicsTable(got, want, { exclude = [] } = {}) {
  if (got === want) return { status: "identical", numeric: 0, worst: null, max_relative: 0, first: [], count: 0, excluded: [] };
  const ra = tsvRows(got);
  const rb = tsvRows(want);
  const header = rb[0] || [];
  const skip = new Set(exclude.map((c) => header.indexOf(c)).filter((j) => j >= 0));
  const diffs = [];
  let worst = null;
  let numeric = 0;
  let maxRelative = 0;
  if (ra.length !== rb.length) diffs.push({ rows: [ra.length, rb.length] });
  if ((ra[0] || []).join("\t") !== header.join("\t")) diffs.push({ header: [(ra[0] || []).join(" "), header.join(" ")] });
  for (let i = 1; i < Math.min(ra.length, rb.length); i++) {
    if (ra[i].length !== rb[i].length) { diffs.push({ row: i, columns: [ra[i].length, rb[i].length] }); continue; }
    for (let j = 0; j < ra[i].length; j++) {
      const u = ra[i][j];
      const v = rb[i][j];
      if (u === v || skip.has(j)) continue;
      const nu = numberOf(u);
      const nv = numberOf(v);
      if (nu !== null && nv !== null) {
        numeric += 1;
        maxRelative = Math.max(maxRelative, relative(nu, nv));
        const fraction = omicsFraction(nu, nv);
        if (!worst || fraction > worst.fraction) worst = { fraction, row: i, column: header[j] ?? j, got: u, want: v };
        if (fraction <= 1) continue;
      }
      diffs.push({ row: i, column: header[j] ?? j, got: u, want: v });
    }
  }
  const excluded = [...skip].map((j) => header[j]);
  if (!diffs.length && !numeric && !excluded.length) diffs.push({ bytes: "the bytes differ, though no cell does (line endings or blank lines)" });
  if (diffs.length) return { status: "different", numeric, worst, max_relative: maxRelative, first: diffs.slice(0, 5), count: diffs.length, excluded };
  return { status: numeric ? "within_tolerance" : "same_apart_from_excluded", numeric, worst, max_relative: maxRelative, first: [], count: 0, excluded };
}

/** report.html with every inline figure (<svg>…</svg>) replaced by a placeholder. */
export function withoutFigures(html) {
  return html.replace(/<svg\b[\s\S]*?<\/svg>/g, "<svg/>");
}

/** A results table's genes with p_adjusted below alpha, and every gene's p_adjusted → {significant: Set, padj: Map}. */
function significance(text, alpha, geneColumn) {
  const [head = [], ...body] = tsvRows(text);
  const g = head.indexOf(geneColumn);
  const q = head.indexOf("p_adjusted");
  const padj = new Map();
  const significant = new Set();
  for (const r of body) {
    const p = numberOf(r[q]);
    padj.set(r[g], p);
    if (p !== null && p < alpha) significant.add(r[g]);
  }
  return { significant, padj };
}

/**
 * The genes called (p_adjusted < alpha) in the browser's table and the native one → {status: "same" | "flips_near_alpha"
 * | "different", significant, flips: [{gene, browser, native, near}]}. A gene called on one side only is a flip; it is
 * tolerated (named, status flips_near_alpha) only when both its values are within OMICS_RULE of alpha.
 */
export function compareSignificant(got, want, alpha, geneColumn = "gene") {
  const a = significance(got, alpha, geneColumn);
  const b = significance(want, alpha, geneColumn);
  const near = (p) => p !== null && omicsFraction(p, alpha) <= 1;
  const flips = [...new Set([...a.significant, ...b.significant])].filter((g) => a.significant.has(g) !== b.significant.has(g)).sort()
    .map((gene) => ({ gene, browser: a.padj.get(gene) ?? null, native: b.padj.get(gene) ?? null, near: near(a.padj.get(gene) ?? null) && near(b.padj.get(gene) ?? null) }));
  const status = !flips.length ? "same" : flips.every((f) => f.near) ? "flips_near_alpha" : "different";
  return { status, significant: b.significant.size, browser: a.significant.size, flips };
}

/**
 * cells.tsv's UMAP layout as a structure → {cells: the cell column, finite, agreement}: agreement is the mean over
 * cells of the share of each cell's k nearest neighbours in the layout (Euclidean; ties by row) that are in its
 * cluster.
 */
export function umapAgreement(text, k = UMAP_K) {
  const [head = [], ...body] = tsvRows(text);
  const col = (name) => head.indexOf(name);
  const [cc, cl, cx, cy] = [col("cell"), col("cluster"), col(UMAP_COLUMNS[0]), col(UMAP_COLUMNS[1])];
  const cells = body.map((r) => r[cc]);
  const cluster = body.map((r) => r[cl]);
  const x = body.map((r) => Number(r[cx]));
  const y = body.map((r) => Number(r[cy]));
  const finite = body.length > 0 && x.every(Number.isFinite) && y.every(Number.isFinite);
  if (!finite || body.length <= k) return { cells, finite, agreement: null };
  let total = 0;
  const d = new Float64Array(body.length);
  const order = new Uint32Array(body.length);
  for (let i = 0; i < body.length; i++) {
    for (let j = 0; j < body.length; j++) {
      d[j] = j === i ? Infinity : (x[i] - x[j]) ** 2 + (y[i] - y[j]) ** 2;
      order[j] = j;
    }
    const nearest = [...order].sort((p, q) => d[p] - d[q] || p - q).slice(0, k);
    total += nearest.filter((j) => cluster[j] === cluster[i]).length / k;
  }
  return { cells, finite, agreement: total / body.length };
}

/** The columns of a TSV text, by name → [[cells of those columns] per row] (header left out). */
function columns(text, names) {
  const [head = [], ...body] = tsvRows(text);
  const idx = names.map((n) => head.indexOf(n));
  return idx.some((j) => j < 0) ? null : body.map((r) => idx.map((j) => r[j]));
}

/** A cell type's pseudobulk file name, as bioagent.omics.scrna._safe makes it. */
const safeName = (name) => [...String(name)].map((ch) => (/[\p{L}\p{N}]/u.test(ch) ? ch : "_")).join("");

const fmt = (v) => (typeof v === "number" ? Number(v.toPrecision(4)) : v);

/**
 * A browser omics job's outputs ({path: {sha256, text}}) against the native command's (path → Buffer or text) → rows
 * [{name, status, ok, counted, why, browser, native, fraction?}]. `kind` is pipeline.rnaseq or pipeline.scrna. Every
 * file both runs wrote is a row (run.json and stages.json through their fields); then the discrete results and, for
 * single-cell, the UMAP's structure. ok is false only for a counted row that is different or missing; a figure that
 * is not the same bytes is "not_compared" and not counted, and says whether what it draws is compared (FIGURES);
 * report.html is "same_apart_from_figures" or "different".
 */
export function compareOmics(kind, browserFiles, nativeFiles) {
  const rows = [];
  const row = (name, status, why, { browser = null, native = null, counted = true, fraction } = {}) => {
    const ok = !counted || !["different", "missing"].includes(status);
    rows.push({ name, status, ok, counted, why, browser, native, ...(fraction === undefined ? {} : { fraction }) });
  };
  const native = nativeFiles || {};
  const names = [...new Set([...Object.keys(browserFiles || {}), ...Object.keys(native)])].filter((n) => !RECORDS.includes(n)).sort();
  const textB = (n) => browserFiles?.[n]?.text;
  const textN = (n) => (native[n] === undefined || native[n] === null ? undefined : textOf(native[n]));
  for (const name of names) {
    const b = browserFiles?.[name];
    const n = native[name];
    const shas = { browser: b?.sha256 ?? null, native: shaOf(n) };
    if (!b || n === undefined || n === null) { row(name, "missing", !b ? "not written in the browser" : "no native file", shas); continue; }
    if (shas.browser === shas.native) { row(name, "identical", "identical bytes", shas); continue; }
    if (typeof b.text !== "string") { row(name, "different", "the bytes differ (the browser's text was not returned)", shas); continue; }
    if (name.endsWith(".svg")) {
      row(name, "not_compared", /umap|paga/.test(name)
        ? `not compared: a UMAP figure (its points are the layout, which amplifies last-bit differences; the layout's structure is checked below)${FIGURES[name] ? `; ${FIGURES[name]}` : ""}`
        : `not compared: a figure, its coordinates printed at two or three digits; ${FIGURES[name] ?? "what it draws is not compared here"}`, { ...shas, counted: false });
      continue;
    }
    if (name.endsWith(".tsv")) {
      const c = compareOmicsTable(b.text, textN(name), { exclude: name === "cells.tsv" ? UMAP_COLUMNS : [] });
      const apart = c.excluded.length ? `; ${c.excluded.join(", ")} not compared (the UMAP layout; its structure is checked below)` : "";
      const worst = c.worst ? `the largest at ${fmt(c.worst.fraction)} of the bound (row ${c.worst.row}, ${c.worst.column}: ${c.worst.got} vs ${c.worst.want}); the largest relative difference ${c.max_relative.toExponential(2)}` : "";
      if (c.status === "same_apart_from_excluded") row(name, c.status, `every compared cell identical${apart}`, shas);
      else if (c.status === "within_tolerance") row(name, c.status, `within tolerance, not identical bytes: ${c.numeric} numeric cell${c.numeric === 1 ? "" : "s"} differ, ${worst}; every other cell identical; rule ${OMICS_RULE}${apart}`, { ...shas, fraction: c.worst.fraction });
      else row(name, c.status, `the cells differ: ${JSON.stringify(c.first)}${c.count > 5 ? ` and ${c.count - 5} more` : ""}${worst ? `; ${worst}` : ""}`, { ...shas, ...(c.worst ? { fraction: c.worst.fraction } : {}) });
      continue;
    }
    if (name === "report.html") {
      // equal with each figure set aside, every number it restates included: no tolerance on its text
      const [got, want] = [withoutFigures(b.text), withoutFigures(textN(name))];
      if (got === want) row(name, "same_apart_from_figures", "identical with each inline figure (<svg>) set aside", shas);
      else row(name, "different", `differs with each inline figure (<svg>) set aside (no tolerance on its text): ${JSON.stringify(compareText(got, want, () => false).first)}`, shas);
      continue;
    }
    let worstText = 0;
    const t = compareText(b.text, textN(name), (x, y) => { const f = omicsFraction(x, y); worstText = Math.max(worstText, f); return f <= 1; });
    if (t.same) row(name, "within_tolerance", `the same words, the numbers within the rule (the largest at ${fmt(worstText)} of the bound); not identical bytes`, { ...shas, fraction: worstText });
    else row(name, "different", `differs: ${JSON.stringify(t.first)}`, shas);
  }

  // the run records: the same stages, ending the same way, and the same outputs listed
  const json = (text) => { try { return JSON.parse(text); } catch { return null; } };
  const rb = json(textB("run.json"));
  const rn = json(textN("run.json"));
  if (!rb || !rn) row("run.json", "missing", !rb ? "no readable run.json from the browser" : "no readable native run.json");
  else {
    const stages = (r) => (r.stages || []).map((s) => `${s.name}:${s.status}${s.check ? `:${s.check}` : ""}`);
    const same = JSON.stringify(stages(rb)) === JSON.stringify(stages(rn)) && rb.complete === rn.complete;
    row("run.json: stages", same ? "same" : "different", same ? `${stages(rn).length} stages ended the same way (${stages(rn).join(" ")}); complete ${rn.complete}` : `browser ${stages(rb).join(" ")} (complete ${rb.complete}) / native ${stages(rn).join(" ")} (complete ${rn.complete})`);
    const outs = (r) => Object.keys(r.outputs || {}).sort();
    const sameOut = JSON.stringify(outs(rb)) === JSON.stringify(outs(rn));
    row("run.json: outputs", sameOut ? "same" : "different", sameOut ? `the same ${outs(rn).length} outputs recorded` : `browser ${outs(rb).join(", ")} / native ${outs(rn).join(", ")}`);
  }

  // the discrete results, exactly
  const tests = [];
  if (kind === "pipeline.rnaseq") tests.push(["deseq2_results.tsv", rn?.differential_expression?.alpha ?? 0.05, "gene_id", "genes called (p_adjusted < alpha)"]);
  else for (const [ct, t] of Object.entries(rn?.pseudobulk?.tests || {})) tests.push([`pseudobulk/${safeName(ct)}.tsv`, t?.alpha ?? 0.05, "gene", `${ct} pseudobulk: genes called (p_adjusted < alpha)`]);
  for (const [file, alpha, gene, label] of tests) {
    if (textB(file) === undefined || textN(file) === undefined) { row(label, "missing", `${file} is missing`); continue; }
    const s = compareSignificant(textB(file), textN(file), alpha, gene);
    const flips = s.flips.map((f) => `${f.gene} (browser ${f.browser ?? "NA"}, native ${f.native ?? "NA"})`).join(", ");
    row(label, s.status, s.status === "same" ? `the same ${s.significant} gene${s.significant === 1 ? "" : "s"} at alpha ${alpha}`
      : s.status === "flips_near_alpha" ? `${s.flips.length} gene${s.flips.length === 1 ? "" : "s"} cross alpha ${alpha} with both values within the rule of it, named not failed: ${flips}`
        : `the genes called differ at alpha ${alpha}: ${flips}`);
  }
  if (kind === "pipeline.scrna") {
    const cellsB = textB("cells.tsv");
    const cellsN = textN("cells.tsv");
    if (cellsB === undefined || cellsN === undefined) row("each cell's cluster and cell type", "missing", "cells.tsv is missing");
    else {
      const a = columns(cellsB, ["cell", "cluster", "cell_type"]);
      const b = columns(cellsN, ["cell", "cluster", "cell_type"]);
      const bad = !a || !b ? -1 : a.length !== b.length ? Math.min(a.length, b.length) : a.findIndex((r, i) => r.join("\t") !== b[i].join("\t"));
      row("each cell's cluster and cell type", bad === -1 && a && b ? "same" : "different", bad === -1 && a && b ? `the same for all ${b.length} cells, in the same order` : !a || !b ? "cells.tsv lacks cell, cluster or cell_type" : `from row ${bad + 1}: browser ${a[bad]?.join(" ")} / native ${b[bad]?.join(" ")}`);
      const ua = umapAgreement(cellsB);
      const un = umapAgreement(cellsN);
      const order = JSON.stringify(ua.cells) === JSON.stringify(un.cells);
      const held = order && ua.finite && un.finite && ua.agreement !== null && un.agreement !== null
        && ua.agreement >= UMAP_AGREEMENT_MIN && Math.abs(ua.agreement - un.agreement) <= UMAP_AGREEMENT_GAP;
      row("UMAP layout: its structure", held ? "structure_held" : "different",
        `${order ? "the same cells in the same order" : "NOT the same cells in the same order"}, coordinates ${ua.finite && un.finite ? "all finite" : "NOT all finite"}; the share of each cell's ${UMAP_K} nearest UMAP neighbours in its cluster: browser ${fmt(ua.agreement)}, native ${fmt(un.agreement)} (at least ${UMAP_AGREEMENT_MIN}, within ${UMAP_AGREEMENT_GAP} of the native); the coordinates are not compared`);
    }
    const ca = textB("clusters.tsv") === undefined ? null : columns(textB("clusters.tsv"), ["cluster", "cells", "cell_type", "top_markers"]);
    const cb = textN("clusters.tsv") === undefined ? null : columns(textN("clusters.tsv"), ["cluster", "cells", "cell_type", "top_markers"]);
    const same = ca && cb && JSON.stringify(ca) === JSON.stringify(cb);
    row("the clusters (cluster, cells, cell type, top markers)", same ? "same" : ca && cb ? "different" : "missing",
      same ? cb.map((r) => `${r[0]}: ${r[2]} (${r[1]})`).join("; ") : ca && cb ? `browser ${JSON.stringify(ca)} / native ${JSON.stringify(cb)}` : "clusters.tsv is missing or lacks those columns");
  }
  return rows;
}

// ------------------------------------------------------------------------- the ADMET jobs (science.mjs --mode admet)
//
// admet.models and pipeline.admet as browser jobs against the native runs on the same files (admet_native.py). The
// browser is held to exactness, not to a tolerance: its chemistry is RDKit.js held to native RDKit, its models are
// scikit-learn 1.8.0 in Pyodide, bit-identical to native 1.8.0 on the same features, and its domain search is integer
// counts. A model card is compared field by field apart from what names the process that wrote it (CARD_SET_ASIDE): the
// model file's and domain file's digests (the domain file's bytes are a row of their own; a pickle's bytes depend on the
// process that wrote it, wasm32 or 64-bit), the training's seconds and the chemistry engine. The native models are
// trained on the molecules the browser chemistry kept (admet_native.py Refusing), the native pipeline run compared
// byte for byte is the one on the molecules the browser is expected to keep; the runner's own run on every molecule
// shows that it reads those the browser refuses. The predictions are compared at full precision (float.hex) and the
// domain search as its integers (nearest training molecule, intersection, union), molecule by molecule. A report that
// names the molecules the browser refused differs from the native one by those lines only (ADMET_REFUSED_LINES).

/** Card fields that name the process that wrote the model, not the model: not compared. */
export const CARD_SET_ASIDE = Object.freeze(["model_sha256", "domain_sha256", "seconds", "chemistry"]);
/** run.json's model identity: the same fields set aside (and the RDKit version the card names, compared on the card). */
const RUN_MODEL_SET_ASIDE = ["*.model_sha256", "*.chemistry"];
/** Files the pipeline writes about the run itself: compared through their fields. */
const ADMET_RECORDS = ["run.json", "stages.json", "job_receipt.json"];
/** Outputs that list the molecules the browser refused: compared with those lines set aside. */
const ADMET_REFUSED_LINES = ["report.md", "report.html", "limitations.md"];
/**
 * The one descriptor whose float64 value the browser's port does not give to the last bit: QED (rdkit.Chem.QED's
 * desirability functions, a sum of exp() terms whose order and libm the JavaScript port cannot match exactly; measured
 * on this harness's molecules: 1 or 2 units in the last place). Its float32 value, the one in the feature row the models
 * read, must still be equal (the feature rows are compared as bytes), and the difference is named, never called
 * identical. Every other descriptor is exact.
 */
export const ADMET_ULP_DESCRIPTORS = Object.freeze({ qed: 4 });

/**
 * A prediction is the model's raw output (the boosted trees' sum, compared exactly) passed through one elementary
 * function: for a classification scipy's expit, whose exp is the C library's (glibc's natively, Emscripten's musl in
 * Pyodide's wasm32), and for a log10 target numpy's 10 ** (natively, on a CPU with AVX-512, numpy's own SIMD power,
 * not glibc's pow: 5% of values differ from glibc there by one unit, and those are most of these differences; without
 * AVX-512, and in Pyodide, it is the C library's pow). Those last bits differ, so such a value may differ by this many
 * units in the last place of float64, named and never called identical; a regression on its own scale has no such
 * call and is exact. Measured on a synthetic archive at the TDC group's sizes: at most 2 units, on 52 of 16,105 test
 * predictions, every raw output identical. Measured on 100,000 values in [-4, 4] (Pyodide 314.0.7 against glibc and
 * numpy 2.4.6 on an AVX-512 x86_64): pow 0.07% differ from glibc's, 5.3% from numpy's AVX-512 power; exp 0.07%;
 * expit 0.03%, at most 2 units; never more.
 */
export const ADMET_VALUE_ULPS = 4;

/** How many units in the last place of float64 `a` and `b` are apart (Infinity when one is not a finite number). */
export function ulps(a, b) {
  if (a === b) return 0;
  if (!Number.isFinite(a) || !Number.isFinite(b) || Math.sign(a) !== Math.sign(b)) return Infinity;
  const view = new DataView(new ArrayBuffer(16));
  view.setFloat64(0, Math.abs(a));
  view.setFloat64(8, Math.abs(b));
  return Number(view.getBigUint64(0) > view.getBigUint64(8) ? view.getBigUint64(0) - view.getBigUint64(8) : view.getBigUint64(8) - view.getBigUint64(0));
}

/** One molecule's descriptors (browser, native) → {exact: [keys that differ beyond any rule], ulp: [{key, ulps}] (the
 * keys ADMET_ULP_DESCRIPTORS allows, within their bound and equal as float32)}. */
export function compareDescriptors(got, want) {
  const exact = [];
  const ulp = [];
  for (const k of [...new Set([...Object.keys(got || {}), ...Object.keys(want || {})])]) {
    const a = got?.[k];
    const b = want?.[k];
    if (a === b) continue;
    const n = typeof a === "number" && typeof b === "number" ? ulps(a, b) : Infinity;
    if (k in ADMET_ULP_DESCRIPTORS && n <= ADMET_ULP_DESCRIPTORS[k] && Math.fround(a) === Math.fround(b)) ulp.push({ key: k, ulps: n, browser: a, native: b });
    else exact.push(k);
  }
  return { exact, ulp };
}

/** The lines of a text that name none of `names` (as a whole word). */
function linesWithout(text, names) {
  const res = names.map((n) => new RegExp(`(^|[^A-Za-z0-9_])${n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^A-Za-z0-9_]|$)`));
  const kept = [];
  const dropped = [];
  for (const line of String(text).split("\n")) (res.some((r) => r.test(line)) ? dropped : kept).push(line);
  return { text: kept.join("\n"), dropped };
}

/** The C library call between a model's raw output and its prediction, by its card: "exp" (expit, a
 * classification), "pow" (10 **, a log10 target) or null (a regression on its own scale). */
export function admetTransform(card) {
  return card?.task === "classification" ? "exp" : card?.log10_target ? "pow" : null;
}

/**
 * Two exact() results of one endpoint, row by row (the rows named by `names`, in each side's order) → the first
 * differences [{name, field, browser, native}], how many rows were compared, and the predictions that differ only in
 * their last bits after the C library's call (`transform`, admetTransform), with the raw output identical: ulp
 * [{name, ulps}]. Without a transform, or beyond ADMET_VALUE_ULPS, a value that differs is a difference.
 */
function exactRows(b, bNames, n, nNames, { transform = null, fields = ["value", "raw", "in_domain", "arg", "inter", "union"] } = {}) {
  const at = new Map(nNames.map((name, i) => [name, i]));
  const diffs = [];
  const ulp = [];
  let compared = 0;
  for (const [i, name] of bNames.entries()) {
    const j = at.get(name);
    if (j === undefined) { diffs.push({ name, field: "row", browser: "predicted", native: "not predicted" }); continue; }
    compared += 1;
    for (const field of fields) {
      const x = b?.[field]?.[i];
      const y = n?.[field]?.[j];
      if (x === y) continue;
      if (field === "value" && transform && typeof x === "string" && typeof y === "string" && b?.raw?.[i] === n?.raw?.[j] && b?.raw?.[i] !== undefined) {
        const k = ulps(parseHexFloat(x), parseHexFloat(y));
        if (k <= ADMET_VALUE_ULPS) { ulp.push({ name, ulps: k }); continue; }
      }
      diffs.push({ name, field, browser: x, native: y });
    }
  }
  return { diffs, compared, ulp };
}

/** Python's float.hex() text → the number ("0x1.8p+1" → 3; "-0x0.0p+0", "inf", "nan"). */
export function parseHexFloat(text) {
  const t = String(text).trim();
  if (/^-?inf$/.test(t)) return t.startsWith("-") ? -Infinity : Infinity;
  if (t === "nan") return NaN;
  const m = /^(-)?0x([01])\.([0-9a-f]*)p([+-]\d+)$/.exec(t);
  if (!m) return NaN;
  const frac = m[3] ? Number.parseInt(m[3], 16) / 16 ** m[3].length : 0;
  const v = (Number(m[2]) + frac) * 2 ** Number(m[4]);
  return m[1] ? -v : v;
}

/** The status and words of a prediction row from exactRows' answer. */
function predictionWords(x, expected, transform, what) {
  if (x.diffs.length || x.compared !== expected) {
    return { status: "different", why: `${x.compared} of ${expected} compared: ${JSON.stringify(x.diffs.slice(0, 4))}${x.diffs.length > 4 ? ` and ${x.diffs.length - 4} more` : ""}` };
  }
  if (!x.ulp.length) return { status: "identical", why: `${what}: each prediction (float64), the model's raw output, the domain flag and the domain search identical` };
  const worst = x.ulp.reduce((m, u) => Math.max(m, u.ulps), 0);
  return {
    status: "within_tolerance",
    why: `${what}: the model's raw output (the trees' sum) and the domain search identical; ${x.ulp.length} prediction${x.ulp.length === 1 ? " differs" : "s differ"} by at most ${worst} unit${worst === 1 ? "" : "s"} in the last place of float64 (allowed: ${ADMET_VALUE_ULPS}), all from ${transform === "exp" ? "the C library's exp in expit (a classification's probability): glibc's natively, Emscripten's in Pyodide" : "the power in 10 ** (a log10 target): numpy's AVX-512 power or glibc's pow natively, Emscripten's pow in Pyodide"} (${listed(x.ulp.map((u) => `${u.name} ${u.ulps}`), 3)}); not identical`,
  };
}

const listed = (names, max = 8) => `${names.slice(0, max).join(", ")}${names.length > max ? ` and ${names.length - max} more` : ""}`;

/**
 * The ADMET jobs of the page (science-check.mjs out.admet: models, rechem, pipeline, probe, forced) against the native
 * side (admet_native.py native: cards, runs, molecules, tests; nativeFiles: {runner, kept} → {path: Buffer} of the two
 * pipeline runs) and what the molecule set expects (expected.json) → rows [{name, status, ok, counted, why}]. `real`:
 * the archive is a real one, whose training refusals are reported rather than expected to be none.
 */
export function compareAdmet({ browser, native, nativeFiles, expected, real = false }) {
  const rows = [];
  const row = (name, status, why, { counted = true } = {}) => {
    rows.push({ name, status, ok: !counted || !["different", "missing"].includes(status), counted, why });
  };
  const b = browser || {};
  const n = native || {};
  const nCards = n.cards || {};
  const bCards = b.models?.cards || {};
  const endpoints = [...new Set([...Object.keys(nCards), ...Object.keys(bCards)])];

  // ---------------------------------------------------------------- the models
  for (const e of endpoints) {
    const bc = bCards[e];
    const nc = nCards[e];
    if (!bc || !nc) { row(`${e}: model card`, "missing", !bc ? "no card built in the browser" : "no native card"); continue; }
    const c = compareJson(bc, nc, CARD_SET_ASIDE, 0);
    row(`${e}: model card`, c.same ? "same_apart_from_excluded" : "different", c.same
      ? `every field identical (${bc.metric} ${bc.test_score} on ${bc.test} test molecules, trained on ${bc.train} in ${bc.iterations} iterations; dropped ${bc.dropped_unreadable} unreadable, ${bc.dropped_unstandardisable} refused) apart from ${CARD_SET_ASIDE.join(", ")}, which name the process that wrote the model`
      : `differs at ${c.first.join(", ")}: ${c.first.slice(0, 3).map((k) => `${k} browser ${JSON.stringify(k.split(".").reduce((o, x) => o?.[x], bc))} / native ${JSON.stringify(k.split(".").reduce((o, x) => o?.[x], nc))}`).join("; ")}`);
    const dom = b.models?.domains?.[e];
    const same = dom?.sha256 === nc.domain_sha256 && bc.domain_sha256 === nc.domain_sha256;
    row(`${e}: applicability domain (its training molecules' fingerprints)`, same ? "identical" : "different", same
      ? `${e}_domain.npy identical bytes (${dom.bytes} bytes, SHA-256 ${nc.domain_sha256.slice(0, 12)}…)`
      : `browser ${dom?.sha256?.slice(0, 12) ?? "missing"}… (its card ${String(bc.domain_sha256).slice(0, 12)}…) / native ${String(nc.domain_sha256).slice(0, 12)}…`);
    row(`${e}: model file`, "not_compared", `not compared: a pickle's bytes depend on the process that wrote it (wasm32 here, 64-bit natively; ${bc.model_sha256 === nc.model_sha256 ? "here they are the same" : "here they differ"}); what the model predicts is compared below, at full precision`, { counted: false });

    const r = b.rechem?.[e];
    if (!r) row(`${e}: training molecules the browser chemistry refused`, "missing", "the page did not prepare the splits again");
    else {
      const refused = r.train_val.refused.length + r.test.refused.length;
      const unreadable = r.train_val.unreadable.length + r.test.unreadable.length;
      const held = refused === bc.dropped_unstandardisable && unreadable === bc.dropped_unreadable;
      const steps = {};
      for (const k of ["train_val", "test"]) for (const [, , m] of r[k].refused) steps[m] = (steps[m] || 0) + 1;
      const what = `${refused} refused${refused ? ` (${Object.entries(steps).map(([k, v]) => `${k}: ${v}`).join(", ")})` : ""} and ${unreadable} not read of ${r.train_val.n} + ${r.test.n}; the card counts ${bc.dropped_unstandardisable} and ${bc.dropped_unreadable}`;
      if (!held) row(`${e}: training molecules the browser chemistry refused`, "different", `${what}: not the card's counts`);
      else if (!real && refused) row(`${e}: training molecules the browser chemistry refused`, "different", `${what}: none expected for the known-answer archive (simple ring systems and substituents)`);
      else row(`${e}: training molecules the browser chemistry refused`, real ? "reported" : "same", `${what}${real ? "; the native models were trained without them, as the browser's were" : "; none expected"}`);
    }
    const bt = b.probe?.sets?.[`test:${e}`];
    const nt = n.tests?.[e];
    if (!bt || !nt) row(`${e}: test-set predictions (full precision)`, "missing", !bt ? "the probe gave none for the browser" : "none natively");
    else {
      const idx = (ok) => ok.flatMap((v, i) => (v ? [`test row ${i}`] : []));
      const okSame = JSON.stringify(bt.ok) === JSON.stringify(nt.ok);
      const transform = admetTransform(nc);
      const x = exactRows(bt.endpoints?.[e], idx(bt.ok), nt.endpoints?.[e], idx(nt.ok), { transform });
      const w = predictionWords(x, idx(nt.ok).length, transform, `all ${x.compared} kept test molecules of ${nt.n}`);
      row(`${e}: test-set predictions (full precision)`, okSame ? w.status : "different", okSame ? w.why : `the molecules kept differ; ${w.why}`);
    }
  }

  // ---------------------------------------------------------------- the pipeline
  const run = b.pipeline;
  const exp = expected || {};
  const refusedNames = Object.keys(exp.refused || {});
  const nKept = nativeFiles?.kept || {};
  const nRunner = nativeFiles?.runner || {};
  const json = (text) => { try { return JSON.parse(text); } catch { return null; } };
  if (!run?.files) row("pipeline.admet: its outputs", "missing", "no outputs came back from the browser job");
  else {
    const files = run.files;
    const names = [...new Set([...Object.keys(files), ...Object.keys(nKept)])].filter((x) => !ADMET_RECORDS.includes(x)).sort();
    for (const name of names) {
      const f = files[name];
      const nat = nKept[name];
      const shas = { browser: f?.sha256 ?? null, native: shaOf(nat) };
      if (!f || !nat) { row(`pipeline: ${name}`, "missing", !f ? "not written in the browser" : "no native file"); continue; }
      if (shas.browser === shas.native) { row(`pipeline: ${name}`, "identical", `identical bytes to the native run on the kept molecules${name === "admet.tsv" ? ` (${tsvRows(f.text).length - 1} molecules: properties, rules, alerts, every prediction and its domain flag)` : ""}`); continue; }
      if (ADMET_REFUSED_LINES.includes(name) && typeof f.text === "string") {
        const a = linesWithout(f.text, refusedNames);
        const w = linesWithout(textOf(nat), refusedNames);
        if (a.text === w.text && a.dropped.length && !w.dropped.length) {
          row(`pipeline: ${name}`, "same_apart_from_refused", `identical to the native run on the kept molecules apart from the ${a.dropped.length} line${a.dropped.length === 1 ? "" : "s"} naming the molecules the browser refused (${listed(refusedNames, 3)})`);
          continue;
        }
      }
      row(`pipeline: ${name}`, "different", typeof f.text === "string" ? `differs: ${JSON.stringify(compareText(f.text, textOf(nat), () => false).first)}` : "the bytes differ");
    }

    // run.json: the stages, the models it names, the molecules read, refused and not read, the similarity it used
    const rb = json(files["run.json"]?.text);
    const rk = json(nKept["run.json"] && textOf(nKept["run.json"]));
    const rr = json(nRunner["run.json"] && textOf(nRunner["run.json"]));
    if (!rb || !rk) row("pipeline: run.json", "missing", !rb ? "no readable run.json from the browser" : "no readable native run.json");
    else {
      const stages = (r) => (r.stages || []).map((s) => `${s.name}:${s.status}${s.check ? `:${s.check}` : ""}`);
      const same = JSON.stringify(stages(rb)) === JSON.stringify(stages(rk)) && rb.complete === rk.complete;
      row("pipeline: run.json stages", same ? "same" : "different", same ? `${stages(rk).length} stages ended the same way (${stages(rk).join(" ")}); complete ${rk.complete}` : `browser ${stages(rb).join(" ")} (complete ${rb.complete}) / native ${stages(rk).join(" ")} (complete ${rk.complete})`);
      const m = compareJson(rb.models || {}, rk.models || {}, RUN_MODEL_SET_ASIDE, 0);
      row("pipeline: run.json models", m.same ? "same_apart_from_excluded" : "different", m.same ? `the same ${Object.keys(rk.models || {}).length} models named (every field but model_sha256 and chemistry)` : `differs at ${m.first.join(", ")}`);
      const read = JSON.stringify(rb.read) === JSON.stringify(rk.read);
      row("pipeline: run.json molecules read", read ? "same" : "different", read ? `the same ${rk.read.length} molecules, in the same order` : `browser ${listed(rb.read || [])} / native ${listed(rk.read || [])}`);
      const failures = JSON.stringify(rb.failures) === JSON.stringify(exp.unreadable) && JSON.stringify(rr?.failures) === JSON.stringify(exp.unreadable);
      row("pipeline: molecules not read", failures ? "same" : "different", failures ? `${Object.entries(exp.unreadable || {}).map(([k, v]) => `${k} (${v})`).join(", ")}, as natively` : `browser ${JSON.stringify(rb.failures)} / native ${JSON.stringify(rr?.failures)} / expected ${JSON.stringify(exp.unreadable)}`);
      // every refusal expected, each naming the step the browser lacks, and each molecule read by the runner
      const got = rb.refused || {};
      const bad = [];
      for (const [name, step] of Object.entries(exp.refused || {})) {
        const prow = run.chemistry?.not_prepared?.[name];
        if (!(name in got)) bad.push(`${name}: not refused`);
        else if (prow?.need !== "runner" || prow.missing !== step) bad.push(`${name}: its prepared row says need ${prow?.need}, missing ${prow?.missing}, not ${step}`);
        else if (got[name] !== prow.error) bad.push(`${name}: run.json gives the reason "${got[name]}", its prepared row "${prow.error}"`);
      }
      for (const name of Object.keys(got)) if (!(name in (exp.refused || {}))) bad.push(`${name}: refused, not expected (${got[name]})`);
      const runnerRead = new Set((rr?.read) || []);
      const unread = refusedNames.filter((x) => !runnerRead.has(x));
      if (unread.length) bad.push(`the runner's own run did not read ${unread.join(", ")}`);
      row("pipeline: molecules the browser refused", bad.length ? "different" : "same", bad.length ? bad.join("; ") : `the ${refusedNames.length} expected, each for its step (${Object.entries(exp.refused || {}).map(([k, v]) => `${k}: ${v}`).join("; ")}), each prepared row need "runner"; the runner's own run read all ${refusedNames.length}`);
      const sim = rb.similarity || {};
      const offered = run.similarity?.doc?.backend;
      const used = sim.backend === offered && !sim.rejected && Object.values(sim.checked || {}).every((v) => v > 0) && Object.keys(sim.checked || {}).length > 0;
      row("pipeline: Python used the browser's similarities", used ? "same" : "different", used ? `backend ${sim.backend}, ${Object.entries(sim.checked).map(([k, v]) => `${k}: ${v} queries re-checked`).join(", ")}, none rejected` : `run.json similarity ${JSON.stringify(sim)}, the page offered ${offered}`);
    }

    // the chemistry of each molecule the browser prepared, and its alerts
    const bRows = run.chemistry?.rows || {};
    const nRows = n.molecules?.chemistry?.rows || {};
    const keptNames = Object.keys(nRows).filter((x) => !refusedNames.includes(x));
    const chemDiffs = [];
    const alertDiffs = [];
    const ulpDiffs = [];
    let alerts = 0;
    for (const name of [...new Set([...Object.keys(bRows), ...keptNames])]) {
      const x = bRows[name];
      const y = nRows[name];
      if (!x || !y) { chemDiffs.push(`${name}: ${!x ? "not prepared in the browser" : "not prepared natively"}`); continue; }
      for (const k of ["features_sha256", "bits_sha256", "canonical", "atoms_with_h"]) if (x[k] !== y[k]) chemDiffs.push(`${name}: ${k}`);
      const d = compareDescriptors(x.descriptors, y.descriptors);
      for (const k of d.exact) chemDiffs.push(`${name}: ${k} (browser ${x.descriptors?.[k]}, native ${y.descriptors?.[k]})`);
      for (const u of d.ulp) ulpDiffs.push({ name, ...u });
      if (JSON.stringify(x.alerts) !== JSON.stringify(y.alerts)) alertDiffs.push(`${name}: browser ${JSON.stringify(x.alerts)} / native ${JSON.stringify(y.alerts)}`);
      alerts += (y.alerts || []).length;
    }
    const worstUlp = ulpDiffs.reduce((m, u) => Math.max(m, u.ulps), 0);
    const ulpKeys = [...new Set(ulpDiffs.map((u) => u.key))];
    const what = `${keptNames.length} molecules: the float32 feature row the models read, the packed bits, the canonical SMILES and the atom count identical to native RDKit's`;
    row("pipeline: each molecule's chemistry", chemDiffs.length ? "different" : ulpDiffs.length ? "within_tolerance" : "identical", chemDiffs.length ? listed(chemDiffs, 6)
      : ulpDiffs.length ? `${what}; every descriptor identical but ${ulpKeys.join(", ")}, whose float64 value differs on ${ulpDiffs.length} molecule${ulpDiffs.length === 1 ? "" : "s"} by at most ${worstUlp} unit${worstUlp === 1 ? "" : "s"} in the last place (allowed: ${ulpKeys.map((k) => `${k} ${ADMET_ULP_DESCRIPTORS[k]}`).join(", ")}), equal as float32 (${listed(ulpDiffs.map((u) => `${u.name} ${u.ulps}`), 4)}); not identical`
        : `${what}; every descriptor identical in float64`);
    row("pipeline: structural alerts", alertDiffs.length ? "different" : "identical", alertDiffs.length ? listed(alertDiffs, 4) : `${alerts} alerts over ${keptNames.length} molecules (PAINS, Brenk, NIH), each the same catalog and description in the same order`);

    // the predictions at full precision and the domain search, molecule by molecule
    const pm = b.probe?.sets?.molecules;
    const nm = n.molecules || {};
    for (const e of Object.keys(nm.endpoints || {})) {
      if (!pm) { row(`pipeline: ${e} predictions (full precision)`, "missing", "the probe gave none for the browser"); continue; }
      const bNames = (run.molecules?.names || []).filter((_, i) => pm.ok?.[i]);
      const transform = admetTransform(nCards[e]);
      const x = exactRows(pm.endpoints?.[e], bNames, nm.endpoints[e], nm.names || [], { transform });
      const w = predictionWords(x, keptNames.length, transform, `${x.compared} molecules`);
      row(`pipeline: ${e} predictions (full precision)`, w.status, w.why);
      const s = run.similarity?.endpoints?.[e];
      if (!s) { row(`pipeline: ${e} domain search (the browser's kernels)`, "missing", "no similarity file for it"); continue; }
      const y = exactRows(s, run.names || [], nm.endpoints[e], nm.names || [], { fields: ["arg", "inter", "union"] });
      const okd = !y.diffs.length && y.compared === keptNames.length;
      row(`pipeline: ${e} domain search (the browser's kernels)`, okd ? "identical" : "different", okd ? `${y.compared} molecules × ${nCards[e]?.train ?? "?"} training molecules: the nearest one, the intersection and the union (${run.similarity.doc?.backend}) identical to native numpy's` : JSON.stringify(y.diffs.slice(0, 4)));
    }
  }

  // ---------------------------------------------------------------- the same run with the GPU forced
  const g = b.forced;
  if (!g?.files || !run?.files) row("forced GPU: pipeline.admet once more", "missing", !g?.files ? "no outputs came back from the forced-GPU run" : "the first run gave none to compare with");
  else {
    const names = [...new Set([...Object.keys(g.files), ...Object.keys(run.files)])].filter((x) => !ADMET_RECORDS.includes(x)).sort();
    const differ = names.filter((x) => g.files[x]?.sha256 !== run.files[x]?.sha256 || !g.files[x]);
    row("forced GPU: every output", differ.length ? "different" : "identical", differ.length ? `not the first run's bytes: ${differ.join(", ")}` : `${names.length} outputs identical bytes to the first run's (${names.join(", ")})`);
    const eps = Object.keys(run.similarity?.endpoints || {});
    const simDiff = eps.filter((e) => g.similarity?.endpoints?.[e]?.sha256 !== run.similarity.endpoints[e].sha256);
    row("forced GPU: the similarity files", simDiff.length || !eps.length ? "different" : "identical", simDiff.length || !eps.length ? `differ: ${simDiff.join(", ") || "none written"}` : `${eps.length} endpoints' nearest index, intersection and union identical to the first run's (${run.similarity.doc?.backend} there, ${g.similarity?.doc?.backend} here)`);
    const rg = json(g.files["run.json"]?.text);
    const r1 = json(run.files["run.json"]?.text);
    const st = (r) => (r?.stages || []).map((s) => `${s.name}:${s.status}${s.check ? `:${s.check}` : ""}`).join(" ");
    const simOk = rg?.similarity?.backend === "webgpu" && !rg.similarity.rejected;
    row("forced GPU: run.json", st(rg) === st(r1) && simOk ? "same" : "different", `stages ${st(rg) === st(r1) ? "the same" : `differ (${st(rg)} / ${st(r1)})`}; similarity ${JSON.stringify(rg?.similarity)}`);
    const domain = (job) => (job.stages || []).find((x) => x.id === "domain");
    const calls = [].concat(domain(g)?.compute || []);
    const first = [].concat(domain(run)?.compute || []);
    const gpuOk = calls.length > 0 && calls.every((f) => f.backend === "webgpu" && f.forced === "webgpu" && f.accelerated !== true && f.gpu?.adapter?.kind === "software" && f.gpu?.validated === true);
    row("forced GPU: the domain stage's compute facts", gpuOk ? "same" : "different", gpuOk
      ? `${calls.length} calls on WebGPU, forced by the harness switch, on a software adapter (${[calls[0].gpu.adapter.vendor, calls[0].gpu.adapter.architecture, calls[0].gpu.adapter.description].filter(Boolean).join(" ") || "no name"}), each result validated on the CPU, never accelerated (${calls[0].basis?.slice(0, 160)})`
      : `facts: ${JSON.stringify(calls.map((f) => ({ backend: f.backend, forced: f.forced, accelerated: f.accelerated, adapter: f.gpu?.adapter?.kind, validated: f.gpu?.validated, error: f.gpu?.error }))).slice(0, 500)}`);
    const cpuOk = first.length > 0 && first.every((f) => ["wasm-simd", "js"].includes(f.backend) && f.accelerated !== true && !f.forced);
    row("first run: the domain stage's compute facts", cpuOk ? "same" : "different", cpuOk ? `${first.length} calls on ${[...new Set(first.map((f) => f.backend))].join(", ")} (no calibration on this device: the GPU is not chosen), not accelerated` : JSON.stringify(first.map((f) => ({ backend: f.backend, forced: f.forced, accelerated: f.accelerated }))).slice(0, 500));
    const used = g.receipt?.device?.used;
    row("forced GPU: the job receipt", used === "cpu+webgpu" ? "same" : "different", `device.used ${used}; first run ${run.receipt?.device?.used}; evidence ${g.receipt?.device?.evidence}`);
  }
  return rows;
}

// ------------------------------------------------------------------------- the docking job (science.mjs --mode dock)
//
// pipeline.dock in its prepared mode as a browser job (the Python stages in Pyodide, AutoDock Vina 1.2.7 compiled to
// WebAssembly in dedicated workers) against the native runs on the same files (dock_native.py). What is exact is held
// exactly: the plan of the Vina runs and the texts each was given (dock_read's, natively), the box, the inputs'
// digests and the ligand table; the analysis, with the Python stages run natively on the browser's own Vina results
// (PrecomputedVina): every output the browser's bytes, run.json and stages.json apart from what names the process and
// its time (DOCK_RUN_SET_ASIDE); Vina's energy model on the poses the browser found: each top pose scored by native
// Vina and by the WebAssembly engine, every value the same float64 bits. What a seeded Monte Carlo search finds is not
// bit-reproducible across engines and CPUs (a last-bit difference in libm or in floating-point contraction grows into
// another pose), so the two searches are held to what does not depend on it: both runs complete, both redock the
// reference within REDOCK_RMSD, the same ligands docked; each ligand's best score against native's at the same seed is
// reported beside the band measured between native runs (DOCK_SEARCH_BAND), not counted. The rule over seeds 1-8 is
// enforced where those seeds are run (web/test/scientific/vina.test.mjs, on vina_parity.json). Last, the receipt: the
// engine named, every Vina run's compute facts vina-wasm in a worker, the device note naming the dedicated workers, and
// their memory said to be not measured.

/** run.json and stages.json fields that name the process that wrote them or its time, not the run: set aside. The
 * inputs are recorded at their paths (the job's directory in the browser, WORK/raw natively): compared by file name. */
export const DOCK_RUN_SET_ASIDE = Object.freeze(["started", "finished", "python", "inputs", "stages.*.seconds"]);
/** Files the run writes about itself: compared through their fields; the browser job's receipt has no native peer. */
const DOCK_RECORDS = ["run.json", "stages.json", "job_receipt.json"];
/** bioagent.docking.prepared.REDOCK_RMSD: the reference's top pose within this many Å of its crystal pose (atom order). */
export const REDOCK_RMSD = 2.0;
/**
 * How far one seeded search's best score moves when only the floating-point details change, measured over seeds 1-8
 * (exhaustiveness 8) on the six fixture ligands: the median |Δbest| per ligand against native glibc is 0.002-0.086
 * kcal/mol for the same wheel with glibc's FMA paths off, 0.002-0.046 with musl's libm, and 0.002-0.077 for this
 * WebAssembly build. A single seed can differ by more (another local minimum): reported, not counted.
 */
export const DOCK_SEARCH_BAND = "native against native at the same seed, other libm or no FMA: median |Δbest| 0.002-0.086 kcal/mol per ligand over seeds 1-8 (WebAssembly: 0.002-0.077)";

const baseName = (p) => String(p).split(/[\\/]/).filter(Boolean).at(-1);
/** run.json's inputs by file name → {name: sha256}. */
const inputsByName = (inputs) => Object.fromEntries(Object.entries(inputs || {}).map(([k, v]) => [baseName(k), v]));

/**
 * The docking job of the page (science-check.mjs out.dock: {job: its record with files, results, plan, receipt,
 * stages; scored: the WebAssembly engine's scores of its top poses}) against the native side (dock_native.py native:
 * {runs: {analysis, runner}, scores}; nativeFiles: {plan, analysis, runner} → {path: Buffer}) and what is expected
 * (`expected`: {files: {name: {sha256}} as dock_native.py prepare copied them, engine: the browser engine as vina.js
 * names it ({name, version, build, wasm_sha256})}) → rows [{name, status, ok, counted, why}].
 */
export function compareDock({ browser, native, nativeFiles, expected }) {
  const rows = [];
  const row = (name, status, why, { counted = true } = {}) => {
    rows.push({ name, status, ok: !counted || !["different", "missing"].includes(status), counted, why });
  };
  const job = browser?.job;
  const n = native || {};
  const nf = nativeFiles || {};
  const exp = expected || {};
  const json = (text) => { try { return JSON.parse(text); } catch { return null; } };
  if (!job?.files) {
    row("the pipeline.dock browser job", "missing", "no outputs came back from the browser job");
    return rows;
  }

  // ---------------------------------------------------------------- the plan
  const planB = job.plan || {};
  const planN = nf.plan || {};
  const plan = json(planB["dock_plan.json"]?.text);
  if (!plan || !planN["dock_plan.json"]) row("plan: dock_plan.json", "missing", !plan ? "the browser's plan was not read back" : "no native plan");
  else {
    const same = planB["dock_plan.json"].sha256 === shaOf(planN["dock_plan.json"]);
    const c = same ? null : compareJson(plan, json(textOf(planN["dock_plan.json"])), [], 0);
    row("plan: dock_plan.json", same ? "identical" : "different", same
      ? `identical bytes to dock_read's natively: ${plan.ligands.length} ligands${plan.reference ? ` and the reference ${plan.reference.name}` : ""}, box (${plan.box.center.join(", ")}) size (${plan.box.size.join(", ")}) Å from the ${plan.box.source}, ${JSON.stringify(plan.params)}`
      : `differs at ${c.first.join(", ") || "its bytes"}`);
    const texts = [plan.receptor, ...(plan.ligands || []), ...(plan.reference ? [plan.reference] : [])].map((x) => baseName(x.file));
    const differ = texts.filter((t) => !planB[t] || !planN[t] || planB[t].sha256 !== shaOf(planN[t]) || planB[t].sha256 !== `sha256:${[plan.receptor, ...plan.ligands, plan.reference].find((x) => x && baseName(x.file) === t)?.sha256}`);
    row("plan: the texts Vina was given", differ.length ? "different" : "identical", differ.length
      ? `not the same bytes, or not at the plan's SHA-256: ${differ.join(", ")}`
      : `${texts.length} texts (${texts.join(", ")}) identical bytes to dock_read's natively, each at the SHA-256 the plan records`);
  }

  // ---------------------------------------------------------------- the run record against the native run
  const rb = json(job.files["run.json"]?.text);
  const rn = json(nf.runner?.["run.json"] && textOf(nf.runner["run.json"]));
  const runner = n.runs?.runner;
  if (!runner) row("native: the runner's command", "missing", "dock_native.py did not run it");
  else {
    const failed = Object.entries(runner.checks || {}).filter(([, v]) => v !== "ok");
    row("native: the runner's command", runner.exit === 0 && !failed.length ? "same" : "different",
      `${(runner.command || []).join(" ")}: exit ${runner.exit} in ${runner.seconds} s; the kind's checks ${failed.length ? `failed: ${failed.map(([k, v]) => `${k}: ${v}`).join("; ")}` : `passed (${Object.keys(runner.checks || {}).join(", ")})`}${runner.exit !== 0 ? `; ${String(runner.stderr || "").slice(-300)}` : ""}`);
  }
  if (!rb || !rn) row("run.json: the run's inputs and settings", "missing", !rb ? "no readable run.json from the browser" : "no readable run.json from the native run");
  else {
    const pick = (r) => ({ box: r.box, config: r.config, receptor: r.receptor, ligands: r.ligands, reference: r.reference });
    const c = compareJson(pick(rb), pick(rn), [], 0);
    row("run.json: box, settings, receptor, ligands and reference", c.same ? "identical" : "different", c.same
      ? `the same box (${rb.box?.source}), settings (${Object.entries(rb.config || {}).filter(([, v]) => v !== null && typeof v !== "object").map(([k, v]) => `${k} ${v}`).join(", ")}), receptor (${rb.receptor?.atoms} atoms, SHA-256 ${String(rb.receptor?.sha256).slice(0, 12)}…) and ligand table (${(rb.ligands || []).map((l) => `${l.name} ${l.heavy_atoms}/${l.torsions}`).join(", ")}; heavy atoms/torsions) as the native run`
      : `differs at ${c.first.join(", ")}`);
    const ib = inputsByName(rb.inputs);
    const inn = inputsByName(rn.inputs);
    const want = Object.fromEntries(Object.entries(exp.files || {}).map(([k, v]) => [k, v.sha256]));
    const sameInputs = JSON.stringify(Object.entries(ib).sort()) === JSON.stringify(Object.entries(inn).sort()) && JSON.stringify(Object.entries(ib).sort()) === JSON.stringify(Object.entries(want).sort());
    row("run.json: the inputs' digests", sameInputs ? "identical" : "different", sameInputs
      ? `${Object.keys(ib).length} files (${Object.keys(ib).sort().join(", ")}), each at the SHA-256 of the file served, in both runs`
      : `browser ${JSON.stringify(ib)} / native ${JSON.stringify(inn)} / served ${JSON.stringify(want)}`);
    const eb = rb.engine || {};
    const en = rn.engine || {};
    const engineOk = compareJson(eb, exp.engine || {}, [], 0).same && en.name === eb.name && en.version === eb.version && en.build === "native"
      && rb.versions?.vina === exp.engine?.version && rn.versions?.vina === exp.engine?.version;
    row("run.json: the engine", engineOk ? "same" : "different", engineOk
      ? `browser ${eb.name} ${eb.version}, ${eb.build} build (SHA-256 ${String(eb.wasm_sha256).slice(0, 12)}…, the pin); native ${en.name} ${en.version}, ${en.build} build`
      : `browser ${JSON.stringify(eb)} (versions ${JSON.stringify(rb.versions)}) / native ${JSON.stringify(en)} (versions ${JSON.stringify(rn.versions)}) / the pin ${JSON.stringify(exp.engine)}`);
  }

  // ---------------------------------------------------------------- the analysis on the browser's own Vina results
  const analysis = n.runs?.analysis;
  const na = nf.analysis || {};
  if (!analysis) row("analysis: run_dock natively on the browser's Vina results", "missing", "dock_native.py did not run it");
  else {
    const e = analysis.ended || {};
    row("analysis: run_dock natively on the browser's Vina results", analysis.exit === 0 ? "same" : "different", analysis.exit === 0
      ? `PrecomputedVina read the browser's ${Object.keys(job.results || {}).length} results; complete, in ${analysis.seconds} s (CPython)`
      : `exit ${analysis.exit}: ${e.refused || (e.failed || []).join(", ") || "no reason given"}`);
  }
  const names = [...new Set([...Object.keys(job.files), ...Object.keys(na)])].filter((x) => !DOCK_RECORDS.includes(x)).sort();
  for (const name of names) {
    const f = job.files[name];
    const nat = na[name];
    if (!f || !nat) { row(`analysis: ${name}`, "missing", !f ? "not written in the browser" : "not written natively from the browser's results"); continue; }
    if (f.sha256 === shaOf(nat)) { row(`analysis: ${name}`, "identical", `identical bytes (${f.bytes} bytes)`); continue; }
    row(`analysis: ${name}`, "different", typeof f.text === "string" ? `differs: ${JSON.stringify(compareText(f.text, textOf(nat), () => false).first)}` : "the bytes differ");
  }
  for (const rec of ["run.json", "stages.json"]) {
    const b = json(job.files[rec]?.text);
    const a = json(na[rec] && textOf(na[rec]));
    if (!b || !a) { row(`analysis: ${rec}`, "missing", !b ? `no readable ${rec} from the browser` : `no readable ${rec} natively`); continue; }
    const c = compareJson(b, a, DOCK_RUN_SET_ASIDE, 0);
    const inputs = rec !== "run.json" || JSON.stringify(Object.entries(inputsByName(b.inputs)).sort()) === JSON.stringify(Object.entries(inputsByName(a.inputs)).sort());
    row(`analysis: ${rec}`, c.same && inputs ? "same_apart_from_excluded" : "different", c.same && inputs
      ? `every field identical apart from ${DOCK_RUN_SET_ASIDE.filter((k) => rec === "run.json" || k.startsWith("stages")).join(", ")} (the process and its time${rec === "run.json" ? "; the inputs compared by file name" : ""})`
      : `differs at ${[...c.first, ...(inputs ? [] : ["inputs (by file name)"])].join(", ")}`);
  }

  // ---------------------------------------------------------------- Vina's energy model on the browser's own poses
  const scoredB = browser?.scored || [];
  const scoredN = new Map((n.scores || []).map((s) => [s.key, s]));
  if (!scoredB.length) row("score: the browser's top poses", "missing", "the page scored none");
  for (const s of scoredB) {
    const label = `score: ${s.name} (${s.key}), its top pose`;
    const t = scoredN.get(s.key);
    if (s.error || !t || t.error) { row(label, "missing", s.error ? `the WebAssembly engine: ${s.error}` : !t ? "not scored natively" : `native Vina: ${t.error}`); continue; }
    const why = [];
    if (t.first_pose !== true) why.push(`the pose scored is not the first MODEL of the browser's result as bioagent.docking.prepared reads it${t.first_pose_error ? ` (${t.first_pose_error})` : ""}`);
    if (s.receptor_sha256 !== t.receptor_sha256 || s.ligand_sha256 !== t.ligand_sha256) why.push("not the same receptor and pose texts");
    const differ = (s.bits || []).map((b, i) => (b === t.bits?.[i] ? null : i)).filter((i) => i !== null);
    if ((s.bits || []).length !== (t.bits || []).length || !s.bits?.length) why.push(`${s.bits?.length ?? 0} values / ${t.bits?.length ?? 0} natively`);
    else if (differ.length) why.push(`values ${differ.join(", ")} differ: ${differ.slice(0, 3).map((i) => `${s.energies[i]} (${s.bits[i]}) / ${t.energies[i]} (${t.bits[i]})`).join("; ")}`);
    row(label, why.length ? "different" : "identical", why.length ? why.join("; ") : `Vina.score() bit-identical, WebAssembly and native: all ${s.bits.length} values (total ${s.energies[0]} kcal/mol), the same float64 bits`);
  }

  // ---------------------------------------------------------------- the searches
  if (rb && rn) {
    const complete = rb.complete === true && rn.complete === true;
    row("search: both runs complete", complete ? "same" : "different", `browser complete ${rb.complete}, native complete ${rn.complete}`);
    const vb = rb.validation || {};
    const vn = rn.validation || {};
    const passed = vb.passed === true && vn.passed === true && vb.top_pose_rmsd <= REDOCK_RMSD && vn.top_pose_rmsd <= REDOCK_RMSD;
    const r2 = (x) => (typeof x === "number" ? x.toFixed(2) : String(x));
    row(`search: ${vb.ligand ?? "the reference"} redocked within ${REDOCK_RMSD} Å`, passed ? "same" : "different",
      `top pose ${r2(vb.top_pose_rmsd)} Å from the crystal pose in the browser (score ${vb.top_score}), ${r2(vn.top_pose_rmsd)} Å natively (score ${vn.top_score}); ${vb.rmsd || "atom order"}`);
    const docked = (r) => (r.results || []).map((x) => x.ligand).sort();
    const sameDocked = JSON.stringify(docked(rb)) === JSON.stringify(docked(rn)) && !Object.keys(rb.failures || {}).length && !Object.keys(rn.failures || {}).length;
    row("search: the ligands docked", sameDocked ? "same" : "different", sameDocked
      ? `all ${docked(rb).length} in both runs (${docked(rb).join(", ")}), none refused`
      : `browser ${docked(rb).join(", ")} (failures ${JSON.stringify(rb.failures)}) / native ${docked(rn).join(", ")} (failures ${JSON.stringify(rn.failures)})`);
    const best = (r) => new Map((r.results || []).map((x) => [x.ligand, x.energies?.[0]?.[0]]));
    const bb = best(rb);
    const bn = best(rn);
    const deltas = [];
    for (const l of rb.ligands || []) {
      const x = bb.get(l.name);
      const y = bn.get(l.name);
      if (typeof x !== "number" || typeof y !== "number") continue;
      deltas.push(Math.abs(x - y));
      row(`search: ${l.name} best score`, "reported", `browser ${x.toFixed(3)}, native ${y.toFixed(3)} kcal/mol at seed ${rb.config?.seed}: |Δ| ${Math.abs(x - y).toFixed(3)}`, { counted: false });
    }
    if (deltas.length) {
      const sorted = [...deltas].sort((a, b) => a - b);
      const median = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
      row("search: |Δbest| over the ligands", "reported", `median ${median.toFixed(3)}, largest ${sorted.at(-1).toFixed(3)} kcal/mol at one seed; the band: ${DOCK_SEARCH_BAND}`, { counted: false });
    }
  }

  // ---------------------------------------------------------------- the receipt
  const receipt = job.receipt || null;
  const tasks = (plan?.ligands?.length ?? 0) + (plan?.reference ? 1 : 0);
  if (!receipt) row("receipt", "missing", "no receipt came back from the browser job");
  else {
    const verified = receipt.assurance === "outputs_verified" && receipt.device?.evidence === "verified";
    row("receipt: outputs_verified, device evidence verified", verified ? "same" : "different", `assurance ${receipt.assurance}, device evidence ${receipt.device?.evidence}`);
    const engines = Array.isArray(receipt.engine) ? receipt.engine : [];
    const vina = engines.find((e) => e?.name === exp.engine?.name || e?.id === exp.engine?.name);
    const engineOk = Boolean(vina) && vina.version === exp.engine?.version && vina.build === exp.engine?.build && vina.wasm_sha256 === exp.engine?.wasm_sha256 && (vina.stages || []).includes("dock");
    row("receipt: the engine", engineOk ? "same" : "different", engineOk
      ? `${vina.name} ${vina.version}, ${vina.build} build, SHA-256 ${vina.wasm_sha256.slice(0, 12)}… (the pin), for the stage ${vina.stages.join(", ")}`
      : `receipt engine ${JSON.stringify(receipt.engine)}; expected ${JSON.stringify(exp.engine)} for the dock stage`);
    const stage = (job.stages || []).find((x) => x.id === "dock");
    const facts = [].concat(stage?.compute || []);
    const factOk = (f) => f.op === "vina.dock" && f.backend === "vina-wasm" && f.placement === "browser" && f.where === "worker" && f.gpu === null && f.accelerated !== true;
    const workers = [...new Set(facts.map((f) => f.worker).filter(Boolean))].sort();
    const computeOk = !stage?.reused && facts.length === tasks && tasks > 0 && facts.every(factOk);
    row("receipt: the dock stage's compute facts", computeOk ? "same" : "different", computeOk
      ? `${facts.length} Vina runs, each vina.dock on vina-wasm in a worker (${workers.join(", ")}), no GPU, not accelerated; ${facts[0].basis}${stage?.record?.why ? `; ${stage.record.why}` : ""}`
      : `${facts.length} facts for ${tasks} runs${stage?.reused ? " (the stage was reused)" : ""}: ${JSON.stringify(facts.filter((f) => !factOk(f)).slice(0, 2)).slice(0, 400)}`);
    const note = String(receipt.device?.note || "");
    const named = `${exp.engine?.name} ${exp.engine?.version} (WebAssembly) in ${workers.length} dedicated Web Worker${workers.length === 1 ? "" : "s"}, one thread each`;
    row("receipt: the device note", workers.length && note.includes(named) ? "same" : "different", `${note.includes(named) ? "names" : "does NOT name"} "${named}": ${note.slice(0, 300)}`);
    const dedicated = [].concat(receipt.resources?.memory?.dedicated || []);
    const memOk = dedicated.length === 1 && dedicated[0].wasm_peak_bytes === null && /^not measured/.test(String(dedicated[0].reason)) && dedicated[0].workers === workers.length;
    row("receipt: the dedicated workers' memory", memOk ? "same" : "different", memOk
      ? `recorded as not measured for ${dedicated[0].engine} (${dedicated[0].workers} workers): ${dedicated[0].reason}`
      : `resources.memory.dedicated ${JSON.stringify(dedicated).slice(0, 300)}`);
  }
  const results = job.results || {};
  const bad = [];
  const want = [...(plan?.reference ? [plan.reference] : []), ...(plan?.ligands || [])];
  for (const t of want) {
    const doc = json(results[t.key]?.text);
    if (!doc) { bad.push(`${t.key}: not read back`); continue; }
    const params = { ...plan.params, center: plan.box.center, size: plan.box.size };
    if (doc.schema !== "tcmstudio.vina-result/1" || doc.key !== t.key) bad.push(`${t.key}: not a tcmstudio.vina-result/1 of it`);
    else if (!compareJson(doc.engine, exp.engine || {}, [], 0).same) bad.push(`${t.key}: engine ${JSON.stringify(doc.engine)}`);
    else if (doc.receptor_sha256 !== plan.receptor.sha256 || doc.ligand_sha256 !== t.sha256) bad.push(`${t.key}: another receptor or ligand text than the plan's`);
    else if (!compareJson(doc.params, params, [], 0).same || Object.keys(doc.params || {}).length !== Object.keys(params).length) bad.push(`${t.key}: params ${JSON.stringify(doc.params)}`);
    else if (doc.error !== undefined) bad.push(`${t.key}: Vina refused it (${doc.error})`);
  }
  const extra = Object.keys(results).filter((k) => !want.some((t) => t.key === k));
  if (extra.length) bad.push(`results of no planned run: ${extra.join(", ")}`);
  row("results: one Vina result per planned run", !want.length ? "missing" : bad.length ? "different" : "same", !want.length ? "no plan to hold them to" : bad.length ? bad.join("; ")
    : `${want.length} results (${want.map((t) => t.key).join(", ")}), each tcmstudio.vina-result/1 of the plan's receptor and ligand text and parameters, from the pinned WebAssembly build`);
  return rows;
}
