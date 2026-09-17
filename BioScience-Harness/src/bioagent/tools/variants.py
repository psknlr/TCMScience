"""Variant tools: HGVS parsing, allele normalisation, allele frequencies, Ts/Tv."""

from __future__ import annotations

import math
import re
from typing import Any, Sequence

__all__ = ["parse_hgvs", "normalise_variant", "allele_frequencies", "transition_transversion"]

_HGVS = re.compile(r"^(?:(?P<reference>[A-Za-z0-9_.()-]+):)?(?P<type>[cgmnpr])\.(?P<body>.+)$")
_POS = r"[-*]?\d+(?:[+-]\d+)?"
_SUB = re.compile(rf"^(?P<start>{_POS})(?P<ref>[ACGTUN])>(?P<alt>[ACGTUN])$")
_DEL = re.compile(rf"^(?P<start>{_POS})(?:_(?P<end>{_POS}))?del(?P<seq>[ACGTUN]+)?$")
_DUP = re.compile(rf"^(?P<start>{_POS})(?:_(?P<end>{_POS}))?dup(?P<seq>[ACGTUN]+)?$")
_INS = re.compile(rf"^(?P<start>{_POS})_(?P<end>{_POS})ins(?P<seq>[ACGTUN]+)$")
_DELINS = re.compile(rf"^(?P<start>{_POS})(?:_(?P<end>{_POS}))?delins(?P<seq>[ACGTUN]+)$")
_PROT = re.compile(r"^(?P<ref>[A-Z][a-z]{2}|[A-Z*])(?P<pos>\d+)"
                   r"(?P<alt>[A-Z][a-z]{2}|Ter|[A-Z*]|fs(?:\*\d+|Ter\d+)?|=|del|dup)$")

_THREE = {"A": "Ala", "R": "Arg", "N": "Asn", "D": "Asp", "C": "Cys", "E": "Glu", "Q": "Gln",
          "G": "Gly", "H": "His", "I": "Ile", "L": "Leu", "K": "Lys", "M": "Met", "F": "Phe",
          "P": "Pro", "S": "Ser", "T": "Thr", "W": "Trp", "Y": "Tyr", "V": "Val", "*": "Ter",
          "X": "Ter"}
_ONE = {v: k for k, v in _THREE.items() if k not in ("X",)}


def _aa3(token: str) -> str:
    if token in ("*", "Ter", "X"):
        return "Ter"
    if len(token) == 1:
        if token not in _THREE:
            raise ValueError(f"unknown amino acid code {token!r}")
        return _THREE[token]
    if token not in _ONE:
        raise ValueError(f"unknown amino acid code {token!r}")
    return token


def parse_hgvs(hgvs: str) -> dict[str, Any]:
    """Parse the common HGVS shapes: substitution, deletion, duplication, insertion,
    deletion-insertion at the c./g./n./m./r. level, and substitution, frameshift,
    nonsense, synonymous, deletion and duplication at the p. level."""
    if not isinstance(hgvs, str) or not hgvs.strip():
        raise ValueError("hgvs must be a non-empty string")
    text = hgvs.strip()
    m = _HGVS.match(text)
    if not m:
        raise ValueError(f"{text!r} is not an HGVS expression of the form [ref:]t.change")
    reference, kind, body = m.group("reference") or "", m.group("type"), m.group("body")
    out: dict[str, Any] = {"input": text, "reference": reference, "level": kind}
    if kind == "p":
        pm = _PROT.match(body)
        if not pm:
            raise ValueError(f"unsupported protein-level change {body!r}")
        ref3 = _aa3(pm.group("ref"))
        alt = pm.group("alt")
        if alt.startswith("fs"):
            change, alt3 = "frameshift", None
        elif alt == "=":
            change, alt3 = "synonymous", ref3
        elif alt in ("del", "dup"):
            change, alt3 = "deletion" if alt == "del" else "duplication", None
        else:
            alt3 = _aa3(alt)
            change = "nonsense" if alt3 == "Ter" else "substitution"
        out.update({"change": change, "position": int(pm.group("pos")), "ref": ref3,
                    "alt": alt3, "normalised": f"p.{ref3}{pm.group('pos')}"
                    + (alt if alt.startswith('fs') or alt in ('=', 'del', 'dup') else alt3)})
        return out
    for pattern, change in ((_SUB, "substitution"), (_DELINS, "delins"), (_DEL, "deletion"),
                            (_DUP, "duplication"), (_INS, "insertion")):
        bm = pattern.match(body)
        if bm:
            groups = bm.groupdict()
            out["change"] = change
            out["start"] = groups.get("start")
            out["end"] = groups.get("end") or groups.get("start")
            out["ref"] = groups.get("ref")
            out["alt"] = groups.get("alt") or groups.get("seq")
            out["intronic"] = any(sym in (out["start"] or "") for sym in "+-") or \
                any(sym in (out["end"] or "") for sym in "+-")
            return out
    raise ValueError(f"unsupported {kind}. change {body!r}")


def normalise_variant(chrom: str, pos: int, ref: str, alt: str) -> dict[str, Any]:
    """Trim shared suffix then shared prefix so equivalent representations share a key."""
    if not isinstance(chrom, str) or not chrom:
        raise ValueError("chrom is required")
    if not isinstance(pos, int) or pos < 1:
        raise ValueError("pos must be a positive 1-based integer")
    r, a = str(ref).upper(), str(alt).upper()
    if not r or not a or any(c not in "ACGTN" for c in r + a):
        raise ValueError("ref and alt must be non-empty ACGTN strings")
    original = (r, a)
    while len(r) > 1 and len(a) > 1 and r[-1] == a[-1]:
        r, a = r[:-1], a[:-1]
    while len(r) > 1 and len(a) > 1 and r[0] == a[0]:
        r, a = r[1:], a[1:]
        pos += 1
    name = chrom[3:] if chrom.lower().startswith("chr") else chrom
    return {"chrom": chrom, "pos": pos, "ref": r, "alt": a, "key": f"{name}-{pos}-{r}-{a}",
            "trimmed": original != (r, a),
            "type": ("snv" if len(r) == len(a) == 1 else "mnv" if len(r) == len(a)
                     else "deletion" if len(r) > len(a) else "insertion")}


def allele_frequencies(genotypes: Sequence[str]) -> dict[str, Any]:
    """Allele frequencies, heterozygosity and a Hardy–Weinberg chi-square (1 df) from
    diploid genotype calls such as ``0/0``, ``0/1``, ``1|1``; ``./.`` is missing."""
    if not isinstance(genotypes, (list, tuple)) or not genotypes:
        raise ValueError("genotypes must be a non-empty list of calls")
    hom_ref = het = hom_alt = missing = 0
    for call in genotypes:
        alleles = re.split(r"[/|]", str(call).strip())
        if len(alleles) != 2 or "." in alleles:
            missing += 1
            continue
        try:
            a, b = int(alleles[0]), int(alleles[1])
        except ValueError:
            raise ValueError(f"unparseable genotype {call!r}") from None
        if a == b == 0:
            hom_ref += 1
        elif a != b:
            het += 1
        else:
            hom_alt += 1
    n = hom_ref + het + hom_alt
    if n == 0:
        raise ValueError("no called genotypes")
    p = (2 * hom_ref + het) / (2 * n)
    q = 1 - p
    expected = [n * p * p, 2 * n * p * q, n * q * q]
    observed = [hom_ref, het, hom_alt]
    chi2 = sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)
    return {"n_called": n, "n_missing": missing,
            "counts": {"hom_ref": hom_ref, "het": het, "hom_alt": hom_alt},
            "ref_allele_frequency": round(p, 6), "alt_allele_frequency": round(q, 6),
            "observed_heterozygosity": round(het / n, 6),
            "expected_heterozygosity": round(2 * p * q, 6),
            "hwe_chi_square": round(chi2, 6),
            "hwe_p_value": round(math.erfc(math.sqrt(chi2 / 2)), 6)}


def transition_transversion(changes: Sequence[Sequence[str]]) -> dict[str, Any]:
    """Ts/Tv ratio over ``[ref, alt]`` single-nucleotide changes."""
    if not isinstance(changes, (list, tuple)) or not changes:
        raise ValueError("changes must be a non-empty list of [ref, alt] pairs")
    purines, ts, tv, skipped = {"A", "G"}, 0, 0, 0
    for item in changes:
        if len(item) != 2:
            raise ValueError(f"each change must be a [ref, alt] pair, got {item!r}")
        r, a = str(item[0]).upper(), str(item[1]).upper()
        if len(r) != 1 or len(a) != 1 or r == a or r not in "ACGT" or a not in "ACGT":
            skipped += 1
            continue
        if (r in purines) == (a in purines):
            ts += 1
        else:
            tv += 1
    return {"transitions": ts, "transversions": tv, "skipped": skipped,
            "ts_tv_ratio": round(ts / tv, 4) if tv else None}
