"""Optional analysis packages, imported only when a backend that needs them is chosen.

The pipelines run on numpy and scipy alone. PyDESeq2, Scanpy, harmonypy, GSEApy and
scvi-tools are other implementations a caller may select. Selecting one that cannot be
imported is refused with the reason before any work is done. Falling back to the
built-in implementation instead would publish one method's numbers under another's
name, and the run record would say so wrongly. Whatever does run is recorded by
distribution name and version.
"""

from __future__ import annotations

import importlib
import warnings
from importlib import metadata
from types import ModuleType

from ..status import ExecutionStatus

__all__ = ["BackendUnavailable", "require", "version"]


class BackendUnavailable(RuntimeError):
    """A selected implementation cannot run here. ``status`` is UNAVAILABLE: nothing ran."""

    status = ExecutionStatus.UNAVAILABLE

    def __init__(self, backend: str, reason: str) -> None:
        super().__init__(f"{backend} is unavailable: {reason}")
        self.backend = backend
        self.reason = reason


def version(distribution: str) -> str:
    """The installed version of ``distribution``, as its metadata states it."""
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return "unknown"


def require(module: str, *, backend: str, distribution: str | None = None,
            extra: str = "analysis") -> ModuleType:
    """Import ``module`` for ``backend``, or refuse with the reason and how to install it.

    The import runs inside ``warnings.catch_warnings`` because importing some of these
    packages rewrites the process-wide warning filters (PyDESeq2 silences every
    FutureWarning), which would hide warnings from unrelated code afterwards. A shared
    library that fails to load is as unavailable as a missing package.
    """
    dist = distribution or module.split(".")[0]
    try:
        with warnings.catch_warnings():
            return importlib.import_module(module)
    except (ImportError, OSError) as exc:
        raise BackendUnavailable(
            backend, f"{dist} cannot be imported ({type(exc).__name__}: {exc}); install it "
                     f"with pip install 'bioagent[{extra}]'") from exc
