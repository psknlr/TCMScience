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
  conditions. An allergy stops a herb it names, under the seed knowledge base's names for
  it or as another processed form of it; one that only may be the herb (a name the materia
  table files with its source, a near name, 花粉) needs the practitioner's reason. A
  medicine recorded by name is read as its interaction classes by its WHO ATC code
  (docs/tcm-clinic.md).

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
- 核查十八反/十九畏、妊娠、过敏、合用西药及既往病史。过敏记录指明该药（含种子知识库所列名称及该药的其他炮制品）时停止，只是可能指该药（药材名表同列于其来源下的名称、相近名称、“花粉”）时须医师说明理由；按名称记录的西药按其 WHO ATC 编码归入相互作用类别（见 docs/tcm-clinic.md）。

草案不是处方，须由执业中医师用 `bioagent clinic sign` 签署；本 Skill 从不签署。它唯一的结论是教材本身的说法（`traditional_use`）：所记录的症状符合某证的诊断标准，而《方剂学》为该证给出此方。
