"""The ADMET pipeline: molecules to properties, rules, alerts and predicted endpoints.

    from bioagent.admet.pipeline import AdmetConfig, run_admet
    run = run_admet("molecules.csv", AdmetConfig(), "out/")

For each molecule (standardised by ``chem.standardize``):

* physicochemical descriptors and drug-likeness rules (``rules.rules``);
* structural alerts (``rules.alerts``);
* when the models are built (``bioagent admet --build-models``), the 22 TDC endpoints,
  each with the model's held-out score and whether the molecule lies in its
  applicability domain.

Outputs: ``admet.tsv`` (one row per molecule), ``report.md`` / ``report.html`` and
``run.json`` with the model cards' digests.

What a result is: a prediction from a model trained on public benchmark data, with the
error its model card states on unseen scaffolds. Outside the applicability domain it is
unreliable. It ranks compounds for testing; it is not a measurement.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..omics import svgplot
from ..omics.rnaseq import _html, _sha256
from . import models as M
from .chem import ChemError, descriptors, need, standardize
from .rules import alerts, rules

__all__ = ["AdmetConfig", "AdmetRun", "read_molecules", "run_admet", "verify_run",
           "default_cache"]


def default_cache() -> Path:
    from ..config import data_lake_dir
    return Path(data_lake_dir()) / "admet"


@dataclass(frozen=True)
class AdmetConfig:
    cache_dir: str | None = None
    endpoints: tuple[str, ...] = ()          # empty: every built endpoint

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdmetRun:
    out_dir: Path
    molecules: list[dict[str, Any]]
    models_built: bool
    cards: dict[str, dict[str, Any]]
    failures: dict[str, str]
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)


def read_molecules(source: str | Path) -> list[tuple[str, str]]:
    from ..docking.prep import read_ligands
    try:
        return read_ligands(source)
    except Exception as exc:                                  # noqa: BLE001
        raise ChemError(str(exc)) from exc


def run_admet(source: str | Path | Sequence[tuple[str, str]], config: AdmetConfig,
              out_dir: str | Path) -> AdmetRun:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = read_molecules(source) if isinstance(source, (str, Path)) else list(source)
    cache = Path(config.cache_dir) if config.cache_dir else default_cache()
    modelset = M.load_models(cache)
    warnings: list[str] = []
    if modelset is None:
        warnings.append("the ADMET models are not built here, so only descriptors, rules and "
                        "alerts are reported; run `bioagent admet --build-models` once")
    endpoints = [e for e in (config.endpoints or tuple(M.ENDPOINTS))
                 if modelset is not None and e in modelset.cards]
    failures: dict[str, str] = {}
    mols, rows = [], []
    for name, smiles in entries:
        try:
            mol = standardize(smiles)
        except ChemError as exc:
            failures[name] = str(exc)
            continue
        Chem = need("rdkit.Chem")
        d = descriptors(mol)
        rows.append({"name": name, "input_smiles": smiles,
                     "smiles": Chem.MolToSmiles(mol), "descriptors": d, "rules": rules(mol),
                     "alerts": alerts(mol), "predictions": {}})
        mols.append(mol)
    if failures:
        warnings.append(f"{len(failures)} molecules could not be read: "
                        + "; ".join(f"{k}: {v}" for k, v in failures.items()))
    if not mols:
        endpoints = []
        if entries:
            warnings.append("no molecule could be read, so nothing was predicted")
    for e in endpoints:
        res = modelset.predict(mols, e)
        for i, row in enumerate(rows):
            row["predictions"][e] = {"value": float(res["value"][i]),
                                     "similarity": round(float(res["similarity"][i]), 3),
                                     "in_domain": bool(res["in_domain"][i])}
    cards = {e: modelset.cards[e] for e in endpoints} if modelset and endpoints else {}
    run = AdmetRun(out_dir=out, molecules=rows, models_built=modelset is not None,
                   cards=cards, failures=failures, warnings=warnings)
    _write(run, config, started, entries)
    return run


def _write(run: AdmetRun, config: AdmetConfig, started: str,
           entries: Sequence[tuple[str, str]]) -> None:
    out = run.out_dir
    endpoints = list(run.cards)
    header = ["name", "smiles", "MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors",
              "NumRotatableBonds", "qed", "lipinski_violations", "veber", "egan", "ghose",
              "alerts"]
    for e in endpoints:
        header += [e, f"{e}_in_domain"]
    with (out / "admet.tsv").open("w", encoding="utf-8") as fh:
        fh.write("\t".join(header) + "\n")
        for r in run.molecules:
            d, ru = r["descriptors"], r["rules"]
            vals = [r["name"], r["smiles"]] + [f"{d[k]:.4g}" for k in (
                "MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors",
                "NumRotatableBonds", "qed")]
            vals += [str(ru["lipinski"]["violations"]), str(ru["veber"]["pass"]),
                     str(ru["egan"]["pass"]), str(ru["ghose"]["pass"]),
                     "; ".join(a["alert"] for a in r["alerts"]) or "-"]
            for e in endpoints:
                p = r["predictions"][e]
                vals += [f"{p['value']:.4g}", str(p["in_domain"])]
            fh.write("\t".join(vals) + "\n")
    plots: dict[str, str] = {}
    cls = [e for e in endpoints if M.ENDPOINTS[e].task == "classification"]
    if cls and run.molecules:
        matrix = np.array([[r["predictions"][e]["value"] for e in cls] for r in run.molecules])
        plots["classification"] = svgplot.heatmap(
            matrix, [r["name"] for r in run.molecules], cls,
            title="Predicted probabilities (classification endpoints)",
            scale_label="probability")
    md = _markdown(run)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.html").write_text(_html(md, plots), encoding="utf-8")
    pdir = out / "plots"
    pdir.mkdir(exist_ok=True)
    for name, svg in plots.items():
        (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
    outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
               if p.is_file() and p.name != "run.json"}
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    manifest = {"pipeline": "bioagent.admet", "code_digest": code.hexdigest(),
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "python": platform.python_version(), "config": config.as_dict(),
                "molecules": [{"name": n, "smiles": s} for n, s in entries],
                "models": {e: {k: c[k] for k in ("model_sha256", "archive_sha256", "metric",
                                                  "test_score", "train", "test")}
                           for e, c in run.cards.items()},
                "outputs": outputs, "warnings": run.warnings}
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


def _fmt_prediction(e: str, p: dict[str, Any]) -> str:
    spec = M.ENDPOINTS[e]
    v = p["value"]
    text = f"{v:.2f}" if spec.task == "classification" else f"{v:.3g}"
    return text + ("" if p["in_domain"] else " (outside domain)")


def _markdown(run: AdmetRun) -> str:
    lines = ["# ADMET prediction", ""]
    if run.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in run.warnings] + [""]
    lines += ["## Properties and rules", "",
              "| molecule | MW | cLogP | TPSA | HBD | HBA | RB | QED | Lipinski viol. | Veber | "
              "Egan | alerts |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in run.molecules:
        d, ru = r["descriptors"], r["rules"]
        al = ", ".join(sorted({a["alert"] for a in r["alerts"]})) or "-"
        lines.append(f"| {r['name']} | {d['MolWt']:.1f} | {d['MolLogP']:.2f} | {d['TPSA']:.1f} | "
                     f"{d['NumHDonors']:.0f} | {d['NumHAcceptors']:.0f} | "
                     f"{d['NumRotatableBonds']:.0f} | {d['qed']:.2f} | "
                     f"{ru['lipinski']['violations']} | {'pass' if ru['veber']['pass'] else 'fail'} "
                     f"| {'pass' if ru['egan']['pass'] else 'fail'} | {al} |")
    lines.append("")
    if run.cards:
        groups: dict[str, list[str]] = {}
        for e in run.cards:
            groups.setdefault(M.ENDPOINTS[e].group, []).append(e)
        for g, es in groups.items():
            lines += [f"## {g.capitalize()}", "",
                      "| molecule | " + " | ".join(M.ENDPOINTS[e].meaning for e in es) + " |",
                      "|---" * (len(es) + 1) + "|"]
            for r in run.molecules:
                lines.append(f"| {r['name']} | " + " | ".join(
                    _fmt_prediction(e, r["predictions"][e]) for e in es) + " |")
            lines.append("")
            lines.append("Units: " + "; ".join(f"{M.ENDPOINTS[e].meaning}: {M.ENDPOINTS[e].unit}"
                                               for e in es) + ".")
            lines.append("")
        lines += ["![classification](plots/classification.svg)", "", "## Model cards", "",
                  "| endpoint | metric | held-out score | train | test |",
                  "|---|---|---|---|---|"]
        for e, c in run.cards.items():
            lines.append(f"| {e} | {c['metric']} | {c['test_score']} | {c['train']} | "
                         f"{c['test']} |")
        lines += ["", "Held-out scores are on TDC's scaffold-split test sets, molecules whose "
                  "scaffolds the model never saw; they are the error to expect for a new "
                  "chemical series, not for the training compounds.", ""]
    lines += ["## What a result is", "",
              "A prediction from a model trained on public benchmark data, with the error its "
              "model card states. Outside the applicability domain (Tanimoto similarity to the "
              "training set below 0.3) it is unreliable. A structural alert marks a "
              "substructure often behind assay interference or reactivity, not a verdict. "
              "None of this replaces measurement; it ranks compounds for testing.", ""]
    return "\n".join(lines)


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    out = Path(out_dir)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    problems = []
    for rel, digest in manifest["outputs"].items():
        p = out / rel
        if not p.is_file():
            problems.append(f"output {rel} is missing")
        elif _sha256(p) != digest:
            problems.append(f"output {rel} has changed")
    return not problems, problems
