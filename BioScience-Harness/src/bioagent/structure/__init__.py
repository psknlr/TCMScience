"""Protein structure: prediction, design, dynamics, quality, comparison.

``pdbio``     reading and writing PDB coordinate files
``mmcif``     the ``_atom_site`` table of an mmCIF model
``geometry``  superposition, RMSD, TM-score, GDT, sequence alignment, dihedrals
``dssp``      secondary structure by the Kabsch-Sander hydrogen-bond definition
``predict``   structure predictors: ESMFold through the ESM Atlas service, ESMFold
              locally (transformers) and ColabFold when installed
``fold``      the pipeline from sequences to predicted models, their quality and their
              agreement with a reference structure
``tasks``     what the compute tasks below share: refusal with reasons, the model spec,
              the record of what ran
``complex``   complex prediction as a task (chains and copies, ligands, modifications,
              constraints) and its result (mmCIF, per-chain pLDDT, pTM / ipTM)
``design``    sequence design as a task (backbone, chains to design, fixed positions)
``dynamics``  relaxation and molecular dynamics as a task (force field, solvent, time)
``engines``   Boltz, Chai-1, ProteinMPNN and OpenMM adapters: detect, refuse with the
              reason, run as a long job (``backends.jobs``), validate, read
"""
