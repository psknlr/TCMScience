"""What every catalogued database is, here: reachable how, licensed how, verified how far.

One record per source card (``tcmdb.catalog()``, the 137 sources) and, for a card with a
dataset, its dataset spec. The checks say what is established and what is not, so a source
that is catalogued but not integrated can never read as ready for analysis:

    catalogue      the card names the source, its URL, how it is reached, its licence
    licence        the licence's reuse class (``tcmdb.spec.licence_class``); a statement
                   that names no licence is UNVERIFIED, not permission
    acquisition    how its files are obtained: by a program (download), by a person
                   (manual import), per entity (live), or not at all (restricted /
                   unreachable: BLOCKED with the card's reason)
    integrity      how a downloaded file is verified: a reviewed checksum for every default
                   file (PASS), a size only, or nothing (UNVERIFIED)
    parser         the dataset's files become tables, and it declares the relations its
                   extractor yields
    real_download  the last real download of its files, from the committed verification
                   record (``data/live_download_verification.json``): PASS with the date and
                   the CI run, or UNVERIFIED when it was never run
    recorded_build when that record holds the dataset's own live run (``bioagent validate
                   datasets --live``, the sources-live workflow): whether the files it
                   fetched were built, checked and queried there
    live_api       for a source with a connector: its operations' last recorded live calls
                   (``data/connector_live_verification.csv``), or UNVERIFIED with none
    local_build    built in this environment (SKIPPED when not: nothing here says it is not)
    query          when built: a relation of each declared kind can be read back

Downstream use is recorded beside the checks: which research tools consume the relations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED, Record, aggregate

__all__ = ["VERIFICATION_RECORD", "load_verification", "connector_record", "readiness"]

#: Committed outcomes of real downloads (CI's live-data workflow), one entry per file.
VERIFICATION_RECORD = "live_download_verification.json"

#: relation kind -> the research tools that read it
CONSUMERS = {
    "organism_compound": ["research.snapshot / research.run (via the snapshot pipeline, for "
                          "NPASS, CMAUP and LOTUS)", "tcmdb_relations", "tcmdb_consensus"],
    "compound_target": ["research.run (measured targets, enrichment)", "tcmdb_relations",
                        "tcmdb_consensus"]}


def load_verification(path: str | Path | None = None) -> dict[str, Any]:
    """The committed real-download record, ``{}`` when there is none."""
    if path is None:
        from importlib import resources
        try:
            text = resources.files("bioagent.data").joinpath(VERIFICATION_RECORD).read_text(
                encoding="utf-8")
        except (FileNotFoundError, OSError):
            return {}
    else:
        p = Path(path)
        if not p.is_file():
            return {}
        text = p.read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _real_download(spec: Any, record: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    entry = (record.get("datasets") or {}).get(spec.key)
    if entry:                                   # the dataset's own live run (validate datasets)
        files = entry.get("files") or {}
        acq = (entry.get("checks") or {}).get("acquisition", UNVERIFIED)
        kept = sum(1 for f in files.values() if f.get("status") in (PASS, UNVERIFIED))
        detail = (f"{kept} of {len(files)} files downloaded by a recorded run "
                  f"({entry.get('verified_at') or 'undated'})")
        if acq not in (PASS, UNVERIFIED) and entry.get("reason"):
            detail += f": {entry['reason'][:300]}"
        return acq, detail, {"files": {n: f.get("status") for n, f in files.items()},
                             "runs": [entry["run"]] if entry.get("run") else []}
    files = record.get("files") or {}
    default = [f for f in spec.files if not f.optional]
    if not default:
        return NOT_APPLICABLE, "no default files", {}
    rows = {f.name: files.get(f.name) for f in default}
    if not any(rows.values()):
        return UNVERIFIED, ("never downloaded from its real server by a recorded run: the "
                            "parser is tested on files with the real headers only"), {}
    statuses = {n: (r or {}).get("status", UNVERIFIED) for n, r in rows.items()}
    worst = aggregate(statuses.values())
    dates = sorted({(r or {}).get("verified_at", "") for r in rows.values() if r})
    runs = sorted({(r or {}).get("run", "") for r in rows.values() if r and r.get("run")})
    detail = (f"{sum(s == PASS for s in statuses.values())} of {len(rows)} default files "
              f"downloaded and verified ({', '.join(d for d in dates if d) or 'undated'})")
    return worst, detail, {"files": statuses, "runs": runs}


def connector_record(path: str | Path | None = None) -> dict[str, list[dict[str, str]]]:
    """The committed live verification of the connectors (``data/
    connector_live_verification.csv``, written by ``scripts/verify_connectors.py``), by
    connector key. Empty outside a checkout that has it."""
    import csv

    from ..config import REPO_ROOT

    p = Path(path) if path else REPO_ROOT / "data" / "connector_live_verification.csv"
    if not p.is_file():
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    with open(p, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(row.get("source", ""), []).append(row)
    return out


def readiness(hub: Any = None, *, verification: Mapping[str, Any] | None = None,
              connectors: Mapping[str, Any] | None = None) -> list[Record]:
    from ..tcmdb.datasets import DATASETS, dataset
    from ..tcmdb.hub import TCMDataHub, catalog
    from ..tcmdb.spec import licence_class

    hub = hub or TCMDataHub()
    record = dict(verification if verification is not None else load_verification())
    live = dict(connectors if connectors is not None else connector_record())
    keys = {d.key for d in DATASETS}
    status = {s["dataset"]: s for s in hub.status()} if hub is not None else {}
    out = []
    for card in catalog():
        rec = Record(f"source.{card.no}", "database", "",
                     provenance={"name": card.name, "url": card.url, "access": card.access,
                                 "licence": card.license, "commercial_use": card.commercial_use,
                                 "checked": card.checked, "origin": card.origin,
                                 "dataset": card.dataset, "connector": card.connector})
        missing = [f for f in ("name", "url", "access", "license") if not getattr(card, f)]
        rec.add("catalogue", FAIL if missing else PASS,
                f"missing {missing}" if missing else f"{card.access}; checked {card.checked}")
        cls = licence_class(card.license)
        rec.add("licence", UNVERIFIED if cls == "unknown" else PASS,
                f"{card.license!r}: {cls}" + (" — not permission" if cls == "unknown" else ""),
                reuse_class=cls)
        spec = dataset(card.dataset) if card.dataset in keys else None
        if card.access in ("restricted", "unreachable"):
            rec.add("acquisition", BLOCKED, card.barriers or card.assessment or card.access)
        elif spec is None:
            how = ("a live connector" if card.callable_live else "nothing integrated")
            rec.add("acquisition", PASS if card.callable_live else UNVERIFIED,
                    f"no dataset; {how}" + (f" ({card.connector})" if card.connector else ""))
        elif spec.access == "manual":
            rec.add("acquisition", NOT_APPLICABLE,
                    f"a person exports the files: {spec.instructions[:300]}")
        elif spec.access == "live":
            rec.add("acquisition", PASS, "filled per entity through its connector")
        else:
            rec.add("acquisition", PASS, f"{len(spec.files)} files from reviewed URLs")
        _dataset_checks(rec, spec, record)
        if card.connector:
            rows = live.get(card.connector) or []
            if not rows:
                rec.add("live_api", UNVERIFIED, f"{card.connector}: no recorded live call "
                        "(scripts/verify_connectors.py --only " + card.connector + ")")
            else:
                bad = [r for r in rows if r.get("status") != "SUCCEEDED"]
                latest = max(r.get("verified_at", "") for r in rows)
                rec.add("live_api", FAIL if bad else PASS,
                        f"{len(rows) - len(bad)} of {len(rows)} operations answered when last "
                        f"called ({latest[:10]})" + (f"; failed: "
                        f"{sorted(r.get('operation', '') for r in bad)}" if bad else ""),
                        verified_at=latest)
        _build_checks(rec, spec, hub, status)
        if spec is not None:
            rec.provenance["consumers"] = sorted({c for k in spec.relations
                                                  for c in CONSUMERS.get(k, ["tcmdb_relations"])})
        out.append(rec)
    # a dataset no source card names would otherwise be in no record at all
    named = {c.dataset for c in catalog() if c.dataset}
    for spec in DATASETS:
        if spec.key in named:
            continue
        rec = Record(f"dataset.{spec.key}", "database", spec.version,
                     provenance={"name": spec.name, "url": spec.homepage, "access": spec.access,
                                 "licence": spec.license, "commercial_use": spec.commercial_use,
                                 "dataset": spec.key, "catalogue": list(spec.catalog)})
        rec.add("catalogue", UNVERIFIED,
                f"no source card names this dataset (it cites card(s) {list(spec.catalog)})")
        cls = licence_class(spec.license)
        rec.add("licence", UNVERIFIED if cls == "unknown" else PASS,
                f"{spec.license!r}: {cls}" + (" — not permission" if cls == "unknown" else ""),
                reuse_class=cls)
        rec.add("acquisition", NOT_APPLICABLE if spec.access == "manual" else PASS,
                {"manual": f"a person exports the files: {spec.instructions[:300]}",
                 "live": "filled per entity through its connector"}.get(
                    spec.access, f"{len(spec.files)} files from reviewed URLs"))
        _dataset_checks(rec, spec, record)
        _build_checks(rec, spec, hub, status)
        out.append(rec)
    return out


def _dataset_checks(rec: Record, spec: Any, record: Mapping[str, Any]) -> None:
    """The checks that belong to a hub dataset rather than to its source card."""
    if spec is not None and spec.access == "download":
        default = [f for f in spec.files if not f.optional]
        sums = sum(1 for f in default if f.checksum)
        sizes = sum(1 for f in default if f.expected_bytes is not None)
        rec.add("integrity", PASS if default and sums == len(default) else UNVERIFIED,
                f"{sums} of {len(default)} default files with a reviewed checksum, "
                f"{sizes} with a reviewed size",
                expected_bytes=sum(f.expected_bytes or 0 for f in default) or None)
        tables = [f for f in spec.files if f.table]
        rec.add("parser", PASS if tables and spec.relations else UNVERIFIED,
                f"{len(tables)} tables; relations {list(spec.relations) or 'none declared'}",
                formats=sorted({f.fmt for f in spec.files}))
        s, d, ev = _real_download(spec, record)
        rec.add("real_download", s, d, **ev)
    else:
        for name in ("integrity", "parser", "real_download"):
            rec.add(name, NOT_APPLICABLE, "no files are downloaded by a program")
    entry = (record.get("datasets") or {}).get(spec.key) if spec is not None else None
    if entry:
        stages = {k: v for k, v in (entry.get("checks") or {}).items()
                  if k in ("build", "check", "queries")}
        rec.add("recorded_build", aggregate(stages.values()) if stages else SKIPPED,
                f"in the recorded live run ({entry.get('verified_at') or 'undated'}): "
                + (", ".join(f"{k} {v}" for k, v in stages.items()) or "not reached"),
                stages=stages, run=entry.get("run"))


def _build_checks(rec: Record, spec: Any, hub: Any, status: Mapping[str, Any]) -> None:
    """Built here, and queried here."""
    here = status.get(spec.key) if spec is not None else None
    if spec is None:
        rec.add("local_build", NOT_APPLICABLE, "no dataset")
    elif here and here.get("built"):
        rec.add("local_build", PASS, f"built: {here.get('relations')}")
        try:
            rows = {k: len(hub.relations(k, sources=[spec.key], limit=1))
                    for k in spec.relations}
        except Exception as exc:                            # noqa: BLE001
            rec.add("query", FAIL, f"{type(exc).__name__}: {exc}")
        else:
            empty = [k for k, n in rows.items() if not n]
            rec.add("query", FAIL if empty else PASS,
                    f"no rows of {empty}" if empty else
                    f"a row of each declared kind reads back ({', '.join(rows)})")
    else:
        rec.add("local_build", SKIPPED, "not built in this environment")
        rec.add("query", SKIPPED, "not built in this environment")
