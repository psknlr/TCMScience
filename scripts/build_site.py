#!/usr/bin/env python3
"""Assemble the GitHub Pages site: the project page, its figures and the Arena.

    python scripts/build_site.py [--out _site]

Layout of the result:

    _site/index.html          the project page (site/)
    _site/assets/fig/*.svg    the figures, from docs/assets (drawn by make_figures.py)
    _site/arena/              the read-only Arena (arena/web)

One builder for CI and for local preview, so what is checked is what is served.
Nothing here fetches or computes: the Arena's data are validated by the Pages
workflow before this runs, and the figures are committed.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    fig = out / "assets" / "fig"
    fig.mkdir(parents=True, exist_ok=True)
    for svg in sorted((ROOT / "docs" / "assets").glob("*.svg")):
        shutil.copy2(svg, fig / svg.name)
    shutil.copytree(ROOT / "arena" / "web", out / "arena",
                    ignore=shutil.ignore_patterns("README.md", "scripts", "__pycache__"))
    (out / ".nojekyll").write_text("", encoding="utf-8")   # serve files as they are
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "_site"))
    out = build(Path(ap.parse_args().out))
    missing = [p for p in ("index.html", "assets/style.css", "assets/fig/overview.svg",
                           "assets/fig/licensing.svg", "arena/index.html") if not (out / p).is_file()]
    if missing:
        print(f"site is incomplete: {missing}")
        return 1
    print(f"built {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
