"""Open Targets target-disease associations (one disease), from the saved API answer.

Each association carries an overall score and one score per evidence type. The types are
kept apart, as separate edges, because they are different kinds of statement:

* ``genetic_association`` / ``somatic_mutation`` — statistical associations in human
  genetic data;
* ``literature`` — co-mention in text (Europe PMC text mining);
* ``known_drug`` / ``clinical`` — a drug acting on the target has been tested in the
  disease;
* ``affected_pathway``, ``rna_expression``, ``animal_model`` — curated pathway,
  expression and model-organism evidence.

Every edge is ``associated_with`` with the study design ``evidence_aggregate``: an
Open Targets score aggregates many evidence items and licenses no claim on its own. An
analysis chooses which evidence type defines its disease gene set, and says so — defining
"disease genes" from literature co-mention would be circular for a literature-derived
compound network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import NodeBook, ParseReport, ParseResult, is_uniprot

__all__ = ["parse_opentargets", "KNOWLEDGE_LEVEL"]

KEY = "opentargets"
LICENSE = "CC0-1.0"

#: Open Targets evidence type -> Biolink knowledge level
KNOWLEDGE_LEVEL = {
    "overall": "not_provided",
    "genetic_association": "statistical_association",
    "somatic_mutation": "statistical_association",
    "genetic_literature": "knowledge_assertion",
    "literature": "text_co_occurrence",
    "known_drug": "knowledge_assertion",
    "clinical": "knowledge_assertion",
    "affected_pathway": "knowledge_assertion",
    "rna_expression": "statistical_association",
    "animal_model": "observation",
}


def parse_opentargets(path: str | Path) -> ParseResult:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    disease = data["disease"]
    disease_id = disease["id"]
    prefix, _, local = disease_id.partition("_")
    disease_node = f"{prefix.lower()}:{local}" if local else f"{KEY}:{disease_id}"
    book.add(disease_node, "disease", disease.get("name"),
             xrefs={"opentargets": [disease_id],
                    **{x.split(":", 1)[0].lower(): [x.split(":", 1)[1]]
                       for x in disease.get("dbXRefs") or [] if ":" in x}})
    edges = []
    for row in data["rows"]:
        report.read["associations"] += 1
        target = row["target"]
        swissprot = sorted(p["id"] for p in target.get("proteinIds") or []
                           if p.get("source") == "uniprot_swissprot" and is_uniprot(p["id"]))
        if swissprot:
            node = f"uniprot:{swissprot[0]}"
        else:
            node = f"ensembl:{target['id']}"
            report.drop("target without a Swiss-Prot accession (kept by Ensembl id)")
        book.add(node, "target", target.get("approvedSymbol") or target["id"],
                 xrefs={"ensembl": [target["id"]], "uniprot": swissprot[:1],
                        "hgnc_symbol": [target.get("approvedSymbol") or ""]},
                 names={"en": [target.get("approvedName") or ""]})
        scores = {"overall": row["score"],
                  **{d["id"]: d["score"] for d in row.get("datatypeScores") or []}}
        for kind, score in sorted(scores.items()):
            if kind not in KNOWLEDGE_LEVEL:
                report.drop(f"unknown evidence type {kind}")
                continue
            edges.append({
                "subject": node, "predicate": "associated_with", "object": disease_node,
                "knowledge_level": KNOWLEDGE_LEVEL[kind], "agent_type": "data_analysis_pipeline",
                "study_design": "evidence_aggregate", "license": LICENSE,
                "source_record_id": f"{target['id']}|{disease_id}|{kind}",
                "primary_knowledge_source": KEY, "score": round(float(score), 6),
                "score_name": kind,
            })
    return ParseResult(book.rows(), edges, report, {path.name: path})
