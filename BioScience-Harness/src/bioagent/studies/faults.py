"""A benchmark that injects design defects into studies whose truth is known.

Each task is a small simulated single-cell study (subjects nested in arms, cells nested
in subjects, a between-subject random effect on every gene). The truth is either
*null* (no condition effect) or *positive* (a state change of one gene in one cell type).
A fault is then injected, or not:

=========================== ============================================== ==================
fault                       what is wrong                                   check that should
                                                                            catch it
=========================== ============================================== ==================
``cells_as_units``          cells of one patient split between training     independent_units
                            and test, and counted as replicates
``shuffled_pairing``        the pre/post sample sheet is shuffled           pairing
``batch_is_condition``      every case in one batch, every control in       batch_confounding
                            another, with a batch effect
``panel_as_transcriptome``  a 1,000-gene-style panel scored against a       coverage
                            genome background
``predicted_as_measured``   a predicted interaction used as a measured one  coverage
``merged_formulas``         two compositions sold under one name pooled     intervention_identity
``validation_in_selection`` validation data used to choose features         leakage
=========================== ============================================== ==================

A pipeline is any callable ``study -> {"claim": bool, "flags": [check names]}``. Two
are provided: ``naive_pipeline`` (cells as replicates, random splits, the declared
background, everything accepted) and ``gated_pipeline`` (design gate and rigor audit
first, then subject-level analysis). ``run_benchmark`` reports, per pipeline, the
false-positive rate on clean null tasks, power on clean positive tasks, spurious claims
on faulted null tasks, defect detection and false alarms on clean tasks. Another
pipeline (for example an LLM agent given the same files) can be passed in the same way.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Callable, Mapping

import numpy as np

from .audit import audit_study, pairing_consistency
from .contract import (CohortManifest, ContrastSpec, OmicsArtifact, OriginalDesign, Sample,
                       StudySpec, ValidationPlan)
from .gate import design_gate
from .singlecell import pseudobulk, state_test
from .stats import welch_difference
from .validation import auc, fit_logistic, grouped_cv

__all__ = ["FAULTS", "EXPECTED_CHECK", "make_study", "inject", "naive_pipeline",
           "gated_pipeline", "run_benchmark", "hypergeom_sf"]

FAULTS = ("cells_as_units", "shuffled_pairing", "batch_is_condition",
          "panel_as_transcriptome", "predicted_as_measured", "merged_formulas",
          "validation_in_selection")

EXPECTED_CHECK = {"cells_as_units": "independent_units", "shuffled_pairing": "pairing",
                  "batch_is_condition": "batch_confounding",
                  "panel_as_transcriptome": "coverage", "predicted_as_measured": "coverage",
                  "merged_formulas": "intervention_identity",
                  "validation_in_selection": "leakage"}

N_GENES = 30
TARGET = "g0"


def hypergeom_sf(k: int, M: int, n: int, N: int) -> float:
    """P(X >= k) for X ~ Hypergeometric(population M, successes n, draws N)."""
    def lchoose(a, b):
        return math.lgamma(a + 1) - math.lgamma(b + 1) - math.lgamma(a - b + 1)
    top = min(n, N)
    if k > top:
        return 0.0
    denom = lchoose(M, N)
    return float(min(1.0, sum(math.exp(lchoose(n, i) + lchoose(M - n, N - i) - denom)
                              for i in range(max(k, 0), top + 1))))


def make_study(truth: str = "null", *, seed: int = 0, n_per_arm: int = 6,
               cells_per_subject: int = 150, paired: bool = False, effect: float = 1.2,
               sd_subject: float = 0.5) -> dict:
    """A clean simulated study. ``truth``: ``null`` or ``positive``."""
    rng = np.random.default_rng(seed)
    genes = [f"g{i}" for i in range(N_GENES)]
    base = {"epi": rng.normal(1.0, 0.5, N_GENES), "imm": rng.normal(0.5, 0.5, N_GENES)}
    samples, counts, cell_sample, cell_type, identity, ident_ids = [], [], [], [], [], []
    arms = [("UC", "HC")] if not paired else [("post", "pre")]
    subjects = []
    if paired:
        for i in range(n_per_arm * 2):
            subjects.append((f"S{i}", None))
    else:
        for arm in arms[0]:
            for i in range(n_per_arm):
                subjects.append((f"{arm}{i}", arm))
    for j, (subj, arm) in enumerate(subjects):
        u = rng.normal(0, sd_subject, N_GENES)          # the person, on every gene
        ident = rng.normal(0, 1, 6)                     # identity features (sex, genotype)
        levels = ["pre", "post"] if paired else [arm]
        for lvl in levels:
            sid = f"{subj}_{lvl}" if paired else subj
            batch = str(j % 2)
            samples.append(Sample(sid, subj, "sim", condition=lvl, batch=batch,
                                  timepoint=lvl if paired else "",
                                  attrs={"intervention": "GQD", "intervention_fp": "fp4"}))
            identity.append(ident + rng.normal(0, 0.15, 6))
            ident_ids.append(sid)
            for c in range(cells_per_subject):
                t = "epi" if rng.random() < 0.5 else "imm"
                eta = base[t] + u + rng.normal(0, 0.3, N_GENES)
                if truth == "positive" and t == "epi" and lvl in ("UC", "post"):
                    eta[0] += effect
                counts.append(rng.poisson(np.exp(eta) * 2.0))
                cell_sample.append(sid)
                cell_type.append(t)
    manifest = CohortManifest(samples)
    case, ctrl = arms[0]
    contrast = ContrastSpec("c1", "condition", case, ctrl, paired=paired)
    spec = StudySpec("sim", "simulated", f"is {TARGET} changed in epithelial cells?",
                     "composition_vs_state", "disease", f"{TARGET} rises in epithelium",
                     f"no subject-level difference in epithelial {TARGET}",
                     ("composition change",), "c1",
                     original=(OriginalDesign("sim", "case_control"),))
    artifacts = [OmicsArtifact("cells", "scrna", level="cell", annotations=("cell_type",))]
    plan = ValidationPlan(discovery=("sim",), families={"primary": ("c1",)},
                          min_subjects_per_arm=5)
    return {"truth": truth, "fault": None, "task": "difference", "spec": spec,
            "manifest": manifest, "contrasts": [contrast], "artifacts": artifacts,
            "plan": plan, "counts": np.array(counts), "cell_sample": cell_sample,
            "cell_type": cell_type, "genes": genes, "identity": np.array(identity),
            "identity_ids": ident_ids, "analyses": [], "seed": seed, "paired": paired}


def inject(study: dict, fault: str, *, seed: int = 0) -> dict:
    s = dict(study)
    s["fault"] = fault
    rng = np.random.default_rng(seed + 991)
    if fault == "cells_as_units":
        s["task"] = "prediction"
        s["contrasts"] = [replace(c, unit="cell_id") for c in s["contrasts"]]
        s["plan"] = replace(s["plan"], split_unit="cell_id")
    elif fault == "shuffled_pairing":
        if not s["paired"]:
            raise ValueError("shuffled_pairing needs a paired study")
        man = s["manifest"]
        post = [x for x in man if x.condition == "post"]
        subj = rng.permutation([x.subject_id for x in post])
        relabel = {x.sample_id: sj for x, sj in zip(post, subj)}
        s["manifest"] = CohortManifest(replace(x, subject_id=relabel.get(x.sample_id,
                                                                         x.subject_id))
                                       for x in man)
    elif fault == "batch_is_condition":
        man = s["manifest"]
        s["manifest"] = CohortManifest(replace(x, batch="B" if x.condition in ("UC", "post")
                                               else "A") for x in man)
        # the batch shifts the target gene in every cell of batch B
        counts = s["counts"].copy()
        inb = np.array([x.startswith("UC") or x.endswith("_post") for x in s["cell_sample"]])
        counts[inb, 0] = rng.poisson(counts[inb, 0] * 3.0 + 1)
        s["counts"] = counts
    elif fault == "panel_as_transcriptome":
        s["task"] = "enrichment"
        s["artifacts"] = [replace(s["artifacts"][0], feature_space="targeted_panel",
                                  features=tuple(s["genes"]))]
        s["analyses"] = [{"artifact": "cells", "background": "genome"}]
    elif fault == "predicted_as_measured":
        s["task"] = "engagement"
        s["artifacts"] = s["artifacts"] + [OmicsArtifact("docking", "structure",
                                                         provenance="predicted",
                                                         feature_space="genome")]
        s["analyses"] = [{"artifact": "docking", "needs_measured": True}]
    elif fault == "merged_formulas":
        man = s["manifest"]
        s["manifest"] = CohortManifest(
            replace(x, attrs={**x.attrs, "intervention_fp": "fp7" if i % 2 else "fp4"})
            for i, x in enumerate(man))
    elif fault == "validation_in_selection":
        s["plan"] = replace(s["plan"], validation=("sim_val",),
                            selection_datasets=("sim", "sim_val"))
    else:
        raise ValueError(f"unknown fault {fault!r}")
    return s


# Pipelines -----------------------------------------------------------------------------

def _cell_log(study) -> np.ndarray:
    x = study["counts"].astype(float)
    return np.log1p(x / x.sum(1, keepdims=True) * 1e3)


def naive_pipeline(study: dict) -> dict:
    """What a quick analysis often does: cells as replicates, random splits, the declared
    background, every artifact accepted."""
    task = study["task"]
    lv = {x.sample_id: x.condition for x in study["manifest"]}
    case = study["contrasts"][0].case
    if task == "prediction":
        X = _cell_log(study)
        y = np.array([1.0 if lv[s] == case else 0.0 for s in study["cell_sample"]])
        rng = np.random.default_rng(study["seed"])
        idx = rng.permutation(len(y))
        test, train = idx[: len(y) // 5], idx[len(y) // 5:]
        mu, sd = X[train].mean(0), X[train].std(0) + 1e-9
        f = fit_logistic((X[train] - mu) / sd, y[train])
        a = auc(y[test], f((X[test] - mu) / sd))
        return {"claim": a >= 0.7, "flags": [], "auc": a}
    if task == "enrichment":
        # 'hits' are the panel genes detected; the declared background is the genome.
        panel = len(study["genes"])
        pathway_in_panel = panel // 2          # panels are built around their pathways
        p = hypergeom_sf(pathway_in_panel, 20000, 200, panel)
        return {"claim": p < 0.05, "flags": [], "p": p}
    if task == "engagement":
        return {"claim": True, "flags": [], "note": "a docking score read as binding"}
    X = _cell_log(study)
    epi = np.array([t == "epi" for t in study["cell_type"]])
    arm = np.array([lv[s] == case for s in study["cell_sample"]])
    w = welch_difference(X[epi & arm, 0], X[epi & ~arm, 0])
    return {"claim": w.p_value < 0.05 and w.estimate > 0, "flags": [], "p": w.p_value}


def gated_pipeline(study: dict) -> dict:
    """Design gate and rigor audit first; then the subject-level analysis."""
    spec, man, contrasts = study["spec"], study["manifest"], study["contrasts"]
    gate = design_gate(spec, man, contrasts, study["artifacts"])
    rep = audit_study(spec, man, contrasts, study["artifacts"], study["plan"],
                      analyses=study["analyses"])
    flags = sorted({f.check for f in rep.findings if f.severity == "stop"})
    c = contrasts[0]
    if c.paired:
        flags += [f.check for f in pairing_consistency(study["identity"],
                                                       study["identity_ids"], man, c)]
    flags = sorted(set(flags))
    if not gate.answerable or flags:
        return {"claim": False, "flags": flags, "gate": gate.status}
    if study["task"] == "prediction":
        pb = pseudobulk(study["counts"], study["cell_sample"], study["cell_type"],
                        study["genes"], min_cells=5)
        rows = [i for i, (_, t) in enumerate(pb.keys) if t == "epi"]
        X = np.log1p(pb.counts[rows] / pb.counts[rows].sum(1, keepdims=True) * 1e6)
        lv = {x.sample_id: (x.subject_id, x.condition) for x in man}
        y = [1.0 if lv[pb.keys[i][0]][1] == c.case else 0.0 for i in rows]
        g = [lv[pb.keys[i][0]][0] for i in rows]
        r = grouped_cv(X, y, g, k=3, n_perm=50, seed=study["seed"])
        return {"claim": r["p_perm"] < 0.05, "flags": [], "auc": r["estimate"]}
    pb = pseudobulk(study["counts"], study["cell_sample"], study["cell_type"],
                    study["genes"], min_cells=5)
    r = state_test(pb, man, c, "epi", genes=[TARGET], min_cells=5)
    row = r["genes"][0]
    return {"claim": row["p_value"] < 0.05 and row["log2_difference"] > 0, "flags": [],
            "p": row["p_value"]}


def run_benchmark(pipelines: Mapping[str, Callable[[dict], dict]], *, n_rep: int = 20,
                  seed: int = 0, faults=FAULTS) -> dict:
    """Clean null, clean positive, a clean paired null (false alarms only), and each
    fault on null data, ``n_rep`` times each."""
    res = {name: {"null_claims": 0, "positive_claims": 0, "clean_false_alarms": 0,
                  "clean_runs": 0, "faults": {f: {"detected": 0, "spurious_claims": 0,
                                                  "runs": 0} for f in faults}}
           for name in pipelines}
    for r in range(n_rep):
        sd = seed + 1000 * r
        clean = {"null": make_study("null", seed=sd),
                 "positive": make_study("positive", seed=sd + 1),
                 "paired_null": make_study("null", seed=sd + 3, paired=True)}
        for truth, st in clean.items():
            for name, pipe in pipelines.items():
                out = pipe(st)
                if truth != "paired_null":
                    res[name][f"{truth}_claims"] += bool(out["claim"])
                res[name]["clean_false_alarms"] += bool(out["flags"])
                res[name]["clean_runs"] += 1
        for f in faults:
            base = make_study("null", seed=sd + 2, paired=(f == "shuffled_pairing"))
            st = inject(base, f, seed=sd)
            for name, pipe in pipelines.items():
                out = pipe(st)
                cell = res[name]["faults"][f]
                cell["runs"] += 1
                cell["detected"] += EXPECTED_CHECK[f] in out["flags"]
                cell["spurious_claims"] += bool(out["claim"])
    for name, d in res.items():
        d["false_positive_rate"] = d["null_claims"] / n_rep
        d["power"] = d["positive_claims"] / n_rep
        d["false_alarm_rate"] = d["clean_false_alarms"] / d["clean_runs"]
        runs = sum(v["runs"] for v in d["faults"].values())
        d["detection_rate"] = sum(v["detected"] for v in d["faults"].values()) / runs
        d["spurious_claim_rate_under_faults"] = sum(
            v["spurious_claims"] for v in d["faults"].values()) / runs
    return {"n_rep": n_rep, "seed": seed, "pipelines": res}

