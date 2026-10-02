#!/usr/bin/env python
"""Ulcerative colitis under vedolizumab and infliximab (GEO GSE73661): audit, then two studies.

    python scripts/study_uc_gse73661.py --geo /path/to/tcmdb/geo --out docs/studies

Inputs (public GEO files, kept outside the repository):

* ``GSE73661_series_matrix.txt.gz``: Arijs et al., Gut 2018 (doi:10.1136/gutjnl-2016-
  312293): 178 colonic biopsies, RMA log2 on Affymetrix HuGene 1.0 ST (GPL6244); 44 UC
  patients on vedolizumab or placebo (GEMINI I / LTS) at W0, W6, W12, W52; 23 UC patients
  before and W4–6 after infliximab; 12 non-IBD control biopsies.
* ``GPL6244.annot.gz``: probe-set to gene symbol.

What it does, in order:

1. Builds the cohort manifest from the sample annotations and audits it (identity of the
   person behind each biopsy, repeated biopsies, outcome definition, processing series).
2. Study A, ``response_prediction``: does the W0 transcriptome predict endoscopic healing
   beyond W0 endoscopic severity and therapy? Subject-level cross-validation with a
   permutation null, against the same question answered with two common leaks.
3. Study B, ``inflammation_vs_repair``: does healed mucosa (responders after infliximab,
   Mayo endoscopic subscore 0–1) still differ from control mucosa, and are those
   differences part of the inflammation signature or not?

Both protocols are written in this file before the expression data are read.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.studies.audit import (Finding, apply_audit, audit_study, check_batch_confounding,  # noqa: E402
                                    check_identity_consistency, check_outcome)
from bioagent.studies.contract import (ClaimRecord, CohortManifest, ContrastSpec,  # noqa: E402
                                       OmicsArtifact, OriginalDesign, Sample, StudySpec,
                                       ValidationPlan)
from bioagent.studies.gate import design_gate  # noqa: E402
from bioagent.studies.stats import bh, welch_difference  # noqa: E402
from bioagent.studies.validation import grouped_cv, sample_split_cv, top_k_by_t  # noqa: E402
from bioagent.studies.workspace import create_workspace, write_claims  # noqa: E402

ACC = "GSE73661"
SEED = 20261002

ORIGINAL = OriginalDesign(
    ACC, "rct_substudy_and_open_label_cohort", randomized=False, sample_unit="biopsy",
    outcome_definition="endoscopic mucosal healing, Mayo endoscopic subscore 0 or 1; VDZ "
                       "assessed at W6/W12/W52, IFX at W4-6",
    timepoints=("W0", "W4_W6", "W6", "W12", "W52"), n_subjects_reported=79,
    notes=("vedolizumab patients come from randomised trials, but the biopsy substudy "
           "compares responders and non-responders, which is not a randomised contrast",
           "the infliximab cohort is open-label, before/after",
           "no hybridisation batch or scan date is deposited; the biobank numbers form "
           "three ranges: infliximab and 6 controls 69-207, 6 controls 607-616, "
           "vedolizumab and placebo 771-1473"))

STUDY_A = StudySpec(
    "uc_response_w0", "Baseline transcriptome and endoscopic healing",
    "does the W0 mucosal transcriptome predict endoscopic healing beyond W0 endoscopic "
    "severity and therapy?", "response_prediction", "disease",
    "W0 expression adds discrimination of later healing over severity and therapy",
    "subject-level cross-validated AUC of expression + severity + therapy does not exceed "
    "severity + therapy (the difference's permutation p ≥ 0.05, or AUC within 0.05)",
    ("baseline severity alone predicts healing", "therapy (IFX vs VDZ) differs in healing "
     "rate and in baseline expression", "collection period differs by therapy"),
    "responder_w0", datasets={ACC: "discovery"}, original=(ORIGINAL,),
    decision_rules={"adds": "expression predicts beyond severity in these trials; needs an "
                            "external cohort before any claim",
                    "does_not_add": "no evidence that baseline expression adds to "
                                    "severity and therapy"})

STUDY_B = StudySpec(
    "uc_residual_after_healing", "Residual mucosal change after infliximab healing",
    "does healed mucosa after infliximab differ from control mucosa, and are the "
    "differences part of the inflammation signature?", "inflammation_vs_repair", "disease",
    "healed mucosa retains differences from controls that are not explained by inflammation",
    "no gene differs between healed and control mucosa at q < 0.05 with |log2 FC| ≥ 1 in "
    "the direction opposite to active disease, against controls of the same collection "
    "period",
    ("residual inflammation below the endoscopic threshold", "processing batch",
     "biopsy site and patient age"),
    "healed_vs_control", datasets={ACC: "discovery"}, original=(ORIGINAL,))


# Changes made after the first run on these data. Each makes the affected analysis
# exploratory; they are written into the result rather than folded into the protocol.
AMENDMENTS = (
    {"study": "uc_residual_after_healing",
     "reason": "first run counted any residual gene whose active-disease difference was "
               "smaller or opposite as 'not explained by inflammation'; many were snoRNA "
               "and histone genes higher in both healed and active mucosa than in controls, "
               "pointing at the controls rather than at repair",
     "changed": ["three-way classification against the inflammation axis",
                 "sensitivity with controls of the same collection period only",
                 "falsifier restricted to genes opposite to active disease"]},
    {"study": "uc_response_w0",
     "reason": "therapy coincides with collection period; the first run stopped on it, "
               "but therapy is a covariate, not the contrast",
     "changed": ["therapy-by-period confounding recorded as a warning"]},
)


def parse_series(path: Path):
    meta, chars = {}, []
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.startswith("!series_matrix_table_begin"):
                break
            if line.startswith("!Sample_"):
                k, *v = line.rstrip("\n").split("\t")
                v = [x.strip('"') for x in v]
                if k == "!Sample_characteristics_ch1":
                    chars.append(v)
                else:
                    meta.setdefault(k, v)
        header = next(f).rstrip("\n").split("\t")
        ids, rows = [], []
        for line in f:
            if line.startswith("!series_matrix_table_end"):
                break
            p = line.rstrip("\n").split("\t")
            ids.append(p[0].strip('"'))
            rows.append(np.array(p[1:], dtype=np.float32))
    gsm = [h.strip('"') for h in header[1:]]
    assert gsm == meta["!Sample_geo_accession"], "column order differs from annotations"
    return meta, chars, ids, np.vstack(rows)


def symbols(path: Path) -> dict[str, str]:
    out = {}
    with gzip.open(path, "rt", encoding="latin-1") as f:
        for line in f:
            if line[0] in "#!^" or line.startswith("ID\t"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) > 2 and p[2]:
                out[p[0]] = p[2]
    return out


def _period(bp: int) -> str:
    """Biobank-number range: the only collection-period proxy GEO records (no scan dates)."""
    return "Bp<300" if bp < 300 else "Bp600s" if bp < 700 else "Bp>=700"


def build_manifest(meta, chars) -> CohortManifest:
    fields = {}
    for row in chars:
        key = row[0].split(": ", 1)[0]
        fields[key] = [x.split(": ", 1)[1] if ": " in x else "" for x in row]
    titles, sources = meta["!Sample_title"], meta["!Sample_source_name_ch1"]
    samples = []
    for i, gsm in enumerate(meta["!Sample_geo_accession"]):
        ind = fields["study individual number"][i]
        title = titles[i]
        title_subject = (re.findall(r"_(\d+)$", title) or [""])[0]
        if "Control" in title:
            title_subject = (re.findall(r"Control_(\d+)", title) or [""])[0]
        src_subject = (re.findall(r"(?:individual|patient|\(R\)|\(NR\)) (\d+)",
                                  sources[i]) or [""])[0]
        status = ("control" if "Control" in title else "active" if "Active" in title else
                  "R" if " R " in f" {title.split('_')[1]} " else
                  "NR" if " NR " in f" {title.split('_')[1]} " else "other")
        src_status = ("R" if "responder (R)" in sources[i] and "non-" not in sources[i]
                      else "NR" if "non-responder" in sources[i] else "")
        therapy = fields["induction therapy_maintenance therapy"][i]
        bp = int(re.match(r"B[pP](\d+)", title).group(1))
        mayo = fields["mayo endoscopic subscore"][i]
        samples.append(Sample(
            gsm, f"{ACC}:{ind}", ACC, condition="control" if therapy == "CO" else "UC",
            timepoint=fields["week (w)"][i], batch=_period(bp),
            tissue="colon", original_study=ACC,
            attrs={"title": title, "therapy": "IFX" if therapy == "IFX" else
                   "control" if therapy == "CO" else "VDZ" if therapy.startswith("vdz")
                   else "placebo", "therapy_arm": therapy,
                   "mayo_endoscopic": "" if mayo == "CO" else mayo, "status": status,
                   "source_status": src_status,
                   "subject_char": ind, "subject_title": title_subject,
                   "subject_source": src_subject}))
    return CohortManifest(samples)


def response_by_subject(man: CohortManifest) -> tuple[dict[str, str], list[str]]:
    """Prespecified: response at the first post-baseline biopsy labelled R or NR."""
    by = defaultdict(list)
    for s in man:
        if s.get("status") in ("R", "NR") and s.timepoint != "W0":
            wk = float(re.findall(r"\d+", s.timepoint)[0])
            by[s.subject_id].append((wk, s.get("status"), s.get("mayo_endoscopic")))
    resp, conflicts = {}, []
    for subj, v in by.items():
        v.sort()
        resp[subj] = v[0][1]
        healed = v[0][2] in ("0", "1")
        if (v[0][1] == "R") != healed:
            conflicts.append(f"{subj}: labelled {v[0][1]} with Mayo {v[0][2]}")
    return resp, conflicts


def study_a(man, X, probe_ids, resp):
    w0 = [s for s in man if s.timepoint == "W0" and s.subject_id in resp]
    mres = CohortManifest(Sample(s.sample_id, s.subject_id, s.dataset,
                                 condition=resp[s.subject_id], timepoint="W0",
                                 batch=s.batch, tissue=s.tissue,
                                 original_study=s.original_study, attrs=dict(s.attrs))
                          for s in w0)
    contrast = ContrastSpec("responder_w0", "condition", "R", "NR",
                            description="W0 biopsies of later responders vs non-responders")
    plan = ValidationPlan(discovery=(ACC,), families={"primary": ("responder_w0",)},
                          min_subjects_per_arm=5,
                          stop_rules=("fewer than 5 subjects in an arm",))
    artifacts = [OmicsArtifact("gse73661_rma", "bulk_rna", provenance="measured",
                               feature_space="whole_transcriptome", n_features=len(probe_ids),
                               protocol="Affymetrix HuGene 1.0 ST, RMA", accession=ACC)]
    gate = design_gate(STUDY_A, mres, [contrast], artifacts)
    audit = audit_study(STUDY_A, mres, [contrast], artifacts, plan, outcome="condition")
    for f in check_batch_confounding(mres, ContrastSpec("therapy_by_period", "therapy", "IFX",
                                                        "VDZ"), batch_field="batch"):
        # therapy is a covariate here, not the contrast: its effect cannot be told apart
        # from the collection period, which limits interpretation but not the R/NR test
        audit.findings.append(Finding(f.check, "warn", f.message + "; therapy is a "
                                      "covariate here, so it is recorded, not a stop",
                                      f.evidence))
    col = {s.sample_id: i for i, s in enumerate(man)}
    idx = [col[s.sample_id] for s in mres]
    y = np.array([1.0 if s.condition == "R" else 0.0 for s in mres])
    g = [s.subject_id for s in mres]
    sev = np.array([float(s.get("mayo_endoscopic")) for s in mres])
    ifx = np.array([1.0 if s.get("therapy") == "IFX" else 0.0 for s in mres])
    expr = X[:, idx].T
    k = 5
    covars = np.column_stack([sev, ifx])

    def selector_with_covars(kk):
        base = top_k_by_t(kk)

        def sel(Xtr, ytr):
            # the two covariates are the last columns and always kept
            p = Xtr.shape[1] - 2
            chosen = base(Xtr[:, :p], ytr)
            return np.r_[chosen, p, p + 1]
        return sel

    runs = {
        "severity_and_therapy": grouped_cv(covars, y, g, k=k, n_perm=200, seed=SEED),
        "expression_top50": grouped_cv(expr, y, g, k=k, select=top_k_by_t(50), n_perm=200,
                                       seed=SEED),
        "expression_top50_plus_severity_therapy": grouped_cv(
            np.column_stack([expr, covars]), y, g, k=k, select=selector_with_covars(50),
            n_perm=200, seed=SEED),
    }
    # The same question answered with two common leaks, for comparison only.
    uc = [s for s in man if s.subject_id in resp]
    idx_all = [col[s.sample_id] for s in uc]
    y_all = np.array([1.0 if resp[s.subject_id] == "R" else 0.0 for s in uc])
    g_all = [s.subject_id for s in uc]
    leaks = {
        "all_timepoints_sample_split": sample_split_cv(X[:, idx_all].T, y_all, k=k,
                                                       select=top_k_by_t(50), seed=SEED),
        "all_timepoints_subject_split": grouped_cv(X[:, idx_all].T, y_all, g_all, k=k,
                                                   select=top_k_by_t(50), n_perm=0,
                                                   seed=SEED),
    }
    leaks["all_timepoints_subject_split"]["leak"] = (
        "post-treatment biopsies are measured when the outcome is assessed: a healed "
        "biopsy predicts 'healed' because it is the outcome")
    leaks["all_timepoints_sample_split"]["leak"] = (
        "subject and outcome leakage: the same patient's biopsies fall in training and "
        "test, and post-treatment biopsies are the outcome")
    n = Counter(s.condition for s in mres)
    by_therapy = Counter((s.get("therapy"), s.condition) for s in mres)
    return {"gate": gate.as_dict(), "audit": audit.as_dict(), "subjects": dict(n),
            "by_therapy": {f"{a}/{b}": c for (a, b), c in sorted(by_therapy.items())},
            "honest": runs, "leaky_for_comparison": leaks,
            "samples_used": {"honest": len(mres), "leaky": len(uc)}}, mres, audit


def study_b(man, X, probe_ids, sym):
    # healed after IFX (first post-baseline biopsy, R, Mayo 0-1) vs control
    healed = [s for s in man if s.get("therapy") == "IFX" and s.get("status") == "R"
              and s.get("mayo_endoscopic") in ("0", "1")]
    ctrl = [s for s in man if s.condition == "control"]
    active = [s for s in man if s.get("therapy") == "IFX" and s.timepoint == "W0"]
    vdz_healed = [s for s in man if s.get("therapy") == "VDZ" and s.get("status") == "R"
                  and s.get("mayo_endoscopic") in ("0", "1")]
    group = {}
    for s in healed:
        group[s.sample_id] = "healed"
    for s in ctrl:
        group[s.sample_id] = "control"
    sub = CohortManifest(Sample(s.sample_id, s.subject_id, s.dataset,
                                condition=group[s.sample_id], timepoint=s.timepoint,
                                batch=s.batch, tissue=s.tissue,
                                original_study=s.original_study, attrs=dict(s.attrs))
                         for s in man if s.sample_id in group)
    contrast = ContrastSpec("healed_vs_control", "condition", "healed", "control")
    plan = ValidationPlan(discovery=(ACC,), families={"primary": ("healed_vs_control",)},
                          min_subjects_per_arm=5)
    artifacts = [OmicsArtifact("gse73661_rma", "bulk_rna", n_features=len(probe_ids),
                               protocol="Affymetrix HuGene 1.0 ST, RMA", accession=ACC)]
    gate = design_gate(STUDY_B, sub, [contrast], artifacts)
    audit = audit_study(STUDY_B, sub, [contrast], artifacts, plan)
    # the tempting alternative: pool vedolizumab healers against the same controls
    pooled = CohortManifest(Sample(s.sample_id, s.subject_id, s.dataset,
                                   condition="healed" if s in vdz_healed else "control",
                                   batch=s.batch) for s in vdz_healed + ctrl)
    vdz_check = check_batch_confounding(pooled, ContrastSpec("vdz_healed_vs_control",
                                                             "condition", "healed",
                                                             "control"))
    col = {s.sample_id: i for i, s in enumerate(man)}

    def per_subject(samples):
        by = defaultdict(list)
        for s in samples:
            by[s.subject_id].append(col[s.sample_id])
        return np.vstack([X[:, v].mean(1) for _, v in sorted(by.items())]), len(by)

    H, nh = per_subject(healed)
    C, nc = per_subject(ctrl)
    A, na = per_subject(active)
    same_period = [s for s in ctrl if s.batch == "Bp<300"]
    Cs, ncs = per_subject(same_period)
    keep = np.flatnonzero(np.array([p in sym for p in probe_ids]))

    def residual(Hm, Cm):
        rows = []
        for j in keep:
            w = welch_difference(Hm[:, j], Cm[:, j])
            rows.append((j, w.estimate, w.p_value))
        q = bh([r[2] for r in rows])
        return [(j, d, p, qq) for (j, d, p), qq in zip(rows, q) if qq < 0.05 and abs(d) >= 1]

    resid = residual(H, C)
    resid_same = residual(H, Cs)
    same_ids = {r[0] for r in resid_same}
    # where each residual gene sits relative to the inflammation axis (active W0 IFX
    # biopsies minus controls): still inflamed (same direction, smaller than in active
    # disease), beyond active disease (same direction, larger), or opposite to it
    infl = {j: welch_difference(A[:, j], C[:, j]).estimate for j, *_ in resid}
    cls = {}
    for j, d, p, qq in resid:
        a_ = infl[j]
        cls[j] = ("opposite_to_inflammation" if np.sign(a_) != np.sign(d) else
                  "partly_resolved_inflammation" if abs(a_) >= abs(d) else
                  "exceeds_active_disease")
    noncoding = re.compile(r"^(SNOR|SCARNA|RNU|RMRP|RPPH|HIST|MIR|SNHG)")

    def show(kind):
        rs = [r for r in resid if cls[r[0]] == kind]
        return [{"gene": sym[probe_ids[j]], "log2_healed_minus_control": round(float(d), 3),
                 "q": float(qq), "log2_active_minus_control": round(float(infl[j]), 3),
                 "survives_same_period_controls": j in same_ids}
                for j, d, p, qq in sorted(rs, key=lambda r: r[3])[:25]]

    counts = Counter(cls.values())
    nc_like = {k: sum(1 for r in resid if cls[r[0]] == k and noncoding.match(sym[probe_ids[r[0]]]))
               for k in counts}
    return {"gate": gate.as_dict(), "audit": audit.as_dict(),
            "vedolizumab_pooling_check": [f.as_dict() for f in vdz_check],
            "subjects": {"healed_ifx": nh, "control": nc, "control_same_period": ncs,
                         "active_ifx_w0": na},
            "genes_tested": len(keep),
            "residual_genes": len(resid),
            "residual_up": sum(1 for r in resid if r[1] > 0),
            "residual_down": sum(1 for r in resid if r[1] < 0),
            "residual_genes_same_period_controls": len(resid_same),
            "residual_surviving_same_period": len(same_ids & set(cls)),
            "classes": dict(counts), "noncoding_or_histone_by_class": nc_like,
            "survive_same_period_by_class": {k: sum(1 for j in cls if cls[j] == k and
                                                    j in same_ids) for k in counts},
            "top": {k: show(k) for k in ("opposite_to_inflammation",
                                         "exceeds_active_disease",
                                         "partly_resolved_inflammation")}}, sub, audit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geo", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "docs" / "studies")
    ap.add_argument("--workspace", type=Path, default=None,
                    help="also write the study directory (protocol, manifest, qc, claims)")
    a = ap.parse_args()
    meta, chars, probe_ids, X = parse_series(a.geo / f"{ACC}_series_matrix.txt.gz")
    sym = symbols(a.geo / "GPL6244.annot.gz")
    man = build_manifest(meta, chars)

    identity = check_identity_consistency(man, ("subject_char", "subject_title",
                                                "subject_source"))
    resp, label_conflicts = response_by_subject(man)
    status_conflicts = [s.sample_id for s in man if s.get("source_status") and
                        s.get("status") in ("R", "NR") and
                        s.get("source_status") != s.get("status")]
    per_subject = Counter(s.subject_id for s in man)
    cohort = {
        "samples": len(man), "subjects": len(per_subject),
        "biopsies_per_subject": dict(sorted(Counter(per_subject.values()).items())),
        "controls": {"samples": sum(1 for s in man if s.condition == "control"),
                     "subjects": len({s.subject_id for s in man if s.condition == "control"})},
        "by_therapy_subjects": dict(Counter(
            next(s.get("therapy") for s in man if s.subject_id == u) for u in per_subject)),
        "identity_conflicts": [f.as_dict() for f in identity],
        "response_label_vs_mayo_conflicts": label_conflicts,
        "response_label_title_vs_source_conflicts": status_conflicts,
        "outcome": [f.as_dict() for f in check_outcome(man, "status")],
        "responders": dict(Counter(resp.values())),
        "collection_period_by_therapy": {f"{b}/{t}": c for (b, t), c in sorted(Counter(
            (s.batch, s.get("therapy")) for s in man).items())},
    }
    a_res, a_man, a_audit = study_a(man, X, probe_ids, resp)
    b_res, b_man, b_audit = study_b(man, X, probe_ids, sym)

    honest = a_res["honest"]
    gain = honest["expression_top50_plus_severity_therapy"]["estimate"] - \
        honest["severity_and_therapy"]["estimate"]
    claim_a = ClaimRecord(
        "A1", "W0 expression adds to severity and therapy in predicting endoscopic healing",
        "disease", "association" if gain > 0.05 and
        honest["expression_top50_plus_severity_therapy"]["p_perm"] < 0.05 else
        "not_supported", contrasts=("responder_w0",), evidence=(ACC,),
        limitations=("single dataset, no external validation",
                     "therapy and collection period coincide"))
    claim_a = apply_audit(claim_a, a_audit, STUDY_A)
    if claim_a.level != "not_supported":
        claim_a = claim_a.cap("exploratory", "no external validation cohort")
    claim_b = ClaimRecord(
        "B1", "mucosa healed after infliximab differs from control mucosa in genes that move "
              "opposite to the inflammation signature (repair rather than residual "
              "inflammation)", "disease",
        "association" if b_res["survive_same_period_by_class"].get(
            "opposite_to_inflammation", 0) > 0 else "not_supported",
        contrasts=("healed_vs_control",), evidence=(ACC,),
        limitations=("8 healed patients", "controls are not age- or site-matched in the "
                     "deposited metadata", "Mayo 0-1 includes mild residual inflammation"))
    claim_b = apply_audit(claim_b, b_audit, STUDY_B)
    claim_b = claim_b.cap("exploratory", "the classification and the same-period "
                                         "sensitivity were amended after the first run")
    result = {"dataset": ACC, "seed": SEED, "amendments": list(AMENDMENTS), "cohort_audit": cohort, "study_a": a_res,
              "study_b": b_res, "claims": [claim_a.as_dict(), claim_b.as_dict()]}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "uc_gse73661.json").write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                                       default=float) + "\n")
    if a.workspace:
        plan = ValidationPlan(discovery=(ACC,), families={"primary": ("responder_w0",)})
        ws = create_workspace(a.workspace / STUDY_A.id, STUDY_A, a_man,
                              [ContrastSpec("responder_w0", "condition", "R", "NR")], plan,
                              [OmicsArtifact("gse73661_rma", "bulk_rna", accession=ACC)],
                              audit=a_audit, overwrite=True)
        write_claims(ws, [claim_a])
        (ws / "discovery" / "cv.json").write_text(json.dumps(a_res["honest"], indent=2))
        (ws / "sensitivity" / "leaky_splits.json").write_text(
            json.dumps(a_res["leaky_for_comparison"], indent=2))
        plan_b = ValidationPlan(discovery=(ACC,), families={"primary": ("healed_vs_control",)})
        ws = create_workspace(a.workspace / STUDY_B.id, STUDY_B, b_man,
                              [ContrastSpec("healed_vs_control", "condition", "healed",
                                            "control")], plan_b,
                              [OmicsArtifact("gse73661_rma", "bulk_rna", accession=ACC)],
                              audit=b_audit, overwrite=True)
        write_claims(ws, [claim_b])
        (ws / "discovery" / "residual_genes.json").write_text(
            json.dumps({k: v for k, v in b_res.items() if k not in ("gate", "audit")},
                       indent=2, ensure_ascii=False, default=float))
        (ws / "sensitivity" / "vedolizumab_pooling.json").write_text(
            json.dumps(b_res["vedolizumab_pooling_check"], indent=2, ensure_ascii=False))
    print(json.dumps({"cohort": {k: cohort[k] for k in ("samples", "subjects",
                                                       "biopsies_per_subject", "controls",
                                                       "responders")},
                      "identity_conflicts": len(identity),
                      "label_conflicts": label_conflicts,
                      "status_conflicts": status_conflicts,
                      "A": {k: {kk: v[kk] for kk in ("estimate", "p_perm") if kk in v}
                            for k, v in {**a_res["honest"],
                                         **a_res["leaky_for_comparison"]}.items()},
                      "A_subjects": a_res["subjects"], "A_by_therapy": a_res["by_therapy"],
                      "A_audit": a_res["audit"]["verdict"],
                      "B": {k: b_res[k] for k in ("subjects", "genes_tested", "residual_genes",
                                                  "residual_up", "residual_down",
                                                  "residual_genes_same_period_controls",
                                                  "residual_surviving_same_period",
                                                  "classes", "noncoding_or_histone_by_class",
                                                  "survive_same_period_by_class")},
                      "B_audit": b_res["audit"]["verdict"],
                      "B_vdz": [f["message"] for f in b_res["vedolizumab_pooling_check"]],
                      "claims": [(c.id, c.level) for c in (claim_a, claim_b)]},
                     indent=1, ensure_ascii=False, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
