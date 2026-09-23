"""LOTUS: referenced structure-organism pairs (the frozen Zenodo export, CC-BY-4.0).

Reads either export: the light ``*_frozen.csv.gz`` (InChIKey, organism name, DOI,
Wikidata ids) or the ``*_frozen_metadata.csv.gz`` (adds names, PubChem CID and the NCBI
taxon id). With the light file an organism is matched by name, so a ``TaxonFilter``
should list synonyms (``Pueraria lobata`` for ``Pueraria montana var. lobata``).

Every pair cites a DOI; pairs are merged per (organism, structure) with all their DOIs.
``manual_validation`` marks pairs a person confirmed; the rest came from automated
aggregation of other databases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (NodeBook, ParseReport, ParseResult, TaxonFilter, clean, compound_id,
                     is_inchikey, publication, read_rows)

__all__ = ["parse_lotus"]

KEY = "lotus"
LICENSE = "CC-BY-4.0"
_TRUE = {"y", "yes", "true", "1"}


def _qid(uri: str | None) -> str | None:
    uri = clean(uri)
    return uri.rsplit("/", 1)[-1] if uri else None


def parse_lotus(path: str | Path, *, taxa: TaxonFilter | None = None) -> ParseResult:
    path = Path(path)
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    pairs: dict[tuple[str, str], dict[str, Any]] = {}

    for row in read_rows(path, delimiter=","):
        report.read["rows"] += 1
        ik = row.get("structure_inchikey")
        org_name = row.get("organism_name")
        if not is_inchikey(ik) or not org_name:
            report.drop("row without an InChIKey or an organism name")
            continue
        tax = row.get("organism_taxonomy_ncbiid")
        if taxa is not None:
            tax = taxa.by_id(tax) or taxa.by_name(org_name)
            if tax is None:
                continue
        org_q = _qid(row.get("organism_wikidata"))
        org_node = f"ncbitaxon:{tax}" if tax and tax.isdigit() else f"{KEY}:{org_q or org_name}"
        book.add(org_node, "organism", org_name,
                 xrefs={"wikidata": [org_q] if org_q else [],
                        "ncbitaxon": [tax] if tax and tax.isdigit() else []},
                 names={"latin": [org_name]})
        struct_q = _qid(row.get("structure_wikidata"))
        cid = row.get("structure_cid")
        name = row.get("structure_nameTraditional")
        book.add(compound_id(KEY, struct_q or ik, ik), "ingredient", name or ik,
                 xrefs={"inchikey": [ik], "wikidata": [struct_q] if struct_q else [],
                        "pubchem": [cid] if cid else []},
                 names={"en": [name]} if name else None)
        pair = pairs.setdefault((org_node, f"inchikey:{ik}"),
                                {"publications": set(), "validated": False,
                                 "record": f"{org_q}|{struct_q}"})
        doi = publication(row.get("reference_doi"), "doi")
        if doi:
            pair["publications"].add(doi)
        if (row.get("manual_validation") or "").lower() in _TRUE:
            pair["validated"] = True

    edges = [{
        "subject": org, "predicate": "contains", "object": compound,
        "knowledge_level": "knowledge_assertion",
        "agent_type": "manual_validation_of_automated_agent" if p["validated"] else "automated_agent",
        "study_design": "chemical_analysis", "composition_level": "C1", "license": LICENSE,
        "source_record_id": p["record"], "primary_knowledge_source": KEY,
        "publications": sorted(p["publications"]),
    } for (org, compound), p in sorted(pairs.items())]
    return ParseResult(book.rows(), edges, report, {path.name: path})
