"""Pharmacogenomics and GPCR drug targets (supplementary sources, review of 2026-09-30;
every URL checked from the harness on 2026-10-01).

* **PharmVar** (catalogue 110): the all-genes download, a zip of per-gene folders. Its
  haplotype tables define each star allele by its variants on RefSeqGene, GRCh37 and
  GRCh38. They become ``allele_variant`` rows (GRCh38). The zip also holds per-allele VCFs
  and FASTA sequences (most of its ~190 MB), which the reader skips.
* **CPIC** (111): the gene-drug pair report, one row per pair with the CPIC level, the
  ClinPGx level of evidence and the PGx level of the FDA label. It becomes
  ``drug_pharmacogene`` rows.
* **ClinPGx** (111, formerly PharmGKB): four of its bulk zips. ``relationships.tsv`` gives
  ClinPGx's own gene-drug and variant-drug associations (associated, not associated,
  ambiguous); ``summary_annotations.tsv`` its literature-scored variant-drug annotations
  with a level of evidence; ``drugLabels.tsv`` the drug-label annotations of FDA, EMA,
  PMDA, Health Canada and Swissmedic; ``chemicals.tsv`` maps drug names and PA ids to
  RxNorm and PubChem.
* **GPCRdb** (112): the drug-target table of its web services (``drugs/download``):
  approved and trial drugs on human GPCRs with InChIKey, UniProt accession and the
  mechanism. It becomes ``drug_target`` rows.

KLIFS (113) and ProteomicsDB (114) publish no bulk files; they are live connectors only
(``providers.supplement.pgx_proteins``), as are the CPIC API and GPCRdb's per-receptor
services.

Ids. Drugs are ``rxnorm:`` when the source gives exactly one RxNorm id (CPIC always, and
ClinPGx when its chemical record has one), else ``pubchem:``, else the source's own id.
Genes are ``symbol:`` (HGNC); GPCRs ``uniprot:``. A PharmVar single-nucleotide variant is
``grch38:<chrom>-<pos>-<ref>-<alt>``, the form the genetics datasets use (eQTL Catalogue),
with its rsID in the context; a PharmVar insertion or deletion keeps PharmVar's own
notation (``pharmvar:<NC_ accession>:<pos>:<ref>><alt>``), because its tables give no
anchor base for the VCF form. ClinPGx names variants only by rsID, so they are
``dbsnp:rs…``; PharmVar's ``crosswalk["variant"]`` maps each rsID it defines to the
GRCh38 id. ClinPGx's ``crosswalk["compound"]`` maps its RxNorm drugs (and so CPIC's) to
their PubChem CID. A named allele
(``CYP2D6*4``, ``HLA-B*57:01``) is ``allele:<name>``: the star-allele and HLA names are
the nomenclature PharmVar, IPD-IMGT/HLA and the gene consortia assign, and CPIC and
ClinPGx use the same names, so one allele meets itself across sources. Haplotypes ClinPGx
names otherwise (``GSTM1 null``) keep ClinPGx's PA id.
"""

from __future__ import annotations

import csv
import html
import io
import re
import zipfile
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, col, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

csv.field_size_limit(1 << 30)

#: Relation kinds no registered kind fits.
KINDS = {
    # a gene whose variation changes a drug's exposure, response or toxicity: a
    # pharmacogenomic pair (CPIC, ClinPGx). Not a target: CYP2C19 metabolises
    # clopidogrel, it is not what clopidogrel acts on.
    "drug_pharmacogene": ("drug", "gene"),
    # a variant's or haplotype's association with a drug response (ClinPGx)
    "variant_drug": ("variant", "drug"),
    # a star allele and a variant that defines it (PharmVar)
    "allele_variant": ("allele", "variant"),
}


def _clean(values) -> list[str | None]:
    out: list[str | None] = []
    for value in values:
        text = "" if value is None else str(value).strip().strip("\r")
        out.append(text or None)
    return out


def _zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise StoreError(f"{path.name}: not a zip archive (an error page saved in its "
                         "place?)") from exc


# ----------------------------------------------------------------------------- readers
_PV_COLUMNS = ("Haplotype Name", "Gene", "rsID", "ReferenceSequence", "Variant Start",
               "Variant Stop", "Reference Allele", "Variant Allele", "Type")
_PV_VERSION = re.compile(r"#\s*version\s*=\s*(\S+)")


def _pharmvar_haplotypes(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """Every ``<GENE>.<refseq>.haplotypes.tsv`` of a PharmVar zip, as one table.

    The folder a table sits in (RefSeqGene, GRCh37, GRCh38, or M33388 for CYP2D6's
    legacy reference) becomes ``Reference Set``, and the ``#version=`` line that opens
    each table becomes ``PharmVar Version``. Members are read in name order, so a rebuild
    gives the same rows in the same order. VCF and FASTA members are skipped.
    """
    archive = _zip(path)
    members = sorted(n for n in archive.namelist() if n.endswith(".haplotypes.tsv"))
    if not members:
        raise StoreError(f"{path.name}: no *.haplotypes.tsv member")
    yield ["Reference Set", *_PV_COLUMNS, "PharmVar Version"]
    for name in members:
        parts = name.split("/")
        refset = parts[-2] if len(parts) >= 2 else None
        version = None
        header: list[str] | None = None
        with archive.open(name) as raw:
            for line in io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace",
                                         newline=""):
                line = line.rstrip("\r\n")
                if line.startswith("#"):
                    m = _PV_VERSION.match(line)
                    version = m.group(1) if m else version
                    continue
                if not line.strip():
                    continue
                cells = line.split("\t")
                if header is None:
                    header = [c.strip() for c in cells]
                    continue
                record = dict(zip(header, cells))
                yield _clean([refset, *(record.get(c) for c in _PV_COLUMNS), version])


def _zip_member_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One tab-separated member of a ClinPGx zip, named after ``clinpgx:`` in the format.

    ClinPGx quotes fields the CSV way (a field holding quotes is wrapped in quotes, its
    own quotes doubled), so the built-in ``tsv`` reader, which keeps quotes, does not
    fit; and its zips open with LICENSE.txt, so the loader's first-member rule does not
    either.
    """
    member = spec.fmt.split(":", 1)[1]
    archive = _zip(path)
    found = [n for n in archive.namelist() if n.rsplit("/", 1)[-1] == member]
    if not found:
        raise StoreError(f"{path.name}: no member {member!r}; have {archive.namelist()}")
    with archive.open(found[0]) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        for row in csv.reader(text, delimiter="\t", quotechar='"'):
            if row and any(c.strip() for c in row):
                yield _clean(row)


_CLINPGX_MEMBERS = ("relationships.tsv", "summary_annotations.tsv", "chemicals.tsv",
                    "drugLabels.tsv")
READERS = {"pharmvar_haplotypes": _pharmvar_haplotypes,
           **{f"clinpgx:{m}": _zip_member_tsv for m in _CLINPGX_MEMBERS}}


# --------------------------------------------------------------------------- id helpers
_RSID = re.compile(r"rs\d+")
_ALLELE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\*\S+")


def _variant_id(name: str | None) -> str | None:
    """``dbsnp:`` for an rsID, ``allele:`` for a star or HLA allele name, else None."""
    text = v(name)
    if not text:
        return None
    if _RSID.fullmatch(text):
        return f"dbsnp:{text}"
    if _ALLELE.fullmatch(text):
        return f"allele:{text}"
    return None


def _split(text: str | None, sep: str = ";") -> list[str]:
    return [p.strip() for p in (v(text) or "").split(sep) if p.strip()]


def _pmids(text: str | None) -> str | None:
    ids = [p for p in _split(text) if p.isdigit()]
    return "; ".join(f"pmid:{p}" for p in ids) or None


# ----------------------------------------------------------------------------- PharmVar
_PV_LICENSE = ("CC BY-SA 4.0 under the PharmVar Terms and Conditions (last modified "
               "2023-03-24): research use only; the data may not be offered for sale as a "
               "commercial item")


#: RefSeq accession of a GRCh38 chromosome -> chromosome name (NC_000001 .. NC_000024)
_NC_CHROM = re.compile(r"NC_0000(\d\d)\.\d+")
_BASES = re.compile(r"[ACGT]+")


def _chrom(accession: str | None) -> str | None:
    m = _NC_CHROM.fullmatch(accession or "")
    if not m:
        return None
    n = int(m.group(1))
    return {23: "X", 24: "Y"}.get(n, str(n) if 1 <= n <= 22 else None)


def _grch38(acc: str | None, start: str | None, stop: str | None, ref: str | None,
            alt: str | None) -> str | None:
    """The shared GRCh38 id of a PharmVar substitution, else None (indels lack an anchor)."""
    chrom = _chrom(acc)
    if (chrom and start and start.isdigit() and (not stop or stop == start)
            and ref and alt and len(ref) == len(alt) == 1
            and _BASES.fullmatch(ref) and _BASES.fullmatch(alt)):
        return f"grch38:{chrom}-{start}-{ref}-{alt}"
    return None


def _pharmvar(conn) -> Iterator[Row | None]:
    if not has(conn, "haplotypes"):
        return
    for r in rows(conn, "SELECT * FROM haplotypes WHERE Reference_Set = 'GRCh38'"):
        name, acc = v(r["Haplotype_Name"]), v(r["ReferenceSequence"])
        if not name or not acc or acc.upper() == "REFERENCE":
            continue                                # the reference allele defines nothing
        allele = _variant_id(name) if "*" in name else None
        allele = allele or f"pharmvar:{name}"       # DPYD and SLCO1B1 name some by rsID
        rsid = v(r["rsID"])
        start, stop = v(r["Variant_Start"]), v(r["Variant_Stop"])
        ref, alt = v(r["Reference_Allele"]), v(r["Variant_Allele"])
        pos = start if start == stop or not stop else f"{start}-{stop}"
        change = f"{acc}:{pos}:{ref or '-'}>{alt or '-'}"
        rsid = rsid if rsid and _RSID.fullmatch(rsid) else None
        variant = _grch38(acc, start, stop, ref, alt) or f"pharmvar:{change}"
        yield rel("allele_variant", "pharmvar", allele, name, variant, rsid or change,
                  "listed", note=v(r["Type"]),
                  context=ctx(variant=names(rsid, change), genome_build="GRCh38"))


# --------------------------------------------------------------------------------- CPIC
_CPIC_LICENSE = "CC0 1.0 (CPIC curated content; CPIC asks for attribution)"
#: CPIC levels whose pairs carry prescribing guidance; C and D pairs are listed with weak
#: or unclear evidence and no recommended action, so they are not labelled "known".
_CPIC_ACTIONABLE = frozenset({"A", "A/B", "B"})


def _cpic(conn) -> Iterator[Row | None]:
    if not has(conn, "pair"):
        return
    for r in rows(conn, "SELECT * FROM pair"):
        level = v(col(r, "CPIC_Level"))
        gene, drug = v(col(r, "Gene")), v(col(r, "Drug"))
        if not gene or not drug or not level or level.lower() == "retired":
            continue                                # a retired pair is no longer a claim
        rx = v(col(r, "Drug_RxNorm_ID"))
        rx = rx.split(".")[0] if rx and re.fullmatch(r"\d+(\.0)?", rx) else None
        subject = f"rxnorm:{rx}" if rx else f"cpic:{drug}"
        clinpgx, label = v(col(r, "ClinPGx_Level_of_Evidence")), v(col(r, "PGx_on_FDA_Label"))
        yield rel("drug_pharmacogene", "cpic", subject, drug, f"symbol:{gene}", gene,
                  "known" if level in _CPIC_ACTIONABLE else "listed",
                  reference=_pmids(col(r, "CPIC_Publications_PMID")),
                  note=names(f"ClinPGx level {clinpgx}" if clinpgx else None,
                             f"FDA label: {label}" if label else None),
                  context=ctx(confidence=f"CPIC level {level}",
                              flags=v(col(r, "CPIC_Level_Status")),
                              source_id=v(col(r, "Guideline"))))


# ------------------------------------------------------------------------------ ClinPGx
_CLINPGX_LICENSE = ("CC BY-SA 4.0 under the ClinPGx Data Usage Policy: research use only; "
                    "ClinPGx data may not be sold for private or commercial use")
_ASSOCIATION = {"associated": "positive", "not associated": "negative",
                "ambiguous": "inconclusive"}


def _ids(text: str | None) -> list[str]:
    return [p for p in re.split(r"[,;\s]+", v(text) or "") if p.isdigit()]


def _chemicals(conn) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """ClinPGx chemicals by PA id and by lower-case name -> (id, name)."""
    by_pa: dict[str, tuple[str, str]] = {}
    by_name: dict[str, tuple[str, str]] = {}
    if not has(conn, "chemical"):
        return by_pa, by_name
    for r in rows(conn, "SELECT * FROM chemical"):
        pa, name = v(col(r, "PharmGKB_Accession_Id")), v(col(r, "Name"))
        if not pa:
            continue
        rx, pc = _ids(col(r, "RxNorm_Identifiers")), _ids(col(r, "PubChem_Compound_Identifiers"))
        cid = (f"rxnorm:{rx[0]}" if len(rx) == 1 else
               f"pubchem:{pc[0]}" if len(pc) == 1 else f"clinpgx:{pa}")
        by_pa[pa] = (cid, name or pa)
        if name:
            by_name.setdefault(name.casefold(), (cid, name))
    return by_pa, by_name


def _clinpgx(conn) -> Iterator[Row | None]:
    by_pa, by_name = _chemicals(conn)

    def drug(pa: str | None, name: str | None) -> tuple[str, str] | None:
        if pa and pa in by_pa:
            return by_pa[pa]
        if name and name.casefold() in by_name:
            return by_name[name.casefold()]
        return (f"clinpgx:{pa}", name or pa) if pa else None

    if has(conn, "relationships"):
        # each pair is listed in both directions; the gene, variant or haplotype side
        # first is read once
        for r in rows(conn, "SELECT * FROM relationships WHERE Entity2_type = 'Chemical' "
                            "AND Entity1_type IN ('Gene', 'Variant', 'Haplotype')"):
            outcome = _ASSOCIATION.get((v(r["Association"]) or "").lower())
            d = drug(v(r["Entity2_id"]), v(r["Entity2_name"]))
            if outcome is None or d is None:
                continue
            kind_of, eid, ename = r["Entity1_type"], v(r["Entity1_id"]), v(r["Entity1_name"])
            common = dict(reference=_pmids(r["PMIDs"]),
                          note=f"ClinPGx evidence: {v(r['Evidence'])}" if v(r["Evidence"])
                          else None,
                          outcome=outcome, context=ctx(mechanism=names(r["PK"], r["PD"])))
            if kind_of == "Gene":
                yield rel("drug_pharmacogene", "clinpgx", d[0], d[1],
                          f"symbol:{ename}" if ename else None, ename, "aggregated",
                          **common)
            else:
                vid = _variant_id(ename) or (f"clinpgx:{eid}" if eid else None)
                yield rel("variant_drug", "clinpgx", vid, ename, d[0], d[1], "aggregated",
                          subject_type="allele" if kind_of == "Haplotype" else None,
                          **common)

    if has(conn, "summary_annotation"):
        for r in rows(conn, "SELECT * FROM summary_annotation"):
            level = v(col(r, "Level_of_Evidence"))
            gene, variants = v(col(r, "Gene")), v(col(r, "Variant_Haplotypes"))
            # Level 4: "the total score is negative and the evidence does not support an
            # association between the variant and the drug phenotype" (ClinPGx)
            outcome = "negative" if level == "4" else "positive"
            context = ctx(confidence=f"ClinPGx level {level}" if level else None,
                          variant=variants, measure=v(col(r, "Phenotype_Category")),
                          phenotype=v(col(r, "Phenotype_s")),
                          flags=v(col(r, "Level_Modifiers")),
                          condition=v(col(r, "Specialty_Population")))
            ref = v(col(r, "URL")) or f"clinpgx:{v(col(r, 'Summary_Annotation_ID'))}"
            listed = [p.strip() for p in (variants or "").split(",") if p.strip()]
            single = _variant_id(listed[0]) if len(listed) == 1 else None
            for name in _split(col(r, "Drug_s")):
                d = by_name.get(name.casefold())
                if d is None:
                    yield unresolved("drug_pharmacogene", "clinpgx", None, name, gene,
                                     "drug name not in chemicals.tsv", reference=ref)
                    continue
                for g in _split(gene):
                    yield rel("drug_pharmacogene", "clinpgx", d[0], d[1], f"symbol:{g}", g,
                              "associated", score=v(col(r, "Score")), reference=ref,
                              outcome=outcome, context=context)
                if single:
                    yield rel("variant_drug", "clinpgx", single, listed[0], d[0], d[1],
                              "associated", score=v(col(r, "Score")), reference=ref,
                              outcome=outcome, context=context,
                              subject_type="allele" if single.startswith("allele:")
                              else None)

    if has(conn, "drug_label"):
        for r in rows(conn, "SELECT * FROM drug_label"):
            genes = _split(col(r, "Genes"))
            agency = v(col(r, "Source"))
            label_id = v(col(r, "PharmGKB_ID"))
            context = ctx(confidence=v(col(r, "Testing_Level")),
                          variant=v(col(r, "Variants_Haplotypes")),
                          flags=names(col(r, "Biomarker_Flag"), col(r, "Has_Prescribing_Info"),
                                      col(r, "Has_Dosing_Info"), col(r, "Has_Alternate_Drug"),
                                      col(r, "Has_Other_Prescribing_Guidance"),
                                      col(r, "Cancer_Genome")),
                          source_db=agency, source_id=label_id)
            for name in _split(col(r, "Chemicals")):
                if name.casefold() not in by_name:
                    yield unresolved("drug_pharmacogene", "clinpgx", None, name,
                                     "; ".join(genes) or None, "drug name not in chemicals.tsv",
                                     reference=f"clinpgx:{label_id}" if label_id else None)
                    continue
                d = by_name[name.casefold()]
                for g in genes:
                    yield rel("drug_pharmacogene", "clinpgx", d[0], d[1], f"symbol:{g}", g,
                              "listed", reference=f"clinpgx:{label_id}" if label_id else None,
                              note=f"via {agency}" if agency else None, context=context)


# ------------------------------------------------------------------------------- GPCRdb
#: GPCRdb's drug_target_relationship -> effect; the word stays in context["action"]. An
#: allosteric modulator states its sign (PAM, NAM); an inverse agonist lowers the
#: receptor's constitutive activity. "Unknown" has no direction.
_GPCR_EFFECT = {"agonist": "activation", "agonist (partial)": "activation",
                "pam": "activation", "antagonist": "inhibition", "nam": "inhibition",
                "inverse agonist": "inhibition"}
_TAG = re.compile(r"<[^>]+>")


def _plain(text: str | None) -> str | None:
    """GPCRdb names are HTML: ``&beta;<sub>2</sub>-adrenoceptor`` -> ``β2-adrenoceptor``."""
    return v(html.unescape(_TAG.sub("", text))) if v(text) else None


def _gpcrdb(conn) -> Iterator[Row | None]:
    if not has(conn, "drug_target"):
        return
    for r in rows(conn, "SELECT * FROM drug_target"):
        ik, lig = v(r["inchikey"]), v(r["gpcrdb_ligand_id"])
        subject = (f"inchikey:{ik}" if ik and len(ik) == 27 else
                   f"gpcrdb:ligand.{lig}" if lig else None)
        acc = v(r["gpcr_target_uniprot_id"])
        action = v(r["drug_target_relationship"])
        yield rel("drug_target", "gpcrdb", subject, r["drug_name"],
                  f"uniprot:{acc}" if acc else None,
                  names(_plain(r["gpcr_target"]), r["gpcr_target_entry_name"]), "known",
                  effect=_GPCR_EFFECT.get((action or "").lower()),
                  context=ctx(action=action, stage=v(r["fda_approval_status"]),
                              species="9606", source_id=lig))


# ------------------------------------------------------------------------------ specs
_CLINPGX = "https://api.clinpgx.org/v1/download/file/data/"

#: PharmVar's GRCh38 single-nucleotide definitions: rsID -> the shared GRCh38 id the
#: extractor writes (NC_0000NN.v -> chromosome NN, 23 -> X, 24 -> Y).
_PV_VARIANT_XWALK = (
    "SELECT DISTINCT 'dbsnp:' || rsID, 'grch38:' || "
    "CASE CAST(substr(ReferenceSequence, 4, 6) AS INTEGER) WHEN 23 THEN 'X' WHEN 24 THEN 'Y' "
    "ELSE CAST(substr(ReferenceSequence, 4, 6) AS INTEGER) END || '-' || Variant_Start || "
    "'-' || Reference_Allele || '-' || Variant_Allele FROM haplotypes "
    "WHERE Reference_Set = 'GRCh38' AND rsID GLOB 'rs[0-9]*' AND rsID NOT GLOB 'rs*[^0-9]*' "
    "AND ReferenceSequence GLOB 'NC_0000[0-2][0-9].*' "
    "AND (Variant_Stop IS NULL OR Variant_Stop = Variant_Start) "
    "AND Reference_Allele GLOB '[ACGT]' AND Variant_Allele GLOB '[ACGT]'")

#: ClinPGx drugs the extractor names ``rxnorm:`` (exactly one RxNorm id) -> their one
#: PubChem CID, so CPIC's and ClinPGx's RxNorm drugs meet structure-keyed sources.
_CLINPGX_COMPOUND_XWALK = (
    "SELECT 'rxnorm:' || RxNorm_Identifiers, 'pubchem:' || PubChem_Compound_Identifiers "
    "FROM chemical WHERE RxNorm_Identifiers GLOB '[0-9]*' "
    "AND RxNorm_Identifiers NOT GLOB '*[^0-9]*' "
    "AND PubChem_Compound_Identifiers GLOB '[0-9]*' "
    "AND PubChem_Compound_Identifiers NOT GLOB '*[^0-9]*'")

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "pharmvar", "PharmVar (Pharmacogene Variation Consortium)", (110,),
        "https://www.pharmvar.org/", _PV_LICENSE,
        (FileSpec("https://www.pharmvar.org/get-download-file?name=ALL&refSeq=ALL&"
                  "fileType=zip&version=current", "pharmvar_all.zip", "haplotypes",
                  fmt="pharmvar_haplotypes",
                  note="all genes, all reference sequences; 193,085,083 bytes for "
                       "6.2.29 (sent chunked, no size announced, so a re-fetch cannot "
                       "check the size; the downloader's 512 MB gate still applies), of "
                       "which the haplotype tables are 2.9 MB; per-allele VCFs and FASTA "
                       "sequences are not loaded. The sha256 of the fetched file is in "
                       "raw/pharmvar/.downloads.json. Not pinned by size: the URL serves "
                       "the current release, which changes size with every release"),),
        version="6.2.29 (VCF fileDate 2026-09-22; 15 genes)",
        notes="Star-allele definitions of the PharmVar genes (CYP1A2, CYP2A6, CYP2A13, "
              "CYP2B6, CYP2C8, CYP2C9, CYP2C19, CYP2D6, CYP3A4, CYP3A5, CYP4F2, DPYD, NAT2, "
              "NUDT15, SLCO1B1): each allele and sub-allele with the variants that define "
              "it, on RefSeqGene, GRCh37 and GRCh38. allele_variant rows are the GRCh38 "
              "definitions, evidence 'listed' (nomenclature, not an association). "
              "Function assignments are not in the files (PharmVar's API, which serves "
              "them, needs an account key; CPIC's API serves CPIC's).",
        relations=("allele_variant",), commercial_use="forbidden",
        crosswalk={"variant": _PV_VARIANT_XWALK}),
    DatasetSpec(
        "cpic", "CPIC gene-drug pairs", (111,), "https://cpicpgx.org/", _CPIC_LICENSE,
        (FileSpec("https://files.cpicpgx.org/data/report/current/pair/"
                  "cpic_gene-drug_pairs.xlsx", "cpic_gene-drug_pairs.xlsx", "pair",
                  fmt="xlsx", sheet="CPIC Gene-Drug Pairs"),),
        version="report of 2026-08-07",
        notes="Every gene-drug pair CPIC has assessed, with its CPIC level (A-D), level "
              "status (final or provisional), ClinPGx level of evidence, the PGx level of "
              "the FDA label and the guideline publications. Pairs of level A, A/B and B "
              "(prescribing action recommended) are 'known'; B/C, C, C/D and D (no action "
              "recommended, evidence weak or unclear) are 'listed'; retired pairs are "
              "left out. The live API (connector 'cpic') adds alleles, function and "
              "recommendations.",
        relations=("drug_pharmacogene",), commercial_use="allowed"),
    DatasetSpec(
        "clinpgx", "ClinPGx (formerly PharmGKB) annotations", (111,),
        "https://www.clinpgx.org/", _CLINPGX_LICENSE,
        (FileSpec(_CLINPGX + "relationships.zip", "relationships.zip", "relationships",
                  fmt="clinpgx:relationships.tsv"),
         FileSpec(_CLINPGX + "summaryAnnotations.zip", "summaryAnnotations.zip",
                  "summary_annotation", fmt="clinpgx:summary_annotations.tsv"),
         FileSpec(_CLINPGX + "chemicals.zip", "chemicals.zip", "chemical",
                  fmt="clinpgx:chemicals.tsv"),
         FileSpec(_CLINPGX + "drugLabels.zip", "drugLabels.zip", "drug_label",
                  fmt="clinpgx:drugLabels.tsv")),
        version="files of 2026-09-05",
        notes="relationships.tsv: ClinPGx's gene-drug and variant/haplotype-drug pairs "
              "with association (associated -> positive, not associated -> negative, "
              "ambiguous -> inconclusive), derived from its own annotations: 'aggregated'. "
              "summary_annotations.tsv: literature-scored variant-drug annotations, "
              "'associated', with the level of evidence (1A-4) in context; level 4 (score "
              "below zero, evidence does not support an association) is a negative "
              "result. Each becomes a gene-drug row and, when it names one variant or "
              "allele, a variant-drug row. drugLabels.tsv: regulators' label annotations, "
              "'listed', with the testing level and the agency (note 'via FDA', ...). "
              "The download URLs answer 303 to s3.pgkb.org. robots.txt of api.clinpgx.org "
              "and www.clinpgx.org (checked 2026-10-01): 'User-agent: * Allow: /' with "
              "'Crawl-delay: 30', and the content signals search=yes, ai-train=no, "
              "use=reference; ClinPGx's API documentation limits clients to 2 requests "
              "per second. The four files are documented downloads, so they are fetched, "
              "and the hub spaces every request to the host (size probe and download) 30 "
              "s apart (min_interval_s), so a fetch takes about four minutes. The "
              "ai-train=no signal reserves model training: the data must not be used to "
              "train models.",
        relations=("drug_pharmacogene", "variant_drug"), commercial_use="forbidden",
        min_interval_s=30.0, crosswalk={"compound": _CLINPGX_COMPOUND_XWALK},
        upstream=("CPIC", "DPWG", "FDA", "EMA", "PMDA", "HCSC", "Swissmedic")),
    DatasetSpec(
        "gpcrdb", "GPCRdb drug-target table", (112,), "https://gpcrdb.org/",
        "CC BY 4.0",
        (FileSpec("https://gpcrdb.org/services/drugs/download/", "gpcrdb_drugs.json",
                  "drug_target", fmt="json",
                  note="the documented full-table download of the web services; 596 KB"),),
        version="release of 2026-09-22",
        notes="Approved (FDA) and in-trial drugs on human GPCRs: InChIKey, UniProt "
              "accession, ligand type, approval status and the drug-target relationship "
              "(agonist, partial agonist, antagonist, inverse agonist, PAM, NAM), as "
              "drug_target rows with the effect it states. Ligand bioactivities, "
              "mutagenesis data and structures are served per receptor by the live "
              "connector 'gpcrdb'.",
        relations=("drug_target",), commercial_use="allowed"),
)

EXTRACTORS = {"pharmvar": _pharmvar, "cpic": _cpic, "clinpgx": _clinpgx, "gpcrdb": _gpcrdb}
