"""Open Targets evidence datatypes, kept apart from fetch to analysis.

A text-mining association and a genetic one carry the same predicate and score scale. The
datatype is what tells them apart, so it must survive every step, a protocol must be able
to say which datatypes define its disease gene set, and what it leaves out must still be
reported. Datatype names are those of the live Platform on 2026-10-07 (API 26.9.0, data
26.09): genetic_association, genetic_literature, somatic_mutation, clinical (drug
evidence; there is no known_drug), affected_pathway, rna_expression, animal_model and
literature (Europe PMC text mining).
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_network_pharmacology import FAST, PATHWAY_A, PROTEINS, _build, ot_answer, ot_row
from test_opentargets import _API

from bioagent.analysis import Parameters, run_network_pharmacology
from bioagent.providers.public_apis import render_call
from bioagent.research.tools import _tupled
from bioagent.sources import build_snapshot
from bioagent.sources.fetch_opentargets import fetch_disease_associations
from bioagent.sources.parsers import parse_opentargets
from bioagent.sources.parsers.opentargets import KNOWLEDGE_LEVEL

LIVE_26_9 = ("genetic_association", "genetic_literature", "somatic_mutation", "clinical",
             "affected_pathway", "rna_expression", "animal_model", "literature")


def test_every_datatype_the_platform_reports_has_a_knowledge_level():
    assert set(LIVE_26_9) <= set(KNOWLEDGE_LEVEL)
    assert KNOWLEDGE_LEVEL["literature"] == "text_co_occurrence"
    assert KNOWLEDGE_LEVEL["genetic_association"] == "statistical_association"


def test_the_live_connector_asks_for_the_score_of_each_datatype():
    query = render_call("opentargets", "associated_diseases")["graphql"]
    assert "datatypeScores{id score}" in query and "rows{score" in query


def test_fetch_parse_and_snapshot_keep_each_datatype_score(tmp_path):
    rows = [ot_row("ENSG00000000001", "P10001", 0.9, genetic_association=0.9,
                   literature=0.6),
            ot_row("ENSG00000000002", "P10002", 0.7, literature=0.7),
            ot_row("ENSG00000000003", "P10003", 0.8, clinical=0.8, somatic_mutation=0.3)]
    saved = fetch_disease_associations("MONDO_0005148", tmp_path / "raw", backend=_API(rows))
    kept = {r["target"]["id"]: {d["id"]: d["score"] for d in r["datatypeScores"]}
            for r in json.loads(saved.read_text(encoding="utf-8"))["rows"]}
    assert kept["ENSG00000000003"] == {"clinical": 0.8, "somatic_mutation": 0.3}
    parsed = parse_opentargets(saved)
    snap = build_snapshot(key="opentargets", version="t", nodes=parsed.nodes,
                          edges=parsed.edges, raw_files=parsed.raw_files, parser="test",
                          root=tmp_path / "snap", license="CC0-1.0", citation="t")
    by = {(e["subject"], e["score_name"]): e for e in snap.edges}
    assert by[("uniprot:P10002", "literature")]["knowledge_level"] == "text_co_occurrence"
    assert by[("uniprot:P10001", "genetic_association")]["score"] == 0.9
    assert ("uniprot:P10002", "genetic_association") not in by
    assert {k for _, k in by} == {"overall", "genetic_association", "literature", "clinical",
                                  "somatic_mutation"}


# ------------------------------------------------------------------ declaring datatypes
def test_one_datatype_stays_a_string_so_existing_protocols_keep_their_digest():
    params = Parameters()
    assert asdict(params)["disease_evidence"] == "genetic_association"
    assert "evidence_datatypes" not in asdict(params)
    assert params.evidence_datatypes == ("genetic_association",)
    several = Parameters(disease_evidence=("genetic_association", "clinical"))
    assert several.evidence_datatypes == ("genetic_association", "clinical")
    # the governed analysis receives parameters as JSON; a tuple comes back a tuple
    trip = {k: _tupled(v) for k, v in json.loads(json.dumps(asdict(several))).items()}
    assert Parameters(**trip) == several


@pytest.mark.parametrize("evidence,needle", [
    (("genetic_association", "gossip"), "disease evidence"),
    ((), "disease evidence"),
    (("overall", "literature"), "'overall'"),
    (("clinical", "clinical"), "repeats a datatype"),
])
def test_a_declaration_that_cannot_mean_one_gene_set_is_refused(evidence, needle):
    with pytest.raises(ValueError, match=needle):
        Parameters(disease_evidence=evidence)


def _disease(root: Path, scores: dict[str, dict[str, float]]):
    rows = [ot_row(f"ENSG{i:011d}", p, max(types.values()), **types)
            for i, (p, types) in enumerate(sorted(scores.items()))]
    root.mkdir(parents=True, exist_ok=True)
    result = parse_opentargets(ot_answer(rows, root / "ot.json"))
    return build_snapshot(key="opentargets", version="fx", nodes=result.nodes,
                          edges=result.edges, raw_files=result.raw_files, parser="fixture",
                          root=root, license="CC0-1.0", citation="fixture")


A = PATHWAY_A
SCORES = {
    A[0]: {"genetic_association": 0.9, "literature": 0.9},   # genetic, and co-mentioned
    A[1]: {"genetic_association": 0.9}, A[2]: {"genetic_association": 0.9},
    A[3]: {"clinical": 0.8},
    A[5]: {"literature": 0.9}, A[6]: {"literature": 0.9},   # co-mention only
    A[7]: {"genetic_association": 0.2, "literature": 0.9},  # genetic below the cut-off
    PROTEINS[30]: {"literature": 0.9},                       # co-mention, not measured
}


def test_a_protocol_declares_its_datatypes_and_literature_only_is_reported_apart(tmp_path):
    snaps = [*_build(tmp_path / "w"), _disease(tmp_path / "ot", SCORES)]
    params = replace(FAST, disease_evidence=("genetic_association", "clinical"))
    d = run_network_pharmacology(snaps, params=params).disease
    assert d["evidence"] == ("genetic_association", "clinical")
    assert d["disease_genes"] == 4                           # A0-A2 genetic, A3 clinical
    assert sorted(t["target"] for t in d["targets"]) == [f"uniprot:{p}" for p in A[:4]]
    assert d["datatypes"]["literature"] == {"disease_genes": 5, "overlap": 4, "used": False}
    assert d["datatypes"]["clinical"]["used"] and d["datatypes"]["genetic_association"][
        "disease_genes"] == 3
    only = d["literature_only"]
    assert (only["disease_genes"], only["overlap"], only["in_gene_set"]) == (4, 3, 0)
    assert d["declared_absent"] == []
    result = run_network_pharmacology(snaps, params=params)
    assert any("4 targets reach the cut-off through literature co-mention alone" in line
               for line in result.limitations)
    assert not any("circular" in line for line in result.limitations)


def test_declaring_literature_puts_co_mention_in_the_set_and_says_so(tmp_path):
    snaps = [*_build(tmp_path / "w"), _disease(tmp_path / "ot", SCORES)]
    result = run_network_pharmacology(
        snaps, params=replace(FAST, disease_evidence=("genetic_association", "literature")))
    assert result.disease["disease_genes"] == 7
    assert result.disease["literature_only"]["in_gene_set"] == 4
    assert any("4 of the disease genes rest on literature co-mention alone" in line
               for line in result.limitations)
    assert any("circular" in line for line in result.limitations)


def test_a_datatype_the_release_does_not_have_is_named_not_silently_empty(tmp_path):
    snaps = [*_build(tmp_path / "w"), _disease(tmp_path / "ot", SCORES)]
    result = run_network_pharmacology(snaps,
                                      params=replace(FAST, disease_evidence="known_drug"))
    assert result.disease["disease_genes"] == 0
    assert result.disease["declared_absent"] == ["known_drug"]
    assert any("no known_drug associations" in line and "'clinical'" in line
               for line in result.limitations)
