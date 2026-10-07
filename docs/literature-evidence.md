# Literature evidence: PaperQA2 finds the passages, and the evidence chain stays ours

TCMScience releases a claim only when it rests on quotes located in primary sources and
typed by study design and scope (`EvidenceItem`, `check_claim`, `validate_artifact`).
PaperQA2 indexes documents and retrieves passages well. Its answer, though, is a model's
text: a summary can drop the qualifier that mattered, and its citations point at a chunk,
not at the words relied on. `bioagent.literature` uses PaperQA2 for document handling and
retrieval and keeps everything after retrieval in the contracts:

```
documents + digests ──build_index──▶ index (paper-qa state + manifest)
                                       │
                 question ──retrieve──▶ passages (text, offset, cosine score)
                                       │                 no model on this path
                             to_evidence ──▶ EvidenceItem (located, typed by rule)
                                       │      or withheld (no design was readable)
                 model + envelope ──synthesise──▶ CandidateAnswer (never evidence)
```

## What runs, and what does not

Read from the installed source of paper-qa 2026.8.12:

| Step | paper-qa piece used | Not used, and why |
| --- | --- | --- |
| Index | `Docs.aadd_texts` (embeds the chunks) | `Docs.aadd`: with no citation it asks a model to write one; with `use_doc_details` it queries metadata services over the network |
| Read | `html2text` for HTML (the converter paper-qa's reader uses); the configured PDF parser (`paperqa_pypdf`) | `parse_text` for `.txt`: it opens the file in the locale's encoding, translates `\r\n` and drops undecodable bytes. A text file is decoded here as strict UTF-8, so its text *is* the file |
| Chunk | `chunk_text(..., use_tiktoken=False)`, `chunk_pdf`: windows of characters | the default `chunk_text` decodes windows of tiktoken tokens and splits multi-byte characters at their edges; cutting a Chinese abstract into 120-character windows, 2 of 4 chunks were not excerpts of the source |
| Embed | `SparseEmbeddingModel` (`"sparse"`): a hashed bag of cl100k_base tokens, 1024 buckets by default | the default `text-embedding-3-small`, a remote API |
| Search | `NumpyVectorStore.max_marginal_relevance_search`, which returns cosine scores | `Docs.aget_evidence`: it summarises every chunk with a model. With summaries skipped, it gives every chunk the fixed score 5 and strips citation-like text, so its "evidence" is neither ranked nor verbatim. `Docs.retrieve_texts` drops the scores |
| Answer | `Docs.aquery` over the retrieved passages, behind the model gate | — |

**No network.** Importing paper-qa imports litellm, which fetches a model cost map from
GitHub at import time unless `LITELLM_LOCAL_MODEL_COST_MAP` is true. The adapter sets the
variable before it imports paper-qa. If litellm is not yet imported and a caller has set
the variable to anything else, the adapter refuses to run. The sparse embedding's tokenizer file ships inside litellm, which points tiktoken at
it, so nothing is downloaded. A test builds an index and retrieves from it in a subprocess
whose sockets refuse every connection. It checks that no connection was attempted, and
that the guard does catch a deliberate one.

## Located: every passage has an offset, every item a receipt

- Each document is checked against its declared SHA-256 before it is parsed. One mismatch
  refuses the whole build: an index silently missing a document reads, at retrieval, as a
  corpus with nothing to say.
- Each chunk is located in the document's text by search, so a chunk that is not a
  verbatim slice is caught at indexing. A passage carries `offset`, and
  `text[offset:offset + len(passage)] == passage` is checked again when it is retrieved.
- **Text files.** The text is the file, so an item's `content_hash` is the document's own
  digest, `\r\n` included.
- **HTML and PDF.** Their bytes are not text. The passage is located in the text the
  parser produced. The source card pins the file's digest and names the parser, so the
  step from file to text can be re-run.
- `to_evidence` issues the receipt with `EvidenceItem.located_in`, which searches the
  content itself and keeps it in the content store. `validate_artifact` re-checks it: in
  the tests, moving a receipt's offset by one character gives `ART115`.

## Typed: by a rule, or not at all

A design is the worst field to guess, because it decides which claims an item may license.
A cohort typed as a trial licenses efficacy. Each field is filled only by a fixed rule, and
only when the document's text gives exactly one reading:

| Field | Rules (ids in `bioagent.literature.evidence`) | Left unassessed when |
| --- | --- | --- |
| design | statements of a design, English and Chinese: `randomized`, `randomly assigned`, `随机对照/双盲/分组`, `随机、双盲`; `prospective cohort`, `case-control`, `observational study`, `队列研究`, `观察性研究`; `systematic review`, `meta-analysis`, `系统评价`; `case report`, `a 45-year-old man`, `病例报告`, `1例…并文献复习`; `mice`, `rats`, `121 dogs`, `大鼠`; `in vitro`, `cell lines`, cell assays, named cell lines, `细胞系`. A review that names itself one (`this meta-analysis`, `本meta分析`) reads as a review whatever else it names | no rule matches; the text states two designs as its own; it is a protocol (`study protocol for a … trial`, `will be randomized`). A mention is set aside when negated (`non-randomized`, `未随机`), plural (`randomized controlled trials` are the ones a review pools or an introduction cites) or a split of data (`随机分为训练集`) |
| population | `in/among <patients…> with/aged/who …`, `cohort/trial of <adults…> …`, `纳入N名<…患者>` | no rule matches, or two different phrases |
| comparator | `placebo`, `安慰剂`, `compared with …` (not baseline), `与…相比` | as above |
| outcome | `primary outcome was …`, `reduced the risk of …`, `was associated with <higher/lower …> …` or `… (hazard ratio …)`, `降低了…` | as above |

Bare words that also occur in other designs' abstracts are not design rules: `placebo` (an
observational study can compare with one), `cohort` alone (a trial reports its "overall
cohort"), `随机` alone (随机抽样 is random sampling), a species without a count ("in
zebrafish, transgenesis is efficient" opens a review). Every reading records its rule, the
matched span and its offset in the same text as the quote, so a reading can be checked the
way a receipt is. The rules prefer no answer to a wrong one.

**How well they read real abstracts** is measured in
[evidence-typing-accuracy.md](evidence-typing-accuracy.md), on 478 abstracts against
PubMed's publication types. On the held-out test split, 97% (94–99%) of the designs they
read are PubMed's, and they read one for 78% of the studies. A protocol is read as a trial
1 time in 17, and the population, comparator and outcome are filled for 7–14% of abstracts.

**A passage with no readable design is withheld, not typed.** `EvidenceItem` requires a
design and refuses an unknown one on purpose ("a default here would be the precise failure
mode this module exists to avoid"). Adding a `not_assessed` design would have meant a new
tier below every other in `tcm.model.EvidenceTier`, a pseudo-design in a vocabulary shared
with PSH, and a weaker "every item states its design" invariant for every consumer. It
would also have changed the pins of the four P0 skills. So `evidence_item.py` is
unchanged. The passage is reported, located, with its readings and the reason, and it
becomes an item when someone states its design. The same rule already holds in the
research loop, where records without a study design are context, not evidence.

On an item:
- **Fields not read** stay empty, and its notes say "not assessed (no rule matched)".
- **Quality** has every dimension `NOT_ASSESSED`. An unassessed risk of bias blocks an
  efficacy or recommendation claim (`CLM006`) until someone assesses it; an association
  claim is not blocked.
- **Retraction** stays `unverified`, because nothing here checks it.
- **Identifier** is the DOI or PMID the corpus manifest declares, else `sha256:<digest>`
  as a `local_artifact`. Nothing is resolved against a registry, and the source card says
  so.

On the three test abstracts:
- **The 葛根芩连汤 trial fixture** becomes a randomized-trial item: population
  成人2型糖尿病患者, comparator 安慰剂, outcome 糖化血红蛋白.
- **The EMPEROR-Preserved sentence** names no design, so it is withheld. Its population,
  comparator and outcome are still reported for the reviewer who types it.
- **The C-telopeptide cohort fixture** becomes an observational item, with its comparator
  unassessed.

## The index

`build_index` writes into an empty directory:
- `manifest.json` names the format, the versions of paper-qa, lmi, tiktoken, html2text and
  pypdf, the embedding class, the chunking, and the corpus sensitivity. It lists every
  document with its digest, parser, text digest and chunk count, and the digest of every
  other file.
- `docs.json` is paper-qa's `Docs` state, with each chunk's embedding and offset. The
  vector store is rebuilt from it at search time, because paper-qa's `Text` hashes use
  Python's per-process string hashing.
- `texts/<sha256>.txt` holds each document's text, so receipts can be issued without the
  original files.

`IndexRef` pins the index by the manifest's digest. `load_index(path, expected_sha256=…)`
refuses a manifest that differs and any file that does not match its digest, and the
files are checked again as retrieval reads them. A fixed probe sentence is embedded at
build and at search. If the digests differ, the tokenizer or the embedding changed, and
retrieval refuses instead of ranking against vectors made another way.

## Synthesis: a candidate answer, behind a gate

`synthesise(retrieval, model=ModelProfile, envelope=RunEnvelope)` refuses:
- with no model: `UNAVAILABLE`;
- with no passages: `DENIED`, because an answer over nothing would be the model's recall;
- when `permit_model` refuses: `DENIED`.

`permit_model` applies the checks of PSH's `ModelGateway`, restated over the public
`RunEnvelope`, `ModelProfile` and `DataLabel` because bioagent may not import the kernel.
It also checks the envelope's hard budget, estimated before the call. Every failing check
is reported:
- the envelope must permit the model's destination;
- the corpus label must be within the run's ceiling, the model's `max_label` and the
  destination's ceiling;
- the estimated tokens, cost and call count must be within the hard budget.

A corpus nobody classified is held at the run's ceiling, not treated as public: the
documents are the caller's files.

When the gate permits it, paper-qa's `Docs.aquery` answers over exactly the retrieved
passages. The system prompt also asks for a final `Conflicts:` paragraph. The result is a
`CandidateAnswer`, kind `candidate_explanation`, `is_evidence: false`, with:
- the passages the text cites;
- the keys it cites that name no passage it was given (`unsupported_citations`);
- the conflicts it stated, and whether it stated any section at all;
- the estimated and recorded tokens and cost.

A provider error is `FAILED` with its cause. A claim cites the passages' items, not this
text.

An embedding other than `"sparse"` is gated the same way, with a `ModelProfile` whose `id`
is the embedding's name. Without a profile it is refused before anything is read. The
profile is recorded in the manifest, so the question's embedding at search time passes the
same gate.

## The skill: `retrieve-literature-evidence`

A candidate (`skills/candidates/literature/`), refused by a governed run until a person
promotes it. Its input is a corpus directory whose `manifest.json` pins each document:

```json
{"sensitivity": "public",
 "documents": [{"file": "trial.txt", "sha256": "…", "doi": "10.5555/example.01",
                "citation": "…", "title": "…", "year": 2024, "license_spdx": "CC-BY-4.0"}]}
```

- Unknown keys are refused, and so is a file that resolves outside the directory.
- `index_dir` names where the index goes. An index already there is reused only when it
  pins the same documents, metadata and sensitivity; otherwise the run is refused.
- The artifact has one pinned source card per document searched, an evidence item for
  each typed passage, and no claim.
- Its outputs are `literature_evidence.json` (every passage with its offset, score,
  readings and evidence id, or why it was withheld) and `index_manifest.json`, the
  index's manifest byte for byte. Its digest is the index's pin.
- The skill calls no model and reaches no network. `resources.expected_tokens` is 0 and
  `permissions.network` is empty.

## Use

```python
from bioagent.literature import DocumentRef, build_index, retrieve, synthesise, to_evidence

index = build_index([DocumentRef(path="cohort.txt", sha256=digest, doi="10.5555/example.01")],
                    "RUN/index", sensitivity="public")
result = retrieve("is serum C-telopeptide associated with mortality?", index, k=5)
typed = [to_evidence(p, result.sources[p.document.text_sha256],
                     source_card_id=f"literature.{p.doc_id}") for p in result.passages]
items = [t.item for t in typed if t.item is not None]       # located and typed
withheld = [t for t in typed if t.item is None]             # located; design unread
answer = synthesise(result)   # UNAVAILABLE: no model is configured
```

```python
from bioagent.governed import run_governed
run = run_governed("retrieve-literature-evidence",
                   {"question": "…", "corpus": "CORPUS", "index_dir": "RUN/index", "k": 5},
                   skill_dir="skills/candidates/literature", allow_unpinned=True)
```

## Refusals

| Situation | Status |
| --- | --- |
| a file's bytes do not match its declared digest | `DENIED` |
| the index directory is not empty, or holds an index of another corpus | `DENIED` |
| the index manifest or any index file differs from its digest | `DENIED` |
| a remote embedding with no profile, or one the envelope does not permit | `DENIED` |
| `LITELLM_LOCAL_MODEL_COST_MAP` set to something other than true | `DENIED` |
| synthesis with no passages, or a model the gate refuses | `DENIED` |
| synthesis with no model configured | `UNAVAILABLE` |
| paper-qa or its PDF parser not installed | `UNAVAILABLE` |
| a text file that is not UTF-8, a document with no text, a non-verbatim chunk, an embedding that no longer reproduces the index's | `FAILED` |

## What this is not

- **Retrieval is lexical.** The sparse embedding hashes tokens, with no notion of meaning
  and no term weighting. A passage that says the same thing in other words can be missed.
  The cosine score ranks passages for one question, and nothing downstream reads it as
  support.
- **The rules are narrow.** On 302 held-out real abstracts
  ([evidence-typing-accuracy.md](evidence-typing-accuracy.md)), they leave the design of 22%
  of studies unassessed. That figure is 61% for the MeSH-defined animal studies. They fill a
  population, comparator or outcome for at most 14% of abstracts, and 23 of 30 filled values
  checked by hand were correct. Abstracts are not full texts: a whole paper names more
  designs, and is ambiguous more often.
- **Nothing is checked against the outside world.** No retraction status, no identifier
  resolution, no quality assessment. The artifact says so in its limitations.
- **This is not a systematic search.** Only the documents the caller supplies are
  searched.
- **Synthesis was run only against a stand-in model.** There is no API key here, so the
  call to a real provider through LiteLLM is untested. The refusals and the stand-in's
  answer are tested.

Tests: `BioScience-Harness/tests/test_literature_evidence.py` (42 tests). 24 need paper-qa
and use `need_module("paperqa")`: they skip in the unit tier and fail under
`BIOAGENT_REQUIRE_TOOLS=1`. The typing rules, the model gate, the corpus manifest and the
skill manifest are tested without it. The rules' accuracy on real abstracts is tested
offline by `tests/test_evidence_typing_benchmark.py`.

## 中文摘要

**问题：** TCMScience 只在引文在原始文献中定位、并标注了研究设计和适用范围时才发布结论。PaperQA2 擅长建索引和检索段落，但它的回答是模型生成的文本：摘要可能丢掉关键限定语，引用也只指向一个文本块，而不是所依据的原话。

**做法：** `bioagent.literature` 只用 PaperQA2 处理文档和检索，检索之后的证据链完全由本项目的契约掌握。
- **建索引：** 每个文件先按声明的 SHA-256 核对，任何一个不符则整个构建被拒绝。文本文件按严格 UTF-8 解码，文本即文件本身，引文的内容哈希就是文档自己的摘要（包括 `\r\n`）。HTML 用 html2text，PDF 用 paper-qa 配置的解析器。
- **切块：** 用 paper-qa 的按字符切块，每个块都是原文的逐字片段。paper-qa 默认按 tiktoken 切块，会把多字节字符切断，在中文摘要上 4 块中有 2 块不是原文片段。
- **嵌入与检索：** 用本地的稀疏嵌入（不联网），通过 paper-qa 的向量库检索并给出余弦得分。不使用 `aget_evidence`：它要么用模型总结每个块，要么在跳过总结时给所有块固定 5 分并删改文本。
- **不联网、不调用模型：** 导入 paper-qa 前设置 `LITELLM_LOCAL_MODEL_COST_MAP`，否则 litellm 在导入时会联网下载价格表。测试在拒绝一切连接的子进程中建索引并检索，确认没有任何连接尝试。

**定位与标注：**
- **定位：** 每个段落都带偏移量，每个证据条目都带引文凭据（内容哈希加偏移量），`validate_artifact` 会重新核对，偏移量差一个字符即报 `ART115`。
- **标注：** 研究设计、人群、对照、结局只由固定的文本规则填写，且只在原文只给出一种读法时填写。
  - 原文陈述两种自身设计时，保持未评估。试验方案（"study protocol""will be randomized"）也不读设计，因为它没有结果。
  - 以下提及会被跳过：被否定的（"非随机""未随机"）、复数的（"randomized controlled trials"，指被汇总或被引用的其他研究）、数据划分（"随机分为训练集"）。
  - 自称系统评价/荟萃分析的文本读作系统评价。
  - 每次读取都记录规则、匹配片段及其偏移量。
  - 在 478 篇真实摘要上的测量见 [evidence-typing-accuracy.md](evidence-typing-accuracy.md)。在留出的测试集上，读出的设计 97% 与 PubMed 一致，78% 的研究被读出设计。
- **读不出研究设计的段落不成为证据条目：** `EvidenceItem` 要求必须写明设计并拒绝未知设计，因此本次没有修改 `evidence_item.py`。这类段落连同位置和读取结果一并报告，等待有人确认设计。
- **其他字段：** 未读出的字段留空并注明"未评估"；质量四个维度均为未评估，因而在有人评估偏倚风险之前，疗效和推荐类结论会被拒绝（`CLM006`）；撤稿状态始终为"未核实"。

**合成：** 只有在配置了模型、运行信封允许其去向、数据标签不超过各项上限、且预算足够时才执行。未分级的语料按运行上限处理，不视为公开。输出是候选解释（`CandidateAnswer`），从不作为证据：它列出所引段落、模型编造的引用键和它陈述的冲突。没有配置模型时拒绝并说明原因。本环境没有 API 密钥，所以真实模型调用未经测试，只用替身模型验证了流程。

**Skill：** 候选 Skill `retrieve-literature-evidence` 不调用模型、不联网，也不作任何结论。它输出带凭据的证据条目、每篇文档一张固定了摘要的来源卡，以及列出全部段落（含未标注原因）的 `literature_evidence.json`。
