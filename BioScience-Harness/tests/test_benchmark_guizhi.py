"""The Guizhi Decoction and palpitations benchmark (bioagent.validation.benchmark).

The stages run on a planted world: a workbook of the two 《伤寒论》 rows as the real workbook
writes them (桂枝甘草汤 with its 草) plus one other 桂枝汤, and snapshots in which the five
herbs' species hold compounds measured against the proteins of one pre-registered cardiac
pathway. Nothing here is a real measurement. The tests check that each stage reads what
the snapshots hold, labels it, refuses what no edge records, and says when it cannot run;
and that snapshots it did not build itself never make a data stage PASS.
"""
from __future__ import annotations

import json
import random
import string
from pathlib import Path
from types import SimpleNamespace

import pytest

from bioagent.analysis.network_pharmacology import Parameters
from bioagent.research import loop
from bioagent.sources import build_snapshot
from bioagent.sources.formulas import FormulaTable, records_from_rows
from bioagent.sources.herbs import GEGEN_QINLIAN, KEY as HERB_KEY, LICENSE as HERB_LICENSE, herb_rows
from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.materia import crude_drugs
from bioagent.validation import BLOCKED, FAIL, PASS, SKIPPED, UNVERIFIED, benchmark

ROWS = [
    ("桂枝汤（《伤寒论》。）", "桂枝3两（去皮），芍药3两，甘草2两（炙），生姜3两（切），大枣12枚（擘）。",
     "《伤寒论》。", "", "外感风寒，汗出恶风，头痛发热，鼻鸣干呕，苔白不渴，脉浮缓或浮弱。", "", ""),
    ("桂枝汤（《圣济总录》卷二十五。）", "桂枝1两，芍药1两，甘草半两", "《圣济总录》卷二十五。",
     "", "", "", ""),
    ("桂枝甘草汤（《伤寒论》。）", "桂枝4两（去皮），草2两（炙）。", "《伤寒论》。", "",
     "发汗过多，其人叉手自冒心，心下悸，欲得按者。", "", ""),
]
HERBS = ("guizhi", "baishao", "shengjiang", "gancao", "dazao")
PROTEINS = [f"P{20000 + i}" for i in range(60)]
CARDIAC = PROTEINS[:8]                         # members of the planted cardiac pathway
FAST = Parameters(permutations=200)            # the assayed background, as pre-registered


def _key(i: int) -> str:
    r = random.Random(1000 + i)
    pick = lambda n: "".join(r.choice(string.ascii_uppercase) for _ in range(n))  # noqa: E731
    return f"inchikey:{pick(14)}-{pick(10)}-N"


def _edge(record, s, p, o, design, level="knowledge_assertion", **kw):
    return {"subject": s, "predicate": p, "object": o, "primary_knowledge_source": "fixture",
            "knowledge_level": level,
            "agent_type": "computational_model" if level == "prediction" else "manual_agent",
            "study_design": design, "license": "CC0-1.0", "source_record_id": record, **kw}


def _potent(record, c, t, value=500.0):
    return _edge(record, c, "targets", f"uniprot:{t}", "in_vitro", publications=["pmid:1"],
                 measure={"type": "IC50", "relation": "=", "value": value, "unit": "nM"})


@pytest.fixture
def table(monkeypatch):
    t = FormulaTable(records_from_rows(ROWS))
    monkeypatch.setattr(loop, "_table", lambda: t)
    return t


@pytest.fixture
def world(tmp_path, table):
    """(snapshot root, ledger path): the planted world, built and recorded."""
    root, ledger = tmp_path / "snap", SnapshotLedger(tmp_path / "snap" / "ledger.jsonl")
    raw = root / "raw.txt"
    raw.parent.mkdir(parents=True)
    raw.write_text("fixture", encoding="utf-8")
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=root, ledger=ledger)
    drugs = crude_drugs()
    formulas = [table.named("桂枝汤")[0].version(), table.named("桂枝甘草汤")[0].version()]
    nodes, edges = herb_rows([GEGEN_QINLIAN, *formulas], drugs=drugs)
    build_snapshot(key=HERB_KEY, version="bench", nodes=nodes, edges=edges, license=HERB_LICENSE,
                   citation="fixture", **common)
    species = [f"ncbitaxon:{drugs[f'tcm:herb.{h}'].species[0].taxid}" for h in HERBS]
    compounds = [_key(i) for i in range(len(HERBS))]
    np_nodes = ([{"id": s, "category": "organism", "name": s, "source": "fx"} for s in species]
                + [{"id": c, "category": "ingredient", "name": f"fixture compound {i}", "source": "fx"}
                   for i, c in enumerate(compounds)]
                + [{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "fx"}
                   for p in PROTEINS])
    contains = [_edge(f"pair{i}", species[i], "contains", compounds[i], "chemical_analysis",
                      composition_level="C1", publications=[f"pmid:{i + 2}"]) for i in range(len(HERBS))]
    targets_of = {0: CARDIAC[:3], 1: CARDIAC[3:5], 2: CARDIAC[5:7], 3: CARDIAC[7:8]}
    potent = [_potent(f"act{i}-{t}", compounds[i], t) for i, ts in targets_of.items() for t in ts]
    tested = [_potent(f"inactive-{t}", compounds[3], t, 90000.0) for t in PROTEINS[8:40]]
    predicted = [_edge("docked", compounds[1], "targets", f"uniprot:{PROTEINS[50]}", "in_silico",
                       level="prediction")]
    build_snapshot(key="npass", version="2.0+bench", nodes=np_nodes,
                   edges=contains + potent + tested + predicted, license="fixture",
                   citation="fixture", **common)
    mixed = [_potent("weak-repeat", compounds[0], CARDIAC[0], 50000.0)]
    build_snapshot(key="cmaup", version="2.0+bench", nodes=np_nodes,
                   edges=contains + [dict(e, source_record_id="c-" + e["source_record_id"])
                                     for e in potent + tested] + mixed,
                   license="fixture", citation="fixture", **common)
    pathways = {"R-HSA-5576890": ("Phase 3 - rapid repolarisation", CARDIAC)}
    for k in range(7):
        pathways[f"R-HSA-B{k}"] = (f"fixture pathway {k}", PROTEINS[8 + k * 7: 15 + k * 7])
    r_nodes = ([{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "fx"} for p in PROTEINS]
               + [{"id": f"reactome:{k}", "category": "pathway", "name": name, "source": "fx"}
                  for k, (name, _) in pathways.items()])
    r_edges = [_edge(f"{p}|{k}", f"uniprot:{p}", "participates_in", f"reactome:{k}", "expert_consensus")
               for k, (_, ps) in pathways.items() for p in ps]
    build_snapshot(key="reactome", version="current", nodes=r_nodes, edges=r_edges,
                   license="CC0-1.0", citation="fixture", **common)
    s_edges = [_edge(f"s{i}", f"uniprot:{CARDIAC[i]}", "interacts_with", f"uniprot:{CARDIAC[i + 1]}",
                     "in_silico", level="prediction", score=0.9) for i in range(4)]
    build_snapshot(key="string", version="12.0+bench", nodes=r_nodes[:60], edges=s_edges,
                   license="CC-BY-4.0", citation="fixture", **common)
    return root, ledger.path


def _by_stage(records):
    return {r.component.rsplit(".", 1)[-1]: r for r in records}


def test_the_definition_names_twelve_stages_and_pre_registers_its_tests():
    d = benchmark.definition()
    assert [s["n"] for s in d["stages"]] == list(range(1, 13))
    assert d["question"].startswith("Investigate the potential biological mechanisms through which "
                                    "Guizhi Decoction may influence palpitations")
    for s in d["stages"]:
        assert s["requirement"] and s["validation"] and "acceptable_limitations" in s
    assert {"R-HSA-5576891", "R-HSA-5576890"} <= {p["id"] for p in d["pathways"]["cardiac_electrophysiology"]}
    assert d["literature"]["europepmc"] and d["literature"]["clinicaltrials"]
    assert benchmark.STAGE_IDS[10] == "formula_distinction"


def test_on_the_planted_world_every_stage_runs_and_none_passes_on_snapshots_it_did_not_build(
        tmp_path, world):
    root, ledger = world
    doc, records = benchmark.run_benchmark(tmp_path / "work", snapshot_root=root, ledger=ledger,
                                           parameters=FAST, repeat=True, log=lambda m: None)
    by = _by_stage(records)
    assert len(records) == 12
    for stage in ("formula_version", "herbs", "formula_distinction"):
        assert by[stage].status == PASS, by[stage].as_dict()
    # the data stages ran, and their checks held; provenance alone keeps them from PASS
    for stage in ("compounds", "targets", "measured_vs_predicted", "pathways", "enrichment",
                  "hypotheses"):
        rec = by[stage]
        assert rec.check("data_provenance").status == UNVERIFIED, stage
        assert all(c.status in (PASS, UNVERIFIED) for c in rec.checks), rec.as_dict()
        assert rec.status == UNVERIFIED
    assert by["literature"].status == SKIPPED
    assert doc["acceptance"]["met"] and doc["acceptance"]["how"] == "explained"
    assert not doc["acceptance"]["completed"]

    art = tmp_path / "work" / "artifacts"
    compounds = json.loads((art / "03_compounds.json").read_text())
    assert compounds["count"] == 5 and all(v == 1 for v in compounds["per_herb"].values())
    targets = json.loads((art / "04_targets.json").read_text())
    assert {t["target"] for t in targets["targets"]} == {f"uniprot:{p}" for p in CARDIAC}
    split = json.loads((art / "05_measured_vs_predicted.json").read_text())
    assert split["predicted_compound_target_left_out"] == 1
    assert split["edge_classes"]["protein_protein (STRING)"] == {"measured": 0, "predicted": 4}
    pathways = json.loads((art / "06_pathways.json").read_text())
    found, = pathways["found"]
    assert found["name"] == "Phase 3 - rapid repolarisation" and len(found["measured_targets"]) == 8
    assert "R-HSA-5576891" not in {p["pathway"] for p in pathways["found"]}
    assert "Cardiac conduction" in pathways["not_annotated"]
    assert "lowest-level" in pathways["not_annotated_means"]
    enrichment = json.loads((art / "07_enrichment.json").read_text())
    assert [r["pathway"] for r in enrichment["significant"]] == ["reactome:R-HSA-5576890"]
    assert enrichment["rebuttal"]["survived"] == ["reactome:R-HSA-5576890"]
    evidence = json.loads((art / "09_evidence.json").read_text())
    kinds = {c["kind"] for c in evidence["contradictions"]}
    assert {"indication_recorded_for_a_related_formula", "mixed_measurements"} <= kinds
    mixed = next(c for c in evidence["contradictions"] if c["kind"] == "mixed_measurements")
    assert mixed["count"] == 1 and mixed["examples"][0]["target"] == f"uniprot:{CARDIAC[0]}"
    licences = {e["type"]: e["licenses"] for e in evidence["evidence"]}
    assert "efficacy" not in sum(licences.values(), [])
    assert licences["computational_enrichment"] == ["mechanism_hypothesis"]
    hypotheses = json.loads((art / "10_hypotheses.json").read_text())
    h, = hypotheses["hypotheses"]
    assert h["label"].startswith("mechanism hypothesis") and h["bears_on_palpitations"]
    assert "computational hypothesis awaiting experimental test" in h["statement"]
    distinction = json.loads((art / "11_formula_distinction.json").read_text())
    assert distinction["palpitation_recorded_for"] == ["桂枝甘草汤《伤寒论》"]
    assert distinction["shared_herbs"] == ["gancao", "guizhi"]
    assert distinction["related"]["erratum"]["id"] == "erratum.0001"
    assert distinction["research"]["targets_reached_by_shared_herbs"] == 4  # 桂枝's 3, 甘草's 1
    assert "reasoning across formulas" in distinction["statement"]
    # the second run, in a fresh state directory, gives the same artifacts
    assert by["artifacts"].check("repeat").status == PASS
    repro = doc["reproducibility"]
    assert repro["report"] == "scientific_reproducibility_report"
    assert repro["summary"]["status"][FAIL] == 0 and repro["summary"]["status"][PASS] >= 10


def test_offline_with_no_files_the_data_stages_say_what_is_missing_and_how_to_get_it(tmp_path, table):
    doc, records = benchmark.run_benchmark(tmp_path / "work", log=lambda m: None)
    by = _by_stage(records)
    assert by["formula_version"].status == PASS and by["formula_distinction"].status == PASS
    for stage in ("compounds", "targets", "enrichment", "hypotheses"):
        assert by[stage].status == SKIPPED
        assert "bioagent snapshots import" in by[stage].failure_reason
    assert doc["acceptance"]["how"] == "explained" and not doc["acceptance"]["completed"]
    paths = benchmark.write(doc, tmp_path / "out")
    assert [p.name for p in paths] == ["research_pipeline_report.json"]


def _stage(sid):
    return next(s for s in benchmark.definition()["stages"] if s["id"] == sid)


def test_a_target_no_snapshot_edge_records_fails_the_stage(tmp_path):
    run = benchmark._Run(tmp_path, benchmark.definition(), live=False, raw=tmp_path, snapshot_root=None,
                         ledger=None, parameters=FAST, log=lambda m: None)
    run.research = SimpleNamespace(snapshots={})
    run.acquisition = {"route": "given"}
    run.data_reason = "fixture"
    run.result = {"compounds": [{"compound": "inchikey:A"}],
                  "targets": [{"target": "uniprot:P99999", "measurements": 1}]}
    run.snaps = {"npass": SimpleNamespace(edges=[])}
    rec = benchmark._targets(run, _stage("targets"))
    assert rec.check("targets_measured").status == FAIL and "uniprot:P99999" in rec.check(
        "targets_measured").detail


def test_no_surviving_pathway_is_an_absence_of_evidence_with_the_test_s_power_stated(tmp_path):
    # live-data run 38127338070: 0 of 1123 pathways passed, on an assayed background where
    # most proteins are potent hits; the stage must not read as "these pathways are uninvolved"
    run = benchmark._Run(tmp_path, benchmark.definition(), live=False, raw=tmp_path, snapshot_root=None,
                         ledger=None, parameters=FAST, log=lambda m: None)
    run.research = SimpleNamespace(snapshots={}, artifact=None, verdict=None, released=True)
    run.acquisition = {"route": "given"}
    run.data_reason = "fixture"
    run.rebuttal = {"survived": [], "refuted": [], "not_run": []}
    run.result = {"claims": [], "background": {"proteins": 558, "potent_in_background": 540}}
    rec = benchmark._hypotheses(run, _stage("hypotheses"))
    detail = rec.check("labelled").detail
    assert rec.check("labelled").status == PASS
    assert "absence of evidence" in detail and "not evidence that any pathway is uninvolved" in detail
    assert "540 of the 558 assayed proteins are potent hits" in detail and "1.03-fold" in detail
    assert benchmark._power({}) == "" and "none of the 9" in benchmark._power(
        {"proteins": 9, "potent_in_background": 0})


class _Answer:
    def __init__(self, status, value=None, error="", http=None):
        self.status = SimpleNamespace(value=status)
        self.value, self.error, self.metadata = value, error, {"http_status": http}


class _Backend:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def invoke(self, manifest, **kw):
        self.calls.append((manifest.id, kw))
        return self.answers.pop(0)


def test_literature_is_recorded_as_answered_and_a_refused_host_is_blocked_never_passed(tmp_path):
    epmc = {"hitCount": 2, "resultList": {"result": [
        {"id": "1", "source": "MED", "pmid": "1", "title": "t", "pubYear": "2020",
         "pubTypeList": {"pubType": ["research-article", "Journal Article"]}},
        {"id": "2", "source": "MED", "pmid": "2", "title": "u", "pubYear": "2021",
         "pubType": "Randomized Controlled Trial; journal article"}]}}   # the lite result type
    backend = _Backend([_Answer("SUCCEEDED", epmc, http=200), _Answer("SUCCEEDED", {"hitCount": 0}),
                        _Answer("FAILED", error="HTTP 403 Forbidden by the sandbox egress policy", http=403),
                        _Answer("SUCCEEDED", {"nextPageToken": "x", "studies": [{"protocolSection": {
                            "identificationModule": {"nctId": "NCT0001", "briefTitle": "a trial"},
                            "statusModule": {"overallStatus": "COMPLETED"}}}]})])
    run = benchmark._Run(tmp_path, benchmark.definition(), live=True, raw=tmp_path, snapshot_root=None,
                         ledger=None, parameters=None, log=lambda m: None, backend=backend)
    rec = benchmark._literature(run, _stage("literature"))
    statuses = {c.name: c.status for c in rec.checks}
    assert statuses == {"query:guizhi_palpitation": PASS, "query:guizhi_gancao": PASS,
                        "query:guizhi_cardiac": BLOCKED, "query:guizhi_trials": PASS}
    assert rec.status == BLOCKED
    content = run.outputs["literature"]["content"]
    first = content["queries"][0]
    assert first["count"] == 2 and first["count_is"] == "total"
    assert first["records"][0]["publication_types"] == ["research-article", "Journal Article"]
    assert first["records"][1]["publication_types"] == ["Randomized Controlled Trial", "journal article"]
    trials = content["queries"][3]
    assert trials["records"][0]["nct_id"] == "NCT0001"
    assert trials["count_is"].startswith("returned") and trials["more"], "a page is not a total"
    assert [c[0] for c in backend.calls] == ["public.connector.europepmc"] * 3 + [
        "public.connector.clinicaltrials"]


def test_the_command_writes_the_report_and_the_summary_lists_what_did_not_pass(tmp_path, table, capsys):
    from bioagent.validation.cli import main, summary_markdown
    out = tmp_path / "out"
    assert main(["benchmark", "--out", str(out)]) == 0, "offline: nothing failed, so exit 0"
    doc = json.loads((out / "research_pipeline_report.json").read_text())
    assert doc["report"] == "research_pipeline_report" and doc["benchmark"]["id"] == "guizhi-palpitations"
    assert Path(doc["benchmark"]["artifacts"], "11_formula_distinction.json").is_file()
    assert main(["benchmark", "--out", str(out), "--strict"]) == 0, "SKIPPED is not BLOCKED"
    text = summary_markdown([out])
    assert "| research_pipeline_report | offline | 12 | 3 | 0 | 0 | 9 | 0 | 0 |" in text
    assert "acceptance explained" in text and "`benchmark.guizhi-palpitations.03.compounds` | SKIPPED" in text
    assert "never counted as passes" in text
    # two runs' reports of the same name, under one directory, are told apart by where they are
    import shutil
    shutil.copytree(out, tmp_path / "all" / "benchmark")
    shutil.copytree(out, tmp_path / "all" / "again")
    text = summary_markdown([tmp_path / "all"])
    assert "| benchmark/research_pipeline_report | offline | 12 |" in text
    assert "| again/research_pipeline_report | offline | 12 |" in text
    assert "| again/research_pipeline_report | `benchmark.guizhi-palpitations.03.compounds` | SKIPPED |" in text


def test_a_stage_that_raises_is_recorded_as_a_failure_and_the_others_still_report(tmp_path, table,
                                                                                  monkeypatch):
    def broken(run, stage):
        raise KeyError("a defect")
    monkeypatch.setattr(benchmark, "_herbs", broken)
    doc, records = benchmark.run_benchmark(tmp_path / "work", log=lambda m: None)
    by = _by_stage(records)
    assert by["herbs"].status == FAIL and "KeyError" in by["herbs"].failure_reason
    assert by["formula_version"].status == PASS and by["formula_distinction"].status == PASS
    assert doc["acceptance"]["how"] == "not met"


def test_a_live_run_repeats_identically_with_its_literature_replayed(tmp_path, world):
    # live-data run 38125314518 failed here: the repeat did not query the literature, so its
    # evidence artifact lacked the literature entry the first run's had
    root, ledger = world
    epmc = {"hitCount": 1, "resultList": {"result": [
        {"id": "1", "source": "MED", "pmid": "1", "title": "t", "pubYear": "2020",
         "pubType": "research-article"}]}}
    trials = {"studies": []}
    backend = _Backend([_Answer("SUCCEEDED", epmc), _Answer("SUCCEEDED", epmc),
                        _Answer("SUCCEEDED", epmc), _Answer("SUCCEEDED", trials)])
    doc, records = benchmark.run_benchmark(tmp_path / "work", snapshot_root=root, ledger=ledger,
                                           parameters=FAST, live=True, repeat=True, backend=backend,
                                           log=lambda m: None)
    by = _by_stage(records)
    assert by["literature"].status == PASS and len(backend.calls) == 4, "asked once, not twice"
    assert by["artifacts"].check("repeat").status == PASS, by["artifacts"].as_dict()
    evidence = json.loads((tmp_path / "work" / "artifacts" / "09_evidence.json").read_text())
    assert any(e["type"] == "literature" for e in evidence["evidence"])
    from bioagent.validation.cli import summary_markdown
    benchmark.write(doc, tmp_path / "out")
    text = summary_markdown([tmp_path / "out"])
    assert "### Benchmark stages: guizhi-palpitations (live)" in text
    assert "`benchmark.guizhi-palpitations.04.targets` | UNVERIFIED | data_provenance" in text
