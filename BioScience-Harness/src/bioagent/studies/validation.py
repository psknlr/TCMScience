"""Prediction checked on people the model has not seen.

Cross-validation splits by subject: every sample, cell or visit of one person is in the
training part or in the test part, never both. Feature selection and scaling happen inside
each training fold. Each estimate comes with a permutation null, where outcomes are
reassigned between subjects, not between samples.

``sample_split_cv`` splits samples at random. It exists only to show, on the same data, how
much a leaky split inflates performance; its output is labelled so.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

import numpy as np

__all__ = ["auc", "grouped_folds", "assert_disjoint", "fit_logistic", "fit_ridge",
           "top_k_by_t", "grouped_cv", "sample_split_cv"]


def auc(y: Sequence[int], score: Sequence[float]) -> float:
    """Area under the ROC curve (Mann-Whitney, ties counted half)."""
    y = np.asarray(y).astype(int)
    s = np.asarray(score, float)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return math.nan
    order = np.argsort(np.r_[pos, neg], kind="mergesort")
    ranks = np.empty(len(order))
    vals = np.r_[pos, neg][order]
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2)
                 / (len(pos) * len(neg)))


def grouped_folds(groups: Sequence[str], k: int, *, seed: int = 0,
                  y: Sequence | None = None) -> list[np.ndarray]:
    """Test-index arrays; whole groups per fold. With a group-constant binary ``y`` the
    groups are dealt per class so each fold sees both."""
    g = np.asarray([str(x) for x in groups])
    uniq = np.unique(g)
    if k > len(uniq):
        raise ValueError(f"{len(uniq)} groups cannot fill {k} folds")
    rng = np.random.default_rng(seed)
    if y is not None:
        yv = np.asarray(y)
        label = {u: yv[g == u][0] for u in uniq}
        order = []
        for cls in sorted(set(label.values()), key=str):
            members = [u for u in uniq if label[u] == cls]
            order.extend(rng.permutation(members))
    else:
        order = list(rng.permutation(uniq))
    assign = {u: i % k for i, u in enumerate(order)}
    return [np.flatnonzero(np.array([assign[x] == f for x in g])) for f in range(k)]


def assert_disjoint(train: np.ndarray, test: np.ndarray, groups: Sequence[str]) -> None:
    g = np.asarray([str(x) for x in groups])
    shared = set(g[train]) & set(g[test])
    if shared:
        raise ValueError(f"{len(shared)} subjects in both training and test: "
                         f"{sorted(shared)[:5]}")


def _standardise(Xtr, Xte):
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd = np.where(sd > 0, sd, 1.0)
    return (Xtr - mu) / sd, (Xte - mu) / sd


def fit_logistic(X: np.ndarray, y: np.ndarray, *, l2: float = 1.0, iters: int = 50
                 ) -> Callable[[np.ndarray], np.ndarray]:
    """L2-penalised logistic regression by Newton steps; returns a scorer (log-odds)."""
    n, p = X.shape
    A = np.column_stack([np.ones(n), X])
    w = np.zeros(p + 1)
    pen = np.r_[0.0, np.full(p, l2)]
    for _ in range(iters):
        eta = np.clip(A @ w, -30, 30)
        mu = 1 / (1 + np.exp(-eta))
        grad = A.T @ (mu - y) + pen * w
        W = mu * (1 - mu)
        H = (A * W[:, None]).T @ A + np.diag(pen + 1e-9)
        step = np.linalg.solve(H, grad)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return lambda Z: np.column_stack([np.ones(len(Z)), Z]) @ w


def fit_ridge(X: np.ndarray, y: np.ndarray, *, l2: float = 1.0
              ) -> Callable[[np.ndarray], np.ndarray]:
    n, p = X.shape
    mu = y.mean()
    w = np.linalg.solve(X.T @ X + l2 * np.eye(p), X.T @ (y - mu))
    return lambda Z: mu + Z @ w


def top_k_by_t(k: int) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """A selector: the ``k`` features with the largest |Welch t| between classes,
    computed on the training data it is given."""
    def select(X, y):
        a, b = X[y == 1], X[y == 0]
        se = np.sqrt(a.var(0, ddof=1) / len(a) + b.var(0, ddof=1) / len(b))
        t = np.abs(a.mean(0) - b.mean(0)) / np.where(se > 0, se, np.inf)
        return np.argsort(-t)[:k]
    return select


def _metric(task, y, score):
    if task == "binary":
        return auc(y, score)
    ss = float(((y - y.mean()) ** 2).sum())
    return 1 - float(((y - score) ** 2).sum()) / ss if ss > 0 else math.nan


def _cv_scores(X, y, folds, task, select, l2, groups=None):
    score = np.full(len(y), np.nan)
    for test in folds:
        train = np.setdiff1d(np.arange(len(y)), test)
        if groups is not None:
            assert_disjoint(train, test, groups)
        cols = select(X[train], y[train]) if select else np.arange(X.shape[1])
        Xtr, Xte = _standardise(X[train][:, cols], X[test][:, cols])
        if task == "binary":
            if len(set(y[train])) < 2:
                continue
            f = fit_logistic(Xtr, y[train], l2=l2)
        else:
            f = fit_ridge(Xtr, y[train], l2=l2)
        score[test] = f(Xte)
    return score


def grouped_cv(X: np.ndarray, y: Sequence, groups: Sequence[str], *, k: int = 5,
               task: str = "binary", select: Callable | None = None, l2: float = 1.0,
               n_perm: int = 200, seed: int = 0) -> dict:
    """Leave-subjects-out cross-validation with a subject-level permutation null."""
    X = np.asarray(X, float)
    yv = np.asarray(y, float)
    g = np.asarray([str(x) for x in groups])
    constant = all(len(set(yv[g == u])) == 1 for u in np.unique(g))
    folds = grouped_folds(g, k, seed=seed, y=yv if task == "binary" and constant else None)
    score = _cv_scores(X, yv, folds, task, select, l2, groups=g)
    ok = ~np.isnan(score)
    observed = _metric(task, yv[ok], score[ok])
    out = {"metric": "auc" if task == "binary" else "r2", "estimate": observed,
           "k": k, "n_samples": int(len(yv)), "n_subjects": int(len(np.unique(g))),
           "split": "by subject", "selection": "inside each training fold"
           if select else "none"}
    if n_perm and constant:
        rng = np.random.default_rng(seed + 1)
        uniq = np.unique(g)
        lab = {u: yv[g == u][0] for u in uniq}
        null = []
        for _ in range(n_perm):
            perm = dict(zip(uniq, rng.permutation([lab[u] for u in uniq])))
            yp = np.array([perm[x] for x in g])
            sp = _cv_scores(X, yp, grouped_folds(g, k, seed=seed,
                                                 y=yp if task == "binary" else None),
                            task, select, l2)
            okp = ~np.isnan(sp)
            null.append(_metric(task, yp[okp], sp[okp]))
        null = np.array(null)
        out["null_mean"] = float(np.nanmean(null))
        out["p_perm"] = float((1 + np.sum(null >= observed - 1e-12)) / (n_perm + 1))
    elif n_perm:
        out["p_perm"] = None
        out["note"] = ("the outcome varies within subjects; a subject-level permutation "
                       "would need block-wise reassignment and is not computed")
    return out


def sample_split_cv(X: np.ndarray, y: Sequence, *, k: int = 5, task: str = "binary",
                    select: Callable | None = None, l2: float = 1.0, seed: int = 0) -> dict:
    """Random sample-level folds: LEAKY when subjects contribute several samples."""
    yv = np.asarray(y, float)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(yv))
    folds = [idx[i::k] for i in range(k)]
    score = _cv_scores(np.asarray(X, float), yv, folds, task, select, l2)
    ok = ~np.isnan(score)
    return {"metric": "auc" if task == "binary" else "r2",
            "estimate": _metric(task, yv[ok], score[ok]), "split": "by sample (leaky)",
            "warning": "samples of one subject can fall in training and test folds"}

