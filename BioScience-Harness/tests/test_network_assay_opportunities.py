"""Regressions for inference on classifiable, comparable measurements."""
from collections import Counter
from dataclasses import replace
import csv
import random

import pytest
from bioagent.analysis import Parameters, run_network_pharmacology
from bioagent.analysis.network_pharmacology import _potency_call
from bioagent.analysis.np_exports import _enrichment_arithmetic, _tables
from bioagent.sources.herbs import GEGEN_QINLIAN
from bioagent.studies.validation import _rewire, background_sensitivity, threshold_scan
from bioagent.tools.stats import benjamini_hochberg
from test_network_pharmacology import FAST, PATHWAY_A, PROTEINS, SCREEN, _build, _screening_snapshot


@pytest.mark.parametrize("relation,value,unit,expected", [
    ("=", 10, "µM", True), (">", 10, "µM", False), (">=", 10, "µM", None),
    ("<", 10.1, "µM", None), ("<", 10, "µM", True), ("=", 1, "µg/mL", None),
    ("=", -5, "nM", None), ("=", float("nan"), "nM", None), ("~", 9, "µM", None),
])
def test_potency_interval(relation, value, unit, expected):
    assert _potency_call({"type": "IC50", "relation": relation, "value": value,
                          "unit": unit}, Parameters()) is expected


def test_many_assay_opportunities_cannot_make_selectivity(tmp_path):
    herb, natural, react, _ = _build(tmp_path)
    template = next(e for e in natural.edges if e.get("measure"))
    comp_edge = next(e for e in natural.edges if e["predicate"] == "contains")
    compounds = [f"compound:{j}" for j in range(20)]
    edges = [dict(comp_edge, object=c, source_record_id=c) for c in compounds]
    for i, p in enumerate(PROTEINS[:50]):
        # Pathway A: 2/20 hits per protein; other proteins: 4/42 hit once.
        for j in range(20 if i < 8 else 1):
            hit = j < 2 if i < 8 else i in (15, 22, 29, 36)
            edges.append(dict(template, subject=compounds[j], object=f"uniprot:{p}",
                              source_record_id=f"{i}-{j}", measure={
                                  "type": "IC50", "relation": "=", "unit": "nM",
                                  "value": 500 if hit else 50000}))
    params = replace(FAST, background="assayed")
    result = run_network_pharmacology([herb, react, replace(natural, edges=tuple(edges))], params=params)
    a = next(r for r in result.enrichment if r["pathway"] == "reactome:R-HSA-A")
    assert a["q_value"] < .05 and a["empirical_p"] == 1
    assert a["pathway"] not in {c["object"] for c in result.claims}


def _screen_world(tmp_path):
    snaps = _build(tmp_path / "base")
    proteins = PATHWAY_A + PROTEINS[8:36]
    screen = _screening_snapshot(tmp_path / "screen", {j: PATHWAY_A[:6] for j in range(4)}, proteins)
    return snaps, screen


def test_conflicts_are_unknown_and_one_campaign_is_not_many_papers(tmp_path):
    snaps, screen = _screen_world(tmp_path)
    active = next(e for e in screen.edges if e["predicate"] == "targets")
    opposite = dict(active, predicate="tested_against", outcome="inactive", source_record_id="opposite")
    mixed = replace(screen, edges=screen.edges + (opposite,))
    result = run_network_pharmacology(snaps + [mixed], params=SCREEN)
    coverage = result.coverage["screening"]
    assert coverage["measurement_records"] == 145 and coverage["unique_pairs"] == 144
    assert coverage["consensus_pairs"] == 143 and coverage["discordant_pairs"] == 1
    row = next(t for t in result.targets if t["target"] == active["object"])
    assert active["subject"] not in row["compounds"]
    assert row["publications"] == 0 and row["uncited_measurements"] == row["measurements"] == 3
    assert row["assays"] == 3


def test_disjoint_campaigns_with_identical_hit_pattern_cannot_be_compared(tmp_path):
    snaps, screen = _screen_world(tmp_path)
    panel = {"uniprot:" + p for p in PATHWAY_A}
    rows = [dict(e, publications=["pubchem.bioassay:1" if e["object"] in panel else "pubchem.bioassay:2"])
            for e in screen.edges]
    result = run_network_pharmacology(snaps + [replace(screen, edges=tuple(rows))], params=SCREEN)
    a = [r for r in result.coverage["screening"]["untestable_pathways"]
         if r["pathway"] == "reactome:R-HSA-A"]
    assert a and a[0]["empirical_p"] is None and a[0]["tests"] == 32
    assert "reactome:R-HSA-A" not in {r["pathway"] for r in result.enrichment}


def test_screening_table_verifies_from_counts_and_rejects_corruption(tmp_path):
    snaps, screen = _screen_world(tmp_path)
    result = run_network_pharmacology(snaps + [screen], params=SCREEN)
    tabla = tmp_path / "enrichment.tsv"
    tabla.write_text(_tables(result.as_dict())["enrichment.tsv"])
    args = (tmp_path, result.background, result.as_dict()["parameters"])
    assert _enrichment_arithmetic(*args)[0] == "PASS"
    with tabla.open() as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    rows[0]["null_exceedances"] = str(SCREEN.permutations + 1)
    with tabla.open("w") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    assert _enrichment_arithmetic(*args)[0] == "FAIL"


def test_screening_sensitivity_reports_its_applicable_parameters(tmp_path):
    snaps, screen = _screen_world(tmp_path)
    snaps.append(screen)
    assert not background_sensitivity(snaps, GEGEN_QINLIAN, SCREEN)["performed"]
    with pytest.raises(ValueError, match="inactive"):
        threshold_scan(snaps, GEGEN_QINLIAN, SCREEN, grid={"activity_max_nm": [1, 1e8]})
    scan = threshold_scan(snaps, GEGEN_QINLIAN, SCREEN)
    assert "screening_min_compounds" in scan["grid"] and "activity_max_nm" not in scan["grid"]


def test_degree_preserving_swap_keeps_repeated_measurements_together():
    edges = [{"subject": "a", "object": "p", "record": i} for i in (1, 2)]
    edges.append({"subject": "b", "object": "q", "record": 3})
    out = _rewire(edges, random.Random(4), 10)
    pairs = {(e["subject"], e["object"]) for e in out}
    assert len(pairs) == 2 and len(out) == len(edges)
    assert Counter(t for _, t in pairs) == {"p": 1, "q": 1}
    assert {e["record"] for e in out} == {1, 2, 3}


def test_bh_preserves_subthreshold_and_near_threshold_precision():
    assert benjamini_hochberg([1e-16, .05000000001])["q_values"] == [2e-16, .05000000001]
    with pytest.raises(ValueError):
        benjamini_hochberg([float("nan")])


def test_string_membership_is_independent_of_reactome_and_unmapped_is_unknown(tmp_path):
    herb, natural, react, string = _build(tmp_path)
    covered = "uniprot:" + PATHWAY_A[0]
    in_string, outside = ["uniprot:" + p for p in PROTEINS[-2:]]
    template = next(e for e in natural.edges if e.get("measure"))
    natural = replace(natural, edges=natural.edges + tuple(
        dict(template, object=p, source_record_id=f"extra-{p}") for p in [in_string, outside]))
    string = replace(string, nodes=tuple(n for n in string.nodes if n["id"] != outside),
                     edges=string.edges + (dict(string.edges[0], subject=in_string, object=covered,
                                                source_record_id="extra-high-confidence"),))
    result = run_network_pharmacology([herb, natural, react, string], params=FAST)
    targets = {r["target"]: r for r in result.targets}
    assert not targets[in_string]["in_background"] and targets[in_string]["degree"] == 1
    assert targets[outside]["degree"] is None
    assert outside in result.network["measured_targets_outside_snapshot"]
