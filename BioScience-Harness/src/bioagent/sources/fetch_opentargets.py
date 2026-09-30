"""Fetch one disease's target associations from the Open Targets Platform (the ``api`` path).

Open Targets publishes the full association tables as bulk Parquet, but one disease's
associations are one GraphQL query, and a snapshot of that answer is enough for an analysis
about one indication. The fetch is the only step that touches the network: it saves the raw
answer — every association, the API and data versions, and when it was fetched — to one
JSON file, and ``parsers.opentargets`` reads that file offline like any other raw file.
Requests go through ``HTTPBackend`` (rate limit, retries, ``Retry-After``).

An answer longer than one page (at most 3,000 rows) is **not** paged. The API orders targets
with tied scores differently on every request, so consecutive pages of one list can repeat
some targets and never show others while the total still comes out right: on 2026-09-30,
paging the 10,206 targets of type 2 diabetes repeated 1 to 11 of them per attempt, and two
attempts together still missed 10. Instead the targets are split by Ensembl id prefix
(``BFilter``) until every group fits in one page, and each group is fetched whole in one
request — the groups are disjoint, so nothing depends on how the API breaks ties.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from ..backends.http import HTTPBackend, HTTPRequest
from ..status import ExecutionStatus

__all__ = ["fetch_disease_associations", "raw_file_name", "OpenTargetsFetchError"]

URL = "https://api.platform.opentargets.org/api/v4/graphql"
PAGE_SIZE = 3000                     # the API's maximum page size (it refuses 3,001)

#: Every Open Targets target is a human Ensembl gene: ``ENSG`` and eleven digits.
ID_PREFIX, ID_LENGTH, DIGITS = "ENSG", 15, "0123456789"

_META = "{meta{apiVersion{x y z} dataVersion{year month iteration}}}"
# ``BFilter`` keeps the targets whose id (or symbol) starts with the given string.
_QUERY = ("query($id:String!,$i:Int!,$n:Int!,$f:String){disease(efoId:$id){id name dbXRefs "
          "associatedTargets(BFilter:$f,page:{index:$i,size:$n}){count rows{score "
          "datatypeScores{id score} target{id approvedSymbol approvedName "
          "proteinIds{id source}}}}}}")


class OpenTargetsFetchError(RuntimeError):
    pass


def raw_file_name(disease_id: str) -> str:
    return f"opentargets_{disease_id}.json"


def _post(backend: HTTPBackend, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    status, value, err, _ = backend.request(
        HTTPRequest(url=URL, method="POST", json_body={"query": query, "variables": variables},
                    accept="application/json"), use_cache=False)
    if status is not ExecutionStatus.SUCCEEDED or not isinstance(value, dict):
        raise OpenTargetsFetchError(f"Open Targets request failed ({status.value}): {err}")
    if value.get("errors"):
        raise OpenTargetsFetchError(f"Open Targets errors: {value['errors']}")
    return value["data"]


def _page(backend: HTTPBackend, disease_id: str, prefix: str | None,
          size: int) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    """The first ``size`` associations of targets whose id starts with ``prefix``."""
    data = _post(backend, _QUERY, {"id": disease_id, "i": 0, "n": size, "f": prefix})
    disease = data.get("disease") or {}
    if not disease:
        raise OpenTargetsFetchError(f"Open Targets has no disease {disease_id!r}")
    page = disease["associatedTargets"]
    return disease, page["count"], page["rows"]


def _tile(backend: HTTPBackend, disease_id: str, prefix: str,
          count: int) -> list[dict[str, Any]]:
    """The ``count`` associations of targets under ``prefix``, each group in one request.

    A group that fits in a page is fetched whole; a larger one is split by the next digit
    of the id, counting each part first with an empty page.
    """
    if count <= PAGE_SIZE:
        _, n, rows = _page(backend, disease_id, prefix, PAGE_SIZE)
        if n != count or len(rows) != n:
            raise OpenTargetsFetchError(
                f"expected {count} associations under {prefix}, received {len(rows)} "
                f"(the page counts {n})")
        return rows
    if len(prefix) >= ID_LENGTH:
        raise OpenTargetsFetchError(f"{count} associations name the one target {prefix}")
    rows: list[dict[str, Any]] = []
    found = 0
    for digit in DIGITS:
        _, n, _ = _page(backend, disease_id, prefix + digit, 0)
        if n:
            rows.extend(_tile(backend, disease_id, prefix + digit, n))
        found += n
        if found >= count:           # every target is accounted for; the rest are empty
            break
    if found != count:
        raise OpenTargetsFetchError(
            f"the {count} associations under {prefix} come to {found} when split by the "
            f"next digit: some target ids are not {prefix} and digits, so the answer "
            "cannot be split into pages that each hold a whole group")
    return rows


def _canonical(row: dict[str, Any]) -> dict[str, Any]:
    """``row`` with its lists in a fixed order; the API varies it from request to request."""
    target = row["target"]
    return {**row,
            "datatypeScores": sorted(row.get("datatypeScores") or [], key=lambda d: d["id"]),
            "target": {**target, "proteinIds": sorted(
                target.get("proteinIds") or [],
                key=lambda p: (p.get("source") or "", p.get("id") or ""))}}


def fetch_disease_associations(disease_id: str, out_dir: str | Path, *,
                               backend: HTTPBackend | None = None) -> Path:
    """Fetch every associated target of ``disease_id`` (e.g. ``MONDO_0005148``)."""
    backend = backend or HTTPBackend()
    meta = _post(backend, _META, {})["meta"]
    disease, count, rows = _page(backend, disease_id, None, PAGE_SIZE)
    if count > PAGE_SIZE:
        rows = _tile(backend, disease_id, ID_PREFIX, count)
    if len(rows) != count:
        raise OpenTargetsFetchError(f"expected {count} associations, received {len(rows)}")
    # A matching total does not prove the answer is whole: were one target in two groups
    # (``BFilter`` also matches symbols), another would be missing and the count would
    # still come out right. Every target must appear exactly once.
    seen = Counter(r["target"]["id"] for r in rows)
    repeated = sorted(i for i, n in seen.items() if n > 1)
    if repeated:
        raise OpenTargetsFetchError(
            f"the answer repeats {len(repeated)} target(s) (e.g. {repeated[:3]}) and so "
            "misses as many others")
    after = _post(backend, _META, {})["meta"]
    if after["dataVersion"] != meta["dataVersion"]:
        raise OpenTargetsFetchError(
            f"the data version changed during the fetch ({meta['dataVersion']} -> "
            f"{after['dataVersion']}); fetch again")
    version = meta["dataVersion"]
    out = Path(out_dir) / raw_file_name(disease_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")     # renamed into place once whole
    part.write_text(json.dumps({
        "api_version": ".".join(str(meta["apiVersion"][k]) for k in "xyz"),
        "data_version": f"{version['year']}.{version['month']}",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disease": {k: disease[k] for k in ("id", "name", "dbXRefs")},
        "rows": sorted((_canonical(r) for r in rows), key=lambda r: r["target"]["id"]),
    }, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(part, out)
    return out
