"""Case 3: a compound and a target → Open Targets → docking and ADMET → a hypothesis report.

The question: is 4-aminobenzamidine worth testing against trypsin-1 (PRSS1), the protease
whose gain-of-function variants cause hereditary pancreatitis? The case runs:

1. **Open Targets**, from a recorded answer (``tests/fixtures/cases/opentargets_prss1.json``,
   API 26.9, data 26.09): the diseases associated with PRSS1, with each evidence datatype
   kept apart. The disease is chosen by its *genetic* score. The overall score mixes in
   literature co-mention, and a target chosen on that would be a target chosen for being
   much written about.
2. **Docking** (``bioagent.docking``) into the 3PTB pocket, after redocking its co-crystal
   ligand benzamidine validates the setup. 3PTB is bovine trypsin. The case docks into a
   model of the human target, and the report says so.
3. **ADMET** rules and alerts (``bioagent.admet``), and the TDC endpoint models when a
   directory of built models is given (``admet_models``), each prediction with its
   applicability domain. By default the case uses an empty directory of its own, so no
   endpoint is predicted, and the report says that too.
4. **Complex prediction** with Boltz (``bioagent.structure.engines``). Boltz is not
   installed here, so the step is UNAVAILABLE. Nothing is approximated in its place.
5. **The hypothesis report**: drafted claims through the claim contract. Docking licenses
   a mechanism hypothesis. An Open Targets association is an aggregate score, which no
   claim may cite as evidence. So the hypothesis is allowed, and the same finding stated
   as a mechanism or as a treatment is refused.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..status import ExecutionStatus
from .report import CaseReport, CaseStep, _versions, check_drafts

__all__ = ["COMPOUND", "VALIDATION_LIGAND", "run_case", "open_targets_rows"]

COMPOUND = ("4-aminobenzamidine", "NC(=N)c1ccc(N)cc1")
VALIDATION_LIGAND = ("benzamidine", "NC(=N)c1ccccc1")
_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
POCKET = _FIXTURES / "docking" / "3ptb_pocket.pdb"
OPEN_TARGETS = _FIXTURES / "cases" / "opentargets_prss1.json"


def open_targets_rows(answer: dict[str, Any]) -> list[dict[str, Any]]:
    """Each associated disease with its overall score and its datatypes, kept apart."""
    target = answer["response"]["data"]["target"]
    return [{"disease": row["disease"]["name"], "id": row["disease"]["id"],
             "overall": row["score"],
             "datatypes": {d["id"]: d["score"] for d in row["datatypeScores"]}}
            for row in target["associatedDiseases"]["rows"]]


def run_case(work_dir: str | Path, *, open_targets: str | Path = OPEN_TARGETS,
             pocket: str | Path = POCKET, admet_models: str | Path | None = None
             ) -> CaseReport:
    from ..contracts import CandidateClaim, EvidenceItem
    from ..omics.optional import BackendUnavailable

    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    name, smiles = COMPOUND
    report = CaseReport(
        case="compound-hypothesis", title="A compound and its target to a hypothesis: "
        "Open Targets, docking, ADMET, the claim contract",
        inputs="4-aminobenzamidine; a recorded Open Targets answer for PRSS1 (API 26.9, "
               "data 26.09); the 3PTB trypsin pocket (bovine) with its co-crystal "
               "benzamidine")

    # 1. Open Targets: the disease chosen by genetic evidence, every datatype reported.
    answer = json.loads(Path(open_targets).read_text(encoding="utf-8"))
    rows = open_targets_rows(answer)
    chosen = max(rows, key=lambda r: r["datatypes"].get("genetic_association", 0.0))
    literature_led = [r["disease"] for r in rows
                      if r["datatypes"].get("literature", 0.0)
                      > r["datatypes"].get("genetic_association", 0.0)]
    meta = answer["response"]["data"]["meta"]
    report.steps.append(CaseStep(
        "Open Targets evidence by datatype", ExecutionStatus.SUCCEEDED,
        f"Open Targets API {'.'.join(meta['apiVersion'].values())}, data "
        f"{meta['dataVersion']['year']}.{meta['dataVersion']['month']} (recorded "
        f"{answer['recorded_at']})",
        f"chosen: {chosen['disease']}, genetic association "
        f"{chosen['datatypes'].get('genetic_association', 0):.3f}, literature "
        f"{chosen['datatypes'].get('literature', 0):.3f}; associations led by literature "
        f"co-mention rather than genetics: {', '.join(literature_led) or 'none'}",
        {"chosen": chosen, "rows": rows,
         "answer_sha256": hashlib.sha256(Path(open_targets).read_bytes()).hexdigest()}))

    # 2. Docking, validated by redocking the co-crystal ligand first.
    try:
        from ..docking.pipeline import DockConfig, run_docking
        dock = run_docking(str(pocket), [COMPOUND, VALIDATION_LIGAND],
                           DockConfig(site_ligand="BEN", site_ligand_smiles=VALIDATION_LIGAND[1]),
                           work / "docking")
    except (BackendUnavailable, ImportError, RuntimeError) as exc:
        report.steps.append(CaseStep("docking", ExecutionStatus.UNAVAILABLE, "",
                                     f"{type(exc).__name__}: {exc}"[:300]))
        report.limits.append("docking did not run, so no hypothesis is drafted")
        return report
    tools = _versions("vina", "meeko", "rdkit", "gemmi")
    best = {r.ligand.name: r.best for r in dock.results}
    pose = best.get(name)
    validation = dock.validation or {}
    report.steps.append(CaseStep(
        "docking", ExecutionStatus.SUCCEEDED if pose and dock.validated
        else ExecutionStatus.FAILED, " ".join(f"{k} {v}" for k, v in tools.items()),
        f"redocking benzamidine: RMSD {validation.get('top_pose_rmsd', float('nan')):.2f} Å "
        f"({'passed' if dock.validated else 'failed'}); {name} top score "
        f"{pose.score if pose else float('nan'):.2f} kcal/mol (Vina's estimate)",
        {"validation": validation, "box": dock.box.source, "failures": dock.failures}))
    if not (pose and dock.validated):
        report.limits.append("the setup did not validate, so no hypothesis is drafted")
        return report

    # 3. ADMET: rules and alerts, and no endpoint unless models were built.
    try:
        from ..admet.pipeline import AdmetConfig, run_admet
        cache = Path(admet_models) if admet_models else work / "admet-models"
        admet = run_admet([COMPOUND], AdmetConfig(cache_dir=str(cache)), work / "admet")
        molecule = admet.molecules[0] if admet.molecules else {}
        predicted = molecule.get("predictions") or {}
        outside = sorted(e for e, p in predicted.items() if not p["in_domain"])
        report.steps.append(CaseStep(
            "ADMET", ExecutionStatus.SUCCEEDED, " ".join(
                f"{k} {v}" for k, v in _versions("rdkit", "scikit-learn").items()),
            "rules and alerts only: " + "; ".join(admet.warnings)
            if not admet.models_built else
            f"{len(admet.cards)} endpoints predicted from TDC models; outside the "
            f"applicability domain: {', '.join(outside) or 'none'}",
            {"molecule": {k: molecule.get(k) for k in ("name", "smiles", "properties",
                                                         "rules", "alerts", "predictions")
                          if k in molecule},
             "models_built": admet.models_built,
             "model_cards": {e: {k: c.get(k) for k in ("metric", "test_score", "train",
                                                       "model_sha256", "archive_is_pinned")}
                             for e, c in admet.cards.items()}}))
    except (BackendUnavailable, ImportError, RuntimeError) as exc:
        report.steps.append(CaseStep("ADMET", ExecutionStatus.UNAVAILABLE, "",
                                     f"{type(exc).__name__}: {exc}"[:300]))

    # 4. Complex prediction: refused, with its reason, when the engine is absent.
    report.steps.append(_complex_prediction(work))

    # 5. The hypothesis report.
    summary = (f"{name} docked into the 3PTB S1 pocket (bovine trypsin) with a top Vina "
               f"score of {pose.score:.2f} kcal/mol; redocking benzamidine reproduced its "
               f"crystal pose within {validation['top_pose_rmsd']:.2f} Å.\n")
    (work / "docking_summary.txt").write_text(summary, encoding="utf-8")
    docked = EvidenceItem(
        id="dock", design="docking", quote=summary.strip(), source_card_id="case.docking",
        identifier=f"sha256:{hashlib.sha256(summary.encode()).hexdigest()}",
        identifier_type="local_artifact", subject=name,
        outcome="trypsin S1 pocket (3PTB, bovine)",
        retracted="not_retracted").located_in(summary)
    evidence = {"dock": docked}

    def claim(text: str, kind: str, population: str = "", outcome: str = "") -> Any:
        return CandidateClaim(
            id=f"c.{kind}.{len(text)}", text=text, claim_kind=kind, subject=name,
            supports=("dock",), asserted_population=population,
            supported_population=population, asserted_outcome=outcome,
            supported_outcome=outcome, confidence=0.3,
            confidence_basis="one docking run with a validated setup",
            falsified_by="an enzyme assay of trypsin-1 that shows no inhibition")

    report.claims = check_drafts([
        (claim(f"Docking suggests that {name} may bind the S1 pocket of trypsin (bovine 3PTB, "
               "a model of human PRSS1), a hypothesis for an enzyme assay.",
               "mechanism_hypothesis", outcome="trypsin S1 pocket (3PTB, bovine)"),
         "the docking result, stated as the hypothesis it is", True),
        (claim(f"{name.capitalize()} inhibits human trypsin-1.", "mechanism",
               outcome="trypsin S1 pocket (3PTB, bovine)"),
         "the same finding stated as a measured mechanism; true or not, a docking score "
         "does not show it", False),
        (claim(f"{name.capitalize()} reduces attacks of hereditary chronic pancreatitis.",
               "efficacy", population="patients with hereditary chronic pancreatitis",
               outcome="attacks of pancreatitis"),
         "the target's genetic association carried to the compound, as a treatment",
         False),
        (claim(f"Docking suggests that {name} may bind trypsin at concentrations reached in "
               "patients.", "mechanism_hypothesis", outcome="trypsin S1 pocket (3PTB, bovine)"),
         "a docking result placed at human exposure", False),
    ], evidence)
    report.limits += [
        "3PTB is bovine trypsin; the human target is modelled by it, and the hypothesis "
        "says so.",
        "A Vina score is an estimate, not an affinity; only an assay (a measured Ki) would "
        "license a mechanism claim, and the case cites none.",
        "The Open Targets association chose the disease. It is an aggregate score and is "
        "cited by no claim.",
    ]
    return report


def _complex_prediction(work: Path) -> CaseStep:
    """Boltz on the trypsin-1 complex: what runs when the engine is here, refused when not."""
    from ..backends.environments import ExecutionEnvironments
    from ..structure.complex import Chain, ComplexPredictionTask, Ligand
    from ..structure.engines import BoltzEngine
    from ..structure.tasks import ModelSpec

    # Human trypsin-1, the mature chain: UniProt P07477 residues 24-247 (signal peptide and
    # activation peptide removed), checked against UniProt on 2026-10-07.
    prss1 = ("IVGGYNCEENSVPYQVSLNSGYHFCGGSLINEQWVVSAGHCYKSRIQVRLGEHNIEVLEGNEQFINAAKIIRHPQYDR"
             "KTLNNDIMLIKLSSRAVINARVSTISLPTAPPATGTKCLISGWGNTASSGADYPDELQCLDAPVLSQAKCEASYPGK"
             "ITSNMFCVGFLEGGKDSCQGDSGGPVVCNGQLQGVVSWGDGCAQKNKPGVYTKVYNYVKWIKNTIAANS")
    task = ComplexPredictionTask(
        "prss1_4aba", chains=(Chain(("A",), prss1),),
        ligands=(Ligand("L", smiles=COMPOUND[1]),),
        model=ModelSpec("boltz-2", version="2.2.0"))
    result = BoltzEngine(ExecutionEnvironments.from_env()).run(task, str(work / "boltz"),
                                                              timeout_s=3600)
    return CaseStep("complex prediction", result.status, "Boltz (boltz-2)",
                    (result.reason or "")[:300],
                    {"ran": bool(getattr(result.provenance, "ran", False))})
