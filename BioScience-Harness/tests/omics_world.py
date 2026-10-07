"""A simulated RNA-seq experiment with a known answer, for the omics tests.

A synthetic chromosome holds 30 genes of two to four exons; every third gene with three
or more exons has a second isoform that skips exon 2. Six libraries (three control,
three treated) are drawn: gene counts negative binomial (dispersion 0.05) around a
per-gene mean, with genes G000-G003 four times higher in treated samples and G004-G006
four times lower; fragments are sampled from the transcripts (fragment length about
200), from either strand, and sequenced as 75-base pairs or single reads.

Everything a pipeline needs is written: transcriptome and genome FASTA, a GTF, FASTQ
files and an nf-core-style sample sheet.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

UP = ("G000", "G001", "G002", "G003")
DOWN = ("G004", "G005", "G006")
READ_LEN = 75
_COMP = str.maketrans("ACGT", "TGCA")


def need_tools(*names: str) -> None:
    """Skip unless every tool is on PATH; fail instead when BIOAGENT_REQUIRE_TOOLS is
    set, so a CI job that installs the tools cannot pass by skipping."""
    missing = [n for n in names if shutil.which(n) is None]
    if missing:
        message = f"not installed: {', '.join(missing)}"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(message)
        pytest.skip(message)


def need_module(name: str):
    """The module, or a skip (a failure under BIOAGENT_REQUIRE_TOOLS)."""
    import importlib
    try:
        return importlib.import_module(name)
    except ImportError:
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(f"not installed: {name}")
        pytest.skip(f"not installed: {name}")


def need_mcp_sdk():
    """The MCP SDK, if it is a major the transport and its fixture server are written for
    (1.x or 2.x); else a skip (a failure under BIOAGENT_REQUIRE_TOOLS). A later major
    imports too, so ``need_module("mcp")`` would let these tests run against APIs it may
    have renamed, as 2.x renamed 1.x's."""
    from importlib import metadata
    module = need_module("mcp")
    version = metadata.version("mcp")
    if version.split(".", 1)[0] not in ("1", "2"):
        why = f"mcp {version} is installed; these tests need the 1.x or 2.x SDK (mcp<3)"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(why)
        pytest.skip(why)
    return module


def need_r_package(name: str) -> None:
    """Skip unless ``Rscript`` can load the R package ``name``; fail instead under
    BIOAGENT_REQUIRE_TOOLS. R on PATH without the package is as unusable as no R."""
    need_tools("Rscript")
    done = subprocess.run(["Rscript", "-e", f"suppressMessages(library({name}))"],
                          capture_output=True, text=True)
    if done.returncode:
        why = f"R package {name} cannot be loaded: {done.stderr.strip()[-300:]}"
        if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
            pytest.fail(why)
        pytest.skip(why)


def simulate_counts(n_genes: int = 300, *, seed: int = 0, numeric: bool = False):
    """Gene counts for eight samples with a batch factor and an age covariate; 10% of
    genes respond to treatment (log2 fold change +-1.5).

    Samples alternate control and treated; the last four are batch b2, 1.4-fold higher.
    Negative binomial, dispersion 0.05; with ``numeric`` the means follow age too.
    Returns genes x samples counts, gene IDs, sample IDs and each sample's variables.
    """
    rng = np.random.default_rng(seed)
    base = rng.lognormal(4.0, 1.2, n_genes)
    lfc = np.where(rng.random(n_genes) < 0.1, rng.choice([-1.5, 1.5], n_genes), 0.0)
    ids, meta, cols = [], {}, []
    for k in range(8):
        condition = "treated" if k % 2 else "control"
        batch = "b2" if k >= 4 else "b1"
        age = 40.0 + 3 * k
        mu = base * 2 ** (lfc * (condition == "treated")) * (1.4 if batch == "b2" else 1.0)
        if numeric:
            mu = mu * np.exp(0.01 * (age - 50))
        size = 1 / 0.05
        cols.append(rng.negative_binomial(size, size / (size + mu)))
        ids.append(f"s{k}")
        meta[f"s{k}"] = {"condition": condition, "batch": batch, "age": str(age)}
    return np.column_stack(cols), [f"g{i}" for i in range(n_genes)], ids, meta


def need_reviewed_engine(engine_cls):
    """(engine, probe) for a structure engine in the interpreter the reviewed environments
    file ($BIOAGENT_ENVIRONMENTS) names for its project; else a skip (a failure under
    BIOAGENT_REQUIRE_TOOLS). A named file that fails its review raises: that is an error
    in the configuration, not a missing tool."""
    from bioagent.backends.environments import ExecutionEnvironments

    envs = ExecutionEnvironments.from_env()
    why = ""
    if envs is None or envs.interpreter(engine_cls.project) is None:
        why = f"no reviewed environment names {engine_cls.project!r} ($BIOAGENT_ENVIRONMENTS)"
    else:
        engine = engine_cls(envs)
        probe = engine.probe()
        if probe.available:
            return engine, probe
        why = probe.reason
    if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
        pytest.fail(why)
    pytest.skip(why)


def need_dir(variable: str, what: str) -> Path:
    """The directory an environment variable names; else a skip (a failure under
    BIOAGENT_REQUIRE_TOOLS)."""
    raw = os.environ.get(variable, "").strip()
    why = f"${variable} does not name {what}" if not raw else (
        "" if Path(raw).is_dir() else f"${variable} names {raw}, which is not a directory")
    if not why:
        return Path(raw)
    if os.environ.get("BIOAGENT_REQUIRE_TOOLS"):
        pytest.fail(why)
    pytest.skip(why)


def revcomp(seq: str) -> str:
    return seq.translate(_COMP)[::-1]


@dataclass
class World:
    root: Path
    transcripts: Path
    genome: Path
    gtf: Path
    sheet: Path
    tx_seqs: dict[str, str]
    tx_gene: dict[str, str]
    paired: bool


def _seq(rng: np.random.Generator, n: int) -> str:
    return "".join(np.array(list("ACGT"))[rng.integers(0, 4, n)])


def make_world(root: Path, *, seed: int = 1, paired: bool = True, genes: int = 30,
               per_group: int = 3, mean_reads: float = 120.0, gzip_reads: bool = True,
               adapter_rate: float = 0.0) -> World:
    rng = np.random.default_rng(seed)
    root.mkdir(parents=True, exist_ok=True)
    chrom: list[str] = []
    pos = 0
    gtf: list[str] = []
    tx_seqs: dict[str, str] = {}
    tx_gene: dict[str, str] = {}
    gene_tx: dict[str, list[str]] = {}

    def add(piece: str) -> int:
        nonlocal pos
        chrom.append(piece)
        start = pos + 1
        pos += len(piece)
        return start

    for g in range(genes):
        gid = f"G{g:03d}"
        add(_seq(rng, 300))                                   # intergenic
        n_exons = int(rng.integers(2, 5))
        exons, coords = [], []
        for e in range(n_exons):
            seq = _seq(rng, int(rng.integers(150, 400)))
            start = add(seq)
            exons.append(seq)
            coords.append((start, start + len(seq) - 1))
            if e < n_exons - 1:
                add("GT" + _seq(rng, int(rng.integers(100, 300))) + "AG")   # intron
        isoforms = {f"{gid}-201": list(range(n_exons))}
        if g % 3 == 2 and n_exons >= 3:
            isoforms[f"{gid}-202"] = [0] + list(range(2, n_exons))
        gene_tx[gid] = list(isoforms)
        gtf.append(f"chr1\tsim\tgene\t{coords[0][0]}\t{coords[-1][1]}\t.\t+\t.\t"
                   f'gene_id "{gid}"; gene_name "SIM{g}";')
        for tid, idx in isoforms.items():
            tx_seqs[tid] = "".join(exons[i] for i in idx)
            tx_gene[tid] = gid
            gtf.append(f"chr1\tsim\ttranscript\t{coords[idx[0]][0]}\t{coords[idx[-1]][1]}\t.\t"
                       f'+\t.\tgene_id "{gid}"; transcript_id "{tid}"; gene_name "SIM{g}";')
            for i in idx:
                gtf.append(f"chr1\tsim\texon\t{coords[i][0]}\t{coords[i][1]}\t.\t+\t.\t"
                           f'gene_id "{gid}"; transcript_id "{tid}"; gene_name "SIM{g}";')
    add(_seq(rng, 300))
    genome = root / "genome.fa"
    genome.write_text(">chr1\n" + "\n".join(_wrap("".join(chrom))) + "\n")
    transcripts = root / "transcripts.fa"
    transcripts.write_text("".join(f">{t} gene={tx_gene[t]}\n" + "\n".join(_wrap(s)) + "\n"
                                   for t, s in tx_seqs.items()))
    gtf_path = root / "genes.gtf"
    gtf_path.write_text("\n".join(gtf) + "\n")

    base = {gid: mean_reads * float(np.exp(rng.normal(0, 0.5))) for gid in gene_tx}
    frac = {gid: (np.array([0.6, 0.4]) if len(t) == 2 else np.array([1.0]))
            for gid, t in gene_tx.items()}
    names = [f"ctrl{i + 1}" for i in range(per_group)] + \
            [f"trt{i + 1}" for i in range(per_group)]
    rows = ["sample,fastq_1,fastq_2,condition"]
    adapter = "AGATCGGAAGAGCACACGTCTGAACTCCAGTCAC"
    for k, name in enumerate(names):
        treated = name.startswith("trt")
        size = float(rng.uniform(0.7, 1.3))
        ext = ".fastq.gz" if gzip_reads else ".fastq"
        f1 = root / f"{name}_R1{ext}"
        f2 = root / f"{name}_R2{ext}"
        opener = (lambda p: gzip.open(p, "wt")) if gzip_reads else (lambda p: open(p, "w"))
        h1 = opener(f1)
        h2 = opener(f2) if paired else None
        n = 0
        for gid, txs in gene_tx.items():
            mu = base[gid] * size
            if treated and gid in UP:
                mu *= 4
            if treated and gid in DOWN:
                mu /= 4
            alpha = 0.05
            count = int(rng.negative_binomial(1 / alpha, 1 / (1 + alpha * mu)))
            split = rng.multinomial(count, frac[gid])
            for tid, c in zip(txs, split):
                s = tx_seqs[tid]
                for _ in range(int(c)):
                    flen = int(min(max(rng.normal(200, 20), READ_LEN), len(s)))
                    start = int(rng.integers(0, len(s) - flen + 1))
                    frag = s[start:start + flen]
                    if rng.random() < 0.5:
                        frag = revcomp(frag)
                    r1 = frag[:READ_LEN]
                    r2 = revcomp(frag)[:READ_LEN]
                    if adapter_rate and rng.random() < adapter_rate:
                        insert = int(rng.integers(30, 60))
                        r1 = (frag[:insert] + adapter)[:READ_LEN]
                        r2 = (revcomp(frag[:insert]) + adapter)[:READ_LEN]
                    q = "I" * len(r1)
                    h1.write(f"@{name}.{n}/1\n{r1}\n+\n{q}\n")
                    if h2 is not None:
                        h2.write(f"@{name}.{n}/2\n{r2}\n+\n{'I' * len(r2)}\n")
                    n += 1
        h1.close()
        if h2 is not None:
            h2.close()
        rows.append(f"{name},{f1.name},{f2.name if paired else ''},"
                    f"{'treated' if treated else 'control'}")
    sheet = root / "samples.csv"
    sheet.write_text("\n".join(rows) + "\n")
    return World(root=root, transcripts=transcripts, genome=genome, gtf=gtf_path,
                 sheet=sheet, tx_seqs=tx_seqs, tx_gene=tx_gene, paired=paired)


def _wrap(seq: str, width: int = 60) -> list[str]:
    return [seq[i:i + width] for i in range(0, len(seq), width)]
