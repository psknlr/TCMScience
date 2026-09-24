"""PubChem BioAssay results, active and inactive: fetch, parse, snapshot.

The saved file and alias file use the real layouts (PUG-REST assay summary columns,
STRING v12 aliases); the rows are invented, so no upstream data is redistributed.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from bioagent.sources import validate_edge, validate_node
from bioagent.sources.build import build_source
from bioagent.sources.fetch_pubchem import RAW_FILE, PubChemFetchError, fetch_assay_summaries
from bioagent.sources.parsers import parse_pubchem_bioassay
from bioagent.sources.parsers.pubchem_bioassay import gene_to_uniprot
from bioagent.status import ExecutionStatus

COLUMNS = ["AID", "Panel Member ID", "SID", "CID", "Activity Outcome", "Target Accession",
           "Target GeneID", "Activity Value [uM]", "Activity Name", "Assay Name", "Assay Type",
           "PubMed ID", "RNAi"]
BERBERINE = "YBHILYKTIRIUTE-UHFFFAOYSA-N"
PUERARIN = "HKEAFJYKMMKDOR-VPRICQMDSA-N"


def aliases(d: Path) -> Path:
    path = d / "9606.protein.aliases.v12.0.txt.gz"
    rows = [("9606.ENSP1", "1544", "UniProt_DR_GeneID"), ("9606.ENSP1", "P05177", "UniProt_AC"),
            ("9606.ENSP2", "1559", "Ensembl_HGNC_entrez_id"), ("9606.ENSP2", "P11712", "UniProt_AC"),
            # one gene naming two proteins: ambiguous, not mapped
            ("9606.ENSP3", "7777", "UniProt_DR_GeneID"), ("9606.ENSP3", "Q16678", "UniProt_AC"),
            ("9606.ENSP4", "7777", "UniProt_DR_GeneID"), ("9606.ENSP4", "P10635", "UniProt_AC")]
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write("#string_protein_id\talias\tsource\n")
        fh.writelines("\t".join(r) + "\n" for r in rows)
    return path


def row(aid, cid, outcome, gene="1544", value="", name="", assay="Confirmatory", pmid="",
        rnai="", sid="11"):
    return [str(aid), "", sid, str(cid), outcome, "NP_1", gene, value, name, "p450 panel",
            assay, pmid, rnai]


def saved(d: Path, rows: list[list[str]]) -> Path:
    path = d / RAW_FILE
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"fetched_at": "2026-09-24T01:00:00Z", "service": "fixture",
                   "inchikeys": [BERBERINE, PUERARIN],
                   "cids": {BERBERINE: [2353], PUERARIN: [5281807]},
                   "columns": COLUMNS, "rows": rows}, fh)
    return path


def test_active_and_inactive_results_are_kept_apart(tmp_path):
    aliases(tmp_path)
    path = saved(tmp_path, [
        row(410, 2353, "Active", value="1.5", name="IC50", pmid="123"),
        row(410, 2353, "Active", value="2.0", name="Ki"),        # a second measurement
        row(411, 5281807, "Inactive", gene="1559", assay="Screening"),
        row(412, 2353, "Inconclusive"),
        row(413, 2353, "Unspecified"),
        row(414, 2353, "Active", gene=""),
        row(415, 2353, "Active", rnai="1"),
        row(416, 2353, "Active", gene="7777"),
        row(417, 999, "Active"),
        row(410, 2353, "Active", value="1.5", name="IC50", pmid="123"),   # the same again
    ])
    result = parse_pubchem_bioassay(path, aliases=tmp_path / "9606.protein.aliases.v12.0.txt.gz")
    edges = sorted(result.edges, key=lambda e: e["source_record_id"])
    assert [(e["predicate"], e["object"]) for e in edges] == [
        ("targets", "uniprot:P05177"), ("targets", "uniprot:P05177"),
        ("tested_against", "uniprot:P11712")]
    first = edges[0]
    assert first["measure"] == {"type": "IC50", "relation": "=", "value": 1500.0, "unit": "nM"}
    assert first["publications"] == ["pubchem.bioassay:410", "pmid:123"]
    assert edges[2]["publications"] == ["pubchem.bioassay:411"]
    assert edges[2]["assay_type"] == "Screening" and edges[2]["outcome"] == "inactive"
    dropped = result.report.as_dict()["dropped"]
    assert dropped == {
        "outcome Inconclusive (neither active nor inactive)": 1,
        "outcome Unspecified (neither active nor inactive)": 1,
        "no protein target": 1, "RNAi screen": 1,
        "target gene is not a human protein with one UniProt accession": 1,
        "compound not in the queried set": 1, "duplicate result": 1}
    for e in result.edges:
        assert validate_edge(e) == [], e
    for n in result.nodes:
        assert validate_node(n) == []


def test_an_ambiguous_gene_is_not_mapped(tmp_path):
    mapping = gene_to_uniprot(aliases(tmp_path))
    assert mapping == {"1544": "P05177", "1559": "P11712"}


def test_the_snapshot_version_is_the_fetch_date_and_the_compound_set(tmp_path):
    aliases(tmp_path)
    path = saved(tmp_path, [row(410, 2353, "Active")])
    snap = build_source("pubchem_bioassay", tmp_path, tmp_path / "snap", path=path)
    assert snap.snapshot_id.startswith("pubchem_bioassay@2026-09-24+subset-")


# ------------------------------------------------------------------ fetch
class _PubChem:
    """Answers the property and assay-summary services; fails batches larger than
    ``max_batch`` the way a timed-out request does."""

    def __init__(self, max_batch: int = 100, missing: tuple[int, ...] = ()):
        self.max_batch, self.missing, self.calls = max_batch, set(missing), []

    def request(self, req, use_cache=True):
        field, _, ids = req.data.decode().partition("=")
        ids = ids.replace("%2C", ",").split(",")
        self.calls.append((req.url.rsplit("/", 3)[-3], len(ids)))
        if len(ids) > self.max_batch:
            return ExecutionStatus.TIMEOUT, None, "timed out", {"http_status": None}
        if field == "inchikey":
            props = [{"CID": 2353 if k == BERBERINE else 5281807, "InChIKey": k}
                     for k in ids if k in (BERBERINE, PUERARIN)]
            return ExecutionStatus.SUCCEEDED, {"PropertyTable": {"Properties": props}}, "", {}
        rows = [{"Cell": row(410, cid, "Active")} for cid in map(int, ids)
                if cid not in self.missing]
        if not rows:
            return ExecutionStatus.FAILED, None, "HTTP 404", {"http_status": 404}
        return (ExecutionStatus.SUCCEEDED,
                {"Table": {"Columns": {"Column": COLUMNS}, "Row": rows}}, "", {})


def test_the_fetch_maps_keys_to_cids_and_saves_every_result(tmp_path):
    backend = _PubChem()
    out = fetch_assay_summaries([PUERARIN, BERBERINE, BERBERINE, "AAAAAAAAAAAAAA-UHFFFAOYSA-N"],
                                tmp_path, backend=backend)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["cids"] == {BERBERINE: [2353], PUERARIN: [5281807]}
    assert len(data["inchikeys"]) == 3 and len(data["rows"]) == 2
    assert data["columns"] == COLUMNS


def test_a_batch_that_fails_is_split_until_it_succeeds(tmp_path, monkeypatch):
    import bioagent.sources.fetch_pubchem as fetch
    monkeypatch.setattr(fetch, "CID_BATCH", 2)
    backend = _PubChem(max_batch=1)
    out = fetch_assay_summaries([PUERARIN, BERBERINE], tmp_path, backend=backend)
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert len(json.load(fh)["rows"]) == 2


def test_no_data_is_empty_but_a_failure_that_splitting_cannot_fix_is_an_error(tmp_path):
    out = fetch_assay_summaries([BERBERINE], tmp_path, backend=_PubChem(missing=(2353,)))
    with gzip.open(out, "rt", encoding="utf-8") as fh:
        assert json.load(fh)["rows"] == []
    with pytest.raises(PubChemFetchError):
        fetch_assay_summaries([BERBERINE], tmp_path, backend=_PubChem(max_batch=0))
