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
| Allergy, to the herb, as a drug-level table says: by name, under any name the seed knowledge base gives it (黄耆 or 北芪 is 黄芪, 国老 is 甘草), a cited pack alias (夜交藤 is 首乌藤) or a reviewed alias of the materia table (川当归 is 当归), in pinyin or Latin (huáng qí, Radix Astragali), or another processed form of it (生地黄 and 熟地黄) | stop |
| Allergy that may be to the herb: a name only the materia table files under the herb's entry (丹皮 with 牡丹皮, 薯蓣 with 山药, but also 鸡蛋 with 鸡内金), a drug of the same species, or a species, genus or English common name of its source (芍药 and 赤芍, Panax ginseng, ginger), a formula of the pack or the knowledge base that holds it (四君子汤, 补中益气丸), one name inside the other (白附子 and 附子, 莱菔 and 莱菔子), the herb's name inside a longer word no table knows (郁金香, 知柏地黄丸), pinyin in an English sentence that says nothing of an allergy, a herb the history may deny or call tolerated (否认黄芪过敏, 黄芪无不适), a herb it says is taken (目前在吃人参), or 花粉 (pollen, and 天花粉's short name) | block |
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
tables hold (`bioagent.clinic.allergy`). An allergy written among the conditions (黄芪过敏
there) is read the same way, as written (对黄芪、人参过敏 there names both).

- **Normalised.** NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens,
  variation selectors, the Hangul fillers, the braille blank, NUL and the other control
  characters removed; look-alike radicals that NFKC leaves (⻩ for 黄, ⻰ for 龙, as text
  copied from a PDF writes them) read as the characters they show; traditional characters
  folded to simplified with the materia table's own table (`materia.to_simplified`), so 黃耆
  is read as 黄耆, 人參 as 人参 and 當歸 as 当归, and with a table of the traditional, Hong
  Kong, Taiwan and Japanese forms that one leaves, for every character of every name the
  scan knows and of the words a history writes around them (過敏, 對, 否認, 黑順片, 人銜,
  四君子湯, 当帰, 芍薬); tone marks dropped from Latin letters (Huáng Qí is huang qi); lower
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
    白及 after 蛋白, 于术 after 对于 (unless the 对 stands as a word of its own and a word of
    allergy follows: 对于术过敏 is 对 + 于术 + 过敏 and stops 白术, which the knowledge base
    gives 于术 to; 对于术后 is 对于 + 术后). Where the longer word is the name with a processing word
    before it, or a product suffix after it, it is that processed name (炙北芪, 炒夜交藤,
    甘草片, 山药粉). A name that starts with a denial's character and runs into what a denial
    names is the denial: 没药物过敏史 is 没 + 药物过敏史, not 没药.
  - A name right after a label that introduces a person (记录人：姜, 姓名, Dr. Huang Jing,
    interpreter Hong Hua, son Yuan Zhi, patient name: Huang Qi), with no word of allergy
    after it in its clause, or right before a title (Du Zhong, RN) with no word of allergy
    anywhere in its clause, is a person's name and is not read: Reaction to huang qi, MD
    notified is read. A label that names a drug (Allergen name:, Drug name:, Substance name:)
    is an allergy line's; after a bare one (Name: huang qi) the name asks, since such a label
    also introduces a person.
- **Denied, tolerated, taken, unconfirmed.** A name is *denied* when its clause opens with a
  denial (否认, 无, 没有, 未, 排除, NKDA, no …) that no 过敏, 史, 但, 出现, "except" or "than"
  closes before the name (否认黄芪过敏, 无黄芪过敏史, 否认青霉素、黄芪过敏, 已排除黄芪过敏), the
  clause being a bracket inside such a denial (否认中药（黄芪、人参）过敏, not a note after a
  whole denial: No allergies (uses licorice root tea)); when a denial follows the name or
  the list it ends (黄芪：无, 黄芪（-）, 黄芪皮试阴性, 黄芪无过敏史, 对黄芪无过敏反应, 黄芪过敏？否,
  黄芪、党参等未见过敏); or when the next clause only takes it back (怀疑黄芪过敏，已排除). A
  record that the herb is *tolerated* (黄芪耐受良好, tolerates 黄芪, 黄芪可用, 服黄芪无不适,
  长期服用黄芪无不良反应, 补中益气汤（含黄芪、人参）无不适) is a denial too, and its message says
  the record calls the herb tolerated. A word that only starts with a denial's character
  denies nothing: 非常, 未成年, 无意中, 没想到, 未经, 无数, no doubt. A dash or an arrow after a
  name is no denial (黄芪——皮疹, huangqi -> hives, 黄芪 -); "-" is one only in brackets or
  after a colon (黄芪（-）, 黄芪：-). A clause ends at punctuation other than 、, / and an
  apostrophe (can't), at a space between two Chinese characters, and at a line break or a tab
  whatever stands around it: NKDA, then a line that says Allergic to huang qi or 黄芪过敏, is two
  statements and stops 黄芪. "Allergies" closes a denial unless the denial goes on to name its
  object (No known drug allergies 黄芪过敏 stops 黄芪; No allergy to huang qi, No allergic
  reaction to huang qi and denies allergies to huang qi deny it), and a denial that takes no
  object (NKDA, NKA, nil, none) does not deny a name with a word of allergy after it (NKDA
  黄芪过敏 stops 黄芪; NKDA 北芪 asks). A record that the herb was taken and tolerated may put a
  time or a word of use between the herb and the denial (服用黄芪后无不适, 黄芪服用后未见过敏,
  服用黄芪期间无不良反应, 人参吃了没事, 当归用过，无过敏, 曾服当归，无反应, 喝黄芪水后没有不适) or say
  it in other words (人参可以吃, 人参没问题, 人参：可, Took ginseng, no reaction): each asks with
  the tolerated wording, never stops. A denial that names nothing (无, 否认过敏史,
  过敏史：无, NKDA) adds nothing, and it never hides a name in another clause: 食物过敏史：无
  药物过敏史：黄芪, 无药物过敏史但对黄芪过敏 and 无过敏史（黄芪过敏除外） all stop 黄芪. A name is
  *taken* when its clause, with the label before it (现用药：人参), says it is taken, eaten or
  used (目前在吃人参, 喜食生姜, 每周炖汤加党参、黄芪, 曾服四君子汤, 人参每日一支, 每日泡黄芪水喝,
  黄芪：服用中, currently on huangqi, takes ginseng daily) and the record says no reaction to it:
  neither that clause nor a clause that goes on from it in its sentence holds a word of
  allergy or reaction (过敏, 疹, 痒, 肿, 反应, 不适, 呼吸困难, 气促, 疙瘩, 心慌, 心跳, 发麻, 上火,
  禁用 …, allergic, rash, hives, can't take, does not tolerate). Such a word inside a name the
  scan found is the name's (泻 of 泽泻, so 目前在吃泽泻 is taken). A clause goes on from the
  herb's when the herb's ends in a time after it (服用黄芪后，皮肤瘙痒; 服用黄芪期间，…) or when it
  opens with what happened: a time after (3小时后, 次日), a part of the body (皮肤, 面部, 嘴唇),
  a word of feeling (自觉, 感觉), a reaction (皮疹), or in English then, got, developed, 2 hours
  later (以前喝过黄芪，出现皮疹; 服用黄芪，3小时后出现皮疹; 吃人参后，嘴唇肿了; Took huang qi, got a
  rash). A clause that names something of its own (目前在吃人参，青霉素过敏), a label or a denial
  (目前在吃人参，过敏史：无) does not go on. A herb named as a cause (出现皮疹，考虑与服用黄芪有关;
  全身瘙痒，服用黄芪所致; 全身皮疹，因服用黄芪) or in a note in brackets on a reaction written before
  it (皮疹（服黄芪后）, not 无过敏史（曾服四君子汤，效佳）) is no herb merely taken. These all
  stop the herb where a drug-level table names it. A Latin or pinyin name in an English
  sentence that holds other words and no word of allergy is *unconfirmed* (NKDA. Seen with
  Huang Qi today.): pinyin is also how a Chinese person's name is written.

An entry in which no name the tables know is found gives no issue: a record of no allergy
(无, 否认过敏史, 过敏史：无, 无药物过敏史, 暂无, NKDA, none, NKA, nil, N/A, No known drug
allergies, 过敏史：阴性 or （-）, 过敏体质 …), an allergy to what is no herb (青霉素, 头孢,
阿司匹林, 磺胺, 海鲜, 虾蟹, 牛奶, 花生, 芒果, 小麦, 尘螨, 乳胶, 碘, 酒精 …), what else an allergy
field holds (否认手术外伤史, 预防接种史随社会, 青霉素过敏（面部皮疹）), and a denied pollen
allergy (否认药物、食物及花粉过敏史). Where a table does link such a word to a herb, it asks
about that herb only: 鸡蛋 is filed with 鸡内金, 花粉 is 天花粉's short name. A drug whose name
starts with a word of negation (没药, 无食子) is still that drug. A blank field ("", spaces,
[""], ["。。。"]) is read as not asked, like `null`, with the 过敏史未询问 warning, and so is
one that holds only its label or a form's labels (过敏史：, Allergies:, Drug allergies:,
Known allergies:, Allergy hx:, Allergy status:, 过敏史（药物/食物）：, 过敏史及不良反应史：, 药物：____
食物：____, 过敏史：□无 □有, 过敏史：无/有, 过敏史：XX), or says the allergies were not asked or
are not known (待询问, 未能询问, 未查, 未提及, 患者不能配合, 不详, 暂不详, 目前不详, 未详, 不明确,
患者过敏史不详, 记不清, 记不清楚, 不太清楚, 不记得, 忘记了, 想不起来, 无法获取, 拒答, 详见病历,
unknown, unsure, unknown to patient, not known to patient, not recorded, not obtained, not
elicited, unable to assess, deferred, see chart); what such an entry holds is still scanned
(过敏史不详 人参过敏 stops 人参). `[]` is asked, none, and so is a labelled negative, with or
without a colon (过敏史：（-）, 过敏史：-, 过敏史（-）, （-）, (-)); a bare "-" may only start a
form's line and reads as not asked. Allergies, medications and conditions are text: a number
or an object there is refused, with the same message by the library and the studio
(`allergies must be a list of strings or null`).

Each name found is matched to every prescribed herb it fits, not only the first: 茯苓过敏
against 茯苓 and 茯神 stops 茯苓 and asks about 茯神, 地黄过敏 stops 生地黄 and 熟地黄 alike. The
herb is compared as written (folded like the history: 黑順片 and ⻩芪 are read), under its
pack alias and under its knowledge-base name (a prescription that writes 黑顺片 is read as
附子), through what it is a processed form of, and, for a prescribed name no table knows,
through the names inside it (山药 of 山药颗粒, as a near name).

An entry stops a herb (`allergy`) only when the name stands, is not denied, taken or
unconfirmed, and a drug-level table says the record names the herb:
- the same name (人参过敏史 names 人参);
- the seed knowledge base gives both names to one herb. Its names are a herb's own and its
  processed forms' (黄耆 and 北芪 are 黄芪, 芍药 is 白芍, 国老 and 炙甘草 are 甘草), and a
  processing word before one of them is the same herb processed (炙黄耆 is 黄芪);
- one of the pack's cited aliases of the herb (夜交藤 is 首乌藤), with or without a
  processing word before it (炒夜交藤, 制黑顺片, 炙松贝), or a reviewed alias of the materia
  table, which it checked name by name (川当归 is 当归);
- the herb's pinyin or Latin name (huang qin, Radix Astragali), in an allergy line;
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

An entry that may be the herb, where no drug-level table says it is, is a block. It can be
signed only with the practitioner's recorded reason, which answers the check its message
asks for (请核实). There are six kinds:
- `allergy_source`: the materia table lists the name, or what it is a processed form of,
  under the herb's entry (丹皮 and 牡丹皮, 熟川军 and 大黄). The table groups names by
  source, not by drug: its 鸡内金 also lists 鸡蛋, and its 海藻 lists 海带 (Saccharina) and
  昆布, though 海藻 is Sargassum. So the message says only where the table files the two
  (药材名表将海带与海藻同列于“海藻”条下（未必同一物种）) and never that they are one herb or one
  species. Or the two drugs share a species, by the materia table's species, the knowledge
  base's, or the species the pack records for a herb the table lacks: 芍药 and 赤芍 (Paeonia
  lactiflora), 何首乌 and 首乌藤 (Polygonum multiflorum, root and stem). Or the entry names a
  species, a genus or an English common name of the herb's source (allergic to Panax
  ginseng, Astragalus, ginger): a plant is not a drug. Then the message names the species.
- `allergy_formula`: the entry names a formula the pack or the knowledge base holds, or the
  same name in another product form (四君子汤过敏, 服用四君子汤后出现皮疹, 补中益气丸过敏,
  龙胆泻肝丸过敏). The allergy may be to any herb of it, so each prescribed herb that is one of
  its herbs, or that a tier above ties to one (生地黄 to the 熟地黄 of 六味地黄丸), asks; the
  message gives the formula's composition and, for another product form, says the
  composition may differ. A draft of the same formula is never signed in silence.
- `allergy_near_name`: one name inside the other, where the tables give the two names to
  different drugs (白附子 and 香附子 are not 附子, 核桃仁 is not 桃仁, 土茯苓 is not 茯苓) or say
  nothing (茱萸 may mean either drug); a name found inside a longer word no table knows,
  whatever else it is, with a message that says only what the record holds: 记录中的“郁金香”
  含有“郁金”的字样，但“郁金香”不是名表所载的药名，未必是指郁金. A patent medicine the tables do
  not hold is not read as its herbs: 知柏地黄丸, 桂枝茯苓丸 and 复方甘草片 ask about the herbs
  named in them, they do not stop them. And a pinyin or Latin name the record leaves
  unconfirmed: the message says the sentence says nothing of an allergy and that pinyin is
  also how names are written.
- `allergy_denied`: the history may deny an allergy to the herb, or call it tolerated
  (above); the message says which.
- `allergy_taken`: the history says the herb is taken or eaten, with no reaction written
  (目前在吃人参, 现用药：人参、黄芪泡水, 无过敏史（曾服四君子汤，效佳）, NKDA, currently on
  huangqi). That is no allergy and no proof of none: the message says what the record says
  and asks. A formula taken asks about its herbs this way, never with the formula-allergy
  wording.
- `allergy_homonym`: an entry of 花粉 (花粉, 花粉过敏 or 对花粉过敏). In an allergy history
  it means pollen, the most common entry of all. In prescriptions 花粉 is 天花粉's short
  name, and the materia table files it there. So it is a block for 天花粉 only, asking
  which was meant. It does not reach 瓜蒌, though 瓜蒌 comes from the same Trichosanthes, and
  a denied pollen allergy asks nothing.

For each herb a stop stands alone; otherwise there is one block of each kind, listing the
first places that gave it and how many more. Each distinct reading is matched to each herb
once, and every step of the reading looks a bounded way around the name (what follows a
clause or a list is worked out once for the whole record), so the time is linear in the
record's length: 20,000 and 200,000 characters of any content (runs of names with no
separators, chains of overlapping names such as 钩钩钩… or 红花椒红花椒…, names across
whitespace, one long list, a clause of names followed by a long run of marks) take about
ten times as long at 200,000 characters as at 20,000, through the intake and the check as
through the reading alone: the intake's label and negative patterns read a run of
whitespace once.

The acceptance corpus, `BioScience-Harness/tests/data/clinic_allergy_corpus.json`, holds
every repro of the reviews of this reading and the common forms of the acceptance
criteria, each with its outcome per prescribed herb (stop, block or none), the records that
must name no herb, and the allergy fields that must read as not asked. `tests/test_clinic.py`
runs it, checks that every name the tables give every pack herb, in each common form and in
traditional characters, is never silent, that every stop gives only a reason a drug-level
table gives, and times 20,000 against 200,000 characters.

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
  A name right after a person label with nothing of an allergy after it is read as a
  person's (记录人：姜).
- A short name only the materia table gives (生地, 丹皮, 银花, 枣, 枸杞) asks rather than
  stops, as above.
- A patent medicine the tables do not hold is not read as the herbs it holds: 双黄连 asks
  about 黄连 only, not about 金银花, 黄芩 and 连翘, and 参麦注射液 asks nothing.
- A denial or a herb taken is read by its clauses and a few words, not its grammar.
  否认青霉素过敏、黄芪过敏 stops 黄芪 (the 过敏 after 青霉素 closes the denial); a clause that
  opens with a denial and names a herb later asks, even where the herb was not denied; a
  reaction in a later clause that names something else (目前在吃人参，青霉素过敏) leaves 人参
  taken, and so does a reaction written before the herb with nothing that ties the two
  (全身皮疹，服用黄芪后): a block either way. A clause after a herb's time that names a reaction
  to something else (服用黄芪后，青霉素过敏) stops the herb. A line of NKDA followed on the same
  line by a herb and no word of allergy (NKDA 北芪) asks as a denial.
- Pinyin stops in an allergy line and asks in other English text; a person's name written
  in an allergy line with no title before it is still read as the herb.
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
  - 过敏：不按一张措辞清单（过敏史：X、对X过敏、X过敏史……）读过敏史——清单漏掉的每种写法都会静默放行，而过敏史的写法远多于任何清单（患者对国老过敏、3年前北芪过敏、北芪/人参过敏、变应原：北芪、allergic to huangqi）。每条过敏记录按原样先规范化，再扫描（`bioagent.clinic.allergy`）其中名表所载的每个药名；写在病史（conditions）里的过敏（如“黄芪过敏”）按原样同样读取（对黄芪、人参过敏两味都指）：
    - 规范化：NFKC；去掉零宽空格与连接符、字节顺序标记、软连字符、异体字选择符、韩文填充符、盲文空白、NUL 等控制字符；NFKC 不转换的形近部首（从 PDF 复制的⻩、⻰）按其所示的字读（黄、龙）；繁体字按药材名表自带的字表（`materia.to_simplified`）转为简体，所以黃耆读作黄耆、人參读作人参、當歸读作当归；该表未收、而扫描所用药名及过敏史常用字中出现的繁体、港台与日文字形另按一张字表转换（過敏、對、否認、黑順片、人銜、四君子湯、当帰、芍薬），这张字表已按 OpenCC 的 s2t、s2hk、s2tw 对每个这样的字核对过（OpenCC 不是依赖，装有它时测试会复核）；拉丁字母去掉声调符号（Huáng Qí 即 huang qi）；转为小写；
    - 只扫描名表所载的名称：种子知识库的药名与别名（黄耆、北芪、国老）；药材名表每一条的全部名称，含单字名（姜、枣）；知识包的药名及其注明出处的别名（`herb_aliases`，见下）；知识包和知识库所收方剂的方名与别名（四君子汤、阳旦汤），以及同一方名的其他剂型（补中益气丸、天麻钩藤颗粒、四物膏、补中益气方；“散”“饮”只用于知识包的方剂，所以四逆散不是知识库的四逆汤；单味药名后的“片”不算剂型，麻黄片是麻黄饮片）。名表未列的药名部分或简写一概不扫描：此前逐一扫描每个药名的每个部分，结果读进了日常用词（明天麻的“明天”、淡黄芩的“淡黄”、金铃子的“金子”），已删去。拉丁名与拼音按整词匹配，可带空格、连字符、撇号、声调符号或声调数字（huang qi、Huang-Qi、Huang'qi、huang2 qi2、Huáng Qí）：种子知识库的拼音名与拉丁名，知识包给每味药的拼音（`herb_pinyin`），药材名表与知识包的拉丁名（首乌藤的 Polygoni Multiflori Caulis），按药典 2020 年版的词序和旧词序（Astragali Radix 与 Radix Astragali），名表所载的物种名和属名（Panax ginseng、Astragalus），以及知识包注明出处的英文俗名（`herb_common_names`：ginger、licorice、ginseng、cinnamon、astragalus、dong quai 等）。每个位置的每个药名都是候选，所以较短的药名不会因同一位置起始的较长药名而丢失：枣皮疹中既有独立在“皮疹”之前的“枣”，也有长词中的“枣皮”；
    - 读两遍：一遍以空白为硬分界，所以一行一条或以空格分隔的过敏清单“姜 / 黄芪”读作姜和黄芪；一遍去掉空白，所以北 芪即北芪。所找到的药名取两遍之并。只在跨空白时才找到的药名，若与第一遍中独立成词的两字以上药名重叠则舍去（姜 / 黄芪不读作姜黄，姜 / 半夏不读作姜半夏），若两遍都不独立成词、又与第一遍找到的药名重叠也舍去（枣 / 皮疹不读作枣皮）；
    - 按所在位置读：药名前后是分界（条目首尾、标点、空白、数字、拉丁字母），或是过敏史中常写在药名前后的字词（对、患者、自诉、因、的、吃、一吃……就、似乎、小时候、年前、服用、过敏、过敏史、变应原、及、等、均、后、皮疹、水、禁用……），或是在其另一侧同样独立成词的两字以上药名时，该药名“独立成词”：北芪人参过敏两味都指，因黄芪过敏住院、对黄芪的成分过敏、黄芪吃了过敏都指黄芪。方名只有其前面本身是分界时才独立成词（柴胡桂枝汤中的桂枝汤在长词中）。单字药名（姜、枣、归、桂、面）只有两侧本身都是分界、且只取较短的一组字词（对、服用、吃、及、和、因、年、中药、过敏、过敏史、后、就、皮疹、出现、引起、致、会、严重、特别、否认……）时才独立成词：对姜过敏、姜过敏史、、姜、、服用姜后出现皮疹、对姜严重过敏、吃姜会过敏、吃姜出现皮疹、姜致皮疹、2019年姜过敏、对中药姜过敏；这些字词与姜、归、枣、桂、芎的每种组合都查过是否构成日常用词，查到的“归类”单独成词。在其他词中一概不算药名：面部、方便面、姜某、枣红色、归档、归类、桂林、沙姜、姜片都不提示。
      - 在独立成词的较长药名或方名之中的药名归该长名（苦杏仁中的杏仁、六味地黄丸中的地黄、天麻钩藤颗粒中的天麻），改按长名比对。少数含药名字样的日常用词单独成词，不指任何药（胡萝卜、桂花、桂鱼、舌头）；
      - 不独立成词的两字以上药名“在更长的词中”：郁金香中的郁金、知柏地黄丸中的地黄、双黄连中的黄连、预防风疹疫苗中的防风、桂枝茯苓丸中的桂枝和茯苓、蛋白之后的白及、对于之后的于术（但“对”本身独立成词、药名后又是过敏字样时除外：对于术过敏是“对 + 于术 + 过敏”，知识库把于术归为白术，所以停止白术；对于术后仍是“对于 + 术后”）。长词是该药名前加炮制词、或后加制品字（片、粉、末等）时，即为该药的炮制品（炙北芪、炒夜交藤、甘草片、山药粉）。以否认用字开头、又接着否认所指对象的药名，按否认读：没药物过敏史是“没 + 药物过敏史”，不是没药；
      - 紧接在表示人的标签之后、且所在分句其后没有过敏字样的名称，或紧接在称谓之前（Du Zhong, RN）、且所在分句中没有任何过敏字样的名称，是人名，不读（记录人：姜、姓名、Dr. Huang Jing、interpreter Hong Hua、son Yuan Zhi、patient name: Huang Qi）；Reaction to huang qi, MD notified 照读。指明药物的标签（Allergen name:、Drug name:、Substance name:）是过敏语句的标签；只写 Name: 的标签也可能引出人名，其后的拼音须说明理由；
    - 否认、耐受、在用、未确认：药名所在分句以否认用语（否认、无、没有、未、排除、NKDA、no……）开头，且在药名之前没有“过敏”“史”“但”“出现”、except 或 than 把否认截断（否认黄芪过敏、无黄芪过敏史、否认青霉素、黄芪过敏、已排除黄芪过敏），或药名在这样的否认中的括号里（否认中药（黄芪、人参）过敏；整句否认之后的括号注释不算：No allergies (uses licorice root tea)）；或药名、或药名所在的列举之后写有否认（黄芪：无、黄芪（-）、黄芪皮试阴性、黄芪无过敏史、对黄芪无过敏反应、黄芪过敏？否、黄芪、党参等未见过敏）；或下一分句只是收回前句（怀疑黄芪过敏，已排除），即为“被否认”。记录说该药可耐受（黄芪耐受良好、tolerates 黄芪、黄芪可用、服黄芪无不适、长期服用黄芪无不良反应、补中益气汤（含黄芪、人参）无不适）也算否认，消息说明记录称其可耐受。只是以否认用字开头的词不算否认：非常、未成年、无意中、没想到、未经、无数、no doubt。药名后的破折号或箭头不是否认（黄芪——皮疹、huangqi -> hives、黄芪 -）；“-”只有在括号中或冒号后才是否认（黄芪（-）、黄芪：-）。分句在“、”“/”和撇号（can't）以外的标点处结束，也在两个汉字之间的空格处结束，并且无论前后是什么字，都在换行和制表符处结束：一行写 NKDA、下一行写 Allergic to huang qi 或黄芪过敏，是两句话，停止黄芪。allergies 会截断否认，除非否认接着写出其对象（No known drug allergies 黄芪过敏停止黄芪；No allergy to huang qi、No allergic reaction to huang qi、denies allergies to huang qi 仍是否认）；不带宾语的否认（NKDA、NKA、nil、none）不否认其后带过敏字样的药名（NKDA 黄芪过敏停止黄芪；NKDA 北芪须说明理由）。记录说服用后可耐受时，药名与否认之间可以有时间或服用字样（服用黄芪后无不适、黄芪服用后未见过敏、服用黄芪期间无不良反应、人参吃了没事、当归用过，无过敏、曾服当归，无反应、喝黄芪水后没有不适），也可以换种说法（人参可以吃、人参没问题、人参：可、Took ginseng, no reaction）：都按“可耐受”的措辞提问，不停止。不指任何药的否认（无、否认过敏史、过敏史：无、NKDA）不增加任何内容，也不会掩盖其他分句中的药名：食物过敏史：无 药物过敏史：黄芪、无药物过敏史但对黄芪过敏、无过敏史（黄芪过敏除外）都停止黄芪。药名所在分句（连同其前的标签，如“现用药：人参”）写的是服用、食用或使用该药（目前在吃人参、喜食生姜、每周炖汤加党参、黄芪、曾服四君子汤、人参每日一支、每日泡黄芪水喝、黄芪：服用中、currently on huangqi、takes ginseng daily），且记录未写对它的反应——该分句及同一句中接续它的分句都没有过敏或反应的字样（过敏、疹、痒、肿、反应、不适、呼吸困难、气促、疙瘩、心慌、心跳、发麻、上火、禁用……，allergic、rash、hives、can't take、does not tolerate）——时，该药“在用”。扫描到的药名中的这类字归药名（泽泻的“泻”，所以目前在吃泽泻仍是在用）。药名所在分句以药名之后的时间结尾（服用黄芪后，皮肤瘙痒；服用黄芪期间，……），或下一分句以所发生的事开头——之后的时间（3小时后、次日）、身体部位（皮肤、面部、嘴唇）、感受（自觉、感觉）、反应（皮疹），英文的 then、got、developed、2 hours later——时，下一分句接续它（以前喝过黄芪，出现皮疹；服用黄芪，3小时后出现皮疹；吃人参后，嘴唇肿了；Took huang qi, got a rash）。另写别的对象（目前在吃人参，青霉素过敏）、标签或否认（目前在吃人参，过敏史：无）的分句不接续。药名被写成原因（出现皮疹，考虑与服用黄芪有关；全身瘙痒，服用黄芪所致；全身皮疹，因服用黄芪），或写在说明前面反应的括号中（皮疹（服黄芪后），而不是无过敏史（曾服四君子汤，效佳）），都不只是在用。这些在有药物一级名表时都停止该药。英文句子中除药名外还有别的词、又没有过敏字样时，其中的拉丁名或拼音“未确认”（NKDA. Seen with Huang Qi today.）：拼音也是中国人名的写法；
    - 扫描不到任何名表所载药名的条目不提示：记为无过敏的（无、否认过敏史、过敏史：无、无药物过敏史、暂无、NKDA、none、NKA、nil、N/A、No known drug allergies、过敏史：阴性或（-）、过敏体质……），对非药材过敏的（青霉素、头孢、阿司匹林、磺胺、海鲜、虾蟹、牛奶、花生、芒果、小麦、尘螨、乳胶、碘、酒精……），过敏史栏中的其他内容（否认手术外伤史、预防接种史随社会、青霉素过敏（面部皮疹）），以及否认花粉过敏（否认药物、食物及花粉过敏史）。名表确把这类词与某药相连时，只就该药提问：鸡蛋列在鸡内金下，花粉是天花粉的简称。以否认用字开头的药名（没药、无食子）仍是该药。空白的过敏史（""、空格、[""]、["。。。"]）与 `null` 一样视为未询问，并提示“过敏史未询问”；只有标签或表格标签的（过敏史：、Allergies:、Drug allergies:、Known allergies:、过敏史（药物/食物）：、过敏史及不良反应史：、药物：____ 食物：____、过敏史：□无 □有、过敏史：无/有、过敏史：XX），或写明未询问、不详的（待询问、未能询问、未查、未提及、患者不能配合、不详、暂不详、目前不详、未详、不明确、患者过敏史不详、记不清、记不清楚、不太清楚、不记得、忘记了、想不起来、无法获取、拒答、详见病历、unknown、unsure、unknown to patient、not known to patient、not recorded、not obtained、not elicited、unable to assess、deferred、see chart，标签也包括 Allergy hx:、Allergy status:）也是如此，但其内容仍会扫描（过敏史不详 人参过敏仍停止人参）。`[]` 才是询问后无，写阴性的也是询问后无，标签后有无冒号均可（过敏史：（-）、过敏史：-、过敏史（-）、（-）、(-)）；单独的“-”可能只是表格行首，按未询问读。过敏史、用药与病史须为文字：填入数字或对象，库与工作室都以同一消息拒绝（`allergies must be a list of strings or null`）；
    - 每个找到的药名与它相符的每一味处方药比对，而不只是第一味：茯苓过敏对茯苓停止、对茯神提问，地黄过敏对生地黄、熟地黄都停止。处方药按原名（与过敏史同样规范化：黑順片、⻩芪都能读）、按知识包别名、按知识库名称（处方写黑顺片即附子）、按其所炮制的药比对；名表都不认识的处方药名还按其中所含药名比对（山药颗粒中的山药，按名称相近处理）；
    - 停止：药名独立成词、未被否认、不在用、不是未确认，且有药物一级的名表说记录所写即该药：与药名相同（人参过敏史含人参）；或种子知识库认定为同一药材（黄耆、北芪即黄芪，芍药即白芍，国老、炙甘草即甘草），其名称前加炮制词的仍是该药（炙黄耆即黄芪）；或为知识包注明出处的别名，前面有没有炮制词都算（夜交藤即首乌藤，炒夜交藤、制黑顺片、炙松贝亦然），或药材名表逐一复核过的别名（川当归即当归）；或为该药的拼音名、拉丁名（huang qin、Radix Astragali），写在过敏语句中；或二者是同一药材的不同炮制品：名表所给药名前加炮制词即该药的炮制品，所以生地黄、熟地黄都是地黄，对其一过敏即停止另一；熟地是知识库给熟地黄的简称，经它归并到生地黄；甘草片、山药粉是该药的饮片和粉末。“鲜”“干”等状态词只在药材名表把两名列于同一条下时去掉（鲜地黄即地黄，干姜不是姜）；药材名表列在另一药名下的名称不算其所含药名的炮制品（炮姜列在干姜下，不是姜即生姜的炮制品）。一名包含另一名本身从不停止，只有药材名表给出的名称也不停止：莱菔（萝卜）与莱菔子（其种子）、车前与车前子、菖蒲与石菖蒲、丹皮与牡丹皮、生地与生地黄、枣与大枣，都被该表列在同一条下，而该表按来源归并名称，分不清同义名（丹皮）与同源之物（鸡内金下的鸡蛋），所以都只提问（`allergy_source`）。姜之所以停止生姜，只因为二者是炮制关系：“生”是炮制词，该表又把姜列在生姜下；枣对大枣只提问，因为“大”不是炮制词。消息写出找到的药名，而不是其前后的文字：患者对地黄过敏对熟地黄给出（地黄与熟地黄同为地黄或其炮制品）；40 字以内的记录按原文整条引用，更长的只按原文引用药名前后的一段。每条消息的引文都有上限：药名前后各 12 字，所在长词最多前后各 6 字，每味药每类只列前四处及其余处数；
    - 须说明理由：记录可能指该药、但没有药物一级的名表说它就是该药时，医师须记录理由（即消息所说的“请核实”）才能签署，分六类：
      - `allergy_source`：药材名表把该名、或其所炮制的药，列在该药条下（丹皮与牡丹皮，熟川军与大黄）。该表按来源而非按药归并名称：鸡内金下列有鸡蛋，海藻（马尾藻属）下列有海带（海带属）、昆布，所以消息只说该表把二者列于何条之下（药材名表将海带与海藻同列于“海藻”条下（未必同一物种）），不称为同一药材或同一物种；或二者来自同一物种，物种取自药材名表、知识库，或名表未收的药取知识包记录的物种：芍药与赤芍（Paeonia lactiflora），何首乌与首乌藤（Polygonum multiflorum，根与藤茎）；或记录写的是该药来源的物种名、属名或英文俗名（allergic to Panax ginseng、Astragalus、ginger）——植物不等于药材。此时消息写出物种；
      - `allergy_formula`：记录写的是知识包或知识库所收的方剂，或同一方名的其他剂型（四君子汤过敏、服用四君子汤后出现皮疹、补中益气丸过敏、龙胆泻肝丸过敏）。过敏可能由方中任一味药引起，所以凡是该方组成药、或经上述各层与组成药相关的处方药（生地黄与六味地黄丸中的熟地黄），都须说明理由；消息写出该方组成，其他剂型还说明组成未必完全相同。起草同一首方剂不会静默通过；
      - `allergy_near_name`：一名包含另一名，而名表认定为不同药材（白附子、香附子不是附子，核桃仁不是桃仁，土茯苓不是茯苓）或未作说明（“茱萸”可能指其中任一种）；在名表不认识的长词中找到的药名，无论属于哪一层，也归此类，消息只说记录中有什么：记录中的“郁金香”含有“郁金”的字样，但“郁金香”不是名表所载的药名，未必是指郁金。名表未收的中成药不拆解为其药材：知柏地黄丸、桂枝茯苓丸、复方甘草片对其名中的药须说明理由，不停止。未确认的拉丁名或拼音也归此类，消息说明所在语句未写过敏、拼音也是人名的写法；
      - `allergy_denied`：记录可能否认了对该药过敏，或说该药可耐受（见上），消息说明是哪一种；
      - `allergy_taken`：记录说正在服用或食用该药，且未写任何反应（目前在吃人参、现用药：人参、黄芪泡水、无过敏史（曾服四君子汤，效佳）、NKDA, currently on huangqi）。这不是过敏记录，也不能据此排除过敏：消息照录记录所写并请核实。在用的方剂也这样对其组成药提问，不用方剂过敏的措辞；
      - `allergy_homonym`：记录为“花粉”（花粉、花粉过敏、对花粉过敏）。过敏史中的花粉指植物花粉，是最常见的过敏记录；处方中“花粉”是天花粉的简称，药材名表也列在天花粉下。所以只对天花粉要求说明理由，问清所指；不延及同为栝楼属的瓜蒌；否认花粉过敏不提示；
    - 每味药有“停止”时只列“停止”；否则每类各列一条，写出给出该类的前几处及其余处数。每种不同的读法与每味药只比对一次，读取的每一步只看药名附近有限的范围（分句或列举之后的内容全文只计算一次），所以读取时间与记录长度成正比：2 万字与 20 万字的任意内容（不加分隔的连写药名、钩钩钩……或红花椒红花椒……这类互相重叠的药名链、跨空白的药名、很长的列举、一串药名之后跟很长一串标点）20 万字所用时间约为 2 万字的十倍；经过四诊采集再核查时也是如此：采集中识别标签和阴性的模式对一串空白只读一遍；
    - 验收语料 `BioScience-Harness/tests/data/clinic_allergy_corpus.json` 收录两轮审查的全部复现记录和验收标准中的常见写法，每条写明对每味处方药的结果（停止、须说明理由或不提示），以及不得指向任何药材的记录和必须视为未询问的过敏史栏；`tests/test_clinic.py` 逐条运行，并检查名表给每味知识包药材的每个名称，按每种常见写法和繁体字书写，都不会静默放行，每个“停止”只给出药物一级名表所给的理由，并对比 2 万字与 20 万字的用时；
    - 知识包的名称：`herb_aliases` 收录两个名表都没有的知识包药材别名，每个注明出处：夜交藤（首乌藤，《中药学》），以及《药典》2020 年版的习称：川黄柏（黄柏），毛山药、光山药（山药），味连、雅连、云连（黄连），盐附子、黑顺片、白附片（附子），松贝、青贝、炉贝（川贝母），大贝、珠贝、浙贝片（浙贝母），连丹皮、刮丹皮（牡丹皮）。`herb_sources` 记录首乌藤的物种和拉丁名——它是药材名表唯一未收的知识包药材（药典 2020：何首乌原植物的干燥藤茎，Polygoni Multiflori Caulis）。`herb_pinyin` 给出每味知识包药材的拼音，是音译而非别名（多音字按药名读音：川芎 chuanxiong，黄柏 huangbo 或 huangbai）。`herb_common_names` 给出英文俗名，每个注明所指物种和出处的 WHO 专论（ginger，Rhizoma Zingiberis；licorice，Radix Glycyrrhizae；ginseng，Radix Ginseng；cinnamon、cassia，Cortex Cinnamomi；astragalus，Radix Astragali；ephedra；turmeric；peony；coptis；bupleurum；platycodon；dong quai，Radix Angelicae Sinensis），按物种读，只提问、不停止。只收有出处的名称；没有出处、同一别名给了两味药、别名或拼音所指的药不在知识包中、俗名没有物种或出处，知识包都拒绝加载；
    - 仍会漏读或多读（验收标准之外的写法）：名表中没有的名称、简写或误写不识别——淮山、怀山（山药），法夏、姜夏、清夏（法半夏），川柏（黄柏），双钩、勾藤（钩藤），交藤（首乌藤），贝母（川贝母、浙贝母都不识别），苡米、米仁（薏苡仁），公英（蒲公英），桂圆（龙眼肉），野山参、园参（人参），贡菊、亳菊（菊花），老翘（连翘），北杏（苦杏仁），焦三仙，肾气丸，去掉剂型字的方名（四君子），柴胡舒肝丸（“舒”作“疏”），生龙牡，古写黄蓍，误写（川穹、白敛、姜蚕、沙仁、牛夕、瓜娄），日文欝金、釣藤，泛称（参类、中药过敏），简称的拼音（shu di、dan pi、bei qi、gou qi），知识包未注明出处的英文俗名（aconite、goji、honeysuckle、hawthorn、jujube、mint、tang kuei），不是名表药名换词序而得的旧拉丁名（Flos Lonicerae、Herba Menthae、Radix Platycodi），以及一个词内的声调数字（huang2qi2）。这些都需要名表中有出处的条目；逐一扫描药名各部分的做法虽能读出其中一些，也读进了日常用词。单字药名只在单独成条或位于过敏字词之间时才算：姜蒜过敏、老姜过敏、对姜片过敏、姜注射液过敏、服用含姜的中药后出现皮疹不涉及生姜；紧接在人名标签后、其后又无过敏字样的名称按人名读（记录人：姜）。只有药材名表给出的简称（生地、丹皮、银花、枣、枸杞）只提问、不停止（见上）。名表未收的中成药不拆解为其所含药材：双黄连只就黄连提问，不涉及金银花、黄芩、连翘；参麦注射液不提示。否认与在用都按分句和少数字词而非语法判断：否认青霉素过敏、黄芪过敏会停止黄芪（青霉素后的“过敏”截断了否认）；以否认开头、后面才写到药名的分句会提问，即使该药并未被否认；后一分句的反应若写的是别的东西（目前在吃人参，青霉素过敏），人参仍按在用提问，写在药名之前、又没有字词把二者连起来的反应（全身皮疹，服用黄芪后）也按在用读：两种都须说明理由。药名之后的时间后面若另写对别的东西的反应（服用黄芪后，青霉素过敏），会停止该药。同一行中 NKDA 之后写药名、其后没有过敏字样的（NKDA 北芪），按否认提问。拼音在过敏语句中停止，在其他英文中提问；过敏语句中不带称谓的人名仍会停止。物种和属名指植物而非药材：Panax ginseng 对人参只提问、不停止，属名（Citrus、Prunus）对该属的每味知识包药材提问。上述以外的字符（其他形近字、私用区字符）不转换；只读过敏史栏和病史中的过敏条目：写在别处的反应（主诉“服用含黄芪的中药后全身皮疹”，病史中不带“过敏”二字的“药疹（黄芪所致）”）不读；
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
