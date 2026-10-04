"""The manual Hong Kong sources: HKBU formulas, HKCMMS standards, reference DNA.

Every row here is made up; the files use the reviewed TSV columns the adapter declares.
Nothing touches the network, and no real HKBU, HKCMMS or reference-sequence data is
involved. The guide's four admission cases are pinned: a manually verified correct record,
a same-name ambiguity that must not be silently merged, a record missing its source, and a
claim that must not pass the release gate from composition evidence alone.
"""

from __future__ import annotations

import csv

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.extra.traditional import (FORMULA_HERB_COLUMNS, KEY_DNA, KEY_FORMULAS,
                                              KEY_STANDARDS)

pytestmark = pytest.mark.unit

_FIELDS = list(FORMULA_HERB_COLUMNS)


def _row(**kw) -> dict:
    base = dict.fromkeys(_FIELDS, "")
    base.update(formula_id="demo-v1", formula_name="演示方剂（非真实）",
                formula_version="demo-v1", herb_id="demo-herb", herb_name="演示药材（非真实）",
                dose="1", dose_unit="g", reference="Synthetic example only",
                source_url="https://example.invalid/demo", locator="record 1",
                source_row_id="demo-row-1", review_status="verified")
    base.update(kw)
    return base


def _write_formulas(raw, rows: list[dict]) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    with (raw / "formula_herb.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def hub(tmp_path) -> TCMDataHub:
    return TCMDataHub(tmp_path)


def test_a_verified_composition_row_becomes_a_listed_relation(hub):
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [_row()])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    result = hub.check(KEY_FORMULAS)
    assert result["ok"], result["problems"]
    # the licence is unrecognisable, which is a warning, never permission
    assert any("grants nothing recognisable" in w for w in result["warnings"])
    rels = hub.relations("formula_herb", sources=[KEY_FORMULAS], subject="演示方剂（非真实）")
    assert len(rels) == 1
    row = rels[0]
    assert row["evidence"] == "listed"
    assert row["object_name"] == "演示药材（非真实）"
    assert row["reference"] == "Synthetic example only"
    assert row["license"] and "not stated" in row["license"]


def test_an_unverified_or_unidentified_row_goes_to_the_queue_not_the_relations(hub):
    raw = hub.raw_dir(KEY_FORMULAS)
    _write_formulas(raw, [
        _row(),                                                        # verified: a relation
        _row(source_row_id="demo-row-2", herb_id="", herb_name="待消歧演示药材",
             review_status="pending"),                                 # not reviewed
        _row(source_row_id="demo-row-3", herb_id="h3", review_status="verified",
             reference=""),                                            # no source
        _row(source_row_id="demo-row-4", herb_id="h4", review_status="verified"),  # ok
    ])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    result = hub.check(KEY_FORMULAS)
    assert result["ok"], result["problems"]
    assert result["unresolved"] == 2
    rels = hub.relations("formula_herb", sources=[KEY_FORMULAS])
    assert len(rels) == 2                                              # only the verified ones
    assert {r["context"] for r in rels}                                # context is JSON


def test_a_same_name_different_row_id_is_not_merged(hub):
    """Two versions of one formula name keep separate subject ids; a processed herb is
    not the crude drug."""
    raw = hub.raw_dir(KEY_FORMULAS)
    _write_formulas(raw, [
        _row(formula_id="A", source_row_id="r1", herb_id="h1", herb_name="单味药甲"),
        _row(formula_id="B", formula_version="加减方", source_row_id="r2",
             herb_id="h2", herb_name="单味药乙"),
        _row(formula_id="A", source_row_id="r3", herb_id="h1-炮", herb_name="炮制单味药甲",
             processing="炙"),
    ])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    rels = hub.relations("formula_herb", sources=[KEY_FORMULAS], contains=True,
                         subject="演示方剂")
    subjects = {r["subject_id"] for r in rels}
    objects = {r["object_id"] for r in rels}
    assert len(subjects) == 2                                          # versions A and B
    assert len(objects) == 3                                           # 炮制 kept apart from 生品


def test_the_quality_standard_export_loads_and_queries_by_monograph(hub):
    raw = hub.raw_dir(KEY_STANDARDS)
    raw.mkdir(parents=True)
    (raw / "quality_standards.tsv").write_text(
        "standard_id\therb_id\tsource_name\tedition\tmonograph\ttest_item\tmethod\t"
        "limit_value\tlimit_unit\tchemical_marker\tsource_url\tpage\tsource_row_id\t"
        "review_status\n"
        "S1\th1\tHKCMMS\tvol.1\t演示药材（非真实）\theavy metals\tICP-MS\t"
        "5\tmg/kg\t\t\thttps://example.invalid/s1\t12\trow-1\tverified\n",
        encoding="utf-8")
    hub.build(KEY_STANDARDS, log=lambda m: None)
    # the standard table carries no relations: a limit is not a herb-ingredient edge
    assert hub.check(KEY_STANDARDS)["ok"]
    rows = hub.query(KEY_STANDARDS, "quality_standards",
                     contains={"monograph": "演示药材（非真实）"})
    assert rows and rows[0]["limit_value"] == "5" and rows[0]["edition"] == "vol.1"


def test_the_reference_dna_export_keeps_the_fasta_as_a_raw_file(hub):
    raw = hub.raw_dir(KEY_DNA)
    raw.mkdir(parents=True)
    (raw / "reference_sequences.fasta").write_text(
        ">seq1 演示物种（非真实）\nATGGCCATTG\n", encoding="utf-8")
    (raw / "specimen_metadata.tsv").write_text(
        "sequence_id\therb_id\ttaxon_id\tscientific_name\tmarker\taccession\tvoucher\t"
        "source_url\treview_status\n"
        "seq1\th1\ttx1\t演示物种（非真实）\tITS2\tACC1\tV1\t"
        "https://example.invalid/d1\tverified\n",
        encoding="utf-8")
    hub.build(KEY_DNA, log=lambda m: None)
    assert hub.check(KEY_DNA)["ok"], hub.check(KEY_DNA)["problems"]
    rows = hub.query(KEY_DNA, "specimen_metadata",
                     contains={"scientific_name": "演示物种（非真实）"})
    assert rows and rows[0]["marker"] == "ITS2" and rows[0]["voucher"] == "V1"
    # the FASTA is retained, not loaded as a table
    assert (raw / "reference_sequences.fasta").exists()
    assert "reference_sequences" not in hub.tables(KEY_DNA)


def test_the_lookup_tools_report_not_loaded_and_never_claim_efficacy(tmp_path, monkeypatch):
    from bioagent.tools import tcmdb as tool
    monkeypatch.setenv("BIOAGENT_TCMDB", str(tmp_path / "empty"))
    absent = tool.hkbu_formula_lookup("演示方剂（非真实）")
    assert absent["status"] == "not_loaded" and absent["records"] == []

    _write_formulas(tmp_path / "empty" / "raw" / KEY_FORMULAS, [_row()])
    hub = TCMDataHub(tmp_path / "empty")
    hub.build(KEY_FORMULAS, log=lambda m: None)
    found = tool.hkbu_formula_lookup("演示方剂（非真实）")
    assert found["status"] == "ok" and found["claim_scope"] == "listed formula composition"
    missing = tool.hkbu_formula_lookup("不存在的方剂")
    assert missing["status"] == "no_matching_record"
    with pytest.raises(ValueError):
        tool.hkbu_formula_lookup("")
    with pytest.raises(ValueError):
        tool.hkbu_formula_lookup("演示方剂（非真实）", limit=0)


def test_a_listed_composition_is_not_an_efficacy_or_reusable_result(tmp_path):
    """The guide's fourth case. Composition evidence is ``listed`` (the source lists the
    herb), never ``known``; the row holds (outcome positive) as a composition fact, and
    its unstated licence keeps it out of a commercial query."""
    hub = TCMDataHub(tmp_path)
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [_row()])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    rels = hub.relations("formula_herb", sources=[KEY_FORMULAS], subject="演示方剂（非真实）")
    assert len(rels) == 1
    assert rels[0]["evidence"] == "listed"            # not "known": not a tested effect
    assert rels[0]["outcome"] == "positive"           # the composition relation holds
    assert not hub.relations("formula_herb", sources=[KEY_FORMULAS], commercial=True)
