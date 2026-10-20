"""Test only (science.mjs --mode fixture): the research snapshot build pointed at the parser tests' fixture files.

The fixtures (BioScience-Harness/tests/test_source_parsers.py: the real headers, the marker compounds of 葛根芩连汤) are
not the reviewed files, so their checksums are put in place of the reviewed ones exactly as
BioScience-Harness/tests/test_snapshot_pipeline.py does: the first file of each project by checksum, the rest by
size, the two unreviewed files by neither. The same function runs natively and in a job worker, on the same bytes,
and the two builds must give the same snapshot ids.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path


def patch(raw_dir: str) -> list[str]:
    from bioagent.acquisition.sources import ACQUIRABLE
    from bioagent.sources import pipeline

    specs, first = {}, set()
    for name, a in pipeline._SPECS.items():
        path = Path(raw_dir) / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        reviewed = a in ACQUIRABLE
        checksum = ("sha256:" + hashlib.sha256(data).hexdigest()
                    if reviewed and a.source_project not in first else None)
        first.add(a.source_project)
        specs[name] = replace(a, checksum=checksum, expected_bytes=len(data) if reviewed else None)
    pipeline._SPECS = specs
    return sorted(specs)
