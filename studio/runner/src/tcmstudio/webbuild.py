"""``tcmstudio webbuild``: the static site served at science.impf.ai (CONTRACTS §5, §10).

    tcmstudio webbuild --out DIR [--web DIR] [--dev] [--pyodide-index-url URL]
    python3 studio/scripts/build_web.py --out studio/_site

The site is the web app (``studio/web``) plus three generated files under ``runtime/``:

* ``catalog.json``: the tool catalog for the browser (§2), so the page can offer tools
  before Python has loaded;
* ``tcms-py.<sha12>.tar.gz``: psh, bioagent (with the skills, registry and lockfiles a
  governed run reads) and tcmstudio, laid out as installed packages with minimal
  ``*.dist-info`` metadata, so ``importlib.metadata`` and the environment record report
  the real versions. It is built reproducibly: sorted entries, fixed times and owners,
  gzip mtime 0, so the same sources give the same bytes and the same name;
* ``boot.json``: what the Pyodide worker loads, and the bundle's SHA-256, which the
  worker checks before unpacking.

The local runner serves the same three files from memory (``runtime_file``) when it has
no built site, so ``http://127.0.0.1:8765/`` gets a browser runtime too.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

__all__ = ["BOOT_SCHEMA", "BuildError", "PYODIDE_INDEX_URL", "PYODIDE_VERSION", "Bundle",
           "add_arguments", "build", "build_bundle", "boot_document", "catalog_bytes",
           "clear_cache", "default_web_dir", "main", "run", "runtime_file", "runtime_files"]

PYODIDE_VERSION = "314.0.7"
PYODIDE_INDEX_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
#: Loaded at boot. ``sqlite3`` (PSH's audit chain) is part of the standard library from
#: Pyodide 314 on; the worker loads only the names its Pyodide lockfile lists and then
#: imports sqlite3, so an older or newer Pyodide gets the right one either way.
PYODIDE_PACKAGES = ("pyyaml", "packaging", "sqlite3")
#: Where the worker unpacks the bundle; it is the one entry added to ``sys.path``.
EXTRACT_DIR = "/opt/tcms/site"
#: Where governed runs keep each project's PSH state in the browser (IDBFS when it works).
STATE_ROOT = "/persist"
BOOT_SCHEMA = "tcmstudio.boot/1"
ENV_INDEX_URL = "TCMSTUDIO_PYODIDE_INDEX_URL"

#: 2020-01-01T00:00:00Z: every bundle entry gets this time, so a rebuild is byte-identical.
_FIXED_MTIME = 1_577_836_800
_SKIP_NAMES = {".DS_Store", "Thumbs.db"}
_SKIP_SUFFIXES = (".pyc", ".pyo")
#: Generated or local-only directories of the web app that are never published as-is.
_WEB_SKIP_TOP = {"runtime", "node_modules"}
_WEB_DEV_TOP = {"test", "dev"}


class BuildError(RuntimeError):
    """The site cannot be built as asked; the message says why and what to do."""


# ================================================================== the Python bundle

@dataclass(frozen=True)
class _Source:
    """One distribution as it goes into the bundle: its package directory and, for a
    source checkout of bioagent, the directories ``setup.py`` would bundle beside it."""

    dist: str
    package: Path
    extras: tuple[tuple[Path, str], ...] = ()
    skip: tuple[str, ...] = ()            # paths (relative to the package) never shipped


@dataclass
class Bundle:
    name: str                              # tcms-py.<sha12>.tar.gz
    data: bytes
    sha256: str
    files: int
    raw_bytes: int
    versions: dict[str, str]
    sources: dict[str, str] = field(default_factory=dict)


def _package_dir(module: str) -> Path:
    """The directory a package is imported from, without importing it."""
    spec = importlib.util.find_spec(module)
    locations = list(getattr(spec, "submodule_search_locations", None) or ()) if spec else []
    if not locations:
        raise BuildError(f"{module} is not installed in this Python ({sys.executable}); "
                         f"install it (pip install -e …) before building the site.")
    return Path(locations[0]).resolve()


def _sources() -> list[_Source]:
    bioagent = _package_dir("bioagent")
    root = bioagent.parent.parent
    if bioagent.parent.name == "src" and (root / "pyproject.toml").is_file():
        # A checkout: ship skills/ and registry/ as bioagent/_bundled/, as setup.py does for
        # a wheel. bioagent.config then reads them there, and the skill hashes are the same.
        extras = []
        for name in ("skills", "registry"):
            if not (root / name).is_dir():
                raise BuildError(f"{root / name} is missing; without it the browser could not "
                                 "run a governed skill.")
            extras.append((root / name, f"bioagent/_bundled/{name}"))
        bio = _Source("bioagent", bioagent, tuple(extras), ("_bundled",))
    elif (bioagent / "_bundled" / "skills").is_dir():
        bio = _Source("bioagent", bioagent)
    else:
        raise BuildError(f"bioagent at {bioagent} carries no skills or registry (neither a "
                         "source checkout nor a wheel with bioagent/_bundled); governed skills "
                         "could not run in the browser.")
    # Keep the complete provider catalogue available to browser capability discovery.
    return [bio,
            _Source("psh", _package_dir("psh")),
            # A wheel of tcmstudio may carry the web app as package data; it is not Python.
            _Source("tcmstudio", _package_dir("tcmstudio"), (), ("web",))]


def _skipped(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if any(p == "__pycache__" or p.endswith(".egg-info") or p.startswith(".") for p in parts):
        return True
    name = parts[-1]
    return name in _SKIP_NAMES or name.startswith("._") or name.endswith(_SKIP_SUFFIXES)


def _git_files(directory: Path) -> list[PurePosixPath] | None:
    """The files git would commit under ``directory``: tracked ones, plus untracked ones it
    does not ignore (a working copy builds what it would commit; a clean checkout, such as
    CI's, builds exactly the tracked tree). None when git cannot say."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        res = subprocess.run([git, "-C", str(directory), "ls-files", "-z", "--cached",
                              "--others", "--exclude-standard", "--", "."],
                             capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    names = res.stdout.decode("utf-8", "surrogateescape").split("\0")
    # A tracked file deleted from the working tree is listed but has nothing to ship.
    found = [PurePosixPath(n) for n in names if n and (directory / n).is_file()]
    return found or None


def _walk_files(directory: Path) -> list[PurePosixPath]:
    out = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_file():
                out.append(PurePosixPath(full.relative_to(directory).as_posix()))
    return out


def _files_of(directory: Path, skip: Iterable[str] = ()) -> list[PurePosixPath]:
    skip = tuple(PurePosixPath(s) for s in skip)
    listed = _git_files(directory)
    if listed is None:
        listed = _walk_files(directory)
    keep = []
    for rel in listed:
        if _skipped(rel) or any(rel == s or s in rel.parents for s in skip):
            continue
        keep.append(rel)
    return sorted(set(keep))


def _dist_info(dist: str, version: str, top_level: str, summary: str) -> dict[str, bytes]:
    base = f"{dist.replace('-', '_')}-{version}.dist-info"
    meta = ["Metadata-Version: 2.1", f"Name: {dist}", f"Version: {version}"]
    if summary:
        meta.append(f"Summary: {' '.join(summary.split())}")
    return {f"{base}/METADATA": ("\n".join(meta) + "\n").encode("utf-8"),
            f"{base}/INSTALLER": b"tcmstudio-webbuild\n",
            f"{base}/top_level.txt": f"{top_level}\n".encode("utf-8")}


def _summary(dist: str) -> str:
    try:
        from importlib.metadata import metadata
        return str(metadata(dist).get("Summary") or "")
    except Exception:                                           # noqa: BLE001
        return ""


def _versions() -> dict[str, str]:
    from .envelope import package_version
    return {d: package_version(d) for d in ("tcmstudio", "bioagent", "psh")}


def _tar_gz(entries: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name in sorted(entries):
            data = entries[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = _FIXED_MTIME
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    out = io.BytesIO()
    # No file name and mtime 0 in the gzip header: only the content decides the bytes.
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=9) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def build_bundle() -> Bundle:
    """The Python bundle the browser worker unpacks into ``EXTRACT_DIR``."""
    versions = _versions()
    entries: dict[str, bytes] = {}
    sources: dict[str, str] = {}

    def add(name: str, data: bytes, origin: Path | str) -> None:
        if name in entries:
            raise BuildError(f"two files would be bundled as {name} (second: {origin})")
        entries[name] = data

    for src in _sources():
        top = src.package.name
        sources[src.dist] = str(src.package)
        files = _files_of(src.package, src.skip)
        if not any(f.name == "__init__.py" and len(f.parts) == 1 for f in files):
            raise BuildError(f"{src.package} has no __init__.py among the files to ship; is it "
                             "the package directory?")
        for rel in files:
            add(f"{top}/{rel.as_posix()}", (src.package / rel).read_bytes(), src.package / rel)
        for directory, prefix in src.extras:
            for rel in _files_of(directory):
                add(f"{prefix}/{rel.as_posix()}", (directory / rel).read_bytes(), directory / rel)
        version = versions.get(src.dist) or "0"
        if version == "unknown":
            raise BuildError(f"the version of {src.dist} cannot be determined; install it so "
                             "its metadata exists.")
        for name, data in _dist_info(src.dist, version, top, _summary(src.dist)).items():
            add(name, data, "dist-info")
    data = _tar_gz(entries)
    sha = hashlib.sha256(data).hexdigest()
    return Bundle(name=f"tcms-py.{sha[:12]}.tar.gz", data=data, sha256=sha, files=len(entries),
                  raw_bytes=sum(len(v) for v in entries.values()), versions=versions,
                  sources=sources)


# =========================================================== catalog and boot manifest

def catalog_bytes(*, strict: bool = True) -> tuple[bytes, dict[str, Any]]:
    """The browser catalog (§2) as published: compact UTF-8 JSON. ``strict`` refuses a
    catalog that reports a section it could not build."""
    from .catalog import build_catalog

    doc = build_catalog("browser", probe=False)
    if strict and doc.get("problems"):
        raise BuildError("the catalog could not be built completely: " + "; ".join(
            f"{p['section']}: {p['error']}" for p in doc["problems"]))
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    return text.encode("utf-8"), doc


def _index_url(explicit: str | None) -> str:
    url = (explicit or os.environ.get(ENV_INDEX_URL) or PYODIDE_INDEX_URL).strip()
    return url if url.endswith("/") else url + "/"


def boot_document(bundle: Bundle, catalog: bytes, *,
                  index_url: str | None = None) -> dict[str, Any]:
    """``runtime/boot.json`` (§5). Paths are relative to the site root."""
    return {
        "schema": BOOT_SCHEMA,
        "pyodide": {"version": PYODIDE_VERSION, "index_url": _index_url(index_url),
                    "packages": list(PYODIDE_PACKAGES)},
        "bundle": {"path": f"runtime/{bundle.name}", "sha256": bundle.sha256,
                   "bytes": len(bundle.data), "format": "gztar", "files": bundle.files,
                   "extract_dir": EXTRACT_DIR, "python_path": [EXTRACT_DIR]},
        "catalog": "runtime/catalog.json",
        "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
        "state_root": STATE_ROOT,
        "versions": dict(bundle.versions),
        "source_gateway": {"path": "runtime/source-gateway.json", "request_path": "/api/sources/request"},
        "data_assets": data_assets()[1],
    }


def _json_bytes(doc: Any) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def source_gateway_document() -> dict[str, Any]:
    """Generate the gateway's fixed request templates from the same connector registry."""
    from bioagent.backends.http import _default_rates
    from bioagent.providers.public_apis import SOURCES
    rates = _default_rates()
    sources = []
    for source in SOURCES:
        if not source.base_url.startswith(("https://", "http://")):
            continue
        operations = []
        for op in source.operations:
            item = {"name": op.name, "path": op.path, "method": op.method,
                    "params": dict(op.params), "accept": op.accept}
            for name in ("graphql", "variables", "json_body", "form"):
                value = getattr(op, name)
                if value is not None and value != {}:
                    item[name] = value
            operations.append(item)
        sources.append({"key": source.key, "name": source.name,
                        "base_url": source.base_url, "host": source.host,
                        "transport": "https" if source.base_url.startswith("https://") else "http",
                        "rate_rps": rates.get(source.host, 2.0),
                        "rate_paths": {key[len(source.host):]: value for key, value in rates.items()
                                       if key.startswith(source.host + "/")},
                        "operations": operations})
    return {"version": 1, "sources": sources}


_DATA_LOCK = threading.Lock()
_DATA_CACHE: tuple[str, dict[str, tuple[bytes, str]], list[dict[str, Any]]] | None = None


def data_assets() -> tuple[dict[str, tuple[bytes, str]], list[dict[str, Any]]]:
    """An indexed complete workbook, loaded on demand; every deployable file is <25 MiB."""
    from .repository_data import FORMULA_MOUNT, build_formula_store, workbook_path
    global _DATA_CACHE
    workbook = workbook_path()
    if workbook is None:
        return {}, []
    key = f"{workbook}:{workbook.stat().st_mtime_ns}:{workbook.stat().st_size}"
    with _DATA_LOCK:
        if _DATA_CACHE is not None and _DATA_CACHE[0] == key:
            return _DATA_CACHE[1], _DATA_CACHE[2]
        with tempfile.TemporaryDirectory(prefix="tcmstudio-web-data-") as folder:
            database = Path(folder) / "repository-formulas.sqlite"
            metadata = build_formula_store(workbook, database)
            packed = _tar_gz({database.name: database.read_bytes()})
        digest = hashlib.sha256(packed).hexdigest()
        source = workbook.read_bytes()
        source_digest = hashlib.sha256(source).hexdigest()
        name = f"formulas.{digest[:12]}.tar.gz"
        original = f"formulas-source.{source_digest[:12]}.xlsx"
        for label, content in ((name, packed), (original, source)):
            if len(content) > 25 * 1024 * 1024:
                raise BuildError(f"{label} exceeds the Cloudflare Pages 25 MiB asset limit")
        files = {name: (packed, "application/gzip"), original: (source,
                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        manifest = [{"key": "repository_formulas", "path": f"runtime/{name}",
                     "sha256": digest, "bytes": len(packed), "format": "gztar",
                     "extract_dir": str(Path(FORMULA_MOUNT).parent),
                     "mount_path": FORMULA_MOUNT, **metadata,
                     "source_path": f"runtime/{original}", "source_bytes": len(source),
                     "lazy": True, "clinical_validation": "not assessed"}]
        _DATA_CACHE = key, files, manifest
        return files, manifest


# =================================================================== runtime on demand

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[bytes, str]] | None = None
_CACHE_KEY: str | None = None


def runtime_files(*, index_url: str | None = None) -> dict[str, tuple[bytes, str]]:
    """``{name: (bytes, media type)}`` for the files under ``runtime/``: ``boot.json``,
    ``catalog.json`` and ``tcms-py.<sha12>.tar.gz``. Built on first use (about two seconds)
    and kept for the life of the process. The bundle's name changes with its content, so it
    may be cached as immutable; the other two must be revalidated."""
    global _CACHE, _CACHE_KEY
    key = _index_url(index_url)
    with _LOCK:
        if _CACHE is None or key != _CACHE_KEY:
            bundle = build_bundle()
            catalog, _ = catalog_bytes(strict=False)
            boot = boot_document(bundle, catalog, index_url=key)
            _CACHE = {"boot.json": (_json_bytes(boot), "application/json; charset=utf-8"),
                      "catalog.json": (catalog, "application/json; charset=utf-8"),
                      bundle.name: (bundle.data, "application/gzip")}
            _CACHE["source-gateway.json"] = (_json_bytes(source_gateway_document()),
                                              "application/json; charset=utf-8")
            _CACHE.update(data_assets()[0])
            _CACHE_KEY = key
        return dict(_CACHE)


def runtime_file(name: str, *, index_url: str | None = None) -> tuple[bytes, str] | None:
    """One runtime file by its name (``boot.json``, ``runtime/boot.json``, …): its bytes and
    media type, or None when there is no such file (a stale bundle name included)."""
    clean = str(name or "").strip().lstrip("/")
    if clean.startswith("runtime/"):
        clean = clean[len("runtime/"):]
    if not clean or "/" in clean:
        return None
    return runtime_files(index_url=index_url).get(clean)


def clear_cache() -> None:
    """Forget the runtime files built for this process (the next request rebuilds them)."""
    global _CACHE, _CACHE_KEY
    with _LOCK:
        _CACHE = None
        _CACHE_KEY = None


# ====================================================================== the full site

def default_web_dir() -> Path | None:
    """The web app beside this code: a copy inside an installed package, else ``studio/web``
    of the source tree."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "web", here.parents[2] / "web"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _copy_web(web: Path, dest: Path, *, dev: bool) -> int:
    count = 0
    for dirpath, dirnames, filenames in os.walk(web):
        rel_dir = Path(dirpath).relative_to(web)
        if rel_dir == Path("."):
            skip_top = _WEB_SKIP_TOP | (set() if dev else _WEB_DEV_TOP)
            dirnames[:] = [d for d in dirnames if d not in skip_top]
        dirnames[:] = sorted(d for d in dirnames
                             if d not in ("__pycache__", "node_modules") and not d.startswith("."))
        for name in sorted(filenames):
            if name in _SKIP_NAMES or name.startswith(".") or name.endswith(_SKIP_SUFFIXES):
                continue
            target = dest / rel_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(dirpath) / name, target)
            count += 1
    return count


_SW_MARKER = "const PRECACHE = null; // filled by the build: {version, index_url, files: [{path, sha256}]}"
# what the service worker does not keep in the shell: content-addressed data the runtime Worker keeps itself,
# the test and design pages of a --dev build, and the worker script and edge headers themselves
_SW_SKIP_PREFIXES = ("test/", "dev/")
_SW_SKIP_SUFFIXES = (".tar.gz", ".xlsx")
_SW_SKIP_NAMES = {"sw.js", "_headers"}


def service_worker_manifest(site: Path, index_url: str) -> dict[str, Any]:
    """The shell files of a built site with their SHA-256, and a version that changes when
    any of them does (``sw.js`` caches exactly these, checked, one version at a time)."""
    files = []
    for path in sorted(p for p in site.rglob("*") if p.is_file()):
        rel = path.relative_to(site).as_posix()
        if rel in _SW_SKIP_NAMES or rel.startswith(_SW_SKIP_PREFIXES) or rel.endswith(_SW_SKIP_SUFFIXES):
            continue
        if any(part.startswith(".") for part in path.relative_to(site).parts):
            continue
        files.append({"path": "/" if rel == "index.html" else f"/{rel}",
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    version = hashlib.sha256(json.dumps([files, index_url], sort_keys=True).encode()).hexdigest()[:16]
    return {"version": version, "index_url": index_url, "files": files}


def _fill_service_worker(site: Path, index_url: str) -> dict[str, Any] | None:
    sw = site / "sw.js"
    if not sw.is_file():
        return None
    text = sw.read_text(encoding="utf-8")
    if _SW_MARKER not in text:
        raise BuildError("sw.js has no PRECACHE line for the build to fill")
    manifest = service_worker_manifest(site, index_url)
    sw.write_text(text.replace(_SW_MARKER, "const PRECACHE = "
                               + json.dumps(manifest, separators=(",", ":"), ensure_ascii=False) + ";"),
                  encoding="utf-8")
    return manifest


def _looks_built(out: Path) -> bool:
    return (out / "runtime" / "boot.json").is_file() and (out / "index.html").is_file()


def _install(staging: Path, out: Path) -> None:
    """Put the staged site at ``out``. A directory that is neither empty nor an earlier
    build is never replaced: ``--out`` pointed at the wrong place must not delete it."""
    if out.exists() or out.is_symlink():
        if not out.is_dir():
            raise BuildError(f"{out} exists and is not a directory")
        if any(out.iterdir()) and not _looks_built(out):
            raise BuildError(f"{out} is not empty and does not look like an earlier build "
                             "(no runtime/boot.json); choose another --out or empty it.")
        old = out.with_name(f".{out.name}.old-{secrets.token_hex(4)}")
        try:
            out.rename(old)
        except OSError:
            # A mount point cannot be renamed: empty it and move the new files in.
            for child in list(out.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            for child in list(staging.iterdir()):
                shutil.move(str(child), str(out / child.name))
            staging.rmdir()
            return
        try:
            staging.rename(out)
        except OSError:
            old.rename(out)              # put the previous build back
            raise
        shutil.rmtree(old, ignore_errors=True)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(out)


def build(out_dir: str | os.PathLike[str], web_dir: str | os.PathLike[str] | None = None, *,
          dev: bool = False, index_url: str | None = None,
          log: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Build the site into ``out_dir`` and return a summary of what was written.

    The site is assembled in a sibling directory and moved into place at the end, so a
    failed build leaves the previous one as it was."""
    say = log or (lambda _msg: None)
    out = Path(out_dir).expanduser().resolve()
    web = Path(web_dir).expanduser().resolve() if web_dir else default_web_dir()
    if web is None or not (web / "index.html").is_file():
        raise BuildError(f"no web app at {web or '(not found)'}; pass --web DIR (the directory "
                         "holding index.html).")
    if out == web or web in out.parents:
        raise BuildError(f"--out {out} is inside the web app {web}; build somewhere else.")
    for needed in ("js/runtime/browser.js", "js/runtime/pyodide.worker.js"):
        if not (web / needed).is_file():
            say(f"warning: {web / needed} is missing; the browser runtime will not start")

    say("bundling psh, bioagent and tcmstudio")
    bundle = build_bundle()
    say(f"  {bundle.name}: {len(bundle.data):,} bytes ({bundle.files} files, "
        f"{bundle.raw_bytes:,} bytes unpacked)")
    say("building the browser catalog")
    catalog, doc = catalog_bytes(strict=True)
    say(f"  {doc['counts']['core']} core tools, {doc['counts']['entries']} entries")
    boot = boot_document(bundle, catalog, index_url=index_url)

    out.parent.mkdir(parents=True, exist_ok=True)
    staging = out.with_name(f".{out.name}.build-{secrets.token_hex(4)}")
    try:
        staging.mkdir()
        copied = _copy_web(web, staging, dev=dev)
        runtime = staging / "runtime"
        runtime.mkdir(exist_ok=True)
        (runtime / bundle.name).write_bytes(bundle.data)
        (runtime / "catalog.json").write_bytes(catalog)
        (runtime / "boot.json").write_bytes(_json_bytes(boot))
        (runtime / "source-gateway.json").write_bytes(_json_bytes(source_gateway_document()))
        for name, (content, _media) in data_assets()[0].items():
            (runtime / name).write_bytes(content)
        sw = _fill_service_worker(staging, boot["pyodide"]["index_url"])
        headers = web.parent / "edge" / "_headers"
        if headers.is_file():
            shutil.copyfile(headers, staging / "_headers")
        _install(staging, out)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    kept = f", sw.js keeping {len(sw['files'])} files" if sw else ""
    say(f"wrote {out} ({copied} web files{' with test/ and dev/' if dev else ''}"
        f"{', _headers' if headers.is_file() else ''}{kept})")
    return {"out": str(out), "web": str(web), "web_files": copied, "dev": dev,
            "headers": headers.is_file(), "boot": boot,
            "service_worker": {"version": sw["version"], "files": len(sw["files"])} if sw else None,
            "catalog": {"bytes": len(catalog), "counts": doc["counts"]},
            "bundle": {"name": bundle.name, "sha256": bundle.sha256, "bytes": len(bundle.data),
                       "files": bundle.files, "raw_bytes": bundle.raw_bytes,
                       "sources": bundle.sources}}


# ============================================================================= CLI

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.description = ("Build the static site: the web app plus runtime/catalog.json, "
                          "runtime/boot.json and the Python bundle the browser runs.")
    parser.add_argument("--out", required=True, help="directory to write the site to "
                        "(replaced if it holds an earlier build)")
    parser.add_argument("--web", default=None, help="the web app to publish (default: "
                        "studio/web of this source tree)")
    parser.add_argument("--dev", action="store_true",
                        help="also publish web/test and web/dev (test pages, design gallery)")
    parser.add_argument("--pyodide-index-url", default=None, metavar="URL",
                        help=f"where the worker loads Pyodide from (default: ${ENV_INDEX_URL} or "
                             f"{PYODIDE_INDEX_URL}); absolute, or relative to the site root "
                             "for a self-hosted copy")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")


def run(args: argparse.Namespace) -> int:
    log = (lambda m: print(m, file=sys.stderr))
    try:
        summary = build(args.out, args.web, dev=bool(args.dev),
                        index_url=getattr(args, "pyodide_index_url", None), log=log)
    except BuildError as exc:
        print(f"tcmstudio webbuild: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tcmstudio webbuild")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":                                      # pragma: no cover
    raise SystemExit(main())
