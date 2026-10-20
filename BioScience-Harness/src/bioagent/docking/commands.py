"""Command-line entry point for docking (``bioagent dock``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

__all__ = ["register", "dispatch", "COMMANDS"]

COMMANDS = ("dock",)


def _triple(text: str) -> tuple[float, float, float]:
    parts = [float(v) for v in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("three comma-separated numbers")
    return parts[0], parts[1], parts[2]


def register(sub: argparse._SubParsersAction) -> None:
    d = sub.add_parser("dock", help="dock ligands into a receptor with AutoDock Vina, with a "
                                    "redocking check of the setup")
    d.add_argument("receptor", nargs="?", default="",
                   help="a PDB file, PDB:<id>[:chains] or UniProt:<acc> (AlphaFold DB); or a "
                        "prepared rigid receptor, a .pdbqt file, for the prepared mode")
    d.add_argument("ligands", nargs="?", default="",
                   help="a CSV (name,smiles), an SDF, a .smi file or one SMILES; in the "
                        "prepared mode a .pdbqt of one ligand or several (MODEL blocks)")
    d.add_argument("--out", default="")
    d.add_argument("--reference-ligand", default="",
                   help="prepared mode: the co-crystal ligand as a .pdbqt in its crystal pose; "
                        "the box is built around it unless --center and --size are given, and "
                        "it is redocked to validate the setup")
    d.add_argument("--site-ligand", default="",
                   help="residue name of a co-crystal ligand: the box is built around it and "
                        "it is redocked to validate the setup")
    d.add_argument("--site-ligand-smiles", default="",
                   help="its SMILES (default: the RCSB Chemical Component Dictionary's)")
    d.add_argument("--center", type=_triple, default=None, help="box centre x,y,z")
    d.add_argument("--size", type=_triple, default=None, help="box size x,y,z (Å)")
    d.add_argument("--site-residue", action="append", default=[],
                   help="chain:number of a site residue; repeatable")
    d.add_argument("--chains", default="", help="protein chains to keep, e.g. AB")
    d.add_argument("--cofactor", action="append", default=[], help="hetero group to keep")
    d.add_argument("--scoring", default="vina", choices=("vina", "vinardo"))
    d.add_argument("--exhaustiveness", type=int, default=8)
    d.add_argument("--poses", type=int, default=9)
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--allow-remote", action="store_true",
                   help="permit fetching the receptor or ligand definitions from RCSB / "
                        "AlphaFold DB")
    d.add_argument("--verify", default="", help="re-check a finished run directory and exit")


def dispatch(a: argparse.Namespace) -> int | None:
    if a.cmd != "dock":
        return None
    from .pipeline import DockConfig, run_docking, verify_run
    from .prep import DockingError

    if a.verify:
        ok, problems = _verify(a.verify, verify_run)
        print("verified: every output matches run.json" if ok
              else "NOT VERIFIED:\n" + "\n".join(f"  - {p}" for p in problems))
        return 0 if ok else 1
    if not (a.receptor and a.ligands and a.out):
        print("a receptor, ligands and --out are required (or --verify DIR)", file=sys.stderr)
        return 2
    if a.receptor.lower().endswith(".pdbqt"):
        return _dock_prepared(a)
    if a.reference_ligand:
        print("--reference-ligand is for prepared inputs (a .pdbqt receptor); with a PDB "
              "receptor name the co-crystal ligand with --site-ligand", file=sys.stderr)
        return 2
    config = DockConfig(site_ligand=a.site_ligand or None,
                        site_ligand_smiles=a.site_ligand_smiles or None,
                        box_center=a.center, box_size=a.size,
                        site_residues=tuple(a.site_residue), chains=tuple(a.chains),
                        cofactors=tuple(a.cofactor), scoring=a.scoring,
                        exhaustiveness=a.exhaustiveness, n_poses=a.poses, seed=a.seed,
                        allow_remote=a.allow_remote)
    try:
        run = run_docking(a.receptor, a.ligands, config, a.out)
    except (DockingError, ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    v = run.validation
    print("validation: " + (f"redocked {v['ligand']} to {v['top_pose_rmsd']:.2f} Å "
                            f"({'passed' if v['passed'] else 'FAILED'})" if v
                            else "none (no co-crystal ligand)"))
    for r in sorted(run.results, key=lambda r: r.best.score if r.best else 0):
        print(f"  {r.ligand.name}: {r.best.score if r.best else 'NA'} kcal/mol")
    for w in run.warnings:
        print(f"warning: {w}")
    print(f"report: {Path(a.out) / 'report.html'}")
    if not run.complete:
        # no ligand docked, redocking missed 2 Å or a best pose failed its checks: the
        # files stay for inspection, and the exit says the run is not a result
        from ..runrecord import problems
        print("incomplete: " + "; ".join(problems(run.stages) or ["no stage was recorded"]),
              file=sys.stderr)
        return 1
    return 0


def _verify(out: str, verify_run):
    """The run's own ``verify_run``: run.json names the pipeline (the prepared mode's or
    the full pipeline's)."""
    import json
    from . import prepared
    try:
        name = json.loads((Path(out) / "run.json").read_text(encoding="utf-8")).get("pipeline")
    except (OSError, ValueError, AttributeError):
        name = None
    return (prepared.verify_run if name == prepared.PIPELINE else verify_run)(out)


def _dock_prepared(a: argparse.Namespace) -> int:
    """``bioagent dock RECEPTOR.pdbqt LIGANDS.pdbqt``: prepared inputs, used as given; the
    site is --center/--size or --reference-ligand (bioagent.docking.prepared)."""
    from .prep import DockingError
    from .prepared import PreparedDockConfig, run_prepared_docking

    given = [flag for flag, value in (("--site-ligand", a.site_ligand),
                                      ("--site-ligand-smiles", a.site_ligand_smiles),
                                      ("--site-residue", a.site_residue),
                                      ("--chains", a.chains), ("--cofactor", a.cofactor),
                                      ("--allow-remote", a.allow_remote)) if value]
    if given:
        print(f"a .pdbqt receptor is used as prepared, so {', '.join(given)} cannot apply: "
              "name the site with --center and --size, or --reference-ligand (a .pdbqt of "
              "the co-crystal ligand in its crystal pose)", file=sys.stderr)
        return 2
    for what, path in (("the ligands", a.ligands), ("--reference-ligand", a.reference_ligand)):
        if path and not path.lower().endswith(".pdbqt"):
            print(f"with a .pdbqt receptor, {what} must be a .pdbqt file as well (prepared: "
                  "one ligand, or several as MODEL blocks); preparing from SMILES, CSV or SDF "
                  "needs a PDB receptor", file=sys.stderr)
            return 2
    try:
        config = PreparedDockConfig(center=a.center, size=a.size, scoring=a.scoring,
                                    exhaustiveness=a.exhaustiveness, n_poses=a.poses,
                                    seed=a.seed)
        run = run_prepared_docking(a.receptor, a.ligands, config, a.out,
                                   reference_path=a.reference_ligand or None)
    except (DockingError, ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    v = run.validation
    print("validation: " + ("none (no reference ligand)" if not v else
                            f"redocking {v['ligand']} refused by Vina" if v.get("error") else
                            f"redocked {v['ligand']} to {v['top_pose_rmsd']:.2f} Å, heavy atoms "
                            f"in atom order ({'passed' if v['passed'] else 'FAILED'})"))
    for r in sorted(run.results, key=lambda r: r.best_raw):
        print(f"  {r.ligand.name}: {r.best_score:.3f} kcal/mol")
    for w in run.warnings:
        print(f"warning: {w}")
    print(f"report: {Path(a.out) / 'report.html'}")
    if not run.complete:
        from ..runrecord import problems
        print("incomplete: " + "; ".join(problems(run.stages) or ["no stage was recorded"]),
              file=sys.stderr)
        return 1
    return 0
