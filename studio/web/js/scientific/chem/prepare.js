// One molecule → its row of bioagent.admet.engine's PreparedSet, in the browser: chem.standardize, the canonical
// SMILES, featurize()'s row, bits(), chem.descriptors, AddHs's atom count (rules.py's Ghose rule) and rules.alerts —
// or why the molecule has none of them (an invalid SMILES, with native's message; a step the browser refuses, with
// the step named).

import { ChemError } from "./rdkit.js";
import { Ctx } from "./molecule.js";
import { StandardizeData, isWasmException, pyRepr, standardize } from "./standardize.js";
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
   * exception it did not catch) is thrown as ChemError "rdkit" and every later call is refused the same way: the
   * chemistry must be started again. A trap leaves its memory in a state nothing should be computed from, earlier
   * results included. An exception that got out of RDKit.js unwound its C++ frames as C++ does, so the molecules before
   * it stand and a fresh module may go on; that error has `restartable` true (and `why`).
   */
  prepare(smiles, options = {}) {
    if (this.broken) {
      throw new ChemError("rdkit", `RDKit.js stopped working earlier (${this.broken}); start the chemistry again`);
    }
    try {
      return this.#prepare(smiles, options);
    } catch (err) {
      const wasm = fromWasm(err);
      if (wasm === null) throw err;
      this.broken = wasm.why;
      throw new ChemError("rdkit", `RDKit.js failed while preparing ${pyRepr(String(smiles)).slice(0, 200)} (${wasm.why}); ` +
        "nothing more is prepared with this module: start the chemistry again", { restartable: !wasm.trap, why: wasm.why });
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

// what the wasm itself throws → {why, trap} (null for anything else): a trap or an abort (WebAssembly.RuntimeError) or
// the engine's stack (RangeError), or a C++ exception that got out (not a trap)
function fromWasm(err) {
  if (err instanceof WebAssembly.RuntimeError || err instanceof RangeError) return { why: String(err.message || err.name), trap: true };
  if (isWasmException(err)) return { why: "an exception RDKit.js did not catch", trap: false };
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
