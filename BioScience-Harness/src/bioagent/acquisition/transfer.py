"""Verified transfers: a resume only onto the same file, a retry only for what is transient.

``VerifiedTransfer`` is ``acquisition.downloader.Downloader`` with the rules a large scientific
file needs, added by subclassing:

* **A resume continues the same file, or nothing.** The identity of the first answer is kept
  beside the partial file, in ``<name>.part.json``: the URL, a strong ``ETag`` (a weak ``W/``
  one cannot validate a range and is not kept), ``Last-Modified``, the answer's ``Date`` and
  the total size. A later fetch resumes with ``Range: bytes=N-`` and ``If-Range`` naming
  that validator (the ETag; a ``Last-Modified`` date only when it is a strong validator, the
  answer's ``Date`` at least one second after it, RFC 9110 §8.8.2.2 and §13.1.5), and
  accepts only a ``206`` whose ``Content-Range`` is exactly ``bytes N-(total-1)/total`` and
  whose ``ETag`` (or, without one, ``Last-Modified``) is the recorded one: a server that
  ignores ``If-Range`` cannot join two files. A ``200`` means the file changed (or the
  server serves no ranges): the transfer starts again from byte 0 on that answer. A ``206``
  for any other range or from another file drops the partial file and restarts the
  transfer, once. A partial file with no usable validator is resumed only when a reviewed
  checksum will check the joined file; otherwise it is dropped ("resume refused: identity
  unverifiable") and the file is fetched whole. A partial file that is already whole (a
  cancel after its last block) is not fetched again: a reviewed checksum, or the server's
  own ETag or date matching the recorded one, checks it as it is. The downloader it extends sent
  ``Range: bytes=N-`` with nothing to say which file the first N bytes came from, and kept
  a file joined from two releases of the same size as verified by its size.
* **A retry only for what may pass.** Timeouts, reset or dropped connections and HTTP 408,
  425, 429 and 5xx are retried, at most ``max_retries`` attempts and ``retry_budget_s``
  seconds of waiting in all; a ``Retry-After`` is honoured up to ``retry_after_cap_s``
  (60 s), and a longer requested pause ends the transfer instead of being shortened.
  404 and 410 fail at once (the reviewed URL is out of date), as do 401/403/407; a 416
  whose ``Content-Range`` says the partial file is the whole file (the ``If-Range``
  validator matched) keeps it for the checks, and any other 416 drops the partial file and
  restarts the transfer once. A full disk (``ENOSPC``) stops at once, while the body, its
  identity record or the record of the kept file is written, and keeps the partial file for
  a later resume; it is never retried and never blamed on the host.
* **Room on the disk is checked against the server's size**, not only the declared one,
  before any byte is requested.
* **One transfer of a file at a time.** A non-blocking ``flock`` on
  ``<root>/.locks/<name>.lock`` is held for the whole fetch: a second fetch of the same file
  (another job, another process) is refused at once instead of appending to the same
  partial file. The read-modify-write of ``.downloads.json`` is held under its own lock.
* **What arrived is checked before it is kept.** A gzip, bzip2 or xz stream is read to its
  end (CRC and length), every member of a zip or xlsx archive is read through its CRC, a
  Parquet file must carry its magic at both ends, and a headerless table must have at least
  the fields its spec declares (as ``tcmdb.store`` reads it; a file that hashes to its
  reviewed checksum is not held to this heuristic). A file that fails is not kept: the final
  name never holds it.
* **The record says how the file was obtained.** The ``.downloads.json`` entry keeps the
  downloader's fields (``url, bytes, checksum, verified, fetched_at``) and adds ``sha256``,
  ``resumed``/``resumed_from``, ``etag``, ``last_modified``, ``final_url``, ``server`` (the
  ``Server`` header), ``retrieved_at``, ``release`` (when the caller's spec names one),
  ``release_headers`` (a rolling source's own, e.g. ``X-UniProt-Release``), ``restarts``,
  ``attempts`` and the content checks. Fields are added, never renamed.
* **Progress and cancellation.** ``progress(name, done, total)`` is called as bytes reach
  the disk; ``cancel`` (a ``threading.Event`` or a callable) stops the transfer between two
  blocks, closes the partial file and keeps it, with its identity record, for a later
  verified resume (``TransferCancelled``). A block is whatever one read of the socket
  returns (at most ``chunk`` bytes), so a cancel takes effect as soon as any byte arrives,
  not after a whole ``chunk`` on a slow server; a server that sends nothing at all holds the
  read until ``timeout_s`` (``acquisition.service.cancel_on_signals`` bounds a cancelled
  process's life regardless).

This module is not imported by ``acquisition/__init__``, ``downloader`` or ``sources``:
those files are hashed into the pinned governed skills (``registry/skills.lock.yaml``), and
a new import there would change what the pins cover. Callers import it by its own name.
"""

from __future__ import annotations

import bz2
import contextlib
import csv
import email.utils
import errno
import gzip
import hashlib
import http.client
import json
import lzma
import os
import shutil
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from ..backends.http import _RedirectRefused
from ..validation import BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, utc_now
from .downloader import _UA, DownloadError, DownloadResult, Downloader

__all__ = ["VerifiedTransfer", "TransferResult", "TransferError", "TransferCancelled",
           "SIDECAR_SCHEMA", "RETRY_AFTER_CAP_S", "check_archive", "check_columns",
           "looks_like_html", "read_sidecar", "write_sidecar", "identity_of", "validator_of",
           "record_entry", "forget_entry", "exclusive"]

SIDECAR_SCHEMA = "bioagent.transfer-part/1"
#: the longest ``Retry-After`` honoured; a server asking for more ends the transfer
RETRY_AFTER_CAP_S = 60.0
MANIFEST = ".downloads.json"
LOCK_DIR = ".locks"
_TRANSIENT_HTTP = frozenset({408, 425, 429})
_DISK_FULL = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})


class TransferError(DownloadError):
    """A transfer that stopped. ``outcome`` is its validation status (FAIL: not the reviewed
    file or not a whole one; BLOCKED: something outside the code stopped it), and ``check``
    names the check that refused a file that did arrive (checksum, size, decompression,
    format, html)."""

    def __init__(self, message: str, *, outcome: str = FAIL, check: str | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.check = check


class TransferCancelled(TransferError):
    """Stopped on request. The partial file and its identity record are kept: the next fetch
    resumes them, onto the same file only."""

    cancelled = True

    def __init__(self, message: str, *, have: int = 0) -> None:
        super().__init__(message, outcome=SKIPPED)
        self.have = have


class _Interrupted(Exception):
    """Reading the body failed part-way (a reset, a timeout, a truncated answer)."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _LocalFailure(Exception):
    """Writing the partial file failed: the disk, not the network."""

    def __init__(self, cause: OSError) -> None:
        super().__init__(str(cause))
        self.cause = cause


@dataclass
class TransferResult(DownloadResult):
    """``DownloadResult`` and how the file was obtained."""

    sha256: str = ""
    resumed_from: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    server: str | None = None
    retrieved_at: str | None = None
    release: str | None = None
    restarts: list[str] = field(default_factory=list)
    attempts: int = 0
    checks: dict[str, Any] = field(default_factory=dict)
    #: headers naming the server's release of a rolling file (``X-UniProt-Release``)
    release_headers: dict[str, str] | None = None

    def record(self) -> dict[str, Any]:
        """The fields this adds to the downloader's record and to an audit line."""
        out: dict[str, Any] = {
            "sha256": self.sha256 or None, "resumed": bool(self.resumed),
            "resumed_from": self.resumed_from if self.resumed else None,
            "etag": self.etag, "last_modified": self.last_modified,
            "final_url": self.final_url, "server": self.server,
            "retrieved_at": self.retrieved_at, "release": self.release,
            "release_headers": self.release_headers or None,
            "restarts": list(self.restarts)[:10] or None,
            "attempts": self.attempts or None}
        for name, check in self.checks.items():
            out[name] = check
        return {k: v for k, v in out.items() if v is not None}


# ------------------------------------------------------------------------- locks

def _lock(fd: int, wait: bool) -> bool:
    try:
        import fcntl
    except ImportError:                                         # pragma: no cover - Windows
        try:
            import msvcrt
        except ImportError:
            return True                       # no lock primitive here: nothing to hold
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        return True
    except (BlockingIOError, InterruptedError):
        return False
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
            return False
        raise


def _unlock(fd: int) -> None:
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:                                         # pragma: no cover - Windows
        try:
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass


@contextlib.contextmanager
def exclusive(path: str | Path, *, wait: bool) -> Iterator[bool]:
    """Hold an exclusive lock on ``path`` (created if absent) for the block; yields whether
    it is held. ``wait=False`` never blocks: a lock another holder has yields False.

    ``flock`` locks belong to an open file, so two transfers in one process exclude each
    other as two processes do. The lock file is never deleted (deleting it would let a
    second holder lock a new file while the first still holds the old one)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        held = _lock(fd, wait)
        try:
            yield held
        finally:
            if held:
                _unlock(fd)
    finally:
        os.close(fd)


def _write_json(path: Path, doc: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)          # a full disk leaves no half-written record
        raise


def _read_manifest(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_entry(root: str | Path, name: str, entry: Mapping[str, Any]) -> None:
    """Set ``name``'s entry in ``<root>/.downloads.json``, read and rewritten under its lock."""
    root = Path(root)
    with exclusive(root / LOCK_DIR / (MANIFEST.lstrip(".") + ".lock"), wait=True):
        data = _read_manifest(root)
        data[name] = dict(entry)
        _write_json(root / MANIFEST, data)


def forget_entry(root: str | Path, name: str) -> None:
    """Drop ``name``'s entry from ``<root>/.downloads.json`` (a file that is no longer kept)."""
    root = Path(root)
    with exclusive(root / LOCK_DIR / (MANIFEST.lstrip(".") + ".lock"), wait=True):
        data = _read_manifest(root)
        if data.pop(name, None) is not None:
            _write_json(root / MANIFEST, data)


def read_sidecar(path: str | Path) -> dict[str, Any] | None:
    """A partial file's identity record (``<name>.part.json``), or None."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == SIDECAR_SCHEMA else None


def write_sidecar(path: str | Path, ident: Mapping[str, Any]) -> None:
    """Write a partial file's identity record (``identity_of``) beside it."""
    _write_json(Path(path), ident)


def identity_of(url: str, headers: Any, total: int | None, *,
                final_url: str | None = None) -> dict[str, Any]:
    """The identity record of the answer a partial file's first byte came from: the URL, a
    strong ETag (a weak one is dropped), Last-Modified, the answer's Date (which decides
    whether that date can validate a range), the total size, the server and a rolling
    source's own release headers (``X-UniProt-Release``)."""
    return {"schema": SIDECAR_SCHEMA, "url": url, "etag": _strong(headers.get("ETag")),
            "last_modified": headers.get("Last-Modified"), "date": headers.get("Date"),
            "total": total, "started_at": utc_now(), "final_url": final_url or url,
            "server": headers.get("Server"),
            "release_headers": {k: v for k, v in headers.items()
                                if "release" in k.lower()} or None}


def validator_of(ident: Mapping[str, Any]) -> str | None:
    """The ``If-Range`` validator an identity record allows: its strong ETag; else its
    Last-Modified, only when that date is a strong validator (RFC 9110 §8.8.2.2: the Date of
    the same answer at least one second after it; §13.1.5 forbids sending a weak date in
    If-Range); else None (a resume then needs a reviewed checksum)."""
    if ident.get("etag"):
        return str(ident["etag"])
    lm = ident.get("last_modified")
    return str(lm) if lm and _strong_date(str(lm), ident.get("date")) else None


# ----------------------------------------------------------------- content checks

def check_archive(path: str | Path, name: str | None = None) -> tuple[str, str, dict[str, Any]]:
    """Read a compressed file or an archive to its end, in bounded memory: a gzip, bzip2 or xz
    stream through its CRC and length, every member of a zip (an xlsx is one) through its
    CRC, a Parquet file's magic at both ends. ``name`` is the file's name when ``path`` is
    its partial file. ``(status, detail, evidence)``; NOT_APPLICABLE for anything else."""
    path = Path(path)
    low = (name or path.name).lower()
    try:
        for suffixes, opener, label in (((".gz", ".tgz"), gzip.open, "gzip"),
                                        ((".bz2",), bz2.open, "bzip2"),
                                        ((".xz",), lzma.open, "xz")):
            if low.endswith(suffixes):
                total = 0
                with opener(path, "rb") as fh:
                    while True:
                        block = fh.read(1 << 22)
                        if not block:
                            break
                        total += len(block)
                return PASS, f"read to its end; the {label} CRC and length check passed", {
                    "uncompressed_bytes": total}
        if low.endswith((".zip", ".xlsx")):
            with zipfile.ZipFile(path) as zf:
                bad = zf.testzip()
                members = len(zf.namelist())
            if bad:
                return FAIL, f"member {bad} fails its CRC", {"members": members}
            return PASS, f"{members} members, every CRC checked", {"members": members}
        if low.endswith(".parquet"):
            size = path.stat().st_size
            with open(path, "rb") as fh:
                head = fh.read(4)
                fh.seek(max(0, size - 4))
                tail = fh.read(4)
            if size >= 12 and head == b"PAR1" and tail == b"PAR1":
                return PASS, "the Parquet magic at both ends", {}
            return FAIL, "not a whole Parquet file (no PAR1 magic at both ends)", {}
    except (OSError, EOFError, zlib.error, lzma.LZMAError, zipfile.BadZipFile,
            RuntimeError, NotImplementedError) as exc:
        return FAIL, f"{type(exc).__name__}: {exc}", {}
    return NOT_APPLICABLE, "not compressed", {}


def check_columns(path: str | Path, name: str | None, fmt: str | None,
                  columns: Iterable[str] = ()) -> tuple[str, str, dict[str, Any]]:
    """A headerless table's first line against the fields its spec declares
    (``FileSpec.columns``). Nothing is checked where the spec declares none: a table with a
    header row is read by its header, and guessing a column count would refuse real files.
    A line with more fields than declared passes, as ``tcmdb.store`` reads it (a trailing
    tab, an extra column); only one with fewer is refused."""
    columns = tuple(columns or ())
    base = (fmt or "").split(".", 1)[0]
    if not columns:
        return NOT_APPLICABLE, "the spec declares no columns to check", {}
    if base not in ("tsv", "csv", "ws"):
        return NOT_APPLICABLE, f"not a delimited table ({fmt})", {}
    path = Path(path)
    gz = (name or path.name).lower().endswith(".gz")
    try:
        with (gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") if gz
              else open(path, encoding="utf-8", errors="replace", newline="")) as fh:
            head = fh.read(1 << 16)
    except (OSError, EOFError, zlib.error) as exc:
        return FAIL, f"{type(exc).__name__}: {exc}", {}
    first = next((line for line in head.splitlines() if line.strip()), "")
    if not first:
        return FAIL, "the file holds no line to check", {}
    if base == "csv":
        fields = next(csv.reader([first]), [])
    elif base == "tsv":
        fields = first.split("\t")
    else:
        fields = first.split()
    if len(fields) < len(columns):
        return FAIL, (f"the first line has {len(fields)} field(s) where the spec declares "
                      f"{len(columns)} ({', '.join(columns)})"), {"fields": len(fields)}
    return PASS, (f"{len(fields)} fields, as the spec declares" if len(fields) == len(columns)
                  else f"{len(fields)} fields, at least the {len(columns)} the spec declares"
                  ), {"fields": len(fields)}


def looks_like_html(path: str | Path) -> bool:
    """Whether a file is an HTML page (its first bytes), as ``tcmdb.hub`` tells one."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return False
    if head[:2] == b"\x1f\x8b" or head[:4] == b"PK\x03\x04":
        return False
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return text.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


# ------------------------------------------------------------------------ helpers

def _strong(etag: str | None) -> str | None:
    """A strong entity tag, or None (absent, or weak: ``W/"..."`` cannot validate a range)."""
    if not etag:
        return None
    etag = etag.strip()
    return None if etag.startswith(("W/", "w/")) else etag


def _strong_date(last_modified: str, date: str | None) -> bool:
    """Whether a Last-Modified date is a strong validator: the answer's Date is at least one
    second after it (a file changed twice within one second would carry the same date)."""
    if not date:
        return False
    try:
        return (email.utils.parsedate_to_datetime(date)
                - email.utils.parsedate_to_datetime(last_modified)).total_seconds() >= 1
    except (TypeError, ValueError, IndexError):
        return False                 # unparsable, or naive against aware: not shown strong


def _unsatisfied_total(value: str | None) -> int | None:
    """A 416's ``Content-Range: bytes */total`` → total."""
    unit, _, rest = (value or "").strip().partition(" ")
    if unit.lower() == "bytes" and rest.startswith("*/") and rest[2:].isdigit():
        return int(rest[2:])
    return None


def _content_range(value: str | None) -> tuple[int, int, int | None] | None:
    """``bytes first-last/total`` → (first, last, total or None for ``*``)."""
    if not value:
        return None
    unit, _, rest = value.strip().partition(" ")
    span, _, total = rest.partition("/")
    first, _, last = span.partition("-")
    if unit.lower() != "bytes" or not first.isdigit() or not last.isdigit():
        return None
    return int(first), int(last), int(total) if total.isdigit() else None


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _transient(exc: BaseException) -> bool:
    """Whether a failed request may pass if asked again: a timeout or a dropped connection
    may; a name that does not resolve, a refused proxy tunnel or a TLS failure will not."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP or exc.code >= 500
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason).lower()
    if isinstance(reason, (socket.gaierror, ssl.SSLError)) or "tunnel connection failed" in text \
            or "certificate verify failed" in text:
        return False
    return isinstance(reason, (TimeoutError, socket.timeout, ConnectionError,
                               http.client.HTTPException, OSError)) or "timed out" in text


def _digests(path: Path, algorithms: Iterable[str]) -> dict[str, str]:
    hashers = {a: hashlib.new(a) for a in dict.fromkeys(algorithms)}
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            for h in hashers.values():
                h.update(block)
    return {a: f"{a}:{h.hexdigest()}" for a, h in hashers.items()}


# ----------------------------------------------------------------------- transfer

class VerifiedTransfer(Downloader):
    """The downloader with verified resume, controlled retries, locks, content checks and a
    fuller record (module docstring). Same constructor, plus ``progress``, ``cancel``,
    ``retry_budget_s``, ``retry_after_cap_s`` and ``retry_backoff_s``; ``fetch`` takes the
    same arguments plus ``release``, ``fmt``, ``columns`` and ``refuse_html``."""

    #: read by ``TCMDataHub._fetch_file``: this downloader takes the extra fetch arguments
    verified_transfer = True

    def __init__(self, root: Path | str, *,
                 progress: Callable[[str, int, int | None], None] | None = None,
                 cancel: threading.Event | Callable[[], bool] | None = None,
                 retry_budget_s: float = 300.0, retry_after_cap_s: float = RETRY_AFTER_CAP_S,
                 retry_backoff_s: float = 1.5, **kwargs: Any) -> None:
        super().__init__(root, **kwargs)
        self.progress = progress
        self.cancel = cancel
        #: the most seconds spent waiting between attempts, over the whole fetch
        self.retry_budget_s = float(retry_budget_s)
        self.retry_after_cap_s = float(retry_after_cap_s)
        #: the first wait after a transient failure; it doubles per attempt, up to 10 s
        self.retry_backoff_s = float(retry_backoff_s)

    # ------------------------------------------------------------ cancellation
    def cancelled(self) -> bool:
        c = self.cancel
        if c is None:
            return False
        if hasattr(c, "is_set"):
            return bool(c.is_set())
        return bool(c())

    def _sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if hasattr(self.cancel, "wait"):
            self.cancel.wait(seconds)
            return
        end = time.monotonic() + seconds
        while not self.cancelled():
            left = end - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(0.2, left))

    def _stop_if_cancelled(self, name: str, part: Path) -> None:
        if self.cancelled():
            have = part.stat().st_size if part.exists() else 0
            raise TransferCancelled(
                f"{name}: cancelled after {have} bytes; the partial file and its identity "
                "record are kept, and the next fetch resumes it only onto the same file",
                have=have)

    # ---------------------------------------------------------------- the api
    def fetch(self, url: str, name: str, *, checksum: str | None = None,
              expected_bytes: int | None = None, confirm: bool = False, force: bool = False,
              release: str | None = None, fmt: str | None = None,
              columns: Iterable[str] = (), refuse_html: bool = False) -> TransferResult:
        """Download ``url`` to ``root/name``, verified, under the file's lock."""
        lock = self.root / LOCK_DIR / f"{name}.lock"
        with exclusive(lock, wait=False) as held:
            if not held:
                raise TransferError(
                    f"{name}: another acquisition of this file is running (it holds {lock}); "
                    "this one did not start and changed nothing", outcome=BLOCKED)
            return self._fetch_locked(url, name, checksum=checksum,
                                      expected_bytes=expected_bytes, confirm=confirm,
                                      force=force, release=release, fmt=fmt,
                                      columns=tuple(columns or ()), refuse_html=refuse_html)

    def _fetch_locked(self, url: str, name: str, *, checksum: str | None,
                      expected_bytes: int | None, confirm: bool, force: bool,
                      release: str | None, fmt: str | None, columns: tuple[str, ...],
                      refuse_html: bool) -> TransferResult:
        t0 = time.perf_counter()
        final = self.root / name
        part = self.root / (name + ".part")
        side = self.root / (name + ".part.json")
        if final.exists() and not force:
            cached = self._cached(url, final, name, checksum, expected_bytes, release, t0)
            if cached is not None:
                return cached
        restarts: list[str] = []
        parsed = urllib.parse.urlsplit(url)
        ident: dict[str, Any] = {}
        joins: list[int] = []
        attempts = 0
        whole_at: int | None = None        # a partial file already whole, kept as it was
        if parsed.scheme == "file":
            src = Path(urllib.request.url2pathname(parsed.path))
            shutil.copyfile(src, part)
            remote_size: int | None = src.stat().st_size
        else:
            self._stop_if_cancelled(name, part)
            info = self._probe(url)
            remote_size = info.get("size")
            size_hint = max(expected_bytes or 0, remote_size or 0)
            if size_hint > self.size_gate_bytes and not confirm:
                raise DownloadError(
                    f"{name} is {size_hint / 1e9:.2f} GB, above the {self.size_gate_bytes / 1e9:.2f} GB "
                    "gate; call fetch(..., confirm=True) to proceed")
            offset, validator, whole = self._plan(name, part, side, url, info, checksum,
                                                  restarts)
            if whole:
                ident = read_sidecar(side) or {}
                whole_at = offset
                remote_size = remote_size or ident.get("total")
                self._log(f"{name}: the partial file is already whole ({offset} bytes); it is "
                          "checked as it is, and nothing is requested")
            else:
                from ..tcmdb.acquire import disk_shortfall
                short = disk_shortfall(self.root, max(0, size_hint - offset))
                if short:
                    raise TransferError(f"{name}: {short}", outcome=BLOCKED)
                self._stop_if_cancelled(name, part)
                state = self._transfer(url, name, part, side, remote_size, offset, validator,
                                       checksum, None if confirm else self.size_gate_bytes,
                                       restarts)
                ident, joins, attempts = state["ident"], state["joins"], state["attempts"]
                whole_at = state.get("whole_at")
                if state["total"] is not None:
                    remote_size = state["total"]       # the answer kept, not the probe's

        got_bytes = part.stat().st_size
        joined = (f"; it was joined from two transfers at byte {joins[0]}, so the two parts "
                  "are not one file") if joins else ""
        if expected_bytes is not None and got_bytes != expected_bytes:
            self._discard(part, side)
            raise DownloadError(f"{name}: size {got_bytes} != expected {expected_bytes}{joined}")
        if remote_size is not None and got_bytes != remote_size and parsed.scheme != "file":
            self._discard(part, side)
            raise DownloadError(f"{name}: size {got_bytes} != server-reported {remote_size}{joined}")
        algorithm = checksum.split(":", 1)[0].lower() if checksum else "sha256"
        sums = _digests(part, ("sha256", algorithm))
        got_sum = sums[algorithm]
        if checksum and got_sum.lower() != checksum.lower():
            self._discard(part, side)
            raise DownloadError(f"{name}: checksum mismatch (got {got_sum[:23]}..., expected "
                                f"{checksum[:23]}...){joined}")
        # the column heuristic is for a file nothing else checks: a reviewed checksum that
        # matched has shown it is the reviewed file, whatever its first line looks like
        checks = self._inspect(part, side, name, fmt, () if checksum else columns, refuse_html)
        verified = bool(checksum) or expected_bytes is not None or remote_size is not None
        resumed_from = joins[0] if joins else whole_at
        res = TransferResult(
            url, final, got_bytes, verified, got_sum, resumed_from is not None,
            time.perf_counter() - t0, sha256=sums["sha256"], resumed_from=resumed_from,
            etag=ident.get("etag"), last_modified=ident.get("last_modified"),
            final_url=ident.get("final_url") or url, server=ident.get("server"),
            retrieved_at=utc_now(), release=release or None, restarts=restarts,
            attempts=attempts, checks=checks, release_headers=ident.get("release_headers"))
        os.replace(part, final)            # atomic: the final name never holds partial data
        try:
            record_entry(self.root, name, {"url": url, "bytes": got_bytes, "checksum": got_sum,
                                           "verified": verified, "fetched_at": time.time(),
                                           "route": "download", **res.record()})
        except OSError as exc:
            # a file kept without its record would read as copied in by hand: it goes back
            # to being a whole partial file, with its identity record, and the next fetch
            # checks it again without downloading it (a rename needs no free space)
            with contextlib.suppress(OSError):
                os.replace(final, part)
            raise self._local_error(name, exc, got_bytes, writing="the record of the kept file"
                                    ) from exc
        side.unlink(missing_ok=True)
        return res

    def _record(self, res: DownloadResult) -> None:
        """The downloader's own record, written under the manifest's lock."""
        extra = res.record() if isinstance(res, TransferResult) else {}
        record_entry(self.root, res.path.name, {
            "url": res.url, "bytes": res.bytes, "checksum": res.checksum,
            "verified": res.verified, "fetched_at": time.time(), **extra})

    # ------------------------------------------------------------- the stages
    def _cached(self, url: str, final: Path, name: str, checksum: str | None,
                expected_bytes: int | None, release: str | None, t0: float
                ) -> TransferResult | None:
        """The kept file, when it is still the one asked for (the downloader's rule)."""
        rec = _read_manifest(self.root).get(name) or {}
        kept = {k: rec.get(k) for k in ("etag", "last_modified", "final_url", "server",
                                        "retrieved_at", "release_headers")}
        size = final.stat().st_size
        if checksum:
            algorithm = checksum.split(":", 1)[0].lower()
            sums = _digests(final, ("sha256", algorithm))
            if sums[algorithm].lower() == checksum.lower():
                return TransferResult(url, final, size, True, sums[algorithm], False,
                                      time.perf_counter() - t0, from_cache=True,
                                      sha256=sums["sha256"],
                                      release=rec.get("release") or release, **kept)
            self._log(f"{name}: existing file fails checksum; re-downloading")
            return None
        if expected_bytes is None or size == expected_bytes:
            return TransferResult(url, final, size, expected_bytes is not None, "", False,
                                  time.perf_counter() - t0, from_cache=True,
                                  sha256=str(rec.get("sha256") or ""),
                                  release=rec.get("release") or release, **kept)
        return None

    def _probe(self, url: str) -> dict[str, Any]:
        """The server's size and the file's identity: a HEAD, then (when the HEAD gave no
        size) a one-byte ranged GET, as the downloader does. Only a 404 or 410 stops the
        probe (the download's single GET then says so); a host that refuses HEAD (401, 403,
        405: signed object-store URLs, some CDNs) may still serve the ranged GET, whose
        size the size gate and the disk check then use."""
        info: dict[str, Any] = {"size": None, "etag": None, "last_modified": None,
                                "server": None, "final_url": None}
        for method, hdrs in (("HEAD", {}), ("GET", {"Range": "bytes=0-0"})):
            try:
                self._pace()
                req = urllib.request.Request(url, method=method,
                                             headers={"User-Agent": _UA, **hdrs})
                with self._open(req) as r:
                    h = r.headers
                    info["etag"] = info["etag"] or _strong(h.get("ETag"))
                    info["last_modified"] = info["last_modified"] or h.get("Last-Modified")
                    info["server"] = info["server"] or h.get("Server")
                    info["final_url"] = getattr(r, "url", None) or url
                    cr = _content_range(h.get("Content-Range"))
                    if cr and cr[2] is not None:
                        info["size"] = cr[2]
                        return info
                    cl = h.get("Content-Length")
                    if method == "HEAD" and cl and str(cl).isdigit() and int(cl) > 0:
                        info["size"] = int(cl)
                        return info
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 410):
                    return info
                continue
            except _RedirectRefused:
                return info                    # the download is refused the same way
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                continue
        return info

    def _plan(self, name: str, part: Path, side: Path, url: str, info: Mapping[str, Any],
              checksum: str | None, restarts: list[str]) -> tuple[int, str | None, bool]:
        """Where a transfer starts: ``(offset, If-Range validator, whole)``. A partial file is
        continued only when its identity record still matches what the server says, and
        when a validator or a reviewed checksum will show that the joined file is one.
        ``whole``: the partial file is already the whole file and its identity is shown
        without a request (a reviewed checksum will check it, or the server's ETag, or its
        date, is the recorded one): it goes straight to the checks."""
        have = part.stat().st_size if part.exists() else 0
        rec = read_sidecar(side) or {}
        if not have:
            self._discard(part, side)
            return 0, None, False
        total = info.get("size")
        why = ""
        if rec and rec.get("url") != url:
            why = "it was started from another URL"
        elif rec.get("total") and total and rec["total"] != total:
            why = f"the server's size changed ({rec['total']} -> {total} bytes): another file"
        elif rec.get("etag") and info.get("etag") and info["etag"] != rec["etag"]:
            why = f"the server's ETag changed ({rec['etag']} -> {info['etag']}): another file"
        elif rec.get("last_modified") and info.get("last_modified") and \
                info["last_modified"] != rec["last_modified"]:
            why = (f"the server's Last-Modified changed ({rec['last_modified']} -> "
                   f"{info['last_modified']}): another file")
        elif (rec.get("total") or total) and have > (rec.get("total") or total):
            why = "it is longer than the file"
        validator = None if why else validator_of(rec)
        if not why and not validator and not checksum:
            why = ("resume refused: identity unverifiable (no strong ETag, and no Last-Modified "
                   "date its answer's Date shows to be strong, was recorded for it, and no "
                   "reviewed checksum will check the joined file)")
        if why:
            restarts.append(f"{have} bytes of a partial file dropped: {why}")
            self._log(f"{name}: {restarts[-1]}; fetching it whole")
            self._discard(part, side)
            return 0, None, False
        size = rec.get("total") or total
        if size and have == size:
            # nothing is left to ask for. Without a checksum or the server's own validator
            # matching the recorded one, the range request below asks the server: a 416
            # whose Content-Range is this size means its If-Range matched (``_transfer``)
            shown = bool(checksum) or bool(rec.get("etag") and info.get("etag") == rec["etag"]) \
                or bool(not rec.get("etag") and validator
                        and info.get("last_modified") == rec.get("last_modified"))
            if shown:
                return have, validator, True
        return have, validator, False

    def _retry_delay(self, exc: BaseException, attempt: int, waited: float) -> float | None:
        """Seconds to wait before asking again, or None when this failure is not retried."""
        if not _transient(exc):
            return None
        delay = min(10.0, self.retry_backoff_s * 2 ** attempt)
        if isinstance(exc, urllib.error.HTTPError):
            after = _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
            if after is not None:
                if after > self.retry_after_cap_s:
                    return None               # a longer pause ends the transfer, unshortened
                delay = after
        if attempt >= self.max_retries or waited + delay > self.retry_budget_s:
            return None
        return delay

    def _http_error(self, name: str, url: str, exc: urllib.error.HTTPError) -> DownloadError:
        if exc.code in (401, 403, 407):
            body = ""
            try:
                body = exc.read(300).decode("utf-8", "replace").lower()
            except Exception:                                   # noqa: BLE001
                pass
            if exc.code == 407 or any(w in body for w in ("sandbox", "proxy", "policy")):
                return DownloadError(
                    f"network access denied for {urllib.parse.urlsplit(url).hostname}")
        return DownloadError(f"{name}: HTTP {exc.code} {exc.reason}".rstrip())

    def _local_error(self, name: str, exc: OSError, have: int, *,
                     writing: str = "the partial file", kept: bool = True) -> TransferError:
        """A local write that failed, said as the disk's failure, never the host's.
        ``writing`` names what was being written; ``kept`` whether a partial file stays."""
        stays = (f"the partial file ({have} bytes) and its identity record are kept, and the "
                 "next fetch resumes it once space is freed" if kept else "nothing was kept")
        if exc.errno in _DISK_FULL:
            return TransferError(
                f"{name}: not enough disk space under {self.root} to go on writing {writing} "
                f"({exc.strerror or exc}); {stays}", outcome=BLOCKED)
        return TransferError(f"{name}: {writing} could not be written ({exc}); nothing was "
                             "retried", outcome=FAIL)

    def _transfer(self, url: str, name: str, part: Path, side: Path, total: int | None,
                  offset: int, validator: str | None, checksum: str | None,
                  max_bytes: int | None, restarts: list[str]) -> dict[str, Any]:
        """The GETs of one file, until its body is whole on disk."""
        ident: dict[str, Any] = read_sidecar(side) or {}
        if offset and ident.get("total") and not total:
            total = int(ident["total"])
        joins: list[int] = []
        attempt, waited = 0, 0.0
        wrong_range_restarted = restarted_416 = False
        while True:
            attempt += 1
            self._stop_if_cancelled(name, part)
            have = offset
            headers = {"User-Agent": _UA}
            if have:
                headers["Range"] = f"bytes={have}-"
                if validator:
                    headers["If-Range"] = validator
            self._pace()
            try:
                resp = self._open(urllib.request.Request(url, headers=headers))
            except _RedirectRefused as exc:
                raise DownloadError(f"{name}: {exc}") from exc       # not a transient
            except urllib.error.HTTPError as exc:
                if exc.code == 416 and have:
                    # RFC 9110 §13.1.5: If-Range is evaluated before Range, so a 416 means
                    # the validator matched (or a reviewed checksum will check the file):
                    # one that names this very size says the partial file is the whole file
                    whole = _unsatisfied_total(exc.headers.get("Content-Range")
                                               if exc.headers else None)
                    if whole == have and (validator or checksum) \
                            and not (exc.headers and self._other_file(ident, exc.headers)):
                        self._log(f"{name}: the server says the partial file is already "
                                  f"whole ({have} bytes); it is checked as it is")
                        return {"ident": ident, "joins": [], "attempts": attempt,
                                "total": whole, "whole_at": have}
                if exc.code == 416 and not restarted_416:
                    restarted_416 = True
                    restarts.append(f"HTTP 416 for bytes={have}-: the partial file was dropped "
                                    "and the transfer restarted")
                    self._log(f"{name}: {restarts[-1]}")
                    self._discard(part, side)
                    offset, validator, ident = 0, None, {}
                    joins.clear()
                    continue
                delay = self._retry_delay(exc, attempt, waited)
                if delay is None:
                    raise self._http_error(name, url, exc) from exc
                self._log(f"{name}: HTTP {exc.code}; retry {attempt} in {delay:.0f} s")
                self._sleep(delay)
                waited += delay
                continue
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                delay = self._retry_delay(exc, attempt, waited)
                if delay is None:
                    raise DownloadError(f"{name}: {type(exc).__name__}: {exc}") from exc
                self._log(f"{name}: retry {attempt} after {type(exc).__name__}, in {delay:.0f} s")
                self._sleep(delay)
                waited += delay
                continue
            with resp:
                status = getattr(resp, "status", None) or 200
                h = resp.headers
                if status == 206:
                    cr = _content_range(h.get("Content-Range"))
                    expect = total if total is not None else (cr[2] if cr else None)
                    ok = (cr is not None and cr[2] is not None and cr[0] == have
                          and cr[1] == cr[2] - 1 and expect == cr[2])
                    other = self._other_file(ident, h) if ok and have else ""
                    if not ok or other:
                        shown = h.get("Content-Range")
                        if wrong_range_restarted:
                            self._discard(part, side)
                            raise TransferError(
                                f"{name}: the server answered bytes={have}- "
                                + (f"from another file again ({other})" if other else
                                   f"with Content-Range {shown!r} again") + "; nothing was kept",
                                outcome=FAIL)
                        wrong_range_restarted = True
                        if other:
                            why = (f"the server answered bytes={have}- from another file "
                                   f"({other}; it ignored If-Range)")
                        else:
                            want = f"bytes {have}-{expect - 1}/{expect}" if expect else \
                                f"bytes {have}-<last>/<total>"
                            why = (f"the server answered bytes={have}- with Content-Range "
                                   f"{shown!r}, not {want}")
                        restarts.append(f"{why}: the partial file was dropped and the "
                                        "transfer restarted")
                        self._log(f"{name}: {restarts[-1]}")
                        self._discard(part, side)
                        offset, validator, ident = 0, None, {}
                        joins.clear()
                        continue
                    total = cr[2]
                    mode = "ab" if have else "wb"
                    if have:
                        joins.append(have)
                else:
                    if have:
                        restarts.append(f"the server sent the whole file for bytes={have}- "
                                        "(If-Range did not match, or it serves no ranges): "
                                        "restarted from byte 0")
                        self._log(f"{name}: {restarts[-1]}")
                        joins.clear()
                    have, mode = 0, "wb"
                    cl = h.get("Content-Length")
                    total = int(cl) if cl and str(cl).isdigit() else None
                if mode == "wb":
                    ident = identity_of(url, h, total, final_url=getattr(resp, "url", None))
                    try:
                        _write_json(side, ident)
                    except OSError as exc:
                        self._discard(part, side)
                        raise self._local_error(name, exc, 0, writing="the file's identity "
                                                "record", kept=False) from exc
                try:
                    done = self._stream_body(resp, name, part, side, mode, have, total,
                                             max_bytes)
                except _Interrupted as exc:
                    done = part.stat().st_size if part.exists() else 0
                    cause: BaseException | None = exc.cause
                    if total is not None and done == total:
                        cause = None           # every byte arrived; the connection failed after
                else:
                    cause = None
                    if total is not None and done < total:
                        cause = http.client.IncompleteRead(b"", total - done)
            if cause is None:
                if total is not None and done > total:
                    self._discard(part, side)
                    raise TransferError(f"{name}: the server sent {done} bytes of a "
                                        f"{total}-byte file; nothing was kept", outcome=FAIL)
                return {"ident": ident, "joins": joins, "attempts": attempt, "total": total}
            delay = self._retry_delay(cause, attempt, waited)
            if delay is None:
                raise TransferError(
                    f"{name}: the transfer broke off at {done} bytes on attempt {attempt} "
                    f"({type(cause).__name__}: {cause}); the partial file is kept for a later "
                    "resume", outcome=BLOCKED) from cause
            self._log(f"{name}: the transfer broke off at {done} bytes "
                      f"({type(cause).__name__}); retry {attempt} in {delay:.0f} s")
            self._sleep(delay)
            waited += delay
            offset, validator, whole = self._plan(name, part, side, url, {"size": total},
                                                  checksum, restarts)
            ident = read_sidecar(side) or {}
            if whole:
                return {"ident": ident, "joins": [], "attempts": attempt, "total": total,
                        "whole_at": offset}
            if not offset:
                joins.clear()

    @staticmethod
    def _other_file(ident: Mapping[str, Any], headers: Any) -> str:
        """Why a 206 for the right range came from another file than the partial one: its
        strong ETag is not the recorded one or, when none was recorded, its Last-Modified is
        not (a server that ignores If-Range answers a range of whatever it holds now). ''
        when nothing shows it."""
        now = _strong(headers.get("ETag"))
        if ident.get("etag") and now and now != ident["etag"]:
            return f"ETag {now}, not the recorded {ident['etag']}"
        lm = headers.get("Last-Modified")
        if not ident.get("etag") and ident.get("last_modified") and lm \
                and lm != ident["last_modified"]:
            return f"Last-Modified {lm}, not the recorded {ident['last_modified']}"
        return ""

    def _stream_body(self, resp: Any, name: str, part: Path, side: Path, mode: str,
                     have: int, total: int | None, max_bytes: int | None) -> int:
        done = have
        next_log = done + (32 << 20)
        step = max(total // 10, 1) if total else None
        next_step = (done // step + 1) * step if step else None
        # one read of the socket per block, returned as soon as any byte is there: a cancel
        # is then seen after the next arrival, not after a whole ``chunk`` (a 1 MiB read
        # blocks until the whole MiB came, 11 s at 80 KiB/s)
        read = getattr(resp, "read1", None) or resp.read
        try:
            fh = open(part, mode)
        except OSError as exc:
            raise self._local_error(name, exc, have) from exc
        try:
            while not self.cancelled():
                try:
                    block = read(self.chunk)
                except (OSError, http.client.HTTPException) as exc:
                    raise _Interrupted(exc) from exc
                if not block:
                    break
                try:
                    self._write_block(fh, block)
                except OSError as exc:
                    raise _LocalFailure(exc) from exc
                done += len(block)
                if max_bytes is not None and done > max_bytes:
                    fh.close()
                    self._discard(part, side)
                    raise DownloadError(
                        f"{part.name}: aborted after {done / 1e9:.2f} GB, above the "
                        f"{max_bytes / 1e9:.2f} GB gate; call fetch(..., confirm=True) "
                        "to proceed")
                if self.progress is not None:
                    self.progress(name, done, total)
                if done >= next_log or (next_step is not None and done >= next_step):
                    shown = (f"{done / 1e6:.1f}/{total / 1e6:.1f} MB ({100 * done / total:.1f}%)"
                             if total else f"{done / 1e6:.1f} MB")
                    self._log(f"{name}: {shown}")
                    next_log = done + (32 << 20)
                    if next_step is not None:
                        next_step = (done // step + 1) * step
            try:
                fh.flush()
            except OSError as exc:
                raise _LocalFailure(exc) from exc
        except _LocalFailure as exc:
            with contextlib.suppress(OSError):
                fh.close()
            kept = part.stat().st_size if part.exists() else 0
            raise self._local_error(name, exc.cause, kept) from exc.cause
        finally:
            with contextlib.suppress(OSError):
                fh.close()
        self._stop_if_cancelled(name, part)
        return done

    @staticmethod
    def _write_block(fh: Any, block: bytes) -> None:
        """One block onto the partial file (a seam: tests make the disk full here)."""
        fh.write(block)

    def _inspect(self, part: Path, side: Path, name: str, fmt: str | None,
                 columns: tuple[str, ...], refuse_html: bool) -> dict[str, Any]:
        """The content checks a file must pass before it is kept."""
        checks: dict[str, Any] = {}
        status, detail, evidence = check_archive(part, name)
        if status != NOT_APPLICABLE:
            checks["decompression"] = {"status": status, "detail": detail, **evidence}
        if status == FAIL:
            self._discard(part, side)
            raise TransferError(f"{name}: the archive is not whole ({detail}); it was not kept",
                                outcome=FAIL, check="decompression")
        if refuse_html and looks_like_html(part):
            self._discard(part, side)
            raise TransferError(f"{name}: the server sent an HTML page (a login, challenge or "
                                "error page), not the file; it was not kept",
                                outcome=BLOCKED, check="html")
        status, detail, evidence = check_columns(part, name, fmt, columns)
        if status != NOT_APPLICABLE:
            checks["format"] = {"status": status, "detail": detail, **evidence}
        if status == FAIL:
            self._discard(part, side)
            raise TransferError(f"{name}: not the table its spec describes ({detail}); it "
                                "was not kept", outcome=FAIL, check="format")
        return checks

    @staticmethod
    def _discard(part: Path, side: Path) -> None:
        part.unlink(missing_ok=True)
        side.unlink(missing_ok=True)
