"""Study design for bioinformatics: contracts, gate, audit, subject-level analyses, faults."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from bioagent.studies.audit import (apply_audit, audit_study, check_batch_confounding,
                                    check_coverage, check_duplicates, check_identity_consistency,
                                    check_independent_units, check_intervention_identity,
                                    check_leakage, check_outcome, check_pairing,
                                    check_sufficiency, check_test_families,
                                    near_duplicate_profiles, pairing_consistency)
from bioagent.studies.contract import (QUESTION_TYPES, ClaimRecord, CohortManifest,
                                       ContrastSpec, OmicsArtifact, OriginalDesign, Sample,
                                       StudySpec, ValidationPlan, assess_bridge, contrasts_tsv,
                                       read_contrasts)
from bioagent.studies.faults import (EXPECTED_CHECK, FAULTS, gated_pipeline, hypergeom_sf,
                                     inject, make_study, naive_pipeline, run_benchmark)
from bioagent.studies.gate import design_gate
from bioagent.studies.longitudinal import match_layers, next_visit_prediction, visit_pairs
from bioagent.studies.power import simulate_power
from bioagent.studies.singlecell import (composition_test, decompose_bulk, pseudobulk,
                                         state_test)
from bioagent.studies.skills import STUDY_SKILLS, skill
from bioagent.studies.spatial import (compare_neighbourhoods, detectable_genes,
                                      neighbour_pairs, neighbourhood_enrichment)
from bioagent.studies.validation import (assert_disjoint, auc, grouped_cv, grouped_folds,
                                         sample_split_cv)
from bioagent.studies.workspace import create_workspace, verify_workspace, write_claims


def _spec(**kw):
    base = dict(id="s", title="t", question="q", question_type="composition_vs_state",
                chain="disease", hypothesis="h", falsifier="f", competing=("c",),
                primary_contrast="c1")
    base.update(kw)
    return StudySpec(**base)


def _manifest(n=6, batch=lambda arm, i: str(i % 2), **attrs):
    return CohortManifest(Sample(f"{arm}{i}", f"{arm}{i}", "d", condition=arm,
                                 batch=batch(arm, i), attrs=dict(attrs))
                          for arm in ("UC", "HC") for i in range(n))


C1 = ContrastSpec("c1", "condition", "UC", "HC")
CELLS = [OmicsArtifact("cells", "scrna", level="cell", annotations=("cell_type",))]


# Contracts -------------------------------------------------------------------------------

def test_manifest_roundtrip_and_refusals():
    m = _manifest(3, extra="x")
    again = CohortManifest.from_tsv(m.to_tsv())
    assert again.to_tsv() == m.to_tsv() and again.fingerprint() == m.fingerprint()
    with pytest.raises(ValueError, match="repeat"):
        CohortManifest([Sample("a", "s", "d"), Sample("a", "s", "d")])
    with pytest.raises(ValueError, match="roles"):
        CohortManifest([Sample("a", "s", "d", role="training")])


def test_contrast_rows_roundtrip():
    c = ContrastSpec("x", "group", "A", "B", paired=True, family="sec",
                     where={"tissue": "colon"}, covariates=("age",))
    assert read_contrasts(contrasts_tsv([c])) == [c]


def test_artifact_panel_needs_features_and_spec_needs_falsifier():
    with pytest.raises(ValueError, match="panel"):
        OmicsArtifact("p", "spatial", feature_space="targeted_panel")
    with pytest.raises(ValueError, match="refute"):
        _spec(falsifier="")
    with pytest.raises(ValueError, match="question type"):
        _spec(question_type="network_pharmacology")


def test_claim_cap_and_bridge():
    c = ClaimRecord("a", "x", "disease", "association")
    assert c.cap("causal", "no").level == "association"       # a cap never raises
    d = c.cap("exploratory", "why")
    assert d.level == "exploratory" and d.downgrades[0]["reason"] == "why"
    disease = ClaimRecord("d", "UC epithelium lowers X", "disease", "validated_association")
    assert assess_bridge("herb acts via X", disease, None).level == "not_supported"
    weak = ClaimRecord("i", "constituent binds X (docking)", "intervention", "association")
    assert assess_bridge("herb acts via X", disease, weak).level == "association"
    exp = ClaimRecord("i", "constituent lowers X at reached exposure", "intervention",
                      "experimentally_supported")
    assert assess_bridge("herb acts via X", disease, exp).level == "validated_association"


def test_original_design_ceiling():
    spec = _spec(original=(OriginalDesign("G", "cohort"),))
    claim = ClaimRecord("c", "treatment causes x", "disease", "causal")
    from bioagent.studies.audit import AuditReport
    capped = apply_audit(claim, AuditReport(), spec)
    assert capped.level == "validated_association"
    rct = _spec(original=(OriginalDesign("G", "rct", randomized=True),))
    assert apply_audit(claim, AuditReport(), rct).level == "causal"


# Gate ---------------------------------------------------------------------------------------

def test_gate_composition_needs_cell_types_and_people():
    m = _manifest(6)
    assert design_gate(_spec(), m, [C1], CELLS).status == "answerable"
    no_labels = [OmicsArtifact("cells", "scrna", level="cell")]
    assert design_gate(_spec(), m, [C1], no_labels).status == "not_answerable"
    assert design_gate(_spec(), _manifest(3), [C1], CELLS).status == "downgraded"
    assert design_gate(_spec(), _manifest(1), [C1], CELLS).status == "not_answerable"


def test_gate_isoform_and_microbial():
    m = _manifest(6)
    iso = _spec(question_type="isoform")
    g = design_gate(iso, m, [C1], [OmicsArtifact("x", "scrna", level="cell",
                                                 protocol="10x 3' v3")])
    assert g.status == "not_answerable" and any("end-tag" in n for n in g.notes)
    mic = _spec(question_type="microbial_function")
    unpaired = [OmicsArtifact("g", "metagenome", samples=("s1",)),
                OmicsArtifact("t", "metatranscriptome", samples=("s2",))]
    assert design_gate(mic, m, [C1], unpaired).status == "not_answerable"
    paired = [OmicsArtifact("g", "metagenome", samples=("s1",)),
              OmicsArtifact("t", "metatranscriptome", samples=("s1",))]
    assert design_gate(mic, m, [C1], paired).answerable


def test_gate_inflammation_needs_severity():
    spec = _spec(question_type="inflammation_vs_repair")
    assert not design_gate(spec, _manifest(6), [C1], []).answerable
    assert design_gate(spec, _manifest(6, mayo_endoscopic="2"), [C1], []).answerable


# Audit ---------------------------------------------------------------------------------------

def test_independent_units():
    m = _manifest(6)
    cell_contrast = replace(C1, unit="cell_id")
    f = check_independent_units(m, [cell_contrast], CELLS)
    assert f and f[0].severity == "stop"
    missing = CohortManifest([Sample("a", "", "d")])
    assert check_independent_units(missing, [C1])[0].severity == "stop"
    pooled = CohortManifest([Sample("lane1", "lane1", "G")])
    assert check_independent_units(pooled, [C1], pooled_datasets=("G",))[0].severity == "stop"
    assert check_independent_units(m, [C1], CELLS) == []


def test_identity_consistency():
    m = CohortManifest([Sample("a", "s1", "d", attrs={"x": "1", "y": "2"}),
                        Sample("b", "s2", "d", attrs={"x": "3", "y": "3"})])
    f = check_identity_consistency(m, ("x", "y"))
    assert len(f) == 1 and f[0].evidence["sample"] == "a"


def test_batch_confounding_levels():
    clean = _manifest(6)
    assert check_batch_confounding(clean, C1) == []
    total = _manifest(6, batch=lambda arm, i: arm)
    assert check_batch_confounding(total, C1)[0].severity == "stop"
    strong = _manifest(6, batch=lambda arm, i: arm if i else ("HC" if arm == "UC" else "UC"))
    f = check_batch_confounding(strong, C1, strong=0.5)
    assert f and f[0].severity == "downgrade"


def test_batch_confounding_counts_people_not_samples():
    # one HC person with 20 samples in batch A must not look like 20 controls
    s = [Sample(f"u{i}", f"U{i}", "d", condition="UC", batch="A") for i in range(5)]
    s += [Sample(f"h{i}", "H0", "d", condition="HC", batch="A") for i in range(20)]
    s += [Sample(f"g{i}", f"H{i+1}", "d", condition="HC", batch="B") for i in range(5)]
    f = check_batch_confounding(CohortManifest(s), C1, strong=0.5)
    table = f[0].evidence["table"]
    assert table["A"] == {"UC": 5, "HC": 1}


def test_leakage_and_duplicates():
    m = CohortManifest([Sample("a", "p1", "D", role="discovery"),
                        Sample("b", "p1", "V", role="validation")])
    plan = ValidationPlan(discovery=("D",), validation=("V",), frozen_selection="x")
    assert check_leakage(m, plan)[0].severity == "stop"
    plan2 = ValidationPlan(discovery=("D",), validation=("V",), selection_datasets=("V",))
    msgs = [f.message for f in check_leakage(CohortManifest([]), plan2)]
    assert any("used to select" in x for x in msgs) and any("frozen" in x for x in msgs)
    dup = CohortManifest([Sample("a", "p1", "D", original_study="GSE1"),
                          Sample("b", "p2", "V", original_study="GSE1")])
    assert check_duplicates(dup, plan)[0].severity == "stop"
    same = CohortManifest([Sample("a", "p1", "D", attrs={"source_sample": "GSM9"}),
                           Sample("b", "p2", "V", attrs={"source_sample": "GSM9"})])
    assert check_duplicates(same)[0].check == "duplicate_studies"


def test_near_duplicate_profiles():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(4, 200))
    x[3] = x[1] + rng.normal(0, 0.01, 200)
    pairs = near_duplicate_profiles(x, ["a", "b", "c", "d"])
    assert [(p[0], p[1]) for p in pairs] == [("b", "d")]


def test_coverage_panel_and_predicted():
    panel = OmicsArtifact("p", "spatial", feature_space="targeted_panel",
                          features=("A", "B"))
    pred = OmicsArtifact("dock", "structure", provenance="predicted")
    f = check_coverage([panel, pred], [{"artifact": "p", "background": "genome"},
                                       {"artifact": "p", "claims": "transcriptome_wide"},
                                       {"artifact": "dock", "needs_measured": True},
                                       {"artifact": "p", "background": ["A"]}])
    assert len(f) == 3 and all(x.severity == "stop" for x in f)
    assert panel.background() == ("A", "B")


def test_outcome_families_sufficiency():
    m = _manifest(6, response="R")
    assert check_outcome(m, "response")[0].severity == "stop"
    assert check_outcome(m, "missing")[0].severity == "stop"
    plan = ValidationPlan(discovery=("d",), families={"primary": ("c1",)})
    assert check_test_families(plan, ["c1"]) == []
    assert check_test_families(plan, ["c1", "c9"])[0].severity == "downgrade"
    assert check_sufficiency(_manifest(2), C1)[0].severity == "stop"
    assert check_sufficiency(_manifest(4), C1)[0].severity == "downgrade"
    assert check_sufficiency(_manifest(6), C1) == []


def test_pairing_checks():
    paired = ContrastSpec("p", "condition", "post", "pre", paired=True)
    m = CohortManifest(Sample(f"S{i}_{t}", f"S{i}", "d", condition=t)
                       for i in range(5) for t in ("pre", "post"))
    assert check_pairing(m, paired) == []
    broken = CohortManifest(list(m)[:-3])
    assert any(f.severity in ("warn", "stop") for f in check_pairing(broken, paired))
    rng = np.random.default_rng(1)
    ids, feats = [], []
    for i in range(10):
        person = rng.normal(size=6)
        for t in ("pre", "post"):
            ids.append(f"S{i}_{t}")
            feats.append(person + rng.normal(0, 0.1, 6))
    mm = CohortManifest(Sample(x, x.split("_")[0], "d", condition=x.split("_")[1]) for x in ids)
    assert pairing_consistency(np.array(feats), ids, mm, paired) == []
    order = rng.permutation(10)
    shuffled = CohortManifest(Sample(x, f"S{order[int(x[1:].split('_')[0])]}"
                                     if x.endswith("post") else x.split("_")[0], "d",
                                     condition=x.split("_")[1]) for x in ids)
    f = pairing_consistency(np.array(feats), ids, shuffled, paired)
    assert f and f[0].severity == "stop"


def test_intervention_identity():
    m = CohortManifest([Sample("a", "1", "d", attrs={"intervention": "GQD",
                                                     "intervention_fp": "fp4"}),
                        Sample("b", "2", "d", attrs={"intervention": "GQD",
                                                     "intervention_fp": "fp7"})])
    assert check_intervention_identity(m)[0].severity == "stop"


def test_audit_study_and_apply():
    m = _manifest(6)
    plan = ValidationPlan(discovery=("d",), families={"primary": ("c1",)})
    rep = audit_study(_spec(), m, [C1], CELLS, plan)
    assert rep.verdict == "proceed" and "leakage" in rep.checks_run
    bad = audit_study(_spec(), _manifest(6, batch=lambda a, i: a), [C1], CELLS, plan)
    assert bad.verdict == "stop" and "stop" in bad.to_markdown()
    claim = apply_audit(ClaimRecord("x", "y", "disease", "association"), bad)
    assert claim.level == "not_supported"


# Single cell --------------------------------------------------------------------------------

def _cells(shift_comp=0.0, shift_state=0.0, n=6, m=300, seed=0):
    rng = np.random.default_rng(seed)
    cs, ct, ex, counts = [], [], [], []
    samples = []
    for arm in ("UC", "HC"):
        for i in range(n):
            sid = f"{arm}{i}"
            samples.append(Sample(sid, sid, "d", condition=arm, batch=str(i % 2)))
            p_epi = 0.5 - (shift_comp if arm == "UC" else 0) + rng.normal(0, 0.03)
            u = rng.normal(0, 0.1)
            for _ in range(m):
                t = "epi" if rng.random() < p_epi else "imm"
                mu = (2.0 if t == "epi" else 0.2) + u
                if t == "epi" and arm == "UC":
                    mu += shift_state
                cs.append(sid)
                ct.append(t)
                ex.append(rng.normal(mu, 0.3))
                counts.append(rng.poisson(np.exp([mu, 1.0, 1.5])))
    return CohortManifest(samples), cs, ct, np.array(ex), np.array(counts)


def test_pseudobulk_sums():
    counts = np.array([[1, 2], [3, 4], [5, 6]])
    pb = pseudobulk(counts, ["a", "a", "b"], ["x", "x", "x"], ["g1", "g2"], min_cells=1)
    assert pb.keys == (("a", "x"), ("b", "x"))
    assert pb.counts.tolist() == [[4, 6], [5, 6]] and pb.n_cells.tolist() == [2, 1]
    assert pseudobulk(counts, ["a", "a", "b"], ["x"] * 3, ["g1", "g2"], min_cells=2).keys == \
        (("a", "x"),)


def test_composition_detects_shift_per_subject():
    m, cs, ct, _, _ = _cells(shift_comp=0.25)
    r = composition_test(cs, ct, m, C1, n_perm=2000)
    epi = next(x for x in r["cell_types"] if x["cell_type"] == "epi")
    assert epi["clr_difference"] < 0 and epi["p_perm"] < 0.01
    assert r["units"] == {"UC": 6, "HC": 6}
    m0, cs0, ct0, _, _ = _cells(shift_comp=0.0, seed=3)
    r0 = composition_test(cs0, ct0, m0, C1, n_perm=2000)
    assert all(x["q_perm"] > 0.05 for x in r0["cell_types"])


def test_decompose_separates_composition_and_state():
    m, cs, ct, ex, _ = _cells(shift_comp=0.25, shift_state=0.0, seed=1)
    d = decompose_bulk(ex, cs, ct, m, C1, n_boot=500)
    assert d["verdict"] == "composition"
    m, cs, ct, ex, _ = _cells(shift_comp=0.0, shift_state=0.8, seed=2)
    d = decompose_bulk(ex, cs, ct, m, C1, n_boot=500)
    assert d["verdict"] == "state"
    assert math.isclose(d["difference"], d["composition"]["estimate"] + d["state"]["estimate"])
    assert abs(d["difference"] + d["within_arm_covariance"] - d["difference_observed"]) < 1e-9


def test_state_test_is_per_subject():
    m, cs, ct, _, counts = _cells(shift_state=1.0, seed=4)
    pb = pseudobulk(counts, cs, ct, ["g0", "g1", "g2"], min_cells=5)
    r = state_test(pb, m, C1, "epi", genes=["g0"])
    assert r["units"] == {"UC": 6, "HC": 6}
    assert r["genes"][0]["log2_difference"] > 0 and r["genes"][0]["p_value"] < 0.01


# Spatial ------------------------------------------------------------------------------------

def test_neighbour_pairs_match_brute_force():
    rng = np.random.default_rng(0)
    xy = rng.uniform(0, 20, (150, 2))
    fast = {tuple(p) for p in neighbour_pairs(xy, 2.0).tolist()}
    d = np.linalg.norm(xy[:, None] - xy[None], axis=-1)
    brute = {(i, j) for i in range(150) for j in range(150) if i != j and d[i, j] < 2.0}
    assert fast == brute


def test_neighbourhood_enrichment_and_subject_comparison():
    rng = np.random.default_rng(0)
    per_sample, samples = {}, []
    for arm in ("UC", "HC"):
        for i in range(5):
            xy = rng.uniform(0, 60, (400, 2))
            if arm == "UC":
                lab = np.where(xy[:, 0] < 30, "a", "b")       # segregated
            else:
                lab = rng.choice(["a", "b"], 400)               # mixed
            sid = f"{arm}{i}"
            samples.append(Sample(sid, sid, "d", condition=arm))
            per_sample[sid] = neighbourhood_enrichment(xy, lab, "a", "a", radius=4,
                                                       n_perm=100, seed=i)
    r = compare_neighbourhoods(per_sample, CohortManifest(samples), C1, n_perm=500)
    assert r["difference_log2_ratio"] > 0.3 and r["subjects"] == {"UC": 5, "HC": 5}
    assert detectable_genes(np.array([[0, 1], [0, 0], [0, 2]]), ["x", "y"],
                            min_fraction=0.5) == ["y"]


# Validation ---------------------------------------------------------------------------------

def test_auc_and_folds():
    assert auc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == 0.75
    assert auc([0, 1], [0.5, 0.5]) == 0.5
    groups = [f"s{i // 3}" for i in range(30)]
    folds = grouped_folds(groups, 5)
    for f in folds:
        train = np.setdiff1d(np.arange(30), f)
        assert_disjoint(train, f, groups)
    with pytest.raises(ValueError, match="both training and test"):
        assert_disjoint(np.array([0, 1]), np.array([2]), ["a", "b", "a"])


def test_sample_split_inflates_where_subject_split_does_not():
    rng = np.random.default_rng(0)
    X, y, g = [], [], []
    for s in range(24):
        person = rng.normal(0, 1, 20)
        label = s % 2
        for _ in range(8):
            X.append(person + rng.normal(0, 0.2, 20))
            y.append(label)
            g.append(f"s{s}")
    honest = grouped_cv(np.array(X), y, g, k=4, n_perm=30, seed=1)
    leaky = sample_split_cv(np.array(X), y, k=4, seed=1)
    assert leaky["estimate"] > 0.9
    assert honest["estimate"] < 0.75 and honest["p_perm"] > 0.05


# Longitudinal -------------------------------------------------------------------------------

def _visits(seed=0, signal=True):
    rng = np.random.default_rng(seed)
    samples, feats, outc = [], {}, {}
    for s in range(30):
        state = rng.normal()
        for v in range(6):
            sid = f"S{s}_v{v}"
            samples.append(Sample(sid, f"S{s}", "d", attrs={"day": str(v * 14)}))
            driver = rng.normal()
            feats[sid] = [driver, rng.normal()]
            outc[sid] = state + rng.normal(0, 0.3)
    # the outcome at v+1 depends on the driver at v
    for s in range(30):
        for v in range(5):
            if signal:
                outc[f"S{s}_v{v + 1}"] += 1.5 * feats[f"S{s}_v{v}"][0]
    return CohortManifest(samples), feats, outc


def test_visit_pairs_respect_window():
    m, _, _ = _visits()
    r = visit_pairs(m, window=(7, 21))
    assert len(r["pairs"]) == 150 and r["subjects"] == 30
    assert visit_pairs(m, window=(1, 7))["pairs"] == []


def test_next_visit_against_persistence():
    m, f, o = _visits(signal=True)
    pairs = visit_pairs(m, window=(7, 21))["pairs"]
    r = next_visit_prediction(f, o, pairs, task="continuous", k=5, n_boot=200)
    assert r["verdict"] == "beats both baselines" and r["split"] == "by subject"
    m, f, o = _visits(signal=False, seed=1)
    pairs = visit_pairs(m, window=(7, 21))["pairs"]
    r = next_visit_prediction(f, o, pairs, task="continuous", k=5, n_boot=200)
    assert r["verdict"] != "beats both baselines"


def test_match_layers_same_collection():
    s = [Sample("g1", "P1", "d", attrs={"data_type": "MGX", "collection": "c1", "day": "0"}),
         Sample("t1", "P1", "d", attrs={"data_type": "MTX", "collection": "c1", "day": "0"}),
         Sample("g2", "P2", "d", attrs={"data_type": "MGX", "collection": "c2", "day": "0"}),
         Sample("t2", "P2", "d", attrs={"data_type": "MTX", "collection": "c3", "day": "10"})]
    r = match_layers(CohortManifest(s), ["MGX", "MTX"], tolerance_days=14)
    assert r["collections_with_all_layers"] == 1 and r["subjects_with_all_layers"] == 2
    assert r["anchor_samples_matched_within_14_days"] == 2


# Power --------------------------------------------------------------------------------------

def test_power_cells_do_not_replace_people():
    null = simulate_power(4, 500, effect=0, sd_subject=1, sd_cell=2, n_sim=600, seed=1)
    assert null["subject_level_rejection"] < 0.09
    assert null["cell_level_rejection"] > 0.5
    few = simulate_power(4, 100, effect=1, sd_subject=1, sd_cell=2, n_sim=600, seed=2)
    many_cells = simulate_power(4, 5000, effect=1, sd_subject=1, sd_cell=2, n_sim=600, seed=2)
    many_people = simulate_power(16, 100, effect=1, sd_subject=1, sd_cell=2, n_sim=600, seed=2)
    assert abs(many_cells["subject_level_rejection"] - few["subject_level_rejection"]) < 0.08
    assert many_people["subject_level_rejection"] > few["subject_level_rejection"] + 0.3


# Fault-injection benchmark ------------------------------------------------------------------

def test_hypergeom_sf():
    assert math.isclose(hypergeom_sf(0, 50, 10, 5), 1.0)
    assert hypergeom_sf(6, 50, 10, 5) == 0.0
    exact = sum(math.comb(10, i) * math.comb(40, 5 - i) for i in range(3, 6)) / math.comb(50, 5)
    assert math.isclose(hypergeom_sf(3, 50, 10, 5), exact, rel_tol=1e-9)


def test_each_fault_is_caught_by_its_check():
    for fault in FAULTS:
        base = make_study("null", seed=11, paired=(fault == "shuffled_pairing"))
        out = gated_pipeline(inject(base, fault, seed=5))
        assert EXPECTED_CHECK[fault] in out["flags"], fault
        assert out["claim"] is False
        assert naive_pipeline(inject(base, fault, seed=5))["flags"] == []


def test_clean_studies_raise_no_alarm():
    for paired in (False, True):
        out = gated_pipeline(make_study("null", seed=3, paired=paired))
        assert out["flags"] == []
    assert gated_pipeline(make_study("positive", seed=4))["claim"] is True


def test_benchmark_summary():
    r = run_benchmark({"naive": naive_pipeline, "gated": gated_pipeline}, n_rep=3, seed=2)
    g, n = r["pipelines"]["gated"], r["pipelines"]["naive"]
    assert g["false_alarm_rate"] == 0 and g["detection_rate"] >= 0.9
    assert g["spurious_claim_rate_under_faults"] == 0
    assert n["detection_rate"] == 0 and n["spurious_claim_rate_under_faults"] > 0.3


# Workspace and skills -----------------------------------------------------------------------

def test_workspace_layout_and_verification(tmp_path):
    m = _manifest(6)
    plan = ValidationPlan(discovery=("d",), families={"primary": ("c1",)})
    spec = _spec(original=(OriginalDesign("G", "case_control", pooled=True,
                                          notes=("lanes",)),))
    rep = audit_study(spec, m, [C1], CELLS, plan)
    gate = design_gate(spec, m, [C1], CELLS)
    root = create_workspace(tmp_path / "st", spec, m, [C1], plan, CELLS, gate=gate,
                            audit=rep, snapshot_lock={"geo": "GSE1#abc"})
    for name in ("protocol.yaml", "cohort_manifest.tsv", "contrasts.tsv", "snapshot_lock.json",
                 "environment.lock", "claims.json", "provenance.json", "limitations.md",
                 "qc/audit.json", "qc/design_gate.json"):
        assert (root / name).exists(), name
    for d in ("discovery", "validation", "sensitivity"):
        assert (root / d).is_dir()
    assert verify_workspace(root)["status"] == "as_registered"
    assert "pooled libraries" in (root / "limitations.md").read_text()
    write_claims(root, [ClaimRecord("a", "b", "disease", "exploratory")])
    assert json.loads((root / "claims.json").read_text())[0]["level"] == "exploratory"
    (root / "cohort_manifest.tsv").write_text(_manifest(5).to_tsv())
    v = verify_workspace(root)
    assert v["status"] == "exploratory" and v["changed"] == ["cohort_manifest.tsv"]
    with pytest.raises(FileExistsError):
        create_workspace(root, spec, m, [C1], plan)


def test_study_skills_are_consistent():
    assert len(STUDY_SKILLS) == 9
    for s in STUDY_SKILLS.values():
        assert s.question_type in QUESTION_TYPES and s.id.startswith("bio.")
    assert not skill("bio.isoform-context").implemented
    with pytest.raises(KeyError):
        skill("bio.unknown")


# Regressions from the independent review ---------------------------------------------------

def test_paired_neighbourhoods_compare_each_subject_with_itself():
    rng = np.random.default_rng(0)
    per, samples = {}, []
    for i in range(6):
        base = rng.normal(0, 3)                 # large between-subject spread
        for lvl, shift in (("pre", 0.0), ("post", 0.5)):
            sid = f"S{i}_{lvl}"
            samples.append(Sample(sid, f"S{i}", "d", condition=lvl))
            per[sid] = {"log2_ratio": base + shift + rng.normal(0, 0.1)}
    c = ContrastSpec("p", "condition", "post", "pre", paired=True)
    r = compare_neighbourhoods(per, CohortManifest(samples), c)
    assert r["paired"] and r["pairs"] == 6
    assert r["ci"][0] > 0 and r["p_perm"] <= 0.05


def test_next_visit_needs_to_beat_the_mean_too():
    # low autocorrelation: persistence is a poor baseline, a noise model must not "win"
    rng = np.random.default_rng(3)
    samples, feats, outc = [], {}, {}
    for s in range(40):
        y = rng.normal()
        for v in range(6):
            sid = f"S{s}_v{v}"
            samples.append(Sample(sid, f"S{s}", "d", attrs={"day": str(v * 14)}))
            y = 0.2 * y + rng.normal()
            outc[sid] = y
            feats[sid] = list(rng.normal(size=3))
    pairs = visit_pairs(CohortManifest(samples), window=(7, 21))["pairs"]
    r = next_visit_prediction(feats, outc, pairs, task="continuous", n_boot=200)
    assert r["persistence"] < r["mean_baseline"]
    assert r["verdict"] != "beats both baselines"


def test_pairing_consistency_with_three_pairs_warns_instead_of_stopping():
    paired = ContrastSpec("p", "condition", "post", "pre", paired=True)
    rng = np.random.default_rng(0)
    ids, feats = [], []
    for i in range(3):
        person = rng.normal(size=6) * 3
        for t in ("pre", "post"):
            ids.append(f"S{i}_{t}")
            feats.append(person + rng.normal(0, 0.01, 6))
    m = CohortManifest(Sample(x, x.split("_")[0], "d", condition=x.split("_")[1]) for x in ids)
    f = pairing_consistency(np.array(feats), ids, m, paired)
    assert f and f[0].severity == "warn" and "too few" in f[0].message


def test_coverage_with_panel_size_only_and_named_backgrounds():
    panel = OmicsArtifact("p", "spatial", feature_space="targeted_panel", n_features=300)
    assert check_coverage([panel], [{"artifact": "p",
                                     "background": [f"g{i}" for i in range(20000)]}])
    assert check_coverage([panel], [{"artifact": "p", "background": "transcriptome"}])
    assert check_coverage([panel], [{"artifact": "p", "background": "panel"}]) == []


def test_decompose_reports_the_true_subject_difference():
    m, cs, ct, ex, _ = _cells(shift_state=0.5, seed=5)
    # a rare third type, below min_cells in every subject
    cs, ct, ex = list(cs), list(ct), list(ex)
    for s in m:
        for _ in range(2):
            cs.append(s.sample_id)
            ct.append("rare")
            ex.append(9.0)
    ex = np.array(ex)
    d = decompose_bulk(ex, cs, ct, m, C1, n_boot=300)
    tissue = {}
    for v, smp in zip(ex, cs):
        tissue.setdefault(smp, []).append(v)
    truth = (np.mean([np.mean(tissue[s.sample_id]) for s in m if s.condition == "UC"])
             - np.mean([np.mean(tissue[s.sample_id]) for s in m if s.condition == "HC"]))
    assert math.isclose(d["difference_observed"], truth, rel_tol=1e-9)
    total = d["difference"] + d["within_arm_covariance"] + d["sparse_cell_remainder"]
    assert math.isclose(total, truth, rel_tol=1e-9)


def test_decompose_verdict_is_calibrated_under_the_null():
    hits = 0
    for r in range(40):
        m, cs, ct, ex, _ = _cells(seed=100 + r, n=4, m=150)
        hits += decompose_bulk(ex, cs, ct, m, C1, n_boot=200, seed=r)["verdict"] != "unresolved"
    assert hits <= 5


def test_small_paired_composition_flags_that_it_cannot_reject():
    samples, cs, ct = [], [], []
    rng = np.random.default_rng(0)
    for i in range(4):
        for lvl, p in (("pre", 0.5), ("post", 0.2)):
            sid = f"S{i}_{lvl}"
            samples.append(Sample(sid, f"S{i}", "d", condition=lvl))
            for _ in range(200):
                cs.append(sid)
                ct.append("epi" if rng.random() < p else "imm")
    c = ContrastSpec("p", "condition", "post", "pre", paired=True)
    r = composition_test(cs, ct, CohortManifest(samples), c)
    assert r["permutation"]["exact"] and r["permutation"]["min_attainable_p"] == 0.125
    assert not r["permutation"]["can_reject_at_0.05"] and "cannot reject" in r["warning"]


def test_exact_permutation_helpers():
    from bioagent.studies.stats import permutation_p, signflip_p
    r = permutation_p([10, 11, 12], [1, 2, 3])
    assert r["exact"] and math.isclose(r["p"], 0.1) and math.isclose(r["min_p"], 0.1)
    s = signflip_p([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert s["exact"] and math.isclose(s["p"], 2 / 64)


def test_batch_nested_in_subject_is_not_a_stop():
    s = [Sample(f"{a}{i}", f"{a}{i}", "d", condition=a, batch=f"run_{a}{i}")
         for a in ("UC", "HC") for i in range(10)]
    f = check_batch_confounding(CohortManifest(s), C1)
    assert f and f[0].severity == "warn"


def test_leakage_respects_explicit_roles():
    m = CohortManifest([Sample("a", "p1", "D", role="validation"),
                        Sample("b", "p2", "D", role="discovery")])
    plan = ValidationPlan(discovery=("D",), validation=(), frozen_selection="x")
    assert check_leakage(m, plan) == []
