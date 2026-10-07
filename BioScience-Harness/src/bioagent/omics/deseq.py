"""Differential expression with a negative binomial GLM, following DESeq2.

Love, Huber & Anders (2014), *Genome Biology* 15:550. The steps are DESeq2's:

1. **Size factors** by the median of ratios to the per-gene geometric mean (``poscounts``
   when every gene has a zero somewhere).
2. **Gene-wise dispersions** maximising the Cox-Reid adjusted profile likelihood, with
   the means fixed at a GLM fit using a rough moments estimate.
3. **A dispersion trend** ``a0 + a1 / mean`` fitted by a gamma GLM with an identity
   link, refitted without outliers until the coefficients settle; the mean dispersion
   when the parametric fit fails.
4. **Shrinkage** of each dispersion towards the trend: the maximum a posteriori
   estimate under a normal prior on log dispersion whose variance is the excess of
   the residuals' (MAD) variance over the sampling variance ``trigamma((m - p) / 2)``,
   at least 0.25. A gene far above the trend keeps its own estimate.
5. **The GLM fit** by iteratively reweighted least squares, a Wald test of the
   contrast, Cook's distances flagging single-sample outliers (with three or more
   replicates in a group), independent filtering on the mean count and
   Benjamini-Hochberg adjustment.

Two simplifications, each stated in the result: the dispersion line search is a grid
refined by golden-section search rather than DESeq2's backtracking search, and fold
changes are maximum-likelihood estimates (no apeglm/ashr shrinkage). The test suite
compares the output with PyDESeq2, an independent implementation of the same method.

Everything is vectorised over genes with numpy and scipy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import special, stats

__all__ = ["DESeqError", "DesignMatrix", "DESeqResult", "design_matrix", "size_factors",
           "run_deseq", "vst", "benjamini_hochberg", "lowess"]

MIN_DISP = 1e-8
#: DESeq2's ``minmu``: fitted means are floored at half a count, which bounds the fold
#: change of a gene with no counts in one group instead of letting it run to infinity.
MIN_MU = 0.5
LN2 = math.log(2.0)


class DESeqError(ValueError):
    """The counts, the sample table or the design cannot be analysed as given."""


# ============================================================== design matrices

@dataclass(frozen=True)
class DesignMatrix:
    """A model matrix with named columns and the factor levels behind them."""

    matrix: np.ndarray                         # samples x coefficients
    columns: tuple[str, ...]                   # "Intercept", "condition[treated]", "age"
    factors: Mapping[str, tuple[str, ...]]     # factor -> levels, reference first
    formula: str

    def contrast(self, factor: str, numerator: str, denominator: str) -> np.ndarray:
        """The coefficient vector for ``numerator`` vs ``denominator`` of ``factor``."""
        levels = self.factors.get(factor)
        if levels is None:
            raise DESeqError(f"{factor!r} is not a factor of the design {self.formula!r}")
        for level in (numerator, denominator):
            if level not in levels:
                raise DESeqError(f"{level!r} is not a level of {factor!r}: {list(levels)}")
        if numerator == denominator:
            raise DESeqError("a contrast compares two different levels")
        vec = np.zeros(len(self.columns))
        for level, sign in ((numerator, 1.0), (denominator, -1.0)):
            if level != levels[0]:
                vec[self.columns.index(f"{factor}[{level}]")] += sign
        return vec


def design_matrix(samples: Sequence[Mapping[str, Any]], formula: str, *,
                  references: Mapping[str, str] | None = None,
                  covariates: Sequence[str] | None = None) -> DesignMatrix:
    """The model matrix of ``formula`` (``"~ batch + condition"``) over ``samples``.

    Additive terms only. A numeric covariate is centred on its mean; any other term is a
    factor with treatment contrasts, the reference level being ``references[factor]`` or
    the first level in sorted order. ``covariates`` names the numeric terms; when it is
    None, a term whose values all parse as numbers is taken as one (so a batch coded
    1, 2, 3 must then be named in ``references`` or passed with ``covariates=()``).
    """
    text = formula.strip()
    if not text.startswith("~"):
        raise DESeqError(f"a design formula starts with '~': {formula!r}")
    terms = [t.strip() for t in text[1:].split("+") if t.strip()]
    if not terms:
        raise DESeqError(f"the design {formula!r} names no variable")
    for term in terms:
        if any(c in term for c in ":*()^-/"):
            raise DESeqError(f"only additive terms are supported, not {term!r}")
    if len(set(terms)) != len(terms):
        raise DESeqError(f"the design {formula!r} names a variable twice")
    references = dict(references or {})
    n = len(samples)
    cols: list[np.ndarray] = [np.ones(n)]
    names = ["Intercept"]
    factors: dict[str, tuple[str, ...]] = {}
    for term in terms:
        values = []
        for i, sample in enumerate(samples):
            if term not in sample or sample[term] in (None, ""):
                raise DESeqError(f"sample {i + 1} has no value for {term!r}")
            values.append(sample[term])
        if covariates is None:
            numeric = _as_numbers(values) if term not in references else None
        elif term in covariates:
            numeric = _as_numbers(values)
            if numeric is None:
                raise DESeqError(f"covariate {term!r} has a value that is not a number")
        else:
            numeric = None
        if numeric is not None:
            centred = numeric - numeric.mean()
            if not np.any(centred):
                raise DESeqError(f"covariate {term!r} does not vary")
            cols.append(centred)
            names.append(term)
            continue
        labels = [str(v) for v in values]
        levels = sorted(set(labels))
        ref = references.get(term, levels[0])
        if ref not in levels:
            raise DESeqError(f"reference {ref!r} is not a level of {term!r}: {levels}")
        if len(levels) < 2:
            raise DESeqError(f"factor {term!r} has a single level {levels[0]!r}")
        ordered = (ref, *[lv for lv in levels if lv != ref])
        factors[term] = ordered
        for level in ordered[1:]:
            cols.append(np.array([1.0 if lab == level else 0.0 for lab in labels]))
            names.append(f"{term}[{level}]")
    matrix = np.column_stack(cols)
    if np.linalg.matrix_rank(matrix) < matrix.shape[1]:
        raise DESeqError(f"the design {formula!r} is not of full rank over these samples "
                         "(a factor is confounded with another)")
    if matrix.shape[0] <= matrix.shape[1]:
        raise DESeqError(f"{n} samples cannot estimate {matrix.shape[1]} coefficients and "
                         "a dispersion; add replicates")
    return DesignMatrix(matrix=matrix, columns=tuple(names), factors=factors,
                        formula=formula)


def _as_numbers(values: Sequence[Any]) -> np.ndarray | None:
    try:
        out = np.array([float(v) for v in values])
    except (TypeError, ValueError):
        return None
    return out if np.all(np.isfinite(out)) else None


# ============================================================== size factors

def size_factors(counts: np.ndarray) -> tuple[np.ndarray, str]:
    """Median-of-ratios size factors, and the method used ("ratio" or "poscounts")."""
    counts = np.asarray(counts, dtype=float)
    with np.errstate(divide="ignore"):
        logs = np.log(counts)
    log_geo = logs.mean(axis=1)
    usable = np.isfinite(log_geo)
    if usable.any():
        sf = np.exp(np.median(logs[usable] - log_geo[usable, None], axis=0))
        return sf, "ratio"
    # poscounts: the geometric mean over non-zero counts, zero counts ignored
    n = counts.shape[1]
    with np.errstate(divide="ignore"):
        pos = np.where(counts > 0, np.log(np.where(counts > 0, counts, 1.0)), 0.0)
    geo = np.exp(pos.sum(axis=1) / n)
    keep = geo > 0
    ratios = np.where(counts[keep] > 0, counts[keep] / geo[keep, None], np.nan)
    sf = np.nanmedian(ratios, axis=0)
    sf = sf / np.exp(np.mean(np.log(sf)))
    return sf, "poscounts"


# ============================================================== likelihood pieces

def _nb_loglik(y: np.ndarray, mu: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Negative binomial log-likelihood summed over samples; ``alpha`` per gene."""
    r = 1.0 / alpha[:, None]
    return (special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1.0)
            + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))).sum(axis=1)


def _cox_reid(mu: np.ndarray, alpha: np.ndarray, X: np.ndarray) -> np.ndarray:
    """-1/2 log det(X' W X) with W = mu / (1 + alpha mu)."""
    w = mu / (1.0 + alpha[:, None] * mu)
    xtwx = np.einsum("gj,jk,jl->gkl", w, X, X)
    sign, logdet = np.linalg.slogdet(xtwx)
    return -0.5 * np.where(sign > 0, logdet, np.inf)


def _apl(log_alpha: np.ndarray, y: np.ndarray, mu: np.ndarray, X: np.ndarray,
         prior_mean: np.ndarray | None = None, prior_var: float | None = None
         ) -> np.ndarray:
    """Cox-Reid adjusted profile log-likelihood (plus a log-normal prior if given)."""
    alpha = np.exp(log_alpha)
    value = _nb_loglik(y, mu, alpha) + _cox_reid(mu, alpha, X)
    if prior_mean is not None and prior_var is not None:
        value = value - (log_alpha - prior_mean) ** 2 / (2.0 * prior_var)
    return value


def _maximise_log_alpha(y: np.ndarray, mu: np.ndarray, X: np.ndarray, lo: float,
                        hi: float, *, prior_mean: np.ndarray | None = None,
                        prior_var: float | None = None, grid: int = 60,
                        iterations: int = 60) -> np.ndarray:
    """Per-gene argmax of the APL over log alpha in [lo, hi]: grid, then golden section."""
    points = np.linspace(lo, hi, grid)
    values = np.empty((y.shape[0], grid))
    for k, la in enumerate(points):
        values[:, k] = _apl(np.full(y.shape[0], la), y, mu, X, prior_mean, prior_var)
    best = np.nanargmax(np.where(np.isfinite(values), values, -np.inf), axis=1)
    step = points[1] - points[0]
    a = np.clip(points[best] - step, lo, hi)
    b = np.clip(points[best] + step, lo, hi)
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - ratio * (b - a)
    d = a + ratio * (b - a)
    fc = _apl(c, y, mu, X, prior_mean, prior_var)
    fd = _apl(d, y, mu, X, prior_mean, prior_var)
    for _ in range(iterations):
        left = fc > fd                          # the maximum lies in [a, d]
        b = np.where(left, d, b)
        a = np.where(left, a, c)
        new_c = b - ratio * (b - a)
        new_d = a + ratio * (b - a)
        c_next = np.where(left, new_c, d)
        d_next = np.where(left, c, new_d)
        fc_next = np.where(left, _apl(new_c, y, mu, X, prior_mean, prior_var), fd)
        fd_next = np.where(left, fc, _apl(new_d, y, mu, X, prior_mean, prior_var))
        c, d, fc, fd = c_next, d_next, fc_next, fd_next
    return (a + b) / 2.0


# ============================================================== GLM fitting

def _fit_glm(y: np.ndarray, sf: np.ndarray, X: np.ndarray, alpha: np.ndarray, *,
             max_iter: int = 100, tol: float = 1e-8, ridge: float = 1e-6
             ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """IRLS for a NB GLM with log link and offset log(size factor), vectorised over genes.

    Returns (beta, mu, covariance of beta, converged). ``ridge`` is a tiny penalty on
    the non-intercept coefficients, as DESeq2 applies without a beta prior; fitted means
    are floored at :data:`MIN_MU` as in DESeq2's ``fitBeta``.
    """
    g, n = y.shape
    p = X.shape[1]
    norm = y / sf
    beta = np.linalg.lstsq(X, np.log(norm + 0.1).T, rcond=None)[0].T          # g x p
    penalty = np.diag([0.0] + [ridge] * (p - 1))
    deviance = np.full(g, np.inf)
    converged = np.zeros(g, dtype=bool)
    for _ in range(max_iter):
        mu = np.maximum(sf * np.exp(np.clip(beta @ X.T, -30.0, 30.0)), MIN_MU)
        w = mu / (1.0 + alpha[:, None] * mu)
        z = np.log(mu / sf) + (y - mu) / mu
        xtwx = np.einsum("gj,jk,jl->gkl", w, X, X) + penalty
        xtwz = np.einsum("gj,jk,gj->gk", w, X, z)
        new_beta = np.linalg.solve(xtwx, xtwz[..., None])[..., 0]
        new_beta = np.where(converged[:, None], beta, new_beta)
        mu = np.maximum(sf * np.exp(np.clip(new_beta @ X.T, -30.0, 30.0)), MIN_MU)
        new_dev = -2.0 * _nb_loglik(y, mu, alpha)
        change = np.abs(new_dev - deviance) / (np.abs(new_dev) + 0.1)
        converged |= change < tol
        beta, deviance = new_beta, new_dev
        if converged.all():
            break
    mu = np.maximum(sf * np.exp(np.clip(beta @ X.T, -30.0, 30.0)), MIN_MU)
    w = mu / (1.0 + alpha[:, None] * mu)
    xtwx = np.einsum("gj,jk,jl->gkl", w, X, X)
    inv = np.linalg.inv(xtwx + penalty)
    cov = inv @ xtwx @ inv
    return beta, mu, cov, converged


# ============================================================== dispersion trend

def _gamma_identity_fit(means: np.ndarray, disps: np.ndarray, start: np.ndarray
                        ) -> tuple[np.ndarray, bool]:
    """Gamma GLM, identity link: disps ~ a0 + a1 / means. IRLS on weights 1/fit^2."""
    A = np.column_stack([np.ones_like(means), 1.0 / means])
    coef = start.astype(float)
    for _ in range(100):
        fitted = A @ coef
        if np.any(fitted <= 0):
            return coef, False
        w = 1.0 / fitted ** 2
        new = np.linalg.solve(A.T @ (A * w[:, None]), A.T @ (w * disps))
        if np.max(np.abs(new - coef) / (np.abs(coef) + 1e-12)) < 1e-10:
            return new, True
        coef = new
    return coef, False


def _parametric_trend(means: np.ndarray, disps: np.ndarray) -> np.ndarray | None:
    """DESeq2's parametricDispersionFit: (a0, a1) or None when it fails."""
    coefs = np.array([0.1, 1.0])
    for _ in range(11):
        residuals = disps / (coefs[0] + coefs[1] / means)
        good = (residuals > 1e-4) & (residuals < 15)
        if good.sum() < 3:
            return None
        new, ok = _gamma_identity_fit(means[good], disps[good], coefs)
        if not np.all(new > 0):
            return None
        done = np.sum(np.log(new / coefs) ** 2) < 1e-6 and ok
        coefs = new
        if done:
            return coefs
    return None


# ============================================================== multiple testing

def benjamini_hochberg(p: np.ndarray) -> np.ndarray:
    """BH adjustment; NaN p-values stay NaN and do not count towards m."""
    p = np.asarray(p, dtype=float)
    out = np.full(p.shape, np.nan)
    ok = ~np.isnan(p)
    m = int(ok.sum())
    if m == 0:
        return out
    vals = p[ok]
    order = np.argsort(vals, kind="mergesort")
    ranked = vals[order] * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1]
    res = np.empty(m)
    res[order] = np.minimum(adjusted, 1.0)
    out[ok] = res
    return out


def lowess(x: np.ndarray, y: np.ndarray, *, frac: float = 2.0 / 3.0,
           iterations: int = 3) -> np.ndarray:
    """Cleveland's locally weighted linear regression with robustness iterations."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    r = max(int(math.ceil(frac * n)), 2)
    fitted = np.zeros(n)
    robust = np.ones(n)
    for _ in range(iterations + 1):
        for i in range(n):
            dist = np.abs(x - x[i])
            h = np.sort(dist)[min(r, n) - 1]
            h = h if h > 0 else 1.0
            w = np.clip(dist / h, 0.0, 1.0)
            w = (1.0 - w ** 3) ** 3 * robust
            sw = w.sum()
            if sw <= 0:
                fitted[i] = y[i]
                continue
            xm = (w * x).sum() / sw
            ym = (w * y).sum() / sw
            sxx = (w * (x - xm) ** 2).sum()
            slope = ((w * (x - xm) * (y - ym)).sum() / sxx) if sxx > 0 else 0.0
            fitted[i] = ym + slope * (x[i] - xm)
        residuals = y - fitted
        s = np.median(np.abs(residuals))
        if s == 0:
            break
        u = np.clip(residuals / (6.0 * s), -1.0, 1.0)
        robust = (1.0 - u ** 2) ** 2
    return fitted


def _independent_filter(base_mean: np.ndarray, p: np.ndarray, alpha: float
                        ) -> tuple[np.ndarray, float]:
    """DESeq2's independent filtering on the mean count: (adjusted p, threshold)."""
    testable = ~np.isnan(p)
    if testable.sum() == 0:
        return benjamini_hochberg(p), 0.0
    lower = float(np.mean(base_mean == 0))
    thetas = np.linspace(lower, 0.95, 50)
    cutoffs = np.quantile(base_mean, thetas)
    rejections = np.zeros(len(thetas))
    adjusted = []
    for k, cut in enumerate(cutoffs):
        keep = base_mean > cut if k else base_mean >= cut
        q = np.full(p.shape, np.nan)
        q[keep] = benjamini_hochberg(np.where(keep, p, np.nan))[keep]
        adjusted.append(q)
        rejections[k] = np.nansum(q < alpha)
    if rejections.max() <= 10:
        return adjusted[0], float(cutoffs[0])
    fit = lowess(thetas, rejections, frac=0.2)
    residual_sd = math.sqrt(float(np.mean((rejections - fit) ** 2)))
    threshold = fit.max() - residual_sd
    k = int(np.argmax(rejections > threshold))
    return adjusted[k], float(cutoffs[k])


# ============================================================== Cook's distances

def _trimmed_mean(values: np.ndarray, trim: float) -> np.ndarray:
    return stats.trim_mean(values, trim, axis=1)


def _robust_dispersion(norm: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """DESeq2's robustMethodOfMomentsDisp: trimmed within-cell variance."""
    trims = (1 / 3, 1 / 4, 1 / 8)
    scales = (2.04, 1.86, 1.51)

    def bin_of(n: int) -> int:
        return 0 if n <= 3.5 else (1 if n <= 23.5 else 2)

    labels, counts = np.unique(cells, return_counts=True)
    big = labels[counts >= 3]
    if len(big):
        idx = np.isin(cells, big)
        sub, sub_cells = norm[:, idx], cells[idx]
        var_est = []
        for lab in np.unique(sub_cells):
            members = sub[:, sub_cells == lab]
            k = bin_of(members.shape[1])
            centre = _trimmed_mean(members, trims[k])
            sq = (members - centre[:, None]) ** 2
            var_est.append(scales[k] * _trimmed_mean(sq, trims[k]))
        v = np.max(np.column_stack(var_est), axis=1)
    else:
        k = bin_of(norm.shape[1])
        centre = _trimmed_mean(norm, trims[k])
        v = scales[k] * _trimmed_mean((norm - centre[:, None]) ** 2, trims[k])
    m = norm.mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        alpha = (v - m) / m ** 2
    return np.maximum(np.nan_to_num(alpha, nan=0.04), 0.04)


# ============================================================== variance stabilising

def vst(norm_counts: np.ndarray, trend: Mapping[str, Any]) -> np.ndarray:
    """DESeq2's variance stabilising transformation for the fitted dispersion trend."""
    q = np.asarray(norm_counts, dtype=float)
    if trend.get("kind") == "parametric":
        a0, a1 = trend["asymptotic"], trend["extra_poisson"]
        return np.log((1 + a1 + 2 * a0 * q + 2 * np.sqrt(a0 * q * (1 + a1 + a0 * q)))
                      / (4 * a0)) / LN2
    alpha = trend["mean"]
    return (2 * np.arcsinh(np.sqrt(alpha * q)) - math.log(alpha) - math.log(4)) / LN2


# ============================================================== the analysis

@dataclass
class DESeqResult:
    """Per-gene results and the fitted quantities behind them."""

    genes: tuple[str, ...]
    samples: tuple[str, ...]
    contrast: tuple[str, str, str]
    design: DesignMatrix
    size_factors: np.ndarray
    size_factor_method: str
    base_mean: np.ndarray
    log2_fold_change: np.ndarray
    lfc_se: np.ndarray
    stat: np.ndarray
    p_value: np.ndarray
    p_adjusted: np.ndarray
    dispersion_gene: np.ndarray
    dispersion_trend: np.ndarray
    dispersion: np.ndarray
    dispersion_outlier: np.ndarray
    trend: dict[str, Any]
    cooks_outlier: np.ndarray
    filter_threshold: float
    converged: np.ndarray
    normalized: np.ndarray
    alpha: float = 0.05
    notes: list[str] = field(default_factory=list)

    def table(self) -> list[dict[str, Any]]:
        """One row per gene, in input order; NaN becomes None."""
        def num(v: float) -> float | None:
            return None if v is None or not np.isfinite(v) else float(v)
        return [{"gene": g, "base_mean": num(self.base_mean[i]),
                 "log2_fold_change": num(self.log2_fold_change[i]),
                 "lfc_se": num(self.lfc_se[i]), "stat": num(self.stat[i]),
                 "p_value": num(self.p_value[i]), "p_adjusted": num(self.p_adjusted[i]),
                 "dispersion": num(self.dispersion[i]),
                 "cooks_outlier": bool(self.cooks_outlier[i])}
                for i, g in enumerate(self.genes)]

    def significant(self, alpha: float | None = None) -> list[int]:
        level = self.alpha if alpha is None else alpha
        return [i for i, q in enumerate(self.p_adjusted) if np.isfinite(q) and q < level]

    def summary(self) -> dict[str, Any]:
        sig = self.significant()
        up = sum(1 for i in sig if self.log2_fold_change[i] > 0)
        return {"genes": len(self.genes), "tested": int(np.sum(np.isfinite(self.p_value))),
                "alpha": self.alpha, "significant": len(sig), "up": up,
                "down": len(sig) - up,
                "filtered_low_count": int(np.sum(np.isfinite(self.p_value)
                                                 & np.isnan(self.p_adjusted))),
                "cooks_outliers": int(self.cooks_outlier.sum()),
                "all_zero": int(np.sum(self.base_mean == 0)),
                "filter_threshold": self.filter_threshold,
                "size_factors": dict(zip(self.samples, map(float, self.size_factors))),
                "size_factor_method": self.size_factor_method,
                "dispersion_trend": self.trend,
                "not_converged": int(np.sum(~self.converged)),
                "design": self.design.formula,
                "contrast": list(self.contrast), "notes": list(self.notes)}


def run_deseq(counts: np.ndarray, genes: Sequence[str], samples: Sequence[Mapping[str, Any]],
              *, design: str, contrast: tuple[str, str, str], alpha: float = 0.05,
              sample_names: Sequence[str] | None = None, cooks_cutoff: bool = True,
              independent_filtering: bool = True,
              covariates: Sequence[str] | None = None) -> DESeqResult:
    """Test ``contrast = (factor, numerator, denominator)`` gene by gene.

    ``counts`` is genes x samples of non-negative integers; ``samples`` holds one
    mapping of design variables per column.
    """
    y_all = np.asarray(counts)
    if y_all.ndim != 2:
        raise DESeqError("counts must be a genes x samples matrix")
    if y_all.shape[0] != len(genes) or y_all.shape[1] != len(samples):
        raise DESeqError(f"counts are {y_all.shape}, but there are {len(genes)} genes and "
                         f"{len(samples)} samples")
    if not np.all(np.isfinite(y_all)) or np.any(y_all < 0):
        raise DESeqError("counts must be finite and non-negative")
    if np.any(np.abs(y_all - np.round(y_all)) > 1e-8):
        raise DESeqError("counts must be integers; round estimated counts first")
    if len(set(genes)) != len(genes):
        raise DESeqError("gene identifiers must be unique")
    y_all = np.round(y_all).astype(float)
    factor, numerator, denominator = contrast
    dm = design_matrix(samples, design, references={factor: denominator},
                       covariates=covariates)
    X = dm.matrix
    m, p = X.shape
    names = tuple(sample_names) if sample_names else tuple(f"sample{i + 1}" for i in range(m))
    if np.any(y_all.sum(axis=0) == 0):
        raise DESeqError("a sample has no counts at all")
    contrast_vec = dm.contrast(factor, numerator, denominator)
    notes = ["fold changes are maximum-likelihood estimates (no apeglm/ashr shrinkage)",
             "dispersions maximise the Cox-Reid adjusted profile likelihood by grid and "
             "golden-section search"]

    sf, sf_method = size_factors(y_all)
    norm_all = y_all / sf
    base_mean = norm_all.mean(axis=1)
    nonzero = base_mean > 0
    y = y_all[nonzero]
    norm = norm_all[nonzero]
    g = y.shape[0]
    if g == 0:
        raise DESeqError("every gene has zero counts")
    max_disp = max(10.0, float(m))

    # rough dispersion: minimum of the moments and the linear-model estimates
    base_var = norm.var(axis=1, ddof=1)
    xim = float(np.mean(1.0 / sf))
    with np.errstate(divide="ignore", invalid="ignore"):
        moments = (base_var - xim * norm.mean(axis=1)) / norm.mean(axis=1) ** 2
    hat = X @ np.linalg.lstsq(X, norm.T, rcond=None)[0]
    lin_mu = np.maximum(hat.T, 1.0)
    rough = np.maximum(np.sum(((norm - lin_mu) ** 2 - lin_mu) / lin_mu ** 2, axis=1)
                       / (m - p), 0.0)
    alpha_init = np.clip(np.nan_to_num(np.minimum(rough, moments), nan=MIN_DISP),
                         MIN_DISP, max_disp)

    # gene-wise dispersions, the means held fixed. When the design has one coefficient
    # per distinct group the means are the group means (DESeq2's linearModelMu);
    # otherwise a GLM fit with the rough dispersions.
    if len(np.unique(X, axis=0)) == p:
        coef = np.linalg.lstsq(X, norm.T, rcond=None)[0]
        mu_init = np.maximum(sf * (X @ coef).T, MIN_MU)
    else:
        _, mu_init, _, _ = _fit_glm(y, sf, X, alpha_init)
    lo, hi = math.log(MIN_DISP / 10.0), math.log(max_disp)
    log_gene = _maximise_log_alpha(y, mu_init, X, lo, hi)
    disp_gene = np.clip(np.exp(log_gene), MIN_DISP, max_disp)

    # the trend over genes whose estimate is above the floor
    fit_mask = disp_gene >= 100 * MIN_DISP
    base_nz = base_mean[nonzero]
    coefs = _parametric_trend(base_nz[fit_mask], disp_gene[fit_mask]) \
        if fit_mask.sum() >= 3 else None
    if coefs is not None:
        trend_vals = coefs[0] + coefs[1] / base_nz
        trend = {"kind": "parametric", "asymptotic": float(coefs[0]),
                 "extra_poisson": float(coefs[1])}
    else:
        use = disp_gene[fit_mask] if fit_mask.any() else disp_gene
        mean_disp = float(stats.trim_mean(use, 0.001))
        trend_vals = np.full(g, mean_disp)
        trend = {"kind": "mean", "mean": mean_disp}
        notes.append("the parametric dispersion trend did not fit; the mean dispersion "
                     "is the trend")

    # prior variance and MAP dispersions
    residuals = np.log(disp_gene[fit_mask]) - np.log(trend_vals[fit_mask])
    if len(residuals) >= 2:
        mad = stats.median_abs_deviation(residuals, scale="normal")
        var_log = float(mad ** 2)
    else:
        var_log = 0.25
    expected = float(special.polygamma(1, (m - p) / 2.0)) if m > p else 0.0
    prior_var = max(var_log - expected, 0.25)
    log_map = _maximise_log_alpha(y, mu_init, X, lo, hi, prior_mean=np.log(trend_vals),
                                  prior_var=prior_var)
    disp_map = np.clip(np.exp(log_map), MIN_DISP, max_disp)
    outlier = np.log(disp_gene) > np.log(trend_vals) + 2.0 * math.sqrt(var_log)
    disp_final = np.where(outlier, disp_gene, disp_map)

    # final fit and Wald test
    beta, mu, cov, converged = _fit_glm(y, sf, X, disp_final)
    lfc = (beta @ contrast_vec) / LN2
    se = np.sqrt(np.einsum("k,gkl,l->g", contrast_vec, cov, contrast_vec)) / LN2
    with np.errstate(divide="ignore", invalid="ignore"):
        wald = lfc / se
    pvals = 2.0 * special.ndtr(-np.abs(wald))

    # Cook's distances, in groups with three or more replicates
    cells = np.array(["".join(f"{v:g}," for v in row) for row in X])
    _, cell_sizes = np.unique(cells, return_counts=True)
    cook_flag = np.zeros(g, dtype=bool)
    if cooks_cutoff and m > p and (cell_sizes >= 3).any():
        robust = _robust_dispersion(norm, cells)
        w = mu / (1.0 + disp_final[:, None] * mu)
        xtwx_inv = np.linalg.inv(np.einsum("gj,jk,jl->gkl", w, X, X))
        h = w * np.einsum("jk,gkl,jl->gj", X, xtwx_inv, X)
        pearson_sq = (y - mu) ** 2 / (mu + robust[:, None] * mu ** 2)
        with np.errstate(divide="ignore", invalid="ignore"):
            cooks = pearson_sq / p * h / (1.0 - h) ** 2
        counted = np.isin(cells, np.unique(cells)[cell_sizes >= 3])
        cutoff = float(stats.f.ppf(0.99, p, m - p))
        cook_flag = np.nanmax(np.where(counted, cooks, 0.0), axis=1) > cutoff
        pvals = np.where(cook_flag, np.nan, pvals)

    # scatter back into the full gene list
    full = len(genes)

    def spread(values: np.ndarray, fill: float = np.nan) -> np.ndarray:
        out = np.full(full, fill, dtype=float)
        out[nonzero] = values
        return out

    p_full = spread(pvals)
    if independent_filtering:
        padj, threshold = _independent_filter(base_mean, p_full, alpha)
    else:
        padj, threshold = benjamini_hochberg(p_full), 0.0
    flags = np.zeros(full, dtype=bool)
    flags[nonzero] = cook_flag
    conv = np.ones(full, dtype=bool)
    conv[nonzero] = converged
    out_flag = np.zeros(full, dtype=bool)
    out_flag[nonzero] = outlier
    return DESeqResult(
        genes=tuple(genes), samples=names, contrast=(factor, numerator, denominator),
        design=dm, size_factors=sf, size_factor_method=sf_method, base_mean=base_mean,
        log2_fold_change=spread(lfc), lfc_se=spread(se), stat=spread(wald),
        p_value=p_full, p_adjusted=padj, dispersion_gene=spread(disp_gene),
        dispersion_trend=spread(trend_vals), dispersion=spread(disp_final),
        dispersion_outlier=out_flag, trend=trend, cooks_outlier=flags,
        filter_threshold=threshold, converged=conv, normalized=norm_all, alpha=alpha,
        notes=notes)
