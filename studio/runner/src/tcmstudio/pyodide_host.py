"""Self-hosted Pyodide (docs/V2.md §15): the pinned files under ``pyodide/<version>/`` of the site.

science.impf.ai serves Pyodide itself instead of sending browsers to jsDelivr: jsDelivr lost its China ICP licence
in 2021, and same-origin files satisfy COEP without a CORP header. ``self_host(staging, catalog=…, log=…)`` (the
webbuild step behind ``--self-host-pyodide``) puts into ``staging/pyodide/314.0.7/``:

* the core files Pyodide loads at boot (``pyodide.mjs``, ``pyodide.asm.mjs``, ``pyodide.asm.wasm``,
  ``python_stdlib.zip``, ``pyodide-lock.json``), each pinned below by SHA-256;
* the wheels of every package a browser page may load, closed over the lockfile's ``depends``: boot.json's packages
  plus every catalog entry's ``pyodide_packages``. Each wheel is checked against the SHA-256 the pinned lockfile
  records for it, which is also what Pyodide checks when it loads one (``fetch`` with ``integrity``).

Files are downloaded once from jsDelivr into a cache (``$TCMSTUDIO_CACHE/pyodide/<version>/``, default
``~/.cache/tcmstudio/pyodide/<version>/``) and verified on every use; nothing unverified is ever published. A file
over 25 MiB is refused: Workers Static Assets serves nothing larger. The lockfile is published as pinned; the
packages it lists that are not hosted are absent, and a page that asks for one gets an "unavailable" result.

boot.json then names the index relative to the site (``pyodide/314.0.7/``); the worker resolves it against the
page, and Pyodide loads the wheels from the directory its lockfile came from.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .webbuild import PYODIDE_PACKAGES, PYODIDE_VERSION, BuildError

__all__ = ["CDN_URL", "CORE_FILES", "INDEX_URL", "MAX_FILE_BYTES", "PyodideHostError", "cache_dir",
           "closure", "self_host", "wanted_packages"]

CDN_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
#: Where the files go in the site, and what boot.json's ``pyodide.index_url`` becomes (relative to the site).
INDEX_URL = f"pyodide/{PYODIDE_VERSION}/"
#: The core files of Pyodide 314.0.7 (jsDelivr ``/pyodide/v314.0.7/full/``), by SHA-256.
CORE_FILES: dict[str, str] = {
    "pyodide.mjs": "6f1d60f7bf529beb300f0f47983c921d3982363640ba20af0e38efdddbc66109",
    "pyodide.asm.mjs": "f7cdc8ece80678ceb712f8e65ebe6d3a83203a180c399865f49612a051693635",
    "pyodide.asm.wasm": "cc36e3cab04fdfc9a63ff13eb52eae2b911bf46c025cc7b281f394bd3de1d5e6",
    "python_stdlib.zip": "fa1957e5777068fc4f7437f96d860ae2fbe9c19732ba06c84e004ec16dd7dd7a",
    "pyodide-lock.json": "5dc2fc119108bc148c7457dc86e7675b5c87e1cafd420b9c34c1eaef7b36c010",
}
LOCKFILE = "pyodide-lock.json"
#: Workers Static Assets refuses files over 25 MiB.
MAX_FILE_BYTES = 25 * 1024 * 1024
_TIMEOUT_S = 120
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

Log = Callable[[str], None]
#: download(url, max_bytes) -> bytes; raises on an HTTP error or a body over max_bytes.
Downloader = Callable[[str, int], bytes]


class PyodideHostError(BuildError):
    """Pyodide cannot be self-hosted as pinned; the message says which file and why."""


def cache_dir() -> Path:
    """``$TCMSTUDIO_CACHE/pyodide/<version>`` (default ``~/.cache/tcmstudio/pyodide/<version>``)."""
    base = os.environ.get("TCMSTUDIO_CACHE")
    root = Path(base).expanduser() if base else Path("~/.cache/tcmstudio").expanduser()
    return root / "pyodide" / PYODIDE_VERSION


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def wanted_packages(catalog: Mapping[str, Any] | None) -> list[str]:
    """boot.json's packages and every catalog entry's ``pyodide_packages``, sorted."""
    names = set(PYODIDE_PACKAGES)
    for entry in (catalog or {}).get("entries") or ():
        for name in (entry or {}).get("pyodide_packages") or ():
            names.add(str(name))
    return sorted(names)


def closure(lock: Mapping[str, Any], names: Iterable[str]) -> tuple[list[str], list[str]]:
    """The lockfile packages ``names`` need, dependencies included (sorted), and the names the lockfile does not
    list (``sqlite3``, for one, is part of the standard library from Pyodide 314 on)."""
    packages = lock.get("packages") or {}
    index = {_canonical(k): k for k in packages}
    found: set[str] = set()
    missing: list[str] = []
    todo = list(names)
    while todo:
        name = todo.pop()
        key = index.get(_canonical(name))
        if key is None:
            if name not in missing:
                missing.append(name)
            continue
        if key in found:
            continue
        found.add(key)
        todo.extend(packages[key].get("depends") or ())
    return sorted(found), sorted(missing)


def _http_download(url: str, max_bytes: int) -> bytes:
    """GET ``url`` (the environment's proxy and CA settings apply), refusing a body over ``max_bytes``."""
    req = urllib.request.Request(url, headers={"User-Agent": "tcmstudio-webbuild"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as res:          # noqa: S310 (a pinned https URL)
        length = res.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise PyodideHostError(f"{url} is {int(length):,} bytes, over the {max_bytes:,}-byte limit")
        buf = bytearray()
        while True:
            chunk = res.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            if len(buf) > max_bytes:
                raise PyodideHostError(f"{url} is over the {max_bytes:,}-byte limit")
    return bytes(buf)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(name: str, sha256: str, *, cache: Path, download: Downloader, base_url: str,
           max_bytes: int, say: Log) -> tuple[Path, bool]:
    """The verified file in the cache: the cached copy when it matches, else a fresh download (checked before it is
    kept). Returns (path, downloaded)."""
    if not _NAME_RE.match(name) or "/" in name:
        raise PyodideHostError(f"refusing the file name {name!r} from the lockfile")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise PyodideHostError(f"{name}: no SHA-256 to check it against")
    path = cache / name
    if path.is_file():
        if path.stat().st_size <= max_bytes and _sha256_file(path) == sha256:
            return path, False
        say(f"  {name}: the cached copy does not match its SHA-256; downloading it again")
        path.unlink()
    url = base_url + name
    try:
        data = download(url, max_bytes)
    except PyodideHostError:
        raise
    except Exception as exc:                                        # noqa: BLE001 (any transport error)
        raise PyodideHostError(f"could not download {url}: {type(exc).__name__}: {exc}") from None
    if len(data) > max_bytes:
        raise PyodideHostError(f"{name} is {len(data):,} bytes, over the {max_bytes:,}-byte limit")
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise PyodideHostError(f"{url}: SHA-256 {digest} does not match the pinned {sha256}; not used")
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f".{name}.{secrets.token_hex(4)}.part"
    tmp.write_bytes(data)
    tmp.replace(path)
    return path, True


def self_host(staging: str | os.PathLike[str], *, catalog: Mapping[str, Any] | None = None,
              log: Log | None = None, download: Downloader | None = None,
              cache: str | os.PathLike[str] | None = None, pins: Mapping[str, str] | None = None,
              base_url: str = CDN_URL, max_bytes: int = MAX_FILE_BYTES) -> dict[str, Any]:
    """Copy the pinned Pyodide core and the wheels the site needs into ``staging/pyodide/<version>/``.

    Returns ``{"index_url": "pyodide/<version>/", "version", "files", "bytes", "packages", "missing", "listing",
    "downloaded", "cache"}``: ``files`` and ``bytes`` count what was published, ``listing`` names each file with its
    SHA-256 and size. Raises ``PyodideHostError`` (a ``BuildError``) when a file cannot be had as pinned."""
    say = log or (lambda _msg: None)
    fetch = download or _http_download
    store = Path(cache).expanduser() if cache else cache_dir()
    core = dict(pins or CORE_FILES)
    if catalog is None:
        from .catalog import build_catalog
        catalog = build_catalog("browser", probe=False)
    say(f"self-hosting Pyodide {PYODIDE_VERSION} (pinned files from {base_url}, cache {store})")

    got: dict[str, tuple[Path, str]] = {}
    downloaded = 0
    for name, sha in core.items():
        path, fresh = _fetch(name, sha, cache=store, download=fetch, base_url=base_url, max_bytes=max_bytes, say=say)
        got[name] = (path, sha)
        downloaded += fresh
    try:
        lock = json.loads(got[LOCKFILE][0].read_text(encoding="utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise PyodideHostError(f"{LOCKFILE} cannot be read: {exc}") from None

    wanted = wanted_packages(catalog)
    packages, missing = closure(lock, wanted)
    if missing:
        say(f"  not in Pyodide {PYODIDE_VERSION}'s lockfile (not hosted): {', '.join(missing)}")
    for key in packages:
        meta = lock["packages"][key]
        name = str(meta.get("file_name") or "")
        if "/" in name or "://" in name:
            raise PyodideHostError(f"{key}: the lockfile names {name!r}, not a file beside it")
        path, fresh = _fetch(name, str(meta.get("sha256") or ""), cache=store, download=fetch,
                             base_url=base_url, max_bytes=max_bytes, say=say)
        got[name] = (path, str(meta["sha256"]))
        downloaded += fresh

    dest = Path(staging) / INDEX_URL
    dest.mkdir(parents=True, exist_ok=True)
    listing = []
    for name in sorted(got):
        path, sha = got[name]
        shutil.copyfile(path, dest / name)
        listing.append({"path": f"{INDEX_URL}{name}", "sha256": sha, "bytes": path.stat().st_size})
    total = sum(f["bytes"] for f in listing)
    say(f"  {INDEX_URL}: {len(listing)} files, {total:,} bytes ({len(packages)} packages: "
        f"{', '.join(packages)}; {downloaded} downloaded, {len(listing) - downloaded} from the cache)")
    return {"index_url": INDEX_URL, "version": PYODIDE_VERSION, "files": len(listing), "bytes": total,
            "packages": packages, "missing": missing, "listing": listing, "downloaded": downloaded,
            "cache": str(store)}
