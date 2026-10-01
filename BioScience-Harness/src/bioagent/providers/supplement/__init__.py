"""Live connectors added beyond the TCM architecture document, one module per domain.

Each module exposes ``SOURCES: tuple[PublicSource, ...]`` and may expose
``PENDING: tuple[PublicSource, ...]`` for connectors defined but not shipped (they failed
verification, or need a credential this harness does not hold). A module is loaded only
when it is named in ``MODULES``; every shipped operation must have a SUCCEEDED row in
``data/connector_live_verification.csv`` (``scripts/verify_connectors.py --only <key>``).

The modules import ``providers.public_apis``, which registers them: ``public_apis`` calls
``load_sources()`` once its own classes exist. So that a domain module can also be the
FIRST import of a process (``import bioagent.providers.supplement.imaging``), this
package imports ``public_apis`` at the end of its own initialisation, after
``load_sources`` is defined. ``public_apis`` then loads every domain module completely
before the requested one is looked up; without this, ``load_sources()`` met the
requested module half-initialised and failed with "has no attribute SOURCES".
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

__all__ = ["MODULES", "load_sources", "load_pending"]

MODULES: tuple[str, ...] = ("tcm_np", "tcm_safety", "genetics", "safety", "spectra", "chem_onto", "rna_reg", "atlases", "drugs", "pgx_proteins", "mechanism", "cells_perturb")


def _modules() -> list[ModuleType]:
    return [import_module(f"{__name__}.{name}") for name in MODULES]


def load_sources() -> tuple[Any, ...]:
    """The shipped connectors of every domain module."""
    return tuple(s for m in _modules() for s in m.SOURCES)


def load_pending() -> tuple[Any, ...]:
    """Connectors defined but not shipped (verified only when named)."""
    return tuple(s for m in _modules() for s in getattr(m, "PENDING", ()))


# Last, so ``load_sources`` exists when ``public_apis`` (imported here if it is not yet)
# calls it. ``import_module`` returns the half-initialised module from ``sys.modules``
# when ``public_apis`` is the import that brought this package in.
import_module(f"{__name__.rsplit('.', 1)[0]}.public_apis")
