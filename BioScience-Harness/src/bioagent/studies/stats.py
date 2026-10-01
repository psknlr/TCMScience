"""The few statistics the study analyses need, written out so their assumptions are visible.

Only the standard library and numpy. The t distribution is computed from the regularised
incomplete beta function (continued fraction, as in Numerical Recipes), its quantile by
bisection; both are checked against tabulated values in the tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

__all__ = ["t_cdf", "t_ppf", "WelchDifference", "welch_difference", "tost", "bh",
           "OLSFit", "ols"]


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1.0 + aa / c
        c = c if abs(c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2.0, 0.5, x)
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    if not 0 < p < 1:
        raise ValueError("p lies in (0, 1)")
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass(frozen=True)
class WelchDifference:
    """Mean of ``a`` minus mean of ``b``, with a Welch interval."""

    estimate: float
    se: float
    df: float
    ci_low: float
    ci_high: float
    level: float
    n_a: int
    n_b: int
    p_value: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def welch_difference(a: Sequence[float], b: Sequence[float], *,
                     level: float = 0.95) -> WelchDifference:
    xa, xb = np.asarray(a, float), np.asarray(b, float)
    if len(xa) < 2 or len(xb) < 2:
        raise ValueError("each group needs at least two independent replicates")
    va, vb = xa.var(ddof=1) / len(xa), xb.var(ddof=1) / len(xb)
    se = math.sqrt(va + vb)
    est = float(xa.mean() - xb.mean())
    if se == 0:
        raise ValueError("no variation in either group: an interval cannot be estimated")
    df = (va + vb) ** 2 / (va ** 2 / (len(xa) - 1) + vb ** 2 / (len(xb) - 1))
    q = t_ppf(1 - (1 - level) / 2, df)
    p = 2 * (1 - t_cdf(abs(est) / se, df))
    return WelchDifference(est, se, df, est - q * se, est + q * se, level, len(xa), len(xb), p)


def tost(diff: WelchDifference, margin: float, *, alpha: float = 0.05) -> dict:
    """Two one-sided tests for equivalence within ``±margin``.

    Equivalent at ``alpha`` exactly when the (1 - 2·alpha) interval lies inside the margin.
    """
    if margin <= 0:
        raise ValueError("an equivalence margin is positive and fixed before the analysis")
    p_low = 1 - t_cdf((diff.estimate + margin) / diff.se, diff.df)
    p_high = t_cdf((diff.estimate - margin) / diff.se, diff.df)
    q = t_ppf(1 - alpha, diff.df)
    lo, hi = diff.estimate - q * diff.se, diff.estimate + q * diff.se
    return {"margin": margin, "alpha": alpha, "p_value": max(p_low, p_high),
            "interval": [lo, hi], "interval_level": 1 - 2 * alpha,
            "equivalent": bool(-margin < lo and hi < margin)}


def bh(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p values, in the input order."""
    p = np.asarray(p_values, float)
    n = len(p)
    if n == 0:
        return []
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(adjusted, 1.0)
    return out.tolist()


@dataclass(frozen=True)
class OLSFit:
    names: tuple[str, ...]
    coef: tuple[float, ...]
    se: tuple[float, ...]
    df: int
    n: int

    def term(self, name: str, *, level: float = 0.95) -> dict:
        i = self.names.index(name)
        q = t_ppf(1 - (1 - level) / 2, self.df)
        est, se = self.coef[i], self.se[i]
        return {"term": name, "estimate": est, "se": se, "ci": [est - q * se, est + q * se],
                "level": level, "p_value": 2 * (1 - t_cdf(abs(est) / se, self.df)),
                "df": self.df}


def ols(y: Sequence[float], columns: dict[str, Sequence[float]]) -> OLSFit:
    """Least squares with an intercept and classical standard errors."""
    names = ("intercept", *columns)
    X = np.column_stack([np.ones(len(y)), *[np.asarray(v, float) for v in columns.values()]])
    yv = np.asarray(y, float)
    n, k = X.shape
    if n <= k:
        raise ValueError(f"{n} observations cannot estimate {k} coefficients")
    if np.linalg.matrix_rank(X) < k:
        raise ValueError("the design is rank deficient: a term is confounded with the others")
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    sigma2 = float(resid @ resid) / (n - k)
    cov = sigma2 * np.linalg.inv(X.T @ X)
    return OLSFit(names, tuple(map(float, beta)), tuple(map(float, np.sqrt(np.diag(cov)))),
                  n - k, n)
