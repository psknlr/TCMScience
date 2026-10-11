"""Where a validation ran: interpreter, packages, hardware, disk, network route, CI run.

A result is only as reproducible as its environment is described. This extends
:func:`bioagent.environment.environment_record` (interpreter, platform, the dependency
closure's versions, and their digest) with what a validation also depends on: CPU and
memory, a GPU if one is visible, free space where data is written, whether outbound
traffic goes through a proxy (never the proxy's address, which can carry credentials),
and the CI run, so a report can be traced to the job that produced it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

__all__ = ["fingerprint", "disk_free", "ci_run"]


def _memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, ValueError, OSError):
        return None


def _gpus() -> list[str]:
    """GPU names ``nvidia-smi -L`` lists, if it is installed; nothing else is probed."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=10,
                             check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split(" (UUID", 1)[0].strip() for line in out.splitlines() if line.strip()]


def disk_free(path: str | Path) -> int | None:
    """Free bytes on the filesystem holding ``path`` (its nearest existing parent)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def ci_run() -> dict[str, Any] | None:
    """The GitHub Actions run this process is part of, if any."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return {"provider": "github-actions", "repository": repo, "run_id": run_id,
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "job": os.environ.get("GITHUB_JOB", ""),
            "event": os.environ.get("GITHUB_EVENT_NAME", ""),
            "ref": os.environ.get("GITHUB_REF", ""), "sha": os.environ.get("GITHUB_SHA", ""),
            "url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""}


def _git_commit() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        return sha
    exe = shutil.which("git")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, check=False, cwd=Path(__file__).resolve().parent)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def fingerprint(roots: Mapping[str, str | Path] | None = None) -> dict[str, Any]:
    """The environment record plus hardware, disk under ``roots``, network route and CI."""
    from ..environment import environment_record

    record = environment_record()
    proxy = any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                                            "http_proxy"))
    out: dict[str, Any] = {
        "python": record["python"], "platform": {**record["platform"],
                                                 "release": platform.release()},
        "packages": record["packages"], "environment_digest": record["digest"],
        "cpu_count": os.cpu_count(), "memory_bytes": _memory_bytes(), "gpus": _gpus(),
        "network": {"proxy_configured": proxy,
                    "note": "whether outbound HTTP goes through a proxy; whether a host is "
                            "reachable is established per request, never from this flag"},
        "commit": _git_commit()}
    if roots:
        out["disk"] = {name: {"path": str(path), "free_bytes": disk_free(path)}
                       for name, path in roots.items()}
    ci = ci_run()
    if ci:
        out["ci"] = ci
    return out
