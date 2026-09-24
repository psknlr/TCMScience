"""The network-pharmacology skill on a small synthetic world with a known answer.

Four herbs of 葛根芩连汤 (the real herb layer), one compound per herb, measured targets
concentrated in one Reactome pathway (``R-HSA-A``) out of a background of 60 proteins: the
pipeline must find that pathway, pass it through the degree-matched null, and release it
as a mechanism hypothesis that cites a real path of snapshot edges.
"""

from __future__ import annotations

import json
import random
import string
from dataclasses import replace
from pathlib import Path

import pytest

from bioagent.analysis import Parameters, run_network_pharmacology
from bioagent.analysis.network_pharmacology import _brandes
from bioagent.analysis.skill_runner import SkillRunRefused, run_skill
from bioagent.providers.skills import SkillContract
from bioagent.sources import build_snapshot, load_snapshot
from bioagent.sources.herbs import GEGEN_QINLIAN, HERBS, herb_rows
from bioagent.sources.herbs import KEY as HERB_KEY, LICENSE as HERB_LICENSE
from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.parsers import parse_opentargets
from bioagent.sources.release import check_release

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "tcm" / "network-pharmacology"
PROTEINS = [f"P{10000 + i}" for i in range(60)]
PATHWAY_A = PROTEINS[:8]                              # the pathway the targets fall in


def _inchikey(i: int) -> str:
    r = random.Random(i)
    pick = lambda n: "".join(r.choice(string.ascii_uppercase) for _ in range(n))  # noqa: E731
    return f"{pick(14)}-{pick(10)}-N"


def _edge(record, s, p, o, design, level="knowledge_assertion", **kw):
    return {"subject": s, "predicate": p, "object": o, "primary_knowledge_source": "fixture",
            "knowledge_level": level,
            "agent_type": "computational_model" if level == "prediction" else "manual_agent",
            "study_design": design, "license": "CC0-1.0", "source_record_id": record, **kw}


def _world(targets_of=None, tested_only=()):
    """(nodes, edges) for a natural-product source and for Reactome."""
    species = [f"ncbitaxon:{h.species[0].taxid}" for h in HERBS.values()]
    compounds = [f"inchikey:{_inchikey(i)}" for i in range(4)]
    targets_of = targets_of or {0: PATHWAY_A[:3], 1: PATHWAY_A[3:5], 2: PATHWAY_A[5:7],
                                3: [PATHWAY_A[7], PROTEINS[40]]}
    np_nodes = ([{"id": s, "category": "organism", "name": s, "source": "fx"} for s in species]
                + [{"id": c, "category": "ingredient", "name": f"cmp{i}", "source": "fx"}
                   for i, c in enumerate(compounds)]
                + [{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "fx"}
                   for p in PROTEINS])
    np_edges = [_edge(f"pair{i}", species[i], "contains", compounds[i], "chemical_analysis",
                      composition_level="C1", publications=[f"pmid:{i + 1}"])
                for i in range(4)]
    for i, ts in targets_of.items():
        for t in ts:
            np_edges.append(_edge(f"act{i}-{t}", compounds[i], "targets", f"uniprot:{t}",
                                  "in_vitro", publications=["pmid:99"],
                                  measure={"type": "IC50", "relation": "=", "value": 500.0,
                                           "unit": "nM"}))
    # things the skill must leave out
    np_edges += [
        _edge("weak", compounds[0], "targets", f"uniprot:{PROTEINS[50]}", "in_vitro",
              publications=["pmid:98"],
              measure={"type": "IC50", "relation": "=", "value": 50000.0, "unit": "nM"}),
        _edge("censored", compounds[0], "targets", f"uniprot:{PROTEINS[51]}", "in_vitro",
              publications=["pmid:97"],
              measure={"type": "Ki", "relation": ">", "value": 10.0, "unit": "nM"}),
        _edge("docked", compounds[1], "targets", f"uniprot:{PROTEINS[52]}", "in_silico",
              level="prediction"),
    ]
    np_edges += [_edge(f"inactive-{t}", compounds[2], "targets", f"uniprot:{t}", "in_vitro",
                       publications=["pmid:96"],
                       measure={"type": "IC50", "relation": "=", "value": 90000.0, "unit": "nM"})
                 for t in tested_only]
    pathways = {"R-HSA-A": PATHWAY_A}
    for k in range(7):                                  # background pathways, 7 proteins each
        pathways[f"R-HSA-B{k}"] = PROTEINS[8 + k * 7: 15 + k * 7]
    pathways["R-HSA-TINY"] = PROTEINS[:2]               # below the size range: not tested
    r_nodes = ([{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "fx"}
                for p in PROTEINS]
               + [{"id": f"reactome:{k}", "category": "pathway", "name": f"pathway {k}",
                   "source": "fx"} for k in pathways])
    r_edges = [_edge(f"{p}|{k}", f"uniprot:{p}", "participates_in", f"reactome:{k}",
                     "expert_consensus") for k, ps in pathways.items() for p in ps]
    s_edges = [_edge(f"s{i}", f"uniprot:{PATHWAY_A[i]}", "interacts_with",
                     f"uniprot:{PATHWAY_A[i + 1]}", "in_silico", level="prediction",
                     score=0.9) for i in range(4)]
    s_edges.append(_edge("low", f"uniprot:{PATHWAY_A[5]}", "interacts_with",
                         f"uniprot:{PATHWAY_A[6]}", "in_silico", level="prediction", score=0.2))
    return (np_nodes, np_edges), (r_nodes, r_edges), (r_nodes[:60], s_edges)


def _build(root: Path, ledger: SnapshotLedger | None = None, **kw):
    raw = root / "raw.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("fixture", encoding="utf-8")
    (np_nodes, np_edges), (r_nodes, r_edges), (s_nodes, s_edges) = _world(**kw)
    herb_nodes, herb_edges = herb_rows()
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=root, ledger=ledger)
    return [
        build_snapshot(key=HERB_KEY, version="gold", nodes=herb_nodes, edges=herb_edges,
                       license=HERB_LICENSE, citation="fixture", **common),
        build_snapshot(key="npass", version="fx", nodes=np_nodes, edges=np_edges,
                       license="fixture", citation="fixture", **common),
        build_snapshot(key="reactome", version="current", nodes=r_nodes, edges=r_edges,
                       license="CC0-1.0", citation="fixture", **common),
        build_snapshot(key="string", version="fx", nodes=s_nodes, edges=s_edges,
                       license="CC-BY-4.0", citation="fixture", **common),
    ]


# The planted world tests the pipeline's mechanics against the whole annotation; the
# assayed background has its own tests below.
FAST = Parameters(permutations=200, background="reactome")


def test_the_pipeline_finds_the_planted_pathway_and_releases_a_hypothesis(tmp_path):
    snaps = _build(tmp_path)
    result = run_network_pharmacology(snaps, params=FAST)
    assert {t["target"] for t in result.targets} == {f"uniprot:{p}" for p in PATHWAY_A
                                                     + [PROTEINS[40]]}
    top = result.enrichment[0]
    assert top["pathway"] == "reactome:R-HSA-A" and top["overlap"] == 8
    assert top["q_value"] <= 0.05 and top["empirical_p"] <= 0.05
    assert "reactome:R-HSA-TINY" not in {r["pathway"] for r in result.enrichment}
    assert [c["object"] for c in result.claims] == ["reactome:R-HSA-A"]
    claim = result.claims[0]
    assert claim["kind"] == "mechanism_hypothesis" and claim["path_licenses"] == [
        "mechanism_hypothesis"]
    assert "below C3" in claim["why_not_stronger"]
    assert len(result.release["released"]) == 1 and not result.release["refused"]


def test_what_the_skill_leaves_out_is_left_out_and_counted(tmp_path):
    result = run_network_pharmacology(_build(tmp_path), params=FAST)
    targets = {t["target"] for t in result.targets}
    for leftover in (PROTEINS[50], PROTEINS[51], PROTEINS[52]):
        assert f"uniprot:{leftover}" not in targets
    assert result.excluded["activity above the cut-off or not a potency measure"] == 2
    assert result.excluded["predicted target edge (not used by this skill)"] == 1
    loose = run_network_pharmacology(_build(tmp_path / "b"),
                                     params=replace(FAST, activity_max_nm=100_000.0))
    assert f"uniprot:{PROTEINS[50]}" in {t["target"] for t in loose.targets}


def test_the_same_inputs_give_the_same_result_in_any_order(tmp_path):
    snaps = _build(tmp_path)
    a = run_network_pharmacology(snaps, params=FAST).digest()
    b = run_network_pharmacology(list(reversed(snaps)), params=FAST).digest()
    assert a == b
    assert run_network_pharmacology(snaps, params=replace(FAST, seed=7)).digest() != a


def test_only_bh_significant_pathways_get_a_permutation_p_and_it_has_a_floor(tmp_path):
    result = run_network_pharmacology(_build(tmp_path), params=FAST)
    for row in result.enrichment:
        if row["q_value"] > 0.05:
            assert row["empirical_p"] is None
        else:
            assert row["empirical_p"] >= 1 / (FAST.permutations + 1)


def test_topology_is_descriptive_and_respects_the_confidence_cut_off(tmp_path):
    result = run_network_pharmacology(_build(tmp_path), params=FAST)
    net = result.network
    assert net["edges"] == 4 and "descriptive" in net["interpretation"]   # the 0.2 edge is out
    assert _brandes({"hub": {"a", "b", "c"}, "a": {"hub"}, "b": {"hub"}, "c": {"hub"}})[
        "hub"] == 1.0


def test_a_claim_stronger_than_its_path_is_refused_at_release(tmp_path):
    snaps = _build(tmp_path)
    result = run_network_pharmacology(snaps, params=FAST)
    claim = result.claims[0]
    from bioagent.sources.release import CandidateClaim
    stronger = CandidateClaim("mechanism", claim["subject"], claim["object"],
                              tuple(tuple(s) for s in claim["support"]))
    verdict = check_release([stronger], snaps)
    assert not verdict.ok and "not mechanism" in verdict.refused[0][1]


def test_the_analysis_needs_the_herb_layer_and_the_background(tmp_path):
    snaps = _build(tmp_path)
    with pytest.raises(ValueError, match="reactome"):
        run_network_pharmacology([s for s in snaps if s.key != "reactome"], params=FAST)
    with pytest.raises(ValueError, match="tcm_herbs"):
        run_network_pharmacology([s for s in snaps if s.key != "tcm_herbs"], params=FAST)


# ================================================================ the shipped skill

def test_the_shipped_skill_contract_is_valid_and_capped_at_a_hypothesis():
    contract = SkillContract.load(SKILL_DIR / "skill.yaml")
    assert contract.id == "tcm.network-pharmacology"
    assert contract.max_claim_kind == "mechanism_hypothesis"
    assert contract.steps["targets"]["design"] == "in_vitro"
    assert not contract.permits("mechanism") and not contract.permits("efficacy")


def test_the_shipped_skill_compiles_in_psh_and_is_refused_as_a_mechanism():
    pytest.importorskip("psh")
    from psh.policy import PolicySnapshot
    from psh.runtime import PlanRejected
    from psh.workflow import ScientificCompiler

    from bioagent.psh.skill_program import ClaimScope, skill_program

    contract = SkillContract.load(SKILL_DIR / "skill.yaml")
    scope = ClaimScope("human proteins (in silico)", GEGEN_QINLIAN.chinese, "pathways")
    policy = PolicySnapshot(profile_id="np-test", require_claim_support=False)
    compiled = ScientificCompiler().compile(skill_program(contract, scope), policy.envelope(),
                                            policy=policy)
    assert compiled.validated.order[-1] == "claim"
    # Raised to "mechanism", the in-vitro targets step alone could license it; the in-silico
    # enrichment and network steps cannot, and PSH refuses the program.
    raised = replace(contract, max_claim_kind="mechanism")
    with pytest.raises(PlanRejected, match="EVIDENCE103"):
        ScientificCompiler().compile(skill_program(raised, scope), policy.envelope(),
                                     policy=policy)


def test_run_skill_end_to_end_writes_outputs_and_provenance(tmp_path):
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    _build(tmp_path / "snap", ledger=ledger)
    # the contract asks for cmaup and lotus too; without snapshots of them the run is refused
    with pytest.raises(SkillRunRefused, match="cmaup"):
        run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap",
                  ledger_path=ledger.path, out_dir=tmp_path / "run", params=FAST)
    provenance = run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap",
                           ledger_path=ledger.path, out_dir=tmp_path / "run", params=FAST,
                           allowed={"npass", "string", "reactome"})
    out = tmp_path / "run"
    for name in ("compounds.tsv", "targets.tsv", "enrichment.tsv", "network.tsv",
                 "claims.json", "release.json", "provenance.json", "limitations.md"):
        assert (out / name).exists(), name
    assert provenance["random_seed"] == FAST.seed
    assert set(provenance["dataset_hashes"]) == {"tcm_herbs", "npass", "reactome", "string"}
    assert set(provenance["sources_refused"]) == {"cmaup@2.0", "lotus@2026-04-13",
                                                  "opentargets@26.06+MONDO_0005148",
                                                  "pubchem_bioassay"}
    assert provenance["disease"] == {}
    assert provenance["claims"] == {"candidates": 1, "released": 1, "refused": 0}
    assert provenance["result_digest"].startswith("sha256:")
    assert "reactome:R-HSA-A" in (out / "enrichment.tsv").read_text(encoding="utf-8")


def test_run_skill_refuses_a_snapshot_changed_after_it_was_recorded(tmp_path):
    ledger = SnapshotLedger(tmp_path / "audit" / "snapshots.jsonl")
    _build(tmp_path / "snap", ledger=ledger)
    # rebuild npass with an extra claim-relevant edge but without recording it
    _build(tmp_path / "snap", targets_of={0: PATHWAY_A, 1: [], 2: [], 3: []})
    with pytest.raises(Exception, match="recorded"):
        run_skill(skill_dir=SKILL_DIR, snapshot_root=tmp_path / "snap",
                  ledger_path=ledger.path, out_dir=tmp_path / "run", params=FAST,
                  allowed={"npass", "string", "reactome"})
    assert load_snapshot(tmp_path / "snap", "npass", "fx")   # consistent in itself


def ot_row(ensg: str, uniprot: str, score: float, **types: float) -> dict:
    return {"score": score,
            "datatypeScores": [{"id": k, "score": v} for k, v in types.items()],
            "target": {"id": ensg, "approvedSymbol": uniprot, "approvedName": uniprot,
                       "proteinIds": [{"id": uniprot, "source": "uniprot_swissprot"}]}}


def ot_answer(rows: list[dict], path: Path) -> Path:
    path.write_text(json.dumps({"api_version": "26.6.3", "data_version": "26.06",
                                "disease": {"id": "MONDO_0005148",
                                            "name": "type 2 diabetes mellitus",
                                            "dbXRefs": []},
                                "rows": rows}), encoding="utf-8")
    return path


# ------------------------------------------------------------------ the indication overlap
def _disease_snapshot(root: Path, genetic: dict[str, float], literature: dict[str, float]):
    rows = []
    for i, p in enumerate(sorted(set(genetic) | set(literature))):
        types = {}
        if p in genetic:
            types["genetic_association"] = genetic[p]
        if p in literature:
            types["literature"] = literature[p]
        rows.append(ot_row(f"ENSG{i:011d}", p, max(types.values()), **types))
    root.mkdir(parents=True, exist_ok=True)
    path = ot_answer(rows, root / "ot.json")
    result = parse_opentargets(path)
    return build_snapshot(key="opentargets", version="fx", nodes=result.nodes,
                          edges=result.edges, raw_files=result.raw_files, parser="fixture",
                          root=root, license="CC0-1.0", citation="fixture")


def test_the_indication_overlap_is_reported_and_claims_nothing(tmp_path):
    snaps = _build(tmp_path / "w")
    without = run_network_pharmacology(snaps, params=FAST)
    genetic = {p: 0.9 for p in PATHWAY_A[:5]} | {PATHWAY_A[5]: 0.2} | {
        p: 0.8 for p in PROTEINS[20:26]}
    literature = {PATHWAY_A[6]: 0.9, PATHWAY_A[7]: 0.9}
    disease = _disease_snapshot(tmp_path / "ot", genetic, literature)
    result = run_network_pharmacology([*snaps, disease], params=FAST)

    d = result.disease
    assert d["disease"] == "mondo:0005148" and d["evidence"] == "genetic_association"
    # the 0.2 score is under the cut-off; literature-only genes are not disease genes
    assert d["disease_genes"] == 11 and d["overlap"] == 5
    assert [t["target"] for t in d["targets"]] == sorted(f"uniprot:{p}" for p in PATHWAY_A[:5])
    assert 0 < d["p_value"] <= 1 and d["empirical_p"] >= 1 / (FAST.permutations + 1)
    assert "not claimed" in d["interpretation"]
    scores = {t["target"]: t["disease_score"] for t in result.targets}
    assert scores[f"uniprot:{PATHWAY_A[0]}"] == 0.9 and scores[f"uniprot:{PATHWAY_A[6]}"] is None
    claim = result.claims[0]
    assert claim["disease_associated_targets"] == sorted(f"uniprot:{p}" for p in PATHWAY_A[:5])
    # the disease changes neither the pathways, their permutation p-values nor the claims
    assert result.enrichment == without.enrichment
    assert [c["kind"] for c in result.claims] == ["mechanism_hypothesis"]
    assert result.release["released"] == without.release["released"]
    assert any("not a claim" in line for line in result.limitations)
    assert not any("No disease gene set" in line for line in result.limitations)

    loose = run_network_pharmacology([*snaps, disease],
                                     params=replace(FAST, disease_evidence="literature"))
    assert loose.disease["overlap"] == 2
    assert any("circular" in line for line in loose.limitations)


def test_disease_parameters_are_checked():
    with pytest.raises(ValueError, match="disease evidence"):
        Parameters(disease_evidence="gossip")
    with pytest.raises(ValueError, match="disease_min_score"):
        Parameters(disease_min_score=2.0)


# ================================================================ the assayed background
B0, B1 = PROTEINS[8:15], PROTEINS[15:22]


def test_the_assayed_background_is_every_protein_measured_potent_or_not(tmp_path):
    result = run_network_pharmacology(_build(tmp_path), params=replace(FAST,
                                                                       background="assayed"))
    # 9 potent targets, plus the weak and the censored measurement; not the docked one
    assert result.background == {"kind": "assayed", "proteins": 11, "annotated": 57,
                                 "assayed_annotated": 11,
                                 "pathways_tested": len(result.enrichment),
                                 "potent_in_background": 9, "hit_rate": round(9 / 11, 4)}
    rows = {r["pathway"]: r for r in result.enrichment}
    assert rows["reactome:R-HSA-A"]["size"] == 8 and rows["reactome:R-HSA-A"]["in_background"] == 8
    assert "reactome:R-HSA-B2" not in rows           # none of its proteins was assayed
    assert any("assayed proteins" in line and "82%" in line and "1.22-fold" in line
               for line in result.limitations)


def test_a_screening_panel_is_enriched_against_the_annotation_but_not_against_the_assays(
        tmp_path):
    # Two panels tested in full, each with the same hit rate (4 of 7) and nothing else
    # tested: the hits sit in those pathways only because those proteins were screened.
    world = dict(targets_of={0: B0[:4], 1: B1[:4]}, tested_only=B0[4:] + B1[4:])
    snaps = _build(tmp_path, **world)
    whole = run_network_pharmacology(snaps, params=FAST)
    assayed = run_network_pharmacology(snaps, params=replace(FAST, background="assayed"))
    significant = {r["pathway"] for r in whole.enrichment if r["q_value"] <= 0.05}
    assert {"reactome:R-HSA-B0", "reactome:R-HSA-B1"} <= significant
    assert not [r for r in assayed.enrichment if r["q_value"] <= 0.05]
    assert assayed.claims == [] and whole.claims


def test_the_background_parameter_is_checked():
    with pytest.raises(ValueError, match="background"):
        Parameters(background="everything")



# ================================================================ screening hits
def _screening_snapshot(root: Path, active: dict[int, list[str]], tested: list[str]):
    """Every compound tested against every protein in ``tested``; active where listed."""
    root.mkdir(parents=True, exist_ok=True)
    raw = root / "raw.txt"
    raw.write_text("fixture", encoding="utf-8")
    compounds = [f"inchikey:{_inchikey(i)}" for i in range(4)]
    nodes = ([{"id": c, "category": "ingredient", "name": c, "source": "fx"} for c in compounds]
             + [{"id": f"uniprot:{p}", "category": "target", "name": p, "source": "fx"}
                for p in tested])
    edges = []
    for i, c in enumerate(compounds):
        for p in tested:
            hit = p in active.get(i, [])
            edges.append(_edge(f"aid{i}-{p}", c, "targets" if hit else "tested_against",
                               f"uniprot:{p}", "in_vitro", level="observation",
                               publications=[f"pubchem.bioassay:{i + 1}"],
                               outcome="active" if hit else "inactive",
                               assay_type="Confirmatory"))
    return build_snapshot(key="pubchem_bioassay", version="fx", nodes=nodes, edges=edges,
                          raw_files={"raw.txt": raw}, parser="fixture", root=root,
                          license="fixture", citation="fixture")


SCREEN = replace(FAST, hits="screening", screening_min_compounds=4, screening_min_members=3)


def test_screening_hits_are_weighed_against_the_inactive_results(tmp_path):
    snaps = _build(tmp_path / "w")
    tested = PATHWAY_A + PROTEINS[8:36]                     # R-HSA-A and B0..B3, 36 proteins
    # R-HSA-A: every compound active on most of its proteins; elsewhere one compound is
    # active on one protein in each pathway, so every tested protein's pathway has *some*
    # hit and counting hit proteins alone would not tell them apart.
    active = {i: PATHWAY_A[:6] for i in range(4)}
    active[0] = active[0] + [PROTEINS[8 + 7 * k] for k in range(4)]
    screen = _screening_snapshot(tmp_path / "pc", active, tested)
    result = run_network_pharmacology([*snaps, screen], params=SCREEN)

    assert result.background["kind"] == "screening"
    assert result.background["proteins"] == 36
    assert result.background["tests"] == 36 * 4
    assert result.background["active_tests"] == 6 * 4 + 4
    top = result.enrichment[0]
    assert top["pathway"] == "reactome:R-HSA-A" and top["active_tests"] == 24
    assert top["tests"] == 32 and top["q_value"] <= 0.05
    assert [c["object"] for c in result.claims] == ["reactome:R-HSA-A"]
    assert "screening results" in result.claims[0]["statement"]
    cited = {tuple(s) for s in result.claims[0]["support"]}
    assert any(sid == screen.snapshot_id for sid, _ in cited)
    assert not result.release["refused"]
    # the curated measurements are left out of a screening run, and counted
    assert result.excluded["curated measurement (not used for screening hits)"] > 0
    assert any("inactive calls" in line for line in result.limitations)


def test_a_potency_run_leaves_screening_results_out(tmp_path):
    snaps = _build(tmp_path / "w")
    screen = _screening_snapshot(tmp_path / "pc", {0: PROTEINS[40:50]}, PROTEINS[40:50])
    with_screen = run_network_pharmacology([*snaps, screen], params=FAST)
    without = run_network_pharmacology(snaps, params=FAST)
    assert with_screen.enrichment == without.enrichment
    assert with_screen.excluded["screening result (not used for potency hits)"] == 4 * 10


def test_proteins_tested_on_too_few_compounds_are_left_out(tmp_path):
    snaps = _build(tmp_path / "w")
    screen = _screening_snapshot(tmp_path / "pc", {0: PATHWAY_A}, PATHWAY_A)
    result = run_network_pharmacology(
        [*snaps, screen], params=replace(SCREEN, screening_min_compounds=5))
    assert result.enrichment == [] and result.claims == []
    assert result.excluded["proteins tested against too few compounds"] == 8


def test_the_hits_parameter_is_checked():
    with pytest.raises(ValueError, match="hits"):
        Parameters(hits="vibes")
