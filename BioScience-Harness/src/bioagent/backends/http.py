"""HTTP backend — public REST and GraphQL data sources as components.

222 catalogue components declare the `http` backend and had nothing to run them.
This backend executes them, with the properties a shared scientific client needs:

* **per-host rate limiting** — public APIs publish limits (NCBI: 3 req/s without
  a key, Ensembl: 15 req/s); exceeding them gets everyone throttled.
* **bounded retries with backoff** on 429/5xx and transient socket errors.
* **response-size cap** so a mis-specified query cannot pull a 2 GB body into RAM.
* **on-disk response cache** keyed by the request hash, so replay is exact and
  repeated lookups are free.
* **honest statuses** — a network denial is `UNAVAILABLE` with the reason, a 4xx
  is `FAILED` with the body excerpt, a timeout is `TIMEOUT`; nothing is `ok`
  unless a 2xx body was parsed, and a body truncated to fit is `DEGRADED`.
* **polite retries** — a 429/503 ``Retry-After`` is honoured up to a cap; a longer
  requested pause ends the call rather than being ignored.
* **redirects stay on declared hosts** — a 30x to a host the call may not contact is
  refused (``DENIED``), as is a downgrade from https to http. ``urlopen`` follows
  redirects silently, so without this the host allow-list only governed the first hop.
* **an HTML page is not data** — a login, CAPTCHA, WAF challenge or error page served
  with ``200`` where the caller asked for JSON, text or XML is ``UNAVAILABLE`` with the
  reason, not a successful result whose "value" is the page's markup.
* **an honest User-Agent** — the harness names itself and a contact
  (``BIOAGENT_CONTACT``), which Wikidata's policy requires and NCBI asks for.

Only the standard library is used, so the backend works in any environment the
harness itself runs in.
"""

from __future__ import annotations

import datetime
import email.utils
import gzip
import hashlib
import http.client
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..runtime.component import ComponentManifest
from ..status import ExecutionStatus
from .base import Backend

#: The project a request comes from, and how its operator can be reached. Wikidata's
#: User-Agent policy requires a contact and throttles or blocks clients without one; NCBI
#: asks for one. ``https://localhost`` named nobody. Set ``BIOAGENT_CONTACT`` to a
#: mailbox or URL that reaches the person running the harness.
_PROJECT_URL = "https://github.com/psknlr/TCMScience"


def user_agent() -> str:
    import os

    from .. import __version__

    contact = os.environ.get("BIOAGENT_CONTACT", "").strip()
    who = f"{_PROJECT_URL}; {contact}" if contact else _PROJECT_URL
    return f"bioagent-harness/{__version__} (+{who}; research use)"


_USER_AGENT = user_agent()

#: Characters of a text/XML body kept in the parsed value; longer bodies are truncated and
#: the call reports DEGRADED rather than SUCCEEDED.
_TEXT_LIMIT = 200_000


def _retry_after_seconds(headers: Any) -> float | None:
    """The pause a 429/503 response asks for, from ``Retry-After`` (seconds or HTTP-date)."""
    raw = headers.get("Retry-After") if headers is not None else None
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds())

class _RedirectRefused(RuntimeError):
    """A redirect would have left the hosts this call may contact."""


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to an allowed host, and never from https down to http."""

    def __init__(self, allowed: frozenset[str], origin_scheme: str) -> None:
        super().__init__()
        self.allowed = allowed
        self.origin_scheme = origin_scheme

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        target = urllib.parse.urlsplit(newurl)
        host = (target.hostname or "").lower()
        if host not in self.allowed:
            raise _RedirectRefused(
                f"redirected ({code}) to {host or newurl!r}, which is not a host this call "
                f"may contact ({', '.join(sorted(self.allowed))})")
        if self.origin_scheme == "https" and target.scheme != "https":
            raise _RedirectRefused(f"redirected ({code}) from https down to {target.scheme}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _html_page(raw: bytes, ctype: str) -> bool:
    """Whether a body is an HTML document rather than the data it stands in for.

    Either the server says so (``text/html``, XHTML) or the body opens like a page. A
    body that parses as JSON is data whatever its content type — some servers label JSON
    ``text/html`` — so it is never counted as a page.
    """
    head = raw[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head[:1] in (b"{", b"["):
        try:
            json.loads(raw.decode("utf-8", "replace"))
            return False
        except json.JSONDecodeError:
            pass
    ct = (ctype or "").lower()
    return ("text/html" in ct or "application/xhtml" in ct
            or head.startswith(b"<!doctype html") or head.startswith(b"<html"))


def _charset(ctype: str) -> str:
    """The body's declared encoding, else UTF-8.

    Every body used to be decoded as UTF-8 whatever it declared. Chinese TCM sites and
    exports are commonly GBK or GB18030, and a GBK page decoded as UTF-8 comes back as
    replacement characters — the Chinese names, the whole point of the record — with a
    SUCCEEDED status.
    """
    import codecs

    m = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", ctype or "")
    if m:
        try:
            return codecs.lookup(m.group(1)).name
        except LookupError:
            pass
    return "utf-8"


def _asks_for_html(accept: str) -> bool:
    return "html" in (accept or "").lower()


#: Published or conservative per-host request rates (requests / second).
DEFAULT_RATES: Mapping[str, float] = {
    "eutils.ncbi.nlm.nih.gov": 3.0,
    "pubchem.ncbi.nlm.nih.gov": 5.0,
    "rest.ensembl.org": 15.0,
    "rest.uniprot.org": 10.0,
    # www.ebi.ac.uk: documented per-entity API look-ups (including IMPC's Solr cores);
    # robots.txt's Crawl-delay 10 s governs crawling, which no connector does
    "www.ebi.ac.uk": 10.0,
    "string-db.org": 1.0,
    "rest.kegg.jp": 3.0,
    "reactome.org": 5.0,
    "api.platform.opentargets.org": 5.0,
    "data.rcsb.org": 10.0,
    "clinicaltrials.gov": 3.0,
    "api.fda.gov": 4.0,          # 240/min without a key
    "gnomad.broadinstitute.org": 1.0,
    "mygene.info": 10.0,
    "myvariant.info": 10.0,
    # extended connector set (v2.4)
    "rest.genenames.org": 10.0,
    "api.ncbi.nlm.nih.gov": 5.0,
    "www.ncbi.nlm.nih.gov": 3.0,         # PubTator 3
    "api.genome.ucsc.edu": 5.0,
    "alphafold.ebi.ac.uk": 5.0,
    "gtexportal.org": 5.0,
    "www.encodeproject.org": 3.0,
    "api.cellxgene.cziscience.com": 3.0,
    "www.wikipathways.org": 3.0,
    "omnipathdb.org": 3.0,
    "dgidb.org": 3.0,
    "civicdb.org": 3.0,
    "www.cbioportal.org": 5.0,
    "api.gdc.cancer.gov": 5.0,
    "clinicaltables.nlm.nih.gov": 5.0,
    "rxnav.nlm.nih.gov": 10.0,
    "dailymed.nlm.nih.gov": 5.0,
    "id.nlm.nih.gov": 5.0,
    "api.crossref.org": 5.0,
    "api.openalex.org": 5.0,
    "api.biorxiv.org": 3.0,
    "ontology.jax.org": 5.0,
    "api.monarchinitiative.org": 5.0,
    "disease-ontology.org": 3.0,
    "bioregistry.io": 5.0,
    "resolver.api.identifiers.org": 5.0,
    "biit.cs.ut.ee": 2.0,
    "pantherdb.org": 2.0,
    "www.proteinatlas.org": 3.0,
    # Wikidata throttles shared cloud addresses to about one query a minute; declared with
    # margin so the limiter paces rather than the service refuses. Raise it with rates= on a
    # network Wikidata treats better.
    "query.wikidata.org": 1.0 / 90.0,
    "api.gbif.org": 5.0,
    # TCM connectors (providers.public_apis_tcm): small academic servers, several of
    # which ask for one request per second
    "batman2api.cloudna.cn": 1.0, "bionet.ncpsb.org.cn": 1.0, "tcmbank.cn": 1.0,
    "itcm.biotcm.net": 1.0, "ttd.idrblab.cn": 1.0, "query-api.iedb.org": 3.0,
    "huggingface.co": 3.0, "datasets-server.huggingface.co": 3.0, "api.figshare.com": 3.0,
    "www.symmap.org": 1.0, "47.92.70.12": 1.0,
    # supplementary sources (review of 2026-09-30), checked 2026-10-01: natural products
    # and plant names (providers.supplement.tcm_np). list.worldfloraonline.org states
    # Crawl-delay: 10 in its robots.txt.
    "coconut.naturalproducts.net": 1.0, "www.knapsackfamily.com": 1.0,
    "list.worldfloraonline.org": 0.1,
    # bulk files these sources' datasets fetch (acquisition.Downloader paces by these too):
    # Zenodo's robots.txt sets Crawl-delay: 10; the others are small academic servers
    "zenodo.org": 0.1, "coconut.s3.uni-jena.de": 1.0, "cb.imsc.res.in": 1.0,
    "tm-mc.kr": 1.0,
    # supplementary sources (review of 2026-09-30), checked 2026-10-01: TCM safety
    # (providers.supplement.tcm_safety), small academic servers
    "mibig.secondarymetabolites.org": 1.0, "bio-computing.hrbmu.edu.cn": 1.0,
    "phytohub.eu": 1.0,
    # genetics supplementary sources: MaveDB enforces no limit but asks to be considerate
    "api.mavedb.org": 1.0,
    # supplementary sources (review of 2026-09-30) -- safety: AOP-Wiki and Orphadata
    "aopwiki.org": 1.0, "api.orphadata.com": 1.0,
    # supplementary sources (review of 2026-09-30): spectra and metabolomics servers
    "massbank.eu": 1.0, "external.gnps2.org": 1.0, "metabolomics-usi.gnps2.org": 1.0,
    "explorer.gnps2.org": 1.0, "massive.ucsd.edu": 1.0, "www.metabolomicsworkbench.org": 1.0,
}


def _default_rates() -> dict[str, float]:
    """``DEFAULT_RATES`` tightened by every source card's declared rate.

    Imported here rather than at module load so the transport does not depend on the
    sources package to be importable at all.
    """
    try:
        from ..sources.cards import request_rates
    except ImportError:                      # pragma: no cover - partial installs
        return dict(DEFAULT_RATES)
    return request_rates(base=DEFAULT_RATES)


class _RateLimiter:
    """Token-bucket limiter per host; thread-safe."""

    def __init__(self, rates: Mapping[str, float], default_rps: float = 2.0) -> None:
        self._rates = dict(rates)
        self._default = default_rps
        self._next_ok: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> float:
        rps = self._rates.get(host, self._default)
        interval = 1.0 / max(rps, 0.01)
        with self._lock:
            now = time.monotonic()
            ready = self._next_ok.get(host, now)
            delay = max(0.0, ready - now)
            self._next_ok[host] = max(ready, now) + interval
        if delay > 0:
            time.sleep(delay)
        return delay


@dataclass
class HTTPRequest:
    """A fully specified request, hashable for caching."""

    url: str
    method: str = "GET"
    params: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Any = None
    data: bytes | None = None
    accept: str = "application/json"
    #: Upstream data version (a release, snapshot or dump date). Part of the cache key, so
    #: a cached answer from last release is never served for this one. Empty for sources
    #: that publish no version.
    version: str = ""

    @property
    def full_url(self) -> str:
        if not self.params:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return self.url + sep + urllib.parse.urlencode(
            {k: v for k, v in self.params.items() if v is not None}, doseq=True)

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    def key(self) -> str:
        """Cache key covering everything that changes the response.

        `accept` is sent on the wire but was missing here, so JSON and CSV
        representations of one URL collided on a single entry and the second
        caller silently got the first caller's format.
        """
        fields = {"u": self.full_url, "m": self.method, "h": dict(self.headers),
                  "a": self.accept,
                  "j": self.json_body, "d": self.data.decode("latin1") if self.data else None}
        if self.version:
            # Only when set, so the keys of existing unversioned cache entries are unchanged.
            fields["v"] = self.version
        blob = json.dumps(fields, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


class HTTPBackend(Backend):
    """Executes `http`-backed components against public REST/GraphQL endpoints."""

    backend = "http"

    def __init__(self, *, cache_dir: Path | str | None = None, timeout_s: float = 30.0,
                 max_bytes: int = 64 * 1024 * 1024, max_retries: int = 3,
                 rates: Mapping[str, float] | None = None, default_rps: float = 2.0,
                 offline: bool = False, max_retry_after_s: float = 30.0) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_retries = max_retries
        self.offline = offline
        self.max_retry_after_s = max_retry_after_s
        self._limiter = _RateLimiter(rates or _default_rates(), default_rps)
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0, "bytes": 0}

    def available(self) -> bool:
        return not self.offline

    def unavailable_reason(self) -> str:
        return "backend constructed in offline mode" if self.offline else ""

    # ------------------------------------------------------------------ core
    def request(self, req: HTTPRequest, *, use_cache: bool = True,
                allowed_hosts: Iterable[str] | None = None,
                ) -> tuple[ExecutionStatus, Any, str, dict]:
        """Perform one request. Returns (status, parsed_value, error, meta).

        ``allowed_hosts`` bounds where a redirect may lead; by default only the request's
        own host, so a direct caller gets the same guarantee a component does.
        """
        meta: dict[str, Any] = {"url": req.full_url, "method": req.method, "host": req.host,
                                "cached": False, "attempts": 0, "http_status": None}
        allowed = frozenset(h.lower() for h in (allowed_hosts or ()) if h) | {req.host.lower()}
        opener = urllib.request.build_opener(
            _GuardedRedirects(allowed, urllib.parse.urlsplit(req.url).scheme))
        if self.offline:
            return ExecutionStatus.UNAVAILABLE, None, "backend is offline", meta

        cache_path = (self.cache_dir / f"{req.key()}.json.gz") if self.cache_dir else None
        if use_cache and cache_path and cache_path.exists():
            try:
                with gzip.open(cache_path, "rt", encoding="utf-8") as fh:
                    rec = json.load(fh)
                ctype = str(rec.get("content_type") or "").lower()
                if ("text/html" in ctype or "application/xhtml" in ctype) \
                        and not _asks_for_html(req.accept):
                    # An entry written before HTML pages were refused: not data either.
                    raise ValueError("cached HTML page")
                self.stats["cache_hits"] += 1
                meta.update(cached=True, http_status=rec.get("http_status"),
                            fetched_at=rec.get("fetched_at"))
                return (*self._judge(rec["value"]), meta)
            except Exception:  # noqa: BLE001 - a corrupt cache entry is simply ignored
                pass

        body: bytes | None = None
        if req.json_body is not None:
            body = json.dumps(req.json_body).encode("utf-8")
        elif req.data is not None:
            body = req.data
        headers = {"User-Agent": _USER_AGENT, "Accept": req.accept, **dict(req.headers)}
        if req.json_body is not None:
            headers.setdefault("Content-Type", "application/json")

        last_err = ""
        for attempt in range(1, self.max_retries + 1):
            meta["attempts"] = attempt
            self._limiter.wait(req.host)
            self.stats["requests"] += 1
            try:
                r = urllib.request.Request(req.full_url, data=body, method=req.method, headers=headers)
                with opener.open(r, timeout=self.timeout_s) as resp:  # noqa: S310
                    meta["http_status"] = resp.status
                    ctype = resp.headers.get("Content-Type", "")
                    final_url = resp.geturl()
                    raw = self._read_capped(resp)
                self.stats["bytes"] += len(raw)
                if final_url and final_url != req.full_url:
                    meta["redirected_to"] = final_url
                if _html_page(raw, ctype) and not _asks_for_html(req.accept):
                    # The shape every reverse-wrapped source fails in: a login, CAPTCHA,
                    # WAF challenge or error page, served with 200 in place of the data.
                    return ExecutionStatus.UNAVAILABLE, None, (
                        f"{req.host} answered with an HTML page ({ctype or 'no content type'}) "
                        f"where {req.accept} was asked for; a login, CAPTCHA, challenge or "
                        "error page is not data"), meta
                value = self._parse(raw, ctype)
                meta["fetched_at"] = time.time()
                if cache_path:
                    with gzip.open(cache_path, "wt", encoding="utf-8") as fh:
                        json.dump({"http_status": meta["http_status"], "value": value,
                                   "content_type": ctype, "fetched_at": meta["fetched_at"]},
                                  fh, default=str)
                return (*self._judge(value), meta)
            except urllib.error.HTTPError as exc:
                meta["http_status"] = exc.code
                excerpt = ""
                try:
                    excerpt = exc.read(600).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    pass
                last_err = f"HTTP {exc.code} {exc.reason}: {excerpt[:300]}"
                if exc.code == 403 and ("sandbox" in excerpt.lower() or "proxy" in excerpt.lower()
                                        or "network policy" in excerpt.lower()):
                    return ExecutionStatus.UNAVAILABLE, None, f"network access denied for {req.host}: {last_err}", meta
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    backoff = min(8.0, 0.8 * (2 ** attempt))
                    asked = _retry_after_seconds(exc.headers)
                    if asked is not None and asked > self.max_retry_after_s:
                        # The server asked for a longer pause than a call may spend waiting;
                        # retrying sooner would ignore it, so stop and say so.
                        meta["retry_after_s"] = asked
                        return ExecutionStatus.FAILED, None, (
                            f"{last_err} (server asked to retry after {asked:.0f}s, more than "
                            f"the {self.max_retry_after_s:.0f}s this backend waits)"), meta
                    self.stats["retries"] += 1
                    time.sleep(max(backoff, asked or 0.0))
                    continue
                return ExecutionStatus.FAILED, None, last_err, meta
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
                    http.client.IncompleteRead, http.client.RemoteDisconnected,
                    http.client.HTTPException) as exc:
                # IncompleteRead: the server closed a chunked stream early — a
                # transient the live tests hit on the first run; retry, never crash.
                reason = str(getattr(exc, "reason", exc))
                last_err = f"{type(exc).__name__}: {reason}"
                if "timed out" in reason.lower():
                    if attempt < self.max_retries:
                        self.stats["retries"] += 1
                        continue
                    return ExecutionStatus.TIMEOUT, None, last_err, meta
                if any(k in reason.lower() for k in ("refused", "proxy", "403", "network")):
                    return ExecutionStatus.UNAVAILABLE, None, f"cannot reach {req.host}: {last_err}", meta
                if attempt < self.max_retries:
                    self.stats["retries"] += 1
                    time.sleep(min(8.0, 0.8 * (2 ** attempt)))
                    continue
            except _TooLarge as exc:
                return ExecutionStatus.FAILED, None, str(exc), meta
            except _RedirectRefused as exc:
                return ExecutionStatus.DENIED, None, str(exc), meta
            except Exception as exc:  # noqa: BLE001 - reported, never raised into the runtime
                return ExecutionStatus.FAILED, None, f"{type(exc).__name__}: {exc}", meta
        return ExecutionStatus.FAILED, None, last_err or "exhausted retries", meta

    @staticmethod
    def _judge(value: Any) -> tuple[ExecutionStatus, Any, str]:
        """SUCCEEDED, or DEGRADED when the parsed body is known to be incomplete."""
        if isinstance(value, dict) and value.get("truncated"):
            return (ExecutionStatus.DEGRADED, value,
                    f"response text truncated to {_TEXT_LIMIT} characters; refine the query")
        return ExecutionStatus.SUCCEEDED, value, ""

    def _read_capped(self, resp) -> bytes:
        chunks, total = [], 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > self.max_bytes:
                raise _TooLarge(f"response exceeded {self.max_bytes} bytes; refine the query")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse(raw: bytes, ctype: str) -> Any:
        ct = ctype.lower()
        text = raw.decode(_charset(ct), "replace")
        if "json" in ct or text[:1] in "{[":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        if "text/html" in ct or "application/xhtml" in ct:
            # A page a connector asked for (accept text/html; any other caller is refused
            # before this). Indented markup has tabs and newlines, and read as a table it
            # kept only each line's text up to its first tab.
            return {"format": "html", "text": text[:_TEXT_LIMIT],
                    "truncated": len(text) > _TEXT_LIMIT}
        if "tab-separated" in ct or "tsv" in ct or ("\t" in text[:2000] and "\n" in text[:2000]):
            lines = [ln for ln in text.splitlines() if ln.strip()]
            if lines:
                hdr = lines[0].lstrip("#").split("\t")
                rows = [dict(zip(hdr, ln.split("\t"))) for ln in lines[1:]]
                return {"columns": hdr, "rows": rows, "n_rows": len(rows), "format": "tsv"}
        if "xml" in ct or text.lstrip().startswith("<"):
            return {"format": "xml", "text": text[:_TEXT_LIMIT], "truncated": len(text) > _TEXT_LIMIT}
        return {"format": "text", "text": text[:_TEXT_LIMIT], "truncated": len(text) > _TEXT_LIMIT}

    # --------------------------------------------------------------- Backend
    def invoke(self, manifest: ComponentManifest, *, path: str = "", method: str = "GET",
               params: Mapping[str, Any] | None = None, json_body: Any = None,
               form: Mapping[str, Any] | None = None,
               headers: Mapping[str, str] | None = None, accept: str = "application/json",
               graphql: str | None = None, variables: Mapping[str, Any] | None = None,
               use_cache: bool = True, **_: Any) -> Any:
        """Execute against the component's declared server (base URL).

        `graphql=` turns the call into a POST with {query, variables}. Any host
        actually contacted must be in `permissions.network`, which the policy
        kernel has already checked — this is a second, local guard.
        """
        t0 = time.perf_counter()
        base = (manifest.runtime.server or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            return self._result(manifest, ExecutionStatus.UNAVAILABLE, t0,
                                error=f"component declares no http(s) server (got {base!r})")
        url = base + ("/" + path.lstrip("/") if path else "")
        host = urllib.parse.urlsplit(url).hostname or ""
        allowed = {h.lower() for h in manifest.permissions.network}
        # Deny by default. `if allowed and ...` inverted the rule the docstring
        # states: a component declaring no hosts skipped the check entirely, so
        # an empty permissions.network was the most permissive setting there was
        # rather than the least. An undeclared endpoint is now refused.
        if not allowed:
            return self._result(manifest, ExecutionStatus.DENIED, t0,
                                error=("component declares no permissions.network; an http "
                                       f"component must declare the hosts it contacts (wanted {host})"))
        if host.lower() not in allowed:
            return self._result(manifest, ExecutionStatus.DENIED, t0,
                                error=f"host {host} not declared in permissions.network {sorted(allowed)}")
        if graphql is not None:
            req = HTTPRequest(url=url, method="POST",
                              json_body={"query": graphql, "variables": dict(variables or {})},
                              headers=dict(headers or {}), accept="application/json")
        elif form is not None:
            # A form post: the fields are the body, url-encoded, as a browser sends them.
            body = urllib.parse.urlencode(
                {k: v for k, v in dict(form).items() if v is not None}, doseq=True)
            req = HTTPRequest(url=url, method="POST", params=dict(params or {}),
                              headers={"Content-Type": "application/x-www-form-urlencoded",
                                       **dict(headers or {})},
                              data=body.encode("utf-8"), accept=accept)
        else:
            req = HTTPRequest(url=url, method=method.upper(), params=dict(params or {}),
                              headers=dict(headers or {}), json_body=json_body, accept=accept)
        status, value, err, meta = self.request(req, use_cache=use_cache, allowed_hosts=allowed)
        if status is ExecutionStatus.SUCCEEDED and graphql is not None and isinstance(value, dict) \
                and value.get("errors"):
            status, err = ExecutionStatus.FAILED, f"graphql errors: {json.dumps(value['errors'])[:400]}"
        return self._result(manifest, status, t0, value=value, error=err or None, metadata=meta)


class _TooLarge(RuntimeError):
    pass


def hostname(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or ""


_SLUG = re.compile(r"[^a-z0-9_.-]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")
