"""The tool catalog (CONTRACTS §2), built from the code itself.

Native tools take their JSON Schema from ``typing.get_type_hints`` and their summary from
the first paragraph of the docstring (``NativeTool.description`` is only its first line and
is often cut mid-sentence). Governed skills come from their manifests and the signatures of
the callables ``run_governed`` joins them to (the P0 manifests declare a ``query`` input
the functions do not take). Connector operations come from the request templates. The
clinic, the TCM data hub, the study designs, the runner's job kinds and the system
operations are described here by hand, because they have no machine-readable signature
that says the same thing.

Nothing here runs a tool, starts a thread or opens a socket; ``probe`` only asks
``importlib.util.find_spec`` whether an optional package is installed.
"""

from __future__ import annotations

import copy
import difflib
import importlib.util
import inspect
import re
import shutil
import types
import typing
from pathlib import Path
from typing import Any, Iterable, Mapping

from .core_tools import (B, B_R, CATEGORY_IDS, CLAIM_KINDS, CORE_TOOLS, ENTRY_KINDS,
                         HERB_LINE_SCHEMA, I, INTAKE_SCHEMA, MODIFICATION_SCHEMA, N, R,
                         VISIT_SCHEMA, S, arr, obj)

__all__ = ["SCHEMA_ID", "CATEGORIES", "JOB_KINDS", "NEVER_OFFERED", "build_catalog",
           "catalog_search", "entries", "get_entry", "entry_ids", "suggest", "schema_for",
           "fn_schema", "skill_specs", "skill_lockfile"]

SCHEMA_ID = "tcmstudio.catalog/1"

CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("tcm_knowledge", "中医知识", "TCM Knowledge"),
    ("tcm_safety", "安全与配伍", "Safety & Compatibility"),
    ("clinic", "临床辨证", "Clinical Differentiation"),
    ("tcm_data", "中医药数据枢纽", "TCM Data Hub"),
    ("live_sources", "在线数据源", "Live Data Sources"),
    ("netpharm", "网络药理与研究闭环", "Network Pharmacology & Research Loop"),
    ("study_design", "统计与研究设计", "Statistics & Study Design"),
    ("clinical_calc", "临床计算与药理", "Clinical Calculators & Pharmacology"),
    ("seq_genomics", "序列与基因组", "Sequences & Genomics"),
    ("structure_molecules", "结构与分子", "Structure & Molecules"),
    ("omics", "组学流程", "Omics Pipelines"),
    ("literature", "文献证据", "Literature & Evidence"),
    ("system", "审计与环境", "Provenance & Environment"),
)
assert tuple(c[0] for c in CATEGORIES) == CATEGORY_IDS

#: Acts reserved for a person. They are never catalog entries and the dispatcher refuses
#: them by name, so no prompt can reach them through call_tool.
NEVER_OFFERED: dict[str, str] = {
    "clinic.sign": "Signing a clinic draft is the licensed practitioner's act; it is done by "
                   "the practitioner in the clinic view, never by a tool call.",
    "clinic_sign": "Signing a clinic draft is the licensed practitioner's act; it is done by "
                   "the practitioner in the clinic view, never by a tool call.",
    "clinic.agreement": "Agreement studies against practitioners' labels are run by a person "
                        "from the clinic view, not by the model.",
    "registry.release": "Registry releases and skill promotion are reviewed acts of the "
                        "maintainers, not tool calls.",
    "registry.promote": "Registry releases and skill promotion are reviewed acts of the "
                        "maintainers, not tool calls.",
    "system.shell": "Studio offers no raw shell: jobs run only the fixed command of a "
                    "registered job kind.",
    "shell": "Studio offers no raw shell: jobs run only the fixed command of a registered "
             "job kind.",
    "tcmdb.fetch_confirm": "A download above the hub's size gate needs the user's own "
                           "confirmation in the data view.",
}

# --------------------------------------------------------------------- native tools

_DOMAIN_TAGS: dict[str, tuple[str, ...]] = {
    "sequence-analysis": ("序列", "序列分析", "核酸", "DNA", "RNA", "sequence"),
    "protein-analysis": ("蛋白", "蛋白质", "多肽", "protein", "peptide"),
    "sequence-alignment": ("比对", "序列比对", "alignment"),
    "file-formats": ("文件格式", "文件解析", "解析", "parser", "file format"),
    "variant-analysis": ("变异", "突变", "基因变异", "variant", "mutation"),
    "statistics": ("统计", "统计检验", "假设检验", "statistics", "test"),
    "clinical-calculators": ("临床计算", "临床评分", "评分", "计算器", "calculator", "score"),
    "pharmacology": ("药理", "药代动力学", "剂量计算", "给药", "pharmacology", "dose"),
    "survival-analysis": ("生存分析", "生存曲线", "survival"),
    "population-genetics": ("群体遗传", "群体遗传学", "population genetics"),
    "phylogenetics": ("系统发育", "进化树", "系统发生", "phylogenetics", "tree"),
    "tcm-knowledge": ("中医", "中药", "中医药", "TCM"),
}

#: zh / en titles of the 150 native tools, for the UI and for Chinese search.
_NATIVE_TITLES: dict[str, tuple[str, str]] = {
    "reverse_complement": ("反向互补序列", "Reverse complement"),
    "transcribe": ("转录（DNA→RNA）", "Transcribe DNA to RNA"),
    "translate": ("翻译（密码子→氨基酸）", "Translate to protein"),
    "gc_content": ("GC 含量", "GC content"),
    "find_orfs": ("开放阅读框", "Open reading frames"),
    "kmer_counts": ("k-mer 计数", "k-mer counts"),
    "hamming_distance": ("汉明距离", "Hamming distance"),
    "edit_distance": ("编辑距离", "Edit distance"),
    "codon_usage": ("密码子使用", "Codon usage"),
    "melting_temperature": ("引物解链温度", "Melting temperature"),
    "restriction_sites": ("限制性酶切位点", "Restriction sites"),
    "nucleic_acid_weight": ("核酸分子量", "Oligo molecular weight"),
    "protein_properties": ("蛋白理化性质", "Protein properties"),
    "hydropathy_profile": ("疏水性曲线", "Hydropathy profile"),
    "global_alignment": ("全局比对", "Global alignment"),
    "local_alignment": ("局部比对", "Local alignment"),
    "protein_alignment": ("蛋白序列比对", "Protein alignment"),
    "parse_fasta": ("解析 FASTA", "Parse FASTA"),
    "parse_fastq": ("解析 FASTQ", "Parse FASTQ"),
    "parse_vcf": ("解析 VCF", "Parse VCF"),
    "parse_bed": ("解析 BED", "Parse BED"),
    "parse_gff": ("解析 GFF/GTF", "Parse GFF/GTF"),
    "parse_hgvs": ("解析 HGVS", "Parse HGVS"),
    "normalise_variant": ("变异标准化", "Normalise a variant"),
    "allele_frequencies": ("等位基因频率与哈迪-温伯格", "Allele frequencies"),
    "transition_transversion": ("转换/颠换比", "Ts/Tv ratio"),
    "hypergeometric_test": ("超几何检验", "Hypergeometric test"),
    "fisher_exact": ("Fisher 精确检验", "Fisher's exact test"),
    "enrichment_analysis": ("富集分析", "Enrichment analysis"),
    "benjamini_hochberg": ("BH 多重检验校正", "Benjamini–Hochberg"),
    "mann_whitney_u": ("Mann-Whitney U 检验", "Mann–Whitney U"),
    "welch_t_test": ("Welch t 检验", "Welch's t-test"),
    "log2_fold_change": ("log2 倍数变化", "log2 fold change"),
    "cpm": ("CPM 标准化", "Counts per million"),
    "tpm": ("TPM 标准化", "Transcripts per million"),
    "correlation": ("相关分析", "Correlation"),
    "diversity": ("多样性指数", "Diversity indices"),
    "odds_ratio": ("比值比", "Odds ratio"),
    "relative_risk": ("相对危险度", "Relative risk"),
    "diagnostic_metrics": ("诊断试验指标", "Diagnostic metrics"),
    "roc_auc": ("ROC 曲线下面积", "ROC AUC"),
    "number_needed_to_treat": ("需治疗人数", "Number needed to treat"),
    "bmi": ("体重指数", "Body mass index"),
    "body_surface_area": ("体表面积", "Body surface area"),
    "ideal_body_weight": ("理想体重", "Ideal body weight"),
    "egfr_ckd_epi_2021": ("估算肾小球滤过率（CKD-EPI 2021）", "eGFR (CKD-EPI 2021)"),
    "creatinine_clearance_cockcroft_gault": ("肌酐清除率（Cockcroft-Gault）",
                                             "Creatinine clearance (Cockcroft–Gault)"),
    "fractional_excretion_sodium": ("钠排泄分数", "Fractional excretion of sodium"),
    "corrected_calcium": ("校正血钙", "Corrected calcium"),
    "anion_gap": ("阴离子间隙", "Anion gap"),
    "corrected_sodium": ("校正血钠", "Corrected sodium"),
    "henderson_hasselbalch": ("Henderson-Hasselbalch 方程", "Henderson–Hasselbalch"),
    "alveolar_gas": ("肺泡气方程与 A-a 梯度", "Alveolar gas equation"),
    "qtc": ("校正 QT 间期", "Corrected QT"),
    "mean_arterial_pressure": ("平均动脉压", "Mean arterial pressure"),
    "cha2ds2_vasc": ("CHA₂DS₂-VASc 卒中风险", "CHA₂DS₂-VASc"),
    "has_bled": ("HAS-BLED 出血风险", "HAS-BLED"),
    "wells_dvt": ("Wells 深静脉血栓评分", "Wells score (DVT)"),
    "wells_pe": ("Wells 肺栓塞评分", "Wells score (PE)"),
    "curb65": ("CURB-65 肺炎严重度", "CURB-65"),
    "meld_na": ("MELD-Na 评分", "MELD-Na"),
    "child_pugh": ("Child-Pugh 分级", "Child–Pugh"),
    "news2": ("国家早期预警评分 NEWS2", "NEWS2"),
    "glasgow_coma_scale": ("格拉斯哥昏迷评分", "Glasgow Coma Scale"),
    "qsofa": ("qSOFA 评分", "qSOFA"),
    "friedewald_ldl": ("Friedewald 低密度脂蛋白", "Friedewald LDL"),
    "hba1c_to_eag": ("糖化血红蛋白换算平均血糖", "HbA1c to estimated average glucose"),
    "basal_metabolic_rate": ("基础代谢率", "Basal metabolic rate"),
    "parkland_formula": ("Parkland 烧伤补液公式", "Parkland formula"),
    "weight_based_dose": ("按体重给药剂量", "Weight-based dose"),
    "tidal_volume": ("保护性潮气量", "Lung-protective tidal volume"),
    "convert_units": ("检验单位换算", "Unit conversion"),
    "phq9": ("PHQ-9 抑郁评分", "PHQ-9"),
    "gad7": ("GAD-7 焦虑评分", "GAD-7"),
    "apgar": ("Apgar 评分", "Apgar score"),
    "bishop_score": ("Bishop 宫颈评分", "Bishop score"),
    "gestational_age": ("孕周与预产期", "Gestational age"),
    "pk_one_compartment": ("一室药代动力学模型", "One-compartment PK"),
    "half_life_from_levels": ("由血药浓度求半衰期", "Half-life from levels"),
    "loading_dose": ("负荷剂量", "Loading dose"),
    "maintenance_dose": ("维持剂量", "Maintenance dose"),
    "steady_state": ("稳态与蓄积", "Steady state"),
    "carboplatin_calvert": ("卡铂 Calvert 公式", "Carboplatin (Calvert)"),
    "glucocorticoid_equivalent": ("糖皮质激素等效剂量", "Glucocorticoid equivalence"),
    "morphine_milligram_equivalents": ("吗啡毫克当量", "Morphine milligram equivalents"),
    "bsa_dose": ("按体表面积给药", "BSA-based dose"),
    "kaplan_meier": ("Kaplan-Meier 生存曲线", "Kaplan–Meier"),
    "log_rank_test": ("Log-rank 检验", "Log-rank test"),
    "meta_analysis": ("Meta 分析", "Meta-analysis"),
    "chi_square_test": ("卡方检验", "Chi-square test"),
    "linear_regression": ("线性回归", "Linear regression"),
    "one_way_anova": ("单因素方差分析", "One-way ANOVA"),
    "kruskal_wallis": ("Kruskal-Wallis 检验", "Kruskal–Wallis"),
    "wilcoxon_signed_rank": ("Wilcoxon 符号秩检验", "Wilcoxon signed-rank"),
    "cohens_d": ("Cohen's d 效应量", "Cohen's d"),
    "post_test_probability": ("验后概率", "Post-test probability"),
    "sample_size_two_proportions": ("样本量（两组率）", "Sample size (two proportions)"),
    "sample_size_two_means": ("样本量（两组均数）", "Sample size (two means)"),
    "incidence_rate": ("发病率与置信区间", "Incidence rate"),
    "linkage_disequilibrium": ("连锁不平衡", "Linkage disequilibrium"),
    "nucleotide_diversity": ("核苷酸多样性", "Nucleotide diversity"),
    "fst": ("群体分化 Fst", "Fst"),
    "distance_matrix": ("序列距离矩阵", "Distance matrix"),
    "neighbor_joining": ("邻接法建树", "Neighbor joining"),
    "upgma": ("UPGMA 建树", "UPGMA"),
    "parse_newick": ("解析 Newick 树", "Parse Newick"),
    "tree_distances": ("树上距离", "Tree distances"),
    "motif_search": ("基序搜索", "Motif search"),
    "cpg_islands": ("CpG 岛", "CpG islands"),
    "six_frame_translation": ("六框翻译", "Six-frame translation"),
    "crispr_guides": ("CRISPR 向导 RNA 设计", "CRISPR guides"),
    "sequence_entropy": ("序列熵", "Sequence entropy"),
    "primer_check": ("引物检查", "Primer check"),
    "peptide_mass": ("多肽质量", "Peptide mass"),
    "in_silico_digest": ("虚拟酶切", "In-silico digest"),
    "parse_sam": ("解析 SAM", "Parse SAM"),
    "parse_pdb": ("解析 PDB 结构", "Parse PDB"),
    "parse_obo": ("解析 OBO 本体", "Parse OBO"),
    "annotate_coding_variant": ("编码区变异注释", "Annotate a coding variant"),
    "ascvd_pooled_cohort": ("ASCVD 十年风险", "ASCVD 10-year risk"),
    "sofa": ("SOFA 器官衰竭评分", "SOFA"),
    "calculated_osmolality": ("计算渗透压", "Calculated osmolality"),
    "winters_formula": ("Winters 公式", "Winters' formula"),
    "acid_base_interpretation": ("酸碱平衡判读", "Acid–base interpretation"),
    "holliday_segar": ("Holliday-Segar 维持液量", "Holliday–Segar"),
    "free_water_deficit": ("自由水缺失量", "Free water deficit"),
    "allowable_blood_loss": ("允许失血量", "Allowable blood loss"),
    "infusion_rate": ("输液速度", "Infusion rate"),
    "heart_score": ("HEART 胸痛评分", "HEART score"),
    "centor_mcisaac": ("Centor/McIsaac 咽炎评分", "Centor (McIsaac)"),
    "alvarado": ("Alvarado 阑尾炎评分", "Alvarado score"),
    "timi_ua_nstemi": ("TIMI 风险评分", "TIMI (UA/NSTEMI)"),
    "abcd2": ("ABCD² 评分", "ABCD² score"),
    "sirs": ("SIRS 标准", "SIRS criteria"),
    "rcri": ("修订心脏风险指数", "Revised Cardiac Risk Index"),
    "stop_bang": ("STOP-Bang 睡眠呼吸暂停筛查", "STOP-Bang"),
    "fib4": ("FIB-4 肝纤维化指数", "FIB-4"),
    "apri": ("APRI 指数", "APRI"),
    "homa_ir": ("HOMA-IR 胰岛素抵抗", "HOMA-IR"),
    "tcm_lookup": ("中医名称解析", "Resolve a TCM name"),
    "tcm_herb": ("药材信息", "Herb record"),
    "tcm_formula": ("方剂信息", "Formula record"),
    "tcm_syndrome": ("证候信息", "Syndrome record"),
    "tcm_compatibility": ("配伍禁忌核查", "Compatibility check"),
    "tcm_applicability": ("主张适用性", "Claim licensing"),
    "tcm_evidence_tiers": ("证据等级", "Evidence tiers"),
    "tcm_classical_search": ("经典条文检索", "Classical passages"),
    "hkbu_formula_lookup": ("港浸会方剂组成（本地数据）", "HKBU formula composition (local)"),
    "hkcmms_standard_lookup": ("香港中药材标准（本地数据）", "HKCMMS standards (local)"),
    "hk_cmm_dna_lookup": ("中药材 DNA 参考序列（本地数据）", "CMM DNA references (local)"),
}

#: Extra Chinese search terms for tools whose title alone would not be found.
_NATIVE_EXTRA_TAGS: dict[str, tuple[str, ...]] = {
    "egfr_ckd_epi_2021": ("肾功能", "肾小球滤过率", "eGFR", "肌酐"),
    "creatinine_clearance_cockcroft_gault": ("肾功能", "肌酐"),
    "kaplan_meier": ("生存率", "随访"), "log_rank_test": ("生存比较",),
    "meta_analysis": ("荟萃分析", "系统评价", "合并效应"),
    "sample_size_two_proportions": ("样本量计算", "试验设计"),
    "sample_size_two_means": ("样本量计算", "试验设计"),
    "enrichment_analysis": ("通路富集", "基因集"),
    "tcm_compatibility": ("配伍", "禁忌", "同用"),
    "tcm_applicability": ("证据适用", "能否说有效"),
    "morphine_milligram_equivalents": ("阿片类", "镇痛"),
    "glucocorticoid_equivalent": ("激素换算", "泼尼松"),
    "bmi": ("肥胖", "体重"), "news2": ("预警评分", "病情恶化"),
    "phq9": ("抑郁",), "gad7": ("焦虑",),
}


def _native_category(name: str, domain: str) -> str:
    if domain == "tcm-knowledge":
        if name == "tcm_compatibility":
            return "tcm_safety"
        if name in ("hkbu_formula_lookup", "hkcmms_standard_lookup", "hk_cmm_dna_lookup"):
            return "tcm_data"
        return "tcm_knowledge"
    if domain in ("statistics", "survival-analysis"):
        return "study_design"
    if domain in ("clinical-calculators", "pharmacology"):
        return "clinical_calc"
    if domain == "protein-analysis" or name == "parse_pdb":
        return "structure_molecules"
    return "seq_genomics"


# ------------------------------------------------------------------- JSON Schema


def schema_for(annotation: Any, default: Any = inspect.Parameter.empty) -> dict[str, Any]:
    """JSON Schema for a Python annotation. Arrays always carry ``items`` and objects
    ``properties`` (empty when the keys are free), so the schema converts to any provider's
    tool format."""
    if annotation is inspect.Parameter.empty or annotation is None:
        if default is not inspect.Parameter.empty and default is not None:
            return schema_for(type(default))
        return {}
    if annotation is type(None):
        return {"type": "null"}
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        parts = [schema_for(a) for a in args if a is not type(None)]
        nullable = any(a is type(None) for a in args)
        if len(parts) == 1 and not nullable:
            return parts[0]
        options = parts + ([{"type": "null"}] if nullable else [])
        return {"anyOf": options}
    if origin is typing.Literal:
        return {"enum": list(args)}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}
    if annotation is typing.Any:
        return {}
    target = origin or annotation
    name = getattr(target, "__name__", "")
    if target in (dict,) or name in ("Mapping", "MutableMapping", "dict"):
        value = schema_for(args[1]) if len(args) == 2 else {}
        return {"type": "object", "properties": {}, "additionalProperties": value or True}
    if target in (list, tuple, set, frozenset) or name in ("Sequence", "Iterable",
                                                           "Collection", "list", "tuple"):
        item_args = [a for a in args if a is not Ellipsis]
        return {"type": "array", "items": schema_for(item_args[0]) if item_args else {}}
    return {"description": f"python type {name or annotation!r}"}


def _json_default(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_default(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_default(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_default(v) for k, v in value.items()}
    raise TypeError


def fn_schema(fn: Any, example: Mapping[str, Any] | None = None, *,
              skip: Iterable[str] = ()) -> dict[str, Any]:
    """The JSON Schema of a function's keyword arguments, from its type hints."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:                                           # noqa: BLE001
        hints = {}
    skip = set(skip)
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in inspect.signature(fn).parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) or p.name in skip:
            continue
        schema = dict(schema_for(hints.get(p.name, p.annotation), p.default))
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
        else:
            try:
                schema["default"] = _json_default(p.default)
            except TypeError:
                pass
        if example and p.name in example:
            try:
                schema["examples"] = [_json_default(example[p.name])]
            except TypeError:
                pass
        props[p.name] = schema
    out: dict[str, Any] = {"type": "object", "properties": props,
                           "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def _first_paragraph(doc: str) -> str:
    return " ".join(inspect.cleandoc(doc or "").split("\n\n", 1)[0].split())


# ------------------------------------------------------------------------ entries


def _entry(id_: str, kind: str, zh: str, en: str, category: str, summary: str,
           parameters: Mapping[str, Any], *, example: Mapping[str, Any] | None = None,
           exec_: list[str] | None = None, network: bool = False, confirm: bool = False,
           job: bool = False, gpu: bool = False, heavy: Iterable[str] = (),
           tags: Iterable[str] = (), **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": id_, "kind": kind, "title": {"zh": zh, "en": en}, "summary": summary,
        "category": category, "parameters": dict(parameters),
        "example": dict(example) if example is not None else None,
        "exec": list(exec_ or B_R), "network": network, "confirm": confirm, "job": job,
        "gpu": gpu, "heavy": list(heavy), "tags": _unique(tags)}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        item = str(item).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _native_entries() -> list[dict[str, Any]]:
    from bioagent.tools import DOMAINS, TOOLS

    out = []
    for t in TOOLS:
        zh, en = _NATIVE_TITLES.get(t.name, (t.name, t.name.replace("_", " ")))
        doc = inspect.getdoc(t.fn) or ""
        data = list(t.data)
        reads_store = bool(t.reads)
        extra: dict[str, Any] = {"domain": t.domain, "doc": doc[:4000]}
        if data:
            extra["data"] = data
            extra["needs"] = ["a tcmdb store built or imported on the runner"]
        out.append(_entry(
            f"native.{t.name}", "native", zh, en, _native_category(t.name, t.domain),
            _first_paragraph(doc) or t.description, fn_schema(t.fn, t.example),
            example=dict(t.example), exec_=R if reads_store else B_R,
            tags=[t.name, *t.tags, *_DOMAIN_TAGS.get(t.domain, ()), DOMAINS.get(t.domain, ""),
                  *_NATIVE_EXTRA_TAGS.get(t.name, ())], **extra))
    return out


# ------------------------------------------------------------------------- skills

_SKILL_META: dict[str, dict[str, Any]] = {
    "normalize-tcm-entities": {"title": ("中医名称规范化", "Normalize TCM names"),
                               "category": "tcm_knowledge", "example": {"names": ["黄芪", "桂枝汤", "姜"]},
                               "tags": ("名称", "规范化", "消歧", "别名")},
    "retrieve-tcm-evidence": {"title": ("中医证据检索", "Retrieve TCM evidence"),
                              "category": "tcm_knowledge",
                              "example": {"subject": "黄芪", "claim_kind": "traditional_use"},
                              "tags": ("证据", "文献", "研究记录", "经典记载")},
    "assess-tcm-safety": {"title": ("中药安全性评估", "Assess TCM safety"),
                          "category": "tcm_safety",
                          "example": {"subject": "甘草", "co_administered": ["甘遂"]},
                          "tags": ("安全性", "毒性", "十八反", "十九畏", "配伍禁忌", "妊娠禁忌",
                                   "相互作用")},
    "analyze-tcm-network-pharmacology": {"title": ("网络药理分析（种子语料）",
                                                   "Network pharmacology (seed corpus)"),
                                         "category": "netpharm",
                                         "example": {"formula_name": "桂枝汤"},
                                         "tags": ("网络药理", "靶点", "机制假说", "通路")},
    "draft-tcm-prescription": {"title": ("处方草案（受治理）", "Draft prescription (governed)"),
                               "category": "clinic", "tags": ("处方", "辨证", "草案", "四诊"),
                               "pyodide_packages": ["numpy", "scipy"]},
    "retrieve-literature-evidence": {"title": ("本地文献证据检索", "Literature evidence (local corpus)"),
                                     "category": "literature", "heavy": ["paper-qa"],
                                     "tags": ("文献", "检索", "RAG")},
    "dock-ligands": {"title": ("分子对接", "Dock ligands"), "category": "structure_molecules",
                     "heavy": ["rdkit", "meeko", "vina", "gemmi"], "network_if": "allow_remote",
                     "tags": ("对接", "AutoDock", "Vina", "配体")},
    "predict-admet": {"title": ("ADMET 预测", "Predict ADMET"), "category": "structure_molecules",
                      "heavy": ["rdkit", "scikit-learn"], "tags": ("药代", "毒性预测", "成药性")},
    "predict-protein-structure": {"title": ("蛋白结构预测", "Predict protein structure"),
                                  "category": "structure_molecules", "gpu": True,
                                  "network_if": "allow_remote",
                                  "optional": ["torch", "transformers", "colabfold_batch"],
                                  "tags": ("结构预测", "ESMFold", "折叠", "pLDDT")},
    "rnaseq-differential-expression": {"title": ("RNA-seq 差异表达", "RNA-seq differential expression"),
                                       "category": "omics",
                                       "optional": ["pydeseq2", "salmon", "kallisto", "fastp"],
                                       "tags": ("转录组", "差异表达", "DESeq2")},
    "scrna-cell-atlas": {"title": ("单细胞图谱", "Single-cell atlas"), "category": "omics",
                         "gpu": True, "optional": ["scanpy", "harmonypy", "leidenalg", "scvi-tools"],
                         "tags": ("单细胞", "聚类", "细胞注释", "scRNA")},
}
_SKILL_INTERNAL_ARGS = ("run_id", "out_dir", "cache_dir", "index_dir")
_RUN_IN_PROCESS = {"normalize-tcm-entities", "retrieve-tcm-evidence", "assess-tcm-safety",
                   "analyze-tcm-network-pharmacology", "draft-tcm-prescription"}


def skill_lockfile(skill_dir: Path) -> Path | None:
    """The lockfile ``run_governed`` would use for a skill directory: the first
    ``registry/skills.lock.yaml`` above it."""
    for parent in (skill_dir, *skill_dir.parents):
        candidate = parent / "registry" / "skills.lock.yaml"
        if candidate.is_file():
            return candidate
    return None


_SKILL_SPECS: list[dict[str, Any]] | None = None


def skill_specs() -> list[dict[str, Any]]:
    """Each governed skill's manifest facts, its directory and its pin. Reads the YAML
    manifests and the lockfile only; the content hash shown is the pin, and the code is
    checked against it when the skill runs."""
    global _SKILL_SPECS
    if _SKILL_SPECS is not None:
        return _SKILL_SPECS
    import yaml
    from bioagent.config import skills_dir

    root = Path(skills_dir())
    found: list[dict[str, Any]] = []
    for manifest in sorted(root.glob("tcm/*/skill.yaml")) + sorted(
            root.glob("candidates/*/*/skill.yaml")):
        try:
            m = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception:                                       # noqa: BLE001
            continue
        runtime = m.get("runtime") or {}
        if not runtime.get("entrypoint"):
            continue                       # a run contract (network-pharmacology), not a skill
        group = manifest.parent.parent
        lock = skill_lockfile(group)
        pins: dict[str, Any] = {}
        if lock is not None:
            try:
                doc = yaml.safe_load(lock.read_text(encoding="utf-8")) or {}
                pins = {s.get("skill_id"): s for s in doc.get("skills") or []}
            except Exception:                                   # noqa: BLE001
                pins = {}
        pin = pins.get(m.get("id")) or {}
        evidence = m.get("evidence") or {}
        perms = m.get("permissions") or {}
        found.append({
            "id": m.get("id"), "name": m.get("name"), "version": str(m.get("version", "")),
            "summary": " ".join(str(m.get("summary", "")).split()),
            "entrypoint": runtime.get("entrypoint"), "timeout_s": runtime.get("timeout_s"),
            "network": list(perms.get("network") or []), "subprocess": bool(perms.get("subprocess")),
            "claim_kinds": list(evidence.get("claim_kinds") or []),
            "forbidden_claims": list(evidence.get("forbidden_claims") or []),
            "max_evidence_tier": evidence.get("max_tier"), "risk": m.get("risk"),
            "min_autonomy": m.get("min_autonomy"), "directory": str(manifest.parent),
            "group": str(group), "set": "tcm" if group.name == "tcm" else "candidates",
            "lockfile": str(lock) if lock else "",
            "pinned": bool(pin) and str(pin.get("version")) == str(m.get("version")),
            "content_hash": pin.get("content_hash") if pin else None})
    _SKILL_SPECS = found
    return found


def _skill_entries() -> list[dict[str, Any]]:
    from bioagent.governed import candidate_callables, skill_callables

    callables = {**skill_callables(), **candidate_callables()}
    out = []
    for spec in skill_specs():
        sid = spec["id"]
        fn = callables.get(sid)
        if fn is None:
            continue
        meta = _SKILL_META.get(sid, {})
        zh, en = meta.get("title", (spec["name"] or sid, spec["name"] or sid))
        params = fn_schema(fn, meta.get("example"), skip=_SKILL_INTERNAL_ARGS)
        if sid == "draft-tcm-prescription":
            # The callable reads the intake from a file; Studio passes the intake itself and
            # writes the file for it.
            params["properties"]["intake"] = INTAKE_SCHEMA
            params["properties"]["modifications"] = arr(MODIFICATION_SCHEMA, default=[])
        if sid == "retrieve-tcm-evidence":
            params["properties"]["claim_kind"]["enum"] = list(CLAIM_KINDS)
        in_process = sid in _RUN_IN_PROCESS
        browser = spec["set"] == "tcm" or sid == "draft-tcm-prescription"
        risky = spec["risk"] not in (None, "r1_routine") or spec["min_autonomy"] not in (
            None, "observe")
        out.append(_entry(
            f"skill.{sid}", "skill", zh, en, meta.get("category", "system"),
            spec["summary"], params, example=meta.get("example"),
            exec_=B_R if (in_process and browser) else R,
            network=bool(spec["network"]) and not meta.get("network_if"),
            network_if=meta.get("network_if"),
            confirm=bool(risky or not in_process), job=not in_process,
            gpu=bool(meta.get("gpu")), heavy=meta.get("heavy", ()),
            tags=[sid, spec["name"] or "", "skill", "governed", "受治理", *meta.get("tags", ()),
                  *spec["claim_kinds"]],
            hosts=spec["network"] or None, optional=meta.get("optional"),
            pyodide_packages=meta.get("pyodide_packages"),
            skill={"version": spec["version"], "pinned": spec["pinned"],
                   "claim_kinds": spec["claim_kinds"],
                   "forbidden_claims": spec["forbidden_claims"],
                   "max_evidence_tier": spec["max_evidence_tier"], "network": spec["network"],
                   "content_hash": spec["content_hash"], "risk": spec["risk"],
                   "min_autonomy": spec["min_autonomy"], "set": spec["set"]}))
    return out


# --------------------------------------------------------------------- connectors

_TCM_CONNECTORS = ("dcabm_tcm", "tcmbank", "itcm", "ttd", "symmap", "herb_api")
_CONNECTOR_DOMAIN_ZH: dict[str, tuple[str, ...]] = {
    "literature": ("文献", "论文", "检索"), "genomics": ("基因组", "基因"),
    "proteomics": ("蛋白", "蛋白质组"), "pathways": ("通路",), "chemistry": ("化合物", "化学"),
    "natural-products": ("天然产物", "成分"), "clinical": ("临床", "临床试验"),
    "tcm": ("中医药", "中药"), "ontology": ("本体", "术语"), "drugs": ("药物",),
    "pharmacology": ("药理", "药物"), "imaging": ("影像",), "spectra": ("质谱", "光谱"),
    "variants": ("变异",), "expression": ("表达",), "interactions": ("相互作用",),
    "safety": ("安全性", "不良反应"), "microbiome": ("微生物",),
}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _placeholders(value: Any, acc: set[str]) -> set[str]:
    if isinstance(value, str):
        acc.update(_PLACEHOLDER.findall(value))
    elif isinstance(value, Mapping):
        for v in value.values():
            _placeholders(v, acc)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _placeholders(v, acc)
    return acc


def _schema_of_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, (list, tuple)):
        return {"type": "array", "items": _schema_of_value(value[0]) if value else {"type": "string"}}
    if isinstance(value, Mapping):
        return {"type": "object", "properties": {}, "additionalProperties": True}
    return {"type": "string"}


def op_schema(op: Any) -> dict[str, Any]:
    names = _placeholders(op.path, set())
    for part in (op.params, op.variables, op.json_body, op.form):
        _placeholders(part, names)
    names |= set(op.args)
    props: dict[str, Any] = {}
    for n in sorted(names):
        if n in op.example:
            schema = _schema_of_value(op.example[n])
            schema["examples"] = [op.example[n]]
            if n not in op.args:
                schema["default"] = op.example[n]
        else:
            schema = {"type": "string"}
        props[n] = schema
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if op.args:
        out["required"] = list(op.args)
    return out


def _connector_entries() -> list[dict[str, Any]]:
    from bioagent.providers.public_apis import SOURCES

    out = []
    for s in SOURCES:
        category = ("literature" if s.domain == "literature"
                    else "tcm_data" if s.key in _TCM_CONNECTORS else "live_sources")
        for op in s.operations:
            example = dict(op.example) if all(a in op.example for a in op.args) else None
            title = f"{s.name} · {op.name}"
            out.append(_entry(
                f"connector.{s.key}.{op.name}", "connector", title, title, category,
                f"{op.description} ({s.name}, {s.host})", op_schema(op), example=example,
                exec_=R, network=True,
                tags=[s.key, s.name, op.name, s.domain, "connector", "API", "在线",
                      *_CONNECTOR_DOMAIN_ZH.get(s.domain, ())],
                hosts=[s.host],
                connector={"key": s.key, "name": s.name, "operation": op.name,
                           "method": op.method, "host": s.host, "license": s.license,
                           "rate_note": s.rate_note, "docs": s.docs, "domain": s.domain}))
    return out


# ------------------------------------------------------------------------- clinic


def _clinic_entries() -> list[dict[str, Any]]:
    tags = ("中医", "临床", "辨证", "四诊", "处方", "clinic", "PHI")
    return [
        _entry("clinic.template", "clinic", "四诊采集模板", "Intake template", "clinic",
               "A blank 四诊 intake (schema bioagent.clinic.intake/1) with its form: 问诊要点 by "
               "十问 group, tongue and pulse reference terms, vital-sign help, and the knowledge "
               "pack's version and review status.", obj({}), example={},
               tags=(*tags, "模板", "问诊", "舌象", "脉象")),
        _entry("clinic.assess", "clinic", "辨证与处方草案", "Differentiation & draft", "clinic",
               "Red-flag screen → syndrome differentiation (主症/次症/舌/脉 rule, questions to ask "
               "next) → a draft prescription for a licensed practitioner to review and sign; "
               "status draft, blocked, needs_information, no_formula or refer. Writes "
               "session.json, report.md and report.html in the project's clinic folder.",
               obj({"intake": INTAKE_SCHEMA, "modifications": arr(MODIFICATION_SCHEMA, default=[]),
                    "apply_textbook": B(default=False), "days": I(minimum=1, maximum=60, default=7)},
                   required=("intake",)),
               tags=(*tags, "辨证论治", "证候", "草案", "红旗征"),
               # The session report is rendered by the omics report writer (numpy, scipy).
               pyodide_packages=["numpy", "scipy"],
               phi=True),
        _entry("clinic.check", "clinic", "处方核查", "Check a prescription", "clinic",
               "Check a practitioner-written prescription against an intake: 十八反/十九畏, "
               "Pharmacopoeia dose ranges, toxicity, pregnancy and breastfeeding, allergies, "
               "conditions, drug interactions, age; severities stop, block, warn, info. No issue "
               "found is not a safety guarantee.",
               obj({"intake": INTAKE_SCHEMA, "herbs": arr(HERB_LINE_SCHEMA, minItems=1)},
                   required=("intake", "herbs")),
               tags=(*tags, "剂量", "十八反", "十九畏", "妊娠", "过敏"), phi=True),
        _entry("clinic.followup", "clinic", "复诊评估", "Follow-up", "clinic",
               "Follow-up visit against baseline: weighted symptom scores (无/轻/中/重), reduction "
               "and category (临床痊愈/显效/有效/无效/加重), adverse events, next actions. It "
               "describes change; it does not attribute it to the treatment.",
               obj({"syndrome": S(), "baseline": VISIT_SCHEMA, "current": VISIT_SCHEMA},
                   required=("baseline", "current")),
               example={"syndrome": "脾胃气虚证",
                        "baseline": {"date": "2026-10-01", "scores": {"神疲乏力": "重", "食少": "中",
                                                                      "便溏": "中", "腹胀": "轻"}},
                        "current": {"date": "2026-10-08", "scores": {"神疲乏力": "轻", "食少": "轻",
                                                                     "便溏": "无", "腹胀": "无"},
                                    "adherence": "full"}},
               tags=(*tags, "复诊", "疗效评价", "证候积分", "不良事件"), phi=True),
        _entry("clinic.verify", "clinic", "核验诊疗记录", "Verify a session", "clinic",
               "Re-check a stored clinic session of this project: the digests of session.json, "
               "the reports and any sign-off. A changed session no longer verifies.",
               obj({"session_id": S("the session_id clinic.assess returned")},
                   required=("session_id",)),
               tags=(*tags, "核验", "哈希", "审计")),
    ]


# --------------------------------------------------------------------- data hub


def _tcmdb_vocab() -> tuple[list[str], list[str], list[str]]:
    try:
        from bioagent.tcmdb import DATASETS, RELATION_KINDS
        kinds = sorted(RELATION_KINDS)
        keys = [d.key for d in DATASETS]
        fetchable = [d.key for d in DATASETS if d.access not in ("manual", "live")]
        return kinds, keys, fetchable
    except Exception:                                           # noqa: BLE001
        return [], [], []


def _tcmdb_entries() -> list[dict[str, Any]]:
    kinds, keys, _ = _tcmdb_vocab()
    kind_s = S("relation kind", enum=kinds) if kinds else S("relation kind")
    dataset_s = S("dataset key", enum=keys) if keys else S("dataset key")
    ev = ["known", "predicted", "aggregated", "listed", "reported", "mentioned", "signal",
          "associated"]
    support = ["independently_replicated", "documented", "associated", "integrated",
               "mentioned", "predicted", "signal", "tested_negative", "inconclusive"]
    tags = ("中医药数据", "数据枢纽", "数据库", "tcmdb", "TCM data")
    hub_note = "Needs the local runner with the dataset fetched and built (tcmdb.fetch / tcmdb.build jobs)."
    return [
        _entry("tcmdb.catalog", "tcmdb", "中医药数据源目录", "TCM data sources", "tcm_data",
               "The 136 TCM source cards: access mode (live_api, snapshot, manual_import, "
               "restricted, unreachable), licence, commercial use, barriers, assessment; on the "
               "runner also whether each dataset is downloaded and built.",
               obj({"query": S(), "module": S("M1..M14"),
                    "access": S(enum=["live_api", "live_api+snapshot", "snapshot",
                                      "manual_import", "restricted", "unreachable"])}),
               example={"query": "HERB"}, tags=(*tags, "数据源", "许可", "授权")),
        _entry("tcmdb.datasets", "tcmdb", "可下载数据集", "Dataset specifications", "tcm_data",
               "The 76 dataset specifications: access (download, manual, live), licence and "
               "its reuse class, commercial use, relation kinds, hosts and expected sizes.",
               obj({"query": S(), "access": S(enum=["download", "manual", "live"])}),
               example={"query": "ddid"}, tags=(*tags, "数据集", "下载", "大小")),
        _entry("tcmdb.relation_kinds", "tcmdb", "关系类型与词表", "Relation kinds & vocabularies",
               "tcm_data", "The relation kinds the hub extracts (subject → object types), the "
               "evidence, effect and outcome vocabularies, and the relation row columns.",
               obj({}), example={}, tags=(*tags, "关系", "词表", "证据类型")),
        _entry("tcmdb.status", "tcmdb", "本地数据状态", "Local data status", "tcm_data",
               "Per dataset: files present, missing default files, whether it is built, table "
               "row counts and relation counts.", obj({"dataset": dataset_s}),
               exec_=R, tags=(*tags, "状态", "已构建")),
        _entry("tcmdb.tables", "tcmdb", "数据表结构", "Tables of a dataset", "tcm_data",
               f"Tables and columns of a built dataset. {hub_note}",
               obj({"dataset": dataset_s}, required=("dataset",)), exec_=R,
               tags=(*tags, "表", "字段")),
        _entry("tcmdb.query", "tcmdb", "数据表查询", "Query a table", "tcm_data",
               "Rows of one source table: where = exact equality, contains = case-insensitive "
               f"substring, both parameterised; column names are checked. {hub_note}",
               obj({"dataset": dataset_s, "table": S(),
                    "where": obj({}, additional=True), "contains": obj({}, additional=S()),
                    "columns": arr(S()), "limit": I(minimum=1, maximum=10000, default=50),
                    "offset": I(minimum=0, default=0)}, required=("dataset", "table")),
               exec_=R, tags=(*tags, "查询", "SQL")),
        _entry("tcmdb.relations", "tcmdb", "数据枢纽关系查询", "Hub relations", "tcm_data",
               "Relations of one shape across every built store, each row with its source, "
               "evidence kind, reference and licence; limit is per source. Predicted, "
               f"aggregated and signal rows support hypotheses only. {hub_note}",
               obj({"kind": kind_s, "subject": S(), "object": S(), "contains": B(default=False),
                    "evidence": arr(S(enum=ev)),
                    "outcomes": arr(S(enum=["positive", "negative", "inconclusive"])),
                    "sources": arr(S()), "commercial": B(default=False),
                    "limit": I("per source", minimum=1, maximum=500, default=50)}),
               exec_=R, tags=(*tags, "关系", "成分", "靶点", "药物相互作用", "herb_ingredient")),
        _entry("tcmdb.consensus", "tcmdb", "多源一致性", "Cross-source consensus", "tcm_data",
               "One relation kind reconciled across sources: ids unified, copies counted once, "
               f"evidence kinds kept apart, contradictions and silence reported. {hub_note}",
               obj({"kind": kind_s, "subject": S(), "object": S(), "sources": arr(S()),
                    "contains": B(default=False), "min_support": S(enum=support),
                    "merge_processed": B(default=False),
                    "limit": I("items returned", minimum=1, maximum=500, default=50)},
                   required=("kind",)), exec_=R, tags=(*tags, "一致性", "独立来源", "共识")),
        _entry("tcmdb.compare", "tcmdb", "来源比较", "Compare sources", "tcm_data",
               f"Per-source object sets of one subject: shared, source-only, overlaps. {hub_note}",
               obj({"kind": kind_s, "subject": S(), "sources": arr(S()),
                    "merge_processed": B(default=False)}, required=("kind", "subject")),
               exec_=R, tags=(*tags, "比较", "重叠")),
        _entry("tcmdb.evidence_for", "tcmdb", "临床试验与文献记录", "Trials and papers recorded",
               "tcm_data", "Clinical trials, meta-analyses and papers recorded for a herb, formula "
               f"or ingredient across the built stores. {hub_note}",
               obj({"subject": S(), "limit": I("per source", minimum=1, maximum=500, default=50),
                    "contains": B(default=False)}, required=("subject",)),
               exec_=R, tags=(*tags, "临床试验", "文献", "荟萃分析")),
        _entry("tcmdb.licences", "tcmdb", "数据许可", "Licences of built data", "tcm_data",
               "Each built dataset's relation kinds with their licence text, reuse class and "
               "whether commercial reuse is allowed.", obj({}), exec_=R,
               tags=(*tags, "许可", "商用", "licence")),
        _entry("tcmdb.unresolved", "tcmdb", "未解析记录", "Unresolved rows", "tcm_data",
               f"Rows a source gave whose object it could not identify. {hub_note}",
               obj({"dataset": dataset_s, "limit": I(minimum=1, maximum=1000, default=50)},
                   required=("dataset",)), exec_=R, tags=(*tags, "未解析")),
    ]


# -------------------------------------------------------------------------- studies


def _study_entries() -> list[dict[str, Any]]:
    group = obj({"label": S("e.g. formula, monomer:berberine"), "values": arr(N(), minItems=2),
                 "study": S("the same study for both groups"), "intervention": S(), "dose": S()},
                required=("label", "values", "study"))
    measurement = obj({"qualifier": S(enum=["measured", "below_lod", "above_max_tested",
                                            "not_detected", "not_measured"]),
                       "value": N(), "unit": S("e.g. uM, ng/mL"), "bound": N()},
                      required=("qualifier",))
    tags = ("研究设计", "可证伪", "统计", "study design")
    return [
        _entry("study.contrast_formula_monomer", "study", "复方与单体对照", "Formula vs monomer",
               "study_design",
               "Whole formula versus its main constituent on one endpoint: Welch difference "
               "with its interval, TOST equivalence against a prespecified margin, a verdict, "
               "and what may and may not be concluded.",
               obj({"formula": group, "monomer": group, "higher_is_better": B(default=True),
                    "margin": N("equivalence margin, fixed in advance, endpoint units"),
                    "alpha": N(minimum=0.001, maximum=0.2, default=0.05),
                    "dose_matched": {"anyOf": [B(), {"type": "null"}]}},
                   required=("formula", "monomer")),
               example={"formula": {"label": "GQD", "values": [2.1, 2.4, 2.0, 2.6, 2.3, 2.2],
                                    "study": "demo"},
                        "monomer": {"label": "berberine", "values": [1.8, 1.7, 2.0, 1.6, 1.9, 1.75],
                                    "study": "demo"}, "margin": 0.5},
               tags=(*tags, "复方", "单体", "等效性", "对照"), pyodide_packages=["numpy"]),
        _entry("study.combination", "study", "联合用药分析", "Combination analysis",
               "study_design",
               "Dose-matrix combination analysis against a reference model chosen in advance "
               "(Bliss, HSA or Loewe) with bootstrap intervals and a cytotoxicity screen. "
               "Excess over a model is not synergy of mechanism.",
               obj({"cells": arr(obj({"dose_a": N(minimum=0), "dose_b": N(minimum=0),
                                      "effect": arr(N(), "replicate fractions in [0, 1]", minItems=1),
                                      "cytotoxicity": arr(N())},
                                     required=("dose_a", "dose_b", "effect")), minItems=3),
                    "primary": S(enum=["bliss", "hsa", "loewe"]), "seed": I(default=0),
                    "boot": I(minimum=10, maximum=5000, default=200),
                    "cytotoxic_above": N(default=0.5), "level": N(default=0.95)},
                   required=("cells", "primary")),
               tags=(*tags, "联合用药", "协同", "Bliss", "Loewe"), pyodide_packages=["numpy"]),
        _entry("study.effect_modification", "study", "效应修饰分析", "Effect modification",
               "study_design",
               "Does a baseline feature modify the treatment effect? An interaction across both "
               "arms, BH-adjusted over the features tested; it is not responder prediction.",
               obj({"outcome": arr(N(), minItems=4), "treated": arr(I(enum=[0, 1]), minItems=4),
                    "modifiers": arr(obj({"name": S(), "values": arr(N()),
                                          "baseline": B("measured before the intervention")},
                                         required=("name", "values", "baseline")), minItems=1),
                    "baseline_outcome": arr(N()), "prespecified": I(minimum=1),
                    "level": N(default=0.95)},
                   required=("outcome", "treated", "modifiers")),
               tags=(*tags, "异质性", "交互作用", "亚组"), pyodide_packages=["numpy"]),
        _entry("study.exposure_screen", "study", "暴露-活性筛查", "Exposure screen",
               "study_design",
               "Can an in-vitro activity occur at the exposure reached at the site of action? "
               "Unit conversion, free-fraction bounds, censored values; an assay with no "
               "exposure measured at the site is undeterminable, not judged against plasma.",
               obj({"exposures": arr(obj({"molecule": S(), "species": S(), "site": S(),
                                          "time_h": {"anyOf": [N(), {"type": "null"}]},
                                          "concentration": measurement, "free": B(default=False),
                                          "free_fraction": arr(N(), minItems=2, maxItems=2),
                                          "method": S(), "study": S()},
                                         required=("molecule", "species", "site", "concentration"))),
                    "assays": arr(obj({"molecule": S(), "target": S(), "endpoint": S(),
                                       "result": measurement, "species": S(), "system": S(),
                                       "method": S(), "flags": arr(S()), "study": S()},
                                      required=("molecule", "target", "endpoint", "result"))),
                    "site": S("plasma | tissue:<uberon> | gut_lumen …"),
                    "mw": obj({}, description="molecular weight per molecule (g/mol)",
                              additional=N())},
                   required=("exposures", "assays", "site")),
               tags=(*tags, "暴露", "血药浓度", "IC50", "体外活性")),
    ]


# ----------------------------------------------------------------------- job kinds

_UPLOAD = "an upload id (POST /api/uploads) or a path the runner allows"

#: Parameters of the runner's job kinds, as Studio offers them. The runner maps each kind
#: to its fixed command line; these are what dispatch passes to ``jobs.submit``.
JOB_KINDS: dict[str, dict[str, Any]] = {
    "pipeline.rnaseq": {
        "title": ("RNA-seq 差异表达流程", "RNA-seq pipeline"), "category": "omics",
        "summary": "FASTQ + sample sheet → QC, trimming, quantification (built-in k-mer EM, "
                   "salmon, kallisto or HISAT2), DESeq2-method differential expression, report.",
        "parameters": obj({"samples": S(f"sample sheet CSV: {_UPLOAD}"),
                           "transcripts": S(f"transcriptome FASTA: {_UPLOAD}"),
                           "annotation": S(f"GTF or tx2gene table: {_UPLOAD}"),
                           "genome": S(f"genome FASTA (hisat2): {_UPLOAD}"),
                           "design": S(default="~ condition"),
                           "contrast": S("factor,numerator,denominator"),
                           "engine": S(enum=["auto", "builtin", "salmon", "kallisto", "hisat2"],
                                       default="auto"),
                           "trimmer": S(enum=["auto", "builtin", "fastp", "none"], default="auto"),
                           "de_backend": S(enum=["builtin", "pydeseq2"], default="builtin"),
                           "alpha": N(minimum=0.0001, maximum=0.5, default=0.05)},
                          required=("samples",)),
        "optional": ["pydeseq2", "salmon", "kallisto", "hisat2", "fastp"], "duration": "hours",
        "tags": ("转录组", "差异表达", "RNA-seq", "FASTQ")},
    "pipeline.scrna": {
        "title": ("单细胞分析流程", "Single-cell pipeline"), "category": "omics",
        "summary": "Count matrices → QC, doublets, integration (Harmony or scVI), Leiden "
                   "clusters, markers, annotation, PAGA, pseudotime, pseudobulk, report.",
        "parameters": obj({"source": S(f"10x directory, .h5, .h5ad, CSV or sheet: {_UPLOAD}"),
                           "doublets": S(enum=["remove", "flag", "off"], default="remove"),
                           "analysis_backend": S(enum=["builtin", "scanpy"], default="builtin"),
                           "integration": S(enum=["none", "harmony", "scvi"], default="harmony"),
                           "resolution": N(minimum=0.05, maximum=5, default=1.0),
                           "markers": S(f"marker table: {_UPLOAD}"), "root": S("root cell type"),
                           "contrast": S("pseudobulk contrast"), "seed": I(default=0)},
                          required=("source",)),
        # bioagent runs scVI on the CPU by design, and the runner gives this kind no GPU (/api/kinds says the same)
        "optional": ["scanpy", "harmonypy", "leidenalg", "scvi-tools"], "gpu": False,
        "duration": "minutes", "tags": ("单细胞", "scRNA", "聚类")},
    "pipeline.fold": {
        "title": ("蛋白结构预测流程", "Structure prediction"), "category": "structure_molecules",
        "summary": "Protein structure prediction (ESM Atlas remote, ESMFold local, ColabFold) "
                   "with pLDDT, DSSP, geometry and TM-score/RMSD against a reference.",
        "parameters": obj({"source": S(f"a FASTA file ({_UPLOAD}) or one sequence"),
                           "method": S(enum=["esmatlas", "esmfold", "colabfold"],
                                       default="esmatlas"),
                           "allow_remote": B("esmatlas sends the sequence to api.esmatlas.com",
                                             default=False),
                           "reference": arr(S("NAME=PDB:1abc[:A] | UniProt:P… | upload id")),
                           "timeout": I(minimum=10, maximum=86400, default=300)},
                          required=("source",)),
        "optional": ["torch", "transformers", "colabfold_batch"], "gpu": True,
        "network_if": "allow_remote", "hosts": ["api.esmatlas.com", "files.rcsb.org",
                                                "alphafold.ebi.ac.uk"],
        "duration": "minutes", "tags": ("结构预测", "折叠", "ESMFold")},
    "pipeline.dock": {
        "title": ("分子对接流程", "Docking"), "category": "structure_molecules",
        "summary": "AutoDock Vina docking with a redocking check of the site: scores, ligand "
                   "efficiency, contacts and poses.",
        "parameters": obj({"receptor": S(f"PDB file ({_UPLOAD}), PDB:<id>[:chains] or UniProt:<acc>"),
                           "ligands": S(f"CSV name,smiles / SDF / .smi ({_UPLOAD}) or one SMILES"),
                           "site_ligand": S("co-crystal ligand residue name"),
                           "center": arr(N(), minItems=3, maxItems=3),
                           "size": arr(N(), minItems=3, maxItems=3),
                           "chains": S(), "scoring": S(enum=["vina", "vinardo"], default="vina"),
                           "exhaustiveness": I(minimum=1, maximum=64, default=8),
                           "poses": I(minimum=1, maximum=20, default=9), "seed": I(default=42),
                           "allow_remote": B("fetch PDB/UniProt structures from RCSB or AlphaFold DB",
                                             default=False)},
                          required=("receptor", "ligands")),
        "heavy": ["rdkit", "meeko", "vina", "gemmi"], "network_if": "allow_remote",
        "hosts": ["files.rcsb.org", "data.rcsb.org", "alphafold.ebi.ac.uk"],
        "duration": "minutes", "tags": ("对接", "Vina", "配体")},
    "pipeline.admet": {
        "title": ("ADMET 预测流程", "ADMET"), "category": "structure_molecules",
        "summary": "ADMET: standardisation, descriptors, Lipinski/Veber/Egan/Ghose, PAINS/Brenk/"
                   "NIH alerts, and 22 TDC-trained endpoints once the models are built. "
                   "Predicted, not measured.",
        "parameters": obj({"molecules": S(f"CSV name,smiles / SDF / .smi ({_UPLOAD}) or one SMILES"),
                           "endpoints": arr(S())}, required=("molecules",)),
        "heavy": ["rdkit", "scikit-learn"], "duration": "minutes",
        "tags": ("ADMET", "成药性", "毒性预测")},
    "research.run": {
        "title": ("研究闭环", "Research loop"), "category": "netpharm",
        "summary": "Closed research loop on ledger snapshots: frozen protocol → retrieve → "
                   "analyse (a PSH tool call) → pre-registered rebuttals → attested release.",
        "parameters": obj({"question": S("the research question; the formula is read from it"),
                           "formula": S("formula name, e.g. 葛根芩连汤"),
                           "disease": S("e.g. MONDO_0005148"), "activity": arr(S()),
                           "optional": arr(S()),
                           "background": S(enum=["assayed", "reactome"], default="assayed"),
                           "hits": S(enum=["potency", "screening"], default="potency"),
                           "permutations": I(minimum=100, maximum=100000, default=1000),
                           "accept_review": B(default=False)}, required=("question",)),
        "heavy": ["pyarrow"], "duration": "minutes",
        "tags": ("网络药理", "研究闭环", "通路富集", "置换检验")},
    "skill.run": {
        "title": ("受治理 Skill 后台运行", "Governed skill (background)"), "category": "system",
        "summary": "Run any governed skill as a background job on the runner, with the project's "
                   "durable audit chain. Unpinned candidates run as development runs: recorded, "
                   "never released.",
        "parameters": obj({"skill_id": S(), "arguments": obj({}, additional=True),
                           "allow_unpinned": B(default=False)}, required=("skill_id", "arguments")),
        "duration": "minutes", "tags": ("Skill", "受治理", "后台")},
    "tcmdb.fetch": {
        "title": ("下载数据集", "Fetch a dataset"), "category": "tcm_data",
        "summary": "Download a dataset's files into the runner's data hub (checksums recorded). "
                   "A dataset above the 512 MB gate needs the user's own confirmation in the "
                   "data view.",
        "parameters": obj({"dataset": S(), "include_optional": B(default=False)},
                          required=("dataset",)),
        "network": True, "duration": "minutes", "tags": ("下载", "数据集")},
    "tcmdb.build": {
        "title": ("构建数据集", "Build a dataset"), "category": "tcm_data",
        "summary": "Load downloaded files into the dataset's SQLite store and extract its "
                   "relations (staged, then replaced atomically).",
        "parameters": obj({"dataset": S()}, required=("dataset",)),
        "duration": "minutes", "tags": ("构建", "SQLite")},
}


def _job_entries() -> list[dict[str, Any]]:
    _, keys, fetchable = _tcmdb_vocab()
    out = []
    for kind, spec in JOB_KINDS.items():
        params = copy.deepcopy(spec["parameters"])
        if kind == "tcmdb.fetch" and fetchable:
            params["properties"]["dataset"]["enum"] = fetchable
        if kind == "tcmdb.build" and keys:
            params["properties"]["dataset"]["enum"] = keys + ["all"]
        if kind == "skill.run":
            ids = [s["id"] for s in skill_specs()]
            if ids:
                params["properties"]["skill_id"]["enum"] = ids
        zh, en = spec["title"]
        out.append(_entry(
            f"job.{kind}", "job", zh, en, spec["category"], spec["summary"], params,
            exec_=R, network=bool(spec.get("network")), confirm=True, job=True,
            gpu=bool(spec.get("gpu")), heavy=spec.get("heavy", ()),
            tags=[kind, "job", "任务", "后台任务", *spec.get("tags", ())],
            hosts=spec.get("hosts"), optional=spec.get("optional"),
            network_if=spec.get("network_if"), duration=spec.get("duration")))
    return out


# ------------------------------------------------------------------------- system


def _system_entries() -> list[dict[str, Any]]:
    tags = ("系统", "环境", "审计", "system")
    return [
        _entry("system.capabilities", "system", "运行能力", "Capabilities", "system",
               "What can run now and where: runtime, versions, network profile, purpose, "
               "optional dependencies, the project's audit-chain location, and counts of "
               "runnable entries.", obj({}), example={}, tags=(*tags, "能力", "状态", "设备")),
        _entry("system.catalog_search", "system", "能力检索", "Search capabilities", "system",
               "Search the catalog by Chinese or English keywords, with filters by category, "
               "kind and what can run now.",
               obj({"query": S(), "category": S(enum=list(CATEGORY_IDS)),
                    "kind": S(enum=list(ENTRY_KINDS)), "runnable_now": B(default=False),
                    "limit": I(minimum=1, maximum=25, default=10)}),
               example={"query": "十八反"}, tags=(*tags, "检索", "目录")),
        _entry("system.job_status", "system", "任务状态", "Job status", "system",
               "State, progress and (once succeeded) outcome and files of a runner job.",
               obj({"job_id": S(), "wait_s": I(minimum=0, maximum=60, default=0)},
                   required=("job_id",)), exec_=R, tags=(*tags, "任务", "进度")),
        _entry("system.doctor", "system", "安装诊断", "Installation check", "system",
               "What this installation can do: backends, datasets, connectors, native tools, "
               "PSH isolation, problems with remedies (bioagent doctor).",
               obj({"smoke": B("also run every native tool's example", default=False)}),
               exec_=R, tags=(*tags, "诊断", "doctor")),
        _entry("system.skills", "system", "受治理 Skill 列表", "Governed skills", "system",
               "The governed skills: version, pin, claim kinds, forbidden claims, evidence "
               "ceiling, network hosts, risk and autonomy.", obj({}), example={},
               tags=(*tags, "Skill", "受治理", "锁定")),
        _entry("system.classify", "system", "敏感信息分级", "Classify text", "system",
               "PSH's local label for a text (PUBLIC … PHI, SECRET) and which destinations it "
               "may reach (local compute, local model, trusted or public remote). Advisory; "
               "nothing leaves the machine.",
               obj({"text": S(maxLength=200000)}, required=("text",)),
               example={"text": "患者张三，住院号 123456"}, tags=(*tags, "隐私", "PHI", "脱敏", "分级")),
        _entry("system.audit_verify", "system", "审计链核验", "Verify the audit chain", "system",
               "Verify the hash chain of this project's governed runs: record count, head hash, "
               "event types, and where it breaks if it does.", obj({}), example={},
               tags=(*tags, "审计链", "哈希链", "核验")),
    ]


# ----------------------------------------------------------------------- assembly

_SECTION_BUILDERS = {"native": _native_entries, "skill": _skill_entries,
                     "connector": _connector_entries, "clinic": _clinic_entries,
                     "tcmdb": _tcmdb_entries, "study": _study_entries, "job": _job_entries,
                     "system": _system_entries}
_SECTIONS: dict[str, list[dict[str, Any]]] = {}
_INDEX: dict[str, dict[str, Any]] = {}
_FAILED: dict[str, str] = {}


def _section(kind: str) -> list[dict[str, Any]]:
    if kind not in _SECTIONS:
        try:
            built = _SECTION_BUILDERS[kind]()
        except Exception as exc:                                # noqa: BLE001
            # One unreadable source (a missing optional manifest, a broken template) must not
            # take the whole catalog with it; the failure is reported in the document.
            _FAILED[kind] = f"{type(exc).__name__}: {exc}"
            built = []
        _SECTIONS[kind] = built
        for e in built:
            _INDEX[e["id"]] = e
    return _SECTIONS[kind]


def get_entry(entry_id: str) -> dict[str, Any] | None:
    """One entry by id, building only the section it belongs to. The returned dict is
    shared: do not modify it."""
    entry_id = str(entry_id or "")
    kind = entry_id.split(".", 1)[0]
    if kind not in _SECTION_BUILDERS:
        return None
    _section(kind)
    return _INDEX.get(entry_id)


def entry_ids() -> list[str]:
    return [e["id"] for kind in _SECTION_BUILDERS for e in _section(kind)]


_MODULES = {"rdkit": "rdkit", "meeko": "meeko", "vina": "vina", "gemmi": "gemmi",
            "scikit-learn": "sklearn", "torch": "torch", "transformers": "transformers",
            "scanpy": "scanpy", "harmonypy": "harmonypy", "leidenalg": "leidenalg",
            "scvi-tools": "scvi", "paper-qa": "paperqa", "pydeseq2": "pydeseq2",
            "pyarrow": "pyarrow", "numpy": "numpy", "scipy": "scipy", "pandas": "pandas",
            "openpyxl": "openpyxl"}
_BINARIES = {"salmon", "kallisto", "hisat2", "fastp", "colabfold_batch"}


def dependency_present(name: str) -> bool:
    """Whether an optional package (or command-line tool) is installed here. Asks the
    import system and PATH; imports nothing and runs nothing."""
    if name in _BINARIES:
        try:
            return shutil.which(name) is not None
        except Exception:                                       # noqa: BLE001
            return False
    try:
        return importlib.util.find_spec(_MODULES.get(name, name)) is not None
    except (ImportError, ValueError):
        return False


def entries(where: str = "runner", probe: bool = True) -> list[dict[str, Any]]:
    """Every entry (copies). On the runner, ``probe`` marks entries whose required optional
    dependencies are missing with ``available: false`` and ``missing``; in the browser
    nothing is probed (this machine is not the user's browser)."""
    out = []
    cache: dict[str, bool] = {}
    for kind in _SECTION_BUILDERS:
        for e in _section(kind):
            item = copy.deepcopy(e)
            if where == "runner" and probe:
                missing = [d for d in item.get("heavy") or ()
                           if not cache.setdefault(d, dependency_present(d))]
                if missing:
                    item["available"] = False
                    item["missing"] = missing
                else:
                    item["available"] = True
                optional = item.get("optional") or ()
                if optional:
                    item["optional_present"] = [d for d in optional
                                                if cache.setdefault(d, dependency_present(d))]
            out.append(item)
    return out


def build_catalog(where: str = "runner", probe: bool = True) -> dict[str, Any]:
    """The §2 catalog document for ``where`` ("runner" or "browser")."""
    from .envelope import versions

    where = "browser" if where == "browser" else "runner"
    items = entries(where, probe)
    counts: dict[str, int] = {c: 0 for c in CATEGORY_IDS}
    for e in items:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    # The packages the catalog was built from; the interpreter is reported per call
    # (the browser catalog is built on another machine than the one it describes).
    built_from = {k: v for k, v in versions().items() if k != "python"}
    doc: dict[str, Any] = {
        "schema": SCHEMA_ID, "versions": built_from, "where": where,
        "categories": [{"id": cid, "zh": zh, "en": en, "count": counts.get(cid, 0)}
                       for cid, zh, en in CATEGORIES],
        "core": copy.deepcopy(list(CORE_TOOLS)), "entries": items,
        "counts": {"core": len(CORE_TOOLS), "entries": len(items),
                   "by_kind": {k: sum(1 for e in items if e["kind"] == k) for k in ENTRY_KINDS}}}
    if _FAILED:
        doc["problems"] = [{"section": k, "error": v} for k, v in sorted(_FAILED.items())]
    return doc


# ------------------------------------------------------------------------- search

_SPLIT = re.compile(r"[\s,，、;；/|()（）\[\]{}:：。.?？!！\"'“”‘’]+")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_KIND_ORDER = {k: i for i, k in enumerate(("native", "skill", "clinic", "tcmdb", "study",
                                           "system", "job", "connector"))}


def _tokens(query: str) -> list[str]:
    return [t for t in _SPLIT.split(query.lower()) if t]


def _bigrams(text: str) -> set[str]:
    chars = [c for c in text if _CJK.match(c)]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


_SEARCH_TEXT: dict[str, dict[str, Any]] = {}


def _search_fields(e: Mapping[str, Any]) -> dict[str, Any]:
    cached = _SEARCH_TEXT.get(e["id"])
    if cached is None:
        cat = next((c for c in CATEGORIES if c[0] == e["category"]), ("", "", ""))
        example = e.get("example")
        cached = {
            "id": e["id"].lower(), "short": e["id"].rsplit(".", 1)[-1].lower(),
            "zh": e["title"]["zh"].lower(), "en": e["title"]["en"].lower(),
            "tags": [t.lower() for t in e.get("tags") or ()],
            "summary": str(e.get("summary") or "").lower(),
            "category": f"{cat[1]} {cat[2]} {e['category']}".lower(),
            "example": str(example).lower() if example else ""}
        _SEARCH_TEXT[e["id"]] = cached
    return cached


def _score(e: Mapping[str, Any], tokens: list[str], full: str) -> float:
    f = _search_fields(e)
    score = 0.0
    matched = 0
    for tok in tokens:
        s = 0.0
        if tok == f["id"] or tok == f["short"]:
            s += 40
        elif tok in f["id"]:
            s += 12
        if tok == f["zh"] or tok == f["en"]:
            s += 25
        elif tok in f["zh"] or tok in f["en"]:
            s += 10
        if tok in f["tags"]:
            s += 9
        elif any(tok in t for t in f["tags"]):
            s += 5
        if tok in f["category"]:
            s += 3
        if tok in f["summary"]:
            s += 2
        if len(tok) > 1 and tok in f["example"]:
            s += 1
        if s:
            matched += 1
        score += s
    # A query written without spaces (常见于中文): credit tags and titles it contains.
    for tag in f["tags"]:
        if len(tag) >= 2 and _CJK.search(tag) and tag in full:
            score += 7
    if len(f["zh"]) >= 2 and f["zh"] in full:
        score += 8
    q_bi = _bigrams(full)
    if q_bi:
        hay = f["zh"] + " " + " ".join(f["tags"]) + " " + f["example"]
        hits = sum(1 for b in q_bi if b in hay)
        score += min(hits, 6) * 1.5
    if tokens and matched == len(tokens):
        score *= 1.3
    return score


def runnable_here(e: Mapping[str, Any], *, where: str, network: bool = False,
                  jobs: bool = False) -> bool:
    if where not in (e.get("exec") or ()):
        return False
    if e.get("available") is False:
        return False
    if e.get("job") and not jobs:
        return False
    return not (e.get("network") and not network)


def _needs(e: Mapping[str, Any], *, where: str, network: bool, jobs: bool) -> list[str]:
    out = []
    if where not in (e.get("exec") or ()):
        out.append("the local runner (tcmstudio serve)" if "runner" in (e.get("exec") or ())
                   else "the browser runtime")
    if e.get("available") is False and e.get("missing"):
        out.append("install: " + ", ".join(e["missing"]))
    if e.get("network") and not network:
        out.append("web access for this project")
    if e.get("job") and not jobs:
        out.append("the runner's job service")
    return out


def catalog_search(query: str = "", category: str | None = None, kind: str | None = None,
                   runnable_now: bool = False, limit: int = 10, where: str = "runner", *,
                   network: bool = False, jobs: bool = False,
                   items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Ranked entries for a query (ids, titles, tags, summaries; Chinese substrings count),
    each with its full parameter schema so the model can call it."""
    limit = max(1, min(int(limit or 10), 25))
    query = str(query or "").strip()
    pool = items if items is not None else [e for k in _SECTION_BUILDERS for e in _section(k)]
    tokens = _tokens(query)
    full = query.lower()
    scored = []
    for e in pool:
        if category and e["category"] != category:
            continue
        if kind and e["kind"] != kind:
            continue
        if runnable_now and not runnable_here(e, where=where, network=network, jobs=jobs):
            continue
        s = _score(e, tokens, full) if tokens else 1.0
        # A lone shared character pair (a bigram such as 分析) is noise, not a match.
        if s >= (3.0 if tokens else 0.5):
            scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], _KIND_ORDER.get(x[1]["kind"], 9), x[1]["id"]))
    matched = []
    for s, e in scored[:limit]:
        run = runnable_here(e, where=where, network=network, jobs=jobs)
        matched.append({"id": e["id"], "kind": e["kind"], "category": e["category"],
                        "title": dict(e["title"]), "summary": e["summary"],
                        "parameters": copy.deepcopy(e["parameters"]),
                        "example": copy.deepcopy(e.get("example")),
                        "exec": list(e.get("exec") or ()), "runnable_now": run,
                        "needs": [] if run else _needs(e, where=where, network=network, jobs=jobs),
                        "network": bool(e.get("network")), "confirm": bool(e.get("confirm")),
                        "job": bool(e.get("job")), "score": round(s, 2)})
    counts: dict[str, int] = {}
    for e in pool:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    return {"query": query, "total": len(scored), "matched": matched,
            "categories": {cid: {"zh": zh, "en": en, "count": counts.get(cid, 0)}
                           for cid, zh, en in CATEGORIES},
            "how": "Run an entry with call_tool {\"tool\": \"<id>\", \"arguments\": {…}} using "
                   "its parameters schema."}


def suggest(name: str, n: int = 3) -> list[str]:
    """Ids close to an unknown name, for a 'did you mean' hint."""
    name = str(name or "").strip()
    if not name:
        return []
    from .core_tools import CORE_BY_NAME
    ids = entry_ids()
    pool = ids + list(CORE_BY_NAME)
    close = difflib.get_close_matches(name, pool, n=n, cutoff=0.6)
    if len(close) < n:
        tails: dict[str, str] = {}
        for i in ids:
            tails.setdefault(i.rsplit(".", 1)[-1], i)
        for tail in difflib.get_close_matches(name.rsplit(".", 1)[-1], list(tails), n=n,
                                              cutoff=0.75):
            close.append(tails[tail])
    if len(close) < n:
        found = catalog_search(name.replace(".", " ").replace("_", " "), limit=n)["matched"]
        close += [m["id"] for m in found if m["score"] >= 10]
    return list(dict.fromkeys(c for c in close if c))[:n]
