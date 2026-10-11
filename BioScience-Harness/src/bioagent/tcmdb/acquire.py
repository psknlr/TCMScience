"""How the hub acquires a file, and the record it keeps of every attempt.

A file reaches ``raw/<dataset>/`` one of two ways: downloaded from its reviewed URL, or
imported from a copy a person obtained (``TCMDataHub.import_raw``). Either way it is kept
only once verified: against its reviewed checksum when there is one, otherwise against its
declared size. Every attempt is appended to ``<root>/audit/downloads.jsonl``, kept or not:

    {"at": ..., "dataset": "npass", "file": "...", "url": "...", "host": "bidd.group",
     "route": "download" | "cache" | "import", "status": "PASS" | "FAIL" | "BLOCKED" |
     "UNVERIFIED", "verified": "checksum" | "size" | "none", "bytes": ..., "checksum": ...,
     "reviewed_checksum": ..., "reason": "...", "duration_s": ...}

``PASS`` is a file kept and verified; ``UNVERIFIED`` one kept with nothing to verify it
against; ``FAIL`` a file refused because it is not the reviewed one (wrong checksum or size,
a redirect elsewhere); ``BLOCKED`` an attempt something outside the code stopped (a network
policy, an unresolvable name, a full disk, a size that needs a person's confirmation).

A file that arrives with neither a reviewed checksum nor a size (a server that streams
it without announcing one) is still checked for being whole when its format allows
(``structure``): a zip's per-member CRC-32s, a gzip, bzip2 or xz stream's own checks, a
JSON or XML document that parses to its end. ``verified`` is then ``structure``; a file
that fails is incomplete and is not kept. Plain text and tables carry no such check and
stay unverified.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import lzma
import os
import shutil
import zipfile
import zlib
from pathlib import Path
from typing import Any, Mapping

from ..validation import BLOCKED, FAIL, utc_now

__all__ = ["AUDIT_FILE", "MANIFEST", "audit_path", "append_audit", "read_audit",
           "copy_verified", "disk_shortfall", "digest", "outcome_of_error", "read_manifest",
           "record_manifest", "structure", "verification"]

AUDIT_FILE = "downloads.jsonl"
#: the downloader's own record of what it fetched (``acquisition.downloader``)
MANIFEST = ".downloads.json"
#: room kept free beyond a file's size, for the store a build writes next to it
_HEADROOM = 64 << 20


def audit_path(root: str | Path) -> Path:
    return Path(root) / "audit" / AUDIT_FILE


def append_audit(root: str | Path, entry: Mapping[str, Any]) -> None:
    path = audit_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": utc_now(), **dict(entry)}, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_audit(root: str | Path, dataset: str | None = None) -> list[dict[str, Any]]:
    path = audit_path(root)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if dataset is None or entry.get("dataset") == dataset:
            out.append(entry)
    return out


def disk_shortfall(path: str | Path, need_bytes: int | None) -> str | None:
    """Why ``need_bytes`` cannot be written under ``path``, or None when it fits."""
    if not need_bytes:
        return None
    from ..validation.environment import disk_free

    free = disk_free(path)
    if free is None or free >= need_bytes + _HEADROOM:
        return None
    return (f"not enough disk space under {path}: the file needs {need_bytes / 1e6:.1f} MB "
            f"(plus {_HEADROOM >> 20} MB headroom) and {free / 1e6:.1f} MB is free; nothing was "
            "started")


def digest(path: str | Path, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return f"{algorithm}:{h.hexdigest()}"


def outcome_of_error(exc: BaseException, host: str) -> tuple[str, str]:
    """``(status, reason)`` for an exception a download raised."""
    from ..acquisition.downloader import DownloadError
    from ..validation.network import classify

    text = str(exc)
    if isinstance(exc, DownloadError):
        low = text.lower()
        if "network access denied" in low:
            return BLOCKED, (f"the network policy here denies {host} ({text}); nothing was "
                             "retried around it")
        if "checksum mismatch" in low:
            return FAIL, (f"{text}: the file is not the reviewed one (the upstream file "
                          "changed, or the transfer was corrupted); it was not kept")
        if "!= expected" in low or "server-reported" in low:
            return FAIL, f"{text}: the size is not the reviewed one; it was not kept"
        if "gate" in low:
            return BLOCKED, f"{text} (a download this large needs a person's confirmation)"
        if "redirected" in low:
            return FAIL, f"{text}: the reviewed URL needs review"
        if exc.__cause__ is not None:
            return classify(exc.__cause__, host)
        return FAIL, text
    if isinstance(exc, (PermissionError, IsADirectoryError, NotADirectoryError,
                        FileNotFoundError)):
        return FAIL, f"{type(exc).__name__}: {text}"
    return classify(exc, host)


def read_manifest(raw_dir: str | Path) -> dict[str, Any]:
    path = Path(raw_dir) / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_manifest(raw_dir: str | Path, name: str, entry: Mapping[str, Any]) -> None:
    """Add ``name`` to the downloader's manifest, the way the downloader records a fetch."""
    raw = Path(raw_dir)
    data = read_manifest(raw)
    data[name] = dict(entry)
    tmp = raw / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, raw / MANIFEST)


def verification(raw_dir: str | Path, name: str, reviewed: str | None) -> str:
    """How the file ``name`` in ``raw_dir`` was verified when it was kept: ``checksum`` (it
    hashed to the reviewed checksum), ``mismatch`` (it was recorded with another checksum
    of the same algorithm: not the reviewed file), ``size``, or ``unverified`` (no record:
    copied in by hand)."""
    rec = read_manifest(raw_dir).get(name) or {}
    if reviewed and str(rec.get("checksum", "")).lower() == reviewed.lower():
        return "checksum"
    if reviewed and str(rec.get("checksum", "")).split(":", 1)[0] == reviewed.split(":", 1)[0]:
        return "mismatch"
    if rec.get("verified"):
        return "structure" if rec.get("verified_by") == "structure" else "size"
    return "unverified"


def structure(path: str | Path) -> tuple[bool | None, str]:
    """Whether a file with nothing else to verify it against is whole, by its format's own
    checks: ``(True, how)``, ``(False, why)`` for an incomplete or corrupt file, or
    ``(None, "")`` for a format that carries no such check (plain text, a table)."""
    import xml.etree.ElementTree as ET

    path = Path(path)
    with open(path, "rb") as fh:
        head = fh.read(64)
    lead = head.lstrip()[:1]
    try:
        if head[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
            return ((True, "every member of the zip matches its CRC-32") if bad is None
                    else (False, f"the zip member {bad} does not match its CRC-32"))
        for magic, opener, kind in ((b"\x1f\x8b", gzip.open, "gzip"),
                                    (b"BZh", bz2.open, "bzip2"),
                                    (b"\xfd7zXZ\x00", lzma.open, "xz")):
            if head.startswith(magic):
                with opener(path, "rb") as fh:
                    while fh.read(1 << 20):
                        pass
                return True, f"the {kind} stream ends with its check intact"
        if lead in (b"{", b"[") or path.suffix.lower() == ".json":
            with open(path, "rb") as fh:
                json.load(fh)
            return True, "it parses as one whole JSON document"
        if lead == b"<" or path.suffix.lower() == ".xml":
            for _event, el in ET.iterparse(path):
                el.clear()
            return True, "it parses as one whole XML document"
    except (zipfile.BadZipFile, EOFError, OSError, ValueError, zlib.error, lzma.LZMAError,
            ET.ParseError) as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
    return None, ""


def copy_verified(src: str | Path, dest: str | Path) -> None:
    """Copy ``src`` to ``dest`` so that ``dest`` never holds part of a file."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
