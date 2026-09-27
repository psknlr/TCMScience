"""The formula table: 84,294 classical and modern formulas, parsed and resolved to crude drugs.

``中医方剂数据表.xlsx`` (one sheet, columns 名称 · 配方 · 出处 · 炮制 · 功效 · 使用方法 · 注意)
records each formula's composition as free text, e.g.::

    硫黄3分，茴香半两，朱砂3分，木香半两，…
    （犭屯）心1具，人参1两，桂心1两，甘草（炙）1两，…

This module turns that text into structured components and resolves each ingredient name
to a crude drug in :mod:`bioagent.sources.materia`. Parsing is literal and conservative:

* items are split on 、，；。 only *outside* parentheses, so a processing note such as
  （去毛，水、酒各半煮烂）stays with its ingredient;
* the dose is read off the end of an item (``3分``, ``半两``, ``各等分``, ``少许``), so 半夏半两 is
  半夏 + 半两 and 半夏 keeps its 半;
* a fragment that is only processing instructions (``去皮尖双仁``, ``切``) is attached to
  the previous ingredient rather than counted as one;
* a name is resolved exactly, or with one processing prefix/suffix removed
  (:func:`materia.resolve_name`), or by its longest known leading name; anything else is
  **unresolved** and kept as written.

A formula is *resolved* only when every ingredient is. Only resolved formulas can be
studied: an unresolved ingredient could be any organism, and analysing the rest as if it
were absent would describe a different formula.

The table's origin and licence are not stated. Its rows are read at run time from the
file the user supplied; nothing derived from them is redistributed beyond the snapshot a
run builds locally, and every edge built from them carries the licence
``LicenseRef-user-supplied-unstated``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .materia import MATERIA, normalise_name, resolve_name

__all__ = ["TABLE_FILE", "TABLE_LICENSE", "Component", "FormulaRecord", "FormulaTable",
           "parse_composition", "load_formula_table"]

TABLE_FILE = Path(__file__).resolve().parents[4] / "中医方剂数据表.xlsx"
TABLE_LICENSE = "LicenseRef-user-supplied-unstated"
COLUMNS = ("名称", "配方", "出处", "炮制", "功效", "使用方法", "注意")

_OPEN, _CLOSE = "（([【〔", "）)]】〕"
_SEPARATORS = "，,、；;。"
_NUM = r"[\d０-９.．/～~\-—至一二三四五六七八九十百千半两]"
_UNIT = (r"(?:钱匕|方寸匕|刀圭|两|钱|分|斤|升|合|斗|个|枚|片|只|具|克|g|G|kg|mg|ml|粒|条|寸|尺|"
         r"撮|握|茎|把|份|株|对|丸|匙|勺|盏|碗|杯|字|铢|钱半|口|头|节|段|块|张|朵|团|叶)")
_DOSE = re.compile(
    rf"(?:各)?(?:{_NUM}+{_UNIT}?(?:{_NUM}+{_UNIT})*|等分|少许|适量|若干|不拘多少|随意|量用|不拘)$")
_PROCESSING = re.compile(
    r"^(?:去|炒|炙|切|研|为末|焙|洗|浸|煨|煅|蒸|煮|烧|浸|捣|细|锉|剉|刮|微|酒|醋|盐|姜|蜜|"
    r"水|汤|童便|米泔|各|生用|熟用|炮|制|晒|阴干|杵|碎|筛|绢|拣|净|打|入|用|取|同|另)")


@dataclass(frozen=True)
class Component:
    written: str                      # the item as the table wrote it, parentheses included
    name: str                         # the name part, bare
    dose: str = ""
    processing: str = ""              # parenthesised notes and attached fragments
    drug: str | None = None           # materia id, or None when unresolved

    @property
    def resolved(self) -> bool:
        return self.drug is not None


@dataclass(frozen=True)
class FormulaRecord:
    row: int
    written_name: str
    name: str
    source: str
    composition: str
    components: tuple[Component, ...]
    preparation: str = ""
    actions: str = ""
    usage: str = ""
    cautions: str = ""

    @property
    def id(self) -> str:
        """Stable across re-sorts of the table: a digest of name, source and composition."""
        blob = "\x1f".join((self.name, self.source, self.composition))
        return "tcm:formula.fx" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]

    @property
    def resolved(self) -> bool:
        return bool(self.components) and all(c.resolved for c in self.components)

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(c.name or c.written for c in self.components if not c.resolved)

    def version(self):
        """This record as a ``herbs.FormulaVersion`` (resolved records only)."""
        from .herbs import FormulaVersion
        if not self.resolved:
            raise ValueError(f"{self.name}: unresolved ingredients {list(self.unresolved)}")
        return FormulaVersion(
            self.id, self.name, self.source or "未注明出处",
            tuple((f"tcm:herb.{c.drug}", "", c.dose, c.processing) for c in self.components),
            license=TABLE_LICENSE)


def _split_outside_parens(text: str) -> list[str]:
    items, depth, current = [], 0, []
    for ch in text:
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        if ch in _SEPARATORS and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return [i.strip() for i in items if i.strip()]


def _split_dose(bare: str) -> tuple[str, str]:
    """(name, dose) for one bare item.

    Several suffixes can read as a dose: in 百合1两 both 百合1两 (百 a numeral, 合 a unit)
    and 1两 do. The split that leaves a known drug name wins; failing that, the longest
    dose that still leaves a name; an item that is all dose stays as written.
    """
    starts = [i for i in range(len(bare)) if _DOSE.fullmatch(bare[i:])]
    for i in starts:
        if i and resolve_name(bare[:i]):
            return bare[:i], bare[i:]
    for i in starts:
        if i:
            return bare[:i], bare[i:]
    return (("", bare) if starts else (bare, ""))


def _longest_known_prefix(name: str) -> tuple[str | None, str]:
    for end in range(len(name), 1, -1):
        drug = resolve_name(name[:end])
        if drug:
            return drug, name[end:]
    return None, ""


def parse_composition(text: str) -> tuple[Component, ...]:
    """Structured components of one 配方 cell."""
    out: list[Component] = []
    for item in _split_outside_parens(text or ""):
        notes = "；".join(m.strip("（）()[]【】〔〕") for m in re.findall(r"[（(【〔\[][^）)】〕\]]*[）)】〕\]]", item))
        bare = normalise_name(item)
        name, dose = _split_dose(bare)
        # A dose that swallowed the whole item ("半两") leaves no name: keep it as written.
        if not name and out and _PROCESSING.match(bare or item):
            prev = out[-1]
            out[-1] = Component(prev.written + "，" + item, prev.name, prev.dose,
                                "；".join(x for x in (prev.processing, item) if x), prev.drug)
            continue
        drug = resolve_name(name)
        extra = ""
        if drug is None and name:
            drug, extra = _longest_known_prefix(name)
            if drug is None and out and _PROCESSING.match(name):
                prev = out[-1]
                out[-1] = Component(prev.written + "，" + item, prev.name, prev.dose,
                                    "；".join(x for x in (prev.processing, item) if x),
                                    prev.drug)
                continue
        processing = "；".join(x for x in (notes, extra) if x)
        out.append(Component(item, name, dose, processing, drug))
    return tuple(out)


_NAME_SOURCE = re.compile(r"[（(]《[^）)]*[）)]\s*$")


def _clean_name(written: str) -> str:
    return _NAME_SOURCE.sub("", (written or "").strip()).strip()


@dataclass
class FormulaTable:
    records: list[FormulaRecord]
    path: str = ""
    digest: str = ""
    _by_name: dict[str, list[FormulaRecord]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for rec in self.records:
            self._by_name.setdefault(rec.name, []).append(rec)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[FormulaRecord]:
        return iter(self.records)

    @property
    def resolved(self) -> list[FormulaRecord]:
        return [r for r in self.records if r.resolved]

    def named(self, name: str) -> list[FormulaRecord]:
        return list(self._by_name.get(name, ()))

    def names_in(self, text: str, *, min_length: int = 3) -> list[str]:
        """Formula names that occur in ``text``, longest first; nested matches dropped."""
        found = sorted((n for n in self._by_name if len(n) >= min_length and n in text),
                       key=len, reverse=True)
        kept: list[str] = []
        for n in found:
            if not any(n in k for k in kept):
                kept.append(n)
        return kept

    def by_id(self, formula_id: str) -> FormulaRecord | None:
        if not hasattr(self, "_ids"):
            self._ids = {r.id: r for r in self.records}
        return self._ids.get(formula_id)

    def stats(self) -> dict[str, float | int]:
        comps = [c for r in self.records for c in r.components]
        return {"formulas": len(self.records), "resolved_formulas": len(self.resolved),
                "components": len(comps),
                "resolved_components": sum(c.resolved for c in comps),
                "drugs_used": len({c.drug for c in comps if c.drug}),
                "drugs_in_table": len(MATERIA)}


def records_from_rows(rows: Iterable[Sequence[object]]) -> list[FormulaRecord]:
    out = []
    for i, row in enumerate(rows, start=2):                 # spreadsheet row numbers
        cells = [str(c).strip() if c is not None else "" for c in (list(row) + [None] * 7)[:7]]
        if not cells[0] and not cells[1]:
            continue
        out.append(FormulaRecord(
            row=i, written_name=cells[0], name=_clean_name(cells[0]), source=cells[2],
            composition=cells[1], components=parse_composition(cells[1]),
            preparation=cells[3], actions=cells[4], usage=cells[5], cautions=cells[6]))
    return out


@lru_cache(maxsize=2)
def load_formula_table(path: str | Path = TABLE_FILE) -> FormulaTable:
    """Read and parse the table. Needs ``openpyxl`` (``pip install bioagent[formulas]``)."""
    try:
        import openpyxl
    except ImportError as exc:                              # pragma: no cover
        raise ImportError("reading the formula table needs openpyxl: "
                          "pip install 'bioagent[formulas]'") from exc
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no formula table at {path}")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        header = tuple(str(c).strip() if c is not None else "" for c in next(rows))
        if header[:7] != COLUMNS:
            raise ValueError(f"unexpected columns {header}; expected {COLUMNS}")
        records = records_from_rows(rows)
    finally:
        wb.close()
    return FormulaTable(records, path=str(path), digest=digest)


def component_counts(records: Iterable[FormulaRecord]) -> Mapping[str, int]:
    """Unresolved ingredient names and how often they occur — the backlog for materia."""
    counts: dict[str, int] = {}
    for rec in records:
        for c in rec.components:
            if not c.resolved:
                counts[c.name or c.written] = counts.get(c.name or c.written, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
