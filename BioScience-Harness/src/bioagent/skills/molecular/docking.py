"""`dock-ligands` — molecular docking as a governed skill.

Runs :func:`bioagent.docking.pipeline.run_docking`. A docked pose is a computational
prediction (design ``docking``), so the most it can support is a ``mechanism_hypothesis``,
and only from a validated setup: a claim is made per ligand only when redocking the
co-crystal ligand reproduced its crystal pose within 2 Å. Each claim's evidence is the
ligand's row of ``scores.tsv`` with a quote receipt.
"""

from __future__ import annotations

import hashlib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...contracts import (CandidateClaim, Directness, EvidenceItem, EvidenceQuality,
                          SourceCard, require_declared)
from ...contracts.source_card import canonical_hash
from ..base import artifact, file_of

__all__ = ["dock_ligands"]

SKILL_ID = "dock-ligands"
SKILL_VERSION = "0.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dock_ligands(receptor: str, ligands: str, *, site_ligand: str = "",
                 site_ligand_smiles: str = "", chains: str = "", allow_remote: bool = False,
                 exhaustiveness: int = 8, out_dir: str = "", run_id: str = "") -> Any:
    from ...docking.pipeline import DockConfig, run_docking

    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bioagent-dock-"))
    started = _now()
    run = run_docking(receptor, ligands, DockConfig(
        site_ligand=site_ligand or None, site_ligand_smiles=site_ligand_smiles or None,
        chains=tuple(chains), allow_remote=bool(allow_remote),
        exhaustiveness=int(exhaustiveness)), out)
    src = run.receptor.source
    receptor_card = SourceCard(
        id=f"structure.{hashlib.sha256(str(src.get('sha256')).encode()).hexdigest()[:12]}",
        name=f"receptor {src.get('receptor', '')}", kind="dataset",
        maintainer="RCSB PDB" if str(src.get("receptor", "")).startswith("PDB") else "the user",
        access_method="local_file", license_spdx=("CC0-1.0" if "CC0" in str(src.get("licence"))
                                                   else "LicenseRef-user-supplied"),
        integration_mode="native", offline_capable=True,
        snapshot_hash=str(src.get("sha256", canonical_hash(src))), snapshot_at=started)
    ligand_card = SourceCard(
        id=f"user.ligands.{canonical_hash(run.manifest['ligands'])[:12]}", name="ligands",
        kind="dataset", maintainer="the user", access_method="local_file",
        license_spdx="LicenseRef-user-supplied", integration_mode="native",
        offline_capable=True, snapshot_hash=canonical_hash(run.manifest["ligands"]),
        snapshot_at=started)
    table = (out / "scores.tsv").read_text(encoding="utf-8")
    rows = {ln.split("\t", 1)[0]: ln for ln in table.splitlines()[1:]}
    evidence, claims = [], []
    site = run.box.source
    if run.validated:
        for r in run.results:
            if r.best is None or r.ligand.name not in rows:
                continue
            item = EvidenceItem(
                id=f"dock.{r.ligand.name}", design="docking", quote=rows[r.ligand.name],
                citation=f"scores.tsv, row {r.ligand.name}",
                identifier=f"scores.tsv#{r.ligand.name}", identifier_type="local_artifact",
                source_card_id=ligand_card.id, subject=r.ligand.smiles,
                population="in silico", outcome="predicted binding pose and Vina score",
                effect=f"Vina score {r.best.score} kcal/mol",
                quality=EvidenceQuality(
                    directness=Directness.DIRECT,
                    rationale={"directness": "the docking run itself; a prediction from a "
                               "setup validated by redocking"},
                    assessed_by=SKILL_ID, assessment_tool="vina-redock-validated"),
                retrieved_by=SKILL_ID, retrieval_run=run_id)
            evidence.append(item.located_in(table))
            claims.append(CandidateClaim(
                id=f"dock.hypothesis.{r.ligand.name}",
                text=(f"Docking predicts that {r.ligand.name} can occupy the {site} site of "
                      f"{src.get('receptor', 'the receptor')} with a Vina score of "
                      f"{r.best.score} kcal/mol; a binding hypothesis to test"),
                claim_kind="mechanism_hypothesis", subject=r.ligand.name,
                predicate="predicted_to_bind", object=str(src.get("receptor", "")),
                supports=(item.id,), asserted_population="in silico",
                supported_population="in silico",
                asserted_outcome="predicted binding pose and Vina score",
                supported_outcome="predicted binding pose and Vina score",
                direction="unclear", hedged=True, confidence=0.2,
                confidence_basis=("a Vina score from a rigid receptor; redocking validated the "
                                  f"site ({run.validation['top_pose_rmsd']:.2f} Å)"),
                falsified_by="a binding or inhibition assay that shows no activity",
                rationale="prioritising compounds for an assay",
                produced_by=SKILL_ID))
    for c in claims:
        require_declared(c)
    outputs = [file_of("scores.tsv", table, media_type="text/tab-separated-values",
                       description="best Vina score per ligand"),
               file_of("report.md", (out / "report.md").read_text(encoding="utf-8"),
                       media_type="text/markdown",
                       description="the report; poses and the receptor are in the run "
                                   "directory")]
    limitations = [
        "a docking score is the scoring function's estimate, not a measured affinity",
        "rigid receptor; protonation and tautomers as given",
        "a pose is a hypothesis about binding; a hit needs an assay",
    ]
    if not run.validated:
        limitations.append("the setup was not validated by redocking a co-crystal ligand, "
                           "so no claim is made")
    limitations += run.warnings
    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question=f"how do these ligands dock into {src.get('receptor', 'the receptor')}?",
        sources=(receptor_card, ligand_card), evidence=evidence, claims=claims,
        outputs=outputs, limitations=limitations,
        assumptions=("the site is where the ligands act",), created_at=_now(),
        provenance={"pipeline": "bioagent.docking", "run_dir": str(out),
                    "validated": run.validated, "validation": run.validation})
