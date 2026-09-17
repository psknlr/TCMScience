"""Native tools: bioinformatics and clinical capabilities that run here, offline.

The capability census is honest about its central number: of 2,567 catalogued
capabilities, the ones executable on a machine without a Biomni checkout, a container
runtime or forty third-party imports were the datasets. This package is the first
tranche of tools that are executable *anywhere the harness runs*: pure Python, no
dependencies, deterministic, each with an example that doubles as its smoke test.

Each tool is a function of keyword arguments returning a JSON-serialisable dict, so it is
at once a BioScience ``python`` component (``NativeToolProvider`` discovers them), a PSH
component through the bridge (``LOCAL_COMPUTE``, the local ceiling — a clinical
calculator may see PHI because nothing leaves the machine), and an entrypoint the PSH
isolated executor can run in a child process.

    from bioagent.tools import TOOLS, run_smoke, tool
    tool("egfr_ckd_epi_2021").fn(creatinine_mg_dl=1.0, age_years=50, sex="female")
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping

from . import align, clinical, formats, protein, sequence, stats, variants

__all__ = ["NativeTool", "TOOLS", "BY_NAME", "NativeToolProvider", "run_smoke", "tool",
           "native_smoke_runner", "DOMAINS"]

#: Domain → omics type used on the manifests (and, through the bridge, the harness).
DOMAINS: Mapping[str, str] = {
    "sequence-analysis": "genomics", "protein-analysis": "proteomics",
    "sequence-alignment": "genomics", "file-formats": "general",
    "variant-analysis": "genomics", "statistics": "general",
    "clinical-calculators": "clinical",
}


@dataclass(frozen=True)
class NativeTool:
    """One tool: the function, where it belongs, and the example that proves it runs."""

    name: str
    fn: Callable[..., dict[str, Any]]
    domain: str
    example: Mapping[str, Any]
    tags: tuple[str, ...] = ()
    description: str = field(default="")

    def __post_init__(self) -> None:
        if self.domain not in DOMAINS:
            raise ValueError(f"{self.name}: unknown domain {self.domain!r}")
        if not self.description:
            doc = (self.fn.__doc__ or "").strip().split("\n", 1)[0].strip()
            object.__setattr__(self, "description", doc or self.name)

    @property
    def component_id(self) -> str:
        return f"native.tool.{self.name}"

    @property
    def entrypoint(self) -> str:
        return f"{self.fn.__module__}:{self.fn.__name__}"

    @property
    def parameters(self) -> list[dict[str, Any]]:
        out = []
        for p in inspect.signature(self.fn).parameters.values():
            item: dict[str, Any] = {"name": p.name, "required": p.default is inspect._empty}
            if p.default is not inspect._empty:
                item["default"] = p.default
            out.append(item)
        return out


def _t(name: str, fn: Callable[..., Any], domain: str, example: Mapping[str, Any],
       *tags: str) -> NativeTool:
    return NativeTool(name=name, fn=fn, domain=domain, example=example, tags=tuple(tags))


_DNA = "ATGGCCATTGTAATGGGCCGCTGAAAGGGTGCCCGATAG"
_PROT = "MEEPQSDPSVEPPLSQETFSDLWKLLPENNVLSPLPSQAMDDLMLSPDDIEQWFTEDPGP"
_FASTA = ">seq1 test\nATGGCC\nATTG\n>seq2\nGGGCCC\n"
_FASTQ = "@r1\nACGT\n+\nIIII\n@r2\nAAAA\n+\n!!!!\n"
_VCF = ("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
        "17\t7675088\trs28934578\tC\tT\t50\tPASS\tDP=20;AF=0.5\tGT:DP\t0/1:10\t1/1:12\n")
_BED = "chr1\t100\t200\tpeak1\t500\t+\nchr1\t300\t450\tpeak2\t300\t-\n"
_GFF = ("chr1\tENSEMBL\tgene\t1000\t2000\t.\t+\t.\tID=gene1;Name=TP53\n"
        "chr1\tENSEMBL\texon\t1000\t1200\t.\t+\t.\tParent=gene1\n")

TOOLS: tuple[NativeTool, ...] = (
    # ------------------------------------------------------------- sequences
    _t("reverse_complement", sequence.reverse_complement, "sequence-analysis",
       {"sequence": _DNA}, "dna", "rna"),
    _t("transcribe", sequence.transcribe, "sequence-analysis", {"sequence": _DNA}, "dna", "rna"),
    _t("translate", sequence.translate, "sequence-analysis", {"sequence": _DNA, "to_stop": True},
       "dna", "protein", "codon"),
    _t("gc_content", sequence.gc_content, "sequence-analysis", {"sequence": _DNA, "window": 10},
       "dna", "composition"),
    _t("find_orfs", sequence.find_orfs, "sequence-analysis",
       {"sequence": _DNA, "min_length_aa": 5}, "dna", "orf", "gene-finding"),
    _t("kmer_counts", sequence.kmer_counts, "sequence-analysis", {"sequence": _DNA, "k": 2},
       "dna", "kmer"),
    _t("hamming_distance", sequence.hamming_distance, "sequence-analysis",
       {"a": "ACGTACGT", "b": "ACGAACGA"}, "distance"),
    _t("edit_distance", sequence.edit_distance, "sequence-analysis",
       {"a": "GATTACA", "b": "GCATGCU"}, "distance", "levenshtein"),
    _t("codon_usage", sequence.codon_usage, "sequence-analysis", {"sequence": _DNA}, "codon"),
    _t("melting_temperature", sequence.melting_temperature, "sequence-analysis",
       {"sequence": "AGCGTCGATTGACCTGACGTAG"}, "primer", "pcr"),
    _t("restriction_sites", sequence.restriction_sites, "sequence-analysis",
       {"sequence": "AAGAATTCGGATCCAAGCTTGCGGCCGC", "enzymes": ["EcoRI", "BamHI", "HindIII"]},
       "cloning", "restriction"),
    _t("nucleic_acid_weight", sequence.nucleic_acid_weight, "sequence-analysis",
       {"sequence": "ACGTACGT", "kind": "dna"}, "oligo", "mass"),
    # -------------------------------------------------------------- proteins
    _t("protein_properties", protein.protein_properties, "protein-analysis",
       {"sequence": _PROT}, "protein", "pi", "mass", "gravy"),
    _t("hydropathy_profile", protein.hydropathy_profile, "protein-analysis",
       {"sequence": _PROT, "window": 9}, "protein", "hydropathy", "transmembrane"),
    # ------------------------------------------------------------- alignment
    _t("global_alignment", align.global_alignment, "sequence-alignment",
       {"a": "GATTACA", "b": "GCATGCT"}, "needleman-wunsch", "alignment"),
    _t("local_alignment", align.local_alignment, "sequence-alignment",
       {"a": "TGTTACGG", "b": "GGTTGACTA"}, "smith-waterman", "alignment"),
    # --------------------------------------------------------------- formats
    _t("parse_fasta", formats.parse_fasta, "file-formats", {"text": _FASTA}, "fasta", "parser"),
    _t("parse_fastq", formats.parse_fastq, "file-formats", {"text": _FASTQ}, "fastq", "quality"),
    _t("parse_vcf", formats.parse_vcf, "file-formats", {"text": _VCF}, "vcf", "variants"),
    _t("parse_bed", formats.parse_bed, "file-formats", {"text": _BED}, "bed", "intervals"),
    _t("parse_gff", formats.parse_gff, "file-formats", {"text": _GFF}, "gff", "gtf", "annotation"),
    # -------------------------------------------------------------- variants
    _t("parse_hgvs", variants.parse_hgvs, "variant-analysis",
       {"hgvs": "NM_000546.6:c.215C>G"}, "hgvs", "variants"),
    _t("normalise_variant", variants.normalise_variant, "variant-analysis",
       {"chrom": "chr17", "pos": 7675088, "ref": "CT", "alt": "TT"}, "variants", "normalisation"),
    _t("allele_frequencies", variants.allele_frequencies, "variant-analysis",
       {"genotypes": ["0/0", "0/1", "0/1", "1/1", "0/0", "./."]}, "population-genetics", "hwe"),
    _t("transition_transversion", variants.transition_transversion, "variant-analysis",
       {"changes": [["A", "G"], ["C", "T"], ["A", "T"], ["G", "C"]]}, "variants", "qc"),
    # ------------------------------------------------------------ statistics
    _t("hypergeometric_test", stats.hypergeometric_test, "statistics",
       {"overlap": 5, "query_size": 20, "set_size": 50, "background": 20000}, "enrichment"),
    _t("fisher_exact", stats.fisher_exact, "statistics", {"a": 3, "b": 1, "c": 1, "d": 3},
       "contingency", "exact-test"),
    _t("enrichment_analysis", stats.enrichment_analysis, "statistics",
       {"genes": ["TP53", "BRCA1", "ATM"], "gene_sets": {"DNA repair": ["TP53", "BRCA1", "ATM", "CHEK2"],
                                                           "Glycolysis": ["HK1", "PFKM"]},
        "background": 20000}, "enrichment", "ora", "fdr"),
    _t("benjamini_hochberg", stats.benjamini_hochberg, "statistics",
       {"p_values": [0.01, 0.04, 0.03, 0.2]}, "fdr", "multiple-testing"),
    _t("mann_whitney_u", stats.mann_whitney_u, "statistics",
       {"x": [1.2, 2.3, 3.1, 4.8], "y": [5.5, 6.1, 7.2, 8.0]}, "nonparametric"),
    _t("welch_t_test", stats.welch_t_test, "statistics",
       {"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [3.0, 4.0, 5.0, 6.0, 7.0]}, "t-test"),
    _t("log2_fold_change", stats.log2_fold_change, "statistics",
       {"treated": [100, 50, 10], "control": [25, 50, 40]}, "expression"),
    _t("cpm", stats.cpm, "statistics", {"counts": [100, 200, 700], "log": True}, "normalisation"),
    _t("tpm", stats.tpm, "statistics", {"counts": [100, 200, 700], "lengths_bp": [1000, 2000, 500]},
       "normalisation"),
    _t("correlation", stats.correlation, "statistics",
       {"x": [1, 2, 3, 4, 5], "y": [2, 4, 5, 4, 5]}, "correlation"),
    _t("diversity", stats.diversity, "statistics", {"counts": [10, 20, 30, 40]},
       "ecology", "microbiome"),
    _t("odds_ratio", stats.odds_ratio, "statistics", {"a": 20, "b": 80, "c": 10, "d": 90},
       "epidemiology"),
    _t("relative_risk", stats.relative_risk, "statistics",
       {"exposed_events": 20, "exposed_total": 100, "unexposed_events": 10, "unexposed_total": 100},
       "epidemiology"),
    _t("diagnostic_metrics", stats.diagnostic_metrics, "statistics",
       {"tp": 90, "fp": 10, "fn": 5, "tn": 95}, "diagnostics"),
    _t("roc_auc", stats.roc_auc, "statistics",
       {"scores": [0.1, 0.4, 0.35, 0.8, 0.9], "labels": [0, 0, 1, 1, 1]}, "diagnostics"),
    _t("number_needed_to_treat", stats.number_needed_to_treat, "statistics",
       {"control_event_rate": 0.2, "experimental_event_rate": 0.15}, "epidemiology"),
    # ------------------------------------------------------ clinical calculators
    _t("bmi", clinical.bmi, "clinical-calculators", {"weight_kg": 70, "height_cm": 175}),
    _t("body_surface_area", clinical.body_surface_area, "clinical-calculators",
       {"weight_kg": 70, "height_cm": 175}),
    _t("ideal_body_weight", clinical.ideal_body_weight, "clinical-calculators",
       {"height_cm": 175, "sex": "male", "actual_weight_kg": 90}),
    _t("egfr_ckd_epi_2021", clinical.egfr_ckd_epi_2021, "clinical-calculators",
       {"creatinine_mg_dl": 1.0, "age_years": 50, "sex": "female"}, "renal"),
    _t("creatinine_clearance_cockcroft_gault", clinical.creatinine_clearance_cockcroft_gault,
       "clinical-calculators", {"creatinine_mg_dl": 1.0, "age_years": 50, "weight_kg": 70, "sex": "male"},
       "renal"),
    _t("fractional_excretion_sodium", clinical.fractional_excretion_sodium, "clinical-calculators",
       {"urine_sodium": 20, "plasma_sodium": 140, "urine_creatinine": 100, "plasma_creatinine": 1.0},
       "renal"),
    _t("corrected_calcium", clinical.corrected_calcium, "clinical-calculators",
       {"calcium_mg_dl": 8.0, "albumin_g_dl": 2.0}, "chemistry"),
    _t("anion_gap", clinical.anion_gap, "clinical-calculators",
       {"sodium": 140, "chloride": 100, "bicarbonate": 24, "albumin_g_dl": 3.0}, "chemistry"),
    _t("corrected_sodium", clinical.corrected_sodium, "clinical-calculators",
       {"sodium": 130, "glucose_mg_dl": 600}, "chemistry"),
    _t("henderson_hasselbalch", clinical.henderson_hasselbalch, "clinical-calculators",
       {"bicarbonate": 24, "pco2_mmHg": 40}, "blood-gas"),
    _t("alveolar_gas", clinical.alveolar_gas, "clinical-calculators",
       {"fio2": 0.21, "paco2_mmHg": 40, "pao2_mmHg": 90, "age_years": 40}, "blood-gas"),
    _t("qtc", clinical.qtc, "clinical-calculators", {"qt_ms": 400, "heart_rate_bpm": 75}, "ecg"),
    _t("mean_arterial_pressure", clinical.mean_arterial_pressure, "clinical-calculators",
       {"systolic": 120, "diastolic": 80}, "haemodynamics"),
    _t("cha2ds2_vasc", clinical.cha2ds2_vasc, "clinical-calculators",
       {"age_years": 76, "sex": "female", "hypertension": True}, "cardiology", "score"),
    _t("has_bled", clinical.has_bled, "clinical-calculators",
       {"hypertension_uncontrolled": True, "age_over_65": True, "drugs": True}, "cardiology", "score"),
    _t("wells_dvt", clinical.wells_dvt, "clinical-calculators",
       {"active_cancer": True, "calf_swelling_over_3cm": True, "pitting_oedema": True}, "score"),
    _t("wells_pe", clinical.wells_pe, "clinical-calculators",
       {"clinical_signs_of_dvt": True, "heart_rate_over_100": True}, "score"),
    _t("curb65", clinical.curb65, "clinical-calculators",
       {"confusion": False, "urea_mmol_l": 8.0, "respiratory_rate": 32, "systolic": 100,
        "diastolic": 65, "age_years": 70}, "score", "pneumonia"),
    _t("meld_na", clinical.meld_na, "clinical-calculators",
       {"bilirubin_mg_dl": 3.0, "inr": 2.0, "creatinine_mg_dl": 2.0, "sodium": 128}, "hepatology", "score"),
    _t("child_pugh", clinical.child_pugh, "clinical-calculators",
       {"bilirubin_mg_dl": 2.5, "albumin_g_dl": 3.0, "inr": 1.8, "ascites": "mild",
        "encephalopathy": "none"}, "hepatology", "score"),
    _t("news2", clinical.news2, "clinical-calculators",
       {"respiratory_rate": 24, "spo2_percent": 93, "supplemental_oxygen": True, "systolic": 95,
        "heart_rate": 115, "alert": True, "temperature_c": 38.5}, "score", "early-warning"),
    _t("glasgow_coma_scale", clinical.glasgow_coma_scale, "clinical-calculators",
       {"eyes": 3, "verbal": 4, "motor": 5}, "score", "neurology"),
    _t("qsofa", clinical.qsofa, "clinical-calculators",
       {"respiratory_rate": 24, "systolic": 95, "altered_mentation": False}, "score", "sepsis"),
    _t("friedewald_ldl", clinical.friedewald_ldl, "clinical-calculators",
       {"total_cholesterol_mg_dl": 200, "hdl_mg_dl": 50, "triglycerides_mg_dl": 150}, "lipids"),
    _t("hba1c_to_eag", clinical.hba1c_to_eag, "clinical-calculators", {"hba1c_percent": 7.0},
       "diabetes"),
    _t("basal_metabolic_rate", clinical.basal_metabolic_rate, "clinical-calculators",
       {"weight_kg": 70, "height_cm": 175, "age_years": 40, "sex": "male", "activity_factor": 1.4},
       "nutrition"),
    _t("parkland_formula", clinical.parkland_formula, "clinical-calculators",
       {"weight_kg": 70, "tbsa_burned_percent": 20}, "burns", "fluids"),
    _t("weight_based_dose", clinical.weight_based_dose, "clinical-calculators",
       {"dose_mg_per_kg": 15, "weight_kg": 20, "max_dose_mg": 1000, "doses_per_day": 4}, "dosing"),
    _t("tidal_volume", clinical.tidal_volume, "clinical-calculators",
       {"height_cm": 175, "sex": "male", "ml_per_kg": 6}, "ventilation"),
    _t("convert_units", clinical.convert_units, "clinical-calculators",
       {"analyte": "glucose", "value": 180, "from_unit": "mg/dL", "to_unit": "mmol/L"}, "units"),
)

BY_NAME: Mapping[str, NativeTool] = {t.name: t for t in TOOLS}
if len(BY_NAME) != len(TOOLS):                     # pragma: no cover - programming error
    raise RuntimeError("duplicate native tool names")


def tool(name: str) -> NativeTool:
    try:
        return BY_NAME[name]
    except KeyError:
        raise KeyError(f"no native tool {name!r}; have {sorted(BY_NAME)}") from None


def run_smoke(name: str) -> tuple[bool, str]:
    """Run a tool's example. The result must be a JSON-serialisable dict."""
    t = tool(name)
    try:
        result = t.fn(**dict(t.example))
    except Exception as exc:  # noqa: BLE001 - the smoke test reports, never raises
        return False, f"{type(exc).__name__}: {exc}"
    if not isinstance(result, dict):
        return False, f"returned {type(result).__name__}, not a dict"
    try:
        json.dumps(result)
    except (TypeError, ValueError) as exc:
        return False, f"result is not JSON-serialisable: {exc}"
    return True, f"{name} ok ({len(result)} keys)"


def native_smoke_runner(manifest: Any) -> tuple[bool, str]:
    """A ``smoke_runner`` for ``HotReloader``/``EvolutionPipeline`` that understands
    ``native:<name>`` smoke tests and passes anything else through as untested."""
    spec = str(getattr(getattr(manifest, "validation", None), "smoke_test", "") or "")
    if not spec.startswith("native:"):
        return True, "no native smoke test declared"
    name = spec.split(":", 1)[1]
    if name not in BY_NAME:
        return False, f"unknown native tool {name!r}"
    return run_smoke(name)


class NativeToolProvider:
    """Yields one BioScience ``python`` component per native tool."""

    name = "native-tools"

    def available(self) -> bool:
        return True

    def discover(self) -> Iterator[Any]:
        from ..runtime.component import (ComponentManifest, LicenseSpec, Permissions, Provider,
                                         Requirements, RuntimeSpec, Validation)

        for t in TOOLS:
            yield ComponentManifest(
                id=t.component_id, kind="tool", name=t.name, version="0.2.4",
                description=t.description[:400], domain=t.domain,
                omics_type=DOMAINS[t.domain],
                provider=Provider(project="bioagent", source_path=f"src/bioagent/tools/{t.fn.__module__.rsplit('.', 1)[-1]}.py"),
                runtime=RuntimeSpec(backend="python", entrypoint=t.entrypoint, deterministic=True),
                inputs={"parameters": t.parameters, "example": dict(t.example)},
                outputs={"type": "object"},
                requires=Requirements(python=("bioagent",)),
                permissions=Permissions(),
                license=LicenseSpec(spdx="MIT", integration_mode="native"),
                validation=Validation(smoke_test=f"native:{t.name}"),
                offline_capable=True, native_connectors=(), signature=", ".join(
                    p["name"] for p in t.parameters))
