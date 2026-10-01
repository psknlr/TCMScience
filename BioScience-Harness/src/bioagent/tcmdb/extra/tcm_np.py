"""Natural products and plant names: COCONUT 2.0, the WFO Plant List and IMPPAT 3.0.

Each was checked from the harness on 2026-10-01 (review of 2026-09-30):

* **COCONUT 2.0** (``coconut``): the CC0 CSV of the monthly release (one zipped CSV, the
  only download that lists the organisms, source collections and DOIs of each compound).
  COCONUT collects other open natural-product databases, so its organism-compound pairs
  are ``aggregated`` and each row names the collections its compound came from
  (``via KNApSaCK; NPASS``). The release URL embeds the month; earlier months are not
  kept on the server.
* **WFO Plant List** (``wfo``): the CC0 taxonomic backbone (Darwin Core) of World Flora
  Online, June 2026 release on Zenodo, loaded as name tables for resolving plant names
  (accepted names, synonyms, WFO ids, IPNI LSIDs). It is a taxonomy of species, not a
  materia medica, and yields no relations.
* **IMPPAT 3.0** (``imppat3``): Indian medicinal plants, their phytochemicals (with
  InChIKeys), therapeutic uses, Ayurvedic formulations, and phytochemical targets. Under
  CC BY-NC-ND 4.0: non-commercial use, and tables derived from it are not to be
  redistributed.

KNApSAcK has no bulk data (its 2008 download is a Java client) and is a live connector
only (``providers.supplement.tcm_np``).
"""

from __future__ import annotations

import csv
import gzip
import io
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, col, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import open_text

__all__ = ["DATASETS", "EXTRACTORS", "READERS"]

_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


def _cell(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().strip("\r")
    return text or None


# ------------------------------------------------------------------------------ readers
def _text(path: Path) -> io.TextIOBase:
    """A text handle, deciding gzip by the file's first bytes rather than its name.

    Zenodo serves WFO's ``.csv.gz`` files with ``Content-Encoding: gzip``: a client that
    decodes content encodings saves plain CSV under the ``.gz`` name.
    """
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig",
                                errors="replace", newline="")
    if path.suffix == ".gz":
        return open(path, encoding="utf-8-sig", errors="replace", newline="")
    return open_text(path)


#: The DwC backbone's columns kept in the ``name`` table. The others (localID,
#: subfamily, tribe, subtribe, subgenus, verbatimTaxonRank, originalNameUsageID,
#: nameAccordingToID, taxonRemarks, created, references) stay in the raw file: they
#: hold The Plant List seed notes as HTML and page URLs, about 60% of its 950 MB.
WFO_NAME_COLUMNS = ("taxonID", "scientificNameID", "scientificName", "scientificNameAuthorship",
                    "taxonRank", "taxonomicStatus", "acceptedNameUsageID", "parentNameUsageID",
                    "nomenclaturalStatus", "namePublishedIn", "family", "genus",
                    "specificEpithet", "infraspecificEpithet", "majorGroup", "source",
                    "modified", "tplID")


def read_wfo_dwc(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """WFO's ``classification.csv``: tab-separated despite its name, with fields in
    double quotes when they hold spaces or punctuation. Keeps ``WFO_NAME_COLUMNS``."""
    with _text(Path(path)) as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"')
        header = [h.strip() for h in next(reader)]
        missing = [c for c in WFO_NAME_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"{Path(path).name}: no columns {missing}; header {header}")
        idx = [header.index(c) for c in WFO_NAME_COLUMNS]
        yield list(WFO_NAME_COLUMNS)
        for row in reader:
            if not row or not any(c.strip() for c in row):
                continue
            yield [_cell(row[i]) if i < len(row) else None for i in idx]


def read_csv_named(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A comma-separated file, plain or gzip (sniffed). ``spec.columns``, when given,
    replaces the file's own header line: WFO's deduplicated-ids file names five columns
    over rows of six (the second field, the replacement id, has no name)."""
    with _text(Path(path)) as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return
        yield list(spec.columns) if spec.columns else [h.strip() for h in header]
        for row in reader:
            if row and any(c.strip() for c in row):
                yield [_cell(c) for c in row]


READERS = {"wfo_dwc": read_wfo_dwc, "csv_named": read_csv_named}


# ----------------------------------------------------------------------------- COCONUT
_COCONUT_S3 = "https://coconut.s3.uni-jena.de/prod/downloads/2026-10/"
#: stored on every row, so kept short: the download page says CC0, and the terms of
#: service keep "those [restrictions] provided by the original data owners"
_COCONUT_LICENSE = "CC0 (COCONUT); each collection's owner terms kept"

COCONUT = DatasetSpec(
    "coconut", "COCONUT 2.0 (COlleCtion of Open Natural prodUcTs)", (67,),
    "https://coconut.naturalproducts.net", _COCONUT_LICENSE,
    (FileSpec(_COCONUT_S3 + "coconut_csv-10-2026.zip", "coconut_csv-10-2026.zip", "molecule",
              fmt="csv", expected_bytes=247_959_854, license="CC0",
              note="one CSV of 700 MB: identifier (CNP), structure, names, NP-likeness, "
                   "ClassyFire and NPClassifier classes, and '|'-separated organisms, "
                   "collections, DOIs, synonyms and CAS numbers"),
     FileSpec(_COCONUT_S3 + "coconut_sdf_2d_lite-10-2026.zip",
              "coconut_sdf_2d_lite-10-2026.zip", "", fmt="raw", optional=True,
              expected_bytes=315_987_333, license="CC0",
              note="2D structures (SDF), kept as a file"),
     FileSpec(_COCONUT_S3 + "coconut_sdf_3d-10-2026.zip", "coconut_sdf_3d-10-2026.zip", "",
              fmt="raw", optional=True, expected_bytes=375_152_147, license="CC0",
              note="RDKit 3D coordinates, not minimised (SDF, zip64), kept as a file")),
    version="2026-10 (monthly release; files of 2026-10-01)",
    notes="Open natural products aggregated from 71 collections (Super Natural, NPASS, UNPD, "
          "KNApSaCK, ChEBI NPs, Wikidata, CMNPD, NPAtlas, FooDB, DrugBankNP, TCM "
          "collections, vendor catalogues ...). organism_compound rows pair each organism "
          "of a compound with its InChIKey (inchikey:..., else coconut:<CNP id>); records "
          "of one structure (one standard InChIKey under several CNP ids) are merged, and "
          "their CNP ids are the row's context source_id. The evidence is aggregated, and "
          "the note names every collection of the records that gave the pair ('via "
          "KNApSaCK; NPASS; UNPD', short names): COCONUT does not say which collection "
          "reported which organism, and its DOIs are not paired with organisms either, so "
          "no row carries a reference. Organisms are free "
          "text, as COCONUT writes them (coconut:organism.<name>). COCONUT's CC0 keeps the "
          "original owners' restrictions, so a row whose compound also came from a "
          "collection under non-commercial terms (KNApSaCK, DrugBankNP) carries those "
          "terms as its licence. The release URL embeds the month (2026-10) and the "
          "server keeps only the current month.",
    relations=("organism_compound",),
    commercial_use="allowed",
    # its largest collections and the TCM ones (organism-bearing compounds, 2026-10 CSV)
    upstream=("Wikidata", "Super Natural", "NPASS", "KNApSAcK", "CMAUP", "NPAtlas", "CMNPD",
              "ZINC", "UNPD", "TCMDB@Taiwan", "NPEdia", "ChEBI", "TIPdb", "StreptomeDB",
              "FooDB", "VietHerb", "TCMID", "GNPS", "DrugBank", "HIT", "HIM"))


def _split(value: object, sep: str = "|") -> list[str]:
    out: list[str] = []
    for part in (v(value) or "").split(sep):
        part = " ".join(part.split())
        if part and v(part) and part not in out:
            out.append(part)
    return out


#: Collections whose own terms forbid commercial use, as this review found them. COCONUT
#: releases its data under CC0 but keeps "the restrictions of the original data owners",
#: and it does not say which collection reported an organism: a row whose compound came
#: from one of these may rest on it, so the row carries that collection's terms.
NON_COMMERCIAL_COLLECTIONS = {
    "KNApSaCK": "KNApSAcK CC BY-NC-ND 4.0",
    "DrugBankNP": "DrugBank CC BY-NC 4.0",
}


def _collection(title: str) -> str:
    """A collection's short name: 'UNPD (Universal Natural Products Database)' -> 'UNPD'."""
    return re.sub(r"\s*\(.*\)\s*$", "", title).strip() or title


def _coconut_licence(collections: list[str]) -> str | None:
    terms = [NON_COMMERCIAL_COLLECTIONS[c] for c in collections
             if c in NON_COMMERCIAL_COLLECTIONS]
    if not terms:
        return None                 # the dataset's licence (build_relations fills it)
    return f"CC0 (COCONUT); collected from non-commercial sources ({'; '.join(terms)})"


def _coconut(conn: sqlite3.Connection) -> Iterator[Row | None]:
    """One row per organism and compound.

    COCONUT keeps several records (CNP ids) for some structures; records with one
    standard InChIKey are one compound, so their organisms are merged: a pair two records
    give is one row, whose context lists both CNP ids and whose note names the
    collections of the records that gave it.
    """
    if not has(conn, "molecule"):
        return

    def compound_of(r: sqlite3.Row) -> str | None:
        ik, cnp = v(r["standard_inchi_key"]), v(r["identifier"])
        return f"inchikey:{ik}" if ik and _INCHIKEY.match(ik) else \
            (f"coconut:{cnp}" if cnp else None)

    def emit(compound: str, group: list[sqlite3.Row]) -> Iterator[Row | None]:
        pairs: dict[str, tuple[list[str], list[str]]] = {}
        for r in group:
            cnp = v(r["identifier"])
            collections = [_collection(c) for c in _split(r["collections"])]
            for organism in _split(r["organisms"]):
                cnps, colls = pairs.setdefault(organism, ([], []))
                if cnp and cnp not in cnps:
                    cnps.append(cnp)
                colls.extend(c for c in collections if c not in colls)
        name = names(*(r["name"] for r in group))
        for organism, (cnps, colls) in pairs.items():
            yield rel("organism_compound", "coconut", f"coconut:organism.{organism}",
                      organism, compound, name, "aggregated",
                      note=f"via {'; '.join(colls)}" if colls else None,
                      context=ctx(source_id=" | ".join(cnps) or None),
                      license=_coconut_licence(colls))

    group: list[sqlite3.Row] = []
    current: str | None = None
    # ordered by the compound id, so the records of one structure arrive together
    for r in rows(conn, "SELECT identifier, standard_inchi_key, name, organisms, collections "
                        "FROM molecule WHERE organisms IS NOT NULL "
                        "ORDER BY standard_inchi_key, identifier"):
        compound = compound_of(r)
        if compound is None:
            continue
        if compound != current and group:
            yield from emit(current, group)     # type: ignore[arg-type]
            group = []
        current = compound
        group.append(r)
    if group and current:
        yield from emit(current, group)


# --------------------------------------------------------------------------------- WFO
_ZENODO = "https://zenodo.org/api/records/20782718/files/"

WFO = DatasetSpec(
    "wfo", "World Flora Online Plant List (June 2026)", (68,),
    "https://www.worldfloraonline.org/", "CC0 1.0 (Zenodo record 20782718)",
    (FileSpec(_ZENODO + "_DwC_backbone_R.zip/content", "_DwC_backbone_R.zip", "name",
              fmt="wfo_dwc", expected_bytes=121_660_019,
              note="classification.csv (tab-separated, 950 MB): one row per name with its "
                   "WFO id, rank, taxonomic status (Accepted, Synonym, Unchecked, ...), "
                   "accepted name id, parent id and IPNI LSID; the name table keeps 18 of "
                   "its 29 columns (tcmdb.extra.tcm_np.WFO_NAME_COLUMNS), the rest stay in "
                   "the file"),
     FileSpec(_ZENODO + "deprecated_names_lookup.csv.gz/content",
              "deprecated_names_lookup.csv.gz", "deprecated_name", fmt="csv_named",
              expected_bytes=707_640, note="WFO ids withdrawn from the classification"),
     FileSpec(_ZENODO + "deduplicated_ids_lookup.csv.gz/content",
              "deduplicated_ids_lookup.csv.gz", "deduplicated_id", fmt="csv_named",
              expected_bytes=429_458,
              columns=("wfo_id", "replacement_wfo_id", "name_canonical", "authors_string",
                       "rank", "nomenclatural_status"),
              note="duplicate WFO ids and the id that replaces each; the file's header "
                   "names five columns over six-field rows, so the header is given here"),
     FileSpec(_ZENODO + "ipni_to_wfo.csv.gz/content", "ipni_to_wfo.csv.gz", "ipni_to_wfo",
              fmt="csv_named", optional=True, expected_bytes=12_372_196,
              note="IPNI LSID -> WFO id; the name table already holds each name's IPNI "
                   "LSID (scientificNameID)"),
     FileSpec(_ZENODO + "wfo_plantlist_2026-06.zip/content", "wfo_plantlist_2026-06.zip", "",
              fmt="raw", optional=True, expected_bytes=132_643_291,
              note="the same release as a Catalogue of Life Data Package (with references), "
                   "kept as a file")),
    version="2026-06 (published 2026-06-21; a release each June and December)",
    notes="The taxonomic backbone of World Flora Online: every vascular plant and bryophyte "
          "name with its WFO id, rank, status and accepted name, for resolving the plant "
          "names other sources give (COCONUT and IMPPAT organisms, TCM herb sources). It is "
          "a taxonomy of species, not a materia medica: a crude drug (Astragali Radix) is "
          "a part of one or more species, and the backbone says nothing about medicinal "
          "use. Names come from IPNI and Tropicos, taxonomy from the WFO Taxonomic Expert "
          "Networks and WCVP, seeded from The Plant List 1.1. No relations: the tables are "
          "for look-up (hub.query('wfo', 'name', where={'scientificName': ...})). Zenodo's "
          "robots.txt asks for 10 s between requests.",
    commercial_use="allowed",
    upstream=("IPNI", "Tropicos", "WCVP", "The Plant List"))


# ------------------------------------------------------------------------------ IMPPAT
_IMPPAT = "https://cb.imsc.res.in/imppat/images/Batch_Download/"
_IMPPAT_LICENSE = "CC BY-NC-ND 4.0 (site footer)"

IMPPAT = DatasetSpec(
    "imppat3", "IMPPAT 3.0 (Indian Medicinal Plants, Phytochemistry And Therapeutics)", (69,),
    "https://cb.imsc.res.in/imppat/", _IMPPAT_LICENSE,
    tuple(FileSpec(_IMPPAT + name, name, table, expected_bytes=size)
          for name, table, size in (
              ("Plant_Information_IMPPAT.tsv", "plant", 541_093),
              ("Chemical_Information_IMPPAT_Phytochemicals.tsv", "phytochemical", 9_901_357),
              ("IMPPAT_Phytochemical_Plant_Association.tsv", "plant_phytochemical",
               14_724_808),
              ("IMPPAT_TherapeuticUse_Plant_Association.tsv", "plant_therapeutic_use",
               7_356_547),
              ("Target_IMPPAT_Phytochemicals.tsv", "target", 2_238_310),
              ("Bioactivity_IMPPAT_Phytochemicals.tsv", "bioactivity", 2_864_778),
              ("IMPPAT_SingleHerbalFormulations.tsv", "single_herbal_formulation", 303_826),
              ("IMPPAT_PolyHerbalFormulations.tsv", "polyherbal_formulation", 4_929_420)))
    + tuple(FileSpec(_IMPPAT + name, name, "", fmt="raw", optional=True, expected_bytes=size,
                     note="phytochemical structures (SDF), kept as a file")
            for name, size in (("2D_Structures_IMPPAT_Phytochemicals.sdf", 90_993_424),
                               ("3D_Structures_IMPPAT_Phytochemicals.sdf", 88_399_154))),
    version="3.0 (released 2026-09-30)",
    notes="4,154 Indian medicinal plants with their phytochemicals (plant part as context), "
          "therapeutic uses, and single- and poly-herbal formulations of the Ayurvedic "
          "Pharmacopoeia and Formulary of India; phytochemicals carry InChIKeys and "
          "PubChem, ChEMBL and ChEBI ids. Evidence: plant->phytochemical "
          "(organism_compound), plant->therapeutic use (herb_disease) and formula->plant "
          "(formula_herb, with the ISBN as reference) are listed, digitised from books and "
          "articles without a per-row citation. compound_target rows from the target file "
          "are aggregated (human genes 'via ChEMBL; NPASS; BindingDB' as the file says); "
          "rows from the bioactivity file are known (an EC50/IC50/Kd/Ki measured upstream, "
          "kept as written in context, 'via NPASS' or 'via BindingDB'); a bioactivity "
          "whose target has no UniProt accession (an organism, a cell line, an assay "
          "readout) waits in the unresolved queue. Formulation uses are kept in the tables "
          "only. CC BY-NC-ND 4.0: non-commercial use only, and the derived tables are not "
          "to be redistributed.",
    relations=("organism_compound", "herb_disease", "formula_herb", "compound_target"),
    commercial_use="forbidden",
    upstream=("ChEMBL", "NPASS", "BindingDB"))


def _compound_ids(conn: sqlite3.Connection) -> dict[str, tuple[str, str | None]]:
    out: dict[str, tuple[str, str | None]] = {}
    if not has(conn, "phytochemical"):
        return out
    for r in rows(conn, "SELECT * FROM phytochemical"):
        pid = v(col(r, "IMPPAT_Phytochemical_identifier"))
        if not pid:
            continue
        ik = v(col(r, "InChIKey"))
        cid = v(col(r, "Pubchem_CID")) or ""
        cid = cid[4:] if cid.upper().startswith("CID_") else cid
        cid_ok = cid.isdigit()
        out[pid] = (f"inchikey:{ik}" if ik and _INCHIKEY.match(ik)
                    else f"pubchem:{cid}" if cid_ok else f"imppat:{pid}",
                    names(col(r, "Phytochemical_name_standardised")))
    return out


def _via(value: object) -> str | None:
    sources = [s.strip() for s in re.split(r"\|", v(value) or "") if s.strip()]
    return f"via {'; '.join(sources)}" if sources else None


def _isbn(value: object) -> str | None:
    refs = []
    for part in re.split(r"[|;,]", v(value) or ""):
        part = part.strip()
        if part.upper().startswith("ISBN:"):
            part = "isbn:" + part[5:].strip()
        if part:
            refs.append(part)
    return " | ".join(refs) or None


_MEASURES = ("EC50", "IC50", "Kd", "Ki")


def _measure_note(measure: str, value: str, source: object) -> str:
    via = _via(source)
    return f"{measure} {value}" + (f"; {via}" if via else "")


def _imppat(conn: sqlite3.Connection) -> Iterator[Row | None]:
    compound = _compound_ids(conn)

    def chem(pid: object) -> tuple[str | None, str | None]:
        pid = v(pid)
        if not pid:
            return None, None
        return compound.get(pid, (f"imppat:{pid}", None))

    plant_by_name: dict[str, str] = {}
    if has(conn, "plant"):
        for r in rows(conn, "SELECT * FROM plant"):
            pid, name = v(col(r, "Plant_identifier")), v(col(r, "Indian_Medicinal_plant"))
            if pid and name:
                plant_by_name.setdefault(name.casefold(), pid)

    if has(conn, "plant_phytochemical"):
        for r in rows(conn, "SELECT * FROM plant_phytochemical"):
            oid, oname = chem(col(r, "IMPPAT_Phytochemical_identifier"))
            plant = v(col(r, "Plant_identifier"))
            yield rel("organism_compound", "imppat3", f"imppat:{plant}" if plant else None,
                      col(r, "Indian_Medicinal_plant"), oid, oname, "listed",
                      context=ctx(part=col(r, "Plant_part")))

    if has(conn, "plant_therapeutic_use"):
        for r in rows(conn, "SELECT * FROM plant_therapeutic_use"):
            plant = v(col(r, "IMPPAT_Plant_identifier", "Plant_identifier"))
            use = v(col(r, "IMPPAT_Therapeutic_use_identifier"))
            yield rel("herb_disease", "imppat3", f"imppat:{plant}" if plant else None,
                      col(r, "Indian_Medicinal_plant"), f"imppat:{use}" if use else None,
                      col(r, "Therapeutic_use"), "listed",
                      context=ctx(part=col(r, "Plant_part")))

    for table in ("single_herbal_formulation", "polyherbal_formulation"):
        if not has(conn, table):
            continue
        for r in rows(conn, f'SELECT * FROM "{table}"'):
            fid = v(col(r, "Formulation_identifier"))
            plant = v(col(r, "Plant_name_standardised"))
            ingredient = v(col(r, "Ingredient_name_standardised"))
            original = col(r, "Ingredient_name_in_API_original", "Ingredient_name_in_AFI_original")
            if plant and plant.casefold() in plant_by_name:
                oid = f"imppat:{plant_by_name[plant.casefold()]}"
            elif plant:
                oid = f"imppat:plant.{plant}"
            elif ingredient or v(original):
                # a non-plant ingredient (a mineral, water, honey): named, not a plant id
                oid = f"imppat:ingredient.{ingredient or v(original)}"
            else:
                oid = None
            yield rel("formula_herb", "imppat3", f"imppat:{fid}" if fid else None,
                      names(col(r, "Formulation_name_in_API_original",
                                "Formulation_name_in_AFI_original")),
                      oid, names(plant, ingredient, original), "listed",
                      reference=_isbn(col(r, "References")),
                      context=ctx(part=col(r, "Plant_part_standardised")))

    if has(conn, "target"):
        for r in rows(conn, "SELECT * FROM target"):
            sid, sname = chem(col(r, "IMPPAT_Phytochemical_identifier"))
            sym = v(col(r, "HGNC_Symbol"))
            ensg = v(col(r, "Gene_identifier"))
            entrez = v(col(r, "Entrez_gene_identifier"))
            tid = (f"symbol:{sym}" if sym else f"ensembl:{ensg}" if ensg
                   else f"ncbigene:{entrez}" if entrez and entrez.isdigit() else None)
            yield rel("compound_target", "imppat3", sid, sname, tid, names(sym, ensg),
                      "aggregated", note=_via(col(r, "Source")),
                      context=ctx(species="Homo sapiens"))

    if has(conn, "bioactivity"):
        for r in rows(conn, "SELECT * FROM bioactivity"):
            sid, sname = chem(col(r, "IMPPAT_Phytochemical_identifier"))
            acc = v(col(r, "UniProt_identifier"))
            target = col(r, "Target_name")
            for measure in _MEASURES:
                value = v(col(r, measure))
                if value is None:
                    continue
                if not acc:
                    yield unresolved("compound_target", "imppat3", sid, sname, target,
                                     "no UniProt accession: the target is an organism, a "
                                     "cell line or an assay readout",
                                     note=_measure_note(measure, value, col(r, "Source")))
                    continue
                yield rel("compound_target", "imppat3", sid, sname, f"uniprot:{acc}",
                          names(target), "known", note=_via(col(r, "Source")),
                          context=ctx(species=col(r, "Target_organism"), measure=measure,
                                      value=value))


DATASETS: tuple[DatasetSpec, ...] = (COCONUT, WFO, IMPPAT)
EXTRACTORS = {"coconut": _coconut, "imppat3": _imppat}
