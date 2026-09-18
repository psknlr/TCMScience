"""Parsers for the text formats bioinformatics runs on: FASTA, FASTQ, VCF, BED, GFF."""

from __future__ import annotations

import statistics
from typing import Any

__all__ = ["parse_fasta", "parse_fastq", "parse_vcf", "parse_bed", "parse_gff"]


def _text(value: Any, what: str = "text") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string")
    if not value.strip():
        raise ValueError(f"{what} is empty")
    return value


def parse_fasta(text: str, max_records: int = 1000) -> dict[str, Any]:
    """FASTA records: id (first token of the header), description, sequence, length."""
    records: list[dict[str, Any]] = []
    header, chunks = None, []
    for line in _text(text).splitlines():
        if line.startswith(">"):
            if header is not None:
                records.append(_fasta_record(header, chunks))
            header, chunks = line[1:].strip(), []
        elif header is not None:
            chunks.append(line.strip())
        elif line.strip():
            raise ValueError("FASTA text must start with a '>' header line")
    if header is not None:
        records.append(_fasta_record(header, chunks))
    return {"count": len(records), "records": records[:max_records],
            "total_length": sum(r["length"] for r in records)}


def _fasta_record(header: str, chunks: list[str]) -> dict[str, Any]:
    seq = "".join(chunks).upper()
    ident, _, desc = header.partition(" ")
    return {"id": ident, "description": desc, "sequence": seq, "length": len(seq)}


def parse_fastq(text: str, max_records: int = 200, phred_offset: int = 33) -> dict[str, Any]:
    """FASTQ summary: read count, length and quality statistics, first records."""
    lines = [ln.rstrip("\n") for ln in _text(text).splitlines() if ln.strip()]
    if len(lines) % 4:
        raise ValueError("FASTQ text is not a multiple of four lines")
    records, lengths, means, q30 = [], [], [], 0
    for i in range(0, len(lines), 4):
        head, seq, plus, qual = lines[i:i + 4]
        if not head.startswith("@") or not plus.startswith("+"):
            raise ValueError(f"malformed FASTQ record at line {i + 1}")
        if len(seq) != len(qual):
            raise ValueError(f"sequence and quality lengths differ at line {i + 1}")
        scores = [ord(c) - phred_offset for c in qual]
        mean_q = sum(scores) / len(scores) if scores else 0.0
        q30 += sum(1 for s in scores if s >= 30)
        lengths.append(len(seq)); means.append(mean_q)
        if len(records) < max_records:
            records.append({"id": head[1:].split()[0], "sequence": seq, "mean_quality": round(mean_q, 2)})
    total_bases = sum(lengths)
    return {"count": len(lengths), "total_bases": total_bases,
            "mean_length": round(statistics.mean(lengths), 2) if lengths else 0,
            "mean_quality": round(statistics.mean(means), 2) if means else 0,
            "q30_fraction": round(q30 / total_bases, 4) if total_bases else 0,
            "records": records}


def _info(field: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if field in (".", ""):
        return out
    for item in field.split(";"):
        key, sep, value = item.partition("=")
        out[key] = value if sep else True
    return out


def parse_vcf(text: str, max_records: int = 500) -> dict[str, Any]:
    """VCF records with parsed INFO and per-sample genotype fields."""
    samples: list[str] = []
    records: list[dict[str, Any]] = []
    seen_header = False
    for line in _text(text).splitlines():
        if not line.strip():
            continue
        if line.startswith("##"):
            continue
        if line.startswith("#CHROM"):
            cols = line[1:].split("\t")
            samples = cols[9:]
            seen_header = True
            continue
        if not seen_header:
            raise ValueError("VCF text has no #CHROM header line")
        cols = line.split("\t")
        if len(cols) < 8:
            raise ValueError(f"VCF record has {len(cols)} columns; at least 8 are required")
        record: dict[str, Any] = {
            "chrom": cols[0], "pos": int(cols[1]), "id": None if cols[2] == "." else cols[2],
            "ref": cols[3], "alt": cols[4].split(","),
            "qual": None if cols[5] == "." else float(cols[5]), "filter": cols[6],
            "info": _info(cols[7])}
        if samples and len(cols) > 9:
            keys = cols[8].split(":")
            record["genotypes"] = {
                sample: dict(zip(keys, cols[9 + k].split(":")))
                for k, sample in enumerate(samples) if 9 + k < len(cols)}
        records.append(record)
    return {"samples": samples, "count": len(records), "records": records[:max_records]}


def parse_bed(text: str, max_records: int = 1000) -> dict[str, Any]:
    """BED intervals (0-based, half-open) with optional name, score and strand."""
    records = []
    for line in _text(text).splitlines():
        if not line.strip() or line.startswith(("track", "browser", "#")):
            continue
        cols = line.split("\t") if "\t" in line else line.split()
        if len(cols) < 3:
            raise ValueError(f"BED line has fewer than three columns: {line[:60]!r}")
        rec: dict[str, Any] = {"chrom": cols[0], "start": int(cols[1]), "end": int(cols[2])}
        if len(cols) > 3:
            rec["name"] = cols[3]
        if len(cols) > 4:
            rec["score"] = cols[4]
        if len(cols) > 5:
            rec["strand"] = cols[5]
        rec["length"] = rec["end"] - rec["start"]
        records.append(rec)
    return {"count": len(records), "records": records[:max_records],
            "total_span": sum(r["length"] for r in records)}


def parse_gff(text: str, max_records: int = 1000) -> dict[str, Any]:
    """GFF3/GTF features with attributes parsed into a mapping."""
    records = []
    for line in _text(text).splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 9:
            raise ValueError(f"GFF line does not have nine columns: {line[:60]!r}")
        attrs: dict[str, str] = {}
        raw = cols[8].strip().rstrip(";")
        for item in raw.split(";"):
            item = item.strip()
            if not item:
                continue
            if "=" in item:                       # GFF3
                k, _, v = item.partition("=")
            else:                                 # GTF: key "value"
                k, _, v = item.partition(" ")
            attrs[k.strip()] = v.strip().strip('"')
        records.append({"seqid": cols[0], "source": cols[1], "type": cols[2],
                        "start": int(cols[3]), "end": int(cols[4]),
                        "score": None if cols[5] == "." else float(cols[5]),
                        "strand": cols[6], "phase": cols[7], "attributes": attrs})
    types: dict[str, int] = {}
    for r in records:
        types[r["type"]] = types.get(r["type"], 0) + 1
    return {"count": len(records), "records": records[:max_records], "feature_types": types}
