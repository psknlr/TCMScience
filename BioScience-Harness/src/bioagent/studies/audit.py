"""Rigor checks that run on every study, before and after analysis.

Each check returns ``Finding``s with a severity:

* ``stop``: the analysis as specified would give a wrong answer (cells counted as
  patients, batch identical to condition, the validation set used to select features,
  a 1,000-gene panel treated as a transcriptome). It does not run.
* ``downgrade``: it may run, but its conclusions are capped (exploratory, association).
* ``warn``: recorded in ``limitations.md``.

The checks are the ones that most often make a published omics finding unreproducible:
independent units, batch–condition confounding, leakage between discovery and
validation, the same original study counted twice, measured feature coverage, whether a
real outcome exists, prespecified test families, sufficient units, pairing, and two
formulas of the same name with different composition pooled together.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .contract import (CLAIM_LEVELS, ClaimRecord, CohortManifest, ContrastSpec,
                       OmicsArtifact, StudySpec, ValidationPlan)

__all__ = ["Finding", "AuditReport", "SEVERITIES", "check_independent_units",
           "check_identity_consistency", "check_batch_confounding", "check_leakage",
           "check_duplicates", "near_duplicate_profiles", "check_coverage",
           "check_outcome", "check_test_families", "check_sufficiency", "check_pairing",
           "pairing_consistency", "check_intervention_identity", "audit_study",
           "apply_audit"]

SEVERITIES = ("warn", "downgrade", "stop")


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity, "message": self.message,
                "evidence": dict(self.evidence)}


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        sev = {f.severity for f in self.findings}
        return "stop" if "stop" in sev else "downgrade" if "downgrade" in sev else "proceed"

    def by_check(self, name: str) -> list[Finding]:
        return [f for f in self.findings if f.check == name]

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "checks_run": self.checks_run,
                "findings": [f.as_dict() for f in self.findings]}

    def to_markdown(self) -> str:
        lines = [f"Audit verdict: **{self.verdict}** ({len(self.checks_run)} checks)", ""]
        if not self.findings:
            lines.append("No findings.")
        for f in sorted(self.findings, key=lambda f: -SEVERITIES.index(f.severity)):
            lines.append(f"- [{f.severity}] {f.check}: {f.message}")
        return "\n".join(lines) + "\n"


def _f(check: str, severity: str, message: str, **evidence: Any) -> Finding:
    return Finding(check, severity, message, evidence)


# 1. Independent units ----------------------------------------------------------------

def check_independent_units(manifest: CohortManifest, contrasts: Sequence[ContrastSpec],
                            artifacts: Sequence[OmicsArtifact] = (), *,
                            pooled_datasets: Sequence[str] = ()) -> list[Finding]:
    out = []
    missing = [s.sample_id for s in manifest if not s.subject_id]
    if missing:
        out.append(_f("independent_units", "stop",
                      f"{len(missing)} samples have no subject: independent units cannot "
                      "be identified", samples=missing[:10]))
    for c in contrasts:
        if c.unit in ("cell", "cell_id", "spot", "barcode", "sample_id") and \
                any(a.level in ("cell", "spot") for a in artifacts):
            out.append(_f("independent_units", "stop",
                          f"contrast {c.id} treats {c.unit}s as independent units; cells "
                          "and spots from one person are not replicates — aggregate per "
                          "subject (pseudobulk) first", contrast=c.id))
        elif c.unit == "sample_id":
            per = Counter(s.subject_id for s in manifest)
            multi = {k: v for k, v in per.items() if v > 1}
            if multi:
                out.append(_f("independent_units", "downgrade",
                              f"contrast {c.id} counts samples, and {len(multi)} subjects "
                              "contribute several samples", contrast=c.id,
                              subjects=sorted(multi)[:10]))
    for s in manifest:
        if s.dataset in pooled_datasets and s.subject_id == s.sample_id:
            out.append(_f("independent_units", "stop",
                          f"{s.dataset} libraries pool several people; a library is not a "
                          "subject — demultiplex (genotype or hashtag) before any "
                          "subject-level analysis", dataset=s.dataset))
            break
    return out


def check_identity_consistency(manifest: CohortManifest, fields: Sequence[str]) -> list[Finding]:
    """Several fields that each name the subject must agree on every sample."""
    out = []
    for s in manifest:
        vals = {f: s.get(f) for f in fields if s.get(f)}
        if len(set(vals.values())) > 1:
            out.append(_f("identity_consistency", "stop",
                          f"{s.sample_id}: the subject is named differently by {vals}",
                          sample=s.sample_id, values=vals))
    return out


# 2. Batch–condition confounding ---------------------------------------------------------

def _cramers_v(table: np.ndarray) -> float:
    n = table.sum()
    if n == 0 or min(table.shape) < 2:
        return 0.0
    expected = table.sum(1, keepdims=True) * table.sum(0, keepdims=True) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.nansum((table - expected) ** 2 / np.where(expected > 0, expected, np.nan))
    return float(math.sqrt(chi2 / (n * (min(table.shape) - 1))))


def check_batch_confounding(manifest: CohortManifest, contrast: ContrastSpec, *,
                            batch_field: str = "batch", strong: float = 0.7) -> list[Finding]:
    sel = contrast.select(manifest)
    if any(not s.get(batch_field) for v in sel.values() for s in v):
        n = sum(1 for v in sel.values() for s in v if not s.get(batch_field))
        return [_f("batch_confounding", "warn",
                   f"{n} samples in {contrast.id} have no {batch_field}: confounding cannot "
                   "be checked", contrast=contrast.id)]
    # counted in independent units: twelve stools of one person are one person
    rows = sorted({(s.get(batch_field), lvl, s.get(contrast.unit) or s.sample_id)
                   for lvl, v in sel.items() for s in v})
    rows = [(b, lvl) for b, lvl, _ in rows]
    if not rows:
        return []
    batches = sorted({b for b, _ in rows})
    levels = [contrast.case, contrast.control]
    table = np.array([[sum(1 for b, lv in rows if b == bb and lv == ll) for ll in levels]
                      for bb in batches], float)
    if len(batches) == 1:
        return []
    mixed = [bb for bb, r in zip(batches, table) if (r > 0).all()]
    v = _cramers_v(table)
    ev = {"contrast": contrast.id, "table": {b: dict(zip(levels, map(int, r)))
                                             for b, r in zip(batches, table)},
          "cramers_v": round(v, 3)}
    if not mixed:
        return [_f("batch_confounding", "stop",
                   f"{batch_field} determines {contrast.variable} in {contrast.id}: no "
                   f"{batch_field} contains both levels, so condition and {batch_field} "
                   "effects cannot be separated", **ev)]
    if v >= strong:
        return [_f("batch_confounding", "downgrade",
                   f"{batch_field} and {contrast.variable} are strongly associated "
                   f"(Cramér's V {v:.2f}, counted in {contrast.unit}s); {len(mixed)} of "
                   f"{len(batches)} {batch_field} values contain both levels", **ev)]
    return []


# 3. Leakage ------------------------------------------------------------------------------

def check_leakage(manifest: CohortManifest, plan: ValidationPlan) -> list[Finding]:
    out = []
    unit = plan.split_unit
    disc = {s.get(unit) for s in manifest if s.dataset in plan.discovery or
            s.role == "discovery"}
    val = {s.get(unit) for s in manifest if s.dataset in plan.validation or
           s.role == "validation"}
    both = sorted((disc & val) - {""})
    if both:
        out.append(_f("leakage", "stop",
                      f"{len(both)} {unit}s appear in both discovery and validation: "
                      "validation is not independent", units=both[:10]))
    used = sorted(set(plan.selection_datasets) & set(plan.validation))
    if used:
        out.append(_f("leakage", "stop",
                      f"validation data {used} were used to select features or "
                      "thresholds; they can no longer validate them", datasets=used))
    if plan.validation and not plan.frozen_selection:
        out.append(_f("leakage", "downgrade",
                      "no frozen record of what discovery selected before validation was "
                      "opened; validation cannot be shown to be untouched"))
    return out


# 4. The same original study counted twice --------------------------------------------------

def check_duplicates(manifest: CohortManifest, plan: ValidationPlan | None = None, *,
                     source_field: str = "source_sample") -> list[Finding]:
    out = []
    by_study = defaultdict(set)
    for s in manifest:
        if s.original_study:
            by_study[s.original_study].add(s.dataset)
    if plan is not None:
        for study, ds in sorted(by_study.items()):
            if ds & set(plan.discovery) and ds & set(plan.validation):
                out.append(_f("duplicate_studies", "stop",
                              f"original study {study} supplies both discovery "
                              f"({sorted(ds & set(plan.discovery))}) and validation "
                              f"({sorted(ds & set(plan.validation))}): a re-deposit is not "
                              "a replication", study=study))
    src = defaultdict(list)
    for s in manifest:
        if s.get(source_field):
            src[s.get(source_field)].append(s)
    for key, ss in sorted(src.items()):
        ds = sorted({s.dataset for s in ss})
        if len(ds) > 1:
            out.append(_f("duplicate_studies", "stop",
                          f"the same source sample {key} appears in datasets {ds}",
                          source=key, samples=[s.sample_id for s in ss]))
    return out


def near_duplicate_profiles(matrix: np.ndarray, ids: Sequence[str], *,
                            threshold: float = 0.995) -> list[tuple[str, str, float]]:
    """Pairs of samples (rows) whose profiles correlate above ``threshold``.

    Technical replicates or one biopsy deposited twice look like this; independent
    biopsies of different people do not. Spearman would be safer for raw counts; rows are
    expected log-scale.
    """
    x = np.asarray(matrix, float)
    x = (x - x.mean(1, keepdims=True)) / (x.std(1, keepdims=True) + 1e-12)
    r = x @ x.T / x.shape[1]
    out = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if r[i, j] >= threshold:
                out.append((ids[i], ids[j], float(r[i, j])))
    return out


# 5. Measured coverage ------------------------------------------------------------------

def check_coverage(artifacts: Sequence[OmicsArtifact], analyses: Sequence[Mapping[str, Any]]
                   ) -> list[Finding]:
    """``analyses``: dicts with ``artifact``, and optionally ``background`` (a feature list or
    the string ``"genome"``), ``needs_measured`` (bool), ``claims`` (e.g.
    ``"transcriptome_wide"``)."""
    out = []
    by_id = {a.id: a for a in artifacts}
    for an in analyses:
        a = by_id.get(an.get("artifact", ""))
        if a is None:
            out.append(_f("coverage", "stop", f"analysis on unknown artifact "
                                              f"{an.get('artifact')!r}"))
            continue
        bg = an.get("background")
        if a.feature_space == "targeted_panel":
            panel = set(a.features)
            if bg == "genome" or (isinstance(bg, (list, tuple, set)) and panel and
                                  not set(bg) <= panel):
                out.append(_f("coverage", "stop",
                              f"{a.id} measures a {len(panel) or a.n_features}-feature panel "
                              "but the analysis uses a background beyond it; enrichment must "
                              "be judged against the measured panel", artifact=a.id))
            if an.get("claims") == "transcriptome_wide":
                out.append(_f("coverage", "stop",
                              f"{a.id} is a targeted panel; it cannot support a "
                              "transcriptome-wide claim (absent genes were not measured)",
                              artifact=a.id))
        if an.get("needs_measured") and not a.measured:
            out.append(_f("coverage", "stop",
                          f"{a.id} is {a.provenance}, not measured; it cannot stand in for a "
                          "measurement", artifact=a.id, provenance=a.provenance))
    return out


# 6. A real outcome exists ------------------------------------------------------------------

def check_outcome(manifest: CohortManifest, outcome: str, *,
                  derived_from: Sequence[str] = ()) -> list[Finding]:
    vals = [s.get(outcome) for s in manifest]
    observed = [v for v in vals if v not in ("", "NA", "nan", "None")]
    if not observed:
        return [_f("outcome", "stop", f"no sample records the outcome {outcome!r}")]
    if len(set(observed)) < 2:
        return [_f("outcome", "stop", f"the outcome {outcome!r} takes a single value "
                                      f"({observed[0]!r}); nothing to predict or compare")]
    out = []
    if len(observed) < len(vals):
        out.append(_f("outcome", "warn", f"{len(vals) - len(observed)} of {len(vals)} samples "
                                         f"lack {outcome!r}"))
    if derived_from:
        out.append(_f("outcome", "downgrade",
                      f"{outcome!r} is derived from {list(derived_from)}, which are also "
                      "predictors or part of the data: the outcome is not independent"))
    return out


# 7. Prespecified test families ---------------------------------------------------------------

def check_test_families(plan: ValidationPlan, tested: Sequence[str]) -> list[Finding]:
    stray = [c for c in tested if plan.family_of(c) is None]
    if stray:
        return [_f("test_family", "downgrade",
                   f"contrasts {stray} were tested but belong to no prespecified family: "
                   "their results are exploratory", contrasts=stray)]
    return []


# 8. Enough independent units --------------------------------------------------------------

def check_sufficiency(manifest: CohortManifest, contrast: ContrastSpec, *,
                      minimum: int = 5, floor: int = 3) -> list[Finding]:
    n = {lvl: len(u) for lvl, u in contrast.units(manifest).items()}
    low = min(n.values()) if n else 0
    if low < floor:
        return [_f("sufficiency", "stop",
                   f"{contrast.id}: {n} independent {contrast.unit}s per arm; below {floor} "
                   "the comparison cannot be estimated, whatever the number of cells",
                   contrast=contrast.id, per_arm=n)]
    if low < minimum:
        return [_f("sufficiency", "downgrade",
                   f"{contrast.id}: {n} {contrast.unit}s per arm, fewer than {minimum}; "
                   "report estimates, not confirmatory tests", contrast=contrast.id,
                   per_arm=n)]
    return []


# 9. Pairing ---------------------------------------------------------------------------

def check_pairing(manifest: CohortManifest, contrast: ContrastSpec, *,
                  minimum: int = 3) -> list[Finding]:
    if not contrast.paired:
        return []
    sel = contrast.select(manifest)
    per = defaultdict(lambda: defaultdict(list))
    for lvl, ss in sel.items():
        for s in ss:
            per[s.get(contrast.unit)][lvl].append(s.sample_id)
    complete = [u for u, d in per.items() if len(d) == 2]
    dup = [u for u, d in per.items() if any(len(v) > 1 for v in d.values())]
    out = []
    if len(complete) < minimum:
        out.append(_f("pairing", "stop",
                      f"{contrast.id} is paired but only {len(complete)} {contrast.unit}s "
                      "have both levels", contrast=contrast.id))
    elif len(complete) < len(per):
        out.append(_f("pairing", "warn",
                      f"{len(per) - len(complete)} {contrast.unit}s lack one level and drop "
                      "out of the paired contrast", contrast=contrast.id))
    if dup:
        out.append(_f("pairing", "warn",
                      f"{len(dup)} {contrast.unit}s have several samples at one level; they "
                      "are averaged within level, not counted as pairs", units=dup[:10]))
    return out


def pairing_consistency(identity: np.ndarray, sample_ids: Sequence[str],
                        manifest: CohortManifest, contrast: ContrastSpec, *,
                        n_perm: int = 2000, seed: int = 0) -> list[Finding]:
    """Do samples declared to come from one subject look like one person?

    ``identity`` holds features that identify a person and do not change with condition
    (sex-chromosome genes, genotype calls, HLA expression), one row per sample. Declared
    pairs should be more similar than random cross-level pairs. If they are not, the
    pairing is likely wrong (shuffled sample sheet) and a paired test is invalid.
    """
    idx = {s: i for i, s in enumerate(sample_ids)}
    x = np.asarray(identity, float)
    x = (x - x.mean(0)) / (x.std(0) + 1e-12)
    sel = contrast.select(manifest)
    a = {s.get(contrast.unit): s.sample_id for s in sel[contrast.case] if s.sample_id in idx}
    b = {s.get(contrast.unit): s.sample_id for s in sel[contrast.control] if s.sample_id in idx}
    units = sorted(set(a) & set(b))
    if len(units) < 3:
        return []
    ia = np.array([idx[a[u]] for u in units])
    ib = np.array([idx[b[u]] for u in units])

    def mean_dist(pa, pb):
        return float(np.mean(np.linalg.norm(x[pa] - x[pb], axis=1)))

    observed = mean_dist(ia, ib)
    rng = np.random.default_rng(seed)
    null = np.array([mean_dist(ia, rng.permutation(ib)) for _ in range(n_perm)])
    p = float((1 + np.sum(null <= observed)) / (n_perm + 1))
    ev = {"contrast": contrast.id, "paired_distance": round(observed, 4),
          "random_distance": round(float(null.mean()), 4), "p_value": round(p, 4),
          "pairs": len(units)}
    if p > 0.05:
        return [_f("pairing", "stop",
                   "declared pairs are no more alike on identity features than random "
                   "pairs: the sample-to-subject pairing is likely wrong", **ev)]
    return []


# 10. Same name, different composition ----------------------------------------------------

def check_intervention_identity(manifest: CohortManifest, *, name_field: str = "intervention",
                                fp_field: str = "intervention_fp") -> list[Finding]:
    by_name = defaultdict(set)
    for s in manifest:
        if s.get(name_field) and s.get(fp_field):
            by_name[s.get(name_field)].add(s.get(fp_field))
    return [_f("intervention_identity", "stop",
               f"{name!r} names {len(fps)} different compositions; samples of different "
               "interventions cannot be pooled under one name", intervention=name,
               fingerprints=sorted(fps))
            for name, fps in sorted(by_name.items()) if len(fps) > 1]


# The whole audit -------------------------------------------------------------------------

def audit_study(spec: StudySpec, manifest: CohortManifest, contrasts: Sequence[ContrastSpec],
                artifacts: Sequence[OmicsArtifact], plan: ValidationPlan, *,
                analyses: Sequence[Mapping[str, Any]] = (), tested: Sequence[str] = (),
                outcome: str | None = None, identity_fields: Sequence[str] = (),
                pooled_datasets: Sequence[str] = (), batch_field: str = "batch") -> AuditReport:
    rep = AuditReport()

    def run(name, findings):
        rep.checks_run.append(name)
        rep.findings.extend(findings)

    run("independent_units", check_independent_units(manifest, contrasts, artifacts,
                                                     pooled_datasets=pooled_datasets))
    if identity_fields:
        run("identity_consistency", check_identity_consistency(manifest, identity_fields))
    for c in contrasts:
        run(f"batch_confounding:{c.id}", check_batch_confounding(manifest, c,
                                                                 batch_field=batch_field))
        run(f"sufficiency:{c.id}", check_sufficiency(manifest, c,
                                                     minimum=plan.min_subjects_per_arm))
        run(f"pairing:{c.id}", check_pairing(manifest, c))
    run("leakage", check_leakage(manifest, plan))
    run("duplicate_studies", check_duplicates(manifest, plan))
    run("coverage", check_coverage(artifacts, analyses))
    if outcome:
        run("outcome", check_outcome(manifest, outcome))
    run("test_family", check_test_families(plan, list(tested) or [c.id for c in contrasts]))
    run("intervention_identity", check_intervention_identity(manifest))
    for o in spec.original:
        if o.pooled and not pooled_datasets:
            rep.findings.append(_f("independent_units", "warn",
                                   f"{o.accession} deposits pooled libraries; subjects must "
                                   "come from demultiplexed cell metadata"))
    return rep


def apply_audit(claim: ClaimRecord, report: AuditReport, spec: StudySpec | None = None
                ) -> ClaimRecord:
    """Cap a claim by the audit verdict and by the original design's ceiling."""
    if report.verdict == "stop":
        stops = "; ".join(f.message for f in report.findings if f.severity == "stop")
        claim = claim.cap("not_supported", f"audit stopped the analysis: {stops}")
    elif report.verdict == "downgrade":
        why = "; ".join(f.message for f in report.findings if f.severity == "downgrade")
        claim = claim.cap("exploratory", f"audit downgraded the analysis: {why}")
    if spec is not None:
        ceiling = spec.ceiling()
        if CLAIM_LEVELS.index(ceiling) < CLAIM_LEVELS.index(claim.level):
            claim = claim.cap(ceiling, "the source design is not randomised: treatment "
                                       "effects are associations")
    return claim
