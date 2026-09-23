"""The network-pharmacology skill on a small synthetic world with a known answer.

Four herbs of 葛根芩连汤 (the real herb layer), one compound per herb, measured targets
concentrated in one Reactome pathway (``R-HSA-A``) out of a background of 60 proteins: the
pipeline must find that pathway, pass it through the degree-matched null, and release it
as a mechanism hypothesis that cites a real path of snapshot edges.
"""

from __future__ import annotations

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


def _world(targets_of=None):
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


FAST = Parameters(permutations=200)


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
    assert set(provenance["sources_refused"]) == {"cmaup@2.0", "lotus@2026-04-13"}
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
