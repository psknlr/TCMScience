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
dataset (``live``) is filled by calling its service for the entities of ``LIVE_PROBES``,
one for each kind of entity its declared relations start from, then built and queried
the same way.

A dataset is validated on its default files plus the optional files a declared relation
kind comes from (``DatasetSpec.optional_relations``); one whose default files hold no
table at all is validated on all of its files. One whose files are all kept as files
(FAERS's quarterly zip, MeSH's XML) is fetched and verified, and its build is
NOT_APPLICABLE. Relation rows that come from per-entity tables fetched on request
(ChIP-Atlas's Target Genes, per transcription factor) are fetched for one entity
(``ENTITY_TABLES``). A kind or table the spec says the pinned release leaves empty
(``DatasetSpec.known_empty``) is reported as such, with how that is known.

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
           "LIVE_PROBES", "ENTITY_TABLES"]

ACCESS_MODES = ("download", "live", "manual")

#: What each per-entity dataset is asked for, as (hub method, entity ids, arguments):
#: enough entities that every relation kind the dataset declares can come back. A herb
#: answers the herb_* kinds and an ingredient (quercetin: SymMap SMIT00013, HERB
#: HBIN041495) the ingredient_* kinds. HERB's first herb, HERB000001, is a gelatin with
#: no inferred targets or diseases (run 38157405923), so two plant herbs stand in for it:
#: Ai Ye (HERB000066, SymMap SMHB00002) and HERB002560.
LIVE_PROBES: dict[str, tuple[tuple[str, tuple[str, ...], dict[str, Any]], ...]] = {
    "symmap_api": (("enrich_symmap", ("SMHB00001",), {}),
                   ("enrich_symmap", ("SMIT00013",), {"related": ("Gene",)})),
    "herb_api": (("enrich_herb", ("HERB000066", "HERB002560"), {"label": "Herb"}),
                 ("enrich_herb", ("HBIN041495",), {"label": "Ingredient"})),
}


#: Downloaded datasets whose relation rows come from tables fetched per entity on
#: request, after the default files: (module, function, entities, arguments) of the call
#: that fetches one. ChIP-Atlas's Target Genes are fetched per transcription factor
#: (STAT3's hg38 table, a few MB), never in bulk.
ENTITY_TABLES: dict[str, tuple[str, str, tuple[str, ...], dict[str, Any]]] = {
    "chip_atlas": ("bioagent.tcmdb.extra.rna_reg", "fetch_target_genes", ("STAT3",),
                   {"genome": "hg38", "distance": 5}),
}


def _entity_tables(rec: Record, hub: Any, key: str, *, live: bool,
                   log: Callable[[str], None]) -> bool:
    """Fetch (live) the per-entity tables a dataset's relations come from."""
    import importlib

    module, function, entities, kwargs = ENTITY_TABLES[key]
    if not live:
        rec.add("entity_tables", SKIPPED,
                f"{function}({list(entities)}) is not called without --live; tables already "
                "in the hub are used")
        return True
    try:
        out = getattr(importlib.import_module(module), function)(
            hub, list(entities), build=False, log=log, **kwargs)
    except Exception as exc:                                    # noqa: BLE001
        from ..tcmdb import acquire
        from ..tcmdb.datasets import dataset
        status, reason = acquire.outcome_of_error(exc, dataset(key).homepage or key)
        rec.add("entity_tables", status, f"{function}({list(entities)}): {reason}")
        return False
    ok = [r for r in out if r.get("ok")]
    rec.add("entity_tables", PASS if ok and len(ok) == len(out) else FAIL,
            f"{function}: {len(ok)} of {len(out)} table(s) fetched ({', '.join(entities)})"
            + ("" if len(ok) == len(out) else
               f"; {[str(r.get('error'))[:160] for r in out if not r.get('ok')]}"),
            tables=[{k: r.get(k) for k in ("antigen", "file", "bytes", "cached", "ok", "error")}
                    for r in out])
    return len(ok) == len(out) and bool(ok)


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
    """The files a dataset is validated on.

    Its default files, plus the optional files the spec says a declared relation kind
    comes from (``optional_relations``: BioGRID's complete file for genetic
    interactions). A dataset whose default files carry no table at all (a superseded
    release, an export behind a README) is validated on all of its files.
    """
    needed = {n for names in (getattr(spec, "optional_relations", None) or {}).values()
              for n in names}
    if not needed and not any(f.table for f in spec.files if not f.optional):
        return list(spec.files)
    return [f for f in spec.files if not f.optional or f.name in needed]


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
            from ..tcmdb.acquire import structure
            whole, how = structure(path)
            ok = whole is not False
            status, why = ((UNVERIFIED, "no reviewed checksum or size to check")
                           if whole is None else
                           (UNVERIFIED, f"whole ({how}); no reviewed checksum or size")
                           if whole else (FAIL, f"incomplete or corrupt: {how}"))
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
    # a source that could not be reached from here can still be added by a person who
    # reaches it (a browser), verified the same way: said, never attempted around
    other_way = (f"; a person who can reach it can add the files with `bioagent tcmdb "
                 f"import-raw {spec.key} FILE...`, which verifies each as a download would be"
                 if status == BLOCKED else "")
    rec.add("acquisition", status,
            f"{len(kept)} of {len(results)} file(s) obtained and kept"
            + (f"; {'; '.join(bad)}" if bad else "") + other_way, files=files)
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
    # a kind or table the spec says the pinned release leaves empty is reported as such,
    # with how that is known, instead of failing (``DatasetSpec.known_empty``)
    known = dict(getattr(spec, "known_empty", None) or {})
    empty_kinds, kinds_ok, known_hit = [], [], []
    for kind in spec.relations:
        rows = hub.relations(kind, sources=[spec.key], limit=1)
        (kinds_ok if rows else known_hit if kind in known else empty_kinds).append(kind)
    # the tables loaded from the source's files; the hub's own (relations, unresolved) are
    # not the source's, and an empty unresolved table is the good case
    tables = list((report.get("tables") or {}))
    empty_tables, tables_ok = [], []
    for table in tables:
        if hub.query(spec.key, table, limit=1):
            tables_ok.append(table)
        else:
            (known_hit if table in known else empty_tables).append(table)
    shapes = _response_shapes(hub, spec) if empty_kinds and spec.access == "live" else {}
    problems = ([f"declared relation kind(s) with no rows from this source: {empty_kinds}"
                 + (f" (the answers hold: {shapes})" if shapes else "")]
                if empty_kinds else []) + (
        [f"table(s) with no rows: {empty_tables}"] if empty_tables else [])
    noted = (f"; empty as the spec says this release leaves them ({', '.join(known_hit)}): "
             f"{known[known_hit[0]]}" if known_hit else "")
    rec.add("queries", FAIL if problems or not (tables_ok or kinds_ok) else PASS,
            ("; ".join(problems) if problems else
             (f"{len(kinds_ok)} relation kind(s) ({', '.join(kinds_ok)}) and "
              if kinds_ok else "no relation kind is declared; ")
             + f"{len(tables_ok)} table(s) answer a query") + noted,
            relation_kinds=kinds_ok, empty_kinds=empty_kinds, tables=tables_ok,
            empty_tables=empty_tables, known_empty=known_hit,
            **({"answer_shapes": shapes} if shapes else {}))


def _response_shapes(hub: Any, spec: Any) -> dict[str, Any]:
    """What a per-entity service's cached answers hold, part by part (the number of rows
    under each top-level key), so a relation kind that came back empty can be told from
    one the parser missed."""
    import json

    out: dict[str, Any] = {}
    for path in sorted(hub.raw_dir(spec.key).glob("*.json"))[:12]:
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("value")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, dict):
            out[path.stem] = {k: (len(v) if isinstance(v, (list, dict)) else type(v).__name__)
                              for k, v in list(value.items())[:30]}
        else:
            out[path.stem] = type(value).__name__
    return out


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
            probes = LIVE_PROBES.get(key, ())
            if not live:
                rec.add("acquisition", SKIPPED, "a per-entity service; nothing is requested "
                                                "without --live")
                _skip_rest(rec, "not requested")
                return rec
            if not probes:
                rec.add("acquisition", UNVERIFIED, "no probe entity is defined for this service")
                _skip_rest(rec, "not requested")
                return rec
            ids = [i for _, entities, _ in probes for i in entities]
            out: list[dict[str, Any]] = []
            try:
                for method, entities, kwargs in probes:
                    out += getattr(hub, method)(list(entities), build=False, log=log, **kwargs)
            except Exception as exc:                            # noqa: BLE001
                from ..tcmdb import acquire
                status, reason = acquire.outcome_of_error(exc, spec.homepage or "")
                rec.add("acquisition", status, f"asking for {ids}: {reason}")
                _skip_rest(rec, "the service did not answer")
                return rec
            ok = [r for r in out if r.get("ok")]
            failed = [f"{r.get('file')}: {str(r.get('error') or 'no answer')[:160]}"
                      for r in out if not r.get("ok")]
            rec.add("acquisition", (PASS if len(ok) == len(out) else FAIL) if ok else BLOCKED,
                    f"{len(ok)} of {len(out)} answers kept ({', '.join(ids)})"
                    + (f"; {'; '.join(failed[:4])}" if failed else "") if ok else
                    f"no entity answered: {failed[:3]}",
                    answers=[{k: r.get(k) for k in ("file", "ok", "cached", "error")}
                             for r in out][:20])
            if len(ok) != len(out):
                _skip_rest(rec, "the service did not answer every request"
                           if ok else "the service did not answer")
                return rec
            rec.add("integrity", NOT_APPLICABLE, "a service's answers, not a released file")
        else:
            results = (hub.fetch(key, files=[f.name for f in _files(spec)], confirm=confirm,
                                 log=log)
                       if live else _present(hub, spec))
            if not _acquisition(rec, spec, results):
                _skip_rest(rec, "the files are not all here")
                return rec
            if key in ENTITY_TABLES and not _entity_tables(rec, hub, key, live=live, log=log):
                _skip_rest(rec, "the per-entity tables the relations come from are not here")
                return rec
            if not any(f.table for f in _files(spec)):
                for stage in ("build", "check", "queries"):
                    rec.add(stage, NOT_APPLICABLE,
                            "the files are kept as files for offline use (for example "
                            "FAERS's quarterly zip, MeSH's XML); nothing is turned into rows")
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
