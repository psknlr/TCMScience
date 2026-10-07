# Literature evidence retrieval · 文献证据检索

`retrieve-literature-evidence` · implementation:
`bioagent.skills.literature.retrieve:retrieve_literature_evidence` · **candidate, not in
the stable lockfile**

> This file is documentation; `skill.yaml` is the contract. A governed run refuses this
> skill until a person reviews and promotes it.

<!-- zh -->
> 本文件是说明文档，契约是 `skill.yaml`。在有人审核并晋级之前，受治理运行会拒绝本 Skill。

The skill reads a corpus directory whose `manifest.json` lists each document (a text, HTML
or PDF file in that directory) with its SHA-256, and what the caller knows about it: DOI or
PMID, citation, title, year, licence. It then:
- checks every file against its digest before reading it;
- indexes the documents with PaperQA2 (paper-qa's readers and character chunkers, its
  local sparse embedding, its vector store);
- retrieves the passages nearest the question, each with its offset in the document's
  text and its cosine score;
- types each passage with fixed text rules: study design, population, comparator,
  outcome.

It calls no model and reaches no network. See `docs/literature-evidence.md`.

<!-- zh -->
本 Skill 读取一个语料目录，其 `manifest.json` 列出每篇文档（目录中的文本、HTML 或 PDF 文件）及其 SHA-256，以及调用方掌握的信息：DOI 或 PMID、引文、标题、年份、许可。随后：
- 读取前先用摘要核对每个文件；
- 用 PaperQA2 建立索引（paper-qa 的读取器与按字符切块、本地稀疏嵌入、向量库）；
- 检索与问题最接近的段落，每段给出其在文档文本中的偏移量和余弦得分；
- 用固定的文本规则为每段标注研究设计、人群、对照和结局。

本 Skill 不调用任何模型，也不访问网络。详见 `docs/literature-evidence.md`。

## What it may claim · 可以声称什么

Nothing. A retrieved passage is material for a claim, not a finding. A passage becomes an
evidence item, with a receipt for its quote, only when a rule reads its document's study
design. Fields no rule reads stay unassessed. A passage with no readable design is listed,
located, in `literature_evidence.json` and withheld. Retraction status is never checked
here, so every item reads `unverified`.

<!-- zh -->
不声称任何结论。检索到的段落是形成结论的材料，而不是发现。只有当规则读出其所在文档的研究设计时，段落才成为带有引文凭据的证据条目。规则未读出的字段保持"未评估"。读不出研究设计的段落会连同其位置列入 `literature_evidence.json`，但不作为证据条目。本 Skill 从不核查撤稿状态，因此每个条目都标为 `unverified`（未核实）。
