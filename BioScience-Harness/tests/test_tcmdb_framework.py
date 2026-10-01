"""The relation row's added columns and the checks a dataset passes before it is used.

Effects, outcomes, contexts and licences on rows; the unresolved queue; the refusal of an
HTML page saved as data; the rebuild digest; and how consensus reads negative results and
opposite effects.
"""

from __future__ import annotations

import sqlite3

import pytest

from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.rowkit import COLUMNS, ctx, rel
from bioagent.tcmdb.spec import allows_commercial, licence_class

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------- licences
@pytest.mark.parametrize("text,cls", [
    ("CC0 1.0", "open"),
    ("CC BY 4.0", "open"),
    ("Creative Commons Attribution 4.0 International", "open"),
    ("MIT", "open"),
    ("US federal public domain", "open"),
    ("CC BY-SA 4.0", "share-alike"),
    ("ODbL 1.0 (database) / DbCL 1.0 (contents)", "share-alike"),
    ("CC BY-NC 4.0", "non-commercial"),
    ("CC BY-NC-ND 4.0", "non-commercial"),
    ("CC BY-ND 4.0", "no-derivatives"),
    ("free for academic use; commercial use by arrangement with the authors",
     "non-commercial"),
    ("CC BY 4.0 (pairs); compound ids mapped with a CC BY-NC 4.0 file", "non-commercial"),
    ("not stated", "unknown"),
    ("not stated (cite the paper)", "unknown"),
    ("", "unknown"),
])
def test_a_licence_is_classed_conservatively(text, cls):
    assert licence_class(text) == cls
    assert allows_commercial(text) == (cls in ("open", "share-alike"))


# -------------------------------------------------------------------------------- rows
def test_a_row_takes_only_known_effects_outcomes_and_context_keys():
    row = rel("drug_target", "x", "a:1", None, "symbol:ADRB2", None, "known",
              effect="activation", context=ctx(action="agonist", species="9606"))
    assert row["outcome"] == "positive"
    assert row["context"] == '{"action": "agonist", "species": "9606"}'
    with pytest.raises(ValueError):
        rel("drug_target", "x", "a:1", None, "b:2", None, "known", effect="agonism")
    with pytest.raises(ValueError):
        rel("drug_target", "x", "a:1", None, "b:2", None, "known", outcome="absent")
    with pytest.raises(ValueError):
        ctx(colour="blue")
    assert ctx(dose=None, cell="") is None


# ------------------------------------------------------------------- TM-MC (real shape)
def _xlsx(path, header, *rows):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    wb.save(path)


def _tmmc(raw):
    raw.mkdir(parents=True)
    (raw / "README.txt").write_text("TM-MC 2.0\n", encoding="utf-8")
    _xlsx(raw / "medicinal_material.xlsx",
          ("LATIN", "CHINESE", "PINYIN", "COMMON", "HANJA", "KOREAN"),
          ("Astragali Radix", "黄芪", "Huangqi", "Milkvetch Root", "黃芪", "황기"))
    _xlsx(raw / "chemical_property.xlsx", ("ID", "CID", "INCHIKEY"),
          (7, 5280343, "REFJWTPEDVJJIY-UHFFFAOYSA-N"))
    _xlsx(raw / "medicinal_compound.xlsx", ("LATIN", "ID", "COMPOUND", "PMID"),
          ("Astragali Radix", 7, "quercetin", 111),
          ("Astragali Radix", 0, "astragalin-like glycoside X", 222),
          ("Astragali Radix", 0, "unknown saponin Y", 333))
    _xlsx(raw / "chemical_protein.xlsx", ("ID", "PROTEINID", "PREFERRED_NAME", "SCORE",
                                          "SOURCE"),
          (7, "ENSP00000269305", "TP53", 0, "PubChem"),
          (7, "ENSP00000398698", "TNF", 0.9, "STITCH"))
    _xlsx(raw / "protein_disease.xlsx", ("PROTEIN_ID", "PREFERRED_NAME", "DISEASEID",
                                         "DISEASENAME", "SCORE"),
          ("ENSP00000269305", "TP53", "C0006142", "Breast Carcinoma", 0.7))
    _xlsx(raw / "prescription.xlsx", ("HANJA", "CHINESE", "PINYIN", "ENGLISH", "KOREAN",
                                      "LATIN", "TEXTBOOK", "PAGE", "DOSAGE", "UNIT",
                                      "PROCESS"),
          ("補中益氣湯", "补中益气汤", "Buzhongyiqi-tang", "", "", "Astragali Radix", "", "",
           "1.5", "g", ""))


@pytest.fixture
def tmmc(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _tmmc(h.raw_dir("tmmc2"))
    report = h.build("tmmc2", log=lambda m: None)
    return h, report


def test_unidentified_compounds_wait_in_a_queue_instead_of_sharing_an_id(tmmc):
    h, report = tmmc
    assert report["unresolved"] == 2
    queue = h.unresolved("tmmc2")
    assert {q["object_name"] for q in queue} == {"astragalin-like glycoside X",
                                                 "unknown saponin Y"}
    assert {q["reference"] for q in queue} == {"pmid:222", "pmid:333"}
    objects = {r["object_id"] for r in h.relations("herb_ingredient")}
    assert objects == {"pubchem:5280343"}                  # no "tmmc:0" compound


def test_a_zero_score_that_means_not_applicable_is_not_kept_as_zero(tmmc):
    h, _ = tmmc
    rows = {r["object_name"]: r for r in h.relations("ingredient_target")}
    assert rows["TP53"]["score"] is None and rows["TP53"]["note"] == "via PubChem"
    assert rows["TNF"]["score"] == "0.9"


def test_each_row_carries_the_licence_of_the_file_it_came_from(tmmc):
    from bioagent.tcmdb.datasets import dataset
    h, _ = tmmc
    spec = dataset("tmmc2")
    for r in h.relations(limit=1000):
        assert r["license"] == spec.licence_of(r["kind"])
    commercial = {r["kind"] for r in h.relations(commercial=True, limit=1000)}
    assert commercial == {k for k in spec.relations if allows_commercial(spec.licence_of(k))}


def test_rebuilding_the_same_files_gives_the_same_relations(tmmc):
    h, first = tmmc
    second = h.build("tmmc2", log=lambda m: None)
    assert first["relations_digest"] == second["relations_digest"]
    with sqlite3.connect(h.db_path("tmmc2")) as conn:
        assert conn.execute("SELECT value FROM _tcmdb_build WHERE key='relations_digest'") \
            .fetchone()[0] == second["relations_digest"]


# --------------------------------------------------------------------- fetch refusals
def test_an_html_page_is_not_kept_as_a_data_file(tmp_path, monkeypatch):
    from bioagent.acquisition import downloader
    from bioagent.tcmdb import hub as hubmod

    class FakeResult:
        bytes, checksum, from_cache = 120, "sha256:x", False

    def fake_fetch(self, url, name, **kw):
        (self.root / name).write_text("<!DOCTYPE html><html><body>Please log in</body></html>")
        return FakeResult()

    monkeypatch.setattr(downloader.Downloader, "fetch", fake_fetch)
    h = TCMDataHub(tmp_path)
    out = h.fetch("ttd", log=lambda m: None)
    assert out and not any(r["ok"] for r in out)
    assert "HTML page" in out[0]["error"]
    assert not any(h.raw_dir("ttd").glob("*.txt"))
    assert not hubmod._looks_like_html(tmp_path / "missing")


# --------------------------------------------------------------- outcomes in consensus
def _store(hub, key, rows):
    path = hub.db_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE relations ({', '.join(c + ' TEXT' for c in COLUMNS)})")
    conn.executemany(f"INSERT INTO relations VALUES ({', '.join('?' * len(COLUMNS))})",
                     [tuple(r.get(c) for c in COLUMNS) for r in rows])
    conn.commit()
    conn.close()


def _ct(source, obj, outcome="positive", effect=None, evidence="known", reference=None):
    return rel("drug_target", source, "chembl:CHEMBL25", "aspirin", obj, None, evidence,
               outcome=outcome, effect=effect, reference=reference)


def test_negative_results_are_reported_and_never_count_as_support(tmp_path):
    h = TCMDataHub(tmp_path)
    # store keys must be dataset keys; what matters is which one records negatives
    _store(h, "dbpth", [_ct("screen", "symbol:PTGS1", reference="pmid:1"),
                        _ct("screen", "symbol:PTGS2", "negative"),
                        _ct("screen", "symbol:ESR1", "inconclusive")])
    _store(h, "ttd", [_ct("curated", "symbol:PTGS2", effect="inhibition",
                          reference="pmid:2"),
                      _ct("curated", "symbol:HTR2A", reference="pmid:3")])
    items = {x["object"]: x for x in h.consensus("drug_target",
                                                  subject="chembl:CHEMBL25")["items"]}
    assert items["symbol:PTGS2"]["support"] == "documented"
    assert items["symbol:PTGS2"]["contradicted_by"] == ["screen"]
    assert items["symbol:PTGS2"]["outcomes"] == {"positive": ["curated"],
                                                 "negative": ["screen"]}
    assert items["symbol:ESR1"]["support"] == "inconclusive"
    # the screen has no row for HTR2A: not tested, which is not silence
    assert items["symbol:HTR2A"]["not_tested_in"] == ["screen"]
    assert items["symbol:HTR2A"]["silent_sources"] == []
    # the curated source covers aspirin but has no row for PTGS1: that is silence
    assert items["symbol:PTGS1"]["silent_sources"] == ["curated"]


def test_a_pair_only_ever_tested_negative_is_classed_so(tmp_path):
    h = TCMDataHub(tmp_path)
    _store(h, "dbpth", [_ct("screen", "symbol:PTGS2", "negative")])
    item = h.consensus("drug_target", subject="chembl:CHEMBL25")["items"][0]
    assert item["support"] == "tested_negative" and item["contradicted_by"] == []


def test_opposite_effects_are_flagged_not_resolved(tmp_path):
    h = TCMDataHub(tmp_path)
    _store(h, "ttd", [_ct("a", "symbol:ADRB2", effect="activation", reference="pmid:1")])
    _store(h, "dbpth", [_ct("b", "symbol:ADRB2", effect="inhibition", reference="pmid:2")])
    item = h.consensus("drug_target", subject="chembl:CHEMBL25")["items"][0]
    assert item["effects"] == {"activation": ["a"], "inhibition": ["b"]}
    assert item["effect_conflict"] is True
    assert item["support"] == "independently_replicated"


# ------------------------------------------------------------------------ added sources
def test_an_added_kind_cannot_redefine_an_existing_one():
    from bioagent.tcmdb.rowkit import RELATION_KINDS, register_kinds
    register_kinds({"herb_ingredient": ("herb", "ingredient")})        # same: fine
    with pytest.raises(ValueError):
        register_kinds({"herb_ingredient": ("herb", "compound")})
    assert RELATION_KINDS["herb_ingredient"] == ("herb", "ingredient")


def test_every_added_dataset_and_connector_is_registered_once():
    from bioagent.providers.public_apis import BY_KEY, SOURCES
    from bioagent.providers.supplement import PENDING_SUPPLEMENT_SOURCES, SUPPLEMENT_SOURCES
    from bioagent.tcmdb.datasets import DATASETS
    from bioagent.tcmdb.extra import EXTRA_DATASETS, EXTRA_EXTRACTORS
    from bioagent.tcmdb.relations import EXTRACTORS
    assert len({d.key for d in DATASETS}) == len(DATASETS)
    assert {d.key for d in EXTRA_DATASETS} <= {d.key for d in DATASETS}
    assert set(EXTRA_EXTRACTORS) <= set(EXTRACTORS)
    assert all(s.key in BY_KEY for s in SUPPLEMENT_SOURCES)
    assert not {s.key for s in PENDING_SUPPLEMENT_SOURCES} & set(BY_KEY)
    assert len(BY_KEY) == len(SOURCES)
    for d in EXTRA_DATASETS:
        assert d.commercial_use in ("allowed", "forbidden", "unknown"), d.key
        if d.relations:
            assert d.key in EXTRACTORS, f"{d.key} declares relations but has no extractor"


def test_the_acceptance_check_passes_a_clean_dataset_and_names_what_fails(tmmc):
    h, _ = tmmc
    result = h.check("tmmc2")
    assert result["ok"], result["problems"]
    assert result["unresolved"] == 2
    assert set(result["licences"]) == {"herb_ingredient", "ingredient_target",
                                       "target_disease", "formula_herb"}
    (h.raw_dir("tmmc2") / "README.txt").write_text("<html><body>Sign in</body></html>")
    result = h.check("tmmc2", rebuild=False)
    assert not result["ok"] and any("HTML page" in p for p in result["problems"])


def test_the_check_command_reports_and_fails_on_a_problem(tmmc, capsys):
    from bioagent.cli import main
    h, _ = tmmc
    assert main(["tcmdb", "--root", str(h.root), "check", "tmmc2", "--no-rebuild"]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_a_dataset_can_declare_id_mappings_for_the_crosswalk(tmp_path, monkeypatch):
    from dataclasses import replace

    from bioagent.tcmdb import datasets as ds
    from bioagent.tcmdb.consensus import Crosswalk
    spec = replace(ds.dataset("ttd"), crosswalk={
        "compound": "SELECT 'chebi:' || id, 'inchikey:' || ik FROM xw",
        "gene": "SELECT 'uniprot:' || acc, 'symbol:' || sym FROM xw"})
    monkeypatch.setattr(ds, "DATASETS", tuple(spec if d.key == "ttd" else d
                                              for d in ds.DATASETS))
    h = TCMDataHub(tmp_path)
    path = h.db_path("ttd")
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE xw (id TEXT, ik TEXT, acc TEXT, sym TEXT)")
        conn.execute("INSERT INTO xw VALUES ('28794', 'REFJWTPEDVJJIY-UHFFFAOYSA-N', "
                     "'P01375', 'tnf')")
        conn.execute("INSERT INTO xw VALUES ('1', 'not-a-key', 'P0', '')")
    cw = Crosswalk(h)
    assert cw.canon("compound", "chebi:28794") == "inchikey:REFJWTPEDVJJIY-UHFFFAOYSA-N"
    assert cw.canon("drug", "chebi:1") == "chebi:1"                 # not an InChIKey
    assert cw.canon("protein", "uniprot:P01375") == "symbol:TNF"
