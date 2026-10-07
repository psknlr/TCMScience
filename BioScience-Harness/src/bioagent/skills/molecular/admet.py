"""`predict-admet` — ADMET prediction as a governed skill.

Runs :func:`bioagent.admet.pipeline.run_admet`. Every number it produces is a model's
prediction or a rule's verdict on computed descriptors, so the artifact makes no claim:
properties, alerts and predicted endpoints are outputs, each prediction with its
model's held-out score and its applicability-domain flag.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...contracts import SourceCard
from ...contracts.source_card import canonical_hash
from ..base import artifact, file_of

__all__ = ["predict_admet"]

SKILL_ID = "predict-admet"
SKILL_VERSION = "0.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def predict_admet(molecules: str, *, cache_dir: str = "", out_dir: str = "",
                  run_id: str = "") -> Any:
    from ...admet.pipeline import AdmetConfig, run_admet

    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bioagent-admet-"))
    started = _now()
    run = run_admet(molecules, AdmetConfig(cache_dir=cache_dir or None), out)
    sources = [SourceCard(
        id=f"user.molecules.{canonical_hash(run.manifest['molecules'])[:12]}",
        name="query molecules", kind="dataset", maintainer="the user",
        access_method="local_file", license_spdx="LicenseRef-user-supplied",
        integration_mode="native", offline_capable=True,
        snapshot_hash=canonical_hash(run.manifest["molecules"]), snapshot_at=started)]
    if run.cards:
        archive = next(iter(run.cards.values()))["archive_sha256"]
        sources.append(SourceCard(
            id="tdc.admet_group", name="Therapeutics Data Commons ADMET benchmark group",
            kind="dataset", maintainer="TDC (Huang et al. 2021)",
            home_url="https://tdcommons.ai", access_method="bulk_download",
            allowed_hosts=("dataverse.harvard.edu",),
            license_spdx="LicenseRef-TDC-per-dataset",
            license_note="TDC's code is MIT; each dataset keeps the terms of its original "
                         "publication",
            integration_mode="native", snapshot_hash=archive, snapshot_at=started,
            known_limits=("scaffold-split held-out scores are in each model card",
                          "models trained here from the archive; none is shipped")))
    outputs = [file_of("admet.tsv", (out / "admet.tsv").read_text(encoding="utf-8"),
                       media_type="text/tab-separated-values",
                       description="per molecule: properties, rules, alerts, predictions"),
               file_of("report.md", (out / "report.md").read_text(encoding="utf-8"),
                       media_type="text/markdown", description="the report")]
    limitations = [
        "every endpoint is a model's prediction, with the held-out error its model card "
        "states; none is a measurement",
        "predictions for molecules outside a model's applicability domain are unreliable",
        "a structural alert is a reason to look closer, not a verdict",
    ] + run.warnings
    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question="what ADMET profile is predicted for these molecules?",
        sources=tuple(sources), evidence=(), claims=(), outputs=tuple(outputs),
        limitations=limitations, assumptions=("the structures are the intended compounds",),
        created_at=_now(),
        provenance={"pipeline": "bioagent.admet", "run_dir": str(out),
                    "models_built": run.models_built, "endpoints": list(run.cards)})
