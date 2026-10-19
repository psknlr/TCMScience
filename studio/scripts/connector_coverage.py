#!/usr/bin/env python3
"""Every public-database connector operation, by the level at which it has been verified.

    python3 studio/scripts/connector_coverage.py            # rewrites studio/docs/compat/connectors.{csv,md}
    python3 studio/scripts/connector_coverage.py --check    # fails when the committed files are out of date

One row per connector entry of the tool catalog (``tcmstudio.catalog``), one column per level. The levels are kept
apart because they show different things, and a lower one is never counted as a higher one:

  mock_transport    run by this script, offline: the operation is called through Studio's dispatcher the way the
                    browser calls it (its Python policy, request rendering and parsing in this interpreter; the
                    transport is ``tcmstudio.browser_http.GatewayOpener``, whose ``send`` is a stand-in that answers
                    HTTP 200 with an empty body of the requested type). PASS means exactly one request reached the
                    transport, on the operation's declared host, with the method, URL and body that
                    ``BioScience-Harness/scripts/verify_connectors.py`` sends for the same example, and the answer
                    came back as a succeeded envelope. It says nothing about the upstream service, nor about the
                    edge gateway's own checks (``studio/edge/src/sources.js``), which are not run here.
  offline_refusal   run by this script: with web access off, the call is refused with ``network_off`` before any
                    request reaches the transport.
  live_upstream     read from the committed record ``BioScience-Harness/data/connector_live_verification.csv``:
                    the real upstream answered the operation's example through ``HTTPBackend``
                    (``scripts/verify_connectors.py``), with the date of that run. No row is ``never``.
  live_gateway      the operation called from a browser through the deployed gateway to the real upstream. Only a
                    committed record could show it, and none exists: every row says ``not verified``.
  scheduled         a workflow that re-runs every operation against the real upstreams on a schedule (read from
                    ``.github/workflows``). Its runs upload a report and commit nothing, so the repository records
                    that the schedule exists, never that a scheduled run passed.

The output is deterministic: dates come only from the committed record, never from the clock, and no age is computed,
so ``--check`` gives the same answer on any day.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import io
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent
OUT = STUDIO / "docs" / "compat"
LIVE_RECORD = "BioScience-Harness/data/connector_live_verification.csv"
WORKFLOWS = REPO / ".github" / "workflows"

for src in (STUDIO / "runner" / "src", REPO / "BioScience-Harness" / "src", REPO / "PSH-Harness" / "src"):
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

LEVELS = ("mock_transport", "offline_refusal", "live_upstream", "live_gateway", "scheduled")
FIELDS = ["id", "source", "operation", "host", "transport", "mock_transport", "mock_detail", "offline_refusal",
          "live_upstream", "live_upstream_at", "live_smoke", "live_gateway", "scheduled"]
# verify_connectors.py writes the call's ExecutionStatus; anything else is not a pass (as tcmstudio.catalog reads it)
_LIVE_STATUS = {"SUCCEEDED": "PASS", "FAILED": "FAIL", "BLOCKED": "BLOCKED"}
NOT_VERIFIED = "not verified"


def catalog() -> dict:
    from tcmstudio.catalog import build_catalog
    return build_catalog("browser", probe=False)


def live_rows(path: Path | None = None) -> dict[tuple[str, str], dict[str, str]]:
    """The committed live verification, by (source, operation)."""
    p = path or REPO / LIVE_RECORD
    if not p.is_file():
        return {}
    with p.open(encoding="utf-8", newline="") as fh:
        return {(r.get("source", ""), r.get("operation", "")): r for r in csv.DictReader(fh)}


def schedules(root: Path = WORKFLOWS) -> list[dict[str, str]]:
    """Workflows that run scripts/verify_connectors.py on a schedule: [{file, cron}], sorted."""
    out = []
    for p in sorted(root.glob("*.y*ml")) if root.is_dir() else ():
        text = p.read_text(encoding="utf-8")
        if "verify_connectors.py" not in text or not re.search(r"^\s*schedule\s*:", text, re.M):
            continue
        for cron in re.findall(r"""^\s*-\s*cron\s*:\s*["']([^"']+)["']""", text, re.M):
            out.append({"file": p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else p.name, "cron": cron})
    return out


def _body_for(accept: str) -> tuple[bytes, str]:
    a = (accept or "").lower()
    if "json" in a:
        return b"{}", "application/json"
    if "xml" in a:
        return b'<?xml version="1.0"?><empty/>', "application/xml"
    return b"", "text/plain"


@contextlib.contextmanager
def mock_transport(answer: Callable[[dict], dict] | None = None) -> Iterator[list[dict]]:
    """Every HTTPBackend request in this block goes to a stand-in for the browser's gateway transport instead of a
    socket; the packets it was handed are collected. The pacing between requests is skipped (it waits for nothing
    here). Restored on exit."""
    from bioagent.backends import http as H
    from tcmstudio.browser_http import GatewayOpener

    packets: list[dict] = []

    def send(packet: str, timeout_ms: int) -> str:
        p = json.loads(packet)
        packets.append(p)
        if answer is not None:
            return json.dumps(answer(p))
        headers = {k.lower(): v for k, v in (p.get("headers") or {}).items()}
        raw, kind = _body_for(headers.get("accept", ""))
        return json.dumps({"status": 200, "headers": {"Content-Type": kind},
                           "body_base64": base64.b64encode(raw).decode(), "url": p["url"]})

    factory, wait = H.HTTPBackend.browser_opener_factory, H._RateLimiter.wait
    H.HTTPBackend.browser_opener_factory = staticmethod(lambda allowed: GatewayOpener(allowed, send))
    H._RateLimiter.wait = lambda self, host, path="": 0.0
    try:
        yield packets
    finally:
        H.HTTPBackend.browser_opener_factory = factory
        H._RateLimiter.wait = wait


def _request(p: Mapping[str, Any]) -> tuple[str, str, str | None]:
    return (p.get("url", ""), p.get("method", ""), p.get("body_base64"))


def check_operations(entries: list[dict], answer: Callable[[dict], dict] | None = None) -> dict[str, dict[str, str]]:
    """{entry id: {mock_transport, mock_detail, offline_refusal}} for the connector entries given."""
    from bioagent.backends.http import HTTPBackend
    from bioagent.providers.public_apis import PublicAPIProvider, render_call
    from tcmstudio import dispatch

    manifests = {m.id.rsplit(".", 1)[-1]: m for m in PublicAPIProvider().discover()}
    out: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="tcmstudio-coverage-") as state, mock_transport(answer) as packets:
        backend = HTTPBackend(cache_dir=None, max_retries=1)       # one attempt: the stand-in answers at once
        on = {"where": "browser", "network": True, "state_root": state, "project_id": "coverage"}
        off = {**on, "network": False}
        for e in entries:
            c = e["connector"]
            row = {"mock_transport": "NOT_APPLICABLE", "mock_detail": "no example to call it with",
                   "offline_refusal": "NOT_APPLICABLE"}
            out[e["id"]] = row
            if e.get("example") is None:
                continue
            # what verify_connectors.py sends for this example (its own rendering through HTTPBackend)
            n = len(packets)
            backend.invoke(manifests[c["key"]], use_cache=False, **render_call(c["key"], c["operation"]))
            expected = packets[n:]
            # the same example through Studio's dispatcher, as the browser calls it
            n = len(packets)
            env = dispatch.call(e["id"], e["example"], on)
            sent = packets[n:]
            problems = []
            if len(expected) != 1:
                problems.append(f"verify_connectors' rendering sent {len(expected)} requests")
            if len(sent) != 1:
                problems.append(f"{len(sent)} requests reached the transport, not 1")
            elif expected and _request(sent[0]) != _request(expected[0]):
                problems.append(f"sent {sent[0].get('method')} {sent[0].get('url')}, not the request "
                                f"verify_connectors sends ({expected[0].get('method')} {expected[0].get('url')})")
            hosts = {re.sub(r"^[a-z]+://([^/:]+).*$", r"\1", p.get("url", "")) for p in sent}
            if sent and hosts != {c["host"]}:
                problems.append(f"sent to {', '.join(sorted(hosts))}, not the declared host {c['host']}")
            if env.get("status") != "succeeded":
                problems.append(f"the envelope is {env.get('status')}: {(env.get('error') or {}).get('message', '')}"[:200])
            elif (env.get("receipt") or {}).get("where") != "browser":
                problems.append("the receipt does not say browser")
            row["mock_transport"] = "FAIL" if problems else "PASS"
            row["mock_detail"] = "; ".join(problems) if problems else (
                f"{sent[0].get('method')} to {c['host']}, the request verify_connectors sends; 200 came back succeeded")
            n = len(packets)
            refused = dispatch.call(e["id"], e["example"], off)
            row["offline_refusal"] = ("PASS" if refused.get("status") == "failed"
                                      and (refused.get("error") or {}).get("type") == "network_off"
                                      and len(packets) == n else "FAIL")
    return out


def rows(doc: dict | None = None, *, live: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
         scheduled: list[dict[str, str]] | None = None,
         checked: Mapping[str, Mapping[str, str]] | None = None) -> list[dict[str, str]]:
    doc = doc or catalog()
    entries = sorted((e for e in doc["entries"] if e["kind"] == "connector"), key=lambda e: e["id"])
    live = live_rows() if live is None else live
    scheduled = schedules() if scheduled is None else scheduled
    checked = check_operations(entries) if checked is None else checked
    plan = ("; ".join(f"defined: {s['file']} (cron '{s['cron']}')" for s in scheduled)
            + "; no scheduled run is recorded in the repository") if scheduled else "none"
    out = []
    for e in entries:
        c = e["connector"]
        rec = live.get((c["key"], c["operation"]))
        mock = checked.get(e["id"]) or {}
        out.append({
            "id": e["id"], "source": c["key"], "operation": c["operation"], "host": c["host"],
            "transport": c.get("transport", ""),
            "mock_transport": mock.get("mock_transport", "UNVERIFIED"), "mock_detail": mock.get("mock_detail", ""),
            "offline_refusal": mock.get("offline_refusal", "UNVERIFIED"),
            "live_upstream": _LIVE_STATUS.get(rec.get("status", ""), "UNVERIFIED") if rec else "never",
            "live_upstream_at": (rec.get("verified_at") or "") if rec else "",
            "live_smoke": ("yes" if str(rec.get("smoke")) == "True" else "no") if rec else "",
            "live_gateway": NOT_VERIFIED, "scheduled": plan,
        })
    return out


def to_csv(table: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(table)
    return buf.getvalue()


def _span(dates: list[str]) -> str:
    days = sorted({d[:10] for d in dates if d})
    if not days:
        return "—"
    return days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"


def _count(values: list[str]) -> str:
    c = Counter(values)
    return ", ".join(f"{k} {n}" for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def summary(table: list[dict[str, str]], scheduled: list[dict[str, str]] | None = None, *,
            record: str | None = None) -> str:
    """The Markdown report. ``record`` names the live record read when it is not the committed one (a fresh run's
    CSV, reported by the monthly workflow): it is then named as given, and it is not the repository's record."""
    scheduled = schedules() if scheduled is None else scheduled
    source = (f"[`{LIVE_RECORD}`](../../../{LIVE_RECORD}), written by `scripts/verify_connectors.py` and changed "
              "only by a reviewed commit") if record is None else (
        f"`{record}`, a run of `scripts/verify_connectors.py` that is not the committed record")
    sources = sorted({r["source"] for r in table})
    lines = [
        "# Connector coverage by verification level", "",
        "Generated by `studio/scripts/connector_coverage.py` from the tool catalog and the committed records; one row "
        "per operation in [`connectors.csv`](connectors.csv). `--check` keeps it current "
        "(`studio/runner/tests/test_connector_coverage.py`).", "",
        f"Operations: {len(table)}, from {len(sources)} sources. Each level is reported on its own. A mock result is "
        "never counted as a live one, and a live call made from a maintainer's machine is not a call through the "
        "deployed site.", "",
        "| Level | What it shows | Where it comes from | Operations |", "|---|---|---|---|",
        "| `mock_transport` | the operation, called through Studio's dispatcher as the browser calls it, sends exactly "
        "one request to its declared host, the same method, URL and body as `verify_connectors.py`, and a stand-in "
        "transport's HTTP 200 comes back as a succeeded envelope. Nothing about the upstream service | this script, "
        f"offline, on every `--check` | {_count([r['mock_transport'] for r in table])} |",
        "| `offline_refusal` | with web access off, the call is refused (`network_off`) before any request | this "
        f"script, offline | {_count([r['offline_refusal'] for r in table])} |",
        "| `live_upstream` | the real upstream answered the operation's example through `HTTPBackend`, on the date "
        f"recorded | {source} | {_count([r['live_upstream'] for r in table])}; dated "
        f"{_span([r['live_upstream_at'] for r in table])} |",
        "| `live_gateway` | the operation called from a browser through the deployed gateway to the real upstream | no "
        f"committed record exists | {_count([r['live_gateway'] for r in table])} |",
    ]
    if scheduled:
        files = ", ".join(f"[`{s['file']}`](../../../{s['file']}) (cron `{s['cron']}`)" for s in scheduled)
        lines.append(f"| `scheduled` | every operation re-run against the real upstreams on a schedule | {files}: "
                     "uploads its report, commits nothing | defined; no scheduled run is recorded in the repository |")
    else:
        lines.append("| `scheduled` | every operation re-run against the real upstreams on a schedule | no workflow "
                     "schedules `verify_connectors.py` | none |")
    lines += [
        "",
        "Not covered by any level here: the edge gateway's own checks (`studio/edge/src/sources.js`: origins, "
        "templates, hosts, redirects, sizes, limits). They are unit-tested against a small synthetic registry "
        "(`studio/edge/test/sources.test.js`), and `studio/e2e/browser-access.spec.mjs` sends one operation "
        "(`connector.mygene.gene`) through them with a mocked upstream when Pyodide's CDN is reachable; no record of "
        "that run is committed. The post-deploy check (`studio/edge/scripts/check.mjs --require-sources`) reads the "
        "deployed gateway's health counts and calls no operation.", "",
        "## By source", "",
        "| Source | Host | Operations | `mock_transport` | `offline_refusal` | `live_upstream` | Live dates | "
        "`live_gateway` |", "|---|---|---:|---|---|---|---|---|",
    ]
    by: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in table:
        by[r["source"]].append(r)
    for key in sources:
        rs = by[key]
        lines.append(f"| `{key}` | {', '.join(sorted({r['host'] for r in rs}))} | {len(rs)} | "
                     f"{_count([r['mock_transport'] for r in rs])} | {_count([r['offline_refusal'] for r in rs])} | "
                     f"{_count([r['live_upstream'] for r in rs])} | {_span([r['live_upstream_at'] for r in rs])} | "
                     f"{_count([r['live_gateway'] for r in rs])} |")
    failing = [r for r in table if r["mock_transport"] != "PASS" or r["offline_refusal"] != "PASS"
               or r["live_upstream"] != "PASS"]
    lines += ["", "## Operations that are not PASS at a level this repository can check", ""]
    if failing:
        lines += ["| Operation | `mock_transport` | `offline_refusal` | `live_upstream` | Detail |", "|---|---|---|---|---|"]
        lines += [f"| `{r['id']}` | {r['mock_transport']} | {r['offline_refusal']} | {r['live_upstream']} | "
                  f"{r['mock_detail'].replace('|', '/')} |" for r in failing]
    else:
        lines.append("None: every operation is PASS for `mock_transport`, `offline_refusal` and `live_upstream`. "
                     "`live_gateway` is not verified for any of them.")
    lines += ["", "Columns of `connectors.csv`: " + ", ".join(f"`{f}`" for f in FIELDS) + ".", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail when the committed files differ")
    ap.add_argument("--live-record", metavar="CSV", help="report a fresh run of verify_connectors.py (its --out) "
                    "instead of the committed record; needs --out, and never touches studio/docs/compat")
    ap.add_argument("--out", metavar="DIR", help="where to write connectors.csv and connectors.md (with --live-record)")
    args = ap.parse_args()
    if bool(args.live_record) != bool(args.out) or (args.live_record and args.check):
        ap.error("--live-record and --out go together, and not with --check: the committed report reads the "
                 "committed record")
    if args.live_record:
        record = Path(args.live_record)
        table = rows(live=live_rows(record))
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "connectors.csv").write_text(to_csv(table), encoding="utf-8")
        (out / "connectors.md").write_text(summary(table, record=str(args.live_record)), encoding="utf-8")
        print(f"wrote {out / 'connectors.csv'} and {out / 'connectors.md'} from {record}")
        return 0
    table = rows()
    files = {OUT / "connectors.csv": to_csv(table), OUT / "connectors.md": summary(table)}
    if args.check:
        stale = [p for p, text in files.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for p in stale:
            print(f"out of date: {p.relative_to(REPO)} (run python3 studio/scripts/connector_coverage.py)")
        bad = [r["id"] for r in table if r["mock_transport"] == "FAIL" or r["offline_refusal"] == "FAIL"]
        for i in bad:
            print(f"FAIL: {i}")
        if stale or bad:
            return 1
        # what was checked, so a CI log says it (the counts are the report's, the dates the committed record's)
        print(f"connector coverage: {len(table)} operations; mock_transport "
              f"{_count([r['mock_transport'] for r in table])}; offline_refusal "
              f"{_count([r['offline_refusal'] for r in table])}; live_upstream "
              f"{_count([r['live_upstream'] for r in table])} (dated {_span([r['live_upstream_at'] for r in table])}); "
              f"live_gateway {_count([r['live_gateway'] for r in table])}")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for p, text in files.items():
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
