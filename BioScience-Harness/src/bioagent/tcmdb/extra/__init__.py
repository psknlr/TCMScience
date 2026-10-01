"""Datasets added beyond the 66 sources of the architecture document.

Each module here covers one domain and exposes:

* ``DATASETS``: its ``DatasetSpec``s (``tcmdb.spec``);
* ``EXTRACTORS``: dataset key -> a function reading that built store and yielding relation
  rows (``tcmdb.rowkit.rel``);
* ``KINDS`` (optional): relation kinds it adds, kind -> (subject type, object type);
* ``READERS`` (optional): file format name -> reader (``tcmdb.store.register_reader``),
  for formats the built-in loaders do not read (XML, OBO, SDF, MSP, ...).

A module is loaded only when it is named in ``MODULES``. Modules import ``tcmdb.spec`` and
``tcmdb.rowkit`` (and ``tcmdb.store`` for its readers) and never ``tcmdb.datasets`` or
``tcmdb.relations``, which import this package.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable, Iterable

from ..rowkit import register_kinds
from ..spec import DatasetSpec
from ..store import register_reader

__all__ = ["MODULES", "EXTRA_DATASETS", "EXTRA_EXTRACTORS"]

#: Domain modules, in the order their datasets are listed.
MODULES: tuple[str, ...] = ("tcm_np", "tcm_safety", "genetics", "safety", "spectra")

EXTRA_DATASETS: tuple[DatasetSpec, ...] = ()
EXTRA_EXTRACTORS: dict[str, Callable[[Any], Iterable[Any]]] = {}

for _name in MODULES:
    _mod = import_module(f"{__name__}.{_name}")
    register_kinds(getattr(_mod, "KINDS", {}))
    for _fmt, _reader in getattr(_mod, "READERS", {}).items():
        register_reader(_fmt, _reader)
    EXTRA_DATASETS += tuple(_mod.DATASETS)
    for _key, _fn in _mod.EXTRACTORS.items():
        if _key in EXTRA_EXTRACTORS:                    # pragma: no cover - programming
            raise RuntimeError(f"two extractors for dataset {_key!r}")
        EXTRA_EXTRACTORS[_key] = _fn
