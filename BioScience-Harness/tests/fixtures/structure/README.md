# Structure fixtures

- `1ubq.pdb`: ubiquitin, PDB entry 1UBQ (Vijay-Kumar, Bugg & Cook 1987), from
  files.rcsb.org. wwPDB data are CC0 1.0.
- `ubq_esmfold.pdb`: the ESMFold model of the same sequence (76 residues), returned by the
  ESM Atlas service (api.esmatlas.com) on 2026-10-07; pLDDT on a 0-1 scale in the B-factor
  column, as the service writes it. ESMFold is MIT-licensed (facebookresearch/esm).

The tests compare the model with the crystal structure offline; the live test that folds
through the service is marked `integration`.
