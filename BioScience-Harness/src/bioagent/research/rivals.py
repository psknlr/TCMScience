"""Rival explanations for the questions TCM research asks most, with sealed predictions.

The pathway inquiry (:mod:`bioagent.research.inquiry`) decides one question about one kind
of result. An agent in conversation meets many: does the formula do more than its main
constituent, is the pair synergistic, does the bench activity happen at the exposure
reached in the body, does the formula work, is the adverse event the herb's, does the herb
change the drug, does the compound bind the target. They have the same shape, and the same
failure when a model answers them alone: the first explanation that fits is the one
reported, and the analysis that would have told it from its rivals is never run.

A **scaffold** writes such a question down the way PSH's inquiry engine
(``psh.scientist.inquiry``) needs it, before any result has been seen:

* the **rivals**: the explanations a careful reviewer would raise, with their claim kinds
  (an artefact, a finding about the method, has none) and their priors, leaving the
  catch-all its share;
* the **tests**: analyses that tell the rivals apart, each with its design, its discrete
  outcomes, the TCMScience tools that produce its result, and the rule that classifies the
  result into one outcome;
* the **predictions**: what each rival says each test will show, sealed when the inquiry
  opens, so that belief afterwards moves only by them.

The numbers are the scaffold's commitments, not estimates fitted to anything. They were
chosen so that every rival can win: for every pair of rivals some test is severe (severity
and power at least 0.8) and replicable, so a world in which one rival is true can lead the
engine to accept it. ``tests/test_rivals.py`` checks that for every rival of every scaffold,
by planting each rival's most likely outcomes. Where an outcome says nothing about which
rival is true (a contrast too imprecise to decide), every rival gives it the same
probability, so it moves no one.

What decides an outcome (an equivalence margin, the smallest important benefit, a
combination's reference model, a clinically relevant change) is a parameter of the
scaffold. It is written into the tests' titles, so it is sealed into the inquiry's trail
with the predictions and cannot be chosen after the result is seen.

Tests with a ``classifier`` are classified by the runtime: the arguments a tool ran with and
its result go to :func:`classify`, which checks them against the sealed criteria and applies
the rule, or refuses (:class:`Unclassifiable`) and records nothing. The other tests are
classified by whoever ran them, against the rule the scaffold states, citing the evidence;
an inquiry's record says which outcomes were which.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = ["CLASSIFIERS", "CLASSIFIER_DATA", "CLASSIFIER_INPUTS", "CLASSIFIER_TOOLS",
           "GAP_NOTE", "Param", "Rival", "SCAFFOLDS", "Scaffold", "Test", "Unclassifiable",
           "classify", "describe", "inputs", "matches", "open_scaffold", "parameters",
           "rendered", "scaffold"]


class Unclassifiable(ValueError):
    """A result that cannot be classified into one of a test's outcomes, or that does not
    meet the conditions the test was sealed with. Nothing is recorded: correct the data,
    or withdraw the test with this reason when the data do not exist."""


@dataclass(frozen=True)
class Param:
    """One value a scaffold is opened with. ``kind`` is ``text``, ``number`` (a positive,
    finite number) or ``choice`` (one of ``choices``). Without a ``default`` it is
    required; ``unset`` is how an optional value that was not given reads in the text."""

    name: str
    description: str
    kind: str = "text"
    choices: tuple[str, ...] = ()
    default: Any = None
    unset: str = ""

    @property
    def required(self) -> bool:
        return self.default is None and not self.unset


@dataclass(frozen=True)
class Rival:
    """One competing explanation, with the scaffold's placeholders in its proposition.
    ``claim_kind`` is a ``ClaimKind`` value, or empty for an artefact."""

    id: str
    proposition: str
    claim_kind: str
    prior: float


@dataclass(frozen=True)
class Test:
    """One analysis that tells rivals apart.

    ``design`` is a ``StudyDesign`` value, or empty for the design the inquiry is opened
    with (the scaffold's ``model``). ``predictions`` maps each rival to its distribution
    over ``outcomes``. ``run_with`` names the tools that produce the result and
    ``classify`` the rule that turns it into one outcome; with a ``classifier`` the runtime
    applies the rule (``CLASSIFIERS``). ``subject`` names the parameter that says what the
    test is about, when a classifier must pick it out of a larger result.
    """

    id: str
    title: str
    outcomes: tuple[str, ...]
    predictions: Mapping[str, Mapping[str, float]]
    run_with: str
    classify: str
    design: str = ""
    cost: float = 1.0
    replicates: str = ""
    classifier: str = ""
    subject: str = ""


@dataclass(frozen=True)
class Scaffold:
    """A research question with its rivals, tests and sealed predictions."""

    id: str
    title: Mapping[str, str]
    question: str
    params: tuple[Param, ...]
    rivals: tuple[Rival, ...]
    tests: tuple[Test, ...]
    notes: tuple[str, ...] = ()
    #: StoppingRule fields that differ from the engine's defaults
    rule: Mapping[str, Any] = field(default_factory=dict)

    def param(self, name: str) -> Param:
        return next(p for p in self.params if p.name == name)


# ------------------------------------------------------------------------- helpers

def _same(rows: Mapping[str, float], *ids: str) -> dict[str, Mapping[str, float]]:
    return {i: rows for i in ids}


_MODEL_PRECLINICAL = Param("model", "where the experiments are done", "choice",
                           ("animal", "in_vitro"), default="animal")
_BETTER = Param("better", "which direction of the endpoint is the effect", "choice",
                ("higher", "lower"), default="higher")

#: The research gap: how a test nobody has run is handled. Repeated in every scaffold's
#: description for the model.
GAP_NOTE = ("A test nobody has run is a proposal, not a failure: withdraw it with that "
            "reason, and the conclusion names it as the study that would decide the "
            "question.")


# ----------------------------------------------------------------- the scaffolds

def _pathway_signal() -> Scaffold:
    from .inquiry import ANALYSES, predictions
    runs = {"enrichment": 1.0, "random": 21.0, "rewired": 11.0}
    enrich = ("enriched: the pathway is released by the run (BH q and permutation p below "
              "its thresholds); not_enriched otherwise")
    how = {
        "annotated": ("network_pharmacology_run with parameters.background 'reactome'",
                      enrich),
        "assayed": ("network_pharmacology_run with parameters.background 'assayed' (the "
                    "default)", enrich),
        "assayed-seed": ("the seed_stability rebuttal of the assayed run (its 'seed+N' "
                         "test), or the run repeated", enrich.replace(
                             "released by the run", "still released under the other seed")),
        "random-herbs": ("the random-formula control of bioagent.studies.validation, on "
                         "the runner: BioScience-Harness/scripts/run_inquiry.py runs it "
                         "(not a Studio tool)",
                         "specific: at most 5% of random herb combinations of the "
                         "formula's size reach the pathway; not_specific: more do; "
                         "not_enriched: the formula's own enrichment did not hold"),
        "rewired": ("the degree-preserving rewiring control of "
                    "bioagent.studies.validation, on the runner (scripts/run_inquiry.py)",
                    "beyond_wiring: at most 5% of rewired networks keep the pathway "
                    "enriched; explained_by_degree: more do; not_enriched: the formula's "
                    "own enrichment did not hold"),
    }
    how["random-herbs-2"] = how["random-herbs"]
    how["rewired-2"] = how["rewired"]
    tests = tuple(
        Test(a, title, outcomes, predictions(a), how[a][0], how[a][1], design="in_silico",
             cost=runs["enrichment" if a == "annotated" else kind], replicates=replicates)
        for a, (title, outcomes, replicates, kind) in ANALYSES.items())
    return Scaffold(
        "pathway_signal", {"zh": "通路富集信号：机制还是假象？",
                           "en": "Pathway signal: mechanism or artefact?"},
        "Do {formula}'s measured targets concentrate in {pathway}, and why?",
        (Param("formula", "the formula, e.g. 葛根芩连汤"),
         Param("pathway", "the pathway, e.g. a Reactome id")),
        (Rival("target", "{formula}'s constituents act selectively on {pathway}: its "
               "measured targets concentrate there beyond what was assayed and what random "
               "herb combinations reach", "mechanism_hypothesis", 0.3),
         Rival("coverage", "the concentration in {pathway} reflects which proteins the "
               "constituents were assayed against, not their activity", "", 0.3),
         Rival("promiscuity", "{pathway}'s proteins are hit by many compounds, so any herb "
               "combination of this size reaches it", "", 0.3)),
        tests,
        ("The same rivals and predictions as bioagent.research.inquiry; "
         "scripts/run_inquiry.py runs the whole inquiry on the runner, recorded in PSH's "
         "world model. In Studio the random-herb and rewiring controls are not tools: "
         "withdraw them with that reason, and the conclusion says the test against "
         "promiscuity is missing.",
         "In-silico evidence licenses at most a tentative mechanism hypothesis, however "
         "high the posterior."),
        rule={"min_gain": 0.001})


def _formula_vs_constituent() -> Scaffold:
    contrast = {
        "formula_adds": {"formula_exceeds": 0.82, "equivalent": 0.05,
                         "monomer_exceeds": 0.01, "inconclusive": 0.12},
        "constituent_accounts": {"formula_exceeds": 0.04, "equivalent": 0.80,
                                 "monomer_exceeds": 0.04, "inconclusive": 0.12},
        "formula_attenuates": {"formula_exceeds": 0.01, "equivalent": 0.05,
                               "monomer_exceeds": 0.82, "inconclusive": 0.12}}
    second_dose = {
        "formula_adds": {"formula_exceeds": 0.70, "equivalent": 0.12,
                         "monomer_exceeds": 0.02, "inconclusive": 0.16},
        "constituent_accounts": {"formula_exceeds": 0.07, "equivalent": 0.70,
                                 "monomer_exceeds": 0.07, "inconclusive": 0.16},
        "formula_attenuates": {"formula_exceeds": 0.02, "equivalent": 0.12,
                               "monomer_exceeds": 0.70, "inconclusive": 0.16}}
    outcomes = ("formula_exceeds", "equivalent", "monomer_exceeds", "inconclusive")
    run = ("study.contrast_formula_monomer: pass its arguments as data to research_inquiry "
           "record, which runs it")
    rule = ("the contrast's verdict (formula_exceeds, equivalent, monomer_exceeds or "
            "inconclusive), with both groups from one study, {constituent} at its amount in "
            "the formula's dose (dose_matched: true), margin {margin}, {better} is better, "
            "alpha 0.05")
    return Scaffold(
        "formula_vs_constituent", {"zh": "复方与主要成分：整方是否多做了什么？",
                                   "en": "Formula vs its main constituent"},
        "Does {formula} do more on {endpoint} than {constituent}, its main constituent, at "
        "the dose of {constituent} it contains?",
        (Param("formula", "the formula, e.g. 葛根芩连汤"),
         Param("constituent", "its main constituent, e.g. berberine"),
         Param("endpoint", "one endpoint, e.g. fasting glucose"),
         Param("margin", "the equivalence margin in the endpoint's units, fixed now",
               "number"),
         _BETTER, _MODEL_PRECLINICAL),
        (Rival("formula_adds", "{formula}'s other constituents add to {constituent}'s effect "
               "on {endpoint}: at the dose of {constituent} it contains, the whole formula's "
               "effect exceeds {constituent}'s alone", "mechanism", 0.35),
         Rival("constituent_accounts", "{constituent} accounts for {formula}'s effect on "
               "{endpoint}: at the dose the formula contains, the two are equivalent within "
               "±{margin}", "mechanism", 0.35),
         Rival("formula_attenuates", "{formula}'s other constituents reduce {constituent}'s "
               "effect on {endpoint} (for example by lowering its absorption): "
               "{constituent} alone exceeds the whole formula", "mechanism", 0.2)),
        (Test("contrast", "{formula} against {constituent} at the dose it contains, on "
              "{endpoint} ({better} is better), in one study, equivalence margin ±{margin}",
              outcomes, contrast, run, rule, classifier="contrast"),
         Test("contrast-rep", "the same contrast in an independent experiment (another "
              "batch, cohort or laboratory)", outcomes, contrast, run, rule,
              replicates="contrast", classifier="contrast"),
         Test("contrast-dose2", "the same contrast at a second dose level, both arms "
              "dose-matched", outcomes, second_dose, run, rule, classifier="contrast")),
        ("A comparison in patients is an efficacy question: use the efficacy scaffold or a "
         "custom inquiry.",
         "Exceeding the constituent is not synergy among constituents (that needs a "
         "combination design), and an animal or cell result says nothing about efficacy "
         "in people.", GAP_NOTE))


def _combination() -> Scaffold:
    matrix = {
        "synergy": {"selective_excess": 0.82, "cytotoxic_excess": 0.08, "no_excess": 0.10},
        "independent": {"selective_excess": 0.05, "cytotoxic_excess": 0.05,
                        "no_excess": 0.90},
        "cytotoxicity": {"selective_excess": 0.05, "cytotoxic_excess": 0.85,
                         "no_excess": 0.10}}
    second_system = {
        "synergy": {"selective_excess": 0.65, "cytotoxic_excess": 0.10, "no_excess": 0.25},
        "independent": {"selective_excess": 0.05, "cytotoxic_excess": 0.07,
                        "no_excess": 0.88},
        "cytotoxicity": {"selective_excess": 0.05, "cytotoxic_excess": 0.80,
                         "no_excess": 0.15}}
    outcomes = ("selective_excess", "cytotoxic_excess", "no_excess")
    run = "study.combination: pass its arguments as data to research_inquiry record"
    rule = ("selective_excess: a combination's 95% interval lies above the {reference} "
            "reference and its cytotoxicity is below 0.5; cytotoxic_excess: every "
            "combination above the reference is at or above 0.5 cytotoxicity; no_excess: "
            "none is above it. Every combination needs a cytotoxicity readout of the same "
            "wells")
    return Scaffold(
        "combination", {"zh": "联合作用：是否超出单药预测？",
                        "en": "Combination: more than each alone predicts?"},
        "Do {agent_a} and {agent_b} together act on {endpoint} beyond what each alone "
        "predicts ({reference} reference)?",
        (Param("agent_a", "the first herb or constituent, e.g. 黄连 or berberine"),
         Param("agent_b", "the second, e.g. 吴茱萸 or evodiamine"),
         Param("endpoint", "the effect measured, e.g. inhibition of HepG2 growth"),
         Param("reference", "the reference model, fixed now", "choice",
               ("bliss", "hsa", "loewe")),
         Param("model", "where the experiments are done", "choice", ("in_vitro", "animal"),
               default="in_vitro")),
        (Rival("synergy", "{agent_a} and {agent_b} together act on {endpoint} beyond the "
               "{reference} reference, selectively (not through general cytotoxicity)",
               "mechanism", 0.3),
         Rival("independent", "{agent_a} and {agent_b} act on {endpoint} as the {reference} "
               "reference predicts from each alone: no excess", "mechanism", 0.4),
         Rival("cytotoxicity", "an apparent excess on {endpoint} comes from general "
               "cytotoxicity at the doses combined, not from a selective interaction", "",
               0.2)),
        (Test("matrix", "a dose matrix of {agent_a} and {agent_b}, each alone at the doses "
              "combined, with a cytotoxicity readout of the same wells, against the "
              "{reference} reference (95% intervals; cytotoxicity of 0.5 or more counts as "
              "general)", outcomes, matrix, run, rule, classifier="combination"),
         Test("matrix-rep", "the same matrix in an independent experiment (another plate "
              "and day)", outcomes, matrix, run, rule, replicates="matrix",
              classifier="combination"),
         Test("matrix-system2", "the same matrix in a second cell system relevant to "
              "{endpoint}", outcomes, second_system, run, rule, classifier="combination")),
        ("An excess over a reference model is not a mechanism of synergy, not "
         "pharmacokinetic enhancement and not clinical synergy.", GAP_NOTE))


def _exposure() -> Scaffold:
    parent = {
        "parent_reaches": {"plausible": 0.82, "implausible": 0.04,
                           "depends_on_unknowns": 0.14},
        "not_reached": {"plausible": 0.03, "implausible": 0.80,
                        "depends_on_unknowns": 0.17},
        "metabolite_carries": {"plausible": 0.05, "implausible": 0.75,
                               "depends_on_unknowns": 0.20}}
    metabolite = {
        "parent_reaches": {"plausible": 0.30, "implausible": 0.40,
                           "depends_on_unknowns": 0.30},
        "not_reached": {"plausible": 0.04, "implausible": 0.82,
                        "depends_on_unknowns": 0.14},
        "metabolite_carries": {"plausible": 0.80, "implausible": 0.05,
                               "depends_on_unknowns": 0.15}}
    outcomes = ("plausible", "implausible", "depends_on_unknowns")
    run = ("study.exposure_screen at site {site}: pass its arguments as data to "
           "research_inquiry record; exposures from literature_search or "
           "tcmdb_relations, potencies from connector_call (chembl activities)")
    rule = ("over the screened pairs of this molecule on {target}: plausible when every "
            "determinate pair is plausible (free concentration at or above the potency "
            "over the whole range), implausible when every one is implausible, "
            "depends_on_unknowns otherwise. Undeterminable pairs (no exposure at {site}, a "
            "parent judged against a metabolite, a unit that does not convert) and an "
            "assay with no activity do not count; with none left there is no outcome")
    return Scaffold(
        "exposure", {"zh": "体外活性能否在体内暴露下发生？",
                     "en": "Bench activity at the exposure reached?"},
        "Does {molecule}'s activity on {target}, measured in vitro, happen at the exposure "
        "reached at {site}?",
        (Param("molecule", "the molecule tested in vitro, e.g. baicalin"),
         Param("target", "the target or assay, e.g. PTP1B"),
         Param("site", "the site of action: plasma | tissue:<uberon id> | gut_lumen"),
         Param("metabolite", "its main circulating metabolite, when known (e.g. baicalein)",
               unset="its main metabolite")),
        (Rival("parent_reaches", "{molecule} reaches {site} at free concentrations at or "
               "above those at which it acted on {target} in vitro", "mechanism_hypothesis",
               0.35),
         Rival("not_reached", "neither {molecule} nor {metabolite} reaches {site} at free "
               "concentrations at which it acts on {target}: the activity seen in vitro "
               "does not happen at that exposure", "mechanism_hypothesis", 0.35),
         Rival("metabolite_carries", "{molecule} itself stays below its active "
               "concentration at {site}, but {metabolite} reaches it at concentrations at "
               "which it acts on {target}", "mechanism_hypothesis", 0.2)),
        (Test("parent", "free {molecule} at {site} against its potency on {target} "
              "(exposure screen; an unknown free fraction is a range, never 1)", outcomes,
              parent, run, rule, design="in_vitro", classifier="exposure",
              subject="molecule"),
         Test("parent-rep", "the same ratio from an independent exposure measurement "
              "(another study of the same species)", outcomes, parent, run, rule,
              design="in_vitro", replicates="parent", classifier="exposure",
              subject="molecule"),
         Test("metabolite", "free {metabolite} at {site} against its own potency on "
              "{target}", outcomes, metabolite, run, rule, design="in_vitro",
              classifier="exposure", subject="metabolite"),
         Test("metabolite-rep", "the same ratio for {metabolite} from an independent "
              "exposure measurement", outcomes, metabolite, run, rule, design="in_vitro",
              replicates="metabolite", classifier="exposure", subject="metabolite")),
        ("An exposure ratio screens for mechanisms worth testing; it is not target "
         "occupancy or a probability of efficacy.",
         "Opened without naming the metabolite, its tests cannot be classified: withdraw "
         "them, and the conclusion says the metabolite route was not tested.", GAP_NOTE))


def _efficacy() -> Scaffold:
    pooled = {
        "effective": {"benefit": 0.80, "no_important_benefit": 0.04, "inconclusive": 0.16},
        "ineffective": {"benefit": 0.10, "no_important_benefit": 0.55,
                        "inconclusive": 0.35},
        "bias": {"benefit": 0.85, "no_important_benefit": 0.03, "inconclusive": 0.12}}
    low_risk = {
        "effective": {"benefit": 0.82, "no_important_benefit": 0.03, "inconclusive": 0.15},
        "ineffective": {"benefit": 0.05, "no_important_benefit": 0.60,
                        "inconclusive": 0.35},
        "bias": {"benefit": 0.06, "no_important_benefit": 0.50, "inconclusive": 0.44}}
    outcomes = ("benefit", "no_important_benefit", "inconclusive")
    run = ("native.meta_analysis on each trial's effect estimate and standard error "
           "(scale {scale}): pass them as data to research_inquiry record. Trials from "
           "literature_search (pubmed, clinicaltrials) and connector_call")
    rule = ("from the random-effects 95% CI, {better} is better: no_important_benefit when "
            "the whole interval falls short of the smallest important benefit ({mcid}); "
            "otherwise benefit when it excludes no effect on the side of benefit; "
            "otherwise inconclusive")
    return Scaffold(
        "efficacy", {"zh": "疗效：真实获益还是偏倚？", "en": "Efficacy: benefit or bias?"},
        "Does {intervention} improve {outcome} in {population}, compared with "
        "{comparator}?",
        (Param("intervention", "the formula or herb as given, e.g. 葛根芩连汤 decoction"),
         Param("outcome", "one outcome, e.g. HbA1c at 12 weeks"),
         Param("population", "e.g. adults with type 2 diabetes"),
         Param("comparator", "e.g. placebo, or metformin alone"),
         Param("scale", "the scale of the trials' estimates: log (log OR, RR, HR) or raw "
               "(mean differences)", "choice", ("log", "raw")),
         Param("better", "which direction of the estimate is benefit", "choice",
               ("higher", "lower")),
         Param("mcid", "the smallest important benefit, a positive number on the "
               "estimates' scale (for log ratios |ln ratio|, e.g. 0.105 for 10%); without "
               "it, no result can show that an important benefit is absent", "number",
               unset="not set")),
        (Rival("effective", "{intervention} improves {outcome} in {population} compared "
               "with {comparator}", "efficacy", 0.3),
         Rival("ineffective", "{intervention} does not improve {outcome} in {population} by "
               "a clinically important amount compared with {comparator}", "efficacy", 0.3),
         Rival("bias", "the benefit reported for {intervention} on {outcome} comes from bias "
               "in how the trials were run or reported (unconcealed allocation, unblinded "
               "assessment, selective publication), not from {intervention}", "", 0.3)),
        (Test("all_trials", "random-effects meta-analysis of every randomised trial of "
              "{intervention} against {comparator} on {outcome} in {population} ({scale} "
              "scale, {better} is better, smallest important benefit {mcid})", outcomes,
              pooled, run, rule, design="systematic_review", classifier="meta"),
         Test("all_trials-rep", "the same meta-analysis of trials found by an independent "
              "search of another database family (Chinese-language: CNKI, Wanfang, VIP; or "
              "English-language: PubMed, Embase, CENTRAL)", outcomes, pooled, run, rule,
              design="systematic_review", replicates="all_trials", classifier="meta"),
         Test("low_risk", "the same meta-analysis restricted to trials at low risk of bias "
              "(allocation concealed and outcome assessment blinded)", outcomes, low_risk,
              run, rule, design="systematic_review", classifier="meta"),
         Test("low_risk-rep", "the low-risk result in trials not in that analysis "
              "(prospectively registered trials at low risk of bias)", outcomes, low_risk,
              run, rule, design="systematic_review", replicates="low_risk",
              classifier="meta")),
        ("Efficacy is licensed only by randomised trials and their systematic reviews: a "
         "classical text, an animal study or a docking score cannot move it.",
         "An accepted verdict reads these trials; it is not a recommendation, which needs "
         "a guideline.", GAP_NOTE))


def _safety_signal() -> Scaffold:
    dechallenge = {
        "causal": {"positive": 0.82, "negative": 0.04, "mixed": 0.14},
        "confounded": {"positive": 0.10, "negative": 0.55, "mixed": 0.35},
        "wrong_material": {"positive": 0.80, "negative": 0.04, "mixed": 0.16}}
    authenticated = {
        "causal": {"authentic_material": 0.82, "adulterated_material": 0.04, "mixed": 0.14},
        "confounded": {"authentic_material": 0.60, "adulterated_material": 0.10,
                       "mixed": 0.30},
        "wrong_material": {"authentic_material": 0.04, "adulterated_material": 0.82,
                           "mixed": 0.14}}
    controlled = {
        "causal": {"association": 0.70, "no_association": 0.05, "inconclusive": 0.25},
        "confounded": {"association": 0.10, "no_association": 0.65, "inconclusive": 0.25},
        "wrong_material": {"association": 0.40, "no_association": 0.35,
                           "inconclusive": 0.25}}
    reports = ("literature_search (pubmed: case reports and series), connector_call "
               "(openfda: herbal adverse events), tcm_safety_report and tcmdb_relations "
               "(safety records)")
    dc_rule = ("positive: most documented cases resolve when {herb} is stopped (or recur "
               "when restarted) with other causes assessed; negative: most do not; mixed "
               "otherwise")
    au_rule = ("authentic_material: the authenticated cases had authentic {herb}, with no "
               "adulterant or contaminant found; adulterated_material: they had "
               "misidentified, substituted or contaminated material; mixed otherwise")
    return Scaffold(
        "safety_signal", {"zh": "安全信号：是药材本身所致吗？",
                          "en": "Safety signal: is it the herb?"},
        "Is {event}, reported in people taking {herb}, caused by {herb}?",
        (Param("herb", "the herb or product, e.g. 何首乌"),
         Param("event", "the adverse event, e.g. drug-induced liver injury")),
        (Rival("causal", "{herb} causes {event} in some people who take it",
               "safety_signal", 0.3),
         Rival("confounded", "{event} reflects co-medication, the condition being treated "
               "or chance co-occurrence, not {herb}", "", 0.3),
         Rival("wrong_material", "products sold as {herb} that were misidentified, "
               "substituted or contaminated (for example with aristolochic acids, heavy "
               "metals or undeclared drugs) cause {event}; authentic {herb} does not",
               "safety_signal", 0.3)),
        (Test("dechallenge", "cases of {event} with {herb} that document dechallenge or "
              "rechallenge and assess other causes", ("positive", "negative", "mixed"),
              dechallenge, reports, dc_rule, design="case_series"),
         Test("dechallenge-rep", "the same in an independent set of cases (another "
              "pharmacovigilance database, country or period)",
              ("positive", "negative", "mixed"), dechallenge, reports, dc_rule,
              design="case_series", replicates="dechallenge"),
         Test("authenticated", "cases of {event} in which the product taken was "
              "authenticated (botanical or chemical identification, tests for adulterants "
              "and contaminants)", ("authentic_material", "adulterated_material", "mixed"),
              authenticated, reports, au_rule, design="case_series"),
         Test("authenticated-rep", "authenticated cases from an independent source",
              ("authentic_material", "adulterated_material", "mixed"), authenticated,
              reports, au_rule, design="case_series", replicates="authenticated"),
         Test("controlled", "a cohort or case-control study of people who took {herb} and "
              "people who did not, adjusted for co-medication and the condition treated",
              ("association", "no_association", "inconclusive"), controlled,
              "literature_search (cohort and case-control studies)",
              "association: the adjusted estimate's 95% CI excludes no association, "
              "towards harm; no_association: it excludes a clinically relevant harm; "
              "inconclusive otherwise", design="cohort", cost=2.0)),
        ("A safety signal does not wait for acceptance: a provisional or undetermined "
         "verdict is not reassurance, and the absence of a record is not evidence of "
         "safety.",
         "Spontaneous reports cannot give an incidence.", GAP_NOTE))


def _herb_drug_interaction() -> Scaffold:
    pk = {
        "pk": {"changed": 0.82, "unchanged": 0.04, "inconclusive": 0.14},
        "pd": {"changed": 0.05, "unchanged": 0.80, "inconclusive": 0.15},
        "none": {"changed": 0.05, "unchanged": 0.80, "inconclusive": 0.15}}
    pd = {
        "pk": {"changed": 0.75, "unchanged": 0.10, "inconclusive": 0.15},
        "pd": {"changed": 0.82, "unchanged": 0.04, "inconclusive": 0.14},
        "none": {"changed": 0.04, "unchanged": 0.82, "inconclusive": 0.14}}
    in_vitro = {
        "pk": {"inhibits_or_induces": 0.80, "no_effect": 0.20},
        "pd": {"inhibits_or_induces": 0.30, "no_effect": 0.70},
        "none": {"inhibits_or_induces": 0.30, "no_effect": 0.70}}
    changed = ("changed", "unchanged", "inconclusive")
    find = ("literature_search (pubmed, clinicaltrials), tcmdb_relations "
            "(herb_drug_interaction) and tcm_safety_report")
    pk_rule = ("from the geometric mean ratio of {drug}'s AUC and its 90% CI: unchanged "
               "when the interval lies within 0.80–1.25; changed when it excludes 1 and the "
               "ratio lies outside 0.80–1.25; inconclusive otherwise")
    pd_rule = ("from the difference in {marker} and its 95% CI: unchanged when the "
               "interval lies within ±{threshold}; changed when it excludes 0 and the "
               "difference is at least {threshold}; inconclusive otherwise")
    return Scaffold(
        "herb_drug_interaction", {"zh": "药材–药物相互作用", "en": "Herb–drug interaction"},
        "Does {herb} change the exposure to, or the effect of, {drug}?",
        (Param("herb", "the herb or product, e.g. 丹参"),
         Param("drug", "the drug, e.g. warfarin"),
         Param("marker", "the drug's effect marker, e.g. INR"),
         Param("threshold", "the smallest clinically relevant change in the marker, in its "
               "units, fixed now", "number")),
        (Rival("pk", "{herb} changes the exposure to {drug} (by inhibiting or inducing its "
               "metabolism or transport)", "safety_signal", 0.3),
         Rival("pd", "{herb} changes the effect of {drug} on {marker} without changing its "
               "exposure (additive or opposing pharmacodynamics)", "safety_signal", 0.3),
         Rival("none", "taken with {herb}, {drug} keeps its exposure, and its effect on "
               "{marker}, within clinically irrelevant limits", "safety_signal", 0.3)),
        (Test("clinical_pk", "a clinical pharmacokinetic study: the ratio of {drug}'s AUC "
              "with {herb} to without it, with its 90% CI (no-effect limits 0.80–1.25)",
              changed, pk, find + "; the ratio and interval as data to research_inquiry "
              "record", pk_rule, design="non_randomised_trial", classifier="ratio_interval"),
         Test("clinical_pk-rep", "the same in an independent study (other participants, "
              "dose or preparation)", changed, pk, find + "; the ratio and interval as "
              "data", pk_rule, design="non_randomised_trial", replicates="clinical_pk",
              classifier="ratio_interval"),
         Test("clinical_pd", "{drug}'s effect on {marker} with and without {herb}: the "
              "difference and its 95% CI (clinically relevant change {threshold})", changed,
              pd, find + "; the difference and interval as data to research_inquiry "
              "record", pd_rule, design="non_randomised_trial",
              classifier="difference_interval"),
         Test("clinical_pd-rep", "the same in an independent study", changed, pd,
              find + "; the difference and interval as data", pd_rule,
              design="non_randomised_trial", replicates="clinical_pd",
              classifier="difference_interval"),
         Test("in_vitro", "inhibition or induction, by {herb}'s extract or constituents, of "
              "the enzymes and transporters that clear {drug}",
              ("inhibits_or_induces", "no_effect"), in_vitro,
              "literature_search; connector_call (chembl activities)",
              "inhibits_or_induces: an IC50, Ki or induction EC50 within reach of the "
              "herb's gut or plasma concentrations; no_effect otherwise",
              design="in_vitro", cost=0.5)),
        ("In-vitro inhibition is a reason for a clinical study, not a finding about "
         "patients.",
         "A provisional verdict is not reassurance: the absence of a recorded interaction "
         "is not evidence that none exists.", GAP_NOTE))


def _target_engagement() -> Scaffold:
    biochemical = {
        "binds": {"active": 0.85, "inactive": 0.15},
        "indirect": {"active": 0.10, "inactive": 0.90},
        "interference": {"active": 0.85, "inactive": 0.15}}
    counter = {
        "binds": {"retained": 0.85, "lost": 0.15},
        "indirect": {"retained": 0.50, "lost": 0.50},
        "interference": {"retained": 0.10, "lost": 0.90}}
    biophysical = {
        "binds": {"binding": 0.82, "no_binding": 0.18},
        "indirect": {"binding": 0.08, "no_binding": 0.92},
        "interference": {"binding": 0.15, "no_binding": 0.85}}
    cellular = {
        "binds": {"changed": 0.80, "unchanged": 0.20},
        "indirect": {"changed": 0.85, "unchanged": 0.15},
        "interference": {"changed": 0.30, "unchanged": 0.70}}
    find = ("connector_call (chembl activities and assays, pubchem bioassays), "
            "tcmdb_relations (ingredient_target, evidence known) and literature_search")
    act = ("active: a dose–response with a potency (IC50, EC50, Ki or Kd) in a cell-free "
           "assay; inactive: tested and inactive up to the highest concentration")
    bind = ("binding: a measured Kd, or a concentration-dependent thermal shift, at "
            "relevant concentrations; no_binding: tested without one")
    return Scaffold(
        "target_engagement", {"zh": "靶点结合：直接作用还是假象？",
                              "en": "Target engagement: binding or artefact?"},
        "Does {compound} act on {target} by binding it?",
        (Param("compound", "the compound, e.g. quercetin"),
         Param("target", "the protein, e.g. PTGS2")),
        (Rival("binds", "{compound} binds {target} and changes its activity directly",
               "mechanism", 0.3),
         Rival("indirect", "{compound} changes {target}'s activity or level without binding "
               "it (through upstream signalling or expression)", "mechanism", 0.25),
         Rival("interference", "the activity measured is assay interference (colloidal "
               "aggregation, fluorescence, redox cycling or covalent reactivity), not an "
               "effect on {target}", "", 0.35)),
        (Test("biochemical", "a cell-free assay of {compound} on purified {target}, with a "
              "dose–response", ("active", "inactive"), biochemical, find, act,
              design="in_vitro"),
         Test("biochemical-rep", "the same in an independent assay (another laboratory, "
              "format or protein batch)", ("active", "inactive"), biochemical, find, act,
              design="in_vitro", replicates="biochemical"),
         Test("counter_screen", "the cell-free assay repeated with 0.01% Triton X-100 and an "
              "orthogonal readout (the aggregation and readout-interference "
              "counter-screens)", ("retained", "lost"), counter, find,
              "retained: the activity survives detergent and the orthogonal readout; lost: "
              "it does not", design="in_vitro"),
         Test("biophysical", "direct binding of {compound} to {target} measured "
              "biophysically (SPR, ITC, thermal shift or CETSA)", ("binding", "no_binding"),
              biophysical, find, bind, design="in_vitro"),
         Test("biophysical-rep", "the same by a second biophysical method",
              ("binding", "no_binding"), biophysical, find, bind, design="in_vitro",
              replicates="biophysical"),
         Test("cellular", "{target}'s activity or level in cells treated with {compound}, "
              "at non-cytotoxic concentrations", ("changed", "unchanged"), cellular, find,
              "changed: a concentration-dependent change in cells at non-cytotoxic "
              "concentrations; unchanged: none", design="in_vitro")),
        ("Docking and target prediction cannot move belief in binding: they license a "
         "mechanism hypothesis, which is a reason to run these tests.",
         "A substructure alert (run_pipeline admet) is a prior concern about interference, "
         "not an outcome.", GAP_NOTE))


SCAFFOLDS: Mapping[str, Scaffold] = {s.id: s for s in (
    _pathway_signal(), _formula_vs_constituent(), _combination(), _exposure(),
    _efficacy(), _safety_signal(), _herb_drug_interaction(), _target_engagement())}


def scaffold(scaffold_id: str) -> Scaffold:
    if scaffold_id not in SCAFFOLDS:
        raise ValueError(f"no scaffold {scaffold_id!r}; the scaffolds are "
                         + ", ".join(SCAFFOLDS))
    return SCAFFOLDS[scaffold_id]


# ---------------------------------------------------------------- parameters

def parameters(scaffold_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """The scaffold's parameters, checked and completed with their defaults. Raises
    ``ValueError`` naming every problem."""
    s = scaffold(scaffold_id)
    known = {p.name for p in s.params}
    problems = [f"unknown parameter {k!r}" for k in values if k not in known]
    out: dict[str, Any] = {}
    for p in s.params:
        v = values.get(p.name)
        if v is None or (isinstance(v, str) and not v.strip()):
            if p.required:
                problems.append(f"{p.name} is required ({p.description})")
            out[p.name] = p.default
            continue
        if p.kind == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v) or v <= 0:
                problems.append(f"{p.name} must be a positive number, not {v!r}")
                continue
            out[p.name] = float(v)
        elif p.kind == "choice":
            if v not in p.choices:
                problems.append(f"{p.name} must be one of {list(p.choices)}, not {v!r}")
                continue
            out[p.name] = v
        else:
            if not isinstance(v, str):
                problems.append(f"{p.name} must be text")
                continue
            out[p.name] = " ".join(v.split())
    if problems:
        raise ValueError("; ".join(problems))
    return out


def _text(s: Scaffold, values: Mapping[str, Any]) -> dict[str, str]:
    shown = {}
    for p in s.params:
        v = values.get(p.name)
        shown[p.name] = (p.unset if v is None else f"{v:g}" if isinstance(v, float)
                         else str(v))
    return shown


def rendered(scaffold_id: str, values: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """How each test is run and classified, with the scaffold's values written in:
    ``{test id: {run_with, classify[, classifier, data]}}``."""
    s = scaffold(scaffold_id)
    shown = _text(s, parameters(scaffold_id, values))
    return {t.id: {"run_with": t.run_with.format_map(shown),
                   "classify": t.classify.format_map(shown),
                   **({"classifier": t.classifier, "data": CLASSIFIER_DATA[t.classifier]}
                      if t.classifier else {})} for t in s.tests}


# ----------------------------------------------------------------- opening

def open_scaffold(scaffold_id: str, values: Mapping[str, Any], *, rule: Any = None,
                  recorder: Any = None, inquiry_id: str = "") -> Any:
    """An inquiry for one scaffold, with every prediction sealed before anything runs."""
    from psh.scientist import Analysis, Explanation, Inquiry, Role, StoppingRule
    from psh.sir.values import ClaimKind, StudyDesign
    s = scaffold(scaffold_id)
    values = parameters(scaffold_id, values)
    shown = _text(s, values)
    model = values.get("model")
    explanations = [
        Explanation(r.id, r.proposition.format_map(shown),
                    ClaimKind(r.claim_kind) if r.claim_kind else None,
                    role=Role.SUBSTANTIVE if r.claim_kind else Role.ARTEFACT, prior=r.prior)
        for r in s.rivals]
    analyses = [Analysis(t.id, t.title.format_map(shown), StudyDesign(t.design or model),
                         t.outcomes, cost=t.cost, replicates=t.replicates)
                for t in s.tests]
    inquiry = Inquiry(s.question.format_map(shown), explanations, analyses,
                      rule or StoppingRule(**s.rule), recorder=recorder,
                      inquiry_id=inquiry_id)
    for t in s.tests:
        inquiry.declare(t.id, {h: dict(row) for h, row in t.predictions.items()})
    return inquiry


def matches(inquiry: Any, scaffold_id: str, values: Mapping[str, Any]) -> bool:
    """Whether ``inquiry`` was opened from this scaffold with these values: its trail
    begins with exactly the entries opening it again would write. A runtime classifier
    reads its criteria from the values, so it applies them only when they are the ones
    sealed into the trail."""
    try:
        again = open_scaffold(scaffold_id, values, rule=inquiry.rule,
                              inquiry_id=inquiry.inquiry_id)
    except Exception:                                           # noqa: BLE001
        return False
    mine, theirs = inquiry.trail, again.trail
    return len(mine) >= len(theirs) and all(
        a["digest"] == b["digest"] for a, b in zip(mine, theirs))


def describe(scaffold_id: str = "") -> Any:
    """Scaffolds for a reader or a model: all of them in brief, or one in full."""
    def brief(s: Scaffold) -> dict[str, Any]:
        return {"id": s.id, "title": dict(s.title), "question": s.question,
                "params": [p.name for p in s.params],
                "rivals": [r.id for r in s.rivals], "tests": [t.id for t in s.tests]}
    if not scaffold_id:
        return [brief(s) for s in SCAFFOLDS.values()]
    s = scaffold(scaffold_id)
    out = brief(s)
    out["params"] = [{"name": p.name, "description": p.description, "kind": p.kind,
                      **({"choices": list(p.choices)} if p.choices else {}),
                      "required": p.required,
                      **({"default": p.default} if p.default is not None else {})}
                     for p in s.params]
    out["rivals"] = [{"id": r.id, "proposition": r.proposition,
                      "claim_kind": r.claim_kind or None,
                      "role": "substantive" if r.claim_kind else "artefact",
                      "prior": r.prior} for r in s.rivals]
    out["tests"] = [{"id": t.id, "title": t.title, "design": t.design or "model",
                     "outcomes": list(t.outcomes), "cost": t.cost,
                     "replicates": t.replicates, "run_with": t.run_with,
                     "classify": t.classify,
                     "classified_by": "runtime" if t.classifier else "agent",
                     **({"data": CLASSIFIER_DATA[t.classifier]} if t.classifier else {}),
                     "predictions": {h: dict(row) for h, row in t.predictions.items()}}
                    for t in s.tests]
    out["notes"] = list(s.notes)
    return out


# ------------------------------------------------------------- classification

def _refuse(why: str) -> Unclassifiable:
    return Unclassifiable(why)


def _close(a: Any, b: float) -> bool:
    try:
        return math.isclose(float(a), b, rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


def _number(data: Mapping[str, Any], key: str) -> float:
    v = data.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _refuse(f"{key} must be a finite number")
    return float(v)


def _contrast(test: Test, arguments: Mapping[str, Any], result: Mapping[str, Any],
              values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if arguments.get("margin") is None or not _close(arguments["margin"], values["margin"]):
        raise _refuse(f"the contrast needs the equivalence margin sealed when the inquiry "
                      f"opened (margin: {values['margin']:g})")
    better = "higher" if arguments.get("higher_is_better", True) else "lower"
    if better != values["better"]:
        raise _refuse(f"the inquiry was opened with '{values['better']} is better' "
                      f"(higher_is_better: {values['better'] == 'higher'})")
    if not _close(arguments.get("alpha", 0.05), 0.05):
        raise _refuse("the contrast is sealed at alpha 0.05")
    if arguments.get("dose_matched") is not True:
        raise _refuse("the constituent must be given at its amount in the formula's dose "
                      "(dose_matched: true): a dose mismatch can create or hide a difference")
    verdict = str(result.get("verdict") or "")
    if verdict not in test.outcomes:
        raise _refuse(f"the contrast returned no verdict among {list(test.outcomes)}")
    return verdict, {"difference": result.get("difference"),
                     "equivalence": result.get("equivalence")}


def _combination(test: Test, arguments: Mapping[str, Any], result: Mapping[str, Any],
                 values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if arguments.get("primary") != values["reference"]:
        raise _refuse(f"the reference model was sealed as {values['reference']!r} "
                      "(primary)")
    if not _close(arguments.get("cytotoxic_above", 0.5), 0.5) \
            or not _close(arguments.get("level", 0.95), 0.95):
        raise _refuse("the matrix is sealed at cytotoxic_above 0.5 and level 0.95")
    combos = [c for c in arguments.get("cells") or ()
              if c.get("dose_a", 0) > 0 and c.get("dose_b", 0) > 0]
    if not combos or any(not c.get("cytotoxicity") for c in combos):
        raise _refuse("every combination needs a cytotoxicity readout of the same wells: "
                      "without it an excess cannot be called selective")
    exceeds = result.get("exceeds_reference") or {}
    if int(exceeds.get("selective", 0)) > 0:
        outcome = "selective_excess"
    elif int(exceeds.get("cells", 0)) > 0:
        outcome = "cytotoxic_excess"
    else:
        outcome = "no_excess"
    return outcome, {"exceeds_reference": dict(exceeds),
                     "primary_model": result.get("primary_model")}


def _fold(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _exposure(test: Test, arguments: Mapping[str, Any], result: Mapping[str, Any],
              values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    molecule = values.get(test.subject)
    if not molecule:
        raise _refuse(f"the inquiry was opened without naming the {test.subject}, so no "
                      "screened pair can be attributed to it")
    if _fold(arguments.get("site")) != _fold(values["site"]):
        raise _refuse(f"the inquiry is about site {values['site']!r}, not "
                      f"{arguments.get('site')!r}")
    pairs = [r for r in result.get("pairs") or ()
             if _fold(r.get("molecule")) == _fold(molecule)
             and _fold(r.get("target")) == _fold(values["target"])]
    if not pairs:
        seen = sorted({f"{r.get('molecule')} on {r.get('target')}"
                       for r in result.get("pairs") or ()})
        raise _refuse(f"no screened pair is of {molecule} on {values['target']} "
                      f"(screened: {', '.join(seen) or 'none'})")
    decided = [r for r in pairs
               if r.get("verdict") in ("plausible", "implausible", "depends_on_unknowns")]
    if not decided:
        why = sorted({str(r.get("reason") or r.get("verdict")) for r in pairs})
        raise _refuse("no pair could be judged: " + "; ".join(why))
    verdicts = {r["verdict"] for r in decided}
    outcome = verdicts.pop() if len(verdicts) == 1 else "depends_on_unknowns"
    return outcome, {"pairs": len(pairs), "judged": len(decided),
                     "verdicts": sorted(r["verdict"] for r in decided)}


def _meta(test: Test, arguments: Mapping[str, Any], result: Mapping[str, Any],
          values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if arguments.get("scale", "log") != values["scale"]:
        raise _refuse(f"the inquiry was opened on the {values['scale']} scale")
    pooled = result.get("random_effects") or {}
    low, high = _number(pooled, "ci_low"), _number(pooled, "ci_high")
    mcid = values.get("mcid")
    if values["better"] == "higher":
        significant, short = low > 0, mcid is not None and high < mcid
    else:
        significant, short = high < 0, mcid is not None and low > -mcid
    outcome = ("no_important_benefit" if short else "benefit" if significant
               else "inconclusive")
    return outcome, {"k": result.get("k"), "random_effects": dict(pooled),
                     "heterogeneity": result.get("heterogeneity")}


def _ratio_interval(test: Test, arguments: Mapping[str, Any], result: Mapping[str, Any],
                    values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    ratio, low, high = (_number(arguments, k) for k in ("ratio", "ci_low", "ci_high"))
    if not 0 < low <= ratio <= high:
        raise _refuse("need 0 < ci_low <= ratio <= ci_high (a ratio and its 90% CI)")
    if 0.8 <= low and high <= 1.25:
        outcome = "unchanged"
    elif (high < 1 or low > 1) and not 0.8 <= ratio <= 1.25:
        outcome = "changed"
    else:
        outcome = "inconclusive"
    return outcome, {"ratio": ratio, "ci_90": [low, high], "limits": [0.8, 1.25]}


def _difference_interval(test: Test, arguments: Mapping[str, Any],
                         result: Mapping[str, Any],
                         values: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    estimate, low, high = (_number(arguments, k) for k in ("estimate", "ci_low", "ci_high"))
    if not low <= estimate <= high:
        raise _refuse("need ci_low <= estimate <= ci_high (a difference and its 95% CI)")
    t = values["threshold"]
    if -t <= low and high <= t:
        outcome = "unchanged"
    elif (low > 0 or high < 0) and abs(estimate) >= t:
        outcome = "changed"
    else:
        outcome = "inconclusive"
    return outcome, {"estimate": estimate, "ci_95": [low, high], "threshold": t}


Classifier = Callable[[Test, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
                      tuple[str, dict[str, Any]]]

#: name -> the rule applied to (test, the arguments a tool ran with, its result, the
#: scaffold's values)
CLASSIFIERS: Mapping[str, Classifier] = {
    "contrast": _contrast, "combination": _combination, "exposure": _exposure,
    "meta": _meta, "ratio_interval": _ratio_interval,
    "difference_interval": _difference_interval}

#: The catalog entry whose result each classifier reads; empty when the classifier reads
#: the numbers it is given (a published ratio or difference and its interval).
CLASSIFIER_TOOLS: Mapping[str, str] = {
    "contrast": "study.contrast_formula_monomer", "combination": "study.combination",
    "exposure": "study.exposure_screen", "meta": "native.meta_analysis",
    "ratio_interval": "", "difference_interval": ""}

#: The arguments each classifier's data are in (:func:`inputs`). A tuple of names is read
#: as rows, the n-th value of each together: a trial's estimate with its standard error.
CLASSIFIER_INPUTS: Mapping[str, tuple[str | tuple[str, ...], ...]] = {
    "contrast": ("formula", "monomer"), "combination": ("cells",),
    "exposure": ("exposures", "assays"), "meta": (("estimates", "standard_errors"),),
    "ratio_interval": ("ratio", "ci_low", "ci_high"),
    "difference_interval": ("estimate", "ci_low", "ci_high")}

#: What ``data`` holds for each classifier, for the model.
CLASSIFIER_DATA: Mapping[str, str] = {
    "contrast": "the arguments of study.contrast_formula_monomer",
    "combination": "the arguments of study.combination",
    "exposure": "the arguments of study.exposure_screen",
    "meta": "the arguments of native.meta_analysis (estimates, standard_errors, labels, "
            "scale)",
    "ratio_interval": "{ratio, ci_low, ci_high}: a geometric mean ratio and its 90% CI",
    "difference_interval": "{estimate, ci_low, ci_high}: a difference and its 95% CI"}


def inputs(classifier: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The data a classifier reads, in a form two recordings can be compared in: for each
    argument its data are in, every number as a float, each record (a cell, an exposure, a
    trial's estimate and standard error) keeping its numbers together under their keys, and
    the records and values in sorted order. Labels and study names, other arguments, the
    order of the records and whether a number is written 1 or 1.0 do not make a second
    data set; the same numbers paired differently do."""
    def canonical(value: Any) -> Any:
        if isinstance(value, Mapping):
            fields = [[str(k), canonical(v)] for k, v in value.items()]
            return sorted(f for f in fields if f[1] is not None)
        if isinstance(value, (list, tuple)):
            return sorted((canonical(v) for v in value), key=json.dumps)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value)):
            return float(value)
        return None                                   # text: a label, a name, a note
    out: dict[str, Any] = {}
    for name in CLASSIFIER_INPUTS[classifier]:
        if isinstance(name, tuple):
            columns = [arguments.get(n) for n in name]
            rows = zip(*(c if isinstance(c, (list, tuple)) else () for c in columns))
            out["+".join(name)] = canonical([list(row) for row in rows])
        else:
            out[name] = canonical(arguments.get(name))
    return out


def classify(scaffold_id: str, test_id: str, values: Mapping[str, Any],
             arguments: Mapping[str, Any], result: Mapping[str, Any] | None = None
             ) -> tuple[str, dict[str, Any]]:
    """The outcome of one runtime-classified test, from the arguments its tool ran with
    and its result (``None`` for a classifier that reads the arguments alone), and what
    the rule read. Raises :class:`Unclassifiable`."""
    s = scaffold(scaffold_id)
    test = next((t for t in s.tests if t.id == test_id), None)
    if test is None or not test.classifier:
        raise _refuse(f"{test_id!r} is not a runtime-classified test of {scaffold_id}")
    values = parameters(scaffold_id, values)
    outcome, read = CLASSIFIERS[test.classifier](test, arguments, result or arguments,
                                                 values)
    if outcome not in test.outcomes:                            # pragma: no cover
        raise _refuse(f"{outcome!r} is not an outcome of {test_id}")
    return outcome, read

