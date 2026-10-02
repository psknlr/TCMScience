#!/usr/bin/env python
"""Audit of the HMP2 / IBDMDB longitudinal multi-omics cohort before any analysis.

    python scripts/audit_hmp2.py --metadata /path/hmp2_metadata_2018-08-20.csv --out docs/studies

Input: the public sample metadata (one row per data product), linked from
https://ibdmdb.org/results (Lloyd-Price et al., Nature 2019). No measurement data are
read: the question is what the cohort can answer, for which units, and where a naive
analysis would go wrong.

The audit answers, for plan C (metagenome → metatranscriptome → metabolome over time):

* how many independent people stand behind the samples, per diagnosis;
* whether recruitment site and diagnosis are confounded;
* how many stool collections carry DNA and RNA (and metabolites) from the same stool;
* whether "disease activity" is one outcome across diagnoses;
* how many consecutive-visit pairs fall in prespecified windows, for next-visit prediction.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.studies.audit import check_batch_confounding, check_outcome  # noqa: E402
from bioagent.studies.contract import (CohortManifest, ContrastSpec, OmicsArtifact,  # noqa: E402
                                       OriginalDesign, Sample, StudySpec)
from bioagent.studies.gate import design_gate  # noqa: E402
from bioagent.studies.longitudinal import match_layers, visit_pairs  # noqa: E402

LAYERS = {"metagenomics": "MGX", "metatranscriptomics": "MTX", "metabolomics": "MBX",
          "viromics": "VIR", "proteomics": "PRT"}
# Windows fixed before counting: weekly-to-monthly stool sampling in HMP2.
WINDOWS = {"MGX_next_visit": (7.0, 35.0), "MTX_next_visit": (7.0, 35.0)}

ORIGINAL = OriginalDesign(
    "HMP2/IBDMDB", "longitudinal_observational", randomized=False, sample_unit="stool",
    outcome_definition="none prespecified; activity by HBI (CD) or SCCAI (UC); fecal "
                       "calprotectin on a subset", n_subjects_reported=132,
    notes=("stool every ~2 weeks for up to a year; not every layer at every visit",
           "study week is used as the time axis (receipt dates are shipping dates)"))


def load(path: Path) -> CohortManifest:
    rows = list(csv.DictReader(open(path, encoding="latin-1")))
    samples = []
    for r in rows:
        layer = LAYERS.get(r["data_type"])
        if not layer:
            continue
        try:
            day = str(float(r["week_num"]) * 7)
        except ValueError:
            day = ""
        samples.append(Sample(
            f"{layer}:{r['External ID']}", r["Participant ID"], "HMP2",
            condition=r["diagnosis"], timepoint=r["visit_num"], batch=r["site_name"],
            original_study="HMP2",
            attrs={"data_type": layer, "collection": r["site_sub_coll"], "day": day,
                   "hbi": r["hbi"], "sccai": r["sccai"], "fecalcal": r["fecalcal"],
                   "antibiotics": r["Antibiotics"]}))
    return CohortManifest(samples)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "docs" / "studies")
    a = ap.parse_args()
    man = load(a.metadata)
    mgx = man.where(data_type="MGX")
    mtx = man.where(data_type="MTX")
    people = {d: len({s.subject_id for s in mgx if s.condition == d})
              for d in ("CD", "UC", "nonIBD")}
    per_person = Counter(s.subject_id for s in mgx)
    site = {}
    for case in ("CD", "UC"):
        c = ContrastSpec(f"{case}_vs_nonIBD", "condition", case, "nonIBD")
        site[c.id] = [f.as_dict() for f in check_batch_confounding(mgx, c, strong=0.3)]
        tab = Counter((s.batch, s.condition) for s in mgx if s.condition in (case, "nonIBD"))
        people_tab = Counter((s.batch, s.condition) for s in
                             {x.subject_id: x for x in mgx
                              if x.condition in (case, "nonIBD")}.values())
        site[c.id + "_samples"] = {f"{b}/{d}": n for (b, d), n in sorted(tab.items())}
        site[c.id + "_people"] = {f"{b}/{d}": n for (b, d), n in sorted(people_tab.items())}
    layers = {"MGX+MTX": match_layers(man, ["MGX", "MTX"], tolerance_days=14),
              "MGX+MTX+MBX": match_layers(man, ["MGX", "MTX", "MBX"], tolerance_days=14)}
    activity = {
        "CD_samples_with_HBI": sum(1 for s in mgx if s.condition == "CD" and s.get("hbi")),
        "CD_samples": sum(1 for s in mgx if s.condition == "CD"),
        "UC_samples_with_SCCAI": sum(1 for s in mgx if s.condition == "UC" and s.get("sccai")),
        "UC_samples": sum(1 for s in mgx if s.condition == "UC"),
        "nonIBD_with_any_index": sum(1 for s in mgx if s.condition == "nonIBD"
                                     and (s.get("hbi") or s.get("sccai"))),
        "samples_with_fecal_calprotectin": sum(1 for s in mgx if s.get("fecalcal")),
        "mgx_samples": len(mgx),
        "pooled_activity_outcome": [f.as_dict() for f in check_outcome(
            mgx, "hbi", derived_from=())] + [{
                "check": "outcome", "severity": "stop",
                "message": "HBI (CD) and SCCAI (UC) are different instruments; an 'active "
                           "vs inactive' outcome pooled across diagnoses mixes two scales, "
                           "and nonIBD participants have neither"}],
    }
    pairs = {name: visit_pairs(man.where(data_type=name.split("_")[0]), window=w)
             for name, w in WINDOWS.items()}
    pairs_summary = {k: {kk: v[kk] for kk in ("window", "subjects",
                                              "consecutive_gaps_outside_window",
                                              "undated_samples")} | {"pairs": len(v["pairs"])}
                     for k, v in pairs.items()}
    spec = StudySpec("hmp2_function", "Microbial function present, expressed, reflected",
                     "is a microbial pathway present (DNA), expressed (RNA) and reflected in "
                     "metabolites in the same stool, differently in IBD?",
                     "microbial_function", "disease",
                     "pathway expression per copy differs in IBD at matched abundance",
                     "no difference in RNA/DNA ratio at the person level",
                     ("abundance alone", "antibiotics", "site"), "CD_vs_nonIBD",
                     original=(ORIGINAL,))
    colls = sorted({s.get("collection") for s in man})
    arts = [OmicsArtifact("hmp2_mgx", "metagenome", samples=tuple(
                sorted({s.get("collection") for s in mgx}))),
            OmicsArtifact("hmp2_mtx", "metatranscriptome", samples=tuple(
                sorted({s.get("collection") for s in mtx}))),
            OmicsArtifact("hmp2_mbx", "metabolome")]
    gate = design_gate(spec, mgx, [ContrastSpec("CD_vs_nonIBD", "condition", "CD", "nonIBD")],
                       arts)
    result = {"cohort": "HMP2/IBDMDB", "metadata": a.metadata.name,
              "samples_by_layer": dict(Counter(s.get("data_type") for s in man)),
              "collections": len(colls),
              "people_with_metagenomes": people,
              "metagenomes_per_person": {"median": sorted(per_person.values())[
                  len(per_person) // 2], "max": max(per_person.values()),
                  "min": min(per_person.values())},
              "site_confounding": site, "layer_matching": layers, "activity": activity,
              "antibiotics_mgx_samples": sum(1 for s in mgx if s.get("antibiotics") == "Yes"),
              "visit_pairs": pairs_summary, "gate_microbial_function": gate.as_dict()}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "hmp2_audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False)
                                           + "\n")
    print(json.dumps(result, indent=1, ensure_ascii=False)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
