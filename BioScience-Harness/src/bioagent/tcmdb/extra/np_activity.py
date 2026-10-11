"""Natural-product occurrence and measured activity: NPASS 2.0, CMAUP 2.0 and LOTUS.

The research loop has read all three since 2026-10-07, through the source-snapshot pipeline
(``bioagent.sources``, ``scripts/build_source_snapshots.py``). The hub's cards named NPASS
and CMAUP (#5, #6) with no dataset behind them, and LOTUS had none, so the hub's tools
could find these sources but not fetch, build or query them. The datasets here close that
gap without a second reading of the files:

* the files are the reviewed rows of the acquisition table
  (``bioagent.acquisition.sources.ACQUIRABLE``): URL, size and checksum, kept in one place;
* each file is read as the snapshot parsers read it (``bioagent.sources.parsers``), with
  their identifiers (``compound_id``, ``target_id``, ``organism_id``), so a hub relation and
  a snapshot edge name the same compound, target and organism.

Relations:

* ``organism_compound``: a natural product isolated from an organism (NPASS, with the
  reference; a pair NPASS relays from another database is ``aggregated`` via it), an
  ingredient recorded in a plant (CMAUP, which gives no reference per pair:
  ``aggregated``), a structure–organism pair as LOTUS curates it in Wikidata (``known``
  when manually validated, otherwise ``reported``).
* ``compound_target``: a measured activity (IC50, Ki, MIC, …) of a compound on a protein
  target, with its PMID or DOI (``known``). Cell lines and organisms as targets, and
  activities without a PMID or DOI, are left out, as the snapshot parsers leave them;
  exact repeats of a row (87,882 of NPASS 2.0's 958,866 activity rows) are read once.

NPASS and CMAUP state no licence (bidd.group, read 2026-10-07): commercial use unknown.
LOTUS is CC-BY-4.0. ``hub.fetch`` keeps a file only if it hashes to its reviewed checksum,
and the checks after a build (``CHECKS``) compare every raw file with it again, so a file
replaced after its download is caught too: a file that differs is a problem, since the
upstream file has changed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterator

from ...acquisition.sources import ACQUIRABLE, AcquisitionSpec
from ...sources.parsers.common import (compound_id, is_inchikey, is_uniprot, organism_id,
                                       parse_measure, publication, target_id)
from ..rowkit import Row, ctx, has, rel, rows, v
from ..spec import DatasetSpec, FileSpec

__all__ = ["DATASETS", "EXTRACTORS", "CHECKS"]

_NO_LICENCE = "not stated (no licence or terms on bidd.group, read 2026-10-07)"
_TRUE = {"y", "yes", "true", "1"}

#: table -> file name, as the snapshot parsers name the files (sources.parsers.npass/cmaup)
_NPASS = {"general": "NPASSv2.0_download_naturalProducts_generalInfo.txt",
          "structure": "NPASSv2.0_download_naturalProducts_structureInfo.txt",
          "species": "NPASSv2.0_download_naturalProducts_speciesInfo.txt",
          "pairs": "NPASSv2.0_download_naturalProducts_species_pair.txt",
          "targets": "NPASSv2.0_download_naturalProducts_targetInfo.txt",
          "activities": "NPASSv2.0_download_naturalProducts_activities.txt"}
_CMAUP = {"plants": "CMAUPv2.0_download_Plants.txt",
          "ingredients": "CMAUPv2.0_download_Ingredients_All.txt",
          "pairs": "CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt",
          "targets": "CMAUPv2.0_download_Targets.txt",
          "activities": "CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt"}
_LOTUS = {"pairs": "260413_frozen.csv.gz", "metadata": "260413_frozen_metadata.csv.gz"}
_HEADERLESS = {"CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt": ("plant_id", "np_id")}

_ACQUIRED: dict[str, AcquisitionSpec] = {a.filename: a for a in ACQUIRABLE}


def _files(tables: dict[str, str], *, fmt: str = "tsv", optional: tuple[str, ...] = (),
           license: str = "") -> tuple[FileSpec, ...]:
    out = []
    for table, name in tables.items():
        a = _ACQUIRED[name]                       # a KeyError here: the acquisition table moved
        out.append(FileSpec(a.url, name, table, fmt=fmt, optional=table in optional,
                            expected_bytes=a.expected_bytes, columns=_HEADERLESS.get(name, ()),
                            note=a.description, license=license, checksum=a.checksum))
    return tuple(out)


DATASETS = (
    DatasetSpec(
        "npass", "NPASS 2.0 (natural products: source organisms and measured activities)", (5,),
        "https://bidd.group/NPASS/", _NO_LICENCE, _files(_NPASS), version="2.0",
        notes="The research loop's default activity source. Activities are against protein "
              "targets with a UniProt accession only; cell-line and organism targets stay in "
              "the activities table and yield no relation.",
        relations=("organism_compound", "compound_target"), min_interval_s=1.0),
    DatasetSpec(
        "cmaup", "CMAUP 2.0 (useful plants: ingredients and measured activities)", (6,),
        "https://bidd.group/CMAUP/", _NO_LICENCE, _files(_CMAUP), version="2.0",
        notes="Plant-ingredient associations carry no reference in the download, so they "
              "are 'aggregated'; ingredient-target activities carry a PMID or DOI.",
        relations=("organism_compound", "compound_target"), min_interval_s=1.0),
    DatasetSpec(
        "lotus", "LOTUS (natural-product occurrences curated in Wikidata)", (137,),
        "https://lotus.naturalproducts.net/", "CC-BY-4.0",
        _files(_LOTUS, fmt="csv", optional=("metadata",)), version="2026-04-13",
        notes="The Zenodo frozen export of 2026-04-13; the metadata file (90 MB) is fetched "
              "only on request. Live occurrences: the wikidata_sparql connector.",
        relations=("organism_compound",), commercial_use="allowed"),
)


# ---------------------------------------------------------------------------- extraction
def _distinct(conn: sqlite3.Connection, table: str, wanted: tuple[str, ...]) -> Iterator[dict]:
    """The table's distinct rows over the wanted columns, keyed as asked: a release with a
    column less reads as that column empty, and exact repeats of a row are read once."""
    have = {r[1].lower(): r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    present = [(w, have[w.lower()]) for w in wanted if w.lower() in have]
    if not present:
        return
    select = ", ".join(f'"{c}"' for _, c in present)
    for r in rows(conn, f'SELECT DISTINCT {select} FROM "{table}"'):
        yield {w: r[c] for w, c in present}


def _shown(name: str | None, inchikey: str | None, source_id: str) -> str:
    """A compound's name: the source's, unless it is an InChIKey standing in for one."""
    return (name if name and not is_inchikey(name) else None) or inchikey or source_id


def _value(measure: dict | None) -> str | None:
    if not measure:
        return None
    rel_ = measure["relation"]
    number = f"{measure['value']:g}"
    return number if rel_ == "=" else f"{rel_}{number}"


def _activities(conn: sqlite3.Connection, key: str, compounds: dict[str, tuple[str, str | None]],
                proteins: dict[str, tuple[str, str | None]], *, table: str, np: str, tid: str,
                ref: str, ref_type: str, kind: str, value: str, unit: str, relation: str,
                assay: tuple[str, str, str] | None = None) -> Iterator[Row | None]:
    wanted = (np, tid, ref, ref_type, kind, value, unit, relation, *(assay or ()))
    for r in _distinct(conn, table, wanted):
        compound, target = compounds.get(v(r.get(np)) or ""), proteins.get(v(r.get(tid)) or "")
        reference = publication(r.get(ref), r.get(ref_type))
        if compound is None or target is None or reference is None:
            continue                    # not a protein target, or no PMID/DOI: as the parsers
        measure = parse_measure(r.get(kind), r.get(value), r.get(unit), r.get(relation))
        species, cell, tissue = (r.get(a) for a in assay) if assay else (None, None, None)
        yield rel("compound_target", key, compound[0], compound[1], target[0], target[1],
                  "known", reference=reference,
                  context=ctx(measure=(measure or {}).get("type") or v(r.get(kind)),
                              value=_value(measure), unit=(measure or {}).get("unit"),
                              species=v(species), cell=v(cell), tissue=v(tissue)))


def _npass(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "species", "pairs", "general"):
        return
    organisms = {}
    for r in _distinct(conn, "species", ("org_id", "org_tax_id", "species_tax_id", "org_name")):
        org = v(r.get("org_id"))
        if org:
            # the record's own taxon, not its parent species (sources.parsers.npass)
            tax = v(r.get("org_tax_id")) or v(r.get("species_tax_id"))
            organisms[org] = (organism_id("npass", org, tax), v(r.get("org_name")))
    inchikeys = ({v(r.get("np_id")): v(r.get("InChIKey"))
                  for r in _distinct(conn, "structure", ("np_id", "InChIKey"))
                  if is_inchikey(v(r.get("InChIKey")))} if has(conn, "structure") else {})
    compounds = {}
    for r in _distinct(conn, "general", ("np_id", "pref_name")):
        np = v(r.get("np_id"))
        if np:
            ik = inchikeys.get(np)
            compounds[np] = (compound_id("npass", np, ik), _shown(v(r.get("pref_name")), ik, np))
    pairs: dict[tuple[str, str], dict] = {}
    for r in _distinct(conn, "pairs", ("org_id", "np_id", "ref_id", "ref_id_type", "ref_type",
                                       "org_isolation_part")):
        org, np = v(r.get("org_id")), v(r.get("np_id"))
        if org not in organisms or np not in compounds:
            continue
        pair = pairs.setdefault((org, np), {"refs": set(), "dbs": set(), "parts": set()})
        reference = publication(r.get("ref_id"), r.get("ref_id_type"))
        if reference:
            pair["refs"].add(reference)
        elif (v(r.get("ref_type")) or "").lower() == "database" and v(r.get("ref_id")):
            pair["dbs"].add(v(r.get("ref_id")))
        if v(r.get("org_isolation_part")):
            pair["parts"].add(v(r.get("org_isolation_part")))
    for (org, np), pair in sorted(pairs.items()):
        relayed = not pair["refs"] and pair["dbs"]
        yield rel("organism_compound", "npass", organisms[org][0], organisms[org][1],
                  compounds[np][0], compounds[np][1], "aggregated" if relayed else "known",
                  reference=" | ".join(sorted(pair["refs"])) or None,
                  note=f"via {sorted(pair['dbs'])[0]}" if relayed else None,
                  context=ctx(part=" | ".join(sorted(pair["parts"])) or None))
    if has(conn, "targets", "activities"):
        proteins = {v(r.get("target_id")): (target_id("npass", v(r.get("target_id")),
                                                      v(r.get("uniprot_id"))),
                                            v(r.get("target_name")))
                    for r in _distinct(conn, "targets", ("target_id", "uniprot_id", "target_name"))
                    if v(r.get("target_id")) and is_uniprot(v(r.get("uniprot_id")))}
        yield from _activities(conn, "npass", compounds, proteins, table="activities",
                               np="np_id", tid="target_id", ref="ref_id",
                               ref_type="ref_id_type", kind="activity_type",
                               value="activity_value", unit="activity_units",
                               relation="activity_relation",
                               assay=("assay_organism", "assay_cell_type", "assay_tissue"))


def _cmaup(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "plants", "pairs", "ingredients"):
        return
    plants = {}
    for r in _distinct(conn, "plants", ("Plant_ID", "Species_Tax_ID", "Species_Name", "Plant_Name")):
        pid = v(r.get("Plant_ID"))
        if pid:
            plants[pid] = (organism_id("cmaup", pid, v(r.get("Species_Tax_ID"))),
                           v(r.get("Species_Name")) or v(r.get("Plant_Name")))
    compounds = {}
    for r in _distinct(conn, "ingredients", ("np_id", "InChIKey", "pref_name")):
        np = v(r.get("np_id"))
        if np:
            ik = v(r.get("InChIKey")) if is_inchikey(v(r.get("InChIKey"))) else None
            compounds[np] = (compound_id("cmaup", np, ik), _shown(v(r.get("pref_name")), ik, np))
    for r in _distinct(conn, "pairs", ("plant_id", "np_id")):
        plant, np = v(r.get("plant_id")), v(r.get("np_id"))
        if plant in plants and np in compounds:
            yield rel("organism_compound", "cmaup", plants[plant][0], plants[plant][1],
                      compounds[np][0], compounds[np][1], "aggregated",
                      note="CMAUP gives no reference for a plant-ingredient pair")
    if has(conn, "targets", "activities"):
        proteins = {v(r.get("Target_ID")): (target_id("cmaup", v(r.get("Target_ID")),
                                                      v(r.get("Uniprot_ID"))),
                                            v(r.get("Protein_Name")) or v(r.get("Gene_Symbol")))
                    for r in _distinct(conn, "targets", ("Target_ID", "Uniprot_ID", "Protein_Name",
                                                         "Gene_Symbol"))
                    if v(r.get("Target_ID")) and is_uniprot(v(r.get("Uniprot_ID")))}
        yield from _activities(conn, "cmaup", compounds, proteins, table="activities",
                               np="Ingredient_ID", tid="Target_ID", ref="Reference_ID",
                               ref_type="Reference_ID_Type", kind="Activity_Type",
                               value="Activity_Value", unit="Activity_Unit",
                               relation="Activity_Relationship")


def _lotus(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "pairs"):
        return
    pairs: dict[tuple[str, str], dict] = {}
    for r in _distinct(conn, "pairs", ("structure_inchikey", "organism_name",
                                       "organism_taxonomy_ncbiid", "organism_wikidata",
                                       "structure_nameTraditional", "reference_doi",
                                       "manual_validation")):
        ik, org_name = v(r.get("structure_inchikey")), v(r.get("organism_name"))
        if not is_inchikey(ik) or not org_name:
            continue
        tax = v(r.get("organism_taxonomy_ncbiid"))
        qid = (v(r.get("organism_wikidata")) or "").rsplit("/", 1)[-1] or None
        organism = f"ncbitaxon:{tax}" if tax and tax.isdigit() else f"lotus:{qid or org_name}"
        pair = pairs.setdefault((organism, f"inchikey:{ik}"), {
            "organism": org_name, "compound": v(r.get("structure_nameTraditional")) or ik,
            "refs": set(), "validated": False})
        doi = publication(r.get("reference_doi"), "doi")
        if doi:
            pair["refs"].add(doi)
        if (v(r.get("manual_validation")) or "").lower() in _TRUE:
            pair["validated"] = True
    for (organism, compound), p in sorted(pairs.items()):
        yield rel("organism_compound", "lotus", organism, p["organism"], compound, p["compound"],
                  "known" if p["validated"] else "reported",
                  reference=" | ".join(sorted(p["refs"])) or None)


EXTRACTORS = {"npass": _npass, "cmaup": _cmaup, "lotus": _lotus}


# ---------------------------------------------------------------------------- checks
def _digest(path: Path, algorithm: str) -> str:
    h = hashlib.new(algorithm)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _checksums(key: str):
    spec = next(d for d in DATASETS if d.key == key)

    def check(raw: Path, conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
        problems: list[str] = []
        for f in spec.files:
            path = Path(raw) / f.name
            if not path.is_file() or not f.checksum:
                continue
            algorithm, want = f.checksum.split(":", 1)
            if _digest(path, algorithm) != want:
                problems.append(f"{f.name}: its {algorithm} differs from the reviewed file's "
                                "(the upstream file has changed; review it before use)")
        return problems, []
    return check


CHECKS = {d.key: _checksums(d.key) for d in DATASETS}
