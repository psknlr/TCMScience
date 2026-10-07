"""The docking pipeline: a receptor, ligands and a site to poses, scores and a report.

    from bioagent.docking.pipeline import DockConfig, run_docking
    run = run_docking("PDB:3PTB", "ligands.csv", DockConfig(site_ligand="BEN"), "out/")

1. **Receptor**: a file, ``PDB:<id>[:chains]`` from RCSB, or ``UniProt:<acc>`` (the
   AlphaFold DB model), prepared by ``prep.prepare_receptor``.
2. **Site**: a box given as centre and size, or built around the co-crystal ligand named
   by ``site_ligand`` (8 Å of padding, at least 18 Å a side), or around listed residues.
3. **Validation**: when the site comes from a co-crystal ligand, that ligand is
   re-embedded from its SMILES and docked back. The setup counts as validated when the
   top pose lies within 2 Å (heavy-atom RMSD, symmetry-aware) of the crystal pose, the
   usual redocking criterion. Without it, scores are reported as unvalidated.
4. **Docking** of each ligand with Vina (``engine``), poses written as SDF.
5. **Contacts** of each best pose (``interactions``).
6. **Report**: a table of scores (with ligand efficiency, score per heavy atom), the
   validation, contacts, 2D depictions, ``report.md`` / ``report.html`` and ``run.json``.

What a result is: a docking score is the scoring function's estimate, not a measured
affinity, and a pose is a hypothesis about binding. Ranking by score enriches actives
only modestly; a hit needs an assay.
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
from . import engine, interactions, prep

__all__ = ["DockConfig", "DockRun", "run_docking", "verify_run"]

REDOCK_RMSD = 2.0


@dataclass(frozen=True)
class DockConfig:
    site_ligand: str | None = None          # residue name of a co-crystal ligand
    site_ligand_smiles: str | None = None   # its SMILES (default: the CCD's)
    box_center: tuple[float, float, float] | None = None
    box_size: tuple[float, float, float] | None = None
    site_residues: tuple[str, ...] = ()     # "A:189", "A:195", ...
    chains: tuple[str, ...] = ()
    cofactors: tuple[str, ...] = ()
    scoring: str = "vina"
    exhaustiveness: int = 8
    n_poses: int = 9
    seed: int = 42
    allow_remote: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DockRun:
    out_dir: Path
    receptor: prep.Receptor
    box: engine.Box
    results: list[engine.DockResult]
    validation: dict[str, Any] | None
    contacts: dict[str, list[interactions.Contact]]
    failures: dict[str, str]
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def validated(self) -> bool:
        return bool(self.validation and self.validation["passed"])


def _receptor_text(spec: str, config: DockConfig) -> tuple[str, dict[str, Any], tuple]:
    path = Path(spec)
    if path.is_file():
        data = path.read_bytes()
        return data.decode("utf-8", errors="replace"), {
            "receptor": str(path), "sha256": hashlib.sha256(data).hexdigest()}, config.chains
    kind, _, rest = spec.partition(":")
    if not config.allow_remote:
        raise prep.DockingError(f"fetching {spec} reaches RCSB or AlphaFold DB; pass "
                                "allow_remote (--allow-remote)")
    if kind.lower() == "pdb":
        code, _, chains = rest.partition(":")
        text = prep.fetch_pdb(code)
        chosen = tuple(chains) if chains else config.chains
        return text, {"receptor": f"PDB {code.upper()}",
                      "sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "licence": "PDB data: CC0 1.0"}, chosen
    if kind.lower() == "uniprot":
        from ..structure.predict import fetch_reference
        text, _, prov = fetch_reference(spec, allow_remote=True)
        return text, prov, config.chains
    raise prep.DockingError(f"receptor {spec!r}: a PDB file, PDB:<id>[:chains] or "
                            "UniProt:<acc>")


def _site(text: str, config: DockConfig) -> tuple[engine.Box, Any]:
    reference = None
    if config.site_ligand:
        reference, coords = prep.cocrystal_ligand(text, config.site_ligand,
                                                  smiles=config.site_ligand_smiles)
        box = engine.Box.around(coords, source=f"co-crystal ligand {config.site_ligand}")
        if config.box_size:
            box = engine.Box(box.center, tuple(config.box_size), box.source)
        return box, reference
    if config.box_center and config.box_size:
        return engine.Box(tuple(config.box_center), tuple(config.box_size), "given"), None
    if config.site_residues:
        coords = []
        wanted = {r.upper() for r in config.site_residues}
        for line in text.splitlines():
            if line[:6] == "ATOM  " and f"{line[21]}:{int(line[22:26])}".upper() in wanted:
                coords.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
        if not coords:
            raise prep.DockingError("none of the site residues is in the receptor")
        return engine.Box.around(np.array(coords), source=f"residues "
                                 f"{', '.join(config.site_residues)}"), None
    raise prep.DockingError("name the site: a co-crystal ligand, a box (centre and size) or "
                            "site residues")


def run_docking(receptor: str, ligands: str | Sequence[tuple[str, str]], config: DockConfig,
                out_dir: str | Path) -> DockRun:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []
    text, source, chains = _receptor_text(receptor, config)
    rec = prep.prepare_receptor(text, chains=chains or None, cofactors=config.cofactors,
                                source=source)
    if rec.unmatched:
        warnings.append(f"{len(rec.unmatched)} receptor residues matched no template and "
                        "were left out")
    box, reference = _site(text, config)
    (out / "receptor.pdbqt").write_text(rec.pdbqt, encoding="utf-8")
    (out / "receptor_clean.pdb").write_text(rec.pdb, encoding="utf-8")

    validation = None
    if reference is not None:
        from rdkit import Chem
        smiles = config.site_ligand_smiles or prep.ccd_smiles(config.site_ligand)
        lig = prep.prepare_ligand(f"redock_{config.site_ligand}", smiles, seed=config.seed)
        res = engine.dock(rec, lig, box, scoring=config.scoring,
                          exhaustiveness=config.exhaustiveness, n_poses=config.n_poses,
                          seed=config.seed)
        rmsds = [engine.pose_rmsd(p.mol, reference) for p in res.poses]
        for p, r in zip(res.poses, rmsds):
            p.rmsd_to_reference = r
        top = rmsds[0] if rmsds else float("inf")
        validation = {"ligand": config.site_ligand, "smiles": smiles,
                      "top_pose_rmsd": top, "best_rmsd_in_top3": min(rmsds[:3]) if rmsds else None,
                      "top_score": res.poses[0].score if res.poses else None,
                      "threshold": REDOCK_RMSD, "passed": top <= REDOCK_RMSD,
                      "poses": [{"rank": p.rank, "score": p.score, "rmsd": p.rmsd_to_reference}
                                for p in res.poses]}
        Chem.MolToMolFile(reference, str(out / f"crystal_{config.site_ligand}.mol"))
        if not validation["passed"]:
            warnings.append(f"redocking {config.site_ligand} put the top pose {top:.2f} Å from "
                            f"the crystal pose (threshold {REDOCK_RMSD} Å); the setup is not "
                            "validated and the scores below should not be ranked")
    else:
        warnings.append("no co-crystal ligand to redock: the setup is not validated")

    entries = prep.read_ligands(ligands) if isinstance(ligands, (str, Path)) else list(ligands)
    results: list[engine.DockResult] = []
    contacts: dict[str, list[interactions.Contact]] = {}
    failures: dict[str, str] = {}
    pose_dir = out / "poses"
    pose_dir.mkdir(exist_ok=True)
    from rdkit import Chem
    for name, smiles in entries:
        try:
            lig = prep.prepare_ligand(name, smiles, seed=config.seed)
            res = engine.dock(rec, lig, box, scoring=config.scoring,
                              exhaustiveness=config.exhaustiveness, n_poses=config.n_poses,
                              seed=config.seed)
        except prep.DockingError as exc:
            failures[name] = str(exc)
            continue
        results.append(res)
        writer = Chem.SDWriter(str(pose_dir / f"{name}.sdf"))
        for p in res.poses:
            m = Chem.Mol(p.mol)
            m.SetProp("_Name", f"{name} pose {p.rank}")
            m.SetProp("vina_score", f"{p.score:.3f}")
            writer.write(m)
        writer.close()
        if res.best is not None:
            contacts[name] = interactions.contacts(res.best.mol, rec.pdb)
    if failures:
        warnings.append(f"{len(failures)} ligands could not be docked: "
                        + "; ".join(f"{k}: {v}" for k, v in failures.items()))
    run = DockRun(out_dir=out, receptor=rec, box=box, results=results, validation=validation,
                  contacts=contacts, failures=failures, warnings=warnings)
    _write(run, config, started, entries)
    return run


def _efficiency(res: engine.DockResult) -> float | None:
    if res.best is None or not res.ligand.heavy_atoms:
        return None
    return round(-res.best.score / res.ligand.heavy_atoms, 3)


def _write(run: DockRun, config: DockConfig, started: str,
           entries: Sequence[tuple[str, str]]) -> None:
    out = run.out_dir
    ranked = sorted(run.results, key=lambda r: r.best.score if r.best else 0.0)
    with (out / "scores.tsv").open("w", encoding="utf-8") as fh:
        fh.write("ligand\tsmiles\tbest_score\tligand_efficiency\theavy_atoms\t"
                 "rotatable_bonds\tformal_charge\tposes\n")
        for r in ranked:
            fh.write(f"{r.ligand.name}\t{r.ligand.smiles}\t{r.best.score if r.best else 'NA'}\t"
                     f"{_efficiency(r)}\t{r.ligand.heavy_atoms}\t{r.ligand.rotatable_bonds}\t"
                     f"{r.ligand.formal_charge}\t{len(r.poses)}\n")
    plots: dict[str, str] = {}
    if ranked:
        plots["scores"] = svgplot.bars([r.ligand.name for r in ranked],
                                       [-(r.best.score if r.best else 0.0) for r in ranked],
                                       title="Best Vina score per ligand (higher bar = more "
                                             "favourable)", ylabel="-score (kcal/mol)")
    depictions = _depictions(ranked)
    md = _markdown(run, config, ranked)
    (out / "report.md").write_text(md, encoding="utf-8")
    html = _html(md, plots)
    if depictions:
        gallery = "".join(f'<figure style="display:inline-block;margin:8px">{svg}'
                          f"<figcaption>{name}</figcaption></figure>"
                          for name, svg in depictions.items())
        html = html.replace("</body>", f"<h2>Ligands</h2><div>{gallery}</div></body>")
    (out / "report.html").write_text(html, encoding="utf-8")
    pdir = out / "plots"
    pdir.mkdir(exist_ok=True)
    for name, svg in plots.items():
        (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
    outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
               if p.is_file() and p.name != "run.json"}
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    versions = {}
    for mod in ("vina", "meeko", "rdkit"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:                                     # noqa: BLE001
            versions[mod] = "unknown"
    manifest = {"pipeline": "bioagent.docking", "code_digest": code.hexdigest(),
                "started": started,
                "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "python": platform.python_version(), "versions": versions,
                "config": config.as_dict(), "receptor": run.receptor.source,
                "box": {"center": run.box.center, "size": run.box.size,
                        "source": run.box.source},
                "ligands": [{"name": n, "smiles": s} for n, s in entries],
                "validation": run.validation, "outputs": outputs, "warnings": run.warnings}
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")


def _depictions(ranked: Sequence[engine.DockResult]) -> dict[str, str]:
    try:
        from rdkit import Chem
        from rdkit.Chem.Draw import rdMolDraw2D
    except ImportError:                                       # pragma: no cover
        return {}
    out = {}
    for r in ranked[:24]:
        mol = Chem.MolFromSmiles(r.ligand.smiles)
        if mol is None:
            continue
        drawer = rdMolDraw2D.MolDraw2DSVG(220, 160)
        drawer.DrawMolecule(mol)
        drawer.FinishDrawing()
        svg = drawer.GetDrawingText()
        out[r.ligand.name] = svg[svg.index("<svg"):]
    return out


def _markdown(run: DockRun, config: DockConfig, ranked: Sequence[engine.DockResult]) -> str:
    v = run.validation
    lines = ["# Molecular docking", "",
             f"Receptor: {run.receptor.source.get('receptor', '')} (chains "
             f"{', '.join(run.receptor.chains)}, {run.receptor.residues} residues"
             + (f", cofactors {', '.join(run.receptor.cofactors)}" if run.receptor.cofactors
                else "") + "). "
             f"Site: {run.box.source}, centre ({', '.join(f'{c:.1f}' for c in run.box.center)}), "
             f"size ({', '.join(f'{c:.0f}' for c in run.box.size)}) Å. Scoring: "
             f"{config.scoring}, exhaustiveness {config.exhaustiveness}, seed {config.seed}.", ""]
    lines += ["## Validation", ""]
    if v:
        lines.append(f"Redocking {v['ligand']}: top pose {v['top_pose_rmsd']:.2f} Å from the "
                     f"crystal pose (best of the top three {v['best_rmsd_in_top3']:.2f} Å), "
                     f"score {v['top_score']}. **{'Validated' if v['passed'] else 'Not validated'}"
                     f"** (threshold {v['threshold']} Å).")
    else:
        lines.append("**Not validated**: no co-crystal ligand was named, so the setup was not "
                     "checked against a known pose.")
    lines.append("")
    if run.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in run.warnings] + [""]
    lines += ["## Scores", "",
              "| ligand | best score (kcal/mol) | ligand efficiency | heavy atoms | "
              "rotatable bonds | charge | contacts of the best pose |",
              "|---|---|---|---|---|---|---|"]
    for r in ranked:
        cs = run.contacts.get(r.ligand.name, [])
        kinds = {}
        for c in cs:
            kinds.setdefault(c.kind, set()).add(c.residue)
        summary = "; ".join(f"{k}: {', '.join(sorted(v)[:6])}" for k, v in sorted(kinds.items()))
        lines.append(f"| {r.ligand.name} | {r.best.score if r.best else 'NA'} | "
                     f"{_efficiency(r)} | {r.ligand.heavy_atoms} | {r.ligand.rotatable_bonds} | "
                     f"{r.ligand.formal_charge} | {summary or '-'} |")
    lines += ["", "Poses are in `poses/<ligand>.sdf` (every pose, with its score); the prepared "
              "receptor is `receptor.pdbqt`.", "", "![scores](plots/scores.svg)", "",
              "## Methods", "",
              "- Receptor: waters and unnamed hetero groups removed; meeko residue templates, "
              "polar hydrogens, Gasteiger charges.",
              "- Ligands: RDKit ETKDG v3 conformer and MMFF94 relaxation; meeko PDBQT; "
              "protonation as given (no pKa model).",
              f"- AutoDock Vina 1.2 ({config.scoring} scoring), rigid receptor.",
              "- Contacts: heavy-atom distances (H-bond 3.5 Å, salt bridge 4.0 Å, hydrophobic "
              "4.0 Å).", "",
              "## What a result is", "",
              "A docking score is the scoring function's estimate, not a measured affinity: "
              "it tracks measured binding with a correlation near 0.5 and errors of about "
              "2 kcal/mol. A pose is a hypothesis about how a molecule could sit in the site. "
              "The receptor is rigid and the protonation states are as given. A promising "
              "score is a reason to test a compound, not evidence that it binds.", "",
              "## Reproducing it", "",
              "`run.json` records the receptor's digest, the box, every ligand, the seed and "
              "each output's digest; `bioagent dock --verify <out>` re-checks the outputs.", ""]
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
