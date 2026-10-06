"""The formula table: 84,294 classical and modern formulas, parsed and resolved to crude drugs.

``中医方剂数据表.xlsx`` (one sheet, columns 名称 · 配方 · 出处 · 炮制 · 功效 · 使用方法 · 注意)
records each formula's composition as free text, e.g.::

    硫黄3分，茴香半两，朱砂3分，木香半两，…
    （犭屯）心1具，人参1两，桂心1两，甘草（炙）1两，…

This module turns that text into structured components and resolves each ingredient name
to a crude drug in :mod:`bioagent.sources.materia`. Parsing is literal and conservative:

* items are split on 、，；。 only *outside* parentheses, so a processing note such as
  （去毛，水、酒各半煮烂）stays with its ingredient;
* the dose is read off the end of an item *before* the name is normalised (``3分``,
  ``半两``, ``1.5g``, ``钱半``, ``各等分``, ``少许``), so 半夏半两 is 半夏 + 半两, 半夏 keeps
  its 半, and 附子1.5g keeps its decimal point (normalising first dropped it: 1.5g became
  15g). ``parse_dose`` reads the amount and unit, and keeps a number only when writing it
  back gives the dose as written;
* a fragment that is only processing instructions (``去皮尖双仁``, ``切``) is attached to
  the previous ingredient rather than counted as one; a fragment with a dose of its own
  (蒸馏水100ml) is an ingredient, never a note;
* a name is resolved exactly, or with one processing prefix/suffix removed
  (:func:`materia.resolve_name`), or by its longest known leading name **when what follows
  is a processing instruction** (甘草去皮, 当归酒浸). Anything else that follows a known
  name is not read as processing: a part (麻黄根 is not 麻黄), a product (鹿角胶, 灯心灰)
  or a constituent (黄连素, 人参皂苷) is a different thing. Such names, and anything else
  the materia table does not know, are **unresolved** and kept as written, and
  ``Component.kind`` says what an unresolved name looks like (a compound, a constituent
  group, an extract).

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
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from .materia import MATERIA, normalise_name, resolve_name

__all__ = ["TABLE_FILE", "TABLE_LICENSE", "Component", "Dose", "FormulaRecord", "FormulaTable",
           "parse_composition", "parse_dose", "load_formula_table"]

TABLE_FILE = Path(__file__).resolve().parents[4] / "中医方剂数据表.xlsx"
TABLE_LICENSE = "LicenseRef-user-supplied-unstated"
COLUMNS = ("名称", "配方", "出处", "炮制", "功效", "使用方法", "注意")

_OPEN, _CLOSE = "（([【〔", "）)]】〕"
_SEPARATORS = "，,、；;。"
_NUM = r"[\d０-９.．/～~\-—至一二三四五六七八九十百千半两]"
_UNIT = (r"(?:钱匕|方寸匕|刀圭|两|钱|分|厘|斤|升|合|斗|个|枚|片|只|具|克|g|G|kg|mg|ml|mL|粒|条|"
         r"寸|尺|撮|握|茎|把|份|株|对|丸|匙|勺|盏|碗|杯|字|铢|口|头|节|段|块|张|朵|团|叶|挺|梃|文)")
#: A dose at the end of an item: numbers and units (1钱2分, 2分5厘), a trailing 半 (1分半,
#: and 钱半 for 一钱半), or one of the phrases the table writes instead of an amount.
_DOSE = re.compile(
    rf"(?:各)?(?:{_NUM}+{_UNIT}?(?:{_NUM}+{_UNIT})*半?|{_UNIT}半|等分|少许|适量|若干|不拘多少|"
    rf"不以多少|随意|量用|不拘|减半)$")
_PROCESSING = re.compile(
    r"^(?:去|炒|炙|切|研|为末|焙|洗|浸|煨|煅|蒸|煮|烧|浸|捣|细|锉|剉|刮|微|酒|醋|盐|姜|蜜|"
    r"水|汤|童便|米泔|各|生用|熟用|炮|制|晒|阴干|杵|碎|筛|绢|拣|净|打|入|用|取|同|另)")
#: What may follow a known drug name and still be that drug: a processing instruction,
#: a medium and a verb (酒浸, 姜汁炒) or a verb (去皮, 炒黄, 为末), or a size (大者). A part
#: (根, 叶, 花, 子), a product (胶, 灰, 油, 霜) or a constituent (素, 皂苷) is not one.
_INSTRUCTION = re.compile(
    r"^(?:(?:微|略|慢火|文火|武火|细|各|另|同|先|后|再)*"
    r"(?:姜汁|童便|米泔|盐水|酒|醋|盐|姜|蜜|麸|土|砂|水|汤|乳|猪脂|胆汁)?"
    r"(?:去|炒|炙|切|研|焙|洗|浸|淬|煨|煅|蒸|煮|烧|捣|锉|剉|刮|炮|制|晒|杵|碎|筛|拣|净|拌|"
    r"泡|渍|熬|煎|烘|熏|焦|炼|擂|为末|阴干|生用|熟用)|(?:大|小)者|如.{1,6}大)")
#: Endings by which an unresolved name reads as something other than a crude drug.
_KINDS = (
    ("constituent_group", re.compile(r"(?:总黄酮|总皂苷|总生物碱|总苷|总蒽醌|多糖|皂苷|黄酮|"
                                     r"生物碱|挥发油)$")),
    ("extract", re.compile(r"(?:提取物|浸膏|流浸膏|浸出物|酊|注射液|精油)$")),
    ("compound", re.compile(r"(?:素|碱|苷|甙|酸|酮|醇|酯|醛|酚|醌|烯|萜)$")),
)


@dataclass(frozen=True)
class Dose:
    """A dose as written, and its amount when the amount is one plain number."""

    written: str                      # 1.5g, 三两, 钱半, 各等分
    amount: Decimal | None = None     # 1.5, 3, 1.5; None when the dose is not one number
    unit: str = ""                    # g, 两, 钱; "" for a bare number or a phrase
    each: bool = False                # 各: the amount is for each ingredient so marked


_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
              "九": 9}
_UNIT_ONLY = re.compile(rf"^{_UNIT}$")
_ARABIC = re.compile(r"[\d０-９]+(?:[.．][\d０-９]+)?")
_FULLWIDTH = str.maketrans("０１２３４５６７８９．", "0123456789.")


def _amount(number: str) -> Decimal | None:
    """1.5, ０．５, 三, 两, 十二, 半: an amount the table writes as one number."""
    if _ARABIC.fullmatch(number):
        return Decimal(number.translate(_FULLWIDTH))
    if number == "半":
        return Decimal("0.5")
    if number in _CN_DIGITS:
        return Decimal(_CN_DIGITS[number])
    m = re.fullmatch(r"([二两三四五六七八九]?)十([一二三四五六七八九]?)", number)
    if m:
        tens = _CN_DIGITS[m.group(1)] if m.group(1) else 1
        return Decimal(tens * 10 + (_CN_DIGITS[m.group(2)] if m.group(2) else 0))
    return None


def _rewritten(amount: Decimal, like: str) -> str:
    """``amount`` written back in the notation of ``like``, to compare with ``like``."""
    if _ARABIC.fullmatch(like):
        return format(amount, "f")
    if amount == Decimal("0.5"):
        return "半"
    two = "两" if like.startswith("两") else "二"
    digits = {1: "一", 2: two, 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}
    n = int(amount)
    if n < 10:
        return digits[n]
    tens, units = divmod(n, 10)
    return ("" if tens == 1 else digits[tens]) + "十" + (digits[units] if units else "")


def parse_dose(written: str) -> Dose:
    """The amount and unit of a dose, read only where they round-trip.

    1.5g is 1.5 g, 三两 is 3 两, 两钱 is 2 钱, 钱半 and 一钱半 are 1.5 钱, 1分半 is 1.5 分.
    The amount is kept only when writing it back gives exactly the number as written, so
    a dose that lost a character on the way (05g, which is 0.5g with its decimal point
    dropped) is never read as a number. Phrases (各等分, 少许) and doses in two units
    (1钱2分) keep no amount.
    """
    text = written or ""
    each = text.startswith("各")
    body = text[1:] if each else text
    for half in (False, True):
        if half and not body.endswith("半"):
            continue
        core = body[:-1] if half else body
        for i in range(len(core) + 1):
            number, unit = core[:i], core[i:]
            if unit and not _UNIT_ONLY.match(unit):
                continue
            if not number:
                if not (half and unit):            # 钱半 is 一钱半; a bare unit is nothing
                    continue
                amount = Decimal(1)
            else:
                amount = _amount(number)
                if amount is None:
                    continue
                plain = number.translate(_FULLWIDTH)
                if _rewritten(amount, number) != (plain if _ARABIC.fullmatch(number)
                                                  else number):
                    continue                       # 05 is not how 5 is written
            return Dose(written, amount + (Decimal("0.5") if half else 0), unit, each)
    return Dose(written, None, "", each)


@dataclass(frozen=True)
class Component:
    written: str                      # the item as the table wrote it, parentheses included
    name: str                         # the name part, bare
    dose: str = ""                    # as written, decimal point and all (1.5g)
    processing: str = ""              # parenthesised notes and attached fragments
    drug: str | None = None           # materia id, or None when unresolved

    @property
    def resolved(self) -> bool:
        return self.drug is not None

    @property
    def parsed_dose(self) -> Dose:
        return parse_dose(self.dose)

    @property
    def kind(self) -> str:
        """``crude_drug`` when resolved; otherwise what the name reads as: a ``compound``
        (黄连素, 甘草酸), a ``constituent_group`` (人参皂苷, 总黄酮), an ``extract``
        (浸膏), or ``unresolved``."""
        if self.drug is not None:
            return "crude_drug"
        name = self.name or self.written
        return next((kind for kind, ending in _KINDS if ending.search(name)), "unresolved")


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

    @property
    def unresolved_kinds(self) -> tuple[tuple[str, str], ...]:
        """(name, kind) of each unresolved ingredient: 黄连素 is a compound, not 黄连."""
        return tuple((c.name or c.written, c.kind) for c in self.components if not c.resolved)

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


#: A decimal point that is not between two digits, and the rest of what ``normalise_name``
#: removes: 附子1.5g keeps its point while 甘草。 loses its full stop.
_LOOSE_POINT = re.compile(r"(?<![\d０-９])[.．]|[.．](?![\d０-９])")
_PUNCT = re.compile(r"[\s　。，、；;,:：]+")


def _bare_with_numbers(item: str) -> str:
    """The item without its parenthesised notes and punctuation, decimal points kept."""
    text = re.sub(r"[（(][^）)]*[）)]", "", item or "")
    return _PUNCT.sub("", _LOOSE_POINT.sub("", text))


def _attach(prev: Component, item: str) -> Component:
    return Component(prev.written + "，" + item, prev.name, prev.dose,
                     "；".join(x for x in (prev.processing, item) if x), prev.drug)


def parse_composition(text: str) -> tuple[Component, ...]:
    """Structured components of one 配方 cell."""
    out: list[Component] = []
    for item in _split_outside_parens(text or ""):
        notes = "；".join(m.strip("（）()[]【】〔〕") for m in re.findall(r"[（(【〔\[][^）)】〕\]]*[）)】〕\]]", item))
        # The dose is split off first, decimal points and all; only the name is normalised.
        bare = _bare_with_numbers(item)
        name_part, dose = _split_dose(bare)
        name = normalise_name(name_part)
        # A dose that swallowed the whole item ("半两") leaves no name: keep it as written.
        if not name and out and _PROCESSING.match(bare or item):
            out[-1] = _attach(out[-1], item)
            continue
        drug = resolve_name(name)
        extra = ""
        if drug is None and name:
            prefix, rest = _longest_known_prefix(name)
            looks_like = next((k for k, ending in _KINDS if ending.search(name)), "")
            if prefix is not None and not looks_like and _INSTRUCTION.match(rest):
                drug, extra = prefix, rest
            elif not dose and out and _PROCESSING.match(name):
                # instructions for the previous ingredient (炒黄, 去皮尖); a fragment with a
                # dose of its own (蒸馏水100ml) is an ingredient, never a note
                out[-1] = _attach(out[-1], item)
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
