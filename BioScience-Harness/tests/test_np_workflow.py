"""The network-pharmacology workflow: sixteen recorded stages and nine exports.

On the planted worlds (bioagent.research.planted) and the small world of
test_network_pharmacology. Nothing here is a measurement: the worlds are fixtures, built
under '+planted' / '+fx' versions, and they test what the workflow records and refuses to
claim, not any biology. The exports are checked against the snapshots they were read from.
"""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from bioagent.analysis import run_network_pharmacology
from bioagent.analysis.np_exports import (COLUMNS, EXPORTS, MANIFEST_FILE, MANIFEST_SCHEMA,
                                          manifest_problems, verify, write_exports,
                                          write_manifest)
from bioagent.analysis.skill_runner import run_skill
from bioagent.research import QuestionRefused, default_protocol, parse_question, run_research
from bioagent.research.planted import build_world
from bioagent.research.workflow import WORKFLOW_STAGES
from bioagent.sources import build_snapshot
from bioagent.sources.herbs import GEGEN_QINLIAN, HERBS, herb_rows
from bioagent.sources.herbs import KEY as HERB_KEY, LICENSE as HERB_LICENSE
from bioagent.sources.ledger import SnapshotLedger
from bioagent.validation import BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED

from test_network_pharmacology import (FAST, PATHWAY_A, PROTEINS, SKILL_DIR, _build,
                                       _disease_snapshot, _edge, _world)

QUESTION = "葛根芩连汤的实测靶点集中在哪些通路？"
PLANTED = replace(FAST, background="assayed")       # the planted worlds' own question
TABLES = [name for name in EXPORTS if name.endswith(".tsv")]
DATA_STAGES = ("herb_compound", "structure_identity", "compound_target",
               "measured_vs_predicted", "enrichment_background", "multiple_testing",
               "permutation_sensitivity")


def _manifest(out: Path) -> dict:
    return json.loads((Path(out) / MANIFEST_FILE).read_text(encoding="utf-8"))


def _stages(out: Path) -> dict:
    return {s["id"]: s for s in _manifest(out)["stages"]}


def _check(stage: dict, name: str) -> dict:
    return next(c for c in stage["checks"] if c["name"] == name)


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _research(tmp: Path, root: Path, ledger: Path, tag: str = "a", **kw):
    q = parse_question(QUESTION)
    protocol = default_protocol(q, parameters=PLANTED, **kw)
    return run_research(q, protocol=protocol, snapshot_root=root, ledger_path=ledger,
                        state_dir=tmp / f"state-{tag}", output_dir=tmp / f"out-{tag}")


def _planted(tmp: Path, scenario: str) -> tuple[Path, Path]:
    ledger = SnapshotLedger(tmp / scenario / "ledger.jsonl")
    build_world(scenario, tmp / scenario / "snap", ledger=ledger)
    return tmp / scenario / "snap", ledger.path


# ------------------------------------------------------------------ the research job

@pytest.mark.parametrize("scenario", ["selective", "coverage", "promiscuous"])
def test_the_sixteen_stages_are_recorded_in_order_and_never_pass_without_data(tmp_path, scenario):
    root, ledger = _planted(tmp_path, scenario)
    run = _research(tmp_path, root, ledger)
    out = Path(run.output_dir)
    manifest = _manifest(out)
    assert manifest["schema"] == MANIFEST_SCHEMA and manifest["runner"] == "research"
    assert [s["id"] for s in manifest["stages"]] == list(WORKFLOW_STAGES)
    assert [s["n"] for s in manifest["stages"]] == list(range(1, 17))
    stages = _stages(out)
    # GO and KEGG are never tested here, and never PASS or FAIL
    annotation = stages["pathway_annotation"]
    assert _check(annotation, "go")["status"] == SKIPPED
    assert "no GO annotation snapshot registered" in _check(annotation, "go")["detail"]
    assert _check(annotation, "kegg")["status"] == BLOCKED
    assert "licence required" in _check(annotation, "kegg")["detail"]
    assert _check(annotation, "reactome")["status"] == PASS
    # no disease was named
    assert stages["disease_genes"]["status"] == NOT_APPLICABLE
    assert stages["intersections"]["status"] == NOT_APPLICABLE
    # the planted worlds have no STRING snapshot: the protocol dropped it, and the PPI
    # stages say so instead of passing on an empty network
    for sid in ("ppi_network", "topology"):
        assert stages[sid]["status"] == UNVERIFIED
        assert "none were read, not because none exist" in stages[sid]["reason"]
        assert stages[sid]["deviations"][0]["source"] == "string"
    # deduplication is reported as InChIKey-exact, never as a re-standardisation
    assert stages["structure_identity"]["status"] == UNVERIFIED
    assert stages["formula_identity"]["status"] == PASS
    assert stages["reproducible_export"]["status"] == PASS
    # every stage carries a reason, its counts and the command that reproduces the run
    for s in manifest["stages"]:
        assert s["reason"] and isinstance(s["counts"], dict)
        assert s["reproduce"].startswith("bioagent research ")
    assert manifest["reproduce"]["argv"][:2] == ["bioagent", "research"]
    assert "--formula" in manifest["reproduce"]["argv"] and manifest["reproduce_exact"] is True
    # the nine exports exist; all but the manifest are artifact outputs with their digests
    for name in EXPORTS:
        assert (out / name).is_file(), name
    listed = {f.path: f.sha256 for f in run.artifact.outputs}
    for name in EXPORTS:
        if name != MANIFEST_FILE:
            assert "sha256:" + listed[name] == manifest["outputs"][name]["sha256"], name
    assert manifest["execution"]["audit_head"] == run.audit_head
    assert manifest_problems(out) == []
    assert run.verdict.states["outputs_verified"] is True
    # every world tested pathways: the run is complete, whatever it found
    assert manifest["complete"] is True and manifest["problems"] == [], manifest["problems"]
    # and the whole result recomputes from the snapshots the manifest names
    report = verify(out, snapshot_root=root, ledger=ledger)
    assert report["status"] == PASS, report
    # the report lists the stages, and limitations.md is what the artifact records
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "Workflow stages" in html and "Pathway annotation (Reactome; GO; KEGG)" in html
    lines = (out / "limitations.md").read_text(encoding="utf-8").splitlines()
    assert lines == list(run.artifact.limitations)


def test_the_planted_answers_are_told_apart_by_their_stages(tmp_path):
    """selective: the pathway survives and a hypothesis is made; coverage: pathways were
    tested and none survived -- a different sentence from 'nothing could be tested'."""
    sel = _research(tmp_path / "s", *_planted(tmp_path / "s", "selective"))
    cov = _research(tmp_path / "c", *_planted(tmp_path / "c", "coverage"))
    assert sel.hypotheses and not cov.hypotheses
    s, c = _stages(Path(sel.output_dir)), _stages(Path(cov.output_dir))
    assert "1 mechanism hypothesis" in s["bounded_interpretation"]["reason"]
    assert "tested and none survived" in c["bounded_interpretation"]["reason"]
    assert c["permutation_sensitivity"]["status"] == NOT_APPLICABLE
    assert "nothing to permute" in c["permutation_sensitivity"]["reason"]
    # tested, and none survived: a completed negative result, not an incomplete run
    for run in (sel, cov):
        assert _manifest(Path(run.output_dir))["complete"] is True
    record = {st["name"]: st for st in json.loads(
        (Path(cov.output_dir) / "stages.json").read_text(encoding="utf-8"))["stages"]}
    assert record["permutation_sensitivity"]["status"] == "succeeded"
    assert record["permutation_sensitivity"]["check"] == NOT_APPLICABLE
    assert any(line.startswith("pathways were tested and none passed both BH and the "
                               "permutation null") for line in cov.artifact.limitations)
    assert not any("nothing could be tested" in line for line in cov.artifact.limitations)
    # one activity source: leave-one-source-out could not run, so the stage is not PASS
    assert s["permutation_sensitivity"]["status"] == UNVERIFIED
    assert "leave_one_source_out" in s["permutation_sensitivity"]["reason"]


def test_two_runs_in_fresh_state_directories_give_byte_identical_exports(tmp_path):
    root, ledger = _planted(tmp_path, "selective")
    first = Path(_research(tmp_path, root, ledger, tag="a").output_dir)
    second = Path(_research(tmp_path, root, ledger, tag="b").output_dir)
    for name in EXPORTS:
        if name != MANIFEST_FILE:
            assert (first / name).read_bytes() == (second / name).read_bytes(), name
    a, b = _manifest(first), _manifest(second)
    # only the execution block names the run: its audit chain and its artifact.json
    assert a.pop("execution")["audit_head"] != b.pop("execution")["audit_head"]
    assert a == b
    for name in EXPORTS:
        text = (first / name).read_text(encoding="utf-8")
        assert "finished_at" not in text and "created_at" not in text, name


def test_a_dropped_activity_source_makes_every_stage_reading_it_unverified(tmp_path):
    root, ledger = _planted(tmp_path, "selective")
    run = _research(tmp_path, root, ledger, optional=("string", "lotus"))
    assert {d["source"] for d in run.deviations} == {"string", "lotus"}
    stages = _stages(Path(run.output_dir))
    for sid in DATA_STAGES:
        assert stages[sid]["status"] not in (PASS, NOT_APPLICABLE), sid
        assert _check(stages[sid], "sources")["status"] == UNVERIFIED, sid
        assert "lotus" in _check(stages[sid], "sources")["detail"]
    # stages that read no activity source are untouched by it
    assert stages["formula_identity"]["status"] == PASS


def _empty_world(root: Path, ledger: SnapshotLedger, constituents: bool) -> None:
    """The herb layer, an activity source and Reactome, with no measured target for the
    formula: with ``constituents`` the formula's four compounds are recorded with only
    weak, censored and predicted activities; without, no constituent of it is recorded."""
    if constituents:
        _build(root, ledger=ledger, targets_of={0: [], 1: [], 2: [], 3: []})
        return
    raw = root / "raw.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("fixture", encoding="utf-8")
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=root, ledger=ledger)
    (np_nodes, _), (r_nodes, r_edges), _ = _world()
    other = "ncbitaxon:9606"                        # not a species of the formula's herbs
    np_nodes = np_nodes + [{"id": other, "category": "organism", "name": "x", "source": "fx"}]
    np_edges = [_edge("elsewhere", other, "contains", np_nodes[len(HERBS)]["id"],
                      "chemical_analysis", composition_level="C1")]
    herb_nodes, herb_edges = herb_rows()
    build_snapshot(key=HERB_KEY, version="gold", nodes=herb_nodes, edges=herb_edges,
                   license=HERB_LICENSE, citation="fixture", **common)
    build_snapshot(key="npass", version="2.0+fx", nodes=np_nodes, edges=np_edges,
                   license="fixture", citation="fixture", **common)
    build_snapshot(key="reactome", version="current", nodes=r_nodes, edges=r_edges,
                   license="CC0-1.0", citation="fixture", **common)


@pytest.mark.parametrize("constituents", [True, False])
def test_an_empty_result_writes_all_nine_files_and_says_nothing_could_be_tested(tmp_path,
                                                                                 constituents):
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    _empty_world(tmp_path / "snap", ledger, constituents)
    run = _research(tmp_path, tmp_path / "snap", ledger.path)
    assert run.released and run.hypotheses == ()       # a negative result is released
    out = Path(run.output_dir)
    for name in EXPORTS:
        assert (out / name).is_file() and (out / name).stat().st_size, name
    header_only = ["targets.tsv", "disease_targets.tsv", "network.tsv"]
    if not constituents:
        header_only += ["compounds.tsv", "enrichment.tsv"]
    for name in header_only:
        lines = (out / name).read_text(encoding="utf-8").splitlines()
        assert lines == ["\t".join(COLUMNS[name])], name
    limits = (out / "limitations.md").read_text(encoding="utf-8")
    if constituents:
        assert ("No measured target was found for the 4 constituents in these snapshots, so "
                "no enrichment could be tested; an empty result, not evidence of inactivity."
                in limits)
    else:
        assert ("No constituent of the formula is recorded in these snapshots, so no measured "
                "target could be looked for and no enrichment could be tested; an empty "
                "result, not evidence of inactivity." in limits)
    # no fold-enrichment bound is stated for a background with no hit in it
    assert "inf-fold" not in limits and "0% of the assayed proteins" not in limits
    assert ("nothing could be tested, so the run releases no hypothesis: an empty result, not "
            "evidence of inactivity") in limits
    stages = _stages(out)
    assert stages["compound_target"]["status"] == UNVERIFIED
    assert stages["enrichment_background"]["status"] == UNVERIFIED
    assert "no enrichment could be tested" in stages["enrichment_background"]["reason"]
    assert stages["multiple_testing"]["status"] == UNVERIFIED
    assert "nothing could be tested" in stages["bounded_interpretation"]["reason"]
    assert stages["reproducible_export"]["status"] == PASS
    _assert_nothing_passes_on_an_empty_result(out, string_read=constituents)
    coverage = _manifest(out)["coverage"]
    assert coverage["targets"]["measured"] == 0
    assert coverage["compounds"]["total"] == (4 if constituents else 0)
    assert manifest_problems(out) == []


def _assert_nothing_passes_on_an_empty_result(out: Path, *, string_read: bool) -> None:
    """Stages 5-15 of a run with no measured target: none PASSes (the disease stages, with
    no disease, are not applicable or skipped), the PPI stages say why there is no network,
    and the run is not complete: nothing could be tested."""
    stages = _stages(out)
    for sid in WORKFLOW_STAGES[4:15]:
        assert stages[sid]["status"] != PASS, (sid, stages[sid]["reason"])
    for sid in ("ppi_network", "topology"):
        assert stages[sid]["status"] == UNVERIFIED, sid
        if string_read:
            # STRING was read: an empty network is not a network without interactions
            assert "no network could be built" in stages[sid]["reason"], sid
            assert "not evidence of no interaction" in stages[sid]["reason"], sid
    assert _check(stages["measured_vs_predicted"], "targets_from_measurements")["status"] \
        == UNVERIFIED
    assert _check(stages["measured_vs_predicted"], "hypotheses_cite_measured_edges")[
        "status"] == NOT_APPLICABLE
    manifest = _manifest(out)
    assert manifest["complete"] is False
    problems = " ".join(manifest["problems"])
    for sid in ("enrichment_background", "multiple_testing", "permutation_sensitivity",
                "bounded_interpretation"):
        assert f"stage {sid} not applicable" in problems, sid
    record = json.loads((out / "stages.json").read_text(encoding="utf-8"))
    assert record["complete"] is False
    assert {st["name"]: st["status"] for st in record["stages"]}["enrichment_background"] \
        == "not_applicable"


def test_the_skill_run_on_an_empty_result_passes_no_stage_and_is_not_complete(tmp_path):
    """The world with STRING and no measured target (the reviewer's case): the skill runner
    and the research job record it alike."""
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    _empty_world(tmp_path / "snap", ledger, constituents=True)
    run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap", ledger_path=ledger.path,
              out_dir=tmp_path / "run", params=FAST, allowed={"npass", "string", "reactome"})
    out = tmp_path / "run"
    assert _manifest(out)["snapshots"]["string"]
    _assert_nothing_passes_on_an_empty_result(out, string_read=True)
    assert manifest_problems(out) == []


def test_a_tampered_export_fails_the_manifest_check(tmp_path):
    root, ledger = _planted(tmp_path, "selective")
    out = Path(_research(tmp_path, root, ledger).output_dir)
    assert verify(out)["status"] in (PASS, UNVERIFIED)       # no snapshots: recompute unverified
    path = out / "enrichment.tsv"
    path.write_text(path.read_text(encoding="utf-8").replace("\t8\t", "\t7\t", 1),
                    encoding="utf-8")
    assert any(p.startswith("enrichment.tsv") for p in manifest_problems(out))
    report = verify(out, snapshot_root=root, ledger=ledger)
    assert report["status"] == FAIL
    assert {c["name"] for c in report["checks"] if c["status"] == FAIL} >= {"manifest_digests"}


# ------------------------------------------------------------------ the formula parameter

def test_a_formula_given_separately_must_be_the_one_the_question_names(monkeypatch):
    monkeypatch.setenv("BIOAGENT_FORMULA_TABLE", "none")      # the workbook is not needed
    q = parse_question("哪些通路？", formula="葛根芩连汤")
    assert q.formula_id == GEGEN_QINLIAN.id and q.text == "哪些通路？"
    assert parse_question(QUESTION, formula=GEGEN_QINLIAN.id).formula_id == GEGEN_QINLIAN.id
    assert parse_question("MONDO_0005148 哪些通路？", formula="葛根芩连汤").disease == "MONDO_0005148"
    from bioagent.sources.formulas import FormulaTable
    with pytest.raises(QuestionRefused, match="no formula has id"):
        parse_question(QUESTION, formula="tcm:formula.nope", table=FormulaTable([]))
    table = _one_formula_table()
    other = table.named("桂枝甘草汤")[0].id
    assert parse_question("桂枝甘草汤的通路", formula=other, table=table).formula_id == other
    with pytest.raises(QuestionRefused, match="the question names"):
        parse_question("tcm:formula.gegen_qinlian_tang 的通路", formula=other, table=table)


def _one_formula_table():
    from bioagent.sources.formulas import FormulaTable, records_from_rows
    return FormulaTable(records_from_rows([
        ("桂枝甘草汤（《伤寒论》。）", "桂枝4两（去皮），甘草2两（炙）。", "《伤寒论》。", "",
         "", "", "")]))


def test_the_research_command_studies_the_formula_it_is_given(tmp_path, capsys, monkeypatch):
    from bioagent.cli import main
    monkeypatch.setenv("BIOAGENT_FORMULA_TABLE", "none")
    root, ledger = _planted(tmp_path, "selective")
    code = main(["research", "这些成分的实测靶点集中在哪些通路？", "--formula", "葛根芩连汤",
                 "--snapshots", str(root), "--ledger", str(ledger),
                 "--state-dir", str(tmp_path / "st"), "--out", str(tmp_path / "o"),
                 "--permutations", "200"])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0 and summary["formula"] == GEGEN_QINLIAN.id
    assert [s["id"] for s in summary["stages"]] == list(WORKFLOW_STAGES)
    assert summary["coverage"]["herbs"]["total"] == 4
    assert set(EXPORTS) - {MANIFEST_FILE} <= set(summary["exports"])
    assert main(["research", "tcm:formula.gegen_qinlian_tang", "--formula", "tcm:formula.x",
                 "--snapshots", "x", "--ledger", "y", "--state-dir", "z", "--out", "w"]) == 2


# ------------------------------------------------------------------ the skill runner's exports

def _skill(tmp: Path, tag: str = "run") -> Path:
    return _skill_run(tmp, tag)[0]


def _skill_run(tmp: Path, tag: str = "run") -> tuple[Path, dict]:
    ledger = SnapshotLedger(tmp / "audit" / "snapshots.jsonl")
    if not (tmp / "snap").is_dir():
        _build(tmp / "snap", ledger=ledger)
    provenance = run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp / "snap",
                           ledger_path=ledger.path, out_dir=tmp / tag, params=FAST,
                           allowed={"npass", "string", "reactome"})
    return tmp / tag, provenance


def test_the_skill_run_writes_the_nine_exports_and_its_stages(tmp_path):
    out, returned = _skill_run(tmp_path)
    for name in EXPORTS:
        assert (out / name).is_file(), name
    for name in TABLES:
        assert (out / name).read_text(encoding="utf-8").splitlines()[0].split("\t") == COLUMNS[name]
    stages = _stages(out)
    assert list(stages) == list(WORKFLOW_STAGES)
    assert stages["ppi_network"]["status"] == PASS and stages["topology"]["status"] == PASS
    assert stages["compound_target"]["status"] == PASS
    # the contract names a disease (opentargets@26.09+MONDO_0005148) and the run refused its
    # snapshot: the disease stages are skipped and say so, never "no disease was named"
    for sid in ("disease_genes", "intersections"):
        assert stages[sid]["status"] == SKIPPED, sid
        assert ("opentargets@26.09+MONDO_0005148 was requested by the run's contract and "
                "refused: not allowed for this run") in stages[sid]["reason"], sid
        assert "no disease was named" not in stages[sid]["reason"]
    # the refused activity sources are named by every stage that reads activity
    refused = {r["source"] for r in stages["compound_target"]["refused"]}
    assert refused == {"cmaup@2.0", "lotus@2026-04-13", "pubchem_bioassay"}
    assert "refused" not in stages["formula_identity"]
    limits = (out / "limitations.md").read_text(encoding="utf-8")
    assert "Sources the run's contract requested and this run refused: cmaup@2.0 (not " \
           "allowed for this run)" in limits
    record = {st["name"]: st for st in json.loads(
        (out / "stages.json").read_text(encoding="utf-8"))["stages"]}
    assert record["disease_genes"]["status"] == "skipped"
    # the skill runner makes no rebuttal and says so; the research job makes them
    assert _check(stages["permutation_sensitivity"], "rebuttals")["status"] == SKIPPED
    assert _check(stages["permutation_sensitivity"], "permutation")["status"] == PASS
    manifest = _manifest(out)
    assert manifest["runner"] == "skill" and manifest_problems(out) == []
    assert manifest["complete"] is True and manifest["reproduce_exact"] is True
    # whether a lock was given identifies the execution: it is not in provenance.json
    assert manifest["execution"]["lock_given"] is False
    targets = {r["target"]: r for r in _rows(out / "targets.tsv")}
    a0 = targets[f"uniprot:{PATHWAY_A[0]}"]
    assert a0["evidence_class"] == "measured" and a0["publications"] == "1"
    assert a0["herbs"] == "tcm:herb.gegen" and a0["degree"] == "1"
    enrichment = _rows(out / "enrichment.tsv")
    assert {r["library"] for r in enrichment} == {"reactome"}
    provenance = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert "finished_at" not in provenance and provenance["coverage"]["herbs"]["total"] == 4
    assert "lock_given" not in provenance
    # the record run_skill returns is what provenance.json holds, field for field: the
    # skill run's own records in "outputs", every export's digest in the manifest
    assert set(provenance["outputs"]) == {"claims.json", "release.json"}
    for key, value in returned.items():
        assert provenance[key] == json.loads(json.dumps(value, default=str)), key


def test_two_skill_runs_write_the_same_bytes(tmp_path):
    first, second = _skill(tmp_path, "a"), _skill(tmp_path, "b")
    for name in EXPORTS:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_network_tsv_is_the_string_edges_at_or_above_the_cut_off(tmp_path):
    snaps = _build(tmp_path)
    result = run_network_pharmacology(snaps, params=FAST)
    out = tmp_path / "out"
    data = json.loads(json.dumps(result.as_dict(), default=str))
    data["digest"] = result.digest()
    write_manifest(write_exports(data, out, formula=GEGEN_QINLIAN, snaps=snaps))
    string = next(s for s in snaps if s.key == "string")
    nodes = {t["target"] for t in result.targets if t["degree"] is not None}
    expected = {(e["subject"], e["object"], float(e["score"]), e["source_record_id"])
                for e in string.edges if e["subject"] in nodes and e["object"] in nodes
                and e["score"] >= FAST.string_min_score}
    rows = _rows(out / "network.tsv")
    assert {(r["a"], r["b"], float(r["score"]), r["record"]) for r in rows} == expected
    assert len(rows) == result.network["edges"] == 4
    assert "low" not in {r["record"] for r in rows}          # the 0.2 edge is under the cut-off
    assert result.network["ppi_source"] == string.snapshot_id
    # every row of the tables rests on a snapshot record named in provenance.json
    edges = json.loads((out / "provenance.json").read_text(encoding="utf-8"))["edges"]
    by_snapshot = {s.snapshot_id: {str(e["source_record_id"]) for e in s.edges} for s in snaps}
    for kind in ("constituents", "activities", "interactions", "pathway_membership",
                 "composition"):
        assert edges[kind], kind
        for ref in edges[kind]:
            assert ref["record"] in by_snapshot[ref["snapshot"]], (kind, ref)
    traced = {r["target"] for r in edges["activities"]}
    assert traced == {r["target"] for r in _rows(out / "targets.tsv")}
    assert {r["compound"] for r in edges["constituents"]} == {
        r["compound"] for r in _rows(out / "compounds.tsv")}


def test_a_screening_run_counts_its_active_calls_as_its_measurements(tmp_path):
    # PubChem BioAssay active calls are observations that carry no activity value: in a
    # screening run they are the run's measurements (stage 5 accepts them), so stage 6 must
    # not call the targets they support predicted, nor a hypothesis that cites one unsupported
    from test_network_pharmacology import SCREEN, _screening_snapshot

    snaps = _build(tmp_path / "w")
    tested = PATHWAY_A + PROTEINS[8:36]
    active = {i: PATHWAY_A[:6] for i in range(4)}
    active[0] = active[0] + [PROTEINS[8 + 7 * k] for k in range(4)]
    screen = _screening_snapshot(tmp_path / "pc", active, tested)
    assert not any(e.get("measure") for e in screen.edges), "the fixture's calls have no value"
    result = run_network_pharmacology([*snaps, screen], params=SCREEN)
    assert result.claims and not result.release["refused"]
    data = json.loads(json.dumps(result.as_dict(), default=str))
    data["digest"] = result.digest()
    exports = write_exports(data, tmp_path / "out", formula=GEGEN_QINLIAN, snaps=[*snaps, screen])
    stages = {s["id"]: s for s in exports.stages}
    assert stages["compound_target"]["status"] == PASS
    split = stages["measured_vs_predicted"]
    assert _check(split, "targets_from_measurements")["status"] == PASS, split
    assert _check(split, "hypotheses_cite_measured_edges")["status"] == PASS, split
    # a curated measurement the screening run left out supports nothing in it: only the
    # screening calls make a target measured here (and the other way round for potency)
    from bioagent.research.workflow import edge_split
    by_key = {s.key: s for s in [*snaps, screen]}
    survived = {"survived": [c["object"] for c in result.claims]}
    only_curated = dict(data, targets=[*data["targets"], {"target": f"uniprot:{PROTEINS[40]}"}])
    assert edge_split(only_curated, by_key, survived)["predicted_only"] == [f"uniprot:{PROTEINS[40]}"]
    potency = run_network_pharmacology([*snaps, screen], params=FAST)
    pdata = json.loads(json.dumps(potency.as_dict(), default=str))
    assert edge_split(pdata, by_key, {"survived": [c["object"] for c in potency.claims]})[
        "predicted_only"] == []


def test_without_string_the_network_is_empty_and_says_why(tmp_path):
    snaps = [s for s in _build(tmp_path) if s.key != "string"]
    result = run_network_pharmacology(snaps, params=FAST)
    assert result.network["ppi_source"] is None and result.network["edge_list"] == []
    assert ("No PPI snapshot was read: the network has no edges because none were read, not "
            "because none exist.") in result.limitations
    assert not any("depends on the STRING confidence cut-off" in line
                   for line in result.limitations)
    data = json.loads(json.dumps(result.as_dict(), default=str))
    data["digest"] = result.digest()
    exports = write_exports(data, tmp_path / "out", formula=GEGEN_QINLIAN, snaps=snaps,
                            requested=("tcm_herbs", "npass", "reactome"))
    stages = {s["id"]: s for s in exports.stages}
    assert stages["ppi_network"]["status"] == SKIPPED and stages["topology"]["status"] == SKIPPED


def test_the_disease_stages_pass_with_an_open_targets_snapshot(tmp_path):
    snaps = _build(tmp_path / "w")
    genetic = {p: 0.9 for p in PATHWAY_A[:5]} | {PATHWAY_A[5]: 0.2} | {
        p: 0.8 for p in PROTEINS[20:26]}
    disease = _disease_snapshot(tmp_path / "ot", genetic, {PATHWAY_A[6]: 0.9})
    result = run_network_pharmacology([*snaps, disease], params=FAST)
    data = json.loads(json.dumps(result.as_dict(), default=str))
    data["digest"] = result.digest()
    exports = write_exports(data, tmp_path / "out", formula=GEGEN_QINLIAN,
                            snaps=[*snaps, disease], disease="MONDO_0005148")
    stages = {s["id"]: s for s in exports.stages}
    assert stages["disease_genes"]["status"] == PASS
    assert stages["intersections"]["status"] == PASS
    assert stages["disease_genes"]["counts"]["disease_genes"] == 11
    # disease_targets.tsv is the whole gene set at the cut-off, the literature-only and the
    # under-cut-off genes left out
    rows = _rows(tmp_path / "out" / "disease_targets.tsv")
    want = {f"uniprot:{p}" for p in PATHWAY_A[:5]} | {f"uniprot:{p}" for p in PROTEINS[20:26]}
    assert {r["target"] for r in rows} == want
    assert {r["target"] for r in rows if r["is_measured_target"] == "True"} == {
        f"uniprot:{p}" for p in PATHWAY_A[:5]}
    assert all(r["datatypes"] == "genetic_association" for r in rows)
    refs = json.loads((tmp_path / "out" / "provenance.json").read_text(
        encoding="utf-8"))["edges"]["disease_associations"]
    assert {r["target"] for r in refs} == want


def test_identity_is_by_full_inchikey_and_shared_blocks_are_reported_not_merged(tmp_path):
    (np_nodes, np_edges), (r_nodes, r_edges), _ = _world()
    species = [f"ncbitaxon:{h.species[0].taxid}" for h in HERBS.values()]
    extra = ["inchikey:AAAAAAAAAAAAAA-BBBBBBBBBB-N", "inchikey:AAAAAAAAAAAAAA-CCCCCCCCCC-N",
             "npass:NPC0001"]
    np_nodes = np_nodes + [{"id": c, "category": "ingredient", "name": c, "source": "fx"}
                           for c in extra]
    np_edges = np_edges + [_edge(f"x{i}", species[i % 2], "contains", c, "chemical_analysis",
                                 composition_level="C1") for i, c in enumerate(extra)]
    raw = tmp_path / "raw.txt"
    raw.write_text("fixture", encoding="utf-8")
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=tmp_path)
    herb_nodes, herb_edges = herb_rows()
    snaps = [
        build_snapshot(key=HERB_KEY, version="gold", nodes=herb_nodes, edges=herb_edges,
                       license=HERB_LICENSE, citation="fixture", **common),
        build_snapshot(key="npass", version="2.0+fx", nodes=np_nodes, edges=np_edges,
                       license="fixture", citation="fixture", **common),
        # cmaup reports one compound npass has, under the identical key
        build_snapshot(key="cmaup", version="2.0+fx", nodes=np_nodes,
                       edges=[dict(np_edges[0], source_record_id="c-pair0")],
                       license="fixture", citation="fixture", **common),
        build_snapshot(key="reactome", version="current", nodes=r_nodes, edges=r_edges,
                       license="CC0-1.0", citation="fixture", **common),
    ]
    result = run_network_pharmacology(snaps, params=FAST)
    ident = result.coverage["identity"]
    assert len(result.compounds) == 7                # nothing merged across different keys
    assert ident["with_inchikey"] == 6 and ident["source_local"] == 1
    assert ident["merged_across_sources"] == 1       # one identical key from two sources
    assert ident["connectivity_groups"] == 1
    assert ident["groups"] == [{"connectivity": "AAAAAAAAAAAAAA",
                                "compounds": extra[:2]}]
    assert any("1 group(s) of distinct keys share a connectivity block" in line
               and "kept separate" in line for line in result.limitations)
    data = json.loads(json.dumps(result.as_dict(), default=str))
    data["digest"] = result.digest()
    write_exports(data, tmp_path / "out", formula=GEGEN_QINLIAN, snaps=snaps)
    rows = {r["compound"]: r for r in _rows(tmp_path / "out" / "compounds.tsv")}
    assert rows["npass:NPC0001"]["id_kind"] == "source_local"
    assert rows["npass:NPC0001"]["inchikey"] == "" and rows["npass:NPC0001"]["connectivity"] == ""
    assert rows[extra[0]]["connectivity"] == rows[extra[1]]["connectivity"] == "AAAAAAAAAAAAAA"
    assert rows[extra[0]]["inchikey"] == "AAAAAAAAAAAAAA-BBBBBBBBBB-N"


def test_the_skill_runs_reproduce_command_rebuilds_every_export(tmp_path):
    """The manifest's reproduce argv, its placeholders filled in, run through the script:
    every export comes out byte for byte, and the manifest differs only in its execution
    block (this run was given the lock)."""
    import importlib.util
    out = _skill(tmp_path)
    manifest = _manifest(out)
    argv = manifest["reproduce"]["argv"]
    assert argv[:2] == ["python", "scripts/run_network_pharmacology.py"]
    fill = {"SNAPSHOTS": str(tmp_path / "snap"),
            "LEDGER": str(tmp_path / "audit" / "snapshots.jsonl"),
            "OUT2": str(tmp_path / "again"),
            "OUT/snapshot_lock.json": str(out / "snapshot_lock.json")}
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_network_pharmacology.py"
    spec = importlib.util.spec_from_file_location("run_np_script", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([fill.get(a, a) for a in argv[2:]]) == 0
    again = tmp_path / "again"
    for name in EXPORTS:
        if name != MANIFEST_FILE:
            assert (out / name).read_bytes() == (again / name).read_bytes(), name
    second = _manifest(again)
    first_execution, second_execution = manifest.pop("execution"), second.pop("execution")
    assert manifest == second
    assert (first_execution["lock_given"], second_execution["lock_given"]) == (False, True)


def test_verify_exits_0_on_pass_1_on_a_contradiction_and_3_when_a_check_cannot_be_made(
        tmp_path, capsys):
    from bioagent.analysis.np_exports import main
    root, ledger = _planted(tmp_path, "selective")
    out = Path(_research(tmp_path, root, ledger).output_dir)
    assert main(["verify", str(out), "--snapshots", str(root), "--ledger", str(ledger)]) == 0
    # no snapshots given: nothing contradicts the run, but the recomputation was not made
    assert main(["verify", str(out)]) == 3
    assert main(["verify", str(out), "--snapshots", str(root)]) == 2
    # an empty enrichment table: nothing to recompute, which is not a failure
    empty = SnapshotLedger(tmp_path / "e" / "audit" / "snapshots.jsonl")
    _empty_world(tmp_path / "e" / "snap", empty, constituents=False)
    eout = Path(_research(tmp_path / "e", tmp_path / "e" / "snap", empty.path).output_dir)
    capsys.readouterr()
    assert main(["verify", str(eout), "--snapshots", str(tmp_path / "e" / "snap"),
                 "--ledger", str(empty.path)]) == 3
    report = json.loads(capsys.readouterr().out)
    assert {c["name"]: c["status"] for c in report["checks"]}["enrichment_arithmetic"] \
        == UNVERIFIED
    assert not any(c["status"] == FAIL for c in report["checks"])
    # a table edited after the run contradicts it
    path = out / "enrichment.tsv"
    path.write_text(path.read_text(encoding="utf-8").replace("\t8\t", "\t7\t", 1),
                    encoding="utf-8")
    assert main(["verify", str(out), "--snapshots", str(root), "--ledger", str(ledger)]) == 1


def test_a_failed_stage_makes_the_research_command_exit_3(tmp_path, capsys, monkeypatch):
    """A stage whose own check FAILs is not a result, whatever the release check said: the
    command exits 3 (the research.run job counts 0 and 1 as results), names the stage, and
    the run record says the run is not complete."""
    from bioagent.cli import main
    from bioagent.research import workflow

    def failing(ctx):
        rec = workflow._topology(ctx)
        rec.add("planted", FAIL, "a check this test makes fail")
        return rec

    monkeypatch.setattr(workflow, "_STAGES", tuple(failing if fn is workflow._topology else fn
                                                   for fn in workflow._STAGES))
    root, ledger = _planted(tmp_path, "selective")
    code = main(["research", QUESTION, "--snapshots", str(root), "--ledger", str(ledger),
                 "--state-dir", str(tmp_path / "st"), "--out", str(tmp_path / "o"),
                 "--permutations", "200"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert code == 3 and summary["released"] is True
    assert "failed: stage 14 topology: FAIL: planted: a check this test makes fail" \
        in captured.err
    assert summary["complete"] is False
    assert any(p.startswith("stage topology") for p in summary["problems"])
    record = {st["name"]: st for st in json.loads(
        (tmp_path / "o" / "stages.json").read_text(encoding="utf-8"))["stages"]}
    assert record["topology"]["status"] == "failed" and record["topology"]["check"] == FAIL


def test_the_skill_reproduce_command_says_when_it_cannot_carry_a_choice():
    """reproduce_exact is false when the run made a choice the script's options cannot
    write down: a parameter it has no option for, an empty allowance, accept_review, or a
    contract other than the default skill's. An ungoverned run's command says so."""
    from bioagent.analysis.skill_runner import _reproduce
    from bioagent.providers.skills import SkillContract
    contract = SkillContract.load(SKILL_DIR / "skill.yaml")
    kw = dict(contract=contract, governed=True, accept_review=False)
    argv, exact = _reproduce(GEGEN_QINLIAN, FAST, {"npass"}, "academic", **kw)
    assert exact and "--allow-ungoverned" not in argv
    assert argv[argv.index("--permutations") + 1] == str(FAST.permutations)
    assert _reproduce(GEGEN_QINLIAN, replace(FAST, fdr=0.1), {"npass"}, "academic",
                      **kw)[1] is False
    assert _reproduce(GEGEN_QINLIAN, FAST, set(), "academic", **kw)[1] is False
    assert _reproduce(GEGEN_QINLIAN, FAST, None, "academic",
                      **{**kw, "accept_review": True})[1] is False
    narrowed = replace(contract, sources=contract.sources[:3])
    assert _reproduce(GEGEN_QINLIAN, FAST, None, "academic",
                      **{**kw, "contract": narrowed})[1] is False
    argv, exact = _reproduce(GEGEN_QINLIAN, FAST, None, "academic",
                             **{**kw, "governed": False})
    assert exact and argv[-1] == "--allow-ungoverned"
