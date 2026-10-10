"""Pairwise alignment: Needleman–Wunsch (global) and Smith–Waterman (local), linear gaps.

A local compute backend may run the dynamic programme (``_compute.KERNELS``): it gets the
normalised sequences and the score of every pair of letters that meet, and returns the
alignment and its score. The result is used only after it is checked here: the aligned
strings must spell the sequences, every cell of a local alignment must stay above zero, and
the score recomputed along the alignment, the way the dynamic programme adds it, must equal
the kernel's to the bit and in type (``int`` or ``float``, as Python's arithmetic would make
it). Otherwise this module's own implementation runs.

The check proves that a kernel's answer is a valid alignment with exactly its score. It
cannot prove that it is the same one of several equally good alignments that this module's
traceback would choose: that the browser's kernel breaks ties as this code does is shown by
its parity tests (studio/web/test/kernels.test.mjs, studio/runner/tests/test_browser_compute.py).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

from ._compute import kernel, note

__all__ = ["global_alignment", "local_alignment", "protein_alignment"]

_KERNEL_VALUE_LIMIT = 2 ** 30   # |score| ≤ 2**30 over ≤ 4e6 + 1 cells stays below 2**53: exact in a double


def _kernel_inputs(x: str, y: str, score: Callable[[str, str], Any], gap: Any):
    """The letters of each sequence and the score of every pair that meets in the
    programme (each pair of a letter of x and a letter of y does), or None when the kernel
    should not run: a pair the matrix lacks (the original raises its own error at the first
    one), or a value that is not a finite int or float."""
    alpha_x, alpha_y = "".join(sorted(set(x))), "".join(sorted(set(y)))
    try:
        table = [score(p, q) for p in alpha_x for q in alpha_y]
    except ValueError:
        return None
    for v in (*table, gap):
        if type(v) not in (int, float) or not math.isfinite(v) or abs(v) > _KERNEL_VALUE_LIMIT:
            return None
    return alpha_x, alpha_y, table


def _same_number(a: Any, b: Any) -> bool:
    """Equal in value and type, and for floats in the sign of zero: what JSON will write."""
    if type(a) is not type(b) or type(a) not in (int, float) or a != b:
        return False
    return type(a) is int or math.copysign(1.0, a) == math.copysign(1.0, b)


def _walk(ax: str, ay: str, x: str, y: str, i: int, j: int):
    """The cells an alignment passes through from (i, j), as (move, i, j) after each column;
    None when the strings do not spell x[i:] and y[j:] (or have a column of two gaps)."""
    if not isinstance(ax, str) or not isinstance(ay, str) or len(ax) != len(ay):
        return None
    steps = []
    for p, q in zip(ax, ay):
        if p == "-" and q == "-":
            return None
        if p != "-" and q != "-":
            if i >= len(x) or j >= len(y) or x[i] != p or y[j] != q:
                return None
            i, j = i + 1, j + 1
            steps.append(("diag", i, j))
        elif q == "-":
            if i >= len(x) or x[i] != p:
                return None
            i += 1
            steps.append(("up", i, j))
        else:
            if j >= len(y) or y[j] != q:
                return None
            j += 1
            steps.append(("left", i, j))
    return steps, i, j


def _checked_global(found: Any, x: str, y: str, score, gap):
    try:
        ax, ay, value = found
    except (TypeError, ValueError):
        return None
    walk = _walk(ax, ay, x, y, 0, 0)
    if walk is None or walk[1:] != (len(x), len(y)):
        return None
    total: Any = 0.0                      # H[0][0]; the first row and column are k * gap
    for move, i, j in walk[0]:
        if i == 0:
            total = j * gap
        elif j == 0:
            total = i * gap
        else:
            total = total + (score(x[i - 1], y[j - 1]) if move == "diag" else gap)
    return (ax, ay, value) if _same_number(total, value) else None


def _checked_local(found: Any, x: str, y: str, score, gap):
    try:
        ax, ay, value, start_a, end_a, start_b, end_b = found
    except (TypeError, ValueError):
        return None
    if not all(type(v) is int for v in (start_a, end_a, start_b, end_b)):
        return None
    if not (1 <= start_a <= end_a + 1 <= len(x) + 1 and 1 <= start_b <= end_b + 1 <= len(y) + 1):
        return None
    walk = _walk(ax, ay, x, y, start_a - 1, start_b - 1)
    if walk is None or walk[1:] != (end_a, end_b):
        return None
    if not walk[0] and (start_a, end_a, start_b, end_b) != (1, 0, 1, 0):
        return None                       # an empty alignment is the best of nothing
    total: Any = 0.0                      # the cell the alignment starts after holds 0.0
    for move, i, j in walk[0]:
        total = total + (score(x[i - 1], y[j - 1]) if move == "diag" else gap)
        if not total > 0:
            return None                   # a cell on the path that would have been a stop
    return (ax, ay, value, start_a, end_a, start_b, end_b) if _same_number(total, value) else None


def _accelerated(name: str, x: str, y: str, score, gap):
    run = kernel(name)
    if run is None:
        return None
    inputs = _kernel_inputs(x, y, score, gap)
    if inputs is None:
        note(name, "declined", "scores the kernel does not take")
        return None
    found = run(x, y, *inputs, gap)
    if found is None:
        note(name, "declined")
        return None
    checked = (_checked_global if name == "align.nw_linear" else _checked_local)(found, x, y, score, gap)
    note(name, "used" if checked is not None else "rejected",
         "" if checked is not None else "the alignment or its score did not check out")
    return checked


def _score_fn(match: float, mismatch: float, matrix: Any):
    if isinstance(matrix, str):
        from .matrices import matrix_by_name
        matrix = matrix_by_name(matrix)
    if matrix:
        def score(a: str, b: str) -> float:
            try:
                return float(matrix[a][b])
            except KeyError:
                try:
                    return float(matrix[b][a])
                except KeyError:
                    raise ValueError(f"substitution matrix has no entry for ({a}, {b})") from None
        return score
    return lambda a, b: match if a == b else mismatch


def _prepare(a: Any, b: Any) -> tuple[str, str]:
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError("both sequences must be strings")
    x, y = "".join(a.split()).upper(), "".join(b.split()).upper()
    if not x or not y:
        raise ValueError("sequences must not be empty")
    if len(x) * len(y) > 4_000_000:
        raise ValueError("sequences too long for in-memory alignment (product of lengths > 4e6)")
    return x, y


def _summarise(ax: str, ay: str, score: float) -> dict[str, Any]:
    matches = sum(1 for p, q in zip(ax, ay) if p == q and p != "-")
    gaps = ax.count("-") + ay.count("-")
    return {"score": score, "aligned_a": ax, "aligned_b": ay, "length": len(ax),
            "matches": matches, "identity": round(matches / len(ax), 4) if ax else 0.0,
            "gaps": gaps}


def global_alignment(a: str, b: str, match: float = 2.0, mismatch: float = -1.0,
                     gap: float = -2.0, matrix: Mapping[str, Mapping[str, float]] | str | None = None
                     ) -> dict[str, Any]:
    """Needleman–Wunsch with a linear gap penalty; ``matrix`` may name BLOSUM62."""
    x, y = _prepare(a, b)
    score = _score_fn(match, mismatch, matrix)
    fast = _accelerated("align.nw_linear", x, y, score, gap)
    if fast is not None:
        out = _summarise(*fast)
        out["mode"] = "global"
        return out
    n, m = len(x), len(y)
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    T = [[0] * (m + 1) for _ in range(n + 1)]          # 0 diag, 1 up, 2 left
    for i in range(1, n + 1):
        H[i][0], T[i][0] = i * gap, 1
    for j in range(1, m + 1):
        H[0][j], T[0][j] = j * gap, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + score(x[i - 1], y[j - 1])
            up = H[i - 1][j] + gap
            left = H[i][j - 1] + gap
            best = max(diag, up, left)
            H[i][j] = best
            T[i][j] = 0 if best == diag else (1 if best == up else 2)
    ax, ay, i, j = [], [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and T[i][j] == 0:
            ax.append(x[i - 1]); ay.append(y[j - 1]); i -= 1; j -= 1
        elif i > 0 and (j == 0 or T[i][j] == 1):
            ax.append(x[i - 1]); ay.append("-"); i -= 1
        else:
            ax.append("-"); ay.append(y[j - 1]); j -= 1
    out = _summarise("".join(reversed(ax)), "".join(reversed(ay)), H[n][m])
    out["mode"] = "global"
    return out


def local_alignment(a: str, b: str, match: float = 2.0, mismatch: float = -1.0,
                    gap: float = -2.0, matrix: Mapping[str, Mapping[str, float]] | str | None = None
                    ) -> dict[str, Any]:
    """Smith–Waterman with a linear gap penalty; reports the best local segment."""
    x, y = _prepare(a, b)
    score = _score_fn(match, mismatch, matrix)
    fast = _accelerated("align.sw_linear", x, y, score, gap)
    if fast is not None:
        ax, ay, best, start_a, end_a, start_b, end_b = fast
        out = _summarise(ax, ay, best)
        out.update({"mode": "local", "start_a": start_a, "end_a": end_a, "start_b": start_b,
                    "end_b": end_b})
        return out
    n, m = len(x), len(y)
    H = [[0.0] * (m + 1) for _ in range(n + 1)]
    T = [[3] * (m + 1) for _ in range(n + 1)]          # 3 = stop
    best, best_pos = 0.0, (0, 0)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = H[i - 1][j - 1] + score(x[i - 1], y[j - 1])
            up = H[i - 1][j] + gap
            left = H[i][j - 1] + gap
            val = max(0.0, diag, up, left)
            H[i][j] = val
            T[i][j] = 3 if val == 0 else (0 if val == diag else (1 if val == up else 2))
            if val > best:
                best, best_pos = val, (i, j)
    ax, ay = [], []
    i, j = best_pos
    end_a, end_b = i, j
    while i > 0 and j > 0 and T[i][j] != 3:
        if T[i][j] == 0:
            ax.append(x[i - 1]); ay.append(y[j - 1]); i -= 1; j -= 1
        elif T[i][j] == 1:
            ax.append(x[i - 1]); ay.append("-"); i -= 1
        else:
            ax.append("-"); ay.append(y[j - 1]); j -= 1
    out = _summarise("".join(reversed(ax)), "".join(reversed(ay)), best)
    out.update({"mode": "local", "start_a": i + 1, "end_a": end_a, "start_b": j + 1,
                "end_b": end_b})
    return out


def protein_alignment(a: str, b: str, mode: str = "global", gap: float = -4.0) -> dict[str, Any]:
    """Protein alignment scored with BLOSUM62 and a linear gap penalty."""
    if mode == "global":
        out = global_alignment(a, b, gap=gap, matrix="BLOSUM62")
    elif mode == "local":
        out = local_alignment(a, b, gap=gap, matrix="BLOSUM62")
    else:
        raise ValueError("mode must be 'global' or 'local'")
    out["matrix"] = "BLOSUM62"
    return out
