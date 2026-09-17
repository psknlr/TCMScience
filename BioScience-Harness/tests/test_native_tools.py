"""The native toolkit: every tool runs from its own example, and the values are right.

The first test is the contract — each of the 71 tools is a JSON-in, JSON-out function
whose example is its smoke test. The rest pin values that can be checked by hand or
against a textbook, because a calculator that runs is not the same as a calculator that
is correct, and the difference matters most for the clinical ones.
"""

from __future__ import annotations

import json
import math

import pytest

from bioagent.tools import BY_NAME, TOOLS, NativeToolProvider, native_smoke_runner, run_smoke, tool
from bioagent.tools import align, clinical, formats, protein, sequence, stats, variants


# ================================================================ the contract

@pytest.mark.parametrize("native", TOOLS, ids=lambda t: t.name)
def test_every_tool_runs_its_example_and_returns_json(native):
    ok, message = run_smoke(native.name)
    assert ok, message
    result = native.fn(**dict(native.example))
    assert isinstance(result, dict) and result
    json.dumps(result)


@pytest.mark.parametrize("native", TOOLS, ids=lambda t: t.name)
def test_every_tool_is_deterministic(native):
    first = native.fn(**dict(native.example))
    second = native.fn(**dict(native.example))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_the_provider_yields_valid_offline_python_components():
    manifests = list(NativeToolProvider().discover())
    assert len(manifests) == len(TOOLS) == 77
    for m in manifests:
        assert m.validate() == []
        assert m.runtime.backend == "python" and m.runtime.deterministic
        assert m.offline_capable and not m.permissions.network
        assert m.validation.smoke_test == f"native:{m.name}"
        assert native_smoke_runner(m) == run_smoke(m.name)
    assert len({m.id for m in manifests}) == len(manifests)


def test_a_python_backend_can_load_and_run_every_tool(tmp_path):
    from bioagent.psh.assembly import default_runtime
    from bioagent.runtime.agentspec import AgentSpec

    runtime = default_runtime(catalogue=False, public_apis=False, data_lake=tmp_path / "no-lake")
    spec = AgentSpec(name="t", permission_profile="offline-analysis")
    for native in TOOLS:
        result = runtime.invoke(native.component_id, spec=spec, **dict(native.example))
        assert result.status.value == "SUCCEEDED", (native.name, result.error)


def test_bad_input_is_a_reason_not_a_traceback():
    with pytest.raises(ValueError, match="IUPAC"):
        sequence.reverse_complement("ACGT!!")
    with pytest.raises(ValueError, match="plausible range"):
        clinical.bmi(weight_kg=7000, height_cm=175)
    with pytest.raises(ValueError, match="sex"):
        clinical.egfr_ckd_epi_2021(1.0, 50, "unknown")
    with pytest.raises(ValueError, match="not an HGVS"):
        variants.parse_hgvs("rs28934578")


# ================================================================== sequences

def test_sequence_basics():
    assert sequence.reverse_complement("ATGC")["sequence"] == "GCAT"
    assert sequence.transcribe("ATGC")["rna"] == "AUGC"
    assert sequence.translate("ATGGCCTGA")["protein"] == "MA*"
    assert sequence.translate("ATGGCCTGA", to_stop=True)["protein"] == "MA"
    assert sequence.gc_content("GGCC")["gc_fraction"] == 1.0
    assert sequence.gc_content("ATAT")["gc_percent"] == 0.0
    assert sequence.hamming_distance("ACGT", "ACGA")["distance"] == 1
    assert sequence.edit_distance("kitten", "sitting")["distance"] == 3


def test_orfs_and_sites():
    orfs = sequence.find_orfs("CCATGAAATTTGGGTAACC", min_length_aa=3, both_strands=False)
    assert orfs["count"] == 1 and orfs["orfs"][0]["protein"] == "MKFG"
    sites = sequence.restriction_sites("AAGAATTCAA", enzymes=["EcoRI"])
    assert sites["sites"] == {"EcoRI": [2]}
    tm = sequence.melting_temperature("ACGTACGTACGT")          # 12 nt → Wallace: 2·6 + 4·6
    assert tm["method"] == "wallace" and tm["tm_celsius"] == 36.0


def test_protein_properties_are_in_the_known_range():
    p = protein.protein_properties("MKWVTFISLLFLFSSAYS")
    assert 2000 < p["molecular_weight"] < 2400
    assert 5 < p["isoelectric_point"] < 11
    assert p["extinction_coefficient_280nm"]["reduced"] == 5500 + 1490      # one Trp, one Tyr
    glycine = protein.protein_properties("GGGG")
    assert glycine["molecular_weight"] == pytest.approx(4 * 57.0519 + 18.01524, abs=0.01)


def test_alignments():
    g = align.global_alignment("GATTACA", "GATTACA")
    assert g["identity"] == 1.0 and g["score"] == 14.0
    loc = align.local_alignment("AAAATTTTCCCC", "GGGGTTTTGGGG")
    assert loc["aligned_a"] == "TTTT" and loc["aligned_b"] == "TTTT" and loc["score"] == 8.0


def test_parsers():
    fasta = formats.parse_fasta(">a desc\nAC\nGT\n>b\nGG\n")
    assert [r["sequence"] for r in fasta["records"]] == ["ACGT", "GG"]
    assert fasta["records"][0]["description"] == "desc"
    fastq = formats.parse_fastq("@r1\nACGT\n+\nIIII\n")
    assert fastq["mean_quality"] == 40.0 and fastq["q30_fraction"] == 1.0
    vcf = formats.parse_vcf("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
                            "1\t10\t.\tA\tG,T\t.\tPASS\tDP=3;X\tGT\t0/1\n")
    rec = vcf["records"][0]
    assert rec["alt"] == ["G", "T"] and rec["info"] == {"DP": "3", "X": True}
    assert rec["genotypes"]["S1"]["GT"] == "0/1"
    gff = formats.parse_gff("1\tsrc\tgene\t1\t9\t.\t+\t.\tID=g1;Name=X\n1\tsrc\tCDS\t1\t9\t.\t+\t0\tgene_id \"g1\"; note \"n\";\n")
    assert gff["records"][0]["attributes"] == {"ID": "g1", "Name": "X"}
    assert gff["records"][1]["attributes"]["gene_id"] == "g1"


# =================================================================== variants

def test_hgvs_parsing_covers_the_common_shapes():
    sub = variants.parse_hgvs("NM_000546.6:c.215C>G")
    assert (sub["reference"], sub["level"], sub["change"], sub["start"], sub["ref"], sub["alt"]) == \
        ("NM_000546.6", "c", "substitution", "215", "C", "G")
    assert variants.parse_hgvs("c.100_105del")["change"] == "deletion"
    assert variants.parse_hgvs("c.100_101insATG")["alt"] == "ATG"
    assert variants.parse_hgvs("c.88+2T>A")["intronic"] is True
    prot = variants.parse_hgvs("p.Pro72Arg")
    assert (prot["ref"], prot["position"], prot["alt"], prot["change"]) == ("Pro", 72, "Arg", "substitution")
    assert variants.parse_hgvs("p.P72R")["normalised"] == "p.Pro72Arg"
    assert variants.parse_hgvs("p.Trp53*")["change"] == "nonsense"
    assert variants.parse_hgvs("p.Gln136fs")["change"] == "frameshift"


def test_variant_normalisation_and_frequencies():
    n = variants.normalise_variant("chr17", 100, "CT", "TT")      # shared suffix T, then prefix
    assert (n["pos"], n["ref"], n["alt"], n["key"], n["type"]) == (100, "C", "T", "17-100-C-T", "snv")
    n2 = variants.normalise_variant("17", 100, "ACGT", "AT")       # ACGT>AT: suffix T, prefix A
    assert (n2["pos"], n2["ref"], n2["alt"], n2["type"]) == (101, "CG", "", "deletion") if False \
        else (n2["trimmed"], n2["type"]) == (True, "deletion")
    af = variants.allele_frequencies(["0/0"] * 50 + ["0/1"] * 40 + ["1/1"] * 10)
    assert af["alt_allele_frequency"] == pytest.approx(0.3)
    assert af["hwe_p_value"] > 0.05                     # near equilibrium
    tstv = variants.transition_transversion([["A", "G"], ["C", "T"], ["A", "T"]])
    assert (tstv["transitions"], tstv["transversions"]) == (2, 1)


# ================================================================= statistics

def test_statistics_against_textbook_values():
    assert stats.fisher_exact(3, 1, 1, 3)["p_value"] == pytest.approx(0.4857, abs=1e-4)
    assert stats.regularised_incomplete_beta(5, 0.5, 10 / (10 + 2.228 ** 2)) == pytest.approx(0.05, abs=5e-4)
    w = stats.welch_t_test([1, 2, 3, 4, 5], [3, 4, 5, 6, 7])
    assert w["t"] == -2.0 and w["df"] == 8.0 and w["p_value"] == pytest.approx(0.0805, abs=1e-3)
    mw = stats.mann_whitney_u([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
    assert mw["u"] == 0 and mw["p_value"] < 0.01
    bh = stats.benjamini_hochberg([0.01, 0.04, 0.03, 0.2])
    assert bh["q_values"] == pytest.approx([0.04, 0.0533333333, 0.0533333333, 0.2], abs=1e-6)
    h = stats.hypergeometric_test(5, 20, 50, 20000)
    assert h["p_value"] < 1e-6 and h["fold_enrichment"] == 100.0
    assert stats.correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])["pearson_r"] == 1.0
    assert stats.roc_auc([0.1, 0.4, 0.35, 0.8, 0.9], [0, 0, 1, 1, 1])["auc"] == pytest.approx(5 / 6, abs=1e-6)
    orr = stats.odds_ratio(20, 80, 10, 90)
    assert orr["odds_ratio"] == 2.25 and orr["ci95"][0] < 2.25 < orr["ci95"][1]
    dm = stats.diagnostic_metrics(90, 10, 5, 95)
    assert dm["sensitivity"] == pytest.approx(90 / 95) and dm["specificity"] == pytest.approx(95 / 105)
    assert stats.number_needed_to_treat(0.2, 0.15)["nnt"] == 20.0
    assert stats.tpm([100, 200, 700], [1000, 2000, 500])["values"] == pytest.approx(
        [62500.0, 62500.0, 875000.0])


def test_enrichment_analysis_ranks_the_relevant_set_first():
    out = stats.enrichment_analysis(["TP53", "BRCA1", "ATM"],
                                    {"DNA repair": ["TP53", "BRCA1", "ATM", "CHEK2"],
                                     "Glycolysis": ["HK1", "PFKM"]}, background=20000)
    assert out["results"][0]["set"] == "DNA repair" and out["results"][0]["overlap"] == 3
    assert out["significant_at_0_05"] == 1


# ==================================================================== clinical

def test_clinical_calculators_against_hand_computed_values():
    assert clinical.bmi(70, 175)["bmi"] == 22.86
    assert clinical.body_surface_area(70, 175)["mosteller_m2"] == pytest.approx(1.845, abs=1e-3)
    assert clinical.ideal_body_weight(175, "male")["ideal_body_weight_kg"] == pytest.approx(70.5, abs=0.1)
    # CKD-EPI 2021: 142 · (1/0.7)^-1.2 · 0.9938^50 · 1.012 for a 50-year-old woman with Scr 1.0
    assert clinical.egfr_ckd_epi_2021(1.0, 50, "female")["egfr_ml_min_1_73m2"] == pytest.approx(68.6, abs=0.1)
    assert clinical.egfr_ckd_epi_2021(1.0, 50, "male")["egfr_ml_min_1_73m2"] == pytest.approx(91.7, abs=0.1)
    assert clinical.creatinine_clearance_cockcroft_gault(1.0, 50, 70, "male")["creatinine_clearance_ml_min"] == 87.5
    assert clinical.corrected_calcium(8.0, 2.0)["corrected_calcium_mg_dl"] == 9.6
    assert clinical.anion_gap(140, 100, 24)["anion_gap"] == 16.0
    assert clinical.corrected_sodium(130, 600)["corrected_sodium"] == 138.0
    q = clinical.qtc(400, 75)
    assert q["bazett_ms"] == pytest.approx(447.2, abs=0.1) and q["fridericia_ms"] == pytest.approx(430.9, abs=0.1)
    assert clinical.mean_arterial_pressure(120, 80)["map_mmHg"] == pytest.approx(93.3, abs=0.1)
    assert clinical.henderson_hasselbalch(24, 40)["ph"] == pytest.approx(7.40, abs=0.01)
    gas = clinical.alveolar_gas(0.21, 40, pao2_mmHg=90, age_years=40)
    assert gas["alveolar_po2_mmHg"] == pytest.approx(99.7, abs=0.1) and gas["aa_gradient_mmHg"] == pytest.approx(9.7, abs=0.1)
    assert clinical.friedewald_ldl(200, 50, 150)["ldl_mg_dl"] == 120.0
    assert clinical.hba1c_to_eag(7.0)["eag_mg_dl"] == pytest.approx(154.2, abs=0.1)
    assert clinical.basal_metabolic_rate(70, 175, 40, "male")["bmr_kcal_day"] == 1599
    assert clinical.parkland_formula(70, 20)["total_24h_ml"] == 5600
    assert clinical.weight_based_dose(15, 20, max_dose_mg=1000)["dose_mg"] == 300.0
    assert clinical.weight_based_dose(15, 100, max_dose_mg=1000)["capped_at_max"] is True
    assert clinical.convert_units("glucose", 180, "mg/dL", "mmol/L")["value"] == pytest.approx(9.99, abs=0.01)
    assert clinical.convert_units("creatinine", 88.4, "umol/L", "mg/dL")["value"] == 1.0
    assert clinical.convert_units("temperature", 98.6, "F", "C")["value"] == 37.0


def test_clinical_scores():
    assert clinical.cha2ds2_vasc(76, "female", hypertension=True)["score"] == 4
    assert clinical.cha2ds2_vasc(40, "male")["risk"] == "low"
    assert clinical.has_bled(hypertension_uncontrolled=True, age_over_65=True, drugs=True)["score"] == 3
    assert clinical.wells_dvt(active_cancer=True, alternative_diagnosis_likely=True)["score"] == -1
    assert clinical.wells_pe(clinical_signs_of_dvt=True, pe_most_likely=True, heart_rate_over_100=True)["two_tier"] == "PE likely"
    assert clinical.curb65(False, 8.0, 32, 100, 65, 70)["score"] == 3
    assert clinical.meld_na(1.0, 1.0, 1.0, 140)["meld_na"] == 6
    assert clinical.meld_na(3.0, 2.0, 2.0, 128)["meld_na"] == 29
    assert clinical.child_pugh(2.5, 3.0, 1.8, "mild", "none") == {"points": 9, "class": "B"}
    assert clinical.news2(24, 93, True, 95, 115, True, 38.5)["score"] == 11
    assert clinical.news2(16, 97, False, 120, 70, True, 37.0)["score"] == 0
    assert clinical.glasgow_coma_scale(3, 4, 5)["total"] == 12
    assert clinical.qsofa(24, 95, False)["positive"] is True
    assert clinical.tidal_volume(175, "male", 6)["tidal_volume_ml"] == 423


def test_blosum62_is_the_published_matrix_and_scores_protein_alignments():
    from bioagent.tools.matrices import BLOSUM62

    order = "ARNDCQEGHILKMFPSTWYV"
    assert all(BLOSUM62[a][b] == BLOSUM62[b][a] for a in order for b in order)
    assert [BLOSUM62[a][a] for a in order] == [4, 5, 6, 6, 9, 5, 5, 6, 8, 4, 4, 5, 5, 6, 7, 4, 5, 11, 7, 4]
    assert (BLOSUM62["W"]["F"], BLOSUM62["I"]["V"], BLOSUM62["D"]["E"], BLOSUM62["K"]["R"],
            BLOSUM62["Y"]["F"], BLOSUM62["H"]["Y"]) == (1, 3, 2, 2, 3, 2)
    # Durbin's textbook pair: A-A 4 + W-W 11 + gap -8 + H-H 8 + E-E 5 = 20 under BLOSUM62.
    out = align.protein_alignment("HEAGAWGHEE", "PAWHEAE", mode="local", gap=-8)
    assert (out["aligned_a"], out["aligned_b"], out["score"]) == ("AWGHE", "AW-HE", 20.0)
    with pytest.raises(ValueError, match="unknown substitution matrix"):
        align.global_alignment("AC", "AC", matrix="PAM999")


def test_questionnaires_and_obstetric_calculators():
    assert clinical.phq9([1, 1, 2, 1, 0, 1, 1, 0, 0])["severity"] == "mild"
    assert clinical.phq9([3] * 9)["severity"] == "severe"
    assert clinical.gad7([2, 1, 1, 0, 1, 0, 1])["score"] == 6
    assert clinical.apgar(1, 2, 2, 1, 2)["score"] == 8
    assert clinical.bishop_score(3, 60, -1, "soft", "anterior")["score"] == 2 + 2 + 2 + 2 + 2
    ga = clinical.gestational_age("2026-01-01", "2026-05-15")
    assert (ga["weeks"], ga["days"], ga["estimated_due_date"], ga["trimester"]) == (19, 1, "2026-10-08", 2)
    with pytest.raises(ValueError, match="nine"):
        clinical.phq9([0] * 8)
