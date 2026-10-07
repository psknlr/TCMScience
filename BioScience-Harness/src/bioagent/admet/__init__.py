"""ADMET prediction from structure.

``chem``      molecule standardisation, descriptors and fingerprints (RDKit)
``rules``     drug-likeness rules (Lipinski, Veber, Egan, Ghose) and structural alerts
              (PAINS, Brenk, NIH) with RDKit's filter catalogues
``models``    22 models trained on the Therapeutics Data Commons ADMET benchmark group,
              each scored on its official scaffold-split test set, with model cards and
              an applicability domain
``pipeline``  molecules to a report of properties, alerts and predictions
``commands``  the ``bioagent admet`` subcommand

RDKit and scikit-learn are optional dependencies (the ``admet`` extra), imported when
used.
"""
