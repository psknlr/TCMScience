# Prepared docking fixtures (PDBQT)

Written by `studio/scripts/make_dock_fixtures.py` from `../3ptb_pocket.pdb` (PDB entry 3PTB, bovine trypsin with
benzamidine; wwPDB data are CC0 1.0) with RDKit 2026.09.1, Meeko 0.8.0 and
`bioagent.docking.prep`; `--check` regenerates them and fails on any difference. They are the inputs of the prepared
mode (`bioagent.docking.prepared`, `bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt`) and of the browser's docking job.

- `3ptb_receptor.pdbqt`: the pocket's protein as `prep.prepare_receptor` writes it (waters and BEN removed; meeko
  residue templates, polar hydrogens, Gasteiger charges, AutoDock atom types). SHA-256
  `f0728cc12f64538310570ded883b305896df6f6cb86e50c72a419ba1386edd13`.
- `ligands.pdbqt`: six ligands as MODEL blocks, each named by a `REMARK  Name = <name>` line, embedded from SMILES
  with `prep.prepare_ligand(name, smiles, seed=42)` (ETKDG v3, MMFF94, meeko; protonation as written).
  SHA-256 `762d9b38ec608208bd33e5571f88e6ae0052ff38f93a655fb0e29e4cc77388e5`.
- `ben_crystal.pdbqt`: BEN in its crystal pose, the reference ligand: the crystal's heavy atoms with bond orders from
  `NC(=N)c1ccccc1` (`prep.cocrystal_ligand`), hydrogens added at computed positions (`Chem.AddHs(addCoords=True)`),
  meeko's PDBQT with the coordinates kept. SHA-256 `a58af3f6373183be9034a71766ea56658736daef13bd7cfef8733b8b766bf5b2`.

| ligand | SMILES |
|---|---|
| benzamidine | `NC(=N)c1ccccc1` |
| p_methylbenzamidine | `Cc1ccc(cc1)C(N)=N` |
| baicalein | `O=c1cc(-c2ccccc2)oc2cc(O)c(O)c(O)c12` |
| ibuprofen | `CC(C)Cc1ccc(cc1)C(C)C(=O)O` |
| ferulic_acid | `COc1cc(/C=C/C(=O)O)ccc1O` |
| paeonol | `COc1ccc(C(C)=O)c(O)c1` |

The box the pipeline builds around the reference (8 Å of padding, at least 18 Å a side): centre
(-1.8555, 14.366, 16.748), size (18, 18, 18) Å.

What native AutoDock Vina 1.2.7 gives on these files is in `studio/web/test/fixtures/vina_parity.json`.
