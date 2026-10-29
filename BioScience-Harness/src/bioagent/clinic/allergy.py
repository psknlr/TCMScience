"""Reading an allergy history against the herbs of a prescription (过敏史核查).

A history is not read through a list of wordings (过敏史：X, 对X过敏, X过敏史 …). Every
wording such a list missed was a silent pass, and histories are written in more ways than
any list holds: 患者对国老过敏, 3年前北芪过敏, 北芪/人参过敏, 变应原：北芪, allergic to huangqi.
The whole record is normalised and *scanned* for every name the tables know, wherever it
stands, and each name found is then read in its place.

Normalising (``fold``)
    NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens, variation
    selectors, fillers, NUL and the other control characters are removed; look-alike radicals
    (⻩ for 黄, ⻰ for 龙) are read as the characters they stand for; traditional characters
    are folded with the materia table's own table (``materia.to_simplified``: 黃耆 → 黄耆, 人參
    → 人参) and, for the characters of every name the tables know and the words a history
    writes around them, with the traditional, Hong Kong, Taiwan and Japanese forms that table
    leaves (過敏 → 过敏, 黑順片 → 黑顺片, 人銜 → 人衔, 当帰 → 当归); tone marks are dropped from
    Latin letters (Huáng Qí → huang qi); lower case. Whitespace is dropped for the scan, so
    北 芪 is 北芪, but it stays a boundary: a name is read across it only where no name of two
    characters or more stands on either side of it (姜 / 黄芪 is 姜 and 黄芪, never 姜黄).

Scanning (``mentions``)
    for the seed knowledge base's herb names and aliases (黄耆, 北芪, 国老), the materia
    table's names (``materia.names_in``, and every name of every entry, one character
    included: 姜, 枣), the pack's herbs and its cited aliases (``herb_aliases``: 夜交藤, 川黄柏),
    the formulas the pack and the knowledge base hold (四君子汤, and the same name in another
    product form: 补中益气丸), and the parts of the names the tables give the prescribed herbs,
    of two characters or more, with the abbreviations that drop one character (贝母 of 浙贝母,
    淮山 of 淮山药, 法夏 of 法半夏), and a few everyday words that hold a name's characters but
    name something else (桂花, 胡萝卜). Latin and pinyin are read as whole words, with or without spaces, hyphens,
    tone marks or tone numbers: the knowledge base's pinyin and Latin names, the pack's
    pinyin of every herb, the materia table's Latin names in the Pharmacopoeia 2020 order
    and the older one (Astragali Radix, Radix Astragali), and the species and genera the
    tables give (Panax ginseng, Astragalus), with the pack's cited English common names
    (ginger, licorice). At each position the longest name is taken, and a name inside a
    longer one found before it (杏仁 in 苦杏仁) is that one's.

Reading each name in its place
    * Names written next to each other with nothing between them (北芪人参过敏, 天麻钩藤颗粒)
      are read together. They *stand as words* when what is before the first and after the
      last is a boundary (the start or end of the record, punctuation, a space, a digit,
      Latin letters) or one of the words a history writes around a name (对, 患者, 自诉,
      因, 的, 吃, 年前, 过敏, 过敏史, 及, 等, 后, 就, 皮疹 …);
    * otherwise each is *inside a longer word* the tables do not know: 郁金 in 郁金香, 地黄 in
      知柏地黄丸, 黄连 in 双黄连, 桂枝 and 茯苓 in 桂枝茯苓丸; so is a name whose first character
      ends an everyday word (于术 after 对于, 白及 after 蛋白), a name written before a formula's
      name (柴胡 of 柴胡桂枝汤), and names that overlap (姜黄芪: 姜黄, 黄芪 and 姜). A longer word
      that the materia table or the knowledge base resolves to the same drug, or that is the
      name processed, is that drug (甘草片, 山药粉, 炙北芪, 制黑顺片). A name that starts with a
      denial's character and runs into what a denial names is the denial (没药物过敏史);
    * a name is *denied* when its clause opens with a denial (否认, 无, 没有, 未, 排除, NKDA,
      no …) that no 过敏, 史, 但, 出现, "except" or "than" closes before the name, the clause
      being a bracket inside such a denial (否认中药（黄芪、人参）过敏); when a denial follows the
      name or the list it ends (黄芪：无, 黄芪（-）, 黄芪无过敏史, 黄芪、党参等未见过敏, 黄芪耐受);
      when the next clause only takes it back (怀疑黄芪过敏，已排除); or when the clause says the
      herb is used all along with no reaction in it (平素服黄芪泡水). A word that only starts
      with a denial is none (非常, 未成年, 无意中, 未经, no doubt). A dash or an arrow after a
      name is no denial (黄芪——皮疹, huangqi -> hives); "-" is one only in brackets or after a
      colon (黄芪（-）, 黄芪：-). A denial that names nothing adds nothing and never hides a name
      in another clause.

Each name found is matched to each prescribed herb through the tiers of ``_tier``; see
``allergy_issues`` for the issues they give.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from .findings import normalise

__all__ = ["fold", "Mention", "mentions", "allergy_issues", "allergy_records_in", "ALLERGY_CODES"]

ALLERGY_CODES = ("allergy", "allergy_source", "allergy_near_name", "allergy_formula",
                 "allergy_homonym", "allergy_denied")


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


@lru_cache(maxsize=8192)
def _materia(name: str) -> Any:
    """The materia table's entry for a whole written name, or None. The table files names
    by source (species or product), for network pharmacology: an entry's names are of one
    source, not always of one drug (鸡内金 with 鸡蛋, 海藻 with 海带)."""
    from ..sources.materia import MATERIA, resolve_name
    try:
        drug = resolve_name(name)
    except Exception:                                            # noqa: BLE001
        return None
    return MATERIA.get(drug) if drug else None


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
    """``name`` without the processing word, or the word of state, in front of a known name
    (熟地黄 → 地黄, 炙黄耆 → 黄耆, 鲜生地 → 生地, 熟川军 → 川军), or ``name`` itself."""
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
    processing leaves the herb what it is, so 生地黄 and 熟地黄 are both 地黄, and 生地, the
    short name of 生地黄, reaches 熟地黄 through it."""
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
    suffix it strips), the knowledge base, or the pack (its herbs and cited aliases)."""
    from ..sources.materia import resolve_name
    return (name in extra or name in _materia_names() or bool(_identity(kb, name))
            or resolve_name(name) is not None)


@lru_cache(maxsize=16384)
def _pair(kb: Any, x: str, y: str, extra: frozenset[str] = frozenset()) -> str:
    x_kb, y_kb = _identity(kb, x), _identity(kb, y)
    same_kb = bool(x_kb & y_kb)
    inside = x == y or (len(x) >= 2 and len(y) >= 2 and (x in y or y in x))
    if inside and x != y and not same_kb and not (_processed(x, y) or _processed(y, x)):
        # one name inside the other is the same drug only where the tables hold both names
        # and give them to one drug (丹皮 and 牡丹皮); a name no table holds is not vouched for
        # by any (决明 is not 石决明, 金银 not 金银花)
        if not (_known(kb, x, extra) and _known(kb, y, extra)):
            return "unknown:" + (x if not _known(kb, x, extra) else y)
        x_m, y_m = _materia(x), _materia(y)
        if (x_m is not None and y_m is not None and x_m.id != y_m.id) or (x_kb and y_kb):
            return "near"
    if inside:
        return "name"
    return "kb" if same_kb else ""


def _one_drug(kb: Any, x: str, y: str) -> str:
    """How the names ``x`` and ``y`` are one drug: ``"name"`` (the same name, a processed
    form of the other, or one inside the other where the name tables hold both and do not
    give them to different drugs), ``"kb"`` (the seed knowledge base gives both to one
    herb), ``"near"`` (one inside the other, but the tables give them to different drugs),
    ``"unknown:<name>"`` (one inside the other, and no table holds that name) or ``""``.
    Two names are compared here; a name of one character is not looked for inside another
    (the 姜 of 生姜 in 沙姜): the scan reads such a character in its place instead."""
    return _pair(kb, x, y)


#: Names that an allergy record uses for something other than the drug the materia table
#: files them under. In a prescription 花粉 is 天花粉's short name; in an allergy history 花粉
#: (花粉过敏, 对花粉过敏) is pollen, the most common entry of all. Such an entry is checked
#: against the drug it may name only, with a block that asks which was meant, and not
#: carried to that drug's species-mates (瓜蒌, from the same Trichosanthes). A denied 花粉
#: (否认花粉过敏) is a denied pollen allergy and asks nothing.
_ALLERGY_HOMONYMS: Mapping[str, str] = {"花粉": "植物花粉"}

#: Everyday words that hold a name of the tables but are something else, each with what it
#: is. Read as words of their own, they name no herb: 桂花 is osmanthus, not 桂 (肉桂) and so
#: not 桂枝; 胡萝卜 is the carrot (Daucus), not the 萝卜 (Raphanus) the materia table files
#: under 莱菔子; 桂圆 is longan, which the table files as 龙眼 and 桂圆肉 under 龙眼肉 (a
#: prescribed 龙眼肉 still reads it, as a part of 桂圆肉).
_OTHER_WORDS: Mapping[str, str] = {
    "桂花": "木犀（Osmanthus fragrans）的花", "桂鱼": "鳜鱼", "鳜鱼": "鳜鱼",
    "胡萝卜": "胡萝卜（Daucus carota）", "桂圆": "龙眼（Dimocarpus longan）"}


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
_BLANKS = frozenset("\u034f\u115f\u1160\u17b4\u17b5\u3164\uffa0\u2800")
_WS = re.compile(r"\s+")


def _invisible(c: str) -> bool:
    o = ord(c)
    if c in _BLANKS or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or 0x180B <= o <= 0x180F:
        return True
    cat = unicodedata.category(c)
    return cat in ("Cf", "Cs") or (cat == "Cc" and c not in "\t\n\r")


def _visible(text: str) -> str:
    """``text`` without the characters that show nothing but can split a name: format
    characters (zero-width space and joiners, the byte-order mark, soft hyphen, bidi
    marks), surrogates, controls other than tab and line breaks (NUL), variation selectors,
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
#: dropped), 及人参. Missing a word here never hides a name: the name is then read as inside
#: a longer word, which asks the practitioner (a block) instead of stopping.
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
改用 换用 应用
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
""".split())

#: Product words, which make a longer word of the name before them even where a word of
#: ``_AFTER`` starts them (口服 of 口服液): 黄芪口服液, 人参注射液.
_PRODUCT_WORDS = ("口服液", "注射液", "注射剂", "注射", "针剂", "颗粒", "胶囊", "糖浆", "合剂", "冲剂",
                  "滴丸", "片剂", "丸剂", "散剂", "膏剂", "酊剂", "外用药", "外用膏")

_BEFORE_MAX = max(map(len, _BEFORE))
_AFTER_MAX = max(map(len, _AFTER))

#: Everyday words whose last character starts a name: in 对于术后 the 于 is 对于's, not the
#: first character of 于术 (白术), and in 鸡蛋白及 the 白 is 蛋白's (protein), not 白及's. A
#: name after one of them is read as inside a longer word, whatever stands before it.
_EVERYDAY = frozenset("""
对于 关于 由于 至于 属于 位于 等于 用于 大于 小于 基于 在于 便于 易于 过于 终于 处于
蛋白 空白 苍白 明白 坦白 表白 洁白 雪白 黑白 灰白 清白
""".split())

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
#: 过敏, 服黄芪无不适, 长期服用黄芪无不良反应, 黄芪耐受, 黄芪可用, 黄芪过敏已排除.
_DENIAL_PHRASE = re.compile(
    r"(?:(?:已|曾|均)?(?:服用过|使用过|用过|服过|吃过|喝过))?(?:"
    r"(?:无|未见|没有|没|未发现|未出现|未发生|未有|并无|从无|从未|未)"
    r"(?:明显|任何|特殊|相关|什么)?(?:过敏|不良反应|不适|异常|反应|副作用|皮疹)"
    r"|不过敏|不会过敏|未过敏|没过敏|没有过敏|无过敏|耐受|可耐受|可以用|可用|可以服|可服|能用"
    r"|无事|没事|无碍|已排除|已除外|排除|否认)"
    r"|noreaction|noallerg|notallergic|nosideeffect|noproblem|tolerat")
#: A clause about using a herb all along, with no reaction in it, says it is tolerated:
#: 平素服黄芪泡水, 长期服用人参. It is read as a denial, which asks (a block), not as an
#: allergy; 长期服用人参后出现皮疹 has a reaction and is not one.
_USE_LEAD = re.compile(r"(?:平素|平时|平日|长期|正在|一直|经常|常年|日常|每天|每日|常)"
                       r"(?:服用|服|喝|吃|用|使用|食用|饮用|泡水喝|泡水)")
_REACTION = re.compile(r"过敏|疹|痒|肿|反应|不适|吐|泻|痛|休克|喘|晕|心慌|心悸|胸闷|中毒|不耐|副作用"
                       r"|allerg|rash|hive|reaction|itch|swell")
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
#: denies 黄芪 too.
_CLAUSE_MARK = re.compile(r"[^\w、/·\-]|_")
_OPEN_BRACKETS = "(（[【〔"
#: What a list may end in before a denial written after it: 黄芪、党参等未见过敏.
_LIST_TAIL = re.compile(r"(?:等(?:中药|药物|药)?|之类|一类|类|均|都|皆|也|全部|等等)*")
_CLOSE_BRACKETS = ")）]】〕"


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
    trie: dict
    names: frozenset[str]
    ascii: Mapping[str, tuple[_Ascii, ...]]
    ascii_max: int
    formulas: Mapping[str, _Formula]
    formula_trie: dict
    known: frozenset[str]         # the pack's herbs and cited aliases


def _trie(names: Iterable[str]) -> dict:
    root: dict = {}
    for n in names:
        node = root
        for c in n:
            node = node.setdefault(c, {})
        node[""] = n                    # no character is "", so this marks a whole name
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
#: also 天麻钩藤颗粒. 散 and 饮 are not among them: 四逆散 is another formula than 四逆汤.
_FORMULA_ENDINGS = ("注射液", "口服液", "软胶囊", "胶囊", "颗粒", "合剂", "冲剂", "糖浆", "滴丸",
                    "汤", "散", "丸", "饮", "方", "膏", "丹", "片")
_PRODUCT_FORMS = ("丸", "汤", "颗粒", "胶囊", "软胶囊", "片", "口服液", "合剂", "冲剂", "糖浆",
                  "滴丸", "浓缩丸", "水丸", "蜜丸", "大蜜丸", "小蜜丸")


def _formulas(kb: Any, pack: Any, cjk: set[str]) -> dict[str, _Formula]:
    """Every formula the pack and the knowledge base hold, under its own name, its aliases
    and its name in the other product forms, with its herbs."""
    exact: dict[str, _Formula] = {}
    for name, f in (getattr(pack, "formulas", None) or {}).items():
        exact[fold(name)] = _Formula(name, tuple(h for h, _ in f.herbs), "知识包")
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
        if len(stem) < 2 or stem in cjk:          # 麻黄汤's 麻黄片 is sliced 麻黄
            continue
        for form in _PRODUCT_FORMS:
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
    cjk.update(f for n in _materia_names() if _all_cjk(f := normalise(fold(n))))
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
    vocab = _Vocab(_trie(cjk), frozenset(cjk),
                   {k: one_each(v) for k, v in ascii_names.items()}, longest,
                   formulas, _trie(formulas), frozenset(known))
    if len(_VOCABS) > 8:
        _VOCABS.clear()
    _VOCABS[key] = vocab
    return vocab


@lru_cache(maxsize=1)
def _other_trie() -> dict:
    return _trie(_OTHER_WORDS)


# ============================================================== the scan
@dataclass(frozen=True)
class Mention:
    """A name found in a record, read in its place.

    ``name`` is the name as the tables write it (the Chinese name a Latin or pinyin name is
    of, the formula a product name is of, the species a species name or common name is of);
    ``written`` the record's own text when it differs (huangqi, astragali radix, 补中益气丸).
    ``word`` is the longer word the name stands inside ("" when it stands as a word):
    郁金香 for 郁金. ``denied`` says the record denies an allergy to it. ``kind`` is "name",
    "part" (a part or an abbreviation of ``of``, a name of a prescribed herb: 淮山 of 淮山药),
    "formula" (``of`` is the formula), "species" or "genus" (``species`` holds them).
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
    #: ``denied`` only because the record says the herb is used all along with no reaction
    tolerated: bool = False


def _compact(text: str) -> tuple[str, frozenset[int], tuple[int, ...]]:
    """``text`` without whitespace, the positions in the result that had some before, and
    for each character of the result its position in ``text``."""
    keep = [i for i, c in enumerate(text) if not c.isspace()]
    gaps = frozenset(k for k in range(1, len(keep)) if keep[k] != keep[k - 1] + 1)
    return "".join(text[i] for i in keep), gaps, tuple(keep)


def _longest(trie: dict, t: str, i: int, limit: int = 16) -> int:
    node, best, j, n = trie, -1, i, len(t)
    while j < n and j - i < limit:
        node = node.get(t[j])
        if node is None:
            break
        j += 1
        if "" in node:
            best = j
    return best


#: Which reading wins where two of one length start at one place.
_KIND_RANK = {"formula": 4, "name": 3, "part": 2, "other": 1}


def _spans(t: str, gaps: frozenset[int], tries: Sequence[tuple[str, dict]]
           ) -> list[tuple[int, int, str]]:
    """(start, end, kind) of the longest known name at each position, a name inside a longer
    one that starts before it dropped. A name is read across whitespace (北 芪) only where no
    name of two characters or more stands, within the whitespace, on any of its characters:
    姜 / 半夏 is 姜 and 半夏, not 姜半夏."""
    from ..sources.materia import names_in
    n = len(t)
    cuts = sorted(gaps)
    inner: dict[int, tuple[int, str]] = {}           # within one whitespace-free stretch
    across: dict[int, tuple[int, str]] = {}

    def better(table: dict, s: int, e: int, kind: str) -> None:
        old = table.get(s)
        if old is None or (e - s, _KIND_RANK[kind]) > (old[0] - s, _KIND_RANK[old[1]]):
            table[s] = (e, kind)

    for kind, trie in tries:
        if not trie:
            continue
        for i, c in enumerate(t):
            if c not in trie:
                continue
            k = bisect_right(cuts, i)
            stop = cuts[k] if k < len(cuts) else n
            e = _longest(trie, t, i, min(16, stop - i))
            if e > 0:
                better(inner, i, e, kind)
            if stop < n:
                e2 = _longest(trie, t, i)
                if e2 > stop:
                    better(across, i, e2, kind)
    bounds = [0, *cuts, n]
    for a, b in zip(bounds, bounds[1:]):
        for m in names_in(t[a:b]):
            # an identity word before a known name that makes no known name (白首乌): the
            # known name is read inside the longer word, like any other
            s = a + m.start + (1 if m.drug_id is None and m.contains else 0)
            better(inner, s, a + m.start + len(m.name), "name")
    covered = {p for s, (e, _) in inner.items() if e - s >= 2 for p in range(s, e)}
    for s, (e, kind) in across.items():
        if not any(p in covered for p in range(s, e)):
            better(inner, s, e, kind)
    out: list[tuple[int, int, str]] = []
    reach = -1
    for s in sorted(inner):
        e, kind = inner[s]
        if e <= reach:
            continue
        out.append((s, e, kind))
        reach = max(reach, e)
    return out


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


def _ascii_spans(t: str, gaps: frozenset[int], vocab: _Vocab
                 ) -> list[tuple[int, int, _Ascii]]:
    """(start, end, what) of each Latin, pinyin, species or English name, as whole words:
    astragali radix, radix astragali, astragaliradix, huangqi, huang qi, huang2 qi2, Huáng
    Qí, panax ginseng, ginger."""
    if not vocab.ascii:
        return []
    toks = _tokens(t, gaps)
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


def _same_drug(kb: Any, word: str, name: str) -> bool:
    """``word``, a longer word holding ``name``, is the same drug by the tables: the
    materia table resolves it to the same entry (甘草片, 山药粉, 生鸡蛋), the knowledge base
    to the same herb (炙北芪), or it is ``name`` processed (炒夜交藤, 制黑顺片)."""
    from ..sources.materia import resolve_name
    m_word = resolve_name(word)
    if m_word is not None and m_word == resolve_name(name):
        return True
    i_word = _identity(kb, word)
    return bool(i_word) and i_word == _identity(kb, name) or _processed(word, name)


@dataclass(frozen=True)
class _Parts:
    """Parts of the prescribed herbs' names: part -> the name it is of."""
    trie: dict
    of: Mapping[str, str]


_NO_PARTS = _Parts({}, {})


def _read(t: str, gaps: frozenset[int], kb: Any, vocab: _Vocab, parts: _Parts = _NO_PARTS,
          shown: tuple[str, Sequence[int]] | None = None) -> list[Mention]:
    """Every name in the compact, folded record ``t``, read in its place. ``shown`` is the
    record as written and where each character of ``t`` is in it, to quote a Latin or pinyin
    name as the record writes it (Radix Astragali, not radix astragali)."""
    spans = _spans(t, gaps, (("formula", vocab.formula_trie), ("name", vocab.trie),
                             ("part", parts.trie), ("other", _other_trie())))
    latin = _ascii_spans(t, gaps, vocab)
    n = len(t)
    end_at = {s: e for s, e, _ in spans}
    end_at.update({s: e for s, e, _ in latin})
    bounds = sorted({0, *(m.end() for m in _CLAUSE_MARK.finditer(t)),
                     *(g for g in gaps if 0 < g < n and _cjk(t[g - 1]) and _cjk(t[g]))})
    closers = [m.start() for m in _CLOSERS.finditer(t)]
    reactions = [m.start() for m in _REACTION.finditer(t)]
    named: dict[int, int] = {}          # start -> end of each name read (filled below)

    def opens_denial(cs: int, s: int) -> str:
        """How the clause that starts at ``cs`` denies what is at ``s``: "deny" (it opens
        with a denial no closer ends before ``s``), "use" (it says the herb is used all
        along, and no reaction follows), or ""."""
        i, at = cs, [cs]
        for _ in range(4):
            lead = _starting(t, i, _CLAUSE_LEADS, s, gaps)
            if not lead:
                break
            i += len(lead)
            at.append(i)
        neg = _negation(t, i, s, gaps)
        if neg and named.get(i, 0) > i + len(neg):
            neg = ""                    # 没药人参过敏: the 没 is 没药's, no denial
        if not neg:
            use = next((m for p in at if (m := _USE_LEAD.match(t, p)) and m.end() <= s), None)
            # a reaction anywhere after it (正在服用黄芪，出现皮疹) makes it no tolerance
            return "use" if use and bisect_left(reactions, cs) == len(reactions) else ""
        j = i + len(neg)
        k = bisect_left(closers, j)
        return "deny" if k == len(closers) or closers[k] >= s else ""

    def after_list(p: int) -> int:
        """Past the rest of the list a name ends and its tail: 黄芪、党参等 → after 等."""
        for _ in range(64):
            if p in end_at:
                p = end_at[p]
                continue
            sep = next((w for w in ("以及", "或者", "、", "/", "和", "及", "与", "跟", "或")
                        if t.startswith(w, p)), "")
            if sep and p + len(sep) in end_at:
                p = end_at[p + len(sep)]
                continue
            break
        p = _LIST_TAIL.match(t, p).end()
        while p < n and t[p] in _CLOSE_BRACKETS:
            p += 1
        if p < n and t[p] in _OPEN_BRACKETS:
            # past a note in brackets: 补中益气汤（含黄芪、人参）无不适
            q = next((j for j in range(p + 1, min(n, p + 40)) if t[j] in _CLOSE_BRACKETS), -1)
            if q > 0:
                p = q + 1
        return p

    def denied_after(p: int) -> bool:
        if _DENIAL_DASH.match(t, p):
            return True
        q = _AFTER_LABEL.match(t, p).end()
        r = _AFTER_SEP.match(t, q).end()
        if _DENIAL_PHRASE.match(t, r):
            return True
        m = _DENIAL_BARE.match(t, r)
        if not m:
            return False
        j = m.end()
        while j < n and t[j] in _CLOSE_BRACKETS:
            j += 1
        return (j >= n or j in gaps or not (_cjk(t[j]) or (t[j].isascii() and t[j].isalnum())))

    def retracted(p: int) -> bool:
        k = bisect_right(bounds, p)
        if k >= len(bounds):
            return False
        b = bounds[k]
        b2 = bounds[k + 1] if k + 1 < len(bounds) else n
        clause = t[b:b2].rstrip("。.，,；;！!、 ")
        return bool(clause) and _RETRACTION.fullmatch(clause) is not None

    def denial(s: int, e: int) -> str:
        cs = bounds[bisect_right(bounds, s) - 1]
        how = opens_denial(cs, s)
        if how:
            return how
        if cs > 0 and t[cs - 1] in _OPEN_BRACKETS:
            # a bracket inside a denial that names what it denies: 否认中药（黄芪、人参）过敏
            outer = bounds[bisect_right(bounds, cs - 1) - 1]
            if outer < cs - 1 and opens_denial(outer, cs - 1) == "deny":
                i = outer
                for _ in range(4):
                    lead = _starting(t, i, _CLAUSE_LEADS, cs - 1, gaps)
                    if not lead:
                        break
                    i += len(lead)
                if i + len(_negation(t, i, cs - 1, gaps)) < cs - 1:
                    return "deny"
        p = after_list(e)
        return "deny" if denied_after(e) or denied_after(p) or retracted(p) else ""

    def denied(s: int, e: int) -> dict[str, bool]:
        how = denial(s, e)
        return {"denied": bool(how), "tolerated": how == "use"}

    def left_ok(a: int, spaced: bool) -> bool:
        if 0 < a < n and t[a - 1:a + 1] in _EVERYDAY:
            return False
        return (a == 0 or (not spaced and a in gaps) or not _cjk(t[a - 1])
                or _ends_with(t, a, _BEFORE, _BEFORE_MAX))

    def right_ok(b: int, spaced: bool) -> bool:
        if b < n and not (not spaced and b in gaps) and t.startswith(_PRODUCT_WORDS, b):
            return False
        return (b >= n or (not spaced and b in gaps) or not _cjk(t[b])
                or _starts_with(t, b, _AFTER, _AFTER_MAX))

    def is_spaced(s: int, e: int) -> bool:
        return any(p in gaps for p in range(s + 1, e))

    # the names written next to each other, with no mark between them, are read together
    overlap = [False] * len(spans)
    reach, reach_i = -1, -1
    for i, (s, e, _) in enumerate(spans):
        if s < reach:
            overlap[i] = overlap[reach_i] = True
        if e > reach:
            reach, reach_i = e, i
    runs: list[list[int]] = []
    for i, (s, e, _) in enumerate(spans):
        last = runs[-1] if runs else []
        if last and overlap[i] and overlap[last[-1]] and s < max(spans[j][1] for j in last):
            last.append(i)              # names that overlap (姜黄 and 黄芪 of 姜黄芪): one word
        elif (last and not overlap[i] and not overlap[last[-1]]
                and spans[last[-1]][1] == s and s not in gaps
                and t[s - 1:s + 1] not in _EVERYDAY):
            last.append(i)
        else:
            runs.append([i])

    starts = [s for s, _, _ in spans]
    sides: list[tuple[list[bool], list[bool]]] = []
    for run in runs:
        # a name's left side is a boundary when the run is one up to it (北芪人参过敏: 人参's
        # left is 北芪's), and its right side when the run is one after it; 天麻钩藤颗粒 has
        # none on the right, and 牛奶蛋白及鸡蛋 none on the left of 白及 but one of 鸡蛋 (及).
        # A formula's name written after another name is a longer formula's (柴胡桂枝汤)
        cluster = any(overlap[i] for i in run)
        k = len(run)
        lok, rok = [False] * k, [False] * k
        for x in range(k):
            s, e, kind = spans[run[x]]
            lok[x] = not cluster and (left_ok(s, is_spaced(s, e))
                                      or (x > 0 and lok[x - 1] and kind != "formula"))
        for x in reversed(range(k)):
            s, e, _ = spans[run[x]]
            rok[x] = not cluster and (right_ok(e, is_spaced(s, e))
                                      or (x < k - 1 and rok[x + 1]
                                          and spans[run[x + 1]][2] != "formula"))
        sides.append((lok, rok))
    # a name that starts with a denial's character and runs into what a denial names is
    # the denial (没药物过敏史 is 没 + 药物过敏史, not 没药); any other name at a denial's
    # place is the name (没药人参过敏 denies nothing)
    dropped: set[int] = set()
    for run, (lok, rok) in zip(runs, sides):
        for x, i in enumerate(run):
            s, e, kind = spans[i]
            if kind in ("name", "part"):
                neg = _negation(t, s, e, gaps)
                if (neg and _cjk(neg[0]) and not rok[x]
                        and t.startswith(_DENIED_OBJECTS, s + len(neg))):
                    dropped.add(i)
    named.update({s: e for i, (s, e, kind) in enumerate(spans)
                  if i not in dropped and kind != "other"})

    out: list[Mention] = []
    for run, (lok, rok) in zip(runs, sides):
        cluster = any(overlap[i] for i in run)
        k = len(run)
        c0, c1 = min(spans[i][0] for i in run), max(spans[i][1] for i in run)
        standing = [lok[x] and rok[x] for x in range(k)]
        if cluster:
            # names that overlap can be read either way: 姜黄芪 is 姜黄 + 芪 or 姜 + 黄芪, so
            # what is left before or after each of them, if a name, is read too (姜)
            word = t[c0:c1]
            for i in run:
                s, e, _ = spans[i]
                for a, b in ((c0, s), (e, c1)):
                    if a < b and t[a:b] in vocab.names:
                        out.append(Mention(t[a:b], a, b, word=word, **denied(c0, c1)))
        for x, i in enumerate(run):
            s, e, kind = spans[i]
            if kind == "other" or i in dropped:
                continue
            name = t[s:e]
            if lok[x] and rok[x]:
                out.append(_mention(kind, name, s, e, vocab, parts, **denied(s, e)))
                continue
            sp = is_spaced(s, e)
            a, b = (c0, c1) if cluster else (s, e)
            # the names next to it that are inside the same word are part of it: 桂枝茯苓丸
            y = x - 1
            while not cluster and y >= 0 and not standing[y]:
                a, y = spans[run[y]][0], y - 1
            y = x + 1
            while not cluster and y < k and not standing[y]:
                b, y = spans[run[y]][1], y + 1
            if not lok[x]:
                while not left_ok(a, sp) and s - a < 12:
                    a -= 1
            if not rok[x]:
                while not right_ok(b, sp) and b - e < 12:
                    b += 1
            word = t[a:b]
            shown = (("…" if not lok[x] and not left_ok(a, sp) else "") + word
                     + ("…" if not rok[x] and not right_ok(b, sp) else ""))
            alone = not any(j != i and spans[j][0] < b and spans[j][1] > a
                            and spans[j][2] != "other"
                            for j in range(bisect_left(starts, a - 16),
                                           bisect_right(starts, b)))
            if alone and kind == "name" and _same_drug(kb, word, name):
                out.append(Mention(word, a, b, **denied(a, b)))
                continue
            out.append(_mention(kind, name, s, e, vocab, parts, word=shown,
                                **denied(a, b)))
    for s, e, entry in latin:
        if shown is not None:
            written = _WS.sub(" ", shown[0][shown[1][s]:shown[1][e - 1] + 1]).strip()
        else:
            written = "".join((" " if p in gaps and p > s else "") + t[p] for p in range(s, e))
        if entry.kind == "name":
            out.append(Mention(entry.target[0], s, e, written=written, how=entry.how,
                               **denied(s, e)))
        else:
            out.append(Mention(written, s, e, written=written, how=entry.how,
                               kind=entry.kind, species=entry.target, **denied(s, e),
                               of=entry.source))
    return sorted(out, key=lambda m: (m.start, m.end))


def _mention(kind: str, name: str, s: int, e: int, vocab: _Vocab, parts: _Parts, *,
             word: str = "", denied: bool = False, tolerated: bool = False) -> Mention:
    how = {"word": word, "denied": denied, "tolerated": tolerated}
    if kind == "formula":
        f = vocab.formulas[name]
        return Mention(f.name, s, e, written=name, how=f.how, kind="formula", of=f.name, **how)
    if kind == "part" and parts.of[name] != name:
        return Mention(name, s, e, kind="part", of=parts.of[name], **how)
    return Mention(name, s, e, **how)


def _table_names(kb: Any, pack: Any, herb: str) -> list[str]:
    """The names the tables give a herb: its own, the pack's cited aliases of it, the
    knowledge base's names of it, and the names the materia table files with it."""
    from ..sources.materia import MATERIA, _NAME_INDEX, resolve_name
    names = [herb]
    aliases = (getattr(pack, "herb_aliases", {}) or {}) if pack is not None else {}
    names += [normalise(fold(a)) for a, (h, _) in aliases.items() if h == herb]
    ids = _identity(kb, herb)
    for e in (*getattr(kb, "herbs", {}).values(), *getattr(kb, "processed", {}).values()):
        if ids and (getattr(e, "herb_id", "") or e.id) in ids:
            names += [normalise(fold(n)) for n in getattr(e, "names", ())]
    drug = resolve_name(herb)
    if drug:
        entry = MATERIA[drug]
        names += [entry.chinese, *entry.aliases]
        names += [n for n, d in _NAME_INDEX.items() if d == drug]
    return list(dict.fromkeys(n for n in names if _all_cjk(n)))


def _parts(kb: Any, pack: Any, vocab: _Vocab, herbs: Iterable[str]) -> _Parts:
    """The parts of two characters or more of the names the tables give the prescribed
    herbs, and the abbreviations that drop one character of a name of three (贝母 of 浙贝母,
    淮山 of 淮山药, 交藤 of 夜交藤, 法夏 of 法半夏, 川柏 of 川黄柏, 鸡金 of 鸡内金), that no
    table holds as a name of its own. A record naming only a part may mean the herb, but no
    table says it does: such a part asks (a block), it never stops. A prescribed name no table
    knows is scanned for as it is (黄芪颗粒)."""
    of: dict[str, str] = {}
    whole: set[str] = set()
    for h in herbs:
        if not (2 <= len(h) <= 12 and _all_cjk(h)):
            continue
        if h not in vocab.names and h not in _BEFORE and h not in _AFTER:
            whole.add(h)            # not a word of the history itself (a herb named 过敏)
        for p, name in _herb_parts(kb, pack, vocab, h):
            of.setdefault(p, name)
    for h in whole:
        of.pop(h, None)
    trie = _trie([*of, *whole])
    of.update({h: h for h in whole})
    return _Parts(trie, of)


_HERB_PARTS: dict[tuple[int, str, str], tuple[tuple[str, str], ...]] = {}


def _herb_parts(kb: Any, pack: Any, vocab: _Vocab, h: str) -> tuple[tuple[str, str], ...]:
    from ..sources.materia import IDENTITY_PREFIXES, PROCESSING_WORDS
    key = (id(kb), getattr(pack, "digest", "") if pack is not None else "", h)
    if key in _HERB_PARTS:
        return _HERB_PARTS[key]
    of: dict[str, str] = {}
    for name in _table_names(kb, pack, h):
        if len(name) < 3 or len(name) > 8:
            continue
        # not the word a name starts with that only says where, how big, what colour or how
        # processed (大麦 of 大麦冬 is barley, 大红 of 大红枣 a colour, 炒白 of 土炒白术), and
        # only a part that holds a character of the herb's own name (高丽 of 高丽参 is Korea)
        lead = next((w for w in sorted((*PROCESSING_WORDS, *_STATE_WORDS, *IDENTITY_PREFIXES),
                                       key=len, reverse=True) if name.startswith(w)), "")
        pieces = {name[i:j] for i in range(len(name)) for j in range(i + 2, len(name) + 1)
                  if not (i < len(lead) and j < len(name))}
        if len(name) == 3:
            pieces.add(name[0] + name[2])
        # the name without the part word it ends in is a part too: 桂圆 of 桂圆肉 (龙眼肉)
        bare = name[:-1] if name[-1] in "肉仁子心皮根叶花" else ""
        for p in sorted(pieces - {name}):
            if (p not in vocab.names and p not in _BEFORE and p not in _AFTER and p not in of
                    and (set(p) & set(h) or p == bare)):
                of[p] = name
    if len(_HERB_PARTS) > 4096:
        _HERB_PARTS.clear()
    _HERB_PARTS[key] = tuple(of.items())
    return _HERB_PARTS[key]


def mentions(record: Any, *, kb: Any = None, pack: Any = None,
             herbs: Sequence[str] = ()) -> list[Mention]:
    """The names an allergy record holds, each read in its place (see the module doc)."""
    kb = kb if kb is not None else _default_kb()
    vocab = _vocab(kb, pack)
    t, gaps, _ = _compact(fold(record))
    return _read(t, gaps, kb, vocab, _parts(kb, pack, vocab, [_herb_key(h) for h in herbs]))


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


def _pack_distinct(pack: Any, x: str, y: str) -> bool:
    """A herb the pack sources itself because the materia table lacks it (首乌藤, the stem
    of the plant whose root is 何首乌) is a drug of its own: a name of another drug inside
    its name (首乌) does not make the two one herb."""
    sources = getattr(pack, "herb_sources", {}) if pack is not None else {}
    for a, b in ((x, y), (y, x)):
        if a in sources and _materia(a) is None and (m := _materia(b)) is not None \
                and m.chinese != a:
            return True
    return False


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


def _same(kb: Any, pack: Any, x: str, y: str) -> str:
    r = _pair(kb, x, y, _pack_names(pack))
    return "near" if r == "name" and x != y and _pack_distinct(pack, x, y) else r


def _first_materia(kb: Any, forms: Sequence[str]) -> Any:
    for b in forms:
        if (m := _materia(b)) is not None:
            return m
    return None


def _tier(kb: Any, pack: Any, x: str, h: str) -> _Tier | None:
    """How the name ``x`` found in a record is the herb ``h``, if it is.

    * 3, ``allergy``: the same name, or one inside the other where the tables hold both and
      do not give them to different drugs (人参过敏史 and 人参, 丹皮 and 牡丹皮); one herb by
      the seed knowledge base (黄耆 and 黄芪); or the processed forms of one drug (生地黄,
      熟地黄).
    * 2, ``allergy_source``: the materia table lists both names, or what they are processed
      forms of, under one entry, which groups a source's names, not one drug's (海带 and 海藻,
      鸡蛋 and 鸡内金, 熟川军 and 大黄); or the two drugs share a species, by the materia
      table's, the pack's or the knowledge base's species (芍药 and 赤芍, 何首乌 and 首乌藤).
    * 1, ``allergy_near_name``: one name inside the other, but the tables give them to
      different drugs (白附子 and 附子, 土茯苓 and 茯苓), or no table holds the shorter one.
    """
    if x == h:
        return _Tier(3, "allergy", "")
    direct = _same(kb, pack, x, h)
    unknown = direct.split(":", 1)[1] if direct.startswith("unknown:") else ""
    if unknown:
        # one name inside the other, and no table holds one of them: not the same drug by
        # any table. A prescribed name no table holds is read through what it is a
        # processed form of and through the names inside it (``_Matcher``)
        direct = ""
    if direct == "name":
        why = (f"{x}与{h}同为{x}或其炮制品" if _processed(h, x) else
               f"{x}与{h}同为{h}或其炮制品" if _processed(x, h) else
               f"“{x}”与{h}一名含另一名，名表未将二者列为不同药材")
        return _Tier(3, "allergy", why)
    if direct == "kb":
        return _Tier(3, "allergy", f"{x}与{h}为同一药材", "种子知识库")
    x_forms, x_wider = _forms(kb, x)
    h_forms, h_wider = _forms(kb, h)
    if any(_same(kb, pack, a, b) in ("name", "kb")
           for xs, ys in ((x_forms, h_wider), (x_wider, h_forms)) for a in xs for b in ys):
        base = h_forms[-1] if len(h_forms) > 1 or len(x_forms) == 1 else x_forms[-1]
        return _Tier(3, "allergy", f"{x}与{h}同为{base}或其炮制品")
    x_m, h_m = _first_materia(kb, x_forms), _first_materia(kb, h_forms)
    if x_m is not None and h_m is not None and x_m.id == h_m.id:
        via = "" if _materia(x) is not None else f"（{x}为{x_forms[-1]}的炮制品）"
        return _Tier(2, "allergy_source",
                     f"药材名表将{x_forms[-1]}与{h}同列于“{h_m.chinese}”条下（未必同一物种）{via}",
                     "药材名表（materia）")
    (x_sp, x_src), (h_sp, h_src) = _species(kb, pack, x), _species(kb, pack, h)
    shared = [s for s in x_sp if s in h_sp]
    if shared:
        return _Tier(2, "allergy_source",
                     f"{x}与{h}同源（同一物种：{'、'.join(shared)}），未必是同一种药",
                     "；".join(dict.fromkeys(s for s in (x_src, h_src) if s)))
    if direct == "near":
        if x_m is not None and h_m is not None:
            which = (f"药材名表将{x}" + ("" if x_m.chinese == x else f"（{x_m.chinese}）")
                     + f"与{h}列为不同药材")
        elif _identity(kb, x) and _identity(kb, h):
            which = f"种子知识库将{x}与{h}列为不同药材"
        else:
            which = f"知识包将{h}与{x}列为不同药材"
        return _Tier(1, "allergy_near_name", f"二者名称相近，但{which}")
    if unknown and unknown != h:
        return _Tier(1, "allergy_near_name",
                     f"“{x}”与{h}一名含另一名，但“{unknown}”不是名表所载的药名，未必是指{h}")
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
        self.names_cache: dict[str, frozenset[str]] = {}

    def table_names(self, h: str) -> frozenset[str]:
        if h not in self.names_cache:
            self.names_cache[h] = frozenset(_table_names(self.kb, self.pack, h))
        return self.names_cache[h]

    def _herb(self, h: str) -> list[_Path]:
        if h in self.herb_paths:
            return self.herb_paths[h]
        paths = _aliases(self.kb, self.pack, h)
        if not _known(self.kb, h, self.vocab.known | _pack_names(self.pack)):
            # a prescribed name no table knows (黄芪颗粒): the names inside it, as near names
            t, gaps, _ = _compact(h)
            for m in _read(t, gaps, self.kb, self.vocab):
                if m.name != h and not m.written and m.kind == "name":
                    paths.append(_Path(m.name, inner=True))
        self.herb_paths[h] = paths
        return paths

    def match(self, x: str, h: str) -> tuple[_Tier, _Path, _Path] | None:
        key = (x, h)
        if key not in self.cache:
            best = None
            for xp in _aliases(self.kb, self.pack, x):
                for hp in self._herb(h):
                    tier = _tier(self.kb, self.pack, xp.name, hp.name)
                    if tier is None:
                        continue
                    # the strongest tier; of equal ones the plainest reading (the same
                    # name), then one a source vouches for (the knowledge base, a cited
                    # alias) over one the names alone suggest (one inside the other)
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


def _snippet(shown: str, keep: Sequence[int], start: int, end: int) -> str:
    """The record around a name, as written (not as folded), for a record too long to
    quote whole."""
    a, b = max(0, start - 12), min(len(keep), end + 12)
    text = _WS.sub(" ", shown[keep[a]:keep[b - 1] + 1]).strip() if b > a else ""
    return ("…" if a > 0 else "") + text + ("…" if b < len(keep) else "")


def allergy_issues(herbs: Sequence[str], records: Sequence[Any], kb: Any, pack: Any = None
                   ) -> list[tuple[str, str, str, str, tuple[str, ...], str]]:
    """Each prescribed herb against every name the allergy records hold, as
    (id, severity, code, message, herbs, source).

    For a name that stands as a word and is not denied, the tier (``_tier``) decides:
    ``stop`` / ``allergy`` when the record names the herb (by name, by the knowledge base,
    by a cited pack alias, or as a processed form of one drug), ``block`` /
    ``allergy_source`` when the two only share a source (the materia table's entry, or a
    species), ``block`` / ``allergy_near_name`` when one name is inside the other but the
    tables give them to different drugs. The rest are blocks whatever the tier, because the
    record holds the name but may not mean the herb:

    * ``allergy_near_name``: a name inside a longer word the tables do not know (郁金 of
      郁金香, 地黄 of 六味地黄丸, 天麻 of 天麻钩藤颗粒), and a part or abbreviation of a
      herb's name that no table holds (淮山, 决明, 法夏);
    * ``allergy_source``: a species, genus or English common name of the herb's source
      (Panax ginseng, Astragalus, ginger);
    * ``allergy_formula``: a formula the pack or the knowledge base holds, by its name or in
      another product form (四君子汤, 补中益气丸): each herb of its composition, and each
      herb a tier ties to one, asks, because the allergy may be to any of them;
    * ``allergy_denied``: a name the record denies (否认黄芪过敏, 黄芪：无);
    * ``allergy_homonym``: 花粉, which is pollen in a history and 天花粉's short name in a
      prescription, for 天花粉 only, unless the record denies it.

    A record that names nothing (无, 否认过敏史, NKDA) adds nothing. Per herb, a stop stands
    alone; otherwise one block per code, each with every record that gave it.
    """
    vocab = _vocab(kb, pack)
    keys = [(h, _herb_key(h)) for h in herbs]
    keys = [(h, k) for h, k in keys if k]
    parts = _parts(kb, pack, vocab, [k for _, k in keys])
    matcher = _Matcher(kb, pack, vocab)
    found: dict[str, list[tuple[str, str, str, str]]] = {h: [] for h, _ in keys}
    for record in records:
        shown_text = _shown_text(record)
        t, gaps, keep = _compact(_fold_shown(shown_text))
        if not t:
            continue
        shown_all = _WS.sub(" ", shown_text).strip()
        seen: set[tuple] = set()
        for m in _read(t, gaps, kb, vocab, parts, (shown_text, keep)):
            sig = (m.name, m.word, m.denied, m.tolerated, m.written, m.kind, m.of)
            if sig in seen:
                continue
            seen.add(sig)
            shown = shown_all if len(shown_all) <= 40 else _snippet(shown_text, keep,
                                                                     m.start, m.end)
            for h, hk in keys:
                issue = _issue(m, h, hk, shown, matcher)
                if issue:
                    found[h].append(issue)
    out: list[tuple[str, str, str, str, tuple[str, ...], str]] = []
    for h, _ in keys:
        items = found[h]
        if any(sev == "stop" for sev, *_ in items):
            items = [i for i in items if i[0] == "stop"]
        by_code: dict[str, tuple[str, dict[str, None], dict[str, None]]] = {}
        for sev, code, msg, source in items:
            _, msgs, sources = by_code.setdefault(code, (sev, {}, {}))
            msgs.setdefault(msg, None)
            if source:
                sources.setdefault(source, None)
        for code, (sev, msgs, sources) in by_code.items():
            shown_msgs = list(msgs)
            message = "；".join(shown_msgs[:4]) + (f"；另有{len(shown_msgs) - 4}处"
                                                   if len(shown_msgs) > 4 else "")
            out.append((f"{code}:{h}", sev, code, message, (h,), "；".join(sources)))
    return out


def _issue(m: Mention, h: str, hk: str, shown: str, matcher: _Matcher
           ) -> tuple[str, str, str, str] | None:
    """The issue the name ``m`` gives the prescribed herb ``h`` (its key ``hk``), if any."""
    prefix = f"过敏史记录“{shown}”：处方含{h}"
    if m.kind == "name" and m.name in _ALLERGY_HOMONYMS:
        if m.denied:
            return None                  # a denied pollen allergy names no herb
        hm, nm = _materia(hk), _materia(m.name)
        if (hm is not None and nm is not None and hm.id == nm.id) or m.name in hk:
            return ("block", "allergy_homonym",
                    f"{prefix}。过敏史中的“{m.name}”通常指{_ALLERGY_HOMONYMS[m.name]}，"
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
        if m.denied:
            return ("block", "allergy_denied",
                    f"{prefix}。{what}，其所在的分句写有否认的字样，可能是否认对其过敏；{h}的来源物种含"
                    f"{'、'.join(shared)}（{src}），请核实并确认该否认无误后再签署", source)
        return ("block", "allergy_source",
                f"{prefix}。{what}，{h}的来源物种含{'、'.join(shared)}（{src}），"
                f"所记过敏未必是对{h}，请核实", source)
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
        if m.denied:
            return ("block", "allergy_denied",
                    f"{prefix}。{what}，{via}；记录中该方所在的分句写有否认的字样，可能是否认对该方过敏，"
                    "请核实并确认该否认无误后再签署", source)
        return ("block", "allergy_formula",
                f"{prefix}。{what}，{via}；所记过敏可能由方中任一味药引起，未必是{h}，请核实", source)
    if m.kind == "part":
        # a part asks only about the herb whose own names it is a part of: 天花 of 天花粉 does
        # not reach 瓜蒌 through the species the two share
        if m.of not in matcher.table_names(hk):
            return None
        hit = matcher.match(m.of, hk)
        if hit is None:
            return None
        tier, xp, hp = hit
        why = _join(xp.why, hp.why, tier.why)
        where = f"记录中的“{m.word}”含有“{m.name}”的字样，" if m.word else ""
        return ("block", "allergy_near_name",
                f"{prefix}。{where}“{m.name}”是名表所载“{m.of}”的一部分或简写，但不是名表所载的药名，"
                f"未必是指{m.of}" + (f"（{why}）" if why else "") + f"，请核实所记过敏是否与{h}有关",
                _join(xp.source, hp.source, tier.source))
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
    if m.denied and m.tolerated:
        return ("block", "allergy_denied",
                f"{prefix}。记录中{m.name}所在的分句说一直在用{m.name}，其后未写任何反应，"
                f"可能是说对{m.name}不过敏" + (f"（{why}）" if why else "")
                + "，请核实并确认无误后再签署", source)
    if m.denied:
        return ("block", "allergy_denied",
                f"{prefix}。记录中{m.name}所在的分句写有否认的字样，可能是否认对{m.name}过敏"
                + (f"（{why}）" if why else "") + "，请核实并确认该否认无误后再签署", source)
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
    return "、".join(dict.fromkeys(m.name for m in mentions(entry) if m.kind != "part"))
