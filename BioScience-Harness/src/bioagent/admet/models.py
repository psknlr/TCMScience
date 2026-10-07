"""ADMET models trained on the Therapeutics Data Commons benchmark group.

The data are TDC's ADMET group (Huang et al. 2021, NeurIPS Datasets and Benchmarks): 22
endpoints, each with the official scaffold-split ``train_val`` and ``test`` sets, so a
model's held-out score is comparable with the published leaderboard. The archive is
downloaded once from Harvard Dataverse and pinned by its SHA-256.

Each endpoint gets a gradient-boosted tree model (scikit-learn's
``HistGradientBoosting``, early stopping on a 10% validation split of ``train_val``) over
the features of ``chem.featurize``. Regression targets that span orders of magnitude
(VDss, half-life, clearance) are modelled as log10. A **model card** per endpoint
records the data, sizes, the TDC metric on the test set, the features, the library
versions and the model file's SHA-256, which is checked before the file is loaded.

The **applicability domain** of a prediction is the largest Tanimoto similarity of the
query to the training molecules; below 0.3 the query is unlike anything the model saw
and the prediction is flagged as outside the domain.

The models are built where they run (``build_models``); nothing trained is shipped.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .chem import ChemError, bits, featurize, need, standardize, tanimoto_max

__all__ = ["ENDPOINTS", "Endpoint", "ModelSet", "build_models", "load_models",
           "fetch_archive", "TDC_ADMET_URL", "TDC_ADMET_SHA256", "DOMAIN_THRESHOLD"]

TDC_ADMET_URL = "https://dataverse.harvard.edu/api/access/datafile/4426004"
TDC_ADMET_SHA256 = "bd0005246cb6f1672333c28ad202112a8bf071ab65d847848715030308faf1f1"
DOMAIN_THRESHOLD = 0.3


@dataclass(frozen=True)
class Endpoint:
    name: str
    group: str                  # absorption, distribution, metabolism, excretion, toxicity
    task: str                   # regression | classification
    metric: str                 # TDC's leaderboard metric
    unit: str
    meaning: str
    log10: bool = False


ENDPOINTS: Mapping[str, Endpoint] = {e.name: e for e in (
    Endpoint("caco2_wang", "absorption", "regression", "mae", "log10(cm/s)",
             "Caco-2 cell permeability"),
    Endpoint("hia_hou", "absorption", "classification", "auroc", "probability",
             "human intestinal absorption (absorbed)"),
    Endpoint("pgp_broccatelli", "absorption", "classification", "auroc", "probability",
             "P-glycoprotein inhibition"),
    Endpoint("bioavailability_ma", "absorption", "classification", "auroc", "probability",
             "oral bioavailability above 20%"),
    Endpoint("lipophilicity_astrazeneca", "absorption", "regression", "mae", "logD7.4",
             "lipophilicity"),
    Endpoint("solubility_aqsoldb", "absorption", "regression", "mae", "log10(mol/L)",
             "aqueous solubility"),
    Endpoint("bbb_martins", "distribution", "classification", "auroc", "probability",
             "blood-brain barrier penetration"),
    Endpoint("ppbr_az", "distribution", "regression", "mae", "% bound",
             "plasma protein binding"),
    Endpoint("vdss_lombardo", "distribution", "regression", "spearman", "L/kg",
             "volume of distribution at steady state", log10=True),
    Endpoint("cyp2c9_veith", "metabolism", "classification", "auprc", "probability",
             "CYP2C9 inhibition"),
    Endpoint("cyp2d6_veith", "metabolism", "classification", "auprc", "probability",
             "CYP2D6 inhibition"),
    Endpoint("cyp3a4_veith", "metabolism", "classification", "auprc", "probability",
             "CYP3A4 inhibition"),
    Endpoint("cyp2c9_substrate_carbonmangels", "metabolism", "classification", "auprc",
             "probability", "CYP2C9 substrate"),
    Endpoint("cyp2d6_substrate_carbonmangels", "metabolism", "classification", "auprc",
             "probability", "CYP2D6 substrate"),
    Endpoint("cyp3a4_substrate_carbonmangels", "metabolism", "classification", "auroc",
             "probability", "CYP3A4 substrate"),
    Endpoint("half_life_obach", "excretion", "regression", "spearman", "hours",
             "half-life", log10=True),
    Endpoint("clearance_hepatocyte_az", "excretion", "regression", "spearman",
             "uL/min/10^6 cells", "hepatocyte clearance", log10=True),
    Endpoint("clearance_microsome_az", "excretion", "regression", "spearman",
             "mL/min/g", "microsomal clearance", log10=True),
    Endpoint("ld50_zhu", "toxicity", "regression", "mae", "-log10(mol/kg)",
             "acute oral toxicity (rat LD50)"),
    Endpoint("herg", "toxicity", "classification", "auroc", "probability",
             "hERG channel blockade"),
    Endpoint("ames", "toxicity", "classification", "auroc", "probability",
             "Ames mutagenicity"),
    Endpoint("dili", "toxicity", "classification", "auroc", "probability",
             "drug-induced liver injury"),
)}


def _metric(name: str, y: np.ndarray, pred: np.ndarray) -> float:
    from scipy.stats import spearmanr
    metrics = need("sklearn.metrics")
    if name == "mae":
        return float(np.mean(np.abs(y - pred)))
    if name == "spearman":
        return float(spearmanr(y, pred).correlation)
    if name == "auroc":
        return float(metrics.roc_auc_score(y, pred))
    if name == "auprc":
        return float(metrics.average_precision_score(y, pred))
    raise ValueError(name)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_archive(cache: Path, *, url: str = TDC_ADMET_URL, sha256: str = TDC_ADMET_SHA256,
                  timeout: float = 600.0) -> Path:
    """The TDC ADMET group archive, downloaded once and held to its pinned SHA-256."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "tdc_admet_group.zip"
    if target.is_file():
        if sha256 and _sha(target.read_bytes()) != sha256:
            raise ChemError(f"{target} does not match the pinned SHA-256 {sha256}; delete it "
                            "and build again")
        return target
    from ..backends.http import user_agent

    # Harvard Dataverse answers Python's default User-Agent with 403 Forbidden, so the
    # download names the harness the way every other request it makes does.
    request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    data, last = None, None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:
                data = r.read()
            break
        except Exception as exc:                              # noqa: BLE001
            last = exc
            time.sleep(2 ** attempt)
    if data is None:
        raise ChemError(f"could not download the TDC ADMET group from {url}: {last}")
    if not data.startswith(b"PK"):
        raise ChemError(f"the download from {url} is not a zip archive")
    if sha256 and _sha(data) != sha256:
        raise ChemError(f"the download from {url} has SHA-256 {_sha(data)}, not the pinned "
                        f"{sha256}; the archive has changed and the models would not match "
                        "their published splits")
    target.write_bytes(data)
    return target


def _read_split(z: zipfile.ZipFile, endpoint: str, split: str) -> tuple[list[str], np.ndarray]:
    import csv
    name = f"admet_group/{endpoint}/{split}.csv"
    with z.open(name) as fh:
        rows = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")))
    return [r["Drug"] for r in rows], np.array([float(r["Y"]) for r in rows])


def _prepare(smiles: Sequence[str]) -> tuple[list[Any], np.ndarray]:
    mols, ok = [], []
    for s in smiles:
        try:
            mols.append(standardize(s))
            ok.append(True)
        except ChemError:
            ok.append(False)
    return mols, np.array(ok)


@dataclass
class ModelSet:
    directory: Path
    cards: dict[str, dict[str, Any]]
    _models: dict[str, Any] = field(default_factory=dict)
    _domain: dict[str, np.ndarray] = field(default_factory=dict)

    def model(self, endpoint: str):
        if endpoint not in self._models:
            card = self.cards[endpoint]
            path = self.directory / card["model_file"]
            data = path.read_bytes()
            if _sha(data) != card["model_sha256"]:
                raise ChemError(f"{path} does not match its model card; rebuild the models")
            import pickle
            self._models[endpoint] = pickle.loads(data)          # digest-checked above
            self._domain[endpoint] = np.load(self.directory / card["domain_file"])
        return self._models[endpoint]

    def predict(self, mols: Sequence[Any], endpoint: str) -> dict[str, np.ndarray]:
        spec = ENDPOINTS[endpoint]
        if not mols:
            empty = np.zeros(0)
            return {"value": empty, "similarity": empty,
                    "in_domain": np.zeros(0, dtype=bool)}
        model = self.model(endpoint)
        x = featurize(mols)
        if spec.task == "classification":
            value = model.predict_proba(x)[:, 1]
        else:
            value = model.predict(x)
            if spec.log10:
                value = 10 ** value
        sim, _ = tanimoto_max(bits(mols), self._domain[endpoint])
        return {"value": value, "similarity": sim, "in_domain": sim >= DOMAIN_THRESHOLD}


def build_models(cache_dir: str | Path, *, archive: str | Path | None = None,
                 endpoints: Sequence[str] | None = None, seed: int = 0, max_iter: int = 600,
                 log=print) -> ModelSet:
    """Train and score every endpoint; write models, domains and model cards."""
    ensemble = need("sklearn.ensemble")
    sklearn = need("sklearn")
    rdkit = need("rdkit")
    cache = Path(cache_dir)
    path = Path(archive) if archive else fetch_archive(cache)
    raw = path.read_bytes()
    archive_sha = _sha(raw)
    out = cache / "models"
    out.mkdir(parents=True, exist_ok=True)
    cards: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        available = {n.split("/")[1] for n in z.namelist() if n.count("/") >= 2}
        for name in (endpoints or list(ENDPOINTS)):
            if name not in available:
                raise ChemError(f"the archive has no endpoint {name}")
            spec = ENDPOINTS[name]
            t0 = time.time()
            train_smiles, y_train = _read_split(z, name, "train_val")
            test_smiles, y_test = _read_split(z, name, "test")
            train_mols, ok_tr = _prepare(train_smiles)
            test_mols, ok_te = _prepare(test_smiles)
            y_train, y_test = y_train[ok_tr], y_test[ok_te]
            x_train, x_test = featurize(train_mols), featurize(test_mols)
            target = np.log10(np.maximum(y_train, 1e-6)) if spec.log10 else y_train
            common = dict(max_iter=max_iter, learning_rate=0.05, max_leaf_nodes=31,
                          l2_regularization=1.0, early_stopping=True,
                          validation_fraction=0.1, n_iter_no_change=30, random_state=seed)
            if spec.task == "classification":
                model = ensemble.HistGradientBoostingClassifier(**common)
                model.fit(x_train, target.astype(int))
                pred = model.predict_proba(x_test)[:, 1]
            else:
                model = ensemble.HistGradientBoostingRegressor(**common)
                model.fit(x_train, target)
                pred = model.predict(x_test)
                if spec.log10:
                    pred = 10 ** pred
            score = _metric(spec.metric, y_test, pred)
            import pickle
            blob = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
            (out / f"{name}.pkl").write_bytes(blob)
            np.save(out / f"{name}_domain.npy", bits(train_mols))
            cards[name] = {
                "endpoint": name, "group": spec.group, "task": spec.task,
                "meaning": spec.meaning, "unit": spec.unit, "log10_target": spec.log10,
                "data": "TDC ADMET benchmark group (train_val / test, scaffold split)",
                "archive_sha256": archive_sha,
                "archive_is_pinned": archive_sha == TDC_ADMET_SHA256,
                "train": int(len(y_train)),
                "test": int(len(y_test)), "dropped_unreadable": int((~ok_tr).sum()
                                                                    + (~ok_te).sum()),
                "metric": spec.metric, "test_score": round(score, 4),
                "model": type(model).__name__, "iterations": int(model.n_iter_),
                "features": "Morgan count FP r2 2048 + 25 RDKit descriptors",
                "sklearn": sklearn.__version__, "rdkit": rdkit.__version__,
                "model_file": f"{name}.pkl", "model_sha256": _sha(blob),
                "domain_file": f"{name}_domain.npy", "domain_threshold": DOMAIN_THRESHOLD,
                "seconds": round(time.time() - t0, 1)}
            log(f"  {name}: {spec.metric} {score:.3f} on {len(y_test)} test molecules "
                f"({cards[name]['seconds']} s)")
    (out / "model_cards.json").write_text(json.dumps(cards, indent=2), encoding="utf-8")
    return ModelSet(directory=out, cards=cards)


def load_models(cache_dir: str | Path) -> ModelSet | None:
    out = Path(cache_dir) / "models"
    cards_file = out / "model_cards.json"
    if not cards_file.is_file():
        return None
    return ModelSet(directory=out, cards=json.loads(cards_file.read_text(encoding="utf-8")))
