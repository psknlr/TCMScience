#!/usr/bin/env python3
"""Build every display item: Figs. 1-7, Table 1 and the Source Data, then check them.

    python manuscript/code/make_all.py                 # from the committed data/
    python manuscript/code/make_all.py --extract       # re-read the code and results first
    python manuscript/code/make_all.py --extract --cases --verify

Checks, after building: every figure is 183 mm wide and no deeper than a page; every PDF
embeds only the allowed fonts, all of them; every text is 5-8 pt and every line at least
0.25 pt (enforced as each figure is saved). The two IEEE-style flowcharts are built too, at
7.16 in and 3.5 in, in Times-metric type of 8 pt or more with strokes of 0.5 pt or more. A
failed check stops the build.
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
from pathlib import Path

import nature_style as ns

FIGURES = ["fig1_architecture", "fig2_licensing", "fig3_ablation", "fig4_evidence_typing",
           "fig5_network_pharmacology", "fig6_falsifiable", "fig7_inquiry"]
IEEE = [("ieee_fig_architecture", 7.16), ("ieee_fig_minimal", 3.5)]


def pdf_size_mm(pdf: Path) -> tuple[float, float]:
    from pypdf import PdfReader
    box = PdfReader(str(pdf)).pages[0].mediabox
    return float(box.width) / 72 * 25.4, float(box.height) / 72 * 25.4


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--extract", action="store_true", help="rebuild data/extracted first")
    ap.add_argument("--cases", action="store_true", help="with --extract: re-run cases 1-2")
    ap.add_argument("--verify", action="store_true",
                    help="with --extract: re-run the benchmarks' CI checks")
    args = ap.parse_args(argv)
    here = Path(__file__).resolve().parent
    if args.extract:
        cmd = [sys.executable, str(here / "extract_data.py")]
        cmd += ["--cases"] * args.cases + ["--verify"] * args.verify
        subprocess.run(cmd, check=True)
    rows = []
    for name in FIGURES:
        pdf, png = importlib.import_module(name).main()
        w, h = pdf_size_mm(pdf)
        if abs(w - 183) > 0.5 or h > 247:
            raise SystemExit(f"{pdf.name}: {w:.1f} × {h:.1f} mm is not a two-column figure")
        fonts = ns.check_fonts(pdf)
        rows.append((pdf.stem, f"{w:.0f} × {h:.0f} mm", ", ".join(sorted(set(fonts)))))
    import ieee_style
    for name, width in IEEE:
        pdf = importlib.import_module(name).main()[0]
        w, h = (v / 25.4 for v in pdf_size_mm(pdf))
        if abs(w - width) > 0.01 or h > ieee_style.MAX_DEPTH_IN:
            raise SystemExit(f"{pdf.name}: {w:.2f} × {h:.2f} in is not a {width} in figure")
        fonts = ieee_style.check_fonts(pdf)
        rows.append((pdf.stem, f"{w:.2f} × {h:.2f} in", ", ".join(sorted(set(fonts)))))
    importlib.import_module("make_table1").main()
    importlib.import_module("make_source_data").main()
    width = max(len(r[2]) for r in rows)
    print(f"{'item':22} {'size':16} fonts")
    for stem, size, fonts in rows:
        print(f"{stem:22} {size:16} {fonts:{width}}")
    print("Table1 .md .csv .tex .docx; Source_Data_Fig1-7.xlsx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
