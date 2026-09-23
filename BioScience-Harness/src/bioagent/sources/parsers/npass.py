"""NPASS 2.0 (BIDD, NUS): natural products, their source organisms, and measured activities.

Files (``acquisition.sources`` names them): ``generalInfo`` (names, ChEMBL, PubChem),
``structureInfo`` (InChIKey), ``speciesInfo`` (organisms with NCBI taxonomy),
``species_pair`` (organism -> product, with isolation part and a reference),
``targetInfo`` (targets; proteins carry a UniProt accession) and ``activities``
(quantitative values with a reference).

Edges:

* organism ``contains`` compound — ``chemical_analysis``, composition level C1 (reported
  in the species). The isolation part, when NPASS records one, is kept in ``raw`` so the
  herb layer can raise a pair to C2 when it is the medicinal part. A pair NPASS took from
  another database names that database as the primary knowledge source and NPASS as the
  aggregator.
* compound ``targets`` protein — ``in_vitro`` measurement with its value. Activities
  against cell lines or whole organisms are not protein targets and are not edges; an
  activity without a PMID or DOI is not citable evidence and is dropped. Both are counted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .common import (NodeBook, ParseReport, ParseResult, TaxonFilter, compound_id, is_inchikey,
                     is_uniprot, organism_id, parse_measure, publication, read_rows, target_id)

__all__ = ["FILES", "parse_npass"]

KEY = "npass"
LICENSE = "Free for academic use"

#: role -> file name in the NPASS 2.0 download.
FILES: Mapping[str, str] = {
    "general": "NPASSv2.0_download_naturalProducts_generalInfo.txt",
    "structure": "NPASSv2.0_download_naturalProducts_structureInfo.txt",
    "species": "NPASSv2.0_download_naturalProducts_speciesInfo.txt",
    "pairs": "NPASSv2.0_download_naturalProducts_species_pair.txt",
    "targets": "NPASSv2.0_download_naturalProducts_targetInfo.txt",
    "activities": "NPASSv2.0_download_naturalProducts_activities.txt",
}


def parse_npass(raw_dir: str | Path, *, taxa: TaxonFilter | None = None,
                files: Mapping[str, str] = FILES) -> ParseResult:
    """Parse the NPASS files in ``raw_dir``; with ``taxa``, only those organisms and the
    compounds and activities that hang off them."""
    raw_dir = Path(raw_dir)
    paths = {role: raw_dir / name for role, name in files.items()}
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    edges: list[dict[str, Any]] = []

    # organisms --------------------------------------------------------------------------
    organisms: dict[str, str] = {}                   # NPO id -> node id
    for row in read_rows(paths["species"]):
        report.read["species"] += 1
        org = row.get("org_id")
        if not org:
            continue
        # The record's own taxon, not its parent species: matching the species would let
        # a variety (Coptis chinensis var. brevisepala) stand in for the official source.
        tax = row.get("org_tax_id") or row.get("species_tax_id")
        if taxa is not None and taxa.by_id(tax) is None:
            continue
        organisms[org] = book.add(
            organism_id(KEY, org, tax), "organism", row.get("org_name"),
            xrefs={KEY: [org], **({"ncbitaxon": [tax]} if tax and tax.isdigit() else {})},
            raw={"species": row.get("species_name"), "genus": row.get("genus_name"),
                 "family": row.get("family_name")})

    # organism -> compound pairs ------------------------------------------------------------
    pairs: dict[str, dict[str, Any]] = {}
    for row in read_rows(paths["pairs"]):
        report.read["pairs"] += 1
        org, np = row.get("org_id"), row.get("np_id")
        if org not in organisms or not np:
            if taxa is None and np:
                report.drop("pair names an organism not in speciesInfo")
            continue
        key = row.get("src_org_pair") or f"{org}-{np}"
        pair = pairs.setdefault(key, {"org": org, "np": np, "publications": set(),
                                      "databases": set(), "parts": set(), "locations": set()})
        pub = publication(row.get("ref_id"), row.get("ref_id_type"))
        if pub:
            pair["publications"].add(pub)
        elif (row.get("ref_type") or "").lower() == "database" and row.get("ref_id"):
            pair["databases"].add(row["ref_id"])
        if row.get("org_isolation_part"):
            pair["parts"].add(row["org_isolation_part"])
        if row.get("org_collect_location"):
            pair["locations"].add(row["org_collect_location"])
    wanted = {p["np"] for p in pairs.values()}

    # compounds ----------------------------------------------------------------------------
    inchikeys: dict[str, str] = {}
    for row in read_rows(paths["structure"]):
        report.read["structure"] += 1
        if (taxa is None or row.get("np_id") in wanted) and is_inchikey(row.get("InChIKey")):
            inchikeys[row["np_id"]] = row["InChIKey"]
    compounds: dict[str, str] = {}
    for row in read_rows(paths["general"]):
        report.read["general"] += 1
        np = row.get("np_id")
        if not np or (taxa is not None and np not in wanted):
            continue
        ik = inchikeys.get(np)
        name = row.get("pref_name")
        if name and is_inchikey(name):              # NPASS uses the key as a stand-in name
            name = None
        xrefs = {KEY: [np]}
        for field, prefix in (("pubchem_cid", "pubchem"), ("chembl_id", "chembl")):
            if row.get(field):
                xrefs[prefix] = [row[field]]
        if ik:
            xrefs["inchikey"] = [ik]
        compounds[np] = book.add(compound_id(KEY, np, ik), "ingredient", name or ik or np,
                                 xrefs=xrefs, names={"en": [name]} if name else None)

    for key, pair in sorted(pairs.items()):
        if pair["np"] not in compounds:
            report.drop("pair names a compound not in generalInfo")
            continue
        edge = {
            "subject": organisms[pair["org"]], "predicate": "contains",
            "object": compounds[pair["np"]], "knowledge_level": "knowledge_assertion",
            "agent_type": "manual_agent", "study_design": "chemical_analysis",
            "composition_level": "C1", "license": LICENSE, "source_record_id": key,
            "primary_knowledge_source": KEY, "publications": sorted(pair["publications"]),
            "raw": {k: sorted(pair[k]) for k in ("parts", "locations", "databases") if pair[k]},
        }
        if not pair["publications"] and pair["databases"]:
            edge["primary_knowledge_source"] = sorted(pair["databases"])[0].lower()
            edge["aggregator_knowledge_source"] = KEY
            edge["agent_type"] = "not_provided"
        edges.append(edge)

    # protein targets and measured activities ---------------------------------------------
    proteins: dict[str, dict[str, Any]] = {}          # NPT id -> row, proteins only
    for row in read_rows(paths["targets"]):
        report.read["targets"] += 1
        if row.get("target_id") and is_uniprot(row.get("uniprot_id")):
            proteins[row["target_id"]] = row           # cell lines, organisms: no accession
    targets: dict[str, str] = {}

    def target(tid: str) -> str:
        if tid not in targets:
            row = proteins[tid]
            targets[tid] = book.add(
                target_id(KEY, tid, row["uniprot_id"]), "target", row.get("target_name"),
                xrefs={KEY: [tid], "uniprot": [row["uniprot_id"]]},
                raw={"organism": row.get("target_organism"), "type": row.get("target_type")})
        return targets[tid]
    for i, row in enumerate(read_rows(paths["activities"])):
        report.read["activities"] += 1
        np, tid = row.get("np_id"), row.get("target_id")
        if np not in compounds:
            continue
        if tid not in proteins:
            report.drop("activity against a non-protein or unmapped target")
            continue
        pub = publication(row.get("ref_id"), row.get("ref_id_type"))
        if pub is None:
            report.drop("activity without a PMID or DOI")
            continue
        measure = parse_measure(row.get("activity_type"), row.get("activity_value"),
                                row.get("activity_units"), row.get("activity_relation"))
        edges.append({
            "subject": compounds[np], "predicate": "targets", "object": target(tid),
            "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
            "study_design": "in_vitro", "license": LICENSE,
            "source_record_id": f"{np}|{tid}|{i}", "primary_knowledge_source": KEY,
            "publications": [pub], "measure": measure,
            "raw": {k: row[k] for k in ("assay_organism", "assay_cell_type", "assay_tissue")
                    if row.get(k)},
        })

    return ParseResult(book.rows(), edges, report,
                       {name: paths[role] for role, name in files.items()})
