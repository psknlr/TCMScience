"""The network-pharmacology validation suite (October 2026 audit, §12).

Built on the planted world of test_network_pharmacology: four 葛根芩连汤 compounds hit
pathway A, and one of them was also measured, inactive, against the proteins of the other
pathways, so the hits in A are not merely where the compounds were assayed. A dozen
control herbs are added whose compounds hit random proteins in the other pathways, so
random herb combinations and rewired networks can be compared with the real formula.
Every check re-runs the unchanged analysis.
"""

from __future__ import annotations

import json
import random

import pytest

from bioagent.analysis.network_pharmacology import Parameters, run_network_pharmacology
from bioagent.sources.herbs import GEGEN_QINLIAN, HERBS
from bioagent.sources.herbs import KEY as HERB_KEY, LICENSE as HERB_LICENSE
from bioagent.sources.herbs import herb_rows
from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.materia import all_drugs
from bioagent.sources.snapshot import build_snapshot
from bioagent.studies import validation as V
from test_network_pharmacology import PROTEINS, _edge, _inchikey, _world

pytestmark = pytest.mark.unit

PARAMS = Parameters(permutations=200, background="reactome")
HUANGLIAN = "tcm:herb.huanglian"


def _control_herbs(n: int = 12) -> list[str]:
    gold = set(HERBS)
    drugs = all_drugs()
    return sorted(h for h, d in drugs.items() if h not in gold and d.species)[:n]


def _build(root, ledger=None, *, tested_only=PROTEINS[8:40]):
    (np_nodes, np_edges), (r_nodes, r_edges), (s_nodes, s_edges) = _world(
        tested_only=tested_only)
    drugs = all_drugs()
    rng = random.Random(7)
    for k, herb in enumerate(_control_herbs()):
        species = f"ncbitaxon:{drugs[herb].species[0].taxid}"
        compound = f"inchikey:{_inchikey(100 + k)}"
        np_nodes += [{"id": species, "category": "organism", "name": species, "source": "fx"},
                     {"id": compound, "category": "ingredient", "name": f"ctl{k}",
                      "source": "fx"}]
        np_edges.append(_edge(f"ctl-pair{k}", species, "contains", compound,
                              "chemical_analysis", composition_level="C1",
                              publications=[f"pmid:{500 + k}"]))
        for t in rng.sample(PROTEINS[8:57], 3):
            np_edges.append(_edge(f"ctl-act{k}-{t}", compound, "targets", f"uniprot:{t}",
                                  "in_vitro", publications=["pmid:600"],
                                  measure={"type": "IC50", "relation": "=", "value": 500.0,
                                           "unit": "nM"}))
    raw = root / "raw.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("fixture", encoding="utf-8")
    herb_nodes, herb_edges = herb_rows()
    common = dict(raw_files={"raw.txt": raw}, parser="fixture", root=root, ledger=ledger)
    return [
        build_snapshot(key=HERB_KEY, version="gold", nodes=herb_nodes, edges=herb_edges,
                       license=HERB_LICENSE, citation="fixture", **common),
        build_snapshot(key="npass", version="2.0+fx", nodes=np_nodes, edges=np_edges,
                       license="fixture", citation="fixture", **common),
        build_snapshot(key="reactome", version="current", nodes=r_nodes, edges=r_edges,
                       license="CC0-1.0", citation="fixture", **common),
        build_snapshot(key="string", version="12.0+fx", nodes=s_nodes, edges=s_edges,
                       license="CC-BY-4.0", citation="fixture", **common),
    ]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("np-validation")
    ledger = SnapshotLedger(root / "audit" / "snapshots.jsonl")
    return root, ledger, _build(root / "snap", ledger)


@pytest.fixture(scope="module")
def report(world):
    root, _, snaps = world
    variants = [("without", c[0], V.without_herb(GEGEN_QINLIAN, c[0]))
                for c in GEGEN_QINLIAN.components]
    variants += [("dose", HUANGLIAN, V.with_dose(GEGEN_QINLIAN, HUANGLIAN, "六两")),
                 ("processing", HUANGLIAN, V.with_processing(GEGEN_QINLIAN, HUANGLIAN, "酒炙")),
                 ("with", _control_herbs()[0],
                  V.with_herb(GEGEN_QINLIAN, _control_herbs()[0]))]
    return V.validate(snaps, GEGEN_QINLIAN, PARAMS, root=root / "derived", variants=variants,
                      controls=20, rewirings=10)


def test_the_planted_pathway_is_found(report):
    assert report["significant"] == ["reactome:R-HSA-A"]


def test_each_formula_variant_changes_what_it_should_and_nothing_else(report):
    """拆方 removes the herb's compounds and the targets only they reach; dose and
    processing are recorded but not modelled, so they change nothing; an added herb brings
    its own compounds and removes none."""
    variants = report["variants"]
    assert variants["base_reproduced_on_derived_layer"]
    assert variants["all_identity_ok"], [r["identity"] for r in variants["variants"]]
    by_kind = {(r["kind"], r["herb"]): r for r in variants["variants"]}
    minus = by_kind[("without", HUANGLIAN)]
    assert minus["compounds_lost"] and not minus["compounds_gained"]
    assert minus["targets_lost"]
    for kind in ("dose", "processing"):
        row = by_kind[(kind, HUANGLIAN)]
        assert not (row["compounds_lost"] or row["targets_lost"] or row["significant_lost"])
    added = next(r for r in variants["variants"] if r["kind"] == "with")
    assert added["compounds_gained"] and not added["compounds_lost"]


def test_a_broken_variant_is_caught():
    """If a removed herb's compound were still analysed, identity would fail."""
    class R:
        def __init__(self, compounds, targets):
            self.compounds, self.targets, self.enrichment = compounds, targets, []
    base = R([{"compound": "c1", "herbs": ["h1"]}, {"compound": "c2", "herbs": ["h2"]}],
             [{"target": "t1", "compounds": ["c1"]}])
    leaky = R([{"compound": "c1", "herbs": ["h1"]}], [{"target": "t1", "compounds": ["c1"]}])
    change = V.compare_results(base, leaky, PARAMS)
    ok, why = V._identity("without", "h1", base, leaky, change)
    assert not ok and "still attributed" in why
    ok, why = V._identity("dose", "h1", base, leaky, change)
    assert not ok and "defect" in why


def test_background_thresholds_and_hubs(report):
    assert "reactome:R-HSA-A" in report["background"]["both"]
    assert report["thresholds"]["frequency"]["reactome:R-HSA-A"] == 1.0
    top1 = next(r for r in report["hubs"]["runs"] if r["top"] == 1)
    assert "reactome:R-HSA-A" not in top1["lost"]


def test_a_signal_that_depends_on_the_cut_off_is_flagged(world):
    """At 100 µM the 90 µM 'inactive' measurements count as hits and dilute pathway A;
    a scan that reaches that far shows the signal holds only below it."""
    _, _, snaps = world
    scan = V.threshold_scan(snaps, GEGEN_QINLIAN, PARAMS,
                            grid={"activity_max_nm": (1_000.0, 10_000.0, 100_000.0)})
    assert scan["frequency"]["reactome:R-HSA-A"] == pytest.approx(2 / 3, abs=1e-6)
    lost = [r["setting"] for r in scan["runs"] if "reactome:R-HSA-A" not in r["significant"]]
    assert lost == [{"activity_max_nm": 100_000.0}]


def test_random_formulas_and_rewired_networks_do_not_reach_the_planted_pathway(report):
    rf = report["random_formulas"]
    assert rf["performed"] and rf["controls"] == 20
    assert rf["specificity"]["reactome:R-HSA-A"] == 0.0
    rw = report["rewired"]
    assert rw["still_enriched"]["reactome:R-HSA-A"] <= 0.1
    assert report["verdicts"]["reactome:R-HSA-A"]["robust"], report["verdicts"]


def test_each_check_can_withhold_the_robust_verdict():
    clean = {"significant": ["p"], "background": {"both": ["p"]},
             "thresholds": {"frequency": {"p": 1.0}},
             "random_formulas": {"performed": True, "specificity": {"p": 0.0}},
             "rewired": {"still_enriched": {"p": 0.0}},
             "hubs": {"runs": [{"top": 1, "lost": []}]}}
    assert V.pathway_verdicts(clean)["p"] == {"robust": True, "failed": []}
    for path, value, phrase in (
            (("background", "both"), [], "both backgrounds"),
            (("thresholds", "frequency"), {"p": 0.5}, "three quarters"),
            (("random_formulas", "specificity"), {"p": 0.4}, "random formulas"),
            (("rewired", "still_enriched"), {"p": 0.6}, "rewiring"),
            (("hubs", "runs"), [{"top": 1, "lost": ["p"]}], "most-reached")):
        broken = json.loads(json.dumps(clean))
        broken[path[0]][path[1]] = value
        verdict = V.pathway_verdicts(broken)["p"]
        assert not verdict["robust"] and phrase in " ".join(verdict["failed"])


def test_a_signal_that_only_reflects_what_was_assayed_is_flagged(tmp_path):
    """Without the inactive measurements the compounds were assayed almost only against
    pathway A, so hits in A are what the assay selection predicts: significant against
    the whole annotation, not against what was measured."""
    snaps = _build(tmp_path, tested_only=())
    bg = V.background_sensitivity(snaps, GEGEN_QINLIAN, PARAMS)
    assert "reactome:R-HSA-A" in bg["significant"]["reactome"]
    assert "reactome:R-HSA-A" not in bg["both"]


def test_the_scope_says_what_the_result_is_not(report):
    scope = report["scope"]
    assert scope["model"] == {"in_vitro": 9}
    assert "not a clinical outcome" in scope["endpoint"]
    assert "not modelled" in scope["exposure"]


def test_the_report_renders(report):
    text = V.render_markdown(report)
    assert "## Verdicts" in text and "reactome:R-HSA-A" in text


def test_a_result_reproduces_from_its_bundle(world, tmp_path):
    root, ledger, snaps = world
    result = run_network_pharmacology(snaps, formula=GEGEN_QINLIAN, params=PARAMS)
    bundle = V.reproducibility_bundle(result, snaps, GEGEN_QINLIAN, out_dir=tmp_path,
                                      ledger=ledger)
    ok, why = V.verify_reproduction(bundle, root / "snap", ledger=ledger)
    assert ok, why
    doc = json.loads(bundle.read_text(encoding="utf-8"))
    doc["parameters"]["activity_max_nm"] = 100.0              # a different analysis
    bundle.write_text(json.dumps(doc), encoding="utf-8")
    ok, why = V.verify_reproduction(bundle, root / "snap", ledger=ledger)
    assert not ok and "re-running gives" in why


def test_a_bundle_names_only_real_data(world, tmp_path):
    from dataclasses import replace
    _, _, snaps = world
    result = run_network_pharmacology(snaps, formula=GEGEN_QINLIAN, params=PARAMS)
    derived = [replace(s, snapshot_id=s.snapshot_id + "+derived:x") for s in snaps]
    with pytest.raises(ValueError, match="derived"):
        V.reproducibility_bundle(result, derived, GEGEN_QINLIAN, out_dir=tmp_path)
