"""Declarative acquisition specs for open bulk datasets.

Each spec is what a manifest needs to make a dataset *fetchable*: where it lives,
how big it is, how to verify it, and what license governs reuse. Checksums are
recorded on first verified fetch (`.downloads.json`) and pinned here once known;
until then a fetch is size-verified against the server's Content-Length.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping

from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions, Provider,
                                 Requirements, RuntimeSpec, Validation)
from ..providers.base import Provider as ProviderBase


@dataclass(frozen=True)
class AcquisitionSpec:
    url: str
    filename: str
    host: str
    license: str
    description: str
    domain: str
    strategy: str = "single_file"          # single_file | per_file_bucket | api_paginated
    expected_bytes: int | None = None
    checksum: str | None = None
    fmt: str = "tsv"
    source_project: str = "public-data"
    version: str = "current"
    notes: str = ""


#: Open bulk datasets with stable URLs. Sizes were read from the servers when
#: this table was written; the downloader re-reads them at fetch time.
ACQUIRABLE: tuple[AcquisitionSpec, ...] = (
    AcquisitionSpec(
        "https://rest.uniprot.org/uniprotkb/stream?query=organism_id:9606+AND+reviewed:true"
        "&format=list",
        "uniprot_human_reviewed_accessions.txt", "rest.uniprot.org", "CC-BY-4.0",
        "UniProtKB/Swiss-Prot human entries, one primary accession per line: the protein "
        "namespace NPASS, CMAUP, BindingDB, Reactome and Open Targets share. STRING's alias "
        "file lists secondary and TrEMBL accessions beside the primary without marking it, "
        "so mapping PubChem's gene ids through STRING needs this list to pick the primary.",
        "proteomics", fmt="tsv", source_project="UniProt"),
    AcquisitionSpec(
        "https://public-download-files.storage.googleapis.com/hgnc/tsv/tsv/hgnc_complete_set.txt",
        "hgnc_complete_set.txt", "public-download-files.storage.googleapis.com", "CC0-1.0",
        "HGNC complete approved human gene nomenclature set (symbols, aliases, cross-references).",
        "genomics", fmt="tsv", source_project="HGNC"),
    AcquisitionSpec(
        "https://reactome.org/download/current/ReactomePathways.txt",
        "ReactomePathways.txt", "reactome.org", "CC0-1.0",
        "All Reactome pathways: stable id, name, species.", "pathways", fmt="tsv",
        source_project="Reactome", notes="headerless: pathway_id\\tname\\tspecies"),
    AcquisitionSpec(
        "https://reactome.org/download/current/Ensembl2Reactome.txt",
        "Ensembl2Reactome.txt", "reactome.org", "CC0-1.0",
        "Ensembl gene → Reactome pathway mapping (lowest-level pathways).", "pathways", fmt="tsv",
        source_project="Reactome", notes="headerless: ensembl_id\\tpathway_id\\turl\\tname\\tevidence\\tspecies"),
    AcquisitionSpec(
        "https://stringdb-downloads.org/download/protein.info.v12.0/9606.protein.info.v12.0.txt.gz",
        "9606.protein.info.v12.0.txt.gz", "stringdb-downloads.org", "CC-BY-4.0",
        "STRING v12 human protein identifiers and annotations.", "proteomics", fmt="tsv.gz",
        expected_bytes=1970090, checksum="sha256:144de4b0d98c6a7dfde6ddc2591cf88657f27b989eadff4f501450c3ed1f0f1c", source_project="STRING", version="12.0"),
    AcquisitionSpec(
        "https://stringdb-downloads.org/download/protein.links.v12.0/9606.protein.links.v12.0.txt.gz",
        "9606.protein.links.v12.0.txt.gz", "stringdb-downloads.org", "CC-BY-4.0",
        "STRING v12 human protein–protein interaction scores.", "proteomics", fmt="tsv.gz",
        expected_bytes=83164437, checksum="sha256:3e22f32572211aa341d5b4bd08d30c32e693e294603202120936872f87719d4f", source_project="STRING", version="12.0"),
    AcquisitionSpec(
        "https://gcp-public-data--gnomad.storage.googleapis.com/release/4.1/constraint/gnomad.v4.1.constraint_metrics.tsv",
        "gnomad.v4.1.constraint_metrics.tsv", "gcp-public-data--gnomad.storage.googleapis.com", "CC0-1.0",
        "gnomAD v4.1 per-gene constraint metrics (pLI, LOEUF, o/e).", "genomics", fmt="tsv",
        expected_bytes=95546041, checksum="sha256:68d8abdb7fc48f570869b02dfaa74b9fecaece7fcc5f301ddca40ec1ce12da00", source_project="gnomAD", version="4.1"),
    AcquisitionSpec(
        "https://ftp.ebi.ac.uk/pub/databases/chembl/ChEMBLdb/latest/chembl_uniprot_mapping.txt",
        "chembl_uniprot_mapping.txt", "ftp.ebi.ac.uk", "CC-BY-SA-3.0",
        "ChEMBL target → UniProt accession mapping.", "chemistry", fmt="tsv",
        expected_bytes=1254330, source_project="ChEMBL"),
    AcquisitionSpec(
        "https://rest.kegg.jp/list/pathway/hsa", "kegg_pathways_hsa.tsv", "rest.kegg.jp",
        "Academic use free; commercial requires license", "KEGG human pathway list (id, name).",
        "pathways", fmt="tsv", source_project="KEGG", notes="headerless: pathway_id\\tname"),
    # ------------------------------------------------------------ natural products
    # The TCM-facing resources (TCMSP, HERB, SymMap, BATMAN-TCM, ETCM) publish web pages,
    # not stable files: HERB's per-file URLs return HTML, BATMAN-TCM's download page
    # answered 503, and TCMSP/HIT/TCMBank did not answer at all when this table was
    # written. What *is* stable is the tranche below — NPASS and CMAUP (BIDD, NUS), whose
    # tables cover the same plants, ingredients and targets with activity values and
    # references; NP Atlas; and LOTUS's frozen Wikidata export. Sizes are the servers'
    # Content-Length at the time of writing; the LOTUS and CellMarker checksums are the
    # md5 values Zenodo publishes for each file.
    AcquisitionSpec(
        "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_generalInfo.txt",
        "NPASSv2.0_download_naturalProducts_generalInfo.txt", "bidd.group",
        "Free for academic use; see bidd.group/NPASS for terms",
        "NPASS 2.0 natural products: identifiers, names, ChEMBL/PubChem cross-references and "
        "organism/target/activity counts.", "natural-products", expected_bytes=9379911, checksum="sha256:4ec8f066abf7cebe8b172e1b16a6248f3b6a94efb00ed587f0a9489f5ca688bf",
        fmt="tsv", source_project="NPASS", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_species_pair.txt",
        "NPASSv2.0_download_naturalProducts_species_pair.txt", "bidd.group",
        "Free for academic use; see bidd.group/NPASS for terms",
        "NPASS 2.0 natural product → source organism pairs with isolation part, collection "
        "location and reference.", "natural-products", expected_bytes=80284064, checksum="sha256:acc4f806d48327c6f258ef75afa6b69ff7d362455dff35e3a720976d09543c3e",
        fmt="tsv", source_project="NPASS", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_activities.txt",
        "NPASSv2.0_download_naturalProducts_activities.txt", "bidd.group",
        "Free for academic use; see bidd.group/NPASS for terms",
        "NPASS 2.0 quantitative activities (IC50, Ki, MIC, ...) of natural products against "
        "targets and cell lines, with references.", "natural-products",
        expected_bytes=90364982, checksum="sha256:ced9ee333e7e5e12eb07d21d52ec20af777b6c429a6438ed9b402ca8fd547507", fmt="tsv", source_project="NPASS", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_speciesInfo.txt",
        "NPASSv2.0_download_naturalProducts_speciesInfo.txt", "bidd.group",
        "Free for academic use; see bidd.group/NPASS for terms",
        "NPASS 2.0 source organisms with NCBI taxonomy lineage (species, genus, family, "
        "kingdom).", "natural-products", expected_bytes=3631384, checksum="sha256:a934641ce11140938c72f0fb63bd775a153398e4caf429a4a171b563669fb65a", fmt="tsv",
        source_project="NPASS", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_structureInfo.txt",
        "NPASSv2.0_download_naturalProducts_structureInfo.txt", "bidd.group",
        "Free for academic use; see bidd.group/NPASS for terms",
        "NPASS 2.0 structures: InChI, InChIKey and SMILES per natural product.",
        "natural-products", expected_bytes=29748831, checksum="sha256:cbe688e9b6fdd0960c78d1a93d7f487ca4a2bbad3017275515c3d90d2a0f72fa", fmt="tsv", source_project="NPASS",
        version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/NPASS/downloadFiles/NPASSv2.0_download_naturalProducts_targetInfo.txt",
        "NPASSv2.0_download_naturalProducts_targetInfo.txt", "bidd.group",
        "Free for academic use; see bidd.group/NPASS for terms",
        "NPASS 2.0 targets: type, name, organism and UniProt accession.", "natural-products",
        expected_bytes=629317, checksum="sha256:d171c261e839a95ff6fdadeba6d1eeacf1536a70eae7668d6a179f85d44d83dc", fmt="tsv", source_project="NPASS", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/CMAUP/downloadFiles/CMAUPv2.0_download_Plants.txt",
        "CMAUPv2.0_download_Plants.txt", "bidd.group",
        "Free for academic use; see bidd.group/CMAUP for terms",
        "CMAUP 2.0 medicinal plants with NCBI taxonomy (species, genus, family).",
        "natural-products", expected_bytes=648352, checksum="sha256:74c41c5b1b85e041163c2b74ac74e400f49cd4696a8f8a2bb1d112da3fffe780", fmt="tsv", source_project="CMAUP",
        version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/CMAUP/downloadFiles/CMAUPv2.0_download_Ingredients_All.txt",
        "CMAUPv2.0_download_Ingredients_All.txt", "bidd.group",
        "Free for academic use; see bidd.group/CMAUP for terms",
        "CMAUP 2.0 plant ingredients: identifiers, physicochemical properties, InChI and "
        "SMILES.", "natural-products", expected_bytes=25238280, checksum="sha256:5b091ad0c4f8c576cfbd2b5ee5418dd25c1171ccaf2f9811acb558717f155c6f", fmt="tsv",
        source_project="CMAUP", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/CMAUP/downloadFiles/CMAUPv2.0_download_Targets.txt",
        "CMAUPv2.0_download_Targets.txt", "bidd.group",
        "Free for academic use; see bidd.group/CMAUP for terms",
        "CMAUP 2.0 protein targets with UniProt, ChEMBL and TTD identifiers and target "
        "classes.", "natural-products", expected_bytes=117843, checksum="sha256:baf471c054fb858e130fdf6ab0b4e78869e8acb20336fad757828113b67ae16b", fmt="tsv",
        source_project="CMAUP", version="2.0"),
    AcquisitionSpec(
        "https://bidd.group/CMAUP/downloadFiles/CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt",
        "CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt", "bidd.group",
        "Free for academic use; see bidd.group/CMAUP for terms",
        "CMAUP 2.0 plant → ingredient associations.", "natural-products",
        expected_bytes=7973286, checksum="sha256:a0a7321201b15f4465e615e97e6f01651e26db6a88c13faea47d8e337a7e598b", fmt="tsv", source_project="CMAUP", version="2.0",
        notes="headerless: plant_id\\tingredient_id"),
    AcquisitionSpec(
        "https://bidd.group/CMAUP/downloadFiles/CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
        "CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
        "bidd.group", "Free for academic use; see bidd.group/CMAUP for terms",
        "CMAUP 2.0 ingredient → target activities with values, units and references.",
        "natural-products", expected_bytes=1338251, checksum="sha256:10b3ffacafcdae53180842945468aad726f1be30deafbc56fd04de4eca1165f5", fmt="tsv", source_project="CMAUP",
        version="2.0"),
    AcquisitionSpec(
        "https://www.npatlas.org/static/downloads/NPAtlas_download.tsv",
        "NPAtlas_download.tsv", "www.npatlas.org", "CC-BY-4.0",
        "NP Atlas: microbially derived natural products with structures, producing organisms "
        "and literature references.", "natural-products", expected_bytes=33671731,
        fmt="tsv", source_project="NPAtlas"),
    AcquisitionSpec(
        "https://zenodo.org/api/records/19360665/files/260413_frozen.csv.gz/content",
        "260413_frozen.csv.gz", "zenodo.org", "CC-BY-4.0",
        "LOTUS frozen export (2026-04-13): structure–organism pairs (InChIKey, SMILES, taxon) "
        "as curated in Wikidata.", "natural-products", expected_bytes=20594507,
        checksum="md5:cf0cf2afa2ca4d758b68f2e39d466f5d", fmt="csv.gz",
        source_project="LOTUS", version="2026-04-13"),
    AcquisitionSpec(
        "https://zenodo.org/api/records/19360665/files/260413_frozen_metadata.csv.gz/content",
        "260413_frozen_metadata.csv.gz", "zenodo.org", "CC-BY-4.0",
        "LOTUS frozen export (2026-04-13) with metadata: references and taxonomy per "
        "structure–organism pair.", "natural-products", expected_bytes=90298678,
        checksum="md5:b17048b3b77daae9ab1e480b6591aabd", fmt="csv.gz",
        source_project="LOTUS", version="2026-04-13"),
    # ------------------------------------------------------------ reference data
    AcquisitionSpec(
        "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdmp.zip", "taxdmp.zip",
        "ftp.ncbi.nlm.nih.gov", "Public domain (NCBI)",
        "NCBI Taxonomy dump: nodes, names and lineage for every taxon.", "taxonomy",
        fmt="zip", source_project="NCBI",
        notes="rebuilt by NCBI regularly, so no size is pinned; verified against the server's "
              "Content-Length at fetch time"),
    AcquisitionSpec(
        "https://zenodo.org/api/records/22808257/files/human_cell_marker.zip/content",
        "human_cell_marker.zip", "zenodo.org", "CC-BY-4.0",
        "CellMarker 3.0 human cell marker genes by tissue and cell type.", "single-cell",
        expected_bytes=48740702, checksum="md5:2209984d99c1f11caf62bbade198c80d", fmt="zip",
        source_project="CellMarker", version="3.0"),
)


class BiomniLakeSource:
    """Biomni's data lake: per-file objects in a public S3 bucket.

    The bundled zip returns 403; Biomni's own downloader fetches per file, and so
    does this. The 76 declared filenames come from the catalogue.
    """

    BASE = "https://biomni-release.s3.amazonaws.com/data_lake/"
    HOST = "biomni-release.s3.amazonaws.com"
    LICENSE = "Apache-2.0 (code); per-dataset terms apply to contents"

    def __init__(self, filenames: Mapping[str, str] | None = None) -> None:
        self.filenames = dict(filenames or {})

    def spec(self, filename: str, description: str = "", expected_bytes: int | None = None) -> AcquisitionSpec:
        return AcquisitionSpec(self.BASE + filename, filename, self.HOST, self.LICENSE,
                               description or f"Biomni data lake file {filename}", "general",
                               strategy="per_file_bucket", expected_bytes=expected_bytes,
                               fmt=Path(filename).suffix.lstrip("."), source_project="Biomni")


def acquisition_for(manifest: ComponentManifest) -> AcquisitionSpec | None:
    """Return the acquisition spec a dataset manifest declares, if any."""
    acq = (manifest.inputs or {}).get("acquisition")
    if not acq:
        return None
    if isinstance(acq, AcquisitionSpec):
        return acq
    fields = set(AcquisitionSpec.__dataclass_fields__)
    return AcquisitionSpec(**{k: v for k, v in dict(acq).items() if k in fields})


def fetch_command(manifest: ComponentManifest) -> str:
    spec = acquisition_for(manifest)
    if spec is None:
        return ""
    return f"bioagent fetch {manifest.id}   # -> {spec.filename} from {spec.host}"


class BulkDatasetProvider(ProviderBase):
    """Yields dataset components for every ACQUIRABLE bulk source."""

    name = "bulk-datasets"

    def __init__(self, specs: tuple[AcquisitionSpec, ...] = ACQUIRABLE) -> None:
        self.specs = specs

    def discover(self) -> Iterator[ComponentManifest]:
        for s in self.specs:
            yield ComponentManifest(
                id=f"{s.source_project.lower()}.dataset.{s.filename.lower().replace('.', '_')}",
                kind="dataset", name=s.filename, version=s.version, description=s.description,
                domain=s.domain, provider=Provider(project=s.source_project, repo=s.url),
                runtime=RuntimeSpec(backend="dataset", deterministic=True),
                inputs={"acquisition": s.__dict__},
                requires=Requirements(datasets=(s.filename,)),
                permissions=Permissions(network=(s.host,)),
                license=LicenseSpec(spdx=s.license, integration_mode="native"),
                validation=Validation(smoke_test="read_head"),
                offline_capable=True,
            )
