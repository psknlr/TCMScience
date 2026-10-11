"""``bioagent validate``: the validation reports, written as files.

    bioagent validate data --out DIR [--live] [--work DIR] [--datasets npass cmaup lotus]
        live_download_validation.json        every reviewed research raw file, stage by stage
        database_readiness_report.json       every catalogued source, plus the hub datasets
                                             built here from those files
        research_pipeline_report.json        the research snapshots and the loop run on them
        scientific_reproducibility_report.json   rebuilds that must give the same digests
    bioagent validate deps --out DIR         dependency_readiness_report.json
    bioagent validate databases --out DIR    database_readiness_report.json (no downloads)
    bioagent validate datasets --out DIR [--live] [--root HUB] [--datasets KEY ...]
        hub_dataset_report.json              every hub dataset: fetched as Studio's tcmdb.fetch
                                             job does, verified, built, checked, queried
    bioagent validate record --report DIR/live_download_validation.json [DIR2/hub_dataset_report.json ...]
                                             fold live runs into the committed record
    bioagent validate benchmark --out DIR [--live] [--raw RAW] [--repeat]
        research_pipeline_report.json        the Guizhi Decoction and palpitations benchmark,
                                             twelve stages, with one hashed artifact each
        scientific_reproducibility_report.json   (--repeat) the artifacts of a second run

Without ``--live`` nothing is requested from any server: the network checks are SKIPPED
and only files already in the work directory are checked. ``--strict`` exits non-zero on
any FAIL or BLOCKED, the setting a dedicated live-data job uses; otherwise only a FAIL does.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import BLOCKED, FAIL, PASS, Record, report, write_report

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bioagent validate", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="what", required=True)
    d = sub.add_parser("data", help="the real files, the hub datasets, the research snapshots")
    d.add_argument("--out", required=True, help="where the reports go")
    d.add_argument("--work", default="", help="raw files, hub and snapshots (default: OUT/work)")
    d.add_argument("--live", action="store_true", help="request the files from their servers")
    d.add_argument("--files", nargs="*", default=None, help="only these raw files")
    d.add_argument("--datasets", nargs="*", default=["npass", "cmaup", "lotus"])
    d.add_argument("--no-research", action="store_true", help="skip snapshots and the loop")
    d.add_argument("--formula", action="append", default=[],
                   help="also put this formula in the herb layer (e.g. 桂枝汤《伤寒论》)")
    d.add_argument("--no-rebuild", action="store_true", help="skip the rebuild comparisons")
    d.add_argument("--strict", action="store_true", help="exit non-zero on BLOCKED too")
    x = sub.add_parser("deps", help="optional scientific packages: import, version, calculation")
    x.add_argument("--out", required=True)
    x.add_argument("--strict", action="store_true")
    b = sub.add_parser("databases", help="every catalogued source, without downloads")
    b.add_argument("--out", required=True)
    b.add_argument("--tcmdb-root", default="")
    m = sub.add_parser("benchmark", help="the Guizhi Decoction and palpitations benchmark")
    m.add_argument("--out", required=True, help="where the reports go")
    m.add_argument("--work", default="", help="snapshots, the research run and the artifacts "
                                              "(default: OUT/work)")
    m.add_argument("--raw", default="", help="the research raw files (default: WORK/raw; "
                                             "`validate data --work W` leaves them in W/raw)")
    m.add_argument("--snapshots", default="", help="snapshots built elsewhere; their data "
                                                   "provenance stays UNVERIFIED")
    m.add_argument("--ledger", default="", help="their ledger (default: SNAPSHOTS/ledger.jsonl)")
    m.add_argument("--live", action="store_true",
                   help="download the files that are missing and query the literature")
    m.add_argument("--repeat", action="store_true",
                   help="run again in a fresh state directory and compare the artifacts")
    m.add_argument("--strict", action="store_true", help="exit non-zero on BLOCKED too")
    v = sub.add_parser("summary", help="every report under the given paths, as one Markdown table")
    v.add_argument("paths", nargs="+", help="report files or directories holding them")
    v.add_argument("--rows", type=int, default=40, help="how many non-passing records to list")
    h = sub.add_parser("datasets", help="every hub dataset: fetched, verified, built, checked "
                                        "and queried")
    h.add_argument("--out", required=True, help="where the report goes")
    h.add_argument("--root", default="", help="the data hub (default: OUT/hub)")
    h.add_argument("--datasets", nargs="*", default=None,
                   help="these keys (default: every dataset of the --access modes)")
    h.add_argument("--access", nargs="*", default=["download", "live", "manual"],
                   choices=["download", "live", "manual"])
    h.add_argument("--live", action="store_true", help="request the files from their servers")
    h.add_argument("--confirm-large", action="store_true",
                   help="allow files above the downloader's size gate (an operator's decision)")
    h.add_argument("--list", action="store_true", help="print the selected keys as JSON, run nothing")
    h.add_argument("--strict", action="store_true", help="exit non-zero on BLOCKED too")
    k = sub.add_parser("reachability",
                       help="the catalogue's sources asked once, over the network, whether "
                            "they answer from here (a card's 'unreachable' re-checked)")
    k.add_argument("--out", required=True, help="where the report goes")
    k.add_argument("--access", nargs="*", default=["unreachable"],
                   help="the catalogue access classes to ask (default: unreachable)")
    r = sub.add_parser("record", help="fold a live run's results into the committed record")
    r.add_argument("--report", required=True, nargs="+",
                   help="live_download_validation.json and/or hub_dataset_report.json files")
    r.add_argument("--record", default="", help="the record to update (default: the shipped "
                                                "bioagent/data/live_download_verification.json)")
    return p


def _exit(records: Sequence[Record], strict: bool) -> int:
    bad = {FAIL, BLOCKED} if strict else {FAIL}
    return 1 if any(r.status in bad for r in records) else 0


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _data(a: argparse.Namespace) -> int:
    from ..sources import pipeline
    from ..tcmdb import TCMDataHub
    from . import data, databases
    from .environment import fingerprint

    out = Path(a.out)
    work = Path(a.work) if a.work else out / "work"
    raw = work / "raw"
    env = fingerprint({"work": work})
    tier = "live" if a.live else "offline"
    specs = pipeline.required_files()
    if a.files:
        specs = [s for s in specs if s.filename in set(a.files)]
    files = data.validate_files(specs, raw, live=a.live, log=_log)
    write_report(out / "live_download_validation.json", report(
        "live_download_validation", files, tier=tier, environment=env,
        notes=["A file is PASS only when its server answered, the bytes are the reviewed ones "
               "and every applicable check passed; a check that could not run is BLOCKED or "
               "SKIPPED and is never counted as passed."]))
    hub = TCMDataHub(work / "tcmdb")
    hub_records = [data.validate_hub_dataset(hub, key, raw, rebuild=not a.no_rebuild, log=_log)
                   for key in a.datasets]
    hub_records.append(data.integration_check(hub, a.datasets))
    static = databases.readiness(hub)
    write_report(out / "database_readiness_report.json", report(
        "database_readiness_report", static + hub_records, tier=tier, environment=env,
        notes=["One record per catalogued source (kind 'database'), then the hub datasets built "
               "here from the real files (kind 'hub_dataset') and their integration."]))
    research: list[Record] = []
    if not a.no_research:
        research = data.validate_research(raw, work, formulas=a.formula, log=_log)
        write_report(out / "research_pipeline_report.json", report(
            "research_pipeline_report", research, tier=tier, environment=env))
    repro = _reproducibility(hub_records, research, work)
    write_report(out / "scientific_reproducibility_report.json", report(
        "scientific_reproducibility_report", repro, tier=tier, environment=env))
    every = files + hub_records + research + repro
    _summary(every)
    return _exit(every, a.strict)


def _reproducibility(hub_records: Sequence[Record], research: Sequence[Record],
                     work: Path) -> list[Record]:
    """Rebuilds that must give what the first build gave."""
    from ..sources import pipeline

    out = []
    for r in hub_records:
        if r.kind != "hub_dataset":
            continue
        rec = Record(r.component, "reproducibility", r.version, reproduce=r.reproduce)
        check = r.check("check")
        build = r.check("build")
        if check is None or build is None or build.status != PASS:
            rec.add("rebuild_digest", "SKIPPED", "the dataset was not built")
        elif not check.evidence.get("rebuilt"):
            rec.add("rebuild_digest", "SKIPPED", "run without the rebuild comparison")
        else:
            changed = [p for p in check.evidence.get("problems", ())
                       if "rebuild" in p.lower() or "two builds" in p.lower()]
            rec.add("rebuild_digest", FAIL if changed else PASS,
                    "; ".join(changed) if changed else
                    "two rebuilds from the same files give the same relations digest",
                    digest=build.evidence.get("digest"))
        out.append(rec)
    snap = next((r for r in research if r.kind == "research_snapshots"), None)
    if snap is not None and snap.status in (PASS, "UNVERIFIED"):
        rec = Record("research.snapshots", "reproducibility", "",
                     reproduce="bioagent snapshots build … (twice)")
        lake = work / "lake"
        again = pipeline.build_research_snapshots(lake / "raw", work / "rebuild", work /
                                                  "rebuild" / "ledger.jsonl", offline=True,
                                                  log=lambda m: None)
        first = {k: v["snapshot_id"] for k, v in (snap.provenance.get("snapshots") or {}).items()}
        second = {k: v["snapshot_id"] for k, v in (again.get("snapshots") or {}).items()}
        same = first == second and bool(first)
        rec.add("snapshot_ids", PASS if same else FAIL,
                "a second build from the same files gives the same snapshot ids" if same else
                f"the ids differ: {sorted(k for k in first if first.get(k) != second.get(k))}",
                first=first, second=second)
        out.append(rec)
    return out


def _summary(records: Sequence[Record]) -> None:
    from collections import Counter

    counts = Counter(r.status for r in records)
    _log("summary: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for r in records:
        if r.status not in (PASS, "NOT_APPLICABLE"):
            _log(f"  {r.status:10} {r.component}: {r.failure_reason or ''}"[:400])


def _record(a: argparse.Namespace) -> int:
    """Fold live runs into the committed record: research raw files by name (``files``),
    hub datasets by key (``datasets``). An offline report verifies nothing about a server
    and is refused; within a live report, a file or dataset that was not requested (its
    connectivity or acquisition SKIPPED) is left as it was."""
    from . import load_report
    from .databases import VERIFICATION_RECORD, load_verification

    target = Path(a.record) if a.record else Path(__file__).resolve().parents[1] / "data" / \
        VERIFICATION_RECORD
    record: dict[str, Any] = load_verification(target) or {
        "schema": "tcmscience.live-downloads/1",
        "note": "Real downloads of the reviewed research files, one entry per file, folded in "
                "from CI's live-data workflow by `bioagent validate record`. A file appears "
                "only once a live run reached its server; its status is that run's."}
    folded = 0
    for path in a.report:
        doc = load_report(path)
        kind = doc.get("report")
        if kind not in ("live_download_validation", "hub_dataset_validation"):
            print(f"{path} is not a live_download_validation or hub_dataset_validation report",
                  file=sys.stderr)
            return 2
        if doc.get("tier") != "live":
            print(f"{path}: an offline run verifies nothing about the servers; not recorded",
                  file=sys.stderr)
            return 2
        run = ((doc.get("environment") or {}).get("ci") or {}).get("url", "")
        when = doc.get("generated_at", "")
        for r in doc.get("records", ()):
            checks = {c["name"]: c for c in r.get("checks", ())}
            if kind == "live_download_validation":
                if checks.get("connectivity", {}).get("status") in (None, "SKIPPED"):
                    continue
                prov = r.get("provenance") or {}
                record.setdefault("files", {})[r["component"]] = {
                    "status": r["status"], "verified_at": when, "run": run,
                    "url": (r.get("input") or {}).get("url"), "bytes": prov.get("bytes"),
                    "sha256": prov.get("sha256"),
                    "checks": {k: v["status"] for k, v in checks.items()},
                    **({"reason": r["failure_reason"]} if r.get("failure_reason") else {})}
            else:
                acq = checks.get("acquisition") or {}
                if (r.get("input") or {}).get("access") == "manual" or \
                        acq.get("status") in (None, "SKIPPED"):
                    continue
                files = ((acq.get("evidence") or {}).get("files") or {})
                record.setdefault("datasets", {})[r["component"].split(".", 1)[-1]] = {
                    "status": r["status"], "verified_at": when, "run": run,
                    "version": r.get("version"),
                    "checks": {k: v["status"] for k, v in checks.items()},
                    "files": {name: {k: v for k, v in (row or {}).items()
                                     if k in ("status", "url", "bytes", "checksum", "verified",
                                              "reason") and v not in (None, "")}
                              for name, row in files.items()},
                    **({"reason": r["failure_reason"]} if r.get("failure_reason") else {})}
            folded += 1
    target.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"recorded {folded} entries ({len(record.get('files') or {})} files, "
          f"{len(record.get('datasets') or {})} datasets) in {target}")
    return 0


def _datasets(a: argparse.Namespace) -> int:
    from ..tcmdb import TCMDataHub
    from .environment import fingerprint
    from .hub_datasets import dataset_keys, validate_datasets

    keys = list(a.datasets) if a.datasets else dataset_keys(a.access)
    if a.list:
        print(json.dumps(keys))
        return 0
    out = Path(a.out)
    hub = TCMDataHub(Path(a.root) if a.root else out / "hub")
    records = validate_datasets(hub, keys, live=a.live, confirm=a.confirm_large, log=_log)
    write_report(out / "hub_dataset_report.json", report(
        "hub_dataset_validation", records, tier="live" if a.live else "offline",
        environment=fingerprint(), extra={"datasets": keys}))
    _summary(records)
    return _exit(records, a.strict)


def _reachability(a: argparse.Namespace) -> int:
    """One request (``HEAD``, else a one-byte ``GET``) to each card's address. A card is
    catalogued ``unreachable`` from where it was assessed; an answer from here says it
    should be re-assessed by a person, not that its data can be obtained: whether they
    can is the card's own question (a login, a page with no export)."""
    from ..tcmdb import catalog
    from .environment import fingerprint
    from .network import probe

    records = []
    for card in catalog():
        # a card's address may carry a note after it ("http://… (2013, Tongji Univ.) -
        # defunct"); one with no address at all is not asked
        url = str(card.url).split()[0] if str(card.url).strip() else ""
        if card.access not in set(a.access) or not url.startswith(("http://", "https://")):
            continue
        rec = Record(f"source.{card.no}", "source_reachability", "",
                     input={"url": url, "card_address": card.url, "access": card.access},
                     provenance={"name": card.name, "assessment": card.assessment,
                                 "barriers": card.barriers, "checked": card.checked},
                     reproduce=f"bioagent validate reachability --out OUT --access {card.access}")
        p = probe(url)
        answered = p.status == PASS or "redirected to" in p.reason
        rec.add("reachable", PASS if answered else p.status,
                (f"answers from here ({p.reason}; HTTP {p.http_status}, final "
                 f"{p.final_url or url}); the card says {card.access!r}: "
                 f"{str(card.assessment)[:200]} — a person should re-assess it")
                if answered else p.reason, probe=p.as_dict())
        _log(f"{'ANSWERS ' if answered else 'no      '} source {card.no:>3} {card.name}: "
             f"{p.reason[:160]}")
        records.append(rec)
    write_report(Path(a.out) / "source_reachability_report.json", report(
        "source_reachability", records, tier="live", environment=fingerprint(),
        notes=["One request per source, to its catalogued address. An answer means the card "
               "should be re-assessed; it does not mean its data can be obtained."]))
    _summary(records)
    return 0


def summary_markdown(paths: Sequence[str | Path], rows: int = 40) -> str:
    """The reports under ``paths`` as Markdown: one line of counts per report, the acceptance
    of a benchmark, and the records that did not pass, each with its reason. A record that
    is BLOCKED, SKIPPED or UNVERIFIED is listed with the failures: none of them is a pass."""
    from . import STATUSES, load_report

    # a report found under a directory is named by its place there (data/…, benchmark/…):
    # two runs write reports of the same name, and a reader has to tell them apart
    files: list[tuple[Path, str]] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += [(f, f.relative_to(p).parent.as_posix()) for f in sorted(p.rglob("*.json"))]
        else:
            files.append((p, "."))
    docs = []
    for f, where in files:
        try:
            doc = load_report(f)
        except (ValueError, OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        docs.append((doc.get("report") if where == "." else f"{where}/{doc.get('report')}", doc))
    head = "| report | tier | records | " + " | ".join(STATUSES) + " |"
    lines = ["## Validation reports", "", head, "|" + "---|" * (3 + len(STATUSES))]
    failing = []
    for name, doc in docs:
        counts = (doc.get("summary") or {}).get("status") or {}
        lines.append(f"| {name} | {doc.get('tier') or '-'} | "
                     f"{(doc.get('summary') or {}).get('records', 0)} | "
                     + " | ".join(str(counts.get(st, 0)) for st in STATUSES) + " |")
        for r in doc.get("records") or ():
            if r.get("status") not in (PASS, "NOT_APPLICABLE"):
                failing.append((name, r))
    for _, doc in docs:
        acc = doc.get("acceptance")
        if acc:
            lines += ["", f"**{(doc.get('benchmark') or {}).get('id', doc.get('report'))}**: "
                          f"acceptance {acc.get('how')}; "
                          + ("every stage completed" if acc.get("completed") else
                             f"{len(acc.get('not_completed') or [])} stage(s) not completed, each "
                             "with the reason below")]
    for _, doc in docs:
        # every stage of a benchmark and every scenario, passed or not, with what it found:
        # the numbers a reader needs are in the checks' details
        if doc.get("benchmark") or doc.get("report") == "agent_regression_report":
            title = ("Benchmark stages: " + str((doc.get("benchmark") or {}).get("id"))
                     if doc.get("benchmark") else "Regression scenarios")
            lines += ["", f"### {title} ({doc.get('tier') or '-'})", "",
                      "| component | status | what it found |", "|---|---|---|"]
            for r in doc.get("records") or ():
                found = "; ".join(f"{c.get('name')}: {c.get('status')} — {c.get('detail', '')}"
                                  for c in r.get("checks") or ())
                found = found.replace("|", "\\|").replace("\n", " ")
                lines.append(f"| `{r.get('component')}` | {r.get('status')} | {found[:900]} |")
    order = {FAIL: 0, BLOCKED: 1, "UNVERIFIED": 2, "SKIPPED": 3}
    failing.sort(key=lambda x: (order.get(x[1].get("status"), 9), x[0] or "", x[1].get("component", "")))
    if failing:
        lines += ["", f"### Not passed ({len(failing)}; the first {min(rows, len(failing))})", "",
                  "| report | component | status | reason |", "|---|---|---|---|"]
        for report_name, r in failing[:rows]:
            reason = (r.get("failure_reason") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {report_name} | `{r.get('component')}` | {r.get('status')} | "
                         f"{reason[:240]} |")
    lines += ["", "A record is PASS only when every check that applies to it passed; BLOCKED, "
                  "SKIPPED and UNVERIFIED are reported as such and never counted as passes."]
    return "\n".join(lines) + "\n"


def _benchmark(a: argparse.Namespace) -> int:
    from . import benchmark

    out = Path(a.out)
    work = Path(a.work) if a.work else out / "work"
    snaps = Path(a.snapshots) if a.snapshots else None
    ledger = Path(a.ledger) if a.ledger else (snaps / "ledger.jsonl" if snaps else None)
    doc, records = benchmark.run_benchmark(work, raw=a.raw or None, snapshot_root=snaps,
                                           ledger=ledger, live=a.live, repeat=a.repeat, log=_log)
    doc["benchmark"]["artifacts"] = str((work / "artifacts").resolve())
    benchmark.write(doc, out)
    _summary(records)
    acceptance = doc["acceptance"]
    _log(f"acceptance: {acceptance['how']} ({'every stage completed' if acceptance['completed'] else str(len(acceptance['not_completed'])) + ' stage(s) not completed, each with its reason'})")
    return _exit(records, a.strict)


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.what == "data":
        return _data(a)
    if a.what == "benchmark":
        return _benchmark(a)
    if a.what == "datasets":
        return _datasets(a)
    if a.what == "reachability":
        return _reachability(a)
    if a.what == "summary":
        sys.stdout.write(summary_markdown(a.paths, a.rows))
        return 0
    if a.what == "deps":
        from .dependencies import readiness
        from .environment import fingerprint
        records = readiness()
        write_report(Path(a.out) / "dependency_readiness_report.json", report(
            "dependency_readiness_report", records, environment=fingerprint()))
        _summary(records)
        return _exit(records, a.strict)
    if a.what == "databases":
        from ..tcmdb import TCMDataHub
        from .databases import readiness
        from .environment import fingerprint
        records = readiness(TCMDataHub(a.tcmdb_root or None))
        write_report(Path(a.out) / "database_readiness_report.json", report(
            "database_readiness_report", records, tier="offline", environment=fingerprint()))
        _summary(records)
        return 0
    return _record(a)


if __name__ == "__main__":                                       # pragma: no cover
    raise SystemExit(main())
