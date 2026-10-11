"""House style for the IEEE Transactions-style figures: flowcharts and plots, in colour.

IEEE sizes figures to its columns: 3.5 in (21 picas) for one column and 7.16 in (43 picas)
for two. It prefers vector files (PDF, EPS) with fonts embedded, raster line art at
600 dpi or more, and type that matches the paper. Accordingly:

* the face is Times New Roman where installed and Liberation Serif otherwise, which is
  metrically identical to it, so the figures set like the IEEEtran text around them;
* every text is 8 pt or larger at final size (block names 8 pt bold, region names 9 pt
  bold) and every stroke at least 0.5 pt, so nothing thins out in print;
* colour is Okabe-Ito (Wong, Nat. Methods 2011), the palette of the Nature figures, with
  one meaning per hue across the set: blue, the trusted kernel and what it releases;
  green, the capability plane; orange, governance; reddish purple, the scientist plane;
  vermillion, refusals and errors; grey, the untrusted model. The six hues pass a
  colour-vision check against white (worst adjacent pair Delta E 9.6 under deuteranopia,
  20.0 in normal vision); orange, purple and sky blue fall below 3:1 contrast, so they
  only fill shapes that also carry a label, and strokes use darkened shades;
* colour is never the only cue, because IEEE prints many pages in greyscale: regions also
  differ in line style (solid, execution; dashed, untrusted; dash-dot, constraint) and
  weight, plotted series in marker and line style, and magnitudes use one hue that runs
  light to dark;
* each figure is written as PDF and EPS (vector, TrueType embedded) and as a 600 dpi PNG.

The checks run as each figure is saved, so a label added later cannot slip below them.
"""

from __future__ import annotations

import os
import subprocess
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Patch  # noqa: E402

import figlib as fl  # noqa: E402
import nature_style as ns  # noqa: E402

OUT = ns.MANUSCRIPT / "ieee"
MM = 1 / 25.4
ONE_COLUMN = 3.5                    # inches, 21 picas
TWO_COLUMN = 7.16                   # inches, 43 picas
MAX_DEPTH_IN = 8.5                  # leaves room for the caption on a 9.5 in text block

SERIF = ["Times New Roman", "Liberation Serif"]
ALLOWED_FONTS = ("TimesNewRoman", "LiberationSerif")

TEXT = 8.0           # every text, the floor
NAME = 8.0           # block names, bold
REGION = 9.0         # region names, bold
MIN_TEXT_PT = 8.0
MAX_TEXT_PT = 10.0
MIN_LINE_PT = 0.5

BLACK = "#000000"
DARK = "#333333"
GREY = "#6e6e6e"
LIGHT = "#bdbdbd"
GRID = "#cfcfcf"
FILL = "#f2f2f2"     # the untrusted model's box
FILL2 = "#e4e4e4"

# Okabe-Ito, one meaning per hue (see the module docstring)
BLUE = "#0072B2"
VERMILLION = "#D55E00"
GREEN = "#009E73"
ORANGE = "#E69F00"
PURPLE = "#CC79A7"
SKY = "#56B4E9"


def shade(colour: str, amount: float) -> tuple[float, float, float]:
    """``colour`` mixed with black: 0 leaves it, 1 is black (for strokes and text)."""
    r, g, b = ns.to_rgb(colour)
    return (r * (1 - amount), g * (1 - amount), b * (1 - amount))


tint = ns.tint

#: Semantic roles: (stroke, region fill, block fill).
ROLE = {
    "kernel": (BLUE, tint(BLUE, 0.08), tint(BLUE, 0.16)),
    "capability": (shade(GREEN, 0.15), tint(GREEN, 0.07), tint(GREEN, 0.15)),
    "governance": (shade(ORANGE, 0.28), tint(ORANGE, 0.08), tint(ORANGE, 0.18)),
    "scientist": (shade(PURPLE, 0.30), tint(PURPLE, 0.10), tint(PURPLE, 0.20)),
    "refusal": (VERMILLION, tint(VERMILLION, 0.08), tint(VERMILLION, 0.16)),
    "model": (GREY, FILL, FILL),
    "neutral": (BLACK, "white", "white"),
}

LINE = TEXT * 1.15 * 25.4 / 72      # one line of 8 pt text at linespacing 1.15, in mm


def _available(families):
    names = {f.name for f in font_manager.fontManager.ttflist}
    return [f for f in families if f in names]


def apply() -> None:
    faces = _available(SERIF)
    if not faces:
        raise RuntimeError("neither Times New Roman nor Liberation Serif is installed; "
                           "install fonts-liberation to build the IEEE figures")
    plt.rcdefaults()                    # nothing carried over from another style
    # Text metrics depend on hinting; state it rather than inherit a system matplotlibrc.
    plt.rcParams["text.hinting"] = "no_hinting"
    plt.rcParams.update({
        "font.family": faces[:1],
        "font.size": TEXT,
        "mathtext.fontset": "custom",
        "mathtext.rm": faces[0],
        "mathtext.it": f"{faces[0]}:italic",
        "mathtext.bf": f"{faces[0]}:bold",
        "text.color": BLACK,
        "axes.labelsize": TEXT,
        "axes.titlesize": TEXT,
        "axes.linewidth": 0.5,
        "axes.edgecolor": BLACK,
        "axes.labelcolor": BLACK,
        "axes.axisbelow": True,
        "axes.unicode_minus": True,
        "xtick.labelsize": TEXT,
        "ytick.labelsize": TEXT,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "xtick.minor.width": 0.5,
        "ytick.minor.width": 0.5,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "xtick.minor.size": 1.5,
        "ytick.minor.size": 1.5,
        "xtick.major.pad": 2.0,
        "ytick.major.pad": 2.0,
        "legend.fontsize": TEXT,
        "legend.frameon": True,
        "legend.fancybox": False,
        "legend.edgecolor": BLACK,
        "legend.framealpha": 1.0,
        "legend.borderpad": 0.35,
        "legend.handlelength": 1.6,
        "legend.handletextpad": 0.4,
        "legend.labelspacing": 0.25,
        "legend.columnspacing": 0.9,
        "lines.linewidth": 0.75,
        "lines.markersize": 3.5,
        "patch.linewidth": 0.75,
        "hatch.linewidth": 0.5,
        "figure.dpi": 150,
        "savefig.dpi": 600,
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "pdf.compression": 9,
    })


def figure(width_in: float, height_mm: float):
    if height_mm * MM > MAX_DEPTH_IN:
        raise ValueError(f"{height_mm} mm leaves no room for a caption")
    return plt.figure(figsize=(width_in, height_mm * MM))


# ------------------------------------------------------------------------------- drawing
def region(ax, x, y, w, h, title, *, fill="white", lw=0.75, style="-", note=None,
           title_size=REGION, title_at="left", edge=BLACK, title_colour=BLACK):
    """A subsystem boundary with its name at the top left or right (and an italic note)."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", fc=fill, ec=edge,
                                lw=lw, linestyle=style, zorder=1))
    if title_at == "right":
        ax.text(x + w - 1.6, y + 1.3, title, fontsize=title_size, fontweight="bold",
                ha="right", va="top", zorder=4, color=title_colour)
    else:
        ax.text(x + 1.6, y + 1.3, title, fontsize=title_size, fontweight="bold", ha="left",
                va="top", zorder=4, color=title_colour)
    if note:
        ax.text(x + w - 1.6, y + 1.3, note, fontsize=TEXT, fontstyle="italic", ha="right",
                va="top", color=DARK, zorder=4)


def block(fig, ax, x, y, w, h, name, body=None, *, fill="white", lw=0.75, style="-",
          align="center", sep=None, name_size=NAME, edge=BLACK, name_colour=BLACK):
    """A component: its name in bold over a body wrapped to the block's width.

    The body is a string (wrapped between words) or a list of phrases (kept whole,
    separated by ``sep``). A body that does not fit stops the build.
    """
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", fc=fill, ec=edge,
                                lw=lw, linestyle=style, zorder=2))
    tx = {"center": x + w / 2, "left": x + 1.4}[align]
    lines = []
    if body and sep == "\n":
        lines = list(body)                      # one phrase per line, as given
    elif body:
        lines = (fl.wrap(fig, body, w - 2.4, TEXT) if sep is None
                 else fl.wrap(fig, body, w - 2.4, TEXT, sep=sep))
    names = name.split("\n") if name else []
    for line, weight in [(n, "bold") for n in names] + [(b, "normal") for b in lines]:
        if fl.text_width(fig, line, TEXT, weight) > w - 1.0:
            raise SystemExit(f"block {name!r}: {line!r} is wider than {w:.1f} mm")
    total = (len(names) + len(lines)) * LINE
    if total > h - 0.8:
        raise SystemExit(f"block {name!r}: {len(names) + len(lines)} lines need "
                         f"{total:.1f} mm, the block is {h:.1f} mm")
    top = y + (h - total) / 2
    if names:
        ax.text(tx, top, "\n".join(names), fontsize=name_size, fontweight="bold", ha=align,
                va="top", linespacing=1.15, zorder=4, color=name_colour)
    if lines:
        ax.text(tx, top + len(names) * LINE, "\n".join(lines), fontsize=TEXT, ha=align,
                va="top", linespacing=1.15, zorder=4)
    return (x, y, w, h)


def strip(fig, ax, x, y, w, h, name, body, *, fill="white", lw=0.75, style="-", sep=" · ",
          edge=BLACK, name_colour=BLACK):
    """A long, low block: its name in bold, then its body on the same line(s)."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", fc=fill, ec=edge,
                                lw=lw, linestyle=style, zorder=2))
    nw = fl.text_width(fig, name, TEXT, "bold")
    lines = fl.wrap(fig, body, w - nw - 4.0, TEXT, sep=sep)
    if len(lines) * LINE > h - 0.6:
        raise SystemExit(f"strip {name!r}: {len(lines)} lines in {h:.1f} mm")
    ax.text(x + 1.4, y + h / 2, name, fontsize=TEXT, fontweight="bold", ha="left",
            va="center", zorder=4, color=name_colour)
    ax.text(x + 2.6 + nw, y + h / 2, "\n".join(lines), fontsize=TEXT, ha="left",
            va="center", linespacing=1.15, zorder=4)


def arrow(ax, points, *, lw=0.75, dashed=False, head=True, both=False, color=BLACK, z=3):
    """An orthogonal connector through ``points`` (mm), with a filled head at the end
    (and at the start too when ``both``)."""
    style = (0, (3.0, 2.0)) if dashed else "-"
    xs, ys = zip(*points)
    first = 1 if both else 0
    if len(points) - first > 2:
        ax.plot(xs[first:-1], ys[first:-1], color=color, lw=lw, linestyle=style,
                solid_capstyle="butt", zorder=z)
    tip = "head_length=1.6,head_width=0.75"
    ax.add_patch(FancyArrowPatch(points[-2], points[-1],
                                 arrowstyle=f"-|>,{tip}" if head else "-",
                                 mutation_scale=1, color=color, lw=lw, linestyle=style,
                                 shrinkA=0, shrinkB=0, zorder=z))
    if both:
        # a head at the start too, on the first segment
        ax.add_patch(FancyArrowPatch(points[1], points[0], arrowstyle=f"-|>,{tip}",
                                     mutation_scale=1, color=color, lw=lw, linestyle=style,
                                     shrinkA=0, shrinkB=0, zorder=z))


def diamond(fig, ax, cx, cy, w, h, label, *, edge=BLACK, fill="white", lw=0.75):
    """A decision: a rhombus centred on (cx, cy) with its question inside. Text that would
    cross the rhombus's edges stops the build."""
    from matplotlib.patches import Polygon
    ax.add_patch(Polygon([(cx - w / 2, cy), (cx, cy - h / 2), (cx + w / 2, cy),
                          (cx, cy + h / 2)], closed=True, fc=fill, ec=edge, lw=lw, zorder=2))
    lines = label.split("\n")
    n = len(lines)
    for i, line in enumerate(lines):
        dy = (i - (n - 1) / 2) * LINE
        room = w * (1 - (abs(dy) + LINE / 2) / (h / 2))
        if fl.text_width(fig, line, TEXT) > room:
            raise SystemExit(f"diamond: {line!r} does not fit a {w}x{h} mm rhombus")
    ax.text(cx, cy, label, fontsize=TEXT, ha="center", va="center", linespacing=1.15,
            zorder=4)


def text(ax, x, y, s, *, ha="left", va="center", style="normal", weight="normal",
         color=BLACK, size=TEXT, bg=False, **kw):
    t = ax.text(x, y, s, fontsize=size, ha=ha, va=va, fontstyle=style, fontweight=weight,
                color=color, zorder=5, linespacing=1.15, **kw)
    if bg:
        t.set_bbox({"facecolor": "white", "edgecolor": "none", "pad": 0.6})
    return t


def step(ax, x, y, n, r=2.0, colour=BLACK):
    """A numbered step marker, as the caption refers to it."""
    ax.add_patch(Circle((x, y), r, fc=colour, ec=colour, lw=0.5, zorder=6))
    ax.text(x, y + 0.05, str(n), fontsize=TEXT, fontweight="bold", color="white",
            ha="center", va="center", zorder=7)


# ---------------------------------------------------------------------------------- plots
def axes(fig, left, top, width, height):
    """Axes placed in millimetres from the figure's top-left corner, in the IEEE look:
    a full frame, ticks inward on all four sides, a light dotted grid."""
    ax = ns.axes_mm(fig, left, top, width, height)
    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_linewidth(0.5)
        sp.set_color(BLACK)
    ax.tick_params(which="both", direction="in", top=True, right=True, colors=BLACK)
    return ax


def grid(ax, axis="both"):
    ax.grid(True, axis=axis, color=GRID, lw=0.5, linestyle=(0, (1.0, 1.5)))
    ax.set_axisbelow(True)


def subcaption(fig, x_mm, y_mm, text):
    """An IEEE sub-caption, "(a) ...", centred under its panel at (x, y) mm from top-left."""
    w, h = fig.get_size_inches()
    fig.text(x_mm * MM / w, 1 - y_mm * MM / h, text, ha="center", va="top", fontsize=TEXT)


# -------------------------------------------------------------------------------- output
def check_standard(fig, name: str) -> None:
    """Every text 8-10 pt and every stroke at least 0.5 pt at the size the figure prints."""
    from matplotlib.lines import Line2D
    from matplotlib.text import Text
    bad = []
    for t in fig.findobj(Text):
        if t.get_visible() and t.get_text().strip():
            s = t.get_fontsize()
            if s < MIN_TEXT_PT - 1e-6 or s > MAX_TEXT_PT + 1e-6:
                bad.append(f"{t.get_text()[:40]!r} at {s:.2f} pt")
    for a in fig.findobj(lambda o: isinstance(o, (Line2D, Patch))):
        if not a.get_visible():
            continue
        if isinstance(a, Line2D) and a.get_linestyle() in ("None", "", " "):
            continue
        if isinstance(a, Patch) and a.get_edgecolor()[3] == 0:
            continue
        lw = a.get_linewidth()
        if 0 < lw < MIN_LINE_PT - 1e-6:
            bad.append(f"{type(a).__name__} at {lw:.2f} pt")
    if bad:
        raise ns.BelowStandard(f"{name}: " + "; ".join(bad[:12]))


def save(fig, name: str) -> list[Path]:
    """Write ``name``.pdf and .eps (vector, TrueType embedded) and .png (600 dpi) to ieee/."""
    check_standard(fig, name)
    OUT.mkdir(parents=True, exist_ok=True)
    pdf, eps, png = (OUT / f"{name}.{ext}" for ext in ("pdf", "eps", "png"))
    meta = {"Title": name, "Author": "TCMScience authors",
            "Subject": "TCMScience architecture (IEEE style)",
            "Creator": "manuscript/code (matplotlib)", "CreationDate": None}
    os.environ["SOURCE_DATE_EPOCH"] = "1791590400"       # 2026-10-10, for the EPS header
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=r".*[Gg]lyph.*missing.*")
        warnings.filterwarnings("error", message=r".*missing from font.*")
        try:
            fig.savefig(pdf, metadata=meta)
            fig.savefig(eps, metadata={"Title": name, "Creator": "manuscript/code"})
            fig.savefig(png, dpi=600, metadata={"Software": None})
        except UserWarning as exc:
            raise ns.MissingGlyph(f"{name}: {exc}") from exc
    plt.close(fig)
    check_fonts(pdf)
    return [pdf, eps, png]


def check_fonts(pdf: Path) -> list[str]:
    try:
        out = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []
    fonts = []
    for line in out.splitlines()[2:]:
        parts = line.split()
        if not parts:
            continue
        face = parts[0].split("+")[-1]
        if not any(face.startswith(a) for a in ALLOWED_FONTS):
            raise ns.MissingGlyph(f"{pdf.name} embeds {face}: a glyph fell through to it")
        if parts[-5] != "yes":
            raise ns.MissingGlyph(f"{pdf.name}: {face} is not embedded")
        fonts.append(face)
    return fonts
