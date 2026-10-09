"""Read the published corpus, in the runner and in the browser (docs/V2.md §11.4).

A ``Corpus`` holds one snapshot's manifest and an LRU of parsed objects. ``get(path)``
fetches the object's gzip bytes, checks their SHA-256 against the manifest, gunzips and
parses them; nothing unverified is ever returned. Each tool call reads through a
``Session`` (``corpus.session()``), which records the ordered, de-duplicated ``{path,
sha256}`` reads that go into the call's receipt; concurrent calls share the cache.

Fetchers bring bytes and nothing else:

* ``DirFetcher(dir)`` — a built site (``…/_site``) or its ``corpus/`` directory;
* ``HttpFetcher(base_url, cache_dir, network=…)`` — the runner: the disk cache first
  (verified on every read), then a download, which honours the call's network setting;
* ``XHRFetcher(base_url)`` — Pyodide only: synchronous XHR from the module worker
  (``responseType = "arraybuffer"``); objects have immutable URLs, so the browser's HTTP
  cache serves repeats.

``default_corpus(ctx)`` picks the source for a call: ``ctx.corpus`` (what the browser worker
passes, ``{base_url, manifest, sha256}``, or ``{latest_url}``, ``{dir}``, ``{cache_dir}``) →
``$TCMSTUDIO_CORPUS`` (a directory or a URL) → a built site next to the runner → the
runner's ``<home>/corpus`` cache with ``https://science.impf.ai/corpus/latest.json``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urljoin

from . import packs as P

__all__ = ["Corpus", "CorpusError", "Session", "DirFetcher", "HttpFetcher", "XHRFetcher",
           "default_corpus", "reset_default_corpora", "corpus_home"]

#: Raw (unzipped) bytes of parsed objects kept in memory, per corpus. A phone's worker
#: keeps less: the Python heap of a browser worker never shrinks.
CACHE_BYTES = 12 * 1024 * 1024 if sys.platform == "emscripten" else 48 * 1024 * 1024
MAX_READS = 50
_TIMEOUT_S = 30.0


class CorpusError(Exception):
    """The corpus cannot be read. ``type`` is ``unavailable`` (absent, or bytes that do
    not match the manifest), ``network_off`` (not cached and downloads are off) or
    ``not_in_snapshot`` (the snapshot holds no such object); ``hint`` says what to do."""

    def __init__(self, type_: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.type = type_
        self.hint = hint


def _manifest_name(ref: str) -> str:
    """``corpus/manifest.<sha12>.json`` (a boot entry) and ``manifest.<sha12>.json`` (a
    latest.json pointer) name the same file in the corpus directory."""
    return str(ref or "").rsplit("/", 1)[-1]


# =========================================================================== fetchers

class DirFetcher:
    """A built site's ``corpus/`` directory (or the directory itself)."""

    kind = "dir"

    def __init__(self, site_dir: str | os.PathLike[str]) -> None:
        root = Path(site_dir).expanduser()
        self.root = root / "corpus" if (root / "corpus" / "latest.json").is_file() else root

    def describe(self) -> str:
        return f"dir:{self.root}"

    def read(self, rel: str, *, fresh: bool = False) -> bytes:
        del fresh
        path = self.root / rel
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CorpusError("unavailable", f"{path}: {exc.strerror or exc}",
                              "Build the site (studio/scripts/build_web.py) or point "
                              "TCMSTUDIO_CORPUS at a built corpus.") from None


class HttpFetcher:
    """The published corpus over HTTP(S), with a disk cache. ``network`` says whether this
    call may download; the cache is read either way (and every read is verified by the
    corpus against the manifest)."""

    kind = "http"

    def __init__(self, base_url: str, cache_dir: str | os.PathLike[str] | None = None, *,
                 network: bool = True, timeout: float = _TIMEOUT_S,
                 opener: Callable[..., Any] | None = None) -> None:
        self.base = base_url if base_url.endswith("/") else base_url + "/"
        self.cache = Path(cache_dir).expanduser() if cache_dir else None
        self.network = network
        self.timeout = timeout
        self._open = opener

    def describe(self) -> str:
        return f"http:{self.base}" + (f" (cache {self.cache})" if self.cache else "")

    def cached(self, rel: str) -> Path | None:
        if self.cache is None:
            return None
        path = self.cache / rel
        return path if path.is_file() else None

    def read(self, rel: str, *, fresh: bool = False) -> bytes:
        hit = None if fresh else self.cached(rel)
        if hit is not None:
            return hit.read_bytes()
        if not self.network:
            stale = self.cached(rel)                 # an old pointer is better than none
            if stale is not None:
                return stale.read_bytes()
            raise CorpusError("network_off",
                              f"{rel} of the corpus is not cached here and web access is off",
                              "Run `tcmstudio corpus fetch` once (with web access) to keep the "
                              "whole corpus on this machine, or turn on web access.")
        data = self._download(self.base + rel)
        if self.cache is not None:
            target = self.cache / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + f".part-{os.getpid()}-{threading.get_ident()}")
                tmp.write_bytes(data)
                os.replace(tmp, target)
            except OSError:
                pass
        return data

    def _download(self, url: str) -> bytes:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "tcmstudio-corpus/1"})
        try:
            opener = self._open or urllib.request.urlopen
            with opener(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise CorpusError("unavailable", f"{url}: HTTP {exc.code}",
                              "The published corpus may have moved; run `tcmstudio corpus "
                              "fetch` to read the latest snapshot.") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CorpusError("unavailable", f"{url}: {reason}",
                              "Check the connection, or run `tcmstudio corpus fetch` once to "
                              "keep the corpus on this machine.") from None


class XHRFetcher:
    """Pyodide: synchronous ``XMLHttpRequest`` from the module worker. Synchronous XHR is
    allowed in workers (not on a page's main thread); ``responseType = "arraybuffer"``
    gives the bytes as stored, so the SHA-256 in the manifest applies to them."""

    kind = "xhr"

    def __init__(self, base_url: str) -> None:
        self.base = base_url if base_url.endswith("/") else base_url + "/"

    def describe(self) -> str:
        return f"xhr:{self.base}"

    def read(self, rel: str, *, fresh: bool = False) -> bytes:
        import importlib
        try:
            js = importlib.import_module("js")
        except ImportError:
            raise CorpusError("unavailable", "synchronous XHR needs the browser runtime",
                              "Read the corpus with DirFetcher or HttpFetcher outside the "
                              "browser.") from None
        url = self.base + rel
        xhr = js.XMLHttpRequest.new()
        try:
            xhr.open("GET", url, False)
            xhr.responseType = "arraybuffer"
            if fresh:
                xhr.setRequestHeader("Cache-Control", "no-cache")
            xhr.send()
        except Exception as exc:                               # noqa: BLE001 (a JS error)
            raise CorpusError("unavailable", f"{url}: {exc}",
                              "The page could not reach the corpus; reload, or check the "
                              "connection.") from None
        status = int(getattr(xhr, "status", 0) or 0)
        if status != 200:
            raise CorpusError("unavailable", f"{url}: HTTP {status or 'no response'}",
                              "The page could not reach the corpus; reload, or check the "
                              "connection.")
        return bytes(js.Uint8Array.new(xhr.response).to_py())


# ============================================================================= corpus

class Corpus:
    """One snapshot: the manifest, verified reads and an LRU of parsed objects.

    ``Corpus(fetcher)`` follows ``latest.json``; ``Corpus(fetcher, manifest=…, sha256=…)``
    pins a manifest (``corpus/manifest.<sha12>.json`` or its bare name) and checks it."""

    def __init__(self, fetcher: Any, *, manifest: str | None = None, sha256: str | None = None,
                 cache_bytes: int = CACHE_BYTES) -> None:
        self.fetcher = fetcher
        if manifest is None:
            raw = fetcher.read("latest.json", fresh=True)
            try:
                latest = json.loads(raw.decode("utf-8"))
                manifest, sha256 = str(latest["manifest"]), str(latest["sha256"])
            except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
                raise CorpusError("unavailable", f"latest.json is not a corpus pointer ({exc})",
                                  "Rebuild or re-fetch the corpus.") from None
        self.manifest_name = _manifest_name(manifest)
        data = fetcher.read(self.manifest_name)
        digest = hashlib.sha256(data).hexdigest()
        if sha256 and digest != sha256:
            raise CorpusError("unavailable", f"{self.manifest_name}: SHA-256 {digest[:12]}… does "
                              f"not match the pinned {str(sha256)[:12]}…; the bytes were not used",
                              "Reload the page, or run `tcmstudio corpus fetch` again.")
        try:
            self.manifest: dict[str, Any] = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise CorpusError("unavailable", f"{self.manifest_name} is not JSON ({exc})") from None
        if self.manifest.get("schema") != P.SCHEMA:
            raise CorpusError("unavailable", f"{self.manifest_name}: schema "
                              f"{self.manifest.get('schema')!r}, expected {P.SCHEMA}",
                              "Update tcmstudio: this snapshot is in a newer format.")
        self.manifest_sha256 = digest
        self.snapshot_id = str(self.manifest.get("snapshot_id") or "")
        self._cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._cache_bytes = 0
        self._limit = cache_bytes
        self._lock = threading.Lock()
        self._reads: list[dict[str, str]] = []
        self.loaded_at = time.monotonic()

    # ------------------------------------------------------------------ reading
    @property
    def objects(self) -> Mapping[str, Any]:
        return self.manifest.get("objects") or {}

    @property
    def packs(self) -> Mapping[str, Any]:
        return self.manifest.get("packs") or {}

    def has(self, path: str) -> bool:
        return path in self.objects

    def raw(self, path: str) -> bytes:
        """The verified, gunzipped bytes of one object."""
        meta = self.objects.get(path)
        if meta is None:
            raise CorpusError("not_in_snapshot", f"{path} is not in snapshot {self.snapshot_id}")
        data = self.fetcher.read(meta["url"])
        digest = hashlib.sha256(data).hexdigest()
        if digest != meta["sha256"]:
            raise CorpusError("unavailable", f"{path}: SHA-256 {digest[:12]}… does not match the "
                              f"manifest's {meta['sha256'][:12]}…; the bytes were not used",
                              "Run `tcmstudio corpus fetch` again (or reload the page) to "
                              "replace a damaged copy.")
        try:
            return gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise CorpusError("unavailable", f"{path}: not gzip ({exc})") from None

    def _load(self, path: str) -> Any:
        with self._lock:
            hit = self._cache.get(path)
            if hit is not None:
                self._cache.move_to_end(path)
                return hit[0]
        raw = self.raw(path)
        value = json.loads(raw.decode("utf-8"))
        with self._lock:
            if path not in self._cache:
                self._cache[path] = (value, len(raw))
                self._cache_bytes += len(raw)
                while self._cache_bytes > self._limit and len(self._cache) > 1:
                    _, (_, size) = self._cache.popitem(last=False)
                    self._cache_bytes -= size
        return value

    def get(self, path: str) -> Any:
        """One object, parsed (shared: do not modify it). Recorded in ``reads()``."""
        value = self._load(path)
        _note(self._reads, path, self.objects[path]["sha256"])
        return value

    def reads(self) -> list[dict[str, str]]:
        return list(self._reads)

    def session(self) -> "Session":
        """A view for one call: same cache, its own read log."""
        return Session(self)


class Session:
    """What one call read from a corpus, for its receipt."""

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self._reads: list[dict[str, str]] = []
        self.packs_read: list[str] = []

    @property
    def manifest(self) -> Mapping[str, Any]:
        return self.corpus.manifest

    @property
    def snapshot_id(self) -> str:
        return self.corpus.snapshot_id

    @property
    def manifest_sha256(self) -> str:
        return self.corpus.manifest_sha256

    def has(self, path: str) -> bool:
        return self.corpus.has(path)

    def has_pack(self, pack: str) -> bool:
        return pack in self.corpus.packs

    def get(self, path: str) -> Any:
        value = self.corpus._load(path)
        _note(self._reads, path, self.corpus.objects[path]["sha256"])
        pack = self.corpus.objects[path].get("pack")
        if pack and pack not in self.packs_read:
            self.packs_read.append(pack)
        return value

    def reads(self) -> list[dict[str, str]]:
        return list(self._reads)

    def receipt(self) -> dict[str, Any]:
        reads = self.reads()
        return {"snapshot_id": self.snapshot_id, "manifest_sha256": self.manifest_sha256,
                "reads": reads[:MAX_READS], "reads_total": len(reads),
                "source": getattr(self.corpus.fetcher, "describe", lambda: "")()}


def _note(reads: list[dict[str, str]], path: str, sha256: str) -> None:
    if not any(r["path"] == path for r in reads):
        reads.append({"path": path, "sha256": sha256})


# ===================================================================== default corpus

_CORPORA: dict[str, Corpus] = {}
_CORPORA_LOCK = threading.Lock()
#: How long a corpus found through latest.json is used before the pointer is read again.
_LATEST_TTL_S = 600.0


def reset_default_corpora() -> None:
    """Forget the corpora opened for calls (tests; a new snapshot fetched by the CLI)."""
    with _CORPORA_LOCK:
        _CORPORA.clear()


def corpus_home(ctx: Any = None) -> Path:
    """Where the runner keeps its copy: ``ctx.corpus.cache_dir``, else ``<home>/corpus``
    (``<home>`` is the parent of the projects directory the runner passes as
    ``state_root``), else ``~/.tcmscience/studio/corpus``."""
    spec = _mapping(_value(ctx, "corpus"))
    if spec.get("cache_dir"):
        return Path(str(spec["cache_dir"])).expanduser()
    state_root = _value(ctx, "state_root")
    if state_root and Path(str(state_root)).name == "projects":
        return Path(str(state_root)).parent / "corpus"
    return Path("~/.tcmscience/studio").expanduser() / "corpus"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _value(ctx: Any, key: str) -> Any:
    """A context field, from a ``dispatch.Context`` or the mapping a caller passed."""
    return ctx.get(key) if isinstance(ctx, Mapping) else getattr(ctx, key, None)


def _is_url(text: str) -> bool:
    return str(text).startswith(("http://", "https://"))


def _site_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    return [here.parents[4] / "_site"] if len(here.parents) > 4 else []


def _opened(key: str, ttl: float | None, make: Callable[[], Corpus]) -> Corpus:
    with _CORPORA_LOCK:
        hit = _CORPORA.get(key)
        if hit is not None and (ttl is None or time.monotonic() - hit.loaded_at < ttl):
            return hit
    corpus = make()
    with _CORPORA_LOCK:
        _CORPORA[key] = corpus
    return corpus


def default_corpus(ctx: Any = None) -> Corpus:
    """The corpus a call reads (docs/V2.md §11.4). Raises ``CorpusError`` when there is
    none; the tools turn that into a failed envelope with the remedy."""
    where = _value(ctx, "where")
    network = bool(_value(ctx, "network"))
    spec = _mapping(_value(ctx, "corpus"))
    browser = where == "browser" or sys.platform == "emscripten"

    def http(base: str, *, manifest: str | None = None, sha256: str | None = None,
             cache: Path | None = None) -> Corpus:
        if browser:
            fetcher: Any = XHRFetcher(base)
        else:
            fetcher = HttpFetcher(base, cache, network=network)
        key = f"{fetcher.describe()}|{manifest or 'latest'}|{sha256 or ''}|{network}"
        return _opened(key, None if manifest else _LATEST_TTL_S,
                       lambda: Corpus(fetcher, manifest=manifest, sha256=sha256))

    # 1. what the caller passed (the browser worker passes the boot entry)
    if spec.get("dir"):
        d = Path(str(spec["dir"]))
        return _opened(f"dir:{d}", None, lambda: Corpus(DirFetcher(d)))
    if spec.get("base_url") and spec.get("manifest"):
        return http(str(spec["base_url"]), manifest=str(spec["manifest"]),
                    sha256=str(spec.get("sha256") or "") or None,
                    cache=corpus_home(ctx) if not browser else None)
    if spec.get("latest_url"):
        base = urljoin(str(spec["latest_url"]), ".")
        return http(base, cache=corpus_home(ctx) if not browser else None)
    # 2. the environment
    env = os.environ.get("TCMSTUDIO_CORPUS", "").strip()
    if env:
        if _is_url(env):
            base = urljoin(env, ".") if env.endswith(".json") else env
            return http(base, cache=corpus_home(ctx) if not browser else None)
        d = Path(env).expanduser()
        return _opened(f"dir:{d}", None, lambda: Corpus(DirFetcher(d)))
    if browser:
        raise CorpusError("unavailable", "the page passed no corpus to the worker",
                          "Reload the page; the browser runtime reads the corpus named in "
                          "runtime/boot.json.")
    # 3. a built site next to the runner (spec.site_dir from the server, or studio/_site)
    for d in ([Path(str(spec["site_dir"]))] if spec.get("site_dir") else []) + _site_candidates():
        if (d / "corpus" / "latest.json").is_file():
            return _opened(f"dir:{d}", None, lambda d=d: Corpus(DirFetcher(d)))
    # 4. the runner's own cache, refreshed from the public pointer when the network allows
    cache = corpus_home(ctx)
    return http(urljoin(P.LATEST_URL, "."), cache=cache)
