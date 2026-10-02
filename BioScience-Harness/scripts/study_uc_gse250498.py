#!/usr/bin/env python
"""Composition against state in UC mucosa, per patient (GEO GSE250498 / GSE250487).

    python scripts/study_uc_gse250498.py --h5ad /path/GSE250487_biopsy_RNA.h5ad --out docs/studies

Input: the processed biopsy object deposited with GSE250487 (part of SuperSeries
GSE250498, "Single-cell and spatial multi-omics highlight effects of anti-integrin therapy
across cellular compartments in ulcerative colitis"). Cells from ten pooled 10x libraries
were assigned to people by genotype (demuxlet); the object holds raw counts
(``layers/counts``), the person, the condition (HC, UC on vedolizumab ``UCV``, UC without
biologics ``UCNB``), the biopsy side and its endoscopic severity, and cell-type labels.
Reading needs ``h5py``; it is not a dependency of the harness.

The protocol below is fixed before the counts are read. Genes for the decomposition are
named in advance: epithelial and myeloid inflammation genes (LCN2, DUOX2, S100A8) for UC
against controls; the vedolizumab target integrin chains (ITGA4, ITGB7) for UC on
vedolizumab against UC without biologics.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.studies.audit import apply_audit, audit_study  # noqa: E402
from bioagent.studies.contract import (ClaimRecord, CohortManifest, ContrastSpec,  # noqa: E402
                                       OmicsArtifact, OriginalDesign, Sample, StudySpec,
                                       ValidationPlan)
from bioagent.studies.gate import design_gate  # noqa: E402
from bioagent.studies.power import simulate_power  # noqa: E402
from bioagent.studies.singlecell import (clr, composition_test, decompose_bulk,  # noqa: E402
                                         pseudobulk, state_test)
from bioagent.studies.stats import welch_difference  # noqa: E402

ACC = "GSE250498"
SEED = 20261002
GENES_UC = ("LCN2", "DUOX2", "S100A8")
GENES_VDZ = ("ITGA4", "ITGB7")
CELLTYPE = "coarse_annotations_MK"
STATE_TYPES = {"LCN2": "10-Epithelial", "DUOX2": "10-Epithelial", "S100A8": "08-MNP",
               "ITGA4": "03-CD4 T", "ITGB7": "03-CD4 T"}

ORIGINAL = OriginalDesign(
    ACC, "cross_sectional", randomized=False, sample_unit="pooled_library", pooled=True,
    outcome_definition="none (cross-sectional; endoscopic severity per biopsy)",
    n_subjects_reported=12,
    notes=("each GEO sample is one 10x lane of a pool holding biopsies of several people; "
           "lanes are technical replicates of the pool, not subjects",
           "people were assigned by genotype (demuxlet); doublets were removed upstream",
           "vedolizumab exposure was not randomised"))

SPEC = StudySpec(
    "uc_composition_state", "Composition and state in UC mucosa",
    "are bulk-level differences of UC mucosa from controls changes in which cells are "
    "present or in what the same cells express?", "composition_vs_state", "disease",
    "inflammation genes rise in UC through a change of state within cell types",
    "the composition term explains the difference of a prespecified gene (state interval "
    "includes 0 while the composition interval excludes it)",
    ("immune infiltration alone (composition)", "biopsy side and severity",
     "differential capture of fragile cell types (epithelium) between samples"),
    "uc_vs_hc", datasets={"GSE250487": "discovery"}, original=(ORIGINAL,))


def read_h5ad(path: Path, genes: tuple[str, ...]):
    import h5py
    f = h5py.File(path, "r")

    def obs(name):
        o = f["obs"][name]
        cats = [c.decode() if isinstance(c, bytes) else c for c in o["categories"][:]]
        return np.array([cats[c] for c in o["codes"][:]])

    meta = {k: obs(k) for k in ("patient_short", "condition", "Endoscopic_Severity_V1",
                                "colon_biopsy", "CoLabs_sample", "LIBRARY", CELLTYPE)}
    names = [x.decode() if isinstance(x, bytes) else x for x in f["var"]["_index"][:]]
    want = {g: names.index(g) for g in genes}
    g = f["layers"]["counts"]
    indptr = g["indptr"][:]
    indices = g["indices"][:]
    data = g["data"][:]
    n_cells = len(indptr) - 1
    out = np.zeros((n_cells, len(genes)), np.float32)
    rows = np.repeat(np.arange(n_cells), np.diff(indptr))
    for j, gene in enumerate(genes):
        hit = indices == want[gene]
        out[rows[hit], j] = data[hit]
    total = np.add.reduceat(data, indptr[:-1].clip(max=len(data) - 1)).astype(np.float64)
    total[np.diff(indptr) == 0] = 0
    return meta, out, total


def build_manifest(meta) -> CohortManifest:
    rows = {}
    for smp, pat, cond, sev, side in zip(meta["CoLabs_sample"], meta["patient_short"],
                                         meta["condition"], meta["Endoscopic_Severity_V1"],
                                         meta["colon_biopsy"]):
        rows.setdefault(smp, (pat, cond, sev, side))
    samples = []
    for smp, (pat, cond, sev, side) in sorted(rows.items()):
        samples.append(Sample(smp, f"GSE250487:{pat}", "GSE250487",
                              condition="HC" if cond == "HC" else "UC", batch="pool1",
                              tissue="colon", original_study=ACC,
                              attrs={"group": cond, "severity": sev, "side": side}))
    return CohortManifest(samples)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--h5ad", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "docs" / "studies")
    a = ap.parse_args()
    genes = GENES_UC + GENES_VDZ
    meta, expr_counts, total = read_h5ad(a.h5ad, genes)
    man = build_manifest(meta)
    cell_sample = meta["CoLabs_sample"]
    cell_type = meta[CELLTYPE]

    uc = ContrastSpec("uc_vs_hc", "condition", "UC", "HC", family="primary")
    vdz = ContrastSpec("ucv_vs_ucnb", "group", "UCV", "UCNB", family="secondary")
    plan = ValidationPlan(discovery=("GSE250487",),
                          families={"primary": ("uc_vs_hc",), "secondary": ("ucv_vs_ucnb",)},
                          min_subjects_per_arm=5)
    artifacts = [OmicsArtifact("gse250487_biopsy", "scrna", level="cell",
                               annotations=("cell_type",), protocol="10x 3' v3, pooled, "
                               "genotype-demultiplexed", accession="GSE250487")]
    gate = design_gate(SPEC, man, [uc, vdz], artifacts)
    audit = audit_study(SPEC, man, [uc, vdz], artifacts, plan)

    # the GEO view: one row per library lane, each lane holding all biopsies of the pool
    lanes = sorted(set(meta["LIBRARY"]))
    per_lane = {ln: len({p for p, L in zip(meta["patient_short"], meta["LIBRARY"]) if L == ln})
                for ln in lanes}
    geo_view = CohortManifest(Sample(ln, ln, "GSE250487", condition="pool") for ln in lanes)

    comp = {c.id: composition_test(cell_sample, cell_type, man, c, n_perm=20000, seed=SEED)
            for c in (uc, vdz)}

    # what treating cells as replicates would report for the same composition question
    lv = {s.sample_id: s.condition for s in man}
    naive = {}
    arm = np.array([lv[s] == "UC" for s in cell_sample])
    for t in sorted(set(cell_type)):
        x = (cell_type == t).astype(float)
        w = welch_difference(x[arm], x[~arm])
        naive[t] = {"difference_in_fraction": w.estimate, "p_cells_as_replicates": w.p_value}

    # power for a composition shift of the observed size, by subjects and cells
    clr_by_subject = defaultdict(lambda: Counter())
    subj_of = {s.sample_id: s.subject_id for s in man}
    for smp, t in zip(cell_sample, cell_type):
        clr_by_subject[subj_of[smp]][t] += 1
    types = sorted(set(cell_type))
    subjects = sorted(clr_by_subject)
    M = clr(np.array([[clr_by_subject[s][t] for t in types] for s in subjects], float))
    cond_of = {s.subject_id: s.condition for s in man}
    is_uc = np.array([cond_of[s] == "UC" for s in subjects])
    power = {}
    for t in ("01-Plasma cells", "10-Epithelial", "03-CD4 T"):
        j = types.index(t)
        sd = float(np.sqrt((M[is_uc, j].var(ddof=1) + M[~is_uc, j].var(ddof=1)) / 2))
        eff = float(M[is_uc, j].mean() - M[~is_uc, j].mean())
        power[t] = {"observed_clr_difference": eff, "between_subject_sd": sd,
                    "grid": [{k: v for k, v in simulate_power(n, m, effect=eff, sd_subject=sd,
                                                              sd_cell=1.0, n_sim=2000,
                                                              seed=SEED).items()
                              if k in ("n_per_arm", "cells_per_subject",
                                       "subject_level_rejection")}
                             for n in (4, 8, 16) for m in (500, 5000)]}

    # decomposition and within-type state for the prespecified genes
    norm = np.log1p(expr_counts / np.maximum(total, 1)[:, None] * 1e4)
    decomp, state = {}, {}
    for c, gl in ((uc, GENES_UC), (vdz, GENES_VDZ)):
        for gname in gl:
            j = genes.index(gname)
            decomp[f"{c.id}:{gname}"] = decompose_bulk(norm[:, j], cell_sample, cell_type, man,
                                                       c, n_boot=4000, seed=SEED)
    other = (total - expr_counts.sum(1))[:, None]
    pb = pseudobulk(np.hstack([expr_counts, other]), cell_sample, cell_type,
                    list(genes) + ["__other__"], min_cells=10)
    for c, gl in ((uc, GENES_UC), (vdz, GENES_VDZ)):
        for gname in gl:
            r = state_test(pb, man, c, STATE_TYPES[gname], genes=[gname], min_cells=20)
            state[f"{c.id}:{gname}@{STATE_TYPES[gname]}"] = {"units": r["units"],
                                                             **r["genes"][0]}

    claims = []
    for key, d in decomp.items():
        cid, gname = key.split(":")
        level = "association" if d["verdict"] != "unresolved" else "not_supported"
        rec = ClaimRecord(f"C-{gname}", f"{gname}: the {cid} difference is a "
                          f"{d['verdict']} effect", "disease", level, contrasts=(cid,),
                          evidence=("GSE250487",),
                          limitations=("4 people per arm", "cross-sectional"))
        rec = apply_audit(rec, audit, SPEC)
        claims.append(rec.as_dict())
    result = {"dataset": ACC, "seed": SEED,
              "cells": int(len(cell_sample)), "biopsies": len(man),
              "people": len(man.subjects()),
              "people_by_group": dict(Counter(s.get("group") for s in
                                              {x.subject_id: x for x in man}.values())),
              "geo_samples_are_lanes": {"lanes": len(lanes), "people_per_lane": per_lane},
              "geo_view_audit": [f.as_dict() for f in audit_study(
                  SPEC, geo_view, [ContrastSpec("x", "condition", "pool", "pool")], artifacts,
                  plan, pooled_datasets=("GSE250487",)).findings
                  if f.check == "independent_units"],
              "gate": gate.as_dict(), "audit": audit.as_dict(),
              "composition": comp, "composition_cells_as_replicates": naive,
              "power": power, "decomposition": decomp, "state": state, "claims": claims}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "uc_gse250498.json").write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                                        default=float) + "\n")
    brief = {"people_by_group": result["people_by_group"], "gate": gate.status,
             "gate_downgrades": gate.downgrades, "audit": audit.verdict,
             "composition_uc": [(r["cell_type"], round(r["clr_difference"], 2),
                                 round(r["p_perm"], 4), round(r["q_perm"], 3))
                                for r in comp["uc_vs_hc"]["cell_types"]],
             "composition_vdz": [(r["cell_type"], round(r["clr_difference"], 2),
                                  round(r["p_perm"], 4), round(r["q_perm"], 3))
                                 for r in comp["ucv_vs_ucnb"]["cell_types"]],
             "naive_significant": sum(v["p_cells_as_replicates"] < 0.05 for v in naive.values()),
             "decomp": {k: (v["verdict"], round(v["composition"]["estimate"], 3),
                            [round(x, 3) for x in v["composition"]["ci"]],
                            round(v["state"]["estimate"], 3),
                            [round(x, 3) for x in v["state"]["ci"]]) for k, v in decomp.items()},
             "state": {k: (round(v["log2_difference"], 2), round(v["p_value"], 4), v["units"])
                       for k, v in state.items()},
             "power": {t: [(g["n_per_arm"], g["cells_per_subject"],
                            g["subject_level_rejection"]) for g in v["grid"]]
                       for t, v in power.items()},
             "claims": [(c["id"], c["level"]) for c in claims]}
    print(json.dumps(brief, indent=1, ensure_ascii=False, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
