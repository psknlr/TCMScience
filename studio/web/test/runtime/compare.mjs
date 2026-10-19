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
// Their numbers come from iterative fits (the DESeq2 dispersion search, a grid then golden sections on a flat
// likelihood; Harmony; the UMAP layout) that turn a last-bit difference between the native build (its numpy, scipy and
// BLAS) and Pyodide's wasm32 one into a difference the tables' six printed digits can show, so a numeric cell is held to
// OMICS_RULE. Measured in Pyodide 314.0.7 against native numpy and scipy over three configurations, the largest
// difference used 0.157 of it (a pseudobulk p-value), and the tighter 1e-7 + 1e-5·max was exceeded 1.55-fold. Each row
// says the rule and how much of it the largest difference used. Everything else is exact: headers, gene and cell order, NA positions, True/False flags, every text cell, the
// set of significant genes (a gene that crosses alpha fails the row, unless both values are within the rule of alpha,
// when it is named, not hidden), each cell's cluster and cell type, the clusters table. The UMAP layout is a picture of
// the graph that amplifies last-bit differences: its coordinates (cells.tsv umap_1, umap_2) and the figures are not
// compared; its structure is (umapAgreement). report.html is compared with its figures set aside.

export const OMICS_RULE = "|a − b| ≤ 1e-6 + 1e-4·max(|a|,|b|) + one unit in the sixth significant digit of max(|a|,|b|) (the tables print %.6g)";
/** The UMAP's structure: each cell's UMAP_K nearest neighbours in the layout, the share of them in its cluster. */
export const UMAP_K = 15;
export const UMAP_AGREEMENT_MIN = 0.95;
export const UMAP_AGREEMENT_GAP = 0.01;
const UMAP_COLUMNS = ["umap_1", "umap_2"];
/** Files the run writes about itself (times, paths, versions): compared through their fields (record rows). */
const RECORDS = ["run.json", "stages.json", "job_receipt.json"];

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
 * is not the same bytes is "not_compared" and not counted.
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
        ? "not compared: a UMAP figure (its points are the layout, which amplifies last-bit differences; the layout's structure is checked below)"
        : "not compared: a figure, its coordinates printed at two or three digits; the numbers it draws are compared in the tables", { ...shas, counted: false });
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
    const [got, want, figures] = name === "report.html" ? [withoutFigures(b.text), withoutFigures(textN(name)), true] : [b.text, textN(name), false];
    if (figures && got === want) { row(name, "same_apart_from_figures", "identical with each inline figure (<svg>) set aside", shas); continue; }
    let worstText = 0;
    const t = compareText(got, want, (x, y) => { const f = omicsFraction(x, y); worstText = Math.max(worstText, f); return f <= 1; });
    if (t.same) row(name, "within_tolerance", `the same words${figures ? " with the figures set aside" : ""}, the numbers within the rule (the largest at ${fmt(worstText)} of the bound); not identical bytes`, { ...shas, fraction: worstText });
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
