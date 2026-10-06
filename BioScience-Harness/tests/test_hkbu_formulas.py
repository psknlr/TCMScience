"""HKBU formula compositions in the research layer, on synthetic data.

The hub has read the reviewed HKBU export (catalogue 134) since the first phase; nothing
could study it. ``sources.hkbu`` turns each formula that can be studied whole into a
formula version of the herb layer, and the network-pharmacology skill runs on it like on
any other formula. Every row and name below is invented for the test; none is a record
of the HKBU database.

Two gaps in the skill runner were found on the way and are pinned here too: a commercial
run studied a formula whose composition record has no stated licence, because the herb
layer takes part in every run and the purpose check only looked at the skill's sources;
and a formula edited after the snapshot was built ran, with the provenance naming a
composition the analysis never read.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from bioagent.analysis.skill_runner import SkillRunRefused, run_skill
from bioagent.sources import build_snapshot
from bioagent.sources.build import build_gold
from bioagent.sources.formulas import TABLE_LICENSE
from bioagent.sources.herbs import (CITATION, GEGEN_QINLIAN, KEY as HERB_KEY,
                                    LICENSE as HERB_LICENSE, FormulaVersion, herb_rows)
from bioagent.sources.hkbu import CITATION as HKBU_CITATION, KEY, LICENSE, load_hkbu_formulas
from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.materia import _NAME_INDEX, resolve_name, to_simplified
from bioagent.sources.schema import validate_edge, validate_node
from bioagent.tcmdb.extra.traditional import FORMULA_HERB_COLUMNS
from bioagent.tcmdb.store import StoreError
from test_network_pharmacology import FAST, SKILL_DIR, _world

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _row(fid, herb, rid, *, dose="", unit="g", processing="", status="verified",
         name="演示葛根芩连方（虚构）", version="", reference="《演示方书》卷一（虚构）",
         herb_id=None):
    return {"formula_id": fid, "formula_name": name, "formula_version": version,
            "herb_id": f"h-{rid}" if herb_id is None else herb_id, "herb_name": herb,
            "dose": dose, "dose_unit": unit if dose else "", "processing": processing,
            "preparation": "", "reference": reference, "source_url": "", "locator": "",
            "source_row_id": rid, "review_status": status}


#: The four herbs of the gold formula, written the way a Hong Kong source writes them.
WHOLE = [_row("F1", "葛根", "r1", dose="24"), _row("F1", "黃芩", "r2", dose="9"),
         _row("F1", "黃連", "r3", dose="9"), _row("F1", "甘草", "r4", dose="6", processing="炙")]


def _export(tmp_path: Path, rows) -> Path:
    path = tmp_path / "raw" / KEY / "formula_herb.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FORMULA_HERB_COLUMNS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return path


# ======================================================================= the export

def test_only_a_formula_reviewed_and_resolved_whole_is_exported(tmp_path):
    rows = WHOLE + [
        _row("F2", "葛根", "r5", status="pending"),
        _row("F2", "黄芩", "r6"),
        _row("F3", "葛根", "r7", status="rejected"),
        _row("F4", "演示药（虚构）", "r8"),
        _row("F5", "生甘草", "r9", processing="生"),
        _row("F5", "炙甘草", "r10"),
        _row("F 6", "葛根", "r11"),
        _row("F7", "葛根", "r12", reference="《甲书》"),
        _row("F7", "黄芩", "r13", reference="《乙书》"),
        _row("F8", "葛根", "r14"),
        _row("F8", "黄芩", "r14"),
        _row("F9", "葛根", "r15", herb_id=""),
        _row("", "葛根", "r16"),
    ]
    export = load_hkbu_formulas(_export(tmp_path, rows))
    assert export.stats() == {"rows": 17, "formulas": 10, "exported": 1, "excluded": 9}
    (f1,) = export.versions
    assert f1.id == "hkbu:formula.F1" and f1.chinese == "演示葛根芩连方（虚构）"
    assert f1.source == "《演示方书》卷一（虚构）"
    assert f1.components == (("tcm:herb.gegen", "", "24g", ""),
                             ("tcm:herb.huangqin", "", "9g", ""),
                             ("tcm:herb.huanglian", "", "9g", ""),
                             ("tcm:herb.gancao", "", "6g", "炙"))
    assert f1.record_ids == tuple(f"{KEY}:r{i}" for i in range(1, 5))
    assert f1.written == ("", "黃芩", "黃連", "")
    assert f1.license == LICENSE and f1.primary_source == KEY
    why = {fid: " | ".join(reasons) for fid, reasons in export.excluded.items()}
    assert "r5: review_status pending, not verified" in why["F2"]
    assert "rejected, not verified" in why["F3"]
    assert "r8: herb 演示药（虚构） does not resolve" in why["F4"]
    assert "tcm:herb.gancao is listed again (also r9)" in why["F5"]
    assert "cannot be part of an identifier" in why["F 6"]
    assert "disagree on reference" in why["F7"]
    assert "r14: source_row_id is used by more than one row" in why["F8"]
    assert "r15: no herb_id" in why["F9"]
    assert why[""] == "r16: no formula_id"
    assert export.why_excluded("hkbu:formula.F2") == export.excluded["F2"]


def test_a_crude_processing_is_the_crude_drug_and_a_version_names_its_source(tmp_path):
    rows = [_row("V1", "葛根", "a1", processing="生品", version="原方"),
            _row("V1", "炙甘草", "a2", version="原方")]
    (f,) = load_hkbu_formulas(_export(tmp_path, rows)).versions
    assert [c[3] for c in f.components] == ["", ""]
    assert f.written == ("", "炙甘草")          # the processing lives in the written name
    assert f.source == "《演示方书》卷一（虚构）·原方"


def test_a_broken_export_is_refused_with_the_hubs_message(tmp_path):
    path = _export(tmp_path, WHOLE)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace("\t", "\t\t", 1)        # one stray tab
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(StoreError, match="line 3: 15 fields where the header has 14"):
        load_hkbu_formulas(path)


def test_traditional_characters_resolve_and_never_to_another_drug():
    for written, drug in (("黃芩", "huangqin"), ("大棗", "dazao"), ("乾薑", "ganjiang"),
                          ("鷄內金", "jineijin"), ("殭蠶", "jiangcan"), ("白朮", "baizhu")):
        assert resolve_name(written) is None and resolve_name(to_simplified(written)) == drug
    assert to_simplified("ABC 甘草") == "ABC 甘草"
    # Simplifying a name the table already knows never moves it to another drug.
    moved = {n: (resolve_name(n), resolve_name(to_simplified(n))) for n in _NAME_INDEX
             if resolve_name(to_simplified(n)) not in (None, resolve_name(n))}
    assert moved == {}


# ================================================================== the herb layer

def test_hkbu_compositions_enter_the_herb_layer_traceably(tmp_path):
    (f1,) = load_hkbu_formulas(_export(tmp_path, WHOLE)).versions
    nodes, edges = herb_rows([GEGEN_QINLIAN, f1])
    assert all(validate_node(n) == [] for n in nodes)
    assert all(validate_edge(e) == [] for e in edges)
    ours = {e["object"]: e for e in edges if e["subject"] == f1.id}
    gancao = ours["tcm:herb.gancao"]
    assert gancao["source_record_id"] == f"{KEY}:r4"
    assert gancao["primary_knowledge_source"] == KEY and gancao["license"] == LICENSE
    assert gancao["raw"] == {"role": "", "dose": "6g", "processing": "炙", "written": ""}
    assert ours["tcm:herb.huangqin"]["raw"]["written"] == "黃芩"
    node = next(n for n in nodes if n["id"] == f1.id)
    assert node["raw"]["fingerprint"] == f1.fingerprint
    # the hand-checked formula's edges and fingerprint are what they were
    gold = [e for e in edges if e["subject"] == GEGEN_QINLIAN.id]
    assert {e["primary_knowledge_source"] for e in gold} == {HERB_KEY}
    assert all("written" not in e["raw"] for e in gold)
    blob = json.dumps({"id": GEGEN_QINLIAN.id, "source": GEGEN_QINLIAN.source,
                       "components": GEGEN_QINLIAN.components},
                      ensure_ascii=False, sort_keys=True)
    assert GEGEN_QINLIAN.fingerprint == "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def test_the_herb_layer_names_every_composition_source(tmp_path):
    path = _export(tmp_path, WHOLE)
    (f1,) = load_hkbu_formulas(path).versions
    table = FormulaVersion("tcm:formula.fx000000000001", "演示方（虚构）", "某书",
                           GEGEN_QINLIAN.components, license=TABLE_LICENSE)
    both = build_gold(tmp_path, tmp_path / "both", sources=(), formulas=[table, f1],
                      formula_files={"formula_herb.tsv": path}).snapshots[HERB_KEY]
    content = both.manifest["content"]
    assert content["license"] == (f"{HERB_LICENSE} (herb → species); {LICENSE}, "
                                  f"{TABLE_LICENSE} (compositions)")
    assert HKBU_CITATION in content["citation"] and "user-supplied" in content["citation"]
    assert {"formula_herb.tsv", "hkbu.py", "traditional.py", "formulas.py"} <= set(
        content["raw_files"])
    # a build from the formula table alone states what it stated before
    alone = build_gold(tmp_path, tmp_path / "alone", sources=(), formulas=[table])
    content = alone.snapshots[HERB_KEY].manifest["content"]
    assert content["citation"] == (CITATION + "; formula compositions from the user-supplied "
                                   "formula table (origin and licence not stated)")
    assert content["license"] == f"{HERB_LICENSE} (herb → species); {TABLE_LICENSE} (compositions)"
    assert "hkbu.py" not in content["raw_files"]


# ===================================================================== the skill run

def _world_with(tmp_path: Path, formulas) -> SnapshotLedger:
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    raw = tmp_path / "snap" / "raw.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("fixture", encoding="utf-8")
    (np_nodes, np_edges), (r_nodes, r_edges), (s_nodes, s_edges) = _world()
    herb_nodes, herb_edges = herb_rows([GEGEN_QINLIAN, *formulas])
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=tmp_path / "snap",
                  ledger=ledger)
    build_snapshot(key=HERB_KEY, version="gold", nodes=herb_nodes, edges=herb_edges,
                   license=HERB_LICENSE, citation="fixture", **common)
    # LOTUS allows commercial use, so a commercial run keeps its compounds
    build_snapshot(key="lotus", version="2026-04-13", nodes=np_nodes, edges=np_edges,
                   license="CC0-1.0", citation="fixture", **common)
    build_snapshot(key="reactome", version="current", nodes=r_nodes, edges=r_edges,
                   license="CC0-1.0", citation="fixture", **common)
    build_snapshot(key="string", version="12.0+fx", nodes=s_nodes, edges=s_edges,
                   license="CC-BY-4.0", citation="fixture", **common)
    return ledger


def _run(tmp_path, ledger, formula, out, **kw):
    return run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap",
                     ledger_path=ledger.path, out_dir=tmp_path / out, params=FAST,
                     allowed={"lotus", "string", "reactome"}, formula=formula, **kw)


def test_the_skill_studies_an_hkbu_formula_and_cites_its_rows(tmp_path):
    (f1,) = load_hkbu_formulas(_export(tmp_path, WHOLE)).versions
    ledger = _world_with(tmp_path, [f1])
    provenance = _run(tmp_path, ledger, f1, "run")
    assert provenance["formula"]["id"] == "hkbu:formula.F1"
    assert provenance["formula"]["license"] == [LICENSE]
    assert provenance["claims"] == {"candidates": 1, "released": 1, "refused": 0}
    (claim,) = json.loads((tmp_path / "run" / "claims.json").read_text(encoding="utf-8"))
    assert claim["subject"] == f1.id and claim["kind"] == "mechanism_hypothesis"
    cited = [record for _, record in claim["support"]]
    assert cited[0] == f"{KEY}:r1"                 # the path starts at the HKBU row


def test_a_commercial_run_needs_a_composition_licensed_for_it(tmp_path):
    """Before: a commercial run kept only sources whose card allows commercial use, yet
    studied a formula whose composition record has no stated licence."""
    (f1,) = load_hkbu_formulas(_export(tmp_path, WHOLE)).versions
    table = FormulaVersion("tcm:formula.fx000000000001", "演示方（虚构）", "某书",
                           GEGEN_QINLIAN.components, license=TABLE_LICENSE)
    ledger = _world_with(tmp_path, [f1, table])
    for formula, licence in ((f1, LICENSE), (table, TABLE_LICENSE)):
        with pytest.raises(SkillRunRefused, match=f"commercial use .* recorded under {licence}"):
            _run(tmp_path, ledger, formula, "refused", purpose="commercial")
        assert _run(tmp_path, ledger, formula, "academic")["claims"]["released"] == 1
    provenance = _run(tmp_path, ledger, GEGEN_QINLIAN, "gold", purpose="commercial")
    assert provenance["purpose"] == "commercial"
    assert provenance["formula"]["license"] == [HERB_LICENSE]
    assert provenance["claims"]["released"] == 1


def test_a_formula_changed_after_the_build_is_refused(tmp_path):
    """Before: the run went ahead on the snapshot's composition and recorded the edited
    formula's fingerprint in the provenance."""
    (f1,) = load_hkbu_formulas(_export(tmp_path, WHOLE)).versions
    ledger = _world_with(tmp_path, [f1])
    rows = [dict(r) for r in WHOLE]
    rows[0]["dose"] = "30"
    (edited,) = load_hkbu_formulas(_export(tmp_path / "edited", rows)).versions
    assert edited.id == f1.id and edited.fingerprint != f1.fingerprint
    with pytest.raises(SkillRunRefused, match="changed after the snapshot was built"):
        _run(tmp_path, ledger, edited, "run")


# ======================================================================= the scripts

def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_run_script_finds_an_hkbu_formula_or_says_why_not(tmp_path, monkeypatch):
    run = _script("run_network_pharmacology")
    path = _export(tmp_path, WHOLE + [_row("F2", "葛根", "r5", status="pending")])
    assert run._formula("hkbu:formula.F1", str(path)).record_ids[0] == f"{KEY}:r1"
    with pytest.raises(SkillRunRefused, match="F2 cannot be studied: r5: review_status pending"):
        run._formula("hkbu:formula.F2", str(path))
    with pytest.raises(SkillRunRefused, match="no formula 'hkbu:formula.F9'"):
        run._formula("hkbu:formula.F9", str(path))
    # by default the export is read from the hub's raw directory
    monkeypatch.setenv("BIOAGENT_TCMDB", str(tmp_path))
    assert run._formula("hkbu:formula.F1").id == "hkbu:formula.F1"
    monkeypatch.setenv("BIOAGENT_TCMDB", str(tmp_path / "elsewhere"))
    with pytest.raises(SkillRunRefused, match="no reviewed HKBU export"):
        run._formula("hkbu:formula.F1")
