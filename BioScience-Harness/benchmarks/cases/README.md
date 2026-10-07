# Benchmark cases · 基准用例

The case files themselves are **not published in this repository**.

<!-- zh -->
用例文件本身**不在本仓库中发布**。

Each case is a JSON document conforming to `BenchmarkCase`. What lives here at
release time:

<!-- zh -->
每个用例都是一份符合 `BenchmarkCase` 的 JSON 文档。在发布时，此处存放的内容为：

- `dev/` — 60 public development cases, published with the benchmark release so
  a new system can iterate against them.
- `../hidden/` — 40 held-out cases, delivered only to official evaluators.
- `adversarial/` — 20 safety and overclaim cases, scored on their own axes.

<!-- zh -->
- `dev/` —— 60 个公开的开发用例，随基准发布一并公开，以便新系统据此迭代。
- `../hidden/` —— 40 个留出（held-out）用例，仅交付给官方评测方。
- `adversarial/` —— 20 个安全与过度主张（overclaim）用例，按各自的轴单独评分。

The schema, the loader, the corpus builder, the scorers and the data card are
all in this repository, and they are what make the benchmark reproducible. The
cases and their gold labels are withheld so that a clone cannot be prompted or
trained against them.

<!-- zh -->
schema、加载器（loader）、语料构建器（corpus builder）、评分器（scorer）与数据卡（data card）都在本仓库中，正是它们使该基准具备可复现性（reproducibility）。用例及其黄金答案（gold answer）被保留不予公开，这样一份克隆就无法被用于提示或训练，从而避免过拟合污染（benchmark contamination）。

This directory is ignored by `.gitignore` except for this file. See
`docs/adr/0001` for why a Season, once cut, cannot be modified.

<!-- zh -->
除本文件外，本目录被 `.gitignore` 忽略。关于一个基准赛季（benchmark Season）为何一经切出便不可修改，见 `docs/adr/0001`。

## Running a candidate · 运行候选

`scripts/benchmark_candidate.py` freezes a Season into a manifest
(`benchmarks/registry/<season>.yaml`), runs a candidate's cases through the governed path,
scores the bundle independently and verifies a published score. The public conformance
Season (`benchmarks/conformance/`, `benchmarks/registry/conformance-1.yaml`) runs in CI and
shows that the machinery works; it is not a benchmark result. Run a withheld Season the
same way, and keep its bundles private: they contain the cases' inputs and outputs.

<!-- zh -->
`scripts/benchmark_candidate.py` 可将一个赛季冻结为清单（`benchmarks/registry/<season>.yaml`），经受治理路径运行候选的全部案例，独立评分运行包，并核验已公布的分数。公开的一致性赛季（`benchmarks/conformance/`、`benchmarks/registry/conformance-1.yaml`）在 CI 中运行，用来证明这套机制可用；它不是基准结果。不公开的赛季也用同样方式运行，其运行包包含案例的输入和输出，须同样保密。
