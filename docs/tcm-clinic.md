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
| 四诊 intake | Records each examination separately (望 inspection with the tongue, 闻 listening and smelling, 问 inquiry, 切 palpation with the pulse), as present findings and pertinent negatives, plus vital signs, medications, allergies and conditions. A field that was not asked (`null`) is kept distinct from one asked with nothing found (`[]`); a blank one ("", [""]) records neither and is read as not asked. The template lists every finding the criteria score, in the 十问 order. | an unusable intake (no age, a male recorded as pregnant, an allergy given as a number or an object) |
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
| Allergy, to the herb: by name, under any name the seed knowledge base or the pack's cited aliases give it (黄耆 or 北芪 is 黄芪, 国老 is 甘草, 夜交藤 is 首乌藤), or to another processed form of it (生地黄 and 熟地黄) | stop |
| Allergy that may be to the herb: a name the materia table files under the herb's entry (薯蓣 with 山药, but also 鸡蛋 with 鸡内金), a drug of the same species (芍药 and 赤芍), a near name the name tables give another drug (白附子 and 附子), the herb's name inside a longer word no table knows (郁金香, 六味地黄丸), a herb the history may deny (否认黄芪过敏), or 花粉 (pollen, and 天花粉's short name) | block |
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

The check does not read a history through a list of wordings (过敏史：X, 对X过敏, X过敏史 …).
Each wording such a list missed was a silent pass, and histories are written in more ways
than a list holds: 患者对国老过敏, 3年前北芪过敏, 北芪/人参过敏, 变应原：北芪, allergic to
huangqi. Each allergy entry, as written, is normalised and then scanned for every name the
tables know (`bioagent.clinic.allergy`):

- **Normalised.** NFKC; zero-width spaces and joiners, the byte-order mark, soft hyphens, NUL
  and the other control characters removed; traditional characters folded to simplified
  with the materia table's own table (`materia.to_simplified`), so 黃耆 is read as 黄耆, 人參
  as 人参 and 當歸 as 当归; the words a history writes around a name folded with a few more
  pairs that table leaves out (過敏, 對, 否認, 不詳, 體質); lower case. Whitespace is dropped
  for the scan, so 北 芪 is 北芪, but it is kept as a boundary.
- **Scanned** for the seed knowledge base's herb names and aliases, with their pinyin and
  Latin names (黄耆, 北芪, 国老, huang qi, Astragali Radix); the materia table's names
  (`materia.names_in`, and every entry's own names, one character included: 姜, 枣); the
  pack's herbs and its cited aliases (`herb_aliases`, below); and the prescribed herbs' own
  names and their parts of two characters or more (贝母 of 浙贝母). Pinyin and Latin names are
  matched as whole words. At each position the longest name is taken, and a name inside a
  longer one found before it (杏仁 in 苦杏仁) is that one's.
- **Read in its place.** A name *stands as a word* when what is before and after it is a
  boundary (the start or end of the entry, punctuation, a space, a digit, Latin letters),
  another name (黄耆人参过敏), or one of the words a history writes around a name (对, 患者,
  自诉, 年前, 服用, 过敏, 过敏史, 变应原, 及, 等, 均, 后, 皮疹 …). Otherwise it is *inside a
  longer word*: 郁金 in 郁金香, 地黄 in 六味地黄丸, 黄连 in 双黄连, 防风 in 预防风疹疫苗, 白及 in
  蛋白及. A longer word that the materia table or the knowledge base resolves to the same
  drug is that drug (甘草片, 山药粉, 炙北芪).
- **Denials fail safe.** A name is *denied* when its clause opens with a denial (否认, 无,
  没有, 未, NKDA, no …) that no 过敏, 史, 但, 出现 or "except" closes before the name
  (否认黄芪过敏, 无黄芪过敏史, 否认青霉素、黄芪过敏), or when a denial follows it (黄芪：无,
  黄芪（-）, 黄芪皮试阴性, 黄芪不过敏). A clause ends at punctuation other than 、 and /, and at
  a space between two Chinese characters. A denial that names nothing (无, 否认过敏史,
  过敏史：无, NKDA) adds nothing, and it never hides a name in another clause:
  食物过敏史：无 药物过敏史：黄芪, 无药物过敏史但对黄芪过敏 and 无过敏史（黄芪过敏除外） all stop
  黄芪. A denied herb is not passed in silence either: it is a block (`allergy_denied`) whose
  message says the clause holds a denial and asks the practitioner to confirm it.

An entry in which no name the tables know is found gives no issue: a record of no allergy
(无, 否认过敏史, 过敏史：无, 无药物过敏史, 暂无, NKDA, none, N/A, 过敏史：阴性 or （-）, 不详,
过敏体质 …) and an allergy to what is no herb (青霉素, 海鲜, 头孢, 芒果, 牛奶, 花生, 尘螨, 碘,
小麦, 乳胶 …). A drug whose name starts with a word of negation (没药, 无食子) is still that
drug. A blank field ("", spaces, [""], ["。。。"]) is read as not asked, like `null`, with the
过敏史未询问 warning; `[]` is asked, none. Allergies, medications and conditions are text: a
number or an object there is refused, with the message the studio gives
(`allergies must be a list of strings or null`).

Each name found is matched to each prescribed herb. The herb is compared as written, under
its pack alias and under its knowledge-base name (a prescription that writes 黑顺片 is read as
附子), and a prescribed name no table knows also through the names inside it (山药 of
山药颗粒, as a near name).

An entry stops a herb (`allergy`) when the name stands as a word, is not denied, and:
- it is the herb's name (人参过敏史 names 人参), or one name is inside the other where the
  name tables do not give them to different drugs (贝母 and 浙贝母, 黄芪 and 炙黄芪);
- or the seed knowledge base gives both names to one herb. Its names are a herb's own and
  its processed forms' (黄耆 and 北芪 are 黄芪, 芍药 is 白芍, 国老 and 炙甘草 are 甘草), and a
  processing word before one of them is the same herb processed (炙黄耆 is 黄芪);
- or it is one of the pack's cited aliases of the herb (夜交藤 is 首乌藤);
- or the two are processed forms of one drug. A processing word before a known name is
  that drug processed, so 生地黄 and 熟地黄 are both 地黄, and an allergy to either stops
  the other. A short name reaches the other form through its full one: 生地 is 生地黄's
  short name and 熟地 熟地黄's. A word of state (鲜, 干) is dropped only where the materia
  table files both names under one entry (鲜生地 is 生地, but 干姜 is not 姜). A name the
  table files under another drug is not a processed form of the one it seems to hold: 炮姜
  is under 干姜, not under 姜 (生姜).

The message names the herb name found, not the text around it: 患者对地黄过敏 against 熟地黄
gives （地黄与熟地黄同为地黄或其炮制品）.

An entry that may be the herb, where the names cannot tell, is a block. It can be signed
only with the practitioner's recorded reason, which answers the check its message asks
for (请核实). There are four kinds:
- `allergy_source`: the materia table lists the name under the herb's entry. The table
  groups names by source, not by drug: its 鸡内金 also lists 鸡蛋, and its 海藻 lists 海带
  (Saccharina) and 昆布, though 海藻 is Sargassum. So the message says only where the table
  files the two (药材名表将海带与海藻同列于“海藻”条下（未必同一物种）) and never that they
  are one herb or one species. Or the two drugs share a species, by the materia table's
  species or by the species the pack records for a herb the table lacks: 芍药 and 赤芍
  (Paeonia lactiflora), 何首乌 and 首乌藤 (Polygonum multiflorum, root and stem). Then the
  message names the species.
- `allergy_near_name`: one name inside the other, where the name tables give the two names
  to different drugs and neither is the other processed. A near name is often another drug:
  白附子 and 香附子 are not 附子, and 核桃仁 (walnut) is not 桃仁. But a name such as 茱萸 can
  mean either drug. A processed form still stops: 熟地黄 is 地黄 processed, though the
  materia table files the two apart. Every name found inside a longer word no table knows
  is this kind too, whatever else it is, with a message that says only what the record
  holds: 记录中的“郁金香”含有“郁金”的字样，但“郁金香”不是名表所载的药名，未必是指郁金. A patent
  medicine is not read as its herbs: 六味地黄丸 and 复方甘草片 ask about 地黄 and 甘草, they do
  not stop them.
- `allergy_denied`: the history may deny an allergy to the herb (above).
- `allergy_homonym`: an entry of 花粉 (花粉, 花粉过敏 or 对花粉过敏). In an allergy history
  it means pollen, the most common entry of all. In prescriptions 花粉 is 天花粉's short
  name, and the materia table files it there. So it is a block for 天花粉 only, asking
  which was meant. It does not reach 瓜蒌, though 瓜蒌 comes from the same Trichosanthes.

For each herb a stop stands alone; otherwise there is one block of each kind, listing every
entry that gave it.

**The pack's aliases.** `herb_aliases` in the pack lists names of pack herbs that neither
table holds, each with the source that gives it: 夜交藤 (首乌藤, 《中药学》), and the
Pharmacopoeia 2020's trade names (习称): 川黄柏 (黄柏), 毛山药 and 光山药 (山药), 味连, 雅连 and
云连 (黄连), 盐附子, 黑顺片 and 白附片 (附子), 松贝, 青贝 and 炉贝 (川贝母), 大贝, 珠贝 and 浙贝片
(浙贝母), 连丹皮 and 刮丹皮 (牡丹皮). `herb_sources` records the species of 首乌藤, the one
pack herb the materia table lacks (药典 2020: the stem of the plant whose root is 何首乌).
A name is listed only with its source. The pack refuses an alias with none, one given to
two herbs, and one of a herb it does not hold.

What the scan still misses or over-reads:
- A name in no table is not recognised. 淮山 (山药), 双钩 and 勾藤 (钩藤), 川柏 (黄柏) and 米仁
  (薏苡仁) are common, but no source in hand gives them, so they are not listed. English
  common names (licorice, ginseng) are in no table either; the pinyin and Latin names are.
- A patent medicine is not read as the herbs it holds: 双黄连 asks about 黄连 only, not
  about 金银花, 黄芩 and 连翘, and 参麦注射液 asks nothing. An allergy to Chinese medicines in
  general (中药过敏) names no herb and gives no issue.
- The words around a name decide only between a stop and a block, never between a block and
  silence. A name next to a word the list lacks is read as inside a longer word and asks
  where a stop was meant; 对于术过敏 asks about 白术, because 对于 is "regarding".
- A one-character name (姜, 枣, 归, 桂) inside another word asks too: 沙姜 (Kaempferia) and
  桂圆 (longan) are not 生姜 and 桂枝, but the practitioner says so.
- A denial is read by its clause, not its grammar. 否认青霉素过敏、黄芪过敏 stops 黄芪 (the 过敏
  after 青霉素 closes the denial), and a clause that opens with a denial and names a herb
  later asks, even where the herb was not denied.

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
  - 过敏：不按一张措辞清单（过敏史：X、对X过敏、X过敏史……）读过敏史——清单漏掉的每种写法都会静默放行，而过敏史的写法远多于任何清单（患者对国老过敏、3年前北芪过敏、北芪/人参过敏、变应原：北芪、allergic to huangqi）。每条过敏记录按原样先规范化，再扫描（`bioagent.clinic.allergy`）其中名表所知的每个药名：
    - 规范化：NFKC；去掉零宽空格与连接符、字节顺序标记、软连字符、NUL 等控制字符；繁体字按药材名表自带的字表（`materia.to_simplified`）转为简体，所以黃耆读作黄耆、人參读作人参、當歸读作当归；过敏史里药名前后的常用字（過敏、對、否認、不詳、體質）另按少量字对转换；转为小写。扫描时去掉空白（北 芪即北芪），但空白仍算分界；
    - 扫描：种子知识库的药名、别名及其拼音名和拉丁名（黄耆、北芪、国老、huang qi、Astragali Radix），药材名表的药名（`materia.names_in`，以及每条的全部名称，含单字名：姜、枣），知识包的药名及其注明出处的别名（`herb_aliases`，见下），以及处方药名本身和其中两字以上的部分（浙贝母中的贝母）。拼音名和拉丁名按整词匹配。每个位置取最长的药名，已在其前找到的更长药名中所含的药名（苦杏仁中的杏仁）归该长名；
    - 按所在位置读：药名前后是分界（条目首尾、标点、空格、数字、拉丁字母）、另一个药名（黄耆人参过敏）或过敏史中常写在药名前后的字词（对、患者、自诉、年前、服用、过敏、过敏史、变应原、及、等、均、后、皮疹……）时，药名“独立成词”；否则药名“在更长的词中”：郁金香中的郁金、六味地黄丸中的地黄、双黄连中的黄连、预防风疹疫苗中的防风、蛋白及中的白及。药材名表或知识库把该长词解析为同一药材时，即为该药（甘草片、山药粉、炙北芪）；
    - 否认按“宁可多问”处理：药名所在分句以否认用语（否认、无、没有、未、NKDA、no……）开头，且在药名之前没有“过敏”“史”“但”“出现”或 except 把否认截断（否认黄芪过敏、无黄芪过敏史、否认青霉素、黄芪过敏），或药名后写有否认（黄芪：无、黄芪（-）、黄芪皮试阴性、黄芪不过敏），即为“被否认”。分句在“、”和“/”以外的标点处结束，也在两个汉字之间的空格处结束。不指任何药的否认（无、否认过敏史、过敏史：无、NKDA）不增加任何内容，也不会掩盖其他分句中的药名：食物过敏史：无 药物过敏史：黄芪、无药物过敏史但对黄芪过敏、无过敏史（黄芪过敏除外）都停止黄芪。被否认的药也不会静默放行，而是须说明理由（`allergy_denied`），消息说明该分句写有否认的字样，请医师确认；
    - 扫描不到任何名表所知药名的条目不提示：记为无过敏的（无、否认过敏史、过敏史：无、无药物过敏史、暂无、NKDA、none、N/A、过敏史：阴性或（-）、不详、过敏体质……），以及对非药材过敏的（青霉素、海鲜、头孢、芒果、牛奶、花生、尘螨、碘、小麦、乳胶……）。以否认用字开头的药名（没药、无食子）仍是该药。空白的过敏史（""、空格、[""]、["。。。"]）与 `null` 一样视为未询问，并提示“过敏史未询问”；`[]` 才是询问后无。过敏史、用药与病史须为文字：填入数字或对象会被拒绝，提示与工作室相同（`allergies must be a list of strings or null`）；
    - 每个找到的药名与每味处方药逐一比对。处方药按原名、按知识包别名、按知识库名称比对（处方写黑顺片即附子），名表都不认识的处方药名还按其中所含药名比对（山药颗粒中的山药，按名称相近处理）；
    - 停止：药名独立成词、未被否认，且记录与药名相同（人参过敏史含人参），或一名包含另一名而名表未认定为不同药材（贝母与浙贝母，黄芪与炙黄芪）；或种子知识库认定为同一药材（黄耆、北芪即黄芪，芍药即白芍，国老、炙甘草即甘草），其名称前加炮制词的仍是该药（炙黄耆即黄芪）；或为知识包注明出处的别名（夜交藤即首乌藤）；或二者是同一药材的不同炮制品：已知药名前加炮制词即该药的炮制品，所以生地黄、熟地黄都是地黄，对其一过敏即停止另一；简称经其全称归并（生地即生地黄，熟地即熟地黄）。“鲜”“干”等状态词只在药材名表把两名列于同一条下时去掉（鲜生地即生地，干姜不是姜）；药材名表列在另一药名下的名称不算其所含药名的炮制品（炮姜列在干姜下，不是姜即生姜的炮制品）。消息写出找到的药名，而不是其前后的文字：患者对地黄过敏对熟地黄给出（地黄与熟地黄同为地黄或其炮制品）；
    - 须说明理由：记录可能指该药、但名称无法判定时，医师须记录理由（即消息所说的“请核实”）才能签署，分四类：
      - `allergy_source`：药材名表把该名列在该药条下。该表按来源而非按药归并名称：鸡内金下列有鸡蛋，海藻（马尾藻属）下列有海带（海带属）、昆布，所以消息只说该表把二者列于何条之下（药材名表将海带与海藻同列于“海藻”条下（未必同一物种）），不称为同一药材或同一物种；或二者来自同一物种，物种取自药材名表，名表未收的药取知识包记录的物种：芍药与赤芍（Paeonia lactiflora），何首乌与首乌藤（Polygonum multiflorum，根与藤茎），此时消息写出物种；
      - `allergy_near_name`：一名包含另一名，但名表认定为不同药材、且二者不是炮制关系。名称相近的常是另一种药（白附子、香附子不是附子，核桃仁不是桃仁），但“茱萸”这类名称可能指其中任一种。炮制品仍停止：熟地黄是地黄的炮制品，虽然药材名表把二者分列。在名表不认识的长词中找到的药名，无论属于哪一层，也归此类，消息只说记录中有什么：记录中的“郁金香”含有“郁金”的字样，但“郁金香”不是名表所载的药名，未必是指郁金。中成药不拆解为其药材：六味地黄丸、复方甘草片对地黄、甘草须说明理由，不停止；
      - `allergy_denied`：记录可能否认了对该药过敏（见上）；
      - `allergy_homonym`：记录为“花粉”（花粉、花粉过敏、对花粉过敏）。过敏史中的花粉指植物花粉，是最常见的过敏记录；处方中“花粉”是天花粉的简称，药材名表也列在天花粉下。所以只对天花粉要求说明理由，问清所指；不延及同为栝楼属的瓜蒌；
    - 每味药有“停止”时只列“停止”；否则每类各列一条，写出给出该类的全部记录；
    - 知识包别名：`herb_aliases` 收录两个名表都没有的知识包药材别名，每个注明出处：夜交藤（首乌藤，《中药学》），以及《药典》2020 年版的习称：川黄柏（黄柏），毛山药、光山药（山药），味连、雅连、云连（黄连），盐附子、黑顺片、白附片（附子），松贝、青贝、炉贝（川贝母），大贝、珠贝、浙贝片（浙贝母），连丹皮、刮丹皮（牡丹皮）。`herb_sources` 记录首乌藤的物种——它是药材名表唯一未收的知识包药材（药典 2020：何首乌原植物的干燥藤茎）。只收有出处的名称；没有出处、同一别名给了两味药、或别名所指的药不在知识包中，知识包都拒绝加载；
    - 仍会漏读或多读：名表都没有的名称不识别——淮山（山药）、双钩和勾藤（钩藤）、川柏（黄柏）、米仁（薏苡仁）虽常用，但手头没有给出它们的出处，所以未收；英文俗名（licorice、ginseng）也不在名表中，拼音名和拉丁名在。中成药不拆解为其所含药材：双黄连只就黄连提问，不涉及金银花、黄芩、连翘；参麦注射液不提示；泛称“中药过敏”不指任何药，不提示。药名前后的字词只决定“停止”还是“须说明理由”，不会让药名被静默放过：药名旁边是清单没有的字词时按长词中的药名处理而提问（对于术过敏对白术提问，因为“对于”是介词）。单字药名（姜、枣、归、桂）在其他词中也提问：沙姜、桂圆不是生姜、桂枝，但由医师说明。否认按分句而非语法判断：否认青霉素过敏、黄芪过敏会停止黄芪（青霉素后的“过敏”截断了否认）；以否认开头、后面才写到药名的分句会提问，即使该药并未被否认；
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
