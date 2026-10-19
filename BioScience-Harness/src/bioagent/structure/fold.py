"""The structure-prediction pipeline: sequences to models, their quality and agreement.

    from bioagent.structure.fold import FoldConfig, run_fold
    run = run_fold("proteins.fasta", FoldConfig(method="esmatlas", allow_remote=True,
                                                references={"ubq": "PDB:1UBQ:A"}), "out/")

For each sequence:

1. **Prediction** by the chosen method (``predict``), the model written as PDB with
   pLDDT (0-100) in the B-factor column.
2. **Confidence**: mean pLDDT, the shares above 90 / 70 / 50, and the segments below 70
   and below 50 (AlphaFold's bands: very high, confident, low, very low); the PAE
   summary when the method gives one.
3. **Geometry**: DSSP secondary structure, radius of gyration, consecutive CA–CA
   distances off 3.8 Å, heavy-atom clashes and backbone dihedrals outside the coarse
   allowed regions.
4. **Agreement** with a reference when one is named (an experimental PDB entry, an
   AlphaFold DB model or a file): sequence alignment, TM-score, RMSD and GDT-TS, and the
   per-residue distance after superposition.
5. **Report**: per-residue confidence and distance plots, the contact map, PAE,
   ``report.md`` / ``report.html`` and ``run.json`` with every digest.

Every stage is recorded (``bioagent.runrecord``; the names are ``STAGES``) with its status,
check, engine, version, device and time, in ``stages.json`` while the run works and in
``run.json`` at the end, with ``complete`` and ``limitations.md``. Each model's entry in
``summary.json`` names the device it was computed on and its checkpoint as far as they
can be observed (``predict``: only local ESMFold's weights are digested; the ESM Atlas
service's cannot be observed and ColabFold's parameters are not digested, so both are
recorded as not verifiable), and ``sequence_identity`` checks that every model, whatever
the method, is of the sequence that was folded. A reference is only compared with; it is
never used in place of a prediction, and a failed prediction stops the run.

What a model is: a computational prediction. pLDDT is the method's confidence in each
residue's local structure, not a measurement, and says nothing about how domains sit
relative to each other (PAE does). Regions below 70 should not be interpreted. A model
is one chain without ligands, cofactors, modifications or partners.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .. import __version__ as _VERSION
from ..omics import svgplot
from ..omics.rnaseq import _html, _sha256
from ..runrecord import (LIMITATIONS_FILE, STAGES_FILE, StageRecorder, output_problems,
                         record_problems, stage_limitations, write_limitations)
from . import dssp, geometry as G
from .pdbio import parse_pdb, read_pdb
from .predict import (ESM_ATLAS_MAX, ESMATLAS_DETAIL, PREDICTORS, Prediction, PredictionError,
                      fetch_reference, predict, validate_sequence)

__all__ = ["FoldConfig", "FoldResult", "read_sequences", "run_fold", "verify_run", "STAGES",
           "LIMITATIONS"]

PIPELINE = "bioagent.structure.fold"
#: The stages of a run, in order; ``compare`` is not applicable without a reference.
STAGES = ("input_check", "predict", "sequence_identity", "confidence", "geometry", "compare",
          "report", "verify")
#: What every model of this pipeline is not, one line each (``limitations.md``).
LIMITATIONS = (
    "A model is a computational prediction, not an experimental structure: one chain "
    "without ligands, cofactors, modifications or partners.",
    "pLDDT is the method's confidence in each residue's local structure, not a "
    "measurement; it does not say how domains sit relative to each other (the predicted "
    "aligned error does), and regions below 70 should not be interpreted.",
)
_UNLISTED = {"run.json", STAGES_FILE}


@dataclass(frozen=True)
class FoldConfig:
    method: str = "esmatlas"
    allow_remote: bool = False
    references: Mapping[str, str] = field(default_factory=dict)   # sequence name -> ref
    timeout: float = 300.0

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["references"] = dict(self.references)
        return d


@dataclass
class FoldResult:
    name: str
    sequence: str
    model_path: Path
    prediction: Prediction
    confidence: dict[str, Any]
    geometry: dict[str, Any]
    secondary: str
    comparison: dict[str, Any] | None
    warnings: list[str]


def read_sequences(source: str | Path) -> list[tuple[str, str]]:
    """(name, sequence) from a FASTA file, or one bare sequence."""
    p = Path(str(source))
    text = p.read_text(encoding="utf-8") if p.is_file() else str(source)
    if ">" not in text:
        return [("seq1", "".join(text.split()))]
    out: list[tuple[str, str]] = []
    name, chunks = None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if name is not None:
                out.append((name, "".join(chunks)))
            name = line[1:].split()[0] if line[1:].strip() else f"seq{len(out) + 1}"
            name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
            chunks = []
        elif line.strip():
            chunks.append(line.strip())
    if name is not None:
        out.append((name, "".join(chunks)))
    names = [n for n, _ in out]
    if len(set(names)) != len(names):
        raise PredictionError("two sequences share a name")
    return out


def _segments(mask: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start + 1, i))
            start = None
    return out


def _confidence(plddt: np.ndarray, pae: np.ndarray | None) -> dict[str, Any]:
    p = plddt[np.isfinite(plddt)]
    out = {"mean_plddt": round(float(p.mean()), 2) if len(p) else None,
           "very_high": round(float((p >= 90).mean()), 4),
           "confident": round(float((p >= 70).mean()), 4),
           "low": round(float(((p >= 50) & (p < 70)).mean()), 4),
           "very_low": round(float((p < 50).mean()), 4),
           "below_70": _segments(plddt < 70), "below_50": _segments(plddt < 50)}
    if pae is not None:
        out["mean_pae"] = round(float(np.nanmean(pae)), 3)
    return out


def _geometry(model) -> dict[str, Any]:
    bb = model.backbone()
    res = model.residues()
    names = [r[3] for r in res]
    ca = bb["CA"]
    ok = np.isfinite(ca).all(axis=1)
    steps = np.linalg.norm(np.diff(ca, axis=0), axis=1)
    breaks = int(np.sum(np.abs(steps[np.isfinite(steps)] - 3.8) > 0.5))
    heavy = [a for a in model.atoms if a.element.upper() != "H"]
    coords = np.array([a.xyz for a in heavy])
    index = {(c, r, i): k for k, (c, r, i, _) in enumerate(res)}
    resid = np.array([index.get((a.chain, a.resseq, a.icode), -10) for a in heavy])
    dih = G.dihedrals(bb)
    allowed = G.ramachandran(dih["phi"], dih["psi"], names)
    return {"residues": int(ok.sum()), "radius_of_gyration": round(G.radius_of_gyration(ca), 3),
            "ca_ca_outliers": breaks, "clashes": G.clashes(coords, resid),
            "dihedral_outliers": int((~allowed).sum()),
            "dihedral_outlier_share": round(float((~allowed).mean()), 4)}


def _observed(pred: Prediction, method: str) -> dict[str, Any]:
    """The device and checkpoint a prediction records; the ESM Atlas service's are known
    from the method itself (a remote device, a checkpoint that cannot be verified)."""
    detail = dict(pred.detail or {})
    if method == "esmatlas":
        for key, value in ESMATLAS_DETAIL.items():
            detail.setdefault(key, value)
    return {"device": detail.get("device"), "device_note": detail.get("device_note"),
            "checkpoint": detail.get("checkpoint"), "resources": detail.get("resources")}


def run_fold(source: str | Path, config: FoldConfig, out_dir: str | Path) -> list[FoldResult]:
    """Every stage, recorded as it runs (``runrecord.StageRecorder``); the run record is
    complete when every sequence was predicted, its model is of that sequence, and its
    confidence and geometry were computed."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = StageRecorder(out, PIPELINE, plan=STAGES)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inputs = {}
    try:
        if Path(str(source)).is_file():
            inputs[str(source)] = _sha256(Path(str(source)))
    except (OSError, ValueError):                     # a long sequence is no file name
        pass

    with rec.stage("input_check", engine=PIPELINE, version=_VERSION) as st:
        seqs = [(name, validate_sequence(seq)) for name, seq in read_sequences(source)]
        if not seqs:
            raise PredictionError("no sequence to fold")
        if config.method not in PREDICTORS:
            raise PredictionError(f"method is one of {', '.join(PREDICTORS)}")
        if config.method in ("esmatlas", "colabfold") and not config.allow_remote:
            raise PredictionError(
                f"{config.method} sends the sequence to a third-party service; pass "
                "allow_remote (--allow-remote) to permit it, or use method 'esmfold' to "
                "fold locally")
        if config.method == "esmatlas":
            long = [n for n, s in seqs if len(s) > ESM_ATLAS_MAX]
            if long:
                raise PredictionError(f"{', '.join(long)}: longer than the {ESM_ATLAS_MAX} "
                                      "residues the ESM Atlas service folds; run ESMFold or "
                                      "ColabFold locally")
        st.check = "PASS"
        st.reason = (f"{len(seqs)} sequences of standard amino acids "
                     f"({min(len(s) for _, s in seqs)} to {max(len(s) for _, s in seqs)} "
                     f"residues); method {config.method}")

    preds: list[Prediction] = []
    observed: list[dict[str, Any]] = []
    mdir = out / "models"
    with rec.stage("predict", engine=config.method) as st:
        from .pdbio import write_pdb
        mdir.mkdir(exist_ok=True)
        for name, seq in seqs:
            pred = predict(name, seq, method=config.method, allow_remote=config.allow_remote,
                           workdir=out, timeout=config.timeout)
            preds.append(pred)
            observed.append(_observed(pred, config.method))
            write_pdb(parse_pdb(pred.pdb, source=name), mdir / f"{name}.pdb",
                      bfactors=pred.plddt)
            st.outputs.append(f"models/{name}.pdb")
        st.version = "; ".join(sorted({p.version for p in preds}))
        devices = sorted({str(o["device"]) for o in observed if o["device"]})
        st.device = ", ".join(devices) if devices else None
        checkpoints = [o["checkpoint"] or {} for o in observed]
        # verifiable only as far as the weights themselves were digested: a digested
        # configuration (ColabFold's) does not say which parameters were loaded
        digested = all(c.get("verifiable") and c.get("weights_sha256") for c in checkpoints)
        notes = sorted({o["device_note"] for o in observed if o.get("device_note")})
        st.reason = (f"{len(preds)} models by {preds[0].method}; checkpoint "
                     + ("weights digested" if digested else "not verifiable: " + "; ".join(
                         sorted({str(c.get('reason') or 'not recorded') for c in checkpoints
                                 if not (c.get("verifiable") and c.get("weights_sha256"))})))
                     + (f"; {'; '.join(notes)}" if notes else ""))

    with rec.stage("sequence_identity", engine=PIPELINE, version=_VERSION) as st:
        models = []
        for pred, (name, seq) in zip(preds, seqs):
            model = parse_pdb(pred.pdb, source=name)
            if model.sequence() != seq:
                st.check = "FAIL"
                raise PredictionError(f"{name}: the model is not of the sequence folded")
            models.append(model)
        st.check = "PASS"
        st.reason = (f"every model's sequence is the one folded ({len(models)} of "
                     f"{len(seqs)}, 100% identical)")

    with rec.stage("confidence", engine=PIPELINE, version=_VERSION) as st:
        confidence = [_confidence(p.plddt, p.pae) for p in preds]
        low = [n for (n, _), c in zip(seqs, confidence)
               if c["mean_plddt"] is not None and c["mean_plddt"] < 70]
        st.reason = (f"pLDDT bands of {len(preds)} models"
                     + (f"; PAE of {sum(p.pae is not None for p in preds)}"
                        if any(p.pae is not None for p in preds) else "; no PAE from the method")
                     + (f"; mean pLDDT below 70: {', '.join(low)}" if low else ""))

    with rec.stage("geometry", engine=PIPELINE, version=_VERSION) as st:
        geometry, secondary = [], []
        for model in models:
            geometry.append(_geometry(model))
            res = model.residues()
            secondary.append(dssp.assign(model.backbone(), [r[3] for r in res]))
        st.reason = ("DSSP, radius of gyration, CA-CA steps, heavy-atom clashes, backbone "
                     f"dihedrals of {len(models)} models")

    results: list[FoldResult] = []
    references: dict[str, Any] = {}
    comparisons: list[dict[str, Any] | None] = [None] * len(seqs)
    if any(config.references.get(name) for name, _ in seqs):
        with rec.stage("compare", required=False, engine=PIPELINE, version=_VERSION) as st:
            for k, ((name, _), model) in enumerate(zip(seqs, models)):
                ref_name = config.references.get(name)
                if not ref_name:
                    continue
                text, chain, prov = fetch_reference(ref_name, allow_remote=config.allow_remote,
                                                    timeout=config.timeout)
                ref = parse_pdb(text, source=prov["reference"])
                chain = chain or ref.chains()[0]
                rb = ref.backbone(chain)
                cmp_ = G.compare(model.sequence(), model.backbone()["CA"], ref.sequence(chain),
                                 rb["CA"])
                comparisons[k] = {**asdict(cmp_), **prov, "chain": chain,
                                  "per_residue_distance": _per_residue_distance(model, ref,
                                                                                 chain)}
                references[name] = prov
            st.reason = (f"{len(references)} models against their reference (TM-score, RMSD, "
                         "GDT-TS); a reference is compared with, never used as, a model")
    else:
        rec.not_applicable("compare", "no reference structure was named")

    for k, ((name, seq), pred) in enumerate(zip(seqs, preds)):
        warnings: list[str] = []
        conf, cmp_ = confidence[k], comparisons[k]
        if conf["mean_plddt"] is not None and conf["mean_plddt"] < 70:
            warnings.append(f"{name}: mean pLDDT {conf['mean_plddt']:.0f}; the model as a whole "
                            "is low confidence")
        if cmp_ is not None:
            if cmp_["identity"] < 0.9:
                warnings.append(f"{name}: the reference's sequence is {cmp_['identity']:.0%} "
                                "identical; the comparison is between different proteins")
            if cmp_["aligned"] < 0.8 * cmp_["reference_length"]:
                warnings.append(f"{name}: the model pairs with {cmp_['aligned']} of the "
                                f"reference's {cmp_['reference_length']} residues, so the "
                                f"TM-score by the reference's length ({cmp_['tm_score']}) is "
                                "low by construction; by the model's length it is "
                                f"{cmp_['tm_score_model']}")
        results.append(FoldResult(name=name, sequence=pred.sequence,
                                  model_path=mdir / f"{name}.pdb", prediction=pred,
                                  confidence=conf, geometry=geometry[k], secondary=secondary[k],
                                  comparison=cmp_, warnings=warnings))
    _write(results, config, out, started, references, rec, inputs, observed)
    return results


def _per_residue_distance(model, ref, chain: str) -> list[float | None]:
    mb, rb = model.backbone()["CA"], ref.backbone(chain)["CA"]
    al = G.align(model.sequence(), ref.sequence(chain))
    pairs = [(i, j) for i, j in al.pairs if np.isfinite(mb[i]).all() and np.isfinite(rb[j]).all()]
    mi = np.array([i for i, _ in pairs])
    ri = np.array([j for _, j in pairs])
    # superpose on the core that the TM-score search settles on: residues within 4 Å
    sel = np.arange(len(pairs))
    for _ in range(10):
        r, t = G.kabsch(mb[mi][sel], rb[ri][sel])
        d = np.linalg.norm(mb[mi] @ r.T + t - rb[ri], axis=1)
        new = np.flatnonzero(d < 4.0)
        if len(new) < 3 or np.array_equal(new, sel):
            break
        sel = new
    out: list[float | None] = [None] * len(mb)
    for k, i in enumerate(mi):
        out[int(i)] = round(float(d[k]), 3)
    return out


def _downsample(m: np.ndarray, size: int = 120) -> np.ndarray:
    k = m.shape[0]
    if k <= size:
        return m
    edges = np.linspace(0, k, size + 1).astype(int)
    return np.array([[m[edges[i]:edges[i + 1], edges[j]:edges[j + 1]].max()
                      for j in range(size)] for i in range(size)])


def _limitations(results: list[FoldResult], config: FoldConfig,
                 rec: StageRecorder) -> list[str]:
    lines = list(LIMITATIONS)
    if config.method == "esmatlas":
        lines.append("The ESM Atlas service ran its own copy of ESMFold; its weights cannot be "
                     "observed from here, so no checkpoint digest is recorded for them.")
    if config.method == "colabfold":
        lines.append("ColabFold queries its MSA server unless configured otherwise; the MSA "
                     "it used is not part of this record, and the AlphaFold2 parameters are "
                     "not digested (only the configuration colabfold_batch wrote).")
    if any(r.comparison for r in results):
        lines.append("A reference structure is used only for comparison (TM-score, RMSD, "
                     "GDT-TS); it never stands in for a prediction.")
    lines += stage_limitations(rec.stages)
    return lines


def _write(results: list[FoldResult], config: FoldConfig, out: Path, started: str,
           references: Mapping[str, Any], rec: StageRecorder, inputs: Mapping[str, str],
           observed: list[dict[str, Any]]) -> None:
    with rec.stage("report", engine=PIPELINE, version=_VERSION) as st:
        plots: dict[str, str] = {}
        for r in results:
            pos = np.arange(1, len(r.sequence) + 1)
            plots[f"plddt_{r.name}"] = svgplot.lines(
                pos, {"pLDDT": list(r.prediction.plddt)}, hlines=(50, 70, 90), ylim=(0, 100),
                title=f"{r.name}: per-residue confidence (pLDDT)", xlabel="residue",
                ylabel="pLDDT")
            bb = parse_pdb(r.prediction.pdb).backbone()
            plots[f"contacts_{r.name}"] = svgplot.heatmap(
                _downsample(G.contact_map(bb["CB"])), [""] * min(len(pos), 120),
                [""] * min(len(pos), 120), title=f"{r.name}: contact map (CB within 8 Å)",
                scale_label="contact")
            if r.prediction.pae is not None:
                plots[f"pae_{r.name}"] = svgplot.heatmap(
                    _downsample(r.prediction.pae), [""] * min(len(pos), 120),
                    [""] * min(len(pos), 120), title=f"{r.name}: predicted aligned error",
                    scale_label="Å", reverse=True)
            if r.comparison:
                d = [np.nan if v is None else v for v in r.comparison["per_residue_distance"]]
                plots[f"distance_{r.name}"] = svgplot.lines(
                    pos, {"distance": d}, hlines=(2.0, 4.0),
                    title=f"{r.name}: distance to {r.comparison['reference']} after "
                          "superposition", xlabel="residue", ylabel="Å")
        md = _markdown(results, config, plots)
        (out / "report.md").write_text(md, encoding="utf-8")
        (out / "report.html").write_text(_html(md, plots), encoding="utf-8")
        pdir = out / "plots"
        pdir.mkdir(exist_ok=True)
        for name, svg in plots.items():
            (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
        summary = []
        for r, o in zip(results, observed):
            summary.append({"name": r.name, "length": len(r.sequence),
                            "method": r.prediction.method,
                            "version": r.prediction.version, "remote": r.prediction.remote,
                            "request_sha256": r.prediction.request_sha256,
                            "response_sha256": r.prediction.response_sha256,
                            "seconds": r.prediction.seconds, "confidence": r.confidence,
                            "geometry": r.geometry, "secondary_structure": r.secondary,
                            "secondary_summary": dssp.summary(r.secondary),
                            "comparison": ({k: v for k, v in r.comparison.items()
                                            if k != "per_residue_distance"}
                                           if r.comparison else None),
                            "warnings": r.warnings, "device": o["device"],
                            "checkpoint": o["checkpoint"], "sequence_identity": 1.0,
                            **({"device_note": o["device_note"]} if o["device_note"] else {}),
                            **({"resources": o["resources"]} if o["resources"] else {})})
        # peak GPU memory varies between runs of the same input: it stays in run.json's
        # summary and out of summary.json, a collected output
        (out / "summary.json").write_text(json.dumps(
            [{k: v for k, v in e.items() if k != "resources"} for e in summary],
            ensure_ascii=False, indent=2), encoding="utf-8")
        limitations = write_limitations(out, _limitations(results, config, rec))
        st.outputs = ["report.md", "report.html", "summary.json", LIMITATIONS_FILE,
                      *(f"plots/{name}.svg" for name in plots)]
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    manifest: dict[str, Any] = {}
    with rec.stage("verify", engine=PIPELINE, version=_VERSION) as st:
        outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
                   if p.is_file() and p.name not in _UNLISTED and "colabfold_" not in str(p)}
        manifest.update({
            "pipeline": PIPELINE, "code_digest": code.hexdigest(), "started": started,
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "config": config.as_dict(),
            "sequences": {r.name: hashlib.sha256(r.sequence.encode()).hexdigest()
                          for r in results},
            "references": dict(references), "outputs": outputs, "summary": summary,
            "inputs": dict(inputs)})
        found = (_content_problems(out, manifest) + _sequence_problems(out, manifest)
                 + output_problems(rec.stages, outputs, out))
        st.check = "FAIL" if found else "PASS"
        st.reason = ("; ".join(found)[:600] if found else
                     f"{len(outputs)} outputs digested; every model is of its sequence")
    manifest.update({"stages": rec.as_list(), "complete": rec.complete,
                     "limitations": limitations})
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    rec.finish()


def _markdown(results: list[FoldResult], config: FoldConfig,
              plots: Mapping[str, str]) -> str:
    lines = ["# Protein structure prediction", "",
             f"Method: **{results[0].prediction.method}** "
             f"({results[0].prediction.version})"
             + ("; the sequences were sent to a remote service." if results[0].prediction.remote
                else "; run on this machine."), "", "## Models", "",
             "| sequence | length | mean pLDDT | ≥ 90 | ≥ 70 | < 50 | helix | strand | "
             "TM-score | RMSD (Å) | reference |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        c, s = r.confidence, dssp.summary(r.secondary)
        cmp_ = r.comparison or {}
        lines.append(f"| {r.name} | {len(r.sequence)} | {c['mean_plddt']} | {c['very_high']:.0%} | "
                     f"{c['confident']:.0%} | {c['very_low']:.0%} | {s['helix']:.0%} | "
                     f"{s['strand']:.0%} | {cmp_.get('tm_score', 'NA')} | "
                     f"{cmp_.get('rmsd', 'NA')} | {cmp_.get('reference', '-')} |")
    lines.append("")
    warnings = [w for r in results for w in r.warnings]
    if warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in warnings] + [""]
    for r in results:
        g, c = r.geometry, r.confidence
        lines += [f"## {r.name}", "", f"`models/{r.name}.pdb` (pLDDT in the B-factor column).",
                  "", "```", r.sequence, r.secondary, "```", "",
                  "- DSSP secondary structure above (H helix, G 3-10, E strand, B bridge, "
                  "T turn).",
                  f"- Below pLDDT 70: {', '.join(f'{a}-{b}' for a, b in c['below_70']) or 'none'}; "
                  f"below 50: {', '.join(f'{a}-{b}' for a, b in c['below_50']) or 'none'}.",
                  f"- Radius of gyration {g['radius_of_gyration']} Å; CA–CA outliers "
                  f"{g['ca_ca_outliers']}; clashes {g['clashes']}; dihedrals outside the coarse "
                  f"allowed regions {g['dihedral_outliers']} ({g['dihedral_outlier_share']:.1%}).",
                  ]
        if r.comparison:
            cmp_ = r.comparison
            lines.append(f"- Against {cmp_['reference']} (chain {cmp_['chain']}, "
                         f"{cmp_['kind']}): TM-score {cmp_['tm_score']} by the reference's "
                         f"length ({cmp_['reference_length']}) and {cmp_['tm_score_model']} by "
                         f"the model's ({cmp_['model_length']}); RMSD {cmp_['rmsd']} Å over "
                         f"{cmp_['aligned']} aligned residues; GDT-TS {cmp_['gdt_ts']}; sequence "
                         f"identity {cmp_['identity']:.0%}. A TM-score above 0.5 means the same "
                         "fold.")
        lines.append("")
        for kind in ("plddt", "contacts", "pae", "distance"):
            if f"{kind}_{r.name}" in plots:
                lines.append(f"![{kind}_{r.name}](plots/{kind}_{r.name}.svg)")
        lines.append("")
    lines += ["## What a model is", "",
              "A computational prediction. pLDDT is the method's confidence in each residue's "
              "local structure, not a measurement, and does not say how domains sit relative "
              "to each other (the predicted aligned error does). Regions below 70 should not "
              "be interpreted. The model is one chain without ligands, cofactors, "
              "modifications or partners, and is not an experimental structure.", "",
              "## Reproducing it", "",
              "`run.json` holds the request and response digests of every prediction, the "
              "digests of every output and each stage's status, engine, device and time; "
              "`summary.json` names each model's device and checkpoint where they can be "
              "observed; `limitations.md` lists this run's limitations. "
              "`bioagent fold --verify <out>` re-checks the outputs and that every model is of "
              "its sequence.", ""]
    return "\n".join(lines)


def _sequence_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    problems = []
    for entry in manifest.get("summary") or ():
        name = entry["name"]
        model = out / "models" / f"{name}.pdb"
        if model.is_file():
            seq = read_pdb(model).sequence()
            if hashlib.sha256(seq.encode()).hexdigest() != manifest["sequences"].get(name):
                problems.append(f"model {name} is not of the sequence that was folded")
    return problems


def _content_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    """summary.json and the models against the run record: one entry and one model per
    sequence folded."""
    found = []
    names = [s.get("name") for s in manifest.get("summary") or ()]
    if sorted(names) != sorted(manifest.get("sequences") or {}):
        found.append("run.json's summary does not cover exactly the sequences folded")
    try:
        written = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return found + ["summary.json is missing or not JSON"]
    if [s.get("name") for s in written] != names:
        found.append("summary.json does not list the models run.json records")
    for name in names:
        if not (out / "models" / f"{name}.pdb").is_file():
            found.append(f"models/{name}.pdb is missing")
    return found


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-hash every recorded output (and the input file, when the record lists it) and
    check that every model is of the sequence that was folded; for a record with stages,
    also that the run is complete and that summary.json agrees with it. A run.json
    without stages gets the digest and sequence checks only."""
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
    problems += _sequence_problems(out, manifest)
    if "stages" in manifest:
        problems += record_problems(manifest, out)
        problems += _content_problems(out, manifest)
    return not problems, problems
