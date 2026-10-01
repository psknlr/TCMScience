"""Reference spectra of compounds: MassBank, GNPS natural-product libraries and NP-MRD.

Each dataset yields ``compound_spectrum`` rows: a compound (by InChIKey when the source
gives one) has a reference spectrum (a MassBank record, a GNPS library spectrum, an NP-MRD
NMR peak list or shift assignment). The peaks themselves stay in the downloaded files,
which are the analysis layer: the store keeps one row per record with its metadata and a
pointer back to the file, and no peak becomes a relation.

How sure a source is that a spectrum belongs to the compound is read from the source's
own label, never guessed:

* **MassBank** records state a confidence in ``COMMENT: CONFIDENCE`` (free text, often a
  Schymanski level). A reference standard (level 1, "standard compound", "reference
  standard") or a record without a statement is ``known``; a level 2 identification
  ("probable", "confident structure") is ``reported``; a tentative one (level 3-5,
  "tentative", "candidate", "structure hypothesis") is ``reported`` with outcome
  ``inconclusive``; "Predicted" is ``predicted``. A deprecated record is ``inconclusive``
  and flagged. Each row carries the record's own licence (CC0 ... CC BY-NC-SA,
  dl-de/by-2-0), which binds that record.
* **GNPS** libraries grade each spectrum: gold (class 1: synthetic or fully characterised)
  is ``known``; silver (2: isolated or crude, with published data) is ``reported``; bronze
  (3: "any other putative" annotation) is ``reported`` and ``inconclusive``; challenge
  spectra (10: identity unknown) go to the unresolved queue. Propagated libraries are
  ``aggregated``. Imported libraries (MassBank, MoNA, ...) say ``via <upstream>`` and
  carry the upstream licence.
* **NP-MRD** deposition peak lists and experimental shift-assignment tables are
  ``known``. Its predicted NMR spectra are a separate archive that is kept apart and not
  loaded.

Unknown or not-applicable values (``N/A``, ``None``, ``null-null-null-null``) are stored
as nothing, and a record whose compound has no structure identifier goes to the
unresolved queue instead of under a shared id.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import open_text, safe_name

__all__ = ["DATASETS", "EXTRACTORS", "READERS"]

_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")


def _inchikey(value: Any) -> str | None:
    text = (v(value) or "").strip().strip('"')
    return text if _INCHIKEY.match(text) else None


def _tables(conn: sqlite3.Connection, prefix: str) -> list[str]:
    return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE "
                                             "type='table'") if r[0].startswith(prefix))


def _clean(value: Any) -> str | None:
    """A source value with its wrapping quotes removed, or None for an N/A spelling."""
    text = v(value)
    if text is None:
        return None
    text = text.strip().strip('"').strip()
    return v(text)


# =================================================================== MassBank
_MB_COLUMNS = (
    "accession", "deprecated", "record_title", "date", "authors", "license", "copyright",
    "publication", "project", "confidence", "comment", "ch_name", "ch_compound_class",
    "ch_formula", "ch_exact_mass", "ch_smiles", "ch_iupac", "inchikey", "pubchem", "chebi",
    "kegg", "cas", "chemspider", "comptox", "hmdb", "knapsack", "lipidmaps", "chembl",
    "chemont", "ch_link_other", "sp_scientific_name", "sp_lineage", "sp_taxid",
    "sp_link_other", "sp_sample", "instrument", "instrument_type", "ms_type", "ion_mode",
    "ionization", "collision_energy", "fragmentation_mode", "column_name",
    "retention_time", "precursor_mz", "precursor_type", "data_processing", "splash",
    "num_peak", "file")
_MB_TAGS = {"ACCESSION": "accession", "DEPRECATED": "deprecated",
            "RECORD_TITLE": "record_title", "DATE": "date", "AUTHORS": "authors",
            "LICENSE": "license", "COPYRIGHT": "copyright", "PUBLICATION": "publication",
            "PROJECT": "project", "CH$NAME": "ch_name",
            "CH$COMPOUND_CLASS": "ch_compound_class", "CH$FORMULA": "ch_formula",
            "CH$EXACT_MASS": "ch_exact_mass", "CH$SMILES": "ch_smiles",
            "CH$IUPAC": "ch_iupac", "SP$SCIENTIFIC_NAME": "sp_scientific_name",
            "SP$LINEAGE": "sp_lineage", "SP$SAMPLE": "sp_sample",
            "AC$INSTRUMENT": "instrument", "AC$INSTRUMENT_TYPE": "instrument_type",
            "MS$DATA_PROCESSING": "data_processing", "PK$SPLASH": "splash",
            "PK$NUM_PEAK": "num_peak"}
_MB_LINKS = {"INCHIKEY": "inchikey", "PUBCHEM": "pubchem", "CHEBI": "chebi",
             "KEGG": "kegg", "CAS": "cas", "CHEMSPIDER": "chemspider",
             "COMPTOX": "comptox", "HMDB": "hmdb", "KNAPSACK": "knapsack",
             "LIPIDMAPS": "lipidmaps", "CHEMBL": "chembl", "CHEMONT": "chemont"}
_MB_SUBTAGS = {("AC$MASS_SPECTROMETRY", "MS_TYPE"): "ms_type",
               ("AC$MASS_SPECTROMETRY", "ION_MODE"): "ion_mode",
               ("AC$MASS_SPECTROMETRY", "IONIZATION"): "ionization",
               ("AC$MASS_SPECTROMETRY", "COLLISION_ENERGY"): "collision_energy",
               ("AC$MASS_SPECTROMETRY", "FRAGMENTATION_MODE"): "fragmentation_mode",
               ("AC$CHROMATOGRAPHY", "COLUMN_NAME"): "column_name",
               ("AC$CHROMATOGRAPHY", "RETENTION_TIME"): "retention_time",
               ("MS$FOCUSED_ION", "PRECURSOR_M/Z"): "precursor_mz",
               ("MS$FOCUSED_ION", "PRECURSOR_TYPE"): "precursor_type"}


def parse_massbank_record(text: str) -> dict[str, str] | None:
    """The metadata of one MassBank record (``TAG: value`` lines), without its peaks.

    Repeated tags are joined with `` | ``. Parsing stops at ``PK$PEAK``; the indented
    lines of ``PK$ANNOTATION`` are skipped. Returns None for a file that is not a record.
    """
    if not text.lstrip("﻿").startswith("ACCESSION:"):
        return None
    out: dict[str, list[str]] = {}

    def put(col: str, value: str) -> None:
        value = value.strip()
        if value:
            out.setdefault(col, []).append(value)

    for line in text.lstrip("﻿").splitlines():
        if line.startswith("PK$PEAK") or line.startswith("//"):
            break
        if line.startswith(" ") or ":" not in line:
            continue
        tag, value = line.split(":", 1)
        value = value.strip()
        if tag in _MB_TAGS:
            put(_MB_TAGS[tag], value)
        elif tag == "COMMENT":
            if value.upper().startswith("CONFIDENCE"):
                put("confidence", value[len("CONFIDENCE"):])
            else:
                put("comment", value)
        elif tag == "CH$LINK":
            db, _, ident = value.partition(" ")
            col = _MB_LINKS.get(db.upper())
            put(col or "ch_link_other", ident if col else value)
        elif tag == "SP$LINK":
            db, _, ident = value.partition(" ")
            if db.upper() == "NCBI-TAXONOMY":
                put("sp_taxid", ident)
            else:
                put("sp_link_other", value)
        else:
            sub, _, rest = value.partition(" ")
            col = _MB_SUBTAGS.get((tag, sub))
            if col:
                put(col, rest)
    return {k: " | ".join(vals) for k, vals in out.items()}


def _read_massbank(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """MassBank's record archive: one row per record text file, in file-name order."""
    yield list(_MB_COLUMNS)
    with zipfile.ZipFile(path) as archive:
        for name in sorted(n for n in archive.namelist() if n.endswith(".txt")):
            record = parse_massbank_record(archive.read(name).decode("utf-8", "replace"))
            if record is None:
                continue
            record["file"] = name
            yield [record.get(c) for c in _MB_COLUMNS]


_TENTATIVE = re.compile(r"tentative|candidate|hypothesis|isomers possible|formula only|"
                        r"substance class|best match|unequivocal molecular formula|\btbd\b")
_LEVEL = re.compile(r"level\s*(\d)|^\s*:?\s*(\d)[a-c]?\b")


def massbank_confidence(text: Any) -> tuple[str, str]:
    """(evidence, outcome) for a MassBank ``COMMENT: CONFIDENCE`` statement."""
    t = (v(text) or "").lower()
    if not t:
        return "known", "positive"           # a reference record that states no level
    if "predicted" in t:
        return "predicted", "positive"
    if _TENTATIVE.search(t):
        return "reported", "inconclusive"
    m = _LEVEL.search(t)
    if m:
        level = int(m.group(1) or m.group(2))
        if level == 1:
            return "known", "positive"
        return ("reported", "positive") if level == 2 else ("reported", "inconclusive")
    if re.search(r"standard|reference|confirmed|authentic", t):
        return "known", "positive"
    # any other statement (a sample origin, a compound class): an identification the
    # record states without a level or a reference standard
    return "reported", "positive"


def _reference(publication: Any) -> str | None:
    text = v(publication) or ""
    m = re.search(r"PMID:?\s*(\d+)", text, re.I)
    if m:
        return f"pmid:{m.group(1)}"
    m = re.search(r"DOI:?\s*(10\.\S+?)[\]\s;,]*$", text, re.I) or \
        re.search(r"\b(10\.\d{4,9}/[^\s\]]+)", text)
    return f"doi:{m.group(1).rstrip('.')}" if m else None


#: MassBank contributors that are themselves another database's records.
_MB_UPSTREAM = {"RIKEN_RESPECT": "ReSpect", "CASMI_2012": "CASMI", "CASMI_2016": "CASMI"}


def _massbank(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "record"):
        return
    for r in rows(conn, "SELECT * FROM record ORDER BY rowid"):
        acc = v(r["accession"])
        if not acc:
            continue
        evidence, outcome = massbank_confidence(r["confidence"])
        flags = None
        if v(r["deprecated"]):
            outcome, flags = "inconclusive", f"deprecated: {r['deprecated']}"
        key = _inchikey(r["inchikey"])
        cid = re.search(r"CID:?\s*(\d+)", v(r["pubchem"]) or "")
        compound = f"inchikey:{key}" if key else (f"pubchem:{cid.group(1)}" if cid else None)
        cname = names(r["ch_name"])
        reference = _reference(r["publication"])
        contributor = acc.split("-")[1] if acc.count("-") >= 2 else ""
        upstream = _MB_UPSTREAM.get(contributor.upper())
        splash = v(r["splash"])
        note = "; ".join(x for x in (f"SPLASH {splash}" if splash else None,
                                     f"via {upstream}" if upstream else None) if x) or None
        if compound is None:
            yield unresolved("compound_spectrum", "massbank", None, cname, f"massbank:{acc}",
                             "the record gives no InChIKey or PubChem CID",
                             reference=reference, note=r["record_title"])
            continue
        licence = v(r["license"])
        condition = "; ".join(x for x in (
            f"CE {r['collision_energy']}" if v(r["collision_energy"]) else None,
            v(r["precursor_type"])) if x) or None
        yield rel("compound_spectrum", "massbank", compound, cname, f"massbank:{acc}",
                  r["record_title"], evidence, outcome=outcome, reference=reference,
                  note=note, license=licence,
                  context=ctx(instrument=v(r["instrument"]), method=v(r["instrument_type"]),
                              ion_mode=v(r["ion_mode"]), assay=v(r["ms_type"]),
                              condition=condition, confidence=v(r["confidence"]),
                              flags=flags))
        organism = v(r["sp_scientific_name"])
        if organism:
            taxid = re.match(r"\s*(\d+)", v(r["sp_taxid"]) or "")
            yield rel("organism_compound", "massbank",
                      f"ncbitaxon:{taxid.group(1)}" if taxid else f"massbank:taxon.{organism}",
                      organism, compound, cname, evidence, outcome=outcome,
                      reference=reference, license=licence,
                      note=f"MassBank {acc}" + (f"; via {upstream}" if upstream else ""),
                      context=ctx(tissue=v(r["sp_sample"]), confidence=v(r["confidence"]),
                                  flags=flags))


_MB_RELEASE = "https://zenodo.org/api/records/19590880/files/MassBank/MassBank-data-2026.03.zip/content"
_MB_LICENSE = ("Per record (each record's LICENSE line binds it): CC BY, CC BY-NC-SA, "
               "CC BY-SA, dl-de/by-2-0, CC BY-NC, CC0, CC BY-NC-ND (counts in release "
               "2026.03: 48404, 33478, 21086, 20658, 8434, 7031, 149). The Zenodo archive's "
               "own cc-by-4.0 does not override them.")

MASSBANK = DatasetSpec(
    "massbank", "MassBank (MassBank-data release 2026.03)", (76,),
    "https://massbank.eu/MassBank/", _MB_LICENSE,
    (FileSpec(_MB_RELEASE, "MassBank-data-2026.03.zip", "record", fmt="massbank_records",
              expected_bytes=239602156,
              note="Zenodo record 19590880 (published 2026-04-15): every record as MassBank "
                   "text, in contributor folders. The only bulk form that keeps each "
                   "record's LICENSE. One row per record in the store; the peaks stay in "
                   "the archive. Zenodo's robots.txt allows /api/records/*/files and asks "
                   "a 10 s crawl delay."),),
    version="2026.03 (data release; the live API at massbank.eu served data version "
            "2025.10 on 2026-10-01)",
    notes="Reference mass spectra deposited by about 60 contributing laboratories, one "
          "text record per spectrum (139,240 records in 2026.03, 234 of them deprecated). "
          "compound_spectrum: compound (InChIKey from CH$LINK, else PubChem CID) -> "
          "MassBank record, with the instrument, instrument type, ion mode, MS level, "
          "collision energy and precursor type as context. The evidence follows the "
          "record's COMMENT: CONFIDENCE (see the module docstring); a deprecated record "
          "is inconclusive. organism_compound: the record's sample organism "
          "(SP$SCIENTIFIC_NAME, NCBI taxon) -> compound, for the about 3,350 records that "
          "name one (mostly mouse tissue, human serum and plant extracts). Records "
          "without an InChIKey or CID (mixtures, unknown structures) wait in the "
          "unresolved queue. Each row's licence is its record's licence. The same "
          "release is also published as NIST and RIKEN MSP files on GitHub "
          "(github.com/MassBank/MassBank-data/releases/download/2026.03/"
          "MassBank_NISTformat.msp, 137 MB) for spectral matching; they drop each "
          "record's LICENSE, so they are not part of this dataset.",
    relations=("compound_spectrum", "organism_compound"),
    commercial_use="unknown",
    upstream=("ReSpect", "CASMI"),
    crosswalk={"compound": "SELECT 'pubchem:' || cid, 'inchikey:' || max(inchikey) FROM "
                           "(SELECT substr(pubchem, instr(pubchem, 'CID:') + 4) AS cid, "
                           "inchikey FROM record WHERE instr(pubchem, 'CID:') > 0 AND "
                           "length(inchikey) = 27) WHERE cid GLOB '[0-9]*' AND "
                           "cid NOT GLOB '*[^0-9]*' GROUP BY cid "
                           "HAVING count(DISTINCT inchikey) = 1"},
)


# ======================================================================= GNPS
_GNPS_COLUMNS = (
    "spectrum_id", "library_membership", "spectrum_status", "ms_level", "splash",
    "Compound_Name", "Compound_Source", "Ion_Source", "Instrument", "Ion_Mode", "Adduct",
    "Precursor_MZ", "ExactMass", "Charge", "CAS_Number", "Pubmed_ID", "Smiles", "INCHI",
    "InChIKey_smiles", "InChIKey_inchi", "Formula_smiles", "Library_Class", "PI",
    "Data_Collector", "submit_user", "create_time", "source_file", "task", "scan", "url")


def _read_gnps(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A GNPS per-library JSON export: one row per spectrum, without its peaks.

    The export is one JSON array; ``peaks_json`` and ``annotation_history`` (the bulk of
    each record) are not loaded: the peaks stay in the file.
    """
    yield list(_GNPS_COLUMNS)
    with open_text(path) as fh:
        data = json.load(fh)
    for rec in data if isinstance(data, list) else []:
        if isinstance(rec, dict):
            yield [None if rec.get(c) is None else str(rec.get(c)) for c in _GNPS_COLUMNS]


#: GNPS library quality classes (documentation, "Spectrum curation").
_GNPS_CLASS = {"1": ("known", "positive", "gold"), "2": ("reported", "positive", "silver"),
               "3": ("reported", "inconclusive", "bronze")}
#: Imported third-party libraries: upstream name and the licence their rows keep.
_GNPS_IMPORT = {
    "MASSBANK": ("MassBank", "MassBank per-record licences (CC0 ... CC BY-NC-SA); the GNPS "
                             "copy does not carry them"),
    "MASSBANKEU": ("MassBank", "MassBank per-record licences (CC0 ... CC BY-NC-SA); the "
                               "GNPS copy does not carry them"),
    "MONA": ("MoNA", "MoNA per-record licences (CC BY 4.0 by default, some non-commercial)"),
    "HMDB": ("HMDB", "HMDB terms: free for non-commercial use"),
    "RESPECT": ("ReSpect", "ReSpect licence (not stated in the GNPS copy)"),
    "BMDMS-NP": ("BMDMS-NP", "BMDMS-NP licence (not stated in the GNPS copy)"),
    "CASMI": ("CASMI", "CASMI licence (not stated in the GNPS copy)"),
    "SUMNER": ("Sumner", "Sumner library licence (not stated in the GNPS copy)"),
    "BIRMINGHAM-UHPLC-MS-POS": ("Birmingham UHPLC-MS", "not stated in the GNPS copy"),
    "BIRMINGHAM-UHPLC-MS-NEG": ("Birmingham UHPLC-MS", "not stated in the GNPS copy"),
}
_GNPS_CC0 = "CC0 1.0 (spectra contributed directly to GNPS)"


def _gnps_propagated(library: str) -> bool:
    """Libraries of annotations propagated by networking, not measured on a standard."""
    lib = library.upper()
    return ("PROPOGATED" in lib or "PROPAGATED" in lib or lib.startswith((
        "MULTIPLEX-SYNTHESIS", "GNPS-CONJUGATED-METABOLOME", "REFRAME-POSITIVE",
        "REFRAME-NEGATIVE")) or lib in {"GNPS-SUSPECTLIST", "GNPS-DRUG-ANALOG",
                                        "GNPS-BILE-ACID-MODIFICATIONS",
                                        "GNPS-MASSQL-BILE-ACID-ISOMER",
                                        "GNPS-CANDIDATE-CARNITINES-MASSQL",
                                        "GNPS-SYNTHESIS-CARNITINES"})


def _gnps(conn: sqlite3.Connection) -> Iterator[Row | None]:
    for table in _tables(conn, "lib_"):
        for r in rows(conn, f'SELECT * FROM "{table}" ORDER BY rowid'):
            sid = v(r["spectrum_id"])
            if not sid:
                continue
            library = v(r["library_membership"]) or table[4:]
            cls = (v(r["Library_Class"]) or "").strip()
            name = _clean(r["Compound_Name"])
            pmid = v(r["Pubmed_ID"])
            reference = f"pmid:{pmid}" if pmid and pmid.isdigit() else None
            if cls == "10":
                yield unresolved("compound_spectrum", "gnps", None, name, f"gnps:{sid}",
                                 "GNPS challenge spectrum (class 10): identity unknown",
                                 reference=reference, note=library)
                continue
            key = _inchikey(r["InChIKey_smiles"]) or _inchikey(r["InChIKey_inchi"])
            if key is None:
                yield unresolved("compound_spectrum", "gnps", None, name, f"gnps:{sid}",
                                 "the library gives no structure (no InChIKey)",
                                 reference=reference, note=library)
                continue
            evidence, outcome, grade = _GNPS_CLASS.get(
                cls, ("reported", "inconclusive", f"class {cls or 'not given'}"))
            if _gnps_propagated(library):
                evidence = "aggregated"
            flags = None
            status = v(r["spectrum_status"])
            if status and status != "1":
                outcome, flags = "inconclusive", f"spectrum_status {status}"
            imported = _GNPS_IMPORT.get(library.upper())
            splash = v(r["splash"])
            splash = None if splash and set(splash.split("-")) == {"null"} else splash
            note = "; ".join(x for x in (f"SPLASH {splash}" if splash else None,
                                         f"via {imported[0]}" if imported else None)
                             if x) or None
            level = v(r["ms_level"])
            title = "; ".join(x for x in (name, v(r["Ion_Source"]), v(r["Instrument"]),
                                          f"MS{level}" if level else None, v(r["Adduct"]))
                              if x)
            yield rel("compound_spectrum", "gnps", f"inchikey:{key}", name, f"gnps:{sid}",
                      title or sid, evidence, outcome=outcome, reference=reference,
                      note=note, license=imported[1] if imported else _GNPS_CC0,
                      context=ctx(instrument=v(r["Instrument"]), method=v(r["Ion_Source"]),
                                  ion_mode=v(r["Ion_Mode"]),
                                  assay=f"MS{level}" if level else None,
                                  condition=v(r["Adduct"]), confidence=grade,
                                  sample=v(r["Compound_Source"]), library=library,
                                  flags=flags))


_GNPS_BASE = "https://external.gnps2.org/gnpslibrary/"
#: Natural-product libraries contributed directly to GNPS (CC0): name, bytes on 2026-10-01.
_GNPS_DEFAULT = (
    ("GNPS-NIH-NATURALPRODUCTSLIBRARY", "NIH Natural Products Library, round 1"),
    ("GNPS-NIH-NATURALPRODUCTSLIBRARY_ROUND2_POSITIVE",
     "NIH Natural Products Library, round 2, positive mode"),
    ("GNPS-NIH-NATURALPRODUCTSLIBRARY_ROUND2_NEGATIVE",
     "NIH Natural Products Library, round 2, negative mode"),
    ("GNPS-PRESTWICKPHYTOCHEM", "Prestwick phytochemical library"),
    ("LEAFBOT", "plant natural-product standards"),
    ("PHENOLICSDB", "phenolic standards"),
    ("XANTHONES-DB", "isolated xanthones"),
    ("TUEBINGEN-NATURAL-PRODUCT-COLLECTION", "Tuebingen natural-product collection"),
    ("PYRROLIZIDINE-ALKALOID-SPECTRAL-LIBRARY", "pyrrolizidine alkaloids"),
    ("MIADB", "monoterpene indole alkaloids"),
    ("ELIXDB-LICHEN-DATABASE", "lichen metabolites"),
    ("UM-NPDC", "University of Michigan natural-products discovery core"),
)

GNPS = DatasetSpec(
    "gnps", "GNPS natural-product spectral libraries", (77,),
    "https://external.gnps2.org/gnpslibrary",
    "CC0 1.0 for spectra contributed directly to GNPS (GNPS documentation, 'License'); "
    "imported third-party libraries keep their upstream licences",
    tuple(FileSpec(_GNPS_BASE + f"{lib}.json", f"{lib}.json", "lib_" + safe_name(lib).lower(),
                   fmt="gnps_library_json", license=_GNPS_CC0, note=what)
          for lib, what in _GNPS_DEFAULT)
    + (FileSpec(_GNPS_BASE + "MASSBANKEU.json", "MASSBANKEU.json", "lib_massbankeu",
                fmt="gnps_library_json", optional=True,
                license=_GNPS_IMPORT["MASSBANKEU"][1],
                note="An IMPORT library: MassBank-EU spectra re-hosted by GNPS (rows say "
                     "'via MassBank'). Prefer the massbank dataset, which keeps each "
                     "record's licence."),
       FileSpec(_GNPS_BASE + "GNPS-LIBRARY.mgf", "GNPS-LIBRARY.mgf", "", fmt="raw",
                optional=True, license=_GNPS_CC0,
                note="The main community library (143 MB, mostly bronze annotations) as "
                     "MGF for spectral matching; not loaded.")),
    version="per-library JSON exports, rebuilt daily by GNPS2 (fetched 2026-10-01; the "
            "store records each file's SHA-256)",
    notes="Twelve natural-product libraries from the 101 public GNPS libraries, chosen for "
          "TCM work and size (about 175 MB together); the aggregated ALL_GNPS files are "
          "2-10 GB and the peak-less gnpslibraryjson is stale (2025-07), so neither is "
          "used. compound_spectrum: compound (InChIKey that GNPS computes from the "
          "annotation's SMILES, else from its InChI) -> GNPS library spectrum (CCMSLIB), "
          "with instrument, ion source, ion mode, MS level, adduct, compound source "
          "(commercial, isolated, crude) and the quality class as context. Gold is known, "
          "silver reported, bronze reported but inconclusive (GNPS: 'any other putative' "
          "annotation); challenge spectra and spectra without a structure wait in the "
          "unresolved queue.",
    relations=("compound_spectrum",),
    relation_licenses={"compound_spectrum": _GNPS_CC0},
    commercial_use="allowed",
    upstream=("MassBank",),
)


# ===================================================================== NP-MRD
_NP_ID = re.compile(r"(NP\d{7})")
_NP_CARD_COLUMNS = (
    "accession", "name", "synonyms", "chemical_formula", "monisotopic_molecular_weight",
    "cas_registry_number", "smiles", "inchi", "inchikey", "kingdom", "super_class",
    "class", "direct_parent", "kegg_id", "chemspider_id", "chebi_id", "pubchem_compound_id",
    "drugbank_id", "foodb_id", "knapsack_id", "phenol_explorer_compound_id", "biocyc_id",
    "pubmed_ids", "creation_date", "update_date", "version")


def _array_items(fh: io.TextIOBase, key: str, chunk: int = 1 << 22) -> Iterator[Any]:
    """The items of the first JSON array under ``key``, decoded one at a time.

    NP-MRD's metadata files are one JSON object of about 350 MB each; this reads them in
    a few MB at a time instead of loading the whole document.
    """
    decoder = json.JSONDecoder()
    buf, pos = "", 0
    marker = f'"{key}"'
    while True:                                   # find the array that opens after key
        more = fh.read(chunk)
        if not more:
            return
        buf += more
        i = buf.find(marker)
        if i < 0:
            buf = buf[-len(marker):]              # the key may straddle two reads
            continue
        j = buf.find("[", i + len(marker))
        if j >= 0:
            pos = j + 1
            break
        buf = buf[i:]
    eof = False
    skip = re.compile(r"[\s,]*")
    while True:
        pos = skip.match(buf, pos).end()
        if pos < len(buf) and buf[pos] == "]":
            return
        try:
            if pos >= len(buf):
                raise json.JSONDecodeError("need more", buf, pos)
            item, end = decoder.raw_decode(buf, pos)
        except json.JSONDecodeError:
            if eof:
                raise
            buf, pos = buf[pos:], 0
            more = fh.read(chunk)
            eof = not more
            buf += more
            continue
        yield item
        pos = end


def _listed(value: Any, inner: str) -> list[Any]:
    """``{"synonym": [...]}`` or ``{"synonym": "x"}`` (an XML export as JSON) as a list."""
    if isinstance(value, dict):
        value = value.get(inner)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _read_np_cards(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """NP-MRD natural-product cards (JSON): identifiers, structure and cross-references."""
    yield list(_NP_CARD_COLUMNS)
    with open_text(path) as fh:
        for rec in _array_items(fh, "natural_product"):
            if not isinstance(rec, dict):
                continue
            tax = rec.get("taxonomy") if isinstance(rec.get("taxonomy"), dict) else {}
            refs = _listed(rec.get("general_references"), "reference")
            row = dict(rec)
            row["synonyms"] = " | ".join(str(s) for s in _listed(rec.get("synonyms"),
                                                                  "synonym") if s)
            for k in ("kingdom", "super_class", "class", "direct_parent"):
                row[k] = tax.get(k)
            row["pubmed_ids"] = " | ".join(str(x.get("pubmed_id")) for x in refs
                                           if isinstance(x, dict) and x.get("pubmed_id"))
            yield [None if row.get(c) in (None, "") or isinstance(row.get(c), (dict, list))
                   else str(row.get(c)) for c in _NP_CARD_COLUMNS]


def _zip_members(path: Path) -> Iterator[tuple[str, str]]:
    with zipfile.ZipFile(path) as archive:
        for name in sorted(n for n in archive.namelist() if not n.endswith("/")):
            yield name.rsplit("/", 1)[-1], archive.read(name).decode("utf-8-sig", "replace")


def _read_np_peak_lists(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """NP-MRD deposited peak lists: one headerless ``atom,ppm`` CSV per deposition."""
    yield ["file", "np_mrd_id", "deposition", "atom", "shift_ppm"]
    for name, text in _zip_members(path):
        m = re.match(r"(NP\d{7})_(\d+)_peak_list", name)
        for fields in csv.reader(io.StringIO(text)):
            if fields and any(f.strip() for f in fields):
                yield [name, m.group(1) if m else None, m.group(2) if m else None,
                       *(fields + [None, None])[:2]]


def _read_np_assignments(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """NP-MRD experimental chemical-shift assignment tables (headerless CSV per file).

    1D tables: ``atom, atom_id, shift_ppm, multiplicity, J_Hz``; C-HSQC tables:
    ``H, h_atom_id, C, c_atom_id, h_shift_ppm, c_shift_ppm``.
    """
    yield ["file", "np_mrd_id", "table_kind", "atom", "atom_id", "shift_ppm",
           "multiplicity", "j_hz", "atom2", "atom2_id", "shift2_ppm"]
    for name, text in _zip_members(path):
        m = _NP_ID.search(name)
        kind = ("chsqc" if "_chsqc_" in name else "user" if "_user_" in name
                else "assignment")
        for f in csv.reader(io.StringIO(text)):
            if not f or not any(x.strip() for x in f):
                continue
            if kind == "chsqc" and len(f) >= 6:
                row = [f[0], f[1], f[4], None, None, f[2], f[3], f[5]]
            else:
                f = (f + [None] * 5)[:5]
                row = [f[0], f[1], f[2], f[3], f[4], None, None, None]
            yield [name, m.group(1) if m else None, kind, *row]


def _np_mrd(conn: sqlite3.Connection) -> Iterator[Row | None]:
    compound: dict[str, tuple[str | None, str | None]] = {}
    for table in _tables(conn, "smiles_"):
        for r in rows(conn, f'SELECT * FROM "{table}"'):
            np_id = v(r["NP_MRD_ID"])
            if np_id:
                compound[np_id] = (None, v(r["Natural_Products_Name"]))
    for table in _tables(conn, "card_"):
        for r in rows(conn, f'SELECT accession, name, inchikey FROM "{table}"'):
            np_id = v(r["accession"])
            if np_id:
                compound[np_id] = (_inchikey(r["inchikey"]),
                                   v(r["name"]) or compound.get(np_id, (None, None))[1])

    def files(table: str, shift_cols: tuple[str, ...]) -> Iterator[tuple[str, str, Any,
                                                                          set[str]]]:
        """(file, NP id, kind, nuclei with a shift) per file of a table."""
        if not has(conn, table):
            return
        kind_col = "table_kind" if table == "assignment" else "NULL"
        current: list[Any] = []
        nuclei: set[str] = set()
        for r in rows(conn, f'SELECT file, np_mrd_id, {kind_col} AS kind, atom, '
                            f'{", ".join(shift_cols)}, '
                            f'{"atom2" if table == "assignment" else "NULL"} AS atom2 '
                            f'FROM "{table}" ORDER BY file, rowid'):
            if current and r["file"] != current[0]:
                yield current[0], current[1], current[2], nuclei
                nuclei = set()
            current = [r["file"], r["np_mrd_id"], r["kind"]]
            if v(r[shift_cols[0]]):
                nuclei.add(_NUCLEUS.get((v(r["atom"]) or "").upper(), v(r["atom"]) or "?"))
            if len(shift_cols) > 1 and v(r[shift_cols[1]]):
                nuclei.add(_NUCLEUS.get((v(r["atom2"]) or "").upper(), v(r["atom2"]) or "?"))
        if current:
            yield current[0], current[1], current[2], nuclei

    assays = {None: "deposited peak list (unassigned)", "assignment": "chemical-shift "
              "assignment", "user": "chemical-shift assignment (depositor)",
              "chsqc": "C-HSQC chemical-shift assignment"}
    for table, shifts in (("peak_list", ("shift_ppm",)),
                          ("assignment", ("shift_ppm", "shift2_ppm"))):
        for name, np_id, kind, nuclei in files(table, shifts):
            np_id = v(np_id)
            if not np_id or not nuclei:            # a table with no shift measured
                continue
            key, cname = compound.get(np_id, (None, None))
            stem = re.sub(r"\.(csv|txt)$", "", name)
            assay = assays.get(kind, assays[None])
            yield rel("compound_spectrum", "np_mrd",
                      f"inchikey:{key}" if key else f"np_mrd:{np_id}", cname,
                      f"np_mrd:{stem}", f"{np_id} NMR {assay} ({', '.join(sorted(nuclei))})",
                      "known",
                      context=ctx(method="NMR", assay=assay, measure="chemical shift",
                                  unit="ppm", condition="nuclei " + ", ".join(sorted(nuclei)),
                                  source_id=np_id))


_NUCLEUS = {"H": "1H", "C": "13C", "N": "15N", "P": "31P", "F": "19F"}
_NP = "https://np-mrd.org/system/downloads/current/"
_NP_CHUNKS = ("NP0000001_NP0050000", "NP0050001_NP0100000", "NP0100001_NP0150000",
              "NP0150001_NP0200000", "NP0200001_NP0250000", "NP0250001_NP0300000",
              "NP0300001_NP0350000")
#: JSON chunks fetched by default: they hold the compounds of 95% of the experimental
#: peak lists and assignment tables (NP00xxxxx and NP033xxxx).
_NP_DEFAULT_CARDS = {0, 6}

NP_MRD = DatasetSpec(
    "np_mrd", "NP-MRD (Natural Products Magnetic Resonance Database)", (78,),
    "https://np-mrd.org/", "CC BY-NC 4.0",
    (FileSpec(_NP + "peak_lists.zip", "peak_lists.zip", "peak_list", fmt="npmrd_peak_lists",
              note="'Unassigned Spectra: NMR Peak List Files (CSV)': 871 deposited "
                   "experimental peak lists (atom, ppm), released 2025-08-01."),
     FileSpec(_NP + "assignment_tables.zip", "assignment_tables.zip", "assignment",
              fmt="npmrd_assignments",
              note="'NMR Experimental Spectra Chemical Shift Assignment Files (CSV)': 1,685 "
                   "assignment tables (1D, depositor and C-HSQC), released 2025-08-01."))
    + tuple(FileSpec(_NP + f"smiles_{c}.csv.gz", f"smiles_{c}.csv.gz", f"smiles_{i + 1}",
                     fmt="csv", note="Natural_Products_Name, NP_MRD_ID, SMILES")
            for i, c in enumerate(_NP_CHUNKS))
    + tuple(FileSpec(_NP + f"npmrd_natural_products_{c}_json.zip",
                     f"npmrd_natural_products_{c}_json.zip", f"card_{i + 1}",
                     fmt="npmrd_cards", optional=i not in _NP_DEFAULT_CARDS,
                     note="NP-Cards (JSON, about 300-350 MB unzipped, read as a stream): "
                          "InChIKey, formula, ClassyFire class and cross-references")
            for i, c in enumerate(_NP_CHUNKS))
    + (FileSpec(_NP + "predicted_nmrml_spectra.zip", "predicted_nmrml_spectra.zip", "",
                fmt="raw", optional=True, expected_bytes=2890156867,
                note="'NMR Predicted Spectra (nmrML)': computational predictions, kept "
                     "apart from the experimental files and never loaded as relations "
                     "(2.9 GB)."),
       FileSpec(_NP + "experimental_spectra.zip", "experimental_spectra.zip", "", fmt="raw",
                optional=True, expected_bytes=13721283834,
                note="Deposition FID files (13.7 GB), analysis layer.")),
    version="downloads released 2025-08-01 (fetched 2026-10-01)",
    notes="NMR data of natural products. compound_spectrum: compound (InChIKey from the "
          "NP-Card when its JSON chunk is loaded, else the NP-MRD id) -> one deposited "
          "experimental peak list or experimental chemical-shift assignment table, evidence "
          "known, with the assay (peak list, 1D, depositor or C-HSQC assignment) and the "
          "nuclei that have a shift as context. A table that lists atoms but no shift "
          "gives no row. Predicted and simulated spectra are separate archives and are not "
          "loaded. The backfilled peak lists, nmrML and FIDs on moldb.np-mrd.org are not "
          "fetched: that host's robots.txt disallows everything. np-mrd.org asks a 2 s "
          "crawl delay. The site carries the banner 'under review for potential "
          "modification in compliance with administration directives'.",
    relations=("compound_spectrum",),
    commercial_use="forbidden",
    upstream=("NPAtlas", "JEOL CH-NMR-NP", "HMDB", "BMRB"),
)


DATASETS: tuple[DatasetSpec, ...] = (MASSBANK, GNPS, NP_MRD)
EXTRACTORS = {"massbank": _massbank, "gnps": _gnps, "np_mrd": _np_mrd}
READERS = {"massbank_records": _read_massbank, "gnps_library_json": _read_gnps,
           "npmrd_cards": _read_np_cards, "npmrd_peak_lists": _read_np_peak_lists,
           "npmrd_assignments": _read_np_assignments}
