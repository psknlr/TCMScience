"""Case 1: counts → differential expression → preranked GSEA → which claims the result carries.

The experiment is simulated, so what is true is known: four control and four treated
samples in two batches, negative-binomial counts, and two planted gene sets, one raised
four-fold by the treatment and one lowered four-fold, among six sets of genes it does not
touch. The case runs:

1. differential expression through ``omics.de_backends.run_de`` with the backend asked for
   (``builtin`` or ``pydeseq2``; one that is not installed is UNAVAILABLE, never swapped);
2. a ranking by the Wald statistic, and preranked GSEA with GSEApy on a GMT snapshot;
3. two evidence items, located in the run's own result tables: a gene's fold change (a
   cell experiment, ``in_vitro``) and the planted set's enrichment (``pathway_enrichment``,
   a computational prediction);
4. drafted claims through the claim contract.

A gene's change in these cells may be stated as a mechanism finding. The enrichment of a
pathway may be stated as a hypothesis only. Neither says anything about patients, or about
the concentrations patients reach.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..status import ExecutionStatus
from .report import CaseReport, CaseStep, check_drafts

__all__ = ["Experiment", "run_case", "simulated_experiment"]

SPECIES, ID_TYPE = "simulated", "simulated_gene_id"


@dataclass(frozen=True)
class Experiment:
    counts: np.ndarray                      # genes x samples
    genes: tuple[str, ...]
    samples: tuple[str, ...]
    metadata: dict[str, dict[str, str]]
    sets: dict[str, tuple[str, ...]]
    up: tuple[str, ...]
    down: tuple[str, ...]


def simulated_experiment(*, n_genes: int = 600, set_size: int = 40,
                         seed: int = 7) -> Experiment:
    """Counts with a known answer: ``PLANTED_UP`` four-fold up, ``PLANTED_DOWN`` down."""
    rng = np.random.default_rng(seed)
    genes = tuple(f"SIM{i:04d}" for i in range(n_genes))
    samples = tuple(f"{c}{n}" for c in ("ctrl", "trt") for n in range(1, 5))
    metadata = {s: {"condition": "control" if s.startswith("ctrl") else "treated",
                    "batch": "b1" if s[-1] in "12" else "b2"} for s in samples}
    mean = rng.lognormal(mean=5.0, sigma=1.0, size=n_genes)
    up = genes[:set_size]
    down = genes[set_size:2 * set_size]
    fold = np.ones(n_genes)
    fold[:set_size], fold[set_size:2 * set_size] = 4.0, 0.25
    dispersion = 0.08
    counts = np.empty((n_genes, len(samples)), dtype=np.int64)
    for j, s in enumerate(samples):
        mu = mean * (fold if metadata[s]["condition"] == "treated" else 1.0) \
            * (1.3 if metadata[s]["batch"] == "b2" else 1.0) * rng.uniform(0.8, 1.2)
        lam = rng.gamma(shape=1.0 / dispersion, scale=mu * dispersion)
        counts[:, j] = rng.poisson(lam)
    rest = list(genes[2 * set_size:])
    rng.shuffle(rest)
    sets = {"PLANTED_UP": up, "PLANTED_DOWN": down}
    for k in range(6):
        sets[f"UNTOUCHED_{k + 1}"] = tuple(rest[k * set_size:(k + 1) * set_size])
    return Experiment(counts, genes, samples, metadata, sets, up, down)


def _write_gmt(path: Path, sets: dict[str, tuple[str, ...]]) -> None:
    path.write_text("".join(f"{name}\tsimulated for this case\t" + "\t".join(genes) + "\n"
                            for name, genes in sets.items()), encoding="utf-8")


def run_case(work_dir: str | Path, *, de_backend: str = "builtin",
             experiment: Experiment | None = None, permutations: int = 1000,
             seed: int = 0) -> CaseReport:
    from ..contracts import CandidateClaim, EvidenceItem
    from ..omics.de_backends import run_de
    from ..omics.gsea import ranked_from_de, read_gmt, run_prerank
    from ..omics.optional import BackendUnavailable

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    exp = experiment or simulated_experiment()
    report = CaseReport(
        case="rnaseq-gsea", title="RNA-seq counts to pathway claims: differential "
        "expression, preranked GSEA, the claim contract",
        inputs=f"a simulated experiment ({len(exp.genes)} genes, 4 control and 4 treated "
               "samples in two batches; PLANTED_UP raised and PLANTED_DOWN lowered "
               "four-fold); gene sets written for the case")

    try:
        de = run_de(exp.counts, exp.genes, exp.samples, exp.metadata,
                    design="~ batch + condition", contrast=("condition", "treated", "control"),
                    backend=de_backend)
    except BackendUnavailable as exc:
        report.steps.append(CaseStep("differential expression", ExecutionStatus.UNAVAILABLE,
                                     de_backend, str(exc)[:300]))
        report.limits.append(f"the {de_backend} backend is not installed here, so nothing "
                             "past differential expression ran; no other backend was used "
                             "in its place")
        return report
    called = set(de.significant())
    up_hits = sum(1 for g in exp.up if exp.genes.index(g) in called
                  and de.log2_fold_change[exp.genes.index(g)] > 0)
    down_hits = sum(1 for g in exp.down if exp.genes.index(g) in called
                    and de.log2_fold_change[exp.genes.index(g)] < 0)
    report.steps.append(CaseStep(
        "differential expression", ExecutionStatus.SUCCEEDED, f"{de.backend} {de.version}",
        f"{len(called)} genes at FDR < {de.alpha}; {up_hits}/{len(exp.up)} planted up and "
        f"{down_hits}/{len(exp.down)} planted down called with their sign",
        {**de.record(), "summary": de.summary()}))
    table = "gene\tlog2_fold_change\tp_adjusted\n" + "".join(
        f"{row['gene']}\t{row['log2_fold_change']:.3f}\t{row['p_adjusted']:.3g}\n"
        for row in de.table() if row["log2_fold_change"] is not None
        and row["p_adjusted"] is not None)
    (work / "de_results.tsv").write_text(table, encoding="utf-8")

    ranked = ranked_from_de(de, method="stat", species=SPECIES, id_type=ID_TYPE)
    _write_gmt(work / "sets.gmt", exp.sets)
    library = read_gmt(work / "sets.gmt", source="written for this case",
                       version=hashlib.sha256(repr(sorted(exp.sets)).encode()).hexdigest()[:12],
                       licence="CC0-1.0", species=SPECIES, id_type=ID_TYPE)
    try:
        gsea = run_prerank(ranked, library, min_size=15, max_size=500,
                           permutations=permutations, seed=seed)
    except BackendUnavailable as exc:
        report.steps.append(CaseStep("preranked GSEA", ExecutionStatus.UNAVAILABLE, "gseapy",
                                     str(exc)[:300]))
        report.limits.append("GSEApy is not installed here, so no pathway was tested")
        return report
    top = {r["term"]: r for r in gsea.rows}
    planted = top["PLANTED_UP"]
    report.steps.append(CaseStep(
        "preranked GSEA", ExecutionStatus.SUCCEEDED,
        f"{gsea.backend['name']} {gsea.backend['version']}",
        f"PLANTED_UP NES {planted['nes']:.2f} (FDR {planted['fdr']:.3g}); PLANTED_DOWN NES "
        f"{top['PLANTED_DOWN']['nes']:.2f}; untouched sets significant at FDR < 0.25: "
        f"{sum(1 for r in gsea.significant() if r['term'].startswith('UNTOUCHED'))}",
        {"ranking": ranked.record(), "library": library.record(),
         "parameters": gsea.parameters}))
    gsea_table = "term\tnes\tp_value\tfdr\tmatched\n" + "".join(
        f"{r['term']}\t{r['nes']:.3f}\t{r['p_value']:.3g}\t{r['fdr']:.3g}\t{r['matched']}\n"
        for r in gsea.rows)
    (work / "gsea_results.tsv").write_text(gsea_table, encoding="utf-8")

    gene = exp.up[0]
    gene_row = next(line for line in table.splitlines() if line.startswith(gene + "\t"))
    set_row = next(line for line in gsea_table.splitlines() if line.startswith("PLANTED_UP"))

    def item(eid: str, design: str, content: str, quote: str, outcome: str) -> EvidenceItem:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return EvidenceItem(
            id=eid, design=design, quote=quote, source_card_id="case.rnaseq-gsea",
            identifier=f"sha256:{digest}", identifier_type="local_artifact",
            subject="treatment T", population="simulated cultured cells", outcome=outcome,
            retracted="not_retracted").located_in(content)

    evidence = {
        "de": item("de", "in_vitro", table, gene_row, f"expression of {gene}"),
        "gsea": item("gsea", "pathway_enrichment", gsea_table, set_row,
                     "the PLANTED_UP gene set"),
    }
    report.steps.append(CaseStep(
        "evidence items", ExecutionStatus.SUCCEEDED, "bioagent.contracts",
        "a gene's fold change (in_vitro) and the set's enrichment (pathway_enrichment), "
        "each a quote located in the run's own result table",
        {k: {"design": v.design, "identifier": v.identifier} for k, v in evidence.items()}))

    def claim(text: str, kind: str, supports: str, outcome: str) -> CandidateClaim:
        return CandidateClaim(
            id=f"c.{supports}.{kind}.{len(text)}", text=text, claim_kind=kind,
            subject="treatment T", supports=(supports,),
            asserted_population="simulated cultured cells",
            supported_population="simulated cultured cells", asserted_outcome=outcome,
            supported_outcome=outcome, confidence=0.5,
            confidence_basis="one simulated experiment, 4 vs 4",
            falsified_by="a replicate experiment with no change")

    pathway = "the PLANTED_UP gene set"
    report.claims = check_drafts([
        (claim(f"In these cells, treatment T raised the expression of {gene}.", "mechanism",
               "de", f"expression of {gene}"),
         "a gene-level finding the cell experiment measured", True),
        (claim("Treatment T may act through the PLANTED_UP pathway: its genes rank high "
               "among those raised in treated cells, a hypothesis for testing.",
               "mechanism_hypothesis", "gsea", pathway),
         "the enrichment, stated as the hypothesis it is", True),
        (claim("Treatment T activates the PLANTED_UP pathway.", "mechanism", "gsea", pathway),
         "the enrichment stated as an established mechanism", False),
        (claim("Treatment T may act through the PLANTED_UP pathway at concentrations "
               "reached in patients.", "mechanism_hypothesis", "gsea", pathway),
         "a cell result placed at human exposure", False),
        (claim("Treatment T improves outcomes in patients.", "efficacy", "gsea", pathway),
         "a pathway enrichment cited for patients", False),
    ], evidence)
    report.limits += [
        "The experiment is simulated; the case checks the chain and the contract, not a "
        "biological finding.",
        "Only one design (batch + condition) and one ranking (the Wald statistic) are run.",
        "The gene sets were written for the case; a real run pins a published library and "
        "checks its licence.",
    ]
    return report
