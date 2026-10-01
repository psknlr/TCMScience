"""Safety: in vitro toxicology, adverse outcome pathways, rare diseases, exposure surveys.

Four snapshot datasets, each checked from the harness on 2026-10-01:

* ``comptox``: EPA's ToxCast database, invitrodb v4.3 (it includes the Tox21 endpoints).
  The per-chemical hit calls come from the release's PubChem-deposition archive, one
  workbook per assay endpoint (aeid): sample, outcome, AC50, continuous hit call, BMD.
  The assay annotations, the endpoint-to-gene and endpoint-to-AOP mappings, the
  cytotoxicity-burst summary and the analytical QC calls are the release's own workbooks.
  The workbooks declare a wrong sheet size (A1) and, for one of them, a drawing that does
  not exist, so the built-in xlsx loader reads one cell or fails: they are read here
  straight from the sheet XML (``toxcast_xlsx``).
* ``aopwiki``: the AOP-Wiki's quarterly XML (the dated, permanently kept release), read
  into tables of AOPs, key events, key-event relationships, stressors and chemicals.
* ``orphadata``: Orphanet's scientific knowledge files (genes, HPO phenotypes,
  nomenclature and cross-references), July 2026 release.
* ``nhanes``: three public-use NHANES files (SAS transport) as tables. NHANES publishes
  participant-level measurements, not relations; associations are computed by the user
  under the survey design (weights, strata, PSUs), so the dataset yields no relations.

MIMIC-IV is not here: its files are released only to credentialed users under a data use
agreement that forbids sharing access and limits use to "lawful use in scientific research
and no other" (so commercial use is forbidden), and PhysioNet's policy forbids sending the
data to online LLM services that are not on its list.
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError, open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

#: Relation kinds no registered kind fits. ToxCast annotates each assay endpoint with the
#: gene it is meant to measure and with the AOP-Wiki key events it informs; the AOP-Wiki
#: lists the events that make up each AOP and the stressors known to trigger an AOP (its
#: XML carries stressors per AOP, not per key event, so ``stressor_event`` cannot hold
#: them without guessing which event a stressor acts on). A stressor is often not a
#: chemical (radiation, hypoxia), so ``stressor_aop`` is declared stressor -> aop; rows
#: whose stressor is a chemical say subject_type 'compound' so that the crosswalk maps them.
KINDS: dict[str, tuple[str, str]] = {
    "assay_target": ("assay", "target"),     # an assay endpoint's intended gene target
    "assay_event": ("assay", "event"),       # an assay endpoint mapped to an AOP key event
    "aop_event": ("aop", "event"),           # a key event in an adverse outcome pathway
    "stressor_aop": ("stressor", "aop"),     # a stressor known to trigger an AOP
}

_NA = frozenset({"#n/a", "na", "n/a", "nan", "none", "null", ""})


def _val(value: Any) -> str | None:
    """A cell value, or None for the spellings these files use for 'missing'."""
    text = v(value)
    return None if text is None or text.lower() in _NA else text


def _float(value: Any) -> float | None:
    text = _val(value)
    try:
        number = float(text) if text is not None else None
    except ValueError:
        return None
    return number if number is not None and math.isfinite(number) else None


# --------------------------------------------------------------------------- xlsx
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _column(ref: str | None) -> int | None:
    if not ref:
        return None
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1 if n else None


def _xlsx_rows(z: zipfile.ZipFile, sheet: str | None) -> Iterator[list[str | None]]:
    """A sheet's rows from the sheet XML, ignoring the declared size and drawings."""
    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") or "" for r in rels}
    sheets = [(s.get("name"), targets.get(s.get(_R + "id"), ""))
              for s in workbook.iter(_M + "sheet")]
    chosen = [t for n, t in sheets if sheet is None or n == sheet]
    if not chosen:
        raise StoreError(f"no sheet {sheet!r}; have {[n for n, _ in sheets]}")
    target = chosen[0]
    path = target.lstrip("/") if target.startswith("/") else "xl/" + target
    strings: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        with z.open("xl/sharedStrings.xml") as fh:
            for _, el in ET.iterparse(fh):
                if el.tag == _M + "si":
                    strings.append("".join(t.text or "" for t in el.iter(_M + "t")))
                    el.clear()
    with z.open(path) as fh:
        for _, el in ET.iterparse(fh):
            if el.tag != _M + "row":
                continue
            cells: dict[int, str | None] = {}
            for pos, c in enumerate(el.findall(_M + "c")):
                col = _column(c.get("r"))
                col = pos if col is None else col
                kind, value = c.get("t"), c.find(_M + "v")
                if kind == "s" and value is not None and value.text is not None:
                    cells[col] = strings[int(value.text)]
                elif kind == "inlineStr":
                    cells[col] = "".join(t.text or "" for t in c.iter(_M + "t"))
                else:
                    cells[col] = value.text if value is not None else None
            el.clear()
            if cells:
                yield [cells.get(i) for i in range(max(cells) + 1)]


def _read_xlsx(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    with zipfile.ZipFile(path) as z:
        yield from _xlsx_rows(z, spec.sheet)


_AEID = re.compile(r"aeid(\d+)_")
_DTXSID = re.compile(r"(DTXSID\d+)")
#: Columns of the deposition kept in the store. The CompTox URL is cut to its DTXSID (it
#: is the same URL prefix on all 3.3M rows) and the PubChem result tag (a row counter) is
#: dropped, which keeps the store about 200 MB smaller.
_HITCALL = ("aeid", "sample", "outcome", "dtxsid", "AC50", "HITC", "BMD")
_HITCALL_SOURCE = ("PUBCHEM_EXT_DATASOURCE_REGID", "PUBCHEM_ACTIVITY_OUTCOME",
                   "PUBCHEM_ACTIVITY_URL", "AC50", "HITC", "BMD")


def _read_pubchem_zip(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The 'Results' sheet of every endpoint workbook in the PubChem-deposition archive.

    Each member is one assay endpoint (``..._aeid<N>_for_pubchem_...xlsx``); its aeid
    becomes the first column. The rows under the header that describe the columns
    (RESULT_TYPE, RESULT_DESCR, RESULT_UNIT, ...) are not results and are left out.
    Rows are written as ``_HITCALL``: the CompTox URL becomes its DTXSID (None when the
    URL names none) and the result tag is dropped.
    """
    header: list[str | None] | None = None
    pick: list[int] = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".xlsx"):
                continue
            m = _AEID.search(info.filename)
            if not m:
                raise StoreError(f"{info.filename}: no aeid in the member name")
            with zipfile.ZipFile(io.BytesIO(z.read(info))) as inner:
                it = _xlsx_rows(inner, spec.sheet or "Results")
                head = next(it, None)
                if head is None:
                    continue
                if header is None:
                    header = head
                    missing = [c for c in _HITCALL_SOURCE if c not in head]
                    if missing:
                        raise StoreError(f"{info.filename}: no column(s) {missing}")
                    pick = [head.index(c) for c in _HITCALL_SOURCE]
                    yield list(_HITCALL)
                elif head != header:
                    raise StoreError(f"{info.filename}: columns {head} differ from {header}")
                url = pick[2]
                for r in it:
                    if r and (r[0] or "").strip().isdigit():
                        r = r + [None] * (len(header) - len(r))
                        out = [r[i] for i in pick]
                        found = _DTXSID.search(r[url] or "")
                        out[2] = found.group(1) if found else None
                        yield [m.group(1), *out]


def _read_dsstox(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """DTXSID, preferred name and InChIKey from EPA's DSSTox dump (CSV members in a zip).

    The archive holds the whole dump (``DSSToxCCDdump.csv``) and the same rows split into
    ``DSSToxCCDdump<N>.csv``; only the whole one is read, so no row is loaded twice. The
    other columns (SMILES, IUPAC name, masses, the long identifier list) are not kept.
    """
    keep = ("DTXSID", "PREFERRED_NAME", "INCHIKEY")
    with zipfile.ZipFile(path) as z:
        members = [i for i in z.infolist()
                   if i.filename.rsplit("/", 1)[-1].lower() == "dsstoxccddump.csv"]
        if len(members) != 1:
            raise StoreError(f"{path.name}: expected one DSSToxCCDdump.csv, found "
                             f"{len(members)}")
        with z.open(members[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
            head = next(reader, None) or []
            if any(c not in head for c in keep):
                raise StoreError(f"{path.name}: columns {head} lack {keep}")
            pick = [head.index(c) for c in keep]
            yield ["dtxsid", "preferred_name", "inchikey"]
            for r in reader:
                if len(r) > max(pick):
                    yield [_val(r[i]) for i in pick]


# ------------------------------------------------------------------------- AOP-Wiki
_A = "{http://www.aopkb.org/aop-xml}"


def _t(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find("/".join(_A + p for p in path.split("/")))
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


_AOP_TABLES: dict[str, list[str]] = {
    "aop": ["id", "title", "short_name", "wiki_license", "oecd_status", "saaop_status",
            "oecd_project", "handbook_version", "creation", "last_modified"],
    "key_event": ["id", "title", "short_name", "level", "organ_source_id", "organ_name",
                  "cell_source_id", "cell_name", "creation", "last_modified"],
    "key_event_relationship": ["id", "upstream_id", "downstream_id", "detail_level",
                               "creation", "last_modified"],
    "aop_event": ["aop_id", "event_id", "role"],
    "aop_relationship": ["aop_id", "relationship_id", "adjacency",
                         "quantitative_understanding", "evidence"],
    "aop_stressor": ["aop_id", "stressor_id", "evidence"],
    "key_event_stressor": ["event_id", "stressor_id", "evidence"],
    "stressor": ["id", "name", "chemical_id", "user_term"],
    "chemical": ["id", "casrn", "jchem_inchi_key", "indigo_inchi_key", "preferred_name",
                 "dsstox_id"],
    "event_component": ["event_id", "object_id", "process_id", "action_id"],
    "term": ["id", "kind", "source", "source_id", "name"],
    "aop_wiki_id": ["id", "kind", "aop_wiki_id"],
}


def _aop_rows(table: str, tag: str, el: ET.Element) -> Iterator[list[str | None]]:
    uid = el.get("id")
    if table == "aop" and tag == "aop":
        yield [uid, _t(el, "title"), _t(el, "short-name"), _t(el, "status/wiki-license"),
               _t(el, "status/oecd-status"), _t(el, "status/saaop-status"),
               _t(el, "oecd-project"), _t(el, "handbook-version"),
               _t(el, "creation-timestamp"), _t(el, "last-modification-timestamp")]
    elif table == "key_event" and tag == "key-event":
        yield [uid, _t(el, "title"), _t(el, "short-name"),
               _t(el, "biological-organization-level"), _t(el, "organ-term/source-id"),
               _t(el, "organ-term/name"), _t(el, "cell-term/source-id"),
               _t(el, "cell-term/name"), _t(el, "creation-timestamp"),
               _t(el, "last-modification-timestamp")]
    elif table == "key_event_relationship" and tag == "key-event-relationship":
        yield [uid, _t(el, "title/upstream-id"), _t(el, "title/downstream-id"),
               _t(el, "title/detail-level"), _t(el, "creation-timestamp"),
               _t(el, "last-modification-timestamp")]
    elif table == "aop_event" and tag == "aop":
        for path, role in (("molecular-initiating-event", "MolecularInitiatingEvent"),
                           ("key-events/key-event", "KeyEvent"),
                           ("adverse-outcome", "AdverseOutcome")):
            for e in el.iterfind("/".join(_A + p for p in path.split("/"))):
                yield [uid, e.get("key-event-id"), role]
    elif table == "aop_relationship" and tag == "aop":
        for r in el.iterfind(f"{_A}key-event-relationships/{_A}relationship"):
            yield [uid, r.get("id"), _t(r, "adjacency"),
                   _t(r, "quantitative-understanding-value"), _t(r, "evidence")]
    elif table == "aop_stressor" and tag == "aop":
        for s in el.iterfind(f"{_A}aop-stressors/{_A}aop-stressor"):
            yield [uid, s.get("stressor-id"), _t(s, "evidence")]
    elif table == "key_event_stressor" and tag == "key-event":
        for s in el.iterfind(f"{_A}key-event-stressors/{_A}key-event-stressor"):
            yield [uid, s.get("stressor-id"), _t(s, "evidence")]
    elif table == "stressor" and tag == "stressor":
        chems = el.findall(f"{_A}chemicals/{_A}chemical-initiator")
        for c in chems or [None]:
            yield [uid, _t(el, "name"), c.get("chemical-id") if c is not None else None,
                   c.get("user-term") if c is not None else None]
    elif table == "chemical" and tag == "chemical":
        yield [uid, _t(el, "casrn"), _t(el, "jchem-inchi-key"), _t(el, "indigo-inchi-key"),
               _t(el, "preferred-name"), _t(el, "dsstox-id")]
    elif table == "event_component" and tag == "key-event":
        for b in el.iterfind(f"{_A}biological-events/{_A}biological-event"):
            yield [uid, b.get("object-id"), b.get("process-id"), b.get("action-id")]
    elif table == "term" and tag in ("biological-object", "biological-process",
                                     "biological-action", "taxonomy"):
        yield [uid, tag, _t(el, "source"), _t(el, "source-id"), _t(el, "name")]
    elif table == "aop_wiki_id" and tag == "vendor-specific":
        for ref in el:
            yield [ref.get("id"), ref.tag.replace(_A, "").removesuffix("-reference"),
                   ref.get("aop-wiki-id")]


def _read_aopwiki(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One table of the AOP-XML file (``spec.table``), streamed element by element."""
    if spec.table not in _AOP_TABLES:
        raise StoreError(f"aopwiki_xml has no table {spec.table!r}")
    yield list(_AOP_TABLES[spec.table])
    depth = 0
    with open_text(path) as fh:
        for event, el in ET.iterparse(fh, events=("start", "end")):
            if event == "start":
                depth += 1
                continue
            depth -= 1
            if depth == 1:                       # a child of <data>
                yield from _aop_rows(spec.table, el.tag.replace(_A, ""), el)
                el.clear()


# ------------------------------------------------------------------------ Orphadata
_ORPHA_TABLES: dict[str, tuple[str, list[str]]] = {
    "gene_association": ("Disorder", [
        "OrphaCode", "Name", "DisorderType", "DisorderGroup", "GeneSymbol", "GeneName",
        "GeneType", "HGNC", "Ensembl", "SwissProt", "OMIM", "Locus",
        "DisorderGeneAssociationType", "DisorderGeneAssociationStatus",
        "SourceOfValidation"]),
    "phenotype": ("HPODisorderSetStatus", [
        "OrphaCode", "Name", "DisorderType", "DisorderGroup", "HPOId", "HPOTerm",
        "HPOFrequency", "DiagnosticCriteria", "Source", "ValidationStatus",
        "ValidationDate", "Online"]),
    "disorder": ("Disorder", [
        "OrphaCode", "Name", "DisorderType", "DisorderGroup", "ExpertLink", "Synonyms"]),
    "disorder_xref": ("Disorder", [
        "OrphaCode", "Source", "Reference", "DisorderMappingRelation",
        "DisorderMappingICDRelation", "DisorderMappingValidationStatus"]),
}


def _x(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    if found is None:
        return None
    return (found.text or "").strip() or None


def _refs(gene: ET.Element, source: str) -> str | None:
    return " | ".join(r.findtext("Reference", "").strip()
                      for r in gene.iterfind("ExternalReferenceList/ExternalReference")
                      if r.findtext("Source", "").strip() == source
                      and r.findtext("Reference", "").strip()) or None


def _disorder(d: ET.Element) -> list[str | None]:
    return [_x(d, "OrphaCode"), _x(d, "Name"), _x(d, "DisorderType/Name"),
            _x(d, "DisorderGroup/Name")]


def _orpha_rows(table: str, el: ET.Element) -> Iterator[list[str | None]]:
    if table == "gene_association":
        head = _disorder(el)
        for a in el.iterfind("DisorderGeneAssociationList/DisorderGeneAssociation"):
            g = a.find("Gene")
            if g is None:
                continue
            loci = " | ".join(x.text.strip() for x in g.iterfind("LocusList/Locus/GeneLocus")
                              if x.text and x.text.strip()) or None
            yield [*head, _x(g, "Symbol"), _x(g, "Name"), _x(g, "GeneType/Name"),
                   _refs(g, "HGNC"), _refs(g, "Ensembl"), _refs(g, "SwissProt"),
                   _refs(g, "OMIM"), loci, _x(a, "DisorderGeneAssociationType/Name"),
                   _x(a, "DisorderGeneAssociationStatus/Name"), _x(a, "SourceOfValidation")]
    elif table == "phenotype":
        d = el.find("Disorder")
        if d is None:
            return
        head = _disorder(d)
        tail = [_x(el, "Source"), _x(el, "ValidationStatus"), _x(el, "ValidationDate"),
                _x(el, "Online")]
        for a in d.iterfind("HPODisorderAssociationList/HPODisorderAssociation"):
            yield [*head, _x(a, "HPO/HPOId"), _x(a, "HPO/HPOTerm"),
                   _x(a, "HPOFrequency/Name"), _x(a, "DiagnosticCriteria/Name"), *tail]
    elif table == "disorder":
        synonyms = " | ".join(s.text.strip() for s in el.iterfind("SynonymList/Synonym")
                              if s.text and s.text.strip()) or None
        yield [*_disorder(el), _x(el, "ExpertLink"), synonyms]
    elif table == "disorder_xref":
        code = _x(el, "OrphaCode")
        for r in el.iterfind("ExternalReferenceList/ExternalReference"):
            yield [code, _x(r, "Source"), _x(r, "Reference"),
                   _x(r, "DisorderMappingRelation/Name"),
                   _x(r, "DisorderMappingICDRelation/Name"),
                   _x(r, "DisorderMappingValidationStatus/Name")]


def _read_orphadata(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One table of an Orphadata product XML file; records are the list's children."""
    if spec.table not in _ORPHA_TABLES:
        raise StoreError(f"orphadata_xml has no table {spec.table!r}")
    record, header = _ORPHA_TABLES[spec.table]
    yield list(header)
    depth = 0
    with open_text(path) as fh:
        for event, el in ET.iterparse(fh, events=("start", "end")):
            if event == "start":
                depth += 1
                continue
            depth -= 1
            if depth == 2 and el.tag == record:      # JDBOR / <...List> / record
                yield from _orpha_rows(spec.table, el)
                el.clear()


# --------------------------------------------------------------------------- NHANES
#: The smallest IBM hex float (16**-65). NHANES transport files store zero this way, and
#: pandas decodes it as 5.397605346934028e-79: in a weight, a count or a below-detection
#: code it is 0.
_XPT_ZERO = 16.0 ** -65


def _xpt_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if isinstance(value, float):
        if math.isnan(value):
            return None                           # a SAS missing value
        if abs(value) == _XPT_ZERO:
            return "0"
        return str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value)
    return str(value)


def _read_xpt(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A SAS transport (XPORT v5) file, as NHANES publishes it, in chunks."""
    import pandas as pd
    with pd.read_sas(path, format="xport", iterator=True, chunksize=20000,
                     encoding="latin-1") as reader:
        first = True
        for chunk in reader:
            if first:
                yield [str(c) for c in chunk.columns]
                first = False
            for values in chunk.itertuples(index=False, name=None):
                yield [_xpt_text(x) for x in values]


READERS = {"toxcast_xlsx": _read_xlsx, "toxcast_pubchem": _read_pubchem_zip,
           "dsstox_csv_zip": _read_dsstox,
           "aopwiki_xml": _read_aopwiki, "orphadata_xml": _read_orphadata,
           "nhanes_xpt": _read_xpt}


# ---------------------------------------------------------------- ToxCast extractor
#: PubChem outcome code -> outcome. Code 3 marks a negative hit call (a fit in the
#: direction the endpoint does not test); EPA's README counts every hitc < 0.9 as
#: inactive, so it is a negative, flagged.
_OUTCOME = {"2": "positive", "1": "negative", "3": "negative"}


def _comptox(conn) -> Iterator[Row | dict | None]:
    endpoint: dict[str, Any] = {}
    if has(conn, "assay_endpoint"):
        endpoint = {r["aeid"]: r for r in rows(conn, "SELECT * FROM assay_endpoint")}
    chem_name: dict[str, str] = {}
    lower_bound: dict[str, float] = {}
    sample_qc: dict[str, str] = {}
    substance_qc: dict[str, str] = {}
    if has(conn, "analytical_qc"):
        for r in rows(conn, "SELECT * FROM analytical_qc"):
            dtx, call = _val(r["dsstox_substance_id"]), _val(r["pass_or_caution"])
            if dtx and _val(r["chnm"]):
                chem_name.setdefault(dtx, r["chnm"])
            if call and r["qc_level"] == "sample" and _val(r["spid"]):
                sample_qc[r["spid"]] = f"sample QC: {call}"
            elif call and r["qc_level"] == "substance" and dtx:
                substance_qc[dtx] = f"substance QC: {call}"
    if has(conn, "dsstox") and has(conn, "hitcall"):
        for r in rows(conn, "SELECT dtxsid, preferred_name FROM dsstox WHERE dtxsid IN "
                            "(SELECT DISTINCT dtxsid FROM hitcall)"):
            if _val(r["preferred_name"]):
                chem_name.setdefault(r["dtxsid"], r["preferred_name"])
    if has(conn, "cytotox"):
        for r in rows(conn, "SELECT * FROM cytotox"):
            dtx = _val(r["dsstox_substance_id"])
            if not dtx:
                continue
            if _val(r["chnm"]):
                chem_name[dtx] = r["chnm"]
            bound = _float(r["cytotox_lower_bound_um"])
            # 1000 uM is the default written when fewer than 5% of the burst assays hit:
            # there is no cytotoxicity estimate to compare an AC50 with
            if bound is not None and bound < 1000:
                lower_bound[dtx] = bound

    def conditions(ep: Any) -> dict[str, Any]:
        if ep is None:
            return {}
        taxon = _val(ep["ncbi_taxon_id"])
        hours = _val(ep["timepoint_hr"])
        return {"species": taxon if taxon and taxon != "0" else None,
                "cell": _val(ep["cell_short_name"]), "tissue": _val(ep["tissue"]),
                "time": f"{hours} h" if hours else None}

    def endpoint_name(aeid: str) -> str | None:
        ep = endpoint.get(aeid)
        return ep["assay_component_endpoint_name"] if ep is not None else None

    if has(conn, "hitcall"):
        for r in rows(conn, "SELECT * FROM hitcall"):
            aeid = _val(r["aeid"])
            dtx = _val(r["dtxsid"])
            if not dtx:
                yield unresolved("compound_assay", "comptox", None, r["sample"],
                                 endpoint_name(aeid or ""), "no DTXSID in the activity URL",
                                 reference=f"comptox:aeid.{aeid}")
                continue
            code = _val(r["outcome"]) or ""
            outcome = _OUTCOME.get(code)
            if outcome is None:
                continue                          # not a result PubChem defines
            ep = endpoint.get(aeid)
            extra: dict[str, Any] = {}
            if outcome == "positive":
                ac50 = _float(r["AC50"])
                if ac50 is not None:
                    extra.update(measure="AC50", value=r["AC50"], unit="uM")
                    bound = lower_bound.get(dtx)
                    burst = ep is not None and _val(ep["burst_assay"]) == "1"
                    if bound is not None and ac50 > bound and not burst:
                        extra["flags"] = ["ac50_above_cytotox_lower_bound"]
            elif code == "3":
                extra["flags"] = ["negative_hitc_unintended_direction"]
            source = _val(ep["assay_source_name"]) if ep is not None else None
            sample = _val(r["sample"])
            yield rel("compound_assay", "comptox", f"comptox:{dtx}", chem_name.get(dtx),
                      f"comptox:aeid.{aeid}", endpoint_name(aeid or ""), "known",
                      score=_val(r["HITC"]), outcome=outcome,
                      note="via Tox21" if source == "TOX21" else None,
                      context=ctx(sample=sample, qc=sample_qc.get(sample or "")
                                  or substance_qc.get(dtx), **conditions(ep), **extra))
    if has(conn, "assay_target"):
        for r in rows(conn, "SELECT * FROM assay_target"):
            aeid, kind, target = (_val(r["aeid"]), _val(r["target_type"]),
                                  _val(r["target_id"]))
            subject = f"comptox:aeid.{aeid}"
            sname = r["assay_component_endpoint_name_x"]
            if kind == "entrez_gene_id" and target:
                taxon = _val(r["ncbi_taxon_id"])
                yield rel("assay_target", "comptox", subject, sname, f"ncbigene:{target}",
                          names(r["official_symbol"], r["official_full_name"]), "listed",
                          context=ctx(species=taxon))
            elif kind == "key_event" and target:
                yield rel("assay_event", "comptox", subject, sname,
                          f"aopwiki:event.{target}", None, "listed")


# --------------------------------------------------------------- AOP-Wiki extractor
#: Per-AOP licence on the AOP-Wiki. Key events and KERs are always CC BY-SA; an AOP page
#: may instead be 'All rights reserved' (for 12 months from creation, extendable).
_BY_SA = "CC BY-SA 2.0 (AOP-Wiki default content licence)"
#: XML element kind (vendor-specific ``*-reference``) -> the id prefix rows use
_AOP_PREFIX = {"aop": "aop", "key-event": "event", "key-event-relationship": "ker",
               "stressor": "stressor", "chemical": "chemical"}
_ARR = "All rights reserved (the AOP's own licence on AOP-Wiki)"
_ASSESSED = frozenset({"High", "Moderate", "Low"})


def _aop_licence(value: str | None) -> str:
    """The licence of an AOP's rows from its ``wiki-license`` value.

    'BY-SA' (and an empty value: CC BY-SA is the wiki's documented default) is CC BY-SA;
    'All rights reserved' is kept. Any other value, such as 'Open for adoption' (an
    authorship status, not a licence grant), states no licence: the rows say so and
    classify as unknown, so they are left out of commercial queries.
    """
    text = (value or "").strip()
    if text.lower() in ("", "by-sa", "cc by-sa", "cc-by-sa"):
        return _BY_SA
    if text.lower() == "all rights reserved":
        return _ARR
    return f"Not stated (AOP-Wiki wiki-license value '{text}' is not a licence grant)"


def _aopwiki(conn) -> Iterator[Row | dict | None]:
    if not has(conn, "aop_wiki_id"):
        return
    num = {(r["kind"], r["id"]): r["aop_wiki_id"]
           for r in rows(conn, "SELECT * FROM aop_wiki_id")}

    def ident(kind: str, uid: str | None) -> str | None:
        n = num.get((kind, uid))
        return f"aopwiki:{_AOP_PREFIX[kind]}.{n}" if n else None

    aop = {r["id"]: r for r in rows(conn, "SELECT * FROM aop")} if has(conn, "aop") else {}
    event = ({r["id"]: r for r in rows(conn, "SELECT * FROM key_event")}
             if has(conn, "key_event") else {})
    ker = ({r["id"]: r for r in rows(conn, "SELECT * FROM key_event_relationship")}
           if has(conn, "key_event_relationship") else {})

    def aop_id(uid: str) -> tuple[str | None, str | None, str]:
        a = aop.get(uid)
        return (ident("aop", uid), names(a["title"], a["short_name"]) if a else None,
                _aop_licence(a["wiki_license"] if a else None))

    def event_id(uid: str | None) -> tuple[str | None, str | None]:
        e = event.get(uid)
        return ident("key-event", uid), names(e["title"]) if e else None

    def status(uid: str) -> str | None:
        a = aop.get(uid)
        return f"OECD status: {a['oecd_status']}" if a and _val(a["oecd_status"]) else None

    if has(conn, "aop_relationship"):
        for r in rows(conn, "SELECT * FROM aop_relationship"):
            k = ker.get(r["relationship_id"])
            if k is None:
                continue
            aid, _, licence = aop_id(r["aop_id"])
            up, up_name = event_id(k["upstream_id"])
            down, down_name = event_id(k["downstream_id"])
            evidence = _val(r["evidence"])
            quant = _val(r["quantitative_understanding"])
            adjacency = _val(r["adjacency"])
            yield rel("key_event_relationship", "aopwiki", up, up_name, down, down_name,
                      "known" if evidence in _ASSESSED else "reported", reference=aid,
                      note=status(r["aop_id"]), license=licence,
                      context=ctx(confidence=evidence,
                                  direct={"adjacent": True, "non-adjacent": False}
                                  .get(adjacency or ""),
                                  qc=(f"quantitative understanding: {quant}"
                                      if quant and quant != "Not Specified" else None),
                                  source_id=ident("key-event-relationship",
                                                  r["relationship_id"])))
    if has(conn, "aop_event"):
        for r in rows(conn, "SELECT * FROM aop_event"):
            aid, aname, licence = aop_id(r["aop_id"])
            eid, ename = event_id(r["event_id"])
            yield rel("aop_event", "aopwiki", aid, aname, eid, ename, "listed",
                      note=r["role"], license=licence)

    chemical = ({r["id"]: r for r in rows(conn, "SELECT * FROM chemical")}
                if has(conn, "chemical") else {})
    stressor: dict[str, list[Any]] = {}
    if has(conn, "stressor"):
        for r in rows(conn, "SELECT * FROM stressor"):
            stressor.setdefault(r["id"], []).append(r)

    def stressor_subjects(uid: str) -> Iterator[tuple[str | None, str | None, str | None,
                                                      str | None]]:
        """(id, names, type, note) for each chemical of a stressor, or the stressor.

        The type is 'compound' for a chemical, else 'stressor' (None: the kind's own).
        """
        sid = ident("stressor", uid)
        for s in stressor.get(uid, []):
            c = chemical.get(s["chemical_id"]) if s["chemical_id"] else None
            if c is None:
                yield sid, names(s["name"]), None, None
                continue
            key = _inchikey(c)
            cid = (f"comptox:{c['dsstox_id']}" if _val(c["dsstox_id"])
                   else f"inchikey:{key}" if key else ident("chemical", c["id"]))
            yield cid, names(c["preferred_name"], s["name"]), "compound", sid

    for table, kind in (("aop_stressor", "stressor_aop"), ("key_event_stressor",
                                                           "stressor_event")):
        if not has(conn, table):
            continue
        for r in rows(conn, f"SELECT * FROM {table}"):
            if kind == "stressor_aop":
                oid, oname, licence = aop_id(r["aop_id"])
                ref, note = oid, status(r["aop_id"])
            else:
                (oid, oname), licence, ref, note = event_id(r["event_id"]), _BY_SA, None, None
            evidence = _val(r["evidence"])
            for sid, sname, stype, snote in stressor_subjects(r["stressor_id"]):
                if stype is None and kind == "stressor_event":
                    stype = "stressor"            # registered as compound -> event
                yield rel(kind, "aopwiki", sid, sname, oid, oname,
                          "known" if evidence in _ASSESSED else "reported",
                          subject_type=stype, reference=ref, license=licence,
                          note=names(snote, note), context=ctx(confidence=evidence))


def _inchikey(chem: Any) -> str | None:
    """The chemical's InChIKey when its two computed keys agree (or only one is given)."""
    keys = {k for k in (_val(chem["jchem_inchi_key"]), _val(chem["indigo_inchi_key"]))
            if k and len(k) == 27}
    return keys.pop() if len(keys) == 1 else None


# -------------------------------------------------------------- Orphadata extractor
_CITATION = re.compile(r"^(.+?)\[(PMID|OTHER)\]$")


def _citations(text: str | None) -> str | None:
    """'22587682[PMID]_11309371[PMID]' -> 'pmid:22587682 | pmid:11309371'.

    Other sources ('ISBN:...[OTHER]') are kept as written without the tag. Orphanet cuts
    long lists at 999 characters, so an item cut short (no closing tag) is left out.
    """
    out = []
    for item in (text or "").split("_"):
        m = _CITATION.match(item.strip())
        if m:
            out.append(f"pmid:{m.group(1)}" if m.group(2) == "PMID" else m.group(1))
    return names(*out)


#: Association types that are not a confirmed causal role, whatever the assessment
#: status: a candidate gene was tested without causality being confirmed, and a biomarker
#: is a non-causal association. Other types (disease-causing, susceptibility factor,
#: modifier, fusion gene, role in the phenotype) take their evidence from the status.
_ORPHA_EVIDENCE = {"candidate gene tested in": "reported",
                   "biomarker tested in": "associated"}


def _orphadata(conn) -> Iterator[Row | dict | None]:
    if has(conn, "gene_association"):
        for r in rows(conn, "SELECT * FROM gene_association"):
            symbol = _val(r["GeneSymbol"])
            if not symbol:
                yield unresolved("target_disease", "orphadata", None, r["GeneName"],
                                 r["Name"], "gene without a symbol",
                                 reference=f"orpha:{r['OrphaCode']}")
                continue
            status = _val(r["DisorderGeneAssociationStatus"])
            kind = _val(r["DisorderGeneAssociationType"])
            yield rel("target_disease", "orphadata", f"symbol:{symbol}",
                      names(symbol, r["GeneName"]), f"orpha:{r['OrphaCode']}", r["Name"],
                      _ORPHA_EVIDENCE.get((kind or "").strip().lower())
                      or ("known" if status == "Assessed" else "reported"),
                      reference=_citations(r["SourceOfValidation"]),
                      context=ctx(mechanism=kind, qc=status))
    if has(conn, "phenotype"):
        for r in rows(conn, "SELECT * FROM phenotype"):
            hpo = _val(r["HPOId"]) or ""
            frequency = _val(r["HPOFrequency"])
            excluded = (frequency or "").lower().startswith("excluded")
            yield rel("disease_phenotype", "orphadata", f"orpha:{r['OrphaCode']}", r["Name"],
                      f"hp:{hpo.split(':', 1)[1]}" if hpo.startswith("HP:") else None,
                      r["HPOTerm"], "known", outcome="negative" if excluded else "positive",
                      reference=_citations(r["Source"]),
                      context=ctx(measure="frequency" if frequency else None,
                                  value=frequency,
                                  flags=[_val(r["DiagnosticCriteria"])]
                                  if _val(r["DiagnosticCriteria"]) else None))


# ------------------------------------------------------------------------- datasets
_CLOWDER = "https://clowder.edap-cluster.com/api/files/"
#: Short on purpose: every relation row carries its licence text.
_TOXCAST_LICENCE = "CC0 1.0 (US EPA open data)"
_AOP = "https://aopwiki.org/downloads/"
_ORPHA = "https://www.orphadata.com/data/xml/"
_ORPHA_LICENCE = "CC BY 4.0 (Orphadata Science; attribution required)"
_NHANES = "https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/"
_NHANES_LICENCE = ("US public domain (CDC/NCHS: attribute the agency, no endorsement implied, "
                   "do not alter the substantive content) under the NCHS Data User "
                   "Agreement: statistical reporting and analysis only, no attempt to "
                   "identify any person, no linkage with individually identifiable data.")

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "comptox", "EPA ToxCast invitrodb v4.3 (CompTox)", (100,),
        "https://www.epa.gov/comptox-tools/exploring-toxcast-data", _TOXCAST_LICENCE,
        (FileSpec(_CLOWDER + "6a85a260e4b0731a36bd04cc/blob",
                  "pubchem_invitrodb_v4_3_19AUG2026.zip", "hitcall", fmt="toxcast_pubchem",
                  expected_bytes=192901527, sheet="Results",
                  note="EPA's PubChem-deposition tables, one workbook per assay endpoint "
                       "(1,536 endpoints): sample id, PubChem outcome (1 inactive, 2 active, "
                       "3 negative hit call), the DTXSID from the CompTox URL, AC50 (uM), "
                       "continuous hit call, BMD (uM)."),
         FileSpec(_CLOWDER + "68af6bd3e4b02565fc7c3aa8/blob",
                  "assay_annotations_invitrodb_v4_3_AUG2025.xlsx", "assay_endpoint",
                  fmt="toxcast_xlsx", sheet="annotations_combined", expected_bytes=1088201,
                  note="Assay, component and endpoint annotations (1,647 endpoints)."),
         FileSpec(_CLOWDER + "68af6bd3e4b02565fc7c3aa0/blob",
                  "assay_target_mappings_invitrodb_v4_3_AUG2025.xlsx", "assay_target",
                  fmt="toxcast_xlsx", expected_bytes=97652,
                  note="Endpoint -> Entrez gene, AOP-Wiki AOP and key-event ids; '#N/A' is "
                       "missing."),
         FileSpec(_CLOWDER + "68af6bd3e4b02565fc7c3aa4/blob",
                  "cytotox_invitrodb_v4_3_AUG2025.xlsx", "cytotox", fmt="toxcast_xlsx",
                  expected_bytes=943302,
                  note="Cytotoxicity burst per chemical over the burst endpoints; 1000 uM "
                       "(3 log uM) is the default when under 5% of them hit."),
         FileSpec(_CLOWDER + "68af6bd3e4b02565fc7c3ab8/blob",
                  "analytical_qc_invitrodb_v4_3_AUG2025.xlsx", "analytical_qc",
                  fmt="toxcast_xlsx", expected_bytes=2915329,
                  note="Analytical QC per substance and sample (pass/caution, stability)."),
         FileSpec(_CLOWDER + "69529775e4b0731a616efc4b/blob",
                  "DSSTox_CCD_dump_12092025_CSVs.zip", "dsstox", fmt="dsstox_csv_zip",
                  expected_bytes=289824966,
                  note="EPA's DSSTox dump of December 2025 (CompTox Chemicals Dashboard, "
                       "CC0, linked from figshare 10.23645/epacomptox.5588566): DTXSID, "
                       "preferred name and InChIKey are kept, for the compound crosswalk."),
         FileSpec(_CLOWDER + "697b7530e4b0731a6170449e/blob", "DB_release_README_SUMMARY.pdf",
                  "", fmt="raw", expected_bytes=171736,
                  note="Column definitions of every summary file."),
         FileSpec(_CLOWDER + "68af6b70e4b02565fc7c3a98/blob", "INVITRODB_SUMMARY.zip", "",
                  fmt="raw", optional=True, expected_bytes=7502075199,
                  note="7.5 GB of summary files (mc5-6 fits with mc6 flags, mc7 AEDs, "
                       "endocrine models), mostly .RData; not fetched by default.")),
        version="invitrodb v4.3 (August 2025); PubChem deposition of 2026-08-19",
        notes="ToxCast/Tox21 high-throughput concentration-response screening, processed "
              "by EPA's tcpl pipeline. One compound_assay row per tested sample and "
              "endpoint: the subject is the chemical's DSSTox id (comptox:DTXSID...), the "
              "object the endpoint (comptox:aeid.<aeid>), the score the continuous hit "
              "call. Outcome is the deposition's own PubChem code: 2 active (hitc >= 0.9) "
              "-> positive, 1 inactive (0 <= hitc < 0.9) -> negative, and 3 -> negative "
              "with the context flag negative_hitc_unintended_direction (on these files "
              "code 3 marks a negative hit call, between -1 and about 0: a fit in the "
              "direction the endpoint does not test; the README counts every hitc < 0.9 "
              "as inactive). Evidence is 'known' (measured). Active rows carry "
              "the AC50 (uM) and the flag ac50_above_cytotox_lower_bound when the AC50 lies "
              "above the chemical's cytotoxicity-burst lower bound (not set on the burst "
              "endpoints themselves). The context gives the sample id, the sample's "
              "analytical QC call (or the substance's when the sample has none) as qc, and "
              "the endpoint's taxon, cell line, tissue and time point. Several samples of "
              "one chemical are kept as separate rows. Endpoint annotations give "
              "assay_target (endpoint -> ncbigene) and assay_event (endpoint -> AOP-Wiki key "
              "event, the same aopwiki:event ids as the aopwiki dataset), both 'listed'; "
              "the endpoint -> AOP mappings stay in the assay_target table. Rows from Tox21 "
              "endpoints say 'via Tox21'. The same records reach PubChem BioAssay (this "
              "archive is EPA's deposit; NCATS also deposits Tox21) and the CompTox "
              "dashboard. Licence: the figshare record 10.23645/epacomptox.6062623.v14 is "
              "CC0, the Clowder datasets declare a Public Domain Dedication, and EPA calls "
              "the data 'free of all copyright restrictions ... for both non-commercial and "
              "commercial use'. Compound crosswalk: the DSSTox dump (December 2025, "
              "CC0) maps the tested DTXSIDs to InChIKeys (8,931 of 9,614; substances "
              "without a "
              "defined structure, such as mixtures and polymers, have none and stay "
              "comptox ids); its preferred names fill the chemical names. Store size: "
              "about 1.9 GB (3.3M hit-call rows kept for rebuilds, as DTXSID rather than "
              "URL, beside 3.3M relation rows and a 3-column DSSTox table). The CTX "
              "Bioactivity API needs a personal key (not wrapped); "
              "Clowder has no robots.txt and answers anonymous downloads.",
        relations=("compound_assay", "assay_target", "assay_event"),
        commercial_use="allowed", upstream=("Tox21",),
        crosswalk={"gene": "SELECT DISTINCT 'ncbigene:' || target_id, 'symbol:' || "
                           "official_symbol FROM assay_target WHERE target_type = "
                           "'entrez_gene_id' AND ncbi_taxon_id = '9606' AND "
                           "official_symbol NOT IN ('#N/A', '')",
                   "compound": "SELECT DISTINCT 'comptox:' || dtxsid, 'inchikey:' || "
                               "inchikey FROM dsstox WHERE length(inchikey) = 27 AND "
                               "dtxsid IN (SELECT dtxsid FROM hitcall)"}),
    DatasetSpec(
        "aopwiki", "AOP-Wiki (OECD AOP Knowledge Base)", (101,), "https://aopwiki.org/",
        "CC BY-SA 2.0 (AOP-Wiki default for all content); AOP pages an author marked "
        "'All rights reserved' keep that licence on their rows",
        tuple(FileSpec(_AOP + "aop-wiki-xml-2026-10-01.gz", "aop-wiki-xml-2026-10-01.gz",
                       table, fmt="aopwiki_xml", expected_bytes=10559600)
              for table in _AOP_TABLES)
        + tuple(FileSpec(_AOP + name, name, table, optional=True, columns=cols,
                         note="Regenerated nightly with no archive; not used for relations.")
                for name, table, cols in (
                    ("aop_ke_mie_ao.tsv", "nightly_aop_event",
                     ("aop", "event", "event_type", "event_name")),
                    ("aop_ke_ker.tsv", "nightly_aop_ker",
                     ("aop", "upstream_event", "downstream_event", "relationship",
                      "adjacency", "evidence", "quantitative_understanding")),
                    ("aop_ke_ec.tsv", "nightly_event_component",
                     ("aop", "event", "action", "object_source", "object_id", "object_term",
                      "process_source", "process_id", "process_term")))),
        version="quarterly XML 2026-10-01 (AOP-Wiki release 2.8)",
        notes="Expert-authored adverse outcome pathways. key_event_relationship: one row "
              "per AOP and key-event relationship (upstream -> downstream event), with the "
              "AOP's weight-of-evidence call as context confidence (High/Moderate/Low or Not "
              "Specified), adjacency as context direct, the quantitative understanding as "
              "qc, the KER id as source_id and the AOP as reference. Evidence is 'known' "
              "when the weight of evidence was assessed (High/Moderate/Low) and 'reported' "
              "when it is Not Specified. aop_event lists each AOP's events (note: "
              "MolecularInitiatingEvent, KeyEvent or AdverseOutcome; 'listed'). "
              "stressor_aop links each stressor an AOP names to the AOP, with the same "
              "evidence rule; a chemical stressor is its DSSTox id (comptox:DTXSID...), "
              "otherwise the stressor (aopwiki:stressor.<n>); the kind is stressor -> aop "
              "and chemical rows say subject_type 'compound'. stressor_event is filled "
              "only from key-event stressors, which this release does not export. The XML "
              "leaves out key events and KERs that belong to no AOP. Rows from AOPs marked "
              "'All rights reserved' carry that licence and are left out of commercial "
              "queries (30 of 599 AOPs on 2026-10-01). AOPs marked 'BY-SA' or with no "
              "value are CC BY-SA, version 2.0 per the handbook's link (share-alike: "
              "derived tables keep the licence). Any other value, such as 'Open for "
              "adoption' (an authorship status, 41 AOPs), grants no licence: those rows "
              "say the licence is not stated and are left out of commercial queries. OECD "
              "status, when set, is in the note; AOPs 'under development' should not be "
              "cited (FAQ). robots.txt restricts nothing.",
        relations=("key_event_relationship", "aop_event", "stressor_aop", "stressor_event"),
        commercial_use="allowed",
        crosswalk={"compound": "SELECT DISTINCT 'comptox:' || dsstox_id, 'inchikey:' || "
                               "coalesce(jchem_inchi_key, indigo_inchi_key) FROM chemical "
                               "WHERE dsstox_id IS NOT NULL AND length(coalesce("
                               "jchem_inchi_key, indigo_inchi_key)) = 27 AND ("
                               "jchem_inchi_key IS NULL OR indigo_inchi_key IS NULL OR "
                               "jchem_inchi_key = indigo_inchi_key)"}),
    DatasetSpec(
        "orphadata", "Orphadata Science (Orphanet)", (102,),
        "https://sciences.orphadata.com/", _ORPHA_LICENCE,
        (FileSpec(_ORPHA + "en_product6.xml", "en_product6.xml", "gene_association",
                  fmt="orphadata_xml", note="Rare diseases and associated genes."),
         FileSpec(_ORPHA + "en_product4.xml", "en_product4.xml", "phenotype",
                  fmt="orphadata_xml", note="Rare diseases with HPO phenotypes and "
                                             "frequencies."),
         FileSpec(_ORPHA + "en_product1.xml", "en_product1.xml", "disorder",
                  fmt="orphadata_xml", note="Nomenclature: names and synonyms."),
         FileSpec(_ORPHA + "en_product1.xml", "en_product1.xml", "disorder_xref",
                  fmt="orphadata_xml", note="Cross-references (OMIM, ICD-10/11, MONDO, "
                                             "UMLS, MeSH, MedDRA, GARD)."),
         FileSpec(_ORPHA + "en_product9_prev.xml", "en_product9_prev.xml", "", fmt="raw",
                  optional=True, note="Epidemiology (prevalence)."),
         FileSpec(_ORPHA + "en_product9_ages.xml", "en_product9_ages.xml", "", fmt="raw",
                  optional=True, note="Natural history: onset, death, inheritance."),
         FileSpec("https://www.orphadata.com/data/ontologies/ordo/last_version/"
                  "ORDO_en_4.9.owl", "ORDO_en_4.9.owl", "", fmt="raw", optional=True,
                  note="Orphanet Rare Disease Ontology 4.9.")),
        version="Orphanet knowledge base, July 2026 release (JDBOR 2026-06-23)",
        notes="Orphanet's expert curation of the literature. target_disease: gene "
              "(symbol:<HGNC symbol>) -> disease (orpha:<ORPHAcode>), evidence 'known' when "
              "Orphanet assessed the association and 'reported' when it is 'Not yet "
              "assessed', except that a 'Candidate gene tested in' association is always "
              "'reported' (causality unconfirmed) and a 'Biomarker tested in' one is "
              "always 'associated' (non-causal); the association type (disease-causing "
              "germline mutation, loss or gain of function, susceptibility factor, "
              "candidate gene, biomarker, ...) is the context mechanism, the status is qc and the validating PMIDs are the "
              "reference. No effect is set: a loss-of-function association is not a "
              "direction of the gene on the disease. disease_phenotype: disease -> HPO term "
              "(hp:<digits>), 'known', with the frequency class as context (measure "
              "'frequency') and the disease's annotation sources (PMIDs) as reference; the "
              "'Excluded (0%)' class is a negative outcome. Diagnostic-criterion and "
              "pathognomonic marks are context flags. The JSON variants of these products "
              "return 404. Licence CC BY 4.0. Cite: 'Orphadata Science: Free access data "
              "from Orphanet. (c) INSERM 1999. Available on https://sciences.orphadata.com/."
              " Data version 2026-06-23.' A tool that offers results built on these data to "
              "third parties must say Orphanet data were used and that the results may not "
              "reflect them (Orphadata FAQ). Only the Science datasets are CC BY; orpha.net "
              "pages are not covered. Downstream copies (HPO annotations, MONDO, Open "
              "Targets, DisGeNET) take these records from Orphanet.",
        relations=("target_disease", "disease_phenotype"), commercial_use="allowed"),
    DatasetSpec(
        "nhanes", "NHANES 2017-2018 public-use files (CDC/NCHS)", (103,),
        "https://wwwn.cdc.gov/nchs/nhanes/", _NHANES_LICENCE,
        (FileSpec(_NHANES + "DEMO_J.xpt", "DEMO_J.xpt", "demographics", fmt="nhanes_xpt",
                  expected_bytes=3412720,
                  note="Demographics, interview and examination weights, PSU and strata."),
         FileSpec(_NHANES + "PFAS_J.xpt", "PFAS_J.xpt", "pfas", fmt="nhanes_xpt",
                  expected_bytes=344800,
                  note="Serum per- and polyfluoroalkyl substances (subsample weights)."),
         FileSpec(_NHANES + "DSQIDS_J.xpt", "DSQIDS_J.xpt", "supplement_ingredient",
                  fmt="nhanes_xpt", expected_bytes=8958720,
                  note="Dietary supplement use, ingredient level (botanicals included).")),
        version="2017-2018 cycle (J)",
        notes="Participant-level survey data joined on SEQN. NHANES states no relations; "
              "an association (an exposure biomarker with an outcome) must be estimated "
              "with the survey design (WTMEC2YR or the subsample weight, SDMVPSU, SDMVSTRA) "
              "and stays the user's analysis, so this dataset yields none. Files are SAS "
              "transport v5 served as text/plain; numbers are written as decimal text and "
              "SAS missing values as empty. Zeros are stored as the smallest IBM float, "
              "which decodes as 5.4e-79; the reader writes them as 0. Other cycles and components follow the URL pattern "
              "Public/<cycle start year>/DataFiles/<TABLE>_<cycle letter>.xpt (read the "
              "letter from the datapage index). 'RDC Only' variables are not public.",
        commercial_use="allowed"),
)

EXTRACTORS = {"comptox": _comptox, "aopwiki": _aopwiki, "orphadata": _orphadata}
