#!/usr/bin/env python
"""葛根芩连汤 and berberine: exposure and whole-formula contrasts from published summaries.

    python scripts/study_gqd_berberine.py --tcmdb /path/to/tcmdb --out docs/studies

Re-analyses group summaries (mean, SD, n) transcribed from the published tables, and the
locally built ToxCast store, under a protocol locked in this file. Writes
gqd_berberine.json and gqd_berberine.md.

Sources (the numbers below are transcribed, with the table they come from):

* Pharmacokinetics: Wang et al., "A 42-Markers Pharmacokinetic Study Reveals Interactions
  of Berberine and Glycyrrhizic Acid in the Anti-diabetic Chinese Medicine Formula
  Gegen-Qinlian Decoction", Front. Pharmacol. 2018;9:622, doi:10.3389/fphar.2018.00622,
  Table 1. Male SD rats, n = 10 per group, single oral dose: GQD 18.9 g/kg; GQD without
  Gan-Cao 16.5 g/kg; Huang-Lian alone 3.54 g/kg (the Huang-Lian amount in the GQD dose).
* Efficacy comparison: Xu et al., "Antidiabetic Effects of Gegen Qinlian Decoction via the
  Gut Microbiota Are Attributable to Its Key Ingredient Berberine", Genomics Proteomics
  Bioinformatics 2020, PMC8377040. GK rats, n = 6 per group; GQD 22 g/kg (≈200 mg
  berberine) vs berberine 200 mg/kg; 12 weeks.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.studies.design import (AssayResult, ExposureRecord, InterventionSpec,  # noqa: E402
                                     Measurement, StudyProtocol)
from bioagent.studies.exposure import exposure_ratio  # noqa: E402
from bioagent.studies.stats import ratio_from_summary, welch_from_summary  # noqa: E402

BERBERINE = "inchikey:YBHILYKTIRIUTE-UHFFFAOYSA-N"
BERBERINE_MW = 336.36            # the cation, the species measured in plasma
COMPTOX_ID = "comptox:DTXSID8024602"   # berberine chloride in ToxCast
MARGIN = 1.25                    # equivalence: ratio within (0.80, 1.25)

#: Front. Pharmacol. 2018;9:622, Table 1 (mean ± SD, n = 10). AUC_last in h·ng/mL,
#: Cmax in ng/mL.
PK = {
    "berberine": {
        "GQD": {"auc": (389.12, 187.34), "cmax": (28.20, 17.45), "t_half": (11.42, 2.78)},
        "GQD-GC": {"auc": (148.97, 70.83), "cmax": (32.28, 13.20), "t_half": (7.09, 0.65)},
        "HL": {"auc": (197.98, 76.94), "cmax": (76.65, 27.89), "t_half": (9.30, 1.67)},
    },
}
N_PK = 10

PROTOCOL = StudyProtocol(
    title="葛根芩连汤 vs its berberine source: exposure and contrasts from published summaries",
    question=("Does the whole formula change berberine exposure relative to the same amount "
              "of Huang-Lian given alone, and is the berberine exposure reached in plasma "
              "compatible with the activities berberine shows in vitro?"),
    hypothesis=("The whole formula raises berberine AUC relative to Huang-Lian alone, and "
                "Gan-Cao contributes to that rise."),
    competing=("AUC_last differs because sampling windows and half-lives differ, not "
               "absorption",
               "summary statistics hide skewed concentration-time data (SDs near the means)",
               "Cmax and AUC move in opposite directions, so 'more exposure' depends on the "
               "metric",
               "in vitro activities at plasma-achievable concentrations are cytotoxicity or "
               "assay interference, not specific mechanisms"),
    primary_endpoint="berberine_auc_last_ratio_GQD_vs_HL",
    analysis={"contrast": "welch_from_summary", "ratio_interval": "delta_log_90",
              "equivalence_ratio": [0.8, 1.25], "alpha": 0.05,
              "exposure_site": "plasma", "free_fraction_unknown_range": [0.01, 1.0],
              "implausible_below": 0.1},
    decision_rules={
        "ratio_ci_above_1.25": "the formula raises berberine AUC relative to Huang-Lian "
                               "alone, in rats, at this dose",
        "ratio_ci_within_0.80_1.25": "berberine AUC is equivalent between formula and "
                                     "Huang-Lian alone",
        "otherwise": "no conclusion about a change in exposure",
        "exposure_implausible": "a plasma-mediated mechanism at that assay is not supported "
                                "at the measured exposure; a gut-lumen mechanism is not "
                                "addressed",
    },
    interventions=(
        InterventionSpec("formula", "葛根芩连汤 (GQD)", (
            ("tcm:herb.gegen", "君", "9.45", "g/kg", ""),
            ("tcm:herb.huangqin", "臣", "3.54", "g/kg", ""),
            ("tcm:herb.huanglian", "臣", "3.54", "g/kg", ""),
            ("tcm:herb.gancao", "佐使", "2.36", "g/kg", "")),
            source="doi:10.3389/fphar.2018.00622").fingerprint,
        InterventionSpec("preparation", "Huang-Lian decoction (HL)", (
            ("tcm:herb.huanglian", "", "3.54", "g/kg", ""),),
            source="doi:10.3389/fphar.2018.00622").fingerprint),
    data=("doi:10.3389/fphar.2018.00622#Table1", "PMC8377040", "tcmdb:comptox"),
).lock()

#: Honest record: the primary contrast was computed while the summary-statistics function
#: was being tested, before this protocol was locked. The study is therefore exploratory.
PROTOCOL = PROTOCOL.amend("the primary contrast (berberine AUC, GQD vs HL) was computed "
                          "during code testing before the protocol was locked")


def pk_contrasts() -> list[dict]:
    out = []
    for analyte, groups in PK.items():
        for a, b, why in (("GQD", "HL", "whole formula vs the same Huang-Lian dose alone"),
                          ("GQD", "GQD-GC", "whole formula vs formula without Gan-Cao"),
                          ("GQD-GC", "HL", "formula without Gan-Cao vs Huang-Lian alone")):
            for metric in ("auc", "cmax"):
                (ma, sa), (mb, sb) = groups[a][metric], groups[b][metric]
                diff = welch_from_summary(ma, sa, N_PK, mb, sb, N_PK)
                ratio = ratio_from_summary(ma, sa, N_PK, mb, sb, N_PK, level=0.90)
                lo, hi = ratio["ci"]
                verdict = ("increased" if lo > MARGIN else
                           "decreased" if hi < 1 / MARGIN else
                           "equivalent" if 1 / MARGIN < lo and hi < MARGIN else
                           "inconclusive")
                out.append({"analyte": analyte, "contrast": f"{a} vs {b}", "why": why,
                            "metric": "AUC_last (h·ng/mL)" if metric == "auc"
                            else "Cmax (ng/mL)",
                            "mean_a": ma, "mean_b": mb, "difference": diff.as_dict(),
                            "ratio": ratio, "verdict": verdict})
    return out


def toxcast_assays(tcmdb: Path) -> tuple[list[AssayResult], int]:
    conn = sqlite3.connect(tcmdb / "db" / "comptox.sqlite")
    rows = conn.execute("SELECT object_id, object_name, outcome, context FROM relations "
                        "WHERE subject_id = ?", (COMPTOX_ID,)).fetchall()
    assays, inactive = [], 0
    for oid, name, outcome, ctx in rows:
        c = json.loads(ctx or "{}")
        if outcome == "negative":
            inactive += 1
            continue
        if outcome != "positive" or c.get("measure") != "AC50" or c.get("value") in (None, ""):
            continue
        assays.append(AssayResult(BERBERINE, name or oid, "AC50",
                                  Measurement("measured", float(c["value"]), c.get("unit", "uM")),
                                  species=str(c.get("species", "")), system=c.get("cell", ""),
                                  flags=tuple(c.get("flags") or ()), study="ToxCast invitrodb"))
    return assays, inactive


def exposure_screen(assays: list[AssayResult]) -> dict:
    out = {}
    for group, vals in PK["berberine"].items():
        cmax = vals["cmax"][0]
        exp = ExposureRecord(BERBERINE, "rat", "plasma", None,
                             Measurement("measured", cmax, "ng/mL"),
                             study="doi:10.3389/fphar.2018.00622")
        res = [exposure_ratio(exp, a, mw=BERBERINE_MW,
                              unknown_free_fraction=(0.01, 1.0), implausible_below=0.1)
               for a in assays]
        verdicts: dict[str, int] = {}
        for r in res:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        open_ = sorted((r for r in res if r["verdict"] != "implausible"),
                       key=lambda r: -r["ratio"][1])
        out[group] = {"cmax_ng_ml": cmax,
                      "cmax_nM_total": round(cmax / BERBERINE_MW * 1e3, 1),
                      "verdicts": verdicts,
                      "not_ruled_out": [{"assay": r["target"], "ratio_range": r["ratio"],
                                         "potency_uM": round(r["potency_molar"] * 1e6, 4),
                                         "verdict": r["verdict"]} for r in open_]}
    return out


GPB_2020 = {
    "study": "PMC8377040 (Genomics Proteomics Bioinformatics 2020)",
    "design": "GK rats, n = 6 per group; GQD 22 g/kg (≈200 mg berberine) vs berberine "
              "200 mg/kg; 12 weeks; normal, diabetic and metformin groups",
    "public": ["GSA CRA001199: 16S rRNA gene sequencing (raw reads)",
               "GSA CRA001200: ileum RNA-seq (raw reads)",
               "Table S2/S3: differentially expressed genes and clusters"],
    "not_public": ["per-animal glucose, OGTT, insulin, HOMA-IR (figures only)",
                   "numerical group summaries of the efficacy endpoints"],
    "assessment": (
        "The paper's 'attributable to berberine' rests on non-significant differences "
        "between GQD and berberine (e.g. microbiota structure, adjusted P = 0.269) and on "
        "few DEGs between them. Under the contrast rules (studies.contrast) that is "
        "'inconclusive' unless an equivalence margin was set and met; with n = 6 per group "
        "only a large difference could have been detected. A re-analysis of the efficacy "
        "endpoints needs the per-animal values (request to the authors); the sequencing "
        "can be re-processed from GSA, which needs a 16S/RNA-seq pipeline and tens of GB."),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tcmdb", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    status = PROTOCOL.check(endpoint=PROTOCOL.primary_endpoint, analysis=dict(PROTOCOL.analysis))
    assays, inactive = toxcast_assays(Path(args.tcmdb))
    result = {"protocol": {"locked": PROTOCOL.locked, "status": status,
                           "amendments": list(PROTOCOL.amendments),
                           "hypothesis": PROTOCOL.hypothesis,
                           "competing": list(PROTOCOL.competing),
                           "decision_rules": dict(PROTOCOL.decision_rules)},
              "pk_contrasts": pk_contrasts(),
              "exposure": {"assays_active": len(assays), "assays_inactive": inactive,
                           "by_group": exposure_screen(assays)},
              "efficacy_study": GPB_2020}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gqd_berberine.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    lines = [f"Protocol {PROTOCOL.locked[:19]}…, status **{status}**.", "",
             "| Contrast | Metric | Means | Ratio (90% CI) | Verdict |",
             "| --- | --- | --- | --- | --- |"]
    for c in result["pk_contrasts"]:
        r = c["ratio"]
        lines.append(f"| {c['contrast']} | {c['metric']} | {c['mean_a']} vs {c['mean_b']} | "
                     f"{r['ratio']:.2f} ({r['ci'][0]:.2f}–{r['ci'][1]:.2f}) | {c['verdict']} |")
    lines += ["", f"ToxCast: {len(assays)} active AC50s and {inactive} inactive results for "
                  "berberine chloride.", "",
              "| Group | Plasma Cmax (total) | Implausible | Depends on unknowns | Plausible |",
              "| --- | --- | ---: | ---: | ---: |"]
    for g, e in result["exposure"]["by_group"].items():
        v = e["verdicts"]
        lines.append(f"| {g} | {e['cmax_ng_ml']} ng/mL ({e['cmax_nM_total']} nM) | "
                     f"{v.get('implausible', 0)} | {v.get('depends_on_unknowns', 0)} | "
                     f"{v.get('plausible', 0)} |")
    (out / "gqd_berberine.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
