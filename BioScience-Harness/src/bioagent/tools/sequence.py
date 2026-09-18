"""Nucleotide sequence tools. Pure Python, deterministic, offline.

Every function takes keyword arguments and returns a JSON-serialisable dict, so it can be
a BioScience ``python`` component, run in a PSH child process, and have its output
schema-checked by the loop's evaluator. Input is validated and refused with a reason;
nothing is guessed from a malformed sequence.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

__all__ = ["reverse_complement", "transcribe", "translate", "gc_content", "find_orfs",
           "kmer_counts", "hamming_distance", "edit_distance", "codon_usage",
           "melting_temperature", "restriction_sites", "nucleic_acid_weight",
           "CODON_TABLE", "RESTRICTION_ENZYMES"]

_COMPLEMENT = str.maketrans("ACGTUNRYKMSWBDHVacgtunrykmswbdhv",
                            "TGCAANYRMKSWVHDBtgcaanyrmkswvhdb")
_VALID_NT = re.compile(r"^[ACGTUNRYKMSWBDHV]+$")

#: The standard genetic code (NCBI translation table 1). ``*`` is a stop.
CODON_TABLE: Mapping[str, str] = {
    "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L", "CTT": "L", "CTC": "L", "CTA": "L",
    "CTG": "L", "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M", "GTT": "V", "GTC": "V",
    "GTA": "V", "GTG": "V", "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S", "CCT": "P",
    "CCC": "P", "CCA": "P", "CCG": "P", "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
    "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A", "TAT": "Y", "TAC": "Y", "TAA": "*",
    "TAG": "*", "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q", "AAT": "N", "AAC": "N",
    "AAA": "K", "AAG": "K", "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E", "TGT": "C",
    "TGC": "C", "TGA": "*", "TGG": "W", "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
    "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R", "GGT": "G", "GGC": "G", "GGA": "G",
    "GGG": "G",
}

#: Recognition sites of common type II restriction enzymes (5'→3').
RESTRICTION_ENZYMES: Mapping[str, str] = {
    "EcoRI": "GAATTC", "BamHI": "GGATCC", "HindIII": "AAGCTT", "NotI": "GCGGCCGC",
    "XhoI": "CTCGAG", "PstI": "CTGCAG", "SmaI": "CCCGGG", "KpnI": "GGTACC", "SacI": "GAGCTC",
    "XbaI": "TCTAGA", "NcoI": "CCATGG", "NdeI": "CATATG", "BglII": "AGATCT", "SalI": "GTCGAC",
    "SpeI": "ACTAGT", "EcoRV": "GATATC", "ClaI": "ATCGAT", "NheI": "GCTAGC", "ApaI": "GGGCCC",
    "SphI": "GCATGC",
}


def _clean(sequence: Any, *, what: str = "sequence") -> str:
    if not isinstance(sequence, str):
        raise ValueError(f"{what} must be a string, got {type(sequence).__name__}")
    text = "".join(sequence.split()).upper()
    if not text:
        raise ValueError(f"{what} is empty")
    if not _VALID_NT.match(text):
        bad = sorted({c for c in text if c not in "ACGTUNRYKMSWBDHV"})
        raise ValueError(f"{what} contains characters that are not IUPAC nucleotides: {bad}")
    return text


def reverse_complement(sequence: str) -> dict[str, Any]:
    """Reverse complement of a DNA or RNA sequence (IUPAC ambiguity codes honoured)."""
    seq = _clean(sequence)
    return {"sequence": seq.translate(_COMPLEMENT)[::-1], "length": len(seq)}


def transcribe(sequence: str) -> dict[str, Any]:
    """DNA coding strand to mRNA (T → U)."""
    seq = _clean(sequence)
    return {"rna": seq.replace("T", "U"), "length": len(seq)}


def translate(sequence: str, frame: int = 0, to_stop: bool = False) -> dict[str, Any]:
    """Translate with the standard genetic code; ``X`` for a codon with ambiguity."""
    seq = _clean(sequence).replace("U", "T")
    if frame not in (0, 1, 2):
        raise ValueError("frame must be 0, 1 or 2")
    protein: list[str] = []
    stops = 0
    for i in range(frame, len(seq) - 2, 3):
        codon = seq[i:i + 3]
        aa = CODON_TABLE.get(codon, "X")
        if aa == "*":
            stops += 1
            if to_stop:
                break
        protein.append(aa)
    return {"protein": "".join(protein), "codons": len(protein), "stop_codons": stops,
            "frame": frame}


def gc_content(sequence: str, window: int = 0) -> dict[str, Any]:
    """GC fraction overall and, with ``window`` > 0, in sliding windows of that width."""
    seq = _clean(sequence)
    gc = sum(seq.count(b) for b in "GCS")
    out: dict[str, Any] = {"gc_fraction": round(gc / len(seq), 6),
                           "gc_percent": round(100.0 * gc / len(seq), 3), "length": len(seq)}
    if window > 0:
        if window > len(seq):
            raise ValueError("window is longer than the sequence")
        out["window"] = window
        out["windows"] = [round(sum(seq[i:i + window].count(b) for b in "GCS") / window, 4)
                          for i in range(0, len(seq) - window + 1)]
    return out


def find_orfs(sequence: str, min_length_aa: int = 30, both_strands: bool = True
              ) -> dict[str, Any]:
    """Open reading frames (ATG … stop) of at least ``min_length_aa`` codons, both strands."""
    seq = _clean(sequence).replace("U", "T")
    if min_length_aa < 1:
        raise ValueError("min_length_aa must be at least 1")
    strands = [("+", seq)]
    if both_strands:
        strands.append(("-", seq.translate(_COMPLEMENT)[::-1]))
    found: list[dict[str, Any]] = []
    for strand, s in strands:
        for frame in range(3):
            i = frame
            while i <= len(s) - 3:
                if s[i:i + 3] == "ATG":
                    j = i
                    protein = []
                    while j <= len(s) - 3:
                        aa = CODON_TABLE.get(s[j:j + 3], "X")
                        if aa == "*":
                            break
                        protein.append(aa)
                        j += 3
                    else:
                        j = None                           # ran off the end: no stop
                    if j is not None and len(protein) >= min_length_aa:
                        found.append({"strand": strand, "frame": frame, "start": i,
                                      "end": j + 3, "length_aa": len(protein),
                                      "protein": "".join(protein)})
                        i = j + 3
                        continue
                i += 3
    found.sort(key=lambda o: -o["length_aa"])
    return {"orfs": found, "count": len(found), "min_length_aa": min_length_aa}


def kmer_counts(sequence: str, k: int = 3, top: int = 20) -> dict[str, Any]:
    """Counts of k-mers, most frequent first."""
    seq = _clean(sequence)
    if k < 1 or k > len(seq):
        raise ValueError("k must be between 1 and the sequence length")
    counts: dict[str, int] = {}
    for i in range(len(seq) - k + 1):
        counts[seq[i:i + k]] = counts.get(seq[i:i + k], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"k": k, "total": len(seq) - k + 1, "distinct": len(counts),
            "top": [[kmer, n] for kmer, n in ranked[:max(top, 0)]]}


def hamming_distance(a: str, b: str) -> dict[str, Any]:
    """Mismatches between two equal-length sequences."""
    x, y = _clean(a, what="a"), _clean(b, what="b")
    if len(x) != len(y):
        raise ValueError(f"sequences differ in length ({len(x)} vs {len(y)})")
    d = sum(1 for p, q in zip(x, y) if p != q)
    return {"distance": d, "length": len(x), "identity": round(1 - d / len(x), 6)}


def edit_distance(a: str, b: str) -> dict[str, Any]:
    """Levenshtein distance between two strings (any alphabet)."""
    if not isinstance(a, str) or not isinstance(b, str):
        raise ValueError("a and b must be strings")
    x, y = a.strip().upper(), b.strip().upper()
    prev = list(range(len(y) + 1))
    for i, ca in enumerate(x, 1):
        cur = [i]
        for j, cb in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return {"distance": prev[-1], "length_a": len(x), "length_b": len(y)}


def codon_usage(sequence: str, frame: int = 0) -> dict[str, Any]:
    """Codon counts and per-amino-acid relative usage in one reading frame."""
    seq = _clean(sequence).replace("U", "T")
    if frame not in (0, 1, 2):
        raise ValueError("frame must be 0, 1 or 2")
    codons: dict[str, int] = {}
    for i in range(frame, len(seq) - 2, 3):
        c = seq[i:i + 3]
        if c in CODON_TABLE:
            codons[c] = codons.get(c, 0) + 1
    by_aa: dict[str, int] = {}
    for c, n in codons.items():
        by_aa[CODON_TABLE[c]] = by_aa.get(CODON_TABLE[c], 0) + n
    relative = {c: round(n / by_aa[CODON_TABLE[c]], 4) for c, n in codons.items()}
    return {"codons": dict(sorted(codons.items())), "amino_acids": dict(sorted(by_aa.items())),
            "relative_usage": dict(sorted(relative.items())), "total_codons": sum(codons.values())}


def melting_temperature(sequence: str, method: str = "auto", sodium_mM: float = 50.0
                        ) -> dict[str, Any]:
    """Primer melting temperature.

    ``wallace``: Tm = 2(A+T) + 4(G+C), the rule of thumb for oligos under 14 nt.
    ``salt_adjusted``: Tm = 81.5 + 16.6·log10([Na+]) + 0.41·%GC − 675/N (Howley et al.).
    ``auto`` picks Wallace below 14 nt and salt-adjusted otherwise.
    """
    seq = _clean(sequence).replace("U", "T")
    if any(c not in "ACGT" for c in seq):
        raise ValueError("melting temperature needs an unambiguous ACGT sequence")
    if sodium_mM <= 0:
        raise ValueError("sodium_mM must be positive")
    chosen = method
    if method == "auto":
        chosen = "wallace" if len(seq) < 14 else "salt_adjusted"
    at = seq.count("A") + seq.count("T")
    gc = seq.count("G") + seq.count("C")
    if chosen == "wallace":
        tm = 2.0 * at + 4.0 * gc
    elif chosen == "salt_adjusted":
        tm = (81.5 + 16.6 * math.log10(sodium_mM / 1000.0) + 0.41 * (100.0 * gc / len(seq))
              - 675.0 / len(seq))
    else:
        raise ValueError("method must be 'auto', 'wallace' or 'salt_adjusted'")
    return {"tm_celsius": round(tm, 2), "method": chosen, "length": len(seq),
            "gc_percent": round(100.0 * gc / len(seq), 2), "sodium_mM": sodium_mM}


def restriction_sites(sequence: str, enzymes: Sequence[str] | None = None) -> dict[str, Any]:
    """0-based positions of recognition sites for the named (or all known) enzymes."""
    seq = _clean(sequence).replace("U", "T")
    names = list(enzymes) if enzymes else list(RESTRICTION_ENZYMES)
    unknown = [n for n in names if n not in RESTRICTION_ENZYMES]
    if unknown:
        raise ValueError(f"unknown enzyme(s) {unknown}; known: {sorted(RESTRICTION_ENZYMES)}")
    sites: dict[str, list[int]] = {}
    for name in names:
        site = RESTRICTION_ENZYMES[name]
        positions = [m.start() for m in re.finditer(f"(?={re.escape(site)})", seq)]
        if positions:
            sites[name] = positions
    return {"sites": sites, "length": len(seq),
            "enzymes_with_sites": len(sites), "enzymes_checked": len(names)}


def nucleic_acid_weight(sequence: str, kind: str = "dna") -> dict[str, Any]:
    """Molecular weight (g/mol) of a single-stranded oligo, IDT's linear formulas."""
    seq = _clean(sequence)
    if kind == "dna":
        seq = seq.replace("U", "T")
        masses = {"A": 313.21, "C": 289.18, "G": 329.21, "T": 304.2}
        offset = -61.96
    elif kind == "rna":
        seq = seq.replace("T", "U")
        masses = {"A": 329.21, "C": 305.18, "G": 345.21, "U": 306.17}
        offset = 159.0
    else:
        raise ValueError("kind must be 'dna' or 'rna'")
    if any(c not in masses for c in seq):
        raise ValueError("molecular weight needs an unambiguous sequence")
    mw = sum(masses[c] for c in seq) + offset
    return {"molecular_weight": round(mw, 2), "kind": kind, "length": len(seq)}
