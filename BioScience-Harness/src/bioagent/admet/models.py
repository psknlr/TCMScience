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

The models are built where they run (``build_models``); nothing trained is shipped. A
build writes each endpoint's card as soon as its model is saved, so an interrupted build
keeps what it finished, and the next build trains only what is missing. A model is kept
only when its file and its domain match its card and the card was built from the same
archive, settings, library versions and chemistry; anything else is trained again. A
browser builds its models one endpoint per call (``train_endpoint``: the same training,
files and card, merged into the models directory's cards), and ``verify_models`` holds
every model to its card, as a runner's model build is checked when it ends.

The chemistry (standardisation, features, the domain's bits) comes from an
``engine.ChemEngine``: natively RDKit (``engine.RDKitEngine``), in a browser what its
RDKit.js chemistry prepared (``engine.PrecomputedEngine``). The card names it
(``chemistry``) and counts the molecules it dropped: those it could not read
(``dropped_unreadable``) and those it refused to standardise because the steps they need
are not ported to it (``dropped_unstandardisable``; always 0 natively), so a model built
in a browser may have been trained on fewer molecules than one built natively.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .chem import FEATURES, ChemError, need, similarity as _similarity, tanimoto_best
from .engine import ChemEngine, Similarity, default_engine, identity

__all__ = ["ENDPOINTS", "Endpoint", "ModelSet", "build_models", "train_endpoint",
           "verify_models", "card_sha256", "load_models", "fetch_archive", "read_archive",
           "read_split", "split_member", "SPLITS", "TDC_ADMET_URL", "TDC_ADMET_SHA256",
           "DOMAIN_THRESHOLD"]

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


SPLITS = ("train_val", "test")


def split_member(endpoint: str, split: str) -> str:
    """The archive member of an endpoint's split, as TDC lays the group out."""
    return f"admet_group/{endpoint}/{split}.csv"


def read_split(z: zipfile.ZipFile, endpoint: str, split: str) -> tuple[list[str], np.ndarray]:
    """An endpoint's split, its SMILES (``Drug``) and targets (``Y``) in the archive's order;
    a member that is missing, or that is not a table of them, is refused (``ChemError``)."""
    import csv
    name = split_member(endpoint, split)
    if name not in z.namelist():
        raise ChemError(f"the archive lacks {name}")
    try:
        with z.open(name) as fh:
            rows = list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8")))
        smiles = [r["Drug"] for r in rows]
        if not all(isinstance(s, str) for s in smiles):
            raise ValueError("a row has no Drug")
        return smiles, np.array([float(r["Y"]) for r in rows])
    except KeyError as exc:
        raise ChemError(f"{name} has no column {exc}") from None
    except (ValueError, TypeError, UnicodeDecodeError, zipfile.BadZipFile, csv.Error) as exc:
        raise ChemError(f"{name} cannot be read as TDC's table (Drug, Y): {exc}") from None


_read_split = read_split          # the name the tests and earlier callers use


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
            domain = self.directory / card["domain_file"]
            if "domain_sha256" in card and _sha(domain.read_bytes()) != card["domain_sha256"]:
                raise ChemError(f"{domain} does not match its model card; rebuild the models")
            import pickle
            self._models[endpoint] = pickle.loads(data)          # digest-checked above
            self._domain[endpoint] = np.load(domain)
        return self._models[endpoint]

    def domain(self, endpoint: str) -> np.ndarray:
        """The packed bits of the endpoint's training molecules, held to its card."""
        self.model(endpoint)
        return self._domain[endpoint]

    def predict(self, x: np.ndarray, bits: np.ndarray, endpoint: str, *,
                similarity: Similarity | None = None) -> dict[str, np.ndarray]:
        """The endpoint's prediction for each feature row of ``x`` (``chem.featurize``),
        with the largest Tanimoto similarity of its packed bits (``chem.bits``) to the
        training molecules and whether that puts it in the applicability domain.
        ``similarity`` searches the domain (default ``chem.tanimoto_best`` on this CPU)."""
        spec = ENDPOINTS[endpoint]
        x, bits = np.asarray(x), np.asarray(bits)
        if x.ndim != 2 or x.shape[1] != FEATURES or len(bits) != len(x):
            raise ChemError(f"a prediction needs one {FEATURES}-column feature row and one "
                            f"fingerprint per molecule (got {x.shape} and {bits.shape})")
        if not len(x):
            empty = np.zeros(0)
            return {"value": empty, "similarity": empty,
                    "in_domain": np.zeros(0, dtype=bool)}
        model = self.model(endpoint)
        if spec.task == "classification":
            value = model.predict_proba(x)[:, 1]
        else:
            value = model.predict(x)
            if spec.log10:
                value = 10 ** value
        search = similarity or (lambda _, q, r: tanimoto_best(q, r))
        _, inter, union = search(endpoint, bits, self._domain[endpoint])
        if not len(inter) == len(union) == len(x):
            raise ChemError(f"the similarity search gave {len(inter)} answers for {len(x)} "
                            "molecules")
        sim = _similarity(inter, union)
        return {"value": value, "similarity": sim, "in_domain": sim >= DOMAIN_THRESHOLD}


def _same_chemistry(card: Mapping[str, Any], chemistry: Mapping[str, Any]) -> bool:
    """Whether the card's model was built with this chemistry (``engine.identity``). A card
    from before cards named it was built natively, with the RDKit it names."""
    built = card.get("chemistry")
    if built is None:
        return chemistry["engine"] == "rdkit" and card.get("rdkit") == chemistry["version"]
    return built == chemistry


def _kept(card: Mapping[str, Any] | None, out: Path, settings: Mapping[str, Any],
          chemistry: Mapping[str, Any]) -> bool:
    """Whether a finished model can stand: same archive, settings, libraries and
    chemistry, and files that still match the card's digests."""
    if (not card or any(card.get(k) != v for k, v in settings.items())
            or not _same_chemistry(card, chemistry)):
        return False
    model, domain = out / card["model_file"], out / card["domain_file"]
    return (model.is_file() and domain.is_file() and "domain_sha256" in card
            and _sha(model.read_bytes()) == card["model_sha256"]
            and _sha(domain.read_bytes()) == card["domain_sha256"])


def _write_cards(out: Path, cards: Mapping[str, Any]) -> None:
    tmp = out / "model_cards.json.tmp"
    tmp.write_text(json.dumps(cards, indent=2), encoding="utf-8")
    os.replace(tmp, out / "model_cards.json")


def read_archive(archive: str | Path) -> tuple[bytes, str]:
    """The archive's bytes and SHA-256, refused (``ChemError``) when it is not a zip."""
    path = Path(archive)
    raw = path.read_bytes()
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise ChemError(f"{path.name} is not a zip archive (the TDC ADMET group is one)")
    return raw, _sha(raw)


def _endpoints_in(z: zipfile.ZipFile) -> set[str]:
    return {n.split("/")[1] for n in z.namelist() if n.count("/") >= 2}


def _settings(archive_sha: str, seed: int, max_iter: int, sklearn: Any,
              engine: ChemEngine) -> dict[str, Any]:
    """What a finished model must have been built with to be kept (``_kept``)."""
    return {"archive_sha256": archive_sha, "seed": seed, "max_iter": max_iter,
            "sklearn": sklearn.__version__, "rdkit": engine.version}


def _train(z: zipfile.ZipFile, name: str, out: Path, *, archive_sha: str, seed: int,
           max_iter: int, engine: ChemEngine) -> tuple[dict[str, Any], str]:
    """Train and score one endpoint from the archive ``z``; write its model and domain into
    ``out`` and return its card (the caller writes it) and the line the build logs."""
    ensemble = need("sklearn.ensemble")
    sklearn = need("sklearn")
    spec = ENDPOINTS[name]
    t0 = time.time()
    train_smiles, y_train = read_split(z, name, "train_val")
    test_smiles, y_test = read_split(z, name, "test")
    prepared = engine.prepare(train_smiles + test_smiles, alerts=False)
    ok_tr, ok_te = prepared.ok[:len(train_smiles)], prepared.ok[len(train_smiles):]
    y_train, y_test = y_train[ok_tr], y_test[ok_te]
    n_train = int(ok_tr.sum())
    x_train, x_test = prepared.features[:n_train], prepared.features[n_train:]
    refused = sum(1 for ok, why in zip(prepared.ok, prepared.needs)
                  if not ok and why == "runner")
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
    np.save(out / f"{name}_domain.npy", prepared.bits[:n_train])
    domain_sha = _sha((out / f"{name}_domain.npy").read_bytes())
    card = {
        "endpoint": name, "group": spec.group, "task": spec.task,
        "meaning": spec.meaning, "unit": spec.unit, "log10_target": spec.log10,
        "data": "TDC ADMET benchmark group (train_val / test, scaffold split)",
        "archive_sha256": archive_sha,
        "archive_is_pinned": archive_sha == TDC_ADMET_SHA256,
        "train": int(len(y_train)),
        "test": int(len(y_test)),
        "dropped_unreadable": int((~prepared.ok).sum()) - refused,
        "dropped_unstandardisable": refused,
        "metric": spec.metric, "test_score": round(score, 4),
        "model": type(model).__name__, "iterations": int(model.n_iter_),
        "features": "Morgan count FP r2 2048 + 25 RDKit descriptors",
        "sklearn": sklearn.__version__, "rdkit": engine.version,
        "chemistry": identity(engine),
        "model_file": f"{name}.pkl", "model_sha256": _sha(blob),
        "domain_file": f"{name}_domain.npy", "domain_sha256": domain_sha,
        "domain_threshold": DOMAIN_THRESHOLD, "seed": seed, "max_iter": max_iter,
        "seconds": round(time.time() - t0, 1)}
    return card, (f"  {name}: {spec.metric} {score:.3f} on {len(y_test)} test molecules "
                  f"({card['seconds']} s)")


def _read_cards(out: Path) -> dict[str, dict[str, Any]]:
    existing = out / "model_cards.json"
    return json.loads(existing.read_text(encoding="utf-8")) if existing.is_file() else {}


def build_models(cache_dir: str | Path, *, archive: str | Path | None = None,
                 endpoints: Sequence[str] | None = None, seed: int = 0, max_iter: int = 600,
                 rebuild: bool = False, log=print, engine: ChemEngine | None = None
                 ) -> ModelSet:
    """Train and score the endpoints; write models, domains and model cards.

    Each card is written as soon as its model is saved. A finished model is kept, not
    trained again, when ``_kept`` says it can stand (``rebuild`` trains every endpoint
    asked for regardless). Cards of endpoints not asked for are left as they are.

    ``engine`` computes the chemistry (default ``engine.RDKitEngine``). An endpoint's
    ``train_val`` SMILES followed by its ``test`` SMILES, in the archive's order, are
    prepared as one list (without alerts), which a ``PrecomputedEngine`` holds as one set
    or as the two splits' sets.
    """
    need("sklearn.ensemble")
    sklearn = need("sklearn")
    if engine is None:
        need("rdkit")                      # the native chemistry, refused before a download
    chem_engine = engine or default_engine()
    chemistry = identity(chem_engine)
    cache = Path(cache_dir)
    raw, archive_sha = read_archive(archive if archive else fetch_archive(cache))
    out = cache / "models"
    out.mkdir(parents=True, exist_ok=True)
    cards = _read_cards(out)
    settings = _settings(archive_sha, seed, max_iter, sklearn, chem_engine)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        available = _endpoints_in(z)
        for name in (endpoints or list(ENDPOINTS)):
            if name not in available:
                raise ChemError(f"the archive has no endpoint {name}")
            if not rebuild and _kept(cards.get(name), out, settings, chemistry):
                log(f"  {name}: kept, its model matches its card")
                continue
            cards.pop(name, None)
            cards[name], line = _train(z, name, out, archive_sha=archive_sha, seed=seed,
                                       max_iter=max_iter, engine=chem_engine)
            _write_cards(out, cards)
            log(line)
    _write_cards(out, cards)
    return ModelSet(directory=out, cards=cards)


def train_endpoint(models_dir: str | Path, *, archive: str | Path, endpoint: str,
                   seed: int = 0, max_iter: int = 600, rebuild: bool = False, log=print,
                   engine: ChemEngine | None = None) -> dict[str, Any]:
    """``build_models`` for one endpoint, into the models directory itself (``<cache>/
    models``): the same training, settings and files (``<endpoint>.pkl``,
    ``<endpoint>_domain.npy``), and its card merged into ``model_cards.json`` (read again
    just before it is replaced, atomically, so the cards of other endpoints stay as they
    are). A finished model that ``_kept`` says can stand is kept unless ``rebuild``.
    Returns the endpoint's card. A browser trains its endpoints one call each, on the
    chemistry its RDKit.js prepared (``engine.PrecomputedEngine``)."""
    need("sklearn.ensemble")
    sklearn = need("sklearn")
    if endpoint not in ENDPOINTS:
        raise ChemError(f"no TDC ADMET endpoint {endpoint!r}; one of {', '.join(ENDPOINTS)}")
    if engine is None:
        need("rdkit")
    chem_engine = engine or default_engine()
    chemistry = identity(chem_engine)
    raw, archive_sha = read_archive(archive)
    out = Path(models_dir)
    out.mkdir(parents=True, exist_ok=True)
    settings = _settings(archive_sha, seed, max_iter, sklearn, chem_engine)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if endpoint not in _endpoints_in(z):
            raise ChemError(f"the archive has no endpoint {endpoint}")
        card = _read_cards(out).get(endpoint)
        if not rebuild and _kept(card, out, settings, chemistry):
            log(f"  {endpoint}: kept, its model matches its card")
            return card
        card, line = _train(z, endpoint, out, archive_sha=archive_sha, seed=seed,
                            max_iter=max_iter, engine=chem_engine)
    cards = _read_cards(out)
    cards.pop(endpoint, None)
    cards[endpoint] = card
    _write_cards(out, cards)
    log(line)
    return card


def card_sha256(card: Mapping[str, Any]) -> str:
    """A model card's identity: SHA-256 of its JSON with sorted keys and no spaces."""
    return _sha(json.dumps(card, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8"))


def verify_models(models_dir: str | Path, endpoints: Sequence[str] | None = None, *,
                  archive_sha256: str | None = None
                  ) -> tuple[bool, list[str], dict[str, Any]]:
    """The models of ``endpoints`` (default: every card) held to their cards: each has a
    card in ``model_cards.json``, its model and domain files are there at the SHA-256 the
    card records, and the card records the archive it was trained on, the same for every
    endpoint (``archive_sha256``, when given, is that archive). Returns ``(ok, problems,
    report)``; the report has each endpoint's card digest (``card_sha256``), file digests,
    archive, chemistry, counts and held-out score."""
    out = Path(models_dir)
    problems: list[str] = []
    try:
        cards = _read_cards(out)
    except (OSError, ValueError) as exc:
        return False, [f"model_cards.json cannot be read: {exc}"], {"endpoints": {}}
    if not isinstance(cards, dict):
        return False, ["model_cards.json is not an object of model cards"], {"endpoints": {}}
    wanted = list(endpoints) if endpoints else list(cards)
    if not wanted:
        problems.append(f"no model card in {out.name}/model_cards.json")
    report: dict[str, Any] = {}
    archives = set()
    for name in wanted:
        card = cards.get(name)
        if not isinstance(card, dict):
            problems.append(f"{name}: no model card")
            continue
        for kind in ("model", "domain"):
            rel, digest = card.get(f"{kind}_file"), card.get(f"{kind}_sha256")
            path = out / str(rel or "")
            if not rel or not digest:
                problems.append(f"{name}: its card names no {kind} file and digest")
            elif Path(str(rel)).name != str(rel) or not path.is_file():
                problems.append(f"{name}: its {kind} file {rel} is missing")
            elif _sha(path.read_bytes()) != digest:
                problems.append(f"{name}: its {kind} file {rel} does not match its card")
        sha = card.get("archive_sha256")
        if not (isinstance(sha, str) and len(sha) == 64
                and all(c in "0123456789abcdef" for c in sha)):
            problems.append(f"{name}: its card records no archive SHA-256")
        else:
            archives.add(sha)
            if archive_sha256 and sha != archive_sha256:
                problems.append(f"{name}: trained on another archive ({sha[:12]}), not "
                                f"{archive_sha256[:12]}")
        report[name] = {"card_sha256": card_sha256(card),
                        **{k: card.get(k) for k in (
                            "model_sha256", "domain_sha256", "archive_sha256",
                            "archive_is_pinned", "chemistry", "train", "test",
                            "dropped_unreadable", "dropped_unstandardisable", "metric",
                            "test_score", "sklearn", "seed", "max_iter")}}
    if len(archives) > 1:
        problems.append(f"the cards record {len(archives)} different archives: "
                        + ", ".join(sorted(a[:12] for a in archives)))
    return not problems, problems, {"endpoints": report}


def load_models(cache_dir: str | Path) -> ModelSet | None:
    out = Path(cache_dir) / "models"
    cards_file = out / "model_cards.json"
    if not cards_file.is_file():
        return None
    return ModelSet(directory=out, cards=json.loads(cards_file.read_text(encoding="utf-8")))
