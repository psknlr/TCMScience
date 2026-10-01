"""Falsifiable study analyses: design records, protocol locking, and the statistics.

Every test uses small hand-made numbers whose answer is known, and needs no network.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from bioagent.studies.combination import Cell, analyse_combination
from bioagent.studies.contrast import Group, contrast_formula_monomer, unexplained_response
from bioagent.studies.design import (AssayResult, ExposureRecord, InterventionSpec,
                                     Measurement, ProtocolError, StudyProtocol)
from bioagent.studies.exposure import exposure_ratio, screen_exposure, to_molar
from bioagent.studies.heterogeneity import Feature, effect_modification
from bioagent.studies.robustness import aid_of, assay_family, group_folds
from bioagent.studies.stats import bh, t_cdf, t_ppf, tost, welch_difference

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------------ statistics
@pytest.mark.parametrize("p,df,q", [(0.975, 10, 2.2281), (0.975, 3, 3.1824),
                                    (0.95, 30, 1.6973), (0.995, 5, 4.0321)])
def test_t_quantiles_match_tables(p, df, q):
    assert t_ppf(p, df) == pytest.approx(q, abs=1e-3)
    assert t_cdf(q, df) == pytest.approx(p, abs=1e-4)


def test_welch_interval_and_bh():
    d = welch_difference([5.1, 4.9, 5.3, 5.0, 5.2], [4.0, 4.2, 3.9, 4.1, 4.3])
    assert d.estimate == pytest.approx(1.0) and d.ci_low > 0
    assert bh([0.01, 0.04, 0.03, 0.2]) == pytest.approx([0.04, 0.0533333, 0.0533333, 0.2])


def test_equivalence_needs_the_interval_inside_the_margin():
    d = welch_difference([1.0, 1.1, 0.9, 1.05, 0.95, 1.02], [1.01, 0.98, 1.03, 0.97, 1.0, 1.0])
    assert tost(d, 0.2)["equivalent"]
    assert not tost(d, 0.01)["equivalent"]
    with pytest.raises(ValueError):
        tost(d, 0)


# --------------------------------------------------------------------------- design
def test_a_formula_name_is_not_an_intervention():
    from bioagent.sources.herbs import GEGEN_QINLIAN
    four = InterventionSpec.from_formula(GEGEN_QINLIAN)
    seven = replace(four, components=four.components + (
        ("tcm:herb.wuweizi", "", "", "", ""), ("tcm:herb.zhimu", "", "", "", ""),
        ("tcm:herb.ganjiang", "", "", "", "")))
    assert four.name == seven.name and not four.same_as(seven)
    assert four.differences(seven)["only_there"] == ["tcm:herb.ganjiang", "tcm:herb.wuweizi",
                                                     "tcm:herb.zhimu"]
    with pytest.raises(ValueError):
        InterventionSpec("compound", "x", (("a", "", "", "", ""), ("b", "", "", "", "")))


def test_censored_values_keep_their_meaning():
    assert not Measurement("below_lod", bound=1.0, unit="ng/mL").known
    with pytest.raises(ValueError):
        Measurement("measured")                       # a measured value needs a value
    with pytest.raises(ValueError):
        Measurement("not_tested", value=0.0)          # "not tested" is never 0
    with pytest.raises(ValueError):
        Measurement("above_max_tested", unit="uM")    # needs the bound


def _protocol():
    return StudyProtocol(
        title="formula vs berberine", question="does the formula add to berberine?",
        hypothesis="the formula lowers fasting glucose more than berberine alone",
        competing=("berberine exposure differs between arms", "dose mismatch"),
        primary_endpoint="fasting_glucose_week8",
        analysis={"model": "welch", "margin": 0.5, "alpha": 0.05},
        decision_rules={"equivalent": "berberine accounts for the effect on this endpoint",
                        "inconclusive": "no conclusion about added effect"})


def test_a_locked_protocol_refuses_a_silent_change_and_an_amendment_is_exploratory():
    p = _protocol().lock()
    assert p.confirmatory
    assert p.check(endpoint="fasting_glucose_week8", analysis=dict(p.analysis)) == "confirmatory"
    with pytest.raises(ProtocolError):
        p.check(endpoint="hba1c_week8", analysis=dict(p.analysis))
    with pytest.raises(ProtocolError):
        p.check(endpoint="fasting_glucose_week8", analysis={**p.analysis, "margin": 1.0})
    amended = p.amend("margin too strict", analysis={**p.analysis, "margin": 1.0})
    assert not amended.confirmatory and amended.amendments[0]["from"] == p.locked
    assert amended.check(endpoint="x", analysis={}) == "exploratory"
    with pytest.raises(ProtocolError):
        replace(_protocol(), competing=()).lock()


# ------------------------------------------------------------------- formula contrast
def test_an_indirect_comparison_across_studies_is_refused():
    with pytest.raises(ValueError):
        contrast_formula_monomer(Group("formula", (1, 2, 3), "s1"),
                                 Group("monomer", (1, 2, 3), "s2"))


def test_no_significant_difference_is_inconclusive_without_a_margin():
    f = Group("formula", (5.0, 6.2, 4.1, 5.9, 4.8, 5.5), "s")
    m = Group("monomer", (5.3, 4.6, 5.8, 4.9, 6.0, 5.1), "s")
    out = contrast_formula_monomer(f, m, higher_is_better=False)
    assert out["verdict"] == "inconclusive"
    assert any("equivalent" in s for s in out["may_not_conclude"])
    out = contrast_formula_monomer(f, m, higher_is_better=False, margin=2.0)
    assert out["verdict"] == "equivalent"


def test_a_real_difference_is_reported_with_its_direction():
    f = Group("formula", (3.0, 3.2, 2.9, 3.1, 3.0, 2.8), "s")
    m = Group("monomer", (5.0, 5.2, 4.9, 5.1, 5.3, 4.8), "s")
    out = contrast_formula_monomer(f, m, higher_is_better=False, dose_matched=True)
    assert out["verdict"] == "formula_exceeds"           # lower glucose is better
    assert "synergy" in " ".join(out["may_not_conclude"])


def test_the_unexplained_response_is_labelled_not_synergy():
    out = unexplained_response(formula=[3.0, 3.1, 2.9, 3.2, 3.0, 3.1],
                               constituents={"berberine": [1.0, 1.1, 0.9, 1.0, 1.05, 0.95]},
                               control=[0.0, 0.1, -0.1, 0.05, 0.0, -0.05])
    assert out["additive_expectation"] == pytest.approx(1.0, abs=0.2)
    assert out["unexplained"]["ci"][0] > 1.0
    assert "not synergy" in out["label"]


# ------------------------------------------------------------------------ combination
def _grid(model: str):
    cells = [Cell(0, 0, (0.0, 0.0, 0.0))]
    for d in (1, 2, 4, 8):
        cells.append(Cell(d, 0, tuple([d / (d + 4)] * 3)))
        cells.append(Cell(0, d, tuple([d / (d + 8)] * 3)))
    for d in (2, 4):
        ea, eb = d / (d + 4), d / (d + 8)
        bliss = ea + eb - ea * eb
        val = bliss + (0.15 if model == "synergy" else 0.0)
        cells.append(Cell(d, d, (val - 0.01, val, val + 0.01),
                          cytotoxicity=(0.9, 0.9, 0.9) if d == 4 else (0.1, 0.1, 0.1)))
    return cells


def test_combination_excess_over_the_prespecified_reference_and_its_selectivity():
    out = analyse_combination(_grid("synergy"), primary="bliss", boot=300)
    assert out["exceeds_reference"] == {"cells": 2, "of": 2, "selective": 1}
    flat = analyse_combination(_grid("additive"), primary="bliss", boot=300)
    assert flat["exceeds_reference"]["cells"] == 0
    assert set(out["sensitivity_cells_above_reference"]) == {"bliss", "hsa", "loewe"}


def test_single_agents_alone_cannot_show_synergy():
    with pytest.raises(ValueError):
        analyse_combination([Cell(1, 0, (0.2, 0.2)), Cell(0, 1, (0.3, 0.3))], primary="bliss")
    with pytest.raises(ValueError):
        analyse_combination([Cell(1, 1, (0.5, 0.5))], primary="bliss")   # no single agents
    with pytest.raises(ValueError):
        analyse_combination(_grid("synergy"), primary="best_of_three")


# --------------------------------------------------------------------------- exposure
def _exposure(value, unit="ng/mL", site="plasma", **kw):
    return ExposureRecord("inchikey:B", "rat", site, 1.0, Measurement("measured", value, unit),
                          **kw)


def _assay(value, unit="uM", endpoint="IC50", qualifier="measured", bound=None):
    m = Measurement(qualifier, value if qualifier == "measured" else None, unit, bound)
    return AssayResult("inchikey:B", "symbol:PTGS2", endpoint, m, species="human")


def test_an_auc_is_not_a_concentration_and_mass_units_need_a_weight():
    with pytest.raises(ValueError):
        to_molar(10, "ng·h/mL", mw=336.4)
    with pytest.raises(ValueError):
        to_molar(10, "ng/mL")
    assert to_molar(336.4, "ng/mL", mw=336.4) == pytest.approx(1e-6)


def test_unknown_free_fraction_gives_a_range_not_an_assumed_one():
    out = exposure_ratio(_exposure(3364.0), _assay(1.0), mw=336.4)   # 10 uM total
    assert out["ratio"] == pytest.approx([0.1, 10.0])
    assert out["verdict"] == "depends_on_unknowns"
    measured = exposure_ratio(_exposure(3364.0, free=True), _assay(1.0), mw=336.4)
    assert measured["verdict"] == "plausible"
    low = exposure_ratio(_exposure(3.364), _assay(10.0), mw=336.4)    # 10 nM vs 10 uM
    assert low["verdict"] == "implausible"


def test_plasma_is_not_tissue_and_parent_is_not_metabolite():
    rows = screen_exposure([_exposure(3364.0)], [_assay(1.0)], site="tissue:UBERON_0002107",
                           mw={"inchikey:B": 336.4})
    assert rows[0]["verdict"] == "undeterminable" and "plasma" in rows[0]["reason"]
    metab = replace(_assay(1.0), molecule="inchikey:M")
    assert exposure_ratio(_exposure(3364.0), metab, mw=336.4)["verdict"] == "undeterminable"


def test_censored_potency_and_exposure_give_bounds_only():
    out = exposure_ratio(_exposure(3364.0, free=True),
                         _assay(None, qualifier="above_max_tested", bound=30.0), mw=336.4)
    assert out["ratio"][0] == 0.0 and out["verdict"] != "plausible"
    inactive = exposure_ratio(_exposure(3364.0), _assay(None, qualifier="inactive"), mw=336.4)
    assert inactive["verdict"] == "inactive_in_assay"


# ---------------------------------------------------------------------- heterogeneity
def test_effect_modification_uses_both_arms_and_baseline_features_only():
    import random
    rng = random.Random(1)
    z = [rng.gauss(0, 1) for _ in range(200)]
    t = [i % 2 for i in range(200)]
    y = [1.0 * ti + 0.2 * zi + 1.5 * ti * zi + rng.gauss(0, 0.5) for ti, zi in zip(t, z)]
    out = effect_modification(y, t, [Feature("akkermansia", tuple(z), baseline=True)])
    inter = out["modifiers"][0]["interaction"]
    assert inter["ci"][0] < 1.5 < inter["ci"][1] and inter["q_value"] < 0.001
    with pytest.raises(ValueError):
        effect_modification(y, [1] * 200, [Feature("z", tuple(z), True)])
    with pytest.raises(ValueError):
        effect_modification(y, t, [Feature("week8_flora", tuple(z), baseline=False)])
    with pytest.raises(ValueError):
        effect_modification(y, t, [Feature("a", tuple(z), True), Feature("b", tuple(z), True)],
                            prespecified=1)


# ------------------------------------------------------------------------ robustness
def test_evidence_is_grouped_by_origin_and_folds_never_split_a_group():
    e = {"source_record_id": "2284394|123|1544||IC50|", "assay_name":
         "TOX21_p450_CYP1A2_Antagonist: Tox21 Cytochrome P450-Glo Antagonism CYP1A2 Assay"}
    assert aid_of(e) == "AID2284394" and assay_family(e) == "cyp_panel"
    assert assay_family({"assay_name": "luciferase reporter qHTS"}) == "luciferase_reporter"
    items = [("pmid:1", i) for i in range(5)] + [("pmid:2", i) for i in range(3)] + \
            [("pmid:3", 0)]
    folds = group_folds(items, lambda x: x[0], 2)
    for f in folds:
        others = [x for g in folds if g is not f for x in g]
        assert not {x[0] for x in f} & {x[0] for x in others}


def test_leave_one_group_out_reports_which_group_carries_a_pathway():
    from types import SimpleNamespace

    from bioagent.studies.robustness import leave_one_group_out

    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Snap:
        key: str
        edges: tuple

    src = Snap(key="screen", edges=tuple({"object": "P1", "g": g} for g in "AAB"))

    def run(snaps, *, formula, params):
        s = next(x for x in snaps if x.key == "screen")
        has_a = any(e["g"] == "A" for e in s.edges)
        return SimpleNamespace(enrichment=[{"pathway": "R1", "name": "R1",
                                            "q_value": 0.01 if has_a else 0.3,
                                            "fold_enrichment": 2.0, "targets": ["P1"]}],
                               release={"released": []})

    out = leave_one_group_out([src], formula=None, params=SimpleNamespace(fdr=0.05),
                              source="screen", group_of=lambda e: e["g"], run=run)
    assert out["pathways"]["R1"]["lost_when_holding_out"] == ["A"]
    assert out["pathways"]["R1"]["verdict"] == "carried by A"


# ---------------------------------------------------------------------- the runner lock
def test_the_runner_refuses_an_ambiguous_or_missing_snapshot():
    from bioagent.analysis.skill_runner import SkillRunRefused, _choose
    recorded = {"a": [("1", "a@1#x")], "b": [("1", "b@1#old"), ("1", "b@1#new")]}
    with pytest.raises(SkillRunRefused):
        _choose(recorded, ["a", "b"], None, latest=False)
    assert _choose(recorded, ["a", "b"], None, latest=True)["b"] == ("1", "b@1#new")
    assert _choose(recorded, ["a", "b"], {"a": "a@1#x", "b": "b@1#old"},
                   latest=False)["b"] == ("1", "b@1#old")
    with pytest.raises(SkillRunRefused):
        _choose(recorded, ["a", "b"], {"a": "a@1#x", "b": "b@1#gone"}, latest=False)
    with pytest.raises(SkillRunRefused):
        _choose(recorded, ["a", "b"], {"a": "a@1#x"}, latest=False)


def test_read_lock_checks_its_schema(tmp_path):
    from bioagent.analysis.skill_runner import SkillRunRefused, read_lock
    p = tmp_path / "snapshot_lock.json"
    p.write_text(json.dumps({"schema": 1, "snapshots": {"a": "a@1#x"}}))
    assert read_lock(p) == {"a": "a@1#x"}
    p.write_text(json.dumps({"a": "a@1#x"}))
    with pytest.raises(SkillRunRefused):
        read_lock(p)
    assert math.isfinite(1.0)


def test_a_version_cannot_be_republished_with_different_content(tmp_path):
    from bioagent.sources.snapshot import SnapshotError, build_snapshot
    raw = tmp_path / "raw.txt"
    raw.write_text("x")
    common = dict(key="fixture", version="1", raw_files={"raw.txt": raw}, parser="p",
                  root=tmp_path / "snap", license="CC0-1.0", citation="doi:10/x")
    nodes = [{"id": "fixture:a", "category": "ingredient", "name": "a", "source": "fixture"}]
    first = build_snapshot(nodes=nodes, edges=[], **common)
    again = build_snapshot(nodes=nodes, edges=[], **common)          # same content: fine
    assert again.snapshot_id == first.snapshot_id
    with pytest.raises(SnapshotError, match="already holds"):
        build_snapshot(nodes=nodes + [{"id": "fixture:b", "category": "ingredient", "name": "b",
                                "source": "fixture"}],
                       edges=[], **common)
