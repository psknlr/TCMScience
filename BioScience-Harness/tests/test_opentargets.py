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
class _API:
    """A backend answering the meta query and ``associatedTargets`` over ``rows``.

    ``BFilter`` keeps rows whose target id or symbol starts with it (case-insensitive, as
    the real API does); ``count`` is the number kept. ``reorder`` shuffles the kept rows
    differently on every request, as the real API does with tied scores.
    """

    def __init__(self, rows: list[dict], *, lose: int = 0, reorder: bool = False,
                 release_midway: bool = False):
        self.rows, self.lose, self.reorder = rows, lose, reorder
        self.release_midway, self.metas, self.calls = release_midway, 0, []

    def request(self, req, use_cache=True):
        import random

        body = req.json_body
        self.calls.append(body.get("variables") or {})
        if "meta" in body["query"]:
            self.metas += 1
            month = "09" if self.release_midway and self.metas > 1 else "06"
            return (ExecutionStatus.SUCCEEDED, {"data": {"meta": {
                "apiVersion": {"x": 26, "y": 6, "z": 3},
                "dataVersion": {"year": "26", "month": month, "iteration": "0"}}}}, None, {})
        v = body["variables"]
        f = (v.get("f") or "").upper()
        kept = [r for r in self.rows if r["target"]["id"].upper().startswith(f)
                or r["target"]["approvedSymbol"].upper().startswith(f)]
        if self.reorder:
            random.Random(len(self.calls)).shuffle(kept)
        page = kept[v["i"] * v["n"]:(v["i"] + 1) * v["n"]]
        if self.lose and page:
            page = page[:-self.lose]
        return (ExecutionStatus.SUCCEEDED, {"data": {"disease": {
            **DISEASE, "associatedTargets": {"count": len(kept), "rows": page}}}}, None, {})


def _ids(*numbers: int) -> list[dict]:
    return [ot_row(f"ENSG{n:011d}", f"P{n:05d}", 0.5) for n in numbers]


SPREAD = (3, 12, 17, 105, 110, 111, 250)


def test_a_short_answer_is_saved_from_one_request(tmp_path):
    backend = _API(_ids(3, 1, 2))
    out = fetch_disease_associations("MONDO_0005148", tmp_path, backend=backend)
    assert out.name == raw_file_name("MONDO_0005148")
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["data_version"] == "26.06" and saved["api_version"] == "26.6.3"
    assert [r["target"]["id"] for r in saved["rows"]] == [
        f"ENSG{n:011d}" for n in (1, 2, 3)]
    # meta, the one page, and meta again: the release must not have changed underneath.
    assert [c.get("f", "meta") for c in backend.calls] == ["meta", None, "meta"]


def test_a_long_answer_is_split_by_id_until_each_group_fits_one_page(tmp_path, monkeypatch):
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    backend = _API(_ids(*SPREAD))
    out = fetch_disease_associations("MONDO_0005148", tmp_path, backend=backend)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert [r["target"]["id"] for r in saved["rows"]] == [f"ENSG{n:011d}" for n in SPREAD]
    # Past the unfiltered first page, rows come only from groups that fit one page: ids
    # ...0003 | ...0012 ...0017 | ...0105 | ...0110 ...0111 | ...0250. Everything else
    # was an empty page asking for a count.
    groups = [c["f"] for c in backend.calls if c.get("n") and c.get("f")]
    assert groups == ["ENSG0000000000", "ENSG0000000001", "ENSG0000000010",
                      "ENSG0000000011", "ENSG000000002"]
    assert all(c["i"] == 0 for c in backend.calls if "i" in c)


def test_an_order_that_changes_between_requests_loses_no_target(tmp_path, monkeypatch):
    """What the live API does with tied scores (2026-09-30): paging one list repeated some
    targets and dropped as many. Groups fetched whole do not depend on the order."""
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    backend = _API(_ids(*SPREAD), reorder=True)
    out = fetch_disease_associations("MONDO_0005148", tmp_path, backend=backend)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert [r["target"]["id"] for r in saved["rows"]] == [f"ENSG{n:011d}" for n in SPREAD]


def test_the_saved_rows_do_not_depend_on_the_order_the_api_lists_scores_in(tmp_path):
    row = ot_row("ENSG00000000001", "P10000", 0.8, literature=0.4, genetic_association=0.9)
    flipped = {**row, "datatypeScores": row["datatypeScores"][::-1],
               "target": {**row["target"], "proteinIds": row["target"]["proteinIds"][::-1]}}
    saved = []
    for i, r in enumerate((row, flipped)):
        out = fetch_disease_associations("MONDO_0005148", tmp_path / str(i), backend=_API([r]))
        saved.append(json.loads(out.read_text(encoding="utf-8"))["rows"])
    assert saved[0] == saved[1]
    assert [d["id"] for d in saved[0][0]["datatypeScores"]] == ["genetic_association",
                                                                "literature"]


def test_a_short_answer_is_an_error_not_a_smaller_snapshot(tmp_path, monkeypatch):
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    with pytest.raises(OpenTargetsFetchError, match=r"expected 1 associations under \w+, "
                                                    r"received 0"):
        fetch_disease_associations("MONDO_0005148", tmp_path,
                                   backend=_API(_ids(*SPREAD), lose=1))
    assert not (tmp_path / raw_file_name("MONDO_0005148")).exists()


def test_a_target_in_two_groups_is_an_error_even_when_the_total_matches(tmp_path, monkeypatch):
    """``BFilter`` matches symbols too; a symbol that looks like another group's id puts
    one target in two groups, and a total that matches cannot tell."""
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    rows = _ids(*SPREAD)
    rows[0]["target"]["approvedSymbol"] = "ENSG00000000250"    # also under ...025
    with pytest.raises(OpenTargetsFetchError, match="cannot be split|repeats 1 target"):
        fetch_disease_associations("MONDO_0005148", tmp_path, backend=_API(rows))
    assert not (tmp_path / raw_file_name("MONDO_0005148")).exists()


def test_ids_outside_the_ensembl_gene_space_cannot_be_split(tmp_path, monkeypatch):
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    rows = _ids(*SPREAD) + [ot_row("OTAR0000001", "P99999", 0.5)]
    with pytest.raises(OpenTargetsFetchError, match="cannot be split"):
        fetch_disease_associations("MONDO_0005148", tmp_path, backend=_API(rows))


def test_a_release_during_the_fetch_is_an_error(tmp_path, monkeypatch):
    import bioagent.sources.fetch_opentargets as fetch
    monkeypatch.setattr(fetch, "PAGE_SIZE", 2)
    with pytest.raises(OpenTargetsFetchError, match="data version changed"):
        fetch_disease_associations("MONDO_0005148", tmp_path,
                                   backend=_API(_ids(*SPREAD), release_midway=True))
    assert not (tmp_path / raw_file_name("MONDO_0005148")).exists()
