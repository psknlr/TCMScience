"""Fetch PubChem BioAssay results for a set of compounds (the ``api`` path).

Curated activity databases record mostly *active* results, so the proteins a compound set
was tested against cannot be told apart from the ones it hit. PubChem BioAssay keeps
every deposited outcome, inactive ones included. For a compound set (a formula's
constituents) this module saves, per compound, every assay result PubChem holds: InChIKey
-> CID through the property service, then the per-compound assay summaries.

The fetch is the only step that touches the network; ``parsers.pubchem_bioassay`` reads
the saved file offline. Requests go through ``HTTPBackend`` (PubChem's 5 requests/s,
retries, ``Retry-After``). A batch that is too large or too slow is split in two until it
succeeds, so no compound is silently left out; a failure that splitting cannot fix is
an error, not a smaller file.
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..backends.http import HTTPBackend, HTTPRequest
from ..status import ExecutionStatus

__all__ = ["fetch_assay_summaries", "RAW_FILE", "PubChemFetchError"]

URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
RAW_FILE = "pubchem_bioassay.json.gz"
KEY_BATCH = 100
CID_BATCH = 10
_FORM = {"Content-Type": "application/x-www-form-urlencoded"}


class PubChemFetchError(RuntimeError):
    pass


def _post(backend: HTTPBackend, path: str, field: str, ids: Sequence[Any]) -> Any | None:
    """The parsed answer, or None when PubChem has no data for any of ``ids`` (404)."""
    body = urllib.parse.urlencode({field: ",".join(map(str, ids))}).encode()
    status, value, err, meta = backend.request(
        HTTPRequest(url=f"{URL}/{path}", method="POST", data=body, headers=_FORM),
        use_cache=False)
    if status is ExecutionStatus.SUCCEEDED and isinstance(value, dict) and "Fault" not in value:
        return value
    if meta.get("http_status") == 404:
        return None
    raise PubChemFetchError(f"PubChem {path} failed for {len(ids)} ids ({status.value}): {err}")


def _split(batch: Sequence[Any], call: Callable[[Sequence[Any]], Any]) -> list[Any]:
    """``call(batch)``, halving the batch on failure down to single ids."""
    try:
        return [call(batch)]
    except PubChemFetchError:
        if len(batch) == 1:
            raise
        mid = len(batch) // 2
        return _split(batch[:mid], call) + _split(batch[mid:], call)


def fetch_assay_summaries(inchikeys: Iterable[str], out_dir: str | Path, *,
                          backend: HTTPBackend | None = None) -> Path:
    """Save every PubChem assay result for the compounds with these InChIKeys."""
    backend = backend or HTTPBackend()
    keys = sorted({k.strip().upper() for k in inchikeys if k and k.strip()})
    cids: dict[str, list[int]] = {}
    for i in range(0, len(keys), KEY_BATCH):
        for answer in _split(keys[i:i + KEY_BATCH], lambda b: _post(
                backend, "compound/inchikey/property/InChIKey/JSON", "inchikey", b)):
            for p in (answer or {}).get("PropertyTable", {}).get("Properties", []):
                cids.setdefault(p["InChIKey"], []).append(int(p["CID"]))
    all_cids = sorted({c for cs in cids.values() for c in cs})
    columns: list[str] = []
    rows: list[list[str]] = []
    for i in range(0, len(all_cids), CID_BATCH):
        for answer in _split(all_cids[i:i + CID_BATCH], lambda b: _post(
                backend, "compound/cid/assaysummary/JSON", "cid", b)):
            table = (answer or {}).get("Table") or {}
            if not table:
                continue
            cols = table["Columns"]["Column"]
            if columns and cols != columns:
                raise PubChemFetchError(f"assay summary columns changed: {cols}")
            columns = cols
            rows.extend(r["Cell"] for r in table.get("Row", []))
    out = Path(out_dir) / RAW_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service": URL,
        "inchikeys": keys,
        "cids": {k: sorted(set(v)) for k, v in sorted(cids.items())},
        "columns": columns,
        "rows": sorted(rows),
    }
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
    return out
