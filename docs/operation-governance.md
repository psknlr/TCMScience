# Operation governance: every external call a governed skill makes

`run_governed` admits a skill by its manifest, its entrypoint and its pin, records the run
in PSH's audit chain, and verifies the artifact the skill returns. Until now it then called
`fn(**arguments)`, and what the skill did inside was nobody's business. A sequence sent to
the ESM Atlas, a PDB entry fetched from RCSB, a ColabFold run talking to its MSA server: none
of these was checked against the hosts the manifest declares, none passed BioScience's policy
kernel or PSH's egress gate, and none left a record. A skill that declared `network: []`
could reach any host and still have its artifact released. **Artifact governance is not
execution governance.**

The compiler had the same gap. `skill_program` wrote every tool step as
`Destination.LOCAL_COMPUTE`, so a step that sends its query to a public web service was
compiled as local compute. PSH's egress and label checks were ruling on a destination that
was false.

Both are closed here: one door for every external call a governed skill makes, and programs
whose destinations come from the components that run them.

## The door

```
skill code ── operation(component, implementation, **arguments)
                 │   no governed run: implementation(**arguments), exactly as before
                 v
     OperationBroker   installed by run_governed while the skill runs (a context variable)
       1 declared?   the component is known; the implementation is its entrypoint;
                     its hosts are in the manifest's permissions.network; a subprocess
                     is declared; a `url` argument names one of the component's hosts
       2 PSH         bridge.admit, then ExecutionBroker.call_tool: the payload is
                     classified, the egress gate rules on the real destinations, the
                     budget is charged, an approval is asked for where risk requires one
       3 BioScience  Runtime.invoke: resolver, policy kernel (licence, the trusted
                     profile's host allowlist), backend, event log
       4 recorded    one evidence entry: PSH chain (`bioscience_operation`) + artifact
```

`bioagent.operations.operation` is what pipeline code calls. Outside a governed run nothing
is installed, so the command line and direct library calls behave as they always did. Inside
one, the broker is the only way the call runs. A refused or failed call raises
`OperationError` (`OperationRefused` for a gate's refusal) in the skill.

**The egress guard** makes the door more than a convention. While a broker is installed, a
Python audit hook watches `urllib.Request` and `socket.connect`. A `urllib` request or an IP
socket connection (loopback included) made outside an operation is refused and recorded as an
`unbrokered` operation. Inside an
operation, a `urllib` request to a host the operation does not declare is refused, and that
includes a redirect or an address the service hands back.

## The operations

| Operation | Implementation | Reaches | Performed by |
| --- | --- | --- | --- |
| `structure.esmatlas.fold` | `structure.predict:esmatlas_fold` | `api.esmatlas.com` | predict-protein-structure, method `esmatlas` |
| `structure.rcsb.entry` | `operations:fetch_bytes` | `files.rcsb.org` | `PDB:` references; dock-ligands `PDB:` receptors |
| `structure.alphafold.model` | `operations:fetch_bytes` | `alphafold.ebi.ac.uk` | `UniProt:` references and receptors (API, then model file) |
| `docking.rcsb.chemcomp` | `operations:fetch_bytes` | `data.rcsb.org` | dock-ligands, a co-crystal ligand's SMILES |
| `structure.colabfold.batch` | `structure.predict:colabfold_batch` | `api.colabfold.com`, subprocess | predict-protein-structure, method `colabfold` |
| `structure.esmfold.weights` | `structure.predict:load_esmfold` | `huggingface.co` | predict-protein-structure, method `esmfold` |

The last two are not declared by predict-protein-structure's manifest, so a governed run
refuses them before a process starts or a weight is fetched. That is the manifest's own
statement: the sequence goes to the ESM Atlas, RCSB and AlphaFold DB and nowhere else.

The other skills perform no external operation and need no change:

- the four P0 skills read the seed corpus compiled into the package;
- predict-admet loads models that `bioagent admet --build-models` built earlier, and the
  TDC download happens in that separate command, not in the skill;
- the omics skills run local tools as subprocesses (`subprocess: true`), which is local
  compute and not brokered;
- draft-tcm-prescription reads an intake file and writes a draft.

## What is recorded

One entry per operation, in the artifact under `provenance.governed.operations` and in the
chain. This is a live governed fold of ubiquitin:

```json
{"seq": 1, "operation": "structure.esmatlas.fold",
 "implementation": "bioagent.structure.predict:esmatlas_fold", "declared": true,
 "hosts": ["api.esmatlas.com"], "destinations": ["LOCAL_COMPUTE", "PUBLIC_REMOTE"],
 "label_ceiling": "RESEARCH_DEIDENTIFIED",
 "checks": ["skill_manifest:passed", "psh_broker:passed",
            "bioscience_resolver:passed", "bioscience_policy:passed"],
 "status": "SUCCEEDED", "reason": "", "arguments_sha256": "654bea53…",
 "output_sha256": "8e44b281…", "evidence_sha256": "4b1e1613…", "recorded": true}
```

`hosts`, `destinations` and `label_ceiling` are what the contract required. They are
derived by the bridge's own rules (`bioagent.psh.manifest.bridge_manifest`), so the record
and the gate cannot disagree. `checks` lists the gates that ruled, in order. `status` is a
`bioagent.status.ExecutionStatus`. `evidence_sha256` is recomputed from the entry, and
`recorded` is set only after the entry has been read back from the chain. Around the
`bioscience_operation` event the chain also holds PSH's own `egress_decision` on
`PUBLIC_REMOTE` and its `tool_call` (`execution: in_process`), and the bridge's admission.
Each operation event names the hash of the event that started the run (`run_anchor`), which
is how the attestor finds one run's operations in a shared chain.

## Release

A run whose ledger holds an operation that did not end SUCCEEDED or DEGRADED, or that the
chain does not hold, is not released, even when the skill caught the error and finished:

1. `run_governed` does not attest the artifact. The chain gets
   `bioscience_artifact_not_attested`, and the artifact's
   `provenance.governed.release_refused` says why.
2. `validate_artifact` reports **ART118** for each such operation. This is a pure check over
   the ledger the artifact declares, so a reviewer holding only the file sees it too.
3. `AuditChainAttestor` holds a declared ledger to the chain. Each entry's digest must match
   a `bioscience_operation` event of that run before its release, and an operation the chain
   records for the run that the ledger leaves out refuses attestation. An artifact from
   before ledgers existed declares none and is judged as before.

A DEGRADED operation ran and produced a value with a stated shortfall, so it does not block
release. DENIED, FAILED, UNAVAILABLE and TIMEOUT do. The offline P0 skills perform no
operation, have an empty ledger and are released as before. `test_operation_broker.py` checks
this against a lockfile pinning them as they are on disk.

## Programs with real destinations

`skill_program(contract, scope, components=..., envelope=...)` now reads each tool step from
the admitted component its tool names. That component is a PSH manifest: what
`BioScienceBridge.admit` returns, or what `bridge_manifest` derives by the same rules. The
step's destinations, effects and risk are the component's. Its label ceiling is the lowest of
the component's ceiling, the run's ceiling and every destination's ceiling. An HTTP connector
therefore compiles to `PUBLIC_REMOTE` at `RESEARCH_DEIDENTIFIED`. It refuses, with a
`SkillProgramError` naming the step, the tool and the component:

- a tool that is not an admitted component;
- a component that reaches a destination the run's envelope does not permit (the message
  names both), or that the envelope does not admit for another reason (risk, mutation,
  licence; PSH's `compatible_with`);
- a run that does not permit `LOCAL_MODEL` for the claim step.

**The safe default.** A caller that passes no component registry, or no envelope, is
refused. Assuming local compute is the error being removed, and a default that is safe for
one caller is unsafe for the next. The two callers now say what they run. In `analysis.
skill_runner`, every step of the network-pharmacology contract (`np.composition` …) is a
stage of `run_network_pharmacology`, called in process on snapshots it reads from disk. In
`research.loop`, every step runs inside the one bridged analysis tool. Both therefore compile
to local compute, and now that is derived rather than assumed.

## The run's policy

A governed run of a skill that declares no host runs under the kernel's default policy, as
before (`policy_id` `default`). A skill that declares hosts gets the same default plus
`PUBLIC_REMOTE` (`default+declared-remote`). The manifest is the reviewed request, checked
against its pin before the run starts. It is granted the destination and not the hosts: the
broker holds each call to the hosts it names, and the BioScience policy kernel holds it to
the trusted profile's allowlist. `api.esmatlas.com` was added to that allowlist
(`biomedical-research`) after a fold request from the harness succeeded on 2026-10-07.
Without it, the BioScience kernel refused every governed ESM Atlas fold.

## What this is not

- **PSH refuses to send a long protein sequence to a public service.** Its classifier
  floors a sequence of roughly a hundred residues or more (the threshold depends on its
  composition) at SENSITIVE ("uninspectable high-entropy content"), and the egress gate
  allows at most RESEARCH_DEIDENTIFIED at `PUBLIC_REMOTE`.
  A governed ESM Atlas fold of such a sequence is refused and recorded. Ubiquitin (76) passes
  and lysozyme (129) does not. This is the kernel's ruling, not a choice made here. Sending
  long sequences under governance needs a declassification path (a principal named in the
  policy), which does not exist yet. The command line, which is ungoverned, is unchanged.
- **The guard is in-process.** It cannot see a subprocess's sockets, which is why ColabFold
  is an operation of its own. A context variable does not follow threads the skill starts.
  Inside an operation, behind a proxy, a raw socket's address is the proxy's, so only
  `urllib` requests are matched to hosts there. DNS lookups are not refused. An OS sandbox,
  not an audit hook, is what would close these.
- **Operations run in this process** (`isolate=False`, recorded on each admission), as the
  skill that calls them does. PSH's isolated executor is not used for them.
- **Declaration is at the granularity the manifest has:** hosts and a subprocess flag. The
  catalogue of operations is code (`bioagent.operations.operation_components`), reviewed
  like the pipelines that call it.
- **PSH's default budget applies** to a run's operations: 200 tool calls, and 90 minutes
  from the first one.

## Use

```python
from bioagent.operations import RCSB_ENTRY, fetch_bytes, operation

data = operation(RCSB_ENTRY, fetch_bytes, url="https://files.rcsb.org/download/1UBQ.pdb",
                 timeout=60)                  # brokered inside run_governed, direct outside

run = run_governed("predict-protein-structure", {...}, skill_dir=..., allow_unpinned=True)
run.operations                                # the ledger
run.artifact.provenance["governed"]["release_refused"]
```

Tests: `BioScience-Harness/tests/test_operation_broker.py`. Its unit tests use a canned
`urllib` opener that still raises the audit event, and two tests marked `integration` make a
live RCSB download and a live ESM Atlas fold. The skill-program tests in `test_release.py`
and `test_network_pharmacology.py` now pass a component registry.

## 中文摘要

**问题：** `run_governed` 会核对技能清单、入口、锁文件固定和最终产物，但执行时直接调用 `fn(**arguments)`。技能内部发出的外部调用，例如把序列发给 ESM Atlas、从 RCSB 下载结构，既不对照清单声明的主机，也不经过 BioScience 策略内核和 PSH 出口闸门，更不留记录。同时，`skill_program` 把每个工具步骤都写成本地计算，PSH 据以判断的目的地是假的。

**做法：** 治理运行期间，每个外部调用都必须经过 `bioagent.operations.operation` 这一个入口，由 `OperationBroker` 依次处理：
- **核对声明：** 操作必须已知；实际执行的函数必须是组件声明的入口；它访问的主机必须在技能 `permissions.network` 中；若启动子进程，清单须声明；`url` 参数的主机必须属于该组件。
- **双内核执行：** 先由 PSH 桥接准入，经 `ExecutionBroker.call_tool` 分类载荷、按真实目的地裁决出口、计预算；再经 BioScience `Runtime.invoke`，走解析器、策略内核（许可证与可信主机白名单）和事件日志。
- **逐条记录：** 每次调用写一条执行证据，同时进入 PSH 审计链和产物 provenance。证据包括契约要求（组件、主机、目的地、标签上限）、经过的闸门、`ExecutionStatus` 和输出的 sha256，回读确认后才算已记录。

**出口守卫：** Python 审计钩子监视 `urllib.Request` 和 `socket.connect`。绕过入口的联网被拒绝，并记为 `unbrokered`；操作内部请求未声明的主机（包括重定向）同样被拒绝。

**发布：** 运行中只要有操作被拒、失败或未记录，即使技能捕获错误继续完成，也不会发布：
- `run_governed` 不签发证明；
- `validate_artifact` 报 **ART118**；
- 审计链证明器核对产物声明的操作账本与链上记录是否一致。

无外部操作的 P0 技能照常发布。

**程序目的地：** `skill_program` 从每个步骤所用的已准入组件推导目的地、效果、风险和标签上限，并与运行信封取交集。HTTP 组件编译为 `PUBLIC_REMOTE`；运行未授权的目的地被拒绝，报错同时点明两者。没有组件表或信封时直接拒绝，不再假定本地计算。

**已知局限：** PSH 分类器把约 100 个残基以上的蛋白序列视为无法检查的高熵内容（SENSITIVE），不允许发往公共服务，因此长序列的受治理 ESM Atlas 预测会被拒绝，需要将来提供降密（declassification）通道。守卫只在本进程内有效，看不到子进程的套接字和技能自建线程。可信白名单新增 `api.esmatlas.com`（2026-10-07 实测可用）。
