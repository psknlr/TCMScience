// What the browser chemistry's ports read from RDKit.js: compiled SMARTS (kept, freed together), substructure matches
// with RDKit's default parameters, and a molecule's graph from get_json() with RDKit's own perception read back
// through SMARTS primitives. RDKit.js's JSON has atoms (element, charge, implicit H count, radicals, isotope), bonds
// (Kekulé orders), the aromatic bonds and the ring atoms, but no hydrogen totals, valences or hybridisations; those
// are what RDKit computed for the same molecule, so they are asked of RDKit through queries ([h3], [v4], [^2]) rather
// than worked out again here.

import { ChemError } from "./rdkit.js";

/** Bond::BondType values the ports use (Code/GraphMol/Bond.h), and getBondTypeAsDouble for them. */
export const BOND = Object.freeze({ SINGLE: 1, DOUBLE: 2, TRIPLE: 3, AROMATIC: 12 });
const BOND_NAMES = { 0: "ZERO", 1: "SINGLE", 2: "DOUBLE", 3: "TRIPLE" };

export function bondTypeAsDouble(type) {
  switch (type) {
    case BOND.SINGLE: return 1;
    case BOND.DOUBLE: return 2;
    case BOND.TRIPLE: return 3;
    case BOND.AROMATIC: return 1.5;
    default: throw refusal(`feature ports: bond type ${type}`);
  }
}

/** The ChemError for a step RDKit.js cannot reproduce exactly: the molecule needs the runner's native RDKit. */
export function refusal(missing, message = null) {
  return new ChemError("refused", message || `the browser chemistry cannot do this exactly (${missing}); native RDKit on the runner can`,
    { need: "runner", missing });
}

/** RDKit.js access shared by every port for one module: query molecules are compiled once and kept until free(). */
export class Ctx {
  #queries = new Map();
  #reactions = new Map();

  constructor(RDKit) {
    this.RDKit = RDKit;
  }

  qmol(smarts) {
    let q = this.#queries.get(smarts);
    if (!q) {
      q = this.RDKit.get_qmol(smarts);
      if (!q || !q.is_valid()) {
        q?.delete();
        throw new ChemError("rdkit", `RDKit.js ${this.RDKit.version()} did not parse the SMARTS ${smarts}`);
      }
      this.#queries.set(smarts, q);
    }
    return q;
  }

  /** Every unique match (RDKit's SubstructMatch defaults: uniquify, at most 1000) → [{atoms, bonds}]. */
  matches(mol, smarts) {
    const r = JSON.parse(mol.get_substruct_matches(this.qmol(smarts)));
    return Array.isArray(r) ? r : (r && r.atoms ? [r] : []);
  }

  /** The first match ({atoms, bonds}) or null. */
  firstMatch(mol, smarts) {
    const r = JSON.parse(mol.get_substruct_match(this.qmol(smarts)));
    return r && r.atoms && r.atoms.length ? r : null;
  }

  hasMatch(mol, smarts) {
    return this.firstMatch(mol, smarts) !== null;
  }

  /** The first atom of every match, in match order (a one-atom pattern: the matching atoms by index). */
  firstAtoms(mol, smarts) {
    return this.matches(mol, smarts).map((m) => m.atoms[0]);
  }

  /** Per atom, an integer RDKit computed, read through a SMARTS primitive: prim "h" (getTotalNumHs(false)), "v"
   * (getTotalValence()), "D" ... An atom no value up to `max` matches is refused. */
  atomIntProp(mol, prim, n, max = 12) {
    const out = new Int32Array(n).fill(-1);
    let left = n;
    for (let v = 0; v <= max && left > 0; v++) {
      for (const m of this.matches(mol, `[${prim}${v}]`)) {
        const i = m.atoms[0];
        if (out[i] < 0) left--;
        out[i] = v;
      }
    }
    if (left > 0) throw refusal(`SMARTS ${prim} for every atom`, `RDKit.js gave no [${prim}<n>] value up to ${max} for some atom`);
    return out;
  }

  /** Reaction SMIRKS compiled with get_rxn, in the order given (kept like the queries). */
  reactions(smirksList) {
    return smirksList.map((s) => {
      let r = this.#reactions.get(s);
      if (!r) {
        r = this.RDKit.get_rxn(s);
        if (!r) throw new ChemError("rdkit", `RDKit.js ${this.RDKit.version()} did not parse the reaction ${s}`);
        this.#reactions.set(s, r);
      }
      return r;
    });
  }

  free() {
    for (const q of this.#queries.values()) q.delete();
    for (const r of this.#reactions.values()) r.delete();
    this.#queries.clear();
    this.#reactions.clear();
  }
}

// RDKit.js's rdkitjson defaults, for the molecule it cannot write: get_json throws on a molecule with no atoms
const EMPTY = Object.freeze({ rdkitjson: { version: 12 },
  defaults: { atom: { z: 6, impHs: 0, chg: 0, nRad: 0, isotope: 0, stereo: "unspecified" }, bond: { bo: 1, stereo: "unspecified" } } });

/** rdkitjson of `mol` (RDKit.js's get_json): {defaults, molecules: [{atoms, bonds, extensions}]}. */
export function molJson(mol) {
  if (mol.get_num_atoms() === 0) return { ...structuredClone(EMPTY), molecules: [{ atoms: [], bonds: [] }] };
  return JSON.parse(mol.get_json());
}

/** A molecule from rdkitjson, with the atoms and bonds in the order given. Stereo is taken as written
 * ({assignStereo: false}): with RDKit's default, re-reading drops E/Z labels the JSON carries. */
export function fromJson(RDKit, doc, what = "the edited molecule") {
  const m = RDKit.get_mol(JSON.stringify(doc), JSON.stringify({ assignStereo: false }));
  if (!m || !m.is_valid()) {
    m?.delete();
    throw refusal("rdkitjson", `RDKit.js could not read back ${what} (rdkitjson)`);
  }
  return m;
}

/** The graph the ports need → {atoms: [{z, chg, impHs, nRad, isotope}], bonds: [{a, b, type, arom}], n, nbrBonds
 * (bond indices per atom, in bond order: RDKit's neighbour order), inRing, hNoNbr(), totalValence(), hyb()}. A bond
 * that is not single, double, triple or aromatic has a string type (e.g. "DATIVE") the ports refuse. */
export function graph(ctx, mol) {
  const j = molJson(mol);
  const m = j.molecules[0];
  const dA = j.defaults.atom;
  const dB = j.defaults.bond;
  const ext = (m.extensions || []).find((e) => e.name === "rdkitRepresentation") || {};
  const atoms = (m.atoms || []).map((a) => ({
    z: a.z ?? dA.z, chg: a.chg ?? dA.chg, impHs: a.impHs ?? dA.impHs, nRad: a.nRad ?? dA.nRad, isotope: a.isotope ?? dA.isotope,
  }));
  const aromatic = new Set(ext.aromaticBonds || []);
  const bonds = (m.bonds || []).map((b, i) => {
    const bo = b.bo ?? dB.bo;
    let type = aromatic.has(i) ? BOND.AROMATIC : ({ 1: BOND.SINGLE, 2: BOND.DOUBLE, 3: BOND.TRIPLE })[bo];
    if (type === undefined) type = BOND_NAMES[bo] || `bo${bo}`;
    return { a: b.atoms[0], b: b.atoms[1], type, arom: aromatic.has(i) };
  });
  const n = atoms.length;
  const nbrBonds = Array.from({ length: n }, () => []);
  bonds.forEach((b, i) => { nbrBonds[b.a].push(i); nbrBonds[b.b].push(i); });
  const inRing = new Uint8Array(n);
  for (const r of ext.atomRings || []) for (const i of r) inRing[i] = 1;
  const g = { atoms, bonds, n, nbrBonds, inRing, rings: ext.atomRings || [] };
  let h = null;
  let v = null;
  let hyb = null;
  g.hNoNbr = () => (h ??= ctx.atomIntProp(mol, "h", n)); // getTotalNumHs(false)
  g.totalValence = () => (v ??= ctx.atomIntProp(mol, "v", n, 16)); // getTotalValence()
  g.hyb = () => { // getHybridization(): sp, sp2, sp3, or "other"
    if (hyb) return hyb;
    hyb = new Array(n).fill("other");
    for (const [prim, name] of [["^1", "sp"], ["^2", "sp2"], ["^3", "sp3"]]) for (const i of ctx.firstAtoms(mol, `[${prim}]`)) hyb[i] = name;
    return hyb;
  };
  return g;
}

/** The atom at the other end of bond `b` from atom `i`. */
export const other = (b, i) => (b.a === i ? b.b : b.a);
