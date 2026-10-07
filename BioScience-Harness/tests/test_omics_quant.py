"""Transcript quantification and gene totals (bioagent.omics.quant)."""

from __future__ import annotations

import random

import numpy as np
import pytest

from bioagent.omics import quant as Q

pytestmark = pytest.mark.unit

K = 21


def _seq(rng, n):
    return "".join(rng.choice("ACGT") for _ in range(n))


def _revcomp(s):
    return s[::-1].translate(str.maketrans("ACGT", "TGCA"))


def _transcriptome(seed=11):
    """Gene A has two isoforms sharing exons 1 and 3; gene B one; gene C one."""
    rng = random.Random(seed)
    e1, e2, e3 = _seq(rng, 400), _seq(rng, 300), _seq(rng, 500)
    return {"A-201": e1 + e2 + e3, "A-202": e1 + e3, "B-201": _seq(rng, 1500),
            "C-201": _seq(rng, 800)}


def _fastq(path, tx, abundance, n, rng, *, read_len=75, frag=(200, 20), paired=False,
           error=0.0):
    """Reads drawn in proportion to abundance x effective length, from either strand."""
    names = list(tx)
    eff = Q.effective_lengths(np.array([len(tx[t]) for t in names]), *frag)
    w = np.array([abundance[t] for t in names]) * eff
    w = w / w.sum()
    out1 = open(path.with_suffix(".1.fq"), "w")
    out2 = open(path.with_suffix(".2.fq"), "w") if paired else None
    for i in range(n):
        t = names[int(rng.choice(len(names), p=w))]
        s = tx[t]
        flen = int(min(max(rng.normal(*frag), read_len), len(s)))
        start = int(rng.integers(0, len(s) - flen + 1))
        fragment = s[start:start + flen]
        if rng.random() < 0.5:
            fragment = _revcomp(fragment)
        r1 = fragment[:read_len]
        r2 = _revcomp(fragment)[:read_len]
        if error:
            r1 = "".join(c if rng.random() > error else "ACGT"[int(rng.integers(4))] for c in r1)
        out1.write(f"@r{i}/1\n{r1}\n+\n{'I' * len(r1)}\n")
        if out2:
            out2.write(f"@r{i}/2\n{r2}\n+\n{'I' * len(r2)}\n")
    out1.close()
    if out2:
        out2.close()
    return path.with_suffix(".1.fq"), (path.with_suffix(".2.fq") if paired else None)


def test_canonical_kmers_are_strand_free():
    seq = "ACGTTGCAAGGCTTACGATCGATCGGA"
    fwd = Q.canonical_kmers(seq, 7)
    rev = Q.canonical_kmers(_revcomp(seq), 7)
    assert np.array_equal(fwd, rev[::-1])
    assert (Q.canonical_kmers("ACGTNACGTACG", 5) == -1).sum() == 5


def test_the_index_records_shared_and_unique_kmers(tmp_path):
    tx = _transcriptome()
    index = Q.KmerIndex.build(tx.items(), k=K)
    shared = index.compatible(tx["A-202"][:60])          # exon 1: both isoforms
    assert [index.transcripts[t] for t in shared] == ["A-201", "A-202"]
    unique = index.compatible(tx["A-201"][420:480])      # exon 2: only the long isoform
    assert [index.transcripts[t] for t in unique] == ["A-201"]
    assert index.compatible(_revcomp(tx["B-201"][100:175])) == (2,)
    assert index.compatible("ACGT" * 20) is None
    loaded = Q.KmerIndex.load(index.save(tmp_path / "idx.npz"))
    assert loaded.digest == index.digest and loaded.classes == index.classes
    assert np.array_equal(loaded.keys, index.keys)


def test_a_duplicate_transcript_is_refused():
    with pytest.raises(Q.QuantError, match="twice"):
        Q.KmerIndex.build([("t", "ACGT" * 20), ("t", "TTTT" * 20)], k=K)


def test_effective_lengths_follow_kallisto():
    """Length - mean fragment length (truncated at the length) + 1, as kallisto's
    trunc_gaussian_fld and calc_eff_lens compute it."""
    eff = Q.effective_lengths(np.array([2000, 150, 50]), 200.0, 20.0)
    assert eff[0] == pytest.approx(2000 - 200 + 1, abs=0.01)
    x = np.arange(151.0)
    w = np.exp(-0.5 * ((x - 200) / 20) ** 2)
    assert eff[1] == pytest.approx(150 - (w * x).sum() / w.sum() + 1)
    assert 1 <= eff[2] < 50


def test_em_splits_shared_reads_by_abundance():
    eff = np.array([1000.0, 1000.0])
    counts, rounds = Q.em({(0,): 300, (1,): 100, (0, 1): 400}, eff)
    assert counts.sum() == pytest.approx(800)
    assert counts[0] / counts[1] == pytest.approx(3.0, rel=1e-3)
    assert rounds > Q.MIN_ROUNDS


@pytest.mark.parametrize("paired", [False, True])
def test_simulated_abundances_are_recovered(tmp_path, paired):
    tx = _transcriptome()
    truth = {"A-201": 10.0, "A-202": 30.0, "B-201": 50.0, "C-201": 10.0}
    rng = np.random.default_rng(3)
    r1, r2 = _fastq(tmp_path / "s", tx, truth, 6000, rng, paired=paired)
    index = Q.KmerIndex.build(tx.items(), k=K)
    result = Q.quantify(index, r1, r2, fragment_mean=200, fragment_sd=20)
    assert result.run["pseudoaligned_fraction"] == 1.0
    assert result.est_counts.sum() == pytest.approx(6000, abs=1e-6)
    if paired:
        assert result.run["fragment_distribution"] == "estimated"
        assert result.run["fragment_mean"] == pytest.approx(200, abs=3)
        assert result.run["pairs_narrowed_by_span"] > 0
    expected = np.array([truth[t] for t in index.transcripts])
    expected = expected / expected.sum() * 1e6
    assert np.allclose(result.tpm, expected, rtol=0.12), (result.tpm, expected)


def test_reads_with_errors_still_pseudoalign(tmp_path):
    tx = _transcriptome()
    rng = np.random.default_rng(4)
    r1, _ = _fastq(tmp_path / "e", tx, {t: 1.0 for t in tx}, 1000, rng, error=0.01)
    result = Q.quantify(Q.KmerIndex.build(tx.items(), k=K), r1)
    assert result.run["pseudoaligned_fraction"] > 0.97


def test_abundance_files_round_trip(tmp_path):
    tx = _transcriptome()
    rng = np.random.default_rng(5)
    r1, _ = _fastq(tmp_path / "a", tx, {t: 1.0 for t in tx}, 500, rng)
    result = Q.quantify(Q.KmerIndex.build(tx.items(), k=K), r1)
    back = Q.read_abundance(Q.write_abundance(result, tmp_path / "abundance.tsv"))
    assert back.transcripts == result.transcripts and back.run["format"] == "kallisto"
    assert np.allclose(back.est_counts, result.est_counts, rtol=1e-5)
    sf = tmp_path / "quant.sf"
    sf.write_text("Name\tLength\tEffectiveLength\tTPM\tNumReads\nt1\t1000\t801\t1e6\t42\n")
    assert Q.read_abundance(sf).run["format"] == "salmon"


def _result(counts, tpm, eff, names=("A-201", "A-202", "B-201")):
    return Q.QuantResult(transcripts=names, lengths=np.array([1200, 900, 1500]),
                         eff_lengths=np.array(eff, dtype=float),
                         est_counts=np.array(counts, dtype=float),
                         tpm=np.array(tpm, dtype=float))


def test_gene_totals_follow_tximport(tmp_path):
    gtf = tmp_path / "a.gtf"
    gtf.write_text(
        '1\tx\ttranscript\t1\t9\t.\t+\t.\tgene_id "gA"; transcript_id "A-201"; gene_name "GA";\n'
        '1\tx\ttranscript\t1\t9\t.\t+\t.\tgene_id "gA"; transcript_id "A-202"; gene_name "GA";\n'
        '1\tx\texon\t1\t9\t.\t+\t.\tgene_id "gB"; transcript_id "B-201"; gene_name "GB";\n')
    t2g = Q.read_tx2gene(gtf)
    s1 = _result([100, 300, 600], [2e5, 3e5, 5e5], [1000, 700, 1300])
    s2 = _result([300, 100, 600], [6e5, 1e5, 3e5], [1000, 700, 1300])
    raw = Q.summarize_to_gene([s1, s2], ["s1", "s2"], t2g, counts_from_abundance="no")
    assert raw.genes == ("gA", "gB") and raw.names == ("GA", "GB")
    assert raw.counts[:, 0].tolist() == [400, 600]
    assert raw.length[0, 0] == pytest.approx((2e5 * 1000 + 3e5 * 700) / 5e5)
    scaled = Q.summarize_to_gene([s1, s2], ["s1", "s2"], t2g)
    assert scaled.counts.sum(axis=0) == pytest.approx(raw.counts.sum(axis=0))
    mean_len = raw.length.mean(axis=1)
    ratio = (scaled.tpm[:, 0] * mean_len) / (scaled.tpm[:, 0] * mean_len).sum()
    assert scaled.counts[:, 0] / scaled.counts[:, 0].sum() == pytest.approx(ratio)


def test_versions_and_missing_abundance(tmp_path):
    table = tmp_path / "t2g.tsv"
    table.write_text("transcript_id\tgene_id\nA-201.3\tgA\nA-202.1\tgA\n")
    s = _result([0, 0, 10], [0, 0, 1e6], [1000, 700, 1300],
                names=("A-201.4", "A-202", "B-201"))
    genes = Q.summarize_to_gene([s, s], ["a", "b"], Q.read_tx2gene(table),
                                counts_from_abundance="no")
    assert genes.genes == ("gA",) and genes.unmatched_transcripts == 1
    assert genes.length[0].tolist() == [850.0, 850.0]     # no abundance: mean tx length
    with pytest.raises(Q.QuantError, match="no transcript"):
        Q.summarize_to_gene([s], ["a"], {"zzz": ("g", "g")})


def test_without_the_span_check_an_exon_skipping_isoform_loses_reads(tmp_path):
    """The bias the span check removes: pairs of A-202 whose mates lie in exons 1 and 3
    are, by k-mers alone, compatible with A-201 too."""
    tx = _transcriptome()
    truth = {"A-201": 10.0, "A-202": 30.0, "B-201": 50.0, "C-201": 10.0}
    rng = np.random.default_rng(3)
    r1, r2 = _fastq(tmp_path / "s", tx, truth, 6000, rng, paired=True)
    index = Q.KmerIndex.build(tx.items(), k=K)
    plain = Q.quantify(index, r1, r2, span_check=False)
    checked = Q.quantify(index, r1, r2)
    a202 = index.transcripts.index("A-202")
    expected = 30.0 / 100.0 * 1e6
    assert abs(checked.tpm[a202] - expected) < abs(plain.tpm[a202] - expected)
    assert plain.tpm[a202] < expected
