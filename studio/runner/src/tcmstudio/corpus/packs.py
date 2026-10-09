"""What the published corpus may carry, and on what basis (docs/V2.md §11.1).

``PACKS`` is the allowlist: each pack names the sources it may draw on, its licence, the
basis on which science.impf.ai publishes it, the strongest evidence it can carry, and the
limits every envelope that reads it must repeat. ``NEVER_PUBLISHED`` is the denylist, with
the reason each source card records. The build refuses a pack whose sources are not all
allowlisted for it, any source on the denylist, and any source whose licence is not an
open licence unless the pack carries a recorded owner decision (``check_sources``).
Users reach the denied sources from the original sites, through the runner
(``tcmdb fetch``) or their own browser; nothing derived from them is published here.

Nothing here reads a clock: ``DATA_DATE`` is the date of the data, set by hand when the
inputs change, and it names the snapshot.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

__all__ = ["SCHEMA", "DATA_DATE", "PUBLIC_SITE", "LATEST_URL", "ATTRIBUTION_URL",
           "ROWS_PER_CHUNK", "OWNER_DECISION_FORMULAS", "PACKS", "PACK_ORDER", "NEVER_PUBLISHED",
           "NEVER_PUBLISHED_REASON", "OPEN_CLASSES", "GateError", "source_key", "denied",
           "check_sources", "pack_meta", "limitations"]

SCHEMA = "tcmstudio.corpus/1"
#: The date of the data in this snapshot (not the build clock). Change it when an input
#: changes; it is part of the snapshot id.
DATA_DATE = "2026-10-09"
PUBLIC_SITE = "https://science.impf.ai"
LATEST_URL = f"{PUBLIC_SITE}/corpus/latest.json"
#: Formula-table rows per object: ``formulas/rows/<rowno // ROWS_PER_CHUNK>.json``.
ROWS_PER_CHUNK = 500
ATTRIBUTION_URL = f"{PUBLIC_SITE}/attribution.html"

#: The owner's decision to publish the formula table, verbatim (V2 §11.1).
OWNER_DECISION_FORMULAS: dict[str, str] = {
    "decided_by": "IMPF-AI（science.impf.ai 站点所有者）",
    "date": "2026-10-08",
    "basis": "所有者决定在 science.impf.ai 公开发布；原表未声明来源与许可",
    "licence": "LicenseRef-owner-published-unstated",
    "commercial": "unknown",
}

#: Licence classes (``bioagent.tcmdb.spec.licence_class``) that may be published on their
#: own terms. Anything else needs a recorded owner decision.
OPEN_CLASSES = ("open", "share-alike")


def _lim(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": " ".join(en.split())}


PACKS: dict[str, dict[str, Any]] = {
    "core": {
        "title": {"zh": "中医核心知识（种子语料 + 药材表 + 临床知识包）",
                  "en": "Core TCM knowledge (seed corpus, materia table, clinic pack)"},
        "licence": "MIT",
        "attribution": {
            "zh": "TCMScience 仓库自有整理（MIT）：种子语料、药材表（物种经 NCBI Taxonomy 核对，公有领域）、"
                  "临床知识包草案（转录自《中医诊断学》、GB/T 16751.2-2021、《方剂学》与《中华人民共和国药典》"
                  "2020年版一部，未经执业中医师审核）。",
            "en": "Curated in the TCMScience repository (MIT): the seed corpus, the materia table "
                  "(species checked against NCBI Taxonomy, public domain) and the draft clinic pack "
                  "(transcribed from 《中医诊断学》, GB/T 16751.2-2021, 《方剂学》 and the Chinese "
                  "Pharmacopoeia 2020 Vol. I; not reviewed by a licensed TCM practitioner)."},
        "sources": ("tcmscience-seed", "tcmscience-materia", "ncbi-taxonomy",
                    "tcmscience-clinic-pack"),
        "publication": {"basis": "open-licence"},
        "evidence_ceiling": "EXPERT_EXPERIENCE",
        "evidence_note": {
            "zh": "种子记录保留各自的证据等级（经典文献记载、名医经验/专家共识）；药材表只提供名称与物种；"
                  "临床知识包为未经审核的草案。",
            "en": "Seed records keep their own tiers (classical text, expert experience); the "
                  "materia layer gives identity only; the clinic layer is an unreviewed draft."},
        "limitations": (
            _lim("种子记录保留各自的证据等级；经典记载是「记载」，不是疗效或安全性的临床证据。",
                 "Seed records keep their tiers: a classical record is an attribution, not "
                 "clinical evidence of efficacy or safety."),
            _lim("药材表层只有名称、物种与药用部位；种子语料以外的药材没有性味、归经、功效记录（为空，不作推定）。",
                 "The materia layer is identity only (names, species, part): herbs outside the "
                 "seed have no nature, meridians or actions recorded (left empty, never "
                 "defaulted)."),
            _lim("临床知识包层是草案，未经执业中医师审核。",
                 "The clinic layer is a draft, not reviewed by a licensed TCM practitioner."),
            _lim("来源未说明的值保持空缺：不默认「无毒」、不默认剂型、不推定严重程度（临床知识包派生的十八反/十九畏"
                 "记录严重程度为 unstated）。",
                 "A value the source does not state stays unstated: no default toxicity, no "
                 "default dosage form, no invented severity (clinic-derived 十八反/十九畏 records "
                 "carry severity 'unstated')."),
            _lim("无记录不等于安全，语料中未找到不等于不存在。",
                 "No record is not safety, and not found in the corpus is not absence."),
        ),
    },
    "formulas": {
        "title": {"zh": "中医方剂数据表（84,294 条）", "en": "Formula table (84,294 rows)"},
        "licence": "LicenseRef-owner-published-unstated",
        "attribution": {
            "zh": "中医方剂数据表.xlsx：原表未声明来源与许可；由站点所有者决定公开发布（见下方决定原文）。"
                  "商业用途未知，商业用途的调用会被拒绝。",
            "en": "中医方剂数据表.xlsx: the table states no origin and no licence; published by the "
                  "site owner's decision (quoted below). Commercial use is unknown, and a "
                  "commercial run refuses it."},
        "sources": ("formula-table",),
        "publication": dict(OWNER_DECISION_FORMULAS),
        "evidence_ceiling": "EXPERT_EXPERIENCE",
        "evidence_note": {
            "zh": "汇编性参考记录：出处是指向原书的线索，不是引文原文，不能作为经典文献记载。",
            "en": "A compiled reference record: 出处 points to a book, it is not a quoted "
                  "passage, so it never counts as a classical text."},
        "limitations": (
            _lim("汇编性参考记录，最高相当于名医经验/专家共识；出处只是线索，不是引文原文，不能当作经典条文引用。",
                 "A compiled reference record (at most expert experience): 出处 is a pointer, "
                 "not a quoted passage, so a row is never a classical quotation."),
            _lim("未记录君臣佐使；「功效」列实为主治文字，部分为现代编者按语（「现用于…」）。",
                 "Roles (君臣佐使) are not recorded; the 功效 column holds 主治 text, partly "
                 "modern editorial notes (现用于…)."),
            _lim("原表未声明来源与许可，由站点所有者决定发布；商业用途未知，商业用途的调用会被拒绝。",
                 "The table states no origin and no licence; it is published by the owner's "
                 "decision; commercial use is unknown, and a commercial run refuses it."),
            _lim("表中未找到不等于古籍未载。",
                 "Not found in the table is not absence from the literature."),
        ),
    },
    "lotus": {
        "title": {"zh": "LOTUS 天然产物来源记录", "en": "LOTUS natural-product occurrences"},
        "licence": "CC-BY-4.0",
        "attribution": {
            "zh": "LOTUS 冻结导出 2026-04-13（Zenodo 记录 19360665，CC BY 4.0）。引用：Rutz A. et al., "
                  "eLife 2022;11:e70780，doi:10.7554/eLife.70780。本站按药材物种筛选与整理，内容未作改写。",
            "en": "LOTUS frozen export 2026-04-13 (Zenodo record 19360665, CC BY 4.0). Cite: Rutz A. "
                  "et al., eLife 2022;11:e70780, doi:10.7554/eLife.70780. Filtered to the species of "
                  "the materia table and regrouped per herb; records are not otherwise changed."},
        "sources": ("lotus",),
        "publication": {"basis": "open-licence"},
        "evidence_ceiling": None,
        "evidence_note": {
            "zh": "来源记录（某化合物曾在某生物中被报道），不是任何证据等级：不支撑药效或作用主张。",
            "en": "Occurrence records (a compound was reported in an organism), not an evidence "
                  "tier: they license no claim about what a herb does."},
        "limitations": (
            _lim("LOTUS 记录的是「该化合物曾在该生物中被报道」：存在不等于活性成分，不等于含量，不等于疗效。",
                 "LOTUS records that the compound was reported in the organism: presence is not "
                 "an active constituent, not a content, not efficacy."),
            _lim("生物与药材按分类单元匹配，不按炮制后的药材匹配。",
                 "The organism ↔ herb match is by taxon, not by the processed drug."),
        ),
    },
    "pathways": {
        "title": {"zh": "Reactome 人类通路与 HGNC 基因符号", "en": "Reactome human pathways and HGNC symbols"},
        "licence": "CC0-1.0",
        "attribution": {
            "zh": "Reactome（UniProt2Reactome.txt、ReactomePathways.txt，CC0）；HGNC 完整基因集（CC0）。"
                  "引用：Milacic M. et al., NAR 2024，doi:10.1093/nar/gkad1025；Seal R.L. et al., NAR 2023，"
                  "doi:10.1093/nar/gkac888。",
            "en": "Reactome (UniProt2Reactome.txt, ReactomePathways.txt, CC0); HGNC complete set "
                  "(CC0). Cite: Milacic M. et al., NAR 2024, doi:10.1093/nar/gkad1025; Seal R.L. "
                  "et al., NAR 2023, doi:10.1093/nar/gkac888."},
        "sources": ("reactome", "hgnc"),
        "publication": {"basis": "open-licence"},
        "evidence_ceiling": None,
        "evidence_note": {
            "zh": "注释（蛋白参与哪些通路），不是任何化合物作用的证据。",
            "en": "Annotation (which pathways a protein takes part in), not evidence of what "
                  "any compound does."},
        "limitations": (
            _lim("通路注释说明蛋白参与什么，不说明任何化合物做了什么。",
                 "Pathway annotation says what a protein takes part in, not what any compound "
                 "does."),
        ),
    },
}
PACK_ORDER = ("core", "formulas", "lotus", "pathways")

#: Sources never published here (V2 §11.1), with the licence their card records and why.
#: ``aliases`` are the other keys the same source goes by (tcmdb dataset keys, versions).
NEVER_PUBLISHED: dict[str, dict[str, Any]] = {
    "npass": {"name": "NPASS", "recorded": "Not stated", "aliases": ("npass2",)},
    "cmaup": {"name": "CMAUP", "recorded": "Not stated", "aliases": ("cmaup2",)},
    "kegg": {"name": "KEGG", "recorded": "academic use free; commercial use requires a licence",
             "aliases": ("kegg_pathways",)},
    "herb": {"name": "HERB", "recorded": "not stated", "aliases": ("herb1", "herb2", "herb_api")},
    "symmap": {"name": "SymMap", "recorded": "not stated", "aliases": ("symmap2", "symmap_api")},
    "itcm": {"name": "ITCM", "recorded": "not stated", "aliases": ()},
    "batman-tcm": {"name": "BATMAN-TCM",
                   "recorded": "free for academic use; commercial use by arrangement with the "
                               "authors", "aliases": ("batman", "batman1", "batman2")},
    "tcmbank": {"name": "TCMBank", "recorded": "not stated (academic use)", "aliases": ()},
    "tcmio": {"name": "TCMIO", "recorded": "not stated", "aliases": ()},
    "dcabm": {"name": "DCABM-TCM", "recorded": "not stated (sister of BATMAN-TCM: free for "
                                               "academic use)", "aliases": ("dcabm_tcm",)},
    "dbpth": {"name": "dbPTH", "recorded": "'All datasets and annotations are free for use' "
                                           "(a use grant, not a redistribution grant)",
              "aliases": ()},
    "ddid": {"name": "DDID", "recorded": "not stated (cite the paper)", "aliases": ()},
    "ttd": {"name": "TTD", "recorded": "not stated (cite the paper)", "aliases": ()},
    "tcmtoxdb": {"name": "TCMToxDB", "recorded": "not stated", "aliases": ()},
    "imppat": {"name": "IMPPAT", "recorded": "CC BY-NC-ND 4.0", "aliases": ("imppat3",)},
    "gutmgene": {"name": "gutMGene", "recorded": "not stated", "aliases": ()},
    "hkbu": {"name": "HKBU exports", "recorded": "not stated; a reviewed manual export",
             "aliases": ("hkbu_formulas_manual",)},
    "hkcmms": {"name": "HKCMMS", "recorded": "Hong Kong Government publication; no reproduction "
                                             "grant found",
               "aliases": ("hkcmms_manual", "hk_cmm_dna_manual")},
    "np-mrd": {"name": "NP-MRD", "recorded": "CC BY-NC 4.0", "aliases": ("np_mrd",)},
}
#: Why, in the words the attribution page uses.
NEVER_PUBLISHED_REASON = {
    "zh": "这些来源未声明许可、仅限学术用途、禁止商用或禁止演绎，本站不转载其内容。需要时请经本机 Runner"
          "（tcmdb fetch）或在自己的浏览器中从原网站获取，并遵守原网站的条款。",
    "en": "These sources state no licence, allow academic use only, or forbid commercial use or "
          "derivatives, so this site republishes nothing from them. Fetch them from the original "
          "site through the local runner (tcmdb fetch) or your own browser, under that site's "
          "terms.",
}


class GateError(ValueError):
    """A pack would publish something its basis does not allow; the build stops."""


_KEY = re.compile(r"[^a-z0-9]+")


def source_key(value: Any) -> str:
    """The comparable form of a source name or key: ``"BATMAN-TCM 2.0"`` → ``batmantcm20``."""
    return _KEY.sub("", str(value or "").lower())


def _denied_index() -> dict[str, str]:
    out: dict[str, str] = {}
    for key, card in NEVER_PUBLISHED.items():
        for k in (key, card["name"], *card.get("aliases", ())):
            out[source_key(k)] = key
    return out


def denied(source: Mapping[str, Any] | str) -> str | None:
    """The denylist key a source falls under (by its key or its name), or None."""
    index = _denied_index()
    if isinstance(source, Mapping):
        names = [source.get("key"), source.get("name")]
    else:
        names = [source]
    for n in names:
        k = source_key(n)
        if not k:
            continue
        if k in index:
            return index[k]
        # "HERB 2.0", "NPASS 2.0 (2023)": the name followed by a version
        for d, key in index.items():
            if k.startswith(d) and k[len(d):].isdigit():
                return key
    return None


def _licence_class(text: str) -> str:
    try:
        from bioagent.tcmdb.spec import licence_class
        return licence_class(text)
    except Exception:                                           # noqa: BLE001
        return "unknown"


def check_sources(pack: str, sources: list[Mapping[str, Any]]) -> None:
    """Refuse (``GateError``) a pack whose sources the allowlist does not cover.

    Each source must be one the pack is allowed to draw on, must not be on the denylist,
    and must carry an open licence unless the pack has a recorded owner decision."""
    if pack not in PACKS:
        raise GateError(f"pack {pack!r} is not in the allowlist (tcmstudio.corpus.packs.PACKS)")
    spec = PACKS[pack]
    allowed = {source_key(s) for s in spec["sources"]}
    owner = spec["publication"].get("basis") != "open-licence"
    if not sources:
        raise GateError(f"pack {pack!r} names no source")
    for s in sources:
        name = s.get("key") or s.get("name")
        bad = denied(s)
        if bad:
            card = NEVER_PUBLISHED[bad]
            raise GateError(f"pack {pack!r}: source {name!r} is never published "
                            f"({card['name']}: {card['recorded']})")
        if source_key(s.get("key") or s.get("name")) not in allowed:
            raise GateError(f"pack {pack!r}: source {name!r} is not allowlisted for this pack "
                            f"(allowed: {', '.join(spec['sources'])})")
        klass = _licence_class(str(s.get("licence") or ""))
        if klass not in OPEN_CLASSES and not owner:
            raise GateError(f"pack {pack!r}: source {name!r} has licence "
                            f"{s.get('licence')!r} (class {klass}); only an open licence or a "
                            "recorded owner decision allows publication")
    if owner:
        decision = spec["publication"]
        missing = [k for k in ("decided_by", "date", "basis", "licence") if not decision.get(k)]
        if missing:
            raise GateError(f"pack {pack!r}: the owner decision lacks {', '.join(missing)}")


def pack_meta(pack: str) -> dict[str, Any]:
    """The manifest fields of a pack that come from this file (counts and sources are added
    by the build)."""
    spec = PACKS[pack]
    return {"title": dict(spec["title"]), "licence": spec["licence"],
            "attribution": dict(spec["attribution"]),
            "publication": dict(spec["publication"]),
            "evidence_ceiling": spec["evidence_ceiling"],
            "evidence_note": dict(spec["evidence_note"]),
            "limitations": [dict(x) for x in spec["limitations"]]}


def limitations(pack: str, lang: str = "en") -> list[str]:
    """The pack's limits in one language."""
    return [x[lang] for x in PACKS[pack]["limitations"]] if pack in PACKS else []
