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

from ..omics import svgplot
from ..omics.rnaseq import _html, _sha256
from . import dssp, geometry as G
from .pdbio import parse_pdb, read_pdb
from .predict import Prediction, PredictionError, fetch_reference, predict

__all__ = ["FoldConfig", "FoldResult", "read_sequences", "run_fold", "verify_run"]


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


def run_fold(source: str | Path, config: FoldConfig, out_dir: str | Path) -> list[FoldResult]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seqs = read_sequences(source)
    results: list[FoldResult] = []
    references: dict[str, Any] = {}
    for name, seq in seqs:
        warnings: list[str] = []
        pred = predict(name, seq, method=config.method, allow_remote=config.allow_remote,
                       workdir=out, timeout=config.timeout)
        model = parse_pdb(pred.pdb, source=name)
        mdir = out / "models"
        mdir.mkdir(exist_ok=True)
        path = mdir / f"{name}.pdb"
        from .pdbio import write_pdb
        write_pdb(model, path, bfactors=pred.plddt)
        conf = _confidence(pred.plddt, pred.pae)
        geo = _geometry(model)
        res = model.residues()
        ss = dssp.assign(model.backbone(), [r[3] for r in res])
        if conf["mean_plddt"] is not None and conf["mean_plddt"] < 70:
            warnings.append(f"{name}: mean pLDDT {conf['mean_plddt']:.0f}; the model as a whole "
                            "is low confidence")
        comparison = None
        ref_name = config.references.get(name)
        if ref_name:
            text, chain, prov = fetch_reference(ref_name, allow_remote=config.allow_remote,
                                                timeout=config.timeout)
            ref = parse_pdb(text, source=prov["reference"])
            chain = chain or ref.chains()[0]
            rb = ref.backbone(chain)
            cmp_ = G.compare(model.sequence(), model.backbone()["CA"], ref.sequence(chain),
                             rb["CA"])
            per_res = _per_residue_distance(model, ref, chain)
            comparison = {**asdict(cmp_), **prov, "chain": chain,
                          "per_residue_distance": per_res}
            references[name] = prov
            if cmp_.identity < 0.9:
                warnings.append(f"{name}: the reference's sequence is {cmp_.identity:.0%} "
                                "identical; the comparison is between different proteins")
            if cmp_.aligned < 0.8 * cmp_.reference_length:
                warnings.append(f"{name}: the model pairs with {cmp_.aligned} of the reference's "
                                f"{cmp_.reference_length} residues, so the TM-score by the "
                                f"reference's length ({cmp_.tm_score}) is low by construction; "
                                f"by the model's length it is {cmp_.tm_score_model}")
        results.append(FoldResult(name=name, sequence=pred.sequence, model_path=path,
                                  prediction=pred, confidence=conf, geometry=geo,
                                  secondary=ss, comparison=comparison, warnings=warnings))
    _write(results, config, out, started, references)
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


def _write(results: list[FoldResult], config: FoldConfig, out: Path, started: str,
           references: Mapping[str, Any]) -> None:
    plots: dict[str, str] = {}
    rows = []
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
                title=f"{r.name}: distance to {r.comparison['reference']} after superposition",
                xlabel="residue", ylabel="Å")
        rows.append(r)
    md = _markdown(results, config, plots)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.html").write_text(_html(md, plots), encoding="utf-8")
    pdir = out / "plots"
    pdir.mkdir(exist_ok=True)
    for name, svg in plots.items():
        (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
    summary = []
    for r in results:
        summary.append({"name": r.name, "length": len(r.sequence), "method": r.prediction.method,
                        "version": r.prediction.version, "remote": r.prediction.remote,
                        "request_sha256": r.prediction.request_sha256,
                        "response_sha256": r.prediction.response_sha256,
                        "seconds": r.prediction.seconds, "confidence": r.confidence,
                        "geometry": r.geometry, "secondary_structure": r.secondary,
                        "secondary_summary": dssp.summary(r.secondary),
                        "comparison": ({k: v for k, v in r.comparison.items()
                                        if k != "per_residue_distance"}
                                       if r.comparison else None),
                        "warnings": r.warnings})
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
               if p.is_file() and p.name != "run.json" and "colabfold_" not in str(p)}
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    manifest = {"pipeline": "bioagent.structure.fold", "code_digest": code.hexdigest(),
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "python": platform.python_version(), "config": config.as_dict(),
                "sequences": {r.name: hashlib.sha256(r.sequence.encode()).hexdigest()
                              for r in results},
                "references": dict(references), "outputs": outputs, "summary": summary}
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                  encoding="utf-8")


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
              "`run.json` holds the request and response digests of every prediction and the "
              "digests of every output; `bioagent fold --verify <out>` re-checks the outputs.",
              ""]
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
    for name, entry in ((s["name"], s) for s in manifest["summary"]):
        model = out / "models" / f"{name}.pdb"
        if model.is_file():
            seq = read_pdb(model).sequence()
            if hashlib.sha256(seq.encode()).hexdigest() != manifest["sequences"].get(name):
                problems.append(f"model {name} is not of the sequence that was folded")
        del entry
    return not problems, problems
