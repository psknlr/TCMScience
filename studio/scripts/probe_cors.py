#!/usr/bin/env python3
"""Which connector hosts a web page may call: ``python3 studio/scripts/probe_cors.py``.

Writes ``studio/runner/src/tcmstudio/data/browser_hosts.json`` (docs/V2.md §12), which the
catalog reads to mark a connector entry ``exec: ["browser", "runner"]`` or runner-only. For
every connector the catalog lists (``bioagent.providers.public_apis.SOURCES``: the core,
extended, TCM and supplement sources), each operation whose example names every required
argument is sent as the runner would send it (``psh.arguments.arguments_for`` and the request
``backends.http.HTTPBackend`` builds), with ``Origin: https://science.impf.ai``:

* ``cors`` — the answer carries ``Access-Control-Allow-Origin: *`` or the origin. Redirects are
  followed by hand on the same host only (the browser layer refuses a cross-host redirect, as
  the backend does), and every hop must carry it. A page can read an answer only then.
* ``preflight`` — for a request that is not CORS-simple (a JSON or GraphQL POST: its
  ``Content-Type: application/json`` is not a safelisted header), the ``OPTIONS`` preflight
  with ``Access-Control-Request-Method`` and ``-Headers`` must answer 2xx with the origin and
  allow the ``content-type`` header (or ``*``). ``null`` when no request to the host needs one.
* ``null`` — not reachable from where the probe ran (DNS, refused, proxy policy, timeout), or
  only 5xx/429 answers: unknown, so the host stays runner-only.

Per host: ``cors`` is true when every successful answer (2xx/3xx; when there is none, every
4xx answer) carries the header; one that does not makes it false, because that operation would
fail in the page. ``ops`` gives each operation's own verdict (request readable from a page,
preflight included), so a later catalog can be finer than the host.

``http://`` sources are false without a request: an https page cannot call them (mixed
content). Polite by construction: one host's requests run one after another, at most one per
second; hosts run in parallel; each request times out after 15 s and the whole probe stops
starting requests after ``--budget`` seconds (what was not sent is reported, not guessed).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent

for _module, _src in (("tcmstudio", STUDIO / "runner" / "src"),
                      ("bioagent", REPO / "BioScience-Harness" / "src"),
                      ("psh", REPO / "PSH-Harness" / "src")):
    if importlib.util.find_spec(_module) is None and _src.is_dir():
        sys.path.insert(0, str(_src))

ORIGIN = "https://science.impf.ai"
SCHEMA = "tcmstudio.browser-hosts/1"
OUT = STUDIO / "runner" / "src" / "tcmstudio" / "data" / "browser_hosts.json"
#: A browser's own User-Agent (the page cannot set one), marked as this probe.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36 tcmstudio-cors-probe/1 (+https://science.impf.ai)")
SAFELISTED_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data",
                            "text/plain")
#: Bytes that make an Accept/Content-Type value non-safelisted (Fetch, "CORS-unsafe request-header byte").
_UNSAFE = set(range(0x09)) | set(range(0x0A, 0x20)) | {0x7F} | set(b'"():<>?@[\\]{}')
METHOD = (
    "For each connector operation whose example names every required argument: the request "
    "the runner sends (same URL, method, Accept, body), with 'Origin: https://science.impf.ai', "
    "redirects followed by hand on the same host; cors = every successful answer carries "
    "Access-Control-Allow-Origin '*' or the origin. Requests that are not CORS-simple (JSON or "
    "GraphQL POST) also get an OPTIONS preflight (Access-Control-Request-Method, "
    "Access-Control-Request-Headers: content-type); preflight = 2xx with the origin and the "
    "header allowed. null = not reachable from the probe's network (DNS, refusal, proxy "
    "policy, timeout) or only 5xx/429 answers. http:// sources are false (mixed content). "
    "Run from a sandboxed Linux container whose outbound HTTPS goes through an egress proxy; "
    "at most 1 request/second per host, {timeout:g} s timeouts.")


@dataclass
class Probe:
    """One operation's request, as the runner would send it."""

    source: str
    op: str
    host: str
    url: str
    method: str
    accept: str
    body: bytes | None = None
    content_type: str = ""
    error: str = ""                      # the request could not be built

    @property
    def name(self) -> str:
        return f"{self.source}.{self.op}"

    @property
    def simple(self) -> bool:
        """CORS-simple: GET/HEAD/POST with only safelisted headers (Accept, and a
        Content-Type that is form, multipart or text/plain)."""
        if self.method not in ("GET", "HEAD", "POST"):
            return False
        if not _safelisted(self.accept):
            return False
        ct = self.content_type.split(";", 1)[0].strip().lower()
        return not ct or (ct in SAFELISTED_CONTENT_TYPES and _safelisted(self.content_type))

    @property
    def request_headers(self) -> list[str]:
        """The non-safelisted header names a preflight must ask for."""
        out = []
        if not _safelisted(self.accept):
            out.append("accept")
        if self.content_type and not (self.content_type.split(";", 1)[0].strip().lower()
                                      in SAFELISTED_CONTENT_TYPES
                                      and _safelisted(self.content_type)):
            out.append("content-type")
        return sorted(out)


@dataclass
class Result:
    probe: Probe
    status: int | None = None
    acao: str | None = None
    hops: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    preflight: dict[str, Any] | None = None
    skipped: str = ""

    @property
    def answered(self) -> bool:
        return self.status is not None

    @property
    def readable(self) -> bool:
        return bool(self.hops) and all(h.get("acao_ok") for h in self.hops)


def _safelisted(value: str) -> bool:
    raw = value.encode("latin-1", "replace")
    return len(raw) <= 128 and not (set(raw) & _UNSAFE)


def build_probes() -> list[Probe]:
    """Every catalog connector operation with a complete example, rendered as the runner
    renders it (arguments_for → HTTPBackend.invoke)."""
    from bioagent.backends.http import HTTPRequest
    from bioagent.providers.public_apis import SOURCES
    from bioagent.psh.arguments import arguments_for

    probes = []
    for s in SOURCES:
        host = urllib.parse.urlsplit(s.base_url).hostname or s.host
        for op in s.operations:
            if not all(a in op.example for a in op.args):
                continue
            try:
                kw = arguments_for({"operation": op.name, **dict(op.example)}, source=s,
                                   component_id=f"public.connector.{s.key}")
            except Exception as exc:                            # noqa: BLE001
                probes.append(Probe(s.key, op.name, host, s.base_url, op.method, op.accept,
                                    error=f"{type(exc).__name__}: {exc}"))
                continue
            base = s.base_url.rstrip("/")
            path = kw.get("path") or ""
            url = base + ("/" + path.lstrip("/") if path else "")
            if kw.get("graphql") is not None:
                req = HTTPRequest(url=url, method="POST", accept="application/json",
                                  json_body={"query": kw["graphql"],
                                             "variables": dict(kw.get("variables") or {})})
            elif kw.get("form") is not None:
                data = urllib.parse.urlencode({k: v for k, v in dict(kw["form"]).items()
                                               if v is not None}, doseq=True).encode("utf-8")
                req = HTTPRequest(url=url, method="POST", params=dict(kw.get("params") or {}),
                                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                                  data=data, accept=kw.get("accept") or "application/json")
            else:
                req = HTTPRequest(url=url, method=str(kw.get("method") or "GET").upper(),
                                  params=dict(kw.get("params") or {}),
                                  json_body=kw.get("json_body"),
                                  accept=kw.get("accept") or "application/json")
            body = (json.dumps(req.json_body).encode("utf-8") if req.json_body is not None
                    else req.data)
            ctype = (dict(req.headers).get("Content-Type")
                     or ("application/json" if req.json_body is not None else ""))
            probes.append(Probe(s.key, op.name, host, req.full_url, req.method, req.accept,
                                body=body, content_type=ctype))
    return probes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _acao_ok(value: str | None) -> bool:
    return (value or "").strip() in ("*", ORIGIN)


class HostClock:
    """At most one request per second to one host."""

    def __init__(self) -> None:
        self._next = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if self._next > now:
            time.sleep(self._next - now)
        self._next = time.monotonic() + 1.0


def _send(url: str, method: str, headers: dict[str, str], body: bytes | None,
          timeout: float) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            # Read a little of the body, as a page would start to; the headers are the answer.
            with contextlib.suppress(Exception):
                resp.read(65536)
            return resp.status, resp.headers
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(Exception):
            exc.read(4096)
        return exc.code, exc.headers


def _error_text(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    text = f"{type(exc).__name__}: {reason if reason is not None else exc}"
    return text[:200]


def run_request(p: Probe, clock: HostClock, *, timeout: float, deadline: float) -> Result:
    res = Result(p)
    url, method, body = p.url, p.method, p.body
    for _hop in range(5):
        if time.monotonic() > deadline:
            res.skipped = "time budget exhausted"
            return res
        headers = {"Origin": ORIGIN, "User-Agent": USER_AGENT, "Accept": p.accept}
        if body is not None and p.content_type:
            headers["Content-Type"] = p.content_type
        clock.wait()
        try:
            status, h = _send(url, method, headers, body, timeout)
        except Exception as exc:                                # noqa: BLE001
            res.error = _error_text(exc)
            return res
        hop = {"url": url[:200], "status": status,
               "acao": h.get("Access-Control-Allow-Origin"), "acao_ok": _acao_ok(
                   h.get("Access-Control-Allow-Origin"))}
        res.hops.append(hop)
        res.status, res.acao = status, hop["acao"]
        location = h.get("Location")
        if status in (301, 302, 303, 307, 308) and location:
            nxt = urllib.parse.urljoin(url, location)
            if (urllib.parse.urlsplit(nxt).hostname or "") != (urllib.parse.urlsplit(url).hostname
                                                               or ""):
                hop["redirect"] = f"to another host ({urllib.parse.urlsplit(nxt).hostname}); " \
                                  "refused in the page as in the runner"
                return res
            if status in (301, 302, 303) and method == "POST":
                method, body = "GET", None
            url = nxt
            continue
        return res
    res.error = "more than 5 redirects"
    return res


def run_preflight(p: Probe, clock: HostClock, *, timeout: float, deadline: float) -> dict[str, Any]:
    if time.monotonic() > deadline:
        return {"skipped": "time budget exhausted"}
    asked = p.request_headers
    headers = {"Origin": ORIGIN, "User-Agent": USER_AGENT,
               "Access-Control-Request-Method": p.method}
    if asked:
        headers["Access-Control-Request-Headers"] = ",".join(asked)
    clock.wait()
    try:
        status, h = _send(p.url, "OPTIONS", headers, None, timeout)
    except Exception as exc:                                    # noqa: BLE001
        return {"error": _error_text(exc)}
    allow_methods = {m.strip().upper() for m in (h.get("Access-Control-Allow-Methods") or "").split(",") if m.strip()}
    allow_headers = {x.strip().lower() for x in (h.get("Access-Control-Allow-Headers") or "").split(",") if x.strip()}
    # Fetch's CORS-preflight check: an ok status, the origin, the method unless it is a
    # safelisted one (GET/HEAD/POST), and every asked header unless "*" (no credentials).
    method_ok = p.method in ("GET", "HEAD", "POST") or p.method in allow_methods or "*" in allow_methods
    headers_ok = "*" in allow_headers or all(x in allow_headers for x in asked)
    ok = 200 <= status <= 299 and _acao_ok(h.get("Access-Control-Allow-Origin")) and method_ok \
        and headers_ok
    return {"status": status, "acao": h.get("Access-Control-Allow-Origin"),
            "allow_methods": h.get("Access-Control-Allow-Methods"),
            "allow_headers": h.get("Access-Control-Allow-Headers"), "ok": ok}


def probe_host(host: str, probes: list[Probe], *, timeout: float, deadline: float) -> list[Result]:
    clock = HostClock()
    out = []
    for p in probes:
        if p.error:
            out.append(Result(p, error=f"request not built: {p.error}"))
            continue
        if p.url.startswith("http://"):
            out.append(Result(p, skipped="http only"))
            continue
        r = run_request(p, clock, timeout=timeout, deadline=deadline)
        if not p.simple and not r.skipped:
            r.preflight = run_preflight(p, clock, timeout=timeout, deadline=deadline)
        out.append(r)
    return out


def _op_verdict(r: Result) -> bool | None:
    if r.skipped == "http only":
        return False
    if not r.answered or r.status >= 500 or r.status == 429:
        return None
    if not r.readable or any(h.get("redirect") for h in r.hops):
        return False
    if r.preflight is not None:
        if "ok" not in r.preflight:
            return None
        return bool(r.preflight["ok"])
    return True


def judge(host: str, results: list[Result]) -> dict[str, Any]:
    ops = {r.probe.name: _op_verdict(r) for r in results}
    if results and all(r.skipped == "http only" for r in results):
        return {"cors": False, "preflight": None, "ops": ops,
                "evidence": f"http:// only ({results[0].probe.url[:80]}): a page served over "
                            "https cannot call it (mixed content); not requested"}
    answered = [r for r in results if r.answered]
    decisive = [r for r in answered if r.status < 500 and r.status != 429]
    success = [r for r in decisive if r.status < 400]
    basis = success or decisive
    sample = (next((r for r in basis if not r.readable), None) or (basis[0] if basis else None))
    if basis:
        cors: bool | None = all(r.readable for r in basis)
        n_ok = sum(1 for r in basis if r.readable)
        kind = "successful" if success else "4xx"
        last = sample.hops[-1]
        evidence = (f"{sample.probe.method} {sample.probe.url[:120]} → {last['status']}, "
                    f"Access-Control-Allow-Origin: {last['acao'] or '(absent)'}"
                    + (f" [{last['redirect']}]" if last.get("redirect") else "")
                    + f"; {n_ok}/{len(basis)} {kind} answers carry it")
    else:
        cors = None
        errs = sorted({r.error or r.skipped or f"HTTP {r.status}" for r in results})
        evidence = "not reachable from the probe: " + "; ".join(errs)[:300]

    needs = [r for r in results if r.preflight is not None]
    preflight: bool | None = None
    if needs:
        done = [r for r in needs if "ok" in r.preflight]
        if done:
            preflight = all(r.preflight["ok"] for r in done)
            pf = next((r for r in done if not r.preflight["ok"]), done[0])
            evidence += (f"; OPTIONS {pf.probe.url[:100]} → {pf.preflight['status']}, "
                         f"allow-origin {pf.preflight['acao'] or '(absent)'}, allow-headers "
                         f"{pf.preflight['allow_headers'] or '(absent)'}; "
                         f"{sum(1 for r in done if r.preflight['ok'])}/{len(done)} preflights pass")
        else:
            errs = sorted({r.preflight.get("error") or r.preflight.get("skipped", "")
                           for r in needs})
            evidence += "; preflight not answered: " + "; ".join(errs)[:200]
    skipped = sum(1 for r in results if r.skipped == "time budget exhausted")
    if skipped:
        evidence += f"; {skipped} request(s) not sent (time budget)"
    return {"cors": cors, "preflight": preflight, "ops": ops, "evidence": evidence}


def browser_capable(doc: dict[str, Any], probes: Iterable[Probe]) -> dict[str, int]:
    """Catalog connector entries the page may run, by the V2 §12 host rule and by the
    per-operation verdicts."""
    by_host = by_op = total = 0
    for p in probes:
        total += 1
        h = doc["hosts"].get(p.host) or {}
        if h.get("cors") is True and (p.simple or h.get("preflight") is True):
            by_host += 1
        if (h.get("ops") or {}).get(p.name) is True:
            by_op += 1
    return {"entries": total, "browser_by_host_rule": by_host, "browser_by_operation": by_op}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--budget", type=float, default=900.0,
                        help="seconds after which no new request is started (default 900)")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=12, help="hosts probed at once")
    parser.add_argument("--hosts", default="",
                        help="comma-separated hosts to probe again; the others are kept from --out")
    parser.add_argument("--report", type=Path, help="also write every request and answer here")
    args = parser.parse_args(list(argv) if argv is not None else None)

    probes = build_probes()
    by_host: dict[str, list[Probe]] = defaultdict(list)
    for p in probes:
        by_host[p.host].append(p)
    wanted = {h.strip() for h in args.hosts.split(",") if h.strip()}
    previous: dict[str, Any] = {}
    if wanted and args.out.is_file():
        previous = json.loads(args.out.read_text(encoding="utf-8")).get("hosts", {})
    todo = sorted(h for h in by_host if not wanted or h in wanted)
    t0 = time.monotonic()
    deadline = t0 + args.budget
    print(f"{len(probes)} operations on {len(by_host)} hosts; probing {len(todo)}",
          file=sys.stderr, flush=True)
    lock = threading.Lock()
    results: dict[str, list[Result]] = {}

    def work(host: str) -> None:
        res = probe_host(host, by_host[host], timeout=args.timeout, deadline=deadline)
        with lock:
            results[host] = res
            print(f"  {host}: {len(res)} requests ({time.monotonic() - t0:.0f} s)",
                  file=sys.stderr, flush=True)

    # Hosts with many operations first, so the long queues start early.
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(work, sorted(todo, key=lambda h: (-len(by_host[h]), h))))

    hosts = {h: v for h, v in previous.items() if h in by_host and h not in results}
    for host, res in results.items():
        hosts[host] = judge(host, res)
    doc = {"schema": SCHEMA,
           "checked": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
           "origin": ORIGIN, "method": METHOD.format(timeout=args.timeout),
           "hosts": dict(sorted(hosts.items()))}
    counts = {"hosts": len(hosts),
              "cors_true": sum(1 for v in hosts.values() if v["cors"] is True),
              "cors_false": sum(1 for v in hosts.values() if v["cors"] is False),
              "cors_null": sum(1 for v in hosts.values() if v["cors"] is None),
              "preflight_true": sum(1 for v in hosts.values() if v["preflight"] is True),
              "preflight_false": sum(1 for v in hosts.values() if v["preflight"] is False),
              **browser_capable({"hosts": hosts}, probes)}
    doc["counts"] = counts
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                        encoding="utf-8")
    if args.report:
        rows = [{"op": r.probe.name, "host": h, "method": r.probe.method, "url": r.probe.url,
                 "simple": r.probe.simple, "status": r.status, "acao": r.acao, "hops": r.hops,
                 "error": r.error, "skipped": r.skipped, "preflight": r.preflight}
                for h, res in sorted(results.items()) for r in res]
        args.report.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
    print(json.dumps(counts), f"in {time.monotonic() - t0:.0f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
