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
| Allergy, to the herb by name or under any name the seed knowledge base gives it (黄耆 or 北芪 is 黄芪, 芍药 is 白芍, 国老 is 甘草) | stop |
| Allergy recorded under a name the materia table files with the herb's source (薯蓣 with 山药, but also 鸡蛋 with 鸡内金), or to a drug of the same species (芍药 and 赤芍) | warn |
| Allergy to a near name that the name tables give another drug (白附子 and 附子) | warn |
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

An allergy entry is read as a whole, or as the name in 对黄耆过敏 or 黄耆过敏. It is not
searched for herb names inside it. An entry stops a herb (`allergy`) when:
- it names the herb: the same name, or one name inside the other (人参过敏史 and 人参,
  黄芪 and 炙黄芪);
- or the seed knowledge base gives both names to one herb. Its names are a herb's own and
  its processed forms' (黄耆 and 北芪 are 黄芪, 芍药 is 白芍, 国老 and 炙甘草 are 甘草), and a
  processing word before one of them is the same herb processed (炙黄耆 is 黄芪).

An entry warns (`allergy_source`) when the materia table puts it and the herb under one
source. Either the entry is another name the table files with the herb (薯蓣 with 山药,
双花 with 金银花), or the two drugs come from one species (芍药 and 赤芍 both come from
Paeonia lactiflora). The table covers all but one of the pack's herbs (首乌藤), so an
alias the knowledge base does not hold is no longer missed. But the table groups names by
source, not by drug: its 鸡内金 also lists 鸡蛋, and its 海藻 lists 海带 and 昆布. So the
warning says that the two share a source, asks the practitioner to check, and never says
they are one herb.

One name inside the other does not stop the herb when the name tables give the two names
to different drugs. A near name is often another drug: 白附子 and 香附子 are not 附子, and
核桃仁 (walnut) is not 桃仁. Such a pair warns instead (`allergy_near_name`), because a
name such as 茱萸 can mean either drug. A processed form still stops: 熟地黄 is 地黄
processed, though the materia table files the two apart.

Two false stops remain, because no naming rule can tell them apart:
- the materia table lists 花粉 as a name of 天花粉 (its usual short name in
  prescriptions), so a bare 花粉 entry stops 天花粉. An entry written 花粉过敏 is read as
  the name 花粉 and only warns;
- neither table knows 西红花 or 藏红花 (saffron), so they stop 红花 (safflower) by
  containment.

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
recognised, not checked".

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
  - 过敏：按整条过敏记录识别，“对黄耆过敏”“黄耆过敏”也识别为黄耆，但不在记录中拆字找药名。
    - 停止：记录与药名相同，或一名包含另一名（人参过敏史与人参，黄芪与炙黄芪）；或种子知识库认定为同一药材（黄耆、北芪即黄芪，芍药即白芍，国老、炙甘草即甘草），其名称前加炮制词的仍是该药（炙黄耆即黄芪）；
    - 警示：药材名表（materia）把记录与该药归于同一来源，即记录是该表列在该药名下的另一名称（薯蓣与山药，双花与金银花），或二者来自同一物种（芍药与赤芍，都可来自 Paeonia lactiflora）。该表收录知识包中除首乌藤外的全部药材，但按来源而非按药归并名称（鸡内金下列有鸡蛋，海藻下列有海带、昆布），所以警示只说二者同源（同一物种/来源），请医师核实，不称为同一药材；
    - 一名包含另一名、但名表认定为不同药材时不停止：名称相近的常是另一种药（白附子、香附子不是附子，核桃仁不是桃仁），改为警示，因为“茱萸”这类名称可能指其中任一种。炮制品仍停止：熟地黄是地黄的炮制品，虽然药材名表把二者分列；
    - 仍有两类误停，命名规则无法区分：药材名表把“花粉”列为天花粉的名称（处方常用简称），所以只写“花粉”会停止天花粉，而“花粉过敏”按名称“花粉”识别，只警示；两个名表都未收西红花、藏红花（番红花），所以按包含关系停止红花；
  - 中西药相互作用：警示。西药按类别名（如利尿剂）识别，也按具体药名识别：知识包按 WHO ATC 分类把 35 种药（中英文通用名和常见商品名，如呋塞米、Lasix）对应到规则中的类别，每种附 ATC 编码，并归入其编码所属的全部类别。噻嗪类（氢氯噻嗪，C03AA03）和吲达帕胺（C03BA11）既是利尿剂也是降压药，所以麻黄与氢氯噻嗪同用也会警示；袢利尿剂（呋塞米）只属利尿剂；β 受体阻滞剂（C07）不属于任何已列类别，未列入。类别名和药名都未识别的用药以“提示”列出，说明未核查其相互作用（未提示不等于无相互作用）；
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
