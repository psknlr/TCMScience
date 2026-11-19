"""What does a network-pharmacology signal mean? A governed inquiry decides.

``run_network_pharmacology`` finds that a formula's measured targets concentrate in a
pathway. ``studies.validation`` then runs every robustness check on that result. Both are
fixed procedures: the same analyses, in the same order, whatever the first one showed.

This module asks the question the checks exist for: *which explanation of the signal is
true?* Each check is the test of one rival explanation, so the rivals are written down
first, with what each predicts every analysis will show, and PSH's inquiry engine
(``psh.scientist.inquiry``) chooses what to run:

=================  ==========================================  ===========================
explanation        it says                                     the analysis that tests it
=================  ==========================================  ===========================
``target``         the constituents act selectively on the     (the one the others must
                   pathway (a mechanism hypothesis)            fail to explain)
``coverage``       the concentration is which proteins were    enrichment against the
                   assayed                                     assayed proteins
``promiscuity``    the pathway's proteins are hit by many      random herb combinations
                   compounds; any formula reaches it           of the formula's size
=================  ==========================================  ===========================

The usual analysis, enrichment against the whole Reactome annotation, is on the list too.
Every explanation predicts that it comes out enriched, so it is worth almost nothing and
the engine rarely runs it: that is the point of the 葛根芩连汤 case study, computed rather
than argued.

What the engine adds over running every check: the analyses are chosen by expected
information gain per network-pharmacology run, the predictions are sealed before any of
them runs, belief moves only by those predictions, and the inquiry stops when the leader
has passed a severe test against each rival and each such test has been replicated. The
conclusion is capped by what in-silico evidence licenses: at most a tentative mechanism
hypothesis, however high the posterior. A mechanism claim is released only when the
inquiry accepts ``target`` *and* the pipeline's own release check passed it.

Analyses run in this process; ``provenance.governed_execution`` says so. The executor is
the port where the kernel's execution broker goes, as it does in the research loop.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = ["ANALYSES", "PathwayInquiryResult", "explanations", "pathway_inquiry",
           "predictions", "render_markdown", "run_pathway_inquiry"]

#: id -> (title, outcomes, replicates, kind). Costs are set from the run counts below.
ANALYSES: Mapping[str, tuple[str, tuple[str, ...], str, str]] = {
    "annotated": ("enrichment against the whole Reactome annotation (the usual analysis)",
                  ("enriched", "not_enriched"), "", "enrichment"),
    "assayed": ("enrichment against the proteins the constituents were assayed against",
                ("enriched", "not_enriched"), "", "enrichment"),
    "assayed-independent": ("assayed-background enrichment on independent activity records",
                     ("enriched", "not_enriched"), "assayed", "enrichment"),
    "random-herbs": ("random herb combinations of the formula's size",
                     ("specific", "not_specific"), "", "random"),
    "random-herbs-2": ("random herb combinations on independent activity records",
                       ("specific", "not_specific"), "random-herbs",
                       "random"),
    "rewired": ("degree-preserving rewiring of the compound-target network",
                ("beyond_wiring", "explained_by_degree"), "", "rewired"),
    "rewired-2": ("degree-preserving rewiring on independent activity records",
                  ("beyond_wiring", "explained_by_degree"), "rewired",
                  "rewired"),
}

#: What each explanation predicts each kind of analysis will show, sealed before anything
#: runs. Read a row as "if this explanation is true, the analysis comes out ...".
#:
#: * Everyone predicts the whole-annotation enrichment. That is why it decides nothing.
#: * ``coverage`` predicts nothing survives the assayed background, and so that the later
#:   checks, which start from the assayed result, have nothing to test.
#: * ``promiscuity`` predicts that random formulas reach the pathway. It is vaguer about
#:   rewiring: rewiring keeps how often each protein is hit, which keeps some of a
#:   promiscuous pathway's hits and not necessarily enough of them.
_PREDICTIONS: Mapping[str, Mapping[str, Mapping[str, float]]] = {
    "annotated": {
        "target": {"enriched": 0.9, "not_enriched": 0.1},
        "coverage": {"enriched": 0.9, "not_enriched": 0.1},
        "promiscuity": {"enriched": 0.9, "not_enriched": 0.1}},
    "enrichment": {
        "target": {"enriched": 0.85, "not_enriched": 0.15},
        "coverage": {"enriched": 0.05, "not_enriched": 0.95},
        "promiscuity": {"enriched": 0.85, "not_enriched": 0.15}},
    # Conditional on an eligible, significant primary result. Controls that cannot run
    # generate no evidence; their absence has no likelihood.
    "random": {
        "target": {"specific": 8/9, "not_specific": 1/9},
        "coverage": {"specific": 0.5, "not_specific": 0.5},
        "promiscuity": {"specific": 1/9, "not_specific": 8/9}},
    "rewired": {
        "target": {"beyond_wiring": 8/9, "explained_by_degree": 1/9},
        "coverage": {"beyond_wiring": 0.5, "explained_by_degree": 0.5},
        "promiscuity": {"beyond_wiring": 4/9, "explained_by_degree": 5/9}},
}

#: The fraction of random formulas (or rewired networks) that may reach the pathway for it
#: still to count as specific to this formula; the validation suite's own limit.
SPECIFICITY_LIMIT = 0.05


def explanations(pathway: str, formula: Any) -> list[Any]:
    from psh.scientist import Explanation, Role
    from psh.sir.values import ClaimKind
    name = getattr(formula, "chinese", "") or getattr(formula, "id", "the formula")
    return [
        Explanation("target", f"{name}'s constituents act selectively on {pathway}: its "
                    "measured targets concentrate there beyond what was assayed and what "
                    "random herb combinations reach", ClaimKind.MECHANISM_HYPOTHESIS,
                    prior=0.3),
        Explanation("coverage", f"the concentration in {pathway} reflects which proteins "
                    "the constituents were assayed against, not their activity",
                    role=Role.ARTEFACT, prior=0.3),
        Explanation("promiscuity", f"{pathway}'s proteins are hit by many compounds, so "
                    "any herb combination of this size reaches it", role=Role.ARTEFACT,
                    prior=0.3),
    ]


def predictions(analysis_id: str) -> dict[str, dict[str, float]]:
    kind = "annotated" if analysis_id == "annotated" else ANALYSES[analysis_id][3]
    return {h: dict(row) for h, row in _PREDICTIONS[kind].items()}


def pathway_inquiry(pathway: str, formula: Any, *, controls: int = 60,
                    rewirings: int = 60, rule: Any = None, recorder: Any = None,
                    question: str = "") -> Any:
    """An inquiry with every prediction sealed, before any analysis has run."""
    from psh.scientist import Analysis, Inquiry, StoppingRule
    from psh.sir.values import StudyDesign
    if rule is not None and not rule.require_replication:
        raise ValueError("a pathway inquiry requires independent replication")
    if controls < 1 or rewirings < 1:
        raise ValueError("controls and rewirings must be positive")
    runs = {"enrichment": 1, "random": controls + 1, "rewired": rewirings + 1}
    analyses = [Analysis(a, title, StudyDesign.IN_SILICO, outcomes,
                         cost=float(runs[kind]), replicates=replicates)
                for a, (title, outcomes, replicates, kind) in ANALYSES.items()]
    name = getattr(formula, "chinese", "") or getattr(formula, "id", "the formula")
    inquiry = Inquiry(
        question or f"Do {name}'s measured targets concentrate in {pathway}, and why?",
        explanations(pathway, formula), analyses,
        rule or StoppingRule(min_gain=0.001), recorder=recorder)
    for analysis in analyses:
        inquiry.declare(analysis.id, predictions(analysis.id))
    return inquiry


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _activity_origins(snapshots: Sequence[Any], screening: bool) -> set[str]:
    """Require a traceable origin for each activity in the proposed replication datasets."""
    from .inquiries import _sources
    origins: set[str] = set()
    for snap in snapshots:
        if (snap.key == "pubchem_bioassay") != screening:
            continue
        for edge in snap.edges:
            if edge.get("predicate") not in ("targets", "tested_against"):
                continue
            if edge.get("knowledge_level") == "prediction":
                continue
            papers = {source for citation in edge.get("publications") or ()
                      for source in _sources(str(citation)) if source.startswith(("pmid:", "doi:"))}
            aid = str(edge.get("source_record_id", "")).split("|", 1)[0]
            if screening and aid.isdigit():
                papers.add("aid:" + aid)
            if not papers:
                return set()
            origins.update(papers)
    return origins


def _null_frequency(hits: int, tested: int) -> tuple[bool, list[float]]:
    """Classify a prespecified upper frequency limit with one-sided 95% exact bounds."""
    from psh.scientist import AnalysisUnavailable
    from scipy.stats import beta
    if not tested:
        raise AnalysisUnavailable("no eligible control tested the original pathway")
    low = float(beta.ppf(.05, hits, tested - hits + 1)) if hits else 0.0
    high = float(beta.ppf(.95, hits + 1, tested - hits)) if hits < tested else 1.0
    if low <= SPECIFICITY_LIMIT < high:
        raise AnalysisUnavailable(f"{hits}/{tested} eligible controls reached the pathway; "
                                  f"the frequency bounds ({low:.4g}, {high:.4g}) "
                                  f"straddle {SPECIFICITY_LIMIT}")
    return high <= SPECIFICITY_LIMIT, [low, high]


class _Executor:
    """Runs one analysis and classifies its result into a declared outcome."""

    def __init__(self, snapshots: Sequence[Any], formula: Any, params: Any, pathway: str,
                 root: Path, controls: int, rewirings: int, replication: Sequence[Any]) -> None:
        self.snapshots, self.formula = list(snapshots), formula
        self.params = replace(params, background="assayed")
        self.pathway, self.root = pathway, root
        self.controls, self.rewirings = controls, rewirings
        self.replication = list(replication)
        self.replication_problem = "no independent activity dataset was supplied"
        if self.replication:
            first = _activity_origins(self.snapshots, params.hits == "screening")
            second = _activity_origins(self.replication, params.hits == "screening")
            self.replication_problem = ("" if first and second and first.isdisjoint(second)
                                        else "replication needs disjoint, traceable assay/study origins")
        self.results: dict[str, dict[str, Any]] = {}
        self.np_runs = 0
        self.assayed_significant: dict[bool, bool] = {}
        self.primary: Any = None       # the assayed-background result, when it ran

    def _significant(self, result: Any) -> bool:
        from ..studies.validation import _significant
        return self.pathway in _significant(result, self.params.fdr,
                                            self.params.permutation_alpha)

    def __call__(self, analysis: Any, step: Any) -> Any:
        from psh.scientist import AnalysisUnavailable, Executed
        from ..analysis.network_pharmacology import run_network_pharmacology
        from ..studies import validation as V
        kind = ANALYSES[analysis.id][3]
        if analysis.replicates and self.replication_problem:
            raise AnalysisUnavailable(self.replication_problem)
        independent = bool(analysis.replicates)
        if kind in ("random", "rewired") and self.assayed_significant.get(independent) is False:
            raise AnalysisUnavailable("the original pathway was not significant; no control test")
        snaps = self.replication if independent else self.snapshots
        seed = self.params.seed
        detail: dict[str, Any]
        if kind == "enrichment":
            background = "reactome" if analysis.id == "annotated" else "assayed"
            if background == "reactome" and self.params.hits == "screening":
                raise AnalysisUnavailable("screening has only the assay-matched background")
            result = run_network_pharmacology(
                snaps, formula=self.formula,
                params=replace(self.params, background=background, seed=seed))
            self.np_runs += 1
            if analysis.id == "assayed":
                self.primary = result
            if background == "assayed":
                self.assayed_significant[independent] = self._significant(result)
            row = next((r for r in result.enrichment if r["pathway"] == self.pathway), None)
            if row is None or row.get("q_value") is None:
                raise AnalysisUnavailable(f"{self.pathway} was not eligible for testing in {background}")
            if self.params.hits == "potency":
                from ..tools.stats import hypergeometric_test
                n = result.background["potent_in_background"]
                total = result.background["proteins"]
                size = row["in_background"]
                minimum = hypergeometric_test(min(n, size), n, size, total)["p_value"] if n else 1
                if minimum > self.params.fdr:
                    raise AnalysisUnavailable(f"no attainable enrichment at the specified FDR in {background}")
            outcome = "enriched" if self._significant(result) else "not_enriched"
            digest = result.digest()
            detail = {"background": background, "seed": seed,
                      "q_value": row.get("q_value"), "empirical_p": row.get("empirical_p"),
                      "overlap": row.get("overlap"), "result_digest": digest}
        elif kind == "random":
            report = V.random_formula_control(
                snaps, self.formula, self.params, n=self.controls,
                seed=20261007 + (1 if analysis.replicates else 0),
                root=self.root / analysis.id)
            if not report.get("performed"):
                raise AnalysisUnavailable(report.get("why") or "no random formulas")
            self.np_runs += 1 + self.controls
            reached = report["specificity"].get(self.pathway)
            if self.pathway not in report["specificity"]:
                raise AnalysisUnavailable("the original pathway was not significant; no specificity test")
            tested = report["tested_in"][self.pathway]
            below, bounds = _null_frequency(report["reached_count"][self.pathway], tested)
            outcome = "specific" if below else "not_specific"
            digest = _digest(report)
            detail = {"controls": report["controls"], "seed": report["seed"],
                      "reached_by_random_formulas": reached, "tested": tested,
                      "one_sided_95_bounds": bounds,
                      "global_count_empirical_p": report["empirical_p"], "pool": report["pool"],
                      "result_digest": digest}
        else:
            report = V.rewired_control(snaps, self.formula, self.params,
                                       n=self.rewirings,
                                       seed=20261007 + (1 if analysis.replicates else 0))
            self.np_runs += 1 + self.rewirings
            still = report["still_enriched"].get(self.pathway)
            if self.pathway not in report["still_enriched"]:
                raise AnalysisUnavailable("the original pathway was not significant; no rewiring test")
            tested = report["tested_in"][self.pathway]
            below, bounds = _null_frequency(report["reached_count"][self.pathway], tested)
            outcome = "beyond_wiring" if below else "explained_by_degree"
            digest = _digest(report)
            detail = {"rewirings": report["rewirings"], "seed": report["seed"],
                      "still_enriched": still, "tested": tested, "one_sided_95_bounds": bounds,
                      "global_count_empirical_p": report["empirical_p"],
                      "result_digest": digest}
        self.results[analysis.id] = {"outcome": outcome, **detail,
                                     "input_snapshots": [s.snapshot_id for s in snaps]}
        return Executed(outcome, evidence_ref=f"{analysis.id}:{digest[:16]}",
                        result_digest=digest)


@dataclass(frozen=True)
class PathwayInquiryResult:
    """An inquiry into one pathway signal, and what it may release."""

    pathway: str
    formula: str
    conclusion: Any                      # psh.scientist.Conclusion
    inquiry: Any                         # psh.scientist.Inquiry
    executions: Mapping[str, Mapping[str, Any]]
    released: tuple[Mapping[str, Any], ...]
    refused: tuple[str, ...]
    snapshots: tuple[str, ...]
    np_runs: int
    np_runs_if_everything: int
    audit_head: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"pathway": self.pathway, "formula": self.formula,
                "conclusion": self.conclusion.as_dict(),
                "steps": [u.as_dict() for u in self.inquiry.updates],
                "withdrawn": self.inquiry.withdrawn,
                "executions": {k: dict(v) for k, v in self.executions.items()},
                "released": [dict(r) for r in self.released],
                "refused": list(self.refused), "snapshots": list(self.snapshots),
                "np_runs": self.np_runs, "np_runs_if_everything": self.np_runs_if_everything,
                "audit_head": self.audit_head, "provenance": dict(self.provenance),
                "trail": self.inquiry.as_dict()}


def run_pathway_inquiry(snapshots: Sequence[Any], *, pathway: str, root: str | Path,
                        formula: Any = None, params: Any = None, controls: int = 60,
                        rewirings: int = 60, state_dir: str | Path | None = None,
                        rule: Any = None,
                        replication_snapshots: Sequence[Any] = ()) -> PathwayInquiryResult:
    """Decide what the pathway signal means, recording the inquiry when ``state_dir`` is set.

    With ``state_dir`` the inquiry is written into PSH's world model and audit chain as it
    runs (``psh.scientist.WorldModelRecorder``), under a kernel at ``state_dir/psh``.
    """
    from psh.scientist import run_inquiry
    from ..analysis.network_pharmacology import Parameters
    from ..sources.herbs import GEGEN_QINLIAN
    formula = formula or GEGEN_QINLIAN
    params = params or Parameters()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    kernel = recorder = world = envelope = None
    if state_dir is not None:
        from psh import PSHConfig, TrustedKernel
        from psh.scientist import ScientificLedger, ScientificWorldModel, WorldModelRecorder
        kernel = TrustedKernel(PSHConfig(state_dir=Path(state_dir) / "psh").ensure_dirs())
        project = kernel.graph.project(f"inquiry: {formula.chinese} {pathway}")
        envelope = kernel.envelope(project_id=project.id)
        world = ScientificWorldModel(ScientificLedger(kernel, project.id))
        recorder = WorldModelRecorder(world, envelope, population=(
            f"in silico: measured activities of {formula.chinese}'s constituents"))
    try:
        inquiry = pathway_inquiry(pathway, formula, controls=controls, rewirings=rewirings,
                                  rule=rule, recorder=recorder)
        execute = _Executor(snapshots, formula, params, pathway, root, controls, rewirings,
                            replication_snapshots)
        conclusion = run_inquiry(inquiry, execute)
        if recorder is not None:
            recorder.close(inquiry, conclusion)
        released: list[Mapping[str, Any]] = []
        refused: list[str] = []
        if conclusion.accepted and conclusion.leader == "target":
            if execute.primary is None:
                refused.append("the assayed-background result the claim would cite did "
                               "not run")
            else:
                release = execute.primary.release
                released = [r for r in release.get("released", ())
                            if r.get("object") == pathway]
                refused += [str(r) for r in release.get("refused", ())
                            if pathway in str(r)]
                if not released:
                    refused.append("the pipeline's release check did not pass a claim on "
                                   f"{pathway}")
        else:
            refused.append(f"the inquiry did not accept 'target' ({conclusion.verdict.value}"
                           f", leader {conclusion.leader or 'none'}): no mechanism claim on "
                           f"{pathway} is released")
        everything = sum(int(a.cost) for a in inquiry.analyses)
        return PathwayInquiryResult(
            pathway=pathway, formula=formula.id, conclusion=conclusion, inquiry=inquiry,
            executions=execute.results, released=tuple(released), refused=tuple(refused),
            snapshots=tuple(s.snapshot_id for s in snapshots), np_runs=execute.np_runs,
            np_runs_if_everything=everything,
            audit_head=kernel.events.head_hash if kernel is not None else "",
            provenance={"governed_execution": False,
                        "execution": "in-process; the executor is the broker's port",
                        "recorded_in_world_model": recorder is not None,
                        "parameters": {k: v for k, v in vars(params).items()
                                       if not k.startswith("_")},
                        "controls": controls, "rewirings": rewirings,
                        "replication_snapshots": [s.snapshot_id for s in replication_snapshots]})
    finally:
        if kernel is not None:
            kernel.close()


def _pct(p: float) -> str:
    return f"{p:.3f}"


def render_markdown(result: PathwayInquiryResult) -> str:
    """The inquiry for a reader: the verdict, then how it was reached."""
    c = result.conclusion
    inquiry = result.inquiry
    lines = [f"# Inquiry: {c.question}", "",
             f"**Verdict: {c.verdict.value}.** {c.describe()}", ""]
    if c.leader:
        kind = c.claim_kind.value if c.claim_kind else "a finding about the analysis"
        lines += [f"- Strongest statement licensed: {kind}"
                  + (f", {c.licensing.value}" if c.licensing else "")
                  + f", stated at most {c.certainty.value}.",
                  f"- Mechanism claims released on {result.pathway}: {len(result.released)}"
                  + ("" if result.released else f" ({'; '.join(result.refused)})"), ""]
    lines += ["## Explanations and sealed predictions", "",
              "| Explanation | Kind | Prior | Posterior |", "| --- | --- | ---: | ---: |"]
    for e in inquiry.explanations:
        prior = (inquiry.trail[0]["body"]["belief"].get(e.id, 0.0))
        kind = e.claim_kind.value if e.claim_kind else e.role.value
        lines.append(f"| `{e.id}`: {e.proposition} | {kind} | {_pct(prior)} | "
                     f"{_pct(c.posterior.get(e.id, 0.0))} |")
    lines += ["", "## What the engine ran, and why", "",
              "| Step | Analysis | Purpose | Outcome | Bits expected | Bits gained | "
              "Leader after |", "| ---: | --- | --- | --- | ---: | ---: | --- |"]
    for u in inquiry.updates:
        leader = max((h for h in u.posterior if h != "catch-all"),
                     key=lambda h: u.posterior[h])
        lines.append(f"| {u.step} | `{u.analysis_id}` | {u.purpose.value}"
                     + (f" vs `{u.against}`" if u.against else "")
                     + f" | {u.outcome} | {u.expected_gain:.3f} | {u.information:.3f} | "
                     f"`{leader}` {_pct(u.posterior[leader])} |")
    skipped = [a.id for a in inquiry.analyses
               if not inquiry.observed(a.id) and a.id not in inquiry.withdrawn]
    if skipped:
        lines += ["", "Not run, because the stopping rule was met first or nothing they "
                  "could show was worth their cost: " + ", ".join(f"`{a}`" for a in skipped)
                  + "."]
    for a, why in inquiry.withdrawn.items():
        lines.append(f"Withdrawn: `{a}` ({why}).")
    lines += ["", f"Network-pharmacology runs: {result.np_runs}, against "
              f"{result.np_runs_if_everything} to run every analysis listed.", "",
              "## Limitations", ""]
    lines += [f"- {x}" for x in c.limitations]
    lines += ["- every analysis is in silico; the conclusion is about the measured "
              "activities in the snapshots, not about patients",
              f"- executed in-process (governed_execution: "
              f"{str(result.provenance.get('governed_execution')).lower()})", "",
              f"Trail head `{inquiry.head}`; snapshots: "
              + ", ".join(f"`{s}`" for s in result.snapshots) + "."]
    return "\n".join(lines) + "\n"
