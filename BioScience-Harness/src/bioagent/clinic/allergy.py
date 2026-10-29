"""Reading an allergy history against the herbs of a prescription (过敏史核查).

A history is not read through a list of wordings (过敏史：X, 对X过敏, X过敏史 …). Every
wording such a list missed was a silent pass, and histories are written in more ways than
any list holds: 患者对国老过敏, 3年前北芪过敏, 北芪/人参过敏, 变应原：北芪, allergic to huangqi.
The whole record is normalised and *scanned* for every name the tables know, wherever it
stands, and each name found is then read in its place.

Normalising (``fold``)
    NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens, NUL and the
    other control characters are removed; traditional characters are folded with the
    materia table's own table (``materia.to_simplified``: 黃耆 → 黄耆, 人參 → 人参) and, for
    the words a history writes around a name, a few more that table leaves (過敏 → 过敏,
    對 → 对, 否認 → 否认); lower case. Whitespace is dropped for the scan, so 北 芪 is 北芪,
    but it is remembered as a boundary.

Scanning (``mentions``)
    for the seed knowledge base's herb names and aliases (黄耆, 北芪, 国老, and their pinyin
    and Latin names), the materia table's names (``materia.names_in``, and every entry's
    own names, one character included: 姜, 枣), the pack's herbs and its cited aliases
    (``herb_aliases`` in the pack: 夜交藤, 川黄柏), and the prescribed herbs' own names and
    the parts of them (贝母 of 浙贝母). At each position the longest name is taken, and a
    name inside a longer one found before it (杏仁 in 苦杏仁) is that one's.

Reading each name in its place
    * it *stands as a word* when what is before and after it is a boundary (the start or
      end of the record, punctuation, a space, a digit, Latin letters), another name
      (黄耆人参过敏), or one of the words a history writes around a name (对, 患者, 自诉, 年前,
      过敏, 过敏史, 及, 等, 均, 后, 皮疹 …);
    * otherwise it is *inside a longer word* the tables do not know: 郁金 in 郁金香, 地黄 in
      六味地黄丸, 黄连 in 双黄连, 防风 in 预防风疹疫苗; so is a name whose first character ends
      an everyday word (于术 after 对于, 白及 after 蛋白). A longer word that the materia
      table or the knowledge base resolves to the same drug is that drug (甘草片, 山药粉,
      炙北芪);
    * it is *denied* when its clause opens with a denial (否认, 无, 没有, 未, NKDA, no …) that
      no 过敏, 史, 但, 出现 or "except" closes before the name (否认黄芪过敏, 无黄芪过敏史,
      否认青霉素、黄芪过敏), or when a denial follows it (黄芪：无, 黄芪（-）, 黄芪不过敏). A clause
      ends at punctuation other than 、 and /, and at a space between two Chinese
      characters, so 无药物过敏史但对黄芪过敏 and 食物过敏史：无 药物过敏史：黄芪 deny nothing
      about 黄芪. A denial that names nothing adds nothing, and it never hides a name in
      another clause.

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

__all__ = ["fold", "Mention", "mentions", "allergy_issues", "ALLERGY_CODES"]

ALLERGY_CODES = ("allergy", "allergy_source", "allergy_near_name", "allergy_homonym",
                 "allergy_denied")


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
    (熟地黄 → 地黄, 炙黄耆 → 黄耆, 鲜生地 → 生地), or ``name`` itself."""
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
    from ..sources.materia import MATERIA
    return frozenset(n for e in MATERIA.values() for n in (e.chinese, *e.aliases))


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
def _pair(kb: Any, x: str, y: str) -> str:
    x_kb, y_kb = _identity(kb, x), _identity(kb, y)
    same_kb = bool(x_kb & y_kb)
    inside = x == y or (len(x) >= 2 and len(y) >= 2 and (x in y or y in x))
    if inside and not same_kb:
        x_m, y_m = _materia(x), _materia(y)
        if (((x_m is not None and y_m is not None and x_m.id != y_m.id) or (x_kb and y_kb))
                and not (_processed(x, y) or _processed(y, x))):
            return "near"
    if inside:
        return "name"
    return "kb" if same_kb else ""


def _one_drug(kb: Any, x: str, y: str) -> str:
    """How the names ``x`` and ``y`` are one drug: ``"name"`` (the same name, or one inside
    the other where the name tables do not give them to different drugs), ``"kb"`` (the
    seed knowledge base gives both to one herb), ``"near"`` (one inside the other, but the
    tables give them to different drugs and neither is the other processed) or ``""``.
    Two names are compared here; a name of one character is not looked for inside another
    (the 姜 of 生姜 in 沙姜): the scan reads such a character in its place instead."""
    return _pair(kb, x, y)


#: Names that an allergy record uses for something other than the drug the materia table
#: files them under. In a prescription 花粉 is 天花粉's short name; in an allergy history 花粉
#: (花粉过敏, 对花粉过敏) is pollen, the most common entry of all. Such an entry is checked
#: against the drug it may name only, with a block that asks which was meant, and not
#: carried to that drug's species-mates (瓜蒌, from the same Trichosanthes).
_ALLERGY_HOMONYMS: Mapping[str, str] = {"花粉": "植物花粉"}


# ============================================================== normalising
#: Traditional forms of the words a history writes around a name that the materia table's
#: fold (``materia.to_simplified``, cut to the characters of drug names) does not cover:
#: 過敏, 對, 否認, 不詳, 體質, 變應原, 陰性, 後, 皮疹 …. One character for one, like that table.
_WORD_T2S = str.maketrans(
    "過對認確歷應嚴異體質詳從後現癢腫難訴經類劑種與陰錄記變顯狀嘔臨診檢驗試蕁暈氣熱屬僅雖卻並還"
    "稱為係個這來時間週歲幾樣麼說話請際況們嗎報導誘數較極頻復敘",
    "过对认确历应严异体质详从后现痒肿难诉经类剂种与阴录记变显状呕临诊检验试荨晕气热属仅虽却并还"
    "称为系个这来时间周岁几样么说话请际况们吗报导诱数较极频复叙")
_WS = re.compile(r"\s+")


def _visible(text: str) -> str:
    """``text`` without the characters that show nothing but can split a name: format
    characters (zero-width space and joiners, the byte-order mark, soft hyphen, bidi
    marks), surrogates, and controls other than tab and line breaks (NUL)."""
    if text.isascii() and text.isprintable():
        return text
    return "".join(c for c in text
                   if not ((cat := unicodedata.category(c)) in ("Cf", "Cs")
                           or (cat == "Cc" and c not in "\t\n\r")))


def fold(text: Any) -> str:
    """The record as the scan reads it: visible characters only, NFKC, traditional
    characters simplified, lower case. One character stays one character, so a position
    in the result is a position in the visible, NFKC text."""
    from ..sources.materia import to_simplified
    s = _visible(str(text or ""))
    s = unicodedata.normalize("NFKC", s)
    s = _visible(s)                    # NFKC can compose nothing invisible, but be sure
    return to_simplified(s).translate(_WORD_T2S).lower()


def _shown(record: Any) -> str:
    """The record as the message quotes it: as written, visible, spaces collapsed."""
    s = unicodedata.normalize("NFKC", _visible(str(record or "")))
    return _WS.sub(" ", s).strip()


def _cjk(c: str) -> bool:
    o = ord(c)
    return (0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3134F
            or o == 0x3007)


def _all_cjk(name: str) -> bool:
    return bool(name) and all(_cjk(c) for c in name)


# ============================================================== record words
#: Words a history writes before a name. A name with one of these just before it stands as
#: a word: 对国老, 患者对国老, 自诉国老, 3年前北芪, 服用北芪, 过敏药品北芪 (colon dropped),
#: 及人参. Missing a word here never hides a name: the name is then read as inside a longer
#: word, which asks the practitioner (a block) instead of stopping.
_BEFORE = frozenset("""
对 对于 有 有过 曾 曾经 曾有 既往 以往 以前 之前 从前 早年 幼时 儿时 年前 月前 周前 天前 日前 前 年 月 后
患者 病人 本人 其 自诉 自述 诉 述 主诉 称 自称 代诉 家属诉
可疑 疑似 怀疑 疑 似 明确 已知 确诊 确认 证实 考虑
服用 服 用 使用 口服 外用 外敷 接触 吃 食用 饮用 注射 静滴 煎服 内服 用药 吃了 用了 服了
出现 发现 发生 引起 导致
过敏 过敏史 过敏原 过敏源 过敏物 过敏物质 过敏药物 过敏药品 过敏药 过敏食物 过敏反应 过敏情况
过敏名称 致敏原 致敏物 致敏药物 致敏药 变应原 变态反应 药敏 药敏史
药物 药品 食物 中药 中药材 草药 药材 饮片 中成药 成分 药名 名称 情况 记录
及 和 与 或 或者 以及 并 跟 同 也 均 都 亦 还 另 另外 此外 再 且 而且
但 但是 而 唯 只 仅 除 除了 可是 不过 然而
是 为 系 含 包括 例如 如 即 等 史 原 源 物 品 药 名
严重 轻度 明显 多次 反复 不适 特殊
阳性 阴性
无 否认 没有 未 未见 未发现 从未 未曾 并无 不 非 排除
""".split())

#: Words a history writes after a name: 国老过敏, 北芪均过敏, 黄耆及人参, 国老等, 北芪后出现皮疹.
_AFTER = frozenset("""
过敏 过 敏 史 及 和 与 或 或者 以及 并 跟 同 等 均 都 也 亦 还 另
后 之后 以后 时 期间 服后 用后 皮疹 瘙痒 痒 红肿 肿 荨麻疹 风团 起疹 出疹 疹子 红疹 皮炎
反应 不良反应 严重 引起 引发 致 导致 所致 诱发 出现 发生 有 类 制剂 及其 其 阳性 阴性 皮试
曾 年 会 可 能 者 已 中毒 不耐受 不耐 不适 腹泻 呕吐 恶心 头晕 心慌 胸闷 气短 气喘 哮喘 喘
休克 水肿 呼吸困难 无 否 否认 不 未 没有 除外 例外 以外 外 药 药物 成分 相关 为主 为 是
""".split())

_BEFORE_MAX = max(map(len, _BEFORE))
_AFTER_MAX = max(map(len, _AFTER))

#: Everyday words whose last character starts a name: in 对于术后 the 于 is 对于's, not the
#: first character of 于术 (白术), and in 鸡蛋白及 the 白 is 蛋白's (protein), not 白及's. A
#: name after one of them is read as inside a longer word, whatever stands before it.
_EVERYDAY = frozenset("""
对于 关于 由于 至于 属于 位于 等于 用于 大于 小于 基于 在于 便于 易于 过于 终于 处于
蛋白 空白 苍白 明白 坦白 表白 洁白 雪白 黑白 灰白 清白
""".split())

#: Words that may open a clause before its denial: 患者否认, 过敏史无, 既往无.
_CLAUSE_LEADS = tuple(sorted("""
患者 病人 本人 其 既往 平素 自诉 自述 诉 述 家属诉 过敏史 药物过敏史 食物过敏史 过敏 药物 食物 对
""".split(), key=len, reverse=True))
#: Denials. An ASCII one is a whole word (no, not nothing).
_NEGATIONS = tuple(sorted("""
否认 没有 没 未曾 未见 未发现 未诉 未述 未 从未 并无 无明确 无 非 排除
no not nkda nka nkfa denies denied deny negative without none nil
""".split(), key=len, reverse=True))
#: Words after a denial that close it before a name: 无过敏史 (a denial of its own),
#: 无药物过敏史但, NKDA except.
_CLOSERS = re.compile(r"过敏|史|但|除|不过|然而|却|唯|可是|出现|发生|诱因"
                      r"|except|but|however|apart|besides")
#: A denial written after the name: 黄芪：无, 黄芪过敏史：否, 黄芪（-）, 黄芪皮试阴性, 黄芪不过敏.
_DENIED_AFTER = re.compile(
    r"(?:过敏(?:史|反应)?|皮试)?(?::|∶)?[(\[]?"
    r"(?:无过敏|不过敏|未过敏|没有过敏|否认|无|否|阴性|没有|未见|-|－|−|—|negative|neg|no|none)"
    r"[)\]]?(?![㐀-鿿a-z0-9])")
#: Marks that do not end a clause: a list goes on after 、 and /, and 否认青霉素、黄芪过敏
#: denies 黄芪 too.
_CLAUSE_MARK = re.compile(r"[^\w、/·\-]|_")


# ============================================================== the scan vocabulary
@dataclass(frozen=True)
class _Vocab:
    trie: dict
    names: frozenset[str]
    ascii: Mapping[str, tuple[tuple[str, str], ...]]   # joined Latin/pinyin -> (中文名, how)
    ascii_max: int


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


_VOCABS: dict[tuple[int, str], _Vocab] = {}


def _vocab(kb: Any, pack: Any = None) -> _Vocab:
    """Every name the tables know, folded: the seed knowledge base's herbs and processed
    forms (Chinese, pinyin, Latin), the materia table's entries and Latin names, the
    pack's herbs and its cited aliases."""
    key = (id(kb), getattr(pack, "digest", "") if pack is not None else "")
    if key in _VOCABS:
        return _VOCABS[key]
    from ..sources.materia import MATERIA
    cjk: set[str] = set()
    ascii_names: dict[str, set[tuple[str, str]]] = {}
    longest = 1

    def add_ascii(name: str, chinese: str, how: str) -> None:
        nonlocal longest
        joined, words = _ascii_key(name)
        if len(joined) >= 4 and chinese:
            ascii_names.setdefault(joined, set()).add((chinese, how))
            longest = max(longest, words)

    for entity in (*getattr(kb, "herbs", {}).values(), *getattr(kb, "processed", {}).values()):
        herb = kb.herbs.get(getattr(entity, "herb_id", "") or entity.id) \
            if hasattr(kb, "herbs") else None
        chinese = fold(getattr(herb or entity, "chinese", "") or "")
        for n in getattr(entity, "names", ()):
            f = fold(n)
            if _all_cjk(normalise(f)):
                cjk.add(normalise(f))
            elif f.isascii():
                add_ascii(f, chinese, "种子知识库所载的拼音名或拉丁名")
    for entry in MATERIA.values():
        for n in (entry.chinese, *entry.aliases):
            f = normalise(fold(n))
            if _all_cjk(f):
                cjk.add(f)
        add_ascii(entry.latin, fold(entry.chinese), "药材名表所载的拉丁名")
    if pack is not None:
        cjk.update(f for h in pack.herbs if _all_cjk(f := normalise(fold(h))))
        cjk.update(f for a in getattr(pack, "herb_aliases", {})
                   if _all_cjk(f := normalise(fold(a))))
    vocab = _Vocab(_trie(cjk), frozenset(cjk),
                   {k: tuple(sorted(v)) for k, v in ascii_names.items()}, longest)
    if len(_VOCABS) > 8:
        _VOCABS.clear()
    _VOCABS[key] = vocab
    return vocab


# ============================================================== the scan
@dataclass(frozen=True)
class Mention:
    """A name found in a record, read in its place.

    ``name`` is the name as the tables write it (the Chinese name a Latin or pinyin name is
    of); ``written`` the record's own text when it differs (huangqi, astragali radix).
    ``word`` is the longer word the name stands inside ("" when it stands as a word):
    郁金香 for 郁金. ``denied`` says the record denies an allergy to it.
    """

    name: str
    start: int
    end: int
    written: str = ""
    how: str = ""
    word: str = ""
    denied: bool = False


def _compact(text: str) -> tuple[str, frozenset[int]]:
    """``text`` without whitespace, and the positions in the result that had some before."""
    parts = _WS.split(text)
    gaps, pos = set(), 0
    for p in parts[:-1]:
        pos += len(p)
        gaps.add(pos)
    return "".join(parts), frozenset(gaps)


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


def _spans(t: str, tries: Sequence[dict]) -> list[tuple[int, int]]:
    """(start, end) of the longest known name at each position, a name inside a longer one
    that starts before it dropped."""
    from ..sources.materia import names_in
    found: dict[int, int] = {}
    for trie in tries:
        if not trie:
            continue
        for i, c in enumerate(t):
            if c in trie and (e := _longest(trie, t, i)) > found.get(i, i):
                found[i] = e
    for m in names_in(t):
        # an identity word before a known name that makes no known name (白首乌): the known
        # name is read inside the longer word, like any other
        s = m.start + 1 if m.drug_id is None and m.contains else m.start
        e = m.start + len(m.name)
        if e > found.get(s, s):
            found[s] = e
    out, reach = [], -1
    for s in sorted(found):
        e = found[s]
        if e <= reach:
            continue
        out.append((s, e))
        reach = max(reach, e)
    return out


def _ascii_spans(t: str, gaps: frozenset[int], vocab: _Vocab
                 ) -> list[tuple[int, int, str, str]]:
    """(start, end, 中文名, how) of each Latin or pinyin name, as whole words: astragali
    radix, astragaliradix, huangqi, huang qi."""
    if not vocab.ascii:
        return []
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
    out: list[tuple[int, int, str, str]] = []
    a = 0
    while a < len(toks):
        hit = None
        for k in range(min(vocab.ascii_max, len(toks) - a), 0, -1):
            run = toks[a:a + k]
            if any(t[q[1]:r[0]] not in ("", "-", ".") for q, r in zip(run, run[1:])):
                continue
            joined = "".join(t[s:e] for s, e in run)
            if joined in vocab.ascii:
                hit = (k, run[0][0], run[-1][1], vocab.ascii[joined])
                break
        if hit is None:
            a += 1
            continue
        k, s, e, names = hit
        out += [(s, e, chinese, how) for chinese, how in names]
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


def _same_drug(kb: Any, word: str, name: str) -> bool:
    """``word``, a longer word holding ``name``, is the same drug by the tables: the
    materia table resolves it to the same entry (甘草片, 山药粉, 生鸡蛋), the knowledge base
    to the same herb (炙北芪), or it is ``name`` processed."""
    from ..sources.materia import resolve_name
    m_word = resolve_name(word)
    if m_word is not None and m_word == resolve_name(name):
        return True
    i_word = _identity(kb, word)
    return bool(i_word) and i_word == _identity(kb, name) or _processed(word, name)


def _read(t: str, gaps: frozenset[int], kb: Any, vocab: _Vocab, extra: dict | None
          ) -> list[Mention]:
    """Every name in the compact, folded record ``t``, read in its place."""
    cjk = _spans(t, (vocab.trie, extra or {}))
    latin = _ascii_spans(t, gaps, vocab)
    starts = {s for s, _ in cjk} | {s for s, *_ in latin}
    ends = {e for _, e in cjk} | {e for _, e, *_ in latin}
    n = len(t)
    bounds = sorted({0, *(m.end() for m in _CLAUSE_MARK.finditer(t)),
                     *(g for g in gaps if 0 < g < n and _cjk(t[g - 1]) and _cjk(t[g]))})
    closers = [m.start() for m in _CLOSERS.finditer(t)]

    def denied(s: int, e: int) -> bool:
        cs = bounds[bisect_right(bounds, s) - 1]
        i = cs
        for _ in range(4):
            lead = _starting(t, i, _CLAUSE_LEADS, s, gaps)
            if not lead:
                break
            i += len(lead)
        neg = _starting(t, i, _NEGATIONS, s, gaps)
        if neg:
            j = i + len(neg)
            k = bisect_left(closers, j)
            if k == len(closers) or closers[k] >= s:
                return True
        return _DENIED_AFTER.match(t, e) is not None

    out: list[Mention] = []
    for s, e in cjk:
        spaced = any(p in gaps for p in range(s + 1, e))    # 北 芪, 过 敏 史 ： 北 芪

        def left_ok(a: int) -> bool:
            if 0 < a < n and t[a - 1:a + 1] in _EVERYDAY:
                return False
            return (a == 0 or (not spaced and a in gaps) or not _cjk(t[a - 1]) or a in ends
                    or _ends_with(t, a, _BEFORE, _BEFORE_MAX))

        def right_ok(b: int) -> bool:
            return (b >= n or (not spaced and b in gaps) or not _cjk(t[b]) or b in starts
                    or _starts_with(t, b, _AFTER, _AFTER_MAX))

        name = t[s:e]
        a, b = s, e
        while not left_ok(a) and s - a < 12:
            a -= 1
        while not right_ok(b) and b - e < 12:
            b += 1
        if (a, b) == (s, e):
            out.append(Mention(name, s, e, denied=denied(s, e)))
            continue
        word = t[a:b]
        if _same_drug(kb, word, name):
            out.append(Mention(word, a, b, denied=denied(a, b)))
            continue
        shown = ("…" if not left_ok(a) else "") + word + ("…" if not right_ok(b) else "")
        out.append(Mention(name, s, e, word=shown))
    for s, e, chinese, how in latin:
        written = "".join((" " if p in gaps and p > s else "") + t[p] for p in range(s, e))
        out.append(Mention(chinese, s, e, written=written, how=how, denied=denied(s, e)))
    return sorted(out, key=lambda m: (m.start, m.end))


def _parts(names: Iterable[str]) -> dict:
    """A trie of the prescribed herbs' names and every part of two characters or more:
    贝母 of 浙贝母, 益母 of 益母草. A record naming only a part names the herb as much as the
    tables allow (``_tier``: one name inside the other)."""
    parts: set[str] = set()
    for h in names:
        if 2 <= len(h) <= 8 and _all_cjk(h) and h not in _BEFORE and h not in _AFTER:
            parts.add(h)
            # a part that is a word of the history itself (过敏 of a herb written 黄芪过敏) is
            # no name
            parts.update(p for i in range(len(h)) for j in range(i + 2, len(h) + 1)
                         if (p := h[i:j]) != h and p not in _BEFORE and p not in _AFTER)
    return _trie(parts)


def mentions(record: Any, *, kb: Any = None, pack: Any = None,
             herbs: Sequence[str] = ()) -> list[Mention]:
    """The names an allergy record holds, each read in its place (see the module doc)."""
    kb = kb if kb is not None else _default_kb()
    t, gaps = _compact(fold(record))
    return _read(t, gaps, kb, _vocab(kb, pack), _parts(_herb_key(h) for h in herbs))


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


def _species(pack: Any, name: str) -> tuple[tuple[str, ...], str]:
    m = _materia(name)
    if m is not None:
        return tuple(m.species_names), "药材名表（materia）"
    sp = (getattr(pack, "herb_sources", {}) or {}).get(name) if pack is not None else None
    return (tuple(sp[0]), sp[1]) if sp else ((), "")


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


def _same(kb: Any, pack: Any, x: str, y: str) -> str:
    r = _pair(kb, x, y)
    return "near" if r == "name" and x != y and _pack_distinct(pack, x, y) else r


def _tier(kb: Any, pack: Any, x: str, h: str) -> _Tier | None:
    """How the name ``x`` found in a record is the herb ``h``, if it is.

    * 3, ``allergy``: the same name, or one inside the other where the tables do not give
      them to different drugs (人参过敏史 and 人参, 贝母 and 浙贝母); one herb by the seed
      knowledge base (黄耆 and 黄芪); or the processed forms of one drug (生地黄, 熟地黄).
    * 2, ``allergy_source``: the materia table lists both names under one entry, which
      groups a source's names, not one drug's (海带 and 海藻, 鸡蛋 and 鸡内金); or the two
      drugs share a species, by the materia table's or the pack's species (芍药 and 赤芍,
      何首乌 and 首乌藤).
    * 1, ``allergy_near_name``: one name inside the other, but the tables give them to
      different drugs (白附子 and 附子, 土茯苓 and 茯苓).
    """
    if x == h:
        return _Tier(3, "allergy", "")
    direct = _same(kb, pack, x, h)
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
    x_m, h_m = _materia(x), _materia(h)
    if x_m is not None and h_m is not None and x_m.id == h_m.id:
        return _Tier(2, "allergy_source",
                     f"药材名表将{x}与{h}同列于“{h_m.chinese}”条下（未必同一物种）",
                     "药材名表（materia）")
    (x_sp, x_src), (h_sp, h_src) = _species(pack, x), _species(pack, h)
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
    return None


@dataclass(frozen=True)
class _Path:
    name: str
    why: str = ""
    source: str = ""
    inner: bool = False          # a name inside the prescribed herb's unknown name


def _aliases(kb: Any, pack: Any, name: str) -> list[_Path]:
    """``name``, and what the pack's cited aliases and the knowledge base say it is."""
    out = [_Path(name)]
    alias = (getattr(pack, "herb_aliases", {}) or {}).get(name) if pack is not None else None
    if alias:
        herb, source = alias
        out.append(_Path(herb, f"{name}为{herb}的别名（{source}）", source))
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

    def _herb(self, h: str) -> list[_Path]:
        if h in self.herb_paths:
            return self.herb_paths[h]
        from ..sources.materia import resolve_name
        paths = _aliases(self.kb, self.pack, h)
        known = (h in self.vocab.names or resolve_name(h) is not None
                 or bool(_identity(self.kb, h)))
        if not known:
            # a prescribed name no table knows (黄芪颗粒): the names inside it, as near names
            t, gaps = _compact(h)
            for m in _read(t, gaps, self.kb, self.vocab, None):
                if m.name != h and not m.written:
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


def _join(*parts: str) -> str:
    return "；".join(p for p in parts if p)


def allergy_issues(herbs: Sequence[str], records: Sequence[Any], kb: Any, pack: Any = None
                   ) -> list[tuple[str, str, str, str, tuple[str, ...], str]]:
    """Each prescribed herb against every name the allergy records hold, as
    (id, severity, code, message, herbs, source).

    For a name that stands as a word and is not denied, the tier (``_tier``) decides:
    ``stop`` / ``allergy`` when the record names the herb (by name, by the knowledge base,
    by a cited pack alias, or as a processed form of one drug), ``block`` /
    ``allergy_source`` when the two only share a source, ``block`` / ``allergy_near_name``
    when one name is inside the other but the tables give them to different drugs. A name
    inside a longer word the tables do not know (郁金 of 郁金香, 地黄 of 六味地黄丸), and a
    name the record denies (否认黄芪过敏), are blocks whatever the tier (``allergy_near_name``
    and ``allergy_denied``): the record holds the name, but may not mean the herb. 花粉 is
    pollen in a history and 天花粉's short name in a prescription: a block for 天花粉 only
    (``allergy_homonym``). A record that names nothing (无, 否认过敏史, NKDA) adds nothing.

    Per herb, a stop stands alone; otherwise one block per code, each with every record
    that gave it.
    """
    vocab = _vocab(kb, pack)
    keys = [(h, _herb_key(h)) for h in herbs]
    keys = [(h, k) for h, k in keys if k]
    extra = _parts(k for _, k in keys)
    matcher = _Matcher(kb, pack, vocab)
    found: dict[str, list[tuple[str, str, str, str]]] = {h: [] for h, _ in keys}
    for record in records:
        shown_all = _shown(record)
        t, gaps = _compact(fold(record))
        if not t:
            continue
        seen: set[tuple] = set()
        for m in _read(t, gaps, kb, vocab, extra):
            sig = (m.name, m.word, m.denied, m.written)
            if sig in seen:
                continue
            seen.add(sig)
            shown = shown_all if len(shown_all) <= 40 else (
                ("…" if m.start > 12 else "") + t[max(0, m.start - 12):m.end + 12]
                + ("…" if m.end + 12 < len(t) else ""))
            for h, hk in keys:
                prefix = f"过敏史记录“{shown}”：处方含{h}"
                if m.name in _ALLERGY_HOMONYMS:
                    hm, nm = _materia(hk), _materia(m.name)
                    if (hm is not None and nm is not None and hm.id == nm.id) or m.name in hk:
                        found[h].append((
                            "block", "allergy_homonym",
                            f"{prefix}。过敏史中的“{m.name}”通常指{_ALLERGY_HOMONYMS[m.name]}，"
                            f"但处方中{h}也简称“{m.name}”，请核实所记过敏是否为{h}",
                            "药材名表（materia）"))
                    continue
                hit = matcher.match(m.name, hk)
                if hit is None:
                    continue
                tier, xp, hp = hit
                read_as = (f"“{m.written}”即{m.name}（{m.how}）" if m.written else "")
                why = _join(read_as, xp.why, hp.why, tier.why)
                source = _join(*dict.fromkeys(s for s in (xp.source, hp.source, tier.source)
                                              if s))
                if hp.inner:
                    found[h].append((
                        "block", "allergy_near_name",
                        f"{prefix}。处方中的“{h}”不是名表所载的药名，其中含有“{hp.name}”的字样"
                        f"（{why or f'记录中写有{m.name}'}），请核实所记过敏是否与之有关", source))
                elif m.word:
                    found[h].append((
                        "block", "allergy_near_name",
                        f"{prefix}。记录中的“{m.word}”含有“{m.name}”的字样，但“{m.word.strip('…')}”"
                        f"不是名表所载的药名，未必是指{m.name}"
                        + (f"（{why}）" if why else "") + f"，请核实所记过敏是否与{h}有关",
                        source))
                elif m.denied:
                    found[h].append((
                        "block", "allergy_denied",
                        f"{prefix}。记录中{m.name}所在的分句写有否认的字样，可能是否认对{m.name}过敏"
                        + (f"（{why}）" if why else "")
                        + "，请核实并确认该否认无误后再签署", source))
                elif tier.rank == 3:
                    found[h].append(("stop", "allergy",
                                     prefix + (f"（{why}）" if why else ""), source))
                elif tier.rank == 2:
                    found[h].append(("block", "allergy_source", f"{prefix}。{why}，请核实",
                                     source))
                else:
                    found[h].append(("block", "allergy_near_name",
                                     f"{prefix}。{why}，请核实所记过敏为何药", source))
    out: list[tuple[str, str, str, str, tuple[str, ...], str]] = []
    for h, _ in keys:
        items = found[h]
        if any(sev == "stop" for sev, *_ in items):
            items = [i for i in items if i[0] == "stop"]
        by_code: dict[str, tuple[str, list[str], list[str]]] = {}
        for sev, code, msg, source in items:
            _, msgs, sources = by_code.setdefault(code, (sev, [], []))
            if msg not in msgs:
                msgs.append(msg)
            if source and source not in sources:
                sources.append(source)
        for code, (sev, msgs, sources) in by_code.items():
            message = "；".join(msgs[:4]) + (f"；另有{len(msgs) - 4}处" if len(msgs) > 4 else "")
            out.append((f"{code}:{h}", sev, code, message, (h,), "；".join(sources)))
    return out


def _allergen(entry: str) -> str:
    """The names one record holds, joined with 、, or "" when it names none (无, 否认过敏史,
    NKDA). Kept for the tools that printed it; the check reads every name in its place
    (``mentions``)."""
    return "、".join(dict.fromkeys(m.name for m in mentions(entry)))
