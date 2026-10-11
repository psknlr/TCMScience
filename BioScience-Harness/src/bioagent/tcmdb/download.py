"""The downloader the hub fetches its files with.

It is the shared downloader (``acquisition.downloader``: resume, the size gate, a file kept
only once its checksum or size verifies, an answer from another host refused) with two
things some hub files need:

* **A reviewed hand-off.** figshare answers a file's URL with a redirect to its S3 bucket,
  Hugging Face with one to its CDN under hf.co, and ClinPGx with one to s3.pgkb.org. The
  shared downloader refuses an answer that came from another host than the URL's. For a
  file whose spec names the hosts it is handed off to (``FileSpec.redirects``), an answer
  from one of those hosts is accepted, and recorded (``served_from``); an answer from any
  other host, or one that fell from https to http, is closed unread and refused, exactly
  as the shared downloader does. Every request for the file (the size probe, the download,
  a resumed range) asks the reviewed URL first, so a signed URL that expires is never
  reused.
* **A body cut off mid-transfer** (``http.client.IncompleteRead``: EMA's generated reports
  end early now and then) is retried like a reset connection, resuming from the bytes
  already written when the server's size is known.

Both belong in the shared downloader; it is in the import closure of the pinned governed
skills, so changing it waits on a maintainer's re-pin, and the hub carries them here.
"""

from __future__ import annotations

import http.client
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from ..acquisition.downloader import (PACER, DownloadError, DownloadResult, Downloader,
                                      _Paced, _RedirectRefused)

__all__ = ["HubDownloader", "handed_off"]


def handed_off(host: str, allowed: Sequence[str]) -> bool:
    """Whether `host` is one a reviewed spec says a file is handed off to: the host
    itself, or, for an entry ``*.domain``, any host under that domain."""
    host = host.lower().rstrip(".")
    for entry in allowed:
        entry = entry.lower().strip()
        if entry.startswith("*."):
            if host.endswith(entry[1:]) and len(host) > len(entry) - 1:
                return True
        elif entry and host == entry:
            return True
    return False


class HubDownloader(Downloader):
    """``Downloader`` with the hand-offs a file's spec names, and cut-off bodies retried."""

    def __init__(self, root: Path | str, **kwargs: Any) -> None:
        super().__init__(root, **kwargs)
        self._handoff: tuple[str, ...] = ()
        #: the host that served the last file fetched, when it was handed off ("" if not)
        self.served_from = ""

    def fetch(self, url: str, name: str, *, redirects: Sequence[str] = (),
              **kwargs: Any) -> DownloadResult:
        """``Downloader.fetch``; `redirects` names the hosts the URL may hand the file off
        to (``FileSpec.redirects``)."""
        self._handoff, self.served_from = tuple(redirects), ""
        try:
            return super().fetch(url, name, **kwargs)
        finally:
            self._handoff = ()

    def _open(self, req: urllib.request.Request):
        """The shared downloader's ``_open`` (paced per host, the final URL checked), with
        an answer from a named hand-off host accepted."""
        if not self._handoff:
            return super()._open(req)
        parts = urllib.parse.urlsplit(req.full_url)
        host = parts.hostname or ""
        PACER.wait(host, self.rates)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout_s)  # noqa: S310
        except BaseException:
            PACER.done(host)                 # a refused request still counts
            raise
        final = urllib.parse.urlsplit(getattr(resp, "url", None) or req.full_url)
        landed = (final.hostname or "").lower()
        moved = landed != host.lower()
        elsewhere = moved and not handed_off(landed, self._handoff)
        if elsewhere or (parts.scheme == "https" and final.scheme != "https"):
            try:
                resp.close()
            finally:
                PACER.done(host)
            raise _RedirectRefused(
                f"redirected to {final.hostname or final.geturl()!r}, which is not a host "
                f"this call may contact ({host}; the spec names {', '.join(self._handoff)})"
                if elsewhere else f"redirected from https down to {final.scheme}")
        if moved and landed != self.served_from:
            self.served_from = landed
            self._log(f"{host} hands the file off to {landed}")
        return _Paced(resp, host)

    def _stream(self, url: str, part: Path, remote_size: int | None,
                *, max_bytes: int | None = None) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                return super()._stream(url, part, remote_size, max_bytes=max_bytes)
            except http.client.HTTPException as exc:
                # a body cut off mid-transfer: the next attempt resumes from the bytes
                # written so far when the server's size is known
                if attempt >= self.max_retries:
                    raise DownloadError(f"{part.name}: {type(exc).__name__}: {exc}") from exc
                self._log(f"{part.name}: retry {attempt} after {type(exc).__name__} "
                          f"({part.stat().st_size if part.exists() else 0} bytes kept)")
                time.sleep(min(10.0, 1.5 * 2 ** attempt))
