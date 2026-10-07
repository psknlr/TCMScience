# Docking fixtures

- `3ptb_pocket.pdb`: bovine trypsin with benzamidine, PDB entry 3PTB (Marquart et al.
  1983), from files.rcsb.org, trimmed for the tests to the 81 residues with an atom
  within 12 Å of the ligand (BEN), with the ligand kept. wwPDB data are CC0 1.0.

Benzamidine's amidine sits against Asp189 at the bottom of the S1 pocket; redocking it
into this pocket is the check the pipeline runs before it reports any score.
