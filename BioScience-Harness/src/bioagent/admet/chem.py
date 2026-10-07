"""Molecules for ADMET: standardisation, descriptors, fingerprints.

Each input is standardised before anything is computed, with RDKit's MolStandardize:
clean-up, the largest organic fragment (salts and solvents dropped), and neutralisation
of charges that can be neutralised. Features are a 2048-bit Morgan count fingerprint
(radius 2, ECFP4-like; counts clipped at 255) and 24 physicochemical descriptors; the
applicability domain uses the bit form of the same fingerprint.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

__all__ = ["ChemError", "standardize", "descriptors", "featurize", "bits", "tanimoto_max",
           "DESCRIPTORS", "need"]

FP_BITS = 2048
DESCRIPTORS = ("MolWt", "MolLogP", "MolMR", "TPSA", "NumHDonors", "NumHAcceptors",
               "NumRotatableBonds", "RingCount", "NumAromaticRings", "NumAliphaticRings",
               "FractionCSP3", "HeavyAtomCount", "NHOHCount", "NOCount", "NumHeteroatoms",
               "LabuteASA", "BalabanJ", "BertzCT", "qed", "NumValenceElectrons",
               "NumSaturatedRings", "NumAromaticHeterocycles", "MaxPartialCharge",
               "MinPartialCharge")


class ChemError(ValueError):
    """A molecule that cannot be read or standardised."""


def need(module: str):
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ChemError(f"ADMET needs {module} (pip install 'bioagent[admet]': rdkit, "
                        "scikit-learn)") from exc


def standardize(smiles: str) -> Any:
    Chem = need("rdkit.Chem")
    rdMolStandardize = need("rdkit.Chem.MolStandardize.rdMolStandardize")
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ChemError(f"{smiles!r} is not a valid SMILES")
    mol = rdMolStandardize.Cleanup(mol)
    mol = rdMolStandardize.FragmentParent(mol)
    mol = rdMolStandardize.Uncharger().uncharge(mol)
    Chem.SanitizeMol(mol)
    return mol


def descriptors(mol: Any) -> dict[str, float]:
    Descriptors = need("rdkit.Chem.Descriptors")
    out = {}
    for name in DESCRIPTORS:
        fn = getattr(Descriptors, name)
        try:
            v = float(fn(mol))
        except Exception:                                     # noqa: BLE001
            v = float("nan")
        out[name] = v if np.isfinite(v) else float("nan")
    Chem = need("rdkit.Chem")
    out["FormalCharge"] = float(Chem.GetFormalCharge(mol))
    return out


def _morgan(mol: Any):
    rdFingerprintGenerator = need("rdkit.Chem.rdFingerprintGenerator")
    return rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=FP_BITS)


def featurize(mols: Sequence[Any]) -> np.ndarray:
    """Counts fingerprint + descriptors, one row per molecule (float32)."""
    if not mols:
        return np.zeros((0, FP_BITS + len(DESCRIPTORS) + 1), dtype=np.float32)
    gen = _morgan(mols[0])
    rows = []
    for m in mols:
        fp = np.minimum(gen.GetCountFingerprintAsNumPy(m), 255).astype(np.float32)
        d = descriptors(m)
        rows.append(np.concatenate([fp, np.array([d[k] for k in (*DESCRIPTORS, "FormalCharge")],
                                                 dtype=np.float32)]))
    x = np.vstack(rows)
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def bits(mols: Sequence[Any]) -> np.ndarray:
    """Packed Morgan bits (n x 256 uint8) for Tanimoto similarity."""
    if not mols:
        return np.zeros((0, FP_BITS // 8), dtype=np.uint8)
    gen = _morgan(mols[0])
    arr = np.vstack([gen.GetFingerprintAsNumPy(m).astype(np.uint8) for m in mols])
    return np.packbits(arr, axis=1)


_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.int32)


def tanimoto_max(query: np.ndarray, ref: np.ndarray, *, chunk: int = 512
                 ) -> tuple[np.ndarray, np.ndarray]:
    """For each query fingerprint: (max Tanimoto similarity to ``ref``, index of that row)."""
    out = np.zeros(len(query))
    arg = np.zeros(len(query), dtype=int)
    ref_pop = _POP[ref].sum(axis=1)
    for s in range(0, len(query), chunk):
        q = query[s:s + chunk]
        q_pop = _POP[q].sum(axis=1)
        inter = _POP[np.bitwise_and(q[:, None, :], ref[None, :, :])].sum(axis=2)
        union = q_pop[:, None] + ref_pop[None, :] - inter
        sim = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
        out[s:s + chunk] = sim.max(axis=1)
        arg[s:s + chunk] = sim.argmax(axis=1)
    return out, arg
