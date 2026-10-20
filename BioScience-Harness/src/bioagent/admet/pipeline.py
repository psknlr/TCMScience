"""The ADMET pipeline: molecules to properties, rules, alerts and predicted endpoints.

    from bioagent.admet.pipeline import AdmetConfig, run_admet
    run = run_admet("molecules.csv", AdmetConfig(), "out/")

For each molecule (standardised by ``chem.standardize``, or by the chemistry engine the
run is given: ``engine.ChemEngine``):

* physicochemical descriptors and drug-likeness rules (``rules.rules``);
* structural alerts (``rules.alerts``);
* when the models are built (``bioagent admet --build-models``), the 22 TDC endpoints,
  each with the model's held-out score and whether the molecule lies in its
  applicability domain.

Outputs: ``admet.tsv`` (one row per molecule), ``report.md`` / ``report.html`` and
``run.json`` with each model's identity (its file's and archive's SHA-256, training set,
held-out metric and score, applicability-domain threshold and digest, library versions).

``run.json`` also names the chemistry (``chemistry``: engine, version, detail) and what
searched the applicability domain (``similarity``: backend, what was checked, and why a
browser's similarities were rejected, when they were; null when nothing was predicted).

Every stage is recorded (``bioagent.runrecord``; the names are ``STAGES``) in
``stages.json`` while the run works and in ``run.json`` at the end, with ``complete`` and
``limitations.md``. Without the models, or without the model of an endpoint asked for,
the models and predictions stages are unavailable: the run is incomplete, and
``bioagent admet`` exits 1, though descriptors, rules and alerts are still written.

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
from typing import Any, Mapping, Sequence

import numpy as np

from .. import __version__ as _VERSION
from ..omics import svgplot
from ..omics.rnaseq import _html, _sha256
from ..runrecord import (LIMITATIONS_FILE, STAGES_FILE, StageRecorder, output_problems,
                         record_problems, stage_limitations, tsv_shape, write_limitations)
from . import models as M
from .chem import ChemError, tanimoto_best
from .engine import CPU_SIMILARITY, ChemEngine, PreparedSet, Similarity, default_engine, identity
from .rules import rules_of

__all__ = ["AdmetConfig", "AdmetRun", "read_molecules", "run_admet", "verify_run",
           "default_cache", "STAGES", "LIMITATIONS"]

PIPELINE = "bioagent.admet"
#: The stages of a run, in order; ``models`` and ``predictions`` are unavailable (and the
#: run incomplete) without the models.
STAGES = ("standardize", "descriptors", "rules", "alerts", "models", "predictions", "report",
          "verify")
#: What every result of this pipeline is not, one line each (``limitations.md``).
LIMITATIONS = (
    "Each predicted endpoint is a model estimate from a gradient-boosted model trained on "
    "the TDC ADMET benchmark group, not a measurement; the error to expect is the model "
    "card's held-out score on TDC's scaffold-split test set, for a new chemical series.",
    "Outside the applicability domain (largest Tanimoto similarity to the training set "
    "below 0.3) a prediction is unreliable; each prediction is flagged in or out.",
    "No per-molecule uncertainty interval is given: there is one model per endpoint, not "
    "an ensemble.",
    "A structural alert marks a substructure often behind assay interference or "
    "reactivity, not a verdict; the drug-likeness rules are heuristics.",
)
BASE_HEADER = ("name", "smiles", "MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors",
               "NumRotatableBonds", "qed", "lipinski_violations", "veber", "egan", "ghose",
               "alerts")
_UNLISTED = {"run.json", STAGES_FILE}


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
    missing_endpoints: list[str] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    limitations: list[str] = field(default_factory=list)
    chemistry: dict[str, Any] = field(default_factory=dict)
    similarity: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        outside = {e: sum(not r["predictions"][e]["in_domain"] for r in self.molecules)
                   for e in self.cards}
        return {"molecules": len(self.molecules), "failures": sorted(self.failures),
                "models_built": self.models_built, "endpoints": list(self.cards),
                "outside_domain": outside, "missing_endpoints": list(self.missing_endpoints),
                "chemistry": dict(self.chemistry), "similarity": self.similarity,
                "warnings": list(self.warnings), "out_dir": str(self.out_dir),
                "complete": self.complete}


def read_molecules(source: str | Path) -> list[tuple[str, str]]:
    from ..docking.prep import read_ligands
    try:
        return read_ligands(source)
    except Exception as exc:                                  # noqa: BLE001
        raise ChemError(str(exc)) from exc


def _identity(card: Mapping[str, Any]) -> dict[str, Any]:
    """What names a model: the six fields run.json always held, then the rest of its card
    that says what it was trained on and how (no retraining; read from the card)."""
    out = {k: card.get(k) for k in ("model_sha256", "archive_sha256", "metric", "test_score",
                                    "train", "test")}
    out.update({"archive_is_pinned": card.get("archive_is_pinned"),
                "training_set": card.get("data"), "model": card.get("model"),
                "iterations": card.get("iterations"), "features": card.get("features"),
                "domain_sha256": card.get("domain_sha256"),
                "domain_threshold": card.get("domain_threshold", M.DOMAIN_THRESHOLD),
                "sklearn": card.get("sklearn"), "rdkit": card.get("rdkit"),
                "seed": card.get("seed"), "chemistry": card.get("chemistry"),
                "dropped_unstandardisable": card.get("dropped_unstandardisable")})
    return out


def run_admet(source: str | Path | Sequence[tuple[str, str]], config: AdmetConfig,
              out_dir: str | Path, *, engine: ChemEngine | None = None,
              similarity: Similarity | None = None) -> AdmetRun:
    """Every stage, recorded as it runs (``runrecord.StageRecorder``). Without the models,
    or without a model of an endpoint that was asked for, the models and predictions
    stages are unavailable and the run is incomplete: descriptors, rules and alerts are
    still written, and ``AdmetRun.complete`` is False.

    ``engine`` computes the chemistry (default ``engine.RDKitEngine``; a browser's is read
    back with ``engine.PrecomputedEngine``). ``similarity`` searches each model's
    applicability domain (default ``chem.tanimoto_best`` on this CPU); one with a
    ``record()`` (``engine.PrecomputedSimilarity``) that rejected what it was offered has
    every endpoint recomputed on the CPU, and the run warns of it."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = StageRecorder(out, PIPELINE, plan=STAGES)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inputs = {}
    if isinstance(source, (str, Path)):
        try:
            if Path(str(source)).is_file():
                inputs[str(source)] = _sha256(Path(str(source)))
        except (OSError, ValueError):                 # a long SMILES is no file name
            pass
    entries = read_molecules(source) if isinstance(source, (str, Path)) else list(source)
    warnings: list[str] = []
    failures: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    chem_engine = engine or default_engine()
    # natively the stages name RDKit as they always have; another engine by its detail
    labels = (("rdkit (rdMolStandardize)", "rdkit") if chem_engine.name == "rdkit"
              else (chem_engine.detail, chem_engine.detail))

    with rec.stage("standardize", engine=labels[0], version=chem_engine.version) as st:
        prepared = chem_engine.prepare([smiles for _, smiles in entries])
        for i, (name, smiles) in enumerate(entries):
            if not prepared.ok[i]:
                failures[name] = prepared.errors[i]
                continue
            rows.append({"name": name, "input_smiles": smiles,
                         "smiles": prepared.canonical[i], "descriptors": {}, "rules": {},
                         "alerts": [], "predictions": {}})
        kept = list(prepared.index)                 # the read molecules' input positions
        if failures:
            warnings.append(f"{len(failures)} molecules could not be read: "
                            + "; ".join(f"{k}: {v}" for k, v in failures.items()))
        st.reason = (f"{len(kept)} of {len(entries)} molecules read and standardised (largest "
                     "fragment, neutralised where possible)")
        if not kept:
            st.status = "failed"
            st.reason = ("no molecule could be read" if entries else "no molecule was given")
            if entries:
                warnings.append("no molecule could be read, so nothing was predicted")
    fills = {"descriptors": lambda i: dict(prepared.descriptors[i]),
             "rules": lambda i: rules_of(prepared.descriptors[i], prepared.atoms_with_h[i]),
             "alerts": lambda i: [{"catalog": c, "alert": a} for c, a in prepared.alerts[i]]}
    for stage, what in (("descriptors", "physicochemical descriptors"),
                        ("rules", "Lipinski, Veber, Egan and Ghose rules"),
                        ("alerts", "PAINS, Brenk and NIH structural alerts")):
        if not kept:
            rec.skip(stage, "no molecule to describe")
            continue
        with rec.stage(stage, engine=labels[1], version=chem_engine.version) as st:
            for row, i in zip(rows, kept):
                row[stage] = fills[stage](i)
            st.reason = f"{what} of {len(kept)} molecules"

    cache = Path(config.cache_dir) if config.cache_dir else default_cache()
    modelset = M.load_models(cache)
    endpoints: list[str] = []
    missing: list[str] = []
    if modelset is None:
        warnings.append("the ADMET models are not built here, so only descriptors, rules and "
                        "alerts are reported; run `bioagent admet --build-models` once")
        # the reason reaches limitations.md and a job's governance, so it names no
        # machine's path; where the cache is, is in run.json ('model_cache')
        rec.unavailable("models", "no model is built in the ADMET model cache: run "
                                  "`bioagent admet --build-models` once (downloads the TDC "
                                  "ADMET group and trains the 22 models)")
    else:
        asked = list(config.endpoints) or list(M.ENDPOINTS)
        unknown = [e for e in asked if e not in M.ENDPOINTS]
        missing = [e for e in asked if e in M.ENDPOINTS and e not in modelset.cards]
        endpoints = [e for e in asked if e in modelset.cards]
        if config.endpoints and (missing or unknown):
            reason = ("; ".join(filter(None, (
                f"not built here: {', '.join(missing)} (`bioagent admet --build-models "
                f"--endpoint ...`)" if missing else "",
                f"no such endpoint: {', '.join(unknown)}" if unknown else ""))))
            warnings.append(f"endpoints asked for have no model: {reason}")
            rec.unavailable("models", reason)
            endpoints = []
        elif not endpoints:
            rec.unavailable("models", "the model cards list no endpoint: run `bioagent admet "
                                      "--build-models`")
        else:
            with rec.stage("models", engine="scikit-learn HistGradientBoosting",
                           version=", ".join(sorted({str(modelset.cards[e].get("sklearn"))
                                                     for e in endpoints}))) as st:
                for e in endpoints:
                    modelset.model(e)              # the file and the domain held to the card
                st.check = "PASS"
                st.reason = (f"{len(endpoints)} models, each file and its applicability "
                             "domain matching the SHA-256 its card records"
                             + (f"; {len(missing)} of the 22 TDC endpoints are not built here"
                                if missing else ""))
    cards = ({e: modelset.cards[e] for e in endpoints} if modelset and endpoints and kept
             else {})

    searched = None
    if not kept:
        rec.skip("predictions", "no molecule to predict", required=True)
    elif not cards:
        rec.unavailable("predictions", "no model to predict with")
    else:
        with rec.stage("predictions", engine="scikit-learn HistGradientBoosting",
                       version=", ".join(sorted({str(c.get("sklearn")) for c in cards.values()}))
                       ) as st:
            outside = 0
            found, searched = _domain_search(similarity, endpoints, prepared, modelset)
            if searched["rejected"]:
                warnings.append(f"the applicability-domain similarities offered were not used "
                                f"({searched['rejected']}); they were recomputed on the CPU")
            for e in endpoints:
                res = modelset.predict(prepared.features, prepared.bits, e,
                                       similarity=lambda ep, q, r: found[ep])
                for i, row in enumerate(rows):
                    row["predictions"][e] = {"value": float(res["value"][i]),
                                             "similarity": round(float(res["similarity"][i]), 3),
                                             "in_domain": bool(res["in_domain"][i])}
                outside += int((~res["in_domain"]).sum())
            st.reason = (f"{len(endpoints)} endpoints x {len(kept)} molecules; {outside} "
                         f"predictions outside the applicability domain (largest Tanimoto "
                         f"similarity to the training set below {M.DOMAIN_THRESHOLD})"
                         + _searched_note(searched))
    run = AdmetRun(out_dir=out, molecules=rows, models_built=modelset is not None,
                   cards=cards, failures=failures, warnings=warnings,
                   chemistry=identity(chem_engine), similarity=searched)
    run.missing_endpoints = missing
    _write(run, config, started, entries, rec, inputs)
    return run


def _domain_search(similarity: Similarity | None, endpoints: Sequence[str],
                   prepared: PreparedSet, modelset: M.ModelSet
                   ) -> tuple[dict[str, tuple[np.ndarray, ...]], dict[str, Any]]:
    """Each endpoint's ``(arg, inter, union)`` for the molecules against its domain, and
    what run.json records of how they were found. A similarity whose ``record()`` says it
    rejected what it was offered has every endpoint recomputed here, on the CPU."""
    if similarity is None:
        return ({e: tanimoto_best(prepared.bits, modelset.domain(e)) for e in endpoints},
                dict(CPU_SIMILARITY))
    found = {e: similarity(e, prepared.bits, modelset.domain(e)) for e in endpoints}
    record = (similarity.record() if hasattr(similarity, "record") else
              {"backend": getattr(similarity, "__name__", type(similarity).__name__),
               "checked": None, "rejected": None})
    if record.get("rejected"):
        found = {e: tanimoto_best(prepared.bits, modelset.domain(e)) for e in endpoints}
    return found, record


def _searched_note(searched: Mapping[str, Any]) -> str:
    """The predictions stage's word on the domain search, when this CPU did not do it."""
    if searched["rejected"]:
        return "; the similarities offered were rejected and recomputed on the CPU"
    if searched["backend"] == "cpu":
        return ""
    checked = searched.get("checked") or {}
    return (f"; similarities from {searched['backend']}" + (
        f", checked against the exact search on the CPU ({sum(checked.values())} queries "
        f"over {len(checked)} endpoints)" if checked else ", not checked"))


def _limitations(run: AdmetRun, rec: StageRecorder) -> list[str]:
    lines = list(LIMITATIONS)
    if any(c.get("archive_is_pinned") is False for c in run.cards.values()):
        lines.append("The models were trained on an archive whose SHA-256 is not the pinned "
                     "TDC ADMET group's, so their held-out scores are not comparable with "
                     "TDC's leaderboard.")
    if run.cards and run.missing_endpoints:
        lines.append(f"{len(run.missing_endpoints)} of the 22 TDC endpoints have no model "
                     "here and were not predicted: " + ", ".join(run.missing_endpoints) + ".")
    if run.failures:
        lines.append(f"{len(run.failures)} molecules could not be read and have no result: "
                     + ", ".join(sorted(run.failures)) + ".")
    lines += stage_limitations(rec.stages)
    return lines


def _header(endpoints: Sequence[str]) -> list[str]:
    header = list(BASE_HEADER)
    for e in endpoints:
        header += [e, f"{e}_in_domain"]
    return header


def _write(run: AdmetRun, config: AdmetConfig, started: str,
           entries: Sequence[tuple[str, str]], rec: StageRecorder,
           inputs: Mapping[str, str]) -> None:
    out = run.out_dir
    endpoints = list(run.cards)
    with rec.stage("report", engine=PIPELINE, version=_VERSION) as st:
        with (out / "admet.tsv").open("w", encoding="utf-8") as fh:
            fh.write("\t".join(_header(endpoints)) + "\n")
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
            matrix = np.array([[r["predictions"][e]["value"] for e in cls]
                               for r in run.molecules])
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
        run.limitations = write_limitations(out, _limitations(run, rec))
        st.outputs = ["admet.tsv", "report.md", "report.html", LIMITATIONS_FILE,
                      *(f"plots/{name}.svg" for name in plots)]
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    manifest: dict[str, Any] = {}
    with rec.stage("verify", engine=PIPELINE, version=_VERSION) as st:
        outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
                   if p.is_file() and p.name not in _UNLISTED}
        manifest.update({
            "pipeline": PIPELINE, "code_digest": code.hexdigest(), "started": started,
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "config": config.as_dict(),
            "molecules": [{"name": n, "smiles": s} for n, s in entries],
            "models": {e: _identity(c) for e, c in run.cards.items()},
            "outputs": outputs, "warnings": run.warnings, "inputs": dict(inputs),
            "read": [r["name"] for r in run.molecules], "failures": dict(run.failures),
            "missing_endpoints": list(run.missing_endpoints),
            "model_cache": str((Path(config.cache_dir) if config.cache_dir
                                else default_cache()) / "models"),
            "chemistry": dict(run.chemistry), "similarity": run.similarity})
        found = _content_problems(out, manifest) + output_problems(rec.stages, outputs, out)
        st.check = "FAIL" if found else "PASS"
        st.reason = ("; ".join(found)[:600] if found else
                     f"{len(outputs)} outputs digested; admet.tsv agrees with the run record")
    run.stages = rec.as_list()
    run.complete = rec.complete
    manifest.update({"stages": run.stages, "complete": run.complete,
                     "limitations": run.limitations})
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    rec.finish()


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
              "None of this replaces measurement; it ranks compounds for testing.", "",
              "## Reproducing it", "",
              "`run.json` records each model's identity (file and archive SHA-256, training "
              "set, held-out score, applicability-domain threshold), each output's digest and "
              "each stage's status and time; `limitations.md` lists this run's limitations. "
              "`bioagent admet --verify <out>` re-checks them.", ""]
    return "\n".join(lines)


def _content_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    """admet.tsv against the run record: a row per molecule read, and the columns of the
    endpoints whose models the record names."""
    path = out / "admet.tsv"
    if not path.is_file():
        return ["admet.tsv is missing"]
    found = []
    header, rows = tsv_shape(path)
    want = _header(list(manifest.get("models") or {}))
    if header != want:
        found.append("admet.tsv: its columns are not the descriptors and the endpoints of "
                     "the models run.json names")
    read = manifest.get("read")
    if read is not None and rows != len(read):
        found.append(f"admet.tsv: {rows} rows, where the run record read {len(read)} "
                     "molecules")
    return found


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-hash every recorded output (and input file, when the record lists them); for a
    record with stages, also check that the run is complete and that admet.tsv agrees
    with it. A run.json without stages gets the digest checks only."""
    out = Path(out_dir)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    problems = []
    for path, digest in (manifest.get("inputs") or {}).items():
        p = Path(path)
        if not p.is_file():
            problems.append(f"input {path} is missing")
        elif _sha256(p) != digest:
            problems.append(f"input {path} has changed")
    for rel, digest in manifest["outputs"].items():
        p = out / rel
        if not p.is_file():
            problems.append(f"output {rel} is missing")
        elif _sha256(p) != digest:
            problems.append(f"output {rel} has changed")
    if "stages" in manifest:
        problems += record_problems(manifest, out)
        problems += _content_problems(out, manifest)
    return not problems, problems
