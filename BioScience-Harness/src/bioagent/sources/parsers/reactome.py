"""Reactome: protein -> pathway membership (``UniProt2Reactome.txt``, CC0).

Columns (headerless): UniProt accession, pathway stable id, URL, pathway name, evidence
code, species. The file lists the lowest-level pathways each protein is annotated to.

Evidence codes: ``TAS`` (traceable author statement) is Reactome's curated annotation,
recorded as expert-curated knowledge; ``IEA`` (inferred from electronic annotation) is a
computational inference — mostly orthology projection to other species — and is labelled
a prediction. Membership in a pathway says what a protein takes part in, not what a
compound does to it; the release check treats it as annotation, not as evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .common import NodeBook, ParseReport, ParseResult, is_uniprot, read_rows

__all__ = ["FILE", "parse_reactome"]

KEY = "reactome"
LICENSE = "CC0-1.0"
FILE = "UniProt2Reactome.txt"
_COLUMNS = ("uniprot", "pathway", "url", "name", "evidence", "species")


def parse_reactome(path: str | Path, *, proteins: Iterable[str] | None = None,
                   species: str = "Homo sapiens") -> ParseResult:
    path = Path(path)
    scope = None if proteins is None else set(proteins)
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    edges = []
    for row in read_rows(path, fieldnames=_COLUMNS):
        report.read["rows"] += 1
        acc, pathway = row.get("uniprot"), row.get("pathway")
        if row.get("species") != species:
            continue
        if scope is not None and acc not in scope:
            continue
        if not is_uniprot(acc) or not pathway:
            report.drop("row without a UniProt accession or a pathway id")
            continue
        curated = row.get("evidence") == "TAS"
        book.add(f"uniprot:{acc}", "target", acc, xrefs={"uniprot": [acc]})
        book.add(f"reactome:{pathway}", "pathway", row.get("name"),
                 xrefs={KEY: [pathway]})
        edges.append({
            "subject": f"uniprot:{acc}", "predicate": "participates_in",
            "object": f"reactome:{pathway}",
            "knowledge_level": "knowledge_assertion" if curated else "prediction",
            "agent_type": "manual_agent" if curated else "automated_agent",
            "study_design": "expert_consensus" if curated else "in_silico",
            "license": LICENSE, "source_record_id": f"{acc}|{pathway}",
            "primary_knowledge_source": KEY, "raw": {"evidence": row.get("evidence")},
        })
    return ParseResult(book.rows(), edges, report, {path.name: path})
