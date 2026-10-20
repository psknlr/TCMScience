// The omics jobs science.mjs --mode omics runs twice, as browser jobs (science-check.mjs) and natively (omics_native.py
// runs the command each job stands for): the same parameters on both sides, the files named as the page's project
// knows them (the files omics_native.py prepare writes). The parameters are the reference manifest's
// (BioScience-Harness/src/bioagent/data/pipeline_references.json): omics_counts_world for RNA-seq, sc_world for
// single-cell, whose sample sheet here names count tables in place of its Cell Ranger directories.

export const OMICS_JOBS = Object.freeze([
  {
    key: "rnaseq",
    kind: "pipeline.rnaseq",
    params: { counts: "counts.tsv", samples: "samples.tsv", design: "~ condition", contrast: "condition,treated,control", de_backend: "builtin", alpha: 0.05 },
  },
  {
    key: "scrna",
    kind: "pipeline.scrna",
    params: { source: "sc_samples.csv", analysis_backend: "builtin", integration: "harmony", n_top_genes: 600, seed: 0 },
  },
]);
