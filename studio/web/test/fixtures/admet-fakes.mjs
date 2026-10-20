// Stand-ins for the ADMET browser jobs' tests (web/test/scientific/admet.test.mjs):
//
//   chemWorkerClass()   the real chemistry worker (js/scientific/chem/chem.worker.js, its own serve() and handlers) in
//                       this process, RDKit.js from npm's package (the pinned build), its wasm "fetched" from npm's file
//   scienceWorkerClass(store, python)   job workers with a memory file system, as science.worker.js does mount,
//                       persist, restore and free; "run" answers what python(fn, args, fs) returns
//   admetPython()       tcmstudio.browser_jobs' ADMET functions as their docstrings state them (admet_split,
//                       admet_train, admet_read, run_admet, verify_pipeline), on a TDC-shaped archive written as JSON:
//                       it reads what the page wrote as bioagent would (a prepared set held to its molecules' SHA-256
//                       and its counts to its bits; similarities checked against an exact search, every query) and
//                       writes what the real ones write (model files, an np.save'd domain, merged cards)
//   projectStore()      the page's project files (core/store.js `files`)
//   npy(rows, n)        an np.save'd uint8 (n, 256) array
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { serve, workerEnv } from "../../js/scientific/chem/chem.worker.js";
import { RDKIT } from "../../js/scientific/chem/rdkit.js";
import { tanimotoBestJS, packedWords } from "../../js/compute/science-cpu.js";

const enc = new TextEncoder();
const dec = new TextDecoder();
export const hex = (bytes) => createHash("sha256").update(bytes).digest("hex");

const CHEM = fileURLToPath(new URL("../../js/scientific/chem/", import.meta.url));
const require = createRequire(fileURLToPath(new URL("../../../package.json", import.meta.url)));
const factory = require("@rdkit/rdkit");
const WASM = readFileSync(require.resolve("@rdkit/rdkit/RDKit_minimal.wasm"));
const readData = async (name) => JSON.parse(readFileSync(path.join(CHEM, name), "utf8"));
export const FIXTURE = JSON.parse(readFileSync(fileURLToPath(new URL("./chem_parity.json", import.meta.url)), "utf8"));

/** The chemistry worker, run here through its own message loop; `fetched` lists the wasm URLs it was asked for. */
export function chemWorkerClass({ wasm = WASM } = {}) {
  const fetched = [];
  const instances = [];
  class ChemWorker {
    constructor(url, opts) {
      this.url = url;
      this.opts = opts;
      this.terminated = false;
      this.received = [];
      instances.push(this);
      const scope = { postMessage: (m) => setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: m }); }, 0) };
      this.scope = scope;
      const env = workerEnv({
        readData, factory, post: (m) => scope.postMessage(m), yieldMs: 5,
        fetch: async (u) => {
          fetched.push(String(u));
          const b = wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength);
          return { ok: true, status: 200, arrayBuffer: async () => b };
        },
      });
      serve(scope, env);
    }
    postMessage(m) {
      if (this.terminated) return;
      this.received.push(m);
      setTimeout(() => { if (!this.terminated) this.scope.onmessage?.({ data: m }); }, 0);
    }
    terminate() { this.terminated = true; }
  }
  ChemWorker.fetched = fetched;
  ChemWorker.instances = instances;
  return ChemWorker;
}

/** An np.save'd uint8 array of `n` rows of 256 bytes (format 1.0, its header padded to 64 bytes as numpy pads it). */
export function npy(rows, n) {
  let header = `{'descr': '|u1', 'fortran_order': False, 'shape': (${n}, 256), }`;
  const total = 10 + header.length + 1;
  header += " ".repeat((64 - (total % 64)) % 64) + "\n";
  const out = new Uint8Array(10 + header.length + rows.length);
  out.set([0x93, ...enc.encode("NUMPY"), 1, 0, header.length & 0xff, header.length >> 8]);
  out.set(enc.encode(header), 10);
  out.set(rows, 10 + header.length);
  return out;
}

/**
 * Job workers whose Python answers what python(fn, args, fs, worker) returns, with a memory file system: mount reads
 * the files from the OPFS the job wrote (read-only in the worker, recorded in worker.mounted), persist writes the memory
 * tree back (with each file's SHA-256), restore and free as science.worker.js does them.
 */
export function scienceWorkerClass(store, python, { versions = { numpy: "2.4.6", scipy: "1.18.0", "scikit-learn": "1.8.0" } } = {}) {
  const instances = [];
  class FakeWorker {
    constructor() { this.terminated = false; this.fs = new Map(); this.calls = []; this.mounted = []; instances.push(this); }
    postMessage(data) {
      if (this.terminated || typeof data !== "string") return;
      const msg = JSON.parse(data);
      setTimeout(() => this.#answer(msg).catch((err) => this.send({ id: msg.id, ok: false, ms: 1, error: { type: err?.code || "runtime_error", message: String(err?.message || err) } })), 0);
    }
    async #answer({ id, op, payload }) {
      const reply = (result) => this.send({ id, ok: true, ms: 1, result });
      this.calls.push({ op, payload });
      if (op === "init") return reply({ runtime: "pyodide-314.0.7 / CPython 3.14.2", platform: "emscripten", machine: "wasm32", packages: ["pyyaml"], pyodide: { version: payload.boot.pyodide.version }, bundle: { path: payload.boot.bundle.path, sha256: payload.boot.bundle.sha256 } });
      if (op === "packages") return reply({ packages: ["pyyaml", ...payload.names], versions: { ...versions, pyyaml: "6.0.3" } });
      if (op === "memory") return reply({ wasm_bytes: 300 << 20 });
      if (op === "mount") {
        const files = [];
        for (const name of payload.files) {
          const b = await store.readBytes([...payload.dir, name]);
          if (!b) throw Object.assign(new Error(`not in this browser's storage: ${[...payload.dir, name].join("/")}`), { code: "storage" });
          this.fs.set(`${payload.root}/${name}`, b);
          files.push({ name, bytes: b.length });
        }
        this.mounted.push({ root: payload.root, dir: payload.dir, files: payload.files });
        return reply({ root: payload.root, mode: "sync-access", files, bytes: files.reduce((a, f) => a + f.bytes, 0) });
      }
      if (op === "persist") {
        if (payload.clean) await store.remove(payload.dir, { recursive: true }).catch(() => false);
        const files = [];
        for (const [p, b] of [...this.fs].sort()) {
          if (!p.startsWith(`${payload.from}/`)) continue;
          const rel = p.slice(payload.from.length + 1).split("/");
          await store.writeBytes([...payload.dir, ...rel], b);
          files.push({ path: rel, bytes: b.length, sha256: hex(b) });
        }
        if (!files.length && ![...this.fs.keys()].some((k) => k.startsWith(`${payload.from}/`))) throw Object.assign(new Error(`nothing at ${payload.from}`), { code: "not_found" });
        return reply({ dir: payload.dir, files });
      }
      if (op === "restore") {
        const walk = async (dir, rel) => {
          for (const e of await store.list(dir)) {
            if (e.kind === "directory") await walk([...dir, e.name], [...rel, e.name]);
            else this.fs.set([payload.to, ...rel, e.name].join("/"), await store.readBytes([...dir, e.name]));
          }
        };
        await walk(payload.dir, []);
        return reply({ to: payload.to, files: 0, bytes: 0 });
      }
      if (op === "free") {
        for (const p of payload.paths) for (const k of [...this.fs.keys()]) if (k === p || k.startsWith(`${p}/`)) this.fs.delete(k);
        return reply({ freed: payload.paths.length });
      }
      if (op === "run") {
        this.send({ type: "event", id, event: { kind: "log", level: "info", text: `python: ${payload.fn}` } });
        const out = await python(payload.fn, payload.args, this.fs, this);
        return reply(out && typeof out === "object" && "ok" in out ? out : { ok: true, result: out });
      }
      return this.send({ id, ok: false, ms: 1, error: { type: "bad_request", message: op } });
    }
    send(obj) { setTimeout(() => { if (!this.terminated) this.onmessage?.({ data: JSON.stringify(obj) }); }, 0); }
    terminate() { this.terminated = true; }
  }
  FakeWorker.instances = instances;
  return FakeWorker;
}

/** The page's project files (core/store.js `files`): records with a Blob and the SHA-256 recorded when it was added. */
export function projectStore() {
  let t = 1000;
  const recs = [];
  return {
    recs,
    add(projectId, name, content, { sha256 = null } = {}) {
      const bytes = typeof content === "string" ? enc.encode(content) : content;
      const rec = { id: `f-${recs.length + 1}`, projectId, name, type: "text/plain", bytes: bytes.length, sha256: sha256 || hex(bytes), blob: new Blob([bytes]), createdAt: (t += 10) };
      recs.push(rec);
      return rec;
    },
    async list(projectId) { return recs.filter((r) => r.projectId === projectId).sort((a, b) => a.createdAt - b.createdAt); },
    async get(id) { return recs.find((r) => r.id === id); },
  };
}

// ------------------------------------------------------------------------------------------------- the Python

const ok = (result) => ({ ok: true, result });
const refused = (message, need) => ({ ok: false, error: { type: "refused", message, ...(need ? { need } : {}) } });
const smilesSha = (list) => hex(enc.encode(list.join("\n")));
const json = (fs, p) => JSON.parse(dec.decode(fs.get(p)));
const put = (fs, p, value) => fs.set(p, typeof value === "string" ? enc.encode(value) : value);

/** A prepared set as PrecomputedEngine reads it, held to `smiles` → {doc, ok, features (Float32Array), bits}. */
function readPrepared(fs, dir, smiles) {
  const doc = json(fs, `${dir}/prepared.json`);
  if (doc.schema !== "tcmstudio.admet-prepared/1") throw new Error(`${dir}: not a prepared set`);
  if (doc.smiles_sha256 !== smilesSha(smiles)) throw new Error(`${dir}: prepared for other molecules`);
  if (doc.n !== smiles.length || doc.rows.length !== smiles.length) throw new Error(`${dir}: ${doc.n} rows for ${smiles.length} SMILES`);
  const nOk = doc.rows.filter((r) => r.ok).length;
  const f = fs.get(`${dir}/features.f32`), b = fs.get(`${dir}/bits.u8`);
  if (f.length !== nOk * 2073 * 4 || b.length !== nOk * 256) throw new Error(`${dir}: arrays of ${f.length} and ${b.length} bytes for ${nOk} rows`);
  const features = new Float32Array(f.slice().buffer);
  // the counts give the bits (packbits of counts > 0, most significant bit first)
  for (let i = 0; i < nOk; i++) {
    for (let byte = 0; byte < 256; byte++) {
      let v = 0;
      for (let k = 0; k < 8; k++) if (features[i * 2073 + byte * 8 + k] > 0) v |= 0x80 >> k;
      if (v !== b[i * 256 + byte]) throw new Error(`${dir}: row ${i}'s counts do not give its bits`);
    }
  }
  return { doc, ok: nOk, features, bits: b };
}

/**
 * tcmstudio.browser_jobs' ADMET functions on an archive written as JSON ({endpoints: {ep: {train_val: [[smiles, y]],
 * test: [...]}}}), each call recorded in .calls; options: verify (a status to answer), rejectSimilarity (a reason: the
 * page's similarities are rejected with it, as PrecomputedSimilarity rejects ones that fail its check, and the CPU
 * searches instead).
 */
export function admetPython({ verify = "PASS", rejectSimilarity = null } = {}) {
  const calls = [];
  const fn = async (name, args, fs) => {
    calls.push({ name, args: JSON.parse(JSON.stringify(args)) });
    if (name === "admet_split") {
      const raw = fs.get(args.archive);
      if (!raw) return refused(`the archive cannot be read: ${args.archive}`);
      let doc;
      // what bioagent's read_archive says of a file that is not a zip (here: not the JSON stand-in)
      try { doc = JSON.parse(dec.decode(raw)); } catch { return refused(`${args.archive.split("/").at(-1)} is not a zip archive (the TDC ADMET group is one)`); }
      const endpoints = {};
      for (const ep of args.endpoints?.length ? args.endpoints : Object.keys(doc.endpoints)) {
        if (!doc.endpoints[ep]) return refused(`the archive has no endpoint ${ep}`);
        endpoints[ep] = {};
        for (const split of ["train_val", "test"]) {
          const rows = doc.endpoints[ep][split];
          put(fs, `${args.out_dir}/${ep}/${split}.json`, JSON.stringify({ smiles: rows.map((r) => r[0]), y: rows.map((r) => r[1]) }));
          endpoints[ep][split] = rows.length;
        }
      }
      return { archive_sha256: hex(raw), pinned: false, endpoints, members: [] };
    }
    if (name === "admet_train") {
      const raw = fs.get(args.archive);
      const doc = JSON.parse(dec.decode(raw)).endpoints[args.endpoint];
      const sets = {};
      for (const split of ["train_val", "test"]) sets[split] = readPrepared(fs, `${args.prepared_dir}/${args.endpoint}/${split}`, doc[split].map((r) => r[0]));
      const engine = sets.train_val.doc.engine;
      const rows = [...sets.train_val.doc.rows, ...sets.test.doc.rows];
      const refusedRows = rows.filter((r) => !r.ok && r.need === "runner").length;
      const model = enc.encode(JSON.stringify({ endpoint: args.endpoint, seed: args.seed, max_iter: args.max_iter, trained_on: sets.train_val.ok, first: Array.from(sets.train_val.features.slice(0, 4)) }));
      const domain = npy(sets.train_val.bits, sets.train_val.ok);
      const card = {
        endpoint: args.endpoint, task: args.endpoint === "hia_hou" ? "classification" : "regression", archive_sha256: hex(raw), archive_is_pinned: false,
        train: sets.train_val.ok, test: sets.test.ok, dropped_unreadable: rows.filter((r) => !r.ok).length - refusedRows, dropped_unstandardisable: refusedRows,
        metric: args.endpoint === "hia_hou" ? "auroc" : "mae", test_score: 0.5, model: "HistGradientBoostingRegressor", iterations: 7,
        sklearn: "1.8.0", rdkit: engine.version, chemistry: { engine: engine.name, version: engine.version, detail: engine.detail },
        model_file: `${args.endpoint}.pkl`, model_sha256: hex(model), domain_file: `${args.endpoint}_domain.npy`, domain_sha256: hex(domain),
        domain_threshold: 0.3, seed: args.seed, max_iter: args.max_iter,
      };
      put(fs, `${args.models_dir}/${card.model_file}`, model);
      put(fs, `${args.models_dir}/${card.domain_file}`, domain);
      const cardsPath = `${args.models_dir}/model_cards.json`;
      const cards = fs.has(cardsPath) ? json(fs, cardsPath) : {};
      delete cards[args.endpoint];
      cards[args.endpoint] = card;
      put(fs, cardsPath, JSON.stringify(cards, null, 2));
      return card;
    }
    if (name === "admet_read") {
      const m = args.molecules;
      let entries;
      if (fs.has(m)) {
        if (/\.(sdf|sdf\.gz|mol)$/i.test(m)) return refused(`${m}: an SDF or .mol file is read with RDKit's SDMolSupplier`, "runner");
        const lines = dec.decode(fs.get(m)).split(/\r?\n/).filter((l) => l.trim());
        entries = lines.slice(1).map((l) => l.split(",")).map(([n, s]) => [n.trim(), s.trim()]);
      } else entries = [["ligand1", m]];
      put(fs, `${args.out_dir}/molecules.json`, JSON.stringify({ names: entries.map((e) => e[0]), smiles: entries.map((e) => e[1]) }));
      return { n: entries.length, file: `${args.out_dir}/molecules.json`, smiles_sha256: smilesSha(entries.map((e) => e[1])) };
    }
    if (name === "run_admet") {
      const mol = json(fs, args.molecules_file);
      const prep = readPrepared(fs, args.prepared_dir, mol.smiles);
      if (prep.doc.rows.some((r) => r.ok && !Array.isArray(r.alerts))) throw new Error("its structural alerts were not computed");
      const cards = json(fs, `${args.models_dir}/model_cards.json`);
      const eps = args.endpoints?.length ? args.endpoints : Object.keys(cards);
      const queries = packedWords(prep.bits.slice());
      const similarity = { backend: "cpu", checked: null, rejected: null };
      const found = {};
      let sim = null;
      if (args.similarity_dir) {
        sim = json(fs, `${args.similarity_dir}/similarity.json`);
        similarity.backend = sim.backend;
        similarity.checked = {};
      }
      for (const ep of eps) {
        const file = fs.get(`${args.models_dir}/${cards[ep].domain_file}`);
        if (hex(file) !== cards[ep].domain_sha256) throw new Error(`${cards[ep].domain_file} does not match its card`);
        const data = file.slice(file.length - cards[ep].train * 256);
        const exact = tanimotoBestJS(queries, packedWords(data.slice()));
        found[ep] = exact;
        if (!sim || similarity.rejected) continue;
        if (rejectSimilarity) { similarity.rejected = `${sim.backend}: ${ep}: ${rejectSimilarity}`; similarity.backend = "cpu"; similarity.checked = null; continue; }
        const entry = sim.endpoints[ep];
        const u32 = fs.get(`${args.similarity_dir}/${ep}.u32`);
        const why = !entry ? "no similarities for this endpoint"
          : entry.n_query !== prep.ok || entry.n_ref !== cards[ep].train ? "computed for another shape"
            : entry.refs_sha256 !== hex(data) ? "computed against other reference fingerprints (refs_sha256)"
              : !u32 || u32.length !== prep.ok * 12 ? `${ep}.u32 is missing or short` : null;
        if (why) { similarity.rejected = `${sim.backend}: ${ep}: ${why}`; similarity.backend = "cpu"; continue; }
        const view = new DataView(u32.buffer, u32.byteOffset, u32.byteLength);
        for (let q = 0; q < prep.ok; q++) {
          if ([exact.arg[q], exact.inter[q], exact.union[q]].some((v, k) => v !== view.getUint32(12 * q + 4 * k, true))) {
            similarity.rejected = `${sim.backend}: ${ep}: query ${q}: the exact search gives another answer`;
            similarity.backend = "cpu";
            break;
          }
        }
        if (!similarity.rejected) similarity.checked[ep] = prep.ok;
      }
      fn.similarities = found;
      const header = ["name", "smiles", ...eps.flatMap((e) => [e, `${e}_in_domain`])];
      let k = 0;
      const lines = mol.names.map((n, i) => {
        const row = prep.doc.rows[i];
        if (!row.ok) return null;
        const j = k++;
        return [n, row.canonical, ...eps.flatMap((e) => ["0.5", String(found[e].union[j] ? found[e].inter[j] / found[e].union[j] >= 0.3 : false)])].join("\t");
      }).filter(Boolean);
      put(fs, `${args.out_dir}/admet.tsv`, `${[header.join("\t"), ...lines].join("\n")}\n`);
      const failures = mol.names.filter((_, i) => !prep.doc.rows[i].ok);
      put(fs, `${args.out_dir}/limitations.md`, ["Each predicted endpoint is a model estimate, not a measurement.", ...(failures.length ? [`${failures.length} molecules could not be read and have no result: ${failures.join(", ")}.`] : [])].join("\n") + "\n");
      put(fs, `${args.out_dir}/report.md`, "# ADMET\n");
      const models = Object.fromEntries(eps.map((e) => [e, { model_sha256: cards[e].model_sha256, archive_sha256: cards[e].archive_sha256, metric: cards[e].metric, test_score: cards[e].test_score }]));
      put(fs, `${args.out_dir}/run.json`, JSON.stringify({ pipeline: "bioagent.admet", complete: true, models, similarity, chemistry: { engine: prep.doc.engine.name, version: prep.doc.engine.version, detail: prep.doc.engine.detail }, inputs: { [args.molecules_file]: hex(fs.get(args.molecules_file)) } }));
      return { exit: 0, summary: { molecules: mol.names.length, failures, complete: true, similarity }, failed: [], refused: null, need: null };
    }
    if (name === "verify_pipeline") {
      if (args.kind === "admet.models") {
        const cards = json(fs, `${args.models_dir}/model_cards.json`);
        const problems = [];
        const report = {};
        for (const e of args.endpoints?.length ? args.endpoints : Object.keys(cards)) {
          const c = cards[e];
          if (!c) { problems.push(`${e}: no card`); continue; }
          for (const [f, sha] of [[c.model_file, c.model_sha256], [c.domain_file, c.domain_sha256]]) {
            if (!fs.has(`${args.models_dir}/${f}`) || hex(fs.get(`${args.models_dir}/${f}`)) !== sha) problems.push(`${f} does not match its card`);
          }
          if (args.archive_sha256 && c.archive_sha256 !== args.archive_sha256) problems.push(`${e}: trained on another archive`);
          report[e] = { card_sha256: hex(enc.encode(JSON.stringify(c))), model_sha256: c.model_sha256, archive_is_pinned: c.archive_is_pinned, metric: c.metric, test_score: c.test_score };
        }
        const status = problems.length ? "FAIL" : verify;
        return { status, checks: [{ name: "verify_models", status }], problems, report: { endpoints: report } };
      }
      const status = fs.has(`${args.out_dir}/run.json`) ? verify : "FAIL";
      return { status, checks: [{ name: "verify_run", status }, { name: "required_artefacts", status }, { name: "validator:run", status }], problems: status === "PASS" ? [] : ["the run did not verify"], report: {} };
    }
    return { ok: false, error: { type: "bad_arguments", message: `no function ${name}` } };
  };
  fn.calls = calls;
  return fn;
}

/** The SMILES of the parity corpus: drug-like molecules it prepares, one it refuses (a step it does not port), one
 * that is not a SMILES. */
export function corpus() {
  const okMols = FIXTURE.molecules.filter((m) => m.expect !== "refused" && m.expect !== "error").map((m) => m.smiles);
  const refusedMol = FIXTURE.molecules.find((m) => m.expect === "refused");
  return { ok: okMols, refused: refusedMol.smiles, refusal: refusedMol.refusal, invalid: "C1CC(" };
}

/** A TDC-shaped archive, as JSON, of two endpoints from `smiles`. */
export function archiveBytes(smiles, { refusedSmiles = null, invalid = null } = {}) {
  const rows = (start, n) => smiles.slice(start, start + n).map((s, i) => [s, (i % 3) * 0.5]);
  const train = rows(0, 10);
  if (refusedSmiles) train.push([refusedSmiles, 1]);
  if (invalid) train.push([invalid, 0]);
  return enc.encode(JSON.stringify({ endpoints: { caco2_wang: { train_val: train, test: rows(10, 4) }, hia_hou: { train_val: rows(14, 9), test: rows(23, 3) } } }));
}

export { RDKIT };
