# TCM differentiation and draft prescription · 中医辨证与处方草案

`draft-tcm-prescription` · implementation:
`bioagent.skills.clinic.draft:draft_tcm_prescription` · **candidate, not in the stable
lockfile** · risk `r3_clinical`

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill。

The skill takes a 四诊 intake (`bioagent clinic template`) and, in order:
- screens for red flags and vital signs that stop the consultation;
- scores the knowledge pack's syndrome criteria, and lists the questions that would
  decide between the leading candidates;
- drafts a prescription from the base formula of a syndrome that meets the criteria:
  - doses fitted to age, and capped at the Pharmacopoeia's limits;
  - textbook 加减 suggested;
- checks the draft against 十八反 / 十九畏, pregnancy, allergies, medications and recorded
  conditions.

The draft is not a prescription. A practitioner signs it with `bioagent clinic sign`, and
the skill never does. Its one claim is the textbooks' own, a `traditional_use`: the
findings meet a syndrome's criteria, and 《方剂学》 gives this formula for it.

<!-- zh -->
本 Skill 读取四诊记录（`bioagent clinic template`），依次：
- 筛查红旗征与生命体征；
- 按知识包的证候诊断标准计分，并列出可区分领先候选证的追问；
- 对符合诊断标准的证候，以基础方生成处方草案：
  - 按年龄折算剂量，超过《药典》上限的剂量按上限起草；
  - 提示教材加减；
- 核查十八反/十九畏、妊娠、过敏、合用西药及既往病史。

草案不是处方，须由执业中医师用 `bioagent clinic sign` 签署；本 Skill 从不签署。它唯一的结论是教材本身的说法（`traditional_use`）：所记录的症状符合某证的诊断标准，而《方剂学》为该证给出此方。
