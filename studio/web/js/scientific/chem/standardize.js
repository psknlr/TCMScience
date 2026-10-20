// chem.standardize (bioagent/admet/chem.py) in the browser: Chem.MolFromSmiles, then MolStandardize's Cleanup,
// FragmentParent (which runs Cleanup again, then LargestFragmentChooser) and Uncharger, as RDKit 2026.09.1 runs them.
// RDKit.js has no MolStandardize at all, so each step is ported from the release's sources (Code/GraphMol/
// MolStandardize: MolStandardize.cpp cleanupInPlace, Normalize.cpp, Charge.cpp, Fragment.cpp, Metal.cpp) on top of
// what RDKit.js does expose, with the release's own data (rdkit_tables.json, generated from those sources by
// studio/scripts/make_chem_fixtures.py and checked there against native RDKit):
//
//   Normalizer      the 22 default transforms, in place as normalizeInPlace applies them: the first transform that
//                   matches anywhere, at its first match, then the list again (at most 200 times). RDKit.js has only
//                   runReactants, whose product holds the matched fragment in another atom order; the atoms are
//                   labelled (isotope = index + 1) before the reaction and put back in their own order after it,
//                   chirality re-expressed for the new bond order, and the result must have the canonical SMILES of
//                   the plain product, so what follows sees RDKit's atom order exactly.
//   LargestFragmentChooser   ported (atom count with hydrogens, then weight, then SMILES), the kept atoms in order.
//   Uncharger       ported (Charge.cpp unchargeInPlace, canonicalOrdering).
//
// What is not ported is refused, never approximated: the molecule then needs native RDKit (the runner). A refusal
// names the first step that cannot be reproduced: "MetalDisconnector" (a metal bonded to N, O, F or to a non-metal it
// would cut), "Reionizer" (a charge correction would apply, or a stronger acid is protonated while a weaker one is
// ionised), "Uncharger canonical atom ranking" (the Uncharger's choice among acid anions or negative sites depends on
// Canon::rankMolAtoms, which RDKit.js does not expose), "Normalizer atom order" (a transform's product could not be put
// back in RDKit's order). On the parity corpus every molecule that is not refused gives native RDKit's canonical
// SMILES and feature row, and every refusal is one the native intermediates predict.
//
// A molecule of more than MAX_ATOMS atoms is refused before RDKit.js reads it ("RDKit.js molecule size"): RDKit.js
// 2026.09.1 runs out of stack on long chains and rings (measured: 550-atom chains, rings, PEGs, polyphenylenes and
// peptides prepare; at 600 atoms get_descriptors throws or traps, at 650 get_smiles traps, at 1,000 get_mol does, and a
// 700-atom chain did not return in 10 minutes), and a trap leaves the module's memory unfit for any later molecule.

import { ChemError } from "./rdkit.js";
import { fromJson, molJson, refusal } from "./molecule.js";

export const MAX_RESTARTS = 200; // Normalizer's
/** The most atoms (smilesAtoms) a molecule may have here: two thirds of the smallest size measured to fail. */
export const MAX_ATOMS = 400;
export const REFUSALS = Object.freeze({
  metal: "MetalDisconnector",
  reionizer: "Reionizer",
  ranking: "Uncharger canonical atom ranking",
  order: "Normalizer atom order",
  size: "RDKit.js molecule size",
});
// what MinimalLib's get_mol reads as something other than SMILES (common.h mol_from_input)
const NOT_SMILES = ["M  END", "commonchem", "rdkitjson"];

/** The atoms a SMILES writes (bracket atoms, the organic subset, aromatic atoms, *) before the first whitespace after
 * it (the parser stops there: a name or CXSMILES follows), so at least the atoms RDKit reads from it, heavy or not. */
export function smilesAtoms(smiles) {
  const s = String(smiles).trimStart().split(/\s/, 1)[0];
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "[") {
      n++;
      const j = s.indexOf("]", i + 1);
      if (j < 0) break;
      i = j;
    } else if ("BCNOPSFI*bcnops".includes(ch)) {
      n++;
      if ((ch === "C" && s[i + 1] === "l") || (ch === "B" && s[i + 1] === "r")) i++;
    }
  }
  return n;
}

/** MolStandardize's data from rdkit_tables.json. */
export class StandardizeData {
  constructor(doc) {
    if (doc?.schema !== "tcmstudio.rdkit-tables/1") throw new TypeError("not a tcmstudio.rdkit-tables/1 document");
    const ms = doc.molstandardize;
    this.normalizations = ms.normalizations.map(([name, smirks]) => ({ name, smirks, reactant: smirks.split(">>")[0] }));
    this.acids = ms.acid_base_pairs.map((p) => p[1]);
    this.bases = ms.acid_base_pairs.map((p) => p[2]);
    this.chargeCorrections = ms.charge_corrections.map((c) => c[1]);
    this.metal = [ms.metal_nof, ms.metal_non];
    this.uncharger = ms.uncharger;
    this.early = new Set(ms.early_atoms);
  }
}

// what Python's str.isprintable() rejects: the categories Other (Cc, Cf, Cs, Co, Cn) and Separator (Zs, Zl, Zp), the
// ASCII space apart. The JS engine's Unicode tables can be a version ahead of Python's, which only changes code points
// one of them still has unassigned (Cn).
const UNPRINTABLE = /^[\p{C}\p{Z}]$/u;

/** Python's repr() of a str, as chem.standardize's "{smiles!r} is not a valid SMILES" writes it: an unprintable
 * character as \xhh, \uhhhh or \Uhhhhhhhh. */
export function pyRepr(s) {
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  let out = q;
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (ch === "\\") out += "\\\\";
    else if (ch === q) out += "\\" + q;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch !== " " && UNPRINTABLE.test(ch)) {
      const h = c.toString(16);
      out += c <= 0xff ? "\\x" + h.padStart(2, "0") : c <= 0xffff ? "\\u" + h.padStart(4, "0") : "\\U" + h.padStart(8, "0");
    } else out += ch;
  }
  return out + q;
}

const atomsOf = (doc) => doc.molecules[0].atoms || [];
const bondsOf = (doc) => doc.molecules[0].bonds || [];
const withDefaults = (defaults, x) => ({ ...defaults, ...x });
// rdkitjson without RDKit's perception (re-derived when it is read back) or anything indexed by atom or bond
const bare = (doc, atoms, bonds) => ({ ...doc, molecules: [{ atoms, bonds }] });

// ---- Normalizer ---------------------------------------------------------------------------------------------------

// the number of swaps (mod 2) that turn `from` into `to` (same members)
function oddPermutation(from, to) {
  const pos = new Map(to.map((x, i) => [x, i]));
  const p = from.map((x) => pos.get(x));
  const seen = new Uint8Array(p.length);
  let odd = false;
  for (let i = 0; i < p.length; i++) {
    if (seen[i]) continue;
    let len = 0;
    for (let j = i; !seen[j]; j = p[j]) { seen[j] = 1; len++; }
    if (len % 2 === 0) odd = !odd;
  }
  return odd;
}

/** One application of `rxn` to `mol` as runReactant applies it in place → the new molecule, or null when it does not
 * match. `mol` is not consumed. */
function applyInPlace(ctx, mol, rxn) {
  const RDKit = ctx.RDKit;
  const doc = molJson(mol);
  const dA = doc.defaults.atom;
  const dB = doc.defaults.bond;
  const atoms = atomsOf(doc).map((a) => withDefaults(dA, a));
  const bonds = bondsOf(doc).map((b) => withDefaults(dB, b));
  const n = atoms.length;
  const labelled = fromJson(RDKit, bare(doc, atoms.map((a, i) => ({ ...a, isotope: i + 1 })), bonds), "a molecule labelled for the Normalizer");
  const reactants = new RDKit.MolList();
  let products;
  try {
    reactants.append(labelled);
    products = rxn.run_reactants(reactants, 1);
  } finally {
    reactants.delete();
    labelled.delete();
  }
  let pdoc = null;
  try {
    for (let k = 0; k < products.size(); k++) {
      const list = products.get(k);
      if (k === 0 && list.size()) {
        const pm = list.at(0);
        pdoc = molJson(pm);
        pm.delete();
      }
      list.delete();
    }
  } finally {
    products.delete();
  }
  if (!pdoc) return null;

  const pdA = pdoc.defaults.atom;
  const pdB = pdoc.defaults.bond;
  const patoms = atomsOf(pdoc).map((a) => withDefaults(pdA, a));
  const label = patoms.map((a) => a.isotope - 1);
  const productOf = new Map();
  label.forEach((orig, k) => {
    if (!(orig >= 0 && orig < n) || productOf.has(orig)) throw refusal(REFUSALS.order, "a Normalizer product lost its atom labels");
    productOf.set(orig, k);
  });
  const pbonds = bondsOf(pdoc).map((b) => withDefaults(pdB, b));
  const pairKey = (u, v) => (u < v ? `${u},${v}` : `${v},${u}`);
  const productBond = new Map(pbonds.map((b, k) => [pairKey(label[b.atoms[0]], label[b.atoms[1]]), k]));

  // A: the molecule in its own atom and bond order, the product's atoms and bonds where the reaction touched them
  const outAtoms = atoms.map((a, i) => (productOf.has(i) ? { ...patoms[productOf.get(i)], isotope: a.isotope } : { ...a }));
  const outBonds = [];
  const used = new Set();
  for (const b of bonds) {
    const [u, v] = b.atoms;
    const inU = productOf.has(u);
    if (inU !== productOf.has(v)) throw refusal(REFUSALS.order, "a Normalizer product split a bond between its fragment and the rest");
    if (!inU) {
      outBonds.push({ ...b });
      continue;
    }
    const k = productBond.get(pairKey(u, v));
    if (k === undefined) continue; // removed by the transform, as runReactant removes it
    used.add(k);
    const pb = pbonds[k];
    const reversed = label[pb.atoms[0]] !== u;
    const stereoAtoms = pb.stereoAtoms?.map((x) => label[x]);
    outBonds.push({ ...pb, atoms: [u, v], ...(stereoAtoms ? { stereoAtoms: reversed ? stereoAtoms.reverse() : stereoAtoms } : {}) });
  }
  pbonds.forEach((pb, k) => {
    if (used.has(k)) return;
    const stereoAtoms = pb.stereoAtoms?.map((x) => label[x]);
    outBonds.push({ ...pb, atoms: [label[pb.atoms[0]], label[pb.atoms[1]]], ...(stereoAtoms ? { stereoAtoms } : {}) });
  });
  // a chiral tag is relative to the order of the atom's bonds: re-express it for A's bond order
  const neighbours = (list, i) => list.filter((b) => b.atoms[0] === i || b.atoms[1] === i).map((b) => (b.atoms[0] === i ? b.atoms[1] : b.atoms[0]));
  const productBondsByLabel = pbonds.map((b) => ({ atoms: [label[b.atoms[0]], label[b.atoms[1]]] }));
  for (const [i, k] of productOf) {
    const s = patoms[k].stereo;
    if (s !== "cw" && s !== "ccw") continue;
    const before = neighbours(productBondsByLabel, i);
    const after = neighbours(outBonds, i);
    if (before.length !== after.length || !before.every((x) => after.includes(x))) {
      throw refusal(REFUSALS.order, "a Normalizer transform changed the neighbours of a stereocentre");
    }
    if (oddPermutation(before, after)) outAtoms[i].stereo = s === "cw" ? "ccw" : "cw";
  }
  const ordered = fromJson(RDKit, bare(doc, outAtoms, outBonds), "a Normalizer product");

  // B: the product followed by the untouched atoms, in the product's own order (no stereo re-expressed)
  const rest = [];
  for (let i = 0; i < n; i++) if (!productOf.has(i)) rest.push(i);
  const index = new Map([...label.map((orig, k) => [orig, k]), ...rest.map((i, r) => [i, patoms.length + r])]);
  const refAtoms = [...patoms.map((a, k) => ({ ...a, isotope: atoms[label[k]].isotope })), ...rest.map((i) => ({ ...atoms[i] }))];
  const refBonds = [
    ...pbonds.map((b) => ({ ...b })),
    ...bonds.filter((b) => !productOf.has(b.atoms[0])).map((b) => ({
      ...b, atoms: b.atoms.map((x) => index.get(x)), ...(b.stereoAtoms ? { stereoAtoms: b.stereoAtoms.map((x) => index.get(x)) } : {}),
    })),
  ];
  let same = false;
  try {
    const reference = fromJson(RDKit, bare(pdoc, refAtoms, refBonds), "a Normalizer product");
    same = reference.get_smiles() === ordered.get_smiles();
    reference.delete();
  } catch (err) {
    ordered.delete();
    throw err;
  }
  if (!same) {
    ordered.delete();
    throw refusal(REFUSALS.order, "a Normalizer product could not be put back in RDKit's in-place atom order");
  }
  return ordered;
}

/** Normalizer::normalizeInPlace → {mol, applied}. `mol` is consumed when a transform applies. A transform is run only
 * when its reactant template matches (the same SMARTS, matched with the same defaults as the reaction's). */
function normalize(ctx, mol, data) {
  const rxns = ctx.reactions(data.normalizations.map((t) => t.smirks));
  let cur = mol;
  let applied = 0;
  try {
    for (let restart = 0; restart < MAX_RESTARTS; restart++) {
      let hit = false;
      for (let k = 0; k < rxns.length; k++) {
        if (!ctx.hasMatch(cur, data.normalizations[k].reactant)) continue;
        const next = applyInPlace(ctx, cur, rxns[k]);
        if (!next) throw refusal(REFUSALS.order, `RDKit.js's reaction gave no product where its template matches (${data.normalizations[k].name})`);
        if (cur !== mol) cur.delete();
        cur = next;
        applied++;
        hit = true;
        break;
      }
      if (!hit) break;
    }
  } catch (err) {
    if (cur !== mol) cur.delete();
    throw err;
  }
  if (cur !== mol) mol.delete();
  return { mol: cur, applied };
}

/** Whether Reionizer::reionizeInPlace would change `mol` (or give up part-way): a charge correction matches, or the
 * strongest protonated acid comes before the weakest ionised one in the acid-base list. */
export function reionizerWouldAct(ctx, mol, data) {
  for (const s of data.chargeCorrections) if (ctx.hasMatch(mol, s)) return true;
  let sp = -1;
  for (let k = 0; k < data.acids.length; k++) if (ctx.hasMatch(mol, data.acids[k])) { sp = k; break; }
  let wi = -1;
  for (let k = data.bases.length - 1; k >= 0; k--) if (ctx.hasMatch(mol, data.bases[k])) { wi = k; break; }
  return sp >= 0 && wi >= 0 && sp < wi;
}

// cleanupInPlace: removeHs (RDKit.js's get_mol has already removed them, as MolFromSmiles has), MetalDisconnector,
// Normalizer, Reionizer; `mol` is consumed
function cleanup(ctx, mol, data, steps) {
  if (data.metal.some((s) => ctx.hasMatch(mol, s))) {
    throw refusal(REFUSALS.metal, "standardisation needs RDKit's MetalDisconnector (a metal bonded to N, O, F or a non-metal), " +
      "which RDKit.js does not have");
  }
  const r = normalize(ctx, mol, data);
  if (r.applied) steps.push(`normalize:${r.applied}`);
  if (reionizerWouldAct(ctx, r.mol, data)) {
    r.mol.delete();
    throw refusal(REFUSALS.reionizer, "standardisation needs RDKit's Reionizer (it would move a charge here), which RDKit.js does not have");
  }
  return r.mol;
}

// ---- LargestFragmentChooser (preferOrganic false, useAtomCount, countHeavyAtomsOnly false) --------------------------
function largestFragment(ctx, mol, steps) {
  const fr = mol.get_frags();
  const list = fr.molList;
  let frags;
  let best = -1;
  try {
    if (list.size() <= 1) return mol;
    frags = JSON.parse(fr.mappings).fragsMolAtomMapping;
    const doc = molJson(mol);
    const dA = doc.defaults.atom;
    const atoms = atomsOf(doc).map((a) => withDefaults(dA, a));
    const H = ctx.atomIntProp(mol, "h", atoms.length);
    let bAtoms = 0;
    let bWeight = 0;
    let bSmiles = "";
    for (let f = 0; f < frags.length; f++) {
      const fm = list.at(f);
      const smiles = fm.get_smiles();
      fm.delete();
      let count = 0;
      let weight = 0;
      for (const i of frags[f]) {
        const a = atoms[i];
        count += 1 + H[i];
        weight += 100 * a.z + a.isotope - a.chg * 0.1; // as Fragment.cpp adds: this, then the hydrogens
        weight += H[i];
      }
      if (best >= 0 && count < bAtoms) continue;
      if (best >= 0 && count === bAtoms && weight < bWeight) continue;
      if (best >= 0 && count === bAtoms && weight === bWeight && smiles > bSmiles) continue;
      best = f;
      bAtoms = count;
      bWeight = weight;
      bSmiles = smiles;
    }
  } finally {
    list.delete();
  }
  const doc = molJson(mol);
  const keep = new Set(frags[best]);
  const index = new Map();
  atomsOf(doc).forEach((a, i) => { if (keep.has(i)) index.set(i, index.size); });
  const atoms = atomsOf(doc).filter((a, i) => keep.has(i));
  const bonds = bondsOf(doc).filter((b) => keep.has(b.atoms[0]) && keep.has(b.atoms[1])).map((b) => ({
    ...b, atoms: b.atoms.map((x) => index.get(x)), ...(b.stereoAtoms ? { stereoAtoms: b.stereoAtoms.map((x) => index.get(x)) } : {}),
  }));
  const out = fromJson(ctx.RDKit, bare(doc, atoms, bonds), "the largest fragment");
  mol.delete();
  steps.push("fragment");
  return out;
}

// ---- Uncharger (Charge.cpp unchargeInPlace; canonicalOrdering true, force false, protonationOnly false) -----------

/** What the Uncharger sees in `mol`: its patterns' matches, H counts, the charge it cannot remove, and the order-free
 * parts of its choices. */
function unchargerView(ctx, mol, data) {
  const doc = molJson(mol);
  const dA = doc.defaults.atom;
  const atoms = atomsOf(doc).map((a) => withDefaults(dA, a));
  const n = atoms.length;
  const H = Array.from(ctx.atomIntProp(mol, "h", n)); // getTotalNumHs()
  const U = data.uncharger;
  const p = ctx.firstAtoms(mol, U.pos_h);
  const q = ctx.firstAtoms(mol, U.pos_noh);
  const neg = ctx.firstAtoms(mol, U.neg);
  const acid = ctx.firstAtoms(mol, U.neg_acid);
  const early = (i) => data.early.has(atoms[i].z);
  const hDeltaNeg = (i) => (early(i) && !(H[i] > 0) ? 0 : early(i) ? -1 : 1);
  const hDeltaPos = (i) => {
    const ce = atoms[i].z === 6 || early(i);
    if (!ce && !(H[i] > 0)) return 0;
    return ce ? 1 : -1;
  };
  let qMatched = 0;
  for (const i of q) qMatched += atoms[i].chg;
  for (const i of p) if (hDeltaPos(i) === 0) qMatched += atoms[i].chg;
  const needsNeutralization = qMatched > 0 && (neg.length > 0 || acid.length > 0);
  const neighbours = Array.from({ length: n }, () => []);
  for (const b of bondsOf(doc)) { // bond order: RDKit's neighbour order
    neighbours[b.atoms[0]].push(b.atoms[1]);
    neighbours[b.atoms[1]].push(b.atoms[0]);
  }
  return { doc, atoms, H, p, neg, acid, qMatched, needsNeutralization, neighbours, hDeltaNeg, hDeltaPos };
}

/** Whether the Uncharger's result depends on Canon::rankMolAtoms. RDKit ranks the atoms only when
 * needsNeutralization; the ranks then order the acid anions (which of those sharing a positive neighbour keep their
 * charge) and the negative sites (which are neutralised when only some are). */
export function rankingMatters(v) {
  if (!v.needsNeutralization) return false;
  const groups = new Map();
  for (const i of v.acid) {
    const nb = v.neighbours[i].find((j) => v.atoms[j].chg > 0);
    if (nb !== undefined) groups.set(nb, [...(groups.get(nb) || []), i]);
  }
  for (const [k, g] of groups) if (g.length > v.atoms[k].chg) return true;
  const skipped = new Set([...groups.values()].flat());
  const acids = new Set(v.acid);
  const negs = [...v.neg.filter((i) => !acids.has(i)), ...v.acid.filter((i) => !skipped.has(i))];
  const surplus = negs.length - v.qMatched;
  const removable = negs.filter((i) => v.hDeltaNeg(i) !== 0).length;
  return surplus > 0 && surplus < removable;
}

function uncharge(ctx, mol, data, steps) {
  // every Uncharger pattern needs a charged atom
  if (!ctx.hasMatch(mol, "[!+0]")) return mol;
  const v = unchargerView(ctx, mol, data);
  if (rankingMatters(v)) {
    throw refusal(REFUSALS.ranking, "the Uncharger's choice here depends on RDKit's canonical atom ranking, which RDKit.js does not expose");
  }
  const { doc, atoms, H, p, neg, acid } = v;
  let changed = false;
  const remove = (i, charge, hDelta) => {
    H[i] += hDelta;
    atoms[i].impHs = H[i];
    atoms[i].chg -= charge;
    changed = true;
  };
  // with no ranking to apply (or none that matters) the sites go in atom order, as RDKit's iota ranks put them
  const acids = new Set(acid);
  const negAtoms = neg.filter((i) => !acids.has(i));
  const skip = new Int32Array(atoms.length);
  for (const i of acid) {
    for (const nb of v.neighbours[i]) {
      const fc = atoms[nb].chg;
      if (fc > 0) {
        if (skip[nb] < fc) {
          skip[nb] += 1;
          skip[i] = 1;
        }
        break;
      }
    }
  }
  for (const i of acid) if (!skip[i]) negAtoms.push(i);
  let surplus = negAtoms.length - v.qMatched; // int - unsigned in C++: it can go below zero, and then nothing stops the loop
  if (surplus) {
    for (const i of negAtoms) {
      const hd = v.hDeltaNeg(i);
      if (hd !== 0) {
        remove(i, -1, hd);
        if (!--surplus) break;
      }
    }
  }
  let net = 0;
  for (const a of atoms) net += a.chg;
  if (net > 0) {
    for (const i of p) {
      while (atoms[i].chg > 0 && net > 0) {
        const hd = v.hDeltaPos(i);
        if (hd === 0) break;
        remove(i, +1, hd);
        net--;
      }
      if (!net) break;
    }
  }
  if (!changed) return mol;
  const out = fromJson(ctx.RDKit, bare(doc, atoms, bondsOf(doc)), "the uncharged molecule");
  mol.delete();
  steps.push("uncharge");
  return out;
}

/**
 * chem.standardize(smiles) → {mol (the caller deletes it), steps}. Throws ChemError "invalid_smiles" with native's
 * message ("'C1CC' is not a valid SMILES"), or "refused" (need "runner", missing: the step) for what RDKit.js cannot
 * reproduce exactly. Whatever RDKit.js itself throws (get_mol catches RDKit's exceptions, so only a trap: the module
 * is then broken) is not caught here.
 */
export function standardize(ctx, smiles, data) {
  const text = String(smiles);
  if (NOT_SMILES.some((s) => text.includes(s))) {
    throw refusal("SMILES input", `${pyRepr(text)} would be read by RDKit.js as a mol block or JSON, not as SMILES`);
  }
  const atoms = smilesAtoms(text);
  if (atoms > MAX_ATOMS) {
    throw refusal(REFUSALS.size, `this SMILES has ${atoms} atoms; RDKit.js takes at most ${MAX_ATOMS} here (larger molecules ` +
      "exhaust its stack, which corrupts its memory)");
  }
  let mol = ctx.RDKit.get_mol(text);
  if (!mol || !mol.is_valid()) {
    mol?.delete();
    throw new ChemError("invalid_smiles", `${pyRepr(text)} is not a valid SMILES`);
  }
  const steps = [];
  try {
    mol = cleanup(ctx, mol, data, steps);
    mol = cleanup(ctx, mol, data, steps); // FragmentParent runs Cleanup again before choosing
    mol = largestFragment(ctx, mol, steps);
    mol = uncharge(ctx, mol, data, steps);
  } catch (err) {
    if (!mol.isDeleted()) mol.delete();
    throw err;
  }
  return { mol, steps };
}
