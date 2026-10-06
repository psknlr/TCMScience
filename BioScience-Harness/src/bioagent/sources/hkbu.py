"""Reviewed HKBU formula compositions as formula versions for the herb layer.

The hub reads ``formula_herb.tsv`` (catalogue 134, ``tcmdb.extra.traditional``) for
lookups. This module reads the same reviewed file for research: each formula becomes a
``herbs.FormulaVersion`` that the herb-layer snapshot can record and the
network-pharmacology skill can study (``--formula hkbu:formula.<formula_id>``), the way a
formula of the user-supplied formula table is studied.

The file is read with the hub's strict template reader, so a file the hub's build
refuses is refused here, with the same message. A formula is exported only when every one
of its rows can be studied:

* reviewed: the row's ``review_status`` is ``verified``. A pending, rejected or misspelt
  status holds back the whole formula: analysing the reviewed rows as if they were all of
  it would describe a different formula;
* complete: the row has a herb id, a source row id and a reference, as the hub's
  extractor requires before a row becomes a relation;
* consistent: the formula's rows agree on its name, version and reference, and no source
  row id is used by two rows;
* resolved: the herb name resolves to one crude drug of ``sources.materia``, as written
  or with its traditional characters simplified (黃芩 is 黄芩), and no crude drug is listed
  twice: 生甘草 and 炙甘草 in one formula are two forms of one drug, and the herb layer
  records one composition edge per drug.

Anything else is listed in ``excluded`` with its reasons, never guessed. A component keeps
the row's processing (黄芪 with 炙 is 黄芪, processing 炙; 生 and its variants are the crude
drug), its recorded dose and unit, and its name as written when that is not the drug's
own name.

Licence: not stated, as the catalogue says. The composition edges carry ``LICENSE``, which
a commercial run refuses (``analysis.skill_runner``), and name the HKBU export as their
primary knowledge source; each edge's record id is the row's ``source_row_id``. Like
every composition in the herb layer they are recorded as ``classical_text``; they are
definitional and license no claim on their own (``release``).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..tcmdb.extra import traditional
from .herbs import FormulaVersion
from .materia import MATERIA, normalise_name, resolve_name, to_simplified
from .schema import is_curie

__all__ = ["KEY", "LICENSE", "CITATION", "ID_PREFIX", "HKBUFormulas", "load_hkbu_formulas",
           "formula_id"]

KEY = traditional.KEY_FORMULAS
LICENSE = "LicenseRef-hkbu-formulas-unstated"
CITATION = ("HKBU Chinese Medicine Formulae Images Database, reviewed manual export "
            "(catalogue 134; licence not stated)")
ID_PREFIX = "hkbu:formula."


def formula_id(hkbu_id: str) -> str:
    """The herb layer's id for the formula the export lists as ``hkbu_id``."""
    return ID_PREFIX + hkbu_id


@dataclass(frozen=True)
class HKBUFormulas:
    path: str
    digest: str
    rows: int
    versions: tuple[FormulaVersion, ...]
    #: the export's formula_id -> why the formula was not exported
    excluded: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def by_id(self, fid: str) -> FormulaVersion | None:
        """The exported formula with herb-layer id ``fid`` (``hkbu:formula.…``)."""
        return next((f for f in self.versions if f.id == fid), None)

    def why_excluded(self, fid: str) -> tuple[str, ...] | None:
        """Why the formula with herb-layer id ``fid`` was not exported, if it was not."""
        return self.excluded.get(fid[len(ID_PREFIX):] if fid.startswith(ID_PREFIX) else fid)

    def stats(self) -> dict[str, int]:
        return {"rows": self.rows, "formulas": len(self.versions) + len(self.excluded),
                "exported": len(self.versions), "excluded": len(self.excluded)}


def _drug(written: str) -> str | None:
    return resolve_name(written) or resolve_name(to_simplified(written))


def _formula(fid: str, rows: list[tuple[int, dict]], shared: set[str]
             ) -> tuple[FormulaVersion | None, list[str]]:
    problems: list[str] = []
    if not is_curie(formula_id(fid)):
        problems.append(f"formula_id {fid!r} cannot be part of an identifier (no spaces)")
    first = rows[0][1]
    for column in ("formula_name", "formula_version", "reference"):
        values = sorted({r.get(column) or "" for _, r in rows})
        if len(values) > 1:
            problems.append(f"its rows disagree on {column}: {values}")
    if not first.get("formula_name"):
        problems.append("no formula_name")
    components, record_ids, written, seen = [], [], [], {}
    for n, r in rows:
        rid = r.get("source_row_id") or ""
        label = rid or f"data row {n}"
        status = r.get("review_status") or ""
        if status != traditional.VERIFIED:
            problems.append(f"{label}: review_status {status or '(empty)'}, not "
                            f"{traditional.VERIFIED}")
        problems += [f"{label}: no {column}" for column in ("herb_id", "source_row_id",
                                                            "reference") if not r.get(column)]
        if rid in shared:
            problems.append(f"{label}: source_row_id is used by more than one row")
        name = r.get("herb_name") or ""
        drug = _drug(name) if name else None
        if drug is None:
            problems.append(f"{label}: herb {name or '(empty)'} does not resolve to a crude "
                            "drug in sources.materia")
            continue
        herb = f"tcm:herb.{drug}"
        if herb in seen:
            problems.append(f"{label}: {herb} is listed again (also {seen[herb]}); the herb "
                            "layer records one composition per crude drug")
            continue
        seen[herb] = label
        processing = r.get("processing") or ""
        if processing.lower() in traditional.CRUDE_PROCESSING:
            processing = ""
        components.append((herb, "", "".join(x for x in (r.get("dose"), r.get("dose_unit"))
                                             if x), processing))
        record_ids.append(f"{KEY}:{rid}")
        written.append("" if normalise_name(name) == MATERIA[drug].chinese else name)
    if problems:
        return None, problems
    source = first.get("reference") or ""
    if first.get("formula_version"):
        source = f"{source}·{first['formula_version']}"
    return FormulaVersion(
        formula_id(fid), first["formula_name"], source, tuple(components), license=LICENSE,
        primary_source=KEY, record_ids=tuple(record_ids),
        written=tuple(written) if any(written) else ()), []


def load_hkbu_formulas(path: str | Path) -> HKBUFormulas:
    """The formulas of a reviewed ``formula_herb.tsv`` that can be studied, and why the
    others cannot. Raises ``tcmdb.store.StoreError`` for a file that breaks the template."""
    path = Path(path)
    records = traditional.read_template(path, "formula_herb")
    groups: dict[str, list[tuple[int, dict]]] = {}
    for n, r in enumerate(records, start=1):
        groups.setdefault(r.get("formula_id") or "", []).append((n, r))
    ids = Counter(r.get("source_row_id") for r in records if r.get("source_row_id"))
    shared = {rid for rid, count in ids.items() if count > 1}
    versions: list[FormulaVersion] = []
    excluded: dict[str, tuple[str, ...]] = {}
    for fid, rows in sorted(groups.items()):
        if not fid:
            excluded[""] = tuple(f"{r.get('source_row_id') or f'data row {n}'}: no formula_id"
                                 for n, r in rows)
            continue
        version, problems = _formula(fid, rows, shared)
        if version is None:
            excluded[fid] = tuple(dict.fromkeys(problems))
        else:
            versions.append(version)
    return HKBUFormulas(str(path), "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                        len(records), tuple(versions), excluded)
