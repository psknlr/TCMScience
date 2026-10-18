"""Structure predictors.

``esmatlas``   ESMFold (Lin et al. 2023, *Science* 379:1123) through Meta's ESM Atlas
               service (``api.esmatlas.com``), sequences of up to 400 residues. The
               sequence leaves this machine, so it must be asked for (``allow_remote``);
               a confidential sequence should not be sent.
``esmfold``    ESMFold run here, through Hugging Face ``transformers``
               (``facebook/esmfold_v1``, ``torch`` required; a GPU is advisable). Also
               returns the predicted aligned error.
``colabfold``  AlphaFold2 through ``colabfold_batch`` when it is installed (Mirdita et al.
               2022, *Nature Methods* 19:679), with its own MSA settings.

Each returns a :class:`Prediction` holding the model as PDB text, per-residue pLDDT on a
0-100 scale, the PAE matrix when the method gives one, and what produced it: method,
version, whether it ran remotely, and digests of the request and the response.

``Prediction.detail`` also says where it computed and from which weights, as far as this
process can observe it:

* ``device``: ``remote:api.esmatlas.com`` for the service; for local ESMFold the device
  its parameters were on (``cuda:0`` or ``cpu``: it moves to a GPU only through CUDA, so
  a job given Apple's MPS computes on the CPU and says so); for ColabFold what its own
  log says (``gpu`` or ``cpu``; ``gpu`` is ColabFold's alone: its log reports a GPU
  without naming one, and a note says so), else null;
* ``checkpoint``: for local ESMFold the Hub model id, its revision and a SHA-256 of the
  weights themselves (each tensor's name, dtype, shape and bytes), ``verifiable: true``;
  for ColabFold its version, model type and the digest of the ``config.json`` it wrote,
  ``verifiable: false`` with ``scope: config_only`` (the AlphaFold2 parameters it loaded
  are not digested); for the ESM Atlas service ``verifiable: false``: the weights behind
  it cannot be observed from here, and no digest is claimed for them. Only a checkpoint
  with ``weights_sha256`` is verifiable;
* ``resources``: the peak GPU memory torch allocated, for local ESMFold on CUDA. It
  varies from run to run, so it goes into ``run.json`` (the predict stage and its
  summary), never into ``summary.json``.

Whatever the method, ``predict`` refuses a model whose sequence is not the one asked for
(``PredictionError``); nothing else is put in its place.

Every call that leaves this process — the fold request, a reference download, ColabFold
with its MSA server, the ESMFold weights from the Hub — goes through
:func:`bioagent.operations.operation`, so under a governed run it is checked against the
skill's manifest, executed through both kernels and recorded (outside one it runs as is).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..operations import (ALPHAFOLD_MODEL, COLABFOLD_BATCH, ESMATLAS_FOLD, ESMFOLD_WEIGHTS,
                          RCSB_ENTRY, fetch_bytes, operation)
from .pdbio import parse_pdb

__all__ = ["Prediction", "PredictionError", "predict", "PREDICTORS", "validate_sequence",
           "fetch_reference", "esmatlas_fold", "colabfold_batch", "load_esmfold",
           "require_sequence", "weights_digest", "ESMATLAS_DETAIL", "ESM_ATLAS_MAX"]

AMINO = set("ACDEFGHIKLMNPQRSTVWY")
PREDICTORS = ("esmatlas", "esmfold", "colabfold")
ESM_ATLAS = "https://api.esmatlas.com/foldSequence/v1/pdb/"
ESM_ATLAS_MAX = 400
ESMFOLD_MODEL = "facebook/esmfold_v1"
#: What can be said of a model the ESM Atlas service made: where, and that its weights
#: cannot be observed (so no checkpoint digest is recorded for them).
ESMATLAS_DETAIL = {
    "device": "remote:api.esmatlas.com",
    "checkpoint": {"model": "esmfold_v1", "verifiable": False,
                   "reason": "the ESM Atlas service runs its own copy of the weights, and "
                             "nothing it returns names or digests them"}}


class PredictionError(RuntimeError):
    """A sequence or a predictor that cannot give a model."""


@dataclass
class Prediction:
    name: str
    sequence: str
    pdb: str
    plddt: np.ndarray
    method: str
    version: str
    remote: bool
    pae: np.ndarray | None = None
    request_sha256: str = ""
    response_sha256: str = ""
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


def validate_sequence(seq: str) -> str:
    s = "".join(seq.split()).upper()
    if not s:
        raise PredictionError("an empty sequence")
    bad = sorted(set(s) - AMINO)
    if bad:
        raise PredictionError(f"the sequence holds {', '.join(bad)}, which are not standard "
                              "amino acids")
    return s


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _post(url: str, data: bytes, *, timeout: float, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, method="POST",
                                         headers={"Content-Type": "text/plain"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise PredictionError(f"{url} refused the request: HTTP {exc.code}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2 ** attempt)
    raise PredictionError(f"{url} did not answer after {retries} attempts: {last}")


def esmatlas_fold(*, sequence: str, timeout: float) -> bytes:
    """One fold request to the ESM Atlas service: the sequence out, the PDB text back.

    The implementation of the ``structure.esmatlas.fold`` operation; the sequence is the
    argument, so PSH's gate classifies what is actually sent.
    """
    return _post(ESM_ATLAS, sequence.encode(), timeout=timeout)


def _esmatlas(name: str, seq: str, *, timeout: float) -> Prediction:
    if len(seq) > ESM_ATLAS_MAX:
        raise PredictionError(f"{name}: {len(seq)} residues; the ESM Atlas service folds at "
                              f"most {ESM_ATLAS_MAX}; run ESMFold or ColabFold locally")
    body = seq.encode()
    t0 = time.time()
    raw = operation(ESMATLAS_FOLD, esmatlas_fold, sequence=seq, timeout=timeout)
    text = raw.decode("utf-8", errors="replace")
    model = parse_pdb(text, source=f"esmatlas:{name}")
    if model.sequence() != seq:
        raise PredictionError(f"{name}: the service returned a model of a different sequence")
    return Prediction(name=name, sequence=seq, pdb=text, plddt=model.plddt(),
                      method="ESMFold (ESM Atlas service)", version="esmfold_v1",
                      remote=True, request_sha256=_sha(body), response_sha256=_sha(raw),
                      seconds=round(time.time() - t0, 2),
                      detail={"endpoint": ESM_ATLAS, **ESMATLAS_DETAIL})


def load_esmfold(*, model_id: str = ESMFOLD_MODEL) -> tuple[Any, Any]:
    """The ESMFold tokenizer and model: from the local cache, or downloaded from the Hub.

    The implementation of the ``structure.esmfold.weights`` operation. Unless the Hub is
    set offline, ``from_pretrained`` contacts huggingface.co even for cached weights, so
    loading them is an external call like any other.
    """
    from transformers import AutoTokenizer, EsmForProteinFolding

    return (AutoTokenizer.from_pretrained(model_id),
            EsmForProteinFolding.from_pretrained(model_id))


def _esmfold_local(name: str, seq: str) -> Prediction:
    try:
        import torch
        import transformers
    except ImportError as exc:
        raise PredictionError("local ESMFold needs torch and transformers "
                              "(pip install torch transformers)") from exc
    t0 = time.time()
    tok, model = operation(ESMFOLD_WEIGHTS, load_esmfold, model_id=ESMFOLD_MODEL)
    model.eval()
    # the weights as loaded, digested on the CPU before any move (equal weights, equal
    # digest, whatever file or cache they came from)
    revision = getattr(getattr(model, "config", None), "_commit_hash", None)
    checkpoint = {"model_id": ESMFOLD_MODEL, "revision": revision,
                  "weights_sha256": _esmfold_digest(model, revision), "verifiable": True}
    cuda = torch.cuda.is_available()
    if cuda:
        model = model.cuda()
        torch.cuda.reset_peak_memory_stats()
    ids = tok([seq], return_tensors="pt", add_special_tokens=False)["input_ids"]
    with torch.no_grad():
        out = model(ids.to(model.device))
    device = str(next(model.parameters()).device)
    resources: dict[str, Any] = {"torch": torch.__version__}
    if cuda:
        resources.update({"cuda": torch.version.cuda,
                          "gpu": torch.cuda.get_device_name(next(model.parameters()).device),
                          "peak_gpu_memory_mb": round(torch.cuda.max_memory_allocated() / 2**20,
                                                      1)})
    pdb = model.output_to_pdb(out)[0]
    parsed = parse_pdb(pdb, source=f"esmfold:{name}")
    pae = out["predicted_aligned_error"][0].cpu().numpy() if "predicted_aligned_error" in out \
        else None
    detail: dict[str, Any] = {"device": device, "checkpoint": checkpoint,
                              "resources": resources}
    if not cuda:
        detail["device_note"] = ("ESMFold moves to a GPU here only through CUDA, and no "
                                 "CUDA device was visible, so it ran on the CPU")
    return Prediction(name=name, sequence=seq, pdb=pdb, plddt=parsed.plddt(),
                      method="ESMFold (local, transformers)",
                      version=f"esmfold_v1 / transformers {transformers.__version__}",
                      remote=False, pae=pae, request_sha256=_sha(seq.encode()),
                      response_sha256=_sha(pdb.encode()), seconds=round(time.time() - t0, 2),
                      detail=detail)


#: (model id, Hub revision) -> the weights digest, so that a run of many sequences digests
#: several GB of weights once and not once per sequence
_ESMFOLD_DIGESTS: dict[tuple[str, str], str] = {}


def _esmfold_digest(model: Any, revision: str | None) -> str:
    """The weights digest of the loaded ESMFold model, computed once per process for each
    Hub revision (a revision is a commit of the model repository, so its weights are the
    same bytes); a model without a known revision is digested on every load."""
    if not revision:
        return weights_digest(model.state_dict())
    key = (ESMFOLD_MODEL, str(revision))
    if key not in _ESMFOLD_DIGESTS:
        _ESMFOLD_DIGESTS[key] = weights_digest(model.state_dict())
    return _ESMFOLD_DIGESTS[key]


def weights_digest(state: Any) -> str:
    """SHA-256 of model weights alone: each tensor's name, dtype, shape and bytes, in name
    order (as ``omics.sc.analysis`` digests scVI's)."""
    h = hashlib.sha256()
    for key in sorted(state):
        a = np.ascontiguousarray(state[key].detach().cpu().numpy())
        h.update(f"{key}\0{a.dtype.str}\0{a.shape}\0".encode())
        h.update(a.tobytes())
    return h.hexdigest()


def _colabfold_device(out: Path) -> str | None:
    """What colabfold_batch's own log says it ran on, or None when it does not say."""
    for log in sorted(out.glob("log*.txt")):
        text = log.read_text(encoding="utf-8", errors="replace").lower()
        if "running on gpu" in text:
            return "gpu"
        if "no gpu detected" in text or "running on cpu" in text:
            return "cpu"
    return None


def colabfold_batch(*, executable: str, fasta: str, out_dir: str) -> dict[str, Any]:
    """One ``colabfold_batch`` run: its exit code and the end of its error output.

    The implementation of the ``structure.colabfold.batch`` operation. ColabFold sends the
    sequence to its MSA server from inside the subprocess, where no in-process guard can
    see it, so the run as a whole is the operation that is declared and recorded.
    """
    done = subprocess.run([executable, "--num-models", "1", fasta, out_dir],
                          capture_output=True, text=True)
    return {"returncode": done.returncode, "stderr_tail": done.stderr[-800:]}


def _colabfold(name: str, seq: str, workdir: Path) -> Prediction:
    exe = shutil.which("colabfold_batch")
    if exe is None:
        raise PredictionError("colabfold_batch is not installed")
    work = workdir / f"colabfold_{name}"
    work.mkdir(parents=True, exist_ok=True)
    fasta = work / "input.fasta"
    fasta.write_text(f">{name}\n{seq}\n")
    t0 = time.time()
    done = operation(COLABFOLD_BATCH, colabfold_batch, executable=exe, fasta=str(fasta),
                     out_dir=str(work / "out"))
    if done["returncode"] != 0:
        raise PredictionError(f"colabfold_batch failed: {done['stderr_tail']}")
    pdbs = sorted((work / "out").glob(f"{name}*rank_001*.pdb"))
    scores = sorted((work / "out").glob(f"{name}*scores_rank_001*.json"))
    if not pdbs:
        raise PredictionError("colabfold_batch wrote no ranked model")
    text = pdbs[0].read_text()
    parsed = parse_pdb(text, source=str(pdbs[0]))
    pae = None
    if scores:
        doc = json.loads(scores[0].read_text())
        if "pae" in doc:
            pae = np.array(doc["pae"], dtype=float)
    version = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
    config_file = work / "out" / "config.json"
    checkpoint: dict[str, Any] = {"version": version or "unknown", "verifiable": False,
                                  "reason": "colabfold_batch wrote no config.json"}
    if config_file.is_file():
        data = config_file.read_bytes()
        try:
            cfg = json.loads(data)
        except ValueError:
            cfg = {}
        # the configuration names the model type, not the weights it loaded: the
        # checkpoint stays unverifiable while the parameters themselves are not digested
        checkpoint = {"version": version or "unknown", "config_sha256": _sha(data),
                      "model_type": cfg.get("model_type"), "verifiable": False,
                      "scope": "config_only",
                      "reason": "the configuration colabfold_batch wrote is digested; the "
                                "AlphaFold2 parameters are not"}
    device = _colabfold_device(work / "out")
    detail: dict[str, Any] = {"note": "ColabFold queries its MSA server unless configured "
                                      "otherwise", "device": device, "checkpoint": checkpoint}
    if device is None:
        detail["device_note"] = "colabfold_batch's log does not say whether it used a GPU"
    elif device == "gpu":
        # ColabFold's log says "Running on GPU" without naming the device, so the value
        # is 'gpu' (a ColabFold-only value), not a cuda index this process never saw
        detail["device_note"] = ("colabfold_batch's log reports a GPU without naming it; "
                                 "the device is as reported, not observed here")
    return Prediction(name=name, sequence=seq, pdb=text, plddt=parsed.plddt(),
                      method="AlphaFold2 (ColabFold)", version=version or "unknown",
                      remote=True, pae=pae, request_sha256=_sha(seq.encode()),
                      response_sha256=_sha(text.encode()), seconds=round(time.time() - t0, 2),
                      detail=detail)


def predict(name: str, sequence: str, *, method: str = "esmatlas", allow_remote: bool = False,
            workdir: str | Path = ".", timeout: float = 300.0) -> Prediction:
    seq = validate_sequence(sequence)
    if method not in PREDICTORS:
        raise PredictionError(f"method is one of {', '.join(PREDICTORS)}")
    if method in ("esmatlas", "colabfold") and not allow_remote:
        raise PredictionError(
            f"{method} sends the sequence to a third-party service; pass allow_remote "
            "(--allow-remote) to permit it, or use method 'esmfold' to fold locally")
    if method == "esmatlas":
        pred = _esmatlas(name, seq, timeout=timeout)
    elif method == "esmfold":
        pred = _esmfold_local(name, seq)
    else:
        pred = _colabfold(name, seq, Path(workdir))
    return require_sequence(pred, seq)


def require_sequence(pred: Prediction, seq: str) -> Prediction:
    """The prediction, if its model is of ``seq``; else PredictionError. A model of
    another sequence (a service's mix-up, a ColabFold run on the wrong FASTA) is not a
    model of this one, and nothing is substituted for it."""
    got = parse_pdb(pred.pdb, source=pred.name).sequence()
    if got != seq:
        same = sum(a == b for a, b in zip(got, seq))
        raise PredictionError(f"{pred.name}: {pred.method} returned a model of a different "
                              f"sequence ({len(got)} residues, {same} of {len(seq)} "
                              "positions identical); it is refused, not used")
    return pred


def fetch_reference(ref: str, *, allow_remote: bool, timeout: float = 120.0
                    ) -> tuple[str, str, dict[str, Any]]:
    """(PDB text, chain or "", provenance) for ``PDB:1UBQ[:A]``, ``UniProt:P0CG48`` (the
    AlphaFold DB model) or a local file path."""
    path = Path(ref)
    if path.is_file():
        data = path.read_bytes()
        return data.decode("utf-8", errors="replace"), "", {
            "reference": str(path), "sha256": _sha(data), "kind": "file"}
    kind, _, rest = ref.partition(":")
    if not rest or kind.lower() not in ("pdb", "uniprot"):
        raise PredictionError(f"reference {ref!r}: a file, PDB:<id>[:chain] or UniProt:<acc>")
    if not allow_remote:
        raise PredictionError("fetching a reference reaches RCSB or AlphaFold DB; pass "
                              "allow_remote")
    if kind.lower() == "pdb":
        code, _, chain = rest.partition(":")
        url = f"https://files.rcsb.org/download/{code.upper()}.pdb"
        data = operation(RCSB_ENTRY, fetch_bytes, url=url, timeout=timeout)
        return data.decode("utf-8", errors="replace"), chain, {
            "reference": f"PDB {code.upper()}", "url": url, "sha256": _sha(data),
            "kind": "experimental", "licence": "PDB data: CC0 1.0"}
    acc = rest.strip()
    api = f"https://alphafold.ebi.ac.uk/api/prediction/{acc}"
    entries = json.loads(operation(ALPHAFOLD_MODEL, fetch_bytes, url=api, timeout=timeout))
    if not entries:
        raise PredictionError(f"AlphaFold DB has no model for {acc}")
    # The model's address comes from the service; the broker holds it to the component's
    # declared host like any other.
    url = entries[0]["pdbUrl"]
    data = operation(ALPHAFOLD_MODEL, fetch_bytes, url=url, timeout=timeout)
    return data.decode("utf-8", errors="replace"), "", {
        "reference": f"AlphaFold DB {entries[0].get('entryId', acc)}", "url": url,
        "sha256": _sha(data), "kind": "predicted",
        "licence": "AlphaFold DB: CC BY 4.0"}
