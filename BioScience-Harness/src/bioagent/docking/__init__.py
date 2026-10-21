"""Molecular docking: receptor and ligand preparation, AutoDock Vina, validation.

``prep``          receptors (meeko templates, polar hydrogens, Gasteiger charges) and
                  ligands (RDKit embedding, meeko PDBQT), and co-crystal ligands
``engine``        docking with AutoDock Vina (Vina or Vinardo scoring)
``interactions``  hydrogen bonds, salt bridges and hydrophobic contacts of a pose
``pipeline``      receptor + ligands + box to poses, scores, a redocking check and a
                  report
``prepared``      the same from prepared inputs (a rigid receptor and ligands as PDBQT),
                  with numpy only: the docking the browser runs (Vina as WebAssembly)
``commands``      the ``bioagent dock`` subcommand

RDKit, meeko and vina are optional dependencies (the ``docking`` extra), imported when
docking runs.
"""
