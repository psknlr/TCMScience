#!/usr/bin/env python3
"""Write the committed operational status of every connector operation (``tcmstudio.opstatus``).

    python3 studio/scripts/operational_status.py --audit-workbook AUDIT.xlsx
        # the observations of a production probe: the 'Gateway Operations' sheet of the availability
        # audit (2026-11-04): the scheduled direct run of 2026-11-03, the first production-gateway pass and its
        # retests, each with the gateway's and the upstream's HTTP status, the probe's category and its content check
    python3 studio/scripts/operational_status.py --live-run connector_live_verification.csv --record actions-<run id>
        # a scheduled direct run (scripts/verify_connectors.py's CSV), appended as route direct_upstream
    python3 studio/scripts/operational_status.py --check
        # the committed record is well formed and names only operations of the catalog

The record keeps every observation (history), never only the latest: a success after a failure marks recovery and
leaves the failure in the record. For an operation that failed in a run, the committed live verification
(BioScience-Harness/data/connector_live_verification.csv) adds its last success, so the record alone says when the
operation last worked. The record changes only by a reviewed commit; nothing here runs on a schedule by itself.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

STUDIO = Path(__file__).resolve().parents[1]
REPO = STUDIO.parent
OUT = STUDIO / "runner" / "src" / "tcmstudio" / "data" / "operational_status.json"
LIVE_RECORD = REPO / "BioScience-Harness" / "data" / "connector_live_verification.csv"

for src in (STUDIO / "runner" / "src", REPO / "BioScience-Harness" / "src", REPO / "PSH-Harness" / "src"):
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

SCHEMA = "tcmstudio.operational_status/1"
AUDIT = "audit-2026-11-04"
SCHEDULED = "actions-41631774775"
LIVE = "connector_live_verification"
RECORDS = {
    AUDIT: {"title": "Tool and database availability audit, 2026-11-04: every registered operation called with its "
                     "example through the production gateway (science.impf.ai/api/sources/request), the 24 that "
                     "failed retested once",
            "route": "production_gateway",
            "source": "TCMScience_Tool_and_Database_Availability_Audit_20261104.xlsx, sheet 'Gateway Operations'"},
    SCHEDULED: {"title": "Scheduled direct run of 2026-11-03 (connectors-live workflow, scripts/verify_connectors.py)",
                "route": "direct_upstream",
                "source": "https://github.com/psknlr/TCMScience/actions/runs/41631774775"},
    LIVE: {"title": "Committed live verification (scripts/verify_connectors.py, by a maintainer)",
           "route": "direct_upstream", "source": "BioScience-Harness/data/connector_live_verification.csv"},
}
#: Probe categories whose answer came back and was read as data (a content check may add a note).
_RECEIVED = {"PAYLOAD_RECEIVED", "HTML_REVIEW", "PAYLOAD_ERROR", "FORMAT_REVIEW", "NO_HITS_REVIEW"}


def _int(v: Any) -> int | None:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n


def _iso(v: Any) -> str | None:
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, str) and v.strip():
        return v.strip().replace(" ", "T") + ("Z" if re.fullmatch(r"[\d-]+T[\d:]+", v.strip().replace(" ", "T"))
                                              else "")
    return None


def _excerpt(v: Any) -> str:
    return " ".join(str(v or "").split())[:200]


def _error_type(excerpt: str) -> str | None:
    m = re.search(r"'type':\s*'([a-z_]+)'", excerpt)
    return m.group(1) if m else None


def _attempt(row: dict[str, Any], *, which: str) -> dict[str, Any] | None:
    """One production-gateway attempt of the audit (``first`` or ``retry``)."""
    if which == "first":
        at, gw, up, cat, excerpt = (row.get("First attempt (UTC)"), row.get("First gateway HTTP"),
                                    row.get("First upstream HTTP"), row.get("First probe category"),
                                    row.get("First excerpt"))
    else:
        at, gw, up, cat, excerpt = (row.get("Retry (UTC)"), row.get("Retry gateway HTTP"),
                                    row.get("Retry upstream HTTP"), row.get("Retry probe category"),
                                    row.get("Retry excerpt"))
    when = _iso(at)
    if not when or not cat:
        return None
    gw_i, up_i, text = _int(gw), _int(up), _excerpt(excerpt)
    ob: dict[str, Any] = {"at": when, "route": "production_gateway", "record": AUDIT}
    finding = str(row.get("Audit finding") or "")
    if cat in _RECEIVED and which == "first" and finding == "Error payload reported as success":
        # SIGNOR: HTTP 200 and a PHP fatal error, which the dispatcher of the day reported as succeeded
        ob.update(outcome="failed", layer="upstream_content",
                  detail="the upstream answered HTTP 200 with a server error report, not data: " + text)
    elif cat in _RECEIVED:
        ob["outcome"] = "succeeded"
        note = _excerpt(row.get("Content check")) if which == "first" else ""
        if note and cat != "PAYLOAD_RECEIVED":
            ob["note"] = note
    elif cat == "UPSTREAM_HTTP":
        ob.update(outcome="failed", layer="upstream_http", detail=text)
    elif cat == "GATEWAY_REFUSED":
        ob.update(outcome="failed", layer="gateway", detail=text)
        et = _error_type(text)
        if et:
            ob["error_type"] = et
    else:                                             # TRANSPORT_ERROR: no answer at all
        ob.update(outcome="failed", layer="transport", detail=text)
    if gw_i:
        ob["gateway_http"] = gw_i
    if up_i:
        ob["upstream_http"] = up_i
    return ob


def _direct(row: dict[str, Any]) -> dict[str, Any] | None:
    """The scheduled direct run of 2026-11-03, as the audit's workbook records it (a date, not a time)."""
    status = str(row.get("Nov 3 direct status") or "")
    if not status:
        return None
    ob: dict[str, Any] = {"at": "2026-11-03", "route": "direct_upstream", "record": SCHEDULED}
    up = _int(row.get("Nov 3 upstream HTTP"))
    if status == "SUCCEEDED":
        ob["outcome"] = "succeeded"
    else:
        ob.update(outcome="failed", layer="transport" if status == "TIMEOUT" else
                  "upstream_http" if up else "transport", detail=status)
    if up:
        ob["upstream_http"] = up
    return ob


def _live_rows(path: Path = LIVE_RECORD) -> dict[str, dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return {f"connector.{r['source']}.{r['operation']}": r for r in csv.DictReader(fh)}
    except FileNotFoundError:
        return {}


def _from_live_row(r: dict[str, str], record: str) -> dict[str, Any] | None:
    at = (r.get("verified_at") or "").strip()
    if not at:
        return None
    ob: dict[str, Any] = {"at": at, "route": "direct_upstream", "record": record}
    if r.get("status") == "SUCCEEDED":
        ob["outcome"] = "succeeded"
    else:
        ob.update(outcome="failed", layer="transport" if r.get("status") in ("TIMEOUT", "UNAVAILABLE")
                  else "upstream_http" if r.get("http_status") else "transport",
                  detail=_excerpt(r.get("error")) or str(r.get("status")))
    if _int(r.get("http_status")):
        ob["upstream_http"] = _int(r.get("http_status"))
    return ob


def read_workbook(path: Path) -> list[dict[str, Any]]:
    import openpyxl                                   # only for this import; not a runtime dependency
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb["Gateway Operations"].iter_rows(values_only=True))
    header_at = next(i for i, r in enumerate(rows) if r and r[0] == "Operation ID")
    header = rows[header_at]
    return [dict(zip(header, r)) for r in rows[header_at + 1:] if r and r[0]]


def from_audit(rows: list[dict[str, Any]], live: dict[str, dict[str, str]] | None = None) -> dict[str, list[dict]]:
    live = _live_rows() if live is None else live
    ops: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        oid = str(row["Operation ID"])
        obs = [o for o in (_direct(row), _attempt(row, which="first"), _attempt(row, which="retry")) if o]
        if any(o["outcome"] != "succeeded" for o in obs) and oid in live:
            # when it last worked, by the committed verification, for an operation that failed in these runs
            earlier = _from_live_row(live[oid], LIVE)
            if earlier and earlier["outcome"] == "succeeded":
                obs.insert(0, earlier)
        ops[oid] = sorted(obs, key=lambda o: o["at"])
    return ops


def load(path: Path = OUT) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": SCHEMA, "records": {}, "operations": {}}


def merge(doc: dict[str, Any], ops: dict[str, list[dict]], records: dict[str, dict]) -> dict[str, Any]:
    """``ops`` added to ``doc``: an observation already present (same time, route and record) is not repeated."""
    out = {k: v for k, v in doc.items() if k not in ("operations", "records")}
    out.setdefault("about", "Observations of each connector operation's availability, by route; history kept. "
                            "Written by studio/scripts/operational_status.py, read by tcmstudio.opstatus.")
    out["schema"] = SCHEMA
    out["records"] = {**doc.get("records", {}), **records}
    merged = {k: list(v) for k, v in doc.get("operations", {}).items()}
    for oid, obs in ops.items():
        have = {(o.get("at"), o.get("route"), o.get("record")) for o in merged.get(oid, ())}
        merged.setdefault(oid, []).extend(o for o in obs if (o["at"], o["route"], o["record"]) not in have)
        merged[oid].sort(key=lambda o: str(o.get("at")))
    out["operations"] = dict(sorted(merged.items()))
    return out


def dump(doc: dict[str, Any]) -> str:
    # one operation per line: reviewable in a diff, small in the bundle
    head = {k: v for k, v in doc.items() if k != "operations"}
    lines = ["{", *(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}," for k, v in head.items()),
             '  "operations": {']
    items = list(doc["operations"].items())
    for i, (oid, obs) in enumerate(items):
        lines.append(f"    {json.dumps(oid)}: {json.dumps(obs, ensure_ascii=False, sort_keys=True)}"
                     + ("," if i < len(items) - 1 else ""))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def problems(doc: dict[str, Any]) -> list[str]:
    from tcmstudio.catalog import entry_ids
    from tcmstudio.opstatus import LAYERS, ROUTES
    out = []
    if doc.get("schema") != SCHEMA:
        out.append(f"schema is {doc.get('schema')!r}, not {SCHEMA}")
    ids = set(entry_ids())
    records = doc.get("records") or {}
    for oid, obs in (doc.get("operations") or {}).items():
        if oid not in ids:
            out.append(f"{oid}: not an entry of the catalog")
        if [o.get("at") for o in obs] != sorted(o.get("at") for o in obs):
            out.append(f"{oid}: observations are not in time order")
        for o in obs:
            if o.get("route") not in ROUTES:
                out.append(f"{oid}: route {o.get('route')!r}")
            if o.get("outcome") not in ("succeeded", "failed"):
                out.append(f"{oid}: outcome {o.get('outcome')!r}")
            if o.get("outcome") == "failed" and o.get("layer") not in LAYERS:
                out.append(f"{oid}: a failure without a known layer ({o.get('layer')!r})")
            if o.get("record") not in records:
                out.append(f"{oid}: record {o.get('record')!r} is not described under records")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit-workbook", type=Path, help="the availability audit's workbook (.xlsx)")
    ap.add_argument("--live-run", type=Path, help="a verify_connectors.py CSV of a scheduled direct run")
    ap.add_argument("--record", help="the record id of --live-run (e.g. actions-<run id>)")
    ap.add_argument("--record-title", default="", help="what --live-run was")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true", help="validate the committed record; write nothing")
    args = ap.parse_args()

    doc = load(args.out)
    if args.check:
        bad = problems(doc)
        for p in bad:
            print(p)
        n = sum(len(v) for v in doc.get("operations", {}).values())
        print(f"operational status: {len(doc.get('operations', {}))} operations, {n} observations; "
              f"{'OK' if not bad else f'{len(bad)} problem(s)'}")
        return 1 if bad else 0
    if args.audit_workbook:
        doc = merge(doc, from_audit(read_workbook(args.audit_workbook)), dict(RECORDS))
    if args.live_run:
        if not args.record:
            ap.error("--live-run needs --record")
        rows = _live_rows(args.live_run)
        ops = {oid: [o] for oid, r in rows.items() if (o := _from_live_row(r, args.record))}
        doc = merge(doc, ops, {args.record: {"title": args.record_title or "scheduled direct run",
                                             "route": "direct_upstream", "source": str(args.live_run.name)}})
    if not (args.audit_workbook or args.live_run):
        ap.error("nothing to do: give --audit-workbook, --live-run or --check")
    bad = problems(doc)
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dump(doc), encoding="utf-8")
    n = sum(len(v) for v in doc["operations"].values())
    print(f"wrote {args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out}: "
          f"{len(doc['operations'])} operations, {n} observations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
