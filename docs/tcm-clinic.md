# TCM syndrome differentiation and prescription support

`bioagent clinic` turns a structured 四诊 record into a syndrome differentiation and a
draft prescription. A licensed TCM practitioner then accepts, modifies or rejects the
draft. The tool never prescribes: a draft becomes a prescription only when a
practitioner signs it. Its knowledge pack is a transcription of named textbooks and the
Pharmacopoeia, and it says when no practitioner has reviewed it. The pack in this
release says exactly that.

```bash
bioagent clinic template --out intake.json        # the blank 四诊 form
bioagent clinic assess intake.json --out visit/   # red flags → 辨证 → 处方草案
bioagent clinic sign visit/ --practitioner 张三 --licence 110… --decision accept \
    --reason "pregnancy_unknown:桃仁=已确认未孕（尿hCG阴性）"
bioagent clinic verify visit/
bioagent clinic check intake.json --herbs 附子:6,法半夏:9   # check a prescription you wrote
bioagent clinic followup visits.json              # 证候积分 change, adverse events
bioagent clinic agreement cases.json --out eval/  # agreement with practitioners' labels
```

## The steps

| Step | What it does | Can stop the next step |
|---|---|---|
| 四诊 intake | Records each examination separately (望 inspection with the tongue, 闻 listening and smelling, 问 inquiry, 切 palpation with the pulse), as present findings and pertinent negatives, plus vital signs, medications, allergies and conditions. A field that was not asked (`null`) is kept distinct from one asked with nothing found (`[]`); a blank one ("", [""]), or an allergy field that holds only its label or says it was not asked or is not known (过敏史：, 待询问, 不详), records neither and is read as not asked. The template lists every finding the criteria score, in the 十问 order. | an unusable intake (no age, a male recorded as pregnant, an allergy given as a number or an object) |
| Red flags | Screens every entry against stop-and-refer rules: chest pain, stroke signs, impaired consciousness, bleeding, shock, self-harm, pregnancy emergencies, anaphylaxis and others. Vital signs are screened against NEWS2's single-parameter red scores. A flag stops the consultation until the intake records who cleared it and how. | yes: `refer` |
| Differentiation | Scores each syndrome's main and secondary findings, tongue and pulse. Matching is by term, synonym, containment or (at half weight) part of a term. Tongues and pulses are matched by features, and a tongue or pulse that contradicts the syndrome counts against it. The criteria decide the result: 主症 ≥ 2, 次症 ≥ 1 (or 主症 ≥ 3), the tongue or pulse agrees, and they do not both contradict. A specific syndrome is ranked before the general one it refines. Otherwise the result is the questions that would decide. | yes: `needs_information`; an emergency syndrome means `refer` |
| Draft | The base formula from 《方剂学》, fitted to the patient (details below) and then checked. | a `stop` issue makes it `blocked` |
| Sign-off | The practitioner accepts, modifies (the changes are checked again) or rejects the draft. Each `block` issue needs a recorded reason, and a `stop` issue cannot be signed. The sign-off names the session's digest. | — |

### Fitting the draft

- **Doses:** the textbook doses, scaled by age under 14 (the pack's 成人量 fractions). A
  dose above the Pharmacopoeia limit is drafted at the limit, and both values are shown.
- **加减:** the textbook modifications that match the recorded findings are suggested.
  They are applied only on request, and an added herb starts at the Pharmacopoeia
  lower limit.
- **Practitioner changes:** add, remove, replace and re-dose.

### The checks

Every prescription goes through the same checks, whether drafted or written by the
practitioner (`clinic check`):

| Check | Severity |
|---|---|
| 十八反 / 十九畏 (the pack's rules and the seed knowledge base) | block |
| Pregnancy: 禁用 | stop |
| Pregnancy: 慎用 | block |
| Pregnancy status unknown in a woman of child-bearing age | block |
| Allergy stated to the herb (R1: the entry is just names, or holds a reaction word or an allergy statement no negator stands right before), under a name a drug-level table gives it: by name, under any name the seed knowledge base gives it (黄耆 or 北芪 is 黄芪, 国老 is 甘草), a cited pack alias (夜交藤 is 首乌藤) or a reviewed alias of the materia table (川当归 is 当归), in pinyin or Latin (huáng qí, Radix Astragali), or another processed form of it (生地黄 and 熟地黄) | stop |
| Allergy that may be to the herb: such a name that the entry only mentions (R2, `allergy_mentioned`: other words around it and no un-negated reaction word: 否认黄芪过敏, 服用黄芪无不适, 目前在吃人参), a name only the materia table files under the herb's entry (丹皮 with 牡丹皮, 薯蓣 with 山药, but also 鸡蛋 with 鸡内金), a drug of the same species, or a species, genus or English common name of its source (芍药 and 赤芍, Panax ginseng, ginger), a formula of the pack or the knowledge base that holds it (四君子汤, 补中益气丸), one name inside the other (白附子 and 附子, 莱菔 and 莱菔子), the herb's name inside a longer word no table knows (郁金香, 知柏地黄丸), or 花粉 (pollen, and 天花粉's short name) | block |
| Interaction with a recorded medication, named by its class (利尿剂) or by its own name (呋塞米, Lasix) | warn |
| A recorded medication that neither the rules' class words nor the named medicines recognise | info |
| Caution for a recorded condition | warn |
| Toxic herb, with its processing and decoction notes | warn |
| Dose above the (age-scaled) range: toxic herb | block |
| Dose above the (age-scaled) range: other herb | warn |
| Dose below the range | info |
| Herb the pack does not hold | block |
| Patient under 1 year | block |
| Patient 65 or older | info |

Where the Pharmacopoeia and the knowledge base disagree, the stricter applies and the
report says the sources differ. For example, the Pharmacopoeia 2020 says 孕妇慎用 for
附子 and the seed's reading says 禁用, so 附子 in pregnancy is a stop.

#### How an allergy history is read

The check does not read a history through a list of wordings (过敏史：X, 对X过敏, X过敏史 …):
each wording such a list missed was a silent pass, and histories are written in more ways
than a list holds: 患者对国老过敏, 3年前北芪过敏, 北芪/人参过敏, 变应原：北芪, allergic to
huangqi. Each allergy entry, as written, is normalised and then scanned for every name the
tables hold (`bioagent.clinic.allergy`); whether it states an allergy to each name it holds
is then decided by one fixed rule (below). An allergy written among the conditions (黄芪过敏
there) is read the same way, as written (对黄芪、人参过敏 there names both): every entry of the
conditions that holds a word of allergy (过敏, 致敏, 变态反应, allerg…) is read whole, a
condition's name included. The 过敏 of 过敏性鼻炎 is a reaction word like any other (R1c), so
过敏性鼻炎，服用黄芪后出现皮疹, 过敏体质，黄芪 and 过敏性鼻炎，服用黄芪后好转 there all stop 黄芪.

- **Normalised.** NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens,
  variation selectors, the Hangul fillers, the braille blank, NUL and the other control
  characters removed; look-alike radicals that NFKC leaves (⻩ for 黄, ⻰ for 龙, as text
  copied from a PDF writes them) read as the characters they show; traditional characters
  folded to simplified with the materia table's own table (`materia.to_simplified`), so 黃耆
  is read as 黄耆, 人參 as 人参 and 當歸 as 当归, and with a table of the traditional, Hong
  Kong, Taiwan and Japanese forms that one leaves, for every character of every name the
  scan knows and of the words a history writes around them (過敏, 對, 否認, 腫脹, 黑順片, 人銜,
  四君子湯, 当帰, 芍薬; every character of every reaction word and negator of the rule, R1c, in
  its traditional form); tone marks dropped from Latin letters (Huáng Qí is huang qi); lower
  case. The second table was checked against OpenCC's s2t, s2hk and s2tw for every such
  character (OpenCC is not a dependency; the test runs where it is installed).
- **Scanned for the tables' names, and nothing else**: the seed knowledge base's herb names
  and aliases (黄耆, 北芪, 国老); every name of every entry of the materia table, one
  character included (姜, 枣); the pack's herbs and its cited aliases (`herb_aliases`,
  below); the formulas the pack and the knowledge base hold, under their own names and
  aliases (四君子汤, 阳旦汤) and in other product forms (补中益气丸 for 补中益气汤, 天麻钩藤颗粒
  for 天麻钩藤饮, 四物膏, 补中益气方; 散 and 饮 for the pack's formulas only, so 四逆散 is not the
  knowledge base's 四逆汤; 片 not after a single herb's name, since 麻黄片 is sliced 麻黄). No
  part or abbreviation of a name is scanned for unless a table lists it: an earlier scan of
  every part of every name read ordinary words (明天 of 明天麻, 淡黄 of 淡黄芩, 金子 of 金铃子)
  and was removed. Latin and pinyin are read as whole words, with or without spaces,
  hyphens, apostrophes, tone marks or tone numbers (huang qi, Huang-Qi, Huang'qi, huang2
  qi2, Huáng Qí): the knowledge base's pinyin and Latin names, the pack's pinyin of every one
  of its herbs (`herb_pinyin`), the Latin names of the materia table and the pack (首乌藤's
  Polygoni Multiflori Caulis) in the Pharmacopoeia 2020 word order and the older one
  (Astragali Radix and Radix Astragali), the species and genera the tables give (Panax
  ginseng, Astragalus), and the pack's cited English common names (`herb_common_names`:
  ginger, licorice, ginseng, cinnamon, astragalus, dong quai …). Every name at every place
  is a candidate, so a shorter name is not lost behind a longer one that starts at the same
  place: 枣皮疹 holds 枣 standing before 皮疹 as well as 枣皮 inside a longer word.
- **Read twice.** Once with whitespace as a hard boundary, so an allergy list written one
  per line or with spaces, 姜 / 黄芪, is 姜 and 黄芪; and once with whitespace removed, so
  北 芪 is 北芪. The names found are the union of the two readings. A name found only
  across whitespace is dropped where it overlaps a name of two characters or more that the
  first reading found standing (姜 / 黄芪 is never 姜黄, 姜 / 半夏 never 姜半夏), and where it
  stands in neither reading and overlaps a name the first reading found (枣 / 皮疹 is not 枣皮).
  Such a name is read only where it stands as one word in the second reading, or as a
  processed form, and never across a line break or a tab: 柑橘 皮疹 is not 橘皮, 猕猴桃 花生 not
  桃花, 花生 地塞米松 not 生地, 荞麦 冬瓜 not 麦冬, and 面粉, then 草莓 on the next line, not 粉草.
- **Read in its place.** A name *stands* when what is before it and after it is a boundary
  (the start or end of the entry, punctuation, whitespace, a digit, Latin letters) or one
  of the words a history writes around a name (对, 患者, 自诉, 因, 的, 吃, 一吃 … 就, 似乎,
  小时候, 年前, 服用, 过敏, 过敏史, 变应原, 及, 等, 均, 后, 皮疹, 水, 禁用 …), or another name
  of two characters or more that stands so on its far side: 北芪人参过敏 names both, and
  因黄芪过敏住院, 对黄芪的成分过敏 and 黄芪吃了过敏 name 黄芪. A formula's name stands only with
  a boundary of its own before it (柴胡桂枝汤 holds 桂枝汤 inside a longer word). A name of one
  character (姜, 枣, 归, 桂, 面) stands only with a boundary of its own on each side, from a
  shorter list of words (对, 服用, 吃, 及, 和, 因, 年, 中药, 过敏, 过敏史, 后, 就, 皮疹, 出现, 引起,
  致, 会, 严重, 特别, 否认 …): 对姜过敏, 姜过敏史, 、姜、, 服用姜后出现皮疹, 对姜严重过敏, 吃姜会过敏,
  吃姜出现皮疹, 姜致皮疹, 2019年姜过敏, 对中药姜过敏. Each pair of such a word and 姜, 归, 枣, 桂 or
  芎 was checked for an everyday word; the one found, 归类 (classify), is a word of its own.
  Inside any other word it is no name at all: 面部, 方便面, 姜某, 枣红色, 归档, 归类, 桂林, 沙姜 and
  姜片 give nothing.
  - A name inside a longer name that stands, or inside a formula's name, is that name's (杏仁
    in 苦杏仁, 地黄 in 六味地黄丸, 天麻 in 天麻钩藤颗粒), and the longer name is matched instead.
    A few everyday words that hold a name are words of their own and name nothing (胡萝卜,
    桂花, 桂鱼, 舌头).
  - A name of two characters or more that does not stand is *inside a longer word*: 郁金 in
    郁金香, 地黄 in 知柏地黄丸, 黄连 in 双黄连, 防风 in 预防风疹疫苗, 桂枝 and 茯苓 in 桂枝茯苓丸,
    白及 after 蛋白, 于术 after 对于 (unless the 对 stands as a word of its own, 于术 stands on its
    right, and its 术 does not start an everyday word there: 对于术过敏, 对于术及青霉素过敏,
    对于术等药物过敏 and 对于术有过敏史 are 对 + 于术 and stop 白术, which the knowledge base gives
    于术 to; 对于术后, 对于术中 and 由于术前 are 对于 + 术后). Where the 用 of 用于 ends a word of use
    (服用, 使用), the everyday word is not there: 服用于术后出现皮疹 is 服用 + 于术 + 后 and stops
    白术. Where the longer word is the name with a processing word
    before it, or a product suffix after it, it is that processed name (炙北芪, 炒夜交藤,
    甘草片, 山药粉). A name that starts with a denial's character and runs into what a denial
    names is the denial: 没药物过敏史 is 没 + 药物过敏史, not 没药.
- **The rule.** Whether an entry states an allergy to a name it holds is decided by one
  fixed rule, not by reading the story around the name. (Five rounds of a reading of what
  was taken, tolerated, improved or treated did not converge: each round's rules moved other
  cases, and some of its messages called a real reaction record "not an allergy record",
  which could lead a practitioner to sign through an allergy.) An entry is one record as
  written, read whole: a reaction word anywhere in it counts for every name in it, and two
  entries are read apart. For each name that stands in the entry and that a drug-level table
  says is the prescribed herb (below), the entry **states an allergy** (R1, `allergy`, a
  stop) when any of these holds:
  - **R1a, just names**: the entry is a list of names, with or without allergy and class labels
    (过敏史：, Allergy:, 药物：, 中药：, 草药：, Allergen name:), list marks (、，,；;/ + 和 及 与 或,
    and, or, a line break, a tab, a space next to a Chinese character or next to a name, an
    ASCII full stop with a space after it, a dash or a middle dot between two words), item
    numbers (1. 2. #1, (1), ① ② with or without spaces, 一、 二、, （一）) and 等: a name in an
    allergy field means an allergy by default. An item that holds a name holds nothing else, so
    any other word beside the name is context (对黄芪, 黄芪（-）). The tables hold no other
    allergen's name, so a list item that holds no name of theirs is another entry of the list
    (青霉素, 头孢, 对乙酰氨基酚, 无花果, 含碘造影剂, 香水, penicillin, codeine, Tylenol with codeine,
    vitamin A) unless it is words: it holds one of the context words of R2 and R6 as a whole
    word. They are of five kinds: a negator or none word (无, 未, 没, 不, 否, 否认, 没有, 未见, 未出现,
    未再出现, 无明显, 阴性, 未发现, 排除, 除外, 正常; no, not, none, nil, NKDA, NKA, NKFA, N/A, negative,
    neg, denies, denied, deny, without, never, except), not asked or not known (询问, 未问, 没问, 漏问,
    不详, 不清楚, 不知道, 未知, 待查, 拒, 拒答, 拒绝; unknown, unsure, deferred, asked, see, chart,
    notes), use and keeping (服, 用,
    吃, 喝, 泡, 炖, 煲, 煮, 熬, 服用, 服药, 使用, 用过, 吃过, 服过, 喝过, 用了, 吃了, 服了, 喝了, 用后, 在用,
    在吃, 在服, 正在, 目前, 现在, 平时, 平素, 长期, 每日, 每天, 经常, 常年, 常备, 家里, 家中, 煲汤, 炖汤, 熬汤,
    泡水, 泡茶, 煮水; take, takes, taking, took, taken, use, uses, using, used, on, currently,
    daily), tolerance, improvement and treatment (耐受, 可以, 可用, 可吃, 可服, 能吃, 能用, 能服, 良好,
    不适, 问题, 好转, 缓解, 改善, 减轻, 消退, 消失, 痊愈, 有效, 见效, 见好, 治疗, 主治, 调理, 进补;
    tolerated and the other words in tolera-, ok, okay, fine, improved, better, resolved, treated
    and the other words in treat-, can't, cannot), and a person (患者, 病人, 本人, 家属, 家人, 母亲,
    父亲, 妈妈, 爸爸, 医生, 医师, 大夫, 护士, 自诉, 自述, 代诉; patient, pt, family, mother, father,
    mom, dad, son, daughter, wife, husband, doctor, Dr., nurse, report, reports, reported,
    recorded). A Chinese word of one character counts only as a word of its own, with
    nothing but a mark, a space, a Latin letter or digit, an
    allergy word, a class word or another context word beside it (无, 无过敏, 不吃, 未用; not the 无
    of 无花果 or the 不 of 不锈钢); an English one only as a whole token. No item is words by its
    first or last character, or by a small word of English (with, in, a, an, the, for). 黄芪,
    过敏史：黄芪, 青霉素，黄芪, 青霉素 黄芪, 黄芪，对乙酰氨基酚, 草药：黄芪、人参, ①黄芪②人参, 一、黄芪
    二、人参, 黄芪+青霉素, penicillin huang qi, Huang Qi penicillin, huang qi. penicillin,
    penicillin/huang qi, huang qi, vitamin A and Allergy: huang qi stop; 黄芪，家里有, 黄芪，医生开的,
    黄芪，好转, 黄芪，平时煲汤, takes huang qi daily and Dr. Huang Jing are mentioned (R2). An item
    of other words is read as another allergen's name: the fail-safe side.
  - **R1b, an allergy statement** about the name: an allergy noun that no negator stands
    immediately before, in the name's statement. The nouns are the English one (allergy,
    allergies, allergen) and the labels without 过敏 (致敏原, 变应原, 药敏); a statement ends at
    a full stop (not an item's number), a semicolon, a question or exclamation mark, a line
    break or a tab, not at a comma, a colon or a dash. So huang qi allergy, allergy to huang
    qi, allergy to the herb huang qi, huang qi severe allergy, Allergies: huang qi, Allergies
    include huang qi and penicillin, Known allergies are huang qi, Allergies reviewed: huang qi
    and Allergies: #1 huang qi stop; no huang qi allergy, denies 黄芪 allergy and No allergies,
    takes huang qi are negated, and Allergies: none. Takes huang qi. holds the noun in another
    statement (R2). An exception states an allergy to what it excepts, so the name is stated
    when an exception word stands right before it (except, except to, except for, 除, 除了; not
    the 除 of 排除 or 解除, which except nothing) or right after it (除外, 以外, 之外, 例外), with only
    the names it excepts, joined as a list, between: No allergies except huang qi, NKDA except
    huang qi, no allergy except to huang qi, 除黄芪外无过敏, 无，除了黄芪, 无过敏史，黄芪除外 and
    无过敏（黄芪除外）stop, as No known allergies except huang qi and 除黄芪外无其他过敏 do. The
    statements a reaction word makes (对黄芪过敏, 黄芪过敏史, 黄芪不耐受, allergic to huang qi) are
    R1c's.
  - **R1c, an un-negated reaction word** anywhere in the entry. The reaction words are the
    rule's list: 过敏, 过敏反应, 不良反应, 皮疹, 红疹, 疹子, 起疹, 荨麻疹, 风团, 瘙痒, 痒, 红肿, 肿胀,
    水肿, 喉头水肿, 呼吸困难, 哮喘发作, 休克, 不耐受, rash, hives, urticaria, itch, swelling,
    angioedema, anaphylaxis, reaction, allergic, intolerance (with their inflections: rashes,
    itching; allergic reaction and skin rash are one term). One is negated only when a
    negator (无, 未, 未见, 未出现, 未再出现, 没有, 没, 不, 否认, 无明显, no, without, denies, denied,
    never) stands immediately before it in its clause, and only that occurrence is. Only the
    names the negator negates may stand between, several of them joined as a list (、 / 和 及
    与 或 跟 以及 and or): 否认黄芪过敏, 无黄芪过敏史, 否认黄芪、人参过敏 and no huang qi allergy are
    negated. Nothing else may: not 对, not a word that names a class, not any other word. So
    否认对黄芪过敏, 无药物过敏史，目前在吃黄芪, 无任何皮疹，服用黄芪, 无已知过敏，在吃黄芪, 黄芪皮试阳性，
    否认其他过敏, no known drug reaction, takes huang qi, no skin rash with huang qi and no
    allergic reaction to huang qi (no negates allergic, not reaction) stop. A clause ends at
    any mark other than 、, / and those inside a word, at a line break or a tab, and at a space
    between two Chinese characters. So 服用黄芪后无不适，次日出现皮疹 stops 黄芪 (皮疹 is not
    negated), while 服用黄芪未见皮疹 and 黄芪，未再出现皮疹 do not; 服用黄芪后未见明显皮疹 stops
    (明显 stands between), and so do 否认青霉素、黄芪过敏 (青霉素 is no name of the tables) and
    否认中药（黄芪）过敏 (the bracket ends the clause). A reaction word inside a name, at the
    extent the tables give the name, is the name's; one written before a name that does not
    stand is not (目前在吃人参，皮疹黄芪 stops 人参, 黄芪 there being inside the longer word 皮疹黄芪).
    The 过敏 of a label (过敏史：无) and of a condition's name (过敏性鼻炎) is a reaction word like
    any other.

  Otherwise the entry only **mentions** the herb (R2, `allergy_mentioned`, a block): it has
  other words around the name and no un-negated reaction word, whether they deny an allergy
  (否认黄芪过敏, 无黄芪过敏史, 黄芪（-）, 黄芪皮试阴性, 对黄芪不过敏), say the herb is tolerated
  (服用黄芪无不适, 黄芪耐受良好), used (目前在吃黄芪, 正在服用黄芪, 服用黄芪治疗咳嗽) or helped
  (服用黄芪后咳嗽好转), or anything else (对黄芪, 黄芪禁用, 黄芪，家里有, Dr. Huang Jing, Can't take
  huang qi).
  Its message is neutral and the same for all of them: 过敏史记录中写到黄芪（上下文未写明是否过敏）。
  请核实是否对黄芪过敏后再签署. No message of any kind says or implies that the record is not an
  allergy, denies one or calls the herb tolerated, and these cases give no other code. A
  pinyin or Latin name standing as words is read by the rule like a Chinese one; no list of
  common allergens is consulted, and a person's title counts only as one of R1a's context
  words.

  **The rule's costs** are known, and are the fail-safe choice. A mention of a herb in an
  entry that holds any un-negated reaction word stops it, whatever the reaction was to and
  however it ended: 目前在吃人参，青霉素过敏; 过敏史：无，目前在吃人参 (the label's 过敏 is a
  reaction word); 过敏性鼻炎，服用黄芪后好转; 服用黄芪后皮疹好转; 否认黄芪过敏，对人参过敏 stops 黄芪
  as well as 人参; Penicillin (rash). Recorded by Sheng Jiang stops 生姜. A negator negates
  only with nothing but names between, so a denial with a class word or 对 in it stops the
  names of its entry (无药物过敏史，目前在吃人参; 否认对黄芪过敏; No known allergies, takes huang
  qi). A denial written after the reaction word does not negate it (黄芪过敏史：否, 黄芪过敏-否,
  已排除黄芪过敏: stops). A list item of words that are none of R1a's context words is read as
  another allergen's name (黄芪，挺贵, 黄芪 positive and Contact Huang Qi for details stop). And a
  name with any other word beside it and no reaction word of the rule's list blocks, even
  where the record means an allergy (对黄芪, 黄芪耐受差, 黄芪接触性皮炎, Can't take huang qi). A
  stop is lifted by correcting the record; a block is signed with the practitioner's reason.

An entry in which no name the tables know is found gives no issue: a record of no allergy
(无, 否认过敏史, 过敏史：无, 无药物过敏史, 暂无, NKDA, none, NKA, nil, N/A, No known drug
allergies, 过敏史：阴性 or （-）, 过敏体质 …), an allergy to what is no herb (青霉素, 头孢,
阿司匹林, 磺胺, 海鲜, 虾蟹, 牛奶, 花生, 芒果, 小麦, 尘螨, 乳胶, 碘, 酒精 …), what else an allergy
field holds (否认手术外伤史, 预防接种史随社会, 青霉素过敏（面部皮疹）). Where a table does link
such a word to a herb, it asks about that herb only: 鸡蛋 is filed with 鸡内金, 花粉 is
天花粉's short name (below). A drug whose name
starts with a word of negation (没药, 无食子) is still that drug. A blank field ("", spaces,
[""], ["。。。"]) is read as not asked, like `null`, with the 过敏史未询问 warning, and so is
one that holds only its label or a form's labels (过敏史：, Allergies:, Drug allergies:,
Known allergies:, Allergy hx:, Allergy status:, 过敏史（药物/食物）：, 过敏史及不良反应史：, 药物：____
食物：____, 过敏史：□无 □有, 过敏史：无/有, 过敏史：XX), or says the allergies were not asked or
are not known (未询问, 没有询问, 没询问, 没有问, 没问, 漏问, 未询及, 未问, 待询问, 未能询问, 未查, 未提及,
患者不能配合, 无法回答, 不详, 不清楚, 未知, 暂不详, 未详, 不明确, 记不清楚, 不记得, 无法获取, 拒答, 详见病历,
unknown, unsure, not asked, not recorded, not obtained, deferred, see chart), unless it also
holds a name the tables know or a none word (无, 否认, 没有, 阴性, none, no, nil, NKDA, N/A,
denies, (-)) outside the not-asked or not-known phrase (R6). Names are looked for before the
labels are taken out, since a label's word may be a character of a name (不详，白及过敏 holds 白及,
whose 及 is also a label's). The "whether" of a question is a label, not a none word (患者不知道有没有过敏
and 没问有没有过敏 are not asked), and so is the 无 of 无法回答. A dash left between a label and a
not-asked or not-known phrase is no negative (过敏史 - 不详, 过敏史-未询问, Allergies - unknown and
Allergies: - unknown are not asked). R6 reads the whole field, not each entry: ["青霉素过敏",
"其他未询问"] is not asked as ["青霉素过敏，其他未询问"] is, ["未询问", "皮疹"] and ["不详", "青霉素"] are not asked
either, and ["无", "其他未询问"] is asked. A word of allergy (过敏, 过敏史, allergy) never answers by
itself. So 过敏史不详 人参过敏, 未问，黄芪, 无药物过敏史，未查过敏原, 过敏史：无，详见病历, NKDA (see chart), NKDA; allergy testing
deferred and 否认药物过敏，食物过敏未提及 are asked; 患者昏迷，未能询问, 没有询问, 患者不知道有无过敏, and 不详，可能对青霉素过敏 and
青霉素过敏，其他未询问 (青霉素 is no name of the tables) are not asked. What a not-asked field holds is still
read (过敏史不详 人参过敏 stops 人参). 没问题 is no not-asked phrase. `[]` is asked, none, and so is a
labelled negative, with or without a colon (过敏史：（-）, 过敏史：-, 过敏史（-）, （-）, (-)); a bare "-" may
only start a form's line and reads as not asked. Allergies, medications and conditions are
text: a number or an object there is refused, with the same message by the library and the
studio (`allergies must be a list of strings or null`).

Each name found is matched to every prescribed herb it fits, not only the first: 茯苓过敏
against 茯苓 and 茯神 stops 茯苓 and asks about 茯神, 地黄过敏 stops 生地黄 and 熟地黄 alike. The
herb is compared as written (folded like the history: 黑順片 and ⻩芪 are read), under its
pack alias and under its knowledge-base name (a prescription that writes 黑顺片 is read as
附子), through what it is a processed form of, and, for a prescribed name no table knows,
through the names inside it (山药 of 山药颗粒, as a near name).

An entry stops a herb (`allergy`) only when the name stands, the entry states an allergy to
it (R1), and a drug-level table says the record names the herb:
- the same name (人参过敏史 names 人参);
- the seed knowledge base gives both names to one herb. Its names are a herb's own and its
  processed forms' (黄耆 and 北芪 are 黄芪, 芍药 is 白芍, 国老 and 炙甘草 are 甘草), and a
  processing word before one of them is the same herb processed (炙黄耆 is 黄芪);
- one of the pack's cited aliases of the herb (夜交藤 is 首乌藤), with or without a
  processing word before it (炒夜交藤, 制黑顺片, 炙松贝), or a reviewed alias of the materia
  table, which it checked name by name (川当归 is 当归);
- the herb's pinyin or Latin name (huang qin, Radix Astragali), standing as words;
- or the two are processed forms of one drug. A processing word before a name the tables
  give the drug is that drug processed, so 生地黄 and 熟地黄 are both 地黄, and an allergy to
  either stops the other; 熟地, the knowledge base's short name of 熟地黄, reaches 生地黄
  through it; 甘草片 and 山药粉 are the herb sliced and powdered. A word of state (鲜, 干) is
  dropped only where the materia table files both names under one entry (鲜地黄 is 地黄, but
  干姜 is not 姜). A name the table files under another drug is not a processed form of the
  one it seems to hold: 炮姜 is under 干姜, not under 姜 (生姜).

One name inside another never stops by itself, and neither does a name only the materia
table gives: 莱菔 (the radish) and 莱菔子 (its seed), 车前 and 车前子, 菖蒲 and 石菖蒲, 丹皮 and
牡丹皮, 生地 and 生地黄, 枣 and 大枣 are each filed under one entry by that table, which groups
a source's names and cannot tell a synonym (丹皮) from a source-mate (鸡蛋 under 鸡内金). They
ask (`allergy_source`). 姜 stops 生姜 only as a processed form: 生 is a processing word and the
table files 姜 with 生姜; 枣 asks about 大枣, since 大 is no processing word.

The message names the herb name found, not the text around it: 患者对地黄过敏 against 熟地黄
gives （地黄与熟地黄同为地黄或其炮制品）. An entry of up to 40 characters is quoted whole, as
written; a longer one around the name, as written. Every message quotes a bounded excerpt:
12 characters either side of the name, up to 6 of the longer word a name is inside, and,
for each herb and kind, the first four places and how many more.

An entry that may be the herb, where no drug-level table says it is or the entry only
mentions it, is a block. It can be signed only with the practitioner's recorded reason,
which answers the check its message asks for (请核实). There are five kinds:
- `allergy_mentioned`: R2, above. A drug-level name the entry mentions without stating an
  allergy, with the neutral message, and a formula it mentions (目前在吃四君子汤, 否认四君子汤
  过敏), for each herb of the formula, with a message that names the formula and the herb.
- `allergy_source` (R3, whatever the words around the name: 否认海带过敏 asks about 海藻 as
  海带过敏 does): the materia table lists the name, or what it is a processed form of,
  under the herb's entry (丹皮 and 牡丹皮, 熟川军 and 大黄). The table groups names by
  source, not by drug: its 鸡内金 also lists 鸡蛋, and its 海藻 lists 海带 (Saccharina) and
  昆布, though 海藻 is Sargassum. So the message says only where the table files the two
  (药材名表将海带与海藻同列于“海藻”条下（未必同一物种）) and never that they are one herb or one
  species. Or the two drugs share a species, by the materia table's species, the knowledge
  base's, or the species the pack records for a herb the table lacks: 芍药 and 赤芍 (Paeonia
  lactiflora), 何首乌 and 首乌藤 (Polygonum multiflorum, root and stem). Or the entry names a
  species, a genus or an English common name of the herb's source (allergic to Panax
  ginseng, Astragalus, ginger): a plant is not a drug. Then the message names the species.
- `allergy_formula` (R4): the entry states an allergy (R1) to a formula the pack or the
  knowledge base holds, or to the same name in another product form (四君子汤过敏,
  服用四君子汤后出现皮疹, 补中益气丸过敏, 龙胆泻肝丸过敏, a bare 四君子汤). The allergy may be to
  any herb of it, so each prescribed herb that is one of its herbs, or that a tier above
  ties to one (生地黄 to the 熟地黄 of 六味地黄丸), asks; the message gives the formula's
  composition and, for another product form, says the composition may differ. A formula
  never stops by itself, and one the entry only mentions gives `allergy_mentioned` naming
  the formula. A draft of the same formula is never signed in silence.
- `allergy_near_name`: one name inside the other, where the tables give the two names to
  different drugs (白附子 and 香附子 are not 附子, 核桃仁 is not 桃仁, 土茯苓 is not 茯苓) or say
  nothing (茱萸 may mean either drug); a name found inside a longer word no table knows,
  whatever else it is and whatever the words around it, with a message that says only what
  the record holds: 记录中的“郁金香”含有“郁金”的字样，但“郁金香”不是名表所载的药名，未必是指郁金。
  A patent medicine the tables do not hold is not read as its herbs: 知柏地黄丸, 桂枝茯苓丸 and
  复方甘草片 ask about the herbs named in them, they do not stop them.
- `allergy_homonym`: an entry of 花粉. In an allergy history it means pollen, the most
  common entry of all. In prescriptions 花粉 is 天花粉's short name, and the materia table
  files it there. So it is a block for 天花粉 only, asking which was meant, whether the entry
  states an allergy (R1: 花粉, 花粉过敏, 对花粉过敏; the message asks whether the recorded
  allergy is to 天花粉) or only mentions it (R2: 对花粉, 目前在吃花粉, 花粉皮试阳性, 花粉：无, Can't
  take 花粉; the message says the record writes 花粉 without saying whether it is an allergy).
  It does not reach 瓜蒌, though 瓜蒌 comes from the same Trichosanthes. Only an explicit
  denial of the rule's asks nothing: 否认花粉过敏, 无花粉过敏史, 对花粉不过敏, 花粉（-）, 花粉皮试
  阴性, no 花粉 allergy, no allergy to 花粉. 否认药物、食物及花粉过敏史 is none (药物、食物 stand
  between 否认 and 过敏, which is not negated, R1c), and asks.

For each herb a stop stands alone; otherwise there is one block of each kind, listing the
first places that gave it and how many more. Each distinct reading is matched to each herb
once (a denied 花粉 and one that is not are two readings, and a place that gives a herb
nothing never hides another that gives it an issue: 否认花粉过敏；花粉皮试阳性 asks about 天花粉 in
either order), and every walk of the rule (back from a reaction word to a negator, along a list to an
allergy noun, over the items of an entry) is remembered for the whole record, so the time is
linear in the record's length: 20,000 and 200,000 characters of any content (runs of names
with no separators, chains of overlapping names such as 钩钩钩… or 红花椒红花椒…, names across
whitespace, one long list, a negator before a long list of names, a long run of negated
reaction words, a clause of names followed by a long run of marks, long lists of 花粉, of
English names after an allergy noun or of reaction words glued to names) take about ten times as
long at 200,000 characters as at 20,000, through the intake and the check as through the
reading alone: the intake's label, negative and not-asked patterns read a run of whitespace
once, and the scan R6 runs on a not-asked field is the reading's own.

The acceptance corpus, `BioScience-Harness/tests/data/clinic_allergy_corpus.json`, holds
every repro of the reviews of this reading, the common forms of the acceptance criteria and
the rule's own examples, each with its outcome per prescribed herb (stop, block or none),
every code it gives and the clause of the rule that gives it (R1a, R1b, R1c, R2, R3, R4,
R0), the records that must name no herb, the allergy fields that must read as not asked, and
conditions entries with what they give. Each outcome and code is the reading's, reviewed by
hand wherever a round changed it: the case keeps the earlier outcome (`before_r8` for the
rule, `before_r8_review` for its review, `before_r9` for the verification of that review)
and the reason (`review`, `review_r9`). The clause is the one
the rule oracle in `tests/test_clinic.py` gives. The oracle is written from the rule's text,
apart from the implementation's tables: from the reading it takes only R0 (the names found,
where they stand) and the name tables' tiers, and it derives each code from the clauses, not
from the reading's messages; its words are the rule's (the reaction words, the negators, the
explicit denials, R6's none and not-asked words, R1a's context words, list marks and
labels, R1b's exception words), and a test checks that it shares no pattern with the
reading. The reading must give the corpus's outcome on every case, and the oracle must read
every case alike; a case it read otherwise would hold the oracle's reading (`oracle`) and why
the corpus differs (`reviewed`). Since R1a's context words are listed in the rule, none
does: Dr. Huang Jing, 黄芪，家里有 and 黄芪，平时煲汤, which it once read as lists, it now reads
as the reading does. `tests/test_clinic.py` also checks
that every name the tables give every pack herb, in each common form and in traditional
characters, is never silent, and stops wherever the name stops the herb; that the rule's
templates give every pack herb the rule's outcome; that a negator negates only the reaction
word right after it; that no message claims the record is not an allergy; that every stop
gives only a reason a drug-level table gives; and times 20,000 against 200,000 characters.

**The pack's names.** `herb_aliases` in the pack lists names of pack herbs that neither
table holds, each with the source that gives it: 夜交藤 (首乌藤, 《中药学》), and the
Pharmacopoeia 2020's trade names (习称): 川黄柏 (黄柏), 毛山药 and 光山药 (山药), 味连, 雅连 and
云连 (黄连), 盐附子, 黑顺片 and 白附片 (附子), 松贝, 青贝 and 炉贝 (川贝母), 大贝, 珠贝 and 浙贝片
(浙贝母), 连丹皮 and 刮丹皮 (牡丹皮). `herb_sources` records the species and the Latin name of
首乌藤, the one pack herb the materia table lacks (药典 2020: the stem of the plant whose root
is 何首乌, Polygoni Multiflori Caulis). `herb_pinyin` gives every pack herb's name in pinyin,
a transliteration and not an alias (polyphones as the herbs are read: 川芎 chuanxiong, 黄柏
huangbo or huangbai). `herb_common_names` gives English common names, each with the species
it names and the WHO monograph that gives it (ginger, Rhizoma Zingiberis; licorice, Radix
Glycyrrhizae; ginseng, Radix Ginseng; cinnamon and cassia, Cortex Cinnamomi; astragalus,
Radix Astragali; ephedra; turmeric; peony; coptis; bupleurum; platycodon; dong quai, Radix
Angelicae Sinensis); they are read as the species, so they ask and never stop. A name is
listed only with its source. The pack refuses an alias with none, one given to two herbs,
one of a herb it does not hold, pinyin of a herb it does not hold, and a common name
without its species or source.

What the reading still misses or over-reads, forms outside the acceptance criteria:
- A name, abbreviation or miswriting no table holds is not read: 淮山 and 怀山 (山药), 法夏,
  姜夏 and 清夏 (法半夏), 川柏 (黄柏), 双钩 and 勾藤 (钩藤), 交藤 (首乌藤), 贝母 (川贝母 and 浙贝母
  alike), 苡米 and 米仁 (薏苡仁), 公英 (蒲公英), 桂圆 (龙眼肉), 野山参 and 园参 (人参), 贡菊 and
  亳菊 (菊花), 老翘 (连翘), 北杏 (苦杏仁), 焦三仙, 肾气丸, a formula's stem without its ending
  (四君子), 柴胡舒肝丸 (舒 for 疏), 生龙牡, the archaic 黄蓍, miswritings (川穹, 白敛, 姜蚕, 沙仁,
  牛夕, 瓜娄), the Japanese 欝金 and 釣藤, category words (参类, 中药过敏), pinyin of short
  names (shu di, dan pi, bei qi, gou qi), English common names the pack does not cite
  (aconite, goji, honeysuckle, hawthorn, jujube, mint, tang kuei), older Latin names that are
  not a table's name in another word order (Flos Lonicerae, Herba Menthae, Radix Platycodi),
  and a tone digit inside one word (huang2qi2). Each would need a cited entry in a table;
  the scan of every part of every name, which read some of these, also read ordinary words.
- A one-character name counts only standing alone or between the words of an allergy
  record: 姜蒜过敏, 老姜过敏, 对姜片过敏, 姜注射液过敏 and 服用含姜的中药后出现皮疹 do not reach 生姜.
  A name after a person's label or title is read as the herb it spells (记录人：姜 and Dr. Huang
  Jing are mentions of 生姜 and 黄精: blocks).
- A short name only the materia table gives (生地, 丹皮, 银花, 枣, 枸杞) asks rather than
  stops, as above.
- A patent medicine the tables do not hold is not read as the herbs it holds: 双黄连 asks
  about 黄连 only, not about 金银花, 黄芩 and 连翘, and 参麦注射液 asks nothing.
- The rule reads no story and no grammar: its costs are above (a reaction word anywhere
  stops every name of the entry; a word beside a name with no reaction word of its list
  blocks). A reaction word missing from the rule's list (腹泻, 头晕, 胸闷, 皮炎, 疙瘩) is no
  reaction word, so 服用黄芪后，自觉胸闷 blocks rather than stops.
- A species or genus is a plant, and a plant is not a drug: Panax ginseng asks about 人参,
  never stops it, and a genus (Citrus, Prunus) asks about every pack herb of it.
- Characters beyond the ones above (other look-alikes, private-use characters) are not
  folded, and only the allergy field and the allergy entries among the conditions are read:
  a reaction written elsewhere (a chief complaint of 服用含黄芪的中药后全身皮疹, a condition of
  药疹（黄芪所致） without the word 过敏) is not.

The interaction rules name classes of medicines. A recorded medicine also counts as its
classes when the pack's mapping lists it. The mapping covers 35 medicines, by generic name
(Chinese and English) and common brand names. Each one carries its WHO ATC code and is
read as every class whose ATC groups hold that code. The pack refuses a mapping that
names another class or leaves one out. A thiazide (hydrochlorothiazide, C03AA03) or
indapamide (C03BA11) is both a diuretic (利尿剂) and an antihypertensive (降压药), so 麻黄
with hydrochlorothiazide warns as 麻黄 with amlodipine does. A loop diuretic (furosemide,
C03CA01) is a diuretic only. Beta blockers are not mapped, because no class lists their
ATC group (C07). A medicine outside the mapping is matched by the class words alone. A
recorded medication that neither recognises is listed in an info issue
(`medications_unrecognised`), so "checked, no interaction" can be told from "not
recognised, not checked". An entry that says there are none (无, 否认, 无特殊用药, none)
is not a medication and is not listed.

### Follow-up

Each visit scores the followed findings from 0 to 3 (无 / 轻 / 中 / 重), with the
syndrome's main findings counted double. The change is the 中药新药临床研究指导原则
(2002) 证候疗效:

| Reduction | Category |
|---|---|
| ≥ 95 % | 临床痊愈 |
| ≥ 70 % | 显效 |
| ≥ 30 % | 有效 |
| less than 30 % | 无效 |
| a rise | 加重 |

The prescription is stopped for review when:
- an adverse event is severe;
- an adverse event is moderate and possibly related;
- a red flag appears among the new findings.

Differentiating again is advised:
- after two weeks without improvement;
- on worsening;
- when new findings appear.

### Agreement with practitioners

`clinic agreement` runs the differentiation blind on cases that practitioners labelled.
It reports:
- top-1 and top-3 agreement with Wilson 95 % intervals;
- Cohen's κ;
- abstentions;
- confusions;
- where two practitioners labelled the same case, their own κ, which is the ceiling.

It refuses cases without a label or a labeller. It refuses synthetic cases unless told
they are a test, and then says so on the report's first line.

## What a result is, and is not

- **A differentiation** is the textbook criteria applied to what was recorded. It is
  only as complete as the intake, and it does not replace 四诊合参.
- **A draft** is the textbook base formula with the Pharmacopoeia's limits and the
  pack's safety rules applied. It is not a prescription until a practitioner signs it.
- **Neither has been validated clinically.** Agreement with practitioners needs labelled
  cases (`clinic agreement`), and benefit needs a prospective study with outcomes. No
  such data are included.
- **The governed skill `draft-tcm-prescription`** is a candidate with risk `r3_clinical`
  that acts only with approval. Its one claim is the textbooks' own (`traditional_use`):
  that the findings meet a syndrome's criteria, and that 《方剂学》 gives the formula
  for it.

## The knowledge pack

`src/bioagent/data/clinic_pack.json`, version `2026-10-draft`, contains:

| Contents | Count | Source |
|---|---|---|
| Syndromes, with criteria, treatment principle and base formula | 23 | 《中医诊断学》; GB/T 16751.2-2021 |
| Formulas, with textbook doses and 加减 | 13 | 《方剂学》 |
| Herbs, with dose ranges, toxicity, processing and pregnancy notes | 93 | 《中华人民共和国药典》2020年版一部 |

It also holds:
- the 十八反 / 十九畏 rules;
- drug interactions, and a mapping of 35 named medicines to the rules' classes by WHO ATC
  code;
- condition cautions;
- red-flag rules;
- the NEWS2 limits;
- paediatric fractions;
- synonyms;
- the intake form's groups.

`load_pack` refuses a pack that is inconsistent. Every formula herb must have limits,
every scored finding must be on the form, and no base formula may hold an incompatible
pair.

Its `review_status` reads `draft — not reviewed by a licensed TCM practitioner`. Until a
practitioner reviews it, every report repeats that status.

### What the pack does not hold

- **Five syndromes have no base formula**: 风热表证, 心脾两虚证, 肺气虚证, 寒湿困脾证 and
  湿热证. The differentiation still runs and reports the 治法; the status is `no_formula`
  and the practitioner chooses the formula. A formula is added only when its composition
  and doses can be transcribed from the textbook exactly, so a half-remembered one is
  never drafted. 少阴阳虚寒厥证 carries no formula by design: it is an emergency and the
  session refers.
- **The 23 syndromes are the common patterns, not the full set.** A person whose findings
  match none of them gets `needs_information` and the questions that would decide, never
  the nearest syndrome.
- **No herb outside the 93 in the pack can be drafted or checked for dose**; a
  practitioner who adds one is told the pack has no limits for it and must check it
  themselves.

## 中文摘要

`bioagent clinic` 根据结构化的四诊记录给出辨证建议和处方草案，供执业中医师审核签署。工具本身从不开具处方：草案只有经医师签署才成为处方。

**流程：**
- 四诊采集：望、闻、问、切分别记录有与无，表格按十问组织，列出所有计分症状；
- 红旗征筛查：包括胸痛、卒中征象、意识障碍、出血、休克、自伤、妊娠急症、过敏性休克等，以及按 NEWS2 红色阈值检查的生命体征。一旦发现即转诊，记录排除者与依据后才能继续；
- 辨证：按主症、次症、舌、脉计分，舌脉按特征比较，相悖时扣分。判定标准为主症 ≥ 2、次症 ≥ 1（或主症 ≥ 3），舌或脉相符，且舌、脉不同时相悖；不符合时给出鉴别所需的追问。急危重证直接转诊；
- 处方草案：
  - 以《方剂学》基础方为起点，按年龄折算剂量，超过《药典》上限时按上限起草；
  - 提示教材加减，需要时才应用；
- 安全核查：
  - 十八反、十九畏：须说明理由；
  - 妊娠禁用：停止；慎用：须说明理由；育龄期患者妊娠状态未知：须先确认；
  - 过敏：不按一张措辞清单（过敏史：X、对X过敏、X过敏史……）读过敏史——清单漏掉的每种写法都会静默放行，而过敏史的写法远多于任何清单（患者对国老过敏、3年前北芪过敏、北芪/人参过敏、变应原：北芪、allergic to huangqi）。每条过敏记录按原样先规范化，再扫描（`bioagent.clinic.allergy`）其中名表所载的每个药名，然后按一条固定规则判定记录是否写明对每个药名过敏（见下）；写在病史（conditions）里的过敏（如“黄芪过敏”）按原样同样读取（对黄芪、人参过敏两味都指）：病史中凡有过敏字样（过敏、致敏、变态反应、allerg…）的条目都按原样整条读取，病名也不例外：过敏性鼻炎中的“过敏”与其他反应字样一样（R1c），所以病史中的“过敏性鼻炎，服用黄芪后出现皮疹”“过敏体质，黄芪”“过敏性鼻炎，服用黄芪后好转”都停止黄芪：
    - 规范化：NFKC；去掉零宽空格与连接符、字节顺序标记、软连字符、异体字选择符、韩文填充符、盲文空白、NUL 等控制字符；NFKC 不转换的形近部首（从 PDF 复制的⻩、⻰）按其所示的字读（黄、龙）；繁体字按药材名表自带的字表（`materia.to_simplified`）转为简体，所以黃耆读作黄耆、人參读作人参、當歸读作当归；该表未收、而扫描所用药名及过敏史常用字中出现的繁体、港台与日文字形另按一张字表转换（過敏、對、否認、腫脹、黑順片、人銜、四君子湯、当帰、芍薬；规则 R1c 的每个反应字样与否定词的每个字，其繁体字形都能转换），这张字表已按 OpenCC 的 s2t、s2hk、s2tw 对每个这样的字核对过（OpenCC 不是依赖，装有它时测试会复核）；拉丁字母去掉声调符号（Huáng Qí 即 huang qi）；转为小写；
    - 只扫描名表所载的名称：种子知识库的药名与别名（黄耆、北芪、国老）；药材名表每一条的全部名称，含单字名（姜、枣）；知识包的药名及其注明出处的别名（`herb_aliases`，见下）；知识包和知识库所收方剂的方名与别名（四君子汤、阳旦汤），以及同一方名的其他剂型（补中益气丸、天麻钩藤颗粒、四物膏、补中益气方；“散”“饮”只用于知识包的方剂，所以四逆散不是知识库的四逆汤；单味药名后的“片”不算剂型，麻黄片是麻黄饮片）。名表未列的药名部分或简写一概不扫描：此前逐一扫描每个药名的每个部分，结果读进了日常用词（明天麻的“明天”、淡黄芩的“淡黄”、金铃子的“金子”），已删去。拉丁名与拼音按整词匹配，可带空格、连字符、撇号、声调符号或声调数字（huang qi、Huang-Qi、Huang'qi、huang2 qi2、Huáng Qí）：种子知识库的拼音名与拉丁名，知识包给每味药的拼音（`herb_pinyin`），药材名表与知识包的拉丁名（首乌藤的 Polygoni Multiflori Caulis），按药典 2020 年版的词序和旧词序（Astragali Radix 与 Radix Astragali），名表所载的物种名和属名（Panax ginseng、Astragalus），以及知识包注明出处的英文俗名（`herb_common_names`：ginger、licorice、ginseng、cinnamon、astragalus、dong quai 等）。每个位置的每个药名都是候选，所以较短的药名不会因同一位置起始的较长药名而丢失：枣皮疹中既有独立在“皮疹”之前的“枣”，也有长词中的“枣皮”；
    - 读两遍：一遍以空白为硬分界，所以一行一条或以空格分隔的过敏清单“姜 / 黄芪”读作姜和黄芪；一遍去掉空白，所以北 芪即北芪。所找到的药名取两遍之并。只在跨空白时才找到的药名，若与第一遍中独立成词的两字以上药名重叠则舍去（姜 / 黄芪不读作姜黄，姜 / 半夏不读作姜半夏），若两遍都不独立成词、又与第一遍找到的药名重叠也舍去（枣 / 皮疹不读作枣皮）。这样的药名只有在第二遍中独立成词、或是炮制品时才读，且从不跨换行或制表符：柑橘 皮疹不读作橘皮，猕猴桃 花生不读作桃花，花生 地塞米松不读作生地，荞麦 冬瓜不读作麦冬，面粉换行再写草莓不读作粉草；
    - 按所在位置读：药名前后是分界（条目首尾、标点、空白、数字、拉丁字母），或是过敏史中常写在药名前后的字词（对、患者、自诉、因、的、吃、一吃……就、似乎、小时候、年前、服用、过敏、过敏史、变应原、及、等、均、后、皮疹、水、禁用……），或是在其另一侧同样独立成词的两字以上药名时，该药名“独立成词”：北芪人参过敏两味都指，因黄芪过敏住院、对黄芪的成分过敏、黄芪吃了过敏都指黄芪。方名只有其前面本身是分界时才独立成词（柴胡桂枝汤中的桂枝汤在长词中）。单字药名（姜、枣、归、桂、面）只有两侧本身都是分界、且只取较短的一组字词（对、服用、吃、及、和、因、年、中药、过敏、过敏史、后、就、皮疹、出现、引起、致、会、严重、特别、否认……）时才独立成词：对姜过敏、姜过敏史、、姜、、服用姜后出现皮疹、对姜严重过敏、吃姜会过敏、吃姜出现皮疹、姜致皮疹、2019年姜过敏、对中药姜过敏；这些字词与姜、归、枣、桂、芎的每种组合都查过是否构成日常用词，查到的“归类”单独成词。在其他词中一概不算药名：面部、方便面、姜某、枣红色、归档、归类、桂林、沙姜、姜片都不提示。
      - 在独立成词的较长药名或方名之中的药名归该长名（苦杏仁中的杏仁、六味地黄丸中的地黄、天麻钩藤颗粒中的天麻），改按长名比对。少数含药名字样的日常用词单独成词，不指任何药（胡萝卜、桂花、桂鱼、舌头）；
      - 不独立成词的两字以上药名“在更长的词中”：郁金香中的郁金、知柏地黄丸中的地黄、双黄连中的黄连、预防风疹疫苗中的防风、桂枝茯苓丸中的桂枝和茯苓、蛋白之后的白及、对于之后的于术（但“对”本身独立成词、于术右侧也独立成词、且其“术”在那里不构成日常用词时除外：对于术过敏、对于术及青霉素过敏、对于术等药物过敏、对于术有过敏史都是“对 + 于术”，知识库把于术归为白术，所以停止白术；对于术后、对于术中、由于术前仍是“对于 + 术后”）。用于的“用”是服用、使用等服用字样的末字时，不构成日常用词：服用于术后出现皮疹是“服用 + 于术 + 后”，停止白术。长词是该药名前加炮制词、或后加制品字（片、粉、末等）时，即为该药的炮制品（炙北芪、炒夜交藤、甘草片、山药粉）。以否认用字开头、又接着否认所指对象的药名，按否认读：没药物过敏史是“没 + 药物过敏史”，不是没药；
    - 规则：记录是否写明对某个药名过敏，按一条固定规则判定，不读药名前后的叙述（此前五轮按“服用过、可耐受、好转、用于治疗”读叙述，始终没有收敛：每一轮的规则都牵动其他写法，部分消息还把真实的反应记录称为“不是过敏记录”，可能使医师签过一次过敏）。一条记录按原文整条读：其中任一处的反应字样对其中每个药名都算，两条记录分开读。对在记录中独立成词、且有药物一级名表说即处方药（见下）的药名，下列任一条成立时，记录“写明过敏”（R1，`allergy`，停止）：
      - R1a，只有药名：记录是药名的列举，可带过敏标签与类别标签（过敏史：、Allergy:、药物：、中药：、草药：、Allergen name:）、列举分隔（、，,；;/ + 和 及 与 或、and、or、换行、制表符、汉字旁或药名旁的空格、后跟空格的英文句点、两个词之间的连字符或间隔号）、序号（1. 2. #1、(1)、带不带空格的① ②、一、二、、（一））和“等”——过敏史栏中的药名默认就是过敏。有药名的一项不能再有别的字词，药名旁的任何其他字词都是上下文（对黄芪、黄芪（-））。名表不收其他过敏原的名称，所以列举中没有名表所载药名的一项，是列举中的另一项（青霉素、头孢、对乙酰氨基酚、无花果、含碘造影剂、香水、penicillin、codeine、Tylenol with codeine、vitamin A），除非它是字词而非名称：其中有 R2、R6 的上下文字词，且作为整词。这些字词分五类：否定词与表示“无”的字词（无、未、没、不、否、否认、没有、未见、未出现、未再出现、无明显、阴性、未发现、排除、除外、正常；no、not、none、nil、NKDA、NKA、NKFA、N/A、negative、neg、denies、denied、deny、without、never、except），未询问或不详（询问、未问、没问、漏问、不详、不清楚、不知道、未知、待查、拒、拒答、拒绝；unknown、unsure、deferred、asked、see、chart、notes），在用与存放（服、用、吃、喝、泡、炖、煲、煮、熬、服用、服药、使用、用过、吃过、服过、喝过、用了、吃了、服了、喝了、用后、在用、在吃、在服、正在、目前、现在、平时、平素、长期、每日、每天、经常、常年、常备、家里、家中、煲汤、炖汤、熬汤、泡水、泡茶、煮水；take、takes、taking、took、taken、use、uses、using、used、on、currently、daily），耐受、好转与治疗（耐受、可以、可用、可吃、可服、能吃、能用、能服、良好、不适、问题、好转、缓解、改善、减轻、消退、消失、痊愈、有效、见效、见好、治疗、主治、调理、进补；tolerated 及其他以 tolera- 开头的词、ok、okay、fine、improved、better、resolved、treated 及其他以 treat- 开头的词、can't、cannot），以及称呼人的词（患者、病人、本人、家属、家人、母亲、父亲、妈妈、爸爸、医生、医师、大夫、护士、自诉、自述、代诉；patient、pt、family、mother、father、mom、dad、son、daughter、wife、husband、doctor、Dr.、nurse、report、reports、reported、recorded）。单字的中文字词只有独立成词时才算：旁边只有标点、空格、拉丁字母或数字、过敏字样、类别词或其他上下文字词（无、无过敏、不吃、未用；无花果的“无”、不锈钢的“不”不算）；英文字词须是整词。任何一项都不因首字或末字、也不因英文虚词（with、in、a、an、the、for）而算作字词。黄芪、过敏史：黄芪、青霉素，黄芪、青霉素 黄芪、黄芪，对乙酰氨基酚、草药：黄芪、人参、①黄芪②人参、一、黄芪 二、人参、黄芪+青霉素、penicillin huang qi、Huang Qi penicillin、huang qi. penicillin、penicillin/huang qi、huang qi, vitamin A、Allergy: huang qi 都停止；黄芪，家里有、黄芪，医生开的、黄芪，好转、黄芪，平时煲汤、takes huang qi daily、Dr. Huang Jing 只是写到（R2）。其他字词构成的一项按另一过敏原读，这是偏向安全的一侧；
      - R1b，关于该药名的过敏陈述：在药名所在的陈述中，有前面没有紧挨着否定词的过敏名词。过敏名词即英文名词（allergy、allergies、allergen）和不含“过敏”的标签（致敏原、变应原、药敏）；陈述在句号（序号的点除外）、分号、问号、叹号、换行或制表符处结束，逗号、冒号、破折号不结束陈述。所以 huang qi allergy、allergy to huang qi、allergy to the herb huang qi、huang qi severe allergy、Allergies: huang qi、Allergies include huang qi and penicillin、Known allergies are huang qi、Allergies reviewed: huang qi、Allergies: #1 huang qi 都停止；no huang qi allergy、denies 黄芪 allergy、No allergies, takes huang qi 是被否定的；Allergies: none. Takes huang qi. 中的名词在另一陈述中（R2）。例外即写明对所除外者过敏，所以例外词紧挨在药名之前（except、except to、except for、除、除了；排除、解除等动词中的“除”不除外任何东西）或紧挨在药名之后（除外、以外、之外、例外），中间只有它所除外的药名（可连成列举）时，该药名也算写明过敏：No allergies except huang qi、NKDA except huang qi、no allergy except to huang qi、除黄芪外无过敏、无，除了黄芪、无过敏史，黄芪除外、无过敏（黄芪除外）都停止，与 No known allergies except huang qi、除黄芪外无其他过敏一样。由反应字样构成的陈述（对黄芪过敏、黄芪过敏史、黄芪不耐受、allergic to huang qi）由 R1c 读；
      - R1c，记录中任一处未被否定的反应字样。反应字样即规则所列：过敏、过敏反应、不良反应、皮疹、红疹、疹子、起疹、荨麻疹、风团、瘙痒、痒、红肿、肿胀、水肿、喉头水肿、呼吸困难、哮喘发作、休克、不耐受、rash、hives、urticaria、itch、swelling、angioedema、anaphylaxis、reaction、allergic、intolerance（含词形变化：rashes、itching）。只有当否定词（无、未、未见、未出现、未再出现、没有、没、不、否认、无明显、no、without、denies、denied、never）在同一分句中紧挨在反应字样之前时，该反应字样才算被否定，而且只否定这一处。二者之间只能有否定词所否定的药名（几个药名可用 、 / 和 及 与 或 跟 以及 and or 连成列举）：否认黄芪过敏、无黄芪过敏史、否认黄芪、人参过敏、no huang qi allergy 都是被否定的。其他任何字词都不能在中间，“对”、表示类别的词、别的字词都不行：所以否认对黄芪过敏、无药物过敏史，目前在吃黄芪、无任何皮疹，服用黄芪、无已知过敏，在吃黄芪、黄芪皮试阳性，否认其他过敏、no known drug reaction, takes huang qi、no skin rash with huang qi、no allergic reaction to huang qi（no 只否定 allergic，不否定 reaction）都停止。分句在“、”“/”和词内符号以外的任何标点、换行、制表符以及两个汉字之间的空格处结束。所以服用黄芪后无不适，次日出现皮疹停止黄芪（“皮疹”未被否定），服用黄芪未见皮疹、黄芪，未再出现皮疹不停止；服用黄芪后未见明显皮疹停止（中间隔着“明显”），否认青霉素、黄芪过敏（青霉素不是名表所载药名）和否认中药（黄芪）过敏（括号结束分句）也停止。按名表所给范围的药名之内的反应字样属于该药名；写在不独立成词的药名之前的则不属于（目前在吃人参，皮疹黄芪停止人参，其中的黄芪在长词“皮疹黄芪”之内）。标签（过敏史：无）和病名（过敏性鼻炎）中的“过敏”同样是反应字样。
      否则，记录只是“写到”该药（R2，`allergy_mentioned`，须说明理由）：药名旁有其他字词、且没有未被否定的反应字样，无论这些字词是否认过敏（否认黄芪过敏、无黄芪过敏史、黄芪（-）、黄芪皮试阴性、对黄芪不过敏）、说可耐受（服用黄芪无不适、黄芪耐受良好）、说在用（目前在吃黄芪、正在服用黄芪、服用黄芪治疗咳嗽）、说好转（服用黄芪后咳嗽好转），还是别的（对黄芪、黄芪禁用、黄芪，家里有、Dr. Huang Jing、Can't take huang qi）。消息措辞中性，对这些情形都一样：过敏史记录中写到黄芪（上下文未写明是否过敏）。请核实是否对黄芪过敏后再签署。任何消息都不说、也不暗示记录不是过敏记录、否认了过敏或该药可耐受，这些情形也不给出其他代码。按整词独立的拼音或拉丁名与中文药名同样按规则读，不查常见过敏原表；人的称谓只作为 R1a 的上下文字词。
      规则的代价是已知的，也是出于安全的选择：记录中有任何未被否定的反应字样，其中写到的药名都停止，无论反应是对什么、结果如何（目前在吃人参，青霉素过敏；过敏史：无，目前在吃人参——标签中的“过敏”是反应字样；过敏性鼻炎，服用黄芪后好转；服用黄芪后皮疹好转；否认黄芪过敏，对人参过敏对黄芪也停止；Penicillin (rash). Recorded by Sheng Jiang 停止生姜）；否定词只在中间只有药名时才否定，所以带有类别词或“对”的否认，其中写到的药名都停止（无药物过敏史，目前在吃人参；否认对黄芪过敏；No known allergies, takes huang qi）；写在反应字样之后的否认不否定它（黄芪过敏史：否、黄芪过敏-否、已排除黄芪过敏：停止）；列举中不含 R1a 上下文字词的一项按另一过敏原读（黄芪，挺贵、黄芪 positive、Contact Huang Qi for details 停止）；药名旁有其他字词、又没有规则所列反应字样的，即使记录指的是过敏，也只须说明理由（对黄芪、黄芪耐受差、黄芪接触性皮炎、Can't take huang qi）。停止须更正记录后解除；须说明理由的，由医师记录理由后签署；
    - 扫描不到任何名表所载药名的条目不提示：记为无过敏的（无、否认过敏史、过敏史：无、无药物过敏史、暂无、NKDA、none、NKA、nil、N/A、No known drug allergies、过敏史：阴性或（-）、过敏体质……），对非药材过敏的（青霉素、头孢、阿司匹林、磺胺、海鲜、虾蟹、牛奶、花生、芒果、小麦、尘螨、乳胶、碘、酒精……），过敏史栏中的其他内容（否认手术外伤史、预防接种史随社会、青霉素过敏（面部皮疹））。名表确把这类词与某药相连时，只就该药提问：鸡蛋列在鸡内金下，花粉是天花粉的简称（见下）。以否认用字开头的药名（没药、无食子）仍是该药。空白的过敏史（""、空格、[""]、["。。。"]）与 `null` 一样视为未询问，并提示“过敏史未询问”；只有标签或表格标签的（过敏史：、Allergies:、Drug allergies:、Known allergies:、过敏史（药物/食物）：、过敏史及不良反应史：、药物：____ 食物：____、过敏史：□无 □有、过敏史：无/有、过敏史：XX，标签也包括 Allergy hx:、Allergy status:），或写明未询问、不详的（未询问、没有询问、没询问、没有问、没问、漏问、未询及、未问、待询问、未能询问、未查、未提及、患者不能配合、无法回答、不详、不清楚、未知、暂不详、未详、不明确、记不清楚、不记得、无法获取、拒答、详见病历、unknown、unsure、not asked、not recorded、not obtained、deferred、see chart）也是如此，除非在未询问或不详的字样之外还有名表所载的药名或表示“无”的字词（无、否认、没有、阴性、none、no、nil、NKDA、N/A、denies、(-)）（R6）。药名在去掉标签之前查找，因为标签用字可能是药名中的字（不详，白及过敏中有白及，“及”也是标签用字）；问句中的“有没有”与“有无”一样是标签，不是表示“无”的字词（患者不知道有没有过敏、没问有没有过敏算未询问），“无法回答”的“无”也不是；标签与未询问或不详字样之间剩下的连字符不是阴性（过敏史 - 不详、过敏史-未询问、Allergies - unknown、Allergies: - unknown 算未询问）。R6 按整个过敏史栏读，而不是逐条读：["青霉素过敏", "其他未询问"] 与 ["青霉素过敏，其他未询问"] 一样算未询问，["未询问", "皮疹"]、["不详", "青霉素"] 也算未询问，["无", "其他未询问"] 算已询问。过敏字样（过敏、过敏史、allergy）本身从不算答复。所以过敏史不详 人参过敏、未问，黄芪、无药物过敏史，未查过敏原、过敏史：无，详见病历、NKDA (see chart)、NKDA; allergy testing deferred、否认药物过敏，食物过敏未提及都算已询问；患者昏迷，未能询问、没有询问、患者不知道有无过敏，以及不详，可能对青霉素过敏、青霉素过敏，其他未询问（青霉素不是名表所载药名）都算未询问。未询问的过敏史栏的内容仍会读取（过敏史不详 人参过敏仍停止人参）；“没问题”不是未询问的字样。`[]` 才是询问后无，写阴性的也是询问后无，标签后有无冒号均可（过敏史：（-）、过敏史：-、过敏史（-）、（-）、(-)）；单独的“-”可能只是表格行首，按未询问读。过敏史、用药与病史须为文字：填入数字或对象，库与工作室都以同一消息拒绝（`allergies must be a list of strings or null`）；
    - 每个找到的药名与它相符的每一味处方药比对，而不只是第一味：茯苓过敏对茯苓停止、对茯神提问，地黄过敏对生地黄、熟地黄都停止。处方药按原名（与过敏史同样规范化：黑順片、⻩芪都能读）、按知识包别名、按知识库名称（处方写黑顺片即附子）、按其所炮制的药比对；名表都不认识的处方药名还按其中所含药名比对（山药颗粒中的山药，按名称相近处理）；
    - 停止：药名独立成词、记录写明对它过敏（R1），且有药物一级的名表说记录所写即该药：与药名相同（人参过敏史含人参）；或种子知识库认定为同一药材（黄耆、北芪即黄芪，芍药即白芍，国老、炙甘草即甘草），其名称前加炮制词的仍是该药（炙黄耆即黄芪）；或为知识包注明出处的别名，前面有没有炮制词都算（夜交藤即首乌藤，炒夜交藤、制黑顺片、炙松贝亦然），或药材名表逐一复核过的别名（川当归即当归）；或为该药的拼音名、拉丁名（huang qin、Radix Astragali），按整词独立；或二者是同一药材的不同炮制品：名表所给药名前加炮制词即该药的炮制品，所以生地黄、熟地黄都是地黄，对其一过敏即停止另一；熟地是知识库给熟地黄的简称，经它归并到生地黄；甘草片、山药粉是该药的饮片和粉末。“鲜”“干”等状态词只在药材名表把两名列于同一条下时去掉（鲜地黄即地黄，干姜不是姜）；药材名表列在另一药名下的名称不算其所含药名的炮制品（炮姜列在干姜下，不是姜即生姜的炮制品）。一名包含另一名本身从不停止，只有药材名表给出的名称也不停止：莱菔（萝卜）与莱菔子（其种子）、车前与车前子、菖蒲与石菖蒲、丹皮与牡丹皮、生地与生地黄、枣与大枣，都被该表列在同一条下，而该表按来源归并名称，分不清同义名（丹皮）与同源之物（鸡内金下的鸡蛋），所以都只提问（`allergy_source`）。姜之所以停止生姜，只因为二者是炮制关系：“生”是炮制词，该表又把姜列在生姜下；枣对大枣只提问，因为“大”不是炮制词。消息写出找到的药名，而不是其前后的文字：患者对地黄过敏对熟地黄给出（地黄与熟地黄同为地黄或其炮制品）；40 字以内的记录按原文整条引用，更长的只按原文引用药名前后的一段。每条消息的引文都有上限：药名前后各 12 字，所在长词最多前后各 6 字，每味药每类只列前四处及其余处数；
    - 须说明理由：记录可能指该药、但没有药物一级的名表说它就是该药，或记录只是写到该药时，医师须记录理由（即消息所说的“请核实”）才能签署，分五类：
      - `allergy_mentioned`：即上述 R2。记录写到药物一级的药名、但未写明过敏时，给出中性的消息；记录写到方剂而未写明过敏时（目前在吃四君子汤、否认四君子汤过敏），对方中每味相关处方药给出，消息写出方名与药名；
      - `allergy_source`（R3，无论药名旁写什么：否认海带过敏与海带过敏一样对海藻提问）：药材名表把该名、或其所炮制的药，列在该药条下（丹皮与牡丹皮，熟川军与大黄）。该表按来源而非按药归并名称：鸡内金下列有鸡蛋，海藻（马尾藻属）下列有海带（海带属）、昆布，所以消息只说该表把二者列于何条之下（药材名表将海带与海藻同列于“海藻”条下（未必同一物种）），不称为同一药材或同一物种；或二者来自同一物种，物种取自药材名表、知识库，或名表未收的药取知识包记录的物种：芍药与赤芍（Paeonia lactiflora），何首乌与首乌藤（Polygonum multiflorum，根与藤茎）；或记录写的是该药来源的物种名、属名或英文俗名（allergic to Panax ginseng、Astragalus、ginger）——植物不等于药材。此时消息写出物种；
      - `allergy_formula`（R4）：记录写明对知识包或知识库所收的方剂过敏（R1），或对同一方名的其他剂型过敏（四君子汤过敏、服用四君子汤后出现皮疹、补中益气丸过敏、龙胆泻肝丸过敏、单写四君子汤）。过敏可能由方中任一味药引起，所以凡是该方组成药、或经上述各层与组成药相关的处方药（生地黄与六味地黄丸中的熟地黄），都须说明理由；消息写出该方组成，其他剂型还说明组成未必完全相同。方剂本身从不停止；记录只是写到方剂时，按 `allergy_mentioned` 写出方名。起草同一首方剂不会静默通过；
      - `allergy_near_name`：一名包含另一名，而名表认定为不同药材（白附子、香附子不是附子，核桃仁不是桃仁，土茯苓不是茯苓）或未作说明（“茱萸”可能指其中任一种）；在名表不认识的长词中找到的药名，无论属于哪一层、旁边写什么，也归此类，消息只说记录中有什么：记录中的“郁金香”含有“郁金”的字样，但“郁金香”不是名表所载的药名，未必是指郁金。名表未收的中成药不拆解为其药材：知柏地黄丸、桂枝茯苓丸、复方甘草片对其名中的药须说明理由，不停止；
      - `allergy_homonym`：“花粉”。过敏史中的花粉指植物花粉，是最常见的过敏记录；处方中“花粉”是天花粉的简称，药材名表也列在天花粉下。所以只对天花粉要求说明理由，问清所指，无论记录写明过敏（R1：花粉、花粉过敏、对花粉过敏，消息问所记过敏是否为天花粉）还是只是写到（R2：对花粉、目前在吃花粉、花粉皮试阳性、花粉：无、Can't take 花粉，消息说记录写到花粉而未写明是否过敏）；不延及同为栝楼属的瓜蒌。只有规则所列的明确否认不提示：否认花粉过敏、无花粉过敏史、对花粉不过敏、花粉（-）、花粉皮试阴性、no 花粉 allergy、no allergy to 花粉；否认药物、食物及花粉过敏史不是（“药物、食物”隔在“否认”与“过敏”之间，“过敏”未被否定，R1c），仍提示；
    - 每味药有“停止”时只列“停止”；否则每类各列一条，写出给出该类的前几处及其余处数。每种不同的读法与每味药只比对一次（被否认的“花粉”与未被否认的是两种读法；某一处对某药不提示，从不遮住另一处对它的提示：否认花粉过敏；花粉皮试阳性无论先后都对天花粉提问），规则的每一次查找（从反应字样往回找否定词、沿列举找到 allergy、逐项读记录）全文只计算一次，所以读取时间与记录长度成正比：2 万字与 20 万字的任意内容（不加分隔的连写药名、钩钩钩……或红花椒红花椒……这类互相重叠的药名链、跨空白的药名、很长的列举、否定词之后很长一串药名、很长一串被否定的反应字样、一串药名之后跟很长一串标点、很长一串花粉、过敏名词之后很长一串英文药名、粘在药名上的一串反应字样）20 万字所用时间约为 2 万字的十倍；经过四诊采集再核查时也是如此：采集中识别标签、阴性和未询问的模式对一串空白只读一遍，R6 对未询问栏所做的扫描就是读取本身的扫描；
    - 验收语料 `BioScience-Harness/tests/data/clinic_allergy_corpus.json` 收录各轮审查的全部复现记录、验收标准中的常见写法和规则本身的示例，每条写明对每味处方药的结果（停止、须说明理由或不提示）、给出的全部代码和给出它们的规则条款（R1a、R1b、R1c、R2、R3、R4、R0），以及不得指向任何药材的记录、必须视为未询问的过敏史栏和病史条目及其结果。每个结果和代码都是读取给出的，凡某一轮改变了结果的都经人工复核：该条保留原结果（规则改变的为 `before_r8`，规则复核改变的为 `before_r8_review`，复核之后的核验改变的为 `before_r9`）及理由（`review`、`review_r9`）。条款取自 `tests/test_clinic.py` 中的规则判定器（oracle）。判定器按规则原文写成，不用实现的词表：它只从读取取 R0（找到的药名及其位置）和名表的层级，代码由条款推出而不取读取的消息；所用字词都是规则自己的（反应字样、否定词、明确否认、R6 的“无”字与未询问字样、R1a 的上下文字词、列举分隔与标签、R1b 的例外词），并有测试检查它与读取不共用任何模式。读取须在每一条上给出语料的结果；判定器也须在每一条上读得一致；读得不一致的条目须写明判定器的读法（`oracle`）和语料为何不同（`reviewed`）。R1a 的上下文字词已列入规则，所以现在没有这样的条目：判定器曾当作列举的 Dr. Huang Jing、黄芪，家里有、黄芪，平时煲汤，现在与读取读法相同。`tests/test_clinic.py` 还检查名表给每味知识包药材的每个名称，按每种常见写法和繁体字书写，都不会静默放行，凡该名称能停止该药之处都停止；规则的模板对每味知识包药材给出规则的结果；否定词只否定紧挨其后的反应字样（中间只能有它所否定的药名）；任何消息都不称记录不是过敏；每个“停止”只给出药物一级名表所给的理由；并对比 2 万字与 20 万字的用时；
    - 知识包的名称：`herb_aliases` 收录两个名表都没有的知识包药材别名，每个注明出处：夜交藤（首乌藤，《中药学》），以及《药典》2020 年版的习称：川黄柏（黄柏），毛山药、光山药（山药），味连、雅连、云连（黄连），盐附子、黑顺片、白附片（附子），松贝、青贝、炉贝（川贝母），大贝、珠贝、浙贝片（浙贝母），连丹皮、刮丹皮（牡丹皮）。`herb_sources` 记录首乌藤的物种和拉丁名——它是药材名表唯一未收的知识包药材（药典 2020：何首乌原植物的干燥藤茎，Polygoni Multiflori Caulis）。`herb_pinyin` 给出每味知识包药材的拼音，是音译而非别名（多音字按药名读音：川芎 chuanxiong，黄柏 huangbo 或 huangbai）。`herb_common_names` 给出英文俗名，每个注明所指物种和出处的 WHO 专论（ginger，Rhizoma Zingiberis；licorice，Radix Glycyrrhizae；ginseng，Radix Ginseng；cinnamon、cassia，Cortex Cinnamomi；astragalus，Radix Astragali；ephedra；turmeric；peony；coptis；bupleurum；platycodon；dong quai，Radix Angelicae Sinensis），按物种读，只提问、不停止。只收有出处的名称；没有出处、同一别名给了两味药、别名或拼音所指的药不在知识包中、俗名没有物种或出处，知识包都拒绝加载；
    - 仍会漏读或多读（验收标准之外的写法）：名表中没有的名称、简写或误写不识别——淮山、怀山（山药），法夏、姜夏、清夏（法半夏），川柏（黄柏），双钩、勾藤（钩藤），交藤（首乌藤），贝母（川贝母、浙贝母都不识别），苡米、米仁（薏苡仁），公英（蒲公英），桂圆（龙眼肉），野山参、园参（人参），贡菊、亳菊（菊花），老翘（连翘），北杏（苦杏仁），焦三仙，肾气丸，去掉剂型字的方名（四君子），柴胡舒肝丸（“舒”作“疏”），生龙牡，古写黄蓍，误写（川穹、白敛、姜蚕、沙仁、牛夕、瓜娄），日文欝金、釣藤，泛称（参类、中药过敏），简称的拼音（shu di、dan pi、bei qi、gou qi），知识包未注明出处的英文俗名（aconite、goji、honeysuckle、hawthorn、jujube、mint、tang kuei），不是名表药名换词序而得的旧拉丁名（Flos Lonicerae、Herba Menthae、Radix Platycodi），以及一个词内的声调数字（huang2qi2）。这些都需要名表中有出处的条目；逐一扫描药名各部分的做法虽能读出其中一些，也读进了日常用词。单字药名只在单独成条或位于过敏字词之间时才算：姜蒜过敏、老姜过敏、对姜片过敏、姜注射液过敏、服用含姜的中药后出现皮疹不涉及生姜；人名标签或称谓之后的名称按其所拼写的药读（记录人：姜、Dr. Huang Jing 分别是写到生姜、黄精：须说明理由）。只有药材名表给出的简称（生地、丹皮、银花、枣、枸杞）只提问、不停止（见上）。名表未收的中成药不拆解为其所含药材：双黄连只就黄连提问，不涉及金银花、黄芩、连翘；参麦注射液不提示。规则不读叙述、也不读语法，其代价见上（任一处反应字样对记录中每个药名都停止；药名旁有字词而无规则所列反应字样的只须说明理由）。规则未列的反应字样（腹泻、头晕、胸闷、皮炎、疙瘩）不算反应字样，所以服用黄芪后，自觉胸闷只须说明理由、不停止。物种和属名指植物而非药材：Panax ginseng 对人参只提问、不停止，属名（Citrus、Prunus）对该属的每味知识包药材提问。上述以外的字符（其他形近字、私用区字符）不转换；只读过敏史栏和病史中的过敏条目：写在别处的反应（主诉“服用含黄芪的中药后全身皮疹”，病史中不带“过敏”二字的“药疹（黄芪所致）”）不读；
  - 中西药相互作用：警示。西药按类别名（如利尿剂）识别，也按具体药名识别：知识包按 WHO ATC 分类把 35 种药（中英文通用名和常见商品名，如呋塞米、Lasix）对应到规则中的类别，每种附 ATC 编码，并归入其编码所属的全部类别。噻嗪类（氢氯噻嗪，C03AA03）和吲达帕胺（C03BA11）既是利尿剂也是降压药，所以麻黄与氢氯噻嗪同用也会警示；袢利尿剂（呋塞米）只属利尿剂；β 受体阻滞剂（C07）不属于任何已列类别，未列入。类别名和药名都未识别的用药以“提示”列出，说明未核查其相互作用（未提示不等于无相互作用）；记为“无”“否认”“无特殊用药”“none”等表示未用药的不算用药，不列出；
  - 病证慎用：警示；
  - 毒性药材：警示，并附炮制与煎法；
  - 超量：有毒药材须说明理由，其他药材警示；
  - 来源不一时取较严者，例如附子孕妇用药：药典为“慎用”，知识库为“禁用”，按“禁用”处理；
- 签署：医师可以接受、修改或拒绝草案。修改内容会重新核查；须说明理由的问题逐条记录理由，“停止”级问题不能签署；
- 随访：按《中药新药临床研究指导原则》的证候积分计算疗效等级。出现严重或可能相关的中度不良反应，或出现红旗征时，停药并复诊；
- 一致性评估：只接受执业医师标注的病例，报告 top-1/top-3 一致率、κ 值和两位医师之间的 κ。合成病例必须显式声明，报告首行会注明“合成”。

**局限：**
- 知识包为草案转录，尚未经执业中医师审核；
- 辨证一致性和临床疗效均未经验证；
- 结果的好坏取决于四诊记录是否完整准确；
- 知识包收录 23 个常见证候、13 首基础方、93 味药。风热表证、心脾两虚证、肺气虚证、寒湿困脾证、湿热证五证只给治法不给方，由医师选方（方剂只在能逐字照录教材组成与剂量时才收入，宁可不给，不作臆测）；少阴阳虚寒厥证按急危重证直接转诊；
- 四诊信息与所有证候均不符时，只给出可区分的追问，不会退而取「最接近」的证候。
