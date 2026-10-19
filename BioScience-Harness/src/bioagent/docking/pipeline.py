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

Between docking and contacts, every pose gets cheap sanity checks (``posecheck``: finite
coordinates, heavy atoms inside the box, no ligand-receptor heavy-atom pair closer than
2.2 Å). They are not a validation of binding.

Every stage is recorded (``bioagent.runrecord``; the names are ``STAGES``) with its status,
check, engine, version, device and time, in ``stages.json`` while the run works and in
``run.json`` at the end, with ``complete`` and ``limitations.md``. The run is incomplete,
and ``bioagent dock`` exits 1, when no ligand was docked, when redocking the co-crystal
ligand misses 2 Å, or when a ligand's best pose fails its checks.

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
from typing import Any, Mapping, Sequence

import numpy as np

from .. import __version__ as _VERSION
from ..omics import svgplot
from ..omics.rnaseq import _html, _sha256
from ..runrecord import (LIMITATIONS_FILE, STAGES_FILE, StageRecorder, output_problems,
                         record_problems, stage_limitations, tsv_shape, write_limitations)
from . import engine, interactions, posecheck, prep

__all__ = ["DockConfig", "DockRun", "run_docking", "verify_run", "STAGES", "LIMITATIONS",
           "REDOCK_RMSD"]

REDOCK_RMSD = 2.0
PIPELINE = "bioagent.docking"
#: The stages of a run, in order. ``redock_validation`` is skipped without a co-crystal
#: ligand (the setup is then not validated), and is a FAIL when the top pose misses
#: REDOCK_RMSD; ``pose_checks`` is a FAIL when a ligand's best pose fails them.
STAGES = ("receptor_prep", "ligand_prep", "site", "redock_validation", "dock", "pose_checks",
          "contacts", "report", "verify")
#: What every result of this pipeline is not, one line each (``limitations.md``).
LIMITATIONS = (
    "A docking score is the scoring function's estimate in kcal/mol, not a measured binding "
    "affinity: across benchmark sets it tracks measured affinities with a correlation near "
    "0.5 and errors of about 2 kcal/mol.",
    "A pose is a hypothesis about how a molecule could sit in the site, docked into a rigid "
    "receptor with protonation states as given (no pKa model); a hit needs an assay.",
    "The pose checks (finite coordinates, heavy atoms inside the box, no ligand-receptor "
    "heavy-atom pair closer than 2.2 Å) are sanity checks, not a validation of binding.",
)
SCORES_HEADER = ("ligand", "smiles", "best_score", "ligand_efficiency", "heavy_atoms",
                 "rotatable_bonds", "formal_charge", "poses", "pose_check",
                 "min_receptor_distance")
_UNLISTED = {"run.json", STAGES_FILE}


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
    pose_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    limitations: list[str] = field(default_factory=list)

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


def _module_version(name: str) -> str | None:
    try:
        return str(__import__(name).__version__)
    except Exception:                                         # noqa: BLE001
        return None


def _input_digests(receptor: str, ligands: Any) -> dict[str, str]:
    """{path: sha256} of the input files (a receptor fetched by id and ligands given as
    SMILES are recorded in run.json's receptor and ligands instead)."""
    out = {}
    for value in (receptor, ligands):
        try:
            is_file = isinstance(value, (str, Path)) and Path(str(value)).is_file()
        except (OSError, ValueError):                  # a long SMILES is no file name
            is_file = False
        if is_file:
            out[str(value)] = _sha256(Path(str(value)))
    return out


def run_docking(receptor: str, ligands: str | Sequence[tuple[str, str]], config: DockConfig,
                out_dir: str | Path) -> DockRun:
    """Every stage, recorded as it runs (``runrecord.StageRecorder``). The run is complete
    only when the receptor and at least one ligand were docked, every best pose passed
    its checks and, when a co-crystal ligand was named, redocking it came within
    ``REDOCK_RMSD``; ``DockRun.complete`` says which."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = StageRecorder(out, PIPELINE, plan=STAGES)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warnings: list[str] = []
    inputs = _input_digests(receptor, ligands)

    with rec.stage("receptor_prep", engine="meeko", version=_module_version("meeko")) as st:
        text, source, chains = _receptor_text(receptor, config)
        receptor_obj = prep.prepare_receptor(text, chains=chains or None,
                                             cofactors=config.cofactors, source=source)
        if receptor_obj.unmatched:
            warnings.append(f"{len(receptor_obj.unmatched)} receptor residues matched no "
                            "template and were left out")
        (out / "receptor.pdbqt").write_text(receptor_obj.pdbqt, encoding="utf-8")
        (out / "receptor_clean.pdb").write_text(receptor_obj.pdb, encoding="utf-8")
        st.outputs = ["receptor.pdbqt", "receptor_clean.pdb"]
        st.reason = (f"chains {', '.join(receptor_obj.chains)}, {receptor_obj.residues} "
                     f"residues; {len(receptor_obj.unmatched)} without a template")

    entries = prep.read_ligands(ligands) if isinstance(ligands, (str, Path)) else list(ligands)
    failures: dict[str, str] = {}
    prepared: list[prep.Ligand] = []
    with rec.stage("ligand_prep", engine="rdkit+meeko",
                   version=f"rdkit {_module_version('rdkit')}; meeko "
                           f"{_module_version('meeko')}") as st:
        for name, smiles in entries:
            try:
                prepared.append(prep.prepare_ligand(name, smiles, seed=config.seed))
            except prep.DockingError as exc:
                failures[name] = str(exc)
        st.reason = f"{len(prepared)} of {len(entries)} ligands prepared (ETKDG v3, MMFF94)"
        if not prepared:
            st.status = "failed"
            st.reason = (f"none of the {len(entries)} ligands could be prepared"
                         if entries else "no ligand was given")

    with rec.stage("site", engine=PIPELINE, version=_VERSION) as st:
        box, reference = _site(text, config)
        st.reason = (f"{box.source}: centre ({', '.join(f'{c:.1f}' for c in box.center)}), "
                     f"size ({', '.join(f'{c:.0f}' for c in box.size)}) Å")

    validation = None
    if reference is not None:
        with rec.stage("redock_validation", engine="vina",
                       version=_module_version("vina")) as st:
            from rdkit import Chem
            smiles = config.site_ligand_smiles or prep.ccd_smiles(config.site_ligand)
            lig = prep.prepare_ligand(f"redock_{config.site_ligand}", smiles, seed=config.seed)
            res = engine.dock(receptor_obj, lig, box, scoring=config.scoring,
                              exhaustiveness=config.exhaustiveness, n_poses=config.n_poses,
                              seed=config.seed)
            rmsds = [engine.pose_rmsd(p.mol, reference) for p in res.poses]
            for p, r in zip(res.poses, rmsds):
                p.rmsd_to_reference = r
            top = rmsds[0] if rmsds else float("inf")
            validation = {"ligand": config.site_ligand, "smiles": smiles,
                          "top_pose_rmsd": top,
                          "best_rmsd_in_top3": min(rmsds[:3]) if rmsds else None,
                          "top_score": res.poses[0].score if res.poses else None,
                          "threshold": REDOCK_RMSD, "passed": top <= REDOCK_RMSD,
                          "poses": [{"rank": p.rank, "score": p.score,
                                     "rmsd": p.rmsd_to_reference} for p in res.poses]}
            crystal = f"crystal_{config.site_ligand}.mol"
            Chem.MolToMolFile(reference, str(out / crystal))
            st.outputs = [crystal]
            st.check = "PASS" if validation["passed"] else "FAIL"
            st.reason = (f"top pose {top:.2f} Å from the crystal pose of "
                         f"{config.site_ligand} (heavy-atom RMSD, symmetry-aware; threshold "
                         f"{REDOCK_RMSD} Å)")
            if not validation["passed"]:
                warnings.append(f"redocking {config.site_ligand} put the top pose {top:.2f} Å "
                                f"from the crystal pose (threshold {REDOCK_RMSD} Å); the setup "
                                "is not validated and the scores below should not be ranked")
    else:
        warnings.append("no co-crystal ligand to redock: the setup is not validated")
        rec.skip("redock_validation", "no co-crystal ligand was named, so the setup was not "
                                      "checked against a known pose")

    results: list[engine.DockResult] = []
    contacts: dict[str, list[interactions.Contact]] = {}
    pose_dir = out / "poses"
    with rec.stage("dock", engine="vina", version=_module_version("vina")) as st:
        from rdkit import Chem
        pose_dir.mkdir(exist_ok=True)
        for lig in prepared:
            try:
                res = engine.dock(receptor_obj, lig, box, scoring=config.scoring,
                                  exhaustiveness=config.exhaustiveness,
                                  n_poses=config.n_poses, seed=config.seed)
            except prep.DockingError as exc:
                failures[lig.name] = str(exc)
                continue
            results.append(res)
            writer = Chem.SDWriter(str(pose_dir / f"{lig.name}.sdf"))
            for p in res.poses:
                m = Chem.Mol(p.mol)
                m.SetProp("_Name", f"{lig.name} pose {p.rank}")
                m.SetProp("vina_score", f"{p.score:.3f}")
                writer.write(m)
            writer.close()
            st.outputs.append(f"poses/{lig.name}.sdf")
        st.reason = (f"{len(results)} of {len(entries)} ligands docked ({config.scoring}, "
                     f"exhaustiveness {config.exhaustiveness}, seed {config.seed})")
        if not results:
            st.status = "failed"
            st.reason = (f"no ligand was docked: {len(failures)} of {len(entries)} failed"
                         if entries else "no ligand was given")
    if failures:
        warnings.append(f"{len(failures)} ligands could not be docked: "
                        + "; ".join(f"{k}: {v}" for k, v in failures.items()))

    pose_report: dict[str, dict[str, Any]] = {}
    if results:
        with rec.stage("pose_checks", engine="bioagent.docking.posecheck",
                       version=_VERSION) as st:
            rec_xyz = posecheck.receptor_heavy_atoms(receptor_obj.pdb)
            bad = []
            for res in results:
                checks = [posecheck.check_pose(p.mol, rec_xyz, box) for p in res.poses]
                pose_report[res.ligand.name] = {
                    "best": checks[0] if checks else None,
                    "poses_failed": [p.rank for p, c in zip(res.poses, checks)
                                     if not c["passed"]]}
                if not checks or not checks[0]["passed"]:
                    bad.append(f"{res.ligand.name} ("
                               + (", ".join(checks[0]["failed"]) if checks else "no pose")
                               + ")")
            st.check = "FAIL" if bad else "PASS"
            st.reason = ((f"best pose of {', '.join(bad)} failed" if bad else
                          f"the best pose of every ligand passed") +
                         f": finite coordinates, heavy atoms inside the box (+-"
                         f"{posecheck.BOX_TOLERANCE} Å), no ligand-receptor heavy-atom pair "
                         f"closer than {posecheck.CLASH_DISTANCE} Å (metals left out); a "
                         "sanity check, not a validation of binding")
            if bad:
                warnings.append("the best pose of " + ", ".join(bad) + " failed the pose "
                                "checks; its score describes a pose that cannot be right")
        with rec.stage("contacts", engine="bioagent.docking.interactions",
                       version=_VERSION) as st:
            for res in results:
                if res.best is not None:
                    contacts[res.ligand.name] = interactions.contacts(res.best.mol,
                                                                      receptor_obj.pdb)
            st.reason = (f"contacts of {len(contacts)} best poses (H-bond 3.5 Å, salt bridge "
                         "4.0 Å, hydrophobic 4.0 Å)")
    else:
        rec.not_applicable("pose_checks", "no pose to check")
        rec.not_applicable("contacts", "no pose to describe")
    run = DockRun(out_dir=out, receptor=receptor_obj, box=box, results=results,
                  validation=validation, contacts=contacts, failures=failures,
                  warnings=warnings)
    run.pose_checks = pose_report
    _write(run, config, started, entries, rec, inputs)
    return run


def _efficiency(res: engine.DockResult) -> float | None:
    if res.best is None or not res.ligand.heavy_atoms:
        return None
    return round(-res.best.score / res.ligand.heavy_atoms, 3)


def _limitations(run: DockRun, rec: StageRecorder) -> list[str]:
    lines = list(LIMITATIONS)
    if not run.validated:
        lines.append("The setup was not validated by redocking a co-crystal ligand within "
                     f"{REDOCK_RMSD} Å, so the scores are not from a validated setup and "
                     "should not be ranked as such.")
    if run.failures:
        lines.append(f"{len(run.failures)} ligands could not be prepared or docked and have "
                     "no score: " + ", ".join(sorted(run.failures)) + ".")
    lines += stage_limitations(rec.stages)
    return lines


def _pose_cell(run: DockRun, name: str, key: str) -> str:
    best = (run.pose_checks.get(name) or {}).get("best") or {}
    if key == "check":
        return ("pass" if best.get("passed") else
                "fail: " + ", ".join(best.get("failed") or ["no pose"]))
    value = best.get("min_receptor_distance")
    return "NA" if value is None else f"{value:.2f}"


def _write(run: DockRun, config: DockConfig, started: str,
           entries: Sequence[tuple[str, str]], rec: StageRecorder,
           inputs: Mapping[str, str]) -> None:
    out = run.out_dir
    ranked = sorted(run.results, key=lambda r: r.best.score if r.best else 0.0)
    with rec.stage("report", engine=PIPELINE, version=_VERSION) as st:
        with (out / "scores.tsv").open("w", encoding="utf-8") as fh:
            fh.write("\t".join(SCORES_HEADER) + "\n")
            for r in ranked:
                fh.write(f"{r.ligand.name}\t{r.ligand.smiles}\t"
                         f"{r.best.score if r.best else 'NA'}\t{_efficiency(r)}\t"
                         f"{r.ligand.heavy_atoms}\t{r.ligand.rotatable_bonds}\t"
                         f"{r.ligand.formal_charge}\t{len(r.poses)}\t"
                         f"{_pose_cell(run, r.ligand.name, 'check')}\t"
                         f"{_pose_cell(run, r.ligand.name, 'distance')}\n")
        plots: dict[str, str] = {}
        if ranked:
            plots["scores"] = svgplot.bars([r.ligand.name for r in ranked],
                                           [-(r.best.score if r.best else 0.0) for r in ranked],
                                           title="Best Vina score per ligand (higher bar = "
                                                 "more favourable)", ylabel="-score (kcal/mol)")
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
        run.limitations = write_limitations(out, _limitations(run, rec))
        st.outputs = ["scores.tsv", "report.md", "report.html", LIMITATIONS_FILE,
                      *(f"plots/{name}.svg" for name in plots)]
    code = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        code.update(p.name.encode() + b"\0" + p.read_bytes())
    versions = {mod: _module_version(mod) or "unknown" for mod in ("vina", "meeko", "rdkit")}
    manifest: dict[str, Any] = {}
    with rec.stage("verify", engine=PIPELINE, version=_VERSION) as st:
        outputs = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
                   if p.is_file() and p.name not in _UNLISTED}
        manifest.update({
            "pipeline": PIPELINE, "code_digest": code.hexdigest(), "started": started,
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "versions": versions,
            "config": config.as_dict(), "receptor": run.receptor.source,
            "box": {"center": run.box.center, "size": run.box.size,
                    "source": run.box.source},
            "ligands": [{"name": n, "smiles": s} for n, s in entries],
            "validation": run.validation, "outputs": outputs, "warnings": run.warnings,
            "inputs": dict(inputs),
            "results": [{"ligand": r.ligand.name, "smiles": r.ligand.smiles,
                         "best_score": r.best.score if r.best else None,
                         "poses": len(r.poses),
                         "pose_checks": run.pose_checks.get(r.ligand.name)}
                        for r in ranked],
            "failures": dict(run.failures),
            "pose_checks": {"clash_distance": posecheck.CLASH_DISTANCE,
                            "box_tolerance": posecheck.BOX_TOLERANCE,
                            "what": "finite coordinates, heavy atoms inside the box, no "
                                    "ligand-receptor heavy-atom pair closer than the clash "
                                    "distance (metal ions left out); not a validation of "
                                    "binding"}})
        found = _content_problems(out, manifest) + output_problems(rec.stages, outputs, out)
        st.check = "FAIL" if found else "PASS"
        st.reason = ("; ".join(found)[:600] if found else
                     f"{len(outputs)} outputs digested; scores.tsv agrees with the run record")
    run.stages = rec.as_list()
    run.complete = rec.complete
    manifest.update({"stages": run.stages, "complete": run.complete,
                     "limitations": run.limitations})
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")
    rec.finish()


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
              "rotatable bonds | charge | pose check | contacts of the best pose |",
              "|---|---|---|---|---|---|---|---|"]
    for r in ranked:
        cs = run.contacts.get(r.ligand.name, [])
        kinds = {}
        for c in cs:
            kinds.setdefault(c.kind, set()).add(c.residue)
        summary = "; ".join(f"{k}: {', '.join(sorted(v)[:6])}" for k, v in sorted(kinds.items()))
        lines.append(f"| {r.ligand.name} | {r.best.score if r.best else 'NA'} | "
                     f"{_efficiency(r)} | {r.ligand.heavy_atoms} | {r.ligand.rotatable_bonds} | "
                     f"{r.ligand.formal_charge} | {_pose_cell(run, r.ligand.name, 'check')} | "
                     f"{summary or '-'} |")
    lines += ["", "Poses are in `poses/<ligand>.sdf` (every pose, with its score); the prepared "
              "receptor is `receptor.pdbqt`.", "", "![scores](plots/scores.svg)", "",
              "## Methods", "",
              "- Receptor: waters and unnamed hetero groups removed; meeko residue templates, "
              "polar hydrogens, Gasteiger charges.",
              "- Ligands: RDKit ETKDG v3 conformer and MMFF94 relaxation; meeko PDBQT; "
              "protonation as given (no pKa model).",
              f"- AutoDock Vina 1.2 ({config.scoring} scoring), rigid receptor.",
              "- Contacts: heavy-atom distances (H-bond 3.5 Å, salt bridge 4.0 Å, hydrophobic "
              "4.0 Å).",
              f"- Pose checks: finite coordinates, every ligand heavy atom inside the box "
              f"(within {posecheck.BOX_TOLERANCE} Å), no ligand-receptor heavy-atom pair "
              f"closer than {posecheck.CLASH_DISTANCE} Å (metal ions left out). They are "
              "sanity checks, not PoseBusters and not a validation of binding; Vina's ranking "
              "is kept as it is.", "",
              "## What a result is", "",
              "A docking score is the scoring function's estimate, not a measured affinity: "
              "it tracks measured binding with a correlation near 0.5 and errors of about "
              "2 kcal/mol. A pose is a hypothesis about how a molecule could sit in the site. "
              "The receptor is rigid and the protonation states are as given. A promising "
              "score is a reason to test a compound, not evidence that it binds.", "",
              "## Reproducing it", "",
              "`run.json` records the receptor's digest, the box, every ligand, the seed, "
              "each output's digest and each stage's status, engine, device and time; "
              "`limitations.md` lists this run's limitations. `bioagent dock --verify <out>` "
              "re-checks the outputs, that every required stage succeeded and that "
              "scores.tsv agrees with the record.", ""]
    return "\n".join(lines)


def _content_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    """scores.tsv against the run record: this version's columns, one row per docked
    ligand (at least one), and the poses of each."""
    found = []
    results = manifest.get("results") or []
    path = out / "scores.tsv"
    if not path.is_file():
        return ["scores.tsv is missing"]
    header, rows = tsv_shape(path)
    if header != list(SCORES_HEADER):
        found.append(f"scores.tsv: its columns are not {', '.join(SCORES_HEADER)}")
    if rows != len(results):
        found.append(f"scores.tsv: {rows} rows, where the run record has {len(results)} "
                     "docked ligands")
    if not results:
        found.append("no ligand was docked")
    for r in results:
        if f"poses/{r.get('ligand')}.sdf" not in (manifest.get("outputs") or {}):
            found.append(f"poses/{r.get('ligand')}.sdf is not among the outputs")
    return found


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-hash every recorded output (and input file, when the record lists them); for a
    record with stages, also check that the run is complete and that scores.tsv agrees
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
