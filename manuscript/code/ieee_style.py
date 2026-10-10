"""House style for the two IEEE Transactions-style architecture flowcharts.

IEEE sizes figures to its columns: 3.5 in (21 picas) for one column and 7.16 in (43 picas)
for two. It prefers vector files (PDF, EPS) with fonts embedded, raster line art at
600 dpi or more, and type that matches the paper. Accordingly:

* the face is Times New Roman where installed and Liberation Serif otherwise, which is
  metrically identical to it, so the figures set like the IEEEtran text around them;
* every text is 8 pt or larger at final size (block names 8 pt bold, region names 9 pt
  bold) and every stroke at least 0.5 pt, so nothing thins out in print;
* the drawing is greyscale: meaning is carried by line style (solid, execution; dashed,
  untrusted or constraint), by weight and by labels, never by hue, so it reads the same
  in print, on screen and in a photocopy;
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
FILL = "#f2f2f2"     # the trusted kernel's region
FILL2 = "#e4e4e4"    # emphasis inside a region

LINE = TEXT * 1.15 * 25.4 / 72      # one line of 8 pt text at linespacing 1.15, in mm


def _available(families):
    names = {f.name for f in font_manager.fontManager.ttflist}
    return [f for f in families if f in names]


def apply() -> None:
    faces = _available(SERIF)
    if not faces:
        raise RuntimeError("neither Times New Roman nor Liberation Serif is installed; "
                           "install fonts-liberation to build the IEEE figures")
    plt.rcParams.update({
        "font.family": faces[:1],
        "font.size": TEXT,
        "mathtext.fontset": "custom",
        "mathtext.rm": faces[0],
        "mathtext.it": f"{faces[0]}:italic",
        "mathtext.bf": f"{faces[0]}:bold",
        "text.color": BLACK,
        "lines.linewidth": 0.75,
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
           title_size=REGION, title_at="left"):
    """A subsystem boundary with its name at the top left or right (and an italic note)."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", fc=fill, ec=BLACK,
                                lw=lw, linestyle=style, zorder=1))
    if title_at == "right":
        ax.text(x + w - 1.6, y + 1.3, title, fontsize=title_size, fontweight="bold",
                ha="right", va="top", zorder=4)
    else:
        ax.text(x + 1.6, y + 1.3, title, fontsize=title_size, fontweight="bold", ha="left",
                va="top", zorder=4)
    if note:
        ax.text(x + w - 1.6, y + 1.3, note, fontsize=TEXT, fontstyle="italic", ha="right",
                va="top", color=DARK, zorder=4)


def block(fig, ax, x, y, w, h, name, body=None, *, fill="white", lw=0.75, style="-",
          align="center", sep=None, name_size=NAME):
    """A component: its name in bold over a body wrapped to the block's width.

    The body is a string (wrapped between words) or a list of phrases (kept whole,
    separated by ``sep``). A body that does not fit stops the build.
    """
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", fc=fill, ec=BLACK,
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
                va="top", linespacing=1.15, zorder=4)
    if lines:
        ax.text(tx, top + len(names) * LINE, "\n".join(lines), fontsize=TEXT, ha=align,
                va="top", linespacing=1.15, zorder=4)
    return (x, y, w, h)


def strip(fig, ax, x, y, w, h, name, body, *, fill="white", lw=0.75, style="-", sep=" · "):
    """A long, low block: its name in bold, then its body on the same line(s)."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="square,pad=0", fc=fill, ec=BLACK,
                                lw=lw, linestyle=style, zorder=2))
    nw = fl.text_width(fig, name, TEXT, "bold")
    lines = fl.wrap(fig, body, w - nw - 4.0, TEXT, sep=sep)
    if len(lines) * LINE > h - 0.6:
        raise SystemExit(f"strip {name!r}: {len(lines)} lines in {h:.1f} mm")
    ax.text(x + 1.4, y + h / 2, name, fontsize=TEXT, fontweight="bold", ha="left",
            va="center", zorder=4)
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


def text(ax, x, y, s, *, ha="left", va="center", style="normal", weight="normal",
         color=BLACK, size=TEXT, bg=False, **kw):
    t = ax.text(x, y, s, fontsize=size, ha=ha, va=va, fontstyle=style, fontweight=weight,
                color=color, zorder=5, linespacing=1.15, **kw)
    if bg:
        t.set_bbox({"facecolor": "white", "edgecolor": "none", "pad": 0.6})
    return t


def step(ax, x, y, n, r=2.0):
    """A numbered step marker, as the caption refers to it."""
    ax.add_patch(Circle((x, y), r, fc=BLACK, ec=BLACK, lw=0.5, zorder=6))
    ax.text(x, y + 0.05, str(n), fontsize=TEXT, fontweight="bold", color="white",
            ha="center", va="center", zorder=7)


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
