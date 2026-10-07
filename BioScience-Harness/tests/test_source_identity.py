"""Source identity: one identifier, one spelling; one source, one count."""

from __future__ import annotations

import pytest

from bioagent.sources.identity import (SCHEMES, SourceId, canonical, canonical_many,
                                       group_sources, normalise_assembly)
from bioagent.sources.parsers.common import publication


@pytest.mark.parametrize("written,scheme,expected", [
    ("PMID: 012345", None, "pmid:12345"),
    ("12345", "pmid", "pmid:12345"),
    ("MED:34956436", None, "pmid:34956436"),                  # Europe PMC's source code
    ("https://pubmed.ncbi.nlm.nih.gov/34956436/", None, "pmid:34956436"),
    ("pmc8696197.2", None, "pmcid:PMC8696197"),               # a PMC article version
    ("8696197", "pmcid", "pmcid:PMC8696197"),
    ("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8696197/", None, "pmcid:PMC8696197"),
    ("https://doi.org/10.1016/J.JEP.2021.114180.", None, "doi:10.1016/j.jep.2021.114180"),
    ("doi:10.1155/2021/2074610", None, "doi:10.1155/2021/2074610"),
    ("10.1155%2F2021%2F2074610", None, "doi:10.1155/2021/2074610"),
    ("info:doi/10.1000/ABC", None, "doi:10.1000/abc"),
    ("DOI: 10.1000/abc;", None, "doi:10.1000/abc"),
    ("nct01234567", None, "nct:NCT01234567"),
    ("01234567", "nct", "nct:NCT01234567"),
    ("https://clinicaltrials.gov/study/NCT06911983", None, "nct:NCT06911983"),
    ("RS7903146", None, "rsid:rs7903146"),
    ("https://www.ncbi.nlm.nih.gov/snp/rs7903146", None, "rsid:rs7903146"),
    ("NM_004333.4(BRAF):c.1799t>a", None, "hgvs:NM_004333.4:c.1799T>A"),
    ("NP_004324.2:p.V600E", None, "hgvs:NP_004324.2:p.Val600Glu"),
    ("NP_004324.2:p.(Val600Glu)", None, "hgvs:NP_004324.2:p.Val600Glu"),
    ("10:g.114758349c>t", None, "hgvs:chr10:g.114758349C>T"),
])
def test_each_spelling_has_one_canonical_form(written, scheme, expected):
    assert str(canonical(written, scheme)) == expected


@pytest.mark.parametrize("written", ["2244", "V600E", "", None, True, "not an id",
                                     "pubchem.bioassay:1234", "BRAF V600E"])
def test_what_names_nothing_it_can_check_is_not_an_identifier(written):
    """A bare number is as likely a PubChem CID as a PMID; it is read as one only when the
    caller says so."""
    assert canonical(written) is None


def test_a_hint_never_turns_another_kind_of_identifier_into_the_hinted_one():
    assert canonical("https://pubmed.ncbi.nlm.nih.gov/123/", "doi") is None
    assert canonical("10.1000/abc", "pmid") is None
    with pytest.raises(ValueError, match="unknown identifier scheme"):
        canonical("123", "isbn")


def test_a_genomic_position_carries_its_assembly():
    """chr10:g.114758349C>T is TCF7L2's risk allele on GRCh37 and a different base on
    GRCh38: the same string under two assemblies is two variants."""
    hg19 = canonical("chr10:g.114758349C>T", assembly="hg19")
    assert str(hg19) == "hgvs:GRCh37:chr10:g.114758349C>T"
    assert hg19 != canonical("chr10:g.114758349C>T", assembly="GRCh38")
    assert hg19 != canonical("chr10:g.114758349C>T")
    assert canonical(str(hg19)) == hg19 == canonical(hg19)
    with pytest.raises(ValueError, match="names GRCh37"):
        canonical("GRCh37:chr10:g.114758349C>T", "hgvs", assembly="GRCh38")
    with pytest.raises(ValueError, match="unknown genome assembly"):
        normalise_assembly("hg91")


@pytest.mark.parametrize("value", [
    "pmid:12345", "pmcid:PMC8696197", "doi:10.1000/abc", "nct:NCT01234567",
    "rsid:rs7903146", "hgvs:NM_004333.4:c.1799T>A", "hgvs:GRCh38:chr7:g.140753336A>T"])
def test_the_canonical_form_is_a_fixed_point(value):
    sid = canonical(value)
    assert str(sid) == value and canonical(sid) == sid and canonical(str(sid)) == sid


@pytest.mark.parametrize("ref_id,ref_type", [
    ("12345", "pmid"), ("12345", None), ("10.1016/J.JEP.2019.112345", "doi"),
    ("https://doi.org/10.1016/j.jep.2019.112345", "doi"), ("10.1000/XYZ", None)])
def test_it_agrees_with_the_snapshot_parsers_where_they_overlap(ref_id, ref_type):
    """``parsers.common.publication`` writes the CURIEs the snapshots hold; identity must
    read those as the same sources, or a snapshot edge and a live record of one paper would
    count twice."""
    assert str(canonical(publication(ref_id, ref_type))) == publication(ref_id, ref_type)


def test_one_paper_reached_three_ways_counts_once():
    """PubMed returns the PMID; BioMCP the PMID with its PMCID and DOI; an Open Targets
    literature row or a TCM database the DOI as a URL. Linked by the record that states
    them together, they are one source."""
    count = group_sources({
        "pubmed": ["PMID: 34956436"],
        "biomcp": ["pmid:34956436", "PMC8696197", "doi:10.1155/2021/2074610"],
        "opentargets": ["https://doi.org/10.1155/2021/2074610"],
        "europepmc": ["PMC:PMC8696197"],
        "other": ["pmid:18397984"],
    })
    assert count.count == 2
    paper = count.group_of("pubmed")
    assert paper.key == SourceId("pmid", "34956436")
    assert paper.members == ("pubmed", "biomcp", "opentargets", "europepmc")
    assert [str(i) for i in paper.identifiers] == [
        "pmid:34956436", "pmcid:PMC8696197", "doi:10.1155/2021/2074610"]
    assert count.as_dict()["sources"] == 2


def test_two_names_that_no_record_joins_stay_two_sources():
    """Mapping a PMID to a DOI needs a lookup service this module never calls."""
    assert group_sources([["pmid:34956436"], ["doi:10.1155/2021/2074610"]]).count == 2


def test_a_record_it_cannot_identify_is_counted_on_its_own_and_reported():
    count = group_sources({"a": ["pmid:1"], "b": ["see the 2019 review"], "c": [],
                           "d": "pmid:1"})
    assert count.count == 3
    assert count.unidentified == ("b", "c")
    assert count.group_of("d").members == ("a", "d")


def test_linking_follows_names_transitively_unless_told_which_schemes_link():
    """Two alleles under one rsID are one dbSNP record; counted by allele they are two."""
    alleles = [["rs7903146", "hgvs:GRCh37:chr10:g.114758349C>T"],
               ["rs7903146", "hgvs:GRCh37:chr10:g.114758349C>G"]]
    assert group_sources(alleles).count == 1
    assert group_sources(alleles, link_on=("hgvs",)).count == 2
    with pytest.raises(ValueError, match="cannot link on"):
        group_sources(alleles, link_on=("isbn",))


def test_grouping_does_not_depend_on_the_order_records_arrive_in():
    records = [["pmid:3", "doi:10.1000/c"], ["doi:10.1000/C"], ["pmid:1"],
               ["pmid:1", "PMC9"]]
    forward = group_sources(records)
    backward = group_sources(list(reversed(records)))
    assert [g.identifiers for g in forward.groups] == [g.identifiers for g in backward.groups]
    assert forward.count == backward.count == 2


def test_canonical_many_keeps_each_name_once_in_scheme_order():
    names = canonical_many(["doi:10.1000/X", "PMID 7", "pmid:7", "nonsense", "rs1",
                            "doi:10.1/too-short"])
    assert [str(n) for n in names] == ["pmid:7", "doi:10.1000/x", "rsid:rs1"]
    assert SCHEMES == ("pmid", "pmcid", "doi", "nct", "rsid", "hgvs")


# ------------------------------------------------------------------ where counts use it
def test_the_retrieve_skill_counts_a_study_recorded_twice_once(monkeypatch):
    """The skill already counted a study cited by two relations once; a study entered
    twice under two ids, once by PMID and once by PMID and DOI, is one study as well."""
    from bioagent.skills.p0 import retrieve_tcm_evidence
    from bioagent.tcm import knowledge as tcm_knowledge
    from bioagent.tcm.model import ActionRelation, EvidenceTier, StudyEvidence

    kb = tcm_knowledge.seed()
    for study in (
            StudyEvidence(id="study.rct_a", tier=EvidenceTier.RANDOMIZED_TRIAL,
                          subject_id="herb.renshen", pmid="18397984", effect="improved"),
            StudyEvidence(id="study.rct_b", tier=EvidenceTier.RANDOMIZED_TRIAL,
                          subject_id="herb.renshen", pmid="PMID: 018397984",
                          doi="10.1016/J.METABOL.2008.01.013", effect="improved"),
            StudyEvidence(id="study.rct_c", tier=EvidenceTier.RANDOMIZED_TRIAL,
                          subject_id="herb.renshen", registry_id="NCT01234567",
                          effect="improved")):
        kb.studies[study.id] = study
    kb.relations["relation.test_rct"] = ActionRelation(
        id="relation.test_rct", subject_id="herb.renshen", predicate="treats",
        object_id="syndrome.qixu", tier=EvidenceTier.RANDOMIZED_TRIAL,
        evidence_ids=("study.rct_a", "study.rct_b", "study.rct_c"))
    monkeypatch.setattr(tcm_knowledge, "default_knowledge", lambda: kb)

    artifact = retrieve_tcm_evidence("人参", run_id="t")
    ids = [e.id for e in artifact.evidence]
    assert "study.study.rct_a" in ids and "study.study.rct_c" in ids
    assert "study.study.rct_b" not in ids
    assert any("study.rct_a, study.rct_b name the same source (pmid:18397984)" in line
               for line in artifact.limitations)


def test_a_consensus_lineage_names_a_paper_once_however_a_database_wrote_it():
    """DDID and dbPTH store a DOI without its prefix; read as written it was not a paper
    at all, and one paper cited by two databases was two lineages."""
    from bioagent.tcmdb.consensus import independent_count, lineage_of

    bare = lineage_of({"source": "ddid", "evidence": "known",
                       "reference": "10.1016/J.JEP.2019.112345"})
    prefixed = lineage_of({"source": "dbpth", "evidence": "known",
                           "reference": "https://doi.org/10.1016/j.jep.2019.112345"})
    assert bare == prefixed == frozenset({"doi:10.1016/j.jep.2019.112345"})
    assert independent_count(bare | prefixed) == 1
    assert lineage_of({"source": "x", "evidence": "known", "reference": "pmid:111"}) == {
        "pmid:111"}
    # a reference on a row that is not an observation is still not a citation lineage
    assert lineage_of({"source": "ddid", "evidence": "predicted",
                       "reference": "10.1016/j.jep.2019.112345"}) == {"model:ddid"}


def test_a_target_measured_twice_in_one_paper_rests_on_one_source(tmp_path):
    from test_network_pharmacology import FAST, PATHWAY_A, _build

    from bioagent.analysis import run_network_pharmacology

    result = run_network_pharmacology(
        _build(tmp_path, targets_of={0: PATHWAY_A[:3], 1: PATHWAY_A[:3],
                                     2: PATHWAY_A[3:7], 3: [PATHWAY_A[7]]}), params=FAST)
    row = next(t for t in result.targets if t["target"] == f"uniprot:{PATHWAY_A[0]}")
    assert row["measurements"] == 2 and row["sources"] == 1
