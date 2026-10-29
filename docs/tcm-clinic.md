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
| 四诊 intake | Records each examination separately (望 inspection with the tongue, 闻 listening and smelling, 问 inquiry, 切 palpation with the pulse), as present findings and pertinent negatives, plus vital signs, medications, allergies and conditions. A field that was not asked (`null`) is kept distinct from one asked with nothing found (`[]`). The template lists every finding the criteria score, in the 十问 order. | an unusable intake (no age, a male recorded as pregnant) |
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
| Allergy, to the herb by name, under any name the seed knowledge base gives it (黄耆 or 北芪 is 黄芪, 芍药 is 白芍, 国老 is 甘草), or to another processed form of it (生地黄 and 熟地黄) | stop |
| Allergy that may be to the herb: a name the materia table files with the herb's source (薯蓣 with 山药, but also 鸡蛋 with 鸡内金), a drug of the same species (芍药 and 赤芍), a near name the name tables give another drug (白附子 and 附子), or 花粉 (pollen, and 天花粉's short name) | block |
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

The intake splits an allergy record into entries at 、，,；;。 and line breaks, so
对黄耆、人参过敏 gives the entries 对黄耆 and 人参过敏. Each entry is read for the name it
records. The words a history writes around the name are dropped, with either colon (： or
:) and any spaces:
- 对 or 有 before it, with or without 过敏 after it, and a word of time before that (曾,
  曾经, 既往, 以往): 对黄耆, 有黄耆过敏史, 曾对黄耆过敏, 既往黄耆过敏;
- a label before it: 过敏, 过敏史, 过敏原 or 过敏源, 过敏药物, 过敏物, 过敏反应, and 药物 or
  食物 before 过敏 or 过敏史 (过敏：黄耆, 过敏源：黄耆, 药物过敏史：黄耆). 过敏性 is not a
  label: 过敏性鼻炎 is a condition, read whole;
- 过敏 after it, and what follows: 黄耆过敏, 黄耆过敏史, 黄耆过敏反应, 黄耆过敏（皮疹）,
  对黄耆有过敏史. So 花粉过敏性鼻炎 records 花粉.

No name the tables know starts with one of these words, holds 过敏 or ends in 有. An entry
that records no allergy (无, 否认, 否认过敏史, 过敏史：无, 无药物过敏史, NKDA) or no allergen
(过敏体质, 过敏反应, 过敏史不详) names nothing and matches no herb. A drug whose name starts
with such a word (没药, 无食子) is still that drug. The entry as written is also searched
for the herb's name as a whole (人参过敏史 names 人参), but it is not split into characters.
Names joined by 及, 和 or 与 in one entry are not split: 对人参及黄耆过敏 is read as the
name 人参及黄耆, so it stops 人参, whose own name it holds, but not 黄芪 under its other name
黄耆. Separate the names with 、 or ，.

Names are compared as written, in simplified characters. The repository has no
traditional-to-simplified table, so an entry in traditional characters is recognised only
where a name table lists that form: 芎藭, which the materia table files under 川芎, needs a
reason for 川芎 (`allergy_source`, below). Others, such as 黃耆 (黄芪), 人參 (人参) and 當歸
(当归), are not read as the herb and give no issue: record allergies in simplified
characters.

An entry stops a herb (`allergy`) when:
- it names the herb: the same name, or one name inside the other (人参过敏史 and 人参,
  黄芪 and 炙黄芪);
- or the seed knowledge base gives both names to one herb. Its names are a herb's own and
  its processed forms' (黄耆 and 北芪 are 黄芪, 芍药 is 白芍, 国老 and 炙甘草 are 甘草), and a
  processing word before one of them is the same herb processed (炙黄耆 is 黄芪);
- or the two are processed forms of one drug. A processing word before a known name is
  that drug processed, so 生地黄 and 熟地黄 are both 地黄, and an allergy to either stops
  the other. A short name reaches the other form through its full one: 生地 is 生地黄's
  short name and 熟地 熟地黄's. A word of state (鲜, 干) is dropped only where the materia
  table files both names under one entry (鲜生地 is 生地, but 干姜 is not 姜). A name the
  table files under another drug is not a processed form of the one it seems to hold: 炮姜
  is under 干姜, not under 姜 (生姜).

An entry that may be the herb, where the names cannot tell, is a block. It can be signed
only with the practitioner's recorded reason, which answers the check its message asks
for (请核实). There are three kinds:
- `allergy_source`: the materia table puts the entry and the herb under one source.
  Either the entry is another name the table files with the herb (薯蓣 with 山药, 双花
  with 金银花), or the two drugs come from one species (芍药 and 赤芍 both come from
  Paeonia lactiflora). The table covers all but one of the pack's herbs (首乌藤), so an
  alias the knowledge base does not hold is not missed. But the table groups names by
  source, not by drug: its 鸡内金 also lists 鸡蛋, and its 海藻 lists 海带 and 昆布. So the
  message says that the two share a source and never says they are one herb.
- `allergy_near_name`: one name inside the other, where the name tables give the two
  names to different drugs and neither is the other processed. A near name is often
  another drug: 白附子 and 香附子 are not 附子, and 核桃仁 (walnut) is not 桃仁. But a name
  such as 茱萸 can mean either drug. A processed form still stops: 熟地黄 is 地黄
  processed, though the materia table files the two apart.
- `allergy_homonym`: an entry of 花粉 (花粉, 花粉过敏 or 对花粉过敏). In an allergy history
  it means pollen, the most common entry of all. In prescriptions 花粉 is 天花粉's short
  name, and the materia table files it there. So it is a block for 天花粉 only, asking
  which was meant. It does not reach 瓜蒌, though 瓜蒌 comes from the same Trichosanthes.

One name inside the other still gives false stops. For example:
- neither table knows 西红花 or 藏红花 (saffron), so they stop 红花 (safflower);
- 双黄连 is a patent medicine of 金银花, 黄芩 and 连翘, but it stops 黄连. An allergy to a
  patent medicine is not read as allergies to its herbs, so the three it holds pass;
- a record that denies an allergy to a named herb (否认人参过敏) stops that herb. A denial is
  read only when it names nothing.

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
  - 过敏：过敏记录先在“、，,；;。”和换行处拆为条目（对黄耆、人参过敏拆为“对黄耆”和“人参过敏”），再识别每条所写的药名，去掉名称前后的过敏用语（全角“：”或半角“:”均可，前后空格不计）：名称前的“对”“有”，后面有无“过敏”均可，其前还可有“曾”“曾经”“既往”“以往”（对黄耆、有黄耆过敏史、曾对黄耆过敏、既往黄耆过敏）；名称前的标签：过敏、过敏史、过敏原或过敏源、过敏药物、过敏物、过敏反应，以及前加“药物”“食物”的过敏、过敏史（过敏：黄耆、过敏源：黄耆、药物过敏史：黄耆），“过敏性”不是标签：过敏性鼻炎是病证，按整条识别；名称后的“过敏”及其后的文字（黄耆过敏、黄耆过敏史、黄耆过敏反应、黄耆过敏（皮疹）、对黄耆有过敏史），所以花粉过敏性鼻炎记的是花粉。名表中没有以这些用语开头、含“过敏”或以“有”结尾的药名。记为无过敏的（无、否认、否认过敏史、过敏史：无、无药物过敏史、NKDA）或无过敏原的（过敏体质、过敏反应、过敏史不详）不指任何药，不与任何药材匹配；以这类字开头的药名（没药、无食子）仍是该药。原记录也按完整药名查找（人参过敏史含人参），但不拆字找药名。同一条中用“及”“和”“与”连接的药名不拆分：对人参及黄耆过敏识别为“人参及黄耆”，按完整药名查找停止人参，但不识别黄耆即黄芪，请用“、”或“，”分隔药名。药名按简体字比对：本仓库没有繁简转换表，繁体字记录只有名表收录了该写法时才能识别（药材名表把芎藭列在川芎下，对川芎须说明理由，即下文的 `allergy_source`），其他如黃耆（黄芪）、人參（人参）、當歸（当归）不识别为该药，也不提示，过敏史请用简体字记录。
    - 停止：记录与药名相同，或一名包含另一名（人参过敏史与人参，黄芪与炙黄芪）；或种子知识库认定为同一药材（黄耆、北芪即黄芪，芍药即白芍，国老、炙甘草即甘草），其名称前加炮制词的仍是该药（炙黄耆即黄芪）；或二者是同一药材的不同炮制品：已知药名前加炮制词即该药的炮制品，所以生地黄、熟地黄都是地黄，对其一过敏即停止另一；简称经其全称归并（生地即生地黄，熟地即熟地黄）。“鲜”“干”等状态词只在药材名表把两名列于同一条下时去掉（鲜生地即生地，干姜不是姜）；药材名表列在另一药名下的名称不算其所含药名的炮制品（炮姜列在干姜下，不是姜即生姜的炮制品）；
    - 须说明理由：记录可能指该药、但名称无法判定时，医师须记录理由（即消息所说的“请核实”）才能签署，分三类：
      - `allergy_source`：药材名表（materia）把记录与该药归于同一来源，即记录是该表列在该药名下的另一名称（薯蓣与山药，双花与金银花），或二者来自同一物种（芍药与赤芍，都可来自 Paeonia lactiflora）。该表收录知识包中除首乌藤外的全部药材，但按来源而非按药归并名称（鸡内金下列有鸡蛋，海藻下列有海带、昆布），所以消息只说二者同源（同一物种/来源），不称为同一药材；
      - `allergy_near_name`：一名包含另一名，但名表认定为不同药材、且二者不是炮制关系。名称相近的常是另一种药（白附子、香附子不是附子，核桃仁不是桃仁），但“茱萸”这类名称可能指其中任一种。炮制品仍停止：熟地黄是地黄的炮制品，虽然药材名表把二者分列；
      - `allergy_homonym`：记录为“花粉”（花粉、花粉过敏、对花粉过敏）。过敏史中的花粉指植物花粉，是最常见的过敏记录；处方中“花粉”是天花粉的简称，药材名表也列在天花粉下。所以只对天花粉要求说明理由，问清所指；不延及同为栝楼属的瓜蒌；
    - 按包含关系仍会误停，例如：两个名表都未收西红花、藏红花（番红花），所以停止红花；双黄连是金银花、黄芩、连翘组成的中成药，却停止黄连，而其所含三药不提示（中成药过敏不拆解为其组成药材的过敏）；否认对某药过敏的记录（否认人参过敏）会停止该药：只有不指任何药的记录才识别为否认；
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
