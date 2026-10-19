"""The RNA-seq pipeline's count-matrix mode: a count table to a differential-expression report.

    from bioagent.omics.rnaseq_counts import RNASeqCountsConfig, run_rnaseq_counts
    run = run_rnaseq_counts("counts.tsv", "samples.tsv", RNASeqCountsConfig(
        design="~ condition", contrast=("condition", "treated", "control")), "results/")

For counts made elsewhere (featureCounts, salmon through tximport, a published matrix).
Two tables go in:

* the **counts**: genes in rows, CSV or TSV (gzipped or not; the header's more frequent
  separator decides): ``gene_id``, an optional ``gene_name``, then one column per
  sample, every value a non-negative integer (``read_counts_table``);
* the **samples**: a ``sample`` column and the design variables (``condition``,
  ``batch``, ...), one row per count column (``read_sample_table``). An nf-core sheet
  (``fastq_1``/``fastq_2``) is refused: reads go through the full pipeline
  (:mod:`.rnaseq`).

From the counts on, the steps are the full pipeline's, by the same code
(``rnaseq._test``, ``_explore``, ``_de_plots``): differential expression with the DESeq2
method (``de_backends.run_de``, built-in or PyDESeq2), the variance-stabilised matrix, PCA
and sample distances, the same tables and figures, ``report.md``/``report.html``,
``limitations.md`` and ``run.json``. Nothing before them ran: no read was seen, so there is
no QC, trimming or quantification, and the report says the counts were taken as given.

The stages are ``STAGES``, each recorded (``bioagent.runrecord``). ``run.json`` names the
pipeline ``PIPELINE``, the input kind ``counts``, each input file (path, SHA-256, size),
and the content digests of the canonical count table (``counts.tsv`` as written) and
sample table, which ``runrecord.match_reference`` compares with the registered reference
datasets. ``verify_run`` re-checks the inputs, the outputs, the stages and the tables;
``rnaseq.verify_run`` hands a record of this mode to it, so one check covers both modes.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import platform
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import scipy

from .. import __version__ as _VERSION
from ..runrecord import (LIMITATIONS_FILE, STAGES_FILE, StageRecorder, match_reference,
                         output_problems, record_problems, stage_limitations,
                         write_limitations)
from . import de_backends
from . import rnaseq as R
from .deseq import DESeqError
from .rnaseq import RNASeqError

__all__ = ["CountsTable", "RNASeqCountsConfig", "RNASeqCountsRun", "read_counts_table",
           "read_sample_table", "run_rnaseq_counts", "verify_run", "PIPELINE", "STAGES",
           "LIMITATIONS"]

PIPELINE = "bioagent.omics.rnaseq_counts"
#: The stages of a run, in order; every one is required.
STAGES = ("validate_counts", "differential_expression", "explore", "report", "verify")
#: What every result of this mode is not, one line each (``limitations.md``); the full
#: pipeline's, said of counts rather than of a reference, and what was not seen.
LIMITATIONS = (
    "A result is a statistical association within this experiment: which genes differ "
    "between the groups of these samples, with these counts and these settings. It does "
    "not establish a mechanism, a cause or any clinical effect.",
    *R.LIMITATIONS[1:],
    "The counts were analysed as given: how they were produced (library preparation, "
    "aligner or quantifier, annotation, any filtering) is not known to this run, and no "
    "read was quality-checked here.",
)
_UNLISTED = {"run.json", STAGES_FILE}
#: The columns of a FASTQ sample sheet, which this mode does not take.
_FASTQ = ("fastq_1", "fastq_2")


# ============================================================== the inputs

@dataclass(frozen=True)
class CountsTable:
    genes: tuple[str, ...]
    gene_names: tuple[str, ...]               # the gene ids where the table names none
    samples: tuple[str, ...]
    counts: np.ndarray                        # int64, genes x samples

    def text(self) -> str:
        """The canonical table (``counts.tsv``): ``gene_id``, ``gene_name`` and the samples,
        tab-separated, every count an integer. Its SHA-256 names the counts whatever file
        format, separator or compressor held them."""
        out = io.StringIO()
        out.write("\t".join(("gene_id", "gene_name", *self.samples)) + "\n")
        for g, name, row in zip(self.genes, self.gene_names, self.counts.tolist()):
            out.write("\t".join((g, name, *map(str, row))) + "\n")
        return out.getvalue()


def _text(raw: bytes, where: str) -> str:
    """A table's text, gunzipped when it is gzip (by its magic bytes, whatever its name)."""
    try:
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8-sig")
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        raise RNASeqError(f"{where} cannot be read as UTF-8 text ({exc})") from None


def _rows(text: str, where: str) -> list[list[str]]:
    """The table's rows (blank lines dropped), split at the separator its header uses
    more: a tab, else a comma (as ``rnaseq.read_sample_sheet`` decides). A quoted cell
    keeps a line break it holds, so that it can be refused (``_plain``), not run together."""
    first = next((line for line in text.splitlines() if line.strip()), None)
    if first is None:
        return []
    dialect = "excel-tab" if first.count("\t") > first.count(",") else "excel"
    try:
        rows = [[c.strip() for c in row]
                for row in csv.reader(io.StringIO(text), dialect=dialect)]
    except csv.Error as exc:                  # a quote never closed, past the cell limit
        raise RNASeqError(f"{where} cannot be read as a CSV or TSV table ({exc})") from None
    return [row for row in rows if any(row)]


def _plain(where: str, what: str, values: Sequence[str]) -> None:
    """Refuse a name or value the tab-separated outputs cannot hold as one cell: one with a
    tab or a line break (it would shift the columns of ``counts.tsv`` and the results)."""
    bad = next((v for v in values if any(ch in v for ch in "\t\r\n")), None)
    if bad is not None:
        raise RNASeqError(f"{where}: the {what} {bad!r} holds a tab or a line break, which a "
                          "tab-separated table cannot hold in one cell")


def _parse_counts(raw: bytes, where: str) -> CountsTable:
    rows = _rows(_text(raw, where), where)
    if not rows:
        raise RNASeqError(f"{where} is empty")
    header = rows[0]
    if not header or header[0] != "gene_id":
        raise RNASeqError(f"{where}: the first column must be gene_id (it is "
                          f"{header[0] if header else ''!r}); then an optional gene_name and "
                          "one column per sample")
    named = len(header) > 1 and header[1] == "gene_name"
    first = 2 if named else 1
    samples = header[first:]
    if len(samples) < 2:
        raise RNASeqError(f"{where}: {len(samples)} sample column(s); a test needs at least two "
                          "samples")
    if any(not s for s in samples):
        raise RNASeqError(f"{where}: a sample column has no name")
    _plain(where, "sample name", samples)
    repeated = sorted(s for s, n in Counter(samples).items() if n > 1)
    if repeated:
        raise RNASeqError(f"{where}: sample(s) named more than once: {', '.join(repeated)}")
    body = rows[1:]
    if not body:
        raise RNASeqError(f"{where} lists no genes")
    for n, row in enumerate(body, start=2):
        if len(row) != len(header):
            raise RNASeqError(f"{where} line {n}: {len(row)} cells where the header has "
                              f"{len(header)}")
        if not row[0]:
            raise RNASeqError(f"{where} line {n}: no gene_id")
    genes = [row[0] for row in body]
    _plain(where, "gene id", genes)
    if named:
        _plain(where, "gene name", [row[1] for row in body])
    repeated = sorted(g for g, n in Counter(genes).items() if n > 1)
    if repeated:
        raise RNASeqError(f"{where}: gene id(s) named more than once: "
                          f"{', '.join(repeated[:10])}")
    cells = [row[first:] for row in body]
    try:
        values = np.array(cells, dtype=float)
    except ValueError:
        # say which cell: the first that is not a number
        for n, row in enumerate(cells, start=2):
            for s, cell in zip(samples, row):
                try:
                    float(cell)
                except ValueError:
                    raise RNASeqError(f"{where} line {n}, sample {s}: {cell!r} is not a "
                                      "number") from None
        raise
    if not np.all(np.isfinite(values)):
        n, j = map(int, np.argwhere(~np.isfinite(values))[0])
        raise RNASeqError(f"{where} line {n + 2}, sample {samples[j]}: counts must be finite")
    if np.any(values < 0):
        n, j = map(int, np.argwhere(values < 0)[0])
        raise RNASeqError(f"{where} line {n + 2}, sample {samples[j]}: counts must be "
                          "non-negative")
    off = np.abs(values - np.round(values)) > 1e-8
    if np.any(off):
        n, j = map(int, np.argwhere(off)[0])
        raise RNASeqError(f"{where} line {n + 2}, sample {samples[j]}: counts must be "
                          "integers; round estimated counts first")
    if np.any(values >= 2.0 ** 53):
        n, j = map(int, np.argwhere(values >= 2.0 ** 53)[0])
        raise RNASeqError(f"{where} line {n + 2}, sample {samples[j]}: a count of 2^53 or "
                          "more cannot be held exactly")
    counts = np.round(values).astype(np.int64)
    empty = [s for s, total in zip(samples, counts.sum(axis=0)) if total == 0]
    if empty:
        raise RNASeqError(f"{where}: sample(s) {', '.join(empty)} have no counts at all")
    names = [row[1] or row[0] for row in body] if named else genes
    return CountsTable(genes=tuple(genes), gene_names=tuple(names), samples=tuple(samples),
                       counts=counts)


def read_counts_table(path: str | Path) -> CountsTable:
    """The count table at ``path``, checked: a ``gene_id`` column, an optional
    ``gene_name``, at least two uniquely named samples, unique gene ids, at least one gene,
    and every count a finite, non-negative integer (within 1e-8), no sample all zero."""
    path = Path(path)
    return _parse_counts(path.read_bytes(), str(path))


def _parse_samples(raw: bytes, where: str) -> list[tuple[str, dict[str, str]]]:
    rows = _rows(_text(raw, where), where)
    if not rows:
        raise RNASeqError(f"{where} is empty")
    header = rows[0]
    if "sample" not in header:
        raise RNASeqError(f"{where} has no 'sample' column")
    if any(c in header for c in _FASTQ):
        raise RNASeqError(f"{where} is a FASTQ sample sheet: the count-matrix mode takes a "
                          "sample table; run the full pipeline from FASTQ on the runner")
    if len(set(header)) != len(header) or any(not h for h in header):
        raise RNASeqError(f"{where}: every column needs a name of its own")
    _plain(where, "column", header)
    out: list[tuple[str, dict[str, str]]] = []
    for n, row in enumerate(rows[1:], start=2):
        if len(row) != len(header):
            raise RNASeqError(f"{where} line {n}: {len(row)} cells where the header has "
                              f"{len(header)}")
        _plain(f"{where} line {n}", "value", row)
        values = dict(zip(header, row))
        name = values.pop("sample")
        if not name:
            raise RNASeqError(f"{where} line {n}: no sample name")
        if any(name == other for other, _ in out):
            raise RNASeqError(f"{where} line {n}: sample {name!r} is listed twice")
        out.append((name, values))
    if not out:
        raise RNASeqError(f"{where} lists no samples")
    return out


def read_sample_table(path: str | Path) -> list[tuple[str, dict[str, str]]]:
    """(sample, its design variables) in table order: CSV or TSV with a ``sample``
    column, every other column a design variable, each sample once. A FASTQ sample sheet
    (``fastq_1``/``fastq_2``) is refused with the reason."""
    path = Path(path)
    return _parse_samples(path.read_bytes(), str(path))


def _samples_text(rows: Sequence[tuple[str, Mapping[str, str]]]) -> str:
    """The sample table in canonical form, for its digest: tab-separated, the rows in the
    order given (the count columns' order, as the run uses them), the variables in name
    order."""
    variables = sorted(rows[0][1])
    lines = ["\t".join(("sample", *variables))]
    lines += ["\t".join((name, *(attrs[v] for v in variables))) for name, attrs in rows]
    return "\n".join(lines) + "\n"


# ============================================================== configuration and the run

@dataclass(frozen=True)
class RNASeqCountsConfig:
    design: str = "~ condition"
    contrast: tuple[str, str, str] | None = None
    de_backend: str = "builtin"               # builtin | pydeseq2
    alpha: float = 0.05
    covariates: tuple[str, ...] = ()          # numeric design terms; the rest are factors
    threads: int = 1                          # PyDESeq2's worker processes

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RNASeqCountsRun:
    out_dir: Path
    samples: list[tuple[str, dict[str, str]]]
    contrast: tuple[str, str, str]
    table: CountsTable
    result: de_backends.DEResult
    vst: np.ndarray
    pca: dict[str, Any]
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = False
    limitations: list[str] = field(default_factory=list)
    reference_dataset: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {"input_kind": "counts", "samples": [name for name, _ in self.samples],
                "genes": len(self.table.genes), "contrast": list(self.contrast),
                "differential_expression": self.result.summary(),
                "warnings": list(self.warnings), "out_dir": str(self.out_dir),
                "complete": self.complete}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_rnaseq_counts(counts: str | Path, samples: str | Path, config: RNASeqCountsConfig,
                      out_dir: str | Path) -> RNASeqCountsRun:
    """Run every stage and write the report; returns what was computed. The sample table
    and the choices are checked before anything is written (a FASTQ sheet, a backend that
    is not installed, a contrast the samples do not have are refused there); the counts
    are read and checked as the first recorded stage. ``RNASeqCountsRun.complete`` says
    whether every stage succeeded and no check failed."""
    counts_path, samples_path = Path(counts), Path(samples)
    samples_raw = samples_path.read_bytes()
    rows = _parse_samples(samples_raw, str(samples_path))
    if not 0 < config.alpha < 1:
        raise RNASeqError(f"alpha is the FDR level, between 0 and 1 (not {config.alpha:g})")
    R._check_de_backend(config)
    contrast = R._contrast([attrs for _, attrs in rows], config.design, config.contrast)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = StageRecorder(out, PIPELINE, plan=STAGES)
    warnings: list[str] = []
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # the counts, read in full and matched to the sample table by name
    with rec.stage("validate_counts", engine=PIPELINE, version=_VERSION) as st:
        counts_raw = counts_path.read_bytes()
        table = _parse_counts(counts_raw, str(counts_path))
        try:
            de_backends.align_samples(table.samples, rows)
        except DESeqError as exc:
            raise RNASeqError(f"the count columns and the sample table: {exc}") from exc
        # from here on the samples are in the count columns' order, as the tables are
        by_name = dict(rows)
        ordered = [(name, by_name[name]) for name in table.samples]
        text = table.text()
        (out / "counts.tsv").write_text(text, encoding="utf-8")
        st.outputs = ["counts.tsv"]
        st.check = "PASS"
        st.reason = (f"{len(table.genes):,} genes x {len(table.samples)} samples of "
                     "non-negative integer counts, gene ids and sample names unique, no "
                     "sample empty; every count column has its row in the sample table")
    inputs = {"counts": {"path": str(counts_path.resolve()), "sha256": _sha256(counts_raw),
                         "bytes": len(counts_raw)},
              "samples": {"path": str(samples_path.resolve()), "sha256": _sha256(samples_raw),
                          "bytes": len(samples_raw)}}
    samples_text = _samples_text(ordered)
    validation = {"counts": {"content_sha256": _sha256(text.encode("utf-8")),
                             "genes": len(table.genes), "samples": list(table.samples)},
                  "samples": {"content_sha256": _sha256(samples_text.encode("utf-8")),
                              "variables": list(rows[0][1])}}
    reference_dataset = match_reference(PIPELINE, {
        k: v["content_sha256"] for k, v in validation.items()})

    result = R._test(rec, out, table.counts, table.genes, table.gene_names, table.samples,
                     {name: dict(attrs) for name, attrs in ordered}, config, contrast,
                     warnings)
    vst, pca, dist = R._explore(rec, out, result, config.de_backend)

    run = RNASeqCountsRun(out_dir=out, samples=ordered, contrast=contrast, table=table,
                          result=result, vst=vst, pca=pca, warnings=warnings)
    run.reference_dataset = reference_dataset
    _write_outputs(run, config, dist, started, rec, inputs, validation)
    return run


# ============================================================== the report and the record

def _limitations(run: RNASeqCountsRun, rec: StageRecorder) -> list[str]:
    lines = list(LIMITATIONS)
    if run.reference_dataset:
        ref = run.reference_dataset
        lines.append(f"The input is the reference dataset {ref['id']} (version "
                     f"{ref['version']}): {ref['label']}. {ref['statement']}")
    lines += stage_limitations(rec.stages)
    return lines


def _markdown(run: RNASeqCountsRun, config: RNASeqCountsConfig) -> str:
    res = run.result
    table = run.table
    num, den = run.contrast[1:]
    lines = [f"# RNA-seq differential expression: {num} vs {den}", ""]
    lines += ["## Result", "", *R._result_lines(res, config.design, run.contrast),
              f"- Input: a count matrix of {len(table.genes):,} genes x "
              f"{len(table.samples)} samples, analysed as given (no read was processed "
              f"here); differential expression: **{R._de_name(res)}**.", ""]
    if run.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in run.warnings] + [""]
    variables = list(run.samples[0][1])
    totals = dict(zip(table.samples, table.counts.sum(axis=0).tolist()))
    factors = dict(zip(res.samples, map(float, res.size_factors)))
    lines += ["## Samples", "",
              "| sample | " + " | ".join(variables) + " | total counts | size factor |",
              "|---" * (len(variables) + 3) + "|"]
    for name, attrs in run.samples:
        lines.append(f"| {name} | " + " | ".join(attrs[v] for v in variables) +
                     f" | {totals[name]:,} | {R._fmt(factors[name])} |")
    lines.append("")
    lines += R._top_genes(res, dict(zip(table.genes, table.gene_names)))
    lines += ["", "The full table is `deseq2_results.tsv`; the counts as tested are "
              "`counts.tsv`; the variance-stabilised matrix is `vst.tsv`. The test's own "
              "estimates (dispersions, Cook's flags, convergence) are in "
              "`de_diagnostics.tsv`.", ""]
    lines += ["## Figures", ""]
    for name in ("pca", "sample_distances", "ma", "volcano", "p_values", "dispersion"):
        lines.append(f"![{name}](plots/{name}.svg)")
    lines += ["", "## Methods", "",
              f"- **Input**: a gene x sample table of {len(table.genes):,} genes and "
              f"{len(table.samples)} samples, checked to hold non-negative integer counts "
              "with unique gene ids and sample names and no empty sample, each count "
              "column matched by name to its row of the sample table. The counts were "
              "analysed as given: read QC, trimming, quantification and gene totals are "
              "not part of this run.",
              *R._de_methods(res, run.pca), ""]
    lines += [f"- {n}" for n in res.summary()["notes"]]
    lines += ["", "## What this result is", "",
              "A statistical association within this experiment: these genes differ between "
              f"the {num} and {den} samples analysed. It does not establish a mechanism, a "
              "cause or any clinical effect, and it holds for these samples, these counts "
              "and these settings. Fold changes are maximum-likelihood estimates without "
              "shrinkage, so low-count genes can show large, imprecise changes.", "",
              "## Reproducing it", "",
              "`run.json` records the digests of both input tables and of every output, the "
              "configuration, and each stage's status, engine, device and time; "
              "`limitations.md` lists this run's limitations. `bioagent rnaseq --verify <out>` "
              "re-checks the digests, that every stage succeeded and that the tables agree "
              "with the record.", ""]
    return "\n".join(lines)


def _write_outputs(run: RNASeqCountsRun, config: RNASeqCountsConfig, dist: np.ndarray,
                   started: str, rec: StageRecorder, inputs: Mapping[str, Any],
                   validation: Mapping[str, Any]) -> None:
    out = run.out_dir
    with rec.stage("report", engine=PIPELINE, version=_VERSION) as st:
        plots = R._de_plots(run.result, run.pca, run.contrast, dict(run.samples), dist)
        pdir = out / "plots"
        pdir.mkdir(exist_ok=True)
        for name, svg in plots.items():
            (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
        md = _markdown(run, config)
        (out / "report.md").write_text(md, encoding="utf-8")
        (out / "report.html").write_text(R._html(md, plots), encoding="utf-8")
        run.limitations = write_limitations(out, _limitations(run, rec))
        st.outputs = ["report.md", "report.html", LIMITATIONS_FILE,
                      *(f"plots/{name}.svg" for name in plots)]
    manifest: dict[str, Any] = {}
    with rec.stage("verify", engine=PIPELINE, version=_VERSION) as st:
        outputs = {p.relative_to(out).as_posix(): R._sha256(p) for p in sorted(out.rglob("*"))
                   if p.is_file() and p.name not in _UNLISTED}
        manifest.update({
            "pipeline": PIPELINE, "input_kind": "counts", "code_digest": R._code_digest(),
            "started": started,
            "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "numpy": np.__version__,
            "scipy": scipy.__version__, "config": config.as_dict(),
            "differential_expression": run.result.record(), "contrast": list(run.contrast),
            "samples": [{"name": name, "attributes": dict(attrs)}
                        for name, attrs in run.samples],
            "inputs": {k: dict(v) for k, v in inputs.items()}, "outputs": outputs,
            "summary": run.summary(), "warnings": run.warnings})
        found = _content_problems(out, manifest) + output_problems(rec.stages, outputs, out)
        st.check = "FAIL" if found else "PASS"
        st.reason = ("; ".join(found)[:600] if found else
                     f"{len(outputs)} outputs digested; tables agree with the run record")
    run.stages = rec.as_list()
    run.complete = rec.complete
    manifest["summary"] = run.summary()
    manifest.update({"stages": run.stages, "complete": run.complete,
                     "limitations": run.limitations, "validation": dict(validation),
                     "reference_dataset": run.reference_dataset})
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")
    rec.finish()


def _content_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    """The tables against the run record: the canonical counts, the result contract, the
    test's own estimates and the VST, each one row per gene, one column per sample."""
    de = (manifest.get("summary") or {}).get("differential_expression") or {}
    samples = [str(s.get("name")) for s in manifest.get("samples") or ()]
    contract = [attr for _, attr, _ in de_backends.CONTRACT]
    expect = {"counts.tsv": ["gene_id", "gene_name", *samples],
              "deseq2_results.tsv": ["gene_id", "gene_name", *contract],
              "de_diagnostics.tsv": ["gene_id", *de_backends.GENE_DIAGNOSTICS],
              "vst.tsv": ["gene_id", *samples]}
    return R._table_problems(out, expect, de.get("genes"))


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-hash both input tables at their recorded paths and every recorded output; check
    that the run is complete (``runrecord.record_problems``) and that its tables agree with
    the record. Returns (no problem, the problems)."""
    out = Path(out_dir)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    problems = []
    for name, spec in (manifest.get("inputs") or {}).items():
        p = Path(str(spec.get("path")))
        if not p.is_file():
            problems.append(f"input {name} ({p}) is missing")
        elif R._sha256(p) != spec.get("sha256"):
            problems.append(f"input {name} ({p}) has changed")
    if set(manifest.get("inputs") or {}) != {"counts", "samples"}:
        problems.append("run.json does not record both input tables (counts, samples)")
    for rel, digest in (manifest.get("outputs") or {}).items():
        p = out / rel
        if not p.is_file():
            problems.append(f"output {rel} is missing")
        elif R._sha256(p) != digest:
            problems.append(f"output {rel} has changed")
    problems += record_problems(manifest, out)
    problems += _content_problems(out, manifest)
    return not problems, problems
