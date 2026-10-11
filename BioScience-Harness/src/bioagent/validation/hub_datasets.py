"""Every hub dataset, from its own server, through the stages the agent relies on.

``validation.data`` follows the research files (NPASS, CMAUP, LOTUS, STRING, Reactome,
UniProt) through snapshots and the research loop. This module covers every dataset the
data hub defines (``tcmdb.datasets``), run the way Studio's ``tcmdb.fetch`` and
``tcmdb.build`` jobs run them (``TCMDataHub.fetch``, ``build``, ``check``), one record each:

    acquisition    each default file requested from its reviewed URL and kept only once
                   verified: a reviewed checksum, else the declared or the server's size;
                   an HTML page saved in its place is refused (``TCMDataHub.fetch``)
    integrity      every file held to a reviewed checksum (PASS); a file kept on its size
                   alone, or on nothing, is UNVERIFIED with the SHA-256 that was read, for a
                   maintainer to review and pin
    build          the SQLite store and its relations
    check          the hub's acceptance checks: two builds from the same files give the
                   same relations, every relation row uses a declared kind, evidence and
                   outcome, every kind has a licence, the dataset's own checks
    queries        every declared relation kind returns rows from this source, and every
                   table answers a query

A manual dataset is one whose files a person obtains under the source's terms (an
export, an account): it is not requested, and its record is BLOCKED with what a person
has to do and where the files go (``TCMDataHub.import_raw`` verifies them). A per-entity
dataset (``live``) is filled by calling its service for one entity, then built and
queried the same way.

Without ``live`` nothing is requested: files already in the hub are checked, and a
dataset whose files are not there is SKIPPED, never counted as passed.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED, Record, aggregate)

__all__ = ["ACCESS_MODES", "dataset_keys", "validate_dataset", "validate_datasets",
           "LIVE_PROBES"]

ACCESS_MODES = ("download", "live", "manual")

#: One entity each per-entity dataset is asked for: the first herb of each service's
#: numbering, which every release has.
LIVE_PROBES = {"symmap_api": ("enrich_symmap", ["SMHB00001"]),
               "herb_api": ("enrich_herb", ["HERB000001"])}


def dataset_keys(access: Iterable[str] = ACCESS_MODES) -> list[str]:
    """Every hub dataset with one of the ``access`` modes, each once, in definition order."""
    from ..tcmdb import datasets as D

    wanted = set(access)
    seen: dict[str, Any] = {}
    for spec in (*D.DATASETS, *(D.EXTRA_DATASETS or ())):
        seen.setdefault(spec.key, spec)
    return [k for k, s in seen.items() if s.access in wanted]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def _files(spec: Any) -> list[Any]:
    """The files a dataset is validated on: its default files, or, for a dataset that
    has only optional ones (a superseded release, a large export), those."""
    default = [f for f in spec.files if not f.optional]
    return default or list(spec.files)


def _present(hub: Any, spec: Any) -> list[dict[str, Any]]:
    """Offline: the files already in the hub, verified against what can be."""
    raw = hub.raw_dir(spec.key)
    out = []
    for f in _files(spec):
        path = raw / f.name
        if not path.is_file():
            out.append({"file": f.name, "ok": False, "status": SKIPPED,
                        "reason": "not here, and nothing is requested without --live"})
            continue
        digest = _sha256(path)
        size = path.stat().st_size
        if f.checksum:
            ok = digest.split(":", 1)[1] == f.checksum.split(":", 1)[-1]
            status, why = (PASS, "hashes to the reviewed checksum") if ok else (
                FAIL, f"{digest} is not the reviewed {f.checksum}")
        elif f.expected_bytes:
            ok = size == f.expected_bytes
            status, why = (UNVERIFIED, "the declared size, no reviewed checksum") if ok else (
                FAIL, f"{size} bytes, not the declared {f.expected_bytes}")
        else:
            ok, status, why = True, UNVERIFIED, "no reviewed checksum or size to check"
        out.append({"file": f.name, "ok": ok, "status": status, "reason": why,
                    "bytes": size, "checksum": digest,
                    "verified": "checksum" if f.checksum and ok else
                                ("size" if f.expected_bytes and ok else "none")})
    return out


def _acquisition(rec: Record, spec: Any, results: Sequence[dict[str, Any]]) -> bool:
    files = {r["file"]: {k: r.get(k) for k in ("status", "reason", "bytes", "checksum",
                                                "verified", "cached")}
             for r in results}
    urls = {f.name: f.url for f in spec.files}
    for name, row in files.items():
        row["url"] = urls.get(name)
    statuses = [r.get("status") or (PASS if r.get("ok") else FAIL) for r in results]
    status = aggregate(statuses) if statuses else NOT_APPLICABLE
    bad = [f"{r['file']}: {r.get('status')}: {str(r.get('reason') or '')[:200]}"
           for r in results if r.get("status") not in (PASS, UNVERIFIED)]
    kept = [r for r in results if r.get("ok")]
    rec.add("acquisition", status,
            f"{len(kept)} of {len(results)} default file(s) obtained and kept"
            + (f"; {'; '.join(bad)}" if bad else ""), files=files)
    if not results:
        return False
    if all(r.get("ok") for r in results):
        unpinned = {r["file"]: r.get("checksum") for r in results
                    if r.get("verified") != "checksum"}
        rec.add("integrity", UNVERIFIED if unpinned else PASS,
                "every file hashes to its reviewed checksum" if not unpinned else
                f"{len(unpinned)} of {len(results)} file(s) have no reviewed checksum; the "
                f"SHA-256 read is recorded for review: "
                + "; ".join(f"{n} {c}" for n, c in list(unpinned.items())[:6]),
                unpinned=unpinned)
        return True
    return False


def _queries(rec: Record, hub: Any, spec: Any, report: dict[str, Any]) -> None:
    empty_kinds, kinds_ok = [], []
    for kind in spec.relations:
        rows = hub.relations(kind, sources=[spec.key], limit=1)
        (kinds_ok if rows else empty_kinds).append(kind)
    # the tables loaded from the source's files; the hub's own (relations, unresolved) are
    # not the source's, and an empty unresolved table is the good case
    tables = list((report.get("tables") or {}))
    empty_tables, tables_ok = [], []
    for table in tables:
        (tables_ok if hub.query(spec.key, table, limit=1) else empty_tables).append(table)
    problems = ([f"declared relation kind(s) with no rows from this source: {empty_kinds}"]
                if empty_kinds else []) + (
        [f"table(s) with no rows: {empty_tables}"] if empty_tables else [])
    rec.add("queries", FAIL if problems or not (tables_ok or kinds_ok) else PASS,
            "; ".join(problems) if problems else
            (f"{len(kinds_ok)} relation kind(s) ({', '.join(kinds_ok)}) and "
             if kinds_ok else "no relation kind is declared; ")
            + f"{len(tables_ok)} table(s) answer a query",
            relation_kinds=kinds_ok, empty_kinds=empty_kinds, tables=tables_ok,
            empty_tables=empty_tables)


def _skip_rest(rec: Record, why: str, stages: Sequence[str] = ("build", "check", "queries")
               ) -> None:
    for name in stages:
        rec.add(name, SKIPPED, why)


def validate_dataset(hub: Any, key: str, *, live: bool, confirm: bool = False,
                     log: Callable[[str], None] = print) -> Record:
    """One hub dataset, from acquisition to queries (see the module docstring)."""
    from ..tcmdb import HubError
    from ..tcmdb.datasets import dataset

    spec = dataset(key)
    rec = Record(f"tcmdb.{key}", "hub_dataset", spec.version,
                 input={"access": spec.access, "files": [f.name for f in _files(spec)]},
                 provenance={"name": spec.name, "homepage": spec.homepage,
                             "license": spec.license, "commercial_use": spec.commercial_use,
                             "catalogue": list(spec.catalog)},
                 reproduce=f"bioagent validate datasets --datasets {key}"
                           + (" --live" if live else ""))
    t0 = time.perf_counter()
    try:
        if spec.access == "manual":
            rec.add("acquisition", BLOCKED,
                    f"a person obtains the files under the source's terms: "
                    f"{spec.instructions or 'see the source card'} Add them with "
                    f"`bioagent tcmdb --root {hub.root} import-raw {key} FILE...`, which "
                    "verifies each as a download would be; nothing here requests them")
            _skip_rest(rec, "the files have to be obtained by a person first")
            return rec
        if spec.access == "live":
            method, ids = LIVE_PROBES.get(key, (None, []))
            if not live:
                rec.add("acquisition", SKIPPED, "a per-entity service; nothing is requested "
                                                "without --live")
                _skip_rest(rec, "not requested")
                return rec
            if method is None:
                rec.add("acquisition", UNVERIFIED, "no probe entity is defined for this service")
                _skip_rest(rec, "not requested")
                return rec
            try:
                out = getattr(hub, method)(ids, build=False, log=log)
            except Exception as exc:                            # noqa: BLE001
                from ..tcmdb import acquire
                status, reason = acquire.outcome_of_error(exc, spec.homepage or "")
                rec.add("acquisition", status, f"asking for {ids}: {reason}")
                _skip_rest(rec, "the service did not answer")
                return rec
            ok = [r for r in out if r.get("ok")]
            rec.add("acquisition", PASS if ok else BLOCKED,
                    f"{len(ok)} of {len(out)} entities answered ({ids})" if ok else
                    f"no entity answered: {[str(r.get('error') or r)[:160] for r in out][:3]}",
                    answers=[{k: r.get(k) for k in ("file", "ok", "cached", "error")}
                             for r in out][:10])
            if not ok:
                _skip_rest(rec, "the service did not answer")
                return rec
            rec.add("integrity", NOT_APPLICABLE, "a service's answers, not a released file")
        else:
            only_optional = not any(not f.optional for f in spec.files)
            results = (hub.fetch(key, include_optional=only_optional, confirm=confirm, log=log)
                       if live else _present(hub, spec))
            if not _acquisition(rec, spec, results):
                _skip_rest(rec, "the files are not all here")
                return rec
        try:
            report = hub.build(key, log=lambda m: None)
        except (HubError, Exception) as exc:                     # noqa: BLE001
            rec.add("build", FAIL, f"{type(exc).__name__}: {str(exc)[:600]}")
            _skip_rest(rec, "the store did not build", ("check", "queries"))
            return rec
        relations = report.get("relations") or {}
        tables = report.get("tables") or {}
        n_rel = sum(relations.values()) if isinstance(relations, dict) else 0
        n_rows = sum(tables.values()) if isinstance(tables, dict) else 0
        built = (n_rel > 0) if spec.relations else (n_rows > 0)
        rec.add("build", PASS if built else FAIL,
                f"{len(tables)} table(s), {n_rows} row(s); relations {relations}"
                if built else
                (f"the extractor yielded no relations from these files (tables: {tables})"
                 if spec.relations else f"no rows were loaded (tables: {tables})"),
                tables=tables, relations=relations, unresolved=report.get("unresolved"),
                digest=report.get("relations_digest"))
        if not built:
            _skip_rest(rec, "the store holds nothing to check", ("check", "queries"))
            return rec
        checked = hub.check(key, rebuild=True)
        rec.add("check", PASS if checked.get("ok") else FAIL,
                "the hub's acceptance checks pass, and a rebuild gives the same relations"
                + (f" (warnings: {'; '.join(checked.get('warnings', []))[:400]})"
                   if checked.get("warnings") else "")
                if checked.get("ok") else "; ".join(checked.get("problems", []))[:1500],
                problems=list(checked.get("problems", []))[:50],
                warnings=list(checked.get("warnings", []))[:20],
                digest=checked.get("relations_digest"))
        _queries(rec, hub, spec, report)
        return rec
    except Exception as exc:                                    # noqa: BLE001
        # a defect in this validator or the hub must not end the run for the other datasets
        rec.add("validator", FAIL, f"{type(exc).__name__}: {str(exc)[:600]}")
        return rec
    finally:
        rec.runtime_s = time.perf_counter() - t0


def validate_datasets(hub: Any, keys: Sequence[str], *, live: bool, confirm: bool = False,
                      log: Callable[[str], None] = print) -> list[Record]:
    records = []
    for key in keys:
        log(f"{key}: validating ({'live' if live else 'offline'})")
        rec = validate_dataset(hub, key, live=live, confirm=confirm, log=log)
        log(f"{key}: {rec.status}" + (f" — {rec.failure_reason}" if rec.failure_reason else ""))
        records.append(rec)
    return records
