"""Cell lines and perturbation resources (review of 2026-09-30, checked 2026-10-01).

Five sources, each taken only in the way its site allows:

* **Cellosaurus** (catalogue 90): the release flat file ``cellosaurus.txt`` (CC BY 4.0),
  read into a cell-line table, its diseases, its cross-references and every name it is
  known by. The live API is the ``cellosaurus`` connector (``providers.supplement``).
* **LINCS L1000 on GEO** (91): only the small metadata tables of GSE92742 (Phase I) and
  GSE70138 (Phase II): signatures, perturbagens, cell lines and the gene space. The
  signature matrices (GCTX, 5-50 GB) are optional raw files for the analysis layer and
  are never turned into rows. GSE106127 is a re-processed subset of both series.
* **DepMap** (92): the CC BY 4.0 figshare deposits of DepMap Public 24Q4 and Repurposing
  Public 24Q2. Releases 25Q2 and later sit behind a Cloudflare Turnstile and terms that
  forbid commercial use; they are not fetched.
* **scPerturb** (93): the file lists of the two Zenodo deposits; the h5ad files are
  optional raw files (relations from them need single-cell differential expression).
* **ARCHS4** (94): the version list; the HDF5 compendia (38-400 GB each) are optional raw
  pointers to pinned versions.

Only Cellosaurus and DepMap yield relations; the matrices of LINCS, DepMap, scPerturb and
ARCHS4 stay files.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

#: A cell line's disease: the disease of the individual the line was taken from, as a
#: curated listing (Cellosaurus DI lines, DepMap's OncoTree annotation). No registered
#: kind has a cell line as its subject.
KINDS = {"cell_line_disease": ("cell_line", "disease")}


# --------------------------------------------------------------------------- Cellosaurus
def _cellosaurus_records(path: Path) -> Iterator[dict[str, list[str]]]:
    """Entries of the flat file: line code -> its values, in file order.

    The file opens with a preamble (licence, line-code table) whose lines start with a
    space; an entry starts at ``ID   `` and ends at ``//``.
    """
    rec: dict[str, list[str]] | None = None
    with open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line.startswith("//"):
                if rec is not None:
                    yield rec
                rec = None
                continue
            if len(line) < 5 or line[2:5] != "   " or not line[:2].strip():
                continue
            code, value = line[:2], line[5:].strip()
            if code == "ID":
                rec = {"ID": [value]}
            elif rec is not None:
                rec.setdefault(code, []).append(value)
    if rec is not None:
        yield rec


def _comments(rec: dict[str, list[str]], category: str) -> str | None:
    prefix = category + ":"
    out = [c[len(prefix):].strip() for c in rec.get("CC", []) if c.startswith(prefix)]
    return " | ".join(out) or None


def _split(value: str | None, sep: str = "; ") -> list[str]:
    return [p.strip() for p in (value or "").split(sep) if p.strip()]


_TAXON = re.compile(r"NCBI_TaxID=(\d+);\s*(?:!\s*(.*))?")

_CELL_LINE_COLUMNS = ["accession", "name", "secondary_accessions", "synonyms", "category",
                      "sex", "age", "species_taxids", "species", "diseases", "parents",
                      "same_individual", "problematic", "derived_from_site", "cell_type",
                      "dates"]


def read_cellosaurus(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """``cellosaurus.txt`` as one of four tables, chosen by the file spec's table name.

    * ``cell_line``: one row per entry; multi-line fields joined by `` | `` as written;
    * ``cell_line_disease``: one row per DI line (database, id, name);
    * ``cell_line_xref``: one row per DR line (database, id);
    * ``cell_line_name``: the recommended name and every synonym, one per row, for exact
      name look-ups (the API's search is ranked, not exact).
    """
    table = spec.table
    if table == "cell_line":
        yield list(_CELL_LINE_COLUMNS)
    elif table == "cell_line_disease":
        yield ["accession", "database", "disease_id", "disease_name"]
    elif table == "cell_line_xref":
        yield ["accession", "database", "xref_id"]
    elif table == "cell_line_name":
        yield ["accession", "name", "name_type"]
    else:                                                   # pragma: no cover - programming
        raise ValueError(f"cellosaurus: no table {table!r}")
    for rec in _cellosaurus_records(path):
        ac = (rec.get("AC") or [None])[0]
        if not ac:
            continue
        name = rec["ID"][0]
        if table == "cell_line":
            taxa = [_TAXON.match(o) for o in rec.get("OX", [])]
            yield [ac, name, " | ".join(rec.get("AS", [])) or None,
                   " | ".join(rec.get("SY", [])) or None,
                   (rec.get("CA") or [None])[0], (rec.get("SX") or [None])[0],
                   (rec.get("AG") or [None])[0],
                   "; ".join(m.group(1) for m in taxa if m) or None,
                   "; ".join((m.group(2) or "").strip() for m in taxa if m) or None,
                   " | ".join(rec.get("DI", [])) or None, " | ".join(rec.get("HI", [])) or None,
                   " | ".join(rec.get("OI", [])) or None,
                   _comments(rec, "Problematic cell line"),
                   _comments(rec, "Derived from site"), _comments(rec, "Cell type"),
                   (rec.get("DT") or [None])[0]]
        elif table == "cell_line_disease":
            for di in rec.get("DI", []):
                parts = di.split("; ", 2)
                if len(parts) == 3:
                    yield [ac, parts[0], parts[1], parts[2]]
        elif table == "cell_line_xref":
            for dr in rec.get("DR", []):
                db, _, ident = dr.partition("; ")
                if ident:
                    yield [ac, db, ident]
        else:
            yield [ac, name, "recommended"]
            for sy in rec.get("SY", []):
                for s in _split(sy):
                    yield [ac, s, "synonym"]


def _disease_id(database: str | None, ident: str | None) -> str | None:
    database, ident = v(database), v(ident)
    if not database or not ident:
        return None
    if database == "NCIt":
        return f"ncit:{ident}"
    if database == "ORDO":
        return "orpha:" + ident.removeprefix("Orphanet_")
    return f"{database.lower()}:{ident}"


def _problem_flag(text: str | None) -> str | None:
    """'Contaminated. Shown to be a HeLa derivative ...' -> 'problematic: Contaminated'."""
    text = v(text)
    if not text:
        return None
    kinds = []
    for part in text.split(" | "):
        head = re.split(r"[.:(]", part, maxsplit=1)[0].strip()[:60]
        if head and head not in kinds:
            kinds.append(head)
    return "problematic cell line: " + "; ".join(kinds or ["see cell_line.problematic"])


def _cellosaurus(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "cell_line_disease"):
        return
    line: dict[str, tuple[str | None, str | None, str | None]] = {}
    if has(conn, "cell_line"):
        for r in rows(conn, "SELECT accession, name, synonyms, species_taxids, problematic "
                            "FROM cell_line"):
            line[r["accession"]] = (names(r["name"], *(r["synonyms"] or "").split(" | ")),
                                    r["species_taxids"], _problem_flag(r["problematic"]))
    for r in rows(conn, "SELECT * FROM cell_line_disease"):
        ac = v(r["accession"])
        label, taxa, flag = line.get(ac or "", (None, None, None))
        yield rel("cell_line_disease", "cellosaurus", f"cellosaurus:{ac}" if ac else None,
                  label, _disease_id(r["database"], r["disease_id"]), r["disease_name"],
                  "listed", context=ctx(species=taxa, flags=flag))


# ------------------------------------------------------------------------------- DepMap
_GENE_ENTREZ = re.compile(r"^\s*(\S+)\s*\((\d+)\)\s*$")


def _depmap(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if has(conn, "model"):
        for r in rows(conn, "SELECT * FROM model"):
            model, rrid = v(r["ModelID"]), v(r["RRID"])
            subject = f"cellosaurus:{rrid}" if rrid and rrid.startswith("CVCL_") \
                else (f"depmap:{model}" if model else None)
            label = names(r["CellLineName"], r["StrippedCellLineName"], model)
            code, disease = v(r["OncotreeCode"]), v(r["OncotreePrimaryDisease"])
            note = f"depmap:{model}" if model else None
            if code:
                yield rel("cell_line_disease", "depmap", subject, label, f"oncotree:{code}",
                          names(r["OncotreeSubtype"], disease), "listed", note=note,
                          context=ctx(tissue=r["OncotreeLineage"]))
            elif disease and disease.lower() not in ("non-cancerous", "unknown"):
                # a disease named without an OncoTree code: nothing to join it to
                yield unresolved("cell_line_disease", "depmap", subject, label,
                                 names(r["OncotreeSubtype"], disease),
                                 "no OncoTree code for the model's primary disease",
                                 note=note)
    if has(conn, "common_essentials"):
        for r in rows(conn, "SELECT * FROM common_essentials"):
            m = _GENE_ENTREZ.match(v(r["Essentials"]) or "")
            if not m:
                continue
            yield rel("screen_gene", "depmap", "depmap:screen.CRISPRInferredCommonEssentials",
                      "DepMap Public 24Q4 CRISPR inferred common essentials",
                      f"symbol:{m.group(1)}", m.group(1), "known", note=f"ncbigene:{m.group(2)}",
                      context=ctx(method="CRISPR knockout screens (Chronos gene effect)",
                                  phenotype="common essential", screen="DepMap Public 24Q4"))
    if has(conn, "portal_compounds"):
        for r in rows(conn, "SELECT * FROM portal_compounds"):
            cid = v(r["CompoundID"])
            for sym in (v(r["GeneSymbolOfTargets"]) or "").split(";"):
                sym = sym.strip()
                if not sym:
                    continue
                yield rel("drug_target", "depmap", f"depmap:{cid}" if cid else None,
                          names(r["CompoundName"], r["Synonyms"]), f"symbol:{sym}", sym,
                          "aggregated", context=ctx(mechanism=r["TargetOrMechanism"]))


# ------------------------------------------------------------------- scPerturb / ARCHS4
def read_zenodo_files(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A Zenodo record export (``/records/<id>/export/json``): one row per deposited file."""
    with open_text(path) as fh:
        data = json.load(fh)
    meta = data.get("metadata") or {}
    rights = "; ".join(r.get("id", "") for r in meta.get("rights") or [] if r.get("id"))
    doi = ((data.get("pids") or {}).get("doi") or {}).get("identifier")
    yield ["record_id", "version", "doi", "license", "key", "size", "checksum", "url",
           "publication_date"]
    entries = ((data.get("files") or {}).get("entries") or {})
    for key in sorted(entries):
        f = entries[key]
        yield [str(data.get("id")), meta.get("version"), doi, rights or None, f.get("key"),
               str(f.get("size")) if f.get("size") is not None else None, f.get("checksum"),
               (f.get("links") or {}).get("content"), meta.get("publication_date")]


_ARCHS4_COLUMNS = ["id", "species", "data_level", "version_major", "version_minor", "samples",
                   "file_size", "checksum", "ensembl_annotation", "timestamp"]


def read_archs4_versions(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The ARCHS4 version list: one row per released HDF5 file (sha1 checksum, size)."""
    with open_text(path) as fh:
        data = json.load(fh)
    yield list(_ARCHS4_COLUMNS)
    for entry in sorted(data.get("versionfiles") or [], key=lambda e: int(e.get("id") or 0)):
        yield [None if entry.get(c) is None else str(entry.get(c)) for c in _ARCHS4_COLUMNS]


READERS = {"cellosaurus_txt": read_cellosaurus, "zenodo_record_files": read_zenodo_files,
           "archs4_versions": read_archs4_versions}


# -------------------------------------------------------------------------------- specs
_CELLO_TXT = "https://ftp.expasy.org/databases/cellosaurus/cellosaurus.txt"
_GEO1 = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE92nnn/GSE92742/suppl/"
_GEO2 = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE70nnn/GSE70138/suppl/"
_GEO3 = "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE106nnn/GSE106127/suppl/"
_FIGSHARE = "https://ndownloader.figshare.com/files/"
_ZENODO_RNA = "https://zenodo.org/api/records/13350497/files/"
_ARCHS4 = "https://s3.dev.maayanlab.cloud/archs4/files/"


def _figshare(file_id: int, name: str, table: str, size: int, *, fmt: str = "csv",
              optional: bool = False, note: str = "") -> FileSpec:
    return FileSpec(_FIGSHARE + str(file_id), name, table, fmt=fmt if table else "raw",
                    optional=optional, expected_bytes=size, note=note)


#: scPerturb's drug-perturbation datasets (sci-Plex, MIX-seq, ...), with their sizes from
#: the record's file list; the genetic screens are listed in the ``files`` table only.
_SCPERTURB_DRUGS = (
    ("AissaBenevolenskaya2021.h5ad", 45919115),
    ("GehringPachter2019.h5ad", 73274146),
    ("SrivatsanTrapnell2020_sciplex2.h5ad", 145178504),
    ("SrivatsanTrapnell2020_sciplex4.h5ad", 253335945),
    ("ChangYe2021.h5ad", 501823050),
    ("ZhaoSims2021.h5ad", 586888140),
    ("McFarlandTsherniak2020.h5ad", 1459410830),
    ("SrivatsanTrapnell2020_sciplex3.h5ad", 2526631614),
)

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "cellosaurus", "Cellosaurus (cell line knowledge resource)", (90,),
        "https://www.cellosaurus.org/", "CC BY 4.0",
        tuple(FileSpec(_CELLO_TXT, "cellosaurus.txt", table, fmt="cellosaurus_txt",
                       note="release flat file, about 122 MB; read four times, once per table")
              for table in ("cell_line", "cell_line_disease", "cell_line_xref",
                            "cell_line_name")),
        version="56.0 (2026-06-25)",
        notes="Curated cell line records (CALIPHO group, SIB): 168,970 cell lines in release "
              "56. cell_line_disease rows are Cellosaurus's DI lines, the disease of the "
              "individual a line was taken from (NCIt or ORDO), a curated listing; a line "
              "flagged 'Problematic cell line' (contaminated, misidentified, misclassified) "
              "carries that flag in the row context, and its DI already names the true "
              "origin (HEp-2 lists HeLa's cervical adenocarcinoma). cell_line_xref maps each "
              "line to DepMap (ACH-), COSMIC, Cell Model Passports, ChEMBL, PubChem, LINCS "
              "and cell banks; cell_line_name resolves a name exactly (the API's search is "
              "ranked). The file is the documented download linked from the Cellosaurus "
              "description page (https://ftp.expasy.org/databases/cellosaurus). "
              "ftp.expasy.org's robots.txt disallows everything for crawlers; only this one "
              "documented file is fetched, once per quarterly release (compare the "
              "connector's release_info), and nothing on that host is listed or crawled. "
              "The same release is also published as RDF (cellosaurus_ttl.tar.gz, 199 MB) "
              "on api.cellosaurus.org, which has no robots rules.",
        relations=("cell_line_disease",), commercial_use="allowed",
        upstream=("NCIt", "ORDO", "NCBI Taxonomy")),
    DatasetSpec(
        "lincs_l1000", "LINCS L1000 metadata (Broad, GEO GSE92742 and GSE70138)", (91,),
        "https://clue.io/GEO-guide",
        "No licence stated. GEO public deposit (NCBI: 'public, non-sensitive, unrestricted "
        "scientific data sharing'); LINCS data release policy: 'released without any "
        "restrictions except correct citation'",
        (FileSpec(_GEO1 + "GSE92742_Broad_LINCS_sig_info.txt.gz",
                  "GSE92742_Broad_LINCS_sig_info.txt.gz", "phase1_sig_info",
                  expected_bytes=11124404, note="473,647 signatures (Phase I)"),
         FileSpec(_GEO1 + "GSE92742_Broad_LINCS_pert_info.txt.gz",
                  "GSE92742_Broad_LINCS_pert_info.txt.gz", "phase1_pert_info",
                  expected_bytes=1138509),
         FileSpec(_GEO1 + "GSE92742_Broad_LINCS_cell_info.txt.gz",
                  "GSE92742_Broad_LINCS_cell_info.txt.gz", "phase1_cell_info",
                  expected_bytes=2528),
         FileSpec(_GEO1 + "GSE92742_Broad_LINCS_gene_info.txt.gz",
                  "GSE92742_Broad_LINCS_gene_info.txt.gz", "gene_info",
                  expected_bytes=216692,
                  note="12,328 genes (978 measured landmarks, pr_is_lm=1); the same in "
                       "GSE70138"),
         FileSpec(_GEO2 + "GSE70138_Broad_LINCS_sig_info_2017-03-06.txt.gz",
                  "GSE70138_Broad_LINCS_sig_info_2017-03-06.txt.gz", "phase2_sig_info",
                  expected_bytes=1943865, note="118,050 signatures (Phase II)"),
         FileSpec(_GEO2 + "GSE70138_Broad_LINCS_pert_info_2017-03-06.txt.gz",
                  "GSE70138_Broad_LINCS_pert_info_2017-03-06.txt.gz", "phase2_pert_info",
                  expected_bytes=82376),
         FileSpec(_GEO2 + "GSE70138_Broad_LINCS_cell_info_2017-04-28.txt.gz",
                  "GSE70138_Broad_LINCS_cell_info_2017-04-28.txt.gz", "phase2_cell_info",
                  expected_bytes=2528),
         FileSpec(_GEO1 + "GSE92742_Broad_LINCS_sig_metrics.txt.gz",
                  "GSE92742_Broad_LINCS_sig_metrics.txt.gz", "phase1_sig_metrics",
                  optional=True, expected_bytes=12520228,
                  note="signature quality: TAS, replicate correlation, exemplar flag"),
         FileSpec(_GEO2 + "GSE70138_Broad_LINCS_sig_metrics_2017-03-06.txt.gz",
                  "GSE70138_Broad_LINCS_sig_metrics_2017-03-06.txt.gz", "phase2_sig_metrics",
                  optional=True),
         FileSpec(_GEO1 + "GSE92742_Broad_LINCS_inst_info.txt.gz",
                  "GSE92742_Broad_LINCS_inst_info.txt.gz", "phase1_inst_info",
                  optional=True, expected_bytes=12046182, note="replicate-level instances"),
         FileSpec(_GEO3 + "GSE106127_sig_info.txt.gz", "GSE106127_sig_info.txt.gz",
                  "gse106127_sig_info", optional=True, expected_bytes=3151251,
                  note="shRNA/CRISPR consensus signatures re-processed from GSE92742 and "
                       "GSE70138 (a subset, not new data): do not count with them"),
         FileSpec(_GEO1 + "GSE92742_Broad_LINCS_Level5_COMPZ.MODZ_n473647x12328.gctx.gz",
                  "GSE92742_Broad_LINCS_Level5_COMPZ.MODZ_n473647x12328.gctx.gz", "",
                  fmt="raw", optional=True, expected_bytes=21328033748,
                  note="Level 5 signatures (moderated z-scores), 21.3 GB HDF5: analysis layer"),
         FileSpec(_GEO2 + "GSE70138_Broad_LINCS_Level5_COMPZ_n118050x12328_2017-03-06.gctx.gz",
                  "GSE70138_Broad_LINCS_Level5_COMPZ_n118050x12328_2017-03-06.gctx.gz", "",
                  fmt="raw", optional=True, note="Phase II Level 5 signatures, 5.0 GB")),
        version="GSE92742 (Phase I, files of 2017) and GSE70138 (Phase II, 2017-03-06 build)",
        notes="Metadata of the L1000 perturbation signatures: which perturbagen (compound "
              "BRD id with InChIKey and SMILES, shRNA, ORF, ligand), at which dose and time, "
              "in which cell line, under which signature id. -666 is the Broad's missing-"
              "value mark and is kept as written. Expression values live only in the GCTX "
              "matrices (optional raw files): 978 landmark genes are measured, the rest "
              "are inferred, so a relation read from them is measured for landmarks and "
              "predicted for the others. No relation rows are extracted. GSE106127 is the "
              "re-processed shRNA/CRISPR portion of GSE92742 and GSE70138, and CLUE's "
              "LINCS2020 build is a re-processing and superset of the same profiles; count "
              "each signature once. The CLUE API needs a registered key and the CLUE S3 "
              "files fall under CLUE terms (named user, academic use, no redistribution): "
              "neither is used. The files are GEO supplementary files on the NCBI FTP site, "
              "whose robots.txt disallows crawling; GEO's own record for each series "
              "(E-utilities esummary 'ftplink') gives that directory as the download "
              "location, and only the named files are fetched (the deposits are frozen "
              "since 2017).",
        commercial_use="allowed",
        upstream=("Broad LINCS Center for Transcriptomics",)),
    DatasetSpec(
        "depmap", "DepMap Public 24Q4 and PRISM Repurposing 24Q2 (figshare deposits)", (92,),
        "https://depmap.org/portal/data_page/", "CC BY 4.0",
        (_figshare(51065297, "Model.csv", "model", 645696,
                   note="cancer models: ModelID, RRID (Cellosaurus), OncoTree lineage and "
                        "disease"),
         _figshare(51064916, "CRISPRInferredCommonEssentials.csv", "common_essentials", 20795),
         _figshare(51065762, "PortalCompounds.csv", "portal_compounds", 692052,
                   note="compounds of the portal's drug screens: targets, mechanism, "
                        "screen ids, ChEMBL id, InChIKey"),
         _figshare(51063560, "AchillesCommonEssentialControls.csv",
                   "common_essential_controls", 17015),
         _figshare(51063566, "AchillesNonessentialControls.csv", "nonessential_controls",
                   11490),
         _figshare(51065795, "README.txt", "", 43103, fmt="raw"),
         _figshare(51064667, "CRISPRGeneEffect.csv", "", 428678699, optional=True,
                   note="Chronos gene effect, models x genes (analysis layer)"),
         _figshare(51064631, "CRISPRGeneDependency.csv", "", 421115594, optional=True,
                   note="probability of dependency, models x genes (analysis layer)"),
         _figshare(51065750, "OmicsSomaticMutationsMatrixHotspot.csv", "", 4210723,
                   optional=True, note="hotspot mutation matrix (analysis layer)"),
         _figshare(46630981, "Repurposing_Public_24Q2_Extended_Primary_Compound_List.csv",
                   "prism_compounds", 719567, optional=True,
                   note="PRISM Repurposing 24Q2 compounds (figshare 25917643, CC BY 4.0)"),
         _figshare(46630984, "Repurposing_Public_24Q2_Extended_Primary_Data_Matrix.csv",
                   "", 72456953, optional=True,
                   note="PRISM viability (log fold change), compounds x cell lines "
                        "(analysis layer)")),
        version="DepMap Public 24Q4 (figshare+ 10.25452/figshare.plus.27993248.v1, "
                "2024-12-10); Repurposing Public 24Q2 (10.6084/m9.figshare.25917643.v1)",
        notes="The last DepMap releases deposited under CC BY 4.0. cell_line_disease rows "
              "are DepMap's OncoTree annotation of each model (subject: the Cellosaurus "
              "accession from the RRID column, else the DepMap ModelID); a primary disease "
              "without an OncoTree code goes to the unresolved queue. screen_gene rows are "
              "the genes DepMap infers to be common essentials across its CRISPR knockout "
              "screens (Chronos; a measured screen call). drug_target rows are the targets "
              "the portal's compound metadata lists (aggregated: DepMap's curation, much of "
              "it from the Broad Drug Repurposing Hub, without per-row provenance; the "
              "mechanism text applies to the compound, so no effect is set). The gene-"
              "effect, dependency and viability matrices are optional raw files and are "
              "never turned into rows. Releases 25Q2, 25Q3 and 26Q1 are reachable only "
              "through a Cloudflare Turnstile challenge and are under DepMap terms that "
              "forbid commercial use (including training models other than for internal "
              "research): they are manual_import, non-commercial, and not wrapped. "
              "depmap.org's robots.txt sets Crawl-delay 500, so its no-captcha release "
              "listing is not polled; the figshare API (documented) lists these files.",
        relations=("cell_line_disease", "screen_gene", "drug_target"),
        commercial_use="allowed",
        upstream=("Sanger Project Score", "Drug Repurposing Hub", "OncoTree", "PubChem"),
        crosswalk={"compound": "SELECT 'depmap:' || CompoundID, 'inchikey:' || InChIKey "
                               "FROM portal_compounds WHERE length(InChIKey) = 27"}),
    DatasetSpec(
        "scperturb", "scPerturb harmonised single-cell perturbation data (Zenodo)", (93,),
        "https://projects.sanderlab.org/scperturb/",
        "CC BY 4.0 (Zenodo record licence; the original studies' deposits keep their own "
        "terms)",
        (FileSpec("https://zenodo.org/records/13350497/export/json",
                  "zenodo_13350497.json", "files", fmt="zenodo_record_files",
                  note="RNA and protein deposit v1.4: 54 h5ad files with size and md5"),
         FileSpec("https://zenodo.org/records/7058382/export/json",
                  "zenodo_7058382.json", "atac_files", fmt="zenodo_record_files",
                  note="ATAC deposit v1.0: 6 zip files"),
         *(FileSpec(_ZENODO_RNA + key + "/content", key, "", fmt="raw", optional=True,
                    expected_bytes=size, note="drug perturbation, AnnData h5ad")
           for key, size in _SCPERTURB_DRUGS)),
        version="Zenodo 13350497 (v1.4, 2024-08-26) and 7058382 (ATAC v1.0)",
        notes="Catalogue of 54 harmonised single-cell RNA/protein and 6 ATAC perturbation "
              "datasets (CRISPRi/a/KO screens, drugs, cytokines), each re-processed from the "
              "original study's deposit (mostly GEO). Relations need single-cell "
              "differential expression per perturbation and are not extracted; the drug "
              "datasets are listed as optional raw h5ad files. Zenodo's robots.txt sets "
              "Crawl-delay 10 and allows /api/records/*/files; the record metadata comes "
              "from the /records/<id>/export/json export, fetched once per refresh.",
        commercial_use="allowed",
        upstream=("GEO", "ArrayExpress")),
    DatasetSpec(
        "archs4", "ARCHS4 uniformly processed RNA-seq (version list and pinned HDF5)", (94,),
        "https://archs4.org/download",
        "not stated (reprocessed public GEO/SRA samples; the site asks for citation of "
        "Lachmann et al. 2018)",
        (FileSpec("https://archs4.org/api/versionfile", "archs4_versionfile.json",
                  "versions", fmt="archs4_versions",
                  note="every released file: species, level, sample count, size, sha1"),
         FileSpec(_ARCHS4 + "human_gene_v2.5.h5", "human_gene_v2.5.h5", "", fmt="raw",
                  optional=True, expected_bytes=47865298452,
                  note="human gene counts v2.5 (888,821 samples), sha1 "
                       "ae96de0519b9f008b0dc3a9f944ee9007daf2f6a"),
         FileSpec(_ARCHS4 + "mouse_gene_v2.5.h5", "mouse_gene_v2.5.h5", "", fmt="raw",
                  optional=True, expected_bytes=38960132574,
                  note="mouse gene counts v2.5 (997,515 samples), sha1 "
                       "22605c9b6c4e7502b0861d4d8591ce128907c39f")),
        version="version list as fetched; HDF5 pinned to v2.5 (2025-01-14)",
        notes="Gene and transcript counts of about a million public RNA-seq samples, "
              "re-aligned uniformly (Ensembl 107). Every HDF5 file is 38-400 GB, so the "
              "compendia are optional raw pointers to pinned versions (the '*.latest.h5' "
              "names are overwritten with each build) and nothing is turned into rows; "
              "slices can be read remotely with HTTP range requests. The version list is "
              "the undocumented JSON endpoint the download page reads (archs4.org has no "
              "robots rules). No data licence is stated; the archs4py client's Apache-2.0 "
              "licence covers code only. Downloads redirect from s3.dev.maayanlab.cloud to "
              "s3.k8s.maayanlab.cloud.",
        commercial_use="unknown",
        upstream=("GEO", "SRA")),
)

EXTRACTORS = {"cellosaurus": _cellosaurus, "depmap": _depmap}
