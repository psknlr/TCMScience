"""An isolation record is evidence for a specific crude drug and part."""
from pathlib import Path

import pytest

from bioagent.analysis.network_pharmacology import Parameters, run_network_pharmacology
from bioagent.sources.herbs import FormulaVersion, herb_rows
from bioagent.sources.materia import all_drugs
from bioagent.sources.snapshot import Snapshot

pytestmark = pytest.mark.unit


def test_bark_is_not_evidence_for_twigs_in_either_order():
    def snapshot(key, nodes=(), edges=()):
        return Snapshot(f"{key}@fixture", key, "fixture", Path("."), {}, tuple(nodes), tuple(edges))
    bark = {"subject": "ncbitaxon:119260", "object": "pubchem:637511", "predicate": "contains",
            "source_record_id": "bark", "composition_level": "C1", "raw": {"parts": ["bark"]},
            "study_design": "chemical_analysis", "knowledge_level": "knowledge_assertion"}
    ids = ("tcm:herb.rougui", "tcm:herb.guizhi")
    for order in (ids, ids[::-1]):
        formula = FormulaVersion("tcm:formula.test", "桂", "fixture",
                                 tuple((herb, "", "", "") for herb in order))
        snapshots = [snapshot("tcm_herbs", *herb_rows(formula, drugs=all_drugs())),
                     snapshot("reactome"), snapshot("npass", edges=[bark])]
        for level, expected in (("C1", {ids[0]: "C2", ids[1]: "C1"}), ("C2", {ids[0]: "C2"})):
            r = run_network_pharmacology(snapshots, formula=formula,
                                         params=Parameters(composition_min_level=level))
            assert r.compounds[0]["herb_levels"] == expected
            assert r.compounds[0]["herbs"] == sorted(expected)
