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

__all__ = ["t_cdf", "t_sf", "t_ppf", "welch_from_summary", "ratio_from_summary", "WelchDifference", "welch_difference", "tost", "bh",
           "OLSFit", "ols", "permutation_p", "signflip_p"]


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


def t_sf(t: float, df: float) -> float:
    """Upper tail P(T > t), computed directly so small tails do not cancel to 0."""
    x = df / (df + t * t)
    tail = 0.5 * _betainc(df / 2.0, 0.5, x)
    return tail if t > 0 else 1.0 - tail


def t_ppf(p: float, df: float) -> float:
    if not 0 < p < 1:
        raise ValueError("p lies in (0, 1)")
    lo, hi = -1.0, 1.0
    while t_cdf(lo, df) > p:
        lo *= 2
    while t_cdf(hi, df) < p:
        hi *= 2
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
    p = 2 * t_sf(abs(est) / se, df)
    return WelchDifference(est, se, df, est - q * se, est + q * se, level, len(xa), len(xb), p)


def tost(diff: WelchDifference, margin: float, *, alpha: float = 0.05) -> dict:
    """Two one-sided tests for equivalence within ``±margin``.

    Equivalent at ``alpha`` exactly when the (1 - 2·alpha) interval lies inside the margin.
    """
    if margin <= 0:
        raise ValueError("an equivalence margin is positive and fixed before the analysis")
    p_low = t_sf((diff.estimate + margin) / diff.se, diff.df)
    p_high = t_cdf((diff.estimate - margin) / diff.se, diff.df)
    q = t_ppf(1 - alpha, diff.df)
    lo, hi = diff.estimate - q * diff.se, diff.estimate + q * diff.se
    return {"margin": margin, "alpha": alpha, "p_value": max(p_low, p_high),
            "interval": [lo, hi], "interval_level": 1 - 2 * alpha,
            "equivalent": bool(-margin < lo and hi < margin)}


def bh(p_values: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p values, in the input order."""
    p = np.asarray(p_values, float)
    if np.isnan(p).any():
        raise ValueError("a p value is missing (NaN); adjust only complete sets")
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
                "level": level, "p_value": 2 * t_sf(abs(est) / se, self.df),
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


def welch_from_summary(mean_a: float, sd_a: float, n_a: int, mean_b: float, sd_b: float,
                       n_b: int, *, level: float = 0.95) -> WelchDifference:
    """``welch_difference`` from published group summaries (mean, SD, n).

    For re-analysis when only summaries are published; it assumes the SDs are standard
    deviations (not standard errors) and the groups are independent animals.
    """
    if min(n_a, n_b) < 2 or min(sd_a, sd_b) < 0:
        raise ValueError("each group needs n >= 2 and a non-negative SD")
    va, vb = sd_a ** 2 / n_a, sd_b ** 2 / n_b
    se = math.sqrt(va + vb)
    if se == 0:
        raise ValueError("both SDs are zero: an interval cannot be estimated")
    df = (va + vb) ** 2 / (va ** 2 / (n_a - 1) + vb ** 2 / (n_b - 1))
    est = mean_a - mean_b
    q = t_ppf(1 - (1 - level) / 2, df)
    return WelchDifference(est, se, df, est - q * se, est + q * se, level, n_a, n_b,
                           2 * t_sf(abs(est) / se, df))


def ratio_from_summary(mean_a: float, sd_a: float, n_a: int, mean_b: float, sd_b: float,
                       n_b: int, *, level: float = 0.90) -> dict:
    """Ratio of means a/b with a delta-method interval on the log scale.

    ``level`` 0.90 is the interval used for two one-sided tests at α = 0.05: the ratio is
    equivalent within (1/m, m) when the 90% interval lies inside it.
    """
    if mean_a <= 0 or mean_b <= 0:
        raise ValueError("a ratio of means needs positive means")
    va = (sd_a / mean_a) ** 2 / n_a
    vb = (sd_b / mean_b) ** 2 / n_b
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (n_a - 1) + vb ** 2 / (n_b - 1))
    q = t_ppf(1 - (1 - level) / 2, df)
    lr = math.log(mean_a / mean_b)
    return {"ratio": mean_a / mean_b, "ci": [math.exp(lr - q * se), math.exp(lr + q * se)],
            "level": level, "df": df, "se_log": se}


def permutation_p(a: Sequence[float], b: Sequence[float], *, n_perm: int = 5000,
                  seed: int = 0, exact_limit: int = 20000) -> dict:
    """Two-sided permutation p for a difference in means between independent units.

    All relabellings are enumerated when there are at most ``exact_limit`` of them, so the
    smallest attainable p is reported exactly; otherwise ``n_perm`` random relabellings
    (p = (hits + 1) / (n_perm + 1)).
    """
    xa, xb = np.asarray(a, float), np.asarray(b, float)
    pooled, na, n = np.r_[xa, xb], len(xa), len(xa) + len(xb)
    obs = abs(xa.mean() - xb.mean())
    total = math.comb(n, na)
    if total <= exact_limit:
        from itertools import combinations
        idx = np.array(list(combinations(range(n), na)))
        mask = np.zeros((len(idx), n), bool)
        mask[np.arange(len(idx))[:, None], idx] = True
        sa = (mask * pooled).sum(1) / na
        sb = (~mask * pooled).sum(1) / (n - na)
        p = float(np.mean(np.abs(sa - sb) >= obs - 1e-12))
        return {"p": p, "exact": True, "min_p": 2 / total if na * 2 == n else 1 / total,
                "relabellings": total}
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        q = rng.permutation(pooled)
        hits += abs(q[:na].mean() - q[na:].mean()) >= obs - 1e-12
    return {"p": (hits + 1) / (n_perm + 1), "exact": False, "min_p": 1 / (n_perm + 1),
            "relabellings": total}


def signflip_p(d: Sequence[float], *, n_perm: int = 5000, seed: int = 0,
               exact_limit: int = 16) -> dict:
    """Two-sided sign-flip p for paired differences; exact for ``n <= exact_limit``."""
    x = np.asarray(d, float)
    n = len(x)
    obs = abs(x.mean())
    if n <= exact_limit:
        signs = 1 - 2 * ((np.arange(2 ** n)[:, None] >> np.arange(n)) & 1)
        p = float(np.mean(np.abs((signs * x).mean(1)) >= obs - 1e-12))
        return {"p": p, "exact": True, "min_p": 2 / 2 ** n, "relabellings": 2 ** n}
    rng = np.random.default_rng(seed)
    signs = rng.choice([-1.0, 1.0], size=(n_perm, n))
    hits = np.sum(np.abs((signs * x).mean(1)) >= obs - 1e-12)
    return {"p": float((hits + 1) / (n_perm + 1)), "exact": False,
            "min_p": 1 / (n_perm + 1), "relabellings": 2 ** n}
