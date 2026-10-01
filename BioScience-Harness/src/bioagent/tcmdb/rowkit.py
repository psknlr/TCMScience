"""The relation row and the small helpers every extractor uses to write one.

``tcmdb.relations`` documents the row; this module holds its vocabulary so that the
extractors of added sources (``tcmdb.extra``) can write rows without importing the core
extractors.

Beyond the original twelve columns a row now says, when the source says it:

* ``effect``: what the subject does to the object, on a small signed vocabulary
  (``EFFECTS``). The source's own word (agonist, up-regulates quantity, ...) stays in
  ``context["action"]``. A row without a direction leaves ``effect`` empty; it is never
  guessed.
* ``outcome``: ``positive`` (the relation was found), ``negative`` (it was tested and not
  found: an inactive assay, a screen without a hit) or ``inconclusive`` (tested, but the
  source flags the result as unreliable). A database that never tested a pair has no row
  for it, so "not tested" and "tested negative" stay apart.
* ``context``: the conditions the result holds under, as JSON: species, cell line,
  tissue, dose and unit, time, method or assay, whether the interaction is direct, the
  residue, the source's quality flags. Keys are listed in ``CONTEXT_KEYS``.
* ``associated`` evidence: a statistical association measured in a population or a
  screen (an eQTL, a GWAS hit, a correlation). It is an observation of covariation, not
  of a mechanism, and it is kept apart from ``known``.
* ``license``: the licence of the file the row came from (filled by ``build_relations``
  from the dataset's spec when the extractor does not set it).
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterator, Mapping

__all__ = ["RELATION_KINDS", "EVIDENCE", "EFFECTS", "OUTCOMES", "CONTEXT_KEYS", "COLUMNS",
           "Row", "register_kinds", "rel", "unresolved", "ctx", "v", "names", "cid", "has",
           "rows", "col"]

#: kind -> (subject type, object type)
RELATION_KINDS: dict[str, tuple[str, str]] = {
    "herb_ingredient": ("herb", "ingredient"),
    "ingredient_target": ("ingredient", "target"),
    "formula_herb": ("formula", "herb"),
    "target_disease": ("target", "disease"),
    "subject_clinical_trial": ("subject", "clinical_trial"),
    "subject_meta_analysis": ("subject", "meta_analysis"),
    "subject_reference": ("subject", "publication"),
    "herb_drug_interaction": ("herb", "drug"),
    "drug_target": ("drug", "target"),
    "gene_set_member": ("gene_set", "target"),
    "drug_adverse_event": ("drug", "adverse_event"),
    # from the SymMap and HERB site query endpoints (tcmdb.live)
    "herb_target": ("herb", "target"),
    "herb_disease": ("herb", "disease"),
    "herb_symptom": ("herb", "symptom"),
    "herb_syndrome": ("herb", "syndrome"),
    "ingredient_disease": ("ingredient", "disease"),
    # sources added after the architecture document (tcmdb.extra); one vocabulary, so the
    # same relation from two sources meets in one kind
    "organism_compound": ("organism", "compound"),      # a natural product found in a taxon
    "compound_target": ("compound", "target"),          # a non-drug ligand on a target
    "drug_indication": ("drug", "disease"),
    "protein_interaction": ("protein", "protein"),      # physical (direct or co-complex)
    "genetic_interaction": ("gene", "gene"),
    "complex_member": ("complex", "protein"),
    "regulation": ("regulator", "target"),              # signed, from curated mechanisms
    "screen_gene": ("screen", "gene"),                  # a CRISPR/RNAi screen's hit or non-hit
    "compound_assay": ("compound", "assay"),            # an assay endpoint, active or inactive
    "compound_spectrum": ("compound", "spectrum"),      # a reference spectrum of a compound
    "reaction_participant": ("reaction", "compound"),
    "enzyme_reaction": ("protein", "reaction"),
    "mirna_target": ("mirna", "gene"),
    "tf_target": ("regulator", "gene"),                 # a transcription factor's bound genes
    "variant_gene": ("variant", "gene"),                # eQTL / sQTL
    "gene_phenotype": ("gene", "phenotype"),            # a knockout's or a variant's phenotype
    "disease_phenotype": ("disease", "phenotype"),
    "key_event_relationship": ("event", "event"),       # adverse outcome pathways
    "stressor_event": ("compound", "event"),
    "food_nutrient": ("food", "compound"),
    "subject_monograph": ("subject", "monograph"),      # a regulator's assessment document
    "ptm_site": ("enzyme", "protein"),                  # a modification of a substrate
}

EVIDENCE = frozenset({"known", "predicted", "aggregated", "listed", "reported", "mentioned",
                      "signal", "associated"})

#: Signed effects. activation/inhibition act on activity (agonist, activator / antagonist,
#: inhibitor, blocker); increase/decrease act on amount or expression (up-regulates
#: quantity, an eQTL's sign, a perturbation signature); binding has no sign.
EFFECTS = frozenset({"activation", "inhibition", "increase", "decrease", "binding",
                     "degradation", "modulation", "other"})
OUTCOMES = frozenset({"positive", "negative", "inconclusive"})
CONTEXT_KEYS = ("species", "cell", "tissue", "part", "dose", "dose_unit", "concentration",
                "time", "method", "assay", "direct", "mechanism", "residue", "action",
                "measure", "value", "unit", "pvalue", "beta", "se", "n", "variant",
                "effect_allele", "flags", "qc", "confidence", "dataset", "sample", "study",
                "screen", "library", "phenotype", "zygosity", "sex", "stage", "condition",
                "genome_build", "model", "instrument", "ion_mode", "source_db", "source_id")

COLUMNS = ("kind", "source", "subject_type", "subject_id", "subject_name", "object_type",
           "object_id", "object_name", "evidence", "score", "reference", "note", "effect",
           "outcome", "context", "license")

_NA = frozenset({"", "na", "n/a", "nan", "none", "null", "-", "--"})

Row = dict[str, Any]


def register_kinds(kinds: Mapping[str, tuple[str, str]]) -> None:
    """Add relation kinds; a kind already registered must keep its subject and object."""
    for kind, types in kinds.items():
        have = RELATION_KINDS.get(kind)
        if have is not None and tuple(have) != tuple(types):
            raise ValueError(f"relation kind {kind!r} is {have}, not {types}")
        RELATION_KINDS[kind] = tuple(types)


def v(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NA else text


def names(*values: Any) -> str | None:
    out: list[str] = []
    for value in values:
        for part in re.split(r"\s*[;|]\s*", v(value) or ""):
            part = part.strip()
            if part and part.lower() not in _NA and part not in out:
                out.append(part)
    return " | ".join(out) or None


def cid(value: Any) -> str | None:
    text = v(value)
    if not text:
        return None
    text = text.split(".")[0] if re.fullmatch(r"\d+\.0", text) else text
    return f"pubchem:{text}" if text.isdigit() else None


def ctx(**values: Any) -> str | None:
    """A context as compact JSON, without empty values; ``None`` when nothing is known."""
    clean = {k: val for k, val in values.items()
             if val is not None and not (isinstance(val, str) and v(val) is None)}
    unknown = set(clean) - set(CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"unknown context keys {sorted(unknown)}; have {CONTEXT_KEYS}")
    return json.dumps(clean, ensure_ascii=False, sort_keys=True) if clean else None


def rel(kind: str, source: str, subject_id: Any, subject_name: Any, object_id: Any,
        object_name: Any, evidence: str, *, score: Any = None, reference: Any = None,
        note: Any = None, subject_type: str | None = None, object_type: str | None = None,
        effect: str | None = None, outcome: str = "positive", context: str | None = None,
        license: str | None = None) -> Row | None:
    """One relation row, or ``None`` when either end has no id."""
    sid, oid = v(subject_id), v(object_id)
    if not sid or not oid:
        return None
    if effect is not None and effect not in EFFECTS:
        raise ValueError(f"effect {effect!r} is not one of {sorted(EFFECTS)}")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome {outcome!r} is not one of {sorted(OUTCOMES)}")
    st, ot = RELATION_KINDS[kind]
    return {"kind": kind, "source": source, "subject_type": subject_type or st,
            "subject_id": sid, "subject_name": v(subject_name),
            "object_type": object_type or ot, "object_id": oid, "object_name": v(object_name),
            "evidence": evidence, "score": v(score), "reference": v(reference),
            "note": v(note), "effect": effect, "outcome": outcome, "context": context,
            "license": license}


def unresolved(kind: str, source: str, subject_id: Any, subject_name: Any, object_name: Any,
               reason: str, *, reference: Any = None, note: Any = None) -> Row:
    """A row the source gives but whose object it could not identify.

    It is not a relation (there is nothing to join it to) and it is not dropped: it goes
    to the store's ``unresolved`` table, a queue for a person or a later mapping to
    resolve. Merging such rows under one placeholder id would make one compound of every
    compound the source could not identify.
    """
    return {"_unresolved": reason, "kind": kind, "source": source,
            "subject_id": v(subject_id), "subject_name": v(subject_name),
            "object_name": v(object_name), "reference": v(reference), "note": v(note)}


def has(conn: sqlite3.Connection, *tables: str) -> bool:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in have for t in tables)


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Iterator[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    yield from conn.execute(sql, params)


def col(row: sqlite3.Row, *candidates: str) -> Any:
    keys = {k.lower(): k for k in row.keys()}
    for n in candidates:
        if n.lower() in keys:
            return row[keys[n.lower()]]
    return None
