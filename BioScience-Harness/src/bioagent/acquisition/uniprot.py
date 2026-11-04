"""UniProt accession lists acquired page by page, checked as one release.

The research snapshots read ``uniprot_human_reviewed_accessions.txt``: UniProtKB/Swiss-Prot
human primary accessions, one per line, which UniProt serves from its ``stream`` endpoint in
one response. On 2026-11-04 the stream answered HTTP 503 twice through the production relay
while single records (P04637) still came back, and the standard snapshot build stopped at
its acquisition stage (audit, repair P1). The same query paged through the ``search``
endpoint (``size=500``, a ``Link: rel="next"`` cursor) returned 20,431 unique accessions in
41 pages from release 2026_03, the service's own total, P04637 among them, and restored the
Ge Gen Qin Lian Tang workflow in the audit environment.

:func:`paginated_accessions` is that equivalent query, with the checks a file assembled from
pages needs before anything is built from it:

* **one release** — every page reports the same ``X-UniProt-Release``; a release that
  changes part-way is two files spliced together, and the run fails;
* **completeness** — the unique accessions equal the ``X-Total-Results`` every page reports,
  and the last page has no next link;
* **no silent duplicates** — an accession served on two pages is counted (and kept once);
  the count is reported, and it never makes up for one that is missing;
* **every line an accession** — a page whose lines are not UniProt accessions (an error
  page, a challenge) fails the run, as does an empty page before the total is reached;
* **known accessions** — ``expect`` (P04637 by default) must be present;
* **file integrity** — the file is written whole to a temporary name and renamed into
  place; its SHA-256 and size go into the downloader's manifest (``route: paginated``) and
  a sidecar ``<file>.acquisition.json`` with the release, total, pages and checks. A later
  run of the **same release** must give the same digest, or it fails: the release did not
  change, so the content must not have.

Only ``rest.uniprot.org`` is contacted, over HTTPS, and a ``next`` link that leaves it is
refused. Pages are paced and a 429/5xx is retried with backoff, honouring ``Retry-After``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

__all__ = ["HOST", "PAGE_SIZE", "PaginationError", "PaginatedResult", "search_url",
           "paginated_accessions", "sidecar_path", "read_sidecar", "check_sidecar"]

HOST = "rest.uniprot.org"
#: UniProt's largest page.
PAGE_SIZE = 500
#: Accessions every human Swiss-Prot list holds: a list without them is not that list.
EXPECT = ("P04637",)
_ACCESSION = re.compile(r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})")
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')
SIDECAR_SUFFIX = ".acquisition.json"

#: ``fetch(url, timeout_s) -> (status, headers, body)``; headers are read case-insensitively.
Fetch = Callable[[str, float], "tuple[int, Mapping[str, str], bytes]"]


class PaginationError(RuntimeError):
    """The paged list could not be assembled into one verified file. ``check`` names the check
    that failed (``release``, ``completeness``, ``format``, ``expected``, ``same_release``,
    ``http``, ``host``) and ``outcome`` is its validation status."""

    def __init__(self, message: str, *, check: str, outcome: str = "FAIL") -> None:
        super().__init__(message)
        self.check = check
        self.outcome = outcome


@dataclass
class PaginatedResult:
    path: Path
    release: str
    release_date: str
    total: int
    pages: int
    unique: int
    duplicates: int
    bytes: int
    sha256: str
    query: str
    retrieved_at: str
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return {"route": "paginated", "release": self.release, "release_date": self.release_date,
                "total": self.total, "pages": self.pages, "unique": self.unique,
                "duplicates": self.duplicates, "bytes": self.bytes,
                "checksum": f"sha256:{self.sha256}", "query": self.query,
                "retrieved_at": self.retrieved_at, "checks": self.checks}


def search_url(stream_url: str, *, size: int = PAGE_SIZE) -> str:
    """The paged ``search`` request equivalent to a ``stream`` URL: the same query and format,
    one page of ``size``. Only a UniProtKB stream URL on rest.uniprot.org is accepted."""
    parts = urllib.parse.urlsplit(stream_url)
    if parts.scheme != "https" or parts.hostname != HOST or not parts.path.endswith("/stream"):
        raise PaginationError(f"{stream_url} is not a UniProt stream URL on {HOST}", check="host")
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k not in ("size", "cursor")] + [("size", str(size))]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path[: -len("stream")]
                                    + "search", urllib.parse.urlencode(query), ""))


def _header(headers: Mapping[str, str], name: str) -> str:
    for k, v in headers.items():
        if k.lower() == name.lower():
            return str(v)
    return ""


def _default_fetch(url: str, timeout_s: float) -> tuple[int, Mapping[str, str], bytes]:
    from ..backends.http import _GuardedRedirects, user_agent

    opener = urllib.request.build_opener(_GuardedRedirects(frozenset({HOST}), "https"))
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(),
                                               "Accept": "text/plain"})
    try:
        with opener.open(req, timeout=timeout_s) as resp:  # noqa: S310 - host checked above
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read() or b""


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = _header(headers, "Retry-After")
    try:
        return max(0.0, float(raw)) if raw else None
    except ValueError:
        return None


def _page(fetch: Fetch, url: str, *, timeout_s: float, retries: int, max_wait_s: float,
          sleep: Callable[[float], None], log: Callable[[str], None]
          ) -> tuple[Mapping[str, str], bytes]:
    for attempt in range(1, retries + 1):
        try:
            status, headers, body = fetch(url, timeout_s)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == retries:
                raise PaginationError(f"{url}: {type(exc).__name__}: {exc}", check="http",
                                      outcome="BLOCKED") from exc
            sleep(min(30.0, 2.0 ** attempt))
            continue
        if 200 <= status < 300:
            return headers, body
        if status in (429, 500, 502, 503, 504) and attempt < retries:
            asked = _retry_after(headers)
            if asked is not None and asked > max_wait_s:
                raise PaginationError(f"{url}: HTTP {status}; the server asked to retry after "
                                      f"{asked:.0f}s", check="http", outcome="BLOCKED")
            wait = max(min(30.0, 2.0 ** attempt), asked or 0.0)
            log(f"uniprot: HTTP {status}, retrying in {wait:.0f}s")
            sleep(wait)
            continue
        raise PaginationError(f"{url}: HTTP {status}: {body[:200].decode('utf-8', 'replace')}",
                              check="http", outcome="FAIL" if 400 <= status < 500 else "BLOCKED")
    raise PaginationError(f"{url}: exhausted {retries} attempts", check="http", outcome="BLOCKED")


def sidecar_path(path: str | os.PathLike[str]) -> Path:
    p = Path(path)
    return p.with_name(p.name + SIDECAR_SUFFIX)


def read_sidecar(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    try:
        return json.loads(sidecar_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_sidecar(path: str | os.PathLike[str]) -> tuple[str, str] | None:
    """For a file assembled from pages: ``("PASS", why)`` when it still hashes to what was
    recorded, ``("FAIL", why)`` when it does not; ``None`` for a file with no sidecar."""
    rec = read_sidecar(path)
    if not rec:
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    want = str(rec.get("checksum") or "").removeprefix("sha256:")
    if h.hexdigest() != want:
        return "FAIL", (f"{Path(path).name} no longer hashes to the {want[:12]}… recorded when it "
                        f"was assembled from {rec.get('pages')} UniProt pages: it changed since")
    return "PASS", (f"assembled from {rec.get('pages')} pages of UniProt release "
                    f"{rec.get('release')}: {rec.get('unique')} unique accessions = the service's "
                    f"total, {rec.get('duplicates')} duplicate(s) dropped, sha256 {want[:12]}…")


def paginated_accessions(stream_url: str, dest_dir: str | os.PathLike[str], filename: str, *,
                         fetch: Fetch | None = None, size: int = PAGE_SIZE,
                         expect: Iterable[str] = EXPECT, timeout_s: float = 60.0,
                         retries: int = 4, max_wait_s: float = 120.0, min_interval_s: float = 0.2,
                         sleep: Callable[[float], None] = time.sleep,
                         log: Callable[[str], None] = print) -> PaginatedResult:
    """Fetch every page of the ``search`` query equivalent to ``stream_url`` and keep the list
    as ``dest_dir/filename`` only when every check passes (module docstring). Raises
    :class:`PaginationError` naming the failed check; nothing is written then."""
    fetch = fetch or _default_fetch
    url: str | None = search_url(stream_url, size=size)
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)).get("query", "")
    release = release_date = ""
    totals: set[int] = set()
    seen: dict[str, None] = {}
    duplicates = pages = 0
    t_last = 0.0
    while url:
        target = urllib.parse.urlsplit(url)
        if target.scheme != "https" or target.hostname != HOST:
            raise PaginationError(f"a next link left {HOST}: {url}", check="host")
        wait = min_interval_s - (time.monotonic() - t_last)
        if wait > 0:
            sleep(wait)
        headers, body = _page(fetch, url, timeout_s=timeout_s, retries=retries,
                              max_wait_s=max_wait_s, sleep=sleep, log=log)
        t_last = time.monotonic()
        pages += 1
        page_release = _header(headers, "X-UniProt-Release")
        if not page_release:
            raise PaginationError(f"page {pages} carries no X-UniProt-Release", check="release")
        if release and page_release != release:
            raise PaginationError(f"the release changed while paging: page 1 is {release}, page "
                                  f"{pages} is {page_release}; run again once the release has "
                                  "settled", check="release")
        release = page_release
        release_date = release_date or _header(headers, "X-UniProt-Release-Date")
        total_text = _header(headers, "X-Total-Results")
        if not total_text.isdigit():
            raise PaginationError(f"page {pages} carries no X-Total-Results", check="completeness")
        totals.add(int(total_text))
        text = body.decode("utf-8", "replace")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        bad = [ln for ln in lines if not _ACCESSION.fullmatch(ln)]
        if bad:
            raise PaginationError(f"page {pages} holds {len(bad)} line(s) that are not UniProt "
                                  f"accessions (first: {bad[0][:60]!r}); an error page is not a "
                                  "list", check="format")
        nxt = _NEXT.search(_header(headers, "Link"))
        if not lines and nxt:
            raise PaginationError(f"page {pages} is empty but names a next page", check="completeness")
        for acc in lines:
            if acc in seen:
                duplicates += 1
            else:
                seen[acc] = None
        if pages % 10 == 0:
            log(f"uniprot: {pages} pages, {len(seen)} accessions (release {release})")
        url = nxt.group(1) if nxt else None
    if len(totals) != 1:
        raise PaginationError(f"the pages reported different totals {sorted(totals)}",
                              check="completeness")
    total = totals.pop()
    if len(seen) != total:
        raise PaginationError(f"{len(seen)} unique accessions in {pages} pages, where UniProt "
                              f"release {release} reports {total}: the list is incomplete",
                              check="completeness")
    absent = [a for a in expect if a not in seen]
    if absent:
        raise PaginationError(f"known accession(s) {absent} are missing from the list",
                              check="expected")
    data = ("\n".join(seen) + "\n").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    final = dest / filename
    earlier = read_sidecar(final) if final.is_file() else None
    if earlier and earlier.get("release") == release \
            and str(earlier.get("checksum")) != f"sha256:{digest}":
        raise PaginationError(f"release {release} was assembled before with sha256 "
                              f"{str(earlier.get('checksum'))[7:19]}…; this run gives "
                              f"{digest[:12]}… for the same release", check="same_release")
    tmp = dest / f".{filename}.paginated.part"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    checks = {
        "release": {"status": "PASS", "detail": f"all {pages} pages report release {release}"},
        "completeness": {"status": "PASS",
                         "detail": f"{len(seen)} unique accessions = X-Total-Results {total}"},
        "duplicates": {"status": "PASS", "detail": f"{duplicates} duplicate(s) across pages, "
                                                   "each kept once", "count": duplicates},
        "format": {"status": "PASS", "detail": "every line is a UniProt accession"},
        "expected": {"status": "PASS", "detail": f"{', '.join(expect) or 'none named'} present"},
        "same_release": {"status": "PASS" if earlier and earlier.get("release") == release
                         else "NOT_APPLICABLE",
                         "detail": "the same digest as the earlier assembly of this release"
                         if earlier and earlier.get("release") == release else
                         "no earlier assembly of this release here"},
    }
    result = PaginatedResult(path=final, release=release, release_date=release_date,
                             total=total, pages=pages, unique=len(seen), duplicates=duplicates,
                             bytes=len(data), sha256=digest, query=query,
                             retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             checks=checks)
    sidecar_path(final).write_text(json.dumps({"schema": "bioagent.uniprot_pages/1",
                                               "stream_url": stream_url, **result.record()},
                                              indent=1, sort_keys=True), encoding="utf-8")
    from ..tcmdb.acquire import record_manifest
    record_manifest(dest, filename, {"url": stream_url, "bytes": len(data),
                                     "checksum": f"sha256:{digest}", "verified": True,
                                     "fetched_at": time.time(), "route": "paginated",
                                     "release": release, "pages": pages, "total": total})
    log(f"uniprot: {len(seen)} accessions in {pages} pages, release {release}, "
        f"sha256 {digest[:12]}…")
    return result
