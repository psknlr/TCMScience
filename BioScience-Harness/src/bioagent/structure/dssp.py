"""Secondary structure from backbone hydrogen bonds (Kabsch & Sander 1983, DSSP).

The hydrogen of each NH is placed 1 Å from N along the bisector DSSP uses (opposite the
previous residue's C=O), and a bond is counted when the electrostatic energy

    E = 0.084 * 332 * (1/r(ON) + 1/r(CH) - 1/r(OH) - 1/r(CN))  kcal/mol

is below -0.5. From the bonds:

* an n-turn at i is a bond from CO(i) to NH(i+n), n = 3, 4, 5;
* two consecutive 4-turns at i-1 and i make residues i..i+3 an alpha helix (H); the
  same for 3-turns gives a 3-10 helix (G) and for 5-turns a pi helix (I);
* a bridge between i and j is parallel when [CO(i-1)->NH(j) and CO(j)->NH(i+1)] or
  [CO(j-1)->NH(i) and CO(i)->NH(j+1)], antiparallel when [CO(i)->NH(j) and
  CO(j)->NH(i)] or [CO(i-1)->NH(j+1) and CO(j-1)->NH(i+1)]; residues in two or more
  consecutive bridges of one kind form a ladder (E), a lone bridge is B;
* remaining turn residues are T.

Priority is H > B/E > G > I > T, as in DSSP. Not reproduced: beta bulges (which DSSP
lets join ladders across a gap) and bends (S); a strand broken by a bulge therefore
reads as two shorter strands. The three-state summary maps H, G, I to helix and E, B to
strand.
"""

from __future__ import annotations

import numpy as np

__all__ = ["assign", "summary"]

Q = 0.084 * 332.0
CUTOFF = -0.5


def _hydrogens(n: np.ndarray, c: np.ndarray, o: np.ndarray) -> np.ndarray:
    h = np.full_like(n, np.nan)
    v = c[:-1] - o[:-1]
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    h[1:] = n[1:] + v / np.where(norm > 0, norm, 1.0)
    return h


def _hbonds(bb: dict[str, np.ndarray]) -> np.ndarray:
    """hb[i, j]: CO of residue i bonds to NH of residue j."""
    n, c, o = bb["N"], bb["C"], bb["O"]
    h = _hydrogens(n, c, o)
    k = len(n)
    with np.errstate(invalid="ignore", divide="ignore"):
        def dist(a, b):
            return np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
        r_on = dist(o, n)
        r_ch = dist(c, h)
        r_oh = dist(o, h)
        r_cn = dist(c, n)
        e = Q * (1 / r_on + 1 / r_ch - 1 / r_oh - 1 / r_cn)
    e = np.where(np.isfinite(e), e, 0.0)
    hb = e < CUTOFF
    idx = np.arange(k)
    hb[np.abs(idx[:, None] - idx[None, :]) < 2] = False     # no bonds to self or neighbour
    names = bb.get("resnames")
    if names is not None:
        pro = np.array([r == "PRO" for r in names])
        hb[:, pro] = False                                   # proline has no NH
    return hb


def assign(bb: dict[str, np.ndarray], resnames: list[str] | None = None) -> str:
    """One DSSP letter per residue (H, G, I, E, B, T or '-')."""
    data = dict(bb)
    if resnames is not None:
        data["resnames"] = resnames
    k = len(bb["CA"])
    ss = ["-"] * k
    if k < 3:
        return "".join(ss)
    hb = _hbonds(data)
    turns = {nn: np.array([i + nn < k and hb[i, i + nn] for i in range(k)])
             for nn in (3, 4, 5)}
    helix = {nn: np.zeros(k, dtype=bool) for nn in (3, 4, 5)}
    for nn in (3, 4, 5):
        t = turns[nn]
        for i in range(1, k):
            if t[i - 1] and t[i]:
                helix[nn][i:min(i + nn, k)] = True
    # bridges
    def h(a: int, b: int) -> bool:
        return 0 <= a < k and 0 <= b < k and bool(hb[a, b])
    par = np.zeros((k, k), dtype=bool)
    anti = np.zeros((k, k), dtype=bool)
    for i in range(1, k - 1):
        for j in range(1, k - 1):
            if abs(i - j) < 3:
                continue
            if (h(i - 1, j) and h(j, i + 1)) or (h(j - 1, i) and h(i, j + 1)):
                par[i, j] = True
            if (h(i, j) and h(j, i)) or (h(i - 1, j + 1) and h(j - 1, i + 1)):
                anti[i, j] = True
    ladder = np.zeros(k, dtype=bool)
    bridge = np.zeros(k, dtype=bool)
    for i in range(k):
        for j in range(k):
            if par[i, j]:
                bridge[i] = True
                if (i + 1 < k and j + 1 < k and par[i + 1, j + 1]) or \
                        (i > 0 and j > 0 and par[i - 1, j - 1]):
                    ladder[i] = True
            if anti[i, j]:
                bridge[i] = True
                if (i + 1 < k and j > 0 and anti[i + 1, j - 1]) or \
                        (i > 0 and j + 1 < k and anti[i - 1, j + 1]):
                    ladder[i] = True
    turn_res = np.zeros(k, dtype=bool)
    for nn in (3, 4, 5):
        for i in np.flatnonzero(turns[nn]):
            turn_res[i + 1:min(i + nn, k)] = True
    for i in range(k):
        if helix[4][i]:
            ss[i] = "H"
        elif ladder[i]:
            ss[i] = "E"
        elif bridge[i]:
            ss[i] = "B"
        elif helix[3][i]:
            ss[i] = "G"
        elif helix[5][i]:
            ss[i] = "I"
        elif turn_res[i]:
            ss[i] = "T"
    return "".join(ss)


def summary(ss: str) -> dict[str, float]:
    k = max(len(ss), 1)
    return {"helix": round(sum(c in "HGI" for c in ss) / k, 4),
            "strand": round(sum(c in "EB" for c in ss) / k, 4),
            "coil": round(sum(c not in "HGIEB" for c in ss) / k, 4)}
