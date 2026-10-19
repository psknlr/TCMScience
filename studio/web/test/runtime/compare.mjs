// How the browser's research exports are held to the native run's (science.mjs --mode gqd). Identical bytes are the
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

/** Two texts word by word, numbers within RELATIVE → {same, first: [[got, want]...]}. */
export function compareText(got, want) {
  const split = (t) => t.split(/([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)/);
  const a = split(got);
  const b = split(want);
  const first = [];
  if (a.length !== b.length) return { same: false, first: [["(word count)", `${a.length} / ${b.length}`]] };
  for (let i = 0; i < a.length && first.length < 5; i++) {
    if (a[i] === b[i]) continue;
    const na = i % 2 ? numberOf(a[i]) : null;
    const nb = i % 2 ? numberOf(b[i]) : null;
    if (na !== null && nb !== null && relative(na, nb) <= RELATIVE) continue;
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
