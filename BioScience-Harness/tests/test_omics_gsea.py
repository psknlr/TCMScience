"""Preranked GSEA (bioagent.omics.gsea) on rankings whose answer is known.

A set made of the top-ranked genes must come out significantly positive, a set of the
bottom genes significantly negative, and random sets not significant. A fixed seed must
give the same numbers twice. Before any of that, the module's own work is checked:
how a ranking is declared and built from a differential-expression result, how a GMT
file becomes a hashed snapshot, how genes are matched, and what is refused rather than
silently mapped. The tests that run GSEApy skip without it, and fail instead in CI's
analysis job.
"""

from __future__ import annotations

import dataclasses
import hashlib
import sys
import types
from importlib import metadata

import numpy as np
import pytest

from bioagent.omics import gsea
from bioagent.omics.de_backends import run_de
from bioagent.omics.optional import BackendUnavailable
from omics_world import need_module

pytestmark = pytest.mark.unit

N = 2000
GENES = [f"GENE{i:04d}" for i in range(N)]


def _ranked(seed=3, **kw):
    """GENE0000 has the highest score and GENE1999 the lowest, given shuffled."""
    rng = np.random.default_rng(seed)
    scores = np.sort(rng.normal(0, 2, N))[::-1]
    order = rng.permutation(N)
    return gsea.rank_genes([GENES[i] for i in order], scores[order], method="stat",
                           species="human", id_type="symbol", **kw)


def _gmt(path, sets):
    path.write_text("".join(f"{name}\t{name.lower()}\t" + "\t".join(genes) + "\n"
                            for name, genes in sets.items()))
    return gsea.read_gmt(path, source="synthetic", version="1", licence="CC0-1.0",
                         species="human", id_type="symbol")


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    rng = np.random.default_rng(5)
    sets = {"TOP": GENES[:40], "BOTTOM": GENES[-40:],
            **{f"RANDOM{k}": sorted(rng.choice(GENES, 40, replace=False))
               for k in range(5)},
            "PARTIAL": GENES[100:120] + [f"ABSENT{i}" for i in range(20)],
            "TINY": GENES[:5]}
    return _gmt(tmp_path_factory.mktemp("gmt") / "sets.gmt", sets)


# ---------------------------------------------------------------- rankings

def test_a_ranking_is_ordered_declared_and_reproducible():
    ranked = gsea.rank_genes(["b", "a", "c", "d", "e"], [1.0, 1.0, np.nan, 3.0, -2.0],
                             method="log2fc", species="mouse", id_type="symbol")
    assert ranked.genes == ("d", "a", "b", "e")          # a tie is broken by gene ID
    assert ranked.dropped == ("c",) and ranked.tied == 2
    record = ranked.record()
    assert record["method"] == "log2fc" and record["species"] == "mouse"
    assert record["order"] == "descending score, ties by gene ID"


@pytest.mark.parametrize("genes,scores,kw,phrase", [
    (["a", "a"], [1.0, 2.0], {}, "repeat"),
    (["a", "b"], [1.0, np.inf], {}, "infinite"),
    (["a", "b"], [1.0, 2.0], {"method": "rank"}, "ranking method"),
    (["a", "b"], [1.0, 2.0], {"species": " "}, "species must be declared"),
])
def test_a_ranking_that_cannot_be_read_is_refused(genes, scores, kw, phrase):
    args = {"method": "stat", "species": "human", "id_type": "symbol", **kw}
    with pytest.raises(gsea.GSEAError, match=phrase):
        gsea.rank_genes(genes, scores, **args)


@pytest.fixture(scope="module")
def de_result():
    """120 genes, the first ten up and the next ten down in treated samples."""
    rng = np.random.default_rng(1)
    base = rng.lognormal(5, 0.8, 120)
    lfc = np.zeros(120)
    lfc[:10], lfc[10:20] = 2.0, -2.0
    ids = [f"s{k}" for k in range(8)]
    meta = {s: {"condition": "treated" if k % 2 else "control"} for k, s in enumerate(ids)}
    cols = [rng.negative_binomial(20, 20 / (20 + base * 2 ** (lfc * (k % 2))))
            for k in range(8)]
    return run_de(np.column_stack(cols), [f"G{i:03d}" for i in range(120)], ids, meta,
                  design="~ condition", contrast=("condition", "treated", "control"))


@pytest.mark.parametrize("method", list(gsea.RANKING_METHODS))
def test_a_de_result_ranks_the_numerator_s_genes_first(de_result, method):
    ranked = gsea.ranked_from_de(de_result, method=method, species="human",
                                 id_type="symbol")
    up = {f"G{i:03d}" for i in range(10)}
    down = {f"G{i:03d}" for i in range(10, 20)}
    assert set(ranked.genes[:10]) == up and set(ranked.genes[-10:]) == down
    assert "condition: treated vs control" in ranked.source
    assert de_result.backend in ranked.source


def test_p_values_of_zero_are_capped_and_counted(de_result):
    p = de_result.p_value.copy()
    p[0] = 0.0
    capped = gsea.ranked_from_de(dataclasses.replace(de_result, p_value=p),
                                 method="signed_log10_p", species="human",
                                 id_type="symbol")
    assert capped.capped == 1 and np.isfinite(capped.scores).all()
    assert capped.genes[0] == "G000"


# ---------------------------------------------------------------- the gene sets

def test_a_gmt_file_is_a_hashed_snapshot(tmp_path, library):
    path = tmp_path / "one.gmt"
    lib = _gmt(path, {"S1": ["A", "B", "B", "C"]})
    assert lib.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert lib.sets["S1"] == ("A", "B", "C")                   # repeats within a set
    assert lib.record() == {"path": str(path), "sha256": lib.sha256, "source": "synthetic",
                            "version": "1", "licence": "CC0-1.0", "species": "human",
                            "id_type": "symbol", "sets": 1}
    changed = _gmt(path, {"S1": ["A", "B", "D"]})
    assert changed.sha256 != lib.sha256
    path.write_text("S1\tx\tA\nS1\tx\tB\n")
    with pytest.raises(gsea.GSEAError, match="defined twice"):
        gsea.read_gmt(path, source="s", version="1", licence="CC0-1.0", species="human",
                      id_type="symbol")
    with pytest.raises(gsea.GSEAError, match="licence must be declared"):
        gsea.read_gmt(path, source="s", version="1", licence="", species="human",
                      id_type="symbol")


# ---------------------------------------------------------------- GSEA

@pytest.fixture(scope="module")
def result(library):
    need_module("gseapy")
    return gsea.run_prerank(_ranked(), library, permutations=1000, seed=11)


def test_top_genes_score_positive_bottom_negative_random_neither(result):
    rows = {r["term"]: r for r in result.rows}
    assert rows["TOP"]["nes"] > 2 and rows["TOP"]["fdr"] < 0.01
    assert rows["BOTTOM"]["nes"] < -2 and rows["BOTTOM"]["fdr"] < 0.01
    for k in range(5):
        assert rows[f"RANDOM{k}"]["p_value"] > 0.05 and rows[f"RANDOM{k}"]["fdr"] > 0.25
    assert set(rows["TOP"]["leading_edge"]) <= set(GENES[:40])
    assert set(rows["BOTTOM"]["leading_edge"]) <= set(GENES[-40:])
    assert {r["term"] for r in result.significant()} == {"TOP", "BOTTOM", "PARTIAL"}


def test_the_mapping_and_the_record_say_what_was_tested(result, library):
    assert result.excluded == {"TINY": "5 of its 5 genes are in the ranked list, fewer "
                                       "than min_size 15"}
    partial = result.mapping["per_set"]["PARTIAL"]
    assert partial["size"] == 40 and partial["matched"] == 20
    assert partial["missing"] == [f"ABSENT{i}" for i in range(20)]
    assert result.mapping["not_found"] == sorted(f"ABSENT{i}" for i in range(20))
    record = result.record()
    assert record["library"]["sha256"] == library.sha256
    assert record["library"]["licence"] == "CC0-1.0"
    assert record["ranking"]["method"] == "stat"
    assert record["backend"] == {"name": "gseapy", "version": metadata.version("gseapy")}
    assert record["parameters"]["seed"] == 11
    assert record["parameters"]["p_value_resolution"] == 1 / 1000
    assert any("p < 1/1000" in n for n in result.notes)


def test_a_fixed_seed_gives_the_same_answer(result, library):
    again = gsea.run_prerank(_ranked(), library, permutations=1000, seed=11)
    assert [(r["term"], r["nes"], r["p_value"], r["fdr"]) for r in again.rows] == \
           [(r["term"], r["nes"], r["p_value"], r["fdr"]) for r in result.rows]


def test_a_de_result_feeds_gsea_with_its_direction(de_result, tmp_path):
    need_module("gseapy")
    lib = _gmt(tmp_path / "de.gmt", {"UP": [f"G{i:03d}" for i in range(10)],
                                     "DOWN": [f"G{i:03d}" for i in range(10, 20)]})
    ranked = gsea.ranked_from_de(de_result, method="stat", species="human",
                                 id_type="symbol")
    rows = {r["term"]: r for r in gsea.run_prerank(ranked, lib, min_size=5,
                                                     permutations=200, seed=0).rows}
    assert rows["UP"]["nes"] > 0 > rows["DOWN"]["nes"]


# ---------------------------------------------------------------- refusals

def test_identifiers_are_never_mapped_silently(tmp_path):
    ranked = _ranked()
    mouse = gsea.rank_genes([g.capitalize() for g in ranked.genes], ranked.scores,
                            method="stat", species="human", id_type="symbol")
    upper = _gmt(tmp_path / "upper.gmt", {"TOP": GENES[:40]})
    # GSEApy would upper-case "Gene0001" to match "GENE0001"; here nothing matches
    with pytest.raises(gsea.GSEAError, match="0 of the 40 set genes were found"):
        gsea.run_prerank(mouse, upper, permutations=10)
    other = dataclasses.replace(upper, species="mouse")
    with pytest.raises(gsea.GSEAError, match="species is 'human'"):
        gsea.run_prerank(ranked, other, permutations=10)


def test_gsea_without_gseapy_is_refused(monkeypatch, library):
    monkeypatch.setitem(sys.modules, "gseapy", None)
    with pytest.raises(BackendUnavailable, match="gseapy"):
        gsea.run_prerank(_ranked(), library, permutations=10)


@pytest.mark.parametrize("found", ["1.1.5", "unknown"])
def test_a_gseapy_that_would_reorder_the_ranking_is_refused(monkeypatch, library, found):
    """Before 1.1.6 GSEApy's prerank sorts the ranking itself (1.1.0 and 1.1.5 then stop
    on ascending=None); a release that cannot be read is not assumed to be newer."""
    monkeypatch.setitem(sys.modules, "gseapy", types.SimpleNamespace())
    monkeypatch.setattr(gsea, "version", lambda distribution: found)
    with pytest.raises(BackendUnavailable, match="needs 1.1.6 or later"):
        gsea.run_prerank(_ranked(), library, permutations=10)
