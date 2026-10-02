"""Every TCM database in the architecture document, behind one API (``TCMDataHub``).

``catalog()`` lists the 66 sources with how each is reached; ``datasets`` holds the
download specifications; ``store`` loads files into SQLite; ``relations`` turns each
source's tables into one relation shape; ``hub`` puts it together, with governed live
calls to the connectors in ``providers.public_apis_tcm``.
"""

from .datasets import DATASETS, DatasetSpec, FileSpec, dataset
from .hub import ACCESS_MODES, ENV_TCMDB, HubError, SourceCard, TCMDataHub, catalog
from .relations import EVIDENCE, RELATION_KINDS

__all__ = ["ACCESS_MODES", "DATASETS", "DatasetSpec", "ENV_TCMDB", "EVIDENCE", "FileSpec",
           "HubError", "RELATION_KINDS", "SourceCard", "TCMDataHub", "catalog", "dataset"]
