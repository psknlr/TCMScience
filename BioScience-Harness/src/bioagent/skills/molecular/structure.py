"""`predict-protein-structure` — structure prediction as a governed skill.

Runs :func:`bioagent.structure.fold.run_fold`. A predicted structure is a computational
result, so the artifact makes no claim: the models, their confidence and their agreement
with a reference are outputs, and every evidence item it records is ``in_silico``. Sending
a sequence to the ESM Atlas service is a disclosure and must be asked for
(``allow_remote``).
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...contracts import SourceCard
from ...contracts.source_card import canonical_hash
from ..base import artifact, file_of, json_file

__all__ = ["predict_protein_structure"]

SKILL_ID = "predict-protein-structure"
SKILL_VERSION = "0.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def predict_protein_structure(sequences: str, *, method: str = "esmatlas",
                              allow_remote: bool = False, reference: str = "",
                              out_dir: str = "", run_id: str = "") -> Any:
    """``sequences``: a FASTA file or one sequence; ``reference``: NAME=REF[,NAME=REF]."""
    from ...structure.fold import FoldConfig, run_fold

    refs = {}
    for item in [r for r in str(reference).split(",") if r.strip()]:
        name, sep, ref = item.partition("=")
        if not sep:
            raise ValueError(f"reference {item!r} is NAME=REF")
        refs[name.strip()] = ref.strip()
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bioagent-fold-"))
    started = _now()
    results = run_fold(sequences, FoldConfig(method=method, allow_remote=bool(allow_remote),
                                             references=refs), out)
    seq_digests = {r.name: hashlib.sha256(r.sequence.encode()).hexdigest() for r in results}
    sources = [SourceCard(
        id=f"user.sequences.{canonical_hash(seq_digests)[:12]}", name="query sequences",
        kind="dataset", maintainer="the user", access_method="local_file",
        license_spdx="LicenseRef-user-supplied", integration_mode="native",
        offline_capable=True, snapshot_hash=canonical_hash(seq_digests), snapshot_at=started)]
    if any(r.prediction.remote for r in results):
        sources.append(SourceCard(
            id="esmatlas.esmfold", name="ESM Metagenomic Atlas: ESMFold prediction service",
            kind="database", maintainer="Meta AI (FAIR)", version="esmfold_v1",
            home_url="https://esmatlas.com", access_method="http_api",
            allowed_hosts=("api.esmatlas.com",), license_spdx="MIT",
            integration_mode="federated",
            snapshot_hash=canonical_hash({r.name: r.prediction.response_sha256
                                          for r in results}), snapshot_at=started,
            known_limits=("a prediction service, not a database of measured structures: "
                          "each response is a model computed for the query",
                          "at most 400 residues", "single chain, no ligands",
                          "the sequence is sent to the service")))
    summary_text = (out / "summary.json").read_text(encoding="utf-8")
    outputs = [file_of("summary.json", summary_text, media_type="application/json",
                       description="per model: confidence, geometry, secondary structure, "
                                   "agreement with the reference")]
    for r in results:
        outputs.append(file_of(f"models/{r.name}.pdb", r.model_path.read_text(encoding="utf-8"),
                               media_type="chemical/x-pdb",
                               description=f"predicted model of {r.name}, pLDDT in B-factors"))
    report, _ = json_file("fold_run.json", {"out_dir": str(out), "method": method,
                                            "remote": any(r.prediction.remote for r in results),
                                            "references": refs},
                          description="where the full report is")
    outputs.append(report)
    limitations = [
        "a predicted structure is a computational result, not an experimental structure",
        "pLDDT is the method's confidence in local structure; regions below 70 should not be "
        "interpreted, and pLDDT says nothing about relative domain placement",
        "one chain without ligands, cofactors, modifications or partners",
    ]
    limitations += [w for r in results for w in r.warnings]
    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question="what structure is predicted for these sequences, and how reliable is it?",
        sources=tuple(sources), evidence=(), claims=(), outputs=tuple(outputs),
        limitations=limitations,
        assumptions=("the sequences are the mature chains of interest",),
        created_at=_now(),
        provenance={"pipeline": "bioagent.structure.fold", "method": method,
                    "remote": any(r.prediction.remote for r in results),
                    "run_dir": str(out)})
