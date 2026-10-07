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
           "fetch_reference", "esmatlas_fold", "colabfold_batch", "load_esmfold"]

AMINO = set("ACDEFGHIKLMNPQRSTVWY")
PREDICTORS = ("esmatlas", "esmfold", "colabfold")
ESM_ATLAS = "https://api.esmatlas.com/foldSequence/v1/pdb/"
ESM_ATLAS_MAX = 400
ESMFOLD_MODEL = "facebook/esmfold_v1"


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
                      detail={"endpoint": ESM_ATLAS})


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
    if torch.cuda.is_available():
        model = model.cuda()
    ids = tok([seq], return_tensors="pt", add_special_tokens=False)["input_ids"]
    with torch.no_grad():
        out = model(ids.to(model.device))
    pdb = model.output_to_pdb(out)[0]
    parsed = parse_pdb(pdb, source=f"esmfold:{name}")
    pae = out["predicted_aligned_error"][0].cpu().numpy() if "predicted_aligned_error" in out \
        else None
    return Prediction(name=name, sequence=seq, pdb=pdb, plddt=parsed.plddt(),
                      method="ESMFold (local, transformers)",
                      version=f"esmfold_v1 / transformers {transformers.__version__}",
                      remote=False, pae=pae, request_sha256=_sha(seq.encode()),
                      response_sha256=_sha(pdb.encode()), seconds=round(time.time() - t0, 2))


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
    return Prediction(name=name, sequence=seq, pdb=text, plddt=parsed.plddt(),
                      method="AlphaFold2 (ColabFold)", version=version or "unknown",
                      remote=True, pae=pae, request_sha256=_sha(seq.encode()),
                      response_sha256=_sha(text.encode()), seconds=round(time.time() - t0, 2),
                      detail={"note": "ColabFold queries its MSA server unless configured "
                                      "otherwise"})


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
        return _esmatlas(name, seq, timeout=timeout)
    if method == "esmfold":
        return _esmfold_local(name, seq)
    return _colabfold(name, seq, Path(workdir))


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
