#!/usr/bin/env python3
"""Collect every number the figures and Table 1 show, from the code and results that hold it.

    python manuscript/code/extract_data.py            # rebuild data/extracted/
    python manuscript/code/extract_data.py --cases    # also re-run the end-to-end cases
    python manuscript/code/extract_data.py --verify   # also re-run the benchmarks' CI checks

Three kinds of input, kept apart:

* **the kernel's own tables**, imported from PSH (`psh.sir.values.LICENSING`,
  `MAX_CERTAINTY`, `psh.workflow.compiler._SUPPORTS`, the compiler's diagnostic registry),
  so a figure of the licensing table cannot disagree with the table the kernel enforces;
* **committed results** (`BioScience-Harness/benchmarks/ablation/results.json`,
  `.../evidence_typing/results.json`, `docs/studies/gqd_berberine.json`), and runs this
  script repeats because they are deterministic and fast: the three planted-world
  inquiries, replayed step by step, and, with `--cases`, the end-to-end cases;
* **curated values** (`data/curated/*.csv`) that exist only in the repository's
  documentation. Each row cites a file and line and quotes a fragment of it; the build
  stops if the fragment is no longer on that line.

Everything written to `data/extracted/` is deterministic: the inquiry's random
identifiers and the digests derived from them are dropped, so a rebuild on the same commit
gives byte-identical files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANUSCRIPT = HERE.parent
REPO = MANUSCRIPT.parent
BIO = REPO / "BioScience-Harness"
PSH = REPO / "PSH-Harness"
CURATED = MANUSCRIPT / "data" / "curated"
OUT = MANUSCRIPT / "data" / "extracted"

sys.path[:0] = [str(BIO / "src"), str(PSH / "src")]
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(BIO / "src"), str(PSH / "src")])}


# ----------------------------------------------------------------------------- helpers
def write_csv(name: str, rows: list[dict], fields: list[str] | None = None) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fields = fields or list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: _fmt(row.get(k)) for k in fields})
    return path


def write_json(name: str, value) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _fmt(v):
    if isinstance(v, float):
        return repr(round(v, 10))
    if isinstance(v, (list, tuple)):
        return ";".join(str(x) for x in v)
    return "" if v is None else v


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ----------------------------------------------------------------------- curated values
def verify_curated() -> int:
    """Every curated row's quote must still be on the line it cites."""
    checked = 0
    problems = []
    for path in sorted(CURATED.glob("*.csv")):
        for i, row in enumerate(read_csv(path), start=2):
            ref, quote = row.get("source", ""), row.get("quote", "")
            if not ref or not quote:
                problems.append(f"{path.name}:{i}: no source or quote")
                continue
            file, _, line = ref.rpartition(":")
            lines = (REPO / file).read_text(encoding="utf-8").splitlines()
            n = int(line)
            if n > len(lines) or quote not in lines[n - 1]:
                problems.append(f"{path.name}:{i}: {quote!r} is not on {ref}")
            checked += 1
    if problems:
        raise SystemExit("curated values no longer match their sources:\n  "
                         + "\n  ".join(problems))
    return checked


# --------------------------------------------------------------------- kernel's tables
def licensing() -> None:
    from psh.sir.values import LICENSING, MAX_CERTAINTY, ClaimKind, StudyDesign
    from psh.workflow.compiler import _SUPPORTS
    from psh.workflow.ir import EVIDENCE_DESIGNS, PREDICTIVE_DESIGNS
    rows = []
    for kind in ClaimKind:
        for design in StudyDesign:
            grade = LICENSING.get(kind, {}).get(design)
            rows.append({"claim_kind": kind.value, "design": design.value,
                         "grade": grade.value if grade else "unlicensed"})
    write_csv("licensing_matrix.csv", rows)
    write_csv("max_certainty.csv", [{"design": d.value,
                                     "max_certainty": MAX_CERTAINTY.get(d).value}
                                    for d in StudyDesign])
    rows = []
    for claim, designs in _SUPPORTS.items():
        for d in sorted(EVIDENCE_DESIGNS | PREDICTIVE_DESIGNS):
            rows.append({"claim_type": claim.value, "design": d,
                         "predictive": int(d in PREDICTIVE_DESIGNS),
                         "licensed": int(d in designs)})
    write_csv("compiler_supports.csv", rows)


def inventory() -> None:
    from bioagent.providers.public_apis import (CORE_SOURCES, SOURCES,
                                                SUPPLEMENT_SOURCES)
    from bioagent.providers.public_apis_ext import EXTENDED_SOURCES
    from bioagent.providers.public_apis_tcm import TCM_SOURCES
    from bioagent.tools import TOOLS
    from psh.compiler.diagnostics import REGISTRY
    from bioagent.contracts.candidate_claim import CLAIM_REASONS
    import collections

    verified = read_csv(BIO / "data" / "connector_live_verification.csv")
    dates = sorted(r["verified_at"][:10] for r in verified)
    domains = collections.Counter(getattr(t, "domain", None) for t in TOOLS)
    families = collections.Counter("".join(c for c in code if c.isalpha())
                                   for code in REGISTRY)
    tcm_tools = domains.get("tcm-knowledge", 0)
    value = {
        "public_sources": len(SOURCES),
        "public_sources_by_group": {"core": len(CORE_SOURCES),
                                    "extended": len(EXTENDED_SOURCES),
                                    "tcm": len(TCM_SOURCES),
                                    "supplement": len(SUPPLEMENT_SOURCES)},
        "typed_operations": sum(len(s.operations) for s in SOURCES),
        "operations_live_verified": sum(r["status"] == "SUCCEEDED" for r in verified),
        "operations_in_verification_file": len(verified),
        "sources_in_verification_file": len({r["source"] for r in verified}),
        "verification_dates": [dates[0], dates[-1]],
        "native_tools": len(TOOLS),
        "native_tool_domains": len(domains),
        "native_tools_by_domain": dict(sorted(domains.items())),
        "tcm_tools": tcm_tools,
        "compiler_diagnostics": len(REGISTRY),
        "compiler_diagnostics_by_family": dict(sorted(families.items())),
        "claim_reason_codes": len(CLAIM_REASONS),
        "claim_reasons": dict(CLAIM_REASONS),
    }
    write_json("inventory.json", value)


# ------------------------------------------------------------------- governance ablation
def ablation() -> None:
    res = json.loads((BIO / "benchmarks" / "ablation" / "results.json").read_text())
    rows = []
    for c in res["configurations"]:
        lo, hi = c["error_release_ci95"]
        rows.append({"configuration": c["configuration"], "gates": sorted(c["gates"]),
                     "n_gates": len(c["gates"]),
                     "errors_released": c["errors_released"], "mutants": c["mutants"],
                     "rate": c["error_release_rate"], "ci_low": lo, "ci_high": hi,
                     "false_refusals": c["false_refusals"], "base_cases": c["base_cases"]})
    write_csv("ablation_configurations.csv", rows)
    cols = ["provenance", "output", "licensing", "claim_contract", "release_path",
            "kernel", "domain", "full"]
    rows = [{"error_class": k, "n": v["n"], **{c: v[c] for c in cols}}
            for k, v in res["kill_matrix"].items()]
    write_csv("ablation_kill_matrix.csv", rows, ["error_class", "n", *cols])
    rows = [{"gate": g, "layer": res["gates"][g], "unique": len(ids), "mutants": ids}
            for g, ids in res["adds"].items()]
    write_csv("ablation_unique.csv", rows)
    rows = []
    for cid, c in res["cases"].items():
        refused = sorted(k for k in c["refused_by"] if "|" not in k)
        rows.append({"case": cid, "base": cid.split("/")[0], "error_class": c["error_class"],
                     "gold": c["gold"], "language": c["language"],
                     "refused_by": refused,
                     "refused_by_output_after_ingest": int("output|ingested" in c["refused_by"])})
    write_csv("ablation_cases.csv", rows)
    from bioagent.benchmarks.ablation_corpus import BASE_CASES
    write_csv("ablation_base_cases.csv", [
        {"case": c["id"], "language": c["language"], "claim_kind": c["claim_kind"],
         "source_design": c["source"]["design"], "statement": c["statement"]}
        for c in BASE_CASES])
    write_json("ablation_summary.json", {
        "base_cases": res["base_cases"], "mutants": res["mutants"],
        "error_classes": len(res["kill_matrix"]), "gates": res["gates"],
        "survivors": res["survivors"], "false_refusals": res["false_refusals"],
        "by_language": res["by_language"]})


# ---------------------------------------------------------------------- evidence typing
DESIGNS = ["systematic_review", "randomized_trial", "observational", "case_report",
           "animal", "in_vitro"]


def evidence_typing() -> None:
    root = BIO / "benchmarks" / "evidence_typing"
    after = json.loads((root / "results.json").read_text())
    before = after["baseline"]
    rows = []
    for version, res in (("before", before), ("after", after)):
        for split in ("dev", "test"):
            s = res["splits"][split]
            for design in DESIGNS:
                for metric in ("precision", "recall", "abstention", "wrong"):
                    m = s["designs"][design][metric]
                    rows.append({"version": version, "split": split, "design": design,
                                 "metric": metric, "k": m["k"], "n": m["n"],
                                 "rate": m["rate"], "ci_low": m["ci95"][0],
                                 "ci_high": m["ci95"][1]})
    write_csv("evidence_typing_designs.csv", rows)
    rows = []
    for version, res in (("before", before), ("after", after)):
        for split in ("dev", "test"):
            s = res["splits"][split]
            for metric in ("precision", "coverage"):
                m = s[metric]
                rows.append({"version": version, "split": split, "metric": metric,
                             "k": m["k"], "n": m["n"], "rate": m["rate"],
                             "ci_low": m["ci95"][0], "ci_high": m["ci95"][1]})
            neg = s["negatives_typed"]["all"]["typed"]
            rows.append({"version": version, "split": split, "metric": "negatives_typed",
                         "k": neg["k"], "n": neg["n"], "rate": neg["rate"],
                         "ci_low": neg["ci95"][0], "ci_high": neg["ci95"][1]})
            rows.append({"version": version, "split": split, "metric": "wrong_readings",
                         "k": s["wrong"], "n": "", "rate": "", "ci_low": "", "ci_high": ""})
    write_csv("evidence_typing_overall.csv", rows)
    t = after["splits"]["test"]
    cols = DESIGNS + ["ambiguous", "no rule"]
    rows = [{"reference": ref, **{c: t["confusion"][ref][c] for c in cols}}
            for ref in t["confusion"]]
    write_csv("evidence_typing_confusion_test.csv", rows, ["reference", *cols])
    rows = []
    for kind, v in t["negatives_typed"].items():
        for what in ("typed", "as_trial", "as_review"):
            m = v[what]
            rows.append({"negative": kind, "read_as": what, "k": m["k"], "n": m["n"],
                         "rate": m["rate"], "ci_low": m["ci95"][0], "ci_high": m["ci95"][1]})
    write_csv("evidence_typing_negatives_test.csv", rows)
    rows = []
    for field, v in t["fields"].items():
        m = v["read"]
        rows.append({"field": field, "filled": m["k"], "n": m["n"], "rate": m["rate"],
                     "ci_low": m["ci95"][0], "ci_high": m["ci95"][1],
                     "ambiguous": v["ambiguous"], "no_rule": v["no_rule"]})
    write_csv("evidence_typing_fields_test.csv", rows)
    manual = after["manual_check"]
    rows = []
    for field, v in manual["by_field"].items():
        rows.append({"field": field, **{k: v.get(k, 0) for k in ("correct", "partly", "wrong")}})
    write_csv("evidence_typing_manual_check.csv", rows)
    sampling = json.loads((root / "sampling.json").read_text())
    rows = [{"stratum": s["name"], "language": s["language"], "reference": s["kind"],
             "drawn": s["drawn"], "frame": s["frame_hits"], "examined": s["examined"]}
            for s in sampling["strata"]]
    write_csv("evidence_typing_sampling.csv", rows)
    write_json("evidence_typing_summary.json", {
        "records": sampling["records"], "licences": sampling["licences"],
        "frozen_on": sampling["frozen_on"],
        "test_records": t["records"], "test_labelled": t["labelled"],
        "test_negatives": t["negatives"], "dev_records": after["splits"]["dev"]["records"],
        "rules_after": after["rules_sha256"][:12], "rules_before": before["rules_sha256"][:12],
        "sample_sha256": after["sample_sha256"][:12],
        "manual_checked": manual["checked"],
        "manual_correct": {k: manual["correct"][k] for k in ("k", "n", "rate", "ci95")},
        "widening": {"n": t["widening"]["n"], "efficacy": t["widening"]["efficacy"]}})


# ---------------------------------------------------------------------------- inquiry
EXPLANATIONS = ["target", "coverage", "promiscuity", "catch-all"]


def inquiry() -> None:
    from psh.scientist.inquiry import Analysis, Explanation, Inquiry, StoppingRule
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(BIO / "scripts" / "run_inquiry.py"), "--planted",
                        "all", "--out", tmp], check=True, env=ENV, cwd=BIO,
                       stdout=subprocess.DEVNULL)
        worlds = {w: json.loads((Path(tmp) / w / "inquiry.json").read_text())
                  for w in ("selective", "coverage", "promiscuous")}
    steps, options, summary, predictions = [], [], [], []
    for world, d in worlds.items():
        trail = d["trail"]["trail"]
        opened = trail[0]["body"]
        analyses = {a["id"]: a for a in opened["analyses"]}
        inq = Inquiry(opened["question"],
                      [Explanation.from_dict(e) for e in opened["explanations"]],
                      [Analysis.from_dict(a) for a in opened["analyses"]],
                      StoppingRule.from_dict(opened["rule"]))
        point = 0

        def snapshot(chosen: str | None) -> None:
            for aid in analyses:
                gain = inq.expected_gain(aid)
                cost = analyses[aid]["cost"]
                options.append({"world": world, "decision": point, "analysis": aid,
                                "ready": int(inq.ready(aid)),
                                "expected_bits": gain, "cost": cost,
                                "bits_per_run": gain / cost,
                                "chosen": int(aid == chosen)})

        observed = [e for e in trail if e["event"] == "observed"]
        k = 0
        for entry in trail[1:]:
            if entry["event"] == "observed":
                snapshot(entry["body"]["analysis_id"])
                inq.observe(entry["body"]["analysis_id"], entry["body"]["outcome"],
                            evidence_ref=entry["body"].get("evidence_ref", ""),
                            result_digest=entry["body"].get("result_digest", ""))
                point += 1
                k += 1
            elif entry["event"] == "declared":
                inq.declare(entry["body"]["analysis_id"], entry["body"]["predictions"])
                if world == "selective":
                    for h, row in entry["body"]["predictions"].items():
                        for outcome, p in row.items():
                            predictions.append({"analysis": entry["body"]["analysis_id"],
                                                "explanation": h, "outcome": outcome,
                                                "p": p})
            else:
                raise SystemExit(f"unexpected trail event {entry['event']!r}")
        snapshot(None)                       # what was left when the engine stopped
        for name, value in inq.belief.items():
            if abs(value - d["conclusion"]["posterior"][name]) > 1e-12:
                raise SystemExit(f"{world}: replayed belief in {name} does not recompute")
        assert k == len(observed) == len(d["steps"])
        spent = 0.0
        prior0 = d["steps"][0]["prior"]
        steps.append({"world": world, "step": 0, "analysis": "", "outcome": "",
                      "cost": 0, "cumulative_runs": 0, "expected_bits": "",
                      "bits_gained": "", **{f"p_{h}": prior0[h] for h in EXPLANATIONS}})
        for s in d["steps"]:
            spent += s["cost"]
            steps.append({"world": world, "step": s["step"], "analysis": s["analysis_id"],
                          "outcome": s["outcome"], "cost": s["cost"],
                          "cumulative_runs": spent, "expected_bits": s["expected_gain"],
                          "bits_gained": s["information"],
                          **{f"p_{h}": s["posterior"][h] for h in EXPLANATIONS}})
        c = d["conclusion"]
        summary.append({"world": world, "verdict": c["verdict"], "leader": c["leader"],
                        "posterior": c["leader_posterior"], "claim_kind": c["claim_kind"],
                        "certainty": c["certainty"], "steps": c["steps"],
                        "np_runs": d["np_runs"], "np_runs_if_everything": d["np_runs_if_everything"],
                        "mechanism_claims_released": len(d["released"]),
                        "not_run": [a for a in analyses if a not in d["executions"]]})
    write_csv("inquiry_steps.csv", steps)
    write_csv("inquiry_options.csv", options)
    write_csv("inquiry_summary.csv", summary)
    write_csv("inquiry_predictions.csv", predictions)
    first = worlds["selective"]["trail"]["trail"][0]["body"]
    write_json("inquiry_design.json", {
        "analyses": [{"id": a["id"], "cost": a["cost"], "outcomes": a["outcomes"],
                      "design": a["design"]} for a in first["analyses"]],
        "explanations": [{"id": e["id"], "prior": e["prior"], "role": e["role"],
                          "claim_kind": e.get("claim_kind")} for e in first["explanations"]],
        "rule": first["rule"]})


# -------------------------------------------------------------------------- berberine
def berberine() -> None:
    d = json.loads((REPO / "docs" / "studies" / "gqd_berberine.json").read_text())
    rows = []
    for c in d["pk_contrasts"]:
        r = c["ratio"]
        rows.append({"contrast": c["contrast"], "metric": c["metric"], "mean_a": c["mean_a"],
                     "mean_b": c["mean_b"], "n_a": c["difference"]["n_a"],
                     "n_b": c["difference"]["n_b"], "ratio": r["ratio"],
                     "ci_low": r["ci"][0], "ci_high": r["ci"][1], "level": r["level"],
                     "verdict": c["verdict"]})
    write_csv("berberine_pk.csv", rows)
    rows = []
    for group, g in d["exposure"]["by_group"].items():
        for a in g["not_ruled_out"]:
            rows.append({"group": group, "cmax_nM_total": g["cmax_nM_total"],
                         "assay": a["assay"], "ac50_uM": a["potency_uM"],
                         "ratio_low": a["ratio_range"][0], "ratio_high": a["ratio_range"][1],
                         "verdict": a["verdict"]})
    write_csv("berberine_exposure.csv", rows)
    write_json("berberine_summary.json", {
        "protocol": d["protocol"]["locked"][:19], "status": d["protocol"]["status"],
        "assays_active": d["exposure"]["assays_active"],
        "assays_inactive": d["exposure"]["assays_inactive"],
        "groups": {k: {"cmax_ng_ml": v["cmax_ng_ml"], "cmax_nM_total": v["cmax_nM_total"],
                       "verdicts": v["verdicts"]}
                   for k, v in d["exposure"]["by_group"].items()}})


# ------------------------------------------------------------------- end-to-end cases
def cases(rerun: bool) -> None:
    path = OUT / "e2e_claims.csv"
    rows = []
    if rerun:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(BIO / "scripts" / "run_end_to_end_cases.py"),
                            "--out", tmp, "--only", "rnaseq", "--only", "literature"],
                           check=True, env=ENV, cwd=BIO, stdout=subprocess.DEVNULL)
            for case in ("rnaseq", "literature"):
                rep = json.loads((Path(tmp) / case / "report.json").read_text())
                versions = {s["name"]: s.get("version") or s.get("implementation", "")
                            for s in rep["steps"]}
                for i, c in enumerate(rep["claims"], start=1):
                    if c["allowed"] != c["expected"]:
                        raise SystemExit(f"{case} claim {i}: verdict differs from the case's")
                    rows.append({"case": case, "order": i, "text": c["text"],
                                 "kind": c["kind"], "allowed": int(c["allowed"]),
                                 "codes": c["codes"], "origin": "re-run here",
                                 "steps": "; ".join(f"{k}: {v}" for k, v in versions.items())})
    elif path.exists():
        rows = [r for r in read_csv(path) if r["case"] != "compound"]
    for r in read_csv(CURATED / "e2e_case3_claims.csv"):
        rows.append({"case": r["case"], "order": r["order"], "text": r["short"],
                     "kind": r["kind"], "allowed": r["allowed"],
                     "codes": r["codes"].split(";") if r["codes"] else [],
                     "origin": f"recorded 2026-10-07 ({r['source']})", "steps": ""})
    write_csv("e2e_claims.csv", rows)


# ------------------------------------------------------------------------------ verify
def verify_benchmarks() -> None:
    """The committed results these figures read must still be what the code produces."""
    for cmd in (["scripts/run_governance_ablation.py", "--check",
                 "benchmarks/ablation/results.json"],
                ["scripts/run_evidence_typing_benchmark.py", "--check",
                 "benchmarks/evidence_typing/results.json"]):
        subprocess.run([sys.executable, *cmd], check=True, env=ENV, cwd=BIO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", action="store_true",
                    help="re-run end-to-end cases 1 and 2 (needs GSEApy and PaperQA2)")
    ap.add_argument("--verify", action="store_true",
                    help="re-run the ablation and evidence-typing checks first")
    args = ap.parse_args(argv)
    n = verify_curated()
    print(f"curated: {n} values match the lines they cite")
    if args.verify:
        verify_benchmarks()
    licensing()
    inventory()
    ablation()
    evidence_typing()
    inquiry()
    berberine()
    cases(args.cases)
    print(f"wrote {len(list(OUT.iterdir()))} files to {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
