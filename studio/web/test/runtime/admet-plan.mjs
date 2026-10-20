// The ADMET jobs science.mjs --mode admet runs twice, as browser jobs (science-check.mjs) and natively (admet_native.py
// runs the command each job stands for, tcmstudio.kinds admet.models and pipeline.admet): the same parameters on both
// sides, the files named as the page's project knows them (the files admet_native.py prepare writes).
//
// The archive is the small known-answer one (BioScience-Harness/tests/admet_world.py make_archive(seed=0): two
// endpoints, byte-reproducible), or, with science.mjs --archive, a real TDC ADMET group archive (the live-data
// workflow's, every endpoint). The molecules are admet_native.py's set: drug-like molecules, TCM natural products,
// salts, molecules the browser chemistry refuses (metal complexes, a charge only RDKit's Reionizer or the Uncharger's
// canonical ranking would place, a dative bond, one of more than 400 atoms) and one SMILES that is
// not one.

/** The endpoints of the known-answer archive (admet_world's members). */
export const WORLD_ENDPOINTS = Object.freeze(["caco2_wang", "hia_hou"]);
/** The project files' names. */
export const ARCHIVE_FILE = "tdc_admet_world.zip";
export const REAL_ARCHIVE_FILE = "tdc_admet_group.zip";
export const MOLECULES_FILE = "admet_molecules.csv";

/**
 * The two jobs → {archive, molecules, models: {kind, params}, pipeline: {kind, params}}. real: the archive is a real TDC
 * ADMET group archive (its 22 endpoints, unless `endpoints` names some); else the known-answer archive's two endpoints.
 * build_models' defaults (seed 0, max_iter 600) are given explicitly, so that both sides read them from here.
 */
export function admetPlan({ real = false, endpoints = null } = {}) {
  const archive = real ? REAL_ARCHIVE_FILE : ARCHIVE_FILE;
  const eps = endpoints?.length ? [...endpoints] : real ? null : [...WORLD_ENDPOINTS];
  return Object.freeze({
    real, archive, molecules: MOLECULES_FILE,
    models: { kind: "admet.models", params: { archive, ...(eps ? { endpoints: eps } : {}), seed: 0, max_iter: 600 } },
    pipeline: { kind: "pipeline.admet", params: { molecules: MOLECULES_FILE } },
  });
}
