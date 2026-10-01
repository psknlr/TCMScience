"""Pharmacogenomics and protein-family sources: PharmVar, CPIC, ClinPGx, GPCRdb (datasets)
and CPIC, GPCRdb, KLIFS, ProteomicsDB (live connectors).

Every fixture is a tiny file written here in the source's real format: the headers are
copied from the files fetched on 2026-10-01 and the rows are made up or real-shaped.
Nothing downloaded is committed.
"""

from __future__ import annotations

import json
import urllib.parse
import zipfile

import pytest

from bioagent.tcmdb import TCMDataHub

pytestmark = pytest.mark.unit


def _hub(tmp_path) -> TCMDataHub:
    return TCMDataHub(tmp_path / "hub")


def _rows(h, key, kind=None):
    return [r for r in h.relations(kind, sources=[key], limit=10_000)]


# ------------------------------------------------------------------------------ PharmVar
_PV_HEADER = ("Haplotype Name\tGene\trsID\tReferenceSequence\tVariant Start\tVariant Stop\t"
              "Reference Allele\tVariant Allele\tType")


def _pharmvar_zip(path):
    grch38 = "\n".join([
        "#version=pharmvar-6.2.29", _PV_HEADER,
        "NUDT15*1\tNUDT15\t\tREFERENCE\t.\t\t\t\t",
        "NUDT15*3\tNUDT15\trs116855232\tNC_000013.11\t48045719\t48045719\tC\tT\tsubstitution",
        "NUDT15*3.001\tNUDT15\trs116855232\tNC_000013.11\t48045719\t48045719\tC\tT\t"
        "substitution",
        "NUDT15*2\tNUDT15\trs746071566\tNC_000013.11\t48037747\t48037748\t-\tGGAGTC\t"
        "insertion",
    ]) + "\n"
    dpyd = "\n".join(["#version=pharmvar-6.2.29", _PV_HEADER,
                      "rs112766203.1\tDPYD\trs112766203\tNC_000001.11\t97305279\t97305279\t"
                      "G\tA\tsubstitution"]) + "\n"
    grch37 = "\n".join(["#version=pharmvar-6.2.29", _PV_HEADER,
                        "NUDT15*3\tNUDT15\trs116855232\tNC_000013.10\t48619855\t48619855\tC\t"
                        "T\tsubstitution"]) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("pharmvar-6.2.29/NUDT15/NUDT15.haplotypes.fasta", ">NUDT15*1 PV0\nACGT\n")
        z.writestr("pharmvar-6.2.29/NUDT15/GRCh38/NUDT15_3.vcf", "##fileformat=VCFv4.1\n")
        z.writestr("pharmvar-6.2.29/NUDT15/GRCh38/NUDT15.NC_000013.11.haplotypes.tsv", grch38)
        z.writestr("pharmvar-6.2.29/NUDT15/GRCh37/NUDT15.NC_000013.10.haplotypes.tsv", grch37)
        z.writestr("pharmvar-6.2.29/DPYD/GRCh38/DPYD.NC_000001.11.haplotypes.tsv", dpyd)


def test_pharmvar_allele_definitions_come_from_grch38_and_skip_the_reference(tmp_path):
    h = _hub(tmp_path)
    _pharmvar_zip(h.raw_dir("pharmvar") / "pharmvar_all.zip")
    report = h.build("pharmvar", log=lambda m: None)
    # every reference set is loaded as one table, VCF and FASTA members are not
    assert report["tables"] == {"haplotypes": 6}
    table = h.query("pharmvar", "haplotypes", where={"Reference_Set": "GRCh37"})
    assert len(table) == 1 and table[0]["PharmVar_Version"] == "pharmvar-6.2.29"
    rows = {(r["subject_id"], r["object_id"]): r for r in _rows(h, "pharmvar")}
    assert set(rows) == {("allele:NUDT15*3", "dbsnp:rs116855232"),
                         ("allele:NUDT15*3.001", "dbsnp:rs116855232"),
                         ("allele:NUDT15*2", "dbsnp:rs746071566"),
                         ("pharmvar:rs112766203.1", "dbsnp:rs112766203")}
    star3 = rows[("allele:NUDT15*3", "dbsnp:rs116855232")]
    assert star3["kind"] == "allele_variant" and star3["evidence"] == "listed"
    assert star3["subject_type"] == "allele" and star3["object_type"] == "variant"
    assert json.loads(star3["context"]) == {"genome_build": "GRCh38",
                                            "variant": "NC_000013.11:48045719:C>T"}
    ins = rows[("allele:NUDT15*2", "dbsnp:rs746071566")]
    assert json.loads(ins["context"])["variant"] == "NC_000013.11:48037747-48037748:->GGAGTC"
    assert ins["note"] == "insertion"
    assert "research use only" in star3["license"]
    assert h.relations("allele_variant", sources=["pharmvar"], commercial=True) == []


def test_a_pharmvar_error_answer_is_not_read_as_a_table(tmp_path):
    from bioagent.tcmdb.store import StoreError
    h = _hub(tmp_path)
    raw = h.raw_dir("pharmvar")
    raw.mkdir(parents=True)
    (raw / "pharmvar_all.zip").write_text('{"errorMessage":"The application failed to '
                                          'save.","errorCode":500}')
    with pytest.raises(StoreError, match="not a zip"):
        h.build("pharmvar", log=lambda m: None)


# ---------------------------------------------------------------------------------- CPIC
_CPIC_HEADER = ("Gene", "Drug", "Drug RxNorm ID", "Drug ATC IDs", "Guideline", "CPIC Level",
                "CPIC Level Status", "ClinPGx Level of Evidence", "PGx on FDA Label",
                "CPIC Publications (PMID)")


def _cpic_xlsx(path):
    openpyxl = pytest.importorskip("openpyxl")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "CPIC Gene-Drug Pairs"
    ws.append(_CPIC_HEADER)
    ws.append(("CYP2C19", "clopidogrel", "32968", "B01AC04",
               "https://www.clinpgx.org/guideline/PA166251443", "A", "Final", "1A",
               "Actionable PGx", "23698643;35034351"))
    ws.append(("CYP2D6", "doxepin", "3638", "N06AA12", None, "B/C", "Provisional", None,
               None, None))
    ws.append(("ADRB1", "bucindolol", None, None, None, "D", "Provisional", "3", None, None))
    ws.append(("CYP2C9", "retiredrug", "999", None, None, "Retired", "Final", None, None,
               None))
    log = wb.create_sheet("Change log")
    log.append(("Date", "Note"))
    log.append(("2023-11-13", "[x-CYP2D6] PGx Testing: Informative PGx >> none"))
    wb.save(path)


def test_cpic_pairs_carry_their_level_and_only_actionable_levels_are_known(tmp_path):
    h = _hub(tmp_path)
    _cpic_xlsx(h.raw_dir("cpic") / "cpic_gene-drug_pairs.xlsx")
    h.build("cpic", log=lambda m: None)
    rows = {r["subject_id"]: r for r in _rows(h, "cpic", "drug_pharmacogene")}
    assert set(rows) == {"rxnorm:32968", "rxnorm:3638", "cpic:bucindolol"}  # retired: out
    clop = rows["rxnorm:32968"]
    assert clop["object_id"] == "symbol:CYP2C19" and clop["evidence"] == "known"
    assert clop["reference"] == "pmid:23698643; pmid:35034351"
    assert clop["note"] == "ClinPGx level 1A | FDA label: Actionable PGx"
    assert json.loads(clop["context"]) == {
        "confidence": "CPIC level A", "flags": "Final",
        "source_id": "https://www.clinpgx.org/guideline/PA166251443"}
    assert rows["rxnorm:3638"]["evidence"] == "listed"                    # B/C
    assert rows["cpic:bucindolol"]["evidence"] == "listed"                # D, no RxNorm
    assert all(r["outcome"] == "positive" for r in rows.values())
    # CC0: kept by a commercial query
    assert len(h.relations("drug_pharmacogene", sources=["cpic"], commercial=True)) == 3


# ------------------------------------------------------------------------------- ClinPGx
_LICENSE = "ClinPGx data is licensed under CC BY-SA 4.0 ...\n"
_REL_HEADER = ("Entity1_id\tEntity1_name\tEntity1_type\tEntity2_id\tEntity2_name\t"
               "Entity2_type\tEvidence\tAssociation\tPK\tPD\tPMIDs")
_SUM_HEADER = ("Summary Annotation ID\tVariant/Haplotypes\tGene\tLevel of Evidence\t"
               "Level Override\tLevel Modifiers\tScore\tPhenotype Category\tPMID Count\t"
               "Evidence Count\tDrug(s)\tPhenotype(s)\tLatest History Date (YYYY-MM-DD)\tURL\t"
               "Specialty Population")
_CHEM_HEADER = ("PharmGKB Accession Id\tName\tGeneric Names\tTrade Names\tBrand Mixtures\tType\t"
                "Cross-references\tSMILES\tInChI\tDosing Guideline\tExternal Vocabulary\t"
                "Clinical Annotation Count\tVariant Annotation Count\tPathway Count\tVIP Count\t"
                "Dosing Guideline Sources\tTop Clinical Annotation Level\t"
                "Top FDA Label Testing Level\tTop Any Drug Label Testing Level\t"
                "Label Has Dosing Info\tRxNorm Identifiers\tATC Identifiers\t"
                "PubChem Compound Identifiers\tTop CPIC Pairs Level\t"
                "FDA Label has Prescribing Info\tIn FDA PGx Association Sections")
_LABEL_HEADER = ("PharmGKB ID\tName\tSource\tBiomarker Flag\tTesting Level\t"
                 "Has Prescribing Info\tHas Dosing Info\tHas Alternate Drug\t"
                 "Has Other Prescribing Guidance\tCancer Genome\tPrescribing\tChemicals\tGenes\t"
                 "Variants/Haplotypes\tLatest History Date (YYYY-MM-DD)")


def _chem(pa, name, rxnorm="", pubchem="", generic=""):
    cells = [pa, name, generic, "", "", "Drug", "", "", "", "No", "", "0", "0", "0", "n/a",
             "", "", "", "", "", rxnorm, "", pubchem, "", "", ""]
    return "\t".join(cells)


def _clinpgx_zip(path, member, header, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("LICENSE.txt", _LICENSE)                   # first, as in the real zips
        z.writestr("CREATED_2026-09-05.txt", "")
        z.writestr(member, "\n".join((header, *lines)) + "\n")
        z.writestr("README.pdf", b"%PDF-1.4")


@pytest.fixture
def clinpgx(tmp_path):
    h = _hub(tmp_path)
    raw = h.raw_dir("clinpgx")
    _clinpgx_zip(raw / "chemicals.zip", "chemicals.tsv", _CHEM_HEADER,
                 _chem("PA449053", "clopidogrel", "32968", "60606",
                       generic='"""(S)-clopidogrel"", ""clopidogrel bisulfate"""'),
                 _chem("PA449957", "codeine", "2670", "5284371"),
                 _chem("PA452233", "antipsychotics"),                  # a class: no ids
                 _chem("PA10000", "two-rx drug", "111, 222", "777"),
                 _chem("PA20000", "tegafur / gimeracil / oteracil", "", "54715158"),
                 _chem("PA30000", "synthetic conjugated estrogens, A", "9999", ""))
    _clinpgx_zip(raw / "relationships.zip", "relationships.tsv", _REL_HEADER,
                 # one pair, listed in both directions
                 "PA124\tCYP2C19\tGene\tPA449053\tclopidogrel\tChemical\t"
                 "SummaryAnnotation,VariantAnnotation\tassociated\t\tPD\t11111;22222",
                 "PA449053\tclopidogrel\tChemical\tPA124\tCYP2C19\tGene\t"
                 "SummaryAnnotation,VariantAnnotation\tassociated\t\tPD\t11111;22222",
                 "PA128\tCYP2D6\tGene\tPA452233\tantipsychotics\tChemical\tVariantAnnotation\t"
                 "not associated\t\t\t33333",
                 "PA166154579\trs4244285\tVariant\tPA449053\tclopidogrel\tChemical\t"
                 "VariantAnnotation\tambiguous\t\t\t44444",
                 "PA165980635\tCYP2C19*2\tHaplotype\tPA449053\tclopidogrel\tChemical\t"
                 "SummaryAnnotation\tassociated\t\t\t55555",
                 "PA165948092\tGSTM1 null\tHaplotype\tPA449957\tcodeine\tChemical\t"
                 "VariantAnnotation\tassociated\t\t\t",
                 "PA124\tCYP2C19\tGene\tPA447288\tEssential hypertension\tDisease\t"
                 "VariantAnnotation\tassociated\t\t\t66666")
    _clinpgx_zip(raw / "summaryAnnotations.zip", "summary_annotations.tsv", _SUM_HEADER,
                 "981755803\tCYP2C19*1, CYP2C19*2, CYP2C19*3\tCYP2C19\t1A\t\tTier 1 VIP\t"
                 "100.5\tEfficacy\t40\t50\tclopidogrel\tAcute coronary syndrome\t2024-01-01\t"
                 "https://www.clinpgx.org/clinicalAnnotation/981755803\t",
                 "655384738\trs17708472\tVKORC1\t4\t\tTier 1 VIP\t-2.0\tDosage\t7\t7\t"
                 "clopidogrel;codeine;unknownium\t\t2021-11-19\t"
                 "https://www.clinpgx.org/clinicalAnnotation/655384738\tPediatric")
    _clinpgx_zip(raw / "drugLabels.zip", "drugLabels.tsv", _LABEL_HEADER,
                 "PA166104916\tAnnotation of FDA Label for codeine and CYP2D6\tFDA\t"
                 "On FDA Biomarker List\tActionable PGx\tPrescribing Info\t\tAlternate Drug\t\t"
                 "\tPrescribing\tcodeine\tCYP2D6\tCYP2D6*1xN; CYP2D6*2xN\t2025-01-01",
                 "PA166271261\tAnnotation of EMA Label for tegafur / gimeracil / oteracil and "
                 "DPYD\tEMA\t\tTesting Recommended\tPrescribing Info\t\tAlternate Drug\t\t\t"
                 "Prescribing\ttegafur / gimeracil / oteracil\tDPYD; TYMS\t\t",
                 "PA166269701\tAnnotation of EMA Label for estrogens\tEMA\t\t\t\t\t\t\t\t\t"
                 "synthetic conjugated estrogens, A\t\t\t2024-08-07")
    report = h.build("clinpgx", log=lambda m: None)
    return h, report


def test_clinpgx_zips_are_read_from_the_named_member_with_csv_quoting(clinpgx):
    h, report = clinpgx
    assert report["tables"] == {"relationships": 7, "summary_annotation": 2, "chemical": 6,
                                "drug_label": 3}
    chem = h.query("clinpgx", "chemical", where={"Name": "clopidogrel"})[0]
    assert chem["Generic_Names"] == '"(S)-clopidogrel", "clopidogrel bisulfate"'


def test_clinpgx_relationships_keep_negative_and_ambiguous_results(clinpgx):
    h, _ = clinpgx
    rows = [r for r in _rows(h, "clinpgx") if r["evidence"] == "aggregated"]
    by = {(r["kind"], r["subject_id"], r["object_id"]): r for r in rows}
    # the pair listed twice gives one row, the gene-disease pair none
    assert len(rows) == 5
    pair = by[("drug_pharmacogene", "rxnorm:32968", "symbol:CYP2C19")]
    assert pair["outcome"] == "positive"
    assert pair["reference"] == "pmid:11111; pmid:22222"
    assert pair["note"] == "ClinPGx evidence: SummaryAnnotation,VariantAnnotation"
    assert json.loads(pair["context"]) == {"mechanism": "PD"}
    # a drug class with neither RxNorm nor PubChem keeps its ClinPGx id
    assert by[("drug_pharmacogene", "clinpgx:PA452233", "symbol:CYP2D6")]["outcome"] \
        == "negative"
    snp = by[("variant_drug", "dbsnp:rs4244285", "rxnorm:32968")]
    assert snp["outcome"] == "inconclusive" and snp["subject_type"] == "variant"
    star = by[("variant_drug", "allele:CYP2C19*2", "rxnorm:32968")]
    assert star["subject_type"] == "allele" and star["outcome"] == "positive"
    null = by[("variant_drug", "clinpgx:PA165948092", "rxnorm:2670")]
    assert null["subject_name"] == "GSTM1 null" and null["reference"] is None


def test_clinpgx_summary_annotations_carry_the_level_and_level_4_is_negative(clinpgx):
    h, report = clinpgx
    rows = [r for r in _rows(h, "clinpgx") if r["evidence"] == "associated"]
    gene_rows = {(r["subject_id"], r["object_id"]): r for r in rows
                 if r["kind"] == "drug_pharmacogene"}
    variant_rows = {(r["subject_id"], r["object_id"]): r for r in rows
                    if r["kind"] == "variant_drug"}
    top = gene_rows[("rxnorm:32968", "symbol:CYP2C19")]
    assert top["outcome"] == "positive" and top["score"] == "100.5"
    assert top["reference"] == "https://www.clinpgx.org/clinicalAnnotation/981755803"
    assert json.loads(top["context"]) == {
        "confidence": "ClinPGx level 1A", "flags": "Tier 1 VIP", "measure": "Efficacy",
        "phenotype": "Acute coronary syndrome", "variant": "CYP2C19*1, CYP2C19*2, CYP2C19*3"}
    # several alleles: no allele is singled out (CYP2C19*1 is the comparison, not a hit)
    assert not any(k[0].startswith("allele:CYP2C19") for k in variant_rows)
    # level 4: the evidence does not support an association
    for key in (("rxnorm:32968", "symbol:VKORC1"), ("rxnorm:2670", "symbol:VKORC1")):
        assert gene_rows[key]["outcome"] == "negative"
    assert variant_rows[("dbsnp:rs17708472", "rxnorm:2670")]["outcome"] == "negative"
    assert json.loads(variant_rows[("dbsnp:rs17708472", "rxnorm:32968")]["context"])[
        "condition"] == "Pediatric"
    # a drug name ClinPGx's chemical list lacks waits in the queue, not under a made-up id
    assert report["unresolved"] == 1
    queued = h.unresolved("clinpgx")[0]
    assert queued["subject_name"] == "unknownium" and queued["object_name"] == "VKORC1"


def test_clinpgx_drug_labels_name_their_agency_and_split_only_on_semicolons(clinpgx):
    h, _ = clinpgx
    rows = [r for r in _rows(h, "clinpgx") if r["evidence"] == "listed"]
    pairs = {(r["subject_id"], r["object_id"]) for r in rows}
    # the combination name keeps its slashes and one RxNorm-less PubChem id; two genes;
    # the label without genes gives no row
    assert pairs == {("rxnorm:2670", "symbol:CYP2D6"), ("pubchem:54715158", "symbol:DPYD"),
                     ("pubchem:54715158", "symbol:TYMS")}
    fda = next(r for r in rows if r["object_id"] == "symbol:CYP2D6")
    assert fda["note"] == "via FDA" and fda["reference"] == "clinpgx:PA166104916"
    ctx = json.loads(fda["context"])
    assert ctx["source_db"] == "FDA" and ctx["confidence"] == "Actionable PGx"
    assert ctx["variant"] == "CYP2D6*1xN; CYP2D6*2xN"
    assert ctx["flags"] == "On FDA Biomarker List | Prescribing Info | Alternate Drug"


def test_clinpgx_rows_are_left_out_of_commercial_queries(clinpgx):
    h, _ = clinpgx
    assert _rows(h, "clinpgx")
    assert h.relations(sources=["clinpgx"], commercial=True) == []
    assert all("research use only" in r["license"] for r in _rows(h, "clinpgx"))


def test_a_drug_with_two_rxnorm_ids_falls_back_to_pubchem():
    import sqlite3

    from bioagent.tcmdb.extra.pgx_proteins import _chemicals
    conn = sqlite3.connect(":memory:")
    conn.execute('CREATE TABLE chemical ("PharmGKB_Accession_Id", "Name", '
                 '"RxNorm_Identifiers", "PubChem_Compound_Identifiers")')
    conn.executemany("INSERT INTO chemical VALUES (?, ?, ?, ?)", [
        ("PA1", "two-rx drug", "111, 222", "777"), ("PA2", "plain", "5", "6"),
        ("PA3", "bare", None, None)])
    by_pa, by_name = _chemicals(conn)
    assert by_pa["PA1"][0] == "pubchem:777"
    assert by_pa["PA2"][0] == "rxnorm:5"
    assert by_name["bare"][0] == "clinpgx:PA3"


# -------------------------------------------------------------------------------- GPCRdb
def test_gpcrdb_drug_targets_carry_the_stated_mechanism(tmp_path):
    h = _hub(tmp_path)
    raw = h.raw_dir("gpcrdb")
    raw.mkdir(parents=True)

    def rec(name, target, entry, acc, rel_, ik="BUXIAWLTBSXYSW-UHFFFAOYSA-N", lig=447,
            status="Approved"):
        return {"drug_name": name, "ligand_type": "Small molecule", "gpcr_target": target,
                "gpcr_target_entry_name": entry, "gpcr_target_uniprot_id": acc,
                "fda_approval_status": status, "smiles": "C", "inchikey": ik, "helm": None,
                "gpcrdb_ligand_id": lig, "drug_target_relationship": rel_}

    data = [rec("salbutamol", "&beta;<sub>2</sub>-adrenoceptor", "adrb2_human", "P07550",
                "Agonist", ik="NDAUXUAQIAJITI-UHFFFAOYSA-N", lig=1),
            rec("AB928", "A<sub>2A</sub> receptor", "aa2ar_human", "P29274", "Antagonist",
                status="Active"),
            rec("cinacalcet", "CaS receptor", "casr_human", "P41180", "PAM", ik=None, lig=9),
            rec("mavacamten-like", "X receptor", "x_human", "P00001", "NAM",
                ik="AAAAAAAAAAAAAA-BBBBBBBBBB-C", lig=10),
            rec("mystery", "Y receptor", "y_human", "P00002", "Unknown",
                ik="CCCCCCCCCCCCCC-DDDDDDDDDD-E", lig=11)]
    (raw / "gpcrdb_drugs.json").write_text(json.dumps(data))
    h.build("gpcrdb", log=lambda m: None)
    rows = {r["subject_name"]: r for r in _rows(h, "gpcrdb", "drug_target")}
    sal = rows["salbutamol"]
    assert sal["subject_id"] == "inchikey:NDAUXUAQIAJITI-UHFFFAOYSA-N"
    assert sal["object_id"] == "uniprot:P07550"
    assert sal["object_name"] == "β2-adrenoceptor | adrb2_human"           # unescaped
    assert sal["effect"] == "activation" and sal["evidence"] == "known"
    assert json.loads(sal["context"]) == {"action": "Agonist", "source_id": "1",
                                          "species": "9606", "stage": "Approved"}
    assert rows["AB928"]["effect"] == "inhibition"
    assert rows["cinacalcet"]["subject_id"] == "gpcrdb:ligand.9"         # no InChIKey
    assert rows["cinacalcet"]["effect"] == "activation"                  # PAM
    assert rows["mavacamten-like"]["effect"] == "inhibition"             # NAM
    assert rows["mystery"]["effect"] is None                             # never guessed
    assert json.loads(rows["mystery"]["context"])["action"] == "Unknown"
    assert len(h.relations("drug_target", sources=["gpcrdb"], commercial=True)) == 5


# ------------------------------------------------------------------- acceptance checks
@pytest.fixture
def with_cards(monkeypatch):
    """Stub catalogue cards for 110-114 until the integrator adds the real ones."""
    from bioagent.tcmdb import hub as hubmod
    real = hubmod.catalog()
    have = {c.no for c in real}
    stub = tuple(hubmod.SourceCard(no=n, name=f"card {n}", modules=(), url="",
                                   access="snapshot", connector=None, dataset=None,
                                   license="", barriers="", assessment="",
                                   checked="2026-10-01")
                 for n in range(110, 115) if n not in have)
    monkeypatch.setattr(hubmod, "catalog", lambda: real + stub)


def test_the_built_stores_pass_the_acceptance_check(tmp_path, with_cards, clinpgx):
    h, _ = clinpgx
    _pharmvar_zip(h.raw_dir("pharmvar") / "pharmvar_all.zip")
    _cpic_xlsx(h.raw_dir("cpic") / "cpic_gene-drug_pairs.xlsx")
    for key in ("pharmvar", "cpic"):
        h.build(key, log=lambda m: None)
    for key in ("pharmvar", "cpic", "clinpgx"):
        result = h.check(key)
        assert result["ok"], (key, result["problems"])
    assert h.check("clinpgx")["licences"]["variant_drug"]["class"] == "non-commercial"
    assert h.check("cpic")["licences"]["drug_pharmacogene"]["class"] == "open"


def test_the_specs_declare_kinds_readers_and_terms():
    from bioagent.tcmdb.datasets import dataset
    from bioagent.tcmdb.rowkit import RELATION_KINDS
    from bioagent.tcmdb.store import READERS
    assert RELATION_KINDS["drug_pharmacogene"] == ("drug", "gene")
    assert RELATION_KINDS["variant_drug"] == ("variant", "drug")
    assert RELATION_KINDS["allele_variant"] == ("allele", "variant")
    assert "pharmvar_haplotypes" in READERS and "clinpgx:relationships.tsv" in READERS
    terms = {k: (dataset(k).catalog, dataset(k).commercial_use)
             for k in ("pharmvar", "cpic", "clinpgx", "gpcrdb")}
    assert terms == {"pharmvar": ((110,), "forbidden"), "cpic": ((111,), "allowed"),
                     "clinpgx": ((111,), "forbidden"), "gpcrdb": ((112,), "allowed")}
    assert not any(f.optional for k in terms for f in dataset(k).files)
    assert "EMA" in dataset("clinpgx").upstream


# ---------------------------------------------------------------------------- connectors
_VERIFIED = {
    ("cpic", "pairs_for_gene"): ("pair_view", {"genesymbol": "eq.CYP2C19", "limit": 20}),
    ("cpic", "pairs_for_drug"): ("pair_view", {"drugname": "eq.clopidogrel", "limit": 20}),
    ("cpic", "pairs_by_level"): ("pair_view", {"cpiclevel": "eq.A", "limit": 5}),
    ("cpic", "drug"): ("drug", {"name": "eq.clopidogrel"}),
    ("cpic", "gene"): ("gene", {"symbol": "eq.CYP2D6"}),
    ("cpic", "alleles"): ("allele", {"genesymbol": "eq.CYP2C19", "limit": 10}),
    ("cpic", "recommendations"): ("recommendation_view",
                                  {"drugname": "eq.clopidogrel", "limit": 5}),
    ("cpic", "report_files"): ("file_artifact", {"select": "type,filename,url",
                                                 "type": "eq.PAIR"}),
    ("gpcrdb", "protein"): ("protein/adrb2_human/", {}),
    ("gpcrdb", "protein_by_accession"): ("protein/accession/P07550/", {}),
    ("gpcrdb", "drugs"): ("drugs/adrb2_human/", {}),
    ("gpcrdb", "ligands"): ("ligands/gpr35_human/", {}),
    ("gpcrdb", "mutants"): ("mutants/ednrb_human/", {}),
    ("gpcrdb", "structure"): ("structure/2RH1/", {}),
    ("klifs", "kinase_id"): ("api/kinase_ID", {"kinase_name": "EGFR", "species": "HUMAN"}),
    ("klifs", "kinase_names"): ("api/kinase_names", {"kinase_group": "TK",
                                                     "species": "HUMAN"}),
    ("klifs", "ligands"): ("api/ligands_list", {"kinase_ID": "406"}),
    ("klifs", "structures"): ("api/structures_list", {"kinase_ID": "406"}),
    ("klifs", "ligand_bioactivities"): ("api/bioactivity_list_id", {"ligand_ID": "26"}),
    ("klifs", "interaction_fingerprint"): ("api_v2/interactions_get_IFP",
                                           {"structure_ID": "782"}),
    ("klifs", "drugs"): ("api_v2/drug_list", {}),
    ("proteomicsdb", "expression"): (
        "api/proteinexpression.xsodata/InputParams(PROTEINFILTER='P00533',MS_LEVEL=1,"
        "TISSUE_ID_SELECTION='',TISSUE_CATEGORY_SELECTION='tissue;fluid',SCOPE_SELECTION=1,"
        "GROUP_BY_TISSUE=1,CALCULATION_METHOD=0,EXP_ID=-1)/Results", {"$format": "json"}),
    ("proteomicsdb", "protein"): ("api_v2/api.xsodata/Protein",
                                  {"$filter": "UNIQUE_IDENTIFIER eq 'P00533'",
                                   "$format": "json", "$top": 5}),
    ("proteomicsdb", "curves"): ("api_v2/api.xsodata/Protein(51261)/Curve",
                                 {"$format": "json", "$top": 5}),
    ("proteomicsdb", "dose_response_datasets"): ("api_v2/api.xsodata/DoseResponseDataSet",
                                                 {"$format": "json", "$top": 25}),
}


def test_every_connector_operation_renders_the_request_that_was_verified():
    from bioagent.providers.public_apis import render_call
    from bioagent.providers.supplement.pgx_proteins import SOURCES
    ops = {(s.key, o.name) for s in SOURCES for o in s.operations}
    assert ops == set(_VERIFIED)
    for (key, op), (path, params) in _VERIFIED.items():
        call = render_call(key, op)
        assert call["method"] == "GET" and call["path"] == path, (key, op)
        for name, value in params.items():
            assert call["params"][name] == value, (key, op, name)


def test_a_proteomicsdb_cell_line_query_encodes_only_its_argument():
    from bioagent.providers.public_apis import render_call
    call = render_call("proteomicsdb", "expression", uniprot="P04637",
                       category="cell line", method=1)
    assert "PROTEINFILTER='P04637'" in call["path"]
    assert "TISSUE_CATEGORY_SELECTION='cell%20line'" in call["path"]
    assert "CALCULATION_METHOD=1" in call["path"]


def test_the_hosts_are_allowed_paced_and_pharmvar_has_no_connector():
    from bioagent.backends.http import DEFAULT_RATES
    from bioagent.policy import PROFILES
    from bioagent.providers.public_apis import BY_KEY
    from bioagent.providers.supplement.pgx_proteins import PENDING, SOURCES
    from bioagent.tcmdb.datasets import dataset
    profile = PROFILES["biomedical-research"]
    hosts = {s.host for s in SOURCES}
    hosts |= {urllib.parse.urlsplit(f.url).hostname
              for k in ("pharmvar", "cpic", "clinpgx", "gpcrdb") for f in dataset(k).files}
    hosts.add("s3.pgkb.org")                          # where api.clinpgx.org redirects
    ruling = profile.check_network(sorted(hosts))
    assert ruling.decision.value == "ALLOW", ruling.reason
    for s in SOURCES:
        assert DEFAULT_RATES[s.host] == 1.0
        assert s.key in BY_KEY
    assert "pharmvar" not in BY_KEY and PENDING == ()


def test_the_reader_formats_are_named_for_their_source():
    """Formats are registered globally, so each carries its source's name."""
    from bioagent.tcmdb.extra import pgx_proteins
    assert all(fmt.startswith(("pharmvar_", "clinpgx:")) for fmt in pgx_proteins.READERS)
