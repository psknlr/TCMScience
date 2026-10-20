// One molecule → its row of bioagent.admet.engine's PreparedSet, in the browser: chem.standardize, the canonical
// SMILES, featurize()'s row, bits(), chem.descriptors, AddHs's atom count (rules.py's Ghose rule) and rules.alerts —
// or why the molecule has none of them (an invalid SMILES, with native's message; a step the browser refuses, with
// the step named).

import { ChemError } from "./rdkit.js";
import { Ctx } from "./molecule.js";
import { StandardizeData, pyRepr, standardize } from "./standardize.js";
import { FeatureTables, features } from "./features.js";
import { AlertCatalog } from "./alerts.js";

export class Preparer {
  /** `RDKit` from rdkit.js loadRDKit; `tables` rdkit_tables.json; `catalogs` filter_catalogs.json. */
  constructor(RDKit, { tables, catalogs }) {
    this.ctx = new Ctx(RDKit);
    this.standardizeData = new StandardizeData(tables);
    this.featureTables = new FeatureTables(tables);
    this.catalog = new AlertCatalog(this.ctx, catalogs);
    this.broken = null; // why RDKit.js is no longer used (see prepare)
  }

  /**
   * `smiles` → {ok, error, refused, canonical, descriptors, atoms_with_h, alerts, features (Float32Array(2073)), bits
   * (Uint8Array(256)), steps}. Not ok: error says why, refused is {need: "runner", missing} for a refusal and null for
   * an invalid SMILES. With alerts false, alerts is null (a training set needs none). Anything else is thrown: it is
   * not a property of the molecule. What RDKit.js throws itself (a trap such as "memory access out of bounds", or an
   * exception it did not catch) leaves its memory in a state nothing more should be computed from, so it is thrown as
   * ChemError "rdkit" and every later call is refused the same way: the chemistry must be started again.
   */
  prepare(smiles, options = {}) {
    if (this.broken) {
      throw new ChemError("rdkit", `RDKit.js stopped working earlier (${this.broken}); start the chemistry again`);
    }
    try {
      return this.#prepare(smiles, options);
    } catch (err) {
      const why = fromWasm(err);
      if (why === null) throw err;
      this.broken = why;
      throw new ChemError("rdkit", `RDKit.js failed while preparing ${pyRepr(String(smiles)).slice(0, 200)} (${why}); ` +
        "nothing more is prepared with this module: start the chemistry again");
    }
  }

  #prepare(smiles, { alerts = true } = {}) {
    let std;
    try {
      std = standardize(this.ctx, smiles, this.standardizeData);
    } catch (err) {
      return failed(err);
    }
    try {
      const f = features(this.ctx, std.mol, this.featureTables);
      return {
        ok: true, error: null, refused: null,
        canonical: std.mol.get_smiles(),
        descriptors: f.descriptors,
        atoms_with_h: f.atomsWithH,
        alerts: alerts ? this.catalog.alerts(std.mol) : null,
        features: f.row,
        bits: f.bits,
        steps: std.steps,
      };
    } catch (err) {
      return failed(err);
    } finally {
      std.mol.delete();
    }
  }

  free() {
    this.ctx.free();
  }
}

// what the wasm itself throws, described (null for anything else): a trap or an abort (WebAssembly.RuntimeError), a C++
// exception that got out (WebAssembly.Exception, or its address in older Emscripten builds), the engine's stack
const WASM_EXCEPTION = typeof WebAssembly.Exception === "function" ? WebAssembly.Exception : null;
function fromWasm(err) {
  if (err instanceof WebAssembly.RuntimeError || err instanceof RangeError) return String(err.message || err.name);
  if ((WASM_EXCEPTION && err instanceof WASM_EXCEPTION) || typeof err === "number") return "an exception RDKit.js did not catch";
  return null;
}

function failed(err) {
  if (!(err instanceof ChemError) || (err.code !== "invalid_smiles" && err.code !== "refused")) throw err;
  return {
    ok: false, error: err.message,
    refused: err.code === "refused" ? { need: err.need || "runner", missing: err.missing } : null,
    canonical: null, descriptors: null, atoms_with_h: null, alerts: null, features: null, bits: null, steps: null,
  };
}
