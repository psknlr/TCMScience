"""The statistical pass: will this design support the inference it is for?

This is the family with no counterpart in a coding agent's compiler, and the one that
decides whether an autonomous scientist produces findings or produces significant numbers.
The 2026 evaluations make the same point from two directions: process-level benchmarking
of biomedical agents finds method selection and interpretation, not tool use, to be where
they fail; and an independent null-model re-analysis of an autonomous discovery system
found part of its output did not beat a random baseline.

So the rules below are the questions a methods reviewer asks, made mechanical:

    how many tests, and corrected how          STAT401 / STAT402
    powered for what                           STAT403
    censored how                               STAT404 / STAT405
    selected features on which data            STAT406
    evaluated on what held-out data            STAT407
    which subgroups, declared when             STAT408
    compared against which null                STAT409
    adjusted for which batch                   STAT410
    missing data handled how                   STAT411
    reported with what interval                STAT412
    replicated where                           STAT413

Two conventions, both deliberate. An **unstated** field is reported as unstated and never
assumed satisfied — the asymmetry ``ScopeVerdict.incomplete`` already uses, because a check
that did not run must not read like a check that passed. And the errors are the ones that
*invalidate* the result rather than weaken it: leakage, an uncorrected screen, a survival
analysis with no censoring rule, a discovery with no null. Everything else is a warning,
because exploratory work legitimately starts without half of these and a compiler that
refuses exploration gets switched off.
"""

from __future__ import annotations

from typing import Any

from ..sir import AnalysisSpec, Role, SIRNode, SIRProgram
from .diagnostics import Diagnostics

__all__ = ["check_statistics", "DISCOVERY_FAMILIES", "CONFIRMATORY_FAMILIES"]

#: Families whose whole purpose is to find something in a large space. They are the ones a
#: null model applies to: "this gene set is enriched" means nothing until random gene sets
#: have been shown not to be.
DISCOVERY_FAMILIES: frozenset[str] = frozenset({"screen", "discovery", "enrichment",
                                                "genome_wide", "omics"})
#: Families that test a stated hypothesis and therefore need a power argument.
CONFIRMATORY_FAMILIES: frozenset[str] = frozenset({"survival", "regression",
                                                   "classification", "trial",
                                                   "comparison"})
#: Families that fit a model and report its performance, so they need held-out data.
PREDICTIVE_FAMILIES: frozenset[str] = frozenset({"classification", "regression",
                                                 "prediction"})

_NO_CORRECTION = frozenset({"", "none", "no", "unstated"})
_NO_SPLIT = frozenset({"", "none", "unstated"})
_NO_NULL = frozenset({"", "none", "unstated"})


def check_statistics(program: SIRProgram, *,
                     diagnostics: Diagnostics | None = None) -> Diagnostics:
    out = diagnostics if diagnostics is not None else Diagnostics(pass_name="statistics")
    for node in program.nodes:
        if node.analysis is None:
            continue
        _check(node, node.analysis, program, out)
    return out


def _check(node: SIRNode, spec: AnalysisSpec, program: SIRProgram,
           out: Diagnostics) -> None:
    family = (spec.family or "").strip().lower()
    method = (spec.method or "").strip().lower()
    where = node.node_id

    # ---- multiplicity ----------------------------------------------------
    if spec.tests is None:
        if family in DISCOVERY_FAMILIES or family in CONFIRMATORY_FAMILIES:
            out.emit("STAT402",
                     f"analysis {where!r} does not say how many hypotheses it tests, so "
                     "whether it needs a multiplicity correction cannot be judged",
                     node_id=where)
    elif spec.tests > 1 and (spec.multiplicity or "").lower() in _NO_CORRECTION:
        out.emit("STAT401",
                 f"analysis {where!r} performs {spec.tests} tests at alpha={spec.alpha:g} "
                 f"and declares no multiplicity correction; "
                 f"{spec.tests * spec.alpha:.1f} false positives are expected by chance "
                 "alone", node_id=where, tests=spec.tests, alpha=spec.alpha)

    if not 0.0 < spec.alpha <= 0.5:
        out.emit("STAT414",
                 f"analysis {where!r} declares alpha={spec.alpha!r}", node_id=where,
                 alpha=spec.alpha)

    # ---- power -----------------------------------------------------------
    confirmatory = family in CONFIRMATORY_FAMILIES or bool(spec.primary_endpoint)
    if confirmatory and spec.sample_size is None and spec.power is None:
        out.emit("STAT403",
                 f"analysis {where!r} tests a stated endpoint and declares neither a "
                 "sample size nor a power; a null result from it is uninterpretable",
                 node_id=where)

    # ---- survival --------------------------------------------------------
    if family == "survival" or "kaplan" in method or "cox" in method or \
            "log-rank" in method or "logrank" in method:
        if not (spec.censoring or "").strip():
            out.emit("STAT404",
                     f"analysis {where!r} is a time-to-event analysis and states no "
                     "censoring strategy; who is censored and when changes the estimate",
                     node_id=where)
        if "cox" in method and not spec.proportional_hazards_checked:
            out.emit("STAT405",
                     f"analysis {where!r} fits a proportional-hazards model and does not "
                     "check the assumption it rests on", node_id=where)

    # ---- leakage ---------------------------------------------------------
    if (spec.feature_selection or "").lower() == "all_data":
        out.emit("STAT406",
                 f"analysis {where!r} selects features on all the data and then evaluates "
                 "on part of it; the evaluation measures the selection, not the model",
                 node_id=where)
    if family in PREDICTIVE_FAMILIES and (spec.data_split or "").lower() in _NO_SPLIT:
        out.emit("STAT407",
                 f"analysis {where!r} fits and reports a {family} model with no held-out "
                 "data", node_id=where)

    # ---- preregistration of subgroups ------------------------------------
    if spec.subgroups and not spec.preregistered:
        out.emit("STAT408",
                 f"analysis {where!r} reports {len(spec.subgroups)} subgroup(s) "
                 f"{list(spec.subgroups)[:4]} that were not preregistered",
                 node_id=where, subgroups=list(spec.subgroups))

    # ---- the null --------------------------------------------------------
    if family in DISCOVERY_FAMILIES and (spec.null_model or "").lower() in _NO_NULL:
        out.emit("STAT409",
                 f"analysis {where!r} searches for a finding in a large space and declares "
                 "no null model; until a permutation, a random feature set or a negative "
                 "control has been shown not to produce the same result, a significant "
                 "value is not a finding", node_id=where, family=family)
    if family in DISCOVERY_FAMILIES and (spec.replication or "").lower() in _NO_NULL \
            and _feeds_a_claim(node, program):
        out.emit("STAT413",
                 f"analysis {where!r} supports a claim and declares no replication",
                 node_id=where)

    # ---- confounding and completeness ------------------------------------
    if spec.batch_variable and not spec.batch_adjusted:
        out.emit("STAT410",
                 f"analysis {where!r} names {spec.batch_variable!r} as a batch variable "
                 "and does not adjust for it", node_id=where,
                 batch=spec.batch_variable)
    if not (spec.missingness or "").strip():
        out.emit("STAT411",
                 f"analysis {where!r} does not say how missing data are handled",
                 node_id=where)
    if not spec.reports_interval:
        out.emit("STAT412",
                 f"analysis {where!r} reports no interval estimate", node_id=where)

    # ---- plausibility against the data it will actually see ---------------
    rows = _upstream_rows(node, program)
    if spec.tests is not None and rows is not None and spec.tests > max(rows, 1) * 100:
        out.emit("STAT415",
                 f"analysis {where!r} declares {spec.tests} tests and its input produces "
                 f"{rows} row(s)", node_id=where, tests=spec.tests, rows=rows)


def _feeds_a_claim(node: SIRNode, program: SIRProgram) -> bool:
    return any(program.node(nid) is not None
               and program.node(nid).produces.is_claim  # type: ignore[union-attr]
               for nid in program.downstream_of(node.node_id))


def _upstream_rows(node: SIRNode, program: SIRProgram) -> int | None:
    """The row count of the nearest upstream node that states one."""
    for dependency in node.dependencies:
        source = program.node(dependency)
        if source is None:
            continue
        data = source.produces.data
        if data is not None and data.rows is not None:
            return data.rows
    return None
