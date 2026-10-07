"""FASTQ reading, quality control and trimming (bioagent.omics.fastq)."""

from __future__ import annotations

import gzip
import random
import re

import pytest

from bioagent.omics import fastq as F

pytestmark = pytest.mark.unit

ADAPTER = F.ADAPTERS["illumina_universal"] + "ACACGTCTGAACTCCAGTCAC"


def _q(values, offset=33):
    return "".join(chr(v + offset) for v in values)


def _random_seq(rng, n, gc=0.5):
    return "".join(rng.choice("GC") if rng.random() < gc else rng.choice("AT") for _ in range(n))


def _write(path, records, offset=33):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt") as fh:
        for name, seq, qual in records:
            fh.write(f"@{name} extra\n{seq}\n+\n{qual}\n")
    return path


def test_plain_and_gzipped_files_read_alike(tmp_path):
    recs = [("r1", "ACGTN", _q([30, 30, 20, 10, 2])), ("r2", "acgt", _q([40] * 4))]
    plain = _write(tmp_path / "a.fastq", recs)
    gz = _write(tmp_path / "a.fq.gz", recs)
    assert list(F.read_fastq(plain)) == list(F.read_fastq(gz))
    first, second = F.read_fastq(plain)
    assert first == ("r1", "ACGTN", _q([30, 30, 20, 10, 2])) and second.seq == "ACGT"


@pytest.mark.parametrize("text,phrase", [
    ("r1\nACGT\n+\nIIII\n", "does not start"),
    ("@r1\nACGT\nIIII\nIIII\n", "no '+'"),
    ("@r1\nACGT\n+\nIII\n", "4 bases and 3 quality"),
])
def test_a_malformed_record_is_refused(tmp_path, text, phrase):
    path = tmp_path / "bad.fastq"
    path.write_text(text)
    with pytest.raises(F.FastqError, match=re.escape(phrase)):
        list(F.read_fastq(path))


def test_the_quality_encoding_is_detected(tmp_path):
    sanger = _write(tmp_path / "s.fq", [("r", "ACGT", _q([2, 20, 35, 41]))])
    old = _write(tmp_path / "o.fq", [("r", "ACGT", _q([2, 20, 35, 40], offset=64))])
    assert F.detect_offset(sanger) == 33 and F.detect_offset(old) == 64


def _library(tmp_path, n=2000, adapter_share=0.3, seed=3):
    rng = random.Random(seed)
    recs = []
    for i in range(n):
        insert = _random_seq(rng, 60 if rng.random() < adapter_share else 100, gc=0.45)
        seq = (insert + ADAPTER + _random_seq(rng, 40))[:100]
        quals = [max(2, min(41, int(rng.gauss(36 - 10 * k / 100, 2)))) for k in range(100)]
        recs.append((f"read{i}", seq, _q(quals)))
    return _write(tmp_path / "lib.fastq.gz", recs)


def test_quality_control_measures_a_library(tmp_path):
    report = F.quality_control(_library(tmp_path))
    assert report.reads == 2000 and report.bases == 200_000 and report.offset == 33
    assert report.length == {"min": 100, "max": 100, "mean": 100.0}
    assert 40 < report.gc_percent < 52
    assert report.adapter_content["illumina_universal"] == pytest.approx(0.3, abs=0.04)
    assert report.flags["adapter_content"] == "fail"
    first, last = report.per_position_quality[0], report.per_position_quality[-1]
    assert first["median"] > last["median"]
    assert report.flags["per_base_quality"] == "pass"
    assert report.duplication_rate == 0.0 and report.flags["duplication"] == "pass"


def test_duplicated_and_poor_libraries_are_flagged(tmp_path):
    rng = random.Random(1)
    same = _random_seq(rng, 50)
    recs = [(f"r{i}", same if i % 2 else _random_seq(rng, 50), _q([15] * 50))
            for i in range(400)]
    report = F.quality_control(_write(tmp_path / "dup.fq", recs))
    assert report.duplication_rate == pytest.approx(199 / 400)
    assert report.flags["duplication"] == "warn"
    assert report.flags["overrepresented"] == "fail"
    assert report.flags["per_base_quality"] == "fail"
    assert report.flags["per_sequence_quality"] == "fail"


def test_quality_trimming_is_bwas_algorithm():
    """cutadapt's documented example: cut-off 10 keeps the first four bases."""
    rec = F.FastqRecord("r", "ACGTACGTAC", _q([42, 40, 26, 27, 8, 7, 11, 4, 2, 3]))
    out, done = F.trim_read(rec, F.TrimSettings(quality=10, adapters=(), poly_g=0))
    assert out.seq == "ACGT" and done == ["quality"]


@pytest.mark.parametrize("insert,tail,expected", [
    (40, ADAPTER, 40),                                    # whole adapter
    (40, "AGATCGGAAGTGCACACG", 40),                       # one mismatch in 18 bases
    (70, "AGATC", 70),                                    # 5-base overhang, exact
    (70, "AGTTC", 75),                                    # short overhang with an error
    (70, "AG", 72),                                       # below the minimum overlap
])
def test_adapters_are_removed_as_cutadapt_would(insert, tail, expected):
    rng = random.Random(insert)
    body = _random_seq(rng, insert).replace("AGATC", "AGTTC")
    seq = (body + tail + _random_seq(rng, 100))[:insert + len(tail)]
    rec = F.FastqRecord("r", seq, _q([38] * len(seq)))
    out, _ = F.trim_read(rec, F.TrimSettings(quality=0, poly_g=0))
    assert len(out.seq) == expected


def test_a_poly_g_tail_is_removed():
    rec = F.FastqRecord("r", "ACGTACGTAC" + "G" * 15, _q([38] * 25))
    out, done = F.trim_read(rec, F.TrimSettings(quality=0))
    assert out.seq == "ACGTACGTAC" and done == ["poly_g"]


def test_pairs_are_trimmed_and_dropped_together(tmp_path):
    rng = random.Random(5)
    good = _random_seq(rng, 80)
    r1 = [("p1/1", good, _q([38] * 80)), ("p2/1", _random_seq(rng, 15) + ADAPTER[:30],
                                          _q([38] * 45))]
    r2 = [("p1/2", good[::-1], _q([38] * 80)), ("p2/2", _random_seq(rng, 45), _q([38] * 45))]
    a = _write(tmp_path / "r1.fq", r1)
    b = _write(tmp_path / "r2.fq", r2)
    report = F.trim_fastq(a, tmp_path / "o1.fq.gz", read2=b, out2=tmp_path / "o2.fq.gz")
    assert report.paired and report.reads_in == 4 and report.reads_out == 2
    assert report.too_short == 1 and report.trimmed["illumina_universal"] == 1
    assert [r.name for r in F.read_fastq(tmp_path / "o1.fq.gz")] == ["p1/1"]
    assert [r.name for r in F.read_fastq(tmp_path / "o2.fq.gz")] == ["p1/2"]


def test_pairs_out_of_step_are_refused(tmp_path):
    a = _write(tmp_path / "r1.fq", [("x/1", "ACGT" * 10, _q([38] * 40))])
    b = _write(tmp_path / "r2.fq", [("y/2", "ACGT" * 10, _q([38] * 40))])
    with pytest.raises(F.FastqError, match="out of step"):
        F.trim_fastq(a, tmp_path / "o1.fq", read2=b, out2=tmp_path / "o2.fq")
    c = _write(tmp_path / "r3.fq", [("x/2", "ACGT" * 10, _q([38] * 40))] * 2)
    with pytest.raises(F.FastqError, match="more reads"):
        F.trim_fastq(a, tmp_path / "o1.fq", read2=c, out2=tmp_path / "o2.fq")
