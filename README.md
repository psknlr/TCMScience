<div align="center">

# TCMScience: An Autonomous Scientist for Traditional Chinese Medicine

### A governed, evidence-grounded and reproducible AI scientist for TCM & biomedical research

**首个面向中医药与生物医学科学发现的受治理自主科研智能体**

[![ci](https://github.com/psknlr/TCMScience/actions/workflows/ci.yml/badge.svg)](https://github.com/psknlr/TCMScience/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
![PSH](https://img.shields.io/badge/PSH-0.5.3-6f42c1)
![BioScience](https://img.shields.io/badge/BioScience-0.2.6-2ea44f)
![TCM](https://img.shields.io/badge/TCM-native-b31b1b)
![Research](https://img.shields.io/badge/status-research%20prototype-orange)

**Yanlan Kang¹ · Ruiqi Liu² · Xukun Zhang³* · [William Cheng-Chung Chu](https://www.sciopen.com/scholar/info?id=1952658822209773569)⁴***

¹ Institute of Medical Philosophy & Future AI (IMPF-AI)
² Shanghai Medical College, Fudan University
³ Li Ka Shing Faculty of Medicine, The University of Hong Kong
⁴ Fuyao University of Science and Technology

Yanlan Kang and Ruiqi Liu contributed equally.
* **Co-corresponding authors**

</div>

---

## Overview

**TCMScience** is an open research framework for building an **autonomous scientist for Traditional Chinese Medicine (TCM)**: an AI research system that can plan scientific tasks, retrieve and grade evidence, call biomedical and TCM tools, execute analyses, inspect intermediate results, generate testable hypotheses, verify claims, and release only policy-compliant outputs.

The central design choice is simple:

> **The language model may reason, but it does not define the laws of the laboratory.**

In high-stakes biomedical research, an LLM should not be its own planner, executor, security policy, evidence judge and release authority at the same time. TCMScience therefore separates the **reasoning plane** from the **trusted execution plane**. Model-generated plans are typed, bounded and validated. Data are labelled at ingress. Tool/model/delegation calls must cross explicit gateways. Outputs are quarantined before release. Scientific claims are checked against evidence scope and provenance before they can enter trusted project memory.

TCMScience is inspired by the emerging vision of automated scientific discovery [1] and general-purpose biomedical agents [2], but focuses on a problem that requires additional structure: **TCM knowledge is heterogeneous across classical texts, materia medica, formulas, syndrome theory, modern molecular evidence and clinical studies, and these evidence types must not be silently collapsed into one another.**

### 中文简介

**TCMScience** 是首个面向“**自动中医药科学家（Autonomous TCM Scientist）**”智能体Harness能够围绕一个中医药科学问题，自主完成**任务规划 → 文献/数据库检索 → 证据分级 → 工具调用 → 数据分析 → 假说生成 → 反证与核验 → 结果发布 → 可追溯科研记忆**的闭环。此外模型及智能体基于**规则受控科研环境**工作以保证安全可信。

因此，TCMScience 将：

* **智能体的推理能力**与**系统的执行权限**解耦；
* **经典记载、传统应用、临床前机制、病例、观察性研究、RCT、系统评价**分层建模；
* 将药材、炮制品、方剂、君臣佐使、证候、经典条文、安全禁忌等作为**原生中医药对象**，而不是普通文本字符串；
* 将模型输出默认视为**待验证科研对象**，而不是天然可信的结论；
* 对进入系统的数据、离开系统的输出以及跨模型/工具的调用建立**标签、策略、审计与发布门**。

TCMScience 的目标不是“让 AI 代替科学家做判断”，而是构建一个能够进行**可验证、可审计、可复现科学工作的自主研究系统**。

---

## Why TCMScience?

### From a chatbot to a scientist

A scientific agent must do more than answer questions.

| Conversational agent               | TCMScience autonomous scientist                                 |
| ---------------------------------- | --------------------------------------------------------------- |
| Generates an answer                | Builds and executes a research plan                             |
| Retrieves text                     | Retrieves evidence with provenance and scope                    |
| Calls tools opportunistically      | Calls tools through typed, policy-gated capabilities            |
| Mixes evidence in free text        | Separates evidence tiers and claim types                        |
| Produces a final response directly | Quarantines, verifies and releases output                       |
| Treats TCM as unstructured text    | Models herbs, formulas, syndromes, classics and safety natively |
| Keeps conversational memory        | Commits only governed, verified project memory                  |
| Can silently overclaim             | Must expose unsupported/extrapolated claims                     |

### Why governance matters

An autonomous researcher needs a laboratory whose rules cannot be rewritten by the researcher itself.

TCMScience therefore implements a **governed harness** around the scientific agent:

* **Prompt-injection-resistant control plane** — model-authored text does not rewrite kernel policy.
* **Monotonic authority** — a run envelope can be narrowed but cannot be widened beyond the policy that minted it.
* **Data labels at ingress** — data sensitivity travels with the value through the research workflow.
* **Quarantine before release** — generated output is not trusted merely because a model produced it.
* **Evidence-aware memory** — verified claims can enter project memory; rejected claims are not silently promoted into future context.
* **Tamper-evident audit** — execution events are recorded in a hash-chained audit trail.
* **Honest isolation reporting** — the system distinguishes process-level controls from OS-level sandboxing instead of claiming isolation that is not present.

> TCMScience does **not** claim universal immunity to prompt injection, certified de-identification, or a tamper-proof execution environment.

---

## System at a glance

| Layer                              | Capability                                                                                                                          |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Trusted kernel**                 | PSH-Harness `0.5.3`                                                                                                                 |
| **Biomedical capability plane**    | BioScience-Harness `0.2.6`                                                                                                          |
| **Public biomedical sources**      | **58** sources                                                                                                                      |
| **Typed public operations**        | **153** operations                                                                                                                  |
| **Native offline tools**           | **147** tools across **12** domains                                                                                                 |
| **Pinned/fetchable bulk datasets** | **24**                                                                                                                              |
| **Unified capability catalogue**   | **2,567** rows                                                                                                                      |
| **TCM-native tools**               | **8**                                                                                                                               |
| **TCM seed knowledge**             | 23 herbs · 5 processed herbs · 6 formulas · 8 syndromes · 9 classical passages · 2 study records · 14 relations · 14 safety records |

---

## Architecture

```mermaid
flowchart TB
    U["Scientific Question / Research Goal"]

    subgraph AGENT["Autonomous Scientist — reasoning & research loop"]
        PLAN["Typed Planner"]
        LOOP["Bounded Agent Loop"]
        CTX["Context + Governed Memory"]
        SUP["Supervisor / Worker Pool"]
    end

    subgraph PSH["PSH-Harness — Trusted Kernel"]
        ING["Ingress Classification"]
        AUTH["Authority + Policy Lattices"]
        BROKER["ExecutionBroker"]
        GATE["Model · Tool · Delegation Gateways"]
        Q["Quarantine + Output Gate"]
        AUDIT["Hash-chained Audit + Persistence"]
    end

    subgraph BIO["BioScience-Harness — Capability Plane"]
        SRC["58 public sources / 153 operations"]
        TOOL["147 native tools / 12 domains"]
        DATA["24 bulk datasets"]
        CAT["2,567-row catalogue"]
    end

    subgraph TCM["bioagent.tcm — TCM-native layer"]
        HERB["Herbs / 炮制"]
        FORM["Formulas / 君臣佐使"]
        SYN["Syndromes / 证候"]
        CLASSIC["Classical passages"]
        EVID["Evidence tiers"]
        SAFE["Safety / 十八反"]
    end

    U --> ING --> AUTH --> PLAN
    PLAN --> LOOP
    CTX --> LOOP
    SUP --> LOOP
    LOOP --> BROKER --> GATE
    GATE --> SRC
    GATE --> TOOL
    GATE --> DATA
    CAT --> GATE
    TOOL --> TCM
    LOOP --> Q --> AUDIT
```

---

## Evidence-aware TCM reasoning

| Rank | Evidence tier       | 中文          | Claim type      |
| ---: | ------------------- | ----------- | --------------- |
|    1 | `CLASSICAL_TEXT`    | 经典文献记载      | attribution     |
|    2 | `EXPERT_EXPERIENCE` | 名医经验 / 专家共识 | traditional use |
|    3 | `PRECLINICAL`       | 临床前研究       | mechanism       |
|    4 | `CASE_REPORT`       | 病例报告 / 病例系列 | safety signal   |
|    5 | `OBSERVATIONAL`     | 观察性研究       | association     |
|    6 | `RANDOMIZED_TRIAL`  | 随机对照试验      | efficacy        |
|    7 | `SYSTEMATIC_REVIEW` | 系统评价 / 荟萃分析 | recommendation  |

A classical text can be the primary provenance for a **classical attribution**, while remaining insufficient evidence for a modern **clinical efficacy** claim.

---

## Citation

```bibtex
@software{kang2026tcmscience,
  title        = {TCMScience: An Autonomous Scientist for Traditional Chinese Medicine},
  author       = {Kang, Yanlan and Liu, Ruiqi and Zhang, Xukun and Chu, William Cheng-Chung},
  year         = {2026},
  url          = {https://github.com/psknlr/TCMScience},
  note         = {Open-source research software}
}
```

### Authors

**Yanlan Kang (康砚澜)**
Institute of Medical Philosophy & Future AI

**Ruiqi Liu (刘瑞琦)**
Shanghai Medical College, Fudan University

**Xukun Zhang (张绪坤)***
Li Ka Shing Faculty of Medicine, The University of Hong Kong

**[William Cheng-Chung Chu (朱正忠)](https://www.sciopen.com/scholar/info?id=1952658822209773569)***
Fuyao University of Science and Technology

* **Co-corresponding authors / 共同通讯作者**

---

## References

1. Lu C, Lu C, Lange RT, Foerster J, Clune J, Ha D. **The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery.** arXiv:2408.06292 (2024).
2. Huang K, Zhang S, Wang H, et al. **Autonomous biomedical research with an artificial intelligence agent.** *Science*. 2026;393(6813):eadz4351. doi:10.1126/science.adz4351.
3. Peat G, Jones W, Nuhn M, et al. **Open Targets Platform: facilitating therapeutic hypotheses building in drug discovery.** *Nucleic Acids Research*. 2025;53(D1):D1467–D1475. doi:10.1093/nar/gkae1128.
4. Zhao H, Yang Y, Wang S, et al. **NPASS database update 2023: quantitative natural product activity and species source database for biomedical research.** *Nucleic Acids Research*. 2023;51(D1):D621–D628. doi:10.1093/nar/gkac1069.
5. Rutz A, Sorokina M, Galgonek J, et al. **The LOTUS initiative for open knowledge management in natural products research.** *eLife*. 2022;11:e70780. doi:10.7554/eLife.70780.

---

<div align="center">

### TCMScience

**From classical knowledge to testable evidence. From AI agents to an autonomous TCM scientist.**

**从经典知识到可验证证据，从科研智能体到自动中医药科学家。**

</div>
