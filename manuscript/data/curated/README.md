# Curated values

Numbers that exist in this repository only in its documentation, because the run that
produced them needs data the repository does not redistribute (the source snapshots of the
葛根芩连汤 case study, the PubChem BioAssay download, the full end-to-end environment).

Every row names the file and line it was taken from (`source`) and a verbatim fragment of
that line (`quote`). `code/extract_data.py` refuses to build if any fragment is no longer on
its line, so a value here cannot drift from the document it cites without the build saying
so. Values derived from these (fold enrichments, hypergeometric P values, Wilson intervals)
are recomputed by the figure code and checked against the reported ones where the document
reports them.

| File | What it holds | Source |
| --- | --- | --- |
| `np_counts.csv` | the 葛根芩连汤 case study's data volumes and enrichment counts | `README.md`, `BioScience-Harness/docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md` |
| `np_screening_proteins.csv` | active / tested compound–protein pairs per protein in the PubChem screening data | same |
| `np_thresholds.csv` | the screening analysis at three minimum-coverage thresholds | spec, §4 |
| `np_threshold_pathways.csv` | pathways passing BH and the permutation test at each threshold | spec, §4 |
| `np_disease_sets.csv` | measured targets against type 2 diabetes genes under five definitions | spec, §4 |
| `robustness_holdouts.csv` | the released hypotheses' q values with evidence held out | `docs/falsifiable-studies.md` |
| `ablation_rounds.csv` | errors the full stack released in each benchmark round | `docs/governance-ablation.md`, committed results |
| `e2e_case3_claims.csv` | case 3's drafted claims and verdicts (docking needs AutoDock Vina) | `docs/end-to-end-cases.md` |
