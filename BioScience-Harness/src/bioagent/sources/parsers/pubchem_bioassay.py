"""PubChem BioAssay results for a compound set, active *and* inactive, from the saved fetch.

One edge per deposited result on a protein target:

* ``Active`` -> ``targets``: the depositor's call that the compound is active in the assay;
* ``Inactive`` -> ``tested_against``: the compound was tested and was not active. This is
  what curated activity databases rarely keep, and what makes "which proteins were
  tested" a usable background.

``Inconclusive`` and ``Unspecified`` outcomes, results without a protein target, RNAi
screens and targets that do not map to a human UniProt accession are left out and
counted. Both edge kinds are ``in_vitro`` observations; the citation is the assay itself
(``pubchem.bioassay:<AID>``, which anyone can look up) plus the PMID when the depositor
gave one. The assay type (Screening / Confirmatory / Other / Summary), the assay name and
the depositor's activity value are kept, so an analysis can choose which results count.

NCBI Gene ids are mapped to UniProt through STRING's alias file (``UniProt_DR_GeneID`` /
``Ensembl_HGNC_entrez_id`` to a STRING protein, ``UniProt_AC`` to its accessions), keeping
the one accession that is a **UniProtKB/Swiss-Prot primary accession** — the namespace
NPASS, CMAUP, BindingDB, Reactome and Open Targets all use. STRING lists every accession
of an entry, secondary and TrEMBL ones included, and does not say which is primary; the
first one listed is the primary for only 23% of human STRING proteins. Taking it put
PubChem targets on accessions that joined Reactome for 2,422 of 19,197 mapped genes,
where the primary joins for 11,201. So the primary list
(``uniprot_human_reviewed_accessions.txt``, from UniProt) is an input like the alias file,
and its hash is part of the snapshot.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterable

from .common import NodeBook, ParseReport, ParseResult, clean, is_uniprot, parse_measure, read_rows

__all__ = ["parse_pubchem_bioassay", "gene_to_uniprot", "read_primary", "PRIMARY_FILE"]

KEY = "pubchem_bioassay"
LICENSE = "NCBI-data-policy"
#: One UniProtKB/Swiss-Prot primary accession per line (``format=list`` from UniProt's REST
#: service, ``organism_id:9606 AND reviewed:true``).
PRIMARY_FILE = "uniprot_human_reviewed_accessions.txt"
_GENE_SOURCES = ("UniProt_DR_GeneID", "Ensembl_HGNC_entrez_id")
_EDGE = {"Active": "targets", "Inactive": "tested_against"}


def read_primary(path: str | Path) -> frozenset[str]:
    """The Swiss-Prot primary accessions in a UniProt ``format=list`` file."""
    with open(path, encoding="utf-8") as fh:
        return frozenset(line.strip() for line in fh if is_uniprot(line.strip()))


def gene_to_uniprot(aliases: str | Path, *, primary: Iterable[str]) -> dict[str, str]:
    """NCBI Gene id -> Swiss-Prot primary accession for human proteins.

    A gene maps when the STRING proteins it names carry exactly one primary accession
    between them: two STRING proteins that are one UniProt entry agree; two different
    proteins are ambiguous and the gene is left out, as is a gene with no reviewed entry.
    """
    primary = frozenset(primary)
    genes: dict[str, set[str]] = {}
    accessions: dict[str, list[str]] = {}
    for row in read_rows(aliases):
        source, alias, protein = row.get("source"), row.get("alias"), row.get("#string_protein_id")
        if source in _GENE_SOURCES and alias and alias.isdigit():
            genes.setdefault(alias, set()).add(protein)
        elif source == "UniProt_AC" and is_uniprot(alias):
            accessions.setdefault(protein, []).append(alias)
    out = {}
    for gene, proteins in genes.items():
        candidates = {a for p in proteins for a in accessions.get(p, ()) if a in primary}
        if len(candidates) == 1:
            out[gene] = next(iter(candidates))
    return out


def parse_pubchem_bioassay(path: str | Path, *, aliases: str | Path,
                           primary: str | Path) -> ParseResult:
    path, aliases, primary = Path(path), Path(aliases), Path(primary)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    report = ParseReport(KEY)
    book = NodeBook(KEY)
    uniprot = gene_to_uniprot(aliases, primary=read_primary(primary))
    inchikey_of = {str(cid): key for key, cids in data["cids"].items() for cid in cids}
    col = {name: i for i, name in enumerate(data["columns"])}
    edges, seen = [], set()
    for cells in data["rows"]:
        report.read["results"] += 1
        get = lambda name: clean(cells[col[name]]) if name in col else None  # noqa: E731
        outcome = get("Activity Outcome")
        if outcome not in _EDGE:
            report.drop(f"outcome {outcome or 'missing'} (neither active nor inactive)")
            continue
        if get("RNAi"):
            report.drop("RNAi screen")
            continue
        gene = get("Target GeneID")
        if not gene:
            report.drop("no protein target")
            continue
        acc = uniprot.get(gene)
        if acc is None:
            report.drop("target gene is not one human protein with a Swiss-Prot primary "
                        "accession")
            continue
        key = inchikey_of.get(get("CID") or "")
        if key is None:
            report.drop("compound not in the queried set")
            continue
        aid = get("AID")
        # one result can carry several measurements (a Ki and an IC50 from one paper)
        record = (f"{aid}|{get('SID')}|{gene}|{get('Panel Member ID') or ''}|"
                  f"{get('Activity Name') or ''}|{get('Activity Value [uM]') or ''}")
        if record in seen:
            report.drop("duplicate result")
            continue
        seen.add(record)
        compound = book.add(f"inchikey:{key}", "ingredient", key,
                            xrefs={"inchikey": [key], "pubchem.compound": [get("CID")]})
        target = book.add(f"uniprot:{acc}", "target", acc,
                          xrefs={"uniprot": [acc], "ncbigene": [gene]})
        pmid = get("PubMed ID")
        edge = {
            "subject": compound, "predicate": _EDGE[outcome], "object": target,
            "knowledge_level": "observation", "agent_type": "not_provided",
            "study_design": "in_vitro", "license": LICENSE, "source_record_id": record,
            "primary_knowledge_source": KEY, "outcome": outcome.lower(),
            "assay_type": get("Assay Type"), "assay_name": get("Assay Name"),
            "publications": [f"pubchem.bioassay:{aid}"]
                            + ([f"pmid:{pmid}"] if pmid and pmid.isdigit() else []),
        }
        measure = parse_measure(get("Activity Name"), get("Activity Value [uM]"), "uM")
        if measure is not None:
            measure = {**measure, "value": round(measure["value"] * 1000, 6), "unit": "nM"}
            edge["measure"] = measure
        edges.append(edge)
    return ParseResult(book.rows(), edges, report,
                       {path.name: path, aliases.name: aliases, primary.name: primary})
