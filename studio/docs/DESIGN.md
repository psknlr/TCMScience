# TCMScience Studio — design and product spec

The binding spec for the web UI: features (MVP / L1 / L2), information architecture, components, design
tokens (light and dark), the governance visual language, microcopy, shortcuts, accessibility and mobile.
It was compiled on 2026-10-07 from a product study of current scientific agent workbenches (Claude Science, Claude for
Life Sciences, FutureHouse/Kosmos, Biomni Lab, AI co-scientist, Elicit, Benchling AI, OpenAI deep research)
and from an audit of the TCMScience project page and the Arena. The tokens are contrast-checked (WCAG 2.2 AA).

> **Tao-S1 disclosure (overrides any wording below):** the page never names the upstream vendor of Tao-S1.
> Card text: 「Tao-S1 · 默认模型。无需密钥；请求经 science.impf.ai 中继转发至模型服务，有调用频率限制。」

## 0. TL;DR for implementers

1. **Where Studio is different.** Claude Science, Biomni Lab, FutureHouse, Kosmos, Elicit and
   Benchling all sell *traceability*: each figure keeps its code, and each claim links to a quote.
   None of them shows *which kind of claim the evidence is allowed to support*. TCMScience's kernel
   already computes this (claim kind × evidence design → allowed/refused, with codes such as
   `CLM005` and `EVIDENCE103`, the weakest tier, caveats, unvalidated extrapolations, the six release
   states, sha256 hashes and the composite version). The UI must make this layer **the first thing
   people see**, not a footnote. Use one consistent visual language: four evidence-family colours,
   a dashed outline for "predicted", verdict badges that always carry icon + text, and monospace
   hashes with copy buttons.
2. **Layout.** Use the Claude-style three-pane workspace. Left sidebar: projects and conversations.
   Centre: the thread, max 760px wide. Right: an **inspector** with tabs 过程 Run · 证据 Evidence ·
   主张 Claims · 文件 Files · 溯源 Provenance. The inspector replaces Claude's artifact panel and holds
   the governance objects. Compute and model are **chips in the composer/top bar** that open
   popovers, not tabs.
3. **Compute lives on the user's machine.** Claude Science works the same way: it "runs on your
   lab's own infrastructure… laptop, Linux box, or HPC login node" and "asks permission before
   reaching new resources". Every tool-run card must say *where* it ran (浏览器 / 本机 Runner /
   远程), on which device (CPU/GPU), and how long it took.
4. **Brand.** Keep the project page palette: paper `#fbfaf7`, ink `#1a2230`, navy `#2d4a7a`, jade
   `#2c7560`, ochre `#9a6428`, vermilion `#b3402d`, and its dark counterparts. Headings use a serif
   (Source Serif 4 + Noto Serif SC). UI text uses Inter with the system CJK font (PingFang SC /
   Microsoft YaHei / Noto Sans CJK). Section 5 has the complete token block, contrast-checked.
5. **Tone.** Precise, restrained, never overclaiming. The vocabulary is in §7: 记载 not 证明, 预测
   not 发现, 无记录 ≠ 安全, 不显著 ≠ 无关. No marketing superlatives inside the app.
6. **Gotchas.** Chrome 142+ shows a **Local Network Access permission prompt** when an https page
   calls `127.0.0.1`, so explain it before you trigger it. Ollama needs `OLLAMA_ORIGINS` and LM
   Studio needs "Enable CORS". Browser calls to Anthropic need the header
   `anthropic-dangerous-direct-browser-access: true`. Never send on Enter while an IME is composing
   (Chinese input).

---

### 1.10 Borrow vs. differentiate

| Borrow (table stakes) | Differentiate (TCMScience only) |
|---|---|
| Projects with instructions + knowledge | Per-project **policy**: allowed sources, web access off by default, skills can only narrow sources |
| Thread + side panel | The side panel is a **governance inspector**, not just an artifact viewer |
| Collapsible thinking / tool calls | Each tool card says *where* it ran (browser / local runner) and on *which device*, and shows input/output **hashes** |
| Plan → approve → run | The plan is a *compiled* program: compile-time refusals (`EVIDENCE103`) appear **before** anything runs |
| Citations to quotes | Citation chip shows the **evidence family** and whether that evidence **licenses** the sentence's claim kind |
| Reviewer agent | A deterministic **release gate** with six named states and codes; a refusal is shown as a result, not an error |
| Versioned artifacts | **Composite version** (runtime · skill · source · benchmark) on every artifact |
| Branch/fork conversation | Same; branches keep separate artifacts and ledgers |

---

### 2.3 Governance objects the UI must render (from the code)

**Claim kinds: two vocabularies exist. Map both to one label set.**

- PSH `psh.workflow.ir.ClaimType` values: `classical_attribution, traditional_use, mechanism,
  mechanism_hypothesis, association, clinical_efficacy, safety_signal`.
- bioagent `bioagent.tcm.model.CLAIM_KINDS`: `attribution, traditional_use, mechanism_hypothesis,
  mechanism, safety_signal, association, efficacy, recommendation`.

UI label map (zh / en). The zh labels follow `docs/assets/make_figures.py` and the site:

| key(s) | zh | en |
|---|---|---|
| `attribution` / `classical_attribution` | 记载 | Attribution |
| `traditional_use` | 传统应用 | Traditional use |
| `mechanism_hypothesis` | 机制假说 | Mechanism hypothesis |
| `mechanism` | 机制 | Mechanism |
| `association` | 关联 | Association |
| `efficacy` / `clinical_efficacy` | 疗效 | Efficacy |
| `safety_signal` | 安全信号 | Safety signal |
| `recommendation` | 推荐 | Recommendation |

**Evidence tiers:** `bioagent.tcm.model.EvidenceTier`, an IntEnum with a `.chinese` property. Real
output:

```
COMPUTATIONAL_PREDICTION 0 计算预测      CLASSICAL_TEXT 1 经典文献记载
EXPERT_EXPERIENCE 2 名医经验/专家共识     PRECLINICAL 3 临床前研究
CASE_REPORT 4 病例报告/病例系列           OBSERVATIONAL 5 观察性研究
RANDOMIZED_TRIAL 6 随机对照试验           SYSTEMATIC_REVIEW 7 系统评价/荟萃分析
```

**Licensing:** which tiers license which kind (`bioagent.tcm.model.CLAIM_SUPPORT`). This is a *set*,
not a threshold:

```
attribution          ← CLASSICAL_TEXT
traditional_use      ← CLASSICAL_TEXT, EXPERT_EXPERIENCE
mechanism_hypothesis ← COMPUTATIONAL_PREDICTION, PRECLINICAL
mechanism            ← PRECLINICAL
safety_signal        ← CASE_REPORT, OBSERVATIONAL, RANDOMIZED_TRIAL, SYSTEMATIC_REVIEW
association          ← OBSERVATIONAL, RANDOMIZED_TRIAL, SYSTEMATIC_REVIEW
efficacy             ← RANDOMIZED_TRIAL, SYSTEMATIC_REVIEW
recommendation       ← SYSTEMATIC_REVIEW
```

PSH's `psh.workflow.compiler._SUPPORTS` works on *designs* (`EVIDENCE_DESIGNS` = animal,
case_report, classical_text, expert_consensus, in_vitro, observational, randomized_trial,
systematic_review; `PREDICTIVE_DESIGNS` = docking, in_silico, molecular_dynamics,
network_prediction, pathway_enrichment, target_prediction). The licensing figure groups them as
**Tradition / Bench / Clinical / Computation**. Studio uses the same four families as its evidence
colour system (§6.1).

**Evidence quality:** `bioagent.contracts.quality`. Four named dimensions with **no aggregate
score by design** ("There is deliberately no `score`, no `grade` and no `total`"). The UI must never
show stars or a combined grade.

```
RiskOfBias  LOW0 低偏倚风险 · SOME_CONCERNS1 存在一定问题 · HIGH2 高偏倚风险 · CRITICAL3 严重偏倚风险 · NOT_ASSESSED4 未评估
Directness  DIRECT0 直接证据 · PARTIAL1 替代终点 · EXTRAPOLATED2 外推 · NOT_ASSESSED3 未评估
Precision   PRECISE0 精确 · IMPRECISE1 不精确 · NOT_ASSESSED2 未评估
Consistency CONSISTENT0 一致 · INCONSISTENT1 不一致 · SINGLE_STUDY2 单一研究 · NOT_ASSESSED3 未评估
```

The JSON carries the integer, so map it through the enum. `NOT_ASSESSED` has the *highest*
integer. Never colour-scale the integer; render "未评估" as a hollow, neutral pill.

**Artifact status:** `bioagent.contracts.artifact.ARTIFACT_STATUSES = ("draft", "validated",
"refused", "experimental")`.

**Release states:** `ArtifactVerdict.states`, six booleans. `release_authorized` is the AND of the
verified ones. "Nothing is released on `publishable` alone."

```
schema_valid · evidence_verified · outputs_verified · execution_declared · execution_attested · release_authorized
```

**Real example.** `analyze_tcm_network_pharmacology("桂枝汤")` →
`validate_artifact(a).as_dict()`, abridged:

```json
{
 "composite_version": {"runtime": "psh-0.6.0+bioagent-0.2.7",
   "skill": "analyze-tcm-network-pharmacology@1.1.0",
   "source": "198210d114c9e6aac0f5d3ed14df8b8520f2c3260a9e784726daf232481e2c6e",
   "benchmark": "unversioned"},
 "outputs": [{"path": "network.json", "sha256": "0da551c093b58f51724d7ab206890c783280d79b008fd7d1ac9c5b45fe1f626d",
   "media_type": "application/json", "bytes": 1579, "description": "herb–target network with predicted/measured split"}],
 "limitations": ["EVERY target edge here is a corpus-recorded relation or a network inference; none is a binding measurement. …", "…"],
 "sources0": {"id": "tcmscience.tcm.seed", "kind": "pharmacopoeia", "license_spdx": "MIT", "access_method": "local_file",
   "snapshot_hash": "b376a9d1…9c901ceb", "snapshot_at": "2026-09-25T00:00:00Z", "offline_capable": true,
   "known_limits": ["seed corpus only: 23 herbs, …", "absence from this corpus is not evidence of absence"]},
 "policy_id": "", "audit_head": "",
 "verdict": {"publishable": true,
   "states": {"schema_valid": true, "evidence_verified": true, "outputs_verified": false,
              "execution_declared": false, "execution_attested": false, "release_authorized": false},
   "unverified": ["output 'network.json' was not checked (no output root or content store)",
                  "no policy_id/audit_head: the run is not attested by the kernel's audit chain"],
   "warnings": [{"code": "ART105", "detail": "artifact makes no claims; it reports material but asserts nothing", "severity": "warning"}]}
}
```

**Claim and claim verdict.** `assess_tcm_safety("甘草", co_administered=["甘遂"])`, abridged:

```json
{"claim": {"id": "safety.signals", "claim_kind": "traditional_use",
  "text": "4 safety record(s) concern 甘草, 3 of them at high or critical severity; the records are expert_experience-level evidence",
  "supports": ["safety.safety.gancao_pseudoaldosteronism"],
  "declared_extrapolations": {"outcome:recorded safety information": "these are corpus records at expert_experience level, not clinical safety data"},
  "confidence": 1.0, "confidence_basis": "counted from the corpus records listed above",
  "falsified_by": "a corpus record that contradicts the table above"},
 "claim_verdict": {"allowed": true, "reasons": [], "weakest_tier": "expert_experience",
  "needs_declaration": false, "prediction_as_fact": false,
  "caveats": ["extrapolation outcome:recorded safety information is declared, not validated by evidence",
              "…: retraction status has not been checked", "…: quality dimension risk_of_bias was not assessed"],
  "unvalidated_extrapolations": ["outcome:recorded safety information"]},
 "artifact_warning": ["ART116", "claim 'safety.signals' extrapolates … on a declaration alone; no evidence item validates the bridge", "warning"]}
```

**Evidence item fields** (same run): `id, design, quote, citation, identifier, identifier_type,
source_card_id, content_hash, quote_verified, quote_offset, quality{…}, subject, population,
condition, comparator, outcome, effect, sample_size, year, retracted ("not_retracted" |
"unverified" | …), retrieved_by, retrieval_run, conflicts_with, notes`.

**Other verdicts to render:**

- `psh.kernel.output_gate.OutputVerdict`: `allowed, reason, supports, unsupported, uncited,
  label`. The output gate checks text about to reach the user: an uncited or unsupported clinical
  sentence.
- `psh.scientist.inquiry.Verdict`: `accepted / provisional / undetermined / needs_hypotheses`.
- `psh.runtime.evaluator.Verdict.goal_status`: `verified / pending_manual / unverified`. The code
  comment insists that "execution succeeded" and "goal verified" "must not be one word", so the
  UI must not collapse them into one ✓ either.

Code families seen in the sources: `INQ*` (83 hits), `CLM*` (76), `ART*` (64), `GATE*` (22),
`EVIDENCE*` (10). Render each code as a mono chip whose tooltip and details carry the localized
explanation. A dictionary of codes → zh/en explanations is needed; another agent should extract it
from the `_CODES` maps (for example `bioagent/contracts/artifact.py::_CODES`).

---

## 3. Feature list

Legend: **MVP** = first public release at science.impf.ai; **L1** = next; **L2** = later.

### 3.1 Projects

- **MVP**
  - Create, rename, archive and delete a project.
  - Per-project **instructions** (system prompt addendum).
  - Per-project **knowledge**: uploaded files plus pasted notes, stored in the browser (IndexedDB)
    or in the runner workspace.
  - Per-project defaults: model, compute target, enabled skills/tools, web access (off by default).
  - Project overview page: instructions, knowledge files, datasets, and recent claims with
    verdicts.
- **L1**
  - Project **policy card**: allowed sources (a skill may narrow but not widen them), licences,
    and an "external hosts this project may reach" list. This mirrors the README rule: "Web access
    is off unless a person has approved it".
  - Claims ledger across conversations.
  - Export the project as a bundle (JSON + files + hashes).
- **L2**
  - Sharing and teams (needs server state; out of scope while the server stays thin).
  - Templates ("网络药理学复核", "安全性检索", "经典方剂考据").

### 3.2 Conversation

- **MVP**
  - Streaming thread.
  - Markdown + tables + code + KaTeX math.
  - Chinese/English mixed text with correct line-breaking.
  - Stop generation; regenerate; edit an earlier message, which creates a **branch** with a ‹1/2›
    switcher (Claude pattern).
  - Copy a message; copy as Markdown with citations.
  - Composer: attach files, mode switch, model chip, compute chip, tools menu ("+").
  - Conversation search (local, client-side index).
  - Auto title, editable.
- **L1**
  - **Governed run** mode: question → choose a skill → **plan preview** (compiled program;
    compile-time refusals shown before running) → approve/edit → run → artifact with release
    verdict. This is the OpenAI deep-research "plan you can edit" pattern combined with Claude
    Science's "asks permission before reaching new resources".
  - Report view for long runs: TOC, sources used, activity history.
  - Pin messages; quote-reply.
- **L2**
  - **Inquiry** mode: rival hypotheses, sealed predictions, next-analysis recommendation, verdict
    states.
  - Fork into a new conversation, then compare two branches side by side.

### 3.3 Tool-run transparency (in the thread and in the Run tab)

- **MVP**
  - Every tool call is a **collapsible card** showing:
    - Tool name, as both the human label and the `bioagent.tool_id`.
    - A short arguments summary; the full args as JSON when expanded.
    - Status: queued / running / succeeded / failed / **refused** / cancelled.
    - **Where it ran** (浏览器 Worker / 本机 Runner @127.0.0.1:port / 中继) and device (CPU / CUDA:0
      / MPS / WebGPU).
    - Duration, plus input and output hashes (sha256, short form).
    - The first lines of the result, with an "在检查器中打开" button.
  - Thinking blocks are collapsed by default and labelled "思考 · 8 秒". Make clear that model
    thinking is *not* evidence.
  - The **permission prompt card** (allow once / allow for this project / deny) appears for: the
    first runner call, any network host not in the policy, writes to disk, and GPU jobs above a
    threshold.
  - A refused tool call is shown as a result, with its code and reason, and is never hidden.
- **L1**
  - Long-job cards: submitted → pending → collected / cancelled, each step timestamped. The README
    says "unfinished work is pending, never a result", so a pending job card must never show
    partial output as a result.
  - Re-run a tool call with the same inputs and compare hashes.
  - "Reproduce" button: copies the exact Python call for the runner CLI.
- **L2**
  - Full timeline/Gantt of a run, and per-step resource graphs.

### 3.4 Evidence and claim governance surfacing (the USP)

- **MVP**
  - **Citation chips** inside answer text: `[E2]` styled by evidence family (§6.1), with a hover
    card showing the design/tier, citation, the quote (marked "引文已核对" when `quote_verified`),
    the source card and the content hash.
  - **Claim blocks**: when a tool returns `CandidateClaim`s, render each as a card showing:
    - claim-kind chip and text
    - supports (evidence chips)
    - verdict (允许 / 拒绝) with reason codes
    - weakest tier
    - caveats
    - unvalidated extrapolations
    - `falsified_by` ("什么会推翻它")
    - confidence + basis, never confidence alone
  - **Licensing ladder:** a mini visual of the eight tiers. It highlights the tiers that license
    this claim kind and places a marker at the claim's weakest tier. Pass or fail is readable
    without colour (✓/✕ + text).
  - **Release panel** for each artifact:
    - the six states as a checklist
    - the `unverified` reasons in plain words
    - status chip (草稿 / 已验证 / 已拒绝 / 实验性)
    - composite version (4 axes)
    - outputs with sha256
    - **limitations, always visible** (an artifact without limitations is invalid by contract)
    - assumptions
    - `policy_id` and `audit_head`
  - Evidence table view (Elicit-style), one row per evidence item, with quality dimensions as four
    separate columns and no total.
  - "Prediction ≠ measurement" styling everywhere: predicted edges, targets and evidence use a
    **dashed outline** plus the label "预测".
- **L1**
  - **Output-gate annotations** on the assistant's own prose. Run
    `psh.kernel.output_gate` (in Pyodide or the runner) over the final answer. Underline uncited
    clinical sentences with a dotted ochre line and give unsupported ones a vermilion margin mark,
    then summarise at message end: "输出闸门：2 句临床断言缺少引用".
  - Source cards browser: licence, access method, snapshot hash/date, known limits, offline
    capability.
  - Audit chain viewer: list of ledger entries with prev-hash → hash; "verify chain" runs locally.
  - Claim ledger across the project, filterable by kind / verdict / code.
- **L2**
  - Licensing matrix explorer (the site's Figure 2, interactive, drawn from `_SUPPORTS` at
    runtime).
  - Diff two artifacts (same question, different source snapshots).

### 3.5 Files and datasets

- **MVP**
  - Drag and drop or paste files (CSV/TSV, XLSX, JSON, TXT/MD, PDF, FASTA, SDF/MOL, h5ad is
    runner-only).
  - Files are hashed on entry (sha256, Web Crypto) and listed in the Files tab with size, type,
    hash, and where the bytes live (browser IndexedDB / runner workspace path).
  - Preview: tables (virtualised, first N rows), JSON tree, text, PDF (pdf.js), images.
  - Size guard: in-browser above roughly 200 MB suggests the runner.
  - Files are **never uploaded to the server**. Say so in the drop zone.
- **L1**
  - Dataset cards: schema inference, row count, column types, **licence field** (unknown is
    displayed as "未声明许可", not as permission).
  - Ingestion assistant (Benchling "Data Entry" style): messy file → proposed structured table →
    user confirms → snapshot hash recorded.
  - Molecule viewer (SDF/SMILES via RDKit-js or a SmilesDrawer-class lib); network viewer
    (predicted vs measured edges styled differently).
- **L2**
  - 3D structure viewer (Mol*), genome tracks (igv.js), the renderers Claude Science ships.

### 3.6 Compute panel (browser vs local runner, CPU/GPU)

- **MVP**
  - Compute chip in the composer: `计算：浏览器` or `计算：本机 · GPU`. Its popover has:
    - **Targets:**
      - 浏览器: Pyodide in a Web Worker, with WebGPU detection and a memory hint.
      - 本机 Runner: URL, a pairing token, status dot, and version.
    - **Device:** auto / CPU / GPU (list detected devices from the runner: CUDA index + name + VRAM,
      Apple MPS, ROCm), plus a thread count slider and a memory cap.
    - **Engines:** an installed/missing list (for example Boltz-2, Chai-1, OpenMM, scVI, PyDESeq2).
      A missing engine shows the install command and the sentence 「未安装时不会用近似结果顶替」.
    - **Network policy:** web access off/on per project, plus the allowed hosts list.
  - The runner pairing flow (§9.1) explains the browser's permission prompt *before* triggering it.
- **L1**
  - Job queue (running, pending, collected), cancel; persistent across reloads (the runner keeps
    state).
  - Resource meters (CPU %, RAM, GPU memory) polled from the runner.
- **L2**
  - Remote targets (SSH/Slurm) through the runner, like Claude Science's HPC submission.

### 3.7 Model settings

- **MVP**
  - **Tao-S1 (default)**: works with no key, through the Cloudflare Worker relay. The card states
    that prompts pass through the science.impf.ai relay, and the rate limits (the upstream vendor is not named).
  - **OpenAI-compatible**: base URL + key + model id, with presets (OpenAI, DeepSeek,
    Qwen/DashScope, Moonshot, Zhipu, SiliconFlow, OpenRouter).
  - **Anthropic**: key + model; uses the browser CORS header (§9.3).
  - **Local**: presets with default URLs — Ollama `http://127.0.0.1:11434`, LM Studio `:1234/v1`,
    vLLM `:8000/v1`, llama.cpp server `:8080/v1`. Each has a CORS help link.
  - "测试连接" (test connection) button: checks a round trip and tool-call support, and reports
    latency.
  - Keys are stored **only in this browser**, with an optional passphrase encryption. The UI states
    「密钥只保存在本浏览器，不会发送到 science.impf.ai」.
  - Per-conversation override via the model chip.
- **L1**
  - Capability flags per model (tools, vision, context window, thinking) that drive UI affordances.
  - Fallback chain (for example local → Tao-S1) with explicit disclosure on the message ("由 Tao-S1
    生成（本地模型无响应）").
  - Per-message model attribution, which is always shown in the message footer.
- **L2**
  - Usage/cost estimates for BYO keys.

### 3.8 Bilingual zh/en

- **MVP**
  - UI language zh/en, defaulting from `navigator.language`, with `?lang=` override and persistence
    in `tcmscience.lang`.
  - Governance vocabulary uses the fixed glossary (§7.4).
  - Scientific identifiers (gene symbols, `CLM005`, ChEMBL ids) are never translated.
  - Herb and formula names stay in Chinese characters in both languages; English UI may add pinyin
    or Latin in muted text (葛根 *Puerariae Lobatae Radix*).
  - Model answer language follows the user's message unless a project instruction says otherwise.
- **L1**
  - Per-project answer language.
  - Bilingual export (side by side).

### 3.9 Onboarding and empty states

- **MVP**
  - First run is three quiet steps, all skippable:
    1. 语言
    2. 模型 — Tao-S1 is ready; you can also connect your own.
    3. 计算 — the browser is ready; there is an optional runner install.
  - Empty project state: a one-line purpose, then **4 example prompts tied to real skills**, each
    of which demonstrates a refusal (§4.6).
  - Empty inspector tabs explain what *will* appear ("运行工具后，这里会列出每条证据及其种类").
- **L1**
  - Guided tour of the governance inspector, run once and dismissable.
  - A "为什么被拒绝？" explainer that links codes to docs.

### 3.10 Keyboard shortcuts: §8.1. Accessibility: §8.2. Mobile: §8.3.

---

## 4. Information architecture, layout, components

### 4.1 Sitemap

```
/                      → last project/conversation (or onboarding)
/p/:projectId          → project overview (instructions, knowledge, datasets, recent claims)
/p/:projectId/c/:convId→ conversation workspace (3 panes)
/p/:projectId/claims   → claims ledger (L1)
/p/:projectId/sources  → source cards + snapshots (L1)
/p/:projectId/audit    → audit chain (L1)
/settings/models | /settings/compute | /settings/general (language, theme, data & privacy) | /settings/shortcuts
/about                 → what Studio is / what it does not claim / links to project page, Arena, GitHub
```

Client-side routing on hash or History API. All state lives in the client (IndexedDB) or in the
runner. Because the server is static plus the relay, deep links only work on the device that holds
the data. Say so in the share menu.

### 4.2 Desktop layout (≥ 1280px)

```
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ Top bar 48px: [≡] TCM·Science Studio  /  项目名 ▾  /  会话标题      [计算●本机·GPU] [EN|中] [☼] │
├──────────────┬───────────────────────────────────────────────┬─────────────────────────────┤
│ Sidebar 264  │ Thread (max 760, centered)                    │ Inspector 400 (320–720,     │
│ ─ 新建会话    │  user msg                                     │ resizable, collapsible)     │
│ ─ 搜索 ⌘K     │  assistant: [思考 ▸] [工具卡 ▸] text [E1][E2] │ Tabs: 过程 证据 主张 文件 溯源 │
│ 项目          │   ┌ 主张卡: 机制假说 · 允许 · 最弱: 计算预测 ┐  │ ─────────────────────────── │
│  ▸ 葛根芩连汤  │   └ 局限 2 条 · 可被推翻：…               ┘  │ (selected object detail,    │
│  ▸ 附子安全性  │   ┌ 产物卡: network.json sha256:0da5…626d ┐  │  or tab list)               │
│ 最近会话      │   └ 发布状态 3/6 · 未准予发布              ┘  │                             │
│ ...          │                                               │                             │
│ ─ 设置 帮助   │ Composer (sticky): [+] textarea  [模式▾][Tao-S1▾][↑] │                       │
└──────────────┴───────────────────────────────────────────────┴─────────────────────────────┘
```

- Sidebar collapses to a 56px rail (icons and tooltips) with ⌘/Ctrl+Shift+S.
- The inspector opens automatically on the first tool result in a conversation, and collapses with
  `Alt+\` (⌥\ on Mac); Ctrl+Shift+I is reserved because it opens DevTools. Its width persists per user.
- Selecting a chip in the thread (`[E2]`, a claim, an artifact) **selects it in the inspector**,
  scrolls to it and highlights it for 1.2s. The reverse works too: clicking an inspector row
  scrolls the thread to the message that produced it.
- The top-bar compute chip mirrors the composer chip. The **runner status dot** is green when
  connected, grey when not configured, and ochre when connected but busy or degraded.

### 4.3 Responsive

| Width | Sidebar | Thread | Inspector |
|---|---|---|---|
| ≥1280 | 264 fixed | fluid, max 760 | 400 docked |
| 1024–1279 | 264, or rail | fluid | **overlay drawer** from the right (420, scrim, focus-trapped) |
| 768–1023 | rail 56 (expand as overlay) | fluid | drawer, full height |
| <768 | off-canvas sheet (swipe or ≡) | full width | **bottom sheet**: 40% peek height → full; tabs as segmented control |

### 4.4 Inspector tabs (content spec)

1. **过程 Run.** A chronological list of steps for the selected assistant turn: plan (if
   governed), thinking (summary), tool calls, permission decisions, gate checks, and the release
   decision. Each row has a step number, name, kind, a "where ran" badge, duration, status and
   hashes. Same visual structure as Arena's `.trace` (`arena/web/assets/style.css` lines ≈542–559).
2. **证据 Evidence.** Grouped by family (经典与经验 / 临床前 / 临床 / 计算预测). Each item shows:
   - design + tier
   - citation
   - quote, verified or not
   - four quality pills (未评估 stays visible)
   - retraction status
   - `conflicts_with` links
   - source card link and `content_hash`

   There is a toggle to a **table view** (Elicit-style).
3. **主张 Claims.** Claim cards (§3.4) with a filter (允许 / 拒绝 / 需声明 / 含外推) and the
   licensing ladder. Refused claims stay listed, with their codes. Same structure as the Arena's
   `.claim--refused`.
4. **文件 Files.** Uploaded inputs and produced outputs. Each row shows path, media type, bytes,
   sha256 and its location (浏览器 / Runner), with preview and download. Outputs are marked
   "已核验" only when `outputs_verified`.
5. **溯源 Provenance.**
   - Composite version (runtime · skill · source · benchmark)
   - Source cards used (licence, snapshot hash/time, known limits)
   - `policy_id` and `audit_head`
   - The six release states with `unverified` reasons
   - Model + compute target + device for this run
   - "复制复现命令" (copy reproduce command)

### 4.5 Component inventory

App shell:
- `AppShell`, `TopBar`, `Sidebar` (+`SidebarRail`), `ProjectSwitcher`, `ConversationList` (date
  groups: 今天 / 昨天 / 过去 7 天 / 更早), `SearchPalette` (⌘K), `InspectorPanel` (+`ResizeHandle`
  with `role="separator"`), `BottomSheet`, `Drawer`, `Dialog`, `Popover`, `Tooltip`, `Toast`,
  `SkipLink`.

Thread:
- `Thread` (`role="log"`), `UserMessage`, `AssistantMessage`, `BranchSwitcher` ‹1/2›,
  `MessageActions` (copy, retry, edit, branch).
- `ThinkingBlock`: collapsible, shows the duration.
- `ToolCallCard`: status, where it ran, device, duration, args/result, hashes.
- `PermissionRequestCard`: once / for project / deny.
- `PlanPreview` (governed mode: steps, refusals before run, approve/edit).
- `CitationChip` [E#], `CitationHoverCard`.
- `ClaimCard` (+`LicensingLadder`, `ReasonCodeChip`, `CaveatList`).
- `ArtifactCard` (+`ReleaseStateChecklist`, `StatusChip`, `HashBadge`, `CompositeVersion`,
  `LimitationsList`).
- `OutputGateSummary` (L1), `JobCard` (L1), `StreamingCursor`, `StopButton`, `ErrorNotice`
  (connection / model / runner).

Composer:
- `Composer` (auto-grow textarea, IME-safe Enter), `AttachButton` + `DropZone`, `AttachmentPill`
  (name, size, hash computing…).
- `ModeSwitch` (对话 / 受治理运行 / 探究).
- `ModelChip` + `ModelPopover`, `ComputeChip` + `ComputePopover`, `ToolsMenu` (skills/tools on/off
  per conversation; web access toggle).

Inspector:
- `RunTimeline`, `EvidenceList` / `EvidenceTable`, `EvidenceItem`, `QualityPills`,
  `SourceCard`, `ClaimList`, `FileList`, `FilePreview` (table/json/text/pdf/image; L1:
  molecule, network), `ProvenancePanel`, `AuditChainView` (L1).

Settings:
- `ModelProviderCard` (Tao-S1, OpenAI-compatible, Anthropic, Local), `ConnectionTest`,
  `KeyField` (masked, reveal, "stored locally" note), `RunnerPairing`, `DeviceSelector`,
  `EngineList`, `LanguageSwitch`, `ThemeSwitch` (系统 / 浅色 / 深色), `DataPrivacy` (export / clear
  local data), `ShortcutSheet`.

Onboarding:
- `FirstRunStepper`, `EmptyProject` (+`ExamplePrompt` cards), `EmptyTab`.

Primitives:
- `Button` (primary, secondary, ghost, danger; sm/md), `IconButton` (≥32px target), `Chip`
  (neutral / family / status / code), `Badge`, `Kbd`, `Switch`, `Segmented`, `Select`,
  `TextField`, `Textarea`, `Slider`, `Tabs` (roving tabindex), `Disclosure`, `Spinner`,
  `ProgressBar`, `Skeleton`, `Meter`, `CopyButton`, `CodeBlock` (copy, wrap toggle, language
  label), `Table` (sortable, sticky header, tabular numbers), `KeyValue` (cf. Arena `.kv`).

### 4.6 Example prompts for the empty state

Each one maps to a real P0 skill in `BioScience-Harness/examples/run_skills.py` and shows a refusal:

1. 「规范化：姜、白芍」 — the ambiguity is preserved (生姜 or 干姜), not resolved.
2. 「桂枝汤的靶点网络，预测与实测分开列出」 — the network is entirely predicted, and the claim
   stays a mechanism hypothesis.
3. 「甘草与甘遂同用，有哪些记载？」 — an 十八反 pair, reported as a record, not as a clinical
   safety signal.
4. 「附子的现有证据有哪些？按证据种类分组」 — the evidence is reported, not adjudicated.
5. (L1, runner) 「复核葛根芩连汤的网络药理学富集：以实际被检测的蛋白为背景」 — reproduces the
   project page's case study.

---

## 5. Design system tokens

The palette is derived from the site and the figure generator, and every pair has been

Measured: light `--ink` on `--bg` 15.3:1, `--ink-2` 9.0, `--ink-3` (#596273) ≥5.4 on every light
surface, navy 8.5, jade 5.3 (4.8 on its tint, hence `--jade-ink` #25664f for text on tints at
6.0), ochre 4.8 (4.4 on its tint, so **`--ochre-ink` #8a5822 at 5.3 is used for ochre text**),
vermilion 5.5, slate 5.7. White on navy 8.9, on jade 5.5, on vermilion 5.7. Dark: every accent is
≥6.7 on its own tint. Control borders meet 3:1 (WCAG 1.4.11): light `#848b98` 3.4 on white;
dark `#677284` 3.6 on `--surface`.

```css
/* TCMScience Studio — tokens. Theme: <html data-theme="light|dark"> or absent = follow system. */
:root {
  color-scheme: light;
  /* surfaces */
  --bg:            #fbfaf7;   /* paper (site --paper) */
  --bg-sidebar:    #f4f2ed;
  --surface:       #ffffff;   /* cards, composer, inspector (site --panel) */
  --surface-2:     #f7f5f0;   /* hover rows, selected list item base */
  --sunken:        #f3f1ec;   /* code, wells, table header (site --code-bg) */
  --scrim:         rgba(20, 26, 36, .40);
  /* text */
  --ink:           #1a2230;
  --ink-2:         #3d4757;   /* secondary (site --soft) */
  --ink-3:         #596273;   /* captions, metadata — darker than site --muted for AA */
  --ink-4:         #8a93a3;   /* disabled, decorative only — never body text */
  /* lines */
  --rule:          #e4e1da;
  --rule-2:        #efece6;
  --control-border:#848b98;   /* inputs, checkboxes: ≥3:1 */
  /* brand / accent (navy = trusted kernel) */
  --accent:        #2d4a7a;
  --accent-hover:  #243d66;
  --accent-ink:    #ffffff;
  --accent-soft:   #eaf0f8;
  --focus:         #2d4a7a;
  --selection:     rgba(45, 74, 122, .16);
  /* brand hues (figure semantics: jade = capability, ochre = governance, vermilion = constraint/seal) */
  --jade:          #2c7560;  --jade-ink:  #25664f;  --jade-soft:  #e7f3ee;
  --ochre:         #9a6428;  --ochre-ink: #8a5822;  --ochre-soft: #f8f0e4;
  --vermilion:     #b3402d;  --vermilion-ink: #b3402d; --vermilion-soft: #fbece8;
  --slate:         #5b6474;  --slate-soft: #f1f2f5;
  /* semantic status */
  --ok:            var(--jade-ink);   --ok-soft:     var(--jade-soft);
  --warn:          var(--ochre-ink);  --warn-soft:   var(--ochre-soft);
  --danger:        var(--vermilion);  --danger-soft: var(--vermilion-soft);
  --info:          var(--accent);     --info-soft:   var(--accent-soft);
  /* evidence families (licensing figure groups) */
  --ev-tradition:  var(--ochre-ink);  --ev-tradition-soft:  var(--ochre-soft);  /* 经典文献 / 名医经验·专家共识 */
  --ev-bench:      var(--jade-ink);   --ev-bench-soft:      var(--jade-soft);   /* 体外 / 动物 */
  --ev-clinical:   var(--accent);     --ev-clinical-soft:   var(--accent-soft); /* 病例 / 观察 / RCT / 系统评价 */
  --ev-predicted:  var(--slate);      --ev-predicted-soft:  var(--slate-soft);  /* 计算预测 — always dashed */
  /* release verdicts */
  --verdict-released:   var(--jade-ink);   /* release_authorized */
  --verdict-consistent: var(--accent);     /* publishable, not yet attested */
  --verdict-caveat:     var(--ochre-ink);  /* allowed with caveats / warnings */
  --verdict-refused:    var(--vermilion);  /* refused, with codes */
  --verdict-draft:      var(--slate);      /* draft / experimental */

  /* type */
  --font-sans:  "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB",
                "Noto Sans SC", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif;
  --font-serif: "Source Serif 4", "Noto Serif SC", "Songti SC", "STSong", "Source Han Serif SC", Georgia, serif;
  --font-mono:  "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --text-xs: 12px;  --text-sm: 13px;  --text-base: 14px;  --text-md: 15px;  --text-lg: 16px;
  --text-xl: 18px;  --text-2xl: 22px; --text-3xl: 28px;   --text-display: 36px;
  --leading-tight: 1.3; --leading-ui: 1.5; --leading-prose-en: 1.65; --leading-prose-zh: 1.8;
  --weight-regular: 400; --weight-medium: 500; --weight-semibold: 600;   /* no 700 for CJK */
  --prose-size: 16px;                       /* message body (en) */
  --tracking-kicker: .12em;

  /* space (4px grid) */
  --space-0-5: 2px; --space-1: 4px; --space-1-5: 6px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-5: 20px;  --space-6: 24px; --space-8: 32px;  --space-10: 40px; --space-12: 48px; --space-16: 64px;

  /* radii */
  --radius-xs: 4px;   /* chips, inline code, hash badges */
  --radius-sm: 6px;   /* inputs, small buttons */
  --radius-md: 8px;   /* buttons, tool cards, popovers */
  --radius-lg: 12px;  /* composer, claim/artifact cards, inspector sections */
  --radius-xl: 16px;  /* dialogs, bottom sheet top corners */
  --radius-pill: 999px;

  /* elevation (site --shadow is level 2) */
  --shadow-1: 0 1px 2px rgba(20, 30, 50, .05);
  --shadow-2: 0 1px 2px rgba(20, 30, 50, .05), 0 8px 24px rgba(20, 30, 50, .06);
  --shadow-3: 0 2px 6px rgba(20, 30, 50, .06), 0 18px 48px rgba(20, 30, 50, .14);

  /* motion */
  --dur-1: 100ms;  /* hover, press */
  --dur-2: 160ms;  /* disclosure, tooltip, chip select */
  --dur-3: 220ms;  /* drawer, inspector, popover */
  --dur-4: 320ms;  /* bottom sheet, dialog */
  --ease-out: cubic-bezier(.2, .7, .2, 1);   /* TaoChronos --ease */
  --ease-in-out: cubic-bezier(.4, 0, .2, 1);

  /* layout */
  --topbar-h: 48px; --sidebar-w: 264px; --rail-w: 56px; --thread-max: 760px;
  --inspector-w: 400px; --inspector-min: 320px; --inspector-max: 720px; --drawer-w: 420px;
  --z-sticky: 10; --z-sidebar: 20; --z-drawer: 30; --z-popover: 40; --z-dialog: 50; --z-toast: 60;
}

/* dark: follow system unless the user forced light; and when forced dark */
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { /* same block as below */ } }
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0f1318; --bg-sidebar: #0c1014; --surface: #151a21; --surface-2: #1b212a; --sunken: #1a2029;
  --scrim: rgba(0, 0, 0, .55);
  --ink: #e7ecf2; --ink-2: #c4ccd6; --ink-3: #98a3b3; --ink-4: #6f7a8a;
  --rule: #262e39; --rule-2: #1c232c; --control-border: #677284;
  --accent: #8fb2ec; --accent-hover: #a9c4f2; --accent-ink: #0f1318; --accent-soft: #16233a;
  --focus: #8fb2ec; --selection: rgba(143, 178, 236, .22);
  --jade: #79c9ad; --jade-ink: #79c9ad; --jade-soft: #132a23;
  --ochre: #e0ab6a; --ochre-ink: #e0ab6a; --ochre-soft: #2b2114;
  --vermilion: #f08a76; --vermilion-ink: #f08a76; --vermilion-soft: #2e1a16;
  --slate: #a9b1bf; --slate-soft: #1d222b;
  --shadow-1: none; --shadow-2: none;                    /* site dark uses no shadow: borders carry depth */
  --shadow-3: 0 2px 6px rgba(0, 0, 0, .35), 0 20px 56px rgba(0, 0, 0, .5);
}
```

Implementation note: CSS cannot share one declaration block between the `@media` rule and
`[data-theme="dark"]`. Either duplicate the dark block, as TaoChronos does, or generate it. Set
`<meta name="theme-color">` for both schemes (`#fbfaf7` / `#0f1318`).

### 5.1 Typography rules

- **UI chrome:** 14px/1.5 Inter + system CJK. Sidebar items 14px. Metadata 12–13px using `--ink-3`.
  Never go below 12px for CJK.
- **Message prose:**
  - en: 16px / 1.65.
  - zh (`:lang(zh)`): 15.5px / 1.8, no justification in chat bubbles. Justify only in long report
    views, as the site does for its abstract.
- **Headings inside answers:** sans semibold (h1 20 / h2 18 / h3 16), not serif. Serif is reserved
  for *product* surfaces: brand wordmark, project titles, empty-state hero, report view titles,
  big numbers in stat tiles. This keeps the scholarly voice of the site without making the chat
  look like a paper.
- **Kicker:** 12px semibold, `letter-spacing:.12em; text-transform:uppercase` in en. In zh use
  `letter-spacing:.04em` and no transform, because tracking makes CJK look loose.
- **Mono** (JetBrains Mono) for code, tool ids, error codes and hashes. Use
  `font-variant-numeric: tabular-nums` in tables, metrics and timers.
- Put a space between CJK and Latin/numbers in authored strings ("Skill 与数据源", "121 个变异体"),
  following the site. Use full-width punctuation in zh and 「」 quotes, as the site does.
- **Webfonts:** self-host Inter (variable, latin subset), JetBrains Mono, Source Serif 4, and Noto
  Serif SC *only* for headings (unicode-range sliced, `font-display: swap`). **Do not ship Noto Sans
  SC as a webfont** for body text: it is several MB, and system CJK fonts are good. The site loads
  Noto Serif SC from Google Fonts. Google Fonts may be slow or blocked in mainland China, so prefer
  self-hosting on Cloudflare.

### 5.2 Iconography and brand

- Line icons at 1.5px stroke, 16px (chrome) and 14px (chips). A Lucide-style set (ISC licence) or
  inline SVGs. Status icons: ✓ released, ◐ consistent / unattested, ! caveat, ✕ refused, ○ draft,
  ⋯ pending.
- Wordmark: `TCM·Science` (serif 600, vermilion middle dot) + `Studio` (sans 500, `--ink-3`).
  Favicon: reuse the site's data-URI icon (navy square, white serif T, vermilion dot).
- Vermilion is used sparingly, like a seal: the brand dot, refusals, and the "constraint" highlight.
  Never use it for primary buttons. Primary buttons are navy (light) / light navy with dark text
  (dark).

### 5.3 Data visualisation

- Categorical order for charts: navy `#2d4a7a`, jade `#2c7560`, ochre `#9a6428`, vermilion
  `#b3402d`, slate `#5b6474` (dark: `#8fb2ec`, `#79c9ad`, `#e0ab6a`, `#f08a76`, `#a9b1bf`).
  Re-validate against colour-vision deficiency before use; load the `dataviz` skill when
  building charts.
- Predicted vs measured: predicted marks are hollow or dashed and labelled. Measured marks are solid.
  Apply this to network edges, target lists and bar fills.

---

## 6. Governance visual language

### 6.1 Evidence family chips

| Family | Members (bioagent tier / PSH design) | Chip style |
|---|---|---|
| 经典与经验 Tradition | CLASSICAL_TEXT, EXPERT_EXPERIENCE / classical_text, expert_consensus | `--ev-tradition-soft` bg, `--ev-tradition` text + 1px border, icon: scroll/book |
| 临床前 Bench | PRECLINICAL / in_vitro, animal | jade, icon: flask |
| 临床 Clinical | CASE_REPORT, OBSERVATIONAL, RANDOMIZED_TRIAL, SYSTEMATIC_REVIEW / case_report, observational, randomized_trial, systematic_review | navy, icon: people/stethoscope |
| 计算预测 Predicted | COMPUTATIONAL_PREDICTION / the six predictive designs | slate, **1px dashed border**, label always includes "预测", icon: dashed hexagon |

Chip text = family short name + specific design, for example 「临床 · 随机对照试验」 or
「预测 · 分子对接」. Colour is never the only carrier: icon + text are always present.

### 6.2 Verdict badges (pill, icon + text)

| State (source) | zh | en | token |
|---|---|---|---|
| `release_authorized = true` | 已准予发布 | Release authorized | `--verdict-released` solid tint + ✓ |
| `publishable && !release_authorized` | 内部一致 · 未核验 | Consistent · not verified | `--verdict-consistent` outline + ◐ |
| claim `allowed` with caveats / artifact warnings | 允许 · 附说明 | Allowed · with caveats | `--verdict-caveat` + ! |
| claim not `allowed` / status `refused` | 已拒绝 | Refused | `--verdict-refused` + ✕ (codes listed next to it) |
| `needs_declaration` / `fixable_by_declaration` | 需声明外推 | Needs a declared limit | ochre outline + ✎ |
| `prediction_as_fact` | 预测被当作事实 | Prediction stated as fact | vermilion + dashed |
| status `draft` / `experimental` | 草稿 / 实验性 | Draft / Experimental | slate outline |
| inquiry `accepted / provisional / undetermined / needs_hypotheses` | 已接受 / 暂定 / 未决 / 需补充假说 | as named | jade / navy / slate / ochre |
| goal `verified / pending_manual / unverified` | 目标已核验 / 待人工判定 / 目标未核验 | as named | jade / ochre / slate |

### 6.3 Release checklist (six states)

Render as a vertical list with an icon, a label, and (when false) the matching `unverified` reason
in `--ink-3`:

```
✓ 结构有效          schema_valid
✓ 引文已核验        evidence_verified
○ 输出已核验        outputs_verified      — 未检查 network.json（无输出目录或内容存储）
○ 已声明执行        execution_declared
○ 执行已证明        execution_attested    — 无 policy_id/audit_head：本次运行未经内核审计链证明
○ 准予发布          release_authorized
```

Header line: 「发布状态 2/6 · 未准予发布」. Never show a green summary unless `release_authorized`.

### 6.4 Licensing ladder

A single row of 8 small cells (tiers 0–7, labels on hover/focus). Cells that license the claim kind
are filled with the family colour; the others are hollow. A caret marks `weakest_tier`. The caption
reads like: 「主张类型：疗效 —— 需要 随机对照试验 或 系统评价；本主张最弱证据：临床前研究 → 不支撑（CLM005）」.

### 6.5 Hashes and versions

- `HashBadge`: `sha256:0da551c0…626d`, i.e. the first 8 and last 4 characters, in mono at 12px.
  Clicking copies the full hash and shows the toast 「已复制完整哈希」. Hover/focus shows the full
  value. In dark mode use `--sunken` bg.
- `CompositeVersion`: four key-value rows (运行时 runtime / Skill / 数据源 source / 基准 benchmark).
  Show `unversioned` literally as 「未版本化」.

---

## 7. Microcopy guidelines (中文)

### 7.1 Principles

1. **准确先于流畅。** Say exactly what happened and what kind of thing it is. Prefer 「检索到 2 条记载」
   to 「找到了相关证据」.
2. **不夸大。** Do not use 「证明」「确认有效」「发现了机制」「安全」「精准」「革命性」「全球首个」 in the
   app. The project-page slogan stays on the project page.
3. **拒绝是结果，不是故障。** Refusals use neutral, informative language and a code. They are never
   styled as errors (no 「出错了」), never apologise, and never use exclamation marks.
4. **主语清楚。** The model 「推理/建议」; the kernel 「判定/放行/拒绝」; a tool 「返回」; the
   user 「批准」. Never 「AI 认为」, and do not anthropomorphise the kernel.
5. **数字带分母与单位。** 「136/205 个成分」「耗时 3.2 秒」「1,579 字节」. State the time zone or use relative
   time with the absolute value in a tooltip.
6. **缺失要说出来。** Absence is information: 「未评估」「未核查撤稿」「未声明许可」「无记录（不等于安全）」.
7. **短。** Buttons take 2–4 characters (运行, 批准, 复制, 重试, 停止). Labels are noun phrases. Helper text
   is one sentence.
8. **一致。** Use the glossary (§7.4) everywhere: in the UI, in tooltips, and in the system prompt
   that tells the model how to talk.

### 7.2 Vocabulary: say / do not say

| 说 | 不说 | 原因 |
|---|---|---|
| 记载、载于《伤寒论》 | 证明、证实 | a classical text licenses attribution only |
| 预测、计算推断 | 发现、揭示机制 | a prediction licenses only a mechanism hypothesis |
| 机制假说 | 作用机制 | unless bench evidence licenses `mechanism` |
| 关联 | 导致、有效 | unless an RCT licenses efficacy |
| 无记录（不等于安全） | 安全、无风险 | "no record is not safety" |
| 不显著（不等于无关） | 无关、无效 | site: "not significant is not irrelevant" |
| 安全信号 | 副作用确认 | safety_signal is a signal |
| 已拒绝（CLM005） | 失败、出错 | refusals are results |
| 未核验 | 有问题 | it is a state, not a judgment |
| 内核判定 | AI 判断 | deterministic rules, not model opinion |
| 本机 Runner | 服务器、云端 | compute is local; never imply upload |
| 引文已核对 | 引用可靠 | `quote_verified` only means the quote is in the source |

### 7.3 Templates

- Tool running: 「正在本机运行 `retrieve_tcm_evidence` · CPU」 → done: 「完成 · 1.4 秒 · 返回 2 条证据」.
- Refused claim: 「未放行：疗效主张需要随机对照试验或系统评价，本主张最弱的证据是临床前研究（CLM005）。可以改为机制假说，或补充临床证据。」
- Needs declaration: 「需要声明外推：本主张把结论推到「人群」，但证据只覆盖细胞实验。声明这一局限即可放行（需声明外推）。」
- Release state header: 「发布状态 3/6 · 未准予发布：输出文件未核验，运行未经审计链证明。」
- Prediction badge tooltip: 「这是计算预测，不是测量结果。它最多支撑机制假说。」
- Empty evidence tab: 「运行工具后，这里会列出每条证据、它的种类和来源快照。」
- Empty project: title 「新项目」. Body 「写下研究问题，或从下面的示例开始。每个示例都会展示一次内核的拒绝。」
- Runner not connected: 「未连接本机 Runner。浏览器内计算仍可用；大文件和 GPU 任务需要 Runner。」 [连接 Runner]
- Before the LNA prompt: 「浏览器接下来会询问是否允许本站访问「本机上的应用」。请选择允许，这样页面才能连接 127.0.0.1 上的 Runner。数据不会离开你的电脑。」
- Local model CORS failure: 「无法连接 Ollama（127.0.0.1:11434）。请用 `OLLAMA_ORIGINS=https://science.impf.ai ollama serve` 重新启动后重试。」
- Key storage note: 「密钥只保存在本浏览器，不会发送到 science.impf.ai。清除浏览器数据会删除它。」
- Tao-S1 card: 「Tao-S1 · 默认模型。无需密钥；请求经 science.impf.ai 中继转发至模型服务，有调用频率限制。」
- Missing engine: 「Boltz-2 未安装。安装后可用；未安装时不会用近似结果顶替。」 [复制安装命令]
- Stop button tooltip: 「停止生成（Esc）」. After stopping: 「已停止。已运行的工具结果保留在「过程」中。」
- Fallback model disclosure: 「本条回答由 Tao-S1 生成：所选本地模型 30 秒内无响应。」
- Output gate summary: 「输出闸门：2 句临床断言缺少引用，已标注。」

### 7.4 Glossary (zh ↔ en), matching the site

可信内核 trusted kernel · 治理层 governance layer · 能力平面 capability plane · 网关 gateway · 发布门 release
gate · 主张 claim · 主张类型 claim kind · 证据种类 evidence kind · 研究设计 study design · 证据等级 evidence tier ·
支撑 license (verb: 「证据能否支撑该主张」) · 放行 / 拒绝 release / refuse · 最弱一环 weakest link · 外推 extrapolation
· 声明 declare · 局限 limitations · 假设 assumptions · 数据源卡片 source card · 快照 snapshot · 内容哈希 content hash ·
哈希链账本 hash-chained ledger · 审计链 audit chain · 复合版本 composite version · 预测 prediction · 实测 measured ·
引文已核对 quote verified · 撤稿 retraction · Skill (keep the English word, as the site does) · 本机 Runner local runner ·
受治理运行 governed run · 探究 inquiry.

### 7.5 English microcopy

Use the same rules. Sentence case, no exclamation marks. "Refused (CLM005)", not "Error". Mirror the
site's phrasing ("Refuses to…", "What we do not claim").

---

## 8. Shortcuts, accessibility, mobile

### 8.1 Keyboard shortcuts (show the sheet with ⌘/Ctrl + /)

| Action | Mac | Win/Linux | Note |
|---|---|---|---|
| Command palette / search | ⌘K | Ctrl+K | projects, conversations, commands, settings |
| New conversation | ⌘⇧O | Ctrl+Shift+O | matches Claude/ChatGPT habit (Ctrl+N cannot be captured) |
| Toggle sidebar | ⌘⇧S | Ctrl+Shift+S | Claude convention |
| Toggle inspector | ⌥\\ | Alt+\\ | Ctrl+Shift+I opens DevTools, so do not use it |
| Inspector tab 1–5 | ⌥1…⌥5 | Alt+1…5 | Ctrl+1–8 are browser tab keys, so do not use them |
| Focus composer | ⌘⇧; or `/` (when not typing) | Ctrl+Shift+; or `/` | |
| Send | Enter | Enter | **ignore while `event.isComposing` / keyCode 229 (IME)** |
| Newline | ⇧Enter | Shift+Enter | |
| Stop generation | Esc | Esc | Esc also closes popovers/drawers first |
| Edit last message | ↑ in empty composer | ↑ | |
| Copy last answer | ⌘⇧C | Ctrl+Shift+C | Claude convention |
| Attach file | ⌘U | Ctrl+U | preventDefault (view-source otherwise) |
| Toggle language | via palette 「切换语言」 | | avoid global chords |
| Shortcut sheet | ⌘/ | Ctrl+/ | |

### 8.2 Accessibility (target WCAG 2.2 AA)

- **Contrast:** tokens are pre-checked (§5). Control borders and focus rings are ≥3:1. The focus
  ring is `outline: 2px solid var(--focus); outline-offset: 2px` on `:focus-visible` (as in the
  Arena).
- **Never colour alone:** every evidence family and verdict has an icon + text. "Predicted" is also
  dashed.
- **Thread:** `role="log"` with `aria-live="polite"`. Do **not** announce every token. Announce
  "回答完成" (and the tool-call count) on completion; announce tool status changes politely and
  refusals assertively once.
- **Disclosures** (thinking, tool cards, claims): `<button aria-expanded aria-controls>`.
- **Inspector tabs:** `role="tablist"`, roving tabindex, arrow-key navigation.
- **Resize handle:** `role="separator" aria-orientation="vertical" aria-valuenow/min/max`, adjustable
  with arrow keys.
- **Dialogs and drawers:** focus trap, Esc closes, focus returns to the trigger. Skip link to the
  composer and the thread.
- **Language:** `<html lang>` is set to `zh-Hans` or `en`. Mixed fragments (an English gene name in
  a zh sentence is fine; English UI labels inside zh content) get `lang` attributes where whole
  phrases switch, so screen readers switch voices.
- **Targets:** ≥24×24 CSS px (WCAG 2.2 2.5.8). The project uses 32px icon buttons and 44px on touch.
- **Motion:** `prefers-reduced-motion: reduce` gives 0ms transitions, no shimmer skeletons (use
  static ones), and no caret blink. Also honour `prefers-contrast: more` (thicker borders; set
  `--ink-3` to `--ink-2`).
- **Zoom:** 200% zoom and 320px width must work without horizontal page scroll. Tables scroll
  inside their own container (`.tablewrap` pattern from the site, with `tabindex="0"` and a label).
- **Hashes:** `aria-label="sha256 完整值 …"` with a copy button labelled 「复制哈希」.

### 8.3 Mobile

- Single column. The sidebar is an off-canvas sheet. The inspector is a **bottom sheet** (peek 40%,
  drag to full) whose tabs become a segmented control. Tapping a citation chip opens the sheet on
  that item.
- Composer: sticky bottom with `padding-bottom: env(safe-area-inset-bottom)`. The textarea font
  size is **≥16px** to stop iOS zooming on focus. Mode/model/compute chips collapse into one
  「⋯」 sheet.
- Compute: phones cannot run the local runner. The compute chip shows 「浏览器」 with a note: 「本机
  Runner 需要在电脑上运行」. Large in-browser jobs ask for confirmation, with a memory warning.
- Tool cards default to collapsed. Hashes are short-form only, with tap-to-copy.
- Test on iOS Safari + Android Chrome at 360–430px widths.

---

## 9. Browser / platform gotchas that shape UX

### 9.1 Local runner from an https page

- **Chrome 142+ Local Network Access:** "The Local Network Access permission prompt is launching in
  Chrome 142". It applies to "requests from the public network to a local network or loopback
  destination". `fetch("http://127.0.0.1/…")` from an https page is exempt from mixed-content
  blocking. You can annotate with `targetAddressSpace: "local"`. Testing flag:
  `chrome://flags#local-network-access-check`. <https://developer.chrome.com/blog/local-network-access>
- From Chrome 145 the site setting is split into "Apps on device" (loopback) and "Local Network".
  Chrome 147 extends the check to WebSockets. (secondary:
  <https://localtonet.com/blog/chrome-local-network-access-apis-websockets>,
  <https://support.beyondidentity.com/hc/en-us/articles/34457691192215-Managing-the-Chrome-v142Local-Network-Access-Prompt>)
- **UX consequence:** a pairing screen that pre-explains the prompt, then a single "连接" button that
  triggers the first fetch. If the user denies, show how to re-enable it (site settings → 本机上的应用 /
  Apps on device). The compute-research agent still needs to verify Safari and Firefox behaviour
  for `http://127.0.0.1` from https.

### 9.2 Local LLM servers need CORS

- Ollama: set `OLLAMA_ORIGINS` (for example `https://science.impf.ai`), then restart. On the macOS
  app use `launchctl setenv OLLAMA_ORIGINS …`.
  <https://api.onlyoffice.com/docs/ai/guides/configuring-ollama-with-cors/>
- LM Studio: Developer → server Settings → **Enable CORS** ("By default, the LM Studio server only
  accepts same-origin requests"). <https://gptforwork.com/help/ai-models/endpoints/set-up-lm-studio-on-macos>
- vLLM and llama.cpp have their own CORS flags. The model-provider agent should confirm the exact
  flags.
- Calls to these servers also trigger the LNA prompt (loopback), so give them the same
  pre-explanation.

### 9.3 Anthropic from the browser

- Requires the header `anthropic-dangerous-direct-browser-access: true`. It suits a
  "bring your own API key" pattern. <https://simonwillison.net/2024/Aug/23/anthropic-dangerous-direct-browser-access>

### 9.4 IME

Chinese input fires Enter to confirm a candidate. The composer must check `e.isComposing` (and
`keyCode === 229` for Safari) before sending. This is the most common bug in Chinese chat UIs.

### 9.5 Storage

Keys and conversations live in IndexedDB. Offer export and clear. Warn that private windows and
"clear site data" erase everything. Note in /about that nothing is synced, because the server
holds no user data.

---
