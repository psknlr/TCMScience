"""The base cases of the governance ablation: correct outputs, and the material to break them.

Every base case is a statement an agent might release, with the source it cites, written
so that a careful reviewer would let it through. Each mutation operator in
``ablation.MUTATIONS`` turns a base case into an output with one known scientific error;
the ``material`` of a case supplies the text an operator needs (which words to swap, what
the cited study would say if it had been a mouse study).

**Sources are fixtures.** Except for the one English trial abstract (EMPEROR-Preserved,
PMID 34449189, quoted as the PSH tests quote it), every source text was written for this
benchmark. None of them summarises a real study, including those that name a real herb or
formula, and their identifiers use the DOI prefix ``10.5555``, which is reserved for examples
and resolves to no publication. They exist to be paraphrased correctly and then
incorrectly, not to be cited.
"""

from __future__ import annotations

from typing import Any

__all__ = ["BASE_CASES"]

_EMPEROR = (
    "Empagliflozin reduced the combined risk of cardiovascular death or hospitalization for "
    "heart failure in patients with heart failure and a preserved ejection fraction. The "
    "primary outcome occurred in 415 of 2997 patients in the empagliflozin group and in 511 "
    "of 2991 patients in the placebo group (hazard ratio, 0.79; 95% confidence interval, "
    "0.69 to 0.90; P<0.001).")

BASE_CASES: tuple[dict[str, Any], ...] = (
    # ---------------------------------------------------------------- efficacy, trials
    {
        "id": "E1", "language": "zh", "claim_kind": "efficacy",
        "statement": "葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白（doi:10.5555/tcm-ablation.01）。",
        "citation": "doi:10.5555/tcm-ablation.01",
        "subject": "葛根芩连汤", "population": "成人2型糖尿病患者", "outcome": "糖化血红蛋白",
        "certainty": "strong", "direction": "decrease",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.01", "design": "randomized_trial",
            "subject": "葛根芩连汤", "population": "成人2型糖尿病患者", "outcome": "糖化血红蛋白",
            "text": "目的：评价葛根芩连汤对成人2型糖尿病患者血糖控制的影响。方法：一项纳入360名成人2型"
                    "糖尿病患者的随机双盲安慰剂对照试验，疗程12周。结果：与安慰剂相比，葛根芩连汤降低了"
                    "糖化血红蛋白（差值0.6%）。结论：葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。"},
        "material": {
            "population": ["成人", "儿童"],
            "outcome": ["糖化血红蛋白", "全因死亡率"],
            "subject": ["葛根芩连汤", "黄连解毒汤"],
            "direction": ["降低", "升高"],
            "certainty": ["葛根芩连汤可降低", "已证实葛根芩连汤必然降低"],
            "animal": {
                "population": "2型糖尿病大鼠",
                "text": "目的：评价葛根芩连汤对2型糖尿病大鼠血糖的影响。方法：高脂饮食联合链脲佐菌素诱导的"
                        "2型糖尿病大鼠灌胃给药12周。结果：葛根芩连汤降低了糖化血红蛋白。结论：葛根芩连汤可"
                        "降低2型糖尿病大鼠的糖化血红蛋白。"},
            "docking": {
                "population": "计算预测，无研究对象",
                "text": "分子对接预测葛根芩连汤的成分葛根素与PTP1B结合（结合能-8.1 kcal/mol），据此推测"
                        "葛根芩连汤可降低成人2型糖尿病患者的糖化血红蛋白。"},
            "plan": {"statement": "葛根芩连汤可治愈成人2型糖尿病，未来研究将优化剂量"
                                  "（doi:10.5555/tcm-ablation.01）。",
                     "outcome": "2型糖尿病治愈"},
        },
    },
    {
        "id": "E2", "language": "en", "claim_kind": "efficacy",
        "statement": "Empagliflozin reduced the combined risk of cardiovascular death or "
                     "hospitalization for heart failure in patients with heart failure and a "
                     "preserved ejection fraction (PMID: 34449189).",
        "citation": "PMID: 34449189",
        "subject": "empagliflozin",
        "population": "patients with heart failure and a preserved ejection fraction",
        "outcome": "cardiovascular death or hospitalization for heart failure",
        "certainty": "strong", "direction": "decrease",
        "source": {
            "identifier": "PMID: 34449189", "design": "randomized_trial",
            "subject": "empagliflozin",
            "population": "patients with heart failure and a preserved ejection fraction",
            "outcome": "cardiovascular death or hospitalization for heart failure",
            "text": _EMPEROR},
        "material": {
            "population": ["patients with heart failure", "children with heart failure"],
            "outcome": ["cardiovascular death or hospitalization for heart failure",
                        "all-cause mortality"],
            "subject": ["Empagliflozin", "Dapagliflozin"],
            "direction": ["reduced", "increased"],
            "certainty": ["Empagliflozin reduced", "Empagliflozin always eliminates"],
            "animal": {
                "population": "mice with heart failure and a preserved ejection fraction",
                "text": "In mice with heart failure and a preserved ejection fraction, "
                        "empagliflozin reduced the combined risk of cardiovascular death or "
                        "hospitalization-equivalent decompensation for heart failure."},
            "docking": {
                "population": "in silico",
                "text": "Molecular docking predicted that empagliflozin binds SGLT2, which "
                        "suggests that empagliflozin reduced the combined risk of "
                        "cardiovascular death or hospitalization for heart failure in "
                        "patients with heart failure and a preserved ejection fraction."},
            "plan": {"statement": "Empagliflozin cures heart failure with a preserved ejection "
                                  "fraction; future studies will optimise the dose "
                                  "(PMID: 34449189).",
                     "outcome": "cure of heart failure"},
        },
    },
    {
        "id": "E3", "language": "zh", "claim_kind": "efficacy",
        "statement": "黄芪注射液可降低成人慢性心力衰竭患者的再住院率（doi:10.5555/tcm-ablation.03）。",
        "citation": "doi:10.5555/tcm-ablation.03",
        "subject": "黄芪注射液", "population": "成人慢性心力衰竭患者", "outcome": "再住院率",
        "certainty": "strong", "direction": "decrease",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.03", "design": "randomized_trial",
            "subject": "黄芪注射液", "population": "成人慢性心力衰竭患者", "outcome": "再住院率",
            "text": "目的：评价黄芪注射液对成人慢性心力衰竭患者再住院的影响。方法：一项纳入240名成人慢性"
                    "心力衰竭患者的随机对照试验，随访6个月。结果：黄芪注射液组再住院率为14%，对照组为21%。"
                    "结论：黄芪注射液可降低成人慢性心力衰竭患者的再住院率。"},
        "material": {
            "population": ["成人", "孕妇"],
            "outcome": ["再住院率", "死亡率"],
            "subject": ["黄芪注射液", "丹参注射液"],
            "direction": ["降低", "升高"],
            "certainty": ["黄芪注射液可降低", "已证实黄芪注射液必然降低"],
            "plan": {"statement": "黄芪注射液可治愈成人慢性心力衰竭，未来研究将优化剂量"
                                  "（doi:10.5555/tcm-ablation.03）。",
                     "outcome": "慢性心力衰竭治愈"},
        },
    },
    {
        "id": "E4", "language": "zh", "claim_kind": "efficacy",
        "statement": "制附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离"
                     "（doi:10.5555/tcm-ablation.04）。",
        "citation": "doi:10.5555/tcm-ablation.04",
        "subject": "制附子", "population": "成人慢性心力衰竭患者", "outcome": "6分钟步行距离",
        "certainty": "strong", "direction": "increase",
        "entities": ["制附子"], "dose": "1.5g",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.04", "design": "randomized_trial",
            "subject": "制附子", "population": "成人慢性心力衰竭患者", "outcome": "6分钟步行距离",
            "entities": ["制附子"], "dose": "1.5g",
            "text": "目的：评价制附子对成人慢性心力衰竭患者运动耐量的影响。方法：一项纳入120名成人慢性心力"
                    "衰竭患者的随机对照试验，在常规治疗基础上加用制附子1.5g/日，疗程8周。结果：制附子组6分钟"
                    "步行距离较对照组增加35米。结论：制附子1.5g/日可增加成人慢性心力衰竭患者的6分钟步行距离。"},
        "material": {
            "near_name": ["制附子", "制白附子"],
            "dose": ["1.5g", "15g"],
            "population": ["成人", "儿童"],
            "direction": ["增加", "减少"],
        },
    },
    # ---------------------------------------------------------------- association
    {
        "id": "A1", "language": "en", "claim_kind": "association",
        "statement": "Higher serum C-telopeptide was associated with all-cause mortality in "
                     "adults aged 65 or older (doi:10.5555/tcm-ablation.05).",
        "citation": "doi:10.5555/tcm-ablation.05",
        "subject": "serum C-telopeptide", "population": "adults aged 65 or older",
        "outcome": "all-cause mortality", "certainty": "moderate", "direction": "increase",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.05", "design": "observational",
            "subject": "serum C-telopeptide", "population": "adults aged 65 or older",
            "outcome": "all-cause mortality",
            "text": "In a prospective cohort of 4,812 adults aged 65 or older followed for 8 "
                    "years, higher serum C-telopeptide was associated with all-cause mortality "
                    "(hazard ratio 1.33 per standard deviation)."},
        "material": {
            "population": ["adults aged 65 or older", "children"],
            "outcome": ["all-cause mortality", "hip fracture"],
            "certainty": ["was associated with", "always causes"],
            "upgrade": {"statement": "Lowering serum C-telopeptide reduces all-cause mortality "
                                     "in adults aged 65 or older "
                                     "(doi:10.5555/tcm-ablation.05).",
                        "claim_kind": "efficacy", "certainty": "strong",
                        "direction": "decrease"},
        },
    },
    {
        "id": "A2", "language": "zh", "claim_kind": "association",
        "statement": "长期服用含马兜铃酸的中药与成人慢性肾脏病患者终末期肾病风险升高相关"
                     "（doi:10.5555/tcm-ablation.06）。",
        "citation": "doi:10.5555/tcm-ablation.06",
        "subject": "含马兜铃酸的中药", "population": "成人慢性肾脏病患者", "outcome": "终末期肾病风险",
        "certainty": "moderate", "direction": "increase",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.06", "design": "observational",
            "subject": "含马兜铃酸的中药", "population": "成人慢性肾脏病患者",
            "outcome": "终末期肾病风险",
            "text": "一项纳入2,000名成人慢性肾脏病患者的前瞻性队列研究随访5年，长期服用含马兜铃酸的中药"
                    "与成人慢性肾脏病患者终末期肾病风险升高相关（风险比1.8）。"},
        "material": {
            "population": ["成人", "儿童"],
            "outcome": ["终末期肾病风险", "全因死亡率"],
            "certainty": ["与成人慢性肾脏病患者终末期肾病风险升高相关",
                          "已被证实必然导致成人慢性肾脏病患者终末期肾病风险升高"],
        },
    },
    # ---------------------------------------------------------------- safety signal
    {
        "id": "S1", "language": "zh", "claim_kind": "safety_signal",
        "statement": "服用含何首乌的制剂可能与成人药物性肝损伤有关（doi:10.5555/tcm-ablation.07）。",
        "citation": "doi:10.5555/tcm-ablation.07",
        "subject": "含何首乌的制剂", "population": "成人", "outcome": "药物性肝损伤",
        "certainty": "tentative", "direction": "",
        "entities": ["何首乌"],
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.07", "design": "case_report",
            "subject": "含何首乌的制剂", "population": "成人", "outcome": "药物性肝损伤",
            "entities": ["何首乌"],
            "text": "病例报告：一名52岁成人女性在服用含何首乌的制剂4周后出现药物性肝损伤，丙氨酸氨基转移酶"
                    "升高至正常上限的12倍，停药后恢复。结论：服用含何首乌的制剂可能与成人药物性肝损伤有关。"},
        "material": {
            "near_name": ["何首乌", "白首乌"],
            "population": ["成人", "儿童"],
            "certainty": ["可能与", "已被证实必然与"],
        },
    },
    # ---------------------------------------------------------------- mechanism
    {
        "id": "M1", "language": "en", "claim_kind": "mechanism",
        "statement": "In vitro, compound A may inhibit target T1 (doi:10.5555/tcm-ablation.08).",
        "citation": "doi:10.5555/tcm-ablation.08",
        "subject": "compound A", "population": "", "outcome": "target T1",
        "certainty": "tentative", "direction": "decrease",
        "path": ["bind", "antag_A_T1"], "path_subject": "A", "path_object": "T1",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.08", "design": "in_vitro",
            "subject": "compound A", "population": "", "outcome": "target T1",
            "text": "In vitro, compound A inhibited target T1 in a cell-based reporter assay "
                    "and bound target T1 with a dissociation constant of 50 nM; compound A may "
                    "inhibit target T1."},
        "material": {
            "borrowed_direction": ["bind", "antag_B_T2"],
            "certainty": ["may inhibit", "always completely inhibits"],
            "upgrade": {"statement": "Compound A reduces disease activity in adults with "
                                     "rheumatoid arthritis (doi:10.5555/tcm-ablation.08).",
                        "claim_kind": "efficacy", "certainty": "strong",
                        "population": "adults with rheumatoid arthritis",
                        "outcome": "disease activity", "direction": "decrease"},
        },
    },
    {
        "id": "H1", "language": "en", "claim_kind": "mechanism_hypothesis",
        "statement": "Docking suggests that baicalin may bind PTP1B, a mechanism hypothesis for "
                     "experimental testing (doi:10.5555/tcm-ablation.09).",
        "citation": "doi:10.5555/tcm-ablation.09",
        "subject": "baicalin", "population": "", "outcome": "PTP1B",
        "certainty": "tentative", "direction": "",
        "source": {
            "identifier": "doi:10.5555/tcm-ablation.09", "design": "docking",
            "subject": "baicalin", "population": "", "outcome": "PTP1B",
            "text": "Molecular docking predicted that baicalin may bind the catalytic site of "
                    "PTP1B (estimated binding energy -8.1 kcal/mol), a hypothesis for "
                    "experimental testing."},
        "material": {
            "subject": ["baicalin", "berberine"],
            "certainty": ["may bind", "is proven to bind"],
            "upgrade": {"statement": "Baicalin improves glycaemic control in adults with type 2 "
                                     "diabetes by inhibiting PTP1B "
                                     "(doi:10.5555/tcm-ablation.09).",
                        "claim_kind": "efficacy", "certainty": "strong",
                        "population": "adults with type 2 diabetes",
                        "outcome": "glycaemic control", "direction": "increase"},
        },
    },
    # ---------------------------------------------------------------- classical attribution
    {
        "id": "C1", "language": "zh", "claim_kind": "attribution",
        "statement": "《伤寒论》第34条记载：喘而汗出者，葛根黄芩黄连汤主之（shanghanlun:34）。",
        "citation": "shanghanlun:34",
        "subject": "葛根黄芩黄连汤", "population": "", "outcome": "",
        "certainty": "strong", "direction": "",
        "source": {
            "identifier": "shanghanlun:34", "design": "classical_text",
            "subject": "葛根黄芩黄连汤", "population": "", "outcome": "",
            "text": "《伤寒论》第34条：太阳病，桂枝证，医反下之，利遂不止，脉促者，表未解也；"
                    "喘而汗出者，葛根黄芩黄连汤主之。"},
        "material": {
            "subject": ["葛根黄芩黄连汤", "桂枝汤"],
            "upgrade": {"statement": "葛根黄芩黄连汤可降低成人急性腹泻患者的腹泻持续时间"
                                     "（shanghanlun:34）。",
                        "claim_kind": "efficacy", "certainty": "strong",
                        "population": "成人急性腹泻患者", "outcome": "腹泻持续时间",
                        "direction": "decrease"},
        },
    },
)
