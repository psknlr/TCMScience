"""Drawing helpers shared by the figure scripts: data loading and millimetre canvases.

Schematic panels are drawn on a canvas whose units are millimetres with y increasing
downwards, so a layout can be read off the code the way it reads on the page.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

import nature_style as ns

EXTRACTED = ns.DATA / "extracted"
CURATED = ns.DATA / "curated"


# -------------------------------------------------------------------------------- data
def rows(name: str) -> list[dict]:
    """A CSV from data/extracted (or data/curated, if not found there)."""
    path = EXTRACTED / name
    if not path.exists():
        path = CURATED / name
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_json(name: str):
    return json.loads((EXTRACTED / name).read_text(encoding="utf-8"))


def counts() -> dict[str, float]:
    """The curated case-study counts as ``key -> value``."""
    return {r["key"]: float(r["value"]) for r in rows("np_counts.csv")}


# ----------------------------------------------------------------------------- canvases
def canvas(fig, left: float, top: float, width: float, height: float):
    """An invisible axes spanning the given millimetre box, in millimetre units (y down)."""
    ax = ns.axes_mm(fig, left, top, width, height)
    ax.set_xlim(0, width)
    ax.set_ylim(height, 0)
    ax.set_axis_off()
    return ax


def box(ax, x, y, w, h, text="", *, fc="white", ec=ns.INK2, lw=0.5, r=0.8,
        size=ns.SIZE, weight="normal", color=ns.INK, ha="center", va="center",
        linespacing=1.15, pad=1.2, style="-", z=2, **kw):
    patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                           fc=fc, ec=ec, lw=lw, linestyle=style, zorder=z)
    ax.add_patch(patch)
    if text:
        tx = {"center": x + w / 2, "left": x + pad, "right": x + w - pad}[ha]
        ty = {"center": y + h / 2, "top": y + pad, "bottom": y + h - pad}[va]
        ax.text(tx, ty, text, ha=ha, va={"top": "top", "bottom": "bottom"}.get(va, "center"),
                fontsize=size, fontweight=weight, color=color, linespacing=linespacing,
                zorder=z + 1, **kw)
    return patch


def arrow(ax, x1, y1, x2, y2, *, color=ns.INK2, lw=0.6, head=2.2, style="-|>",
          dashed=False, z=1, shrink=0.0, connection="arc3,rad=0"):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=f"{style},head_length={head * 0.55},"
                        f"head_width={head * 0.32}", color=color, lw=lw,
                        linestyle=(0, (2.2, 1.6)) if dashed else "-", zorder=z,
                        shrinkA=shrink, shrinkB=shrink, mutation_scale=1,
                        connectionstyle=connection)
    ax.add_patch(a)
    return a


def label(ax, x, y, text, *, size=ns.SIZE, color=ns.INK, ha="left", va="center",
          weight="normal", style="normal", z=4, **kw):
    return ax.text(x, y, text, fontsize=size, color=color, ha=ha, va=va,
                   fontweight=weight, fontstyle=style, zorder=z, **kw)


def pct(v: float) -> str:
    return f"{100 * v:.0f}"


# ------------------------------------------------------------------ reproducible files
FIXED_TIME = (2026, 10, 10, 0, 0, 0)
FIXED_ISO = "2026-10-10T00:00:00Z"


def deterministic_zip(path: Path) -> None:
    """Rewrite an Office file (.xlsx, .docx) so the same content gives the same bytes.

    Writers stamp the current time on every zip entry and in docProps/core.xml; both are
    set to a fixed date here, so a rebuild on the same commit changes nothing in git.
    """
    import re
    import zipfile
    with zipfile.ZipFile(path) as src:
        entries = [(info, src.read(info.filename)) for info in src.infolist()]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info, data in entries:
            if info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                text = re.sub(r"(<dcterms:(created|modified)[^>]*>)[^<]*(</dcterms:\2>)",
                              rf"\g<1>{FIXED_ISO}\g<3>", text)
                data = text.encode("utf-8")
            fixed = zipfile.ZipInfo(info.filename, date_time=FIXED_TIME)
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = 0o644 << 16
            dst.writestr(fixed, data)
    tmp.replace(path)
