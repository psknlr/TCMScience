"""Genetics snapshots: molecular QTLs (eQTL Catalogue) and mouse knockout phenotypes (IMPC).

Checked from this environment on 2026-10-01 (review of 2026-09-30).

**eQTL Catalogue** (``eqtl_catalogue``). The REST API is retired (HTTP 410), so the data
are files. By default the snapshot holds the catalogue itself: one row per dataset
(study, tissue or cell type with its UBERON/CL id, condition, sample size, quantification
method, PubMed id) for release 7 and the release-8 beta, and release 7's FTP paths of each
dataset's summary statistics, credible sets and Bayes factors. Those per-dataset files are
not fetched by default. Under ``https://ftp.ebi.ac.uk/pub/databases/spot/eQTL`` they
follow the patterns

    sumstats/{QTS}/{QTD}/{QTD}.all.tsv.gz              (ge, microarray: ~2.2 GB each)
    sumstats/{QTS}/{QTD}/{QTD}.cc.tsv.gz               (exon, tx, txrev, leafcutter)
    susie/{QTS}/{QTD}/{QTD}.credible_sets.tsv.gz       (1-2 MB each)
    r8_beta/susie/{QTS}/{QTD}/{QTD}.credible_sets.parquet

(``ftp_paths_r7`` lists every release-7 path). One credible-set file is declared as an
optional table, GTEx v8 liver gene expression (QTD000266, 1.3 MB); a file loaded into any
table named ``credible_sets*`` becomes ``variant_gene`` rows:

* the subject is the variant as ``grch38:<chrom>-<pos>-<ref>-<alt>``; its rsids (the file
  repeats a variant once per rsid) are in the context, not the id;
* the object is the Ensembl gene;
* evidence ``associated`` (a population statistic, not a mechanism), score = SuSiE PIP;
* effect ``increase`` / ``decrease`` is the sign of beta, which the eQTL Catalogue reports
  for the ALT allele ("the ALT allele is always the effect allele"); beta 0 has no effect;
* the context holds tissue, condition, dataset, study, sample size, quantification
  method, beta, se, P value, effect allele and genome build; the reference is the study's
  PubMed id; the note names the cohort (``via GTEx``), because the same samples underlie
  other resources' eQTLs (the GTEx portal, Open Targets).

The release-8 beta's merged credible sets (Zenodo, 0.23 to 0.78 GB per quantification
method) are listed as an optional raw file for the analysis layer.

**IMPC** (``impc``). Data release 24.0 (2026-03-16), pinned to ``release-24.0/results``
rather than the moving ``latest``. ``genotype-phenotype-assertions-ALL`` holds every
significant knockout phenotype call; each becomes a ``gene_phenotype`` row:

* subject ``mgi:<id>`` (mouse gene), object ``mp:<id>`` (Mammalian Phenotype term) or,
  for a histopathology call, ``mpath:<id>`` (Mouse Pathology term; the organ goes to the
  context's tissue). The one call that names no term is queued as unresolved;
* evidence ``known``: a phenotype measured on knockout mice and called by IMPC's
  statistical pipeline (or by an expert, for categorical observations);
* context: species (mouse), zygosity, sex, procedure, parameter, statistical method,
  P value, effect size, allele, colony, phenotyping centre and resource;
* ``Supplied as data`` marks categorical and pathology calls: their effect size of 1.0 and
  P value of exactly 0 or 1 (3i's anti-nuclear antibody calls carry 1.0) are placeholders,
  not measurements, and are not kept;
* rows from the legacy resources the file re-hosts (EuroPhenome, MGP, 3i) carry
  ``via <resource>``; the Pain Working Group's (``pwg``) ran at IMPC centres and do not;
* a sex of ``no data`` is unknown and is not kept; ``not_considered`` is kept.

The viability report is a table, not rows: a line called ``viable`` would read as a tested
negative for preweaning lethality, but 143 of the 6,284 lines DR 24.0 calls viable also
carry a preweaning-lethality call in the assertions, so that reading is not safe.

Calls are significant only: a phenotype a line was tested for and did not show is in
``statistical-results-ALL`` (0.56 GB, optional raw file, ``significant`` = false), not
here.
"""

from __future__ import annotations

import math
import re
import sqlite3
from typing import Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec

__all__ = ["DATASETS", "EXTRACTORS"]

_EQTL_GITHUB = ("https://raw.githubusercontent.com/eQTL-Catalogue/eQTL-Catalogue-resources/"
                "master")
_EQTL_FTP = "https://ftp.ebi.ac.uk/pub/databases/spot/eQTL"
_IMPC_FTP = "https://ftp.ebi.ac.uk/pub/databases/impc/all-data-releases/release-24.0/results"

EQTL_CATALOGUE = DatasetSpec(
    key="eqtl_catalogue", name="eQTL Catalogue (dataset catalogue and fine-mapped eQTLs)",
    catalog=(95,), homepage="https://www.ebi.ac.uk/eqtl/",
    license="CC BY 4.0 (data); code Apache-2.0",
    version="release 7 (2023) and release-8 beta (2026-09)",
    commercial_use="allowed",
    upstream=("GTEx", "BLUEPRINT", "GEUVADIS", "TwinsUK", "OneK1K", "INTERVAL",
              "Sun_2018"),
    relations=("variant_gene",),
    notes="Uniformly re-processed molecular QTLs (gene expression, exon, transcript, "
          "txrevise and Leafcutter splicing, microarray, one plasma pQTL study) from about "
          "42 public studies. The default files describe the 758 release-7 and 592 "
          "release-8-beta datasets; dataset ids are reused across releases for different "
          "data (QTD000266 is GTEx v8 liver in r7 and GTEx v10 liver in r8_beta), so the "
          "release is part of every row's provenance. variant_gene rows come from SuSiE "
          "credible-set files (optional): evidence 'associated', score = posterior "
          "inclusion probability, effect = sign of the ALT allele's beta. Cite Kerimov et "
          "al. 2021 Nat Genet 53:1290 and 2023 PLoS Genet 19:e1010932.",
    files=(
        FileSpec(f"{_EQTL_GITHUB}/data_tables/dataset_metadata_r7.tsv",
                 "dataset_metadata_r7.tsv", "datasets_r7",
                 note="release 7: study, dataset, tissue (UBERON/CL/EFO id), condition, "
                      "sample size, quantification method, PubMed id"),
        FileSpec(f"{_EQTL_GITHUB}/data_tables/dataset_metadata_r8_beta.tsv",
                 "dataset_metadata_r8_beta.tsv", "datasets_r8_beta",
                 note="release-8 beta (GTEx v10, INTERVAL RNA, MAGE, IBDverse, re-annotated "
                      "single-cell sets); the final release 8 is due in December 2026"),
        FileSpec(f"{_EQTL_GITHUB}/tabix/tabix_ftp_paths.tsv", "tabix_ftp_paths.tsv",
                 "ftp_paths_r7",
                 note="release 7: each dataset's summary-statistics, credible-set and "
                      "Bayes-factor paths (ftp:// URLs; the same paths serve over "
                      "https://ftp.ebi.ac.uk/)"),
        FileSpec(f"{_EQTL_FTP}/susie/QTS000015/QTD000266/QTD000266.credible_sets.tsv.gz",
                 "QTD000266.credible_sets.tsv.gz", "credible_sets_gtex_liver",
                 optional=True, expected_bytes=1280028,
                 note="release 7, GTEx v8 liver, gene expression: SuSiE credible sets "
                      "(one row per variant and rsid)"),
        FileSpec("https://zenodo.org/api/records/22846842/files/"
                 "eQTL_Catalogue_r8-beta_cs_ge_190926.parquet/content",
                 "eQTL_Catalogue_r8-beta_cs_ge_190926.parquet", "", fmt="raw",
                 optional=True, expected_bytes=778981518,
                 note="release-8 beta, every gene-expression dataset's credible sets merged "
                      "(19.9 million rows, doi:10.5281/zenodo.22846842); analysis layer, "
                      "not loaded"),
    ),
)

IMPC = DatasetSpec(
    key="impc", name="IMPC knockout mouse phenotype calls",
    catalog=(97,), homepage="https://www.mousephenotype.org/",
    license="CC BY 4.0", version="data release 24.0 (2026-03-16)",
    commercial_use="allowed", upstream=("EuroPhenome", "MGP", "3i"),
    relations=("gene_phenotype",),
    notes="Significant genotype-phenotype calls of knockout mouse lines (MGI gene -> MP "
          "or MPATH term) from the International Mouse Phenotyping Consortium, with the legacy "
          "EuroPhenome, Sanger MGP and 3i calls the same file re-hosts. Evidence 'known' "
          "(measured on mice). Only significant calls are listed; tested negatives are in "
          "the optional statistical-results file. Genes are mouse genes: a human mapping "
          "needs an orthology source.",
    files=(
        FileSpec(f"{_IMPC_FTP}/genotype-phenotype-assertions-ALL.csv.gz",
                 "genotype-phenotype-assertions-ALL.csv.gz", "genotype_phenotype", fmt="csv",
                 expected_bytes=5001223,
                 note="every significant call, all resources (IMPC, EuroPhenome, MGP, 3i)"),
        FileSpec(f"{_IMPC_FTP}/viability.csv.gz", "viability.csv.gz", "viability", fmt="csv",
                 expected_bytes=479554,
                 note="viability of each line and zygosity (viable / subviable / lethal) "
                      "with pup counts"),
        FileSpec(f"{_IMPC_FTP}/README.md", "README.md", "", fmt="raw",
                 note="what each report holds"),
        FileSpec(f"{_IMPC_FTP}/statistical-results-ALL.csv.gz",
                 "statistical-results-ALL.csv.gz", "", fmt="raw", optional=True,
                 expected_bytes=564423904,
                 note="every statistical test, significant or not (79 columns); analysis "
                      "layer, not loaded"),
    ),
)

DATASETS: tuple[DatasetSpec, ...] = (EQTL_CATALOGUE, IMPC)


# --------------------------------------------------------------------- eQTL Catalogue
_VARIANT = re.compile(r"^(?:chr)?([0-9]{1,2}|X|Y|MT?)_(\d+)_([A-Za-z*]+)_([A-Za-z*]+)$")
_DATASET_URL = re.compile(r"/(r8_beta/)?susie/(QTS\d+)/(QTD\d+)/")
_COHORT_SUFFIX = re.compile(r"(?:_v\d+|_reannotated|_RNA(?:_WGS)?)$")


def _float(value) -> float | None:
    text = v(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return None if math.isnan(number) else number


def _eqtl_catalogue(conn: sqlite3.Connection) -> Iterator[Row | None]:
    meta: dict[tuple[str, str], sqlite3.Row] = {}
    for table, release in (("datasets_r7", "r7"), ("datasets_r8_beta", "r8_beta")):
        if has(conn, table):
            for r in rows(conn, f"SELECT * FROM {table}"):
                meta[(release, r["dataset_id"])] = r
    sources = [(r["tbl"], r["url"]) for r in
               rows(conn, "SELECT tbl, url FROM _tcmdb_files ORDER BY tbl")
               if str(r["tbl"]).startswith("credible_sets")]
    for table, url in sources:
        m = _DATASET_URL.search(url or "")
        release = "r8_beta" if m and m.group(1) else "r7"
        dataset_id = m.group(3) if m else None
        ds = meta.get((release, dataset_id)) if dataset_id else None
        study = ds["study_label"] if ds else None
        cohort = _COHORT_SUFFIX.sub("", study) if study else None
        pmid = v(ds["pmid"]) if ds else None
        # The file repeats a variant once per rsid; every other column is the same, so
        # the rows are grouped and the rsids collected.
        grouped = rows(conn, f'SELECT molecular_trait_id, gene_id, cs_id, variant, cs_size, '
                             f"pip, pvalue, beta, se, cs_min_r2, group_concat(rsid, ' ') "
                             f'AS rsids FROM "{table}" GROUP BY molecular_trait_id, gene_id, '
                             f'cs_id, variant, cs_size, pip, pvalue, beta, se, cs_min_r2')
        for r in grouped:
            rsids = " | ".join(sorted({x for x in (r["rsids"] or "").split() if v(x)}))
            parsed = _VARIANT.match(v(r["variant"]) or "")
            gene = v(r["gene_id"])
            if not parsed:
                yield unresolved("variant_gene", "eqtl_catalogue", None, rsids or None,
                                 gene, f"variant {r['variant']!r} is not chr_pos_ref_alt",
                                 reference=f"pmid:{pmid}" if pmid else None)
                continue
            chrom, pos, ref, alt = parsed.groups()
            beta = _float(r["beta"])
            effect = None if not beta else ("increase" if beta > 0 else "decrease")
            trait = v(r["molecular_trait_id"])
            yield rel(
                "variant_gene", "eqtl_catalogue", f"grch38:{chrom}-{pos}-{ref}-{alt}",
                r["variant"], f"ensembl:{gene}" if gene else None, None, "associated",
                score=r["pip"], reference=f"pmid:{pmid}" if pmid else None, effect=effect,
                note=names(f"SuSiE credible set {r['cs_id']} of {r['cs_size']} variants "
                           f"(min r2 {r['cs_min_r2']})", f"eQTL Catalogue {release}",
                           f"via {cohort}" if cohort else None),
                context=ctx(genome_build="GRCh38", variant=rsids or None, effect_allele=alt,
                            beta=v(r["beta"]), se=v(r["se"]), pvalue=v(r["pvalue"]),
                            dataset=f"{dataset_id} ({release})" if dataset_id else None,
                            study=study, tissue=ds["tissue_label"] if ds else None,
                            condition=ds["condition_label"] if ds else None,
                            n=ds["sample_size"] if ds else None,
                            method=ds["quant_method"] if ds else None,
                            measure=trait if trait and trait != gene else None))


# ------------------------------------------------------------------------------- IMPC
_SUPPLIED = "supplied as data"
#: Resources whose calls the file re-hosts; ``pwg`` (the Pain Working Group) is a project
#: run at IMPC centres, so it is IMPC's own.
_LEGACY = frozenset({"EuroPhenome", "MGP", "3i"})
#: Ontologies of the phenotype column: MP terms, and MPATH terms for histopathology.
_TERM_PREFIXES = {"MP": "mp", "MPATH": "mpath"}
_ORGAN = re.compile(r"^(.+?)\s+-\s+MPATH\b")


def _term(curie) -> str | None:
    """``MP:0001297`` -> ``mp:0001297``, ``MPATH:134`` -> ``mpath:134``; else ``None``."""
    head, _, tail = (v(curie) or "").partition(":")
    prefix = _TERM_PREFIXES.get(head)
    return f"{prefix}:{tail}" if prefix and tail else None


def _mgi(curie) -> str | None:
    head, _, tail = (v(curie) or "").partition(":")
    return f"mgi:{tail}" if head == "MGI" and tail.isdigit() else None


def _impc(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "genotype_phenotype"):
        return
    for r in rows(conn, "SELECT * FROM genotype_phenotype"):
        gene = _mgi(r["marker_accession_id"])
        term = _term(r["mp_term_id"])
        resource = v(r["resource_name"])
        if gene and not term:
            # one EuroPhenome call (DR 24.0) names no phenotype term at all
            yield unresolved("gene_phenotype", "impc", gene, r["marker_symbol"],
                             names(r["mp_term_name"], r["parameter_name"]),
                             f"no MP or MPATH term ({r['mp_term_id']!r})")
            continue
        method = v(r["statistical_method"])
        supplied = (method or "").lower() == _SUPPLIED
        # A categorical or pathology call is "Supplied as data": its effect size of 1.0
        # and a P value of exactly 0 or 1 are placeholders (DR 24.0's 3i anti-nuclear
        # antibody calls are significant with P 1.0). A real P value (a viability test's)
        # stays.
        pvalue = v(r["p_value"])
        if supplied and _float(pvalue) in (0.0, 1.0):
            pvalue = None
        effect_size = None if supplied else v(r["effect_size"])
        change = v(r["percentage_change"])
        sex = v(r["sex"])
        organ = _ORGAN.match(v(r["parameter_name"]) or "")
        yield rel(
            "gene_phenotype", "impc", gene, r["marker_symbol"], term, r["mp_term_name"],
            "known",
            note=names(f"percentage change {change}" if change else None,
                       f"via {resource}" if resource in _LEGACY else None),
            context=ctx(species="ncbitaxon:10090", zygosity=r["zygosity"],
                        sex=None if sex == "no data" else sex,
                        tissue=organ.group(1) if organ else None,
                        assay=names(r["procedure_name"], r["procedure_stable_id"]),
                        measure=names(r["parameter_name"], r["parameter_stable_id"]),
                        method=method, pvalue=pvalue, value=effect_size,
                        model=names(r["allele_symbol"], r["strain_name"]),
                        sample=r["colony_id"], study=r["phenotyping_center"],
                        source_db=resource))


EXTRACTORS = {
    "eqtl_catalogue": _eqtl_catalogue,
    "impc": _impc,
}
