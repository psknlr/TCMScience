"""Fetch one disease's target associations from the Open Targets Platform (the ``api`` path).

Open Targets publishes the full association tables as bulk Parquet, but one disease's
associations are a single paged GraphQL query, and a snapshot of that answer is enough for
an analysis about one indication. The fetch is the only step that touches the network: it
saves the raw answer — every page, the API and data versions, and when it was fetched — to
one JSON file, and ``parsers.opentargets`` reads that file offline like any other raw file.
Requests go through ``HTTPBackend`` (rate limit, retries, ``Retry-After``).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..backends.http import HTTPBackend, HTTPRequest
from ..status import ExecutionStatus

__all__ = ["fetch_disease_associations", "raw_file_name", "OpenTargetsFetchError"]

URL = "https://api.platform.opentargets.org/api/v4/graphql"
PAGE_SIZE = 3000                     # the API's maximum page size

_META = "{meta{apiVersion{x y z} dataVersion{year month iteration}}}"
_QUERY = ("query($id:String!,$i:Int!,$n:Int!){disease(efoId:$id){id name dbXRefs "
          "associatedTargets(page:{index:$i,size:$n}){count rows{score "
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


def fetch_disease_associations(disease_id: str, out_dir: str | Path, *,
                               backend: HTTPBackend | None = None) -> Path:
    """Fetch every associated target of ``disease_id`` (e.g. ``MONDO_0005148``)."""
    backend = backend or HTTPBackend()
    meta = _post(backend, _META, {})["meta"]
    rows: list[dict[str, Any]] = []
    disease: dict[str, Any] = {}
    index, count = 0, None
    while count is None or len(rows) < count:
        data = _post(backend, _QUERY, {"id": disease_id, "i": index, "n": PAGE_SIZE})
        disease = data.get("disease") or {}
        if not disease:
            raise OpenTargetsFetchError(f"Open Targets has no disease {disease_id!r}")
        page = disease["associatedTargets"]
        count = page["count"]
        if not page["rows"]:
            break
        rows.extend(page["rows"])
        index += 1
    if len(rows) != count:
        raise OpenTargetsFetchError(f"expected {count} associations, received {len(rows)}")
    version = meta["dataVersion"]
    out = Path(out_dir) / raw_file_name(disease_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "api_version": ".".join(str(meta["apiVersion"][k]) for k in "xyz"),
        "data_version": f"{version['year']}.{version['month']}",
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "disease": {k: disease[k] for k in ("id", "name", "dbXRefs")},
        "rows": sorted(rows, key=lambda r: r["target"]["id"]),
    }, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return out
