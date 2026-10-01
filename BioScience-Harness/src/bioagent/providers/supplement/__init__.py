"""Live connectors added beyond the TCM architecture document, one module per domain.

Each module exposes ``SOURCES: tuple[PublicSource, ...]`` and may expose
``PENDING: tuple[PublicSource, ...]`` for connectors defined but not shipped (they failed
verification, or need a credential this harness does not hold). A module is loaded only
when it is named in ``MODULES``; every shipped operation must have a SUCCEEDED row in
``data/connector_live_verification.csv`` (``scripts/verify_connectors.py --only <key>``).

The modules import ``providers.public_apis``, which registers them, so this package
imports nothing at load time: ``public_apis`` calls ``load_sources()`` once its own
classes exist.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import Any

__all__ = ["MODULES", "load_sources", "load_pending"]

MODULES: tuple[str, ...] = ("drugs",)


def _modules() -> list[ModuleType]:
    return [import_module(f"{__name__}.{name}") for name in MODULES]


def load_sources() -> tuple[Any, ...]:
    """The shipped connectors of every domain module."""
    return tuple(s for m in _modules() for s in m.SOURCES)


def load_pending() -> tuple[Any, ...]:
    """Connectors defined but not shipped (verified only when named)."""
    return tuple(s for m in _modules() for s in getattr(m, "PENDING", ()))
