"""Reading an allergy history against the herbs of a prescription (过敏史核查).

A history is not read through a list of wordings (过敏史：X, 对X过敏, X过敏史 …): every wording
such a list missed was a silent pass. The record is normalised and *scanned* for every name
the tables hold, wherever it stands (R0). Whether the entry states an allergy to a name it
holds is then decided by one fixed rule (R1, R2), not by reading the story around the name:
a name in an allergy entry stops its herb unless the entry carries other context and no
un-negated reaction word, and then it blocks with wording that never says or implies that the
record is not an allergy.

Normalising (``fold``)
    NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens, variation
    selectors, fillers, NUL and the other control characters removed; look-alike radicals
    (⻩ for 黄, ⻰ for 龙) read as the characters they stand for; traditional characters
    folded with the materia table's own table (``materia.to_simplified``: 黃耆 → 黄耆, 人參
    → 人参) and, for the characters of every name the tables know and the words a history
    writes around them, with the traditional, Hong Kong, Taiwan and Japanese forms that table
    leaves (過敏 → 过敏, 黑順片 → 黑顺片, 人銜 → 人衔, 当帰 → 当归); tone marks dropped from
    Latin letters (Huáng Qí → huang qi); lower case. One character stays one character.

Finding names (R0, ``_Reader.read``)
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
    per line, 姜 / 黄芪, is 姜 and 黄芪), and with whitespace removed (北 芪 is 北芪). A name found
    only across whitespace is read only where it stands as one word in the second reading, or
    as a processed form, and never across a line break or a tab (柑橘 皮疹 is not 橘皮, 樱桃 /
    花生 on two lines not 桃花), nor where it overlaps a name that stands in the first.

    A name *stands* when what is before and after it is a boundary (the start or end of the
    record, punctuation, a space in the first reading, a digit, Latin letters) or one of the
    words a history writes around a name (对, 患者, 自诉, 因, 的, 吃, 年前, 过敏, 过敏史, 及, 等,
    后, 就, 皮疹 …), or another name of two characters or more that stands so on its far side
    (北芪人参过敏). A formula's name stands only with a boundary of its own before it. A name of
    one character stands only between boundaries of its own from a shorter list of words (对姜
    过敏, 、姜、, 吃姜出现皮疹), never inside a word (面部, 姜某, 枣红色, 归档). A name whose first
    character ends an everyday word (于术 after 对于, 白及 after 蛋白) is inside that word unless
    the everyday word's first character stands on its own (对于术过敏 is 对 + 于术). A name inside
    a longer standing name or formula is that name's (地黄 in 六味地黄丸); a few everyday words
    that hold a name are words of their own (胡萝卜, 桂花). A name that starts with a denial's
    character and runs into what a denial names is the denial (没药物过敏史 is not 没药). A name
    of two characters or more that does not stand is *near*: inside a longer word no table
    knows (郁金香, 知柏地黄丸), unless the longer word is the name processed (炙北芪, 甘草片).

The rule (R1, R2: ``_Rule``)
    For each name standing in an entry (one record as written) and matching a prescribed herb
    at drug level (``_tier`` 3: the same herb, or a processed form by a table), the entry
    *states an allergy* to it (R1, ``allergy``, a stop) when any of these holds:

    a. the entry is just names: a list of names, with or without allergy and class labels
       (过敏史：, Allergy:, 药物：, 中药：, 草药：), list marks (、，,；;/ + 和 及 与 或 and or, line
       breaks, tabs, a space next to a Chinese character or next to a name: 黄芪 青霉素,
       penicillin huang qi; an ASCII full stop with a space after it: huang qi. penicillin; a
       dash or a middle dot between two words: 黄芪-青霉素, 黄芪·青霉素), list numbers (1. 2. #1,
       ① ②, (1), 一、 二、, （一）) and 等. An item that holds a name holds nothing else, so any other
       word next to a name (对黄芪, 黄芪（-）) is context. The tables hold no other allergen's
       name, so an item that holds none of theirs is another entry of the list (青霉素,
       penicillin, 对乙酰氨基酚, 无花果, Tylenol with codeine) unless it is words: it holds one of
       the context words of R2 and R6 as a whole word (``_words``: a negator or none word, not
       asked or not known, use and keeping, tolerance, improvement and treatment, a person:
       黄芪；无, 黄芪，服用中, 黄芪，家里有, 黄芪，医生开的, Dr. Huang Jing). A Chinese word of one
       character counts only standing as a word of its own (无, 不吃; not the 无 of 无花果), an
       English one only as a whole token, and no item is words by its first or last character
       or by a small word of English (with, in, a). Any other item is read as another name, the
       fail-safe side;
    b. an allergy noun that no negator stands immediately before stands in the name's
       statement (``_ALLERGY_NOUN``, ``_ALLERGY_NOUNS_CJK``: X allergy, allergy to X, Allergies:
       X, Allergies include X, X severe allergy, 致敏原：X); a statement ends at a full stop, a
       semicolon, a question or exclamation mark, a line break or a tab. Or the name is what an
       exception excepts (``_Rule.excepted``: No allergies except X, NKDA except X, no allergy
       except to X, 除X外无过敏, 无，除了X, 无过敏史，X除外, 无过敏（X除外）), which states an allergy
       to it. The statements a reaction word makes (对X过敏, X过敏史, X不耐受, allergic to X) are
       c's;
    c. the entry holds a reaction word anywhere (``_REACTION_CJK``, ``_REACTION_ASCII``:
       过敏, 过敏反应, 不良反应, 皮疹, 红疹, 疹子, 起疹, 荨麻疹, 风团, 瘙痒, 痒, 红肿, 肿胀, 水肿,
       喉头水肿, 呼吸困难, 哮喘发作, 休克, 不耐受, rash, hives, urticaria, itch, swelling,
       angioedema, anaphylaxis, reaction, allergic, intolerance) that is not negated. A
       reaction word is negated only when a negator (无, 未, 未见, 未出现, 未再出现, 没有, 没, 不,
       否认, 无明显, no, without, denies, denied, never) stands immediately before it in its
       clause, with nothing between but the names it negates, joined as a list (否认黄芪过敏,
       无黄芪过敏史, 否认黄芪、人参过敏, no huang qi allergy). 对, a class word or any other word
       between leaves it un-negated (否认对黄芪过敏, 无药物过敏史, 无任何皮疹, no known drug
       reaction, no allergic reaction: no negates allergic, not reaction). Only that occurrence
       is negated: 服用黄芪后无不适，次日出现皮疹 holds an un-negated 皮疹. A clause ends at a
       punctuation mark other than 、 and /, a line break or a tab, and a space between two
       Chinese characters. A reaction word inside a name at the extent the tables give it is
       the name's; one before a name that stands inside a longer word is not (皮疹黄芪). The
       过敏 of a label (过敏史：) and of a condition's name (过敏性鼻炎) is a reaction word like any
       other.

    Otherwise the entry only *mentions* the herb (R2, ``allergy_mentioned``, a block): it has
    words around the name and no un-negated reaction word (否认黄芪过敏, 无黄芪过敏史, 黄芪（-）,
    黄芪皮试阴性, 对黄芪不过敏, 服用黄芪无不适, 黄芪耐受良好, 目前在吃黄芪, 服用黄芪后咳嗽好转, Dr.
    Huang Jing). Its message says the record writes the name and does not say whether it is an
    allergy, and asks the practitioner to check before signing; it never says the record is
    not an allergy, or that the herb is tolerated.

    The rule reads no story: what was taken, tolerated, improved or treated, and what a
    reaction was caused by, are not read. Its costs are known and are the fail-safe choice: a
    record that mentions a herb next to any un-negated reaction word stops it (目前在吃人参，
    青霉素过敏; 过敏史：无，目前在吃人参; 无药物过敏史，目前在吃人参; 过敏性鼻炎，服用黄芪后好转),
    and one that mentions it with any other word blocks it (对黄芪).

Other names (R3, R4)
    A name the materia table files with the herb's entry, or that shares its species, and a
    species, genus or English common name of its source, give ``allergy_source`` (a block
    that says where the table files it), whatever the context words. One name inside the
    other (白附子 and 附子) gives ``allergy_near_name``, as does a near name. A formula's name
    is read by R1 and R2 like any name: under R1 each herb of it gives ``allergy_formula`` (the
    allergy may be to any herb of it), under R2 ``allergy_mentioned`` naming the formula; it
    never stops by itself. 花粉, which is pollen in a history and 天花粉's short name in a
    prescription, gives ``allergy_homonym`` for 天花粉 under R1 and R2 alike, and nothing only
    where the record writes one of the rule's explicit denials of it (否认花粉过敏, 无花粉过敏史,
    对花粉不过敏, 花粉（-）, 花粉皮试阴性: ``_Rule.denied``).

The reading is linear in the length of the record, and each message quotes a bounded excerpt
of it.
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

ALLERGY_CODES = ("allergy", "allergy_mentioned", "allergy_source", "allergy_near_name",
                 "allergy_formula", "allergy_homonym")


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
    "稱為係個這來時間週歲幾樣麼說話請際況們嗎報導誘數較極頻復敘脹"
    "順銜帰薬竜艸蔴実呉沢黒亀剤経続歯斉"
    "爲買親衝則剛別匱醫喫聽團塊處爾師態換據湯潔漿濃塗滿燉燒愛著終給櫱慮見觸計讓講許論證該説諸適長悶聞靜顆飲餅鱖",
    "过对认确历应严异体质详从后现痒肿难诉经类剂种与阴录记变显状呕临诊检验试荨晕气热属仅虽却并还"
    "称为系个这来时间周岁几样么说话请际况们吗报导诱数较极频复叙胀"
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
汤剂 煎剂 煎液 饮片 药汁 治疗 治 调理
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
#: character stands as a word of a history on its own, the name stands on its right, and the
#: name's last character does not start an everyday word of its own there (``_EVERYDAY_NEXT``):
#: 对于术过敏, 对于术及青霉素过敏 and 对于术有过敏史 are 对 + 于术; 对于术后 and 由于术中 are 对于 +
#: 术后. Where the everyday word's first character ends a word of use (服用于术, 使用于术), the
#: everyday word is not there at all: 服用 + 于术.
_EVERYDAY = frozenset("""
对于 关于 由于 至于 属于 位于 等于 用于 大于 小于 基于 在于 便于 易于 过于 终于 处于
蛋白 空白 苍白 明白 坦白 表白 洁白 雪白 黑白 灰白 清白
""".split())
_EVERYDAY_BROKEN = ("服用", "使用", "食用", "饮用", "外用")
#: Words that start with the last character of a name after an everyday word: 术后 (after an
#: operation), so 对于术后 is 对于 + 术后.
_EVERYDAY_NEXT = frozenset("术后 术中 术前 术者 术式 术区 术野 术侧 术毕 术晨 术日".split())

#: The first characters of a denial that a name may start with (没药, 无食子): such a name that
#: runs into what a denial names (没药物过敏史, 无食物过敏) is the denial, not the name.
_DENIAL_HEADS = ("没有", "没", "无", "未")
#: Words a denial's clause names after it (无药物过敏史, 没食物过敏).
_DENIED_OBJECTS = ("药物", "药品", "食物", "过敏", "其他", "其它", "特殊", "明显",
                   "已知", "任何", "发现")


# ============================================================== the rule's words
#: Reaction words (R1c), as the rule lists them, matched in the record with whitespace dropped.
#: A longer one is read before a shorter one at the same place (过敏反应 before 过敏).
_REACTION_CJK = re.compile(
    "过敏反应|不良反应|喉头水肿|呼吸困难|哮喘发作|荨麻疹|过敏|皮疹|红疹|疹子|起疹|风团|瘙痒|红肿|肿胀|水肿"
    "|休克|不耐受|痒")
#: English reaction words: a word that starts with one of these (rashes, hives, itching).
_REACTION_ASCII = ("rash", "hive", "urticari", "itch", "swell", "swollen", "angioedem", "anaphyla",
                   "reaction", "allergic", "intoleran")
#: Negators (R1c), longest first: one of these immediately before a reaction word negates it.
_NEGATORS_CJK = ("未再出现", "未出现", "无明显", "未见", "没有", "否认", "无", "未", "没", "不")
_NEGATORS_ASCII = frozenset({"no", "without", "denies", "denied", "never"})
#: What joins the names a negator negates (否认黄芪、人参过敏, no huang qi or ren shen allergy):
#: only names, joined by these, may stand between a negator and the word it negates. Nothing
#: else may: 无药物过敏史, 否认对黄芪过敏, 无任何皮疹 and no known drug reaction leave the
#: reaction word un-negated.
_NAME_JOINERS_CJK = ("以及", "、", "/", "／", "和", "及", "与", "或", "跟")
_NAME_JOINERS_ASCII = frozenset({"and", "or"})
#: Marks inside a clause: a list goes on after 、 and /; an apostrophe or a hyphen is inside a
#: word. Every other mark, a line break, a tab and a space between two Chinese characters end
#: the clause.
_IN_CLAUSE = frozenset("、/／·'’-–")
#: The nouns of an allergy statement (R1b), which are not reaction words: the English noun
#: (huang qi allergy, allergy to huang qi, Allergies: huang qi, Allergies include huang qi) and
#: the Chinese labels without 过敏 (致敏原：黄芪). The adjective (allergic) and every word with 过敏
#: are reaction words (R1c).
_ALLERGY_NOUN = re.compile(r"allerg(?:y|ies|en|ens)")
_ALLERGY_NOUNS_CJK = ("致敏药物", "致敏原", "致敏物", "变应原", "药敏史", "药敏")
_LABEL_COLONS = (":", "：", "∶", "︰")
#: R1b: what ends a statement: a full stop, a semicolon, a question or exclamation mark, a
#: line break or a tab. A comma, a colon or a dash does not (Allergies: penicillin, huang qi;
#: huang qi - allergy), and neither does the full stop of an item's number (Allergies: 1. huang
#: qi).
_STATEMENT_ENDS = frozenset("。；;！!？?")
#: R1b: the characters that make a verb of a 除 after them (排除, 解除, 去除, 消除, 切除 …), which
#: excepts nothing: 已排除黄芪 is no exception.
_NOT_EXCEPTING = frozenset("排解去清消切免祛剔删根拆废扣摘割铲破驱革戒开撤")

#: R1a: what ends a list item. A space next to a Chinese character or next to a name, a line
#: break and a tab end one too (黄芪 青霉素, penicillin huang qi); a space between two other
#: Latin words does not. So do an ASCII full stop with whitespace after it (huang qi. penicillin,
#: Dr. Huang Jing; not an item's number, 1. huang qi), a dash or a middle dot between two words
#: (黄芪-青霉素, 黄芪·青霉素; not 黄芪（-）), an item's number in brackets or after # ((1)黄芪(2)人参,
#: （一）黄芪（二）人参, 黄芪-#2 青霉素) and a circled or other enclosed number (①黄芪②人参, ⑴黄芪), which
#: NFKC writes as a digit.
_ITEM_MARKS = frozenset("、，,；;/／|｜。？?！!&+")
_ITEM_DASHES = frozenset("-–—−·")
_ITEM_NUMBER = re.compile(r"[(（](?:[0-9]{1,3}|[一二三四五六七八九十]{1,3})[)）]|#[0-9]{1,3}(?![0-9])")
_ENCLOSED_NUMBER = re.compile(
    "[①-⒛⓪-⓿❶-➓㈠-㈩㉈-㉏㉑-㉟㊀-㊉"
    "㊱-㊿\U0001f100-\U0001f10c]")
_ITEM_WORDS_CJK = ("以及", "和", "及", "与", "或", "跟")
_ITEM_WORDS_ASCII = frozenset({"and", "or", "plus"})
#: R1a: words that are not context in an item that holds a name: allergy labels, the words
#: that name a class (药物：黄芪), 等, the reaction words themselves (R1c reads them), and an
#: item's number (1. 黄芪).
_TRANSPARENT = re.compile(
    r"(?:既往|已知|明确|药物|食物|中药|西药|其他|其它){0,3}(?:过敏史|过敏原|过敏源|过敏物质|过敏物|过敏药物|过敏药品"
    r"|过敏食物|过敏情况|过敏反应|过敏|药敏史|药敏|致敏药物|致敏原|致敏物|变应原|变态反应)"
    r"|中成药|中草药|草药|药物|药品|食物|食品|中药|西药|名称|等等|之类|一类|等|类|均|都|也|皆"
    r"|" + _REACTION_CJK.pattern +
    r"|(?<![a-z0-9])(?:allerg[a-z]*|known|drugs?|foods?|medications?|meds?|medicines?|hx|history"
    r"|list|status|names?|substances?|etc|intoleran[a-z]*|reactions?|rash[a-z]*|hives?|urticari[a-z]*|itch[a-z]*"
    r"|swell[a-z]*|swollen|angioedem[a-z]*|anaphyla[a-z]*)(?![a-z0-9])")
_NUMBERING = re.compile(r"^\s*\d{1,3}(?![0-9])|(?<![0-9a-z.])\d{1,3}\s*[.．)）](?![0-9])"
                        r"|[(（]\d{1,3}[)）]|#\s*\d{1,3}(?![0-9])"
                        # a Chinese ordinal: 一、黄芪 二、人参, 一）黄芪, （一）黄芪
                        r"|^\s*[一二三四五六七八九十]{1,3}\s*(?:[.．)）]|$)|[(（][一二三四五六七八九十]{1,3}[)）]")
#: R1a: a negative mark is a word: 黄芪（-）, 黄芪(-), 黄芪：-.
_NEGATIVE_MARK = re.compile(r"[(（\[【]\s*[-－−]\s*[)）\]】]|[:：∶]\s*[-－−]\s*$")
#: R1a: an item of a list that holds no name the tables know is another entry of the list (青霉素,
#: penicillin, 对乙酰氨基酚, 无花果, Tylenol with codeine), unless it is words, not a name: it holds
#: one of the context words of R2 and R6 (``_CONTEXT_CJK``, ``_CONTEXT_ONE``, ``_CONTEXT_ASCII``)
#: as a whole word. The tables hold no other allergen's name, so nothing else tells words from a
#: name, and no part of an item is read as a word by its first or last character. The words are
#: of the kinds R2 and R6 name: a negator or none word, not asked or not known, use and keeping
#: (who gave it, where it is kept), tolerance, improvement and treatment, and the words for a
#: person (黄芪；无, 黄芪，服用中, 黄芪，家里有, 黄芪，医生开的, 黄芪，平时煲汤, 黄芪，好转, huang qi,
#: takes daily, Dr. Huang Jing). A Chinese word of two characters or more is read where the item
#: holds it; one of one character (无, 不, 吃 …) only as a word of its own: between the item's ends,
#: marks, spaces or Latin letters, or next to another context word (无, 不吃, 未用), never inside
#: another word (无花果, 不锈钢). An English one is a whole token (no, takes, dr), never a part of
#: one, and the small words of English (with, in, a, an, the, for) are none of them. Any other
#: item is read as another name: fail-safe, since the herb's name in a list stops it.
_CONTEXT_CJK = re.compile(
    # none and negation (R1c's negators, R6's none words), not asked or not known (R6)
    r"未再出现|未出现|无明显|未见|没有|否认|阴性|未发现|排除|除外|正常|询问|未问|没问|漏问|不详|不清楚|不知道"
    r"|未知|待查|拒答|拒绝"
    # use and keeping (R2: 服用X, 目前在吃X, X，家里有, X，平时煲汤)
    r"|服用|服药|使用|用过|吃过|服过|喝过|用了|吃了|服了|喝了|用后|在用|在吃|在服|正在|目前|现在|平时|平素|长期|每日"
    r"|每天|经常|常年|常备|家里|家中"
    r"|煲汤|炖汤|熬汤|泡水|泡茶|煮水"
    # a person who says it or gave it (X，医生开的, X，妈妈说的; R2's Dr. Huang Jing)
    r"|患者|病人|本人|家属|家人|母亲|父亲|妈妈|爸爸|医生|医师|大夫|护士|自诉|自述|代诉"
    # tolerance, improvement and treatment (R2: 无不适, 耐受良好, 好转, 治疗)
    r"|耐受|可以|可用|可吃|可服|能吃|能用|能服|良好|不适|问题|好转|缓解|改善|减轻|消退|消失|痊愈|有效|见效|见好"
    r"|治疗|主治|调理|进补")
_CONTEXT_ONE = frozenset("无未没不否拒服用吃喝泡炖煲煮熬")
_CONTEXT_ASCII = re.compile(
    r"(?<![a-z0-9])(?:no|not|none|nil|nkda|nka|nkfa|n/?a|negative|neg|denies|denied|deny|without|never"
    r"|except|unknown|unsure|deferred|asked|see|chart|notes?|takes?|taking|took|taken|using|uses?|used"
    r"|on|currently|daily|tolera[a-z]*|ok|okay|fine|improved|better|resolved|treat[a-z]*"
    r"|can'?t|cannot|reports?|reported|recorded|patient|pt|family|mother|father|mom|dad|son|daughter"
    r"|wife|husband|doctor|dr|nurse)(?![a-z0-9])")


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
    """A name found in a record, and what the record says of it by the rule.

    ``name`` is the name as the tables write it (the Chinese name a Latin or pinyin name is
    of, the formula a product name is of, the species a species name or common name is of);
    ``written`` the record's own text when it differs (huangqi, astragali radix, 补中益气丸).
    ``word`` is the longer word the name stands inside ("" when it stands): 郁金香 for 郁金.
    ``kind`` is "name", "formula" (``of`` is the formula), "species" or "genus" (``species``
    holds them). ``allergy`` says the record states an allergy to it (R1), and ``why`` which
    clause of R1 holds: "list" (the record is just names), "reaction" (an un-negated reaction
    word) or "statement" (an un-negated allergy statement about it); "" when it only
    mentions the name (R2). ``denied`` says, under R2, that the record writes one of the
    explicit denials of the rule about it (否认X过敏, 无X过敏史, 对X不过敏, X（-）, X皮试阴性, no
    X allergy); only the 花粉 homonym reads it. ``start`` and ``end`` are where the name is in
    the record with whitespace dropped (``fold``, then whitespace removed), and ``span`` the
    longer word for a name inside one, else (start, end).
    """

    name: str
    start: int
    end: int
    written: str = ""
    how: str = ""
    word: str = ""
    kind: str = "name"
    of: str = ""
    species: tuple[str, ...] = ()
    allergy: bool = False
    why: str = ""
    span: tuple[int, int] = ()
    denied: bool = False


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


#: The longest excerpt of a word a name stands inside that a message quotes, on each side.
_WORD_REACH = 6


class _Reader:
    """One record, read: the names it holds, each in its place (R0), and what the record says
    of each by the rule (``_Rule``; see the module doc)."""

    def __init__(self, record: Any, kb: Any, vocab: _Vocab):
        self.kb, self.vocab = kb, vocab
        self.record = record
        self.shown = _shown_text(record)
        self._raw: tuple[str, list[int]] | None = None
        self.f = _fold_shown(self.shown)              # folded, whitespace kept
        self.t, self.gaps, self.keep, self.breaks = _compact(self.f)
        self.n = len(self.t)
        self.cuts = sorted(self.gaps)
        self.break_list = sorted(self.breaks)
        self._dl: dict[tuple[int, bool, bool], bool] = {}
        self._dr: dict[tuple[int, bool, bool], bool] = {}

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

    def broken(self, s: int, e: int) -> bool:
        """A line break or a tab stands before some position from ``s`` to ``e``: whitespace
        that ends a line or a field, which no name crosses."""
        k = bisect_left(self.break_list, s)
        return k < len(self.break_list) and self.break_list[k] <= e

    def everyday(self, p: int) -> bool:
        """An everyday word (``_EVERYDAY``) holds the characters on both sides of ``p``: 对于,
        蛋白. Not where its first character ends a word of use (服用于术, 使用于术)."""
        t = self.t
        return (0 < p < self.n and t[p - 1:p + 1] in _EVERYDAY
                and not (p >= 2 and t[p - 2:p] in _EVERYDAY_BROKEN))

    # ---------------------------------------------------------------- boundaries
    def dl(self, p: int, spaced: bool, one: bool = False) -> bool:
        """A boundary of its own before position ``p``. ``spaced`` is the first reading, in
        which whitespace is one; ``one`` asks it for a name of one character."""
        key = (p, spaced, one)
        if key not in self._dl:
            t = self.t
            if p <= 0:
                v = True
            elif self.everyday(p):
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
            if not self.everyday(p):
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
            if kind == "name" and self.everyday(s):
                # 对于术过敏, 对于术及青霉素过敏: the 于 of 对于 may start 于术 after all, where 对
                # stands as a word, the name stands on its right, and its last character does not
                # start an everyday word there (对于术后 is 对于 + 术后)
                return (t[s - 1] in _BEFORE and self.dl(s - 1, spaced) and rx[e]
                        and t[e - 1:e + 1] not in _EVERYDAY_NEXT)
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
                # a name across whitespace is read only where it stands as one word (北 芪过敏),
                # or as a processed form (炙 黄芪), never across a line break or a tab: 柑橘 皮疹
                # is not 橘皮, 花生 地塞米松 is not 生地, 樱桃\n花生 is not 桃花
                if (self.broken(s + 1, e - 1) or cover_b.covers(s, e) or stand2.any(s, e)
                        or (not standing[x] and (stand1.any(s, e) or kind != "name"
                                                 or self._processed_word(s, e, False) is None))):
                    continue
            elif not standing[x] and cover_b.covers(s, e):
                continue
            keep_x.append(x)
        # 4. a name that starts with a denial's character and runs into what a denial
        # names is the denial (没药物过敏史, 无食物过敏)
        dropped: set[int] = set()
        for x in keep_x:
            s, e, kind = cands[x]
            if kind == "name" and not crossing[x] and not ra.get(e, False):
                head = next((h for h in _DENIAL_HEADS if len(h) < e - s and t.startswith(h, s)), "")
                if head and t.startswith(_DENIED_OBJECTS, s + len(head)):
                    dropped.add(x)
        keep_x = [x for x in keep_x if x not in dropped]
        # 5. each name in its place: standing, processed, or inside a longer word
        found: list[tuple[str, str, int, int, str, tuple[int, int]]] = []
        for x in keep_x:
            s, e, kind = cands[x]
            name = t[s:e]
            spaced = not crossing[x]
            if standing[x]:
                found.append((kind, name, s, e, "", (s, e)))
                continue
            if e - s == 1 and kind == "name":
                continue                  # a name of one character inside a word is no name
            if kind == "name":
                whole = self._processed_word(s, e, spaced)
                if whole is not None:
                    a, b = whole
                    found.append(("name", t[a:b], a, b, "", (a, b)))
                    continue
            a, b = self._word(s, e, spaced)
            word = (("…" if not self._left(a, spaced) else "") + self.text(a, b)
                    + ("…" if not self._right(b, spaced) else ""))
            found.append((kind, name, s, e, word, (a, b)))
        toks = _tokens(t, self.gaps)
        latin = _ascii_spans(t, toks, vocab)
        # 6. what the record says of each (R1, R2). The rule reads each name at the extent the
        # tables give it (s, e); only R1a's list items take a near name's longer word as one
        # item, since that word is a name too (郁金香)
        own = [(s, e) for _, _, s, e, _, _ in found] + [(s, e) for s, e, _ in latin]
        words = [cover for *_, cover in found] + [(s, e) for s, e, _ in latin]
        rule = _Rule(self, toks, own, words)
        out: list[Mention] = []
        for kind, name, s, e, word, (a, b) in found:
            why = rule.reading(s, e)
            denied = not why and name in _ALLERGY_HOMONYMS and rule.denied(s, e)
            if kind == "formula":
                f = vocab.formulas[name]
                out.append(Mention(f.name, s, e, written=name, how=f.how, kind="formula", of=f.name,
                                   word=word, allergy=bool(why), why=why, span=(a, b),
                                   denied=denied))
            else:
                out.append(Mention(name, s, e, word=word, allergy=bool(why), why=why, span=(a, b),
                                   denied=denied))
        for s, e, entry in latin:
            why = rule.reading(s, e)
            written = self.text(s, e)
            if entry.kind == "name":
                out.append(Mention(entry.target[0], s, e, written=written, how=entry.how,
                                   allergy=bool(why), why=why, span=(s, e)))
            else:
                out.append(Mention(written, s, e, written=written, how=entry.how,
                                   kind=entry.kind, species=entry.target, of=entry.source,
                                   allergy=bool(why), why=why, span=(s, e)))
        return sorted(out, key=lambda m: (m.start, m.end))

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


class _Rule:
    """The rule (R1, R2) over one record whose names are found: whether it is just names
    (R1a), whether it holds an un-negated reaction word (R1c), and whether an un-negated
    allergy noun stands in a name's statement (R1b). ``own`` are the names at the extent the
    tables give them, which a negator may negate and which mask the reaction words inside them;
    ``words`` the same with a near name's longer word, which R1a reads as one item. Every walk
    over the record is remembered, so the whole is linear in its length."""

    def __init__(self, reader: _Reader, toks: Sequence[tuple[int, int]],
                 own: Sequence[tuple[int, int]], words: Sequence[tuple[int, int]]):
        self.reader = reader
        self.t, self.n = reader.t, reader.n
        self.gaps, self.breaks = reader.gaps, reader.breaks
        self.toks = toks
        self.tok_at = {s: e for s, e in toks}
        self.tok_end = {e: s for s, e in toks}
        self.name_end: dict[int, int] = {}         # end -> the earliest start of a name ending there
        self.name_start: dict[int, int] = {}       # start -> the latest end of a name starting there
        for s, e in own:
            if s < self.name_end.get(e, e):
                self.name_end[e] = s
            if e > self.name_start.get(s, s):
                self.name_start[s] = e
        self.own = _Marks(self.n, own)
        self.covered = _Marks(self.n, words)
        self.inner = _Marks(self.n, [(s + 1, e) for s, e in words if e - s >= 2])
        self._neg: dict[int, bool] = {}
        self._past: dict[int, bool] = {}
        self._fwd: dict[int, int] = {}
        self._back: dict[int, int] = {}
        self._list: bool | None = None
        self._reaction: bool | None = None
        self._stated: tuple[list[int], list[bool]] | None = None

    def reading(self, s: int, e: int) -> str:
        """Which clause of R1 holds for the name at ``s``–``e``, or "" (R2)."""
        if self.just_names():
            return "list"
        if self.reaction():
            return "reaction"
        return "statement" if self.statement(s, e) or self.excepted(s, e) else ""

    # ---------------------------------------------------------------- negation (R1c)
    def _boundary(self, q: int) -> bool:
        """A clause starts at ``q``: the record's start, a line break or a tab, a space between
        two Chinese characters, or a mark other than those inside a clause."""
        if q <= 0 or q in self.breaks:
            return True
        t = self.t
        if q in self.gaps and q < self.n and _cjk(t[q - 1]) and _cjk(t[q]):
            return True
        c = t[q - 1]
        return not (c.isalnum() or _cjk(c) or c in _IN_CLAUSE)

    def _negator(self, q: int) -> bool:
        """A negator of the rule ends at ``q``."""
        t = self.t
        if any(q >= len(w) and t.startswith(w, q - len(w)) for w in _NEGATORS_CJK):
            return True
        s = self.tok_end.get(q)
        return s is not None and t[s:q] in _NEGATORS_ASCII

    def _joiner_before(self, q: int) -> int:
        """The length of a word that joins two names, ending at ``q`` (、, 和, and), or 0."""
        t = self.t
        for w in _NAME_JOINERS_CJK:
            if q >= len(w) and t.startswith(w, q - len(w)):
                return len(w)
        s = self.tok_end.get(q)
        return q - s if s is not None and t[s:q] in _NAME_JOINERS_ASCII else 0

    def _joiner_after(self, q: int) -> int:
        t = self.t
        for w in _NAME_JOINERS_CJK:
            if t.startswith(w, q):
                return len(w)
        e = self.tok_at.get(q)
        return e - q if e is not None and t[q:e] in _NAME_JOINERS_ASCII else 0

    def negated(self, p: int) -> bool:
        """A negator stands immediately before position ``p`` in its clause. Only the names it
        negates, joined by 、 / 和 及 与 或 and or, may stand between (否认黄芪过敏, 无黄芪过敏史,
        否认黄芪、人参过敏, no huang qi allergy); nothing else may (无药物过敏史, 否认对黄芪过敏, 无任何
        皮疹, 未见明显皮疹, no known drug reaction: not negated)."""
        if p not in self._neg:
            out = False
            if not self._boundary(p):
                if self._negator(p):
                    out = True
                else:
                    s = self.name_end.get(p)
                    if s is not None and s < p:
                        out = self._past_names(s)
            self._neg[p] = out
        return self._neg[p]

    def _past_names(self, q: int) -> bool:
        """From ``q``, where a name starts, back over the names joined to it: a negator right
        before them."""
        path: list[int] = []
        out = False
        while True:
            if q in self._past:
                out = self._past[q]
                break
            path.append(q)
            if self._boundary(q):
                break
            if self._negator(q):
                out = True
                break
            j = self._joiner_before(q)
            q2 = q - j
            s = self.name_end.get(q2) if j else None
            if s is None or s >= q2 or self._boundary(q2):
                break
            q = s
        for x in path:
            self._past[x] = out
        return out

    def reaction(self) -> bool:
        """R1c: the record holds a reaction word that is not negated. A reaction word inside a
        name the tables give (at the tables' extent) is the name's."""
        if self._reaction is None:
            self._reaction = self._find_reaction()
        return self._reaction

    def _find_reaction(self) -> bool:
        t = self.t
        for m in _REACTION_CJK.finditer(t):
            a, b = m.span()
            if not self.own.all(a, b) and not self.negated(a):
                return True
        for s, e in self.toks:
            if t[s:e].startswith(_REACTION_ASCII) and not self.own.all(s, e) \
                    and not self.negated(s):
                return True
        return False

    # ---------------------------------------------------------------- R1b
    def _statements(self) -> tuple[list[int], list[bool]]:
        """Where each statement starts, and whether it holds an allergy noun no negator stands
        immediately before (allergy, allergies, allergen; 致敏原, 变应原, 药敏 …)."""
        if self._stated is None:
            t, n = self.t, self.n
            starts = [0]
            for p in range(1, n):
                c = t[p - 1]
                if (p in self.breaks or c in _STATEMENT_ENDS
                        or (c in ".．" and not (p >= 2 and t[p - 2].isdigit()))):
                    starts.append(p)
            stated = [False] * len(starts)
            nouns = [(s, e) for s, e in self.toks if _ALLERGY_NOUN.fullmatch(t, s, e)]
            for w in _ALLERGY_NOUNS_CJK:
                # a label (致敏原：黄芪), not the word in a sentence (药敏试验阴性)
                at = t.find(w)
                while at >= 0:
                    if t.startswith(_LABEL_COLONS, at + len(w)):
                        nouns.append((at, at + len(w)))
                    at = t.find(w, at + len(w))
            for a, b in nouns:
                k = bisect_right(starts, a) - 1
                if not stated[k] and not self.own.all(a, b) and not self.negated(a):
                    stated[k] = True
            self._stated = (starts, stated)
        return self._stated

    def statement(self, s: int, e: int) -> bool:
        """R1b: an allergy noun no negator stands immediately before, in the name's statement:
        huang qi allergy, allergy to huang qi, Allergies: huang qi, Allergies include huang qi,
        huang qi severe allergy, Allergies reviewed: huang qi, 致敏原：黄芪. A statement ends at a
        full stop, a semicolon, a question or exclamation mark, a line break or a tab."""
        starts, stated = self._statements()
        return stated[bisect_right(starts, s) - 1]

    def excepted(self, s: int, e: int) -> bool:
        """R1b: the name is what an exception excepts, which states an allergy to it: No
        allergies except X, NKDA except X, no allergy except to X, 除X外无过敏, 无，除了X, 无过敏史，
        X除外, 无过敏（X除外）. The exception word stands right before the name (except, except to,
        except for, 除, 除了; not the 除 of 排除 or 解除) or right after it (除外, 以外, 之外, 例外),
        with only the names it excepts, joined as a list, between."""
        t = self.t
        b = self._backward(s)
        if (b >= 2 and t.startswith("除了", b - 2)) or (
                b >= 1 and t[b - 1] == "除" and not (b >= 2 and t[b - 2] in _NOT_EXCEPTING)):
            return True
        a = self.tok_end.get(b)
        if a is not None and t[a:b] in ("to", "for"):
            a, b = self.tok_end.get(a), a
        if a is not None and t[a:b] == "except":
            return True
        return t.startswith(("除外", "以外", "之外", "例外"), self._forward(e))

    # ---------------------------------------------------------------- the 花粉 denial
    def _forward(self, q: int) -> int:
        """Past the names joined to the one that ends at ``q``."""
        path: list[int] = []
        while True:
            if q in self._fwd:
                q = self._fwd[q]
                break
            path.append(q)
            j = self._joiner_after(q)
            e = self.name_start.get(q + j) if j else None
            if e is None or e <= q + j:
                break
            q = e
        for x in path:
            self._fwd[x] = q
        return q

    def _backward(self, q: int) -> int:
        """Back past the names joined to the one that starts at ``q``."""
        path: list[int] = []
        while True:
            if q in self._back:
                q = self._back[q]
                break
            path.append(q)
            j = self._joiner_before(q)
            s = self.name_end.get(q - j) if j else None
            if s is None or s >= q - j:
                break
            q = s
        for x in path:
            self._back[x] = q
        return q

    def _allergy_word(self, q: int) -> bool:
        """过敏, or an English allergy word (allergy, allergic), starts at ``q``."""
        t = self.t
        if t.startswith("过敏", q):
            return True
        e = self.tok_at.get(q)
        return e is not None and (_ALLERGY_NOUN.fullmatch(t, q, e) is not None or t[q:e] == "allergic")

    def denied(self, s: int, e: int) -> bool:
        """The record writes one of the rule's explicit denials about the name at ``s``–``e``
        (R2: 否认X过敏, 无X过敏史, 对X不过敏, X（-）, X皮试阴性; no X allergy, no allergy to X). Only
        these are denials: 对X, X，未再出现皮疹, X without rash and 服用X are not."""
        t = self.t
        q = self._forward(e)
        if not self._boundary(q):
            # 否认X过敏, 无X过敏史, denies X allergy: the allergy word right after the names,
            # negated through them
            if self._allergy_word(q) and self.negated(q):
                return True
            # 对X不过敏, X无过敏: a negator right after the names, then the allergy word
            for w in _NEGATORS_CJK:
                if t.startswith(w, q) and self._allergy_word(q + len(w)):
                    return True
            end = self.tok_at.get(q)
            if end is not None and t[q:end] in _NEGATORS_ASCII and self._allergy_word(end):
                return True
            # X（-）, X：-, X阴性, X皮试阴性, X negative
            if _DENIED_MARK.match(t, q):
                return True
        # no allergy to X, denies allergies to X
        b = self._backward(s)
        to = self.tok_end.get(b)
        if to is not None and t[to:b] in ("to", "of"):
            noun = self.tok_end.get(to)
            if noun is not None and _ALLERGY_NOUN.fullmatch(t, noun, to) and self.negated(noun):
                return True
        return False

    # ---------------------------------------------------------------- R1a
    def just_names(self) -> bool:
        if self._list is None:
            self._list = self._just_names()
        return self._list

    def _just_names(self) -> bool:
        """R1a: every item of the record's list is names (with labels, 等, its number and the
        reaction words R1c reads), or holds no name and is not words (``_words``)."""
        t, n, gaps = self.t, self.n, self.gaps
        covered, inner = self.covered, self.inner
        enclosed = self._enclosed()
        any_name = False
        named, residue = False, []

        def item_ok() -> bool:
            r = "".join(residue)
            if _NEGATIVE_MARK.search(r):
                return False
            if not named:
                return not _words(r)
            rest = _NUMBERING.sub(" ", _TRANSPARENT.sub(" ", r))
            return not any(c.isalnum() for c in rest)

        p = 0
        while p < n:
            if (p > 0 and p in gaps and not inner.any(p, p + 1)
                    and (p in self.breaks or _cjk(t[p - 1]) or _cjk(t[p])
                         or covered.any(p - 1, p) or covered.any(p, p + 1))):
                if (named or residue) and not item_ok():
                    return False
                any_name = any_name or named
                named, residue = False, []
            if covered.any(p, p + 1):
                if residue and residue[-1] != " ":
                    residue.append(" ")
                named = True
                p += 1
                continue
            c = t[p]
            w = 1 if c in _ITEM_MARKS else next(
                (len(x) for x in _ITEM_WORDS_CJK if t.startswith(x, p)), 0)
            if not w and p in self.tok_at and t[p:self.tok_at[p]] in _ITEM_WORDS_ASCII:
                w = self.tok_at[p] - p
            if not w and (p in enclosed or (c == "." and p + 1 in gaps and p > 0 and not t[p - 1].isdigit())
                          or (c in _ITEM_DASHES and 0 < p < n - 1 and _wordy(t[p - 1]) and _wordy(t[p + 1]))):
                # huang qi. penicillin; 黄芪-青霉素, 黄芪·青霉素; ①黄芪②人参
                w = 1
            if not w and c in "(（#" and (m := _ITEM_NUMBER.match(t, p)):
                w = m.end() - p                 # (1)黄芪(2)人参, #1 黄芪 #2 人参
            if w:
                if (named or residue) and not item_ok():
                    return False
                any_name = any_name or named
                named, residue = False, []
                p += w
                continue
            if p in gaps:
                residue.append(" ")
            residue.append(c)
            p += 1
        if (named or residue) and not item_ok():
            return False
        return any_name or named

    def _enclosed(self) -> frozenset[int]:
        """Where the record writes a circled or other enclosed number (①, ⑴, ⒈, ❶, ㊀), which
        numbers an item: NFKC writes most of them as digits, so ①黄芪②人参 would read 1黄芪2人参."""
        if not _ENCLOSED_NUMBER.search(str(self.reader.record or "")):
            return frozenset()
        raw, at = self.reader.raw()
        keep = self.reader.keep
        return frozenset(p for p in range(self.n) if _ENCLOSED_NUMBER.match(raw[at[keep[p]]]))


def _wordy(c: str) -> bool:
    return c.isalnum() or _cjk(c)


#: The 花粉 denial (``_Rule.denied``): a negative mark or a negative test right after the name.
_DENIED_MARK = re.compile(r"[(（\[【][-－−][)）\]】]|[:：∶][-－−](?![0-9a-z])|皮试阴性|阴性|皮试[(（][-－−][)）]"
                          r"|negative(?![a-z0-9])")


def _words(item: str) -> bool:
    """A list item that holds no name the tables know is words, not another allergen's name,
    when it holds a context word of R2 and R6 as a whole word, labels, class words and its number
    aside (``_CONTEXT_CJK``, ``_CONTEXT_ONE``, ``_CONTEXT_ASCII``): 家里有, 医生开的, 平时煲汤, 服用中,
    好转, 无, 不吃, none, takes daily, Dr. Not 无花果, 对乙酰氨基酚, 香水, Tylenol with codeine or
    vitamin A, which name another allergen."""
    core = _NUMBERING.sub(" ", _TRANSPARENT.sub(" ", item))
    if _CONTEXT_CJK.search(core) or _CONTEXT_ASCII.search(core):
        return True
    last = len(core) - 1
    for i, c in enumerate(core):
        if c in _CONTEXT_ONE and all(not _cjk(core[j]) or core[j] in _CONTEXT_ONE
                                     for j in (i - 1, i + 1) if 0 <= j <= last):
            return True
    return False


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

    For a name that stands as a word, the tier (``_tier``) and the rule decide:

    * ``stop`` / ``allergy``: a drug-level table says the record names the herb (the same
      name, the knowledge base, a cited pack alias, the pinyin or Latin name, a processed
      form of one drug) and the record states an allergy to it (R1: just names, an
      un-negated reaction word, or an un-negated allergy statement about it);
    * ``block`` / ``allergy_mentioned``: the same, but the record only mentions it (R2): the
      message says the record writes the name without saying whether it is an allergy, and
      asks the practitioner to check before signing;
    * ``block`` / ``allergy_source``: the two only share a source (the materia table's entry,
      or a species), and a species, genus or English common name of the herb's source
      (Panax ginseng, Astragalus, ginger), whatever the words around it (R3);
    * ``block`` / ``allergy_near_name``: one name inside the other (白附子 and 附子), or the
      herb's name inside a longer word the tables do not know (郁金 of 郁金香, 地黄 of
      知柏地黄丸, 桂枝 of 桂枝茯苓丸);
    * ``block`` / ``allergy_formula``: a formula the pack or the knowledge base holds, by its
      name or in another product form (四君子汤, 补中益气丸), that the record states an
      allergy to: each herb of its composition, and each herb a tier ties to one, asks,
      because the allergy may be to any of them; a formula it only mentions gives
      ``allergy_mentioned`` naming the formula (R4). A formula never stops by itself;
    * ``block`` / ``allergy_homonym``: 花粉, which is pollen in a history and 天花粉's short
      name in a prescription, for 天花粉 only, unless the record writes an explicit denial of
      it (否认花粉过敏); its message says whether the record states an allergy (R1) or only
      writes the name (R2).

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
        # each distinct reading once: its first places are quoted, the rest counted. A denied
        # 花粉 and one that is not are two readings (否认花粉过敏；花粉皮试阳性), so the key holds
        # ``denied``, and a place that gives a herb nothing never hides one that gives it an
        # issue: each place is matched, none is skipped for another's sake
        groups: dict[tuple, list[Any]] = {}
        for m in ms:
            key = (m.name, m.kind, m.how, m.of, m.species, bool(m.word), m.allergy, m.denied,
                   _ascii_key(m.written)[0] if m.written else "")
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
                        continue
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


#: The words of an ``allergy_mentioned`` message: the record writes the name, and does not say
#: whether it is an allergy. It never says the record is not one.
_MENTIONED = "（上下文未写明是否过敏）"


def _issue(m: Mention, h: str, hk: str, shown: str, matcher: _Matcher
           ) -> tuple[str, str, str, str] | None:
    """The issue the name ``m`` gives the prescribed herb ``h`` (its key ``hk``), if any."""
    prefix = f"过敏史记录“{shown}”：处方含{h}"
    if m.kind == "name" and m.name in _ALLERGY_HOMONYMS:
        if m.denied:
            return None                  # a denied pollen allergy (否认花粉过敏, 花粉（-）)
        hm, nm = _materia(hk), _materia(m.name)
        if (hm is not None and nm is not None and hm.id == nm.id) or m.name in hk:
            where = f"记录中的“{m.word}”含有“{m.name}”的字样；" if m.word else ""
            homonym = (f"过敏史中的“{m.name}”通常指{_ALLERGY_HOMONYMS[m.name]}，"
                       f"但处方中{h}也简称“{m.name}”")
            if m.allergy:
                return ("block", "allergy_homonym",
                        f"{prefix}。{where}{homonym}，请核实所记过敏是否为{h}", "药材名表（materia）")
            return ("block", "allergy_homonym",
                    f"{prefix}。{where}过敏史记录中写到{m.name}{_MENTIONED}；{homonym}。"
                    f"请核实是否对{h}过敏后再签署", "药材名表（materia）")
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
        written = m.word or m.written
        what = (f"记录中的“{written}”" + ("含有" if m.word else "是")
                + f"{f.source}所载方剂{f.name}的名称" + (f"（{f.how}）" if f.how else "")
                + f"，{f.name}的组成为{'、'.join(f.herbs)}")
        source = _join(f"{f.source}方剂{f.name}", xp.source, hp.source, tier.source)
        if m.allergy:
            return ("block", "allergy_formula",
                    f"{prefix}。{what}，{via}；所记过敏可能由方中任一味药引起，未必是{h}，请核实", source)
        return ("block", "allergy_mentioned",
                f"{prefix}。{what}，{via}；过敏史记录中写到{f.name}{_MENTIONED}。"
                f"请核实是否对{f.name}或其中的{c}过敏后再签署", source)
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
        return ("block", "allergy_near_name",
                f"{prefix}。记录中的“{m.word}”含有“{m.name}”的字样，但“{m.word.strip('…')}”"
                f"不是名表所载的药名，未必是指{m.name}" + (f"（{why}）" if why else "")
                + f"，请核实所记过敏是否与{h}有关", source)
    if tier.rank == 3:
        if m.allergy:
            return ("stop", "allergy", prefix + (f"（{why}）" if why else ""), source)
        written = m.written or m.name
        return ("block", "allergy_mentioned",
                f"{prefix}。过敏史记录中写到{written}{_MENTIONED}" + (f"；{why}" if why else "")
                + f"。请核实是否对{written}过敏后再签署", source)
    if tier.rank == 2:
        return ("block", "allergy_source", f"{prefix}。{why}，请核实", source)
    return ("block", "allergy_near_name", f"{prefix}。{why}，请核实所记过敏为何药", source)


_ALLERGY_WORD = re.compile(r"过敏|致敏|变态反应|allerg")


def allergy_records_in(entries: Iterable[Any]) -> list[str]:
    """The entries of another field (the conditions) that hold a word of allergy, each read
    whole like an entry of the allergy field: 黄芪过敏 written under conditions stops 黄芪 as it
    would under allergies. No entry with such a word is skipped, a condition's name included:
    the 过敏 of 过敏性鼻炎 is a reaction word like any other (R1c), so 过敏性鼻炎，服用黄芪后出现皮疹
    and 过敏性鼻炎，服用黄芪后好转 there both stop 黄芪."""
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
