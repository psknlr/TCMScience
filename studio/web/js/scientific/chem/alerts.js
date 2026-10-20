// rules.alerts (bioagent/admet/rules.py) in the browser: RDKit's FilterCatalog with PAINS_A, PAINS_B, PAINS_C, BRENK
// and NIH, matched with RDKit.js. MinimalLib has no FilterCatalog; filter_catalogs.json holds the release's entries
// (studio/scripts/make_chem_fixtures.py: read from RDKit 2026.09.1's Filters.cpp and *.in files and checked to rebuild
// native RDKit's entries), each as SMARTS RDKit.js parses to the entry's own pattern. Loading checks every entry once
// more here: RDKit.js must parse it and write it back exactly as native RDKit writes it ("written").
//
// An entry matches as SmartsMatcher matches (at least min_count unique matches, RDKit's default substructure
// parameters); the alerts are (FilterSet, description) in catalog order, each pair kept once, first occurrence first,
// as rules.alerts de-duplicates them. An entry that could not be ported, or that RDKit.js does not read back exactly,
// is listed in `unported`, and then no molecule gets alerts here: without that entry the list could be short.

import { ChemError } from "./rdkit.js";
import { refusal } from "./molecule.js";

export const CATALOGS_SCHEMA = "tcmstudio.filter-catalogs/1";

export class AlertCatalog {
  /** `doc`: filter_catalogs.json; `ctx`: a molecule.js Ctx (its queries are compiled here and freed with it). */
  constructor(ctx, doc) {
    if (doc?.schema !== CATALOGS_SCHEMA) throw new TypeError(`not a ${CATALOGS_SCHEMA} document`);
    this.ctx = ctx;
    this.release = doc.rdkit;
    this.catalogs = doc.catalogs;
    this.entries = [];
    this.unported = (doc.unported || []).map((e) => ({ catalog: e.catalog, description: e.description, reason: e.reason }));
    for (const e of doc.entries) {
      let written = null;
      try {
        written = ctx.qmol(e.smarts).get_smarts();
      } catch (err) {
        if (!(err instanceof ChemError)) throw err; // RDKit.js itself failed: not a property of the entry
        this.unported.push({ catalog: e.catalog, description: e.description, reason: `RDKit.js: ${err.message}` });
        continue;
      }
      if (written !== e.written) {
        this.unported.push({ catalog: e.catalog, description: e.description,
          reason: `RDKit.js writes the parsed pattern as ${written}, native RDKit as ${e.written}` });
        continue;
      }
      this.entries.push({ filterSet: e.filter_set, description: e.description, smarts: e.smarts, minCount: e.min_count });
    }
  }

  /** The alerts of a standardised molecule → [[filter_set, description], ...]. Refused (ChemError "refused") while any
   * entry is unported. */
  alerts(mol) {
    if (this.unported.length) {
      const names = this.unported.slice(0, 3).map((e) => `${e.catalog} ${e.description}`).join(", ");
      throw refusal("FilterCatalog entries", `${this.unported.length} FilterCatalog entr${this.unported.length === 1 ? "y is" : "ies are"} ` +
        `not matched exactly here (${names}${this.unported.length > 3 ? ", …" : ""}), so the alerts would be incomplete`);
    }
    const out = [];
    const seen = new Set();
    for (const e of this.entries) {
      const hit = e.minCount <= 1 ? this.ctx.hasMatch(mol, e.smarts) : this.ctx.matches(mol, e.smarts).length >= e.minCount;
      if (!hit) continue;
      const key = `${e.filterSet}\u0000${e.description}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push([e.filterSet, e.description]);
    }
    return out;
  }
}
