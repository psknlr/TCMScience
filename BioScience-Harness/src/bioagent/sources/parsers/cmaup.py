"""CMAUP 2.0 (BIDD, NUS): medicinal plants, their ingredients, and ingredient-target activities.

Files: ``Plants`` (NCBI taxonomy), ``Ingredients_All`` (InChIKey, PubChem, ChEMBL),
``Plant_Ingredient_Associations_allIngredients`` (headerless: plant id, ingredient id;
no reference), ``Targets`` (UniProt) and
``Ingredient_Target_Associations_ActivityValues_References`` (values with a reference).

Plant -> ingredient pairs carry no reference in CMAUP, so they are ``knowledge_assertion``
by an unstated agent at composition level C1: usable to enumerate candidates, not to
cite. Activities without a PMID or DOI are dropped and counted, as for NPASS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .common import (NodeBook, ParseReport, ParseResult, TaxonFilter, compound_id, is_inchikey,
                     is_uniprot, organism_id, parse_measure, publication, read_rows, target_id)

__all__ = ["FILES", "parse_cmaup"]

KEY = "cmaup"
LICENSE = "Free for academic use"

FILES: Mapping[str, str] = {
    "plants": "CMAUPv2.0_download_Plants.txt",
    "ingredients": "CMAUPv2.0_download_Ingredients_All.txt",
    "pairs": "CMAUPv2.0_download_Plant_Ingredient_Associations_allIngredients.txt",
    "targets": "CMAUPv2.0_download_Targets.txt",
    "activities": "CMAUPv2.0_download_Ingredient_Target_Associations_ActivityValues_References.txt",
}


def parse_cmaup(raw_dir: str | Path, *, taxa: TaxonFilter | None = None,
                files: Mapping[str, str] = FILES) -> ParseResult:
    raw_dir = Path(raw_dir)
    paths = {role: raw_dir / name for role, name in files.items()}
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    edges: list[dict[str, Any]] = []

    plants: dict[str, str] = {}
    for row in read_rows(paths["plants"]):
        report.read["plants"] += 1
        pid, tax = row.get("Plant_ID"), row.get("Species_Tax_ID")
        if not pid or (taxa is not None and taxa.by_id(tax) is None):
            continue
        plants[pid] = book.add(
            organism_id(KEY, pid, tax), "organism", row.get("Species_Name") or row.get("Plant_Name"),
            xrefs={KEY: [pid], **({"ncbitaxon": [tax]} if tax and tax.isdigit() else {})},
            raw={"genus": row.get("Genus_Name"), "family": row.get("Family_Name")})

    pairs: list[tuple[str, str]] = []
    for row in read_rows(paths["pairs"], fieldnames=("plant_id", "np_id")):
        report.read["pairs"] += 1
        if row.get("plant_id") in plants and row.get("np_id"):
            pairs.append((row["plant_id"], row["np_id"]))
    wanted = {np for _, np in pairs}

    compounds: dict[str, str] = {}
    for row in read_rows(paths["ingredients"]):
        report.read["ingredients"] += 1
        np = row.get("np_id")
        if not np or (taxa is not None and np not in wanted):
            continue
        ik = row.get("InChIKey") if is_inchikey(row.get("InChIKey")) else None
        name = row.get("pref_name")
        if name and is_inchikey(name):
            name = None
        xrefs = {KEY: [np]}
        for field, prefix in (("pubchem_cid", "pubchem"), ("chembl_id", "chembl")):
            if row.get(field):
                xrefs[prefix] = [row[field]]
        if ik:
            xrefs["inchikey"] = [ik]
        compounds[np] = book.add(compound_id(KEY, np, ik), "ingredient", name or ik or np,
                                 xrefs=xrefs, names={"en": [name]} if name else None)

    for plant, np in sorted(set(pairs)):
        if np not in compounds:
            report.drop("pair names an ingredient not in Ingredients_All")
            continue
        edges.append({
            "subject": plants[plant], "predicate": "contains", "object": compounds[np],
            "knowledge_level": "knowledge_assertion", "agent_type": "not_provided",
            "study_design": "chemical_analysis", "composition_level": "C1",
            "license": LICENSE, "source_record_id": f"{plant}-{np}",
            "primary_knowledge_source": KEY, "publications": [],
        })

    proteins: dict[str, dict[str, Any]] = {}
    for row in read_rows(paths["targets"]):
        report.read["targets"] += 1
        if row.get("Target_ID") and is_uniprot(row.get("Uniprot_ID")):
            proteins[row["Target_ID"]] = row
    targets: dict[str, str] = {}

    def target(tid: str) -> str:
        if tid not in targets:
            row = proteins[tid]
            targets[tid] = book.add(
                target_id(KEY, tid, row["Uniprot_ID"]), "target",
                row.get("Protein_Name") or row.get("Gene_Symbol"),
                xrefs={KEY: [tid], "uniprot": [row["Uniprot_ID"]],
                       **({"hgnc_symbol": [row["Gene_Symbol"]]} if row.get("Gene_Symbol") else {})})
        return targets[tid]

    for i, row in enumerate(read_rows(paths["activities"])):
        report.read["activities"] += 1
        np, tid = row.get("Ingredient_ID"), row.get("Target_ID")
        if np not in compounds:
            continue
        if tid not in proteins:
            report.drop("activity against a non-protein or unmapped target")
            continue
        pub = publication(row.get("Reference_ID"), row.get("Reference_ID_Type"))
        if pub is None:
            report.drop("activity without a PMID or DOI")
            continue
        edges.append({
            "subject": compounds[np], "predicate": "targets", "object": target(tid),
            "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
            "study_design": "in_vitro", "license": LICENSE,
            "source_record_id": f"{np}|{tid}|{i}", "primary_knowledge_source": KEY,
            "publications": [pub],
            "measure": parse_measure(row.get("Activity_Type"), row.get("Activity_Value"),
                                     row.get("Activity_Unit"), row.get("Activity_Relationship")),
        })

    return ParseResult(book.rows(), edges, report,
                       {name: paths[role] for role, name in files.items()})
