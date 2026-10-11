"""Reference outputs for the native tools, established independently of their code.

A smoke test shows that a tool returns something; a reference shows that it returns the
right thing. Each reference here is an input and an expectation the tool's own code did
not produce: a published worked value (the 6-MP leukaemia trial's Kaplan–Meier median of
23 weeks, CKD-EPI 2021 re-derived from its equation), arithmetic from the defining formula
(Mosteller's √(h·w/3600), the anion gap), a residue-mass sum, the standard genetic code, or
an independent implementation (SciPy for the statistical tests). A tool without one is
UNVERIFIED for functional correctness, never PASS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = ["Reference", "REFERENCES", "references_for", "check"]

#: (path, expected, tolerance): ``path`` is a dotted key path into the result
Expect = tuple[str, Any, float]


@dataclass(frozen=True)
class Reference:
    tool: str
    args: Mapping[str, Any]
    #: values computed here, independently; or a callable returning them (for SciPy)
    expect: tuple[Expect, ...] | Callable[[], tuple[Expect, ...]]
    source: str
    needs: tuple[str, ...] = field(default_factory=tuple)      # modules the oracle imports


def _get(value: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(value, Mapping):
            value = value[part]
        elif isinstance(value, (list, tuple)):
            value = value[int(part)]
        else:
            raise KeyError(path)
    return value


def check(ref: Reference, result: Any) -> tuple[bool, list[str]]:
    """Compare a tool's result with the reference; the differences, if any."""
    expected = ref.expect() if callable(ref.expect) else ref.expect
    problems = []
    for path, want, tol in expected:
        try:
            got = _get(result, path)
        except (KeyError, IndexError, ValueError, TypeError):
            problems.append(f"{path}: missing")
            continue
        if isinstance(want, float) or (isinstance(want, int) and tol):
            try:
                ok = math.isclose(float(got), float(want), rel_tol=tol, abs_tol=tol)
            except (TypeError, ValueError):
                ok = False
        elif isinstance(want, (list, tuple)) and want and isinstance(want[0], float):
            ok = (isinstance(got, (list, tuple)) and len(got) == len(want)
                  and all(math.isclose(float(g), float(w), rel_tol=tol, abs_tol=tol)
                          for g, w in zip(got, want)))
        else:
            ok = got == want
        if not ok:
            problems.append(f"{path}: {got!r}, expected {want!r}" + (f" (±{tol:g})" if tol else ""))
    return not problems, problems


# ------------------------------------------------------------- independent helpers
_COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")
_CODE = {  # the standard genetic code, NCBI table 1
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L", "CTA": "L",
    "CTG": "L", "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M", "GTT": "V", "GTC": "V",
    "GTA": "V", "GTG": "V", "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S", "CCT": "P",
    "CCC": "P", "CCA": "P", "CCG": "P", "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A", "TAT": "Y", "TAC": "Y", "TAA": "*",
    "TAG": "*", "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q", "AAT": "N", "AAC": "N",
    "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E", "TGT": "C",
    "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R", "GGT": "G", "GGC": "G", "GGA": "G",
    "GGG": "G"}
#: monoisotopic residue masses (Unimod), water 18.010565, proton 1.007276
_MONO = {"S": 87.032028, "A": 71.037114, "M": 131.040485, "P": 97.052764, "L": 113.084064,
         "E": 129.042593, "R": 156.101111}
SEQ = "ATGGCCATTGTAATGGGCCGCTGAAAGGGTGCCCGATAG"


def _translate_to_stop(seq: str) -> str:
    out = []
    for i in range(0, len(seq) - 2, 3):
        aa = _CODE[seq[i:i + 3]]
        if aa == "*":
            break
        out.append(aa)
    return "".join(out)


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _ckd_epi_2021(scr: float, age: float, female: bool) -> float:
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    r = scr / kappa
    return 142 * min(r, 1) ** alpha * max(r, 1) ** -1.200 * 0.9938 ** age * (1.012 if female else 1)


def _pi_pairwise(seqs: list[str]) -> float:
    pairs = [(a, b) for i, a in enumerate(seqs) for b in seqs[i + 1:]]
    return sum(sum(x != y for x, y in zip(a, b)) for a, b in pairs) / len(pairs)


def _scipy_tests() -> dict[str, Callable[[], tuple[Expect, ...]]]:
    def hyper():
        from scipy.stats import hypergeom
        return (("p_value", float(hypergeom.sf(4, 20000, 50, 20)), 1e-6),)

    def fisher():
        from scipy.stats import fisher_exact
        odds, p = fisher_exact([[3, 1], [1, 3]])
        return (("p_value", float(p), 1e-6), ("odds_ratio", float(odds), 1e-9))

    def bh():
        from scipy.stats import false_discovery_control
        q = [float(x) for x in false_discovery_control([0.01, 0.04, 0.03, 0.2])]
        return (("q_values", q, 1e-6),)

    def mwu():
        from scipy.stats import mannwhitneyu
        r = mannwhitneyu([1.2, 2.3, 3.1, 4.8], [5.5, 6.1, 7.2, 8.0], alternative="two-sided",
                         method="asymptotic", use_continuity=False)
        return (("u1", float(r.statistic), 1e-9), ("p_value", float(r.pvalue), 1e-6))

    def welch():
        from scipy.stats import ttest_ind
        r = ttest_ind([1.0, 2.0, 3.0, 4.0, 5.0], [3.0, 4.0, 5.0, 6.0, 7.0], equal_var=False)
        return (("t", float(r.statistic), 1e-9), ("p_value", float(r.pvalue), 1e-6))

    def corr():
        from scipy.stats import pearsonr, spearmanr
        x, y = [1, 2, 3, 4, 5], [2, 4, 5, 4, 5]
        r, p = pearsonr(x, y)
        return (("pearson_r", float(r), 1e-5), ("pearson_p_value", float(p), 1e-5),
                ("spearman_rho", float(spearmanr(x, y)[0]), 1e-5))

    def chi2():
        from scipy.stats import chi2_contingency
        stat, p, df, _ = chi2_contingency([[10, 20], [30, 40]], correction=False)
        return (("chi_square", float(stat), 1e-3), ("p_value", float(p), 1e-5), ("df", int(df), 0))

    def regress():
        from scipy.stats import linregress
        r = linregress([1, 2, 3, 4, 5], [2.1, 3.9, 6.2, 7.8, 10.1])
        return (("slope", float(r.slope), 1e-6), ("intercept", float(r.intercept), 1e-6),
                ("r", float(r.rvalue), 1e-5))

    def anova():
        from scipy.stats import f_oneway
        r = f_oneway([1, 2, 3], [2, 3, 4], [6, 7, 8])
        return (("F", float(r.statistic), 1e-6), ("p_value", float(r.pvalue), 1e-5))

    def kruskal():
        from scipy.stats import kruskal as kw
        r = kw([1, 2, 3], [2, 3, 4], [6, 7, 8])
        return (("H", float(r.statistic), 1e-3), ("p_value", float(r.pvalue), 1e-5))

    def logrank():
        # the 6-MP trial (Freireich 1963): 6-MP arm against placebo, as textbooks tabulate it
        from scipy.stats import chi2 as chi2_dist
        return (("chi_square", 16.79, 0.01), ("p_value", float(chi2_dist.sf(16.79, 1)), 1e-6))
    return {"hypergeometric_test": hyper, "fisher_exact": fisher, "benjamini_hochberg": bh,
            "mann_whitney_u": mwu, "welch_t_test": welch, "correlation": corr,
            "chi_square_test": chi2, "linear_regression": regress, "one_way_anova": anova,
            "kruskal_wallis": kruskal, "log_rank_test": logrank}


_S = _scipy_tests()
_MP6_T = [6, 6, 6, 6, 7, 9, 10, 10, 11, 13, 16, 17, 19, 20, 22, 23, 25, 32, 32, 34, 35]
_MP6_S = [1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0]
_PLACEBO_T = [1, 1, 2, 2, 3, 4, 4, 5, 5, 8, 8, 8, 8, 11, 11, 12, 12, 15, 17, 22, 23]
_SAMPLER = sum(_MONO[a] for a in "SAMPLER") + 18.010565
_DUBOIS = 0.007184 * 70 ** 0.425 * 175 ** 0.725
_HEIGHT_IN = 175 / 2.54

REFERENCES: tuple[Reference, ...] = (
    # ---------------------------------------------------------------- sequences
    Reference("reverse_complement", {"sequence": SEQ},
              (("sequence", SEQ[::-1].translate(_COMPLEMENT), 0),), "base pairing"),
    Reference("transcribe", {"sequence": SEQ}, (("rna", SEQ.replace("T", "U"), 0),),
              "T → U"),
    Reference("translate", {"sequence": SEQ, "to_stop": True},
              (("protein", _translate_to_stop(SEQ), 0),), "the standard genetic code"),
    Reference("gc_content", {"sequence": SEQ},
              (("gc_fraction", sum(c in "GC" for c in SEQ) / len(SEQ), 1e-5),), "count"),
    Reference("hamming_distance", {"a": "ACGTACGT", "b": "ACGAACGA"},
              (("distance", 2, 0), ("identity", 0.75, 1e-9)), "count"),
    Reference("edit_distance", {"a": "GATTACA", "b": "GCATGCU"},
              (("distance", _levenshtein("GATTACA", "GCATGCU"), 0),), "Levenshtein DP"),
    Reference("kmer_counts", {"sequence": SEQ, "k": 2},
              (("total", len(SEQ) - 1, 0),), "n − k + 1 windows"),
    Reference("restriction_sites", {"sequence": "AAGAATTCGGATCCAAGCTTGCGGCCGC",
                                    "enzymes": ["EcoRI", "BamHI", "HindIII"]},
              (("sites.EcoRI", ["AAGAATTCGGATCCAAGCTTGCGGCCGC".find("GAATTC")], 0),
               ("sites.BamHI", ["AAGAATTCGGATCCAAGCTTGCGGCCGC".find("GGATCC")], 0)),
              "recognition sequences GAATTC, GGATCC"),
    Reference("peptide_mass", {"sequence": "SAMPLER", "charges": [1, 2]},
              (("monoisotopic_mass", _SAMPLER, 1e-4), ("mz.1", _SAMPLER + 1.007276, 1e-4),
               ("mz.2", (_SAMPLER + 2 * 1.007276) / 2, 1e-4)),
              "monoisotopic residue masses (Unimod) + water; [M+zH]z+"),
    Reference("annotate_coding_variant", {"cds": SEQ, "position": 4, "ref": "G", "alt": "A"},
              (("hgvs_p", "p.Ala2Thr", 0), ("consequence", "missense", 0)),
              "GCC (Ala) → ACC (Thr), the standard code"),
    Reference("normalise_variant", {"chrom": "chr17", "pos": 7675088, "ref": "CT", "alt": "TT"},
              (("ref", "C", 0), ("alt", "T", 0), ("pos", 7675088, 0)),
              "trim the shared suffix (VCF normalisation)"),
    Reference("transition_transversion",
              {"changes": [["A", "G"], ["C", "T"], ["A", "T"], ["G", "C"]]},
              (("transitions", 2, 0), ("transversions", 2, 0)), "purine↔purine, pyrimidine↔pyrimidine"),
    Reference("allele_frequencies", {"genotypes": ["0/0", "0/1", "0/1", "1/1", "0/0", "./."]},
              (("alt_allele_frequency", 0.4, 1e-9), ("n_called", 5, 0)), "allele count"),
    Reference("parse_fastq", {"text": "@r1\nACGT\n+\nIIII\n@r2\nAAAA\n+\n!!!!\n"},
              (("mean_quality", 20.0, 1e-9), ("q30_fraction", 0.5, 1e-9)),
              "Phred+33: I is 40, ! is 0"),
    Reference("parse_bed", {"text": "chr1\t100\t200\tpeak1\t500\t+\nchr1\t300\t450\tpeak2\t300\t-\n"},
              (("total_span", 250, 0),), "half-open intervals"),
    # ------------------------------------------------------------ population genetics
    Reference("linkage_disequilibrium", {"haplotype_counts": {"AB": 50, "Ab": 10, "aB": 10, "ab": 30}},
              (("D", 0.5 - 0.6 * 0.6, 1e-9), ("D_prime", 0.14 / min(0.6 * 0.4, 0.4 * 0.6), 1e-6),
               ("r_squared", 0.14 ** 2 / (0.6 * 0.4 * 0.6 * 0.4), 1e-6)), "Lewontin D, D′, r²"),
    Reference("fst", {"allele_frequencies": [0.8, 0.3], "sample_sizes": [100, 100]},
              (("H_S", (2 * 0.8 * 0.2 + 2 * 0.3 * 0.7) / 2, 1e-9), ("H_T", 2 * 0.55 * 0.45, 1e-9),
               ("G_ST", (0.495 - 0.37) / 0.495, 1e-6)), "Nei's G_ST"),
    Reference("nucleotide_diversity",
              {"sequences": ["ACGTACGTAC", "ACGTACGTAT", "ACGAACGTAC", "ACGTACCTAC"]},
              (("pi_per_pair", _pi_pairwise(["ACGTACGTAC", "ACGTACGTAT", "ACGAACGTAC",
                                             "ACGTACCTAC"]), 1e-9),), "mean pairwise differences"),
    # ------------------------------------------------------------------ phylogenetics
    Reference("tree_distances", {"newick": "((A:1,B:1):1,C:2);"},
              (("matrix.0.1", 2.0, 1e-9), ("matrix.0.2", 4.0, 1e-9)), "path lengths"),
    Reference("parse_newick", {"newick": "((A:1,B:1):1,C:2);"},
              (("total_branch_length", 5.0, 1e-9), ("n_leaves", 3, 0)), "sum of branch lengths"),
    Reference("upgma", {"names": ["A", "B", "C"], "matrix": [[0, 2, 4], [2, 0, 4], [4, 4, 0]]},
              (("root_height", 2.0, 1e-9),), "UPGMA: half the last merge distance"),
    # ---------------------------------------------------------------------- survival
    Reference("kaplan_meier", {"times": _MP6_T, "status": _MP6_S},
              (("median_survival_time", 23.0, 1e-9), ("survival_at_last_event", 0.448, 1e-3),
               ("events", 9, 0)),
              "the 6-MP arm of Freireich et al. 1963 (Kleinbaum & Klein, Survival Analysis): "
              "median 23 weeks, S(23) = 0.448"),
    Reference("log_rank_test", {"times_a": _MP6_T, "status_a": _MP6_S, "times_b": _PLACEBO_T,
                                "status_b": [1] * 21},
              _S["log_rank_test"], "6-MP against placebo: χ² 16.79 on 1 df (Kleinbaum & Klein)",
              ("scipy",)),
    # --------------------------------------------------------------------- statistics
    *(Reference(name, args, _S[name], "SciPy, an independent implementation", ("scipy",))
      for name, args in (
          ("hypergeometric_test", {"overlap": 5, "query_size": 20, "set_size": 50,
                                   "background": 20000}),
          ("fisher_exact", {"a": 3, "b": 1, "c": 1, "d": 3}),
          ("benjamini_hochberg", {"p_values": [0.01, 0.04, 0.03, 0.2]}),
          ("mann_whitney_u", {"x": [1.2, 2.3, 3.1, 4.8], "y": [5.5, 6.1, 7.2, 8.0]}),
          ("welch_t_test", {"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [3.0, 4.0, 5.0, 6.0, 7.0]}),
          ("correlation", {"x": [1, 2, 3, 4, 5], "y": [2, 4, 5, 4, 5]}),
          ("chi_square_test", {"table": [[10, 20], [30, 40]]}),
          ("linear_regression", {"x": [1, 2, 3, 4, 5], "y": [2.1, 3.9, 6.2, 7.8, 10.1]}),
          ("one_way_anova", {"groups": [[1, 2, 3], [2, 3, 4], [6, 7, 8]]}),
          ("kruskal_wallis", {"groups": [[1, 2, 3], [2, 3, 4], [6, 7, 8]]}))),
    Reference("odds_ratio", {"a": 20, "b": 80, "c": 10, "d": 90},
              (("odds_ratio", 20 * 90 / (80 * 10), 1e-9),
               ("ci95.0", math.exp(math.log(2.25) - 1.959964 * math.sqrt(1 / 20 + 1 / 80 + 1 / 10 + 1 / 90)), 1e-3)),
              "ad/bc; Woolf's log interval"),
    Reference("relative_risk", {"exposed_events": 20, "exposed_total": 100, "unexposed_events": 10,
                                "unexposed_total": 100},
              (("relative_risk", 2.0, 1e-9), ("absolute_risk_difference", 0.1, 1e-9)), "ratio of risks"),
    Reference("diagnostic_metrics", {"tp": 90, "fp": 10, "fn": 5, "tn": 95},
              (("sensitivity", 90 / 95, 1e-5), ("specificity", 95 / 105, 1e-5), ("ppv", 0.9, 1e-9),
               ("npv", 0.95, 1e-9)), "the 2×2 definitions"),
    Reference("roc_auc", {"scores": [0.1, 0.4, 0.35, 0.8, 0.9], "labels": [0, 0, 1, 1, 1]},
              (("auc", 5 / 6, 1e-5),), "the share of positive–negative pairs ranked correctly"),
    Reference("number_needed_to_treat", {"control_event_rate": 0.2, "experimental_event_rate": 0.15},
              (("nnt", 20.0, 1e-9), ("relative_risk_reduction", 0.25, 1e-9)), "1 / ARR"),
    Reference("diversity", {"counts": [10, 20, 30, 40]},
              (("shannon", -sum(p * math.log(p) for p in (0.1, 0.2, 0.3, 0.4)), 1e-5),
               ("simpson", 1 - sum(p * p for p in (0.1, 0.2, 0.3, 0.4)), 1e-9)), "Shannon H′, Gini–Simpson"),
    Reference("tpm", {"counts": [100, 200, 700], "lengths_bp": [1000, 2000, 500]},
              (("values", [62500.0, 62500.0, 875000.0], 1e-6),), "reads per kb, per million"),
    Reference("cohens_d", {"x": [2, 4, 4, 4, 5, 5, 7, 9], "y": [1, 2, 2, 3, 3, 4, 5, 6]},
              (("mean_difference", 1.75, 1e-9),
               ("pooled_sd", math.sqrt((7 * 4.571428571 + 7 * 2.785714286) / 14), 1e-5)),
              "pooled SD with n − 1"),
    # ------------------------------------------------------------------- pharmacology
    Reference("pk_one_compartment", {"dose_mg": 500, "volume_l": 40, "half_life_h": 6,
                                     "times_h": [0, 6, 12, 24]},
              (("k_per_h", math.log(2) / 6, 1e-6), ("c0_mg_per_l", 12.5, 1e-9),
               ("auc_0_inf_mg_h_per_l", 12.5 / (math.log(2) / 6), 1e-4)), "C0 = D/V, AUC = C0/k"),
    Reference("half_life_from_levels", {"c1": 10, "t1_h": 2, "c2": 2.5, "t2_h": 10},
              (("half_life_h", 4.0, 1e-6),), "t½ = ln2 · Δt / ln(c1/c2)"),
    Reference("loading_dose", {"target_concentration_mg_per_l": 15,
                               "volume_of_distribution_l_per_kg": 0.7, "weight_kg": 70},
              (("loading_dose_mg", 735.0, 1e-9),), "Vd × C"),
    Reference("maintenance_dose", {"target_css_mg_per_l": 10, "clearance_l_per_h": 3, "interval_h": 8},
              (("dose_per_interval_mg", 240.0, 1e-9),), "CL × Css × τ"),
    Reference("steady_state", {"half_life_h": 6, "interval_h": 6},
              (("accumulation_ratio", 2.0, 1e-6),), "1 / (1 − e^(−kτ))"),
    Reference("carboplatin_calvert", {"target_auc_mg_ml_min": 5, "gfr_ml_min": 80},
              (("dose_mg", 525.0, 1e-9),), "Calvert: AUC × (GFR + 25)"),
    # --------------------------------------------------------------- clinical formulae
    Reference("bmi", {"weight_kg": 70, "height_cm": 175}, (("bmi", 70 / 1.75 ** 2, 5e-3),),
              "kg/m²"),
    Reference("body_surface_area", {"weight_kg": 70, "height_cm": 175},
              (("mosteller_m2", math.sqrt(175 * 70 / 3600), 1e-3), ("dubois_m2", _DUBOIS, 1e-3)),
              "Mosteller 1987; Du Bois 1916"),
    Reference("ideal_body_weight", {"height_cm": 175, "sex": "male", "actual_weight_kg": 90},
              (("ideal_body_weight_kg", 50 + 2.3 * (_HEIGHT_IN - 60), 0.06),), "Devine 1974"),
    Reference("egfr_ckd_epi_2021", {"creatinine_mg_dl": 1.0, "age_years": 50, "sex": "female"},
              (("egfr_ml_min_1_73m2", _ckd_epi_2021(1.0, 50, True), 0.06),),
              "CKD-EPI 2021 (Inker et al., NEJM 2021), from its equation"),
    Reference("creatinine_clearance_cockcroft_gault",
              {"creatinine_mg_dl": 1.0, "age_years": 50, "weight_kg": 70, "sex": "male"},
              (("creatinine_clearance_ml_min", (140 - 50) * 70 / 72, 0.06),), "Cockcroft–Gault 1976"),
    Reference("fractional_excretion_sodium", {"urine_sodium": 20, "plasma_sodium": 140,
                                              "urine_creatinine": 100, "plasma_creatinine": 1.0},
              (("fena_percent", 20 * 1.0 / (140 * 100) * 100, 6e-3),), "FENa"),
    Reference("corrected_calcium", {"calcium_mg_dl": 8.0, "albumin_g_dl": 2.0},
              (("corrected_calcium_mg_dl", 8.0 + 0.8 * 2.0, 1e-9),), "Payne: Ca + 0.8 (4 − albumin)"),
    Reference("anion_gap", {"sodium": 140, "chloride": 100, "bicarbonate": 24, "albumin_g_dl": 3.0},
              (("anion_gap", 16.0, 1e-9), ("albumin_corrected_gap", 18.5, 1e-9)),
              "Na − (Cl + HCO3); + 2.5 per g/dL albumin below 4"),
    Reference("corrected_sodium", {"sodium": 130, "glucose_mg_dl": 600},
              (("corrected_sodium", 130 + 1.6 * 5, 1e-9),), "Katz: 1.6 per 100 mg/dL glucose"),
    Reference("henderson_hasselbalch", {"bicarbonate": 24, "pco2_mmHg": 40},
              (("ph", 6.1 + math.log10(24 / (0.03 * 40)), 1e-3),), "pH = 6.1 + log(HCO3 / 0.03 pCO2)"),
    Reference("alveolar_gas", {"fio2": 0.21, "paco2_mmHg": 40, "pao2_mmHg": 90, "age_years": 40},
              (("alveolar_po2_mmHg", 0.21 * (760 - 47) - 40 / 0.8, 0.06),), "the alveolar gas equation"),
    Reference("qtc", {"qt_ms": 400, "heart_rate_bpm": 75},
              (("bazett_ms", 400 / math.sqrt(0.8), 0.06), ("fridericia_ms", 400 / 0.8 ** (1 / 3), 0.06)),
              "Bazett, Fridericia"),
    Reference("mean_arterial_pressure", {"systolic": 120, "diastolic": 80},
              (("map_mmHg", (120 + 2 * 80) / 3, 0.06),), "(SBP + 2 DBP) / 3"),
    Reference("friedewald_ldl", {"total_cholesterol_mg_dl": 200, "hdl_mg_dl": 50,
                                 "triglycerides_mg_dl": 150},
              (("ldl_mg_dl", 200 - 50 - 150 / 5, 1e-9),), "Friedewald 1972"),
    Reference("hba1c_to_eag", {"hba1c_percent": 7.0},
              (("eag_mg_dl", 28.7 * 7 - 46.7, 0.06),), "ADAG: 28.7 A1c − 46.7"),
    Reference("basal_metabolic_rate", {"weight_kg": 70, "height_cm": 175, "age_years": 40,
                                       "sex": "male", "activity_factor": 1.4},
              (("bmr_kcal_day", round(10 * 70 + 6.25 * 175 - 5 * 40 + 5), 1.0),), "Mifflin–St Jeor"),
    Reference("parkland_formula", {"weight_kg": 70, "tbsa_burned_percent": 20},
              (("total_24h_ml", 4 * 70 * 20, 0),), "4 mL × kg × %TBSA"),
    Reference("holliday_segar", {"weight_kg": 25},
              (("ml_per_hour", 4 * 10 + 2 * 10 + 1 * 5, 1e-9),), "4-2-1"),
    Reference("free_water_deficit", {"weight_kg": 70, "sodium": 154, "sex": "male"},
              (("free_water_deficit_l", 0.6 * 70 * (154 / 140 - 1), 1e-6),), "TBW × (Na/140 − 1)"),
    Reference("calculated_osmolality", {"sodium": 140, "glucose_mg_dl": 90, "bun_mg_dl": 14,
                                        "measured_osmolality": 300},
              (("calculated_osmolality_mosm_kg", 2 * 140 + 90 / 18 + 14 / 2.8, 1e-6),),
              "2 Na + glucose/18 + BUN/2.8"),
    Reference("winters_formula", {"bicarbonate": 12, "pco2_mmHg": 26},
              (("expected_pco2_mmHg", 1.5 * 12 + 8, 1e-9),), "1.5 HCO3 + 8"),
    Reference("fib4", {"age_years": 60, "ast_u_l": 40, "alt_u_l": 40, "platelets_10e9_per_l": 150},
              (("fib4", 60 * 40 / (150 * math.sqrt(40)), 6e-3),), "Sterling 2006"),
    Reference("apri", {"ast_u_l": 80, "ast_upper_limit_normal_u_l": 40, "platelets_10e9_per_l": 100},
              (("apri", (80 / 40) / 100 * 100, 1e-9),), "Wai 2003"),
    Reference("homa_ir", {"fasting_glucose_mg_dl": 100, "fasting_insulin_uU_ml": 10},
              (("homa_ir", 100 * 10 / 405, 6e-3),), "Matthews 1985"),
    Reference("glasgow_coma_scale", {"eyes": 3, "verbal": 4, "motor": 5}, (("total", 12, 0),), "E + V + M"),
    Reference("infusion_rate", {"volume_ml": 1000, "duration_min": 480, "drop_factor_gtt_per_ml": 20},
              (("ml_per_hour", 125.0, 1e-9),), "volume / time"),
    Reference("weight_based_dose", {"dose_mg_per_kg": 15, "weight_kg": 20, "max_dose_mg": 1000,
                                    "doses_per_day": 4},
              (("dose_mg", 300.0, 1e-9), ("daily_total_mg", 1200.0, 1e-9)), "mg/kg × kg"),
)


def references_for(tool: str) -> list[Reference]:
    return [r for r in REFERENCES if r.tool == tool]
