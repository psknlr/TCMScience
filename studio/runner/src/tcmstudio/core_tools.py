"""The core tools offered to the model every turn (CONTRACTS §2), and how each reaches its
catalog entry.

Names are fixed by the contract. Each description is written for the model: what the tool
does, what it returns, and the limits it must repeat. Every array schema has ``items`` and
every object schema has ``properties``, so the definitions convert cleanly to OpenAI
functions and Anthropic tools. ``route`` maps a core call to the entry it executes; the
mapping was checked against the real functions (the P0 skills take ``subject``, ``names``
and ``formula_name``, not the ``query`` their manifests declare).
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

__all__ = ["CORE_TOOLS", "CORE_BY_NAME", "CATEGORY_IDS", "ENTRY_KINDS", "CLAIM_KINDS",
           "INTAKE_SCHEMA", "VISIT_SCHEMA", "HERB_LINE_SCHEMA", "MODIFICATION_SCHEMA",
           "PIPELINES", "route", "core_tools", "S", "I", "N", "B", "arr", "obj"]

# ------------------------------------------------------------------ schema helpers


def S(description: str = "", **kw: Any) -> dict[str, Any]:
    return {"type": "string", **({"description": description} if description else {}), **kw}


def I(description: str = "", **kw: Any) -> dict[str, Any]:            # noqa: E743
    return {"type": "integer", **({"description": description} if description else {}), **kw}


def N(description: str = "", **kw: Any) -> dict[str, Any]:
    return {"type": "number", **({"description": description} if description else {}), **kw}


def B(description: str = "", **kw: Any) -> dict[str, Any]:
    return {"type": "boolean", **({"description": description} if description else {}), **kw}


def arr(items: Mapping[str, Any] | None = None, description: str = "", **kw: Any) -> dict[str, Any]:
    return {"type": "array", "items": dict(items) if items is not None else {"type": "string"},
            **({"description": description} if description else {}), **kw}


def obj(properties: Mapping[str, Any] | None = None, required: tuple[str, ...] | list[str] = (),
        description: str = "", additional: Any = None, **kw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": dict(properties or {})}
    if required:
        out["required"] = list(required)
    if description:
        out["description"] = description
    if additional is not None:
        out["additionalProperties"] = additional
    out.update(kw)
    return out


def _nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"anyOf": [dict(schema), {"type": "null"}]}


CATEGORY_IDS = ("tcm_knowledge", "tcm_safety", "clinic", "tcm_data", "live_sources", "netpharm",
                "study_design", "clinical_calc", "seq_genomics", "structure_molecules", "omics",
                "literature", "system")
ENTRY_KINDS = ("native", "skill", "connector", "clinic", "tcmdb", "study", "job", "system")
CLAIM_KINDS = ("attribution", "traditional_use", "mechanism_hypothesis", "mechanism",
               "safety_signal", "association", "efficacy", "recommendation")
PIPELINES = ("rnaseq", "scrna", "fold", "dock", "admet")

# --------------------------------------------------------------- clinic schemas

_STRINGS = arr(S())
_FINDINGS = obj({"present": arr(S(), "findings present, e.g. 神疲乏力、胃口差"),
                 "absent": arr(S(), "findings asked about and absent")})

INTAKE_SCHEMA = obj({
    "schema": S("always bioagent.clinic.intake/1", enum=["bioagent.clinic.intake/1"]),
    "patient": obj({
        "id": S("a pseudonymous id; never a name"),
        "age": N("years", minimum=0, maximum=130),
        "sex": S(enum=["female", "male", "other", "unknown"]),
        "pregnant": _nullable(B()), "breastfeeding": _nullable(B()),
        "weight_kg": _nullable(N(minimum=0))}, required=("age",)),
    "chief_complaint": S("主诉"), "duration": S("病程"),
    "inspection": obj({"present": _STRINGS, "absent": _STRINGS,
                       "tongue": arr(S(), "舌象, e.g. 舌淡，苔白")}, description="望诊"),
    "listening_smelling": obj({"present": _STRINGS, "absent": _STRINGS}, description="闻诊"),
    "inquiry": obj({"present": _STRINGS, "absent": _STRINGS}, description="问诊"),
    "palpation": obj({"pulse": arr(S(), "脉象, e.g. 脉缓弱"), "present": _STRINGS,
                      "absent": _STRINGS}, description="切诊"),
    "vitals": obj({"temperature_c": N(), "heart_rate": N(), "resp_rate": N(), "systolic": N(),
                   "diastolic": N(), "spo2": N("percent")}),
    "medications": _nullable(arr(S(), "current medications; null = not asked, [] = none")),
    "allergies": _nullable(arr(S(), "null = not asked, [] = none")),
    "conditions": _nullable(arr(S(), "known conditions; null = not asked, [] = none")),
    "red_flags_cleared": arr(obj({"flag": S(), "by": S(), "how": S()},
                                 required=("flag", "by", "how")),
                             "red flags a clinician has examined and excluded"),
    "collected_by": S(), "collected_at": S("ISO date-time"),
}, required=("patient",), description="四诊 intake, schema bioagent.clinic.intake/1")

MODIFICATION_SCHEMA = obj({
    "add": S("herb to add"), "remove": S("herb to remove"), "replace": S("herb to replace"),
    "with": S("replacement herb"), "dose": {"anyOf": [N("grams for add/replace"),
                                                      S("herb whose dose is set (with g)")]},
    "g": N("grams, with dose: HERB")},
    description="one change: {add, dose?} | {remove} | {replace, with, dose?} | {dose: HERB, g}")

HERB_LINE_SCHEMA = obj({"herb": S("herb name, e.g. 黄芪 or 炙甘草"), "grams": N(minimum=0)},
                       required=("herb", "grams"))

VISIT_SCHEMA = obj({
    "date": S("ISO date"),
    "scores": obj({}, description="finding → severity", additional=S(enum=["无", "轻", "中", "重"])),
    "adverse_events": arr(obj({"event": S(), "severity": S(enum=["mild", "moderate", "severe"]),
                               "relation": S(enum=["unrelated", "unlikely", "possible",
                                                   "probable", "certain"])},
                              required=("event",))),
    "new_findings": _STRINGS,
    "adherence": S(enum=["full", "partial", "none", "unknown"]),
    "tongue": S(), "pulse": S(), "notes": S()}, required=("date", "scores"))

_RELATION_EVIDENCE = ["known", "predicted", "aggregated", "listed", "reported", "mentioned",
                      "signal", "associated"]
_SUPPORT = ["independently_replicated", "documented", "associated", "integrated", "mentioned",
            "predicted", "signal", "tested_negative", "inconclusive"]
_ACCESS = ["live_api", "live_api+snapshot", "snapshot", "manual_import", "restricted",
           "unreachable"]

B_R = ["browser", "runner"]
R = ["runner"]


def _core(name: str, zh: str, en: str, category: str, description: str,
          parameters: Mapping[str, Any], maps_to: str | None, exec_: list[str], *,
          network: bool = False, confirm: bool = False, job: bool = False,
          **extra: Any) -> dict[str, Any]:
    params = dict(parameters)
    # A misspelt argument is refused with a hint rather than silently ignored.
    params.setdefault("additionalProperties", False)
    return {"name": name, "title": {"zh": zh, "en": en}, "description": " ".join(description.split()),
            "parameters": params, "category": category, "exec": list(exec_),
            "network": network, "confirm": confirm, "job": job, "maps_to": maps_to, **extra}


CORE_TOOLS: tuple[dict[str, Any], ...] = (
    _core("tcm_lookup", "名称解析", "Resolve a TCM name", "tcm_knowledge", """
        Resolve a herb, processed herb, formula or syndrome name (Chinese, pinyin or Latin) to
        a curated entity or an identity from all 401 materia records and all 84,294 repository
        formula rows. Multiple source versions and ambiguous names (参 → 人参 / 丹参) are
        reported rather than guessed. Processed-form and syndrome annotations remain scoped
        to their recorded corpus. A name not found is not evidence that it does not exist.""",
          obj({"name": S("e.g. 黄芪, huang qi, Astragali Radix"),
               "kind": S("restrict to one kind; empty = any",
                         enum=["", "herb", "processed", "formula", "syndrome"], default="")},
              required=("name",)), "native.tcm_lookup", B_R),
    _core("tcm_herb", "药材", "Herb record", "tcm_knowledge", """
        A herb's identity from the full 401-record materia corpus, plus curated annotations
        where recorded: nature and flavours (性味), meridians (归经), actions, processed forms,
        relations, classical passages and safety records. Identity-only records do not infer
        missing clinical properties. A missing safety record is not evidence of safety.""",
          obj({"name": S("herb name, e.g. 黄芪")}, required=("name",)), "native.tcm_herb", B_R),
    _core("tcm_formula", "方剂", "Formula record", "tcm_knowledge", """
        A formula's source versions from the complete 84,294-row repository workbook,
        preserving literal compositions, doses, source texts, actions, cautions and unresolved
        ingredients. Curated roles, indications and safety records are added only for their
        exact source formula. Use tcmdb.formulas for paginated searches across all fields.
        Workbook data licence is unstated; an indication is an attribution, not evidence of efficacy.""",
          obj({"name": S("formula name, e.g. 桂枝汤")}, required=("name",)),
          "native.tcm_formula", B_R),
    _core("tcm_syndrome", "证候", "Syndrome record", "tcm_knowledge", """
        A syndrome (证): manifestations, tongue, pulse, treatment principle (治法) and the
        formulas recorded for it, from the seed corpus (8 syndromes), with the classical
        passages that record them. A recorded indication is an attribution, not evidence of
        efficacy.""",
          obj({"name": S("syndrome name, e.g. 脾胃气虚证")}, required=("name",)),
          "native.tcm_syndrome", B_R),
    _core("tcm_classical_search", "经典条文检索", "Classical passages", "tcm_knowledge", """
        Find classical passages (伤寒论, 神农本草经, 太平惠民和剂局方 …) by a phrase or by
        the entities they mention; returns passage ids to cite. Seed corpus of 9 passages. A
        passage licenses an attribution ('the text records …'), never an efficacy claim.""",
          obj({"query": S("phrase or entity name, e.g. 桂枝汤主之"),
               "limit": I(minimum=1, maximum=50, default=5)}, required=("query",)),
          "native.tcm_classical_search", B_R),
    _core("tcm_compatibility", "配伍禁忌核查", "Compatibility check", "tcm_safety", """
        Check two or more herbs (or a formula's ingredients) for recorded incompatibilities —
        十八反, 十九畏 and other records — and report names it could not resolve.
        'compatible: true' only means no record was found in the seed corpus; it is never a
        statement that the combination is safe.""",
          obj({"herbs": arr(S(), "herb names, e.g. [甘草, 甘遂]", minItems=2)},
              required=("herbs",)), "native.tcm_compatibility", B_R),
    _core("tcm_applicability", "主张适用性", "Claim licensing", "tcm_knowledge", """
        Ask which kind of claim the recorded evidence licenses that SUBJECT treats or is
        indicated for OBJECT, for a population and condition: a classical text licenses
        attribution, never efficacy; efficacy needs randomized trials or a systematic review;
        evidence from one population is an extrapolation to another. Call it before stating
        anything stronger than an attribution, and report the verdict and the tier
        required.""",
          obj({"subject": S("herb or formula, e.g. 桂枝汤"),
               "object": S("syndrome or condition, e.g. 太阳中风证"),
               "claim_kind": S(enum=list(CLAIM_KINDS), default="efficacy"),
               "population": S(default=""), "condition": S(default="")},
              required=("subject", "object")), "native.tcm_applicability", B_R),
    _core("tcm_normalize", "名称规范化（受治理）", "Normalize names (governed)", "tcm_knowledge", """
        Governed run of the normalize-tcm-entities skill: resolve a list of names to
        seed-corpus entities, keeping ambiguity (candidates) instead of guessing; an
        unresolved name is reported, not dropped. Returns an attested artifact with the
        kernel's release verdict and the claims it allowed.""",
          obj({"names": arr(S(), "names to resolve, e.g. [黄芪, 桂枝汤, 姜]", minItems=1)},
              required=("names",)), "skill.normalize-tcm-entities", B_R),
    _core("tcm_evidence", "证据检索（受治理）", "Retrieve evidence (governed)", "tcm_knowledge", """
        Governed run of retrieve-tcm-evidence: the study records and classical passages
        recorded about a subject, grouped by evidence kind, with design labels, retraction
        status, quality dimensions (never a single score) and the kernel's verdict on each
        claim. claim_kind says which kind of claim you are asking about; the skill releases
        at most attribution and traditional_use claims. Seed corpus only: an absence here is
        not evidence of absence.""",
          obj({"subject": S("herb or formula, e.g. 黄芪"),
               "claim_kind": S(enum=list(CLAIM_KINDS), default="efficacy"),
               "max_results": I(minimum=1, maximum=100, default=20)},
              required=("subject",)), "skill.retrieve-tcm-evidence", B_R),
    _core("tcm_safety_report", "安全性与配伍禁忌", "Safety & contraindications", "tcm_safety", """
        Governed run of assess-tcm-safety: the recorded toxicity, contraindications,
        interactions and incompatibilities (十八反) of a herb, optionally with co-administered
        herbs or drugs and a population (e.g. pregnancy). Reports 'unknown', never 'safe',
        when nothing is recorded. The records are corpus entries, not clinical safety data:
        absence of a record is not evidence of safety.""",
          obj({"subject": S("herb, e.g. 甘草"),
               "co_administered": arr(S(), "other herbs or drugs taken with it", default=[]),
               "population": S("e.g. pregnancy, children", default="")},
              required=("subject",)), "skill.assess-tcm-safety", B_R),
    _core("tcm_network_hypothesis", "网络药理假说（受治理）", "Network hypothesis (governed)",
          "netpharm", """
        Governed run of analyze-tcm-network-pharmacology on the seed corpus: a formula's
        composition and recorded indications with predicted and measured edges kept apart.
        On the seed corpus it returns no targets, and its claims are capped at
        mechanism_hypothesis: predicted ≠ measured. For measured-target network
        pharmacology on data snapshots, use network_pharmacology_run (a runner job).""",
          obj({"formula_name": S("formula, e.g. 桂枝汤")}, required=("formula_name",)),
          "skill.analyze-tcm-network-pharmacology", B_R),
    _core("clinic_assess", "辨证与处方草案", "Differentiation & draft", "clinic", """
        From a 四诊 intake (bioagent.clinic.intake/1): red-flag screen → syndrome
        differentiation (with the questions to ask next) → a draft prescription for a
        licensed TCM practitioner to review. Status is draft, blocked, needs_information,
        no_formula or refer; an uncleared red flag means refer, with no draft. The output is
        a draft, never a prescription, and only the practitioner can sign it — no tool can.
        The intake is patient data. The tool runs locally (browser or runner), but what you
        write and read in this conversation goes to the model service the user selected; use
        a pseudonymous id, never a name. The knowledge pack is a draft not yet reviewed by a
        licensed practitioner.""",
          obj({"intake": INTAKE_SCHEMA,
               "modifications": arr(MODIFICATION_SCHEMA, "changes to the base formula",
                                    default=[]),
               "apply_textbook": B("apply the 《方剂学》 加减 whose findings are present",
                                   default=False),
               "days": I(minimum=1, maximum=60, default=7)}, required=("intake",)),
          "clinic.assess", B_R),
    _core("clinic_check_prescription", "处方核查", "Check a prescription", "clinic", """
        Check a practitioner-written prescription (herb + grams) against an intake with the
        knowledge pack's rules: 十八反 / 十九畏, Pharmacopoeia dose ranges, toxicity, pregnancy
        and breastfeeding, allergies, conditions, drug interactions and age. Each issue has a
        severity: stop, block, warn or info. Finding no issue is not a safety guarantee; the
        pack is a draft.""",
          obj({"intake": INTAKE_SCHEMA, "herbs": arr(HERB_LINE_SCHEMA, minItems=1)},
              required=("intake", "herbs")), "clinic.check", B_R),
    _core("clinic_followup", "复诊评估", "Follow-up", "clinic", """
        Compare a follow-up visit with baseline: weighted symptom scores (无/轻/中/重), the
        reduction and its category (临床痊愈/显效/有效/无效/加重), adverse events and
        suggested next actions for the practitioner. It describes change in the recorded
        scores; it does not show that a treatment caused the change.""",
          obj({"syndrome": S("the syndrome treated, e.g. 脾胃气虚证"),
               "baseline": VISIT_SCHEMA, "current": VISIT_SCHEMA},
              required=("baseline", "current")), "clinic.followup", B_R),
    _core("tcmdb_catalog", "中医药数据源目录", "TCM data sources", "tcm_data", """
        List the TCM data sources (136 source cards): how each is reached (live_api,
        snapshot, manual_import, restricted, unreachable), its licence, whether commercial use
        is allowed, barriers and assessment. A card with a dataset also reports whether a
        store is built or imported in the current runtime. Licence 'not stated' means unknown,
        not permitted.""",
          obj({"query": S("words in the name, licence or assessment"),
               "module": S("architecture module M1..M14"),
               "access": S(enum=_ACCESS)}), "tcmdb.catalog", B_R),
    _core("tcmdb_relations", "数据枢纽关系查询", "Hub relations", "tcm_data", """
        Query relations across every database store in this runtime (herb_ingredient,
        ingredient_target, formula_herb, herb_drug_interaction, target_disease,
        subject_clinical_trial …). Each row keeps its source, evidence kind (known, predicted,
        aggregated, listed, reported, mentioned, signal, associated), reference and licence;
        limit applies per source. Predicted, aggregated and signal rows support hypotheses
        only, and a missing row is not evidence of absence. Needs dataset stores built on
        the runner or imported into this browser.""",
          obj({"kind": S("relation kind, e.g. herb_drug_interaction"),
               "subject": S("id or exact name (e.g. 黄芪, pubchem:5280343)"),
               "object": S("id or exact name"),
               "contains": B("substring match on names instead of exact", default=False),
               "evidence": arr(S(enum=_RELATION_EVIDENCE)),
               "outcomes": arr(S(enum=["positive", "negative", "inconclusive"])),
               "sources": arr(S(), "dataset keys"),
               "commercial": B("only rows whose licence allows commercial reuse", default=False),
               "limit": I("rows per source", minimum=1, maximum=500, default=50)}),
          "tcmdb.relations", B_R),
    _core("tcmdb_consensus", "多源一致性", "Cross-source consensus", "tcm_data", """
        Reconcile one relation kind for a subject (or object) across the built sources: ids
        unified, copies counted once (independent lineages), evidence kinds kept apart,
        contradictions and silent sources reported, with a support level per item
        (independently_replicated … predicted). Support counts sources, not truth. Needs
        dataset stores built on the runner or imported into this browser.""",
          obj({"kind": S("relation kind, e.g. herb_ingredient"), "subject": S(), "object": S(),
               "min_support": S(enum=_SUPPORT),
               "merge_processed": B("count 炙黄芪 with 黄芪", default=False),
               "limit": I("items returned", minimum=1, maximum=500, default=50)},
              required=("kind",)), "tcmdb.consensus", B_R),
    _core("connector_call", "在线数据源调用", "Call a live connector", "live_sources", """
        Call one operation of a public biomedical API (117 sources, 444 operations: UniProt,
        Ensembl, ChEMBL, PubChem, Open Targets, STRING, ClinicalTrials.gov, openFDA herbal
        events, DCABM-TCM, TCMBank, ITCM, SymMap, HERB, COCONUT …) in this runtime,
        under the project's network permission profile and each host's rate limit. Find
        connector and operation names with catalog_search(kind='connector'). Live
        third-party data, unreviewed; the source's licence applies.""",
          obj({"connector": S("connector key, e.g. uniprot"),
               "operation": S("operation name, e.g. entry"),
               "arguments": obj({}, description="the operation's arguments", additional=True)},
              required=("connector", "operation")), "connector.*", B_R, network=True),
    _core("literature_search", "文献检索", "Literature search", "literature", """
        Search the literature: Europe PMC (default), PubMed through NCBI E-utilities, or
        Crossref. Returns titles with PMID / DOI to cite. Runs in this runtime with web
        access on. A hit is a pointer to read, not evidence: no claim is licensed until the
        study itself has been assessed.""",
          obj({"query": S("search terms"),
               "source": S(enum=["europepmc", "pubmed", "crossref"], default="europepmc"),
               "limit": I(minimum=1, maximum=50, default=10)}, required=("query",)),
          "connector.europepmc.search", B_R, network=True,
          hosts=["www.ebi.ac.uk", "eutils.ncbi.nlm.nih.gov", "api.crossref.org"]),
    _core("network_pharmacology_run", "网络药理研究闭环（任务）", "Network pharmacology run (job)",
          "netpharm", """
        Start the closed research loop as a job on the local runner: frozen protocol →
        verified data snapshots (NPASS / CMAUP / LOTUS, STRING, Reactome, Open Targets) →
        measured-target pathway enrichment with a degree-matched permutation null →
        pre-registered rebuttals → attested release. Needs the snapshots built on the
        runner; the user approves each job. Returns a job id: follow it with job_status.
        Claims are capped at mechanism_hypothesis, and a refuted hypothesis is a result.""",
          obj({"formula": S("formula name, e.g. 葛根芩连汤"),
               "disease": S("disease ontology id, e.g. MONDO_0005148"),
               "parameters": obj({
                   "background": S(enum=["assayed", "reactome"], default="assayed"),
                   "hits": S(enum=["potency", "screening"], default="potency"),
                   "permutations": I(minimum=100, maximum=100000, default=1000),
                   "activity": arr(S(), "activity sources, e.g. [npass]"),
                   "optional": arr(S(), "optional sources, e.g. [string]")})},
              required=("formula",)), "job.research.run", R, confirm=True, job=True),
    _core("run_pipeline", "计算流程（任务）", "Run a pipeline (job)", "omics", """
        Start a heavy pipeline as a job on the user's machine (local runner, CPU or GPU per
        the device setting): rnaseq (FASTQ → differential expression), scrna (count matrices
        → annotated clusters), fold (protein structure), dock (AutoDock Vina), admet
        (descriptors, alerts, endpoints). arguments follow the job kind's schema
        (catalog_search kind='job'); files are upload ids. The user approves each job, and
        allow_remote (sending a sequence to a third-party service) separately. Returns a job
        id; a pipeline that has not succeeded has no result. A missing engine is refused,
        never approximated.""",
          obj({"pipeline": S(enum=list(PIPELINES)),
               "arguments": obj({}, description="the job kind's parameters", additional=True)},
              required=("pipeline", "arguments")), "job.pipeline.*", R, confirm=True, job=True),
    _core("job_status", "任务状态", "Job status", "system", """
        Status of a runner job: state (queued, running, succeeded, failed, cancelled),
        progress, and once it has succeeded its outcome and output files. wait_s (at most 60)
        waits for a change. Until the state is succeeded the job is pending work: never
        present partial output as a result.""",
          obj({"job_id": S(), "wait_s": I(minimum=0, maximum=60, default=0)},
              required=("job_id",)), "system.job_status", R),
    _core("capabilities_status", "运行能力", "Capabilities", "system", """
        What can run now and where: the browser runtime (Python on the CPU, single thread,
        WebAssembly), the local runner (devices, network profile, purpose), optional
        dependencies present, and how many catalog entries are runnable here. Use it to
        explain why something is unavailable and what would make it available.""",
          obj({}), "system.capabilities", B_R),
    _core("catalog_search", "能力检索", "Search capabilities", "system", """
        Search every TCMScience capability beyond these core tools (about 640: 150 native
        tools — clinical calculators, statistics, sequences, phylogenetics …; 444 API
        operations; governed skills; TCM data hub, clinic, study-design and job operations)
        with Chinese or English keywords (十八反, eGFR, 生存分析). Returns ids with their full
        parameter schemas; run one with call_tool.""",
          obj({"query": S("keywords, Chinese or English"),
               "category": S(enum=list(CATEGORY_IDS)),
               "kind": S(enum=list(ENTRY_KINDS)),
               "runnable_now": B("only entries that can run here now", default=False),
               "limit": I(minimum=1, maximum=25, default=10)}), "system.catalog_search", B_R),
    _core("call_tool", "调用能力", "Call a capability", "system", """
        Run any catalog entry by its id (e.g. native.egfr_ckd_epi_2021,
        connector.uniprot.entry, study.combination) with arguments that match its parameters
        schema from catalog_search. The entry's own rules apply: network entries need web
        access, and jobs and confirm entries need the user's approval. Never invent an id:
        search first.""",
          obj({"tool": S("catalog entry id"),
               "arguments": obj({}, description="the entry's arguments", additional=True)},
              required=("tool",)), None, B_R),
)

CORE_BY_NAME: dict[str, dict[str, Any]] = {t["name"]: t for t in CORE_TOOLS}

_NATIVE_CORE = ("tcm_lookup", "tcm_herb", "tcm_formula", "tcm_syndrome", "tcm_classical_search",
                "tcm_compatibility", "tcm_applicability")
_SIMPLE = {"tcm_normalize": "skill.normalize-tcm-entities",
           "tcm_evidence": "skill.retrieve-tcm-evidence",
           "tcm_safety_report": "skill.assess-tcm-safety",
           "tcm_network_hypothesis": "skill.analyze-tcm-network-pharmacology",
           "clinic_assess": "clinic.assess", "clinic_check_prescription": "clinic.check",
           "clinic_followup": "clinic.followup", "tcmdb_catalog": "tcmdb.catalog",
           "tcmdb_relations": "tcmdb.relations", "tcmdb_consensus": "tcmdb.consensus",
           "job_status": "system.job_status", "capabilities_status": "system.capabilities",
           "catalog_search": "system.catalog_search"}


def core_tools(where: str = "runner") -> list[dict[str, Any]]:
    """The core tool definitions (copies). ``where`` does not filter them: the page offers
    every core tool and the router says where each can run."""
    del where
    return copy.deepcopy(list(CORE_TOOLS))


def route(name: str, arguments: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The catalog entry a core call executes, with the entry's arguments.

    ``literature_search`` and ``call_tool`` are resolved by the dispatcher itself (the
    first may make two connector calls; the second names its entry)."""
    args = {k: v for k, v in dict(arguments).items() if v is not None}
    if name in _NATIVE_CORE:
        return f"native.{name}", args
    if name in _SIMPLE:
        return _SIMPLE[name], args
    if name == "connector_call":
        return (f"connector.{args.get('connector', '')}.{args.get('operation', '')}",
                dict(args.get("arguments") or {}))
    if name == "network_pharmacology_run":
        formula = str(args.get("formula", "")).strip()
        params = dict(args.get("parameters") or {})
        job = {"question": f"{formula}的实测靶点集中在哪些通路？", "formula": formula}
        if args.get("disease"):
            job["disease"] = args["disease"]
        for key in ("background", "hits", "permutations", "activity", "optional"):
            if key in params:
                job[key] = params[key]
        return "job.research.run", job
    if name == "run_pipeline":
        return f"job.pipeline.{args.get('pipeline', '')}", dict(args.get("arguments") or {})
    raise KeyError(name)
