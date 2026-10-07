"""Preranked gene set enrichment analysis (GSEA), run by GSEApy.

    from bioagent.omics import gsea
    ranked = gsea.ranked_from_de(result, method="stat", species="human", id_type="symbol")
    library = gsea.read_gmt("hallmark.gmt", source="MSigDB Hallmark", version="2024.1.Hs",
                            licence="CC-BY-4.0", species="human", id_type="symbol")
    res = gsea.run_prerank(ranked, library, permutations=1000, seed=0)

GSEA (Subramanian et al. 2005, *PNAS* 102:15545) asks whether the genes of a set
gather at one end of a ranked list. GSEApy (Fang et al. 2023, *Bioinformatics*
39:btac757; BSD-3-Clause) computes the enrichment score, its normalised form (NES),
the nominal p-value from gene-set permutations, the FDR and the leading edge.

This module adds what a result must carry to be read correctly, and guards against
what GSEApy would otherwise do without saying so:

* **The ranking is declared**, as ``stat`` (the Wald statistic), ``signed_log10_p``
  (the sign of the fold change times -log10 p) or ``log2fc``. They ask different
  questions; a list ranked by fold change, for one, puts noisy low-count genes at its
  ends. Genes without a value are dropped and listed, p-values that underflowed to 0
  are capped and counted, and ties are broken by gene ID, so the order is reproducible.
  Ties are counted too.
* **The gene sets are a snapshot**: a local GMT file whose SHA-256 is recorded with its
  source, version and licence. GSEApy takes a ``gene_sets`` string that is not a file
  for an Enrichr library name and downloads it, so here the sets are parsed from the
  file and handed over as a dictionary, and nothing is fetched.
* **Identifiers are declared and matched exactly.** The ranked list and the library each
  declare a species and an identifier type, and a mismatch is refused. GSEApy upper-
  cases a mostly lower-case ranked list when the sets look upper-case, which maps
  mouse symbols onto human ones without a word. Here genes reach GSEApy only as neutral
  codes, after exact matching, so that heuristic cannot apply. The mapping report lists
  every set gene that is not in the ranked list.
* **GSEApy's own clean-up is not relied on.** It renames duplicate IDs, drops missing
  values with a log line, replaces infinities by their neighbours, and sorts with an
  unstable sort that orders ties arbitrarily. Here duplicates and infinities are
  refused, missing values are dropped and reported, and the order is passed as is
  (``ascending=None``). GSEApy keeps a given order only from 1.1.6; an earlier
  release, which would sort anyway, is refused with that reason.

A set is tested when between ``min_size`` and ``max_size`` of its genes are in the
ranked list, and fewer than all of them; the other sets are reported with the reason. A
nominal p-value of 0 means that no permutation reached the observed score, so p is
below 1 / permutations; that resolution is recorded with the result.

A result says that the genes of a set lie towards one end of this ranking more than
chance would place them. It is not evidence that the pathway is active, and a set's
name is a label, not a finding.
"""

from __future__ import annotations

import hashlib
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ..status import ExecutionStatus
from .de_backends import DEResult
from .optional import BackendUnavailable, require, version

__all__ = ["RANKING_METHODS", "GSEAError", "RankedGenes", "GeneSetLibrary", "GSEAResult",
           "rank_genes", "ranked_from_de", "read_gmt", "run_prerank"]

#: The first GSEApy release whose prerank keeps the order it is given (ascending=None).
#: Before it, prerank always sorts the ranking itself; 1.1.0 and 1.1.5 stop on None.
GSEAPY_FLOOR = (1, 1, 6)

RANKING_METHODS = {
    "stat": "the Wald statistic",
    "signed_log10_p": "the sign of the log2 fold change times -log10 of the p-value",
    "log2fc": "the log2 fold change",
}
#: Below this share of the library's genes found in the ranked list, the identifiers
#: most likely do not match (symbols against Ensembl IDs, one species against another).
LOW_MAPPING = 0.5


class GSEAError(ValueError):
    """A ranking, gene-set file or setting GSEA cannot be run with."""


@dataclass(frozen=True)
class RankedGenes:
    """Genes by descending score, ties broken by gene ID."""

    genes: tuple[str, ...]
    scores: np.ndarray
    method: str
    species: str
    id_type: str
    source: str = ""
    dropped: tuple[str, ...] = ()       # genes without a value
    capped: int = 0                     # p-values of 0, capped at the smallest double
    tied: int = 0                       # genes sharing their score with another

    def record(self) -> dict[str, Any]:
        return {"method": self.method, "meaning": RANKING_METHODS[self.method],
                "species": self.species, "id_type": self.id_type, "source": self.source,
                "genes": len(self.genes), "dropped": list(self.dropped),
                "capped": self.capped, "tied": self.tied,
                "order": "descending score, ties by gene ID"}


def _declared(**values: str) -> None:
    for name, value in values.items():
        if not str(value).strip():
            raise GSEAError(f"{name} must be declared; a result without it cannot be "
                            "read or repeated")


def rank_genes(genes: Sequence[Any], scores: Sequence[float], *, method: str,
               species: str, id_type: str, source: str = "") -> RankedGenes:
    """A ranked list from parallel gene IDs and scores; NaN scores are dropped."""
    if method not in RANKING_METHODS:
        raise GSEAError(f"the ranking method is one of {', '.join(RANKING_METHODS)}")
    _declared(species=species, id_type=id_type)
    ids = np.array([str(g) for g in genes], dtype=object)
    values = np.asarray(scores, dtype=float)
    if values.shape != ids.shape:
        raise GSEAError(f"{len(ids)} genes and {values.size} scores")
    repeated = sorted(k for k, n in Counter(ids.tolist()).items() if n > 1)
    if repeated:
        raise GSEAError(f"{len(repeated)} gene IDs repeat (e.g. {repeated[0]!r}); "
                        "collapse them to one score per gene first")
    if np.isinf(values).any():
        raise GSEAError("a score is infinite; GSEA needs finite scores")
    missing = np.isnan(values)
    dropped = tuple(sorted(ids[missing].tolist()))
    ids, values = ids[~missing], values[~missing]
    order = np.lexsort((ids, -values))
    _, counts = np.unique(values, return_counts=True)
    return RankedGenes(genes=tuple(ids[order].tolist()), scores=values[order],
                       method=method, species=species, id_type=id_type, source=source,
                       dropped=dropped, tied=int(counts[counts > 1].sum()))


def ranked_from_de(result: DEResult, *, method: str, species: str,
                   id_type: str) -> RankedGenes:
    """The genes of a differential-expression result, ranked by ``method``.

    A positive score means higher in the contrast's numerator, whichever backend ran.
    """
    capped = 0
    if method == "stat":
        scores = result.stat
    elif method == "log2fc":
        scores = result.log2_fold_change
    elif method == "signed_log10_p":
        p = np.asarray(result.p_value, dtype=float)
        capped = int(np.sum(p == 0))
        with np.errstate(invalid="ignore"):
            scores = (np.sign(result.log2_fold_change)
                      * -np.log10(np.maximum(p, np.finfo(float).tiny)))
    else:
        raise GSEAError(f"the ranking method is one of {', '.join(RANKING_METHODS)}")
    factor, numerator, denominator = result.contrast
    source = (f"{factor}: {numerator} vs {denominator}, by {result.backend} "
              f"{result.version}")
    ranked = rank_genes(result.genes, scores, method=method, species=species,
                        id_type=id_type, source=source)
    return replace(ranked, capped=capped)


@dataclass(frozen=True)
class GeneSetLibrary:
    """Gene sets read from a local GMT file, with what is needed to cite it."""

    path: str
    sha256: str
    source: str
    version: str
    licence: str
    species: str
    id_type: str
    sets: dict[str, tuple[str, ...]]
    descriptions: dict[str, str]

    def record(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "source": self.source,
                "version": self.version, "licence": self.licence, "species": self.species,
                "id_type": self.id_type, "sets": len(self.sets)}


def read_gmt(path: str | Path, *, source: str, version: str, licence: str, species: str,
             id_type: str) -> GeneSetLibrary:
    """A GMT file (set name, description, then genes, tab-separated) as a snapshot."""
    _declared(source=source, version=version, licence=licence, species=species,
              id_type=id_type)
    p = Path(path)
    data = p.read_bytes()
    sets: dict[str, tuple[str, ...]] = {}
    descriptions: dict[str, str] = {}
    for n, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        name = fields[0].strip()
        if len(fields) < 3 or not name:
            raise GSEAError(f"{p} line {n}: a GMT line is a name, a description, then "
                            "genes, separated by tabs")
        if name in sets:
            raise GSEAError(f"{p} line {n}: gene set {name!r} is defined twice")
        genes = tuple(dict.fromkeys(g.strip() for g in fields[2:] if g.strip()))
        if not genes:
            raise GSEAError(f"{p} line {n}: gene set {name!r} lists no gene")
        sets[name] = genes
        descriptions[name] = fields[1].strip()
    if not sets:
        raise GSEAError(f"{p} holds no gene set")
    return GeneSetLibrary(path=str(p), sha256=hashlib.sha256(data).hexdigest(),
                          source=source, version=version, licence=licence,
                          species=species, id_type=id_type, sets=sets,
                          descriptions=descriptions)


@dataclass
class GSEAResult:
    """Tested sets by FDR, the sets not tested and why, and how the genes mapped."""

    rows: list[dict[str, Any]]
    excluded: dict[str, str]
    mapping: dict[str, Any]
    ranking: dict[str, Any]
    library: dict[str, Any]
    parameters: dict[str, Any]
    backend: dict[str, str]
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED
    notes: list[str] = field(default_factory=list)

    def significant(self, fdr: float = 0.25) -> list[dict[str, Any]]:
        """Sets at FDR below ``fdr`` (0.25 is GSEA's own suggestion for exploration)."""
        return [r for r in self.rows if r["fdr"] < fdr]

    def record(self) -> dict[str, Any]:
        return {"status": self.status.value, "backend": self.backend,
                "parameters": self.parameters, "ranking": self.ranking,
                "library": self.library, "mapping": self.mapping,
                "excluded": self.excluded, "notes": self.notes,
                "rows": [{**r, "leading_edge": list(r["leading_edge"])} for r in self.rows]}


def _mapping(ranked: RankedGenes, library: GeneSetLibrary) -> dict[str, Any]:
    known = set(ranked.genes)
    in_sets = sorted({g for genes in library.sets.values() for g in genes})
    not_found = [g for g in in_sets if g not in known]
    return {"ranked_genes": len(ranked.genes), "set_genes": len(in_sets),
            "found": len(in_sets) - len(not_found),
            "fraction_found": (len(in_sets) - len(not_found)) / len(in_sets),
            "not_found": not_found,
            "ranked_genes_in_no_set": len(known - set(in_sets)),
            "per_set": {name: {"size": len(genes),
                               "matched": sum(g in known for g in genes),
                               "missing": [g for g in genes if g not in known]}
                        for name, genes in library.sets.items()}}


def run_prerank(ranked: RankedGenes, library: GeneSetLibrary, *, min_size: int = 15,
                max_size: int = 500, permutations: int = 1000, seed: int = 0,
                weight: float = 1.0, threads: int = 1) -> GSEAResult:
    """GSEA of ``library`` on ``ranked`` by gene-set permutation, with GSEApy."""
    for what in ("species", "id_type"):
        if getattr(ranked, what) != getattr(library, what):
            raise GSEAError(f"the ranked list's {what} is {getattr(ranked, what)!r} and the "
                            f"gene sets' is {getattr(library, what)!r}; map one to the "
                            "other first")
    if not 1 <= min_size <= max_size:
        raise GSEAError("gene-set sizes need 1 <= min_size <= max_size")
    if permutations < 1:
        raise GSEAError("at least one permutation is needed")
    position = {g: i for i, g in enumerate(ranked.genes)}
    n = len(ranked.genes)
    tested: dict[str, list[str]] = {}
    excluded: dict[str, str] = {}
    for name, genes in library.sets.items():
        found = [g for g in genes if g in position]
        if len(found) < min_size:
            excluded[name] = (f"{len(found)} of its {len(genes)} genes are in the ranked "
                              f"list, fewer than min_size {min_size}")
        elif len(found) > max_size:
            excluded[name] = (f"{len(found)} of its genes are in the ranked list, more "
                              f"than max_size {max_size}")
        elif len(found) >= n:
            excluded[name] = "it holds every gene of the ranked list"
        else:
            tested[name] = found
    mapping = _mapping(ranked, library)
    if not tested:
        raise GSEAError(f"no gene set has between {min_size} and {max_size} genes in the "
                        f"ranked list; {mapping['found']} of the {mapping['set_genes']} "
                        f"set genes were found. Check that both use {ranked.id_type} "
                        f"identifiers of {ranked.species}")
    gseapy = require("gseapy", backend="GSEA")
    installed = version("gseapy")
    release = re.match(r"\d+(?:\.\d+)*", installed)
    if release is None or tuple(map(int, release.group(0).split("."))) < GSEAPY_FLOOR:
        floor = ".".join(map(str, GSEAPY_FLOOR))
        raise BackendUnavailable("GSEA", f"gseapy {installed} cannot keep the ranking's order "
                                         f"(ascending=None needs {floor} or later); install "
                                         "it with pip install 'bioagent[analysis]'")
    import pandas as pd
    # neutral codes: GSEApy sees no gene name, so no case heuristic can match genes
    codes = [f"G{i:08d}" for i in range(n)]
    gene_of = dict(zip(codes, ranked.genes))
    rnk = pd.Series(np.asarray(ranked.scores, dtype=float), index=codes)
    sets = {name: [codes[position[g]] for g in found] for name, found in tested.items()}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pre = gseapy.prerank(rnk=rnk, gene_sets=sets, min_size=min_size, max_size=max_size,
                             permutation_num=permutations, weight=weight, ascending=None,
                             threads=threads, seed=seed, outdir=None, no_plot=True,
                             verbose=False)
    rows = []
    for name, found in tested.items():
        r = pre.results.get(name)
        if r is None:
            raise RuntimeError(f"GSEApy returned no result for gene set {name!r}")
        rows.append({"term": name, "description": library.descriptions[name],
                     "size": len(library.sets[name]), "matched": len(found),
                     "es": float(r["es"]), "nes": float(r["nes"]),
                     "p_value": float(r["pval"]), "fdr": float(r["fdr"]),
                     "fwer": float(r["fwerp"]),
                     "leading_edge": tuple(gene_of[c] for c in str(r["lead_genes"]).split(";")
                                           if c)})
    rows.sort(key=lambda r: (r["fdr"], -abs(r["nes"]), r["term"]))
    notes = []
    if any(r["p_value"] == 0 for r in rows):
        notes.append(f"a nominal p-value of 0 means p < 1/{permutations}: no permutation "
                     "reached the observed score")
    if mapping["fraction_found"] < LOW_MAPPING:
        notes.append(f"only {mapping['fraction_found']:.0%} of the gene-set genes are in the "
                     f"ranked list; check that both use {ranked.id_type} identifiers of "
                     f"{ranked.species}")
    if ranked.tied:
        notes.append(f"{ranked.tied} genes share their score with another; they are ordered "
                     "by gene ID")
    notes += sorted({f"GSEApy warned: {w.category.__name__}: {w.message}"[:300]
                     for w in caught})
    return GSEAResult(
        rows=rows, excluded=excluded, mapping=mapping, ranking=ranked.record(),
        library=library.record(),
        parameters={"min_size": min_size, "max_size": max_size,
                    "permutations": permutations, "seed": seed, "weight": weight,
                    "threads": threads, "null": "gene-set permutation",
                    "p_value_resolution": 1.0 / permutations},
        backend={"name": "gseapy", "version": installed}, notes=notes)
