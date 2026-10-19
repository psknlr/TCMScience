"""Real-data validation: the real files, from their real servers, through every stage.

A dataset registered in the hub, with a parser tested on small files that have the real
headers, has been shown to *parse the format*. It has not been shown that its server
answers, that the file there is still the reviewed one, that the full file parses
without losing records, or that what comes out drives an analysis. This module checks
each of those on the real thing, and records the outcome (``bioagent.validation``):

per raw file (:func:`validate_files`)
    connectivity   a real request to the reviewed URL (``validation.network.probe``)
    size           the server's size against the reviewed size
    resume         an interrupted download resumed with a byte range (the smallest file
                   on each host): half the file fetched and kept with its identity record,
                   the transfer continues it with If-Range, the whole is verified;
                   NOT_APPLICABLE where nothing could verify a resume (no strong validator
                   from the server, no reviewed checksum)
    download       the downloader's own fetch, with its checksum or size verification
    integrity      SHA-256, and the reviewed algorithm, against the reviewed checksum
    decompression  a gzip or zip file read to its end (CRC and length checked)
    format         an HTML page saved as data is refused; a table has columns
    version        the release the file name carries against the reviewed one

per hub dataset (:func:`validate_hub_dataset`), from the files above
    acquisition    each file imported into the hub, held to the same checksum
    build          the SQLite store and its relations
    record_loss    every data row of every file is a row of its table
    check          the hub's acceptance checks
    queries        facts the files are known to hold: the marker compounds of 葛根芩连汤's
                   herbs (``sources.herbs.GOLD``) in their source species, and a measured
                   protein target of berberine
    integration    the sources meet on one identity: a compound two of them place in the
                   same species, a target two of them report for one compound

and downstream (:func:`validate_research`): the research snapshots built from the same
files (``sources.pipeline``) and the research loop run on them to its verdict.

Without ``live``, nothing is requested: files already in the work directory are checked
(integrity, decompression, format, version) and the network checks are SKIPPED — never
counted as passed.
"""

from __future__ import annotations

import csv
import gzip
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from . import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED, Record, aggregate,
               utc_now)

__all__ = ["HUB_DATASETS", "validate_files", "validate_hub_dataset", "integration_check",
           "validate_research", "record_counts"]

HUB_DATASETS = ("npass", "cmaup", "lotus")

#: Facts the real files are known to hold, beyond the gold markers: berberine
#: (YBHILYKTIRIUTE-UHFFFAOYSA-N), the most studied alkaloid of 黄连, has measured
#: activities on human proteins in NPASS and CMAUP.
BERBERINE = "inchikey:YBHILYKTIRIUTE-UHFFFAOYSA-N"


@contextmanager
def _requests_seen() -> Iterator[list[tuple[str, str | None]]]:
    """Record the URL and Range header of every request urllib sends meanwhile: evidence of
    what the downloader actually asked for, nothing more (it changes no request)."""
    seen: list[tuple[str, str | None]] = []
    real = urllib.request.urlopen

    def recording(req, *args, **kwargs):
        if isinstance(req, urllib.request.Request):
            seen.append((req.full_url, req.get_header("Range")))
        return real(req, *args, **kwargs)

    urllib.request.urlopen = recording
    try:
        yield seen
    finally:
        urllib.request.urlopen = real


def _version_check(name: str, version: str) -> tuple[str, str]:
    v = (version or "").strip()
    if not v or v in ("current", "latest"):
        return NOT_APPLICABLE, "a rolling release: the file's checksum, not a version, pins it"
    if v in name:
        return PASS, f"the file name carries release {v}"
    if len(v) == 10 and v[4] == "-" and v[7] == "-" and f"{v[2:4]}{v[5:7]}{v[8:10]}" in name:
        return PASS, f"the file name carries the {v} freeze"
    return UNVERIFIED, f"the file name does not say which release it is (reviewed: {v})"


def _decompression(path: Path) -> tuple[str, str, dict[str, Any]]:
    """The check a download now passes before it is kept (``acquisition.transfer``): a
    gzip, bzip2 or xz stream read to its end, every zip member through its CRC. It used to
    miss a deflate stream corrupted in the middle (``zlib.error`` is not an ``OSError``),
    which raised instead of failing the check."""
    from ..acquisition.transfer import check_archive

    return check_archive(path)


def _open_text(path: Path):
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="")
    return open(path, encoding="utf-8", errors="replace", newline="")


def _format(path: Path, fmt: str) -> tuple[str, str, dict[str, Any]]:
    if fmt in ("zip", "raw") or path.suffix.lower() == ".zip":
        return NOT_APPLICABLE, "not a text table", {}
    with _open_text(path) as fh:
        head = fh.read(4096)
    if head.lstrip().lower().startswith(("<!doctype html", "<html")):
        return FAIL, "an HTML page, not the file", {}
    first = next((line for line in head.splitlines() if line.strip()), "")
    sep = "," if fmt.startswith("csv") or ".csv" in path.name.lower() else "\t"
    columns = len(first.split(sep))
    if fmt.startswith("tsv") and columns < 2 and len(first.split()) >= 2:
        columns = len(first.split())                    # whitespace-separated (STRING links)
    if columns < 2 and fmt not in ("text", "list"):
        if path.name.endswith("accessions.txt"):
            return PASS, "one accession per line", {"first_line": first[:80]}
        return FAIL, f"the first line has {columns} column", {"first_line": first[:120]}
    return PASS, f"{columns} columns in the first line", {"columns": columns}


#: why a resume is not tested on a file nothing could verify a resume of
_NO_VALIDATOR = ("a verified resume needs a validator or a reviewed checksum: the server sent "
                 "no strong ETag, nor a Last-Modified date its Date shows to be strong, and the "
                 "file has no reviewed checksum, so an interrupted download of it starts again "
                 "from zero (acquisition.transfer)")


def _first_half(a: Any, raw: Path, size: int) -> tuple[int, str]:
    """Fetch the first half of ``a`` into its ``.part`` file, as an interrupted download
    would leave it, with the identity record the transfer keeps beside a partial file
    (``<name>.part.json``: the answer's strong ETag, Last-Modified, Date and the total size;
    ``acquisition.transfer.identity_of``), so the resume is sent with ``If-Range`` and
    checked as a real one is. ``(bytes kept, why not)``: 0 and the reason when the server
    will not serve a range, or when nothing could verify a resume (no validator in its
    answer and no reviewed checksum): the half is then not kept."""
    from ..acquisition.transfer import identity_of, validator_of, write_sidecar
    from ..backends.http import user_agent

    half = size // 2
    req = urllib.request.Request(a.url, headers={"User-Agent": user_agent(),
                                                 "Range": f"bytes=0-{half - 1}"})
    with urllib.request.urlopen(req, timeout=120) as resp:      # noqa: S310 - reviewed URL
        code = getattr(resp, "status", 200)
        body = resp.read() if code == 206 else b""
        ident = identity_of(a.url, resp.headers, size, final_url=getattr(resp, "url", None))
    if code != 206 or len(body) != half:
        return 0, (f"the server answered a range with HTTP {code}: an interrupted download "
                   "starts again from zero")
    if not validator_of(ident) and not a.checksum:
        return 0, _NO_VALIDATOR
    (raw / (a.filename + ".part")).write_bytes(body)
    write_sidecar(raw / (a.filename + ".part.json"), ident)
    return half, ""


def _network_checks(rec: Record, a: Any, raw: Path, *, resume_here: bool, resume_on: str,
                    refused: dict[str, str], log: Callable[[str], None]) -> None:
    from ..acquisition.service import release_of
    from ..acquisition.transfer import VerifiedTransfer
    from ..tcmdb.acquire import outcome_of_error
    from .network import probe

    path = raw / a.filename
    p = probe(a.url)
    rec.add("connectivity", p.status, p.reason, http_status=p.http_status, method=p.method,
            final_url=p.final_url, elapsed_ms=p.elapsed_ms, headers=p.headers)
    if p.status == BLOCKED and ("policy" in p.reason or "resolve" in p.reason):
        refused[a.host] = p.reason
    if p.status != PASS:
        why = "the server could not be reached" if p.status == BLOCKED else \
            "the URL failed its first request"
        for name in ("size", "resume", "download"):
            rec.add(name, BLOCKED if p.status == BLOCKED else SKIPPED, why)
        return
    if a.expected_bytes is None:
        rec.add("size", UNVERIFIED, f"no reviewed size to compare the server's ({p.size}) with",
                server_bytes=p.size)
    elif p.size is None:
        rec.add("size", NOT_APPLICABLE, "the server does not say the size",
                reviewed_bytes=a.expected_bytes)
    else:
        same = p.size == a.expected_bytes
        rec.add("size", PASS if same else FAIL,
                "the reviewed size" if same else f"the server has {p.size} bytes, the reviewed "
                f"file {a.expected_bytes}: the upstream file changed",
                server_bytes=p.size, reviewed_bytes=a.expected_bytes)
    had = 0
    if not resume_here:
        rec.add("resume", NOT_APPLICABLE, f"tested once per host, on its smallest file ({resume_on})")
    elif path.exists():
        rec.add("resume", NOT_APPLICABLE, "the file is already here")
    elif p.ranges is False or not p.size:
        rec.add("resume", NOT_APPLICABLE, "the server does not say it serves byte ranges: an "
                "interrupted download starts again from zero")
    elif not a.checksum and not p.headers.get("last-modified") and (
            not p.headers.get("etag") or p.headers["etag"].strip().startswith(("W/", "w/"))):
        rec.add("resume", NOT_APPLICABLE, _NO_VALIDATOR)        # nothing is fetched for it
    else:
        try:
            had, why = _first_half(a, raw, p.size)
            if not had:
                rec.add("resume", NOT_APPLICABLE, why)
        except Exception as exc:                                # noqa: BLE001
            status, reason = outcome_of_error(exc, a.host)
            rec.add("resume", status, f"fetching the first half: {reason}")
    dl = VerifiedTransfer(raw, timeout_s=300, log=log,
                          min_interval_s=1.0 if a.host == "bidd.group" else 0.0)
    try:
        with _requests_seen() as seen:
            r = dl.fetch(a.url, a.filename, checksum=a.checksum, expected_bytes=a.expected_bytes,
                         release=release_of(a.version), fmt=a.fmt)
    except Exception as exc:                                    # noqa: BLE001
        status, reason = outcome_of_error(exc, a.host)
        rec.add("download", status, reason)
        if had:
            rec.add("resume", status, reason)
        return
    verified = "checksum" if a.checksum else ("size" if r.verified else "none")
    rec.add("download", PASS if verified != "none" else UNVERIFIED,
            f"{r.bytes} bytes in {r.duration_s:.1f} s, verified by {verified}"
            + (" (reused)" if r.from_cache else ""),
            bytes=r.bytes, seconds=round(r.duration_s, 2), from_cache=r.from_cache)
    if had:
        ranged = [rng for _, rng in seen if rng and rng.startswith(f"bytes={had}-")]
        ok = bool(ranged) and r.bytes == p.size and r.resumed_from == had
        rec.add("resume", PASS if ok else FAIL,
                f"resumed at byte {had} with a range request; the whole file verified" if ok
                else "the partial file was not continued with a range request"
                + (f" ({'; '.join(r.restarts)})" if r.restarts else ""),
                resumed_at=had, range_requests=ranged, resumed_from=r.resumed_from)


def validate_files(specs: Sequence[Any], raw_dir: str | Path, *, live: bool,
                   resume_check: bool = True, log: Callable[[str], None] = print
                   ) -> list[Record]:
    """One record per reviewed file (``AcquisitionSpec``), its checks in order."""
    from ..tcmdb.acquire import digest

    raw = Path(raw_dir)
    raw.mkdir(parents=True, exist_ok=True)
    smallest: dict[str, Any] = {}
    for a in specs:
        if a.host not in smallest or (a.expected_bytes or 1 << 62) < (
                smallest[a.host].expected_bytes or 1 << 62):
            smallest[a.host] = a
    refused: dict[str, str] = {}
    records = []
    for a in specs:
        rec = Record(a.filename, "raw_file", a.version,
                     input={"url": a.url, "reviewed_bytes": a.expected_bytes,
                            "reviewed_checksum": a.checksum},
                     provenance={"project": a.source_project, "host": a.host,
                                 "license": a.license, "reviewed": a.checksum is not None},
                     reproduce=f"bioagent validate data --live --files {a.filename}")
        path = raw / a.filename
        t0 = time.perf_counter()
        if not live:
            for name in ("connectivity", "size", "resume", "download"):
                rec.add(name, SKIPPED, "offline run: nothing is requested")
        elif a.host in refused:
            for name in ("connectivity", "size", "resume", "download"):
                rec.add(name, BLOCKED, f"{refused[a.host]} (learnt from an earlier file)")
        else:
            _network_checks(rec, a, raw, resume_here=resume_check and smallest[a.host] is a,
                            resume_on=smallest[a.host].filename, refused=refused, log=log)
        if not path.is_file():
            for name in ("integrity", "decompression", "format"):
                rec.add(name, SKIPPED, "the file is not here")
        else:
            size = path.stat().st_size
            sha = digest(path, "sha256")
            rec.provenance.update(bytes=size, sha256=sha, retrieved_at=utc_now())
            if a.checksum:
                algorithm = a.checksum.split(":", 1)[0]
                observed = sha if algorithm == "sha256" else digest(path, algorithm)
                ok = observed.lower() == a.checksum.lower()
                rec.add("integrity", PASS if ok else FAIL,
                        f"hashes to the reviewed {algorithm}" if ok else
                        f"{observed[:23]}… is not the reviewed {a.checksum[:23]}…",
                        observed=observed)
            elif a.expected_bytes is not None:
                ok = size == a.expected_bytes
                rec.add("integrity", PASS if ok else FAIL,
                        "the reviewed size (no reviewed checksum)" if ok else
                        f"{size} bytes, not the reviewed {a.expected_bytes}", sha256=sha)
            else:
                rec.add("integrity", UNVERIFIED, "no reviewed checksum or size: its SHA-256 "
                        "is recorded here for a person to review and pin", sha256=sha)
            status, detail, ev = _decompression(path)
            rec.add("decompression", status, detail, **ev)
            status, detail, ev = _format(path, a.fmt)
            rec.add("format", status, detail, **ev)
        rec.add("version", *_version_check(a.filename, a.version))
        rec.runtime_s = time.perf_counter() - t0
        log(f"{a.filename}: {rec.status}"
            + (f" — {rec.failure_reason}" if rec.failure_reason else ""))
        records.append(rec)
    return records


def record_counts(path: Path, fmt: str, headerless: bool) -> int:
    """The data records of a table file: rows, not lines (a quoted CSV field may hold a
    newline), header excluded."""
    with _open_text(path) as fh:
        if fmt == "csv":
            n = sum(1 for row in csv.reader(fh) if row)
        else:
            n = sum(1 for line in fh if line.strip())
    return n if headerless else max(0, n - 1)


def _gold_hits(hub: Any, key: str) -> dict[str, Any]:
    from ..sources.herbs import GOLD, HERBS

    out = {}
    for herb, (name, inchikey) in GOLD.items():
        species = {f"ncbitaxon:{s.taxid}" for s in HERBS[herb].species}
        names = {s.name.lower() for s in HERBS[herb].species} | {
            syn.lower() for s in HERBS[herb].species for syn in s.synonyms}
        rows = hub.relations("organism_compound", object=f"inchikey:{inchikey}", sources=[key],
                             limit=500)
        hits = sorted({r["subject_id"] for r in rows
                       if r["subject_id"] in species
                       or str(r.get("subject_name") or "").lower() in names})
        out[name] = {"herb": HERBS[herb].chinese, "found_in": hits, "rows": len(rows)}
    return out


def validate_hub_dataset(hub: Any, key: str, raw_dir: str | Path, *, rebuild: bool = True,
                         log: Callable[[str], None] = print) -> Record:
    """Import the verified files of ``key`` into ``hub``, then build, reconcile, check and
    query it."""
    from ..tcmdb import HubError
    from ..tcmdb.datasets import dataset

    spec = dataset(key)
    rec = Record(f"tcmdb.{key}", "hub_dataset", spec.version,
                 provenance={"name": spec.name, "homepage": spec.homepage,
                             "license": spec.license, "commercial_use": spec.commercial_use},
                 reproduce=f"bioagent validate data --live --datasets {key}")
    t0 = time.perf_counter()
    raw = Path(raw_dir)
    imported, missing = [], []
    for f in spec.files:
        if f.optional:
            continue
        src = raw / f.name
        if not src.is_file():
            missing.append(f.name)
            continue
        try:
            imported.append(hub.import_raw(key, src, log=lambda m: None))
        except HubError as exc:
            rec.add("acquisition", FAIL, str(exc))
            rec.runtime_s = time.perf_counter() - t0
            return rec
    if missing:
        rec.add("acquisition", BLOCKED, f"not obtained: {', '.join(missing)}")
        for name in ("build", "record_loss", "check", "queries", "integration"):
            rec.add(name, SKIPPED, "the files are not all here")
        rec.runtime_s = time.perf_counter() - t0
        return rec
    rec.add("acquisition", aggregate(r["status"] for r in imported),
            f"{len(imported)} files imported, each held to its reviewed checksum or size",
            verified={r["file"]: r["verified"] for r in imported})
    try:
        report = hub.build(key, log=lambda m: None)
    except Exception as exc:                                    # noqa: BLE001
        rec.add("build", FAIL, f"{type(exc).__name__}: {exc}")
        rec.runtime_s = time.perf_counter() - t0
        return rec
    relations = report.get("relations") or {}
    rec.add("build", PASS if sum(relations.values()) else FAIL,
            f"tables {report.get('tables')}, relations {relations}",
            tables=report.get("tables"), relations=relations,
            unresolved=report.get("unresolved"), digest=report.get("relations_digest"))
    losses = {}
    for f in spec.files:
        if f.optional or not f.table:
            continue
        expected = record_counts(raw / f.name, f.fmt, headerless=bool(f.columns))
        got = (report.get("tables") or {}).get(f.table)
        if got != expected:
            losses[f.name] = {"records": expected, "rows": got}
    rec.add("record_loss", FAIL if losses else PASS,
            "every data record of every file is a row of its table" if not losses else
            f"rows lost or added on import: {losses}", differences=losses)
    try:
        checked = hub.check(key, rebuild=rebuild)
        rec.add("check", PASS if checked.get("ok") else FAIL,
                "the hub's acceptance checks pass" if checked.get("ok") else
                "; ".join(checked.get("problems", []))[:1500],
                problems=list(checked.get("problems", []))[:50],
                warnings=list(checked.get("warnings", []))[:20], rebuilt=rebuild)
    except Exception as exc:                                    # noqa: BLE001
        rec.add("check", FAIL, f"{type(exc).__name__}: {exc}")
    gold = _gold_hits(hub, key)
    found = [n for n, g in gold.items() if g["found_in"]]
    queries = {"gold": gold}
    if "compound_target" in relations:
        targets = hub.relations("compound_target", subject=BERBERINE, sources=[key], limit=500)
        measured = [t for t in targets if str(t.get("object_id", "")).startswith("uniprot:")
                    and t.get("reference")]
        queries["berberine_targets"] = len(measured)
        status = PASS if found and measured else FAIL
        detail = (f"{len(found)} of {len(gold)} marker compounds in their herbs' species "
                  f"({', '.join(found) or 'none'}); berberine has {len(measured)} measured "
                  "protein targets with a reference")
    else:
        status = PASS if found else FAIL
        detail = (f"{len(found)} of {len(gold)} marker compounds in their herbs' species "
                  f"({', '.join(found) or 'none'})")
    rec.add("queries", status, detail, **queries)
    rec.runtime_s = time.perf_counter() - t0
    return rec


def integration_check(hub: Any, keys: Iterable[str]) -> Record:
    """Do the sources meet on one identity? A compound two of them place in the same
    species, and a target two of them report for one compound."""
    from ..sources.herbs import GOLD

    keys = [k for k in keys if hub.db_path(k).exists()]
    rec = Record("tcmdb.np_activity", "integration", "",
                 reproduce="bioagent validate data --live")
    if len(keys) < 2:
        rec.add("integration", SKIPPED, f"needs two built sources; built: {keys}")
        return rec
    shared = {}
    for name, inchikey in GOLD.values():
        # the same organism is the same NCBI taxon, or the same name where a source has no
        # taxon id for it (a LOTUS organism known only by its Wikidata item)
        by: dict[str, set[str]] = {}
        for k in keys:
            for r in hub.relations("organism_compound", object=f"inchikey:{inchikey}",
                                   sources=[k], limit=500):
                if str(r["subject_id"]).startswith("ncbitaxon:"):
                    by.setdefault(r["subject_id"], set()).add(k)
                if r.get("subject_name"):
                    by.setdefault("name:" + str(r["subject_name"]).strip().lower(), set()).add(k)
        agree = {s: sorted(v) for s, v in by.items() if len(v) >= 2}
        if agree:
            shared[name] = agree
    targets: dict[str, set[str]] = {}
    for k in keys:
        for r in hub.relations("compound_target", subject=BERBERINE, sources=[k], limit=1000):
            if str(r.get("object_id", "")).startswith("uniprot:"):
                targets.setdefault(r["object_id"], set()).add(k)
    common = sorted(t for t, v in targets.items() if len(v) >= 2)
    with_targets = [k for k in keys
                    if "compound_target" in (hub.status(k)[0].get("relations") or {})]
    ok = bool(shared) and (bool(common) or len(with_targets) < 2)
    rec.add("integration", PASS if ok else FAIL,
            f"{len(shared)} marker compounds placed in the same species by two or more sources; "
            f"{len(common)} berberine targets reported by two or more",
            species=shared, berberine_common_targets=common[:50])
    return rec


def validate_research(raw_dir: str | Path, work: str | Path, *, formulas: Sequence[str] = (),
                      log: Callable[[str], None] = print) -> list[Record]:
    """The research snapshots from the same files, and the loop run on them."""
    from ..sources import pipeline

    work = Path(work)
    lake_raw = work / "lake" / "raw"
    snap = work / "lake" / "snapshots"
    records = []
    rec = Record("research.snapshots", "research_snapshots", "",
                 reproduce="bioagent snapshots build --raw RAW --out SNAP --ledger SNAP/ledger.jsonl")
    present = [Path(raw_dir) / a.filename for a in pipeline.required_files()
               if (Path(raw_dir) / a.filename).is_file()]
    try:
        pipeline.import_files(lake_raw, present, log=lambda m: None)
    except (OSError, ValueError) as exc:
        rec.add("import", FAIL, str(exc))
        return [rec]
    doc = pipeline.build_research_snapshots(lake_raw, snap, snap / "ledger.jsonl",
                                            formulas=formulas, offline=True,
                                            report_path=work / "snapshot_report.json", log=log)
    for stage in doc["stages"]:
        rec.add(stage["stage"], stage["status"], stage["detail"])
    rec.provenance = {"snapshots": doc.get("snapshots", {}), "report": str(work /
                                                                           "snapshot_report.json")}
    records.append(rec)
    loop = Record("research.run:葛根芩连汤", "research_run", "",
                  reproduce="bioagent research '葛根芩连汤的实测靶点是否集中在某条通路？' "
                            "--snapshots SNAP --ledger SNAP/ledger.jsonl --state-dir S --out O")
    if doc["status"] not in (PASS, UNVERIFIED):
        loop.add("run", SKIPPED, "the snapshots were not built")
        return records + [loop]
    from ..research import default_protocol, parse_question, run_research
    t0 = time.perf_counter()
    try:
        question = parse_question("葛根芩连汤的实测靶点是否集中在某条通路？")
        run = run_research(question, protocol=default_protocol(question), snapshot_root=snap,
                           ledger_path=snap / "ledger.jsonl", state_dir=work / "state",
                           output_dir=work / "research_run")
    except Exception as exc:                                    # noqa: BLE001
        loop.add("run", FAIL, f"{type(exc).__name__}: {exc}")
        return records + [loop]
    loop.runtime_s = time.perf_counter() - t0
    loop.add("run", PASS if run.verdict is not None else FAIL,
             f"ran to its verdict in {loop.runtime_s:.0f} s: {len(run.hypotheses)} hypotheses, "
             f"{len(run.refuted)} refuted, released: {run.released}",
             hypotheses=list(run.hypotheses)[:20], refuted=list(run.refuted)[:20],
             released=run.released, states=(run.verdict.states if run.verdict else {}))
    loop.add("snapshots_used", PASS if all(run.snapshots.get(k) ==
                                           (doc.get("snapshots") or {}).get(k, {}).get("snapshot_id")
                                           for k in run.snapshots
                                           if k in (doc.get("snapshots") or {})) else FAIL,
             "the run read exactly the snapshots the build recorded",
             snapshots=dict(run.snapshots))
    loop.provenance = {"audit_head": run.audit_head, "output_dir": str(work / "research_run")}
    return records + [loop]

