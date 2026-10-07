"""The standard command-line tools, run when they are installed.

Each wrapper checks the tool is on ``PATH``, records its version and the exact command,
runs it with a log, and reads its output into the structures the built-in steps produce,
so the pipeline treats either path alike:

``fastp``         quality control and trimming (Chen et al. 2018, *Bioinformatics* 34:i884)
``salmon``        selective alignment and EM with bias models (Patro et al. 2017,
                  *Nature Methods* 14:417)
``kallisto``      pseudoalignment and EM (Bray et al. 2016)
``hisat2``        spliced alignment to a genome (Kim et al. 2019, *Nature Biotechnology*
                  37:907), sorted and indexed with ``samtools``
``featureCounts`` gene counts from alignments (Liao et al. 2014, *Bioinformatics* 30:923)

Nothing here is imported unless a tool is used; with none installed, the built-in steps
(``fastq``, ``quant``) run instead and the report says which ran.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .quant import QuantResult, read_abundance

__all__ = ["BackendError", "Tool", "Step", "find_tool", "available_tools", "fastp",
           "salmon_index", "salmon_quant", "kallisto_index", "kallisto_quant",
           "hisat2_index", "hisat2_align", "featurecounts", "CountMatrix"]

#: How each tool reports its version.
VERSION_ARGS = {"fastp": ["--version"], "salmon": ["--version"], "kallisto": ["version"],
                "hisat2": ["--version"], "hisat2-build": ["--version"],
                "samtools": ["--version"], "featureCounts": ["-v"]}
_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


class BackendError(RuntimeError):
    """A tool that is missing, or that ran and failed."""


@dataclass(frozen=True)
class Tool:
    name: str
    path: str
    version: str


@dataclass
class Step:
    """One tool invocation: what ran, on what, and what it wrote."""

    tool: str
    version: str
    command: list[str]
    inputs: dict[str, str] = field(default_factory=dict)       # path -> sha256
    outputs: dict[str, str] = field(default_factory=dict)
    log: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "version": self.version, "command": self.command,
                "inputs": self.inputs, "outputs": self.outputs, "log": self.log}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _digests(paths: Sequence[str | Path]) -> dict[str, str]:
    out = {}
    for p in paths:
        p = Path(p)
        if p.is_file():
            out[str(p)] = _sha256(p)
        elif p.is_dir():
            for f in sorted(q for q in p.rglob("*") if q.is_file()):
                out[str(f)] = _sha256(f)
    return out


_found: dict[str, Tool | None] = {}


def find_tool(name: str) -> Tool | None:
    """The tool on PATH with its version, or None."""
    if name in _found:
        return _found[name]
    path = shutil.which(name)
    tool = None
    if path:
        try:
            done = subprocess.run([path, *VERSION_ARGS.get(name, ["--version"])],
                                  capture_output=True, text=True, errors="replace",
                                  timeout=30)
            lines = [ln for ln in (done.stdout + "\n" + done.stderr).splitlines()
                     if ln.strip()]
        except (OSError, subprocess.TimeoutExpired):
            lines = []
        first = lines[0] if lines else ""
        match = _VERSION.search(first.split("version", 1)[-1])
        tool = Tool(name=name, path=path, version=match.group(1) if match else "unknown")
    _found[name] = tool
    return tool


def available_tools() -> dict[str, str]:
    """Every wrapped tool that is installed, with its version."""
    return {n: t.version for n in VERSION_ARGS if (t := find_tool(n)) is not None}


def _require(name: str) -> Tool:
    tool = find_tool(name)
    if tool is None:
        raise BackendError(f"{name} is not installed (looked on PATH)")
    return tool


def _run(tool: Tool, args: Sequence[str], *, log: Path, inputs: Sequence[str | Path],
         outputs: Sequence[str | Path], stdout: Path | None = None) -> Step:
    command = [tool.name, *map(str, args)]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as err:
        out = stdout.open("wb") if stdout is not None else subprocess.DEVNULL
        try:
            done = subprocess.run([tool.path, *map(str, args)], stdout=out, stderr=err,
                                  env={**os.environ, "LC_ALL": "C"})
        finally:
            if stdout is not None:
                out.close()
    if done.returncode != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:]
        raise BackendError(f"{' '.join(command)} exited with {done.returncode}:\n"
                           + "\n".join(tail))
    return Step(tool=tool.name, version=tool.version, command=command,
                inputs=_digests(inputs), outputs=_digests(outputs), log=str(log))


# ============================================================== fastp

def fastp(read1: str | Path, out1: str | Path, *, read2: str | Path | None = None,
          out2: str | Path | None = None, workdir: str | Path, quality: int = 20,
          min_length: int = 20, threads: int = 2) -> tuple[dict[str, Any], Step]:
    """QC and trimming: (summary in the built-in TrimReport's terms plus fastp's own
    before/after QC, the step)."""
    tool = _require("fastp")
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    report = work / "fastp.json"
    args = ["-i", read1, "-o", out1, "--json", report, "--html", work / "fastp.html",
            "--cut_tail", "--cut_tail_mean_quality", quality,
            "--length_required", min_length, "--thread", threads,
            "--dont_eval_duplication"]        # QC measures it; fastp's table costs seconds
    if read2 is not None:
        if out2 is None:
            raise BackendError("a second read file needs a second output")
        args += ["-I", read2, "-O", out2, "--detect_adapter_for_pe"]
    step = _run(tool, args, log=work / "fastp.log",
                inputs=[read1] + ([read2] if read2 else []),
                outputs=[out1, report] + ([out2] if out2 else []))
    doc = json.loads(report.read_text(encoding="utf-8"))
    before = doc["summary"]["before_filtering"]
    after = doc["summary"]["after_filtering"]
    filt = doc.get("filtering_result", {})
    summary = {
        "reads_in": int(before["total_reads"]), "reads_out": int(after["total_reads"]),
        "bases_in": int(before["total_bases"]), "bases_out": int(after["total_bases"]),
        "too_short": int(filt.get("too_short_reads", 0)),
        "too_many_n": int(filt.get("too_many_N_reads", 0)),
        "low_quality": int(filt.get("low_quality_reads", 0)),
        "q30_before": float(before.get("q30_rate", 0.0)),
        "q30_after": float(after.get("q30_rate", 0.0)),
        "gc_before": float(before.get("gc_content", 0.0)),
        "adapter_trimmed_reads": int(doc.get("adapter_cutting", {})
                                     .get("adapter_trimmed_reads", 0)),
        "paired": read2 is not None, "engine": f"fastp {tool.version}"}
    return summary, step


# ============================================================== salmon / kallisto

def salmon_index(transcripts: str | Path, out_dir: str | Path, *, k: int = 31,
                 threads: int = 2) -> Step:
    tool = _require("salmon")
    out = Path(out_dir)
    return _run(tool, ["index", "-t", transcripts, "-i", out, "-k", k, "-p", threads],
                log=out.parent / f"{out.name}.log", inputs=[transcripts], outputs=[])


def salmon_quant(index_dir: str | Path, read1: str | Path, out_dir: str | Path, *,
                 read2: str | Path | None = None, threads: int = 2,
                 library_type: str = "A", gc_bias: bool = False) -> tuple[QuantResult, Step]:
    tool = _require("salmon")
    out = Path(out_dir)
    args = ["quant", "-i", index_dir, "-l", library_type, "-o", out, "-p", threads]
    args += ["-1", read1, "-2", read2] if read2 is not None else ["-r", read1]
    if gc_bias:
        args.append("--gcBias")
    step = _run(tool, args, log=out.parent / f"{out.name}.log",
                inputs=[read1] + ([read2] if read2 else []), outputs=[out / "quant.sf"])
    result = read_abundance(out / "quant.sf")
    meta = {}
    meta_file = out / "aux_info" / "meta_info.json"
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    lib = out / "lib_format_counts.json"
    library = json.loads(lib.read_text(encoding="utf-8")) if lib.is_file() else {}
    processed = int(meta.get("num_processed", 0))
    mapped = int(meta.get("num_mapped", 0))
    result.run = {"method": f"salmon {tool.version} selective alignment + EM",
                  "reads": processed, "pseudoaligned": mapped,
                  "pseudoaligned_fraction": round(mapped / processed, 6) if processed else 0.0,
                  "library_type": library.get("expected_format", library_type),
                  "fragment_distribution": "estimated by salmon",
                  "paired": read2 is not None, "gc_bias": gc_bias}
    return result, step


def kallisto_index(transcripts: str | Path, index_path: str | Path, *, k: int = 31) -> Step:
    tool = _require("kallisto")
    path = Path(index_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _run(tool, ["index", "-i", path, "-k", k, transcripts],
                log=path.with_suffix(".log"), inputs=[transcripts], outputs=[path])


def kallisto_quant(index_path: str | Path, read1: str | Path, out_dir: str | Path, *,
                   read2: str | Path | None = None, fragment_mean: float = 200.0,
                   fragment_sd: float = 30.0, threads: int = 2) -> tuple[QuantResult, Step]:
    tool = _require("kallisto")
    out = Path(out_dir)
    args = ["quant", "-i", index_path, "-o", out, "-t", threads]
    if read2 is None:
        args += ["--single", "-l", fragment_mean, "-s", fragment_sd, read1]
    else:
        args += [read1, read2]
    step = _run(tool, args, log=out.parent / f"{out.name}.log",
                inputs=[read1] + ([read2] if read2 else []),
                outputs=[out / "abundance.tsv"])
    result = read_abundance(out / "abundance.tsv")
    info_file = out / "run_info.json"
    info = json.loads(info_file.read_text(encoding="utf-8")) if info_file.is_file() else {}
    processed = int(info.get("n_processed", 0))
    aligned = int(info.get("n_pseudoaligned", 0))
    result.run = {"method": f"kallisto {tool.version} pseudoalignment + EM",
                  "reads": processed, "pseudoaligned": aligned,
                  "pseudoaligned_fraction": round(aligned / processed, 6) if processed else 0.0,
                  "fragment_distribution": ("assumed truncated Gaussian" if read2 is None
                                            else "estimated by kallisto"),
                  "paired": read2 is not None}
    return result, step


# ============================================================== HISAT2 + featureCounts

def hisat2_index(genome: str | Path, prefix: str | Path, *, threads: int = 2) -> Step:
    tool = _require("hisat2-build")
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    return _run(tool, ["-p", threads, genome, prefix], log=prefix.with_suffix(".build.log"),
                inputs=[genome], outputs=[])


def hisat2_align(prefix: str | Path, read1: str | Path, bam: str | Path, *,
                 read2: str | Path | None = None, threads: int = 2) -> tuple[dict, list[Step]]:
    """Align, then sort and index: (alignment summary, steps)."""
    hisat2 = _require("hisat2")
    samtools = _require("samtools")
    bam = Path(bam)
    bam.parent.mkdir(parents=True, exist_ok=True)
    sam = bam.with_suffix(".sam")
    summary_file = bam.with_suffix(".hisat2.txt")
    args = ["-x", prefix, "-p", threads, "--summary-file", summary_file, "-S", sam]
    args += ["-1", read1, "-2", read2] if read2 is not None else ["-U", read1]
    steps = [_run(hisat2, args, log=bam.with_suffix(".hisat2.log"),
                  inputs=[read1] + ([read2] if read2 else []), outputs=[summary_file])]
    steps.append(_run(samtools, ["sort", "-@", threads, "-o", bam, sam],
                      log=bam.with_suffix(".sort.log"), inputs=[], outputs=[bam]))
    steps.append(_run(samtools, ["index", bam], log=bam.with_suffix(".index.log"),
                      inputs=[], outputs=[]))
    sam.unlink(missing_ok=True)
    text = summary_file.read_text(encoding="utf-8", errors="replace")
    rate = re.search(r"([\d.]+)% overall alignment rate", text)
    return {"overall_alignment_rate": float(rate.group(1)) / 100 if rate else None,
            "summary": text.strip()}, steps


@dataclass
class CountMatrix:
    genes: tuple[str, ...]
    samples: tuple[str, ...]
    counts: np.ndarray
    lengths: np.ndarray
    assigned: dict[str, dict[str, int]] = field(default_factory=dict)


def featurecounts(bams: Sequence[str | Path], samples: Sequence[str], annotation: str | Path,
                  out: str | Path, *, paired: bool = False, strand: int = 0,
                  threads: int = 2) -> tuple[CountMatrix, Step]:
    """Gene counts (``-t exon -g gene_id``); ``strand`` 0 unstranded, 1 stranded, 2
    reversely stranded, as featureCounts' ``-s``."""
    tool = _require("featureCounts")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["-a", annotation, "-o", out, "-t", "exon", "-g", "gene_id", "-s", strand,
            "-T", threads]
    if paired:
        args += ["-p", "--countReadPairs"]
    args += [str(b) for b in bams]
    step = _run(tool, args, log=out.with_suffix(".log"), inputs=[annotation, *bams],
                outputs=[out, Path(f"{out}.summary")])
    genes, lengths, rows = [], [], []
    with out.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or line.startswith("Geneid\t"):
                continue
            cols = line.rstrip("\n").split("\t")
            genes.append(cols[0])
            lengths.append(int(cols[5]))
            rows.append([int(v) for v in cols[6:]])
    assigned: dict[str, dict[str, int]] = {s: {} for s in samples}
    with Path(f"{out}.summary").open(encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            for s, v in zip(samples, cols[1:]):
                if int(v):
                    assigned[s][cols[0]] = int(v)
    return CountMatrix(genes=tuple(genes), samples=tuple(samples),
                       counts=np.array(rows, dtype=float).reshape(len(genes), len(samples)),
                       lengths=np.array(lengths, dtype=float), assigned=assigned), step
