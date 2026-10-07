"""FASTQ files: streaming reads, quality control and trimming.

``tools.formats.parse_fastq`` summarises a FASTQ string a model hands it. This module
reads sequencing files, plain or gzipped and of any size, one record at a time. It
reports what an analyst checks before trusting a library, with the module checks and
thresholds FastQC uses:

* per-position quality (quartiles from a per-position histogram), per-read mean quality;
* GC content, N content, the length distribution;
* adapter content (Illumina universal, Nextera, small-RNA 3');
* duplication and over-represented sequences, from the first 100,000 reads.

Trimming follows cutadapt's conventions and order:

* poly-G tails from two-colour chemistry, then 3' quality trimming by the running-sum
  algorithm BWA uses;
* then adapter removal anywhere in the read, with errors allowed in proportion to the
  overlap (none below ten bases, as in cutadapt) and the adapter's start allowed to
  overhang the read's 3' end. Candidate positions come from exact seeds, so an adapter
  whose every seed carries an error is missed; a full aligner (cutadapt, fastp) is the
  better choice for large libraries and is run when installed (``backends``);
* reads with too many Ns or too short after trimming are dropped, pairs together.

This implementation is the reference the backends are compared with and the path for
small libraries; it reads about 10^5 reads per second.
"""

from __future__ import annotations

import gzip
import io
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, NamedTuple

import numpy as np

__all__ = ["ADAPTERS", "FastqError", "FastqRecord", "QCReport", "TrimReport", "TrimSettings",
           "detect_offset", "open_text", "quality_control", "read_fastq", "trim_fastq",
           "trim_read"]

#: Adapter prefixes, as cutadapt and FastQC use them.
ADAPTERS: Mapping[str, str] = {
    "illumina_universal": "AGATCGGAAGAGC",
    "nextera": "CTGTCTCTTATACACATCT",
    "illumina_small_rna_3p": "TGGAATTCTCGGGTGCCAAGG",
}
MAX_QUAL = 94


class FastqError(ValueError):
    """A file that is not a well-formed FASTQ file."""


class FastqRecord(NamedTuple):
    name: str
    seq: str
    qual: str


def open_text(path: str | Path):
    """A text handle on ``path``, decompressing gzip by its magic bytes, not its name."""
    path = Path(path)
    with path.open("rb") as fh:
        magic = fh.read(2)
    if magic == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="ascii", newline=None)
    return path.open("r", encoding="ascii", newline=None)


def read_fastq(path: str | Path) -> Iterator[FastqRecord]:
    """Every record of ``path``, each checked: header, separator, matching lengths."""
    with open_text(path) as fh:
        line_no = 0
        while True:
            head = fh.readline()
            if not head:
                return
            line_no += 1
            if not head.strip():
                continue
            seq, plus, qual = fh.readline(), fh.readline(), fh.readline()
            if not head.startswith("@"):
                raise FastqError(f"{path}: line {line_no} does not start a record with '@'")
            if not plus.startswith("+"):
                raise FastqError(f"{path}: record at line {line_no} has no '+' separator")
            seq, qual = seq.rstrip("\r\n"), qual.rstrip("\r\n")
            if len(seq) != len(qual):
                raise FastqError(f"{path}: record at line {line_no} has {len(seq)} bases "
                                 f"and {len(qual)} quality values")
            line_no += 3
            yield FastqRecord(head[1:].rstrip("\r\n").split()[0], seq.upper(), qual)


def detect_offset(path: str | Path, n: int = 10_000) -> int:
    """33 (Sanger, Illumina 1.8+) or 64 (Illumina 1.3-1.7), from the quality characters."""
    lo, hi = 255, 0
    for i, rec in enumerate(read_fastq(path)):
        if i >= n:
            break
        if rec.qual:
            lo, hi = min(lo, min(map(ord, rec.qual))), max(hi, max(map(ord, rec.qual)))
    if lo == 255:
        return 33
    if lo < 59:
        return 33
    if lo >= 64 and hi > 74:
        return 64
    return 33


# ============================================================== quality control

@dataclass
class QCReport:
    """What one FASTQ file holds, with FastQC-style pass / warn / fail per module."""

    path: str
    reads: int                                  # reads measured
    bases: int
    offset: int
    length: dict[str, float]
    gc_percent: float
    mean_quality: float
    q30_fraction: float
    per_position_quality: list[dict[str, float]]
    n_content_max: float
    adapter_content: dict[str, float]
    duplication_rate: float
    overrepresented: list[dict[str, Any]]
    flags: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _quartiles(hist: np.ndarray) -> tuple[float, float, float, float]:
    """(lower quartile, median, upper quartile, mean) from a histogram of 0..MAX_QUAL."""
    total = hist.sum()
    if not total:
        return 0.0, 0.0, 0.0, 0.0
    cdf = np.cumsum(hist)
    q = [float(np.searchsorted(cdf, total * f)) for f in (0.25, 0.5, 0.75)]
    mean = float((hist * np.arange(len(hist))).sum() / total)
    return q[0], q[1], q[2], mean


def quality_control(path: str | Path, *, offset: int | None = None,
                    sample: int = 100_000, max_reads: int | None = None) -> QCReport:
    """Read ``path`` once and measure it: every read, or the first ``max_reads``."""
    offset = detect_offset(path) if offset is None else offset
    pos_hist = np.zeros((0, MAX_QUAL + 1), dtype=np.int64)
    n_pos = np.zeros(0, dtype=np.int64)
    lengths: Counter[int] = Counter()
    read_mean_hist = np.zeros(MAX_QUAL + 1, dtype=np.int64)
    gc_hist = np.zeros(101, dtype=float)
    gc_models: dict[int, list[tuple[np.ndarray, np.ndarray]]] = {}
    adapter_first: dict[str, Counter[int]] = {k: Counter() for k in ADAPTERS}
    seen: Counter[str] = Counter()
    reads = bases = q30 = 0
    gc_total = at_total = 0
    for rec in read_fastq(path):
        if max_reads is not None and reads >= max_reads:
            break
        length = len(rec.seq)
        reads += 1
        bases += length
        lengths[length] += 1
        if length == 0:
            continue
        q = np.frombuffer(rec.qual.encode("ascii"), dtype=np.uint8).astype(np.int64) - offset
        if q.min() < 0 or q.max() > MAX_QUAL:
            raise FastqError(f"{path}: quality values outside Phred+{offset} in {rec.name}")
        if length > pos_hist.shape[0]:
            pos_hist = np.vstack([pos_hist, np.zeros((length - pos_hist.shape[0],
                                                      MAX_QUAL + 1), dtype=np.int64)])
            n_pos = np.concatenate([n_pos, np.zeros(length - n_pos.shape[0], dtype=np.int64)])
        pos_hist[np.arange(length), q] += 1
        q30 += int((q >= 30).sum())
        read_mean_hist[int(round(float(q.mean())))] += 1
        seq_bytes = np.frombuffer(rec.seq.encode("ascii"), dtype=np.uint8)
        n_pos[:length] += seq_bytes == ord("N")
        g = rec.seq.count("G") + rec.seq.count("C")
        a = rec.seq.count("A") + rec.seq.count("T")
        gc_total += g
        at_total += a
        model = gc_models.get(length)
        if model is None:
            model = gc_models[length] = _gc_model(length)
        bins, weights = model[g]
        gc_hist[bins] += weights
        for name, adapter in ADAPTERS.items():
            at = rec.seq.find(adapter[:12])
            if at >= 0:
                adapter_first[name][at] += 1
        if reads <= sample:
            seen[rec.seq] += 1
    if not reads:
        raise FastqError(f"{path} holds no reads")
    per_position = []
    for i in range(pos_hist.shape[0]):
        lq, med, uq, mean = _quartiles(pos_hist[i])
        per_position.append({"position": i + 1, "mean": round(mean, 3), "lower_quartile": lq,
                             "median": med, "upper_quartile": uq})
    coverage = pos_hist.sum(axis=1)
    n_fraction = np.where(coverage > 0, n_pos / np.maximum(coverage, 1), 0.0)
    adapter_content = {}
    for name, firsts in adapter_first.items():
        # cumulative: the share of reads with the adapter starting at or before a position
        cumulative = 0
        worst = 0.0
        for pos in sorted(firsts):
            cumulative += firsts[pos]
            worst = max(worst, cumulative / reads)
        adapter_content[name] = round(worst, 6)
    sampled = sum(seen.values())
    duplication = 1.0 - len(seen) / sampled if sampled else 0.0
    over = [{"sequence": s, "count": c, "percent": round(100 * c / sampled, 4)}
            for s, c in seen.most_common(20) if c / sampled > 0.001]
    mean_quality = float((read_mean_hist * np.arange(MAX_QUAL + 1)).sum() / reads)
    gc_percent = 100.0 * gc_total / max(gc_total + at_total, 1)
    length_values = np.repeat(np.array(list(lengths)), list(lengths.values()))
    report = QCReport(
        path=str(path), reads=reads, bases=bases, offset=offset,
        length={"min": int(length_values.min()), "max": int(length_values.max()),
                "mean": round(float(length_values.mean()), 3)},
        gc_percent=round(gc_percent, 3), mean_quality=round(mean_quality, 3),
        q30_fraction=round(q30 / max(bases, 1), 6), per_position_quality=per_position,
        n_content_max=round(float(n_fraction.max()) if len(n_fraction) else 0.0, 6),
        adapter_content=adapter_content, duplication_rate=round(duplication, 6),
        overrepresented=over)
    report.flags = _flags(report, read_mean_hist, gc_hist)
    return report


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _gc_model(length: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """FastQC's GCModel: the percentage bins a read of ``length`` bases with g G/C bases
    adds to, and by how much. A count claims every percentage its +-0.5 interval rounds
    to, each shared among the counts that claim it, so short reads do not leave gaps
    between the percentages they can reach."""
    spans = []
    claims = np.zeros(101)
    for g in range(length + 1):
        lo = min(max(g - 0.5, 0.0), length)
        hi = min(max(g + 0.5, 0.0), length)
        a, b = _round_half_up(lo * 100 / length), _round_half_up(hi * 100 / length)
        spans.append((a, b))
        claims[a:b + 1] += 1
    return [(np.arange(a, b + 1), 1.0 / claims[a:b + 1]) for a, b in spans]


def _gc_deviation(gc_hist: np.ndarray) -> float | None:
    """FastQC's per-sequence GC deviation: the summed difference from a normal centred
    on the mode (averaged over neighbours within 10% of it) with the data's SD, as a
    fraction of reads."""
    total = float(gc_hist.sum())
    if total <= 1:
        return None
    first = int(np.argmax(gc_hist))
    top = gc_hist[first]
    near = top - top / 10
    idx, fell_top, fell_bottom = [first], True, True
    for i in range(first + 1, 101):
        if gc_hist[i] > near:
            idx.append(i)
        else:
            fell_top = False
            break
    for i in range(first - 1, -1, -1):
        if gc_hist[i] > near:
            idx.append(i)
        else:
            fell_bottom = False
            break
    mode = float(first) if (fell_top or fell_bottom) else float(np.mean(idx))
    x = np.arange(101)
    sd = math.sqrt(float((gc_hist * (x - mode) ** 2).sum()) / (total - 1)) or 1.0
    theory = np.exp(-0.5 * ((x - mode) / sd) ** 2) / (sd * math.sqrt(2 * math.pi)) * total
    return float(np.abs(theory - gc_hist).sum() / total)


def _flags(report: QCReport, read_mean_hist: np.ndarray, gc_hist: np.ndarray) -> dict[str, str]:
    """FastQC's module verdicts, with its default thresholds."""
    def level(warn: bool, fail: bool) -> str:
        return "fail" if fail else ("warn" if warn else "pass")
    lq = [p["lower_quartile"] for p in report.per_position_quality]
    med = [p["median"] for p in report.per_position_quality]
    flags = {"per_base_quality": level(min(lq) < 10 or min(med) < 25,
                                       min(lq) < 5 or min(med) < 20)}
    mode = int(np.argmax(read_mean_hist))
    flags["per_sequence_quality"] = level(mode < 27, mode < 20)
    flags["n_content"] = level(report.n_content_max > 0.05, report.n_content_max > 0.2)
    worst_adapter = max(report.adapter_content.values(), default=0.0)
    flags["adapter_content"] = level(worst_adapter > 0.05, worst_adapter > 0.1)
    flags["duplication"] = level(report.duplication_rate > 0.2, report.duplication_rate > 0.5)
    flags["overrepresented"] = level(any(o["percent"] > 0.1 for o in report.overrepresented),
                                     any(o["percent"] > 1.0 for o in report.overrepresented))
    deviation = _gc_deviation(gc_hist)
    if deviation is not None:
        flags["gc_content"] = level(deviation > 0.15, deviation > 0.3)
    return flags


# ============================================================== trimming

@dataclass(frozen=True)
class TrimSettings:
    """cutadapt's defaults where it has one."""

    quality: int = 20                     # 3' quality cut-off (cutadapt -q)
    min_length: int = 20                  # shorter reads are dropped (-m)
    adapters: tuple[str, ...] = tuple(ADAPTERS.values())
    error_rate: float = 0.1               # mismatches allowed per base of overlap (-e)
    min_overlap: int = 3                  # a shorter 3' overhang is not trimmed (-O)
    poly_g: int = 10                      # trim a 3' run of at least this many Gs (0: off)
    max_n_fraction: float = 0.1           # reads with more Ns than this are dropped

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mismatches(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


def _adapter_cut(seq: str, adapter: str, error_rate: float, min_overlap: int) -> int:
    """Where ``adapter`` starts in ``seq`` (len(seq) when it does not).

    Full and long partial occurrences are found from exact eight-base seeds at the
    adapter's start, its end and (for adapters of 16 bases or more) its second eight
    bases, then verified with the error allowance; an overhang shorter than a seed must
    match exactly, as cutadapt requires below ten bases at a 10% error rate.
    """
    n, a = len(seq), len(adapter)
    seed = min(8, a)
    starts = set()
    for offset in sorted({0, max(a - seed, 0), seed if a >= 2 * seed else 0}):
        probe = adapter[offset:offset + seed]
        at = seq.find(probe)
        while at != -1:
            start = at - offset
            if 0 <= start <= n - min_overlap:
                starts.add(start)
            at = seq.find(probe, at + 1)
    for start in sorted(starts):
        overlap = min(n - start, a)
        if _mismatches(seq[start:start + overlap], adapter[:overlap]) <= int(
                overlap * error_rate):
            return start
    for overlap in range(min(seed - 1, n), min_overlap - 1, -1):
        if seq.endswith(adapter[:overlap]):
            return n - overlap
    return n


def _quality_cut(qual: str, offset: int, cutoff: int) -> int:
    """BWA's 3' trimming point: maximise the sum of (cutoff - q) from the 3' end."""
    s = best = 0
    cut = len(qual)
    for i in range(len(qual) - 1, -1, -1):
        s += cutoff - (ord(qual[i]) - offset)
        if s < 0:
            break
        if s > best:
            best, cut = s, i
    return cut


def trim_read(rec: FastqRecord, settings: TrimSettings, offset: int = 33
              ) -> tuple[FastqRecord, list[str]]:
    """The trimmed record, and what was done to it (adapter names, 'quality', 'poly_g')."""
    seq, qual = rec.seq, rec.qual
    done: list[str] = []
    if settings.poly_g:
        stripped = seq.rstrip("G")
        if len(seq) - len(stripped) >= settings.poly_g:
            seq, qual = stripped, qual[:len(stripped)]
            done.append("poly_g")
    if settings.quality:
        cut = _quality_cut(qual, offset, settings.quality)
        if cut < len(seq):
            seq, qual = seq[:cut], qual[:cut]
            done.append("quality")
    names = {v: k for k, v in ADAPTERS.items()}
    for adapter in settings.adapters:
        cut = _adapter_cut(seq, adapter, settings.error_rate, settings.min_overlap)
        if cut < len(seq):
            seq, qual = seq[:cut], qual[:cut]
            done.append(names.get(adapter, f"adapter:{adapter}"))
    return FastqRecord(rec.name, seq, qual), done


@dataclass
class TrimReport:
    reads_in: int = 0
    reads_out: int = 0
    bases_in: int = 0
    bases_out: int = 0
    too_short: int = 0
    too_many_n: int = 0
    trimmed: dict[str, int] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    paired: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _write(fh, rec: FastqRecord) -> None:
    fh.write(f"@{rec.name}\n{rec.seq}\n+\n{rec.qual}\n")


def trim_fastq(read1: str | Path, out1: str | Path, *, read2: str | Path | None = None,
               out2: str | Path | None = None, settings: TrimSettings = TrimSettings(),
               offset: int | None = None) -> TrimReport:
    """Trim one file, or a pair together (a pair is kept or dropped as one)."""
    if (read2 is None) != (out2 is None):
        raise ValueError("a second read file needs a second output, and the reverse")
    offset = detect_offset(read1) if offset is None else offset
    report = TrimReport(settings=settings.as_dict(), paired=read2 is not None)
    trimmed: Counter[str] = Counter()

    def opener(path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return (gzip.open(path, "wt", encoding="ascii") if path.suffix == ".gz"
                else path.open("w", encoding="ascii"))

    first = read_fastq(read1)
    second = read_fastq(read2) if read2 is not None else None
    with opener(out1) as fh1, (opener(out2) if out2 is not None else _Null()) as fh2:
        for rec1 in first:
            records = [rec1]
            if second is not None:
                rec2 = next(second, None)
                if rec2 is None:
                    raise FastqError(f"{read2} has fewer reads than {read1}")
                if _pair_name(rec1.name) != _pair_name(rec2.name):
                    raise FastqError(f"pair out of step: {rec1.name} / {rec2.name}")
                records.append(rec2)
            report.reads_in += len(records)
            report.bases_in += sum(len(r.seq) for r in records)
            outs, keep = [], True
            for rec in records:
                out, done = trim_read(rec, settings, offset)
                trimmed.update(done)
                if out.seq.count("N") > settings.max_n_fraction * max(len(out.seq), 1):
                    report.too_many_n += 1
                    keep = False
                elif len(out.seq) < settings.min_length:
                    report.too_short += 1
                    keep = False
                outs.append(out)
            if not keep:
                continue
            _write(fh1, outs[0])
            if len(outs) > 1:
                _write(fh2, outs[1])
            report.reads_out += len(outs)
            report.bases_out += sum(len(r.seq) for r in outs)
        if second is not None and next(second, None) is not None:
            raise FastqError(f"{read2} has more reads than {read1}")
    report.trimmed = dict(sorted(trimmed.items()))
    return report


def _pair_name(name: str) -> str:
    return name[:-2] if name.endswith(("/1", "/2")) else name


class _Null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, _text):  # pragma: no cover - never called
        pass
