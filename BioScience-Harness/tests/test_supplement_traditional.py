"""The manual Hong Kong sources: HKBU formulas, HKCMMS standards, reference DNA.

Every row here is made up; the files use the reviewed TSV columns the adapter declares.
Nothing touches the network, and no real HKBU, HKCMMS or reference-sequence data is
involved. The guide's four admission cases are pinned: a manually verified correct record,
a same-name ambiguity that must not be silently merged, a record missing its source, and a
claim that must not pass the release gate from composition evidence alone.
"""

from __future__ import annotations

import csv
import json

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.extra.traditional import (FORMULA_HERB_COLUMNS, KEY_DNA, KEY_FORMULAS,
                                              KEY_STANDARDS, material_name)
from bioagent.tcmdb.store import StoreError

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


_STANDARD_HEADER = ("standard_id\therb_id\tsource_name\tedition\tmonograph\ttest_item\t"
                    "method\tlimit_value\tlimit_unit\tchemical_marker\tsource_url\tpage\t"
                    "source_row_id\treview_status\n")
#: 14 values for the 14 columns; chemical_marker is empty.
_STANDARD_ROW = ("S1\th1\tHKCMMS\tvol.1\t演示药材（非真实）\theavy metals\tICP-MS\t5\tmg/kg"
                 "\t\thttps://example.invalid/s1\t12\trow-1\tverified\n")


def _write_standards(hub: TCMDataHub, *rows: str) -> None:
    raw = hub.raw_dir(KEY_STANDARDS)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "quality_standards.tsv").write_text(_STANDARD_HEADER + "".join(rows),
                                               encoding="utf-8")


def test_the_quality_standard_export_loads_and_queries_by_monograph(hub):
    _write_standards(hub, _STANDARD_ROW)
    hub.build(KEY_STANDARDS, log=lambda m: None)
    # the standard table carries no relations: a limit is not a herb-ingredient edge
    assert hub.check(KEY_STANDARDS)["ok"]
    rows = hub.query(KEY_STANDARDS, "quality_standards",
                     contains={"monograph": "演示药材（非真实）"})
    # every field where the file put it: the review found this sample one tab long, its
    # source, page, row id and review status each shifted a column, and only two fields
    # were checked
    assert rows == [{"standard_id": "S1", "herb_id": "h1", "source_name": "HKCMMS",
                     "edition": "vol.1", "monograph": "演示药材（非真实）",
                     "test_item": "heavy metals", "method": "ICP-MS", "limit_value": "5",
                     "limit_unit": "mg/kg", "chemical_marker": None,
                     "source_url": "https://example.invalid/s1", "page": "12",
                     "source_row_id": "row-1", "review_status": "verified"}]


def test_a_row_with_a_stray_tab_is_refused_and_the_previous_store_is_kept(hub):
    _write_standards(hub, _STANDARD_ROW)
    hub.build(KEY_STANDARDS, log=lambda m: None)
    shifted = _STANDARD_ROW.replace("mg/kg\t", "mg/kg\t\t", 1)      # 15 values
    _write_standards(hub, shifted)
    with pytest.raises(StoreError, match=r"line 2: 15 fields where the header has 14"):
        hub.build(KEY_STANDARDS, log=lambda m: None)
    kept = hub.query(KEY_STANDARDS, "quality_standards")
    assert [r["source_url"] for r in kept] == ["https://example.invalid/s1"]


def test_a_tab_or_line_break_inside_a_quoted_value_stays_in_its_field(hub):
    _write_formulas(hub.raw_dir(KEY_FORMULAS),
                    [_row(reference="Book A\tvol. 2", locator="p. 3\nline 4")])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    row = hub.query(KEY_FORMULAS, "formula_herb")[0]
    assert row["reference"] == "Book A\tvol. 2" and row["locator"] == "p. 3\nline 4"
    assert row["source_row_id"] == "demo-row-1" and row["review_status"] == "verified"


def test_a_file_missing_a_column_is_refused_and_the_relations_survive(hub):
    """A failed import used to replace the good store first, leaving 0 relations of 1."""
    raw = hub.raw_dir(KEY_FORMULAS)
    _write_formulas(raw, [_row()])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    fields = [f for f in _FIELDS if f != "herb_id"]
    with (raw / "formula_herb.tsv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerow(_row())
    with pytest.raises(StoreError, match=r"missing \['herb_id'\]"):
        hub.build(KEY_FORMULAS, log=lambda m: None)
    assert len(hub.relations("formula_herb", sources=[KEY_FORMULAS])) == 1
    assert not hub.db_path(KEY_FORMULAS).with_name(
        hub.db_path(KEY_FORMULAS).name + ".staging").exists()


def test_an_extractor_that_fails_leaves_the_previous_store(hub, monkeypatch):
    from bioagent.tcmdb import relations
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [_row()])
    hub.build(KEY_FORMULAS, log=lambda m: None)

    def broken(conn):
        raise RuntimeError("extractor bug")
        yield  # pragma: no cover

    monkeypatch.setitem(relations.EXTRACTORS, KEY_FORMULAS, broken)
    with pytest.raises(RuntimeError, match="extractor bug"):
        hub.build(KEY_FORMULAS, log=lambda m: None)
    assert len(hub.relations("formula_herb", sources=[KEY_FORMULAS])) == 1


@pytest.mark.parametrize("header, why", [
    ("standard_id\therb_id\n", "missing"),
    (_STANDARD_HEADER.replace("page", "page_no"), "not in the template"),
    (_STANDARD_HEADER.replace("\n", "\tpage\n"), "repeated"),
])
def test_the_header_must_be_the_template_s(hub, header, why):
    raw = hub.raw_dir(KEY_STANDARDS)
    raw.mkdir(parents=True)
    (raw / "quality_standards.tsv").write_text(header, encoding="utf-8")
    with pytest.raises(StoreError, match=why):
        hub.build(KEY_STANDARDS, log=lambda m: None)


def test_a_file_that_is_not_utf8_is_refused(hub):
    raw = hub.raw_dir(KEY_STANDARDS)
    raw.mkdir(parents=True)
    (raw / "quality_standards.tsv").write_bytes(
        (_STANDARD_HEADER + _STANDARD_ROW).encode("gb18030"))
    with pytest.raises(StoreError, match="not valid UTF-8"):
        hub.build(KEY_STANDARDS, log=lambda m: None)


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


# ------------------------------------------------------------------ the lookup tools
def _dna_files(hub: TCMDataHub, *, fasta: str | None = ">seq1 演示物种\nATGGCCATTG\n",
               status: str = "verified", sequence_id: str = "seq1") -> None:
    raw = hub.raw_dir(KEY_DNA)
    raw.mkdir(parents=True, exist_ok=True)
    if fasta is not None:
        (raw / "reference_sequences.fasta").write_text(fasta, encoding="utf-8")
    (raw / "specimen_metadata.tsv").write_text(
        "sequence_id\therb_id\ttaxon_id\tscientific_name\tmarker\taccession\tvoucher\t"
        "source_url\treview_status\n"
        f"{sequence_id}\th1\ttx1\t演示物种（非真实）\tITS2\tACC1\tV1\t"
        f"https://example.invalid/d1\t{status}\n", encoding="utf-8")


@pytest.fixture
def tools(tmp_path, monkeypatch):
    from bioagent.tools import tcmdb as tool
    monkeypatch.setenv("BIOAGENT_TCMDB", str(tmp_path / "hub"))
    monkeypatch.delenv("BIOAGENT_DATA_USE", raising=False)
    return tool, TCMDataHub(tmp_path / "hub")


def test_every_record_carries_its_dataset_and_data_licence(tools):
    tool, hub = tools
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [_row()])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    _write_standards(hub, _STANDARD_ROW)
    hub.build(KEY_STANDARDS, log=lambda m: None)
    _dna_files(hub)
    hub.build(KEY_DNA, log=lambda m: None)
    answers = (tool.hkbu_formula_lookup("演示方剂（非真实）"),
               tool.hkcmms_standard_lookup("演示药材（非真实）"),
               tool.hk_cmm_dna_lookup("演示物种（非真实）"))
    for answer, key in zip(answers, (KEY_FORMULAS, KEY_STANDARDS, KEY_DNA)):
        assert answer["status"] == "ok", answer
        assert answer["dataset_licence"]["class"] == "unknown"
        for record in answer["records"]:
            assert record["source_dataset"] == key
            assert record["license"] and record["licence_class"] == "unknown"
            assert record["commercial_use"] == "unknown"
    assert answers[1]["records"][0]["page"] == "12"


def test_the_lookups_tell_apart_absent_incomplete_unmatched_and_withheld(tools):
    tool, hub = tools
    assert tool.hk_cmm_dna_lookup("演示物种")["status"] == "not_loaded"
    assert str(hub.db_path(KEY_DNA)) == tool.hk_cmm_dna_lookup("演示物种")["store"]
    # the FASTA alone: the store exists, the specimen table does not (this raised)
    raw = hub.raw_dir(KEY_DNA)
    raw.mkdir(parents=True)
    (raw / "reference_sequences.fasta").write_text(">seq1\nATGC\n", encoding="utf-8")
    hub.build(KEY_DNA, log=lambda m: None)
    answer = tool.hk_cmm_dna_lookup("演示物种")
    assert answer["status"] == "incomplete_dataset" and "specimen_metadata" in answer["reason"]
    # the metadata without the sequences it describes is incomplete too
    (raw / "reference_sequences.fasta").unlink()
    _dna_files(hub, fasta=None)
    hub.build(KEY_DNA, log=lambda m: None)
    assert tool.hk_cmm_dna_lookup("演示物种")["status"] == "incomplete_dataset"
    _dna_files(hub, status="pending")
    hub.build(KEY_DNA, log=lambda m: None)
    assert tool.hk_cmm_dna_lookup("没有这个物种")["status"] == "no_matching_record"
    pending = tool.hk_cmm_dna_lookup("演示物种")
    assert pending["status"] == "withheld" and pending["records"] == []
    assert pending["withheld"] == {"not verified by a person (pass include_pending=True to "
                                   "see it)": 1}
    shown = tool.hk_cmm_dna_lookup("演示物种", include_pending=True)
    assert shown["status"] == "ok" and shown["records"][0]["review_status"] == "pending"


def test_an_unreviewed_formula_row_is_counted_not_returned(tools):
    tool, hub = tools
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [
        _row(), _row(source_row_id="demo-row-2", herb_id="h2", review_status="pending")])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    answer = tool.hkbu_formula_lookup("演示方剂（非真实）")
    assert answer["status"] == "ok" and len(answer["records"]) == 1
    assert answer["unreviewed_rows"] == 1


def test_commercial_data_use_withholds_records_whose_licence_is_unknown(tools, monkeypatch):
    tool, hub = tools
    _write_standards(hub, _STANDARD_ROW)
    hub.build(KEY_STANDARDS, log=lambda m: None)
    assert tool.hkcmms_standard_lookup("演示药材")["data_use"] == "academic"
    asked = tool.hkcmms_standard_lookup("演示药材", commercial=True)
    assert asked["status"] == "withheld" and asked["data_use"] == "commercial"
    assert asked["withheld"] == {"licence does not allow commercial use": 1}
    # the operator's floor holds whatever the call asks for, and an unknown value is the
    # stricter use
    for floor in ("commercial", "COMMERCIAL", "sometimes"):
        monkeypatch.setenv("BIOAGENT_DATA_USE", floor)
        answer = tool.hkcmms_standard_lookup("演示药材", commercial=False)
        assert answer["status"] == "withheld" and answer["data_use"] == "commercial"
    monkeypatch.setenv("BIOAGENT_DATA_USE", "academic")
    assert tool.hkcmms_standard_lookup("演示药材")["status"] == "ok"
    with pytest.raises(ValueError, match="true or false"):
        tool.hkcmms_standard_lookup("演示药材", commercial="yes")


# ------------------------------------------------------------------ acceptance checks
def test_check_warns_on_a_query_only_dataset_s_unknown_licence_and_pending_rows(hub):
    _write_standards(hub, _STANDARD_ROW.replace("verified", "pending"),
                     _STANDARD_ROW.replace("row-1", "row-2").replace("verified", "verifed"))
    hub.build(KEY_STANDARDS, log=lambda m: None)
    result = hub.check(KEY_STANDARDS)
    assert result["ok"], result["problems"]
    assert result["dataset_licence"]["class"] == "unknown"
    text = " ".join(result["warnings"])
    assert "treat its tables as not licensed for reuse" in text
    assert "2 of 2 rows are not verified" in text and "['verifed']" in text


def test_check_matches_the_fasta_against_the_specimen_metadata(hub):
    _dna_files(hub, sequence_id="seq9")
    hub.build(KEY_DNA, log=lambda m: None)
    result = hub.check(KEY_DNA)
    assert not result["ok"]
    assert any("no FASTA record has" in p and "seq9" in p for p in result["problems"])
    assert any("have no specimen_metadata row" in w for w in result["warnings"])
    _dna_files(hub, fasta=">seq1 a\nATGC\n>seq1 b\n\n")
    hub.build(KEY_DNA, log=lambda m: None)
    problems = " ".join(hub.check(KEY_DNA)["problems"])
    assert "repeats the record ids ['seq1']" in problems and "without a sequence" in problems
    _dna_files(hub)
    hub.build(KEY_DNA, log=lambda m: None)
    assert hub.check(KEY_DNA)["ok"]


# ------------------------------------------------------------------ processed herbs
@pytest.mark.parametrize("name, processing, expected", [
    ("黄芪", "炙", "炙黄芪"), ("黄芪", "生", "黄芪"), ("黄芪", "", "黄芪"),
    ("炙甘草", "炙", "炙甘草"), ("炙黄芪", "蜜炙", "炙黄芪"), ("附子", "炮", "炮附子")])
def test_the_listed_material_carries_its_processing(name, processing, expected):
    assert material_name(name, processing) == expected


def test_a_crude_and_a_processed_herb_stay_apart_through_consensus(hub):
    """The processing used to live only in a JSON note, which identity never read: 黄芪
    (生) and 黄芪 (炙) were two herb ids in the store and one herb in a consensus."""
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [
        _row(source_row_id="r1", herb_id="h1-raw", herb_name="黄芪", processing="生"),
        _row(source_row_id="r2", herb_id="h1-processed", herb_name="黄芪", processing="炙")])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    apart = hub.consensus("formula_herb", subject="演示方剂（非真实）")
    assert sorted(x["object"] for x in apart["items"]) == ["materia:huangqi",
                                                           "materia:huangqi#炙黄芪"]
    merged = hub.consensus("formula_herb", subject="演示方剂（非真实）", merge_processed=True)
    assert [x["object"] for x in merged["items"]] == ["materia:huangqi"]
    note = json.loads(next(r for r in hub.relations("formula_herb", sources=[KEY_FORMULAS])
                           if r["object_id"].endswith("h1-processed"))["note"])
    assert note["herb_name"] == "黄芪" and note["processing"] == "炙"


def test_blast_fried_aconite_is_not_the_crude_root():
    from bioagent.tcmdb.consensus import herb_key
    assert herb_key("炮附子", "x:1") != herb_key("附子", "x:1")
    assert herb_key("炮附子", "x:1", merge_processed=True) == herb_key("附子", "x:1")


# ------------------------------------------------------------------ governance
def test_the_tools_declare_what_they_read_and_whose_licence_their_data_is():
    from bioagent.tools import NativeToolProvider
    manifests = {m.name: m for m in NativeToolProvider().discover()}
    for name, key in (("hkbu_formula_lookup", KEY_FORMULAS),
                      ("hkcmms_standard_lookup", KEY_STANDARDS),
                      ("hk_cmm_dna_lookup", KEY_DNA)):
        m = manifests[name]
        assert m.permissions.filesystem_read == ("${tcmdb}",)
        assert m.license.spdx == "MIT" and m.license.data.startswith(f"{key}: ")
        assert "class unknown" in m.license.data
    assert manifests["gc_content"].license.data == ""             # reads nothing


def test_a_declared_tcmdb_read_is_ruled_on_where_it_resolves(tmp_path, monkeypatch):
    from bioagent.policy import PROFILES
    lake = tmp_path / "lake"
    monkeypatch.setenv("BIOAGENT_DATA_LAKE", str(lake))
    monkeypatch.delenv("BIOAGENT_TCMDB", raising=False)
    profile = PROFILES["offline-analysis"]
    assert profile.check_filesystem(["${tcmdb}"], mode="read").allowed
    monkeypatch.setenv("BIOAGENT_TCMDB", str(lake / "my-hub"))
    assert profile.check_filesystem(["${tcmdb}"], mode="read").allowed
    monkeypatch.setenv("BIOAGENT_TCMDB", str(tmp_path / "elsewhere"))
    ruling = profile.check_filesystem(["${tcmdb}"], mode="read")
    assert not ruling.allowed and str(tmp_path / "elsewhere") in ruling.reason


def test_the_bridge_reports_the_data_licence_apart_from_the_code_s():
    pytest.importorskip("psh")
    from bioagent.psh import bridge_manifest
    from bioagent.tools import NativeToolProvider
    m = next(x for x in NativeToolProvider().discover() if x.name == "hkcmms_standard_lookup")
    psh_manifest = bridge_manifest(m)
    assert psh_manifest.requires_filesystem
    assert psh_manifest.provenance["data_license"].startswith(f"{KEY_STANDARDS}: ")
    assert psh_manifest.provenance["code_license"] == "MIT"


def test_an_isolated_lookup_reads_the_hub_the_operator_configured(tmp_path, monkeypatch):
    """Through the real broker, in a child process: the child used to see only the data
    lake, so a hub at $BIOAGENT_TCMDB answered directly and read as not loaded there."""
    pytest.importorskip("psh")
    from psh.config import PSHConfig
    from psh.contracts import Autonomy, ContractViolation, RiskTier
    from psh.kernel import TrustedKernel
    from psh.labels import Destination, Sensitivity
    from psh.policy import PolicySnapshot

    from bioagent.psh import BioScienceBridge, default_runtime
    from bioagent.tools import NativeToolProvider

    lake = tmp_path / "lake"
    monkeypatch.setenv("BIOAGENT_DATA_LAKE", str(lake))
    hub = TCMDataHub(lake / "my-hub")
    _write_formulas(hub.raw_dir(KEY_FORMULAS), [_row()])
    hub.build(KEY_FORMULAS, log=lambda m: None)
    manifest = next(m for m in NativeToolProvider().discover()
                    if m.name == "hkbu_formula_lookup")
    policy = PolicySnapshot(profile_id="t", max_data_label=Sensitivity.PHI,
                            allowed_destinations=(Destination.LOCAL_COMPUTE,
                                                  Destination.USER_OUTPUT),
                            autonomy=Autonomy.ACT, risk_ceiling=RiskTier.R3_CLINICAL,
                            require_claim_support=False)

    def call(state: str):
        kernel = TrustedKernel(PSHConfig(state_dir=tmp_path / state).ensure_dirs(),
                               policy=policy)
        try:
            runtime = default_runtime(catalogue=False, public_apis=False, native_tools=False,
                                      skills=False, extra_manifests=(manifest,),
                                      data_lake=lake)
            bridge = BioScienceBridge(kernel, runtime, isolate=True)
            bridge.admit(manifest)
            result = kernel.broker.call_tool(bridge.component(manifest.id),
                                             {"formula": "演示方剂（非真实）"},
                                             kernel.policy.envelope())
            return result.value if isinstance(result.value, dict) else json.loads(result.value)
        finally:
            kernel.close()

    monkeypatch.setenv("BIOAGENT_TCMDB", str(lake / "my-hub"))
    answer = call("k1")
    assert answer["status"] == "ok" and answer["records"][0]["licence_class"] == "unknown"
    # a hub outside every root the profile grants is refused, not reported missing
    outside = TCMDataHub(tmp_path / "outside")
    _write_formulas(outside.raw_dir(KEY_FORMULAS), [_row()])
    outside.build(KEY_FORMULAS, log=lambda m: None)
    monkeypatch.setenv("BIOAGENT_TCMDB", str(tmp_path / "outside"))
    with pytest.raises(ContractViolation, match="outside the roots"):
        call("k2")


# ------------------------------------------------------------------ templates and verify
def test_a_template_is_the_header_and_nothing_else(tmp_path):
    from bioagent.tcmdb.extra.traditional import SCHEMAS
    from bioagent.tcmdb.manual import MANUAL_TEMPLATES, templates
    for key, files in MANUAL_TEMPLATES.items():
        written = templates(key, tmp_path / key)
        assert [p.name for p in written] == [name for name, _ in files]
        for path, (_, table) in zip(written, files):
            lines = path.read_text(encoding="utf-8").splitlines()
            assert lines == ["\t".join(SCHEMAS[table])]
    with pytest.raises(FileExistsError):
        templates(KEY_FORMULAS, tmp_path / KEY_FORMULAS)


def test_the_demo_set_is_fictional_pending_and_makes_no_relation(tmp_path):
    from bioagent.tcmdb.manual import templates
    hub = TCMDataHub(tmp_path / "hub")
    for key in (KEY_FORMULAS, KEY_STANDARDS, KEY_DNA):
        templates(key, hub.raw_dir(key), demo=True)
        hub.build(key, log=lambda m: None)
        assert hub.check(key)["ok"], hub.check(key)["problems"]
    assert hub.relations("formula_herb", sources=[KEY_FORMULAS]) == []
    assert len(hub.unresolved(KEY_FORMULAS)) == 2
    names = [r["formula_name"] for r in hub.query(KEY_FORMULAS, "formula_herb")]
    assert names and all("虚构" in n for n in names)
    statuses = {r["review_status"] for k, t in ((KEY_FORMULAS, "formula_herb"),
                                               (KEY_STANDARDS, "quality_standards"),
                                               (KEY_DNA, "specimen_metadata"))
                for r in hub.query(k, t)}
    assert statuses == {"pending"}


def test_verify_tells_nothing_checked_from_all_passed_and_from_a_failure(tmp_path, capsys):
    from bioagent.cli import main
    from bioagent.tcmdb.manual import EXIT_NONE_CHECKED, templates
    root = tmp_path / "hub"
    assert main(["tcmdb", "--root", str(root), "verify"]) == EXIT_NONE_CHECKED
    assert "NO DATASETS CHECKED" in capsys.readouterr().out
    hub = TCMDataHub(root)
    templates(KEY_STANDARDS, hub.raw_dir(KEY_STANDARDS), demo=True)
    assert main(["tcmdb", "--root", str(root), "verify"]) == 0
    out = capsys.readouterr().out
    assert "== hkcmms_manual: PASS" in out and "hkcmms_standard_lookup" in out
    assert "ALL CHECKED DATASETS PASSED (1/1" in out
    _dna_files(hub, sequence_id="seq9")                    # metadata the FASTA lacks
    assert main(["tcmdb", "--root", str(root), "verify"]) == 1
    assert "== hk_cmm_dna_manual: FAIL" in capsys.readouterr().out
    assert main(["tcmdb", "--root", str(root), "verify", "herb2"]) == 2
