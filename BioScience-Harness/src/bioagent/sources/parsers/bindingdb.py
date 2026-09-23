"""BindingDB: measured protein–small-molecule affinities (Ki, IC50, Kd, EC50).

The download is a TSV (usually zipped) whose row ends with a block of columns repeated
once per protein chain of the target, so the header names only the first block. Column
positions follow BindingDB's "Tab-Separated Value (TSV) Files" description; names are
matched case- and spacing-insensitively because spellings drift between releases.

The full file is several gigabytes, so the parser keeps only ligands whose InChIKey is in
``inchikeys`` — the compounds a study actually needs. The download page requires an
interactive step, so the file is obtained by a person and imported (``manual`` access).

Licences are per record: data curated by BindingDB are CC-BY-4.0, data BindingDB took
from ChEMBL are CC-BY-SA-3.0 (``Curation/DataSource`` says which). Each measured value
(Ki, IC50, Kd, EC50) is one ``targets`` edge; a row may carry more than one. The first
chain with a UniProt accession identifies the target; rows with none are counted and
dropped, as are rows citing no PMID, DOI or patent.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable

from .common import (NodeBook, ParseReport, ParseResult, clean, compound_id, is_inchikey,
                     is_uniprot, open_text, parse_measure, publication, target_id)

__all__ = ["parse_bindingdb", "LICENSES"]

KEY = "bindingdb"
LICENSES = {"chembl": "CC-BY-SA-3.0"}
DEFAULT_LICENSE = "CC-BY-4.0"
MEASURES = {"ki (nm)": "Ki", "ic50 (nm)": "IC50", "kd (nm)": "Kd", "ec50 (nm)": "EC50"}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def parse_bindingdb(path: str | Path, *, inchikeys: Iterable[str]) -> ParseResult:
    path = Path(path)
    keep = {k.upper() for k in inchikeys}
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    edges: list[dict[str, Any]] = []

    with open_text(path) as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = [_norm(h) for h in next(reader)]

        def col(prefix: str) -> int:
            for i, h in enumerate(header):
                if h.startswith(prefix):
                    return i
            raise ValueError(f"BindingDB header has no column starting {prefix!r}")

        c = {name: col(prefix) for name, prefix in {
            "reactant": "bindingdb reactant_set_id", "inchikey": "ligand inchi key",
            "monomer": "bindingdb monomerid", "ligand": "bindingdb ligand name",
            "target": "target name", "curation": "curation/datasource",
            "doi": "article doi", "pmid": "pmid", "patent": "patent number",
            "cid": "pubchem cid", "chembl": "chembl id of ligand",
            "chains": "number of protein chains in target"}.items()}
        measures = {kind: col(prefix) for prefix, kind in MEASURES.items()}
        block = header[c["chains"] + 1:]
        acc_offset = next((i for i, h in enumerate(block)
                           if h.startswith("uniprot (swissprot) primary id of target chain")), None)
        if acc_offset is None or not block:
            raise ValueError("BindingDB header has no per-chain UniProt primary id column")

        for row in reader:
            report.read["rows"] += 1
            get = (lambda i: clean(row[i]) if i < len(row) else None)
            ik = (get(c["inchikey"]) or "").upper()
            if ik not in keep:
                continue
            if not is_inchikey(ik):
                report.drop("ligand without a valid InChIKey")
                continue
            accession = None
            chains = get(c["chains"])
            n = int(chains) if chains and chains.isdigit() else 1
            for k in range(max(n, 1)):
                value = get(c["chains"] + 1 + k * len(block) + acc_offset)
                if is_uniprot(value):
                    accession = value
                    break
            if accession is None:
                report.drop("target chain without a UniProt accession")
                continue
            pubs = [p for p in (publication(get(c["pmid"]), "pmid"),
                                publication(get(c["doi"]), "doi")) if p]
            if get(c["patent"]):
                pubs.append(f"patent:{get(c['patent'])}")
            if not pubs:
                report.drop("measurement without a PMID, DOI or patent")
                continue
            curation = get(c["curation"]) or ""
            licence = LICENSES.get(curation.lower(), DEFAULT_LICENSE)
            monomer = get(c["monomer"]) or ik
            compound = book.add(
                compound_id(KEY, monomer, ik), "ingredient", get(c["ligand"]) or ik,
                xrefs={"inchikey": [ik], KEY: [monomer],
                       "pubchem": [get(c["cid"])] if get(c["cid"]) else [],
                       "chembl": [get(c["chembl"])] if get(c["chembl"]) else []})
            target = book.add(target_id(KEY, accession, accession), "target", get(c["target"]),
                              xrefs={"uniprot": [accession]})
            found = False
            for kind, idx in measures.items():
                measure = parse_measure(kind, get(idx), "nM")
                if measure is None:
                    continue
                found = True
                edges.append({
                    "subject": compound, "predicate": "targets", "object": target,
                    "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
                    "study_design": "in_vitro", "license": licence,
                    "source_record_id": f"{get(c['reactant'])}|{measure['type']}",
                    "primary_knowledge_source": "chembl" if curation.lower() == "chembl" else KEY,
                    **({"aggregator_knowledge_source": KEY} if curation.lower() == "chembl" else {}),
                    "publications": pubs, "measure": measure,
                    "raw": {"curation": curation} if curation else {},
                })
            if not found:
                report.drop("row without Ki, IC50, Kd or EC50")
    return ParseResult(book.rows(), edges, report, {path.name: path})
