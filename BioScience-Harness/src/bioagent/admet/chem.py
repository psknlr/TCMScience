"""Molecules for ADMET: standardisation, descriptors, fingerprints.

Each input is standardised before anything is computed, with RDKit's MolStandardize:
clean-up, the largest organic fragment (salts and solvents dropped), and neutralisation
of charges that can be neutralised. Features are a 2048-bit Morgan count fingerprint
(radius 2, ECFP4-like; counts clipped at 255) and 24 physicochemical descriptors; the
applicability domain uses the bit form of the same fingerprint.

The applicability-domain search (``tanimoto_best``) is exact integer arithmetic on the
packed bits, 64 at a time (``np.bitwise_count``), in blocks whose temporaries stay under
``BLOCK_BYTES`` whatever the sizes, so that an endpoint's whole training set is searched
in a 32-bit browser runtime as it is natively, with the same answer.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np

__all__ = ["ChemError", "standardize", "descriptors", "featurize", "feature_rows", "bits",
           "tanimoto_best", "tanimoto_max", "similarity", "DESCRIPTORS", "FEATURES", "FP_BITS",
           "BLOCK_BYTES", "need"]

FP_BITS = 2048
DESCRIPTORS = ("MolWt", "MolLogP", "MolMR", "TPSA", "NumHDonors", "NumHAcceptors",
               "NumRotatableBonds", "RingCount", "NumAromaticRings", "NumAliphaticRings",
               "FractionCSP3", "HeavyAtomCount", "NHOHCount", "NOCount", "NumHeteroatoms",
               "LabuteASA", "BalabanJ", "BertzCT", "qed", "NumValenceElectrons",
               "NumSaturatedRings", "NumAromaticHeterocycles", "MaxPartialCharge",
               "MinPartialCharge")
#: Columns of a feature row: the count fingerprint, the descriptors, the formal charge.
FEATURES = FP_BITS + len(DESCRIPTORS) + 1
#: The most the domain search holds in temporaries at once (bytes; ``tanimoto_best``).
BLOCK_BYTES = 32 << 20


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
    return feature_rows(mols, [descriptors(m) for m in mols])


def feature_rows(mols: Sequence[Any], described: Sequence[dict[str, float]]) -> np.ndarray:
    """``featurize`` with each molecule's ``descriptors`` already computed."""
    if not mols:
        return np.zeros((0, FEATURES), dtype=np.float32)
    gen = _morgan(mols[0])
    rows = []
    for m, d in zip(mols, described, strict=True):
        fp = np.minimum(gen.GetCountFingerprintAsNumPy(m), 255).astype(np.float32)
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


# Bytes one (query, reference) pair holds at most while its block is searched: the AND
# of one 64-bit word, its popcount and the running intersection (8 + 1 + 2) during the
# word loop; then the intersection, the union (int64) and the similarity's mask, clamped
# union, quotient and result (2 + 8 + 1 + 8 + 8 + 8 = 35). Rounded up, with a quarter of
# the budget left for the block's references, transposed.
_PAIR_BYTES = 48


def _words(packed: np.ndarray) -> np.ndarray:
    """Packed fingerprints (n x bytes, uint8) as 64-bit words, zero-padded to a whole word."""
    pad = -packed.shape[1] % 8
    if pad:
        packed = np.pad(packed, ((0, 0), (0, pad)))
    return np.ascontiguousarray(packed).view(np.uint64)


def _packed(b: Any, what: str) -> np.ndarray:
    b = np.asarray(b)
    if b.dtype != np.uint8 or b.ndim != 2:
        raise ValueError(f"the {what} fingerprints must be packed bits, a 2-D uint8 array "
                         f"(not {b.ndim}-D {b.dtype})")
    return b


def similarity(inter: np.ndarray, union: np.ndarray) -> np.ndarray:
    """Tanimoto similarity from integer intersection and union counts (float64; 0 where
    the union is empty)."""
    inter, union = np.asarray(inter), np.asarray(union)
    return np.where(union > 0, inter / np.maximum(union, 1), 0.0)


def tanimoto_best(query: np.ndarray, ref: np.ndarray, *, block_bytes: int = BLOCK_BYTES
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each query fingerprint, the ``ref`` row most similar to it: ``(arg, inter,
    union)``, that row's index (the smallest on ties, as ``np.argmax`` gives) and the
    integer intersection and union of their bits.

    Exact: popcounts of 64-bit words, compared as the float64 ``similarity`` of the counts
    (two different ratios of counts this small never round to the same float64). The
    search runs over blocks of queries x references sized so that its temporaries stay
    under ``block_bytes``; a reference set too large for one block is searched in parts.
    """
    if not hasattr(np, "bitwise_count"):
        raise ChemError(f"the applicability-domain search needs numpy 2.0 or later "
                        f"(np.bitwise_count); this is numpy {np.__version__}")
    query, ref = _packed(query, "query"), _packed(ref, "reference")
    if query.shape[1] != ref.shape[1]:
        raise ValueError(f"the query and reference fingerprints differ in length "
                         f"({query.shape[1]} and {ref.shape[1]} bytes)")
    q, r = _words(query), _words(ref)
    n_q, n_r = len(q), len(r)
    arg = np.zeros(n_q, dtype=int)
    inter = np.zeros(n_q, dtype=np.int64)
    union = np.zeros(n_q, dtype=np.int64)
    if not n_q:
        return arg, inter, union
    if not n_r:
        raise ValueError("there are no reference fingerprints to compare with")
    q_pop = np.bitwise_count(q).sum(axis=1, dtype=np.int64)
    r_pop = np.bitwise_count(r).sum(axis=1, dtype=np.int64)
    counter = np.uint16 if q.shape[1] * 64 <= np.iinfo(np.uint16).max else np.uint32
    pairs = max(1, block_bytes // _PAIR_BYTES)
    n_rb = max(1, min(n_r, pairs, block_bytes // (4 * 8 * q.shape[1])))
    n_qb = max(1, min(n_q, pairs // n_rb))
    best = np.full(n_q, -1.0)
    for r0 in range(0, n_r, n_rb):
        rt = np.ascontiguousarray(r[r0:r0 + n_rb].T)          # words x references
        width = rt.shape[1]
        for q0 in range(0, n_q, n_qb):
            qq = q[q0:q0 + n_qb]
            both = np.empty((len(qq), width), dtype=np.uint64)
            count = np.empty((len(qq), width), dtype=np.uint8)
            common = np.zeros((len(qq), width), dtype=counter)
            for k in range(q.shape[1]):
                np.bitwise_and(qq[:, k, None], rt[k, None, :], out=both)
                np.bitwise_count(both, out=count)
                common += count
            del both, count
            joint = q_pop[q0:q0 + len(qq), None] + r_pop[None, r0:r0 + width] - common
            sim = similarity(common, joint)
            top = sim.argmax(axis=1)
            value = sim[np.arange(len(qq)), top]
            del sim
            # a later block of references wins only when strictly more similar, so the
            # smallest index stands on ties, as one argmax over every reference gives
            won = np.nonzero(value > best[q0:q0 + len(qq)])[0]
            at = q0 + won
            best[at] = value[won]
            arg[at] = r0 + top[won]
            inter[at] = common[won, top[won]]
            union[at] = joint[won, top[won]]
    return arg, inter, union


def tanimoto_max(query: np.ndarray, ref: np.ndarray, *, block_bytes: int = BLOCK_BYTES
                 ) -> tuple[np.ndarray, np.ndarray]:
    """For each query fingerprint: (max Tanimoto similarity to ``ref``, index of that row)."""
    arg, inter, union = tanimoto_best(query, ref, block_bytes=block_bytes)
    return similarity(inter, union), arg
