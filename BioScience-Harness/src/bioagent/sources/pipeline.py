"""The research snapshots, built in one recorded run: discover, acquire, verify, build, record.

The research loop (``bioagent research``, Studio's ``research.run`` job) reads source
snapshots through a hash-chained ledger. Building them took a person at a shell: a
``bioagent fetch`` per raw file, then ``scripts/build_source_snapshots.py gold --network``.
:func:`build_research_snapshots` does the same work as one run in which every stage leaves a
record and a status (``bioagent.validation``'s six):

    discovery    the raw files the requested sources need, and which are here already
    acquisition  each missing file adopted from a verified copy the data hub already holds
                 (``mirrors``: a hard link, only for a file with a reviewed checksum), or
                 downloaded from its reviewed URL through ``acquisition.transfer`` (a resume
                 only onto the same file, the downloader's pacing and size gate, archives
                 read through before a file is kept); a host the network policy refuses is
                 learnt from its first refusal and not asked again
    integrity    every file hashed again and compared with its reviewed checksum; a file
                 with none (Reactome's current UniProt mapping, the UniProt accession list)
                 is verified by its size, and its SHA-256 recorded for review
    build        parse, normalise and gate each source (``sources.build.build_gold``): the
                 QC status, warnings and dropped rows, and the gold standard — every herb's
                 marker compound found in one of its source species, a fact the real files
                 must reproduce
    ledger       every snapshot id recorded in the ledger the loop reads, and the chain
                 verified
    report       one JSON document (``tcmscience.snapshots/1``): stages, files, snapshots,
                 the next step when a stage could not complete, and the command that
                 reproduces the run

A stage that cannot complete stops the run with its reason; the stages before it stay done,
so a rerun reuses the verified files. Nothing here goes around a barrier: a file the network
here cannot reach is BLOCKED, and :func:`import_files` is the route for a copy a person
obtained elsewhere, held to the same checksum.
"""

from __future__ import annotations

import shlex
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..acquisition.sources import ACQUIRABLE, AcquisitionSpec
from ..validation import BLOCKED, FAIL, PASS, SKIPPED, UNVERIFIED, aggregate, utc_now

__all__ = ["SCHEMA", "RESEARCH_SOURCES", "UNREVIEWED", "required_files", "plan",
           "build_research_snapshots", "import_files", "formulas_named", "summary"]

SCHEMA = "tcmscience.snapshots/1"
RESEARCH_SOURCES = ("npass", "cmaup", "lotus")

#: Files the research build reads that the reviewed acquisition table does not list yet.
#: Their URLs are the ones the source cards name (``sources.cards``). STRING's aliases, a
#: frozen release (v12.0), carry the size and SHA-256 two independent live downloads read
#: (the live-data workflow, runs 38127338070 on 2026-10-11 and 40307982154 on 2026-10-26);
#: Reactome's has none (its 'current' file changes every release), so a download of it is
#: verified against the server's Content-Length and its SHA-256 is recorded in the report.
UNREVIEWED: tuple[AcquisitionSpec, ...] = (
    AcquisitionSpec(
        "https://stringdb-downloads.org/download/protein.aliases.v12.0/"
        "9606.protein.aliases.v12.0.txt.gz",
        "9606.protein.aliases.v12.0.txt.gz", "stringdb-downloads.org", "CC-BY-4.0",
        "STRING v12 human protein aliases, UniProt accessions among them: how a measured "
        "target is found among STRING's proteins.", "proteomics", fmt="tsv.gz",
        expected_bytes=19777800,
        checksum="sha256:b65f730b993ed0c1bd72edf4565d3d425db42861101b29699704810e8f125680",
        source_project="STRING", version="12.0",
        notes="the same release and URL scheme as the reviewed info and links files"),
    AcquisitionSpec(
        "https://reactome.org/download/current/UniProt2Reactome.txt",
        "UniProt2Reactome.txt", "reactome.org", "CC0-1.0",
        "Reactome: UniProt accession to lowest-level pathway, every species (the parser keeps "
        "the human rows): the enrichment background.", "pathways", fmt="tsv",
        source_project="Reactome", version="current",
        notes="the URL of the Reactome source card; 'current' changes quarterly, so no size "
              "is pinned"),
)

_SPECS: dict[str, AcquisitionSpec] = {a.filename: a for a in ACQUIRABLE + UNREVIEWED}
#: the least seconds between two requests to a host (NPASS's and CMAUP's server)
_MIN_INTERVAL = {"bidd.group": 1.0}


def required_files(sources: Iterable[str] = RESEARCH_SOURCES, *,
                   network: bool = True) -> list[AcquisitionSpec]:
    """The raw files ``build_gold`` reads for ``sources`` (and STRING and Reactome)."""
    from .build import LOTUS_FILE
    from .parsers import cmaup, npass, pubchem_bioassay, reactome, string_db

    names: list[str] = []
    sources = list(sources)
    unknown = sorted(set(sources) - set(RESEARCH_SOURCES))
    if unknown:
        raise ValueError(f"no research source {unknown}; one of {list(RESEARCH_SOURCES)}")
    if "npass" in sources:
        names += list(npass.FILES.values())
    if "cmaup" in sources:
        names += list(cmaup.FILES.values())
    if "lotus" in sources:
        names.append(LOTUS_FILE)
    if network:
        names += [string_db.FILES[k] for k in ("info", "links", "aliases")]
        names += [reactome.FILE, pubchem_bioassay.PRIMARY_FILE]
    return [_SPECS[n] for n in dict.fromkeys(names)]


def _stage(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"stage": name, "status": status, "detail": detail, "at": utc_now(), **extra}


def plan(raw_dir: str | Path, sources: Iterable[str] = RESEARCH_SOURCES, *,
         network: bool = True) -> dict[str, Any]:
    """Which files are needed, and which are here and how they were verified."""
    from ..tcmdb.acquire import verification

    raw = Path(raw_dir)
    rows = []
    for a in required_files(sources, network=network):
        present = (raw / a.filename).is_file()
        rows.append({"file": a.filename, "project": a.source_project, "url": a.url,
                     "host": a.host, "expected_bytes": a.expected_bytes,
                     "reviewed_checksum": a.checksum,
                     "reviewed": a in ACQUIRABLE, "present": present,
                     "verified": verification(raw, a.filename, a.checksum) if present else None})
    need = [r for r in rows if not r["present"]]
    known = [r["expected_bytes"] for r in need]
    return {"files": rows, "missing": [r["file"] for r in need],
            "download_bytes": sum(b for b in known if b) if all(known) else None,
            "hosts": sorted({r["host"] for r in need})}


def _acquire(raw: Path, specs: Sequence[AcquisitionSpec], *, confirm: bool, offline: bool,
             log: Callable[[str], None], mirrors: Sequence[str | Path] = ()
             ) -> list[dict[str, Any]]:
    """Each file here, adopted from a verified copy in ``mirrors`` (the hub's raw directory
    of the same dataset: one copy on disk, never one without a reviewed checksum), or
    downloaded through ``acquisition.transfer.VerifiedTransfer`` (a resume only onto the same
    file, retries only for what is transient, archives read through before a file is kept)."""
    from ..acquisition.service import adopt_verified, release_of
    from ..acquisition.transfer import VerifiedTransfer
    from ..tcmdb import acquire

    refused: dict[str, str] = {}
    out = []
    for a in specs:
        t0 = time.perf_counter()
        host = urllib.parse.urlsplit(a.url).hostname or a.host
        row: dict[str, Any] = {"file": a.filename, "url": a.url, "host": host}
        cached = (raw / a.filename).is_file()
        adopted = None if cached or offline or not mirrors else adopt_verified(
            mirrors, raw, a.filename, a.checksum, url=a.url, release=release_of(a.version),
            log=log)
        if adopted is not None:
            status, reason = PASS, (f"adopted from {adopted['mirror']} ({adopted['adopted_as']}); "
                                    "it hashes to the reviewed checksum")
            row.update(bytes=adopted["bytes"], checksum=adopted["checksum"], verified="checksum",
                       route="local-mirror")
        elif offline and cached:
            status, reason = PASS, "here; this run downloads nothing (the integrity stage checks it)"
            row["route"] = "local"
        elif offline:
            status, reason = SKIPPED, "not here, and this run downloads nothing"
        elif not cached and host in refused:
            status, reason = BLOCKED, f"{refused[host]} (learnt from an earlier file; not asked again)"
        elif not cached and (short := acquire.disk_shortfall(raw, a.expected_bytes)):
            status, reason = BLOCKED, short
        else:
            dl = VerifiedTransfer(raw, timeout_s=120, log=log,
                                  min_interval_s=_MIN_INTERVAL.get(host, 0))
            try:
                r = dl.fetch(a.url, a.filename, checksum=a.checksum,
                             expected_bytes=a.expected_bytes, confirm=confirm,
                             release=release_of(a.version), fmt=a.fmt, refuse_html=True)
                verified = "checksum" if a.checksum else ("size" if r.verified else "none")
                status = PASS if verified != "none" else UNVERIFIED
                reason = f"{'reused' if r.from_cache else 'downloaded'}, verified by {verified}"
                row.update(bytes=r.bytes, checksum=r.checksum, verified=verified,
                           route="cache" if r.from_cache else "download",
                           **{k: v for k, v in r.record().items()
                              if k in ("resumed", "resumed_from", "etag", "last_modified",
                                       "retrieved_at", "release", "decompression")})
            except Exception as exc:                                # noqa: BLE001
                status, reason = acquire.outcome_of_error(exc, host)
                if status == BLOCKED and ("policy" in reason or "resolve" in reason):
                    refused[host] = reason
        row.update(status=status, reason=reason, duration_s=round(time.perf_counter() - t0, 3))
        log(f"{a.filename}: {status} — {reason}")
        out.append(row)
    return out


def _integrity(raw: Path, specs: Sequence[AcquisitionSpec]) -> list[dict[str, Any]]:
    from ..tcmdb.acquire import digest

    out = []
    for a in specs:
        path = raw / a.filename
        if not path.is_file():
            out.append({"file": a.filename, "status": SKIPPED, "reason": "not here"})
            continue
        size = path.stat().st_size
        sha = digest(path, "sha256")
        row: dict[str, Any] = {"file": a.filename, "bytes": size, "sha256": sha}
        if a.checksum:
            algorithm = a.checksum.split(":", 1)[0]
            observed = sha if algorithm == "sha256" else digest(path, algorithm)
            ok = observed.lower() == a.checksum.lower()
            row.update(status=PASS if ok else FAIL, reviewed_checksum=a.checksum,
                       reason="hashes to the reviewed checksum" if ok else
                       f"{observed[:23]}… is not the reviewed {a.checksum[:23]}…: the file "
                       "has changed since it was reviewed")
        elif a.expected_bytes is not None:
            ok = size == a.expected_bytes
            row.update(status=PASS if ok else FAIL,
                       reason="the reviewed size" if ok else
                       f"{size} bytes, not the reviewed {a.expected_bytes}")
        else:
            row.update(status=UNVERIFIED,
                       reason="no reviewed checksum or size: verified against the server's "
                              "size when downloaded; its SHA-256 is recorded here for review")
        out.append(row)
    return out


def formulas_named(names: Iterable[str], table: Any = None) -> list[Any]:
    """The compositions ``names`` (a formula id, or a name with its source book, e.g.
    ``桂枝汤《伤寒论》``) stand for, resolved as the research loop resolves a question —
    so a name the loop would refuse as ambiguous is refused here, with its candidates."""
    from ..research.loop import formula_for, parse_question

    out = []
    for name in names:
        question = parse_question(str(name), table=table)
        out.append(formula_for(question.formula_id, table))
    return out


def _snapshot_summary(snap: Any) -> dict[str, Any]:
    content = snap.manifest["content"]
    return {"snapshot_id": snap.snapshot_id, "version": snap.version,
            "qc": content["qc"]["status"], "warnings": content["qc"]["warnings"],
            "nodes": len(snap.nodes), "edges": len(snap.edges),
            "dropped": (content.get("extra") or {}).get("parse_report", {}).get("dropped", {})}


def _command(raw: Path, out: Path, ledger: Path, sources: Sequence[str], network: bool,
             formulas: Sequence[str], formula_table: str | None) -> str:
    argv = ["bioagent", "snapshots", "build", "--raw", str(raw), "--out", str(out),
            "--ledger", str(ledger), "--sources", *sources]
    if not network:
        argv.append("--no-network")
    for f in formulas:
        argv += ["--formula", f]
    if formula_table:
        argv += ["--formula-table", formula_table]
    return " ".join(shlex.quote(a) for a in argv)


def build_research_snapshots(raw_dir: str | Path, snapshot_root: str | Path,
                             ledger_path: str | Path, *,
                             sources: Sequence[str] = RESEARCH_SOURCES, network: bool = True,
                             formulas: Sequence[str] = (), formula_table: str | None = None,
                             confirm: bool = False, offline: bool = False,
                             report_path: str | Path | None = None,
                             mirrors: Sequence[str | Path] = (),
                             log: Callable[[str], None] = print) -> dict[str, Any]:
    """Run every stage and return (and, with ``report_path``, write) the report.

    ``mirrors`` are directories that may already hold some of the same raw files (the data
    hub's ``raw/npass``, ``raw/cmaup``, ``raw/lotus``): a file there that hashes to its
    reviewed checksum is adopted as a hard link instead of being downloaded again."""
    from .build import build_gold
    from .ledger import LedgerError, SnapshotLedger
    from .snapshot import SnapshotError

    raw, out, ledger_file = Path(raw_dir), Path(snapshot_root), Path(ledger_path)
    raw.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    sources = list(sources)
    doc: dict[str, Any] = {
        "schema": SCHEMA, "started_at": started,
        "parameters": {"raw_dir": str(raw), "snapshot_root": str(out),
                       "ledger": str(ledger_file), "sources": sources, "network": network,
                       "formulas": list(formulas), "formula_table": formula_table,
                       "offline": offline},
        "reproduce": _command(raw, out, ledger_file, sources, network, formulas, formula_table),
        "stages": []}
    stages: list[dict[str, Any]] = doc["stages"]

    def finish(next_step: str = "") -> dict[str, Any]:
        doc["status"] = aggregate(s["status"] for s in stages)
        doc["finished_at"] = utc_now()
        if next_step:
            doc["next_step"] = next_step
        if report_path:
            from ..validation import write_report
            write_report(report_path, doc)
        return doc

    try:
        specs = required_files(sources, network=network)
    except ValueError as exc:
        stages.append(_stage("discovery", FAIL, str(exc)))
        return finish("name research sources among " + ", ".join(RESEARCH_SOURCES))
    found = plan(raw, sources, network=network)
    stages.append(_stage("discovery", PASS,
                         f"{len(specs)} files needed, {len(found['missing'])} not here",
                         files=found["files"], download_bytes=found["download_bytes"]))

    rows = _acquire(raw, specs, confirm=confirm, offline=offline, log=log, mirrors=mirrors)
    acquired = aggregate(r["status"] for r in rows)
    stages.append(_stage("acquisition", acquired,
                         f"{sum(r['status'] in (PASS, UNVERIFIED) for r in rows)} of {len(rows)} "
                         "files here and verified", files=rows))
    if acquired not in (PASS, UNVERIFIED):
        missing = [r for r in rows if r["status"] not in (PASS, UNVERIFIED)]
        hosts = sorted({r["host"] for r in missing})
        return finish(
            f"{len(missing)} file(s) could not be obtained here ({missing[0]['reason']}). "
            f"Download them where {', '.join(hosts)} can be reached, then add them with "
            f"`bioagent snapshots import --raw {raw} FILE...` (held to the same checksums) "
            "and run again; the verified files are kept.")

    checks = _integrity(raw, specs)
    integrity = aggregate(r["status"] for r in checks)
    stages.append(_stage("integrity", integrity,
                         f"{sum(r['status'] == PASS for r in checks)} of {len(checks)} files "
                         "match their reviewed checksum or size", files=checks))
    if integrity == FAIL:
        bad = [r["file"] for r in checks if r["status"] == FAIL]
        return finish(f"{', '.join(bad)} differ from the reviewed files: review the upstream "
                      "change before building from them")

    try:
        table = None
        if formula_table or formulas:
            from ..research.loop import _table
            from .formulas import load_formula_table
            table = load_formula_table(formula_table) if formula_table else _table()
        layer: list[Any] = []
        if formulas:
            layer += formulas_named(formulas, table)
        if formula_table and not formulas:
            layer += [r.version() for r in table.resolved]
        ledger = SnapshotLedger(ledger_file)
        t0 = time.perf_counter()
        build = build_gold(raw, out, ledger=ledger, network=network, sources=sources,
                           formulas=layer or None)
    except (SnapshotError, LedgerError, ValueError, KeyError, OSError) as exc:
        stages.append(_stage("build", FAIL, f"{type(exc).__name__}: {exc}"))
        return finish("the parse or quality gate refused the files; the message says which "
                      "and why")
    snapshots = {k: _snapshot_summary(v) for k, v in build.snapshots.items()}
    review = sorted(k for k, s in snapshots.items() if s["qc"] != "pass")
    gold = PASS if build.passed else FAIL
    status = aggregate([gold, UNVERIFIED if review else PASS])
    detail = (f"{len(snapshots)} snapshots in {time.perf_counter() - t0:.1f} s; the gold standard "
              + ("reproduced" if build.passed else "NOT reproduced: " + "; ".join(
                  f"{h}: {why}" for h, why in sorted(build.missing.items())))
              + (f"; QC asks for review of {', '.join(review)}" if review else ""))
    stages.append(_stage("build", status, detail, snapshots=snapshots,
                         gold={"passed": build.passed, "missing": build.missing},
                         composition_rows=len(build.composition),
                         formulas=[getattr(f, "id", str(f)) for f in layer]))
    doc["snapshots"] = snapshots
    if gold == FAIL:
        return finish("the real files do not contain the marker compounds they are known to "
                      "hold: the parse or the files are wrong; do not run research on them")

    try:
        n = ledger.verify()
        missing = [k for k, s in snapshots.items()
                   if ledger.expected(k, s["version"]) != s["snapshot_id"]]
    except (LedgerError, KeyError, ValueError) as exc:
        stages.append(_stage("ledger", FAIL, f"{type(exc).__name__}: {exc}"))
        return finish("the ledger is damaged; do not run research until it verifies")
    stages.append(_stage("ledger", FAIL if missing else PASS,
                         f"{n} entries verified" + (f"; not recorded: {missing}" if missing else ""),
                         head=ledger.head()))
    return finish("" if not missing else "rebuild with the ledger the research loop reads")


def import_files(raw_dir: str | Path, paths: Iterable[str | Path], *,
                 log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Add copies of research raw files a person obtained, held to the reviewed checksum
    (or size) a download would be; a file with neither is kept and reported UNVERIFIED."""
    from ..tcmdb import acquire

    raw = Path(raw_dir)
    out = []
    for p in paths:
        src = Path(p)
        a = _SPECS.get(src.name)
        if a is None:
            raise ValueError(f"{src.name} is not a research raw file; one of "
                             f"{', '.join(sorted(_SPECS))}")
        size = src.stat().st_size
        algorithm = (a.checksum or "sha256:").split(":", 1)[0]
        observed = acquire.digest(src, algorithm)
        if a.checksum and observed.lower() != a.checksum.lower():
            raise ValueError(f"{src} is not the reviewed {a.filename} ({observed[:23]}… where "
                             f"the reviewed file is {a.checksum[:23]}…); it was not imported")
        if not a.checksum and a.expected_bytes is not None and size != a.expected_bytes:
            raise ValueError(f"{src} has {size} bytes, not the reviewed {a.expected_bytes}")
        verified = "checksum" if a.checksum else ("size" if a.expected_bytes else "none")
        acquire.copy_verified(src, raw / a.filename)
        acquire.record_manifest(raw, a.filename, {
            "url": a.url, "bytes": size, "checksum": observed, "verified": verified != "none",
            "fetched_at": time.time(), "route": "import"})
        status = PASS if verified != "none" else UNVERIFIED
        log(f"{a.filename}: imported ({size} bytes, verified by {verified})")
        out.append({"file": a.filename, "bytes": size, "checksum": observed,
                    "verified": verified, "status": status})
    return out


def summary(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The few lines a job result or a log needs from a report."""
    return {"status": doc.get("status"),
            "stages": {s["stage"]: s["status"] for s in doc.get("stages", ())},
            "snapshots": {k: {"id": v["snapshot_id"], "version": v["version"], "qc": v["qc"],
                              "edges": v["edges"]}
                          for k, v in (doc.get("snapshots") or {}).items()},
            "next_step": doc.get("next_step", "")}
