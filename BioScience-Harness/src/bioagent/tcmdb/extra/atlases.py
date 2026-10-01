"""Reference-atlas snapshots: HuBMAP's bulk metadata and the 4D Nucleome loop sets.

Atlas data says where a measurement comes from (organ, assay, cell line), not what a
compound does. Two sources publish small files worth keeping locally:

* **HuBMAP** (``hubmap``): the portal's bulk metadata exports of every dataset, sample
  and donor (documented in the portal's llms.txt), plus HuBMAP's organ list from its
  Ontology (UBKG) API, which maps each organ label to its UBERON term. The exports are
  generated on request: ``HEAD`` answers 415 and ``Range`` is ignored, so the downloader
  streams each one whole under its size cap, and the fetch date is the version. Line two
  of each export is a row of field descriptions, not data; the reader skips it, and it
  leaves out the submitters' e-mail addresses and display names. The extractor yields
  ``dataset_tissue``: a published dataset and the organ (and, where registered, the
  anatomical structure) its tissue came from.
* **4D Nucleome** (``fourdn``): the union chromatin-loop sets of the 4DN joint analysis
  for HFFc6 and H1-hESC, integrated across Hi-C, Micro-C, CTCF and RNAPII ChIA-PET and
  H3K4me3 PLAC-seq. They are on the AWS Open Data bucket, which needs no account
  (portal downloads do, and robots.txt disallows them). The extractor yields
  ``chromatin_loop`` rows between GRCh38 anchors, with the platforms supporting each
  loop. The per-experiment loop files these sets were integrated from are not loaded,
  so a loop is not counted twice. Contact matrices (``.hic``, ``.mcool``) are
  multi-gigabyte analysis inputs and are not part of this dataset.

The Human Cell Atlas, BioSamples and the 4DN metadata are reached live
(``providers.supplement.atlases``); HTAN needs an account for every programmatic route
and is catalogued only.
"""

from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path
from typing import Iterator

from ..rowkit import Row, col, ctx, has, names, rel, rows, v
from ..spec import DatasetSpec, FileSpec
from ..store import open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

#: Atlas relations no registered kind expresses: a dataset and the tissue it sampled, and
#: a chromatin loop between two genomic anchors.
KINDS = {
    "dataset_tissue": ("dataset", "tissue"),
    "chromatin_loop": ("region", "region"),
}

# ------------------------------------------------------------------------------ HuBMAP
#: Columns naming the people who entered a record or ran an assay, with their e-mail
#: addresses. They are contact details, not metadata about the tissue, and are not loaded.
_PERSONAL = re.compile(r"e-?mail|^created_by_user_displayname$", re.I)


def _hubmap_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A HuBMAP metadata export: tab-separated with CSV quoting, line two describing the
    fields. Quoted values may hold tabs, quotes and line breaks."""
    with open_text(path) as fh:
        reader = csv.reader(fh, delimiter="\t")
        header = next(reader, None)
        if header is None:
            return
        keep = [i for i, name in enumerate(header) if not _PERSONAL.search(name.strip())]
        yield [header[i].strip() for i in keep]
        for n, row in enumerate(reader):
            if not row or not any(c.strip() for c in row):
                continue
            if n == 0 and row[0].strip() == "#":            # the field-description row
                continue
            row = row + [""] * (len(header) - len(row))
            yield [(row[i].strip() or None) for i in keep]


READERS = {"hubmap_tsv": _hubmap_tsv}

#: Anatomy terms as HuBMAP writes them (UBERON, or FMA where UBERON has no term).
_ANATOMY = re.compile(r"^(UBERON|FMA)[:_](\d+)$", re.I)
_PORTAL = "https://portal.hubmapconsortium.org/metadata/v0"


def _labels(text: str | None) -> list[str]:
    """``['Kidney (Right)', 'Lung']`` (a Python list as text) -> its labels."""
    text = v(text)
    if not text:
        return []
    if text.startswith("["):
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            value = re.findall(r"'([^']*)'|\"([^\"]*)\"", text)
            value = [a or b for a, b in value]
        return [str(x).strip() for x in value if str(x).strip()]
    return [text]


def _anatomy(value: str | None) -> str | None:
    """``UBERON:0002106`` -> ``uberon:0002106``; ``FMA:24977`` -> ``fma:24977``."""
    m = _ANATOMY.match((v(value) or "").strip())
    return f"{m.group(1).lower()}:{m.group(2)}" if m else None


def _hubmap(conn) -> Iterator[Row | None]:
    if not has(conn, "datasets"):
        return
    # organ label (as the exports write it) -> (anatomy id, label, the organ it is part of)
    organs: dict[str, tuple[str, str, str | None]] = {}
    if has(conn, "organs"):
        for r in rows(conn, "SELECT * FROM organs"):
            term, uid = v(col(r, "term")), _anatomy(col(r, "organ_uberon"))
            if not term or not uid:
                continue
            parent = None
            try:
                category = json.loads(v(col(r, "category")) or "null")
                parent = (category or {}).get("term")
            except (ValueError, AttributeError):
                parent = None
            organs[term.casefold()] = (uid, term, parent if parent != term else None)
    for r in rows(conn, "SELECT * FROM datasets"):
        hid = v(col(r, "hubmap_id"))
        # a retracted or unpublished dataset is not evidence of anything
        if not hid or (v(col(r, "status")) or "").casefold() != "published":
            continue
        access = (v(col(r, "data_access_level")) or "").casefold()
        context = ctx(species="9606", assay=v(col(r, "dataset_type")),
                      method=v(col(r, "assay_type")),
                      flags="protected sequence data (metadata public)"
                      if access == "protected" else None)
        sid, sname = f"hubmap:{hid}", names(hid)
        for label in _labels(col(r, "origin_samples_unique_mapped_organs")):
            uid, term, parent = organs.get(label.casefold(), (None, label, None))
            yield rel("dataset_tissue", "hubmap", sid, sname, uid or f"hubmap:organ.{label}",
                      term, "listed", context=context,
                      note=f"organ; part of {parent}" if parent else "organ")
        # the anatomical structure a spatial region of interest was registered to
        ids = [x.strip() for x in (v(col(r, "anatomical_structure_id")) or "").split(",")]
        labels = [x.strip() for x in (v(col(r, "anatomical_structure_label")) or "")
                  .split(",")]
        for i, raw in enumerate(ids):
            uid = _anatomy(raw)
            if uid:
                yield rel("dataset_tissue", "hubmap", sid, sname, uid,
                          labels[i] if len(labels) == len(ids) else None, "listed",
                          context=context, note="anatomical structure of the sampled region")


_HUBMAP_LICENCE = ("CC BY 4.0 (HuBMAP External Data Sharing Policy: open data released "
                   "under a permissive licence such as CC BY 4.0; DOI-registered datasets "
                   "carry CC BY 4.0); do not use the data to identify or contact "
                   "participants")

HUBMAP = DatasetSpec(
    key="hubmap", name="HuBMAP bulk metadata (datasets, samples, donors) and organ list",
    catalog=(122,), homepage="https://portal.hubmapconsortium.org/",
    license=_HUBMAP_LICENCE,
    files=(
        FileSpec(f"{_PORTAL}/datasets.tsv", "datasets.tsv", "datasets", fmt="hubmap_tsv",
                 note="every dataset: assay, organ, donor, status, access level; "
                      "~8 MB, regenerated per request"),
        FileSpec(f"{_PORTAL}/samples.tsv", "samples.tsv", "samples", fmt="hubmap_tsv",
                 note="every tissue sample: category, organ, preservation, donor"),
        FileSpec(f"{_PORTAL}/donors.tsv", "donors.tsv", "donors", fmt="hubmap_tsv",
                 note="de-identified donor demographics and medical history"),
        FileSpec("https://ontology.api.hubmapconsortium.org/organs?application_context="
                 "HUBMAP", "organs.json", "organs", fmt="json",
                 note="HuBMAP organ labels and codes with their UBERON (or FMA) terms",
                 license="HuBMAP application ontology via the UBKG Ontology API; UBERON "
                         "terms CC BY 3.0"),
    ),
    version="live export, fetched 2026-10-01",
    notes="Metadata of the Human BioMolecular Atlas Program: healthy human tissue mapped "
          "by single-cell, spatial and mass-spectrometry assays. dataset_tissue rows say a "
          "published dataset was made from tissue of that organ (provenance metadata, "
          "evidence 'listed'), with the dataset type as assay; organs carry the UBERON "
          "term (FMA for the knee) of HuBMAP's own organ list, and a spatial region "
          "registered to an anatomical structure adds a row for that structure. Retracted "
          "datasets yield no rows. Protected sequence data is metadata only here. The "
          "exports carry no expression values.",
    relations=("dataset_tissue",),
    commercial_use="allowed",
)

# ------------------------------------------------------------------------- 4D Nucleome
_BEDPE = ("chrom1", "start1", "end1", "chrom2", "start2", "end2", "supporting_assays")
_S3 = "https://4dn-open-data-public.s3.amazonaws.com/fourfront-webprod/wfoutput"
#: table -> (cell line, 4DN file accession)
_LOOP_SETS = {"loops_hffc6": ("HFFc6", "4DNFI32J1C6W"),
              "loops_h1esc": ("H1-hESC", "4DNFIX6VZKOA")}
_COORD = re.compile(r"^\d+$")


def _anchor(chrom: str | None, start: str | None, end: str | None) -> str | None:
    chrom, start, end = v(chrom), v(start), v(end)
    if not chrom or not start or not end or not (_COORD.match(start) and _COORD.match(end)):
        return None
    return f"grch38:{chrom}:{start}-{end}"


def _fourdn(conn) -> Iterator[Row | None]:
    for table, (cell, accession) in _LOOP_SETS.items():
        if not has(conn, table):
            continue
        for r in rows(conn, f'SELECT * FROM "{table}"'):
            a = _anchor(r["chrom1"], r["start1"], r["end1"])
            b = _anchor(r["chrom2"], r["start2"], r["end2"])
            assays = ",".join(sorted(x.strip() for x in (v(r["supporting_assays"]) or "")
                                     .split(",") if x.strip())) or None
            yield rel("chromatin_loop", "fourdn", a, None, b, None, "known",
                      reference=f"4dn:{accession}",
                      context=ctx(species="9606", cell=cell, genome_build="GRCh38",
                                  assay=assays, method="4DN union loop calls"),
                      note="union loop set of the 4DN joint analysis")


_FOURDN_LICENCE = ("4DN data on AWS Open Data: 'External data users may freely download, "
                   "analyze, and publish results based on any 4DN data provided here "
                   "without restrictions'; cite the 4DN papers and acknowledge the lab")

FOURDN = DatasetSpec(
    key="fourdn", name="4D Nucleome union chromatin-loop sets (HFFc6, H1-hESC)",
    catalog=(124,), homepage="https://data.4dnucleome.org/",
    license=_FOURDN_LICENCE,
    files=(
        FileSpec(f"{_S3}/153d40b0-8e43-4821-87a2-b5b78842f453/4DNFI32J1C6W.bedpe.gz",
                 "HFFc6.union-loops.bedpe.gz", "loops_hffc6", fmt="tsv", columns=_BEDPE,
                 expected_bytes=1595045,
                 note="4DNFI32J1C6W, GRCh38, released 2025-11-04 (md5 "
                      "4ba4f58dd9d8e2a3ac003947b6ac085a)"),
        FileSpec(f"{_S3}/ed5747aa-9b78-4ff5-965f-0d7b389a6219/4DNFIX6VZKOA.bedpe.gz",
                 "H1ESC.union-loops.bedpe.gz", "loops_h1esc", fmt="tsv", columns=_BEDPE,
                 expected_bytes=1478901, note="4DNFIX6VZKOA, GRCh38"),
    ),
    version="union loop sets released 2025-11-04 (4DNFI32J1C6W, 4DNFIX6VZKOA)",
    notes="Chromatin loops called by the 4DN Data Coordination and Integration Center for "
          "the two 4DN joint-analysis cell lines, as the union of loops found by Hi-C, "
          "Micro-C, CTCF and RNAPII ChIA-PET and H3K4me3 PLAC-seq (Peakachu with "
          "platform-specific callers). Each chromatin_loop row joins two GRCh38 anchors "
          "(grch38:<chrom>:<start>-<end>) and lists the supporting platforms in "
          "context.assay; a loop seen by one platform only is as much a row as one seen "
          "by five. Evidence 'known': measured contacts, called computationally.",
    relations=("chromatin_loop",),
    commercial_use="allowed",
)

DATASETS = (HUBMAP, FOURDN)
EXTRACTORS = {"hubmap": _hubmap, "fourdn": _fourdn}
