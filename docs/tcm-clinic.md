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
| Allergy | stop |
| Interaction with a recorded medication | warn |
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
- drug interactions;
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
  - 过敏：停止；
  - 中西药相互作用、病证慎用：警示；
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
