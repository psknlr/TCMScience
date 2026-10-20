"""Test only (science.mjs --mode admet): the ADMET jobs' inputs, their native runs, and the exact values compared.

    python admet_native.py prepare WORK PLAN_JSON [ARCHIVE]
    python admet_native.py native WORK PLAN_JSON KEPT_JSON
    python admet_native.py values WORK CARDS

``prepare`` writes WORK/raw, the files the page is given as a project's files: the archive (``ARCHIVE``, a real TDC
ADMET group zip, copied; without it the known-answer archive of BioScience-Harness/tests/admet_world.py
``make_archive(seed=0)``, built twice to show its bytes do not change) and the molecule set (``MOLECULES``: drug-like
molecules, TCM natural products, salts, molecules the browser chemistry refuses with the step it says it lacks, and a
SMILES that is not one), as a CSV (name,smiles), and the same set without the molecules the browser is expected to
refuse (``*_kept.csv``). It prints ``{environment, archive, molecules}``; the expected refusals go into
WORK/expected.json.

``native`` runs, after the page, what each job stands for, natively (RDKit 2026.9.1 and scikit-learn 1.8.0; the caller
sets one thread): the models from the same archive with the same settings, and the pipeline on the molecules, twice:
on every molecule as the runner's command does (to show it reads those the browser refused) and on the kept ones (the
browser's result must be its bytes). The models are trained on the molecules the browser kept: KEPT_JSON lists, per
endpoint and split, the training SMILES the browser chemistry refused ({"training": {endpoint: {split: [[smiles,
reason], ...]}}}); when there are none the build is the runner's own command (``bioagent admet --build-models``),
otherwise ``build_models`` with native RDKit leaving those molecules out as the browser did (``Refusing``). It then
computes what the page's probe computes in the browser (``exact``): every endpoint's prediction at full precision and
its applicability-domain search (nearest training molecule, intersection, union) for the kept molecules of the set and
of each endpoint's test split, and each molecule's chemistry (feature row and bits digests, canonical SMILES,
descriptors, atom count, alerts). It prints ``{environment, how, seconds, cards, runs, molecules, tests}``.

``values`` holds model cards (CARDS: a model_cards.json, the browser's or the native build's) to the known answer of
the small archive (BioScience-Harness/tests/test_admet.py: a learnable regression, MAE below 0.5; a classification,
AUROC above 0.7; 80 training and 25 test molecules each). It prints ``{checks: [{label, ok, detail}]}``.

``exact`` and ``probe`` also run in the page's job worker (Pyodide; science-check.mjs reads this file and runs them on
the browser's models and prepared sets), so that both sides compute their full-precision values with the same code.
This module imports nothing at its top that Pyodide does not have.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

#: The molecule set: (name, SMILES, expected), expected None (prepared in the browser), ("refused", the step the
#: browser chemistry names as missing: standardize.js REFUSALS), or ("unreadable", native RDKit's own message). The
#: SMILES are the parity fixture's (web/test/fixtures/chem_parity.json), whose native answers they have there.
MOLECULES: tuple[tuple[str, str, tuple[str, str] | None], ...] = (
    # drug-like
    ("aspirin", "CC(=O)Oc1ccccc1C(=O)O", None),
    ("caffeine", "Cn1cnc2c1c(=O)n(C)c(=O)n2C", None),
    ("ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O", None),
    ("paracetamol", "CC(=O)Nc1ccc(O)cc1", None),
    # salts (the largest fragment, neutralised)
    ("metformin_hcl", "CN(C)C(=N)NC(=N)N.Cl", None),
    ("warfarin_sodium", "CC(=O)CC(c1ccccc1)c1c([O-])c2ccccc2oc1=O.[Na+]", None),
    ("imatinib_mesylate", "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1.CS(=O)(=O)O", None),
    ("sodium_benzoate", "O=C([O-])c1ccccc1.[Na+]", None),
    ("losartan_potassium", "CCCCc1nc(Cl)c(CO)n1Cc1ccc(-c2ccccc2-c2nnn[n-]2)cc1.[K+]", None),
    ("fluoxetine_hcl", "CNCCC(Oc1ccc(cc1)C(F)(F)F)c1ccccc1.Cl", None),
    # TCM natural products
    ("baicalein", "O=c1cc(-c2ccccc2)oc2cc(O)c(O)c(O)c12", None),
    ("baicalin", "O=C(O)[C@H]1O[C@@H](Oc2cc3oc(-c4ccccc4)cc(=O)c3c(O)c2O)[C@H](O)[C@@H](O)[C@@H]1O", None),
    ("wogonin", "COc1c(O)cc(O)c2c(=O)cc(-c3ccccc3)oc12", None),
    ("berberine_chloride", "COc1ccc2cc3[n+](cc2c1OC)CCc1cc2c(cc1-3)OCO2.[Cl-]", None),
    ("quercetin", "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12", None),
    ("puerarin", "O=c1c(-c2ccc(O)cc2)coc2c([C@@H]3O[C@H](CO)[C@@H](O)[C@H](O)[C@H]3O)c(O)ccc12", None),
    ("artemisinin", "C[C@@H]1CC[C@H]2[C@@H](C)C(=O)O[C@@H]3O[C@@]4(C)CC[C@@H]1[C@]32OO4", None),
    ("glycyrrhetinic_acid", "CC1(C)[C@@H](O)CC[C@]2(C)[C@H]1CC[C@]1(C)[C@@H]2C(=O)C=C2[C@@H]3C[C@@](C)(C(=O)O)CC[C@]3(C)"
                            "CC[C@]21C", None),
    ("matrine", "O=C1CCC[C@@H]2[C@H]3CCCN4CCC[C@H](CN12)[C@@H]34", None),
    ("oxymatrine", "[O-][N+]12CCC[C@H]3CN4C(=O)CCC[C@@H]4[C@H](CCC1)[C@@H]32", None),
    ("tanshinone_iia", "CC1=COC2=C1C(=O)C(=O)c1c2ccc2c1CCCC2(C)C", None),
    ("emodin", "Cc1cc(O)c2c(c1)C(=O)c1cc(O)cc(O)c1C2=O", None),
    ("curcumin", "COc1cc(/C=C/C(=O)CC(=O)/C=C/c2ccc(O)c(OC)c2)ccc1O", None),
    ("ephedrine_hcl", "CN[C@@H](C)[C@H](O)c1ccccc1.Cl", None),
    ("chlorogenic_acid", "O=C(/C=C/c1ccc(O)c(O)c1)O[C@@H]1C[C@](O)(C(=O)O)C[C@@H](O)[C@H]1O", None),
    ("aristolochic_acid_i", "COc1cccc2c1cc([N+](=O)[O-])c1c(C(=O)O)cc3c(c12)OCO3", None),
    ("rutin", "C[C@@H]1O[C@@H](OC[C@H]2O[C@@H](Oc3c(-c4ccc(O)c(O)c4)oc4cc(O)cc(O)c4c3=O)[C@H](O)[C@@H](O)[C@@H]2O)"
              "[C@H](O)[C@H](O)[C@H]1O", None),
    ("egcg", "O=C(O[C@@H]1Cc2c(O)cc(O)cc2O[C@@H]1c1cc(O)c(O)c(O)c1)c1cc(O)c(O)c(O)c1", None),
    ("cantharidin", "C[C@@]12C(=O)OC(=O)[C@@]1(C)[C@@H]1CC[C@H]2O1", None),
    # what the browser chemistry refuses (a step of chem.standardize it does not port, or a size RDKit.js does not
    # survive), each read by the runner's native RDKit
    ("cisplatin", "N.N.Cl[Pt]Cl", ("refused", "MetalDisconnector")),
    ("aurothioglucose", "OC[C@H]1O[C@@H](S[Au])[C@H](O)[C@@H](O)[C@@H]1O", ("refused", "MetalDisconnector")),
    ("iron_trichloride", "Cl[Fe](Cl)Cl", ("refused", "MetalDisconnector")),
    ("sodium_benzoate_covalent", "[Na]OC(=O)c1ccccc1", ("refused", "MetalDisconnector")),
    ("phenolate_carboxylic", "[O-]c1ccc(cc1)C(=O)O", ("refused", "Reionizer")),
    ("peg_406_atoms", "O" + "CCO" * 135, ("refused", "RDKit.js molecule size")),
    # a zwitterion whose neutralisation needs the Uncharger's canonical atom ranking, and a dative bond the feature
    # ports do not take (refused after standardisation, at the features)
    ("quaternary_dicarboxylate", "C[N+](C)(C)CCC(C(=O)[O-])C(=O)[O-]", ("refused", "Uncharger canonical atom ranking")),
    ("amine_oxide_dative", "CN(C)(C)->O", ("refused", "feature ports: bond type DATIVE")),
    # a TDC training molecule whose Normalizer product RDKit cannot sanitise (native RDKit goes on with it unsanitised)
    ("molsidomine", "CCOC(=O)[N-]c1c[n+](N2CCOCC2)no1", ("refused", "Normalizer unsanitisable product")),
    # not a SMILES: neither side reads it
    ("unclosed_ring", "C1CC", ("unreadable", "'C1CC' is not a valid SMILES")),
)
#: The known answer of the small archive (BioScience-Harness/tests/test_admet.py, test_admet_engine.py).
WORLD_ANSWER = {"caco2_wang": {"metric": "mae", "below": 0.5}, "hia_hou": {"metric": "auroc", "above": 0.7}}
WORLD_SIZES = {"train": 80, "test": 25}
ONE_THREAD = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


def _tests() -> Path:
    return Path(__file__).resolve().parents[4] / "BioScience-Harness" / "tests"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def environment() -> dict[str, Any]:
    import numpy as np
    import scipy
    import sklearn
    try:
        import rdkit
        rd = rdkit.__version__
    except ImportError:
        rd = None
    return {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__,
            "sklearn": sklearn.__version__, "rdkit": rd, "machine": platform.machine(),
            "threads": {k: os.environ.get(k) for k in ONE_THREAD}}


# --------------------------------------------------------------------------------------------- the exact values

def exact(models_dir: str | Path, prepared: Any, endpoints: Sequence[str]) -> dict[str, Any]:
    """Each endpoint's prediction for the prepared rows (``ModelSet.predict``, float64, written with ``float.hex``), the
    model's raw output it is computed from (the boosted trees' sum: a classifier's ``decision_function``, whose
    ``expit`` is the probability; a regressor's ``predict``, of which a log10 target's value is ``10 **``), and its
    applicability-domain search on this CPU (``chem.tanimoto_best``: the nearest training molecule, the intersection,
    the union), with the models of ``models_dir`` (its model_cards.json and files) → {endpoint: {value, raw,
    in_domain, arg, inter, union}}."""
    from bioagent.admet.chem import tanimoto_best
    from bioagent.admet.models import ENDPOINTS, ModelSet
    directory = Path(models_dir)
    cards = json.loads((directory / "model_cards.json").read_text(encoding="utf-8"))
    models = ModelSet(directory=directory, cards=cards)
    out: dict[str, Any] = {}
    for e in endpoints:
        found: dict[str, Any] = {}

        def search(endpoint, query, ref):
            found[endpoint] = tanimoto_best(query, ref)
            return found[endpoint]
        res = models.predict(prepared.features, prepared.bits, e, similarity=search)
        arg, inter, union = found.get(e, ([], [], []))
        model = models.model(e)
        raw = ((model.decision_function(prepared.features) if ENDPOINTS[e].task == "classification"
                else model.predict(prepared.features)) if len(prepared.features) else [])
        out[e] = {"value": [float(v).hex() for v in res["value"]], "raw": [float(v).hex() for v in raw],
                  "in_domain": [bool(v) for v in res["in_domain"]],
                  "arg": [int(v) for v in arg], "inter": [int(v) for v in inter],
                  "union": [int(v) for v in union]}
    return out


def chemistry_rows(prepared: Any, names: Sequence[str]) -> dict[str, Any]:
    """Each prepared molecule's chemistry by name: the SHA-256 of its float32 feature row (little-endian) and of its
    packed bits, its canonical SMILES, descriptors (JSON's null for NaN), atom count with hydrogens and alerts; and
    why each other molecule was not prepared."""
    import numpy as np
    rows, missing = {}, {}
    at = 0
    for i, name in enumerate(names):
        if not prepared.ok[i]:
            missing[name] = {"error": prepared.errors[i], "need": prepared.needs[i]}
            continue
        d = prepared.descriptors[i]
        rows[name] = {
            "features_sha256": _sha(np.ascontiguousarray(prepared.features[at]).astype("<f4").tobytes()),
            "bits_sha256": _sha(np.ascontiguousarray(prepared.bits[at]).tobytes()),
            "canonical": prepared.canonical[i],
            "descriptors": {k: (float(v) if np.isfinite(v) else None) for k, v in d.items()},
            "atoms_with_h": prepared.atoms_with_h[i],
            "alerts": None if prepared.alerts[i] is None else [list(a) for a in prepared.alerts[i]]}
        at += 1
    return {"rows": rows, "not_prepared": missing}


def probe(args: Mapping[str, Any]) -> dict[str, Any]:
    """The page's probe (science-check.mjs, in a job worker): ``exact`` on the browser's models (``models_dir``) for
    each set ({key, prepared_dir, smiles (a list) or molecules (admet_read's molecules.json), endpoints}), whose
    chemistry is the browser's prepared set read back (``engine.PrecomputedEngine``) → {key: {n, ok, endpoints}}."""
    from bioagent.admet.engine import PrecomputedEngine
    out: dict[str, Any] = {}
    for s in args["sets"]:
        if "molecules" in s:
            doc = json.loads(Path(s["molecules"]).read_text(encoding="utf-8"))
            smiles = list(doc["smiles"])
        else:
            smiles = list(s["smiles"])
        prepared = PrecomputedEngine(s["prepared_dir"]).prepare(smiles, alerts=False)
        out[s["key"]] = {"n": len(smiles), "ok": [bool(v) for v in prepared.ok],
                         "endpoints": exact(args["models_dir"], prepared, s["endpoints"])}
    return out


class Refusing:
    """Native RDKit with the molecules another chemistry refused left out as it left them out (not prepared, need
    "runner", its reason), so that a model trained natively is trained on the molecules a browser kept. Its name,
    version and detail are native RDKit's own: the cards it builds say native RDKit built them."""

    def __init__(self, refused: Mapping[str, str]) -> None:
        from bioagent.admet.engine import RDKitEngine
        self.inner = RDKitEngine()
        self.refused = dict(refused)
        self.name = self.inner.name

    @property
    def version(self) -> str | None:
        return self.inner.version

    @property
    def detail(self) -> str:
        return self.inner.detail

    def prepare(self, smiles: Sequence[str], *, alerts: bool = True):
        import numpy as np
        from bioagent.admet.engine import PreparedSet
        p = self.inner.prepare(smiles, alerts=alerts)
        drop = np.array([bool(p.ok[i]) and s in self.refused for i, s in enumerate(smiles)], dtype=bool)
        if not drop.any():
            return p
        keep = ~drop[p.ok]

        def gone(column):
            return tuple(None if drop[i] else v for i, v in enumerate(column))
        return PreparedSet(
            smiles=p.smiles, ok=p.ok & ~drop,
            errors=tuple(self.refused[s] if drop[i] else e for i, (s, e) in enumerate(zip(smiles, p.errors))),
            canonical=gone(p.canonical), features=p.features[keep], bits=p.bits[keep],
            descriptors=gone(p.descriptors), atoms_with_h=gone(p.atoms_with_h), alerts=gone(p.alerts),
            needs=tuple("runner" if drop[i] else n for i, n in enumerate(p.needs)))


# --------------------------------------------------------------------------------------------------- prepare

def _csv(rows: Sequence[tuple[str, str, Any]]) -> str:
    return "name,smiles\n" + "".join(f"{n},{s}\n" for n, s, _ in rows)


def kept_name(name: str) -> str:
    """The molecule file without the molecules the browser is expected to refuse."""
    return name[:-4] + "_kept.csv" if name.endswith(".csv") else name + "_kept"


def prepare(work: Path, plan: Mapping[str, Any], archive: Path | None) -> dict[str, Any]:
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    target = raw / plan["archive"]
    if archive is not None:
        shutil.copyfile(archive, target)
        built = {"from": str(archive), "reproducible": None}
    else:
        sys.path.insert(0, str(_tests()))
        from admet_world import make_archive
        make_archive(target, seed=0)
        again = make_archive(work / "archive_again.zip", seed=0)
        built = {"from": "BioScience-Harness/tests/admet_world.py make_archive(seed=0)",
                 "reproducible": _sha(again.read_bytes()) == _sha(target.read_bytes())}
        again.unlink()
    from bioagent.admet.models import TDC_ADMET_SHA256
    data = target.read_bytes()
    (raw / plan["molecules"]).write_text(_csv(MOLECULES), encoding="utf-8")
    kept = [m for m in MOLECULES if not (m[2] and m[2][0] == "refused")]
    (work / kept_name(plan["molecules"])).write_text(_csv(kept), encoding="utf-8")
    expected = {"refused": {n: e[1] for n, _, e in MOLECULES if e and e[0] == "refused"},
                "unreadable": {n: e[1] for n, _, e in MOLECULES if e and e[0] == "unreadable"},
                "names": [n for n, _, _ in MOLECULES], "smiles": {n: s for n, s, _ in MOLECULES}}
    (work / "expected.json").write_text(json.dumps(expected, indent=1), encoding="utf-8")
    return {"environment": environment(),
            "archive": {"name": plan["archive"], "sha256": _sha(data), "bytes": len(data),
                        "pinned": _sha(data) == TDC_ADMET_SHA256, **built},
            "molecules": {"name": plan["molecules"], "n": len(MOLECULES), "refused": expected["refused"],
                          "unreadable": expected["unreadable"]}}


# ---------------------------------------------------------------------------------------------------- native

def _cli(*args: str) -> list[str]:
    """The runner's command line (tcmstudio.kinds _cli): bioagent's CLI in this Python, isolated."""
    return [sys.executable, "-I", "-m", "bioagent.cli", *args]


def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    t0 = time.perf_counter()
    r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **ONE_THREAD}, cwd=cwd)
    return {"command": [Path(cmd[0]).name, *cmd[1:]], "exit": r.returncode,
            "seconds": round(time.perf_counter() - t0, 2), "stdout": r.stdout[-3000:], "stderr": r.stderr[-3000:]}


def _models_command(plan: Mapping[str, Any], archive: Path, cache: Path) -> list[str]:
    """tcmstudio.kinds _admet_models_command for the plan's parameters."""
    p = plan["models"]["params"]
    argv = _cli("admet", "--build-models", f"--cache={cache}", f"--max-iter={p.get('max_iter', 600)}",
                f"--seed={p.get('seed', 0)}", f"--archive={archive}")
    if p.get("rebuild"):
        argv.append("--rebuild")
    return argv + [f"--endpoint={e}" for e in p.get("endpoints") or ()]


def _pipeline_command(plan: Mapping[str, Any], molecules: Path, cache: Path, out: Path) -> list[str]:
    """tcmstudio.kinds _admet_command for the plan's parameters."""
    p = plan["pipeline"]["params"]
    return _cli("admet", str(molecules), f"--out={out}", f"--cache={cache}") + [
        f"--endpoint={e}" for e in p.get("endpoints") or ()]


def _training_refusals(kept: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for splits in (kept.get("training") or {}).values():
        for rows in (splits or {}).values():
            for smiles, why in rows or ():
                out[str(smiles)] = str(why)
    return out


def native(work: Path, plan: Mapping[str, Any], kept: Mapping[str, Any]) -> dict[str, Any]:
    import zipfile

    from bioagent.admet import models as M
    from bioagent.admet.engine import RDKitEngine
    raw = work / "raw"
    archive = raw / plan["archive"]
    root = work / "native"
    cache = root / "cache"
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)
    refused = _training_refusals(kept)
    params = plan["models"]["params"]
    runs: dict[str, Any] = {}
    if not refused:
        how = "the runner's command (bioagent admet --build-models): the browser chemistry refused no training molecule"
        runs["models"] = _run(_models_command(plan, archive, cache), work)
        if runs["models"]["exit"] != 0:
            raise SystemExit(f"the native model build failed (exit {runs['models']['exit']}): "
                             f"{runs['models']['stderr']}")
    else:
        how = (f"build_models with native RDKit, the {len(refused)} training molecules the browser chemistry refused "
               "left out as it left them out (need runner), so that both are trained on the same molecules")
        t0 = time.perf_counter()
        said: list[str] = []
        M.build_models(cache, archive=archive, endpoints=params.get("endpoints") or None,
                       seed=params.get("seed", 0), max_iter=params.get("max_iter", 600),
                       engine=Refusing(refused), log=said.append)
        runs["models"] = {"command": ["build_models", "engine=Refusing"], "exit": 0,
                          "seconds": round(time.perf_counter() - t0, 2), "stdout": "\n".join(said)[-3000:]}
    models_dir = cache / "models"
    cards = json.loads((models_dir / "model_cards.json").read_text(encoding="utf-8"))
    endpoints = list(params.get("endpoints") or M.ENDPOINTS)

    # the pipeline: the runner's command on every molecule, and on the molecules the browser is expected to keep
    mol_file = raw / plan["molecules"]
    for key, molecules in (("runner", mol_file), ("kept", work / kept_name(plan["molecules"]))):
        out = root / key
        if out.exists():
            shutil.rmtree(out)
        runs[key] = _run(_pipeline_command(plan, molecules, cache, out), work)
        runs[key]["out"] = str(out)

    # the exact values of the molecules (each prepared natively: native RDKit reads those the browser refuses)
    names = [n for n, _, _ in MOLECULES]
    prepared = RDKitEngine().prepare([s for _, s, _ in MOLECULES])
    ok_names = [n for i, n in enumerate(names) if prepared.ok[i]]
    predicted = [e for e in (plan["pipeline"]["params"].get("endpoints") or endpoints) if e in cards]
    mols = {"chemistry": chemistry_rows(prepared, names), "names": ok_names,
            "endpoints": exact(models_dir, prepared, predicted)}

    # each endpoint's test split: its molecules the browser kept, prepared natively, predicted
    tests: dict[str, Any] = {}
    with zipfile.ZipFile(archive) as z:
        for e in endpoints:
            smiles, _ = M.read_split(z, e, "test")
            test_refused = {s: why for s, why in ((kept.get("training") or {}).get(e) or {}).get("test") or ()}
            p = Refusing(test_refused).prepare(smiles, alerts=False)
            tests[e] = {"n": len(smiles), "ok": [bool(v) for v in p.ok], "endpoints": exact(models_dir, p, [e])}
    return {"environment": environment(), "how": how, "runs": runs, "cards": cards, "models_dir": str(models_dir),
            "molecules": mols, "tests": tests}


# ---------------------------------------------------------------------------------------------------- values

def values(cards_file: Path) -> list[dict[str, Any]]:
    cards = json.loads(cards_file.read_text(encoding="utf-8"))
    checks = []
    for e, want in WORLD_ANSWER.items():
        c = cards.get(e) or {}
        score = c.get("test_score")
        if "below" in want:
            ok, bound = isinstance(score, (int, float)) and score < want["below"], f"below {want['below']}"
        else:
            ok, bound = isinstance(score, (int, float)) and score > want["above"], f"above {want['above']}"
        checks.append({"label": f"{e}: {want['metric']} {bound} (a learnable target of the small archive)",
                       "ok": bool(ok) and c.get("metric") == want["metric"],
                       "detail": f"{c.get('metric')} {score} after {c.get('iterations')} iterations"})
        sizes = {k: c.get(k) for k in WORLD_SIZES}
        checks.append({"label": f"{e}: {WORLD_SIZES['train']} training and {WORLD_SIZES['test']} test molecules",
                       "ok": sizes == WORLD_SIZES, "detail": json.dumps(sizes)})
    return checks


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        argv = [argv[0], str(Path(argv[1]).resolve()), *argv[2:]]
    if argv[:1] == ["prepare"] and len(argv) in (3, 4):
        archive = Path(argv[3]) if len(argv) == 4 else None
        print(json.dumps(prepare(Path(argv[1]), json.loads(argv[2]), archive)))
        return 0
    if argv[:1] == ["native"] and len(argv) == 4:
        kept = json.loads(Path(argv[3]).read_text(encoding="utf-8"))
        doc = native(Path(argv[1]), json.loads(argv[2]), kept)
        (Path(argv[1]) / "native.json").write_text(json.dumps(doc), encoding="utf-8")
        print(json.dumps({"written": str(Path(argv[1]) / "native.json")}))
        return 0
    if argv[:1] == ["values"] and len(argv) == 3:
        print(json.dumps({"checks": values(Path(argv[2]))}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
