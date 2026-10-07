"""The RNA-seq pipeline: from a sample sheet to a differential-expression report.

    from bioagent.omics.rnaseq import RNASeqConfig, run_rnaseq
    run = run_rnaseq("samples.csv", RNASeqConfig(
        transcripts="transcripts.fa", annotation="genes.gtf", design="~ condition",
        contrast=("condition", "treated", "control")), "results/")

The sample sheet follows nf-core/rnaseq: ``sample,fastq_1,fastq_2`` and then the design
variables (``condition``, ``batch``, ...). Rows sharing a sample name are lanes of one
library and are merged. Paths are relative to the sheet.

Steps, each recorded with its tool, version, parameters and the digests of what it read
and wrote:

1. **QC** of every FASTQ file (``fastq.quality_control``; FastQC's modules and limits).
2. **Trimming**: fastp when installed, otherwise the built-in cutadapt-style trimmer.
3. **Quantification**: salmon, then kallisto, then the built-in pseudoaligner, whichever
   is installed first (``engine="auto"``); or HISAT2 alignment to a genome with
   featureCounts (``engine="hisat2"``).
4. **Gene totals** with tximport's rules (transcript engines).
5. **Differential expression** with the DESeq2 method (``deseq.run_deseq``).
6. **Exploration**: the variance-stabilised matrix, PCA of the 500 most variable genes and
   sample-to-sample distances, as DESeq2's vignette does.
7. **Report**: ``report.md`` and ``report.html`` with tables and SVG figures, and
   ``run.json`` holding every parameter, version and digest; ``verify_run`` re-checks the
   digests.

A result is a statistical association within the experiment analysed: which genes
differ between the groups of these samples. It says nothing about why, and nothing
clinical.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import html
import json
import math
import platform
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from . import backends, deseq, fastq, quant, svgplot

__all__ = ["RNASeqConfig", "RNASeqError", "RNASeqRun", "Sample", "read_sample_sheet",
           "run_rnaseq", "verify_run", "ENGINES"]

ENGINES = ("auto", "builtin", "salmon", "kallisto", "hisat2")
TRIMMERS = ("auto", "builtin", "fastp", "none")
MIN_MAPPING = 0.5            # below this a library is flagged
TOP_VARIABLE = 500           # genes for PCA, as DESeq2's plotPCA
RESERVED = {"sample", "fastq_1", "fastq_2", "strandedness"}


class RNASeqError(ValueError):
    """A sample sheet, reference or setting the pipeline cannot run with."""


# ============================================================== the sample sheet

@dataclass(frozen=True)
class Sample:
    name: str
    fastq_1: tuple[Path, ...]                 # one per lane
    fastq_2: tuple[Path, ...]                 # empty for single-end
    attributes: Mapping[str, str]

    @property
    def paired(self) -> bool:
        return bool(self.fastq_2)


def read_sample_sheet(path: str | Path) -> list[Sample]:
    """Samples in sheet order; lanes merged; every file checked to exist."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    dialect = "excel-tab" if text.splitlines()[0].count("\t") > text.splitlines()[0].count(",") \
        else "excel"
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    if not rows:
        raise RNASeqError(f"{path} lists no samples")
    header = [h.strip() for h in rows[0].keys()]
    for need in ("sample", "fastq_1"):
        if need not in header:
            raise RNASeqError(f"{path} has no '{need}' column (nf-core sample sheet format)")
    lanes: dict[str, list[dict[str, str]]] = {}
    for n, row in enumerate(rows, start=2):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k is not None}
        name = row.get("sample", "")
        if not name:
            raise RNASeqError(f"{path} line {n}: no sample name")
        if any(ch in name for ch in "/\\ \t"):
            raise RNASeqError(f"{path} line {n}: sample name {name!r} has a space or slash")
        lanes.setdefault(name, []).append(row)
    samples = []
    base = path.parent
    for name, group in lanes.items():
        attrs = {k: v for k, v in group[0].items() if k not in RESERVED}
        for row in group[1:]:
            other = {k: v for k, v in row.items() if k not in RESERVED}
            if other != attrs:
                raise RNASeqError(f"sample {name}: lanes disagree on the design variables")
        f1, f2 = [], []
        for row in group:
            for key, bucket in (("fastq_1", f1), ("fastq_2", f2)):
                value = row.get(key, "")
                if not value:
                    continue
                p = Path(value)
                p = p if p.is_absolute() else base / p
                if not p.is_file():
                    raise RNASeqError(f"sample {name}: {p} does not exist")
                bucket.append(p)
        if f2 and len(f2) != len(f1):
            raise RNASeqError(f"sample {name}: every lane needs both mates or none")
        samples.append(Sample(name=name, fastq_1=tuple(f1), fastq_2=tuple(f2),
                              attributes=attrs))
    if len({s.paired for s in samples}) > 1:
        raise RNASeqError("the sheet mixes single-end and paired-end samples")
    return samples


# ============================================================== configuration

@dataclass(frozen=True)
class RNASeqConfig:
    transcripts: str | Path | None = None     # transcriptome FASTA (transcript engines)
    annotation: str | Path | None = None      # GTF, or a transcript-to-gene table
    genome: str | Path | None = None          # genome FASTA (engine "hisat2")
    design: str = "~ condition"
    contrast: tuple[str, str, str] | None = None
    engine: str = "auto"
    trimmer: str = "auto"
    alpha: float = 0.05
    k: int = 31
    fragment_mean: float = 200.0              # single-end reads only
    fragment_sd: float = 30.0
    covariates: tuple[str, ...] = ()          # numeric design terms; the rest are factors
    counts_from_abundance: str = "lengthScaledTPM"
    strand: int = 0                           # featureCounts -s
    quality: int = 20
    min_length: int = 20
    qc_reads: int = 200_000                   # QC reads at most this many per file
    threads: int = 2

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _code_digest() -> str:
    """sha256 over this package's source, so a report names the code that made it."""
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


def _choose_engine(config: RNASeqConfig) -> str:
    if config.engine not in ENGINES:
        raise RNASeqError(f"engine is one of {', '.join(ENGINES)}")
    if config.engine == "hisat2":
        if not (config.genome and config.annotation):
            raise RNASeqError("engine 'hisat2' needs a genome FASTA and a GTF annotation")
        for tool in ("hisat2", "hisat2-build", "samtools", "featureCounts"):
            if backends.find_tool(tool) is None:
                raise RNASeqError(f"engine 'hisat2' needs {tool}, which is not installed")
        return "hisat2"
    if not config.transcripts:
        raise RNASeqError("a transcriptome FASTA is needed (or engine 'hisat2' with a genome)")
    if config.engine in ("salmon", "kallisto") and backends.find_tool(config.engine) is None:
        raise RNASeqError(f"engine {config.engine!r} is not installed")
    if config.engine != "auto":
        return config.engine
    for tool in ("salmon", "kallisto"):
        if backends.find_tool(tool) is not None:
            return tool
    return "builtin"


def _choose_trimmer(config: RNASeqConfig) -> str:
    if config.trimmer not in TRIMMERS:
        raise RNASeqError(f"trimmer is one of {', '.join(TRIMMERS)}")
    if config.trimmer == "fastp" and backends.find_tool("fastp") is None:
        raise RNASeqError("fastp is not installed")
    if config.trimmer == "auto":
        return "fastp" if backends.find_tool("fastp") is not None else "builtin"
    return config.trimmer


def _infer_contrast(samples: Sequence[Sample], config: RNASeqConfig) -> tuple[str, str, str]:
    if config.contrast is not None:
        factor, num, den = config.contrast
        levels = {s.attributes.get(factor) for s in samples}
        if factor not in samples[0].attributes:
            raise RNASeqError(f"the sample sheet has no column {factor!r}")
        for level in (num, den):
            if level not in levels:
                raise RNASeqError(f"{factor} has no level {level!r} (levels: "
                                  f"{', '.join(sorted(map(str, levels)))})")
        return factor, num, den
    terms = [t.strip() for t in config.design.replace("~", "").split("+") if t.strip()]
    if not terms:
        raise RNASeqError("the design names no variable")
    factor = terms[-1]
    if factor not in samples[0].attributes:
        raise RNASeqError(f"the sample sheet has no column {factor!r}")
    levels = sorted({s.attributes[factor] for s in samples})
    if len(levels) != 2:
        raise RNASeqError(f"{factor} has {len(levels)} levels; name the contrast to test")
    return factor, levels[1], levels[0]


# ============================================================== the run

@dataclass
class RNASeqRun:
    out_dir: Path
    samples: list[Sample]
    engine: str
    trimmer: str
    contrast: tuple[str, str, str]
    qc: dict[str, dict[str, Any]]
    trimming: dict[str, dict[str, Any]]
    quantification: dict[str, dict[str, Any]]
    genes: quant.GeneTable
    result: deseq.DESeqResult
    vst: np.ndarray
    pca: dict[str, Any]
    warnings: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"samples": [s.name for s in self.samples], "engine": self.engine,
                "trimmer": self.trimmer, "contrast": list(self.contrast),
                "differential_expression": self.result.summary(),
                "warnings": list(self.warnings), "out_dir": str(self.out_dir)}


def _concat(paths: Sequence[Path], out: Path) -> Path:
    """Lanes into one gzipped file (gzip members concatenate into a valid stream)."""
    with out.open("wb") as dst:
        for p in paths:
            with p.open("rb") as src:
                magic = src.read(2)
                src.seek(0)
                if magic == b"\x1f\x8b":
                    shutil.copyfileobj(src, dst)
                else:
                    with gzip.GzipFile(fileobj=dst, mode="wb", mtime=0) as gz:
                        shutil.copyfileobj(src, gz)
    return out


def _qc(path: Path, config: RNASeqConfig) -> dict[str, Any]:
    report = fastq.quality_control(path, max_reads=config.qc_reads)
    d = report.as_dict()
    d["file"] = path.name
    return d


def run_rnaseq(sheet: str | Path, config: RNASeqConfig, out_dir: str | Path) -> RNASeqRun:
    """Run every step and write the report; returns what was computed."""
    samples = read_sample_sheet(sheet)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = _choose_engine(config)
    trimmer = _choose_trimmer(config)
    contrast = _infer_contrast(samples, config)
    warnings: list[str] = []
    steps: list[dict[str, Any]] = []
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1-2: QC and trimming, per sample (lanes merged first)
    qc: dict[str, dict[str, Any]] = {}
    qc_failed: dict[str, list[str]] = {}
    trimming: dict[str, dict[str, Any]] = {}
    reads_for: dict[str, tuple[Path, Path | None]] = {}
    merged_dir = out / "merged"
    for s in samples:
        r1 = s.fastq_1[0]
        r2 = s.fastq_2[0] if s.paired else None
        if len(s.fastq_1) > 1:
            merged_dir.mkdir(exist_ok=True)
            r1 = _concat(s.fastq_1, merged_dir / f"{s.name}_R1.fastq.gz")
            r2 = _concat(s.fastq_2, merged_dir / f"{s.name}_R2.fastq.gz") if s.paired else None
        qc[s.name] = {"R1": _qc(r1, config)}
        if r2 is not None:
            qc[s.name]["R2"] = _qc(r2, config)
        for mate, report in qc[s.name].items():
            for module, verdict in report["flags"].items():
                if verdict == "fail":
                    qc_failed.setdefault(module, []).append(f"{s.name} {mate}")
        if trimmer == "none":
            reads_for[s.name] = (r1, r2)
            continue
        tdir = out / "trimmed"
        tdir.mkdir(exist_ok=True)
        o1 = tdir / f"{s.name}_R1.trimmed.fastq.gz"
        o2 = tdir / f"{s.name}_R2.trimmed.fastq.gz" if r2 is not None else None
        if trimmer == "fastp":
            summary, step = backends.fastp(r1, o1, read2=r2, out2=o2, workdir=tdir / s.name,
                                           quality=config.quality,
                                           min_length=config.min_length,
                                           threads=config.threads)
            steps.append(step.as_dict())
        else:
            settings = fastq.TrimSettings(quality=config.quality,
                                          min_length=config.min_length)
            rep = fastq.trim_fastq(r1, o1, read2=r2, out2=o2, settings=settings)
            summary = rep.as_dict()
            summary["engine"] = "builtin (cutadapt-style)"
        trimming[s.name] = summary
        if summary["reads_in"] and summary["reads_out"] / summary["reads_in"] < 0.5:
            warnings.append(f"{s.name}: trimming kept {summary['reads_out']} of "
                            f"{summary['reads_in']} reads")
        reads_for[s.name] = (o1, o2)

    files = sum(len(v) for v in qc.values())
    for module, where in sorted(qc_failed.items()):
        warnings.append(f"QC module {module} failed in {len(where)} of {files} files: "
                        + ", ".join(where))

    # 3-4: quantification and gene totals
    qdir = out / "quant"
    qdir.mkdir(exist_ok=True)
    quantification: dict[str, dict[str, Any]] = {}
    if engine == "hisat2":
        idx = out / "index" / "genome"
        steps.append(backends.hisat2_index(config.genome, idx, threads=config.threads)
                     .as_dict())
        bams = []
        for s in samples:
            r1, r2 = reads_for[s.name]
            stats, st = backends.hisat2_align(idx, r1, qdir / f"{s.name}.bam", read2=r2,
                                              threads=config.threads)
            steps += [x.as_dict() for x in st]
            quantification[s.name] = {"method": "HISAT2 + featureCounts",
                                      "mapping_rate": stats["overall_alignment_rate"]}
            bams.append(qdir / f"{s.name}.bam")
        matrix, st = backends.featurecounts(bams, [s.name for s in samples],
                                            config.annotation, qdir / "featureCounts.txt",
                                            paired=samples[0].paired, strand=config.strand,
                                            threads=config.threads)
        steps.append(st.as_dict())
        names = _gene_names(config.annotation, matrix.genes)
        lengths = np.repeat(matrix.lengths[:, None], len(samples), axis=1)
        rate = matrix.counts / matrix.lengths[:, None]
        tpm = rate / np.where(rate.sum(axis=0) > 0, rate.sum(axis=0), 1.0) * 1e6
        genes = quant.GeneTable(genes=matrix.genes, names=names,
                                samples=tuple(s.name for s in samples), counts=matrix.counts,
                                tpm=tpm, length=lengths, counts_from_abundance="no",
                                notes=["gene counts from featureCounts (-t exon -g gene_id)"])
        for s in samples:
            assigned = matrix.assigned.get(s.name, {})
            total = sum(assigned.values())
            quantification[s.name]["assigned_fraction"] = (
                round(assigned.get("Assigned", 0) / total, 6) if total else 0.0)
    else:
        results = []
        if engine == "builtin":
            index_path = out / "index" / "kmer_index.npz"
            index = quant.KmerIndex.from_fasta(config.transcripts, k=config.k)
            index.save(index_path)
            steps.append({"tool": "bioagent.omics.quant", "version": _code_digest()[:12],
                          "command": ["KmerIndex.from_fasta", str(config.transcripts),
                                      f"k={config.k}"],
                          "inputs": {str(config.transcripts): _sha256(Path(config.transcripts))},
                          "outputs": {str(index_path): _sha256(index_path)}})
        elif engine == "salmon":
            index_dir = out / "index" / "salmon"
            steps.append(backends.salmon_index(config.transcripts, index_dir, k=config.k,
                                               threads=config.threads).as_dict())
        else:
            index_file = out / "index" / "kallisto.idx"
            steps.append(backends.kallisto_index(config.transcripts, index_file,
                                                 k=config.k).as_dict())
        for s in samples:
            r1, r2 = reads_for[s.name]
            sdir = qdir / s.name
            if engine == "builtin":
                res = quant.quantify(index, r1, r2, fragment_mean=config.fragment_mean,
                                     fragment_sd=config.fragment_sd)
                quant.write_abundance(res, sdir / "abundance.tsv")
            elif engine == "salmon":
                res, st = backends.salmon_quant(index_dir, r1, sdir, read2=r2,
                                                threads=config.threads)
                steps.append(st.as_dict())
            else:
                res, st = backends.kallisto_quant(index_file, r1, sdir, read2=r2,
                                                  fragment_mean=config.fragment_mean,
                                                  fragment_sd=config.fragment_sd,
                                                  threads=config.threads)
                steps.append(st.as_dict())
            results.append(res)
            quantification[s.name] = dict(res.run)
            quantification[s.name]["mapping_rate"] = res.run.get("pseudoaligned_fraction")
        tx_ids = results[0].transcripts
        if config.annotation:
            tx2gene = quant.read_tx2gene(config.annotation)
        else:
            tx2gene = {t: (t, t) for t in tx_ids}
            warnings.append("no annotation: each transcript is analysed as its own gene")
        genes = quant.summarize_to_gene(results, [s.name for s in samples], tx2gene,
                                        counts_from_abundance=config.counts_from_abundance)
    for s in samples:
        rate = quantification[s.name].get("mapping_rate")
        if rate is not None and rate < MIN_MAPPING:
            warnings.append(f"{s.name}: only {rate:.1%} of reads mapped; check the "
                            "reference and the organism")

    # 5: differential expression
    counts = np.round(genes.counts)
    design_rows = [dict(s.attributes) for s in samples]
    result = deseq.run_deseq(counts, list(genes.genes), design_rows, design=config.design,
                             contrast=contrast, alpha=config.alpha,
                             sample_names=[s.name for s in samples],
                             covariates=config.covariates)
    factor = contrast[0]
    per_level: dict[str, int] = {}
    for s in samples:
        per_level[s.attributes[factor]] = per_level.get(s.attributes[factor], 0) + 1
    if min(per_level.values()) < 3:
        warnings.append("fewer than three replicates in a group: Cook's distance outlier "
                        "filtering is not applied there, and estimates are less stable")

    # 6: exploration
    vst = deseq.vst(result.normalized, result.trend)
    pca = _pca(vst, [s.name for s in samples])
    dist = _distances(vst)

    run = RNASeqRun(out_dir=out, samples=samples, engine=engine, trimmer=trimmer,
                    contrast=contrast, qc=qc, trimming=trimming,
                    quantification=quantification, genes=genes, result=result, vst=vst,
                    pca=pca, warnings=warnings)
    _write_outputs(run, config, dist, steps, started)
    return run


def _gene_names(annotation: str | Path, genes: Sequence[str]) -> tuple[str, ...]:
    try:
        mapping = quant.read_tx2gene(annotation)
    except quant.QuantError:
        return tuple(genes)
    names = {g: n for g, n in mapping.values()}
    return tuple(names.get(g, g) for g in genes)


def _pca(vst: np.ndarray, samples: Sequence[str]) -> dict[str, Any]:
    """DESeq2's plotPCA: the 500 most variable genes, centred, not scaled."""
    var = vst.var(axis=1)
    top = np.argsort(-var, kind="mergesort")[:min(TOP_VARIABLE, len(var))]
    x = vst[top].T
    x = x - x.mean(axis=0)
    u, sv, _ = np.linalg.svd(x, full_matrices=False)
    scores = u * sv
    explained = sv ** 2 / max(float((sv ** 2).sum()), 1e-300)
    # fix the sign so the largest loading is positive (stable across platforms)
    for j in range(scores.shape[1]):
        if scores[np.argmax(np.abs(scores[:, j])), j] < 0:
            scores[:, j] = -scores[:, j]
    pcs = min(2, scores.shape[1])
    return {"genes": int(len(top)), "samples": list(samples),
            "pc1": [float(v) for v in scores[:, 0]],
            "pc2": [float(v) for v in scores[:, 1]] if pcs > 1 else [0.0] * len(samples),
            "explained": [float(v) for v in explained[:pcs]]}


def _distances(vst: np.ndarray) -> np.ndarray:
    x = vst.T
    sq = (x ** 2).sum(axis=1)
    d2 = np.maximum(sq[:, None] + sq[None, :] - 2 * x @ x.T, 0.0)
    return np.sqrt(d2)


def _order(dist: np.ndarray) -> list[int]:
    """Leaf order of complete-linkage clustering (pheatmap's default)."""
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform
    n = dist.shape[0]
    if n < 3:
        return list(range(n))
    return [int(i) for i in leaves_list(linkage(squareform(dist, checks=False),
                                                method="complete"))]


# ============================================================== writing the report

def _fmt(v: Any, digits: int = 3) -> str:
    if v is None:
        return "NA"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "NA"
        if v != 0 and (abs(v) < 1e-3 or abs(v) >= 1e5):
            return f"{v:.{digits - 1}e}"
        return f"{v:.{digits}g}"
    return str(v)


def _write_tsv(path: Path, header: Sequence[str], rows: Sequence[Sequence[Any]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header) + "\n")
        for row in rows:
            fh.write("\t".join("NA" if v is None else (f"{v:.6g}" if isinstance(v, float)
                                                       else str(v)) for v in row) + "\n")
    return path


def _write_outputs(run: RNASeqRun, config: RNASeqConfig, dist: np.ndarray,
                   steps: list[dict[str, Any]], started: str) -> None:
    out = run.out_dir
    res = run.result
    genes = run.genes
    names = dict(zip(genes.genes, genes.names))
    run.genes.write(out / "counts.tsv", what="counts")
    run.genes.write(out / "tpm.tsv", what="tpm")
    table = res.table()
    _write_tsv(out / "deseq2_results.tsv",
               ["gene_id", "gene_name", "base_mean", "log2_fold_change", "lfc_se", "stat",
                "p_value", "p_adjusted", "dispersion", "cooks_outlier"],
               [[r["gene"], names.get(r["gene"], r["gene"]), r["base_mean"],
                 r["log2_fold_change"], r["lfc_se"], r["stat"], r["p_value"],
                 r["p_adjusted"], r["dispersion"], r["cooks_outlier"]] for r in table])
    samples = [s.name for s in run.samples]
    _write_tsv(out / "vst.tsv", ["gene_id", *samples],
               [[g, *map(float, run.vst[i])] for i, g in enumerate(genes.genes)])
    plots = _plots(run, dist)
    pdir = out / "plots"
    pdir.mkdir(exist_ok=True)
    for name, svg in plots.items():
        (pdir / f"{name}.svg").write_text(svg, encoding="utf-8")
    md = _markdown(run, config, steps)
    (out / "report.md").write_text(md, encoding="utf-8")
    (out / "report.html").write_text(_html(md, plots), encoding="utf-8")
    inputs = {}
    for s in run.samples:
        for p in (*s.fastq_1, *s.fastq_2):
            inputs[str(p)] = _sha256(p)
    for ref in (config.transcripts, config.annotation, config.genome):
        if ref:
            inputs[str(ref)] = _sha256(Path(ref))
    outputs = {}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "run.json" and "trimmed" not in p.parts \
                and "merged" not in p.parts and "index" not in p.parts:
            outputs[str(p.relative_to(out))] = _sha256(p)
    manifest = {
        "pipeline": "bioagent.omics.rnaseq", "code_digest": _code_digest(),
        "started": started,
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(), "numpy": np.__version__,
        "config": config.as_dict(), "engine": run.engine, "trimmer": run.trimmer,
        "tools": backends.available_tools(),
        "contrast": list(run.contrast), "samples": [
            {"name": s.name, "fastq_1": [str(p) for p in s.fastq_1],
             "fastq_2": [str(p) for p in s.fastq_2], "attributes": dict(s.attributes)}
            for s in run.samples],
        "steps": steps, "inputs": inputs, "outputs": outputs,
        "summary": run.summary(), "warnings": run.warnings}
    run.manifest = manifest
    (out / "run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                             default=str), encoding="utf-8")


def verify_run(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Re-hash every recorded input and output; (all match, the mismatches)."""
    out = Path(out_dir)
    manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
    problems = []
    for path, digest in manifest["inputs"].items():
        p = Path(path)
        if not p.is_file():
            problems.append(f"input {path} is missing")
        elif _sha256(p) != digest:
            problems.append(f"input {path} has changed")
    for rel, digest in manifest["outputs"].items():
        p = out / rel
        if not p.is_file():
            problems.append(f"output {rel} is missing")
        elif _sha256(p) != digest:
            problems.append(f"output {rel} has changed")
    return not problems, problems


def _plots(run: RNASeqRun, dist: np.ndarray) -> dict[str, str]:
    res = run.result
    plots: dict[str, str] = {}
    first = next(iter(run.qc))
    q = run.qc[first]["R1"]["per_position_quality"]
    plots["quality_" + first] = svgplot.lines(
        [p["position"] for p in q], {"median": [p["median"] for p in q]},
        band=([p["lower_quartile"] for p in q], [p["upper_quartile"] for p in q]),
        title=f"Per-base quality, {first} read 1 (median and quartiles)",
        xlabel="position in read", ylabel="Phred quality", hlines=(20, 28), ylim=(0, 42))
    names = [s.name for s in run.samples]
    rates = [float(run.quantification[n].get("mapping_rate") or 0.0) for n in names]
    plots["mapping"] = svgplot.bars(names, rates, title="Fraction of reads assigned",
                                    ylabel="fraction", hline=MIN_MAPPING)
    factor = run.contrast[0]
    groups = [s.attributes[factor] for s in run.samples]
    ev = run.pca["explained"] + [0.0, 0.0]
    plots["pca"] = svgplot.scatter(
        run.pca["pc1"], run.pca["pc2"], groups=groups, labels=names, radius=5,
        title=f"PCA of the {run.pca['genes']} most variable genes (VST)",
        xlabel=f"PC1 ({ev[0]:.0%} of variance)", ylabel=f"PC2 ({ev[1]:.0%} of variance)")
    order = _order(dist)
    plots["sample_distances"] = svgplot.heatmap(
        dist[np.ix_(order, order)], [names[i] for i in order], [names[i] for i in order],
        title="Sample-to-sample distances (Euclidean, VST)", scale_label="distance",
        reverse=True)
    ok = np.isfinite(res.base_mean) & (res.base_mean > 0)
    sig = np.isfinite(res.p_adjusted) & (res.p_adjusted < res.alpha)
    status = np.where(sig & (res.log2_fold_change > 0), "up",
                      np.where(sig, "down", "not significant"))
    colours = {"not significant": svgplot.GREY, "up": svgplot.PALETTE[1],
               "down": svgplot.PALETTE[0]}
    plots["ma"] = svgplot.scatter(
        np.log10(res.base_mean[ok]), res.log2_fold_change[ok], groups=list(status[ok]),
        colours=colours, hlines=(0,), radius=2,
        title=f"MA plot: {run.contrast[1]} vs {run.contrast[2]} (FDR < {res.alpha:g})",
        xlabel="log10 mean of normalised counts", ylabel="log2 fold change")
    tested = np.isfinite(res.p_value)
    plots["volcano"] = svgplot.scatter(
        res.log2_fold_change[tested], -np.log10(np.maximum(res.p_value[tested], 1e-300)),
        groups=list(status[tested]), colours=colours, radius=2,
        title="Volcano plot", xlabel="log2 fold change", ylabel="-log10 p-value")
    plots["p_values"] = svgplot.histogram(res.p_value[tested], bins=40, lo=0.0, hi=1.0,
                                          title="Distribution of p-values",
                                          xlabel="p-value")
    disp = np.isfinite(res.dispersion_gene) & ok
    x = np.log10(res.base_mean[disp])
    final = np.log10(res.dispersion[disp])
    trend = np.log10(res.dispersion_trend[disp])
    # gene-wise estimates far below the rest (at the floor) are drawn at the axis foot
    floor = (min(final.min(), trend.min()) - 1.0) if len(final) else -8.0
    gene = np.maximum(np.log10(res.dispersion_gene[disp]), floor)
    plots["dispersion"] = svgplot.scatter(
        np.concatenate([x, x, x]), np.concatenate([gene, final, trend]),
        groups=(["gene-wise"] * len(x) + ["final"] * len(x) + ["trend"] * len(x)),
        colours={"gene-wise": svgplot.GREY, "final": svgplot.PALETTE[0],
                 "trend": svgplot.PALETTE[1]}, radius=1.6,
        title="Dispersion estimates (gene-wise below the axis drawn at its foot)",
        xlabel="log10 mean of normalised counts", ylabel="log10 dispersion")
    return plots


def _markdown(run: RNASeqRun, config: RNASeqConfig, steps: list[dict[str, Any]]) -> str:
    res = run.result
    summ = res.summary()
    names = dict(zip(run.genes.genes, run.genes.names))
    factor, num, den = run.contrast
    lines = [f"# RNA-seq differential expression: {num} vs {den}", ""]
    lines += ["## Result", "",
              f"- Design `{config.design}`, contrast `{factor}`: **{num}** vs **{den}** "
              f"(log2 fold change > 0 means higher in {num}).",
              f"- {summ['tested']} genes tested; **{summ['significant']}** differ at FDR "
              f"< {res.alpha:g} ({summ['up']} higher, {summ['down']} lower in {num}).",
              f"- Low-count filter: {summ['filtered_low_count']} genes below mean count "
              f"{_fmt(summ['filter_threshold'])} were not adjusted (DESeq2 independent "
              f"filtering); {summ['cooks_outliers']} genes flagged by Cook's distance; "
              f"{summ['all_zero']} genes had no reads.",
              f"- Quantification: **{run.engine}**; trimming: **{run.trimmer}**.", ""]
    if run.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in run.warnings] + [""]
    unit = "read pairs" if run.samples[0].paired else "reads"
    lines += ["## Samples", "",
              "| sample | " + " | ".join(run.samples[0].attributes) +
              f" | {unit} | kept after trimming | assigned | QC fail | QC warn |",
              "|---" * (len(run.samples[0].attributes) + 6) + "|"]
    for s in run.samples:
        fails = sorted({m for r in run.qc[s.name].values() for m, v in r["flags"].items()
                        if v == "fail"})
        warns = sorted({m for r in run.qc[s.name].values() for m, v in r["flags"].items()
                        if v == "warn"})
        trimmed = run.trimming.get(s.name, {})
        mates = 2 if s.paired else 1
        total = (trimmed["reads_in"] // mates if trimmed
                 else run.quantification[s.name].get("reads") or run.qc[s.name]["R1"]["reads"])
        kept = (f"{trimmed['reads_out'] / trimmed['reads_in']:.1%}"
                if trimmed.get("reads_in") else "not trimmed")
        rate = run.quantification[s.name].get("mapping_rate")
        lines.append(f"| {s.name} | " + " | ".join(s.attributes.values()) +
                     f" | {total:,} | {kept} | "
                     f"{'NA' if rate is None else f'{rate:.1%}'} | "
                     f"{', '.join(fails) or '-'} | {', '.join(warns) or '-'} |")
    lines += ["", f"QC measured at most the first {config.qc_reads:,} reads of each file.",
              ""]
    order = [i for i in np.argsort(np.where(np.isfinite(res.p_adjusted), res.p_adjusted,
                                            np.inf), kind="mergesort")
             if np.isfinite(res.p_adjusted[i])][:20]
    lines += ["## Top genes", "",
              "| gene | name | base mean | log2 FC | SE | p | FDR |",
              "|---|---|---|---|---|---|---|"]
    for i in order:
        g = res.genes[i]
        lines.append(f"| {g} | {names.get(g, g)} | {_fmt(float(res.base_mean[i]))} | "
                     f"{_fmt(float(res.log2_fold_change[i]))} | "
                     f"{_fmt(float(res.lfc_se[i]))} | {_fmt(float(res.p_value[i]))} | "
                     f"{_fmt(float(res.p_adjusted[i]))} |")
    if not order:
        lines.append("| (none) | | | | | | |")
    lines += ["", "The full table is `deseq2_results.tsv`; counts and TPM are `counts.tsv` "
              "and `tpm.tsv`; the variance-stabilised matrix is `vst.tsv`.", ""]
    lines += ["## Figures", ""]
    for name in ("pca", "sample_distances", "ma", "volcano", "p_values", "dispersion",
                 "mapping"):
        lines.append(f"![{name}](plots/{name}.svg)")
    lines += ["", "## Methods", ""]
    tools = backends.available_tools()
    lines += [
        "- **QC**: per-base and per-read quality, GC, N, adapter, duplication and "
        "over-represented sequences with FastQC's modules and pass/warn/fail limits.",
        ("- **Trimming**: fastp " + tools.get("fastp", "?") + f" (3' sliding-window quality "
         f"{config.quality}, minimum length {config.min_length}, adapters auto-detected)."
         if run.trimmer == "fastp" else
         f"- **Trimming**: built-in, cutadapt's algorithms (3' quality {config.quality}, "
         f"adapters at 10% error, minimum length {config.min_length})."
         if run.trimmer == "builtin" else "- **Trimming**: none."),
        {"salmon": f"- **Quantification**: salmon {tools.get('salmon', '?')} (selective "
                   "alignment, library type detected).",
         "kallisto": f"- **Quantification**: kallisto {tools.get('kallisto', '?')} "
                     "(pseudoalignment).",
         "builtin": f"- **Quantification**: built-in k-mer pseudoalignment (k = {config.k}) "
                    "and kallisto's EM; read pairs checked for fragment-length consistency.",
         "hisat2": f"- **Alignment**: HISAT2 {tools.get('hisat2', '?')}, samtools "
                   f"{tools.get('samtools', '?')}; gene counts by featureCounts "
                   f"{tools.get('featureCounts', '?')} (strand {config.strand})."}[run.engine],
        (f"- **Gene totals**: tximport's rules ({run.genes.counts_from_abundance}), "
         "rounded for the count model." if run.engine != "hisat2" else
         "- **Gene totals**: featureCounts' gene counts."),
        "- **Differential expression**: the DESeq2 method (Love et al. 2014): median-of-"
        "ratios size factors, Cox-Reid dispersions shrunk to a parametric trend, NB GLM, "
        "Wald test, Cook's distances, independent filtering, Benjamini-Hochberg.",
        "- **Exploration**: VST with the fitted trend; PCA of the "
        f"{run.pca['genes']} most variable genes; Euclidean sample distances.", ""]
    lines += [f"- {n}" for n in summ["notes"] + list(run.genes.notes)]
    lines += ["", "## What this result is", "",
              "A statistical association within this experiment: these genes differ between "
              f"the {num} and {den} samples analysed. It does not establish a mechanism, a "
              "cause or any clinical effect, and it holds for these samples, this "
              "reference and these settings. Fold changes are maximum-likelihood estimates "
              "without shrinkage, so low-count genes can show large, imprecise changes.", "",
              "## Reproducing it", "",
              "`run.json` records every input and output digest, each tool's version and "
              "command, and the configuration. `bioagent rnaseq --verify <out>` re-checks the "
              "digests.", ""]
    return "\n".join(lines)


def _html(md: str, plots: Mapping[str, str]) -> str:
    """The Markdown report as one HTML page, figures inline."""
    body: list[str] = []
    table: list[str] = []

    def flush() -> None:
        if not table:
            return
        rows = [r for r in table if not set(r.replace("|", "").strip()) <= {"-"}]
        body.append("<table>")
        for k, r in enumerate(rows):
            cells = [c.strip() for c in r.strip().strip("|").split("|")]
            tag = "th" if k == 0 else "td"
            body.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells)
                        + "</tr>")
        body.append("</table>")
        table.clear()

    in_list = False
    in_code = False
    for line in md.splitlines():
        if line.startswith("```"):
            flush()
            body.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(line))
            continue
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("# "):
            body.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("!["):
            name = line[2:line.index("]")]
            body.append(f'<figure>{plots.get(name, "")}</figure>')
        elif line.strip():
            body.append(f"<p>{_inline(line)}</p>")
    flush()
    if in_list:
        body.append("</ul>")
    if in_code:
        body.append("</pre>")
    style = ("body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;"
             "margin:2em auto;padding:0 16px;color:#222;background:#fff}"
             "table{border-collapse:collapse;margin:1em 0;font-size:13px}"
             "td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
             "th{background:#f3f3f3}figure{margin:1em 0}svg{max-width:100%;height:auto}"
             "code{background:#f3f3f3;padding:0 3px}"
             "pre{background:#f6f6f6;padding:8px;overflow-x:auto;font-size:12px}")
    title = md.splitlines()[0].lstrip("# ")
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{style}</style></head><body>\n"
            + "\n".join(body) + "\n</body></html>\n")


def _inline(text: str) -> str:
    out = html.escape(text)
    parts = out.split("`")
    out = "".join(f"<code>{p}</code>" if i % 2 else p for i, p in enumerate(parts))
    parts = out.split("**")
    return "".join(f"<strong>{p}</strong>" if i % 2 else p for i, p in enumerate(parts))
