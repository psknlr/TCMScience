"""`rnaseq-differential-expression` — the RNA-seq pipeline as a governed skill.

Runs :func:`bioagent.omics.rnaseq.run_rnaseq` on a sample sheet and returns its result
as a research artifact:

* **Sources.** The user's reads and the reference are each a source card pinned by the
  digests of their files, so the artifact names exactly what was analysed.
* **Evidence.** Each gene that differs at the FDR level (the 50 strongest) is one
  evidence item whose quote is that gene's row of ``deseq2_results.tsv``, located in
  the table so the quote carries a receipt. The design is the experiment's, as the
  caller declares it: ``in_vitro``, ``animal`` or ``observational`` (human samples).
* **The claim.** One claim, and only when some gene differs. Its kind follows from the
  design: an observational study of human samples supports an ``association``; cells
  or animals support a ``mechanism_hypothesis`` and nothing about people. Its text
  states what was tested and found in these samples and nothing beyond.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...contracts import (CandidateClaim, Directness, EvidenceItem, EvidenceQuality,
                          SourceCard, require_declared)
from ...contracts.source_card import canonical_hash
from ..base import artifact, file_of, json_file

__all__ = ["rnaseq_differential_expression", "EXPERIMENT_DESIGNS"]

SKILL_ID = "rnaseq-differential-expression"
SKILL_VERSION = "0.1.0"
EXPERIMENT_DESIGNS = ("in_vitro", "animal", "observational")
MAX_EVIDENCE = 50
OUTCOME = "gene expression (DESeq2 Wald test)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rnaseq_differential_expression(sample_sheet: str, *, transcripts: str = "",
                                   annotation: str = "", genome: str = "",
                                   design: str = "~ condition", contrast: str = "",
                                   experiment_design: str = "in_vitro",
                                   engine: str = "auto", trimmer: str = "auto",
                                   de_backend: str = "builtin", alpha: float = 0.05,
                                   out_dir: str = "", run_id: str = "") -> Any:
    """Differential expression from FASTQ files, with the full report on disk.

    ``de_backend`` picks the DESeq2 implementation (``builtin`` or ``pydeseq2``); the
    one that ran, with its version, is in the provenance and the claim's basis.
    """
    from ...omics.rnaseq import RNASeqConfig, run_rnaseq

    if experiment_design not in EXPERIMENT_DESIGNS:
        raise ValueError(f"experiment_design is one of {', '.join(EXPERIMENT_DESIGNS)}: "
                         "in_vitro (cells), animal, or observational (human samples)")
    parsed = None
    if contrast:
        parts = [p.strip() for p in str(contrast).split(",")]
        if len(parts) != 3:
            raise ValueError("contrast is 'factor,numerator,denominator'")
        parsed = (parts[0], parts[1], parts[2])
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="bioagent-rnaseq-"))
    started = _now()
    run = run_rnaseq(sample_sheet, RNASeqConfig(
        transcripts=transcripts or None, annotation=annotation or None,
        genome=genome or None, design=design, contrast=parsed, engine=engine,
        trimmer=trimmer, de_backend=de_backend, alpha=float(alpha)), out)
    manifest = run.manifest
    factor, num, den = run.contrast
    res = run.result

    # ---- sources: the reads, and the reference -----------------------------
    reads = {p: d for p, d in manifest["inputs"].items()
             if p not in {str(x) for x in (transcripts, annotation, genome) if x}}
    sheet_digest = hashlib.sha256(Path(sample_sheet).read_bytes()).hexdigest()
    data_card = SourceCard(
        id=f"user.rnaseq.{sheet_digest[:12]}", name=f"RNA-seq reads ({len(run.samples)} "
        "samples)", kind="dataset", maintainer="the user", access_method="local_file",
        license_spdx="LicenseRef-user-supplied",
        license_note="the user's own data; its reuse terms are the user's",
        integration_mode="native", offline_capable=True,
        snapshot_hash=canonical_hash({"sheet": sheet_digest, "files": reads}),
        snapshot_at=started,
        known_limits=(f"{len(run.samples)} samples; design declared by the caller as "
                      f"{experiment_design}",))
    ref_files = {p: d for p, d in manifest["inputs"].items() if p not in reads}
    ref_card = SourceCard(
        id=f"user.reference.{canonical_hash(ref_files)[:12]}", name="reference sequences "
        "and annotation", kind="dataset", maintainer="the user", access_method="local_file",
        license_spdx="LicenseRef-user-supplied",
        license_note="supplied by the user; Ensembl and GENCODE references are open",
        integration_mode="native", offline_capable=True,
        snapshot_hash=canonical_hash(ref_files), snapshot_at=started)

    # ---- evidence: one item per differing gene -------------------------------
    table_text = (out / "deseq2_results.tsv").read_text(encoding="utf-8")
    lines = {line.split("\t", 1)[0]: line for line in table_text.splitlines()[1:]}
    significant = sorted(res.significant(), key=lambda i: (res.p_adjusted[i], res.genes[i]))
    names = dict(zip(run.genes.genes, run.genes.names))
    levels: dict[str, int] = {}
    for s in run.samples:
        levels[s.attributes[factor]] = levels.get(s.attributes[factor], 0) + 1
    population = (f"{len(run.samples)} {_population(experiment_design)} samples "
                  f"({', '.join(f'{v} {k}' for k, v in sorted(levels.items()))})")
    evidence: list[EvidenceItem] = []
    for i in significant[:MAX_EVIDENCE]:
        gene = res.genes[i]
        lfc, padj = float(res.log2_fold_change[i]), float(res.p_adjusted[i])
        item = EvidenceItem(
            id=f"de.{gene}", design=experiment_design, quote=lines[gene],
            citation=f"deseq2_results.tsv, row {gene} ({names.get(gene, gene)})",
            identifier=f"deseq2_results.tsv#{gene}", identifier_type="local_artifact",
            source_card_id=data_card.id, subject=gene, population=population,
            condition=f"{factor}: {num}", comparator=den,
            outcome=OUTCOME,
            effect=f"log2 fold change {lfc:+.2f}, FDR {padj:.2g}",
            sample_size=len(run.samples),
            quality=EvidenceQuality(
                directness=Directness.DIRECT,
                rationale={"directness": "the samples were measured directly; the result "
                           "holds for these samples and this reference"},
                assessed_by=SKILL_ID, assessment_tool="deseq2-wald"),
            retrieved_by=SKILL_ID, retrieval_run=run_id, notes=_direction(lfc))
        evidence.append(item.located_in(table_text))

    # ---- outputs --------------------------------------------------------------
    summary = run.summary()
    out_summary, _ = json_file("rnaseq_summary.json", {
        "summary": summary, "steps": manifest["steps"], "tools": manifest["tools"],
        "code_digest": manifest["code_digest"], "out_dir": str(out),
        "significant": [{"gene": res.genes[i], "name": names.get(res.genes[i]),
                         "log2_fold_change": float(res.log2_fold_change[i]),
                         "p_adjusted": float(res.p_adjusted[i])} for i in significant]},
        description="the run's summary, steps, tool versions and significant genes")
    out_table = file_of("deseq2_results.tsv", table_text, media_type="text/tab-separated-values",
                        description="DESeq2-method results for every gene")
    out_report = file_of("report.md", (out / "report.md").read_text(encoding="utf-8"),
                         media_type="text/markdown",
                         description="the report; figures and the HTML page are in "
                                     "the run directory")

    # ---- the claim --------------------------------------------------------------
    up = sum(1 for i in significant if res.log2_fold_change[i] > 0)
    down = len(significant) - up
    kind = "association" if experiment_design == "observational" else "mechanism_hypothesis"
    claims: list[CandidateClaim] = []
    if evidence:
        tested = int(sum(1 for v in res.p_value if math.isfinite(v)))
        claims.append(CandidateClaim(
            id="rnaseq.differential_expression",
            text=(f"In these {len(run.samples)} samples, {len(significant)} of {tested} "
                  f"tested genes differ in expression between {num} and {den} ({up} higher "
                  f"and {down} lower in {num}; DESeq2 Wald test, FDR < {res.alpha:g})"),
            claim_kind=kind, subject=f"{factor}={num}", predicate="differs_in_expression_from",
            object=f"{factor}={den}", supports=tuple(e.id for e in evidence),
            asserted_population=population, supported_population=population,
            asserted_outcome=OUTCOME, supported_outcome=OUTCOME,
            direction="mixed" if up and down else ("increase" if up else "decrease"),
            magnitude=f"{len(significant)} genes at FDR < {res.alpha:g}",
            confidence=0.5,
            confidence_basis=("a negative binomial GLM with shrunken dispersions on "
                              f"{len(run.samples)} libraries (the DESeq2 method, "
                              f"{res.backend} implementation); one experiment, not "
                              "replicated independently"),
            hedged=True,
            falsified_by=("an independent replicate experiment, or qPCR of the listed "
                          "genes, that does not show the same differences"),
            rationale=("which genes respond, as candidates for follow-up; a single "
                       "experiment's differential expression, not an explanation"),
            produced_by=SKILL_ID))
    for claim in claims:
        require_declared(claim)

    limitations = [
        "a statistical association within one experiment: it holds for these samples, this "
        "reference and these settings, and does not establish a mechanism or a cause",
        "fold changes are maximum-likelihood estimates without shrinkage; low-count genes "
        "can show large, imprecise changes",
        f"the experiment design ({experiment_design}) is declared by the caller and was not "
        "checked",
    ]
    if experiment_design != "observational":
        limitations.append("cells or animals: nothing here describes people")
    if not evidence:
        limitations.append("no gene differs at the FDR level, so no claim is made; this is "
                           "not evidence that expression is unchanged")
    if len(significant) > MAX_EVIDENCE:
        limitations.append(f"the claim cites the {MAX_EVIDENCE} strongest of "
                           f"{len(significant)} genes; the table lists all")
    limitations += [f"warning from the run: {w}" for w in run.warnings]

    return artifact(
        id=SKILL_ID, run_id=run_id, skill_id=SKILL_ID, skill_version=SKILL_VERSION,
        question=f"which genes differ in expression between {num} and {den}?",
        sources=(data_card, ref_card), evidence=evidence, claims=claims,
        outputs=(out_summary, out_table, out_report), limitations=limitations,
        assumptions=("the sample sheet's design columns describe the samples correctly",
                     "the reference matches the organism sequenced"),
        created_at=_now(),
        provenance={"pipeline": "bioagent.omics.rnaseq", "engine": run.engine,
                    "trimmer": run.trimmer, "contrast": list(run.contrast),
                    "de_backend": {"name": res.backend, "version": res.version},
                    "code_digest": manifest["code_digest"], "run_dir": str(out),
                    "tools": manifest["tools"], "experiment_design": experiment_design})


def _population(design: str) -> str:
    return {"in_vitro": "cell culture", "animal": "animal",
            "observational": "human"}[design]


def _direction(lfc: float) -> str:
    return "higher in the numerator group" if lfc > 0 else "lower in the numerator group"
