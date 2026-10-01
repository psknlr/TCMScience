"""Live connectors added beyond the TCM architecture document, one module per domain.

Each module exposes ``SOURCES: tuple[PublicSource, ...]`` and may expose
``PENDING: tuple[PublicSource, ...]`` for connectors defined but not shipped (they failed
verification, or need a credential this harness does not hold). A module is loaded only
when it is named in ``MODULES``; every shipped operation must have a SUCCEEDED row in
``data/connector_live_verification.csv`` (``scripts/verify_connectors.py --only <key>``).
"""

from __future__ import annotations

from importlib import import_module

from ..public_apis import PublicSource

__all__ = ["MODULES", "SUPPLEMENT_SOURCES", "PENDING_SUPPLEMENT_SOURCES"]

MODULES: tuple[str, ...] = ()

SUPPLEMENT_SOURCES: tuple[PublicSource, ...] = ()
PENDING_SUPPLEMENT_SOURCES: tuple[PublicSource, ...] = ()
for _name in MODULES:
    _mod = import_module(f"{__name__}.{_name}")
    SUPPLEMENT_SOURCES += tuple(_mod.SOURCES)
    PENDING_SUPPLEMENT_SOURCES += tuple(getattr(_mod, "PENDING", ()))
