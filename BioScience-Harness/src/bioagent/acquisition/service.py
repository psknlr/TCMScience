"""Dataset acquisition: a registered dataset's files, fetched, verified, recorded and reported.

``acquire_dataset(hub, key)`` is what ``bioagent tcmdb fetch`` and Studio's ``tcmdb.fetch``
job run. A dataset is named by its key in the hub's registry (``tcmdb.datasets``); its files
come from the reviewed URLs there, never from a URL a caller supplies. For each file:

* a copy already held elsewhere on this machine (``mirrors``: the research snapshot
  pipeline's raw directory, or the hub's, for the same file) is adopted only when it hashes
  to the reviewed checksum, as a hard link where the file system allows (one copy on disk,
  never written in place by either side) or else as a copy, and recorded with the route
  ``local-mirror``; a file without a reviewed checksum is never adopted, since its name and
  size do not show it is the reviewed file;
* otherwise it goes through the hub's own ``_fetch_file`` with an
  ``acquisition.transfer.VerifiedTransfer``: the hub's disk check on the declared size, its
  HTML refusal, its learning of a host the network policy refuses and its append-only audit
  (``audit/downloads.jsonl``) all apply unchanged, and the transfer adds a resume only onto
  the same file, retries only for what is transient, a lock per file, the server's size in
  the disk check, archives read through their CRC before a file is kept, and the transfer's
  record (ETag, Last-Modified, final URL, server, retrieval time, release, resume offset).

``acquisition_state`` reads back, from the files on disk, their records and the audit, what
an acquisition has done so far: per file ``pending``, ``transferring``, ``kept``,
``refused`` (it never arrived: a policy, a name that does not resolve, a full disk, a 404)
or ``rejected`` (it arrived and a check refused it: checksum, size, decompression, format or
an HTML page), the bytes on disk against the bytes expected, and how the kept files were
verified. Studio's job progress and its ``/api/datasets/acquisitions`` routes read it.

Nothing here stores data anywhere but the runner's own directories, and nothing is
redistributed: a file is downloaded into the user's data directory and stays there.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import stat
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..validation import BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, aggregate, utc_now
from .transfer import (LOCK_DIR, TransferCancelled, VerifiedTransfer, _digests, exclusive,
                       read_sidecar, record_entry)

__all__ = ["SCHEMA", "FILE_STATES", "SIZE_GATE_BYTES", "CANCEL_GRACE_S", "acquire_dataset",
           "acquisition_state", "adopt_verified", "cancel_on_signals", "plan", "release_of",
           "wanted_files"]

SCHEMA = "bioagent.acquisition/1"
FILE_STATES = ("pending", "transferring", "kept", "refused", "rejected")
#: the downloader's size gate; Studio never confirms a file above it
SIZE_GATE_BYTES = 512 * 1024 * 1024
_VERIFIED = {"checksum": "checksum", "size": "size", "unverified": "none", "mismatch": "mismatch"}


def release_of(version: str | None) -> str | None:
    """The release a spec's version names, or None for a rolling one ('current', 'latest'):
    a rolling file is identified by its ETag, Last-Modified and checksum instead."""
    v = (version or "").strip()
    return None if not v or v.lower() in ("current", "latest") else v


def _spec(key: str) -> Any:
    # read through the hub module, so a test that swaps the hub's registry swaps this too
    from ..tcmdb import hub as hub_module

    return hub_module.dataset(key)


def wanted_files(spec: Any, include_optional: bool = False) -> list[Any]:
    """The dataset's files a fetch asks for, each file name once (a file several tables are
    read from is downloaded once)."""
    seen: set[str] = set()
    out = []
    for f in spec.files:
        if (include_optional or not f.optional) and f.name not in seen:
            seen.add(f.name)
            out.append(f)
    return out


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


def plan(hub: Any, key: str, *, include_optional: bool = False) -> dict[str, Any]:
    """What fetching ``key`` would ask for: its files, which are here, the bytes still to
    download (None when a file's size is not declared) and the hosts it would contact."""
    spec = _spec(key)
    raw = hub.raw_dir(key)
    rows = [{"file": f.name, "url": f.url, "host": _host(f.url),
             "expected_bytes": f.expected_bytes, "reviewed_checksum": f.checksum,
             "optional": f.optional, "present": (raw / f.name).is_file()}
            for f in wanted_files(spec, include_optional)]
    need = [r for r in rows if not r["present"]]
    known = [r["expected_bytes"] for r in need]
    return {"dataset": key, "version": spec.version or None, "access": spec.access,
            "files": rows,
            "download_bytes": sum(b for b in known if b) if all(b is not None for b in known)
            else None,
            "unknown_sizes": sum(1 for b in known if b is None),
            "hosts": sorted({r["host"] for r in need if r["host"]}),
            "size_gate_bytes": SIZE_GATE_BYTES}


def adopt_verified(mirrors: Iterable[str | Path], raw: str | Path, name: str,
                   checksum: str | None, *, url: str | None = None,
                   release: str | None = None, log: Callable[[str], None] = lambda m: None
                   ) -> dict[str, Any] | None:
    """Put ``name`` into ``raw`` from the first mirror directory holding a copy that hashes
    to the reviewed ``checksum``: a hard link (the same bytes, stored once) or, across file
    systems, a copy hashed again. None when there is no reviewed checksum, no such copy, the
    file is here already, another transfer of it holds its lock, or the disk refused the
    link, the copy or its record (``ENOSPC``, ``EACCES``): the caller then downloads it as
    if no copy were here. Both sides only ever replace a file by renaming a new one over
    it, so a hard link is never written through. An adopted file leaves no partial download
    of it behind (``<name>.part`` and its identity record)."""
    if not checksum:
        return None
    raw = Path(raw)
    dest = raw / name
    if dest.exists():
        return None
    algorithm = checksum.split(":", 1)[0].lower()
    for d in mirrors or ():
        src = Path(d) / name
        try:
            if not src.is_file() or (dest.exists() and src.samefile(dest)):
                continue
        except OSError:
            continue
        tmp = raw / f".{name}.adopt.{os.getpid()}"
        try:
            raw.mkdir(parents=True, exist_ok=True)
            with exclusive(raw / LOCK_DIR / f"{name}.lock", wait=False) as held:
                if not held:
                    return None
                tmp.unlink(missing_ok=True)
                try:
                    os.link(src, tmp)
                    how = "hard link"
                except OSError:
                    shutil.copyfile(src, tmp)
                    how = "copy"
                # the bytes kept are the ones hashed: the link (or copy) is checked, not the
                # mirror's name, which its owner may have replaced meanwhile
                sums = _digests(tmp, ("sha256", algorithm))
                if sums[algorithm].lower() != checksum.lower():
                    log(f"{name}: the copy in {d} is not the reviewed file; not adopted")
                    continue
                from ..tcmdb.acquire import read_manifest
                theirs = read_manifest(src.parent).get(name) or {}
                size = tmp.stat().st_size
                entry = {"url": url or theirs.get("url"), "bytes": size,
                         "checksum": sums[algorithm], "verified": True,
                         "fetched_at": time.time(), "route": "local-mirror",
                         "sha256": sums["sha256"], "mirror": str(src), "adopted_as": how,
                         "retrieved_at": theirs.get("retrieved_at") or utc_now(),
                         **{k: theirs[k] for k in ("etag", "last_modified", "final_url",
                                                   "server") if theirs.get(k)}}
                if release:
                    entry["release"] = release
                os.replace(tmp, dest)
                try:
                    record_entry(raw, name, entry)
                except OSError:
                    dest.unlink(missing_ok=True)   # never a kept file without its record
                    raise
                # a partial download of the same file is of no further use
                (raw / f"{name}.part").unlink(missing_ok=True)
                (raw / f"{name}.part.json").unlink(missing_ok=True)
        except OSError as exc:
            log(f"{name}: the copy in {d} could not be adopted ({type(exc).__name__}: {exc}); "
                "it is downloaded instead")
            continue
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
        log(f"{name}: adopted from {d} ({how}); it hashes to the reviewed {algorithm}")
        return {"file": name, "bytes": size, "checksum": sums[algorithm],
                "sha256": sums["sha256"], "route": "local-mirror", "mirror": str(src),
                "adopted_as": how}
    return None


#: how long a cancelled process may take to stop by itself before it is ended
CANCEL_GRACE_S = 10.0


def cancel_on_signals(signals: Sequence[int] = (signal.SIGTERM, signal.SIGINT), *,
                      grace_s: float = CANCEL_GRACE_S,
                      end_process: Callable[[int], Any] = os._exit) -> threading.Event:
    """An event that SIGTERM (a job's cancel) or SIGINT sets: the transfer then stops at the
    next block that arrives, closes its partial file and keeps it for a verified resume.

    The signal also arms a watchdog: a process still running ``grace_s`` seconds later (a
    read the server never answers holds until the transfer's timeout, and Studio's job
    supervisor, gone at SIGTERM, never escalates to SIGKILL) is ended with status 130.
    What it leaves is safe to resume: the partial file is a prefix of the body (writes are
    in order) beside its identity record, and its locks are released with the process. Only
    the main thread can install handlers; elsewhere the event is returned unwired."""
    event = threading.Event()
    armed: list[threading.Timer] = []

    def handler(signum: int, frame: Any) -> None:
        del signum, frame
        event.set()
        if not armed:
            timer = threading.Timer(grace_s, end_process, (130,))
            timer.daemon = True
            armed.append(timer)
            timer.start()

    for s in signals:
        try:
            signal.signal(s, handler)
        except (ValueError, OSError):
            pass
    return event


def acquire_dataset(hub: Any, key: str, *, include_optional: bool = False,
                    confirm: bool = False, log: Callable[[str], None] = print,
                    progress: Callable[[str, int, int | None], None] | None = None,
                    cancel: threading.Event | Callable[[], bool] | None = None,
                    mirrors: Iterable[str | Path] = (),
                    report_path: str | Path | None = None) -> dict[str, Any]:
    """Fetch ``key``'s files into the hub (module docstring) and return the report
    (``bioagent.acquisition/1``): per file what ``TCMDataHub._fetch_file`` returns (status,
    reason, verified, bytes, checksum, and how it was obtained), the overall status, and
    whether it was cancelled. Manual and live datasets raise ``HubError`` with what a person
    does instead. ``confirm`` lifts the size gate; Studio never passes it."""
    from ..tcmdb import HubError
    from ..tcmdb import acquire

    spec = _spec(key)
    if spec.access == "manual":
        raise HubError(f"{spec.name} cannot be downloaded by a program. "
                       f"{spec.instructions} Directory: {hub.raw_dir(key)}")
    if spec.access == "live":
        raise HubError(f"{spec.name} is filled per entity: use hub.enrich(<name>) or "
                       f"hub.enrich_{'symmap' if key == 'symmap_api' else 'herb'}([...])")
    raw = hub.raw_dir(key)
    raw.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    dl = VerifiedTransfer(raw, timeout_s=120, log=log, min_interval_s=spec.min_interval_s,
                          progress=progress, cancel=cancel)
    mirrors = [Path(m) for m in mirrors or () if m]
    refused: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    cancelled = False
    for f in wanted_files(spec, include_optional):
        if cancelled or dl.cancelled():
            cancelled = True
            results.append({"file": f.name, "ok": False, "status": SKIPPED,
                            "reason": "not started: the acquisition was cancelled"})
            continue
        t0 = time.perf_counter()
        adopted = adopt_verified(mirrors, raw, f.name, f.checksum, url=f.url,
                                 release=release_of(spec.version), log=log) if mirrors else None
        if adopted is not None:
            reason = (f"adopted from {adopted['mirror']} ({adopted['adopted_as']}): it hashes "
                      f"to the reviewed {f.checksum.split(':')[0]}")
            acquire.append_audit(hub.root, {
                "dataset": key, "file": f.name, "url": f.url, "host": _host(f.url),
                "route": "local-mirror", "status": PASS, "verified": "checksum",
                "bytes": adopted["bytes"], "checksum": adopted["checksum"],
                "sha256": adopted["sha256"], "reviewed_checksum": f.checksum,
                "mirror": adopted["mirror"], "release": release_of(spec.version),
                "reason": reason, "duration_s": round(time.perf_counter() - t0, 3)})
            results.append({"file": f.name, "bytes": adopted["bytes"],
                            "checksum": adopted["checksum"], "cached": False, "ok": True,
                            "verified": "checksum", "route": "local-mirror", "status": PASS,
                            "reason": reason})
            continue
        try:
            results.append(hub._fetch_file(key, f, dl, refused, confirm=confirm, log=log,
                                           release=release_of(spec.version)))
        except TransferCancelled as exc:
            cancelled = True
            results.append({"file": f.name, "ok": False, "status": SKIPPED, "reason": str(exc),
                            "error": str(exc)[:300], "have_bytes": exc.have})
    kept = [r for r in results if r.get("ok")]
    doc: dict[str, Any] = {
        "schema": SCHEMA, "dataset": key, "name": spec.name, "version": spec.version or None,
        "root": str(hub.root), "started_at": started, "finished_at": utc_now(),
        "status": aggregate(r["status"] for r in results) if results else NOT_APPLICABLE,
        "cancelled": cancelled, "files": results,
        "kept": len(kept), "bytes": sum(int(r.get("bytes") or 0) for r in kept),
        "verified": {v: sum(1 for r in kept if r.get("verified") == v)
                     for v in ("checksum", "size", "none")}}
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    return doc


def _size(path: Path) -> int | None:
    """A regular file's size, or None when it is not there (now)."""
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def acquisition_state(hub: Any, key: str, *, include_optional: bool = False,
                      since: str | None = None, running: bool = False,
                      audit: bool = True) -> dict[str, Any]:
    """What an acquisition of ``key`` has done, read from the disk (module docstring).

    ``since`` (ISO UTC, as the audit writes it) limits the audit entries read to one
    acquisition's; ``running`` says whether its process is still running (a partial file is
    then ``transferring``, otherwise ``pending``: kept for a later resume). ``audit=False``
    skips the audit log (a progress poll needs only the files). ``progress`` is the shape a
    job's progress takes: ``{fraction, bytes:{done,total}, files:{done,total},
    verified:{checksum,size,none}, stage:"transferring"|"verifying"}``; the fraction comes
    from the bytes on disk when every file's size is known (declared, or said by the server
    when its transfer started), from the files otherwise."""
    from ..tcmdb import acquire

    spec = _spec(key)
    raw = hub.raw_dir(key)
    manifest = acquire.read_manifest(raw)
    last: dict[str, dict[str, Any]] = {}
    if audit:
        for e in acquire.read_audit(hub.root, key):
            if since and str(e.get("at") or "") < since:
                continue
            last[str(e.get("file"))] = e
    rows: list[dict[str, Any]] = []
    for f in wanted_files(spec, include_optional):
        final, part = raw / f.name, raw / (f.name + ".part")
        side = read_sidecar(raw / (f.name + ".part.json")) or {}
        rec = manifest.get(f.name) or {}
        row: dict[str, Any] = {"file": f.name, "url": f.url, "host": _host(f.url),
                               "optional": f.optional, "expected_bytes": f.expected_bytes,
                               "reviewed_checksum": f.checksum}
        e = last.get(f.name)
        # a running transfer renames its partial file over the final name at any moment:
        # each name is read once, and a file gone between the two reads is read as kept
        part_have = _size(part)
        have_final = _size(final)
        if have_final is None and part_have is not None and not part.exists():
            have_final = _size(final)
            part_have = None
        if have_final is not None:
            have = have_final
            row.update(state="kept", have_bytes=have, total_bytes=have,
                       verification=acquire.verification_of(rec, f.checksum),
                       **{k: rec[k] for k in ("route", "retrieved_at", "resumed",
                                              "resumed_from", "release", "sha256")
                          if rec.get(k) is not None})
        elif part_have is not None:
            have = part_have
            total = f.expected_bytes or side.get("total")
            row.update(state="transferring" if running else "pending", have_bytes=have,
                       total_bytes=total,
                       resumable=bool(side.get("etag") or side.get("last_modified")
                                      or f.checksum))
            if running and total and have >= total:
                row["stage"] = "verifying"
        elif e is not None and e.get("status") in (FAIL, BLOCKED):
            row.update(state="rejected" if e.get("check") else "refused", have_bytes=0,
                       total_bytes=f.expected_bytes, status=e.get("status"),
                       reason=e.get("reason"))
            if e.get("check"):
                row["check"] = e["check"]
        else:
            row.update(state="pending", have_bytes=0, total_bytes=f.expected_bytes)
        if e is not None:
            row["last_attempt"] = {k: e.get(k) for k in ("at", "status", "route", "reason",
                                                         "check") if e.get(k) is not None}
        rows.append(row)
    totals = [r["total_bytes"] for r in rows]
    known = [t for t in totals if t is not None]
    done = sum(min(r["have_bytes"], r["total_bytes"]) if r["total_bytes"] is not None
               else r["have_bytes"] for r in rows)
    kept = [r for r in rows if r["state"] == "kept"]
    if rows and len(known) == len(rows) and sum(known):
        fraction = done / sum(known)
    elif rows:
        fraction = sum(1.0 if r["state"] == "kept" else
                       (min(1.0, r["have_bytes"] / r["total_bytes"]) if r["total_bytes"] else 0.0)
                       for r in rows) / len(rows)
    else:
        fraction = 0.0
    if running:
        fraction = min(fraction, 0.99)        # done only once the job has been collected
    verified = {"checksum": 0, "size": 0, "none": 0}
    for r in kept:
        v = _VERIFIED.get(r.get("verification") or "unverified", "none")
        verified[v] = verified.get(v, 0) + 1
    progress: dict[str, Any] = {
        "fraction": round(fraction, 3),
        "bytes": {"done": done, "total": sum(known) if known else None},
        "files": {"done": len(kept), "total": len(rows)},
        "verified": verified,
        "stage": "verifying" if any(r.get("stage") == "verifying" for r in rows)
        else "transferring"}
    if len(known) != len(rows):
        progress["bytes"]["unknown"] = len(rows) - len(known)
    return {"dataset": key, "version": spec.version or None, "files": rows,
            "progress": progress,
            "states": {s: sum(1 for r in rows if r["state"] == s) for s in FILE_STATES}}

