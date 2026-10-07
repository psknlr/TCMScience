"""Transcript quantification: k-mer pseudoalignment and EM, then gene totals.

The method is kallisto's (Bray et al. 2016, *Nature Biotechnology* 34:525):

* the index maps every canonical k-mer of the transcriptome (k = 31) to the transcripts
  that contain it, and where;
* a read, or a read pair, is compatible with the transcripts shared by all of its k-mers
  that the index holds; reads compatible with the same set form an equivalence class;
* the EM algorithm estimates how many reads each transcript produced, weighting a
  transcript by the inverse of its effective length, with kallisto's convergence rule
  (no estimate above 0.01 reads changing by more than 1% after at least 50 rounds) and
  its final zeroing of estimates below 1e-8;
* effective lengths are kallisto's: the length minus the mean of the fragment-length
  distribution truncated at that length, plus one. For read pairs the distribution is
  estimated from the pairs that fall in a single transcript, as kallisto does; for
  single reads it is a truncated Gaussian whose mean and SD are given.

One step goes beyond kallisto. A pair whose mates both fall in shared exons is
compatible, by its k-mers, with an isoform that holds an extra exon between them; the
fragment it would imply there is longer by that exon. A transcript is therefore dropped
from a pair's class when the implied fragment exceeds the estimated distribution's
mean + 5 SD (the class is kept whole when that would leave nothing). Without the check,
reads of an exon-skipping isoform drift to the longer one.

Gene totals follow tximport (Soneson, Love & Robinson 2015): counts summed over a gene's
transcripts, TPM summed, the gene length the abundance-weighted mean of its transcripts'
effective lengths; with ``lengthScaledTPM`` the counts are rebuilt from TPM and the
average gene length so a change in isoform usage does not read as a change in expression.

The index lives in memory, about 25 bytes per k-mer occurrence. It suits gene panels,
small genomes and test data; for a full mammalian transcriptome salmon or kallisto is
run instead when installed (``backends``).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import numpy as np

from .fastq import FastqError, open_text, read_fastq

__all__ = ["KmerIndex", "QuantResult", "GeneTable", "QuantError", "FragmentLengths",
           "read_fasta", "canonical_kmers", "effective_lengths", "em", "quantify",
           "read_tx2gene", "summarize_to_gene", "read_abundance", "write_abundance",
           "MAX_FRAG_LEN"]

MAX_FRAG_LEN = 1000                     # kallisto's: no fragment is this long or longer
ALPHA_LIMIT = 1e-7
ALPHA_CHANGE_LIMIT = 1e-2
ALPHA_CHANGE = 1e-2
MIN_ROUNDS = 50
MAX_ROUNDS = 10_000
MIN_PAIRS_FOR_FLD = 100                 # fewer unique pairs: use the given distribution
SPAN_SDS = 5.0

_CODE = np.full(256, 4, dtype=np.int64)
for _i, _b in enumerate("ACGT"):
    _CODE[ord(_b)] = _i
    _CODE[ord(_b.lower())] = _i
_CODE[ord("U")] = _CODE[ord("u")] = 3


class QuantError(ValueError):
    """An index, read set or annotation that cannot be quantified as given."""


# ============================================================== sequences and k-mers

def read_fasta(path: str | Path) -> Iterator[tuple[str, str]]:
    """(identifier, sequence) per record; the identifier is the first word of the header,
    cut at the first '|' as GENCODE headers require."""
    name, chunks = None, []
    with open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(chunks)
                header = line[1:].strip()
                if not header:
                    raise QuantError(f"{path}: a FASTA record has an empty header")
                name, chunks = header.split()[0].split("|")[0], []
            elif line:
                if name is None:
                    raise QuantError(f"{path}: sequence before the first '>' header")
                chunks.append(line.strip())
    if name is not None:
        yield name, "".join(chunks)


def _powers(k: int) -> np.ndarray:
    return 4 ** np.arange(k - 1, -1, -1, dtype=np.int64)


def _kmer_rows(codes: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """(canonical code, forward?) for every k-mer of each row of a reads x length array
    of base codes (0-3, 4 for anything else); code -1 for a window holding a 4."""
    n, length = codes.shape
    w = length - k + 1
    invalid = codes > 3
    clean = np.where(invalid, 0, codes)
    fwd = np.zeros((n, w), dtype=np.int64)
    rev = np.zeros((n, w), dtype=np.int64)
    for i in range(k):
        fwd = fwd * 4 + clean[:, i:i + w]
        rev = rev * 4 + (3 - clean[:, k - 1 - i:k - 1 - i + w])
    canon = np.minimum(fwd, rev)
    if invalid.any():
        cs = np.concatenate([np.zeros((n, 1), dtype=np.int64),
                             np.cumsum(invalid, axis=1)], axis=1)
        canon[(cs[:, k:] - cs[:, :w]) > 0] = -1
    return canon, fwd <= rev


def _codes(seqs: Sequence[str]) -> np.ndarray:
    """Base codes of equal-length sequences, one row each."""
    joined = "".join(seqs).encode("ascii", "replace")
    return _CODE[np.frombuffer(joined, dtype=np.uint8)].reshape(len(seqs), -1)


def _kmers(seq: str, k: int) -> tuple[np.ndarray, np.ndarray]:
    """(canonical code, forward?) for each k-mer of ``seq``; code -1 for a window holding
    anything but A, C, G, T. ``forward`` says the k-mer as read is the canonical one."""
    if len(seq) < k:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=bool)
    canon, fwd = _kmer_rows(_codes([seq]), k)
    return canon[0], fwd[0]


def canonical_kmers(seq: str, k: int) -> np.ndarray:
    """The canonical (smaller of forward and reverse-complement) 2-bit code of each
    k-mer of ``seq``, -1 for a window holding anything but A, C, G, T."""
    return _kmers(seq, k)[0]


# ============================================================== the index

@dataclass
class _Hit:
    """The first indexed k-mer of a read: where it is in the read and in the index."""

    offset: int
    forward: bool
    key: int
    length: int


@dataclass
class KmerIndex:
    """Canonical k-mer -> equivalence class of transcripts, and each k-mer's place."""

    k: int
    transcripts: tuple[str, ...]
    lengths: np.ndarray                   # per transcript
    keys: np.ndarray                      # sorted distinct canonical k-mers (int64)
    values: np.ndarray                    # class id per key (int32)
    classes: list[tuple[int, ...]]        # class id -> sorted transcript indices
    occ_start: np.ndarray                 # key i occurs in rows occ_start[i]:occ_start[i+1]
    occ_tx: np.ndarray                    # transcript of each occurrence (sorted per key)
    occ_pos: np.ndarray                   # first position of the k-mer in it
    occ_fwd: np.ndarray                   # the k-mer is canonical as the transcript reads
    digest: str                           # sha256 of k and every (id, sequence)

    @classmethod
    def build(cls, records: Iterable[tuple[str, str]], *, k: int = 31,
              max_kmers: int = 50_000_000) -> "KmerIndex":
        if not 5 <= k <= 31 or k % 2 == 0:
            raise QuantError("k must be odd and between 5 and 31")
        names: list[str] = []
        lengths: list[int] = []
        parts: list[tuple[np.ndarray, ...]] = []
        digest = hashlib.sha256(f"k={k}\n".encode())
        seen = set()
        total = 0
        for name, seq in records:
            if name in seen:
                raise QuantError(f"transcript {name} appears twice")
            seen.add(name)
            seq = seq.upper()
            digest.update(f">{name}\n{seq}\n".encode())
            t = len(names)
            names.append(name)
            lengths.append(len(seq))
            km, fwd = _kmers(seq, k)
            ok = km >= 0
            uniq, first = np.unique(km[ok], return_index=True)
            pos = np.flatnonzero(ok)[first]
            total += len(uniq)
            if total > max_kmers:
                raise QuantError(f"the transcriptome holds more than {max_kmers:,} k-mers; "
                                 "use salmon or kallisto for a transcriptome this size")
            parts.append((uniq, np.full(len(uniq), t, dtype=np.int32),
                          pos.astype(np.int32), fwd[pos]))
        if not names:
            raise QuantError("the transcriptome is empty")
        keys = np.concatenate([p[0] for p in parts])
        txs = np.concatenate([p[1] for p in parts])
        poss = np.concatenate([p[2] for p in parts])
        fwds = np.concatenate([p[3] for p in parts])
        order = np.lexsort((txs, keys))
        keys, txs, poss, fwds = keys[order], txs[order], poss[order], fwds[order]
        uniq, start = np.unique(keys, return_index=True)
        stop = np.append(start[1:], len(keys))
        values = np.empty(len(uniq), dtype=np.int32)
        single = (stop - start) == 1
        values[single] = txs[start[single]]            # class t is transcript t alone
        classes: list[tuple[int, ...]] = [(t,) for t in range(len(names))]
        lookup = {c: i for i, c in enumerate(classes)}
        for i in np.flatnonzero(~single):
            members = tuple(int(t) for t in txs[start[i]:stop[i]])
            cid = lookup.get(members)
            if cid is None:
                cid = lookup[members] = len(classes)
                classes.append(members)
            values[i] = cid
        return cls(k=k, transcripts=tuple(names), lengths=np.array(lengths, dtype=np.int64),
                   keys=uniq.astype(np.int64), values=values, classes=classes,
                   occ_start=np.append(start, len(keys)).astype(np.int64), occ_tx=txs,
                   occ_pos=poss, occ_fwd=fwds, digest=digest.hexdigest())

    @classmethod
    def from_fasta(cls, path: str | Path, *, k: int = 31, **kw: Any) -> "KmerIndex":
        return cls.build(read_fasta(path), k=k, **kw)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        flat = np.array([t for c in self.classes for t in c], dtype=np.int32)
        offsets = np.cumsum([0] + [len(c) for c in self.classes]).astype(np.int64)
        meta = json.dumps({"k": self.k, "transcripts": list(self.transcripts),
                           "digest": self.digest, "format": 1})
        with path.open("wb") as fh:
            np.savez_compressed(fh, keys=self.keys, values=self.values, lengths=self.lengths,
                                flat=flat, offsets=offsets, occ_start=self.occ_start,
                                occ_tx=self.occ_tx, occ_pos=self.occ_pos,
                                occ_fwd=self.occ_fwd, meta=np.array(meta))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "KmerIndex":
        with np.load(Path(path), allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            if meta.get("format") != 1:
                raise QuantError(f"{path} is not an index this version reads")
            flat, offsets = z["flat"], z["offsets"]
            classes = [tuple(int(t) for t in flat[offsets[i]:offsets[i + 1]])
                       for i in range(len(offsets) - 1)]
            return cls(k=int(meta["k"]), transcripts=tuple(meta["transcripts"]),
                       lengths=z["lengths"], keys=z["keys"], values=z["values"],
                       classes=classes, occ_start=z["occ_start"], occ_tx=z["occ_tx"],
                       occ_pos=z["occ_pos"], occ_fwd=z["occ_fwd"], digest=meta["digest"])

    def _lookup(self, kmers: np.ndarray) -> np.ndarray:
        """Key index per k-mer, -1 when the index does not hold it."""
        out = np.full(len(kmers), -1, dtype=np.int64)
        if not len(kmers) or not len(self.keys):
            return out
        pos = np.minimum(np.searchsorted(self.keys, kmers), len(self.keys) - 1)
        hit = (self.keys[pos] == kmers) & (kmers >= 0)
        out[hit] = pos[hit]
        return out

    def _scan(self, seqs: Sequence[str]) -> list[tuple[set[int], _Hit | None]]:
        """For each read: the classes of its indexed k-mers, and its first indexed k-mer.

        Reads of one length are handled together, as arrays."""
        out: list[tuple[set[int], _Hit | None]] = [(set(), None)] * len(seqs)
        by_length: dict[int, list[int]] = {}
        for i, seq in enumerate(seqs):
            by_length.setdefault(len(seq), []).append(i)
        for length, rows in by_length.items():
            if length < self.k:
                continue
            canon, fwd = _kmer_rows(_codes([seqs[i] for i in rows]), self.k)
            at = self._lookup(canon.ravel()).reshape(canon.shape)
            found = at >= 0
            first = np.argmax(found, axis=1)
            cls = np.where(found, self.values[np.where(found, at, 0)], -1)
            top = cls.max(axis=1)
            bottom = np.where(found, cls, np.iinfo(np.int64).max).min(axis=1)
            for r, i in enumerate(rows):
                if top[r] < 0:
                    continue
                f = int(first[r])
                hit = _Hit(offset=f, forward=bool(fwd[r, f]), key=int(at[r, f]),
                           length=length)
                classes = ({int(top[r])} if top[r] == bottom[r]
                           else {int(c) for c in np.unique(cls[r][found[r]])})
                out[i] = (classes, hit)
        return out

    def _interval(self, t: int, hit: _Hit) -> tuple[int, int]:
        """Where on transcript ``t`` the read of ``hit`` lies: [start, end)."""
        lo, hi = int(self.occ_start[hit.key]), int(self.occ_start[hit.key + 1])
        row = lo + int(np.searchsorted(self.occ_tx[lo:hi], t))
        p = int(self.occ_pos[row])
        if bool(self.occ_fwd[row]) == hit.forward:        # read runs along the transcript
            start = p - hit.offset
            return start, start + hit.length
        end = p + self.k + hit.offset
        return end - hit.length, end

    def compatible(self, *seqs: str) -> tuple[int, ...] | None:
        """The transcripts every indexed k-mer of the read (or pair) lies in; None when
        no k-mer is indexed or the k-mers disagree."""
        return self._combine(self._scan(seqs))[0]

    def _combine(self, mates: Sequence[tuple[set[int], _Hit | None]]
                 ) -> tuple[tuple[int, ...] | None, dict[int, int]]:
        """(class, fragment span per transcript of the class, for a located pair)."""
        found: set[int] = set()
        hits = []
        for classes, hit in mates:
            found |= classes
            if hit is not None:
                hits.append(hit)
        if not found:
            return None, {}
        if len(found) == 1:
            ec = self.classes[next(iter(found))]
        else:
            ordered = sorted(found, key=lambda c: len(self.classes[c]))
            common = set(self.classes[ordered[0]])
            for c in ordered[1:]:
                common.intersection_update(self.classes[c])
                if not common:
                    return None, {}
            ec = tuple(sorted(common))
        spans: dict[int, int] = {}
        if len(hits) == 2:
            for t in ec:
                (s1, e1), (s2, e2) = self._interval(t, hits[0]), self._interval(t, hits[1])
                spans[t] = max(e1, e2) - min(s1, s2)
        return ec, spans


# ============================================================== fragment lengths, EM

@dataclass(frozen=True)
class FragmentLengths:
    """A fragment-length distribution over 0..MAX_FRAG_LEN-1."""

    weights: np.ndarray
    source: str                               # "estimated" or "assumed"

    @classmethod
    def gaussian(cls, mean: float, sd: float) -> "FragmentLengths":
        if mean <= 0 or sd <= 0:
            raise QuantError("the fragment length mean and SD must be positive")
        x = (np.arange(MAX_FRAG_LEN, dtype=float) - mean) / sd
        return cls(np.exp(-0.5 * x * x) / sd, "assumed")

    @classmethod
    def from_spans(cls, spans: Counter[int]) -> "FragmentLengths":
        w = np.zeros(MAX_FRAG_LEN)
        for span, n in spans.items():
            if 0 <= span < MAX_FRAG_LEN:
                w[span] += n
        return cls(w, "estimated")

    @property
    def mean(self) -> float:
        return float((self.weights * np.arange(MAX_FRAG_LEN)).sum() / self.weights.sum())

    @property
    def sd(self) -> float:
        x = np.arange(MAX_FRAG_LEN)
        return math.sqrt(float((self.weights * (x - self.mean) ** 2).sum()
                               / self.weights.sum()))

    def truncated_means(self) -> np.ndarray:
        """kallisto's mean_fl_trunc: the mean fragment length among fragments <= L."""
        x = np.arange(MAX_FRAG_LEN, dtype=float)
        mass = np.cumsum(self.weights * x)
        density = np.cumsum(self.weights)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(mass > 0, mass / np.where(density > 0, density, 1.0), 0.0)


def effective_lengths(lengths: np.ndarray, mean: float | None = None,
                      sd: float | None = None, *,
                      distribution: FragmentLengths | None = None) -> np.ndarray:
    """kallisto's effective lengths: length - truncated mean fragment length + 1, or the
    length itself when that is below one."""
    fld = distribution or FragmentLengths.gaussian(float(mean), float(sd))
    lengths = np.asarray(lengths, dtype=float)
    trunc = fld.truncated_means()
    eff = lengths - trunc[np.minimum(lengths.astype(np.int64), MAX_FRAG_LEN - 1)] + 1.0
    return np.where(eff < 1.0, lengths, eff)


def em(ec_counts: Mapping[tuple[int, ...], float], eff_lengths: np.ndarray, *,
       min_rounds: int = MIN_ROUNDS, max_rounds: int = MAX_ROUNDS) -> tuple[np.ndarray, int]:
    """kallisto's EM: (estimated reads per transcript, rounds run).

    Each round gives every equivalence class's reads to its transcripts in proportion to
    abundance / effective length. It stops one round after no estimate above 0.01 reads
    changed by more than 1% (after at least ``min_rounds``), zeroing estimates below
    1e-8 before that last round, as kallisto does.
    """
    n = len(eff_lengths)
    weights = 1.0 / np.asarray(eff_lengths, dtype=float)
    items = [(ec, float(c)) for ec, c in ec_counts.items() if c > 0]
    alpha = np.full(n, 1.0 / n)
    if not items:
        return np.zeros(n), 0
    sizes = np.array([len(ec) for ec, _ in items])
    cols = np.fromiter((t for ec, _ in items for t in ec), dtype=np.int64,
                       count=int(sizes.sum()))
    rows = np.repeat(np.arange(len(items)), sizes)
    reads = np.array([c for _, c in items])[rows]
    final = False
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        share = alpha[cols] * weights[cols]
        denom = np.bincount(rows, weights=share, minlength=len(items))[rows]
        contrib = np.where(denom > 0, reads * share / np.where(denom > 0, denom, 1.0), 0.0)
        nxt = np.bincount(cols, weights=contrib, minlength=n)
        changing = np.sum((nxt > ALPHA_CHANGE_LIMIT)
                          & (np.abs(nxt - alpha) / np.where(nxt > 0, nxt, 1.0) > ALPHA_CHANGE))
        alpha = nxt
        if final:
            break
        if changing == 0 and rounds - 1 > min_rounds:
            final = True
            alpha = np.where(alpha < ALPHA_LIMIT / 10.0, 0.0, alpha)
    return alpha, rounds


# ============================================================== quantifying a sample

@dataclass
class QuantResult:
    """Per-transcript estimates for one sample, in kallisto's abundance.tsv terms."""

    transcripts: tuple[str, ...]
    lengths: np.ndarray
    eff_lengths: np.ndarray
    est_counts: np.ndarray
    tpm: np.ndarray
    run: dict[str, Any] = field(default_factory=dict)

    def rows(self) -> list[dict[str, Any]]:
        return [{"target_id": t, "length": int(self.lengths[i]),
                 "eff_length": float(self.eff_lengths[i]),
                 "est_counts": float(self.est_counts[i]), "tpm": float(self.tpm[i])}
                for i, t in enumerate(self.transcripts)]


def _tpm(counts: np.ndarray, eff: np.ndarray) -> np.ndarray:
    rate = counts / eff
    total = rate.sum()
    return rate / total * 1e6 if total > 0 else np.zeros_like(rate)


def quantify(index: KmerIndex, reads1: str | Path, reads2: str | Path | None = None, *,
             fragment_mean: float = 200.0, fragment_sd: float = 30.0,
             span_check: bool = True, chunk: int = 2048) -> QuantResult:
    """Pseudoalign a FASTQ file (or pair) and estimate transcript abundances.

    For pairs the fragment distribution is estimated from pairs in one transcript when
    there are at least 100 of them; otherwise, and for single reads, it is the
    truncated Gaussian ``fragment_mean`` / ``fragment_sd``.
    """
    simple: Counter[tuple[int, ...]] = Counter()
    located: Counter[tuple[tuple[int, ...], tuple[int, ...]]] = Counter()
    unique_spans: Counter[int] = Counter()
    reads = unaligned = 0

    def tally(chunk1: list[str], chunk2: list[str] | None) -> None:
        nonlocal unaligned
        scanned1 = index._scan(chunk1)
        scanned2 = index._scan(chunk2) if chunk2 is not None else None
        for j, mate1 in enumerate(scanned1):
            mates = [mate1] if scanned2 is None else [mate1, scanned2[j]]
            ec, spans = index._combine(mates)
            if ec is None:
                unaligned += 1
            elif len(ec) == 1:
                simple[ec] += 1
                if spans:
                    unique_spans[spans[ec[0]]] += 1
            elif spans:
                located[(ec, tuple(spans[t] for t in ec))] += 1
            else:
                simple[ec] += 1

    first = read_fastq(reads1)
    second = read_fastq(reads2) if reads2 is not None else None
    buf1: list[str] = []
    buf2: list[str] = []
    for rec in first:
        buf1.append(rec.seq)
        if second is not None:
            mate = next(second, None)
            if mate is None:
                raise FastqError(f"{reads2} has fewer reads than {reads1}")
            buf2.append(mate.seq)
        reads += 1
        if len(buf1) == chunk:
            tally(buf1, buf2 if second is not None else None)
            buf1, buf2 = [], []
    if buf1:
        tally(buf1, buf2 if second is not None else None)
    if second is not None and next(second, None) is not None:
        raise FastqError(f"{reads2} has more reads than {reads1}")
    if reads == 0:
        raise QuantError(f"{reads1} holds no reads")
    pairs_in_one = sum(unique_spans.values())
    if reads2 is not None and pairs_in_one >= MIN_PAIRS_FOR_FLD:
        fld = FragmentLengths.from_spans(unique_spans)
    else:
        fld = FragmentLengths.gaussian(fragment_mean, fragment_sd)
    bound = fld.mean + SPAN_SDS * fld.sd
    ecs: Counter[tuple[int, ...]] = Counter(simple)
    narrowed = 0
    for (ec, spans), n in located.items():
        keep = tuple(t for t, s in zip(ec, spans) if s <= bound) if span_check else ec
        if keep and keep != ec:
            narrowed += n
            ecs[keep] += n
        else:
            ecs[ec] += n
    eff = effective_lengths(index.lengths, distribution=fld)
    counts, rounds = em(ecs, eff)
    run = {"method": "k-mer pseudoalignment + EM (kallisto's algorithm)",
           "index_digest": index.digest, "k": index.k, "reads": reads,
           "pseudoaligned": reads - unaligned,
           "pseudoaligned_fraction": round((reads - unaligned) / reads, 6),
           "unique": sum(c for ec, c in ecs.items() if len(ec) == 1),
           "equivalence_classes": len(ecs), "em_rounds": rounds,
           "paired": reads2 is not None,
           "fragment_distribution": fld.source,
           "fragment_mean": round(fld.mean, 3), "fragment_sd": round(fld.sd, 3),
           "pairs_in_one_transcript": pairs_in_one,
           "span_check": bool(span_check and reads2 is not None),
           "span_bound": round(bound, 3) if reads2 is not None else None,
           "pairs_narrowed_by_span": narrowed}
    return QuantResult(transcripts=index.transcripts, lengths=index.lengths,
                       eff_lengths=eff, est_counts=counts, tpm=_tpm(counts, eff), run=run)


def write_abundance(result: QuantResult, path: str | Path) -> Path:
    """kallisto's abundance.tsv."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("target_id\tlength\teff_length\test_counts\ttpm\n")
        for r in result.rows():
            fh.write(f"{r['target_id']}\t{r['length']}\t{r['eff_length']:.6g}\t"
                     f"{r['est_counts']:.6g}\t{r['tpm']:.6g}\n")
    return path


def read_abundance(path: str | Path) -> QuantResult:
    """kallisto's abundance.tsv or salmon's quant.sf, whichever ``path`` is."""
    with Path(path).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if not rows:
        raise QuantError(f"{path} lists no transcripts")
    cols = set(rows[0])
    if {"target_id", "eff_length", "est_counts", "tpm"} <= cols:
        key, ln, eff, cnt, tpm, tool = "target_id", "length", "eff_length", "est_counts", \
            "tpm", "kallisto"
    elif {"Name", "EffectiveLength", "NumReads", "TPM"} <= cols:
        key, ln, eff, cnt, tpm, tool = "Name", "Length", "EffectiveLength", "NumReads", \
            "TPM", "salmon"
    else:
        raise QuantError(f"{path} is neither a kallisto abundance.tsv nor a salmon quant.sf")
    return QuantResult(
        transcripts=tuple(r[key] for r in rows),
        lengths=np.array([float(r[ln]) for r in rows]),
        eff_lengths=np.array([float(r[eff]) for r in rows]),
        est_counts=np.array([float(r[cnt]) for r in rows]),
        tpm=np.array([float(r[tpm]) for r in rows]),
        run={"read_from": str(path), "format": tool})


# ============================================================== gene totals (tximport)

_ATTR = re.compile(r'(\S+)\s+"([^"]*)"')


def read_tx2gene(path: str | Path) -> dict[str, tuple[str, str]]:
    """transcript -> (gene id, gene name), from a GTF or from a table of two or three
    columns (transcript, gene[, gene name]) separated by tabs or commas."""
    mapping: dict[str, tuple[str, str]] = {}
    gtf: bool | None = None
    with open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            cols = line.split("\t")
            if gtf is None:
                gtf = len(cols) >= 9
            if gtf:
                if len(cols) < 9:
                    raise QuantError(f"{path}: a GTF line has {len(cols)} columns")
                attrs = dict(_ATTR.findall(cols[8]))
                tx, gene = attrs.get("transcript_id"), attrs.get("gene_id")
                if tx and gene:
                    name = attrs.get("gene_name", gene)
                    mapping.setdefault(tx, (gene, name))
                    version = attrs.get("transcript_version")
                    if version and not tx.endswith(f".{version}"):
                        mapping.setdefault(f"{tx}.{version}", (gene, name))
                continue
            cells = cols if len(cols) > 1 else line.split(",")
            if len(cells) < 2:
                raise QuantError(f"{path}: {line!r} names no gene")
            if cells[0].strip().lower() in {"transcript", "tx", "tx_id", "transcript_id",
                                            "txname", "target_id"}:
                continue
            gene = cells[1].strip()
            mapping.setdefault(cells[0].strip(),
                               (gene, cells[2].strip() if len(cells) > 2 else gene))
    if not mapping:
        raise QuantError(f"{path} maps no transcript to a gene")
    return mapping


def _strip_version(name: str) -> str:
    return re.sub(r"\.\d+$", "", name)


@dataclass
class GeneTable:
    """Genes x samples: counts for DESeq2, TPM, and average transcript lengths."""

    genes: tuple[str, ...]
    names: tuple[str, ...]
    samples: tuple[str, ...]
    counts: np.ndarray
    tpm: np.ndarray
    length: np.ndarray
    counts_from_abundance: str
    unmatched_transcripts: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"genes": len(self.genes), "samples": list(self.samples),
                "counts_from_abundance": self.counts_from_abundance,
                "unmatched_transcripts": self.unmatched_transcripts,
                "library_sizes": dict(zip(self.samples,
                                          map(float, self.counts.sum(axis=0)))),
                "notes": list(self.notes)}

    def write(self, path: str | Path, *, what: str = "counts") -> Path:
        matrix = {"counts": self.counts, "tpm": self.tpm, "length": self.length}[what]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write("gene_id\tgene_name\t" + "\t".join(self.samples) + "\n")
            for i, g in enumerate(self.genes):
                fh.write(f"{g}\t{self.names[i]}\t"
                         + "\t".join(f"{v:.6g}" for v in matrix[i]) + "\n")
        return path


def summarize_to_gene(results: Sequence[QuantResult], samples: Sequence[str],
                      tx2gene: Mapping[str, tuple[str, str]], *,
                      counts_from_abundance: str = "lengthScaledTPM") -> GeneTable:
    """tximport's summarizeToGene for several samples quantified on one transcriptome."""
    if counts_from_abundance not in ("no", "scaledTPM", "lengthScaledTPM"):
        raise QuantError("counts_from_abundance is 'no', 'scaledTPM' or 'lengthScaledTPM'")
    if len(results) != len(samples) or not results:
        raise QuantError("one quantification per sample is needed")
    txs = results[0].transcripts
    if any(r.transcripts != txs for r in results[1:]):
        raise QuantError("the samples were quantified against different transcriptomes")
    lookup = dict(tx2gene)
    stripped = {_strip_version(t): v for t, v in tx2gene.items()}
    gene_of: list[tuple[str, str] | None] = []
    for t in txs:
        gene_of.append(lookup.get(t) or stripped.get(_strip_version(t)))
    unmatched = sum(1 for g in gene_of if g is None)
    if unmatched == len(txs):
        raise QuantError("no transcript of the quantification is in the annotation")
    keep = [i for i, g in enumerate(gene_of) if g is not None]
    genes = sorted({gene_of[i][0] for i in keep})
    row = {g: k for k, g in enumerate(genes)}
    names = {gene_of[i][0]: gene_of[i][1] for i in keep}
    gi = np.array([row[gene_of[i][0]] for i in keep])
    ng, ns = len(genes), len(results)
    counts = np.zeros((ng, ns))
    tpm = np.zeros((ng, ns))
    weighted = np.zeros((ng, ns))
    tx_len = np.column_stack([r.eff_lengths[keep] for r in results])
    for j, r in enumerate(results):
        np.add.at(counts[:, j], gi, r.est_counts[keep])
        np.add.at(tpm[:, j], gi, r.tpm[keep])
        np.add.at(weighted[:, j], gi, r.tpm[keep] * r.eff_lengths[keep])
    with np.errstate(divide="ignore", invalid="ignore"):
        length = weighted / tpm
    # a gene with no abundance in a sample: tximport's replaceMissingLength
    ave_tx = tx_len.mean(axis=1)
    sums = np.zeros(ng)
    nums = np.zeros(ng)
    np.add.at(sums, gi, ave_tx)
    np.add.at(nums, gi, 1)
    ave_gene = sums / nums
    for i in np.flatnonzero(~np.isfinite(length).all(axis=1)):
        bad = ~np.isfinite(length[i])
        length[i, bad] = (ave_gene[i] if bad.all()
                          else math.exp(float(np.mean(np.log(length[i, ~bad])))))
    notes = []
    if unmatched:
        notes.append(f"{unmatched} transcripts are not in the annotation and are left out")
    if counts_from_abundance != "no":
        library = counts.sum(axis=0)
        new = tpm * length.mean(axis=1, keepdims=True) \
            if counts_from_abundance == "lengthScaledTPM" else tpm.copy()
        total = new.sum(axis=0)
        counts = np.where(total > 0, new * (library / np.where(total > 0, total, 1.0)), 0.0)
        notes.append(f"counts rebuilt from abundance ({counts_from_abundance}), "
                     "as tximport does")
    return GeneTable(genes=tuple(genes), names=tuple(names[g] for g in genes),
                     samples=tuple(samples), counts=counts, tpm=tpm, length=length,
                     counts_from_abundance=counts_from_abundance,
                     unmatched_transcripts=unmatched, notes=notes)
