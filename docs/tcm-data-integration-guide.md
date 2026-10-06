# TCMScience 中医药数据源接入指南

核对日期：2026-10-03（第 11 节为 2026-10-04 实施记录）。本文档位于 `docs/tcm-data-integration-guide.md`。代码基准：[df0cd3e](https://github.com/psknlr/TCMScience/tree/df0cd3e37c254c65dc39db6703955bac0e3418ad)。以下命令从仓库根目录执行，除非明确说明切换目录。

建议第一期做“HKBU 方剂组成 + 港标质量标准”，先完成许可范围内的数据整理、本地查询和来源追溯；再做证候、古籍与临床试验数据。不要把每个新库都包装成药材—靶点数据库。

本指南的适配器、只读查询函数和证据快照示例已用合成数据验证。指南内的 Python 代码块均通过语法检查（2026-10-05 修订后为 7 个）；只读工具注册与发现已验证。**没有下载真实 HKBU 数据，没有验证任何未公开 API**。示例中的药材、方剂和证候均为虚构。第一期实际实现见第 11 节：仅合成数据验证，推送到 fork 分支，未写入上游 `main`。第 11 节末尾记录了 2026-10-05 根据独立复核所做的修订（严格读取人工模板、构建失败不影响原库、数据许可随记录传递、隔离执行读同一个库、炮制品不在共识中合并、查询状态细分）；下文各节已按修订后的行为更新。

## 1. 先理解项目的四个接入层次

| 层次 | 完成什么 | 实际入口 |
|---|---|---|
| 来源登记 | 用户可以在 `tcmdb sources` 中发现资源 | `BioScience-Harness/src/bioagent/data/tcm_source_catalog.json` |
| 数据接入 | 本地文件入 SQLite，可查原始表和统一关系 | `BioScience-Harness/src/bioagent/tcmdb/extra/` |
| Agent 工具 | Agent 能调用受约束的查询函数 | `BioScience-Harness/src/bioagent/tools/` 和其 `__init__.py` |
| 科研证据 | 数据转成节点/边快照，结论通过证据审核 | `BioScience-Harness/src/bioagent/sources/` |

只添加目录条目，不会下载数据。只完成 SQLite 入库，不会自动更新原有 `tools/tcm.py` 的中医知识工具，也不会自动进入科研证据流程。这个区别决定了需要改哪些文件。

## 2. 建立环境，先检查已有资源

需要 Python 3.11 或更高版本。

```bash
git clone https://github.com/psknlr/TCMScience.git
cd TCMScience
git checkout df0cd3e37c254c65dc39db6703955bac0e3418ad
python -m venv .venv
source .venv/bin/activate
python -m pip install -e "PSH-Harness[test]"
python -m pip install -e "BioScience-Harness[dev]"
export BIOAGENT_DATA_LAKE="$PWD/data/lake"      # Hub 默认在 $BIOAGENT_DATA_LAKE/tcmdb
python -m bioagent.cli tcmdb sources --name HERB --json
python -m bioagent.cli tcmdb status
```

上面的固定版本用于复现本指南；日常开发可以使用项目当前版本，但需要重新核对接口和来源编号。

Hub 的位置：不设 `BIOAGENT_TCMDB` 时，Hub 在 `<数据湖>/tcmdb`。Agent 经受治理的运行时（包括 PSH 隔离子进程）调用查询工具时，只能读取权限配置许可的根目录（数据湖与工作区）之内的文件；`<数据湖>/tcmdb` 总在其中。若另设 `BIOAGENT_TCMDB`，它也必须位于数据湖或工作区之内，否则调用会被拒绝，拒绝原因里给出实际路径（不会被当作“未载入”）。

HERB、SymMap、ITCM、TCMBank 等已有接入定义，应优先复用。以已有的 `herb2` 数据集为例，在核对其当期许可和下载情况后执行：

```bash
python -m bioagent.cli tcmdb fetch herb2
python -m bioagent.cli tcmdb build herb2
python -m bioagent.cli tcmdb check herb2
python -m bioagent.cli tcmdb relations herb_ingredient --source herb2 --subject 黄芪
```

这些是项目真实命令；实际下载能否成功取决于上游服务与文件。`access=restricted`、`unreachable` 或仅有 catalog 条目，不代表已经实现可用接入。对于 manual 数据集，手动放置已获准使用的文件后直接 `build`；`fetch` 不负责登录或替你导出。

## 3. 各数据库具体选择什么接入路线

下表中的 dataset key 是建议新增的名称，除明确说明外，不是仓库现有命令可直接调用的名称。

| 资源 | 第一阶段获取方式 | 建议文件 / key | 第一阶段能力 |
|---|---|---|---|
| HKBU 中药方剂、药材、植物等图像数据库 | 获准的资料导出或人工整理；没有确认开放 API，不假定网页有批量导出按钮 | `formula_herb.tsv`；`hkbu_formulas_manual` | 方剂组成、用量、药材基原和文献出处查询 |
| 香港中药材标准 HKCMMS | 官方提供的标准文件；核对使用及再分发条件后解析 | 原始 PDF + `quality_standards.tsv`；`hkcmms_manual` | 鉴别方法、含量限度、检查项、版次和页码查询 |
| 香港中药材参考 DNA 序列库 | 官方参考序列及配套说明，必要时申请 | FASTA + `specimen_metadata.tsv`；`hk_cmm_dna_manual` | 基原鉴别、序列与凭证标本关联 |
| TCMSSD | 根据原论文联系维护方，申请版本化导出和字段说明 | `syndromes.tsv`、`syndrome_symptom.tsv`、关系来源表；`tcmssd_manual` | 证候词表与证候—症状映射 |
| 国家中医药古籍数字图书馆 | 授权文本、图像或合作导出；未确认公共 API | 原图/PDF + `passages.jsonl`；`tcm_classics_manual` | 古籍检索、原文定位、经审核的出处关系 |
| WHO ICTRP / ChiCTR | 优先使用官方搜索结果导出；规模化服务另行申请 | 原始 XML/CSV + `trials.tsv`；`ictrp_tcm_manual` | 找到相关注册试验及其注册信息 |
| 中国药典 2025 年版 | 获授权的电子内容或许可数据库服务 | 原文 + `monographs.tsv`；`chp2025_manual` | 品种、炮制、质量检查、版本化条款检索 |
| NGDC 中医药相关组学资源 | 按具体项目下载或申请；涉及临床数据时保留其访问范围 | 矩阵/VCF 等原始格式 + `samples.tsv` | 特定证候/研究的数据分析；不是一个统一 API 的简单映射 |
| HerbComb、GNDC | 先核实可下载数据、稳定接口和使用条款 | 确认格式后独立注册 | 方剂组合分析或天然产物补充；没有确认的接口不列为已接入 |

WHO 目前说明搜索结果可导出 XML；其 XML Web Service 需要联系、确认条件及费用，Crawling Service 页面标为不可用。因此第一期选择导出文件接入，不能默认调用免费的匿名 API。

HKCMMS 的标准覆盖名称、来源、鉴别、检查及含量测定等内容，适合做质量检索。TCMSSD 论文描述的数据包含自动化知识图谱与证候预测，导入时要区分原始文献事实、算法抽取和预测结果。

## 4. 完整示例：接入 HKBU 方剂组成的人工整理表

### 4.1 先定义表结构

这个例子定义本项目的中间表格式，不声称它是 HKBU 官方导出格式。若维护方给出 Excel 或 CSV，另写转换程序，将其转换成下面的 TSV，并保留上游原文件。

一行表示“特定版本方剂中的一味药”，文件为 UTF-8，列名固定：

```text
formula_id, formula_name, formula_version, herb_id, herb_name,
dose, dose_unit, processing, preparation, reference,
source_url, locator, source_row_id, review_status
```

实际文件以制表符分隔，不是上面显示的逗号。

| 字段 | 填写要求 |
|---|---|
| `formula_id` | 稳定 ID；涉及不同出处或版本的方剂，应区分 ID，不能只按名称合并 |
| `herb_id` | 上游 ID 或人工维护的稳定本地 ID；不确定药材身份时留空进入待消歧队列 |
| `formula_version` | 原书、加减方、版本等可区分信息 |
| `processing` / `preparation` | 炮制和制备方式；例如生品/炮制品不能默认为同一实体 |
| `dose` / `dose_unit` | 原始数值和单位；历史单位另建有出处的换算记录 |
| `reference` / `source_url` / `locator` | 文献或页面来源，以及页码、条目号等定位信息 |
| `source_row_id` | 稳定的原记录 ID；不能使用会因排序变化而改变的行号作为唯一身份 |
| `review_status` | 审核完成填 `verified`；其余填 `pending` |

额外保留繁简名称、别名和原始字段；归一化字段用于检索，不能覆盖原文。一个药材名可能有多个基原，药材、植物物种、药用部位和炮制品要分别表达。

### 4.2 新建适配器模块

新建 `BioScience-Harness/src/bioagent/tcmdb/extra/traditional_manual.py`：

```python
"""Manual-export example; does not download or authenticate to any website."""

import json

from ..rowkit import ctx, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec

KEY = "hkbu_formulas_manual"

DATASETS = (
    DatasetSpec(
        key=KEY,
        name="HKBU formulas (reviewed manual export)",
        catalog=(134,),
        homepage="https://library.hkbu.edu.hk/electronic/libdbs/cmfid/index.html",
        license="not stated; verify applicable data permissions before production use",
        files=(FileSpec("", "formula_herb.tsv", "formula_herb", fmt="tsv"),),
        access="manual",
        version="manual-export-v1",
        instructions="Place the permitted, reviewed export in raw/hkbu_formulas_manual/.",
        relations=("formula_herb",),
        notes="Composition as listed by the source; not evidence of efficacy.",
        commercial_use="unknown",
    ),
)


def extract(conn):
    for r in rows(conn, "SELECT * FROM formula_herb"):
        fid = v(r["formula_id"])
        hid = v(r["herb_id"])
        rid = v(r["source_row_id"])
        reference = v(r["reference"])
        verified = v(r["review_status"]) == "verified"
        subject_id = f"{KEY}:formula.{fid}" if fid else None
        if not fid or not hid or not rid or not reference or not verified:
            yield unresolved(
                "formula_herb", KEY, subject_id, r["formula_name"], r["herb_name"],
                "missing stable id/reference or not manually verified",
                reference=reference,
                note=json.dumps(dict(r), ensure_ascii=False, sort_keys=True),
            )
            continue
        qualifiers = {
            k: r[k] for k in (
                "formula_version", "processing", "preparation", "source_url", "locator"
            ) if v(r[k]) is not None
        }
        yield rel(
            "formula_herb", KEY,
            subject_id, r["formula_name"],
            f"{KEY}:herb.{hid}", r["herb_name"],
            "listed", reference=reference,
            note=json.dumps(qualifiers, ensure_ascii=False, sort_keys=True),
            context=ctx(
                dose=v(r["dose"]), dose_unit=v(r["dose_unit"]),
                source_db="HKBU", source_id=rid,
            ),
        )


EXTRACTORS = {KEY: extract}
```

这个模块完成三件事：声明本地文件、加载表后抽取 `formula_herb` 关系、把未审核或缺少关键标识的行送到 `unresolved` 队列。`listed` 只表示来源列出了组成。

`license` 的未知值是为了如实描述目前尚未确认的许可。真实数据投入使用前，应填入已确认的授权内容；不要因为代码能运行就把它改成 CC BY 或 MIT。

### 4.3 注册模块和来源目录

在 `BioScience-Harness/src/bioagent/tcmdb/extra/__init__.py` 的 `MODULES` 元组中加入 `"traditional_manual"`，保留全部原有模块。例如在元组开头插入这一项。该模块仅导入 `spec`、`rowkit` 等底层文件，避免从 `datasets` 或 `relations` 导入造成循环依赖。

然后在仓库根目录运行下面的目录登记脚本。此版本下一编号是 134；换版本时先核对编号，并同步修改适配器中的 `catalog=(134,)`。

```python
import json
from pathlib import Path

path = Path("BioScience-Harness/src/bioagent/data/tcm_source_catalog.json")
catalog = json.loads(path.read_text(encoding="utf-8"))
assert not any(e["no"] == 134 for e in catalog["entries"]), "编号 134 已被使用"
assert not any(e.get("dataset") == "hkbu_formulas_manual" for e in catalog["entries"])
catalog["entries"].append({
    "no": 134,
    "name": "HKBU formulas (manual integration template)",
    "modules": [],
    "url": "https://library.hkbu.edu.hk/electronic/libdbs/cmfid/index.html",
    "access": "manual_import",
    "connector": None,
    "dataset": "hkbu_formulas_manual",
    "license": "not stated; verify applicable data permissions before production use",
    "barriers": "Obtain permitted data and review it before import.",
    "assessment": "Adapter example verified with synthetic records; no real HKBU dataset downloaded.",
    "checked": "2026-10-03",
    "origin": "manual_integration",
    "commercial_use": "unknown",
})
path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

`modules=[]` 保持不假定业务模块归属；需要时根据项目 M1–M14 定义填写。日后更新 `checked`、`assessment` 时，应记录真正核查的情况。

### 4.4 用合成数据验证接入

先使用独立演示目录，避免混入实际数据：

```bash
export BIOAGENT_TCMDB="$BIOAGENT_DATA_LAKE/tcmdb-demo"   # 在数据湖之内，受治理的调用也能读
```

以下 Python 代码在仓库根目录执行，生成一条可接入关系和一条待消歧记录。示例中的 `verified` 只用于演示准入规则，名称是明显虚构的；真实模板一律从 `pending` 开始，由人工核对后才改为 `verified`。也可以直接生成模板：`python -m bioagent.cli tcmdb template hkbu_formulas_manual --out <目录>` 只写表头；加 `--demo` 写入全部为 `pending` 的虚构示例（构建后不产生任何关系，全部进入待审队列）。

```python
import csv
import os
from pathlib import Path

raw = Path(os.environ["BIOAGENT_TCMDB"]) / "raw" / "hkbu_formulas_manual"
raw.mkdir(parents=True, exist_ok=True)
fields = "formula_id formula_name formula_version herb_id herb_name dose dose_unit processing preparation reference source_url locator source_row_id review_status".split()
good = dict.fromkeys(fields, "")
good.update(
    formula_id="demo-v1", formula_name="演示方剂（非真实）", formula_version="demo-v1",
    herb_id="demo-herb", herb_name="演示药材（非真实）", dose="1", dose_unit="g",
    reference="Synthetic example only", source_url="https://example.invalid/demo",
    locator="record 1", source_row_id="demo-row-1", review_status="verified",
)
pending = dict(good, source_row_id="demo-row-2", herb_id="",
               herb_name="待消歧演示药材", review_status="pending")
with (raw / "formula_herb.tsv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
    writer.writeheader()
    writer.writerows([good, pending])
```

运行：

```bash
python -m bioagent.cli tcmdb sources --name HKBU
python -m bioagent.cli tcmdb build hkbu_formulas_manual
python -m bioagent.cli tcmdb check hkbu_formulas_manual
python -m bioagent.cli tcmdb query hkbu_formulas_manual formula_herb --where 'formula_name=演示方剂（非真实）'
python -m bioagent.cli tcmdb relations formula_herb --source hkbu_formulas_manual --subject '演示方剂（非真实）'
```

预期：原始表 2 行，统一关系 1 条，`unresolved=1`；`check` 的 `ok=true`，同时提示许可未知。原始表查询保留待审核行；统一关系查询只输出已审核行。

`check` 校验结构、重建一致性和字段词表等。`ok=true` 不等于获得数据使用许可，也不等于中医药事实已经被医学验证。这里的关系默认 `outcome=positive` 表示记录支持这条“组成关系”，不是临床试验的阳性结果。

Python 查询接口注意使用 `sources`，CLI 对应的选项是 `--source`：

```python
from bioagent.tcmdb.hub import TCMDataHub

hub = TCMDataHub()
records = hub.relations(
    "formula_herb", sources=["hkbu_formulas_manual"],
    subject="演示方剂（非真实）", limit=20,
)
print(records)
```

真实接入时，取消演示用的 `BIOAGENT_TCMDB`（或指向数据湖内的生产目录），放入已获准使用的真实 TSV，重新构建、核查和抽样审核。合成数据应一直保留在演示目录。放好文件后可用 `python -m bioagent.cli tcmdb verify` 一次完成构建、核查和一次工具查询：全部通过退出码为 0，有失败为 1，一个数据集都没有检查到时输出 `NO DATASETS CHECKED` 并以 3 退出（不会被误读为“全部通过”）。

**人工模板的读取规则（2026-10-05）**：三个模板文件按模板严格读取——列名必须正好是模板的列（顺序不限、不重复）；每行字段数必须与表头相同；值里含制表符、换行或以引号开头时必须加引号（`csv.writer(delimiter="\t")` 写出的文件自动满足）；文件必须是 UTF-8。不符合时构建失败并指出文件和行号，原来的库保持不变。项目共用的宽松 `tsv` 读取器为了兼容某些上游导出，会把字段少的行与下一行拼接、把多出的字段截掉，用于人工模板会把一个多余的制表符变成整行错位，所以人工模板不使用它。

## 5. 怎样让 Agent 使用新库

查询工具在 `BioScience-Harness/src/bioagent/tools/tcmdb.py`（`hkbu_formula_lookup`、`hkcmms_standard_lookup`、`hk_cmm_dna_lookup`）。读取本地数据的只读工具应满足以下几点，新增同类工具时照此实现：

| 要求 | 第一期工具的做法 |
|---|---|
| 状态分清 | `not_loaded`（工具查找的位置没有库，`store` 给出路径）、`incomplete_dataset`（缺表、缺列或缺文件，如只有 FASTA 没有标本表）、`no_matching_record`（库完整但无匹配）、`withheld`（有匹配，但没有经人工核对的行，或许可不允许本次用途；`withheld` 给出各原因的条数）、`ok`。任何一种都不能表述为药物无效或不安全 |
| 数据许可随记录 | 每条记录带 `source_dataset`、`license`（该文件构建时的许可）、`licence_class`、`commercial_use`；回答里另有 `dataset_licence` |
| 待审记录 | 港标、DNA 默认只返回 `review_status=verified` 的行；`include_pending=True` 才显示其余行，且保留其审核状态。HKBU 未核对的行本就不进入关系表，回答中以 `unreviewed_rows` 计数 |
| 数据用途 | `commercial=True` 只返回许可允许商用的记录；运营方可设 `BIOAGENT_DATA_USE=commercial` 作为下限，调用只能收紧不能放宽，无法识别的值按商用处理 |
| 声明读取与许可 | 在 `TOOLS` 注册时写明 `reads=("${tcmdb}",)` 和 `data=(<dataset key>,)`。生成的 manifest 因此声明 `filesystem_read` 与 `license.data`，权限配置据此裁决读取路径，PSH 桥接把数据许可报告为 `data_license`、把包装代码的 MIT 报告为 `code_license` |

注册示例（`tools/__init__.py` 的 `TOOLS` 元组中的一项）：

```python
_t("hkbu_formula_lookup", tcmdb.hkbu_formula_lookup, "tcm-knowledge",
   {"formula": "演示方剂（非真实）", "limit": 20}, "tcm", "provenance",
   reads=("${tcmdb}",), data=("hkbu_formulas_manual",)),
```

注册后组件 ID 是 `native.tool.hkbu_formula_lookup`，由 `NativeToolProvider` 发现。相关 skill 的 manifest 还需要声明实际使用的工具和来源范围，再按项目现有运行、权限与审核流程启用。

工具只读取已构建的数据，不接收任意 SQL 或数据根目录参数。Hub 的位置由部署端固定（见第 2 节）；PSH 隔离执行时，子进程会收到与父进程相同的 Hub 路径，与直接调用读取同一个库。组件被发现也不代表已在所有 Agent 工作流中启用。

## 6. 科研分析还要接入证据快照

如果只做方剂组成和标准检索，先完成前述步骤即可。若要用新数据生成机制、关联或疗效结论，还需要专门的解析器，把数据转换为 `sources/schema.py` 支持的节点和边。

| 项目 | 最少字段 / 要求 |
|---|---|
| 节点 | `id, category, name, source`；ID 使用稳定 CURIE，如 `hkbu:formula.<id>` |
| 边 | `subject, predicate, object, primary_knowledge_source, knowledge_level, agent_type, study_design, license, source_record_id` |
| 文献和方法 | 保留原文/页面定位；实验和临床研究需实际 PMID/DOI 及方法，不能从数据库总介绍推断 |
| 原文件与解析器 | `raw_files` 指向真实文件；`parser` 固定实际解析代码；代码或输入变化时生成新版本 |
| 快照和审计 | `build_snapshot` 后，用 `load_snapshot(..., ledger=...)` 重新验证，再使用审核后的快照 |
| 结论 | 用 `CandidateClaim` 标注具体结论类型、对象、来源记录；经 `check_release` 审核 |

映射时遵循以下规则：

- 方剂—药材组成可映射 `contains`，但不设置植物化学的 C1–C4 检出等级；定义性的组成边本身不支持疗效结论。
- 古籍记载使用 `classical_text`；教材或专家来源按实际情况记录 `expert_consensus`。不能把数据库展示的一条记载直接写成 `randomized_trial`。
- 模型生成的证候预测、靶点预测和对接结果使用 `knowledge_level=prediction`，并配套适当的预测型 `study_design`。
- 药材含某成分时区分物种报告、药用部位、炮制品/煎液、体内检出，分别对应 C1、C2、C3、C4。没有证据不能升格。
- 试验注册信息存成注册记录；即使计划为随机试验，也不能据此制造具有疗效结果的 `randomized_trial` 边。需要进一步找到真实结果论文并解析。
- 科研快照 schema（`sources/schema.py`）已有 `organism`、`processed_herb` 节点类别和 `has_base_species` 关系，并已由 `sources/herbs.py` 实际生成；药材—基原物种关系可以直接复用这一层。Hub 的关系词表（`tcmdb.rowkit`）则没有药材—物种关系，两套词表不要混用。
- 标准条款、DNA 序列对象、临床样本等不是当前科学节点类别；先保留原始文件/元数据，确需进入图谱时再设计并验证 schema 扩展。

### HKBU 方剂组成进入药材层

审核过的 HKBU 导出（`raw/hkbu_formulas_manual/formula_herb.tsv`）可以直接进入药材层快照，由网络药理 skill 研究，不必另写解析器。`sources/hkbu.py` 用 Hub 的同一个严格读取器读这份文件，把每首能完整研究的方剂转成药材层的 `FormulaVersion`，与方剂表（`中医方剂数据表.xlsx`）的方剂走同一条路：

```bash
python BioScience-Harness/scripts/build_source_snapshots.py gold \
    --raw RAW --out WORK/snapshots --ledger WORK/audit/snapshots.jsonl \
    --hkbu-formulas "$BIOAGENT_DATA_LAKE/tcmdb/raw/hkbu_formulas_manual/formula_herb.tsv"
python BioScience-Harness/scripts/run_network_pharmacology.py --snapshots WORK/snapshots \
    --ledger WORK/audit/snapshots.jsonl --out WORK/run --formula hkbu:formula.<formula_id>
```

`--formula hkbu:formula.<id>` 默认从 `<Hub>/raw/hkbu_formulas_manual/formula_herb.tsv` 读取，也可用 `--hkbu-formulas` 指定。规则如下：

- **整首方剂才导出。** 一首方剂的每一行都必须满足：`verified`；有 `herb_id`、`source_row_id`、`reference`；各行的方名、版本、出处一致；`source_row_id` 不重复；药名能解析为 `sources.materia` 中的一味药材，且同一药材不重复出现。有一行不满足，整首方剂就不导出——只研究审核过的那几味，等于研究另一首方剂。构建时在 `excluded` 中列出原因；运行时选中这首方剂会被拒绝，并给出同样的原因。
- **繁体药名。** 黃芩、大棗、乾薑等按原文解析不出时，逐字转为简体再解析一次，转换后必须正好是已知药名。字表取自 OpenCC，只限药名用到的字（见 `NOTICE`）。
- **组成边可追溯。** 方剂 ID 为 `hkbu:formula.<formula_id>`；每条组成边的主要知识来源是 `hkbu_formulas_manual`，记录号是 `hkbu_formulas_manual:<source_row_id>`；原文药名、剂量（原数值加单位）和炮制保留在边的 `raw` 中；许可为 `LicenseRef-hkbu-formulas-unstated`。组成边是定义性的，本身不支持任何结论；放行的机制假说，其支撑路径从这条记录开始。
- **生品与炮制品。** 同一首方剂里同时有一味药材的生品和炮制品（生甘草与炙甘草）时，整首不导出：药材层每味药材只有一条组成边。
- **商用运行。** `--purpose commercial` 要求被研究方剂的组成记录本身允许商用。HKBU 和方剂表的许可都没有声明，因此都会被拒绝；手工核对的葛根芩连汤（CC0）不受影响。
- **改动后须重建。** 方剂在快照构建之后被改动（指纹与快照记录的不同）时，运行会被拒绝，需先重建药材层。
- **尚未覆盖。** 研究闭环（`research.loop`）的问题解析还不识别 `hkbu:formula` ID；目前通过 `run_network_pharmacology.py --formula` 或 `run_skill(formula=...)` 使用。

下面是完整、可独立运行的“快照构建 → 校验加载 → 结论审核”演示。将其保存为仓库根目录的 `snapshot_demo.py` 后执行 `python snapshot_demo.py`。合成数据由本例声明 CC0，仅适用于这个演示，不适用于 HKBU、药典等上游数据。

```python
"""Synthetic evidence only: verifies snapshot loading and the release gate."""
import json
import os
from pathlib import Path

from bioagent.sources.ledger import SnapshotLedger
from bioagent.sources.release import CandidateClaim, check_release
from bioagent.sources.snapshot import build_snapshot, load_snapshot

def main():
    root = Path(os.environ.get("TCM_SNAPSHOT_DEMO_ROOT", "data/tcm-evidence-demo"))
    key, version = "integration_demo", "v1"
    nodes = [
        {"id": "demo:herb", "category": "herb", "name": "演示药材（非真实）", "source": key},
        {"id": "demo:syndrome", "category": "syndrome", "name": "演示证候（非真实）", "source": key},
    ]
    edges = [{
        "subject": "demo:herb", "predicate": "indicated_for", "object": "demo:syndrome",
        "primary_knowledge_source": "demo:source",
        "knowledge_level": "knowledge_assertion", "agent_type": "manual_agent",
        "study_design": "classical_text", "license": "CC0-1.0",
        "source_record_id": "demo-row-1",
    }]
    raw = root / "raw" / "synthetic.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    ledger = SnapshotLedger(root / "audit" / "snapshots.jsonl")
    built = build_snapshot(
        key=key, version=version, nodes=nodes, edges=edges,
        raw_files={"synthetic.json": raw}, parser=Path(__file__).read_text(encoding="utf-8"),
        root=root / "snapshots", license="CC0-1.0",
        citation="Synthetic integration fixture; not a real medical source.", ledger=ledger,
    )
    loaded = load_snapshot(root / "snapshots", key, version, ledger=ledger)
    support = ((loaded.snapshot_id, "demo-row-1"),)
    traditional = CandidateClaim(
        kind="traditional_use", subject="demo:herb", object="demo:syndrome", support=support,
        statement="演示文献记载演示药材用于演示证候。",
    )
    efficacy = CandidateClaim(
        kind="efficacy", subject="demo:herb", object="demo:syndrome", support=support,
        statement="演示疗效结论。",
    )
    permitted = check_release([traditional], [loaded])
    refused = check_release([efficacy], [loaded])
    assert loaded.snapshot_id == built.snapshot_id
    assert permitted.ok and len(permitted.released) == 1
    assert not refused.ok and len(refused.refused) == 1
    print(json.dumps({"snapshot_id": loaded.snapshot_id,
                      "traditional_use_allowed": permitted.ok,
                      "efficacy_allowed": refused.ok,
                      "refusal_reason": refused.refused[0][1]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
```

预期：`traditional_use_allowed=true`，`efficacy_allowed=false`，拒绝原因是该证据只支持出处和传统使用记载。真实资源应新增对应 `sources/cards.py` 的 SourceCard 和解析器：填写实际许可、引用、terms URL、可用访问方式、QC 指标；Hub 的目录 SourceCard 不会替你注册这一层。

部署时将 ledger 放在 Agent 无写权限的审计位置，并限制 Agent 改写原始数据和快照。演示目录结构本身不建立操作系统权限边界。QC 为 `review` 时按项目流程人工处理，不用 `accept_review=True` 绕过待审状态。`check_release` 是证据结论审核，不是数据使用许可审核。

## 7. 港标、DNA、古籍、证候和试验如何复用上述路线

### 港标 / 药典：原文件 + 结构化标准表

建议中间表列为：

```text
standard_id, herb_id, source_name, edition, monograph,
test_item, method, limit_value, limit_unit, chemical_marker,
source_url, page, source_row_id, review_status
```

把每个标准条款保留为记录，不能把重金属限度、药典鉴别方法等强行转成 `ingredient_target`。至少核对版次、药材基原、干品/样品基准、检测方法和单位。不同版本先并存，用生效日期与适用范围决定查询。

文件定义可采用以下写法；这是放入一个 DatasetSpec 的 `files`，需另外提供实际 key、目录条目和许可：

```python
from bioagent.tcmdb.extra.traditional import FMT     # 人工模板的严格读取格式
from bioagent.tcmdb.spec import FileSpec

files = (
    FileSpec("", "quality_standards.tsv", "quality_standards", fmt=FMT),
    FileSpec("", "monographs.pdf", "", fmt="raw"),
)
```

人工整理的固定模板用 `FMT` 严格读取（列名、字段数、引号和编码不符即拒绝）；共用的 `tsv` 读取器只用于不受我们控制格式的上游导出。新的模板表要在 `traditional.SCHEMAS` 中登记列名。

`raw` 保留文件，不会自动 OCR 或解析 PDF。多份文件逐一声明或使用明确的文件清单。先用 `tcmdb query <dataset> quality_standards` 提供表查询；有跨库关系需求时再通过 extra 模块的 `KINDS` 定义质量标准关系和 extractor。

### DNA：FASTA + 标本和物种元数据

保留 `sequence_id, herb_id, taxon_id, scientific_name, marker, accession, voucher, source_url, review_status`。序列文件用 `fmt="raw"` 保留，元数据表用 TSV。药材—基原可转换为项目支持的 `has_base_species`；序列对象不要伪装成药物成分。身份验证还需实际序列质量检查、参考集和鉴别方法。

### 古籍：可引用的文段索引

保存 `book_id, edition, volume, page, passage_id, original_text, normalized_text, image_file, ocr_confidence, review_status`。图像/PDF 保留原件，文段用 JSONL 或 TSV。OCR 和模型抽取先进入候选队列，人工核对后才生成古籍关系。检索返回原文、版本、页码和图像定位；嵌入检索如有需要另行实现，这个 Hub 没有自动提供向量 RAG。

### TCMSSD：先拿字段和证据来源

向维护方申请时，应明确要：版本、证候稳定 ID、别名、症状关系、方剂/疾病映射、每条关系的来源、抽取/预测方式、许可证、允许保留及再分发范围。拿到表后建立新的 DatasetSpec 和 extractor。症状/证候仅名称一致不能直接视为同一标准概念；算法预测和文献记载要分别保留。

### ICTRP / ChiCTR：导出记录并去重

保留注册 ID、UTN/secondary IDs、题目、干预、疾病/证候、计划设计、招募状态、注册时间、最后更新时间、结果链接及原记录 URL。用原生注册 ID 标识，跨注册库记录借助官方桥接或有依据的 ID 去重。

XML 必须有显式转换脚本，或在 extra 模块中通过 `READERS` 注册解析器，不能简单改扩展名当作 TSV。导出字段可能变化，应读取真实表头建立映射。中药/方剂名称匹配先生成候选，再人工确认实际干预。相关关联可使用 Hub 中 `subject_clinical_trial`，其含义是“注册试验涉及此对象”，不表示结果有效。

## 8. 确认有 API 后，如何新增在线连接器

API 路线只适用于已核实的官方接口或被允许使用的服务。本指南不提供臆造的 `/api/search`。

1. 获取接口文档、示例请求、返回 schema、认证方式、限流规则与数据条款；运行一个被允许的最小请求。
2. 在 `providers/public_apis_tcm.py` 中按现有 `PublicSource`、`Operation` 注册真实请求；开发未验证时按项目 pending 来源流程保存。
3. 声明 `public.connector.<key>` 的精确 host 和实际操作。GET 参数、POST 表单/JSON 等按真实接口定义。需要专有认证时，先确认现有 backend 能处理，必要时补认证实现，凭据通过运行环境管理。
4. 在 `backends/http.py` 配置实际 host 的速率上限，在 `policy.py` 对相应权限 profile 增加经过确认的 host；文字说明 `rate_note` 不代替执行限流。下载或重定向域名也需明确处理。
5. 实现空结果、分页、429、认证失效、HTML 登录/挑战页面、字段变化的处理。不能把 HTTP 200 的 HTML 错误页当成成功数据。
6. 在 `BioScience-Harness` 目录验证新来源，保留验证记录，再决定是否将其列为已可用。

```bash
cd BioScience-Harness
python scripts/verify_connectors.py --only your_registered_key --no-write
```

`your_registered_key` 必须替换为真正已经注册的 key。这个脚本执行网络请求；得到响应后仍需确认业务字段、解析结果和语义正确。服务器暂不可用时保留人工导入路线，不通过关闭 TLS 校验或绕过登录来宣布接入成功。

## 9. 上线验收和更新规则

| 验收点 | 通过条件 |
|---|---|
| 许可 | 已知数据使用范围；代码许可证和数据许可证分别记录 |
| 来源 | 每条有效关系可追到原记录、URL/页码和版本 |
| 标识 | 稳定 ID，无同名误合并；药材、物种、炮制品有明确区分 |
| 待审记录 | 缺 ID、缺来源或未审核行进入 unresolved；原始表保留 |
| 数据重建 | 同输入重复 `check` 无 digest 漂移；报错不能默认为成功 |
| 科学快照 | 原文件和解析器代码固定；结构/QC 通过；重新加载与 ledger 一致 |
| 语义 | 预测与实验证据分开；注册计划与结果分开；组成与疗效分开 |
| 人工模板 | 列名、字段数、引号与编码严格校验；不符即拒绝并指出行号，原库保持不变 |
| 查询工具 | 限制来源、查询范围和条数；未载入、数据不完整、无匹配、被扣留、成功五种状态分开；每条记录带数据许可；声明读取路径与数据许可 |
| 商业查询 | 未知、非商业或禁止衍生数据许可不会混入商业可复用结果；机器过滤不能代替许可核对 |
| 更新 | 新输入/解析代码采用新版本；审查条数和 ID 变化，不覆盖旧科研快照 |

建议在新增源测试中至少固定一个人工核对的正确案例、一个同名歧义案例、一个缺来源案例，以及一个不得发布的疗效结论。对真实来源先做 20–50 条抽样核查，再扩大导入。

在 `BioScience-Harness` 目录运行与本接入相关的现有回归测试：

```bash
python -m pytest tests/test_tcmdb_framework.py tests/test_sources.py tests/test_release.py -q
```

第一阶段可交付“一个可查询的数据源 + 稳定来源 ID + 原始记录和待审队列 + 一个只读工具”。第二阶段再接入特定科研 skill 的快照解析、证据合同和发布审核。不要为了增加目录数量，把尚未取得数据或没有验证的 API 写成已接入。

## 10. 代码及官方资料

- [TCMScience 数据源说明](https://github.com/psknlr/TCMScience/blob/df0cd3e37c254c65dc39db6703955bac0e3418ad/docs/tcm-data-sources.md)
- [第三方数据库连接器规范](https://github.com/psknlr/TCMScience/blob/df0cd3e37c254c65dc39db6703955bac0e3418ad/BioScience-Harness/docs/THIRD_PARTY_DB_CONNECTOR_SPEC.md)
- [extra 扩展入口](https://github.com/psknlr/TCMScience/blob/df0cd3e37c254c65dc39db6703955bac0e3418ad/BioScience-Harness/src/bioagent/tcmdb/extra/__init__.py)
- [科学快照 schema](https://github.com/psknlr/TCMScience/blob/df0cd3e37c254c65dc39db6703955bac0e3418ad/BioScience-Harness/src/bioagent/sources/schema.py)
- [HKBU 中药方剂图像数据库](https://library.hkbu.edu.hk/electronic/libdbs/cmfid/index.html)
- [香港中药材标准官方说明](https://www.cmro.gov.hk/html/gb/useful_information/public_health/pamphlet/Hong_Kong_Chinese_Materia_Medica_Standards.html)
- [香港参考 DNA 序列库入口](https://www.cmro.gov.hk/html/gb/useful_information/gcmti/research/dna_sequences/CMMR_SL.html)；本次复查该入口超时，实际获取方式和可访问性需再确认。
- [TCMSSD 原论文](https://pubmed.ncbi.nlm.nih.gov/38471316/)
- [WHO ICTRP 搜索门户](https://trialsearch.who.int/)
- [WHO XML Web Service 说明](https://www.who.int/tools/clinical-trials-registry-platform/the-ictrp-search-portal/ictrp-search-portal-web-service)
- [WHO Crawling Service 状态](https://www.who.int/tools/clinical-trials-registry-platform/the-ictrp-search-portal/ictrp-search-portal-crawling-service)

## 11. 第一期实施记录（2026-10-04）

已按本指南第 4–6 节，在分支 `data/tcm-integration-phase1`（基线 `df0cd3e`）完成第一期接入，并推送到 fork `rachael1216/TCMScience`（[PR #28](https://github.com/psknlr/TCMScience/pull/28)）。2026-10-05，这些提交连同下文“2026-10-05 修订”中的修正，经 [PR #29](https://github.com/psknlr/TCMScience/pull/29) 合并进上游 `psknlr/TCMScience` 的 `main`（合并提交 `9343a91`）。

**已实现（代码 + 合成数据，未下载任何真实数据）**

| 文件 | 内容 |
|---|---|
| `BioScience-Harness/src/bioagent/tcmdb/extra/traditional.py` | 新增模块：134 `hkbu_formulas_manual`、135 `hkcmms_manual`、136 `hk_cmm_dna_manual` 三个 manual `DatasetSpec`；HKBU 的 `formula_herb` extractor，产出 `listed` 证据，缺 ID/出处/未审核行进入 `unresolved` |
| `BioScience-Harness/src/bioagent/tcmdb/extra/__init__.py` | `MODULES` 加入 `traditional` |
| `BioScience-Harness/src/bioagent/tools/tcmdb.py` | 三个只读工具：`hkbu_formula_lookup`、`hkcmms_standard_lookup`、`hk_cmm_dna_lookup`，未载入/未命中返回明确状态 |
| `BioScience-Harness/src/bioagent/tools/__init__.py` | 注册上述三个工具（native 工具数 147 → 150） |
| `BioScience-Harness/src/bioagent/data/tcm_source_catalog.json` | 追加 134–136（`origin=integration-2026-10-04`，`checked=2026-10-04`，`license` 记为 unknown） |
| `BioScience-Harness/tests/test_supplement_traditional.py` | 合成数据回归：核对正确案例、同名歧义不合并、缺来源入队、组成证据不得当作疗效/不得进入商用查询 |
| `docs/tcm-data-sources.md` | 新增 134–136 说明（英文 + 中文摘要） |
| 同步测试 | `tests/test_tcmdb.py`（目录 1–136）、`tests/test_tcm.py`（tcm 工具名集合）、`tests/test_native_tools.py`（150） |

**验证结果**

- `tcmdb build hkbu_formulas_manual` → 原始表 2 行、统一关系 1 条、`unresolved=1`；`tcmdb check` → `ok=true`，并提示许可 `unknown`。
- `python -m pytest -q` → **1572 passed, 6 skipped**（全绿）。

**与指南的差异（需要你决定）**

1. **模块归属留空**：134–136 目录条目的 `modules` 为空。无法在仓库内找到 M1–M14 图例（架构文档 `tcm_agent_architecture.docx` 在仓库外），未臆测归属，已在条目的 `assessment` 中注明。若你能给出该图例，可再补。
2. **港标与 DNA 仅可查询**：未导入科研快照（`sources/` 层）。指南第 6/7 节要求质量标准和序列进入图谱前先做 schema 扩展与解析器，"标准文件、序列……先保留原始文件/元数据，确需进入图谱时再设计并验证 schema 扩展"，因此本期保留为可查询表 + 原始文件。
3. **后续阶段未登记**：TCMSSD、古籍、WHO ICTRP/ChiCTR、2025 版《中国药典》、HerbComb、GNDC 未写入目录，避免把未取得数据或未验证的接口列为已接入。需要时可按 134–136 的同一条路线补 manual 条目。

### 2026-10-05 修订（根据独立复核）

复核在独立副本上复现了五个问题，均已修正并有回归测试（`tests/test_supplement_traditional.py`，合成数据）：

| 问题 | 复现 | 修正 |
|---|---|---|
| ① 人工 TSV 没有严格校验 | 港标测试样例表头 14 列、数据 15 个值，来源 URL、页码、行 ID、审核状态依次错位，最后一个值被截掉，`check` 仍为 `ok`；含制表符的值被拆开；缺 `herb_id` 列时构建抛 `IndexError`，原有效库被替换，关系 1 条变 0 条 | 三个模板文件改用严格读取器（`traditional.FMT`）；构建在临时库中完成并提取关系后才替换正式库（对 Hub 的所有数据集生效）；修正港标测试并逐字段断言 |
| ② PSH 隔离执行看不到 `BIOAGENT_TCMDB` | 直接调用 `ok`，隔离调用 `not_loaded` | 隔离子进程收到父进程解析出的 Hub 路径（`--tcmdb`）；工具声明读取 `${tcmdb}`，权限配置按实际路径裁决：在数据湖或工作区内则读同一个库，在外则明确拒绝 |
| ③ 数据许可没有传到结果和治理层 | 港标、DNA 记录没有许可字段；`check` 无警告；PSH 中 `data_license` 是包装代码的 MIT | 记录带数据集、许可、许可类别、商业使用；只供查询的数据集也提示未知许可；manifest 声明 `license.data` 与读取路径，PSH 报告 `data_license`（数据）与 `code_license`（MIT）；`commercial` 参数与 `BIOAGENT_DATA_USE` |
| ④ 生品与炮制品经 `consensus` 后合并 | 黄芪（生）与黄芪（炙）在库中是两个 ID，共识中成为一个 `materia:huangqi` | 炮制并入所列药材名（炙黄芪），原药名与炮制仍保存在备注中；共识中保持为两条；补充「炮」「煨」前缀（炮附子不再等同附子） |
| ⑤ 库文件存在不等于数据完整 | 只有 FASTA 时 DNA 工具抛 `HubError`；待审行可被查询；FASTA 与元数据不核对 | 五种状态；默认只返回已核对行；`check` 要求 FASTA 与标本元数据一一对应，重复或空序列报错 |

交接材料同时修正：生产模板只留表头（`tcmdb template`），演示数据单独生成、名称明显虚构、全部 `pending`（`--demo`）；安装命令改为同级目录 `-e "../PSH-Harness[test]"`；验证改为 `tcmdb verify`，一个数据集都没检查到时报告 `NO DATASETS CHECKED`（退出码 3），并包含一次实际的工具查询。

范围说明：这些测试证明的是读取、构建、查询、许可传递和隔离执行的行为，**不**等于三个来源已经完成端到端的科研证据发布验收。三个来源都还没有进入 `sources/` 科研快照，也没有接入既有的科研 skill；发现了工具，并不会让已有的方剂或机制研究流程自动使用这些数据。第 4 节第 7 个测试证明的是 `listed` 标签与商业过滤，第 6 节的独立示例证明的是 `integration_demo` 的发布门行为，两者都不应扩大解读。（HKBU 此后接入了科研快照，见下文 2026-10-06 的记录。）

### 2026-10-06 HKBU 方剂接入科研快照（打通通道）

复核建议的第 6 项是：选一个来源，走完“实体映射 → 一个既有科研任务 → 来源追溯与发布门”。这次先在 HKBU 方剂组成上打通了通道，以合成数据验证：

- 新增 `sources/hkbu.py`，构建脚本增加 `--hkbu-formulas`，运行脚本支持 `--formula hkbu:formula.<id>`。规则见第 6 节“HKBU 方剂组成进入药材层”。
- 在合成数据上，网络药理 skill 对一首 HKBU 方剂运行，放行一条机制假说，其支撑路径的第一条记录正是 HKBU 行的 `source_row_id`（`tests/test_hkbu_formulas.py`）。
- 修正了 `run_skill` 的两个漏洞，现在两种情况都会被拒绝：
  1. 商用运行此前不检查被研究方剂组成记录的许可。药材层每次都参与运行，用途检查却只看 skill 声明的数据源，所以许可未声明的方剂（方剂表的方剂也是如此）照样能跑。
  2. 方剂在快照构建后被改动时，运行照常进行，`provenance.json` 却记录了分析从未读取的组成。

仍然没有做的：
- 真实的、获准使用并经人工核对的 HKBU 记录，以及用真实记录跑一次、核对结果；
- 港标和 DNA 仍只可查询；
- 研究闭环尚不识别 HKBU 方剂 ID。
