"""STRING v12 human protein-protein associations, restricted to a set of proteins.

Files: ``9606.protein.links.v12.0.txt.gz`` (space-separated: protein1 protein2
combined_score, 0-1000), ``9606.protein.aliases.v12.0.txt.gz`` (STRING id -> alias,
source; ``UniProt_AC`` rows give the accessions) and ``9606.protein.info.v12.0.txt.gz``
(preferred names).

STRING's combined score integrates experiments, curated databases, text mining,
co-expression and genomic-context predictions into one probabilistic confidence. An edge
is therefore a ``prediction`` of functional association (``computational_model``,
``in_silico``) whatever its score, and the score is kept as it is. No score cut-off is
applied here: choosing one (400 medium, 700 high, 900 highest) is an analysis decision
that belongs in the analysis and its protocol.

The full human network has about 13 million links, so the parser keeps the subnetwork
over ``proteins`` (UniProt accessions): ``induced`` keeps links between two proteins in
the set; ``neighbours`` also keeps links from the set to any other protein.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .common import (NodeBook, ParseReport, ParseResult, clean, is_uniprot, open_text,
                     read_rows)

__all__ = ["FILES", "parse_string"]

KEY = "string"
LICENSE = "CC-BY-4.0"
FILES = {
    "links": "9606.protein.links.v12.0.txt.gz",
    "aliases": "9606.protein.aliases.v12.0.txt.gz",
    "info": "9606.protein.info.v12.0.txt.gz",
}


def parse_string(raw_dir: str | Path, *, proteins: Iterable[str], mode: str = "induced",
                 files: dict[str, str] = FILES) -> ParseResult:
    if mode not in ("induced", "neighbours"):
        raise ValueError("mode is induced or neighbours")
    raw_dir = Path(raw_dir)
    paths = {role: raw_dir / name for role, name in files.items()}
    scope = {p for p in proteins if is_uniprot(p)}
    report = ParseReport(KEY)
    book = NodeBook(KEY)

    accessions: dict[str, list[str]] = {}
    for row in read_rows(paths["aliases"]):
        report.read["aliases"] += 1
        if row.get("source") == "UniProt_AC" and is_uniprot(row.get("alias")):
            accessions.setdefault(row["#string_protein_id"], []).append(row["alias"])
    names = {row["#string_protein_id"]: row.get("preferred_name")
             for row in read_rows(paths["info"]) if row.get("#string_protein_id")}

    def accession(string_id: str) -> str | None:
        """The in-scope accession of a STRING protein, else its first accession."""
        accs = accessions.get(string_id) or []
        inside = [a for a in accs if a in scope]
        return (sorted(inside) or sorted(accs) or [None])[0]

    in_scope = {sid for sid, accs in accessions.items() if any(a in scope for a in accs)}
    edges = []
    seen: set[tuple[str, str]] = set()
    with open_text(paths["links"]) as fh:
        next(fh)                                           # protein1 protein2 combined_score
        for line in fh:
            report.read["links"] += 1
            parts = line.split()
            if len(parts) != 3:
                report.drop("malformed link line")
                continue
            a, b, score = parts
            inside = (a in in_scope) + (b in in_scope)
            if inside == 0 or (mode == "induced" and inside < 2):
                continue
            acc_a, acc_b = accession(a), accession(b)
            if not acc_a or not acc_b:
                report.drop("STRING protein without a UniProt accession")
                continue
            pair = tuple(sorted((acc_a, acc_b)))
            if pair in seen or acc_a == acc_b:             # STRING lists each link twice
                continue
            seen.add(pair)
            for acc, sid in ((acc_a, a), (acc_b, b)):
                book.add(f"uniprot:{acc}", "target", clean(names.get(sid)) or acc,
                         xrefs={"uniprot": [acc], KEY: [sid]})
            edges.append({
                "subject": f"uniprot:{pair[0]}", "predicate": "interacts_with",
                "object": f"uniprot:{pair[1]}", "knowledge_level": "prediction",
                "agent_type": "computational_model", "study_design": "in_silico",
                "license": LICENSE, "source_record_id": f"{a}|{b}",
                "primary_knowledge_source": KEY,
                "score": int(score) / 1000, "score_name": "combined_score",
            })
    missing = scope - {n["xrefs"]["uniprot"][0] for n in book.rows()}
    report.read["proteins_in_scope"] = len(scope)
    if missing:
        report.drop("protein in scope with no STRING link to another in-scope protein",
                    len(missing))
    return ParseResult(book.rows(), edges, report,
                       {name: paths[role] for role, name in files.items()})
