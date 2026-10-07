# Provider bindings and licence records: what runs, and on whose terms

A capability catalogue row records where a component was *found* and under which licence
the project that listed it is published. Three of those facts were being read as facts
about the code that would run:

1. **The source path.** For Biomni it is the tool's description,
   `biomni/tool/tool_description/genomics.py`, a list of dicts the agent reads. The
   function is defined in `biomni/tool/genomics.py`. The python entrypoint derived from
   the catalogue path, `biomni.tool.tool_description.genomics:annotate_celltype_scRNA`,
   names a module with no function in it. Of 817 python-backed catalogue components, 222
   were derived from a description file this way.
2. **The licence column.** Biomni lists other projects' packages in
   `biomni/env_desc.py` ("[Python Package] A Python wrapper for Gene Set Enrichment
   Analysis…"), and the catalogue gave every one of them Biomni's Apache-2.0. GSEApy is
   BSD-3-Clause; python-igraph is GPL-2.0-or-later.
3. **`NONE + federated`.** PSH's code matrix allows an unlicensed component to be invoked
   in its own process, because invoking is not redistributing. That was the only answer a
   commercial run ever got, and it says nothing about whether the owner permits commercial
   use.

```
catalogue row ──> CatalogueProvider ──> manifest
   source_path         │  binding? ── yes ──> entrypoint + implementation path (bound)
   licenses            │            ── no ───> derived entrypoint, kept, never run
                       │  licence record? ──> license.spdx, license.record
                       └  the row's value  ──> license.catalogue (provenance)

Biomni tree @ commit ──> BiomniProvider ──> verified manifests | refusals (with reasons)

run (purpose=commercial) ──> assets_for ──> usage_decision ──> Authorization + provenance
```

## Implementation bindings

A binding (`BioScience-Harness/registry/implementation_bindings.yaml`, loaded by
`bioagent.providers.bindings`) states which function a component runs:

| field | meaning |
| :--- | :--- |
| `component` | the catalogue component id (`biomni.tool.annotate_celltype_scrna`) |
| `entrypoint` | `module:function` to import; the module must be the one `implementation_path` defines |
| `repo`, `commit` | the upstream repository and the full 40-character commit it was checked at |
| `implementation_path` | the file the function is defined in |
| `description_path` | the file that describes it, kept apart (it may be empty) |
| `signature` | every parameter: name, kind (`positional_or_keyword`, `keyword_only`, …), whether a default is present |
| `licence` | the implementation's own SPDX id, and where that was read |
| `checked_on` | the date the entry was checked against the tree |

The file ships four bindings, all genomics and single-cell tools at Biomni commit
`400c1f366b96a35ca253e13c9b06c5076af41d65` (the commit `data/repos_manifest.csv` records):
`annotate_celltype_scRNA`, `create_harmony_embeddings_scRNA`,
`create_scvi_embeddings_scRNA` and `gene_set_enrichment_analysis`.

**What happens to a component without one.** `CatalogueProvider` keeps the derived
entrypoint on the manifest and marks it `runtime.entrypoint_basis = "derived"`. The
resolver (`runtime.registry.unbound_entrypoint`) then reports it UNAVAILABLE: "entrypoint
… was derived from the catalogue path …, not bound to a verified implementation; add a
reviewed binding". This representation was chosen over the alternatives on purpose:

- *Deleting the row, or quarantining it,* would silently remove 813 components from the
  index. They are real tools; the catalogue may say they exist without saying they run.
- *Setting the backend to `none`* would say the component has no invocable entrypoint,
  which is false, and would hide it from `scripts/entrypoint_census.py`.
- *The resolver* is where every other "this cannot run here, and here is why" is said, so
  the derived candidate stays registered and searchable and fails there, by name.

`scripts/entrypoint_census.py` now reports, besides CONSISTENT/IMPORTABLE/CALLABLE, how
many entrypoints are BOUND and how many DERIVED (and how many of those from a description
file): today 817 python components, 4 bound, 813 derived, 218 from a description.

**Static verification.** `BiomniProvider(root)` takes a Biomni tree and the bindings and
admits a tool only when its binding holds for that tree. Nothing is imported: the
implementation and description files are parsed with `ast`, and the description dict is
read with `ast.literal_eval`. A binding is refused, with the reason, when

- the tree is not at the commit the binding pins (read from `.git` without running git,
  through symbolic and packed refs; a tree without git metadata needs a declared commit,
  and a declaration that disagrees with `.git` is refused);
- the implementation path is a description file, or outside `biomni/`;
- the function is not a `def` at the top of the module body (only inside an `if`, a
  `try` or a class, or absent), is rebound or deleted later at module level, or is
  decorated (a decorator decides what the name is, which a static check cannot see);
- its parameters differ from the binding's in name, kind, order or default presence;
- the description does not name the function, or lists required or optional parameters
  the implementation does not take.

A verified manifest carries `entrypoint_basis = "verified"`, both paths, the commit, the
version read from `biomni/version.py`, the implementation's licence from the binding, and
its requirements: the module's imports *plus the function's own* — so the scVI tool needs
`scvi` and the Harmony tool `harmony`, not each other's. Run against a fresh shallow clone
at the pinned commit (the integration test), all four shipped bindings verify and Biomni is
never imported.

**Adding a binding** is a pull request: read the function at the commit, write its
signature, run `BiomniProvider` over a clone (or the integration test), and cite the
licence file. Moving a binding to a new commit means checking it again there.

## Licence records

`bioagent.licences` defines one record type per kind of asset, loaded from
`BioScience-Harness/registry/licence_records.yaml`:

| kind | the record states |
| :--- | :--- |
| `CodeLicence` | SPDX id, repository, commit, licence file and the sha256 of its text, integration mode, the catalogue components and import names it covers |
| `ModelLicence` | weights version and sha256, licence and terms URL, permitted purposes, what the terms say about outputs |
| `DataLicence` | version, licence (and per record where it differs), commercial use, retention, redistribution |
| `ServiceTerms` | terms URL and version, the account used (`env:NAME` or `none`, never a credential), permitted purposes, rate limit |

Every record says where its facts were read and when. The eight source cards in
`sources/cards.py` are data records already and are not repeated.

The code records shipped, each read from the upstream repository's own licence file at
the commit of a release: the one installed in the project's test environment, or the
latest where the package is not installed there (harmony-pytorch):

| catalogue row | upstream, release | file | SPDX | the catalogue said |
| :--- | :--- | :--- | :--- | :--- |
| `biomni.software.gseapy` | zqfang/GSEApy v1.3.1 (`08cb2b68`) | `LICENSE` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.scanpy` | scverse/scanpy 1.12.4 (`ff213311`) | `LICENSE` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.anndata` | scverse/anndata 0.13.4 (`a487b81d`) | `LICENSE` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.rdkit` | rdkit/rdkit Release_2026_03_6 (`0e0d85f4`) | `license.txt` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.scikit-learn` | scikit-learn 1.9.1 (`866c0f51`) | `COPYING` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.umap-learn` | lmcinnes/umap release-0.5.12 (`cdb0eeb2`) | `LICENSE.txt` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.harmony-pytorch` | lilab-bcb/harmony-pytorch 0.1.8 (`87a5ae36`) | `LICENSE` | BSD-3-Clause | Apache-2.0 |
| `biomni.software.igraph` | igraph/python-igraph 1.0.0 (`b16f2761`) | `LICENSE` + package notice | GPL-2.0-or-later | Apache-2.0 |
| `biomni.software.vina` | ccsb-scripps/AutoDock-Vina v1.2.7 (`8eb40404`) | `LICENSE` | Apache-2.0 | Apache-2.0 |

plus `code.biomni` (Apache-2.0, Biomni's own `LICENSE` at `400c1f36`) for the bound tools,
and `code.bioagent` (MIT, `BioScience-Harness/LICENSE`) for this repository's own code. The
full commits and digests are in the file. python-igraph's `LICENSE` is the GPL version 2
text; `src/igraph/__init__.py` grants "version 2 … or (at your option) any later version".
Vina's copied value happened to be right; the row now rests on Vina's own licence file.

`CatalogueProvider` puts the reviewed value in `license.spdx`, names its source in
`license.record` (`code.gseapy`, or `binding:<component>`), and keeps what the row said in
`license.catalogue`. A row nobody has reviewed keeps the catalogue's value with an empty
`license.record`, so research results do not change and a commercial run can tell the
difference.

Not recorded, because the evidence the rule asks for is not there: the R `harmony`
package (immunogenomics/harmony ships no `LICENSE` file; its `DESCRIPTION` says GPL-3);
Biomni's data-lake datasets (Biomni's own `license_info.md` lists several as
non-commercial, e.g. DisGeNET and DDInter under CC BY-NC-SA 4.0, but a dataset has no
licence file to read); PSH-Harness (no `LICENSE` file; its `pyproject.toml` says MIT).

## The usage gate

`usage_decision(assets, purpose=…)` rules on every asset one use draws on.

- **`academic`**: nothing is refused here. The policy kernel and the source cards rule on
  research use exactly as before, and the runtime records nothing new.
- **`commercial`**: every asset needs a record that permits commercial use, and an asset
  without one is refused with the licence it merely declared ("it declares 'Apache-2.0',
  which is a statement, not a reviewed grant"). Code is ruled by `psh.licensing` with
  `commercial=True`; a source card by `effective_sources` itself, so the two cannot
  disagree (a test checks every card for both purposes); other data by the same rule
  (`commercial_use` must be `allowed`); a model by its permitted purposes and its output
  terms (`unrestricted` or `attribution`); a service by its permitted purposes.

`psh.licensing.license_ruling(spdx, mode, commercial=True)` uses
`COMMERCIAL_LICENSE_TABLE`, which differs from the research table in one cell: an
unlicensed component integrated `federated` is DENY ("running code nobody licensed,
copied in or invoked in its own process, is no permission to use it commercially"). A
native equivalent, which runs none of the upstream code, stays allowed. The commercial table is never more
permissive than the research table, cell for cell, and without `commercial=True` every
answer is unchanged (both are tested in `PSH-Harness/tests/test_licensing.py`).

**What a use draws on** (`assets_for`): what runs (code by its entrypoint's import root —
`biomni` for a bound Biomni tool, `bioagent` for a native tool; a service behind a
connector; a dataset), the third-party modules it imports (`requires.python`), the data it
hands back when its licence is stated separately (`license.data`), any record that names
the component (model weights, a service it calls), and the same for every manifest in its
dependency closure.

**Where the decision goes.** `AgentSpec.purpose` (default `academic`). For a commercial
run, `Runtime.invoke` folds the gate's rulings into the call's `Authorization`, so a
refusal is DENIED with its reason and rule beside the kernel's, and records
`UsageDecision.as_dict()` — purpose, verdict, and per asset its kind, reference, role, the
licence it declared, the record relied on, the terms (for code: SPDX, repository, commit,
licence URL and digest), the verdict, reason and rule — on the POLICY_CHECKED event and in
`CallResult.metadata["usage"]`. `Runtime.lazy_set` applies the same gate, and so does
`Runtime.fetch` when given the run's spec, which auto-fetch now passes: auto-fetch runs
before invoke's gate, and a commercial run must not have the bytes on disk by the time
their use is refused.

A verified Biomni tool shows what the gate does with what is known today:

```
code  biomni.tool.gene_set_enrichment_analysis  implementation  code.biomni   ALLOW
code  gseapy                                     requirement     code.gseapy   ALLOW
code  scanpy                                     requirement     code.scanpy   ALLOW
code  esm, gget, numpy, pandas, pybiomart, requests, torch, tqdm  (no record)  DENY
```

so a commercial run of it is refused, and the refusal lists exactly which terms nobody has
reviewed.

## Epistemics: the computational tier

`bioagent.psh.epistemics.DESIGN_FOR_TIER` had no entry for `COMPUTATIONAL_PREDICTION`, the
tier split out of `PRECLINICAL` so that a prediction could not license a mechanism claim,
and `design_for()` indexed it: the records the split protects raised `KeyError`. It now
maps to `IN_SILICO`, the tier's refinement group admits only `IN_SILICO` (a prediction
whose text says "randomised" stays in silico, and the note says the tier decided), and
`SUBJECT_FOR_DESIGN` covers every `StudyDesign` (`UNKNOWN` is `UNSPECIFIED`, not human).
Tests check all three tables against the enums, run `design_for` over every tier and
every design cue, and show a computational prediction reaching `mechanism_hypothesis` and
nothing else — the same answer as `CLAIM_SUPPORT`.

## Use

```python
from bioagent.providers.biomni import BiomniProvider
from bioagent.runtime.agentspec import AgentSpec

result = BiomniProvider("/path/to/Biomni").verify()     # nothing is imported
result.manifests, result.refusals                        # verified tools, and why not

runtime.invoke("native.tool.reverse_complement",
               spec=AgentSpec(name="product", purpose="commercial"), sequence="ATG")
# CallResult(status=SUCCEEDED, metadata={"usage": {"verdict": "allow", ...}})
```

```
python scripts/entrypoint_census.py --no-imports   # BOUND / DERIVED counts
pytest -m integration tests/test_bindings_licences.py   # verify against a fresh clone
```

## Refusals

| reason (abridged) | rule |
| :--- | :--- |
| entrypoint … derived from the catalogue path …, not bound | resolver (UNAVAILABLE) |
| the binding pins X and the tree is at Y | BiomniProvider |
| implementation path … is a tool description | BiomniProvider |
| … is not defined at module level / is rebound / is decorated | BiomniProvider |
| … takes (…) and the binding expects (…) | BiomniProvider |
| the description … lists required … the implementation takes … | BiomniProvider |
| … has no reviewed licence record … | `usage.<kind>.unknown` |
| … grants no licence, and running code nobody licensed … is no permission to use it commercially | `usage.code.license.none.federated.commercial` |
| a commercial run needs commercial use allowed … | `usage.data.commercial_use`, `usage.data.source_card` |
| … permit ['academic'], not commercial use | `usage.model.purpose`, `usage.service.purpose` |
| psh.licensing is not importable … | `usage.code.no_psh` |
| purpose 'x' is not one of ('academic', 'commercial') | `usage.purpose.unknown` |

## What this is not

- **Verified is static.** It means the named function exists at module level with that
  signature in the tree at the pinned commit. The loader imports whatever `biomni` is
  installed; nothing yet compares the installed distribution with the pinned commit (the
  event log does record the component's commit).
- **A record is not a lawyer.** It states what a licence file says, at a commit. The gate
  does not re-read licences at run time and does not check that an installed library's
  version is the release the record was read at.
- **Unknown SPDX ids fail closed.** Neither licence table lists `GPL-2.0-or-later` yet, so
  python-igraph is classified unlicensed (DENY for vendoring and for commercial use).
  Adding the modern `-only`/`-or-later` ids is a change to both tables.
- **No model, dataset or service has a record yet.** The types are complete and tested;
  until someone reviews real terms, a commercial run that uses one is refused.
- **Skill runs.** `analysis/skill_runner.py` applies the source cards and the composition
  licence for a commercial run but does not call the gate on code; doing so would refuse
  every commercial skill run until PSH-Harness ships a licence file.

## 中文摘要

**问题：** 能力目录的一行记录的是组件在哪里被发现、由哪个项目列出，但有三个事实被误当作“将要运行的代码”的事实：
- **源码路径：** Biomni 的目录路径指向工具*描述*文件（`tool_description/genomics.py`），据此推导出的入口指向一个没有函数的模块；817 个 Python 组件中有 222 个如此。
- **许可证列：** Biomni 在 `env_desc.py` 中列出的第三方软件都被记成 Biomni 自己的 Apache-2.0，而 GSEApy 实为 BSD-3-Clause，python-igraph 为 GPL-2.0-or-later。
- **`NONE + federated`：** PSH 的代码矩阵允许在独立进程中调用无许可证的组件（调用不等于再分发），这从来不代表上游允许商业使用。

**实现绑定：** `registry/implementation_bindings.yaml` 逐条写明组件 id、`module:function`、上游仓库与固定提交、实现文件与（分开记录的）描述文件、期望签名（参数名、种类、是否有默认值）以及实现自身的许可证与出处。目录提供者有绑定时使用绑定；没有绑定的推导入口仍可检索，但标为 `derived`，解析器报告“不可用”并说明原因——不删除、不隔离，也不伪装成无后端。`BiomniProvider` 只用 AST 静态核验（从不导入 Biomni）：提交一致、实现不是描述文件、函数定义在模块顶层且未被重绑定或装饰、签名一致、描述与实现的参数一致；不通过的逐条记录拒绝原因。四个基因组/单细胞函数已在固定提交 `400c1f36` 的全新克隆上通过核验。

**许可证记录与使用闸门：** 四类资产各有记录——代码（SPDX、仓库、提交、许可文件摘要、集成方式）、模型（权重版本与摘要、允许用途、输出条款）、数据（版本、逐条许可、商业使用、保留与再分发）、服务（条款与版本、账号引用而非密钥、允许用途、速率限制）。商业用途下，一次运行实际使用的每项资产都必须有明确允许该用途的已审记录；未知条款一律拒绝，绝不推定。代码由 `psh.licensing`（`commercial=True`）裁决，数据源卡片直接由 `effective_sources` 裁决，二者不会不一致。学术用途的结果不变。决定（资产、条款、结论）记录在策略事件与 `CallResult.metadata["usage"]` 中。

**已核验的许可证：** GSEApy、Scanpy、anndata、RDKit、scikit-learn、UMAP、harmony-pytorch 为 BSD-3-Clause，python-igraph 为 GPL-2.0-or-later，AutoDock Vina 为 Apache-2.0，均读自上游仓库在对应发布提交上的许可文件并记录摘要；目录原值保留为 `license.catalogue`。

**证据分级：** `COMPUTATIONAL_PREDICTION` 现映射到 `IN_SILICO`，`design_for()` 对所有证据层级都不再抛错，计算预测最多许可“机制假说”。
