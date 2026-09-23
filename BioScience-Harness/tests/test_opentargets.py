"""Open Targets target-disease associations: fetch, parse, snapshot. The indication
overlap they feed is tested in ``test_network_pharmacology``.

The saved answer uses the real shape of the Platform's GraphQL response (API 26.6); the
rows are invented, so no upstream data is redistributed with the tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bioagent.sources import validate_edge, validate_node
from bioagent.sources.build import build_source
from bioagent.sources.cards import card
from bioagent.sources.fetch_opentargets import (OpenTargetsFetchError,
                                                fetch_disease_associations, raw_file_name)
from bioagent.sources.parsers import parse_opentargets
from bioagent.status import ExecutionStatus

DISEASE = {"id": "MONDO_0005148", "name": "type 2 diabetes mellitus",
           "dbXRefs": ["DOID:9352", "ICD10CM:E11"]}


def ot_row(ensg: str, uniprot: str | None, score: float, **types: float) -> dict:
    proteins = [{"id": uniprot, "source": "uniprot_swissprot"}] if uniprot else []
    proteins.append({"id": "A0A000", "source": "uniprot_trembl"})
    return {"score": score,
            "datatypeScores": [{"id": k, "score": v} for k, v in types.items()],
            "target": {"id": ensg, "approvedSymbol": f"SYM{ensg[-3:]}",
                       "approvedName": f"protein {ensg[-3:]}", "proteinIds": proteins}}


def ot_answer(rows: list[dict], path: Path) -> Path:
    path.write_text(json.dumps({"api_version": "26.6.3", "data_version": "26.06",
                                "fetched_at": "2026-09-23T00:00:00Z", "disease": DISEASE,
                                "rows": rows}), encoding="utf-8")
    return path


# ------------------------------------------------------------------ parse and build
def test_each_evidence_type_is_its_own_labelled_association(tmp_path):
    path = ot_answer([ot_row("ENSG00000000001", "P10000", 0.8, genetic_association=0.9,
                         literature=0.4),
                    ot_row("ENSG00000000002", None, 0.3, literature=0.5)], tmp_path / "ot.json")
    result = parse_opentargets(path)
    nodes = {n["id"]: n for n in result.nodes}
    assert nodes["mondo:0005148"]["category"] == "disease"
    assert nodes["mondo:0005148"]["xrefs"]["doid"] == ["9352"]
    assert nodes["uniprot:P10000"]["xrefs"]["ensembl"] == ["ENSG00000000001"]
    assert "ensembl:ENSG00000000002" in nodes            # no Swiss-Prot entry: kept, counted
    assert result.report.as_dict()["dropped"] == {
        "target without a Swiss-Prot accession (kept by Ensembl id)": 1}
    by_kind = {(e["subject"], e["score_name"]): e for e in result.edges}
    assert set(by_kind) == {("uniprot:P10000", "overall"),
                            ("uniprot:P10000", "genetic_association"),
                            ("uniprot:P10000", "literature"),
                            ("ensembl:ENSG00000000002", "overall"),
                            ("ensembl:ENSG00000000002", "literature")}
    assert by_kind[("uniprot:P10000", "genetic_association")]["knowledge_level"] == (
        "statistical_association")
    assert by_kind[("uniprot:P10000", "literature")]["knowledge_level"] == "text_co_occurrence"
    for e in result.edges:
        assert e["study_design"] == "evidence_aggregate" and e["predicate"] == "associated_with"
        assert validate_edge(e) == []
    for n in result.nodes:
        assert validate_node(n) == []


def test_the_snapshot_version_is_the_platform_release_and_the_disease(tmp_path):
    path = ot_answer([ot_row("ENSG00000000001", "P10000", 0.8, genetic_association=0.9)],
                   tmp_path / "ot.json")
    snap = build_source("opentargets", ".", tmp_path / "snap", path=path)
    assert snap.snapshot_id.startswith("opentargets@26.06+MONDO_0005148#")
    assert snap.manifest["content"]["license"] == card("opentargets").license == "CC0-1.0"


# ------------------------------------------------------------------ fetch
class _Pages:
    """A backend answering the meta query and then pages of ``rows``."""

    def __init__(self, rows: list[dict], page: int, lose: int = 0):
        self.rows, self.page, self.lose, self.calls = rows, page, lose, []

    def request(self, req, use_cache=True):
        body = req.json_body
        self.calls.append(body["variables"])
        if "meta" in body["query"]:
            return (ExecutionStatus.SUCCEEDED, {"data": {"meta": {
                "apiVersion": {"x": 26, "y": 6, "z": 3},
                "dataVersion": {"year": "26", "month": "06", "iteration": "0"}}}}, None, {})
        i, n = body["variables"]["i"], self.page
        rows = self.rows[i * n:(i + 1) * n]
        if i == 1 and self.lose:
            rows = rows[:-self.lose]
        return (ExecutionStatus.SUCCEEDED, {"data": {"disease": {
            **DISEASE, "associatedTargets": {"count": len(self.rows), "rows": rows}}}},
            None, {})


def test_the_fetch_pages_until_every_association_is_saved(tmp_path, monkeypatch):
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    rows = [ot_row(f"ENSG0000000000{i}", f"P1000{i}", 0.5) for i in (3, 1, 2, 5, 4)]
    backend = _Pages(rows, page=2)
    out = fetch_disease_associations("MONDO_0005148", tmp_path, backend=backend)
    assert out.name == raw_file_name("MONDO_0005148")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["data_version"] == "26.06" and saved["api_version"] == "26.6.3"
    assert [r["target"]["id"] for r in saved["rows"]] == sorted(r["target"]["id"] for r in rows)
    assert [c.get("i") for c in backend.calls] == [None, 0, 1, 2]


def test_a_short_answer_is_an_error_not_a_smaller_snapshot(tmp_path, monkeypatch):
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    rows = [ot_row(f"ENSG0000000000{i}", f"P1000{i}", 0.5) for i in range(5)]
    with pytest.raises(OpenTargetsFetchError, match="expected 5"):
        fetch_disease_associations("MONDO_0005148", tmp_path, backend=_Pages(rows, 2, lose=1))
    assert not (tmp_path / raw_file_name("MONDO_0005148")).exists()
