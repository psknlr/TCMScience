"""Structure geometry: superposition, comparison scores, alignment, dihedrals.

* **Kabsch** superposition and RMSD.
* **TM-score** (Zhang & Skolnick 2004, *Proteins* 57:702) for a given residue
  correspondence, normalised by the reference length with
  d0 = 1.24 (L - 15)^(1/3) - 1.8 (at least 0.5), maximised over superpositions by the
  TM-score program's heuristic: seed superpositions on fragments of L, L/2, L/4, ...
  (at least 4) residues, each refined by re-superposing on the residues within
  d0 (at least 4.5, at most 8 Å) until the set stops changing.
* **GDT-TS**: the mean over 1, 2, 4 and 8 Å of the largest share of residues within the
  cut-off under one superposition, found the same way.
* **Global alignment** of two sequences with BLOSUM62 and affine gaps (Gotoh), end gaps
  free, to pair a model's residues with an experimental structure's that lacks some.
* Backbone **dihedrals**, the radius of gyration, clashes and contact maps.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

__all__ = ["kabsch", "rmsd", "tm_score", "gdt_ts", "align", "dihedrals", "ramachandran",
           "radius_of_gyration", "clashes", "contact_map", "AlignmentResult", "Comparison",
           "compare"]


def kabsch(p: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(R, t) minimising |R p + t - q|."""
    pc, qc = p.mean(axis=0), q.mean(axis=0)
    h = (p - pc).T @ (q - qc)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T))
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, qc - r @ pc


def rmsd(p: np.ndarray, q: np.ndarray, *, superpose: bool = True) -> float:
    if superpose:
        r, t = kabsch(p, q)
        p = p @ r.T + t
    return float(np.sqrt(((p - q) ** 2).sum(axis=1).mean()))


def _d0(length: int) -> float:
    return max(1.24 * (length - 15) ** (1.0 / 3.0) - 1.8, 0.5) if length > 21 else 0.5


def _search(p: np.ndarray, q: np.ndarray, score, cutoff: float) -> tuple[float, np.ndarray]:
    """Best score over fragment-seeded, iteratively refined superpositions."""
    n = len(p)
    best, best_d = -1.0, np.full(n, np.inf)
    length = n
    seeds = []
    while length >= 4 or not seeds:
        step = max(1, length // 2) if length < n else 1
        seeds += [(s, length) for s in range(0, max(n - length, 0) + 1, step)]
        if length == 4 or length <= 4:
            break
        length = max(length // 2, 4)
    for start, length in seeds:
        sel = np.arange(start, min(start + length, n))
        for _ in range(20):
            if len(sel) < 3:
                break
            r, t = kabsch(p[sel], q[sel])
            d = np.sqrt(((p @ r.T + t - q) ** 2).sum(axis=1))
            value = score(d)
            if value > best:
                best, best_d = value, d
            cut = cutoff
            new = np.flatnonzero(d < cut)
            while len(new) < 3 and cut < 30:
                cut += 0.5
                new = np.flatnonzero(d < cut)
            if np.array_equal(new, sel):
                break
            sel = new
    return best, best_d


def tm_score(model: np.ndarray, reference: np.ndarray, *, length: int | None = None
             ) -> float:
    """TM-score of paired CA coordinates, normalised by ``length`` (default: pairs)."""
    lref = length or len(reference)
    d0 = _d0(lref)
    cutoff = min(max(d0, 4.5), 8.0)

    def score(d: np.ndarray) -> float:
        return float((1.0 / (1.0 + (d / d0) ** 2)).sum() / lref)

    value, _ = _search(np.asarray(model, float), np.asarray(reference, float), score, cutoff)
    return value


def gdt_ts(model: np.ndarray, reference: np.ndarray, *, length: int | None = None) -> float:
    lref = length or len(reference)
    shares = []
    for c in (1.0, 2.0, 4.0, 8.0):
        value, _ = _search(np.asarray(model, float), np.asarray(reference, float),
                           lambda d, c=c: float((d <= c).sum() / lref), c)
        shares.append(value)
    return float(np.mean(shares))


# ------------------------------------------------------------------ alignment

_B62_ORDER = "ARNDCQEGHILKMFPSTWYVBZX*"
_B62_ROWS = """
 4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0 -2 -1  0 -4
-1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3 -1  0 -1 -4
-2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3  3  0 -1 -4
-2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3  4  1 -1 -4
 0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1 -3 -3 -2 -4
-1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2  0  3 -1 -4
-1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2  1  4 -1 -4
 0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3 -1 -2 -1 -4
-2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3  0  0 -1 -4
-1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3 -3 -3 -1 -4
-1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1 -4 -3 -1 -4
-1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2  0  1 -1 -4
-1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1 -3 -1 -1 -4
-2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1 -3 -3 -1 -4
-1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2 -2 -1 -2 -4
 1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2  0  0  0 -4
 0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0 -1 -1  0 -4
-3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3 -4 -3 -2 -4
-2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1 -3 -2 -1 -4
 0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4 -3 -2 -1 -4
-2 -1  3  4 -3  0  1 -1  0 -3 -4  0 -3 -3 -2  0 -1 -4 -3 -3  4  1 -1 -4
-1  0  0  1 -3  3  4 -2  0 -3 -3  1 -1 -3 -1  0 -1 -3 -2 -2  1  4 -1 -4
 0 -1 -1 -1 -2 -1 -1 -1 -1 -1 -1 -1 -1 -1 -2  0  0 -2 -1 -1 -1 -1 -1 -4
-4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4 -4  1
"""
_B62 = np.array([[int(v) for v in row.split()] for row in _B62_ROWS.strip().splitlines()])
_IDX = {a: i for i, a in enumerate(_B62_ORDER)}


@dataclass(frozen=True)
class AlignmentResult:
    pairs: list[tuple[int, int]]          # (index in a, index in b) of aligned residues
    identity: float                       # identical pairs / aligned pairs
    coverage_a: float
    coverage_b: float
    score: float


def align(a: str, b: str, *, gap_open: float = 11.0, gap_extend: float = 1.0
          ) -> AlignmentResult:
    """Global alignment with free end gaps (Gotoh's affine-gap recursion)."""
    n, m = len(a), len(b)
    ia = np.array([_IDX.get(c, _IDX["X"]) for c in a.upper()], dtype=int)
    ib = np.array([_IDX.get(c, _IDX["X"]) for c in b.upper()], dtype=int)
    neg = -1e18
    mats = {k: np.full((n + 1, m + 1), neg) for k in "MXY"}
    back = {k: np.zeros((n + 1, m + 1), dtype=np.int8) for k in "MXY"}
    M, X, Y = mats["M"], mats["X"], mats["Y"]
    M[0, 0] = 0.0
    X[1:, 0] = 0.0                                  # free leading gaps
    Y[0, 1:] = 0.0
    for i in range(1, n + 1):
        prev = np.vstack([M[i - 1, :-1], X[i - 1, :-1], Y[i - 1, :-1]])
        back["M"][i, 1:] = np.argmax(prev, axis=0)
        M[i, 1:] = prev.max(axis=0) + _B62[ia[i - 1], ib]
        up = np.vstack([M[i - 1, 1:] - gap_open, X[i - 1, 1:] - gap_extend,
                        Y[i - 1, 1:] - gap_open])
        back["X"][i, 1:] = np.argmax(up, axis=0)
        X[i, 1:] = up.max(axis=0)
        for j in range(1, m + 1):
            options = (M[i, j - 1] - gap_open, X[i, j - 1] - gap_open, Y[i, j - 1] - gap_extend)
            k = int(np.argmax(options))
            back["Y"][i, j], Y[i, j] = k, options[k]
    # free trailing gaps: finish anywhere on the last row or column
    best, end = neg, ("M", n, m)
    for state, mat in mats.items():
        for i, j in [(n, j) for j in range(m + 1)] + [(i, m) for i in range(n + 1)]:
            if mat[i, j] > best:
                best, end = mat[i, j], (state, i, j)
    state, i, j = end
    pairs = []
    names = "MXY"
    while i > 0 and j > 0:
        k = int(back[state][i, j])
        if state == "M":
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif state == "X":
            i -= 1
        else:
            j -= 1
        state = names[k]
    pairs.reverse()
    ident = sum(1 for x, y in pairs if a[x].upper() == b[y].upper())
    return AlignmentResult(pairs=pairs, identity=ident / len(pairs) if pairs else 0.0,
                           coverage_a=len(pairs) / n if n else 0.0,
                           coverage_b=len(pairs) / m if m else 0.0, score=float(best))


# ------------------------------------------------------------------ geometry

def _dihedral(p0, p1, p2, p3) -> np.ndarray:
    b0, b1, b2 = p0 - p1, p2 - p1, p3 - p2
    b1n = b1 / np.linalg.norm(b1, axis=-1, keepdims=True)
    v = b0 - (b0 * b1n).sum(-1, keepdims=True) * b1n
    w = b2 - (b2 * b1n).sum(-1, keepdims=True) * b1n
    x = (v * w).sum(-1)
    y = (np.cross(b1n, v) * w).sum(-1)
    return np.degrees(np.arctan2(y, x))


def dihedrals(bb: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """phi, psi and omega per residue in degrees (NaN at the termini)."""
    n, ca, c = bb["N"], bb["CA"], bb["C"]
    k = len(ca)
    phi, psi, omega = (np.full(k, np.nan) for _ in range(3))
    if k > 1:
        phi[1:] = _dihedral(c[:-1], n[1:], ca[1:], c[1:])
        psi[:-1] = _dihedral(n[:-1], ca[:-1], c[:-1], n[1:])
        omega[:-1] = _dihedral(ca[:-1], c[:-1], n[1:], ca[1:])
    return {"phi": phi, "psi": psi, "omega": omega}


def ramachandran(phi: np.ndarray, psi: np.ndarray, resnames: list[str]) -> np.ndarray:
    """True where (phi, psi) lies in a coarse allowed region: right-handed helix, beta,
    left-handed helix; any for glycine; proline's phi near -65. A screen, not MolProbity's
    contours."""
    ok = np.zeros(len(phi), dtype=bool)
    for i, (f, s) in enumerate(zip(phi, psi)):
        if not (np.isfinite(f) and np.isfinite(s)):
            ok[i] = True
            continue
        name = resnames[i]
        if name == "GLY":
            ok[i] = True
            continue
        alpha = -170 <= f <= -20 and -125 <= s <= 55
        beta = -180 <= f <= -40 and (s >= 80 or s <= -165)
        left = 25 <= f <= 100 and -25 <= s <= 100
        if name == "PRO":
            ok[i] = -100 <= f <= -40 and (alpha or beta)
        else:
            ok[i] = alpha or beta or left
    return ok


def radius_of_gyration(ca: np.ndarray) -> float:
    x = ca[np.isfinite(ca).all(axis=1)]
    return float(np.sqrt(((x - x.mean(axis=0)) ** 2).sum(axis=1).mean())) if len(x) else 0.0


def clashes(coords: np.ndarray, residue: np.ndarray, *, cutoff: float = 2.2) -> int:
    """Heavy-atom pairs closer than ``cutoff`` Å in residues two or more apart."""
    from scipy.spatial import cKDTree
    tree = cKDTree(coords)
    pairs = tree.query_pairs(cutoff, output_type="ndarray")
    if not len(pairs):
        return 0
    return int((np.abs(residue[pairs[:, 0]] - residue[pairs[:, 1]]) >= 2).sum())


def contact_map(cb: np.ndarray, cutoff: float = 8.0) -> np.ndarray:
    d = np.sqrt(((cb[:, None, :] - cb[None, :, :]) ** 2).sum(-1))
    return (d < cutoff).astype(float)


@dataclass(frozen=True)
class Comparison:
    tm_score: float              # normalised by the reference
    tm_score_model: float        # normalised by the model
    rmsd: float                  # over every aligned pair, after superposition
    gdt_ts: float
    aligned: int
    identity: float
    reference_length: int
    model_length: int


def compare(model_seq: str, model_ca: np.ndarray, ref_seq: str, ref_ca: np.ndarray
            ) -> Comparison:
    al = align(model_seq, ref_seq)
    pairs = [(i, j) for i, j in al.pairs
             if np.isfinite(model_ca[i]).all() and np.isfinite(ref_ca[j]).all()]
    if len(pairs) < 3:
        raise ValueError("fewer than three residues pair between the model and the reference")
    mi = np.array([i for i, _ in pairs])
    ri = np.array([j for _, j in pairs])
    p, q = model_ca[mi], ref_ca[ri]
    lref = int(np.isfinite(ref_ca).all(axis=1).sum())
    lmod = int(np.isfinite(model_ca).all(axis=1).sum())
    return Comparison(tm_score=round(tm_score(p, q, length=lref), 4),
                      tm_score_model=round(tm_score(p, q, length=lmod), 4),
                      rmsd=round(rmsd(p, q), 3), gdt_ts=round(gdt_ts(p, q, length=lref), 4),
                      aligned=len(pairs), identity=round(al.identity, 4),
                      reference_length=lref, model_length=lmod)

