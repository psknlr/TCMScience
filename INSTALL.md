# Install and run · 安装与运行

Three ways in, shortest first. Everything here works offline with no API key —
the TCM knowledge layer is a seed corpus compiled into the package.

<!-- zh -->
三种进入方式，从最短的开始。这里的一切都能离线跑，不需要 API key —— TCM 知识层是编译进包里的种子语料库。

---

## 1. Try it in 30 seconds, without installing anything · 1. 30 秒试一下，什么都不用装

From the repository root, with nothing but **Python 3.11+**:

<!-- zh -->
在仓库根目录下，只需要 **Python 3.11+**：

```bash
cd BioScience-Harness
python examples/run_skills.py
```

That runs the four P0 skills and prints what each one produced, including its
limitations and whether it passed the publication gate. It finds both trees on
`sys.path` itself, so there is no environment to set up. **If you only do one
thing, do this one.**

<!-- zh -->
这会跑起四个 P0 技能，打印出每个技能产出了什么 —— 包括它的局限，以及它有没有通过发布门。它会自己在 `sys.path` 上找到两棵源码树，所以不需要配置任何环境。**如果只做一件事，就做这一件。**

You should see five artifacts and a final line:

<!-- zh -->
你应该会看到五个科研产物（research artifact, RA），以及最后一行：

```
5/5 artifacts passed the publication gate
```

---

## 2. Install it, to use the library or the CLI · 2. 安装它，以便使用库或 CLI

Two packages, and the second one is not optional — `bioagent` imports `psh`:

<!-- zh -->
两个包，第二个不是可选的 —— `bioagent` 会 import `psh`：

```bash
pip install -e "PSH-Harness[test]"          # the trusted kernel (+ pytest, hypothesis)
pip install -e "BioScience-Harness[dev]"   # the capability plane + the governance layer
```

The extras are what the test suites need. Drop them if you only want to run the
code — but without `[test]` PSH's suite does not run: three of its property modules
`importorskip("hypothesis")` and skip, and twelve modules import hypothesis directly
or through another test module that does, so pytest stops at collection with twelve
errors. Install `[test]` to run it.

<!-- zh -->
这些 extras 是测试套件需要的东西。如果你只想跑代码，可以把它们去掉 —— 但不加 `[test]`，PSH 的测试套件就跑不起来：其中三个性质测试模块会 `importorskip("hypothesis")` 而被跳过，另有十二个模块直接或经另一个导入它的测试模块导入 hypothesis，pytest 会在收集阶段因十二个错误而中止。要运行它，请安装 `[test]`。

Then:

<!-- zh -->
然后：

```bash
# what can this installation run?
python -m bioagent.cli skills --dir BioScience-Harness/skills/tcm

# run one skill
python -m bioagent.cli skill normalize-tcm-entities \
    --arg names=姜,白芍 --dir BioScience-Harness/skills/tcm

# the same thing as JSON, for a script
python -m bioagent.cli skill assess-tcm-safety \
    --arg subject=附子 --json --dir BioScience-Harness/skills/tcm
```

Nothing is installed globally; `-e` means the packages run from this checkout, so
edits take effect immediately.

<!-- zh -->
没有任何东西装到全局；`-e` 意味着这些包直接从当前这份 checkout 运行，所以改动立刻生效。

`--dir` may be left out: the CLI then uses the reviewed skills the installation ships.
In a checkout that is `BioScience-Harness/skills/tcm`. A wheel carries the same skills,
the lockfile that pins them and the rest of `registry/` inside the package, so an
installed wheel runs a governed skill from any directory:

<!-- zh -->
`--dir` 可以省略，此时 CLI 使用安装自带的已审核技能。在源码 checkout 中就是 `BioScience-Harness/skills/tcm`。wheel 在包内携带同样的技能、固定它们的锁文件和 `registry/` 的其余内容，所以从 wheel 安装后，在任何目录下都能运行受治理技能：

```bash
pip install "./PSH-Harness[test]" "./BioScience-Harness[dev]"    # not -e: built wheels
cd /tmp && python -m bioagent.cli skill normalize-tcm-entities --arg names=黄芪
```

---

## 3. Use it from Python · 3. 在 Python 里使用

```python
from bioagent.skills.p0 import assess_tcm_safety
from bioagent.contracts import validate_artifact

artifact = assess_tcm_safety("甘草", co_administered=["甘遂"], run_id="demo")

print(artifact.composite_version_string)
# runtime=psh-0.6.0+bioagent-0.2.7|skill=assess-tcm-safety@1.0.0|source=1982...|benchmark=unversioned

verdict = validate_artifact(artifact)
print(verdict.publishable, verdict.codes)   # True ()
```

The four entry points are:

<!-- zh -->
四个入口是：

| Skill | Call | Ask it |
| --- | --- | --- |
| `normalize-tcm-entities` | `normalize_tcm_entities(names)` | resolve herb/formula/syndrome names |
| `retrieve-tcm-evidence` | `retrieve_tcm_evidence(subject)` | what evidence exists |
| `analyze-tcm-network-pharmacology` | `analyze_tcm_network_pharmacology(formula_name)` | infer a target network |
| `assess-tcm-safety` | `assess_tcm_safety(subject, co_administered=[...])` | recorded safety information |

<!-- zh -->
| Skill 技能 | Call 调用 | Ask it 用途 |
| --- | --- | --- |
| `normalize-tcm-entities` | `normalize_tcm_entities(names)` | 解析药材／方剂／证候名称 |
| `retrieve-tcm-evidence` | `retrieve_tcm_evidence(subject)` | 存在哪些证据 |
| `analyze-tcm-network-pharmacology` | `analyze_tcm_network_pharmacology(formula_name)` | 推断靶点网络 |
| `assess-tcm-safety` | `assess_tcm_safety(subject, co_administered=[...])` | 记录在案的安全性信息 |

Each returns a `ResearchArtifact`. See [USAGE.md](USAGE.md) for what is in one and
how to read it.

<!-- zh -->
每个都返回一个 `ResearchArtifact`。它里面有什么、该怎么读，见 [USAGE.md](USAGE.md)。

---

## Running the tests · 运行测试

```bash
(cd PSH-Harness        && PYTHONPATH=src python -m pytest -q)
(cd BioScience-Harness && PYTHONPATH=src:../PSH-Harness/src python -m pytest -q -m unit)
```

`-m unit` selects the tier that needs no network and no data lake; there is an
`integration` tier that does, and it is not run by default. On 2026-10-29, in a
Python 3.12 environment with both packages installed, the first printed `1575 passed`
and the second `3584 passed, 30 skipped, 16 deselected, 1 xfailed` (a test whose optional
engine is not installed skips, so the skipped count depends on the environment; the xfail
is a known limit, an allergy written in traditional characters, which
[docs/tcm-clinic.md](docs/tcm-clinic.md) states); the second took 26 minutes. The counts
grow as tests are added: they are what that day's tree printed.

<!-- zh -->
`-m unit` 选中的是既不需要网络、也不需要数据湖的那一层；另有一个 `integration` 层需要它们，默认不跑。2026-10-29 在装好两个包的 Python 3.12 环境中，第一条命令输出 `1575 passed`，第二条输出 `3584 passed, 30 skipped, 16 deselected, 1 xfailed`（缺少可选引擎的测试会跳过，所以跳过的数量取决于环境；那一个 xfail 是已知的限制：用繁体字写的过敏史，[docs/tcm-clinic.md](docs/tcm-clinic.md) 写明了这一点）；第二条用了 26 分钟。测试会不断增加，这些数字是当天的代码树输出的。

## The CI checks, runnable locally · CI 检查，可在本地运行

```bash
cd BioScience-Harness
python scripts/check_lockfile.py            # the pinned skills match the tree
python scripts/make_release.py --check      # packaging hygiene
PYTHONPATH=../PSH-Harness/src python scripts/make_release.py --outdir /tmp/dist
                                            # build sdist + wheel, then run a governed
                                            # skill from the wheel alone
cd .. && python scripts/build_arena_data.py # regenerate the Arena's benchmarks.json
```

---

## What is deliberately not installed · 刻意不安装的内容

- **No benchmark cases.** They are withheld (see `.gitignore`), so `benchmarks/cases/`
  holds a README in a clone. The harness, the scorers and the schema are all here.
- **No benchmark scores.** `arena/web/data/*.json` is generated and committed:
  `scripts/build_arena_data.py` writes `benchmarks.json` from the registries, and
  `scripts/run_demo_season.py` the published demonstration run (`runs.json`,
  `tracks.json`, `skills.json` and the leaderboard's rows), which is not a benchmark
  score. The `*.example.json` files beside them carry placeholder rows, read where a
  generated file is missing. The benchmark cases and their gold answers stay withheld.
- **No live data connectors for the TCM skills.** They read the seed corpus in
  `bioagent.tcm`. A connector-backed variant would declare hosts in its
  `skill.yaml` and the compiler would derive network authority from them.

<!-- zh -->
- **不含基准用例。** 它们被刻意扣留（见 `.gitignore`），所以 clone 出来的仓库里 `benchmarks/cases/` 只有一份 README。评测框架、打分器和 schema 都在这里。
- **不含基准分数。** `arena/web/data/*.json` 是生成的，并已提交：`scripts/build_arena_data.py` 从注册表写出 `benchmarks.json`，`scripts/run_demo_season.py` 写出已发布的示范运行（`runs.json`、`tracks.json`、`skills.json` 与排行榜中的行），它不是基准分数。旁边的 `*.example.json` 文件里是占位行，在生成文件缺失时读取。基准用例及其标准答案仍不公开。
- **TCM 技能没有实时数据连接器。** 它们读的是 `bioagent.tcm` 里的种子语料库。若换成连接器驱动的版本，它会在自己的 `skill.yaml` 里声明 hosts，编译器再据此推导出网络权限。
