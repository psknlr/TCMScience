"""Crude drugs (药材) the formula table's compositions resolve to, with their source species.

The formula table (``中医方剂数据表.xlsx``) names ingredients the way the source texts
wrote them: 甘草, 炙甘草, 粉草 and 国老 are one crude drug, 白茯苓 and 赤茯苓 are one
fungus, and 桂心/桂/官桂 are the bark that the Pharmacopoeia calls 肉桂. Network
pharmacology needs the *species* behind each name, because natural-product databases
index organisms. This table is the bridge:

* one row per crude drug: its Pharmacopoeia name, pharmaceutical Latin, category, the
  medicinal part, the source species (Chinese Pharmacopoeia 2020 where it lists them),
  and the other names it goes by in the formula table;
* species are written as names here, and joined to NCBI Taxonomy ids from
  ``registry/materia_taxa.json``, which ``scripts/verify_materia_taxa.py`` writes by
  querying NCBI. A name NCBI does not resolve contributes no taxon, and the record
  says so; nothing is guessed.

Categories: ``plant``, ``fungus`` and ``animal`` drugs have organisms and can carry
constituents; ``mineral`` and ``other`` (fermented or synthetic products such as 神曲 and
冰片) are recognised as components but contribute no organism. A formula containing them
can be analysed; its limitations say which components carry no constituent data.

Where a historical name is ambiguous between two drugs with different species (贝母:
川贝母 or 浙贝母) it is deliberately *not* an alias, so a formula naming it stays
unresolved rather than being assigned one species silently. The same holds for names
whose historical identity is disputed (防葵, 鬼臼, 狼毒, 红豆). A historical name for one
drug is an alias: 山芋 is 山药 under the Song taboo on 蓣.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from ..config import registry_dir

__all__ = ["CATEGORIES", "IDENTITY_PREFIXES", "MATERIA", "MateriaEntry", "NameMention",
           "PROCESSING_WORDS", "TAXA_FILE", "all_drugs", "crude_drugs", "names_in",
           "normalise_name", "processing_of", "resolve_name", "shared_name", "to_simplified",
           "unverified"]

CATEGORIES = ("plant", "fungus", "animal", "mineral", "other")
#: ``registry/materia_taxa.json`` of the checkout, or the copy an installed package carries.
#: It was resolved from this file's location alone, so an installed package found no taxa
#: and silently knew only the four hand-checked herbs (audit AUD-25).
TAXA_FILE = registry_dir() / "materia_taxa.json"


@dataclass(frozen=True)
class MateriaEntry:
    id: str                               # pinyin; the drug id is tcm:herb.<id>
    chinese: str
    latin: str
    category: str
    parts: tuple[str, ...]
    part_zh: str
    species_names: tuple[str, ...]
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: category {self.category!r}")
        if self.category in ("plant", "fungus", "animal") and not self.species_names:
            raise ValueError(f"{self.id}: an organism-derived drug needs its source species")

    @property
    def drug_id(self) -> str:
        return f"tcm:herb.{self.id}"

    @property
    def has_organism(self) -> bool:
        return self.category in ("plant", "fungus", "animal")


_ROWS = [ ("gancao", "甘草", "Glycyrrhizae Radix et Rhizoma", "plant", ("root", "rhizome"), "根及根茎",
  ["Glycyrrhiza uralensis", "Glycyrrhiza inflata", "Glycyrrhiza glabra"],
  ["炙甘草", "粉草", "国老", "生甘草", "甘草梢", "甘草节", "炙草", "大甘草", "粉甘草", "甘草末"]),
 ("gegen", "葛根", "Puerariae Lobatae Radix", "plant", ("root",), "根",
  ["Pueraria montana var. lobata"], ["干葛", "粉葛根", "葛根粉"]),
 ("huangqin", "黄芩", "Scutellariae Radix", "plant", ("root",), "根",
  ["Scutellaria baicalensis"], ["条芩", "枯芩", "子芩", "酒黄芩", "淡黄芩", "片芩", "黄芩片"]),
 ("huanglian", "黄连", "Coptidis Rhizoma", "plant", ("rhizome",), "根茎",
  ["Coptis chinensis", "Coptis deltoidea", "Coptis teeta"], ["川连", "酒黄连", "宣黄连", "川黄连", "姜黄连", "雅连"]),
 ("renshen", "人参", "Ginseng Radix et Rhizoma", "plant", ("root", "rhizome"), "根及根茎",
  ["Panax ginseng"], ["生晒参", "红参", "高丽参", "人参须", "上党人参", "人参芦"]),
 ("danggui", "当归", "Angelicae Sinensis Radix", "plant", ("root",), "根",
  ["Angelica sinensis"], ["全当归", "当归身", "当归尾", "归身", "归尾", "酒当归", "归", "当归须", "秦当归", "川归", "全归"]),
 ("baizhu", "白术", "Atractylodis Macrocephalae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Atractylodes macrocephala"], ["炒白术", "于术", "冬术", "焦白术", "土炒白术", "冬白术", "生白术"]),
 ("fangfeng", "防风", "Saposhnikoviae Radix", "plant", ("root",), "根",
  ["Saposhnikovia divaricata"], ["北防风", "关防风"]),
 ("chuanxiong", "川芎", "Chuanxiong Rhizoma", "plant", ("rhizome",), "根茎",
  ["Ligusticum chuanxiong"], ["芎䓖", "芎藭", "芎\ue840", "抚芎", "芎", "川芎䓖"]),
 ("chuanmuxiang", "川木香", "Vladimiriae Radix", "plant", ("root",), "根",
  ["Dolomiaea souliei", "Vladimiria souliei"], []),
 ("muxiang", "木香", "Aucklandiae Radix", "plant", ("root",), "根",
  ["Aucklandia lappa", "Dolomiaea costus", "Saussurea costus"], ["广木香", "云木香", "南木香"]),
 ("fuling", "茯苓", "Poria", "fungus", ("sclerotium",), "菌核",
  ["Wolfiporia cocos", "Poria cocos", "Wolfiporia extensa"], ["白茯苓", "赤茯苓", "云苓", "茯神", "白茯神", "朱茯神", "云茯苓", "茯苓皮", "赤苓", "白苓", "白茯"]),
 ("chenpi", "陈皮", "Citri Reticulatae Pericarpium", "plant", ("pericarp", "peel"), "果皮",
  ["Citrus reticulata"], ["橘皮", "陈橘皮", "广陈皮", "新会皮", "橘皮白", "广皮", "陈皮白"]),
 # 橘红 and 化橘红 are two monographs of their own, not 陈皮 (Pharmacopoeia 2020): 橘红 is
 # the outer pericarp of 橘 itself, 化橘红 that of 化州柚 or 柚 — another species. Writing
 # 化橘红 as an alias of 陈皮 sent its formulas to Citrus reticulata's constituents.
 ("juhong", "橘红", "Citri Exocarpium Rubrum", "plant", ("pericarp", "peel"), "外层果皮",
  ["Citrus reticulata"], []),
 ("huajuhong", "化橘红", "Citri Grandis Exocarpium", "plant", ("pericarp", "peel"), "外层果皮",
  ["Citrus maxima", "Citrus grandis"], ["化州橘红"]),
 ("qingpi", "青皮", "Citri Reticulatae Pericarpium Viride", "plant", ("fruit", "pericarp"), "幼果或未成熟果皮",
  ["Citrus reticulata"], ["青橘皮"]),
 ("banxia", "半夏", "Pinelliae Rhizoma", "plant", ("tuber",), "块茎",
  ["Pinellia ternata"], ["法半夏", "姜半夏", "清半夏", "制半夏", "半夏曲"]),
 ("ganjiang", "干姜", "Zingiberis Rhizoma", "plant", ("rhizome",), "根茎",
  ["Zingiber officinale"], ["炮姜", "干生姜", "白姜", "炮干姜", "姜炭"]),
 ("shengjiang", "生姜", "Zingiberis Rhizoma Recens", "plant", ("rhizome",), "新鲜根茎",
  ["Zingiber officinale"], ["姜", "鲜姜", "生姜汁", "姜汁", "煨姜", "生姜皮", "姜皮"]),
 ("huangqi", "黄芪", "Astragali Radix", "plant", ("root",), "根",
  ["Astragalus mongholicus", "Astragalus membranaceus"], ["黄耆", "炙黄芪", "绵黄芪", "绵黄耆", "炙黄耆", "生黄芪", "北黄芪"]),
 ("shexiang", "麝香", "Moschus", "animal", ("secretion",), "分泌物",
  ["Moschus berezovskii", "Moschus chrysogaster", "Moschus moschiferus"], ["当门子", "元寸", "麝"]),
 ("chaihu", "柴胡", "Bupleuri Radix", "plant", ("root",), "根",
  ["Bupleurum chinense", "Bupleurum scorzonerifolium"], ["北柴胡", "南柴胡", "软柴胡", "醋柴胡"]),
 ("rougui", "肉桂", "Cinnamomi Cortex", "plant", ("bark",), "树皮",
  ["Cinnamomum cassia"], ["桂心", "桂", "官桂", "紫桂", "桂皮", "肉桂心", "辣桂", "安桂", "上肉桂"]),
 ("guizhi", "桂枝", "Cinnamomi Ramulus", "plant", ("twig",), "嫩枝",
  ["Cinnamomum cassia"], ["桂枝尖", "嫩桂枝"]),
 ("jiegeng", "桔梗", "Platycodonis Radix", "plant", ("root",), "根",
  ["Platycodon grandiflorus"], ["苦桔梗", "白桔梗"]),
 ("qianghuo", "羌活", "Notopterygii Rhizoma et Radix", "plant", ("rhizome", "root"), "根茎和根",
  ["Notopterygium incisum", "Notopterygium franchetii", "Notopterygium forbesii"], ["川羌活", "西羌活"]),
 ("dahuang", "大黄", "Rhei Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Rheum palmatum", "Rheum tanguticum", "Rheum officinale"], ["川大黄", "酒大黄", "锦纹", "生大黄", "熟大黄", "锦纹大黄", "川军", "生军", "将军", "制大黄", "大黄末"]),
 ("binglang", "槟榔", "Arecae Semen", "plant", ("seed",), "种子",
  ["Areca catechu"], ["花槟榔", "大腹子", "尖槟榔", "鸡心槟榔", "槟榔片"]),
 ("xixin", "细辛", "Asari Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Asarum heterotropoides", "Asarum sieboldii"], ["北细辛", "华细辛", "辽细辛"]),
 ("baizhi", "白芷", "Angelicae Dahuricae Radix", "plant", ("root",), "根",
  ["Angelica dahurica"], ["香白芷", "川白芷", "杭白芷"]),
 ("zhiqiao", "枳壳", "Aurantii Fructus", "plant", ("fruit",), "未成熟果实",
  ["Citrus aurantium"], ["炒枳壳", "麸炒枳壳"]),
 ("zhishi", "枳实", "Aurantii Fructus Immaturus", "plant", ("fruit",), "幼果",
  ["Citrus aurantium", "Citrus sinensis"], ["炒枳实", "麸炒枳实"]),
 ("baishao", "白芍", "Paeoniae Radix Alba", "plant", ("root",), "根",
  ["Paeonia lactiflora"], ["白芍药", "芍药", "杭芍", "炒白芍", "酒白芍", "生白芍", "白芍药末"]),
 ("chishao", "赤芍", "Paeoniae Radix Rubra", "plant", ("root",), "根",
  ["Paeonia lactiflora", "Paeonia veitchii"], ["赤芍药"]),
 ("fuzi", "附子", "Aconiti Lateralis Radix Praeparata", "plant", ("root",), "子根",
  ["Aconitum carmichaelii"], ["炮附子", "制附子", "熟附子", "黑附子", "川附子", "附片", "黑附片", "淡附片", "大附子", "生附子", "侧子"]),
 ("chuanwu", "川乌", "Aconiti Radix", "plant", ("root",), "母根",
  ["Aconitum carmichaelii"], ["乌头", "川乌头", "制川乌", "生川乌"]),
 ("ruxiang", "乳香", "Olibanum", "plant", ("resin",), "树脂",
  ["Boswellia sacra", "Boswellia carterii", "Boswellia bhaw-dajiana"], ["明乳香", "滴乳香", "制乳香", "乳香末", "熏陆香"]),
 ("moyao", "没药", "Myrrha", "plant", ("resin",), "树脂",
  ["Commiphora myrrha"], ["制没药", "明没药"]),
 ("houpo", "厚朴", "Magnoliae Officinalis Cortex", "plant", ("bark",), "干皮、根皮及枝皮",
  ["Magnolia officinalis", "Houpoea officinalis"], ["川厚朴", "姜厚朴", "制厚朴", "紫厚朴", "川朴", "厚朴皮"]),
 ("mahuang", "麻黄", "Ephedrae Herba", "plant", ("stem", "herb"), "草质茎",
  ["Ephedra sinica", "Ephedra intermedia", "Ephedra equisetina"], ["净麻黄", "炙麻黄", "麻黄茸", "生麻黄"]),
 ("xingren", "苦杏仁", "Armeniacae Semen Amarum", "plant", ("seed",), "种子",
  ["Prunus armeniaca", "Prunus sibirica", "Prunus mandshurica"], ["杏仁", "苦杏仁", "光杏仁", "杏子"]),
 ("huangbo", "黄柏", "Phellodendri Chinensis Cortex", "plant", ("bark",), "树皮",
  ["Phellodendron chinense", "Phellodendron amurense"], ["黄檗", "川黄柏", "关黄柏", "盐黄柏", "酒黄柏", "黄檗皮"]),
 ("shengma", "升麻", "Cimicifugae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Actaea heracleifolia", "Actaea dahurica", "Actaea cimicifuga", "Cimicifuga heracleifolia", "Cimicifuga dahurica", "Cimicifuga foetida"], ["绿升麻", "川升麻"]),
 ("wuweizi", "五味子", "Schisandrae Chinensis Fructus", "plant", ("fruit",), "果实",
  ["Schisandra chinensis"], ["北五味子", "北五味", "五味"]),
 ("maidong", "麦冬", "Ophiopogonis Radix", "plant", ("root",), "块根",
  ["Ophiopogon japonicus"], ["麦门冬", "寸冬", "麦门冬汁"]),
 ("chuanmutong", "川木通", "Clematidis Armandii Caulis", "plant", ("stem",), "藤茎",
  ["Clematis armandii", "Clematis montana"], []),
 ("mutong", "木通", "Akebiae Caulis", "plant", ("stem",), "藤茎",
  ["Akebia quinata", "Akebia trifoliata"], []),
 ("cangzhu", "苍术", "Atractylodis Rhizoma", "plant", ("rhizome",), "根茎",
  ["Atractylodes lancea", "Atractylodes chinensis"], ["茅术", "茅山苍术", "制苍术", "炒苍术"]),
 ("shengdi", "地黄", "Rehmanniae Radix", "plant", ("root",), "块根",
  ["Rehmannia glutinosa"], ["生地", "生地黄", "生干地黄", "干地黄", "干生地", "细生地", "大生地", "鲜地黄", "鲜生地", "生地黄汁", "地黄汁"]),
 ("shudi", "熟地黄", "Rehmanniae Radix Praeparata", "plant", ("root",), "块根（炮制）",
  ["Rehmannia glutinosa"], ["熟地", "熟干地黄", "大熟地", "熟地黄炭"]),
 ("zhimu", "知母", "Anemarrhenae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Anemarrhena asphodeloides"], ["肥知母", "盐知母"]),
 ("niuxi", "牛膝", "Achyranthis Bidentatae Radix", "plant", ("root",), "根",
  ["Achyranthes bidentata"], ["怀牛膝", "淮牛膝"]),
 # 川牛膝 is Cyathula officinalis, not 牛膝 (Achyranthes bidentata): stripping 川 made it so.
 ("chuanniuxi", "川牛膝", "Cyathulae Radix", "plant", ("root",), "根",
  ["Cyathula officinalis"], []),
 ("duhuo", "独活", "Angelicae Pubescentis Radix", "plant", ("root",), "根",
  ["Angelica biserrata", "Angelica pubescens"], ["川独活", "香独活"]),
 ("lianqiao", "连翘", "Forsythiae Fructus", "plant", ("fruit",), "果实",
  ["Forsythia suspensa"], ["青连翘", "连翘心", "连乔"]),
 # 白丁香 is the sparrow's droppings (雄雀屎), not 丁香 with a 白 in front of it.
 ("baidingxiang", "白丁香", "Passeris Faeces", "animal", ("faeces",), "粪便",
  ["Passer montanus"], ["雄雀屎", "雀屎"]),
 ("dingxiang", "丁香", "Caryophylli Flos", "plant", ("flower", "bud"), "花蕾",
  ["Syzygium aromaticum"], ["公丁香", "丁香皮", "母丁香", "鸡舌香"]),
 ("zexie", "泽泻", "Alismatis Rhizoma", "plant", ("tuber", "rhizome"), "块茎",
  ["Alisma orientale", "Alisma plantago-aquatica"], ["建泽泻", "福泽泻"]),
 ("jingjie", "荆芥", "Schizonepetae Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Nepeta tenuifolia", "Schizonepeta tenuifolia"], ["荆芥穗", "芥穗", "荆芥炭"]),
 ("chenxiang", "沉香", "Aquilariae Lignum Resinatum", "plant", ("wood", "resin"), "含树脂木材",
  ["Aquilaria sinensis"], ["沉水香", "伽南香"]),
 ("xiangfu", "香附", "Cyperi Rhizoma", "plant", ("rhizome",), "根茎",
  ["Cyperus rotundus"], ["香附子", "制香附", "醋香附", "炒香附", "莎草根"]),
 ("xuanshen", "玄参", "Scrophulariae Radix", "plant", ("root",), "根",
  ["Scrophularia ningpoensis"], ["元参", "黑参", "乌元参"]),
 ("tianma", "天麻", "Gastrodiae Rhizoma", "plant", ("tuber",), "块茎",
  ["Gastrodia elata"], ["明天麻", "赤箭"]),
 ("qianhu", "前胡", "Peucedani Radix", "plant", ("root",), "根",
  ["Peucedanum praeruptorum", "Kitagawia praeruptora"], ["白前胡"]),
 ("yuanzhi", "远志", "Polygalae Radix", "plant", ("root",), "根",
  ["Polygala tenuifolia", "Polygala sibirica"], ["远志肉", "制远志", "炙远志"]),
 ("taoren", "桃仁", "Persicae Semen", "plant", ("seed",), "种子",
  ["Prunus persica", "Prunus davidiana"], ["桃仁泥", "光桃仁"]),
 ("digupi", "地骨皮", "Lycii Cortex", "plant", ("root bark", "bark"), "根皮",
  ["Lycium chinense", "Lycium barbarum"], []),
 ("bohe", "薄荷", "Menthae Haplocalycis Herba", "plant", ("herb", "aerial", "leaf"), "地上部分",
  ["Mentha canadensis", "Mentha haplocalyx"], ["薄荷叶", "苏薄荷", "龙脑薄荷", "薄荷末"]),
 ("niuhuang", "牛黄", "Bovis Calculus", "animal", ("gallstone",), "胆结石",
  ["Bos taurus"], ["西牛黄", "犀黄", "丑宝"]),
 ("ejiao", "阿胶", "Asini Corii Colla", "animal", ("skin",), "皮熬制胶块",
  ["Equus asinus"], ["阿胶珠", "驴皮胶"]),
 ("badou", "巴豆", "Crotonis Fructus", "plant", ("fruit", "seed"), "果实",
  ["Croton tiglium"], ["巴豆霜", "巴豆仁", "巴豆肉"]),
 ("shanyao", "山药", "Dioscoreae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Dioscorea polystachya", "Dioscorea opposita"], ["薯蓣", "怀山药", "干山药", "淮山药", "山薯", "山芋"]),
 ("sharen", "砂仁", "Amomi Fructus", "plant", ("fruit", "seed"), "果实",
  ["Wurfbainia villosa", "Amomum villosum", "Amomum longiligulare"], ["缩砂仁", "缩砂", "缩砂蜜", "春砂仁", "阳春砂"]),
 ("honghua", "红花", "Carthami Flos", "plant", ("flower",), "花",
  ["Carthamus tinctorius"], ["草红花", "红蓝花", "杜红花"]),
 ("chuanbeimu", "川贝母", "Fritillariae Cirrhosae Bulbus", "plant", ("bulb",), "鳞茎",
  ["Fritillaria cirrhosa", "Fritillaria unibracteata", "Fritillaria przewalskii", "Fritillaria delavayi"], ["川贝"]),
 ("zhebeimu", "浙贝母", "Fritillariae Thunbergii Bulbus", "plant", ("bulb",), "鳞茎",
  ["Fritillaria thunbergii"], ["浙贝", "象贝", "象贝母", "大贝"]),
 ("wuzhuyu", "吴茱萸", "Euodiae Fructus", "plant", ("fruit",), "近成熟果实",
  ["Tetradium ruticarpum", "Euodia rutaecarpa"], ["吴萸", "淡吴萸", "制吴茱萸", "茱萸"]),
 ("baifuzi", "白附子", "Typhonii Rhizoma", "plant", ("tuber",), "块茎",
  ["Sauromatum giganteum", "Typhonium giganteum"], ["禹白附", "制白附子"]),
 ("cheqianzi", "车前子", "Plantaginis Semen", "plant", ("seed",), "种子",
  ["Plantago asiatica", "Plantago depressa"], ["车前", "车前草", "盐车前子"]),
 ("duzhong", "杜仲", "Eucommiae Cortex", "plant", ("bark",), "树皮",
  ["Eucommia ulmoides"], ["川杜仲", "盐杜仲", "炒杜仲"]),
 ("qinjiao", "秦艽", "Gentianae Macrophyllae Radix", "plant", ("root",), "根",
  ["Gentiana macrophylla", "Gentiana straminea", "Gentiana crassicaulis", "Gentiana dahurica"], ["左秦艽"]),
 ("mudanpi", "牡丹皮", "Moutan Cortex", "plant", ("root bark", "bark"), "根皮",
  ["Paeonia suffruticosa"], ["丹皮", "粉丹皮", "牡丹根皮", "牡丹"]),
 ("quanxie", "全蝎", "Scorpio", "animal", ("whole",), "干燥体",
  ["Mesobuthus martensii", "Buthus martensii", "Olivierus martensii"], ["全虫", "蝎梢", "蝎尾", "干蝎", "蝎"]),
 ("roudoukou", "肉豆蔻", "Myristicae Semen", "plant", ("seed",), "种仁",
  ["Myristica fragrans"], ["肉果", "肉蔻", "玉果"]),
 ("huoxiang", "广藿香", "Pogostemonis Herba", "plant", ("herb", "aerial", "leaf"), "地上部分",
  ["Pogostemon cablin"], ["藿香", "藿香叶", "藿香梗"]),
 ("tiannanxing", "天南星", "Arisaematis Rhizoma", "plant", ("tuber",), "块茎",
  ["Arisaema erubescens", "Arisaema heterophyllum", "Arisaema amurense"], ["南星", "胆南星", "制南星", "制天南星", "生南星", "胆星"]),
 ("kushen", "苦参", "Sophorae Flavescentis Radix", "plant", ("root",), "根",
  ["Sophora flavescens"], []),
 ("zhizi", "栀子", "Gardeniae Fructus", "plant", ("fruit",), "果实",
  ["Gardenia jasminoides"], ["栀子仁", "山栀", "山栀子", "焦栀子", "黑山栀", "山栀仁", "焦山栀", "生栀子", "栀子皮"]),
 ("shanzhuyu", "山茱萸", "Corni Fructus", "plant", ("fruit",), "果肉",
  ["Cornus officinalis"], ["山萸肉", "萸肉", "山茱萸肉", "枣皮", "山萸"]),
 ("biejia", "鳖甲", "Trionycis Carapax", "animal", ("shell",), "背甲",
  ["Pelodiscus sinensis"], ["炙鳖甲", "醋鳖甲"]),
 ("wuyao", "乌药", "Linderae Radix", "plant", ("root",), "块根",
  ["Lindera aggregata"], ["台乌药", "天台乌药"]),
 ("xuduan", "续断", "Dipsaci Radix", "plant", ("root",), "根",
  ["Dipsacus asper", "Dipsacus asperoides"], ["川续断", "川断"]),
 ("shihu", "石斛", "Dendrobii Caulis", "plant", ("stem",), "茎",
  ["Dendrobium nobile", "Dendrobium chrysotoxum", "Dendrobium fimbriatum", "Dendrobium huoshanense"], ["金钗石斛", "川石斛", "鲜石斛"]),
 ("jiangcan", "僵蚕", "Bombyx Batryticatus", "animal", ("whole",), "干燥体",
  ["Bombyx mori"], ["白僵蚕", "炒僵蚕", "天虫"]),
 ("dazao", "大枣", "Jujubae Fructus", "plant", ("fruit",), "果实",
  ["Ziziphus jujuba"], ["枣", "红枣", "枣肉", "大红枣", "干枣", "枣子"]),
 ("sangbaipi", "桑白皮", "Mori Cortex", "plant", ("root bark", "bark"), "根皮",
  ["Morus alba"], ["桑根白皮", "桑皮", "炙桑白皮"]),
 ("sangye", "桑叶", "Mori Folium", "plant", ("leaf",), "叶",
  ["Morus alba"], ["冬桑叶", "霜桑叶"]),
 ("kuandonghua", "款冬花", "Farfarae Flos", "plant", ("flower", "bud"), "花蕾",
  ["Tussilago farfara"], ["款冬", "冬花"]),
 ("ziwan", "紫菀", "Asteris Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Aster tataricus"], ["紫菀茸", "炙紫菀"]),
 ("tiandong", "天冬", "Asparagi Radix", "plant", ("root",), "块根",
  ["Asparagus cochinchinensis"], ["天门冬", "天门冬汁"]),
 ("maiya", "麦芽", "Hordei Fructus Germinatus", "plant", ("fruit", "seed"), "发芽果实",
  ["Hordeum vulgare"], ["大麦芽", "炒麦芽", "麦糵", "大麦糵"]),
 ("shanzha", "山楂", "Crataegi Fructus", "plant", ("fruit",), "果实",
  ["Crataegus pinnatifida"], ["山查", "焦山楂", "山楂肉", "山查肉"]),
 ("wumei", "乌梅", "Mume Fructus", "plant", ("fruit",), "近成熟果实",
  ["Prunus mume"], ["乌梅肉"]),
 ("hezi", "诃子", "Chebulae Fructus", "plant", ("fruit",), "果实",
  ["Terminalia chebula"], ["诃黎勒", "诃子肉", "诃黎勒皮"]),
 ("baidoukou", "豆蔻", "Amomi Fructus Rotundus", "plant", ("fruit",), "果实",
  ["Amomum kravanh", "Wurfbainia compacta", "Amomum compactum"], ["白豆蔻", "白蔻", "白蔻仁", "蔻仁", "白豆蔻仁"]),
 ("caodoukou", "草豆蔻", "Alpiniae Katsumadai Semen", "plant", ("seed",), "种子",
  ["Alpinia hainanensis", "Alpinia katsumadai"], ["草蔻", "草豆蔻仁"]),
 ("yizhiren", "益智", "Alpiniae Oxyphyllae Fructus", "plant", ("fruit",), "果实",
  ["Alpinia oxyphylla"], ["益智仁", "益智子"]),
 ("gaoliangjiang", "高良姜", "Alpiniae Officinarum Rhizoma", "plant", ("rhizome",), "根茎",
  ["Alpinia officinarum"], ["良姜"]),
 ("puhuang", "蒲黄", "Typhae Pollen", "plant", ("pollen",), "花粉",
  ["Typha angustifolia", "Typha orientalis"], ["生蒲黄", "炒蒲黄", "蒲黄炭"]),
 ("yanhusuo", "延胡索", "Corydalis Rhizoma", "plant", ("tuber",), "块茎",
  ["Corydalis yanhusuo"], ["玄胡索", "元胡", "玄胡", "延胡", "醋延胡索", "元胡索"]),
 ("yujin", "郁金", "Curcumae Radix", "plant", ("root",), "块根",
  ["Curcuma wenyujin", "Curcuma longa", "Curcuma kwangsiensis", "Curcuma phaeocaulis"], ["广郁金", "川郁金"]),
 ("jianghuang", "姜黄", "Curcumae Longae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Curcuma longa"], ["片姜黄"]),
 ("ezhu", "莪术", "Curcumae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Curcuma phaeocaulis", "Curcuma kwangsiensis", "Curcuma wenyujin"], ["蓬莪术", "蓬术", "莪荗", "蓬莪茂"]),
 ("sanleng", "三棱", "Sparganii Rhizoma", "plant", ("tuber",), "块茎",
  ["Sparganium stoloniferum"], ["京三棱", "荆三棱"]),
 # 茺蔚子 is the fruit of 益母草's species, a monograph of its own, not the herb.
 ("chongweizi", "茺蔚子", "Leonuri Fructus", "plant", ("fruit",), "果实",
  ["Leonurus japonicus"], ["茺蔚实"]),
 ("yimucao", "益母草", "Leonuri Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Leonurus japonicus"], ["茺蔚", "坤草"]),
 ("niubangzi", "牛蒡子", "Arctii Fructus", "plant", ("fruit",), "果实",
  ["Arctium lappa"], ["鼠粘子", "大力子", "恶实", "牛子", "牛蒡"]),
 ("juhua", "菊花", "Chrysanthemi Flos", "plant", ("flower",), "头状花序",
  ["Chrysanthemum morifolium", "Chrysanthemum × morifolium"], ["甘菊花", "白菊花", "杭菊花", "甘菊", "滁菊", "黄菊花", "白菊", "杭菊"]),
 ("jinyinhua", "金银花", "Lonicerae Japonicae Flos", "plant", ("flower", "bud"), "花蕾",
  ["Lonicera japonica"], ["银花", "忍冬花", "双花", "二花", "金银花藤", "忍冬藤", "忍冬"]),
 ("banlangen", "板蓝根", "Isatidis Radix", "plant", ("root",), "根",
  ["Isatis tinctoria", "Isatis indigotica"], ["蓝根"]),
 ("yinchen", "茵陈", "Artemisiae Scopariae Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Artemisia scoparia", "Artemisia capillaris"], ["茵陈蒿", "绵茵陈"]),
 ("zhuling", "猪苓", "Polyporus", "fungus", ("sclerotium",), "菌核",
  ["Polyporus umbellatus"], []),
 ("mugua", "木瓜", "Chaenomelis Fructus", "plant", ("fruit",), "近成熟果实",
  ["Chaenomeles speciosa"], ["宣木瓜"]),
 ("weilingxian", "威灵仙", "Clematidis Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Clematis chinensis", "Clematis hexapetala", "Clematis terniflora var. mandshurica"], ["灵仙"]),
 ("fangji", "防己", "Stephaniae Tetrandrae Radix", "plant", ("root",), "根",
  ["Stephania tetrandra"], ["汉防己", "粉防己"]),
 ("buguzhi", "补骨脂", "Psoraleae Fructus", "plant", ("fruit",), "果实",
  ["Cullen corylifolium", "Psoralea corylifolia"], ["破故纸", "故纸", "胡韭子", "盐补骨脂"]),
 ("tusizi", "菟丝子", "Cuscutae Semen", "plant", ("seed",), "种子",
  ["Cuscuta chinensis", "Cuscuta australis"], ["菟丝", "吐丝子"]),
 ("roucongrong", "肉苁蓉", "Cistanches Herba", "plant", ("stem",), "带鳞叶的肉质茎",
  ["Cistanche deserticola", "Cistanche tubulosa"], ["苁蓉", "淡苁蓉", "大芸"]),
 ("bajitian", "巴戟天", "Morindae Officinalis Radix", "plant", ("root",), "根",
  ["Morinda officinalis"], ["巴戟", "巴戟肉"]),
 ("yinyanghuo", "淫羊藿", "Epimedii Folium", "plant", ("leaf",), "叶",
  ["Epimedium brevicornu", "Epimedium sagittatum", "Epimedium pubescens", "Epimedium koreanum"], ["仙灵脾"]),
 ("gouqizi", "枸杞子", "Lycii Fructus", "plant", ("fruit",), "果实",
  ["Lycium barbarum"], ["枸杞", "甘杞子", "杞子", "枸杞子汁"]),
 ("heshouwu", "何首乌", "Polygoni Multiflori Radix", "plant", ("root",), "块根",
  ["Reynoutria multiflora", "Polygonum multiflorum", "Fallopia multiflora"], ["首乌", "制首乌", "制何首乌"]),
 ("suanzaoren", "酸枣仁", "Ziziphi Spinosae Semen", "plant", ("seed",), "种子",
  ["Ziziphus jujuba var. spinosa"], ["枣仁", "炒枣仁", "炒酸枣仁"]),
 ("baiziren", "柏子仁", "Platycladi Semen", "plant", ("seed",), "种仁",
  ["Platycladus orientalis"], ["柏仁", "柏子"]),
 ("longyanrou", "龙眼肉", "Longan Arillus", "plant", ("aril",), "假种皮",
  ["Dimocarpus longan"], ["桂圆肉", "龙眼"]),
 ("baihe", "百合", "Lilii Bulbus", "plant", ("bulb",), "肉质鳞叶",
  ["Lilium lancifolium", "Lilium brownii var. viridulum", "Lilium pumilum"], []),
 ("chantui", "蝉蜕", "Cicadae Periostracum", "animal", ("exuvia",), "若虫羽化时脱落的皮壳",
  ["Cryptotympana atrata", "Cryptotympana pustulata"], ["蝉衣", "蝉退", "蝉壳"]),
 ("shechuangzi", "蛇床子", "Cnidii Fructus", "plant", ("fruit",), "果实",
  ["Cnidium monnieri"], ["蛇床"]),
 ("baixianpi", "白鲜皮", "Dictamni Cortex", "plant", ("root bark",), "根皮",
  ["Dictamnus dasycarpus"], ["白藓皮"]),
 ("chuanlianzi", "川楝子", "Toosendan Fructus", "plant", ("fruit",), "果实",
  ["Melia toosendan", "Melia azedarach"], ["金铃子", "楝实", "川楝实", "楝子"]),
 ("shijunzi", "使君子", "Quisqualis Fructus", "plant", ("fruit",), "果实",
  ["Combretum indicum", "Quisqualis indica"], ["使君子仁", "使君肉"]),
 ("changshan", "常山", "Dichroae Radix", "plant", ("root",), "根",
  ["Dichroa febrifuga"], ["蜀漆", "恒山"]),
 ("caoguo", "草果", "Tsaoko Fructus", "plant", ("fruit",), "果实",
  ["Lanxangia tsao-ko", "Amomum tsao-ko", "Amomum tsaoko"], ["草果仁", "草果子"]),
 ("tongcao", "通草", "Tetrapanacis Medulla", "plant", ("pith", "stem"), "茎髓",
  ["Tetrapanax papyrifer"], ["通脱木"]),
 ("dengxincao", "灯心草", "Junci Medulla", "plant", ("pith", "stem"), "茎髓",
  ["Juncus effusus"], ["灯心", "灯草", "灯芯"]),
 ("tianhuafen", "天花粉", "Trichosanthis Radix", "plant", ("root",), "根",
  ["Trichosanthes kirilowii", "Trichosanthes rosthornii"], ["栝楼根", "瓜蒌根", "花粉", "栝蒌根"]),
 ("gualou", "瓜蒌", "Trichosanthis Fructus", "plant", ("fruit",), "果实",
  ["Trichosanthes kirilowii", "Trichosanthes rosthornii"], ["栝楼", "栝蒌", "全瓜蒌", "瓜蒌实", "栝楼实", "瓜蒌仁", "栝楼仁", "瓜蒌皮"]),
 ("xuanfuhua", "旋覆花", "Inulae Flos", "plant", ("flower",), "头状花序",
  ["Inula japonica", "Inula britannica"], ["金沸草", "覆花"]),
 ("zisuye", "紫苏叶", "Perillae Folium", "plant", ("leaf",), "叶",
  ["Perilla frutescens"], ["紫苏", "苏叶", "紫苏茎叶", "苏梗", "紫苏梗", "紫苏叶末"]),
 ("zisuzi", "紫苏子", "Perillae Fructus", "plant", ("fruit", "seed"), "果实",
  ["Perilla frutescens"], ["苏子", "炒苏子"]),
 ("shegan", "射干", "Belamcandae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Iris domestica", "Belamcanda chinensis"], []),
 ("zaojiao", "皂荚", "Gleditsiae Sinensis Fructus", "plant", ("fruit",), "果实",
  ["Gleditsia sinensis"], ["皂角", "猪牙皂", "皂荚子", "皂角刺", "皂角子", "大皂角", "牙皂", "猪牙皂角"]),
 ("qianniuzi", "牵牛子", "Pharbitidis Semen", "plant", ("seed",), "种子",
  ["Ipomoea nil", "Ipomoea purpurea", "Pharbitis nil"], ["黑牵牛", "白牵牛", "黑丑", "二丑", "牵牛", "牵牛末", "黑牵牛子"]),
 ("huomaren", "火麻仁", "Cannabis Fructus", "plant", ("fruit", "seed"), "果实",
  ["Cannabis sativa"], ["麻子仁", "大麻仁", "麻仁", "麻子"]),
 ("fengmi", "蜂蜜", "Mel", "animal", ("honey",), "蜜",
  ["Apis cerana", "Apis mellifera"], ["蜜", "白蜜", "炼蜜", "白沙蜜", "蜂蜜汁"]),
 ("gouteng", "钩藤", "Uncariae Ramulus cum Uncis", "plant", ("stem", "hook"), "带钩茎枝",
  ["Uncaria rhynchophylla"], ["双钩藤", "钩钩", "钩藤钩", "嫩钩藤"]),
 ("tianzhuhuang", "天竺黄", "Bambusae Concretio Silicea", "plant", ("concretion",), "分泌液干燥后的块状物",
  ["Bambusa textilis"], ["竹黄", "天竹黄"]),
 ("zhuru", "竹茹", "Bambusae Caulis in Taenias", "plant", ("stem",), "茎秆的中间层",
  ["Bambusa tuldoides", "Phyllostachys nigra"], ["青竹茹", "淡竹茹", "竹沥", "淡竹沥", "竹沥汁"]),
 ("danzhuye", "淡竹叶", "Lophatheri Herba", "plant", ("leaf", "stem"), "茎叶",
  ["Lophatherum gracile"], []),
 # 竹叶 (竹叶石膏汤) is the leaf of the bamboo 淡竹, not the grass Pharmacopoeia 淡竹叶;
 # 苦竹叶 is another bamboo's and is left unresolved.
 ("zhuye", "竹叶", "Phyllostachydis Henonis Folium", "plant", ("leaf",), "叶",
  ["Phyllostachys nigra var. henonis"], []),
 ("lugen", "芦根", "Phragmitis Rhizoma", "plant", ("rhizome",), "根茎",
  ["Phragmites australis"], ["苇茎", "芦苇根", "鲜芦根"]),
 ("dafupi", "大腹皮", "Arecae Pericarpium", "plant", ("pericarp",), "果皮",
  ["Areca catechu"], ["大腹", "腹皮"]),
 ("xiaohuixiang", "小茴香", "Foeniculi Fructus", "plant", ("fruit",), "果实",
  ["Foeniculum vulgare"], ["茴香", "茴香子", "怀香", "舶上茴香", "谷茴香", "小茴"]),
 ("bajiaohuixiang", "八角茴香", "Anisi Stellati Fructus", "plant", ("fruit",), "果实",
  ["Illicium verum"], ["大茴香", "八角"]),
 ("huajiao", "花椒", "Zanthoxyli Pericarpium", "plant", ("pericarp",), "果皮",
  ["Zanthoxylum bungeanum", "Zanthoxylum schinifolium"], ["川椒", "蜀椒", "椒红", "秦椒", "椒目", "汉椒", "椒"]),
 ("bibo", "荜茇", "Piperis Longi Fructus", "plant", ("fruit",), "果穗",
  ["Piper longum"], ["荜拨"]),
 ("hujiao", "胡椒", "Piperis Fructus", "plant", ("fruit",), "果实",
  ["Piper nigrum"], ["白胡椒", "黑胡椒"]),
 ("bichengqie", "荜澄茄", "Litseae Fructus", "plant", ("fruit",), "果实",
  ["Litsea cubeba"], []),
 ("huzhang", "虎杖", "Polygoni Cuspidati Rhizoma et Radix", "plant", ("rhizome", "root"), "根茎和根",
  ["Reynoutria japonica", "Polygonum cuspidatum"], []),
 ("danshen", "丹参", "Salviae Miltiorrhizae Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Salvia miltiorrhiza"], ["紫丹参"]),
 ("sanqi", "三七", "Notoginseng Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Panax notoginseng"], ["田七", "参三七", "三七粉"]),
 ("dangshen", "党参", "Codonopsis Radix", "plant", ("root",), "根",
  ["Codonopsis pilosula", "Codonopsis tangshen"], ["潞党参", "潞党", "台党参"]),
 ("taizishen", "太子参", "Pseudostellariae Radix", "plant", ("root",), "块根",
  ["Pseudostellaria heterophylla"], ["孩儿参"]),
 ("shashen", "北沙参", "Glehniae Radix", "plant", ("root",), "根",
  ["Glehnia littoralis"], ["沙参", "北沙参"]),
 ("huangjing", "黄精", "Polygonati Rhizoma", "plant", ("rhizome",), "根茎",
  ["Polygonatum kingianum", "Polygonatum sibiricum", "Polygonatum cyrtonema"], []),
 ("yuzhu", "玉竹", "Polygonati Odorati Rhizoma", "plant", ("rhizome",), "根茎",
  ["Polygonatum odoratum"], ["萎蕤", "葳蕤"]),
 ("nvzhenzi", "女贞子", "Ligustri Lucidi Fructus", "plant", ("fruit",), "果实",
  ["Ligustrum lucidum"], ["女贞实"]),
 ("mohanlian", "墨旱莲", "Ecliptae Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Eclipta prostrata"], ["旱莲草", "墨旱莲草"]),
 ("sangjisheng", "桑寄生", "Taxilli Herba", "plant", ("stem", "leaf"), "带叶茎枝",
  ["Taxillus chinensis"], ["寄生", "广寄生"]),
 ("guisuibu", "骨碎补", "Drynariae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Drynaria roosii"], ["猴姜", "申姜"]),
 ("gouji", "狗脊", "Cibotii Rhizoma", "plant", ("rhizome",), "根茎",
  ["Cibotium barometz"], ["金毛狗脊"]),
 ("wujiapi", "五加皮", "Acanthopanacis Cortex", "plant", ("root bark",), "根皮",
  ["Eleutherococcus nodiflorus", "Acanthopanax gracilistylus"], ["南五加皮"]),
 ("mubiezi", "木鳖子", "Momordicae Semen", "plant", ("seed",), "种子",
  ["Momordica cochinchinensis"], ["木鳖", "木鳖子仁"]),
 ("aiye", "艾叶", "Artemisiae Argyi Folium", "plant", ("leaf",), "叶",
  ["Artemisia argyi"], ["艾", "蕲艾", "熟艾", "艾绒"]),
 ("yiyiren", "薏苡仁", "Coicis Semen", "plant", ("seed",), "种仁",
  ["Coix lacryma-jobi var. ma-yuen", "Coix lacryma-jobi"], ["薏苡", "苡仁", "薏仁", "薏米", "生苡仁"]),
 ("biandou", "白扁豆", "Lablab Semen Album", "plant", ("seed",), "种子",
  ["Lablab purpureus"], ["扁豆", "炒扁豆"]),
 ("lianzi", "莲子", "Nelumbinis Semen", "plant", ("seed",), "种子",
  ["Nelumbo nucifera"], ["莲肉", "莲子肉", "建莲", "石莲肉", "莲实", "石莲子", "莲子心", "莲须"]),
 ("qianshi", "芡实", "Euryales Semen", "plant", ("seed",), "种仁",
  ["Euryale ferox"], ["鸡头实", "芡实米"]),
 ("nanwuweizi", "南五味子", "Schisandrae Sphenantherae Fructus", "plant", ("fruit",), "果实",
  ["Schisandra sphenanthera"], []),
 ("fupenzi", "覆盆子", "Rubi Fructus", "plant", ("fruit",), "果实",
  ["Rubus chingii"], []),
 ("jinyingzi", "金樱子", "Rosae Laevigatae Fructus", "plant", ("fruit",), "果实",
  ["Rosa laevigata"], []),
 ("muli", "牡蛎", "Ostreae Concha", "animal", ("shell",), "贝壳",
  ["Crassostrea gigas", "Magallana gigas", "Crassostrea talienwhanensis", "Crassostrea rivularis"], ["左牡蛎", "煅牡蛎", "生牡蛎", "牡蛎粉"]),
 ("shijueming", "石决明", "Haliotidis Concha", "animal", ("shell",), "贝壳",
  ["Haliotis diversicolor", "Haliotis discus hannai"], ["煅石决明"]),
 ("zhenzhu", "珍珠", "Margarita", "animal", ("pearl",), "珍珠",
  ["Pinctada fucata", "Hyriopsis cumingii"], ["真珠", "珍珠粉", "真珠末"]),
 ("dilong", "地龙", "Pheretima", "animal", ("whole",), "干燥体",
  ["Amynthas aspergillum", "Pheretima aspergillum"], ["蚯蚓", "广地龙"]),
 ("wugong", "蜈蚣", "Scolopendra", "animal", ("whole",), "干燥体",
  ["Scolopendra subspinipes mutilans", "Scolopendra mutilans"], []),
 ("shuizhi", "水蛭", "Hirudo", "animal", ("whole",), "干燥全体",
  ["Whitmania pigra", "Hirudo nipponia"], ["蚂蟥"]),
 ("chuanshanjia", "穿山甲", "Manis Squama", "animal", ("scale",), "鳞甲",
  ["Manis pentadactyla"], ["山甲", "炮山甲", "穿山甲片"]),
 ("xijiao", "犀角", "Rhinocerotis Cornu", "animal", ("horn",), "角",
  ["Rhinoceros unicornis", "Rhinoceros sondaicus", "Dicerorhinus sumatrensis"], ["犀角屑", "犀角末", "生犀角", "乌犀角", "生犀"]),
 ("lingyangjiao", "羚羊角", "Saigae Tataricae Cornu", "animal", ("horn",), "角",
  ["Saiga tatarica"], ["羚羊角屑", "羚角", "羚羊角末"]),
 ("lurong", "鹿茸", "Cervi Cornu Pantotrichum", "animal", ("antler",), "幼角",
  ["Cervus nippon", "Cervus elaphus"], ["鹿茸片"]),
 ("lujiao", "鹿角", "Cervi Cornu", "animal", ("antler",), "角",
  ["Cervus nippon", "Cervus elaphus"], ["鹿角胶", "鹿角霜", "鹿角屑", "鹿角末"]),
 ("guijia", "龟甲", "Testudinis Carapax et Plastrum", "animal", ("shell",), "背甲及腹甲",
  ["Mauremys reevesii", "Chinemys reevesii"], ["龟板", "炙龟板", "龟版", "败龟板", "败龟"]),
 ("shenqu", "神曲", "Massa Medicata Fermentata", "other", (), "发酵加工品",
  [], ["六神曲", "炒神曲", "建曲", "焦神曲", "陈曲", "神麴"]),
 ("bingpian", "冰片", "Borneolum", "other", (), "结晶（合成或提取）",
  [], ["龙脑", "梅片", "片脑", "龙脑香", "脑子"]),
 # minerals: recorded as components, no organism
 ("zhusha", "朱砂", "Cinnabaris", "mineral", (), "矿石", [], ["辰砂", "丹砂", "飞朱砂", "朱砂末", "水飞朱砂"]),
 ("xionghuang", "雄黄", "Realgar", "mineral", (), "矿石", [], ["明雄黄", "腰黄"]),
 ("shigao", "石膏", "Gypsum Fibrosum", "mineral", (), "矿石", [], ["生石膏", "煅石膏", "软石膏"]),
 ("huashi", "滑石", "Talcum", "mineral", (), "矿石", [], ["滑石粉", "飞滑石"]),
 ("qingfen", "轻粉", "Calomelas", "mineral", (), "加工品", [], ["水银粉", "腻粉"]),
 ("baifan", "白矾", "Alumen", "mineral", (), "矿石加工品", [], ["明矾", "枯矾", "矾石", "生白矾", "枯白矾", "飞矾"]),
 ("liuhuang", "硫黄", "Sulfur", "mineral", (), "矿石", [], ["石硫黄", "硫磺"]),
 ("chishizhi", "赤石脂", "Halloysitum Rubrum", "mineral", (), "矿石", [], ["煅赤石脂"]),
 ("longgu", "龙骨", "Os Draconis", "mineral", (), "古动物骨骼化石", [], ["煅龙骨", "生龙骨", "五花龙骨", "白龙骨", "龙齿"]),
 ("mangxiao", "芒硝", "Natrii Sulfas", "mineral", (), "矿石加工品", [], ["朴硝", "玄明粉", "元明粉", "芒消", "朴消", "风化硝", "马牙硝", "马牙消", "风化消", "牙消", "牙硝"]),
 ("cishi", "磁石", "Magnetitum", "mineral", (), "矿石", [], ["灵磁石", "煅磁石", "慈石"]),
 ("daizheshi", "赭石", "Haematitum", "mineral", (), "矿石", [], ["代赭石", "代赭", "煅赭石"]),
 ("yunmu", "云母", "Muscovitum", "mineral", (), "矿石", [], []),
 ("hanshuishi", "寒水石", "Glauberitum", "mineral", (), "矿石", [], ["凝水石"]),
 ("qianfen", "铅丹", "Plumbum Rubrum", "mineral", (), "加工品", [], ["黄丹", "广丹", "东丹", "章丹"]),
 ("shuiyin", "水银", "Hydrargyrum", "mineral", (), "金属", [], []),
 ("pengsha", "硼砂", "Borax", "mineral", (), "矿石加工品", [], ["月石", "蓬砂"]),
 ("zirantong", "自然铜", "Pyritum", "mineral", (), "矿石", [], ["煅自然铜"]),
 ("luganshi", "炉甘石", "Calamina", "mineral", (), "矿石", [], []),
 ("qingdai", "青黛", "Indigo Naturalis", "plant", ("leaf",), "叶或茎叶加工品",
  ["Strobilanthes cusia", "Baphicacanthus cusia", "Persicaria tinctoria", "Isatis tinctoria"], ["靛花", "青黛末"]),
 ("longdan", "龙胆", "Gentianae Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Gentiana scabra", "Gentiana manshurica", "Gentiana triflora", "Gentiana rigescens"], ["龙胆草", "草龙胆", "胆草"]),
 ("tinglizi", "葶苈子", "Descurainiae Semen Lepidii Semen", "plant", ("seed",), "种子",
  ["Descurainia sophia", "Lepidium apetalum"], ["葶苈", "甜葶苈", "苦葶苈", "葶苈子末"]),
 ("xuejie", "血竭", "Draconis Sanguis", "plant", ("resin",), "树脂",
  ["Calamus draco", "Daemonorops draco"], ["麒麟竭"]),
 ("wulingzhi", "五灵脂", "Trogopteri Faeces", "animal", ("faeces",), "粪便",
  ["Trogopterus xanthipes"], ["灵脂"]),
 ("gaoben", "藁本", "Ligustici Rhizoma et Radix", "plant", ("rhizome", "root"), "根茎和根",
  ["Ligusticum sinense", "Ligusticum jeholense"], ["川藁本", "北藁本"]),
 ("shichangpu", "石菖蒲", "Acori Tatarinowii Rhizoma", "plant", ("rhizome",), "根茎",
  ["Acorus tatarinowii", "Acorus gramineus"], ["菖蒲", "九节菖蒲", "节菖蒲", "石菖"]),
 ("caowu", "草乌", "Aconiti Kusnezoffii Radix", "plant", ("root",), "块根",
  ["Aconitum kusnezoffii"], ["草乌头", "制草乌", "生草乌"]),
 ("diyu", "地榆", "Sanguisorbae Radix", "plant", ("root",), "根",
  ["Sanguisorba officinalis"], ["地榆炭"]),
 ("bixie", "萆薢", "Dioscoreae Rhizoma (Bixie)", "plant", ("rhizome",), "根茎",
  ["Dioscorea hypoglauca", "Dioscorea spongiosa", "Dioscorea septemloba"], ["粉萆薢", "绵萆薢", "川萆薢"]),
 ("huhuanglian", "胡黄连", "Picrorhizae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Neopicrorhiza scrophulariiflora", "Picrorhiza scrophulariiflora"], ["胡连"]),
 ("jili", "蒺藜", "Tribuli Fructus", "plant", ("fruit",), "果实",
  ["Tribulus terrestris"], ["白蒺藜", "刺蒺藜", "蒺藜子", "炒蒺藜"]),
 ("bailian", "白蔹", "Ampelopsis Radix", "plant", ("root",), "块根",
  ["Ampelopsis japonica"], []),
 ("tianxiong", "天雄", "Aconiti Radix (Tianxiong)", "plant", ("root",), "块根",
  ["Aconitum carmichaelii"], []),
 ("manjingzi", "蔓荆子", "Viticis Fructus", "plant", ("fruit",), "果实",
  ["Vitex trifolia"], ["蔓荆", "蔓荆实"]),
 ("yuliren", "郁李仁", "Pruni Semen", "plant", ("seed",), "种子",
  ["Prunus humilis", "Prunus japonica", "Prunus pedunculata"], ["郁李"]),
 ("luhui", "芦荟", "Aloe", "plant", ("leaf", "juice"), "叶汁浓缩物",
  ["Aloe vera", "Aloe ferox"], ["卢会", "奴会"]),
 ("baiji", "白及", "Bletillae Rhizoma", "plant", ("tuber",), "块茎",
  ["Bletilla striata"], ["白芨"]),
 ("chixiaodou", "赤小豆", "Vignae Semen", "plant", ("seed",), "种子",
  ["Vigna umbellata", "Vigna angularis"], ["赤豆", "红小豆"]),
 ("ganqi", "干漆", "Toxicodendri Resina", "plant", ("resin",), "树脂加工品",
  ["Toxicodendron vernicifluum"], ["漆"]),
 ("wubeizi", "五倍子", "Galla Chinensis", "plant", ("gall",), "虫瘿",
  ["Rhus chinensis"], ["文蛤", "百药煎"]),
 ("yuanhua", "芫花", "Genkwa Flos", "plant", ("flower", "bud"), "花蕾",
  ["Daphne genkwa"], ["醋芫花"]),
 ("gansui", "甘遂", "Kansui Radix", "plant", ("root",), "块根",
  ["Euphorbia kansui"], ["醋甘遂"]),
 ("awei", "阿魏", "Ferulae Resina", "plant", ("resin",), "树脂",
  ["Ferula sinkiangensis", "Ferula fukanensis"], []),
 ("congbai", "葱白", "Allii Fistulosi Bulbus", "plant", ("bulb",), "鳞茎",
  ["Allium fistulosum"], ["葱", "连须葱白", "葱头", "葱白头", "葱汁"]),
 ("chansu", "蟾酥", "Bufonis Venenum", "animal", ("secretion",), "分泌物",
  ["Bufo gargarizans", "Duttaphrynus melanostictus"], ["蟾蜍", "干蟾"]),
 ("huangla", "蜂蜡", "Cera Flava", "animal", ("wax",), "蜡",
  ["Apis cerana", "Apis mellifera"], ["黄蜡", "白蜡", "蜡"]),
 ("pipaye", "枇杷叶", "Eriobotryae Folium", "plant", ("leaf",), "叶",
  ["Eriobotrya japonica"], ["炙枇杷叶", "蜜枇杷叶"]),
 ("baijiezi", "芥子", "Sinapis Semen", "plant", ("seed",), "种子",
  ["Sinapis alba", "Brassica juncea"], ["白芥子", "芥菜子", "黄芥子"]),
 ("muzei", "木贼", "Equiseti Hiemalis Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Equisetum hyemale"], ["木贼草"]),
 ("qumai", "瞿麦", "Dianthi Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Dianthus superbus", "Dianthus chinensis"], ["瞿麦穗"]),
 ("banmao", "斑蝥", "Mylabris", "animal", ("whole",), "干燥体",
  ["Mylabris phalerata", "Mylabris cichorii"], ["斑猫", "斑蟊"]),
 ("xiongdan", "熊胆", "Ursi Fellis Pulvis", "animal", ("bile",), "胆汁",
  ["Ursus thibetanus", "Ursus arctos"], ["熊胆粉"]),
 ("sumu", "苏木", "Sappan Lignum", "plant", ("wood",), "心材",
  ["Biancaea sappan", "Caesalpinia sappan"], ["苏方木"]),
 ("zicao", "紫草", "Arnebiae Radix", "plant", ("root",), "根",
  ["Arnebia euchroma", "Arnebia guttata", "Lithospermum erythrorhizon"], ["紫草茸", "紫草根", "硬紫草", "软紫草"]),
 ("sangpiaoxiao", "桑螵蛸", "Mantidis Oötheca", "animal", ("egg case",), "卵鞘",
  ["Tenodera sinensis", "Statilia maculata", "Hierodula patellifera"], []),
 ("heidou", "黑豆", "Sojae Semen Nigrum", "plant", ("seed",), "种子",
  ["Glycine max"], ["黑大豆", "大豆", "乌豆", "大豆黄卷", "黄豆"]),
 ("dandouchi", "淡豆豉", "Sojae Semen Praeparatum", "plant", ("seed",), "发酵种子",
  ["Glycine max"], ["豉", "豆豉", "香豉", "淡豉"]),
 ("ercha", "儿茶", "Catechu", "plant", ("extract",), "枝干煎膏",
  ["Senegalia catechu", "Acacia catechu"], ["孩儿茶"]),
 ("jingmi", "粳米", "Oryzae Semen", "plant", ("seed",), "种仁",
  ["Oryza sativa"], ["糯米", "米", "陈仓米", "陈米", "白米", "大米", "秫米", "粳米饭"]),
 ("hugu", "虎骨", "Tigris Os", "animal", ("bone",), "骨骼",
  ["Panthera tigris"], ["虎胫骨", "虎头骨", "虎睛"]),
 ("wuyi", "芜荑", "Ulmi Macrocarpae Fructus Praeparatus", "plant", ("fruit",), "果实加工品",
  ["Ulmus macrocarpa"], []),
 ("qingmuxiang", "青木香", "Aristolochiae Radix", "plant", ("root",), "根",
  ["Aristolochia debilis"], []),
 ("ruiren", "蕤仁", "Prinsepiae Nux", "plant", ("seed",), "果核",
  ["Prinsepia uniflora"], ["蕤核"]),
 ("wushaoshe", "乌梢蛇", "Zaocys", "animal", ("whole",), "干燥体",
  ["Ptyas dhumnades", "Zaocys dhumnades"], ["乌蛇", "乌梢蛇肉", "乌蛇肉"]),
 ("jingdaji", "京大戟", "Euphorbiae Pekinensis Radix", "plant", ("root",), "根",
  ["Euphorbia pekinensis"], ["大戟"]),
 ("huaihua", "槐花", "Sophorae Flos", "plant", ("flower",), "花及花蕾",
  ["Styphnolobium japonicum", "Sophora japonica"], ["槐米", "槐实", "槐角", "槐子"]),
 ("baiwei", "白薇", "Cynanchi Atrati Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Vincetoxicum atratum", "Cynanchum atratum", "Vincetoxicum versicolor", "Cynanchum versicolor"], []),
 ("gansong", "甘松", "Nardostachyos Radix et Rhizoma", "plant", ("root", "rhizome"), "根及根茎",
  ["Nardostachys jatamansi"], ["甘松香"]),
 ("haitongpi", "海桐皮", "Erythrinae Cortex", "plant", ("bark",), "树皮",
  ["Erythrina variegata"], []),
 ("leiwan", "雷丸", "Omphalia", "fungus", ("sclerotium",), "菌核",
  ["Omphalia lapidescens", "Laccocephalum mylittae"], []),
 ("juemingzi", "决明子", "Cassiae Semen", "plant", ("seed",), "种子",
  ["Senna obtusifolia", "Senna tora", "Cassia obtusifolia"], ["草决明", "马蹄决明"]),
 ("mangcao", "莽草", "Illicii Lanceolati Folium", "plant", ("leaf",), "叶",
  ["Illicium lanceolatum"], []),
 ("tanxiang", "檀香", "Santali Albi Lignum", "plant", ("wood",), "心材",
  ["Santalum album"], ["白檀香", "紫檀香", "白檀"]),
 ("qingxiangzi", "青葙子", "Celosiae Semen", "plant", ("seed",), "种子",
  ["Celosia argentea"], []),
 ("haizao", "海藻", "Sargassum", "plant", ("thallus",), "藻体",
  ["Sargassum pallidum", "Sargassum fusiforme"], ["海带", "昆布"]),
 ("zelan", "泽兰", "Lycopi Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Lycopus lucidus"], ["泽兰叶"]),
 ("baihuashe", "蕲蛇", "Agkistrodon", "animal", ("whole",), "干燥体",
  ["Deinagkistrodon acutus", "Agkistrodon acutus"], ["白花蛇", "蕲蛇肉", "白花蛇肉"]),
 ("laifuzi", "莱菔子", "Raphani Semen", "plant", ("seed",), "种子",
  ["Raphanus sativus"], ["萝卜子", "萝菔子", "莱菔", "萝卜"]),
 ("haipiaoxiao", "海螵蛸", "Sepiae Endoconcha", "animal", ("shell",), "内壳",
  ["Sepiella maindroni", "Sepia esculenta"], ["乌贼骨", "乌鲗骨", "乌贼鱼骨"]),
 ("lilu", "藜芦", "Veratri Nigri Radix et Rhizoma", "plant", ("root", "rhizome"), "根及根茎",
  ["Veratrum nigrum"], []),
 ("mahuanggen", "麻黄根", "Ephedrae Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Ephedra sinica", "Ephedra intermedia"], []),
 ("huashanyao", "蛤蚧", "Gecko", "animal", ("whole",), "干燥体",
  ["Gekko gecko"], []),
 ("hupo", "琥珀", "Succinum", "other", (), "古代松科植物树脂化石", [], ["血珀", "琥珀末"]),
 ("naosha", "硇砂", "Sal Ammoniac", "mineral", (), "矿石", [], ["白硇砂", "紫硇砂"]),
 ("qingyan", "青盐", "Halitum", "mineral", (), "矿石", [], ["戎盐", "食盐", "盐", "大青盐"]),
 ("jinbo", "金箔", "Aurum Foliatum", "mineral", (), "金属", [], ["金", "金屑"]),
 ("mituoseng", "密陀僧", "Lithargyrum", "mineral", (), "加工品", [], []),
 ("tiefen", "铁粉", "Ferri Pulvis", "mineral", (), "金属", [], ["铁精", "铁华粉", "铁落", "生铁落"]),
 ("tonglv", "铜绿", "Aerugo", "mineral", (), "加工品", [], ["铜青"]),
 ("danfan", "胆矾", "Chalcanthitum", "mineral", (), "矿石", [], ["石胆"]),
 ("yinbo", "银箔", "Argentum Foliatum", "mineral", (), "金属", [], ["银"]),
 ("zishiying", "紫石英", "Fluoritum", "mineral", (), "矿石", [], []),
 ("yangqishi", "阳起石", "Actinolitum", "mineral", (), "矿石", [], []),
 ("jiu", "酒", "Vinum", "other", (), "酒", [], ["黄酒", "清酒", "好酒", "醇酒", "无灰酒"]),
 ("cu", "醋", "Acetum", "other", (), "醋", [], ["米醋", "苦酒", "醋汁"]),
 ("tongbian", "童便", "Urina Hominis", "other", (), "人尿", [], ["童子小便", "小便"]),
 ("xiaoshi", "硝石", "Nitrum", "mineral", (), "矿石", [], ["消石", "火消", "火硝", "焰硝"]),
 ("loulu", "漏芦", "Rhapontici Radix", "plant", ("root",), "根",
  ["Rhaponticum uniflorum"], []),
 ("daqingye", "大青叶", "Isatidis Folium", "plant", ("leaf",), "叶",
  ["Isatis tinctoria", "Isatis indigotica"], ["大青", "大青草"]),
 ("anxixiang", "安息香", "Benzoinum", "plant", ("resin",), "树脂",
  ["Styrax tonkinensis"], []),
 ("baibu", "百部", "Stemonae Radix", "plant", ("root",), "块根",
  ["Stemona sessilifolia", "Stemona japonica", "Stemona tuberosa"], ["炙百部"]),
 ("qinghao", "青蒿", "Artemisiae Annuae Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Artemisia annua"], ["青蒿子", "草蒿"]),
 ("fengfang", "蜂房", "Vespae Nidus", "animal", ("nest",), "巢",
  ["Polistes olivaceus", "Polistes japonicus", "Parapolybia varia"], ["露蜂房", "蜂窠"]),
 ("songzhi", "松香", "Colophonium", "plant", ("resin",), "树脂",
  ["Pinus massoniana", "Pinus tabuliformis"], ["松脂", "松胶", "沥青"]),
 ("gujingcao", "谷精草", "Eriocauli Flos", "plant", ("flower",), "带花茎的头状花序",
  ["Eriocaulon buergerianum"], ["谷精"]),
 ("xiangru", "香薷", "Moslae Herba", "plant", ("herb", "aerial"), "地上部分",
  ["Mosla chinensis"], ["香茹", "陈香薷"]),
 ("hake", "蛤壳", "Meretricis Concha Cyclinae Concha", "animal", ("shell",), "贝壳",
  ["Meretrix meretrix", "Cyclina sinensis"], ["蛤粉", "海蛤", "海蛤壳", "海蛤粉", "文蛤粉"]),
 ("liuzhi", "柳枝", "Salicis Babylonicae Ramulus", "plant", ("twig",), "枝",
  ["Salix babylonica"], ["柳条", "垂柳枝"]),
 ("guanzhong", "绵马贯众", "Dryopteridis Crassirhizomatis Rhizoma", "plant", ("rhizome",), "根茎及叶柄残基",
  ["Dryopteris crassirhizoma"], ["贯众", "贯仲"]),
 ("zhima", "黑芝麻", "Sesami Semen Nigrum", "plant", ("seed",), "种子",
  ["Sesamum indicum"], ["胡麻", "麻油", "香油", "芝麻", "胡麻仁", "巨胜子", "芝麻油", "脂麻"]),
 ("yingsuqiao", "罂粟壳", "Papaveris Pericarpium", "plant", ("pericarp",), "果壳",
  ["Papaver somniferum"], ["粟壳", "御米壳", "米壳", "罂粟"]),
 ("shetui", "蛇蜕", "Serpentis Periostracum", "animal", ("exuvia",), "表皮膜",
  ["Elaphe carinata", "Elaphe taeniura", "Ptyas dhumnades"], ["蛇蜕皮", "蛇皮", "龙衣"]),
 ("mengchong", "虻虫", "Tabanus", "animal", ("whole",), "雌虫干燥体",
  ["Tabanus bivittatus"], ["蝱虫"]),
 ("xiakucao", "夏枯草", "Prunellae Spica", "plant", ("spike", "fruit"), "果穗",
  ["Prunella vulgaris"], ["夏枯", "夏枯草穗"]),
 ("heshi", "鹤虱", "Carpesii Fructus", "plant", ("fruit",), "果实",
  ["Carpesium abrotanoides"], []),
 ("shiwei", "石韦", "Pyrrosiae Folium", "plant", ("leaf",), "叶",
  ["Pyrrosia sheareri", "Pyrrosia lingua", "Pyrrosia petiolosa"], ["石苇"]),
 ("dongkuizi", "冬葵果", "Malvae Fructus", "plant", ("fruit", "seed"), "果实",
  ["Malva verticillata"], ["冬葵子", "葵子", "葵菜子"]),
 ("qinpi", "秦皮", "Fraxini Cortex", "plant", ("bark",), "枝皮或干皮",
  ["Fraxinus chinensis subsp. rhynchophylla", "Fraxinus chinensis"], []),
 ("zhangnao", "樟脑", "Camphora", "plant", ("wood", "extract"), "枝叶提取物",
  ["Cinnamomum camphora"], ["潮脑", "韶脑"]),
 ("bimazi", "蓖麻子", "Ricini Semen", "plant", ("seed",), "种子",
  ["Ricinus communis"], ["蓖麻仁", "蓖麻", "萆麻子"]),
 ("shandougen", "山豆根", "Sophorae Tonkinensis Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Sophora tonkinensis"], ["广豆根"]),
 ("yemingsha", "夜明砂", "Vespertilionis Faeces", "animal", ("faeces",), "粪便",
  ["Vespertilio sinensis", "Vespertilio superans"], []),
 ("difuzi", "地肤子", "Kochiae Fructus", "plant", ("fruit",), "果实",
  ["Bassia scoparia", "Kochia scoparia"], []),
 ("huaizhi", "槐枝", "Sophorae Ramulus", "plant", ("twig",), "嫩枝",
  ["Styphnolobium japonicum", "Sophora japonica"], ["槐条", "槐白皮"]),
 ("tuguagen", "土瓜根", "Trichosanthis Cucumeroidis Radix", "plant", ("root",), "根",
  ["Trichosanthes cucumeroides"], ["王瓜根"]),
 ("yinyu", "茵芋", "Skimmiae Folium", "plant", ("leaf", "stem"), "茎叶",
  ["Skimmia reevesiana"], []),
 ("tufuling", "土茯苓", "Smilacis Glabrae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Smilax glabra"], ["仙遗粮"]),
 ("madouling", "马兜铃", "Aristolochiae Fructus", "plant", ("fruit",), "果实",
  ["Aristolochia contorta", "Aristolochia debilis"], ["兜铃"]),
 ("pugongying", "蒲公英", "Taraxaci Herba", "plant", ("herb",), "全草",
  ["Taraxacum mongolicum"], ["黄花地丁"]),
 ("sangzhi", "桑枝", "Mori Ramulus", "plant", ("twig",), "嫩枝",
  ["Morus alba"], ["嫩桑枝", "桑条"]),
 ("xiaomai", "小麦", "Tritici Fructus", "plant", ("fruit", "seed"), "颖果",
  ["Triticum aestivum"], ["浮小麦", "白面", "面", "麦", "干面", "麦面", "小麦面"]),
 ("cebaiye", "侧柏叶", "Platycladi Cacumen", "plant", ("leaf", "twig"), "枝梢和叶",
  ["Platycladus orientalis"], ["柏叶", "侧柏", "柏枝"]),
 ("linglingxiang", "零陵香", "Lysimachiae Foenum-graeci Herba", "plant", ("herb",), "全草",
  ["Lysimachia foenum-graecum"], ["薰草"]),
 ("guadi", "甜瓜蒂", "Melo Pedicellus", "plant", ("pedicel",), "果柄",
  ["Cucumis melo"], ["瓜蒂", "苦丁香"]),
 ("cangerzi", "苍耳子", "Xanthii Fructus", "plant", ("fruit",), "果实",
  ["Xanthium strumarium", "Xanthium sibiricum"], ["苍耳", "枲耳实", "苍耳草"]),
 ("liujinu", "刘寄奴", "Artemisiae Anomalae Herba", "plant", ("herb",), "全草",
  ["Artemisia anomala"], []),
 ("baijiaoxiang", "枫香脂", "Liquidambaris Resina", "plant", ("resin",), "树脂",
  ["Liquidambar formosana"], ["白胶香", "枫香"]),
 ("guijianyu", "鬼箭羽", "Euonymi Alati Ramulus", "plant", ("twig",), "具翅状物的枝条",
  ["Euonymus alatus"], ["卫矛"]),
 ("xiebai", "薤白", "Allii Macrostemonis Bulbus", "plant", ("bulb",), "鳞茎",
  ["Allium macrostemon"], ["薤", "薤白头"]),
 ("baimaogen", "白茅根", "Imperatae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Imperata cylindrica"], ["茅根", "茅针", "鲜茅根"]),
 ("huluba", "胡芦巴", "Trigonellae Semen", "plant", ("seed",), "种子",
  ["Trigonella foenum-graecum"], ["葫芦巴", "胡卢巴"]),
 ("weipi", "刺猬皮", "Erinacei Corium", "animal", ("skin",), "皮",
  ["Erinaceus europaeus", "Erinaceus amurensis"], ["猬皮", "刺猬皮"]),
 ("lvdou", "绿豆", "Phaseoli Radiati Semen", "plant", ("seed",), "种子",
  ["Vigna radiata"], ["绿豆粉", "绿豆皮"]),
 ("zhuzhi", "猪脂", "Suis Adeps", "animal", ("fat",), "脂肪",
  ["Sus scrofa"], ["猪脂膏", "猪油", "猪膏", "腊猪脂", "猪胆", "猪胆汁", "猪肚", "猪腰子", "猪肾"]),
 ("jiuzi", "韭菜子", "Allii Tuberosi Semen", "plant", ("seed",), "种子",
  ["Allium tuberosum"], ["韭子", "韭菜", "韭根", "韭白"]),
 ("taozhi", "桃枝", "Persicae Ramulus", "plant", ("twig",), "枝",
  ["Prunus persica"], ["桃叶", "桃花", "桃奴", "桃树皮"]),
 ("jineijin", "鸡内金", "Galli Gigerii Endothelium Corneum", "animal", ("gizzard lining",), "砂囊内壁",
  ["Gallus gallus"], ["鸡肫皮", "鸡子", "鸡子清", "鸡子黄", "鸡蛋", "鸡屎白", "乌鸡"]),
 ("shanglu", "商陆", "Phytolaccae Radix", "plant", ("root",), "根",
  ["Phytolacca acinosa", "Phytolacca americana"], ["商陆根"]),
 ("wangbuliuxing", "王不留行", "Vaccariae Semen", "plant", ("seed",), "种子",
  ["Gypsophila vaccaria", "Vaccaria segetalis"], ["王不留"]),
 ("baiqian", "白前", "Cynanchi Stauntonii Rhizoma et Radix", "plant", ("rhizome", "root"), "根茎和根",
  ["Vincetoxicum stauntonii", "Cynanchum stauntonii", "Vincetoxicum glaucescens", "Cynanchum glaucescens"], []),
 ("mimenghua", "密蒙花", "Buddlejae Flos", "plant", ("flower",), "花蕾及花序",
  ["Buddleja officinalis"], []),
 ("moshizi", "没食子", "Galla Turcica", "plant", ("gall",), "虫瘿",
  ["Quercus infectoria"], ["没石子", "无食子"]),
 ("juanbai", "卷柏", "Selaginellae Herba", "plant", ("herb",), "全草",
  ["Selaginella tamariscina"], []),
 ("qiancao", "茜草", "Rubiae Radix et Rhizoma", "plant", ("root", "rhizome"), "根和根茎",
  ["Rubia cordifolia"], ["茜根", "茜草根"]),
 ("shinan", "石楠叶", "Photiniae Folium", "plant", ("leaf",), "叶",
  ["Photinia serratifolia"], ["石南", "石南叶"]),
 ("haijinsha", "海金沙", "Lygodii Spora", "plant", ("spore",), "孢子",
  ["Lygodium japonicum"], []),
 ("yubaipi", "榆白皮", "Ulmi Pumilae Cortex", "plant", ("bark",), "树皮或根皮",
  ["Ulmus pumila"], ["榆皮"]),
 ("liangtoujian", "两头尖", "Anemones Raddeanae Rhizoma", "plant", ("rhizome",), "根茎",
  ["Anemone raddeana"], []),
 ("qianglang", "蜣螂", "Catharsius", "animal", ("whole",), "干燥体",
  ["Catharsius molossus"], []),
 ("shiliupi", "石榴皮", "Granati Pericarpium", "plant", ("pericarp",), "果皮",
  ["Punica granatum"], ["酸石榴皮", "石榴", "安石榴皮"]),
 ("jiangxiang", "降香", "Dalbergiae Odoriferae Lignum", "plant", ("wood",), "心材",
  ["Dalbergia odorifera"], ["降真香"]),
 ("suoyang", "锁阳", "Cynomorii Herba", "plant", ("stem",), "肉质茎",
  ["Cynomorium songaricum"], []),
 ("qianjinzi", "千金子", "Euphorbiae Semen", "plant", ("seed",), "种子",
  ["Euphorbia lathyris"], ["续随子", "千金子霜"]),
 ("hetaoren", "核桃仁", "Juglandis Semen", "plant", ("seed",), "种子",
  ["Juglans regia"], ["胡桃肉", "胡桃", "核桃", "胡桃仁"]),
 ("dafengzi", "大风子", "Hydnocarpi Semen", "plant", ("seed",), "种子",
  ["Hydnocarpus anthelminthicus"], ["大枫子"]),
 ("chushizi", "楮实子", "Broussonetiae Fructus", "plant", ("fruit",), "果实",
  ["Broussonetia papyrifera"], ["楮实"]),
 ("tianxianzi", "天仙子", "Hyoscyami Semen", "plant", ("seed",), "种子",
  ["Hyoscyamus niger"], ["莨菪子", "莨菪"]),
 ("sumi", "粟米", "Setariae Semen", "plant", ("seed",), "种仁",
  ["Setaria italica"], ["小米", "粟", "谷芽", "粟芽"]),
 ("xinyi", "辛夷", "Magnoliae Flos", "plant", ("flower", "bud"), "花蕾",
  ["Magnolia biondii", "Yulania denudata", "Magnolia denudata", "Magnolia sprengeri"], ["辛夷花", "木笔花"]),
 ("zihuadiding", "紫花地丁", "Violae Herba", "plant", ("herb",), "全草",
  ["Viola philippica", "Viola yedoensis"], ["地丁", "地丁草"]),
 ("furongye", "木芙蓉叶", "Hibisci Mutabilis Folium", "plant", ("leaf",), "叶",
  ["Hibiscus mutabilis"], ["芙蓉叶", "芙蓉花"]),
 ("zhachan", "蚱蝉", "Cryptotympana", "animal", ("whole",), "干燥体",
  ["Cryptotympana atrata"], ["蝉"]),
 ("juhe", "橘核", "Citri Reticulatae Semen", "plant", ("seed",), "种子",
  ["Citrus reticulata"], ["橘子仁", "橘仁"]),
 ("baitouweng", "白头翁", "Pulsatillae Radix", "plant", ("root",), "根",
  ["Pulsatilla chinensis"], []),
 ("xiangpi", "象皮", "Elephatis Corium", "animal", ("skin",), "皮",
  ["Elephas maximus"], []),
 ("baicaoshuang", "百草霜", "Fuligo Plantae", "other", (), "杂草燃烧后的烟灰", [], ["锅底灰", "灶突墨"]),
 ("ziheche", "紫河车", "Placenta Hominis", "other", (), "人胎盘", [], ["胞衣", "河车"]),
 ("tianlinggai", "天灵盖", "Os Cranii Hominis", "other", (), "人颅骨", [], []),
 ("fenshuang", "粉霜", "Hydrargyri Chloridum", "mineral", (), "加工品", [], ["白粉霜", "升药"]),
 ("renzhongbai", "人中白", "Urinae Hominis Sedimen", "other", (), "人尿沉淀", [], ["人中黄"]),
 ("hufen", "铅粉", "Cerussa", "mineral", (), "加工品", [], ["胡粉", "定粉", "官粉", "铅白霜", "铅霜", "黑铅", "铅"]),
 ("yuyuliang", "禹余粮", "Limonitum", "mineral", (), "矿石", [], ["禹粮石", "太一余粮"]),
 ("zhongru", "钟乳石", "Stalactitum", "mineral", (), "矿石", [], ["钟乳", "钟乳粉", "石钟乳", "鹅管石"]),
 ("fulonggan", "灶心土", "Terra Flava Usta", "mineral", (), "烧结土", [], ["伏龙肝"]),
 ("baishizhi", "白石脂", "Kaolinitum", "mineral", (), "矿石", [], []),
 ("cihuang", "雌黄", "Orpimentum", "mineral", (), "矿石", [], []),
 ("pishuang", "砒石", "Arsenicum", "mineral", (), "矿石", [], ["砒霜", "信石", "白砒", "红砒"]),
 ("baishiying", "白石英", "Quartz Album", "mineral", (), "矿石", [], ["石英"]),
 ("yinzhu", "银朱", "Vermilion", "mineral", (), "加工品", [], []),
 ("lvfan", "绿矾", "Melanteritum", "mineral", (), "矿石", [], ["皂矾", "青矾"]),
 ("mengshi", "礞石", "Lapis Chloriti", "mineral", (), "矿石", [], ["青礞石", "金礞石"]),
 ("shihui", "石灰", "Calx", "mineral", (), "矿石加工品", [], ["风化石灰", "陈石灰"]),
 ("zhensha", "针砂", "Ferri Acus Pulvis", "mineral", (), "加工品", [], []),
 ("xueyu", "血余炭", "Crinis Carbonisatus", "other", (), "人发炭", [], ["血余", "乱发", "乱发灰", "发灰", "头发"]),
 ("xiangmo", "墨", "Atramentum", "other", (), "松烟墨", [], ["香墨", "京墨", "陈墨"]),
 ("su", "酥", "Butyrum", "animal", ("milk fat",), "乳脂", ["Bos taurus"], ["牛酥", "酥油", "牛乳", "乳汁"]),
]

MATERIA: Mapping[str, MateriaEntry] = {
    r[0]: MateriaEntry(r[0], r[1], r[2], r[3], tuple(r[4]), r[5], tuple(r[6]), tuple(r[7]))
    for r in _ROWS}

#: Names that resolved only by stripping 川, 大, 小 or 白, or by reading a part word as
#: processing, each checked against what it denotes and kept because it is the drug it is
#: listed under: 川当归 is 当归 from Sichuan, 大半夏 a large 半夏, 白云苓 is 茯苓, 败龟版 is
#: 龟甲, 枸杞根皮 is 地骨皮. Where a part has no entry of its own the drug's entry already
#: holds that part (瓜蒌仁 is in 瓜蒌, 皂角刺 in 皂荚), and these follow it. Names of the same
#: forms that denote something else stay unresolved: 川牛膝, 川木通, 川木香 and 白丁香 are
#: entries of their own, 大麦 is barley, 大麻子 (hemp or castor), 胡麻子 (sesame or flax) and
#: 大椒 are ambiguous, and 牛蒡根, 车前叶, 红花子 are other parts. Three were a different
#: species under the old rules: 莲子草 is 墨旱莲 (not 莲子), 金头蜈蚣 a centipede (not 金箔).
_REVIEWED_ALIASES: Mapping[str, str] = {
    # 川: the drug from its Sichuan origin
    "川山甲": "chuanshanjia", "川朴消": "mangxiao", "川芒消": "mangxiao", "川当归": "danggui",
    "川归身": "danggui", "川姜": "ganjiang", "川白姜": "ganjiang", "川干姜": "ganjiang",
    "川巴戟": "bajitian", "小川连": "huanglian", "川雅连": "huanglian", "川椒红": "huajiao",
    "川附片": "fuzi", "川五灵脂": "wulingzhi", "川桂枝": "guizhi", "川常山": "changshan",
    "川蜈蚣": "wugong", "川文蛤": "wubeizi",
    # 大 / 小: a size of the same drug
    "大川乌": "chuanwu", "大川乌头": "chuanwu", "大梅片": "bingpian", "大冰片": "bingpian",
    "大半夏": "banxia", "大川芎": "chuanxiong", "小川芎": "chuanxiong", "大芎": "chuanxiong",
    "大贝母": "zhebeimu", "大南星": "tiannanxing", "大天南星": "tiannanxing",
    "小枳实": "zhishi", "大当归": "danggui", "小生地": "shengdi", "小青皮": "qingpi",
    "大黑豆": "heidou", "小黑豆": "heidou", "大蜈蚣": "wugong", "大麦冬": "maidong",
    "大槟榔": "binglang", "大乌梅": "wumei", "大朱砂": "zhusha", "大粉草": "gancao",
    "大麻子仁": "huomaren", "大栀子": "zhizi", "大丁香": "dingxiang", "小红枣": "dazao",
    # 白: the drug's own colour or grade
    "白芜荑": "wuyi", "白槟榔": "binglang", "白滑石": "huashi", "白硼砂": "pengsha",
    "白龙脑": "bingpian", "白云苓": "fuling", "白粳米": "jingmi", "白石膏": "shigao",
    "白酒": "jiu", "白干姜": "ganjiang", "白通草": "tongcao", "白明矾": "baifan",
    "白雷丸": "leiwan", "白干葛": "gegen", "白松香": "songzhi",
    # a form of the drug that a part or product word left unresolved
    "香附米": "xiangfu", "诃子皮": "hezi", "白矾灰": "baifan", "猪牙皂荚": "zaojiao",
    "牙皂角": "zaojiao", "皂角针": "zaojiao", "皂荚刺": "zaojiao", "槐角子": "huaihua",
    "败龟版": "guijia", "枸杞根皮": "digupi", "枸杞根": "digupi", "黄柏皮": "huangbo",
    "菟丝饼": "tusizi", "百部根": "baibu", "瓜蒌子": "gualou", "栝楼子": "gualou",
    "三七根": "sanqi", "紫苏茎": "zisuye", "连翘壳": "lianqiao", "云母石": "yunmu",
    "禹余粮石": "yuyuliang", "牵牛头末": "qianniuzi", "黑丑头末": "qianniuzi",
    "马兜铃根": "qingmuxiang", "大麦蘖": "maiya", "麦糵面": "maiya", "香薷叶": "xiangru",
    "地榆根": "diyu", "猪脂油": "zhuzhi", "血余灰": "xueyu", "胡桃瓤": "hetaoren",
    "茯苓块": "fuling", "木瓜干": "mugua", "生姜自然汁": "shengjiang", "杏仁泥": "xingren",
    "蕲艾叶": "aiye",
    # a different species under the old rules
    "莲子草": "mohanlian", "金头蜈蚣": "wugong",
}


def _names() -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in MATERIA.values():
        for name in (entry.chinese, *entry.aliases):
            if name in out and out[name] != entry.id:
                raise ValueError(f"name {name!r} is claimed by {out[name]} and {entry.id}")
            out[name] = entry.id
    for name, drug in _REVIEWED_ALIASES.items():
        if drug not in MATERIA:
            raise ValueError(f"reviewed alias {name!r} names no entry {drug!r}")
        if name in out and out[name] != drug:
            raise ValueError(f"name {name!r} is claimed by {out[name]} and {drug}")
        out[name] = drug
    return out


_NAME_INDEX = _names()

#: Processing words written before or after a drug name (炙甘草, 酒大黄, 麸炒枳壳, 甘草末).
#: Stripped only when the remainder is itself a known name, so 生姜 stays 生姜 and 熟地黄
#: stays 熟地黄 (both listed as names in their own right).
#:
#: Besides processing, only words of quality or state are stripped (真阿胶, 嫩黄耆, 陈枳壳,
#: 鲜芦根, 干地龙): they leave the drug what it is. Words of origin, size or colour do not:
#: 川牛膝 is not 牛膝, 白丁香 is not 丁香 and 大麦 is not 小麦. Stripping 川, 大, 小 and 白
#: folded such near names into another species; the names of that form that do denote the
#: drug they contain (川当归, 大半夏, 白云苓) are reviewed aliases now (``_REVIEWED_ALIASES``).
_PREFIXES = ("麸炒", "土炒", "酒炒", "醋炒", "盐炒", "姜炒", "蜜炙", "酒洗", "酒浸", "醋炙",
             "炙", "炒", "焙", "煨", "煅", "酒", "醋", "盐", "蜜", "制", "生", "净", "焦",
             "真", "好", "上", "嫩", "新", "陈", "鲜", "干", "细")
_SUFFIXES = ("末", "粉", "片", "汁", "炭", "霜", "屑", "肉", "仁", "心", "头", "尖", "梢", "节")

#: Traditional characters and their simplified forms, for the characters this table's
#: names use. The names here are simplified, so a source written in traditional characters
#: (黃芩, 大棗, 乾薑, as Hong Kong sources write them) would otherwise resolve to nothing.
#: 252 pairs are OpenCC's ``TSCharacters`` and two its ``HKVariantsRev`` (枱, 衞), cut to
#: the simplified characters that occur in these names (Apache-2.0; see NOTICE). Where
#: OpenCC lists several simplified forms, the first is used (乾 → 干, 麴 → 曲).
_TRADITIONAL = (
    "乾亂佈倉側兒內兩凈劉勝參吳喬噁噹囌國圓實寶將崑帶幹廣恆惡懞懷捲撥撫敗斷於昇曬會朮東枱査梔條棗楓榦樓樸樹檯檳櫻歸殭殻殼決沒"
    "淨漢澤濛瀉瀝烏無煉燈燻爐牀牽獨甦當痠發皁盧眞眾矇硃礬禦稜穀竈筆節糧紅紋紙細絨絲綠綿緑縮續罌翹脛脫腎腦膚膠膩膽臘臺荊莖莢華"
    "萊葉葦葯蒼蓋蓮蓯蓽蔔蔘蔞蔥蕓蕪蕷薈薑藍藥藭藶蘄蘆蘇蘚蘞蘭蘿蛻蝟蝨螞蟬蟲蠍蠟蠣蠶衆術衚衛衞補製覈訶誌豬貓貝貞貫賊車軍軟輕連"
    "遠遺遼醜針釵鈎鈡鈴鉅鉛鉤銀銅錦鍊鍋鍼鍾鎖鏇鐘鐵門閤闆關陞陳陸陽隨雙雞雲靈韆韋須頭風颱飛飯餘馬驢髮鬆鬍鬚鬱魚鮮鰂鱉鵝鶴鷄鹽"
    "麗麥麩麪麫麯麴麵黃黨鼕齒龍龜")
_SIMPLIFIED = (
    "干乱布仓侧儿内两净刘胜参吴乔恶当苏国圆实宝将昆带干广恒恶蒙怀卷拨抚败断于升晒会术东台查栀条枣枫干楼朴树台槟樱归僵壳壳决没"
    "净汉泽蒙泻沥乌无炼灯熏炉床牵独苏当酸发皂卢真众蒙朱矾御棱谷灶笔节粮红纹纸细绒丝绿绵绿缩续罂翘胫脱肾脑肤胶腻胆腊台荆茎荚华"
    "莱叶苇药苍盖莲苁荜卜参蒌葱芸芜蓣荟姜蓝药䓖苈蕲芦苏藓蔹兰萝蜕猬虱蚂蝉虫蝎蜡蛎蚕众术胡卫卫补制核诃志猪猫贝贞贯贼车军软轻连"
    "远遗辽丑针钗钩钟铃巨铅钩银铜锦炼锅针钟锁旋钟铁门合板关升陈陆阳随双鸡云灵千韦须头风台飞饭余马驴发松胡须郁鱼鲜鲗鳖鹅鹤鸡盐"
    "丽麦麸面面曲曲面黄党冬齿龙龟")
_T2S = str.maketrans(_TRADITIONAL, _SIMPLIFIED)


def normalise_name(raw: str) -> str:
    """The bare name: parenthesised notes, spaces and trailing punctuation removed."""
    text = re.sub(r"[（(][^）)]*[）)]", "", raw or "")
    return re.sub(r"[\s　。，、；;,.:：]+", "", text)


def to_simplified(text: str) -> str:
    """``text`` with the traditional characters of this table's names simplified.

    Character by character, so it is for resolving a name and nothing else: its result is
    worth only as much as an exact match on a known name (``resolve_name`` of the result),
    and a character no name here uses is left as it was.
    """
    return (text or "").translate(_T2S)


def resolve_name(raw: str) -> str | None:
    """The table id a written ingredient name resolves to, or None.

    Exact names first; then one processing prefix and/or suffix is stripped, but only when
    the remainder is a known name. Anything else is unresolved, never approximated.
    """
    name = normalise_name(raw)
    if not name:
        return None
    if name in _NAME_INDEX:
        return _NAME_INDEX[name]
    candidates = [name]
    for p in _PREFIXES:
        if name.startswith(p) and len(name) > len(p) + 0:
            candidates.append(name[len(p):])
    for c in list(candidates):
        for s in _SUFFIXES:
            if c.endswith(s) and len(c) > len(s):
                candidates.append(c[: -len(s)])
    for c in candidates[1:]:
        if c in _NAME_INDEX:
            return _NAME_INDEX[c]
    return None


#: Words of origin, size or colour that make another drug of the name they precede: 川牛膝
#: is not 牛膝, 白附子 is not 附子, 土茯苓 is not 茯苓, 水半夏 is not 半夏. ``resolve_name``
#: never strips them; ``names_in`` uses them to tell a name from the end of another one.
IDENTITY_PREFIXES: tuple[str, ...] = ("川", "大", "小", "白", "土", "水")

#: What a formula or a preparation is called after the names in it (小柴胡汤, 大承气汤,
#: 黄芪注射液). An identity word in front of a herb's name, with one of these after it, is
#: the start of a formula's name, not a near name of the herb.
_FORMULA_ENDINGS = ("汤", "散", "丸", "饮", "方", "膏", "丹", "颗粒", "胶囊", "注射液", "片",
                    "口服液", "合剂", "糖浆", "冲剂", "滴丸", "软胶囊", "注射剂")

#: Everyday words that end in an identity word. In 减小附子剂量 the 小 belongs to 减小, and
#: in 加大黄芪用量 the 大 belongs to 加大: neither is part of the name after it. Place names
#: are left out on purpose, because 四川牛膝 may well mean 川牛膝.
_ENDS_IN_IDENTITY = frozenset({
    "加大", "增大", "较大", "过大", "最大", "放大", "扩大", "偏大", "很大", "更大",
    "减小", "较小", "最小", "缩小", "过小", "偏小", "很小", "更小",
    "蛋白", "空白", "苍白", "饮水", "温水", "冷水", "热水", "开水", "沸水"})


@dataclass(frozen=True)
class NameMention:
    """A drug name found in running text, and what it identifies.

    ``drug_id`` is None when the name as written identifies no recorded drug: an identity
    word stands before a known name and the two together are no name in the table
    (白首乌 before 首乌). ``contains`` is then the drug the known part would have named,
    which is the drug a careless reading would take it for.
    """

    name: str
    drug_id: str | None
    start: int
    contains: str | None = None

    @property
    def identified(self) -> bool:
        return self.drug_id is not None


@lru_cache(maxsize=1)
def _scan_index() -> dict[str, tuple[str, ...]]:
    by_first: dict[str, list[str]] = {}
    for name in _NAME_INDEX:
        if len(name) >= 2:                  # one character is a word, not a name to scan for
            by_first.setdefault(name[0], []).append(name)
    return {k: tuple(sorted(v, key=len, reverse=True)) for k, v in by_first.items()}


def names_in(text: str) -> list[NameMention]:
    """Every drug name written in ``text``, longest match first, in order of appearance.

    A name found right after an identity word is reported as the longer name, identified
    only if the table knows it: in 白首乌 the scanner finds 首乌, an alias of 何首乌, and
    reports 白首乌, unidentified, rather than 何首乌. Names of one character are not scanned
    for; they are words (姜, 酒, 盐) far more often than names in running text.

    An identity word that ends an everyday word belongs to that word: 减小附子 reads 附子,
    and 加大黄芪 reads 黄芪, not 大黄. 加大黄, with no name after the 大, still reads 大黄.
    """
    text = to_simplified(text or "")
    index = _scan_index()

    def starts_name(at: int) -> bool:
        return any(text.startswith(n, at) for n in index.get(text[at:at + 1], ()))

    out: list[NameMention] = []
    i = 0
    while i < len(text):
        if i > 0 and text[i - 1:i + 1] in _ENDS_IN_IDENTITY and starts_name(i + 1):
            i += 1
            continue
        for name in index.get(text[i], ()):
            if not text.startswith(name, i):
                continue
            drug = _NAME_INDEX[name]
            before = text[i - 1] if i > 0 else ""
            if before in IDENTITY_PREFIXES and text[i - 2:i] not in _ENDS_IN_IDENTITY:
                longer = before + name
                other = resolve_name(longer)
                if other != drug:
                    # 小柴胡汤 names a formula; it is no mention of 柴胡 at all.
                    if not text.startswith(_FORMULA_ENDINGS, i + len(name)):
                        out.append(NameMention(longer, other, i - 1, contains=drug))
                    i += len(name)
                    break
            out.append(NameMention(name, drug, i))
            i += len(name)
            break
        else:
            i += 1
    return out


def shared_name(a: str, b: str) -> str | None:
    """The longest drug name both ``a`` and ``b`` contain, if any: 附子 for 制白附子 and
    制附子, 牛膝 for 川牛膝 and 牛膝. Two names sharing one are near names; two that share
    only a character (黄芩, 黄芪) are not."""
    a, b = to_simplified(a), to_simplified(b)
    best: str | None = None
    for i in range(len(a)):
        for name in _scan_index().get(a[i], ()):
            if a.startswith(name, i) and name in b and (best is None or len(name) > len(best)):
                best = name
    return best


#: Processing that changes what a drug does, written before its name: 生附子 is the raw root,
#: far more toxic than 制附子, and 炙甘草 is not 生甘草. ``resolve_name`` strips these to find
#: the drug, which is right for identity; a claim must still not carry one state's evidence
#: to another. Words of quality (真, 好, 陈, 鲜 …) are not processing and are not listed.
PROCESSING_WORDS: tuple[str, ...] = ("麸炒", "土炒", "酒炒", "醋炒", "盐炒", "姜炒", "蜜炙",
                                     "酒洗", "酒浸", "醋炙", "炙", "炒", "焙", "煨", "煅",
                                     "酒", "醋", "盐", "蜜", "制", "生", "焦", "熟", "炮")

#: Processed forms listed under their own names, whose processing no prefix shows: 姜半夏,
#: 法半夏 and 清半夏 are 半夏 processed with ginger, with lime and liquorice, and with alum;
#: 生半夏 is the raw tuber, toxic as it is.
_PROCESSED_FORMS: Mapping[str, str] = {"姜半夏": "姜制", "法半夏": "法制", "清半夏": "清制"}

#: Everyday words that end in a processing word: in 未发生附子相关不良反应 the 生 belongs to
#: 发生, and in 抑制黄芪 the 制 to 抑制, not to the drug after them.
_ENDS_IN_PROCESSING = frozenset({
    "发生", "产生", "出生", "卫生", "学生", "医生", "先生", "再生", "新生", "衍生", "派生",
    "共生", "寄生", "野生", "养生", "人生", "终生", "一生", "伴生", "滋生",
    "抑制", "机制", "控制", "限制", "体制", "研制", "编制", "配制", "节制", "压制"})


def processing_of(mention: NameMention, text: str = "") -> str:
    """The processing state a drug name is written in, or "" when none is written.

    Read from the listed name itself (制附子 is a name of 附子 with 制 before it) or from
    the word immediately before the name in ``text`` (生 in 生附子, when only 附子 is
    listed). A processing word that ends an everyday word (发生, 抑制) is not read.
    """
    name = mention.name
    before = to_simplified(text or "")[:mention.start]
    if name in _PROCESSED_FORMS:
        return _PROCESSED_FORMS[name]
    for word in PROCESSING_WORDS:
        if (name.startswith(word) and len(name) > len(word)
                and resolve_name(name[len(word):]) == mention.drug_id):
            # 未发生附子…: the listed name 生附子 was read across the end of 发生.
            return "" if (before[-1:] + word) in _ENDS_IN_PROCESSING else word
    for word in PROCESSING_WORDS:
        if before.endswith(word):
            if before[-len(word) - 1:] in _ENDS_IN_PROCESSING:
                return ""
            return word
    return ""


@lru_cache(maxsize=4)
def _taxa(path: str) -> dict[str, dict]:
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("names", {})


def crude_drugs(taxa_file: str | Path = TAXA_FILE):
    """Every organism-derived entry as a ``herbs.CrudeDrug`` with NCBI-verified species.

    Species whose name NCBI did not resolve are left out; an entry left with none is
    left out too (and reported by :func:`unverified`).
    """
    from .herbs import HERBS, CrudeDrug, Species

    taxa = _taxa(str(taxa_file))
    out = {}
    for entry in MATERIA.values():
        if entry.drug_id in HERBS:
            out[entry.drug_id] = HERBS[entry.drug_id]   # the hand-checked gold herbs
            continue
        if not entry.has_organism:
            continue
        by_taxid: dict[str, list[str]] = {}
        for name in entry.species_names:
            rec = taxa.get(name)
            # A match on NCBI's "any name" index is accepted only once a person has checked
            # it: that index attaches "Cinnamomum cassia" to Neolitsea cassia, not to the
            # Pharmacopoeia's cassia bark.
            if rec and rec.get("taxid") and (rec.get("matched_on") != "All Names"
                                             or rec.get("reviewed")):
                by_taxid.setdefault(str(rec["taxid"]), []).append(name)
                if rec.get("scientific_name") and rec["scientific_name"] != name:
                    by_taxid[str(rec["taxid"])].append(rec["scientific_name"])
        if not by_taxid:
            continue
        species = tuple(Species(tid, names[0], tuple(dict.fromkeys(names[1:])))
                        for tid, names in sorted(by_taxid.items()))
        out[entry.drug_id] = CrudeDrug(entry.drug_id, entry.chinese, entry.latin, species,
                                       entry.parts, entry.part_zh)
    return out


def unverified(taxa_file: str | Path = TAXA_FILE) -> dict[str, list[str]]:
    """Entry id -> species names NCBI did not resolve."""
    taxa = _taxa(str(taxa_file))
    def ok(name: str) -> bool:
        rec = taxa.get(name) or {}
        return bool(rec.get("taxid")) and (rec.get("matched_on") != "All Names"
                                           or bool(rec.get("reviewed")))
    return {e.id: [n for n in e.species_names if not ok(n)]
            for e in MATERIA.values() if any(not ok(n) for n in e.species_names)}


@lru_cache(maxsize=1)
def all_drugs():
    """Every organism-derived drug with verified species, the four gold herbs included."""
    from .herbs import HERBS
    return {**crude_drugs(), **HERBS}
