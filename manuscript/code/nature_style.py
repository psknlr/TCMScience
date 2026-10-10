"""House style for the manuscript's display items, applied once for every figure.

Sizes and type follow Nature's guide to preparing final figures:

* widths of 89 mm (one column), 120-136 mm (one and a half) or 183 mm (two columns), and a
  depth that leaves room for the legend on a 247 mm page;
* a sans-serif face at 5-7 pt at final size, panel labels in 8 pt bold lower case;
* no line thinner than 0.25 pt, RGB colour, and text kept as text: fonts are embedded as
  TrueType (``pdf.fonttype = 42``), so labels stay editable in the PDF.

The face is Arial where it is installed and Liberation Sans otherwise. Liberation Sans is
metrically identical to Arial, so a figure set in one has the layout of the other. Chinese
characters fall back to WenQuanYi Zen Hei. A glyph that neither font has is an error, not a
silent substitution: :func:`save` refuses to write a figure that needed a third font.

Colour is Okabe-Ito (Wong, Nat. Methods 8, 441; 2011). The four hues used for categories
(blue, vermillion, bluish green, orange) were checked with the colour validator of this
project's chart guidance against a white page: every pair separates under simulated
protanopia and deuteranopia (worst Delta E 11.0, OKLab x100), and under normal vision (worst
15.6). Orange sits below 3:1 contrast on white, so it never carries meaning without a direct
label. Magnitude uses one hue, light to dark.
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, to_rgb  # noqa: E402

HERE = Path(__file__).resolve().parent
MANUSCRIPT = HERE.parent
REPO = MANUSCRIPT.parent
FIGURES = MANUSCRIPT / "figures"
DATA = MANUSCRIPT / "data"

MM = 1 / 25.4                       # inches per millimetre
SINGLE = 89 * MM                    # one column
ONE_HALF = 136 * MM                 # one and a half columns
DOUBLE = 183 * MM                   # two columns
MAX_DEPTH = 247 * MM                # a full page, legend included

# ----------------------------------------------------------------------------- colour
BLUE = "#0072B2"          # trusted kernel; the leading series
VERMILLION = "#D55E00"    # domain layer; the series in contrast to it
GREEN = "#009E73"         # capability plane
ORANGE = "#E69F00"        # governance layer (always labelled: < 3:1 on white)
SKY = "#56B4E9"
PURPLE = "#CC79A7"
YELLOW = "#F0E442"

INK = "#1a1a1a"           # primary text
INK2 = "#4d4d4d"          # secondary text
MUTED = "#808080"         # axis text that should recede, "before" values
RULE = "#b3b3b3"          # axes and hairlines
HAIR = "#d9d9d9"          # gridlines
WASH = "#f2f2f2"          # backgrounds of grouped regions

#: Single-hue ramp for magnitude (ColorBrewer Blues, monotone in lightness).
BLUES = LinearSegmentedColormap.from_list(
    "blues", ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6",
              "#2171b5", "#08519c", "#08306b"])


def tint(colour: str, amount: float) -> tuple[float, float, float]:
    """``colour`` mixed with white: ``amount`` 0 is white, 1 is the colour itself."""
    r, g, b = to_rgb(colour)
    return (1 - amount + amount * r, 1 - amount + amount * g, 1 - amount + amount * b)


def ink_on(rgb) -> str:
    """Text colour for a label set inside a filled cell: white on dark fills, ink on light."""
    r, g, b = to_rgb(rgb)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "white" if luminance < 0.5 else INK


# ------------------------------------------------------------------------------- type
LATIN = ["Arial", "Liberation Sans"]
CJK = ["WenQuanYi Zen Hei"]
#: Every font a figure may embed. Anything else in a PDF means a glyph fell through.
ALLOWED_FONTS = ("Arial", "LiberationSans", "WenQuanYiZenHei")

SIZE = 6.0            # body text: axis labels, annotations
SMALL = 5.5           # tick labels, legends, dense annotation
TINY = 5.0            # the floor Nature allows
PANEL = 8.0           # panel letters


def _available(families: list[str]) -> list[str]:
    names = {f.name for f in font_manager.fontManager.ttflist}
    return [f for f in families if f in names]


def apply() -> None:
    """Set matplotlib's defaults to the house style. Call before creating a figure."""
    latin = _available(LATIN)
    if not latin:
        raise RuntimeError("neither Arial nor Liberation Sans is installed; "
                           "install fonts-liberation (or Arial) to build the figures")
    family = latin[:1] + _available(CJK)
    plt.rcParams.update({
        "font.family": family,
        "font.size": SIZE,
        "mathtext.fontset": "custom",
        "mathtext.rm": latin[0],
        "mathtext.it": f"{latin[0]}:italic",
        "mathtext.bf": f"{latin[0]}:bold",
        "mathtext.sf": latin[0],
        "axes.labelsize": SIZE,
        "axes.titlesize": SIZE,
        "axes.titleweight": "bold",
        "axes.labelcolor": INK,
        "axes.edgecolor": INK2,
        "axes.linewidth": 0.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.unicode_minus": True,
        "axes.axisbelow": True,
        "xtick.labelsize": SMALL,
        "ytick.labelsize": SMALL,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "xtick.labelcolor": INK,
        "ytick.labelcolor": INK,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "xtick.minor.width": 0.4,
        "ytick.minor.width": 0.4,
        "xtick.major.size": 2.0,
        "ytick.major.size": 2.0,
        "xtick.minor.size": 1.2,
        "ytick.minor.size": 1.2,
        "xtick.major.pad": 1.5,
        "ytick.major.pad": 1.5,
        "grid.color": HAIR,
        "grid.linewidth": 0.4,
        "grid.linestyle": "-",
        "lines.linewidth": 0.9,
        "lines.markersize": 3.0,
        "patch.linewidth": 0.5,
        "hatch.linewidth": 0.5,
        "legend.fontsize": SMALL,
        "legend.frameon": False,
        "legend.handlelength": 1.2,
        "legend.handletextpad": 0.4,
        "legend.borderaxespad": 0.2,
        "legend.labelspacing": 0.3,
        "legend.columnspacing": 0.9,
        "figure.dpi": 150,
        "savefig.dpi": 600,
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "pdf.compression": 9,
    })


def figure(width: float, height_mm: float):
    """A blank figure ``width`` inches wide and ``height_mm`` millimetres deep."""
    if height_mm * MM > MAX_DEPTH:
        raise ValueError(f"{height_mm} mm is deeper than a page")
    return plt.figure(figsize=(width, height_mm * MM))


def panel_label(fig, letter: str, x_mm: float, y_mm: float) -> None:
    """Bold lower-case panel letter with its top-left corner at (``x_mm``, ``y_mm``)
    measured from the figure's top-left corner."""
    w, h = fig.get_size_inches()
    fig.text(x_mm * MM / w, 1 - y_mm * MM / h, letter, fontsize=PANEL, fontweight="bold",
             ha="left", va="top", color="black")


def axes_mm(fig, left: float, top: float, width: float, height: float, **kw):
    """Axes placed in millimetres from the figure's top-left corner, so panels align to a
    grid that can be read off the layout rather than tuned by eye."""
    w, h = fig.get_size_inches()
    return fig.add_axes([left * MM / w, 1 - (top + height) * MM / h,
                         width * MM / w, height * MM / h], **kw)


def hairline_grid(ax, axis: str = "y") -> None:
    ax.grid(True, axis=axis, color=HAIR, linewidth=0.4)
    ax.set_axisbelow(True)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for ``k`` successes in ``n`` trials (95 % by default).

    The interval the repository's benchmarks report, recomputed here so that numbers taken
    from documentation get the same treatment as those read from result files.
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


# ------------------------------------------------------------------------------ output
class MissingGlyph(RuntimeError):
    pass


class BelowStandard(RuntimeError):
    pass


#: Nature's floor for text at final size; nothing on a figure may be set smaller.
MIN_TEXT_PT = 5.0
MAX_TEXT_PT = 8.0
MIN_LINE_PT = 0.25


def check_standard(fig, name: str) -> None:
    """Every text at 5-8 pt and every line at least 0.25 pt, at the size the figure prints.

    Checked on the artists themselves, so a label added later cannot slip below the floor.
    """
    from matplotlib.lines import Line2D
    from matplotlib.text import Text
    small, thin = [], []
    for artist in fig.findobj(Text):
        if not artist.get_visible() or not artist.get_text().strip():
            continue
        size = artist.get_fontsize()
        if size < MIN_TEXT_PT - 1e-6 or size > MAX_TEXT_PT + 1e-6:
            small.append(f"{artist.get_text()[:40]!r} at {size:.2f} pt")
    for artist in fig.findobj(Line2D):
        if artist.get_visible() and artist.get_linestyle() not in ("None", "", " ") \
                and 0 < artist.get_linewidth() < MIN_LINE_PT - 1e-6:
            thin.append(f"line at {artist.get_linewidth():.2f} pt")
    if small or thin:
        raise BelowStandard(f"{name}: " + "; ".join((small + thin)[:12]))


def save(fig, name: str) -> list[Path]:
    """Write ``name``.pdf (vector, fonts embedded) and ``name``.png (600 dpi) to figures/.

    A glyph missing from every allowed font makes matplotlib warn and draw a box. Here
    that is an error, and so is a PDF that embeds any font outside ``ALLOWED_FONTS``, and
    so is any text below 5 pt or line below 0.25 pt (:func:`check_standard`).
    """
    check_standard(fig, name)
    FIGURES.mkdir(parents=True, exist_ok=True)
    pdf, png = FIGURES / f"{name}.pdf", FIGURES / f"{name}.png"
    meta = {"Title": name, "Author": "TCMScience authors",
            "Subject": "TCMScience manuscript display item",
            "Creator": "manuscript/code (matplotlib)", "CreationDate": None}
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=r".*[Gg]lyph.*missing.*")
        warnings.filterwarnings("error", message=r".*missing from font.*")
        try:
            fig.savefig(pdf, metadata=meta)
            fig.savefig(png, dpi=600, metadata={"Software": None})
        except UserWarning as exc:
            raise MissingGlyph(f"{name}: {exc}") from exc
    plt.close(fig)
    check_fonts(pdf)
    return [pdf, png]


def check_fonts(pdf: Path) -> list[str]:
    """Fonts embedded in ``pdf``; raises if any is not embedded or not allowed."""
    try:
        out = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []                                   # poppler absent: nothing to check with
    fonts = []
    for line in out.splitlines()[2:]:
        parts = line.split()
        if not parts:
            continue
        name = parts[0].split("+")[-1]
        emb = parts[-5] if len(parts) >= 5 else "no"
        if not any(name.startswith(a) for a in ALLOWED_FONTS):
            raise MissingGlyph(f"{pdf.name} embeds {name}: a glyph fell through to it")
        if emb != "yes":
            raise MissingGlyph(f"{pdf.name}: {name} is not embedded")
        fonts.append(name)
    return fonts
