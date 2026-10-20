// The docking job science.mjs --mode dock runs twice, as a browser job (science-check.mjs) and natively (dock_native.py
// runs the command the job stands for, tcmstudio.kinds pipeline.dock in its prepared mode: `bioagent dock RECEPTOR.pdbqt
// LIGANDS.pdbqt` with native AutoDock Vina 1.2.7): the same parameters on both sides, the files named as the page's
// project knows them (the files dock_native.py prepare copies).
//
// The files are BioScience-Harness/tests/fixtures/docking/prepared/ (studio/scripts/make_dock_fixtures.py writes them):
// 3PTB's pocket as a rigid receptor, six ligands as MODEL blocks (benzamidine and an analogue, four TCM constituents),
// and the crystal ligand BEN in its crystal pose as the reference, which also builds the box and is redocked to validate
// it. The search settings are the pipeline's defaults, given explicitly so that both sides read them from here; seed 42
// redocks BEN within 2 Å natively and in WebAssembly (measured: 1.92 Å and 1.14 Å, atom order).

/** Where the files come from (repository-relative) and their names as project files. */
export const FIXTURE_DIR = "BioScience-Harness/tests/fixtures/docking/prepared";
export const RECEPTOR_FILE = "3ptb_receptor.pdbqt";
export const LIGANDS_FILE = "ligands.pdbqt";
export const REFERENCE_FILE = "ben_crystal.pdbqt";
/** The ligands as the pipeline names them (their REMARK Name lines), in the file's order, and the reference's name. */
export const LIGANDS = Object.freeze(["benzamidine", "p_methylbenzamidine", "baicalein", "ibuprofen", "ferulic_acid", "paeonol"]);
export const REFERENCE = "BEN";

/** The job → {files, ligands, reference, job: {kind, params}}. */
export function dockPlan() {
  return Object.freeze({
    files: [RECEPTOR_FILE, LIGANDS_FILE, REFERENCE_FILE], ligands: LIGANDS, reference: REFERENCE,
    job: {
      kind: "pipeline.dock",
      params: { receptor: RECEPTOR_FILE, ligands: LIGANDS_FILE, reference_ligand: REFERENCE_FILE, scoring: "vina", exhaustiveness: 8, poses: 9, seed: 42 },
    },
  });
}

/**
 * The first pose of Vina's output (its first MODEL block, the MODEL and ENDMDL lines left out; all of it, blank lines
 * dropped, when it has no MODEL record), as text ending with "\n": bioagent.docking.prepared.pose_models(text)[0] joined
 * as make_dock_fixtures.py first_pose joins it, the pose both engines re-score (dock_native.py holds the two equal);
 * null when no MODEL block is closed.
 */
export function firstPose(text) {
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  if (!lines.some((l) => l.startsWith("MODEL"))) return `${lines.filter(Boolean).join("\n")}\n`;
  const models = [];
  let current = null;
  for (const line of lines) {
    if (line.startsWith("MODEL")) current = [];
    else if (line.startsWith("ENDMDL")) { if (current) models.push(current); current = null; }
    else if (current) current.push(line);
  }
  return models.length ? `${models[0].join("\n")}\n` : null;
}

/** A float64's bits as 16 hex digits, big-endian (dock_native.py: struct.pack(">d", x).hex()): equal only when every
 * bit is, -0 and 0 apart. */
export function float64Bits(x) {
  const v = new DataView(new ArrayBuffer(8));
  v.setFloat64(0, x);
  return v.getBigUint64(0).toString(16).padStart(16, "0");
}
