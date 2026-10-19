// The RNA-seq (count matrix) and single-cell analyses, run in this browser (studio/docs/BROWSER_JOBS.md). They are the
// runner's pipeline.rnaseq, in its count-matrix mode, and pipeline.scrna (studio/runner/src/tcmstudio/kinds.py) with
// the same Python doing the work: tcmstudio.browser_jobs runs bioagent's pipeline (run_rnaseq_counts, run_scrna) in a
// job worker holding Pyodide's numpy and scipy, then verify_pipeline holds the outputs to what the runner's job
// requires of them (its required artefacts and its validator, the pipeline's own verify_run).
//
// What runs here, and what is refused with the runner named as what would run it:
//   pipeline.rnaseq  a count matrix (genes × samples, integers) and a sample table; the built-in DESeq2 method, VST,
//                    PCA, distances, plots, report. Refused: a FASTQ run (no counts), PyDESeq2.
//   pipeline.scrna   a count table (CSV, TSV or TXT, gzipped or not; genes in rows) or a sample sheet of them, and an
//                    optional marker table; the built-in analysis with built-in Harmony or none, built-in DE.
//                    Refused: Scanpy, scVI, 10x HDF5 (.h5) and AnnData (.h5ad).
//
// The inputs are the project's own files (core/store.js, in IndexedDB). Each is copied into this origin's private file
// system under the job (tcmstudio/jobs/<id>/inputs/<name>), hashed as it is written, and used only when it has the
// SHA-256 the project recorded for it; then it is mounted read-only in the worker. A sample sheet's files are found the
// same way and the sheet is rewritten to name the copies, as the runner's _stage_sheet does.
//
// inputs → analysis → verify. The analysis stage is checkpointed (its parameters and its inputs' digests): a resumed
// job reuses it when every output it wrote still re-hashes to its recorded digest.

import { canonicalJson, isPlainObject, sha256Hex } from "../../core/util.js";
import { Sha256 } from "../data/digest.js";
import { bareSha256 } from "../engines/evidence.js";
import { limitationLines, onPythonEvent, refusal, storageRefusal, unwrap, work } from "./shared.js";

/** Pyodide packages the built-in paths import (beyond the standard library): nothing else is loaded for them. */
const PACKAGES = ["numpy", "scipy"];
/** The runner's bounds (the catalog's parameter schemas, tcmstudio.kinds). */
const ALPHA = [0.0001, 0.5];
const RESOLUTION = [0.05, 5];
const SEED = [0, 2 ** 31 - 1];
const N_TOP_GENES = [50, 10000];
/** The job copies its inputs into OPFS and the pipeline writes about as much again: room for three times them. */
const STORAGE_FACTOR = 3;
const RUNNER = "Connect the local runner";
const TABLE = /\.(csv|tsv|txt)(\.gz)?$/i;
const SHEET = /\.(csv|tsv|txt)$/i;

/** The browser's line about where the numbers were computed; the versions are the worker's own (never written here). */
function computedLine(versions) {
  const v = (name) => (versions?.[name] ? `${name} ${versions[name]}` : `${name} (its version was not reported)`);
  return `Computed in this browser with Pyodide's ${v("numpy")} and ${v("scipy")}; the native run is the reference, and its numbers can differ in the last digits (held to a stated tolerance)`;
}
const UMAP_LINE = "The UMAP layout is a picture of the graph: it amplifies last-digit differences and is not compared with the native run's";

// wasm32 holds a job worker to about 4 GB. Running out is said as what it is, a refusal the runner answers: Python's
// MemoryError (numpy's _ArrayMemoryError among them), or the worker's own allocation failing (Emscripten's OOM abort,
// a Memory.grow past the limit, which Pyodide reports as a fatal error)
const OUT_OF_MEMORY = /\bMemoryError\b|_ArrayMemoryError|\bOOM\b|out of memory|Cannot enlarge memory|Maximum memory size exceeded|Memory\.grow/i;
const MEMORY_HINT = "Connect the local runner: it runs the same analysis natively, without the browser's memory limit.";

/** The typed refusal (type "refused", need "memory") for a worker call that ran out of memory, or null. */
function memoryRefusal(err, what) {
  const message = String(err?.message || "");
  // Python's own refusal for memory keeps its words; it gets the hint
  if (err?.type === "refused" && err.need === "memory") {
    if (!err.hint) err.hint = MEMORY_HINT;
    return err;
  }
  if (!err || err.type === "refused" || !OUT_OF_MEMORY.test(message)) return null;
  const e = new Error(`${what} ran out of memory: a browser job worker holds at most about 4 GB (wasm32); run it on the runner (${message.slice(0, 300)})`);
  e.type = "refused";
  e.need = "memory";
  e.hint = MEMORY_HINT;
  return e;
}

/** A Python function in the stage's worker → its result; running out of memory is a refusal, not a runtime error. */
async function pythonCall(ctx, lease, fn, args, [base, span], what) {
  try {
    return unwrap(await lease.call("run", { fn, args }, { signal: ctx.signal, onEvent: (ev) => onPythonEvent(ctx, ev, base, span) }), fn);
  } catch (err) {
    throw memoryRefusal(err, what) || err;
  }
}

// ------------------------------------------------------------------------------------------------- the two kinds

const RNASEQ = {
  kind: "pipeline.rnaseq",
  fn: "run_rnaseq_counts",
  // the runner's artefact names (kinds.py) for the files it requires; every other output is named by its path
  artefacts: { run: "run.json", report: "report.html", results: "deseq2_results.tsv", limitations: "limitations.md" },
  params: ["counts", "samples", "design", "contrast", "alpha", "de_backend", "transcripts", "annotation", "genome", "engine", "trimmer"],
  files: ["counts", "samples"],
  lines: [],
};

const SCRNA = {
  kind: "pipeline.scrna",
  fn: "run_scrna",
  artefacts: { run: "run.json", report: "report.html", clusters: "clusters.tsv", cells: "cells.tsv", limitations: "limitations.md" },
  params: ["source", "doublets", "analysis_backend", "integration", "resolution", "seed", "markers", "root", "contrast", "n_top_genes"],
  files: ["source", "markers"],
  lines: [UMAP_LINE],
};

const given = (v) => v !== undefined && v !== null && v !== "";
// a number or a choice left out is its default (null as well, as the runner's schema check drops it); an empty text
// is not a number or one of the choices, and the runner refuses it, so it is checked
const present = (v) => v !== undefined && v !== null;
const ext = (name) => String(name || "").toLowerCase();

/**
 * Why this pipeline.rnaseq call needs the runner, from its parameters alone → {message, hint}, or null when the
 * browser can run it. The job service's preflight says it again with the files in view; the router asks this before
 * it places a call.
 */
export function rnaseqNeedsRunner(params) {
  const p = isPlainObject(params) ? params : {};
  if (!given(p.counts)) {
    return {
      message: "an RNA-seq run from FASTQ (a sample sheet of FASTQ files, with quantification) runs only on the runner; this browser runs the count-matrix mode (counts and a sample table)",
      hint: `${RUNNER}: it runs the full pipeline from FASTQ (QC, trimming, quantification, then the same differential expression). Or give counts: a gene × sample count table.`,
    };
  }
  if (p.de_backend === "pydeseq2") {
    return {
      message: "PyDESeq2 is not in this browser's Python; the built-in DESeq2 implementation is (de_backend builtin)",
      hint: `${RUNNER} with PyDESeq2 installed, or leave de_backend at builtin.`,
    };
  }
  return null;
}

/** Why this pipeline.scrna call needs the runner, from its parameters alone → {message, hint}, or null. */
export function scrnaNeedsRunner(params) {
  const p = isPlainObject(params) ? params : {};
  if (p.analysis_backend === "scanpy") {
    return {
      message: "Scanpy (with anndata, leidenalg and umap-learn) is not in this browser's Python; the built-in analysis is (analysis_backend builtin)",
      hint: `${RUNNER} with Scanpy installed, or leave analysis_backend at builtin.`,
    };
  }
  if (p.integration === "scvi") {
    return {
      message: "scVI (scvi-tools and torch) is not in this browser's Python; built-in Harmony and no integration are (integration harmony or none)",
      hint: `${RUNNER} with scvi-tools installed, or use integration harmony.`,
    };
  }
  return formatNeedsRunner("source", p.source);
}

/** A file this browser does not read, by its name: 10x HDF5 and AnnData. */
function formatNeedsRunner(param, name) {
  const n = typeof name === "string" ? ext(name.trim()) : "";
  if (n.endsWith(".h5ad")) {
    return {
      message: `${param}: AnnData (.h5ad) needs anndata, which is not in this browser's Python`,
      hint: `${RUNNER} with anndata installed, or write the counts as a CSV or TSV table (genes in rows).`,
    };
  }
  if (n.endsWith(".h5")) {
    return {
      // Pyodide has h5py, but reading an .h5 through the job's file mount is untested: not offered yet
      message: `${param}: 10x HDF5 (.h5) is not yet supported in this browser (these jobs load only numpy and scipy; reading an .h5 with h5py through the job's file mount is untested)`,
      hint: `${RUNNER} with h5py installed, or write the counts as a CSV or TSV table (genes in rows).`,
    };
  }
  return null;
}

// --------------------------------------------------------------------------------------------- arguments

function bad(message) {
  return refusal("bad_arguments", message, "arguments");
}

function runnerRefusal(why) {
  return refusal("unavailable", why.message, "runner", why.hint);
}

/** Unknown parameters are refused, never ignored: one the runner would read differently would change the result. */
function checkKnown(spec, p) {
  const extra = Object.keys(p).filter((k) => given(p[k]) && !spec.params.includes(k)).sort();
  if (extra.length) throw bad(`unknown parameter${extra.length > 1 ? "s" : ""} ${extra.join(", ")}; ${spec.kind} takes ${spec.params.join(", ")}`);
}

/** Text as the runner takes it (tcmstudio.kinds._text): not empty, at most `max` characters, no leading "-", no
 * control characters, in `pattern`'s form when one is given. → the text, trimmed. */
function checkText(name, value, { max = 200, pattern = null } = {}) {
  if (typeof value !== "string" || !value.trim()) throw bad(`${name}: a non-empty text is needed`);
  const v = value.trim();
  if (v.length > max) throw bad(`${name}: at most ${max} characters`);
  if (v.startsWith("-")) throw bad(`${name}: a value cannot start with '-'`);
  if (/[\u0000-\u001f]/.test(v)) throw bad(`${name}: control characters are not allowed`);
  if (pattern && !pattern.test(v)) throw bad(`${name}: ${JSON.stringify(v)} is not in the expected form`);
  return v;
}

function checkNumber(name, value, [low, high], { integer = false } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value) || (integer && !Number.isInteger(value))) {
    throw bad(`${name}: ${integer ? "a whole number" : "a number"} is needed, not ${JSON.stringify(value)}`);
  }
  if (value < low || value > high) throw bad(`${name}: between ${low} and ${high}, not ${value}`);
  return value;
}

function checkEnum(name, value, allowed) {
  if (!allowed.includes(value)) throw bad(`${name}: one of ${allowed.join(", ")}, not ${JSON.stringify(value)}`);
  return value;
}

function checkRnaseq(p) {
  // a count-matrix run has no FASTQ reference and no quantifier, here as on the runner (kinds.py, count-matrix mode)
  const fastq = ["transcripts", "genome", "annotation", "engine", "trimmer"].filter((k) => given(p[k]));
  if (fastq.length) throw bad(`a count-matrix run takes no FASTQ reference or engine: ${fastq.join(", ")}`);
  if (!given(p.samples)) throw bad("samples: the sample table (CSV or TSV: a sample column and the design's variables) is required with counts");
  if (given(p.design)) checkText("design", p.design);
  if (given(p.contrast)) checkText("contrast", p.contrast, { pattern: /^[^,\s]+,[^,]+,[^,]+$/ });
  if (present(p.alpha)) checkNumber("alpha", p.alpha, ALPHA);
  if (present(p.de_backend)) checkEnum("de_backend", p.de_backend, ["builtin", "pydeseq2"]);
}

function checkScrna(p) {
  if (!given(p.source)) throw bad("source: a count table of this project (CSV, TSV or TXT, gzipped or not; genes in rows), or a sample sheet of them, is required");
  if (present(p.doublets)) checkEnum("doublets", p.doublets, ["remove", "flag", "off"]);
  if (present(p.analysis_backend)) checkEnum("analysis_backend", p.analysis_backend, ["builtin", "scanpy"]);
  if (present(p.integration)) checkEnum("integration", p.integration, ["none", "harmony", "scvi"]);
  if (present(p.resolution)) checkNumber("resolution", p.resolution, RESOLUTION);
  if (present(p.seed)) checkNumber("seed", p.seed, SEED, { integer: true });
  if (present(p.n_top_genes)) checkNumber("n_top_genes", p.n_top_genes, N_TOP_GENES, { integer: true });
  for (const key of ["root", "contrast"]) if (given(p[key])) checkText(key, p[key]);
}

// --------------------------------------------------------------------------------------------- project files

/** The project file a parameter names (its id, or its name: the newest file of that name in the project). */
async function findFile(projectFiles, projectId, param, ref) {
  if (typeof ref !== "string" || !ref.trim()) throw bad(`${param}: name a file of this project (its name or its id)`);
  if (!projectFiles) {
    throw refusal("unavailable", "This page gives the job service no project files to read the inputs from.", "storage",
      "Open Studio itself (the page that holds the project), or connect the local runner.");
  }
  let rec = null;
  try {
    rec = await projectFiles.find(projectId ?? null, ref.trim());
  } catch (err) {
    throw refusal("unavailable", `${param}: the project's files could not be read (${err?.message || err})`, "storage");
  }
  if (!rec) {
    throw bad(`${param}: no file named ${JSON.stringify(ref.trim())} in this project${projectId ? "" : " (the job belongs to no project, so only a file id can name one)"}`);
  }
  if (!rec.blob || typeof rec.blob.slice !== "function" || !bareSha256(rec.sha256)) {
    throw refusal("unavailable", `${param}: ${rec.name} has no bytes or no recorded SHA-256 in this browser`, "storage", "Attach the file to the project again.");
  }
  return rec;
}

/** The first line of a (small) file's text, without a byte-order mark. */
async function firstLine(blob) {
  const text = await blob.slice(0, 64 * 1024).text();
  return text.replace(/^\uFEFF/, "").split(/\r?\n/, 1)[0] || "";
}

/** The header cells of a CSV or TSV first line, split as the runner's _is_sheet splits it. */
function headerCells(line) {
  return line.split(/[\t,]/).map((c) => c.trim().replace(/^"(.*)"$/, "$1"));
}

/**
 * Every file the call names, found in the project → {files: [{param, rec, line?}], sheet: {rec} | null, bytes}. A
 * scRNA sample sheet's path cells are files too (param "source.files", line "path:<n>").
 */
async function plan(spec, p, projectFiles, projectId) {
  const files = [];
  let sheet = null;
  for (const param of spec.files) {
    if (!given(p[param])) continue;
    const rec = await findFile(projectFiles, projectId, param, p[param]);
    files.push({ param, rec });
    if (spec === RNASEQ && param === "samples") {
      const cells = headerCells(await firstLine(rec.blob));
      if (cells.includes("fastq_1") || cells.includes("fastq_2")) {
        throw runnerRefusal({
          message: `samples: ${rec.name} is a FASTQ sample sheet: the count-matrix mode takes a sample table (a sample column and the design's variables)`,
          hint: `${RUNNER}: it runs the full pipeline from FASTQ (leave out counts there).`,
        });
      }
    }
    if (spec === SCRNA && param === "source") {
      const tied = formatNeedsRunner("source", rec.name);
      if (tied) throw runnerRefusal(tied);
      const cells = SHEET.test(rec.name) ? headerCells(await firstLine(rec.blob)) : [];
      if (cells.includes("sample") && cells.includes("path")) {
        if (/\.txt$/i.test(rec.name)) throw bad(`source: ${rec.name} is a sample sheet, and a sample sheet is read as .csv or .tsv only; name it so`);
        sheet = { rec };
        for (const row of await sheetRows(rec)) {
          const found = await findFile(projectFiles, projectId, `source: line ${row.line}, path`, row.path);
          const why = formatNeedsRunner(`source: line ${row.line}, path`, found.name);
          if (why) throw runnerRefusal(why);
          if (!TABLE.test(found.name)) throw bad(`source: line ${row.line}, path ${found.name} is not a count table (CSV, TSV or TXT, gzipped or not; genes in rows)`);
          files.push({ param: "source.files", line: `path:${row.line}`, rec: found });
        }
      } else if (!TABLE.test(rec.name)) {
        throw bad(`source: ${rec.name} is not a count table this job reads (CSV, TSV or TXT, gzipped or not; genes in rows, cells in columns) or a sample sheet of them`);
      }
    }
  }
  const seen = new Set();
  let bytes = 0;
  for (const f of files) {
    if (seen.has(f.rec.id)) continue;
    seen.add(f.rec.id);
    bytes += Number(f.rec.bytes ?? f.rec.blob.size) || 0;
  }
  return { files, sheet, bytes };
}

// --------------------------------------------------------------------------------------------- sample sheets

/** The rows of CSV or TSV text, quoted as Python's csv module quotes (RFC 4180): [[cell, …], …]; blank lines skipped. */
export function parseDelimited(text, delimiter) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  let started = false;
  const s = String(text).replace(/^\uFEFF/, "");
  const end = () => {
    row.push(cell);
    cell = "";
  };
  const endRow = () => {
    end();
    if (row.length > 1 || row[0] !== "" || started) rows.push(row);
    row = [];
    started = false;
  };
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quoted) {
      if (c === '"' && s[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') quoted = false;
      else cell += c;
      continue;
    }
    if (c === '"' && cell === "") { quoted = true; started = true; } else if (c === delimiter) { end(); started = true; } else if (c === "\n" || c === "\r") {
      if (c === "\r" && s[i + 1] === "\n") i++;
      endRow();
    } else cell += c;
  }
  if (cell !== "" || row.length || started) endRow();
  return rows;
}

/** One CSV or TSV line, quoted only where it must be (csv.QUOTE_MINIMAL). */
function formatRow(cells, delimiter) {
  return cells.map((c) => (c.includes(delimiter) || /["\r\n]/.test(c) ? `"${c.replace(/"/g, '""')}"` : c)).join(delimiter);
}

/** The tab or the comma, whichever the header uses more (tcmstudio.kinds._sniff_dialect). */
function delimiterOf(text) {
  const first = String(text).replace(/^\uFEFF/, "").split(/\r?\n/, 1)[0] || "";
  return (first.match(/\t/g) || []).length > (first.match(/,/g) || []).length ? "\t" : ",";
}

/** A sample sheet read as the runner reads it → {header, rows: [{line, values: {col: text}, path}]}. */
function readSheet(text) {
  const delimiter = delimiterOf(text);
  const [head = [], ...body] = parseDelimited(text, delimiter);
  const header = head.map((h) => h.trim());
  const rows = body.map((cells, i) => {
    const values = Object.fromEntries(header.map((h, j) => [h, (cells[j] ?? "").trim()]));
    return { line: i + 2, values, path: values.path || "" };
  });
  return { header, rows, delimiter };
}

/** A sample sheet's text, read as UTF-8 (a byte-order mark allowed), or refused as the runner refuses it. */
function sheetText(rec, bytes) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw bad(`source: the sheet ${rec.name} could not be read as UTF-8 text`);
  }
}

async function sheetRows(rec) {
  const { rows } = readSheet(sheetText(rec, new Uint8Array(await rec.blob.arrayBuffer())));
  if (!rows.length) throw bad(`source: ${rec.name} lists no samples`);
  const empty = rows.find((r) => !r.path);
  if (empty) throw bad(`source: line ${empty.line} of ${rec.name} names no path`);
  return rows;
}

/** The sheet with each path cell replaced (paths: line → the new path), written with the sheet's own delimiter. */
function rewriteSheet(text, paths) {
  const { header, rows, delimiter } = readSheet(text);
  const out = [formatRow(header, delimiter)];
  for (const r of rows) out.push(formatRow(header.map((h) => (h === "path" && paths.has(r.line) ? paths.get(r.line) : r.values[h])), delimiter));
  return `${out.join("\n")}\n`;
}

// --------------------------------------------------------------------------------------------- the job

/** A name the job's inputs directory can hold: the file's own name where it can (Python reads a format, and a single
 * table's sample name, from it), else with the characters a path segment cannot have replaced. */
function safeName(name) {
  const s = String(name || "").replace(/[/\\\0]/g, "_").trim();
  return !s || s === "." || s === ".." ? "input" : s;
}

/**
 * Copy the inputs into the job's directory in OPFS (tcmstudio/jobs/<id>/inputs/), each hashed as it is written and
 * refused unless its SHA-256 is the one the project recorded; the sheet rewritten to name the copies. Each input is
 * recorded on the job (ctx.input) → {names: the staged files, paths: param → path in the worker, digests}.
 */
async function stageInputs(ctx, spec, inputsDir) {
  const { files, sheet } = await plan(spec, ctx.params, ctx.projectFiles, ctx.job.project_id);
  const dir = [...ctx.dir, "inputs"];
  // an earlier attempt's copies are not trusted: every input is copied and checked again
  await ctx.store.remove(dir, { recursive: true }).catch(() => false);
  const staged = new Map(); // project file id → staged name
  const used = new Set();
  const digests = {};
  const paths = {};
  const sheetPaths = new Map();
  const nameFor = (rec) => {
    if (staged.has(rec.id)) return staged.get(rec.id);
    let name = safeName(rec.name);
    for (let k = 2; used.has(name); k++) name = `${k}-${safeName(rec.name)}`;
    used.add(name);
    staged.set(rec.id, name);
    return name;
  };
  const copied = new Map(); // id → {sha256, bytes}
  let done = 0;
  for (const f of files) {
    ctx.throwIfCancelled();
    const name = nameFor(f.rec);
    if (!copied.has(f.rec.id)) {
      if (sheet && f.rec.id === sheet.rec.id && f.param === "source") {
        copied.set(f.rec.id, null); // written below, rewritten, once its files are staged
      } else {
        copied.set(f.rec.id, await copyChecked(ctx, [...dir, name], f.rec));
        if (f.rec.name !== name) ctx.log("info", `${f.rec.name} is staged as ${name}: another input has its name`);
      }
    }
    done += 1;
    ctx.progress(0.1 * (done / files.length), `${f.rec.name} copied and checked`);
    const path = `${inputsDir}/${name}`;
    if (f.param === "source.files") sheetPaths.set(Number(f.line.slice(5)), path);
    else paths[f.param] = path;
    const got = copied.get(f.rec.id);
    if (got) {
      ctx.input({ name: f.line || f.param, group: f.param === "source.files" ? "source.files" : undefined, sha256: got.sha256, bytes: got.bytes, source: { project_file: f.rec.id, name: f.rec.name } });
      if (f.param === "source.files") (digests["source.files"] ||= {})[f.line] = got.sha256;
      else digests[f.param] = got.sha256;
    }
  }
  if (sheet) {
    // the sheet's own bytes are held to the project's digest; the copy the pipeline reads names the staged files, as
    // the runner's staged sheet names its uploads
    const bytes = new Uint8Array(await sheet.rec.blob.arrayBuffer());
    const sha256 = await sha256Hex(bytes);
    if (sha256 !== bareSha256(sheet.rec.sha256)) throw mismatch(sheet.rec, sha256);
    await ctx.store.writeText([...dir, staged.get(sheet.rec.id)], rewriteSheet(sheetText(sheet.rec, bytes), sheetPaths));
    ctx.input({ name: "source", sha256, bytes: bytes.length, source: { project_file: sheet.rec.id, name: sheet.rec.name } });
    digests.source = sha256;
  }
  return { names: [...used], paths, digests, files: files.length };
}

function mismatch(rec, sha256) {
  const e = new Error(`${rec.name}: its bytes in this browser have SHA-256 ${sha256.slice(0, 16)}…, not the ${bareSha256(rec.sha256).slice(0, 16)}… the project recorded; it was not used`);
  e.type = "integrity";
  e.hint = "Attach the file to the project again.";
  return e;
}

/** One project file copied to OPFS `path`, hashed as it is written → {sha256, bytes}; a copy whose digest is not the
 * project's is removed and refused. */
async function copyChecked(ctx, path, rec) {
  const h = new Sha256();
  const bytes = await ctx.store.writeBlob(path, rec.blob, { signal: ctx.signal, onChunk: (b) => h.update(b) });
  const sha256 = h.digestHex();
  if (sha256 !== bareSha256(rec.sha256)) {
    await ctx.store.remove(path).catch(() => false);
    throw mismatch(rec, sha256);
  }
  return { sha256, bytes };
}

/** The Python arguments of the analysis: the parameters the pipeline takes, the files as the worker sees them. */
function pythonArgs(spec, p, paths, outDir) {
  if (spec === RNASEQ) {
    return { counts: paths.counts, samples: paths.samples, design: p.design, contrast: p.contrast, alpha: p.alpha, de_backend: p.de_backend, out_dir: outDir };
  }
  return {
    source: paths.source, markers: paths.markers, doublets: p.doublets, analysis_backend: p.analysis_backend, integration: p.integration,
    resolution: p.resolution, seed: p.seed, root: p.root, contrast: p.contrast, n_top_genes: p.n_top_genes, out_dir: outDir,
  };
}

/** An output as the job records it: the runner's artefact name for a file the kind requires, else its path. */
function artefactOf(spec, rel, f) {
  const file = rel.join("/");
  const named = Object.entries(spec.artefacts).find(([, path]) => path === file);
  return { name: named ? named[0] : file, path: ["out", ...rel], bytes: f.bytes, sha256: f.sha256 };
}

/** The job's limitations: the run's limitations.md lines, then where it was computed and what is not compared. */
async function recordLimits(ctx, spec, versions, s) {
  const md = await ctx.store.readBytes([...ctx.dir, "out", "limitations.md"]).catch(() => null);
  const lines = md ? limitationLines(new TextDecoder().decode(md)) : [];
  lines.push(computedLine(versions), ...spec.lines);
  s?.record({ packages: versions || null });
  ctx.outcome({ limitations: lines });
}

function handler(spec, { title, analysis }) {
  const needsRunner = spec === RNASEQ ? rnaseqNeedsRunner : scrnaNeedsRunner;
  return {
    kind: spec.kind,
    title,
    stages: [
      { id: "inputs", title: { zh: "复制并核对输入文件", en: "Copy and check the input files" } },
      { id: "analysis", title: analysis },
      { id: "verify", title: { zh: "按 Runner 的要求核对输出", en: "Check the outputs as the runner does" } },
    ],
    /** The runner's names of the outputs it requires (kinds.py): missing any of them fails the job. */
    expected: Object.keys(spec.artefacts),
    /** Why a call needs the runner, from its parameters alone (the router asks before it places one), or null. */
    needsRunner,

    async preflight(params, svc, { project_id = null } = {}) {
      const p = isPlainObject(params) ? params : {};
      checkKnown(spec, p);
      const tied = needsRunner(p);
      if (tied) throw runnerRefusal(tied);
      if (spec === RNASEQ) checkRnaseq(p);
      else checkScrna(p);
      if (!svc.store) throw await storageRefusal(null);
      if (!svc.pool) throw refusal("unavailable", "This browser cannot start job workers.", "job_service");
      const { bytes } = await plan(spec, p, svc.projectFiles, project_id);
      const refused = await storageRefusal(svc.store, bytes * STORAGE_FACTOR);
      if (refused) throw refused;
    },

    async run(ctx) {
      const { params, pool } = ctx;
      const root = work(ctx.job);
      const SUMMARY = [...ctx.dir, "summary.json"];
      let staged = null;
      await ctx.stage("inputs", async (s) => {
        staged = await stageInputs(ctx, spec, `${root}/inputs`);
        s.record({ inputs: staged.digests });
        return { detail: `${staged.names.length} file${staged.names.length === 1 ? "" : "s"} copied and checked against the project's SHA-256` };
      });
      // what the analysis depends on: its parameters and the bytes of its inputs
      const inputsDigest = `sha256:${await sha256Hex(canonicalJson({ params, inputs: staged.digests }))}`;
      const lease = await pool.lease({ packages: PACKAGES });
      let summary = null;
      // a worker refused for memory is not handed to the next job: if it ran out, its wasm memory never shrinks again
      let exhausted = false;
      try {
        // the versions this worker loaded, as it reports them: the limitations say what computed the numbers
        const loaded = await lease.call("packages", { names: PACKAGES }, { signal: ctx.signal });
        const versions = Object.fromEntries(PACKAGES.map((n) => [n, loaded?.versions?.[n] ?? null]));
        const m = await lease.call("mount", { root: `${root}/inputs`, dir: [...ctx.dir, "inputs"], files: staged.names }, { signal: ctx.signal });
        ctx.log("info", `${m.files.length} input file${m.files.length === 1 ? "" : "s"} mounted read-only (${(m.bytes / 1e6).toFixed(1)} MB, ${m.mode})`);

        const reuse = async (s) => {
          const check = await ctx.checkpoint.reusable("analysis", { inputs_digest: inputsDigest });
          if (!check.ok) return check;
          const kept = await ctx.store.readJson(SUMMARY).catch(() => null);
          if (!isPlainObject(kept)) return { ok: false, problems: ["the earlier attempt's summary is gone"] };
          summary = kept.summary ?? null;
          // the verify stage reads the outputs from the worker's memory: put back what the earlier attempt kept (its
          // job receipt is the service's, not the pipeline's)
          await lease.call("restore", { dir: [...ctx.dir, "out"], to: `${root}/out` }, { signal: ctx.signal });
          await lease.call("free", { paths: [`${root}/out/job_receipt.json`] }, { signal: ctx.signal });
          const rec = await ctx.checkpoint.read("analysis");
          for (const o of rec.outputs) ctx.artefact(artefactOf(spec, o.path.slice(1), o));
          s.worker = lease.id;
          await recordLimits(ctx, spec, versions, s);
          return { ok: true, detail: `reused: its ${check.checked} outputs re-hash to the digests recorded when it ran` };
        };
        await ctx.stage("analysis", async (s) => {
          const out = await pythonCall(ctx, lease, spec.fn, pythonArgs(spec, params, staged.paths, `${root}/out`), [0.1, 0.75], "the analysis");
          s.record({ exit: out.exit, failed: out.failed || [], worker: lease.id });
          if (out.exit === 2) {
            // what it needs, when Python says (need "memory": more cells than a browser worker holds)
            const e = new Error(`refused: ${out.refused || "the pipeline refused its inputs"}`);
            e.type = "refused";
            if (typeof out.need === "string" && out.need) e.need = out.need;
            if (out.need === "memory") e.hint = MEMORY_HINT;
            throw e;
          }
          summary = out.summary ?? null;
          // what it wrote is kept and listed even when it did not complete: the run record says how far it got. out/ is
          // written afresh, so an earlier attempt's outputs there are no longer the job's
          const kept = await lease.call("persist", { from: `${root}/out`, dir: [...ctx.dir, "out"], clean: true }, { signal: ctx.signal });
          ctx.forget(["out"]);
          for (const f of kept.files) ctx.artefact(artefactOf(spec, f.path, f));
          await recordLimits(ctx, spec, versions, s);
          if (out.exit !== 0) {
            const e = new Error(`the pipeline did not complete${(out.failed || []).length ? `: ${out.failed.join("; ")}` : ` (exit ${out.exit})`}`);
            e.type = "failed";
            throw e;
          }
          await ctx.store.writeJson(SUMMARY, { summary });
          return {
            detail: `${kept.files.length} output files`, worker: lease.id,
            checkpoint: { inputs_digest: inputsDigest, outputs: kept.files.map((f) => ({ path: ["out", ...f.path], bytes: f.bytes, sha256: f.sha256 })) },
          };
        }, { reuse });

        await ctx.stage("verify", async (s) => {
          // in the same worker, with the inputs still mounted where the run record says they were read
          const v = await pythonCall(ctx, lease, "verify_pipeline", { kind: spec.kind, out_dir: `${root}/out` }, [0.85, 0.15], "the check of the outputs");
          s.record({ status: v.status, worker: lease.id });
          const record = await ctx.store.readJson([...ctx.dir, "out", "run.json"]).catch(() => null);
          // as the runner's receipt names them: the checks that passed by name, what failed in words
          ctx.outcome({
            checks: (v.checks || []).filter((c) => c.status === "PASS").map((c) => c.name), problems: v.problems || [],
            ...(typeof record?.complete === "boolean" ? { complete: record.complete } : {}),
          });
          if (v.status !== "PASS") {
            const e = new Error(`the outputs did not verify: ${(v.problems || []).slice(0, 3).join("; ") || v.status}`);
            e.type = "failed";
            throw e;
          }
          return { detail: "the run re-verified (its outputs and inputs re-hashed, its tables checked) and held to the runner's checks", worker: lease.id, verified: true };
        });
      } catch (err) {
        if (err?.need === "memory") exhausted = true;
        throw err;
      } finally {
        if (exhausted) lease.terminate();
        else {
          await lease.call("free", { paths: [root] }).catch(() => {});
          lease.release();
        }
      }
      return { result: summary };
    },
  };
}

/** pipeline.rnaseq in its count-matrix mode: differential expression from a count matrix and a sample table. */
export const rnaseqCounts = handler(RNASEQ, {
  title: { zh: "RNA-seq 差异表达（计数矩阵，浏览器）", en: "RNA-seq differential expression (count matrix, browser)" },
  analysis: { zh: "差异表达、VST、PCA 与报告", en: "Differential expression, VST, PCA and report" },
});

/** pipeline.scrna: count tables to annotated clusters, with the built-in analysis. */
export const scrna = handler(SCRNA, {
  title: { zh: "单细胞分析（计数表，浏览器）", en: "Single-cell analysis (count tables, browser)" },
  analysis: { zh: "质控、整合、聚类、注释与报告", en: "QC, integration, clustering, annotation and report" },
});

export const OMICS_HANDLERS = [rnaseqCounts, scrna];

/** What tests and the router read: the kinds' required artefacts and the browser's limitation lines. */
export const OMICS = Object.freeze({ packages: PACKAGES, rnaseq: RNASEQ.artefacts, scrna: SCRNA.artefacts, computedLine, umapLine: UMAP_LINE });
