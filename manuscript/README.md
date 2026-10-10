# Manuscript display items: Figs. 1–7, Table 1 and two IEEE-style flowcharts

The figures, table, legends and Source Data for the TCMScience paper, built to Nature's
specifications for final figures, and two architecture flowcharts in the style of the IEEE
Transactions. Every name and number they show is read from the code and committed results of
this repository, or from its documentation with the file and line cited, and the build checks
that it still is. Where the README and the code disagree about which tools and databases the
system has, the figures follow the code (see [Found while building](#found-while-building)).

This directory is self-contained and changes nothing outside it, so it merges cleanly with
`main`. It is `manuscript/` rather than `paper/` because `.gitignore` reserves `paper/` as a
backstop for the manuscript's source and PDF, which live outside this working tree.

## Contents

| Item | Shows | Built from |
| --- | --- | --- |
| [Fig. 1](figures/Fig1.pdf) | Architecture, each component named as in the code; the 117 public sources by domain, every one named; data entry as audited snapshots, with the eight source cards and their licences; the TCM database catalogue, the federated capability catalogue and the native tools | the code: PSH's kernel, compiler and runtime; `bioagent.providers`, `.tools`, `.sources.cards`, `.tcmdb`, the registries and the federated catalogue; the connectors' live-verification record |
| [Fig. 2](figures/Fig2.pdf) | Study designs as types: the design × claim licensing matrix, the four points where it is enforced, 15 drafted claims and their verdicts | `psh.sir.values` (checked against `psh.workflow.compiler`); end-to-end cases 1–2 re-run, case 3 as recorded |
| [Fig. 3](figures/Fig3.pdf) | Governance ablation: 121 mutants × 14 configurations, which gate stops which error class, leave-one-out, closure rounds | `BioScience-Harness/benchmarks/ablation/results.json` (re-checked by its `--check`) |
| [Fig. 4](figures/Fig4.pdf) | Evidence typing on 478 real abstracts: protocol, confusion matrix, precision and recall before and after, dev vs test, PCO check | `BioScience-Harness/benchmarks/evidence_typing/results.json` (re-checked by its `--check`) |
| [Fig. 5](figures/Fig5.pdf) | The 葛根芩连汤 case study: data, background choice, disease-gene definitions, CYP signal, threshold robustness | the case study's documented counts (`data/curated/np_*.csv`); folds and P values recomputed |
| [Fig. 6](figures/Fig6.pdf) | Falsifiable follow-up: robustness to evidence hold-outs, berberine exposure ratios, activity at measured exposure, the eight routes | `docs/falsifiable-studies.md`, `docs/studies/gqd_berberine.json` |
| [Fig. 7](figures/Fig7.pdf) | The inquiry engine on three planted worlds: sealed predictions, belief trajectories, the engine's options at each step | `scripts/run_inquiry.py --planted all`, re-run and replayed step by step through `psh.scientist.inquiry` |
| [Table 1](tables/Table1.md) | Each figure's question, construction, principal result and what it does not show | the same data files (`.md`, `.csv`, `.tex`, `.docx`) |
| [Legends](legends.md) | Figure legends, 264–347 words each | — |
| [Source Data](source_data/) | One workbook per figure, one sheet per panel, with provenance on every sheet | the same data files |
| [IEEE Fig. 1](ieee/IEEE_Fig_Architecture.pdf) | The agent architecture in full, as a two-column IEEE flowchart (7.16 in): entry, the trusted kernel's ten numbered steps, governance, the capability plane | the code, as for Fig. 1 |
| [IEEE Fig. 2](ieee/IEEE_Fig_Minimal.pdf) | The same architecture reduced to its one idea, as a one-column IEEE flowchart (3.5 in) | — |
| [IEEE captions](ieee/captions.md) | Captions for both, also as IEEEtran `figure*`/`figure` environments ([`captions.tex`](ieee/captions.tex)) | — |

```
manuscript/
├── figures/        Fig1–Fig7: .pdf (vector, fonts embedded) and .png (600 dpi preview)
├── tables/         Table1.md · .csv · .tex · .docx
├── legends.md
├── source_data/    Source_Data_Fig1–7.xlsx
├── ieee/           IEEE_Fig_Architecture and IEEE_Fig_Minimal: .pdf, .eps (vector) and .png
│                   (600 dpi); captions.md, captions.tex
├── data/
│   ├── extracted/  read from the code and committed results by code/extract_data.py
│   └── curated/    documented values, each with the file, line and a quote it must match
└── code/           nature_style.py, figlib.py, extract_data.py, fig1–fig7, make_table1.py,
                    make_source_data.py, ieee_style.py, ieee_fig_architecture.py,
                    ieee_fig_minimal.py, make_all.py, requirements.txt
```

## Build

```bash
pip install -r manuscript/code/requirements.txt     # plus fonts-liberation, fonts-wqy-zenhei
python manuscript/code/make_all.py                  # figures, table, Source Data (~20 s)

# re-read everything from the code first (needs PSH-Harness and BioScience-Harness installed)
python manuscript/code/make_all.py --extract --verify
# … and re-run end-to-end cases 1 and 2 (needs GSEApy and PaperQA2)
python manuscript/code/make_all.py --extract --cases --verify
```

On the same commit a rebuild is byte-identical: PDFs carry no creation date, the EPS files a
fixed one, and the Office files' timestamps are fixed. A difference after a rebuild is therefore a change in the data or
the code.

## Standards applied

Checked by the build, not by eye:

- **Size.** Two-column width, 183 mm; depth 140–213 mm, inside a 247 mm page.
- **Type.** Every text 5–8 pt at final size, every line at least 0.25 pt: `check_standard`
  refuses to save a figure that breaks either. Panel letters 8 pt bold lower case.
- **Fonts.** Arial where installed, otherwise Liberation Sans, which is metrically identical;
  Chinese in WenQuanYi Zen Hei. Fonts are embedded as TrueType, so text stays editable. A glyph
  that would fall through to any other font stops the build (`pdffonts` is read back).
- **Colour.** Okabe–Ito (Wong, *Nat. Methods* 2011). The four categorical hues were run through
  a colour-vision validator against white: every pair separates under simulated protanopia and
  deuteranopia (worst ΔE 11.0) and under normal vision (worst 15.6). Orange, below 3:1 contrast,
  never carries meaning without a label. Magnitude uses one hue, light to dark. Identity is never
  colour alone: marker shape, fill and direct labels carry it too.
- **Statistics.** Every proportion with its n and a 95% Wilson interval; exposure ratios with
  90% intervals as the study's protocol fixes; P values exact, in the form Nature prints them;
  what every bar and band means is stated in the legend. No panel carries a title of its own.
- **Source Data.** The numbers behind every panel, with the derived values the figure computes
  beside their inputs; for Fig. 1, every public source, catalogue entry, source card, federated
  project and engine by name.

The IEEE-style flowcharts follow IEEE's figure sizes instead, also checked by the build:

- **Size.** One column, 3.5 in (21 pc); two columns, 7.16 in (43 pc), so `captions.tex` places
  them at `\columnwidth` and `\textwidth` of an IEEEtran journal page, at 100 %.
- **Type.** Times New Roman where installed, otherwise Liberation Serif, which is metrically
  identical, embedded as TrueType; every text 8–9 pt at final size, every stroke at least
  0.5 pt. Text that does not fit its block stops the build rather than overflowing it.
- **Greyscale.** Meaning is carried by line style (solid, execution; dashed, an untrusted
  component or a constraint), weight and labels, never by hue, so the figures read the same in
  print and on screen. Numbered steps in the full flowchart are the ones its caption walks
  through.
- **Files.** PDF and EPS (vector) and PNG at 600 dpi, IEEE's floor for line art.

## Provenance and checks

- **Extracted values** (`data/extracted/`) come from the kernel's own tables, imported rather
  than copied (the licensing matrix, the compiler's passes and diagnostics, the plan
  validator's check families, the loop's stop reasons, the four stores), from the capability
  plane's own registries (every public source, the TCM catalogue and data hub, the source cards
  and parsers, the federated catalogue, the reviewed external tools, the engines, and the tool
  catalogue Studio builds from the code), from the committed benchmark results, and from runs
  repeated here: the
  planted-world inquiries (replayed through the engine, whose recomputed beliefs must match the
  run's) and end-to-end cases 1 and 2 (whose verdicts must match the cases' expectations).
  `--verify` also re-runs the ablation's and the evidence-typing benchmark's own `--check`.
- **Curated values** (`data/curated/`) are the case-study numbers that exist only in the
  repository's documentation, because the snapshots behind them are not redistributed. Each row
  names a file and line and quotes a fragment of it; `extract_data.py` stops if any of the 111
  fragments is no longer on its line.
- **Recomputation.** Fold enrichments and hypergeometric P values in Fig. 5 are recomputed from the
  documented counts and must match the reported values; the maximum fold on the assayed background
  must equal 391/237; the compile-time licensing table must be a subset of the matrix drawn in
  Fig. 2; and the panel counts in Fig. 5e must equal the reported number of passing pathways.

## Found while building

The figures follow the code where it and the prose disagree. These were left as they are,
since this directory changes nothing outside itself:

1. **Inventory.** `README.md` lines 62 and 240 (and `README.zh-CN.md`, and the hard-coded labels
   in `docs/assets/make_figures.py` line 142) give 58 public sources, 153 typed operations and
   147 native tools. At this commit the code registers 117 sources in 29 domains with 444
   operations, all verified live, and 150 native tools (`tests/test_native_tools.py` asserts
   150). The code's own descriptions lag too: the bridge's module docstring
   (`BioScience-Harness/src/bioagent/psh/__init__.py` line 7) says fifty-nine public sources;
   the TCM hub's (`bioagent/tcmdb/__init__.py` line 3) says its catalogue lists 66 sources,
   where `tcm_source_catalog.json` holds 136 entries; and `studio/docs/ARCHITECTURE.md` line 60
   says Studio's core tools reach 642 catalogue entries, where the catalogue Studio builds from
   the code has 647 (150 native tools, 444 connector operations, 11 skills and 42 others).
2. **Ablation base cases.** `docs/governance-ablation.md` line 12 lists "three randomised trials,
   two cohorts, a case report, a bench assay, a docking run, and a passage", which is nine; the
   corpus has ten, four of them citing randomized trials (E1–E4).
3. **Berberine exposure.** `docs/studies/gqd_berberine.md` lines 62–63 say that no activity has a
   ratio ≥ 1 even if all of the drug were free. At 黄连 alone's Cmax (228 nM), 15 of the 28 assays
   not ruled out exceed 1 at f_u = 1 (up to 2.86), and one does with the whole formula (1.05).
   What holds, and what Fig. 6c shows, is that none is plausible across the whole range of f_u
   (0.01–1) the analysis allows.

## What these figures do not claim

They inherit every limitation the project states. The ablation is a regression benchmark of a
stated construction, not a field error rate; evidence typing measures agreement with PubMed, not
with the truth; the planted worlds have a known answer by construction; the case-study results are
statements about public databases, not about the formula's mechanism; and no figure shows a
model writing science under these checks, which the four-arm comparison (`docs/comparison.md`)
is designed for and has not yet run.
