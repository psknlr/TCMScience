"""Protein structure: prediction, quality, comparison.

``pdbio``     reading and writing PDB coordinate files
``geometry``  superposition, RMSD, TM-score, GDT, sequence alignment, dihedrals
``dssp``      secondary structure by the Kabsch-Sander hydrogen-bond definition
``predict``   structure predictors: ESMFold through the ESM Atlas service, ESMFold
              locally (transformers) and ColabFold when installed
``fold``      the pipeline from sequences to predicted models, their quality and their
              agreement with a reference structure
"""
