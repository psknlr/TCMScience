"""Imaging archives: which collections and screens exist, and what the screens found.

No image is downloaded. Two archives publish metadata files a snapshot can pin:

* **NCI Imaging Data Commons** (``idc``): the ``idc-index-data`` wheel that the IDC's own
  ``idc-index`` package installs (PyPI, MIT, 77 MB). It holds the collection, analysis
  result and release tables as parquet, and the per-series index (1.03 M series, 73 MB),
  which stays in the wheel unloaded: the live connector ``idc`` answers series queries
  (``sql``). Relations: an imaging collection and the cancer types it covers
  (``dataset_disease``, listed). Every collection lists its sources, each with its DOI
  and licence; the licences go into the row's note, and a collection holding
  non-commercial series (CC BY-NC) or series under the NLM terms is flagged. Most
  collections are TCIA collections re-hosted (DOI prefix 10.7937): those rows say
  ``via TCIA``, so an IDC row and a TCIA record of the same collection count once.
* **Image Data Resource** (``idr``): per-study exports of the IDR searcher (static files,
  one per study): the MitoCheck genome-wide RNAi screen (idr0013 screen A, CC0) and the
  pericentriolar-material localisation study (idr0021, CC BY 4.0). Relations: a gene and
  the CMPO phenotype its knock-down (or its protein's localisation) showed
  (``gene_phenotype``, known), and MitoCheck's gene-level hit calls, hits and non-hits
  (``screen_gene``). Rows carry their study's licence.

Format notes. The IDC tables are members of a wheel (a zip with many members), so this
module reads them itself (``idc_wheel``). An IDR export writes one row per image, the
study's annotation repeated on each; values a merged row holds several times are joined
with commas (``ENSG…,ENSG…``), and the CSV export writes ``None`` for a missing value.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec, licence_class
from ..store import StoreError

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

#: An imaging, single-cell or cohort data collection and a disease it studies. No
#: registered kind joins a dataset to a disease; the subject is a collection, not a
#: compound, gene or target.
KINDS = {"dataset_disease": ("dataset", "disease")}

# ----------------------------------------------------------------------------- IDC
_IDC_VERSION = "24.2.2"
_IDC_WHEEL = ("https://files.pythonhosted.org/packages/70/b3/"
              "3adf736fa7db08e64dbbc04084a48054c18cc08822bee75ae5d6f7832f1c/"
              f"idc_index_data-{_IDC_VERSION}-py3-none-any.whl")
_IDC_WHEEL_NAME = f"idc_index_data-{_IDC_VERSION}-py3-none-any.whl"
#: table -> parquet member of the wheel
_IDC_MEMBERS = {"idc_collections": "collections_index.parquet",
                "idc_collection_sources": "collections_index.parquet",
                "idc_analysis_results": "analysis_results_index.parquet",
                "idc_versions": "version_metadata_index.parquet"}
_SOURCE_COLUMNS = ("collection_id", "source_id", "source_type", "source_doi", "source_url",
                   "source_title", "access", "image_types", "modalities",
                   "license_short_name", "license_long_name", "license_url", "citation")
#: TCIA registers its dataset DOIs under this DataCite prefix.
_TCIA_DOI = "10.7937/"
_SPECIES = {"human": "9606", "mouse": "10090", "canine": "9615", "homo sapiens": "9606"}
#: cancer_types values that name no disease (a phantom, healthy subjects)
_NO_DISEASE = frozenset({"phantom", "non-cancer", "non-diseased", "normal (non-cancer)",
                         "healthy controls (non-cancer)", "pathologically benign"})
#: values that name diseases without saying which
_UNSPECIFIED = frozenset({"various"})


def _cell(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    text = str(value).strip()
    return text or None


def _wheel_member(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        found = [n for n in archive.namelist() if n.rsplit("/", 1)[-1] == name]
        if len(found) != 1:
            raise StoreError(f"{path.name}: {len(found)} members named {name}")
        return archive.read(found[0])


def _idc_wheel(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One parquet table of the idc-index-data wheel, chosen by the spec's table.

    ``idc_collection_sources`` is the collections table's ``sources`` list, one row per
    source of a collection (its DOI, type, access and licence); the collections table
    keeps the list as written, as JSON.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    member = _IDC_MEMBERS.get(spec.table)
    if member is None:
        raise StoreError(f"{path.name}: no wheel member for table {spec.table!r}")
    table = pq.read_table(pa.BufferReader(_wheel_member(path, member)))
    if spec.table == "idc_collection_sources":
        yield list(_SOURCE_COLUMNS)
        for rec in table.select(["collection_id", "sources"]).to_pylist():
            for src in rec["sources"] or ():
                lic = src.get("license") or {}
                yield [_cell(x) for x in (
                    rec["collection_id"], src.get("source_id"), src.get("source_type"),
                    src.get("source_doi"), src.get("source_url"), src.get("source_title"),
                    src.get("Access"), src.get("ImageTypes"), src.get("modalities"),
                    lic.get("license_short_name"), lic.get("license_long_name"),
                    lic.get("license_url"), src.get("citation"))]
        return
    yield list(table.column_names)
    for batch in table.to_batches(max_chunksize=5000):
        cols = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
        for values in zip(*cols):
            yield [_cell(x) for x in values]


def _cancer_types(text: str | None) -> list[str]:
    """IDC's comma-separated cancer types; "…, Not Otherwise Specified" is one type."""
    out: list[str] = []
    for part in (p.strip() for p in (text or "").split(",")):
        if not part:
            continue
        if out and (part.lower() == "nos" or part.lower().startswith("not otherwise")):
            out[-1] = f"{out[-1]}, {part}"
        else:
            out.append(part)
    return out


def _disease_id(name: str) -> str:
    core = re.sub(r"\s*\(non-cancer\)\s*$", "", name, flags=re.I).strip().lower()
    return "idc:disease." + re.sub(r"[^0-9a-z]+", "_", core).strip("_")


def _idc(conn: Any) -> Iterator[Row | None]:
    if not has(conn, "idc_collections"):
        return
    sources: dict[str, list[Any]] = defaultdict(list)
    if has(conn, "idc_collection_sources"):
        for s in rows(conn, "SELECT * FROM idc_collection_sources"):
            sources[s["collection_id"]].append(s)
    for r in rows(conn, "SELECT * FROM idc_collections"):
        cid = v(r["collection_id"])
        if not cid:
            continue
        srcs = sources.get(cid, [])
        original = [s for s in srcs if v(s["source_type"]) == "original_data"]
        dois = [f"doi:{d.lower()}" for s in original if (d := v(s["source_doi"]))]
        licences = sorted({lic for s in srcs if (lic := v(s["license_short_name"]))})
        classes = {licence_class(lic) for lic in licences}
        flags = names("non-commercial series" if "non-commercial" in classes else None,
                      "series under terms that grant no recognisable licence"
                      if "unknown" in classes else None)
        species = names(*(_SPECIES.get(x.strip().lower(), x.strip())
                          for x in (v(r["species"]) or "").split(",")
                          if x.strip().lower() != "phantom"))
        via = "TCIA" if any(d.startswith("doi:" + _TCIA_DOI) for d in dois) else None
        note = "; ".join(x for x in (
            f"series licences: {', '.join(licences)}" if licences else None,
            f"via {via}" if via else None) if x)
        context = ctx(species=species, tissue=v(r["tumor_locations"]),
                      n=v(r["subjects"]), flags=flags, dataset=v(r["collection_name"]),
                      method=names(*(v(s["modalities"]) for s in original)))
        subject = (f"idc:{cid}", names(r["collection_name"], cid))
        for disease in _cancer_types(v(r["cancer_types"])):
            low = disease.lower()
            if low in _NO_DISEASE:
                continue                       # a phantom or healthy subjects: no disease
            if low in _UNSPECIFIED:
                yield unresolved("dataset_disease", "idc", subject[0], subject[1], disease,
                                 "cancer type given only as 'Various'",
                                 reference=names(*dois), note=note or None)
                continue
            yield rel("dataset_disease", "idc", subject[0], subject[1], _disease_id(disease),
                      disease, "listed", reference=names(*dois), note=note or None,
                      context=context)


# ----------------------------------------------------------------------------- IDR
_IDR = ("https://idr.openmicroscopy.org/searchengine/api/v1/resources/container_data/"
        "?container_name={name}&container_type={kind}&data_source=idr&file_type={fmt}")

#: table -> the study it exports, as the study's IDR annotation states it
_IDR_STUDIES: dict[str, dict[str, str]] = {
    "idr0013_mitocheck": {
        "container": "idr0013-neumann-mitocheck/screenA", "license": "CC0 1.0",
        "pmid": "20360735", "title": "MitoCheck genome-wide RNAi screen",
        "method": "RNAi (siRNA); live-cell time-lapse fluorescence microscopy (H2B-GFP)"},
    "idr0021_pcm": {
        "container": "idr0021-lawo-pericentriolarmaterial/experimentA",
        "license": "CC BY 4.0", "pmid": "23086237",
        "title": "Pericentriolar material localisation (Lawo et al.)",
        "method": "immunofluorescence; structured illumination microscopy (SIM)"},
}
_POTENTIAL = "potential mitotic hit (primary screen, gene level)"
_VALIDATED = "validated mitotic hit (gene level)"


def _values(value: Any) -> list[str]:
    """The distinct values of a cell; a merged export row repeats them with commas."""
    out: list[str] = []
    for part in (v(value) or "").split(","):
        part = part.strip()
        if v(part) and part not in out:
            out.append(part)
    return out


def _one(value: Any) -> str | None:
    """The cell's value when it holds exactly one, else ``None`` (absent or ambiguous)."""
    vals = _values(value)
    return vals[0] if len(vals) == 1 else None


def _qc_passed(row: Any) -> bool:
    """True unless the row's Quality Control says it failed (all parts must pass)."""
    qc = _values(_get(row, "Quality_Control"))
    return all(q.lower() == "true" for q in qc)


def _get(row: Any, column: str) -> Any:
    return row[column] if column in row.keys() else None


def _species(row: Any) -> str | None:
    org = _one(_get(row, "Organism"))
    return _SPECIES.get(org.lower(), org) if org else None


def _idr_phenotypes(conn: Any, table: str, study: dict[str, str]) -> Iterator[Row | None]:
    """Gene -> CMPO phenotype, one row per reagent (siRNA or antibody) and term."""
    queued: set[tuple] = set()
    for r in rows(conn, f'SELECT * FROM "{table}"'):
        accessions = _values(_get(r, "Phenotype_Term_Accession"))
        if not accessions or v(_get(r, "Control_Type")) or not _qc_passed(r):
            continue
        labels = _values(_get(r, "Phenotype_Term_Name"))
        labels = labels if len(labels) == len(accessions) else [None] * len(accessions)
        sirna = _one(_get(r, "Mitocheck_siRNA_Identifier"))
        antibody = _one(_get(r, "Antibody_Target"))
        gene, symbol = _one(_get(r, "Gene_Identifier")), _one(_get(r, "Gene_Symbol"))
        # IDR marks a reagent's phenotype with Has Phenotype = yes at the annotation level
        # 'multiple replicates of reagent'; a term without that call (MitoCheck's
        # automatic 'cell death' calls) is kept as inconclusive rather than as a finding
        confirmed = (_one(_get(r, "Has_Phenotype")) or "").lower() == "yes"
        context = ctx(species=_species(r), cell=_one(_get(r, "Cell_Line")),
                      method=study["method"], screen=study["container"],
                      source_id=sirna, assay=f"antibody {antibody}" if antibody else None,
                      stage=_one(_get(r, "Cell_Cycle_Phase")),
                      phenotype=v(_get(r, "Phenotype")),
                      flags=None if confirmed else "Has Phenotype not set (no replicate-"
                                                   "level call)")
        for acc, label in zip(accessions, labels):
            if not gene or not gene.startswith("ENSG"):
                if (sirna, symbol, acc) in queued:      # the same reagent's other wells
                    continue
                queued.add((sirna, symbol, acc))
                yield unresolved("gene_phenotype", "idr",
                                 f"idr:sirna.{sirna}" if sirna else None,
                                 symbol or _one(_get(r, "Original_Gene_Target")), label or acc,
                                 "the reagent maps to no single Ensembl gene",
                                 reference=f"pmid:{study['pmid']}", note=study["container"])
                continue
            yield rel("gene_phenotype", "idr", f"ensembl:{gene}", symbol, f"cmpo:{acc}",
                      label, "known", reference=f"pmid:{study['pmid']}",
                      outcome="positive" if confirmed else "inconclusive", context=context,
                      license=study["license"])


def _idr_hits(conn: Any, table: str, study: dict[str, str]) -> Iterator[Row | None]:
    """MitoCheck's gene-level calls: hits, and genes tested without a hit.

    A gene is tested when at least one non-control well with an siRNA mapped to it passed
    quality control. A potential hit is a hit of the primary screen; a gene with tested
    wells and no such call is a non-hit (outcome negative). Validation was run on hits
    only and the export does not say which of them were retested, so a validated hit is
    a positive row and an unvalidated one gets no row.
    """
    cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')}
    if "Potential_Mitotic_Hit_At_Gene_Level" not in cols:
        return
    genes: dict[str, dict[str, Any]] = {}
    for r in rows(conn, f'SELECT * FROM "{table}"'):
        gene = _one(r["Gene_Identifier"])
        if not gene or not gene.startswith("ENSG") or v(r["Control_Type"]):
            continue
        g = genes.setdefault(gene, {"symbol": None, "sirnas": set(), "potential": False,
                                    "validated": False})
        g["symbol"] = g["symbol"] or _one(r["Gene_Symbol"])
        g["potential"] |= "yes" in _values(r["Potential_Mitotic_Hit_At_Gene_Level"])
        g["validated"] |= "yes" in _values(r["Validated_Mitotic_Hit_At_Gene_Level"])
        sirna = _one(r["Mitocheck_siRNA_Identifier"])
        if sirna and _qc_passed(r):
            g["sirnas"].add(sirna)
    screen = (f"idr:{study['container']}", f"{study['title']} ({study['container']})")
    for gene, g in sorted(genes.items()):
        base = dict(species="9606", cell="HeLa", method=study["method"],
                    screen=study["container"])
        if g["potential"] or g["sirnas"]:
            yield rel("screen_gene", "idr", screen[0], screen[1], f"ensembl:{gene}",
                      g["symbol"], "known", reference=f"pmid:{study['pmid']}",
                      outcome="positive" if g["potential"] else "negative",
                      context=ctx(**base, measure=_POTENTIAL, n=len(g["sirnas"]) or None),
                      license=study["license"])
        if g["validated"]:
            yield rel("screen_gene", "idr", screen[0], screen[1], f"ensembl:{gene}",
                      g["symbol"], "known", reference=f"pmid:{study['pmid']}",
                      context=ctx(**base, measure=_VALIDATED), license=study["license"])


def _idr(conn: Any) -> Iterator[Row | None]:
    for table, study in _IDR_STUDIES.items():
        if has(conn, table):
            yield from _idr_phenotypes(conn, table, study)
            yield from _idr_hits(conn, table, study)


# --------------------------------------------------------------------------- specs
DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "idc", "NCI Imaging Data Commons index (idc-index-data)", (129,),
        "https://portal.imaging.datacommons.cancer.gov/", "MIT (idc-index-data package)",
        tuple(FileSpec(_IDC_WHEEL, _IDC_WHEEL_NAME, table, fmt="idc_wheel",
                       expected_bytes=77341561, note=note)
              for table, note in (
                  ("idc_collections", "collections_index.parquet: 176 collections with "
                   "cancer types, tumour locations, species, subjects and sources"),
                  ("idc_collection_sources", "the collections' sources, one row each: "
                   "DOI, type (original data or analysis result), access, licence"),
                  ("idc_analysis_results", "analysis_results_index.parquet: segmentations "
                   "and annotations with their source collections, DOI and licence"),
                  ("idc_versions", "version_metadata_index.parquet: IDC releases")))
        + (FileSpec(_IDC_WHEEL, _IDC_WHEEL_NAME, "", fmt="raw", expected_bytes=77341561,
                    note="idc_index.parquet (1.03 M series: PatientID, Study/Series UIDs, "
                         "modality, body part, licence, size, S3 URL) stays in the wheel; "
                         "read it with pyarrow or DuckDB, or query the live connector"),),
        version=f"IDC v24 (idc-index-data {_IDC_VERSION}, 2026-07-11)",
        notes="Which public cancer imaging collections exist (radiology and slide "
              "microscopy) and the cancer types each covers, as IDC's curated collection "
              "table lists them (evidence 'listed': a collection's stated scope, not a "
              "finding). The metadata are MIT; the images are licensed per series, and a "
              "row's note lists its collection's series licences (CC BY 4.0, CC BY 3.0, "
              "CC BY-NC 4.0, CC BY-NC 3.0, the NLM Visible Human terms); flags say when "
              "any is non-commercial. Collections whose cancer type is a phantom or "
              "healthy subjects give no row; 'Various' goes to the unresolved queue. The "
              "segmentation, slide-microscopy and clinical indices are GitHub release "
              "assets not in the wheel (reach them with the live connector's sql "
              "operation). No image is fetched.",
        relations=("dataset_disease",), commercial_use="allowed",
        upstream=("TCIA", "GDC", "CPTAC", "HTAN", "GTEx", "NLM Visible Human Project")),
    DatasetSpec(
        "idr", "Image Data Resource study exports", (132,),
        "https://idr.openmicroscopy.org/",
        "CC0 1.0 (idr0013 MitoCheck); CC BY 4.0 (idr0021); per study",
        (FileSpec(_IDR.format(name="idr0013-neumann-mitocheck/screenA", kind="screen",
                              fmt="parquet"),
                  "idr0013-neumann-mitocheck_screenA.parquet", "idr0013_mitocheck",
                  fmt="parquet", expected_bytes=128248107, license="CC0 1.0",
                  note="one row per well image (191,368): siRNA, gene, QC, CMPO phenotypes, "
                       "gene-level hit calls"),
         FileSpec(_IDR.format(name="idr0021-lawo-pericentriolarmaterial/experimentA",
                              kind="project", fmt="csv"),
                  "idr0021-lawo-pericentriolarmaterial_experimentA.csv", "idr0021_pcm",
                  fmt="csv", expected_bytes=301670, license="CC BY 4.0",
                  note="one row per image (414): antibody, gene, localisation phenotype")),
        version="IDR exports of 2026-06-23 (searcher 0.8.2)",
        notes="Curated key-values of two IDR studies, as the IDR searcher exports them. "
              "gene_phenotype: a gene whose siRNA knock-down (MitoCheck) or whose "
              "protein's localisation (idr0021) showed a CMPO phenotype, one row per "
              "reagent and term (evidence 'known'; a term IDR did not confirm across "
              "replicates is 'inconclusive'). screen_gene: MitoCheck's gene-level calls, "
              "hits and tested non-hits (outcome negative), and validated hits. Genes are "
              "Ensembl ids with the study's symbols (Ensembl 53 for MitoCheck, 84 for "
              "idr0021). Controls and wells that failed QC give no row; a reagent mapped "
              "to no single gene goes to the unresolved queue. Other studies (some NC or "
              "ND licensed) are reachable through the live connector 'idr'.",
        relations=("gene_phenotype", "screen_gene"),
        relation_licenses={"screen_gene": "CC0 1.0"},
        commercial_use="allowed", upstream=("MitoCheck",),
        # the studies' own Ensembl -> symbol pairs; names that are no HGNC-style symbol
        # (clone ids such as AC012345.1, UniProt names such as Q6ZUQ5_HUMAN) are left out
        crosswalk={"gene": "SELECT DISTINCT subject_id, 'symbol:' || subject_name "
                           "FROM relations WHERE kind = 'gene_phenotype' "
                           "AND subject_id LIKE 'ensembl:ENSG%' AND subject_name IS NOT NULL "
                           "AND instr(subject_name, '.') = 0 AND instr(subject_name, '_') = 0 "
                           "UNION SELECT DISTINCT object_id, 'symbol:' || object_name "
                           "FROM relations WHERE kind = 'screen_gene' "
                           "AND object_id LIKE 'ensembl:ENSG%' AND object_name IS NOT NULL "
                           "AND instr(object_name, '.') = 0 AND instr(object_name, '_') = 0"}),
)

EXTRACTORS = {"idc": _idc, "idr": _idr}
READERS = {"idc_wheel": _idc_wheel}
