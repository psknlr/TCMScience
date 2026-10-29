"""Reading an allergy history against the herbs of a prescription (过敏史核查).

A history is not read through a list of wordings (过敏史：X, 对X过敏, X过敏史 …): every wording
such a list missed was a silent pass. The record is normalised and *scanned* for every name
the tables hold, wherever it stands, and each name found is then read in its place.

Normalising (``fold``)
    NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens, variation
    selectors, fillers, NUL and the other control characters removed; look-alike radicals
    (⻩ for 黄, ⻰ for 龙) read as the characters they stand for; traditional characters
    folded with the materia table's own table (``materia.to_simplified``: 黃耆 → 黄耆, 人參
    → 人参) and, for the characters of every name the tables know and the words a history
    writes around them, with the traditional, Hong Kong, Taiwan and Japanese forms that table
    leaves (過敏 → 过敏, 黑順片 → 黑顺片, 人銜 → 人衔, 当帰 → 当归); tone marks dropped from
    Latin letters (Huáng Qí → huang qi); lower case. One character stays one character.

Scanning (``_Reader``)
    The names are the tables' own, and nothing else: the seed knowledge base's herb names and
    aliases (黄耆, 北芪, 国老), every name of every entry of the materia table, one character
    included (姜, 枣), the pack's herbs and its cited aliases (``herb_aliases``: 夜交藤, 川黄柏),
    and the formulas the pack and the knowledge base hold, under their names and in other
    product forms (补中益气丸, 天麻钩藤颗粒; 散 only for the pack's formulas, since 四逆散 is
    not 四逆汤). No part or abbreviation of a name is scanned for unless a table lists it.
    Latin and pinyin are read as whole words, with or without spaces, hyphens, tone marks or
    tone numbers: the knowledge base's pinyin and Latin names, the pack's pinyin of every
    herb, the materia table's Latin names in either word order (Astragali Radix, Radix
    Astragali), the species and genera the tables give (Panax ginseng, Astragalus) and the
    pack's cited English common names (ginger, licorice).

    The record is read twice: with whitespace as a hard boundary (an allergy list written one
    per line, 姜 / 黄芪, is 姜 and 黄芪), and with whitespace removed (北 芪 is 北芪). The names
    found are the union of the two readings. A name found only across whitespace is dropped
    where it overlaps a name of two characters or more that the first reading found standing
    (姜 / 黄芪 is not 姜黄), or where it stands in neither reading and overlaps a name the first
    reading found (枣 / 皮疹 is not 枣皮). Every name at every place is a candidate, so a
    shorter name is not lost behind a longer one that starts at the same place (枣皮疹 holds
    枣 standing before 皮疹, and 枣皮 inside a longer word).

Reading each name in its place
    * A name *stands* when what is before it and after it is a boundary (the start or end of
      the record, punctuation, a space in the first reading, a digit, Latin letters) or one
      of the words a history writes around a name (对, 患者, 自诉, 因, 的, 吃, 年前, 过敏, 过敏史,
      及, 等, 后, 就, 皮疹 …), or another name of two characters or more that stands that way
      on its own far side (北芪人参过敏). A formula's name stands only with a boundary of its
      own before it (柴胡桂枝汤 holds 桂枝汤 inside a longer word). A name of one character
      stands only with a boundary of its own on both sides, from a shorter list of words (对,
      服用, 吃, 及, 和, 因, 年, 中药, 过敏, 过敏史, 后, 就, 皮疹, 出现, 引起, 致, 会, 严重, 否认 …:
      对姜过敏, 姜过敏, 、姜、, 对姜严重过敏, 吃姜出现皮疹), never through a neighbouring name and
      never inside a word (面部, 姜某, 枣红色, 归档, 归类, 姜片).
    * A name whose first character ends an everyday word (于术 after 对于, 白及 after 蛋白) is
      inside that word, unless the everyday word's first character stands as a word of a
      history and a word of allergy follows the name: 对于术过敏 is 对 + 于术 + 过敏, 对于术后
      is 对于 + 术后.
    * A name inside a longer standing name, or inside a formula's name, is that name's (杏仁
      in 苦杏仁, 地黄 in 六味地黄丸, 天麻 in 天麻钩藤颗粒); the longer name is matched instead. A
      few everyday words that hold a name are words of their own (胡萝卜, 桂花, 舌头), and a
      name that does not stand and shares a character with one is that word's.
    * A name of two characters or more that does not stand is *inside a longer word*: 郁金 in
      郁金香, 地黄 in 知柏地黄丸, 桂枝 and 茯苓 in 桂枝茯苓丸. Where the longer word is the name
      with a processing word before it, or a product suffix after it (炙北芪, 炒夜交藤, 甘草片,
      山药粉), it is that processed name. A name that starts with a denial's character and
      runs into what a denial names is the denial (没药物过敏史).
    * A name written right after a label that introduces a person (记录人：姜, Dr. Huang Jing,
      interpreter Hong Hua, patient name: Huang Qi), with no word of allergy after it in its
      clause, or right before a title (Jing Jie, MD) with no word of allergy in its clause, is
      a person's name and is not read. After a bare name label (Name: huang qi) it asks.
    * A clause ends at punctuation other than 、, / and an apostrophe, at a space between two
      Chinese characters, and at a line break or a tab whatever stands around it (NKDA, then a
      line 黄芪过敏, is two statements).
    * A name is *denied* when its clause opens with a denial (否认, 无, 没有, 未, 排除, NKDA,
      no …) that no 过敏, 史, 但, 出现, "except", "than" or "allergies" (not followed by to, of
      …) closes before the name (the clause being a bracket inside such a denial: 否认中药
      （黄芪、人参）过敏); when a denial follows the name or the list it ends (黄芪：无, 黄芪（-）,
      黄芪无过敏史, 黄芪耐受); or when the next clause only takes it back (怀疑黄芪过敏，已排除).
      A denial that takes no object (NKDA, NKA, nil, none) does not deny a name with a word of
      allergy after it (NKDA 黄芪过敏). A record that the herb is tolerated (黄芪耐受, 黄芪可用,
      人参可以吃, 人参：可, 服黄芪无不适, 服用黄芪后无不适, 黄芪服用后未见过敏, 人参吃了没事, 当归用过，
      无过敏, 曾服当归，无反应, tolerates 黄芪) is a denial that says so. A word that only starts
      with a denial is none (非常, 未成年, 无意中, no doubt).
    * A name is *taken* when the record says it is taken, eaten or used (目前在吃人参, 现用药：
      人参, 喜食生姜, 每日泡黄芪水喝, 黄芪：服用中, currently on huangqi) and says no reaction to
      it: neither its clause (with the label before it) nor a clause that goes on from it in
      its sentence holds a word of allergy or reaction (过敏, 疹, 痒, 肿, 呼吸困难, 疙瘩, 心慌,
      发麻 …, rash, hives, can't take …; such a word inside a name, 泻 of 泽泻, is the name's).
      A clause goes on when the herb's ends in a time after it (服用黄芪后，皮肤瘙痒) or when it
      opens with a time, a part of the body, a word of feeling or a reaction (服用黄芪，3小时后
      出现皮疹; 吃人参后，嘴唇肿了; 以前喝过黄芪，出现皮疹); not when it names something of its own
      (目前在吃人参，青霉素过敏) or is a label or a denial (目前在吃人参，过敏史：无). A herb named as a
      cause (与服用黄芪有关, 服用黄芪所致) or in a note in brackets on a reaction (皮疹（服黄芪后）)
      is no herb merely taken.
    * A Latin or pinyin name in an English sentence that says nothing of an allergy (no
      allerg*, rash, hives, 过敏 …, and other words than names) is *unconfirmed*: pinyin is
      also how Chinese person names are written.

Each name found is matched to every prescribed herb through the tiers of ``_tier``; see
``allergy_issues`` for the issues they give. The reading is linear in the length of the
record, and each message quotes a bounded excerpt of it.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from .findings import normalise

__all__ = ["fold", "Mention", "mentions", "allergy_issues", "allergy_records_in", "ALLERGY_CODES",
           "names_of"]

ALLERGY_CODES = ("allergy", "allergy_source", "allergy_near_name", "allergy_formula",
                 "allergy_homonym", "allergy_denied", "allergy_taken")


# ============================================================== the name tables
def _resolve(kb: Any, herb: str) -> Any:
    try:
        r = kb.resolve(herb)
    except Exception:                                            # noqa: BLE001
        return None
    return r.entity if getattr(r, "found", False) else None


@lru_cache(maxsize=8192)
def _identity(kb: Any, name: str) -> frozenset[str]:
    """The herb a whole written name denotes by the seed knowledge base's names (黄耆, 北芪 and
    绵芪 are 黄芪, 芍药 is 白芍, 国老 is 甘草); a processed form is its herb, whether the
    knowledge base lists it (炙甘草) or a processing word stands before one of its names
    (炙黄耆, 酒当归). This is drug identity. The materia table's names are not: it groups the
    names of a source, so its 鸡内金 also lists 鸡蛋 and 乌鸡, and its 海藻 海带 and 昆布
    (``_materia``)."""
    from ..sources.materia import PROCESSING_WORDS
    entity = _resolve(kb, name)
    for word in sorted(PROCESSING_WORDS, key=len, reverse=True):
        if entity is not None:
            break
        if name.startswith(word) and len(name) > len(word):
            entity = _resolve(kb, name[len(word):])
    if entity is None:
        return frozenset()
    herb = getattr(entity, "herb_id", "") or entity.id
    return frozenset({herb}) if herb.startswith("herb.") else frozenset()


#: Suffixes that name a form of the drug they follow, not another part of the plant: 甘草片,
#: 山药粉, 生姜汁, 荆芥炭. The materia table also strips 肉, 仁, 心, 头 …, which name another
#: part (米仁 is not 米), and those are not stripped here.
_PRODUCT_SUFFIXES = ("末", "粉", "片", "汁", "炭", "霜", "屑")


@lru_cache(maxsize=8192)
def _materia_id(name: str) -> str | None:
    """The materia table's entry for a written name: the name as the table lists it, or with
    one of its processing or state words before it (炙, 鲜, 干 …) and / or one product suffix
    after it (片, 粉 …) stripped. Never by stripping a word that names another part."""
    from ..sources.materia import _NAME_INDEX, _PREFIXES
    if not name:
        return None
    if name in _NAME_INDEX:
        return _NAME_INDEX[name]
    cands = [name] + [name[len(p):] for p in _PREFIXES if name.startswith(p) and len(name) > len(p)]
    for c in list(cands):
        cands += [c[:-len(s)] for s in _PRODUCT_SUFFIXES if c.endswith(s) and len(c) > len(s)]
    for c in cands[1:]:
        if c in _NAME_INDEX:
            return _NAME_INDEX[c]
    return None


@lru_cache(maxsize=8192)
def _materia(name: str) -> Any:
    """The materia table's entry for a whole written name (``_materia_id``), or None. The
    table files names by source (species or product), for network pharmacology: an entry's
    names are of one source, not always of one drug (鸡内金 with 鸡蛋, 海藻 with 海带)."""
    from ..sources.materia import MATERIA
    drug = _materia_id(name)
    return MATERIA.get(drug) if drug else None


@lru_cache(maxsize=8192)
def _reviewed(name: str) -> str | None:
    """The drug a name is a *reviewed* alias of in the materia table (川当归 is 当归, 大贝母 is
    浙贝母): names the table checked one by one against what they denote, which makes them
    names of the drug and not only of its source."""
    from ..sources.materia import _REVIEWED_ALIASES, MATERIA
    drug = _REVIEWED_ALIASES.get(name)
    return MATERIA[drug].chinese if drug in MATERIA else None


@lru_cache(maxsize=8192)
def _processed(longer: str, shorter: str) -> bool:
    """``longer`` is ``shorter`` with a processing word in front: 炙黄芪 of 黄芪, 熟地黄 of
    地黄, 制附子 of 附子. Not a word of origin or colour (白附子 is not 附子), and not when the
    materia table files ``longer`` under another drug, unless that drug is named ``longer``:
    熟地黄 is the table's own entry for processed 地黄, but its 炮姜 is under 干姜, not 姜
    (which it files with 生姜)."""
    from ..sources.materia import PROCESSING_WORDS
    if not (len(longer) > len(shorter) and longer.endswith(shorter)
            and longer[:-len(shorter)] in PROCESSING_WORDS):
        return False
    lm, sm = _materia(longer), _materia(shorter)
    return lm is None or sm is None or lm.id == sm.id or lm.chinese == longer


#: Words of quality or state that the materia table also strips before a name (鲜地黄,
#: 干地黄). They are dropped only where the table files both names under one entry: 鲜生地 is
#: 生地, but 干姜, which the table files apart, is not 姜.
_STATE_WORDS = ("鲜", "干", "真", "好", "上", "嫩", "新", "陈", "细", "净")


@lru_cache(maxsize=8192)
def _base(kb: Any, name: str) -> str:
    """``name`` without the processing word or the word of state in front of a known name, or
    the product suffix after one (熟地黄 → 地黄, 炙黄耆 → 黄耆, 鲜生地 → 生地, 甘草片 → 甘草), or
    ``name`` itself."""
    from ..sources.materia import PROCESSING_WORDS
    for word in sorted(PROCESSING_WORDS, key=len, reverse=True):
        rest = name[len(word):]
        if (name.startswith(word) and rest and _processed(name, rest)
                and (_materia(rest) is not None or _resolve(kb, rest) is not None)):
            return rest
    whole = _materia(name)
    for word in _STATE_WORDS:
        rest = name[len(word):]
        if (whole is not None and name.startswith(word) and rest
                and (part := _materia(rest)) is not None and part.id == whole.id):
            return rest
    for suffix in _PRODUCT_SUFFIXES:
        rest = name[:-len(suffix)]
        if (name.endswith(suffix) and rest and name not in _materia_names()
                and ((part := _materia(rest)) is not None or _resolve(kb, rest) is not None)
                and (whole is None or part is None or part.id == whole.id)):
            return rest
    return name


@lru_cache(maxsize=1)
def _materia_names() -> frozenset[str]:
    from ..sources.materia import MATERIA, _NAME_INDEX
    return frozenset({n for e in MATERIA.values() for n in (e.chinese, *e.aliases)}
                     | set(_NAME_INDEX))


@lru_cache(maxsize=8192)
def _forms(kb: Any, name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(``name`` and what it is a processed form of, those plus the drug's other processed
    forms that the tables name). 熟地黄 gives ((熟地黄, 地黄), (熟地黄, 地黄, 生地黄, …)):
    processing leaves the herb what it is, so 生地黄 and 熟地黄 are both 地黄."""
    from ..sources.materia import PROCESSING_WORDS
    forms = [name]
    while (b := _base(kb, forms[-1])) != forms[-1] and b not in forms:
        forms.append(b)
    wider = list(forms)
    for b in forms:
        for word in PROCESSING_WORDS:
            s = word + b
            if (s not in wider and _processed(s, b)
                    and (s in _materia_names() or _resolve(kb, s) is not None)):
                wider.append(s)
    return tuple(forms), tuple(wider)


@lru_cache(maxsize=16384)
def _known(kb: Any, name: str, extra: frozenset[str] = frozenset()) -> bool:
    """A name some table holds: the materia table (as written or with a processing word or
    product suffix stripped), the knowledge base, or the pack (its herbs and cited aliases)."""
    return (name in extra or name in _materia_names() or bool(_identity(kb, name))
            or _materia_id(name) is not None)


#: Names that an allergy record uses for something other than the drug the materia table
#: files them under. In a prescription 花粉 is 天花粉's short name; in an allergy history 花粉
#: (花粉过敏, 对花粉过敏) is pollen, the most common entry of all. Such an entry is checked
#: against the drug it may name only, with a block that asks which was meant, and not
#: carried to that drug's species-mates (瓜蒌, from the same Trichosanthes). A denied 花粉
#: (否认花粉过敏) is a denied pollen allergy and asks nothing.
_ALLERGY_HOMONYMS: Mapping[str, str] = {"花粉": "植物花粉"}

#: Everyday words that hold a name of the tables but are something else, each with what it
#: is. They are words of their own and name no herb: 胡萝卜 is the carrot (Daucus), not the
#: 萝卜 (Raphanus) the materia table files under 莱菔子; 桂花 is osmanthus.
_OTHER_WORDS: Mapping[str, str] = {
    "桂花": "木犀（Osmanthus fragrans）的花", "桂鱼": "鳜鱼", "鳜鱼": "鳜鱼",
    "胡萝卜": "胡萝卜（Daucus carota）", "桂圆": "龙眼（Dimocarpus longan）",
    "舌头": "舌", "眉头": "眉", "额头": "额", "鸡皮疙瘩": "鸡皮疙瘩", "归类": "分类"}


# ============================================================== normalising
#: Traditional, Hong Kong, Taiwan and Japanese forms of the characters of the names the
#: tables know and of the words a history writes around them, which the materia table's fold
#: (``materia.to_simplified``, cut to the characters of its own names) leaves: 過敏, 對, 否認,
#: 不詳, 體質, 變應原, 黑順片, 人銜, 當帰, 芍薬. One character for one, like that table. The
#: first part is the words of a history; the rest was checked against OpenCC's s2t, s2hk and
#: s2tw for every character of the scan's names (``test_every_traditional_form_is_folded``).
_WORD_T2S = str.maketrans(
    "過對認確歷應嚴異體質詳從後現癢腫難訴經類劑種與陰錄記變顯狀嘔臨診檢驗試蕁暈氣熱屬僅雖卻並還"
    "稱為係個這來時間週歲幾樣麼說話請際況們嗎報導誘數較極頻復敘"
    "順銜帰薬竜艸蔴実呉沢黒亀剤経続歯斉"
    "爲買親衝則剛別匱醫喫聽團塊處爾師態換據湯潔漿濃塗滿燉燒愛著終給櫱慮見觸計讓講許論證該説諸適長悶聞靜顆飲餅鱖",
    "过对认确历应严异体质详从后现痒肿难诉经类剂种与阴录记变显状呕临诊检验试荨晕气热属仅虽却并还"
    "称为系个这来时间周岁几样么说话请际况们吗报导诱数较极频复叙"
    "顺衔归药龙草麻实吴泽黑龟剂经续齿齐"
    "为买亲冲则刚别匮医吃听团块处尔师态换据汤洁浆浓涂满炖烧爱着终给蘖虑见触计让讲许论证该说诸适长闷闻静颗饮饼鳜")

#: Look-alike radicals from the CJK Radicals Supplement, which NFKC leaves (it folds only the
#: Kangxi radicals, ⼈ to 人): text copied from a PDF writes ⻩芪 for 黄芪 and ⻰胆 for 龙胆.
#: Each is read as the character it stands for (Unicode's EquivalentUnifiedIdeograph), in its
#: simplified form.
_RADICALS = str.maketrans(
    "⺁⺊⺌⺎⺒⺓⺜⺝⺠⺩⺬⺶⺼⺽⻁⻄⻅⻆⻉⻋⻑⻓⻔⻗⻘⻙⻚⻛⻜⻝⻢⻣⻤⻥⻦⻧⻨⻩⻪⻫⻬⻭⻮⻯⻰⻱⻲",
    "厂卜小兀巳幺日月民王示羊月臼虎西见角贝车长长门雨青韦页风飞食马骨鬼鱼鸟卤麦黄黾齐齐齿齿龙龙龟龟")

#: Characters that show nothing but can split a name, beyond the format characters
#: (category Cf) and controls: the combining grapheme joiner, the Hangul fillers, the
#: braille blank, variation selectors.
_BLANKS = frozenset("͏ᅟᅠ឴឵ㅤﾠ⠀")
_WS = re.compile(r"\s+")


def _invisible(c: str) -> bool:
    o = ord(c)
    if c in _BLANKS or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or 0x180B <= o <= 0x180F:
        return True
    cat = unicodedata.category(c)
    return cat in ("Cf", "Cs") or (cat == "Cc" and c not in _LINE_BREAKS)


#: Whitespace that ends a line (or a field: a tab), which ends a clause whatever stands on
#: either side of it: NKDA, then a line that says 黄芪过敏, is two statements.
_LINE_BREAKS = frozenset("\t\n\r\v\f\x85  ")


def _visible(text: str) -> str:
    """``text`` without the characters that show nothing but can split a name: format
    characters (zero-width space and joiners, the byte-order mark, soft hyphen, bidi
    marks), surrogates, controls other than tabs and line breaks (NUL), variation selectors,
    fillers and the braille blank."""
    if text.isascii() and text.isprintable():
        return text
    return "".join(c for c in text if not _invisible(c))


def _shown_text(record: Any) -> str:
    """The record as the scan sees it before folding: visible characters only, NFKC. Every
    character of ``fold`` is the folded form of the character at the same place here."""
    s = _visible(str(record or ""))
    return _visible(unicodedata.normalize("NFKC", s))


def _fold_char(c: str) -> str:
    if c.isascii():
        return c.lower()
    if "À" <= c <= "ɏ" or "Ḁ" <= c <= "ỿ":
        # a Latin letter with a tone mark or another diacritic: huáng is huang, lǜ is lu
        base = unicodedata.normalize("NFKD", c)[:1]
        if base.isascii():
            return base.lower()
    low = c.lower()
    return low if len(low) == 1 else c


def _fold_shown(s: str) -> str:
    from ..sources.materia import to_simplified
    s = to_simplified(s.translate(_RADICALS)).translate(_WORD_T2S)
    if s.isascii():
        return s.lower()
    return "".join(_fold_char(c) for c in s)


def fold(text: Any) -> str:
    """The record as the scan reads it: visible characters only, NFKC, look-alike radicals
    and traditional characters simplified, tone marks dropped, lower case. One character
    stays one character, so a position in the result is a position in the visible, NFKC
    text (``_shown_text``)."""
    return _fold_shown(_shown_text(text))


def _cjk(c: str) -> bool:
    o = ord(c)
    return (0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3134F
            or o == 0x3007)


def _all_cjk(name: str) -> bool:
    return bool(name) and all(_cjk(c) for c in name)


# ============================================================== record words
#: Words a history writes before a name. A name with one of these just before it stands as
#: a word: 对国老, 患者对国老, 自诉国老, 3年前北芪, 服用北芪, 因北芪, 一吃北芪, 过敏药品北芪 (colon
#: dropped), 及人参. Missing a word here never hides a name of two characters or more: the name
#: is then read as inside a longer word, which asks the practitioner (a block) instead of
#: stopping.
_BEFORE = frozenset("""
对 对于 有 有过 曾 曾经 曾有 既往 以往 以前 之前 从前 早年 幼时 儿时 年前 月前 周前 天前 日前 前 年 月 后
患者 病人 本人 其 自诉 自述 诉 述 主诉 称 自称 代诉 家属诉 据诉 诉称 述称
可疑 疑似 怀疑 疑 似 明确 已知 确诊 确认 证实 考虑 似乎 好像 可能 大概 也许 或许 估计 据说 听说 记得 不确定
服用 服 用 使用 口服 外用 外敷 接触 吃 喝 饮 食 食用 饮用 注射 静滴 煎服 内服 用药 吃了 用了 服了 喝了
吃过 用过 服过 喝过 服食 涂 涂抹 嚼 泡 煮 炖 熬 煎 配 含 含有 包括 包含 内含 加 加用 买 喂 遇 碰 闻
出现 发现 发生 引起 导致
过敏 过敏史 过敏原 过敏源 过敏物 过敏物质 过敏药物 过敏药品 过敏药 过敏食物 过敏反应 过敏情况
过敏名称 致敏原 致敏物 致敏药物 致敏药 变应原 变态反应 药敏 药敏史
药物 药品 食物 中药 中药材 草药 药材 饮片 中成药 成分 药名 名称 情况 记录
及 和 与 或 或者 以及 并 跟 同 也 均 都 亦 还 另 另外 此外 再 且 而且 并且 尤其 特别 比如 例如 诸如 像
但 但是 而 唯 只 仅 除 除了 可是 不过 然而
是 为 系 含 包括 例如 如 即 等 史 原 源 物 品 药 名 的 了 着 过 就 便 才 又 一 每 每次 一旦 只要 一次
因 因为 由于 由 被 把 将 给 让 向 从 自 到 在 这 那 此 该 某 凡 已 已经 刚 常 常常 经常 偶尔 多次 反复 首次
小时候 年轻时 童年 去年 今年 前年 最近 近期 近来 当时 那时 曾因 每当 时 时候 怕 害怕 未知 生熟
我 你 他 她 我们 他们 患儿 家属 母亲 父亲 家长 孩子 小孩 儿子 女儿 丈夫 妻子 爱人 医生 医师
表示 告知 提到 提及 反映 补充 说 讲
避免 避免使用 忌 忌用 忌服 禁 禁用 禁服 慎 慎用 勿 勿用 不能 不能用 不可 不可用 不宜 不宜用 停用 停服
改用 换用 应用 常备 备有 备用
严重 轻度 明显 多次 反复 不适 特殊
阳性 阴性
无 否认 没有 未 未见 未发现 从未 未曾 并无 不 非 排除 已排除 是否 有无
""".split())

#: Words a history writes after a name: 国老过敏, 北芪均过敏, 黄耆及人参, 国老等, 北芪后出现皮疹,
#: 北芪的成分, 北芪吃了, 北芪就过敏, 北芪水. A product word (丸, 片, 汤, 颗粒, 注射液, 油, 素, 糖 …)
#: is not one: 黄芪注射液 and 红花油 are read as longer words.
_AFTER = frozenset("""
过敏 过 敏 史 及 和 与 或 或者 以及 并 跟 同 等 均 都 也 亦 还 另 皆
后 之后 以后 时 期间 服后 用后 吃后 喝后 皮疹 瘙痒 痒 红肿 肿 荨麻疹 风团 起疹 起疹子 出疹 疹子 红疹 皮炎
反应 不良反应 副作用 严重 引起 引发 致 导致 所致 诱发 出现 发生 有 类 制剂 及其 其 阳性 阴性 皮试
曾 年 会 可 能 者 已 中毒 不耐受 不耐 不适 腹泻 腹痛 呕吐 恶心 头晕 头痛 心慌 心悸 胸闷 气短 气喘 哮喘 喘
休克 水肿 呼吸困难 上火 发热 发烧 发痒 难受 住院 就诊 入院 入口 即吐 立即 马上 耐受
无 否 否认 不 未 没 没有 除外 例外 以外 外 药 药物 成分 相关 为主 为 是
的 了 着 就 便 即 则 才 又 再 而 且 但 却 要 被 让 使 所 吃 喝 服 用 食 一 这 那 此 每 样 种 味 之
吃了 用了 服了 喝了 服用 服用后 使用 使用后 食用 饮用 外用 口服 禁用 忌用 慎用 禁服 忌服 停用
一类 这类 之类 等药 等中药 等药物 这味药 这味 水 茶 泡水 煮水 煎水 炖鸡 煲汤 熬汤 泡茶 接触 接触性
汤剂 煎剂 煎液 饮片 药汁
高度 极度 轻度 中度 重度 明显 非常 很 特别 比较 较 有些 一直 曾经 经常 偶尔 容易 易
""".split())

#: Product words, which make a longer word of the name before them even where a word of
#: ``_AFTER`` starts them (口服 of 口服液): 黄芪口服液, 人参注射液.
_PRODUCT_WORDS = ("口服液", "注射液", "注射剂", "注射", "针剂", "颗粒", "胶囊", "糖浆", "合剂", "冲剂",
                  "滴丸", "片剂", "丸剂", "散剂", "膏剂", "酊剂", "外用药", "外用膏")

_BEFORE_MAX = max(map(len, _BEFORE))
_AFTER_MAX = max(map(len, _AFTER))

#: The words around a name of one character (姜, 枣, 归, 面): only the words of an allergy
#: record itself, of a list, of taking it and of denying it. A name of one character is a word
#: far more often than a name (面部, 方便面, 姜某, 归档), so it counts only between these or
#: between boundaries: 对姜过敏, 姜过敏史, 、姜、, 服用姜后皮疹, 否认姜过敏, 对姜严重过敏, 吃姜会过敏,
#: 吃姜出现皮疹, 姜致皮疹, 2019年姜过敏, 对中药姜过敏. Every pair of a word here and a name of one
#: character the tables give a pack herb (姜, 归, 枣, 桂, 芎) was checked for an everyday word
#: (``test_no_one_character_reading_is_an_everyday_word``); the one found, 归类, is a word of
#: its own (``_OTHER_WORDS``).
_BEFORE_ONE = frozenset("""
对 曾对 有 有过 曾 曾经 既往 年前 月前 前 患者 自诉 自述 诉 述 怀疑 疑似 可疑 服用 服 吃 喝 食用 饮用 用
使用 吃了 喝了 用了 服了 吃过 喝过 用过 服过 一吃 接触 过敏 过敏史 过敏原 过敏源 过敏物 过敏药物 过敏药品
过敏食物 致敏原 变应原 药物 食物 及 和 与 或 或者 以及 并 等 也 均 否认 无 没有 未 对其 因 因为
年 中药 中药材 草药 药材 含 含有
""".split())
_AFTER_ONE = frozenset("""
过敏 过敏史 过敏反应 后 等 及 和 与 或 或者 以及 也 均 都 皮疹 瘙痒 红肿 不耐受 不适 类 制品 中毒
皮炎 红疹 荨麻疹 风团 水肿 腹泻 呕吐 吃了 用了 服了 喝了 无 否 未 没有 阴性 阳性 皮试 就 有过敏
出现 发生 引起 引发 导致 所致 诱发 致 会 严重 特别 非常 轻度 中度 重度 明显 药物 煎剂 煎液 汤剂
""".split())
_BEFORE_ONE_MAX = max(map(len, _BEFORE_ONE))
_AFTER_ONE_MAX = max(map(len, _AFTER_ONE))

#: Everyday words whose last character starts a name: in 对于术后 the 于 is 对于's, not the
#: first character of 于术 (白术), and in 鸡蛋白及 the 白 is 蛋白's (protein), not 白及's. A
#: name after one of them is read as inside a longer word, unless the everyday word's first
#: character stands as a word of a history on its own and a word of allergy (or the end of
#: the entry) follows the name: 对于术过敏 is 对 + 于术 + 过敏, 对于术后 is 对于 + 术后.
_EVERYDAY = frozenset("""
对于 关于 由于 至于 属于 位于 等于 用于 大于 小于 基于 在于 便于 易于 过于 终于 处于
蛋白 空白 苍白 明白 坦白 表白 洁白 雪白 黑白 灰白 清白
""".split())
_STRONG_AFTER = ("过敏", "不耐受", "皮疹", "瘙痒", "中毒")

#: Words that may open a clause before its denial: 患者否认, 过敏史无, 既往无, 已排除.
_CLAUSE_LEADS = tuple(sorted("""
患者 病人 本人 其 既往 平素 自诉 自述 诉 述 家属诉 过敏史 药物过敏史 食物过敏史 过敏 药物 食物 对 已 经 现
""".split(), key=len, reverse=True))
#: Denials. An ASCII one is a whole word (no, not nothing).
_NEGATIONS = tuple(sorted("""
否认 没有 没 未曾 未见 未发现 未诉 未述 未 从未 并无 无明确 无 非 排除 可用 可以用 能用 可服
no not nkda nka nkfa denies denied deny negative without none nil tolerates tolerated tolerating
""".split(), key=len, reverse=True))
#: Words that start with a denial but deny nothing: 非常 (very), 未成年 (under age), 无意中
#: (accidentally), 没想到 (unexpectedly), 未经 (without), 无数 (countless); no doubt, not
#: only, none other (than).
_NOT_DENIALS = tuple("""
非常 非要 非得 非凡 非典 未成年 未满 未经 未来 未料 未想到 未曾想 无意 无数 无论 无比 无法 无奈 无故 无端
无心 没想到 没料到 没多久 没注意 没留意 没过多久 未及 非处方 未知
nodoubt notonly noneother notsure
""".split())
#: Words after a denial that close it before a name: 无过敏史 (a denial of its own),
#: 无药物过敏史但, NKDA except, no other allergy than 黄芪.
_CLOSERS = re.compile(r"过敏|史|但|除|不过|然而|却|唯|只|仅|可是|出现|发生|诱因"
                      r"|except|but|however|apart|besides|than|only")
#: What may stand between a name (or the list it ends) and a denial written after it:
#: 黄芪过敏史：否, 黄芪皮试阴性, 黄芪过敏？否, 黄芪过敏-否, 黄芪（无）.
_AFTER_LABEL = re.compile(r"(?:过敏(?:史|反应|试验)?|皮试(?:结果)?|试验|反应)?")
_AFTER_SEP = re.compile(r"[:：∶?？=]*[\-－—–~]*[(\[【]?")
#: A denial written after the name that may run on: 黄芪无过敏史, 对黄芪无过敏反应, 曾服用黄芪未见
#: 过敏, 服黄芪无不适, 长期服用黄芪无不良反应, 黄芪耐受, 黄芪可用, 黄芪过敏已排除, 人参可以吃, 人参没问题.
_DENIAL_PHRASE = re.compile(
    r"(?:(?:已|曾|均)?(?:服用过|使用过|用过|服过|吃过|喝过))?(?:"
    r"(?:均|都|也|亦)?(?:无|未见|没有|没|未发现|未出现|未发生|未有|并无|从无|从未|未诉|未述|未)"
    r"(?:明显|任何|特殊|相关|什么|啥)?"
    r"(?:过敏|不良反应|不适|异常|反应|副作用|皮疹|问题|不舒服)"
    r"|不过敏|不会过敏|未过敏|没过敏|没有过敏|无过敏|耐受|可耐受|可以用|可用|可以服|可服|能用"
    r"|可以吃|能吃|可吃|可以喝|能喝|能服|一切正常|一切如常|无事|没事|无碍|无妨|已排除|已除外|排除|否认)"
    r"|noreaction|noallerg|notallergic|nosideeffect|noproblem|noissue|tolerat")
#: What may stand between a name (or the list it ends) and a denial that says the herb was
#: used with no reaction: 服用黄芪后无不适, 黄芪服用后未见过敏, 服用黄芪期间无不良反应, 人参吃了没事,
#: 当归用过，无过敏, 服黄芪3天后无不适, 喝黄芪水后没有不适. Its use word or its 后 / 期间 says the herb
#: was taken, so what follows says it was tolerated.
_USE_SEGMENT = re.compile(
    r"(?:泡水|水|茶|汤|粥)?"
    r"(?:(?:已|曾|均|都|也|多次|长期|一直|经常|平时)?(?:服用|使用|食用|饮用|口服|服|用|吃|喝)(?:过|了|完)?)?"
    r"(?:\d+(?:个)?多?(?:天|日|周|月|年))?(?:之后|以后|后|期间|时|中|过程中)?")
#: A word after a separator that says a herb can be used: 人参：可, 黄芪（可）, ginseng: ok.
_TOLERATE_BARE = re.compile(r"可以|可|能|ok|okay|fine")
#: Of those, the ones that deny an allergy; the rest say the herb is tolerated (耐受, 可用, 无不适,
#: 无不良反应, tolerates).
_DENIES_ALLERGY = re.compile(r"过敏|allerg|否认|排除|除外")
#: Denials that open a clause and say the herb can be used: 可用黄芪, tolerates 黄芪.
_TOLERATES = frozenset({"可用", "可以用", "能用", "可服", "tolerates", "tolerated", "tolerating"})
#: A denial written after the name that must end there: 黄芪：无, 黄芪（否）, 黄芪皮试阴性.
_DENIAL_BARE = re.compile(r"阴性|没有|无|否|negative|neg|none|nil|nkda|nka|no")
#: "-" is a denial only in brackets or after a colon: 黄芪（-）, 黄芪：-. Not 黄芪 - or 黄芪——皮疹.
_DENIAL_DASH = re.compile(r"(?:过敏(?:史)?|皮试)?(?:[:：∶][\-－−]|[(\[【][\-－−][)\]】])")
#: A clause that takes back the one before it: 怀疑黄芪过敏，已排除; 曾疑人参过敏，后证实非过敏.
_RETRACTION = re.compile(
    r"(?:但|后|经|现|已|均|皆|检查|复查|皮试|证实|确认|诊断|随后|后来)*"
    r"(?:排除|已除外|否认|否定|非过敏|不是过敏|并非过敏|未证实|不支持|阴性)(?:过敏)?")
#: Words a denial's clause names after it (无药物过敏史, 没食物过敏): a name that starts with
#: the denial's character and runs into one of these (没药 of 没药物过敏史, 无食 of 无食物过敏)
#: is the denial, not the name.
_DENIED_OBJECTS = ("药物", "药品", "食物", "过敏", "其他", "其它", "特殊", "明显",
                   "已知", "任何", "发现")
#: Marks that do not end a clause: a list goes on after 、 and /, and 否认青霉素、黄芪过敏
#: denies 黄芪 too; an apostrophe is inside a word (can't, Huang'qi).
_CLAUSE_MARK = re.compile(r"[^\w、/·\-'’]|_")
_OPEN_BRACKETS = "(（[【〔"
#: What a list may end in before a denial written after it: 黄芪、党参等未见过敏.
_LIST_TAIL = re.compile(r"(?:等(?:中药|药物|药)?|之类|一类|类|均|都|皆|也|全部|等等)*")
_CLOSE_BRACKETS = ")）]】〕"

#: Words of allergy, of a reaction or symptom, and of a herb to be avoided. A clause that holds
#: one, or a clause that goes on from it, records an allergy, not a herb taken: 服用北芪后出现
#: 皮疹, 以前喝过黄芪，出现皮疹, 服用黄芪后，皮肤瘙痒, 黄芪禁用. Such a word inside a name the
#: scan found is that name's (泻 of 泽泻, 肿 of 肿节风), not a reaction.
_ALLERGY_CJK = re.compile(
    r"过敏|敏感|致敏|变态反应|变应|疹|痒|肿|风团|红斑|斑块|水疱|水泡|疙瘩|起包|鼓包|红点|反应|副作用|不良"
    r"|不耐|休克|晕|昏厥|喘|憋|胸闷|气促|气急|气短|气紧|呼吸困难|呼吸急促|窒息|喉头|心慌|心悸|心跳"
    r"|心动过速|呕|吐|恶心|泻|拉肚子|腹痛|胃痛|胃疼|头痛|头疼|中毒|不适|难受|不舒服|发红|潮红|充血"
    r"|发麻|麻木|刺痛|灼热|烧灼|皮炎|脱皮|咳|喷嚏|流涕|阳性|禁|忌|慎用|停用|勿用|避免|不能用|不能吃"
    r"|不能服|不能喝|不可服|吃不了|用不了|不敢吃|不敢用|不宜|上火|发热|发烧|出血|鼻血")
_ALLERGY_ASCII = ("allerg", "react", "rash", "hive", "itch", "swell", "anaphyla", "intoleran",
                  "sensitiv", "adverse", "urticari", "wheez", "angioedem", "adr", "contraindicat",
                  "avoid", "hypersensitiv", "breath", "nause", "vomit", "diarrh", "dizz",
                  "palpitat", "flush", "blister", "wheal", "welt", "tingl", "numbn", "faint",
                  "syncop", "headache", "upset", "sick", "unwell", "shock", "edema", "oedema")
#: English words that say a herb cannot be taken (can't take huang qi, unable to take, does not
#: tolerate): they record an intolerance, which is read like an allergy.
_CANNOT_ASCII = frozenset("cannot cant unable".split())
_CANNOT_NT = frozenset("can couldn shouldn mustn won".split())
_NOT_TOLERATE = frozenset("not never poorly doesn didn don".split())
#: Words that say a herb is taken, eaten or used: 目前在吃人参, 现用药：人参, 喜食生姜, 平素服黄芪
#: 泡水, 每周炖汤加党参, 每日泡黄芪水喝. 食物, 食品, 饮食, 饮片 and 衣服 are not; 用 after 禁, 忌, 慎,
#: 停, 作 or 药 is not, nor 泡 of 水泡 or 起泡.
_USE_CJK = re.compile(
    r"吃|喝|(?<![衣舒说克制])服(?![务装])|饮(?![食片])|食(?![物品])|泡水|泡茶|(?<![水起气血])泡(?![沫澡脚疹])"
    r"|炖|煲|煮|熬|嚼|含服|涂|擦|敷|(?<![禁忌慎停勿作药不副])用(?![量法])|常备|进补|药膳|代茶")
#: Words after a name that say it is taken all along: 人参每日一支, 黄芪泡水, 党参炖汤, 枸杞常吃.
_USE_AFTER = re.compile(r"每日|每天|每周|每晚|长期|常年|泡水|泡茶|煲汤|炖汤|代茶|在服|在吃|在用|服用中|常吃|常喝|常服"
                        r"|调理|保健|进补")
_USE_ASCII = frozenset("""
take takes taking took taken on using uses use used drink drinks drinking drank eat eats eating
ate enjoys enjoy consume consumes consuming supplement supplements supplementing currently
""".split())
#: Words that open a clause going on from the one before it, to tell what happened: 以前喝过
#: 黄芪，出现皮疹; 服用黄芪，3小时后出现皮疹; 吃人参后，嘴唇肿了; 服用黄芪后，自觉胸闷. A time, a
#: part of the body or a word of feeling opens one; a clause that names something else of its own
#: (目前在吃人参，青霉素过敏) does not.
_CONTINUE_CJK = tuple(sorted("""
出现 发生 发作 引起 引发 导致 诱发 随即 立即 立刻 马上 然后 之后 以后 后来 继而 接着 随后 稍后 不久 很快 片刻
即 就 便 起 后 致 当即 当天 当晚 当日 次日 翌日 第二天 隔天 隔日 约 大约 自觉 感觉 感到 觉得 伴 伴有 开始
逐渐 渐 突然 突发 皮疹 过敏 瘙痒 红肿 全身 周身 浑身 皮肤 面部 颜面 面颊 面色 脸 脸上 脸部 嘴唇 口唇 口周
口腔 口角 嘴 舌头 舌体 眼睛 眼睑 眼周 双眼 眼部 身上 躯干 四肢 上肢 下肢 双手 手部 手臂 手背 手掌 双腿 腿部
胸部 胸口 胸前 背部 腹部 胃部 喉咙 喉头 咽喉 咽部 头部 头面 头皮 颈部 脖子 耳朵 鼻子 局部
""".split(), key=len, reverse=True))
_CONTINUE_TIME = re.compile(r"(?:\d+|[一二两三四五六七八九十半几数]+)个?多?(?:分钟|分|小时|钟头|天|日|周|星期|月)"
                            r"(?:之后|以后|后|内|左右|许)")
_TIME_UNITS = frozenset("min mins minute minutes hr hrs hour hours day days week weeks".split())
_CONTINUE_ASCII = frozenset("""
then developed develops causing caused resulting followed with and got get gets getting had broke
breaks experienced experiences noticed felt feels suffered presented resulted leading led after
within later soon immediately next shortly subsequently which
""".split())
#: Words after a name in its clause that name it as a cause: 与服用黄芪有关, 服用黄芪所致, 服黄芪引起.
#: A herb named as the cause of something is no herb merely taken.
_CAUSE_CJK = re.compile(r"所致|有关|相关|引起|引发|导致|诱发|造成|致使|而致|所引")
#: A cause written right before the herb: 全身皮疹，因服用黄芪; 由于吃人参.
_CAUSE_BEFORE = re.compile(r"(?:因为|因|由于)(?:服用|服|吃了|吃|喝了|喝|使用|用了|用|食用)?$")
_CAUSE_ASCII = frozenset("caused due induced related attributed secondary triggered".split())
#: Words a temporal clause ends with after a name: 服用黄芪后, 服用黄芪期间, 吃人参之后. What
#: follows it in its sentence tells what happened after the herb was taken.
_TEMPORAL = re.compile(r"(?:服用|使用|食用|饮用|服|用|吃|喝)?(?:过|了|完)?"
                       r"(?:\d+(?:个)?多?(?:分钟|小时|天|日|周|月))?(?:之后|以后|后|期间|时|过程中)")
#: Denials that are a statement of their own and take no object: NKDA, NKA, nil, none. A name
#: after one with a word of allergy after it is another statement: NKDA 黄芪过敏.
_COMPLETE_DENIALS = frozenset("nkda nka nkfa nil none".split())
#: Words after allergies that make it the object of a denial still open (no allergy to …).
_ALLERGY_OF = frozenset("""
to against of for with from toward towards reaction reactions history hx status or nor
""".split())
#: Marks that end a sentence; a line break or a tab does too.
_SENTENCE_MARK = re.compile(r"[。！？；;!?]|(?<!\d)\.|\.(?!\d)")
#: Labels after which a name is a person's (记录人：姜, 姓名：黄精): in a history a label that
#: introduces a person names nobody's allergy.
_PERSON_CJK = re.compile(
    r"(?:记录人|记录者|记录员|签名|签字|医生|医师|护士|询问人|询问者|填写人|填表人|录入人|录入者"
    r"|审核人|姓名|联系人|翻译|陪诊|陪同|采集人|采集者|操作者)[:：∶]?$")
#: Titles before a Latin-lettered name, or after it, that make it a person's: Dr. Huang Jing,
#: interpreter Hong Hua, son Yuan Zhi, patient name: Huang Qi, Du Zhong, RN. A name label
#: that says whose name it is makes it a person's; one that names a drug (Allergen name:,
#: Drug name:, Substance name:) does not, and a bare one (Name:) leaves it unconfirmed.
_PERSON_BEFORE = re.compile(
    r"(?:\b(?:dr|doctor|mr|mrs|ms|miss|prof|professor|nurse|pharmacist|interpreter|translator"
    r"|son|daughter|wife|husband|mother|father|brother|sister|per"
    r"|(?:patient|patient's|pt|full|first|last|family|given|maiden|person's|staff|user|caller"
    r"|signer|doctor's|nurse's|recorder|clinician|provider|physician|informant|contact)\s+name"
    r"|(?:reviewed|recorded|confirmed|signed|seen|completed|entered|written|taken|checked|obtained)"
    r"\s+by|history\s+from)\b\.?\s*[:：,，\-–]?\s*)$")
_PERSON_AFTER = re.compile(r"^\s*[,，]?\s*\b(?:rn|md|rph|np|pa|lpn|mbbs|phd|pharmd)\b")
#: A bare name label right before a Latin-lettered name: Name: huang qi.
_NAME_LABEL = re.compile(r"(?:^|[^a-z'’])name\s*[:：\-–]?\s*$")
_DRUG_NAME_LABEL = re.compile(
    r"\b(?:allergen|allergy|allergic|drug|substance|medication|medicine|med|herb|herbal|product"
    r"|agent|item|ingredient|food|trade|brand|generic|chemical|causative|offending)\s+name"
    r"\s*[:：\-–]?\s*$")
#: Words of an English allergy line that are not names: Allergy: huang qi and ren shen;
#: Substance name: ren shen.
_ASCII_LABELS = frozenset("""
to and or hx history list known drug drugs med meds medication medications herb herbs herbal
x the a an of name names substance substances agent agents product products item items
ingredient ingredients food foods
""".split())


# ============================================================== the scan vocabulary
@dataclass(frozen=True)
class _Ascii:
    kind: str                     # "name": a Chinese name; "species" or "genus"
    target: tuple[str, ...]       # the Chinese name, or the species / the genus
    how: str
    source: str = ""


@dataclass(frozen=True)
class _Formula:
    name: str                     # the formula as its table writes it
    herbs: tuple[str, ...]
    source: str                   # 知识包 or 种子知识库
    how: str = ""                 # "" when written as its table writes it


@dataclass(frozen=True)
class _Vocab:
    trie: dict                    # every Chinese name, formula and everyday word, with its kind
    longest: int
    names: frozenset[str]
    ascii: Mapping[str, tuple[_Ascii, ...]]
    ascii_max: int
    formulas: Mapping[str, _Formula]
    known: frozenset[str]         # the pack's herbs and cited aliases


_KIND_RANK = {"formula": 3, "name": 2, "other": 1}


def _trie(items: Iterable[tuple[str, str]]) -> dict:
    root: dict = {}
    for n, kind in items:
        node = root
        for c in n:
            node = node.setdefault(c, {})
        old = node.get("")             # no character is "", so this marks a whole name
        if old is None or _KIND_RANK[kind] > _KIND_RANK[old]:
            node[""] = kind
    return root


def _ascii_key(name: str) -> tuple[str, int]:
    s = unicodedata.normalize("NFKD", name.lower())
    words = re.findall(r"[a-z0-9]+", "".join(c for c in s if not unicodedata.combining(c)))
    return "".join(words), len(words)


#: The words of a Latin drug name that name the part used. The Pharmacopoeia 2020 writes the
#: plant first (Astragali Radix); its earlier editions, the WHO monographs and most English
#: texts write the part first (Radix Astragali).
_LATIN_PARTS = frozenset("""
radix rhizoma herba folium flos fructus semen cortex caulis ramulus pericarpium bulbus concha
cornu medulla lignum resina spica periostracum pollen spora arillus exocarpium nidus squama
carapax plastrum os venenum colla faeces cacumen pedicellus nux oötheca ootheca endoconcha
calculus corium squama gigerii
""".split())


def _latin_forms(latin: str) -> list[str]:
    """``latin`` and its older word order: Astragali Radix → Radix Astragali; Ginseng Radix et
    Rhizoma → Radix et Rhizoma Ginseng and Radix Ginseng; Zingiberis Rhizoma Recens →
    Rhizoma Zingiberis Recens."""
    words = latin.split()
    out = [latin]
    first = next((i for i, w in enumerate(words) if w.lower() in _LATIN_PARTS), None)
    if not first:                       # no part word, or the part already first
        return out
    taxon = words[:first]
    run, i = [words[first]], first + 1
    while i + 1 < len(words) and words[i].lower() in ("et", "cum"):
        run += words[i:i + 2]
        i += 2
    rest = words[i:]
    out.append(" ".join(run + taxon + rest))
    if len(run) > 1:
        out.append(" ".join(run[:1] + taxon + rest))
        out.append(" ".join(taxon + run[:1] + rest))
    return out


def _order(how: str, latin: str, form: str) -> str:
    return how if form == latin else f"{how}{latin}的另一词序"


#: Genus names that are also everyday English words, which are not read as genera.
_NOT_GENERA = frozenset({"glycine", "iris", "passer", "apis", "aster", "piper", "calamus",
                         "rosa", "salix", "sepia", "senna", "cassia", "bufo", "manis"})

_VOCABS: dict[tuple[int, str], _Vocab] = {}

#: The product forms one formula is sold or written in: 补中益气汤 is also 补中益气丸, 天麻钩藤饮
#: also 天麻钩藤颗粒. For a formula of the pack, 散 and 饮 too (四物散, 天麻钩藤汤); not for the
#: knowledge base's, where 四逆散 is another formula than 四逆汤.
_FORMULA_ENDINGS = ("注射液", "口服液", "软胶囊", "胶囊", "颗粒", "合剂", "冲剂", "糖浆", "滴丸",
                    "汤", "散", "丸", "饮", "方", "膏", "丹", "片")
_PRODUCT_FORMS = ("丸", "汤", "颗粒", "胶囊", "软胶囊", "片", "口服液", "合剂", "冲剂", "糖浆",
                  "滴丸", "浓缩丸", "水丸", "蜜丸", "大蜜丸", "小蜜丸", "膏", "方")
_PACK_PRODUCT_FORMS = ("散", "饮")
#: A form that, after a single herb's name, is that herb (麻黄片 is sliced 麻黄), so a formula
#: named after one herb is not read in it. 桂枝颗粒 is read as 桂枝汤 in granules (a product of
#: that name exists), which asks about 桂枝 and the rest of 桂枝汤 alike.
_HERB_PRODUCT_FORMS = ("片",)


def _formulas(kb: Any, pack: Any, cjk: set[str]) -> dict[str, _Formula]:
    """Every formula the pack and the knowledge base hold, under its own name, its aliases
    and its name in the other product forms, with its herbs."""
    exact: dict[str, _Formula] = {}
    of_pack: set[str] = set()
    for name, f in (getattr(pack, "formulas", None) or {}).items():
        exact[fold(name)] = _Formula(name, tuple(h for h, _ in f.herbs), "知识包")
        of_pack.add(fold(name))
    for f in (getattr(kb, "formulas", None) or {}).values():
        herbs = []
        for ing in getattr(f, "ingredients", ()):
            proc = getattr(kb, "processed", {}).get(ing.processed) if ing.processed else None
            herb = getattr(kb, "herbs", {}).get(ing.herb_id)
            name = getattr(proc or herb, "chinese", "")
            if name:
                herbs.append(name)
        if not herbs:
            continue
        main = fold(f.chinese)
        if main not in exact:
            exact[main] = _Formula(f.chinese, tuple(herbs), "种子知识库")
        for alias in getattr(f, "aliases", ()):
            a = fold(alias)
            if a not in exact:
                exact[a] = _Formula(exact[main].name, exact[main].herbs, exact[main].source,
                                    f"{alias}为{exact[main].name}的别名（种子知识库）")
    out = dict(exact)
    for written, f in exact.items():
        ending = next((e for e in _FORMULA_ENDINGS if written.endswith(e)), "")
        stem = written[:-len(ending)] if ending else ""
        if len(stem) < 2:
            continue
        forms = _PRODUCT_FORMS + (_PACK_PRODUCT_FORMS if written in of_pack else ())
        for form in forms:
            if stem in cjk and form in _HERB_PRODUCT_FORMS:
                continue
            v = stem + form
            if v not in out and v not in cjk:
                out[v] = _Formula(f.name, f.herbs, f.source,
                                  f"与{f.name}同名而剂型不同，组成未必完全相同")
    return out


def _vocab(kb: Any, pack: Any = None) -> _Vocab:
    """Every name the tables know, folded: the seed knowledge base's herbs and processed
    forms (Chinese, pinyin, Latin, species), the materia table's names, Latin names and
    species, the pack's herbs, cited aliases, pinyin, Latin and English common names, and
    the formulas of the pack and the knowledge base."""
    key = (id(kb), getattr(pack, "digest", "") if pack is not None else "")
    if key in _VOCABS:
        return _VOCABS[key]
    from ..sources.materia import MATERIA
    cjk: set[str] = set()
    ascii_names: dict[str, set[_Ascii]] = {}
    longest = 1

    def add_ascii(name: str, entry: _Ascii) -> None:
        nonlocal longest
        joined, words = _ascii_key(fold(name))
        if len(joined) >= 4:
            ascii_names.setdefault(joined, set()).add(entry)
            longest = max(longest, words)

    def add_species(names: Iterable[str], how: str, source: str) -> None:
        for sp in names:
            add_ascii(sp, _Ascii("species", (sp,), how, source))
            genus = sp.split()[0] if " " in sp else ""
            if len(genus) >= 4 and genus.lower() not in _NOT_GENERA:
                add_ascii(genus, _Ascii("genus", (genus,), "属名", source))

    for entity in (*getattr(kb, "herbs", {}).values(), *getattr(kb, "processed", {}).values()):
        herb = kb.herbs.get(getattr(entity, "herb_id", "") or entity.id) \
            if hasattr(kb, "herbs") else None
        chinese = fold(getattr(herb or entity, "chinese", "") or "")
        for n in getattr(entity, "names", ()):
            f = fold(n)
            if _all_cjk(normalise(f)):
                cjk.add(normalise(f))
            elif f.isascii() and chinese:
                how = "种子知识库所载的拼音名或拉丁名"
                for form in _latin_forms(n) if n[:1].isupper() else [n]:
                    add_ascii(form, _Ascii("name", (chinese,), _order(how, n, form)))
        if entity is herb and getattr(entity, "species", ()):
            add_species(entity.species, "种子知识库所载的物种名", "种子知识库")
    for entry in MATERIA.values():
        for n in (entry.chinese, *entry.aliases):
            f = normalise(fold(n))
            if _all_cjk(f):
                cjk.add(f)
        for form in _latin_forms(entry.latin):
            add_ascii(form, _Ascii("name", (fold(entry.chinese),),
                                   _order("药材名表所载的拉丁名", entry.latin, form)))
        add_species(entry.species_names, "药材名表所载的物种名", "药材名表（materia）")
    # every name the materia table lists, also one written with a character outside the
    # CJK blocks (芎 followed by a private-use character for a rare one)
    cjk.update(f for n in _materia_names() if (f := normalise(fold(n))) and not f.isascii())
    known: set[str] = set()
    if pack is not None:
        cjk.update(f for h in pack.herbs if _all_cjk(f := normalise(fold(h))))
        known.update(normalise(fold(h)) for h in pack.herbs)
        for a in getattr(pack, "herb_aliases", {}) or {}:
            if _all_cjk(f := normalise(fold(a))):
                cjk.add(f)
                known.add(f)
        pinyin = getattr(pack, "herb_pinyin", {}) or {}
        source = getattr(pack, "herb_pinyin_source", "") or "知识包"
        for herb, names in pinyin.items():
            for p in names:
                add_ascii(p, _Ascii("name", (fold(herb),), "知识包所载的拼音", source))
        for herb, latin in (getattr(pack, "herb_latin", {}) or {}).items():
            for form in _latin_forms(latin):
                add_ascii(form, _Ascii("name", (fold(herb),),
                                       _order("知识包所载的拉丁名", latin, form)))
        for herb, (species, src) in (getattr(pack, "herb_sources", {}) or {}).items():
            add_species(species, "知识包所载的物种名", src)
        for names, species, src in getattr(pack, "herb_common_names", ()) or ():
            for n in names:
                add_ascii(n, _Ascii("species", tuple(species),
                                    f"英文俗名，通常指{'、'.join(species)}（{src}）", src))

    def one_each(entries: set[_Ascii]) -> tuple[_Ascii, ...]:
        # a Latin name two tables give one herb, or a species two tables hold, is one name
        first: dict[tuple[str, tuple[str, ...]], _Ascii] = {}
        for a in sorted(entries, key=lambda a: (a.kind, a.target, a.how, a.source)):
            first.setdefault((a.kind, a.target), a)
        return tuple(first.values())

    formulas = _formulas(kb, pack, cjk)
    items = [*((n, "name") for n in cjk), *((f, "formula") for f in formulas),
             *((w, "other") for w in _OTHER_WORDS)]
    vocab = _Vocab(_trie(items), max(len(n) for n, _ in items), frozenset(cjk),
                   {k: one_each(v) for k, v in ascii_names.items()}, longest,
                   formulas, frozenset(known))
    if len(_VOCABS) > 8:
        _VOCABS.clear()
    _VOCABS[key] = vocab
    return vocab


# ============================================================== the scan
@dataclass(frozen=True)
class Mention:
    """A name found in a record, read in its place.

    ``name`` is the name as the tables write it (the Chinese name a Latin or pinyin name is
    of, the formula a product name is of, the species a species name or common name is of);
    ``written`` the record's own text when it differs (huangqi, astragali radix, 补中益气丸).
    ``word`` is the longer word the name stands inside ("" when it stands): 郁金香 for 郁金.
    ``denied`` says the record denies an allergy to it, ``taken`` that the record says it is
    taken or eaten with no reaction. ``kind`` is "name", "formula" (``of`` is the formula),
    "species" or "genus" (``species`` holds them). ``context`` is "unconfirmed" for a Latin
    or pinyin name in an English sentence that says nothing of an allergy, and "name_label"
    for one right after a bare name label (Name: huang qi), which may introduce a person.
    """

    name: str
    start: int
    end: int
    written: str = ""
    how: str = ""
    word: str = ""
    denied: bool = False
    kind: str = "name"
    of: str = ""
    species: tuple[str, ...] = ()
    taken: bool = False
    context: str = ""


def _compact(text: str) -> tuple[str, frozenset[int], tuple[int, ...], frozenset[int]]:
    """``text`` without whitespace, the positions in the result that had some before, for
    each character of the result its position in ``text``, and the positions whose
    whitespace before held a line break or a tab."""
    keep = [i for i, c in enumerate(text) if not c.isspace()]
    gaps = frozenset(k for k in range(1, len(keep)) if keep[k] != keep[k - 1] + 1)
    breaks = frozenset(k for k in gaps
                       if any(c in _LINE_BREAKS for c in text[keep[k - 1] + 1:keep[k]]))
    return "".join(text[i] for i in keep), gaps, tuple(keep), breaks


def _tokens(t: str, gaps: frozenset[int]) -> list[tuple[int, int]]:
    toks: list[tuple[int, int]] = []
    i, n = 0, len(t)
    while i < n:
        if t[i].isascii() and t[i].isalnum():
            j = i + 1
            while j < n and t[j].isascii() and t[j].isalnum() and j not in gaps:
                j += 1
            toks.append((i, j))
            i = j
        else:
            i += 1
    return toks


#: What may join the words of one Latin or pinyin name: nothing, a hyphen or dash, a full
#: stop, an apostrophe (Huang'qi).
_ASCII_JOINERS = frozenset(("", "-", ".", "'", "’", "‘", "–", "—", "‐", "‑", "·"))
_TONE = re.compile(r"(?<=[a-z])[1-5]$")


def _ascii_spans(t: str, toks: Sequence[tuple[int, int]], vocab: _Vocab
                 ) -> list[tuple[int, int, _Ascii]]:
    """(start, end, what) of each Latin, pinyin, species or English name, as whole words:
    astragali radix, radix astragali, astragaliradix, huangqi, huang qi, huang2 qi2, Huáng
    Qí, panax ginseng, ginger."""
    if not vocab.ascii:
        return []
    words = [_TONE.sub("", t[s:e]) for s, e in toks]
    out: list[tuple[int, int, _Ascii]] = []
    a = 0
    while a < len(toks):
        hit = None
        for k in range(min(vocab.ascii_max, len(toks) - a), 0, -1):
            run = toks[a:a + k]
            if any(t[q[1]:r[0]] not in _ASCII_JOINERS for q, r in zip(run, run[1:])):
                continue
            joined = "".join(words[a:a + k])
            if joined in vocab.ascii:
                hit = (k, run[0][0], run[-1][1], vocab.ascii[joined])
                break
        if hit is None:
            a += 1
            continue
        k, s, e, entries = hit
        out += [(s, e, entry) for entry in entries]
        a += k
    return out


def _ends_with(t: str, a: int, words: frozenset[str], longest: int) -> bool:
    return any(t[a - k:a] in words for k in range(1, min(longest, a) + 1))


def _starts_with(t: str, b: int, words: frozenset[str], longest: int) -> bool:
    return any(t[b:b + k] in words for k in range(1, min(longest, len(t) - b) + 1))


def _starting(t: str, i: int, words: Sequence[str], limit: int, gaps: frozenset[int]) -> str:
    """The first of ``words`` that ``t`` has at ``i`` and that ends by ``limit``. A Latin
    word must end there too: "no" of "no huangqi" (a space, which the compact text keeps
    as a gap), not of "nothing"."""
    for w in words:
        if i + len(w) <= limit and t.startswith(w, i):
            j = i + len(w)
            if w.isascii() and j < len(t) and j not in gaps and t[j].isascii() \
                    and t[j].isalnum():
                continue
            return w
    return ""


def _negation(t: str, i: int, limit: int, gaps: frozenset[int]) -> str:
    """The denial ``t`` opens with at ``i``, unless the word there only starts with one
    (非常, 未成年, 无意中, no doubt)."""
    w = _starting(t, i, _NEGATIONS, limit, gaps)
    if w and any(t.startswith(nd, i) for nd in _NOT_DENIALS):
        return ""
    return w


#: The longest excerpt of a word a name stands inside that a message quotes, on each side.
_WORD_REACH = 6


class _Reader:
    """One record, read: the names it holds, each in its place (see the module doc)."""

    def __init__(self, record: Any, kb: Any, vocab: _Vocab):
        self.kb, self.vocab = kb, vocab
        self.record = record
        self.shown = _shown_text(record)
        self._raw: tuple[str, list[int]] | None = None
        self.f = _fold_shown(self.shown)              # folded, whitespace kept
        self.t, self.gaps, self.keep, self.breaks = _compact(self.f)
        self.n = len(self.t)
        self.cuts = sorted(self.gaps)
        self._dl: dict[tuple[int, bool, bool], bool] = {}
        self._dr: dict[tuple[int, bool, bool], bool] = {}
        self._da: dict[int, str] = {}
        self._nw: list[int] | None = None

    # ---------------------------------------------------------------- text helpers
    def raw(self) -> tuple[str, list[int]]:
        """The record as written (visible characters only) and, for each character of the
        normalised text, where it comes from in it. Where NFKC joins characters (a
        combining mark), the normalised text itself."""
        if self._raw is None:
            raw = _visible(str(self.record or ""))
            pieces = [unicodedata.normalize("NFKC", c) for c in raw]
            if "".join(pieces) == self.shown:
                self._raw = (raw, [i for i, piece in enumerate(pieces) for _ in piece])
            else:
                self._raw = (self.shown, list(range(len(self.shown))))
        return self._raw

    def text(self, a: int, b: int) -> str:
        """The record as written between compact positions ``a`` and ``b``, whitespace
        between them shown as one space."""
        raw, at = self.raw()
        out, last = [], -1
        for p in range(a, b):
            r = at[self.keep[p]]
            if r != last:
                out.append((" " if p in self.gaps and p > a else "") + raw[r])
                last = r
        return "".join(out)

    def snippet(self, a: int, b: int, reach: int = 12) -> str:
        lo, hi = max(0, a - reach), min(self.n, b + reach)
        return ("…" if lo > 0 else "") + self.text(lo, hi) + ("…" if hi < self.n else "")

    def crosses(self, s: int, e: int) -> bool:
        k = bisect_right(self.cuts, s)
        return k < len(self.cuts) and self.cuts[k] < e

    # ---------------------------------------------------------------- boundaries
    def dl(self, p: int, spaced: bool, one: bool = False) -> bool:
        """A boundary of its own before position ``p``. ``spaced`` is the first reading, in
        which whitespace is one; ``one`` asks it for a name of one character."""
        key = (p, spaced, one)
        if key not in self._dl:
            t = self.t
            if p <= 0:
                v = True
            elif p < self.n and t[p - 1:p + 1] in _EVERYDAY:
                v = False
            elif spaced and p in self.gaps:
                v = True
            elif not _cjk(t[p - 1]):
                v = True
            elif one:
                v = _ends_with(t, p, _BEFORE_ONE, _BEFORE_ONE_MAX)
            else:
                v = _ends_with(t, p, _BEFORE, _BEFORE_MAX)
            self._dl[key] = v
        return self._dl[key]

    def dr(self, p: int, spaced: bool, one: bool = False) -> bool:
        """A boundary of its own after position ``p``."""
        key = (p, spaced, one)
        if key not in self._dr:
            t = self.t
            if p >= self.n:
                v = True
            elif spaced and p in self.gaps:
                v = True
            elif t.startswith(_PRODUCT_WORDS, p):
                v = False
            elif not _cjk(t[p]):
                v = True
            elif one:
                v = _starts_with(t, p, _AFTER_ONE, _AFTER_ONE_MAX)
            else:
                v = _starts_with(t, p, _AFTER, _AFTER_MAX)
            self._dr[key] = v
        return self._dr[key]

    def _strong_right(self, p: int, spaced: bool) -> bool:
        """After position ``p``: the end of the entry, a mark, Latin letters or a digit, or a
        word of allergy itself (过敏, 不耐受, 皮疹)."""
        t = self.t
        return (p >= self.n or (spaced and p in self.gaps) or not _cjk(t[p])
                or t.startswith(_STRONG_AFTER, p))

    def next_word(self, p: int) -> int:
        """The first position at or after ``p`` that holds a letter, a digit or a Chinese
        character (``n`` if none): worked out once for the whole record."""
        if self._nw is None:
            t, n = self.t, self.n
            nw = [n] * (n + 1)
            for i in range(n - 1, -1, -1):
                nw[i] = i if (t[i].isalnum() or _cjk(t[i])) else nw[i + 1]
            self._nw = nw
        return self._nw[min(p, self.n)]

    # ---------------------------------------------------------------- reading
    def read(self) -> list[Mention]:
        t, n = self.t, self.n
        if not n:
            return []
        vocab = self.vocab
        # 1. every name at every place
        cands: list[tuple[int, int, str]] = []
        root, longest = vocab.trie, vocab.longest
        for i in range(n):
            node = root.get(t[i])
            j = i
            while node is not None:
                j += 1
                kind = node.get("")
                if kind is not None:
                    cands.append((i, j, kind))
                if j >= n or j - i >= longest:
                    break
                node = node.get(t[j])
        crossing = [self.crosses(s, e) for s, e, _ in cands]
        # 2. which stand: a boundary of its own on each side, or a chain of names of two
        # characters or more that leads to one (北芪人参过敏). The first reading (A) has
        # whitespace as a boundary and no name across it; the second (B) has neither.
        ends_at: dict[int, list[int]] = {}
        starts_at: dict[int, list[int]] = {}
        for x, (s, e, kind) in enumerate(cands):
            if e - s >= 2 and kind != "other":
                ends_at.setdefault(e, []).append(x)
                if kind == "name":
                    starts_at.setdefault(s, []).append(x)
        positions = sorted({p for s, e, _ in cands for p in (s, e)})
        la: dict[int, bool] = {}
        lb: dict[int, bool] = {}
        for p in positions:
            a, b = self.dl(p, True), self.dl(p, False)
            if not (p < n and p > 0 and t[p - 1:p + 1] in _EVERYDAY):
                for x in ends_at.get(p, ()):
                    s = cands[x][0]
                    a = a or (not crossing[x] and la.get(s, False))
                    b = b or lb.get(s, False)
            la[p], lb[p] = a, b
        ra: dict[int, bool] = {}
        rb: dict[int, bool] = {}
        for p in reversed(positions):
            a, b = self.dr(p, True), self.dr(p, False)
            for x in starts_at.get(p, ()):
                e = cands[x][1]
                a = a or (not crossing[x] and ra.get(e, False))
                b = b or rb.get(e, False)
            ra[p], rb[p] = a, b
        self.la, self.lb, self.ra, self.rb = la, lb, ra, rb

        def stands(x: int) -> bool:
            s, e, kind = cands[x]
            spaced = not crossing[x]
            lx, rx = (la, ra) if spaced else (lb, rb)
            if kind == "formula":
                return self.dl(s, spaced) and rx[e]
            if e - s == 1:
                return self.dl(s, spaced, True) and self.dr(e, spaced, True)
            if kind == "name" and s > 0 and t[s - 1:s + 1] in _EVERYDAY:
                # 对于术过敏: the 于 of 对于 may start 于术 after all, where 对 stands as a word
                # and an allergy word, or the end of the entry, follows (对于术后 is 对于 + 术后)
                return (t[s - 1] in _BEFORE and self.dl(s - 1, spaced)
                        and self._strong_right(e, spaced))
            return lx[s] and rx[e]

        standing = [kind != "other" and stands(x) for x, (_, _, kind) in enumerate(cands)]
        # 3. a name inside a longer standing name (or an everyday word) is that one's
        cover_a = _Cover([(s, e) for x, (s, e, k) in enumerate(cands)
                          if (standing[x] and not crossing[x]) or k == "other"
                          or (k == "formula" and not crossing[x])])
        cover_b = _Cover([(s, e) for x, (s, e, _) in enumerate(cands)
                          if standing[x] and crossing[x]])
        stand2 = _Marks(n, [(s, e) for x, (s, e, _) in enumerate(cands)
                            if standing[x] and not crossing[x] and e - s >= 2])
        stand1 = _Marks(n, [(s, e) for x, (s, e, _) in enumerate(cands)
                            if standing[x] and not crossing[x]])
        # a name that does not stand and shares a character with an everyday word is that
        # word's (头发 across 舌头|发麻)
        others = _Marks(n, [(s, e) for s, e, k in cands if k == "other"])
        keep_x: list[int] = []
        for x, (s, e, kind) in enumerate(cands):
            if kind == "other" or cover_a.covers(s, e):
                continue
            if not standing[x] and others.any(s, e):
                continue
            if crossing[x]:
                if (cover_b.covers(s, e) or stand2.any(s, e)
                        or (not standing[x] and stand1.any(s, e))):
                    continue
            elif not standing[x] and cover_b.covers(s, e):
                continue
            keep_x.append(x)
        # 4. a name that starts with a denial's character and runs into what a denial
        # names is the denial (没药物过敏史)
        dropped: set[int] = set()
        for x in keep_x:
            s, e, kind = cands[x]
            if kind == "name" and not crossing[x]:
                neg = _negation(t, s, e, self.gaps)
                if (neg and _cjk(neg[0]) and not ra.get(e, False)
                        and t.startswith(_DENIED_OBJECTS, s + len(neg))):
                    dropped.add(x)
        keep_x = [x for x in keep_x if x not in dropped]
        self.named = {cands[x][0]: cands[x][1] for x in keep_x if cands[x][2] != "other"}
        self.toks = _tokens(t, self.gaps)
        latin = _ascii_spans(t, self.toks, vocab)
        self.end_at: dict[int, int] = {}
        for x in keep_x:
            if standing[x]:
                s, e, _ = cands[x]
                self.end_at[s] = max(self.end_at.get(s, 0), e)
        for s, e, _ in latin:
            self.end_at[s] = max(self.end_at.get(s, 0), e)
        self.latin_cover = _Cover([(s, e) for s, e, _ in latin])
        self._clause_marks(_Marks(n, [(cands[x][0], cands[x][1]) for x in keep_x
                                      if cands[x][2] != "other"]))
        # 5. each name in its place
        out: list[Mention] = []
        for x in keep_x:
            s, e, kind = cands[x]
            name = t[s:e]
            spaced = not crossing[x]
            if self._person(s, e):
                continue
            if standing[x]:
                out.append(self._mention(kind, name, s, e))
                continue
            if e - s == 1 and kind == "name":
                continue                  # a name of one character inside a word is no name
            if kind == "name":
                whole = self._processed_word(s, e, spaced)
                if whole is not None:
                    a, b = whole
                    out.append(self._mention("name", t[a:b], a, b))
                    continue
            a, b = self._word(s, e, spaced)
            word = (("…" if not self._left(a, spaced) else "") + self.text(a, b)
                    + ("…" if not self._right(b, spaced) else ""))
            out.append(self._mention(kind, name, s, e, word=word, span=(a, b)))
        for s, e, entry in latin:
            if self._person_ascii(s, e):
                continue
            written = self.text(s, e)
            denied, taken = self._status(s, e)
            if entry.kind == "name":
                ctx = "" if denied or taken else (self._bare_name_label(s, e)
                                                  or self._ascii_context(s, e))
                out.append(Mention(entry.target[0], s, e, written=written, how=entry.how,
                                   denied=denied, taken=taken, context=ctx))
            else:
                out.append(Mention(written, s, e, written=written, how=entry.how,
                                   kind=entry.kind, species=entry.target, denied=denied,
                                   taken=taken, of=entry.source))
        return sorted(out, key=lambda m: (m.start, m.end))

    def _mention(self, kind: str, name: str, s: int, e: int, *, word: str = "",
                 span: tuple[int, int] | None = None) -> Mention:
        a, b = span or (s, e)
        denied, taken = self._status(a, b)
        if kind == "formula":
            f = self.vocab.formulas[name]
            return Mention(f.name, s, e, written=name, how=f.how, kind="formula", of=f.name,
                           word=word, denied=denied, taken=taken)
        return Mention(name, s, e, word=word, denied=denied, taken=taken)

    def _left(self, p: int, spaced: bool) -> bool:
        table = self.la if spaced else self.lb
        return table[p] if p in table else self.dl(p, spaced)

    def _right(self, p: int, spaced: bool) -> bool:
        table = self.ra if spaced else self.rb
        return table[p] if p in table else self.dr(p, spaced)

    def _word(self, s: int, e: int, spaced: bool) -> tuple[int, int]:
        """The longer word a name that does not stand is inside, up to ``_WORD_REACH``
        characters on each side."""
        a, b = s, e
        while not self._left(a, spaced) and s - a < _WORD_REACH and a > 0:
            a -= 1
        while not self._right(b, spaced) and b - e < _WORD_REACH and b < self.n:
            b += 1
        return a, b

    def _processed_word(self, s: int, e: int, spaced: bool) -> tuple[int, int] | None:
        """The name with a processing word before it, or a product suffix after it, standing
        as one word (炙北芪, 炒夜交藤, 甘草片), or None."""
        from ..sources.materia import _ENDS_IN_PROCESSING, PROCESSING_WORDS
        t = self.t
        if self._right(e, spaced):
            for w in sorted(PROCESSING_WORDS, key=len, reverse=True):
                a = s - len(w)
                if (a >= 0 and t.startswith(w, a) and self.dl(a, spaced)
                        and (a == 0 or t[a - 1:s] not in _ENDS_IN_PROCESSING)):
                    return a, e
        if (e < self.n and t[e] in _PRODUCT_SUFFIXES and self._left(s, spaced)
                and self.dr(e + 1, spaced)):
            return s, e + 1
        return None

    def _person(self, s: int, e: int) -> bool:
        """The name right after a label that introduces a person (记录人：姜), with no word of
        allergy after it in its clause (医生：黄芪过敏 is an allergy)."""
        return (_PERSON_CJK.search(self.t[max(0, s - 6):s]) is not None
                and not self._has(self.allergy, e, self._clause(s)[1]))

    def _person_ascii(self, s: int, e: int) -> bool:
        """The Latin-lettered name right after a title or label that introduces a person
        (Dr. Huang Jing, patient name: Huang Qi), with no word of allergy after it in its
        clause; or right before a title (Jing Jie, MD), with no word of allergy anywhere in
        its clause (Reaction to huang qi, MD notified is an allergy)."""
        a, b = self.keep[s], self.keep[e - 1] + 1
        cs, ce = self._clause(s)
        if self._has(self.allergy, e, ce):
            return False
        if _PERSON_BEFORE.search(self.f[max(0, a - 40):a]) is not None:
            return True
        return (_PERSON_AFTER.match(self.f[b:b + 12]) is not None
                and not self._has(self.allergy, self._lead_clause(cs), s))

    def _bare_name_label(self, s: int, e: int) -> str:
        """"name_label" for a Latin-lettered name right after a bare name label (Name: huang
        qi) with no word of allergy in its clause or its label: such a label also introduces
        a person. A label that names a drug (Allergen name:, Drug name:) is an allergy line's."""
        a = self.keep[s]
        before = self.f[max(0, a - 40):a]
        if _NAME_LABEL.search(before) is None or _DRUG_NAME_LABEL.search(before) is not None:
            return ""
        cs, ce = self._clause(s)
        return "" if self._has(self.allergy, self._lead_clause(cs), ce) else "name_label"

    # ---------------------------------------------------------------- clauses
    def _clause_marks(self, names: "_Marks") -> None:
        t, n, gaps = self.t, self.n, self.gaps
        self.bounds = sorted({0, *(m.end() for m in _CLAUSE_MARK.finditer(t)),
                              *(g for g in gaps if 0 < g < n and _cjk(t[g - 1]) and _cjk(t[g])),
                              *(g for g in self.breaks if 0 < g < n)})
        self.sentences = sorted({*(m.end() for m in _SENTENCE_MARK.finditer(t)),
                                 *(g for g in self.breaks if 0 < g < n)})
        closers = [m.start() for m in _CLOSERS.finditer(t)]
        # a word of allergy, reaction or use inside a name found is the name's: 泻 of 泽泻,
        # 食 of 食盐
        allergy = [m.start() for m in _ALLERGY_CJK.finditer(t)
                   if not names.all(m.start(), m.end())]
        use = [m.start() for m in _USE_CJK.finditer(t) if not names.all(m.start(), m.end())]
        self.use_after = [m.start() for m in _USE_AFTER.finditer(t)]
        cause = [m.start() for m in _CAUSE_CJK.finditer(t)]
        toks = self.toks
        for k, (s, e) in enumerate(toks):
            w = t[s:e]
            nxt = t[toks[k + 1][0]:toks[k + 1][1]] if k + 1 < len(toks) else ""
            if w.startswith(_ALLERGY_ASCII):
                allergy.append(s)
                # allergies that a denial does not go on to name closes it: No known drug
                # allergies 黄芪过敏; not No allergy to huang qi, No allergic reaction to …
                if w.startswith("allerg") and nxt not in _ALLERGY_OF:
                    closers.append(s)
            elif w in _USE_ASCII:
                use.append(s)
            elif w in _CAUSE_ASCII:
                cause.append(s)
            # can't take, cannot take, unable to take, does not tolerate: an intolerance
            if (w in _CANNOT_ASCII or (w in _CANNOT_NT and nxt == "t"
                                       and t[e:toks[k + 1][0]] in ("'", "’"))):
                allergy.append(s)
            elif w.startswith("tolerat") and k > 0 and t[toks[k - 1][0]:toks[k - 1][1]] in (
                    *_NOT_TOLERATE, "t"):
                allergy.append(s)
        self.allergy = sorted(allergy)
        self.use = sorted(use)
        self.cause = sorted(cause)
        self.closers = sorted(closers)
        self.tok_starts = [s for s, _ in toks]
        self._opens: dict[int, tuple[int, str]] = {}
        self._after: dict[int, int] = {}

    def _clause(self, p: int) -> tuple[int, int]:
        k = bisect_right(self.bounds, p)
        return self.bounds[k - 1], (self.bounds[k] if k < len(self.bounds) else self.n)

    @staticmethod
    def _has(points: Sequence[int], a: int, b: int) -> bool:
        k = bisect_left(points, a)
        return k < len(points) and points[k] < b

    def _lead_clause(self, cs: int) -> int:
        """Where a clause begins together with the label clause before it (现用药：人参,
        Allergy: huangqi): the label clause's start, or ``cs``."""
        if cs > 0 and self.t[cs - 1] in ":：∶":
            return self._clause(cs - 1)[0]
        return cs

    def _status(self, s: int, e: int) -> tuple[bool, bool]:
        """(denied, taken) for the name at ``s``–``e``. A record that the herb is tolerated
        (黄芪耐受, 服黄芪无不适, tolerates 黄芪) is both: it denies, and says the herb is used."""
        how = self._denial(s, e)
        if how:
            return True, how == "tolerate"
        return False, self._taken(s, e)

    def _used(self, s: int, e: int, lead: int, ce: int) -> tuple[bool, int]:
        """Whether the record says the herb at ``s``–``e`` is taken, eaten or used: a word
        of use before it in its clause or label (目前在吃人参, 现用药：人参, 每日泡黄芪水喝), or
        right after it or its list, also across a colon or a bracket (人参每日一支, 黄芪水喝,
        人参吃了, 黄芪：服用中, 黄芪（服用中）); and where the clause it is said in ends."""
        # (``s``–``e`` may be the longer word a name is inside: 党参炖汤)
        if (self._has(self.use, max(lead, s - 40), s)
                or self._has(self.use_after, s + 1, min(ce, e + 8))
                or self._has(self.use, s + 1, min(ce, e + 8))):
            return True, ce
        for p in dict.fromkeys((e, self._after_list(e))):
            r = _AFTER_SEP.match(self.t, p, min(self.n, p + 4)).end()
            if r > p and (self._has(self.use, r, min(self.n, r + 4))
                          or self._has(self.use_after, r, min(self.n, r + 4))):
                return True, self._clause(r)[1]
        return False, ce

    def _taken(self, s: int, e: int) -> bool:
        """The record says the herb is taken, eaten or used, and says no reaction to it:
        neither its clause (with the label before it) nor a clause going on from it, to the
        end of the sentence, holds a word of allergy or reaction; it is not named as the cause
        of something (与服用黄芪有关, 服用黄芪所致, 全身皮疹，因服用黄芪); and it is not a note in
        brackets on a reaction written before it (皮疹（服黄芪后）)."""
        t = self.t
        cs, ce = self._clause(s)
        lead = self._lead_clause(cs)
        if (self._has(self.allergy, lead, ce) or self._has(self.cause, e, ce)
                or _CAUSE_BEFORE.search(t, max(cs, s - 5), s)):
            return False
        used, ce = self._used(s, e, lead, ce)
        if not used or self._has(self.allergy, lead, ce):
            return False
        if cs > 0 and t[cs - 1] in _OPEN_BRACKETS:
            # a note on a reaction before it, not on a denial (无过敏史（曾服四君子汤，效佳）)
            before, _ = self._clause(cs - 1)
            if self._has(self.allergy, before, cs - 1) and not self._opening(before)[1]:
                return False
        return not self._goes_on(e, ce)

    def _goes_on(self, e: int, ce: int) -> bool:
        """A clause after the one that ends at ``ce`` goes on from it, in the same sentence,
        and holds a word of allergy or reaction. It goes on when the herb's clause ends in a
        time after the herb (服用黄芪后，皮肤瘙痒; 服用黄芪期间，…) or when it opens with a word
        that tells what happened: a time (3小时后, 次日), a part of the body (皮肤, 面部, 嘴唇),
        a word of feeling (自觉, 感觉), a reaction (皮疹), or an English word that goes on
        (then, got, developed). A clause that is a label (过敏史：) or opens with a denial
        (无不适) is passed over. Looks no further than 60 characters and four clauses."""
        t, n = self.t, self.n
        nxt = self.next_word(ce)
        if nxt >= n:
            return False
        k = bisect_right(self.sentences, ce - 1)
        end = min(n, ce + 60, self.sentences[k] if k < len(self.sentences) else n)
        if nxt >= end:
            return False
        p = self._after_list(e)
        timed = p < ce and _TEMPORAL.match(t, p, ce) is not None
        if not timed:
            j = bisect_left(self.tok_starts, nxt)
            words = [t[a:b] for a, b in self.toks[j:j + 3]] if (
                j < len(self.toks) and self.toks[j][0] == nxt) else []
            first = words[0] if words else ""
            later = (len(words) == 3 and first.isdigit() and words[1] in _TIME_UNITS
                     and words[2] in ("later", "after"))       # 2 hours later, …
            if not (t.startswith(_CONTINUE_CJK, nxt) or _CONTINUE_TIME.match(t, nxt, end)
                    or _ALLERGY_CJK.match(t, nxt, end) or first in _CONTINUE_ASCII
                    or later or first.startswith(_ALLERGY_ASCII)):
                return False
        a = nxt
        for _ in range(4):
            if a >= end:
                break
            b = min(self._clause(a)[1], end)
            label = t[b - 1] in ":：∶" if b > a else False
            if not label and not self._opening(a)[1] and self._has(self.allergy, a, b):
                return True
            a = self.next_word(b)
        return False

    def _ascii_context(self, s: int, e: int) -> str:
        """"" when the Latin or pinyin name at ``s``–``e`` stands in an allergy line (a word
        of allergy in its clause or the label before it, or nothing but names around it);
        "unconfirmed" otherwise."""
        cs, ce = self._clause(s)
        lead = self._lead_clause(cs)
        if self._has(self.allergy, lead, ce):
            return ""
        nxt = self.next_word(ce)
        if nxt < self.n and nxt - ce <= 3:
            _, ce2 = self._clause(nxt)
            if self._has(self.allergy, nxt, min(ce2, nxt + 12)):
                return ""
        k0 = bisect_left(self.tok_starts, lead)
        k1 = bisect_left(self.tok_starts, ce)
        lo = max(k0, bisect_left(self.tok_starts, s) - 12)
        hi = min(k1, bisect_left(self.tok_starts, e) + 12)
        for k in range(lo, hi):
            a, b = self.toks[k]
            if self.latin_cover.holds(a, b):
                continue              # a word of a name (radix of astragali radix)
            if self.t[a:b] not in _ASCII_LABELS and not self.t[a:b].isdigit():
                return "unconfirmed"
        return ""

    # ---------------------------------------------------------------- denials
    def _opening(self, cs: int) -> tuple[int, str]:
        """Where the denial a clause starting at ``cs`` opens with stands, and which (""
        for none): 患者否认 → 否认, 过敏史无 → 无."""
        t, gaps = self.t, self.gaps
        if cs not in self._opens:
            i = cs
            for _ in range(4):
                lead = _starting(t, i, _CLAUSE_LEADS, self.n, gaps)
                if not lead:
                    break
                i += len(lead)
            neg = _negation(t, i, self.n, gaps)
            if neg and self.named.get(i, 0) > i + len(neg):
                neg = ""                    # 没药人参过敏: the 没 is 没药's, no denial
            self._opens[cs] = (i, neg)
        return self._opens[cs]

    def _opens_denial(self, cs: int, s: int) -> bool:
        """The clause that starts at ``cs`` opens with a denial that no closer ends before
        ``s`` (``_opens[cs]`` holds which)."""
        i, neg = self._opening(cs)
        if not neg or i + len(neg) > s:
            return False
        j = i + len(neg)
        k = bisect_left(self.closers, j)
        return k == len(self.closers) or self.closers[k] >= s

    def _after_list(self, p: int) -> int:
        """Past the rest of the list a name ends and its tail: 黄芪、党参等 → after 等."""
        t, n, end_at = self.t, self.n, self.end_at
        if p in self._after:
            return self._after[p]
        path = []
        q = p
        while True:
            if q in self._after:
                q = self._after[q]
                break
            path.append(q)
            if q in end_at and end_at[q] > q:
                q = end_at[q]
                continue
            sep = next((w for w in ("以及", "或者", "、", "/", "和", "及", "与", "跟", "或")
                        if t.startswith(w, q)), "")
            if sep and q + len(sep) in end_at:
                q = end_at[q + len(sep)]
                continue
            q = _LIST_TAIL.match(t, q).end()
            while q < n and t[q] in _CLOSE_BRACKETS:
                q += 1
            if q < n and t[q] in _OPEN_BRACKETS:
                # past a note in brackets: 补中益气汤（含黄芪、人参）无不适
                r = next((j for j in range(q + 1, min(n, q + 40)) if t[j] in _CLOSE_BRACKETS), -1)
                if r > 0:
                    q = r + 1
            break
        for x in path:
            self._after[x] = q
        return q

    def _denied_after(self, p: int) -> str:
        """"deny" or "tolerate" when a denial is written at ``p``, after a name or its list
        (黄芪：无, 黄芪无过敏史; 黄芪耐受, 服黄芪无不适, 服用黄芪后无不适, 人参吃了没事, 当归用过，
        无过敏, 人参：可), or "". Worked out once for each place: many names of one list share
        the place after it."""
        if p not in self._da:
            self._da[p] = self._denied_at(p)
        return self._da[p]

    def _ends_word(self, j: int) -> bool:
        t, n = self.t, self.n
        while j < n and t[j] in _CLOSE_BRACKETS:
            j += 1
        return (j >= n or j in self.gaps
                or not (_cjk(t[j]) or (t[j].isascii() and t[j].isalnum())))

    def _denied_at(self, p: int) -> str:
        t, n = self.t, self.n
        if _DENIAL_DASH.match(t, p):
            return "deny"
        q = _AFTER_LABEL.match(t, p).end()
        r = _AFTER_SEP.match(t, q).end()
        m = _DENIAL_PHRASE.match(t, r)
        if m:
            return "deny" if _DENIES_ALLERGY.search(m.group(0)) else "tolerate"
        # a word of use or a time between: 服用黄芪后无不适, 黄芪服用后未见过敏, 人参吃了没事,
        # 当归用过，无过敏. The herb was taken, so what follows says it was tolerated.
        u = _USE_SEGMENT.match(t, r).end()
        if u > r:
            v = u + 1 if u < n and t[u] in ",，" else u
            if _DENIAL_PHRASE.match(t, v):
                return "tolerate"
        m = _DENIAL_BARE.match(t, r)
        if m:
            return "deny" if self._ends_word(m.end()) else ""
        m = _TOLERATE_BARE.match(t, r)
        if m and r > q and t[r - 1] in ":：∶(" and self._ends_word(m.end()):
            return "tolerate"                       # 人参：可, 黄芪（可）
        return ""

    def _next_clause_tolerates(self, s: int, e: int) -> bool:
        """A clause that says the herb is used, and the next clause says there was no
        reaction: 曾服当归，无反应; 吃过人参，没事; Took ginseng, no reaction."""
        cs, ce = self._clause(s)
        lead = self._lead_clause(cs)
        if self._has(self.allergy, lead, ce):
            return False
        used, ce = self._used(s, e, lead, ce)
        if not used:
            return False
        nxt = self.next_word(ce)
        if nxt >= self.n or nxt - ce > 3 or nxt in self.breaks:
            return False
        return _DENIAL_PHRASE.match(self.t, nxt) is not None

    def _retracted(self, p: int) -> bool:
        k = bisect_right(self.bounds, p)
        if k >= len(self.bounds):
            return False
        b = self.bounds[k]
        b2 = self.bounds[k + 1] if k + 1 < len(self.bounds) else self.n
        if b2 - b > 40:
            return False
        clause = self.t[b:b2].rstrip("。.，,；;！!、 ")
        return bool(clause) and _RETRACTION.fullmatch(clause) is not None

    def _denial(self, s: int, e: int) -> str:
        """"deny", "tolerate" (the record says the herb is used with no reaction, or can be
        used: 黄芪耐受, 服黄芪无不适, 黄芪可用, tolerates 黄芪) or ""."""
        cs, ce = self._clause(s)
        if self._opens_denial(cs, s):
            neg = self._opens[cs][1]
            # NKDA, nil, none take no object: NKDA 黄芪过敏 is a second statement, not a denial
            # of 黄芪 (NKDA 北芪 still is one)
            if not (neg in _COMPLETE_DENIALS and self._has(self.allergy, e, ce)):
                return "tolerate" if neg in _TOLERATES else "deny"
        t = self.t
        if cs > 0 and t[cs - 1] in _OPEN_BRACKETS:
            # a bracket inside a denial that names what it denies: 否认中药（黄芪、人参）过敏
            outer, _ = self._clause(cs - 1)
            if outer < cs - 1 and self._opens_denial(outer, cs - 1):
                i, neg = self._opens[outer]
                # the bracket is what the denial names (否认中药（…）过敏), not a note after a
                # whole denial (No allergies (uses licorice root tea))
                if (i + len(neg) < cs - 1
                        and not self._has(self.allergy, i + len(neg), cs - 1)):
                    return "deny"
        p = self._after_list(e)
        return (self._denied_after(e) or self._denied_after(p)
                or ("deny" if self._retracted(p) else "")
                or ("tolerate" if self._next_clause_tolerates(s, e) else ""))


class _Cover:
    """Intervals, to ask whether one holds a shorter interval."""

    def __init__(self, spans: Iterable[tuple[int, int]]):
        at: dict[int, int] = {}
        for s, e in spans:
            if e > at.get(s, -1):
                at[s] = e
        self.at = at
        self.starts = sorted(at)
        self.best: list[int] = []
        m = -1
        for s in self.starts:
            m = max(m, at[s])
            self.best.append(m)

    def covers(self, s: int, e: int) -> bool:
        """An interval holds ``s``–``e`` and is longer."""
        if self.at.get(s, -1) > e:
            return True
        k = bisect_left(self.starts, s) - 1
        return k >= 0 and self.best[k] >= e

    def holds(self, s: int, e: int) -> bool:
        """An interval holds ``s``–``e``, or is it."""
        return self.at.get(s, -1) >= e or self.covers(s, e)


class _Marks:
    """Positions covered by some interval, to ask whether a span overlaps one."""

    def __init__(self, n: int, spans: Iterable[tuple[int, int]]):
        diff = [0] * (n + 2)
        for s, e in spans:
            diff[s] += 1
            diff[e] -= 1
        run, acc = 0, [0]
        for p in range(n + 1):
            run += diff[p]
            acc.append(acc[-1] + (1 if run > 0 else 0))
        self.acc = acc

    def any(self, s: int, e: int) -> bool:
        return self.acc[e] - self.acc[s] > 0

    def all(self, s: int, e: int) -> bool:
        """Every position of ``s``–``e`` is covered."""
        return self.acc[e] - self.acc[s] == e - s


def mentions(record: Any, *, kb: Any = None, pack: Any = None,
             herbs: Sequence[str] = ()) -> list[Mention]:
    """The names an allergy record holds, each read in its place (see the module doc).
    ``herbs`` is accepted for compatibility and not used: no part of a prescribed herb's
    name is scanned for."""
    kb = kb if kb is not None else _default_kb()
    return _Reader(record, kb, _vocab(kb, pack)).read()


def _default_kb() -> Any:
    from ..tcm.knowledge import default_knowledge
    return default_knowledge()


def _herb_key(herb: str) -> str:
    return normalise(fold(herb))


# ============================================================== matching a name to a herb
@dataclass(frozen=True)
class _Tier:
    rank: int                 # 3 the herb (stop), 2 one source (block), 1 a near name (block)
    code: str
    why: str
    source: str = ""


def _species(kb: Any, pack: Any, name: str) -> tuple[tuple[str, ...], str]:
    """The source species the tables give a name, and which table gives them."""
    for b in _forms(kb, name)[0][::-1] if kb is not None else (name,):
        m = _materia(b)
        if m is not None:
            return tuple(m.species_names), "药材名表（materia）"
    sp = (getattr(pack, "herb_sources", {}) or {}).get(name) if pack is not None else None
    if sp:
        return tuple(sp[0]), sp[1]
    ids = _identity(kb, name) if kb is not None else frozenset()
    if len(ids) == 1 and hasattr(kb, "herbs") and (h := kb.herbs.get(next(iter(ids)))):
        return tuple(getattr(h, "species", ())), "种子知识库"
    return (), ""


_PACK_NAMES: dict[tuple[int, str], frozenset[str]] = {}


def _pack_names(pack: Any) -> frozenset[str]:
    """The pack's herbs and cited aliases: names a table (the pack) holds."""
    if pack is None:
        return frozenset()
    key = (id(pack), getattr(pack, "digest", ""))
    if key not in _PACK_NAMES:
        if len(_PACK_NAMES) > 8:
            _PACK_NAMES.clear()
        _PACK_NAMES[key] = frozenset((*pack.herbs, *(getattr(pack, "herb_aliases", {}) or {})))
    return _PACK_NAMES[key]


def _first_materia(kb: Any, forms: Sequence[str]) -> Any:
    for b in forms:
        if (m := _materia(b)) is not None:
            return m
    return None


@lru_cache(maxsize=16384)
def _one_herb(kb: Any, x: str, y: str) -> str:
    """How the names ``x`` and ``y`` are one herb by a drug-level table: "same" (one name),
    "kb" (the seed knowledge base gives both to one herb), "reviewed" (the materia table's
    reviewed alias), or ""."""
    if x == y:
        return "same"
    if _identity(kb, x) & _identity(kb, y):
        return "kb"
    if _reviewed(x) == y or _reviewed(y) == x:
        return "reviewed"
    return ""


def _one_drug(kb: Any, x: str, y: str) -> str:
    """Kept for the tools that called it: how ``x`` and ``y`` are one drug by a drug-level
    table (``_one_herb``), or ""; one name inside another is not."""
    return _one_herb(kb, x, y)


def _tier(kb: Any, pack: Any, x: str, h: str) -> _Tier | None:
    """How the name ``x`` found in a record is the herb ``h``, if it is.

    * 3, ``allergy``: what a drug-level table says is the herb: the same name; one herb by
      the seed knowledge base (黄耆 and 黄芪); a reviewed alias of the materia table (川当归
      and 当归); or the processed forms of one drug, a processing word before a name the
      tables give the herb (炙黄芪, 生地黄 and 熟地黄 as forms of 地黄, 甘草片).
    * 2, ``allergy_source``: the materia table lists both names, or what they are processed
      forms of, under one entry, which groups a source's names, not one drug's (海带 and 海藻,
      鸡蛋 and 鸡内金, 丹皮 and 牡丹皮); or the two drugs share a species, by the materia
      table's, the pack's or the knowledge base's species (芍药 and 赤芍, 何首乌 and 首乌藤).
    * 1, ``allergy_near_name``: one name inside the other, which no table says is one drug
      (白附子 and 附子, 土茯苓 and 茯苓).
    One name inside another is never more than a near name: 莱菔 is the plant and 莱菔子 its
    seed, 车前 the plant and 车前子 its seed, though the materia table files each pair under
    one entry.
    """
    if x == h:
        return _Tier(3, "allergy", "")
    how = _one_herb(kb, x, h)
    if how == "kb":
        return _Tier(3, "allergy", f"{x}与{h}为同一药材", "种子知识库")
    if how == "reviewed":
        return _Tier(3, "allergy", f"{x}为{h}的别名", "药材名表（materia）复核的别名")
    x_forms, x_wider = _forms(kb, x)
    h_forms, h_wider = _forms(kb, h)
    for xs, ys in ((x_forms, h_wider), (x_wider, h_forms)):
        for a in xs:
            for b in ys:
                if (rel := _one_herb(kb, a, b)) and (a != x or b != h):
                    base = h_forms[-1] if len(h_forms) > 1 or len(x_forms) == 1 else x_forms[-1]
                    return _Tier(3, "allergy", f"{x}与{h}同为{base}或其炮制品",
                                 "种子知识库" if rel == "kb" else "")
    x_m, h_m = _first_materia(kb, x_forms), _first_materia(kb, h_forms)
    if x_m is not None and h_m is not None and x_m.id == h_m.id:
        named = x_forms[-1] if _materia(x) is None else x
        via = "" if named == x else f"（{x}为{named}的炮制品或制品）"
        return _Tier(2, "allergy_source",
                     f"药材名表将{named}与{h}同列于“{h_m.chinese}”条下（未必同一物种）{via}",
                     "药材名表（materia）")
    (x_sp, x_src), (h_sp, h_src) = _species(kb, pack, x), _species(kb, pack, h)
    shared = [s for s in x_sp if s in h_sp]
    if shared:
        return _Tier(2, "allergy_source",
                     f"{x}与{h}同源（同一物种：{'、'.join(shared)}），未必是同一种药",
                     "；".join(dict.fromkeys(s for s in (x_src, h_src) if s)))
    if len(x) >= 2 and len(h) >= 2 and (x in h or h in x):
        extra = _pack_names(pack)
        if not _known(kb, x, extra) or not _known(kb, h, extra):
            unknown = x if not _known(kb, x, extra) else h
            if unknown == h:
                return None           # read through the names inside it (``_Matcher``)
            which = f"“{unknown}”不是名表所载的药名"
        elif x_m is not None and h_m is not None:
            which = (f"药材名表将{x}" + ("" if x_m.chinese == x else f"（{x_m.chinese}）")
                     + f"与{h}列为不同药材")
        elif _identity(kb, x) and _identity(kb, h):
            which = f"种子知识库将{x}与{h}列为不同药材"
        else:
            which = f"名表未说{x}与{h}是同一药材"
        return _Tier(1, "allergy_near_name", f"二者名称相近（一名含另一名），但{which}")
    return None


@dataclass(frozen=True)
class _Path:
    name: str
    why: str = ""
    source: str = ""
    inner: bool = False          # a name inside the prescribed herb's unknown name


def _aliases(kb: Any, pack: Any, name: str) -> list[_Path]:
    """``name``, and what the pack's cited aliases and the knowledge base say it is. A
    processing word before a cited alias is that herb processed (炒夜交藤, 制黑顺片)."""
    from ..sources.materia import PROCESSING_WORDS
    out = [_Path(name)]
    table = (getattr(pack, "herb_aliases", {}) or {}) if pack is not None else {}
    if name in table:
        herb, source = table[name]
        out.append(_Path(herb, f"{name}为{herb}的别名（{source}）", source))
    else:
        for word in sorted(PROCESSING_WORDS, key=len, reverse=True):
            rest = name[len(word):]
            if name.startswith(word) and rest in table:
                herb, source = table[rest]
                out.append(_Path(herb, f"{name}为{rest}的炮制品，{rest}为{herb}的别名（{source}）",
                                 source))
                break
    ids = _identity(kb, name)
    if len(ids) == 1:
        herb = kb.herbs.get(next(iter(ids))) if hasattr(kb, "herbs") else None
        chinese = getattr(herb, "chinese", "")
        if chinese and chinese != name and all(p.name != chinese for p in out):
            out.append(_Path(chinese, f"{name}即{chinese}", "种子知识库"))
    return out


class _Matcher:
    """The tiers of every (name, herb) pair one check needs, each worked out once."""

    def __init__(self, kb: Any, pack: Any, vocab: _Vocab):
        self.kb, self.pack, self.vocab = kb, pack, vocab
        self.cache: dict[tuple[str, str], tuple[_Tier, _Path, _Path] | None] = {}
        self.herb_paths: dict[str, list[_Path]] = {}
        self.species_cache: dict[str, tuple[tuple[str, ...], str]] = {}
        self.alias_cache: dict[str, list[_Path]] = {}

    def _herb(self, h: str) -> list[_Path]:
        if h in self.herb_paths:
            return self.herb_paths[h]
        paths = _aliases(self.kb, self.pack, h)
        if not _known(self.kb, h, self.vocab.known | _pack_names(self.pack)):
            # a prescribed name no table knows (黄芪颗粒): the names inside it, as near names
            for m in _Reader(h, self.kb, self.vocab).read():
                if m.name != h and not m.written and m.kind == "name" and len(m.name) >= 2:
                    paths.append(_Path(m.name, inner=True))
        self.herb_paths[h] = paths
        return paths

    def _name(self, x: str) -> list[_Path]:
        if x not in self.alias_cache:
            self.alias_cache[x] = _aliases(self.kb, self.pack, x)
        return self.alias_cache[x]

    def match(self, x: str, h: str) -> tuple[_Tier, _Path, _Path] | None:
        key = (x, h)
        if key not in self.cache:
            best = None
            for xp in self._name(x):
                for hp in self._herb(h):
                    tier = _tier(self.kb, self.pack, xp.name, hp.name)
                    if tier is None:
                        continue
                    # the strongest tier; of equal ones the plainest reading (the same
                    # name), then one a source vouches for (the knowledge base, a cited
                    # alias) over one the names alone suggest
                    plain = not (xp.why or hp.why or tier.why)
                    vouched = bool(xp.source or hp.source or tier.source)
                    rank = (tier.rank - (3 if hp.inner else 0), plain, vouched)
                    if best is None or rank > best[0]:
                        best = (rank, tier, xp, hp)
            self.cache[key] = best[1:] if best else None
        return self.cache[key]

    def species(self, h: str) -> tuple[tuple[str, ...], str]:
        if h not in self.species_cache:
            sp, src = _species(self.kb, self.pack, h)
            for p in self._herb(h)[1:]:
                if not sp and not p.inner:
                    sp, src = _species(self.kb, self.pack, p.name)
            self.species_cache[h] = (sp, src)
        return self.species_cache[h]


def _join(*parts: str) -> str:
    return "；".join(p for p in parts if p)


#: A record up to this many characters is quoted whole; a longer one around the name.
_QUOTE_WHOLE = 40
#: Of the places that give one issue, the messages quote this many; the rest are counted.
_SHOWN = 4


def allergy_issues(herbs: Sequence[str], records: Sequence[Any], kb: Any, pack: Any = None
                   ) -> list[tuple[str, str, str, str, tuple[str, ...], str]]:
    """Each prescribed herb against every name the allergy records hold, as
    (id, severity, code, message, herbs, source).

    For a name that stands as a word, is not denied and is not said to be taken, the tier
    (``_tier``) decides: ``stop`` / ``allergy`` when a drug-level table says the record
    names the herb (the same name, the knowledge base, a cited pack alias, the pinyin or
    Latin name, a processed form of one drug), ``block`` / ``allergy_source`` when the two
    only share a source (the materia table's entry, or a species), ``block`` /
    ``allergy_near_name`` when one name is inside the other. The rest are blocks whatever
    the tier, because the record holds the name but may not mean the herb:

    * ``allergy_near_name``: a name inside a longer word the tables do not know (郁金 of
      郁金香, 地黄 of 知柏地黄丸, 桂枝 of 桂枝茯苓丸), and a pinyin or Latin name in an
      English sentence that says nothing of an allergy (it may be a person's name);
    * ``allergy_source``: a species, genus or English common name of the herb's source
      (Panax ginseng, Astragalus, ginger);
    * ``allergy_formula``: a formula the pack or the knowledge base holds, by its name or in
      another product form (四君子汤, 补中益气丸): each herb of its composition, and each
      herb a tier ties to one, asks, because the allergy may be to any of them;
    * ``allergy_denied``: a name the record denies (否认黄芪过敏, 黄芪：无, 黄芪耐受);
    * ``allergy_taken``: a name the record says is taken or eaten, with no reaction
      written (目前在吃人参, 现用药：黄芪): not an allergy, but no proof of none either;
    * ``allergy_homonym``: 花粉, which is pollen in a history and 天花粉's short name in a
      prescription, for 天花粉 only, unless the record denies it.

    A name found is matched to every prescribed herb, not only the first it fits. A record
    that names nothing (无, 否认过敏史, NKDA) adds nothing. Per herb, a stop stands alone;
    otherwise one block per code, each quoting the first places that gave it and counting
    the rest. Each distinct reading is matched once, so the time is linear in the records'
    length.
    """
    vocab = _vocab(kb, pack)
    keys = [(h, _herb_key(h)) for h in herbs]
    keys = [(h, k) for h, k in keys if k]
    matcher = _Matcher(kb, pack, vocab)
    # (herb, code) -> [severity, messages (ordered, unique), places, sources]
    found: dict[str, dict[str, list[Any]]] = {h: {} for h, _ in keys}
    for record in records:
        reader = _Reader(record, kb, vocab)
        if not reader.n:
            continue
        ms = reader.read()
        if not ms:
            continue
        whole = _WS.sub(" ", _visible(str(record or ""))).strip()
        short = len(whole) <= _QUOTE_WHOLE
        # each distinct reading once: its first places are quoted, the rest counted
        groups: dict[tuple, list[Any]] = {}
        for m in ms:
            key = (m.name, m.kind, m.how, m.of, m.species, bool(m.word), m.denied, m.taken,
                   m.context, _ascii_key(m.written)[0] if m.written else "")
            g = groups.setdefault(key, [[], 0])
            if len(g[0]) < _SHOWN:
                g[0].append(m)
            g[1] += 1
        for reps, places in groups.values():
            for h, hk in keys:
                slot = None
                new_msgs = 0
                for m in reps:
                    quote = whole if short else reader.snippet(m.start, m.end)
                    issue = _issue(m, h, hk, quote, matcher)
                    if issue is None:
                        break
                    sev, code, msg, source = issue
                    slot = found[h].setdefault(code, [sev, {}, 0, {}])
                    if msg not in slot[1]:
                        slot[1][msg] = None
                        new_msgs += 1
                    if source:
                        slot[3].setdefault(source, None)
                if slot is not None:
                    # a record short enough to quote whole gives one message per reading,
                    # however often it repeats it; a longer one, one place per occurrence
                    slot[2] += new_msgs if short else places
    out: list[tuple[str, str, str, str, tuple[str, ...], str]] = []
    for h, _ in keys:
        by_code = found[h]
        if any(slot[0] == "stop" for slot in by_code.values()):
            by_code = {c: slot for c, slot in by_code.items() if slot[0] == "stop"}
        for code, (sev, msgs, places, sources) in by_code.items():
            shown = list(msgs)[:_SHOWN]
            rest = max(0, places - len(shown))
            message = "；".join(shown) + (f"；另有{rest}处" if rest else "")
            out.append((f"{code}:{h}", sev, code, message, (h,), "；".join(sources)))
    return out


def _issue(m: Mention, h: str, hk: str, shown: str, matcher: _Matcher
           ) -> tuple[str, str, str, str] | None:
    """The issue the name ``m`` gives the prescribed herb ``h`` (its key ``hk``), if any."""
    prefix = f"过敏史记录“{shown}”：处方含{h}"
    if m.kind == "name" and m.name in _ALLERGY_HOMONYMS:
        if m.denied or m.taken:
            return None                  # a denied pollen allergy names no herb
        hm, nm = _materia(hk), _materia(m.name)
        if (hm is not None and nm is not None and hm.id == nm.id) or m.name in hk:
            where = f"记录中的“{m.word}”含有“{m.name}”的字样；" if m.word else ""
            return ("block", "allergy_homonym",
                    f"{prefix}。{where}过敏史中的“{m.name}”通常指{_ALLERGY_HOMONYMS[m.name]}，"
                    f"但处方中{h}也简称“{m.name}”，请核实所记过敏是否为{h}", "药材名表（materia）")
        return None
    if m.kind in ("species", "genus"):
        sp, src = matcher.species(hk)
        if m.kind == "species":
            shared = [s for s in m.species if s.lower() in {x.lower() for x in sp}]
        else:
            shared = [s for s in sp if s.lower().startswith(m.species[0].lower() + " ")]
        if not shared:
            return None
        what = f"记录中的“{m.written}”是{m.how}" + (f"（{m.species[0]}属）" if m.kind == "genus" else "")
        source = _join(*dict.fromkeys(s for s in (m.of, src) if s))
        tail = f"{h}的来源物种含{'、'.join(shared)}（{src}）"
        if m.denied and m.taken:
            return ("block", "allergy_denied",
                    f"{prefix}。{what}，记录写到它可用、耐受或用后无不适，可能是说对其不过敏；{tail}，"
                    f"这不是过敏记录，也不能据此排除对{h}过敏，请核实并确认无误后再签署", source)
        if m.denied:
            return ("block", "allergy_denied",
                    f"{prefix}。{what}，其所在的分句写有否认的字样，可能是否认对其过敏；{tail}，"
                    "请核实并确认该否认无误后再签署", source)
        if m.taken:
            return ("block", "allergy_taken",
                    f"{prefix}。{what}，记录写到在服用或食用它，所在分句未写过敏或不良反应；{tail}，"
                    f"这不是过敏记录，也不能据此排除对{h}过敏，请核实后再签署", source)
        return ("block", "allergy_source", f"{prefix}。{what}，{tail}，所记过敏未必是对{h}，请核实",
                source)
    if m.kind == "formula":
        f = matcher.vocab.formulas.get(m.written) or next(
            (v for v in matcher.vocab.formulas.values() if v.name == m.of), None)
        if f is None:
            return None
        best = None
        for c in f.herbs:
            hit = matcher.match(_herb_key(c), hk)
            # a near name of a herb of the formula is another drug: only the herb itself, its
            # processed forms and its source-mates ask
            if hit and hit[0].rank >= 2 and (best is None or hit[0].rank > best[1][0].rank):
                best = (c, hit)
        if best is None:
            return None
        c, (tier, xp, hp) = best
        why = _join(xp.why, hp.why, tier.why)
        via = f"其中含{c}" + (f"（{why}）" if c != h and why else "")
        what = (f"记录中的“{m.word or m.written}”" + ("含有" if m.word else "是")
                + f"{f.source}所载方剂{f.name}的名称" + (f"（{f.how}）" if f.how else "")
                + f"，{f.name}的组成为{'、'.join(f.herbs)}")
        source = _join(f"{f.source}方剂{f.name}", xp.source, hp.source, tier.source)
        if m.denied and m.taken:
            return ("block", "allergy_denied",
                    f"{prefix}。{what}，{via}；记录写到该方可用、耐受或用后无不适，可能是说对该方不过敏，"
                    f"这不是过敏记录，也不能据此排除对{h}过敏，请核实并确认无误后再签署", source)
        if m.denied:
            return ("block", "allergy_denied",
                    f"{prefix}。{what}，{via}；记录中该方所在的分句写有否认的字样，可能是否认对该方过敏，"
                    "请核实并确认该否认无误后再签署", source)
        if m.taken:
            return ("block", "allergy_taken",
                    f"{prefix}。{what}，{via}；记录写到在服用该方，所在分句未写过敏或不良反应，这不是过敏记录，"
                    f"也不能据此排除对{h}过敏，请核实后再签署", source)
        return ("block", "allergy_formula",
                f"{prefix}。{what}，{via}；所记过敏可能由方中任一味药引起，未必是{h}，请核实", source)
    hit = matcher.match(m.name, hk)
    if hit is None:
        return None
    tier, xp, hp = hit
    read_as = f"“{m.written}”即{m.name}（{m.how}）" if m.written else ""
    why = _join(read_as, xp.why, hp.why, tier.why)
    source = _join(*dict.fromkeys(s for s in (xp.source, hp.source, tier.source) if s))
    if hp.inner:
        return ("block", "allergy_near_name",
                f"{prefix}。处方中的“{h}”不是名表所载的药名，其中含有“{hp.name}”的字样"
                f"（{why or f'记录中写有{m.name}'}），请核实所记过敏是否与之有关", source)
    if m.word:
        inside = (f"记录中的“{m.word}”含有“{m.name}”的字样，但“{m.word.strip('…')}”不是名表所载的药名，"
                  f"未必是指{m.name}" + (f"（{why}）" if why else ""))
        if m.denied and m.taken:
            return ("block", "allergy_denied",
                    f"{prefix}。{inside}；记录写到它可用、耐受或用后无不适，可能是说对其不过敏，这不是过敏记录，"
                    f"也不能据此排除对{h}过敏，请核实并确认无误后再签署", source)
        if m.denied:
            return ("block", "allergy_denied",
                    f"{prefix}。{inside}；其所在的分句写有否认的字样，可能是否认对其过敏，"
                    "请核实并确认该否认无误后再签署", source)
        if m.taken:
            return ("block", "allergy_taken",
                    f"{prefix}。{inside}；记录写到在服用或使用它，所在分句未写过敏或不良反应，这不是过敏记录，"
                    f"也不能据此排除对{h}过敏，请核实后再签署", source)
        return ("block", "allergy_near_name", f"{prefix}。{inside}，请核实所记过敏是否与{h}有关",
                source)
    if m.denied and m.taken:
        return ("block", "allergy_denied",
                f"{prefix}。记录写到{m.name}可用、耐受或用后无不适，可能是说对{m.name}不过敏"
                + (f"（{why}）" if why else "") + f"；这不是过敏记录，也不能据此排除对{h}过敏，"
                "请核实并确认无误后再签署", source)
    if m.denied:
        return ("block", "allergy_denied",
                f"{prefix}。记录中{m.name}所在的分句写有否认的字样，可能是否认对{m.name}过敏"
                + (f"（{why}）" if why else "") + "，请核实并确认该否认无误后再签署", source)
    if m.taken:
        return ("block", "allergy_taken",
                f"{prefix}。记录写到在服用或食用{m.name}" + (f"（{why}）" if why else "")
                + f"，所在分句未写过敏或不良反应：这不是过敏记录，也不能据此排除对{h}过敏，请核实后再签署",
                source)
    if m.context == "unconfirmed":
        return ("block", "allergy_near_name",
                f"{prefix}。记录中写有“{m.written}”（{why}），但所在分句未写过敏或不良反应，"
                f"可能另有所指（拼音也是人名的写法），请核实所记是否为对{h}过敏", source)
    if m.context == "name_label":
        return ("block", "allergy_near_name",
                f"{prefix}。记录中写有“{m.written}”（{why}），写在“name”一类的标签之后，可能是人名"
                f"（拼音也是人名的写法），请核实所记是否为对{h}过敏", source)
    if tier.rank == 3:
        return ("stop", "allergy", prefix + (f"（{why}）" if why else ""), source)
    if tier.rank == 2:
        return ("block", "allergy_source", f"{prefix}。{why}，请核实", source)
    return ("block", "allergy_near_name", f"{prefix}。{why}，请核实所记过敏为何药", source)


_ALLERGY_WORD = re.compile(r"过敏|致敏|变态反应|allerg")


def allergy_records_in(entries: Iterable[Any]) -> list[str]:
    """The entries of another field (the conditions) that record an allergy: 黄芪过敏 written
    under conditions is read like one written under allergies."""
    return [str(e) for e in entries if _ALLERGY_WORD.search(fold(e))]


def _allergen(entry: str) -> str:
    """The names one record holds, joined with 、, or "" when it names none (无, 否认过敏史,
    NKDA). Kept for the tools that printed it; the check reads every name in its place
    (``mentions``)."""
    return "、".join(dict.fromkeys(m.name for m in mentions(entry)))


def _table_names(kb: Any, pack: Any, herb: str) -> list[str]:
    """The Chinese names the tables give a herb: its own, the pack's cited aliases of it,
    the knowledge base's names of it, and the names the materia table files with it."""
    from ..sources.materia import MATERIA, _NAME_INDEX
    names = [herb]
    aliases = (getattr(pack, "herb_aliases", {}) or {}) if pack is not None else {}
    names += [normalise(fold(a)) for a, (h, _) in aliases.items() if h == herb]
    ids = _identity(kb, herb)
    for e in (*getattr(kb, "herbs", {}).values(), *getattr(kb, "processed", {}).values()):
        if ids and (getattr(e, "herb_id", "") or e.id) in ids:
            names += [normalise(fold(n)) for n in getattr(e, "names", ())]
    drug = _materia_id(herb)
    if drug:
        entry = MATERIA[drug]
        names += [entry.chinese, *entry.aliases]
        names += [n for n, d in _NAME_INDEX.items() if d == drug]
    return list(dict.fromkeys(n for n in names if n and not n.isascii()))


def names_of(herb: str, *, kb: Any = None, pack: Any = None) -> dict[str, list[str]]:
    """Every name the tables give a herb, by kind: "chinese" (its own, the knowledge base's
    names and aliases, the materia table's names, the pack's cited aliases), "latin" (the
    materia table's and the pack's Latin names in both word orders, the knowledge base's
    Latin and pinyin names), "pinyin" (the pack's), "species" (the tables' source species)
    and "common" (the pack's cited English names for one of those species)."""
    from ..sources.materia import MATERIA
    kb = kb if kb is not None else _default_kb()
    out: dict[str, list[str]] = {"chinese": _table_names(kb, pack, herb), "latin": [],
                                 "pinyin": [], "species": [], "common": []}
    drug = _materia_id(herb)
    if drug:
        out["latin"] += _latin_forms(MATERIA[drug].latin)
    if pack is not None and herb in (getattr(pack, "herb_latin", {}) or {}):
        out["latin"] += _latin_forms(pack.herb_latin[herb])
    ids = _identity(kb, herb)
    for e in (*getattr(kb, "herbs", {}).values(), *getattr(kb, "processed", {}).values()):
        if ids and (getattr(e, "herb_id", "") or e.id) in ids:
            out["latin"] += [n for n in getattr(e, "names", ()) if fold(n).isascii()]
    if pack is not None:
        out["pinyin"] += list((getattr(pack, "herb_pinyin", {}) or {}).get(herb, ()))
    species, _ = _species(kb, pack, herb)
    out["species"] += list(species)
    if pack is not None:
        for names, sp, _ in getattr(pack, "herb_common_names", ()) or ():
            if set(sp) & set(species):
                out["common"] += list(names)
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}
