"""RNA, regulation and protein-modification sources: snapshot datasets.

Checked from the harness on 2026-10-01 (review of 2026-09-30):

* **iPTMnet** (catalogue 115): PTM sites with their enzymes, integrated from curated
  databases and from PIR's text mining (RLIMS-P, eFIP). Each row names its source, so
  curated rows are ``aggregated`` with ``via <source>`` and text-mined rows are
  ``mentioned``. The licence is non-commercial (CC BY-NC-SA 4.0); the readme footer's
  CC BY 4.0 contradicts the licence page and is not applied.
* **DisProt** (116, CC BY 4.0): manually curated disordered regions with IDPO/GO terms,
  ECO evidence codes and the papers, and the binding partners of those regions.
* **RNAcentral** (117, CC0): id-mapping tables (URS <-> miRBase, HGNC, TarBase, LncBase).
  Targets and interactions are API-only (connector ``rnacentral``).
* **miRTarBase** (118): experimentally validated miRNA-target interactions. Its host
  rate-limits hard (HTTP 429), so files are fetched once and never retried in a loop.
* **ChIP-Atlas** (119): antigen, cell type and analysis lists, and the per-TF Target
  Genes tables, fetched per transcription factor on request (``fetch_target_genes``),
  never in bulk.
* **ReMap 2022** (120, CC BY-NC 4.0): the dataset index (regulator, GEO/ENCODE series,
  cell type) and the human biotype table; the BED catalogues are optional raw files.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..rowkit import Row, col, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS", "fetch_target_genes",
           "IPTMNET_SOURCES"]

#: Relation kinds no registered kind fits.
KINDS = {
    # a residue of a protein observed modified, with no enzyme named (iPTMnet's
    # substrate-only rows). ``ptm_site`` needs an enzyme as its subject.
    "protein_site": ("protein", "site"),
    # a curated annotation of a protein region (residues start-end) with an ontology
    # term: DisProt's IDPO disorder states and transitions, GO functions and processes
    "protein_region": ("protein", "term"),
}


def _clean(values: Iterable[Any]) -> list[str | None]:
    out: list[str | None] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        text = str(value).strip()
        out.append(text or None)
    return out


# ---------------------------------------------------------------------------- readers
def _tab(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """Tab-separated, one record per line, never rejoined across lines.

    The header is ``spec.columns`` for a headerless file, else the file's first line.
    A line with more fields than the header keeps the surplus, tab-joined, in the last
    column (ChIP-Atlas's free metadata columns); a shorter line is padded.
    """
    header: list[str] | None = list(spec.columns) or None
    if header:
        yield header
    with open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if header is None:
                header = [p.strip() for p in parts]
                yield header
                continue
            n = len(header)
            if len(parts) > n:
                parts = parts[:n - 1] + ["\t".join(parts[n - 1:])]
            yield _clean(parts) + [None] * (n - len(parts))


def _disprot_entries_json(path: Path) -> list[dict[str, Any]]:
    with open_text(path) as fh:
        data = json.load(fh)
    entries = data.get("data", []) if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)]


def _disprot_entries(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One row per DisProt entry: ids, the first gene name, organism and counts."""
    yield ["disprot_id", "acc", "name", "gene", "organism", "ncbi_taxon_id", "length",
           "disorder_content", "regions_counter", "released"]
    for e in _disprot_entries_json(path):
        gene = None
        for g in e.get("genes") or []:
            name = g.get("name") if isinstance(g, dict) else None
            if isinstance(name, dict) and name.get("value"):
                gene = name["value"]
                break
        yield _clean([e.get("disprot_id"), e.get("acc"), e.get("name"), gene,
                      e.get("organism"), e.get("ncbi_taxon_id"), e.get("length"),
                      e.get("disorder_content"), e.get("regions_counter"),
                      e.get("released")])


def _disprot_partners(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One row per curated binding partner of a DisProt region (JSON only)."""
    yield ["disprot_id", "acc", "region_id", "start", "end", "term_id", "term_name",
           "term_namespace", "term_is_binding", "ec_id", "ec_name", "reference_source",
           "reference_id", "confidence", "partner_db", "partner_id", "partner_start",
           "partner_end"]
    for e in _disprot_entries_json(path):
        for r in e.get("regions") or []:
            if not isinstance(r, dict):
                continue
            for p in r.get("interaction_partner") or []:
                if not isinstance(p, dict):
                    continue
                yield _clean([e.get("disprot_id"), e.get("acc"), r.get("region_id"),
                              r.get("start"), r.get("end"), r.get("term_id"),
                              r.get("term_name"), r.get("term_namespace"),
                              r.get("term_is_binding"), r.get("ec_id"), r.get("ec_name"),
                              r.get("reference_source"), r.get("reference_id"),
                              r.get("confidence"), p.get("db"), p.get("id"),
                              p.get("partner_start"),
                              p.get("partner_end")])


#: Where ``fetch_target_genes`` puts ChIP-Atlas Target Genes tables, under raw/chip_atlas.
TARGET_DIR = "target_genes"
_TARGET_NAME = re.compile(r"^(?P<genome>[^.]+)\.(?P<antigen>.+)\.(?P<distance>1|5|10)\.tsv$")


def _chip_targets(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The Target Genes tables fetched so far, one row per (TF, gene, window).

    ``path`` is the dataset's analysis list; the tables sit beside it in
    ``target_genes/<genome>.<antigen>.<distance>.tsv`` (``fetch_target_genes``). Each
    table is a matrix: one row per gene whose TSS a peak of the TF overlaps within the
    window, one column per experiment (MACS2 score, 0 = no peak), an ``Average`` column
    and a STRING column. The matrix stays in the file; the store keeps, per gene, the
    average, how many experiments bound it, the maximum and the bound experiments.
    """
    yield ["genome", "antigen", "distance_kb", "gene", "average", "experiments", "bound",
           "max_score", "string_score", "bound_experiments", "file"]
    folder = Path(path).parent / TARGET_DIR
    for table in sorted(folder.glob("*.tsv")) if folder.is_dir() else ():
        m = _TARGET_NAME.match(table.name)
        if not m:
            continue
        with open_text(table) as fh:
            header = fh.readline().rstrip("\r\n").split("\t")
            if len(header) < 3 or header[0] != "Target_genes" \
                    or not header[1].endswith("|Average"):
                continue
            exps = header[2:-1] if header[-1] == "STRING" else header[2:]
            string_col = len(header) - 1 if header[-1] == "STRING" else None
            for line in fh:
                parts = line.rstrip("\r\n").split("\t")
                if not parts or not parts[0].strip():
                    continue
                scores = parts[2:2 + len(exps)]
                bound = [exps[i] for i, s in enumerate(scores) if _num(s)]
                values = [x for x in (_num(s) for s in scores) if x is not None]
                yield _clean([m["genome"], m["antigen"], m["distance"], parts[0],
                              parts[1] if len(parts) > 1 else None, len(exps), len(bound),
                              max(values) if values else None,
                              parts[string_col] if string_col is not None
                              and len(parts) > string_col else None,
                              ",".join(bound), table.name])


def _num(text: Any) -> float | None:
    """A positive score, else None (0 is ChIP-Atlas's 'no peak in this experiment')."""
    try:
        x = float(text)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


_ANCHOR = re.compile(r"<a\s+href=['\"]?([^'\" >]+)['\"]?[^>]*>(.*?)</a>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_REMAP = "https://remap.univ-amu.fr/"


def _anchor(cell: Any) -> tuple[str | None, str | None]:
    text = str(cell or "")
    m = _ANCHOR.search(text)
    if not m:
        return None, (_TAGS.sub("", text).strip() or None)
    return m.group(1), (_TAGS.sub("", m.group(2)).strip() or None)


def _remap_datasets(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """ReMap's per-target download index: one row per regulator x series x biotype.

    The JSON is DataTables rows of HTML anchors; the links are kept as absolute https
    URLs (the site serves the same files over https).
    """
    yield ["target", "taxid", "species", "series", "biotype", "all_peaks_url",
           "nr_peaks_url", "assembly"]
    with open_text(path) as fh:
        data = json.load(fh)
    for row in (data.get("data") or []) if isinstance(data, dict) else []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        thref, target = _anchor(row[0])
        taxid = thref.rsplit(":", 1)[1] if thref and ":" in thref else None
        _, series = _anchor(row[2])
        _, biotype = _anchor(row[3])
        allhref, assembly = _anchor(row[4])
        nrhref, _ = _anchor(row[5])

        def absolute(href: str | None) -> str | None:
            if not href:
                return None
            return href if href.startswith("http") else urllib.parse.urljoin(_REMAP, href)
        yield _clean([target, taxid, _anchor(row[1])[1], series, biotype, absolute(allhref),
                      absolute(nrhref), assembly])


READERS = {"rnareg_tab": _tab, "disprot_entries": _disprot_entries,
           "disprot_partners": _disprot_partners, "chip_atlas_targets": _chip_targets,
           "remap_datasets": _remap_datasets}


# --------------------------------------------------------------------------- datasets
_IPTM = "https://research.bioinformatics.udel.edu/iptmnet_data/files/current/"
_DISPROT = ("https://disprot.org/api/search?release=2026_06&show_ambiguous=true"
            "&show_obsolete=false&namespace=all&get_consensus=false&format=")
_RNAC = "https://ftp.ebi.ac.uk/pub/databases/RNAcentral/releases/27.0/"
_MTB = "https://awi.cuhk.edu.cn/miRTarBase/downloads/files/"
_CHIP = "https://chip-atlas.dbcls.jp/data/"
_REMAP_STORE = _REMAP + "storage/remap2022/"

_IPTMNET_LICENCE = ("CC BY-NC-SA 4.0 (iPTMnet licence page, download page and readme "
                    "header; the readme footer says CC BY 4.0 and the API spec CC BY-NC-ND "
                    "4.0: the files' most restrictive statement is applied). Citing the "
                    "source databases of each row is required when redistributing.")
_CHIP_LICENCE = ("CC BY 4.0 (chip-atlas.org footer and OpenAPI); the NBDC LSDB archive "
                 "licence page (2020-05-14) states CC BY-SA 4.0, so share-alike is assumed")
_MTB_LICENCE = ("Self-declared public domain: the LICENSE file is a warranty disclaimer "
                "('MIRTARBASE IS PROVIDED AT NO COST IN THE PUBLIC DOMAIN'), with no "
                "licence grant; the site footer says 'Copyright ISBLab, CUHK-Shenzhen'")
_RNAC_COLS = ("urs", "database", "external_id", "taxid", "rna_type", "gene")

#: iPTMnet ``source`` codes -> the resource a row comes from. Codes not listed are kept
#: as written (upper-cased) in ``via``.
IPTMNET_SOURCES: dict[str, str] = {
    "pro": "PRO", "unip": "UniProt", "hprd": "HPRD", "pgrd": "PhosphoGRID",
    "phat": "PhosPhAt", "p3db": "P3DB", "sgd": "SGD", "pomb": "PomBase", "iedb": "IEDB",
    "intact": "IntAct", "glygen": "GlyGen", "sno": "dbSNO", "sign": "SIGNOR",
    "npro": "neXtProt", "rlim": "RLIMS-P", "rlim+": "RLIMS-P", "efip": "eFIP",
}
#: Sources that are PIR's text mining of abstracts, not curation.
_TEXT_MINED = frozenset({"rlim", "rlim+", "efip"})

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "iptmnet", "iPTMnet", (115,), "https://research.bioinformatics.udel.edu/iptmnet/",
        _IPTMNET_LICENCE,
        (FileSpec(_IPTM + "readme.txt", "readme.txt", "", fmt="raw",
                  note="Release 6.2 description of the three files and the licence"),
         FileSpec(_IPTM + "ptm.txt", "ptm.txt", "ptm", fmt="rnareg_tab",
                  columns=("ptm_type", "source", "substrate_ac", "substrate_gene",
                           "organism", "site", "enzyme_ac", "enzyme_gene", "note", "pmid"),
                  note="One row per PTM site per source and paper; enzyme empty when "
                       "none is known (44 MB, last modified 2025-10-14)"),
         FileSpec(_IPTM + "score.txt", "score.txt", "score", fmt="rnareg_tab",
                  columns=("substrate_ac", "site", "enzyme_ac", "ptm_type", "score"),
                  note="iPTMnet's integer score per (substrate, site, enzyme, type), "
                       "'calculated based on all the evidence sources' (2024-01-31)"),
         FileSpec(_IPTM + "protein.txt", "protein.txt", "protein", fmt="rnareg_tab",
                  columns=("uniprot_ac", "uniprot_id", "protein_name", "gene_name",
                           "organism", "pro_id", "review_status"))),
        version="6.2 (ptm.txt 2025-10-14; score.txt and protein.txt 2024-01-31)",
        notes="Enzyme-substrate-site records (ptm_site) and substrate sites with no enzyme "
              "named (protein_site). Evidence follows each row's source: curated upstream "
              "databases are 'aggregated' with 'via <database>' and the source code in "
              "context; RLIMS-P and eFIP rows are PIR's text mining of abstracts "
              "('mentioned'). The score is iPTMnet's evidence-count score, joined from "
              "score.txt (older than ptm.txt, so some rows have none). The API "
              "(connector 'iptmnet', pending) returned HTTP 503 on 2026-10-01.",
        relations=("ptm_site", "protein_site"),
        commercial_use="forbidden",
        upstream=("PRO", "UniProt", "HPRD", "PhosphoGRID", "PhosPhAt", "P3DB", "SGD",
                  "PomBase", "IEDB", "IntAct", "GlyGen", "dbSNO", "SIGNOR", "neXtProt"),
        crosswalk={"gene": "SELECT 'uniprot:' || uniprot_ac, 'symbol:' || "
                           "trim(substr(gene_name, 7, instr(gene_name, ';') - 7)) "
                           "FROM protein WHERE review_status = 'SP' AND organism LIKE "
                           "'Homo sapiens%' AND gene_name LIKE 'Name: %' AND "
                           "instr(gene_name, ';') > 7"}),
    DatasetSpec(
        "disprot", "DisProt", (116,), "https://disprot.org/", "CC BY 4.0",
        (FileSpec(_DISPROT + "tsv", "disprot_2026_06_regions.tsv", "regions",
                  fmt="rnareg_tab",
                  note="Release 2026_06 regions with ambiguous evidence, without obsolete "
                       "ones: one row per region and term (an API export, about 4.7 MB)"),
         FileSpec(_DISPROT + "json", "disprot_2026_06.json", "entries",
                  fmt="disprot_entries", note="Release 2026_06 entries (about 28 MB)"),
         FileSpec(_DISPROT + "json", "disprot_2026_06.json", "partners",
                  fmt="disprot_partners",
                  note="Binding partners of the regions, only in the JSON")),
        version="2026_06",
        notes="Manual curation from the literature. Each region carries an IDPO or GO "
              "term, an ECO evidence code and a paper: 'known'. Regions DisProt flags "
              "as ambiguous evidence are 'inconclusive'. Binding partners of a region "
              "(UniProt accessions) are protein_interaction rows.",
        relations=("protein_region", "protein_interaction"),
        commercial_use="allowed",
        crosswalk={"gene": "SELECT DISTINCT 'uniprot:' || acc, 'symbol:' || gene FROM "
                           "entries WHERE ncbi_taxon_id = '9606' AND gene IS NOT NULL"}),
    DatasetSpec(
        "rnacentral", "RNAcentral id mappings", (117,), "https://rnacentral.org/",
        "CC0 1.0 (release 20 onwards)",
        (FileSpec(_RNAC + "release_notes.txt", "release_notes.txt", "", fmt="raw"),
         *(FileSpec(_RNAC + f"id_mapping/database_mappings/{db}.tsv", f"{db}.tsv", db,
                    fmt="rnareg_tab", columns=_RNAC_COLS)
           for db in ("mirbase", "hgnc", "tarbase", "lncbase", "intact")),
         FileSpec(_RNAC + "id_mapping/database_mappings/ensembl_gencode.tsv",
                  "ensembl_gencode.tsv", "ensembl_gencode", fmt="rnareg_tab",
                  columns=_RNAC_COLS, optional=True),
         FileSpec(_RNAC + "id_mapping/id_mapping.tsv.gz", "id_mapping.tsv.gz", "",
                  fmt="raw", optional=True, expected_bytes=2660438847,
                  note="All expert databases (2.66 GB); not fetched by default"),
         FileSpec(_RNAC + "go_annotations/rnacentral_rfam_annotations.tsv.gz",
                  "rnacentral_rfam_annotations.tsv.gz", "", fmt="raw", optional=True,
                  expected_bytes=176932399,
                  note="GO terms inferred from Rfam family membership (computational)")),
        version="27 (2026-07-20)",
        notes="Id mappings only: RNAcentral URS ids to miRBase (MI/MIMAT accessions and "
              "names), HGNC, TarBase, LncBase and IntAct, with taxon and RNA type. The "
              "files hold no target or interaction pairs: those are served per URS by "
              "the 'rnacentral' connector. Release 27 is pinned (releases/27.0).",
        relations=(), commercial_use="allowed",
        upstream=("miRBase", "HGNC", "TarBase", "LncBase", "IntAct")),
    DatasetSpec(
        "mirtarbase", "miRTarBase", (118,), "https://awi.cuhk.edu.cn/miRTarBase/",
        _MTB_LICENCE,
        (FileSpec(_MTB + "LICENSE", "LICENSE", "", fmt="raw",
                  note="License terms for miRTarBase downloads (705 bytes)"),
         FileSpec(_MTB + "10.0/miRTarBase_SE_WR.csv", "miRTarBase_SE_WR.csv", "mti_strong",
                  fmt="csv",
                  note="MTIs supported by strong evidence: reporter assay or western blot "
                       "(3.3 MB)"),
         FileSpec(_MTB + "10.0/miRTarBase_MTI.csv", "miRTarBase_MTI.csv", "mti", fmt="csv",
                  optional=True, expected_bytes=393095667,
                  note="All MTIs, every evidence class (393 MB)"),
         FileSpec(_MTB + "10.0/hsa_MTI.csv", "hsa_MTI.csv", "mti_hsa", fmt="csv",
                  optional=True, expected_bytes=337103345,
                  note="Human MTIs, every evidence class (337 MB)"),
         FileSpec(_MTB + "10.0/MicroRNA_Target_Sites.csv", "MicroRNA_Target_Sites.csv", "",
                  fmt="raw", optional=True,
                  note="Curated target sites (not verified: HTTP 429 on 2026-10-01)")),
        version="10.0 files (the downloads page is labelled Release 11.0)",
        notes="Literature-curated miRNA-target interactions: 'known', with the "
              "experiments in context.method and miRTarBase's support type (Functional "
              "MTI, Functional MTI (Weak), ...) in context.flags; a weak class is never "
              "upgraded. 'Non-Functional' support types (an experiment found no "
              "regulation) are outcome 'negative'. The host answers HTTP 429 after a few "
              "requests: download each file once, at most one request every few minutes.",
        relations=("mirna_target",), commercial_use="unknown"),
    DatasetSpec(
        "chip_atlas", "ChIP-Atlas", (119,), "https://chip-atlas.org/", _CHIP_LICENCE,
        (FileSpec(_CHIP + "metadata/analysisList.tab", "analysisList.tab", "analysis",
                  fmt="rnareg_tab",
                  columns=("antigen", "colocalization_cell_classes", "target_genes",
                           "genome"),
                  note="Antigens with Colocalization and Target Genes results, per genome"),
         FileSpec(_CHIP + "metadata/analysisList.tab", "analysisList.tab", "target_genes",
                  fmt="chip_atlas_targets",
                  note="Rows of the Target Genes tables fetched per TF into "
                       "raw/chip_atlas/target_genes/ by fetch_target_genes"),
         FileSpec(_CHIP + "metadata/antigenList.tab", "antigenList.tab", "antigens",
                  fmt="rnareg_tab",
                  note="Genome, antigen class, antigen, number and ids of experiments"),
         FileSpec(_CHIP + "metadata/celltypeList.tab", "celltypeList.tab", "cell_types",
                  fmt="rnareg_tab",
                  note="Genome, cell type class, cell type, number and ids of experiments"),
         FileSpec(_CHIP + "metadata/experimentList.tab", "experimentList.tab", "experiments",
                  fmt="rnareg_tab", optional=True, expected_bytes=359471390,
                  columns=("experiment", "genome", "antigen_class", "antigen",
                           "cell_type_class", "cell_type", "cell_type_description",
                           "processing_log", "title", "metadata"),
                  note="Every experiment with its curated antigen and cell type (359 MB)")),
        version="metadata of 2026-09-28",
        notes="Target Genes: genes whose TSS a TF's ChIP-seq peaks (MACS2 q<1e-05) "
              "overlap within ±1, 5 or 10 kb, pooled over all public experiments of the "
              "TF; ChIP-Atlas calls them predicted direct targets. Rows are tf_target, "
              "'predicted', effect 'binding', score = the mean MACS2 score, with the "
              "number of experiments that bound the gene. Tables are fetched per TF on "
              "request (fetch_target_genes), never in bulk. Experiments come from SRA "
              "(GEO, ENCODE) and overlap ReMap's: compare them by SRX/GSE accession.",
        relations=("tf_target",), commercial_use="allowed"),
    DatasetSpec(
        "remap", "ReMap 2022", (120,), "https://remap.univ-amu.fr/",
        "CC BY-NC 4.0 (ReMap catalogues; ReMapEnrich and the pipeline are GPLv3)",
        (FileSpec(_REMAP + "download_by_target.json", "download_by_target.json",
                  "datasets", fmt="remap_datasets",
                  note="Per-regulator dataset index (DataTables JSON of HTML links)"),
         FileSpec(_REMAP_STORE + "biotypes/remap2022_hsap_biotypes.xlsx",
                  "remap2022_hsap_biotypes.xlsx", "biotypes_hsap", fmt="xlsx",
                  note="Human cell line and tissue (biotype) metadata"),
         FileSpec(_REMAP_STORE + "hg38/MACS2/remap2022_crm_macs2_hg38_v1_0.bed.gz",
                  "remap2022_crm_macs2_hg38_v1_0.bed.gz", "", fmt="raw", optional=True,
                  expected_bytes=199805648,
                  note="Cis-regulatory modules, hg38 (200 MB): the analysis layer"),
         FileSpec(_REMAP_STORE + "hg38/MACS2/remap2022_nr_macs2_hg38_v1_0.bed.gz",
                  "remap2022_nr_macs2_hg38_v1_0.bed.gz", "", fmt="raw", optional=True,
                  expected_bytes=1456682065,
                  note="Non-redundant peaks, hg38 (1.46 GB); fetch only deliberately")),
        version="2022",
        notes="Metadata only: which regulator was profiled in which cell type by which "
              "GEO/ENCODE series, with the per-regulator BED URLs. Peaks stay BED files "
              "(optional). ReMap and ChIP-Atlas reprocess the same public experiments, "
              "so compare them by series accession before counting binding evidence "
              "twice. Live dataset lookups: connector 'remap'.",
        relations=(), commercial_use="forbidden"),
)


# ------------------------------------------------------------------------- extractors
_UNIPROT = re.compile(r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})"
                      r"(?:-\d+)?")


def _protein_id(acc: Any) -> str | None:
    acc = v(acc)
    if not acc:
        return None
    if _UNIPROT.fullmatch(acc):
        return f"uniprot:{acc}"
    if acc.startswith("PR:"):
        return f"pro:{acc[3:]}"
    if acc.startswith("GO:"):                     # a complex named by its GO term
        return f"go:{acc[3:]}"
    return f"iptmnet:{acc}"


_CONDITION = frozenset({"in vivo", "in vitro", "in vitro;in vivo", "in vivo;in vitro"})


def _iptm_note(code: str, note: Any) -> dict[str, str]:
    """The ``note`` column means something different per source.

    HPRD and dbSNO: in vivo / in vitro (a condition). IEDB and IntAct: the record id in
    that database. PomBase: the substrate's systematic id. PhosPhAt writes a number the
    readme does not explain, kept as written in ``flags``. "-" is no value.
    """
    text = v(note)
    if not text:
        return {}
    if text.lower() in _CONDITION:
        return {"condition": text}
    if re.fullmatch(r"[a-z]+:\S+", text) or code == "pomb":
        return {"source_id": text}
    return {"flags": text}


def _pmids(text: Any) -> str | None:
    ids = [p.strip() for p in re.split(r"[,;|\s]+", v(text) or "") if p.strip().isdigit()]
    return names(*(f"pmid:{p}" for p in ids))


def _iptmnet(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "ptm"):
        return
    if has(conn, "score"):
        conn.execute("CREATE INDEX IF NOT EXISTS score_site ON score(substrate_ac, site)")
        sql = ("SELECT p.*, s.score AS _score FROM ptm p LEFT JOIN score s "
               "ON s.substrate_ac = p.substrate_ac AND s.site = p.site "
               "AND coalesce(s.enzyme_ac, '') = coalesce(p.enzyme_ac, '') "
               "AND upper(s.ptm_type) = upper(p.ptm_type)")
    else:
        sql = "SELECT p.*, NULL AS _score FROM ptm p"
    for r in rows(conn, sql):
        code = (v(r["source"]) or "").lower()
        if not code:
            continue
        upstream = IPTMNET_SOURCES.get(code, code.upper())
        evidence = "mentioned" if code in _TEXT_MINED else "aggregated"
        ptm = (v(r["ptm_type"]) or "").lower() or None
        site = v(r["site"])
        substrate = _protein_id(r["substrate_ac"])
        context = ctx(species=r["organism"], residue=site, mechanism=ptm, source_db=code,
                      **_iptm_note(code, r["note"]))
        common = dict(score=r["_score"], reference=_pmids(r["pmid"]),
                      note=f"via {upstream}", context=context)
        enzyme = _protein_id(r["enzyme_ac"])
        if enzyme:
            yield rel("ptm_site", "iptmnet", enzyme, r["enzyme_gene"], substrate,
                      r["substrate_gene"], evidence, **common)
        elif substrate and site:
            yield rel("protein_site", "iptmnet", substrate, r["substrate_gene"],
                      f"{substrate}/{site}",
                      " ".join(x for x in (v(r["substrate_gene"]) or v(r["substrate_ac"]),
                                           site, ptm) if x), evidence, **common)


def _term_id(term: Any) -> str | None:
    term = v(term)
    if not term or ":" not in term:
        return None
    prefix, local = term.split(":", 1)
    return f"{prefix.lower()}:{local}"


def _reference(text: Any) -> str | None:
    """DisProt writes ``pmid:123``; other sources (DOI) are kept as written."""
    text = v(text)
    if not text:
        return None
    return text if ":" in text else (f"pmid:{text}" if text.isdigit() else text)


def _ambiguous(value: Any) -> bool:
    text = (v(value) or "").lower()
    return bool(text) and text not in ("false", "0", "no")


def _disprot(conn: sqlite3.Connection) -> Iterator[Row | None]:
    gene: dict[str, str | None] = {}
    if has(conn, "entries"):
        gene = {r["acc"]: names(r["gene"], r["name"]) for r in rows(
            conn, "SELECT acc, gene, name FROM entries")}
    if has(conn, "regions"):
        for r in rows(conn, "SELECT * FROM regions"):
            if _ambiguous(r["obsolete"]):
                continue                      # an obsolete annotation is not a claim
            acc = v(r["acc"])
            yield rel("protein_region", "disprot", f"uniprot:{acc}" if acc else None,
                      gene.get(acc) or r["name"], _term_id(r["term"]), r["term_name"],
                      "known", reference=_reference(r["reference"]),
                      note=r["term_namespace"],
                      outcome="inconclusive" if _ambiguous(r["confidence"]) else "positive",
                      context=ctx(species=r["ncbi_taxon_id"],
                                  residue=f"{v(r['start'])}-{v(r['end'])}",
                                  method=r["ec_name"], assay=r["ec"],
                                  flags=r["confidence"], source_id=r["region_id"]))
    if has(conn, "partners"):
        for r in rows(conn, "SELECT * FROM partners"):
            if (v(r["partner_db"]) or "").lower() != "uniprot":
                continue                      # nucleic acids, small molecules: table only
            acc = v(r["acc"])
            partner = v(r["partner_id"])
            ref = (f"pmid:{v(r['reference_id'])}" if (v(r["reference_source"]) or "").lower()
                   == "pmid" and v(r["reference_id"]) else _reference(r["reference_id"]))
            binding = (v(r["term_is_binding"]) or "").lower() in ("true", "1")
            yield rel("protein_interaction", "disprot", f"uniprot:{acc}" if acc else None,
                      gene.get(acc), f"uniprot:{partner}" if partner else None, None,
                      "known", reference=ref, effect="binding" if binding else None,
                      note=r["term_name"],
                      outcome="inconclusive" if _ambiguous(r["confidence"]) else "positive",
                      context=ctx(residue=f"{v(r['start'])}-{v(r['end'])}",
                                  method=r["ec_name"], assay=r["ec_id"],
                                  flags=r["confidence"], source_id=r["region_id"],
                                  sample=_partner_range(r)))


def _partner_range(r: sqlite3.Row) -> str | None:
    start, end = v(r["partner_start"]), v(r["partner_end"])
    return f"partner residues {start}-{end}" if start and end else None


def _mirtarbase(conn: sqlite3.Connection) -> Iterator[Row | None]:
    """The files repeat a record verbatim (same MTI, experiments, support and paper), and
    the strong-evidence file is a subset of the full ones: each record is emitted once."""
    seen: set[tuple[Any, ...]] = set()
    for table in ("mti_strong", "mti", "mti_hsa"):
        if not has(conn, table):
            continue
        for r in rows(conn, f'SELECT * FROM "{table}"'):
            key = tuple(v(x) for x in r)
            if key in seen:
                continue
            seen.add(key)
            mirna = v(col(r, "miRNA"))
            symbol = v(col(r, "Target_Gene"))
            entrez = v(col(r, "Target_Gene_Entrez_ID", "Target_Gene_Entrez_Gene_ID"))
            entrez = entrez.split(".")[0] if entrez and re.fullmatch(r"\d+(\.0)?", entrez) \
                else None
            support = v(col(r, "Support_Type"))
            pmid = v(col(r, "References_PMID", "References"))
            ref = f"pmid:{pmid.split('.')[0]}" if pmid and re.fullmatch(r"\d+(\.0)?", pmid) \
                else pmid
            mti = v(col(r, "miRTarBase_ID"))
            if entrez in (None, "0"):
                if mirna:
                    yield unresolved("mirna_target", "mirtarbase", f"mirbase:{mirna}", mirna,
                                     symbol, "no Entrez gene id", reference=ref, note=mti)
                continue
            methods = names(*re.split(r"//|;", v(col(r, "Experiments")) or ""))
            low = (support or "").lower()
            yield rel("mirna_target", "mirtarbase", f"mirbase:{mirna}" if mirna else None,
                      mirna, f"ncbigene:{entrez}", symbol, "known", reference=ref,
                      note=support,
                      outcome="negative" if low.startswith("non-functional") else "positive",
                      context=ctx(method=methods,
                                  species=names(col(r, "Species_miRNA"),
                                                col(r, "Species_Target_Gene")),
                                  flags=support, source_id=mti))


_HUMAN = ("hg38", "hg19")


def _chip_atlas(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "target_genes"):
        return
    for r in rows(conn, "SELECT * FROM target_genes"):
        bound = int(v(r["bound"]) or 0)
        if not bound:
            continue                  # no experiment has a peak: not a target by definition
        genome, tf, gene = v(r["genome"]), v(r["antigen"]), v(r["gene"])
        human = genome in _HUMAN

        def gid(sym: str | None) -> str | None:
            if not sym:
                return None
            return f"symbol:{sym}" if human else f"chip_atlas:{genome}.{sym}"
        distance = v(r["distance_kb"])
        yield rel("tf_target", "chip_atlas", gid(tf), tf, gid(gene), gene, "predicted",
                  score=r["average"], effect="binding",
                  note=f"{bound} of {v(r['experiments'])} experiments with a peak",
                  context=ctx(genome_build=genome, n=bound,
                              method=f"ChIP-seq peak (MACS2 q<1e-05) within ±{distance} kb "
                                     "of the TSS", action="binds near TSS",
                              dataset=r["file"]))


EXTRACTORS = {"iptmnet": _iptmnet, "disprot": _disprot, "mirtarbase": _mirtarbase,
              "chip_atlas": _chip_atlas}


# ------------------------------------------------------------- per-TF fetch (ChIP-Atlas)
def fetch_target_genes(hub: Any, antigens: Iterable[str], *, genome: str = "hg38",
                       distance: int = 5, refresh: bool = False, build: bool = True,
                       log=print) -> list[dict[str, Any]]:
    """Download ChIP-Atlas Target Genes tables of the named TFs, then rebuild.

    One table per TF (about 1-3 MB), from the documented URL
    ``https://chip-atlas.dbcls.jp/data/<genome>/target/<TF>.<distance>.tsv``. Only TFs
    the downloaded analysis list marks as having a Target Genes table (``+``) are
    requested, so no URL is guessed. Tables already present are reused unless
    ``refresh``. Requests are spaced one second apart.
    """
    from ..hub import HubError, _looks_like_html
    from ...acquisition.downloader import Downloader, DownloadError
    if distance not in (1, 5, 10):
        raise HubError("distance must be 1, 5 or 10 (kb from the TSS)")
    raw = hub.raw_dir("chip_atlas")
    listing = raw / "analysisList.tab"
    if not listing.exists():
        raise HubError("fetch('chip_atlas') first: the analysis list says which TFs have "
                       "Target Genes tables")
    listed: set[str] = set()
    with open_text(listing) as fh:
        for line in fh:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) >= 4 and parts[3].strip() == genome and parts[2].strip() == "+":
                listed.add(parts[0].strip())
    dl = Downloader(raw / TARGET_DIR, timeout_s=120, log=log)
    out: list[dict[str, Any]] = []
    for antigen in antigens:
        if antigen not in listed:
            out.append({"antigen": antigen, "ok": False,
                        "error": f"no Target Genes table for {antigen!r} in {genome}"})
            continue
        name = f"{genome}.{antigen}.{distance}.tsv"
        url = (f"{_CHIP}{genome}/target/"
               f"{urllib.parse.quote(antigen, safe='')}.{distance}.tsv")
        try:
            r = dl.fetch(url, name, force=refresh)
            if _looks_like_html(raw / TARGET_DIR / name):
                (raw / TARGET_DIR / name).unlink(missing_ok=True)
                raise HubError(f"{url} returned an HTML page, not the table")
            out.append({"antigen": antigen, "file": name, "bytes": r.bytes,
                        "cached": r.from_cache, "ok": True})
            if not r.from_cache:
                time.sleep(1.0)
        except (DownloadError, OSError, HubError) as exc:
            out.append({"antigen": antigen, "file": name, "ok": False,
                        "error": str(exc)[:300]})
        log(f"chip_atlas: {out[-1]}")
    if build and any(r.get("ok") for r in out):
        hub.build("chip_atlas", log=log)
    return out
