"""TCMScience Studio: one tool surface for two runtimes.

``tcmstudio.catalog`` builds the tool catalog from the code itself (type hints, skill
manifests, connector templates); ``tcmstudio.dispatch`` executes a call and returns one
envelope shape. The local runner (``tcmstudio serve``) and the Pyodide worker in the
browser import these same modules, so a tool behaves the same in both, and so do its
governance fields.

Importing the package does nothing else: no thread, process or socket is started, and
the heavier modules load when first used.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

__all__ = ["__version__", "build_catalog", "call", "catalog_search"]


def __getattr__(name: str) -> Any:
    # Lazy, so ``import tcmstudio`` stays cheap in the browser worker.
    if name == "call":
        from .dispatch import call
        return call
    if name in ("build_catalog", "catalog_search"):
        from . import catalog
        return getattr(catalog, name)
    raise AttributeError(f"module 'tcmstudio' has no attribute {name!r}")
