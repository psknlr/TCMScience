"""Statistics for omics and epidemiology, from the standard library only.

The p-values come from closed forms (the error function for the normal and the 1-df
chi-square, the regularised incomplete beta for Student's t) rather than a numerics
library, so a tool runs in a PSH child with a clean environment. Each is tested against
textbook values.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = ["hypergeometric_test", "fisher_exact", "enrichment_analysis", "benjamini_hochberg",
           "mann_whitney_u", "welch_t_test", "log2_fold_change", "cpm", "tpm",
           "correlation", "diversity", "odds_ratio", "relative_risk", "diagnostic_metrics",
           "roc_auc", "number_needed_to_treat", "regularised_incomplete_beta"]


# ------------------------------------------------------------- special functions

def _ln_choose(n: int, k: int) -> float:
    if k < 0 or k > n:
        return -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-15:
            break
    return h


def regularised_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b), by the continued fraction of Numerical Recipes."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_two_sided(t: float, df: float) -> float:
    return regularised_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))


def _z_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def _numbers(values: Any, what: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{what} must be a non-empty list of numbers")
    try:
        return [float(v) for v in values]
    except (TypeError, ValueError):
        raise ValueError(f"{what} must contain only numbers") from None


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# ------------------------------------------------------------------ enrichment

def hypergeometric_test(overlap: int, query_size: int, set_size: int, background: int
                        ) -> dict[str, Any]:
    """P(X ≥ overlap) drawing ``query_size`` from ``background`` with ``set_size`` marked."""
    for name, v in (("overlap", overlap), ("query_size", query_size), ("set_size", set_size),
                    ("background", background)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if query_size > background or set_size > background:
        raise ValueError("query_size and set_size cannot exceed background")
    if overlap > min(query_size, set_size):
        raise ValueError("overlap cannot exceed the smaller of query_size and set_size")
    denom = _ln_choose(background, query_size)
    p = 0.0
    for i in range(overlap, min(query_size, set_size) + 1):
        p += math.exp(_ln_choose(set_size, i) + _ln_choose(background - set_size, query_size - i)
                      - denom)
    expected = query_size * set_size / background if background else 0.0
    return {"p_value": min(1.0, p), "expected_overlap": round(expected, 4),
            "fold_enrichment": round(overlap / expected, 4) if expected else None,
            "overlap": overlap, "query_size": query_size, "set_size": set_size,
            "background": background}


def fisher_exact(a: int, b: int, c: int, d: int) -> dict[str, Any]:
    """Fisher's exact test on the 2×2 table [[a, b], [c, d]]: two-sided and one-sided."""
    for name, v in (("a", a), ("b", b), ("c", c), ("d", d)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    n = a + b + c + d
    if n == 0:
        raise ValueError("the table is empty")
    row1, col1 = a + b, a + c
    denom = _ln_choose(n, col1)

    def prob(x: int) -> float:
        return math.exp(_ln_choose(row1, x) + _ln_choose(n - row1, col1 - x) - denom)

    observed = prob(a)
    lo, hi = max(0, col1 - (n - row1)), min(row1, col1)
    two = sum(p for p in (prob(x) for x in range(lo, hi + 1)) if p <= observed * (1 + 1e-9))
    greater = sum(prob(x) for x in range(a, hi + 1))
    less = sum(prob(x) for x in range(lo, a + 1))
    odds = (a * d) / (b * c) if b * c else None
    return {"p_value": min(1.0, two), "p_greater": min(1.0, greater), "p_less": min(1.0, less),
            "odds_ratio": round(odds, 4) if odds is not None else None,
            "table": [[a, b], [c, d]]}


def enrichment_analysis(genes: Sequence[str], gene_sets: Mapping[str, Sequence[str]],
                        background: int, min_overlap: int = 1) -> dict[str, Any]:
    """Over-representation analysis of a gene list against named sets, BH-adjusted."""
    query = {str(g).upper() for g in genes if str(g).strip()}
    if not query:
        raise ValueError("genes must be a non-empty list")
    if not isinstance(gene_sets, Mapping) or not gene_sets:
        raise ValueError("gene_sets must be a non-empty mapping of name -> genes")
    if not isinstance(background, int) or background < len(query):
        raise ValueError("background must be an integer no smaller than the query")
    rows = []
    for name, members in gene_sets.items():
        member_set = {str(g).upper() for g in members}
        overlap = sorted(query & member_set)
        if len(overlap) < min_overlap:
            continue
        test = hypergeometric_test(len(overlap), len(query), min(len(member_set), background),
                                   background)
        rows.append({"set": name, "overlap": len(overlap), "set_size": len(member_set),
                     "p_value": test["p_value"], "fold_enrichment": test["fold_enrichment"],
                     "genes": overlap})
    adjusted = benjamini_hochberg([r["p_value"] for r in rows])["q_values"] if rows else []
    for row, q in zip(rows, adjusted):
        row["q_value"] = q
    rows.sort(key=lambda r: (r["p_value"], r["set"]))
    return {"query_size": len(query), "background": background, "results": rows,
            "significant_at_0_05": sum(1 for r in rows if r["q_value"] <= 0.05)}


def benjamini_hochberg(p_values: Sequence[float]) -> dict[str, Any]:
    """Benjamini–Hochberg false-discovery-rate adjustment, order preserved."""
    ps = _numbers(p_values, "p_values")
    if any(p < 0 or p > 1 for p in ps):
        raise ValueError("p-values must lie in [0, 1]")
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, ps[i] * m / rank)
        q[i] = running
    return {"q_values": [round(v, 10) for v in q],
            "bonferroni": [min(1.0, p * m) for p in ps], "m": m}


# --------------------------------------------------------------- two samples

def mann_whitney_u(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Mann–Whitney U with tie-corrected normal approximation (two-sided)."""
    a, b = _numbers(x, "x"), _numbers(y, "y")
    n1, n2 = len(a), len(b)
    ranks = _ranks(a + b)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    n = n1 + n2
    counts: dict[float, int] = {}
    for v in a + b:
        counts[v] = counts.get(v, 0) + 1
    tie_term = sum(t ** 3 - t for t in counts.values())
    sigma = math.sqrt(n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))) if n > 1 else 0.0
    mean_u = n1 * n2 / 2.0
    z = (u - mean_u) / sigma if sigma else 0.0
    return {"u": u, "u1": u1, "u2": u2, "z": round(z, 6),
            "p_value": round(_z_two_sided(z), 10) if sigma else 1.0,
            "n1": n1, "n2": n2, "auc": round(u1 / (n1 * n2), 6)}


def welch_t_test(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Welch's unequal-variance t-test, two-sided, with Welch–Satterthwaite df."""
    a, b = _numbers(x, "x"), _numbers(y, "y")
    if len(a) < 2 or len(b) < 2:
        raise ValueError("each sample needs at least two values")
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((v - ma) ** 2 for v in a) / (len(a) - 1)
    vb = sum((v - mb) ** 2 for v in b) / (len(b) - 1)
    se2 = va / len(a) + vb / len(b)
    if se2 == 0:
        raise ValueError("both samples are constant; the statistic is undefined")
    t = (ma - mb) / math.sqrt(se2)
    df = se2 ** 2 / ((va / len(a)) ** 2 / (len(a) - 1) + (vb / len(b)) ** 2 / (len(b) - 1))
    return {"t": round(t, 6), "df": round(df, 4), "p_value": round(_t_two_sided(t, df), 10),
            "mean_x": round(ma, 6), "mean_y": round(mb, 6), "difference": round(ma - mb, 6)}


# ------------------------------------------------------------- expression maths

def log2_fold_change(treated: Sequence[float], control: Sequence[float],
                     pseudocount: float = 1.0) -> dict[str, Any]:
    """Element-wise log2((treated + pc) / (control + pc))."""
    a, b = _numbers(treated, "treated"), _numbers(control, "control")
    if len(a) != len(b):
        raise ValueError("treated and control must have the same length")
    if pseudocount < 0:
        raise ValueError("pseudocount must be non-negative")
    lfc = [round(math.log2((p + pseudocount) / (q + pseudocount)), 6) for p, q in zip(a, b)]
    return {"log2_fold_change": lfc, "pseudocount": pseudocount,
            "mean": round(sum(lfc) / len(lfc), 6)}


def cpm(counts: Sequence[float], log: bool = False) -> dict[str, Any]:
    """Counts per million; ``log`` gives log2(CPM + 1)."""
    c = _numbers(counts, "counts")
    total = sum(c)
    if total <= 0:
        raise ValueError("counts must sum to a positive number")
    values = [v / total * 1e6 for v in c]
    if log:
        values = [math.log2(v + 1) for v in values]
    return {"values": [round(v, 4) for v in values], "library_size": total, "log": log}


def tpm(counts: Sequence[float], lengths_bp: Sequence[float]) -> dict[str, Any]:
    """Transcripts per million from raw counts and feature lengths."""
    c, lens = _numbers(counts, "counts"), _numbers(lengths_bp, "lengths_bp")
    if len(c) != len(lens):
        raise ValueError("counts and lengths_bp must have the same length")
    if any(v <= 0 for v in lens):
        raise ValueError("lengths must be positive")
    rpk = [v / (ln / 1000.0) for v, ln in zip(c, lens)]
    scale = sum(rpk)
    if scale <= 0:
        raise ValueError("counts must sum to a positive number")
    return {"values": [round(r / scale * 1e6, 4) for r in rpk]}


def correlation(x: Sequence[float], y: Sequence[float]) -> dict[str, Any]:
    """Pearson and Spearman correlation with a t-test p-value for Pearson's r."""
    a, b = _numbers(x, "x"), _numbers(y, "y")
    if len(a) != len(b) or len(a) < 3:
        raise ValueError("x and y need the same length of at least three")

    def pearson(u: Sequence[float], v: Sequence[float]) -> float:
        mu, mv = sum(u) / len(u), sum(v) / len(v)
        num = sum((p - mu) * (q - mv) for p, q in zip(u, v))
        den = math.sqrt(sum((p - mu) ** 2 for p in u) * sum((q - mv) ** 2 for q in v))
        if den == 0:
            raise ValueError("a constant series has no correlation")
        return num / den

    r = pearson(a, b)
    rho = pearson(_ranks(a), _ranks(b))
    n = len(a)
    if abs(r) < 1:
        t = r * math.sqrt((n - 2) / (1 - r * r))
        p = _t_two_sided(t, n - 2)
    else:
        p = 0.0
    return {"pearson_r": round(r, 6), "pearson_p_value": round(p, 10),
            "spearman_rho": round(rho, 6), "n": n}


def diversity(counts: Sequence[float]) -> dict[str, Any]:
    """Shannon (natural log), Simpson's index and Pielou evenness."""
    c = [v for v in _numbers(counts, "counts") if v > 0]
    total = sum(c)
    if total <= 0:
        raise ValueError("counts must contain positive values")
    props = [v / total for v in c]
    shannon = -sum(p * math.log(p) for p in props)
    return {"shannon": round(shannon, 6), "simpson": round(1 - sum(p * p for p in props), 6),
            "richness": len(c), "evenness": round(shannon / math.log(len(c)), 6) if len(c) > 1 else None}


# ------------------------------------------------------------- epidemiology

def odds_ratio(a: int, b: int, c: int, d: int) -> dict[str, Any]:
    """Odds ratio with a 95% CI; Haldane–Anscombe 0.5 correction when a cell is zero."""
    cells = [a, b, c, d]
    if any((not isinstance(v, int)) or v < 0 for v in cells):
        raise ValueError("cells must be non-negative integers")
    corrected = any(v == 0 for v in cells)
    aa, bb, cc, dd = [v + 0.5 for v in cells] if corrected else cells
    orr = (aa * dd) / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    return {"odds_ratio": round(orr, 4), "ci95": [round(math.exp(math.log(orr) - 1.96 * se), 4),
                                                   round(math.exp(math.log(orr) + 1.96 * se), 4)],
            "haldane_corrected": corrected, "table": [[a, b], [c, d]]}


def relative_risk(exposed_events: int, exposed_total: int, unexposed_events: int,
                  unexposed_total: int) -> dict[str, Any]:
    """Relative risk with a 95% CI, absolute risk difference and NNT/NNH."""
    for name, v in (("exposed_events", exposed_events), ("exposed_total", exposed_total),
                    ("unexposed_events", unexposed_events), ("unexposed_total", unexposed_total)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if exposed_events > exposed_total or unexposed_events > unexposed_total:
        raise ValueError("events cannot exceed totals")
    if exposed_total == 0 or unexposed_total == 0 or exposed_events == 0 or unexposed_events == 0:
        raise ValueError("relative risk needs events in both groups")
    r1, r0 = exposed_events / exposed_total, unexposed_events / unexposed_total
    rr = r1 / r0
    se = math.sqrt(1 / exposed_events - 1 / exposed_total + 1 / unexposed_events
                   - 1 / unexposed_total)
    ard = r1 - r0
    return {"relative_risk": round(rr, 4),
            "ci95": [round(math.exp(math.log(rr) - 1.96 * se), 4),
                     round(math.exp(math.log(rr) + 1.96 * se), 4)],
            "risk_exposed": round(r1, 6), "risk_unexposed": round(r0, 6),
            "absolute_risk_difference": round(ard, 6),
            "number_needed": round(1 / abs(ard), 2) if ard else None}


def diagnostic_metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    """Sensitivity, specificity, predictive values, likelihood ratios, accuracy, F1, Youden."""
    for name, v in (("tp", tp), ("fp", fp), ("fn", fn), ("tn", tn)):
        if not isinstance(v, int) or v < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if tp + fn == 0 or fp + tn == 0:
        raise ValueError("both a diseased and a non-diseased group are needed")
    sens, spec = tp / (tp + fn), tn / (fp + tn)
    ppv = tp / (tp + fp) if tp + fp else None
    npv = tn / (tn + fn) if tn + fn else None
    return {"sensitivity": round(sens, 6), "specificity": round(spec, 6),
            "ppv": round(ppv, 6) if ppv is not None else None,
            "npv": round(npv, 6) if npv is not None else None,
            "lr_positive": round(sens / (1 - spec), 4) if spec < 1 else None,
            "lr_negative": round((1 - sens) / spec, 4) if spec > 0 else None,
            "accuracy": round((tp + tn) / (tp + fp + fn + tn), 6),
            "f1": round(2 * tp / (2 * tp + fp + fn), 6) if tp + fp + fn else None,
            "youden_j": round(sens + spec - 1, 6),
            "prevalence": round((tp + fn) / (tp + fp + fn + tn), 6)}


def roc_auc(scores: Sequence[float], labels: Sequence[int]) -> dict[str, Any]:
    """Area under the ROC curve by the rank (Mann–Whitney) identity."""
    s = _numbers(scores, "scores")
    if not isinstance(labels, (list, tuple)) or len(labels) != len(s):
        raise ValueError("labels must be a list the same length as scores")
    lab = [1 if bool(v) else 0 for v in labels]
    n_pos, n_neg = sum(lab), len(lab) - sum(lab)
    if n_pos == 0 or n_neg == 0:
        raise ValueError("both classes must be present")
    ranks = _ranks(s)
    rank_sum = sum(r for r, y in zip(ranks, lab) if y == 1)
    auc = (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return {"auc": round(auc, 6), "n_positive": n_pos, "n_negative": n_neg}


def number_needed_to_treat(control_event_rate: float, experimental_event_rate: float
                           ) -> dict[str, Any]:
    """NNT (benefit) or NNH (harm) from two event rates in [0, 1]."""
    for name, v in (("control_event_rate", control_event_rate),
                    ("experimental_event_rate", experimental_event_rate)):
        if not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(f"{name} must be a rate between 0 and 1")
    arr = control_event_rate - experimental_event_rate
    return {"absolute_risk_reduction": round(arr, 6),
            "relative_risk_reduction": round(arr / control_event_rate, 6) if control_event_rate else None,
            "nnt": round(1 / arr, 2) if arr > 0 else None,
            "nnh": round(-1 / arr, 2) if arr < 0 else None}
