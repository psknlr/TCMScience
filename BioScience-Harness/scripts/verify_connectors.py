"""Execute every operation of every public source and record what actually happened.

The connector table is a list of claims about other people's services. This script is
how the claims stay honest: it renders each operation from its own example arguments,
sends it through the real ``HTTPBackend`` (rate limits, retries and size caps included),
and writes one row per operation to ``data/connector_live_verification.csv``. A source is
shipped because its rows say SUCCEEDED, and the date on the row says when.

    PYTHONPATH=src python scripts/verify_connectors.py                # everything
    PYTHONPATH=src python scripts/verify_connectors.py --only hgnc ols # a subset
    PYTHONPATH=src python scripts/verify_connectors.py --no-write      # report only
    PYTHONPATH=src python scripts/verify_connectors.py --out X.csv --report DIR
                                                    # elsewhere, plus a validation report

``--report DIR`` also writes ``DIR/connector_live_report.json``: one record per operation
(``bioagent.validation``), PASS for an answer, BLOCKED for a service that refused or did not
answer, FAIL otherwise, read by ``bioagent validate summary`` with the other live reports.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.backends.http import HTTPBackend  # noqa: E402
from bioagent.providers.public_apis import SOURCES, PublicAPIProvider, render_call  # noqa: E402
from bioagent.providers.public_apis_tcm import PENDING_TCM_SOURCES  # noqa: E402
from bioagent.providers.supplement import load_pending  # noqa: E402

FIELDS = ("source", "source_name", "operation", "smoke", "status", "http_status", "attempts",
          "latency_ms", "result_hash", "error", "host", "license", "verified_at")
#: outcomes that are not a wrong answer: an answer with a documented shortfall, a service
#: that refused, was not there or did not answer in time (reported, never a pass)
_NOT_A_FAILURE = ("SUCCEEDED", "DEGRADED", "DENIED", "UNAVAILABLE", "TIMEOUT")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="source keys to verify")
    ap.add_argument("--out", default=str(ROOT / "data" / "connector_live_verification.csv"))
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--report", default="", help="also write DIR/connector_live_report.json")
    ap.add_argument("--fail-on", choices=("any", "fail"), default="any",
                    help="exit non-zero on any operation that did not succeed (any), or only "
                         "on a wrong answer (fail): a service that refused, was not there or "
                         "timed out is then reported BLOCKED without failing the run")
    args = ap.parse_args()

    # Pending connectors are verified only when named: they are not shipped, and a run
    # of everything should not start recording them.
    pending = tuple(p for p in PENDING_TCM_SOURCES + load_pending()
                    if args.only and p.key in args.only)
    sources = SOURCES + pending
    manifests = {m.id.rsplit(".", 1)[-1]: m for m in PublicAPIProvider(sources).discover()}
    backend = HTTPBackend(cache_dir=None, timeout_s=args.timeout, max_retries=2)
    rows: list[dict] = []
    failures = wrong = 0
    for source in sources:
        if args.only and source.key not in args.only:
            continue
        manifest = manifests[source.key]
        for op in source.operations:
            rendered = (op.render(**op.example) if source in pending
                        else render_call(source.key, op.name))
            cred = getattr(source, "credential", None)
            if cred is not None:
                # a person's credential, from the environment, as a header to the source's
                # own host; without it nothing is sent, and the row says why
                from bioagent.credentials import CredentialMissing, headers_for
                try:
                    rendered["headers"] = {**dict(rendered.get("headers") or {}),
                                           **headers_for(cred, source.base_url, source.host)}
                except CredentialMissing as exc:
                    failures += 1
                    rows.append({
                        "source": source.key, "source_name": source.name,
                        "operation": op.name, "smoke": op.name == source.smoke,
                        "status": "DENIED", "http_status": "", "attempts": 0,
                        "latency_ms": 0, "result_hash": "", "error": str(exc)[:160],
                        "host": source.host, "license": source.license,
                        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                    print(f"FAIL {source.key:22s} {op.name:22s} DENIED      -      0ms "
                          f"no credential ({cred.env} is not set)")
                    continue
            t0 = time.perf_counter()
            result = backend.invoke(manifest, use_cache=False, **rendered)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            meta = result.metadata or {}
            digest = ""
            if result.value is not None:
                blob = json.dumps(result.value, sort_keys=True, default=str).encode()
                digest = "sha256:" + hashlib.sha256(blob).hexdigest()[:12]
            ok = result.status.value == "SUCCEEDED"
            failures += 0 if ok else 1
            wrong += 0 if result.status.value in _NOT_A_FAILURE else 1
            rows.append({
                "source": source.key, "source_name": source.name, "operation": op.name,
                "smoke": op.name == source.smoke, "status": result.status.value,
                "http_status": meta.get("http_status") or "", "attempts": meta.get("attempts") or "",
                "latency_ms": latency_ms, "result_hash": digest,
                "error": (result.error or "")[:160].replace("\n", " "), "host": source.host,
                "license": source.license,
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            flag = "ok " if ok else "FAIL"
            print(f"{flag} {source.key:22s} {op.name:22s} {result.status.value:11s} "
                  f"{meta.get('http_status') or '-':>4} {latency_ms:6d}ms {(result.error or '')[:90]}")

    total = len(rows)
    print(f"\n{total - failures}/{total} operations succeeded across "
          f"{len({r['source'] for r in rows})} sources")
    if args.report:
        _write_report(rows, Path(args.report))
    if not args.no_write:
        out = Path(args.out)
        if args.only and out.exists():
            # A subset run replaces those sources' rows and keeps everyone else's: the
            # file stays a record of every shipped operation, not of the last run.
            with out.open(newline="", encoding="utf-8") as fh:
                kept = [r for r in csv.DictReader(fh) if r["source"] not in set(args.only)]
            rows = kept + rows
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {out}")
    return 1 if (wrong if args.fail_on == "fail" else failures) else 0


def _write_report(rows: list[dict], out: Path) -> None:
    from bioagent.validation import (BLOCKED, FAIL, PASS, UNVERIFIED, Record, report,
                                     write_report)
    from bioagent.validation.environment import fingerprint

    records = []
    for r in rows:
        rec = Record(f"connector.{r['source']}.{r['operation']}", "connector_operation", "",
                     input={"host": r["host"], "smoke": r["smoke"]},
                     provenance={"name": r["source_name"], "license": r["license"]},
                     reproduce=f"PYTHONPATH=src python scripts/verify_connectors.py --only "
                               f"{r['source']} --no-write")
        # an answer with a documented shortfall (DEGRADED: a truncated body) is not a pass;
        # a service that refused or was not there is BLOCKED; a wrong answer is a FAIL
        status = {"SUCCEEDED": PASS, "DEGRADED": UNVERIFIED, "DENIED": BLOCKED,
                  "UNAVAILABLE": BLOCKED, "TIMEOUT": BLOCKED}.get(r["status"], FAIL)
        rec.add("live_call", status, f"{r['status']} (HTTP {r['http_status'] or '-'}, "
                f"{r['latency_ms']} ms){': ' + r['error'] if r['error'] else ''}",
                http_status=r["http_status"], result_hash=r["result_hash"],
                verified_at=r["verified_at"])
        records.append(rec)
    write_report(out / "connector_live_report.json", report(
        "connector_live_verification", records, tier="live", environment=fingerprint()))
    print(f"wrote {out / 'connector_live_report.json'}")


if __name__ == "__main__":
    sys.exit(main())
