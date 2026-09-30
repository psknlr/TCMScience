"""The environment a run executed in, recorded and digested.

The skill pin (``skills.loader.skill_content_hash``) covers the skill's manifest and its
local import closure. It does not cover the third-party packages that code imports, or
the interpreter: the same source can behave differently under another pandas or another
Python. The audit (F08) asked for the two to be kept apart, a *skill* digest and an
*environment* digest, and for the second to exist at all.

:func:`environment_record` returns what can be established from inside the process:

* the interpreter (implementation, version) and platform;
* ``bioagent`` and ``psh`` and their versions;
* every distribution ``bioagent`` declares as a runtime dependency, transitively, with the
  version installed — and ``missing`` for one that is declared but absent;

and a SHA-256 over it. Governed skill runs and research runs write the record into the
artifact's provenance and the audit chain, so two results can be compared on the
environment as well as on the code. It records; it does not enforce. Pinning an
environment is a deployment decision (a lockfile, a container digest), and a runtime
refusing to start because a patch release differs would be the wrong default.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
from functools import lru_cache
from importlib import metadata
from typing import Any

__all__ = ["environment_record", "environment_digest"]

_ROOTS = ("bioagent", "psh")
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _requirements(dist: str) -> list[str]:
    """Names of the runtime (non-extra) requirements a distribution declares."""
    try:
        reqs = metadata.requires(dist) or []
    except metadata.PackageNotFoundError:
        return []
    out = []
    for req in reqs:
        marker = req.split(";", 1)[1] if ";" in req else ""
        if "extra" in marker:                      # optional extras are not the runtime
            continue
        if marker and not _marker_applies(marker):  # e.g. tzdata only on some platforms
            continue
        m = _NAME.match(req)
        if m:
            out.append(m.group(1))
    return out


def _marker_applies(marker: str) -> bool:
    try:
        from packaging.markers import Marker
    except ImportError:                            # pragma: no cover - keep it, say so
        return True
    try:
        return Marker(marker).evaluate({"extra": ""})
    except Exception:                              # noqa: BLE001 - unparseable: keep it
        return True


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


@lru_cache(maxsize=1)
def environment_record() -> dict[str, Any]:
    """Interpreter, platform and the installed versions of the dependency closure."""
    versions: dict[str, str] = {}
    pending = list(_ROOTS)
    while pending:
        name = _canonical(pending.pop())
        if name in versions:
            continue
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "missing"
            continue
        pending.extend(_requirements(name))
    record = {
        "python": {"implementation": platform.python_implementation(),
                   "version": platform.python_version()},
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "packages": dict(sorted(versions.items())),
        "note": ("installed versions of bioagent, psh and bioagent's declared runtime "
                 "dependencies, transitively; 'missing' means declared but not installed"),
    }
    record["digest"] = environment_digest(record)
    return record


def environment_digest(record: dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k not in ("digest", "note")}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


if __name__ == "__main__":                                     # pragma: no cover
    json.dump(environment_record(), sys.stdout, indent=2)
    print()
