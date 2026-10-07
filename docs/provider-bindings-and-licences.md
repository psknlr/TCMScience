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
| `DataLicence` | version, licence (and per record where it differs), the provider's terms page, commercial use, retention, redistribution |
| `ServiceTerms` | terms URL and version, the account used (`env:NAME` or `none`, never a credential), permitted purposes, rate limit |

Every record says where its facts were read and when. A model, data or service record
also carries the sha256 of what was read (`checked.sha256`; a code record digests its
licence file in `licence_sha256`): a terms page has no commit to pin, and the digest is how
a later reader tells whether the page that was read is the page there now. The eight
source cards in `sources/cards.py` rule on the snapshots built from their sources; a data
record rules on the components that read a source directly.

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

plus `code.biomni` (Apache-2.0, Biomni's own `LICENSE` at `400c1f36`) for the bound tools;
the first-party `code.bioagent` (MIT, `BioScience-Harness/LICENSE`) and `code.psh` (MIT,
`PSH-Harness/LICENSE`, added on 2026-10-07: the same text, "Copyright (c) 2026 PSH-Harness
contributors"; PSH's `pyproject.toml` had declared MIT with no file); and the two libraries
a skill run reads its inputs with, `code.pyarrow` (Apache-2.0, apache/arrow
`apache-arrow-25.0.1`, `LICENSE.txt`, which also carries the permissive notices of the code
Arrow bundles) and `code.pyyaml` (MIT, yaml/pyyaml `6.0.3`). The full commits and digests
are in the file. python-igraph's `LICENSE` is the GPL version 2 text;
`src/igraph/__init__.py` grants "version 2 … or (at your option) any later version".
Vina's copied value happened to be right; the row now rests on Vina's own licence file.

`CatalogueProvider` puts the reviewed value in `license.spdx`, names its source in
`license.record` (`code.gseapy`, or `binding:<component>`), and keeps what the row said in
`license.catalogue`. A row nobody has reviewed keeps the catalogue's value with an empty
`license.record`, so research results do not change and a commercial run can tell the
difference.

Not recorded, because the evidence the rule asks for is not there: the R `harmony`
package (immunogenomics/harmony ships no `LICENSE` file; its `DESCRIPTION` says GPL-3);
Biomni's data-lake datasets other than BindingDB's (Biomni's own `license_info.md` lists
several as non-commercial, e.g. DisGeNET and DDInter under CC BY-NC-SA 4.0, but a dataset
has no licence file to read).

### Data and service records

Each was read on 2026-10-07 from the provider's own licence page or file; the record names
what was read and its sha256. A record attaches to the components that draw on the
source: the live connectors, the reviewed ToolUniverse tools, the bulk datasets the
acquisition layer fetches and the operations of governed skills
([operation-governance.md](operation-governance.md)).

| record | licence read | commercial use | components |
| :--- | :--- | :--- | :--- |
| `data.opentargets` (26.09) | CC0 1.0, platform-docs licence page | allowed | the connector |
| `data.reactome` (release 97) | CC0 for data, section 1(c) of the licence agreement | allowed | connector, ToolUniverse tool, two bulk files |
| `data.string` (12.5; files 12.0) | CC BY 4.0, "including commercial use" | allowed | connector, ToolUniverse tool, two bulk files |
| `data.uniprot` (2026_03) | CC BY 4.0 for copyrightable parts; patents disclaimed | allowed | connector, ToolUniverse tool, bulk file |
| `data.chembl` (ChEMBL_37) | CC BY-SA 3.0, the release's `LICENSE` and `REQUIRED.ATTRIBUTION` | allowed | connector, ToolUniverse tool, bulk file |
| `data.pubchem` | NCBI policy: no restriction placed, nothing granted, depositors' rights | unknown | connector, two ToolUniverse tools |
| `data.bindingdb` | CC BY 3.0 (curated), CC BY-SA 3.0 (from ChEMBL), PubChem-derived not stated | unknown | the Biomni data-lake copy (202409) |
| `data.lotus` (2026-04-13) | CC BY 4.0, the Zenodo record | allowed | two bulk files |
| `data.rcsb-pdb` | CC0 1.0 for the archive and the APIs, usage policies | allowed | `structure.rcsb.entry`, `docking.rcsb.chemcomp`, the connector |
| `data.alphafold-db` (v6) | CC BY 4.0, "for academic and commercial use" | allowed | `structure.alphafold.model` |
| `service.rcsb` | usage policies: no purpose restricted; rate limit unknown | allowed | as `data.rcsb-pdb` |
| `service.alphafold-db` | EMBL-EBI terms of use, revised 2024-02-05 | allowed | `structure.alphafold.model` |

Not recorded, because the provider states no terms that could be read, so a commercial
use stays refused as unknown:

- **NPASS** (bidd.group/NPASS, now version 3.0: home, about, help, download and privacy
  pages) and **CMAUP** (bidd.group/CMAUP: "All data can be freely downloaded" and a
  copyright line) state no licence or terms of use.
- **The ESM Atlas fold service.** esmatlas.com states no terms. The esm README
  (facebookresearch/esm at `2b369911`) licenses ESM Atlas *data* CC BY 4.0 "for academic
  and commercial use", subject to the Meta Open Source Terms of Use, which by their own text
  govern only opensource.fb.com; nothing covers what the fold service returns for a
  submitted sequence.

What the reading found against what the repository already said, reported here and not
changed (each is in the P0 lockfile's closure or a connector's manifest):

- the `bindingdb` source card and its parser give BindingDB's own records CC-BY-4.0; its
  page says CC BY 3.0, and states no licence for the PubChem assays it includes;
- the `npass` and `cmaup` cards say "Free for academic use"; the pages read do not;
- the Reactome connector declares CC-BY-4.0, which is the licence of Reactome's
  illustrations; its data is CC0.

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

**Copyleft, by mode.** Both tables (`psh.licensing` and `bioagent.policy`) now list the
current GNU ids — `GPL-2.0-only`/`-or-later`, `GPL-3.0-only`/`-or-later`,
`LGPL-2.1-only`/`-or-later`, `LGPL-3.0-only`/`-or-later`, `AGPL-3.0-only`/`-or-later` —
beside the deprecated bare spellings, which SPDX defines as the `-only` licences.
`-only` and `-or-later` differ in which later versions a recipient may choose, not in what
the licence obliges, so all are copyleft. A copyleft licence grants running the code for
any purpose and attaches its obligations to passing it on (`COPYLEFT_OBLIGATIONS`: for the
GPL, a work built on it is distributed only with its source under the same licence; for
the LGPL, the library and changes to it, and the work stays relinkable; for the AGPL, also
a modified version offered over a network). So vendoring, whose copy is distributed with
the tree, is PREFER_ALTERNATIVE; native and federated use are ALLOW, for a commercial
purpose too, and the ruling names the obligation the use does not incur. Before, these ids
read as unlicensed: python-igraph (GPL-2.0-or-later) was refused vendoring and refused a
commercial run in its own process, and a profile that admits the `none` class but not
`copyleft` let it through. A test holds the two tables to the same answer for every id.

**What a use draws on** (`assets_for`): what runs (code by its entrypoint's import root —
`biomni` for a bound Biomni tool, `bioagent` for a native tool; a service behind a
connector; a dataset), the service at the hosts its code reaches (`permissions.network`),
the third-party modules it imports (`requires.python`), the data it hands back when its
licence is stated separately (`license.data`), any record that names the component (model
weights, the licence of what a service returns), and what runs for every manifest in its
dependency closure. The reached service is new: before, a python component's service
counted only when a record named it, so an operation calling an unreviewed service (the ESM
Atlas fold) passed a commercial run because nothing said a service was used.

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

**Skill runs.** `analysis.skill_runner.run_skill(purpose="commercial")` asks the same gate,
the same way: every asset the run draws on — the analysis (`code.bioagent`), the
libraries it reads its inputs with (`pyarrow` for the Parquet snapshots, `yaml` for the
contract), PSH when it compiled the program (`code.psh`), and each source snapshot it uses
(ruled by its card) — is decided on, the decision is recorded as `provenance["usage"]`, and
a refusal stops the run before the analysis with the decision's reason. The studied
composition's licence keeps its own check. A research run does not consult the gate and its
provenance gains nothing. Before, a skill run applied the source cards and the composition
licence and asked nothing about code, because PSH-Harness had no licence file and every
commercial run would have been refused for it. Tests: `tests/test_skill_run_usage.py`.

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
| the usage gate refused this commercial run: … | `SkillRunRefused`, before the analysis runs |
| checked.sha256, the digest of the terms as they were read, is required | `LicenceRecordError` (loading a record) |

## What this is not

- **Verified is static.** It means the named function exists at module level with that
  signature in the tree at the pinned commit. The loader imports whatever `biomni` is
  installed; nothing yet compares the installed distribution with the pinned commit (the
  event log does record the component's commit).
- **A record is not a lawyer.** It states what a licence file or a terms page says, at a
  commit or on a date. The gate does not re-read licences at run time and does not check
  that an installed library's version is the release the record was read at; a page's
  digest certifies what was read, not that the page has not changed since.
- **Unknown SPDX ids fail closed.** An id neither table lists is unlicensed, so a new or
  misspelt one is refused vendoring and refused a commercial run until it is added to both.
- **No model has a record yet**, and the APIs behind the live connectors other than RCSB's
  have no service record: their data licences are recorded, their terms of use are not, so
  a commercial call through one is still refused, and says which asset was not reviewed.

## 中文摘要

**问题：** 能力目录的一行记录的是组件在哪里被发现、由哪个项目列出，但有三个事实被误当作“将要运行的代码”的事实：
- **源码路径：** Biomni 的目录路径指向工具*描述*文件（`tool_description/genomics.py`），据此推导出的入口指向一个没有函数的模块；817 个 Python 组件中有 222 个如此。
- **许可证列：** Biomni 在 `env_desc.py` 中列出的第三方软件都被记成 Biomni 自己的 Apache-2.0，而 GSEApy 实为 BSD-3-Clause，python-igraph 为 GPL-2.0-or-later。
- **`NONE + federated`：** PSH 的代码矩阵允许在独立进程中调用无许可证的组件（调用不等于再分发），这从来不代表上游允许商业使用。

**实现绑定：** `registry/implementation_bindings.yaml` 逐条写明组件 id、`module:function`、上游仓库与固定提交、实现文件与（分开记录的）描述文件、期望签名（参数名、种类、是否有默认值）以及实现自身的许可证与出处。目录提供者有绑定时使用绑定；没有绑定的推导入口仍可检索，但标为 `derived`，解析器报告“不可用”并说明原因——不删除、不隔离，也不伪装成无后端。`BiomniProvider` 只用 AST 静态核验（从不导入 Biomni）：提交一致、实现不是描述文件、函数定义在模块顶层且未被重绑定或装饰、签名一致、描述与实现的参数一致；不通过的逐条记录拒绝原因。四个基因组/单细胞函数已在固定提交 `400c1f36` 的全新克隆上通过核验。

**许可证记录与使用闸门：** 四类资产各有记录——代码（SPDX、仓库、提交、许可文件摘要、集成方式）、模型（权重版本与摘要、允许用途、输出条款）、数据（版本、逐条许可、商业使用、保留与再分发）、服务（条款与版本、账号引用而非密钥、允许用途、速率限制）。商业用途下，一次运行实际使用的每项资产都必须有明确允许该用途的已审记录；未知条款一律拒绝，绝不推定。代码由 `psh.licensing`（`commercial=True`）裁决，数据源卡片直接由 `effective_sources` 裁决，二者不会不一致。学术用途的结果不变。决定（资产、条款、结论）记录在策略事件与 `CallResult.metadata["usage"]` 中。

**已核验的许可证：** GSEApy、Scanpy、anndata、RDKit、scikit-learn、UMAP、harmony-pytorch 为 BSD-3-Clause，python-igraph 为 GPL-2.0-or-later，AutoDock Vina 为 Apache-2.0，均读自上游仓库在对应发布提交上的许可文件并记录摘要；目录原值保留为 `license.catalogue`。另有自有代码 `code.bioagent` 与 `code.psh`（PSH-Harness 新增 MIT `LICENSE` 文件），以及技能运行读取输入所用的 pyarrow（Apache-2.0）与 PyYAML（MIT）。

**Copyleft 许可：** 两张许可表（`psh.licensing` 与 `bioagent.policy`）现已收录 GPL、LGPL、AGPL 的 `-only`/`-or-later` 新 SPDX 标识。copyleft 许可允许出于任何目的运行代码，义务只在传播时产生：拷入本仓库（vendor）为“建议替代”，原生实现与独立进程调用为“允许”，商业用途亦然，裁决理由写明对应义务。python-igraph 不再被误判为无许可。

**数据与服务记录：** 2026-10-07 逐一读取提供方官方许可页面或文件，记录日期与所读内容的 SHA-256，并挂到实际使用该来源的组件（在线连接器、ToolUniverse 工具、批量数据集、受治理技能的操作）：Open Targets、Reactome、RCSB PDB（CC0）；STRING、UniProt、LOTUS、AlphaFold DB（CC BY 4.0）；ChEMBL（CC BY-SA 3.0）；PubChem 与 BindingDB 的整体商业使用为“未知”。服务条款记录了 RCSB 与 AlphaFold DB（EMBL-EBI）。NPASS、CMAUP 与 ESM Atlas 折叠服务未公布可读条款，故不记录，商业用途仍被拒绝。阅读中发现：BindingDB 卡片写 CC-BY-4.0 而页面为 CC BY 3.0；NPASS、CMAUP 卡片的“学术免费”在现页面上找不到；Reactome 连接器声明的是插图许可——均未修改，留待处理。代码访问的远程服务现在也计为资产，未审服务不能再借 Python 组件通过商业闸门。

**技能运行：** `run_skill(purpose="commercial")` 与 `Runtime.invoke` 一样逐项资产裁决（分析代码、pyarrow、PyYAML、PSH、各数据源快照），决定写入 `provenance["usage"]`，任一资产未获许可即在分析前拒绝；学术运行不咨询闸门，结果不变。

**证据分级：** `COMPUTATIONAL_PREDICTION` 现映射到 `IN_SILICO`，`design_for()` 对所有证据层级都不再抛错，计算预测最多许可“机制假说”。
