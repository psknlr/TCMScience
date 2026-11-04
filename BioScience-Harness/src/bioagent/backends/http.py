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
* **an error report is not data** — a PHP fatal error, a Python, Java, .NET or Node stack
  trace served with ``200`` (SIGNOR answered its ``connect`` example with a PHP
  ``TypeError`` as ``text/plain``, which parsed as "XML" and was reported SUCCEEDED in
  the audit of 2026-11-04) is ``FAILED`` with ``metadata.content_check`` saying why, and
  is never cached.
* **a declared shape is checked** — an operation may declare what its answer must look
  like (``contract``: a table's columns, the fields every row must fill, patterns they
  must match, a minimum row count). An answer that does not fit — malformed, misaligned
  or empty — is ``FAILED`` before it can reach evidence synthesis, not a result.
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


#: Server-side error reports a body can be *instead of* data, each with a pattern specific
#: enough that no record carries it by chance. PHP's are the common case on academic
#: servers (SIGNOR, 2026-11-04: ``<b>Fatal error</b>: Uncaught TypeError: array_map()``
#: as ``text/plain`` with HTTP 200); an interpreter's stack trace is the general one.
_ERROR_REPORTS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("PHP error", re.compile(
        r"<b>\s*(?:Fatal error|Parse error|Warning|Notice|Deprecated|Recoverable fatal error)"
        r"\s*</b>\s*:", re.I)),
    ("PHP error", re.compile(
        r"(?:PHP )?(?:Fatal error|Parse error)\s*:\s+.{0,800}?"
        r"(?:\.php\b|Uncaught\b|Allowed memory size|Maximum execution time)", re.I | re.S)),
    ("PHP warning", re.compile(
        r"(?:Warning|Notice|Deprecated)\s*:\s+.{0,800}?\.php\s+on line\s+\d+", re.I | re.S)),
    ("PHP stack trace", re.compile(r"^Stack trace:\s*$\s*^#0 ", re.M)),
    ("Python traceback", re.compile(r"Traceback \(most recent call last\):\s*\n\s+File \"")),
    ("Java stack trace", re.compile(r"\n\s*at [\w$.<>]+\([\w$]+\.java:\d+\)")),
    (".NET error page", re.compile(r"Server Error in '[^']*' Application")),
    (".NET stack trace", re.compile(r"\n\s*at [\w.`<>]+\(.*\) in .+:line \d+")),
    ("Node.js stack trace", re.compile(r"\n\s+at .{1,200} \((?:/|[A-Za-z]:\\|node:).+:\d+:\d+\)")),
)
#: The browser source gateway's refusals (``studio/edge/src/sources.js``) that the same
#: request would meet again: an authorization, input or configuration refusal. A retry is
#: pointless, so the call stops at once; ``rate_limited`` waits for its Retry-After and
#: ``timeout`` / ``upstream_unreachable`` / ``unavailable`` are retried as transients.
_GATEWAY_FINAL = frozenset({"forbidden_origin", "https_required", "invalid_body",
                            "method_not_allowed", "not_configured", "not_found",
                            "redirect_refused", "source_not_allowed", "too_large"})
#: How much of each end of a body is searched: a report is either the whole answer or
#: what a server appended when it died part-way through (PHP's memory errors).
_REPORT_WINDOW = 8192


def _error_report(raw: bytes) -> str | None:
    """``"<kind>: <excerpt>"`` when a body is a server's error report, else ``None``.

    A body that parses as JSON is the API's own answer (its error objects are read by
    the caller), so it is never counted as a report.
    """
    if not raw:
        return None
    head = raw[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head[:1] in (b"{", b"["):
        try:
            json.loads(raw.decode("utf-8", "replace"))
            return None
        except json.JSONDecodeError:
            pass
    ends = [raw[:_REPORT_WINDOW]]
    if len(raw) > _REPORT_WINDOW:
        ends.append(raw[-_REPORT_WINDOW:])
    for part in ends:
        text = part.decode("utf-8", "replace")
        for kind, pattern in _ERROR_REPORTS:
            m = pattern.search(text)
            if m:
                start = max(0, m.start() - 40)
                excerpt = re.sub(r"<[^>]{1,20}>", "", text[start:m.start() + 200])
                return f"{kind}: {' '.join(excerpt.split())[:200]}"
    return None


def _contract_problems(value: Any, contract: Mapping[str, Any] | None) -> list[str]:
    """What keeps a parsed answer from fitting the operation's declared ``contract``.

    The contract is the dict an ``Operation`` renders (``providers.public_apis``):
    ``kind`` (only ``table`` so far), ``columns``, ``required`` (fields every row must
    fill), ``patterns`` (field -> regular expression), ``min_fields`` (fields a row must
    have, counted before they are named) and ``min_rows``.
    """
    if not contract:
        return []
    what = str(contract.get("what") or "rows")
    if contract.get("kind", "table") != "table":
        return [f"unknown contract kind {contract.get('kind')!r}"]
    if not isinstance(value, dict) or value.get("format") != "tsv" \
            or not isinstance(value.get("rows"), list):
        fmt = value.get("format") if isinstance(value, dict) else type(value).__name__
        return [f"the body is not the tab-separated table of {what} the operation returns "
                f"(it parsed as {fmt})"]
    rows = value["rows"]
    min_rows = int(contract.get("min_rows", 1))
    if len(rows) < min_rows:
        return [f"the body holds {len(rows)} row(s) of {what}; at least {min_rows} expected. "
                "An empty answer cannot be told apart from a failed one, so it is not evidence "
                "that nothing exists"]
    required = [str(c) for c in contract.get("required") or ()]
    patterns = {str(k): re.compile(str(v)) for k, v in (contract.get("patterns") or {}).items()}
    min_fields = int(contract.get("min_fields") or 0)
    widths = value.get("widths") or []
    bad: list[str] = []
    n_bad = 0
    for i, row in enumerate(rows):
        why = []
        if min_fields and i < len(widths) and widths[i] < min_fields:
            why.append(f"{widths[i]} field(s), not at least {min_fields}")
        empty = [c for c in required if not str(row.get(c) or "").strip()]
        if empty:
            why.append(f"no {', '.join(empty)}")
        off = [c for c, p in patterns.items()
               if str(row.get(c) or "").strip() and not p.fullmatch(str(row.get(c)).strip())]
        if off:
            why.append(", ".join(f"{c}={str(row.get(c))[:40]!r}" for c in off)
                       + " does not match the declared form")
        if why:
            n_bad += 1
            if len(bad) < 3:
                bad.append(f"row {i + 1}: {'; '.join(why)}")
    if n_bad:
        return [f"{n_bad} of {len(rows)} row(s) of {what} are malformed ({' | '.join(bad)}); "
                "a payload that does not fit its declared shape is not a result"]
    return []


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
    # supplementary chemistry/reaction connectors (providers.supplement.chem_onto):
    # Rhea's and SABIO-RK's robots.txt bar crawlers, their API docs invite programs;
    # SABIO-RK allows 60 requests a minute; MetaNetX asks for a 10 s crawl delay
    "www.rhea-db.org": 1.0, "sabiork.h-its.org": 1.0, "rdf.metanetx.org": 0.1,
    # supplementary sources (review of 2026-09-30): RNA, regulation and protein
    # modification. Crawl-delays in robots.txt become rates: RNAcentral 5 s, ChIP-Atlas
    # 30 s (its /agents page invites the /data calls robots.txt disallows for crawlers).
    "rnacentral.org": 0.2, "chip-atlas.org": 1.0 / 30.0, "disprot.org": 1.0,
    "remap.univ-amu.fr": 1.0, "research.bioinformatics.udel.edu": 1.0,
    # supplementary sources (review of 2026-09-30), checked 2026-10-01: atlases
    "service.azul.data.humancellatlas.org": 1.0, "search.api.hubmapconsortium.org": 1.0,
    "entity.api.hubmapconsortium.org": 1.0, "portal.hubmapconsortium.org": 1.0,
    "data.4dnucleome.org": 1.0,
    # supplementary sources (review of 2026-09-30), checked 2026-10-01: drugs
    "uxn2ycvimg.us-east-2.awsapprunner.com": 1.0, "pharmacodb.ca": 1.0,
    "pharos-api.ncats.io": 1.0,
    # pharmacogenomics and protein families (providers.supplement.pgx_proteins): academic
    # servers with no stated limit, kept at one request per second
    "api.cpicpgx.org": 1.0, "gpcrdb.org": 1.0, "klifs.net": 1.0,
    "www.proteomicsdb.org": 1.0,
    # supplementary sources (review of 2026-09-30), mechanism: academic servers
    "signor.uniroma2.it": 1.0, "jaspar.elixir.no": 1.0,
    # supplementary sources (review of 2026-09-30): cells and perturbations
    "api.cellosaurus.org": 1.0,
    # immune_microbe (supplementary sources): AIRR Data Commons repositories and BV-BRC
    "covid19-1.ireceptor.org": 1.0, "vdjserver.org": 1.0, "www.bv-brc.org": 1.0,
    # MGnify sits on www.ebi.ac.uk, whose robots.txt sets Crawl-Delay: 10. A path-prefix key
    # ("host/path") paces only the URLs under it, on top of the host's own rate, so the
    # other EBI APIs on that host keep theirs.
    "www.ebi.ac.uk/metagenomics/": 0.1,
    # imaging archives (providers.supplement.imaging): academic services, one at a time
    "api.imaging.datacommons.cancer.gov": 1.0, "services.cancerimagingarchive.net": 1.0,
    "www.cancerimagingarchive.net": 1.0, "idr.openmicroscopy.org": 1.0,
    "openneuro.org": 1.0,
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
    """Token-bucket limiter per host; thread-safe.

    A rate keyed ``"host/path-prefix"`` (it contains a ``/``) paces only the requests whose
    ``host + path`` starts with it, in addition to the host's own rate: one service on a
    shared host (MGnify on www.ebi.ac.uk) can be held to a slower pace than its neighbours.
    """

    def __init__(self, rates: Mapping[str, float], default_rps: float = 2.0) -> None:
        self._rates = {k: v for k, v in rates.items() if "/" not in k}
        self._prefixes = {k: v for k, v in rates.items() if "/" in k}
        self._default = default_rps
        self._next_ok: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, path: str = "") -> float:
        buckets = {host: self._rates.get(host, self._default)}
        target = host + (path or "/")
        for prefix, rps in self._prefixes.items():
            if target.startswith(prefix):
                buckets[prefix] = rps
        with self._lock:
            now = time.monotonic()
            start = max([now] + [self._next_ok.get(k, now) for k in buckets])
            for k, rps in buckets.items():
                self._next_ok[k] = start + 1.0 / max(rps, 0.01)
        delay = start - now
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
    #: The shape the answer must have (``_contract_problems``), from the operation. Part of
    #: the cache key when set, since a headerless table is parsed by its columns.
    contract: Mapping[str, Any] | None = None

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
        if self.contract:
            fields["c"] = dict(self.contract)
        blob = json.dumps(fields, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


class HTTPBackend(Backend):
    """Executes `http`-backed components against public REST/GraphQL endpoints."""

    backend = "http"
    # Installed only by Studio's Pyodide worker. Native Python keeps urllib's guarded
    # opener; a browser transport must preserve the same response/error interface.
    browser_opener_factory = None

    def __init__(self, *, cache_dir: Path | str | None = None, timeout_s: float = 30.0,
                 max_bytes: int = 64 * 1024 * 1024, max_retries: int = 3,
                 rates: Mapping[str, float] | None = None, default_rps: float = 2.0,
                 offline: bool = False, max_retry_after_s: float = 30.0,
                 max_age_s: float | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_retries = max_retries
        self.offline = offline
        self.max_retry_after_s = max_retry_after_s
        #: Cached answers older than this are fetched again. ``None`` keeps them forever,
        #: which is right only for a request pinned to a data ``version``.
        self.max_age_s = max_age_s
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
        factory = type(self).browser_opener_factory
        opener = (factory(allowed) if factory else urllib.request.build_opener(
            _GuardedRedirects(allowed, urllib.parse.urlsplit(req.url).scheme)))
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
                if self.max_age_s is not None and (
                        time.time() - float(rec.get("fetched_at") or 0)) > self.max_age_s:
                    raise ValueError("cached answer is older than max_age_s")
                cached_text = (rec["value"].get("text") if isinstance(rec.get("value"), dict)
                               else None)
                if isinstance(cached_text, str) and _error_report(cached_text.encode("utf-8")):
                    # An entry written before error reports were refused: not data either.
                    raise ValueError("cached error report")
                if _contract_problems(rec["value"], req.contract):
                    raise ValueError("cached answer does not fit the contract")
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
            self._limiter.wait(req.host, urllib.parse.urlsplit(req.url).path)
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
                    meta["content_check"] = {"ok": False, "check": "html_page",
                                             "layer": "upstream_content"}
                    meta["failure_layer"] = "upstream_content"
                    return ExecutionStatus.UNAVAILABLE, None, (
                        f"{req.host} answered with an HTML page ({ctype or 'no content type'}) "
                        f"where {req.accept} was asked for; a login, CAPTCHA, challenge or "
                        "error page is not data"), meta
                report = _error_report(raw)
                if report:
                    meta["content_check"] = {"ok": False, "check": "server_error_report",
                                             "layer": "upstream_content",
                                             "detail": report}
                    meta["failure_layer"] = "upstream_content"
                    return ExecutionStatus.FAILED, None, (
                        f"{req.host} answered HTTP {meta['http_status']} with a server error "
                        f"report instead of data ({report}); an error report is not a result "
                        "and contributes no evidence"), meta
                value = self._parse(raw, ctype, contract=req.contract)
                problems = _contract_problems(value, req.contract)
                if problems:
                    meta["content_check"] = {"ok": False, "check": "response_contract",
                                             "layer": "upstream_content",
                                             "detail": problems[0][:400]}
                    meta["failure_layer"] = "upstream_content"
                    return ExecutionStatus.FAILED, None, (
                        f"{req.host} answered HTTP {meta['http_status']}, but not with what "
                        f"the operation returns: {problems[0]}"), meta
                if req.contract:
                    meta["content_check"] = {"ok": True, "check": "response_contract",
                                             "rows": len(value.get("rows") or ())}
                meta["fetched_at"] = time.time()
                if cache_path:
                    with gzip.open(cache_path, "wt", encoding="utf-8") as fh:
                        json.dump({"http_status": meta["http_status"], "value": value,
                                   "content_type": ctype, "fetched_at": meta["fetched_at"]},
                                  fh, default=str)
                return (*self._judge(value), meta)
            except urllib.error.HTTPError as exc:
                meta["http_status"] = exc.code
                # The upstream answered with an error status (through the gateway, its own).
                meta["failure_layer"] = "upstream_http"
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
                # Which layer failed: the browser's source gateway says so on the exception
                # (tcmstudio.browser_http.GatewayRefusal); anything else is the transport.
                meta["failure_layer"] = getattr(exc, "layer", None) or "transport"
                gateway = getattr(exc, "gateway", None)
                if isinstance(gateway, Mapping):
                    meta["gateway"] = dict(gateway)
                    etype = str(gateway.get("error_type") or "")
                    asked = gateway.get("retry_after_s")
                    if etype in _GATEWAY_FINAL:
                        # an authorization or input refusal: retrying sends the same request
                        return ExecutionStatus.UNAVAILABLE, None, (
                            f"the source gateway refused the request to {req.host} "
                            f"({etype or 'refused'}, HTTP {gateway.get('status')}): {reason}"), meta
                    if etype == "rate_limited":
                        if asked is not None and float(asked) > self.max_retry_after_s:
                            meta["retry_after_s"] = float(asked)
                            return ExecutionStatus.FAILED, None, (
                                f"{last_err} (the source gateway asked to retry after "
                                f"{float(asked):.0f}s, more than the "
                                f"{self.max_retry_after_s:.0f}s this backend waits)"), meta
                        if attempt < self.max_retries:
                            self.stats["retries"] += 1
                            time.sleep(max(min(8.0, 0.8 * (2 ** attempt)), float(asked or 0)))
                            continue
                        if asked is not None:
                            meta["retry_after_s"] = float(asked)
                        return ExecutionStatus.FAILED, None, last_err, meta
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
                meta["failure_layer"] = "client"
                return ExecutionStatus.FAILED, None, str(exc), meta
            except _RedirectRefused as exc:
                meta["failure_layer"] = "policy"
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
    def _parse(raw: bytes, ctype: str, contract: Mapping[str, Any] | None = None) -> Any:
        ct = ctype.lower()
        text = raw.decode(_charset(ct), "replace")
        if contract and contract.get("kind", "table") == "table" and contract.get("columns") \
                and not contract.get("header", False) and (not text.strip()
                                                           or text.lstrip()[:1] not in "{[<"):
            # A headerless table the operation names the columns of (SIGNOR's getData.php).
            # The generic reader below took its first relation for the column names, and a
            # dict keyed by values lost every field whose value repeated another's.
            names = [str(c) for c in contract["columns"]]
            lines = [ln.rstrip("\r") for ln in text.split("\n") if ln.strip()]
            fields = [ln.split("\t") for ln in lines]
            rows = [dict(zip(names, f)) for f in fields]
            return {"columns": names, "rows": rows, "n_rows": len(rows), "format": "tsv",
                    "widths": [len(f) for f in fields]}
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
               use_cache: bool = True, contract: Mapping[str, Any] | None = None,
               **_: Any) -> Any:
        """Execute against the component's declared server (base URL).

        `graphql=` turns the call into a POST with {query, variables}. Any host
        actually contacted must be in `permissions.network`, which the policy
        kernel has already checked — this is a second, local guard. `contract=` is the
        shape the operation's answer must have (``_contract_problems``).
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
                              data=body.encode("utf-8"), accept=accept, contract=contract)
        else:
            req = HTTPRequest(url=url, method=method.upper(), params=dict(params or {}),
                              headers=dict(headers or {}), json_body=json_body, accept=accept,
                              contract=contract)
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
