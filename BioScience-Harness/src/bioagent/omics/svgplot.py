"""Small, dependency-free SVG charts for the analysis reports.

Each function returns one self-contained SVG document. Output is deterministic (fixed
precision, no timestamps), so a report's digest changes only when its data does. The
palette is Okabe and Ito's, distinguishable with the common colour-vision deficiencies;
sequential scales use viridis.
"""

from __future__ import annotations

import math
from html import escape
from typing import Mapping, Sequence

import numpy as np

__all__ = ["PALETTE", "scatter", "histogram", "heatmap", "lines", "bars"]

PALETTE = ("#0072B2", "#D55E00", "#009E73", "#E69F00", "#56B4E9", "#CC79A7", "#F0E442",
           "#000000")
GREY = "#A0A0A0"
INK = "#222222"
_VIRIDIS = ((68, 1, 84), (72, 40, 120), (62, 74, 137), (49, 104, 142), (38, 130, 142),
            (31, 158, 137), (53, 183, 121), (109, 205, 89), (180, 222, 44), (253, 231, 37))

W, H = 640, 420
LEFT, RIGHT, TOP, BOTTOM = 70, 20, 40, 55
LEGEND = 130                                  # width kept for a legend, right of the axes


def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if math.isfinite(v) else "0"


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step) * step
    out = []
    v = first
    while v <= hi + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def _label(v: float) -> str:
    if v == 0:
        return "0"
    if abs(v) >= 1e4 or abs(v) < 1e-3:
        return f"{v:.2g}"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.3g}"


class _Canvas:
    def __init__(self, title: str, width: int = W, height: int = H):
        self.w, self.h = width, height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
                      f'height="{height}" viewBox="0 0 {width} {height}" '
                      'font-family="system-ui, -apple-system, Segoe UI, sans-serif" '
                      'font-size="12">',
                      f"<title>{escape(title)}</title>",
                      f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
                      f'<text x="{LEFT}" y="22" font-size="14" font-weight="600" '
                      f'fill="{INK}">{escape(title)}</text>']

    def add(self, text: str) -> None:
        self.parts.append(text)

    def done(self) -> str:
        return "\n".join(self.parts + ["</svg>"]) + "\n"


class _Axes:
    """Data -> pixel mapping with axes, ticks and labels drawn on a canvas."""

    def __init__(self, canvas: _Canvas, xlim: tuple[float, float], ylim: tuple[float, float],
                 xlabel: str, ylabel: str, *, xticks: bool = True, legend: bool = False):
        self.c = canvas
        self.x0, self.x1 = LEFT, canvas.w - RIGHT - (LEGEND if legend else 0)
        self.y0, self.y1 = canvas.h - BOTTOM, TOP
        self.xlim = self._pad(xlim)
        self.ylim = self._pad(ylim)
        c = canvas
        c.add('<g stroke="#dddddd" stroke-width="1">')
        for t in _ticks(*self.ylim):
            y = self.py(t)
            c.add(f'<line x1="{self.x0}" x2="{self.x1}" y1="{_f(y)}" y2="{_f(y)}"/>')
        c.add("</g>")
        c.add(f'<line x1="{self.x0}" x2="{self.x1}" y1="{self.y0}" y2="{self.y0}" '
              f'stroke="{INK}"/>')
        c.add(f'<line x1="{self.x0}" x2="{self.x0}" y1="{self.y0}" y2="{self.y1}" '
              f'stroke="{INK}"/>')
        for t in _ticks(*self.ylim):
            c.add(f'<text x="{self.x0 - 6}" y="{_f(self.py(t) + 4)}" text-anchor="end" '
                  f'fill="{INK}">{_label(t)}</text>')
        if xticks:
            for t in _ticks(*self.xlim):
                x = self.px(t)
                c.add(f'<line x1="{_f(x)}" x2="{_f(x)}" y1="{self.y0}" y2="{self.y0 + 4}" '
                      f'stroke="{INK}"/>')
                c.add(f'<text x="{_f(x)}" y="{self.y0 + 17}" text-anchor="middle" '
                      f'fill="{INK}">{_label(t)}</text>')
        c.add(f'<text x="{(self.x0 + self.x1) / 2}" y="{canvas.h - 14}" '
              f'text-anchor="middle" fill="{INK}">{escape(xlabel)}</text>')
        cy = (self.y0 + self.y1) / 2
        c.add(f'<text x="16" y="{cy}" text-anchor="middle" fill="{INK}" '
              f'transform="rotate(-90 16 {cy})">{escape(ylabel)}</text>')

    @staticmethod
    def _pad(lim: tuple[float, float]) -> tuple[float, float]:
        lo, hi = lim
        if not (math.isfinite(lo) and math.isfinite(hi)):
            return 0.0, 1.0
        if hi <= lo:
            return lo - 1.0, hi + 1.0
        pad = (hi - lo) * 0.04
        return lo - pad, hi + pad

    def px(self, v: float) -> float:
        lo, hi = self.xlim
        return self.x0 + (v - lo) / (hi - lo) * (self.x1 - self.x0)

    def py(self, v: float) -> float:
        lo, hi = self.ylim
        return self.y0 - (v - lo) / (hi - lo) * (self.y0 - self.y1)


def _limits(values: np.ndarray) -> tuple[float, float]:
    v = values[np.isfinite(values)]
    return (float(v.min()), float(v.max())) if len(v) else (0.0, 1.0)


def _legend(c: _Canvas, items: Sequence[tuple[str, str]]) -> None:
    x = c.w - RIGHT - LEGEND + 16
    for i, (label, colour) in enumerate(items):
        y = TOP + 8 + 16 * i
        c.add(f'<circle cx="{x}" cy="{y}" r="4" fill="{colour}"/>')
        c.add(f'<text x="{x + 10}" y="{y + 4}" fill="{INK}">{escape(label)}</text>')


def scatter(x: Sequence[float], y: Sequence[float], *, title: str, xlabel: str, ylabel: str,
            groups: Sequence[str] | None = None, colours: Mapping[str, str] | None = None,
            labels: Sequence[str] | None = None, hlines: Sequence[float] = (),
            vlines: Sequence[float] = (), radius: float = 2.5) -> str:
    """Points, optionally coloured by group and labelled; dashed reference lines."""
    xs, ys = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    order = list(dict.fromkeys(groups)) if groups is not None else ["all"]
    c = _Canvas(title)
    ax = _Axes(c, _limits(xs), _limits(ys), xlabel, ylabel, legend=len(order) > 1)
    palette = dict(colours or {})
    for i, g in enumerate(order):
        palette.setdefault(g, PALETTE[i % len(PALETTE)])
    for v in hlines:
        if ax.ylim[0] <= v <= ax.ylim[1]:
            c.add(f'<line x1="{ax.x0}" x2="{ax.x1}" y1="{_f(ax.py(v))}" y2="{_f(ax.py(v))}" '
                  'stroke="#666666" stroke-dasharray="4 3"/>')
    for v in vlines:
        if ax.xlim[0] <= v <= ax.xlim[1]:
            c.add(f'<line x1="{_f(ax.px(v))}" x2="{_f(ax.px(v))}" y1="{ax.y0}" y2="{ax.y1}" '
                  'stroke="#666666" stroke-dasharray="4 3"/>')
    # draw grey (background) groups first so highlighted points sit on top
    draw = sorted(range(len(xs)), key=lambda i: (palette[groups[i]] != GREY) if groups else 0)
    c.add('<g fill-opacity="0.8">')
    for i in draw:
        if not (math.isfinite(xs[i]) and math.isfinite(ys[i])):
            continue
        colour = palette[groups[i]] if groups is not None else PALETTE[0]
        c.add(f'<circle cx="{_f(ax.px(xs[i]))}" cy="{_f(ax.py(ys[i]))}" r="{radius}" '
              f'fill="{colour}"/>')
    c.add("</g>")
    if labels is not None:
        for i, text in enumerate(labels):
            if text and math.isfinite(xs[i]) and math.isfinite(ys[i]):
                c.add(f'<text x="{_f(ax.px(xs[i]) + 5)}" y="{_f(ax.py(ys[i]) - 5)}" '
                      f'fill="{INK}" font-size="11">{escape(text)}</text>')
    if groups is not None and len(order) > 1:
        _legend(c, [(g, palette[g]) for g in order])
    return c.done()


def histogram(values: Sequence[float], *, title: str, xlabel: str, bins: int = 40,
              lo: float | None = None, hi: float | None = None,
              ylabel: str = "count") -> str:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    lo = float(v.min()) if lo is None and len(v) else (lo if lo is not None else 0.0)
    hi = float(v.max()) if hi is None and len(v) else (hi if hi is not None else 1.0)
    counts, edges = np.histogram(v, bins=bins, range=(lo, hi if hi > lo else lo + 1))
    c = _Canvas(title)
    ax = _Axes(c, (float(edges[0]), float(edges[-1])), (0.0, float(max(counts.max(), 1))),
               xlabel, ylabel)
    c.add(f'<g fill="{PALETTE[0]}">')
    for k, n in enumerate(counts):
        x, x2 = ax.px(edges[k]), ax.px(edges[k + 1])
        y = ax.py(n)
        c.add(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(max(x2 - x - 1, 0.5))}" '
              f'height="{_f(ax.y0 - y)}"/>')
    c.add("</g>")
    return c.done()


def _viridis(t: float) -> str:
    t = min(max(t, 0.0), 1.0) * (len(_VIRIDIS) - 1)
    i = min(int(t), len(_VIRIDIS) - 2)
    f = t - i
    rgb = [round(a + (b - a) * f) for a, b in zip(_VIRIDIS[i], _VIRIDIS[i + 1])]
    return "#" + "".join(f"{v:02x}" for v in rgb)


def heatmap(matrix: np.ndarray, rows: Sequence[str], cols: Sequence[str], *, title: str,
            scale_label: str, reverse: bool = False) -> str:
    """A labelled matrix on the viridis scale (``reverse``: small values bright)."""
    m = np.asarray(matrix, dtype=float)
    n_r, n_c = m.shape
    cell = max(10, min(36, int(360 / max(n_r, n_c, 1))))
    left = 20 + 7 * max((len(r) for r in rows), default=4)
    top = 50
    width = left + cell * n_c + 120
    height = top + cell * n_r + 20 + 7 * max((len(cl) for cl in cols), default=4)
    c = _Canvas(title, width=max(width, 360, LEFT + 9 * len(title)), height=max(height, 200))
    finite = m[np.isfinite(m)]
    lo, hi = (float(finite.min()), float(finite.max())) if len(finite) else (0.0, 1.0)
    span = hi - lo or 1.0
    for i in range(n_r):
        for j in range(n_c):
            t = (m[i, j] - lo) / span if math.isfinite(m[i, j]) else 0.0
            colour = _viridis(1 - t if reverse else t)
            c.add(f'<rect x="{left + j * cell}" y="{top + i * cell}" width="{cell}" '
                  f'height="{cell}" fill="{colour}"><title>{escape(rows[i])} / '
                  f'{escape(cols[j])}: {_label(float(m[i, j]))}</title></rect>')
        c.add(f'<text x="{left - 4}" y="{top + i * cell + cell / 2 + 4}" text-anchor="end" '
              f'fill="{INK}">{escape(rows[i])}</text>')
    for j in range(n_c):
        x = left + j * cell + cell / 2
        y = top + n_r * cell + 8
        c.add(f'<text x="{x}" y="{y}" fill="{INK}" transform="rotate(60 {x} {y})">'
              f'{escape(cols[j])}</text>')
    bx = left + n_c * cell + 20
    for k in range(50):
        t = k / 49
        c.add(f'<rect x="{bx}" y="{_f(top + (1 - t) * 150)}" width="12" height="3.2" '
              f'fill="{_viridis(1 - t if reverse else t)}"/>')
    c.add(f'<text x="{bx + 16}" y="{top + 8}" fill="{INK}">{_label(hi)}</text>')
    c.add(f'<text x="{bx + 16}" y="{top + 153}" fill="{INK}">{_label(lo)}</text>')
    c.add(f'<text x="{bx}" y="{top + 172}" fill="{INK}">{escape(scale_label)}</text>')
    return c.done()


def lines(x: Sequence[float], series: Mapping[str, Sequence[float]], *, title: str,
          xlabel: str, ylabel: str, band: tuple[Sequence[float], Sequence[float]] | None = None,
          hlines: Sequence[float] = (), ylim: tuple[float, float] | None = None) -> str:
    """One line per series, optionally over a shaded band (e.g. the quartiles)."""
    xs = np.asarray(x, dtype=float)
    every = np.concatenate([np.asarray(s, dtype=float) for s in series.values()]
                           + ([np.asarray(band[0]), np.asarray(band[1])] if band else []))
    c = _Canvas(title)
    ax = _Axes(c, _limits(xs), ylim or _limits(every), xlabel, ylabel,
               legend=len(series) > 1)
    if band is not None:
        lo, hi = np.asarray(band[0], dtype=float), np.asarray(band[1], dtype=float)
        pts = [f"{_f(ax.px(a))},{_f(ax.py(b))}" for a, b in zip(xs, hi)]
        pts += [f"{_f(ax.px(a))},{_f(ax.py(b))}" for a, b in zip(xs[::-1], lo[::-1])]
        c.add(f'<polygon points="{" ".join(pts)}" fill="{PALETTE[4]}" fill-opacity="0.35"/>')
    for v in hlines:
        c.add(f'<line x1="{ax.x0}" x2="{ax.x1}" y1="{_f(ax.py(v))}" y2="{_f(ax.py(v))}" '
              'stroke="#666666" stroke-dasharray="4 3"/>')
    for k, (name, ys) in enumerate(series.items()):
        pts = " ".join(f"{_f(ax.px(a))},{_f(ax.py(b))}" for a, b in zip(xs, ys)
                       if math.isfinite(b))
        c.add(f'<polyline points="{pts}" fill="none" stroke="{PALETTE[k % len(PALETTE)]}" '
              'stroke-width="1.8"/>')
    if len(series) > 1:
        _legend(c, [(n, PALETTE[k % len(PALETTE)]) for k, n in enumerate(series)])
    return c.done()


def bars(labels: Sequence[str], values: Sequence[float], *, title: str, ylabel: str,
         colours: Sequence[str] | None = None, hline: float | None = None) -> str:
    v = np.asarray(values, dtype=float)
    c = _Canvas(title)
    n = len(labels)
    ax = _Axes(c, (0.0, float(n)), (0.0, float(max(v.max() if n else 1.0, hline or 0.0))),
               "", ylabel, xticks=False)
    width = (ax.x1 - ax.x0) / max(n, 1)
    for i, (label, value) in enumerate(zip(labels, v)):
        x = ax.x0 + i * width + width * 0.15
        y = ax.py(value)
        colour = colours[i] if colours else PALETTE[0]
        c.add(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(width * 0.7)}" '
              f'height="{_f(ax.y0 - y)}" fill="{colour}"><title>{escape(label)}: '
              f'{_label(float(value))}</title></rect>')
        cx = x + width * 0.35
        c.add(f'<text x="{_f(cx)}" y="{ax.y0 + 14}" text-anchor="middle" fill="{INK}" '
              f'font-size="11">{escape(label)}</text>')
    if hline is not None:
        c.add(f'<line x1="{ax.x0}" x2="{ax.x1}" y1="{_f(ax.py(hline))}" '
              f'y2="{_f(ax.py(hline))}" stroke="#666666" stroke-dasharray="4 3"/>')
    return c.done()
