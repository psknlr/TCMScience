"""`scrna-cell-atlas` — the single-cell pipeline as a governed skill.

Runs :func:`bioagent.omics.scrna.run_scrna` and returns its result as a research artifact.
Clusters, cell-type labels, the trajectory and pseudotime are outputs, not claims: a
label is an inference from marker expression and a pseudotime an ordering by
similarity, and the artifact says so. A claim is made only for a comparison between
conditions, per cell type by pseudobulk, and its kind follows the declared design as
for bulk RNA-seq: human samples support an ``association``, cells or animals a
``mechanism_hypothesis``. Its evidence is the rows of the pseudobulk tables, each with a
quote receipt.
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
from ..base import artifact, file_of, json_file
from .rnaseq import EXPERIMENT_DESIGNS, _population

__all__ = ["scrna_cell_atlas"]

SKILL_ID = "scrna-cell-atlas"
SKILL_VERSION = "0.1.0"
OUTCOME = "gene expression per cell type (pseudobulk, DESeq2 Wald test)"
MAX_EVIDENCE = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrna_cell_atlas(source: str, *, experiment_design: str = "in_vitro",
                     root: str = "", doublets: str = "remove", resolution: float = 1.0,
                     markers: str = "", contrast: str = "",
                     analysis_backend: str = "builtin", integration_method: str = "harmony",
                     de_backend: str = "builtin", out_dir: str = "",
                     run_id: str = "") -> Any:
    """The atlas as an artifact. ``analysis_backend`` (``builtin`` or ``scanpy``),
    ``integration_method`` (``none``, ``harmony`` or ``scvi``) and ``de_backend``
    (``builtin`` or ``pydeseq2``, for the pseudobulk test) are independent; what ran,
    with versions, is in the provenance."""
    from ...omics.scrna import ScConfig, run_scrna

    if experiment_design not in EXPERIMENT_DESIGNS:
        raise ValueError(f"experiment_design is one of {', '.join(EXPERIMENT_DESIGNS)}")
    parsed = None
    if contrast:
        parts = [p.strip() for p in str(contrast).split(",")]
        if len(parts) != 3:
            raise ValueError("contrast is 'factor,numerator,denominator'")
        parsed = (parts[0], parts[1], parts[2])
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bioagent-scrna-"))
    started = _now()
    run = run_scrna(source, ScConfig(root=root or None, doublets=doublets,
                                     resolution=float(resolution), markers=markers or None,
                                     contrast=parsed, analysis_backend=analysis_backend,
                                     integration_method=integration_method,
                                     de_backend=de_backend), out)
    manifest = run.manifest
    digest = hashlib.sha256(Path(source).read_bytes() if Path(source).is_file()
                            else str(source).encode()).hexdigest()
    card = SourceCard(
        id=f"user.scrna.{digest[:12]}", name=f"single-cell counts ({run.n_cells} cells)",
        kind="dataset", maintainer="the user", access_method="local_file",
        license_spdx="LicenseRef-user-supplied",
        license_note="the user's own data; its reuse terms are the user's",
        integration_mode="native", offline_capable=True,
        snapshot_hash=canonical_hash(manifest["inputs"]), snapshot_at=started,
        known_limits=(f"design declared by the caller as {experiment_design}",))

    evidence: list[EvidenceItem] = []
    tested: dict[str, int] = {}
    pb = run.pseudobulk
    if pb is not None:
        for ct, res in sorted(pb.results.items()):
            safe = "".join(ch if ch.isalnum() else "_" for ch in ct)
            text = (out / "pseudobulk" / f"{safe}.tsv").read_text(encoding="utf-8")
            lines = {ln.split("\t", 1)[0]: ln for ln in text.splitlines()[1:]}
            sig = sorted(res.significant(), key=lambda i: (res.p_adjusted[i], res.genes[i]))
            tested[ct] = len(sig)
            population = (f"{len(res.samples)} {_population(experiment_design)} samples, "
                          f"{ct}")
            for i in sig[:MAX_EVIDENCE]:
                gene = res.genes[i]
                item = EvidenceItem(
                    id=f"pb.{safe}.{gene}", design=experiment_design, quote=lines[gene],
                    citation=f"pseudobulk/{safe}.tsv, row {gene}",
                    identifier=f"pseudobulk/{safe}.tsv#{gene}",
                    identifier_type="local_artifact", source_card_id=card.id, subject=gene,
                    population=population, outcome=OUTCOME,
                    effect=f"log2 fold change {float(res.log2_fold_change[i]):+.2f}, "
                           f"FDR {float(res.p_adjusted[i]):.2g}",
                    sample_size=len(res.samples),
                    quality=EvidenceQuality(
                        directness=Directness.DIRECT,
                        rationale={"directness": "pseudobulk of the cells measured; holds "
                                   "for these samples and this cell-type assignment"},
                        assessed_by=SKILL_ID, assessment_tool="pseudobulk-deseq2"),
                    retrieved_by=SKILL_ID, retrieval_run=run_id)
                evidence.append(item.located_in(text))

    claims: list[CandidateClaim] = []
    kind = "association" if experiment_design == "observational" else "mechanism_hypothesis"
    contrast_used = None
    if pb is not None and pb.results:
        first = next(iter(pb.results.values()))
        contrast_used = first.contrast
    for ct, n_sig in sorted(tested.items()):
        if not n_sig:
            continue
        safe = "".join(ch if ch.isalnum() else "_" for ch in ct)
        items = [e for e in evidence if e.id.startswith(f"pb.{safe}.")]
        res = pb.results[ct]
        population = items[0].population
        factor, num, den = contrast_used
        claims.append(CandidateClaim(
            id=f"scrna.pseudobulk.{safe}",
            text=(f"In {ct} of these samples, {n_sig} genes differ in expression between "
                  f"{num} and {den} (pseudobulk DESeq2 Wald test, FDR < {res.alpha:g})"),
            claim_kind=kind, subject=f"{factor}={num}; cell type {ct}",
            predicate="differs_in_expression_from", object=f"{factor}={den}",
            supports=tuple(e.id for e in items), asserted_population=population,
            supported_population=population, asserted_outcome=OUTCOME,
            supported_outcome=OUTCOME, direction="mixed", hedged=True,
            magnitude=f"{n_sig} genes at FDR < {res.alpha:g}", confidence=0.5,
            confidence_basis=(f"pseudobulk over {len(res.samples)} samples, tested by the "
                              f"{res.backend} DESeq2 implementation; one experiment; the "
                              "cell-type assignment is itself inferred from markers"),
            falsified_by=("an independent replicate, or a sorted-population assay of these "
                          "genes, that does not show the same differences"),
            rationale="which genes respond within a cell type, as candidates for follow-up",
            produced_by=SKILL_ID))
    for claim in claims:
        require_declared(claim)

    summary, _ = json_file("scrna_summary.json", {
        "summary": run.summary(), "qc": run.qc, "doublets": run.doublets,
        "integration": run.integration, "paga_tree": run.paga.tree,
        "annotation": ({"labels": run.annotation.labels,
                        "confidence": run.annotation.confidence,
                        "scores": run.annotation.scores} if run.annotation else None),
        "pseudobulk_tested": tested,
        "pseudobulk_skipped": (pb.skipped if pb is not None else {}),
        "code_digest": manifest["code_digest"], "out_dir": str(out)},
        description="clusters, annotation, trajectory and the run's settings")
    outputs = [summary,
               file_of("clusters.tsv", (out / "clusters.tsv").read_text(encoding="utf-8"),
                       media_type="text/tab-separated-values",
                       description="clusters, cell types and top markers"),
               file_of("report.md", (out / "report.md").read_text(encoding="utf-8"),
                       media_type="text/markdown",
                       description="the report; figures and per-cell tables are in the "
                                   "run directory")]
    limitations = [
        "cell-type labels are inferred from marker expression against a panel, not "
        "measured; a cluster no type leads is left unassigned",
        "pseudotime orders cells by similarity from a chosen root and is not time",
        "clusters depend on the resolution and the neighbour graph; they are groups of "
        "similar cells in this data set",
        f"the experiment design ({experiment_design}) is declared by the caller and was "
        "not checked",
    ]
    if experiment_design != "observational":
        limitations.append("cells or animals: nothing here describes people")
    if not claims:
        limitations.append("no comparison between conditions found a difference, or none "
                           "was possible, so no claim is made")
    limitations += [f"warning from the run: {w}" for w in run.warnings]
    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question="which cell populations are present, and how do they differ between "
                 "conditions?",
        sources=(card,), evidence=evidence, claims=claims, outputs=outputs,
        limitations=limitations,
        assumptions=("the sample sheet's variables describe the samples correctly",
                     "the counts are raw UMI counts"),
        created_at=_now(),
        provenance={"pipeline": "bioagent.omics.scrna", "code_digest": manifest["code_digest"],
                    "run_dir": str(out), "experiment_design": experiment_design,
                    "analysis": {"backend": run.analysis.get("backend"),
                                 "versions": run.analysis.get("versions", {})},
                    "integration": {k: run.integration.get(k)
                                    for k in ("method", "implementation", "version")},
                    "de_backend": ({"name": pb.backend, "version": pb.version}
                                   if pb is not None else None)})
